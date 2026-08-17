"""
Tests for tools/run_1975_burnin_probes.py — pure logic only.

No model simulation is run.  All tests work on synthetic outputs dicts,
verifying probe definitions, metric extraction, spike detection,
interpretation, config preparation, and HTML report.
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

from tools.run_1975_burnin_probes import (
    CALIB_END_YEAR,
    CONFIG_PATHS,
    HMD_YEAR_POPULATIONS,
    _interpret_probes,
    _probe_definitions,
    build_html_report,
    detect_pre_1990_spike,
    extract_probe_metrics,
    extract_probe_timeseries,
    prepare_probe_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _synthetic_outputs(
    peak_year: int = 1995,
    peak_value: float = 571.0,
    start_year: int = 1975,
) -> dict:
    """Build synthetic model outputs with a gaussian-ish AIDS death peak."""
    outputs: dict = {}

    years = list(range(start_year, CALIB_END_YEAR + 1))

    # AIDS deaths: gaussian peak
    aids = {}
    for y in years:
        dist = abs(y - peak_year)
        aids[y] = peak_value * math.exp(-(dist ** 2) / (2 * 8 ** 2))
    outputs["aids_deaths"] = aids

    # PLHIV: logistic growth then plateau
    plhiv = {}
    for y in years:
        plhiv[y] = 25000 / (1 + math.exp(-0.3 * (y - 1995)))
    outputs["PLHIV"] = plhiv

    # New infections
    infections = {}
    for y in years:
        infections[y] = max(0, 2000 * math.exp(-0.05 * (y - 1990)))
    outputs["new_infections"] = infections

    # New diagnoses
    diag = {}
    for y in years:
        diag[y] = max(0, 1000 * math.exp(-0.02 * (y - 2000)))
    outputs["new_diagnoses"] = diag

    # MSM diagnoses
    msm_diag = {}
    for y in years:
        msm_diag[y] = max(0, 500 * math.exp(-0.02 * (y - 2000)))
    outputs["new_diagnoses_msm"] = msm_diag

    # ART stock
    art = {}
    for y in years:
        art[y] = 20000 / (1 + math.exp(-0.4 * (y - 2005))) if y >= 1996 else 0
    outputs["on_art_stock"] = art

    # AIDS cases
    aids_cases = {}
    for y in years:
        aids_cases[y] = aids.get(y, 0) * 1.2
    outputs["new_aids_cases"] = aids_cases

    return outputs


def _synthetic_targets() -> list[dict]:
    """Build synthetic calibration targets."""
    targets = []

    # AIDS death targets with bounds
    for y in range(1990, 2020):
        val = 571 * math.exp(-((y - 1995) ** 2) / (2 * 8 ** 2))
        targets.append({
            "indicator": "aids_deaths",
            "model_output": "aids_deaths",
            "year": y,
            "value": val,
            "lower_bound": val * 0.5,
            "upper_bound": val * 1.5,
        })

    # PLHIV targets
    for y in range(2018, 2024):
        targets.append({
            "indicator": "PLHIV",
            "model_output": "PLHIV",
            "year": y,
            "value": 24000,
        })

    # Diagnosis targets
    for y in [2024]:
        targets.append({
            "indicator": "new_diagnoses",
            "model_output": "new_diagnoses",
            "year": y,
            "value": 800,
        })
        targets.append({
            "indicator": "new_diagnoses_msm",
            "model_output": "new_diagnoses_msm",
            "year": y,
            "value": 400,
        })

    # ART targets
    for y in range(2018, 2024):
        targets.append({
            "indicator": "on_art_stock",
            "model_output": "on_art_stock",
            "year": y,
            "value": 19500,
        })

    # Infection targets
    for y in range(2018, 2024):
        targets.append({
            "indicator": "new_infections",
            "model_output": "new_infections",
            "year": y,
            "value": 500,
        })

    # MSM diagnosis historical
    for y in range(1996, 2016):
        targets.append({
            "indicator": "new_diagnoses_msm",
            "model_output": "new_diagnoses_msm",
            "year": y,
            "value": 450,
        })

    # AIDS cases
    for y in range(1990, 2018):
        targets.append({
            "indicator": "new_aids_cases",
            "model_output": "new_aids_cases",
            "year": y,
            "value": 600,
        })

    return targets


def _minimal_1975_config() -> dict:
    """Minimal config for prepare_probe_config."""
    dist = {}
    for lo_hi in [(0, 4), (5, 9), (10, 14), (15, 19), (20, 24), (25, 29),
                   (30, 34), (35, 39), (40, 44), (45, 49), (50, 54),
                   (55, 59), (60, 64), (65, 69), (70, 74), (75, 79),
                   (80, 84), (85, 89), (90, 94), (95, 99), (100, 104),
                   (105, 109)]:
        dist[f"{lo_hi[0]}-{lo_hi[1]}"] = {"female": 0.02, "male": 0.02}
    dist["110+"] = {"female": 0.001, "male": 0.001}

    return {
        "run_control": {
            "start_year": {"value": 1975},
            "end_year": {"value": 2026},
            "deterministic_mode": {"value": False},
        },
        "output": {"enabled": {"value": True}},
        "population": {
            "total_size": {"value": 13_598_912},
            "age_sex_distribution": {"value": dist},
            "epidemic_seed": {
                "enabled": {"value": True},
                "seed_prevalence": {"value": 0.002},
                "seed_year": {"value": 1975.0},
                "stage_distribution": {
                    "value": {
                        "Acute": 0.65,
                        "Chronic": 0.30,
                        "AIDS": 0.05,
                    },
                },
            },
        },
        "transmission_model": {
            "disease": {
                "mortality": {"aids": {"value": 0.258}},
                "progression": {
                    "chronic_to_aids": {"value": 0.0466},
                    "chronic_to_aids_on_art": {"value": 0.008},
                },
                "advanced": {"art_mortality_reduction": {"value": 0.911}},
                "diagnosis": {
                    "schedule": {
                        "value": {
                            "1985": 0.082,
                            "1990": 0.165,
                            "1997": 0.343,
                            "2015": 0.584,
                        },
                    },
                },
            },
        },
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {
                        "value": {
                            "1996": 0.144,
                            "2000": 0.617,
                            "2015": 0.819,
                            "2020": 0.878,
                        },
                    },
                },
            },
        },
    }


def _mock_result_from_outputs(outputs: dict) -> dict:
    """Build a minimal result dict that extract_annual_outputs can parse."""
    # The extract_annual_outputs function uses result["summary"],
    # but we monkeypatch it at the function level
    return {"_synthetic_outputs": outputs}


# ---------------------------------------------------------------------------
# _probe_definitions
# ---------------------------------------------------------------------------


class TestProbeDefinitions:
    def test_has_six_probes(self):
        probes = _probe_definitions()
        assert len(probes) == 6

    def test_probe_names_unique(self):
        probes = _probe_definitions()
        names = [p["name"] for p in probes]
        assert len(names) == len(set(names))

    def test_baseline_probe_exists(self):
        probes = _probe_definitions()
        names = [p["name"] for p in probes]
        assert "baseline_1980_current" in names

    def test_all_probes_have_required_keys(self):
        probes = _probe_definitions()
        for p in probes:
            assert "name" in p
            assert "description" in p
            assert "overrides" in p
            assert isinstance(p["overrides"], dict)

    def test_seed_variants_cover_three_years(self):
        probes = _probe_definitions()
        seed_years = set()
        for p in probes:
            if "seed_year" in p["overrides"]:
                seed_years.add(int(p["overrides"]["seed_year"]))
        assert 1975 in seed_years
        assert 1977 in seed_years
        assert 1980 in seed_years

    def test_chronic_heavy_probe(self):
        probes = _probe_definitions()
        heavy = [p for p in probes if p["name"] == "burnin_1975_seed_1975_chronic_heavy"]
        assert len(heavy) == 1
        assert heavy[0]["overrides"]["seed_chronic_share"] == 0.40
        assert heavy[0]["overrides"]["seed_aids_share"] == 0.02

    def test_aids_light_probe(self):
        probes = _probe_definitions()
        light = [p for p in probes if p["name"] == "burnin_1975_seed_1975_aids_light"]
        assert len(light) == 1
        assert light[0]["overrides"]["seed_aids_share"] == 0.005
        assert light[0]["overrides"]["seed_chronic_share"] == 0.25


# ---------------------------------------------------------------------------
# detect_pre_1990_spike
# ---------------------------------------------------------------------------


class TestDetectPre1990Spike:
    def test_no_spike_normal(self):
        outputs = _synthetic_outputs(peak_year=1995)
        result = detect_pre_1990_spike(outputs)
        assert result["has_spike"] == False

    def test_spike_detected_large_early_deaths(self):
        outputs = {"aids_deaths": {
            1980: 500, 1981: 400, 1982: 300,
            1990: 100, 1991: 80, 1992: 60, 1993: 50,
            1994: 40, 1995: 30,
        }}
        result = detect_pre_1990_spike(outputs)
        assert result["has_spike"] == True
        assert result["pre_1990_peak_year"] == 1980
        assert result["pre_1990_peak"] == 500

    def test_no_pre_1990_data(self):
        outputs = {"aids_deaths": {1990: 100, 1991: 200}}
        result = detect_pre_1990_spike(outputs)
        assert result["has_spike"] is False

    def test_small_pre_1990_no_spike(self):
        outputs = {"aids_deaths": {
            1985: 10, 1986: 15,
            1990: 200, 1991: 300, 1992: 250,
        }}
        result = detect_pre_1990_spike(outputs)
        assert result["has_spike"] is False

    def test_custom_threshold(self):
        outputs = {"aids_deaths": {
            1985: 30,
            1990: 10, 1991: 12,
        }}
        result = detect_pre_1990_spike(outputs, threshold=25.0)
        assert result["has_spike"] == True


# ---------------------------------------------------------------------------
# extract_probe_metrics
# ---------------------------------------------------------------------------


class TestExtractProbeMetrics:
    @pytest.fixture(autouse=True)
    def _patch_extract(self, monkeypatch):
        """Monkeypatch extract_annual_outputs to return synthetic data."""
        self._outputs = _synthetic_outputs()

        def fake_extract(result, start_year, end_year):
            return self._outputs

        monkeypatch.setattr(
            "tools.run_1975_burnin_probes.extract_annual_outputs",
            fake_extract,
        )

    def test_basic_metrics(self):
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert metrics["collapsed"] is False
        assert metrics["error"] is None
        assert "peak_aids_deaths_year" in metrics
        assert "peak_aids_deaths_value" in metrics

    def test_peak_year_correct(self):
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert metrics["peak_aids_deaths_year"] == 1995

    def test_cumulative_aids_deaths(self):
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert metrics["aids_deaths_cumul_1990_2005"] > 0

    def test_pre_1990_spike_field(self):
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert "pre_1990_death_spike" in metrics
        assert metrics["pre_1990_death_spike"] == True or metrics["pre_1990_death_spike"] == False

    def test_with_targets_adds_ratios(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "aids_death_bounds_fraction" in metrics
        assert "target_aids_deaths_peak_year" in metrics

    def test_diagnosis_ratio(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "total_diagnosis_ratio_2024" in metrics
        assert metrics["total_diagnosis_ratio_2024"] > 0

    def test_plhiv_mape(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "plhiv_recent_mape" in metrics

    def test_art_mape(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "art_recent_mape" in metrics

    def test_incidence_ratio(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "incidence_recent_median_ratio" in metrics

    def test_msm_diag_hist_mape(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "msm_diag_hist_mape" in metrics

    def test_aids_cases_max_ratio(self):
        targets = _synthetic_targets()
        metrics = extract_probe_metrics({}, targets=targets, start_year=1975)
        assert "aids_cases_max_ratio" in metrics

    def test_early_peak_detected(self):
        self._outputs = _synthetic_outputs(peak_year=1985)
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert metrics["peak_aids_deaths_year"] == 1985

    def test_late_peak_detected(self):
        self._outputs = _synthetic_outputs(peak_year=2010)
        metrics = extract_probe_metrics({}, targets=None, start_year=1975)
        assert metrics["peak_aids_deaths_year"] == 2010


# ---------------------------------------------------------------------------
# extract_probe_timeseries
# ---------------------------------------------------------------------------


class TestExtractProbeTimeseries:
    @pytest.fixture(autouse=True)
    def _patch_extract(self, monkeypatch):
        self._outputs = _synthetic_outputs()

        def fake_extract(result, start_year, end_year):
            return self._outputs

        monkeypatch.setattr(
            "tools.run_1975_burnin_probes.extract_annual_outputs",
            fake_extract,
        )

    def test_returns_dataframe(self):
        ts = extract_probe_timeseries({}, start_year=1975)
        assert isinstance(ts, pd.DataFrame)
        assert "year" in ts.columns

    def test_has_expected_columns(self):
        ts = extract_probe_timeseries({}, start_year=1975)
        for col in ["aids_deaths", "PLHIV", "new_infections", "new_diagnoses"]:
            assert col in ts.columns

    def test_year_range(self):
        ts = extract_probe_timeseries({}, start_year=1975)
        assert ts["year"].min() == 1975
        assert ts["year"].max() == CALIB_END_YEAR


# ---------------------------------------------------------------------------
# prepare_probe_config
# ---------------------------------------------------------------------------


class TestPrepareProbeConfig:
    def test_deterministic_mode_set(self):
        cfg = _minimal_1975_config()
        result, failed = prepare_probe_config(cfg, {"start_year": 1975})
        assert result["run_control"]["deterministic_mode"]["value"] is True

    def test_deep_copy(self):
        cfg = _minimal_1975_config()
        original_seed = cfg["population"]["epidemic_seed"]["seed_year"]["value"]
        result, _ = prepare_probe_config(cfg, {"seed_year": 1980.0})
        assert cfg["population"]["epidemic_seed"]["seed_year"]["value"] == original_seed
        assert result["population"]["epidemic_seed"]["seed_year"]["value"] == 1980.0

    def test_seed_stage_override(self):
        cfg = _minimal_1975_config()
        result, _ = prepare_probe_config(cfg, {
            "seed_chronic_share": 0.40,
            "seed_aids_share": 0.02,
        })
        stage = result["population"]["epidemic_seed"]["stage_distribution"]["value"]
        assert stage["Chronic"] == 0.40
        assert stage["AIDS"] == 0.02
        assert abs(stage["Acute"] - 0.58) < 1e-6

    def test_seed_stage_acute_nonnegative(self):
        cfg = _minimal_1975_config()
        result, _ = prepare_probe_config(cfg, {
            "seed_chronic_share": 0.95,
            "seed_aids_share": 0.10,
        })
        stage = result["population"]["epidemic_seed"]["stage_distribution"]["value"]
        assert stage["Acute"] >= 0
        assert stage["Chronic"] + stage["AIDS"] + stage["Acute"] == pytest.approx(1.0, abs=1e-6)

    def test_unknown_override_fails_gracefully(self):
        cfg = _minimal_1975_config()
        _, failed = prepare_probe_config(cfg, {"nonexistent_param": 999})
        assert len(failed) == 1
        assert "nonexistent_param" in failed[0]

    def test_output_disabled(self):
        cfg = _minimal_1975_config()
        result, _ = prepare_probe_config(cfg, {})
        assert result["output"]["enabled"]["value"] is False
        assert result["output"]["terminal_summary"]["value"] is False

    def test_end_year_set(self):
        cfg = _minimal_1975_config()
        result, _ = prepare_probe_config(cfg, {})
        assert result["run_control"]["end_year"]["value"] == CALIB_END_YEAR + 1


# ---------------------------------------------------------------------------
# _interpret_probes
# ---------------------------------------------------------------------------


class TestInterpretProbes:
    def _summary_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_empty_df(self):
        result = _interpret_probes(pd.DataFrame())
        assert len(result) == 1
        assert "No probe results" in result[0]

    def test_baseline_only(self):
        df = self._summary_df([{
            "probe": "baseline_1980_current",
            "peak_aids_deaths_value": 300,
            "peak_aids_deaths_year": 2000,
            "aids_deaths_cumul_1990_2005": 2000,
        }])
        lines = _interpret_probes(df)
        assert any("Baseline" in l for l in lines)

    def test_promising_improvement(self):
        df = self._summary_df([
            {
                "probe": "baseline_1980_current",
                "peak_aids_deaths_value": 200,
                "peak_aids_deaths_year": 2000,
                "aids_deaths_cumul_1990_2005": 1000,
            },
            {
                "probe": "burnin_1975_seed_1975",
                "peak_aids_deaths_value": 500,
                "peak_aids_deaths_year": 1995,
                "aids_deaths_cumul_1990_2005": 3000,
                "pre_1990_death_spike": False,
            },
        ])
        lines = _interpret_probes(df)
        assert any("PROMISING" in l for l in lines)

    def test_no_improvement(self):
        df = self._summary_df([
            {
                "probe": "baseline_1980_current",
                "peak_aids_deaths_value": 400,
                "peak_aids_deaths_year": 1997,
                "aids_deaths_cumul_1990_2005": 3000,
            },
            {
                "probe": "burnin_1975_seed_1975",
                "peak_aids_deaths_value": 350,
                "peak_aids_deaths_year": 2005,
                "aids_deaths_cumul_1990_2005": 2800,
                "pre_1990_death_spike": False,
            },
        ])
        lines = _interpret_probes(df)
        assert any("NO IMPROVEMENT" in l for l in lines)

    def test_partial_improvement(self):
        df = self._summary_df([
            {
                "probe": "baseline_1980_current",
                "peak_aids_deaths_value": 200,
                "peak_aids_deaths_year": 2000,
                "aids_deaths_cumul_1990_2005": 1000,
            },
            {
                "probe": "burnin_1975_seed_1975",
                "peak_aids_deaths_value": 400,
                "peak_aids_deaths_year": 2005,
                "aids_deaths_cumul_1990_2005": 2000,
                "pre_1990_death_spike": False,
            },
        ])
        lines = _interpret_probes(df)
        assert any("PARTIAL" in l for l in lines)

    def test_spike_warning(self):
        df = self._summary_df([
            {
                "probe": "burnin_1975_seed_1975",
                "pre_1990_death_spike": True,
                "pre_1990_peak_deaths": 800,
                "pre_1990_peak_year": 1982,
            },
        ])
        lines = _interpret_probes(df)
        assert any("WARNING" in l and "spike" in l.lower() for l in lines)

    def test_seed_timing_matters(self):
        df = self._summary_df([
            {
                "probe": "burnin_1975_seed_1975",
                "aids_deaths_cumul_1990_2005": 3000,
            },
            {
                "probe": "burnin_1975_seed_1980",
                "aids_deaths_cumul_1990_2005": 1500,
            },
        ])
        lines = _interpret_probes(df)
        assert any("MATTERS" in l for l in lines)

    def test_seed_timing_minimal(self):
        df = self._summary_df([
            {
                "probe": "burnin_1975_seed_1975",
                "aids_deaths_cumul_1990_2005": 3000,
            },
            {
                "probe": "burnin_1975_seed_1980",
                "aids_deaths_cumul_1990_2005": 2900,
            },
        ])
        lines = _interpret_probes(df)
        assert any("MINIMAL" in l for l in lines)


# ---------------------------------------------------------------------------
# CONFIG_PATHS
# ---------------------------------------------------------------------------


class TestConfigPaths:
    def test_all_paths_non_empty(self):
        for name, path in CONFIG_PATHS.items():
            assert len(path) > 0
            assert "." in path

    def test_key_params_present(self):
        assert "aids_mortality" in CONFIG_PATHS
        assert "chronic_to_aids" in CONFIG_PATHS
        assert "start_year" in CONFIG_PATHS
        assert "seed_year" in CONFIG_PATHS
        assert "seed_prevalence" in CONFIG_PATHS
        assert "seed_chronic_share" in CONFIG_PATHS
        assert "seed_aids_share" in CONFIG_PATHS


# ---------------------------------------------------------------------------
# HMD_YEAR_POPULATIONS
# ---------------------------------------------------------------------------


class TestHMDPopulations:
    def test_known_years(self):
        assert 1970 in HMD_YEAR_POPULATIONS
        assert 1975 in HMD_YEAR_POPULATIONS
        assert 1980 in HMD_YEAR_POPULATIONS

    def test_1975_value(self):
        assert HMD_YEAR_POPULATIONS[1975] == 13_598_912

    def test_1980_value(self):
        assert HMD_YEAR_POPULATIONS[1980] == 14_090_318

    def test_ordering(self):
        assert HMD_YEAR_POPULATIONS[1970] < HMD_YEAR_POPULATIONS[1975]
        assert HMD_YEAR_POPULATIONS[1975] < HMD_YEAR_POPULATIONS[1980]


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def test_produces_html(self):
        summary_df = pd.DataFrame([{
            "probe": "baseline_1980_current",
            "peak_aids_deaths_value": 300,
            "peak_aids_deaths_year": 2000,
        }])
        timeseries_df = pd.DataFrame([{
            "probe": "baseline_1980_current",
            "year": 1990,
            "aids_deaths": 100,
        }])
        interpretation = ["Baseline (1980 start): peak = 300 in 2000."]
        manifest = {"created_at": "2026-05-28T00:00:00", "base_config": "test.json"}

        html = build_html_report(summary_df, timeseries_df, interpretation, manifest)
        assert "<!DOCTYPE html>" in html
        assert "V13.14" in html
        assert "Diagnostic only" in html
        assert "baseline_1980_current" in html

    def test_spike_warning_rendered(self):
        summary_df = pd.DataFrame()
        timeseries_df = pd.DataFrame()
        interpretation = ["WARNING: spike detected in probe X"]
        manifest = {}

        html = build_html_report(summary_df, timeseries_df, interpretation, manifest)
        assert "bad" in html  # CSS class for warning lines
        assert "WARNING" in html

    def test_promising_rendered_green(self):
        summary_df = pd.DataFrame()
        timeseries_df = pd.DataFrame()
        interpretation = ["PROMISING: burn-in improves deaths"]
        manifest = {}

        html = build_html_report(summary_df, timeseries_df, interpretation, manifest)
        assert "good" in html

    def test_empty_data(self):
        html = build_html_report(pd.DataFrame(), pd.DataFrame(), [], {})
        assert "<!DOCTYPE html>" in html
