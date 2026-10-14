#!/usr/bin/env python3
"""Anchor-preserving paired audit of ART-initiation interpolation geometry."""

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
import run_temporal_diagnosis_shape_audit as temporal
from coupled_calibration_search import ContractError
from schedule_utils import TimeSchedule

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/art_interpolation_shape_audit_20260921.json"


def _art_block(config: dict) -> dict:
    return config["interventions"]["art"]["schedule"]


def _art_anchors(config: dict) -> dict[float, float]:
    return {float(year): float(value) for year, value in
            _art_block(config)["initiation"]["value"].items()}


def _densify_art(anchors: dict[float, float], geometry: str, step: float) -> dict[str, float]:
    years = sorted(anchors)
    out = {temporal._key(year): value for year, value in anchors.items()}
    for left, right in zip(years, years[1:]):
        n = int(round((right - left) / step))
        for index in range(1, n):
            year = left + index * step
            t = (year - left) / (right - left)
            if geometry == "linear_hazard":
                z0, z1 = -math.log1p(-anchors[left]), -math.log1p(-anchors[right])
                value = -math.expm1(-(z0 + t * (z1 - z0)))
            elif geometry == "smoothstep_probability":
                weight = t * t * (3.0 - 2.0 * t)
                value = anchors[left] + weight * (anchors[right] - anchors[left])
            else:
                raise ContractError(f"unsupported ART densification geometry {geometry}")
            out[temporal._key(year)] = value
    return dict(sorted(out.items(), key=lambda item: float(item[0])))


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text())
    ids = [int(value) for value in plan["candidate_ids"]]
    seeds = [int(value) for value in plan["replicate_seeds"]]
    profiles = [profile["id"] for profile in plan["profiles"]]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)) or \
            len(profiles) != len(set(profiles)):
        raise ContractError("candidate ids, seeds and profile ids must each be distinct")
    if profiles[0] != "baseline":
        raise ContractError("the first profile must be baseline")
    if int(plan["trajectory_budget"]) != len(ids) * len(seeds) * len(profiles):
        raise ContractError("trajectory budget does not equal candidates x seeds x profiles")
    valid_art = {"linear_probability", "linear_hazard", "smoothstep_probability",
                 "stepwise_probability"}
    valid_msm = {"linear_multiplier", "log_linear_multiplier"}
    for profile in plan["profiles"]:
        if profile["art_geometry"] not in valid_art or profile["msm_geometry"] not in valid_msm:
            raise ContractError(f"unsupported geometry in {profile['id']}")
    return plan


def apply_profile(config: dict, profile: dict, *, quarter_step: float) -> dict:
    out = copy.deepcopy(config)
    original_art = _art_anchors(out)
    art = _art_block(out)
    if profile["art_geometry"] == "stepwise_probability":
        art["interpolation"]["value"] = "stepwise"
    elif profile["art_geometry"] != "linear_probability":
        art["initiation"]["value"] = _densify_art(
            original_art, profile["art_geometry"], quarter_step)
        art["interpolation"]["value"] = "linear"

    if profile["msm_geometry"] == "log_linear_multiplier":
        msm = temporal._msm_block(out)
        original_msm = temporal._float_anchors(msm)
        msm["value"] = temporal._densified(
            original_msm, "log_linear_multiplier", quarter_step)
        msm["interpolation"]["value"] = "linear"

    for year, value in original_art.items():
        actual = float(art["initiation"]["value"][temporal._key(year)])
        if actual != value:
            raise ContractError(f"{profile['id']} changed ART anchor {year}")
    rcc.assert_stochastic_msm_config(out)
    return out


def _context(plan: dict):
    return post._context(plan)


def _schedule_rows(config: dict, candidate_id: int, profile_id: str) -> list[dict]:
    art = _art_block(config)
    art_schedule = TimeSchedule(art["initiation"]["value"], art["interpolation"]["value"],
                                default_before=0.0)
    diagnosis = temporal._schedule_rows(config, candidate_id, profile_id)
    art_by_year = {year: art_schedule.get_value(year) for year in range(1996, 2025)}
    for row in diagnosis:
        row["art_initiation_probability"] = art_by_year[row["year"]]
    return diagnosis


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
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
                                   "config_sha256": digest})
    return {"ok": True, "design": plan["design"], "candidate_ids": plan["candidate_ids"],
            "profiles": [profile["id"] for profile in plan["profiles"]],
            "replicates": len(plan["replicate_seeds"]),
            "trajectory_budget": plan["trajectory_budget"],
            "source_search_status": search["manifest"]["status"],
            "source_confirmation_status": confirmation["status"],
            "seed_preflight": frc.seed_preflight(plan["replicate_seeds"]),
            "configurations": configurations, "simulations_started": 0,
            "engines_constructed": 0, "artifacts_written": 0}


def _decision(summary: pd.DataFrame, scores: pd.DataFrame, targets) -> pd.DataFrame:
    years = [1996, 2000, 2004, 2010, 2015, 2019, 2024]
    focus = summary[(summary.series == "new_diagnoses_msm") & summary.year.isin(years)].pivot(
        index=["candidate_id", "profile_id"], columns="year", values="mean").reset_index()
    focus.columns = [column if isinstance(column, str) else f"diagnoses_{column}"
                     for column in focus.columns]
    aids = summary[(summary.series == "aids_first_diagnosis_proxy_msm") &
                   (summary.year == 2022)][["candidate_id", "profile_id", "mean"]].rename(
        columns={"mean": "aids_2022"})
    focus = focus.merge(aids, on=["candidate_id", "profile_id"]).merge(
        scores, on=["candidate_id", "profile_id"])
    observed = {target.key: float(target.z) for target in targets}
    focus["diagnosis_rmse"] = np.sqrt(sum(
        (focus[f"diagnoses_{year}"] - observed[f"shm_new_diagnoses_msm@{year}"]) ** 2
        for year in years) / len(years))
    focus["aids_2022_error"] = (focus.aids_2022 - observed["rivm_aids_msm@2022"]).abs()
    focus["level_error_2024"] = (focus.diagnoses_2024 - observed["shm_new_diagnoses_msm@2024"]).abs()
    baseline = focus[focus.profile_id == "baseline"][[
        "candidate_id", "diagnosis_rmse", "aids_2022_error", "primary_i_max_diagnostic"
    ]].rename(columns={"diagnosis_rmse": "baseline_diagnosis_rmse",
                       "aids_2022_error": "baseline_aids_2022_error",
                       "primary_i_max_diagnostic": "baseline_i_max"})
    focus = focus.merge(baseline, on="candidate_id")
    focus["useful_response"] = (focus.profile_id != "baseline") & \
        (focus.diagnosis_rmse < focus.baseline_diagnosis_rmse) & \
        (focus.primary_i_max_diagnostic <= focus.baseline_i_max) & \
        (focus.aids_2022_error <= focus.baseline_aids_2022_error)
    return focus


def _write_report(path: Path, decision: pd.DataFrame, plan: dict) -> None:
    lines = ["# ART interpolation-shape paired audit", "",
             "Five-seed anchor-preserving structural sensitivity; this is not recalibration.", "",
             "| Candidate | Profile | Dx 2000 | Dx 2010 | Dx 2019 | Dx 2024 | AIDS 2022 | Dx RMSE | I_max | Decision |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    order = {profile["id"]: index for index, profile in enumerate(plan["profiles"])}
    decision = decision.copy()
    decision["profile_order"] = decision.profile_id.map(order)
    for row in decision.sort_values(["candidate_id", "profile_order"]).itertuples(index=False):
        label = "reference" if row.profile_id == "baseline" else \
            ("USEFUL" if row.useful_response else "FAIL")
        lines.append(f"| {int(row.candidate_id)} | {row.profile_id} | {row.diagnoses_2000:.1f} | "
                     f"{row.diagnoses_2010:.1f} | {row.diagnoses_2019:.1f} | "
                     f"{row.diagnoses_2024:.1f} | {row.aids_2022:.1f} | "
                     f"{row.diagnosis_rmse:.1f} | {row.primary_i_max_diagnostic:.3f} | {label} |")
    lines += ["", "## Decision contract", ""]
    lines.extend(f"- {item}" for item in plan["decision_contract"])
    rcc.atomic_write_text(path / "ART_INTERPOLATION_SHAPE_AUDIT.md", "\n".join(lines) + "\n")


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
            print(f"ART interpolation audit: {len(results)}/{len(payloads)} cells complete", flush=True)
    results.sort(key=lambda row: int(row["audit_id"]))
    annual, paired, summary, scores = beta_audit._summaries(
        results, plan, targets, space, params_by_candidate)
    decision = _decision(summary, scores, targets)
    annual.to_csv(output_dir / "annual_seed_level.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_level.csv", index=False)
    summary.to_csv(output_dir / "annual_summary.csv", index=False)
    scores.to_csv(output_dir / "history_match_diagnostic.csv", index=False)
    pd.DataFrame(schedule_rows).to_csv(output_dir / "implied_schedules.csv", index=False)
    decision.to_csv(output_dir / "response_decision_table.csv", index=False)
    _write_report(output_dir, decision, plan)
    manifest = {"design": plan["design"], "status": "ART_INTERPOLATION_SHAPE_AUDIT_COMPLETE",
                "n_candidates": len(plan["candidate_ids"]), "n_profiles": len(plan["profiles"]),
                "replicates": len(plan["replicate_seeds"]), "trajectories": plan["trajectory_budget"],
                "failed": 0, "useful_response_rows": int(decision.useful_response.sum()),
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
