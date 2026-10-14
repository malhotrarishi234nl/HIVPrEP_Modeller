from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import run_post2015_stock_flow_decomposition as audit

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/post2015_stock_flow_decomposition_20260921.json"


class Groups:
    orientations = ["Female", "Heterosexual_Male", "MSM"]
    activities = ["Low", "Medium", "High"]
    hiv_stages = ["Susceptible", "Acute", "Chronic", "AIDS"]
    diagnosis_states = ["Undiagnosed", "Diagnosed"]
    art_states = ["Not_on_ART", "On_ART"]
    prep_states = ["None", "Oral_PrEP", "Lenacapavir"]
    orient_to_idx = {name: i for i, name in enumerate(orientations)}
    activity_to_idx = {name: i for i, name in enumerate(activities)}
    hiv_to_idx = {name: i for i, name in enumerate(hiv_stages)}
    prep_to_idx = {name: i for i, name in enumerate(prep_states)}
    n_activities = 3


def test_plan_is_exactly_two_candidates_by_ten_reused_seeds():
    plan = audit.load_plan(PLAN)
    assert plan["candidate_ids"] == [1326, 1788]
    assert plan["replicate_seeds"] == list(range(200600, 200610))
    assert plan["trajectory_budget"] == 20
    assert plan["stock_years"] == list(range(2015, 2026))


def test_stock_vector_is_activity_resolved_and_linear():
    shape = (1, 3, 3, 4, 2, 2, 3)
    a = np.zeros(shape)
    b = np.zeros(shape)
    msm = Groups.orient_to_idx["MSM"]
    # Medium: susceptible on oral PrEP.
    a[0, msm, 1, 0, 0, 0, 1] = 7
    # High: undiagnosed PLHIV off ART.
    b[0, msm, 2, 2, 0, 0, 0] = 5
    # Low: diagnosed PLHIV on ART.
    b[0, msm, 0, 3, 1, 1, 0] = 3
    combined = audit.stock_vector(a + b, Groups)
    assert np.array_equal(combined, audit.stock_vector(a, Groups) + audit.stock_vector(b, Groups))
    assert combined[1, audit.METRICS.index("susceptible_msm")] == 7
    assert combined[1, audit.METRICS.index("oral_prep_msm")] == 7
    assert combined[2, audit.METRICS.index("undiagnosed_plhiv_msm")] == 5
    assert combined[0, audit.METRICS.index("on_art_plhiv_msm")] == 3


def test_validation_is_read_only(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    result = audit.validate(PLAN)
    assert result["ok"]
    assert result["trajectory_budget"] == 20
    assert result["simulations_started"] == 0
    assert result["activity_movement"].startswith("not_applicable")
