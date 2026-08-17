"""
Tests for tools/compare_calibration_candidate_mechanisms.py — pure logic only.

No model simulation is run in any of these tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compare_calibration_candidate_mechanisms import (
    build_candidate_row,
    build_html_report,
    parse_candidate_arg,
    _derive_non_msm_2024,
    _non_msm_mape,
    _derive_non_msm_outputs,
    _safe_ratio,
)


# ---------------------------------------------------------------------------
# parse_candidate_arg
# ---------------------------------------------------------------------------


class TestParseCandidateArg:
    def test_basic_name_path(self):
        name, path = parse_candidate_arg("baseline=/some/path/config.json")
        assert name == "baseline"
        assert path == "/some/path/config.json"

    def test_name_path_with_spaces(self):
        name, path = parse_candidate_arg("v13_10_best = /data/config.json")
        assert name == "v13_10_best"
        assert path == "/data/config.json"

    def test_path_containing_equals_sign(self):
        """PATH itself may contain '='; only the first '=' is split on."""
        name, path = parse_candidate_arg("cand=outputs/calib=test/config.json")
        assert name == "cand"
        assert path == "outputs/calib=test/config.json"

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="NAME=PATH"):
            parse_candidate_arg("noequalssign")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="NAME=PATH"):
            parse_candidate_arg("=/some/path.json")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="NAME=PATH"):
            parse_candidate_arg("baseline=")

    def test_returns_tuple_of_strings(self):
        result = parse_candidate_arg("candidate_a=/path/to/file.json")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, str) for x in result)


# ---------------------------------------------------------------------------
# derive_non_msm_2024
# ---------------------------------------------------------------------------


class TestDeriveNonMsm2024:
    def _make_outputs(self, total=550.0, msm=360.0):
        return {
            "new_diagnoses": {2024: total},
            "new_diagnoses_msm": {2024: msm},
        }

    def _make_targets(self, total_target=520.0, msm_target=340.0):
        return [
            {"model_output": "new_diagnoses", "year": 2024, "estimate": total_target},
            {"model_output": "new_diagnoses_msm", "year": 2024, "estimate": msm_target},
        ]

    def test_model_non_msm_is_positive(self):
        model_non_msm, target_non_msm = _derive_non_msm_2024(
            self._make_outputs(), self._make_targets(), 2024
        )
        assert model_non_msm == pytest.approx(550.0 - 360.0)
        assert target_non_msm == pytest.approx(520.0 - 340.0)

    def test_clamped_to_zero(self):
        model_non_msm, _ = _derive_non_msm_2024(
            self._make_outputs(total=300.0, msm=400.0), self._make_targets(), 2024
        )
        assert model_non_msm == pytest.approx(0.0)

    def test_returns_none_when_output_missing(self):
        model_non_msm, target_non_msm = _derive_non_msm_2024({}, self._make_targets(), 2024)
        assert model_non_msm is None

    def test_returns_none_when_target_missing(self):
        _, target_non_msm = _derive_non_msm_2024(self._make_outputs(), [], 2024)
        assert target_non_msm is None


# ---------------------------------------------------------------------------
# _non_msm_mape
# ---------------------------------------------------------------------------


class TestNonMsmMape:
    def _make_rows(self):
        return [
            (2015, 200.0, 180.0),
            (2016, 210.0, 200.0),
            (2017, 190.0, 220.0),
            (2020, 150.0, 200.0),
            (2024, 10.0,  180.0),
        ]

    def test_mape_within_window(self):
        rows = self._make_rows()
        mape = _non_msm_mape(rows, 2015, 2017)
        assert mape is not None
        # manual: |200-180|/180, |210-200|/200, |190-220|/220 in pct
        expected = sorted([abs((200-180)/180)*100, abs((210-200)/200)*100, abs((190-220)/220)*100])
        assert mape == pytest.approx(sorted([abs((200-180)/180)*100, abs((210-200)/200)*100, abs((190-220)/220)*100])[1])

    def test_zero_target_skipped(self):
        rows = [(2015, 100.0, 0.0), (2016, 200.0, 100.0)]
        mape = _non_msm_mape(rows, 2015, 2016)
        assert mape == pytest.approx(abs((200-100)/100)*100)

    def test_no_rows_in_window_returns_none(self):
        rows = [(2015, 100.0, 100.0)]
        mape = _non_msm_mape(rows, 2020, 2024)
        assert mape is None

    def test_empty_rows_returns_none(self):
        assert _non_msm_mape([], 2015, 2024) is None


# ---------------------------------------------------------------------------
# _safe_ratio
# ---------------------------------------------------------------------------


class TestSafeRatio:
    def test_basic_ratio(self):
        assert _safe_ratio(3.0, 4.0) == pytest.approx(0.75)

    def test_zero_denominator(self):
        assert _safe_ratio(3.0, 0.0) is None

    def test_none_numerator(self):
        assert _safe_ratio(None, 4.0) is None

    def test_none_denominator(self):
        assert _safe_ratio(3.0, None) is None

    def test_inf_value(self):
        assert _safe_ratio(float("inf"), 4.0) is None


# ---------------------------------------------------------------------------
# build_candidate_row: handles missing columns safely
# ---------------------------------------------------------------------------


class TestBuildCandidateRowSafe:
    def _minimal_gate_result(self):
        return {
            "v1382_gates": {"overall_accept": True, "gates": {}, "failures": [], "warnings": []},
            "v139_gates": {"overall_accept": True, "gates": {}, "failures": [], "warnings": []},
            "v1395_gate": {"passed": True, "failures": [], "warnings": [], "metrics": {}},
            "total_hard_gate_failures": 0,
            "overall_accept": True,
            "hist_overall_accept": True,
            "non_msm_gate_pass": True,
        }

    def test_returns_dict_with_candidate_key(self):
        row = build_candidate_row(
            "test_cand", "/path/config.json",
            {}, {}, {},
            self._minimal_gate_result(), [],
        )
        assert row["candidate"] == "test_cand"

    def test_no_crash_with_empty_outputs(self):
        row = build_candidate_row(
            "empty_cand", "/path/config.json",
            {}, {}, {},
            self._minimal_gate_result(), [],
        )
        assert row.get("diag_2024_total_ratio") is None

    def test_no_crash_with_empty_targets(self):
        row = build_candidate_row(
            "cand", "/path/config.json",
            {"new_diagnoses": {2024: 500.0}, "new_diagnoses_msm": {2024: 320.0}},
            {},
            {},
            self._minimal_gate_result(), [],
        )
        assert row["candidate"] == "cand"

    def test_no_crash_with_empty_config(self):
        row = build_candidate_row(
            "cand", "/path/config.json",
            {}, {}, {},
            self._minimal_gate_result(), [],
        )
        assert row.get("param_p_msm") is None

    def test_gate_flags_reflected_in_row(self):
        gate = self._minimal_gate_result()
        gate["overall_accept"] = False
        gate["total_hard_gate_failures"] = 3
        row = build_candidate_row("cand", "/p", {}, {}, {}, gate, [])
        assert row["overall_accept"] is False
        assert row["total_hard_gate_failures"] == 3


# ---------------------------------------------------------------------------
# build_html_report: creates valid HTML from synthetic data
# ---------------------------------------------------------------------------


class TestBuildHtmlReport:
    def _make_df(self):
        return pd.DataFrame([
            {
                "candidate": "v138_rank001",
                "config_path": "/outputs/v138/config.json",
                "overall_accept": True,
                "hist_overall_accept": False,
                "non_msm_gate_pass": False,
                "total_hard_gate_failures": 2,
                "diag_2024_total_ratio": 0.95,
                "diag_2024_msm_ratio": 1.10,
                "diag_2024_non_msm_ratio": 0.01,
                "plhiv_2015_2024_mape": 4.5,
                "art_2015_2024_mape": 3.2,
                "aids_death_bounds_fraction": 0.45,
                "incidence_2015_2024_median_ratio": 1.05,
                "msm_diag_1996_2003_max_ratio": 4.3,
                "msm_diag_1996_2014_mape": 47.0,
                "non_msm_recent_mape": 95.0,
                "non_msm_hist_mape": None,
                "non_msm_undiagnosed_stock_2024_exists": False,
                "v1382_fail_reasons": "",
                "v139_fail_reasons": "msm_diag_hist_gate:mape_47%>40%",
                "v1395_fail_reasons": "non_msm:2024:ratio_0.050<floor_0.500",
                "param_p_msm": 0.053,
                "param_aids_mortality": 0.22,
            },
            {
                "candidate": "v1310_pilot_best",
                "config_path": "/outputs/v1310/config.json",
                "overall_accept": True,
                "hist_overall_accept": True,
                "non_msm_gate_pass": False,
                "total_hard_gate_failures": 1,
                "diag_2024_total_ratio": 0.97,
                "diag_2024_msm_ratio": 0.99,
                "diag_2024_non_msm_ratio": 0.00,
                "plhiv_2015_2024_mape": 3.8,
                "art_2015_2024_mape": 2.9,
                "aids_death_bounds_fraction": 0.20,
                "incidence_2015_2024_median_ratio": 0.98,
                "msm_diag_1996_2003_max_ratio": 2.1,
                "msm_diag_1996_2014_mape": 22.0,
                "non_msm_recent_mape": 98.0,
                "non_msm_hist_mape": None,
                "non_msm_undiagnosed_stock_2024_exists": None,
                "v1382_fail_reasons": "",
                "v139_fail_reasons": "",
                "v1395_fail_reasons": "non_msm:2024:ratio_0.000<floor_0.500",
                "param_p_msm": 0.055,
                "param_aids_mortality": 0.24,
            },
        ])

    def _make_manifest(self):
        return {
            "created_at": "2026-05-27T12:00:00",
            "candidates": ["v138_rank001", "v1310_pilot_best"],
        }

    def test_returns_string(self):
        html = build_html_report(self._make_df(), self._make_manifest())
        assert isinstance(html, str)

    def test_html_doctype(self):
        html = build_html_report(self._make_df(), self._make_manifest())
        assert "<!DOCTYPE html>" in html

    def test_html_contains_candidate_names(self):
        html = build_html_report(self._make_df(), self._make_manifest())
        assert "v138_rank001" in html
        assert "v1310_pilot_best" in html

    def test_html_contains_metric_labels(self):
        html = build_html_report(self._make_df(), self._make_manifest())
        assert "plhiv_2015_2024_mape" in html
        assert "aids_death_bounds_fraction" in html

    def test_csv_writer_creates_expected_file(self, tmp_path):
        df = self._make_df()
        out = tmp_path / "candidate_mechanism_comparison.csv"
        df.to_csv(out, index=False)
        assert out.exists()
        reread = pd.read_csv(out)
        assert "candidate" in reread.columns
        assert len(reread) == 2

    def test_manifest_json_serializable(self, tmp_path):
        import json
        manifest = self._make_manifest()
        out = tmp_path / "manifest.json"
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["candidates"] == ["v138_rank001", "v1310_pilot_best"]
