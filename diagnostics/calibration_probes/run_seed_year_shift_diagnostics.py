#!/usr/bin/env python3
"""
Diagnostics-only seed-year shift probe.

Tests whether moving the epidemic seed five years earlier improves early
AIDS-death fit when the simulation is also initialized with matching HMD
population data for the earlier start year.
"""

from __future__ import annotations

import argparse
import copy
import csv
import sys
from collections import defaultdict
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
from config_utils import extract_value
from run_historical_seed_diagnostics import (
    add_annual_stage_stocks,
    run_candidate_with_stage_stocks,
)
from calib_extractor import extract_annual_outputs
HMD_POP_PATH = ROOT / "Validation " / "HMD_NL_Pop.txt"
CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "baseline_1980_start_1980_seed",
        "start_year": 1980,
        "seed_year": 1980,
        "seed_prev_multiplier": 1.0,
        "use_hmd_start_population": False,
        "rationale": "Current candidate initialization.",
    },
    {
        "scenario": "start1975_seed1980_same_prev",
        "start_year": 1975,
        "seed_year": 1980,
        "seed_prev_multiplier": 1.0,
        "use_hmd_start_population": True,
        "rationale": "Demographic start-year control; seed timing unchanged.",
    },
    {
        "scenario": "start1975_seed1975_same_prev",
        "start_year": 1975,
        "seed_year": 1975,
        "seed_prev_multiplier": 1.0,
        "use_hmd_start_population": True,
        "rationale": "Five extra years of acute-seed epidemic maturation.",
    },
    {
        "scenario": "start1975_seed1975_2x_prev",
        "start_year": 1975,
        "seed_year": 1975,
        "seed_prev_multiplier": 2.0,
        "use_hmd_start_population": True,
        "rationale": "Five-year shift plus larger seed magnitude stress test.",
    },
)


def parse_age_bounds(label: str) -> tuple[int, int | None]:
    if label.endswith("+"):
        return int(label[:-1]), None
    left, right = label.split("-", 1)
    return int(left), int(right)


def model_age_label(age: int, labels: list[str]) -> str | None:
    for label in labels:
        lower, upper = parse_age_bounds(label)
        if upper is None:
            if age >= lower:
                return label
        elif lower <= age <= upper:
            return label
    return None


def hmd_age_sex_distribution(year: int, model_labels: list[str]) -> tuple[int, dict[str, dict[str, float]]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"female": 0, "male": 0})
    with HMD_POP_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Type", "").strip() != "O":
                continue
            if int(row["Year"]) != year:
                continue
            sex_raw = row.get("Sex", "").strip()
            if sex_raw not in {"m", "f"}:
                continue
            age_raw = row.get("Age", "").strip()
            if age_raw in {"TOT", "UNK"}:
                continue
            age = int(age_raw.rstrip("+"))
            label = model_age_label(age, model_labels)
            if label is None:
                continue
            sex = "male" if sex_raw == "m" else "female"
            counts[label][sex] += int(float(row["Population"]))
    total = sum(sex_counts[sex] for sex_counts in counts.values() for sex in ("female", "male"))
    if total <= 0:
        raise ValueError(f"No HMD Type O population rows found for {year}")
    distribution = {
        label: {
            "female": counts[label]["female"] / total,
            "male": counts[label]["male"] / total,
        }
        for label in model_labels
    }
    return total, distribution


def patch_scenario(config: dict, scenario: dict[str, Any]) -> dict:
    patched = copy.deepcopy(config)
    patched["run_control"]["start_year"]["value"] = int(scenario["start_year"])
    patched["run_control"]["end_year"]["value"] = CALIB_END_YEAR + 1
    seed_block = patched["population"]["epidemic_seed"]
    seed_block["seed_year"]["value"] = float(scenario["seed_year"])
    base_prev = float(extract_value(seed_block["seed_prevalence"]))
    seed_block["seed_prevalence"]["value"] = base_prev * float(scenario["seed_prev_multiplier"])

    if scenario["use_hmd_start_population"]:
        labels = list(patched["population"]["age_sex_distribution"]["value"].keys())
        total, distribution = hmd_age_sex_distribution(int(scenario["start_year"]), labels)
        patched["population"]["total_size"]["value"] = int(total)
        patched["population"]["total_size"]["notes"] = (
            f"Diagnostics-only HMD Netherlands Jan 1 {scenario['start_year']} total population."
        )
        patched["population"]["age_sex_distribution"]["value"] = distribution
        patched["population"]["age_sex_distribution"]["notes"] = (
            f"Diagnostics-only HMD Netherlands Jan 1 {scenario['start_year']} age-sex distribution."
        )
    return patched


def build_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = pd.read_csv(args.normalized_targets_csv).to_dict("records")
    settings = AcceptanceGateSettings()
    rows: list[dict[str, Any]] = []
    selected_years = (1980, 1985, 1990, 1993, 1995, 1996, 1997, 2000, 2005, 2010, 2015, 2020, 2023)

    for config_idx, config_path in enumerate(args.configs, start=1):
        source_config = load_json(config_path)
        base_prev = float(extract_value(source_config["population"]["epidemic_seed"]["seed_prevalence"]))
        for scenario_idx, scenario in enumerate(SCENARIOS, start=1):
            config = patch_scenario(source_config, scenario)
            seed = args.seed + config_idx * 100 + scenario_idx
            result = run_candidate_with_stage_stocks(
                config,
                seed=seed,
                deterministic=args.deterministic,
                start_year=int(scenario["start_year"]),
                end_year=CALIB_END_YEAR,
            )
            outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
            add_annual_stage_stocks(outputs, result["stage_stocks"])
            acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
            row = {
                "config_path": str(Path(config_path).resolve()),
                "config_name": Path(config_path).name,
                "scenario": scenario["scenario"],
                "scenario_rationale": scenario["rationale"],
                "start_year": scenario["start_year"],
                "seed_year": scenario["seed_year"],
                "seed_prevalence": base_prev * float(scenario["seed_prev_multiplier"]),
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
        description="Run diagnostics-only five-year epidemic seed shift scenarios."
    )
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument("--normalized-targets-csv", required=True)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "seed_year_shift_diagnostics_v13_6.csv"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    args = parser.parse_args(argv)
    if not HMD_POP_PATH.exists():
        parser.error(f"HMD population file not found: {HMD_POP_PATH}")
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
    print(f"Seed-year shift diagnostic written: {output_csv}")
    display_cols = [
        "config_name",
        "scenario",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
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
