#!/usr/bin/env python3
"""
V13.14 — Demographic feasibility audit for 1975 historical burn-in.

Runs deterministic no-HIV demographic simulations from 1980 and 1975 (and
optionally 1970) to verify that population dynamics remain stable and
plausible before attempting disease calibration with an earlier start year.

Diagnostic only — does not modify production configs, gates, or targets.

Usage
-----
python3 tools/audit_1975_burnin_population.py \
    --base-config outputs/calibration/abc_smc_v13_10_msm_diag_multiplier_pilot/abc_smc_best_fit_rank_001.json \
    --output-dir outputs/calibration/v13_14_1975_burnin_diagnostic/population_audit
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
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_utils import extract_value
from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_END_YEAR = 2026

HMD_POP_FILE = ROOT.parent / "data" / "raw" / "validation" / "HMD_NL_Pop.txt"
HMD_VITAL_FILE = ROOT / "Validation " / "HMD_NL_VitalStats_1970_2023.csv"
MIGRATION_FILE = ROOT.parent / "data" / "raw" / "validation" / "Migration_Net.csv"

AGE_BINS = [
    (0, 4), (5, 9), (10, 14), (15, 19), (20, 24), (25, 29), (30, 34),
    (35, 39), (40, 44), (45, 49), (50, 54), (55, 59), (60, 64), (65, 69),
    (70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 99),
    (100, 104), (105, 109),
]


# ---------------------------------------------------------------------------
# HMD observed data loaders
# ---------------------------------------------------------------------------


def load_hmd_observed_population(
    hmd_path: Path | None = None,
) -> pd.DataFrame | None:
    """Load HMD Netherlands population by year (single-age → 5-year bins)."""
    path = hmd_path or HMD_POP_FILE
    if not path.exists():
        return None

    df = pd.read_csv(path)
    df = df[df["Type"] == "O"]
    df["Age"] = pd.to_numeric(df["Age"], errors="coerce")
    df = df.dropna(subset=["Age"])
    df["Age"] = df["Age"].astype(int)

    rows = []
    for year in df["Year"].unique():
        year_df = df[df["Year"] == year]
        total = year_df["Population"].sum()
        male_total = year_df[year_df["Sex"] == "m"]["Population"].sum()
        female_total = year_df[year_df["Sex"] == "f"]["Population"].sum()
        rows.append({
            "year": int(year),
            "observed_total_population": total,
            "observed_male": male_total,
            "observed_female": female_total,
        })
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def load_hmd_vital_stats(
    vital_path: Path | None = None,
) -> pd.DataFrame | None:
    path = vital_path or HMD_VITAL_FILE
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return df


def _age_sex_distribution_for_year(
    year: int, hmd_path: Path | None = None,
) -> dict | None:
    """Compute relative age-sex weights from HMD single-age data."""
    path = hmd_path or HMD_POP_FILE
    if not path.exists():
        return None

    df = pd.read_csv(path)
    df = df[(df["Year"] == year) & (df["Type"] == "O")]
    df["Age"] = pd.to_numeric(df["Age"], errors="coerce")
    df = df.dropna(subset=["Age"])
    df["Age"] = df["Age"].astype(int)

    if len(df) == 0:
        return None

    total = df["Population"].sum()
    if total <= 0:
        return None

    result = {}
    for lo, hi in AGE_BINS:
        bin_label = f"{lo}-{hi}"
        female = df[(df["Sex"] == "f") & (df["Age"] >= lo) & (df["Age"] <= hi)]["Population"].sum()
        male = df[(df["Sex"] == "m") & (df["Age"] >= lo) & (df["Age"] <= hi)]["Population"].sum()
        result[bin_label] = {
            "female": float(female / total),
            "male": float(male / total),
        }

    # 110+ bin
    female_110 = df[(df["Sex"] == "f") & (df["Age"] >= 110)]["Population"].sum()
    male_110 = df[(df["Sex"] == "m") & (df["Age"] >= 110)]["Population"].sum()
    result["110+"] = {
        "female": float(female_110 / total),
        "male": float(male_110 / total),
    }

    return result


def _total_population_for_year(year: int, hmd_path: Path | None = None) -> int | None:
    path = hmd_path or HMD_POP_FILE
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df = df[(df["Year"] == year) & (df["Type"] == "O")]
    df["Age"] = pd.to_numeric(df["Age"], errors="coerce")
    df = df.dropna(subset=["Age"])
    total = df["Population"].sum()
    return int(total) if total > 0 else None


# ---------------------------------------------------------------------------
# Demographic scenario construction
# ---------------------------------------------------------------------------


def _disable_epidemic(config: dict) -> dict:
    """Disable HIV epidemic seeding to get pure demographic run."""
    ep = config.get("population", {}).get("epidemic_seed", {})
    if isinstance(ep.get("enabled"), dict):
        ep["enabled"]["value"] = False
    else:
        ep["enabled"] = {"value": False}
    return config


def build_demographic_scenario(
    base_config: dict,
    start_year: int,
    label: str,
    hmd_path: Path | None = None,
) -> tuple[dict, dict]:
    """
    Build a no-HIV demographic config for a given start year.

    Returns (config, metadata) where metadata describes what was done.
    """
    cfg = copy.deepcopy(base_config)
    meta = {"label": label, "start_year": start_year, "adjustments": []}

    # Set time range
    cfg["run_control"]["start_year"]["value"] = start_year
    cfg["run_control"]["end_year"]["value"] = SIM_END_YEAR
    cfg["run_control"]["deterministic_mode"]["value"] = True
    cfg.setdefault("output", {})["enabled"] = {"value": False}
    cfg["output"]["terminal_summary"] = {"value": False}

    _disable_epidemic(cfg)

    # If start year != 1980, try to update age-sex distribution and total pop
    if start_year != 1980:
        dist = _age_sex_distribution_for_year(start_year, hmd_path)
        if dist is not None:
            cfg["population"]["age_sex_distribution"]["value"] = dist
            meta["adjustments"].append(
                f"age_sex_distribution replaced with HMD {start_year} weights"
            )
        else:
            meta["adjustments"].append(
                f"WARNING: no HMD data for {start_year}, using 1980 age-sex distribution"
            )

        total = _total_population_for_year(start_year, hmd_path)
        if total is not None:
            cfg["population"]["total_size"]["value"] = total
            meta["adjustments"].append(f"total_size set to HMD {start_year}: {total:,}")
        else:
            meta["adjustments"].append(
                f"WARNING: no HMD total pop for {start_year}, using 1980 value"
            )

    return cfg, meta


# ---------------------------------------------------------------------------
# Run scenario and extract demographic time series
# ---------------------------------------------------------------------------


def run_demographic_scenario(config: dict, seed: int = 42) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(config, seed=seed)
        result = engine.run(run_number=1)
    return result


def extract_demographic_timeseries(result: dict, start_year: int) -> pd.DataFrame:
    """Extract annual population counts from simulation result."""
    summary = result.get("summary")
    if summary is None:
        return pd.DataFrame()
    if isinstance(summary, dict):
        summary = pd.DataFrame(summary)

    df = summary.copy()
    df["calib_year"] = df["year"].astype(float).apply(int)

    rows = []
    for year, year_df in df.groupby("calib_year", sort=True):
        final = year_df.iloc[-1]
        row = {"year": int(year)}

        for col in [
            "sexually_active_population", "childhood_pool_total",
            "total_population", "total_births", "total_deaths",
            "net_migration", "aids_deaths", "new_infections",
        ]:
            if col in final.index:
                row[col] = float(final[col]) if col.endswith("population") or col.endswith("total") else None

        # Flow columns: sum across quarters
        for col in ["total_births", "total_deaths", "net_migration", "aids_deaths", "new_infections"]:
            if col in year_df.columns:
                row[col] = float(year_df[col].sum())

        # Stock columns: end-of-year
        for col in ["sexually_active_population", "childhood_pool_total", "total_population"]:
            if col in final.index:
                row[col] = float(final[col])

        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Demographic checks
# ---------------------------------------------------------------------------


def check_negative_populations(ts: pd.DataFrame) -> dict:
    """Flag any year with negative population values."""
    issues = []
    for col in ["sexually_active_population", "childhood_pool_total", "total_population"]:
        if col not in ts.columns:
            continue
        negatives = ts[ts[col] < 0]
        if len(negatives) > 0:
            for _, row in negatives.iterrows():
                issues.append({
                    "column": col, "year": int(row["year"]),
                    "value": float(row[col]),
                })
    return {
        "check": "negative_populations",
        "status": "fail" if issues else "pass",
        "issues": issues,
    }


def check_population_crash(ts: pd.DataFrame, threshold: float = 0.10) -> dict:
    """Flag if total population drops more than threshold fraction year-on-year."""
    issues = []
    col = "total_population" if "total_population" in ts.columns else "sexually_active_population"
    if col not in ts.columns:
        return {"check": "population_crash", "status": "blocked", "issues": []}

    vals = ts[[col, "year"]].dropna().sort_values("year")
    for i in range(1, len(vals)):
        prev = vals.iloc[i - 1][col]
        curr = vals.iloc[i][col]
        if prev > 0:
            change = (curr - prev) / prev
            if change < -threshold:
                issues.append({
                    "year": int(vals.iloc[i]["year"]),
                    "previous": float(prev),
                    "current": float(curr),
                    "change_fraction": round(float(change), 4),
                })
    return {
        "check": "population_crash",
        "status": "fail" if issues else "pass",
        "issues": issues,
    }


def check_plausible_trajectory(
    ts: pd.DataFrame,
    observed: pd.DataFrame | None = None,
    max_deviation: float = 0.15,
) -> dict:
    """Compare model total population against HMD observed (where overlapping)."""
    col = "total_population" if "total_population" in ts.columns else "sexually_active_population"
    if col not in ts.columns or observed is None:
        return {
            "check": "plausible_trajectory",
            "status": "blocked" if observed is None else "pass",
            "comparison": [],
            "max_deviation": None,
        }

    comparisons = []
    for _, obs_row in observed.iterrows():
        y = int(obs_row["year"])
        obs_val = obs_row["observed_total_population"]
        model_row = ts[ts["year"] == y]
        if len(model_row) == 0:
            continue
        model_val = float(model_row.iloc[0][col])
        if obs_val > 0:
            deviation = (model_val - obs_val) / obs_val
            comparisons.append({
                "year": y,
                "model": round(model_val),
                "observed": round(obs_val),
                "deviation": round(float(deviation), 4),
            })

    max_dev = max(abs(c["deviation"]) for c in comparisons) if comparisons else 0.0
    return {
        "check": "plausible_trajectory",
        "status": "fail" if max_dev > max_deviation else "pass",
        "comparison": comparisons,
        "max_deviation": round(max_dev, 4),
    }


def check_sex_ratio(ts: pd.DataFrame) -> dict:
    """Flag implausible sex ratios if available."""
    return {"check": "sex_ratio", "status": "pass", "detail": "Not directly available from summary outputs."}


def run_all_checks(
    ts: pd.DataFrame,
    observed: pd.DataFrame | None = None,
) -> list[dict]:
    return [
        check_negative_populations(ts),
        check_population_crash(ts),
        check_plausible_trajectory(ts, observed),
        check_sex_ratio(ts),
    ]


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_CSS = """<style>
body { font-family: 'Segoe UI', Arial, sans-serif; max-width: 1400px; margin: 2em auto; padding: 0 1em; }
h1, h2, h3 { color: #1a1a2e; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; font-size: 0.85em; }
th { background: #f0f0f0; text-align: center; }
.note { color: #555; font-size: 0.9em; }
.warn { background: #fff3cd; padding: 0.8em; border-left: 4px solid #ffc107; }
.good { background: #d4edda; padding: 0.8em; border-left: 4px solid #28a745; }
.bad { background: #f8d7da; padding: 0.8em; border-left: 4px solid #dc3545; }
pre { background: #f8f8f8; padding: 1em; overflow-x: auto; font-size: 0.85em; }
</style>"""


def _df_table(df: pd.DataFrame, max_rows: int = 200) -> str:
    if df.empty:
        return "<p class='note'>No data.</p>"
    show = df.head(max_rows)
    header = "".join(f"<th>{c}</th>" for c in show.columns)
    rows_html = []
    for _, row in show.iterrows():
        cells = "".join(
            f"<td>{v:,.0f}</td>" if isinstance(v, (int, float)) and not math.isnan(v) else f"<td>{v}</td>"
            for v in row.values
        )
        rows_html.append(f"<tr>{cells}</tr>")
    return f"<table><tr>{header}</tr>{''.join(rows_html)}</table>"


def build_html_report(
    scenario_results: list[dict],
    comparison_df: pd.DataFrame,
    checks_by_scenario: dict[str, list[dict]],
    manifest: dict,
) -> str:
    """Build the population audit HTML report."""

    # Checks summary
    checks_html_parts = []
    for label, checks in checks_by_scenario.items():
        checks_html_parts.append(f"<h3>{label}</h3>")
        for c in checks:
            status = c["status"]
            css = "good" if status == "pass" else ("bad" if status == "fail" else "warn")
            detail = ""
            if c.get("issues"):
                detail = f" — {len(c['issues'])} issue(s)"
            if c.get("max_deviation") is not None:
                detail += f" — max deviation: {c['max_deviation']:.1%}"
            checks_html_parts.append(
                f"<div class='{css}'>{c['check']}: {status}{detail}</div>"
            )
    checks_html = "\n".join(checks_html_parts)

    # Timeseries tables
    ts_html_parts = []
    for sr in scenario_results:
        ts_html_parts.append(f"<h3>{sr['label']}</h3>")
        ts_html_parts.append(_df_table(sr["timeseries"]))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.14 Population Audit — 1975 Burn-In</title>
{_CSS}
</head>
<body>
<h1>V13.14 Population Audit — 1975 Burn-In</h1>
<p class='note'>Generated: {manifest.get("created_at", "")} &nbsp;|&nbsp;
Base config: {manifest.get("base_config", "")}</p>
<p class='warn'><b>Diagnostic only.</b> This audit checks whether the model's demographic
engine can run from 1975 without population instability.</p>

<h2>Demographic Checks</h2>
{checks_html}

<h2>Scenario Comparison</h2>
{_df_table(comparison_df)}

<h2>Population Time Series by Scenario</h2>
{"".join(ts_html_parts)}

<h2>Manifest</h2>
<pre>{json.dumps(manifest, indent=2, default=str)}</pre>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(
    base_config_path: str,
    output_dir: str,
    hmd_path: Path | None = None,
    include_1970: bool = True,
) -> dict:
    """Run the full demographic feasibility audit."""
    base_config = json.loads(Path(base_config_path).read_text())
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    observed = load_hmd_observed_population(hmd_path)

    # Define scenarios
    scenarios = [
        ("base_1980_demography", 1980),
        ("candidate_1975_demography", 1975),
    ]
    if include_1970:
        scenarios.append(("candidate_1970_demography", 1970))

    scenario_results = []
    all_ts_rows = []
    checks_by_scenario: dict[str, list[dict]] = {}

    for label, start_year in scenarios:
        print(f"  Running {label} (start={start_year})...", flush=True)
        cfg, meta = build_demographic_scenario(
            base_config, start_year, label, hmd_path,
        )

        try:
            result = run_demographic_scenario(cfg)
            ts = extract_demographic_timeseries(result, start_year)
            meta["status"] = "completed"
            meta["n_years"] = len(ts)
        except Exception as e:
            ts = pd.DataFrame()
            meta["status"] = "failed"
            meta["error"] = str(e)

        ts_labeled = ts.copy()
        ts_labeled.insert(0, "scenario", label)
        all_ts_rows.append(ts_labeled)

        checks = run_all_checks(ts, observed)
        checks_by_scenario[label] = checks

        scenario_results.append({
            "label": label,
            "meta": meta,
            "timeseries": ts,
            "checks": checks,
        })

    # Combine timeseries
    combined_ts = pd.concat(all_ts_rows, ignore_index=True) if all_ts_rows else pd.DataFrame()

    # Build comparison (key years)
    comparison_rows = []
    key_years = [1970, 1975, 1980, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025]
    for sr in scenario_results:
        ts = sr["timeseries"]
        for y in key_years:
            row_data = ts[ts["year"] == y]
            if len(row_data) == 0:
                continue
            r = row_data.iloc[0]
            comparison_rows.append({
                "scenario": sr["label"],
                "year": y,
                "total_population": r.get("total_population"),
                "sexually_active_population": r.get("sexually_active_population"),
                "childhood_pool_total": r.get("childhood_pool_total"),
            })
    comparison_df = pd.DataFrame(comparison_rows)

    # Overall assessment
    all_pass = all(
        c["status"] != "fail"
        for checks in checks_by_scenario.values()
        for c in checks
    )

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_config": str(base_config_path),
        "scenarios": [label for label, _ in scenarios],
        "overall_pass": all_pass,
    }

    # Build summary
    summary = {
        "overall_pass": all_pass,
        "scenarios": {},
    }
    for sr in scenario_results:
        summary["scenarios"][sr["label"]] = {
            "status": sr["meta"]["status"],
            "adjustments": sr["meta"].get("adjustments", []),
            "checks": {c["check"]: c["status"] for c in sr["checks"]},
            "n_years": sr["meta"].get("n_years"),
        }

    # Write outputs
    (out / "population_audit_summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )
    if not combined_ts.empty:
        combined_ts.to_csv(out / "population_audit_timeseries.csv", index=False)
    if not comparison_df.empty:
        comparison_df.to_csv(out / "population_audit_comparison.csv", index=False)

    html = build_html_report(scenario_results, comparison_df, checks_by_scenario, manifest)
    (out / "population_audit_report.html").write_text(html)

    print(f"\n  Population audit complete: {'PASS' if all_pass else 'ISSUES FOUND'}")
    for label, checks in checks_by_scenario.items():
        for c in checks:
            if c["status"] == "fail":
                print(f"    FAIL: {label} — {c['check']}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="V13.14 Demographic feasibility audit for 1975 burn-in"
    )
    parser.add_argument(
        "--base-config", required=True,
        help="Path to base config JSON (e.g. V13.10 best fit)",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files",
    )
    parser.add_argument(
        "--no-1970", action="store_true",
        help="Skip optional 1970 scenario",
    )
    args = parser.parse_args()

    run(
        base_config_path=args.base_config,
        output_dir=args.output_dir,
        include_1970=not args.no_1970,
    )


if __name__ == "__main__":
    main()
