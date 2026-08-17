#!/usr/bin/env python3
"""
V13.13 Constraint conflict audit across calibration particle CSVs.

Reads one or more pre-computed particle CSVs and determines whether the
acceptance gates are empirically incompatible under the current model
architecture.  Reports per-gate pass rates, gate intersection counts,
conditional metric summaries, and parameter frontiers.

Diagnostic only — no model simulation is run.  Does not modify production
configs, calibration spaces, gates, or acceptance logic.

Usage
-----
python3 tools/audit_calibration_constraint_conflicts.py \\
    --particles outputs/calibration/.../abc_smc_particles_*.csv \\
    --compare-particles outputs/calibration/.../abc_smc_particles_*.csv \\
    --output-dir outputs/calibration/v13_13_closure_audit/constraint_conflicts
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
# Gate columns
# ---------------------------------------------------------------------------

RECENT_GATES: list[str] = [
    "diagnosis_gate_pass",
    "plhiv_gate_pass",
    "incidence_gate_pass",
    "aids_death_gate_pass",
    "art_stock_gate_pass",
]

HISTORICAL_GATES: list[str] = [
    "plhiv_hist_gate_pass",
    "diagnoses_hist_gate_pass",
    "msm_diag_hist_gate_pass",
    "aids_cases_hist_gate_pass",
]

ALL_GATES: list[str] = RECENT_GATES + HISTORICAL_GATES

# ---------------------------------------------------------------------------
# Metric columns used for subset summaries
# ---------------------------------------------------------------------------

METRIC_COLS: dict[str, str] = {
    "acceptance_diagnosis_gate_new_diagnoses_ratio": "total_diag_ratio",
    "acceptance_diagnosis_gate_new_diagnoses_msm_ratio": "msm_diag_ratio",
    "acceptance_plhiv_gate_recent_median_abs_pct": "plhiv_recent_mape",
    "acceptance_art_stock_gate_recent_median_abs_pct": "art_recent_mape",
    "acceptance_incidence_gate_recent_median_model_target_ratio": "incidence_recent_ratio",
    "acceptance_aids_death_gate_bounds_fraction": "aids_death_bounds_fraction",
    "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape": "msm_diag_hist_mape",
    "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": "msm_early_max_ratio",
    "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio": "aids_cases_max_ratio",
}

# ---------------------------------------------------------------------------
# Key parameters for frontier table
# ---------------------------------------------------------------------------

FRONTIER_PARAMS: list[str] = [
    "seed_prevalence", "seed_msm_fraction", "seed_chronic_share",
    "seed_aids_share", "p_msm",
    "diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2015",
    "art_init_1996", "art_init_2000", "art_init_2015", "art_init_2020",
    "chronic_to_aids", "chronic_to_aids_on_art",
    "aids_mortality", "art_mortality_reduction",
    "orient_het_male", "orient_msm",
    "msm_diag_mult_1996", "msm_diag_mult_2004", "msm_diag_mult_2015",
]

# ---------------------------------------------------------------------------
# Gate intersection definitions
# ---------------------------------------------------------------------------

GATE_INTERSECTIONS: list[tuple[str, list[str]]] = [
    ("diagnosis + aids_death", ["diagnosis_gate_pass", "aids_death_gate_pass"]),
    ("diagnosis + plhiv + art + incidence",
     ["diagnosis_gate_pass", "plhiv_gate_pass", "art_stock_gate_pass", "incidence_gate_pass"]),
    ("aids_death + plhiv + art + incidence",
     ["aids_death_gate_pass", "plhiv_gate_pass", "art_stock_gate_pass", "incidence_gate_pass"]),
    ("diagnosis + aids_death + plhiv + art + incidence",
     ["diagnosis_gate_pass", "aids_death_gate_pass", "plhiv_gate_pass",
      "art_stock_gate_pass", "incidence_gate_pass"]),
    ("diagnosis + aids_death + msm_diag_hist",
     ["diagnosis_gate_pass", "aids_death_gate_pass", "msm_diag_hist_gate_pass"]),
    ("all_recent", RECENT_GATES),
    ("all_historical", HISTORICAL_GATES),
    ("all_recent + all_historical", ALL_GATES),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fval(row: pd.Series, col: str) -> float | None:
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def is_evaluable_row(row: pd.Series) -> bool:
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


def _gate_bool(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].astype(str).str.strip().str.lower().isin({"true", "1"})


# ---------------------------------------------------------------------------
# Core analysis functions (pure — no I/O)
# ---------------------------------------------------------------------------


def compute_gate_pass_rates(df: pd.DataFrame) -> dict[str, dict]:
    """Per-gate pass counts and rates."""
    n = len(df)
    result = {}
    for gate in ALL_GATES:
        if gate in df.columns:
            passing = int(_gate_bool(df, gate).sum())
            result[gate] = {"n_pass": passing, "n_total": n,
                            "rate": round(passing / n, 6) if n > 0 else 0.0}
        else:
            result[gate] = {"n_pass": 0, "n_total": n, "rate": 0.0, "missing": True}
    return result


def compute_gate_intersections(df: pd.DataFrame) -> list[dict]:
    """Count particles passing each gate combination."""
    n = len(df)
    results = []
    for label, gates in GATE_INTERSECTIONS:
        mask = pd.Series(True, index=df.index)
        missing_gates = []
        for g in gates:
            if g in df.columns:
                mask = mask & _gate_bool(df, g)
            else:
                mask = mask & False
                missing_gates.append(g)
        passing = int(mask.sum())
        results.append({
            "intersection": label,
            "gates": gates,
            "n_pass": passing,
            "n_total": n,
            "rate": round(passing / n, 6) if n > 0 else 0.0,
            "missing_gates": missing_gates,
        })
    return results


def summarize_subset_metrics(df: pd.DataFrame, mask: pd.Series,
                             label: str) -> dict:
    """Summarize key metrics for a subset of particles."""
    sub = df[mask]
    n = len(sub)
    summary = {"label": label, "n": n}
    for col, short_name in METRIC_COLS.items():
        if col in sub.columns:
            vals = pd.to_numeric(sub[col], errors="coerce").dropna()
            if len(vals) > 0:
                summary[f"{short_name}_median"] = round(float(vals.median()), 6)
                summary[f"{short_name}_mean"] = round(float(vals.mean()), 6)
                summary[f"{short_name}_min"] = round(float(vals.min()), 6)
                summary[f"{short_name}_max"] = round(float(vals.max()), 6)
                summary[f"{short_name}_p10"] = round(float(vals.quantile(0.10)), 6)
                summary[f"{short_name}_p90"] = round(float(vals.quantile(0.90)), 6)
    return summary


def compute_conditional_summaries(df: pd.DataFrame) -> list[dict]:
    """For each major gate, summarise metrics among particles that pass it."""
    summaries = []
    for gate in ALL_GATES:
        if gate in df.columns:
            mask = _gate_bool(df, gate)
            summaries.append(summarize_subset_metrics(df, mask, f"pass_{gate}"))
            summaries.append(summarize_subset_metrics(df, ~mask, f"fail_{gate}"))
    summaries.append(summarize_subset_metrics(
        df, pd.Series(True, index=df.index), "all_particles"))
    return summaries


def build_frontier_table(df: pd.DataFrame, top_n: int = 25) -> pd.DataFrame:
    """Build a parameter frontier table from multiple selection criteria."""
    metric_cols_present = [c for c in METRIC_COLS.keys() if c in df.columns]
    param_cols_present = [c for c in FRONTIER_PARAMS if c in df.columns]
    sort_cols = ["acceptance_n_failures", "hist_n_failures", "abc_distance"]
    sort_cols_present = [c for c in sort_cols if c in df.columns]

    all_frontier_cols = (
        ["particle_id", "generation", "source_label"]
        + sort_cols_present
        + metric_cols_present
        + param_cols_present
    )
    all_frontier_cols = [c for c in all_frontier_cols if c in df.columns]

    selections = []

    if sort_cols_present:
        by_failures = df.nsmallest(top_n, sort_cols_present, keep="first")
        by_failures = by_failures.copy()
        by_failures["frontier_source"] = "top_by_failures"
        selections.append(by_failures)

    if "diagnosis_gate_pass" in df.columns:
        diag_pass = df[_gate_bool(df, "diagnosis_gate_pass")]
        if len(diag_pass) > 0:
            if "abc_distance" in diag_pass.columns:
                diag_pass = diag_pass.nsmallest(top_n, "abc_distance", keep="first")
            else:
                diag_pass = diag_pass.head(top_n)
            diag_pass = diag_pass.copy()
            diag_pass["frontier_source"] = "top_diagnosis_pass"
            selections.append(diag_pass)

    if "aids_death_gate_pass" in df.columns:
        aids_pass = df[_gate_bool(df, "aids_death_gate_pass")]
        if len(aids_pass) > 0:
            if "abc_distance" in aids_pass.columns:
                aids_pass = aids_pass.nsmallest(top_n, "abc_distance", keep="first")
            else:
                aids_pass = aids_pass.head(top_n)
            aids_pass = aids_pass.copy()
            aids_pass["frontier_source"] = "top_aids_death_pass"
            selections.append(aids_pass)

    if "abc_distance" in df.columns:
        by_distance = df.nsmallest(top_n, "abc_distance", keep="first").copy()
        by_distance["frontier_source"] = "top_by_distance"
        selections.append(by_distance)

    if not selections:
        return pd.DataFrame()

    frontier = pd.concat(selections, ignore_index=True)
    keep_cols = ["frontier_source"] + all_frontier_cols
    keep_cols = [c for c in keep_cols if c in frontier.columns]
    return frontier[keep_cols].drop_duplicates(
        subset=["particle_id"] if "particle_id" in frontier.columns else None
    )


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------


def interpret_conflicts(
    pass_rates: dict,
    intersections: list[dict],
    conditional_summaries: list[dict],
) -> list[str]:
    """Produce human-readable interpretation lines."""
    lines = []

    diag_aids = next(
        (i for i in intersections if i["intersection"] == "diagnosis + aids_death"),
        None,
    )
    if diag_aids:
        if diag_aids["n_pass"] == 0:
            lines.append(
                "CONFLICT DETECTED: Zero particles pass both diagnosis and AIDS-death "
                "gates simultaneously. These gates appear empirically incompatible."
            )
        elif diag_aids["rate"] < 0.001:
            lines.append(
                f"NEAR-CONFLICT: Only {diag_aids['n_pass']}/{diag_aids['n_total']} "
                f"particles pass both diagnosis and AIDS-death gates (rate {diag_aids['rate']:.4f})."
            )

    all_recent = next(
        (i for i in intersections if i["intersection"] == "all_recent"), None
    )
    if all_recent and all_recent["n_pass"] == 0:
        lines.append(
            "CONFLICT: No particle passes all 5 recent gates simultaneously."
        )

    all_combo = next(
        (i for i in intersections
         if i["intersection"] == "all_recent + all_historical"), None
    )
    if all_combo and all_combo["n_pass"] == 0:
        lines.append(
            "CONFLICT: No particle passes all recent + historical gates."
        )

    diag_pass_summary = next(
        (s for s in conditional_summaries if s["label"] == "pass_diagnosis_gate_pass"),
        None,
    )
    if diag_pass_summary and diag_pass_summary["n"] > 0:
        aids_bf_med = diag_pass_summary.get("aids_death_bounds_fraction_median")
        if aids_bf_med is not None and aids_bf_med < 0.15:
            lines.append(
                f"Among diagnosis-passing particles, AIDS-death bounds fraction "
                f"median is very low ({aids_bf_med:.3f}), suggesting systematic "
                f"inability to fit AIDS deaths when diagnosis is matched."
            )

    aids_pass_summary = next(
        (s for s in conditional_summaries if s["label"] == "pass_aids_death_gate_pass"),
        None,
    )
    if aids_pass_summary and aids_pass_summary["n"] > 0:
        diag_med = aids_pass_summary.get("total_diag_ratio_median")
        if diag_med is not None and diag_med < 0.70:
            lines.append(
                f"Among AIDS-death-passing particles, total diagnosis ratio "
                f"median is {diag_med:.3f} — well below the 0.90 gate floor."
            )

    if not lines:
        lines.append(
            "No clear gate conflict detected. Search insufficiency or marginal "
            "near-misses may explain the failure pattern."
        )

    diag_pr = pass_rates.get("diagnosis_gate_pass", {})
    aids_pr = pass_rates.get("aids_death_gate_pass", {})
    if diag_pr.get("n_pass", 0) > 0 and aids_pr.get("n_pass", 0) > 0 and diag_aids and diag_aids["n_pass"] == 0:
        lines.append(
            f"Note: diagnosis gate passes independently ({diag_pr['n_pass']}), "
            f"AIDS-death gate passes independently ({aids_pr['n_pass']}), "
            f"but their intersection is empty. This is the hallmark of a structural tradeoff."
        )

    return lines


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------

_CSS = """<style>
body { font-family: 'Segoe UI', Arial, sans-serif; max-width: 1400px; margin: 2em auto; padding: 0 1em; }
h1, h2, h3 { color: #1a1a2e; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; font-size: 0.85em; }
th { background: #f0f0f0; text-align: center; }
.conflict { background: #ffe0e0; padding: 0.8em; margin: 0.5em 0; border-left: 4px solid #cc0000; }
.note { color: #555; font-size: 0.9em; }
.warn { background: #fff3cd; padding: 0.8em; border-left: 4px solid #ffc107; }
.good { background: #d4edda; padding: 0.8em; border-left: 4px solid #28a745; }
pre { background: #f8f8f8; padding: 1em; overflow-x: auto; font-size: 0.85em; }
</style>"""


def _df_to_html_table(df: pd.DataFrame, max_rows: int = 100) -> str:
    if df.empty:
        return "<p class='note'>No data.</p>"
    show = df.head(max_rows)
    header = "".join(f"<th>{c}</th>" for c in show.columns)
    rows = []
    for _, row in show.iterrows():
        cells = "".join(f"<td>{v}</td>" for v in row.values)
        rows.append(f"<tr>{cells}</tr>")
    note = ""
    if len(df) > max_rows:
        note = f"<p class='note'>Showing {max_rows} of {len(df)} rows.</p>"
    return f"<table><tr>{header}</tr>{''.join(rows)}</table>{note}"


def build_html_report(
    source_label: str,
    pass_rates: dict,
    intersections: list[dict],
    conditional_summaries: list[dict],
    frontier_df: pd.DataFrame,
    interpretation: list[str],
    manifest: dict,
) -> str:
    interp_html = "\n".join(
        f"<div class='conflict'>{line}</div>" if "CONFLICT" in line
        else f"<div class='warn'>{line}</div>" if "NEAR" in line or "Note:" in line
        else f"<div class='good'>{line}</div>"
        for line in interpretation
    )

    pr_rows = "".join(
        f"<tr><td>{g}</td><td>{info['n_pass']}</td><td>{info['n_total']}</td>"
        f"<td>{info['rate']:.4f}</td></tr>"
        for g, info in pass_rates.items()
    )
    pr_table = (
        "<table><tr><th>Gate</th><th>Pass</th><th>Total</th><th>Rate</th></tr>"
        f"{pr_rows}</table>"
    )

    int_rows = "".join(
        f"<tr><td style='text-align:left'>{i['intersection']}</td>"
        f"<td>{i['n_pass']}</td><td>{i['n_total']}</td>"
        f"<td>{i['rate']:.4f}</td></tr>"
        for i in intersections
    )
    int_table = (
        "<table><tr><th>Intersection</th><th>Pass</th><th>Total</th><th>Rate</th></tr>"
        f"{int_rows}</table>"
    )

    cond_df = pd.DataFrame(conditional_summaries)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.13 Constraint Conflict Audit — {source_label}</title>
{_CSS}
</head>
<body>
<h1>V13.13 Constraint Conflict Audit</h1>
<p class='note'>Source: {source_label} &nbsp;|&nbsp;
Generated: {manifest.get("created_at", "")} &nbsp;|&nbsp;
Particles: {manifest.get("n_total", "")}</p>
<p class='warn'><b>Diagnostic only.</b> This audit checks whether calibration
gates are empirically compatible. It does not modify gates, configs, or
acceptance logic.</p>

<h2>Interpretation</h2>
{interp_html}

<h2>Per-Gate Pass Rates</h2>
{pr_table}

<h2>Gate Intersections</h2>
{int_table}

<h2>Conditional Metric Summaries (pass/fail each gate)</h2>
{_df_to_html_table(cond_df, max_rows=50)}

<h2>Parameter Frontier Table</h2>
{_df_to_html_table(frontier_df, max_rows=200)}

<h2>Manifest</h2>
<pre>{json.dumps(manifest, indent=2)}</pre>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(
    particles_path: str,
    output_dir: str,
    compare_particles_path: str | None = None,
    top_n: int = 25,
) -> dict:
    """Core pipeline. Returns the manifest dict."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    csvs: list[tuple[str, pd.DataFrame]] = []
    primary = pd.read_csv(particles_path, low_memory=False)
    primary["source_label"] = "primary"
    csvs.append(("primary", primary))

    if compare_particles_path is not None:
        compare = pd.read_csv(compare_particles_path, low_memory=False)
        compare["source_label"] = "compare"
        csvs.append(("compare", compare))

    combined = pd.concat([df for _, df in csvs], ignore_index=True)
    evaluable_mask = combined.apply(is_evaluable_row, axis=1)
    combined_eval = combined[evaluable_mask].copy()

    all_pass_rates = {}
    all_intersections = []
    all_conditional = []
    all_interpretations = []
    all_frontiers = []

    for label, df in [("combined", combined_eval)] + csvs:
        sub = df[df.apply(is_evaluable_row, axis=1)].copy() if label != "combined" else df
        pr = compute_gate_pass_rates(sub)
        gi = compute_gate_intersections(sub)
        cs = compute_conditional_summaries(sub)
        interp = interpret_conflicts(pr, gi, cs)
        frontier = build_frontier_table(sub, top_n=top_n)

        all_pass_rates[label] = pr
        all_intersections.append({"source": label, "intersections": gi})
        all_conditional.extend([{**s, "source": label} for s in cs])
        all_interpretations.append({"source": label, "interpretation": interp})
        if not frontier.empty:
            frontier["analysis_source"] = label
            all_frontiers.append(frontier)

    pass_rate_rows = []
    for source, rates in all_pass_rates.items():
        for gate, info in rates.items():
            pass_rate_rows.append({"source": source, "gate": gate, **info})
    pd.DataFrame(pass_rate_rows).to_csv(
        out / "constraint_gate_pass_rates.csv", index=False
    )

    intersection_rows = []
    for entry in all_intersections:
        for gi in entry["intersections"]:
            intersection_rows.append({
                "source": entry["source"],
                "intersection": gi["intersection"],
                "n_pass": gi["n_pass"],
                "n_total": gi["n_total"],
                "rate": gi["rate"],
            })
    pd.DataFrame(intersection_rows).to_csv(
        out / "constraint_gate_intersections.csv", index=False
    )

    pd.DataFrame(all_conditional).to_csv(
        out / "constraint_subset_summaries.csv", index=False
    )

    frontier_combined = (
        pd.concat(all_frontiers, ignore_index=True)
        if all_frontiers
        else pd.DataFrame()
    )
    frontier_combined.to_csv(
        out / "constraint_frontier_particles.csv", index=False
    )

    summary_dict = {
        "pass_rates": all_pass_rates,
        "interpretations": {
            entry["source"]: entry["interpretation"]
            for entry in all_interpretations
        },
        "intersection_highlights": {
            entry["source"]: {
                gi["intersection"]: gi["n_pass"]
                for gi in entry["intersections"]
            }
            for entry in all_intersections
        },
    }
    (out / "constraint_conflict_summary.json").write_text(
        json.dumps(summary_dict, indent=2), encoding="utf-8"
    )

    manifest = {
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "particles_path": str(particles_path),
        "compare_particles_path": str(compare_particles_path) if compare_particles_path else None,
        "n_total": len(combined),
        "n_evaluable": len(combined_eval),
        "n_sources": len(csvs),
        "top_n": top_n,
    }

    combined_pr = all_pass_rates.get("combined", {})
    combined_gi = next(
        (e["intersections"] for e in all_intersections if e["source"] == "combined"),
        [],
    )
    combined_cs = [s for s in all_conditional if s.get("source") == "combined"]
    combined_interp = next(
        (e["interpretation"] for e in all_interpretations if e["source"] == "combined"),
        [],
    )

    html = build_html_report(
        source_label="combined" if compare_particles_path else "primary",
        pass_rates=combined_pr,
        intersections=combined_gi,
        conditional_summaries=combined_cs,
        frontier_df=frontier_combined,
        interpretation=combined_interp,
        manifest=manifest,
    )
    (out / "constraint_conflict_report.html").write_text(html, encoding="utf-8")

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V13.13 constraint conflict audit over particle CSVs."
    )
    parser.add_argument(
        "--particles", required=True,
        help="Path to the primary particle CSV.",
    )
    parser.add_argument(
        "--compare-particles", default=None,
        help="Path to a second particle CSV for comparison.",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files.",
    )
    parser.add_argument(
        "--top-n", type=int, default=25,
        help="Top-N particles per frontier selection criterion.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = run(
        particles_path=args.particles,
        compare_particles_path=args.compare_particles,
        output_dir=args.output_dir,
        top_n=args.top_n,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
