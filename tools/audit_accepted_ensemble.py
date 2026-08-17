#!/usr/bin/env python3
"""
Independent gate re-audit for the accepted calibration ensemble.

Runs the model deterministically on each accepted config and re-evaluates
all V13.8.2 acceptance gates. Does not modify any config files.

Usage:
    python3 tools/audit_accepted_ensemble.py \
        --manifest-csv outputs/calibration/accepted_ensemble_v13_8_3/accepted_ensemble_manifest.csv \
        --output-dir outputs/calibration/accepted_ensemble_v13_8_3
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
from run_v3 import SimulationEngineV3

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025
GATE_VERSION = "v13.8.2"
INCIDENCE_UPPER_TOLERANCE = 0.05

# Locked V13.8.2 gate thresholds — do not modify without a gate change decision
_LOCKED_SETTINGS = AcceptanceGateSettings(
    diagnosis_floor=0.90,
    diagnosis_ceiling=1.50,
    plhiv_recent_mape_ceiling=10.0,
    incidence_recent_ratio_floor=0.50,
    incidence_recent_ratio_ceiling=1.75,
    incidence_year_upper_tolerance=INCIDENCE_UPPER_TOLERANCE,
    aids_bounds_fraction_floor=0.40,
    art_recent_mape_ceiling=10.0,
)


def _run_candidate(config_path: str, seed: int) -> dict:
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    config["run_control"]["deterministic_mode"]["value"] = True
    config["run_control"]["start_year"]["value"] = CALIB_START_YEAR
    config["run_control"]["end_year"]["value"] = CALIB_END_YEAR + 1
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    with contextlib.redirect_stdout(io.StringIO()):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Independent gate re-audit for accepted calibration ensemble."
    )
    parser.add_argument(
        "--manifest-csv",
        required=True,
        help="Manifest CSV produced by build_accepted_calibration_ensemble.py",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write audit outputs.",
    )
    parser.add_argument(
        "--targets-workbook",
        default=str(default_disease_workbook_path(ROOT)),
        help="Disease calibration targets workbook.",
    )
    parser.add_argument(
        "--target-mapping",
        default=str(default_target_mapping_path(ROOT)),
        help="Disease target mapping JSON.",
    )
    parser.add_argument("--stage", default="joint", help="Calibration stage (default: joint).")
    parser.add_argument("--seed", type=int, default=42, help="Base RNG seed.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = pd.read_csv(args.manifest_csv)
    if "copied_config_path" in manifest.columns:
        config_paths = manifest["copied_config_path"].tolist()
    else:
        config_paths = manifest["source_config_path"].tolist()

    targets = load_disease_targets(
        args.targets_workbook,
        args.target_mapping,
        stage=args.stage,
        start_year=1990,
        end_year=CALIB_END_YEAR,
    )

    rows = []
    n_fresh_pass = 0
    n_fresh_fail = 0
    n_mismatch = 0
    total = len(manifest)

    for idx, (manifest_idx, manifest_row) in enumerate(manifest.iterrows(), start=1):
        config_path = config_paths[idx - 1]
        embedded_accept = bool(manifest_row.get("overall_accept", True))

        print(
            f"  [{idx:3d}/{total}] {Path(config_path).name} ...",
            end=" ",
            flush=True,
        )
        outputs = _run_candidate(config_path, seed=args.seed + idx)
        acceptance = evaluate_acceptance_gates(outputs, targets, settings=_LOCKED_SETTINGS)
        fresh_accept = bool(acceptance["overall_accept"])

        if fresh_accept:
            n_fresh_pass += 1
        else:
            n_fresh_fail += 1

        mismatch = fresh_accept != embedded_accept
        if mismatch:
            n_mismatch += 1

        status = "PASS" if fresh_accept else f"FAIL({';'.join(acceptance['failures'][:2])})"
        mismatch_flag = " [MISMATCH]" if mismatch else ""
        print(f"{status}{mismatch_flag}")

        row = {
            "rank": manifest_row.get("rank"),
            "config_path": str(config_path),
            "embedded_overall_accept": embedded_accept,
            "fresh_overall_accept": fresh_accept,
            "gate_version": GATE_VERSION,
            "incidence_upper_tolerance": INCIDENCE_UPPER_TOLERANCE,
            "metadata_mismatch": mismatch,
            **acceptance_gate_row_fields(acceptance),
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_dir / "accepted_ensemble_gate_audit.csv", index=False)

    summary = {
        "gate_version": GATE_VERSION,
        "incidence_upper_tolerance_documented": INCIDENCE_UPPER_TOLERANCE,
        "n_candidates_audited": len(rows),
        "n_fresh_pass": n_fresh_pass,
        "n_fresh_fail": n_fresh_fail,
        "n_metadata_mismatch": n_mismatch,
        "all_pass": n_fresh_fail == 0,
        "any_mismatch": n_mismatch > 0,
        "gate_settings": {
            "diagnosis_floor": _LOCKED_SETTINGS.diagnosis_floor,
            "diagnosis_ceiling": _LOCKED_SETTINGS.diagnosis_ceiling,
            "plhiv_recent_mape_ceiling": _LOCKED_SETTINGS.plhiv_recent_mape_ceiling,
            "incidence_recent_ratio_floor": _LOCKED_SETTINGS.incidence_recent_ratio_floor,
            "incidence_recent_ratio_ceiling": _LOCKED_SETTINGS.incidence_recent_ratio_ceiling,
            "incidence_year_upper_tolerance": _LOCKED_SETTINGS.incidence_year_upper_tolerance,
            "aids_bounds_fraction_floor": _LOCKED_SETTINGS.aids_bounds_fraction_floor,
            "art_recent_mape_ceiling": _LOCKED_SETTINGS.art_recent_mape_ceiling,
        },
    }
    (output_dir / "accepted_ensemble_gate_audit_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(
        f"\nAudit complete: {n_fresh_pass}/{total} pass fresh gates, "
        f"{n_fresh_fail} fail, {n_mismatch} metadata mismatches"
    )
    print(f"Gate audit CSV:  {output_dir / 'accepted_ensemble_gate_audit.csv'}")
    print(f"Audit summary:   {output_dir / 'accepted_ensemble_gate_audit_summary.json'}")

    return 0 if n_fresh_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
