#!/usr/bin/env python3
"""
Historical-shape audit for V13.9 calibration design.

Runs V13.8.2 endpoint gates AND V13.9 historical shape gates on a set of
candidates.  Candidates can be supplied either as an accepted ensemble manifest
(reruns the model deterministically for each config) or as a pre-computed fit
timeseries CSV (skips model runs).

Outputs
-------
historical_shape_audit.csv
    One row per candidate with all gate pass/fail fields and per-gate metrics.
historical_shape_summary.json
    Summary statistics: how many pass V13.8.2 only, historical only, and both;
    which gate is most binding.

Usage examples
--------------
# Audit V13.8.3 accepted ensemble using pre-computed fit timeseries:
python3 tools/audit_historical_shape_candidates.py \\
    --fit-timeseries-csv outputs/calibration/accepted_ensemble_v13_8_3/accepted_ensemble_fit_timeseries.csv \\
    --manifest-csv outputs/calibration/accepted_ensemble_v13_8_3/accepted_ensemble_manifest.csv \\
    --output-dir outputs/calibration/accepted_ensemble_v13_8_3

# Full rerun from manifest (no pre-computed timeseries):
python3 tools/audit_historical_shape_candidates.py \\
    --manifest-csv outputs/calibration/accepted_ensemble_v13_8_3/accepted_ensemble_manifest.csv \\
    --output-dir outputs/calibration/accepted_ensemble_v13_8_3
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections import Counter
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
from calib_historical_shape_gates import (
    HistoricalShapeGateSettings,
    evaluate_historical_shape_distance,
    evaluate_historical_shape_gates,
    historical_shape_distance_row_fields,
    historical_shape_gate_row_fields,
)
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from run_v3 import SimulationEngineV3

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025

# Locked V13.8.2 gate thresholds — do not modify without a gate change decision.
_V13_8_2_SETTINGS = AcceptanceGateSettings(
    diagnosis_floor=0.90,
    diagnosis_ceiling=1.50,
    plhiv_recent_mape_ceiling=10.0,
    incidence_recent_ratio_floor=0.50,
    incidence_recent_ratio_ceiling=1.75,
    incidence_year_upper_tolerance=0.05,
    aids_bounds_fraction_floor=0.40,
    art_recent_mape_ceiling=10.0,
)


# ---------------------------------------------------------------------------
# Model runner (used when fit timeseries CSV is not provided)
# ---------------------------------------------------------------------------


def _run_config(config_path: str, seed: int) -> dict:
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


# ---------------------------------------------------------------------------
# Timeseries CSV → per-candidate outputs dict
# ---------------------------------------------------------------------------


def _outputs_from_timeseries(
    ts_df: pd.DataFrame,
    rank: int,
    config_path: str,
) -> dict | None:
    """Reconstruct model_outputs dict for one candidate from a pre-built timeseries CSV."""
    mask = ts_df["rank"] == rank
    if not mask.any() and "config_path" in ts_df.columns:
        mask = ts_df["config_path"].astype(str).str.endswith(Path(config_path).name)
    subset = ts_df[mask]
    if subset.empty:
        return None
    outputs: dict[str, dict[int, float]] = {}
    for _, row in subset.iterrows():
        output_name = str(row["output"])
        year = int(row["year"])
        value = float(row["model_value"])
        outputs.setdefault(output_name, {})[year] = value
    return outputs


# ---------------------------------------------------------------------------
# Gate evaluation for one candidate
# ---------------------------------------------------------------------------


def _evaluate_candidate(
    outputs: dict,
    targets: list[dict],
    hist_settings: HistoricalShapeGateSettings,
) -> dict:
    """Run both V13.8.2 and historical shape gates on pre-computed outputs."""
    v13_result = evaluate_acceptance_gates(outputs, targets, settings=_V13_8_2_SETTINGS)
    hist_result = evaluate_historical_shape_gates(outputs, targets, settings=hist_settings)
    hist_dist = evaluate_historical_shape_distance(outputs, targets, settings=hist_settings)

    v13_accept = bool(v13_result.get("overall_accept", False))
    hist_accept = bool(hist_result.get("overall_accept", False))
    combined_accept = v13_accept and hist_accept

    row: dict = {
        "v13_8_2_overall_accept": v13_accept,
        "hist_shape_overall_accept": hist_accept,
        "combined_overall_accept": combined_accept,
        **acceptance_gate_row_fields(v13_result),
        **historical_shape_gate_row_fields(hist_result),
        **historical_shape_distance_row_fields(hist_dist),
    }
    return row


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def _build_summary(
    rows: list[dict],
    hist_settings: HistoricalShapeGateSettings,
    n_from_timeseries: int,
    n_rerun: int,
    n_error: int,
) -> dict:
    n_total = len(rows)
    n_v13_pass = sum(1 for r in rows if r.get("v13_8_2_overall_accept"))
    n_hist_pass = sum(1 for r in rows if r.get("hist_shape_overall_accept"))
    n_combined_pass = sum(1 for r in rows if r.get("combined_overall_accept"))
    n_v13_only = sum(
        1 for r in rows
        if r.get("v13_8_2_overall_accept") and not r.get("hist_shape_overall_accept")
    )
    n_hist_only = sum(
        1 for r in rows
        if not r.get("v13_8_2_overall_accept") and r.get("hist_shape_overall_accept")
    )

    # Most binding gate: count failures per gate name
    failure_counts: Counter = Counter()
    for r in rows:
        for reason in str(r.get("hist_fail_reasons", "")).split(";"):
            reason = reason.strip()
            if reason:
                gate = reason.split(":")[0] if ":" in reason else reason
                failure_counts[gate] += 1
    most_binding_hist = failure_counts.most_common(1)

    v13_failure_counts: Counter = Counter()
    for r in rows:
        for reason in str(r.get("acceptance_fail_reasons", "")).split(";"):
            reason = reason.strip()
            if reason:
                gate = reason.split(":")[0] if ":" in reason else reason
                v13_failure_counts[gate] += 1
    most_binding_v13 = v13_failure_counts.most_common(1)

    return {
        "gate_versions": {"recent_endpoint": "v13.8.2", "historical_shape": "v13.9"},
        "n_candidates_audited": n_total,
        "n_from_precomputed_timeseries": n_from_timeseries,
        "n_rerun_fresh": n_rerun,
        "n_error": n_error,
        "pass_counts": {
            "v13_8_2_only": n_v13_pass,
            "historical_shape_only": n_hist_pass,
            "both_combined": n_combined_pass,
            "v13_pass_but_hist_fail": n_v13_only,
            "hist_pass_but_v13_fail": n_hist_only,
        },
        "pass_rates": {
            "v13_8_2": n_v13_pass / n_total if n_total else 0.0,
            "historical_shape": n_hist_pass / n_total if n_total else 0.0,
            "combined": n_combined_pass / n_total if n_total else 0.0,
        },
        "most_binding_hist_gate": most_binding_hist[0][0] if most_binding_hist else None,
        "most_binding_v13_gate": most_binding_v13[0][0] if most_binding_v13 else None,
        "hist_gate_failure_counts": dict(failure_counts),
        "v13_gate_failure_counts": dict(v13_failure_counts),
        "historical_shape_settings": {
            "plhiv_hist_mape_ceiling": hist_settings.plhiv_hist_mape_ceiling,
            "plhiv_anchor_max_multiplier": hist_settings.plhiv_anchor_max_multiplier,
            "diagnoses_hist_mape_ceiling": hist_settings.diagnoses_hist_mape_ceiling,
            "diagnoses_spike_ratio_ceiling": hist_settings.diagnoses_spike_ratio_ceiling,
            "msm_diag_hist_mape_ceiling": hist_settings.msm_diag_hist_mape_ceiling,
            "msm_diag_early_ratio_ceiling": hist_settings.msm_diag_early_ratio_ceiling,
            "aids_cases_ratio_ceiling": hist_settings.aids_cases_ratio_ceiling,
            "aids_cases_hist_mape_ceiling": hist_settings.aids_cases_hist_mape_ceiling,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit V13.8.2 and V13.9 historical shape gates on calibration candidates.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--manifest-csv",
        required=True,
        help="Accepted ensemble manifest CSV (from build_accepted_calibration_ensemble.py).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write audit outputs.",
    )
    parser.add_argument(
        "--fit-timeseries-csv",
        default=None,
        help=(
            "Pre-computed fit timeseries CSV (from summarize_accepted_ensemble_fit.py). "
            "When provided, model outputs are read from here instead of rerunning the model."
        ),
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
    parser.add_argument("--stage", default="joint", help="Calibration stage.")
    parser.add_argument("--seed", type=int, default=42, help="Base RNG seed for model reruns.")

    # Historical shape gate thresholds (diagnostic; adjust after first audit)
    parser.add_argument(
        "--plhiv-hist-mape-ceiling",
        type=float, default=40.0,
        help="PLHIV 1990-2005 MAPE ceiling (%%).",
    )
    parser.add_argument(
        "--plhiv-anchor-max-multiplier",
        type=float, default=2.0,
        help="1990 PLHIV model/target ratio ceiling.",
    )
    parser.add_argument(
        "--diagnoses-hist-mape-ceiling",
        type=float, default=40.0,
        help="Total diagnoses 1990-2014 MAPE ceiling (%%).",
    )
    parser.add_argument(
        "--diagnoses-spike-ratio-ceiling",
        type=float, default=2.5,
        help="1997 diagnosis model/target ratio ceiling (spike guard).",
    )
    parser.add_argument(
        "--msm-diag-hist-mape-ceiling",
        type=float, default=40.0,
        help="MSM diagnoses 1996-2014 MAPE ceiling (%%).",
    )
    parser.add_argument(
        "--msm-diag-early-ratio-ceiling",
        type=float, default=2.5,
        help="MSM diagnoses 1996-2003 model/target ratio ceiling.",
    )
    parser.add_argument(
        "--aids-cases-ratio-ceiling",
        type=float, default=4.0,
        help="AIDS cases 1990-2017 model/target ratio ceiling (any year).",
    )
    parser.add_argument(
        "--aids-cases-hist-mape-ceiling",
        type=float, default=80.0,
        help="AIDS cases 1990-2017 MAPE ceiling (%%).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    hist_settings = HistoricalShapeGateSettings(
        enabled=True,
        plhiv_hist_mape_ceiling=args.plhiv_hist_mape_ceiling,
        plhiv_anchor_max_multiplier=args.plhiv_anchor_max_multiplier,
        diagnoses_hist_mape_ceiling=args.diagnoses_hist_mape_ceiling,
        diagnoses_spike_ratio_ceiling=args.diagnoses_spike_ratio_ceiling,
        msm_diag_hist_mape_ceiling=args.msm_diag_hist_mape_ceiling,
        msm_diag_early_ratio_ceiling=args.msm_diag_early_ratio_ceiling,
        aids_cases_ratio_ceiling=args.aids_cases_ratio_ceiling,
        aids_cases_hist_mape_ceiling=args.aids_cases_hist_mape_ceiling,
    )

    manifest = pd.read_csv(args.manifest_csv)
    config_col = (
        "copied_config_path" if "copied_config_path" in manifest.columns else "source_config_path"
    )
    config_paths = manifest[config_col].tolist()
    ranks = manifest["rank"].tolist() if "rank" in manifest.columns else list(range(1, len(manifest) + 1))

    targets = load_disease_targets(
        args.targets_workbook, args.target_mapping,
        stage=args.stage, start_year=1990, end_year=CALIB_END_YEAR,
    )

    ts_df: pd.DataFrame | None = None
    if args.fit_timeseries_csv:
        ts_path = Path(args.fit_timeseries_csv)
        if ts_path.exists():
            ts_df = pd.read_csv(ts_path)
            print(f"Using pre-computed fit timeseries: {ts_path}")
        else:
            print(f"WARNING: --fit-timeseries-csv not found at {ts_path}; will rerun model.")

    rows: list[dict] = []
    n_total = len(manifest)
    n_from_ts = 0
    n_rerun = 0
    n_error = 0

    for idx, (rank, config_path) in enumerate(zip(ranks, config_paths), start=1):
        print(
            f"  [{idx:3d}/{n_total}] rank {rank}: {Path(config_path).name} ...",
            end=" ", flush=True,
        )
        outputs: dict | None = None

        if ts_df is not None:
            outputs = _outputs_from_timeseries(ts_df, rank, config_path)
            if outputs is not None:
                n_from_ts += 1

        if outputs is None:
            try:
                outputs = _run_config(config_path, seed=args.seed + idx)
                n_rerun += 1
            except Exception as exc:
                print(f"ERROR ({exc})")
                rows.append({
                    "rank": rank,
                    "config_path": str(config_path),
                    "error": str(exc),
                    "v13_8_2_overall_accept": False,
                    "hist_shape_overall_accept": False,
                    "combined_overall_accept": False,
                })
                n_error += 1
                continue

        gate_row = _evaluate_candidate(outputs, targets, hist_settings)
        row = {
            "rank": rank,
            "config_path": str(config_path),
            "error": "",
            **gate_row,
        }
        rows.append(row)

        v13_flag = "PASS" if row.get("v13_8_2_overall_accept") else "FAIL"
        hist_flag = "PASS" if row.get("hist_shape_overall_accept") else "FAIL"
        print(f"v13.8.2={v13_flag}  hist={hist_flag}")

    audit_df = pd.DataFrame(rows)
    audit_csv = output_dir / "historical_shape_audit.csv"
    audit_df.to_csv(audit_csv, index=False)

    summary = _build_summary(rows, hist_settings, n_from_ts, n_rerun, n_error)
    summary_json = output_dir / "historical_shape_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    pc = summary["pass_counts"]
    pr = summary["pass_rates"]
    print(
        f"\nAudit complete: {n_total} candidates\n"
        f"  V13.8.2 pass:    {pc['v13_8_2_only']} ({pr['v13_8_2']:.1%})\n"
        f"  Hist shape pass: {pc['historical_shape_only']} ({pr['historical_shape']:.1%})\n"
        f"  Combined pass:   {pc['both_combined']} ({pr['combined']:.1%})\n"
        f"  V13.8.2 pass but hist fail: {pc['v13_pass_but_hist_fail']}\n"
        f"  Most binding hist gate: {summary['most_binding_hist_gate']}\n"
        f"  Most binding V13.8.2 gate: {summary['most_binding_v13_gate']}"
    )
    print(f"Audit CSV:    {audit_csv}")
    print(f"Summary JSON: {summary_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
