"""
Tests for tools/audit_model_mechanism_sanity.py — pure logic only.

No model simulation is run. All tests work on synthetic DataFrames and configs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_model_mechanism_sanity import (
    CHECK_STATUS,
    STEP_ORDERING_DESCRIPTION,
    _annual_summary,
    build_html_report,
    check_aids_compartment_entry,
    check_aids_death_vs_stock,
    check_aids_deaths_counted,
    check_art_mortality_reduction_config,
    check_art_protection_timing,
    check_diagnosis_reconciliation,
    check_orientation_multipliers,
    check_step_ordering_concern,
    run_all_checks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_annual(years=None, **col_overrides) -> pd.DataFrame:
    if years is None:
        years = list(range(1980, 2026))
    n = len(years)
    base = {
        "new_infections": [50.0] * n,
        "aids_deaths": [10.0] * n,
        "new_aids_cases": [15.0] * n,
        "new_diagnoses": [30.0] * n,
        "new_diagnoses_msm": [12.0] * n,
        "hiv_deaths": [12.0] * n,
        "art_initiations": [20.0] * n,
        "art_initiations_aids": [3.0] * n,
        "prevalence": [0.003] * n,
        "sexually_active_population": [1_000_000.0] * n,
        "on_art_stock": [500.0] * n,
        "PLHIV": [3000.0] * n,
    }
    base.update(col_overrides)
    df = pd.DataFrame(base, index=years)
    df.index.name = "year"
    return df


def _make_config(**overrides) -> dict:
    cfg = {
        "transmission_model": {
            "disease": {
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
    }
    if "amr_value" in overrides:
        cfg["transmission_model"]["disease"]["advanced"]["art_mortality_reduction"]["value"] = overrides["amr_value"]
    return cfg


# ---------------------------------------------------------------------------
# check_aids_compartment_entry
# ---------------------------------------------------------------------------


class TestAidsCompartmentEntry:
    def test_cleared(self):
        annual = _make_annual(new_aids_cases=[200.0] * 46)
        result = check_aids_compartment_entry(annual)
        assert result["status"] == "cleared"
        assert result["total_aids_cases"] > 100

    def test_suspicious_low(self):
        annual = _make_annual(new_aids_cases=[2.0] * 46)
        result = check_aids_compartment_entry(annual)
        assert result["status"] == "suspicious"

    def test_blocked_missing_col(self):
        annual = _make_annual()
        annual = annual.drop(columns=["new_aids_cases"])
        result = check_aids_compartment_entry(annual)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_aids_deaths_counted
# ---------------------------------------------------------------------------


class TestAidsDeathsCounted:
    def test_cleared(self):
        annual = _make_annual(aids_deaths=[100.0] * 46)
        result = check_aids_deaths_counted(annual)
        assert result["status"] == "cleared"

    def test_suspicious_zero_deaths_with_cases(self):
        annual = _make_annual(aids_deaths=[0.0] * 46, new_aids_cases=[50.0] * 46)
        result = check_aids_deaths_counted(annual)
        assert result["status"] == "suspicious"
        assert "zero AIDS deaths" in result["detail"]

    def test_blocked(self):
        annual = _make_annual()
        annual = annual.drop(columns=["aids_deaths"])
        result = check_aids_deaths_counted(annual)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_art_protection_timing
# ---------------------------------------------------------------------------


class TestArtProtectionTiming:
    def test_cleared_normal_coverage(self):
        years = list(range(1980, 2026))
        art = [0.0] * 10 + [100.0] * 10 + [500.0] * 15 + [2000.0] * 11
        annual = _make_annual(on_art_stock=art, PLHIV=[3000.0] * 46)
        result = check_art_protection_timing(annual)
        assert result["status"] == "cleared"

    def test_suspicious_high_early_coverage(self):
        annual = _make_annual(on_art_stock=[2000.0] * 46, PLHIV=[3000.0] * 46)
        result = check_art_protection_timing(annual)
        assert result["status"] == "suspicious"
        assert result["early_coverage"] > 0.5

    def test_blocked(self):
        annual = _make_annual()
        annual = annual.drop(columns=["on_art_stock"])
        result = check_art_protection_timing(annual)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_step_ordering_concern
# ---------------------------------------------------------------------------


class TestStepOrdering:
    def test_always_suspicious(self):
        result = check_step_ordering_concern()
        assert result["status"] == "suspicious"
        assert "step 4" in result["detail"]
        assert "step 10" in result["detail"]


# ---------------------------------------------------------------------------
# check_diagnosis_reconciliation
# ---------------------------------------------------------------------------


class TestDiagnosisReconciliation:
    def test_cleared(self):
        annual = _make_annual(new_diagnoses=[30.0] * 46, new_diagnoses_msm=[12.0] * 46)
        result = check_diagnosis_reconciliation(annual)
        assert result["status"] == "cleared"
        assert result["negative_years"] == 0

    def test_bug_msm_exceeds_total(self):
        annual = _make_annual(new_diagnoses=[10.0] * 46, new_diagnoses_msm=[20.0] * 46)
        result = check_diagnosis_reconciliation(annual)
        assert result["status"] == "bug_confirmed"
        assert result["negative_years"] > 0

    def test_suspicious_near_zero_non_msm(self):
        annual = _make_annual(new_diagnoses=[12.0] * 46, new_diagnoses_msm=[12.0] * 46)
        result = check_diagnosis_reconciliation(annual)
        assert result["total_non_msm_diagnoses"] < 10
        assert result["status"] == "suspicious"


# ---------------------------------------------------------------------------
# check_aids_death_vs_stock
# ---------------------------------------------------------------------------


class TestAidsDeathVsStock:
    def test_cleared(self):
        annual = _make_annual(new_aids_cases=[50.0] * 46, aids_deaths=[10.0] * 46)
        result = check_aids_death_vs_stock(annual)
        assert result["status"] == "cleared"
        assert result["peak_aids_stock"] > 20

    def test_suspicious_low_stock(self):
        annual = _make_annual(new_aids_cases=[1.0] * 46, aids_deaths=[1.0] * 46)
        result = check_aids_death_vs_stock(annual)
        assert result["peak_aids_stock"] <= 20 or result["status"] == "cleared"

    def test_blocked(self):
        annual = _make_annual()
        annual = annual.drop(columns=["new_aids_cases"])
        result = check_aids_death_vs_stock(annual)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_art_mortality_reduction_config
# ---------------------------------------------------------------------------


class TestArtMortalityReductionConfig:
    def test_cleared_normal(self):
        config = _make_config(amr_value=0.90)
        result = check_art_mortality_reduction_config(config)
        assert result["status"] == "cleared"
        assert result["amr_value"] == 0.90

    def test_suspicious_high(self):
        config = _make_config(amr_value=0.98)
        result = check_art_mortality_reduction_config(config)
        assert result["status"] == "suspicious"

    def test_blocked_missing(self):
        config = {"transmission_model": {"disease": {"advanced": {}}}}
        result = check_art_mortality_reduction_config(config)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# check_orientation_multipliers
# ---------------------------------------------------------------------------


class TestOrientationMultipliers:
    def test_cleared(self):
        config = _make_config()
        result = check_orientation_multipliers(config)
        assert result["status"] == "cleared"

    def test_blocked_missing(self):
        config = {"transmission_model": {"groups": {"advanced": {}}}}
        result = check_orientation_multipliers(config)
        assert result["status"] == "blocked"


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    def test_returns_list(self):
        config = _make_config()
        annual = _make_annual()
        checks = run_all_checks(config, annual, "test_candidate")
        assert len(checks) == 8
        assert all(c["candidate"] == "test_candidate" for c in checks)
        assert all(c["status"] in CHECK_STATUS for c in checks)


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestHtmlReport:
    def test_produces_html(self):
        config = _make_config()
        annual = _make_annual()
        checks = run_all_checks(config, annual, "test")
        html = build_html_report(
            checks,
            annual.reset_index(),
            pd.DataFrame([{"candidate": "test", "total_aids_deaths": 460}]),
            {"created_at": "now", "n_candidates": 1},
        )
        assert "<html" in html
        assert "V13.13" in html
        assert "Mechanism Sanity" in html
