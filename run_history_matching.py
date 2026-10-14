#!/usr/bin/env python3
"""
History matching -- the wave loop (HM-4 / task B-6).

    wave 1:  space-filling design (Latin hypercube) over the full prior box
             -> evaluate simulator -> compute I_max -> NROY_1 = {I_max <= 3}
    wave k:  Latin hypercube inside NROY_(k-1), optionally adding targets
             -> evaluate -> NROY_k
    stop:    when NROY volume stops shrinking materially, or targets are exhausted

Built on the LHS infrastructure (``run_lhs_calibration``), not the ABC-SMC
runner, as spec section 5 requires: wave 1 *is* an LHS plus a filter.  Nothing in
here computes a distance, a weight or a gate.

A KNOWN LIMITATION OF THE NO-EMULATOR DECISION -- read before running a wave 2
-----------------------------------------------------------------------------
Andrianakis et al. use the Gaussian process emulator twice: to score candidates
cheaply, and to *generate* the next wave's design by sampling the region the
emulator says is non-implausible.  Spec section 3.2 removes the emulator, which
removes the first use (fine -- the simulator is cheap enough) but also the second.
Without an emulator, implausibility is only known at points that have actually
been run, so an NROY-restricted design cannot be sampled exactly.

What this module does instead is stated openly rather than hidden: it proposes
the next design from the axis-aligned bounding box of the retained points,
optionally thinned by a k-nearest-neighbour proximity test against them.  That
region is a SUPERSET of NROY.  Proposals falling outside the true NROY are not
an error -- they are evaluated and ruled out -- but they cost simulator runs, and
the waste grows as NROY becomes non-convex or lower-dimensional.  If wave-over-
wave acceptance falls far enough that wall clock becomes binding, THAT is the
demonstration spec section 8.3 asks for before an emulator may be reconsidered.
Report the acceptance rate per wave; do not quietly widen anything to raise it.

Completed runs can be continued without replaying earlier simulations by using
``--continue-from``.  The parent is immutable: its manifest and every recorded
artefact are verified, scientific inputs and runtime modules must match, and its
final primary NROY members provide only the geometry for the next declared wave.
The continuation writes a new run with an explicit provenance link to its parent.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
LEGACY_PRE_CONTINUATION_RUNNER_SHA256 = "bf53b5aa0814ba68011c9a96f705159492cfda5c4cda26f1fe4ac427328c398d"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hm_diagnostics as hd
import hm_implausibility as hm
import hm_target_register as tr
import hm_variance_budget as vb

#: V_ensemble contracts. ``a5_file``: A-5 per-run variance / K (mean matching).
#: ``predictive_local``: s(x) + s(x)/K from each point's replicates (user decision 2026-09-16).
PREDICTIVE_LOCAL = "predictive_local"
A5_FILE = "a5_file"
V_ENSEMBLE_MODES = {A5_FILE, PREDICTIVE_LOCAL}
MIN_PREDICTIVE_REPLICATES = 5


# ==========================================================================
# Wave plan
# ==========================================================================

@dataclass(frozen=True)
class StoppingRule:
    """Pre-specified before the first production wave.  Spec section 5.

    ``min_volume_shrinkage`` -- stop when a wave fails to shrink the retained
    fraction by at least this relative amount.  ``max_waves`` -- hard cap.
    ``min_nroy_members`` -- stop and ESCALATE below this count (spec section 10).
    """

    max_waves: int
    min_volume_shrinkage: float
    min_nroy_members: int

    def should_stop(self, history: Sequence[dict]) -> tuple[bool, str]:
        if not history:
            return False, ""
        latest = history[-1]
        if latest["n_nroy"] == 0:
            return True, (
                "NROY is EMPTY. Per spec section 8.2 this is a RESULT, not a failure: the model "
                "cannot reproduce the data within the stated uncertainty budget. Stop and report. "
                "Do NOT widen any variance term to recover members."
            )
        if latest["n_nroy"] < self.min_nroy_members:
            return True, (
                f"NROY has {latest['n_nroy']} members, below min_nroy_members="
                f"{self.min_nroy_members}. ESCALATE per spec section 10 rather than continuing."
            )
        if len(history) >= self.max_waves:
            return True, f"max_waves={self.max_waves} reached"
        if len(history) >= 2:
            previous, current = history[-2]["frac_candidates"], latest["frac_candidates"]
            if previous > 0:
                shrinkage = (previous - current) / previous
                if shrinkage < self.min_volume_shrinkage:
                    return True, (
                        f"retained fraction shrank by {shrinkage:.3%}, below the pre-specified "
                        f"min_volume_shrinkage={self.min_volume_shrinkage:.3%}"
                    )
        return False, ""


@dataclass(frozen=True)
class WaveSpec:
    """One wave's declared plan.  ``target_series`` MUST be declared before the wave runs."""

    index: int
    n_design: int
    seed: int
    target_series: list[str]
    disc_multiplier: float | None = None


def load_wave_plan(path: str | Path) -> tuple[list[WaveSpec], StoppingRule, dict]:
    with Path(path).open(encoding="utf-8") as handle:
        plan = json.load(handle)
    stopping = StoppingRule(
        max_waves=int(plan["stopping_rule"]["max_waves"]),
        min_volume_shrinkage=float(plan["stopping_rule"]["min_volume_shrinkage"]),
        min_nroy_members=int(plan["stopping_rule"]["min_nroy_members"]),
    )
    discrepancy = dict(plan.get("discrepancy", {}) or {})
    default_disc_multiplier = discrepancy.get("multiplier")
    waves = [
        WaveSpec(
            index=int(entry.get("index", position + 1)),
            n_design=int(entry["n_design"]),
            seed=int(entry["seed"]),
            target_series=list(entry["target_series"]),
            disc_multiplier=entry.get("disc_multiplier", default_disc_multiplier),
        )
        for position, entry in enumerate(plan["waves"])
    ]
    if not waves:
        raise ValueError(f"Wave plan {path} declares no waves")
    return waves, stopping, plan


def _fixed_value(calib_space: dict, name: str):
    for entry in calib_space.get("fixed_parameters", []):
        if entry.get("name") == name:
            return entry.get("value")
    return None


def _config_value(config: dict, path: str):
    obj = config
    for token in path.split("."):
        obj = obj[token]
    return obj


def validate_run_contract(base_config: dict, calib_space: dict, plan: dict) -> None:
    """Fail before compute when the declared model, space and estimator disagree."""
    settings = dict(plan.get("run_settings", {}) or {})
    from run_lhs_calibration import validate_config_paths  # noqa: PLC0415

    validate_config_paths(base_config, calib_space)
    parameters = list(calib_space.get("parameters", []))
    names = [str(parameter.get("name")) for parameter in parameters]
    if len(names) != len(set(names)):
        raise ValueError("Calibration space contains duplicate parameter names")
    for parameter in parameters:
        low, high = float(parameter["min"]), float(parameter["max"])
        if not (np.isfinite(low) and np.isfinite(high) and low < high):
            raise ValueError(
                f"Invalid bounds for {parameter.get('name')}: min={low}, max={high}"
            )
    expected_count = plan.get("expected_parameter_count")
    if expected_count is not None and len(parameters) != int(expected_count):
        raise ValueError(
            f"Calibration-space parameter count is {len(parameters)}, expected {expected_count}"
        )
    start_config = int(_config_value(base_config, "run_control.start_year.value"))
    start_space = _fixed_value(calib_space, "simulation_start_year")
    start_plan = settings.get("simulation_start_year")
    for label, value in (("calibration space", start_space), ("wave plan", start_plan)):
        if value is not None and int(value) != start_config:
            raise ValueError(
                f"Start-year mismatch: base config={start_config}, {label}={value}. "
                "The prior box and simulator must describe the same model."
            )

    seed_config = float(_config_value(base_config, "population.epidemic_seed.seed_year.value"))
    seed_space = _fixed_value(calib_space, "seed_year")
    if seed_space is not None and float(seed_space) != seed_config:
        raise ValueError(
            f"Seed-year mismatch: base config={seed_config}, calibration space={seed_space}."
        )

    deterministic = bool(settings.get("deterministic_calibration", True))
    replicates = int(settings.get("replicates_per_design_point", 1))
    if replicates < 1:
        raise ValueError("replicates_per_design_point must be >= 1")
    if deterministic and replicates != 1:
        raise ValueError("Deterministic calibration must use exactly one replicate per design point")
    if not deterministic and replicates < 2:
        raise ValueError(
            "Stochastic calibration must average at least two replicates; one draw is not E[f(x)]."
        )

    exogenous = _config_value(
        base_config,
        "transmission_model.disease.exogenous_non_msm.enabled.value",
    )
    if not bool(exogenous):
        raise ValueError("C-1 prerequisite failed: exogenous non-MSM forcing is not enabled")

    if not plan.get("analysis_status"):
        raise ValueError("Wave plan must declare analysis_status (for example baseline_learning_wave)")
    if plan.get("status", "").startswith("TEMPLATE"):
        raise ValueError("Refusing to run a TEMPLATE wave plan")


def validate_scoring_contract(
    register: tr.TargetRegister,
    waves: Sequence[WaveSpec],
    scored_years: Sequence[int] | dict[str, Sequence[int]] | None,
    v_ensemble: dict[str, vb.BudgetTerm] | None,
    *,
    ensemble_mode: str = A5_FILE,
) -> None:
    """Ensure every declared target/year and variance term resolves before compute."""
    available_series = set(register.series_names())
    for wave in waves:
        unknown = set(wave.target_series) - available_series
        if unknown:
            raise ValueError(f"Wave {wave.index} names unknown target series: {sorted(unknown)}")
        non_calibration = {
            name for name in wave.target_series
            if all(
                target.role not in tr.CALIBRATION_ROLES
                for target in register.targets if target.series_name == name
            )
        }
        if non_calibration:
            raise ValueError(
                f"Wave {wave.index} names non-calibration target series: {sorted(non_calibration)}"
            )
        candidates = [
            target for target in register.calibration_targets()
            if target.series_name in set(wave.target_series)
        ]
        selected = register.restrict_years(candidates, scored_years)
        if not selected:
            raise ValueError(f"Wave {wave.index} resolves zero scalar calibration targets")
        selected_series = {target.series_name for target in selected}
        missing_series = set(wave.target_series) - selected_series
        if missing_series:
            raise ValueError(
                f"Wave {wave.index} resolves no scored years for: {sorted(missing_series)}"
            )
        if ensemble_mode == PREDICTIVE_LOCAL:
            derived = sorted({t.series_name for t in selected if t.model_output in tr.DERIVED_OUTPUTS})
            if derived:
                raise ValueError(
                    f"predictive_local needs replicate variances recorded per output; derived outputs have none: {derived}"
                )
            continue
        missing_variances = [target.key for target in selected if target.key not in v_ensemble]
        if missing_variances:
            raise ValueError(
                f"V_ensemble file lacks {len(missing_variances)} scored targets: "
                f"{missing_variances[:5]}"
            )


def load_v_ensemble_terms(
    path: str | Path,
    *,
    replicates_per_design_point: int,
) -> dict[str, vb.BudgetTerm]:
    """Load A-5 per-run variances and scale them to the replicate mean."""
    source = Path(path)
    frame = pd.read_csv(source)
    required = {"target_key", "recommended_v_ensemble_per_run", "recommended_estimator"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"V_ensemble file {source} missing columns: {sorted(missing)}")
    if frame["target_key"].duplicated().any():
        duplicates = frame.loc[frame["target_key"].duplicated(), "target_key"].tolist()
        raise ValueError(f"V_ensemble file has duplicate target keys: {duplicates[:5]}")
    k = int(replicates_per_design_point)
    if k < 1:
        raise ValueError("replicates_per_design_point must be >= 1")
    terms: dict[str, vb.BudgetTerm] = {}
    for row in frame.itertuples(index=False):
        per_run = float(row.recommended_v_ensemble_per_run)
        terms[str(row.target_key)] = vb.BudgetTerm(
            name="v_ensemble",
            variance=per_run / k,
            status="derived",
            derivation=(
                f"A-5 measured per-run variance {per_run:.6g} / k={k} stochastic "
                f"replicates averaged; estimator={row.recommended_estimator}; source={source}"
            ),
            direction="none",
        )
    return terms




def v_ensemble_mode(settings: dict | None) -> str:
    mode = str((settings or {}).get("v_ensemble_mode", A5_FILE))
    if mode not in V_ENSEMBLE_MODES:
        raise ValueError(f"run_settings.v_ensemble_mode must be one of {sorted(V_ENSEMBLE_MODES)}, got {mode!r}")
    return mode


def pointwise_predictive_variance(
    targets: Sequence[tr.ScalarTarget],
    frame: pd.DataFrame,
    replicates: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], list[dict]]:
    """Predictive V_ensemble(x) = s(x) + s(x)/K from each point's own replicates.

    The observed count is one stochastic realisation, so its comparison with a
    K-replicate simulator mean carries the per-run process variance s(x) plus the
    Monte Carlo error of the mean, s(x)/K (Andrianakis et al. 2017, sec. 3.2-3.3).
    ``s(x)`` is the unbiased (ddof=1) sample variance recorded in ``mcvar__*``.
    Returns ``(predictive, per_run_s, unresolved)``.  A target whose output has
    no recorded replicate variance (e.g. a derived difference) is unresolved,
    never assigned an imputed variance.
    """
    k = int(replicates)
    if k < 2:
        raise ValueError("predictive_local variance needs at least two replicates")
    predictive: dict[str, np.ndarray] = {}
    per_run: dict[str, np.ndarray] = {}
    unresolved: list[dict] = []
    for target in targets:
        column = f"mcvar__{target.model_output}_{target.year}"
        if column not in frame.columns:
            unresolved.append({"target_key": target.key, "reason": f"missing replicate variance {column}"})
            continue
        s = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        per_run[target.key] = s
        predictive[target.key] = s * (1.0 + 1.0 / k)
    return predictive, per_run, unresolved


def _pointwise_budget_term(per_run: np.ndarray, replicates: int) -> vb.BudgetTerm:
    finite = per_run[np.isfinite(per_run)]
    median = float(np.median(finite)) if finite.size else 0.0
    p95 = float(np.percentile(finite, 95)) if finite.size else 0.0
    k = int(replicates)
    return vb.BudgetTerm(
        name="v_ensemble",
        variance=median * (1.0 + 1.0 / k),
        status="derived",
        derivation=(
            f"POINTWISE predictive s(x)(1+1/K), K={k}; s(x)=within-point ddof=1 variance of K CRN "
            f"replicates. Value shown is the design MEDIAN (s median={median:.6g}, p95={p95:.6g}); "
            "scoring uses each point's own value, not this summary."
        ),
        direction="none",
    )


# ==========================================================================
# Design generation
# ==========================================================================

def prior_box(calib_space: dict) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {}
    for parameter in calib_space.get("parameters", []):
        bounds[parameter["name"]] = (float(parameter["min"]), float(parameter["max"]))
    return bounds


def design_over_box(
    calib_space: dict,
    n: int,
    seed: int,
    *,
    draw_fn: Callable[[Sequence[dict], int, int], list[dict]] | None = None,
) -> pd.DataFrame:
    """Constrained maximin-style LHS over the parameter box in ``calib_space``.

    Delegates to ``run_lhs_calibration.generate_lhs_draws`` so the ordering
    constraints already encoded there (diagnosis and ART rates monotone, contact
    strata ordered, simplex shares valid) apply identically.  Those constraints
    are part of the prior box: a draw violating them is not in the space being
    excluded from.
    """
    if draw_fn is None:
        from run_lhs_calibration import generate_lhs_draws as draw_fn  # noqa: PLC0415
    return pd.DataFrame(draw_fn(calib_space["parameters"], n, seed))


def load_supplementary_design(calib_space: dict, settings: dict | None) -> tuple[pd.DataFrame | None, dict | None]:
    """Declared warm-start vectors appended to a NEW sequence's first wave.

    ``run_settings.supplementary_design_file`` + ``supplementary_design_sha256`` (both
    required). Every prior parameter must be present, inside the prior box and pass
    the same draw constraints as the LHS. Derived columns are recomputed, never read.
    These points PROPOSE; they never define the search region.
    """
    settings = dict(settings or {})
    path_value = settings.get("supplementary_design_file")
    if not path_value:
        return None, None
    path = Path(path_value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    expected = settings.get("supplementary_design_sha256")
    actual = sha256_of(path)
    if not expected or expected != actual:
        raise ValueError(f"supplementary design {path} SHA-256 {actual} does not match declared {expected}")
    from run_lhs_calibration import _valid_draw_mask, _with_derived_parameters  # noqa: PLC0415

    frame = pd.read_csv(path)
    names = [str(p["name"]) for p in calib_space.get("parameters", [])]
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise ValueError(f"supplementary design lacks parameters: {missing}")
    design = _with_derived_parameters(frame[names].astype(float))
    for parameter in calib_space["parameters"]:
        column = design[parameter["name"]]
        if not column.between(float(parameter["min"]), float(parameter["max"])).all():
            raise ValueError(f"supplementary design has values outside the prior box for {parameter['name']}")
    invalid = ~_valid_draw_mask(design)
    if invalid.any():
        raise ValueError(f"supplementary design rows violate draw constraints: {list(design.index[invalid])}")
    return design.reset_index(drop=True), {"path": str(path), "sha256": actual, "n_rows": int(len(design))}


def restrict_space_to_nroy(
    calib_space: dict,
    retained: pd.DataFrame,
    *,
    pad_fraction: float = 0.0,
) -> dict:
    """Narrow each parameter's bounds to the retained points' axis-aligned range.

    ``pad_fraction`` widens each side by that fraction of the retained range, to
    reduce the chance of clipping a boundary that the finite design merely failed
    to sample.  This is a SUPERSET of NROY by construction (see the module
    docstring); it never narrows below what was retained.
    """
    if retained.empty:
        raise ValueError("Cannot restrict the space to an empty NROY set")
    restricted = copy.deepcopy(calib_space)
    for parameter in restricted.get("parameters", []):
        name = parameter["name"]
        if name not in retained.columns:
            continue
        column = pd.to_numeric(retained[name], errors="coerce").dropna()
        if column.empty:
            continue
        low, high = float(column.min()), float(column.max())
        pad = (high - low) * float(pad_fraction)
        parameter["min"] = max(float(parameter["min"]), low - pad)
        parameter["max"] = min(float(parameter["max"]), high + pad)
        if parameter["max"] <= parameter["min"]:
            # A parameter the previous wave pinned to a single value.
            parameter["max"] = parameter["min"] + abs(parameter["min"]) * 1e-9 + 1e-12
        parameter["restricted_from_nroy"] = {"retained_min": low, "retained_max": high, "pad_fraction": pad_fraction}
    return restricted


def thin_by_proximity(
    proposals: pd.DataFrame,
    retained: pd.DataFrame,
    bounds: dict[str, tuple[float, float]],
    *,
    k: int = 1,
    radius_quantile: float = 0.95,
) -> pd.DataFrame:
    """Drop proposals far from every retained point, in the normalised box metric.

    Keeps a proposal when its distance to its ``k``-th nearest retained point is
    no greater than the ``radius_quantile`` of the retained set's own k-th
    nearest-neighbour distances.  This is a heuristic tightening of the bounding
    box towards the retained region's shape, NOT an implausibility test, and it
    can only remove proposals -- it never admits a point the box excluded.
    """
    names = [name for name in bounds if name in proposals.columns and name in retained.columns]
    if not names or retained.empty or proposals.empty:
        return proposals

    def normalise(frame: pd.DataFrame) -> np.ndarray:
        columns = []
        for name in names:
            low, high = bounds[name]
            width = high - low if high > low else 1.0
            columns.append((pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float) - low) / width)
        return np.column_stack(columns)

    retained_points = normalise(retained)
    if retained_points.shape[0] <= k:
        return proposals
    proposal_points = normalise(proposals)

    # A dense pairwise distance matrix is not an option here: a 4,000-point wave in
    # 27 dimensions would materialise 4000 x 4000 x 27 floats (~3.5 GB) as an
    # intermediate. Use a KD-tree, and fall back to a CHUNKED dense computation
    # rather than the full one if scipy is unavailable.
    try:
        from scipy.spatial import cKDTree  # noqa: PLC0415

        tree = cKDTree(retained_points)
        self_distances = tree.query(retained_points, k=k + 1)[0][:, k]
        nearest = tree.query(proposal_points, k=k)[0]
        nearest = nearest[:, -1] if nearest.ndim == 2 else nearest
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        def kth_nearest(query: np.ndarray, reference: np.ndarray, kth: int) -> np.ndarray:
            out = np.empty(query.shape[0], dtype=float)
            chunk = max(1, int(2e6 // max(reference.shape[0] * reference.shape[1], 1)))
            for start in range(0, query.shape[0], chunk):
                block = query[start:start + chunk]
                distances = np.linalg.norm(block[:, None, :] - reference[None, :, :], axis=-1)
                out[start:start + chunk] = np.partition(distances, kth, axis=1)[:, kth]
            return out

        self_distances = kth_nearest(retained_points, retained_points, k)
        nearest = kth_nearest(proposal_points, retained_points, k - 1)

    radius = float(np.quantile(self_distances, radius_quantile))
    return proposals.loc[nearest <= radius]


# ==========================================================================
# Simulator evaluation
# ==========================================================================

def _evaluate_worker(payload: dict) -> dict:
    """Run one design point and return replicate means and within-point variances."""
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_lhs_calibration import (  # noqa: PLC0415
        CALIB_END_YEAR,
        CALIB_START_YEAR,
        _run_simulation_quietly,
        inject_params,
    )

    row: dict = {"design_id": payload["design_id"], **{k: float(v) for k, v in payload["params"].items()}}
    try:
        config = inject_params(
            payload["base_config"],
            payload["params"],
            payload["calib_space"],
            deterministic_mode=payload.get("deterministic_calibration"),
            simulation_start_year=payload.get("simulation_start_year"),
        )
        seeds = [int(seed) for seed in payload.get("replicate_seeds", [payload["seed"]])]
        replicate_values: dict[str, list[float]] = {}
        start = payload.get("scoring_start_year") or CALIB_START_YEAR
        for replicate_seed in seeds:
            result = _run_simulation_quietly(copy.deepcopy(config), seed=replicate_seed)
            outputs = extract_annual_outputs(result, start, CALIB_END_YEAR)
            for indicator, series in outputs.items():
                for year, value in sorted(series.items()):
                    key = f"{indicator}_{year}"
                    try:
                        replicate_values.setdefault(key, []).append(float(value))
                    except (TypeError, ValueError):
                        replicate_values.setdefault(key, []).append(np.nan)
        for key, values in replicate_values.items():
            array = np.asarray(values, dtype=float)
            row[key] = float(np.mean(array)) if np.all(np.isfinite(array)) else np.nan
            row[f"mcvar__{key}"] = (
                float(np.var(array, ddof=1)) if array.size >= 2 and np.all(np.isfinite(array)) else 0.0
            )
        row["n_replicates"] = len(seeds)
        row["replicate_seeds"] = ";".join(str(seed) for seed in seeds)
        row["error"] = ""
    except Exception as exc:  # noqa: BLE001 - a failed run is data, not a crash
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def evaluate_design(
    design: pd.DataFrame,
    base_config: dict,
    calib_space: dict,
    *,
    seed: int,
    workers: int = 1,
    run_settings: dict | None = None,
    worker: Callable[[dict], dict] | None = None,
    checkpoint_path: Path | None = None,
) -> pd.DataFrame:
    """Run every design point under the estimator declared in ``run_settings``.

    For stochastic calibration, every point uses the same replicate seeds (CRN
    across parameter vectors) and the scored output is their arithmetic mean.
    The within-point sample variance is retained in ``mcvar__*`` columns for
    audit, while the independently measured A-5 variance supplies V_ensemble.
    """
    worker = worker or _evaluate_worker
    settings = dict(run_settings or {})
    replicates = int(settings.pop("replicates_per_design_point", 1))
    simulation_seed = int(settings.pop("simulation_seed", seed))
    replicate_seed_stride = int(settings.pop("replicate_seed_stride", 1))
    progress_every = max(1, int(settings.pop("progress_every", 25)))
    settings.pop("supplementary_design_file", None)
    settings.pop("supplementary_design_sha256", None)
    replicate_seeds = [simulation_seed + replicate_seed_stride * offset for offset in range(replicates)]
    payloads = [
        {
            "design_id": int(index),
            "params": {name: float(value) for name, value in row.items()},
            "base_config": base_config,
            "calib_space": calib_space,
            "seed": seed,
            "replicate_seeds": replicate_seeds,
            **settings,
        }
        for index, row in design.iterrows()
    ]
    def record_progress(rows: list[dict]) -> None:
        completed = len(rows)
        if checkpoint_path is not None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).sort_values("design_id").to_csv(checkpoint_path, index=False)
        print(
            f"history matching: completed {completed}/{len(payloads)} design points "
            f"({replicates} replicate{'s' if replicates != 1 else ''} each)",
            flush=True,
        )

    if workers == 1:
        rows = []
        for payload in payloads:
            rows.append(worker(payload))
            if len(rows) % progress_every == 0 or len(rows) == len(payloads):
                record_progress(rows)
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, payload): payload["design_id"] for payload in payloads}
            for future in as_completed(futures):
                rows.append(future.result())
                if len(rows) % progress_every == 0 or len(rows) == len(payloads):
                    record_progress(rows)
    return pd.DataFrame(rows).sort_values("design_id").reset_index(drop=True)


# ==========================================================================
# Scoring one wave
# ==========================================================================

def _score_matrix(
    evaluated: pd.DataFrame,
    targets: Sequence[tr.ScalarTarget],
    register: tr.TargetRegister,
    *,
    disc_multiplier: float | None,
    disc_method: str | None,
    v_ensemble: dict[str, vb.BudgetTerm] | None,
    ensemble_mode: str,
    replicates: int,
) -> dict:
    """Resolve targets, assemble budgets and the implausibility matrix for either V_ensemble mode."""
    simulated, resolved, unresolved = tr.resolve_simulated(targets, evaluated)
    per_run: dict[str, np.ndarray] = {}
    predictive: dict[str, np.ndarray] = {}
    if ensemble_mode == PREDICTIVE_LOCAL:
        predictive, per_run, missing = pointwise_predictive_variance(resolved, evaluated, replicates)
        unresolved = list(unresolved) + missing
        resolved = [target for target in resolved if target.key in predictive]
        v_ensemble = {key: _pointwise_budget_term(values, replicates) for key, values in per_run.items()}
    if not resolved:
        return {"resolved": [], "unresolved": unresolved}
    budgets = tr.build_budgets(
        resolved,
        simulated,
        defaults=register.meta["defaults_effective"],
        v_ensemble=v_ensemble,
        disc_multiplier=disc_multiplier,
        disc_method=disc_method,
    )
    order = [t.key for t in resolved]
    observed = {t.key: t.z for t in resolved}
    if ensemble_mode == PREDICTIVE_LOCAL:
        base = {key: budgets[key].total_variance - budgets[key].v_ensemble.variance for key in order}
        variance = {key: base[key] + predictive[key] for key in order}
        matrix, names = hm.implausibility_matrix_pointwise(
            {key: simulated[key] for key in order}, observed, variance, target_order=order
        )
    else:
        base = {}
        matrix, names = hm.implausibility_matrix(
            {key: simulated[key] for key in order},
            observed,
            {key: budgets[key].total_variance for key in order},
            target_order=order,
        )
    return {
        "resolved": resolved, "unresolved": unresolved, "simulated": simulated, "budgets": budgets,
        "matrix": matrix, "names": names, "per_run": per_run, "base_variance": base, "observed": observed,
    }


def predictive_variance_stability(
    core: dict,
    replicates: int,
    *,
    cutoff: float,
    alpha: float = 0.05,
) -> dict:
    """How many NROY decisions depend on the sampling error of s(x) itself.

    With K replicates, (K-1)s/sigma^2 ~ chi^2_{K-1} under normal replicates.  Each
    point is rescored with s at the lower and upper (1-alpha) confidence bounds
    for sigma^2.  Points retained only under the upper bound are classified as
    variance-unstable.  Diagnostic only; never used to change the primary set.
    """
    from scipy.stats import chi2  # noqa: PLC0415

    k = int(replicates)
    df = k - 1
    lower_factor = df / chi2.ppf(1.0 - alpha / 2.0, df)
    upper_factor = df / chi2.ppf(alpha / 2.0, df)
    order = [t.key for t in core["resolved"]]
    masks = {}
    for label, factor in (("lower", lower_factor), ("upper", upper_factor)):
        variance = {
            key: core["base_variance"][key] + core["per_run"][key] * factor * (1.0 + 1.0 / k)
            for key in order
        }
        # A zero sample variance with zero base variance cannot be scored; treat as NaN column entries.
        matrix, names = hm.implausibility_matrix_pointwise(
            {key: core["simulated"][key] for key in order}, core["observed"], variance, target_order=order
        )
        masks[label] = hm.combine(matrix, names).nroy_mask(cutoff=cutoff, rule="max")
    return {
        "replicates": k,
        "alpha": alpha,
        "s_lower_factor": float(lower_factor),
        "s_upper_factor": float(upper_factor),
        "n_nroy_s_lower_bound": int(masks["lower"].sum()),
        "n_nroy_s_upper_bound": int(masks["upper"].sum()),
        "n_variance_unstable": int((masks["upper"] & ~masks["lower"]).sum()),
        "unstable_mask": masks["upper"] & ~masks["lower"],
    }


def score_wave(
    evaluated: pd.DataFrame,
    register: tr.TargetRegister,
    target_series: Sequence[str],
    *,
    disc_multiplier: float | None = None,
    disc_method: str | None = None,
    v_ensemble: dict[str, vb.BudgetTerm] | None = None,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
    scored_years: Sequence[int] | dict[str, Sequence[int]] | None = None,
    ensemble_mode: str = A5_FILE,
    replicates: int = 1,
) -> dict:
    """Compute implausibility, NROY and the wave's diagnostics.

    Only targets whose ``role`` is a calibration role AND whose series appears in
    ``target_series`` enter I_max.  Cross-validation comparators are scored and
    reported separately; they never rule anything out.
    """
    wanted = set(target_series)
    unknown = wanted - set(register.series_names())
    if unknown:
        raise ValueError(f"Wave declares target series absent from the register: {sorted(unknown)}")
    non_calibration = {
        name for name in wanted
        if all(t.role not in tr.CALIBRATION_ROLES for t in register.targets if t.series_name == name)
    }
    if non_calibration:
        raise ValueError(
            f"Wave declares non-calibration series as targets: {sorted(non_calibration)}. "
            "Cross-validation comparators and excluded series must not enter I_max."
        )

    targets = register.restrict_years(
        [t for t in register.calibration_targets() if t.series_name in wanted], scored_years
    )
    if not targets:
        raise ValueError("Wave resolved zero calibration targets")

    core = _score_matrix(
        evaluated, targets, register,
        disc_multiplier=disc_multiplier, disc_method=disc_method, v_ensemble=v_ensemble,
        ensemble_mode=ensemble_mode, replicates=replicates,
    )
    resolved, unresolved = core["resolved"], core["unresolved"]
    if ensemble_mode == PREDICTIVE_LOCAL and unresolved:
        raise ValueError(
            f"predictive_local scoring could not resolve primary targets: {unresolved[:5]}. "
            "Dropping a primary target weakens the test; refusing."
        )
    if not resolved:
        raise ValueError("No declared target resolved against the evaluated design")
    budgets, matrix, names = core["budgets"], core["matrix"], core["names"]
    combined = hm.combine(matrix, names)
    mask = combined.nroy_mask(cutoff=cutoff, rule="max")
    stability = (
        predictive_variance_stability(core, replicates, cutoff=cutoff)
        if ensemble_mode == PREDICTIVE_LOCAL else None
    )
    return {
        "variance_stability": stability,
        "combined": combined,
        "matrix": matrix,
        "targets": resolved,
        "budgets": budgets,
        "unresolved": unresolved,
        "mask": mask,
        "nroy_summary": hd.nroy_summary(combined, cutoff=cutoff),
        "per_target": hd.per_target_summary(matrix, resolved, budgets, cutoff=cutoff),
        "binding": hd.binding_target_counts(combined),
        "budget_table": pd.DataFrame(vb.budget_table(budgets.values())),
    }


def score_cross_validation(
    evaluated: pd.DataFrame,
    register: tr.TargetRegister,
    *,
    disc_multiplier: float | None = None,
    disc_method: str | None = None,
    v_ensemble: dict[str, vb.BudgetTerm] | None = None,
    years: Sequence[int] | dict[str, Sequence[int]] | None = None,
    ensemble_mode: str = A5_FILE,
    replicates: int = 1,
) -> dict:
    """Score held-out comparators without producing an acceptance decision."""
    targets = register.restrict_years(register.cross_validation_targets(), years)
    core = _score_matrix(
        evaluated, targets, register,
        disc_multiplier=disc_multiplier, disc_method=disc_method, v_ensemble=v_ensemble,
        ensemble_mode=ensemble_mode, replicates=replicates,
    )
    resolved, unresolved = core["resolved"], core["unresolved"]
    if not resolved:
        return {
            "per_target": pd.DataFrame(),
            "budget_table": pd.DataFrame(),
            "unresolved": unresolved,
        }
    budgets, matrix = core["budgets"], core["matrix"]
    return {
        "per_target": hd.per_target_summary(matrix, resolved, budgets),
        "budget_table": pd.DataFrame(vb.budget_table(budgets.values())),
        "unresolved": unresolved,
    }


# ==========================================================================
# Manifest
# ==========================================================================

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(out_dir: Path, payload: dict) -> Path:
    """Checksum every artefact written, so a wave is reproducible from the manifest."""
    files = sorted(p for p in out_dir.rglob("*") if p.is_file() and p.name != "manifest.json")
    payload = dict(payload)
    payload["artefacts"] = [
        {"path": str(p.relative_to(out_dir)), "bytes": p.stat().st_size, "sha256": sha256_of(p)} for p in files
    ]
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_continuation_source(
    source_dir: str | Path,
    *,
    current_provenance: dict,
    calib_space: dict,
    waves: Sequence[WaveSpec],
    cutoff: float,
) -> tuple[pd.DataFrame, dict]:
    """Validate and load the final primary NROY set from a completed run.

    Continuation never modifies the parent run.  Every recorded parent artefact
    is checked before its NROY geometry is trusted, and scientific/scoring inputs
    must match the current invocation.  The one legacy runner hash below allows
    the 2026-09-15 learning wave (which predates this option) to seed its first
    continuation; all subsequent continuations require an exact runtime match.
    """
    source = Path(source_dir).resolve()
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Continuation source has no manifest.json: {source}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read continuation manifest {manifest_path}: {exc}") from exc

    history = list(manifest.get("history", []))
    if not history:
        raise ValueError("Continuation source manifest has no completed wave history")
    last = history[-1]
    last_wave = int(last["wave"])
    if int(last.get("n_errors", 0)) != 0:
        raise ValueError("Continuation source's final wave contains simulation errors")
    if int(last.get("n_nroy", 0)) <= 0:
        raise ValueError("Continuation source's final wave has no primary NROY members")
    parent_min_nroy = int((manifest.get("stopping_rule") or {}).get("min_nroy_members", 1))
    if int(last["n_nroy"]) < parent_min_nroy:
        raise ValueError(
            f"Continuation source has only {last['n_nroy']} NROY members, below its declared "
            f"minimum of {parent_min_nroy}; review rather than continuing automatically"
        )
    if not waves or int(waves[0].index) != last_wave + 1:
        raise ValueError(
            f"A continuation from wave {last_wave} must begin with wave index {last_wave + 1}"
        )

    artefacts = list(manifest.get("artefacts", []))
    if not artefacts:
        raise ValueError("Continuation source manifest records no artefacts")
    source_prefix = source.as_posix().rstrip("/") + "/"
    verified: dict[str, dict] = {}
    for entry in artefacts:
        relative = str(entry.get("path", ""))
        candidate = (source / relative).resolve()
        if candidate.as_posix() != source.as_posix() and not candidate.as_posix().startswith(source_prefix):
            raise ValueError(f"Unsafe continuation artefact path: {relative}")
        if not candidate.is_file():
            raise ValueError(f"Continuation artefact is missing: {relative}")
        if candidate.stat().st_size != int(entry["bytes"]) or sha256_of(candidate) != entry["sha256"]:
            raise ValueError(f"Continuation artefact failed integrity verification: {relative}")
        verified[relative] = entry

    parent_inputs = dict(manifest.get("input_provenance", {}))
    for name in ("base_config", "calib_space", "target_register", "v_ensemble"):
        parent_hash = (parent_inputs.get(name) or {}).get("sha256")
        current_hash = (current_provenance.get(name) or {}).get("sha256")
        if not parent_hash or parent_hash != current_hash:
            raise ValueError(
                f"Continuation input mismatch for {name}: parent={parent_hash}, current={current_hash}"
            )

    parent_modules = ((parent_inputs.get("runtime") or {}).get("modules") or {})
    current_modules = ((current_provenance.get("runtime") or {}).get("modules") or {})
    module_differences = sorted(
        name for name in set(parent_modules) | set(current_modules)
        if parent_modules.get(name) != current_modules.get(name)
    )
    allowed_legacy_difference = (
        module_differences == ["run_history_matching.py"]
        and parent_modules.get("run_history_matching.py") == LEGACY_PRE_CONTINUATION_RUNNER_SHA256
    )
    if module_differences and not allowed_legacy_difference:
        raise ValueError(
            "Continuation runtime differs from the parent run in: "
            f"{module_differences}. Start a new full wave sequence or document and validate the change."
        )

    parent_cutoff = float(manifest.get("cutoff", hm.IMPLAUSIBILITY_CUTOFF))
    if not np.isclose(float(cutoff), parent_cutoff):
        raise ValueError(
            f"Continuation cutoff mismatch: parent={parent_cutoff}, current={cutoff}"
        )

    relative_nroy = f"wave_{last_wave:02d}/nroy_members.csv"
    if relative_nroy not in verified:
        raise ValueError(f"Continuation manifest does not record {relative_nroy}")
    retained = pd.read_csv(source / relative_nroy)
    if len(retained) != int(last["n_nroy"]):
        raise ValueError(
            f"Continuation NROY row count {len(retained)} does not match manifest {last['n_nroy']}"
        )
    parameter_names = [str(parameter["name"]) for parameter in calib_space.get("parameters", [])]
    missing_parameters = sorted(set(parameter_names) - set(retained.columns))
    if missing_parameters:
        raise ValueError(f"Continuation NROY lacks parameters: {missing_parameters}")
    if "i_max" not in retained.columns:
        raise ValueError("Continuation NROY contains a missing or above-cutoff i_max")
    retained_i_max = pd.to_numeric(retained["i_max"], errors="coerce")
    if retained_i_max.isna().any() or (retained_i_max > cutoff).any():
        raise ValueError("Continuation NROY contains a missing or above-cutoff i_max")
    if "error" in retained and retained["error"].fillna("").astype(str).str.strip().ne("").any():
        raise ValueError("Continuation NROY contains rows with simulation errors")
    for parameter in calib_space.get("parameters", []):
        values = pd.to_numeric(retained[parameter["name"]], errors="coerce")
        if values.isna().any() or not values.between(float(parameter["min"]), float(parameter["max"])).all():
            raise ValueError(f"Continuation NROY has invalid values for {parameter['name']}")

    context = {
        "source_dir": str(source),
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_of(manifest_path),
        "parent_last_wave": last_wave,
        "parent_nroy_members": len(retained),
        "parent_nroy_path": relative_nroy,
        "parent_nroy_sha256": sha256_of(source / relative_nroy),
        "parent_stop_reason": manifest.get("stop_reason"),
        "runtime_module_differences": module_differences,
        "legacy_runner_bridge_used": allowed_legacy_difference,
    }
    return retained, context


def repository_provenance() -> dict:
    """Hash the model modules that determine a wave and record git/environment state."""
    module_names = [
        "run_history_matching.py", "hm_target_register.py", "hm_variance_budget.py",
        "hm_implausibility.py", "hm_diagnostics.py", "run_lhs_calibration.py",
        "calib_extractor.py", "run_v3.py", "engine_v3.py", "demography.py",
        "migration.py", "transmission_corrected.py", "exogenous_diagnoses.py",
        "counter_based_rng.py", "stochastic_allocation.py", "schedule_utils.py",
        "config_reader.py", "config_utils.py", "aids_observation.py",
    ]
    modules = {
        name: sha256_of(REPO_ROOT / name)
        for name in module_names
        if (REPO_ROOT / name).exists()
    }
    git: dict[str, object] = {}
    try:
        git["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
            capture_output=True, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"], cwd=REPO_ROOT, text=True,
            capture_output=True, check=True,
        ).stdout
        git["dirty"] = bool(status.strip())
        git["status_sha256"] = hashlib.sha256(status.encode("utf-8")).hexdigest()
    except (OSError, subprocess.CalledProcessError) as exc:
        git["error"] = f"{type(exc).__name__}: {exc}"
    return {
        "modules": modules,
        "git": git,
        "python": sys.version,
        "platform": platform.platform(),
        "argv": list(sys.argv),
    }


def write_run_summary(out_dir: Path, payload: dict, analysis_status: str) -> Path:
    lines = [
        "# History-matching run summary",
        "",
        f"**Analysis status:** `{analysis_status}`",
        "",
        f"**Stop reason:** {payload['stop_reason']}",
        "",
        "This is a history-matching NROY set, not a posterior distribution. Sensitivity",
        "discrepancy results never determine continuation of the primary wave sequence.",
        "",
    ]
    continuation = payload.get("continuation")
    if continuation:
        lines.extend(
            [
                f"**Continuation source:** `{continuation['source_dir']}`",
                "",
                f"Started from {continuation['parent_nroy_members']} primary NROY members "
                f"in parent wave {continuation['parent_last_wave']}.",
                "",
            ]
        )
    lines.extend([
        "| Wave | Evaluated | Errors | Targets | Primary NROY | Primary fraction | Top binding target |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ])
    for record in payload["history"]:
        lines.append(
            f"| {record['wave']} | {record['n_evaluated']} | {record['n_errors']} | "
            f"{record['n_targets']} | {record['n_nroy']} | {record['frac_candidates']:.4f} | "
            f"{record.get('top_binding_target') or ''} |"
        )
        for sensitivity in record.get("discrepancy_sensitivities", []):
            lines.extend(
                [
                    "",
                    f"- Wave {record['wave']} sensitivity `{sensitivity['label']}`: "
                    f"{sensitivity['n_nroy']} NROY ({sensitivity['frac_candidates']:.4f}); "
                    "diagnostic only.",
                ]
            )
    path = out_dir / "RUN_SUMMARY.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ==========================================================================
# The loop
# ==========================================================================

def run_waves(
    *,
    base_config: dict,
    calib_space: dict,
    register: tr.TargetRegister,
    waves: Sequence[WaveSpec],
    stopping: StoppingRule,
    out_dir: Path,
    workers: int = 1,
    run_settings: dict | None = None,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
    evaluate: Callable[..., pd.DataFrame] | None = None,
    scored_years: Sequence[int] | dict[str, Sequence[int]] | None = None,
    disc_method: str | None = None,
    v_ensemble: dict[str, vb.BudgetTerm] | None = None,
    input_provenance: dict | None = None,
    sensitivity_discrepancies: Sequence[dict] | None = None,
    cross_validation_years: Sequence[int] | dict[str, Sequence[int]] | None = None,
    analysis_status: str = "unspecified",
    initial_retained: pd.DataFrame | None = None,
    continuation_context: dict | None = None,
) -> dict:
    """Execute the wave loop, writing one directory per wave plus a run manifest."""
    evaluate = evaluate or evaluate_design
    ensemble_mode = v_ensemble_mode(run_settings)
    replicates = int((run_settings or {}).get("replicates_per_design_point", 1))
    mode_kwargs = {"ensemble_mode": ensemble_mode, "replicates": replicates}
    out_dir.mkdir(parents=True, exist_ok=True)
    box = prior_box(calib_space)
    space = copy.deepcopy(calib_space)
    history: list[dict] = []
    retained: pd.DataFrame | None = None if initial_retained is None else initial_retained.copy()
    stop_reason = "all planned waves executed"

    for spec in waves:
        wave_dir = out_dir / f"wave_{spec.index:02d}"
        wave_dir.mkdir(parents=True, exist_ok=True)

        if retained is not None:
            space = restrict_space_to_nroy(space, retained)
        design = design_over_box(space, spec.n_design, spec.seed)
        n_proposed = len(design)
        if retained is not None and not retained.empty:
            design = thin_by_proximity(design, retained, box).reset_index(drop=True)
        n_supplementary = 0
        if retained is None and not history and initial_retained is None:
            supplementary, supplementary_info = load_supplementary_design(calib_space, run_settings)
            if supplementary is not None:
                n_lhs = len(design)
                design = pd.concat([design, supplementary[design.columns]], ignore_index=True)
                n_supplementary = len(supplementary)
                pd.DataFrame({"design_row": range(len(design)),
                              "source": ["lhs"] * n_lhs + ["supplementary"] * n_supplementary}).to_csv(
                    wave_dir / "design_sources.csv", index=False)
                (wave_dir / "supplementary_design_provenance.json").write_text(
                    json.dumps(supplementary_info, indent=2), encoding="utf-8")
        design.to_csv(wave_dir / "design.csv", index=False)

        if evaluate is evaluate_design:
            evaluated = evaluate(
                design,
                base_config,
                space,
                seed=spec.seed,
                workers=workers,
                run_settings=run_settings,
                checkpoint_path=wave_dir / "evaluated.partial.csv",
            )
        else:
            evaluated = evaluate(
                design, base_config, space, seed=spec.seed,
                workers=workers, run_settings=run_settings,
            )
        evaluated.to_csv(wave_dir / "evaluated.csv", index=False)

        scored = score_wave(
            evaluated, register, spec.target_series,
            disc_multiplier=spec.disc_multiplier,
            disc_method=disc_method,
            v_ensemble=v_ensemble,
            cutoff=cutoff,
            scored_years=scored_years,
            **mode_kwargs,
        )
        stability_record = None
        if scored.get("variance_stability") is not None:
            stability = scored["variance_stability"]
            stability_record = {key: value for key, value in stability.items() if key != "unstable_mask"}
            stability_record["n_nroy_primary"] = int(scored["mask"].sum())
            stability_record["note"] = (
                "Chi-square sampling bounds on s(x); diagnostic only, never used to alter the primary NROY set."
            )
            (wave_dir / "variance_contract_stability.json").write_text(
                json.dumps(stability_record, indent=2), encoding="utf-8"
            )
        scored["nroy_summary"].to_csv(wave_dir / "nroy_summary.csv", index=False)
        scored["per_target"].to_csv(wave_dir / "per_target_implausibility.csv", index=False)
        scored["binding"].to_csv(wave_dir / "binding_targets.csv", index=False)
        scored["budget_table"].to_csv(wave_dir / "variance_budget.csv", index=False)

        cross_validation = score_cross_validation(
            evaluated,
            register,
            disc_multiplier=spec.disc_multiplier,
            disc_method=disc_method,
            v_ensemble=v_ensemble,
            years=cross_validation_years,
            **mode_kwargs,
        )
        cross_validation_dir = wave_dir / "cross_validation"
        cross_validation_dir.mkdir(parents=True, exist_ok=True)
        cross_validation["per_target"].to_csv(
            cross_validation_dir / "per_target_standardised_discrepancy.csv", index=False
        )
        cross_validation["budget_table"].to_csv(
            cross_validation_dir / "variance_budget.csv", index=False
        )
        (cross_validation_dir / "unresolved_targets.json").write_text(
            json.dumps(cross_validation["unresolved"], indent=2), encoding="utf-8"
        )

        sensitivity_records: list[dict] = []
        for sensitivity in sensitivity_discrepancies or []:
            label = str(sensitivity["label"])
            method = str(sensitivity["method"])
            multiplier = float(sensitivity["multiplier"])
            alternate = score_wave(
                evaluated,
                register,
                spec.target_series,
                disc_multiplier=multiplier,
                disc_method=method,
                v_ensemble=v_ensemble,
                cutoff=cutoff,
                scored_years=scored_years,
                **mode_kwargs,
            )
            sensitivity_dir = wave_dir / f"sensitivity_{label}"
            sensitivity_dir.mkdir(parents=True, exist_ok=True)
            alternate["nroy_summary"].to_csv(sensitivity_dir / "nroy_summary.csv", index=False)
            alternate["per_target"].to_csv(sensitivity_dir / "per_target_implausibility.csv", index=False)
            alternate["binding"].to_csv(sensitivity_dir / "binding_targets.csv", index=False)
            alternate["budget_table"].to_csv(sensitivity_dir / "variance_budget.csv", index=False)
            alternate_members = evaluated.loc[alternate["mask"]].copy()
            alternate_members["i_max"] = alternate["combined"].i_max[alternate["mask"]]
            alternate_members["i_second"] = alternate["combined"].i_second[alternate["mask"]]
            alternate_members.to_csv(sensitivity_dir / "nroy_members.csv", index=False)
            sensitivity_records.append(
                {
                    "label": label,
                    "method": method,
                    "multiplier": multiplier,
                    "n_nroy": int(alternate["mask"].sum()),
                    "frac_candidates": float(alternate["mask"].mean()),
                    "note": "Sensitivity only; never used to continue or rescue the primary wave sequence.",
                }
            )

        mask = scored["mask"]
        volume = hd.nroy_volume(evaluated, mask, box)
        hd.per_parameter_range_table(volume).to_csv(wave_dir / "parameter_ranges.csv", index=False)
        nroy_rows = evaluated.loc[mask].copy()
        nroy_rows["i_max"] = scored["combined"].i_max[mask]
        nroy_rows["i_second"] = scored["combined"].i_second[mask]
        nroy_rows["binding_target"] = [
            name for name, keep in zip(scored["combined"].binding_target, mask) if keep
        ]
        if scored.get("variance_stability") is not None:
            nroy_rows["variance_unstable"] = scored["variance_stability"]["unstable_mask"][mask]
        nroy_rows.to_csv(wave_dir / "nroy_members.csv", index=False)

        record = {
            "wave": spec.index,
            "n_design_proposed": n_proposed,
            "n_design_after_thinning": int(len(design)),
            "n_supplementary_design": int(n_supplementary),
            "n_evaluated": int(len(evaluated)),
            "n_errors": int((evaluated.get("error", pd.Series(dtype=object)).astype(str) != "").sum()),
            "n_targets": len(scored["targets"]),
            "target_series": list(spec.target_series),
            "disc_multiplier": spec.disc_multiplier,
            "disc_method": disc_method or register.meta["defaults_effective"].get("v_disc", {}).get("method"),
            "n_nroy": int(mask.sum()),
            "frac_candidates": volume["frac_candidates"],
            "frac_bounding_box": volume["frac_bounding_box"],
            "top_binding_target": (scored["binding"]["target_key"].iloc[0] if not scored["binding"].empty else None),
            "unresolved_targets": scored["unresolved"],
            "n_cross_validation_targets": int(len(cross_validation["per_target"])),
            "unresolved_cross_validation_targets": cross_validation["unresolved"],
            "discrepancy_sensitivities": sensitivity_records,
            "v_ensemble_mode": ensemble_mode,
            "variance_contract_stability": stability_record,
        }
        history.append(record)
        (wave_dir / "wave_summary.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

        should_stop, reason = stopping.should_stop(history)
        if should_stop:
            stop_reason = reason
            break
        retained = nroy_rows

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff,
        "stopping_rule": {
            "max_waves": stopping.max_waves,
            "min_volume_shrinkage": stopping.min_volume_shrinkage,
            "min_nroy_members": stopping.min_nroy_members,
        },
        "stop_reason": stop_reason,
        "history": history,
        "register_defaults": register.meta.get("defaults_effective"),
        "run_settings": dict(run_settings or {}),
        "scored_years": scored_years,
        "input_provenance": dict(input_provenance or {}),
        "sensitivity_discrepancies": list(sensitivity_discrepancies or []),
        "cross_validation_years": cross_validation_years,
        "analysis_status": analysis_status,
        "continuation": dict(continuation_context) if continuation_context else None,
        "vocabulary_note": (
            "NROY is a SET, not a posterior. It carries no probability structure, so it supports a "
            "RANGE across the ensemble, never P(cost-effective). Do not use the words 'posterior' or "
            "'credible interval' for anything in these files (spec section 8.5)."
        ),
    }
    pd.DataFrame(history).to_csv(out_dir / "wave_history.csv", index=False)
    write_run_summary(out_dir, payload, analysis_status)
    write_manifest(out_dir, payload)
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wave-plan", required=True, help="JSON plan: waves and the pre-specified stopping rule")
    parser.add_argument("--base-config", required=True)
    parser.add_argument("--calib-space", required=True)
    parser.add_argument("--register", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--cutoff", type=float, default=hm.IMPLAUSIBILITY_CUTOFF)
    parser.add_argument(
        "--continue-from",
        default=None,
        help=(
            "Completed history-matching run whose final primary NROY set defines the starting "
            "region. The parent is read-only and fully integrity/compatibility checked."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the complete run contract and inputs, print a summary, and run no simulations.",
    )
    parser.add_argument(
        "--acknowledge-prerequisites",
        action="store_true",
        help=(
            "Confirm that the named plan's status and limitations have been reviewed, C-1 is enabled, "
            "the calibration space matches the base model, and the code is post-fix."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.acknowledge_prerequisites:
        print(
            "REFUSING TO RUN.\n\n"
            "Review the named wave plan and its limitations before compute. The runner will then verify:\n"
            "  - the base-config, calibration-space and plan start years agree;\n"
            "  - the epidemic seed years agree;\n"
            "  - C-1 exogenous non-MSM forcing is enabled;\n"
            "  - stochastic calibration averages at least two replicates;\n"
            "  - a measured V_ensemble file and explicit V_disc method are supplied; and\n"
            "  - the plan is not a template and declares its analysis status.\n\n"
            "Re-run with --acknowledge-prerequisites only after reviewing those declarations.\n",
            file=sys.stderr,
        )
        return 2

    waves, stopping, plan = load_wave_plan(args.wave_plan)
    with Path(args.base_config).open(encoding="utf-8") as handle:
        base_config = json.load(handle)
    with Path(args.calib_space).open(encoding="utf-8") as handle:
        calib_space = json.load(handle)
    register = tr.load_register(args.register)
    validate_run_contract(base_config, calib_space, plan)

    settings = dict(plan.get("run_settings", {}) or {})
    replicates = int(settings.get("replicates_per_design_point", 1))
    ensemble_mode = v_ensemble_mode(settings)
    v_ensemble_source: Path | None = None
    if ensemble_mode == PREDICTIVE_LOCAL:
        if settings.get("v_ensemble_file"):
            raise ValueError("predictive_local must not also name a v_ensemble_file; the two contracts are exclusive")
        if replicates < MIN_PREDICTIVE_REPLICATES:
            raise ValueError(
                f"predictive_local needs >= {MIN_PREDICTIVE_REPLICATES} replicates to estimate s(x); got {replicates}"
            )
        v_ensemble = None
    else:
        v_ensemble_path = settings.get("v_ensemble_file")
        if not v_ensemble_path:
            raise ValueError("run_settings.v_ensemble_file is required for a production-path wave")
        v_ensemble_source = Path(v_ensemble_path)
        if not v_ensemble_source.is_absolute():
            v_ensemble_source = REPO_ROOT / v_ensemble_source
        v_ensemble = load_v_ensemble_terms(
            v_ensemble_source,
            replicates_per_design_point=replicates,
        )
    validate_scoring_contract(
        register, waves, plan.get("scored_years"), v_ensemble, ensemble_mode=ensemble_mode
    )

    discrepancy = dict(plan.get("discrepancy", {}) or {})
    disc_method = discrepancy.get("method")
    if disc_method not in {"fraction_of_output_variance", "relative_to_target"}:
        raise ValueError(
            "Wave plan discrepancy.method must be 'fraction_of_output_variance' "
            "or 'relative_to_target'"
        )

    input_paths = {
        "wave_plan": Path(args.wave_plan),
        "base_config": Path(args.base_config),
        "calib_space": Path(args.calib_space),
        "target_register": Path(args.register) if args.register else tr.DEFAULT_REGISTER_PATH,
    }
    if v_ensemble_source is not None:
        input_paths["v_ensemble"] = v_ensemble_source
    provenance = {
        name: {"path": str(path.resolve()), "sha256": sha256_of(path.resolve())}
        for name, path in input_paths.items()
    }
    if v_ensemble_source is None:
        descriptor = json.dumps({"method": PREDICTIVE_LOCAL, "replicates": replicates}, sort_keys=True)
        provenance["v_ensemble"] = {
            "path": None,
            "method": PREDICTIVE_LOCAL,
            "sha256": hashlib.sha256(descriptor.encode("utf-8")).hexdigest(),
        }
    provenance["runtime"] = repository_provenance()

    initial_retained = None
    continuation_context = None
    if args.continue_from:
        source_dir = Path(args.continue_from).resolve()
        output_dir = Path(args.output_dir).resolve()
        if source_dir == output_dir:
            raise ValueError("Continuation output directory must differ from its read-only parent")
        initial_retained, continuation_context = load_continuation_source(
            source_dir,
            current_provenance=provenance,
            calib_space=calib_space,
            waves=waves,
            cutoff=args.cutoff,
        )
        provenance["continuation_source"] = dict(continuation_context)

    if args.validate_only:
        selected = register.restrict_years(
            [
                target for target in register.calibration_targets()
                if target.series_name in set(waves[0].target_series)
            ],
            plan.get("scored_years"),
        )
        continuation_design_preview = None
        if initial_retained is not None:
            preview_space = restrict_space_to_nroy(calib_space, initial_retained)
            preview = design_over_box(preview_space, waves[0].n_design, waves[0].seed)
            preview_after_thinning = thin_by_proximity(
                preview, initial_retained, prior_box(calib_space)
            )
            continuation_design_preview = {
                "n_proposed": len(preview),
                "n_after_proximity_thinning": len(preview_after_thinning),
            }
        print(json.dumps({
            "status": "VALID -- no simulations run",
            "analysis_status": plan["analysis_status"],
            "waves": len(waves),
            "first_planned_wave_index": waves[0].index,
            "first_planned_wave_design_points": waves[0].n_design,
            "replicates_per_design_point": replicates,
            "workers": args.workers,
            "primary_discrepancy": discrepancy,
            "n_primary_targets_first_planned_wave": len(selected),
            "primary_target_keys_first_planned_wave": [target.key for target in selected],
            "supplementary_design": (load_supplementary_design(calib_space, settings)[1]
                                     if not args.continue_from else None),
            "v_ensemble_mode": ensemble_mode,
            "v_ensemble_terms_loaded": None if v_ensemble is None else len(v_ensemble),
            "cross_validation_target_keys": [
                target.key for target in register.restrict_years(
                    register.cross_validation_targets(), plan.get("cross_validation_years")
                )
            ],
            "continuation": continuation_context,
            "continuation_design_preview": continuation_design_preview,
        }, indent=2))
        return 0

    out_dir = Path(args.output_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"Refusing to write into a non-empty directory: {out_dir}", file=sys.stderr)
        return 2

    payload = run_waves(
        base_config=base_config,
        calib_space=calib_space,
        register=register,
        waves=waves,
        stopping=stopping,
        out_dir=out_dir,
        workers=args.workers,
        run_settings=settings,
        cutoff=args.cutoff,
        scored_years=plan.get("scored_years"),
        disc_method=disc_method,
        v_ensemble=v_ensemble,
        input_provenance=provenance,
        sensitivity_discrepancies=plan.get("sensitivity_discrepancies", []),
        cross_validation_years=plan.get("cross_validation_years"),
        analysis_status=str(plan["analysis_status"]),
        initial_retained=initial_retained,
        continuation_context=continuation_context,
    )
    print(json.dumps({"stop_reason": payload["stop_reason"], "history": payload["history"]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
