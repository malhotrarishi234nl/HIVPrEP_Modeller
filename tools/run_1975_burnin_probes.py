#!/usr/bin/env python3
"""
V13.14 — 1975 burn-in deterministic disease probes.

Tests whether starting the simulation in 1975 (with various seed years)
improves AIDS-death timing without destroying recent diagnosis/PLHIV/ART fit.

Diagnostic only — does not modify production configs, gates, or targets.

Usage
-----
python3 tools/run_1975_burnin_probes.py \
    --base-config configs/v13_14_1975_burnin_base.json \
    --output-dir outputs/calibration/v13_14_1975_burnin_diagnostic/probes
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
    "seed_year": "population.epidemic_seed.seed_year.value",
    "seed_prevalence": "population.epidemic_seed.seed_prevalence.value",
}

# HMD-based population data for year adjustments
HMD_YEAR_POPULATIONS = {
    1970: 13032335,
    1975: 13598912,
    1980: 14090318,
}


# ---------------------------------------------------------------------------
# Probe definitions
# ---------------------------------------------------------------------------


def _probe_definitions() -> list[dict]:
    """Return the 1975 burn-in probe set."""
    return [
        {
            "name": "baseline_1980_current",
            "description": "Control: V13.10 best fit at 1980 start, current seed year 1980.",
            "overrides": {
                "start_year": 1980,
                "seed_year": 1980.0,
            },
            "use_1980_demography": True,
        },
        {
            "name": "burnin_1975_seed_1975",
            "description": "1975 start, epidemic seed fires in 1975.",
            "overrides": {
                "start_year": 1975,
                "seed_year": 1975.0,
            },
        },
        {
            "name": "burnin_1975_seed_1977",
            "description": "1975 start, epidemic seed fires in 1977 (intermediate timing).",
            "overrides": {
                "start_year": 1975,
                "seed_year": 1977.0,
            },
        },
        {
            "name": "burnin_1975_seed_1980",
            "description": (
                "1975 start, seed at 1980. Control: isolates demographic burn-in "
                "from disease burn-in."
            ),
            "overrides": {
                "start_year": 1975,
                "seed_year": 1980.0,
            },
        },
        {
            "name": "burnin_1975_seed_1975_chronic_heavy",
            "description": (
                "1975 start, seed 1975, high chronic share (0.40), low AIDS (0.02). "
                "Within V13.10 bounds."
            ),
            "overrides": {
                "start_year": 1975,
                "seed_year": 1975.0,
                "seed_chronic_share": 0.40,
                "seed_aids_share": 0.02,
            },
        },
        {
            "name": "burnin_1975_seed_1975_aids_light",
            "description": (
                "1975 start, seed 1975, very small AIDS seed (0.005), moderate chronic (0.25). "
                "Intended to avoid early death spike."
            ),
            "overrides": {
                "start_year": 1975,
                "seed_year": 1975.0,
                "seed_aids_share": 0.005,
                "seed_chronic_share": 0.25,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Config manipulation
# ---------------------------------------------------------------------------


def _safe_set(config: dict, path: str, value: Any) -> bool:
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


def prepare_probe_config(
    base_config: dict,
    overrides: dict,
    use_1980_demography: bool = False,
) -> tuple[dict, list[str]]:
    """Deep-copy base config, apply overrides, force deterministic mode."""
    cfg = copy.deepcopy(base_config)

    _safe_set(cfg, "run_control.deterministic_mode.value", True)
    _safe_set(cfg, "run_control.end_year.value", SIM_END_EXCLUSIVE)
    cfg.setdefault("output", {})["enabled"] = {"value": False}
    cfg["output"]["terminal_summary"] = {"value": False}

    # Handle 1980 demography restoration for baseline probe
    if use_1980_demography:
        from tools.audit_1975_burnin_population import (
            _age_sex_distribution_for_year,
            _total_population_for_year,
        )
        dist_1980 = _age_sex_distribution_for_year(1980)
        if dist_1980:
            cfg["population"]["age_sex_distribution"]["value"] = dist_1980
        total_1980 = _total_population_for_year(1980)
        if total_1980:
            cfg["population"]["total_size"]["value"] = total_1980

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

    # Apply remaining overrides
    failed = []
    for param_name, value in overrides.items():
        if param_name in ("seed_aids_share", "seed_chronic_share", "use_1980_demography"):
            continue
        path = CONFIG_PATHS.get(param_name)
        if path is None:
            failed.append(f"{param_name}: no config path")
            continue
        if not _safe_set(cfg, path, value):
            failed.append(f"{param_name}: path {path} not found")

    return cfg, failed


# ---------------------------------------------------------------------------
# Simulation
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


def detect_pre_1990_spike(outputs: dict, threshold: float = 50.0) -> dict:
    """Detect whether AIDS deaths have a pre-1990 spike (artifact of early seeding)."""
    deaths = outputs.get("aids_deaths", {})
    pre_1990 = {y: v for y, v in deaths.items() if y < 1990}
    post_1990 = {y: v for y, v in deaths.items() if 1990 <= y <= 2005}

    if not pre_1990:
        return {"has_spike": False, "pre_1990_peak": 0, "pre_1990_peak_year": None}

    peak_year = max(pre_1990, key=pre_1990.get)
    peak_val = pre_1990[peak_year]
    post_mean = np.mean(list(post_1990.values())) if post_1990 else 0

    return {
        "has_spike": peak_val > threshold and (post_mean == 0 or peak_val > 2 * post_mean),
        "pre_1990_peak": round(peak_val, 2),
        "pre_1990_peak_year": peak_year,
        "post_1990_mean": round(float(post_mean), 2),
    }


def extract_probe_metrics(
    result: dict,
    targets: list | None = None,
    start_year: int = 1975,
) -> dict:
    """Extract summary metrics from a probe run."""
    outputs = extract_annual_outputs(result, start_year, CALIB_END_YEAR)

    metrics: dict[str, Any] = {}
    metrics["collapsed"] = False
    metrics["error"] = None

    # AIDS death peak
    if "aids_deaths" in outputs:
        years = sorted(outputs["aids_deaths"].keys())
        vals = [outputs["aids_deaths"][y] for y in years]
        if vals:
            peak_idx = int(np.argmax(vals))
            metrics["peak_aids_deaths_year"] = years[peak_idx]
            metrics["peak_aids_deaths_value"] = round(vals[peak_idx], 2)

    # Target AIDS death peak
    if targets:
        aids_targets = [
            (t.get("year"), _target_value(t))
            for t in targets
            if _target_indicator(t) == "aids_deaths" and _target_value(t) is not None
        ]
        if aids_targets:
            target_peak = max(aids_targets, key=lambda x: x[1])
            metrics["target_aids_deaths_peak_year"] = target_peak[0]
            metrics["target_aids_deaths_peak_value"] = round(target_peak[1], 2)

    # AIDS death bounds fraction
    if "aids_deaths" in outputs and targets:
        bf = _compute_bounds_fraction(outputs, targets, "aids_deaths")
        if bf is not None:
            metrics["aids_death_bounds_fraction"] = round(bf, 4)

    # Cumulative AIDS deaths 1990-2005
    if "aids_deaths" in outputs:
        cumul = sum(v for y, v in outputs["aids_deaths"].items() if 1990 <= y <= 2005)
        metrics["aids_deaths_cumul_1990_2005"] = round(cumul, 2)

    # Target cumulative AIDS deaths 1990-2005
    if targets:
        target_cumul = _cumulative_target(targets, "aids_deaths", 1990, 2005)
        if target_cumul is not None:
            metrics["target_aids_deaths_cumul_1990_2005"] = round(target_cumul, 2)

    # Pre-1990 cumulative AIDS deaths
    if "aids_deaths" in outputs:
        pre_cumul = sum(v for y, v in outputs["aids_deaths"].items() if 1980 <= y < 1990)
        metrics["aids_deaths_cumul_1980_1989"] = round(pre_cumul, 2)

    # Pre-1990 spike detection
    if "aids_deaths" in outputs:
        spike = detect_pre_1990_spike(outputs)
        metrics["pre_1990_death_spike"] = spike["has_spike"]
        metrics["pre_1990_peak_deaths"] = spike["pre_1990_peak"]
        metrics["pre_1990_peak_year"] = spike["pre_1990_peak_year"]

    # Diagnosis ratios
    if "new_diagnoses" in outputs and targets:
        target_diag = _find_target_value(targets, "new_diagnoses", 2024)
        model_diag = outputs["new_diagnoses"].get(2024)
        if target_diag and model_diag is not None:
            metrics["total_diagnosis_ratio_2024"] = round(model_diag / target_diag, 4)

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
            metrics["incidence_recent_median_ratio"] = round(float(np.median(ratios)), 4)

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


def extract_probe_timeseries(result: dict, start_year: int = 1975) -> pd.DataFrame:
    """Extract annual time series from a probe run."""
    outputs = extract_annual_outputs(result, start_year, CALIB_END_YEAR)
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
# Interpretation
# ---------------------------------------------------------------------------


def _interpret_probes(summary_df: pd.DataFrame) -> list[str]:
    lines = []
    if summary_df.empty:
        return ["No probe results to interpret."]

    baseline = summary_df[summary_df["probe"] == "baseline_1980_current"]
    seed_1975 = summary_df[summary_df["probe"] == "burnin_1975_seed_1975"]
    seed_1977 = summary_df[summary_df["probe"] == "burnin_1975_seed_1977"]
    seed_1980 = summary_df[summary_df["probe"] == "burnin_1975_seed_1980"]

    # Baseline reference
    if len(baseline) > 0:
        b = baseline.iloc[0]
        lines.append(
            f"Baseline (1980 start): peak AIDS deaths = {b.get('peak_aids_deaths_value', 0):.0f} "
            f"in {b.get('peak_aids_deaths_year', '?')}, "
            f"cumul 1990-2005 = {b.get('aids_deaths_cumul_1990_2005', 0):.0f}."
        )

    # Compare seed timing
    for label, probe_df in [
        ("seed_1975", seed_1975),
        ("seed_1977", seed_1977),
        ("seed_1980 (demo-only burn-in)", seed_1980),
    ]:
        if len(probe_df) == 0:
            continue
        p = probe_df.iloc[0]
        peak = p.get("peak_aids_deaths_value", 0)
        peak_year = p.get("peak_aids_deaths_year", "?")
        cumul = p.get("aids_deaths_cumul_1990_2005", 0)
        spike = p.get("pre_1990_death_spike", False)
        lines.append(
            f"1975 burn-in {label}: peak = {peak:.0f} in {peak_year}, "
            f"cumul 1990-2005 = {cumul:.0f}"
            f"{', PRE-1990 SPIKE DETECTED' if spike else ''}."
        )

    # Key comparison: does 1975 seed improve over baseline?
    if len(baseline) > 0 and len(seed_1975) > 0:
        b_cumul = baseline.iloc[0].get("aids_deaths_cumul_1990_2005", 0)
        s_cumul = seed_1975.iloc[0].get("aids_deaths_cumul_1990_2005", 0)
        b_peak_year = baseline.iloc[0].get("peak_aids_deaths_year", 9999)
        s_peak_year = seed_1975.iloc[0].get("peak_aids_deaths_year", 9999)

        if s_cumul > b_cumul * 1.1 and 1990 <= s_peak_year <= 2000:
            lines.append(
                "PROMISING: 1975 seed improves 1990-2005 cumulative AIDS deaths "
                f"({s_cumul:.0f} vs baseline {b_cumul:.0f}) and peak timing is "
                f"in the 1990-2000 window."
            )
        elif s_cumul > b_cumul * 1.1:
            lines.append(
                f"PARTIAL: 1975 seed improves cumulative AIDS deaths ({s_cumul:.0f} "
                f"vs {b_cumul:.0f}) but peak timing ({s_peak_year}) may not be optimal."
            )
        else:
            lines.append(
                f"NO IMPROVEMENT: 1975 seed cumulative ({s_cumul:.0f}) does not "
                f"materially exceed baseline ({b_cumul:.0f})."
            )

    # Check if seed_1975 vs seed_1980 (both 1975 start) shows disease timing matters
    if len(seed_1975) > 0 and len(seed_1980) > 0:
        s75_cumul = seed_1975.iloc[0].get("aids_deaths_cumul_1990_2005", 0)
        s80_cumul = seed_1980.iloc[0].get("aids_deaths_cumul_1990_2005", 0)
        if abs(s75_cumul - s80_cumul) < max(s75_cumul, s80_cumul, 1) * 0.1:
            lines.append(
                "Seed timing (1975 vs 1980) has MINIMAL effect — "
                "extra demographic burn-in alone is not the mechanism."
            )
        else:
            lines.append(
                f"Seed timing MATTERS: seed_1975 cumul = {s75_cumul:.0f} vs "
                f"seed_1980 cumul = {s80_cumul:.0f}."
            )

    # Spike warnings
    for _, row in summary_df.iterrows():
        if row.get("pre_1990_death_spike"):
            lines.append(
                f"WARNING: {row['probe']} has pre-1990 AIDS death spike "
                f"(peak {row.get('pre_1990_peak_deaths', 0):.0f} in "
                f"{row.get('pre_1990_peak_year', '?')}). "
                f"This is a seed artifact, not genuine epidemic mass."
            )

    return lines if lines else ["No specific interpretation — review metrics."]


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


def build_html_report(
    summary_df: pd.DataFrame,
    timeseries_df: pd.DataFrame,
    interpretation: list[str],
    manifest: dict,
) -> str:
    interp_html = "\n".join(
        f"<div class='{'bad' if 'NO IMPROVEMENT' in line or 'SPIKE' in line or 'WARNING' in line else ('good' if 'PROMISING' in line else 'warn')}'>{line}</div>"
        for line in interpretation
    )

    def _df_table(df, max_rows=300):
        if df.empty:
            return "<p class='note'>No data.</p>"
        show = df.head(max_rows)
        header = "".join(f"<th>{c}</th>" for c in show.columns)
        rows = []
        for _, row in show.iterrows():
            cells = "".join(
                f"<td>{v:,.2f}</td>" if isinstance(v, float) else f"<td>{v}</td>"
                for v in row.values
            )
            rows.append(f"<tr>{cells}</tr>")
        return f"<table><tr>{header}</tr>{''.join(rows)}</table>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>V13.14 — 1975 Burn-In Probes</title>
{_CSS}
</head>
<body>
<h1>V13.14 — 1975 Burn-In Disease Probes</h1>
<p class='note'>Generated: {manifest.get("created_at", "")} &nbsp;|&nbsp;
Base config: {manifest.get("base_config", "")}</p>
<p class='warn'><b>Diagnostic only.</b> These are exploratory probes testing whether
a 1975 start year improves AIDS-death timing.</p>

<h2>Interpretation</h2>
{interp_html}

<h2>Probe Summary</h2>
{_df_table(summary_df)}

<h2>Annual Time Series (All Probes)</h2>
{_df_table(timeseries_df, max_rows=600)}

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
) -> dict:
    """Run the 1975 burn-in probe set."""
    base_config = json.loads(Path(base_config_path).read_text())
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Load targets
    try:
        targets = load_disease_targets(
            default_disease_workbook_path(),
            default_target_mapping_path(),
        )
    except Exception:
        targets = None

    probes = _probe_definitions()
    summary_rows = []
    all_ts = []

    for probe in probes:
        name = probe["name"]
        print(f"  Running probe: {name} ...", flush=True)

        use_1980 = probe.get("use_1980_demography", False)
        cfg, failed = prepare_probe_config(
            base_config, probe["overrides"], use_1980_demography=use_1980,
        )

        start_year = int(cfg["run_control"]["start_year"]["value"])

        if failed:
            print(f"    WARNING: failed overrides: {failed}")

        try:
            result = run_probe(cfg)
            metrics = extract_probe_metrics(result, targets, start_year=start_year)
            ts = extract_probe_timeseries(result, start_year=start_year)
        except Exception as e:
            metrics = {"collapsed": True, "error": str(e)}
            ts = pd.DataFrame()

        row = {"probe": name, **metrics}
        summary_rows.append(row)

        ts_labeled = ts.copy()
        ts_labeled.insert(0, "probe", name)
        all_ts.append(ts_labeled)

    summary_df = pd.DataFrame(summary_rows)
    timeseries_df = pd.concat(all_ts, ignore_index=True) if all_ts else pd.DataFrame()
    interpretation = _interpret_probes(summary_df)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_config": str(base_config_path),
        "n_probes": len(probes),
        "probes": [p["name"] for p in probes],
    }

    # Write outputs
    (out / "burnin_probe_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str)
    )
    summary_df.to_csv(out / "burnin_probe_summary.csv", index=False)
    if not timeseries_df.empty:
        timeseries_df.to_csv(out / "burnin_probe_timeseries.csv", index=False)

    html = build_html_report(summary_df, timeseries_df, interpretation, manifest)
    (out / "burnin_probe_report.html").write_text(html)

    print(f"\n  Burn-in probes complete. {len(probes)} probes run.")
    for line in interpretation:
        print(f"    {line}")

    return {
        "summary": summary_df.to_dict(orient="records"),
        "interpretation": interpretation,
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="V13.14 — 1975 burn-in deterministic disease probes"
    )
    parser.add_argument(
        "--base-config", required=True,
        help="Path to 1975 base config JSON",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files",
    )
    args = parser.parse_args()
    run(base_config_path=args.base_config, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
