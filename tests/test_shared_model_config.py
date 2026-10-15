"""No-engine checks for the collaborator-facing shared M0/M1 config."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import types

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


def _write_copy(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "collaborator_config.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _write_approved_canonical(tmp_path: Path) -> Path:
    payload = json.loads(SHARED.read_text())
    payload["production_approval"] = {
        "schema": run_model.PRODUCTION_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "production_eligible": True,
        "approved": True,
        "approved_by": "Synthetic test owner",
        "approved_utc": "2026-10-15T09:00:00Z",
    }
    path = tmp_path / "synthetic_approved_canonical.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


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


def test_validate_only_never_creates_output_or_reproducibility_files(tmp_path, capsys):
    output = tmp_path / "must-not-exist"
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0",
        "--output-dir", str(output), "--validate-only",
    ]) == 0
    assert "engine was not constructed" in capsys.readouterr().out
    assert not output.exists()


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
        "--seed", "12345",
        "--output-dir", str(output),
    ]) == 2
    assert "output directory is not empty" in capsys.readouterr().out


def test_operator_run_requires_an_explicit_seed_before_engine_import(tmp_path, capsys):
    output = tmp_path / "new-run"
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0",
        "--output-dir", str(output),
    ]) == 2
    message = capsys.readouterr().out
    assert "--seed is required for every run" in message
    assert not output.exists()


def test_operator_run_refuses_unapproved_config_before_engine_import(
    tmp_path, monkeypatch, capsys,
):
    output = tmp_path / "must-not-run"

    class MustNotConstruct:
        def __init__(self, config, seed):
            raise AssertionError("unapproved config reached engine construction")

    monkeypatch.setitem(
        sys.modules,
        "run_v3",
        types.SimpleNamespace(SimulationEngineV3=MustNotConstruct),
    )
    assert run_model.main([
        "--config", str(SHARED), "--model", "M0", "--seed", "12345",
        "--output-dir", str(output),
    ]) == 2
    message = capsys.readouterr().out
    assert "actual runs require a protected production_approval block" in message
    assert not output.exists()


def test_operator_cannot_self_approve_a_copy(tmp_path, capsys):
    config = json.loads(SHARED.read_text())
    config["production_approval"] = {
        "schema": run_model.PRODUCTION_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "production_eligible": True,
        "approved": True,
        "approved_by": "Unreviewed operator edit",
        "approved_utc": "2026-10-15T09:00:00Z",
    }
    path = _write_copy(tmp_path, config)
    assert run_model.main([
        "--config", str(path), "--model", "M0", "--validate-only",
    ]) == 2
    assert "forbidden config edit at production_approval" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "DRAFT", "status must be 'APPROVED'"),
        ("production_eligible", 1, "production_eligible must be true"),
        ("approved", "true", "approved must be true"),
        ("approved_by", "  ", "must name the approving owner"),
        ("approved_utc", "2026-10-15T09:00:00", "must include a timezone"),
    ],
)
def test_production_approval_contract_rejects_false_or_malformed_claims(
    field, value, message,
):
    config = {
        "production_approval": {
            "schema": run_model.PRODUCTION_APPROVAL_SCHEMA,
            "status": "APPROVED",
            "production_eligible": True,
            "approved": True,
            "approved_by": "Synthetic test owner",
            "approved_utc": "2026-10-15T09:00:00Z",
        }
    }
    config["production_approval"][field] = value
    with pytest.raises(run_model.OperatorConfigError, match=message):
        run_model._require_production_approval(config)


def test_operator_copy_may_change_an_existing_scientific_value(tmp_path, capsys):
    config = json.loads(SHARED.read_text())
    config["transmission_model"]["disease"]["diagnosis"]["annual_rate"][
        "value"
    ] = 0.4
    path = _write_copy(tmp_path, config)
    assert run_model.main([
        "--config", str(path), "--model", "M0", "--validate-only",
    ]) == 0
    assert "VALID: M0" in capsys.readouterr().out


def test_operator_copy_accepts_a_value_inside_declared_bounds(tmp_path, capsys):
    config = json.loads(SHARED.read_text())
    config["population"]["p_msm"]["value"] = 0.08
    path = _write_copy(tmp_path, config)
    assert run_model.main([
        "--config", str(path), "--model", "M0", "--validate-only",
    ]) == 0
    assert "VALID: M0" in capsys.readouterr().out


def test_operator_copy_rejects_a_value_outside_declared_bounds(tmp_path, capsys):
    config = json.loads(SHARED.read_text())
    config["population"]["p_msm"]["value"] = 0.5
    path = _write_copy(tmp_path, config)
    assert run_model.main([
        "--config", str(path), "--model", "M0", "--validate-only",
    ]) == 2
    message = capsys.readouterr().out
    assert "population.p_msm.value=0.5" in message
    assert "declared bounds [0.05, 0.09]" in message


def test_unchanged_canonical_value_is_not_reinterpreted_against_old_bounds():
    # This historical candidate contains at least one value outside old bound
    # metadata. The collaborator gate checks operator edits; it does not silently
    # redefine or repair the reviewed canonical scientific baseline.
    config = run_model.load_shared_config(SHARED)
    assert config["transmission_model"]["disease"]["progression"][
        "chronic_to_aids"
    ]["value"] > config["transmission_model"]["disease"]["progression"][
        "chronic_to_aids"
    ]["bounds"][1]


def test_run_writes_bound_effective_config_and_manifest_with_fake_engine(
    tmp_path, monkeypatch, capsys,
):
    output = tmp_path / "recorded-run"
    approved = _write_approved_canonical(tmp_path)
    monkeypatch.setattr(run_model, "DEFAULT_CONFIG", approved)
    seen = {}

    class FakeEngine:
        def __init__(self, config, seed):
            seen["config"] = config
            seen["seed"] = seed

        def run(self, run_number):
            seen["run_number"] = run_number

    monkeypatch.setitem(
        sys.modules, "run_v3", types.SimpleNamespace(SimulationEngineV3=FakeEngine)
    )
    monkeypatch.setattr(run_model, "_runtime_identity", lambda: {"python": "test"})
    monkeypatch.setattr(run_model, "_code_identity", lambda: {
        "scientific_files_sha256": "code-hash",
        "files": {"run_model.py": "launcher-hash"},
        "git_commit": "abc123",
        "git_scientific_files_dirty": False,
        "git_scientific_diff_sha256": hashlib.sha256(b"").hexdigest(),
    })
    times = iter(("2026-10-15T10:00:00+00:00", "2026-10-15T10:01:00+00:00"))
    monkeypatch.setattr(run_model, "_utc_now", lambda: next(times))

    assert run_model.main([
        "--config", str(approved), "--model", "M1", "--p", "0.2",
        "--seed", "42001", "--output-dir", str(output),
    ]) == 0
    assert seen["seed"] == 42001
    assert seen["run_number"] == 1

    effective_path = output / run_model.EFFECTIVE_CONFIG_FILENAME
    manifest_path = output / run_model.RUN_MANIFEST_FILENAME
    effective = json.loads(effective_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema"] == run_model.RUN_MANIFEST_SCHEMA
    assert manifest["status"] == "COMPLETE"
    assert manifest["route"] == {"model": "M1", "p": 0.2, "seed": 42001}
    assert manifest["input_config"] == {
        "path": str(approved.resolve()),
        "sha256": hashlib.sha256(approved.read_bytes()).hexdigest(),
    }
    assert manifest["effective_config"]["path"] == str(effective_path.resolve())
    assert manifest["effective_config"]["sha256"] == hashlib.sha256(
        effective_path.read_bytes()
    ).hexdigest()
    assert manifest["output_path"] == str(output.resolve())
    assert manifest["runtime_identity"] == {"python": "test"}
    assert manifest["code_identity"]["scientific_files_sha256"] == "code-hash"
    assert manifest["prepared_utc"] == "2026-10-15T10:00:00+00:00"
    assert manifest["completed_utc"] == "2026-10-15T10:01:00+00:00"
    node = effective["transmission_model"]["disease"]["diagnosis"][
        "stage_structured_diagnosis"
    ]
    assert node["enabled"]["value"] is True
    assert node["aids_symptom_annual_probability"]["value"] == pytest.approx(0.2)
    assert effective["production_approval"] == {
        "schema": run_model.PRODUCTION_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "production_eligible": True,
        "approved": True,
        "approved_by": "Synthetic test owner",
        "approved_utc": "2026-10-15T09:00:00Z",
    }
    assert "reproducibility manifest" in capsys.readouterr().out


def test_fake_engine_failure_marks_manifest_failed(tmp_path, monkeypatch):
    output = tmp_path / "failed-run"
    approved = _write_approved_canonical(tmp_path)
    monkeypatch.setattr(run_model, "DEFAULT_CONFIG", approved)

    class FailingFakeEngine:
        def __init__(self, config, seed):
            pass

        def run(self, run_number):
            raise RuntimeError("synthetic test failure")

    monkeypatch.setitem(
        sys.modules,
        "run_v3",
        types.SimpleNamespace(SimulationEngineV3=FailingFakeEngine),
    )
    monkeypatch.setattr(run_model, "_runtime_identity", lambda: {"python": "test"})
    monkeypatch.setattr(run_model, "_code_identity", lambda: {
        "scientific_files_sha256": "code-hash",
        "files": {"run_model.py": "launcher-hash"},
        "git_commit": "abc123",
        "git_scientific_files_dirty": False,
        "git_scientific_diff_sha256": hashlib.sha256(b"").hexdigest(),
    })
    times = iter(("2026-10-15T10:00:00+00:00", "2026-10-15T10:01:00+00:00"))
    monkeypatch.setattr(run_model, "_utc_now", lambda: next(times))

    with pytest.raises(RuntimeError, match="synthetic test failure"):
        run_model.main([
            "--config", str(approved), "--model", "M0", "--seed", "42002",
            "--output-dir", str(output),
        ])

    manifest = json.loads(
        (output / run_model.RUN_MANIFEST_FILENAME).read_text()
    )
    assert manifest["status"] == "FAILED"
    assert manifest["failure_type"] == "RuntimeError"
    assert manifest["completed_utc"] == "2026-10-15T10:01:00+00:00"


@pytest.mark.parametrize(
    ("edit", "forbidden_path"),
    [
        (
            lambda c: c["meta"]["version"].__setitem__("value", "changed"),
            "meta.version.value",
        ),
        (
            lambda c: c["population"].__setitem__(
                "unexpected_parameter", {"value": 1.0}
            ),
            "population.unexpected_parameter",
        ),
        (
            lambda c: c["transmission_model"]["disease"]["diagnosis"][
                "annual_rate"
            ].__setitem__("notes", "changed"),
            "transmission_model.disease.diagnosis.annual_rate.notes",
        ),
        (
            lambda c: c["run_control"]["seed"].__setitem__("value", 9001),
            "run_control.seed.value",
        ),
        (
            lambda c: c["output"]["directory"].__setitem__("value", "elsewhere"),
            "output.directory.value",
        ),
        (
            lambda c: c["transmission_model"]["disease"]["diagnosis"][
                "stage_structured_diagnosis"
            ]["symptom_start_year"].__setitem__("value", 1990),
            (
                "transmission_model.disease.diagnosis.stage_structured_diagnosis."
                "symptom_start_year.value"
            ),
        ),
    ],
)
def test_operator_copy_rejects_schema_metadata_and_runtime_edits(
    tmp_path, capsys, edit, forbidden_path,
):
    config = json.loads(SHARED.read_text())
    edit(config)
    path = _write_copy(tmp_path, config)
    assert run_model.main([
        "--config", str(path), "--model", "M0", "--validate-only",
    ]) == 2
    output = capsys.readouterr().out
    assert f"forbidden config edit at {forbidden_path}" in output
    assert "Start from a fresh canonical copy" in output
