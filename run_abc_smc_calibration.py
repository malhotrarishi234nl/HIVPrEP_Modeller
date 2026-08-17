"""
ABC-SMC epidemic calibration runner.

Reuses infrastructure from run_lhs_calibration.py and adds prior sampling,
resampling, perturbation, and continuous distance tracking specific to the
ABC-SMC framework. Hard acceptance gates remain the authority for final
candidate acceptance; the ABC distance is used only for search guidance and
ranking.

Output files follow the naming convention:
  abc_smc_particles_<timestamp>.csv
  abc_smc_generation_summary_<timestamp>.csv
  abc_smc_metadata_<timestamp>.json
  abc_smc_summary_<timestamp>.xlsx
  abc_smc_best_fit_rank_001.json ...
"""

from __future__ import annotations

import argparse
import copy
import glob as _glob
import json
import math
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_historical_shape_gates import (
    HistoricalShapeGateSettings,
    evaluate_historical_shape_distance,
    evaluate_historical_shape_gates,
    historical_shape_distance_row_fields,
    historical_shape_gate_row_fields,
)
from calib_non_msm_diagnosis import (
    NonMsmDiagnosisSettings,
    evaluate_non_msm_diagnosis_gate,
    non_msm_diagnosis_distance,
    non_msm_diagnosis_row_fields,
)
from calib_distance import (
    CalibrationDistanceSettings,
    calibration_distance_row_fields,
    evaluate_calibration_distance,
)
from calib_extractor import extract_annual_outputs
from calib_scorer import score_draw
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
    write_normalized_targets_csv,
)
from run_lhs_calibration import (
    CALIB_END_YEAR,
    CALIB_START_YEAR,
    _run_simulation_quietly,
    _valid_draw_mask,
    _with_derived_parameters,
    filter_calib_space_for_stage,
    get_nested,
    inject_params,
    load_json,
    sha256_file,
    validate_config_paths,
    write_json,
)


# ---------------------------------------------------------------------------
# Tolerance schedule: fraction of particles to keep at each generation.
# ---------------------------------------------------------------------------

_TOLERANCE_SCHEDULE: dict[int, float] = {
    0: 1.00,
    1: 0.50,
    2: 0.35,
    3: 0.25,
    4: 0.15,
}
_DEFAULT_TOLERANCE = 0.10


def _survival_quantile(generation: int) -> float:
    return _TOLERANCE_SCHEDULE.get(generation, _DEFAULT_TOLERANCE)


# ---------------------------------------------------------------------------
# Parameter-space helpers
# ---------------------------------------------------------------------------


def _parameter_bounds(parameters: Sequence[dict]) -> dict[str, tuple[float, float]]:
    return {p["name"]: (float(p["min"]), float(p["max"])) for p in parameters}


def _bandwidth(parameters: Sequence[dict], fraction: float = 0.10) -> dict[str, float]:
    """Gaussian perturbation kernel bandwidth as a fraction of each parameter's range."""
    return {
        p["name"]: max((float(p["max"]) - float(p["min"])) * fraction, 1e-9)
        for p in parameters
    }


def _parameter_by_name(parameters: Sequence[dict]) -> dict[str, dict]:
    return {p["name"]: p for p in parameters}


def _sample_uniform_column(
    pmap: dict[str, dict],
    name: str,
    size: int,
    rng: np.random.Generator,
    *,
    lower: float | np.ndarray | None = None,
    upper: float | np.ndarray | None = None,
) -> np.ndarray:
    """Sample one parameter column within its configured bounds.

    Optional lower/upper arguments impose row-wise conditional bounds for
    diagnostic spaces with ordering constraints.  If a conditional interval is
    infeasible for a row, the configured bound is used and final validation
    still decides whether the draw survives.
    """
    p = pmap[name]
    lo = float(p["min"]) if lower is None else np.maximum(float(p["min"]), lower)
    hi = float(p["max"]) if upper is None else np.minimum(float(p["max"]), upper)
    lo_arr = np.full(size, lo, dtype=float) if np.isscalar(lo) else np.asarray(lo, dtype=float)
    hi_arr = np.full(size, hi, dtype=float) if np.isscalar(hi) else np.asarray(hi, dtype=float)
    bad = hi_arr < lo_arr
    if np.any(bad):
        lo_arr = lo_arr.copy()
        hi_arr = hi_arr.copy()
        lo_arr[bad] = float(p["min"])
        hi_arr[bad] = float(p["max"])
    return rng.uniform(lo_arr, hi_arr)


def _sample_ordered_columns(
    pmap: dict[str, dict],
    names: Sequence[str],
    size: int,
    rng: np.random.Generator,
    max_attempts: int = 200,
) -> dict[str, np.ndarray]:
    """Sample columns satisfying a strict increasing order.

    The dimensions are tiny (3-4 columns), so local rejection is cheap and avoids
    global rejection collapse when several independent order constraints exist.
    """
    out = {name: np.empty(size, dtype=float) for name in names}
    filled = 0
    while filled < size:
        remaining = size - filled
        batch_n = max(remaining * 4, 256)
        batch = {
            name: rng.uniform(float(pmap[name]["min"]), float(pmap[name]["max"]), size=batch_n)
            for name in names
        }
        mask = np.ones(batch_n, dtype=bool)
        for earlier, later in zip(names, names[1:]):
            mask &= batch[earlier] < batch[later]
        valid_idx = np.flatnonzero(mask)
        if len(valid_idx) == 0:
            max_attempts -= 1
            if max_attempts <= 0:
                raise RuntimeError(
                    f"Could not sample ordered columns {list(names)} within configured bounds."
                )
            continue
        take = valid_idx[:remaining]
        for name in names:
            out[name][filled:filled + len(take)] = batch[name][take]
        filled += len(take)
    return out


def _sample_activity_columns(
    pmap: dict[str, dict],
    size: int,
    rng: np.random.Generator,
    max_attempts: int = 200,
) -> dict[str, np.ndarray]:
    """Sample activity_med/activity_high with derived activity_low valid."""
    names = ("activity_med", "activity_high")
    out = {name: np.empty(size, dtype=float) for name in names}
    filled = 0
    while filled < size:
        remaining = size - filled
        batch_n = max(remaining * 4, 256)
        med = rng.uniform(
            float(pmap["activity_med"]["min"]),
            float(pmap["activity_med"]["max"]),
            size=batch_n,
        )
        high = rng.uniform(
            float(pmap["activity_high"]["min"]),
            float(pmap["activity_high"]["max"]),
            size=batch_n,
        )
        activity_low = 1.0 - med - high
        mask = (activity_low >= 0.14) & (activity_low <= 0.81)
        valid_idx = np.flatnonzero(mask)
        if len(valid_idx) == 0:
            max_attempts -= 1
            if max_attempts <= 0:
                raise RuntimeError("Could not sample valid activity shares within configured bounds.")
            continue
        take = valid_idx[:remaining]
        out["activity_med"][filled:filled + len(take)] = med[take]
        out["activity_high"][filled:filled + len(take)] = high[take]
        filled += len(take)
    return out


def _sample_validity_aware_prior_batch(
    parameters: Sequence[dict],
    batch_n: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Draw a prior batch while respecting common algebraic constraints.

    This keeps the ABC generation-0 prior sampler viable for intentionally wide
    structural-capacity spaces.  Final validation still runs through
    `_valid_draw_mask`, so this only improves proposal efficiency.
    """
    pmap = _parameter_by_name(parameters)
    names = set(pmap)
    rows: dict[str, np.ndarray] = {}

    handled: set[str] = set()

    if {"activity_med", "activity_high"}.issubset(names):
        rows.update(_sample_activity_columns(pmap, batch_n, rng))
        handled.update({"activity_med", "activity_high"})

    if {"seed_chronic_share", "seed_aids_share"}.issubset(names):
        chronic = _sample_uniform_column(pmap, "seed_chronic_share", batch_n, rng)
        aids = _sample_uniform_column(
            pmap, "seed_aids_share", batch_n, rng, upper=1.0 - chronic
        )
        rows["seed_chronic_share"] = chronic
        rows["seed_aids_share"] = aids
        handled.update({"seed_chronic_share", "seed_aids_share"})

    if {"seed_msm_chronic_share", "seed_msm_aids_share"}.issubset(names):
        chronic = _sample_uniform_column(pmap, "seed_msm_chronic_share", batch_n, rng)
        aids = _sample_uniform_column(
            pmap, "seed_msm_aids_share", batch_n, rng, upper=1.0 - chronic
        )
        rows["seed_msm_chronic_share"] = chronic
        rows["seed_msm_aids_share"] = aids
        handled.update({"seed_msm_chronic_share", "seed_msm_aids_share"})

    if {"seed_non_msm_chronic_share", "seed_non_msm_aids_share"}.issubset(names):
        chronic = _sample_uniform_column(pmap, "seed_non_msm_chronic_share", batch_n, rng)
        lower = rows.get("seed_msm_aids_share")
        aids = _sample_uniform_column(
            pmap,
            "seed_non_msm_aids_share",
            batch_n,
            rng,
            lower=lower,
            upper=1.0 - chronic,
        )
        rows["seed_non_msm_chronic_share"] = chronic
        rows["seed_non_msm_aids_share"] = aids
        handled.update({"seed_non_msm_chronic_share", "seed_non_msm_aids_share"})

    if {"orient_het_male", "orient_msm"}.issubset(names):
        orient_het_male = _sample_uniform_column(
            pmap,
            "orient_het_male",
            batch_n,
            rng,
            lower=max(1.0, float(pmap["orient_het_male"]["min"])),
            upper=float(pmap["orient_msm"]["max"]),
        )
        orient_msm = _sample_uniform_column(
            pmap, "orient_msm", batch_n, rng, lower=orient_het_male
        )
        rows["orient_het_male"] = orient_het_male
        rows["orient_msm"] = orient_msm
        handled.update({"orient_het_male", "orient_msm"})
    elif {"orient_female", "orient_msm"}.issubset(names):
        orient_female = _sample_uniform_column(
            pmap,
            "orient_female",
            batch_n,
            rng,
            upper=min(1.0, float(pmap["orient_msm"]["max"])),
        )
        orient_msm = _sample_uniform_column(
            pmap, "orient_msm", batch_n, rng, lower=np.maximum(1.0, orient_female)
        )
        rows["orient_female"] = orient_female
        rows["orient_msm"] = orient_msm
        handled.update({"orient_female", "orient_msm"})

    for group_names in (
        ("contact_low", "contact_med", "contact_high"),
        ("diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2019"),
        ("diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2015"),
        ("art_init_1996", "art_init_2000", "art_init_2015", "art_init_2020"),
    ):
        if set(group_names).issubset(names):
            rows.update(_sample_ordered_columns(pmap, group_names, batch_n, rng))
            handled.update(group_names)

    for p in parameters:
        name = p["name"]
        if name in handled:
            continue
        rows[name] = rng.uniform(float(p["min"]), float(p["max"]), size=batch_n)

    return _with_derived_parameters(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Prior sampling
# ---------------------------------------------------------------------------


def _sample_valid_prior(
    parameters: Sequence[dict],
    n: int,
    rng: np.random.Generator,
    max_attempts: int = 20,
) -> list[dict]:
    """Sample n valid particles uniformly from prior bounds.

    Derived constraints (activity_low, seed_acute_share, contact ordering,
    diagnosis and ART monotonicity) are enforced in the proposal batch where
    possible, then checked by the shared validity mask.
    """
    valid_parts: list[pd.DataFrame] = []
    for attempt in range(max_attempts):
        batch_n = max(n * 3, 100) if attempt == 0 else max(n * 2, 50)
        df = _sample_validity_aware_prior_batch(parameters, batch_n, rng)
        mask = _valid_draw_mask(df)
        valid_parts.append(df[mask])
        combined = pd.concat(valid_parts, ignore_index=True)
        if len(combined) >= n:
            return combined.iloc[:n].to_dict("records")
    combined = pd.concat(valid_parts, ignore_index=True) if valid_parts else pd.DataFrame()
    if len(combined) < n:
        raise RuntimeError(
            f"Could not generate {n} valid prior particles after {max_attempts} attempts; "
            f"only {len(combined)} passed constraints."
        )
    return combined.iloc[:n].to_dict("records")


# ---------------------------------------------------------------------------
# Perturbation and resampling
# ---------------------------------------------------------------------------


def _perturb_particle(
    particle: dict,
    bounds: dict[str, tuple[float, float]],
    bandwidth: dict[str, float],
    rng: np.random.Generator,
    max_attempts: int = 50,
) -> dict | None:
    """Perturb a particle with a Gaussian kernel clipped to prior bounds.

    Derived parameters (activity_low, seed_acute_share) are recomputed after
    each perturbation proposal. Returns None if no valid perturbation is found
    within max_attempts.
    """
    # Only perturb free parameters that appear in the particle dict.
    free_names = [name for name in bounds if name in particle]
    for _ in range(max_attempts):
        proposal = dict(particle)
        for name in free_names:
            lo, hi = bounds[name]
            bw = bandwidth.get(name, 0.0)
            raw = float(particle[name]) + rng.normal(0.0, bw)
            proposal[name] = float(np.clip(raw, lo, hi))
        # Recompute derived parameters from the perturbed free parameters.
        df = _with_derived_parameters(pd.DataFrame([proposal]))
        if _valid_draw_mask(df).iloc[0]:
            return df.iloc[0].to_dict()
    return None


def _resample_particles(
    population: list[dict],
    distances: list[float],
    n: int,
    rng: np.random.Generator,
) -> tuple[list[dict], list[int]]:
    """Resample n particles with probability proportional to inverse ABC distance.

    Returns (resampled_particles, resample_indices) where resample_indices[i]
    is the index into population of the i-th returned particle.

    Smaller distance → higher weight. Infinite-distance particles are assigned
    a weight corresponding to 10× the largest finite distance.
    """
    dist_arr = np.array(distances, dtype=float)
    finite = np.isfinite(dist_arr)
    if not finite.any():
        indices = rng.integers(0, len(population), size=n)
        return [copy.deepcopy(population[i]) for i in indices], list(map(int, indices))
    max_finite = dist_arr[finite].max()
    dist_arr = np.where(finite, dist_arr, max_finite * 10.0 + 1.0)
    weights = 1.0 / (dist_arr + 1e-9)
    weights /= weights.sum()
    indices = rng.choice(len(population), size=n, replace=True, p=weights)
    return [copy.deepcopy(population[i]) for i in indices], list(map(int, indices))


# ---------------------------------------------------------------------------
# Worker (module-level for multiprocessing pickling)
# ---------------------------------------------------------------------------


def _run_particle_worker(payload: dict) -> dict:
    """Evaluate one ABC-SMC particle and return a result row.

    Module-level so it can be pickled by ProcessPoolExecutor.
    """
    particle_id = payload["particle_id"]
    generation = payload["generation"]
    base_config = payload["base_config"]
    calib_space = payload["calib_space"]
    params = payload["params"]
    targets = payload["targets"]
    seed = payload["seed"]
    balance_target_families = bool(payload.get("balance_target_families", False))
    acceptance_gate_settings = payload.get("acceptance_gate_settings")
    distance_settings = payload.get("distance_settings")
    proposal_source = payload.get("proposal_source", "prior")
    anchor_config_path = payload.get("anchor_config_path", "")
    parent_particle_id = payload.get("parent_particle_id", "")
    historical_shape_settings: HistoricalShapeGateSettings | None = payload.get(
        "historical_shape_settings"
    )
    historical_shape_weights: dict[str, float] = payload.get("historical_shape_weights", {})
    non_msm_settings: NonMsmDiagnosisSettings | None = payload.get("non_msm_diagnosis_settings")
    non_msm_weight: float = float(payload.get("non_msm_diagnosis_weight", 1.0))
    simulation_start_year = payload.get("simulation_start_year")
    scoring_start_year = payload.get("scoring_start_year")

    try:
        config = inject_params(
            base_config, params, calib_space, deterministic_mode=True,
            simulation_start_year=simulation_start_year,
        )
        result = _run_simulation_quietly(config, seed=seed)
        extract_start = scoring_start_year if scoring_start_year is not None else CALIB_START_YEAR
        outputs = extract_annual_outputs(result, extract_start, CALIB_END_YEAR)
        score = score_draw(outputs, targets, balance_families=balance_target_families)

        row: dict = {
            "particle_id": particle_id,
            "generation": generation,
            "particle_seed": seed,
            "proposal_source": proposal_source,
            "anchor_config_path": anchor_config_path,
            "parent_particle_id": parent_particle_id,
            **{k: float(v) for k, v in params.items()},
            "total_ll": score["total_ll"],
            "collapsed": score["collapsed"],
            "n_years_scored": score["n_years_scored"],
            "missing_outputs": ",".join(score.get("missing_outputs", [])),
            "error": "",
        }
        for indicator, value in score["ll_by_indicator"].items():
            row[f"ll_{indicator}"] = value
        for family, value in score.get("ll_by_family", {}).items():
            row[f"ll_family_{family}"] = value

        # Hard acceptance gates (V13.8.2 recent-endpoint gates).
        acceptance = None
        if acceptance_gate_settings is not None:
            acceptance = evaluate_acceptance_gates(
                outputs, targets, settings=acceptance_gate_settings
            )
            row.update(acceptance_gate_row_fields(acceptance))

        # Continuous ABC distance (reuses acceptance dict to avoid re-evaluation).
        dist_result = evaluate_calibration_distance(
            outputs,
            targets,
            settings=distance_settings,
            acceptance=acceptance,
            collapsed=bool(score["collapsed"]),
            error=None,
        )
        row.update(calibration_distance_row_fields(dist_result))

        # Historical shape gates (V13.9, opt-in only via --historical-shape-gates).
        if historical_shape_settings is not None and historical_shape_settings.enabled:
            hist_gates = evaluate_historical_shape_gates(
                outputs, targets, settings=historical_shape_settings
            )
            row.update(historical_shape_gate_row_fields(hist_gates))

            # Override overall_accept to be AND of V13.8.2 gates AND historical gates.
            if not hist_gates["overall_accept"]:
                row["overall_accept"] = False
                extra_failures = ";".join(hist_gates.get("failures", []))
                existing = str(row.get("acceptance_fail_reasons") or "")
                row["acceptance_fail_reasons"] = (
                    f"{existing};{extra_failures}" if existing else extra_failures
                )
                row["acceptance_n_failures"] = (
                    int(row.get("acceptance_n_failures") or 0)
                    + len(hist_gates.get("failures", []))
                )
                row["abc_overall_accept"] = False

            # Historical shape distance (adds to abc_distance for search guidance).
            hist_dist = evaluate_historical_shape_distance(
                outputs, targets,
                settings=historical_shape_settings,
                weights=historical_shape_weights,
            )
            row.update(historical_shape_distance_row_fields(hist_dist))
            current_dist = row.get("abc_distance", math.inf)
            if math.isfinite(float(current_dist)):
                row["abc_distance"] = float(current_dist) + float(hist_dist.get("distance", 0.0))

        # Non-MSM diagnosis gate (V13.9.5, opt-in only via --non-msm-diagnosis-gate).
        # When enabled, gate failure sets overall_accept=False and the weighted
        # distance contribution is added to abc_distance for search guidance.
        # Existing gates (V13.8.2 and V13.9 historical shape) are never altered.
        if non_msm_settings is not None and non_msm_settings.enabled:
            non_msm_gate = evaluate_non_msm_diagnosis_gate(
                outputs, targets, settings=non_msm_settings
            )
            non_msm_dist = non_msm_diagnosis_distance(
                outputs, targets, settings=non_msm_settings
            )
            row.update(non_msm_diagnosis_row_fields(non_msm_gate, non_msm_dist))

            if not non_msm_gate["passed"]:
                row["overall_accept"] = False
                extra_failures = ";".join(non_msm_gate.get("failures", []))
                existing = str(row.get("acceptance_fail_reasons") or "")
                row["acceptance_fail_reasons"] = (
                    f"{existing};{extra_failures}" if existing else extra_failures
                )
                row["acceptance_n_failures"] = (
                    int(row.get("acceptance_n_failures") or 0)
                    + len(non_msm_gate.get("failures", []))
                )
                row["abc_overall_accept"] = False

            current_dist = row.get("abc_distance", math.inf)
            if math.isfinite(float(current_dist)):
                row["abc_distance"] = float(current_dist) + float(
                    non_msm_dist.get("distance", 0.0)
                ) * non_msm_weight

        return row

    except Exception as exc:
        return {
            "particle_id": particle_id,
            "generation": generation,
            "particle_seed": seed,
            "proposal_source": proposal_source,
            "anchor_config_path": anchor_config_path,
            "parent_particle_id": parent_particle_id,
            **{k: float(v) for k, v in params.items()},
            "total_ll": float("-inf"),
            "collapsed": True,
            "n_years_scored": 0,
            "missing_outputs": "",
            "error": f"{type(exc).__name__}: {exc}",
            "abc_distance": float("inf"),
        }


def _run_generation_payloads(payloads: list[dict], workers: int) -> list[dict]:
    if workers == 1:
        rows = [_run_particle_worker(p) for p in payloads]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_run_particle_worker, p): p["particle_id"]
                for p in payloads
            }
            for future in as_completed(future_map):
                rows.append(future.result())
    return sorted(rows, key=lambda r: int(r.get("particle_id", 10**18)))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _abc_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Sort particles by: overall_accept desc, n_failures asc, abc_distance asc, total_ll desc."""
    work = df.copy()
    work["_accept"] = (
        work["overall_accept"].map(lambda v: False if pd.isna(v) else bool(v)).astype(int)
        if "overall_accept" in work.columns
        else 0
    )
    work["_nfail"] = (
        pd.to_numeric(work["acceptance_n_failures"], errors="coerce").fillna(999)
        if "acceptance_n_failures" in work.columns
        else 999
    )
    work["_dist"] = (
        pd.to_numeric(work["abc_distance"], errors="coerce")
        .replace([np.inf], 1e12)
        .fillna(1e12)
        if "abc_distance" in work.columns
        else 1e12
    )
    work["_ll"] = (
        pd.to_numeric(work["total_ll"], errors="coerce").replace([np.inf], np.nan)
    )
    return (
        work.sort_values(
            ["_accept", "_nfail", "_dist", "_ll"],
            ascending=[False, True, True, False],
            na_position="last",
        )
        .drop(columns=["_accept", "_nfail", "_dist", "_ll"])
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Survivor eligibility
# ---------------------------------------------------------------------------


def _eligible_survivors_mask(gen_df: pd.DataFrame) -> pd.Series:
    """Boolean mask: True for rows eligible to enter the survivor pool.

    A row is ineligible—and must not be resampled in later generations—if any
    of the following hold:
      - collapsed is True (or NaN, treated as collapsed)
      - error is a non-empty, non-whitespace string
      - abc_distance is not finite (inf, -inf, or NaN)
    """
    mask = pd.Series(True, index=gen_df.index)

    if "collapsed" in gen_df.columns:
        # map avoids pandas FutureWarning on object-dtype fillna; treats NA as collapsed.
        mask &= ~gen_df["collapsed"].map(lambda v: True if pd.isna(v) else bool(v))

    if "error" in gen_df.columns:
        mask &= gen_df["error"].fillna("").astype(str).str.strip() == ""

    if "abc_distance" in gen_df.columns:
        dist_numeric = pd.to_numeric(gen_df["abc_distance"], errors="coerce")
        mask &= np.isfinite(dist_numeric.values.astype(float))

    return mask


def _select_survivors(
    gen_df: pd.DataFrame,
    param_names_set: set[str],
    tolerance: float,
) -> tuple[list[dict], list[float], list[int]]:
    """Build the survivor pool for resampling in the next generation.

    Only eligible rows (non-collapsed, no error, finite distance) contribute.
    The tolerance quantile is then applied to their distances to select the
    better-fitting subset.  Returns ([], [], []) when no eligible rows exist so
    the caller can fall back to fresh prior sampling.

    Returns:
        (next_pop, next_dist, next_ids) where next_ids[i] is the particle_id
        of the i-th survivor (used for parent tracking in later generations).
    """
    eligible_mask = _eligible_survivors_mask(gen_df)
    eligible_df = gen_df[eligible_mask].reset_index(drop=True)
    if eligible_df.empty:
        return [], [], []

    dist_arr = pd.to_numeric(
        eligible_df.get("abc_distance", pd.Series(dtype=float)), errors="coerce"
    ).values.astype(float)
    threshold = float(np.nanquantile(dist_arr, tolerance))

    next_pop: list[dict] = []
    next_dist: list[float] = []
    next_ids: list[int] = []
    for i, row_dict in enumerate(eligible_df.to_dict("records")):
        if dist_arr[i] <= threshold:
            particle_dict = {
                name: row_dict[name] for name in param_names_set if name in row_dict
            }
            for derived in ("activity_low", "seed_acute_share"):
                if derived in row_dict:
                    particle_dict[derived] = row_dict[derived]
            next_pop.append(particle_dict)
            next_dist.append(float(dist_arr[i]))
            next_ids.append(int(row_dict.get("particle_id", -1)))
    return next_pop, next_dist, next_ids


# ---------------------------------------------------------------------------
# Generation summary
# ---------------------------------------------------------------------------


def _generation_summary(df: pd.DataFrame, generation: int, tolerance: float) -> dict:
    n_total = len(df)
    n_collapsed = int(df["collapsed"].fillna(True).sum()) if "collapsed" in df.columns else 0
    dist_s = pd.to_numeric(
        df.get("abc_distance", pd.Series(dtype=float)), errors="coerce"
    ).replace([np.inf], np.nan)
    ll_s = pd.to_numeric(
        df.get("total_ll", pd.Series(dtype=float)), errors="coerce"
    ).replace([np.inf, float("-inf")], np.nan)
    finite_d = dist_s.dropna()
    finite_ll = ll_s.dropna()

    n_accept = 0
    gate_pass_rate: float = float("nan")
    if "overall_accept" in df.columns:
        n_accept = int(df["overall_accept"].fillna(False).sum())
        gate_pass_rate = n_accept / n_total if n_total > 0 else 0.0

    median_failures: float = float("nan")
    if "acceptance_n_failures" in df.columns:
        fail_s = pd.to_numeric(df["acceptance_n_failures"], errors="coerce").dropna()
        if not fail_s.empty:
            median_failures = float(fail_s.median())

    n_eligible = int(_eligible_survivors_mask(df).sum())

    return {
        "generation": generation,
        "n_particles": n_total,
        "n_collapsed": n_collapsed,
        "collapse_rate": n_collapsed / n_total if n_total > 0 else float("nan"),
        "n_eligible_survivors": n_eligible,
        "n_gate_pass": n_accept,
        "gate_pass_rate": gate_pass_rate,
        "median_failures": median_failures,
        "tolerance_quantile": tolerance,
        "abc_distance_min": float(finite_d.min()) if not finite_d.empty else float("nan"),
        "abc_distance_median": float(finite_d.median()) if not finite_d.empty else float("nan"),
        "abc_distance_p25": float(finite_d.quantile(0.25)) if not finite_d.empty else float("nan"),
        "abc_distance_p75": float(finite_d.quantile(0.75)) if not finite_d.empty else float("nan"),
        "total_ll_max": float(finite_ll.max()) if not finite_ll.empty else float("nan"),
        "total_ll_median": float(finite_ll.median()) if not finite_ll.empty else float("nan"),
    }


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _git_provenance() -> dict:
    """Attempt to read git commit hash and dirty status. Never raises."""
    result: dict = {}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(ROOT),
        )
        if commit.returncode == 0:
            result["git_commit"] = commit.stdout.strip()
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=5, cwd=str(ROOT),
            )
            result["git_dirty"] = (
                bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
            )
        else:
            result["git_commit"] = None
            result["git_dirty"] = None
    except Exception:
        result["git_commit"] = None
        result["git_dirty"] = None
    return result


# ---------------------------------------------------------------------------
# Anchor loading
# ---------------------------------------------------------------------------


def _load_anchor_particle(
    anchor_config_path: Path,
    parameters: Sequence[dict],
) -> dict | None:
    """Extract calibration parameter values from an anchor config JSON.

    Primary source: config_path field of each parameter (reflects actual
    runnable config).  Fallback: calibration_metadata.abc_smc_calibration
    .parameters embedded in the config by a previous ABC-SMC run.

    Returns a validated particle dict (with derived fields), or None on failure.
    """
    try:
        config = load_json(anchor_config_path)
    except Exception as exc:
        print(f"  WARNING: Could not load anchor config {anchor_config_path}: {exc}")
        return None

    # Embedded parameter fallback from previous ABC-SMC metadata.
    fallback_params: dict = {}
    try:
        fallback_params = (
            config.get("calibration_metadata", {})
            .get("abc_smc_calibration", {})
            .get("parameters", {})
        )
    except (AttributeError, TypeError):
        pass

    def _shared_seed_stage_value(stage: str) -> float | None:
        """Read a shared seed-stage share from metadata or config if present."""
        fallback_name = f"seed_{stage.lower()}_share"
        if fallback_name in fallback_params:
            try:
                return float(fallback_params[fallback_name])
            except (TypeError, ValueError):
                pass
        try:
            return float(
                get_nested(
                    config,
                    f"population.epidemic_seed.stage_distribution.value.{stage}",
                )
            )
        except (KeyError, TypeError, ValueError):
            return None

    shared_seed_stage_fallbacks = {
        "seed_msm_chronic_share": ("Chronic",),
        "seed_non_msm_chronic_share": ("Chronic",),
        "seed_msm_aids_share": ("AIDS",),
        "seed_non_msm_aids_share": ("AIDS",),
    }

    particle: dict = {}
    for param in parameters:
        name = param["name"]
        config_path_str = param.get("config_path")

        # Primary: extract via config_path.
        if config_path_str:
            try:
                value = get_nested(config, config_path_str)
                particle[name] = float(value)
                continue
            except (KeyError, TypeError, ValueError):
                pass

        # Fallback: embedded ABC-SMC parameter dict.
        if name in fallback_params:
            try:
                particle[name] = float(fallback_params[name])
                continue
            except (TypeError, ValueError):
                pass

        # Compatibility path for V13.14 shared seed-stage anchors used in
        # V13.15 orientation-specific seed-stage diagnostic spaces.
        if name in shared_seed_stage_fallbacks:
            stage = shared_seed_stage_fallbacks[name][0]
            shared_value = _shared_seed_stage_value(stage)
            if shared_value is not None:
                particle[name] = shared_value
                continue

        print(
            f"  WARNING: Could not extract '{name}' from anchor "
            f"{anchor_config_path.name}; skipping anchor."
        )
        return None

    # Recompute derived parameters and validate constraints.
    df = _with_derived_parameters(pd.DataFrame([particle]))
    if not _valid_draw_mask(df).iloc[0]:
        print(
            f"  WARNING: Anchor {anchor_config_path.name} failed constraint "
            "validation; skipping."
        )
        return None

    return df.iloc[0].to_dict()


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_abc_best_fit_configs(
    ranked: pd.DataFrame,
    base_config: dict,
    calib_space: dict,
    output_dir: Path,
    top_n: int,
    metadata_stub: dict,
) -> list[str]:
    """Write top N particles as model config JSON files with ABC metadata."""
    paths: list[str] = []
    param_names = [p["name"] for p in calib_space.get("parameters", [])]

    dist_col = pd.to_numeric(
        ranked.get("abc_distance", pd.Series(dtype=float)), errors="coerce"
    )
    ll_col = pd.to_numeric(ranked["total_ll"], errors="coerce")
    valid_mask = np.isfinite(dist_col) | np.isfinite(ll_col)
    valid = ranked[valid_mask].head(top_n)

    for idx, row in enumerate(valid.itertuples(index=False), start=1):
        row_dict = row._asdict()
        vector = {name: row_dict[name] for name in param_names if name in row_dict}
        if "activity_low" in ranked.columns:
            vector["activity_low"] = row_dict.get("activity_low")

        cfg = inject_params(
            base_config,
            vector,
            calib_space,
            deterministic_mode=True,
            simulation_start_year=metadata_stub.get("simulation_start_year"),
        )
        cfg.setdefault("calibration_metadata", {})

        try:
            n_fail = int(row_dict.get("acceptance_n_failures", -1))
        except (ValueError, TypeError):
            n_fail = -1

        cfg["calibration_metadata"]["abc_smc_calibration"] = {
            **metadata_stub,
            "rank": idx,
            "particle_id": int(row_dict.get("particle_id", -1)),
            "particle_seed": int(row_dict.get("particle_seed", -1)),
            "generation": int(row_dict.get("generation", -1)),
            "total_ll": float(row_dict["total_ll"]),
            "abc_distance": float(row_dict.get("abc_distance", float("nan"))),
            "overall_accept": bool(row_dict.get("overall_accept", False)),
            "acceptance_n_failures": n_fail,
            "parameters": {k: v for k, v in vector.items() if k not in {"activity_low"}},
        }
        path = output_dir / f"abc_smc_best_fit_rank_{idx:03d}.json"
        write_json(path, cfg)
        paths.append(str(path))

    return paths


def _write_abc_excel_summary(
    path: Path,
    all_particles: pd.DataFrame,
    generation_summary: pd.DataFrame,
    calib_space: dict,
    metadata: dict,
    top_n: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = _abc_rank(all_particles)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        ranked.head(top_n).to_excel(writer, sheet_name="Top_Particles", index=False)
        ranked.to_excel(writer, sheet_name="All_Particles", index=False)
        generation_summary.to_excel(writer, sheet_name="Generation_Summary", index=False)
        pd.DataFrame(calib_space.get("parameters", [])).to_excel(
            writer, sheet_name="Parameter_Space", index=False
        )
        pd.DataFrame(calib_space.get("fixed_parameters", [])).to_excel(
            writer, sheet_name="Fixed_Parameters", index=False
        )
        if "target_summary" in metadata:
            pd.DataFrame(metadata["target_summary"]).to_excel(
                writer, sheet_name="Target_Summary", index=False
            )
        pd.DataFrame(
            [
                {
                    "key": key,
                    "value": json.dumps(value) if isinstance(value, (dict, list)) else value,
                }
                for key, value in metadata.items()
            ]
        ).to_excel(writer, sheet_name="Metadata", index=False)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_abc_smc_calibration(args: argparse.Namespace) -> dict:
    started_at = datetime.now()
    started_monotonic = time.monotonic()

    config_path = Path(args.config).resolve()
    calib_space_path = Path(args.calib_space).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Warn if this output directory already has ABC-SMC runs.
    existing_meta = list(output_dir.glob("abc_smc_metadata_*.json"))
    if existing_meta:
        print(
            "WARNING: output directory already contains ABC-SMC runs; new timestamped files\n"
            "will be appended. Use a fresh --output-dir for thesis-final runs."
        )

    source_sha_before = sha256_file(config_path)
    base_config = load_json(config_path)
    raw_calib_space = load_json(calib_space_path)
    calib_space = filter_calib_space_for_stage(raw_calib_space, args.stage)
    validate_config_paths(base_config, calib_space)

    workbook_path = Path(args.targets_workbook).resolve()
    mapping_path = Path(args.target_mapping).resolve()
    targets = load_disease_targets(
        workbook_path,
        mapping_path,
        stage=args.stage,
        start_year=1990,
        end_year=CALIB_END_YEAR,
    )
    target_source_path = str(workbook_path)

    parameters = calib_space.get("parameters", [])
    bounds = _parameter_bounds(parameters)
    bw = _bandwidth(parameters, fraction=float(getattr(args, "perturbation_bandwidth", 0.10)))
    workers = os.cpu_count() if args.workers == -1 else max(1, args.workers)
    balance_target_families = bool(getattr(args, "family_balanced_score", False))
    sim_start_override = getattr(args, "simulation_start_year", None)
    score_start_override = getattr(args, "scoring_start_year", None)

    # When historical shape gates are requested, acceptance gate audit is forced
    # on so that overall_accept reflects both gate sets.
    _hist_gates_enabled = bool(getattr(args, "historical_shape_gates", False))
    _soft_aids_death_gate = bool(getattr(args, "soft_aids_death_gate", False))
    acceptance_gate_settings: AcceptanceGateSettings | None = (
        AcceptanceGateSettings(aids_death_hard_gate=not _soft_aids_death_gate)
        if bool(getattr(args, "acceptance_gate_audit", False)) or _hist_gates_enabled
        else None
    )

    historical_shape_settings: HistoricalShapeGateSettings | None = (
        HistoricalShapeGateSettings(
            enabled=True,
            plhiv_hist_mape_ceiling=float(args.plhiv_hist_mape_ceiling),
            plhiv_anchor_max_multiplier=float(args.plhiv_anchor_max_multiplier),
            diagnoses_hist_mape_ceiling=float(args.diagnoses_hist_mape_ceiling),
            diagnoses_spike_ratio_ceiling=float(args.diagnoses_spike_ratio_ceiling),
            msm_diag_hist_mape_ceiling=float(args.msm_diag_hist_mape_ceiling),
            msm_diag_early_ratio_ceiling=float(args.msm_diag_early_ratio_ceiling),
            aids_cases_ratio_ceiling=float(args.aids_cases_ratio_ceiling),
            aids_cases_hist_mape_ceiling=float(args.aids_cases_hist_mape_ceiling),
        )
        if _hist_gates_enabled
        else None
    )
    historical_shape_weights: dict[str, float] = {
        "plhiv_hist":       float(getattr(args, "weight_historical_plhiv",          1.0)),
        "diagnoses_hist":   float(getattr(args, "weight_historical_diagnoses",       1.0)),
        "msm_diag_hist":    float(getattr(args, "weight_historical_msm_diagnoses",   1.0)),
        "aids_cases_hist":  float(getattr(args, "weight_historical_aids_cases",      1.0)),
    }

    component_weights: dict[str, float] = {
        "diagnosis":   float(getattr(args, "weight_diagnosis",   1.0)),
        "plhiv":       float(getattr(args, "weight_plhiv",       1.0)),
        "plhiv_sex":   float(getattr(args, "weight_plhiv_sex",   0.5)),
        "incidence":   float(getattr(args, "weight_incidence",   1.0)),
        "aids_deaths": float(getattr(args, "weight_aids_deaths", 1.0)),
        "art_stock":   float(getattr(args, "weight_art_stock",   1.0)),
    }

    # V13.9.5 non-MSM diagnosis gate (opt-in; no effect unless --non-msm-diagnosis-gate).
    _non_msm_gate_enabled = bool(getattr(args, "non_msm_diagnosis_gate", False))
    non_msm_diagnosis_settings: NonMsmDiagnosisSettings | None = (
        NonMsmDiagnosisSettings(
            enabled=True,
            latest_floor=float(args.non_msm_latest_floor),
            latest_ceiling=float(args.non_msm_latest_ceiling),
            recent_mape_ceiling=float(args.non_msm_recent_mape_ceiling),
            hist_mape_ceiling=float(args.non_msm_hist_mape_ceiling),
        )
        if _non_msm_gate_enabled
        else None
    )
    non_msm_diagnosis_weight: float = float(getattr(args, "weight_non_msm_diagnosis", 1.0))
    distance_settings = CalibrationDistanceSettings(
        gate_settings=acceptance_gate_settings or AcceptanceGateSettings(),
        component_weights=component_weights,
    )

    rng = np.random.default_rng(args.seed)
    run_stamp = started_at.strftime("%Y%m%d_%H%M%S")

    # --- Load anchor configs (generation-0 elite injection) ---
    anchor_config_paths_requested: list[str] = []
    for p in getattr(args, "anchor_config", []) or []:
        anchor_config_paths_requested.append(str(Path(p).resolve()))
    for pattern in getattr(args, "anchor_glob", []) or []:
        anchor_config_paths_requested.extend(
            sorted(str(Path(p).resolve()) for p in _glob.glob(pattern))
        )
    # Deduplicate while preserving order.
    seen_paths: set[str] = set()
    unique_anchor_paths: list[str] = []
    for p in anchor_config_paths_requested:
        if p not in seen_paths:
            seen_paths.add(p)
            unique_anchor_paths.append(p)
    anchor_config_paths_requested = unique_anchor_paths

    anchor_max = getattr(args, "anchor_max", None)
    if anchor_max is not None and len(anchor_config_paths_requested) > anchor_max:
        print(
            f"  --anchor-max {anchor_max}: keeping first {anchor_max} of "
            f"{len(anchor_config_paths_requested)} requested anchor paths."
        )
        anchor_config_paths_requested = anchor_config_paths_requested[:anchor_max]

    valid_anchors: list[tuple[Path, dict]] = []
    invalid_anchor_paths: list[str] = []
    for path_str in anchor_config_paths_requested:
        anchor_path = Path(path_str)
        particle = _load_anchor_particle(anchor_path, parameters)
        if particle is not None:
            valid_anchors.append((anchor_path, particle))
        else:
            invalid_anchor_paths.append(path_str)

    n_particles = args.particles
    if len(valid_anchors) > n_particles:
        print(
            f"  WARNING: {len(valid_anchors)} valid anchors > --particles {n_particles}; "
            f"keeping first {n_particles} anchors."
        )
        valid_anchors = valid_anchors[:n_particles]

    if anchor_config_paths_requested:
        print(
            f"  Elite anchors: {len(valid_anchors)} valid, "
            f"{len(invalid_anchor_paths)} invalid"
        )

    normalized_targets_path = output_dir / f"normalized_targets_{args.stage}_{run_stamp}.csv"
    write_normalized_targets_csv(normalized_targets_path, targets)

    all_rows: list[dict] = []
    generation_summaries: list[dict] = []
    current_population: list[dict] = []
    current_distances: list[float] = []
    current_particle_ids: list[int] = []
    particle_counter = 0
    n_generations = args.generations

    print(
        f"ABC-SMC: {n_particles} particles × {n_generations} generation(s), {workers} worker(s)"
    )

    param_names_set = {p["name"] for p in parameters}

    for gen in range(n_generations):
        tolerance = _survival_quantile(gen)
        print(f"  Generation {gen}: tolerance={tolerance:.2f}")

        proposal_sources: list[str] = []
        anchor_config_paths_for_proposals: list[str] = []
        parent_particle_ids: list[Any] = []

        if gen == 0 or not current_population:
            proposals: list[dict] = []

            # In generation 0, inject valid anchors first.
            if gen == 0:
                for anchor_path, anchor_particle in valid_anchors:
                    proposals.append(dict(anchor_particle))
                    proposal_sources.append("anchor")
                    anchor_config_paths_for_proposals.append(str(anchor_path))
                    parent_particle_ids.append("")

            # Fill remaining slots from prior.
            n_prior = n_particles - len(proposals)
            if n_prior > 0:
                for p in _sample_valid_prior(parameters, n_prior, rng):
                    proposals.append(p)
                    proposal_sources.append("prior")
                    anchor_config_paths_for_proposals.append("")
                    parent_particle_ids.append("")
        else:
            resampled, resample_indices = _resample_particles(
                current_population, current_distances, n_particles, rng
            )
            proposals = []
            for parent, res_idx in zip(resampled, resample_indices):
                parent_pid = (
                    current_particle_ids[res_idx]
                    if res_idx < len(current_particle_ids)
                    else ""
                )
                perturbed = _perturb_particle(parent, bounds, bw, rng)
                proposals.append(perturbed if perturbed is not None else dict(parent))
                proposal_sources.append("perturbed")
                anchor_config_paths_for_proposals.append("")
                parent_particle_ids.append(parent_pid)

        payloads: list[dict] = []
        for i, proposal in enumerate(proposals):
            particle_counter += 1
            payloads.append(
                {
                    "particle_id": particle_counter,
                    "generation": gen,
                    "base_config": base_config,
                    "calib_space": calib_space,
                    "params": proposal,
                    "targets": targets,
                    "seed": args.seed + particle_counter,
                    "balance_target_families": balance_target_families,
                    "acceptance_gate_settings": acceptance_gate_settings,
                    "distance_settings": distance_settings,
                    "historical_shape_settings": historical_shape_settings,
                    "historical_shape_weights": historical_shape_weights,
                    "non_msm_diagnosis_settings": non_msm_diagnosis_settings,
                    "non_msm_diagnosis_weight": non_msm_diagnosis_weight,
                    "proposal_source": proposal_sources[i],
                    "anchor_config_path": anchor_config_paths_for_proposals[i],
                    "parent_particle_id": parent_particle_ids[i],
                    "simulation_start_year": sim_start_override,
                    "scoring_start_year": score_start_override,
                }
            )

        rows = _run_generation_payloads(payloads, workers)
        all_rows.extend(rows)

        gen_df = pd.DataFrame(rows)
        gen_summary = _generation_summary(gen_df, gen, tolerance)
        generation_summaries.append(gen_summary)

        print(
            f"    n={gen_summary['n_particles']}  "
            f"eligible={gen_summary['n_eligible_survivors']}  "
            f"dist_min={gen_summary['abc_distance_min']:.4f}  "
            f"gate_pass={gen_summary['gate_pass_rate']:.3f}  "
            f"collapse={gen_summary['collapse_rate']:.3f}"
        )

        # Select survivors for the next generation.
        # Collapsed, errored, and non-finite-distance rows are excluded before
        # the tolerance quantile is applied, preventing bad particles from
        # contaminating later generations.
        next_pop, next_dist, next_ids = _select_survivors(gen_df, param_names_set, tolerance)
        if not next_pop:
            print(
                f"    WARNING: all particles in generation {gen} are ineligible "
                f"(collapsed, errored, or non-finite distance). "
                f"Next generation will sample fresh from the prior."
            )
        current_population = next_pop
        current_distances = next_dist
        current_particle_ids = next_ids

    # Rank all evaluated particles.
    all_df = pd.DataFrame(all_rows)
    final_ranked = _abc_rank(all_df)

    # --- Write outputs ---
    particles_csv = output_dir / f"abc_smc_particles_{run_stamp}.csv"
    final_ranked.to_csv(particles_csv, index=False)

    gen_summary_df = pd.DataFrame(generation_summaries)
    gen_summary_csv = output_dir / f"abc_smc_generation_summary_{run_stamp}.csv"
    gen_summary_df.to_csv(gen_summary_csv, index=False)

    source_sha_after = sha256_file(config_path)
    completed_at = datetime.now()
    duration_seconds = round(time.monotonic() - started_monotonic, 2)

    metadata_stub: dict = {
        "runner": "abc_smc",
        "source_config_path": str(config_path),
        "source_sha256_before": source_sha_before,
        "created_at": started_at.isoformat(timespec="seconds"),
        "seed": args.seed,
        "seed_policy": "particle_seed = base_seed + particle_id",
        "base_seed": args.seed,
        "stage": args.stage,
        "particles": n_particles,
        "generations": n_generations,
        "workers": workers,
        "deterministic_calibration": True,
        "family_balanced_score": balance_target_families,
        "acceptance_gate_audit": acceptance_gate_settings is not None,
        "soft_aids_death_gate": _soft_aids_death_gate,
        "historical_shape_gates": _hist_gates_enabled,
        "historical_shape_weights": historical_shape_weights if _hist_gates_enabled else {},
        "historical_shape_settings": (
            {
                "plhiv_hist_mape_ceiling": historical_shape_settings.plhiv_hist_mape_ceiling,
                "plhiv_anchor_max_multiplier": historical_shape_settings.plhiv_anchor_max_multiplier,
                "diagnoses_hist_mape_ceiling": historical_shape_settings.diagnoses_hist_mape_ceiling,
                "diagnoses_spike_ratio_ceiling": historical_shape_settings.diagnoses_spike_ratio_ceiling,
                "msm_diag_hist_mape_ceiling": historical_shape_settings.msm_diag_hist_mape_ceiling,
                "msm_diag_early_ratio_ceiling": historical_shape_settings.msm_diag_early_ratio_ceiling,
                "aids_cases_ratio_ceiling": historical_shape_settings.aids_cases_ratio_ceiling,
                "aids_cases_hist_mape_ceiling": historical_shape_settings.aids_cases_hist_mape_ceiling,
            }
            if historical_shape_settings is not None
            else {}
        ),
        "perturbation_bandwidth": float(getattr(args, "perturbation_bandwidth", 0.10)),
        "component_weights": component_weights,
        "fixed_parameters": calib_space.get("fixed_parameters", []),
        "non_msm_diagnosis_gate": _non_msm_gate_enabled,
        "non_msm_diagnosis_weight": non_msm_diagnosis_weight if _non_msm_gate_enabled else None,
        "non_msm_diagnosis_settings": (
            {
                "latest_floor":          non_msm_diagnosis_settings.latest_floor,
                "latest_ceiling":        non_msm_diagnosis_settings.latest_ceiling,
                "recent_mape_ceiling":   non_msm_diagnosis_settings.recent_mape_ceiling,
                "hist_mape_ceiling":     non_msm_diagnosis_settings.hist_mape_ceiling,
            }
            if non_msm_diagnosis_settings is not None
            else {}
        ),
        "simulation_start_year": sim_start_override,
        "scoring_start_year": score_start_override,
    }

    provenance: dict = {
        "argv": list(sys.argv),
        "cwd": str(Path.cwd()),
        "python_version": sys.version,
        "platform": platform.platform(),
        "started_at": started_at.isoformat(timespec="seconds"),
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "run_stamp": run_stamp,
        "output_dir": str(output_dir),
        **_git_provenance(),
    }

    metadata: dict = {
        **metadata_stub,
        **provenance,
        "source_sha256_after": source_sha_after,
        "source_config_unchanged": source_sha_before == source_sha_after,
        "calib_space_path": str(calib_space_path),
        "target_source_path": target_source_path,
        "normalized_targets_path": str(normalized_targets_path),
        "parameter_names": [p["name"] for p in parameters],
        "fixed_parameter_names": [p["name"] for p in calib_space.get("fixed_parameters", [])],
        "excluded_parameter_names": [
            p["name"] for p in calib_space.get("excluded_parameters", [])
        ],
        "calibration_window": [
            score_start_override if score_start_override is not None else CALIB_START_YEAR,
            CALIB_END_YEAR,
        ],
        "simulation_start_year": sim_start_override,
        "scoring_start_year": score_start_override,
        "n_particles_total": len(all_rows),
        "anchor_config_paths_requested": anchor_config_paths_requested,
        "anchor_config_paths_used": [str(p) for p, _ in valid_anchors],
        "anchor_config_paths_invalid": invalid_anchor_paths,
        "n_anchors_used": len(valid_anchors),
    }

    if isinstance(targets, list):
        target_df = pd.DataFrame(targets)
        metadata["target_summary"] = (
            target_df.groupby(["target_family", "model_output", "calibration_use"])
            .size()
            .reset_index(name="n_targets")
            .to_dict("records")
        )

    best_fit_paths = write_abc_best_fit_configs(
        final_ranked, base_config, calib_space, output_dir, args.top_n, metadata_stub
    )
    metadata["best_fit_config_paths"] = best_fit_paths

    metadata_path = output_dir / f"abc_smc_metadata_{run_stamp}.json"
    write_json(metadata_path, metadata)

    excel_path = output_dir / f"abc_smc_summary_{run_stamp}.xlsx"
    _write_abc_excel_summary(
        excel_path, all_df, gen_summary_df, calib_space, metadata, args.top_n
    )

    print(f"Particles CSV:      {particles_csv}")
    print(f"Generation summary: {gen_summary_csv}")
    print(f"Excel summary:      {excel_path}")
    print(f"Metadata:           {metadata_path}")
    if best_fit_paths:
        print(f"Best-fit config:    {best_fit_paths[0]}")
    print(f"Source config unchanged: {metadata['source_config_unchanged']}")

    return {
        "particles_csv": str(particles_csv),
        "generation_summary_csv": str(gen_summary_csv),
        "excel_summary": str(excel_path),
        "metadata": str(metadata_path),
        "normalized_targets": str(normalized_targets_path),
        "best_fit_configs": best_fit_paths,
        "ranked": final_ranked,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ABC-SMC epidemic calibration.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", required=True, help="Baseline model config JSON.")
    parser.add_argument("--calib-space", required=True, help="Calibration space JSON.")
    parser.add_argument(
        "--stage",
        choices=["backbone", "diagnosis", "art", "joint", "all"],
        default="joint",
        help="Calibration stage controlling active parameters and target families.",
    )
    parser.add_argument(
        "--particles", type=int, default=50, help="Particles per generation."
    )
    parser.add_argument("--generations", type=int, default=3, help="ABC-SMC generations.")
    parser.add_argument(
        "--workers", type=int, default=1, help="Worker processes (-1 = all CPUs)."
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--output-dir", required=True, help="Directory to write outputs.")
    parser.add_argument("--top-n", type=int, default=10, help="Top N best-fit configs to write.")
    parser.add_argument(
        "--targets-workbook",
        default=str(default_disease_workbook_path(ROOT)),
        help="Disease calibration target workbook.",
    )
    parser.add_argument(
        "--target-mapping",
        default=str(default_target_mapping_path(ROOT)),
        help="Disease target mapping JSON.",
    )
    parser.add_argument(
        "--family-balanced-score",
        action="store_true",
        help="Score by mean family likelihood rather than raw point sum.",
    )
    parser.add_argument(
        "--acceptance-gate-audit",
        action="store_true",
        help="Include hard acceptance-gate columns in each particle row.",
    )
    parser.add_argument(
        "--soft-aids-death-gate",
        action="store_true",
        help=(
            "Treat AIDS-death gate failures as warnings for overall_accept. "
            "AIDS deaths remain scored in abc_distance via --weight-aids-deaths."
        ),
    )
    parser.add_argument(
        "--perturbation-bandwidth",
        type=float,
        default=0.10,
        help="Gaussian kernel bandwidth as fraction of each parameter's prior range.",
    )
    parser.add_argument(
        "--weight-diagnosis", type=float, default=1.0,
        help="ABC distance component weight for diagnosis.",
    )
    parser.add_argument(
        "--weight-plhiv", type=float, default=1.0,
        help="ABC distance component weight for PLHIV.",
    )
    parser.add_argument(
        "--weight-plhiv-sex", type=float, default=0.5,
        help="ABC distance component weight for adult female/male PLHIV targets.",
    )
    parser.add_argument(
        "--weight-incidence", type=float, default=1.0,
        help="ABC distance component weight for incidence.",
    )
    parser.add_argument(
        "--weight-aids-deaths", type=float, default=1.0,
        help="ABC distance component weight for AIDS deaths.",
    )
    parser.add_argument(
        "--weight-art-stock", type=float, default=1.0,
        help="ABC distance component weight for ART stock.",
    )
    parser.add_argument(
        "--anchor-config",
        action="append",
        default=[],
        metavar="PATH",
        help="Anchor config JSON to inject into generation 0 (repeatable).",
    )
    parser.add_argument(
        "--anchor-glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Shell glob pattern for anchor config JSONs (repeatable).",
    )
    parser.add_argument(
        "--anchor-max",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of anchor configs to use (applied after deduplication).",
    )
    # V13.9 historical shape gates (opt-in; does not change V13.8.2 behaviour when absent)
    parser.add_argument(
        "--historical-shape-gates",
        action="store_true",
        default=False,
        help=(
            "Enable V13.9 historical trajectory shape gates. "
            "Forces --acceptance-gate-audit on. "
            "overall_accept becomes AND of V13.8.2 gates AND historical shape gates."
        ),
    )
    parser.add_argument(
        "--weight-historical-plhiv",
        type=float,
        default=1.0,
        help="ABC distance weight for historical PLHIV shape component (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--weight-historical-diagnoses",
        type=float,
        default=1.0,
        help="ABC distance weight for historical total diagnoses trajectory (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--weight-historical-msm-diagnoses",
        type=float,
        default=1.0,
        help="ABC distance weight for historical MSM diagnoses trajectory (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--weight-historical-aids-cases",
        type=float,
        default=1.0,
        help="ABC distance weight for historical AIDS cases trajectory (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--plhiv-hist-mape-ceiling",
        type=float,
        default=40.0,
        help="V13.9 PLHIV 1990-2005 MAPE ceiling in percent (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--plhiv-anchor-max-multiplier",
        type=float,
        default=2.0,
        help="V13.9 1990 PLHIV model/target ratio ceiling (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--diagnoses-hist-mape-ceiling",
        type=float,
        default=40.0,
        help="V13.9 total diagnoses 1990-2014 MAPE ceiling in percent (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--diagnoses-spike-ratio-ceiling",
        type=float,
        default=2.5,
        help="V13.9 1997 total diagnosis model/target ratio ceiling (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--msm-diag-hist-mape-ceiling",
        type=float,
        default=40.0,
        help="V13.9 MSM diagnoses 1996-2014 MAPE ceiling in percent (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--msm-diag-early-ratio-ceiling",
        type=float,
        default=2.5,
        help="V13.9 MSM diagnoses 1996-2003 model/target ratio ceiling (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--aids-cases-ratio-ceiling",
        type=float,
        default=4.0,
        help="V13.9 AIDS cases 1990-2017 any-year model/target ratio ceiling (requires --historical-shape-gates).",
    )
    parser.add_argument(
        "--aids-cases-hist-mape-ceiling",
        type=float,
        default=80.0,
        help="V13.9 AIDS cases 1990-2017 MAPE ceiling in percent (requires --historical-shape-gates).",
    )
    # V13.9.5 non-MSM diagnosis gate (opt-in; default off to preserve V13.8.2 behaviour)
    parser.add_argument(
        "--non-msm-diagnosis-gate",
        action="store_true",
        default=False,
        help=(
            "Enable V13.9.5 non-MSM diagnosis composition diagnostic gate. "
            "Evaluates model vs target non-MSM diagnoses (= total - MSM). "
            "overall_accept requires this gate only when this flag is set. "
            "Existing V13.8.2 and V13.9 gates are never altered."
        ),
    )
    parser.add_argument(
        "--weight-non-msm-diagnosis",
        type=float,
        default=1.0,
        help=(
            "ABC distance weight for non-MSM diagnosis distance component "
            "(requires --non-msm-diagnosis-gate)."
        ),
    )
    parser.add_argument(
        "--non-msm-latest-floor",
        type=float,
        default=0.50,
        help="V13.9.5 non-MSM latest-year model/target ratio floor (requires --non-msm-diagnosis-gate).",
    )
    parser.add_argument(
        "--non-msm-latest-ceiling",
        type=float,
        default=1.50,
        help="V13.9.5 non-MSM latest-year model/target ratio ceiling (requires --non-msm-diagnosis-gate).",
    )
    parser.add_argument(
        "--non-msm-recent-mape-ceiling",
        type=float,
        default=35.0,
        help="V13.9.5 non-MSM 2015-2024 MAPE ceiling in percent (requires --non-msm-diagnosis-gate).",
    )
    parser.add_argument(
        "--non-msm-hist-mape-ceiling",
        type=float,
        default=60.0,
        help="V13.9.5 non-MSM 1996-2014 MAPE ceiling in percent (requires --non-msm-diagnosis-gate).",
    )
    # V13.14 burn-in year overrides (diagnostic only)
    parser.add_argument(
        "--simulation-start-year",
        type=int,
        default=None,
        help="Override simulation start year (e.g. 1975 for historical burn-in). Default: use CALIB_START_YEAR.",
    )
    parser.add_argument(
        "--scoring-start-year",
        type=int,
        default=None,
        help="Override scoring/extraction start year (e.g. 1990). Default: use CALIB_START_YEAR.",
    )
    args = parser.parse_args(argv)
    if args.particles <= 0:
        parser.error("--particles must be positive")
    if args.generations <= 0:
        parser.error("--generations must be positive")
    if args.top_n <= 0:
        parser.error("--top-n must be positive")
    if not 0.0 < args.perturbation_bandwidth <= 1.0:
        parser.error("--perturbation-bandwidth must be in (0, 1]")
    for flag, val in [
        ("--weight-diagnosis",   args.weight_diagnosis),
        ("--weight-plhiv",       args.weight_plhiv),
        ("--weight-plhiv-sex",   args.weight_plhiv_sex),
        ("--weight-incidence",   args.weight_incidence),
        ("--weight-aids-deaths", args.weight_aids_deaths),
        ("--weight-art-stock",   args.weight_art_stock),
    ]:
        if val < 0.0:
            parser.error(f"{flag} must be non-negative")
    if args.anchor_max is not None and args.anchor_max <= 0:
        parser.error("--anchor-max must be positive")
    for flag, val in [
        ("--weight-historical-plhiv",        args.weight_historical_plhiv),
        ("--weight-historical-diagnoses",     args.weight_historical_diagnoses),
        ("--weight-historical-msm-diagnoses", args.weight_historical_msm_diagnoses),
        ("--weight-historical-aids-cases",    args.weight_historical_aids_cases),
        ("--weight-non-msm-diagnosis",        args.weight_non_msm_diagnosis),
    ]:
        if val < 0.0:
            parser.error(f"{flag} must be non-negative")
    for flag, val in [
        ("--plhiv-hist-mape-ceiling",        args.plhiv_hist_mape_ceiling),
        ("--plhiv-anchor-max-multiplier",    args.plhiv_anchor_max_multiplier),
        ("--diagnoses-hist-mape-ceiling",    args.diagnoses_hist_mape_ceiling),
        ("--diagnoses-spike-ratio-ceiling",  args.diagnoses_spike_ratio_ceiling),
        ("--msm-diag-hist-mape-ceiling",     args.msm_diag_hist_mape_ceiling),
        ("--msm-diag-early-ratio-ceiling",   args.msm_diag_early_ratio_ceiling),
        ("--aids-cases-ratio-ceiling",       args.aids_cases_ratio_ceiling),
        ("--aids-cases-hist-mape-ceiling",   args.aids_cases_hist_mape_ceiling),
        ("--non-msm-latest-floor",           args.non_msm_latest_floor),
        ("--non-msm-latest-ceiling",         args.non_msm_latest_ceiling),
        ("--non-msm-recent-mape-ceiling",    args.non_msm_recent_mape_ceiling),
        ("--non-msm-hist-mape-ceiling",      args.non_msm_hist_mape_ceiling),
    ]:
        if val <= 0.0:
            parser.error(f"{flag} must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    run_abc_smc_calibration(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
