#!/usr/bin/env python3
"""Fresh-seed validation of forecast-aligned candidates from the completed search.

This is deliberately separate from ``run_coupled_calibration.py --confirm``.  It
does not amend that completed confirmation.  It freezes a new, transparent
candidate-selection rule based on the already-completed discovery archive and
tests the selected vectors on an unused seed matrix.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/forecast_readiness_confirmation_20260921.json"


def _load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text())
    required = {
        "design", "source_search", "source_manifest_sha256", "source_artifact_sha256",
        "selection_rule", "selected_candidate_ids", "confirmation_rule",
        "replicate_seeds", "trajectory_budget",
    }
    missing = sorted(required - set(plan))
    if missing:
        raise ContractError(f"forecast plan lacks required fields: {missing}")
    seeds = [int(seed) for seed in plan["replicate_seeds"]]
    ids = [int(cid) for cid in plan["selected_candidate_ids"]]
    if len(seeds) != len(set(seeds)) or len(ids) != len(set(ids)):
        raise ContractError("candidate ids and replicate seeds must each be distinct")
    if int(plan["trajectory_budget"]) != len(seeds) * len(ids):
        raise ContractError("trajectory budget does not equal candidates x replicate seeds")
    return plan


def _source_paths(plan: dict) -> tuple[Path, dict]:
    source = ROOT / plan["source_search"]
    search = rcc.load_complete_search(source)
    if ccs.sha256_file(source / "manifest.json") != plan["source_manifest_sha256"]:
        raise ContractError("source search manifest differs from the frozen forecast plan")
    for name, expected in plan["source_artifact_sha256"].items():
        path = source / name
        if not path.is_file() or ccs.sha256_file(path) != expected:
            raise ContractError(f"source artifact differs from the frozen plan: {name}")
    return source, search


def derive_selection(source: Path, plan: dict) -> tuple[list[int], pd.DataFrame]:
    """Recompute the frozen selection from source tables; never trust listed ids alone."""
    rule = plan["selection_rule"]
    summary = pd.read_csv(source / "archive_summary.csv").set_index("candidate_id")
    residuals = pd.read_csv(source / "signed_search_residuals.csv")
    boundaries = pd.read_csv(source / "orientation_boundary_diagnostics.csv")
    targets = list(rule["recent_targets"])
    recent = residuals[residuals["target_key"].isin(targets)].copy()
    recent["absolute_relative_mean_error"] = (
        (recent["replicate_mean"] - recent["observed_z"]).abs() / recent["observed_z"]
    )
    errors = recent.pivot(
        index="candidate_id", columns="target_key", values="absolute_relative_mean_error"
    )
    if list(errors.columns.sort_values()) != sorted(targets):
        raise ContractError("the source archive does not resolve both recent targets exactly once")
    close = (errors[targets] <= float(rule["maximum_absolute_relative_mean_error"])).all(axis=1)
    bound = boundaries.groupby("candidate_id")["within_5pct_of_bound"].any()
    eligible = summary[
        summary["discovery_primary_pass"].astype(bool)
        & summary.index.isin(close[close].index)
        & ~summary.index.to_series().map(bound).fillna(True)
    ].sort_values(["search_j", "search_rank"])
    selected = [int(cid) for cid in eligible.index]
    declared = [int(cid) for cid in plan["selected_candidate_ids"]]
    if selected != declared:
        raise ContractError(
            f"recomputed candidate selection {selected} differs from frozen plan {declared}"
        )
    details = eligible.join(errors[targets])
    return selected, details.reset_index()


def seed_preflight(seeds: list[int], outputs_root: Path | None = None) -> dict:
    from run_joint_fit_comparison_20260917 import _seeds_from_obj  # noqa: PLC0415

    root = Path(outputs_root) if outputs_root is not None else ROOT / "outputs"
    found: set[int] = set(rcc.DECLARED_FOREIGN_SEEDS)
    found.update(ccs.DISCOVERY_SEEDS)
    found.update(ccs.CONFIRMATION_SEEDS)
    found.update(ccs.SMOKE_SEEDS)
    found.add(ccs.PROPOSAL_RNG_SEED)
    manifests = 0
    for path in sorted(root.rglob("manifest.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        manifests += 1
        _seeds_from_obj(payload, found)
    overlap = sorted(set(seeds) & found)
    if overlap:
        raise ContractError(f"forecast confirmation seeds have recorded collisions: {overlap}")
    return {"n_manifests_scanned": manifests, "n_recorded_seeds": len(found),
            "reserved": list(seeds), "overlap": overlap}


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = _load_plan(plan_path)
    source, search = _source_paths(plan)
    selected, details = derive_selection(source, plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    seed_check = seed_preflight([int(seed) for seed in plan["replicate_seeds"]])
    for cid in selected:
        raw = rcc._raw_record(source, cid)
        if raw["params_sha256"] != ccs.vector_identity(raw["params"]):
            raise ContractError(f"candidate {cid} parameter identity is invalid")
        if not bool(raw.get("per_seed")) or len(raw["replicate_seeds"]) != ccs.DISCOVERY_REPLICATES:
            raise ContractError(f"candidate {cid} lacks its complete discovery record")
    return {
        "ok": True, "design": plan["design"], "source_status": search["manifest"]["status"],
        "selected_candidate_ids": selected, "n_candidates": len(selected),
        "replicates": len(plan["replicate_seeds"]),
        "trajectory_budget": int(plan["trajectory_budget"]),
        "prepared_status": prepared["manifest"]["status"], "seed_preflight": seed_check,
        "selection": ccs.json_safe(details.to_dict(orient="records")),
        "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0,
    }


def _forecast_rows(records, targets, plan: dict) -> list[dict]:
    by_key = {target.key: i for i, target in enumerate(targets)}
    tolerance = float(plan["selection_rule"]["maximum_absolute_relative_mean_error"])
    keys = list(plan["selection_rule"]["recent_targets"])
    rows = []
    for record in records:
        row = record.summary_row()
        forecast_pass = True
        for key in keys:
            idx = by_key[key]
            observed = float(targets[idx].z)
            mean = float(record.means[idx])
            error = abs(mean - observed) / observed
            suffix = str(targets[idx].year)
            row[f"mean_{suffix}"] = mean
            row[f"observed_{suffix}"] = observed
            row[f"absolute_relative_mean_error_{suffix}"] = error
            row[f"mean_gate_pass_{suffix}"] = error <= tolerance
            forecast_pass = forecast_pass and error <= tolerance
        row["forecast_mean_gate_pass"] = forecast_pass
        row["combined_forecast_readiness_pass"] = bool(record.primary_pass and forecast_pass)
        rows.append(row)
    return rows


def _write_report(output_dir: Path, summary: pd.DataFrame, manifest: dict) -> None:
    ranked = summary.sort_values(
        ["combined_forecast_readiness_pass", "confirmation_primary_i_max"],
        ascending=[False, True],
    )
    lines = [
        "# Forecast-readiness fresh-seed confirmation", "",
        f"Combined passes: **{manifest['n_combined_pass']} of {manifest['n_candidates']}**. ",
        "A combined pass requires the unchanged 13-target primary history-match rule and a ",
        "20-seed mean within 10% of both 2019 and 2024 MSM HIV diagnoses.", "",
        "| Candidate | Primary I_max | Primary pass | 2019 mean | 2019 error | 2024 mean | "
        "2024 error | Forecast gate | Combined pass |",
        "|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for row in ranked.itertuples(index=False):
        lines.append(
            f"| {int(row.candidate_id)} | {row.confirmation_primary_i_max:.3f} | "
            f"{'yes' if row.confirmation_primary_pass else 'no'} | {row.mean_2019:.1f} | "
            f"{100*row.absolute_relative_mean_error_2019:.1f}% | {row.mean_2024:.1f} | "
            f"{100*row.absolute_relative_mean_error_2024:.1f}% | "
            f"{'yes' if row.forecast_mean_gate_pass else 'no'} | "
            f"{'yes' if row.combined_forecast_readiness_pass else 'no'} |"
        )
    lines += ["", "## Interpretation", "",
              "This validation does not alter the completed original confirmation and does not "
              "add a mechanism. A combined passer still requires review of its 2025 state, PrEP, "
              "ART and incidence trajectory before it can become an HTA baseline.", ""]
    rcc.atomic_write_text(output_dir / "FORECAST_READINESS_REPORT.md", "\n".join(lines))


def run(plan_path: Path, output_dir: Path, *, workers: int) -> dict:
    validation = validate(plan_path)
    plan = _load_plan(plan_path)
    source, search = _source_paths(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    state = prepared["state"]
    targets = state["targets"]
    space = state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    selected = validation["selected_candidate_ids"]
    seeds = [int(seed) for seed in plan["replicate_seeds"]]

    rcc.assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rcc.dump_json(output_dir / "frozen_plan.json", plan)
    rcc.dump_json(output_dir / "validation.json", validation)
    proposals = []
    discovery_raw = {}
    for cid in selected:
        raw = rcc._raw_record(source, cid)
        discovery_raw[cid] = raw
        proposals.append({"candidate_id": cid, "batch": -1,
                          "origin": "forecast_readiness_selection", "params": raw["params"],
                          "provenance": {"source_candidate_id": cid,
                                         "source_params_sha256": raw["params_sha256"]}})

    budget = ccs.TrajectoryBudget(cap=int(plan["trajectory_budget"]))
    started = time.time()
    rcc.evaluate_candidates(
        proposals, stage="forecast_readiness_confirmation", output_dir=output_dir,
        base_config=state["base_config"], space_json=space_json, targets=targets,
        seeds=seeds, budget=budget, workers=workers, completed=set(), dispatched=set(),
        worker=rcc.evaluate_candidate_worker, executor_factory=ProcessPoolExecutor,
        clock=time.time, deadline=None, save_final_state=True,
    )
    raw_records = [rcc._raw_record(output_dir, cid) for cid in selected]
    records = []
    for raw in raw_records:
        if [int(seed) for seed in raw["replicate_seeds"]] != seeds:
            raise ContractError(f"candidate {raw['candidate_id']} used the wrong seed matrix")
        records.append(ccs.build_record(
            candidate_id=int(raw["candidate_id"]), batch=-1, origin=str(raw["origin"]),
            provenance=raw.get("provenance", {}), params=raw["params"], space=space,
            replicate_seeds=raw["replicate_seeds"], means=raw["means"],
            sample_variances=raw["sample_variances"], targets=targets, replicates=len(seeds),
        ))

    crosscheck = ccs.repository_acceptance_check(
        {record.candidate_id: record.means for record in records},
        {record.candidate_id: record.sample_variances for record in records}, targets,
        register_path=state["paths"]["target_register"], replicates=len(seeds),
    )
    harness = pd.DataFrame([{"candidate_id": record.candidate_id,
                             "harness_i_max": record.primary.i_max,
                             "harness_primary_pass": record.primary_pass} for record in records])
    merged = crosscheck.merge(harness, on="candidate_id")
    merged["i_max_absolute_difference"] = (
        merged["repository_i_max"] - merged["harness_i_max"]
    ).abs()
    merged.to_csv(output_dir / "repository_acceptance_crosscheck.csv", index=False)
    if not bool((merged["repository_primary_pass"] == merged["harness_primary_pass"]).all()) \
            or float(merged["i_max_absolute_difference"].max()) >= 1e-9:
        raise ContractError("forecast confirmation disagrees with repository acceptance scoring")

    rows = _forecast_rows(records, targets, plan)
    summary = pd.DataFrame(rows).rename(columns={
        "discovery_primary_pass": "confirmation_primary_pass",
        "discovery_primary_i_max": "confirmation_primary_i_max",
        "discovery_primary_binding_target": "confirmation_binding_target",
        "discovery_sensitivity_pass": "confirmation_sensitivity_pass",
        "discovery_sensitivity_i_max": "confirmation_sensitivity_i_max",
    })
    summary.to_csv(output_dir / "forecast_readiness_summary.csv", index=False)
    paired = []
    for record in records:
        discovery = discovery_raw[record.candidate_id]
        for idx, target in enumerate(targets):
            paired.append({"candidate_id": record.candidate_id, "target_key": target.key,
                           "observed_z": target.z, "discovery_mean": discovery["means"][idx],
                           "confirmation_mean": record.means[idx],
                           "confirmation_primary_implausibility": record.primary.implausibility[idx]})
    pd.DataFrame(paired).to_csv(output_dir / "paired_discovery_confirmation.csv", index=False)

    stability, cross_validation = rcc._confirmation_diagnostics(
        output_dir, raw_records, state, targets)
    combined = summary[summary["combined_forecast_readiness_pass"]]
    manifest = {
        "design": plan["design"], "status": "FORECAST_CONFIRMATION_COMPLETE",
        "stage": "forecast_readiness_confirmation", "n_candidates": len(records),
        "n_primary_pass": int(summary["confirmation_primary_pass"].sum()),
        "n_forecast_mean_gate_pass": int(summary["forecast_mean_gate_pass"].sum()),
        "n_combined_pass": int(summary["combined_forecast_readiness_pass"].sum()),
        "combined_pass_ids": [int(cid) for cid in combined["candidate_id"]],
        "budget": budget.as_dict(), "elapsed_seconds": time.time() - started,
        "seeds": seeds, "replicates": len(seeds), "source_search": str(source.resolve()),
        "source_search_manifest_sha256": plan["source_manifest_sha256"],
        "plan_sha256": ccs.sha256_file(plan_path), "runner_sha256": ccs.sha256_file(Path(__file__)),
        "runtime_module_sha256": rcc.module_hashes(), "variance_stability": stability,
        "cross_validation": cross_validation, "environment": rcc.environment(),
        "generated_utc": rcc.now(),
        "limitations": plan["interpretation"],
    }
    rcc.dump_json(output_dir / "manifest.json", manifest)
    _write_report(output_dir, summary, manifest)
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
            print(json.dumps({key: result[key] for key in (
                "status", "n_candidates", "n_primary_pass", "n_forecast_mean_gate_pass",
                "n_combined_pass", "combined_pass_ids")}, indent=2))
        else:
            print(json.dumps(validate(args.plan), indent=2))
            print("VALIDATION PASSED; 0 engines, 0 simulations, 0 files written.")
        return 0
    except ContractError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
