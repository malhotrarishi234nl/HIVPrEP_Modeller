"""Adversarial JSON parsing tests for robust-NROY production authority inputs.

These tests construct no simulation engine.  They prove that duplicate keys,
non-standard non-finite tokens and finite-looking float overflow are rejected
before scientific evidence, configuration or stage lineage is interpreted.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402
from nroy_fixtures import ROOT  # noqa: E402

import nroy_acceptance_inputs as nai  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_execution as ne  # noqa: E402
import nroy_identity_gate as nig  # noqa: E402
import run_robust_nroy_freeze as runner  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402


BAD_JSON = (
    ('{"value": 1, "value": 2}', "duplicate JSON object key"),
    ('{"value": NaN}', "non-finite JSON token"),
    ('{"value": Infinity}', "non-finite JSON token"),
    ('{"value": -Infinity}', "non-finite JSON token"),
    ('{"value": 1e999}', "overflows to a non-finite value"),
)


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_acceptance_inputs_use_strict_json(tmp_path, text, problem):
    path = tmp_path / "acceptance_input.json"
    path.write_text(text)
    with pytest.raises(ContractError, match=problem):
        nai._read_mapping(path, label="acceptance-input evidence")


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_identity_certificate_uses_strict_json(tmp_path, text, problem):
    path = tmp_path / nig.GATE_FILENAME
    path.write_text(text)
    with pytest.raises(ContractError, match=problem):
        nig.assert_gate_satisfied(path, root=tmp_path)


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_identity_gate_base_config_uses_strict_json(tmp_path, text, problem):
    path = tmp_path / "base.json"
    path.write_text(text)
    with pytest.raises(ContractError, match=problem):
        nig.gate_config(base_config=path, root=tmp_path)


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_m1_confirmation_evidence_uses_strict_json(tmp_path, text, problem):
    path = tmp_path / "m1_confirmation.json"
    path.write_text(text)
    with pytest.raises(ContractError, match=problem):
        runner._validate_m1_anchor_evidence(
            path,
            anchors_path=tmp_path / "anchors.csv",
            anchors=[],
            plan=None,
            model=None,
            acceptance_identity={},
            observed={},
            base_variance={},
        )


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_m1_per_seed_evidence_uses_strict_json(tmp_path, text, problem):
    path = tmp_path / "seed_result.json"
    path.write_text(text)
    with pytest.raises(ContractError, match=problem):
        runner._recompute_m1_anchor_seed_result(
            path,
            candidate_id="candidate",
            anchor=None,
            plan=None,
            model=None,
            acceptance_identity={},
            observed={},
            base_variance={},
        )


@pytest.mark.parametrize(
    ("field", "text", "problem"),
    [
        ("models.base_config", '{"run_control": {}, "run_control": {}}',
         "duplicate JSON object key"),
        ("models.m0.calib_space", '{"parameters": [], "bad": NaN}',
         "non-finite JSON token"),
    ],
)
def test_production_model_and_calibration_inputs_use_strict_json(
    tmp_path, field, text, problem,
):
    plan = fx.signed_plan(tmp_path)
    path = tmp_path / "authority_input.json"
    path.write_text(text)
    node = plan.raw
    parts = field.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = str(path)
    with pytest.raises(ContractError, match=problem):
        runner.ProductionEvaluator(plan, root=ROOT)


def test_production_target_register_is_strictly_preflighted(tmp_path):
    plan = fx.signed_plan(tmp_path)
    register = tmp_path / "target_register.json"
    register.write_text('{"targets": [], "targets": []}')
    plan = dataclasses.replace(
        plan,
        targets=nc.TargetContract(
            frozen_order=plan.targets.frozen_order,
            register_path=str(register),
            register_sha256=plan.targets.register_sha256,
        ),
    )
    with pytest.raises(ContractError, match="duplicate JSON object key"):
        runner.ProductionEvaluator(plan, root=ROOT)


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_ledger_metadata_uses_strict_json(tmp_path, text, problem):
    (tmp_path / "ledger_meta.json").write_text(text)
    with pytest.raises(ContractError, match=problem):
        ne.Ledger.load(tmp_path)


@pytest.mark.parametrize(("text", "problem"), BAD_JSON)
def test_stage_manifest_uses_strict_json(tmp_path, text, problem):
    (tmp_path / "manifest.json").write_text(text)
    (tmp_path / "artifacts.json").write_text(json.dumps({"schema": "unused"}))
    with pytest.raises(ContractError, match=problem):
        ne.validate_artifact_index(tmp_path)
