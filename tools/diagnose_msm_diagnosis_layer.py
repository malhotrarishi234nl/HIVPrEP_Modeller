#!/usr/bin/env python3
"""
Post-hoc MSM diagnosis-layer diagnostic (V13.9.4).

Reruns one calibration candidate deterministically and computes a year-by-year
breakdown of the MSM diagnosis gap: how much does the model over/under-produce
MSM diagnoses relative to the target, and what post-hoc multiplier would be
needed to close that gap locally?

IMPORTANT CAVEATS
-----------------
The implied MSM output multiplier (= target_msm / model_msm) is a POST-HOC
DIAGNOSTIC ONLY, not a valid model parameter.  Changing the diagnosis rates
mid-run would alter the dynamics of the diagnosed/undiagnosed stock balances
throughout the simulation.  This tool characterises the observed gap; it does
NOT prescribe a causal fix.

Context
-------
The V13.9.3 best candidate passes all recent endpoint gates and all historical
gates except the historical MSM diagnosis layer (MAPE 47.7%, early max ratio
4.32, thresholds 40% / 2.50).  The model uses a single global diagnosis
schedule; it has no MSM-specific ascertainment multiplier.  Literature support
for group-structured testing behaviour: Stolte et al. (2008), Eur J Public
Health.

Usage
-----
python3 tools/diagnose_msm_diagnosis_layer.py \\
    --config outputs/calibration/.../lhs_best_fit_rank_001.json \\
    --normalized-targets-csv outputs/calibration/.../normalized_targets_joint_*.csv \\
    --output-dir outputs/diagnostics/msm_layer_v13_9_4 \\
    --seed 42
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs
from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Era definitions
# ---------------------------------------------------------------------------

_ERAS: list[tuple[str, int, int]] = [
    ("early_1996_2003",  1996, 2003),
    ("mid_1996_2014",    1996, 2014),
    ("recent_2015_2024", 2015, 2024),
    ("latest_2024_only", 2024, 2024),
]

_STABILITY_CV_THRESHOLD = 0.25  # coefficient of variation below which multiplier is "stable"


# ---------------------------------------------------------------------------
# Pure helper functions (unit-testable; no I/O dependencies)
# ---------------------------------------------------------------------------


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """Return numerator / denominator.

    Returns None when denominator is zero rather than raising ZeroDivisionError.
    Zero numerator with non-zero denominator correctly returns 0.0.
    """
    if denominator == 0.0:
        return None
    return numerator / denominator


def _safe_implied_multiplier(target: float, model: float) -> float | None:
    """Return target / model — the factor that would scale model output to target.

    - Returns None when model is zero.
    - Multiplier > 1: model underproduces (target > model).
    - Multiplier < 1: model overproduces (model > target).
    """
    if model == 0.0:
        return None
    return target / model


def _compute_yearly_row(
    year: int,
    model_total: float,
    target_total: float,
    model_msm: float,
    target_msm: float,
) -> dict:
    """Compute one full diagnostic row for a given year.

    All ratio / multiplier fields are None when the relevant denominator is zero.
    """
    model_non_msm = model_total - model_msm
    target_non_msm = target_total - target_msm

    return {
        "year": year,
        "model_total": model_total,
        "target_total": target_total,
        "model_msm": model_msm,
        "target_msm": target_msm,
        "model_non_msm": model_non_msm,
        "target_non_msm": target_non_msm,
        "msm_model_target_ratio": _safe_ratio(model_msm, target_msm),
        "msm_implied_output_multiplier": _safe_implied_multiplier(target_msm, model_msm),
        "non_msm_model_target_ratio": _safe_ratio(model_non_msm, target_non_msm),
        "msm_share_model": _safe_ratio(model_msm, model_total),
        "msm_share_target": _safe_ratio(target_msm, target_total),
    }


def _compute_era_summary(
    yearly_df: pd.DataFrame,
    era_name: str,
    year_min: int,
    year_max: int,
) -> dict:
    """Compute summary statistics over a year window from the yearly DataFrame."""
    subset = yearly_df[
        (yearly_df["year"] >= year_min) & (yearly_df["year"] <= year_max)
    ].copy()

    n = len(subset)
    if n == 0:
        return {
            "era_name": era_name,
            "year_min": year_min,
            "year_max": year_max,
            "n_years": 0,
            "median_msm_ratio": None,
            "max_msm_ratio": None,
            "min_msm_ratio": None,
            "median_implied_multiplier": None,
            "max_implied_multiplier": None,
            "min_implied_multiplier": None,
            "median_non_msm_ratio": None,
            "median_msm_share_model": None,
            "median_msm_share_target": None,
        }

    def _stat_finite(col: str, fn) -> float | None:
        vals = subset[col].dropna()
        vals = pd.to_numeric(vals, errors="coerce").dropna()
        vals = vals[vals.apply(math.isfinite)]
        return float(fn(vals)) if len(vals) > 0 else None

    return {
        "era_name": era_name,
        "year_min": year_min,
        "year_max": year_max,
        "n_years": n,
        "median_msm_ratio": _stat_finite("msm_model_target_ratio", pd.Series.median),
        "max_msm_ratio": _stat_finite("msm_model_target_ratio", pd.Series.max),
        "min_msm_ratio": _stat_finite("msm_model_target_ratio", pd.Series.min),
        "median_implied_multiplier": _stat_finite("msm_implied_output_multiplier", pd.Series.median),
        "max_implied_multiplier": _stat_finite("msm_implied_output_multiplier", pd.Series.max),
        "min_implied_multiplier": _stat_finite("msm_implied_output_multiplier", pd.Series.min),
        "median_non_msm_ratio": _stat_finite("non_msm_model_target_ratio", pd.Series.median),
        "median_msm_share_model": _stat_finite("msm_share_model", pd.Series.median),
        "median_msm_share_target": _stat_finite("msm_share_target", pd.Series.median),
    }


def _multiplier_stability(values: list[float]) -> str:
    """Classify the implied multiplier time series as 'stable' or 'time_varying'.

    Stable if the coefficient of variation (std/mean) is below the threshold.
    Returns 'insufficient_data' if fewer than 2 finite values are available.
    """
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if len(finite) < 2:
        return "insufficient_data"
    mean = sum(finite) / len(finite)
    if mean == 0.0:
        return "insufficient_data"
    variance = sum((v - mean) ** 2 for v in finite) / len(finite)
    cv = math.sqrt(variance) / abs(mean)
    return "stable" if cv <= _STABILITY_CV_THRESHOLD else "time_varying"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_targets_csv(
    path: str | Path,
    start_year: int = 1990,
    end_year: int = 2025,
) -> dict[str, dict[int, dict]]:
    """Load a normalized targets CSV and return {model_output: {year: row_dict}}."""
    df = pd.read_csv(path)
    required_outputs = {"new_diagnoses", "new_diagnoses_msm"}
    present = set(df["model_output"].unique()) if "model_output" in df.columns else set()
    missing = required_outputs - present
    if missing:
        print(
            f"WARNING: targets CSV missing model_output(s): {sorted(missing)}. "
            "Affected columns will be blank in the yearly table."
        )

    targets: dict[str, dict[int, dict]] = {}
    for _, row in df.iterrows():
        output = str(row.get("model_output", ""))
        if output not in required_outputs:
            continue
        year = row.get("year")
        if year is None or (hasattr(year, "__float__") and math.isnan(float(year))):
            continue
        year = int(year)
        if year < start_year or year > end_year:
            continue
        targets.setdefault(output, {})[year] = {
            "estimate": float(row.get("estimate", float("nan"))),
            "lower": row.get("lower"),
            "upper": row.get("upper"),
        }
    return targets


def _run_config_quietly(config_path: str | Path, seed: int) -> dict:
    """Load, configure, and run the simulation deterministically; suppress stdout."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    config["run_control"]["deterministic_mode"]["value"] = True
    config["run_control"]["start_year"]["value"] = 1980
    config["run_control"]["end_year"]["value"] = 2026
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return result


# ---------------------------------------------------------------------------
# Diagnostic table builders
# ---------------------------------------------------------------------------


def _build_yearly_table(
    outputs: dict[str, dict[int, float]],
    targets: dict[str, dict[int, dict]],
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """Build the year-by-year diagnostic DataFrame."""
    model_total_map = outputs.get("new_diagnoses", {})
    model_msm_map = outputs.get("new_diagnoses_msm", {})
    target_total_map = {y: v["estimate"] for y, v in targets.get("new_diagnoses", {}).items()}
    target_msm_map = {y: v["estimate"] for y, v in targets.get("new_diagnoses_msm", {}).items()}

    # Union of years covered by either model or target
    all_years = sorted(
        y for y in set(model_total_map) | set(target_total_map) | set(model_msm_map) | set(target_msm_map)
        if start_year <= y <= end_year
    )

    rows = []
    for year in all_years:
        model_total = model_total_map.get(year)
        target_total = target_total_map.get(year)
        model_msm = model_msm_map.get(year)
        target_msm = target_msm_map.get(year)

        # Only compute ratios when both model and target are available
        if (
            model_total is not None
            and target_total is not None
            and model_msm is not None
            and target_msm is not None
        ):
            row = _compute_yearly_row(year, model_total, target_total, model_msm, target_msm)
        else:
            row = {
                "year": year,
                "model_total": model_total,
                "target_total": target_total,
                "model_msm": model_msm,
                "target_msm": target_msm,
                "model_non_msm": (
                    model_total - model_msm
                    if model_total is not None and model_msm is not None
                    else None
                ),
                "target_non_msm": (
                    target_total - target_msm
                    if target_total is not None and target_msm is not None
                    else None
                ),
                "msm_model_target_ratio": None,
                "msm_implied_output_multiplier": None,
                "non_msm_model_target_ratio": None,
                "msm_share_model": None,
                "msm_share_target": None,
            }
        rows.append(row)

    return pd.DataFrame(rows)


def _build_era_summaries(yearly_df: pd.DataFrame) -> list[dict]:
    return [_compute_era_summary(yearly_df, name, y0, y1) for name, y0, y1 in _ERAS]


def _build_summary_json(
    yearly_df: pd.DataFrame,
    era_summaries: list[dict],
    config_path: str,
    args,
) -> dict:
    """Build the interpretation summary dict."""
    # Find early era (1996-2003)
    early = next((e for e in era_summaries if e["era_name"] == "early_1996_2003"), {})
    latest = next((e for e in era_summaries if e["era_name"] == "latest_2024_only"), {})
    recent = next((e for e in era_summaries if e["era_name"] == "recent_2015_2024"), {})

    # Latest year values
    latest_year_row = None
    if not yearly_df.empty:
        last_rows = yearly_df[yearly_df["year"] == yearly_df["year"].max()]
        if not last_rows.empty:
            latest_year_row = last_rows.iloc[0].to_dict()

    multiplier_values = (
        yearly_df["msm_implied_output_multiplier"].dropna().tolist()
        if "msm_implied_output_multiplier" in yearly_df.columns
        else []
    )

    # Stability over the full window
    stability = _multiplier_stability(multiplier_values)

    return {
        "diagnostic_version": "v13.9.4",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(config_path),
        "calibration_note": (
            "V13.9.3 best candidate: passes all recent endpoint and historical gates "
            "except historical MSM diagnosis layer (MAPE 47.7%, early max ratio 4.32)."
        ),
        "interpretation_caveats": (
            "Implied multipliers are LOCAL POST-HOC DIAGNOSTICS ONLY. "
            "They do not account for feedback through diagnosed/undiagnosed stock dynamics. "
            "Reference: Stolte et al. (2008) Eur J Public Health — group-structured "
            "testing behaviour in the Netherlands."
        ),
        "start_year": int(getattr(args, "start_year", 1990)),
        "end_year": int(getattr(args, "end_year", 2025)),
        "n_years_with_full_data": int(
            yearly_df[["msm_model_target_ratio"]].notna().sum().iloc[0]
            if not yearly_df.empty
            else 0
        ),
        # Key metrics
        "worst_early_msm_ratio": early.get("max_msm_ratio"),
        "median_early_msm_ratio": early.get("median_msm_ratio"),
        "median_early_implied_multiplier": early.get("median_implied_multiplier"),
        "latest_msm_ratio": (
            latest_year_row.get("msm_model_target_ratio") if latest_year_row else None
        ),
        "latest_implied_multiplier": (
            latest_year_row.get("msm_implied_output_multiplier") if latest_year_row else None
        ),
        "latest_model_non_msm": (
            latest_year_row.get("model_non_msm") if latest_year_row else None
        ),
        "latest_target_non_msm": (
            latest_year_row.get("target_non_msm") if latest_year_row else None
        ),
        "multiplier_stability": stability,
        "multiplier_stability_note": (
            "Stable: coefficient of variation of implied multiplier <= 0.25 across all years. "
            "Time_varying: larger spread — changing diagnosis rates would shift different "
            "year bands by different amounts, making a single multiplier an over-simplification."
        ),
        "recent_median_msm_ratio": recent.get("median_msm_ratio"),
        "recent_median_implied_multiplier": recent.get("median_implied_multiplier"),
        "era_summaries": era_summaries,
    }


# ---------------------------------------------------------------------------
# SVG chart helper
# ---------------------------------------------------------------------------


def _svg_chart(
    title: str,
    years: list[int],
    series: list[dict],  # [{"label": str, "color": str, "values": list[float|None]}]
    width: int = 620,
    height: int = 170,
) -> str:
    """Generate a simple inline SVG line chart with multiple series."""
    PAD_L, PAD_R, PAD_T, PAD_B = 48, 12, 22, 32
    pw = width - PAD_L - PAD_R
    ph = height - PAD_T - PAD_B

    # Collect all finite values for y-range
    all_vals = [
        float(v)
        for s in series
        for v in s["values"]
        if v is not None and math.isfinite(float(v))
    ]
    if not all_vals or not years:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<text x="10" y="30" font-size="11" fill="#999">No data — {title}</text>'
            f"</svg>"
        )

    y_min, y_max = min(all_vals), max(all_vals)
    y_pad = max((y_max - y_min) * 0.08, 0.01)
    y_min -= y_pad
    y_max += y_pad
    y_rng = y_max - y_min

    n = len(years)

    def px(i: int) -> float:
        return PAD_L + (i / max(n - 1, 1)) * pw

    def py(v: float) -> float:
        return PAD_T + (1.0 - (v - y_min) / y_rng) * ph

    elements: list[str] = []

    # Title
    elements.append(
        f'<text x="{width // 2}" y="14" text-anchor="middle" '
        f'font-size="11" font-weight="bold" fill="#333">{title}</text>'
    )

    # Background grid lines (3 horizontal)
    for pct in (0.0, 0.5, 1.0):
        v = y_min + pct * y_rng
        y = py(v)
        elements.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + pw}" y2="{y:.1f}" '
            f'stroke="#e8e8e8" stroke-dasharray="3 3"/>'
        )
        elements.append(
            f'<text x="{PAD_L - 3}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="8" fill="#999">{v:.2f}</text>'
        )

    # Year axis ticks
    for i, yr in enumerate(years):
        if yr % 5 == 0:
            x = px(i)
            elements.append(
                f'<line x1="{x:.1f}" y1="{PAD_T + ph}" x2="{x:.1f}" '
                f'y2="{PAD_T + ph + 4}" stroke="#ccc"/>'
            )
            elements.append(
                f'<text x="{x:.1f}" y="{PAD_T + ph + 14}" text-anchor="middle" '
                f'font-size="8" fill="#999">{yr}</text>'
            )

    # Series paths
    for si, s in enumerate(series):
        color = s.get("color", "#2196F3")
        label = s.get("label", f"series{si}")
        vals = s["values"]

        # Split at None/non-finite values
        segments: list[list[tuple[float, float]]] = []
        seg: list[tuple[float, float]] = []
        for i, v in enumerate(vals):
            if v is not None and math.isfinite(float(v)):
                seg.append((px(i), py(float(v))))
            else:
                if seg:
                    segments.append(seg)
                    seg = []
        if seg:
            segments.append(seg)

        for seg in segments:
            d = " ".join(
                f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}"
                for j, (x, y) in enumerate(seg)
            )
            elements.append(
                f'<path d="{d}" stroke="{color}" stroke-width="1.8" fill="none" stroke-linejoin="round"/>'
            )

        # Legend entry
        lx = PAD_L + si * 140
        ly = height - 6
        elements.append(f'<line x1="{lx}" y1="{ly}" x2="{lx + 18}" y2="{ly}" stroke="{color}" stroke-width="2"/>')
        elements.append(
            f'<text x="{lx + 22}" y="{ly + 3.5}" font-size="9" fill="#555">{label}</text>'
        )

    # Border
    elements.append(
        f'<rect x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}" '
        f'fill="none" stroke="#ccc"/>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="background:#fafafa; display:block;">'
        + "".join(elements)
        + "</svg>"
    )


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------

_HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       font-size: 13px; margin: 0; padding: 20px 32px; color: #222; background: #fff; }
h1 { font-size: 20px; margin-bottom: 4px; color: #1a1a2e; }
h2 { font-size: 15px; margin: 24px 0 8px; color: #16213e; border-bottom: 2px solid #e0e0e0; padding-bottom: 4px; }
h3 { font-size: 13px; margin: 16px 0 6px; color: #333; }
.meta { color: #666; font-size: 12px; margin-bottom: 20px; }
.caveat { background: #fff8e1; border-left: 4px solid #f9a825; padding: 10px 14px;
           margin: 12px 0; border-radius: 2px; font-size: 12px; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 10px; margin: 12px 0; }
.kpi { background: #f5f7fa; border: 1px solid #dde; border-radius: 4px;
       padding: 10px 14px; }
.kpi-label { font-size: 11px; color: #888; margin-bottom: 2px; }
.kpi-value { font-size: 18px; font-weight: 600; color: #1a1a2e; }
.kpi-value.warn { color: #e65100; }
.kpi-value.ok { color: #2e7d32; }
table { border-collapse: collapse; width: 100%; font-size: 12px; margin: 8px 0; }
th { background: #f0f2f5; text-align: right; padding: 5px 8px; border: 1px solid #d0d4d8;
     font-weight: 600; color: #444; white-space: nowrap; }
th:first-child { text-align: left; }
td { padding: 4px 8px; border: 1px solid #e8eaed; text-align: right; }
td:first-child { text-align: left; font-weight: 500; }
tr:nth-child(even) td { background: #fafbfc; }
.ratio-high { color: #c62828; font-weight: 600; }
.ratio-ok   { color: #2e7d32; }
.ratio-mid  { color: #e65100; }
.chart-wrap { margin: 10px 0 4px; }
.section { margin-bottom: 32px; }
"""


def _fmt(v, decimals: int = 3, missing: str = "—") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return missing
    return f"{v:.{decimals}f}"


def _ratio_cls(ratio: float | None) -> str:
    if ratio is None or not math.isfinite(ratio):
        return ""
    if ratio > 2.5:
        return "ratio-high"
    if ratio > 1.5:
        return "ratio-mid"
    return "ratio-ok"


def _build_html_report(
    yearly_df: pd.DataFrame,
    era_summaries: list[dict],
    summary: dict,
    config_path: str,
) -> str:
    years = sorted(yearly_df["year"].tolist()) if not yearly_df.empty else []

    def col_vals(col: str) -> list[float | None]:
        if col not in yearly_df.columns:
            return [None] * len(years)
        return [
            float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
            for v in yearly_df.set_index("year").reindex(years)[col].tolist()
        ]

    # --- Charts ---
    chart_msm_counts = _svg_chart(
        "MSM Diagnoses: Model vs Target",
        years,
        [
            {"label": "Model MSM", "color": "#1565C0", "values": col_vals("model_msm")},
            {"label": "Target MSM", "color": "#BF360C", "values": col_vals("target_msm")},
        ],
    )
    chart_msm_ratio = _svg_chart(
        "Model / Target MSM Ratio (1.0 = perfect match)",
        years,
        [{"label": "MSM model/target ratio", "color": "#6A1B9A", "values": col_vals("msm_model_target_ratio")}],
    )
    chart_multiplier = _svg_chart(
        "Implied MSM Output Multiplier (target / model)  [POST-HOC ONLY]",
        years,
        [{"label": "Implied multiplier", "color": "#E65100", "values": col_vals("msm_implied_output_multiplier")}],
    )
    chart_non_msm = _svg_chart(
        "Non-MSM Diagnoses: Model vs Target",
        years,
        [
            {"label": "Model non-MSM", "color": "#2E7D32", "values": col_vals("model_non_msm")},
            {"label": "Target non-MSM", "color": "#AD1457", "values": col_vals("target_non_msm")},
        ],
    )
    chart_shares = _svg_chart(
        "MSM Share of Total Diagnoses",
        years,
        [
            {"label": "Model MSM share", "color": "#1565C0", "values": col_vals("msm_share_model")},
            {"label": "Target MSM share", "color": "#BF360C", "values": col_vals("msm_share_target")},
        ],
    )

    # --- KPI values ---
    def _kpi_cls(v, threshold_warn=2.5, threshold_ok=1.5):
        if v is None:
            return ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return ""
        if fv > threshold_warn:
            return "warn"
        if fv <= threshold_ok:
            return "ok"
        return ""

    kpis = [
        ("Worst early MSM ratio", summary.get("worst_early_msm_ratio"), "x"),
        ("Median early MSM ratio", summary.get("median_early_msm_ratio"), "x"),
        ("Median early implied mult.", summary.get("median_early_implied_multiplier"), "x"),
        ("Latest MSM ratio", summary.get("latest_msm_ratio"), "x"),
        ("Latest implied multiplier", summary.get("latest_implied_multiplier"), "x"),
        ("Multiplier stability", summary.get("multiplier_stability"), ""),
    ]

    kpi_html = '<div class="summary-grid">'
    for label, val, unit in kpis:
        if isinstance(val, float) and not math.isfinite(val):
            val = None
        cls = _kpi_cls(val) if isinstance(val, (int, float)) else ""
        display = (
            f"{val:.3f}{unit}" if isinstance(val, (int, float)) and val is not None
            else (str(val) if val is not None else "—")
        )
        kpi_html += (
            f'<div class="kpi">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value {cls}">{display}</div>'
            f"</div>"
        )
    kpi_html += "</div>"

    # --- Yearly table ---
    def _row_html(r: dict) -> str:
        ratio = r.get("msm_model_target_ratio")
        cls = _ratio_cls(ratio)
        return (
            f'<tr>'
            f'<td>{int(r["year"])}</td>'
            f'<td>{_fmt(r.get("model_total"), 0)}</td>'
            f'<td>{_fmt(r.get("target_total"), 0)}</td>'
            f'<td>{_fmt(r.get("model_msm"), 0)}</td>'
            f'<td>{_fmt(r.get("target_msm"), 0)}</td>'
            f'<td>{_fmt(r.get("model_non_msm"), 0)}</td>'
            f'<td>{_fmt(r.get("target_non_msm"), 0)}</td>'
            f'<td class="{cls}">{_fmt(ratio)}</td>'
            f'<td>{_fmt(r.get("msm_implied_output_multiplier"))}</td>'
            f'<td>{_fmt(r.get("non_msm_model_target_ratio"))}</td>'
            f'<td>{_fmt(r.get("msm_share_model"))}</td>'
            f'<td>{_fmt(r.get("msm_share_target"))}</td>'
            f"</tr>"
        )

    yearly_rows_html = (
        "".join(_row_html(r) for r in yearly_df.to_dict("records"))
        if not yearly_df.empty
        else "<tr><td colspan='12'>No data</td></tr>"
    )

    # --- Era summary table ---
    def _era_row_html(e: dict) -> str:
        ratio = e.get("median_msm_ratio")
        cls = _ratio_cls(ratio)
        return (
            f'<tr>'
            f'<td>{e["era_name"].replace("_", " ")}</td>'
            f'<td>{e["year_min"]}–{e["year_max"]}</td>'
            f'<td>{e["n_years"]}</td>'
            f'<td class="{cls}">{_fmt(e.get("median_msm_ratio"))}</td>'
            f'<td class="{_ratio_cls(e.get("max_msm_ratio"))}">{_fmt(e.get("max_msm_ratio"))}</td>'
            f'<td>{_fmt(e.get("min_msm_ratio"))}</td>'
            f'<td>{_fmt(e.get("median_implied_multiplier"))}</td>'
            f'<td>{_fmt(e.get("median_non_msm_ratio"))}</td>'
            f'<td>{_fmt(e.get("median_msm_share_model"))}</td>'
            f'<td>{_fmt(e.get("median_msm_share_target"))}</td>'
            f"</tr>"
        )

    era_rows_html = "".join(_era_row_html(e) for e in era_summaries)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.9.4 MSM Diagnosis Layer Diagnostic</title>
<style>
{_HTML_CSS}
</style>
</head>
<body>
<h1>V13.9.4 Post-Hoc MSM Diagnosis Layer Diagnostic</h1>
<div class="meta">
  <strong>Config:</strong> {config_path}<br>
  <strong>Generated:</strong> {summary.get("created_at", "")}<br>
  <strong>Window:</strong> {summary.get("start_year")} – {summary.get("end_year")}
</div>

<div class="caveat">
  <strong>⚠ Caveat:</strong> {summary.get("interpretation_caveats", "")}
</div>

<h2>Key Interpretation Metrics</h2>
{kpi_html}

<div class="section">
<h2>Section 1 — MSM Diagnoses: Model vs Target</h2>
<h3>Chart</h3>
<div class="chart-wrap">{chart_msm_counts}</div>
</div>

<div class="section">
<h2>Section 2 — Model / Target MSM Ratio</h2>
<p>Ratio > 1 means the model produces more MSM diagnoses than observed.
Gate threshold (historical MSM early max): ≤ 2.50.</p>
<div class="chart-wrap">{chart_msm_ratio}</div>
</div>

<div class="section">
<h2>Section 3 — Implied MSM Output Multiplier</h2>
<p>target_msm / model_msm — the factor needed to rescale model output to match
target post-hoc. Values &lt; 1 indicate model overproduces MSM diagnoses.
<strong>This is a local diagnostic, not a valid parameter.</strong></p>
<div class="chart-wrap">{chart_multiplier}</div>
</div>

<div class="section">
<h2>Section 4 — Non-MSM Diagnoses: Model vs Target</h2>
<div class="chart-wrap">{chart_non_msm}</div>
</div>

<div class="section">
<h2>Section 5 — MSM Share of Total Diagnoses</h2>
<div class="chart-wrap">{chart_shares}</div>
</div>

<h2>Year-by-Year Detail</h2>
<div style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Year</th>
  <th>Model total</th><th>Target total</th>
  <th>Model MSM</th><th>Target MSM</th>
  <th>Model non-MSM</th><th>Target non-MSM</th>
  <th>MSM ratio (m/t)</th><th>Implied mult. (t/m)</th>
  <th>Non-MSM ratio</th>
  <th>MSM share model</th><th>MSM share target</th>
</tr>
</thead>
<tbody>
{yearly_rows_html}
</tbody>
</table>
</div>

<h2>Era Summaries</h2>
<div style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Era</th><th>Years</th><th>N</th>
  <th>Median MSM ratio</th><th>Max MSM ratio</th><th>Min MSM ratio</th>
  <th>Median implied mult.</th>
  <th>Median non-MSM ratio</th>
  <th>Median MSM share (model)</th><th>Median MSM share (target)</th>
</tr>
</thead>
<tbody>
{era_rows_html}
</tbody>
</table>
</div>

<h2>Notes</h2>
<ul>
  <li><strong>MSM ratio</strong> = model_msm / target_msm.
      Gate ceiling (historical MSM early max, 1996–2003): ≤ 2.50.</li>
  <li><strong>Implied multiplier</strong> = target_msm / model_msm.
      Post-hoc only — does not account for stock dynamics.</li>
  <li><strong>Non-MSM ratio</strong> = model_non_msm / target_non_msm.
      Cross-check: if non-MSM fit is good but MSM is not, the issue is MSM-specific.</li>
  <li><strong>Multiplier stability</strong>: {summary.get("multiplier_stability")} —
      stable if coefficient of variation ≤ {_STABILITY_CV_THRESHOLD}.</li>
  <li>Reference: Stolte et al. (2008), Eur J Public Health — group-structured HIV
      testing behaviour in the Netherlands.</li>
</ul>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_diagnostic(args: argparse.Namespace) -> dict:
    config_path = Path(args.config).resolve()
    targets_csv_path = Path(args.normalized_targets_csv).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    start_year = int(getattr(args, "start_year", 1990))
    end_year = int(getattr(args, "end_year", 2025))
    seed = int(getattr(args, "seed", 42))

    print(f"[V13.9.4] Config: {config_path.name}")
    print(f"[V13.9.4] Targets: {targets_csv_path.name}")
    print(f"[V13.9.4] Window: {start_year}–{end_year}  seed={seed}")

    # 1. Run model
    print("[V13.9.4] Running simulation deterministically (1980–2025)...")
    result = _run_config_quietly(config_path, seed=seed)
    outputs = extract_annual_outputs(result, start_year=1980, end_year=2025)
    print(f"[V13.9.4] Extracted outputs: {sorted(outputs.keys())}")

    # 2. Load targets
    targets = _load_targets_csv(targets_csv_path, start_year=start_year, end_year=end_year)
    print(f"[V13.9.4] Targets loaded for outputs: {sorted(targets.keys())}")

    # 3. Build tables
    yearly_df = _build_yearly_table(outputs, targets, start_year, end_year)
    era_summaries = _build_era_summaries(yearly_df)
    summary = _build_summary_json(yearly_df, era_summaries, config_path, args)

    # 4. Write outputs
    yearly_csv = output_dir / "msm_diagnosis_layer_yearly.csv"
    yearly_df.to_csv(yearly_csv, index=False)

    era_csv = output_dir / "msm_diagnosis_layer_era_summary.csv"
    pd.DataFrame(era_summaries).to_csv(era_csv, index=False)

    summary_json = output_dir / "msm_diagnosis_layer_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    html_report = output_dir / "msm_diagnosis_layer_report.html"
    html_report.write_text(
        _build_html_report(yearly_df, era_summaries, summary, str(config_path)),
        encoding="utf-8",
    )

    print(f"[V13.9.4] Yearly CSV:     {yearly_csv}")
    print(f"[V13.9.4] Era CSV:        {era_csv}")
    print(f"[V13.9.4] Summary JSON:   {summary_json}")
    print(f"[V13.9.4] HTML report:    {html_report}")

    # Print headline metrics
    wsm = summary.get("worst_early_msm_ratio")
    med_early = summary.get("median_early_msm_ratio")
    lat_ratio = summary.get("latest_msm_ratio")
    lat_mult = summary.get("latest_implied_multiplier")
    stab = summary.get("multiplier_stability")
    print(
        f"\n[V13.9.4] Headline:\n"
        f"  Worst early MSM ratio (1996-2003):  {_fmt(wsm)}\n"
        f"  Median early MSM ratio (1996-2003): {_fmt(med_early)}\n"
        f"  Latest MSM ratio:                   {_fmt(lat_ratio)}\n"
        f"  Latest implied multiplier:          {_fmt(lat_mult)}\n"
        f"  Multiplier stability:               {stab}"
    )

    return {
        "yearly_csv": str(yearly_csv),
        "era_csv": str(era_csv),
        "summary_json": str(summary_json),
        "html_report": str(html_report),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V13.9.4 post-hoc MSM diagnosis-layer diagnostic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to calibrated candidate config JSON.",
    )
    parser.add_argument(
        "--normalized-targets-csv",
        required=True,
        help=(
            "Normalized targets CSV written by run_lhs_calibration.py "
            "(write_normalized_targets_csv)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for output files.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for deterministic simulation run.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1990,
        help="First year included in the diagnostic window.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2025,
        help="Last year included in the diagnostic window.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    run_diagnostic(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
