from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_coupled_calibration as rcc
import run_temporal_diagnosis_shape_audit as audit

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/temporal_diagnosis_shape_audit_20260921.json"


def _base(candidate_id=1326):
    plan = audit.load_plan(PLAN)
    source, search, _ = audit._context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    raw = rcc._raw_record(source, candidate_id)
    space = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    return rcc.injected_config(prepared["state"]["base_config"], raw["params"], space)


def test_validation_is_read_only_and_budgeted(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    result = audit.validate(PLAN)
    assert result["ok"]
    assert result["trajectory_budget"] == 110
    assert result["seed_preflight"]["overlap"] == []
    assert result["simulations_started"] == 0


@pytest.mark.parametrize("profile_id", [
    "base_hazard_linear", "base_logit_linear", "msm_log_linear", "joint_hazard_log",
    "stepwise_both",
])
def test_geometry_profiles_preserve_every_original_anchor(profile_id):
    plan = audit.load_plan(PLAN)
    profile = next(value for value in plan["profiles"] if value["id"] == profile_id)
    base = _base()
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
    base = _base()
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
