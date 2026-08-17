#!/usr/bin/env python3
"""
Diagnostics-only seed prevalence x stage-share probe.

This tests whether increasing the actual seed_prevalence value, while using
simple interpretable Acute/Chronic/AIDS shares, shows enough promise to justify
a larger recalibration run. The relative scale is reported only for audit
readability; it is not a proposed production config key.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_calibration_acceptance import candidate_metadata, load_json
from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_extractor import extract_annual_outputs
from config_utils import extract_value
from run_historical_seed_diagnostics import add_annual_stage_stocks
from run_seed_distribution_diagnostics import run_candidate_with_distribution_seed
CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "acute100_seed_1p00",
        "seed_scale": 1.00,
        "shares": {"Acute": 1.00, "Chronic": 0.00, "AIDS": 0.00},
        "rationale": "Current all-acute seed at candidate seed prevalence.",
    },
    {
        "scenario": "acute100_seed_1p25",
        "seed_scale": 1.25,
        "shares": {"Acute": 1.00, "Chronic": 0.00, "AIDS": 0.00},
        "rationale": "Small increase in total acute seed prevalence.",
    },
    {
        "scenario": "acute100_seed_1p50",
        "seed_scale": 1.50,
        "shares": {"Acute": 1.00, "Chronic": 0.00, "AIDS": 0.00},
        "rationale": "Moderate increase in total acute seed prevalence.",
    },
    {
        "scenario": "acute100_seed_2p00",
        "seed_scale": 2.00,
        "shares": {"Acute": 1.00, "Chronic": 0.00, "AIDS": 0.00},
        "rationale": "Upper quick-look increase in total acute seed prevalence.",
    },
    {
        "scenario": "acute95_aids05_seed_1p25",
        "seed_scale": 1.25,
        "shares": {"Acute": 0.95, "Chronic": 0.00, "AIDS": 0.05},
        "rationale": "Small total seed increase with limited mature AIDS share.",
    },
    {
        "scenario": "acute95_aids05_seed_1p50",
        "seed_scale": 1.50,
        "shares": {"Acute": 0.95, "Chronic": 0.00, "AIDS": 0.05},
        "rationale": "Moderate total seed increase with limited mature AIDS share.",
    },
    {
        "scenario": "acute95_aids05_seed_2p00",
        "seed_scale": 2.00,
        "shares": {"Acute": 0.95, "Chronic": 0.00, "AIDS": 0.05},
        "rationale": "Upper quick-look increase with limited mature AIDS share.",
    },
    {
        "scenario": "acute90_chronic05_aids05_seed_1p50",
        "seed_scale": 1.50,
        "shares": {"Acute": 0.90, "Chronic": 0.05, "AIDS": 0.05},
        "rationale": "Moderate total seed increase with mixed mature shares.",
    },
)


def patch_seed_prevalence(config: dict, seed_prevalence: float) -> dict:
    patched = copy.deepcopy(config)
    patched["population"]["epidemic_seed"]["seed_prevalence"]["value"] = float(seed_prevalence)
    return patched


def build_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = pd.read_csv(args.normalized_targets_csv).to_dict("records")
    settings = AcceptanceGateSettings()
    rows: list[dict[str, Any]] = []
    selected_years = (1985, 1990, 1993, 1995, 1996, 1997, 2000, 2005, 2010, 2015, 2020, 2023)

    for config_idx, config_path in enumerate(args.configs, start=1):
        source_config = load_json(config_path)
        base_seed_prevalence = float(
            extract_value(source_config["population"]["epidemic_seed"]["seed_prevalence"])
        )
        for scenario_idx, scenario in enumerate(SCENARIOS, start=1):
            seed_prevalence = base_seed_prevalence * float(scenario["seed_scale"])
            config = patch_seed_prevalence(source_config, seed_prevalence)
            seed = args.seed + config_idx * 100 + scenario_idx
            result = run_candidate_with_distribution_seed(
                config,
                seed=seed,
                deterministic=args.deterministic,
                shares=scenario["shares"],
            )
            outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
            add_annual_stage_stocks(outputs, result["stage_stocks"])
            acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
            row = {
                "config_path": str(Path(config_path).resolve()),
                "config_name": Path(config_path).name,
                "scenario": scenario["scenario"],
                "scenario_rationale": scenario["rationale"],
                "base_seed_prevalence": base_seed_prevalence,
                "seed_prevalence": seed_prevalence,
                "seed_prevalence_relative_to_candidate": scenario["seed_scale"],
                "seed_acute_share": scenario["shares"]["Acute"],
                "seed_chronic_share": scenario["shares"]["Chronic"],
                "seed_aids_share": scenario["shares"]["AIDS"],
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
                "aids_stock",
                "aids_stock_off_art",
                "aids_stock_on_art",
            ):
                for year in selected_years:
                    if year in outputs.get(output, {}):
                        row[f"{output}_{year}"] = outputs[output][year]
            rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diagnostics-only seed prevalence and stage-share scenarios."
    )
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument("--normalized-targets-csv", required=True)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "seed_prevalence_grid_diagnostics_v13_6.csv"),
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
    print(f"Seed prevalence grid diagnostic written: {output_csv}")
    display_cols = [
        "config_name",
        "scenario",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
        "seed_prevalence",
        "aids_stock_1995",
        "aids_deaths_1995",
        "new_aids_cases_1995",
        "aids_deaths_1997",
        "PLHIV_2023",
    ]
    print(df[[col for col in display_cols if col in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
