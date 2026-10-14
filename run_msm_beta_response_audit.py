#!/usr/bin/env python3
"""Paired local response audit for the two active MSM anal-route transmission betas."""

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
import run_post2019_diagnosis_disruption_audit as post
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/msm_beta_response_audit_20260921.json"


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
    for profile in plan["profiles"]:
        for key in ("receptive_factor", "insertive_factor"):
            value = float(profile[key])
            if not 0.90 <= value <= 1.10:
                raise ContractError(f"{profile['id']} {key}={value} is outside local audit bounds")
    return plan


def _context(plan: dict):
    return post._context(plan)


def _beta_blocks(config: dict) -> tuple[dict, dict]:
    routes = config["transmission_model"]["disease"]["transmission"][
        "advanced"]["beta_per_route"]
    return routes["anal_receptive"]["chronic"], routes["anal_insertive"]["chronic"]


def apply_profile(config: dict, profile: dict) -> dict:
    out = copy.deepcopy(config)
    receptive, insertive = _beta_blocks(out)
    for block, factor in ((receptive, profile["receptive_factor"]),
                          (insertive, profile["insertive_factor"])):
        value = float(block["value"]) * float(factor)
        lower, upper = map(float, block["bounds"])
        if not lower <= value <= upper:
            raise ContractError(
                f"{profile['id']} beta {value} lies outside literature bounds [{lower}, {upper}]"
            )
        block["value"] = value
    rcc.assert_stochastic_msm_config(out)
    return out


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    seed_check = frc.seed_preflight(plan["replicate_seeds"])
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
                raise ContractError(f"candidate {cid}: duplicate beta profile {profile['id']}")
            seen.add(digest)
            receptive, insertive = _beta_blocks(config)
            configurations.append({
                "candidate_id": int(cid), "profile_id": profile["id"],
                "anal_receptive_beta": receptive["value"],
                "anal_insertive_beta": insertive["value"], "config_sha256": digest,
            })
    return {"ok": True, "design": plan["design"], "candidate_ids": plan["candidate_ids"],
            "profiles": [profile["id"] for profile in plan["profiles"]],
            "replicates": len(plan["replicate_seeds"]),
            "trajectory_budget": plan["trajectory_budget"],
            "source_search_status": search["manifest"]["status"],
            "source_confirmation_status": confirmation["status"],
            "seed_preflight": seed_check, "configurations": configurations,
            "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0}


def _summaries(results, plan, targets, space, params_by_candidate):
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
    summary = paired.groupby(
        ["candidate_id", "profile_id", "year", "series"], as_index=False
    ).agg(mean=("value", "mean"), sd=("value", "std"),
          baseline_mean=("baseline_value", "mean"),
          mean_paired_difference=("paired_difference", "mean"))
    return annual, paired, summary, pd.DataFrame(score_rows)


def _target(targets, key: str) -> float:
    values = [float(target.z) for target in targets if target.key == key]
    if len(values) != 1:
        raise ContractError(f"expected exactly one target {key}")
    return values[0]


def _decision(summary, scores, targets):
    focus = summary[
        (summary.series == "new_diagnoses_msm") & summary.year.isin([2000, 2010, 2015, 2019, 2024])
    ].pivot(index=["candidate_id", "profile_id"], columns="year", values="mean").reset_index()
    focus.columns = [str(column) if isinstance(column, str) else f"diagnoses_{column}"
                     for column in focus.columns]
    focus = focus.rename(columns={"candidate_id": "candidate_id", "profile_id": "profile_id"})
    focus = focus.merge(scores, on=["candidate_id", "profile_id"])
    observed_2019 = _target(targets, "shm_new_diagnoses_msm@2019")
    observed_2024 = _target(targets, "shm_new_diagnoses_msm@2024")
    observed_ratio = observed_2024 / observed_2019
    focus["diagnosis_ratio_2024_2019"] = focus.diagnoses_2024 / focus.diagnoses_2019
    focus["shape_error"] = (focus.diagnosis_ratio_2024_2019 - observed_ratio).abs()
    focus["level_error_2024"] = (focus.diagnoses_2024 - observed_2024).abs()
    base = focus[focus.profile_id == "baseline"][
        ["candidate_id", "shape_error", "level_error_2024", "primary_i_max_diagnostic"]
    ].rename(columns={"shape_error": "baseline_shape_error",
                      "level_error_2024": "baseline_level_error_2024",
                      "primary_i_max_diagnostic": "baseline_i_max"})
    focus = focus.merge(base, on="candidate_id")
    focus["shape_improves"] = focus.shape_error < focus.baseline_shape_error
    focus["level_2024_improves"] = focus.level_error_2024 < focus.baseline_level_error_2024
    focus["i_max_not_worse"] = focus.primary_i_max_diagnostic <= focus.baseline_i_max
    focus["strict_response"] = (focus.profile_id != "baseline") & focus.shape_improves & \
        focus.level_2024_improves & focus.i_max_not_worse
    return focus


def _write_report(path: Path, decision: pd.DataFrame, plan: dict) -> None:
    lines = ["# MSM beta paired response audit", "",
             "Five-seed paired local sensitivity; this is not recalibration.", "",
             "| Candidate | Profile | Dx 2000 | Dx 2015 | Dx 2019 | Dx 2024 | 2024/2019 | I_max | Strict response |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    order = {profile["id"]: i for i, profile in enumerate(plan["profiles"])}
    decision["profile_order"] = decision.profile_id.map(order)
    for row in decision.sort_values(["candidate_id", "profile_order"]).itertuples(index=False):
        label = "reference" if row.profile_id == "baseline" else \
            ("PASS" if row.strict_response else "FAIL")
        lines.append(f"| {int(row.candidate_id)} | {row.profile_id} | {row.diagnoses_2000:.1f} | "
                     f"{row.diagnoses_2015:.1f} | {row.diagnoses_2019:.1f} | "
                     f"{row.diagnoses_2024:.1f} | {row.diagnosis_ratio_2024_2019:.3f} | "
                     f"{row.primary_i_max_diagnostic:.3f} | {label} |")
    lines += ["", "Observed 2024/2019 diagnosis ratio: 318/368 = 0.864.", "",
              "## Decision contract", ""]
    lines.extend(f"- {item}" for item in plan["decision_contract"])
    rcc.atomic_write_text(path / "MSM_BETA_RESPONSE_AUDIT.md", "\n".join(lines) + "\n")


def run(plan_path: Path, output_dir: Path, *, workers: int) -> dict:
    validation = validate(plan_path)
    plan = load_plan(plan_path)
    source, search, _ = _context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    state, targets, space = prepared["state"], prepared["state"]["targets"], prepared["state"]["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    rcc.assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rcc.dump_json(output_dir / "frozen_plan.json", plan)
    rcc.dump_json(output_dir / "validation.json", validation)
    payloads, params_by_candidate, audit_id = [], {}, 0
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
        futures = [pool.submit(post.audit_worker, payload) for payload in payloads]
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                raise ContractError(f"audit cell failed: {result['error']}")
            results.append(result)
            rcc.dump_json(output_dir / "raw" /
                          f"candidate_{result['candidate_id']}__{result['profile_id']}.json", result)
            print(f"MSM beta audit: {len(results)}/{len(payloads)} cells complete", flush=True)
    results.sort(key=lambda row: int(row["audit_id"]))
    annual, paired, summary, scores = _summaries(results, plan, targets, space, params_by_candidate)
    decision = _decision(summary, scores, targets)
    annual.to_csv(output_dir / "annual_seed_level.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_level.csv", index=False)
    summary.to_csv(output_dir / "annual_summary.csv", index=False)
    scores.to_csv(output_dir / "history_match_diagnostic.csv", index=False)
    decision.to_csv(output_dir / "response_decision_table.csv", index=False)
    _write_report(output_dir, decision, plan)
    manifest = {"design": plan["design"], "status": "MSM_BETA_AUDIT_COMPLETE",
                "n_candidates": len(plan["candidate_ids"]), "n_profiles": len(plan["profiles"]),
                "replicates": len(plan["replicate_seeds"]),
                "trajectories": plan["trajectory_budget"], "failed": 0,
                "strict_response_rows": int(decision.strict_response.sum()),
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
