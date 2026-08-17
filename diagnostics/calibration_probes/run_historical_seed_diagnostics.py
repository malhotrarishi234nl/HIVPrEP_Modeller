#!/usr/bin/env python3
"""
Diagnostics-only historical seed-stage probe.

This runner tests whether the early AIDS-death deficit is driven by insufficient
mature HIV/AIDS stock at the start of the historical simulation. It mutates
candidate configs only in memory and writes audit CSVs under outputs/.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_calibration_acceptance import candidate_metadata, load_json, set_nested
from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_extractor import extract_annual_outputs
from config_utils import extract_value
from run_v3 import SimulationEngineV3
CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "baseline_acute_current_prev",
        "seed_stage": None,
        "seed_prev_multiplier": 1.0,
        "rationale": "Current candidate seed stage and prevalence.",
    },
    {
        "scenario": "chronic_current_prev",
        "seed_stage": "Chronic",
        "seed_prev_multiplier": 1.0,
        "rationale": "Same seed magnitude, but introduced as established chronic HIV.",
    },
    {
        "scenario": "aids_current_prev",
        "seed_stage": "AIDS",
        "seed_prev_multiplier": 1.0,
        "rationale": "Upper-bound stage-maturity diagnostic at current seed magnitude.",
    },
    {
        "scenario": "chronic_2x_prev",
        "seed_stage": "Chronic",
        "seed_prev_multiplier": 2.0,
        "rationale": "Established chronic seed with twice current seed prevalence.",
    },
    {
        "scenario": "aids_2x_prev",
        "seed_stage": "AIDS",
        "seed_prev_multiplier": 2.0,
        "rationale": "Upper-bound mature AIDS stock with twice current seed prevalence.",
    },
    {
        "scenario": "chronic_5x_prev",
        "seed_stage": "Chronic",
        "seed_prev_multiplier": 5.0,
        "rationale": "Stress test for whether larger established stock can recover early deaths.",
    },
    {
        "scenario": "aids_5x_prev",
        "seed_stage": "AIDS",
        "seed_prev_multiplier": 5.0,
        "rationale": "Extreme upper-bound; not a candidate production mechanism.",
    },
)


def patch_seed_scenario(config: dict, scenario: dict[str, Any]) -> tuple[dict, float, str]:
    patched = copy.deepcopy(config)
    seed_block = patched["population"]["epidemic_seed"]
    base_prev = float(extract_value(seed_block["seed_prevalence"]))
    scenario_prev = base_prev * float(scenario["seed_prev_multiplier"])
    seed_block["seed_prevalence"]["value"] = scenario_prev
    if scenario["seed_stage"] is not None:
        seed_block["seed_stage"]["value"] = scenario["seed_stage"]
    return patched, scenario_prev, str(extract_value(seed_block["seed_stage"]))


def stage_stocks(engine: SimulationEngineV3) -> dict[str, float]:
    pop = engine.state.population
    hiv_to_idx = engine.groups.hiv_to_idx
    out: dict[str, float] = {}
    for stage in ("Acute", "Chronic", "AIDS"):
        idx = hiv_to_idx[stage]
        out[f"{stage.lower()}_stock"] = float(np.sum(pop[:, :, :, idx, :, :, :]))
    aids_idx = hiv_to_idx["AIDS"]
    out["aids_stock_off_art"] = float(np.sum(pop[:, :, :, aids_idx, :, 0, :]))
    out["aids_stock_on_art"] = float(np.sum(pop[:, :, :, aids_idx, :, 1, :]))
    out["plhiv_stock_direct"] = sum(out.values())
    out["plhiv_stock_direct"] -= out["aids_stock_off_art"] + out["aids_stock_on_art"]
    return out


def run_candidate_with_stage_stocks(
    config: dict,
    seed: int,
    deterministic: bool,
    start_year: int = CALIB_START_YEAR,
    end_year: int = CALIB_END_YEAR,
) -> dict[str, Any]:
    if deterministic:
        set_nested(config, "run_control.deterministic_mode.value", True)
    set_nested(config, "run_control.start_year.value", start_year)
    set_nested(config, "run_control.end_year.value", end_year + 1)
    set_nested(config, "output.enabled.value", False)
    set_nested(config, "output.terminal_summary.value", False)

    stage_rows: list[dict[str, float]] = []
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(config, seed=seed)
        for step in range(engine.time.n_steps):
            engine.step_simulation(step)
            engine.validate_prep_mass_balance(step)
            engine.validate_art_mass_balance(step)
            year = engine.time.start_year + (step * engine.time.years_per_step)
            stage_rows.append({"year": year, **stage_stocks(engine)})
    summary = pd.DataFrame(engine.outputs)
    return {
        "summary": summary,
        "stage_stocks": pd.DataFrame(stage_rows),
        "final_state": engine.state.population.copy(),
        "seed": seed,
    }


def add_annual_stage_stocks(outputs: dict[str, dict[int, float]], stage_df: pd.DataFrame) -> None:
    work = stage_df.sort_values("year").copy()
    work["calib_year"] = work["year"].astype(float).apply(int)
    work = work[(work["calib_year"] >= CALIB_START_YEAR) & (work["calib_year"] <= CALIB_END_YEAR)]
    for column in (
        "acute_stock",
        "chronic_stock",
        "aids_stock",
        "aids_stock_off_art",
        "aids_stock_on_art",
        "plhiv_stock_direct",
    ):
        outputs[column] = {}
    for year, year_df in work.groupby("calib_year", sort=True):
        final_row = year_df.iloc[-1]
        for column in (
            "acute_stock",
            "chronic_stock",
            "aids_stock",
            "aids_stock_off_art",
            "aids_stock_on_art",
            "plhiv_stock_direct",
        ):
            outputs[column][int(year)] = float(final_row[column])


def build_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = pd.read_csv(args.normalized_targets_csv).to_dict("records")
    settings = AcceptanceGateSettings()
    rows: list[dict[str, Any]] = []
    selected_years = (1985, 1990, 1993, 1995, 1996, 1997, 2000, 2005, 2010, 2015, 2020, 2023)

    for config_idx, config_path in enumerate(args.configs, start=1):
        source_config = load_json(config_path)
        for scenario_idx, scenario in enumerate(SCENARIOS, start=1):
            config, seed_prev, seed_stage = patch_seed_scenario(source_config, scenario)
            seed = args.seed + config_idx * 100 + scenario_idx
            result = run_candidate_with_stage_stocks(config, seed=seed, deterministic=args.deterministic)
            outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
            add_annual_stage_stocks(outputs, result["stage_stocks"])
            acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
            row = {
                "config_path": str(Path(config_path).resolve()),
                "config_name": Path(config_path).name,
                "scenario": scenario["scenario"],
                "scenario_rationale": scenario["rationale"],
                "seed_stage": seed_stage,
                "seed_prevalence": seed_prev,
                "audit_deterministic_runtime": bool(args.deterministic),
                **candidate_metadata(source_config),
                **acceptance_gate_row_fields(acceptance),
            }
            for output in (
                "PLHIV",
                "new_infections",
                "new_diagnoses",
                "new_diagnoses_msm",
                "new_aids_cases",
                "aids_deaths",
                "on_art_stock",
                "acute_stock",
                "chronic_stock",
                "aids_stock",
                "aids_stock_off_art",
                "aids_stock_on_art",
                "plhiv_stock_direct",
            ):
                for year in selected_years:
                    if year in outputs.get(output, {}):
                        row[f"{output}_{year}"] = outputs[output][year]
            rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diagnostics-only historical HIV seed-stage scenarios."
    )
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument(
        "--normalized-targets-csv",
        required=True,
        help="Normalized target CSV used by acceptance-gate diagnostics.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "historical_seed_diagnostics_v13_6.csv"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    args = parser.parse_args(argv)
    if not Path(args.normalized_targets_csv).exists():
        parser.error(f"--normalized-targets-csv not found: {args.normalized_targets_csv}")
    missing = [path for path in args.configs if not Path(path).exists()]
    if missing:
        parser.error(f"Config path(s) not found: {', '.join(missing)}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = build_rows(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"Historical seed diagnostic written: {output_csv}")
    display_cols = [
        "config_name",
        "scenario",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
        "seed_stage",
        "seed_prevalence",
        "aids_stock_1990",
        "aids_stock_1995",
        "aids_stock_off_art_1995",
        "aids_stock_on_art_1995",
        "new_aids_cases_1995",
        "aids_deaths_1995",
        "aids_deaths_1997",
        "PLHIV_2023",
    ]
    print(df[[col for col in display_cols if col in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
