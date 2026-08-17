"""
Tests for tools/audit_calibration_constraint_conflicts.py — pure logic only.

No model simulation is run. All tests work on synthetic in-memory DataFrames.
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

from tools.audit_calibration_constraint_conflicts import (
    ALL_GATES,
    GATE_INTERSECTIONS,
    HISTORICAL_GATES,
    METRIC_COLS,
    RECENT_GATES,
    build_frontier_table,
    build_html_report,
    compute_conditional_summaries,
    compute_gate_intersections,
    compute_gate_pass_rates,
    interpret_conflicts,
    is_evaluable_row,
    run,
    summarize_subset_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**overrides) -> dict:
    base = {
        "particle_id": 1,
        "generation": 0,
        "abc_distance": 5.0,
        "acceptance_n_failures": 0,
        "hist_n_failures": 0,
        "collapsed": False,
        "error": "",
        "diagnosis_gate_pass": True,
        "plhiv_gate_pass": True,
        "incidence_gate_pass": True,
        "aids_death_gate_pass": True,
        "art_stock_gate_pass": True,
        "plhiv_hist_gate_pass": True,
        "diagnoses_hist_gate_pass": True,
        "msm_diag_hist_gate_pass": True,
        "aids_cases_hist_gate_pass": True,
        "acceptance_diagnosis_gate_new_diagnoses_ratio": 1.05,
        "acceptance_diagnosis_gate_new_diagnoses_msm_ratio": 1.00,
        "acceptance_plhiv_gate_recent_median_abs_pct": 5.0,
        "acceptance_art_stock_gate_recent_median_abs_pct": 4.0,
        "acceptance_incidence_gate_recent_median_model_target_ratio": 1.10,
        "acceptance_aids_death_gate_bounds_fraction": 0.65,
        "hist_metric_msm_diag_hist_gate_msm_diag_hist_mape": 25.0,
        "hist_metric_msm_diag_hist_gate_msm_diag_early_max_ratio": 2.00,
        "hist_metric_aids_cases_hist_gate_aids_cases_hist_max_ratio": 1.5,
        "seed_prevalence": 0.002,
        "seed_msm_fraction": 0.9,
        "seed_chronic_share": 0.1,
        "seed_aids_share": 0.02,
        "p_msm": 0.07,
    }
    base.update(overrides)
    return base


def _make_df(rows: list[dict] | None = None, n: int = 10) -> pd.DataFrame:
    if rows is not None:
        return pd.DataFrame(rows)
    return pd.DataFrame([_make_row(particle_id=i) for i in range(n)])


# ---------------------------------------------------------------------------
# is_evaluable_row
# ---------------------------------------------------------------------------


class TestIsEvaluableRow:
    def test_normal_row(self):
        assert is_evaluable_row(pd.Series(_make_row())) is True

    def test_error_row(self):
        assert is_evaluable_row(pd.Series(_make_row(error="boom"))) is False

    def test_collapsed_row(self):
        assert is_evaluable_row(pd.Series(_make_row(collapsed=True))) is False

    def test_collapsed_string(self):
        assert is_evaluable_row(pd.Series(_make_row(collapsed="True"))) is False

    def test_inf_distance(self):
        assert is_evaluable_row(pd.Series(_make_row(abc_distance=float("inf")))) is False


# ---------------------------------------------------------------------------
# compute_gate_pass_rates
# ---------------------------------------------------------------------------


class TestGatePassRates:
    def test_all_pass(self):
        df = _make_df(n=5)
        rates = compute_gate_pass_rates(df)
        for gate in ALL_GATES:
            assert rates[gate]["n_pass"] == 5
            assert rates[gate]["rate"] == 1.0

    def test_some_fail(self):
        rows = [
            _make_row(particle_id=0, aids_death_gate_pass=True),
            _make_row(particle_id=1, aids_death_gate_pass=False),
            _make_row(particle_id=2, aids_death_gate_pass=False),
        ]
        df = _make_df(rows)
        rates = compute_gate_pass_rates(df)
        assert rates["aids_death_gate_pass"]["n_pass"] == 1
        assert rates["diagnosis_gate_pass"]["n_pass"] == 3

    def test_missing_column(self):
        df = _make_df(n=3)
        df = df.drop(columns=["aids_death_gate_pass"])
        rates = compute_gate_pass_rates(df)
        assert rates["aids_death_gate_pass"]["n_pass"] == 0
        assert rates["aids_death_gate_pass"].get("missing") is True

    def test_empty_df(self):
        df = _make_df(rows=[])
        rates = compute_gate_pass_rates(df)
        for gate in ALL_GATES:
            assert rates[gate]["rate"] == 0.0


# ---------------------------------------------------------------------------
# compute_gate_intersections
# ---------------------------------------------------------------------------


class TestGateIntersections:
    def test_all_pass_intersection(self):
        df = _make_df(n=5)
        gi = compute_gate_intersections(df)
        for entry in gi:
            assert entry["n_pass"] == 5

    def test_diag_aids_conflict(self):
        rows = [
            _make_row(particle_id=0, diagnosis_gate_pass=True, aids_death_gate_pass=False),
            _make_row(particle_id=1, diagnosis_gate_pass=False, aids_death_gate_pass=True),
            _make_row(particle_id=2, diagnosis_gate_pass=True, aids_death_gate_pass=False),
        ]
        df = _make_df(rows)
        gi = compute_gate_intersections(df)
        diag_aids = next(i for i in gi if i["intersection"] == "diagnosis + aids_death")
        assert diag_aids["n_pass"] == 0

    def test_partial_intersection(self):
        rows = [
            _make_row(particle_id=0),
            _make_row(particle_id=1, plhiv_gate_pass=False),
        ]
        df = _make_df(rows)
        gi = compute_gate_intersections(df)
        all_recent = next(i for i in gi if i["intersection"] == "all_recent")
        assert all_recent["n_pass"] == 1


# ---------------------------------------------------------------------------
# summarize_subset_metrics
# ---------------------------------------------------------------------------


class TestSubsetSummaries:
    def test_basic_summary(self):
        df = _make_df(n=5)
        mask = pd.Series(True, index=df.index)
        s = summarize_subset_metrics(df, mask, "all")
        assert s["n"] == 5
        assert "total_diag_ratio_median" in s
        assert s["total_diag_ratio_median"] == 1.05

    def test_empty_mask(self):
        df = _make_df(n=5)
        mask = pd.Series(False, index=df.index)
        s = summarize_subset_metrics(df, mask, "none")
        assert s["n"] == 0


# ---------------------------------------------------------------------------
# compute_conditional_summaries
# ---------------------------------------------------------------------------


class TestConditionalSummaries:
    def test_produces_pass_and_fail(self):
        df = _make_df(n=5)
        cs = compute_conditional_summaries(df)
        labels = {s["label"] for s in cs}
        assert "pass_diagnosis_gate_pass" in labels
        assert "fail_diagnosis_gate_pass" in labels
        assert "all_particles" in labels

    def test_metrics_populated(self):
        df = _make_df(n=3)
        cs = compute_conditional_summaries(df)
        all_p = next(s for s in cs if s["label"] == "all_particles")
        assert all_p["n"] == 3
        assert "aids_death_bounds_fraction_median" in all_p


# ---------------------------------------------------------------------------
# build_frontier_table
# ---------------------------------------------------------------------------


class TestFrontierTable:
    def test_produces_frontier(self):
        df = _make_df(n=30)
        frontier = build_frontier_table(df, top_n=5)
        assert len(frontier) > 0
        assert "frontier_source" in frontier.columns

    def test_empty_df(self):
        df = _make_df(rows=[])
        frontier = build_frontier_table(df)
        assert frontier.empty

    def test_multiple_sources(self):
        rows = []
        for i in range(30):
            rows.append(_make_row(
                particle_id=i,
                abc_distance=float(i),
                diagnosis_gate_pass=(i % 2 == 0),
                aids_death_gate_pass=(i % 3 == 0),
            ))
        df = _make_df(rows)
        frontier = build_frontier_table(df, top_n=5)
        sources = frontier["frontier_source"].unique()
        assert len(sources) >= 2


# ---------------------------------------------------------------------------
# interpret_conflicts
# ---------------------------------------------------------------------------


class TestInterpretConflicts:
    def test_no_conflict(self):
        df = _make_df(n=5)
        pr = compute_gate_pass_rates(df)
        gi = compute_gate_intersections(df)
        cs = compute_conditional_summaries(df)
        interp = interpret_conflicts(pr, gi, cs)
        assert any("No clear gate conflict" in line for line in interp)

    def test_detects_conflict(self):
        rows = [
            _make_row(particle_id=0, diagnosis_gate_pass=True, aids_death_gate_pass=False,
                      acceptance_aids_death_gate_bounds_fraction=0.05),
            _make_row(particle_id=1, diagnosis_gate_pass=False, aids_death_gate_pass=True,
                      acceptance_diagnosis_gate_new_diagnoses_ratio=0.3),
        ]
        df = _make_df(rows)
        pr = compute_gate_pass_rates(df)
        gi = compute_gate_intersections(df)
        cs = compute_conditional_summaries(df)
        interp = interpret_conflicts(pr, gi, cs)
        assert any("CONFLICT" in line for line in interp)

    def test_structural_tradeoff_note(self):
        rows = [
            _make_row(particle_id=0, diagnosis_gate_pass=True, aids_death_gate_pass=False),
            _make_row(particle_id=1, diagnosis_gate_pass=False, aids_death_gate_pass=True),
        ]
        df = _make_df(rows)
        pr = compute_gate_pass_rates(df)
        gi = compute_gate_intersections(df)
        cs = compute_conditional_summaries(df)
        interp = interpret_conflicts(pr, gi, cs)
        assert any("hallmark" in line for line in interp)


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


class TestHtmlReport:
    def test_produces_html(self):
        df = _make_df(n=5)
        pr = compute_gate_pass_rates(df)
        gi = compute_gate_intersections(df)
        cs = compute_conditional_summaries(df)
        interp = interpret_conflicts(pr, gi, cs)
        frontier = build_frontier_table(df)
        html = build_html_report("test", pr, gi, cs, frontier, interp, {"created_at": "now"})
        assert "<html" in html
        assert "V13.13" in html


# ---------------------------------------------------------------------------
# run (full pipeline on temp files)
# ---------------------------------------------------------------------------


class TestRun:
    def test_single_csv(self, tmp_path):
        df = _make_df(n=20)
        csv_path = tmp_path / "particles.csv"
        df.to_csv(csv_path, index=False)
        out_dir = tmp_path / "out"
        manifest = run(str(csv_path), str(out_dir))
        assert manifest["n_total"] == 20
        assert (out_dir / "constraint_gate_pass_rates.csv").exists()
        assert (out_dir / "constraint_gate_intersections.csv").exists()
        assert (out_dir / "constraint_subset_summaries.csv").exists()
        assert (out_dir / "constraint_frontier_particles.csv").exists()
        assert (out_dir / "constraint_conflict_summary.json").exists()
        assert (out_dir / "constraint_conflict_report.html").exists()

    def test_compare_csv(self, tmp_path):
        df1 = _make_df(n=10)
        df2 = _make_df(n=10)
        csv1 = tmp_path / "p1.csv"
        csv2 = tmp_path / "p2.csv"
        df1.to_csv(csv1, index=False)
        df2.to_csv(csv2, index=False)
        out_dir = tmp_path / "out"
        manifest = run(str(csv1), str(out_dir), compare_particles_path=str(csv2))
        assert manifest["n_total"] == 20
        assert manifest["n_sources"] == 2

    def test_conflict_scenario(self, tmp_path):
        rows = []
        for i in range(50):
            rows.append(_make_row(
                particle_id=i,
                diagnosis_gate_pass=True,
                aids_death_gate_pass=False,
                acceptance_aids_death_gate_bounds_fraction=0.05,
            ))
        for i in range(50, 100):
            rows.append(_make_row(
                particle_id=i,
                diagnosis_gate_pass=False,
                aids_death_gate_pass=True,
                acceptance_diagnosis_gate_new_diagnoses_ratio=0.3,
            ))
        df = _make_df(rows)
        csv_path = tmp_path / "conflict.csv"
        df.to_csv(csv_path, index=False)
        out_dir = tmp_path / "out"
        run(str(csv_path), str(out_dir))
        summary = json.loads((out_dir / "constraint_conflict_summary.json").read_text())
        interps = summary["interpretations"]["primary"]
        assert any("CONFLICT" in line for line in interps)

    def test_with_errors_and_collapsed(self, tmp_path):
        rows = [
            _make_row(particle_id=0),
            _make_row(particle_id=1, error="timeout"),
            _make_row(particle_id=2, collapsed=True),
        ]
        df = _make_df(rows)
        csv_path = tmp_path / "mixed.csv"
        df.to_csv(csv_path, index=False)
        out_dir = tmp_path / "out"
        manifest = run(str(csv_path), str(out_dir))
        assert manifest["n_total"] == 3
        assert manifest["n_evaluable"] == 1
