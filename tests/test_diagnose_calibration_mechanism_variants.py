"""
Tests for tools/diagnose_calibration_mechanism_variants.py — pure logic only.

No model simulation is run in any of these tests.
"""

from __future__ import annotations

import copy
import csv
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnose_calibration_mechanism_variants import (
    _VARIANT_KEYS,
    _VARIANT_MSM_SCHEDULES,
    build_yearly_rows,
    compute_summary_row,
    derive_non_msm_diagnoses,
    extract_config_params,
    build_html_report,
    patch_config_for_variant,
    _inject_msm_multiplier_schedule,
    _finite,
)


# ---------------------------------------------------------------------------
# Minimal config fixture
# ---------------------------------------------------------------------------


def _minimal_config() -> dict:
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
        "transmission_model": {
            "disease": {
                "diagnosis": {
                    "schedule": {
                        "value": {"1985": 0.10, "1990": 0.22, "1997": 0.35, "2015": 0.40}
                    }
                },
                "mortality": {"aids": {"value": 0.22}},
                "advanced": {"art_mortality_reduction": {"value": 0.90}},
            },
            "groups": {
                "advanced": {
                    "orientation_contact_multipliers": {
                        "value": {"Female": 0.85, "MSM": 2.30, "Heterosexual_Male": 1.0}
                    }
                }
            },
        },
        "population": {
            "p_msm": {"value": 0.053},
            "epidemic_seed": {
                "seed_prevalence": {"value": 0.00015},
                "msm_fraction": {"value": 0.85},
                "stage_distribution": {
                    "value": {"Acute": 0.30, "Chronic": 0.60, "AIDS": 0.10}
                },
            },
        },
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {
                        "value": {"1996": 0.05, "2000": 0.15, "2015": 0.60, "2020": 0.80}
                    }
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# patch_config_for_variant: leaves original unchanged
# ---------------------------------------------------------------------------


class TestPatchConfigForVariant:
    def test_original_config_unchanged_after_patch(self):
        """Variant config patching must not modify the original config dict."""
        original = _minimal_config()
        original_copy = copy.deepcopy(original)

        for variant_key in _VARIANT_KEYS:
            _ = patch_config_for_variant(original, variant_key)
            assert original == original_copy, (
                f"Original config was mutated by variant '{variant_key}'"
            )

    def test_baseline_returns_deepcopy(self):
        """Baseline variant returns a deepcopy, not the same object."""
        original = _minimal_config()
        patched = patch_config_for_variant(original, "baseline")
        assert patched is not original
        assert patched == original

    def test_unknown_variant_raises(self):
        """Unknown variant key raises ValueError."""
        with pytest.raises(ValueError, match="Unknown variant"):
            patch_config_for_variant(_minimal_config(), "nonexistent_variant")


# ---------------------------------------------------------------------------
# Multiplier schedule injection
# ---------------------------------------------------------------------------


class TestInjectMsmMultiplierSchedule:
    def test_schedule_applied_to_copy(self):
        """MSM multiplier schedule is written into the copy, not the original."""
        cfg = _minimal_config()
        original_id = id(cfg)
        schedule = {1996: 0.35, 2004: 0.65, 2015: 0.70}

        result = _inject_msm_multiplier_schedule(copy.deepcopy(cfg), schedule)

        diag = (
            result["transmission_model"]["disease"]["diagnosis"]
        )
        assert "msm_relative_diagnosis" in diag
        block = diag["msm_relative_diagnosis"]
        assert block["enabled"]["value"] is True
        sched_vals = block["schedule"]["value"]
        assert sched_vals["1996"] == pytest.approx(0.35)
        assert sched_vals["2004"] == pytest.approx(0.65)
        assert sched_vals["2015"] == pytest.approx(0.70)

    def test_multiplier_only_variant_has_correct_schedule(self):
        """msm_diag_multiplier_only variant gets the {1996:0.35, 2004:0.65, 2015:0.70} schedule."""
        cfg = _minimal_config()
        patched = patch_config_for_variant(cfg, "msm_diag_multiplier_only")
        sched = (
            patched["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]["value"]
        )
        assert float(sched["1996"]) == pytest.approx(0.35)
        assert float(sched["2004"]) == pytest.approx(0.65)
        assert float(sched["2015"]) == pytest.approx(0.70)

    def test_soft_variant_has_correct_schedule(self):
        """msm_diag_multiplier_soft variant gets {1996:0.50, 2004:0.75, 2015:0.75}."""
        cfg = _minimal_config()
        patched = patch_config_for_variant(cfg, "msm_diag_multiplier_soft")
        sched = (
            patched["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]["value"]
        )
        assert float(sched["1996"]) == pytest.approx(0.50)
        assert float(sched["2004"]) == pytest.approx(0.75)
        assert float(sched["2015"]) == pytest.approx(0.75)

    def test_strong_variant_has_correct_schedule(self):
        """msm_diag_multiplier_strong variant gets {1996:0.25, 2004:0.65, 2015:0.60}."""
        cfg = _minimal_config()
        patched = patch_config_for_variant(cfg, "msm_diag_multiplier_strong")
        sched = (
            patched["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]["value"]
        )
        assert float(sched["1996"]) == pytest.approx(0.25)
        assert float(sched["2004"]) == pytest.approx(0.65)
        assert float(sched["2015"]) == pytest.approx(0.60)

    def test_combined_current_variant_has_same_as_multiplier_only(self):
        """combined_current and msm_diag_multiplier_only use the same schedule."""
        cfg = _minimal_config()
        only = patch_config_for_variant(cfg, "msm_diag_multiplier_only")
        combined = patch_config_for_variant(cfg, "combined_current")
        sched_only = (
            only["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]["value"]
        )
        sched_combined = (
            combined["transmission_model"]["disease"]["diagnosis"]
            ["msm_relative_diagnosis"]["schedule"]["value"]
        )
        assert sched_only == sched_combined

    def test_baseline_and_seed_current_have_no_msm_block(self):
        """baseline and seed_distribution_current do not inject msm_relative_diagnosis."""
        cfg = _minimal_config()
        for v in ("baseline", "seed_distribution_current"):
            patched = patch_config_for_variant(cfg, v)
            diag = patched["transmission_model"]["disease"]["diagnosis"]
            assert "msm_relative_diagnosis" not in diag, (
                f"Variant '{v}' should not inject msm_relative_diagnosis"
            )


# ---------------------------------------------------------------------------
# derive_non_msm_diagnoses
# ---------------------------------------------------------------------------


class TestDeriveNonMsmDiagnoses:
    def test_basic_subtraction(self):
        """Non-MSM = total - MSM for years where both exist."""
        outputs = {
            "new_diagnoses": {2020: 500.0, 2021: 480.0},
            "new_diagnoses_msm": {2020: 300.0, 2021: 290.0},
        }
        result = derive_non_msm_diagnoses(outputs, [2020, 2021])
        assert result[2020] == pytest.approx(200.0)
        assert result[2021] == pytest.approx(190.0)

    def test_clamped_to_zero_not_negative(self):
        """Result is clamped to 0 when MSM > total (numerical artefact)."""
        outputs = {
            "new_diagnoses": {2020: 100.0},
            "new_diagnoses_msm": {2020: 110.0},
        }
        result = derive_non_msm_diagnoses(outputs, [2020])
        assert result[2020] == pytest.approx(0.0)

    def test_missing_year_returns_none(self):
        """Year absent from outputs returns None."""
        outputs = {
            "new_diagnoses": {2020: 500.0},
            "new_diagnoses_msm": {},
        }
        result = derive_non_msm_diagnoses(outputs, [2020, 2021])
        assert result[2020] is None
        assert result[2021] is None

    def test_empty_outputs_returns_all_none(self):
        """Empty outputs dict returns None for every requested year."""
        result = derive_non_msm_diagnoses({}, [2020, 2021, 2022])
        assert all(v is None for v in result.values())


# ---------------------------------------------------------------------------
# compute_summary_row: handles missing columns safely
# ---------------------------------------------------------------------------


class TestComputeSummaryRowSafe:
    def _make_outputs(self):
        return {
            "PLHIV": {yr: 20000 + yr * 10 for yr in range(1990, 2025)},
            "new_infections": {yr: 400 - (yr - 1990) * 5 for yr in range(1990, 2025)},
            "aids_deaths": {yr: 150 + (yr - 1990) * 2 for yr in range(1990, 2025)},
            "new_diagnoses": {yr: 550 - (yr - 1990) * 3 for yr in range(1990, 2025)},
            "new_diagnoses_msm": {yr: 400 - (yr - 1990) * 2 for yr in range(1990, 2025)},
            "on_art_stock": {yr: 18000 + yr * 50 for yr in range(1990, 2025)},
        }

    def _make_targets(self):
        rows = []
        for yr in range(1990, 2025):
            rows.append({"model_output": "PLHIV", "year": yr, "estimate": 20100 + yr * 10,
                         "lower": 18000, "upper": 24000})
            rows.append({"model_output": "new_infections", "year": yr, "estimate": 380 - (yr - 1990) * 5,
                         "lower": 200, "upper": 600})
            rows.append({"model_output": "aids_deaths", "year": yr, "estimate": 140,
                         "lower": 100, "upper": 200})
            rows.append({"model_output": "new_diagnoses", "year": yr, "estimate": 520,
                         "lower": 450, "upper": 650})
            rows.append({"model_output": "new_diagnoses_msm", "year": yr, "estimate": 350,
                         "lower": 280, "upper": 420})
            rows.append({"model_output": "on_art_stock", "year": yr, "estimate": 19000 + yr * 50,
                         "lower": 16000, "upper": 24000})
        return rows

    def test_returns_dict_with_variant_key(self):
        row = compute_summary_row(
            "baseline", self._make_outputs(), {}, self._make_targets()
        )
        assert row["variant"] == "baseline"

    def test_no_error_when_strat_outputs_empty(self):
        """Summary does not raise when stratified outputs dict is empty."""
        row = compute_summary_row("baseline", self._make_outputs(), {}, self._make_targets())
        assert "non_msm_undiagnosed_stock_2024_exists" in row
        assert row["non_msm_undiagnosed_stock_2024_exists"] is None

    def test_no_error_when_targets_empty(self):
        """Summary does not raise when targets list is empty."""
        row = compute_summary_row("baseline", self._make_outputs(), {}, [])
        assert row["variant"] == "baseline"
        assert row.get("diag_2024_total_ratio") is None

    def test_no_error_when_outputs_empty(self):
        """Summary does not raise when outputs dict is empty."""
        row = compute_summary_row("baseline", {}, {}, self._make_targets())
        assert row["variant"] == "baseline"

    def test_plhiv_mape_is_finite(self):
        row = compute_summary_row("baseline", self._make_outputs(), {}, self._make_targets())
        mape = row.get("plhiv_2015_2024_mape")
        assert mape is not None
        assert _finite(mape)
        assert 0 <= mape < 50

    def test_non_msm_diag_generating_stock_when_positive(self):
        """non_msm_diag_generating_stock_exists_2015_2024 is True when total > MSM."""
        row = compute_summary_row("baseline", self._make_outputs(), {}, self._make_targets())
        assert row["non_msm_diag_generating_stock_exists_2015_2024"] is True


# ---------------------------------------------------------------------------
# build_yearly_rows
# ---------------------------------------------------------------------------


class TestBuildYearlyRows:
    def test_returns_list_of_dicts(self):
        outputs = {
            "new_infections": {2020: 400.0, 2021: 380.0},
            "new_diagnoses": {2020: 550.0, 2021: 530.0},
            "new_diagnoses_msm": {2020: 380.0, 2021: 360.0},
        }
        rows = build_yearly_rows("baseline", outputs, {}, 2020, 2021)
        assert len(rows) == 2
        assert all(r["variant"] == "baseline" for r in rows)
        assert rows[0]["year"] == 2020
        assert rows[1]["year"] == 2021

    def test_non_msm_derived_in_yearly_rows(self):
        outputs = {
            "new_infections": {2020: 400.0},
            "new_diagnoses": {2020: 550.0},
            "new_diagnoses_msm": {2020: 350.0},
        }
        rows = build_yearly_rows("baseline", outputs, {}, 2020, 2020)
        assert rows[0]["new_diagnoses_non_msm"] == pytest.approx(200.0)

    def test_stratified_fields_as_none_when_absent(self):
        outputs = {"new_infections": {2020: 400.0}}
        rows = build_yearly_rows("baseline", outputs, {}, 2020, 2020)
        assert rows[0].get("msm_hiv_pos") is None


# ---------------------------------------------------------------------------
# HTML/CSV writer: creates expected files from synthetic data
# ---------------------------------------------------------------------------


class TestHtmlCsvWriter:
    def _make_summary_df(self):
        return pd.DataFrame([
            {
                "variant": "baseline",
                "diag_2024_total_ratio": 0.95,
                "diag_2024_msm_ratio": 1.05,
                "diag_2024_non_msm_ratio": 0.02,
                "msm_diag_1996_2003_max_ratio": 4.5,
                "msm_diag_1996_2014_mape": 48.0,
                "plhiv_2015_2024_mape": 5.2,
                "art_2015_2024_mape": 3.1,
                "aids_death_bounds_fraction": 0.41,
                "incidence_2015_2024_median_ratio": 1.1,
                "non_msm_undiagnosed_stock_2024_exists": None,
                "non_msm_diag_generating_stock_exists_2015_2024": False,
            }
        ])

    def _make_yearly_df(self):
        return pd.DataFrame([
            {
                "variant": "baseline",
                "year": yr,
                "PLHIV": 20000,
                "new_diagnoses": 500,
                "new_diagnoses_msm": 350,
                "new_diagnoses_non_msm": 150,
                "new_infections": 400,
                "aids_deaths": 150,
                "on_art_stock": 18000,
            }
            for yr in range(2020, 2025)
        ])

    def _make_manifest(self):
        return {
            "created_at": "2026-05-27T12:00:00",
            "variants": ["baseline"],
            "missing_outputs": ["msm_hiv_pos", "non_msm_hiv_pos"],
        }

    def test_build_html_report_returns_string(self):
        html = build_html_report(
            self._make_summary_df(),
            self._make_yearly_df(),
            "/some/config.json",
            self._make_manifest(),
        )
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_html_contains_variant_name(self):
        html = build_html_report(
            self._make_summary_df(),
            self._make_yearly_df(),
            "/some/config.json",
            self._make_manifest(),
        )
        assert "baseline" in html

    def test_html_lists_missing_outputs(self):
        html = build_html_report(
            self._make_summary_df(),
            self._make_yearly_df(),
            "/some/config.json",
            self._make_manifest(),
        )
        assert "msm_hiv_pos" in html

    def test_csv_writer_creates_file(self, tmp_path):
        """Summary DataFrame can be written to CSV and re-read correctly."""
        df = self._make_summary_df()
        out = tmp_path / "mechanism_variant_summary.csv"
        df.to_csv(out, index=False)
        assert out.exists()
        reread = pd.read_csv(out)
        assert "variant" in reread.columns
        assert reread.iloc[0]["variant"] == "baseline"

    def test_yearly_csv_writer_creates_file(self, tmp_path):
        df = self._make_yearly_df()
        out = tmp_path / "mechanism_variant_yearly.csv"
        df.to_csv(out, index=False)
        assert out.exists()
        reread = pd.read_csv(out)
        assert len(reread) == 5
        assert "year" in reread.columns


# ---------------------------------------------------------------------------
# extract_config_params
# ---------------------------------------------------------------------------


class TestExtractConfigParams:
    def test_extracts_p_msm(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("p_msm") == pytest.approx(0.053)

    def test_extracts_seed_shares(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("seed_chronic_share") == pytest.approx(0.60)
        assert params.get("seed_aids_share") == pytest.approx(0.10)
        assert params.get("seed_acute_share") == pytest.approx(0.30)

    def test_extracts_orient_msm(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("orient_msm") == pytest.approx(2.30)

    def test_extracts_diagnosis_schedule(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("diagnosis_1985") == pytest.approx(0.10)
        assert params.get("diagnosis_2015") == pytest.approx(0.40)

    def test_extracts_art_init_schedule(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("art_init_1996") == pytest.approx(0.05)
        assert params.get("art_init_2015") == pytest.approx(0.60)

    def test_extracts_msm_diag_mult_when_present(self):
        cfg = _minimal_config()
        cfg = _inject_msm_multiplier_schedule(cfg, {1996: 0.35, 2004: 0.65, 2015: 0.70})
        params = extract_config_params(cfg)
        assert params.get("msm_diag_mult_1996") == pytest.approx(0.35)
        assert params.get("msm_diag_mult_2004") == pytest.approx(0.65)
        assert params.get("msm_diag_mult_2015") == pytest.approx(0.70)

    def test_no_crash_on_missing_sections(self):
        """extract_config_params does not raise on minimal/empty config."""
        params = extract_config_params({})
        assert isinstance(params, dict)
        assert params.get("p_msm") is None

    def test_extracts_aids_mortality(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("aids_mortality") == pytest.approx(0.22)

    def test_extracts_art_mortality_reduction(self):
        cfg = _minimal_config()
        params = extract_config_params(cfg)
        assert params.get("art_mortality_reduction") == pytest.approx(0.90)
