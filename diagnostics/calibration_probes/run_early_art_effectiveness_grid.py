#!/usr/bin/env python3
"""
Diagnostics-only probe for calendar-era ART mortality effectiveness.

This script runs existing calibrated candidate configs under a small set of
counterfactual ART mortality-reduction schedules. It does not alter calibration
spaces, LHS ranking, or candidate JSON files.
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

from audit_calibration_acceptance import candidate_metadata, load_json, run_candidate
from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "constant_0_90",
        "schedule": None,
        "constant": None,
        "rationale": "Current calibrated assumption.",
    },
    {
        "scenario": "constant_0_00_no_art_mortality_benefit",
        "schedule": None,
        "constant": 0.0,
        "rationale": "Upper-bound diagnostic: ART stock remains, but ART gives no mortality reduction.",
    },
    {
        "scenario": "art96_0_00_2005_0_90",
        "schedule": {1996: 0.00, 2005: 0.90},
        "constant": None,
        "rationale": "Upper-bound early-era attenuation with modern effectiveness restored by 2005.",
    },
    {
        "scenario": "art96_0_75_2000_0_85_2005_0_90",
        "schedule": {1996: 0.75, 2000: 0.85, 2005: 0.90},
        "constant": None,
        "rationale": "Moderate early-era attenuation with quick convergence.",
    },
    {
        "scenario": "art96_0_65_2000_0_80_2005_0_90",
        "schedule": {1996: 0.65, 2000: 0.80, 2005: 0.90},
        "constant": None,
        "rationale": "Central diagnostic stress test for early ART era.",
    },
    {
        "scenario": "art96_0_50_2000_0_75_2005_0_90",
        "schedule": {1996: 0.50, 2000: 0.75, 2005: 0.90},
        "constant": None,
        "rationale": "Strong early-era attenuation with quick convergence.",
    },
    {
        "scenario": "art96_0_65_2005_0_90",
        "schedule": {1996: 0.65, 2005: 0.90},
        "constant": None,
        "rationale": "Prolonged moderate attenuation through early 2000s.",
    },
    {
        "scenario": "art96_0_50_2005_0_90",
        "schedule": {1996: 0.50, 2005: 0.90},
        "constant": None,
        "rationale": "Prolonged strong attenuation through early 2000s.",
    },
    {
        "scenario": "art96_0_65_2010_0_90",
        "schedule": {1996: 0.65, 2010: 0.90},
        "constant": None,
        "rationale": "Long attenuation; useful as an upper-bound stress test.",
    },
)


def apply_art_mortality_schedule(
    config: dict,
    schedule: dict[int, float] | None,
    constant: float | None,
) -> dict:
    """Return a copied config with the requested ART mortality schedule applied."""
    patched = copy.deepcopy(config)
    art_mortality = (
        patched.setdefault("transmission_model", {})
        .setdefault("disease", {})
        .setdefault("advanced", {})
        .setdefault("art_mortality_reduction", {})
    )
    if constant is not None:
        art_mortality["value"] = float(constant)
        art_mortality.pop("schedule", None)
        return patched
    if schedule is None:
        art_mortality.pop("schedule", None)
        return patched
    art_mortality["schedule"] = {
        "value": {str(year): float(value) for year, value in schedule.items()},
        "interpolation": {"value": "stepwise"},
        "notes": "V13.6 diagnostics-only ART mortality-reduction calendar schedule.",
    }
    return patched


def scenario_value(schedule: dict[int, float] | None, year: int, baseline: float = 0.90) -> float:
    if not schedule:
        return baseline
    current = baseline
    for anchor_year in sorted(schedule):
        if year >= anchor_year:
            current = float(schedule[anchor_year])
    return current


def build_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = pd.read_csv(args.normalized_targets_csv).to_dict("records")
    settings = AcceptanceGateSettings()
    rows: list[dict[str, Any]] = []
    selected_output_years = (1990, 1993, 1996, 1997, 2000, 2005, 2010, 2015, 2020, 2023, 2024)

    for config_idx, config_path in enumerate(args.configs, start=1):
        source_config = load_json(config_path)
        for scenario_idx, scenario in enumerate(SCENARIOS, start=1):
            config = apply_art_mortality_schedule(
                source_config,
                scenario["schedule"],
                scenario["constant"],
            )
            seed = args.seed + config_idx * 100 + scenario_idx
            outputs = run_candidate(config, seed=seed, deterministic=args.deterministic)
            acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
            row = {
                "config_path": str(Path(config_path).resolve()),
                "config_name": Path(config_path).name,
                "scenario": scenario["scenario"],
                "scenario_rationale": scenario["rationale"],
                "schedule_1996": scenario_value(scenario["schedule"], 1996),
                "schedule_2000": scenario_value(scenario["schedule"], 2000),
                "schedule_2005": scenario_value(scenario["schedule"], 2005),
                "schedule_2010": scenario_value(scenario["schedule"], 2010),
                "audit_deterministic_runtime": bool(args.deterministic),
                **candidate_metadata(source_config),
                **acceptance_gate_row_fields(acceptance),
            }
            for output in (
                "aids_deaths",
                "new_aids_cases",
                "PLHIV",
                "new_infections",
                "new_diagnoses",
                "new_diagnoses_msm",
                "on_art_stock",
            ):
                for year in selected_output_years:
                    if year in outputs.get(output, {}):
                        row[f"{output}_{year}"] = outputs[output][year]
            rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diagnostics-only early ART effectiveness scenarios on existing candidate configs."
    )
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument(
        "--normalized-targets-csv",
        required=True,
        help="Normalized target CSV used by acceptance-gate diagnostics.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "early_art_effectiveness_grid_v13_6.csv"),
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
    print(f"Early ART effectiveness diagnostic written: {output_csv}")
    display_cols = [
        "config_name",
        "scenario",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
        "acceptance_aids_death_gate_bounds_fraction",
        "acceptance_aids_death_gate_peak_to_recent_mean_ratio",
        "aids_deaths_1997",
        "aids_deaths_2000",
        "aids_deaths_2005",
        "PLHIV_2023",
    ]
    print(df[[col for col in display_cols if col in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
