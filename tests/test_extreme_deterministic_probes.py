"""
Tests for tools/run_extreme_deterministic_probes.py — pure logic only.

No model simulation is run. Tests cover probe definition structure,
config preparation, metrics extraction, and interpretation logic.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_extreme_deterministic_probes import (
    CONFIG_PATHS,
    _compute_bounds_fraction,
    _compute_max_ratio,
    _compute_recent_mape,
    _compute_recent_ratios,
    _cumulative_target,
    _find_target_value,
    _interpret_probes,
    _probe_definitions,
    _target_indicator,
    _target_value,
    build_html_report,
    extract_probe_metrics,
    extract_probe_timeseries,
    prepare_probe_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_base_config() -> dict:
    return {
        "run_control": {
            "start_year": {"value": 1980},
            "end_year": {"value": 2026},
            "deterministic_mode": {"value": False},
        },
        "output": {
            "enabled": {"value": True},
            "terminal_summary": {"value": True},
        },
        "population": {
            "epidemic_seed": {
                "seed_prevalence": {"value": 0.002},
                "msm_fraction": {"value": 0.9},
                "stage_distribution": {
                    "value": {
                        "Acute": 0.60,
                        "Chronic": 0.35,
                        "AIDS": 0.05,
                    }
                },
            },
            "p_msm": {"value": 0.07},
            "activity_distribution": {
                "value": {"Low": 0.65, "Medium": 0.27, "High": 0.08}
            },
        },
        "transmission_model": {
            "disease": {
                "mortality": {
                    "acute": {"value": 0.0},
                    "chronic": {"value": 0.01},
                    "aids": {"value": 0.25},
                },
                "progression": {
                    "acute_to_chronic": {"value": 1.0},
                    "chronic_to_aids": {"value": 0.05},
                    "chronic_to_aids_on_art": {"value": 0.005},
                },
                "diagnosis": {
                    "schedule": {
                        "value": {
                            "1985": 0.10,
                            "1990": 0.20,
                            "1997": 0.35,
                            "2015": 0.55,
                        }
                    },
                    "msm_relative_diagnosis": {
                        "schedule": {
                            "value": {
                                "1996": 0.40,
                                "2004": 0.70,
                                "2015": 0.70,
                            }
                        }
                    },
                },
                "advanced": {
                    "art_mortality_reduction": {"value": 0.90},
                },
            },
            "groups": {
                "advanced": {
                    "orientation_contact_multipliers": {
                        "value": {
                            "Female": 0.5,
                            "Heterosexual_Male": 1.0,
                            "MSM": 3.5,
                        }
                    }
                }
            },
        },
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {
                        "value": {
                            "1996": 0.20,
                            "2000": 0.50,
                            "2015": 0.70,
                            "2020": 0.85,
                        }
                    }
                },
                "advanced": {
                    "art_dropout_rate": {"value": 0.01},
                },
            }
        },
    }


def _make_targets() -> list:
    targets = []
    for y in range(1990, 2025):
        targets.append({"indicator": "aids_deaths", "year": y,
                        "value": 80.0, "lower_bound": 40.0, "upper_bound": 120.0})
        targets.append({"indicator": "new_diagnoses", "year": y, "value": 500.0})
        targets.append({"indicator": "new_diagnoses_msm", "year": y, "value": 300.0})
        targets.append({"indicator": "PLHIV", "year": y, "value": 25000.0})
        targets.append({"indicator": "on_art_stock", "year": y, "value": 20000.0})
        targets.append({"indicator": "new_infections", "year": y, "value": 300.0})
        targets.append({"indicator": "new_aids_cases", "year": y, "value": 100.0})
    targets.append({"indicator": "new_diagnoses", "year": 2024, "value": 500.0})
    targets.append({"indicator": "new_diagnoses_msm", "year": 2024, "value": 300.0})
    return targets


def _make_sim_result() -> dict:
    """Fake sim result with a summary DataFrame."""
    years = []
    for y in range(1980, 2026):
        for q in range(4):
            years.append(y + q * 0.25)
    n = len(years)
    df = pd.DataFrame({
        "year": years,
        "prevalence": [0.003] * n,
        "sexually_active_population": [1_000_000] * n,
        "new_infections": [50] * n,
        "aids_deaths": [5] * n,
        "new_aids_cases": [8] * n,
        "new_diagnoses": [30] * n,
        "new_diagnoses_msm": [12] * n,
        "on_art_stock": [500] * n,
        "art_initiations": [10] * n,
    })
    return {"summary": df}


# ---------------------------------------------------------------------------
# _probe_definitions
# ---------------------------------------------------------------------------


class TestProbeDefinitions:
    def test_minimum_probes_exist(self):
        probes = _probe_definitions()
        names = {p["name"] for p in probes}
        assert "baseline_v13_10_best" in names
        assert "aids_mortality_high" in names
        assert "art_weak_early" in names
        assert "progression_high" in names
        assert "seed_aids_high" in names
        assert "combined_aids_capacity" in names
        assert "diagnosis_capacity" in names
        assert "combined_diagnosis_and_aids" in names

    def test_probes_have_required_fields(self):
        for p in _probe_definitions():
            assert "name" in p
            assert "description" in p
            assert "overrides" in p
            assert isinstance(p["overrides"], dict)

    def test_baseline_has_no_overrides(self):
        probes = _probe_definitions()
        baseline = next(p for p in probes if p["name"] == "baseline_v13_10_best")
        assert baseline["overrides"] == {}


# ---------------------------------------------------------------------------
# prepare_probe_config
# ---------------------------------------------------------------------------


class TestPrepareProbeConfig:
    def test_deterministic_forced(self):
        cfg, failed = prepare_probe_config(_make_base_config(), {})
        assert cfg["run_control"]["deterministic_mode"]["value"] is True
        assert cfg["output"]["enabled"]["value"] is False
        assert not failed

    def test_aids_mortality_override(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(), {"aids_mortality": 0.70}
        )
        assert cfg["transmission_model"]["disease"]["mortality"]["aids"]["value"] == 0.70
        assert not failed

    def test_art_overrides(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(),
            {"art_init_1996": 0.02, "art_mortality_reduction": 0.50}
        )
        assert cfg["interventions"]["art"]["schedule"]["initiation"]["value"]["1996"] == 0.02
        assert cfg["transmission_model"]["disease"]["advanced"]["art_mortality_reduction"]["value"] == 0.50

    def test_seed_stage_override(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(),
            {"seed_aids_share": 0.20, "seed_chronic_share": 0.50}
        )
        stage_dist = cfg["population"]["epidemic_seed"]["stage_distribution"]["value"]
        assert stage_dist["AIDS"] == 0.20
        assert stage_dist["Chronic"] == 0.50
        assert abs(stage_dist["Acute"] - 0.30) < 1e-6

    def test_seed_aids_only_override(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(),
            {"seed_aids_share": 0.10}
        )
        stage_dist = cfg["population"]["epidemic_seed"]["stage_distribution"]["value"]
        assert stage_dist["AIDS"] == 0.10
        assert stage_dist["Acute"] + stage_dist["Chronic"] + stage_dist["AIDS"] == pytest.approx(1.0)

    def test_diagnosis_overrides(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(),
            {"diagnosis_1985": 0.40, "diagnosis_2015": 0.90}
        )
        sched = cfg["transmission_model"]["disease"]["diagnosis"]["schedule"]["value"]
        assert sched["1985"] == 0.40
        assert sched["2015"] == 0.90

    def test_unknown_override_fails(self):
        cfg, failed = prepare_probe_config(
            _make_base_config(),
            {"nonexistent_param": 999.0}
        )
        assert len(failed) == 1
        assert "no config path" in failed[0]

    def test_deep_copy(self):
        base = _make_base_config()
        cfg, _ = prepare_probe_config(base, {"aids_mortality": 0.99})
        assert base["transmission_model"]["disease"]["mortality"]["aids"]["value"] == 0.25

    def test_combined_aids_capacity(self):
        probes = _probe_definitions()
        cap = next(p for p in probes if p["name"] == "combined_aids_capacity")
        cfg, failed = prepare_probe_config(_make_base_config(), cap["overrides"])
        assert not failed
        assert cfg["transmission_model"]["disease"]["mortality"]["aids"]["value"] == 0.70
        assert cfg["transmission_model"]["disease"]["progression"]["chronic_to_aids"]["value"] == 0.15


# ---------------------------------------------------------------------------
# Target helpers
# ---------------------------------------------------------------------------


class TestTargetHelpers:
    def test_find_target_value(self):
        targets = _make_targets()
        val = _find_target_value(targets, "aids_deaths", 1995)
        assert val == 80.0

    def test_find_target_missing(self):
        assert _find_target_value([], "aids_deaths", 2024) is None

    def test_find_target_real_format(self):
        targets = [{"model_output": "aids_deaths", "year": 2000, "estimate": 90.0}]
        val = _find_target_value(targets, "aids_deaths", 2000)
        assert val == 90.0

    def test_target_indicator_both_formats(self):
        assert _target_indicator({"indicator": "foo"}) == "foo"
        assert _target_indicator({"model_output": "bar"}) == "bar"

    def test_target_value_both_formats(self):
        assert _target_value({"value": 1.0}) == 1.0
        assert _target_value({"estimate": 2.0}) == 2.0

    def test_recent_mape(self):
        outputs = {"PLHIV": {y: 25000.0 for y in range(1990, 2025)}}
        targets = _make_targets()
        mape = _compute_recent_mape(outputs, targets, "PLHIV", 2018, 2023)
        assert mape is not None
        assert mape == pytest.approx(0.0)

    def test_recent_mape_with_error(self):
        outputs = {"PLHIV": {y: 30000.0 for y in range(1990, 2025)}}
        targets = _make_targets()
        mape = _compute_recent_mape(outputs, targets, "PLHIV", 2018, 2023)
        assert mape is not None
        assert mape == pytest.approx(20.0)

    def test_bounds_fraction(self):
        outputs = {"aids_deaths": {y: 80.0 for y in range(1990, 2025)}}
        targets = _make_targets()
        bf = _compute_bounds_fraction(outputs, targets, "aids_deaths")
        assert bf == 1.0

    def test_bounds_fraction_outside(self):
        outputs = {"aids_deaths": {y: 200.0 for y in range(1990, 2025)}}
        targets = _make_targets()
        bf = _compute_bounds_fraction(outputs, targets, "aids_deaths")
        assert bf == 0.0

    def test_cumulative_target(self):
        targets = _make_targets()
        cumul = _cumulative_target(targets, "aids_deaths", 1990, 2005)
        assert cumul == pytest.approx(80.0 * 16)

    def test_max_ratio(self):
        outputs = {"new_aids_cases": {y: 200.0 for y in range(1990, 2025)}}
        targets = _make_targets()
        ratio = _compute_max_ratio(outputs, targets, "new_aids_cases")
        assert ratio == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# extract_probe_metrics
# ---------------------------------------------------------------------------


class TestExtractProbeMetrics:
    def test_basic_extraction(self):
        result = _make_sim_result()
        targets = _make_targets()
        metrics = extract_probe_metrics(result, targets)
        assert "peak_aids_deaths_year" in metrics
        assert "aids_deaths_cumul_1990_2005" in metrics
        assert metrics["collapsed"] is False

    def test_without_targets(self):
        result = _make_sim_result()
        metrics = extract_probe_metrics(result, None)
        assert "peak_aids_deaths_year" in metrics
        assert "total_diagnosis_ratio_2024" not in metrics


# ---------------------------------------------------------------------------
# extract_probe_timeseries
# ---------------------------------------------------------------------------


class TestExtractProbeTimeseries:
    def test_basic_extraction(self):
        result = _make_sim_result()
        ts = extract_probe_timeseries(result)
        assert len(ts) > 0
        assert "year" in ts.columns
        assert "aids_deaths" in ts.columns
        assert "PLHIV" in ts.columns


# ---------------------------------------------------------------------------
# _interpret_probes
# ---------------------------------------------------------------------------


class TestInterpretProbes:
    def test_low_aids_capacity(self):
        df = pd.DataFrame([{
            "probe": "combined_aids_capacity",
            "peak_aids_deaths_value": 10,
        }])
        interp = _interpret_probes(df)
        assert any("STRUCTURAL" in line for line in interp)

    def test_has_aids_capacity(self):
        df = pd.DataFrame([{
            "probe": "combined_aids_capacity",
            "peak_aids_deaths_value": 200,
        }])
        interp = _interpret_probes(df)
        assert any("CAN produce" in line for line in interp)

    def test_structural_tradeoff(self):
        df = pd.DataFrame([
            {"probe": "combined_aids_capacity", "peak_aids_deaths_value": 200},
            {"probe": "combined_diagnosis_and_aids",
             "total_diagnosis_ratio_2024": 0.3, "aids_death_bounds_fraction": 0.1},
        ])
        interp = _interpret_probes(df)
        assert any("structural tradeoff" in line for line in interp)

    def test_capacity_exists(self):
        df = pd.DataFrame([
            {"probe": "combined_aids_capacity", "peak_aids_deaths_value": 200},
            {"probe": "combined_diagnosis_and_aids",
             "total_diagnosis_ratio_2024": 1.0, "aids_death_bounds_fraction": 0.5},
        ])
        interp = _interpret_probes(df)
        assert any("capacity exists" in line for line in interp)

    def test_empty_df(self):
        interp = _interpret_probes(pd.DataFrame())
        assert len(interp) > 0


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestHtmlReport:
    def test_produces_html(self):
        summary_df = pd.DataFrame([{
            "probe": "baseline",
            "peak_aids_deaths_value": 50,
        }])
        ts_df = pd.DataFrame([{"year": 2000, "probe": "baseline", "aids_deaths": 5}])
        html = build_html_report(
            summary_df, ts_df, ["Test interpretation."],
            {"created_at": "now", "base_config": "test.json"}
        )
        assert "<html" in html
        assert "V13.13" in html
