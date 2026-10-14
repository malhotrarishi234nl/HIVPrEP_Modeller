#!/usr/bin/env python3
"""Paired audit of recent PrEP, ART and MSM-diagnosis mechanisms.

The audit changes one declared mechanism at a time around two stable confirmed
calibration candidates.  It is explanatory and cannot promote a variant into a
calibrated or HTA-ready baseline.
"""

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
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/recent_mechanism_audit_20260921.json"


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text())
    ids = [int(cid) for cid in plan["candidate_ids"]]
    seeds = [int(seed) for seed in plan["replicate_seeds"]]
    variants = [str(item["id"]) for item in plan["variants"]]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)) \
            or len(variants) != len(set(variants)):
        raise ContractError("candidate ids, seeds and variant ids must each be distinct")
    expected = len(ids) * len(seeds) * len(variants)
    if int(plan["trajectory_budget"]) != expected:
        raise ContractError(f"trajectory budget {plan['trajectory_budget']} != {expected}")
    if variants[0] != "baseline":
        raise ContractError("the first variant must be the paired baseline")
    return plan


def source_context(plan: dict) -> tuple[Path, dict, dict]:
    search_path = ROOT / plan["source_search"]
    search = rcc.load_complete_search(search_path)
    if ccs.sha256_file(search_path / "manifest.json") != plan["source_manifest_sha256"]:
        raise ContractError("source search manifest differs from the frozen audit plan")
    confirmation_path = ROOT / plan["source_confirmation"]
    confirmation = json.loads((confirmation_path / "manifest.json").read_text())
    if ccs.sha256_file(confirmation_path / "manifest.json") != \
            plan["source_confirmation_manifest_sha256"]:
        raise ContractError("source confirmation manifest differs from the frozen audit plan")
    ids = set(int(cid) for cid in plan["candidate_ids"])
    confirmed = set(int(cid) for cid in confirmation["confirmed_primary_pass_ids"])
    unstable = set(int(cid) for cid in
                   confirmation["variance_stability"]["candidate_ids_variance_unstable"])
    if not ids <= confirmed or ids & unstable:
        raise ContractError(
            f"audit candidates must be confirmed stable passers; confirmed={sorted(confirmed)}, "
            f"unstable={sorted(unstable)}, requested={sorted(ids)}"
        )
    return search_path, search, confirmation


def _set_enabled(container: dict, value: bool) -> None:
    if isinstance(container.get("enabled"), dict):
        container["enabled"]["value"] = bool(value)
    else:
        container["enabled"] = bool(value)


def _historical_prep_rates(config: dict) -> dict:
    return config["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]


def apply_variant(config: dict, variant_id: str) -> dict:
    """Apply exactly one frozen mechanism change to an already injected config."""
    out = copy.deepcopy(config)
    prep = out["prep_schedules"]["Oral_PrEP"]
    corridor = prep["advanced"]["historical_stock_corridor"]
    rates = _historical_prep_rates(out)

    if variant_id == "baseline":
        return out
    if variant_id == "prep_corridor_off":
        _set_enabled(corridor, False)
    elif variant_id == "prep_floor_only":
        for year in list(rates):
            if int(year) <= 2025:
                rates[year] = 0.0
    elif variant_id == "prep_half_rate_corridor_off":
        _set_enabled(corridor, False)
        for year in list(rates):
            if int(year) <= 2025:
                rates[year] = 0.5 * float(rates[year])
    elif variant_id == "prep_none_historical":
        _set_enabled(corridor, False)
        for year in list(rates):
            if int(year) <= 2025:
                rates[year] = 0.0
    elif variant_id == "art_transmission_reduction_0_80":
        out["transmission_model"]["disease"]["advanced"][
            "art_transmission_reduction"
        ]["value"] = 0.80
    elif variant_id == "art_recent_initiation_0_75x":
        anchors = out["interventions"]["art"]["schedule"]["initiation"]["value"]
        for year in ("2015", "2020"):
            anchors[year] = 0.75 * float(anchors[year])
    elif variant_id in {
        "msm_diagnosis_2019_anchor_0_75x", "msm_diagnosis_2019_anchor_1_25x"
    }:
        factor = 0.75 if "0_75x" in variant_id else 1.25
        anchors = out["transmission_model"]["disease"]["diagnosis"][
            "msm_relative_diagnosis"
        ]["schedule"]["value"]
        anchors["2019"] = float(np.clip(factor * float(anchors["2019"]), 0.0, 2.0))
    else:
        raise ContractError(f"unknown audit variant {variant_id!r}")
    rcc.assert_stochastic_msm_config(out)
    return out


def audit_worker(payload: dict) -> dict:
    started = time.time()
    row = {key: payload[key] for key in
           ("audit_id", "candidate_id", "variant_id", "config_sha256")}
    row["error"] = ""
    try:
        per_seed = []
        for seed in payload["seeds"]:
            trajectory = rcc.run_one_trajectory(payload["config"], int(seed), observe=True)
            annual = {
                name: {str(year): float(trajectory["outputs"][name][year])
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


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation = source_context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    seeds = [int(seed) for seed in plan["replicate_seeds"]]
    seed_check = frc.seed_preflight(seeds)
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    config_hashes = []
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        base = rcc.injected_config(prepared["state"]["base_config"], raw["params"], space_json)
        seen = set()
        for variant in plan["variants"]:
            config = apply_variant(base, variant["id"])
            digest = ccs.sha256_json(config)
            if digest in seen:
                raise ContractError(f"candidate {cid}: variant {variant['id']} changes no config")
            seen.add(digest)
            config_hashes.append({"candidate_id": int(cid), "variant_id": variant["id"],
                                  "config_sha256": digest})
    return {"ok": True, "design": plan["design"],
            "candidate_ids": [int(cid) for cid in plan["candidate_ids"]],
            "variants": [item["id"] for item in plan["variants"]],
            "replicates": len(seeds), "trajectory_budget": plan["trajectory_budget"],
            "source_search_status": search["manifest"]["status"],
            "source_confirmation_status": confirmation["status"],
            "prepared_status": prepared["manifest"]["status"],
            "seed_preflight": seed_check, "configurations": config_hashes,
            "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0}


def _summaries(results: list[dict], plan: dict, targets, space, params_by_candidate: dict):
    annual_rows = []
    score_rows = []
    for result in results:
        cid, variant = int(result["candidate_id"]), result["variant_id"]
        for item in result["per_seed"]:
            for series, values in item["annual"].items():
                for year, value in values.items():
                    annual_rows.append({"candidate_id": cid, "variant_id": variant,
                                        "seed": int(item["seed"]), "year": int(year),
                                        "series": series, "value": float(value)})
        matrix = np.asarray(result["target_matrix"], dtype=float)
        record = ccs.build_record(
            candidate_id=int(result["audit_id"]), batch=-1, origin=variant,
            provenance={"source_candidate_id": cid}, params=params_by_candidate[cid], space=space,
            replicate_seeds=plan["replicate_seeds"], means=matrix.mean(axis=0),
            sample_variances=matrix.var(axis=0, ddof=1), targets=targets,
            replicates=len(plan["replicate_seeds"]),
        )
        score_rows.append({"candidate_id": cid, "variant_id": variant,
                           "primary_i_max_diagnostic": record.primary.i_max,
                           "primary_pass_diagnostic": record.primary_pass,
                           "binding_target": record.primary.binding_target})
    annual = pd.DataFrame(annual_rows)
    baseline = annual[annual.variant_id == "baseline"].rename(
        columns={"value": "baseline_value"})[
        ["candidate_id", "seed", "year", "series", "baseline_value"]]
    paired = annual.merge(baseline, on=["candidate_id", "seed", "year", "series"])
    paired["paired_difference"] = paired["value"] - paired["baseline_value"]
    summary = paired.groupby(["candidate_id", "variant_id", "year", "series"], as_index=False).agg(
        mean=("value", "mean"), sd=("value", "std"),
        baseline_mean=("baseline_value", "mean"),
        mean_paired_difference=("paired_difference", "mean"),
    )
    return annual, paired, summary, pd.DataFrame(score_rows)


def _write_report(output_dir: Path, summary: pd.DataFrame, scores: pd.DataFrame,
                  plan: dict) -> None:
    focus = summary[(summary.year == 2024) & summary.series.isin(
        ["new_infections_msm", "new_diagnoses_msm", "msm_on_oral_mean"]
    )].pivot(index=["candidate_id", "variant_id"], columns="series", values="mean").reset_index()
    focus = focus.merge(scores, on=["candidate_id", "variant_id"])
    lines = ["# Recent-mechanism paired audit", "",
             "All values are ten-seed means. Differences are explanatory paired model responses, "
             "not calibrated alternatives or policy scenarios.", "",
             "| Candidate | Variant | 2024 infections MSM | 2024 diagnoses MSM | "
             "2024 oral-PrEP mean stock | Diagnostic I_max |", "|---:|---|---:|---:|---:|---:|"]
    order = {variant["id"]: idx for idx, variant in enumerate(plan["variants"])}
    focus["variant_order"] = focus.variant_id.map(order)
    for row in focus.sort_values(["candidate_id", "variant_order"]).itertuples(index=False):
        lines.append(f"| {int(row.candidate_id)} | {row.variant_id} | "
                     f"{row.new_infections_msm:.1f} | {row.new_diagnoses_msm:.1f} | "
                     f"{row.msm_on_oral_mean:.1f} | {row.primary_i_max_diagnostic:.3f} |")
    lines += ["", "## Interpretation contract", ""]
    lines.extend(f"- {item}" for item in plan["interpretation_contract"])
    rcc.atomic_write_text(output_dir / "RECENT_MECHANISM_AUDIT.md", "\n".join(lines) + "\n")


def run(plan_path: Path, output_dir: Path, *, workers: int) -> dict:
    validation = validate(plan_path)
    plan = load_plan(plan_path)
    source, search, _ = source_context(plan)
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
        for variant in plan["variants"]:
            audit_id += 1
            config = apply_variant(base, variant["id"])
            payloads.append({"audit_id": audit_id, "candidate_id": int(cid),
                             "variant_id": variant["id"], "config": config,
                             "config_sha256": ccs.sha256_json(config),
                             "seeds": list(plan["replicate_seeds"]),
                             "years": list(plan["outputs"]["annual_years"]),
                             "series": list(plan["outputs"]["annual_series"]),
                             "target_columns": [target.model_output for target in targets],
                             "target_years": [target.year for target in targets]})
    started = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit_worker, payload): payload for payload in payloads}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                raise ContractError(f"audit cell failed: {result['error']}")
            results.append(result)
            rcc.dump_json(output_dir / "raw" /
                          f"candidate_{result['candidate_id']}__{result['variant_id']}.json", result)
            print(f"recent mechanism audit: {len(results)}/{len(payloads)} cells complete", flush=True)
    results.sort(key=lambda row: int(row["audit_id"]))
    annual, paired, summary, scores = _summaries(
        results, plan, targets, space, params_by_candidate)
    annual.to_csv(output_dir / "annual_seed_level.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_level.csv", index=False)
    summary.to_csv(output_dir / "annual_summary.csv", index=False)
    scores.to_csv(output_dir / "history_match_diagnostic.csv", index=False)
    _write_report(output_dir, summary, scores, plan)
    manifest = {"design": plan["design"], "status": "MECHANISM_AUDIT_COMPLETE",
                "n_candidates": len(plan["candidate_ids"]),
                "n_variants": len(plan["variants"]),
                "replicates": len(plan["replicate_seeds"]),
                "trajectories": int(plan["trajectory_budget"]),
                "failed": 0, "elapsed_seconds": time.time() - started,
                "source_search_manifest_sha256": plan["source_manifest_sha256"],
                "source_confirmation_manifest_sha256":
                    plan["source_confirmation_manifest_sha256"],
                "plan_sha256": ccs.sha256_file(plan_path),
                "runner_sha256": ccs.sha256_file(Path(__file__)),
                "runtime_module_sha256": rcc.module_hashes(),
                "interpretation_contract": plan["interpretation_contract"],
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
            print(json.dumps({key: result[key] for key in
                              ("status", "n_candidates", "n_variants", "trajectories",
                               "failed", "elapsed_seconds")}, indent=2))
        else:
            print(json.dumps(validate(args.plan), indent=2))
            print("VALIDATION PASSED; 0 engines, 0 simulations, 0 files written.")
        return 0
    except ContractError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
