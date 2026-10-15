from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_msm_beta_response_audit as audit
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/msm_beta_response_audit_20260921.json"
STALE_SEARCH_ERROR = (
    "code changed since the search: "
    "['aids_observation.py', 'calib_extractor.py', 'config_utils.py', "
    "'hm_target_register.py', 'hm_variance_budget.py', "
    "'run_coupled_calibration.py', 'run_lhs_calibration.py', 'run_v3.py']"
)


def _synthetic_base():
    """Minimal pure fixture for beta transforms, never a calibration claim."""
    return {
        "run_control": {"deterministic_mode": {"value": False}},
        "transmission_model": {
            "disease": {
                "diagnosis": {
                    "msm_relative_diagnosis": {
                        "enabled": {"value": True},
                        "schedule": {"value": {"2019": 1.0}},
                    },
                },
                "transmission": {
                    "advanced": {
                        "beta_per_route": {
                            "anal_receptive": {
                                "chronic": {"value": 0.04, "bounds": [0.02, 0.06]},
                            },
                            "anal_insertive": {
                                "chronic": {"value": 0.01, "bounds": [0.005, 0.015]},
                            },
                        },
                    },
                },
            },
        },
    }


def test_historical_lineage_is_rejected_for_module_hash_mismatch(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    with pytest.raises(ContractError) as caught:
        audit.validate(PLAN)
    assert str(caught.value) == STALE_SEARCH_ERROR


def test_profile_changes_only_the_two_declared_beta_values():
    plan = audit.load_plan(PLAN)
    base = _synthetic_base()
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
    base = _synthetic_base()
    profile = {"id": "bad", "receptive_factor": 10.0, "insertive_factor": 1.0}
    with pytest.raises(ContractError, match="outside literature bounds"):
        audit.apply_profile(base, profile)
