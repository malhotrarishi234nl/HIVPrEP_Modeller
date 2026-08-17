#!/usr/bin/env python3
"""
Audit recent-fit plausibility for one or more calibration candidate configs.

This is a lightweight V13.5 pre-rerun tool. It runs each candidate through the
calibration window, extracts annual outputs, applies the recent-fit audit, and
writes a CSV suitable for comparing deterministic finalists versus LHS
stochastic-rerank winners.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs
from calib_recent_fit import RecentFitSettings, evaluate_recent_fit, recent_fit_row_fields
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from config_utils import extract_value
from run_lhs_calibration import CALIB_END_YEAR, CALIB_START_YEAR, set_nested
from run_v3 import SimulationEngineV3


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def run_candidate(config: dict, seed: int, deterministic: bool) -> dict[str, dict[int, float]]:
    if deterministic:
        set_nested(config, "run_control.deterministic_mode.value", True)
    set_nested(config, "run_control.start_year.value", CALIB_START_YEAR)
    set_nested(config, "run_control.end_year.value", CALIB_END_YEAR + 1)
    set_nested(config, "output.enabled.value", False)
    set_nested(config, "output.terminal_summary.value", False)
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(config, seed=seed)
        result = engine.run(run_number=1)
    return extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)


def candidate_metadata(config: dict) -> dict:
    lhs = config.get("calibration_metadata", {}).get("lhs_calibration", {})
    return {
        "lhs_stage": lhs.get("stage", ""),
        "lhs_rank": lhs.get("rank", ""),
        "lhs_draw_id": lhs.get("draw_id", ""),
        "lhs_total_ll": lhs.get("total_ll", ""),
        "lhs_deterministic_calibration": lhs.get("deterministic_calibration", ""),
        "runtime_deterministic": extract_value(
            config.get("run_control", {}).get("deterministic_mode", {"value": None})
        ),
    }


def audit_configs(args: argparse.Namespace) -> pd.DataFrame:
    targets = load_disease_targets(
        args.targets_workbook,
        args.target_mapping,
        stage=args.stage,
        start_year=1990,
        end_year=CALIB_END_YEAR,
    )
    settings = RecentFitSettings(
        min_observed_fraction=args.recent_fit_min_observed_fraction,
        sigma_multiplier=args.recent_fit_sigma_multiplier,
        diagnosis_year=args.recent_fit_diagnosis_year,
    )
    rows = []
    for idx, path in enumerate(args.configs, start=1):
        config = load_json(path)
        metadata = candidate_metadata(config)
        outputs = run_candidate(config, seed=args.seed + idx, deterministic=args.deterministic)
        recent_fit = evaluate_recent_fit(outputs, targets, settings=settings)
        row = {
            "config_path": str(Path(path).resolve()),
            **metadata,
            "audit_deterministic_runtime": bool(args.deterministic),
            **recent_fit_row_fields(recent_fit),
        }
        for output, years in outputs.items():
            for year in (2020, 2021, 2022, 2023, 2024, 2025):
                if year in years:
                    row[f"{output}_{year}"] = years[year]
        rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit recent fit for calibration candidate configs.")
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument("--output-csv", default=str(ROOT / "outputs" / "calibration" / "recent_fit_audit.csv"))
    parser.add_argument("--targets-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    parser.add_argument("--stage", default="joint")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    parser.add_argument("--recent-fit-min-observed-fraction", type=float, default=0.75)
    parser.add_argument("--recent-fit-sigma-multiplier", type=float, default=3.0)
    parser.add_argument(
        "--recent-fit-diagnosis-year",
        type=int,
        default=None,
        help="Optional fixed diagnosis gate year. Defaults to latest available target year per diagnosis output.",
    )
    args = parser.parse_args(argv)
    if not 0.0 <= args.recent_fit_min_observed_fraction <= 1.0:
        parser.error("--recent-fit-min-observed-fraction must be between 0 and 1")
    if args.recent_fit_sigma_multiplier <= 0:
        parser.error("--recent-fit-sigma-multiplier must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = audit_configs(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"Recent-fit audit written: {output_csv}")
    print(df[["config_path", "recent_fit_pass", "recent_fit_fail_reasons", "recent_fit_warning_reasons"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
