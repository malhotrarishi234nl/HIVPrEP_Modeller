"""
run_psa_hta.py — PSA × HTA entry point.

Runs probabilistic sensitivity analysis across the full HTA pipeline:
  For each draw: sample epi + HTA params → run baseline → freeze →
  fork 3 arms → economics → collect incremental outcomes.
  After all draws: compute CE plane, CEAC, summary Excel.

Usage:
    python run_psa_hta.py [--config 1980_test.json]
                          [--hta-config configs/hta_config.json]
                          [--psa-config configs/psa_config.json]
                          [--n-draws N]
                          [--output-dir outputs/psa_hta]
                          [--seed SEED]
                          [--scenario scenario_a_base]
                          [--workers W]
                          [--dry-run]
                          [--quiet]

V11.11.3 — 2026-04-29

Known limitations (documented, do not fix in this patch):
  - Correlation groups (BACKLOG-1): epi and HTA params sampled independently.
  - AE placeholder distributions (BACKLOG-15): results computable but not
    defensible until all placeholder distributions are sourced.
  - Single scenario per run: PSA × scenario grid is deferred.
  - No parallelism within draws: each draw runs 4 sims sequentially
    (1 baseline + 3 arms).
"""

import argparse
import copy
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

from hta_config_loader import load_hta_config


# ── Standalone arm config builder ─────────────────────────────────────────────

def _build_arm_config_standalone(base_config: dict, arm_name: str,
                                  analysis_scenario: dict,
                                  runtime_config: dict = None) -> dict:
    """
    Build an arm-specific configuration dict without requiring a
    MultiScenarioRunner instance. Replicates the logic from
    run_scenarios.MultiScenarioRunner._build_arm_config exactly.

    Parameters
    ----------
    base_config : dict
        Deep copy of the base model config (already has epi draws injected).
    arm_name : str
        One of "no_prep", "oral_prep", "lenacapavir".
    analysis_scenario : dict
        Scenario dict from hta_config["analysis_scenarios"].

    Returns
    -------
    dict
        Modified deep copy of base_config for this arm.
    """
    config = copy.deepcopy(base_config)

    if arm_name == "no_prep":
        # Disable all PrEP interventions
        if "interventions" in config:
            for intervention_name in config["interventions"]:
                config["interventions"][intervention_name]["enabled"] = {
                    "value": False
                }
        # Zero out all PrEP schedules (exact pattern from run_scenarios)
        if "prep_schedules" in config:
            for prep_type in config["prep_schedules"]:
                if "initiation" in config["prep_schedules"][prep_type]:
                    config["prep_schedules"][prep_type]["initiation"] = {
                        "schedule": {
                            str(year): 0.0
                            for year in range(1980, 2060)
                        }
                    }

    elif arm_name in ("oral_prep", "oral_prep_expansion"):
        if "interventions" in config:
            if "oral_prep" in config["interventions"]:
                config["interventions"]["oral_prep"]["enabled"] = {"value": True}
            if "lenacapavir" in config["interventions"]:
                config["interventions"]["lenacapavir"]["enabled"] = {"value": False}
        # Change C (V12.9): Apply oral_prep_overrides from scenario
        oral_overrides = analysis_scenario.get("oral_prep_overrides", {})
        if "eligible_activity_classes" in oral_overrides:
            if "oral_prep" in config.get("interventions", {}):
                config["interventions"]["oral_prep"]["eligible_activity_classes"] = {
                    "value": oral_overrides["eligible_activity_classes"]
                }

    elif arm_name == "lenacapavir":
        if "interventions" in config:
            if "oral_prep" in config["interventions"]:
                config["interventions"]["oral_prep"]["enabled"] = {"value": False}
            if "lenacapavir" in config["interventions"]:
                config["interventions"]["lenacapavir"]["enabled"] = {"value": True}
        # Apply scenario-specific eligible_activity_classes
        lena_overrides = analysis_scenario.get("lenacapavir_overrides", {})
        if "eligible_activity_classes" in lena_overrides:
            if "lenacapavir" in config.get("interventions", {}):
                config["interventions"]["lenacapavir"][
                    "eligible_activity_classes"
                ] = {"value": lena_overrides["eligible_activity_classes"]}

    elif arm_name == "oral_prep_plus_lena":
        # Change D (V12.9): Both oral PrEP and Lenacapavir enabled simultaneously
        if "interventions" in config:
            if "oral_prep" in config["interventions"]:
                config["interventions"]["oral_prep"]["enabled"] = {"value": True}
            if "lenacapavir" in config["interventions"]:
                config["interventions"]["lenacapavir"]["enabled"] = {"value": True}
        # Apply per-intervention eligibility
        oral_overrides = analysis_scenario.get("oral_prep_overrides", {})
        if "eligible_activity_classes" in oral_overrides:
            if "oral_prep" in config.get("interventions", {}):
                config["interventions"]["oral_prep"]["eligible_activity_classes"] = {
                    "value": oral_overrides["eligible_activity_classes"]
                }
        lena_overrides = analysis_scenario.get("lenacapavir_overrides", {})
        if "eligible_activity_classes" in lena_overrides:
            if "lenacapavir" in config.get("interventions", {}):
                config["interventions"]["lenacapavir"]["eligible_activity_classes"] = {
                    "value": lena_overrides["eligible_activity_classes"]
                }

    else:
        raise ValueError(f"Unknown arm: '{arm_name}'")

    # Change B (V12.9): prep_schedule_overrides injection (deep-merge)
    # Year→rate pairs are injected into prep_schedules[prep_type][sched_key]["schedule"]
    # so that _validate_lenacapavir_schedule_active and ScheduleManager find them correctly.
    schedule_overrides = analysis_scenario.get("prep_schedule_overrides", {})
    arm_schedule = schedule_overrides.get(arm_name, {})
    if arm_schedule:
        for prep_type, sched_block in arm_schedule.items():
            if prep_type not in config.get("prep_schedules", {}):
                config.setdefault("prep_schedules", {})[prep_type] = {}
            for sched_key, sched_val in sched_block.items():
                if sched_key.startswith("_"):
                    continue
                if not isinstance(sched_val, dict):
                    config["prep_schedules"][prep_type][sched_key] = sched_val
                    continue
                year_keys = {k: v for k, v in sched_val.items()
                             if not k.startswith("_") and k.isdigit()}
                if year_keys:
                    target = config["prep_schedules"][prep_type].setdefault(sched_key, {})
                    if not isinstance(target, dict):
                        config["prep_schedules"][prep_type][sched_key] = {}
                        target = config["prep_schedules"][prep_type][sched_key]
                    schedule_dict = target.setdefault("schedule", {})
                    schedule_dict.update(year_keys)
                else:
                    for sub_key, sub_val in sched_val.items():
                        if not sub_key.startswith("_"):
                            config["prep_schedules"][prep_type].setdefault(sched_key, {})[sub_key] = sub_val

    if analysis_scenario.get("arm_pathways"):
        from run_scenarios import _apply_prep_pathway_to_arm_config
        for pathway in analysis_scenario.get("arm_pathways", {}).get(arm_name, []):
            _apply_prep_pathway_to_arm_config(config, pathway, runtime_config)

    return config


def _inject_hta_value(hta_config: dict, dot_path: str, value: float) -> None:
    """
    Navigate dot_path through hta_config and set the terminal "value" key.
    Handles array-indexed segments by matching the "id" field.
    """
    segments = dot_path.split(".")
    node = hta_config

    for seg in segments[:-1]:
        if isinstance(node, list):
            found = False
            for item in node:
                if isinstance(item, dict) and item.get("id") == seg:
                    node = item
                    found = True
                    break
            if not found:
                raise KeyError(
                    f"_inject_hta_value: id='{seg}' not found in list "
                    f"(path='{dot_path}')"
                )
        elif isinstance(node, dict):
            if seg not in node:
                raise KeyError(
                    f"_inject_hta_value: key '{seg}' not found at segment "
                    f"'{seg}' (path='{dot_path}')"
                )
            node = node[seg]
        else:
            raise TypeError(
                f"_inject_hta_value: unexpected type {type(node)} at '{seg}' "
                f"(path='{dot_path}')"
            )

    leaf = segments[-1]
    if isinstance(node, list):
        # Terminal in a list — find item by id and set its value key
        for item in node:
            if isinstance(item, dict) and item.get("id") == leaf:
                item["value"] = value
                return
        raise KeyError(
            f"_inject_hta_value: id='{leaf}' not found in list "
            f"at terminal of path '{dot_path}'"
        )
    elif isinstance(node, dict) and leaf in node:
        if isinstance(node[leaf], dict) and "value" in node[leaf]:
            node[leaf]["value"] = value
        else:
            node[leaf] = value
    else:
        raise KeyError(
            f"_inject_hta_value: terminal key '{leaf}' not found (path='{dot_path}')"
        )


# ── Per-draw function ─────────────────────────────────────────────────────────

def _run_single_hta_draw(draw_index, epi_draw_row, hta_draw_row,
                          base_config, hta_config, seed,
                          analysis_scenario, freeze_year, eval_years,
                          quiet=True):
    """
    Run one PSA draw across all 3 arms.

    Parameters
    ----------
    draw_index : int
    epi_draw_row : dict  {dot_path: value} for epi parameters
    hta_draw_row : dict  {dot_path: value} for HTA parameters
    base_config  : dict  original base model config (not mutated)
    hta_config   : dict  original hta_config (not mutated)
    seed         : int   base RNG seed (seed + draw_index used per draw)
    analysis_scenario : dict  scenario dict from hta_config["analysis_scenarios"]
    freeze_year  : int   year at which to freeze the baseline
    eval_years   : int   number of evaluation years after freeze
    quiet        : bool  suppress engine output

    Returns
    -------
    dict with keys: draw_index, arm_results, incremental
    """
    from run_v3 import SimulationEngineV3
    from state_snapshot import StateSnapshotManager
    from economics import EconomicsAccumulator
    from adverse_events import AdverseEventModule
    from config_utils import extract_value
    from run_psa import _inject_value
    from run_scenarios import (
        _collapse_prep_to_none,
        _validate_lenacapavir_schedule_active,
    )

    # 1. Apply epi draws to base config
    cfg = copy.deepcopy(base_config)
    cfg["run_control"]["deterministic_mode"]["value"] = True
    cfg["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}
    for path, value in epi_draw_row.items():
        _inject_value(cfg, path, float(value))

    # 2. Apply HTA draws to hta_config
    hta_cfg = copy.deepcopy(hta_config)
    for path, value in hta_draw_row.items():
        _inject_hta_value(hta_cfg, path, float(value))

    # 3. Run baseline to freeze year
    ssm = StateSnapshotManager()
    ssm.configure(freeze_enabled=True, freeze_year=freeze_year)

    with redirect_stdout(io.StringIO()) if quiet else _null_ctx():
        baseline_engine = SimulationEngineV3(cfg, seed=seed + draw_index)
        baseline_engine._snapshot_manager = ssm
        freeze_step_target = round(
            (freeze_year - baseline_engine.time.start_year)
            / baseline_engine.time.years_per_step
        )
        if freeze_step_target >= baseline_engine.time.n_steps:
            baseline_engine.time.n_steps = freeze_step_target + 1
        baseline_engine.run()

    snapshot = ssm.baseline_snapshot
    if snapshot is None:
        raise RuntimeError(
            f"Draw {draw_index}: baseline freeze failed at year {freeze_year}"
        )

    freeze_step = snapshot.step
    years_per_step = baseline_engine.time.years_per_step
    steps_per_year = round(1.0 / years_per_step)
    eval_steps = eval_years * steps_per_year
    end_step = freeze_step + eval_steps
    step_fraction = years_per_step

    # Read prep_reset_at_fork flag
    _prep_reset = extract_value(
        hta_cfg.get("time_horizon", {})
               .get("prep_reset_at_fork", {"value": True})
    )

    # 4. Fork arms from frozen baseline
    # Split runtime configs provide an explicit arm_order. Legacy monolithic
    # scenarios fall back to the historical head-to-head/expansion defaults.
    arm_order = analysis_scenario.get("arm_order")
    if not arm_order:
        arm_structure = analysis_scenario.get("arm_structure", "head_to_head")
        if arm_structure == "expansion":
            arm_order = ["no_prep", "oral_prep", "oral_prep_plus_lena"]
        else:
            arm_order = ["no_prep", "oral_prep", "lenacapavir"]
    arm_results = {}

    for arm_name in arm_order:
        arm_cfg = _build_arm_config_standalone(
            cfg, arm_name, analysis_scenario,
            runtime_config=hta_cfg.get("_runtime_config"),
        )
        if arm_name in ("lenacapavir", "oral_prep_plus_lena"):
            _validate_lenacapavir_schedule_active(
                arm_cfg, analysis_scenario, freeze_year, eval_years
            )
        restored = ssm.restore(arm_name)

        # Change A (V12.9): per-arm prep_reset control
        arm_prep_reset_map = analysis_scenario.get("arm_prep_reset", {})
        should_reset = arm_prep_reset_map.get(arm_name, _prep_reset)
        if should_reset:
            _collapse_prep_to_none(restored)

        with redirect_stdout(io.StringIO()) if quiet else _null_ctx():
            arm_engine = SimulationEngineV3(
                arm_cfg, seed=seed + draw_index, initial_snapshot=restored
            )
            arm_engine.time.n_steps = max(arm_engine.time.n_steps, end_step)

            econ = EconomicsAccumulator(
                hta_config=hta_cfg,
                groups=arm_engine.groups,
                freeze_year=float(freeze_year),
                step_fraction=step_fraction,
                arm_name=(
                    "oral_prep" if arm_name == "oral_prep_expansion" else arm_name
                ),
            )
            ae = AdverseEventModule(
                hta_cfg, arm_engine.groups,
                step_fraction=step_fraction, rng=arm_engine.rng,
            )
            econ.set_ae_module(ae)
            arm_engine._economics = econ
            arm_engine.run(start_step=freeze_step, end_step=end_step)

        arm_results[arm_name] = econ.get_summary()
        del arm_engine

    # 5. Compute incremental outcomes
    perspective = hta_cfg.get("perspective", {}).get("primary", "payer")
    wtp = extract_value(hta_cfg["wtp_threshold_eur_per_qaly"])

    incremental = {}

    comparator_arm = analysis_scenario.get("comparator_arm")
    intervention_arm = analysis_scenario.get("intervention_arm")
    if comparator_arm and intervention_arm:
        comparator = arm_results[comparator_arm]
        intervention = arm_results[intervention_arm]
        if perspective == "societal":
            dc = (
                intervention["total_costs_disc_societal"]
                - comparator["total_costs_disc_societal"]
            )
        else:
            dc = intervention["total_costs_disc"] - comparator["total_costs_disc"]
        dq = intervention["total_qalys_disc"] - comparator["total_qalys_disc"]
        incremental[f"{intervention_arm}_vs_{comparator_arm}"] = {
            "delta_cost": dc,
            "delta_qaly": dq,
            "icer": dc / dq if abs(dq) > 1e-6 else float("inf"),
            "nmb": dq * wtp - dc,
            "infections_averted": (
                comparator["cumul_new_infections"]
                - intervention["cumul_new_infections"]
            ),
        }

    elif analysis_scenario.get("arm_structure", "head_to_head") == "expansion":
        # Change F (V12.9): expansion framing — primary comparison is combo vs oral_prep
        ref = arm_results["no_prep"]
        oral = arm_results["oral_prep"]
        combo = arm_results["oral_prep_plus_lena"]

        # oral_prep vs no_prep (absolute baseline)
        if perspective == "societal":
            dc_oral = oral["total_costs_disc_societal"] - ref["total_costs_disc_societal"]
        else:
            dc_oral = oral["total_costs_disc"] - ref["total_costs_disc"]
        dq_oral = oral["total_qalys_disc"] - ref["total_qalys_disc"]
        incremental["oral_prep_vs_no_prep"] = {
            "delta_cost": dc_oral,
            "delta_qaly": dq_oral,
            "icer": dc_oral / dq_oral if abs(dq_oral) > 1e-6 else float("inf"),
            "nmb": dq_oral * wtp - dc_oral,
            "infections_averted": ref["cumul_new_infections"] - oral["cumul_new_infections"],
        }

        # oral_prep_plus_lena vs oral_prep (primary thesis ICER)
        if perspective == "societal":
            dc = combo["total_costs_disc_societal"] - oral["total_costs_disc_societal"]
        else:
            dc = combo["total_costs_disc"] - oral["total_costs_disc"]
        dq = combo["total_qalys_disc"] - oral["total_qalys_disc"]
        incremental["oral_prep_plus_lena_vs_oral_prep"] = {
            "delta_cost": dc,
            "delta_qaly": dq,
            "icer": dc / dq if abs(dq) > 1e-6 else float("inf"),
            "nmb": dq * wtp - dc,
            "infections_averted": oral["cumul_new_infections"] - combo["cumul_new_infections"],
        }

        # oral_prep_plus_lena vs no_prep (absolute)
        if perspective == "societal":
            dc_np = combo["total_costs_disc_societal"] - ref["total_costs_disc_societal"]
        else:
            dc_np = combo["total_costs_disc"] - ref["total_costs_disc"]
        dq_np = combo["total_qalys_disc"] - ref["total_qalys_disc"]
        incremental["oral_prep_plus_lena_vs_no_prep"] = {
            "delta_cost": dc_np,
            "delta_qaly": dq_np,
            "icer": dc_np / dq_np if abs(dq_np) > 1e-6 else float("inf"),
            "nmb": dq_np * wtp - dc_np,
            "infections_averted": ref["cumul_new_infections"] - combo["cumul_new_infections"],
        }

    else:
        # Standard head-to-head: each arm vs no_prep + lena vs oral_prep
        ref = arm_results["no_prep"]
        for arm_name in ["oral_prep", "lenacapavir"]:
            intv = arm_results[arm_name]
            if perspective == "societal":
                delta_cost = intv["total_costs_disc_societal"] - ref["total_costs_disc_societal"]
            else:
                delta_cost = intv["total_costs_disc"] - ref["total_costs_disc"]
            delta_qaly = intv["total_qalys_disc"] - ref["total_qalys_disc"]
            icer = delta_cost / delta_qaly if abs(delta_qaly) > 1e-6 else float("inf")
            nmb = delta_qaly * wtp - delta_cost
            incremental[f"{arm_name}_vs_no_prep"] = {
                "delta_cost": delta_cost,
                "delta_qaly": delta_qaly,
                "icer": icer,
                "nmb": nmb,
                "infections_averted": (
                    ref["cumul_new_infections"] - intv["cumul_new_infections"]
                ),
            }

        # Also lenacapavir vs oral_prep
        oral = arm_results["oral_prep"]
        lena = arm_results["lenacapavir"]
        if perspective == "societal":
            dc = lena["total_costs_disc_societal"] - oral["total_costs_disc_societal"]
        else:
            dc = lena["total_costs_disc"] - oral["total_costs_disc"]
        dq = lena["total_qalys_disc"] - oral["total_qalys_disc"]
        incremental["lenacapavir_vs_oral_prep"] = {
            "delta_cost": dc,
            "delta_qaly": dq,
            "icer": dc / dq if abs(dq) > 1e-6 else float("inf"),
            "nmb": dq * wtp - dc,
            "infections_averted": (
                oral["cumul_new_infections"] - lena["cumul_new_infections"]
            ),
        }

    del baseline_engine
    return {
        "draw_index": draw_index,
        "arm_results": arm_results,
        "incremental": incremental,
    }


class _null_ctx:
    """No-op context manager for when quiet=False."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ── Main orchestration ────────────────────────────────────────────────────────

def run_psa_hta(config_path, hta_config_path, psa_config_path,
                n_draws_override=None, output_dir_override=None,
                seed_override=None, scenario_name=None,
                dry_run=False, quiet=False, workers=1):
    """
    Run PSA across the full HTA pipeline.

    Parameters
    ----------
    config_path      : str   Path to v11_configuration.json
    hta_config_path  : str   Path to hta_config.json
    psa_config_path  : str   Path to psa_config.json
    n_draws_override : int   Override n_draws (for testing)
    output_dir_override : str  Override output directory
    seed_override    : int   Override RNG seed
    scenario_name    : str   Name of analysis scenario (default: first in list)
    dry_run          : bool  Pre-flight only, no simulation
    quiet            : bool  Suppress per-draw engine output
    workers          : int   Parallel draw workers. 1 = sequential, 0 = CPU count.

    Returns
    -------
    dict or None
        {"incremental_df": pd.DataFrame, "arm_outcomes_df": pd.DataFrame}
        on success, or None if dry_run.
    """
    # 1. Load configs
    with open(config_path) as f:
        base_config = json.load(f)
    hta_config = load_hta_config(hta_config_path)
    with open(psa_config_path) as f:
        psa_config = json.load(f)

    # 2. Apply CLI overrides
    if n_draws_override is not None:
        psa_config["sampling"]["n_draws"]["value"] = n_draws_override
    if output_dir_override is not None:
        psa_config["outputs"]["directory"]["value"] = output_dir_override
    if seed_override is not None:
        psa_config["sampling"]["seed"]["value"] = seed_override

    n = psa_config["sampling"]["n_draws"]["value"]
    seed = psa_config["sampling"]["seed"]["value"]
    output_dir = Path(psa_config["outputs"]["directory"]["value"])
    if workers == 0:
        workers = os.cpu_count() or 4
    workers = max(1, min(int(workers), int(n)))

    # 3. Resolve analysis scenario
    scenarios = hta_config.get("analysis_scenarios", [])
    if not scenarios:
        raise ValueError("hta_config has no analysis_scenarios")
    if scenario_name:
        matched = [s for s in scenarios if s["name"] == scenario_name]
        if not matched:
            raise ValueError(
                f"Scenario '{scenario_name}' not found in hta_config. "
                f"Available: {[s['name'] for s in scenarios]}"
            )
        analysis_scenario = matched[0]
    else:
        analysis_scenario = next(
            (s for s in scenarios if s.get("primary_comparison")),
            scenarios[0],
        )

    freeze_year = hta_config["time_horizon"]["freeze_year"]
    eval_years = hta_config["time_horizon"]["evaluation_years"]

    # 4. Init samplers
    from psa_sampler import PSASampler
    from hta_sampler import HTASampler

    epi_sampler = PSASampler(base_config, psa_config)
    hta_sampler = HTASampler(hta_config, psa_config)

    print(f"PSA × HTA pre-flight OK")
    print(f"  Scenario:       {analysis_scenario['name']}")
    print(f"  Freeze year:    {freeze_year}")
    print(f"  Eval horizon:   {eval_years} years")
    print(f"  Epi params:     {len(epi_sampler.param_paths)}")
    print(f"  HTA params:     {len(hta_sampler.param_paths)}")
    print(f"  Draws:          {n}")
    print(f"  Seed:           {seed}")
    print(f"  Method:         {psa_config['sampling']['method']['value'].upper()}")
    print(f"  Workers:        {workers}" + (" (sequential)" if workers == 1 else " (parallel)"))
    print()

    warnings = ["Params sampled independently (no correlation groups) [BACKLOG-1]"]
    art_entry = hta_config.get("disease_costs", {}).get("art_first_line", {})
    art_value = art_entry.get("value")
    art_mean = art_entry.get("distribution", {}).get("mean")
    if (
        isinstance(art_value, (int, float))
        and isinstance(art_mean, (int, float))
        and art_value
        and abs(art_mean - art_value) / abs(art_value) > 0.01
    ):
        warnings.append(
            f"art_first_line: distribution.mean ({art_mean}) != value ({art_value})"
        )
    placeholder_params = [
        path for path, _, _, entry in hta_sampler._param_specs
        if "placeholder" in entry.get("distribution_status", "")
    ]
    if placeholder_params:
        warnings.append(
            f"{len(placeholder_params)} sampled HTA parameter(s) still marked placeholder"
        )

    print("  WARNINGS:")
    for warning in warnings:
        print(f"    - {warning}")
    print()

    if dry_run:
        print("DRY RUN — stopping before simulation.")
        epi_summary = epi_sampler.get_parameter_summary()
        hta_summary = hta_sampler.get_parameter_summary()
        print(f"\nEpi parameter summary ({len(epi_summary)} params):")
        print(epi_summary[["path", "dist_type", "value"]].to_string(index=False))
        print(f"\nHTA parameter summary ({len(hta_summary)} params):")
        print(hta_summary[["path", "dist_type", "value"]].to_string(index=False))
        return None

    # 5. Generate draws matrices
    epi_draws = epi_sampler.generate_draws(n=n, seed=seed)
    hta_draws = hta_sampler.generate_draws(n=n, seed=seed + 1)  # offset seed

    # 6. Run PSA loop
    output_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    if workers == 1:
        all_draw_results, failed_draws = _run_psa_hta_sequential(
            epi_draws=epi_draws,
            hta_draws=hta_draws,
            base_config=base_config,
            hta_config=hta_config,
            seed=seed,
            analysis_scenario=analysis_scenario,
            freeze_year=freeze_year,
            eval_years=eval_years,
            quiet=quiet,
            n=n,
            t_start=t_start,
        )
    else:
        all_draw_results, failed_draws = _run_psa_hta_parallel(
            epi_draws=epi_draws,
            hta_draws=hta_draws,
            base_config=base_config,
            hta_config=hta_config,
            seed=seed,
            analysis_scenario=analysis_scenario,
            freeze_year=freeze_year,
            eval_years=eval_years,
            quiet=quiet,
            n=n,
            workers=workers,
            t_start=t_start,
        )

    total_time = time.time() - t_start
    n_ok = len(all_draw_results)
    print(f"\nPSA × HTA complete: {n_ok}/{n} draws in {total_time:.1f}s "
          f"({total_time / max(n, 1):.1f}s per draw)")
    if failed_draws:
        print(f"  WARNING: {len(failed_draws)} draws failed.")

    if n_ok == 0:
        raise RuntimeError("All draws failed — no results to report.")

    # 7. Collate results into DataFrames
    incremental_rows = []
    arm_rows = []

    for dr in all_draw_results:
        idx = dr["draw_index"]
        for comp_key, vals in dr["incremental"].items():
            incremental_rows.append({
                "draw_index": idx,
                "comparison": comp_key,
                **vals,
            })
        for arm_name, summary in dr["arm_results"].items():
            arm_rows.append({
                "draw_index": idx,
                "arm": arm_name,
                "total_qalys_disc": summary["total_qalys_disc"],
                "total_costs_disc": summary["total_costs_disc"],
                "total_costs_disc_societal": summary["total_costs_disc_societal"],
                "cumul_new_infections": summary["cumul_new_infections"],
            })

    incremental_df = pd.DataFrame(incremental_rows)
    arm_outcomes_df = pd.DataFrame(arm_rows)

    # 8. Write outputs
    from psa_hta_output_writer import PSAHTAOutputWriter
    writer = PSAHTAOutputWriter(
        incremental_df=incremental_df,
        arm_outcomes_df=arm_outcomes_df,
        epi_sampler=epi_sampler,
        hta_sampler=hta_sampler,
        psa_config=psa_config,
        hta_config=hta_config,
        output_dir=output_dir,
        failed_draws=failed_draws,
    )
    writer.write_all()

    return {"incremental_df": incremental_df, "arm_outcomes_df": arm_outcomes_df}


def _run_psa_hta_sequential(
    epi_draws, hta_draws, base_config, hta_config, seed, analysis_scenario,
    freeze_year, eval_years, quiet, n, t_start,
):
    """Run PSA × HTA draws sequentially in the current process."""
    all_draw_results = []
    failed_draws = []

    for i in range(n):
        try:
            epi_row = dict(epi_draws.iloc[i])
            hta_row = dict(hta_draws.iloc[i]) if len(hta_draws) > 0 else {}

            result = _run_single_hta_draw(
                draw_index=i,
                epi_draw_row=epi_row,
                hta_draw_row=hta_row,
                base_config=base_config,
                hta_config=hta_config,
                seed=seed,
                analysis_scenario=analysis_scenario,
                freeze_year=freeze_year,
                eval_years=eval_years,
                quiet=quiet,
            )
            all_draw_results.append(result)

        except Exception as e:
            import traceback
            failed_draws.append({
                "draw_index": i,
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
            if not quiet:
                print(f"\n  WARNING: Draw {i} failed: {e}")

        _report_progress(i, n, t_start)

    return all_draw_results, failed_draws


def _run_psa_hta_parallel(
    epi_draws, hta_draws, base_config, hta_config, seed, analysis_scenario,
    freeze_year, eval_years, quiet, n, workers, t_start,
):
    """Run PSA × HTA draws across processes, preserving draw order."""
    all_draw_results = [None] * n
    failed_draws = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i in range(n):
            epi_row = dict(epi_draws.iloc[i])
            hta_row = dict(hta_draws.iloc[i]) if len(hta_draws) > 0 else {}
            future = executor.submit(
                _run_single_hta_draw,
                i,
                epi_row,
                hta_row,
                base_config,
                hta_config,
                seed,
                analysis_scenario,
                freeze_year,
                eval_years,
                quiet,
            )
            futures[future] = i

        completed = 0
        for future in as_completed(futures):
            draw_idx = futures[future]
            try:
                all_draw_results[draw_idx] = future.result()
            except Exception as e:
                import traceback
                failed_draws.append({
                    "draw_index": draw_idx,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                })
                if not quiet:
                    print(f"\n  WARNING: Draw {draw_idx} failed: {e}")
            completed += 1
            _report_progress(completed - 1, n, t_start)

    return [r for r in all_draw_results if r is not None], failed_draws


def _report_progress(i, n, t_start):
    elapsed = time.time() - t_start
    rate = (i + 1) / elapsed if elapsed > 0 else 0
    eta = (n - i - 1) / rate if rate > 0 else 0
    print(f"  Draw {i+1}/{n}  |  {elapsed:.0f}s elapsed  |  "
          f"{rate:.2f} draws/s  |  ETA {eta:.0f}s", end="\r")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PSA × HTA — probabilistic sensitivity analysis over full CEA pipeline"
    )
    parser.add_argument("--config", default="configs/1980_test.json")
    parser.add_argument("--hta-config", default="configs/hta_config.json")
    parser.add_argument("--psa-config", default="configs/psa_config.json")
    parser.add_argument("--n-draws", type=int, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    run_psa_hta(
        config_path=args.config,
        hta_config_path=args.hta_config,
        psa_config_path=args.psa_config,
        n_draws_override=args.n_draws,
        output_dir_override=args.output_dir,
        seed_override=args.seed,
        scenario_name=args.scenario,
        dry_run=args.dry_run,
        quiet=args.quiet,
        workers=args.workers,
    )
