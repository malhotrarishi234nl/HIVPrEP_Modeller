#!/usr/bin/env python3
"""Post-2015 stock-and-flow ledger for the two stable calibration candidates.

The runner installs passive wrappers around production transition methods.  Each
wrapper differences the seven-axis population tensor before and after one
production process.  It therefore attributes changes without altering model
logic, random-number calls, or transition ordering.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/post2015_stock_flow_decomposition_20260921.json"

METRICS = (
    "adult_msm",
    "susceptible_msm",
    "undiagnosed_plhiv_msm",
    "diagnosed_off_art_msm",
    "on_art_plhiv_msm",
    "oral_prep_msm",
    "lenacapavir_msm",
)

PROCESS_METHODS = {
    "epidemic_seed": "_apply_epidemic_seed",
    "disease_progression": "_step_disease_progression",
    "diagnosis": "_step_diagnosis_and_exogenous",
    "art_transition": "_step_art_transitions",
    "lena_waning": "_step_lena_waning_transition",
    "prep_transition": "_step_prep_transitions",
    "hiv_mortality": "_step_hiv_mortality",
    "aging_and_debut": "_step_aging_and_debut",
}


def load_plan(path: Path = DEFAULT_PLAN) -> dict:
    plan = json.loads(Path(path).read_text())
    required = {
        "source_search", "source_manifest_sha256", "source_confirmation",
        "source_confirmation_manifest_sha256", "candidate_ids", "replicate_seeds",
        "first_flow_year", "last_flow_year", "stock_years", "trajectory_budget",
    }
    missing = sorted(required - set(plan))
    if missing:
        raise ContractError(f"stock-flow plan lacks required fields: {missing}")
    ids = [int(value) for value in plan["candidate_ids"]]
    seeds = [int(value) for value in plan["replicate_seeds"]]
    years = [int(value) for value in plan["stock_years"]]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)):
        raise ContractError("candidate ids and seeds must each be distinct")
    if years != list(range(int(plan["first_flow_year"]), int(plan["last_flow_year"]) + 2)):
        raise ContractError("stock_years must be every annual boundary around the flow window")
    if int(plan["trajectory_budget"]) != len(ids) * len(seeds):
        raise ContractError("trajectory_budget must equal candidates x seeds")
    return plan


def source_context(plan: dict) -> tuple[Path, dict, dict, dict]:
    source = ROOT / plan["source_search"]
    search = rcc.load_complete_search(source)
    if ccs.sha256_file(source / "manifest.json") != plan["source_manifest_sha256"]:
        raise ContractError("source search manifest differs from the frozen ledger plan")
    confirmation_path = ROOT / plan["source_confirmation"]
    confirmation = json.loads((confirmation_path / "manifest.json").read_text())
    if ccs.sha256_file(confirmation_path / "manifest.json") != \
            plan["source_confirmation_manifest_sha256"]:
        raise ContractError("source confirmation manifest differs from the frozen ledger plan")
    requested = set(int(cid) for cid in plan["candidate_ids"])
    confirmed = set(int(cid) for cid in confirmation["confirmed_primary_pass_ids"])
    unstable = set(int(cid) for cid in
                   confirmation["variance_stability"]["candidate_ids_variance_unstable"])
    if not requested <= confirmed or requested & unstable:
        raise ContractError(
            f"ledger candidates must be stable confirmed passers; requested={sorted(requested)}, "
            f"confirmed={sorted(confirmed)}, unstable={sorted(unstable)}"
        )
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    return source, search, confirmation, prepared


def _indices(groups) -> dict:
    return {
        "msm": groups.orient_to_idx["MSM"],
        "susceptible": groups.hiv_to_idx["Susceptible"],
        "undiagnosed": groups.diagnosis_states.index("Undiagnosed"),
        "diagnosed": groups.diagnosis_states.index("Diagnosed"),
        "off_art": groups.art_states.index("Not_on_ART"),
        "on_art": groups.art_states.index("On_ART"),
        "oral": groups.prep_to_idx.get("Oral_PrEP"),
        "lena": groups.prep_to_idx.get("Lenacapavir"),
    }


def stock_vector(population: np.ndarray, groups) -> np.ndarray:
    """Return activity x metric counts; linear, so it also accepts signed deltas."""
    p = np.asarray(population, dtype=float)
    if p.ndim != 7:
        raise ContractError(f"expected seven-axis population tensor, got shape {p.shape}")
    i = _indices(groups)
    msm = p[:, i["msm"], :, :, :, :, :]
    # Remaining axes: age, activity, hiv, diagnosis, art, prep.
    out = np.zeros((groups.n_activities, len(METRICS)), dtype=float)
    out[:, 0] = msm.sum(axis=(0, 2, 3, 4, 5))
    out[:, 1] = msm[:, :, i["susceptible"], :, :, :].sum(axis=(0, 2, 3, 4))
    out[:, 2] = msm[:, :, 1:, i["undiagnosed"], :, :].sum(axis=(0, 2, 3, 4))
    out[:, 3] = msm[:, :, 1:, i["diagnosed"], i["off_art"], :].sum(axis=(0, 2, 3))
    out[:, 4] = msm[:, :, 1:, :, i["on_art"], :].sum(axis=(0, 2, 3, 4))
    if i["oral"] is not None:
        out[:, 5] = msm[:, :, i["susceptible"], :, :, i["oral"]].sum(axis=(0, 2, 3))
    if i["lena"] is not None:
        out[:, 6] = msm[:, :, i["susceptible"], :, :, i["lena"]].sum(axis=(0, 2, 3))
    return out


class LedgerRecorder:
    def __init__(self, engine, first_year: int, last_year: int):
        self.engine = engine
        self.first_year = int(first_year)
        self.last_year = int(last_year)
        self.stocks: dict[int, np.ndarray] = {}
        self.flows: dict[tuple[int, str], np.ndarray] = {}

    def capture_stock(self, year: int) -> None:
        if self.first_year <= year <= self.last_year + 1 and year not in self.stocks:
            self.stocks[year] = stock_vector(self.engine.state.population, self.engine.groups)

    def add_delta(self, process: str, before: np.ndarray, after: np.ndarray) -> None:
        year = int(float(self.engine._current_year))
        if not self.first_year <= year <= self.last_year:
            return
        delta = stock_vector(after - before, self.engine.groups)
        key = (year, process)
        self.flows[key] = self.flows.get(key, np.zeros_like(delta)) + delta

    def add_signed_tensor(self, process: str, delta: np.ndarray) -> None:
        year = int(float(self.engine._current_year))
        if not self.first_year <= year <= self.last_year:
            return
        values = stock_vector(delta, self.engine.groups)
        key = (year, process)
        self.flows[key] = self.flows.get(key, np.zeros_like(values)) + values


def _wrap_engine_method(engine, recorder: LedgerRecorder, process: str, method_name: str) -> None:
    original = getattr(engine, method_name)

    def wrapped(*args, **kwargs):
        before = engine.state.population.copy()
        result = original(*args, **kwargs)
        recorder.add_delta(process, before, engine.state.population)
        return result

    setattr(engine, method_name, wrapped)


def install_ledger(engine, first_year: int, last_year: int) -> LedgerRecorder:
    recorder = LedgerRecorder(engine, first_year, last_year)
    for process, method_name in PROCESS_METHODS.items():
        _wrap_engine_method(engine, recorder, process, method_name)

    original_adjustment = engine.demography.apply_population_adjustments

    def adjustment(population, current_year):
        result = original_adjustment(population, current_year)
        recorder.add_delta("population_adjustment", population, result)
        return result

    engine.demography.apply_population_adjustments = adjustment

    original_apply_infections = engine.transmission_engine.apply_infections_to_population

    def apply_infections(population, infections):
        before = population.copy()
        result = original_apply_infections(population, infections)
        recorder.add_delta("transmission", before, result)
        return result

    engine.transmission_engine.apply_infections_to_population = apply_infections

    original_background = engine.demography.calculate_background_mortality

    def background(*args, **kwargs):
        deaths = original_background(*args, **kwargs)
        recorder.add_signed_tensor("background_mortality", -np.asarray(deaths, dtype=float))
        return deaths

    engine.demography.calculate_background_mortality = background

    original_migration = engine.migration.apply

    def migration(*args, **kwargs):
        before = engine.state.population.copy()
        result = original_migration(*args, **kwargs)
        recorder.add_delta("migration", before, engine.state.population)
        return result

    engine.migration.apply = migration

    original_step = engine.step_simulation

    def step(step_number: int):
        year = engine.time.start_year + step_number * engine.time.years_per_step
        rounded = int(round(year))
        if abs(year - rounded) < 1e-9:
            recorder.capture_stock(rounded)
        return original_step(step_number)

    engine.step_simulation = step
    return recorder


def run_trajectory(config: dict, candidate_id: int, seed: int,
                   first_year: int, last_year: int) -> dict:
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(copy.deepcopy(config), seed=int(seed))
        engine.output_writer.enabled = False
        recorder = install_ledger(engine, first_year, last_year)
        result = engine.run(run_number=1)
        recorder.capture_stock(last_year + 1)
    missing = sorted(set(range(first_year, last_year + 2)) - set(recorder.stocks))
    if missing:
        raise ContractError(f"candidate {candidate_id} seed {seed}: missing stocks {missing}")
    rows = []
    activities = list(engine.groups.activities)
    processes = sorted({process for _, process in recorder.flows})
    for year in range(first_year, last_year + 1):
        for ai, activity in enumerate(activities):
            for mi, metric in enumerate(METRICS):
                start = float(recorder.stocks[year][ai, mi])
                end = float(recorder.stocks[year + 1][ai, mi])
                contributions = {
                    process: float(recorder.flows.get(
                        (year, process), np.zeros((len(activities), len(METRICS)))
                    )[ai, mi])
                    for process in processes
                }
                flow_sum = float(sum(contributions.values()))
                rows.append({
                    "candidate_id": int(candidate_id), "seed": int(seed), "year": year,
                    "activity": activity, "metric": metric, "stock_start": start,
                    "stock_end": end, "stock_change": end - start,
                    "flow_sum": flow_sum, "balance_residual": end - start - flow_sum,
                    **{f"delta__{name}": value for name, value in contributions.items()},
                })
    frame = pd.DataFrame(rows)
    max_residual = float(frame["balance_residual"].abs().max())
    if max_residual > 1e-9:
        bad = frame.loc[frame["balance_residual"].abs().idxmax()]
        raise ContractError(
            f"candidate {candidate_id} seed {seed}: ledger fails for "
            f"{bad.metric}/{bad.activity}/{bad.year}; residual={bad.balance_residual}"
        )
    return {
        "candidate_id": int(candidate_id), "seed": int(seed),
        "ledger": frame.to_dict(orient="records"), "max_balance_residual": max_residual,
        "summary_digest": ccs.sha256_json(result["summary"].to_dict(orient="list")),
        "wall_seconds": time.time() - started,
    }


def worker(payload: dict) -> dict:
    return run_trajectory(
        payload["config"], int(payload["candidate_id"]), int(payload["seed"]),
        int(payload["first_year"]), int(payload["last_year"]),
    )


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation, prepared = source_context(plan)
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    configs = []
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        config = rcc.injected_config(prepared["state"]["base_config"], raw["params"], space_json)
        rcc.assert_stochastic_msm_config(config)
        configs.append({"candidate_id": int(cid), "params_sha256": raw["params_sha256"],
                        "config_sha256": ccs.sha256_json(config)})
    return {
        "ok": True, "design": plan["design"], "candidate_ids": plan["candidate_ids"],
        "replicate_seeds": plan["replicate_seeds"],
        "trajectory_budget": plan["trajectory_budget"],
        "flow_years": [plan["first_flow_year"], plan["last_flow_year"]],
        "stock_years": plan["stock_years"], "metrics": list(METRICS),
        "processes": sorted(set(PROCESS_METHODS) | {
            "population_adjustment", "transmission", "background_mortality", "migration"
        }),
        "activity_movement": "not_applicable_no_production_activity_transition_operator",
        "source_search_status": search["manifest"]["status"],
        "source_confirmation_status": confirmation["status"],
        "prepared_status": prepared["manifest"]["status"], "configurations": configs,
        "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0,
    }


def _configs(plan: dict, source: Path, prepared: dict) -> dict[int, dict]:
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    return {
        int(cid): rcc.injected_config(
            prepared["state"]["base_config"], rcc._raw_record(source, int(cid))["params"],
            space_json,
        )
        for cid in plan["candidate_ids"]
    }


def smoke(plan_path: Path = DEFAULT_PLAN) -> dict:
    """One paired observed/unobserved trajectory; hashes must be identical."""
    plan = load_plan(plan_path)
    source, _, _, prepared = source_context(plan)
    cid, seed = int(plan["candidate_ids"][0]), int(plan["replicate_seeds"][0])
    config = _configs(plan, source, prepared)[cid]
    observed = run_trajectory(
        config, cid, seed, int(plan["first_flow_year"]), int(plan["last_flow_year"])
    )
    production = rcc.run_one_trajectory(copy.deepcopy(config), seed, observe=False)
    production_digest = ccs.sha256_json(production["summary"].to_dict(orient="list"))
    if observed["summary_digest"] != production_digest:
        raise ContractError("ledger observer changed the production trajectory")
    return {
        "ok": True, "candidate_id": cid, "seed": seed,
        "observer_summary_digest": observed["summary_digest"],
        "production_summary_digest": production_digest,
        "max_balance_residual": observed["max_balance_residual"],
        "observed_wall_seconds": observed["wall_seconds"],
        "production_wall_seconds": production["wall_seconds"],
    }


def _write_report(output_dir: Path, summary: pd.DataFrame, manifest: dict) -> None:
    focus = summary[
        summary["metric"].eq("undiagnosed_plhiv_msm")
        & summary["year"].isin([2015, 2019, 2024])
    ]
    lines = [
        "# Post-2015 MSM stock-and-flow decomposition", "",
        "This is a descriptive baseline ledger. Every process contribution is the exact "
        "change in the named stock produced by that production operator.", "",
        f"Maximum seed-level balance residual: **{manifest['max_balance_residual']:.3g}**.", "",
        "| Candidate | Year | Activity | Start undiagnosed | End undiagnosed | Annual change |",
        "|---:|---:|---|---:|---:|---:|",
    ]
    for row in focus.sort_values(["candidate_id", "year", "activity"]).itertuples():
        lines.append(
            f"| {int(row.candidate_id)} | {int(row.year)} | {row.activity} | "
            f"{row.stock_start_mean:.1f} | {row.stock_end_mean:.1f} | "
            f"{row.stock_change_mean:+.1f} |"
        )
    lines += ["", "Activity movement: **not applicable**; the production model has no "
              "post-entry activity-transition operator.", ""]
    (output_dir / "POST2015_STOCK_FLOW_DECOMPOSITION.md").write_text("\n".join(lines))


def run(plan_path: Path, output_dir: Path, workers: int) -> dict:
    validation = validate(plan_path)
    plan = load_plan(plan_path)
    source, _, _, prepared = source_context(plan)
    configs = _configs(plan, source, prepared)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ContractError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "frozen_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    (output_dir / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    payloads = [
        {"candidate_id": int(cid), "seed": int(seed), "config": configs[int(cid)],
         "first_year": int(plan["first_flow_year"]),
         "last_year": int(plan["last_flow_year"])}
        for cid in plan["candidate_ids"] for seed in plan["replicate_seeds"]
    ]
    started = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=int(workers)) as pool:
        futures = [pool.submit(worker, payload) for payload in payloads]
        for future in as_completed(futures):
            results.append(future.result())
    seed_level = pd.concat([pd.DataFrame(item["ledger"]) for item in results], ignore_index=True)
    key = ["candidate_id", "year", "activity", "metric"]
    value_columns = [column for column in seed_level if column not in key + ["seed"]]
    summary = seed_level.groupby(key, as_index=False)[value_columns].agg(["mean", "std"])
    summary.columns = ["_".join(filter(None, column)).rstrip("_")
                       for column in summary.columns.to_flat_index()]
    summary = summary.rename(columns={name: name.removesuffix("_mean")
                                      for name in key if f"{name}_mean" in summary.columns})
    seed_level.to_csv(output_dir / "annual_seed_level_ledger.csv", index=False)
    summary.to_csv(output_dir / "annual_mean_ledger.csv", index=False)
    max_residual = float(seed_level["balance_residual"].abs().max())
    manifest = {
        "design": plan["design"], "status": "STOCK_FLOW_DECOMPOSITION_COMPLETE",
        "n_candidates": len(plan["candidate_ids"]),
        "replicates": len(plan["replicate_seeds"]), "trajectories": len(results),
        "max_balance_residual": max_residual,
        "activity_movement": "not_applicable_no_production_activity_transition_operator",
        "elapsed_seconds": time.time() - started,
        "plan_sha256": ccs.sha256_file(plan_path),
        "runner_sha256": ccs.sha256_file(Path(__file__)),
        "environment": {"python": sys.version, "platform": platform.platform()},
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "interpretation_contract": plan["interpretation_contract"],
    }
    _write_report(output_dir, summary, manifest)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/calibration/post2015_stock_flow_decomposition_20260921")
    parser.add_argument("--workers", type=int, default=8)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.run:
        result = run(args.plan, args.output_dir, args.workers)
    elif args.smoke:
        result = smoke(args.plan)
    else:
        result = validate(args.plan)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
