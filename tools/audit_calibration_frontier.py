#!/usr/bin/env python3
"""
Pareto / frontier audit across existing calibration particle CSVs.

Reads one or more pre-computed particle CSV files (produced by
run_abc_smc_calibration.py), derives normalised lower-is-better losses for
each objective, identifies Pareto-non-dominated candidates, summarises
performance by run label, and produces a correlation / tradeoff matrix.

No model simulation is run. Diagnostic-only — does not modify calibration
scores, gates, or acceptance logic.

Outputs
-------
frontier_all_candidates.csv
    Every row from every input CSV, with derived loss columns appended.
frontier_pareto_candidates.csv
    Non-dominated subset (Pareto front across the 6 objectives listed below).
frontier_summary_by_run.csv
    One row per run label: counts, best abc_distance, best losses, gate
    pass-rates, median seed-stage parameters among top-N.
frontier_tradeoff_matrix.csv
    Spearman rank-correlation matrix among key loss / seed metrics.
frontier_report.html
    Self-contained HTML report (tables + auto-generated interpretation).
frontier_manifest.json
    Metadata: timestamp, run labels, row counts, top_n, missing columns.

Usage
-----
python3 tools/audit_calibration_frontier.py \\
    --particles v13_9_5=outputs/calibration/abc_smc_v13_9_5_*/abc_smc_particles_*.csv \\
    --particles v13_10=outputs/calibration/abc_smc_v13_10_*/abc_smc_particles_*.csv \\
    --output-dir outputs/diagnostics/frontier_audit \\
    --top-n 50
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
# Column names we try to pull from the particle CSV
# ---------------------------------------------------------------------------

_ENDPOINT_GATE_COLS: list[str] = [
    "overall_accept",
    "acceptance_n_failures",
    "diagnosis_gate_pass",
    "plhiv_gate_pass",
    "incidence_gate_pass",
    "aids_death_gate_pass",
    "art_stock_gate_pass",
]

_HIST_GATE_COLS: list[str] = [
    "hist_overall_accept",
    "hist_n_failures",
    "plhiv_hist_gate_pass",
    "diagnoses_hist_gate_pass",
    "msm_diag_hist_gate_pass",
    "aids_cases_hist_gate_pass",
]

_SCALAR_METRIC_COLS: list[str] = [
    "abc_distance",
    "total_ll",
    "acceptance_diagnosis_gate_new_diagnoses_ratio",
    "acceptance_diagnosis_gate_new_diagnoses_msm_ratio",
    "acceptance_plhiv_gate_recent_median_abs_pct",
    "acceptance_incidence_gate_recent_median_model_target_ratio",
    "acceptance_aids_death_gate_bounds_fraction",
    "acceptance_aids_death_gate_peak_to_recent_mean_ratio",
    "acceptance_art_stock_gate_recent_median_abs_pct",
    "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape",
    "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio",
    "hist_metric_diagnoses_hist_gate_diagnoses_hist_mape",
    "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio",
    "hist_metric_aids_cases_hist_gate_aids_cases_hist_mape",
]

_SEED_PARAM_COLS: list[str] = [
    "seed_prevalence",
    "seed_msm_fraction",
    "seed_msm_chronic_share",
    "seed_msm_aids_share",
    "seed_non_msm_chronic_share",
    "seed_non_msm_aids_share",
]

_V13_10_MULT_COLS: list[str] = [
    "msm_diag_mult_1996",
    "msm_diag_mult_2004",
    "msm_diag_mult_2015",
]

# Columns kept in the consolidated output CSV (metadata + all above)
_META_COLS: list[str] = [
    "run_label",
    "particle_id",
    "generation",
    "collapsed",
]

_ALL_INPUT_COLS: list[str] = (
    _META_COLS
    + _ENDPOINT_GATE_COLS
    + _HIST_GATE_COLS
    + _SCALAR_METRIC_COLS
    + _SEED_PARAM_COLS
    + _V13_10_MULT_COLS
)

# ---------------------------------------------------------------------------
# Pareto objectives (lower is better — we minimise these losses)
# ---------------------------------------------------------------------------

_PARETO_OBJECTIVES: list[str] = [
    "endpoint_loss",
    "historical_loss",
    "aids_loss",
    "loss_total_diag_2024",
    "loss_msm_hist_mape",
    "loss_aids_deaths",
]

# Metrics included in the tradeoff (Spearman) matrix
_TRADEOFF_COLS: list[str] = [
    "endpoint_loss",
    "historical_loss",
    "aids_loss",
    "loss_total_diag_2024",
    "loss_msm_hist_mape",
    "loss_msm_early_ratio",
    "loss_aids_deaths",
    "loss_incidence_recent",
    "seed_prevalence",
    "seed_msm_fraction",
    "seed_msm_chronic_share",
    "seed_msm_aids_share",
    "seed_non_msm_chronic_share",
    "seed_non_msm_aids_share",
]


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def parse_label_path(token: str) -> tuple[str, str]:
    """Parse ``LABEL=PATH`` token.  If no ``=`` present, use path stem as label."""
    if "=" in token:
        label, path = token.split("=", 1)
        return label.strip(), path.strip()
    p = Path(token)
    return p.stem, str(p)


def load_particles(label: str, path: str) -> pd.DataFrame:
    """Read one particle CSV, tag rows with *run_label*, return DataFrame."""
    df = pd.read_csv(path, low_memory=False)
    df["run_label"] = label
    return df


# ---------------------------------------------------------------------------
# Loss derivation (pure — no I/O)
# ---------------------------------------------------------------------------


def _fval(row: pd.Series, col: str) -> float | None:
    """Return a float value from a row-column pair, or None if missing/nan."""
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_abs_diff_from_one(v: float | None) -> float | None:
    if v is None:
        return None
    return abs(v - 1.0)


def derive_losses(row: pd.Series) -> dict:
    """Compute all lower-is-better audit losses from one particle row.

    Returns a dict of loss column names → float | None.
    Missing source columns produce None for dependent losses, which propagate
    gracefully through composite sums (missing components are treated as 0
    so the composite remains partially informative).
    """
    total_diag = _fval(row, "acceptance_diagnosis_gate_new_diagnoses_ratio")
    msm_diag   = _fval(row, "acceptance_diagnosis_gate_new_diagnoses_msm_ratio")
    plhiv_mape = _fval(row, "acceptance_plhiv_gate_recent_median_abs_pct")
    art_mape   = _fval(row, "acceptance_art_stock_gate_recent_median_abs_pct")
    inc_ratio  = _fval(row, "acceptance_incidence_gate_recent_median_model_target_ratio")
    aids_bf    = _fval(row, "acceptance_aids_death_gate_bounds_fraction")
    aids_peak  = _fval(row, "acceptance_aids_death_gate_peak_to_recent_mean_ratio")
    msm_mape   = _fval(row, "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape")
    msm_early  = _fval(row, "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio")
    aids_mape  = _fval(row, "hist_metric_aids_cases_hist_gate_aids_cases_hist_mape")
    aids_max   = _fval(row, "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio")

    # --- Individual losses ---
    loss_total_diag    = _safe_abs_diff_from_one(total_diag)
    loss_msm_diag      = _safe_abs_diff_from_one(msm_diag)
    loss_plhiv         = (plhiv_mape / 100.0) if plhiv_mape is not None else None
    loss_art           = (art_mape   / 100.0) if art_mape   is not None else None
    loss_incidence     = _safe_abs_diff_from_one(inc_ratio)
    loss_aids_deaths   = (max(0.0, 0.40 - aids_bf)        if aids_bf   is not None else None)
    loss_aids_peak     = (max(0.0, 2.0  - aids_peak)       if aids_peak is not None else None)
    loss_msm_hist_mape = (msm_mape / 100.0)                if msm_mape is not None else None
    loss_msm_early     = (max(0.0, msm_early - 2.5) / 2.5) if msm_early is not None else None
    loss_aids_c_mape   = (aids_mape / 100.0)                if aids_mape is not None else None
    loss_aids_c_max    = (max(0.0, aids_max - 4.0) / 4.0)   if aids_max  is not None else None

    # --- Composite losses (sum available components; None treated as 0) ---
    ep_parts   = [loss_total_diag, loss_msm_diag, loss_plhiv, loss_art, loss_incidence]
    hist_parts = [loss_msm_hist_mape, loss_msm_early, loss_aids_c_mape, loss_aids_c_max]
    aids_parts = [loss_aids_deaths, loss_aids_peak]

    endpoint_loss   = sum(x for x in ep_parts   if x is not None) if any(x is not None for x in ep_parts)   else None
    historical_loss = sum(x for x in hist_parts if x is not None) if any(x is not None for x in hist_parts) else None
    aids_loss       = sum(x for x in aids_parts if x is not None) if any(x is not None for x in aids_parts) else None

    all_parts = [endpoint_loss, historical_loss, aids_loss]
    frontier_loss = sum(x for x in all_parts if x is not None) if any(x is not None for x in all_parts) else None

    return {
        "loss_total_diag_2024":    loss_total_diag,
        "loss_msm_diag_2024":      loss_msm_diag,
        "loss_plhiv_recent":       loss_plhiv,
        "loss_art_recent":         loss_art,
        "loss_incidence_recent":   loss_incidence,
        "loss_aids_deaths":        loss_aids_deaths,
        "loss_aids_peak_shape":    loss_aids_peak,
        "loss_msm_hist_mape":      loss_msm_hist_mape,
        "loss_msm_early_ratio":    loss_msm_early,
        "loss_aids_cases_mape":    loss_aids_c_mape,
        "loss_aids_cases_max_ratio": loss_aids_c_max,
        "endpoint_loss":           endpoint_loss,
        "historical_loss":         historical_loss,
        "aids_loss":               aids_loss,
        "frontier_loss":           frontier_loss,
    }


def add_loss_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of *df* with all loss columns appended."""
    df = df.copy()
    loss_rows = [derive_losses(row) for _, row in df.iterrows()]
    loss_df   = pd.DataFrame(loss_rows, index=df.index)
    return pd.concat([df, loss_df], axis=1)


# ---------------------------------------------------------------------------
# Pareto dominance (pure — no I/O)
# ---------------------------------------------------------------------------


def _row_objectives(row: pd.Series, objectives: list[str]) -> list[float]:
    """Return objective values, substituting +inf for missing/None."""
    vals = []
    for obj in objectives:
        v = row.get(obj)
        try:
            f = float(v)
            vals.append(f if not math.isnan(f) else math.inf)
        except (TypeError, ValueError):
            vals.append(math.inf)
    return vals


def is_dominated(a_obj: list[float], b_obj: list[float]) -> bool:
    """Return True if candidate *a* is Pareto-dominated by *b*.

    *b* dominates *a* iff b is no worse on every objective and strictly better
    on at least one.  Lower values are better (minimisation problem).
    """
    at_least_as_good = all(b <= a for b, a in zip(b_obj, a_obj))
    strictly_better  = any(b <  a for b, a in zip(b_obj, a_obj))
    return at_least_as_good and strictly_better


def compute_pareto_front(df: pd.DataFrame, objectives: list[str]) -> pd.DataFrame:
    """Return the Pareto-non-dominated subset of *df*.

    Uses naïve O(n²) pairwise comparison — acceptable for typical particle
    CSV sizes (< 50 000 rows).
    """
    if df.empty:
        return df.copy()

    obj_matrix = [_row_objectives(row, objectives) for _, row in df.iterrows()]
    n = len(obj_matrix)
    dominated = [False] * n

    for i in range(n):
        if dominated[i]:
            continue
        for j in range(n):
            if i == j or dominated[j]:
                continue
            if is_dominated(obj_matrix[i], obj_matrix[j]):
                dominated[i] = True
                break

    mask = [not d for d in dominated]
    return df[mask].copy()


# ---------------------------------------------------------------------------
# Summary by run
# ---------------------------------------------------------------------------


def _bool_rate(series: pd.Series) -> float | None:
    """Fraction of True/1 values in a boolean-ish series; None if empty."""
    valid = series.dropna()
    if valid.empty:
        return None
    truthy = valid.apply(lambda v: bool(v) if not isinstance(v, float) else v == 1.0)
    return float(truthy.mean())


def _safe_min(series: pd.Series) -> float | None:
    valid = series.dropna()
    if valid.empty:
        return None
    return float(valid.min())


def _safe_median(series: pd.Series) -> float | None:
    valid = series.dropna()
    if valid.empty:
        return None
    return float(valid.median())


def _best_candidate_id(df: pd.DataFrame, col: str, ascending: bool = True) -> object:
    """Return the particle_id of the row with min/max value in *col*."""
    sub = df[df[col].notna()]
    if sub.empty:
        return None
    if ascending:
        idx = sub[col].idxmin()
    else:
        idx = sub[col].idxmax()
    pid_col = "particle_id" if "particle_id" in sub.columns else None
    if pid_col is not None:
        return sub.at[idx, pid_col]
    return int(idx)


def compute_summary_by_run(
    df: pd.DataFrame,
    top_n: int = 50,
) -> pd.DataFrame:
    """Aggregate per-run summary statistics.

    Parameters
    ----------
    df:
        Consolidated DataFrame with *run_label* and all loss columns.
    top_n:
        How many lowest-frontier_loss rows to use for median seed-stage stats.
    """
    rows = []
    for label, group in df.groupby("run_label", sort=False):
        row: dict = {"run_label": label, "n_total": len(group)}

        # Collapsed count
        if "collapsed" in group.columns:
            collapsed_val = group["collapsed"].apply(
                lambda v: bool(v) if not isinstance(v, float) else v == 1.0
            )
            row["n_collapsed"] = int(collapsed_val.sum())
        else:
            row["n_collapsed"] = None

        # Distance / loss best values
        for col in ("abc_distance", "frontier_loss", "endpoint_loss", "historical_loss", "aids_loss"):
            row[f"min_{col}"] = _safe_min(group.get(col, pd.Series(dtype=float)))

        # Gate pass-rates
        gate_cols = _ENDPOINT_GATE_COLS + _HIST_GATE_COLS
        for col in gate_cols:
            if col in group.columns:
                row[f"pass_rate_{col}"] = _bool_rate(group[col])
            else:
                row[f"pass_rate_{col}"] = None

        # Best-candidate IDs
        row["best_particle_abc"]      = _best_candidate_id(group, "abc_distance",   ascending=True)
        row["best_particle_frontier"] = _best_candidate_id(group, "frontier_loss",  ascending=True)
        row["best_particle_endpoint"] = _best_candidate_id(group, "endpoint_loss",  ascending=True)
        row["best_particle_hist"]     = _best_candidate_id(group, "historical_loss",ascending=True)
        row["best_particle_aids"]     = _best_candidate_id(group, "aids_loss",       ascending=True)

        # Median seed-stage params among top-N by frontier_loss
        if "frontier_loss" in group.columns:
            valid_fl = group[group["frontier_loss"].notna()]
            top_group = valid_fl.nsmallest(min(top_n, len(valid_fl)), "frontier_loss")
        else:
            top_group = group.head(top_n)

        for param in _SEED_PARAM_COLS + _V13_10_MULT_COLS:
            row[f"top{top_n}_median_{param}"] = _safe_median(top_group.get(param, pd.Series(dtype=float)))

        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tradeoff / correlation matrix
# ---------------------------------------------------------------------------


def compute_tradeoff_matrix(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Compute Spearman rank-correlation matrix for *cols* present in *df*."""
    available = [c for c in cols if c in df.columns]
    if not available:
        return pd.DataFrame()
    sub = df[available].apply(pd.to_numeric, errors="coerce")
    return sub.corr(method="spearman")


# ---------------------------------------------------------------------------
# Auto-generated interpretation text
# ---------------------------------------------------------------------------


def _interpret(
    summary_df: pd.DataFrame,
    pareto_df: pd.DataFrame,
    tradeoff_df: pd.DataFrame,
    run_labels: list[str],
) -> list[str]:
    """Return a list of plain-text interpretation sentences."""
    lines: list[str] = []

    # Check whether any candidate satisfies all three composite losses < threshold
    if not summary_df.empty:
        has_joint = False
        for _, row in summary_df.iterrows():
            ep   = row.get("min_endpoint_loss")
            hist = row.get("min_historical_loss")
            aids = row.get("min_aids_loss")
            if ep is not None and hist is not None and aids is not None:
                if ep < 0.30 and hist < 0.40 and aids < 0.10:
                    has_joint = True
                    break
        if has_joint:
            lines.append(
                "At least one candidate approaches joint satisfaction of endpoint, "
                "historical, and AIDS objectives (all composite losses within informal thresholds)."
            )
        else:
            lines.append(
                "No candidate jointly satisfies endpoint, historical, and AIDS objectives "
                "within informal thresholds (endpoint < 0.30, hist < 0.40, AIDS < 0.10). "
                "Structural tradeoff likely."
            )

    # Correlation sign: AIDS vs MSM history
    if not tradeoff_df.empty and "aids_loss" in tradeoff_df.index and "loss_msm_hist_mape" in tradeoff_df.columns:
        corr = tradeoff_df.at["aids_loss", "loss_msm_hist_mape"]
        if corr is not None and not math.isnan(float(corr)):
            sign_word = "positively" if corr > 0 else "negatively"
            lines.append(
                f"AIDS loss and MSM history MAPE loss are {sign_word} correlated "
                f"(Spearman r = {corr:.2f}): these objectives appear to "
                + ("trade off against each other." if corr > 0.20 else
                   "move together (no structural tradeoff detected)." if corr < -0.20 else
                   "be weakly correlated (inconclusive).")
            )

    # Compare frontier loss across runs
    if not summary_df.empty and "run_label" in summary_df.columns and "min_frontier_loss" in summary_df.columns:
        valid = summary_df[summary_df["min_frontier_loss"].notna()].copy()
        if len(valid) >= 2:
            valid = valid.sort_values("min_frontier_loss")
            best_label = valid.iloc[0]["run_label"]
            others = [str(r) for r in valid.iloc[1:]["run_label"].tolist()]
            lines.append(
                f"Lowest frontier loss: run '{best_label}' "
                f"(min = {valid.iloc[0]['min_frontier_loss']:.4f}). "
                + (f"Compared to: {', '.join(others)}." if others else "")
            )
            # Specifically comment on V13.11 vs others
            v13_11_rows  = valid[valid["run_label"].str.contains("v13_11", case=False)]
            v13_earlier  = valid[~valid["run_label"].str.contains("v13_11", case=False)]
            if not v13_11_rows.empty and not v13_earlier.empty:
                best_11  = v13_11_rows["min_frontier_loss"].min()
                best_pre = v13_earlier["min_frontier_loss"].min()
                if best_11 < best_pre:
                    lines.append(
                        "V13.11 run(s) achieve a lower frontier loss than earlier runs — "
                        "orientation-specific seeding appears to improve joint fit."
                    )
                else:
                    lines.append(
                        "V13.11 run(s) do not achieve a lower frontier loss than earlier runs — "
                        "orientation-specific seeding does not improve frontier performance on this particle set."
                    )

    # Pareto front size
    lines.append(f"Pareto front contains {len(pareto_df)} non-dominated candidates.")

    return lines


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_TABLE_CSS = """
<style>
body { font-family: Arial, sans-serif; margin: 30px; font-size: 13px; }
h1 { color: #2c3e50; }
h2 { color: #34495e; border-bottom: 1px solid #bdc3c7; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
th { background: #2c3e50; color: white; padding: 6px 10px; text-align: left; font-size: 12px; }
td { padding: 5px 10px; border-bottom: 1px solid #ecf0f1; }
tr:nth-child(even) td { background: #f9f9f9; }
tr:hover td { background: #eaf4ff; }
.good  { background: #d5f5e3 !important; }
.warn  { background: #fef9e7 !important; }
.bad   { background: #fadbd8 !important; }
.note  { font-size: 11px; color: #7f8c8d; }
.interp { background: #eaf4ff; padding: 12px 16px; border-left: 4px solid #2980b9; margin-bottom: 16px; }
</style>
"""


def _df_to_html_table(df: pd.DataFrame, float_fmt: str = "{:.4f}", max_rows: int = 200) -> str:
    if df.empty:
        return "<p class='note'>No data.</p>"
    display = df.head(max_rows).copy()
    # Round floats for readability
    for col in display.select_dtypes(include="float").columns:
        display[col] = display[col].apply(
            lambda v: float_fmt.format(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else ""
        )
    return display.to_html(index=False, border=0, classes="", escape=True)


def build_html_report(
    summary_df:  pd.DataFrame,
    pareto_df:   pd.DataFrame,
    all_df:      pd.DataFrame,
    tradeoff_df: pd.DataFrame,
    run_labels:  list[str],
    top_n:       int,
    manifest:    dict,
) -> str:
    """Assemble a self-contained HTML audit report."""
    now = manifest.get("created_at", "")
    interp_lines = _interpret(summary_df, pareto_df, tradeoff_df, run_labels)

    # Top-N candidates (by frontier_loss) across all runs
    if "frontier_loss" in all_df.columns:
        top_all = all_df[all_df["frontier_loss"].notna()].nsmallest(top_n, "frontier_loss")
    else:
        top_all = all_df.head(top_n)

    # Choose display columns for top candidates
    candidate_display_cols = (
        ["run_label", "particle_id", "generation"]
        + ["abc_distance", "total_ll"]
        + ["overall_accept", "hist_overall_accept"]
        + ["frontier_loss", "endpoint_loss", "historical_loss", "aids_loss"]
        + ["loss_total_diag_2024", "loss_msm_hist_mape", "loss_aids_deaths"]
        + _SEED_PARAM_COLS
    )
    top_cols = [c for c in candidate_display_cols if c in all_df.columns]

    # Tradeoff matrix styling hint
    def _style_corr(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return ""
        if f > 0.50:
            return "bad"
        if f < -0.50:
            return "good"
        return ""

    # Build tradeoff HTML manually for colour coding
    if not tradeoff_df.empty:
        tr_html = "<table><tr><th></th>"
        for col in tradeoff_df.columns:
            tr_html += f"<th>{col}</th>"
        tr_html += "</tr>"
        for row_label, tr_row in tradeoff_df.iterrows():
            tr_html += f"<tr><td><b>{row_label}</b></td>"
            for col in tradeoff_df.columns:
                v = tr_row.get(col)
                css = _style_corr(v)
                fv  = f"{v:.3f}" if v is not None and not (isinstance(v, float) and math.isnan(v)) else ""
                tr_html += f"<td class='{css}'>{fv}</td>"
            tr_html += "</tr>"
        tr_html += "</table>"
    else:
        tr_html = "<p class='note'>No tradeoff data available.</p>"

    missing_note = ""
    if manifest.get("missing_columns"):
        mc = manifest["missing_columns"]
        missing_note = (
            f"<p class='note'>Missing columns in input data "
            f"(metrics will be None for these): {', '.join(mc)}</p>"
        )

    interp_html = "\n".join(
        f"<div class='interp'>{line}</div>" for line in interp_lines
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Calibration Frontier Audit Report</title>
{_TABLE_CSS}
</head>
<body>
<h1>Calibration Frontier Audit Report</h1>
<p class='note'>Generated: {now} &nbsp;|&nbsp; Runs: {', '.join(run_labels)}
&nbsp;|&nbsp; top_n={top_n}</p>
{missing_note}

<h2>Auto-generated Interpretation</h2>
{interp_html}

<h2>Summary by Run</h2>
{_df_to_html_table(summary_df, float_fmt="{:.4f}")}

<h2>Top {top_n} Frontier Candidates (all runs)</h2>
{_df_to_html_table(top_all[top_cols], float_fmt="{:.4f}", max_rows=top_n)}

<h2>Pareto-Non-Dominated Candidates</h2>
<p class='note'>Non-dominated on: {', '.join(_PARETO_OBJECTIVES)}</p>
{_df_to_html_table(pareto_df[top_cols] if not pareto_df.empty and all(c in pareto_df.columns for c in top_cols) else pareto_df, float_fmt="{:.4f}")}

<h2>Tradeoff Matrix (Spearman correlation)</h2>
<p class='note'>Blue = negative correlation (objectives align);
Red = positive correlation (objectives trade off).
|r| &gt; 0.50 highlighted.</p>
{tr_html}

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
        description="Pareto / frontier audit across calibration particle CSVs."
    )
    parser.add_argument(
        "--particles",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help=(
            "Particle CSV to include, in LABEL=PATH format. "
            "Repeat for multiple runs."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for output files.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=50,
        help="Number of top-frontier-loss candidates for summary medians.",
    )
    return parser.parse_args(argv)


def run(
    particles: list[str],
    output_dir: str,
    top_n: int = 50,
) -> dict:
    """Core pipeline.  Returns manifest dict.

    Parameters
    ----------
    particles:
        List of ``LABEL=PATH`` strings.
    output_dir:
        Directory to write output files.
    top_n:
        Top-N threshold for summary medians.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # --- Load and concatenate ---
    frames: list[pd.DataFrame] = []
    run_labels: list[str] = []
    row_counts: dict[str, int] = {}

    for token in particles:
        label, path = parse_label_path(token)
        df = load_particles(label, path)
        frames.append(df)
        run_labels.append(label)
        row_counts[label] = len(df)

    all_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # --- Identify missing columns (for manifest) ---
    expected_cols = (
        _ENDPOINT_GATE_COLS + _HIST_GATE_COLS
        + _SCALAR_METRIC_COLS + _SEED_PARAM_COLS
        + _V13_10_MULT_COLS
    )
    missing_cols = [c for c in expected_cols if c not in all_df.columns]

    # --- Derive losses ---
    if not all_df.empty:
        all_df = add_loss_columns(all_df)

    # --- Write all candidates ---
    all_path = out / "frontier_all_candidates.csv"
    all_df.to_csv(all_path, index=False)

    # --- Pareto front ---
    pareto_df = compute_pareto_front(all_df, _PARETO_OBJECTIVES)
    pareto_path = out / "frontier_pareto_candidates.csv"
    pareto_df.to_csv(pareto_path, index=False)

    # --- Summary by run ---
    summary_df  = compute_summary_by_run(all_df, top_n=top_n)
    summary_path = out / "frontier_summary_by_run.csv"
    summary_df.to_csv(summary_path, index=False)

    # --- Tradeoff matrix ---
    tradeoff_df = compute_tradeoff_matrix(all_df, _TRADEOFF_COLS)
    tradeoff_path = out / "frontier_tradeoff_matrix.csv"
    tradeoff_df.to_csv(tradeoff_path)

    # --- Manifest ---
    manifest: dict = {
        "created_at":     datetime.utcnow().isoformat(timespec="seconds"),
        "run_labels":     run_labels,
        "row_counts":     row_counts,
        "total_rows":     len(all_df),
        "pareto_n":       len(pareto_df),
        "top_n":          top_n,
        "pareto_objectives": _PARETO_OBJECTIVES,
        "missing_columns":   missing_cols,
    }
    manifest_path = out / "frontier_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- HTML report ---
    html = build_html_report(
        summary_df=summary_df,
        pareto_df=pareto_df,
        all_df=all_df,
        tradeoff_df=tradeoff_df,
        run_labels=run_labels,
        top_n=top_n,
        manifest=manifest,
    )
    html_path = out / "frontier_report.html"
    html_path.write_text(html, encoding="utf-8")

    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = run(
        particles=args.particles,
        output_dir=args.output_dir,
        top_n=args.top_n,
    )
    print(f"Frontier audit complete. Rows: {manifest['total_rows']}, "
          f"Pareto front: {manifest['pareto_n']}")
    print(f"Outputs written to: {args.output_dir}")


if __name__ == "__main__":
    main()
