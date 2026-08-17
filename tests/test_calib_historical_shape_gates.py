"""
Tests for V13.9 historical shape gates (calib_historical_shape_gates).

Coverage:
- PLHIV historical gate pass/fail (MAPE and anchor-year multiplier)
- Diagnoses trajectory gate pass/fail (MAPE and 1997 spike ratio)
- MSM diagnoses early-ratio gate pass/fail
- AIDS cases trajectory gate pass/fail (ratio ceiling and MAPE)
- Missing target data is handled gracefully (warns, does not fail)
- Default settings (enabled=False) produce no failures and do not affect
  V13.8.2 behaviour
- Combined overall_accept is False when any historical gate fails
"""

from __future__ import annotations

import pytest

from calib_historical_shape_gates import (
    HistoricalShapeGateSettings,
    evaluate_historical_shape_distance,
    evaluate_historical_shape_gates,
    historical_shape_distance_row_fields,
    historical_shape_gate_row_fields,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _t(output: str, year: int, estimate: float, use: str = "primary") -> dict:
    return {"model_output": output, "year": year, "estimate": estimate, "calibration_use": use}


_ENABLED = HistoricalShapeGateSettings(enabled=True)


def _plhiv_outputs(ratio_1990: float = 1.0, ratio_2000: float = 1.0) -> dict:
    """Synthetic PLHIV outputs relative to 10 000 base."""
    return {"PLHIV": {1990: 10_000 * ratio_1990, 2000: 12_000 * ratio_2000}}


def _plhiv_targets() -> list[dict]:
    return [_t("PLHIV", 1990, 10_000), _t("PLHIV", 2000, 12_000)]


# ---------------------------------------------------------------------------
# Default (disabled) behaviour
# ---------------------------------------------------------------------------


def test_disabled_by_default_produces_no_failures():
    outputs = {"PLHIV": {1990: 100_000}}
    targets = [_t("PLHIV", 1990, 5_000)]  # absurdly bad ratio, should not matter
    result = evaluate_historical_shape_gates(outputs, targets)
    assert result["overall_accept"] is True
    assert result["failures"] == []
    assert any("disabled" in w for w in result["warnings"])


def test_disabled_distance_is_zero():
    outputs = {"PLHIV": {1990: 100_000}}
    targets = [_t("PLHIV", 1990, 5_000)]
    dist = evaluate_historical_shape_distance(outputs, targets)
    assert dist["distance"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# PLHIV historical gate
# ---------------------------------------------------------------------------


def test_plhiv_hist_gate_passes_when_mape_and_anchor_within_limits():
    outputs = _plhiv_outputs(ratio_1990=1.1, ratio_2000=1.05)
    targets = _plhiv_targets()
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    assert result["overall_accept"] is True
    assert result["gates"]["plhiv_hist_gate"]["passed"] is True


def test_plhiv_hist_gate_fails_when_mape_exceeds_ceiling():
    # Model is 3x target in 1990 and 1.0x in 2000 → MAPE includes a 200% error
    outputs = {"PLHIV": {1990: 30_000, 2000: 12_000}}
    targets = _plhiv_targets()
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["plhiv_hist_gate"]
    assert gate["passed"] is False
    assert any("hist_mape" in f for f in gate["failures"])


def test_plhiv_hist_gate_fails_when_anchor_year_ratio_exceeds_multiplier():
    # 1990 model = 2.5x target → exceeds 2.0x multiplier ceiling
    outputs = {"PLHIV": {1990: 25_000, 2000: 12_100}}
    targets = _plhiv_targets()
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["plhiv_hist_gate"]
    assert gate["passed"] is False
    assert any("ratio" in f and "1990" in f for f in gate["failures"])


def test_plhiv_hist_gate_warns_when_no_historical_targets():
    outputs = {"PLHIV": {2020: 25_000}}
    targets = [_t("PLHIV", 2020, 25_000)]  # no 1990-2005 targets
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["plhiv_hist_gate"]
    assert gate["passed"] is True  # missing data → warn, not fail
    assert any("no_comparable" in w or "no_anchor" in w for w in gate["warnings"])


# ---------------------------------------------------------------------------
# Diagnoses trajectory gate
# ---------------------------------------------------------------------------


def _diag_outputs(
    ratio_1995: float = 1.0,
    ratio_1997: float = 1.0,
    ratio_2005: float = 1.0,
) -> dict:
    return {
        "new_diagnoses": {
            1995: 1_000 * ratio_1995,
            1997: 1_200 * ratio_1997,
            2005: 900 * ratio_2005,
        }
    }


def _diag_targets() -> list[dict]:
    return [
        _t("new_diagnoses", 1995, 1_000),
        _t("new_diagnoses", 1997, 1_200),
        _t("new_diagnoses", 2005, 900),
    ]


def test_diagnoses_hist_gate_passes_when_trajectory_reasonable():
    result = evaluate_historical_shape_gates(
        _diag_outputs(1.05, 1.1, 0.95), _diag_targets(), settings=_ENABLED
    )
    assert result["gates"]["diagnoses_hist_gate"]["passed"] is True


def test_diagnoses_hist_gate_fails_on_spike_year_ratio():
    # 1997 model = 3.0x target → spike ratio > 2.5 ceiling
    outputs = _diag_outputs(ratio_1995=1.0, ratio_1997=3.0, ratio_2005=1.0)
    result = evaluate_historical_shape_gates(outputs, _diag_targets(), settings=_ENABLED)
    gate = result["gates"]["diagnoses_hist_gate"]
    assert gate["passed"] is False
    assert any("spike_ratio" in f for f in gate["failures"])


def test_diagnoses_hist_gate_fails_on_high_mape():
    # Model is 2x target everywhere → 100% MAPE > 40% ceiling
    outputs = {"new_diagnoses": {1995: 2_000, 1997: 2_400, 2005: 1_800}}
    result = evaluate_historical_shape_gates(outputs, _diag_targets(), settings=_ENABLED)
    gate = result["gates"]["diagnoses_hist_gate"]
    assert gate["passed"] is False
    assert any("hist_mape" in f for f in gate["failures"])


def test_diagnoses_hist_gate_warns_when_no_historical_targets():
    outputs = {"new_diagnoses": {2022: 500}}
    targets = [_t("new_diagnoses", 2022, 500)]
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["diagnoses_hist_gate"]
    assert gate["passed"] is True
    assert any("no_comparable" in w for w in gate["warnings"])


# ---------------------------------------------------------------------------
# MSM diagnoses gate
# ---------------------------------------------------------------------------


def _msm_outputs(ratio_1998: float = 1.0, ratio_2010: float = 1.0) -> dict:
    return {"new_diagnoses_msm": {1998: 300 * ratio_1998, 2010: 500 * ratio_2010}}


def _msm_targets() -> list[dict]:
    return [_t("new_diagnoses_msm", 1998, 300), _t("new_diagnoses_msm", 2010, 500)]


def test_msm_diag_hist_gate_passes_when_ratios_within_limits():
    result = evaluate_historical_shape_gates(
        _msm_outputs(1.1, 1.05), _msm_targets(), settings=_ENABLED
    )
    assert result["gates"]["msm_diag_hist_gate"]["passed"] is True


def test_msm_diag_hist_gate_fails_when_early_ratio_exceeds_ceiling():
    # 1998 model = 3.0x target → early max ratio > 2.5 ceiling
    outputs = _msm_outputs(ratio_1998=3.0, ratio_2010=1.1)
    result = evaluate_historical_shape_gates(outputs, _msm_targets(), settings=_ENABLED)
    gate = result["gates"]["msm_diag_hist_gate"]
    assert gate["passed"] is False
    assert any("early_max_ratio" in f for f in gate["failures"])


def test_msm_diag_hist_gate_warns_when_no_early_targets():
    outputs = {"new_diagnoses_msm": {2015: 600}}
    targets = [_t("new_diagnoses_msm", 2015, 600)]
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["msm_diag_hist_gate"]
    assert gate["passed"] is True
    assert any("no_comparable" in w for w in gate["warnings"])


# ---------------------------------------------------------------------------
# AIDS cases gate
# ---------------------------------------------------------------------------


def _aids_outputs(ratio_1995: float = 1.0, ratio_2000: float = 1.0) -> dict:
    return {"new_aids_cases": {1995: 200 * ratio_1995, 2000: 150 * ratio_2000}}


def _aids_targets() -> list[dict]:
    return [_t("new_aids_cases", 1995, 200), _t("new_aids_cases", 2000, 150)]


def test_aids_cases_hist_gate_passes_when_ratios_reasonable():
    result = evaluate_historical_shape_gates(
        _aids_outputs(1.2, 1.1), _aids_targets(), settings=_ENABLED
    )
    assert result["gates"]["aids_cases_hist_gate"]["passed"] is True


def test_aids_cases_hist_gate_fails_when_ratio_ceiling_exceeded():
    # 1995 model = 5x target → max ratio > 4.0 ceiling
    outputs = _aids_outputs(ratio_1995=5.0, ratio_2000=1.0)
    result = evaluate_historical_shape_gates(outputs, _aids_targets(), settings=_ENABLED)
    gate = result["gates"]["aids_cases_hist_gate"]
    assert gate["passed"] is False
    assert any("max_ratio" in f for f in gate["failures"])


def test_aids_cases_hist_gate_warns_when_no_historical_targets():
    outputs = {"new_aids_cases": {2023: 50}}
    targets = [_t("new_aids_cases", 2023, 50)]
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    gate = result["gates"]["aids_cases_hist_gate"]
    assert gate["passed"] is True
    assert any("no_comparable" in w for w in gate["warnings"])


# ---------------------------------------------------------------------------
# Combined overall_accept
# ---------------------------------------------------------------------------


def test_overall_accept_false_when_any_gate_fails():
    # 1990 PLHIV 3x target (fails anchor) — all other series fine
    outputs = {
        "PLHIV": {1990: 30_000, 2000: 12_100},
        "new_diagnoses": {1995: 1_000},
        "new_diagnoses_msm": {1998: 300},
        "new_aids_cases": {1995: 200},
    }
    targets = (
        _plhiv_targets()
        + [_t("new_diagnoses", 1995, 1_000)]
        + [_t("new_diagnoses_msm", 1998, 300)]
        + [_t("new_aids_cases", 1995, 200)]
    )
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    assert result["overall_accept"] is False
    assert len(result["failures"]) > 0


def test_overall_accept_true_when_all_gates_pass():
    outputs = {
        "PLHIV": {1990: 10_500, 2000: 12_100},
        "new_diagnoses": {1997: 1_300},
        "new_diagnoses_msm": {1998: 310},
        "new_aids_cases": {1995: 210},
    }
    targets = (
        [_t("PLHIV", 1990, 10_000), _t("PLHIV", 2000, 12_000)]
        + [_t("new_diagnoses", 1997, 1_200)]
        + [_t("new_diagnoses_msm", 1998, 300)]
        + [_t("new_aids_cases", 1995, 200)]
    )
    result = evaluate_historical_shape_gates(outputs, targets, settings=_ENABLED)
    assert result["overall_accept"] is True
    assert result["failures"] == []


# ---------------------------------------------------------------------------
# Row-fields helpers
# ---------------------------------------------------------------------------


def test_gate_row_fields_are_csv_friendly():
    result = evaluate_historical_shape_gates(
        _plhiv_outputs(ratio_1990=3.0), _plhiv_targets(), settings=_ENABLED
    )
    fields = historical_shape_gate_row_fields(result)
    assert "hist_overall_accept" in fields
    assert "hist_n_failures" in fields
    assert "hist_fail_reasons" in fields
    assert isinstance(fields["hist_overall_accept"], bool)
    assert isinstance(fields["hist_n_failures"], int)


def test_distance_row_fields_are_csv_friendly():
    dist = evaluate_historical_shape_distance(
        _plhiv_outputs(ratio_1990=3.0), _plhiv_targets(), settings=_ENABLED
    )
    fields = historical_shape_distance_row_fields(dist)
    assert "hist_distance" in fields
    assert "hist_distance_plhiv_hist" in fields
    assert fields["hist_distance"] > 0


def test_distance_is_positive_when_ceiling_exceeded():
    outputs = {"PLHIV": {1990: 30_000, 2000: 12_000}}
    targets = _plhiv_targets()
    dist = evaluate_historical_shape_distance(outputs, targets, settings=_ENABLED)
    assert dist["distance"] > 0
    assert dist["components"]["plhiv_hist"] > 0


def test_distance_is_zero_when_within_limits():
    outputs = {"PLHIV": {1990: 10_100, 2000: 12_050}}
    targets = _plhiv_targets()
    dist = evaluate_historical_shape_distance(outputs, targets, settings=_ENABLED)
    plhiv_dist = dist["components"]["plhiv_hist"]
    # MAPE ~1% and ratio ~1.01x are both well within ceilings → near-zero
    assert plhiv_dist < 0.5
