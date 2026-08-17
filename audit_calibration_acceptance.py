#!/usr/bin/env python3
"""
Audit full calibration acceptance gates for candidate configs.

This is diagnostics-only: it does not modify configs, calibration spaces, model
mechanisms, ranked outputs, or runner selection.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import io
import json
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_extractor import extract_annual_outputs
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from config_utils import extract_value
from run_v3 import SimulationEngineV3


ROOT = Path(__file__).resolve().parent
CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025
_PATH_PART_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]")


def load_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def parse_config_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        if part.isdigit():
            tokens.append(part)
            continue
        pos = 0
        for match in _PATH_PART_RE.finditer(part):
            if match.start() != pos:
                raise ValueError(f"Invalid config path token '{part}' in '{path}'")
            if match.group(1) is not None:
                tokens.append(match.group(1))
            else:
                tokens.append(int(match.group(2)))
            pos = match.end()
        if pos != len(part):
            raise ValueError(f"Invalid config path token '{part}' in '{path}'")
    return tokens


def set_nested(config: dict, path: str, value: Any) -> None:
    tokens = parse_config_path(path)
    if not tokens:
        raise ValueError("Config path cannot be empty")
    obj: Any = config
    for token in tokens[:-1]:
        obj = obj[token]
    obj[tokens[-1]] = value


def run_candidate(config: dict, seed: int, deterministic: bool) -> dict[str, dict[int, float]]:
    if deterministic:
        set_nested(config, "run_control.deterministic_mode.value", True)
    set_nested(config, "run_control.start_year.value", CALIB_START_YEAR)
    set_nested(config, "run_control.end_year.value", CALIB_END_YEAR + 1)
    set_nested(config, "output.enabled.value", False)
    set_nested(config, "output.terminal_summary.value", False)
    with contextlib.redirect_stdout(io.StringIO()):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
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


def load_targets(args: argparse.Namespace) -> list[dict]:
    if args.normalized_targets_csv:
        return pd.read_csv(args.normalized_targets_csv).to_dict("records")
    return load_disease_targets(
        args.targets_workbook,
        args.target_mapping,
        stage=args.stage,
        start_year=1990,
        end_year=CALIB_END_YEAR,
    )


def expand_config_paths(args: argparse.Namespace) -> list[str]:
    paths = [str(Path(path)) for path in args.configs]
    for pattern in args.candidate_glob or []:
        paths.extend(sorted(glob.glob(pattern)))
    seen = set()
    unique = []
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    if args.max_candidates is not None:
        unique = unique[: args.max_candidates]
    return unique


def audit_configs(args: argparse.Namespace) -> pd.DataFrame:
    targets = load_targets(args)
    settings = AcceptanceGateSettings(
        diagnosis_floor=args.diagnosis_floor,
        diagnosis_ceiling=args.diagnosis_ceiling,
        plhiv_recent_mape_ceiling=args.plhiv_recent_mape_ceiling,
        incidence_recent_ratio_floor=args.incidence_recent_ratio_floor,
        incidence_recent_ratio_ceiling=args.incidence_recent_ratio_ceiling,
        incidence_year_upper_tolerance=args.incidence_year_upper_tolerance,
        aids_bounds_fraction_floor=args.aids_bounds_fraction_floor,
        art_recent_mape_ceiling=args.art_recent_mape_ceiling,
    )
    rows = []
    for idx, path in enumerate(expand_config_paths(args), start=1):
        config = load_json(path)
        outputs = run_candidate(config, seed=args.seed + idx, deterministic=args.deterministic)
        acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
        row = {
            "config_path": str(Path(path).resolve()),
            **candidate_metadata(config),
            "audit_deterministic_runtime": bool(args.deterministic),
            **acceptance_gate_row_fields(acceptance),
        }
        for output in (
            "PLHIV",
            "new_infections",
            "aids_deaths",
            "new_diagnoses",
            "new_diagnoses_msm",
            "on_art_stock",
        ):
            for year in (2015, 2020, 2021, 2022, 2023, 2024):
                if year in outputs.get(output, {}):
                    row[f"{output}_{year}"] = outputs[output][year]
        rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit calibration acceptance gates for candidate configs.")
    parser.add_argument("configs", nargs="*", help="Candidate config JSON paths.")
    parser.add_argument(
        "--candidate-glob",
        action="append",
        help="Glob pattern for additional candidate config JSON paths. May be supplied more than once.",
    )
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--output-csv", default=str(ROOT / "outputs" / "calibration" / "calibration_acceptance_audit.csv"))
    parser.add_argument("--output-xlsx", default=None)
    parser.add_argument("--normalized-targets-csv", default=None)
    parser.add_argument("--targets-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    parser.add_argument("--stage", default="joint")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    parser.add_argument("--diagnosis-floor", type=float, default=0.90)
    parser.add_argument("--diagnosis-ceiling", type=float, default=1.50)
    parser.add_argument("--plhiv-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument("--incidence-recent-ratio-floor", type=float, default=0.50)
    parser.add_argument("--incidence-recent-ratio-ceiling", type=float, default=1.75)
    parser.add_argument("--incidence-year-upper-tolerance", type=float, default=0.05)
    parser.add_argument("--aids-bounds-fraction-floor", type=float, default=0.40)
    parser.add_argument("--art-recent-mape-ceiling", type=float, default=10.0)
    args = parser.parse_args(argv)
    if not args.configs and not args.candidate_glob:
        parser.error("Provide at least one config path or --candidate-glob.")
    if args.max_candidates is not None and args.max_candidates <= 0:
        parser.error("--max-candidates must be positive when supplied.")
    if not 0.0 <= args.diagnosis_floor <= args.diagnosis_ceiling:
        parser.error("--diagnosis-floor must be non-negative and <= --diagnosis-ceiling.")
    if not 0.0 <= args.aids_bounds_fraction_floor <= 1.0:
        parser.error("--aids-bounds-fraction-floor must be between 0 and 1.")
    if args.incidence_year_upper_tolerance < 0.0:
        parser.error("--incidence-year-upper-tolerance must be non-negative.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = audit_configs(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    if args.output_xlsx:
        output_xlsx = Path(args.output_xlsx)
        output_xlsx.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(output_xlsx, index=False)
    print(f"Calibration acceptance audit written: {output_csv}")
    summary_cols = [
        "config_path",
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
