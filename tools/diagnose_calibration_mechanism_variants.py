#!/usr/bin/env python3
"""
V13.10 Targeted mechanism variant diagnostic.

Runs deterministic model variants for one candidate config and compares
outcomes across six mechanism configurations.  Used to identify whether
the missing non-MSM stock, the diagnosis multiplier, or insufficient local
search is the primary bottleneck before running more ABC-SMC.

Variants (variant_set=v13_10_mechanism_decomp):
  A  baseline                  — config unchanged
  B  msm_diag_multiplier_only  — MSM relative diagnosis: 1996=0.35, 2004=0.65, 2015=0.70
  C  msm_diag_multiplier_soft  — MSM relative diagnosis: 1996=0.50, 2004=0.75, 2015=0.75
  D  msm_diag_multiplier_strong— MSM relative diagnosis: 1996=0.25, 2004=0.65, 2015=0.60
  E  seed_distribution_current — unchanged; explicitly reports seed shares
  F  combined_current          — candidate seed as-is + MSM multiplier 1996=0.35, 2004=0.65, 2015=0.70

Usage
-----
python3 tools/diagnose_calibration_mechanism_variants.py \\
    --config outputs/calibration/.../lhs_best_fit_rank_001.json \\
    --normalized-targets-csv outputs/calibration/.../normalized_targets_joint_*.csv \\
    --output-dir outputs/diagnostics/mechanism_variants \\
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
from typing import Dict, Iterable, List, Mapping, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs
from config_utils import extract_value
from run_v3 import SimulationEngineV3

try:
    from tools.stratified_output_extractor import extract_stratified_outputs, ALL_STRATIFIED_FIELDS
except ImportError:
    sys.path.insert(0, str(ROOT / "tools"))
    from stratified_output_extractor import extract_stratified_outputs, ALL_STRATIFIED_FIELDS


# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

_VARIANT_KEYS: list[str] = [
    "baseline",
    "msm_diag_multiplier_only",
    "msm_diag_multiplier_soft",
    "msm_diag_multiplier_strong",
    "seed_distribution_current",
    "combined_current",
]

_VARIANT_MSM_SCHEDULES: dict[str, dict[int, float] | None] = {
    "baseline":                 None,
    "msm_diag_multiplier_only": {1996: 0.35, 2004: 0.65, 2015: 0.70},
    "msm_diag_multiplier_soft": {1996: 0.50, 2004: 0.75, 2015: 0.75},
    "msm_diag_multiplier_strong": {1996: 0.25, 2004: 0.65, 2015: 0.60},
    "seed_distribution_current": None,
    "combined_current":         {1996: 0.35, 2004: 0.65, 2015: 0.70},
}

_VARIANT_DESCRIPTIONS: dict[str, str] = {
    "baseline": "Config unchanged.",
    "msm_diag_multiplier_only": "MSM relative diagnosis enabled: 1996=0.35, 2004=0.65, 2015=0.70.",
    "msm_diag_multiplier_soft": "MSM relative diagnosis enabled: 1996=0.50, 2004=0.75, 2015=0.75.",
    "msm_diag_multiplier_strong": "MSM relative diagnosis enabled: 1996=0.25, 2004=0.65, 2015=0.60.",
    "seed_distribution_current": "Config unchanged; seed distribution reported explicitly.",
    "combined_current": "Candidate seed as-is + MSM multiplier 1996=0.35, 2004=0.65, 2015=0.70.",
}

_STANDARD_OUTPUT_FIELDS: list[str] = [
    "PLHIV",
    "new_infections",
    "aids_deaths",
    "new_aids_cases",
    "on_art_stock",
    "new_diagnoses",
    "new_diagnoses_msm",
]

_ALL_YEARLY_FIELDS: list[str] = (
    _STANDARD_OUTPUT_FIELDS
    + ["new_diagnoses_non_msm"]
    + ALL_STRATIFIED_FIELDS
)


# ---------------------------------------------------------------------------
# Config patching (pure functions — no I/O)
# ---------------------------------------------------------------------------


def patch_config_for_variant(config: dict, variant_key: str) -> dict:
    """Return a deep-copied, patched config for the given variant.

    The original config dict is never modified.
    """
    if variant_key not in _VARIANT_KEYS:
        raise ValueError(f"Unknown variant '{variant_key}'. Known: {_VARIANT_KEYS}")

    cfg = copy.deepcopy(config)
    schedule = _VARIANT_MSM_SCHEDULES[variant_key]

    if schedule is not None:
        cfg = _inject_msm_multiplier_schedule(cfg, schedule)

    return cfg


def _inject_msm_multiplier_schedule(config: dict, schedule: dict[int, float]) -> dict:
    """Enable msm_relative_diagnosis with the given schedule (in-place on a copy)."""
    diag = (
        config
        .setdefault("transmission_model", {})
        .setdefault("disease", {})
        .setdefault("diagnosis", {})
    )
    diag["msm_relative_diagnosis"] = {
        "enabled": {"value": True},
        "schedule": {
            "interpolation": {"value": "stepwise"},
            "value": {str(k): float(v) for k, v in schedule.items()},
        },
    }
    return config


# ---------------------------------------------------------------------------
# Config parameter extraction (pure — no I/O)
# ---------------------------------------------------------------------------


def _cfg_read(config: dict, *keys) -> object:
    """Navigate nested config dicts, unwrapping {'value': X} wrappers."""
    node = config
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
        if node is None:
            return None
    return extract_value(node)


def extract_config_params(config: dict) -> dict:
    """Extract key calibration parameters from a config dict for reporting."""
    tm = config.get("transmission_model", {})
    pop = config.get("population", {})
    seed_block = pop.get("epidemic_seed", {})
    diag_block = tm.get("disease", {}).get("diagnosis", {})
    art_block = config.get("interventions", {}).get("art", {})

    params: dict = {}

    params["p_msm"] = _cfg_read(pop, "p_msm")
    params["seed_prevalence"] = _cfg_read(seed_block, "seed_prevalence")
    params["seed_msm_fraction"] = _cfg_read(seed_block, "msm_fraction")

    stage_dist = _cfg_read(seed_block, "stage_distribution")
    if isinstance(stage_dist, dict):
        params["seed_chronic_share"] = stage_dist.get("Chronic")
        params["seed_aids_share"] = stage_dist.get("AIDS")
        params["seed_acute_share"] = stage_dist.get("Acute")
    else:
        params["seed_chronic_share"] = None
        params["seed_aids_share"] = None
        params["seed_acute_share"] = None

    orient_mult = _cfg_read(tm, "groups", "advanced", "orientation_contact_multipliers")
    params["orient_msm"] = orient_mult.get("MSM") if isinstance(orient_mult, dict) else None

    diag_sched = _cfg_read(diag_block, "schedule")
    if isinstance(diag_sched, dict):
        for yr in ("1985", "1990", "1997", "2015"):
            params[f"diagnosis_{yr}"] = diag_sched.get(yr)

    msm_diag_block = diag_block.get("msm_relative_diagnosis", {})
    msm_enabled = _cfg_read(msm_diag_block, "enabled")
    if msm_enabled:
        msm_sched = _cfg_read(msm_diag_block, "schedule")
        if isinstance(msm_sched, dict):
            for yr in ("1996", "2004", "2015"):
                params[f"msm_diag_mult_{yr}"] = msm_sched.get(yr)

    art_sched = _cfg_read(art_block, "schedule", "initiation")
    if isinstance(art_sched, dict):
        for yr in ("1996", "2000", "2015", "2020"):
            params[f"art_init_{yr}"] = art_sched.get(yr)

    params["aids_mortality"] = _cfg_read(tm, "disease", "mortality", "aids")
    params["art_mortality_reduction"] = _cfg_read(tm, "disease", "advanced", "art_mortality_reduction")

    return params


# ---------------------------------------------------------------------------
# Targets loading
# ---------------------------------------------------------------------------


def load_targets(path: str | Path, start_year: int = 1990, end_year: int = 2025) -> list[dict]:
    """Load normalized targets CSV as a list of row dicts (model_output, year, estimate, lower, upper)."""
    df = pd.read_csv(path)
    rows = []
    for _, row in df.iterrows():
        year = row.get("year")
        if year is None or (isinstance(year, float) and math.isnan(year)):
            continue
        year = int(year)
        if not start_year <= year <= end_year:
            continue
        rows.append({
            "model_output": str(row.get("model_output", "")),
            "year": year,
            "estimate": float(row.get("estimate", float("nan"))),
            "lower": row.get("lower"),
            "upper": row.get("upper"),
        })
    return rows


# ---------------------------------------------------------------------------
# Summary metric helpers (pure — no I/O)
# ---------------------------------------------------------------------------


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or not (_finite(num) and _finite(den)) or den == 0.0:
        return None
    return num / den


def _median_abs_pct_error(
    outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output_name: str,
    start_year: int,
    end_year: int,
) -> float | None:
    errors = []
    for t in targets:
        if t["model_output"] != output_name:
            continue
        yr = t["year"]
        if not start_year <= yr <= end_year:
            continue
        est = t["estimate"]
        model_val = outputs.get(output_name, {}).get(yr)
        if model_val is None or not (_finite(est) and _finite(model_val)) or est == 0:
            continue
        errors.append(abs((model_val - est) / est) * 100.0)
    return median(errors) if errors else None


def _median_ratio(
    outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output_name: str,
    start_year: int,
    end_year: int,
) -> float | None:
    ratios = []
    for t in targets:
        if t["model_output"] != output_name:
            continue
        yr = t["year"]
        if not start_year <= yr <= end_year:
            continue
        est = t["estimate"]
        model_val = outputs.get(output_name, {}).get(yr)
        if model_val is None or not (_finite(est) and _finite(model_val)) or est == 0:
            continue
        ratios.append(model_val / est)
    return median(ratios) if ratios else None


def _max_ratio(
    outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output_name: str,
    start_year: int,
    end_year: int,
) -> float | None:
    ratios = []
    for t in targets:
        if t["model_output"] != output_name:
            continue
        yr = t["year"]
        if not start_year <= yr <= end_year:
            continue
        est = t["estimate"]
        model_val = outputs.get(output_name, {}).get(yr)
        if model_val is None or not (_finite(est) and _finite(model_val)) or est == 0:
            continue
        ratios.append(model_val / est)
    return max(ratios) if ratios else None


def _aids_bounds_fraction(
    outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
) -> float | None:
    comparable = 0
    within = 0
    for t in targets:
        if t["model_output"] != "aids_deaths":
            continue
        yr = t["year"]
        model_val = outputs.get("aids_deaths", {}).get(yr)
        if model_val is None:
            continue
        lower = t.get("lower")
        upper = t.get("upper")
        try:
            lower = float(lower) if lower is not None else None
            upper = float(upper) if upper is not None else None
        except (TypeError, ValueError):
            lower = upper = None
        if lower is None or upper is None:
            continue
        comparable += 1
        if lower <= model_val <= upper:
            within += 1
    return within / comparable if comparable > 0 else None


def derive_non_msm_diagnoses(
    outputs: Mapping[str, Mapping[int, float]],
    years: Iterable[int],
) -> dict[int, float | None]:
    """Compute new_diagnoses_non_msm = new_diagnoses - new_diagnoses_msm per year."""
    total = outputs.get("new_diagnoses", {})
    msm = outputs.get("new_diagnoses_msm", {})
    result = {}
    for yr in years:
        t = total.get(yr)
        m = msm.get(yr)
        if t is not None and m is not None and _finite(t) and _finite(m):
            result[yr] = max(0.0, t - m)
        else:
            result[yr] = None
    return result


def _get_latest_target(targets: list[dict], output_name: str, year: int) -> dict | None:
    matches = [t for t in targets if t["model_output"] == output_name and t["year"] == year]
    if not matches:
        return None
    primary = [t for t in matches if t.get("calibration_use") == "primary"]
    return primary[0] if primary else matches[0]


def compute_summary_row(
    variant_key: str,
    outputs: dict[str, dict[int, float]],
    strat_outputs: dict[str, dict[int, float]],
    targets: list[dict],
    latest_year: int = 2024,
) -> dict:
    """Compute all summary metrics for one variant."""
    row: dict = {"variant": variant_key}

    # 2024 diagnosis ratios
    latest_total_target = _get_latest_target(targets, "new_diagnoses", latest_year)
    latest_msm_target = _get_latest_target(targets, "new_diagnoses_msm", latest_year)

    model_total_2024 = outputs.get("new_diagnoses", {}).get(latest_year)
    model_msm_2024 = outputs.get("new_diagnoses_msm", {}).get(latest_year)
    target_total_2024 = float(latest_total_target["estimate"]) if latest_total_target else None
    target_msm_2024 = float(latest_msm_target["estimate"]) if latest_msm_target else None
    target_non_msm_2024 = (
        max(0.0, target_total_2024 - target_msm_2024)
        if target_total_2024 is not None and target_msm_2024 is not None
        else None
    )
    model_non_msm_2024 = (
        max(0.0, model_total_2024 - model_msm_2024)
        if model_total_2024 is not None and model_msm_2024 is not None
        else None
    )

    row["diag_2024_total_ratio"] = _safe_ratio(model_total_2024, target_total_2024)
    row["diag_2024_msm_ratio"] = _safe_ratio(model_msm_2024, target_msm_2024)
    row["diag_2024_non_msm_ratio"] = _safe_ratio(model_non_msm_2024, target_non_msm_2024)

    # MSM historical shape
    row["msm_diag_1996_2003_max_ratio"] = _max_ratio(outputs, targets, "new_diagnoses_msm", 1996, 2003)
    row["msm_diag_1996_2014_mape"] = _median_abs_pct_error(outputs, targets, "new_diagnoses_msm", 1996, 2014)

    # Recent PLHIV and ART
    row["plhiv_2015_2024_mape"] = _median_abs_pct_error(outputs, targets, "PLHIV", 2015, 2024)
    row["art_2015_2024_mape"] = _median_abs_pct_error(outputs, targets, "on_art_stock", 2015, 2024)

    # AIDS death bounds
    row["aids_death_bounds_fraction"] = _aids_bounds_fraction(outputs, targets)

    # Incidence recent median ratio
    row["incidence_2015_2024_median_ratio"] = _median_ratio(outputs, targets, "new_infections", 2015, 2024)

    # Non-MSM stock presence
    non_msm_undiag = strat_outputs.get("non_msm_undiagnosed_hiv_pos", {})
    if non_msm_undiag:
        val_2024 = non_msm_undiag.get(latest_year)
        row["non_msm_undiagnosed_stock_2024_exists"] = (
            val_2024 is not None and _finite(val_2024) and val_2024 > 0
        )
    else:
        row["non_msm_undiagnosed_stock_2024_exists"] = None

    non_msm_diag_series = [
        max(0.0, outputs.get("new_diagnoses", {}).get(yr, 0.0)
            - outputs.get("new_diagnoses_msm", {}).get(yr, 0.0))
        for yr in range(2015, latest_year + 1)
        if outputs.get("new_diagnoses", {}).get(yr) is not None
        and outputs.get("new_diagnoses_msm", {}).get(yr) is not None
    ]
    if non_msm_diag_series:
        row["non_msm_diag_generating_stock_exists_2015_2024"] = any(v > 0 for v in non_msm_diag_series)
    else:
        row["non_msm_diag_generating_stock_exists_2015_2024"] = None

    return row


# ---------------------------------------------------------------------------
# Yearly row builder (pure)
# ---------------------------------------------------------------------------


def build_yearly_rows(
    variant_key: str,
    outputs: dict[str, dict[int, float]],
    strat_outputs: dict[str, dict[int, float]],
    start_year: int,
    end_year: int,
) -> list[dict]:
    """Build per-year output rows for one variant."""
    all_years = sorted(
        yr for yr in set(outputs.get("new_infections", {}).keys())
        if start_year <= yr <= end_year
    )
    non_msm_map = derive_non_msm_diagnoses(outputs, all_years)

    rows = []
    for yr in all_years:
        row: dict = {"variant": variant_key, "year": yr}
        for field in _STANDARD_OUTPUT_FIELDS:
            row[field] = outputs.get(field, {}).get(yr)
        row["new_diagnoses_non_msm"] = non_msm_map.get(yr)
        for field in ALL_STRATIFIED_FIELDS:
            val = strat_outputs.get(field, {}).get(yr)
            row[field] = val if val is not None else None
        rows.append(row)
    return rows


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
h3{font-size:13px;margin:14px 0 5px;color:#333}
.meta{color:#666;font-size:12px;margin-bottom:20px}
.note{background:#fff8e1;border-left:4px solid #f9a825;padding:8px 12px;
      margin:10px 0;border-radius:2px;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0}
th{background:#f0f2f5;text-align:right;padding:5px 8px;border:1px solid #d0d4d8;
   font-weight:600;color:#444;white-space:nowrap}
th:first-child{text-align:left}
td{padding:4px 8px;border:1px solid #e8eaed;text-align:right}
td:first-child{text-align:left;font-weight:500}
tr:nth-child(even) td{background:#fafbfc}
.good{color:#2e7d32;font-weight:600}
.warn{color:#e65100;font-weight:600}
.bad{color:#c62828;font-weight:600}
.na{color:#aaa}
"""


def _fmt(v, decimals: int = 3, missing: str = "—") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return f'<span class="na">{missing}</span>'
    return f"{v:.{decimals}f}"


def _ratio_cls(v) -> str:
    if v is None:
        return "na"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "na"
    if f > 2.0:
        return "bad"
    if f > 1.3 or f < 0.75:
        return "warn"
    return "good"


def _bool_cell(v) -> str:
    if v is None:
        return '<span class="na">—</span>'
    if v is True:
        return '<span class="good">YES</span>'
    return '<span class="bad">NO</span>'


def build_html_report(
    summary_df: pd.DataFrame,
    yearly_df: pd.DataFrame,
    config_path: str,
    manifest: dict,
) -> str:
    created = manifest.get("created_at", "")
    variants = manifest.get("variants", [])
    missing_outputs = manifest.get("missing_outputs", [])

    # --- Summary table ---
    summary_cols = [
        ("diag_2024_total_ratio", "2024 total diag ratio", 3),
        ("diag_2024_msm_ratio", "2024 MSM diag ratio", 3),
        ("diag_2024_non_msm_ratio", "2024 non-MSM diag ratio", 3),
        ("msm_diag_1996_2003_max_ratio", "MSM 1996-2003 max ratio", 3),
        ("msm_diag_1996_2014_mape", "MSM 1996-2014 MAPE %", 1),
        ("plhiv_2015_2024_mape", "PLHIV 2015-2024 MAPE %", 1),
        ("art_2015_2024_mape", "ART 2015-2024 MAPE %", 1),
        ("aids_death_bounds_fraction", "AIDS death bounds frac.", 3),
        ("incidence_2015_2024_median_ratio", "Incidence 2015-2024 med. ratio", 3),
    ]

    th_row = "<tr><th>Variant</th>"
    for _, label, _ in summary_cols:
        th_row += f"<th>{label}</th>"
    th_row += "<th>Non-MSM undiag stock 2024</th><th>Non-MSM diag stock 2015-2024</th></tr>"

    summary_rows_html = ""
    for _, row in summary_df.iterrows():
        summary_rows_html += f"<tr><td>{row.get('variant','')}</td>"
        for col, _, dec in summary_cols:
            v = row.get(col)
            cls = _ratio_cls(v) if "ratio" in col else ""
            cell = _fmt(v, dec) if not cls else f'<span class="{cls}">{_fmt(v, dec)}</span>'
            summary_rows_html += f"<td>{cell}</td>"
        summary_rows_html += f"<td>{_bool_cell(row.get('non_msm_undiagnosed_stock_2024_exists'))}</td>"
        summary_rows_html += f"<td>{_bool_cell(row.get('non_msm_diag_generating_stock_exists_2015_2024'))}</td>"
        summary_rows_html += "</tr>"

    # --- Variant descriptions ---
    desc_rows = "".join(
        f"<tr><td>{v}</td><td>{_VARIANT_DESCRIPTIONS.get(v, '')}</td></tr>"
        for v in variants
    )

    # --- Missing outputs ---
    if missing_outputs:
        missing_note = (
            '<div class="note"><strong>Missing stratified outputs (reported as NA):</strong> '
            + ", ".join(missing_outputs)
            + ". Engine changes required to add these columns.</div>"
        )
    else:
        missing_note = ""

    # --- Yearly table (truncated to key columns) ---
    key_cols = ["variant", "year", "PLHIV", "new_diagnoses", "new_diagnoses_msm",
                "new_diagnoses_non_msm", "new_infections", "aids_deaths", "on_art_stock"]
    yearly_key = yearly_df[[c for c in key_cols if c in yearly_df.columns]]
    yearly_html = _df_to_html_table(yearly_key, max_rows=200)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.10 Mechanism Variant Diagnostic</title>
<style>{_CSS}</style>
</head>
<body>
<h1>V13.10 Targeted Mechanism Variant Diagnostic</h1>
<div class="meta">
  <strong>Config:</strong> {config_path}<br>
  <strong>Generated:</strong> {created}
</div>

<h2>Variant Descriptions</h2>
<table>
<thead><tr><th>Variant</th><th>Description</th></tr></thead>
<tbody>{desc_rows}</tbody>
</table>

{missing_note}

<h2>Summary Metrics by Variant</h2>
<div style="overflow-x:auto;">
<table>
<thead>{th_row}</thead>
<tbody>{summary_rows_html}</tbody>
</table>
</div>

<h2>Decision Rules</h2>
<div class="note">
<strong>A.</strong> If non-MSM undiagnosed stock is absent (NO), diagnosis multipliers cannot solve composition.
Non-MSM diagnoses remain zero regardless of multiplier settings.<br>
<strong>B.</strong> If MSM multiplier improves MSM shape (lowers max ratio, lowers MAPE) but worsens AIDS deaths
(lowers bounds fraction), the mechanism is not sufficient alone.<br>
<strong>C.</strong> If variants B–D materially improve MSM fit but non-MSM diagnosis ratio remains ~0, keep
multiplier as sensitivity only and address non-MSM stock as the primary repair.<br>
<strong>D.</strong> Only run more ABC-SMC after these diagnostics identify the bottleneck.
</div>

<h2>Yearly Outputs (key columns)</h2>
<div style="overflow-x:auto;">
{yearly_html}
</div>

</body>
</html>
"""


def _df_to_html_table(df: pd.DataFrame, max_rows: int = 200) -> str:
    if df.empty:
        return "<p>No data.</p>"
    df_show = df.head(max_rows)
    headers = "".join(f"<th>{c}</th>" for c in df_show.columns)
    rows_html = ""
    for _, row in df_show.iterrows():
        cells = ""
        for v in row:
            if v is None or (isinstance(v, float) and not math.isfinite(v)):
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


def run_diagnostic(args: argparse.Namespace) -> dict:
    config_path = Path(args.config).resolve()
    targets_csv = Path(args.normalized_targets_csv).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    start_year = int(getattr(args, "start_year", 1990))
    end_year = int(getattr(args, "end_year", 2025))
    seed = int(getattr(args, "seed", 42))
    variant_set = getattr(args, "variant_set", "v13_10_mechanism_decomp")

    print(f"[V13.10-variants] Config: {config_path.name}")
    print(f"[V13.10-variants] Targets: {targets_csv.name}")
    print(f"[V13.10-variants] Window: {start_year}–{end_year}  seed={seed}")
    print(f"[V13.10-variants] Variant set: {variant_set}")

    with open(config_path, encoding="utf-8") as f:
        base_config = json.load(f)

    targets = load_targets(targets_csv, start_year=start_year, end_year=end_year)
    base_params = extract_config_params(base_config)

    all_yearly_rows: list[dict] = []
    all_summary_rows: list[dict] = []
    missing_outputs: list[str] = []

    for variant_key in _VARIANT_KEYS:
        print(f"[V13.10-variants] Running variant: {variant_key} ...", end="", flush=True)
        cfg = patch_config_for_variant(base_config, variant_key)
        result = _run_config_quietly(cfg, seed=seed, run_end=end_year)
        outputs = extract_annual_outputs(result, start_year=1980, end_year=end_year)
        strat_outputs, missing = extract_stratified_outputs(result, start_year=start_year, end_year=end_year)
        if variant_key == "baseline":
            missing_outputs = missing

        yearly_rows = build_yearly_rows(variant_key, outputs, strat_outputs, start_year, end_year)
        all_yearly_rows.extend(yearly_rows)

        summary_row = compute_summary_row(variant_key, outputs, strat_outputs, targets, latest_year=min(end_year, 2024))
        all_summary_rows.append(summary_row)
        print(" done.")

    yearly_df = pd.DataFrame(all_yearly_rows)
    summary_df = pd.DataFrame(all_summary_rows)

    manifest = {
        "tool": "diagnose_calibration_mechanism_variants",
        "version": "v13.10",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(config_path),
        "targets_csv": str(targets_csv),
        "start_year": start_year,
        "end_year": end_year,
        "seed": seed,
        "variant_set": variant_set,
        "variants": _VARIANT_KEYS,
        "variant_descriptions": _VARIANT_DESCRIPTIONS,
        "base_config_params": {k: v for k, v in base_params.items() if v is not None},
        "missing_outputs": missing_outputs,
        "note_missing": (
            "Fields listed in missing_outputs are absent from the engine summary DataFrame "
            "and are reported as NA. Engine changes are required to add stratified stock columns."
        ),
    }

    yearly_csv = output_dir / "mechanism_variant_yearly.csv"
    yearly_df.to_csv(yearly_csv, index=False)

    summary_csv = output_dir / "mechanism_variant_summary.csv"
    summary_df.to_csv(summary_csv, index=False)

    html_path = output_dir / "mechanism_variant_report.html"
    html_path.write_text(
        build_html_report(summary_df, yearly_df, str(config_path), manifest),
        encoding="utf-8",
    )

    manifest_path = output_dir / "mechanism_variant_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print(f"[V13.10-variants] Yearly CSV:   {yearly_csv}")
    print(f"[V13.10-variants] Summary CSV:  {summary_csv}")
    print(f"[V13.10-variants] HTML report:  {html_path}")
    print(f"[V13.10-variants] Manifest:     {manifest_path}")

    _print_headline(summary_df)

    return {
        "yearly_csv": str(yearly_csv),
        "summary_csv": str(summary_csv),
        "html_report": str(html_path),
        "manifest": str(manifest_path),
        "summary_df": summary_df,
    }


def _print_headline(summary_df: pd.DataFrame) -> None:
    print("\n[V13.10-variants] Headline summary:")
    cols = [
        "variant",
        "diag_2024_total_ratio",
        "diag_2024_msm_ratio",
        "diag_2024_non_msm_ratio",
        "msm_diag_1996_2003_max_ratio",
        "msm_diag_1996_2014_mape",
        "plhiv_2015_2024_mape",
        "aids_death_bounds_fraction",
    ]
    for _, row in summary_df.iterrows():
        print(f"  {row.get('variant','?'):35s}", end="")
        for c in cols[1:]:
            v = row.get(c)
            print(f"  {_fmts(v):>7}", end="")
        print()


def _fmts(v) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:.3f}"


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _finite(v) -> bool:
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V13.10 targeted mechanism variant diagnostic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", required=True, help="Calibrated candidate config JSON.")
    p.add_argument(
        "--normalized-targets-csv", required=True,
        help="Normalized targets CSV from run_lhs_calibration.",
    )
    p.add_argument("--output-dir", required=True, help="Directory for outputs.")
    p.add_argument("--seed", type=int, default=42, help="RNG seed.")
    p.add_argument("--start-year", type=int, default=1990, help="First year of diagnostic window.")
    p.add_argument("--end-year", type=int, default=2025, help="Last year of diagnostic window.")
    p.add_argument(
        "--variant-set", default="v13_10_mechanism_decomp",
        help="Variant set name recorded in manifest.",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    run_diagnostic(parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
