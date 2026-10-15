"""Focused guards for the bounded stage-structured diagnosis audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from aids_observation import diagnosis_observation_counts
from run_v3 import SimulationEngineV3
from schedule_utils import TimeSchedule
from stage_structured_diagnosis import (
    StageStructuredDiagnosis,
    StageStructuredDiagnosisConfigError,
    load_stage_structured_diagnosis,
    stage_step_probability,
)
import run_stage_structured_diagnosis_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def _profile(config: dict, enabled: bool, rho: float, symptom: float) -> dict:
    out = copy.deepcopy(config)
    diagnosis = out["transmission_model"]["disease"]["diagnosis"]
    diagnosis["stage_structured_diagnosis"] = {
        "enabled": {"value": enabled},
        "routine_hazard_scale": {"value": rho},
        "aids_symptom_annual_probability": {"value": symptom},
    }
    return out


def _short_config() -> dict:
    config = json.loads((ROOT / "configs/1980_test.json").read_text())
    config["run_control"]["end_year"] = {"value": 1981}
    config["run_control"]["deterministic_mode"] = {"value": False}
    return config


def test_disabled_and_enabled_identity_are_exact_under_paired_seed():
    base = _short_config()
    disabled = SimulationEngineV3(_profile(base, False, 0.6, 0.85), seed=9182)
    identity = SimulationEngineV3(_profile(base, True, 1.0, 0.0), seed=9182)
    disabled.output_writer.enabled = identity.output_writer.enabled = False
    a, b = disabled.run(), identity.run()
    np.testing.assert_array_equal(a["final_state"], b["final_state"])
    pd.testing.assert_frame_equal(a["summary"], b["summary"], check_exact=True)


def test_quarterly_hazard_conversion_matches_hand_calculation():
    p = stage_step_probability(
        0.2, routine_hazard_scale=0.6,
        aids_symptom_annual_probability=0.35,
        years_per_step=0.25, aids_stage=True,
    )
    expected = 1.0 - np.exp(-(
        0.6 * -np.log(0.8) + -np.log(0.65)
    ) * 0.25)
    assert p == pytest.approx(expected)


def test_symptom_uplift_is_aids_only_and_not_msm_multiplied():
    e = object.__new__(SimulationEngineV3)
    e.state = SimpleNamespace(population=np.ones((1, 2, 1, 4, 2, 1, 1), dtype=int))
    e.state.population[:, :, :, 0] = 0
    e.groups = SimpleNamespace(
        orient_to_idx={"Female": 0, "MSM": 1},
        hiv_to_idx={"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3},
    )
    e.time = SimpleNamespace(years_per_step=0.25)
    e.msm_relative_diagnosis_schedule = TimeSchedule(
        {"2000": 2.0}, interpolation="stepwise", default_before=1.0
    )
    rates = {}

    def draw(name, pool, probability):
        rates[name] = probability
        return np.zeros_like(pool)

    e._draw_events_for = draw
    result = e._step_stage_structured_diagnosis(
        2000, 0.2, 1, StageStructuredDiagnosis(True, 0.8, 0.35)
    )
    assert rates["stage_structured_diagnosis_non_msm_aids"] > \
        rates["stage_structured_diagnosis_non_msm_non_aids"]
    expected_msm_aids = stage_step_probability(
        0.4, routine_hazard_scale=0.8,
        aids_symptom_annual_probability=0.35,
        years_per_step=0.25, aids_stage=True,
    )
    assert rates["stage_structured_diagnosis_msm_aids"] == pytest.approx(expected_msm_aids)
    assert result["total"] == result["msm"] == 0


@pytest.mark.parametrize("routine,symptom,dt", [(-0.1, 0, 0.25), (1.1, 0, 0.25),
                                                 (0.2, float("nan"), 0.25),
                                                 (0.2, 0.3, 0)])
def test_invalid_inputs_fail_closed(routine, symptom, dt):
    with pytest.raises(StageStructuredDiagnosisConfigError):
        stage_step_probability(
            routine, routine_hazard_scale=0.8,
            aids_symptom_annual_probability=symptom,
            years_per_step=dt, aids_stage=True,
        )


def test_probability_one_is_safe_absorbing_limit():
    assert stage_step_probability(
        1.0, routine_hazard_scale=0.6,
        aids_symptom_annual_probability=0.0,
        years_per_step=0.25, aids_stage=False,
    ) == 1.0


def test_stage_counters_partition_total_msm_and_non_msm():
    draw = np.arange(12).reshape(1, 2, 1, 3, 2, 1)
    counts = diagnosis_observation_counts(draw, 3, 1)
    total = 0
    for stage in ("acute", "chronic", "aids"):
        stem = f"hiv_diagnoses_in_{stage}_stage"
        assert counts[stem] == counts[stem + "_msm"] + counts[stem + "_non_msm"]
        total += counts[stem]
    assert total == int(draw.sum())


def test_block_validation_is_strict_and_disabled_values_do_not_activate():
    assert load_stage_structured_diagnosis({}).enabled is False
    with pytest.raises(StageStructuredDiagnosisConfigError):
        load_stage_structured_diagnosis({"disease": {"diagnosis": {
            "stage_structured_diagnosis": {
                "enabled": {"value": True},
                "routine_hazard_scale": {"value": 1.2},
            }
        }}})


def test_frozen_runner_design_and_validate_only_contract():
    plan = audit.load_plan()
    assert plan["trajectory_budget"] == 2 * 12 * 10 == 240
    validation = audit.validate()
    assert validation["ok"] is True
    assert validation["simulations_started"] == 0
    assert validation["engines_constructed"] == 0
    assert validation["artifacts_written"] == 0
    assert len(validation["configurations"]) == 24
    for candidate in plan["candidate_ids"]:
        rows = [row for row in validation["configurations"]
                if row["candidate_id"] == candidate]
        assert len({row["config_without_profile_sha256"] for row in rows}) == 1
        assert len({row["config_sha256"] for row in rows}) == 12


def test_output_directory_requires_explicit_resume(tmp_path):
    output = tmp_path / "occupied"
    output.mkdir()
    with pytest.raises(Exception, match="already exists"):
        audit._prepare_output(output, audit.DEFAULT_PLAN, {}, resume=False)
