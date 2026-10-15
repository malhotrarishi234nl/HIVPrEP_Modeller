"""No-engine checks for the collaborator-facing shared M0/M1 config."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from config_reader import ConfigReader
import run_model
import stage_structured_diagnosis as ssd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json"
SHARED = ROOT / (
    "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_"
    "stochastic_m0_m1_20261015.json"
)


def _has_profile(config: dict) -> bool:
    node = config
    for key in ssd.PROFILE_PATH:
        if key not in node:
            return False
        node = node[key]
    return True


def test_shared_config_is_a_minimal_declared_derivative():
    source = json.loads(SOURCE.read_text())
    shared = json.loads(SHARED.read_text())
    profile = shared["transmission_model"]["disease"]["diagnosis"].pop(
        "stage_structured_diagnosis"
    )
    assert profile["enabled"]["value"] is False
    assert profile["routine_hazard_scale"]["value"] == 1.0
    assert profile["aids_symptom_annual_probability"]["value"] == 0.0
    assert profile["symptom_start_year"]["value"] == 1985

    # Normalise the explicitly documented metadata and mode changes; every
    # scientific parameter and all remaining config content must then match.
    for path in (
        ("_readme",),
        ("meta", "version", "value"),
        ("meta", "version", "notes"),
        ("meta", "model_notes", "calibration_status"),
        ("run_control", "deterministic_mode", "value"),
        ("run_control", "deterministic_mode", "notes"),
    ):
        left = shared
        right = source
        for key in path[:-1]:
            left, right = left[key], right[key]
        left[path[-1]] = right[path[-1]]
    assert shared == source


def test_shared_config_loads_as_safe_standalone_m0_without_an_engine():
    base = run_model.load_shared_config(SHARED)
    profile = ssd.validate_shared_base_config(base)
    assert profile.enabled is False
    assert profile.is_identity
    assert profile.symptom_start_year == 1985.0
    translated = ConfigReader(base).as_v6_dict()
    assert ssd.load_stage_structured_diagnosis(translated) == profile


def test_m0_route_removes_optional_block_and_never_mutates_base():
    base = run_model.load_shared_config(SHARED)
    before = copy.deepcopy(base)
    runtime = run_model.config_for_model(
        base, model="M0", aids_symptom_annual_probability=None
    )
    assert not _has_profile(runtime)
    assert base == before


def test_m1_route_explicitly_enables_and_injects_p_without_mutating_base():
    base = run_model.load_shared_config(SHARED)
    before = copy.deepcopy(base)
    runtime = run_model.config_for_model(
        base, model="M1", aids_symptom_annual_probability=0.25
    )
    node = runtime["transmission_model"]["disease"]["diagnosis"][
        "stage_structured_diagnosis"
    ]
    assert node["enabled"]["value"] is True
    assert node["routine_hazard_scale"]["value"] == 1.0
    assert node["aids_symptom_annual_probability"]["value"] == pytest.approx(0.25)
    assert node["symptom_start_year"]["value"] == 1985
    assert base == before


def test_shared_route_fails_closed_when_the_complete_block_is_missing():
    base = run_model.load_shared_config(SHARED)
    del base["transmission_model"]["disease"]["diagnosis"][
        "stage_structured_diagnosis"
    ]
    with pytest.raises(ssd.StageStructuredDiagnosisConfigError, match="missing"):
        ssd.route_shared_config(base, model="M1", p=0.2)


def test_operator_validation_is_explicit_and_never_constructs_engine(capsys):
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0", "--validate-only",
    ]) == 0
    assert "engine was not constructed" in capsys.readouterr().out
    assert run_model.main([
        "--config", str(SHARED), "--model", "M1", "--p", "0.2",
        "--validate-only",
    ]) == 0
    assert "VALID: M1" in capsys.readouterr().out


def test_operator_route_rejects_ambiguous_variant_requests(capsys):
    assert run_model.main([
        "--config", str(SHARED), "--model", "M1", "--validate-only",
    ]) == 2
    assert "M1 requires --p" in capsys.readouterr().out
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0", "--p", "0.2",
        "--validate-only",
    ]) == 2
    assert "--p is only valid" in capsys.readouterr().out


def test_operator_run_refuses_to_overwrite_a_nonempty_output(tmp_path, capsys):
    output = tmp_path / "existing-run"
    output.mkdir()
    (output / "result.csv").write_text("preserve me")
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0",
        "--output-dir", str(output),
    ]) == 2
    assert "output directory is not empty" in capsys.readouterr().out
