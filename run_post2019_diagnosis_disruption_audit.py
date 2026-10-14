#!/usr/bin/env python3
"""Strict paired falsification test of a temporary post-2019 MSM diagnosis disruption."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_forecast_readiness_confirmation as frc
import run_recent_mechanism_audit as mechanism
from coupled_calibration_search import ContractError
from schedule_utils import TimeSchedule

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/post2019_diagnosis_disruption_audit_20260921.json"


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
    validate_profiles(plan)
    return plan


def _quarterly_factor_mean(factors: dict[str, float]) -> float:
    schedule = TimeSchedule(factors, interpolation="linear", default_before=1.0)
    years = np.arange(2020.0, 2025.0, 0.25)
    return float(np.mean([schedule.get_value(float(year)) for year in years]))


def validate_profiles(plan: dict) -> None:
    contract = plan["profile_contract"]
    for profile in plan["profiles"]:
        factors = {str(year): float(value) for year, value in profile["factors"].items()}
        if profile["id"] == "baseline":
            if factors:
                raise ContractError("baseline factors must be empty")
            continue
        schedule = TimeSchedule(factors, interpolation="linear", default_before=1.0)
        for year in np.arange(1975.0, 2020.0, 0.25):
            if schedule.get_value(float(year)) != 1.0:
                raise ContractError(f"{profile['id']} is not neutral before 2020 at {year}")
        values = list(factors.values())
        if min(values) < float(contract["minimum_factor"]) or \
                max(values) > float(contract["maximum_factor"]):
            raise ContractError(f"{profile['id']} exceeds the frozen factor bounds")
        if schedule.get_value(float(contract["recovered_by_year"])) != 1.0:
            raise ContractError(f"{profile['id']} has not recovered by 2024")
        tolerance = float(contract["quarterly_2020_2024_mean_factor_tolerance"])
        if abs(_quarterly_factor_mean(factors) - 1.0) > tolerance:
            raise ContractError(f"{profile['id']} is not approximately exposure-balanced")
        if not any(value < 1.0 for value in values) or not any(value > 1.0 for value in values):
            raise ContractError(f"{profile['id']} must contain both a dip and recovery overshoot")


def apply_profile(config: dict, profile: dict) -> dict:
    out = copy.deepcopy(config)
    if profile["id"] == "baseline":
        return out
    block = out["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]
    anchors = block["value"]
    base_2019 = float(anchors["2019"])
    for year, factor in profile["factors"].items():
        anchors[str(year)] = float(base_2019 * float(factor))
    rcc.assert_stochastic_msm_config(out)
    return out


def audit_worker(payload: dict) -> dict:
    started = time.time()
    row = {key: payload[key] for key in
           ("audit_id", "candidate_id", "profile_id", "config_sha256")}
    row["error"] = ""
    try:
        per_seed = []
        for seed in payload["seeds"]:
            trajectory = rcc.run_one_trajectory(payload["config"], int(seed), observe=True)
            annual = {
                name: {str(year): _annual_value(trajectory, name, year)
                       for year in payload["years"]}
                for name in payload["series"]
            }
            target_values = [
                float(trajectory["outputs"][column][year])
                for column, year in zip(payload["target_columns"], payload["target_years"])
            ]
            per_seed.append({"seed": int(seed), "annual": annual,
                             "target_values": target_values,
                             "wall_seconds": trajectory["wall_seconds"]})
        matrix = np.asarray([item["target_values"] for item in per_seed], dtype=float)
        row.update({"per_seed": per_seed, "target_matrix": matrix.tolist(),
                    "means": matrix.mean(axis=0).tolist(),
                    "sample_variances": matrix.var(axis=0, ddof=1).tolist()})
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_seconds"] = time.time() - started
    return row


def _annual_value(trajectory: dict, name: str, year: int) -> float:
    """Read an extracted annual series or a declared year-end diagnostic stock.

    ``calib_extractor`` intentionally exposes only calibration series.  The engine's
    MSM undiagnosed stock is a diagnostic summary column, so derive its year-end
    value locally instead of changing the frozen calibration extractor.
    """
    if name in trajectory["outputs"]:
        return float(trajectory["outputs"][name][year])
    if name != "msm_undiagnosed_hiv_pos":
        raise ContractError(f"annual audit series is unavailable: {name}")
    summary = trajectory["summary"].copy()
    if name not in summary.columns:
        raise ContractError(f"simulation summary lacks diagnostic stock {name}")
    rows = summary[summary["year"].astype(float).apply(int) == int(year)].sort_values("year")
    if rows.empty:
        raise ContractError(f"simulation summary has no rows for diagnostic year {year}")
    return float(rows.iloc[-1][name])


def _context(plan: dict):
    return mechanism.source_context({
        "source_search": plan["source_search"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "source_confirmation": plan["source_confirmation"],
        "source_confirmation_manifest_sha256": plan["source_confirmation_manifest_sha256"],
        "candidate_ids": plan["candidate_ids"],
    })


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    seed_check = frc.seed_preflight([int(seed) for seed in plan["replicate_seeds"]])
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    configurations = []
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        base = rcc.injected_config(prepared["state"]["base_config"], raw["params"], space_json)
        seen = set()
        for profile in plan["profiles"]:
            config = apply_profile(base, profile)
            digest = ccs.sha256_json(config)
            if digest in seen:
                raise ContractError(f"candidate {cid}: profile {profile['id']} changes no config")
            seen.add(digest)
            configurations.append({"candidate_id": int(cid), "profile_id": profile["id"],
                                   "config_sha256": digest})
    return {"ok": True, "design": plan["design"],
            "candidate_ids": plan["candidate_ids"],
            "profiles": [profile["id"] for profile in plan["profiles"]],
            "replicates": len(plan["replicate_seeds"]),
            "trajectory_budget": plan["trajectory_budget"],
            "quarterly_factor_means": {
                profile["id"]: (1.0 if profile["id"] == "baseline" else
                                _quarterly_factor_mean(profile["factors"]))
                for profile in plan["profiles"]},
            "source_search_status": search["manifest"]["status"],
            "source_confirmation_status": confirmation["status"],
            "seed_preflight": seed_check, "configurations": configurations,
            "simulations_started": 0, "engines_constructed": 0,
            "artifacts_written": 0}


def _summaries(results: list[dict], plan: dict, targets, space, params_by_candidate: dict):
    annual_rows, score_rows = [], []
    for result in results:
        cid, profile = int(result["candidate_id"]), result["profile_id"]
        for item in result["per_seed"]:
            for series, values in item["annual"].items():
                for year, value in values.items():
                    annual_rows.append({"candidate_id": cid, "profile_id": profile,
                                        "seed": int(item["seed"]), "year": int(year),
                                        "series": series, "value": float(value)})
        matrix = np.asarray(result["target_matrix"], dtype=float)
        record = ccs.build_record(
            candidate_id=int(result["audit_id"]), batch=-1, origin=profile,
            provenance={"source_candidate_id": cid}, params=params_by_candidate[cid], space=space,
            replicate_seeds=plan["replicate_seeds"], means=matrix.mean(axis=0),
            sample_variances=matrix.var(axis=0, ddof=1), targets=targets,
            replicates=len(plan["replicate_seeds"]),
        )
        score_rows.append({"candidate_id": cid, "profile_id": profile,
                           "primary_i_max_diagnostic": record.primary.i_max,
                           "primary_pass_diagnostic": record.primary_pass,
                           "binding_target": record.primary.binding_target})
    annual = pd.DataFrame(annual_rows)
    baseline = annual[annual.profile_id == "baseline"].rename(
        columns={"value": "baseline_value"})[
        ["candidate_id", "seed", "year", "series", "baseline_value"]]
    paired = annual.merge(baseline, on=["candidate_id", "seed", "year", "series"])
    paired["paired_difference"] = paired.value - paired.baseline_value
    pre = paired[(paired.profile_id != "baseline") & (paired.year <= 2019)]
    tolerance = float(plan["profile_contract"]["pre2020_output_absolute_tolerance"])
    if float(pre.paired_difference.abs().max()) > tolerance:
        bad = pre.loc[pre.paired_difference.abs().idxmax()]
        raise ContractError(
            f"pre-2020 invariant failed for candidate {bad.candidate_id}, "
            f"profile {bad.profile_id}, seed {bad.seed}, {bad.series}@{bad.year}: "
            f"difference={bad.paired_difference}"
        )
    summary = paired.groupby(
        ["candidate_id", "profile_id", "year", "series"], as_index=False
    ).agg(mean=("value", "mean"), sd=("value", "std"),
          baseline_mean=("baseline_value", "mean"),
          mean_paired_difference=("paired_difference", "mean"))
    return annual, paired, summary, pd.DataFrame(score_rows)


def _target_value(targets, key: str) -> float:
    matches = [float(target.z) for target in targets if target.key == key]
    if len(matches) != 1:
        raise ContractError(f"expected one target named {key}, got {len(matches)}")
    return matches[0]


def _decision_table(summary: pd.DataFrame, scores: pd.DataFrame, targets) -> pd.DataFrame:
    keys = ["new_diagnoses_msm", "new_infections_msm",
            "aids_first_diagnosis_proxy_msm", "msm_undiagnosed_hiv_pos"]
    focus = summary[
        summary.series.isin(keys) & summary.year.isin([2019, 2022, 2024])
    ].pivot(index=["candidate_id", "profile_id"], columns=["series", "year"],
            values="mean")
    focus.columns = [f"{series}_{year}" for series, year in focus.columns]
    focus = focus.reset_index().merge(scores, on=["candidate_id", "profile_id"])
    diagnosis_target = _target_value(targets, "shm_new_diagnoses_msm@2024")
    aids_target = _target_value(targets, "rivm_aids_msm@2022")
    base = focus[focus.profile_id == "baseline"][
        ["candidate_id", "new_diagnoses_msm_2024", "aids_first_diagnosis_proxy_msm_2022"]
    ].rename(columns={"new_diagnoses_msm_2024": "baseline_diagnoses_2024",
                      "aids_first_diagnosis_proxy_msm_2022": "baseline_aids_2022"})
    focus = focus.merge(base, on="candidate_id")
    focus["diagnoses_2024_improves"] = (
        (focus.new_diagnoses_msm_2024 - diagnosis_target).abs() <
        (focus.baseline_diagnoses_2024 - diagnosis_target).abs()
    )
    focus["aids_2022_not_worse"] = (
        (focus.aids_first_diagnosis_proxy_msm_2022 - aids_target).abs() <=
        (focus.baseline_aids_2022 - aids_target).abs()
    )
    focus["strict_gate"] = (focus.profile_id != "baseline") & \
        focus.diagnoses_2024_improves & focus.aids_2022_not_worse
    return focus


def _write_report(output_dir: Path, decision: pd.DataFrame, plan: dict) -> None:
    lines = ["# Strict post-2019 diagnosis-disruption audit", "",
             "All values are ten-seed means. Pre-2020 paired outputs were required to be exactly unchanged.", "",
             "| Candidate | Profile | 2019 diagnoses | 2024 diagnoses | 2024 infections | 2022 AIDS | 2024 undiagnosed | I_max | Strict gate |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    order = {profile["id"]: index for index, profile in enumerate(plan["profiles"])}
    decision["profile_order"] = decision.profile_id.map(order)
    for row in decision.sort_values(["candidate_id", "profile_order"]).itertuples(index=False):
        gate = "reference" if row.profile_id == "baseline" else ("PASS" if row.strict_gate else "FAIL")
        lines.append(
            f"| {int(row.candidate_id)} | {row.profile_id} | {row.new_diagnoses_msm_2019:.1f} | "
            f"{row.new_diagnoses_msm_2024:.1f} | {row.new_infections_msm_2024:.1f} | "
            f"{row.aids_first_diagnosis_proxy_msm_2022:.1f} | "
            f"{row.msm_undiagnosed_hiv_pos_2024:.1f} | {row.primary_i_max_diagnostic:.3f} | {gate} |"
        )
    lines += ["", "## Decision contract", ""]
    lines.extend(f"- {item}" for item in plan["decision_contract"])
    rcc.atomic_write_text(output_dir / "POST2019_DIAGNOSIS_DISRUPTION_AUDIT.md",
                          "\n".join(lines) + "\n")


def run(plan_path: Path, output_dir: Path, *, workers: int) -> dict:
    validation = validate(plan_path)
    plan = load_plan(plan_path)
    source, search, _ = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    state = prepared["state"]
    targets, space = state["targets"], state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    rcc.assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rcc.dump_json(output_dir / "frozen_plan.json", plan)
    rcc.dump_json(output_dir / "validation.json", validation)
    payloads, params_by_candidate = [], {}
    audit_id = 0
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        params_by_candidate[int(cid)] = raw["params"]
        base = rcc.injected_config(state["base_config"], raw["params"], space_json)
        for profile in plan["profiles"]:
            audit_id += 1
            config = apply_profile(base, profile)
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
        futures = [pool.submit(audit_worker, payload) for payload in payloads]
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                raise ContractError(f"audit cell failed: {result['error']}")
            results.append(result)
            rcc.dump_json(output_dir / "raw" /
                          f"candidate_{result['candidate_id']}__{result['profile_id']}.json", result)
            print(f"post-2019 audit: {len(results)}/{len(payloads)} cells complete", flush=True)
    results.sort(key=lambda row: int(row["audit_id"]))
    annual, paired, summary, scores = _summaries(
        results, plan, targets, space, params_by_candidate)
    decision = _decision_table(summary, scores, targets)
    annual.to_csv(output_dir / "annual_seed_level.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_level.csv", index=False)
    summary.to_csv(output_dir / "annual_summary.csv", index=False)
    scores.to_csv(output_dir / "history_match_diagnostic.csv", index=False)
    decision.to_csv(output_dir / "strict_decision_table.csv", index=False)
    _write_report(output_dir, decision, plan)
    manifest = {"design": plan["design"], "status": "POST2019_AUDIT_COMPLETE",
                "n_candidates": len(plan["candidate_ids"]),
                "n_profiles": len(plan["profiles"]),
                "replicates": len(plan["replicate_seeds"]),
                "trajectories": plan["trajectory_budget"], "failed": 0,
                "pre2020_invariant": "EXACT_PASS",
                "strict_gate_pass_rows": int(decision.strict_gate.sum()),
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
            result = run(args.plan, args.output_dir, workers=args.workers)
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(validate(args.plan), indent=2))
            print("VALIDATION PASSED; 0 engines, 0 simulations, 0 files written.")
        return 0
    except ContractError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
