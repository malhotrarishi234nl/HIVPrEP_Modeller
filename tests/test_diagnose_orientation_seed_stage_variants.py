"""
Tests for tools/diagnose_orientation_seed_stage_variants.py — pure logic only.

No model simulation is run in any of these tests.
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

from tools.diagnose_orientation_seed_stage_variants import (
    _VARIANT_DESCRIPTIONS,
    _VARIANT_KEYS,
    _VARIANT_ORIENT_SEED,
    _V13_10_MSM_SCHEDULE,
    _finite,
    _safe_ratio,
    build_html_report,
    build_yearly_rows,
    compute_summary_row,
    derive_non_msm_diagnoses,
    extract_config_params,
    patch_config_for_variant,
)


# ---------------------------------------------------------------------------
# Variant registry sanity
# ---------------------------------------------------------------------------


class TestVariantRegistry:
    def test_all_variant_keys_have_descriptions(self):
        for k in _VARIANT_KEYS:
            assert k in _VARIANT_DESCRIPTIONS, f"missing description for '{k}'"

    def test_all_variant_keys_have_orient_spec_entry(self):
        for k in _VARIANT_KEYS:
            assert k in _VARIANT_ORIENT_SEED, f"missing orient spec for '{k}'"

    def test_v13_10_multiplier_schedule_keys(self):
        assert set(_V13_10_MSM_SCHEDULE.keys()) == {1996, 2004, 2015}

    def test_combined_variant_uses_best_seed_schedule(self):
        spec = _VARIANT_ORIENT_SEED["v13_10_multiplier_plus_best_seed"]
        assert spec is not None
        assert "MSM"     in spec
        assert "non_MSM" in spec


# ---------------------------------------------------------------------------
# patch_config_for_variant: immutability and correctness
# ---------------------------------------------------------------------------


class TestPatchConfigForVariant:
    def _make_config(self, with_shared_dist=False):
        cfg = {
            "population": {"epidemic_seed": {}},
            "transmission_model": {"disease": {"diagnosis": {}}},
            "run_control": {"deterministic_mode": {"value": False},
                            "start_year": {"value": 1980},
                            "end_year": {"value": 2026}},
            "output": {"enabled": {"value": True},
                       "terminal_summary": {"value": True}},
        }
        if with_shared_dist:
            cfg["population"]["epidemic_seed"]["stage_distribution"] = {
                "value": {"Acute": 0.90, "Chronic": 0.08, "AIDS": 0.02}
            }
        return cfg

    def test_original_config_not_mutated(self):
        cfg = self._make_config()
        import copy
        original = copy.deepcopy(cfg)
        patch_config_for_variant(cfg, "non_msm_more_chronic")
        assert cfg == original

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError, match="Unknown variant"):
            patch_config_for_variant({}, "nonexistent_variant")

    def test_shared_current_unchanged(self):
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "shared_current")
        ep = result["population"]["epidemic_seed"]
        assert "stage_distribution_by_orientation" not in ep

    def test_non_msm_more_chronic_injects_block(self):
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "non_msm_more_chronic")
        ep = result["population"]["epidemic_seed"]
        assert "stage_distribution_by_orientation" in ep
        block = ep["stage_distribution_by_orientation"]
        assert block["enabled"]["value"] is True

    def test_non_msm_more_chronic_non_msm_values(self):
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "non_msm_more_chronic")
        ep = result["population"]["epidemic_seed"]
        non_msm = ep["stage_distribution_by_orientation"]["value"]["non_MSM"]
        assert non_msm["Acute"]   == pytest.approx(0.70)
        assert non_msm["Chronic"] == pytest.approx(0.25)
        assert non_msm["AIDS"]    == pytest.approx(0.05)

    def test_non_msm_more_chronic_msm_uses_shared(self):
        """When MSM spec is None, MSM distribution should mirror shared dist."""
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "non_msm_more_chronic")
        ep = result["population"]["epidemic_seed"]
        msm = ep["stage_distribution_by_orientation"]["value"]["MSM"]
        assert msm["Acute"]   == pytest.approx(0.90)
        assert msm["Chronic"] == pytest.approx(0.08)
        assert msm["AIDS"]    == pytest.approx(0.02)

    def test_msm_less_aids_injects_both_orientations(self):
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "msm_less_aids_non_msm_more_aids")
        ep = result["population"]["epidemic_seed"]
        val = ep["stage_distribution_by_orientation"]["value"]
        assert "MSM"     in val
        assert "non_MSM" in val
        assert val["MSM"]["AIDS"]     == pytest.approx(0.01)
        assert val["non_MSM"]["AIDS"] == pytest.approx(0.10)

    def test_combined_variant_injects_msm_multiplier(self):
        cfg = self._make_config(with_shared_dist=True)
        result = patch_config_for_variant(cfg, "v13_10_multiplier_plus_best_seed")
        msm_diag = (result["transmission_model"]["disease"]["diagnosis"]
                    .get("msm_relative_diagnosis", {}))
        assert msm_diag.get("enabled", {}).get("value") is True
        schedule = msm_diag["schedule"]["value"]
        assert "1996" in schedule
        assert float(schedule["1996"]) == pytest.approx(0.35)

    def test_combined_variant_seed_same_as_variant_d(self):
        cfg = self._make_config(with_shared_dist=True)
        result_d = patch_config_for_variant(cfg, "msm_less_aids_non_msm_more_aids")
        result_f = patch_config_for_variant(cfg, "v13_10_multiplier_plus_best_seed")
        val_d = result_d["population"]["epidemic_seed"]["stage_distribution_by_orientation"]["value"]
        val_f = result_f["population"]["epidemic_seed"]["stage_distribution_by_orientation"]["value"]
        assert val_d["MSM"]     == val_f["MSM"]
        assert val_d["non_MSM"] == val_f["non_MSM"]

    def test_non_msm_more_aids_distinct_from_non_msm_more_chronic(self):
        cfg = self._make_config(with_shared_dist=True)
        r_b = patch_config_for_variant(cfg, "non_msm_more_chronic")
        r_c = patch_config_for_variant(cfg, "non_msm_more_aids")
        aids_b = r_b["population"]["epidemic_seed"]["stage_distribution_by_orientation"]["value"]["non_MSM"]["AIDS"]
        aids_c = r_c["population"]["epidemic_seed"]["stage_distribution_by_orientation"]["value"]["non_MSM"]["AIDS"]
        assert aids_c > aids_b


# ---------------------------------------------------------------------------
# derive_non_msm_diagnoses
# ---------------------------------------------------------------------------


class TestDeriveNonMsmDiagnoses:
    def test_subtraction(self):
        outputs = {"new_diagnoses": {2020: 300.0}, "new_diagnoses_msm": {2020: 200.0}}
        result = derive_non_msm_diagnoses(outputs, [2020])
        assert result[2020] == pytest.approx(100.0)

    def test_clamped_to_zero(self):
        outputs = {"new_diagnoses": {2020: 100.0}, "new_diagnoses_msm": {2020: 200.0}}
        result = derive_non_msm_diagnoses(outputs, [2020])
        assert result[2020] == pytest.approx(0.0)

    def test_missing_year_returns_none(self):
        outputs = {"new_diagnoses": {}, "new_diagnoses_msm": {}}
        result = derive_non_msm_diagnoses(outputs, [2020])
        assert result[2020] is None


# ---------------------------------------------------------------------------
# compute_summary_row
# ---------------------------------------------------------------------------


class TestComputeSummaryRowSafe:
    def test_no_crash_with_empty_inputs(self):
        row = compute_summary_row("shared_current", {}, {}, [])
        assert row["variant"] == "shared_current"

    def test_diag_ratio_computed(self):
        outputs = {
            "new_diagnoses":     {2024: 500.0},
            "new_diagnoses_msm": {2024: 320.0},
            "new_infections":    {2015: 100.0, 2024: 80.0},
            "aids_deaths":       {1990: 50.0},
            "on_art_stock":      {2024: 2000.0},
            "PLHIV":             {2024: 2200.0},
        }
        targets = [
            {"model_output": "new_diagnoses",     "year": 2024, "estimate": 500.0, "lower": None, "upper": None},
            {"model_output": "new_diagnoses_msm", "year": 2024, "estimate": 320.0, "lower": None, "upper": None},
        ]
        row = compute_summary_row("test", outputs, {}, targets)
        assert row["diag_2024_total_ratio"] == pytest.approx(1.0)
        assert row["diag_2024_msm_ratio"]   == pytest.approx(1.0)

    def test_non_msm_stock_fields_present(self):
        strat = {
            "non_msm_hiv_pos":             {2024: 500.0},
            "non_msm_undiagnosed_hiv_pos": {2024: 150.0},
        }
        row = compute_summary_row("v", {}, strat, [])
        assert row["non_msm_hiv_pos_2024"]           == pytest.approx(500.0)
        assert row["non_msm_undiagnosed_stock_2024"] == pytest.approx(150.0)

    def test_aids_death_bounds_fraction_computed(self):
        outputs = {"aids_deaths": {1990: 55.0, 1991: 62.0}}
        targets = [
            {"model_output": "aids_deaths", "year": 1990, "estimate": 50.0, "lower": 40.0, "upper": 60.0},
            {"model_output": "aids_deaths", "year": 1991, "estimate": 60.0, "lower": 40.0, "upper": 65.0},
        ]
        row = compute_summary_row("v", outputs, {}, targets)
        assert row["aids_death_bounds_fraction"] == pytest.approx(1.0)

    def test_non_msm_diag_sum_computed(self):
        outputs = {
            "new_diagnoses":     {yr: 300.0 for yr in range(2015, 2025)},
            "new_diagnoses_msm": {yr: 200.0 for yr in range(2015, 2025)},
        }
        row = compute_summary_row("v", outputs, {}, [])
        assert row["non_msm_diag_2015_2024_sum"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# build_yearly_rows
# ---------------------------------------------------------------------------


class TestBuildYearlyRows:
    def test_returns_list_of_dicts(self):
        outputs = {"new_infections": {2020: 100.0, 2021: 90.0}}
        rows = build_yearly_rows("shared_current", outputs, {}, 2020, 2021)
        assert isinstance(rows, list)
        assert len(rows) == 2

    def test_variant_key_in_each_row(self):
        outputs = {"new_infections": {2020: 100.0}}
        rows = build_yearly_rows("msm_chronic_non_msm_chronic", outputs, {}, 2020, 2020)
        assert rows[0]["variant"] == "msm_chronic_non_msm_chronic"

    def test_non_msm_derived_in_rows(self):
        outputs = {
            "new_infections":    {2020: 100.0},
            "new_diagnoses":     {2020: 300.0},
            "new_diagnoses_msm": {2020: 200.0},
        }
        rows = build_yearly_rows("v", outputs, {}, 2020, 2020)
        assert rows[0]["new_diagnoses_non_msm"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def _make_summary_df(self):
        return pd.DataFrame([{
            "variant": "shared_current",
            "diag_2024_total_ratio":            1.0,
            "diag_2024_msm_ratio":              1.0,
            "diag_2024_non_msm_ratio":          0.0,
            "msm_diag_1996_2003_max_ratio":     3.5,
            "msm_diag_1996_2014_mape":          45.0,
            "plhiv_2015_2024_mape":             5.0,
            "art_2015_2024_mape":               4.0,
            "aids_death_bounds_fraction":       0.5,
            "aids_death_early_peak_max_ratio":  2.0,
            "aids_death_1985_1995_mape":        30.0,
            "incidence_2015_2024_median_ratio": 0.95,
            "non_msm_hiv_pos_2024":             800.0,
            "non_msm_undiagnosed_stock_2024":   200.0,
            "non_msm_diag_2015_2024_sum":       500.0,
        }])

    def _make_manifest(self):
        return {
            "created_at": "2026-05-28T10:00:00",
            "variants": _VARIANT_KEYS,
            "missing_outputs": [],
        }

    def test_returns_string(self):
        html = build_html_report(self._make_summary_df(), pd.DataFrame(), "/cfg.json", self._make_manifest())
        assert isinstance(html, str)

    def test_html_doctype(self):
        html = build_html_report(self._make_summary_df(), pd.DataFrame(), "/cfg.json", self._make_manifest())
        assert "<!DOCTYPE html>" in html

    def test_variant_keys_in_html(self):
        html = build_html_report(self._make_summary_df(), pd.DataFrame(), "/cfg.json", self._make_manifest())
        for k in _VARIANT_KEYS:
            assert k in html

    def test_metric_labels_in_html(self):
        html = build_html_report(self._make_summary_df(), pd.DataFrame(), "/cfg.json", self._make_manifest())
        assert "AIDS death bounds" in html
        assert "non-MSM diag sum" in html
        assert "PLHIV" in html

    def test_missing_outputs_note_shown(self):
        manifest = self._make_manifest()
        manifest["missing_outputs"] = ["non_msm_hiv_pos", "non_msm_undiagnosed_hiv_pos"]
        html = build_html_report(self._make_summary_df(), pd.DataFrame(), "/cfg.json", manifest)
        assert "non_msm_hiv_pos" in html

    def test_csv_roundtrip(self, tmp_path):
        df = self._make_summary_df()
        out = tmp_path / "summary.csv"
        df.to_csv(out, index=False)
        reread = pd.read_csv(out)
        assert "variant" in reread.columns
        assert len(reread) == 1

    def test_manifest_json_serializable(self, tmp_path):
        manifest = self._make_manifest()
        out = tmp_path / "manifest.json"
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        loaded = json.loads(out.read_text())
        assert set(loaded["variants"]) == set(_VARIANT_KEYS)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_finite_basic(self):
        assert _finite(1.0)
        assert not _finite(None)
        assert not _finite(float("nan"))
        assert not _finite(float("inf"))

    def test_safe_ratio_basic(self):
        assert _safe_ratio(3.0, 4.0) == pytest.approx(0.75)
        assert _safe_ratio(3.0, 0.0) is None
        assert _safe_ratio(None, 4.0) is None
