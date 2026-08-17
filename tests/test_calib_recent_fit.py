import pytest

from calib_recent_fit import RecentFitSettings, evaluate_recent_fit, recent_fit_row_fields


def _target(output, year, estimate, sigma=10.0, use="primary"):
    return {
        "model_output": output,
        "year": year,
        "estimate": estimate,
        "sigma": sigma,
        "calibration_use": use,
    }


def test_recent_fit_passes_when_recent_diagnoses_exceed_75_percent_and_infections_not_low():
    targets = [
        _target("new_diagnoses", 2024, 500.0),
        _target("new_diagnoses_msm", 2024, 300.0, use="secondary"),
        _target("new_infections", 2023, 300.0, sigma=50.0),
        _target("PLHIV", 2023, 25_000.0, sigma=1_000.0),
    ]
    outputs = {
        "new_diagnoses": {2024: 400.0},
        "new_diagnoses_msm": {2024: 240.0},
        "new_infections": {2023: 180.0},
        "PLHIV": {2023: 22_500.0},
    }

    result = evaluate_recent_fit(outputs, targets)

    assert result["passed"] is True
    assert result["failures"] == []
    assert result["metrics"]["new_diagnoses_2024_observed_fraction"] == pytest.approx(0.8)
    assert result["metrics"]["new_diagnoses_gate_year"] == 2024


def test_recent_fit_uses_latest_available_diagnosis_year_by_default():
    targets = [
        _target("new_diagnoses", 2023, 400.0),
        _target("new_diagnoses", 2025, 600.0),
        _target("new_diagnoses_msm", 2023, 200.0, use="secondary"),
        _target("new_diagnoses_msm", 2025, 300.0, use="secondary"),
        _target("new_infections", 2023, 300.0, sigma=50.0),
    ]
    outputs = {
        "new_diagnoses": {2023: 400.0, 2025: 300.0},
        "new_diagnoses_msm": {2023: 200.0, 2025: 300.0},
        "new_infections": {2023: 300.0},
    }

    result = evaluate_recent_fit(outputs, targets)

    assert result["passed"] is False
    assert result["metrics"]["new_diagnoses_gate_year"] == 2025
    assert any("new_diagnoses:2025:model_fraction_0.500" in reason for reason in result["failures"])


def test_recent_fit_can_pin_diagnosis_gate_year_when_requested():
    targets = [
        _target("new_diagnoses", 2023, 400.0),
        _target("new_diagnoses", 2025, 600.0),
        _target("new_diagnoses_msm", 2023, 200.0, use="secondary"),
        _target("new_diagnoses_msm", 2025, 300.0, use="secondary"),
        _target("new_infections", 2023, 300.0, sigma=50.0),
    ]
    outputs = {
        "new_diagnoses": {2023: 400.0, 2025: 300.0},
        "new_diagnoses_msm": {2023: 200.0, 2025: 300.0},
        "new_infections": {2023: 300.0},
    }

    result = evaluate_recent_fit(outputs, targets, RecentFitSettings(diagnosis_year=2023))

    assert result["passed"] is True
    assert result["metrics"]["new_diagnoses_gate_year"] == 2023


def test_recent_fit_fails_when_recent_diagnoses_below_75_percent_observed():
    targets = [
        _target("new_diagnoses", 2024, 500.0),
        _target("new_diagnoses_msm", 2024, 300.0, use="secondary"),
        _target("new_infections", 2023, 300.0, sigma=50.0),
    ]
    outputs = {
        "new_diagnoses": {2024: 250.0},
        "new_diagnoses_msm": {2024: 240.0},
        "new_infections": {2023: 300.0},
    }

    result = evaluate_recent_fit(outputs, targets)

    assert result["passed"] is False
    assert any("new_diagnoses:2024:model_fraction_0.500" in reason for reason in result["failures"])


def test_recent_fit_incidence_gate_is_lower_tail_hard_and_upper_tail_warning():
    targets = [
        _target("new_diagnoses", 2024, 500.0),
        _target("new_diagnoses_msm", 2024, 300.0, use="secondary"),
        _target("new_infections", 2023, 300.0, sigma=50.0),
    ]
    low_outputs = {
        "new_diagnoses": {2024: 500.0},
        "new_diagnoses_msm": {2024: 300.0},
        "new_infections": {2023: 100.0},
    }
    high_outputs = {
        "new_diagnoses": {2024: 500.0},
        "new_diagnoses_msm": {2024: 300.0},
        "new_infections": {2023: 500.0},
    }

    low = evaluate_recent_fit(low_outputs, targets, RecentFitSettings(sigma_multiplier=3.0))
    high = evaluate_recent_fit(high_outputs, targets, RecentFitSettings(sigma_multiplier=3.0))

    assert low["passed"] is False
    assert any("new_infections:2023:z_-4.00" in reason for reason in low["failures"])
    assert high["passed"] is True
    assert any("new_infections:2023:z_4.00" in warning for warning in high["warnings"])


def test_recent_fit_row_fields_are_ranked_csv_friendly():
    result = {
        "passed": False,
        "failures": ["a"],
        "warnings": ["b"],
        "metrics": {"version": "v13.5", "new_diagnoses_2024_observed_fraction": 0.5},
    }

    fields = recent_fit_row_fields(result)

    assert fields["recent_fit_pass"] is False
    assert fields["recent_fit_n_failures"] == 1
    assert fields["recent_fit_fail_reasons"] == "a"
    assert fields["recent_fit_new_diagnoses_2024_observed_fraction"] == pytest.approx(0.5)
