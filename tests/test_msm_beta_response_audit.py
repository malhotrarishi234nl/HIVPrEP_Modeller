from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_coupled_calibration as rcc
import run_msm_beta_response_audit as audit
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/msm_beta_response_audit_20260921.json"


def _base():
    plan = audit.load_plan(PLAN)
    source, search, _ = audit._context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    raw = rcc._raw_record(source, 1326)
    space = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    return rcc.injected_config(prepared["state"]["base_config"], raw["params"], space)


def test_validation_is_read_only_and_budgeted(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    result = audit.validate(PLAN)
    assert result["ok"]
    assert result["trajectory_budget"] == 60
    assert result["seed_preflight"]["overlap"] == []


def test_profile_changes_only_the_two_declared_beta_values():
    plan = audit.load_plan(PLAN)
    base = _base()
    changed = audit.apply_profile(base, plan["profiles"][3])
    expected = json.loads(json.dumps(base))
    receptive, insertive = audit._beta_blocks(expected)
    receptive["value"] *= 1.10
    insertive["value"] *= 1.10
    assert changed == expected


def test_local_factor_outside_ten_percent_is_refused(tmp_path):
    plan = json.loads(PLAN.read_text())
    plan["profiles"][1]["receptive_factor"] = 1.11
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ContractError, match="outside local audit bounds"):
        audit.load_plan(path)


def test_literature_bound_is_enforced():
    base = _base()
    profile = {"id": "bad", "receptive_factor": 10.0, "insertive_factor": 1.0}
    with pytest.raises(ContractError, match="outside literature bounds"):
        audit.apply_profile(base, profile)
