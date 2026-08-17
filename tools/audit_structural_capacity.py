#!/usr/bin/env python3
"""
V13.12 Structural capacity / history-matching audit.

Reads a pre-computed particle CSV and answers:

    Can the current model architecture (shared seed staging + V13.10 MSM
    diagnosis multiplier) hit the calibration targets at all, even if we allow
    parameter values outside defensible ranges?

Diagnostic only — no model simulation is run.  Does not modify production
configs, calibration spaces, gates, or acceptance logic.

Classification
--------------
Each particle row is classified into one of four mutually-exclusive labels:

  capacity_pass
      All 8 capacity criteria satisfied AND implausibility_score <= threshold.

  mathematical_only_fit
      All 8 capacity criteria satisfied BUT implausibility_score > threshold.
      Interpretation: the model CAN hit these targets but only at parameter
      values that are not academically defensible.

  near_capacity_pass
      Fails at most 2 of the 8 criteria AND none of the failures are extreme.
      Extreme = total_diag_ratio < 0.70, AIDS bounds fraction < 0.20, or
      MSM early max ratio > 4.0.

  not_capacity_fit
      Everything else.

Capacity criteria (8)
---------------------
  1.  total diagnosis 2024 ratio ∈ [0.90, 1.50]
  2.  MSM diagnosis 2024 ratio   ∈ [0.90, 1.50]
  3.  PLHIV recent MAPE          ≤  10 %
  4.  ART recent MAPE            ≤  10 %
  5.  incidence recent median ratio ∈ [0.50, 1.75]
  6.  AIDS death bounds fraction ≥  0.40
  7.  MSM hist MAPE              ≤  40 %
  8.  MSM early max ratio        ≤  2.50

Implausibility score
--------------------
For each particle, each parameter is scored as the normalised distance
outside the defensible V13.10 production bound (0 if inside the bound).
Score is the sum across all parameters.

Outputs
-------
  capacity_summary.json
  capacity_candidates.csv      capacity_pass + near_capacity_pass + mathematical_only_fit
  capacity_near_misses.csv     near_capacity_pass only
  capacity_implausibility.csv  per-parameter implausibility summary over top-N
  capacity_report.html

Usage
-----
python3 tools/audit_structural_capacity.py \\
    --particles outputs/calibration/.../abc_smc_particles_*.csv \\
    --output-dir outputs/diagnostics/structural_capacity_v13_12 \\
    --top-n 100
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Defensible V13.10 bounds used for implausibility scoring
# ---------------------------------------------------------------------------

#: Pairs of (defensible_min, defensible_max) from the V13.10 production space.
#: Any particle parameter value outside these bounds incurs a proportional
#: implausibility penalty.
_V13_10_DEFENSIBLE_BOUNDS: dict[str, tuple[float, float]] = {
    "p_msm":                   (0.053, 0.090),
    "seed_prevalence":         (0.001, 0.0035),
    "seed_msm_fraction":       (0.80,  1.00),
    "seed_chronic_share":      (0.00,  0.40),
    "seed_aids_share":         (0.00,  0.05),
    "orient_female":           (0.20,  1.00),
    "orient_msm":              (1.00,  5.00),
    "epsilon_hetero":          (0.00,  1.00),
    "epsilon_msm":             (0.00,  1.00),
    "activity_med":            (0.20,  0.45),
    "activity_high":           (0.03,  0.15),
    "contact_low":             (1.00,  6.00),
    "contact_med":             (6.00,  18.00),
    "contact_high":            (18.00, 60.00),
    "diagnosis_1985":          (0.03,  0.18),
    "diagnosis_1990":          (0.08,  0.30),
    "diagnosis_1997":          (0.18,  0.50),
    "diagnosis_2015":          (0.25,  0.70),
    "msm_diag_mult_1996":      (0.20,  0.70),
    "msm_diag_mult_2004":      (0.45,  1.00),
    "msm_diag_mult_2015":      (0.45,  1.00),
    "art_init_1996":           (0.05,  0.50),
    "art_init_2000":           (0.30,  0.95),
    "art_init_2015":           (0.60,  0.98),
    "art_init_2020":           (0.75,  0.995),
    "art_dropout_rate":        (0.00,  0.03),
    "chronic_to_aids":         (0.045, 0.085),
    "chronic_to_aids_on_art":  (0.001, 0.015),
    "aids_mortality":          (0.15,  0.35),
    "art_mortality_reduction": (0.75,  0.97),
}

#: Implausibility score above which a capacity-passing particle is reclassified
#: as mathematical_only_fit.  Value of 2.0 ≈ two parameters at the outer edge
#: of the stress-test bounds.
_IMPLAUSIBILITY_THRESHOLD: float = 2.0

# ---------------------------------------------------------------------------
# Capacity criteria column names → (criterion description, check function)
# ---------------------------------------------------------------------------

#: Each capacity criterion has a column name in the particle CSV plus a
#: one-argument predicate that returns True when the criterion is SATISFIED.
_CAPACITY_CRITERIA: list[tuple[str, object, str]] = [
    (
        "acceptance_diagnosis_gate_new_diagnoses_ratio",
        lambda v: 0.90 <= v <= 1.50,
        "total_diag_ratio in [0.90, 1.50]",
    ),
    (
        "acceptance_diagnosis_gate_new_diagnoses_msm_ratio",
        lambda v: 0.90 <= v <= 1.50,
        "msm_diag_ratio in [0.90, 1.50]",
    ),
    (
        "acceptance_plhiv_gate_recent_median_abs_pct",
        lambda v: v <= 10.0,
        "plhiv_recent_mape <= 10%",
    ),
    (
        "acceptance_art_stock_gate_recent_median_abs_pct",
        lambda v: v <= 10.0,
        "art_recent_mape <= 10%",
    ),
    (
        "acceptance_incidence_gate_recent_median_model_target_ratio",
        lambda v: 0.50 <= v <= 1.75,
        "incidence_ratio in [0.50, 1.75]",
    ),
    (
        "acceptance_aids_death_gate_bounds_fraction",
        lambda v: v >= 0.40,
        "aids_bounds_fraction >= 0.40",
    ),
    (
        "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape",
        lambda v: v <= 40.0,
        "msm_hist_mape <= 40%",
    ),
    (
        "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio",
        lambda v: v <= 2.50,
        "msm_early_max_ratio <= 2.50",
    ),
]

#: Columns that, if failing, constitute an *extreme* failure.
#: An extreme failure disqualifies a row from near_capacity_pass even if
#: only one or two criteria fail in total.
_EXTREME_FAILURE_CHECKS: list[tuple[str, object]] = [
    ("acceptance_diagnosis_gate_new_diagnoses_ratio",       lambda v: v < 0.70),
    ("acceptance_aids_death_gate_bounds_fraction",          lambda v: v < 0.20),
    ("hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio", lambda v: v > 4.00),
]

_CLASSIFICATION_LABELS = (
    "capacity_pass",
    "mathematical_only_fit",
    "near_capacity_pass",
    "not_capacity_fit",
)


# ---------------------------------------------------------------------------
# Implausibility scoring (pure — no I/O)
# ---------------------------------------------------------------------------


def _fval(row: pd.Series, col: str) -> float | None:
    """Return float value from row or None if absent/NaN."""
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def is_evaluable_row(row: pd.Series) -> bool:
    """Return True only for rows with completed model outputs.

    Error/collapsed rows can still contain sampled parameters, but they do not
    contain valid gate metrics. They must not be classified as capacity evidence.
    """
    err = row.get("error")
    if err is not None and not pd.isna(err) and str(err).strip():
        return False

    collapsed = row.get("collapsed")
    if collapsed is not None and not pd.isna(collapsed):
        if isinstance(collapsed, str):
            if collapsed.strip().lower() in {"true", "1", "yes"}:
                return False
        elif bool(collapsed):
            return False

    distance = _fval(row, "abc_distance")
    if distance is not None and not math.isfinite(distance):
        return False

    return True


def param_implausibility(
    value: float,
    defensible_min: float,
    defensible_max: float,
) -> float:
    """Normalised distance outside the defensible bound; 0 if inside.

    If value is below defensible_min: (min - value) / range.
    If value is above defensible_max: (value - max) / range.
    Otherwise: 0.
    The range is (defensible_max - defensible_min); returns 0 if range == 0.
    """
    drange = defensible_max - defensible_min
    if drange <= 0.0:
        return 0.0
    if value < defensible_min:
        return (defensible_min - value) / drange
    if value > defensible_max:
        return (value - defensible_max) / drange
    return 0.0


def compute_implausibility_score(row: pd.Series) -> float:
    """Sum of per-parameter implausibility contributions across V13.10 bounds."""
    total = 0.0
    for param, (dmin, dmax) in _V13_10_DEFENSIBLE_BOUNDS.items():
        v = _fval(row, param)
        if v is not None:
            total += param_implausibility(v, dmin, dmax)
    return total


def compute_per_param_implausibility(row: pd.Series) -> dict[str, float]:
    """Return {param: contribution} for all parameters with defensible bounds."""
    out: dict[str, float] = {}
    for param, (dmin, dmax) in _V13_10_DEFENSIBLE_BOUNDS.items():
        v = _fval(row, param)
        if v is not None:
            out[param] = param_implausibility(v, dmin, dmax)
        else:
            out[param] = 0.0
    return out


# ---------------------------------------------------------------------------
# Capacity criteria evaluation (pure — no I/O)
# ---------------------------------------------------------------------------


def evaluate_capacity_criteria(row: pd.Series) -> dict:
    """Return per-criterion pass/fail plus aggregate counts.

    Returns a dict with keys:
      - criteria_results: {criterion_description: True/False/None}
      - n_pass, n_fail, n_missing: ints
      - has_extreme_failure: bool
    """
    results: dict[str, bool | None] = {}
    n_pass = n_fail = n_missing = 0

    for col, check, desc in _CAPACITY_CRITERIA:
        v = _fval(row, col)
        if v is None:
            results[desc] = None
            n_missing += 1
        elif check(v):
            results[desc] = True
            n_pass += 1
        else:
            results[desc] = False
            n_fail += 1

    has_extreme = False
    for col, extreme_check in _EXTREME_FAILURE_CHECKS:
        v = _fval(row, col)
        if v is not None and extreme_check(v):
            has_extreme = True
            break

    return {
        "criteria_results": results,
        "n_pass":           n_pass,
        "n_fail":           n_fail,
        "n_missing":        n_missing,
        "has_extreme_failure": has_extreme,
    }


def classify_capacity(row: pd.Series) -> str:
    """Return one of the four capacity classification labels for a particle row."""
    if not is_evaluable_row(row):
        return "not_capacity_fit"

    ev = evaluate_capacity_criteria(row)
    n_fail   = ev["n_fail"]
    n_missing = ev["n_missing"]
    extreme  = ev["has_extreme_failure"]
    implaus  = compute_implausibility_score(row)

    if n_missing > 0:
        return "not_capacity_fit"

    if n_fail == 0:
        # All capacity criteria satisfied
        if implaus <= _IMPLAUSIBILITY_THRESHOLD:
            return "capacity_pass"
        else:
            return "mathematical_only_fit"

    if n_fail <= 2 and not extreme:
        return "near_capacity_pass"

    return "not_capacity_fit"


def classify_all(df: pd.DataFrame) -> pd.Series:
    """Vectorised classification across a DataFrame. Returns a Series of labels."""
    return pd.Series(
        [classify_capacity(row) for _, row in df.iterrows()],
        index=df.index,
        name="capacity_classification",
    )


# ---------------------------------------------------------------------------
# Per-parameter implausibility summary
# ---------------------------------------------------------------------------


def build_implausibility_summary(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """Return per-parameter implausibility statistics across the top-N particles.

    Rows are sorted by implausibility_score (ascending) before selecting top-N.
    Returned DataFrame has one row per parameter with:
      - param: parameter name
      - n_outside_bounds: count of top-N particles with any implausibility > 0
      - mean_implausibility: mean contribution across top-N particles
      - max_implausibility: max contribution
    """
    if df.empty:
        return pd.DataFrame(columns=["param", "n_outside_bounds",
                                     "mean_implausibility", "max_implausibility"])

    df = df[[is_evaluable_row(row) for _, row in df.iterrows()]].copy()
    if df.empty:
        return pd.DataFrame(columns=["param", "n_outside_bounds",
                                     "mean_implausibility", "max_implausibility"])

    if "implausibility_score" in df.columns:
        sub = df[df["implausibility_score"].notna()].nsmallest(
            min(top_n, len(df)), "implausibility_score"
        )
    else:
        sub = df.head(top_n)

    records = []
    for param, (dmin, dmax) in _V13_10_DEFENSIBLE_BOUNDS.items():
        contribs = []
        for _, row in sub.iterrows():
            v = _fval(row, param)
            if v is not None:
                contribs.append(param_implausibility(v, dmin, dmax))
        if not contribs:
            continue
        n_outside = sum(1 for c in contribs if c > 0.0)
        records.append({
            "param":              param,
            "n_outside_bounds":   n_outside,
            "mean_implausibility":  sum(contribs) / len(contribs),
            "max_implausibility":   max(contribs),
        })

    result = pd.DataFrame(records)
    if not result.empty:
        result = result.sort_values("mean_implausibility", ascending=False)
    return result


# ---------------------------------------------------------------------------
# Capacity summary dict
# ---------------------------------------------------------------------------


def build_capacity_summary(
    df: pd.DataFrame,
    implausibility_df: pd.DataFrame,
    top_n: int,
) -> dict:
    """Assemble the capacity_summary.json content."""
    counts = df["capacity_classification"].value_counts().to_dict() if "capacity_classification" in df.columns else {}

    def _best_row(classification: str) -> dict | None:
        sub = df[df.get("capacity_classification", pd.Series()) == classification] if "capacity_classification" in df.columns else pd.DataFrame()
        if sub.empty:
            return None
        # Sort by implausibility_score (best = lowest), then abc_distance
        sort_col = "implausibility_score" if "implausibility_score" in sub.columns else None
        if sort_col:
            sub = sub[sub[sort_col].notna()].sort_values(sort_col)
        if sub.empty:
            return None
        row = sub.iloc[0]
        out: dict = {}
        for col in ("particle_id", "generation", "implausibility_score",
                    "abc_distance", "n_capacity_criteria_fail",
                    "acceptance_diagnosis_gate_new_diagnoses_ratio",
                    "acceptance_aids_death_gate_bounds_fraction",
                    "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape",
                    "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio"):
            v = row.get(col)
            if v is not None:
                try:
                    out[col] = float(v) if not isinstance(v, str) else v
                except (TypeError, ValueError):
                    out[col] = str(v)
        return out

    # Median implausibility among passes and near-passes
    pass_cats = {"capacity_pass", "mathematical_only_fit", "near_capacity_pass"}
    interesting = df[df.get("capacity_classification", pd.Series(dtype=str)).isin(pass_cats)] \
        if "capacity_classification" in df.columns else pd.DataFrame()
    med_implaus = None
    if "implausibility_score" in interesting.columns and not interesting.empty:
        valid_i = interesting["implausibility_score"].dropna()
        if not valid_i.empty:
            med_implaus = float(valid_i.median())

    # Most implausible parameters (top 5 by mean implausibility)
    top_implaus_params = []
    if not implausibility_df.empty and "param" in implausibility_df.columns:
        top_implaus_params = implausibility_df.head(5)["param"].tolist()

    return {
        "created_at":         datetime.utcnow().isoformat(timespec="seconds"),
        "n_total":            len(df),
        "n_evaluable":        int(sum(is_evaluable_row(row) for _, row in df.iterrows())),
        "n_capacity_pass":    counts.get("capacity_pass",           0),
        "n_near_capacity_pass": counts.get("near_capacity_pass",   0),
        "n_mathematical_only": counts.get("mathematical_only_fit", 0),
        "n_not_capacity_fit": counts.get("not_capacity_fit",        0),
        "top_n_used":         top_n,
        "implausibility_threshold": _IMPLAUSIBILITY_THRESHOLD,
        "best_capacity_candidate":  _best_row("capacity_pass"),
        "best_mathematical_only":   _best_row("mathematical_only_fit"),
        "best_near_miss":           _best_row("near_capacity_pass"),
        "median_implausibility_passes_and_nearmisses": med_implaus,
        "most_implausible_params_top5": top_implaus_params,
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_CSS = """
<style>
body { font-family: Arial, sans-serif; margin: 30px; font-size: 13px; }
h1 { color: #2c3e50; }
h2 { color: #34495e; border-bottom: 1px solid #bdc3c7; padding-bottom: 4px; margin-top: 28px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
th { background: #2c3e50; color: white; padding: 6px 10px; text-align: left; font-size: 12px; }
td { padding: 5px 10px; border-bottom: 1px solid #ecf0f1; }
tr:nth-child(even) td { background: #f9f9f9; }
tr:hover td { background: #eaf4ff; }
.pass  { background: #d5f5e3 !important; color: #1e8449; font-weight: bold; }
.near  { background: #fef9e7 !important; color: #784212; font-weight: bold; }
.math  { background: #eaf2ff !important; color: #154360; font-weight: bold; }
.fail  { background: #fadbd8 !important; color: #922b21; }
.warn  { background: #fdebd0 !important; }
.note  { font-size: 11px; color: #7f8c8d; }
.panel { background: #eaf4ff; border-left: 4px solid #2980b9;
         padding: 12px 16px; margin-bottom: 16px; }
pre  { font-size: 11px; background: #f4f4f4; padding: 10px; }
</style>
"""


def _df_to_html_table(df: pd.DataFrame, max_rows: int = 200,
                      classification_col: str | None = "capacity_classification") -> str:
    if df.empty:
        return "<p class='note'>No data.</p>"
    display = df.head(max_rows).copy()
    for col in display.select_dtypes(include="float").columns:
        display[col] = display[col].apply(
            lambda v: f"{v:.4f}" if v is not None and not (isinstance(v, float) and math.isnan(v)) else ""
        )
    rows_html = ""
    for _, row in display.iterrows():
        css = ""
        if classification_col and classification_col in row:
            label = str(row[classification_col])
            css = {"capacity_pass": "pass", "near_capacity_pass": "near",
                   "mathematical_only_fit": "math", "not_capacity_fit": "fail"}.get(label, "")
        row_html = "".join(f"<td>{v}</td>" for v in row)
        rows_html += f"<tr class='{css}'>{row_html}</tr>\n"
    header = "".join(f"<th>{c}</th>" for c in display.columns)
    return f"<table><tr>{header}</tr>\n{rows_html}</table>"


def _interpret(summary: dict) -> list[str]:
    lines: list[str] = []
    n_cap = summary.get("n_capacity_pass", 0)
    n_near = summary.get("n_near_capacity_pass", 0)
    n_math = summary.get("n_mathematical_only", 0)
    n_total = summary.get("n_total", 1)
    implaus_med = summary.get("median_implausibility_passes_and_nearmisses")
    top5 = summary.get("most_implausible_params_top5", [])

    if n_cap == 0 and n_math == 0:
        lines.append(
            "No particle passes all 8 capacity criteria with any parameter combination, "
            "including the stress-test wide bounds. This is strong evidence of structural "
            "incapacity: the model architecture cannot jointly match all key targets even "
            "with implausibly relaxed parameter values."
        )
    elif n_cap > 0 and (implaus_med is None or implaus_med <= _IMPLAUSIBILITY_THRESHOLD):
        lines.append(
            f"{n_cap} particle(s) pass all capacity criteria within or near defensible bounds "
            f"(implausibility ≤ {_IMPLAUSIBILITY_THRESHOLD:.1f}). "
            "The model architecture has structural capacity. Proceed to defensible ABC-SMC run."
        )
    elif n_math > 0 and n_cap == 0:
        lines.append(
            f"No capacity pass within defensible bounds, but {n_math} particle(s) pass all "
            "criteria at highly implausible parameter values. The model CAN produce the right "
            "outputs mathematically, but requires parameter combinations outside prior ranges. "
            "This is evidence of structural tension — a mechanism change or prior relaxation "
            "may be needed."
        )

    if n_near > 0:
        pct = 100.0 * n_near / max(n_total, 1)
        lines.append(
            f"{n_near} near-capacity passes ({pct:.1f}% of total): particles that fail ≤ 2 "
            "criteria without extreme failures. Review which criteria fail most often in this group."
        )

    if top5:
        lines.append(
            "Parameters most often outside defensible V13.10 bounds among top-N particles: "
            + ", ".join(top5) + ". "
            "High implausibility on these parameters suggests the model fitness landscape "
            "requires extreme values in these dimensions."
        )

    if not lines:
        lines.append("No capacity classification findings to report.")

    return lines


def build_html_report(
    summary: dict,
    candidates_df: pd.DataFrame,
    near_misses_df: pd.DataFrame,
    implausibility_df: pd.DataFrame,
    manifest: dict,
) -> str:
    interp_lines = _interpret(summary)
    interp_html = "\n".join(f"<div class='panel'>{line}</div>" for line in interp_lines)

    counts_table = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in [
            ("capacity_pass",          summary.get("n_capacity_pass", 0)),
            ("mathematical_only_fit",  summary.get("n_mathematical_only", 0)),
            ("near_capacity_pass",     summary.get("n_near_capacity_pass", 0)),
            ("not_capacity_fit",       summary.get("n_not_capacity_fit", 0)),
            ("TOTAL",                  summary.get("n_total", 0)),
        ]
    )
    counts_html = f"<table><tr><th>Classification</th><th>Count</th></tr>{counts_table}</table>"

    best_cap  = summary.get("best_capacity_candidate")  or {}
    best_near = summary.get("best_near_miss")           or {}
    best_math = summary.get("best_mathematical_only")   or {}

    def _dict_table(d: dict) -> str:
        if not d:
            return "<p class='note'>None.</p>"
        rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in d.items())
        return f"<table>{rows}</table>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.12 Structural Capacity Audit</title>
{_CSS}
</head>
<body>
<h1>V13.12 Structural Capacity / History-Matching Audit</h1>
<p class='note'>Generated: {manifest.get("created_at", "")}
&nbsp;|&nbsp; particles: {manifest.get("particles_path", "")}
&nbsp;|&nbsp; top_n={manifest.get("top_n", "")}</p>
<p class='warn'>
<b>Diagnostic only.</b> This is a stress-test / history-matching implausibility screen, not a production calibration.
Capacity passes at high implausibility scores are <em>mathematical only</em> and do not imply the model is correctly
specified.
</p>

<h2>Interpretation</h2>
{interp_html}

<h2>Classification Counts</h2>
{counts_html}

<h2>Best Capacity Candidate</h2>
{_dict_table(best_cap)}

<h2>Best Mathematical-Only Fit</h2>
{_dict_table(best_math)}

<h2>Best Near-Capacity Pass (Near Miss)</h2>
{_dict_table(best_near)}

<h2>Capacity / Near-Miss Candidates</h2>
<p class='note'>
Colour: <span class='pass'>capacity_pass</span> &nbsp;
        <span class='math'>mathematical_only_fit</span> &nbsp;
        <span class='near'>near_capacity_pass</span>
</p>
{_df_to_html_table(candidates_df, max_rows=200)}

<h2>Per-Parameter Implausibility (top-{manifest.get("top_n", "")} particles by implausibility_score)</h2>
{_df_to_html_table(implausibility_df, max_rows=50, classification_col=None)}

<h2>Manifest</h2>
<pre>{json.dumps(manifest, indent=2)}</pre>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V13.12 structural capacity audit over a particle CSV."
    )
    parser.add_argument(
        "--particles",
        required=True,
        help="Path to the particle CSV (produced by run_abc_smc_calibration.py).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for output files.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=100,
        help="Top-N particles (by implausibility_score) for summary statistics.",
    )
    parser.add_argument(
        "--frontier-audit-csv",
        default=None,
        help=(
            "Optional: path to frontier_all_candidates.csv from "
            "audit_calibration_frontier.py. If supplied, frontier_loss is "
            "used as a secondary sort key in the report."
        ),
    )
    return parser.parse_args(argv)


def run(
    particles_path: str,
    output_dir: str,
    top_n: int = 100,
    frontier_audit_csv: str | None = None,
) -> dict:
    """Core pipeline. Returns the manifest dict."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # --- Load particles ---
    df = pd.read_csv(particles_path, low_memory=False)

    # --- Optional: merge frontier_loss from frontier audit CSV ---
    if frontier_audit_csv is not None:
        try:
            fa = pd.read_csv(frontier_audit_csv, low_memory=False)
            merge_cols = ["particle_id", "frontier_loss"]
            if all(c in fa.columns for c in merge_cols) and "particle_id" in df.columns:
                fa_sub = fa[merge_cols].drop_duplicates("particle_id")
                df = df.merge(fa_sub, on="particle_id", how="left", suffixes=("", "_frontier"))
        except Exception:
            pass  # Frontier merge is optional; don't fail the audit

    # --- Classify and score ---
    df["capacity_classification"]  = classify_all(df)
    df["implausibility_score"]     = [compute_implausibility_score(row) for _, row in df.iterrows()]
    df["n_capacity_criteria_fail"] = [
        evaluate_capacity_criteria(row)["n_fail"] + evaluate_capacity_criteria(row)["n_missing"]
        for _, row in df.iterrows()
    ]

    # --- Candidate splits ---
    interesting_mask = df["capacity_classification"].isin(
        {"capacity_pass", "near_capacity_pass", "mathematical_only_fit"}
    )
    candidates_df  = df[interesting_mask].copy()
    near_misses_df = df[df["capacity_classification"] == "near_capacity_pass"].copy()

    # Sort candidates by implausibility_score ascending, then classification
    _cat_order = {"capacity_pass": 0, "mathematical_only_fit": 1,
                  "near_capacity_pass": 2, "not_capacity_fit": 3}
    if not candidates_df.empty and "implausibility_score" in candidates_df.columns:
        candidates_df = candidates_df.sort_values("implausibility_score")

    # --- Implausibility summary ---
    implausibility_df = build_implausibility_summary(df, top_n)

    # --- Summary dict ---
    summary = build_capacity_summary(df, implausibility_df, top_n)

    # --- Write outputs ---
    (out / "capacity_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    candidates_df.to_csv(out / "capacity_candidates.csv", index=False)
    near_misses_df.to_csv(out / "capacity_near_misses.csv", index=False)
    implausibility_df.to_csv(out / "capacity_implausibility.csv", index=False)

    manifest = {
        "created_at":     datetime.utcnow().isoformat(timespec="seconds"),
        "particles_path": str(particles_path),
        "frontier_audit_csv": str(frontier_audit_csv) if frontier_audit_csv else None,
        "top_n":          top_n,
        "n_total":        len(df),
        "n_evaluable":    int(sum(is_evaluable_row(row) for _, row in df.iterrows())),
        "n_candidates":   len(candidates_df),
        "n_near_misses":  len(near_misses_df),
        "implausibility_threshold": _IMPLAUSIBILITY_THRESHOLD,
        "capacity_criteria_count": len(_CAPACITY_CRITERIA),
    }

    html = build_html_report(summary, candidates_df, near_misses_df,
                             implausibility_df, manifest)
    (out / "capacity_report.html").write_text(html, encoding="utf-8")

    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = run(
        particles_path=args.particles,
        output_dir=args.output_dir,
        top_n=args.top_n,
        frontier_audit_csv=args.frontier_audit_csv,
    )
    print(f"Structural capacity audit complete. "
          f"n_total={manifest['n_total']}, "
          f"n_candidates={manifest['n_candidates']}, "
          f"n_near_misses={manifest['n_near_misses']}")
    print(f"Outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
