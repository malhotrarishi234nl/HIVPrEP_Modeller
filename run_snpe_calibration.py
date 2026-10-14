"""
SNPE (Sequential Neural Posterior Estimation) epidemic calibration runner.

Third calibration path alongside LHS (run_lhs_calibration.py) and
ABC-SMC (run_abc_smc_calibration.py).  Uses the sbi library to train a
normalizing flow that approximates P(θ | x_obs) from simulated (θ, x_sim)
pairs.  Hard acceptance gates are applied only to final posterior samples —
not during training — so the runner produces a posterior even when the
classical acceptance rate is 0 %.

Design principles
-----------------
- Zero changes to existing runners, config files, or calibration modules.
- Reads the same calib_space.json as LHS and ABC-SMC (no new config format).
- Parallel simulation uses the same ProcessPoolExecutor + payload-dict pattern.
- Outputs mirror the ABC-SMC structure (particles CSV, round summary, Excel,
  best-fit JSON configs) so existing audit tooling works unchanged.
- MAF (Masked Autoregressive Flow) is the default density estimator; NSF is
  available via --density-estimator nsf.

Output files
------------
  snpe_particles_<timestamp>.csv         All evaluated particles, all rounds
  snpe_round_summary_<timestamp>.csv     Per-round training/simulation stats
  snpe_posterior_samples_<timestamp>.csv Samples drawn from the final posterior
  snpe_metadata_<timestamp>.json         Run config + calib_space + git hash
  snpe_summary_<timestamp>.xlsx          Excel workbook (6 sheets)
  snpe_best_fit_rank_NNN.json            Top accepted model configs

References
----------
Greenberg et al. (2019) Automatic Posterior Transformation. ICML 2019.
  https://proceedings.mlr.press/v97/greenberg19a.html
Tejero-Cantero et al. (2020) sbi: A toolkit for SBI. JOSS.
  https://doi.org/10.21105/joss.02505
"""

from __future__ import annotations

import argparse
import copy
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

# ---------------------------------------------------------------------------
# sbi import — deferred to give a helpful message if not installed
# ---------------------------------------------------------------------------
try:
    import torch
    from sbi.inference import SNPE
    from sbi.utils import BoxUniform
except ImportError as _sbi_err:
    raise ImportError(
        "sbi is required for the SNPE calibration runner.\n"
        "Install with:  pip install sbi==0.26.1\n"
        f"Original error: {_sbi_err}"
    ) from _sbi_err

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_distance import (
    CalibrationDistanceSettings,
    calibration_distance_row_fields,
    evaluate_calibration_distance,
)
from calib_extractor import extract_annual_outputs
from calib_scorer import score_draw
from calib_snpe_summary_stats import (
    DEFAULT_SUMMARY_STAT_SPEC,
    N_SUMMARY_STATS,
    compute_summary_stats,
    observed_summary_stats,
    stat_row_fields,
    summary_stat_labels,
)
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
    write_normalized_targets_csv,
)
from run_lhs_calibration import (
    _run_simulation_quietly,
    filter_calib_space_for_stage,
    inject_params,
    load_json,
    validate_config_paths,
    write_json,
)
from run_v3 import SimulationEngineV3  # noqa: F401  (ensures ROOT is importable)

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025


# ---------------------------------------------------------------------------
# Particle worker  (module-level so ProcessPoolExecutor can pickle it)
# ---------------------------------------------------------------------------

def _run_snpe_particle_worker(payload: dict) -> dict:
    """Evaluate one SNPE particle and return a flat result row.

    Nearly identical to the ABC-SMC worker.  In addition to scoring and gate
    evaluation it calls compute_summary_stats to build the x_sim vector that
    SNPE training requires.
    """
    particle_id = payload["particle_id"]
    snpe_round = payload["snpe_round"]
    base_config = payload["base_config"]
    calib_space = payload["calib_space"]
    params = payload["params"]
    targets = payload["targets"]
    seed = payload["seed"]
    acceptance_gate_settings = payload.get("acceptance_gate_settings")
    distance_settings = payload.get("distance_settings")
    simulation_start_year = payload.get("simulation_start_year")
    scoring_start_year = payload.get("scoring_start_year")
    summary_stat_spec = payload.get("summary_stat_spec")  # None → default spec

    try:
        config = inject_params(
            base_config,
            params,
            calib_space,
            deterministic_mode=True,
            simulation_start_year=simulation_start_year,
        )
        result = _run_simulation_quietly(config, seed=seed)
        extract_start = (
            scoring_start_year if scoring_start_year is not None else CALIB_START_YEAR
        )
        outputs = extract_annual_outputs(result, extract_start, CALIB_END_YEAR)

        # Summary statistics vector (x_sim for SNPE training)
        x_sim = compute_summary_stats(outputs, spec=summary_stat_spec)

        # Acceptance gates (applied to final posterior samples, stored here
        # for diagnostics on all evaluated particles)
        acceptance = evaluate_acceptance_gates(
            outputs,
            targets,
            settings=acceptance_gate_settings,
        )

        # Continuous distance (for sorting / diagnostics)
        distance_result = evaluate_calibration_distance(
            outputs,
            targets,
            settings=distance_settings,
            acceptance=acceptance,
        )

        # Log-likelihood score (for Excel summary / comparisons with LHS)
        score = score_draw(outputs, targets)

        row: dict[str, Any] = {
            "particle_id": particle_id,
            "snpe_round": snpe_round,
            **{k: float(v) for k, v in params.items()},
            "total_ll": score["total_ll"],
            "collapsed": score["collapsed"],
            "error": "",
        }
        row.update(stat_row_fields(x_sim, spec=summary_stat_spec))
        row.update(calibration_distance_row_fields(distance_result))
        row.update(acceptance_gate_row_fields(acceptance))
        return row

    except Exception as exc:
        n_stats = (
            len(summary_stat_labels(summary_stat_spec))
            if summary_stat_spec is not None
            else N_SUMMARY_STATS
        )
        sentinel = math.log1p(1.0)
        row = {
            "particle_id": particle_id,
            "snpe_round": snpe_round,
            **{k: float(v) for k, v in params.items()},
            "total_ll": float("-inf"),
            "collapsed": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
        # Fill stat columns with sentinel so the row has the correct schema
        for label in summary_stat_labels(summary_stat_spec):
            row[f"x_{label}"] = sentinel
        row["abc_distance"] = float("inf")
        row["overall_accept"] = False
        return row


def _run_round_payloads(payloads: list[dict], workers: int) -> list[dict]:
    """Dispatch particle payloads in parallel and collect result rows,
    restored to the original payloads order before returning.

    ProcessPoolExecutor's as_completed() yields results in whatever order
    workers happen to finish, which varies run to run. Since row order
    feeds directly into theta/x tensors and therefore minibatch
    composition during training, an unstable order makes training
    non-reproducible even with a fixed torch seed. Restoring submission
    order (rather than sorting by particle_id) also avoids relying on
    particle_id being numeric -- the final posterior-evaluation call site
    uses string ids like "post_10", which would otherwise sort
    lexicographically.
    """
    if workers == 1:
        return [_run_snpe_particle_worker(p) for p in payloads]
    indexed_rows: list[tuple[int, dict]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_snpe_particle_worker, p): i
            for i, p in enumerate(payloads)
        }
        for future in as_completed(futures):
            indexed_rows.append((futures[future], future.result()))
    return [row for _, row in sorted(indexed_rows, key=lambda pair: pair[0])]


# ---------------------------------------------------------------------------
# Prior and posterior helpers
# ---------------------------------------------------------------------------

def _build_prior(calib_space: dict) -> BoxUniform:
    """Build an sbi BoxUniform prior from calib_space parameter bounds."""
    parameters = calib_space.get("parameters", [])
    lower = [float(p["min"]) for p in parameters]
    upper = [float(p["max"]) for p in parameters]
    return BoxUniform(
        low=torch.tensor(lower, dtype=torch.float32),
        high=torch.tensor(upper, dtype=torch.float32),
    )


def _param_names(calib_space: dict) -> list[str]:
    """Independent parameter names in the same order as the prior dimensions."""
    return [p["name"] for p in calib_space.get("parameters", [])]


def _theta_from_rows(rows: list[dict], param_names: list[str]) -> torch.Tensor:
    """Extract parameter columns from result rows → (N, d) float32 tensor."""
    theta = np.array(
        [[float(row[name]) for name in param_names] for row in rows],
        dtype=np.float32,
    )
    return torch.tensor(theta, dtype=torch.float32)


def _x_from_rows(
    rows: list[dict],
    stat_labels: list[str],
) -> torch.Tensor:
    """Extract summary-stat columns from result rows → (N, S) float32 tensor."""
    x = np.array(
        [[float(row.get(f"x_{label}", math.log1p(1.0))) for label in stat_labels] for row in rows],
        dtype=np.float32,
    )
    return torch.tensor(x, dtype=torch.float32)


def _clip_to_bounds(
    sample: dict[str, float],
    calib_space: dict,
) -> dict[str, float]:
    """Clip a parameter dict to the calib_space bounds (flow can exceed them)."""
    clipped = dict(sample)
    for param in calib_space.get("parameters", []):
        name = param["name"]
        if name in clipped:
            clipped[name] = float(
                np.clip(clipped[name], float(param["min"]), float(param["max"]))
            )
    return clipped


def _tensor_to_param_dicts(
    samples: torch.Tensor,
    param_names: list[str],
    calib_space: dict,
) -> list[dict[str, float]]:
    """Convert an in-prior posterior sample tensor to parameter dicts."""
    arr = samples.detach().cpu().numpy()
    result = []
    for row in arr:
        d = {name: float(val) for name, val in zip(param_names, row)}
        _validate_param_bounds(d, calib_space)
        result.append(d)
    return result


def _param_dicts_from_array(
    arr: np.ndarray,
    param_names: list[str],
) -> list[dict[str, float]]:
    return [
        {name: float(val) for name, val in zip(param_names, row)}
        for row in arr
    ]


def _bounds_arrays(calib_space: dict) -> tuple[np.ndarray, np.ndarray]:
    params = calib_space.get("parameters", [])
    lower = np.array([float(p["min"]) for p in params], dtype=np.float32)
    upper = np.array([float(p["max"]) for p in params], dtype=np.float32)
    return lower, upper


def _in_bounds_rows(arr: np.ndarray, calib_space: dict) -> np.ndarray:
    """Boolean mask for finite samples inside calibration-space bounds."""
    lower, upper = _bounds_arrays(calib_space)
    return (
        np.isfinite(arr).all(axis=1)
        & (arr >= lower).all(axis=1)
        & (arr <= upper).all(axis=1)
    )


def _validate_param_bounds(
    sample: dict[str, float],
    calib_space: dict,
    *,
    atol: float = 1e-6,
) -> None:
    """Raise if a posterior sample falls outside the calibration space."""
    for param in calib_space.get("parameters", []):
        name = param["name"]
        if name not in sample:
            continue
        value = float(sample[name])
        lower = float(param["min"])
        upper = float(param["max"])
        if not math.isfinite(value) or value < lower - atol or value > upper + atol:
            raise ValueError(
                f"Posterior sample for {name}={value} is outside bounds "
                f"[{lower}, {upper}]. Use reject_outside_prior=True instead of clipping."
            )


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _train_snpe(
    inference: SNPE,
    theta: torch.Tensor,
    x: torch.Tensor,
    proposal,
    round_num: int,
    *,
    force_first_round_loss: bool = False,
    show_train_summary: bool = True,
    skip_append: bool = False,
) -> Any:
    """Append simulations and train SNPE; return the density estimator.

    Parameters
    ----------
    inference:
        The SNPE inference object (accumulates data across rounds).
    theta, x:
        Simulations from the *current* round only.
    proposal:
        The distribution used to sample theta for this round.
        Pass ``None`` (prior) for round 0.
    round_num:
        Current round index (0-based).  Round 0 always uses the prior.
    force_first_round_loss:
        Forwarded to ``inference.train()``. Defaults to False, the
        statistically correct setting for round_num > 0 with a non-prior
        proposal: sbi's atomic/proposal-correction (APT) loss is what makes
        multi-round SNPE-C valid when training data was NOT drawn from the
        prior (see Greenberg et al. 2019). Passing True disables that
        correction and trains as if every round's data came from the prior —
        an explicit, auditable degraded mode, not a silent default.
    show_train_summary:
        Forwarded to ``inference.train()`` so per-epoch train/val loss is
        logged, rather than swallowed silently.
    skip_append:
        Skip the append_simulations call. Set True only when re-training
        (e.g. a force_first_round_loss retry) after this round's data was
        already appended by an earlier, failed call -- prevents double
        counting the same round's simulations in sbi's accumulated dataset.
    """
    if not skip_append:
        if round_num == 0 or proposal is None:
            inference.append_simulations(theta, x)
        else:
            inference.append_simulations(theta, x, proposal=proposal)
    density_estimator = inference.train(
        force_first_round_loss=force_first_round_loss,
        show_train_summary=show_train_summary,
    )
    return density_estimator


def _train_round_with_fallback(
    inference: SNPE,
    theta: torch.Tensor,
    x: torch.Tensor,
    proposal,
    round_num: int,
    force_first_round_loss: bool,
) -> tuple[Any, bool]:
    """Train round >= 1 with proposal correction; retry once, uncorrected, on failure.

    Returns (density_estimator_or_None, used_fallback_loss). The retry passes
    skip_append=True because the primary attempt's append_simulations() call
    already succeeded before its train() call threw -- re-appending would
    silently double-count this round's simulations in sbi's accumulated
    dataset (see _train_snpe's skip_append docstring).
    """
    try:
        density_estimator = _train_snpe(
            inference, theta, x, proposal=proposal,
            round_num=round_num, force_first_round_loss=force_first_round_loss,
        )
        return density_estimator, False
    except Exception as exc:
        print(
            f"  WARNING: proposal-corrected training failed in round "
            f"{round_num} ({type(exc).__name__}: {exc}); retrying once "
            f"with force_first_round_loss=True as a logged fallback."
        )
        try:
            density_estimator = _train_snpe(
                inference, theta, x, proposal=proposal,
                round_num=round_num, force_first_round_loss=True,
                skip_append=True,
            )
            return density_estimator, True
        except Exception as exc2:
            print(
                f"  WARNING: fallback training also failed in round "
                f"{round_num} ({type(exc2).__name__}: {exc2}); "
                f"skipping training update for this round."
            )
            return None, False


def _extract_training_losses(inference: SNPE) -> tuple[float, float]:
    """Best-effort extraction of the most recent round's final train/val loss
    from sbi's internal training summary, for diagnostics only.

    sbi's exact summary attribute/key names have shifted across versions;
    this never raises -- it returns (nan, nan) if the expected shape isn't
    found, so a diagnostic-capture bug can never break a live run.
    """
    try:
        summary = getattr(inference, "summary", {}) or {}
        train_losses = summary.get("training_loss") or summary.get("train_log_probs") or []
        val_losses = summary.get("validation_loss") or summary.get("validation_log_probs") or []
        final_train = float(train_losses[-1]) if len(train_losses) else float("nan")
        final_val = float(val_losses[-1]) if len(val_losses) else float("nan")
        return final_train, final_val
    except Exception:
        return float("nan"), float("nan")


def _build_posterior(inference: SNPE, density_estimator: Any) -> Any:
    """Wrap the density estimator in a sbi posterior object."""
    return inference.build_posterior(density_estimator)


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def _sample_from_prior(
    calib_space: dict,
    n: int,
    rng: np.random.Generator,
) -> list[dict[str, float]]:
    """Uniform prior samples (round-0 bootstrap)."""
    params_spec = calib_space.get("parameters", [])
    samples: list[dict[str, float]] = []
    for _ in range(n):
        draw = {
            p["name"]: float(rng.uniform(float(p["min"]), float(p["max"])))
            for p in params_spec
        }
        samples.append(draw)
    return samples


def _sample_from_posterior(
    posterior: Any,
    x_obs: torch.Tensor,
    n: int,
    param_names: list[str],
    calib_space: dict,
    max_draw_multiplier: int = 25,
) -> list[dict[str, float]]:
    """Draw in-bounds posterior samples without unbounded rejection loops.

    Some trained flows put most density outside the BoxUniform support.  sbi's
    built-in ``reject_outside_prior=True`` can then spend a very long time
    trying to fill the requested sample count.  Instead, draw raw posterior
    batches, keep only finite in-prior rows, and stop after an explicit budget.
    This preserves the no-clipping guarantee while making bad posteriors
    diagnosable.
    """
    if n <= 0:
        return []

    accepted: list[dict[str, float]] = []
    raw_draws = 0
    max_raw_draws = max(n, n * max_draw_multiplier)
    batch_size = max(n, 256)

    with torch.no_grad():
        while len(accepted) < n and raw_draws < max_raw_draws:
            draw_n = min(batch_size, max_raw_draws - raw_draws)
            samples_tensor = posterior.sample(
                (draw_n,),
                x=x_obs,
                show_progress_bars=False,
                reject_outside_prior=False,
            )
            raw_draws += draw_n
            arr = samples_tensor.detach().cpu().numpy()
            in_bounds = _in_bounds_rows(arr, calib_space)
            if np.any(in_bounds):
                accepted.extend(
                    _param_dicts_from_array(arr[in_bounds], param_names)
                )

    if len(accepted) < n:
        rate = len(accepted) / raw_draws if raw_draws else 0.0
        print(
            "  WARNING: posterior in-prior sample yield was low "
            f"({len(accepted)}/{raw_draws}, {rate:.3%}); continuing with "
            f"{len(accepted)} sample(s) instead of {n}."
        )
    return accepted[:n]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _git_provenance() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        return {"git_commit": commit, "git_dirty": bool(dirty)}
    except Exception:
        return {"git_commit": "unknown", "git_dirty": None}


def _round_summary(
    rows: list[dict],
    snpe_round: int,
    training_duration_s: float,
    simulation_duration_s: float,
    final_train_loss: float = float("nan"),
    final_val_loss: float = float("nan"),
    used_fallback_loss: bool = False,
) -> dict:
    round_rows = [r for r in rows if r.get("snpe_round") == snpe_round]
    n = len(round_rows)
    n_non_collapsed = sum(1 for r in round_rows if not r.get("collapsed", True))
    n_accept = sum(1 for r in round_rows if r.get("overall_accept", False))
    distances = [
        r.get("abc_distance", math.inf)
        for r in round_rows
        if math.isfinite(r.get("abc_distance", math.inf))
    ]
    return {
        "snpe_round": snpe_round,
        "n_particles": n,
        "n_non_collapsed": n_non_collapsed,
        "n_accept": n_accept,
        "accept_rate": n_accept / n if n > 0 else 0.0,
        "abc_distance_min": min(distances) if distances else math.inf,
        "abc_distance_median": float(np.median(distances)) if distances else math.inf,
        "abc_distance_p25": float(np.percentile(distances, 25)) if distances else math.inf,
        "training_duration_s": training_duration_s,
        "simulation_duration_s": simulation_duration_s,
        "final_train_loss": final_train_loss,
        "final_val_loss": final_val_loss,
        "used_fallback_loss": used_fallback_loss,
    }


def write_snpe_best_fit_configs(
    all_rows: list[dict],
    posterior_sample_rows: list[dict],
    base_config: dict,
    calib_space: dict,
    output_dir: Path,
    top_n: int,
    timestamp: str,
    metadata_stub: dict,
) -> list[str]:
    """Write accepted posterior configs, or explicit best-distance fallbacks."""
    param_names = _param_names(calib_space)
    paths: list[str] = []

    # Accepted posterior samples first; fall back to best-distance from all_rows
    posterior_accepted = [
        r for r in posterior_sample_rows if r.get("overall_accept", False)
    ]
    n_posterior_accepted = len(posterior_accepted)
    if posterior_accepted:
        selected = sorted(
            posterior_accepted,
            key=lambda r: r.get("abc_distance", math.inf),
        )
        selection_source = "accepted_posterior"
        selection_reason = "posterior sample passed all hard acceptance gates"
    else:
        selected = sorted(
            [r for r in all_rows if math.isfinite(r.get("abc_distance", math.inf))],
            key=lambda r: r.get("abc_distance", math.inf),
        )
        selection_source = "fallback_all_particles"
        selection_reason = (
            "no accepted posterior samples; selected lowest-distance evaluated particles"
        )

    for rank, row in enumerate(selected[:top_n], start=1):
        params = {name: row[name] for name in param_names if name in row}
        config = inject_params(
            copy.deepcopy(base_config),
            params,
            calib_space,
            deterministic_mode=False,
        )
        meta = _snpe_selection_metadata(
            row=row,
            rank=rank,
            timestamp=timestamp,
            metadata_stub=metadata_stub,
            selection_source=selection_source,
            selection_reason=selection_reason,
            n_posterior_accepted=n_posterior_accepted,
        )
        calibration_metadata = config.setdefault("calibration_metadata", {})
        inherited_abc = calibration_metadata.pop("abc_smc_calibration", None)
        if inherited_abc is not None:
            calibration_metadata.setdefault(
                "source_base_abc_smc_calibration",
                inherited_abc,
            )
        calibration_metadata["snpe_calibration"] = meta
        config["_snpe_metadata"] = meta
        fname = output_dir / f"snpe_best_fit_rank_{rank:03d}.json"
        write_json(fname, config)
        paths.append(str(fname))

    return paths


def _snpe_selection_metadata(
    *,
    row: dict,
    rank: int,
    timestamp: str,
    metadata_stub: dict,
    selection_source: str,
    selection_reason: str,
    n_posterior_accepted: int,
) -> dict:
    """Build current SNPE metadata for an exported candidate config."""
    meta = {
        "snpe_runner": "run_snpe_calibration.py",
        "rank": rank,
        "particle_id": row.get("particle_id", "posterior_sample"),
        "snpe_round": row.get("snpe_round", "posterior"),
        "selection_source": selection_source,
        "selection_reason": selection_reason,
        "n_posterior_accepted": n_posterior_accepted,
        "overall_accept": bool(row.get("overall_accept", False)),
        "abc_distance": row.get("abc_distance", None),
        "total_ll": row.get("total_ll", None),
        "timestamp": timestamp,
        **{
            k: v
            for k, v in metadata_stub.items()
            if k in ("git_commit", "calib_space_path")
        },
    }

    for key, value in row.items():
        if (
            key.startswith("acceptance_")
            or key.startswith("abc_distance_")
            or key.endswith("_gate_pass")
            or key.endswith("_gate_fail_reasons")
            or key.endswith("_gate_warning_reasons")
            or key in ("acceptance_version", "abc_overall_accept")
        ):
            meta[key] = value
    return meta


def _write_snpe_excel_summary(
    all_rows: list[dict],
    round_summaries: list[dict],
    posterior_sample_rows: list[dict],
    calib_space: dict,
    metadata: dict,
    output_dir: Path,
    timestamp: str,
) -> Path:
    """Write a multi-sheet Excel summary workbook."""
    param_names = _param_names(calib_space)
    stat_labels = summary_stat_labels()
    path = output_dir / f"snpe_summary_{timestamp}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # Sheet 1: Run metadata
        meta_rows = [{"key": k, "value": str(v)} for k, v in metadata.items()]
        pd.DataFrame(meta_rows).to_excel(writer, sheet_name="Metadata", index=False)

        # Sheet 2: Round-by-round progress
        pd.DataFrame(round_summaries).to_excel(writer, sheet_name="Round_Progress", index=False)

        # Sheet 3: Accepted posterior samples (gate-passed)
        accepted = [r for r in posterior_sample_rows if r.get("overall_accept", False)]
        pd.DataFrame(accepted).to_excel(writer, sheet_name="Accepted_Ensemble", index=False)

        # Sheet 4: All posterior samples (top 500 by distance)
        all_post_sorted = sorted(
            posterior_sample_rows,
            key=lambda r: r.get("abc_distance", math.inf),
        )
        pd.DataFrame(all_post_sorted[:500]).to_excel(
            writer, sheet_name="Posterior_Samples", index=False
        )

        # Sheet 5: Parameter space definition
        pd.DataFrame(calib_space.get("parameters", [])).to_excel(
            writer, sheet_name="Parameter_Space", index=False
        )

        # Sheet 6: Summary stat definition
        stat_spec_rows = [
            {"column": f"x_{label}", "indicator": label.rsplit("_", 1)[0], "year": label.rsplit("_", 1)[1]}
            for label in stat_labels
        ]
        pd.DataFrame(stat_spec_rows).to_excel(
            writer, sheet_name="Summary_Stats", index=False
        )

    return path


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_snpe_calibration(args: argparse.Namespace) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("SNPE Calibration Runner")
    print(f"  timestamp      : {timestamp}")
    print(f"  output_dir     : {output_dir}")
    print(f"  density_estimator: {args.density_estimator}")
    print(f"  bootstrap      : {args.bootstrap}")
    print(f"  rounds         : {args.rounds}")
    print(f"  round_particles: {args.round_particles}")
    print(f"  posterior_samples: {args.posterior_samples}")
    print(f"  final_samples  : {args.final_samples}")
    print(f"  workers        : {args.workers}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Load configs and targets
    # ------------------------------------------------------------------
    base_config = load_json(args.config)
    calib_space_raw = load_json(args.calib_space)
    calib_space = filter_calib_space_for_stage(calib_space_raw, "joint")
    validate_config_paths(base_config, calib_space)

    targets = load_disease_targets(
        workbook_path=Path(args.targets_workbook),
        mapping_path=Path(args.target_mapping),
    )
    write_normalized_targets_csv(output_dir / f"normalized_targets_{timestamp}.csv", targets)

    acceptance_gate_settings = AcceptanceGateSettings(
        aids_death_hard_gate=not args.soft_aids_gate,
    )
    distance_settings = CalibrationDistanceSettings(
        gate_settings=acceptance_gate_settings,
    )

    param_names = _param_names(calib_space)
    stat_labels = summary_stat_labels()
    n_params = len(param_names)
    workers = os.cpu_count() if args.workers == -1 else max(1, args.workers)

    print(f"  Parameter dimensions : {n_params}")
    print(f"  Summary stat dim     : {N_SUMMARY_STATS}")
    print(f"  Parallel workers     : {workers}\n")

    # Observed summary statistics (x_obs)
    x_obs_np = observed_summary_stats(targets)
    x_obs = torch.tensor(x_obs_np, dtype=torch.float32)

    # ------------------------------------------------------------------
    # Build sbi prior and inference object
    # ------------------------------------------------------------------
    # torch's global RNG drives flow weight init, minibatch shuffling, and
    # posterior sampling -- none of which --seed (numpy-only) controls.
    # Without this, two runs with identical --seed/config/data can still
    # train a different flow and get a different round-1+ outcome purely
    # from torch RNG noise, which makes any before/after comparison invalid.
    torch.manual_seed(args.seed)
    prior = _build_prior(calib_space)
    inference = SNPE(prior=prior, density_estimator=args.density_estimator)
    rng = np.random.default_rng(args.seed)
    particle_counter = 0
    all_rows: list[dict] = []
    round_summaries: list[dict] = []
    current_posterior = None
    current_proposal = None  # None = prior (round 0)

    # Metadata stub for best-fit configs
    git_info = _git_provenance()
    metadata_stub: dict = {
        "runner": "run_snpe_calibration.py",
        "timestamp": timestamp,
        "density_estimator": args.density_estimator,
        "bootstrap": args.bootstrap,
        "rounds": args.rounds,
        "round_particles": args.round_particles,
        "posterior_samples": args.posterior_samples,
        "final_samples": args.final_samples,
        "calib_space_path": args.calib_space,
        "config_path": args.config,
        "python": sys.version,
        "platform": platform.platform(),
        "proposal_correction": not args.force_first_round_loss,
        "force_first_round_loss": args.force_first_round_loss,
        **git_info,
    }

    # ------------------------------------------------------------------
    # Round 0: bootstrap from prior (or load existing CSV)
    # ------------------------------------------------------------------
    print("Round 0 — bootstrap (prior samples)")
    t0 = time.time()

    if args.bootstrap_csv:
        bootstrap_rows = _load_bootstrap_csv(
            Path(args.bootstrap_csv), param_names, stat_labels, calib_space
        )
        print(f"  Loaded {len(bootstrap_rows)} rows from {args.bootstrap_csv}")
    else:
        prior_draws = _sample_from_prior(calib_space, args.bootstrap, rng)
        payloads = [
            {
                "particle_id": (particle_counter := particle_counter + 1),
                "snpe_round": 0,
                "base_config": base_config,
                "calib_space": calib_space,
                "params": draw,
                "targets": targets,
                "seed": args.seed + particle_counter,
                "acceptance_gate_settings": acceptance_gate_settings,
                "distance_settings": distance_settings,
                "simulation_start_year": getattr(args, "simulation_start_year", None),
                "scoring_start_year": getattr(args, "scoring_start_year", None),
                "summary_stat_spec": None,
            }
            for draw in prior_draws
        ]
        sim_t0 = time.time()
        bootstrap_rows = _run_round_payloads(payloads, workers)
        sim_dur = time.time() - sim_t0
        print(f"  Simulated {len(bootstrap_rows)} bootstrap particles in {sim_dur:.1f}s")

    all_rows.extend(bootstrap_rows)
    round_summaries.append(
        _round_summary(bootstrap_rows, 0, training_duration_s=0.0,
                       simulation_duration_s=time.time() - t0)
    )
    _print_round_summary(round_summaries[-1])

    # Train on bootstrap data once (from prior — no proposal correction needed).
    # sbi accumulates data internally; append_simulations is called once per
    # round with ONLY that round's new data.
    bootstrap_usable = [
        r for r in bootstrap_rows
        if not r.get("collapsed", True) and math.isfinite(r.get("abc_distance", math.inf))
    ]
    if len(bootstrap_usable) >= 10:
        print(f"  Training {args.density_estimator.upper()} on "
              f"{len(bootstrap_usable)} bootstrap particles (round 0)...")
        train_t0 = time.time()
        theta_r0 = _theta_from_rows(bootstrap_usable, param_names)
        x_r0 = _x_from_rows(bootstrap_usable, stat_labels)
        density_estimator = _train_snpe(
            inference, theta_r0, x_r0, proposal=None, round_num=0,
            force_first_round_loss=args.force_first_round_loss,
        )
        round_summaries[-1]["final_train_loss"], round_summaries[-1]["final_val_loss"] = (
            _extract_training_losses(inference)
        )
        current_posterior = _build_posterior(inference, density_estimator)
        current_posterior.set_default_x(x_obs)
        current_proposal = current_posterior
        print(f"  Bootstrap training complete in {time.time() - train_t0:.1f}s")
    else:
        print(f"  WARNING: only {len(bootstrap_usable)} non-collapsed bootstrap "
              "particles — skipping initial training.")

    # ------------------------------------------------------------------
    # Active rounds: sample from posterior → simulate → train on new data only
    # ------------------------------------------------------------------
    for snpe_round in range(1, args.rounds + 1):
        print(f"\nRound {snpe_round} of {args.rounds}")
        train_dur = 0.0

        # --- Sampling phase ---
        if current_posterior is None:
            print("  No posterior yet — sampling from prior.")
            proposals = _sample_from_prior(calib_space, args.round_particles, rng)
        else:
            print(f"  Sampling {args.posterior_samples} proposals from posterior...")
            proposals = _sample_from_posterior(
                current_posterior, x_obs, args.posterior_samples,
                param_names, calib_space
            )
            proposals = proposals[: args.round_particles]

        # --- Simulation phase ---
        print(f"  Simulating {len(proposals)} proposals...")
        sim_t0 = time.time()
        payloads = [
            {
                "particle_id": (particle_counter := particle_counter + 1),
                "snpe_round": snpe_round,
                "base_config": base_config,
                "calib_space": calib_space,
                "params": draw,
                "targets": targets,
                "seed": args.seed + particle_counter,
                "acceptance_gate_settings": acceptance_gate_settings,
                "distance_settings": distance_settings,
                "simulation_start_year": getattr(args, "simulation_start_year", None),
                "scoring_start_year": getattr(args, "scoring_start_year", None),
                "summary_stat_spec": None,
            }
            for draw in proposals
        ]
        round_rows = _run_round_payloads(payloads, workers)
        sim_dur = time.time() - sim_t0
        all_rows.extend(round_rows)

        # --- Training phase: append ONLY this round's new data ---
        round_usable = [
            r for r in round_rows
            if not r.get("collapsed", True) and math.isfinite(r.get("abc_distance", math.inf))
        ]
        n_cumulative = sum(1 for r in all_rows if not r.get("collapsed", True))
        final_train_loss = float("nan")
        final_val_loss = float("nan")
        used_fallback_loss = False
        if len(round_usable) >= 10:
            print(f"  Training {args.density_estimator.upper()} on "
                  f"{len(round_usable)} new particles (cumulative non-collapsed: {n_cumulative})...")
            train_t0 = time.time()
            theta_new = _theta_from_rows(round_usable, param_names)
            x_new = _x_from_rows(round_usable, stat_labels)
            # Proposals for round >= 1 are drawn from the posterior/proposal
            # trained in the previous round (see the sampling phase above),
            # NOT the prior — so the APT/atomic proposal correction (Greenberg
            # et al. 2019) is required for the resulting flow to remain a
            # valid multi-round posterior estimator. Omitting it (as this
            # code used to, via force_first_round_loss=True with no
            # `proposal=`) silently biases the flow toward the region the
            # proposal already sampled, which is consistent with the
            # "zero posterior samples after round 0" failure mode logged in
            # PHASE2_DEVELOPMENT_CHECKLIST.md.
            density_estimator, used_fallback_loss = _train_round_with_fallback(
                inference, theta_new, x_new, current_proposal, snpe_round,
                args.force_first_round_loss,
            )
            if density_estimator is not None:
                final_train_loss, final_val_loss = _extract_training_losses(inference)
                current_posterior = _build_posterior(inference, density_estimator)
                current_posterior.set_default_x(x_obs)
                current_proposal = current_posterior
                train_dur = time.time() - train_t0
                print(f"  Training complete in {train_dur:.1f}s")
        else:
            print(f"  WARNING: only {len(round_usable)} non-collapsed particles "
                  "in this round — skipping training update.")

        rs = _round_summary(
            round_rows, snpe_round, train_dur, sim_dur,
            final_train_loss=final_train_loss,
            final_val_loss=final_val_loss,
            used_fallback_loss=used_fallback_loss,
        )
        round_summaries.append(rs)
        _print_round_summary(rs)

    # ------------------------------------------------------------------
    # Final posterior sampling — use the last trained posterior directly.
    # sbi has already accumulated all data across rounds; no retraining needed.
    # ------------------------------------------------------------------
    print("\nSampling from final posterior...")
    posterior_sample_rows: list[dict] = []

    if current_posterior is None:
        print("  WARNING: no posterior available (all particles collapsed).")
    else:
        print(f"  Drawing {args.final_samples} samples from final posterior...")
        final_samples = _sample_from_posterior(
            current_posterior, x_obs, args.final_samples, param_names, calib_space
        )
        print(f"  Evaluating {len(final_samples)} posterior samples with hard gates...")
        sim_t0 = time.time()
        post_payloads = [
            {
                "particle_id": f"post_{i+1}",
                "snpe_round": "posterior",
                "base_config": base_config,
                "calib_space": calib_space,
                "params": draw,
                "targets": targets,
                "seed": args.seed + 10_000_000 + i,
                "acceptance_gate_settings": acceptance_gate_settings,
                "distance_settings": distance_settings,
                "simulation_start_year": getattr(args, "simulation_start_year", None),
                "scoring_start_year": getattr(args, "scoring_start_year", None),
                "summary_stat_spec": None,
            }
            for i, draw in enumerate(final_samples)
        ]
        posterior_sample_rows = _run_round_payloads(post_payloads, workers)
        post_sim_dur = time.time() - sim_t0
        n_post_accept = sum(1 for r in posterior_sample_rows if r.get("overall_accept", False))
        print(f"  Posterior sample evaluation: {post_sim_dur:.1f}s  "
              f"accepted={n_post_accept}/{len(posterior_sample_rows)}")

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    print("\nWriting outputs...")

    # Particles CSV
    particles_path = output_dir / f"snpe_particles_{timestamp}.csv"
    pd.DataFrame(all_rows).to_csv(particles_path, index=False)
    print(f"  {particles_path.name}")

    # Round summary CSV
    rounds_path = output_dir / f"snpe_round_summary_{timestamp}.csv"
    pd.DataFrame(round_summaries).to_csv(rounds_path, index=False)
    print(f"  {rounds_path.name}")

    # Posterior samples CSV
    if posterior_sample_rows:
        post_path = output_dir / f"snpe_posterior_samples_{timestamp}.csv"
        pd.DataFrame(posterior_sample_rows).to_csv(post_path, index=False)
        print(f"  {post_path.name}")

    # Metadata JSON
    full_metadata = {
        **metadata_stub,
        "n_total_particles": len(all_rows),
        "n_posterior_samples": len(posterior_sample_rows),
        "n_posterior_accepted": sum(
            1 for r in posterior_sample_rows if r.get("overall_accept", False)
        ),
        "calib_space_summary": {
            "n_parameters": len(calib_space.get("parameters", [])),
            "parameter_names": param_names,
        },
        "posterior_sampling": {
            "reject_outside_prior": False,
            "bounded_filter": True,
            "max_draw_multiplier": 25,
            "clip_to_bounds": False,
        },
        "best_fit_selection_policy": (
            "accepted posterior samples sorted by distance; if none pass gates, "
            "fallback to lowest-distance evaluated particles"
        ),
        "n_summary_stats": N_SUMMARY_STATS,
        "round_summaries": round_summaries,
    }
    meta_path = output_dir / f"snpe_metadata_{timestamp}.json"
    write_json(meta_path, full_metadata)
    print(f"  {meta_path.name}")

    # Best-fit configs
    best_fit_paths = write_snpe_best_fit_configs(
        all_rows=all_rows,
        posterior_sample_rows=posterior_sample_rows,
        base_config=base_config,
        calib_space=calib_space,
        output_dir=output_dir,
        top_n=args.top_n,
        timestamp=timestamp,
        metadata_stub=metadata_stub,
    )
    print(f"  {len(best_fit_paths)} best-fit config(s) written")

    # Excel summary
    excel_path = _write_snpe_excel_summary(
        all_rows=all_rows,
        round_summaries=round_summaries,
        posterior_sample_rows=posterior_sample_rows,
        calib_space=calib_space,
        metadata=full_metadata,
        output_dir=output_dir,
        timestamp=timestamp,
    )
    print(f"  {excel_path.name}")

    # Final summary
    n_accepted_posterior = sum(
        1 for r in posterior_sample_rows if r.get("overall_accept", False)
    )
    n_accepted_particles = sum(1 for r in all_rows if r.get("overall_accept", False))
    print(f"\n{'='*60}")
    print(f"SNPE calibration complete")
    print(f"  Total particles evaluated : {len(all_rows)}")
    print(f"  Gate-accepted (particles) : {n_accepted_particles}")
    print(f"  Posterior samples         : {len(posterior_sample_rows)}")
    print(f"  Gate-accepted (posterior) : {n_accepted_posterior}")
    print(f"  Output dir: {output_dir}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Bootstrap CSV loader
# ---------------------------------------------------------------------------

def _load_bootstrap_csv(
    csv_path: Path,
    param_names: list[str],
    stat_labels: list[str],
    calib_space: dict,
) -> list[dict]:
    """Load a pre-existing LHS/ABC particle CSV as round-0 training data.

    The CSV must contain --store-timeseries output columns (indicator_year)
    so summary stats can be reconstructed.  Rows where all stat columns are
    already present (x_PLHIV_1990, ...) are used directly.

    Any CSV produced by run_lhs_calibration.py --store-timeseries qualifies.
    """
    df = pd.read_csv(csv_path, low_memory=False)
    rows: list[dict] = []

    stat_x_cols = [f"x_{label}" for label in stat_labels]
    have_x_cols = all(c in df.columns for c in stat_x_cols)

    # Build a minimal outputs dict from timeseries columns so we can call
    # compute_summary_stats if x_ cols are not pre-computed.
    for _, row in df.iterrows():
        row_dict = row.to_dict()

        # Reconstruct params dict from column values
        params = {name: float(row_dict[name]) for name in param_names if name in row_dict}
        if len(params) < len(param_names):
            continue  # row doesn't have all param columns — skip

        if have_x_cols:
            result = {
                "particle_id": row_dict.get("particle_id", row_dict.get("draw_id", "bootstrap")),
                "snpe_round": 0,
                **params,
                **{col: float(row_dict[col]) for col in stat_x_cols},
                "abc_distance": float(row_dict.get("abc_distance", math.inf)),
                "overall_accept": bool(row_dict.get("overall_accept", False)),
                "collapsed": bool(row_dict.get("collapsed", False)),
                "total_ll": float(row_dict.get("total_ll", float("-inf"))),
                "error": str(row_dict.get("error", "")),
            }
        else:
            # Reconstruct outputs from raw timeseries columns
            outputs: dict[str, dict[int, float]] = {}
            for col in df.columns:
                for indicator in ["PLHIV", "new_infections", "new_diagnoses",
                                   "new_diagnoses_msm", "aids_deaths", "on_art_stock"]:
                    if col.startswith(f"{indicator}_"):
                        try:
                            year = int(col[len(indicator)+1:])
                        except ValueError:
                            continue
                        outputs.setdefault(indicator, {})[year] = float(row_dict[col])
            if not outputs:
                continue
            x_sim = compute_summary_stats(outputs)
            result = {
                "particle_id": row_dict.get("particle_id", row_dict.get("draw_id", "bootstrap")),
                "snpe_round": 0,
                **params,
                **stat_row_fields(x_sim),
                "abc_distance": float(row_dict.get("abc_distance", math.inf)),
                "overall_accept": bool(row_dict.get("overall_accept", False)),
                "collapsed": bool(row_dict.get("collapsed", False)),
                "total_ll": float(row_dict.get("total_ll", float("-inf"))),
                "error": str(row_dict.get("error", "")),
            }
        rows.append(result)

    return rows


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

def _print_round_summary(rs: dict) -> None:
    print(
        f"    n={rs['n_particles']}  non_collapsed={rs['n_non_collapsed']}  "
        f"accept={rs['n_accept']}  dist_min={rs['abc_distance_min']:.4f}  "
        f"sim_time={rs['simulation_duration_s']:.1f}s"
    )
    final_train_loss = rs.get("final_train_loss", float("nan"))
    final_val_loss = rs.get("final_val_loss", float("nan"))
    if math.isfinite(final_train_loss) or math.isfinite(final_val_loss):
        fallback_note = " [fallback loss]" if rs.get("used_fallback_loss") else ""
        print(
            f"    train_loss={final_train_loss:.4f}  val_loss={final_val_loss:.4f}"
            f"{fallback_note}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SNPE epidemic calibration (simulation-based inference)."
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "V13.22_20260604_nearfit_rank001_basecase.json"),
        help="Path to base model config JSON. Defaults to the current V13.22 near-fit. "
             "All calibrated parameters are overwritten by inject_params on each draw; "
             "the structural settings (feature flags, seed year, non-calibrated params) are what matter.",
    )
    parser.add_argument(
        "--calib-space",
        default=str(ROOT / "configs" / "calib_space.json"),
        help="Path to calib_space JSON (same files used by LHS and ABC-SMC).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs" / "calibration" / "snpe"),
        help="Directory for all output files.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="Number of active learning rounds after bootstrap (default: 4).",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=2000,
        help="Particles to simulate in round 0 from the prior (default: 2000).",
    )
    parser.add_argument(
        "--round-particles",
        type=int,
        default=1000,
        help="Particles to simulate per active round (default: 1000).",
    )
    parser.add_argument(
        "--posterior-samples",
        type=int,
        default=1500,
        help="Proposals drawn from posterior per active round (default: 1500).",
    )
    parser.add_argument(
        "--final-samples",
        type=int,
        default=5000,
        help="Samples drawn from the final posterior for gate evaluation (default: 5000).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=-1,
        help="Parallel workers for simulation (-1 = all CPUs).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for prior sampling and simulation.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of best-fit config JSON files to write (default: 10).",
    )
    parser.add_argument(
        "--density-estimator",
        choices=["nsf", "maf"],
        default="maf",
        help="Normalizing flow architecture: 'maf' (Masked Autoregressive Flow, default) "
             "or 'nsf' (Neural Spline Flow — may fail numerically with this nflows version).",
    )
    parser.add_argument(
        "--bootstrap-csv",
        default=None,
        help="Optional path to an existing LHS/ABC particle CSV (with --store-timeseries "
             "columns) to use as round-0 training data instead of running new simulations.",
    )
    parser.add_argument(
        "--targets-workbook",
        default=str(default_disease_workbook_path(ROOT)),
        help="Path to disease targets Excel workbook.",
    )
    parser.add_argument(
        "--target-mapping",
        default=str(default_target_mapping_path(ROOT)),
        help="Path to target mapping JSON.",
    )
    parser.add_argument(
        "--soft-aids-gate",
        action="store_true",
        default=False,
        help="Demote the AIDS-death acceptance gate to a warning (matches ABC-SMC V13.18 behaviour).",
    )
    parser.add_argument(
        "--force-first-round-loss",
        action="store_true",
        default=False,
        help=(
            "Train every round with sbi's first-round (prior-assumed) loss, "
            "disabling the APT/atomic proposal correction even for rounds >= 1 "
            "whose proposals come from a trained posterior, not the prior. "
            "This reproduces the pre-fix behaviour; use only to diff against "
            "historical runs or as a manual fallback if proposal-corrected "
            "training is unstable on a given config."
        ),
    )
    parser.add_argument(
        "--simulation-start-year",
        type=int,
        default=None,
        help="Override simulation start year (default: 1980).",
    )
    parser.add_argument(
        "--scoring-start-year",
        type=int,
        default=None,
        help="Override scoring/extraction start year.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_snpe_calibration(args)


if __name__ == "__main__":
    main()
