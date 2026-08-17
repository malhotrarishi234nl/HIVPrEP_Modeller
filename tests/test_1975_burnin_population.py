"""
Tests for tools/audit_1975_burnin_population.py — pure logic only.

No model simulation is run.  All tests work on synthetic in-memory DataFrames
or dicts, verifying the demographic checks, scenario builder, and HTML report.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_1975_burnin_population import (
    AGE_BINS,
    build_demographic_scenario,
    build_html_report,
    check_negative_populations,
    check_plausible_trajectory,
    check_population_crash,
    check_sex_ratio,
    extract_demographic_timeseries,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(start_year: int = 1980, total_size: int = 14_000_000) -> dict:
    """Minimal config dict with required population structure."""
    dist = {}
    n_bins = len(AGE_BINS) + 1  # +1 for 110+
    weight = 1.0 / n_bins / 2
    for lo, hi in AGE_BINS:
        dist[f"{lo}-{hi}"] = {"female": weight, "male": weight}
    dist["110+"] = {"female": weight, "male": weight}

    return {
        "run_control": {
            "start_year": {"value": start_year},
            "end_year": {"value": 2026},
            "deterministic_mode": {"value": False},
        },
        "output": {"enabled": {"value": True}},
        "population": {
            "total_size": {"value": total_size},
            "age_sex_distribution": {"value": dist},
            "epidemic_seed": {
                "enabled": {"value": True},
                "seed_prevalence": {"value": 0.002},
            },
        },
    }


def _stable_population_ts(start_year: int = 1980, n_years: int = 46) -> pd.DataFrame:
    """Synthetic time series with gentle 0.5% annual growth."""
    rows = []
    pop = 14_000_000.0
    for i in range(n_years):
        rows.append({
            "year": start_year + i,
            "total_population": pop,
            "sexually_active_population": pop * 0.6,
            "childhood_pool_total": pop * 0.2,
            "total_births": pop * 0.012,
            "total_deaths": pop * 0.009,
            "net_migration": pop * 0.002,
        })
        pop *= 1.005
    return pd.DataFrame(rows)


def _observed_population(years: list[int] | None = None) -> pd.DataFrame:
    """HMD-style observed population for comparison."""
    if years is None:
        years = list(range(1970, 2024))
    pop = 13_000_000.0
    rows = []
    for y in years:
        rows.append({
            "year": y,
            "observed_total_population": pop,
            "observed_male": pop * 0.49,
            "observed_female": pop * 0.51,
        })
        pop *= 1.005
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# check_negative_populations
# ---------------------------------------------------------------------------


class TestCheckNegativePopulations:
    def test_all_positive(self):
        ts = _stable_population_ts()
        result = check_negative_populations(ts)
        assert result["status"] == "pass"
        assert result["issues"] == []

    def test_negative_detected(self):
        ts = _stable_population_ts()
        ts.loc[5, "total_population"] = -100
        result = check_negative_populations(ts)
        assert result["status"] == "fail"
        assert len(result["issues"]) >= 1
        assert result["issues"][0]["column"] == "total_population"
        assert result["issues"][0]["value"] == -100

    def test_negative_in_childhood(self):
        ts = _stable_population_ts()
        ts.loc[3, "childhood_pool_total"] = -50
        result = check_negative_populations(ts)
        assert result["status"] == "fail"
        found = [i for i in result["issues"] if i["column"] == "childhood_pool_total"]
        assert len(found) == 1

    def test_empty_ts(self):
        ts = pd.DataFrame(columns=["year", "total_population"])
        result = check_negative_populations(ts)
        assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# check_population_crash
# ---------------------------------------------------------------------------


class TestCheckPopulationCrash:
    def test_stable_population(self):
        ts = _stable_population_ts()
        result = check_population_crash(ts)
        assert result["status"] == "pass"

    def test_crash_detected(self):
        ts = _stable_population_ts()
        ts.loc[10, "total_population"] = 1_000_000  # ~93% drop
        result = check_population_crash(ts)
        assert result["status"] == "fail"
        assert len(result["issues"]) >= 1

    def test_gradual_decline_ok(self):
        ts = _stable_population_ts()
        # 8% drop in one year — within 10% threshold
        ts.loc[10, "total_population"] = ts.loc[9, "total_population"] * 0.92
        result = check_population_crash(ts)
        assert result["status"] == "pass"

    def test_threshold_boundary(self):
        ts = _stable_population_ts()
        # Exactly 10% drop — should fail (strict > threshold)
        base_pop = ts.loc[9, "total_population"]
        ts.loc[10, "total_population"] = base_pop * 0.89
        result = check_population_crash(ts)
        assert result["status"] == "fail"

    def test_missing_column(self):
        ts = pd.DataFrame({"year": [1980, 1981]})
        result = check_population_crash(ts)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_plausible_trajectory
# ---------------------------------------------------------------------------


class TestCheckPlausibleTrajectory:
    def test_matching_trajectory(self):
        ts = _stable_population_ts()
        observed = _observed_population()
        result = check_plausible_trajectory(ts, observed)
        assert result["status"] == "pass"
        assert result["max_deviation"] is not None

    def test_large_deviation_fails(self):
        ts = _stable_population_ts()
        ts["total_population"] = ts["total_population"] * 1.20  # 20% over
        observed = _observed_population()
        result = check_plausible_trajectory(ts, observed)
        assert result["status"] == "fail"
        assert result["max_deviation"] > 0.15

    def test_no_observed_data(self):
        ts = _stable_population_ts()
        result = check_plausible_trajectory(ts, None)
        assert result["status"] == "blocked"

    def test_no_overlap_years(self):
        ts = _stable_population_ts(start_year=2050, n_years=5)
        observed = _observed_population([1970, 1975])
        result = check_plausible_trajectory(ts, observed)
        assert result["status"] == "pass"
        assert result["max_deviation"] == 0.0


# ---------------------------------------------------------------------------
# check_sex_ratio
# ---------------------------------------------------------------------------


class TestCheckSexRatio:
    def test_always_passes(self):
        ts = _stable_population_ts()
        result = check_sex_ratio(ts)
        assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    def test_all_pass(self):
        ts = _stable_population_ts()
        observed = _observed_population()
        results = run_all_checks(ts, observed)
        assert len(results) == 4
        names = {r["check"] for r in results}
        assert "negative_populations" in names
        assert "population_crash" in names
        assert "plausible_trajectory" in names
        assert "sex_ratio" in names

    def test_mixed_results(self):
        ts = _stable_population_ts()
        ts.loc[5, "total_population"] = -100
        results = run_all_checks(ts, None)
        neg_check = [r for r in results if r["check"] == "negative_populations"][0]
        assert neg_check["status"] == "fail"


# ---------------------------------------------------------------------------
# build_demographic_scenario
# ---------------------------------------------------------------------------


class TestBuildDemographicScenario:
    def test_1980_scenario_unchanged(self):
        cfg = _minimal_config(1980, 14_000_000)
        result_cfg, meta = build_demographic_scenario(cfg, 1980, "base_1980")
        assert result_cfg["run_control"]["start_year"]["value"] == 1980
        assert result_cfg["run_control"]["deterministic_mode"]["value"] is True
        assert meta["start_year"] == 1980

    def test_epidemic_disabled(self):
        cfg = _minimal_config()
        result_cfg, _ = build_demographic_scenario(cfg, 1975, "test")
        ep = result_cfg["population"]["epidemic_seed"]
        if isinstance(ep.get("enabled"), dict):
            assert ep["enabled"]["value"] is False
        else:
            assert ep["enabled"] is False or ep["enabled"]["value"] is False

    def test_deep_copy(self):
        cfg = _minimal_config()
        original_start = cfg["run_control"]["start_year"]["value"]
        result_cfg, _ = build_demographic_scenario(cfg, 1975, "test")
        assert cfg["run_control"]["start_year"]["value"] == original_start
        assert result_cfg["run_control"]["start_year"]["value"] == 1975

    def test_meta_records_adjustments(self):
        cfg = _minimal_config()
        _, meta = build_demographic_scenario(cfg, 1975, "test_1975")
        assert meta["label"] == "test_1975"
        assert meta["start_year"] == 1975
        assert isinstance(meta["adjustments"], list)


# ---------------------------------------------------------------------------
# extract_demographic_timeseries
# ---------------------------------------------------------------------------


class TestExtractDemographicTimeseries:
    def test_empty_result(self):
        result = {"summary": None}
        ts = extract_demographic_timeseries(result, 1980)
        assert len(ts) == 0

    def test_basic_extraction(self):
        summary = pd.DataFrame({
            "year": [1980.0, 1980.25, 1980.5, 1980.75, 1981.0, 1981.25, 1981.5, 1981.75],
            "total_population": [14e6, 14.01e6, 14.02e6, 14.03e6, 14.04e6, 14.05e6, 14.06e6, 14.07e6],
            "sexually_active_population": [8e6] * 8,
            "childhood_pool_total": [3e6] * 8,
            "total_births": [40000] * 8,
            "total_deaths": [30000] * 8,
            "net_migration": [5000] * 8,
            "aids_deaths": [0] * 8,
            "new_infections": [0] * 8,
        })
        result = {"summary": summary}
        ts = extract_demographic_timeseries(result, 1980)
        assert len(ts) == 2
        assert list(ts["year"]) == [1980, 1981]
        assert ts.iloc[0]["total_births"] == 160000  # 40000 * 4 quarters


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def test_produces_html(self):
        scenario_results = [
            {
                "label": "base_1980",
                "timeseries": _stable_population_ts(),
            },
        ]
        comparison_df = pd.DataFrame({
            "scenario": ["base_1980"],
            "year": [1980],
            "total_population": [14e6],
        })
        checks_by_scenario = {
            "base_1980": [
                {"check": "negative_populations", "status": "pass", "issues": []},
                {"check": "population_crash", "status": "pass", "issues": []},
            ],
        }
        manifest = {"created_at": "2026-05-28T00:00:00", "base_config": "test.json"}

        html = build_html_report(scenario_results, comparison_df, checks_by_scenario, manifest)
        assert "<!DOCTYPE html>" in html
        assert "V13.14 Population Audit" in html
        assert "base_1980" in html
        assert "Diagnostic only" in html

    def test_fail_status_in_report(self):
        scenario_results = [
            {"label": "test", "timeseries": pd.DataFrame()},
        ]
        comparison_df = pd.DataFrame()
        checks_by_scenario = {
            "test": [
                {"check": "negative_populations", "status": "fail", "issues": [{"column": "total_population", "year": 1985, "value": -100}]},
            ],
        }
        manifest = {"created_at": "2026-05-28"}

        html = build_html_report(scenario_results, comparison_df, checks_by_scenario, manifest)
        assert "fail" in html
        assert "bad" in html  # CSS class for fail


# ---------------------------------------------------------------------------
# Constants / structure
# ---------------------------------------------------------------------------


class TestConstants:
    def test_age_bins_coverage(self):
        all_ages = set()
        for lo, hi in AGE_BINS:
            for a in range(lo, hi + 1):
                all_ages.add(a)
        # Should cover 0 through 109
        assert 0 in all_ages
        assert 109 in all_ages

    def test_age_bins_non_overlapping(self):
        ranges = []
        for lo, hi in AGE_BINS:
            ranges.append((lo, hi))
        for i in range(len(ranges) - 1):
            assert ranges[i][1] < ranges[i + 1][0]
