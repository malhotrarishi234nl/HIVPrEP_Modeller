"""
Lightweight tests for tools/diagnose_msm_diagnosis_layer.py pure helpers.

Covers:
  - _safe_ratio: zero denominator, zero numerator, both-zero
  - _safe_implied_multiplier: model > target gives multiplier < 1;
      model < target gives multiplier > 1; zero model returns None
  - _compute_yearly_row: non-MSM derivation, all ratio fields,
      zero-denominator handling
  - _compute_era_summary: year filtering, statistics, empty subset
  - _multiplier_stability: stable vs time-varying vs insufficient data
  - _load_targets_csv: CSV round-trip with both outputs present and
      missing outputs
"""

from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.diagnose_msm_diagnosis_layer import (
    _compute_era_summary,
    _compute_yearly_row,
    _load_targets_csv,
    _multiplier_stability,
    _safe_implied_multiplier,
    _safe_ratio,
)


# ---------------------------------------------------------------------------
# _safe_ratio
# ---------------------------------------------------------------------------


class TestSafeRatio:
    def test_normal_division(self):
        assert _safe_ratio(6.0, 3.0) == pytest.approx(2.0)

    def test_zero_denominator_returns_none(self):
        assert _safe_ratio(5.0, 0.0) is None

    def test_zero_numerator_nonzero_denom_returns_zero(self):
        assert _safe_ratio(0.0, 5.0) == pytest.approx(0.0)

    def test_both_zero_returns_none(self):
        assert _safe_ratio(0.0, 0.0) is None

    def test_negative_values(self):
        assert _safe_ratio(-4.0, 2.0) == pytest.approx(-2.0)

    def test_large_numerator(self):
        assert _safe_ratio(1_000_000.0, 500.0) == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# _safe_implied_multiplier
# ---------------------------------------------------------------------------


class TestSafeImpliedMultiplier:
    def test_model_gt_target_gives_multiplier_lt_one(self):
        # model = 100, target = 80  →  multiplier = 80/100 = 0.8
        result = _safe_implied_multiplier(target=80.0, model=100.0)
        assert result == pytest.approx(0.8)
        assert result < 1.0

    def test_model_lt_target_gives_multiplier_gt_one(self):
        # model = 100, target = 150  →  multiplier = 150/100 = 1.5
        result = _safe_implied_multiplier(target=150.0, model=100.0)
        assert result == pytest.approx(1.5)
        assert result > 1.0

    def test_perfect_match_gives_one(self):
        assert _safe_implied_multiplier(target=200.0, model=200.0) == pytest.approx(1.0)

    def test_zero_model_returns_none(self):
        assert _safe_implied_multiplier(target=100.0, model=0.0) is None

    def test_zero_target_returns_zero(self):
        assert _safe_implied_multiplier(target=0.0, model=50.0) == pytest.approx(0.0)

    def test_both_zero_returns_none(self):
        assert _safe_implied_multiplier(target=0.0, model=0.0) is None


# ---------------------------------------------------------------------------
# _compute_yearly_row
# ---------------------------------------------------------------------------


class TestComputeYearlyRow:
    def test_basic_fields_present(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 300.0, 350.0)
        assert row["year"] == 2020
        assert row["model_total"] == pytest.approx(500.0)
        assert row["target_total"] == pytest.approx(550.0)
        assert row["model_msm"] == pytest.approx(300.0)
        assert row["target_msm"] == pytest.approx(350.0)

    def test_non_msm_derivation_model(self):
        """model_non_msm = model_total - model_msm."""
        row = _compute_yearly_row(2020, 400.0, 450.0, 250.0, 300.0)
        assert row["model_non_msm"] == pytest.approx(150.0)

    def test_non_msm_derivation_target(self):
        """target_non_msm = target_total - target_msm."""
        row = _compute_yearly_row(2020, 400.0, 450.0, 250.0, 300.0)
        assert row["target_non_msm"] == pytest.approx(150.0)

    def test_msm_model_target_ratio(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 300.0, 200.0)
        # ratio = model_msm / target_msm = 300 / 200 = 1.5
        assert row["msm_model_target_ratio"] == pytest.approx(1.5)

    def test_msm_implied_multiplier(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 300.0, 200.0)
        # multiplier = target_msm / model_msm = 200 / 300 < 1
        assert row["msm_implied_output_multiplier"] == pytest.approx(200.0 / 300.0)
        assert row["msm_implied_output_multiplier"] < 1.0

    def test_zero_model_msm_ratio_is_zero(self):
        # model_msm=0 with non-zero target is valid: ratio = 0/350 = 0.0 (model produced nothing).
        # None is reserved for zero *denominator* (target_msm=0).
        row = _compute_yearly_row(2020, 500.0, 550.0, 0.0, 350.0)
        assert row["msm_model_target_ratio"] == pytest.approx(0.0)

    def test_zero_model_msm_multiplier_is_none(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 0.0, 350.0)
        assert row["msm_implied_output_multiplier"] is None

    def test_zero_target_msm_ratio_is_none(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 300.0, 0.0)
        assert row["msm_model_target_ratio"] is None

    def test_msm_share_model(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 200.0, 300.0)
        assert row["msm_share_model"] == pytest.approx(200.0 / 500.0)

    def test_msm_share_target(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 200.0, 300.0)
        assert row["msm_share_target"] == pytest.approx(300.0 / 550.0)

    def test_non_msm_ratio(self):
        # model_non_msm = 400 - 200 = 200; target_non_msm = 500 - 300 = 200 → ratio = 1.0
        row = _compute_yearly_row(2020, 400.0, 500.0, 200.0, 300.0)
        assert row["non_msm_model_target_ratio"] == pytest.approx(200.0 / 200.0)

    def test_all_expected_keys_present(self):
        row = _compute_yearly_row(2020, 500.0, 550.0, 300.0, 350.0)
        expected_keys = {
            "year", "model_total", "target_total",
            "model_msm", "target_msm",
            "model_non_msm", "target_non_msm",
            "msm_model_target_ratio", "msm_implied_output_multiplier",
            "non_msm_model_target_ratio",
            "msm_share_model", "msm_share_target",
        }
        assert expected_keys == set(row.keys())


# ---------------------------------------------------------------------------
# _compute_era_summary
# ---------------------------------------------------------------------------


class TestComputeEraSummary:
    def _sample_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"year": 1996, "msm_model_target_ratio": 4.0, "msm_implied_output_multiplier": 0.25,
             "non_msm_model_target_ratio": 1.1, "msm_share_model": 0.8, "msm_share_target": 0.6},
            {"year": 2000, "msm_model_target_ratio": 3.0, "msm_implied_output_multiplier": 0.33,
             "non_msm_model_target_ratio": 0.9, "msm_share_model": 0.7, "msm_share_target": 0.5},
            {"year": 2010, "msm_model_target_ratio": 1.5, "msm_implied_output_multiplier": 0.67,
             "non_msm_model_target_ratio": 1.0, "msm_share_model": 0.6, "msm_share_target": 0.4},
            {"year": 2020, "msm_model_target_ratio": 1.2, "msm_implied_output_multiplier": 0.83,
             "non_msm_model_target_ratio": 0.95, "msm_share_model": 0.55, "msm_share_target": 0.45},
        ])

    def test_era_filters_to_year_range(self):
        summary = _compute_era_summary(self._sample_df(), "early", 1996, 2003)
        # Only 1996 and 2000 are in range
        assert summary["n_years"] == 2

    def test_era_name_stored(self):
        summary = _compute_era_summary(self._sample_df(), "test_era", 1996, 2025)
        assert summary["era_name"] == "test_era"

    def test_year_bounds_stored(self):
        summary = _compute_era_summary(self._sample_df(), "e", 1996, 2003)
        assert summary["year_min"] == 1996
        assert summary["year_max"] == 2003

    def test_median_msm_ratio(self):
        # Years 1996 (4.0) and 2000 (3.0) → median = 3.5
        summary = _compute_era_summary(self._sample_df(), "early", 1996, 2003)
        assert summary["median_msm_ratio"] == pytest.approx(3.5)

    def test_max_msm_ratio(self):
        summary = _compute_era_summary(self._sample_df(), "early", 1996, 2003)
        assert summary["max_msm_ratio"] == pytest.approx(4.0)

    def test_min_msm_ratio(self):
        summary = _compute_era_summary(self._sample_df(), "early", 1996, 2003)
        assert summary["min_msm_ratio"] == pytest.approx(3.0)

    def test_median_implied_multiplier(self):
        # 0.25 and 0.33 → median = 0.29
        summary = _compute_era_summary(self._sample_df(), "early", 1996, 2003)
        assert summary["median_implied_multiplier"] == pytest.approx(
            (0.25 + 0.33) / 2.0, rel=1e-2
        )

    def test_empty_year_range_returns_none_stats(self):
        summary = _compute_era_summary(self._sample_df(), "none", 1800, 1850)
        assert summary["n_years"] == 0
        assert summary["median_msm_ratio"] is None
        assert summary["max_msm_ratio"] is None
        assert summary["min_msm_ratio"] is None
        assert summary["median_implied_multiplier"] is None

    def test_empty_dataframe_returns_none_stats(self):
        empty_df = pd.DataFrame(
            columns=["year", "msm_model_target_ratio", "msm_implied_output_multiplier",
                     "non_msm_model_target_ratio", "msm_share_model", "msm_share_target"]
        )
        summary = _compute_era_summary(empty_df, "empty", 1990, 2025)
        assert summary["n_years"] == 0
        assert summary["median_msm_ratio"] is None

    def test_none_values_excluded_from_stats(self):
        df = pd.DataFrame([
            {"year": 2000, "msm_model_target_ratio": None, "msm_implied_output_multiplier": None,
             "non_msm_model_target_ratio": None, "msm_share_model": None, "msm_share_target": None},
            {"year": 2001, "msm_model_target_ratio": 2.5, "msm_implied_output_multiplier": 0.4,
             "non_msm_model_target_ratio": 1.0, "msm_share_model": 0.6, "msm_share_target": 0.5},
        ])
        summary = _compute_era_summary(df, "mixed", 2000, 2001)
        assert summary["n_years"] == 2
        # Only 2001 has a finite ratio → median = 2.5
        assert summary["median_msm_ratio"] == pytest.approx(2.5)

    def test_single_year_latest_only(self):
        df = self._sample_df()
        summary = _compute_era_summary(df, "latest", 2020, 2020)
        assert summary["n_years"] == 1
        assert summary["median_msm_ratio"] == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# _multiplier_stability
# ---------------------------------------------------------------------------


class TestMultiplierStability:
    def test_stable_low_cv(self):
        # Values all close to 0.5 → CV well below 0.25
        values = [0.48, 0.50, 0.52, 0.49, 0.51]
        assert _multiplier_stability(values) == "stable"

    def test_time_varying_high_cv(self):
        # Values spread from 0.1 to 3.0 → high CV
        values = [0.1, 3.0, 0.2, 2.8, 0.15]
        assert _multiplier_stability(values) == "time_varying"

    def test_single_value_insufficient_data(self):
        assert _multiplier_stability([0.5]) == "insufficient_data"

    def test_empty_list_insufficient_data(self):
        assert _multiplier_stability([]) == "insufficient_data"

    def test_all_none_insufficient_data(self):
        assert _multiplier_stability([None, None]) == "insufficient_data"  # type: ignore

    def test_mixed_none_and_finite_uses_finite_only(self):
        # Only two finite values: 0.5 and 0.51 → very stable
        values = [None, 0.5, None, 0.51]
        assert _multiplier_stability(values) == "stable"  # type: ignore


# ---------------------------------------------------------------------------
# _load_targets_csv (I/O helper — using tmp_path fixture)
# ---------------------------------------------------------------------------


class TestLoadTargetsCsv:
    def _make_csv(self, tmp_path, rows: list[dict]) -> Path:
        df = pd.DataFrame(rows)
        path = tmp_path / "targets.csv"
        df.to_csv(path, index=False)
        return path

    def _sample_rows(self) -> list[dict]:
        return [
            {"model_output": "new_diagnoses",     "year": 2020, "estimate": 500.0,
             "lower": 400.0, "upper": 600.0, "sigma": 50.0, "calibration_use": "primary"},
            {"model_output": "new_diagnoses",     "year": 2021, "estimate": 520.0,
             "lower": None,  "upper": None,  "sigma": 52.0, "calibration_use": "primary"},
            {"model_output": "new_diagnoses_msm", "year": 2020, "estimate": 300.0,
             "lower": 250.0, "upper": 350.0, "sigma": 30.0, "calibration_use": "secondary"},
            {"model_output": "new_diagnoses_msm", "year": 2021, "estimate": 310.0,
             "lower": None,  "upper": None,  "sigma": 31.0, "calibration_use": "secondary"},
            # Should be ignored — different output
            {"model_output": "PLHIV", "year": 2020, "estimate": 25000.0,
             "lower": None, "upper": None, "sigma": 2500.0, "calibration_use": "primary"},
        ]

    def test_loads_both_outputs(self, tmp_path):
        path = self._make_csv(tmp_path, self._sample_rows())
        targets = _load_targets_csv(path, start_year=2019, end_year=2022)
        assert "new_diagnoses" in targets
        assert "new_diagnoses_msm" in targets

    def test_plhiv_not_included(self, tmp_path):
        path = self._make_csv(tmp_path, self._sample_rows())
        targets = _load_targets_csv(path, start_year=2019, end_year=2022)
        assert "PLHIV" not in targets

    def test_estimates_correct(self, tmp_path):
        path = self._make_csv(tmp_path, self._sample_rows())
        targets = _load_targets_csv(path, start_year=2019, end_year=2022)
        assert targets["new_diagnoses"][2020]["estimate"] == pytest.approx(500.0)
        assert targets["new_diagnoses_msm"][2020]["estimate"] == pytest.approx(300.0)

    def test_year_range_filtering(self, tmp_path):
        path = self._make_csv(tmp_path, self._sample_rows())
        targets = _load_targets_csv(path, start_year=2021, end_year=2025)
        # 2020 should be excluded
        assert 2020 not in targets.get("new_diagnoses", {})
        assert 2021 in targets.get("new_diagnoses", {})

    def test_missing_msm_output_prints_warning(self, tmp_path, capsys):
        rows_no_msm = [
            {"model_output": "new_diagnoses", "year": 2020, "estimate": 500.0,
             "lower": None, "upper": None, "sigma": 50.0, "calibration_use": "primary"},
        ]
        path = self._make_csv(tmp_path, rows_no_msm)
        targets = _load_targets_csv(path, start_year=2019, end_year=2022)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out or "new_diagnoses_msm" in captured.out
        assert "new_diagnoses_msm" not in targets
