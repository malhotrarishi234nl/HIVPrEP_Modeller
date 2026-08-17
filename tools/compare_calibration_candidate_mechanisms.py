#!/usr/bin/env python3
"""
V13.10 Multi-candidate mechanism comparison tool.

Runs each named candidate config deterministically and compares outcomes across
all V13.8.2 and V13.9 gate metrics, selected parameters, and composition
diagnostics.  Intended to compare:
  - Old LHS final candidate (if available)
  - V13.8.2 accepted rank001
  - V13.9.3 best near miss
  - V13.9.5 non-MSM diagnostic best
  - V13.10 multiplier pilot best

Usage
-----
python3 tools/compare_calibration_candidate_mechanisms.py \\
    --candidate baseline=outputs/calibration/...../config.json \\
    --candidate v13_10_best=outputs/calibration/...../config.json \\
    --normalized-targets-csv outputs/calibration/.../normalized_targets_joint_*.csv \\
    --output-dir outputs/diagnostics/candidate_comparison \\
    --seed 42
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calib_acceptance_gates import evaluate_acceptance_gates, AcceptanceGateSettings
from calib_extractor import extract_annual_outputs
from calib_historical_shape_gates import evaluate_historical_shape_gates, HistoricalShapeGateSettings
from calib_non_msm_diagnosis import evaluate_non_msm_diagnosis_gate, NonMsmDiagnosisSettings
from config_utils import extract_value
from run_v3 import SimulationEngineV3

try:
    from tools.stratified_output_extractor import extract_stratified_outputs, ALL_STRATIFIED_FIELDS
    from tools.diagnose_calibration_mechanism_variants import (
        compute_summary_row,
        extract_config_params,
        load_targets,
        _finite,
        _median_abs_pct_error,
        _median_ratio,
        _max_ratio,
        _aids_bounds_fraction,
    )
except ImportError:
    sys.path.insert(0, str(ROOT / "tools"))
    from stratified_output_extractor import extract_stratified_outputs, ALL_STRATIFIED_FIELDS
    from diagnose_calibration_mechanism_variants import (
        compute_summary_row,
        extract_config_params,
        load_targets,
        _finite,
        _median_abs_pct_error,
        _median_ratio,
        _max_ratio,
        _aids_bounds_fraction,
    )


# ---------------------------------------------------------------------------
# Argument parsing helpers (pure — testable)
# ---------------------------------------------------------------------------


def parse_candidate_arg(arg: str) -> tuple[str, str]:
    """Parse 'NAME=PATH' candidate argument.

    Returns (name, path) tuple.
    Raises ValueError if the argument does not contain exactly one '='.
    """
    parts = arg.split("=", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"--candidate argument must be in 'NAME=PATH' format, got: {arg!r}"
        )
    return parts[0].strip(), parts[1].strip()


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_all_gates(
    outputs: dict,
    targets: list[dict],
    enable_historical: bool = True,
    enable_non_msm: bool = True,
) -> dict:
    """Evaluate V13.8.2 acceptance gates + V13.9 historical gates + V13.9.5 non-MSM gate."""
    gate_settings = AcceptanceGateSettings()
    hist_settings = HistoricalShapeGateSettings(enabled=enable_historical)
    non_msm_settings = NonMsmDiagnosisSettings(enabled=enable_non_msm)

    v1382 = evaluate_acceptance_gates(outputs, targets, gate_settings)
    v139 = evaluate_historical_shape_gates(outputs, targets, hist_settings)
    v1395 = evaluate_non_msm_diagnosis_gate(outputs, targets, non_msm_settings)

    n_failures = (
        len(v1382.get("failures", []))
        + len(v139.get("failures", []))
        + (len(v1395.get("failures", [])) if enable_non_msm else 0)
    )

    return {
        "v1382_gates": v1382,
        "v139_gates": v139,
        "v1395_gate": v1395,
        "total_hard_gate_failures": n_failures,
        "overall_accept": v1382.get("overall_accept", False),
        "hist_overall_accept": v139.get("overall_accept", False),
        "non_msm_gate_pass": v1395.get("passed", True),
    }


# ---------------------------------------------------------------------------
# Candidate row builder (pure — no I/O)
# ---------------------------------------------------------------------------


def build_candidate_row(
    name: str,
    config_path: str,
    config: dict,
    outputs: dict,
    strat_outputs: dict,
    gate_result: dict,
    targets: list[dict],
    latest_year: int = 2024,
) -> dict:
    """Build the full comparison row for one candidate."""
    row: dict = {"candidate": name, "config_path": config_path}

    # Gate pass/fail flags
    v1382 = gate_result.get("v1382_gates", {})
    v139 = gate_result.get("v139_gates", {})
    v1395 = gate_result.get("v1395_gate", {})

    row["overall_accept"] = gate_result.get("overall_accept", False)
    row["hist_overall_accept"] = gate_result.get("hist_overall_accept", False)
    row["non_msm_gate_pass"] = gate_result.get("non_msm_gate_pass", True)
    row["total_hard_gate_failures"] = gate_result.get("total_hard_gate_failures", 0)

    for gate_name, gate_res in v1382.get("gates", {}).items():
        row[f"v1382_{gate_name}_pass"] = gate_res.get("passed", False)

    for gate_name, gate_res in v139.get("gates", {}).items():
        row[f"v139_{gate_name}_pass"] = gate_res.get("passed", False)

    row["v1395_non_msm_diag_gate_pass"] = v1395.get("passed", True)

    # Selected metrics
    row["diag_2024_total_ratio"] = _latest_ratio(outputs, targets, "new_diagnoses", latest_year)
    row["diag_2024_msm_ratio"] = _latest_ratio(outputs, targets, "new_diagnoses_msm", latest_year)

    model_non_msm, target_non_msm = _derive_non_msm_2024(outputs, targets, latest_year)
    row["diag_2024_non_msm_ratio"] = _safe_ratio(model_non_msm, target_non_msm)

    row["plhiv_2015_2024_mape"] = _median_abs_pct_error(outputs, targets, "PLHIV", 2015, 2024)
    row["art_2015_2024_mape"] = _median_abs_pct_error(outputs, targets, "on_art_stock", 2015, 2024)
    row["aids_death_bounds_fraction"] = _aids_bounds_fraction(outputs, targets)
    row["incidence_2015_2024_median_ratio"] = _median_ratio(outputs, targets, "new_infections", 2015, 2024)
    row["msm_diag_1996_2003_max_ratio"] = _max_ratio(outputs, targets, "new_diagnoses_msm", 1996, 2003)
    row["msm_diag_1996_2014_mape"] = _median_abs_pct_error(outputs, targets, "new_diagnoses_msm", 1996, 2014)

    # Non-MSM MAPE (recent and historical)
    non_msm_outputs = _derive_non_msm_outputs(outputs, targets)
    row["non_msm_recent_mape"] = _non_msm_mape(non_msm_outputs, 2015, 2024)
    row["non_msm_hist_mape"] = _non_msm_mape(non_msm_outputs, 1996, 2014)

    # Non-MSM stock presence
    non_msm_undiag = strat_outputs.get("non_msm_undiagnosed_hiv_pos", {})
    val_2024 = non_msm_undiag.get(latest_year)
    row["non_msm_undiagnosed_stock_2024_exists"] = (
        val_2024 is not None and _finite(val_2024) and val_2024 > 0
    ) if non_msm_undiag else None

    # Parameters
    params = extract_config_params(config)
    for k, v in params.items():
        row[f"param_{k}"] = v

    # Failures summary
    row["v1382_fail_reasons"] = ";".join(v1382.get("failures", []))
    row["v139_fail_reasons"] = ";".join(v139.get("failures", []))
    row["v1395_fail_reasons"] = ";".join(v1395.get("failures", []))

    return row


def _latest_ratio(outputs, targets, output_name, year):
    model_val = outputs.get(output_name, {}).get(year)
    target_row = next(
        (t for t in targets if t["model_output"] == output_name and t["year"] == year), None
    )
    if model_val is None or target_row is None:
        return None
    est = float(target_row["estimate"])
    return _safe_ratio(model_val, est)


def _safe_ratio(num, den):
    if num is None or den is None or not (_finite(num) and _finite(den)) or den == 0.0:
        return None
    return num / den


def _derive_non_msm_2024(outputs, targets, year):
    total_model = outputs.get("new_diagnoses", {}).get(year)
    msm_model = outputs.get("new_diagnoses_msm", {}).get(year)
    model_non_msm = max(0.0, total_model - msm_model) if total_model is not None and msm_model is not None else None

    target_total = next(
        (float(t["estimate"]) for t in targets if t["model_output"] == "new_diagnoses" and t["year"] == year), None
    )
    target_msm = next(
        (float(t["estimate"]) for t in targets if t["model_output"] == "new_diagnoses_msm" and t["year"] == year), None
    )
    target_non_msm = (
        max(0.0, target_total - target_msm)
        if target_total is not None and target_msm is not None
        else None
    )
    return model_non_msm, target_non_msm


def _derive_non_msm_outputs(outputs, targets):
    """Return (year, model_non_msm, target_non_msm) tuples for all shared years."""
    total_t = {t["year"]: float(t["estimate"]) for t in targets if t["model_output"] == "new_diagnoses"}
    msm_t = {t["year"]: float(t["estimate"]) for t in targets if t["model_output"] == "new_diagnoses_msm"}
    rows = []
    for yr in sorted(set(total_t) & set(msm_t)):
        target_non_msm = max(0.0, total_t[yr] - msm_t[yr])
        model_total = outputs.get("new_diagnoses", {}).get(yr)
        model_msm = outputs.get("new_diagnoses_msm", {}).get(yr)
        if model_total is None or model_msm is None:
            continue
        model_non_msm = max(0.0, model_total - model_msm)
        rows.append((yr, model_non_msm, target_non_msm))
    return rows


def _non_msm_mape(non_msm_rows, start_year, end_year):
    errors = []
    for yr, model_non_msm, target_non_msm in non_msm_rows:
        if not start_year <= yr <= end_year:
            continue
        if target_non_msm == 0:
            continue
        errors.append(abs((model_non_msm - target_non_msm) / target_non_msm) * 100.0)
    return median(errors) if errors else None


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------


def _run_config_quietly(config: dict, seed: int, run_end: int) -> dict:
    config = copy.deepcopy(config)
    config["run_control"]["deterministic_mode"]["value"] = True
    config["run_control"]["start_year"]["value"] = 1980
    config["run_control"]["end_year"]["value"] = max(run_end + 1, 2026)
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return result


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------


_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;
     margin:0;padding:20px 32px;color:#222;background:#fff}
h1{font-size:20px;margin-bottom:4px;color:#1a1a2e}
h2{font-size:15px;margin:24px 0 8px;color:#16213e;border-bottom:2px solid #e0e0e0;padding-bottom:4px}
.meta{color:#666;font-size:12px;margin-bottom:20px}
.note{background:#fff8e1;border-left:4px solid #f9a825;padding:8px 12px;
      margin:10px 0;border-radius:2px;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:11px;margin:8px 0}
th{background:#f0f2f5;text-align:right;padding:4px 7px;border:1px solid #d0d4d8;
   font-weight:600;color:#444;white-space:nowrap}
th:first-child{text-align:left}
td{padding:3px 7px;border:1px solid #e8eaed;text-align:right}
td:first-child{text-align:left;font-weight:500}
tr:nth-child(even) td{background:#fafbfc}
.good{color:#2e7d32;font-weight:600}
.warn{color:#e65100;font-weight:600}
.bad{color:#c62828;font-weight:600}
.na{color:#aaa}
.pass{background:#e8f5e9 !important;color:#1b5e20}
.fail{background:#ffebee !important;color:#b71c1c}
"""


def _fmt(v, decimals=3):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return '<span class="na">—</span>'
    if isinstance(v, bool):
        return f'<span class="{"good" if v else "bad"}">{"PASS" if v else "FAIL"}</span>'
    return f"{v:.{decimals}f}"


def build_html_report(comparison_df: pd.DataFrame, manifest: dict) -> str:
    created = manifest.get("created_at", "")
    candidates = manifest.get("candidates", [])

    # Gate pass/fail table
    gate_cols = [c for c in comparison_df.columns if c.endswith("_pass") or c == "total_hard_gate_failures"]
    gate_html = _pivot_table_html(comparison_df, gate_cols)

    # Metric table
    metric_cols = [
        "diag_2024_total_ratio", "diag_2024_msm_ratio", "diag_2024_non_msm_ratio",
        "plhiv_2015_2024_mape", "art_2015_2024_mape", "aids_death_bounds_fraction",
        "incidence_2015_2024_median_ratio", "msm_diag_1996_2003_max_ratio",
        "msm_diag_1996_2014_mape", "non_msm_recent_mape", "non_msm_hist_mape",
        "non_msm_undiagnosed_stock_2024_exists",
    ]
    metric_html = _pivot_table_html(comparison_df, [c for c in metric_cols if c in comparison_df.columns])

    # Parameter table
    param_cols = [c for c in comparison_df.columns if c.startswith("param_")]
    param_html = _pivot_table_html(comparison_df, param_cols)

    # Failures section
    failure_rows = ""
    for _, row in comparison_df.iterrows():
        c = row.get("candidate", "?")
        for col, label in [
            ("v1382_fail_reasons", "V13.8.2"),
            ("v139_fail_reasons", "V13.9 hist"),
            ("v1395_fail_reasons", "V13.9.5 non-MSM"),
        ]:
            reasons = str(row.get(col, "")).strip()
            if reasons:
                failure_rows += f"<tr><td>{c}</td><td>{label}</td><td>{reasons}</td></tr>"

    failures_html = (
        f'<table><thead><tr><th>Candidate</th><th>Gate set</th><th>Failures</th></tr></thead>'
        f'<tbody>{failure_rows}</tbody></table>'
        if failure_rows else "<p>No gate failures recorded.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.10 Candidate Mechanism Comparison</title>
<style>{_CSS}</style>
</head>
<body>
<h1>V13.10 Candidate Mechanism Comparison</h1>
<div class="meta">
  <strong>Candidates:</strong> {", ".join(candidates)}<br>
  <strong>Generated:</strong> {created}
</div>

<h2>Gate Pass/Fail</h2>
<div style="overflow-x:auto;">{gate_html}</div>

<h2>Fit Metrics</h2>
<div style="overflow-x:auto;">{metric_html}</div>

<h2>Calibration Parameters</h2>
<div style="overflow-x:auto;">{param_html}</div>

<h2>Gate Failure Detail</h2>
{failures_html}

<h2>Decision Rules (V13.10)</h2>
<div class="note">
<strong>A.</strong> If non-MSM undiagnosed stock is absent across all candidates, multipliers cannot solve composition.<br>
<strong>B.</strong> If V13.10 improves MSM shape but not non-MSM, keep multiplier as sensitivity only.<br>
<strong>C.</strong> If no candidate passes all gates, identify the specific gate that is hardest to satisfy.<br>
<strong>D.</strong> Only run more ABC-SMC after this comparison identifies the bottleneck candidate and mechanism.
</div>
</body>
</html>
"""


def _pivot_table_html(df: pd.DataFrame, cols: list[str]) -> str:
    if not cols or df.empty:
        return "<p>No data.</p>"
    headers = "<th>Metric</th>" + "".join(f"<th>{r}</th>" for r in df.get("candidate", [""]))
    rows_html = ""
    for col in cols:
        if col not in df.columns:
            continue
        cells = f"<td>{col}</td>"
        for _, row in df.iterrows():
            v = row.get(col)
            if isinstance(v, bool):
                cls = "pass" if v else "fail"
                cells += f'<td class="{cls}">{"PASS" if v else "FAIL"}</td>'
            elif v is None or (isinstance(v, float) and not math.isfinite(v)):
                cells += '<td class="na">—</td>'
            elif isinstance(v, float):
                cells += f"<td>{v:.3f}</td>"
            else:
                cells += f"<td>{v}</td>"
        rows_html += f"<tr>{cells}</tr>"
    return f'<table><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>'


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_comparison(args: argparse.Namespace) -> dict:
    candidate_args: list[str] = getattr(args, "candidate", [])
    targets_csv = Path(args.normalized_targets_csv).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    seed = int(getattr(args, "seed", 42))
    start_year = int(getattr(args, "start_year", 1990))
    end_year = int(getattr(args, "end_year", 2025))

    candidates: list[tuple[str, Path]] = []
    for arg in candidate_args:
        name, path = parse_candidate_arg(arg)
        candidates.append((name, Path(path).resolve()))

    if not candidates:
        print("[V13.10-compare] No candidates specified. Use --candidate NAME=PATH.")
        return {}

    print(f"[V13.10-compare] Candidates: {[n for n, _ in candidates]}")
    print(f"[V13.10-compare] Targets: {targets_csv.name}  seed={seed}")

    targets = load_targets(targets_csv, start_year=start_year, end_year=end_year)

    comparison_rows: list[dict] = []
    missing_per_candidate: dict[str, list[str]] = {}

    for name, config_path in candidates:
        print(f"[V13.10-compare] Running candidate: {name} ...", end="", flush=True)
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        result = _run_config_quietly(config, seed=seed, run_end=end_year)
        outputs = extract_annual_outputs(result, start_year=1980, end_year=end_year)
        strat_outputs, missing = extract_stratified_outputs(result, start_year=start_year, end_year=end_year)
        missing_per_candidate[name] = missing

        gate_result = evaluate_all_gates(outputs, targets, enable_historical=True, enable_non_msm=True)
        row = build_candidate_row(
            name, str(config_path), config, outputs, strat_outputs, gate_result, targets,
            latest_year=min(end_year, 2024),
        )
        comparison_rows.append(row)
        n_fail = gate_result.get("total_hard_gate_failures", 0)
        accept = gate_result.get("overall_accept", False)
        print(f" done. accept={accept}, total_failures={n_fail}")

    comparison_df = pd.DataFrame(comparison_rows)

    manifest = {
        "tool": "compare_calibration_candidate_mechanisms",
        "version": "v13.10",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "targets_csv": str(targets_csv),
        "seed": seed,
        "start_year": start_year,
        "end_year": end_year,
        "candidates": [n for n, _ in candidates],
        "candidate_paths": {n: str(p) for n, p in candidates},
        "missing_outputs_per_candidate": missing_per_candidate,
    }

    comparison_csv = output_dir / "candidate_mechanism_comparison.csv"
    comparison_df.to_csv(comparison_csv, index=False)

    html_path = output_dir / "candidate_mechanism_comparison.html"
    html_path.write_text(build_html_report(comparison_df, manifest), encoding="utf-8")

    manifest_path = output_dir / "candidate_mechanism_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print(f"[V13.10-compare] Comparison CSV:  {comparison_csv}")
    print(f"[V13.10-compare] HTML report:     {html_path}")
    print(f"[V13.10-compare] Manifest:        {manifest_path}")

    return {
        "comparison_csv": str(comparison_csv),
        "html_report": str(html_path),
        "manifest": str(manifest_path),
        "comparison_df": comparison_df,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V13.10 multi-candidate mechanism comparison.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--candidate", action="append", default=[],
        metavar="NAME=PATH",
        help="Candidate to compare. Repeat for multiple candidates. Format: NAME=PATH.",
    )
    p.add_argument(
        "--normalized-targets-csv", required=True,
        help="Normalized targets CSV from run_lhs_calibration.",
    )
    p.add_argument("--output-dir", required=True, help="Directory for outputs.")
    p.add_argument("--seed", type=int, default=42, help="RNG seed.")
    p.add_argument("--start-year", type=int, default=1990, help="First year of window.")
    p.add_argument("--end-year", type=int, default=2025, help="Last year of window.")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    run_comparison(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
