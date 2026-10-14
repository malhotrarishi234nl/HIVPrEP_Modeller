import copy

import pandas as pd
import pytest

from tools.run_stage_diagnosis_matched_hta import (
    paired_structure_differences,
    validate_matched_configs,
)


def test_config_match_guard_allows_only_diagnosis_differences():
    legacy = {
        "shared": {"x": 1},
        "transmission_model": {"disease": {"diagnosis": {"enabled": False}}},
    }
    candidate = copy.deepcopy(legacy)
    candidate["transmission_model"]["disease"]["diagnosis"]["enabled"] = True
    changed = validate_matched_configs({"legacy": legacy, "candidate": candidate})
    assert changed == ["transmission_model.disease.diagnosis.enabled"]


def test_config_match_guard_rejects_non_diagnosis_difference():
    legacy = {
        "shared": {"x": 1},
        "transmission_model": {"disease": {"diagnosis": {"enabled": False}}},
    }
    candidate = copy.deepcopy(legacy)
    candidate["shared"]["x"] = 2
    with pytest.raises(ValueError, match="non-diagnosis"):
        validate_matched_configs({"legacy": legacy, "candidate": candidate})


def test_paired_structure_difference_is_candidate_minus_legacy():
    records = []
    for structure, offset in (("legacy", 0.0), ("candidate", 2.0)):
        for seed in (42, 43):
            records.append(
                {
                    "structure": structure,
                    "seed": seed,
                    "scenario": "base_case_context",
                    "delta_cost": 10.0 + seed + offset,
                    "delta_qaly": 20.0 + seed + offset,
                    "infections_averted": 30.0 + seed + offset,
                    "nmb_20k": 40.0 + seed + offset,
                    "nmb_50k": 50.0 + seed + offset,
                    "nmb_80k": 60.0 + seed + offset,
                }
            )
    # The helper expects every configured scenario. Replicate the simple fixture.
    base = pd.DataFrame(records)
    frames = []
    from tools.run_stage_diagnosis_matched_hta import SCENARIOS

    for scenario in SCENARIOS:
        frame = base.copy()
        frame["scenario"] = scenario
        frames.append(frame)
    detail, summary = paired_structure_differences(pd.concat(frames, ignore_index=True))
    assert (detail["candidate_minus_legacy"] == 2.0).all()
    assert (summary["mean"] == 2.0).all()
