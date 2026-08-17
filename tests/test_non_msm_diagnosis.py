"""
Tests for calib_non_msm_diagnosis and the V13.9.5 opt-in gates wired into
run_abc_smc_calibration.

Covers:
  - derived non-MSM model/target series (subtraction, clamping, missing data)
  - latest-year ratio gate pass/fail
  - recent and historical MAPE gate pass/fail
  - zero/negative derived values handled safely
  - disabled gate is transparent (always passes, zero distance)
  - CLI parse tests for all new flags
  - overall_accept interaction: non-MSM gate blocks accept only when enabled
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_non_msm_diagnosis import (
    NonMsmDiagnosisSettings,
    build_non_msm_diagnosis_series,
    evaluate_non_msm_diagnosis_gate,
    non_msm_diagnosis_distance,
    non_msm_diagnosis_row_fields,
)
from run_abc_smc_calibration import parse_args


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _t(model_output: str, year: int, estimate: float) -> dict:
    """Minimal target record."""
    return {
        "model_output": model_output,
        "year": year,
        "estimate": estimate,
        "calibration_use": "primary",
    }


def _mo(total_by_year: dict[int, float], msm_by_year: dict[int, float]) -> dict:
    """Minimal model outputs dict."""
    return {
        "new_diagnoses":     total_by_year,
        "new_diagnoses_msm": msm_by_year,
    }


# ---------------------------------------------------------------------------
# TestBuildNonMsmSeries
# ---------------------------------------------------------------------------


class TestBuildNonMsmSeries:
    def test_basic_subtraction(self):
        mo = _mo({2020: 600.0}, {2020: 400.0})
        targets = [_t("new_diagnoses", 2020, 550.0), _t("new_diagnoses_msm", 2020, 350.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert len(series) == 1
        row = series[0]
        assert row["model_non_msm"] == pytest.approx(200.0)
        assert row["target_non_msm"] == pytest.approx(200.0)

    def test_negative_model_non_msm_clamped_to_zero(self):
        """Model MSM > model total → non-MSM clamped to 0, never negative."""
        mo = _mo({2020: 300.0}, {2020: 400.0})  # model MSM exceeds total
        targets = [_t("new_diagnoses", 2020, 500.0), _t("new_diagnoses_msm", 2020, 300.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert series[0]["model_non_msm"] == pytest.approx(0.0)

    def test_negative_target_non_msm_clamped_to_zero(self):
        """Target MSM > target total → non-MSM clamped to 0."""
        mo = _mo({2020: 500.0}, {2020: 300.0})
        targets = [_t("new_diagnoses", 2020, 300.0), _t("new_diagnoses_msm", 2020, 400.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert series[0]["target_non_msm"] == pytest.approx(0.0)

    def test_missing_msm_target_skips_year(self):
        """Year without new_diagnoses_msm target is excluded."""
        mo = _mo({2020: 600.0}, {})
        targets = [_t("new_diagnoses", 2020, 500.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert series == []

    def test_missing_total_target_skips_year(self):
        """Year without new_diagnoses target is excluded."""
        mo = _mo({}, {2020: 300.0})
        targets = [_t("new_diagnoses_msm", 2020, 300.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert series == []

    def test_missing_model_output_skips_year(self):
        """Year where model outputs are absent is silently skipped."""
        mo = {"new_diagnoses": {}, "new_diagnoses_msm": {}}
        targets = [_t("new_diagnoses", 2020, 500.0), _t("new_diagnoses_msm", 2020, 300.0)]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert series == []

    def test_multiple_years_sorted_ascending(self):
        mo = _mo({2018: 500.0, 2019: 520.0, 2020: 540.0},
                 {2018: 300.0, 2019: 310.0, 2020: 320.0})
        targets = [
            _t("new_diagnoses",     2018, 480.0), _t("new_diagnoses_msm", 2018, 290.0),
            _t("new_diagnoses",     2019, 500.0), _t("new_diagnoses_msm", 2019, 300.0),
            _t("new_diagnoses",     2020, 520.0), _t("new_diagnoses_msm", 2020, 310.0),
        ]
        series = build_non_msm_diagnosis_series(mo, targets)
        assert [r["year"] for r in series] == [2018, 2019, 2020]

    def test_returned_row_contains_all_expected_keys(self):
        mo = _mo({2020: 600.0}, {2020: 400.0})
        targets = [_t("new_diagnoses", 2020, 550.0), _t("new_diagnoses_msm", 2020, 350.0)]
        row = build_non_msm_diagnosis_series(mo, targets)[0]
        expected_keys = {
            "year", "model_total", "target_total",
            "model_msm", "target_msm",
            "model_non_msm", "target_non_msm",
        }
        assert expected_keys == set(row.keys())

    def test_empty_targets_returns_empty(self):
        mo = _mo({2020: 500.0}, {2020: 300.0})
        assert build_non_msm_diagnosis_series(mo, []) == []


# ---------------------------------------------------------------------------
# TestEvaluateNonMsmGate
# ---------------------------------------------------------------------------


class TestEvaluateNonMsmGate:
    def _settings(self, **kwargs) -> NonMsmDiagnosisSettings:
        defaults = dict(
            enabled=True,
            latest_floor=0.50,
            latest_ceiling=1.50,
            recent_start=2015,
            recent_end=2024,
            recent_mape_ceiling=35.0,
            hist_start=1996,
            hist_end=2014,
            hist_mape_ceiling=60.0,
        )
        defaults.update(kwargs)
        return NonMsmDiagnosisSettings(**defaults)

    def _basic_mo_targets(self, latest_model_non_msm: float, latest_target_non_msm: float):
        # 2024 is the latest year; construct so non-MSM = supplied values.
        mo = _mo({2024: latest_model_non_msm + 100.0}, {2024: 100.0})
        targets = [
            _t("new_diagnoses",     2024, latest_target_non_msm + 100.0),
            _t("new_diagnoses_msm", 2024, 100.0),
        ]
        return mo, targets

    def test_disabled_by_default_always_passes(self):
        settings = NonMsmDiagnosisSettings()  # enabled=False
        mo = _mo({2024: 0.0}, {2024: 0.0})
        result = evaluate_non_msm_diagnosis_gate(mo, [], settings=settings)
        assert result["passed"] is True
        assert result["metrics"]["enabled"] is False
        assert "non_msm_diagnosis_gate:disabled" in result["warnings"]

    def test_latest_ratio_within_bounds_passes(self):
        mo, targets = self._basic_mo_targets(100.0, 100.0)  # ratio = 1.0 → within [0.5, 1.5]
        result = evaluate_non_msm_diagnosis_gate(mo, targets, settings=self._settings())
        assert result["passed"] is True
        assert result["metrics"]["latest_ratio"] == pytest.approx(1.0)

    def test_latest_ratio_below_floor_fails(self):
        # model_non_msm = 40, target_non_msm = 100 → ratio = 0.40 < floor 0.50
        mo, targets = self._basic_mo_targets(40.0, 100.0)
        result = evaluate_non_msm_diagnosis_gate(mo, targets, settings=self._settings())
        assert result["passed"] is False
        assert any("floor" in f for f in result["failures"])

    def test_latest_ratio_above_ceiling_fails(self):
        # model_non_msm = 200, target_non_msm = 100 → ratio = 2.0 > ceiling 1.50
        mo, targets = self._basic_mo_targets(200.0, 100.0)
        result = evaluate_non_msm_diagnosis_gate(mo, targets, settings=self._settings())
        assert result["passed"] is False
        assert any("ceiling" in f for f in result["failures"])

    def test_recent_mape_within_ceiling_passes(self):
        # 2020-2024: model matches target → MAPE = 0%
        years = range(2020, 2025)
        mo  = _mo({y: 600.0 for y in years}, {y: 400.0 for y in years})
        tgts = []
        for y in years:
            tgts += [_t("new_diagnoses", y, 600.0), _t("new_diagnoses_msm", y, 400.0)]
        result = evaluate_non_msm_diagnosis_gate(
            mo, tgts, settings=self._settings(recent_mape_ceiling=35.0)
        )
        assert result["metrics"]["recent_mape"] == pytest.approx(0.0)
        assert result["passed"] is True

    def test_recent_mape_above_ceiling_fails(self):
        # 2020-2024: model_non_msm = 0, target_non_msm = 200 → MAPE = 100%
        years = range(2020, 2025)
        mo  = _mo({y: 400.0 for y in years}, {y: 400.0 for y in years})  # non-MSM = 0
        tgts = []
        for y in years:
            tgts += [_t("new_diagnoses", y, 600.0), _t("new_diagnoses_msm", y, 400.0)]
        result = evaluate_non_msm_diagnosis_gate(
            mo, tgts, settings=self._settings(latest_floor=0.0, recent_mape_ceiling=35.0)
        )
        assert result["passed"] is False
        assert any("recent_mape" in f for f in result["failures"])

    def test_hist_mape_above_ceiling_fails(self):
        # 2000-2010: model_non_msm = 0, target_non_msm = 100 → MAPE = 100% > 60%
        years = range(2000, 2011)
        mo  = _mo({y: 300.0 for y in years}, {y: 300.0 for y in years})  # non-MSM = 0
        tgts = []
        for y in years:
            tgts += [_t("new_diagnoses", y, 400.0), _t("new_diagnoses_msm", y, 300.0)]
        # Use a latest year with a passing ratio to isolate hist gate.
        mo[2024] = 500.0 if isinstance(mo, dict) else None
        mo = dict(mo)
        mo["new_diagnoses"][2024] = 500.0
        mo["new_diagnoses_msm"][2024] = 300.0
        tgts += [_t("new_diagnoses", 2024, 500.0), _t("new_diagnoses_msm", 2024, 300.0)]
        result = evaluate_non_msm_diagnosis_gate(
            mo, tgts,
            settings=self._settings(latest_floor=0.0, latest_ceiling=9.0, hist_mape_ceiling=60.0),
        )
        assert result["passed"] is False
        assert any("hist_mape" in f for f in result["failures"])

    def test_zero_model_non_msm_latest_ratio_is_zero_not_none(self):
        # model_non_msm = 0 → ratio = 0/target = 0.0 (informative, not None)
        mo  = _mo({2024: 400.0}, {2024: 400.0})  # model non-MSM = 0
        tgts = [_t("new_diagnoses", 2024, 500.0), _t("new_diagnoses_msm", 2024, 300.0)]
        result = evaluate_non_msm_diagnosis_gate(
            mo, tgts, settings=self._settings(latest_floor=0.5)
        )
        assert result["metrics"]["latest_ratio"] == pytest.approx(0.0)
        # ratio 0 < floor 0.5 → fails
        assert result["passed"] is False

    def test_zero_target_non_msm_emits_warning_not_failure(self):
        # target non-MSM = 0 → ratio undefined; should warn, not fail
        mo  = _mo({2024: 400.0}, {2024: 300.0})  # model non-MSM = 100
        tgts = [_t("new_diagnoses", 2024, 300.0), _t("new_diagnoses_msm", 2024, 300.0)]
        result = evaluate_non_msm_diagnosis_gate(mo, tgts, settings=self._settings())
        assert result["passed"] is True  # no failure because ratio is undefined
        assert any("zero" in w for w in result["warnings"])

    def test_no_comparable_points_passes_with_warning(self):
        mo = {"new_diagnoses": {}, "new_diagnoses_msm": {}}
        result = evaluate_non_msm_diagnosis_gate(mo, [], settings=self._settings())
        assert result["passed"] is True
        assert any("no_comparable" in w for w in result["warnings"])

    def test_metrics_version_is_v1395(self):
        mo, targets = self._basic_mo_targets(100.0, 100.0)
        result = evaluate_non_msm_diagnosis_gate(mo, targets, settings=self._settings())
        assert result["metrics"]["version"] == "v13.9.5"


# ---------------------------------------------------------------------------
# TestNonMsmDiagnosisDistance
# ---------------------------------------------------------------------------


class TestNonMsmDiagnosisDistance:
    def test_disabled_returns_zero_distance(self):
        settings = NonMsmDiagnosisSettings(enabled=False)
        mo  = _mo({2024: 100.0}, {2024: 100.0})
        result = non_msm_diagnosis_distance(mo, [], settings=settings)
        assert result["distance"] == pytest.approx(0.0)
        assert result["components"] == {}

    def test_perfect_fit_returns_zero_distance(self):
        settings = NonMsmDiagnosisSettings(enabled=True)
        years = range(2015, 2025)
        mo  = _mo({y: 600.0 for y in years}, {y: 400.0 for y in years})
        tgts = []
        for y in years:
            tgts += [_t("new_diagnoses", y, 600.0), _t("new_diagnoses_msm", y, 400.0)]
        result = non_msm_diagnosis_distance(mo, tgts, settings=settings)
        assert result["distance"] == pytest.approx(0.0)

    def test_below_floor_returns_positive_distance(self):
        # model_non_msm = 0, target_non_msm = 200 → ratio = 0 < floor 0.5
        settings = NonMsmDiagnosisSettings(enabled=True, latest_floor=0.50)
        mo   = _mo({2024: 400.0}, {2024: 400.0})  # non-MSM = 0
        tgts = [_t("new_diagnoses", 2024, 600.0), _t("new_diagnoses_msm", 2024, 400.0)]
        result = non_msm_diagnosis_distance(mo, tgts, settings=settings)
        assert result["distance"] > 0.0
        assert result["components"]["latest_ratio"] > 0.0

    def test_above_ceiling_returns_positive_distance(self):
        # model_non_msm = 400, target_non_msm = 100 → ratio = 4.0 > ceiling 1.5
        settings = NonMsmDiagnosisSettings(enabled=True, latest_ceiling=1.50)
        mo   = _mo({2024: 500.0}, {2024: 100.0})  # model non-MSM = 400
        tgts = [_t("new_diagnoses", 2024, 200.0), _t("new_diagnoses_msm", 2024, 100.0)]
        result = non_msm_diagnosis_distance(mo, tgts, settings=settings)
        assert result["distance"] > 0.0
        assert result["components"]["latest_ratio"] > 0.0

    def test_components_dict_contains_expected_keys(self):
        settings = NonMsmDiagnosisSettings(enabled=True)
        mo   = _mo({2020: 500.0}, {2020: 300.0})
        tgts = [_t("new_diagnoses", 2020, 500.0), _t("new_diagnoses_msm", 2020, 300.0)]
        result = non_msm_diagnosis_distance(mo, tgts, settings=settings)
        assert "latest_ratio" in result["components"]
        assert "recent_mape"  in result["components"]
        assert "hist_mape"    in result["components"]


# ---------------------------------------------------------------------------
# TestNonMsmDiagnosisRowFields
# ---------------------------------------------------------------------------


class TestNonMsmDiagnosisRowFields:
    def test_fields_present_with_gate_result(self):
        gate = {
            "passed": True,
            "failures": [],
            "warnings": [],
            "metrics": {"version": "v13.9.5", "enabled": True, "latest_ratio": 0.95},
        }
        fields = non_msm_diagnosis_row_fields(gate)
        assert "non_msm_diag_passed" in fields
        assert "non_msm_diag_n_failures" in fields
        assert "non_msm_diag_fail_reasons" in fields
        assert "non_msm_diag_enabled" in fields

    def test_fields_include_dist_when_provided(self):
        gate = {"passed": True, "failures": [], "warnings": [], "metrics": {}}
        dist = {"distance": 0.42, "components": {"latest_ratio": 0.42, "recent_mape": 0.0}}
        fields = non_msm_diagnosis_row_fields(gate, dist)
        assert fields["non_msm_diag_distance"] == pytest.approx(0.42)
        assert "non_msm_diag_dist_latest_ratio" in fields

    def test_failure_reasons_joined_by_semicolon(self):
        gate = {
            "passed": False,
            "failures": ["non_msm:2024:ratio_0.3<floor_0.5", "non_msm:recent_mape_40%>35%"],
            "warnings": [],
            "metrics": {},
        }
        fields = non_msm_diagnosis_row_fields(gate)
        assert fields["non_msm_diag_n_failures"] == 2
        parts = fields["non_msm_diag_fail_reasons"].split(";")
        assert len(parts) == 2


# ---------------------------------------------------------------------------
# TestCliParseNonMsmFlags
# ---------------------------------------------------------------------------


MINIMAL_CLI = ["--config", "x.json", "--calib-space", "y.json", "--output-dir", "/tmp"]


class TestCliParseNonMsmFlags:
    def test_flag_absent_defaults_to_disabled(self):
        args = parse_args(MINIMAL_CLI)
        assert args.non_msm_diagnosis_gate is False

    def test_flag_absent_defaults_all_thresholds(self):
        args = parse_args(MINIMAL_CLI)
        assert args.weight_non_msm_diagnosis    == pytest.approx(1.0)
        assert args.non_msm_latest_floor        == pytest.approx(0.50)
        assert args.non_msm_latest_ceiling      == pytest.approx(1.50)
        assert args.non_msm_recent_mape_ceiling == pytest.approx(35.0)
        assert args.non_msm_hist_mape_ceiling   == pytest.approx(60.0)

    def test_flag_present_enables_gate(self):
        args = parse_args(MINIMAL_CLI + ["--non-msm-diagnosis-gate"])
        assert args.non_msm_diagnosis_gate is True

    def test_custom_floor_and_ceiling_parsed(self):
        args = parse_args(MINIMAL_CLI + [
            "--non-msm-diagnosis-gate",
            "--non-msm-latest-floor", "0.30",
            "--non-msm-latest-ceiling", "2.00",
        ])
        assert args.non_msm_latest_floor   == pytest.approx(0.30)
        assert args.non_msm_latest_ceiling == pytest.approx(2.00)

    def test_custom_mape_ceilings_parsed(self):
        args = parse_args(MINIMAL_CLI + [
            "--non-msm-recent-mape-ceiling", "50.0",
            "--non-msm-hist-mape-ceiling",   "80.0",
        ])
        assert args.non_msm_recent_mape_ceiling == pytest.approx(50.0)
        assert args.non_msm_hist_mape_ceiling   == pytest.approx(80.0)

    def test_custom_weight_parsed(self):
        args = parse_args(MINIMAL_CLI + ["--weight-non-msm-diagnosis", "2.5"])
        assert args.weight_non_msm_diagnosis == pytest.approx(2.5)

    def test_negative_weight_raises_system_exit(self):
        with pytest.raises(SystemExit):
            parse_args(MINIMAL_CLI + ["--weight-non-msm-diagnosis", "-0.5"])

    def test_zero_floor_raises_system_exit(self):
        with pytest.raises(SystemExit):
            parse_args(MINIMAL_CLI + ["--non-msm-latest-floor", "0.0"])

    def test_zero_mape_ceiling_raises_system_exit(self):
        with pytest.raises(SystemExit):
            parse_args(MINIMAL_CLI + ["--non-msm-recent-mape-ceiling", "0.0"])


# ---------------------------------------------------------------------------
# TestOverallAcceptNonMsmGateInteraction
# ---------------------------------------------------------------------------


class TestOverallAcceptNonMsmGateInteraction:
    """
    Tests that overall_accept is blocked by the non-MSM gate only when enabled.

    We simulate the logic in _run_particle_worker directly (without a model run)
    to verify the contract without an integration/slow test.
    """

    def _apply_non_msm_gate_update(self, row: dict, gate_result: dict) -> dict:
        """Replicate the non-MSM gate update logic from _run_particle_worker."""
        import copy
        row = copy.deepcopy(row)
        if not gate_result["passed"]:
            row["overall_accept"] = False
            extra_failures = ";".join(gate_result.get("failures", []))
            existing = str(row.get("acceptance_fail_reasons") or "")
            row["acceptance_fail_reasons"] = (
                f"{existing};{extra_failures}" if existing else extra_failures
            )
            row["acceptance_n_failures"] = (
                int(row.get("acceptance_n_failures") or 0)
                + len(gate_result.get("failures", []))
            )
        return row

    def test_gate_disabled_does_not_affect_overall_accept(self):
        """With enabled=False, evaluate_non_msm_diagnosis_gate always passes → no effect."""
        settings = NonMsmDiagnosisSettings(enabled=False)
        mo = _mo({2024: 400.0}, {2024: 400.0})  # model non-MSM = 0 (would fail if enabled)
        tgts = [_t("new_diagnoses", 2024, 600.0), _t("new_diagnoses_msm", 2024, 400.0)]
        gate = evaluate_non_msm_diagnosis_gate(mo, tgts, settings=settings)
        assert gate["passed"] is True  # transparent pass

        row = {"overall_accept": True, "acceptance_n_failures": 0, "acceptance_fail_reasons": ""}
        row_updated = self._apply_non_msm_gate_update(row, gate)
        assert row_updated["overall_accept"] is True  # unchanged

    def test_gate_enabled_and_failing_sets_overall_accept_false(self):
        """With enabled=True and a failing ratio, overall_accept becomes False."""
        settings = NonMsmDiagnosisSettings(
            enabled=True, latest_floor=0.50, latest_ceiling=1.50
        )
        # model non-MSM = 0 (all diagnoses MSM) → ratio = 0/200 = 0 < floor 0.50
        mo   = _mo({2024: 400.0}, {2024: 400.0})
        tgts = [_t("new_diagnoses", 2024, 600.0), _t("new_diagnoses_msm", 2024, 400.0)]
        gate = evaluate_non_msm_diagnosis_gate(mo, tgts, settings=settings)
        assert gate["passed"] is False

        row = {"overall_accept": True, "acceptance_n_failures": 0, "acceptance_fail_reasons": ""}
        row_updated = self._apply_non_msm_gate_update(row, gate)
        assert row_updated["overall_accept"] is False
        assert row_updated["acceptance_n_failures"] >= 1

    def test_gate_enabled_and_passing_leaves_overall_accept_true(self):
        """With enabled=True and a passing ratio, overall_accept is unchanged."""
        settings = NonMsmDiagnosisSettings(
            enabled=True, latest_floor=0.50, latest_ceiling=1.50
        )
        # model non-MSM = 200, target_non_msm = 200 → ratio = 1.0
        mo   = _mo({2024: 600.0}, {2024: 400.0})
        tgts = [_t("new_diagnoses", 2024, 600.0), _t("new_diagnoses_msm", 2024, 400.0)]
        gate = evaluate_non_msm_diagnosis_gate(mo, tgts, settings=settings)
        assert gate["passed"] is True

        row = {"overall_accept": True, "acceptance_n_failures": 0, "acceptance_fail_reasons": ""}
        row_updated = self._apply_non_msm_gate_update(row, gate)
        assert row_updated["overall_accept"] is True
        assert row_updated["acceptance_n_failures"] == 0
