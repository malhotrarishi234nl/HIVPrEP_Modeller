"""
Tests for tools/audit_structural_capacity.py — pure logic only.

No model simulation is run.  All tests work on synthetic in-memory DataFrames
or small CSV files written to tmp_path.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_structural_capacity import (
    _CAPACITY_CRITERIA,
    _IMPLAUSIBILITY_THRESHOLD,
    _V13_10_DEFENSIBLE_BOUNDS,
    build_capacity_summary,
    build_html_report,
    build_implausibility_summary,
    classify_all,
    classify_capacity,
    compute_implausibility_score,
    compute_per_param_implausibility,
    evaluate_capacity_criteria,
    is_evaluable_row,
    param_implausibility,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_pass_row(**overrides) -> pd.Series:
    """Return a row that satisfies all 8 capacity criteria, low implausibility."""
    base = {
        # All 8 capacity criteria satisfied
        "acceptance_diagnosis_gate_new_diagnoses_ratio":       1.05,
        "acceptance_diagnosis_gate_new_diagnoses_msm_ratio":   1.00,
        "acceptance_plhiv_gate_recent_median_abs_pct":         5.0,
        "acceptance_art_stock_gate_recent_median_abs_pct":     4.0,
        "acceptance_incidence_gate_recent_median_model_target_ratio": 1.10,
        "acceptance_aids_death_gate_bounds_fraction":          0.65,
        "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape":  25.0,
        "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": 2.00,
        # All params inside V13.10 defensible bounds → implausibility = 0
        "p_msm":                   0.07,
        "seed_prevalence":         0.002,
        "seed_msm_fraction":       0.90,
        "seed_chronic_share":      0.05,
        "seed_aids_share":         0.02,
        "orient_female":           0.50,
        "orient_msm":              2.50,
        "epsilon_hetero":          0.30,
        "epsilon_msm":             0.50,
        "activity_med":            0.30,
        "activity_high":           0.08,
        "contact_low":             3.0,
        "contact_med":             10.0,
        "contact_high":            35.0,
        "diagnosis_1985":          0.10,
        "diagnosis_1990":          0.20,
        "diagnosis_1997":          0.35,
        "diagnosis_2015":          0.55,
        "msm_diag_mult_1996":      0.40,
        "msm_diag_mult_2004":      0.70,
        "msm_diag_mult_2015":      0.70,
        "art_init_1996":           0.20,
        "art_init_2000":           0.65,
        "art_init_2015":           0.80,
        "art_init_2020":           0.90,
        "art_dropout_rate":        0.01,
        "chronic_to_aids":         0.065,
        "chronic_to_aids_on_art":  0.008,
        "aids_mortality":          0.25,
        "art_mortality_reduction": 0.85,
    }
    base.update(overrides)
    return pd.Series(base)


# ---------------------------------------------------------------------------
# param_implausibility
# ---------------------------------------------------------------------------


class TestParamImplausibility:
    def test_inside_bounds_gives_zero(self):
        assert param_implausibility(0.5, 0.0, 1.0) == pytest.approx(0.0)

    def test_at_lower_bound_gives_zero(self):
        assert param_implausibility(0.0, 0.0, 1.0) == pytest.approx(0.0)

    def test_at_upper_bound_gives_zero(self):
        assert param_implausibility(1.0, 0.0, 1.0) == pytest.approx(0.0)

    def test_below_lower_bound_proportional(self):
        # value=0.0, bounds=[0.2, 1.0], range=0.8 → (0.2-0.0)/0.8 = 0.25
        assert param_implausibility(0.0, 0.2, 1.0) == pytest.approx(0.25)

    def test_above_upper_bound_proportional(self):
        # value=1.6, bounds=[0.2, 1.0], range=0.8 → (1.6-1.0)/0.8 = 0.75
        assert param_implausibility(1.6, 0.2, 1.0) == pytest.approx(0.75)

    def test_double_exceedance_doubles_score(self):
        s1 = param_implausibility(0.40, 0.15, 0.35)  # 0.40 > 0.35 by 0.05, range=0.20 → 0.25
        s2 = param_implausibility(0.45, 0.15, 0.35)  # 0.45 > 0.35 by 0.10, range=0.20 → 0.50
        assert s2 == pytest.approx(2 * s1)

    def test_zero_range_returns_zero(self):
        # defensible_max == defensible_min → no penalty
        assert param_implausibility(5.0, 1.0, 1.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_implausibility_score
# ---------------------------------------------------------------------------


class TestComputeImplausibilityScore:
    def test_all_inside_bounds_gives_zero(self):
        row = _all_pass_row()
        assert compute_implausibility_score(row) == pytest.approx(0.0)

    def test_one_param_outside_gives_positive(self):
        # aids_mortality=0.70 > defensible max 0.35
        row = _all_pass_row(aids_mortality=0.70)
        score = compute_implausibility_score(row)
        assert score > 0.0

    def test_score_increases_with_exceedance(self):
        s1 = compute_implausibility_score(_all_pass_row(aids_mortality=0.40))
        s2 = compute_implausibility_score(_all_pass_row(aids_mortality=0.55))
        assert s2 > s1

    def test_missing_params_give_zero_contribution(self):
        row = pd.Series({})  # No params
        assert compute_implausibility_score(row) == pytest.approx(0.0)

    def test_nan_param_gives_zero_contribution(self):
        row = _all_pass_row(aids_mortality=float("nan"))
        # Should equal same score as all inside bounds (NaN treated as missing)
        assert compute_implausibility_score(row) == pytest.approx(0.0)

    def test_multiple_outside_params_add(self):
        # Two params outside → score = sum of individual contributions
        row1 = _all_pass_row(aids_mortality=0.70)
        row2 = _all_pass_row(chronic_to_aids=0.20)
        row_both = _all_pass_row(aids_mortality=0.70, chronic_to_aids=0.20)
        s1 = compute_implausibility_score(row1)
        s2 = compute_implausibility_score(row2)
        sb = compute_implausibility_score(row_both)
        assert sb == pytest.approx(s1 + s2)


class TestComputePerParamImplausibility:
    def test_returns_dict_for_all_known_params(self):
        row = _all_pass_row()
        result = compute_per_param_implausibility(row)
        assert set(result.keys()) == set(_V13_10_DEFENSIBLE_BOUNDS.keys())

    def test_inside_bounds_all_zero(self):
        row = _all_pass_row()
        result = compute_per_param_implausibility(row)
        assert all(v == pytest.approx(0.0) for v in result.values())

    def test_outside_bounds_positive(self):
        row = _all_pass_row(aids_mortality=0.70)
        result = compute_per_param_implausibility(row)
        assert result["aids_mortality"] > 0.0
        # Other params still zero
        assert result["p_msm"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# evaluate_capacity_criteria
# ---------------------------------------------------------------------------


class TestEvaluateCapacityCriteria:
    def test_all_pass_returns_eight_pass(self):
        row = _all_pass_row()
        ev = evaluate_capacity_criteria(row)
        assert ev["n_pass"] == 8
        assert ev["n_fail"] == 0
        assert ev["n_missing"] == 0
        assert ev["has_extreme_failure"] is False

    def test_single_fail_counted(self):
        # Fail PLHIV criterion (set to 15% > 10%)
        row = _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=15.0)
        ev = evaluate_capacity_criteria(row)
        assert ev["n_fail"] == 1
        assert ev["n_pass"] == 7

    def test_missing_column_counted_as_missing(self):
        row = _all_pass_row()
        row = row.drop("acceptance_plhiv_gate_recent_median_abs_pct")
        ev = evaluate_capacity_criteria(row)
        assert ev["n_missing"] >= 1

    def test_extreme_failure_total_diag_below_0_70(self):
        row = _all_pass_row(acceptance_diagnosis_gate_new_diagnoses_ratio=0.65)
        ev = evaluate_capacity_criteria(row)
        assert ev["has_extreme_failure"] is True

    def test_extreme_failure_aids_bounds_below_0_20(self):
        row = _all_pass_row(acceptance_aids_death_gate_bounds_fraction=0.10)
        ev = evaluate_capacity_criteria(row)
        assert ev["has_extreme_failure"] is True

    def test_extreme_failure_msm_early_max_above_4(self):
        row = _all_pass_row(hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio=4.50)
        ev = evaluate_capacity_criteria(row)
        assert ev["has_extreme_failure"] is True

    def test_borderline_total_diag_0_70_not_extreme(self):
        # Exactly 0.70 is NOT below 0.70 → no extreme failure (but fails capacity criterion)
        row = _all_pass_row(acceptance_diagnosis_gate_new_diagnoses_ratio=0.70)
        ev = evaluate_capacity_criteria(row)
        assert ev["has_extreme_failure"] is False

    def test_empty_row_all_missing(self):
        ev = evaluate_capacity_criteria(pd.Series({}))
        assert ev["n_missing"] == 8
        assert ev["n_fail"] == 0
        assert ev["has_extreme_failure"] is False


# ---------------------------------------------------------------------------
# classify_capacity — all four categories
# ---------------------------------------------------------------------------


class TestClassifyCapacity:
    def test_all_pass_low_implaus_gives_capacity_pass(self):
        assert classify_capacity(_all_pass_row()) == "capacity_pass"

    def test_all_pass_high_implaus_gives_mathematical_only(self):
        # Push many params far outside defensible bounds to exceed threshold
        row = _all_pass_row(
            aids_mortality=0.70,          # way outside [0.15, 0.35]
            contact_high=80.0,            # outside [18, 60]
            seed_prevalence=0.006,        # outside [0.001, 0.0035]
            art_mortality_reduction=0.40, # outside [0.75, 0.97]
        )
        label = classify_capacity(row)
        assert label == "mathematical_only_fit"

    def test_one_failure_not_extreme_gives_near_pass(self):
        # Fail PLHIV (15% > 10%), not extreme
        row = _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=15.0)
        assert classify_capacity(row) == "near_capacity_pass"

    def test_two_failures_not_extreme_gives_near_pass(self):
        # Fail PLHIV (15%) and ART (15%), neither extreme
        row = _all_pass_row(
            acceptance_plhiv_gate_recent_median_abs_pct=15.0,
            acceptance_art_stock_gate_recent_median_abs_pct=15.0,
        )
        assert classify_capacity(row) == "near_capacity_pass"

    def test_three_failures_gives_not_capacity_fit(self):
        row = _all_pass_row(
            acceptance_plhiv_gate_recent_median_abs_pct=15.0,
            acceptance_art_stock_gate_recent_median_abs_pct=15.0,
            hist_metric_msm_diag_hist_gate_msm_diag_hist_mape=50.0,
        )
        assert classify_capacity(row) == "not_capacity_fit"

    def test_one_failure_but_extreme_gives_not_capacity_fit(self):
        # Only 1 criterion fails but it's the extreme total_diag < 0.70
        row = _all_pass_row(acceptance_diagnosis_gate_new_diagnoses_ratio=0.65)
        assert classify_capacity(row) == "not_capacity_fit"

    def test_two_failures_with_extreme_aids_bounds_gives_not_capacity_fit(self):
        row = _all_pass_row(
            acceptance_plhiv_gate_recent_median_abs_pct=15.0,     # non-extreme fail
            acceptance_aids_death_gate_bounds_fraction=0.10,       # extreme fail
        )
        assert classify_capacity(row) == "not_capacity_fit"

    def test_near_pass_exact_two_failures_boundary(self):
        # Exactly 2 failures, neither extreme → near_capacity_pass
        row = _all_pass_row(
            acceptance_diagnosis_gate_new_diagnoses_ratio=1.60,    # outside [0.90,1.50]
            acceptance_plhiv_gate_recent_median_abs_pct=12.0,     # > 10%
        )
        # total_diag=1.60 is NOT below 0.70, so no extreme failure
        result = classify_capacity(row)
        assert result == "near_capacity_pass"

    def test_completely_empty_row_gives_not_capacity_fit(self):
        # Missing metrics are not capacity evidence.
        result = classify_capacity(pd.Series({}))
        assert result == "not_capacity_fit"

    def test_error_row_gives_not_capacity_fit(self):
        row = _all_pass_row(error="ValueError: invalid parameters")
        assert classify_capacity(row) == "not_capacity_fit"

    def test_collapsed_row_gives_not_capacity_fit(self):
        row = _all_pass_row(collapsed=True)
        assert classify_capacity(row) == "not_capacity_fit"

    def test_implausibility_below_threshold_gives_capacity_pass(self):
        # aids_mortality=0.70: excess = 0.70-0.35 = 0.35, range = 0.20 → contrib = 1.75 < 2.0
        row = _all_pass_row(aids_mortality=0.70)
        score = compute_implausibility_score(row)
        assert score < _IMPLAUSIBILITY_THRESHOLD
        assert classify_capacity(row) == "capacity_pass"

    def test_implausibility_above_threshold_gives_mathematical_only(self):
        # aids_mortality=0.80: excess = 0.80-0.35 = 0.45, range = 0.20 → contrib = 2.25 > 2.0
        row = _all_pass_row(aids_mortality=0.80)
        score = compute_implausibility_score(row)
        assert score > _IMPLAUSIBILITY_THRESHOLD
        assert classify_capacity(row) == "mathematical_only_fit"


# ---------------------------------------------------------------------------
# classify_all (vectorised)
# ---------------------------------------------------------------------------


class TestClassifyAll:
    def test_returns_series_same_length(self):
        df = pd.DataFrame([_all_pass_row().to_dict() for _ in range(5)])
        result = classify_all(df)
        assert len(result) == 5

    def test_all_capacity_pass_when_all_criteria_met(self):
        df = pd.DataFrame([_all_pass_row().to_dict() for _ in range(3)])
        result = classify_all(df)
        assert (result == "capacity_pass").all()

    def test_mixed_classifications(self):
        rows = [
            _all_pass_row().to_dict(),
            _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=15.0).to_dict(),
            _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=15.0,
                          acceptance_art_stock_gate_recent_median_abs_pct=15.0,
                          hist_metric_msm_diag_hist_gate_msm_diag_hist_mape=50.0).to_dict(),
        ]
        df = pd.DataFrame(rows)
        result = classify_all(df)
        assert result.iloc[0] == "capacity_pass"
        assert result.iloc[1] == "near_capacity_pass"
        assert result.iloc[2] == "not_capacity_fit"


class TestIsEvaluableRow:
    def test_valid_completed_row_is_evaluable(self):
        row = _all_pass_row(collapsed=False, abc_distance=1.0)
        assert is_evaluable_row(row) is True

    def test_error_row_is_not_evaluable(self):
        row = _all_pass_row(error="ValueError: invalid")
        assert is_evaluable_row(row) is False

    def test_infinite_distance_is_not_evaluable(self):
        row = _all_pass_row(collapsed=False, abc_distance=float("inf"))
        assert is_evaluable_row(row) is False


# ---------------------------------------------------------------------------
# build_implausibility_summary
# ---------------------------------------------------------------------------


class TestBuildImplausibilitySummary:
    def test_returns_dataframe(self):
        df = pd.DataFrame([_all_pass_row().to_dict() for _ in range(5)])
        df["implausibility_score"] = [compute_implausibility_score(row) for _, row in df.iterrows()]
        result = build_implausibility_summary(df, top_n=5)
        assert isinstance(result, pd.DataFrame)

    def test_has_required_columns(self):
        df = pd.DataFrame([_all_pass_row().to_dict() for _ in range(3)])
        df["implausibility_score"] = 0.0
        result = build_implausibility_summary(df, top_n=3)
        for col in ("param", "n_outside_bounds", "mean_implausibility", "max_implausibility"):
            assert col in result.columns

    def test_all_zero_when_all_inside_bounds(self):
        df = pd.DataFrame([_all_pass_row().to_dict() for _ in range(5)])
        df["implausibility_score"] = 0.0
        result = build_implausibility_summary(df, top_n=5)
        assert (result["n_outside_bounds"] == 0).all()
        assert (result["mean_implausibility"] == 0).all()

    def test_outside_param_detected(self):
        row = _all_pass_row(aids_mortality=0.70)
        df = pd.DataFrame([row.to_dict()])
        df["implausibility_score"] = [compute_implausibility_score(row)]
        result = build_implausibility_summary(df, top_n=1)
        aids_row = result[result["param"] == "aids_mortality"]
        assert len(aids_row) == 1
        assert aids_row.iloc[0]["n_outside_bounds"] == 1
        assert aids_row.iloc[0]["mean_implausibility"] > 0.0

    def test_empty_df_returns_empty(self):
        result = build_implausibility_summary(pd.DataFrame(), top_n=10)
        assert result.empty

    def test_error_rows_are_excluded(self):
        good = _all_pass_row(aids_mortality=0.70, collapsed=False, abc_distance=1.0).to_dict()
        bad = _all_pass_row(aids_mortality=0.90, collapsed=True, abc_distance=float("inf"), error="bad").to_dict()
        df = pd.DataFrame([bad, good])
        df["implausibility_score"] = [99.0, compute_implausibility_score(pd.Series(good))]
        result = build_implausibility_summary(df, top_n=2)
        aids_row = result[result["param"] == "aids_mortality"]
        assert len(aids_row) == 1
        assert aids_row.iloc[0]["n_outside_bounds"] == 1


# ---------------------------------------------------------------------------
# build_capacity_summary
# ---------------------------------------------------------------------------


class TestBuildCapacitySummary:
    def _make_classified_df(self, n_pass=2, n_near=3, n_math=1, n_fail=5):
        rows = []
        for _ in range(n_pass):
            r = _all_pass_row().to_dict()
            r["capacity_classification"] = "capacity_pass"
            r["implausibility_score"] = 0.1
            r["n_capacity_criteria_fail"] = 0
            rows.append(r)
        for _ in range(n_near):
            r = _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=15.0).to_dict()
            r["capacity_classification"] = "near_capacity_pass"
            r["implausibility_score"] = 0.3
            r["n_capacity_criteria_fail"] = 1
            rows.append(r)
        for _ in range(n_math):
            r = _all_pass_row(aids_mortality=0.70).to_dict()
            r["capacity_classification"] = "mathematical_only_fit"
            r["implausibility_score"] = 3.5
            r["n_capacity_criteria_fail"] = 0
            rows.append(r)
        for _ in range(n_fail):
            r = _all_pass_row(acceptance_plhiv_gate_recent_median_abs_pct=20.0,
                              acceptance_art_stock_gate_recent_median_abs_pct=20.0,
                              hist_metric_msm_diag_hist_gate_msm_diag_hist_mape=60.0).to_dict()
            r["capacity_classification"] = "not_capacity_fit"
            r["implausibility_score"] = 0.0
            r["n_capacity_criteria_fail"] = 3
            rows.append(r)
        return pd.DataFrame(rows)

    def test_counts_correct(self):
        df = self._make_classified_df(n_pass=2, n_near=3, n_math=1, n_fail=5)
        implaus = build_implausibility_summary(df, top_n=5)
        summary = build_capacity_summary(df, implaus, top_n=5)
        assert summary["n_capacity_pass"]       == 2
        assert summary["n_near_capacity_pass"]  == 3
        assert summary["n_mathematical_only"]   == 1
        assert summary["n_not_capacity_fit"]    == 5
        assert summary["n_total"]               == 11

    def test_best_capacity_candidate_not_none(self):
        df = self._make_classified_df(n_pass=1)
        implaus = build_implausibility_summary(df, top_n=5)
        summary = build_capacity_summary(df, implaus, top_n=5)
        assert summary["best_capacity_candidate"] is not None

    def test_best_near_miss_not_none_when_present(self):
        df = self._make_classified_df(n_near=2)
        implaus = build_implausibility_summary(df, top_n=5)
        summary = build_capacity_summary(df, implaus, top_n=5)
        assert summary["best_near_miss"] is not None

    def test_all_required_keys_present(self):
        df = self._make_classified_df()
        implaus = build_implausibility_summary(df, top_n=5)
        summary = build_capacity_summary(df, implaus, top_n=5)
        for k in ("n_capacity_pass", "n_near_capacity_pass", "n_mathematical_only",
                  "n_not_capacity_fit", "n_total", "best_capacity_candidate",
                  "best_near_miss", "implausibility_threshold"):
            assert k in summary, f"Missing key: {k}"


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def _make_summary(self, n_cap=1, n_near=2, n_math=0):
        return {
            "created_at":              "2026-05-28T12:00:00",
            "n_total":                 100,
            "n_capacity_pass":         n_cap,
            "n_near_capacity_pass":    n_near,
            "n_mathematical_only":     n_math,
            "n_not_capacity_fit":      100 - n_cap - n_near - n_math,
            "implausibility_threshold": _IMPLAUSIBILITY_THRESHOLD,
            "best_capacity_candidate": {"particle_id": 1, "implausibility_score": 0.1},
            "best_near_miss":          {"particle_id": 2, "n_capacity_criteria_fail": 1},
            "best_mathematical_only":  None,
            "median_implausibility_passes_and_nearmisses": 0.15,
            "most_implausible_params_top5": ["aids_mortality", "contact_high"],
        }

    def _make_manifest(self):
        return {
            "created_at":         "2026-05-28T12:00:00",
            "particles_path":     "/tmp/particles.csv",
            "frontier_audit_csv": None,
            "top_n":              100,
            "n_total":            100,
            "n_candidates":       3,
            "n_near_misses":      2,
            "implausibility_threshold": _IMPLAUSIBILITY_THRESHOLD,
            "capacity_criteria_count": 8,
        }

    def test_returns_string(self):
        html = build_html_report(
            self._make_summary(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert isinstance(html, str)

    def test_doctype_present(self):
        html = build_html_report(
            self._make_summary(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert "<!DOCTYPE html>" in html

    def test_capacity_pass_count_in_html(self):
        html = build_html_report(
            self._make_summary(n_cap=1), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert "capacity_pass" in html

    def test_structural_incapacity_message_when_no_pass(self):
        html = build_html_report(
            self._make_summary(n_cap=0, n_near=0, n_math=0),
            pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert "structural" in html.lower() or "incapacity" in html.lower()

    def test_mathematical_only_message_when_no_defensible_pass(self):
        summary = self._make_summary(n_cap=0, n_near=0, n_math=3)
        summary["best_mathematical_only"] = {"particle_id": 5}
        html = build_html_report(
            summary, pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert "mathematical" in html.lower() or "implausible" in html.lower()

    def test_ends_with_html_tag(self):
        html = build_html_report(
            self._make_summary(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), self._make_manifest()
        )
        assert html.strip().endswith("</html>")


# ---------------------------------------------------------------------------
# V13.12 config JSON checks
# ---------------------------------------------------------------------------


class TestV13_12Config:
    CONFIG_PATH = ROOT / "configs" / "calib_space_v13_12_structural_capacity_msm_multiplier.json"

    def test_config_file_exists(self):
        assert self.CONFIG_PATH.exists(), \
            f"Config not found: {self.CONFIG_PATH}"

    def test_config_is_valid_json(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        assert "parameters" in cfg
        assert "fixed_parameters" in cfg

    def test_no_stage_distribution_by_orientation_in_parameters(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        param_names = [p["name"] for p in cfg.get("parameters", [])]
        assert "seed_msm_chronic_share" not in param_names, \
            "V13.12 must not use orientation-specific seed params"
        assert "seed_non_msm_chronic_share" not in param_names
        assert "seed_msm_aids_share" not in param_names
        assert "seed_non_msm_aids_share" not in param_names

    def test_no_stage_distribution_by_orientation_config_paths(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        all_entries = (
            cfg.get("parameters", [])
            + cfg.get("fixed_parameters", [])
            + cfg.get("excluded_parameters", [])
        )
        for entry in all_entries:
            cp = entry.get("config_path", "")
            assert "stage_distribution_by_orientation" not in cp, \
                f"V13.12 must not reference stage_distribution_by_orientation: {cp}"

    def test_msm_relative_diagnosis_enabled_is_fixed_true(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        fixed = {p["name"]: p for p in cfg.get("fixed_parameters", [])}
        assert "msm_relative_diagnosis_enabled" in fixed, \
            "V13.12 must fix msm_relative_diagnosis_enabled"
        assert fixed["msm_relative_diagnosis_enabled"]["value"] is True

    def test_msm_diag_mult_params_present(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        param_names = {p["name"] for p in cfg.get("parameters", [])}
        for name in ("msm_diag_mult_1996", "msm_diag_mult_2004", "msm_diag_mult_2015"):
            assert name in param_names, f"Missing V13.10 multiplier param: {name}"

    def test_wide_bounds_wider_than_v13_10(self):
        """Key stress-test params must have wider bounds than V13.10."""
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        params = {p["name"]: p for p in cfg.get("parameters", [])}
        # aids_mortality: V13.10 max=0.35, V13.12 should be wider
        assert params["aids_mortality"]["max"] > 0.35, "aids_mortality max should exceed V13.10 bound"
        assert params["aids_mortality"]["min"] < 0.15, "aids_mortality min should be below V13.10 bound"
        # seed_chronic_share: V13.10 max=0.40, V13.12 should be wider
        assert params["seed_chronic_share"]["max"] > 0.40

    def test_constraints_include_contact_ordering(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        expressions = [c["expression"] for c in cfg.get("constraints", [])]
        joined = " ".join(expressions)
        assert "contact_low" in joined and "contact_med" in joined and "contact_high" in joined

    def test_seed_acute_share_in_excluded(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        excluded_names = {p["name"] for p in cfg.get("excluded_parameters", [])}
        assert "seed_acute_share" in excluded_names, \
            "seed_acute_share must be in excluded_parameters (derived)"

    def test_version_field_present(self):
        with open(self.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        assert "version" in cfg


# ---------------------------------------------------------------------------
# CLI integration: run() writes all expected output files
# ---------------------------------------------------------------------------


class TestRunCli:
    def _write_particle_csv(self, tmp_path: Path, n: int = 10) -> str:
        rows = []
        for i in range(n):
            row = _all_pass_row().to_dict()
            row["particle_id"] = i + 1
            row["generation"]  = 0
            row["abc_distance"] = 0.3 + i * 0.01
            row["collapsed"]    = False
            # Mix of classifications
            if i < 3:
                pass  # all-pass, low implausibility → capacity_pass
            elif i < 5:
                row["acceptance_plhiv_gate_recent_median_abs_pct"] = 15.0  # near pass
            elif i < 7:
                # Math-only: all criteria pass but high implausibility
                row["aids_mortality"] = 0.70
                row["contact_high"]   = 80.0
                row["seed_prevalence"] = 0.006
                row["art_mortality_reduction"] = 0.40
            else:
                row["acceptance_plhiv_gate_recent_median_abs_pct"]    = 20.0
                row["acceptance_art_stock_gate_recent_median_abs_pct"] = 20.0
                row["hist_metric_msm_diag_hist_gate_msm_diag_hist_mape"] = 60.0
            rows.append(row)
        df = pd.DataFrame(rows)
        p = tmp_path / "particles.csv"
        df.to_csv(p, index=False)
        return str(p)

    def test_all_output_files_written(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        for fname in (
            "capacity_summary.json",
            "capacity_candidates.csv",
            "capacity_near_misses.csv",
            "capacity_implausibility.csv",
            "capacity_report.html",
        ):
            assert (out_dir / fname).exists(), f"Missing output: {fname}"

    def test_summary_json_has_required_keys(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out2"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        with open(out_dir / "capacity_summary.json", encoding="utf-8") as f:
            s = json.load(f)
        for k in ("n_total", "n_capacity_pass", "n_near_capacity_pass",
                  "n_mathematical_only", "n_not_capacity_fit"):
            assert k in s

    def test_candidates_csv_contains_classification(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out3"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        df = pd.read_csv(out_dir / "capacity_candidates.csv")
        assert "capacity_classification" in df.columns
        # Should only contain pass/near-pass/math rows
        assert set(df["capacity_classification"]).issubset(
            {"capacity_pass", "near_capacity_pass", "mathematical_only_fit"}
        )

    def test_near_misses_csv_only_near_capacity(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out4"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        df = pd.read_csv(out_dir / "capacity_near_misses.csv")
        if not df.empty:
            assert (df["capacity_classification"] == "near_capacity_pass").all()

    def test_implausibility_csv_has_param_column(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out5"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        df = pd.read_csv(out_dir / "capacity_implausibility.csv")
        assert "param" in df.columns

    def test_report_html_is_valid_html(self, tmp_path):
        path = self._write_particle_csv(tmp_path)
        out_dir = tmp_path / "out6"
        run(particles_path=path, output_dir=str(out_dir), top_n=5)

        html = (out_dir / "capacity_report.html").read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_missing_columns_handled_gracefully(self, tmp_path):
        # CSV with no gate columns at all
        df = pd.DataFrame([{"particle_id": i, "abc_distance": 0.5} for i in range(5)])
        p = tmp_path / "sparse.csv"
        df.to_csv(p, index=False)
        out_dir = tmp_path / "out7"
        # Should not crash
        manifest = run(particles_path=str(p), output_dir=str(out_dir), top_n=5)
        assert manifest["n_total"] == 5
