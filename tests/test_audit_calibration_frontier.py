"""
Tests for tools/audit_calibration_frontier.py — pure logic only.

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

from tools.audit_calibration_frontier import (
    _PARETO_OBJECTIVES,
    _SEED_PARAM_COLS,
    _TRADEOFF_COLS,
    _V13_10_MULT_COLS,
    add_loss_columns,
    build_html_report,
    compute_pareto_front,
    compute_summary_by_run,
    compute_tradeoff_matrix,
    derive_losses,
    is_dominated,
    load_particles,
    parse_label_path,
    run,
)


# ---------------------------------------------------------------------------
# parse_label_path
# ---------------------------------------------------------------------------


class TestParseLabelPath:
    def test_with_equals_separator(self):
        label, path = parse_label_path("v13_9_5=/some/path/particles.csv")
        assert label == "v13_9_5"
        assert path == "/some/path/particles.csv"

    def test_without_separator_uses_stem(self, tmp_path):
        p = tmp_path / "abc_smc_particles.csv"
        p.touch()
        label, path = parse_label_path(str(p))
        assert label == "abc_smc_particles"
        assert path == str(p)

    def test_label_stripped_of_whitespace(self):
        label, path = parse_label_path("  my_run  = /tmp/f.csv")
        assert label == "my_run"
        assert path == "/tmp/f.csv"

    def test_path_with_equals_sign_in_path(self):
        # Only split on the first '='
        label, path = parse_label_path("label=/path/to/a=b.csv")
        assert label == "label"
        assert path == "/path/to/a=b.csv"


# ---------------------------------------------------------------------------
# load_particles
# ---------------------------------------------------------------------------


class TestLoadParticles:
    def _write_csv(self, tmp_path, rows):
        df = pd.DataFrame(rows)
        p = tmp_path / "particles.csv"
        df.to_csv(p, index=False)
        return str(p)

    def test_run_label_column_added(self, tmp_path):
        path = self._write_csv(tmp_path, [{"abc_distance": 1.0}])
        df = load_particles("my_run", path)
        assert "run_label" in df.columns
        assert df.iloc[0]["run_label"] == "my_run"

    def test_existing_columns_preserved(self, tmp_path):
        path = self._write_csv(tmp_path, [{"abc_distance": 0.5, "total_ll": -100.0}])
        df = load_particles("r", path)
        assert "abc_distance" in df.columns
        assert df.iloc[0]["abc_distance"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# derive_losses — individual loss formulas
# ---------------------------------------------------------------------------


class TestDeriveLossesLowerIsBetter:
    def _row(self, **kwargs) -> pd.Series:
        return pd.Series(kwargs)

    def test_total_diag_exact_one_gives_zero(self):
        row = self._row(acceptance_diagnosis_gate_new_diagnoses_ratio=1.0)
        losses = derive_losses(row)
        assert losses["loss_total_diag_2024"] == pytest.approx(0.0)

    def test_total_diag_under_one(self):
        row = self._row(acceptance_diagnosis_gate_new_diagnoses_ratio=0.80)
        losses = derive_losses(row)
        assert losses["loss_total_diag_2024"] == pytest.approx(0.20)

    def test_total_diag_over_one(self):
        row = self._row(acceptance_diagnosis_gate_new_diagnoses_ratio=1.30)
        losses = derive_losses(row)
        assert losses["loss_total_diag_2024"] == pytest.approx(0.30)

    def test_plhiv_mape_divided_by_100(self):
        row = self._row(acceptance_plhiv_gate_recent_median_abs_pct=8.0)
        losses = derive_losses(row)
        assert losses["loss_plhiv_recent"] == pytest.approx(0.08)

    def test_art_mape_divided_by_100(self):
        row = self._row(acceptance_art_stock_gate_recent_median_abs_pct=5.0)
        losses = derive_losses(row)
        assert losses["loss_art_recent"] == pytest.approx(0.05)

    def test_aids_deaths_within_threshold_gives_zero(self):
        # bounds_fraction = 0.80 > 0.40 → loss = max(0, 0.40 - 0.80) = 0
        row = self._row(acceptance_aids_death_gate_bounds_fraction=0.80)
        losses = derive_losses(row)
        assert losses["loss_aids_deaths"] == pytest.approx(0.0)

    def test_aids_deaths_below_threshold(self):
        # bounds_fraction = 0.10 → loss = 0.40 - 0.10 = 0.30
        row = self._row(acceptance_aids_death_gate_bounds_fraction=0.10)
        losses = derive_losses(row)
        assert losses["loss_aids_deaths"] == pytest.approx(0.30)

    def test_aids_peak_above_threshold_gives_zero(self):
        # peak ratio = 3.0 > 2.0 → loss = max(0, 2.0-3.0) = 0
        row = self._row(acceptance_aids_death_gate_peak_to_recent_mean_ratio=3.0)
        losses = derive_losses(row)
        assert losses["loss_aids_peak_shape"] == pytest.approx(0.0)

    def test_aids_peak_below_threshold(self):
        # peak ratio = 1.5 → loss = 2.0 - 1.5 = 0.50
        row = self._row(acceptance_aids_death_gate_peak_to_recent_mean_ratio=1.5)
        losses = derive_losses(row)
        assert losses["loss_aids_peak_shape"] == pytest.approx(0.50)

    def test_msm_hist_mape_divided_by_100(self):
        row = self._row(hist_metric_msm_diag_hist_gate_msm_diag_hist_mape=40.0)
        losses = derive_losses(row)
        assert losses["loss_msm_hist_mape"] == pytest.approx(0.40)

    def test_msm_early_ratio_above_threshold(self):
        # early ratio = 3.0, threshold 2.5 → (3.0-2.5)/2.5 = 0.20
        row = self._row(hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio=3.0)
        losses = derive_losses(row)
        assert losses["loss_msm_early_ratio"] == pytest.approx(0.20)

    def test_msm_early_ratio_below_threshold_gives_zero(self):
        row = self._row(hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio=2.0)
        losses = derive_losses(row)
        assert losses["loss_msm_early_ratio"] == pytest.approx(0.0)

    def test_aids_cases_max_ratio_above_threshold(self):
        # max ratio = 6.0, threshold 4.0 → (6.0-4.0)/4.0 = 0.50
        row = self._row(hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio=6.0)
        losses = derive_losses(row)
        assert losses["loss_aids_cases_max_ratio"] == pytest.approx(0.50)

    def test_missing_column_gives_none(self):
        row = self._row()  # no columns at all
        losses = derive_losses(row)
        assert losses["loss_total_diag_2024"] is None
        assert losses["endpoint_loss"] is None
        assert losses["frontier_loss"] is None

    def test_nan_column_gives_none(self):
        row = self._row(acceptance_diagnosis_gate_new_diagnoses_ratio=float("nan"))
        losses = derive_losses(row)
        assert losses["loss_total_diag_2024"] is None


# ---------------------------------------------------------------------------
# derive_losses — composite losses
# ---------------------------------------------------------------------------


class TestDeriveLossesComposite:
    def _full_row(self) -> pd.Series:
        return pd.Series({
            "acceptance_diagnosis_gate_new_diagnoses_ratio":      1.0,
            "acceptance_diagnosis_gate_new_diagnoses_msm_ratio":  1.0,
            "acceptance_plhiv_gate_recent_median_abs_pct":        5.0,
            "acceptance_art_stock_gate_recent_median_abs_pct":    5.0,
            "acceptance_incidence_gate_recent_median_model_target_ratio": 1.0,
            "acceptance_aids_death_gate_bounds_fraction":         1.0,
            "acceptance_aids_death_gate_peak_to_recent_mean_ratio": 3.0,
            "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape":  10.0,
            "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": 2.0,
            "hist_metric_aids_cases_hist_gate_aids_cases_hist_mape":    10.0,
            "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio": 3.0,
        })

    def test_endpoint_loss_is_sum_of_parts(self):
        row = self._full_row()
        losses = derive_losses(row)
        # total_diag=0, msm_diag=0, plhiv=0.05, art=0.05, inc=0
        assert losses["endpoint_loss"] == pytest.approx(0.10, abs=1e-9)

    def test_historical_loss_is_sum_of_parts(self):
        row = self._full_row()
        losses = derive_losses(row)
        # msm_mape=0.10, msm_early=0, aids_mape=0.10, aids_max=0
        assert losses["historical_loss"] == pytest.approx(0.20, abs=1e-9)

    def test_aids_loss_is_sum_of_parts(self):
        row = self._full_row()
        losses = derive_losses(row)
        # aids_bf=0 (fraction=1 > 0.40), peak=0 (3.0 > 2.0)
        assert losses["aids_loss"] == pytest.approx(0.0)

    def test_frontier_loss_sum_of_composites(self):
        row = self._full_row()
        losses = derive_losses(row)
        expected = losses["endpoint_loss"] + losses["historical_loss"] + losses["aids_loss"]
        assert losses["frontier_loss"] == pytest.approx(expected)

    def test_partial_availability_does_not_crash(self):
        # Only endpoint columns available
        row = pd.Series({
            "acceptance_diagnosis_gate_new_diagnoses_ratio": 0.95,
            "acceptance_plhiv_gate_recent_median_abs_pct": 5.0,
        })
        losses = derive_losses(row)
        # historical and aids parts are None, but endpoint should be non-None
        assert losses["endpoint_loss"] is not None
        assert losses["frontier_loss"] is not None


# ---------------------------------------------------------------------------
# add_loss_columns
# ---------------------------------------------------------------------------


class TestAddLossColumns:
    def test_loss_columns_added_to_frame(self):
        df = pd.DataFrame([{
            "acceptance_diagnosis_gate_new_diagnoses_ratio": 1.0,
            "acceptance_aids_death_gate_bounds_fraction": 0.5,
        }])
        result = add_loss_columns(df)
        assert "loss_total_diag_2024" in result.columns
        assert "frontier_loss"        in result.columns

    def test_original_df_not_mutated(self):
        df = pd.DataFrame([{"acceptance_diagnosis_gate_new_diagnoses_ratio": 1.0}])
        original_cols = list(df.columns)
        _ = add_loss_columns(df)
        assert list(df.columns) == original_cols

    def test_empty_df_returns_loss_columns(self):
        result = add_loss_columns(pd.DataFrame())
        # Empty input returns empty output without crashing
        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# Pareto dominance
# ---------------------------------------------------------------------------


class TestIsDominated:
    def test_clearly_dominated(self):
        # a = [2, 2], b = [1, 1] → b dominates a
        assert is_dominated([2.0, 2.0], [1.0, 1.0]) is True

    def test_not_dominated_equal(self):
        # Same values → neither dominates
        assert is_dominated([1.0, 1.0], [1.0, 1.0]) is False

    def test_not_dominated_tradeoff(self):
        # a = [1, 2], b = [2, 1] → no domination
        assert is_dominated([1.0, 2.0], [2.0, 1.0]) is False

    def test_dominated_by_one_strictly_better(self):
        # a = [1, 2], b = [1, 1] → b is as good on dim0, strictly better on dim1
        assert is_dominated([1.0, 2.0], [1.0, 1.0]) is True

    def test_inf_objectives_dominated_by_finite(self):
        # a has inf on first dim → b = [1, 1] dominates it
        assert is_dominated([math.inf, 1.0], [1.0, 1.0]) is True


class TestComputeParetoFront:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_single_row_always_on_front(self):
        df = self._make_df([{"endpoint_loss": 1.0, "historical_loss": 1.0,
                              "aids_loss": 1.0, "loss_total_diag_2024": 0.5,
                              "loss_msm_hist_mape": 0.5, "loss_aids_deaths": 0.5}])
        result = compute_pareto_front(df, _PARETO_OBJECTIVES)
        assert len(result) == 1

    def test_dominated_row_excluded(self):
        # Row 0 dominates row 1 on all objectives
        df = self._make_df([
            {"endpoint_loss": 0.1, "historical_loss": 0.1, "aids_loss": 0.0,
             "loss_total_diag_2024": 0.0, "loss_msm_hist_mape": 0.1, "loss_aids_deaths": 0.0},
            {"endpoint_loss": 1.0, "historical_loss": 1.0, "aids_loss": 1.0,
             "loss_total_diag_2024": 1.0, "loss_msm_hist_mape": 1.0, "loss_aids_deaths": 1.0},
        ])
        result = compute_pareto_front(df, _PARETO_OBJECTIVES)
        assert len(result) == 1
        assert result.iloc[0]["endpoint_loss"] == pytest.approx(0.1)

    def test_tradeoff_both_on_front(self):
        # row 0 better on endpoint, row 1 better on historical → both non-dominated
        df = self._make_df([
            {"endpoint_loss": 0.1, "historical_loss": 1.0, "aids_loss": 0.5,
             "loss_total_diag_2024": 0.0, "loss_msm_hist_mape": 1.0, "loss_aids_deaths": 0.0},
            {"endpoint_loss": 1.0, "historical_loss": 0.1, "aids_loss": 0.5,
             "loss_total_diag_2024": 1.0, "loss_msm_hist_mape": 0.1, "loss_aids_deaths": 0.0},
        ])
        result = compute_pareto_front(df, _PARETO_OBJECTIVES)
        assert len(result) == 2

    def test_missing_objective_treated_as_inf(self):
        # Row 0 has all values; row 1 missing an objective (treated as inf → dominated)
        df = self._make_df([
            {"endpoint_loss": 0.1, "historical_loss": 0.1, "aids_loss": 0.0,
             "loss_total_diag_2024": 0.0, "loss_msm_hist_mape": 0.0, "loss_aids_deaths": 0.0},
            {"endpoint_loss": None, "historical_loss": None, "aids_loss": None,
             "loss_total_diag_2024": None, "loss_msm_hist_mape": None, "loss_aids_deaths": None},
        ])
        result = compute_pareto_front(df, _PARETO_OBJECTIVES)
        assert len(result) == 1

    def test_empty_df(self):
        df = pd.DataFrame(columns=_PARETO_OBJECTIVES)
        result = compute_pareto_front(df, _PARETO_OBJECTIVES)
        assert result.empty


# ---------------------------------------------------------------------------
# compute_summary_by_run
# ---------------------------------------------------------------------------


class TestComputeSummaryByRun:
    def _make_df(self) -> pd.DataFrame:
        base = {
            "run_label": "v13_9_5",
            "particle_id": 1,
            "generation": 0,
            "abc_distance": 0.5,
            "total_ll": -100.0,
            "overall_accept": True,
            "hist_overall_accept": False,
            "collapsed": False,
            "acceptance_diagnosis_gate_new_diagnoses_ratio": 1.05,
            "acceptance_plhiv_gate_recent_median_abs_pct": 7.0,
            "acceptance_art_stock_gate_recent_median_abs_pct": 4.0,
            "acceptance_incidence_gate_recent_median_model_target_ratio": 1.1,
            "acceptance_aids_death_gate_bounds_fraction": 0.50,
            "acceptance_aids_death_gate_peak_to_recent_mean_ratio": 2.5,
            "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape": 35.0,
            "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": 2.0,
            "hist_metric_aids_cases_hist_gate_aids_cases_hist_mape": 20.0,
            "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio": 3.0,
            "seed_prevalence": 0.002,
            "seed_msm_fraction": 0.8,
        }
        rows = []
        for i in range(5):
            r = dict(base)
            r["particle_id"] = i + 1
            r["abc_distance"] = 0.3 + i * 0.1
            rows.append(r)
        df = add_loss_columns(pd.DataFrame(rows))
        return df

    def test_returns_one_row_per_label(self):
        df = self._make_df()
        result = compute_summary_by_run(df, top_n=3)
        assert len(result) == 1
        assert result.iloc[0]["run_label"] == "v13_9_5"

    def test_n_total_correct(self):
        df = self._make_df()
        result = compute_summary_by_run(df, top_n=3)
        assert result.iloc[0]["n_total"] == 5

    def test_min_abc_distance(self):
        df = self._make_df()
        result = compute_summary_by_run(df, top_n=5)
        assert result.iloc[0]["min_abc_distance"] == pytest.approx(0.3)

    def test_best_particle_abc_is_particle_id_1(self):
        df = self._make_df()
        result = compute_summary_by_run(df, top_n=5)
        assert result.iloc[0]["best_particle_abc"] == 1

    def test_multiple_runs_multiple_rows(self):
        df = self._make_df()
        df2 = df.copy()
        df2["run_label"] = "v13_10"
        combined = pd.concat([df, df2], ignore_index=True)
        result = compute_summary_by_run(combined, top_n=5)
        assert len(result) == 2
        assert set(result["run_label"]) == {"v13_9_5", "v13_10"}

    def test_pass_rate_overall_accept(self):
        df = self._make_df()
        # All rows have overall_accept=True
        result = compute_summary_by_run(df, top_n=5)
        assert result.iloc[0]["pass_rate_overall_accept"] == pytest.approx(1.0)

    def test_median_seed_prevalence_in_output(self):
        df = self._make_df()
        result = compute_summary_by_run(df, top_n=5)
        col = "top5_median_seed_prevalence"
        assert col in result.columns
        assert result.iloc[0][col] == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# compute_tradeoff_matrix (Spearman)
# ---------------------------------------------------------------------------


class TestComputeTradeoffMatrix:
    def _make_df(self) -> pd.DataFrame:
        import numpy as np
        rng = np.random.default_rng(0)
        n = 30
        x = rng.random(n)
        return pd.DataFrame({
            "endpoint_loss":     x,
            "historical_loss":   x + rng.random(n) * 0.1,   # correlated with x
            "aids_loss":         1 - x + rng.random(n) * 0.1,  # anti-correlated
            "seed_prevalence":   rng.random(n),
        })

    def test_returns_square_matrix(self):
        df = self._make_df()
        cols = ["endpoint_loss", "historical_loss", "aids_loss"]
        result = compute_tradeoff_matrix(df, cols)
        assert result.shape == (3, 3)

    def test_diagonal_is_one(self):
        df = self._make_df()
        cols = ["endpoint_loss", "historical_loss"]
        result = compute_tradeoff_matrix(df, cols)
        assert result.at["endpoint_loss",   "endpoint_loss"]   == pytest.approx(1.0)
        assert result.at["historical_loss", "historical_loss"] == pytest.approx(1.0)

    def test_correlated_pair_positive(self):
        df = self._make_df()
        cols = ["endpoint_loss", "historical_loss"]
        result = compute_tradeoff_matrix(df, cols)
        # endpoint_loss and historical_loss are constructed to be correlated
        assert result.at["endpoint_loss", "historical_loss"] > 0.5

    def test_anticorrelated_pair_negative(self):
        df = self._make_df()
        cols = ["endpoint_loss", "aids_loss"]
        result = compute_tradeoff_matrix(df, cols)
        # aids_loss = 1 - x  →  anti-correlated with endpoint_loss = x
        assert result.at["endpoint_loss", "aids_loss"] < -0.5

    def test_missing_columns_ignored(self):
        df = self._make_df()
        cols = ["endpoint_loss", "nonexistent_col", "historical_loss"]
        result = compute_tradeoff_matrix(df, cols)
        # nonexistent_col not included
        assert "nonexistent_col" not in result.columns
        assert result.shape == (2, 2)

    def test_empty_df_returns_empty(self):
        result = compute_tradeoff_matrix(pd.DataFrame(), _TRADEOFF_COLS)
        assert result.empty


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def _make_summary_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "run_label":               "v13_9_5",
            "n_total":                 100,
            "min_abc_distance":        0.35,
            "min_frontier_loss":       0.30,
            "min_endpoint_loss":       0.10,
            "min_historical_loss":     0.15,
            "min_aids_loss":           0.05,
        }])

    def _make_pareto_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "run_label":      "v13_9_5",
            "particle_id":    42,
            "endpoint_loss":  0.10,
            "historical_loss":0.15,
            "aids_loss":      0.05,
            "frontier_loss":  0.30,
        }])

    def _make_all_df(self) -> pd.DataFrame:
        rows = []
        for i in range(5):
            rows.append({
                "run_label":      "v13_9_5",
                "particle_id":    i,
                "generation":     0,
                "abc_distance":   0.3 + i * 0.1,
                "frontier_loss":  0.4 + i * 0.05,
                "endpoint_loss":  0.1,
                "historical_loss":0.1,
                "aids_loss":      0.1,
            })
        return pd.DataFrame(rows)

    def _make_manifest(self, run_labels=None) -> dict:
        return {
            "created_at":        "2026-05-28T10:00:00",
            "run_labels":        run_labels or ["v13_9_5"],
            "row_counts":        {"v13_9_5": 100},
            "total_rows":        100,
            "pareto_n":          1,
            "top_n":             50,
            "pareto_objectives": _PARETO_OBJECTIVES,
            "missing_columns":   [],
        }

    def test_returns_string(self):
        html = build_html_report(
            self._make_summary_df(), self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, self._make_manifest()
        )
        assert isinstance(html, str)

    def test_doctype_present(self):
        html = build_html_report(
            self._make_summary_df(), self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, self._make_manifest()
        )
        assert "<!DOCTYPE html>" in html

    def test_run_label_in_html(self):
        html = build_html_report(
            self._make_summary_df(), self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, self._make_manifest()
        )
        assert "v13_9_5" in html

    def test_pareto_objectives_mentioned(self):
        html = build_html_report(
            self._make_summary_df(), self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, self._make_manifest()
        )
        assert "endpoint_loss" in html
        assert "aids_loss" in html

    def test_missing_columns_note_shown(self):
        manifest = self._make_manifest()
        manifest["missing_columns"] = ["hist_metric_msm_diag_hist_gate_msm_diag_hist_mape"]
        html = build_html_report(
            self._make_summary_df(), self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, manifest
        )
        assert "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape" in html

    def test_no_joint_satisfaction_message_when_high_loss(self):
        summary_df = pd.DataFrame([{
            "run_label":          "v13_9_5",
            "min_endpoint_loss":  2.0,
            "min_historical_loss":2.0,
            "min_aids_loss":      2.0,
            "min_frontier_loss":  6.0,
        }])
        html = build_html_report(
            summary_df, self._make_pareto_df(),
            self._make_all_df(), pd.DataFrame(),
            ["v13_9_5"], 50, self._make_manifest()
        )
        assert "jointly satisfies" in html.lower() or "no candidate" in html.lower()


# ---------------------------------------------------------------------------
# CLI integration: run() writes all expected output files
# ---------------------------------------------------------------------------


class TestRunCli:
    def _write_particle_csv(self, tmp_path: Path, label: str, n: int = 5) -> str:
        rows = []
        for i in range(n):
            rows.append({
                "particle_id":         i + 1,
                "generation":          0,
                "collapsed":           False,
                "abc_distance":        0.3 + i * 0.05,
                "total_ll":            -100.0 - i * 5,
                "overall_accept":      (i % 2 == 0),
                "hist_overall_accept": False,
                "diagnosis_gate_pass": True,
                "plhiv_gate_pass":     True,
                "incidence_gate_pass": True,
                "aids_death_gate_pass":True,
                "art_stock_gate_pass": True,
                "acceptance_diagnosis_gate_new_diagnoses_ratio":      0.95 + i * 0.01,
                "acceptance_diagnosis_gate_new_diagnoses_msm_ratio":  0.90 + i * 0.01,
                "acceptance_plhiv_gate_recent_median_abs_pct":        6.0,
                "acceptance_art_stock_gate_recent_median_abs_pct":    5.0,
                "acceptance_incidence_gate_recent_median_model_target_ratio": 1.1,
                "acceptance_aids_death_gate_bounds_fraction":         0.5 + i * 0.05,
                "acceptance_aids_death_gate_peak_to_recent_mean_ratio": 2.5,
                "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape":  35.0,
                "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": 2.5,
                "hist_metric_diagnoses_hist_gate_diagnoses_hist_mape":     20.0,
                "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio": 3.5,
                "hist_metric_aids_cases_hist_gate_aids_cases_hist_mape":     15.0,
                "seed_prevalence":         0.002,
                "seed_msm_fraction":       0.80,
                "seed_msm_chronic_share":  0.05,
                "seed_msm_aids_share":     0.02,
                "seed_non_msm_chronic_share": 0.10,
                "seed_non_msm_aids_share":    0.05,
            })
        df = pd.DataFrame(rows)
        p = tmp_path / f"particles_{label}.csv"
        df.to_csv(p, index=False)
        return str(p)

    def test_all_output_files_created(self, tmp_path):
        path1 = self._write_particle_csv(tmp_path, "v13_9_5")
        path2 = self._write_particle_csv(tmp_path, "v13_10")
        out_dir = tmp_path / "audit_out"

        manifest = run(
            particles=[f"v13_9_5={path1}", f"v13_10={path2}"],
            output_dir=str(out_dir),
            top_n=3,
        )

        assert (out_dir / "frontier_all_candidates.csv").exists()
        assert (out_dir / "frontier_pareto_candidates.csv").exists()
        assert (out_dir / "frontier_summary_by_run.csv").exists()
        assert (out_dir / "frontier_tradeoff_matrix.csv").exists()
        assert (out_dir / "frontier_report.html").exists()
        assert (out_dir / "frontier_manifest.json").exists()

    def test_manifest_keys(self, tmp_path):
        path = self._write_particle_csv(tmp_path, "run_a")
        out_dir = tmp_path / "audit_out2"

        manifest = run(
            particles=[f"run_a={path}"],
            output_dir=str(out_dir),
            top_n=5,
        )

        for key in ("created_at", "run_labels", "row_counts", "total_rows",
                    "pareto_n", "top_n", "pareto_objectives", "missing_columns"):
            assert key in manifest, f"Missing manifest key: {key}"

    def test_manifest_json_readable(self, tmp_path):
        path = self._write_particle_csv(tmp_path, "run_b")
        out_dir = tmp_path / "audit_out3"
        run(particles=[f"run_b={path}"], output_dir=str(out_dir), top_n=2)

        with open(out_dir / "frontier_manifest.json", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["run_labels"] == ["run_b"]

    def test_all_candidates_csv_has_loss_columns(self, tmp_path):
        path = self._write_particle_csv(tmp_path, "run_c")
        out_dir = tmp_path / "audit_out4"
        run(particles=[f"run_c={path}"], output_dir=str(out_dir), top_n=5)

        df = pd.read_csv(out_dir / "frontier_all_candidates.csv")
        assert "frontier_loss" in df.columns
        assert "endpoint_loss" in df.columns
        assert "loss_aids_deaths" in df.columns

    def test_summary_csv_one_row_per_run(self, tmp_path):
        path1 = self._write_particle_csv(tmp_path, "run_d1")
        path2 = self._write_particle_csv(tmp_path, "run_d2")
        out_dir = tmp_path / "audit_out5"
        run(
            particles=[f"run_d1={path1}", f"run_d2={path2}"],
            output_dir=str(out_dir),
            top_n=5,
        )
        df = pd.read_csv(out_dir / "frontier_summary_by_run.csv")
        assert len(df) == 2
        assert set(df["run_label"]) == {"run_d1", "run_d2"}

    def test_pareto_csv_subset_of_all(self, tmp_path):
        path = self._write_particle_csv(tmp_path, "run_e", n=10)
        out_dir = tmp_path / "audit_out6"
        run(particles=[f"run_e={path}"], output_dir=str(out_dir), top_n=5)

        all_df   = pd.read_csv(out_dir / "frontier_all_candidates.csv")
        pareto_df = pd.read_csv(out_dir / "frontier_pareto_candidates.csv")
        assert len(pareto_df) <= len(all_df)

    def test_html_report_is_valid_html(self, tmp_path):
        path = self._write_particle_csv(tmp_path, "run_f")
        out_dir = tmp_path / "audit_out7"
        run(particles=[f"run_f={path}"], output_dir=str(out_dir), top_n=5)

        html = (out_dir / "frontier_report.html").read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
