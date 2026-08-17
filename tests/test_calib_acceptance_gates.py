import pytest

from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from audit_calibration_acceptance import set_nested


def _target(output, year, estimate, lower=None, upper=None, use="primary"):
    return {
        "model_output": output,
        "year": year,
        "estimate": estimate,
        "lower": lower,
        "upper": upper,
        "sigma": max(abs(estimate) * 0.1, 1.0),
        "calibration_use": use,
    }


def _passing_targets():
    targets = [
        _target("new_diagnoses", 2024, 500),
        _target("new_diagnoses_msm", 2024, 300, use="secondary"),
        _target("PLHIV", 2023, 25_000, 22_000, 28_000),
        _target("new_infections", 2023, 300, 200, 400),
    ]
    for year in range(2015, 2024):
        targets.append(_target("PLHIV", year, 25_000, 22_000, 28_000))
        targets.append(_target("new_infections", year, 300, 200, 400))
        targets.append(_target("on_art_stock", year, 20_000))
    for year in range(1990, 2024):
        targets.append(_target("aids_deaths", year, 100, 80, 160))
    return targets


def _passing_outputs():
    outputs = {
        "new_diagnoses": {2024: 500},
        "new_diagnoses_msm": {2024: 300},
        "PLHIV": {year: 24_000 for year in range(2015, 2024)},
        "new_infections": {year: 330 for year in range(2015, 2024)},
        "on_art_stock": {year: 19_500 for year in range(2015, 2024)},
        "aids_deaths": {},
    }
    for year in range(1990, 2024):
        outputs["aids_deaths"][year] = 100
    outputs["aids_deaths"][1995] = 260
    for year in range(2015, 2024):
        outputs["aids_deaths"][year] = 90
    return outputs


def test_acceptance_gates_pass_at_locked_thresholds():
    result = evaluate_acceptance_gates(_passing_outputs(), _passing_targets())

    assert result["overall_accept"] is True
    assert result["failures"] == []
    assert result["gates"]["diagnosis_gate"]["metrics"]["new_diagnoses_ratio"] == pytest.approx(1.0)
    assert result["gates"]["aids_death_gate"]["metrics"]["bounds_fraction"] >= 0.40


def test_diagnosis_gate_fails_below_floor_and_above_ceiling():
    targets = _passing_targets()
    low_outputs = _passing_outputs()
    low_outputs["new_diagnoses"][2024] = 449
    high_outputs = _passing_outputs()
    high_outputs["new_diagnoses_msm"][2024] = 451

    low = evaluate_acceptance_gates(low_outputs, targets)
    high = evaluate_acceptance_gates(high_outputs, targets)

    assert low["overall_accept"] is False
    assert any("new_diagnoses:2024:ratio_0.898<floor" in reason for reason in low["failures"])
    assert high["overall_accept"] is False
    assert any("new_diagnoses_msm:2024:ratio_1.503>ceiling" in reason for reason in high["failures"])


def test_source_bound_failures_are_hard_for_plhiv_and_incidence():
    targets = _passing_targets()
    outputs = _passing_outputs()
    outputs["PLHIV"][2023] = 21_999
    outputs["new_infections"][2023] = 421

    result = evaluate_acceptance_gates(outputs, targets)

    assert result["overall_accept"] is False
    assert any("plhiv_gate:PLHIV:2023:outside_source_bounds" == reason for reason in result["failures"])
    assert any(
        reason.startswith("incidence_gate:new_infections:2023:outside_source_bounds")
        for reason in result["failures"]
    )


def test_incidence_upper_bound_allows_locked_five_percent_margin():
    targets = _passing_targets()
    outputs = _passing_outputs()
    outputs["new_infections"][2023] = 420

    result = evaluate_acceptance_gates(outputs, targets)

    assert result["overall_accept"] is True
    metrics = result["gates"]["incidence_gate"]["metrics"]
    assert metrics["upper"] == pytest.approx(400)
    assert metrics["upper_tolerance"] == pytest.approx(0.05)
    assert metrics["effective_upper"] == pytest.approx(420)
    assert metrics["within_source_bounds"] is False
    assert metrics["within_bounds"] is True


def test_incidence_upper_bound_tolerance_can_be_disabled():
    targets = _passing_targets()
    outputs = _passing_outputs()
    outputs["new_infections"][2023] = 401
    settings = AcceptanceGateSettings(incidence_year_upper_tolerance=0.0)

    result = evaluate_acceptance_gates(outputs, targets, settings=settings)

    assert result["overall_accept"] is False
    assert any(
        reason.startswith("incidence_gate:new_infections:2023:outside_source_bounds")
        for reason in result["failures"]
    )


def test_aids_death_gate_requires_bounds_coverage_and_peak_shape():
    targets = _passing_targets()
    outputs = _passing_outputs()
    for year in range(1990, 2024):
        outputs["aids_deaths"][year] = 40

    result = evaluate_acceptance_gates(outputs, targets)

    assert result["overall_accept"] is False
    assert result["gates"]["aids_death_gate"]["passed"] is False
    assert any("aids_deaths:bounds_fraction" in reason for reason in result["failures"])
    assert any("peak_to_recent_mean_ratio" in reason for reason in result["failures"])


def test_soft_aids_death_gate_moves_failures_to_warnings():
    targets = _passing_targets()
    outputs = _passing_outputs()
    for year in range(1990, 2024):
        outputs["aids_deaths"][year] = 40

    result = evaluate_acceptance_gates(
        outputs,
        targets,
        settings=AcceptanceGateSettings(aids_death_hard_gate=False),
    )

    assert result["overall_accept"] is True
    assert result["gates"]["aids_death_gate"]["passed"] is False
    assert not any("aids_death_gate:" in reason for reason in result["failures"])
    assert any(
        "aids_death_gate:aids_deaths:bounds_fraction" in reason
        for reason in result["warnings"]
    )

    fields = acceptance_gate_row_fields(result)
    assert fields["overall_accept"] is True
    assert fields["aids_death_gate_pass"] is False
    assert fields["acceptance_n_failures"] == 0
    assert fields["acceptance_n_warnings"] >= 1
    assert fields["acceptance_aids_death_hard_gate"] is False


def test_missing_model_output_is_reported_as_gate_failure():
    targets = _passing_targets()
    outputs = _passing_outputs()
    del outputs["new_diagnoses"][2024]

    result = evaluate_acceptance_gates(outputs, targets)

    assert result["overall_accept"] is False
    assert any("diagnosis_gate:new_diagnoses:2024:missing_model_output" == reason for reason in result["failures"])


def test_acceptance_gate_row_fields_are_csv_friendly():
    result = evaluate_acceptance_gates(_passing_outputs(), _passing_targets())
    fields = acceptance_gate_row_fields(result)

    assert fields["acceptance_version"] == "v13.8.2"
    assert fields["overall_accept"] is True
    assert fields["diagnosis_gate_pass"] is True
    assert "acceptance_diagnosis_gate_new_diagnoses_ratio" in fields


def test_audit_set_nested_accepts_year_key_paths():
    cfg = {"schedule": {"value": {"2015": 0.4}}, "items": [{"value": 1}]}

    set_nested(cfg, "schedule.value.2015", 0.6)
    set_nested(cfg, "items[0].value", 2)

    assert cfg["schedule"]["value"]["2015"] == pytest.approx(0.6)
    assert cfg["items"][0]["value"] == 2
