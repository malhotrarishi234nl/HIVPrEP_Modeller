"""
Standalone LHS epidemic calibration runner.

This module is intentionally separate from the demographic calibration runner
(`run_calibration.py`).  Existing config JSON files are treated as read-only:
every draw operates on an in-memory deep copy, and best-fit configs are written
as new files under the requested output directory.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd
from scipy.stats import qmc

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_data_loader import default_unaids_path, load_unaids_targets, restrict_years
from calib_extractor import extract_annual_outputs
from calib_recent_fit import (
    RecentFitSettings,
    evaluate_recent_fit,
    recent_fit_row_fields,
)
from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_scorer import score_draw
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
    write_normalized_targets_csv,
)
from run_v3 import SimulationEngineV3


CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025
SIM_END_EXCLUSIVE = CALIB_END_YEAR + 1
HET_MALE_PATH = (
    "transmission_model.groups.advanced.orientation_contact_multipliers."
    "value.Heterosexual_Male"
)
MSM_PATH = (
    "transmission_model.groups.advanced.orientation_contact_multipliers."
    "value.MSM"
)
FEMALE_PATH = (
    "transmission_model.groups.advanced.orientation_contact_multipliers."
    "value.Female"
)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_json_safe(payload), f, indent=2, sort_keys=False)
        f.write("\n")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value) if not isinstance(value, (str, bytes, bool)) else False:
        return None
    return value


_PATH_PART_RE = re.compile(r"([A-Za-z0-9_ -]+)|\[(\d+)\]")


def parse_config_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        pos = 0
        for match in _PATH_PART_RE.finditer(part):
            if match.start() != pos:
                raise ValueError(f"Invalid config path token '{part}' in '{path}'")
            if match.group(1) is not None:
                tokens.append(match.group(1))
            else:
                tokens.append(int(match.group(2)))
            pos = match.end()
        if pos != len(part):
            raise ValueError(f"Invalid config path token '{part}' in '{path}'")
    return tokens


def get_nested(config: dict, path: str) -> Any:
    obj: Any = config
    for token in parse_config_path(path):
        if isinstance(token, int):
            if not isinstance(obj, list) or token >= len(obj):
                raise KeyError(f"List index {token} does not exist in path '{path}'")
            obj = obj[token]
        else:
            if not isinstance(obj, dict) or token not in obj:
                raise KeyError(f"Key '{token}' does not exist in path '{path}'")
            obj = obj[token]
    return obj


def set_nested(config: dict, path: str, value: Any) -> None:
    """Strict setter: destination key/index must already exist."""
    tokens = parse_config_path(path)
    if not tokens:
        raise ValueError("Config path cannot be empty")
    obj: Any = config
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(obj, list) or token >= len(obj):
                raise KeyError(f"List index {token} does not exist in path '{path}'")
            obj = obj[token]
        else:
            if not isinstance(obj, dict) or token not in obj:
                raise KeyError(f"Key '{token}' does not exist in path '{path}'")
            obj = obj[token]

    final = tokens[-1]
    if isinstance(final, int):
        if not isinstance(obj, list) or final >= len(obj):
            raise KeyError(f"List index {final} does not exist in path '{path}'")
        obj[final] = value
    else:
        if not isinstance(obj, dict) or final not in obj:
            raise KeyError(f"Key '{final}' does not exist in path '{path}'")
        obj[final] = value


def parameter_by_name(calib_space: dict) -> dict[str, dict]:
    return {param["name"]: param for param in calib_space.get("parameters", [])}


def filter_calib_space_for_stage(calib_space: dict, stage: str) -> dict:
    """Return a copy containing only parameters active for the requested stage."""
    if stage == "all":
        return copy.deepcopy(calib_space)
    filtered = copy.deepcopy(calib_space)
    for key in ("parameters", "fixed_parameters", "excluded_parameters"):
        filtered[key] = [
            param for param in filtered.get(key, [])
            if stage in param.get("stages", ["backbone", "joint"])
        ]
    return filtered


def validate_config_paths(base_config: dict, calib_space: dict) -> None:
    derived_seed_share_names = {
        "seed_chronic_share", "seed_aids_share", "seed_acute_share",
        # V13.11: orientation-specific derived shares (injected specially, no direct config_path)
        "seed_msm_chronic_share", "seed_msm_aids_share", "seed_msm_acute_share",
        "seed_non_msm_chronic_share", "seed_non_msm_aids_share", "seed_non_msm_acute_share",
    }
    paths = [
        param["config_path"]
        for param in calib_space.get("parameters", [])
        if param.get("name") not in derived_seed_share_names
    ]
    for param in calib_space.get("fixed_parameters", []):
        paths.append(param["config_path"])
    for param in calib_space.get("excluded_parameters", []):
        if param.get("name") not in derived_seed_share_names:
            paths.append(param["config_path"])
    paths.extend(
        [
            "population.activity_distribution.value.Low",
            "output.enabled.value",
            "output.terminal_summary.value",
            "run_control.start_year.value",
            "run_control.end_year.value",
            "run_control.deterministic_mode.value",
        ]
    )
    for path in paths:
        get_nested(base_config, path)


def _uniform_draws(parameters: Sequence[dict], n: int, seed: int) -> pd.DataFrame:
    sampler = qmc.LatinHypercube(d=len(parameters), seed=seed)
    unit_samples = sampler.random(n=n)
    rows: dict[str, np.ndarray] = {}
    for idx, param in enumerate(parameters):
        low = float(param["min"])
        high = float(param["max"])
        rows[param["name"]] = low + unit_samples[:, idx] * (high - low)
    return pd.DataFrame(rows)


def _parameter_bounds(parameters: Sequence[dict]) -> dict[str, tuple[float, float]]:
    return {
        param["name"]: (float(param["min"]), float(param["max"]))
        for param in parameters
    }


def refine_calib_space_around_base(
    base_config: dict,
    calib_space: dict,
    fraction: float,
) -> dict:
    """
    Narrow active parameter bounds around the current input config.

    A fraction of 0.25 means each refined range spans 25% of the original range,
    centred on the input value where possible and clipped to the original bounds.
    """
    if fraction <= 0.0:
        return copy.deepcopy(calib_space)
    if fraction > 1.0:
        raise ValueError("refinement fraction must be <= 1.0")

    refined = copy.deepcopy(calib_space)
    for param in refined.get("parameters", []):
        original_low = float(param["min"])
        original_high = float(param["max"])
        span = original_high - original_low
        if span <= 0.0:
            continue
        try:
            center = float(get_nested(base_config, param["config_path"]))
        except (KeyError, TypeError, ValueError):
            continue
        half_width = span * fraction / 2.0
        low = max(original_low, center - half_width)
        high = min(original_high, center + half_width)
        if high <= low:
            continue
        param["min"] = low
        param["max"] = high
        param["refined_from"] = {
            "original_min": original_low,
            "original_max": original_high,
            "center": center,
            "fraction": fraction,
        }
    return refined


def _batch_seed(seed: int, batch_idx: int) -> int:
    """Derive independent retry-batch seeds from the run seed."""
    return int(np.random.SeedSequence([int(seed), int(batch_idx)]).generate_state(1, dtype=np.uint32)[0])


def _with_derived_parameters(draws: pd.DataFrame) -> pd.DataFrame:
    result = draws.copy()
    if "activity_med" in result.columns and "activity_high" in result.columns:
        result["activity_low"] = 1.0 - result["activity_med"] - result["activity_high"]
    if "seed_chronic_share" in result.columns and "seed_aids_share" in result.columns:
        result["seed_acute_share"] = 1.0 - result["seed_chronic_share"] - result["seed_aids_share"]
    # V13.11: orientation-specific derived acute shares
    if "seed_msm_chronic_share" in result.columns and "seed_msm_aids_share" in result.columns:
        result["seed_msm_acute_share"] = 1.0 - result["seed_msm_chronic_share"] - result["seed_msm_aids_share"]
    if "seed_non_msm_chronic_share" in result.columns and "seed_non_msm_aids_share" in result.columns:
        result["seed_non_msm_acute_share"] = 1.0 - result["seed_non_msm_chronic_share"] - result["seed_non_msm_aids_share"]
    return result


def _valid_draw_mask(draws: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=draws.index)
    if {"contact_low", "contact_med", "contact_high"}.issubset(draws.columns):
        mask &= draws["contact_low"] < draws["contact_med"]
        mask &= draws["contact_med"] < draws["contact_high"]
    diagnosis_cols = ["diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2019"]
    if not set(diagnosis_cols).issubset(draws.columns):
        diagnosis_cols = ["diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2015"]
    if set(diagnosis_cols).issubset(draws.columns):
        for earlier, later in zip(diagnosis_cols, diagnosis_cols[1:]):
            mask &= draws[earlier] <= draws[later]
    art_cols = ["art_init_1996", "art_init_2000", "art_init_2015", "art_init_2020"]
    if set(art_cols).issubset(draws.columns):
        for earlier, later in zip(art_cols, art_cols[1:]):
            mask &= draws[earlier] <= draws[later]
    if "activity_low" in draws.columns:
        mask &= draws["activity_low"].between(0.14, 0.81)
    if "seed_acute_share" in draws.columns:
        mask &= draws["seed_acute_share"].between(0.0, 1.0)
    if "seed_chronic_share" in draws.columns:
        mask &= draws["seed_chronic_share"].between(0.0, 1.0)
    if "seed_aids_share" in draws.columns:
        mask &= draws["seed_aids_share"].between(0.0, 1.0)
    # V13.11: orientation-specific seed stage simplex validity
    if "seed_msm_acute_share" in draws.columns:
        mask &= draws["seed_msm_acute_share"] >= 0.0
    if "seed_non_msm_acute_share" in draws.columns:
        mask &= draws["seed_non_msm_acute_share"] >= 0.0
    # V13.11 pilot constraint: MSM AIDS share should not exceed non-MSM AIDS share
    if "seed_msm_aids_share" in draws.columns and "seed_non_msm_aids_share" in draws.columns:
        mask &= draws["seed_msm_aids_share"] <= draws["seed_non_msm_aids_share"]
    return mask


def generate_lhs_draws(parameters: Sequence[dict], n: int, seed: int, max_batches: int = 10) -> list[dict]:
    """Generate constrained LHS draws, preserving first n valid samples."""
    valid_parts = []
    generated = 0
    for batch_idx in range(max_batches):
        batch_n = n if batch_idx == 0 else max(n, math.ceil(n * 0.5))
        generated += batch_n
        draws = _with_derived_parameters(_uniform_draws(parameters, batch_n, _batch_seed(seed, batch_idx)))
        valid_parts.append(draws[_valid_draw_mask(draws)])
        valid = pd.concat(valid_parts, ignore_index=True)
        if len(valid) >= n:
            return valid.iloc[:n].to_dict("records")
    valid = pd.concat(valid_parts, ignore_index=True) if valid_parts else pd.DataFrame()
    if len(valid) < n:
        raise RuntimeError(
            f"Could not generate {n} valid LHS draws after {generated} samples; "
            f"only {len(valid)} passed constraints"
        )
    return valid.iloc[:n].to_dict("records")


def inject_params(
    base_config: dict,
    param_vector: dict,
    calib_space: dict,
    deterministic_mode: bool | None = None,
    simulation_start_year: int | None = None,
) -> dict:
    """Deep-copy base config and inject one calibration parameter vector."""
    config = copy.deepcopy(base_config)
    params = parameter_by_name(calib_space)

    for name, value in param_vector.items():
        if name in {
            "activity_low",
            "seed_acute_share", "seed_chronic_share", "seed_aids_share",
            # V13.11: orientation-specific shares handled below
            "seed_msm_acute_share", "seed_msm_chronic_share", "seed_msm_aids_share",
            "seed_non_msm_acute_share", "seed_non_msm_chronic_share", "seed_non_msm_aids_share",
        }:
            continue
        if name not in params:
            raise KeyError(f"Unknown calibration parameter '{name}'")
        set_nested(config, params[name]["config_path"], float(value))

    # V13.9.3: when p_msm is sampled, keep both population initialization and ongoing
    # sexual-debut cohorts on the same MSM-risk denominator.
    if "p_msm" in param_vector:
        set_nested(config, "demography.sexual_debut.p_msm.value", float(param_vector["p_msm"]))

    if "activity_med" in param_vector and "activity_high" in param_vector:
        low = round(1.0 - float(param_vector["activity_med"]) - float(param_vector["activity_high"]), 10)
        if not 0.14 <= low <= 0.81:
            raise ValueError(f"Derived activity_low out of bounds: {low}")
        set_nested(config, "population.activity_distribution.value.Low", low)
        set_nested(config, "population.activity_distribution.value.Medium", float(param_vector["activity_med"]))
        set_nested(config, "population.activity_distribution.value.High", float(param_vector["activity_high"]))

    if "seed_chronic_share" in param_vector and "seed_aids_share" in param_vector:
        chronic = float(param_vector["seed_chronic_share"])
        aids = float(param_vector["seed_aids_share"])
        acute = round(1.0 - chronic - aids, 10)
        if acute < 0.0:
            raise ValueError(
                "Derived seed_acute_share out of bounds: "
                f"acute={acute}, chronic={chronic}, aids={aids}"
            )
        config.setdefault("population", {}).setdefault("epidemic_seed", {})["stage_distribution"] = {
            "value": {
                "Acute": acute,
                "Chronic": chronic,
                "AIDS": aids,
            },
            "notes": "V13.7 LHS-derived fixed-total epidemic seed stage distribution.",
        }

    # V13.11: orientation-specific seed stage injection
    if ("seed_msm_chronic_share" in param_vector and "seed_msm_aids_share" in param_vector
            and "seed_non_msm_chronic_share" in param_vector and "seed_non_msm_aids_share" in param_vector):
        msm_chronic = float(param_vector["seed_msm_chronic_share"])
        msm_aids = float(param_vector["seed_msm_aids_share"])
        msm_acute = round(1.0 - msm_chronic - msm_aids, 10)
        non_msm_chronic = float(param_vector["seed_non_msm_chronic_share"])
        non_msm_aids = float(param_vector["seed_non_msm_aids_share"])
        non_msm_acute = round(1.0 - non_msm_chronic - non_msm_aids, 10)
        if msm_acute < 0.0:
            raise ValueError(
                "Derived seed_msm_acute_share out of bounds: "
                f"msm_acute={msm_acute}, msm_chronic={msm_chronic}, msm_aids={msm_aids}"
            )
        if non_msm_acute < 0.0:
            raise ValueError(
                "Derived seed_non_msm_acute_share out of bounds: "
                f"non_msm_acute={non_msm_acute}, non_msm_chronic={non_msm_chronic}, "
                f"non_msm_aids={non_msm_aids}"
            )
        ep_seed = config.setdefault("population", {}).setdefault("epidemic_seed", {})
        ep_seed["stage_distribution_by_orientation"] = {
            "enabled": {"value": True},
            "value": {
                "MSM":     {"Acute": msm_acute,     "Chronic": msm_chronic,     "AIDS": msm_aids},
                "non_MSM": {"Acute": non_msm_acute, "Chronic": non_msm_chronic, "AIDS": non_msm_aids},
            },
        }

    for fixed in calib_space.get("fixed_parameters", []):
        set_nested(config, fixed["config_path"], fixed["value"])

    effective_start = simulation_start_year if simulation_start_year is not None else CALIB_START_YEAR
    set_nested(config, "run_control.start_year.value", effective_start)
    set_nested(config, "run_control.end_year.value", SIM_END_EXCLUSIVE)
    if deterministic_mode is not None:
        set_nested(config, "run_control.deterministic_mode.value", bool(deterministic_mode))
    set_nested(config, "output.enabled.value", False)
    set_nested(config, "output.terminal_summary.value", False)
    return config


def _run_simulation_quietly(config: dict, seed: int) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(config, seed=seed)
        return engine.run(run_number=1)


def _run_draw_worker(payload: dict) -> dict:
    draw_id = payload["draw_id"]
    base_config = payload["base_config"]
    calib_space = payload["calib_space"]
    params = payload["params"]
    targets = payload["targets"]
    seed = payload["seed"]
    deterministic_calibration = payload.get("deterministic_calibration")
    balance_target_families = bool(payload.get("balance_target_families", False))
    recent_fit_settings = payload.get("recent_fit_settings")
    acceptance_gate_settings = payload.get("acceptance_gate_settings")
    simulation_start_year = payload.get("simulation_start_year")
    scoring_start_year = payload.get("scoring_start_year")
    store_timeseries = bool(payload.get("store_timeseries", False))
    try:
        config = inject_params(
            base_config,
            params,
            calib_space,
            deterministic_mode=deterministic_calibration,
            simulation_start_year=simulation_start_year,
        )
        result = _run_simulation_quietly(config, seed=seed)
        extract_start = scoring_start_year if scoring_start_year is not None else CALIB_START_YEAR
        outputs = extract_annual_outputs(result, extract_start, CALIB_END_YEAR)
        score = score_draw(
            outputs,
            targets,
            balance_families=balance_target_families,
        )
        row = {
            "draw_id": draw_id,
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
        if isinstance(targets, list) and recent_fit_settings is not None:
            recent_fit = evaluate_recent_fit(
                outputs,
                targets,
                settings=recent_fit_settings,
            )
            row.update(recent_fit_row_fields(recent_fit))
        if isinstance(targets, list) and acceptance_gate_settings is not None:
            acceptance = evaluate_acceptance_gates(
                outputs,
                targets,
                settings=acceptance_gate_settings,
            )
            row.update(acceptance_gate_row_fields(acceptance))
        if store_timeseries:
            for _indicator, _series in outputs.items():
                for _year, _val in sorted(_series.items()):
                    try:
                        row[f"{_indicator}_{_year}"] = float(_val)
                    except (TypeError, ValueError):
                        row[f"{_indicator}_{_year}"] = None
        return row
    except Exception as exc:
        row = {
            "draw_id": draw_id,
            **{k: float(v) for k, v in params.items()},
            "total_ll": float("-inf"),
            "collapsed": True,
            "n_years_scored": 0,
            "missing_outputs": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
        return row


def _param_vector_from_row(row: dict, param_names: Sequence[str]) -> dict:
    vector = {name: row[name] for name in param_names if name in row}
    if "activity_low" in row:
        vector["activity_low"] = row["activity_low"]
    return vector


def _run_payloads(payloads: list[dict], workers: int) -> list[dict]:
    if workers == 1:
        return [_run_draw_worker(payload) for payload in payloads]

    rows = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_id = {executor.submit(_run_draw_worker, payload): payload["draw_id"] for payload in payloads}
        for future in as_completed(future_to_id):
            rows.append(future.result())
    return rows


def rerank_top_draws(
    ranked: pd.DataFrame,
    base_config: dict,
    calib_space: dict,
    targets,
    seed: int,
    top_n: int,
    replicates: int,
    workers: int,
    balance_target_families: bool,
    recent_fit_settings: RecentFitSettings | None = None,
) -> pd.DataFrame:
    """Rerank elite draws using stochastic replicate means."""
    if top_n <= 0 or replicates <= 1 or ranked.empty:
        return ranked

    param_names = [p["name"] for p in calib_space.get("parameters", [])]
    candidates = ranked[np.isfinite(ranked["total_ll"].astype(float))].head(top_n)
    payloads: list[dict] = []
    for candidate_idx, row in enumerate(candidates.to_dict("records"), start=1):
        vector = _param_vector_from_row(row, param_names)
        for replicate_idx in range(replicates):
            payloads.append(
                {
                    "draw_id": row["draw_id"],
                    "candidate_idx": candidate_idx,
                    "replicate_idx": replicate_idx + 1,
                    "base_config": base_config,
                    "calib_space": calib_space,
                    "params": vector,
                    "targets": targets,
                    "seed": seed + 100_000 + int(row["draw_id"]) * 1_000 + replicate_idx,
                    "deterministic_calibration": False,
                    "balance_target_families": balance_target_families,
                    "recent_fit_settings": recent_fit_settings,
                }
            )

    replicate_rows = _run_payloads(payloads, workers)
    replicate_df = pd.DataFrame(replicate_rows)
    if replicate_df.empty:
        return ranked

    numeric_total = pd.to_numeric(replicate_df["total_ll"], errors="coerce")
    replicate_df = replicate_df.assign(_total_ll_numeric=numeric_total)
    aggregate_spec = {
        "rerank_total_ll_mean": ("_total_ll_numeric", "mean"),
        "rerank_total_ll_sd": ("_total_ll_numeric", "std"),
        "rerank_replicates": ("_total_ll_numeric", "count"),
    }
    if "recent_fit_pass" in replicate_df.columns:
        aggregate_spec["rerank_recent_fit_pass_rate"] = ("recent_fit_pass", "mean")
    aggregate = replicate_df.groupby("draw_id").agg(**aggregate_spec).reset_index()
    updated = ranked.merge(aggregate, on="draw_id", how="left")
    updated["screen_total_ll"] = updated["total_ll"]
    reranked_mask = updated["rerank_total_ll_mean"].notna()
    updated.loc[reranked_mask, "total_ll"] = updated.loc[reranked_mask, "rerank_total_ll_mean"]
    updated["_reranked_priority"] = reranked_mask.astype(int)
    updated["_rank_key"] = pd.to_numeric(updated["total_ll"], errors="coerce").replace([np.inf], np.nan)
    updated = (
        updated.sort_values(
            ["_reranked_priority", "_rank_key"],
            ascending=[False, False],
            na_position="last",
        )
        .drop(columns=["_reranked_priority", "_rank_key"])
        .reset_index(drop=True)
    )
    return updated
def apply_recent_fit_gate(ranked: pd.DataFrame) -> pd.DataFrame:
    """Prioritize candidates that pass recent-fit eligibility."""
    if ranked.empty or "recent_fit_pass" not in ranked.columns:
        return ranked
    if not ranked["recent_fit_pass"].fillna(False).any():
        raise RuntimeError(
            "Recent-fit gate rejected all candidates. Inspect recent_fit_fail_reasons "
            "in the ranked CSV before widening thresholds or rerunning."
        )
    work = ranked.copy()
    work["_recent_fit_priority"] = work["recent_fit_pass"].fillna(False).astype(int)
    work["_rank_key"] = pd.to_numeric(work["total_ll"], errors="coerce").replace([np.inf], np.nan)
    return (
        work.sort_values(
            ["_recent_fit_priority", "_rank_key"],
            ascending=[False, False],
            na_position="last",
        )
        .drop(columns=["_recent_fit_priority", "_rank_key"])
        .reset_index(drop=True)
    )


def apply_acceptance_gate_priority(ranked: pd.DataFrame) -> pd.DataFrame:
    """Prioritize candidates passing all acceptance gates, retaining all rows."""
    if ranked.empty or "overall_accept" not in ranked.columns:
        return ranked
    work = ranked.copy()
    work["_acceptance_priority"] = (
        work["overall_accept"].map(lambda v: False if pd.isna(v) else bool(v)).astype(int)
    )
    work["_n_failures"] = pd.to_numeric(work.get("acceptance_n_failures", 999), errors="coerce").fillna(999)
    work["_rank_key"] = pd.to_numeric(work["total_ll"], errors="coerce").replace([np.inf], np.nan)
    return (
        work.sort_values(
            ["_acceptance_priority", "_n_failures", "_rank_key"],
            ascending=[False, True, False],
            na_position="last",
        )
        .drop(columns=["_acceptance_priority", "_n_failures", "_rank_key"])
        .reset_index(drop=True)
    )


def run_orientation_sensitivity(base_config: dict, calib_space: dict, targets: dict, seed: int) -> dict:
    """Screen MSM multiplier for observable calibration effect."""
    included_in_lhs = "orient_msm" in parameter_by_name(calib_space)
    low_value = 1.0
    high_value = 5.0
    comparisons = []
    for value in (low_value, high_value):
        cfg = copy.deepcopy(base_config)
        for fixed in calib_space.get("fixed_parameters", []):
            set_nested(cfg, fixed["config_path"], fixed["value"])
        set_nested(cfg, HET_MALE_PATH, 1.0)
        set_nested(cfg, MSM_PATH, value)
        set_nested(cfg, "run_control.start_year.value", CALIB_START_YEAR)
        set_nested(cfg, "run_control.end_year.value", SIM_END_EXCLUSIVE)
        set_nested(cfg, "run_control.deterministic_mode.value", True)
        set_nested(cfg, "output.enabled.value", False)
        set_nested(cfg, "output.terminal_summary.value", False)
        result = _run_simulation_quietly(cfg, seed=seed)
        outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
        score = score_draw(outputs, targets)
        comparisons.append({"value": value, "outputs": outputs, "score": score})

    max_abs_diff = 0.0
    for indicator in ("PLHIV", "new_infections", "aids_deaths"):
        years = set(comparisons[0]["outputs"][indicator]) | set(comparisons[1]["outputs"][indicator])
        for year in years:
            diff = abs(
                comparisons[0]["outputs"][indicator].get(year, 0.0)
                - comparisons[1]["outputs"][indicator].get(year, 0.0)
            )
            max_abs_diff = max(max_abs_diff, diff)

    screened_inert = max_abs_diff <= 1e-6
    return {
        "parameter": "orient_msm",
        "config_path": MSM_PATH,
        "values_tested": [low_value, high_value],
        "max_abs_output_diff": max_abs_diff,
        "screened_inert": screened_inert,
        "included_in_lhs": included_in_lhs,
        "reason": (
            "Diagnostic only. Heterosexual_Male is fixed as the 1.0 reference; "
            "the active backbone/joint calibration space samples MSM above that reference."
        ),
    }


def include_het_male_parameter(calib_space: dict) -> dict:
    """Backward-compatible no-op retained for older callers."""
    updated = copy.deepcopy(calib_space)
    return updated


def _write_excel_summary(path: Path, ranked: pd.DataFrame, calib_space: dict, metadata: dict, top_n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        ranked.head(top_n).to_excel(writer, sheet_name="Top_Draws", index=False)
        ranked.to_excel(writer, sheet_name="All_Draws", index=False)
        pd.DataFrame(calib_space.get("parameters", [])).to_excel(writer, sheet_name="Parameter_Space", index=False)
        pd.DataFrame(calib_space.get("fixed_parameters", [])).to_excel(writer, sheet_name="Fixed_Parameters", index=False)
        pd.DataFrame(calib_space.get("excluded_parameters", [])).to_excel(writer, sheet_name="Excluded_Parameters", index=False)
        if "target_summary" in metadata:
            pd.DataFrame(metadata["target_summary"]).to_excel(writer, sheet_name="Target_Summary", index=False)
        pd.DataFrame(
            [{"key": key, "value": json.dumps(value) if isinstance(value, (dict, list)) else value}
             for key, value in metadata.items()]
        ).to_excel(writer, sheet_name="Metadata", index=False)


def _finite_ranked(results: pd.DataFrame) -> pd.DataFrame:
    ranked = results.copy()
    ranked["_rank_key"] = ranked["total_ll"].replace([np.inf], np.nan)
    ranked = ranked.sort_values("_rank_key", ascending=False, na_position="last").drop(columns=["_rank_key"])
    return ranked.reset_index(drop=True)


def write_best_fit_configs(
    ranked: pd.DataFrame,
    base_config: dict,
    calib_space: dict,
    output_dir: Path,
    top_n: int,
    metadata_stub: dict,
) -> list[str]:
    paths: list[str] = []
    valid = ranked[np.isfinite(ranked["total_ll"].astype(float))].head(top_n)
    param_names = [p["name"] for p in calib_space.get("parameters", [])]
    for idx, row in enumerate(valid.itertuples(index=False), start=1):
        row_dict = row._asdict()
        vector = {name: row_dict[name] for name in param_names if name in row_dict}
        if "activity_low" in ranked.columns:
            vector["activity_low"] = row_dict.get("activity_low")
        cfg = inject_params(
            base_config,
            vector,
            calib_space,
            deterministic_mode=metadata_stub.get("deterministic_calibration"),
        )
        cfg.setdefault("calibration_metadata", {})
        recent_fit_metadata = {
            key.replace("recent_fit_", "", 1): value
            for key, value in row_dict.items()
            if key.startswith("recent_fit_")
        }
        cfg["calibration_metadata"]["lhs_calibration"] = {
            **metadata_stub,
            "rank": idx,
            "draw_id": int(row_dict["draw_id"]),
            "total_ll": float(row_dict["total_ll"]),
            "parameters": vector,
            "recent_fit": recent_fit_metadata,
        }
        path = output_dir / f"lhs_best_fit_rank_{idx:03d}.json"
        write_json(path, cfg)
        paths.append(str(path))
    return paths


def run_lhs_calibration(args: argparse.Namespace) -> dict:
    config_path = Path(args.config).resolve()
    calib_space_path = Path(args.calib_space).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_sha_before = sha256_file(config_path)
    base_config = load_json(config_path)
    raw_calib_space = load_json(calib_space_path)
    calib_space = filter_calib_space_for_stage(raw_calib_space, args.stage)
    validate_config_paths(base_config, calib_space)
    refine_fraction = float(getattr(args, "refine_around_input", 0.0))
    if refine_fraction:
        calib_space = refine_calib_space_around_base(base_config, calib_space, refine_fraction)

    normalized_targets_path = None
    if args.legacy_unaids_targets:
        unaids_path = Path(args.unaids_data).resolve() if args.unaids_data else default_unaids_path(ROOT)
        targets = restrict_years(load_unaids_targets(unaids_path), 1990, CALIB_END_YEAR)
        target_source_path = str(unaids_path)
        target_mapping_path = None
        target_records = None
    else:
        workbook_path = Path(args.targets_workbook).resolve()
        mapping_path = Path(args.target_mapping).resolve()
        target_records = load_disease_targets(
            workbook_path,
            mapping_path,
            stage=args.stage,
            start_year=1990,
            end_year=CALIB_END_YEAR,
        )
        targets = target_records
        target_source_path = str(workbook_path)
        target_mapping_path = str(mapping_path)

    orientation_sensitivity = None
    if not args.skip_het_sensitivity:
        print("Running MSM orientation pre-flight sensitivity check...")
        orientation_sensitivity = run_orientation_sensitivity(base_config, calib_space, targets, seed=args.seed)
        if (
            getattr(args, "fail_on_inert_sensitivity", False)
            and orientation_sensitivity.get("included_in_lhs")
            and orientation_sensitivity.get("screened_inert")
        ):
            raise RuntimeError(
                "Orientation sensitivity check found an active but inert LHS parameter: "
                f"{orientation_sensitivity['parameter']}"
            )

    parameters = calib_space.get("parameters", [])
    draws = generate_lhs_draws(parameters, args.n, args.seed)
    workers = os.cpu_count() if args.workers == -1 else max(1, args.workers)
    print(f"Running {len(draws)} LHS draws with {workers} worker(s)...")
    deterministic_calibration = bool(getattr(args, "deterministic_calibration", True))
    balance_target_families = bool(getattr(args, "family_balanced_score", False))
    recent_fit_settings = (
        RecentFitSettings(
            min_observed_fraction=float(getattr(args, "recent_fit_min_observed_fraction", 0.75)),
            sigma_multiplier=float(getattr(args, "recent_fit_sigma_multiplier", 3.0)),
            diagnosis_year=getattr(args, "recent_fit_diagnosis_year", None),
        )
        if isinstance(targets, list) and bool(getattr(args, "recent_fit_audit", True))
        else None
    )
    acceptance_gate_settings = (
        AcceptanceGateSettings(
            diagnosis_floor=float(getattr(args, "acceptance_diagnosis_floor", 0.90)),
            diagnosis_ceiling=float(getattr(args, "acceptance_diagnosis_ceiling", 1.50)),
            plhiv_recent_mape_ceiling=float(getattr(args, "acceptance_plhiv_recent_mape_ceiling", 10.0)),
            incidence_recent_ratio_floor=float(getattr(args, "acceptance_incidence_recent_ratio_floor", 0.50)),
            incidence_recent_ratio_ceiling=float(getattr(args, "acceptance_incidence_recent_ratio_ceiling", 1.75)),
            aids_bounds_fraction_floor=float(getattr(args, "acceptance_aids_bounds_fraction_floor", 0.40)),
            art_recent_mape_ceiling=float(getattr(args, "acceptance_art_recent_mape_ceiling", 10.0)),
        )
        if isinstance(targets, list) and bool(getattr(args, "acceptance_gate_audit", False))
        else None
    )

    sim_start_override = getattr(args, "simulation_start_year", None)
    score_start_override = getattr(args, "scoring_start_year", None)

    payloads = [
        {
            "draw_id": idx,
            "base_config": base_config,
            "calib_space": calib_space,
            "params": draw,
            "targets": targets,
            "seed": args.seed + idx,
            "deterministic_calibration": deterministic_calibration,
            "balance_target_families": balance_target_families,
            "recent_fit_settings": recent_fit_settings,
            "acceptance_gate_settings": acceptance_gate_settings,
            "simulation_start_year": sim_start_override,
            "scoring_start_year": score_start_override,
            "store_timeseries": bool(getattr(args, "store_timeseries", False)),
        }
        for idx, draw in enumerate(draws, start=1)
    ]

    rows = _run_payloads(payloads, workers)
    ranked = _finite_ranked(pd.DataFrame(rows))
    rerank_top = int(getattr(args, "rerank_top", 0))
    rerank_replicates = int(getattr(args, "rerank_replicates", 1))
    if rerank_top > 0 and rerank_replicates > 1:
        print(f"Reranking top {rerank_top} draw(s) with {rerank_replicates} stochastic replicates...")
        ranked = rerank_top_draws(
            ranked,
            base_config,
            calib_space,
            targets,
            seed=args.seed,
            top_n=rerank_top,
            replicates=rerank_replicates,
            workers=workers,
            balance_target_families=balance_target_families,
            recent_fit_settings=recent_fit_settings,
        )
    if bool(getattr(args, "recent_fit_gate", False)):
        ranked = apply_recent_fit_gate(ranked)
    if bool(getattr(args, "acceptance_gate", False)):
        ranked = apply_acceptance_gate_priority(ranked)

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if target_records is not None:
        normalized_targets_path = output_dir / f"normalized_targets_{args.stage}_{run_stamp}.csv"
        write_normalized_targets_csv(normalized_targets_path, target_records)
    full_csv = output_dir / f"lhs_ranked_results_{run_stamp}.csv"
    ranked.to_csv(full_csv, index=False)

    source_sha_after = sha256_file(config_path)
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_config_path": str(config_path),
        "source_sha256_before": source_sha_before,
        "source_sha256_after": source_sha_after,
        "source_config_unchanged": source_sha_before == source_sha_after,
        "calib_space_path": str(calib_space_path),
        "stage": args.stage,
        "target_source_path": target_source_path,
        "target_mapping_path": target_mapping_path,
        "normalized_targets_path": str(normalized_targets_path) if normalized_targets_path else None,
        "legacy_unaids_targets": bool(args.legacy_unaids_targets),
        "n_draws": args.n,
        "seed": args.seed,
        "workers": workers,
        "deterministic_calibration": deterministic_calibration,
        "family_balanced_score": balance_target_families,
        "refine_around_input": refine_fraction,
        "rerank_top": rerank_top,
        "rerank_replicates": rerank_replicates,
        "recent_fit_audit": recent_fit_settings is not None,
        "recent_fit_gate": bool(getattr(args, "recent_fit_gate", False)),
        "acceptance_gate_audit": acceptance_gate_settings is not None,
        "acceptance_gate": bool(getattr(args, "acceptance_gate", False)),
        "acceptance_gate_settings": (
            acceptance_gate_settings.__dict__ if acceptance_gate_settings else None
        ),
        "recent_fit_min_observed_fraction": (
            recent_fit_settings.min_observed_fraction if recent_fit_settings else None
        ),
        "recent_fit_sigma_multiplier": (
            recent_fit_settings.sigma_multiplier if recent_fit_settings else None
        ),
        "recent_fit_diagnosis_year": (
            recent_fit_settings.diagnosis_year if recent_fit_settings else None
        ),
        "calibration_window": [CALIB_START_YEAR, CALIB_END_YEAR],
        "simulation_start_year": sim_start_override,
        "scoring_start_year": score_start_override,
        "orientation_sensitivity": orientation_sensitivity,
        "het_male_sensitivity": orientation_sensitivity,
        "parameter_names": [p["name"] for p in parameters],
        "fixed_parameter_names": [p["name"] for p in calib_space.get("fixed_parameters", [])],
        "excluded_parameter_names": [p["name"] for p in calib_space.get("excluded_parameters", [])],
    }
    if target_records is not None:
        target_df = pd.DataFrame(target_records)
        metadata["target_summary"] = (
            target_df.groupby(["target_family", "model_output", "calibration_use"])
            .size()
            .reset_index(name="n_targets")
            .to_dict("records")
        )

    metadata_stub = {
        "source_config_path": str(config_path),
        "source_sha256_before": source_sha_before,
        "created_at": metadata["created_at"],
        "seed": args.seed,
        "stage": args.stage,
        "deterministic_calibration": deterministic_calibration,
        "family_balanced_score": balance_target_families,
        "refine_around_input": refine_fraction,
        "rerank_top": rerank_top,
        "rerank_replicates": rerank_replicates,
        "recent_fit_gate": bool(getattr(args, "recent_fit_gate", False)),
        "acceptance_gate_audit": acceptance_gate_settings is not None,
        "acceptance_gate": bool(getattr(args, "acceptance_gate", False)),
        "acceptance_gate_settings": (
            acceptance_gate_settings.__dict__ if acceptance_gate_settings else None
        ),
        "recent_fit_min_observed_fraction": (
            recent_fit_settings.min_observed_fraction if recent_fit_settings else None
        ),
        "recent_fit_sigma_multiplier": (
            recent_fit_settings.sigma_multiplier if recent_fit_settings else None
        ),
        "recent_fit_diagnosis_year": (
            recent_fit_settings.diagnosis_year if recent_fit_settings else None
        ),
        "orientation_sensitivity": orientation_sensitivity,
        "het_male_sensitivity": orientation_sensitivity,
        "fixed_parameters": calib_space.get("fixed_parameters", []),
    }
    best_fit_paths = write_best_fit_configs(
        ranked, base_config, calib_space, output_dir, args.top_n, metadata_stub
    )
    metadata["best_fit_config_paths"] = best_fit_paths

    metadata_path = output_dir / f"lhs_metadata_{run_stamp}.json"
    write_json(metadata_path, metadata)

    excel_path = output_dir / f"lhs_summary_{run_stamp}.xlsx"
    _write_excel_summary(excel_path, ranked, calib_space, metadata, args.top_n)

    best = ranked.iloc[0].to_dict() if len(ranked) else {}
    print(f"Ranked CSV: {full_csv}")
    print(f"Excel summary: {excel_path}")
    print(f"Metadata: {metadata_path}")
    if best_fit_paths:
        print(f"Best-fit config: {best_fit_paths[0]}")
    print(f"Source config unchanged: {metadata['source_config_unchanged']}")
    if best:
        print(f"Best draw {int(best['draw_id'])}: total_ll={best['total_ll']}")

    return {
        "ranked_csv": str(full_csv),
        "excel_summary": str(excel_path),
        "metadata": str(metadata_path),
        "normalized_targets": str(normalized_targets_path) if normalized_targets_path else None,
        "best_fit_configs": best_fit_paths,
        "ranked": ranked,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run separate LHS epidemic calibration.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "1980_test.json"))
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=-1)
    parser.add_argument("--output-dir", default=str(ROOT / "outputs" / "calibration" / "lhs"))
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--calib-space", default=str(ROOT / "configs" / "calib_space.json"))
    parser.add_argument("--unaids-data", default=None)
    parser.add_argument("--targets-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    parser.add_argument(
        "--stage",
        choices=["backbone", "diagnosis", "art", "joint", "all"],
        default="backbone",
        help="Calibration stage controlling active target families and sampled parameters.",
    )
    parser.add_argument(
        "--legacy-unaids-targets",
        action="store_true",
        help="Use the old UNAIDS CSV target loader instead of the disease workbook.",
    )
    parser.add_argument(
        "--skip-het-sensitivity",
        action="store_true",
        help="Skip orientation pre-flight sensitivity.",
    )
    parser.add_argument(
        "--stochastic-screen",
        dest="deterministic_calibration",
        action="store_false",
        default=True,
        help="Evaluate LHS screen draws with stochastic simulation instead of deterministic expected flows.",
    )
    parser.add_argument(
        "--family-balanced-score",
        action="store_true",
        help="Rank normalized workbook targets by mean likelihood per target family instead of raw point-sum likelihood.",
    )
    parser.add_argument(
        "--refine-around-input",
        type=float,
        default=0.0,
        help="Narrow each active parameter range around the input config by this fraction of the original range.",
    )
    parser.add_argument(
        "--rerank-top",
        type=int,
        default=0,
        help="Number of initially ranked draws to rerank with stochastic replicate means.",
    )
    parser.add_argument(
        "--rerank-replicates",
        type=int,
        default=1,
        help="Stochastic replicate count for --rerank-top candidates.",
    )
    parser.add_argument(
        "--no-recent-fit-audit",
        dest="recent_fit_audit",
        action="store_false",
        default=True,
        help="Disable V13.5 recent-fit audit columns for normalized target runs.",
    )
    parser.add_argument(
        "--recent-fit-gate",
        action="store_true",
        help="Require ranked finalists to pass broad recent-fit eligibility checks.",
    )
    parser.add_argument(
        "--acceptance-gate-audit",
        action="store_true",
        help="Add V13.6 acceptance-gate columns to each LHS draw.",
    )
    parser.add_argument(
        "--acceptance-gate",
        action="store_true",
        help="Prioritize draws passing all V13.6 acceptance gates before writing best-fit configs.",
    )
    parser.add_argument("--acceptance-diagnosis-floor", type=float, default=0.90)
    parser.add_argument("--acceptance-diagnosis-ceiling", type=float, default=1.50)
    parser.add_argument("--acceptance-plhiv-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument("--acceptance-incidence-recent-ratio-floor", type=float, default=0.50)
    parser.add_argument("--acceptance-incidence-recent-ratio-ceiling", type=float, default=1.75)
    parser.add_argument("--acceptance-aids-bounds-fraction-floor", type=float, default=0.40)
    parser.add_argument("--acceptance-art-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument(
        "--recent-fit-min-observed-fraction",
        type=float,
        default=0.75,
        help="Minimum model/observed fraction for recent diagnosis gates.",
    )
    parser.add_argument(
        "--recent-fit-sigma-multiplier",
        type=float,
        default=3.0,
        help="One-sided lower-tail sigma gate for latest observed incidence.",
    )
    parser.add_argument(
        "--recent-fit-diagnosis-year",
        type=int,
        default=None,
        help="Optional fixed diagnosis gate year. Defaults to latest available target year per diagnosis output.",
    )
    parser.add_argument(
        "--fail-on-inert-sensitivity",
        action="store_true",
        help="Fail if the pre-flight sensitivity check finds an active but inert calibration dimension.",
    )
    parser.add_argument(
        "--simulation-start-year",
        type=int,
        default=None,
        help="Override simulation start year (default: 1980). Use 1975 for historical burn-in.",
    )
    parser.add_argument(
        "--scoring-start-year",
        type=int,
        default=None,
        help="Override scoring/extraction start year (default: same as simulation start). Use 1990 with 1975 burn-in.",
    )
    parser.add_argument(
        "--store-timeseries",
        action="store_true",
        default=False,
        help=(
            "Write full output timeseries columns (indicator_year) into the ranked results CSV. "
            "Required when using LHS outputs as SNPE bootstrap data. "
            "When not set, output is identical to prior behaviour — no regression."
        ),
    )
    args = parser.parse_args(argv)
    if args.n <= 0:
        parser.error("--n must be positive")
    if args.top_n <= 0:
        parser.error("--top-n must be positive")
    if not 0.0 <= args.refine_around_input <= 1.0:
        parser.error("--refine-around-input must be between 0 and 1")
    if args.rerank_top < 0:
        parser.error("--rerank-top must be non-negative")
    if args.rerank_replicates <= 0:
        parser.error("--rerank-replicates must be positive")
    if not 0.0 <= args.recent_fit_min_observed_fraction <= 1.0:
        parser.error("--recent-fit-min-observed-fraction must be between 0 and 1")
    if args.recent_fit_sigma_multiplier <= 0:
        parser.error("--recent-fit-sigma-multiplier must be positive")
    if args.acceptance_gate and not args.acceptance_gate_audit:
        args.acceptance_gate_audit = True
    if not 0.0 <= args.acceptance_diagnosis_floor <= args.acceptance_diagnosis_ceiling:
        parser.error("--acceptance-diagnosis-floor must be non-negative and <= --acceptance-diagnosis-ceiling")
    if not 0.0 <= args.acceptance_aids_bounds_fraction_floor <= 1.0:
        parser.error("--acceptance-aids-bounds-fraction-floor must be between 0 and 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    run_lhs_calibration(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
