#!/usr/bin/env python3
"""
V13.13 Extreme deterministic probes.

Runs a small controlled set of "can the model do it at all?" experiments by
overriding specific parameters in a base best-fit config and running in
deterministic mode.

These are not fits and not candidate production configurations — they are
mechanical probes to determine the model's capacity envelope.

Diagnostic only — does not modify production configs, gates, or acceptance
logic.

Usage
-----
python3 tools/run_extreme_deterministic_probes.py \\
    --base-config outputs/calibration/.../abc_smc_best_fit_rank_001.json \\
    --output-dir outputs/calibration/v13_13_closure_audit/extreme_probes
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

from calib_extractor import extract_annual_outputs
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from config_utils import extract_value
from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025
SIM_END_EXCLUSIVE = CALIB_END_YEAR + 1

CONFIG_PATHS = {
    "aids_mortality": "transmission_model.disease.mortality.aids.value",
    "chronic_to_aids": "transmission_model.disease.progression.chronic_to_aids.value",
    "chronic_to_aids_on_art": "transmission_model.disease.progression.chronic_to_aids_on_art.value",
    "art_mortality_reduction": "transmission_model.disease.advanced.art_mortality_reduction.value",
    "art_init_1996": "interventions.art.schedule.initiation.value.1996",
    "art_init_2000": "interventions.art.schedule.initiation.value.2000",
    "art_init_2015": "interventions.art.schedule.initiation.value.2015",
    "art_init_2020": "interventions.art.schedule.initiation.value.2020",
    "diagnosis_1985": "transmission_model.disease.diagnosis.schedule.value.1985",
    "diagnosis_1990": "transmission_model.disease.diagnosis.schedule.value.1990",
    "diagnosis_1997": "transmission_model.disease.diagnosis.schedule.value.1997",
    "diagnosis_2015": "transmission_model.disease.diagnosis.schedule.value.2015",
    "seed_chronic_share": "population.epidemic_seed.stage_distribution.value.Chronic",
    "seed_aids_share": "population.epidemic_seed.stage_distribution.value.AIDS",
    "start_year": "run_control.start_year.value",
}


# ---------------------------------------------------------------------------
# Probe definitions
# ---------------------------------------------------------------------------


def _probe_definitions() -> list[dict]:
    """Return the set of probe specifications."""
    return [
        {
            "name": "baseline_v13_10_best",
            "description": "Baseline — no overrides, deterministic mode only.",
            "overrides": {},
        },
        {
            "name": "aids_mortality_high",
            "description": "AIDS mortality pushed to 0.70 (stress-test ceiling).",
            "overrides": {"aids_mortality": 0.70},
        },
        {
            "name": "art_weak_early",
            "description": "Weaken early ART: low initiation 1996-2000, reduce ART mortality protection.",
            "overrides": {
                "art_init_1996": 0.02,
                "art_init_2000": 0.10,
                "art_mortality_reduction": 0.50,
            },
        },
        {
            "name": "progression_high",
            "description": "High chronic-to-AIDS progression (0.15).",
            "overrides": {"chronic_to_aids": 0.15},
        },
        {
            "name": "seed_aids_high",
            "description": "High AIDS share in seed (0.20), chronic 0.50, acute 0.30.",
            "overrides": {
                "seed_aids_share": 0.20,
                "seed_chronic_share": 0.50,
            },
        },
        {
            "name": "combined_aids_capacity",
            "description": (
                "Maximum AIDS death generation: high mortality + high progression "
                "+ high seed AIDS + weak ART."
            ),
            "overrides": {
                "aids_mortality": 0.70,
                "chronic_to_aids": 0.15,
                "seed_aids_share": 0.20,
                "seed_chronic_share": 0.50,
                "art_init_1996": 0.02,
                "art_init_2000": 0.10,
                "art_mortality_reduction": 0.50,
            },
        },
        {
            "name": "diagnosis_capacity",
            "description": "High diagnosis schedule within stress-test bounds.",
            "overrides": {
                "diagnosis_1985": 0.40,
                "diagnosis_1990": 0.60,
                "diagnosis_1997": 0.80,
                "diagnosis_2015": 0.90,
            },
        },
        {
            "name": "combined_diagnosis_and_aids",
            "description": "High diagnosis + high AIDS death capacity — key mechanical test.",
            "overrides": {
                "aids_mortality": 0.70,
                "chronic_to_aids": 0.15,
                "seed_aids_share": 0.15,
                "seed_chronic_share": 0.50,
                "art_init_1996": 0.02,
                "art_init_2000": 0.10,
                "art_mortality_reduction": 0.50,
                "diagnosis_1985": 0.40,
                "diagnosis_1990": 0.60,
                "diagnosis_1997": 0.80,
                "diagnosis_2015": 0.90,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Config manipulation
# ---------------------------------------------------------------------------


def _safe_set(config: dict, path: str, value: Any) -> bool:
    """Attempt to set a nested config value. Returns False if path doesn't exist."""
    from run_lhs_calibration import parse_config_path
    tokens = parse_config_path(path)
    if not tokens:
        return False
    obj = config
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(obj, list) or token >= len(obj):
                return False
            obj = obj[token]
        else:
            if not isinstance(obj, dict) or token not in obj:
                return False
            obj = obj[token]
    final = tokens[-1]
    if isinstance(final, int):
        if not isinstance(obj, list) or final >= len(obj):
            return False
        obj[final] = value
    else:
        if not isinstance(obj, dict) or final not in obj:
            return False
        obj[final] = value
    return True


def prepare_probe_config(base_config: dict, overrides: dict) -> dict:
    """Deep-copy base config, apply overrides, force deterministic mode."""
    cfg = copy.deepcopy(base_config)

    # Force deterministic + calibration time range + no file output
    _safe_set(cfg, "run_control.deterministic_mode.value", True)
    _safe_set(cfg, "run_control.end_year.value", SIM_END_EXCLUSIVE)
    cfg.setdefault("output", {})["enabled"] = {"value": False}
    cfg["output"]["terminal_summary"] = {"value": False}

    # Handle seed stage distribution overrides
    if "seed_aids_share" in overrides or "seed_chronic_share" in overrides:
        chronic = overrides.get("seed_chronic_share")
        aids = overrides.get("seed_aids_share")
        ep_seed = cfg.get("population", {}).get("epidemic_seed", {})
        stage_dist = ep_seed.get("stage_distribution", {}).get("value", {})

        if chronic is None:
            chronic = stage_dist.get("Chronic", 0.3)
            if isinstance(chronic, dict):
                chronic = extract_value(chronic)
        if aids is None:
            aids = stage_dist.get("AIDS", 0.05)
            if isinstance(aids, dict):
                aids = extract_value(aids)

        acute = round(1.0 - float(chronic) - float(aids), 10)
        if acute < 0:
            acute = 0.0
            chronic = round(1.0 - float(aids), 10)

        cfg.setdefault("population", {}).setdefault("epidemic_seed", {})["stage_distribution"] = {
            "value": {
                "Acute": acute,
                "Chronic": float(chronic),
                "AIDS": float(aids),
            },
        }

    # Apply remaining overrides via config paths
    failed_overrides = []
    for param_name, value in overrides.items():
        if param_name in ("seed_aids_share", "seed_chronic_share"):
            continue
        path = CONFIG_PATHS.get(param_name)
        if path is None:
            failed_overrides.append(f"{param_name}: no config path")
            continue
        if not _safe_set(cfg, path, float(value)):
            failed_overrides.append(f"{param_name}: path {path} not found")

    return cfg, failed_overrides


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------


def run_probe(config: dict, seed: int = 42) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(config, seed=seed)
        result = engine.run(run_number=1)
    return result


# ---------------------------------------------------------------------------
# Metrics extraction
# ---------------------------------------------------------------------------


def extract_probe_metrics(
    result: dict,
    targets: list | None = None,
) -> dict:
    """Extract summary metrics from a probe run."""
    outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)

    metrics: dict[str, Any] = {}
    metrics["collapsed"] = False
    metrics["error"] = None

    # Diagnosis ratio
    if "new_diagnoses" in outputs and targets:
        target_diag = _find_target_value(targets, "new_diagnoses", 2024)
        model_diag = outputs["new_diagnoses"].get(2024)
        if target_diag and model_diag is not None:
            metrics["total_diagnosis_ratio_2024"] = round(model_diag / target_diag, 4)

    # MSM diagnosis ratio
    if "new_diagnoses_msm" in outputs and targets:
        target_msm = _find_target_value(targets, "new_diagnoses_msm", 2024)
        model_msm = outputs["new_diagnoses_msm"].get(2024)
        if target_msm and model_msm is not None:
            metrics["msm_diagnosis_ratio_2024"] = round(model_msm / target_msm, 4)

    # PLHIV recent MAPE
    if "PLHIV" in outputs and targets:
        mape = _compute_recent_mape(outputs, targets, "PLHIV", 2018, 2023)
        if mape is not None:
            metrics["plhiv_recent_mape"] = round(mape, 4)

    # ART recent MAPE
    if "on_art_stock" in outputs and targets:
        mape = _compute_recent_mape(outputs, targets, "on_art_stock", 2018, 2023)
        if mape is not None:
            metrics["art_recent_mape"] = round(mape, 4)

    # Incidence ratio
    if "new_infections" in outputs and targets:
        ratios = _compute_recent_ratios(outputs, targets, "new_infections", 2018, 2023)
        if ratios:
            metrics["incidence_recent_median_ratio"] = round(
                float(np.median(ratios)), 4
            )

    # AIDS death bounds fraction
    if "aids_deaths" in outputs and targets:
        bf = _compute_bounds_fraction(outputs, targets, "aids_deaths")
        if bf is not None:
            metrics["aids_death_bounds_fraction"] = round(bf, 4)

    # AIDS death peak
    if "aids_deaths" in outputs:
        years = sorted(outputs["aids_deaths"].keys())
        vals = [outputs["aids_deaths"][y] for y in years]
        if vals:
            peak_idx = int(np.argmax(vals))
            metrics["peak_aids_deaths_year"] = years[peak_idx]
            metrics["peak_aids_deaths_value"] = round(vals[peak_idx], 2)

    # Cumulative AIDS deaths 1990-2005
    if "aids_deaths" in outputs:
        cumul = sum(
            v for y, v in outputs["aids_deaths"].items()
            if 1990 <= y <= 2005
        )
        metrics["aids_deaths_cumul_1990_2005"] = round(cumul, 2)

    # Target cumulative AIDS deaths 1990-2005
    if targets:
        target_cumul = _cumulative_target(targets, "aids_deaths", 1990, 2005)
        if target_cumul is not None:
            metrics["target_aids_deaths_cumul_1990_2005"] = round(target_cumul, 2)

    # MSM diagnosis historical MAPE
    if "new_diagnoses_msm" in outputs and targets:
        mape = _compute_recent_mape(outputs, targets, "new_diagnoses_msm", 1996, 2015)
        if mape is not None:
            metrics["msm_diag_hist_mape"] = round(mape, 4)

    # AIDS cases max ratio
    if "new_aids_cases" in outputs and targets:
        max_ratio = _compute_max_ratio(outputs, targets, "new_aids_cases")
        if max_ratio is not None:
            metrics["aids_cases_max_ratio"] = round(max_ratio, 4)

    return metrics


def extract_probe_timeseries(result: dict) -> pd.DataFrame:
    """Extract annual time series from a probe run."""
    outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
    rows = []
    years = sorted(
        set().union(*(outputs[k].keys() for k in outputs if isinstance(outputs[k], dict)))
    )
    for y in years:
        row = {"year": y}
        for indicator in [
            "PLHIV", "new_infections", "aids_deaths", "new_diagnoses",
            "new_diagnoses_msm", "new_aids_cases", "on_art_stock",
        ]:
            if indicator in outputs:
                row[indicator] = outputs[indicator].get(y)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Target helpers
# ---------------------------------------------------------------------------


def _target_indicator(t: dict) -> str:
    return t.get("indicator", t.get("model_output", ""))


def _target_value(t: dict) -> float | None:
    return t.get("value", t.get("estimate"))


def _find_target_value(targets: list, indicator: str, year: int) -> float | None:
    for t in targets:
        if _target_indicator(t) == indicator and t.get("year") == year:
            return _target_value(t)
    return None


def _compute_recent_mape(
    outputs: dict, targets: list, indicator: str,
    start: int, end: int,
) -> float | None:
    errors = []
    for t in targets:
        if _target_indicator(t) != indicator:
            continue
        y = t.get("year")
        if y is None or not (start <= y <= end):
            continue
        target_val = _target_value(t)
        model_val = outputs.get(indicator, {}).get(y)
        if target_val and model_val is not None and target_val > 0:
            errors.append(abs(model_val - target_val) / target_val)
    return float(np.mean(errors)) * 100 if errors else None


def _compute_recent_ratios(
    outputs: dict, targets: list, indicator: str,
    start: int, end: int,
) -> list[float]:
    ratios = []
    for t in targets:
        if _target_indicator(t) != indicator:
            continue
        y = t.get("year")
        if y is None or not (start <= y <= end):
            continue
        target_val = _target_value(t)
        model_val = outputs.get(indicator, {}).get(y)
        if target_val and model_val is not None and target_val > 0:
            ratios.append(model_val / target_val)
    return ratios


def _compute_bounds_fraction(
    outputs: dict, targets: list, indicator: str,
) -> float | None:
    n_total = 0
    n_within = 0
    for t in targets:
        if _target_indicator(t) != indicator:
            continue
        y = t.get("year")
        model_val = outputs.get(indicator, {}).get(y)
        if model_val is None:
            continue
        lower = t.get("lower_bound", t.get("lower"))
        upper = t.get("upper_bound", t.get("upper"))
        if lower is not None and upper is not None:
            n_total += 1
            if lower <= model_val <= upper:
                n_within += 1
    return n_within / n_total if n_total > 0 else None


def _cumulative_target(
    targets: list, indicator: str, start: int, end: int,
) -> float | None:
    total = 0.0
    found = False
    for t in targets:
        if _target_indicator(t) != indicator:
            continue
        y = t.get("year")
        tv = _target_value(t)
        if y is not None and start <= y <= end and tv is not None:
            total += tv
            found = True
    return total if found else None


def _compute_max_ratio(
    outputs: dict, targets: list, indicator: str,
) -> float | None:
    max_ratio = 0.0
    found = False
    for t in targets:
        if _target_indicator(t) != indicator:
            continue
        y = t.get("year")
        target_val = _target_value(t)
        model_val = outputs.get(indicator, {}).get(y)
        if target_val and model_val is not None and target_val > 0:
            ratio = model_val / target_val
            max_ratio = max(max_ratio, ratio)
            found = True
    return max_ratio if found else None


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


def _interpret_probes(summary_df: pd.DataFrame) -> list[str]:
    lines = []
    if summary_df.empty:
        return ["No probe results to interpret."]

    combined_aids = summary_df[summary_df["probe"] == "combined_aids_capacity"]
    if len(combined_aids) > 0:
        peak = combined_aids.iloc[0].get("peak_aids_deaths_value", 0)
        if peak < 50:
            lines.append(
                f"STRUCTURAL CONCERN: combined_aids_capacity probe peak AIDS deaths "
                f"= {peak:.0f}. Even with extreme parameters, the engine cannot "
                f"generate substantial AIDS deaths. This strongly suggests a code/"
                f"accounting issue or missing historical burn-in."
            )
        else:
            lines.append(
                f"combined_aids_capacity probe generated peak AIDS deaths = {peak:.0f}. "
                f"The engine CAN produce AIDS deaths under extreme parameters."
            )

    combined_both = summary_df[summary_df["probe"] == "combined_diagnosis_and_aids"]
    if len(combined_both) > 0:
        row = combined_both.iloc[0]
        diag_ratio = row.get("total_diagnosis_ratio_2024")
        aids_bf = row.get("aids_death_bounds_fraction")
        if diag_ratio is not None and aids_bf is not None:
            if diag_ratio < 0.7 or aids_bf < 0.2:
                lines.append(
                    f"combined_diagnosis_and_aids: diagnosis ratio = {diag_ratio}, "
                    f"AIDS bounds fraction = {aids_bf}. Cannot produce both — "
                    f"this suggests a structural tradeoff."
                )
            else:
                lines.append(
                    f"combined_diagnosis_and_aids: diagnosis ratio = {diag_ratio}, "
                    f"AIDS bounds fraction = {aids_bf}. Both metrics are reasonable — "
                    f"capacity exists and search may have been insufficient."
                )

    baseline = summary_df[summary_df["probe"] == "baseline_v13_10_best"]
    if len(baseline) > 0:
        peak = baseline.iloc[0].get("peak_aids_deaths_value", 0)
        lines.append(f"Baseline probe peak AIDS deaths = {peak:.0f}.")

    return lines if lines else ["No specific interpretation — review metrics."]


def build_html_report(
    summary_df: pd.DataFrame,
    timeseries_df: pd.DataFrame,
    interpretation: list[str],
    manifest: dict,
) -> str:
    interp_html = "\n".join(
        f"<div class='{'bad' if 'STRUCTURAL' in line or 'Cannot' in line else 'good'}'>{line}</div>"
        for line in interpretation
    )

    def _df_table(df, max_rows=200):
        if df.empty:
            return "<p class='note'>No data.</p>"
        show = df.head(max_rows)
        header = "".join(f"<th>{c}</th>" for c in show.columns)
        rows = []
        for _, row in show.iterrows():
            cells = "".join(f"<td>{v}</td>" for v in row.values)
            rows.append(f"<tr>{cells}</tr>")
        return f"<table><tr>{header}</tr>{''.join(rows)}</table>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.13 Extreme Deterministic Probes</title>
{_CSS}
</head>
<body>
<h1>V13.13 Extreme Deterministic Probes</h1>
<p class='note'>Generated: {manifest.get("created_at", "")} &nbsp;|&nbsp;
Base config: {manifest.get("base_config", "")}</p>
<p class='warn'><b>Diagnostic only.</b> These are mechanical probes, not production
configurations. Parameter values may be academically indefensible.</p>

<h2>Interpretation</h2>
{interp_html}

<h2>Probe Summary</h2>
{_df_table(summary_df)}

<h2>Annual Time Series (All Probes)</h2>
{_df_table(timeseries_df, max_rows=500)}

<h2>Manifest</h2>
<pre>{json.dumps(manifest, indent=2)}</pre>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(
    base_config_path: str,
    output_dir: str,
    probes: list[dict] | None = None,
) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    base_path = Path(base_config_path)
    if not base_path.exists():
        raise FileNotFoundError(f"Base config not found: {base_config_path}")
    base_config = json.loads(base_path.read_text(encoding="utf-8"))

    # Load targets for metric calculation
    targets = None
    try:
        wb_path = default_disease_workbook_path()
        map_path = default_target_mapping_path()
        if Path(wb_path).exists() and Path(map_path).exists():
            targets = load_disease_targets(wb_path, map_path)
    except Exception:
        pass

    if probes is None:
        probes = _probe_definitions()

    summary_rows = []
    all_timeseries = []

    for probe in probes:
        name = probe["name"]
        desc = probe["description"]
        overrides = probe["overrides"]
        print(f"  Running probe: {name} ...")

        try:
            cfg, failed = prepare_probe_config(base_config, overrides)
            result = run_probe(cfg)
            metrics = extract_probe_metrics(result, targets)
            ts = extract_probe_timeseries(result)
            ts["probe"] = name
            all_timeseries.append(ts)

            row = {
                "probe": name,
                "description": desc,
                **metrics,
            }
            if failed:
                row["failed_overrides"] = "; ".join(failed)
            summary_rows.append(row)

        except Exception as exc:
            summary_rows.append({
                "probe": name,
                "description": desc,
                "collapsed": True,
                "error": str(exc),
            })

    summary_df = pd.DataFrame(summary_rows)
    timeseries_df = (
        pd.concat(all_timeseries, ignore_index=True)
        if all_timeseries
        else pd.DataFrame()
    )

    interpretation = _interpret_probes(summary_df)

    manifest_probes = []
    for p in probes:
        manifest_probes.append({
            "name": p["name"],
            "description": p["description"],
            "overrides": {k: str(v) for k, v in p["overrides"].items()},
        })

    manifest = {
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "base_config": str(base_config_path),
        "n_probes": len(probes),
        "n_completed": sum(1 for r in summary_rows if not r.get("collapsed")),
        "n_failed": sum(1 for r in summary_rows if r.get("collapsed")),
        "probes": manifest_probes,
        "targets_loaded": targets is not None,
    }

    (out / "extreme_probe_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    summary_df.to_csv(out / "extreme_probe_summary.csv", index=False)
    timeseries_df.to_csv(out / "extreme_probe_timeseries.csv", index=False)

    html = build_html_report(summary_df, timeseries_df, interpretation, manifest)
    (out / "extreme_probe_report.html").write_text(html, encoding="utf-8")

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V13.13 extreme deterministic probes."
    )
    parser.add_argument(
        "--base-config", required=True,
        help="Path to a best-fit JSON config to use as the base.",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = run(
        base_config_path=args.base_config,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
