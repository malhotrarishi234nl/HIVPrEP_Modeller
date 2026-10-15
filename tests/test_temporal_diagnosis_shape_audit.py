from __future__ import annotations

from pathlib import Path

import pytest

import run_temporal_diagnosis_shape_audit as audit
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/temporal_diagnosis_shape_audit_20260921.json"


def _synthetic_base():
    """Small current-code fixture for temporal geometry, not a historical search product."""
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
    assert audit.load_plan(PLAN)["trajectory_budget"] == 110


@pytest.mark.parametrize("profile_id", [
    "base_hazard_linear", "base_logit_linear", "msm_log_linear", "joint_hazard_log",
    "stepwise_both",
])
def test_geometry_profiles_preserve_every_original_anchor(profile_id):
    plan = audit.load_plan(PLAN)
    profile = next(value for value in plan["profiles"] if value["id"] == profile_id)
    base = _synthetic_base()
    changed = audit.apply_profile(
        base, profile, quarter_step=plan["profile_contract"]["quarter_step"])
    assert audit._float_anchors(audit._base_block(base)).items() <= \
        audit._float_anchors(audit._base_block(changed)).items()
    assert audit._float_anchors(audit._msm_block(base)).items() <= \
        audit._float_anchors(audit._msm_block(changed)).items()


def test_hazard_interpolation_differs_between_but_not_at_anchors():
    anchors = {2000.0: 0.2, 2010.0: 0.8}
    values = audit._densified(anchors, "linear_hazard", 0.25)
    assert values["2000"] == 0.2
    assert values["2010"] == 0.8
    assert values["2005"] != pytest.approx(0.5)


def test_post2019_profiles_leave_existing_anchors_and_pre2020_schedule_unchanged():
    plan = audit.load_plan(PLAN)
    profile = next(value for value in plan["profiles"]
                   if value["id"] == "recent_late_up_30")
    base = _synthetic_base()
    changed = audit.apply_profile(
        base, profile, quarter_step=plan["profile_contract"]["quarter_step"])
    rows_base = audit._schedule_rows(base, 1326, "baseline")
    rows_changed = audit._schedule_rows(changed, 1326, profile["id"])
    for before, after in zip(rows_base, rows_changed):
        if before["year"] <= 2019:
            assert before["effective_msm_annual_probability"] == \
                after["effective_msm_annual_probability"]
    assert rows_changed[-1]["effective_msm_annual_probability"] > \
        rows_base[-1]["effective_msm_annual_probability"]
