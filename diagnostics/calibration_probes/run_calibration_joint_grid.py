#!/usr/bin/env python3
"""
Run a small diagnostics-only joint calibration grid.

This is not a calibration runner. It applies named, temporary in-memory
mechanism/care-timing scenarios to existing candidate configs and evaluates the
acceptance gates. Candidate JSON files and calibration spaces are not modified.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_calibration_acceptance import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    candidate_metadata,
    load_targets,
    run_candidate,
    set_nested,
)
from calib_acceptance_gates import evaluate_acceptance_gates


DIAGNOSIS_1985 = "transmission_model.disease.diagnosis.schedule.value.1985"
DIAGNOSIS_1990 = "transmission_model.disease.diagnosis.schedule.value.1990"
DIAGNOSIS_1997 = "transmission_model.disease.diagnosis.schedule.value.1997"
DIAGNOSIS_2015 = "transmission_model.disease.diagnosis.schedule.value.2015"
ART_1996 = "interventions.art.schedule.initiation.value.1996"
ART_2000 = "interventions.art.schedule.initiation.value.2000"
ART_2015 = "interventions.art.schedule.initiation.value.2015"
ART_2020 = "interventions.art.schedule.initiation.value.2020"
ART_DROPOUT = "interventions.art.advanced.art_dropout_rate.value"
CHRONIC_TO_AIDS = "transmission_model.disease.progression.chronic_to_aids.value"
CHRONIC_TO_AIDS_ON_ART = "transmission_model.disease.progression.chronic_to_aids_on_art.value"
AIDS_MORTALITY = "transmission_model.disease.mortality.aids.value"
ART_MORTALITY_REDUCTION = "transmission_model.disease.advanced.art_mortality_reduction.value"


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "modern_care_mild",
        "scenario_class": "care_timing_refined",
        "notes": "Small modern care increase to look for a smoother incidence/stock tradeoff.",
        "overrides": {
            DIAGNOSIS_2015: 0.40,
            ART_2015: 0.72,
            ART_2020: 0.94,
            ART_DROPOUT: 0.004,
        },
    },
    {
        "scenario": "modern_care_medium",
        "scenario_class": "care_timing_refined",
        "notes": "Moderate modern care increase between current candidates and the broad high-care probe.",
        "overrides": {
            DIAGNOSIS_2015: 0.48,
            ART_2015: 0.80,
            ART_2020: 0.96,
            ART_DROPOUT: 0.002,
        },
    },
    {
        "scenario": "early_delay_mild",
        "scenario_class": "care_timing_refined",
        "notes": "Mild early-care delay to test AIDS-death peak recovery without catastrophic stock movement.",
        "overrides": {
            DIAGNOSIS_1985: 0.08,
            DIAGNOSIS_1990: 0.20,
            DIAGNOSIS_1997: 0.30,
            ART_1996: 0.35,
            ART_2000: 0.50,
        },
    },
    {
        "scenario": "mild_compression",
        "scenario_class": "care_timing_refined",
        "notes": "Mild early-care delay plus moderate modern care increase.",
        "overrides": {
            DIAGNOSIS_1985: 0.08,
            DIAGNOSIS_1990: 0.20,
            DIAGNOSIS_1997: 0.30,
            DIAGNOSIS_2015: 0.48,
            ART_1996: 0.35,
            ART_2000: 0.50,
            ART_2015: 0.80,
            ART_2020: 0.96,
            ART_DROPOUT: 0.002,
        },
    },
    {
        "scenario": "mild_compression_disease_local",
        "scenario_class": "joint_refined",
        "notes": "Mild care compression plus local upper disease severity.",
        "overrides": {
            DIAGNOSIS_1985: 0.08,
            DIAGNOSIS_1990: 0.20,
            DIAGNOSIS_1997: 0.30,
            DIAGNOSIS_2015: 0.48,
            ART_1996: 0.35,
            ART_2000: 0.50,
            ART_2015: 0.80,
            ART_2020: 0.96,
            ART_DROPOUT: 0.002,
            CHRONIC_TO_AIDS: 0.0703,
            AIDS_MORTALITY: 0.2444,
        },
    },
    {
        "scenario": "mild_compression_on_art_progression_up",
        "scenario_class": "joint_refined",
        "notes": "Mild care compression plus higher on-ART AIDS progression.",
        "overrides": {
            DIAGNOSIS_1985: 0.08,
            DIAGNOSIS_1990: 0.20,
            DIAGNOSIS_1997: 0.30,
            DIAGNOSIS_2015: 0.48,
            ART_1996: 0.35,
            ART_2000: 0.50,
            ART_2015: 0.80,
            ART_2020: 0.96,
            ART_DROPOUT: 0.002,
            CHRONIC_TO_AIDS_ON_ART: 0.015,
        },
    },
    {
        "scenario": "mild_compression_art_mortality_reduction_low",
        "scenario_class": "joint_refined",
        "notes": "Mild care compression plus lower ART mortality protection.",
        "overrides": {
            DIAGNOSIS_1985: 0.08,
            DIAGNOSIS_1990: 0.20,
            DIAGNOSIS_1997: 0.30,
            DIAGNOSIS_2015: 0.48,
            ART_1996: 0.35,
            ART_2000: 0.50,
            ART_2015: 0.80,
            ART_2020: 0.96,
            ART_DROPOUT: 0.002,
            ART_MORTALITY_REDUCTION: 0.85,
        },
    },
    {
        "scenario": "modern_care_high",
        "scenario_class": "care_timing",
        "notes": "Raise modern diagnosis/ART and remove ART dropout to test recent incidence control.",
        "overrides": {
            DIAGNOSIS_2015: 0.60,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
        },
    },
    {
        "scenario": "early_care_delayed",
        "scenario_class": "care_timing",
        "notes": "Delay early diagnosis and ART to test whether the missing AIDS-death peak is care-timing driven.",
        "overrides": {
            DIAGNOSIS_1985: 0.05,
            DIAGNOSIS_1990: 0.12,
            DIAGNOSIS_1997: 0.25,
            ART_1996: 0.10,
            ART_2000: 0.35,
        },
    },
    {
        "scenario": "delayed_early_modern_high",
        "scenario_class": "care_timing",
        "notes": "Combine delayed early care with strong modern care.",
        "overrides": {
            DIAGNOSIS_1985: 0.05,
            DIAGNOSIS_1990: 0.12,
            DIAGNOSIS_1997: 0.25,
            DIAGNOSIS_2015: 0.60,
            ART_1996: 0.10,
            ART_2000: 0.35,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
        },
    },
    {
        "scenario": "care_compression",
        "scenario_class": "care_timing",
        "notes": "Low early care and high late care to test stronger historical compression.",
        "overrides": {
            DIAGNOSIS_1985: 0.05,
            DIAGNOSIS_1990: 0.15,
            DIAGNOSIS_1997: 0.45,
            DIAGNOSIS_2015: 0.65,
            ART_1996: 0.05,
            ART_2000: 0.30,
            ART_2015: 0.95,
            ART_2020: 0.99,
            ART_DROPOUT: 0.0,
        },
    },
    {
        "scenario": "disease_upper_local",
        "scenario_class": "disease",
        "notes": "Move chronic progression and AIDS mortality to the current local upper evidence bounds.",
        "overrides": {
            CHRONIC_TO_AIDS: 0.0703,
            AIDS_MORTALITY: 0.2444,
        },
    },
    {
        "scenario": "disease_upper_wide_probe",
        "scenario_class": "disease",
        "notes": "Wider diagnostic probe only; checks whether stronger disease severity can recover deaths at all.",
        "overrides": {
            CHRONIC_TO_AIDS: 0.08,
            AIDS_MORTALITY: 0.25,
        },
    },
    {
        "scenario": "delayed_early_modern_high_disease_local",
        "scenario_class": "joint",
        "notes": "Care compression plus local upper disease severity.",
        "overrides": {
            DIAGNOSIS_1985: 0.05,
            DIAGNOSIS_1990: 0.12,
            DIAGNOSIS_1997: 0.25,
            DIAGNOSIS_2015: 0.60,
            ART_1996: 0.10,
            ART_2000: 0.35,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
            CHRONIC_TO_AIDS: 0.0703,
            AIDS_MORTALITY: 0.2444,
        },
    },
    {
        "scenario": "delayed_early_modern_high_disease_wide",
        "scenario_class": "joint",
        "notes": "Care compression plus wider disease-severity probe.",
        "overrides": {
            DIAGNOSIS_1985: 0.05,
            DIAGNOSIS_1990: 0.12,
            DIAGNOSIS_1997: 0.25,
            DIAGNOSIS_2015: 0.60,
            ART_1996: 0.10,
            ART_2000: 0.35,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
            CHRONIC_TO_AIDS: 0.08,
            AIDS_MORTALITY: 0.25,
        },
    },
    {
        "scenario": "modern_high_on_art_progression_up",
        "scenario_class": "joint",
        "notes": "Strong modern care with higher on-ART AIDS progression.",
        "overrides": {
            DIAGNOSIS_2015: 0.60,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
            CHRONIC_TO_AIDS_ON_ART: 0.015,
        },
    },
    {
        "scenario": "modern_high_art_mortality_reduction_low",
        "scenario_class": "joint",
        "notes": "Strong modern care with less ART mortality protection.",
        "overrides": {
            DIAGNOSIS_2015: 0.60,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
            ART_MORTALITY_REDUCTION: 0.85,
        },
    },
    {
        "scenario": "modern_high_art_mortality_reduction_high",
        "scenario_class": "joint",
        "notes": "Strong modern care with greater ART mortality protection.",
        "overrides": {
            DIAGNOSIS_2015: 0.60,
            ART_2015: 0.90,
            ART_2020: 0.98,
            ART_DROPOUT: 0.0,
            ART_MORTALITY_REDUCTION: 0.97,
        },
    },
    {
        "scenario": "retention_high_dropout",
        "scenario_class": "care_timing",
        "notes": "Higher ART dropout stress test within the current calibration range.",
        "overrides": {
            ART_DROPOUT: 0.02,
        },
    },
)

KEY_OUTPUTS = (
    ("PLHIV", 2023),
    ("new_infections", 2023),
    ("aids_deaths", 1995),
    ("aids_deaths", 2023),
    ("new_diagnoses", 2024),
    ("new_diagnoses_msm", 2024),
    ("on_art_stock", 2023),
)


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def settings_from_args(args: argparse.Namespace) -> AcceptanceGateSettings:
    return AcceptanceGateSettings(
        diagnosis_floor=args.diagnosis_floor,
        diagnosis_ceiling=args.diagnosis_ceiling,
        plhiv_recent_mape_ceiling=args.plhiv_recent_mape_ceiling,
        incidence_recent_ratio_floor=args.incidence_recent_ratio_floor,
        incidence_recent_ratio_ceiling=args.incidence_recent_ratio_ceiling,
        aids_bounds_fraction_floor=args.aids_bounds_fraction_floor,
        art_recent_mape_ceiling=args.art_recent_mape_ceiling,
    )


def output_fields(outputs: dict[str, dict[int, float]]) -> dict:
    fields = {}
    for output, year in KEY_OUTPUTS:
        fields[f"{output}_{year}"] = outputs.get(output, {}).get(year, "")
    return fields


def override_field(scenario: dict) -> str:
    return ";".join(f"{path}={value}" for path, value in scenario["overrides"].items())


def evaluate_config(config: dict, targets: list[dict], args: argparse.Namespace, seed: int) -> tuple[dict, dict]:
    outputs = run_candidate(config, seed=seed, deterministic=args.deterministic)
    acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings_from_args(args))
    return outputs, acceptance_gate_row_fields(acceptance)


def joint_grid_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = load_targets(args)
    rows = []
    scenarios = list(SCENARIOS)

    for baseline_idx, baseline_path in enumerate(args.baselines, start=1):
        config_path = Path(baseline_path).resolve()
        baseline_config = load_json(config_path)
        outputs, gate_fields = evaluate_config(
            copy.deepcopy(baseline_config),
            targets,
            args,
            seed=args.seed + baseline_idx * 1000,
        )
        rows.append(
            {
                "baseline_config_path": str(config_path),
                "scenario": "baseline",
                "scenario_class": "baseline",
                "notes": "",
                "overrides": "",
                **candidate_metadata(baseline_config),
                **gate_fields,
                **output_fields(outputs),
            }
        )

        for scenario_idx, scenario in enumerate(scenarios, start=1):
            scenario_config = copy.deepcopy(baseline_config)
            for path, value in scenario["overrides"].items():
                set_nested(scenario_config, path, value)
            outputs, gate_fields = evaluate_config(
                scenario_config,
                targets,
                args,
                seed=args.seed + baseline_idx * 1000 + scenario_idx,
            )
            rows.append(
                {
                    "baseline_config_path": str(config_path),
                    "scenario": scenario["scenario"],
                    "scenario_class": scenario["scenario_class"],
                    "notes": scenario["notes"],
                    "overrides": override_field(scenario),
                    **candidate_metadata(baseline_config),
                    **gate_fields,
                    **output_fields(outputs),
                }
            )

    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a focused joint calibration diagnostics grid.")
    parser.add_argument("baselines", nargs="+", help="Baseline candidate config JSON paths.")
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "calibration_joint_grid_v13_6.csv"),
    )
    parser.add_argument("--output-xlsx", default=None)
    parser.add_argument("--normalized-targets-csv", default=None)
    parser.add_argument("--targets-workbook", default=None)
    parser.add_argument("--target-mapping", default=None)
    parser.add_argument("--stage", default="joint")
    parser.add_argument("--seed", type=int, default=6262)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    parser.add_argument("--diagnosis-floor", type=float, default=0.90)
    parser.add_argument("--diagnosis-ceiling", type=float, default=1.50)
    parser.add_argument("--plhiv-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument("--incidence-recent-ratio-floor", type=float, default=0.50)
    parser.add_argument("--incidence-recent-ratio-ceiling", type=float, default=1.75)
    parser.add_argument("--aids-bounds-fraction-floor", type=float, default=0.40)
    parser.add_argument("--art-recent-mape-ceiling", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = joint_grid_rows(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    if args.output_xlsx:
        output_xlsx = Path(args.output_xlsx)
        output_xlsx.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(output_xlsx, index=False)
    print(f"Calibration joint grid written: {output_csv}")
    summary_cols = [
        "baseline_config_path",
        "scenario",
        "scenario_class",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
        "acceptance_fail_reasons",
    ]
    print(df[[col for col in summary_cols if col in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
