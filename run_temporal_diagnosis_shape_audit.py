#!/usr/bin/env python3
"""Paired audit of diagnosis interpolation geometry and bounded post-2019 shape."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_forecast_readiness_confirmation as frc
import run_msm_beta_response_audit as beta_audit
import run_post2019_diagnosis_disruption_audit as post
from coupled_calibration_search import ContractError
from schedule_utils import TimeSchedule

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/temporal_diagnosis_shape_audit_20260921.json"


def _base_block(config: dict) -> dict:
    return config["transmission_model"]["disease"]["diagnosis"]["schedule"]


def _msm_block(config: dict) -> dict:
    return config["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]


def _float_anchors(block: dict) -> dict[float, float]:
    return {float(year): float(value) for year, value in block["value"].items()}


def _key(year: float) -> str:
    return str(int(year)) if float(year).is_integer() else f"{year:.6f}".rstrip("0")


def _transform(value: float, geometry: str) -> float:
    if geometry == "linear_hazard":
        return -math.log1p(-value)
    if geometry == "linear_logit":
        return math.log(value / (1.0 - value))
    if geometry == "log_linear_multiplier":
        return math.log(value)
    return value


def _inverse(value: float, geometry: str) -> float:
    if geometry == "linear_hazard":
        return -math.expm1(-value)
    if geometry == "linear_logit":
        return 1.0 / (1.0 + math.exp(-value))
    if geometry == "log_linear_multiplier":
        return math.exp(value)
    return value


def _densified(anchors: dict[float, float], geometry: str, step: float) -> dict[str, float]:
    years = sorted(anchors)
    out = {_key(year): value for year, value in anchors.items()}
    for left, right in zip(years, years[1:]):
        n = int(round((right - left) / step))
        z0, z1 = _transform(anchors[left], geometry), _transform(anchors[right], geometry)
        for index in range(1, n):
            year = left + index * step
            fraction = (year - left) / (right - left)
            out[_key(year)] = _inverse(z0 + fraction * (z1 - z0), geometry)
    return dict(sorted(out.items(), key=lambda item: float(item[0])))


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text())
    ids = [int(value) for value in plan["candidate_ids"]]
    seeds = [int(value) for value in plan["replicate_seeds"]]
    profiles = [str(value["id"]) for value in plan["profiles"]]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)) or \
            len(profiles) != len(set(profiles)):
        raise ContractError("candidate ids, seeds and profile ids must each be distinct")
    if profiles[0] != "baseline":
        raise ContractError("the first profile must be baseline")
    expected = len(ids) * len(seeds) * len(profiles)
    if int(plan["trajectory_budget"]) != expected:
        raise ContractError(f"trajectory budget {plan['trajectory_budget']} != {expected}")
    valid_base = {"linear_probability", "linear_hazard", "linear_logit", "stepwise_probability"}
    valid_msm = {"linear_multiplier", "log_linear_multiplier", "stepwise_multiplier"}
    contract = plan["profile_contract"]
    for profile in plan["profiles"]:
        if profile["base_geometry"] not in valid_base or profile["msm_geometry"] not in valid_msm:
            raise ContractError(f"unsupported geometry in {profile['id']}")
        factors = {float(year): float(value)
                   for year, value in profile["post2019_factors"].items()}
        if factors:
            if profile["stage"] not in {"post2019_shape", "post2019_shape_boundary"}:
                raise ContractError(f"{profile['id']} adds post-2019 anchors in the wrong stage")
            if min(factors) < float(contract["post2019_first_change_not_before"]):
                raise ContractError(f"{profile['id']} changes before the frozen boundary")
            if factors[min(factors)] != 1.0:
                raise ContractError(f"{profile['id']} must start at the neutral factor")
            if min(factors.values()) < float(contract["post2019_minimum_factor"]) or \
                    max(factors.values()) > float(contract["post2019_maximum_factor"]):
                raise ContractError(f"{profile['id']} exceeds the post-2019 factor bounds")
        elif profile["stage"].startswith("post2019"):
            raise ContractError(f"{profile['id']} is a post-2019 profile without factors")
    return plan


def apply_profile(config: dict, profile: dict, *, quarter_step: float) -> dict:
    out = copy.deepcopy(config)
    base, msm = _base_block(out), _msm_block(out)
    original_base, original_msm = _float_anchors(base), _float_anchors(msm)

    if profile["base_geometry"] == "stepwise_probability":
        base["interpolation"]["value"] = "stepwise"
    elif profile["base_geometry"] != "linear_probability":
        base["value"] = _densified(original_base, profile["base_geometry"], quarter_step)
        base["interpolation"]["value"] = "linear"

    if profile["msm_geometry"] == "stepwise_multiplier":
        msm["interpolation"]["value"] = "stepwise"
    elif profile["msm_geometry"] != "linear_multiplier":
        msm["value"] = _densified(original_msm, profile["msm_geometry"], quarter_step)
        msm["interpolation"]["value"] = "linear"

    if profile["post2019_factors"]:
        anchor_2019 = original_msm[2019.0]
        for year, factor in profile["post2019_factors"].items():
            msm["value"][str(year)] = anchor_2019 * float(factor)

    # Every original numerical anchor is a frozen equality, irrespective of geometry.
    for year, value in original_base.items():
        if float(base["value"][_key(year)]) != value:
            raise ContractError(f"{profile['id']} changed base anchor {year}")
    for year, value in original_msm.items():
        if float(msm["value"][_key(year)]) != value:
            raise ContractError(f"{profile['id']} changed MSM anchor {year}")
    rcc.assert_stochastic_msm_config(out)
    return out


def _context(plan: dict):
    return post._context(plan)


def _schedule_rows(config: dict, candidate_id: int, profile_id: str) -> list[dict]:
    base_block, msm_block = _base_block(config), _msm_block(config)
    base = TimeSchedule(base_block["value"], base_block["interpolation"]["value"],
                        default_before=0.0)
    msm = TimeSchedule(msm_block["value"], msm_block["interpolation"]["value"],
                       default_before=1.0)
    rows = []
    for year in range(1996, 2025):
        b, m = base.get_value(year), msm.get_value(year)
        rows.append({"candidate_id": candidate_id, "profile_id": profile_id,
                     "year": year, "base_probability": b, "msm_multiplier": m,
                     "effective_msm_annual_probability": min(1.0, b * m)})
    return rows


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    seed_check = frc.seed_preflight(plan["replicate_seeds"])
    configurations = []
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        base = rcc.injected_config(prepared["state"]["base_config"], raw["params"], space_json)
        seen = set()
        for profile in plan["profiles"]:
            config = apply_profile(base, profile,
                                   quarter_step=float(plan["profile_contract"]["quarter_step"]))
            digest = ccs.sha256_json(config)
            if digest in seen:
                raise ContractError(f"candidate {cid}: duplicate profile {profile['id']}")
            seen.add(digest)
            configurations.append({"candidate_id": int(cid), "profile_id": profile["id"],
                                   "stage": profile["stage"], "config_sha256": digest})
    return {"ok": True, "design": plan["design"], "candidate_ids": plan["candidate_ids"],
            "profiles": [profile["id"] for profile in plan["profiles"]],
            "replicates": len(plan["replicate_seeds"]),
            "trajectory_budget": plan["trajectory_budget"],
            "source_search_status": search["manifest"]["status"],
            "source_confirmation_status": confirmation["status"],
            "seed_preflight": seed_check, "configurations": configurations,
            "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0}


def _target_map(targets) -> dict[str, float]:
    return {target.key: float(target.z) for target in targets}


def _decision(summary: pd.DataFrame, scores: pd.DataFrame, targets, plan: dict) -> pd.DataFrame:
    diagnosis_years = [1996, 2000, 2004, 2010, 2015, 2019, 2024]
    focus = summary[(summary.series == "new_diagnoses_msm") &
                    summary.year.isin(diagnosis_years)].pivot(
        index=["candidate_id", "profile_id"], columns="year", values="mean").reset_index()
    focus.columns = [column if isinstance(column, str) else f"diagnoses_{column}"
                     for column in focus.columns]
    aids = summary[(summary.series == "aids_first_diagnosis_proxy_msm") &
                   (summary.year == 2022)][["candidate_id", "profile_id", "mean"]].rename(
        columns={"mean": "aids_2022"})
    focus = focus.merge(aids, on=["candidate_id", "profile_id"]).merge(
        scores, on=["candidate_id", "profile_id"])
    stages = {profile["id"]: profile["stage"] for profile in plan["profiles"]}
    focus["stage"] = focus.profile_id.map(stages)
    observed = _target_map(targets)
    squared = []
    for year in diagnosis_years:
        z = observed[f"shm_new_diagnoses_msm@{year}"]
        squared.append((focus[f"diagnoses_{year}"] - z) ** 2)
    focus["diagnosis_rmse"] = np.sqrt(sum(squared) / len(squared))
    focus["level_error_2024"] = (focus.diagnoses_2024 - observed["shm_new_diagnoses_msm@2024"]).abs()
    observed_ratio = observed["shm_new_diagnoses_msm@2024"] / observed["shm_new_diagnoses_msm@2019"]
    focus["ratio_2024_2019"] = focus.diagnoses_2024 / focus.diagnoses_2019
    focus["shape_error"] = (focus.ratio_2024_2019 - observed_ratio).abs()
    focus["aids_2022_error"] = (focus.aids_2022 - observed["rivm_aids_msm@2022"]).abs()
    metrics = ["diagnosis_rmse", "level_error_2024", "shape_error", "aids_2022_error",
               "primary_i_max_diagnostic"]
    baseline = focus[focus.profile_id == "baseline"][["candidate_id"] + metrics].rename(
        columns={metric: f"baseline_{metric}" for metric in metrics})
    focus = focus.merge(baseline, on="candidate_id")
    focus["geometry_useful"] = focus.stage.str.startswith("anchor_preserving") & \
        (focus.diagnosis_rmse < focus.baseline_diagnosis_rmse) & \
        (focus.primary_i_max_diagnostic <= focus.baseline_primary_i_max_diagnostic)
    focus["recent_strict_response"] = focus.stage.str.startswith("post2019") & \
        (focus.level_error_2024 < focus.baseline_level_error_2024) & \
        (focus.shape_error < focus.baseline_shape_error) & \
        (focus.aids_2022_error <= focus.baseline_aids_2022_error)
    return focus


def _pre2020_invariant(paired: pd.DataFrame, plan: dict) -> None:
    recent_ids = {profile["id"] for profile in plan["profiles"]
                  if profile["stage"].startswith("post2019")}
    rows = paired[(paired.profile_id.isin(recent_ids)) & (paired.year <= 2019)]
    tolerance = float(plan["profile_contract"]["pre2020_output_absolute_tolerance"])
    maximum = 0.0 if rows.empty else float(rows.paired_difference.abs().max())
    if maximum > tolerance:
        raise ContractError(f"post-2019 prehistory invariant failed: max difference={maximum}")


def _write_report(path: Path, decision: pd.DataFrame, plan: dict) -> None:
    lines = ["# Temporal diagnosis-shape paired audit", "",
             "Five-seed structural sensitivity; this is not recalibration.", "",
             "| Candidate | Stage | Profile | Dx 2000 | Dx 2010 | Dx 2019 | Dx 2024 | 2024/2019 | AIDS 2022 | Dx RMSE | I_max | Decision |",
             "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    order = {profile["id"]: index for index, profile in enumerate(plan["profiles"])}
    decision = decision.copy()
    decision["profile_order"] = decision.profile_id.map(order)
    for row in decision.sort_values(["candidate_id", "profile_order"]).itertuples(index=False):
        if row.profile_id == "baseline":
            label = "reference"
        elif row.stage.startswith("anchor_preserving"):
            label = "USEFUL" if row.geometry_useful else "FAIL"
        else:
            label = "PASS" if row.recent_strict_response else "FAIL"
        lines.append(f"| {int(row.candidate_id)} | {row.stage} | {row.profile_id} | "
                     f"{row.diagnoses_2000:.1f} | {row.diagnoses_2010:.1f} | "
                     f"{row.diagnoses_2019:.1f} | {row.diagnoses_2024:.1f} | "
                     f"{row.ratio_2024_2019:.3f} | {row.aids_2022:.1f} | "
                     f"{row.diagnosis_rmse:.1f} | {row.primary_i_max_diagnostic:.3f} | {label} |")
    lines += ["", "Observed 2024/2019 diagnosis ratio: 318/368 = 0.864; observed AIDS 2022 = 57.",
              "", "## Decision contract", ""]
    lines.extend(f"- {item}" for item in plan["decision_contract"])
    rcc.atomic_write_text(path / "TEMPORAL_DIAGNOSIS_SHAPE_AUDIT.md", "\n".join(lines) + "\n")


def run(plan_path: Path, output_dir: Path, *, workers: int) -> dict:
    validation, plan = validate(plan_path), load_plan(plan_path)
    source, search, _ = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    state, targets, space = prepared["state"], prepared["state"]["targets"], prepared["state"]["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    rcc.assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rcc.dump_json(output_dir / "frozen_plan.json", plan)
    rcc.dump_json(output_dir / "validation.json", validation)
    payloads, params_by_candidate, schedule_rows, audit_id = [], {}, [], 0
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        params_by_candidate[int(cid)] = raw["params"]
        base = rcc.injected_config(state["base_config"], raw["params"], space_json)
        for profile in plan["profiles"]:
            audit_id += 1
            config = apply_profile(base, profile,
                                   quarter_step=float(plan["profile_contract"]["quarter_step"]))
            schedule_rows.extend(_schedule_rows(config, int(cid), profile["id"]))
            payloads.append({"audit_id": audit_id, "candidate_id": int(cid),
                             "profile_id": profile["id"], "config": config,
                             "config_sha256": ccs.sha256_json(config),
                             "seeds": plan["replicate_seeds"],
                             "years": plan["outputs"]["annual_years"],
                             "series": plan["outputs"]["annual_series"],
                             "target_columns": [target.model_output for target in targets],
                             "target_years": [target.year for target in targets]})
    started, results = time.time(), []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(post.audit_worker, payload) for payload in payloads]
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                raise ContractError(f"audit cell failed: {result['error']}")
            results.append(result)
            rcc.dump_json(output_dir / "raw" /
                          f"candidate_{result['candidate_id']}__{result['profile_id']}.json", result)
            print(f"Temporal audit: {len(results)}/{len(payloads)} cells complete", flush=True)
    results.sort(key=lambda row: int(row["audit_id"]))
    annual, paired, summary, scores = beta_audit._summaries(
        results, plan, targets, space, params_by_candidate)
    _pre2020_invariant(paired, plan)
    decision = _decision(summary, scores, targets, plan)
    annual.to_csv(output_dir / "annual_seed_level.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_level.csv", index=False)
    summary.to_csv(output_dir / "annual_summary.csv", index=False)
    scores.to_csv(output_dir / "history_match_diagnostic.csv", index=False)
    pd.DataFrame(schedule_rows).to_csv(output_dir / "implied_diagnosis_schedules.csv", index=False)
    decision.to_csv(output_dir / "response_decision_table.csv", index=False)
    _write_report(output_dir, decision, plan)
    manifest = {"design": plan["design"], "status": "TEMPORAL_DIAGNOSIS_SHAPE_AUDIT_COMPLETE",
                "n_candidates": len(plan["candidate_ids"]),
                "n_profiles": len(plan["profiles"]), "replicates": len(plan["replicate_seeds"]),
                "trajectories": plan["trajectory_budget"], "failed": 0,
                "geometry_useful_rows": int(decision.geometry_useful.sum()),
                "recent_strict_response_rows": int(decision.recent_strict_response.sum()),
                "elapsed_seconds": time.time() - started,
                "plan_sha256": ccs.sha256_file(plan_path),
                "runner_sha256": ccs.sha256_file(Path(__file__)),
                "runtime_module_sha256": rcc.module_hashes(),
                "decision_contract": plan["decision_contract"],
                "environment": rcc.environment(), "generated_utc": rcc.now()}
    rcc.dump_json(output_dir / "manifest.json", manifest)
    rcc.write_artifact_index(output_dir)
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not 1 <= args.workers <= ccs.MAX_WORKERS:
        raise SystemExit(f"error: --workers must be between 1 and {ccs.MAX_WORKERS}")
    try:
        if args.run:
            if args.output_dir is None:
                raise SystemExit("error: --run needs --output-dir")
            print(json.dumps(run(args.plan, args.output_dir, workers=args.workers), indent=2))
        else:
            print(json.dumps(validate(args.plan), indent=2))
            print("VALIDATION PASSED; 0 engines, 0 simulations, 0 files written.")
        return 0
    except ContractError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
