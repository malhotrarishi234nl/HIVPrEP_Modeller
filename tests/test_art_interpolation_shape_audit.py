from __future__ import annotations

from pathlib import Path

import pytest

import run_art_interpolation_shape_audit as audit
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/art_interpolation_shape_audit_20260921.json"


def _synthetic_base():
    """Small current-code fixture for profile geometry, not a historical search product."""
    return {
        "run_control": {"deterministic_mode": {"value": False}},
        "transmission_model": {
            "disease": {
                "diagnosis": {
                    "schedule": {
                        "value": {"1996": 0.10, "2005": 0.20, "2019": 0.35},
                        "interpolation": {"value": "linear"},
                    },
                    "msm_relative_diagnosis": {
                        "enabled": {"value": True},
                        "schedule": {
                            "value": {"1996": 1.10, "2005": 1.25, "2019": 1.40},
                            "interpolation": {"value": "linear"},
                        },
                    },
                }
            }
        },
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {
                        "value": {"1996": 0.05, "2005": 0.25, "2019": 0.65}
                    },
                    "interpolation": {"value": "linear"},
                }
            }
        },
    }


def test_historical_lineage_is_refused_for_module_hash_mismatch(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    with pytest.raises(ContractError, match="code changed since the search") as caught:
        audit.validate(PLAN)
    assert "run_coupled_calibration.py" in str(caught.value)


def test_frozen_plan_contract_remains_budgeted():
    assert audit.load_plan(PLAN)["trajectory_budget"] == 70


@pytest.mark.parametrize("profile_id", [
    "art_hazard_linear", "art_smoothstep", "art_stepwise",
    "msm_log_plus_art_hazard", "msm_log_plus_art_smoothstep",
])
def test_every_art_geometry_preserves_original_anchors(profile_id):
    plan = audit.load_plan(PLAN)
    profile = next(value for value in plan["profiles"] if value["id"] == profile_id)
    base = _synthetic_base()
    changed = audit.apply_profile(
        base, profile, quarter_step=plan["profile_contract"]["quarter_step"])
    assert audit._art_anchors(base).items() <= audit._art_anchors(changed).items()


def test_smoothstep_is_distinct_between_anchors_and_equal_at_endpoints():
    values = audit._densify_art({2000.0: 0.2, 2010.0: 0.8},
                                "smoothstep_probability", 0.25)
    assert values["2000"] == 0.2
    assert values["2010"] == 0.8
    assert values["2002.5"] != pytest.approx(0.35)
