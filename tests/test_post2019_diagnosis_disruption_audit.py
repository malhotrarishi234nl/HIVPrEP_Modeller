from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run_post2019_diagnosis_disruption_audit as audit
from coupled_calibration_search import ContractError
from schedule_utils import TimeSchedule

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/post2019_diagnosis_disruption_audit_20260921.json"
STALE_SEARCH_ERROR = (
    "code changed since the search: "
    "['aids_observation.py', 'calib_extractor.py', 'config_utils.py', "
    "'run_coupled_calibration.py', 'run_lhs_calibration.py', 'run_v3.py']"
)


def _synthetic_base():
    """Minimal pure fixture for profile transforms, never a calibration claim."""
    return {
        "run_control": {"deterministic_mode": {"value": False}},
        "transmission_model": {
            "disease": {
                "diagnosis": {
                    "msm_relative_diagnosis": {
                        "enabled": {"value": True},
                        "schedule": {
                            "value": {"1996": 0.7, "2004": 0.9, "2019": 1.0},
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


def test_profiles_are_neutral_before_2020_bounded_balanced_and_recovered():
    plan = audit.load_plan(PLAN)
    for profile in plan["profiles"][1:]:
        schedule = TimeSchedule(profile["factors"], interpolation="linear", default_before=1.0)
        assert all(schedule.get_value(float(year)) == 1.0
                   for year in np.arange(1975.0, 2020.0, 0.25))
        assert min(profile["factors"].values()) >= 0.65
        assert max(profile["factors"].values()) <= 1.30
        assert schedule.get_value(2024.0) == 1.0
        assert audit._quarterly_factor_mean(profile["factors"]) == pytest.approx(1.0, abs=0.02)


def test_profile_changes_only_post2019_msm_diagnosis_anchors():
    plan = audit.load_plan(PLAN)
    base = _synthetic_base()
    changed = audit.apply_profile(base, plan["profiles"][1])
    expected = json.loads(json.dumps(base))
    base_2019 = expected["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]["value"]["2019"]
    anchors = expected["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]["value"]
    for year, factor in plan["profiles"][1]["factors"].items():
        anchors[year] = base_2019 * factor
    assert changed == expected


def test_one_direction_profile_is_refused():
    plan = audit.load_plan(PLAN)
    plan["profiles"][1]["factors"] = {
        "2019.999999": 1.0, "2020": 0.8, "2021": 0.9,
        "2022": 1.0, "2023": 1.0, "2024": 1.0,
    }
    with pytest.raises(ContractError):
        audit.validate_profiles(plan)


def test_undiagnosed_stock_is_read_from_year_end_summary_not_calibration_extractor():
    trajectory = {
        "outputs": {},
        "summary": pd.DataFrame({
            "year": [2024.0, 2024.25, 2024.5, 2024.75],
            "msm_undiagnosed_hiv_pos": [110, 105, 101, 98],
        }),
    }
    assert audit._annual_value(trajectory, "msm_undiagnosed_hiv_pos", 2024) == 98.0
