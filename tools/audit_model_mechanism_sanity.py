#!/usr/bin/env python3
"""
V13.13 Code and mechanism sanity audit.

Runs one or more candidate best-fit configs in deterministic mode and inspects
timestep-level outputs to rule out (or confirm) bugs or accounting issues that
suppress AIDS deaths, miscount diagnoses, or mis-route ART protection.

Diagnostic only — does not modify engine behaviour, production configs, or
calibration gates.

Usage
-----
python3 tools/audit_model_mechanism_sanity.py \\
    --candidate outputs/calibration/.../abc_smc_best_fit_rank_001.json \\
    --candidate outputs/calibration/.../abc_smc_best_fit_rank_001.json \\
    --output-dir outputs/calibration/v13_13_closure_audit/mechanism_sanity
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
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_utils import extract_value
from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025
SIM_END_EXCLUSIVE = CALIB_END_YEAR + 1

STEP_ORDERING_DESCRIPTION = [
    "1. Transmission (new infections)",
    "2. Epidemic seeding",
    "3. Migration (HIV-positive entrants)",
    "4. Disease progression (Acute → Chronic → AIDS)",
    "5. Diagnosis (undiagnosed → diagnosed)",
    "6. ART initiation (diagnosed off-ART → on-ART)",
    "7. PrEP transitions",
    "8. Births",
    "9. Childhood mortality",
    "10. HIV mortality (all stages; ART reduction applied)",
    "10.5. Net migration (post-mortality)",
    "11. Aging + sexual debut",
]

CHECK_STATUS = ("cleared", "suspicious", "blocked", "bug_confirmed")


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------


def _prepare_config(config: dict) -> dict:
    """Deep-copy config and force deterministic diagnostic mode."""
    cfg = copy.deepcopy(config)
    rc = cfg.setdefault("run_control", {})
    rc["deterministic_mode"] = {"value": True}
    rc["start_year"] = {"value": CALIB_START_YEAR}
    rc["end_year"] = {"value": SIM_END_EXCLUSIVE}
    cfg.setdefault("output", {})["enabled"] = {"value": False}
    cfg["output"]["terminal_summary"] = {"value": False}
    return cfg


def _run_sim(config: dict, seed: int = 42) -> dict:
    cfg = _prepare_config(config)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(cfg, seed=seed)
        result = engine.run(run_number=1)
    return result


def _annual_summary(result: dict) -> pd.DataFrame:
    df = result["summary"].copy()
    df["calib_year"] = df["year"].astype(float).apply(int)
    agg = {}
    flow_cols = [
        "new_infections", "aids_deaths", "new_aids_cases",
        "new_diagnoses", "new_diagnoses_msm",
        "art_initiations", "art_initiations_aids",
        "hiv_deaths",
    ]
    stock_cols = [
        "prevalence", "sexually_active_population", "on_art_stock",
    ]
    for col in flow_cols:
        if col in df.columns:
            agg[col] = df.groupby("calib_year")[col].sum()
    for col in stock_cols:
        if col in df.columns:
            agg[col] = df.groupby("calib_year")[col].last()
    annual = pd.DataFrame(agg)
    annual.index.name = "year"
    if "prevalence" in annual.columns and "sexually_active_population" in annual.columns:
        annual["PLHIV"] = annual["prevalence"] * annual["sexually_active_population"]
    return annual


# ---------------------------------------------------------------------------
# Check functions (each returns a check dict)
# ---------------------------------------------------------------------------


def check_aids_compartment_entry(annual: pd.DataFrame) -> dict:
    """Are people entering the AIDS compartment?"""
    if "new_aids_cases" not in annual.columns:
        return {
            "name": "aids_compartment_entry",
            "status": "blocked",
            "detail": "new_aids_cases column not available in output.",
        }
    total_aids = annual["new_aids_cases"].sum()
    years_with_aids = int((annual["new_aids_cases"] > 0).sum())
    peak_year = int(annual["new_aids_cases"].idxmax()) if total_aids > 0 else None
    peak_val = float(annual["new_aids_cases"].max()) if total_aids > 0 else 0
    status = "cleared" if total_aids > 100 else "suspicious"
    return {
        "name": "aids_compartment_entry",
        "status": status,
        "detail": (
            f"Total AIDS progressions: {total_aids:.0f} over {years_with_aids} years. "
            f"Peak: {peak_val:.0f} in {peak_year}."
        ),
        "total_aids_cases": float(total_aids),
        "peak_year": peak_year,
        "peak_value": float(peak_val),
    }


def check_aids_deaths_counted(annual: pd.DataFrame) -> dict:
    """Are AIDS deaths being counted in the output used by gates?"""
    if "aids_deaths" not in annual.columns:
        return {
            "name": "aids_deaths_counted",
            "status": "blocked",
            "detail": "aids_deaths column not available.",
        }
    total_aids_deaths = annual["aids_deaths"].sum()
    total_hiv_deaths = annual["hiv_deaths"].sum() if "hiv_deaths" in annual.columns else None
    peak_year = int(annual["aids_deaths"].idxmax()) if total_aids_deaths > 0 else None
    peak_val = float(annual["aids_deaths"].max()) if total_aids_deaths > 0 else 0

    detail_parts = [
        f"Total AIDS deaths: {total_aids_deaths:.0f}.",
        f"Peak: {peak_val:.0f} in {peak_year}." if peak_year else "No peak.",
    ]
    if total_hiv_deaths is not None:
        detail_parts.append(f"Total HIV deaths (all stages): {total_hiv_deaths:.0f}.")
        if total_hiv_deaths > 0:
            aids_fraction = total_aids_deaths / total_hiv_deaths
            detail_parts.append(
                f"AIDS fraction of HIV deaths: {aids_fraction:.2%}."
            )

    if total_aids_deaths == 0 and (annual.get("new_aids_cases", pd.Series()).sum() > 0):
        status = "suspicious"
        detail_parts.append(
            "WARNING: AIDS cases exist but zero AIDS deaths — "
            "mortality may be fully suppressed."
        )
    elif total_aids_deaths < 50:
        status = "suspicious"
    else:
        status = "cleared"

    return {
        "name": "aids_deaths_counted",
        "status": status,
        "detail": " ".join(detail_parts),
        "total_aids_deaths": float(total_aids_deaths),
        "peak_year": peak_year,
        "peak_value": float(peak_val),
    }


def check_art_protection_timing(annual: pd.DataFrame) -> dict:
    """Does ART initiation occur so quickly that AIDS mortality is avoided?"""
    if "on_art_stock" not in annual.columns or "PLHIV" not in annual.columns:
        return {
            "name": "art_protection_timing",
            "status": "blocked",
            "detail": "on_art_stock or PLHIV column not available.",
        }

    annual_copy = annual.copy()
    annual_copy["art_coverage"] = annual_copy.apply(
        lambda r: r["on_art_stock"] / r["PLHIV"] if r["PLHIV"] > 0 else 0, axis=1
    )

    early_years = annual_copy.loc[
        (annual_copy.index >= 1990) & (annual_copy.index <= 2000)
    ]
    recent_years = annual_copy.loc[annual_copy.index >= 2015]

    early_coverage = float(early_years["art_coverage"].median()) if len(early_years) > 0 else 0
    recent_coverage = float(recent_years["art_coverage"].median()) if len(recent_years) > 0 else 0

    art_aids_init = 0
    if "art_initiations_aids" in annual.columns:
        art_aids_init = float(annual["art_initiations_aids"].sum())

    status = "cleared"
    detail_parts = [
        f"Early ART coverage (1990-2000 median): {early_coverage:.2%}.",
        f"Recent ART coverage (2015+ median): {recent_coverage:.2%}.",
        f"Total AIDS-stage ART initiations: {art_aids_init:.0f}.",
    ]
    if early_coverage > 0.50:
        status = "suspicious"
        detail_parts.append(
            "WARNING: Early ART coverage above 50% — historically implausible, "
            "may suppress AIDS deaths in 1990s."
        )
    if art_aids_init > 0:
        detail_parts.append(
            "AIDS-stage patients are being initiated on ART — this directly "
            "reduces their mortality via art_mortality_reduction."
        )

    return {
        "name": "art_protection_timing",
        "status": status,
        "detail": " ".join(detail_parts),
        "early_coverage": early_coverage,
        "recent_coverage": recent_coverage,
        "aids_stage_art_initiations": art_aids_init,
    }


def check_step_ordering_concern() -> dict:
    """Document the step ordering and its implications for AIDS deaths."""
    return {
        "name": "step_ordering_concern",
        "status": "suspicious",
        "detail": (
            "Engine step ordering: progression (→ AIDS) fires at step 4, "
            "diagnosis at step 5, ART initiation at step 6, "
            "HIV mortality at step 10. This means within the SAME timestep, "
            "a patient can progress to AIDS, get diagnosed, initiate ART, "
            "and then face reduced mortality. This is mechanically correct "
            "per the model design but it may compress the window during "
            "which AIDS patients are exposed to un-reduced mortality, "
            "especially under high diagnosis and ART initiation rates."
        ),
        "step_ordering": STEP_ORDERING_DESCRIPTION,
    }


def check_diagnosis_reconciliation(annual: pd.DataFrame) -> dict:
    """Does new_diagnoses_msm + implied non-MSM reconcile with new_diagnoses?"""
    if "new_diagnoses" not in annual.columns or "new_diagnoses_msm" not in annual.columns:
        return {
            "name": "diagnosis_reconciliation",
            "status": "blocked",
            "detail": "new_diagnoses or new_diagnoses_msm column not available.",
        }
    annual_copy = annual.copy()
    annual_copy["implied_non_msm"] = annual_copy["new_diagnoses"] - annual_copy["new_diagnoses_msm"]
    negative_years = int((annual_copy["implied_non_msm"] < -0.5).sum())

    total_diag = float(annual_copy["new_diagnoses"].sum())
    total_msm = float(annual_copy["new_diagnoses_msm"].sum())
    total_non_msm = total_diag - total_msm

    status = "cleared"
    detail_parts = [
        f"Total diagnoses: {total_diag:.0f}, "
        f"MSM: {total_msm:.0f}, non-MSM: {total_non_msm:.0f}.",
    ]
    if negative_years > 0:
        status = "bug_confirmed"
        detail_parts.append(
            f"BUG: {negative_years} years where MSM diagnoses exceed total "
            f"diagnoses — accounting error in diagnosis pathway."
        )
    elif total_non_msm < 10:
        status = "suspicious"
        detail_parts.append(
            "Non-MSM diagnoses are near-zero — check whether non-MSM "
            "HIV-positive stocks are eligible for diagnosis events."
        )

    msm_share = total_msm / total_diag if total_diag > 0 else 0
    detail_parts.append(f"MSM share of all diagnoses: {msm_share:.2%}.")

    return {
        "name": "diagnosis_reconciliation",
        "status": status,
        "detail": " ".join(detail_parts),
        "total_diagnoses": total_diag,
        "total_msm_diagnoses": total_msm,
        "total_non_msm_diagnoses": total_non_msm,
        "msm_share": msm_share,
        "negative_years": negative_years,
    }


def check_aids_death_vs_stock(annual: pd.DataFrame) -> dict:
    """Are AIDS stocks large enough to plausibly generate target deaths?"""
    if "new_aids_cases" not in annual.columns or "aids_deaths" not in annual.columns:
        return {
            "name": "aids_death_vs_stock",
            "status": "blocked",
            "detail": "new_aids_cases or aids_deaths column not available.",
        }

    annual_copy = annual.copy()
    annual_copy["aids_cases_cumulative"] = annual_copy["new_aids_cases"].cumsum()
    annual_copy["aids_deaths_cumulative"] = annual_copy["aids_deaths"].cumsum()
    annual_copy["implied_aids_stock"] = (
        annual_copy["aids_cases_cumulative"] - annual_copy["aids_deaths_cumulative"]
    ).clip(lower=0)

    peak_stock_year = int(annual_copy["implied_aids_stock"].idxmax())
    peak_stock = float(annual_copy["implied_aids_stock"].max())

    cumul_deaths_2005 = float(
        annual_copy.loc[annual_copy.index <= 2005, "aids_deaths"].sum()
    )
    cumul_cases_2005 = float(
        annual_copy.loc[annual_copy.index <= 2005, "new_aids_cases"].sum()
    )

    status = "cleared"
    detail_parts = [
        f"Peak implied AIDS stock: {peak_stock:.0f} in {peak_stock_year}.",
        f"Cumulative AIDS cases to 2005: {cumul_cases_2005:.0f}.",
        f"Cumulative AIDS deaths to 2005: {cumul_deaths_2005:.0f}.",
    ]
    if peak_stock < 20:
        status = "suspicious"
        detail_parts.append(
            "Very low AIDS stock — too few people in AIDS compartment "
            "to generate meaningful death counts."
        )

    return {
        "name": "aids_death_vs_stock",
        "status": status,
        "detail": " ".join(detail_parts),
        "peak_aids_stock": peak_stock,
        "peak_aids_stock_year": peak_stock_year,
        "cumulative_deaths_to_2005": cumul_deaths_2005,
        "cumulative_cases_to_2005": cumul_cases_2005,
    }


def check_art_mortality_reduction_config(config: dict) -> dict:
    """Inspect ART mortality reduction value and scope."""
    disease = config.get("transmission_model", config).get("disease", config.get("disease", {}))
    advanced = disease.get("advanced", {})
    amr_config = advanced.get("art_mortality_reduction", {})
    amr_value = extract_value(amr_config) if amr_config else None
    amr_schedule = amr_config.get("schedule")

    detail_parts = []
    status = "cleared"

    if amr_value is not None:
        detail_parts.append(f"art_mortality_reduction scalar value: {amr_value}")
        if amr_value > 0.95:
            status = "suspicious"
            detail_parts.append(
                "ART reduction > 95% — nearly eliminates AIDS mortality for "
                "anyone on ART."
            )
    else:
        detail_parts.append("art_mortality_reduction not found in config.")
        status = "blocked"

    if amr_schedule:
        detail_parts.append(f"ART mortality reduction has a schedule: {amr_schedule}")

    detail_parts.append(
        "Note: art_mortality_reduction applies to ALL HIV stages on ART, "
        "not just AIDS. The engine applies reduced_rate = base_rate * (1 - reduction) "
        "to on-ART patients at EVERY stage (Acute, Chronic, AIDS)."
    )

    return {
        "name": "art_mortality_reduction_config",
        "status": status,
        "detail": " ".join(detail_parts),
        "amr_value": amr_value,
        "has_schedule": amr_schedule is not None,
    }


def check_orientation_multipliers(config: dict) -> dict:
    """Are orientation contact multipliers properly injected?"""
    groups = config.get("transmission_model", config).get("groups", config.get("groups", {}))
    advanced = groups.get("advanced", {})
    ocm = advanced.get("orientation_contact_multipliers", {})
    ocm_value = ocm.get("value", {})
    if isinstance(ocm_value, dict) and ocm_value.get("value"):
        ocm_value = ocm_value["value"]

    status = "cleared"
    detail_parts = []

    if not ocm_value or not isinstance(ocm_value, dict):
        status = "blocked"
        detail_parts.append("Orientation contact multipliers not found or not a dict.")
    else:
        for orient_name, mult_val in ocm_value.items():
            val = extract_value(mult_val) if isinstance(mult_val, dict) else mult_val
            detail_parts.append(f"{orient_name}: {val}")
        het_male = ocm_value.get("Heterosexual_Male")
        if het_male is not None:
            val = extract_value(het_male) if isinstance(het_male, dict) else het_male
            if abs(float(val) - 1.0) < 0.01:
                detail_parts.append(
                    "Heterosexual_Male multiplier is ~1.0 (reference), as expected."
                )

    return {
        "name": "orientation_multipliers",
        "status": status,
        "detail": " ".join(detail_parts),
        "multipliers": ocm_value if isinstance(ocm_value, dict) else {},
    }


# ---------------------------------------------------------------------------
# Aggregate checks
# ---------------------------------------------------------------------------


def run_all_checks(
    config: dict,
    annual: pd.DataFrame,
    label: str,
) -> list[dict]:
    checks = []
    checks.append(check_aids_compartment_entry(annual))
    checks.append(check_aids_deaths_counted(annual))
    checks.append(check_art_protection_timing(annual))
    checks.append(check_step_ordering_concern())
    checks.append(check_diagnosis_reconciliation(annual))
    checks.append(check_aids_death_vs_stock(annual))
    checks.append(check_art_mortality_reduction_config(config))
    checks.append(check_orientation_multipliers(config))
    for c in checks:
        c["candidate"] = label
    return checks


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_CSS = """<style>
body { font-family: 'Segoe UI', Arial, sans-serif; max-width: 1400px; margin: 2em auto; padding: 0 1em; }
h1, h2, h3 { color: #1a1a2e; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; font-size: 0.85em; }
th { background: #f0f0f0; text-align: center; }
.cleared { background: #d4edda; padding: 0.6em; margin: 0.3em 0; border-left: 4px solid #28a745; }
.suspicious { background: #fff3cd; padding: 0.6em; margin: 0.3em 0; border-left: 4px solid #ffc107; }
.blocked { background: #e2e3e5; padding: 0.6em; margin: 0.3em 0; border-left: 4px solid #6c757d; }
.bug_confirmed { background: #f8d7da; padding: 0.6em; margin: 0.3em 0; border-left: 4px solid #dc3545; }
.note { color: #555; font-size: 0.9em; }
pre { background: #f8f8f8; padding: 1em; overflow-x: auto; font-size: 0.85em; }
</style>"""


def build_html_report(
    all_checks: list[dict],
    timeseries_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    manifest: dict,
) -> str:
    checks_html = ""
    for c in all_checks:
        css_class = c.get("status", "blocked")
        checks_html += (
            f"<div class='{css_class}'>"
            f"<b>[{c['status'].upper()}]</b> {c['candidate']} — "
            f"<b>{c['name']}</b>: {c['detail']}</div>\n"
        )

    ts_table = ""
    if not timeseries_df.empty:
        cols = list(timeseries_df.columns)
        header = "".join(f"<th>{c}</th>" for c in cols)
        rows = []
        for _, row in timeseries_df.head(200).iterrows():
            cells = "".join(f"<td>{v}</td>" for v in row.values)
            rows.append(f"<tr>{cells}</tr>")
        ts_table = f"<table><tr>{header}</tr>{''.join(rows)}</table>"

    comp_table = ""
    if not comparison_df.empty:
        cols = list(comparison_df.columns)
        header = "".join(f"<th>{c}</th>" for c in cols)
        rows = []
        for _, row in comparison_df.iterrows():
            cells = "".join(f"<td>{v}</td>" for v in row.values)
            rows.append(f"<tr>{cells}</tr>")
        comp_table = f"<table><tr>{header}</tr>{''.join(rows)}</table>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.13 Mechanism Sanity Audit</title>
{_CSS}
</head>
<body>
<h1>V13.13 Code and Mechanism Sanity Audit</h1>
<p class='note'>Generated: {manifest.get("created_at", "")} &nbsp;|&nbsp;
Candidates: {manifest.get("n_candidates", "")}</p>

<h2>Engine Step Ordering</h2>
<ol>{''.join(f"<li>{s}</li>" for s in STEP_ORDERING_DESCRIPTION)}</ol>
<p class='note'>Within each timestep, a patient can progress to AIDS, get diagnosed,
start ART, and then face reduced mortality — all in the same step.</p>

<h2>Check Results</h2>
{checks_html}

<h2>Candidate Comparison</h2>
{comp_table if comp_table else "<p class='note'>Single candidate — no comparison.</p>"}

<h2>Annual Time Series</h2>
{ts_table if ts_table else "<p class='note'>No time series data.</p>"}

<h2>Manifest</h2>
<pre>{json.dumps(manifest, indent=2)}</pre>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(
    candidate_paths: list[str],
    output_dir: str,
) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_checks: list[dict] = []
    all_timeseries: list[pd.DataFrame] = []
    comparison_rows: list[dict] = []
    candidate_labels: list[str] = []

    for i, cpath in enumerate(candidate_paths):
        p = Path(cpath)
        if not p.exists():
            print(f"WARNING: candidate not found: {cpath} — skipping")
            continue
        label = p.parent.name + "/" + p.name
        candidate_labels.append(label)
        config = json.loads(p.read_text(encoding="utf-8"))
        print(f"Running candidate {i+1}/{len(candidate_paths)}: {label}")

        result = _run_sim(config)
        annual = _annual_summary(result)
        annual_out = annual.copy()
        annual_out["candidate"] = label
        all_timeseries.append(annual_out.reset_index())

        checks = run_all_checks(config, annual, label)
        all_checks.extend(checks)

        row = {"candidate": label}
        if "aids_deaths" in annual.columns:
            row["total_aids_deaths"] = float(annual["aids_deaths"].sum())
            row["peak_aids_deaths"] = float(annual["aids_deaths"].max())
            row["peak_aids_deaths_year"] = int(annual["aids_deaths"].idxmax()) if row["peak_aids_deaths"] > 0 else None
            row["aids_deaths_1990_2005"] = float(
                annual.loc[(annual.index >= 1990) & (annual.index <= 2005), "aids_deaths"].sum()
            )
        if "new_diagnoses" in annual.columns:
            row["total_diagnoses"] = float(annual["new_diagnoses"].sum())
        if "new_diagnoses_msm" in annual.columns:
            row["total_msm_diagnoses"] = float(annual["new_diagnoses_msm"].sum())
        if "PLHIV" in annual.columns:
            row["plhiv_2023"] = float(annual.loc[2023, "PLHIV"]) if 2023 in annual.index else None
        if "on_art_stock" in annual.columns:
            row["art_stock_2023"] = float(annual.loc[2023, "on_art_stock"]) if 2023 in annual.index else None
        if "new_aids_cases" in annual.columns:
            row["total_aids_cases"] = float(annual["new_aids_cases"].sum())
        n_suspicious = sum(1 for c in checks if c["status"] == "suspicious")
        n_bugs = sum(1 for c in checks if c["status"] == "bug_confirmed")
        row["n_suspicious"] = n_suspicious
        row["n_bug_confirmed"] = n_bugs
        comparison_rows.append(row)

    timeseries_df = pd.concat(all_timeseries, ignore_index=True) if all_timeseries else pd.DataFrame()
    comparison_df = pd.DataFrame(comparison_rows) if comparison_rows else pd.DataFrame()

    summary_dict = {
        "checks": [
            {k: v for k, v in c.items() if k not in ("step_ordering",)}
            for c in all_checks
        ],
        "status_counts": {
            s: sum(1 for c in all_checks if c["status"] == s)
            for s in CHECK_STATUS
        },
    }
    (out / "mechanism_sanity_summary.json").write_text(
        json.dumps(summary_dict, indent=2, default=str), encoding="utf-8"
    )
    timeseries_df.to_csv(out / "mechanism_sanity_timeseries.csv", index=False)
    comparison_df.to_csv(out / "mechanism_sanity_candidate_comparison.csv", index=False)

    manifest = {
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "candidate_paths": candidate_paths,
        "n_candidates": len(candidate_labels),
        "candidates_found": candidate_labels,
        "status_counts": summary_dict["status_counts"],
    }
    html = build_html_report(all_checks, timeseries_df, comparison_df, manifest)
    (out / "mechanism_sanity_report.html").write_text(html, encoding="utf-8")

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V13.13 mechanism sanity audit — run candidates and inspect outputs."
    )
    parser.add_argument(
        "--candidate", action="append", required=True,
        help="Path to a best-fit JSON config (can be repeated).",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = run(
        candidate_paths=args.candidate,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
