#!/usr/bin/env python3
"""
Run diagnostics-only one-at-a-time calibration sensitivity grids.

This script never writes modified candidate configs. It loads a baseline config,
applies temporary in-memory overrides, runs the model, evaluates acceptance
gates, and writes one row per perturbation.
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
from config_utils import extract_value


DEFAULT_DISEASE_GRID: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    (
        "chronic_to_aids",
        "transmission_model.disease.progression.chronic_to_aids.value",
        (0.02, 0.05, 0.0683, 0.08, 0.12),
    ),
    (
        "chronic_to_aids_on_art",
        "transmission_model.disease.progression.chronic_to_aids_on_art.value",
        (0.001, 0.005, 0.015),
    ),
    (
        "aids_mortality",
        "transmission_model.disease.mortality.aids.value",
        (0.15, 0.2193, 0.25, 0.35, 0.45),
    ),
    (
        "art_mortality_reduction",
        "transmission_model.disease.advanced.art_mortality_reduction.value",
        (0.75, 0.85, 0.90, 0.97),
    ),
)

KEY_METRICS = (
    "acceptance_incidence_gate_recent_median_model_target_ratio",
    "acceptance_aids_death_gate_bounds_fraction",
    "acceptance_aids_death_gate_peak_to_recent_mean_ratio",
    "acceptance_plhiv_gate_recent_median_abs_pct",
    "acceptance_art_stock_gate_recent_median_abs_pct",
    "acceptance_diagnosis_gate_new_diagnoses_ratio",
    "acceptance_diagnosis_gate_new_diagnoses_msm_ratio",
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


def get_nested(config: dict, path: str) -> Any:
    obj: Any = config
    for token in path.split("."):
        obj = obj[token]
    return obj


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def custom_grid_entries(overrides: Sequence[str] | None) -> list[tuple[str, str, tuple[Any, ...]]]:
    entries = []
    for idx, override in enumerate(overrides or [], start=1):
        if "=" not in override:
            raise ValueError(f"Override must be PATH=VALUE, got: {override}")
        path, raw_value = override.split("=", 1)
        entries.append((f"custom_{idx}", path, (parse_value(raw_value),)))
    return entries


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


def evaluate_config(config: dict, targets: list[dict], args: argparse.Namespace, seed: int) -> tuple[dict, dict]:
    outputs = run_candidate(config, seed=seed, deterministic=args.deterministic)
    acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings_from_args(args))
    return outputs, acceptance_gate_row_fields(acceptance)


def output_fields(outputs: dict[str, dict[int, float]]) -> dict:
    fields = {}
    for output, year in KEY_OUTPUTS:
        fields[f"{output}_{year}"] = outputs.get(output, {}).get(year, "")
    return fields


def delta_fields(row: dict, baseline_row: dict) -> dict:
    fields = {}
    for key in KEY_METRICS:
        if key in row and key in baseline_row:
            try:
                fields[f"delta_{key}"] = float(row[key]) - float(baseline_row[key])
            except (TypeError, ValueError):
                pass
    for output, year in KEY_OUTPUTS:
        key = f"{output}_{year}"
        if key in row and key in baseline_row and row[key] != "" and baseline_row[key] != "":
            fields[f"delta_{key}"] = float(row[key]) - float(baseline_row[key])
    return fields


def sensitivity_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = load_targets(args)
    grid = list(DEFAULT_DISEASE_GRID if args.default_disease_grid else ())
    grid.extend(custom_grid_entries(args.override))
    rows = []

    for baseline_idx, baseline_path in enumerate(args.baselines, start=1):
        config_path = Path(baseline_path).resolve()
        baseline_config = load_json(config_path)
        outputs, gate_fields = evaluate_config(
            copy.deepcopy(baseline_config),
            targets,
            args,
            seed=args.seed + baseline_idx * 1000,
        )
        baseline_row = {
            "baseline_config_path": str(config_path),
            "scenario": "baseline",
            "lever": "",
            "config_path": "",
            "baseline_value": "",
            "override_value": "",
            **candidate_metadata(baseline_config),
            **gate_fields,
            **output_fields(outputs),
        }
        rows.append(baseline_row)

        for lever_idx, (lever, path, values) in enumerate(grid, start=1):
            try:
                baseline_value = extract_value(get_nested(baseline_config, path))
            except KeyError:
                baseline_value = ""
            for value_idx, value in enumerate(values, start=1):
                scenario_config = copy.deepcopy(baseline_config)
                set_nested(scenario_config, path, value)
                outputs, gate_fields = evaluate_config(
                    scenario_config,
                    targets,
                    args,
                    seed=args.seed + baseline_idx * 1000 + lever_idx * 100 + value_idx,
                )
                row = {
                    "baseline_config_path": str(config_path),
                    "scenario": "override",
                    "lever": lever,
                    "config_path": path,
                    "baseline_value": baseline_value,
                    "override_value": value,
                    **candidate_metadata(baseline_config),
                    **gate_fields,
                    **output_fields(outputs),
                }
                row.update(delta_fields(row, baseline_row))
                rows.append(row)

    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-at-a-time calibration sensitivity diagnostics.")
    parser.add_argument("baselines", nargs="+", help="Baseline candidate config JSON paths.")
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "calibration_sensitivity_grid_v13_6.csv"),
    )
    parser.add_argument("--output-xlsx", default=None)
    parser.add_argument("--normalized-targets-csv", default=None)
    parser.add_argument("--targets-workbook", default=None)
    parser.add_argument("--target-mapping", default=None)
    parser.add_argument("--stage", default="joint")
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    parser.add_argument("--default-disease-grid", action="store_true", default=True)
    parser.add_argument("--no-default-disease-grid", dest="default_disease_grid", action="store_false")
    parser.add_argument(
        "--override",
        action="append",
        help="Additional one-off override as CONFIG.PATH=JSON_VALUE. May be supplied more than once.",
    )
    parser.add_argument("--diagnosis-floor", type=float, default=0.90)
    parser.add_argument("--diagnosis-ceiling", type=float, default=1.50)
    parser.add_argument("--plhiv-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument("--incidence-recent-ratio-floor", type=float, default=0.50)
    parser.add_argument("--incidence-recent-ratio-ceiling", type=float, default=1.75)
    parser.add_argument("--aids-bounds-fraction-floor", type=float, default=0.40)
    parser.add_argument("--art-recent-mape-ceiling", type=float, default=10.0)
    args = parser.parse_args(argv)
    if not args.default_disease_grid and not args.override:
        parser.error("No sensitivity grid requested; use default grid or --override.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = sensitivity_rows(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    if args.output_xlsx:
        output_xlsx = Path(args.output_xlsx)
        output_xlsx.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(output_xlsx, index=False)
    print(f"Calibration sensitivity grid written: {output_csv}")
    summary_cols = [
        "baseline_config_path",
        "scenario",
        "lever",
        "override_value",
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
