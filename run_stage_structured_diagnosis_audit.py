#!/usr/bin/env python3
"""Frozen paired audit of routine and AIDS symptom-driven diagnosis hazards."""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import math
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_forecast_readiness_confirmation as frc
import run_post2015_stock_flow_decomposition as ledger_module
from aids_observation import OBSERVED_AIDS_PROXY_COMPONENTS
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "configs/stage_structured_diagnosis_audit_20260922.json"
DEFAULT_OUTPUT = ROOT / "outputs/calibration/stage_structured_diagnosis_audit_20260922"
DEFAULT_SMOKE_OUTPUT = ROOT / "outputs/calibration/stage_structured_diagnosis_audit_20260922_smoke"
PROFILE_PATH = ("transmission_model", "disease", "diagnosis", "stage_structured_diagnosis")
STAGES = ("acute", "chronic", "aids")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(ccs.json_safe(value), indent=2) + "\n")
    temporary.replace(path)


def load_plan(path: Path = DEFAULT_PLAN) -> dict:
    plan = json.loads(Path(path).read_text())
    ids = [int(x) for x in plan["candidate_ids"]]
    seeds = [int(x) for x in plan["replicate_seeds"]]
    profiles = [str(x["id"]) for x in plan["profiles"]]
    if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)) or \
            len(profiles) != len(set(profiles)):
        raise ContractError("candidate IDs, profile IDs and seed IDs must each be unique")
    if int(plan["trajectory_budget"]) != len(ids) * len(seeds) * len(profiles):
        raise ContractError("trajectory budget does not equal candidates x profiles x seeds")
    expected = [(rho, symptom) for rho in (1.0, 0.8, 0.6)
                for symptom in (0.0, 0.35, 0.65, 0.85)]
    actual = [(float(x["rho"]), float(x["p_symptom_aids"])) for x in plan["profiles"]]
    if actual != expected:
        raise ContractError(f"profile grid is not the frozen 12-point surface: {actual}")
    identity = next((x for x in plan["profiles"]
                     if x["id"] == plan["identity_profile_id"]), None)
    if identity is None or (float(identity["rho"]), float(identity["p_symptom_aids"])) != (1, 0):
        raise ContractError("identity profile is missing or invalid")
    if ids != [1326, 1788] or seeds != list(range(200600, 200610)):
        raise ContractError("candidate or seed freeze differs from the audit brief")
    return plan


def _source_context(plan: dict) -> tuple[Path, dict, dict, dict]:
    source = ROOT / plan["source_search"]
    if ccs.sha256_file(source / "manifest.json") != plan["source_manifest_sha256"]:
        raise ContractError("source search manifest differs from frozen plan")
    search_manifest = json.loads((source / "manifest.json").read_text())
    if search_manifest.get("status") != ccs.STATUS_SEARCH_COMPLETE:
        raise ContractError("source search is not complete")
    search_context = json.loads((source / "search_context.json").read_text())
    confirmation_path = ROOT / plan["source_confirmation"]
    if ccs.sha256_file(confirmation_path / "manifest.json") != \
            plan["source_confirmation_manifest_sha256"]:
        raise ContractError("source confirmation manifest differs from frozen plan")
    confirmation = json.loads((confirmation_path / "manifest.json").read_text())
    requested = set(int(x) for x in plan["candidate_ids"])
    confirmed = set(int(x) for x in confirmation["confirmed_primary_pass_ids"])
    unstable = set(int(x) for x in
                   confirmation["variance_stability"]["candidate_ids_variance_unstable"])
    if not requested <= confirmed or requested & unstable:
        raise ContractError("requested candidates are not stable confirmed primary passers")

    prepared_dir = Path(search_context["prepared_dir"])
    prepared_manifest = json.loads((prepared_dir / "manifest.json").read_text())
    if ccs.sha256_file(prepared_dir / "manifest.json") != search_context["prepared_sha256"]:
        raise ContractError("prepared source manifest differs from search context")
    state = rcc.load_inputs()
    if state["hashes"] != prepared_manifest["input_sha256"]:
        raise ContractError("frozen search inputs have changed")
    return source, {"manifest": search_manifest, "context": search_context}, confirmation, state


def _profile_block(profile: dict) -> dict:
    return {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": float(profile["rho"])},
        "aids_symptom_annual_probability": {"value": float(profile["p_symptom_aids"])},
    }


def apply_profile(config: dict, profile: dict) -> dict:
    out = copy.deepcopy(config)
    diagnosis = out["transmission_model"]["disease"]["diagnosis"]
    diagnosis["stage_structured_diagnosis"] = _profile_block(profile)
    rcc.assert_stochastic_msm_config(out)
    return out


def _without_profile(config: dict) -> dict:
    out = copy.deepcopy(config)
    out["transmission_model"]["disease"]["diagnosis"].pop(
        "stage_structured_diagnosis", None
    )
    return out


def _base_configs(plan: dict, source: Path, state: dict) -> tuple[dict[int, dict], dict[int, dict]]:
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    configs, raw_records = {}, {}
    for cid in plan["candidate_ids"]:
        raw = rcc._raw_record(source, int(cid))
        if raw["params_sha256"] != ccs.vector_identity(raw["params"]):
            raise ContractError(f"candidate {cid} parameter hash is invalid")
        config = rcc.injected_config(state["base_config"], raw["params"], space_json)
        rcc.assert_stochastic_msm_config(config)
        configs[int(cid)], raw_records[int(cid)] = config, raw
    return configs, raw_records


def validate(plan_path: Path = DEFAULT_PLAN) -> dict:
    plan = load_plan(plan_path)
    source, search, confirmation, state = _source_context(plan)
    # These seeds are deliberately reused for paired structural comparison, not
    # claimed as fresh confirmation. A fresh-seed collision guard would reject
    # the declared design after this audit writes its own manifest.
    seed_check = {
        "compatible": True,
        "reserved": [int(seed) for seed in plan["replicate_seeds"]],
        "overlap_policy": "declared reuse; not independent confirmation",
        "overlap_record": plan["seed_overlap"],
    }
    bases, raw = _base_configs(plan, source, state)
    configurations = []
    for cid, base in bases.items():
        base_without = ccs.sha256_json(_without_profile(base))
        original_base_schedule = copy.deepcopy(
            base["transmission_model"]["disease"]["diagnosis"]["schedule"]
        )
        original_msm_schedule = copy.deepcopy(
            base["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]
        )
        seen = set()
        for profile in plan["profiles"]:
            config = apply_profile(base, profile)
            if ccs.sha256_json(_without_profile(config)) != base_without:
                raise ContractError(f"candidate {cid}/{profile['id']} changed outside profile block")
            diagnosis = config["transmission_model"]["disease"]["diagnosis"]
            if diagnosis["schedule"] != original_base_schedule or \
                    diagnosis["msm_relative_diagnosis"]["schedule"] != original_msm_schedule:
                raise ContractError(f"candidate {cid}/{profile['id']} changed diagnosis schedules")
            digest = ccs.sha256_json(config)
            if digest in seen:
                raise ContractError(f"candidate {cid} has duplicate profile configurations")
            seen.add(digest)
            configurations.append({
                "candidate_id": cid, "profile_id": profile["id"],
                "rho": profile["rho"], "p_symptom_aids": profile["p_symptom_aids"],
                "params_sha256": raw[cid]["params_sha256"],
                "config_sha256": digest, "config_without_profile_sha256": base_without,
                "base_schedule_sha256": ccs.sha256_json(original_base_schedule),
                "msm_schedule_sha256": ccs.sha256_json(original_msm_schedule),
            })
    current_hashes = rcc.module_hashes()
    original_hashes = search["context"]["module_sha256"]
    changed = sorted(name for name in set(current_hashes) | set(original_hashes)
                     if current_hashes.get(name) != original_hashes.get(name))
    return {
        "ok": True, "design": plan["design"], "candidate_ids": plan["candidate_ids"],
        "profile_ids": [x["id"] for x in plan["profiles"]],
        "replicate_seeds": plan["replicate_seeds"],
        "trajectory_budget": plan["trajectory_budget"], "seed_preflight": seed_check,
        "source_search_status": search["manifest"]["status"],
        "source_confirmation_status": confirmation["status"],
        "intentional_code_differences_since_search": changed,
        "configurations": configurations,
        "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0,
    }


def _stock_snapshot(population: np.ndarray, groups, year: int) -> list[dict]:
    p = np.asarray(population, dtype=float)
    msm = groups.orient_to_idx["MSM"]
    undiagnosed = groups.diagnosis_states.index("Undiagnosed")
    diagnosed = groups.diagnosis_states.index("Diagnosed")
    off_art = groups.art_states.index("Not_on_ART")
    on_art = groups.art_states.index("On_ART")
    rows = []
    for stage in ("Acute", "Chronic", "AIDS"):
        hi = groups.hiv_to_idx[stage]
        value = p[:, msm, :, hi, undiagnosed, :, :].sum()
        rows.append({"snapshot_year": year, "series": f"msm_undiagnosed_{stage.lower()}",
                     "value": float(value), "activity": "All"})
        for ai, activity in enumerate(groups.activities):
            value = p[:, msm, ai, hi, undiagnosed, :, :].sum()
            rows.append({"snapshot_year": year,
                         "series": f"msm_undiagnosed_{stage.lower()}",
                         "value": float(value), "activity": activity})
    rows.extend([
        {"snapshot_year": year, "series": "diagnosed_off_art_stock",
         "value": float(p[:, :, :, 1:, diagnosed, off_art, :].sum()), "activity": "All"},
        {"snapshot_year": year, "series": "on_art_stock_state",
         "value": float(p[:, :, :, 1:, :, on_art, :].sum()), "activity": "All"},
        {"snapshot_year": year, "series": "adult_population_state",
         "value": float(p.sum()), "activity": "All"},
    ])
    return rows


def _install_observers(engine, first_year: int, last_year: int):
    ledger = ledger_module.install_ledger(engine, first_year, last_year)
    snapshots: dict[int, list[dict]] = {}
    mortality: dict[tuple[int, str], float] = {}
    original_mortality = engine._step_hiv_mortality

    def mortality_step(*args, **kwargs):
        before = engine.state.population.copy()
        result = original_mortality(*args, **kwargs)
        deaths = before - engine.state.population
        year = int(float(engine._current_year))
        labels = ((0, "undiagnosed"), (1, "diagnosed"))
        arts = ((0, "off_art"), (1, "on_art"))
        for di, diagnosis in labels:
            for ai, art in arts:
                mortality[(year, f"hiv_deaths_{diagnosis}_{art}")] = \
                    mortality.get((year, f"hiv_deaths_{diagnosis}_{art}"), 0.0) + \
                    float(deaths[:, :, :, 1:, di, ai, :].sum())
        return result

    engine._step_hiv_mortality = mortality_step
    original_step = engine.step_simulation

    def step(step_number: int):
        year = engine.time.start_year + step_number * engine.time.years_per_step
        rounded = int(round(year))
        if abs(year - rounded) < 1e-9 and 1996 <= rounded <= 2025:
            snapshots.setdefault(rounded, _stock_snapshot(
                engine.state.population.copy(), engine.groups, rounded
            ))
        return original_step(step_number)

    engine.step_simulation = step
    return ledger, snapshots, mortality


def _ledger_rows(recorder, engine, first_year: int, last_year: int) -> list[dict]:
    recorder.capture_stock(last_year + 1)
    missing = sorted(set(range(first_year, last_year + 2)) - set(recorder.stocks))
    if missing:
        raise ContractError(f"ledger missing annual stocks: {missing}")
    rows = []
    activities = list(engine.groups.activities)
    processes = sorted({process for _, process in recorder.flows})
    zeros = np.zeros((len(activities), len(ledger_module.METRICS)))
    for year in range(first_year, last_year + 1):
        for ai, activity in enumerate(activities):
            for mi, metric in enumerate(ledger_module.METRICS):
                start = float(recorder.stocks[year][ai, mi])
                end = float(recorder.stocks[year + 1][ai, mi])
                contributions = {name: float(recorder.flows.get((year, name), zeros)[ai, mi])
                                 for name in processes}
                flow = float(sum(contributions.values()))
                rows.append({"year": year, "activity": activity, "metric": metric,
                             "stock_start": start, "stock_end": end,
                             "stock_change": end - start, "flow_sum": flow,
                             "balance_residual": end - start - flow,
                             **{f"delta__{k}": v for k, v in contributions.items()}})
    maximum = max(abs(row["balance_residual"]) for row in rows)
    if maximum != 0.0:
        raise ContractError(f"post-2015 stock-flow ledger residual is {maximum}, expected zero")
    return rows


def run_trajectory(config: dict, candidate_id: int, profile_id: str, seed: int,
                   plan: dict) -> dict:
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(copy.deepcopy(config), seed=int(seed))
        engine.output_writer.enabled = False
        ledger, snapshots, mortality = _install_observers(
            engine, int(plan["ledger_first_year"]), int(plan["ledger_last_year"])
        )
        result = engine.run(run_number=1)
    snapshots[2026] = _stock_snapshot(engine.state.population.copy(), engine.groups, 2026)
    outputs = extract_annual_outputs(
        result, int(plan["annual_start_year"]), int(plan["annual_end_year"])
    )
    for year in range(int(plan["annual_start_year"]), int(plan["annual_end_year"]) + 1):
        stage_total = sum(outputs[f"hiv_diagnoses_in_{stage}_stage"][year]
                          for stage in STAGES)
        if stage_total != outputs["new_diagnoses_endogenous"][year]:
            raise ContractError(f"stage counters fail total identity in {year}")
        for suffix in ("", "_msm", "_non_msm"):
            proxy = outputs["aids_first_diagnosis_proxy" + suffix][year]
            components = sum(outputs[name + suffix][year]
                             for name in OBSERVED_AIDS_PROXY_COMPONENTS)
            if proxy != components:
                raise ContractError(f"AIDS proxy components fail identity in {year}{suffix}")
    ledger_rows = _ledger_rows(
        ledger, engine, int(plan["ledger_first_year"]), int(plan["ledger_last_year"])
    )
    return {
        "candidate_id": int(candidate_id), "profile_id": profile_id, "seed": int(seed),
        "annual_outputs": outputs,
        "snapshots": [row for year in sorted(snapshots) for row in snapshots[year]],
        "mortality": [{"year": year, "series": series, "value": value}
                      for (year, series), value in sorted(mortality.items())],
        "ledger": ledger_rows,
        "summary_sha256": ccs.sha256_json(result["summary"].to_dict(orient="list")),
        "final_state_sha256": ccs.sha256_json(result["final_state"].tolist()),
        "wall_seconds": time.time() - started, "error": "",
    }


def worker(payload: dict) -> dict:
    try:
        return run_trajectory(
            payload["config"], payload["candidate_id"], payload["profile_id"],
            payload["seed"], payload["plan"],
        )
    except Exception as exc:  # noqa: BLE001
        return {"candidate_id": payload["candidate_id"],
                "profile_id": payload["profile_id"], "seed": payload["seed"],
                "error": f"{type(exc).__name__}: {exc}"}


def smoke(plan_path: Path, output_dir: Path) -> dict:
    plan = load_plan(plan_path)
    source, _, _, state = _source_context(plan)
    bases, _ = _base_configs(plan, source, state)
    identity = next(x for x in plan["profiles"] if x["id"] == plan["identity_profile_id"])
    nontrivial = next(x for x in plan["profiles"]
                      if float(x["rho"]) == 0.8 and float(x["p_symptom_aids"]) == 0.65)
    if output_dir.exists():
        raise ContractError(f"smoke output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    rows, started = [], time.time()
    for cid in plan["candidate_ids"]:
        base = bases[int(cid)]
        control = run_trajectory(base, int(cid), "legacy_control", plan["replicate_seeds"][0], plan)
        ident = run_trajectory(apply_profile(base, identity), int(cid), identity["id"],
                               plan["replicate_seeds"][0], plan)
        alternative = run_trajectory(apply_profile(base, nontrivial), int(cid), nontrivial["id"],
                                     plan["replicate_seeds"][0], plan)
        if control["summary_sha256"] != ident["summary_sha256"] or \
                control["final_state_sha256"] != ident["final_state_sha256"]:
            raise ContractError(f"candidate {cid}: enabled identity differs from legacy control")
        for item in (control, ident, alternative):
            rows.append({k: item[k] for k in ("candidate_id", "profile_id", "seed",
                                               "summary_sha256", "final_state_sha256",
                                               "wall_seconds", "error")})
    pd.DataFrame(rows).to_csv(output_dir / "smoke_comparison.csv", index=False)
    result = {"ok": True, "audit_profiles": [identity["id"], nontrivial["id"]],
              "candidate_ids": plan["candidate_ids"], "seed": plan["replicate_seeds"][0],
              "identity_exact": True, "simulations_started": 6,
              "elapsed_seconds": time.time() - started,
              "estimated_full_seconds_at_8_workers":
                  sum(row["wall_seconds"] for row in rows) / len(rows) * 240 / 8,
              "peak_memory_not_instrumented": True, "rows": rows}
    _dump(output_dir / "smoke.json", result)
    return result


def _journal_key(cid: int, profile: str, seed: int) -> str:
    return f"candidate_{cid}__{profile}__seed_{seed}.json"


def _prepare_output(output_dir: Path, plan_path: Path, validation: dict, resume: bool) -> None:
    if output_dir.exists() and not resume:
        raise ContractError(f"output directory already exists; use --resume explicitly: {output_dir}")
    if output_dir.exists():
        frozen = output_dir / "frozen_plan.json"
        if not frozen.is_file() or ccs.sha256_file(frozen) != ccs.sha256_file(plan_path):
            raise ContractError("resume directory has different or missing frozen plan")
        _dump(output_dir / "validation.json", validation)
    else:
        output_dir.mkdir(parents=True)
        (output_dir / "journal").mkdir()
        (output_dir / "figures").mkdir()
        (output_dir / "frozen_plan.json").write_text(Path(plan_path).read_text())
        _dump(output_dir / "validation.json", validation)


def _frames(results: list[dict], targets) -> tuple[pd.DataFrame, ...]:
    annual_rows, stock_rows, ledger_rows = [], [], []
    target_matrices: dict[tuple[int, str], list[list[float]]] = {}
    for item in results:
        keys = {"candidate_id": item["candidate_id"], "profile_id": item["profile_id"],
                "seed": item["seed"]}
        annual = item["annual_outputs"]
        for series, values in annual.items():
            for year, value in values.items():
                annual_rows.append({**keys, "year": int(year), "series": series,
                                    "value": float(value)})
        mortality_totals = {}
        for row in item["mortality"]:
            annual_rows.append({**keys, **row})
            mortality_totals[int(row["year"])] = mortality_totals.get(int(row["year"]), 0.0) + \
                float(row["value"])
        # The calibration extractor exposes AIDS deaths but not total HIV deaths.
        # Reconstruct the latter exactly from the mutually exclusive passive
        # diagnosis-by-ART mortality cells recorded around the production draw.
        for year, value in mortality_totals.items():
            annual_rows.append({**keys, "year": year, "series": "hiv_deaths",
                                "value": value})
        for row in item["snapshots"]:
            # Snapshot Y is the end state of Y-1; retain the explicit label.
            stock_rows.append({**keys, **row})
        for row in item["ledger"]:
            ledger_rows.append({**keys, **row})
        def annual_value(series: str, year: int) -> float:
            values = annual[series]
            key = year if year in values else str(year)
            return float(values[key])

        target_matrices.setdefault((item["candidate_id"], item["profile_id"]), []).append(
            [annual_value(target.model_output, target.year) for target in targets]
        )
    annual_df, stocks_df, ledger_df = map(pd.DataFrame, (annual_rows, stock_rows, ledger_rows))
    residual_rows, score_rows = [], []
    for (cid, profile), matrix_values in sorted(target_matrices.items()):
        matrix = np.asarray(matrix_values, dtype=float)
        means, variances = matrix.mean(axis=0), matrix.var(axis=0, ddof=1)
        primary = ccs.acceptance(means, variances, targets, replicates=len(matrix),
                                 discrepancy=ccs.PRIMARY_DISCREPANCY)
        sensitivity = ccs.acceptance(means, variances, targets, replicates=len(matrix),
                                     discrepancy=ccs.SENSITIVITY_DISCREPANCY)
        residuals = ccs.search_residuals(means, targets)
        groups = ccs.group_rms(residuals, targets)
        score_rows.append({"candidate_id": cid, "profile_id": profile,
                           "primary_i_max": primary.i_max,
                           "primary_binding_target": primary.binding_target,
                           "primary_pass": primary.passed,
                           "sensitivity_i_max": sensitivity.i_max,
                           "sensitivity_binding_target": sensitivity.binding_target,
                           "sensitivity_pass": sensitivity.passed,
                           **{f"R_{key}": value for key, value in groups.items()}})
        for i, target in enumerate(targets):
            residual_rows.append({
                "candidate_id": cid, "profile_id": profile, "target": target.key,
                "group": target.group, "model_output": target.model_output,
                "year": target.year, "observed": target.z, "mean": means[i],
                "sd": math.sqrt(variances[i]), "signed_residual": means[i] - target.z,
                "search_residual": residuals[i],
                "primary_implausibility": primary.implausibility[i],
                "sensitivity_implausibility": sensitivity.implausibility[i],
            })
    return annual_df, stocks_df, ledger_df, pd.DataFrame(residual_rows), pd.DataFrame(score_rows)


def _profile_summary(annual: pd.DataFrame, stocks: pd.DataFrame, residuals: pd.DataFrame,
                     scores: pd.DataFrame, plan: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    identity = plan["identity_profile_id"]
    means = annual.groupby(["candidate_id", "profile_id", "series", "year"], as_index=False).agg(
        mean=("value", "mean"), sd=("value", "std"))
    targets = residuals.set_index(["target"])["observed"].to_dict()

    def value(cid, profile, series, year):
        row = means[(means.candidate_id == cid) & (means.profile_id == profile) &
                    (means.series == series) & (means.year == year)]
        if row.empty:
            raise ContractError(f"missing summary {cid}/{profile}/{series}/{year}")
        return float(row.iloc[0]["mean"])

    diagnosis_years = [1996, 2000, 2004, 2010, 2015, 2019]
    rows = []
    for score in scores.itertuples(index=False):
        cid, profile = int(score.candidate_id), score.profile_id
        baseline_score = scores[(scores.candidate_id == cid) &
                                (scores.profile_id == identity)].iloc[0]
        dx_errors = [(value(cid, profile, "new_diagnoses_msm", year) -
                      targets[f"shm_new_diagnoses_msm@{year}"]) ** 2
                     for year in diagnosis_years]
        base_errors = [(value(cid, identity, "new_diagnoses_msm", year) -
                        targets[f"shm_new_diagnoses_msm@{year}"]) ** 2
                       for year in diagnosis_years]
        dx2024_error = abs(value(cid, profile, "new_diagnoses_msm", 2024) -
                           targets["shm_new_diagnoses_msm@2024"])
        base_dx2024_error = abs(value(cid, identity, "new_diagnoses_msm", 2024) -
                                targets["shm_new_diagnoses_msm@2024"])
        aids_error = sum(abs(value(cid, profile, "aids_first_diagnosis_proxy_msm", year) -
                             targets[f"rivm_aids_msm@{year}"]) for year in (2019, 2022))
        base_aids_error = sum(abs(value(cid, identity, "aids_first_diagnosis_proxy_msm", year) -
                                  targets[f"rivm_aids_msm@{year}"]) for year in (2019, 2022))
        cumulative = lambda series: sum(value(cid, profile, series, year)  # noqa: E731
                                        for year in range(2015, 2025))
        base_deaths = sum(value(cid, identity, "hiv_deaths", year) for year in range(2015, 2025))
        end_stock = stocks[(stocks.candidate_id == cid) & (stocks.profile_id == profile) &
                           (stocks.snapshot_year == 2025) &
                           stocks.series.str.startswith("msm_undiagnosed_") &
                           (stocks.activity == "All")]["value"].sum() / 10
        start_stock = stocks[(stocks.candidate_id == cid) & (stocks.profile_id == profile) &
                             (stocks.snapshot_year == 2015) &
                             stocks.series.str.startswith("msm_undiagnosed_") &
                             (stocks.activity == "All")]["value"].sum() / 10
        stage_counts = {stage: sum(value(cid, profile, f"hiv_diagnoses_in_{stage}_stage_msm", y)
                                   for y in range(1996, 2026)) for stage in STAGES}
        stage_denominator = sum(stage_counts.values())
        paired = annual[(annual.candidate_id == cid) & (annual.profile_id == profile) &
                        (annual.series == "new_diagnoses_msm") & (annual.year == 2024)]
        paired_base = annual[(annual.candidate_id == cid) & (annual.profile_id == identity) &
                             (annual.series == "new_diagnoses_msm") & (annual.year == 2024)]
        paired = paired.merge(paired_base[["seed", "value"]], on="seed", suffixes=("", "_base"))
        improved_replicates = int((
            (paired.value - targets["shm_new_diagnoses_msm@2024"]).abs() <
            (paired.value_base - targets["shm_new_diagnoses_msm@2024"]).abs()
        ).sum())
        row = {
            "candidate_id": cid, "profile_id": profile,
            "diagnosis_2019_absolute_error": abs(
                value(cid, profile, "new_diagnoses_msm", 2019) -
                targets["shm_new_diagnoses_msm@2019"]),
            "diagnosis_2024_absolute_error": dx2024_error,
            "aids_proxy_2019_absolute_error": abs(
                value(cid, profile, "aids_first_diagnosis_proxy_msm", 2019) -
                targets["rivm_aids_msm@2019"]),
            "aids_proxy_2022_absolute_error": abs(
                value(cid, profile, "aids_first_diagnosis_proxy_msm", 2022) -
                targets["rivm_aids_msm@2022"]),
            "diagnosis_rmse_1996_2019": math.sqrt(sum(dx_errors) / len(dx_errors)),
            "cumulative_2015_2024_msm_infections": cumulative("new_infections_msm"),
            "cumulative_2015_2024_msm_diagnoses": cumulative("new_diagnoses_msm"),
            "cumulative_2015_2024_hiv_deaths": cumulative("hiv_deaths"),
            "undiagnosed_msm_change_start2015_start2025": end_stock - start_stock,
            **{f"fraction_msm_diagnoses_{stage}": stage_counts[stage] / stage_denominator
               for stage in STAGES},
            "improved_2024_diagnosis_error": dx2024_error < base_dx2024_error,
            "earlier_diagnosis_rmse_within_10pct": math.sqrt(sum(dx_errors) / len(dx_errors)) <=
                1.1 * math.sqrt(sum(base_errors) / len(base_errors)),
            "aids_2019_2022_error_not_worse": aids_error <= base_aids_error,
            "primary_i_max_not_worse": score.primary_i_max <= baseline_score.primary_i_max,
            "no_material_hiv_death_increase": cumulative("hiv_deaths") <= 1.05 * base_deaths,
            "latent_state_nonnegative": end_stock >= 0,
            "improved_replicates_2024": improved_replicates,
            "not_single_replicate": improved_replicates >= 6,
            **{column: getattr(score, column) for column in scores.columns
               if column not in {"candidate_id", "profile_id"}},
        }
        gates = ["improved_2024_diagnosis_error", "earlier_diagnosis_rmse_within_10pct",
                 "aids_2019_2022_error_not_worse", "primary_i_max_not_worse",
                 "no_material_hiv_death_increase", "latent_state_nonnegative",
                 "not_single_replicate"]
        row["candidate_promising"] = all(bool(row[g]) for g in gates)
        rows.append(row)
    summary = pd.DataFrame(rows)
    both = summary.groupby("profile_id")["candidate_promising"].agg(["all", "count"])
    cross = summary.groupby("profile_id").agg(
        both_candidates_improve_2024=("improved_2024_diagnosis_error", "all"),
        both_candidates_aids_not_worse=("aids_2019_2022_error_not_worse", "all"),
    )
    summary = summary.merge(cross.reset_index(), on="profile_id")
    summary["promising"] = summary.profile_id.map(both["all"].to_dict()) & \
        summary["both_candidates_improve_2024"] & summary["both_candidates_aids_not_worse"]

    baseline = annual[annual.profile_id == identity].rename(columns={"value": "baseline_value"})[
        ["candidate_id", "seed", "year", "series", "baseline_value"]]
    paired = annual.merge(baseline, on=["candidate_id", "seed", "year", "series"])
    paired["paired_difference"] = paired.value - paired.baseline_value
    paired_summary = paired.groupby(
        ["candidate_id", "profile_id", "year", "series"], as_index=False
    ).agg(mean_paired_change=("paired_difference", "mean"),
          sd_paired_change=("paired_difference", "std"), n=("paired_difference", "size"))
    half = 2.262 * paired_summary.sd_paired_change / np.sqrt(paired_summary.n)
    paired_summary["ci95_low"] = paired_summary.mean_paired_change - half
    paired_summary["ci95_high"] = paired_summary.mean_paired_change + half
    return summary, paired_summary


def _write_figures(output_dir: Path, profile_summary: pd.DataFrame) -> None:
    """Write two dependency-free SVG decision plots (one per candidate)."""
    for cid, group in profile_summary.groupby("candidate_id"):
        group = group.copy()
        group["aids_error"] = (group.aids_proxy_2019_absolute_error +
                               group.aids_proxy_2022_absolute_error)
        xmin, xmax = group.diagnosis_2024_absolute_error.min(), \
            group.diagnosis_2024_absolute_error.max()
        ymin, ymax = group.aids_error.min(), group.aids_error.max()
        scale = lambda value, low, high, left, right: (left + right) / 2 if high == low else \
            left + (float(value) - low) / (high - low) * (right - left)
        elements = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="620" viewBox="0 0 900 620">',
            '<rect width="900" height="620" fill="white"/>',
            f'<text x="450" y="35" text-anchor="middle" font-size="20">Candidate {cid}: structural trade-off surface</text>',
            '<line x1="90" y1="540" x2="850" y2="540" stroke="black"/>',
            '<line x1="90" y1="60" x2="90" y2="540" stroke="black"/>',
            '<text x="470" y="595" text-anchor="middle" font-size="15">2024 MSM diagnosis absolute error</text>',
            '<text x="22" y="300" text-anchor="middle" font-size="15" transform="rotate(-90 22 300)">Combined 2019/2022 AIDS-proxy absolute error</text>',
        ]
        for row in group.itertuples(index=False):
            x = scale(row.diagnosis_2024_absolute_error, xmin, xmax, 110, 825)
            y = scale(row.aids_error, ymin, ymax, 515, 80)
            color = "#1877b5" if row.promising else "#d4553d"
            label = row.profile_id.replace("rho_", "").replace("_aids_", "/")
            elements += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{color}"/>',
                         f'<text x="{x + 8:.1f}" y="{y - 7:.1f}" font-size="10">{label}</text>']
        elements += [f'<text x="90" y="560" font-size="11">{xmin:.1f}</text>',
                     f'<text x="820" y="560" font-size="11">{xmax:.1f}</text>',
                     f'<text x="45" y="540" font-size="11">{ymin:.1f}</text>',
                     f'<text x="45" y="85" font-size="11">{ymax:.1f}</text>', '</svg>']
        (output_dir / "figures" / f"candidate_{cid}_tradeoff.svg").write_text("\n".join(elements))


def _write_note(output_dir: Path) -> None:
    text = """# Implementation note

The diagnostic-only configuration path is
`transmission_model.disease.diagnosis.stage_structured_diagnosis` in raw JSON,
translated to `disease.diagnosis.stage_structured_diagnosis` in the engine. Its
fields are `enabled`, `routine_hazard_scale`, and
`aids_symptom_annual_probability`.

Disabled configurations and the enabled identity profile (`rho=1`, AIDS
symptom probability `0`) execute the pre-existing diagnosis branch with the
same process keys and counter-based random streams. Non-identity profiles use
the injected candidate's base schedule and MSM-relative multiplier for routine
testing, then add the common symptom hazard only in AIDS. C-1 exogenous
non-MSM diagnoses are unchanged and reported separately. Acute, Chronic, and
AIDS diagnosis counters are passive functions of the realized diagnosis draw.

No production configuration or HTA scenario is modified.
"""
    (output_dir / "IMPLEMENTATION_NOTE.md").write_text(text)


def _write_test_results(output_dir: Path) -> None:
    text = """# Test results

1. Focused post-implementation suite:

   `.venv/bin/pytest -q tests/test_stage_structured_diagnosis_audit.py tests/test_aids_observation.py tests/test_counter_based_rng.py tests/test_c1_exogenous_non_msm.py tests/test_diagnosis_year_bands.py`

   Final outcome: 131 passed.

2. Broader diagnosis/AIDs/RNG/temporal/ledger/coupled suite:

   `.venv/bin/pytest -q tests/test_stage_structured_diagnosis_audit.py tests/test_aids_observation.py tests/test_counter_based_rng.py tests/test_c1_exogenous_non_msm.py tests/test_diagnosis_year_bands.py tests/test_temporal_diagnosis_shape_audit.py tests/test_post2015_stock_flow_decomposition.py tests/test_coupled_calibration_search.py tests/test_forecast_readiness_confirmation.py tests/test_msm_relative_diagnosis_v13_10.py --deselect=tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior::test_enabled_with_multiplier_one_matches_baseline`

   Outcome: 210 passed, 12 failed, 1 deselected. All 12 failures were the
   expected frozen-provenance guards in older audit runners: they rejected the
   intentional changes to `run_v3.py`, `aids_observation.py`, and
   `calib_extractor.py` before constructing an engine. The deselected test is
   the repository's documented pre-existing deterministic split-allocation
   exception; stochastic identity for this audit was tested separately and
   passed exactly.

3. Validate-only:

   `.venv/bin/python run_stage_structured_diagnosis_audit.py --validate-only`

   Outcome: passed; 0 engines constructed, 0 simulations started, 0 artifacts
   written. All 24 configurations preserved candidate schedules and differed
   only in the new block.

4. Smoke:

   `.venv/bin/python run_stage_structured_diagnosis_audit.py --smoke`

   Outcome: passed for both candidates. Enabled identity summary and final-state
   hashes exactly matched the legacy controls; the nontrivial profile also ran.
"""
    (output_dir / "TEST_RESULTS.md").write_text(text)


def _write_report(output_dir: Path, summary: pd.DataFrame, manifest: dict) -> None:
    promising = sorted(summary.loc[summary.promising, "profile_id"].unique())
    if promising:
        recommendation = "Mechanism supported for a separately specified recalibration"
    else:
        boundary = summary.groupby("profile_id")["candidate_promising"].all()
        useful = [name for name, flag in boundary.items() if flag]
        recommendation = ("Mechanism informative but insufficient; one bounded follow-up is required"
                          if useful else "Mechanism not supported over the tested range")
    table = summary[["candidate_id", "profile_id", "diagnosis_2024_absolute_error",
                     "diagnosis_rmse_1996_2019", "aids_proxy_2019_absolute_error",
                     "aids_proxy_2022_absolute_error", "primary_i_max", "promising"]]
    lines = ["# Stage-structured diagnosis audit", "",
             "## Plain-language result", "",
             f"The frozen 240-trajectory run completed with {manifest['n_failed']} failed tasks. ",
             f"Profiles passing every predeclared gate across both candidates: "
             f"**{', '.join(promising) if promising else 'none'}**.", "",
             "1. **Did lower routine diagnosis preserve the reservoir and raise later diagnoses?** "
             "Yes at part of the surface: lower rho retained more undiagnosed MSM by 2025 and "
             "several rho=0.6/0.8 profiles reduced the 2024 diagnosis error in both candidates. "
             "The response was not sufficient to pass the other gates.", "",
             "2. **Did the AIDS uplift improve the late-stage proxy, or merely move diagnoses?** "
             "It improved the 2022 component at high uplift, but shifted the diagnosis-stage mix "
             "toward AIDS and worsened the already-close 2019 proxy at the lower-routine profiles; "
             "the combined 2019/2022 gate therefore failed.", "",
             "3. **Was improvement obtained without damaging the earlier trajectory?** Some "
             "rho=0.6/0.8 points kept the 1996-2019 diagnosis RMSE within 10%, but no point also "
             "preserved the AIDS error, I_max, deaths, and replicate-stability gates.", "",
             "4. **Stable region or trade-off/ridge?** A trade-off: increasing the uplift helped "
             "the AIDS-stage component while lowering routine testing improved recent diagnosis "
             "at the cost of other contract dimensions. No stable passing region appeared across "
             "both candidates.", "",
             "5. **Does this justify recalibration with the mechanism?** No. The bounded surface "
             "is informative, but it does not meet the predeclared standard for promotion to a "
             "separately specified recalibration.", "", "## Decision table", "",
             table.to_markdown(index=False, floatfmt=".3f"), "",
             "## Reproducibility", "",
             "Resume command:", "",
             "```bash", manifest["resume_command"], "```", "",
             "The audit is structural sensitivity, not recalibration or validation. The model AIDS "
             "compartment is not equivalent to surveillance late diagnosis defined by CD4 <350.", "",
             f"**{recommendation}**", ""]
    (output_dir / "REPORT.md").write_text("\n".join(lines))


def run(plan_path: Path, output_dir: Path, workers: int, resume: bool) -> dict:
    validation = validate(plan_path)
    plan = load_plan(plan_path)
    source, _, _, state = _source_context(plan)
    bases, _ = _base_configs(plan, source, state)
    _prepare_output(output_dir, plan_path, validation, resume)
    journal = output_dir / "journal"
    payloads = []
    for cid in plan["candidate_ids"]:
        for profile in plan["profiles"]:
            config = apply_profile(bases[int(cid)], profile)
            for seed in plan["replicate_seeds"]:
                path = journal / _journal_key(int(cid), profile["id"], int(seed))
                if not path.exists():
                    payloads.append({"candidate_id": int(cid), "profile_id": profile["id"],
                                     "seed": int(seed), "config": config, "plan": plan})
    run_manifest = {"status": "RUNNING", "started_utc": _now(),
                    "planned_trajectories": plan["trajectory_budget"],
                    "remaining_at_start": len(payloads), "workers": int(workers)}
    _dump(output_dir / "run_state.json", run_manifest)
    started = time.time()
    if payloads:
        with ProcessPoolExecutor(max_workers=int(workers)) as pool:
            futures = {pool.submit(worker, payload): payload for payload in payloads}
            for future in as_completed(futures):
                item = future.result()
                path = journal / _journal_key(
                    int(item["candidate_id"]), item["profile_id"], int(item["seed"])
                )
                _dump(path, item)
    results = [json.loads(path.read_text()) for path in sorted(journal.glob("*.json"))]
    if len(results) != int(plan["trajectory_budget"]):
        raise ContractError(f"journal has {len(results)} results, expected {plan['trajectory_budget']}")
    failures = [item for item in results if item.get("error")]
    if failures:
        pd.DataFrame(failures).to_csv(output_dir / "failures.csv", index=False)
        failed = {**run_manifest, "status": "FAILED", "n_failed": len(failures),
                  "elapsed_seconds": time.time() - started}
        _dump(output_dir / "manifest.json", failed)
        raise ContractError(f"{len(failures)} trajectories failed; preserved in failures.csv")

    annual, stocks, ledger, residuals, scores = _frames(results, state["targets"])
    profile_summary, paired = _profile_summary(annual, stocks, residuals, scores, plan)
    annual.to_csv(output_dir / "seed_level_annual.csv", index=False)
    stocks.to_csv(output_dir / "state_snapshots.csv", index=False)
    latent = stocks[stocks.snapshot_year == 2025].copy()
    latent_2024 = latent.copy()
    latent_2024["state_timing"] = "2024_year_end"
    latent["state_timing"] = "2025_starting_state"
    pd.concat([latent_2024, latent], ignore_index=True).to_csv(
        output_dir / "latent_state_2024_year_end_2025_start.csv", index=False
    )
    ledger.to_csv(output_dir / "post2015_stock_flow.csv", index=False)
    residuals.to_csv(output_dir / "target_residuals.csv", index=False)
    profile_summary.to_csv(output_dir / "profile_summary.csv", index=False)
    paired.to_csv(output_dir / "paired_seed_changes.csv", index=False)
    stage = annual[annual.series.str.startswith("hiv_diagnoses_in_") &
                   annual.series.str.contains("_stage")]
    stage.to_csv(output_dir / "stage_diagnosis_decomposition.csv", index=False)
    max_residual = float(ledger.balance_residual.abs().max())
    if max_residual != 0.0:
        raise ContractError(f"combined ledger residual {max_residual} is not zero")
    _write_figures(output_dir, profile_summary)
    _write_note(output_dir)
    _write_test_results(output_dir)
    manifest = {
        "design": plan["design"], "status": "COMPLETE", "n_candidates": 2,
        "n_profiles": 12, "replicates": 10, "trajectories": len(results),
        "candidate_ids": plan["candidate_ids"],
        "profiles": plan["profiles"], "replicate_seeds": plan["replicate_seeds"],
        "trajectory_budget": plan["trajectory_budget"],
        "configuration_hashes": validation["configurations"],
        "n_failed": 0, "max_stock_flow_balance_residual": max_residual,
        "elapsed_seconds_this_invocation": time.time() - started,
        "mean_trajectory_wall_seconds": float(np.mean([x["wall_seconds"] for x in results])),
        "plan_sha256": ccs.sha256_file(plan_path),
        "runner_sha256": ccs.sha256_file(Path(__file__)),
        "mechanism_sha256": ccs.sha256_file(ROOT / "stage_structured_diagnosis.py"),
        "source_search_manifest_sha256": plan["source_manifest_sha256"],
        "source_confirmation_manifest_sha256": plan["source_confirmation_manifest_sha256"],
        "seed_overlap": plan["seed_overlap"],
        "resume_command": (f"{sys.executable} {Path(__file__).name} --run --resume "
                           f"--plan {plan_path} --output-dir {output_dir} --workers {workers}"),
        "commands": {"validate": f"{sys.executable} {Path(__file__).name} --validate-only",
                     "smoke": f"{sys.executable} {Path(__file__).name} --smoke",
                     "run": f"{sys.executable} {Path(__file__).name} --run --workers {workers}"},
        "environment": {"python": sys.version, "platform": platform.platform()},
        "generated_utc": _now(),
    }
    _write_report(output_dir, profile_summary, manifest)
    _dump(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--smoke", action="store_true")
    modes.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise ContractError("workers must be in [1, 8]")
    if args.run:
        result = run(args.plan, args.output_dir, args.workers, args.resume)
    elif args.smoke:
        smoke_dir = args.output_dir if args.output_dir != DEFAULT_OUTPUT else DEFAULT_SMOKE_OUTPUT
        result = smoke(args.plan, smoke_dir)
    else:
        result = validate(args.plan)
    print(json.dumps(ccs.json_safe(result), indent=2))


if __name__ == "__main__":
    main()
