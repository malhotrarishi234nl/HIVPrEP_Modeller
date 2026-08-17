import pandas as pd

from hta_reliability import (
    baseline_year_coverage,
    calibration_handoff_rows,
    infer_steps_per_year,
    summarize_calibration_handoff,
)
from run_scenarios import _force_deterministic_hta_base_case


def test_summarize_calibration_handoff_warns_when_metadata_missing():
    summary = summarize_calibration_handoff({"meta": {"version": {"value": "x"}}})

    assert summary["status"] == "unversioned"
    assert summary["is_lhs_calibrated"] is False
    assert "No LHS calibration metadata" in summary["label"]
    assert summary["warnings"]


def test_summarize_calibration_handoff_reports_joint_rank_one_fit():
    config = {
        "run_control": {"deterministic_mode": {"value": True}},
        "calibration_metadata": {
            "lhs_calibration": {
                "stage": "joint",
                "rank": 1,
                "draw_id": 17660,
                "total_ll": -236373.6856,
                "created_at": "2026-05-21T14:28:41",
                "family_balanced_score": True,
                "rerank_top": 25,
                "rerank_replicates": 10,
                "parameters": {"x": 1.0},
            }
        }
    }

    summary = summarize_calibration_handoff(config)
    rows = calibration_handoff_rows(config)

    assert summary["status"] == "lhs_calibrated"
    assert summary["warnings"] == []
    assert "draw 17660" in summary["label"]
    assert rows[0][0] == "Calibration handoff"
    assert "family_balanced=True" in rows[3][1]


def test_summarize_calibration_handoff_warns_on_metadata_runtime_mismatch():
    config = {
        "run_control": {"deterministic_mode": {"value": False}},
        "calibration_metadata": {
            "lhs_calibration": {
                "stage": "joint",
                "rank": 1,
                "draw_id": 17660,
                "deterministic_calibration": True,
                "parameters": {"x": 1.0},
            }
        },
    }

    summary = summarize_calibration_handoff(config)

    assert summary["runtime_deterministic"] is False
    assert any("deterministic_calibration=True" in warning for warning in summary["warnings"])


def test_force_deterministic_hta_base_case_sets_authoritative_run_control_flag():
    config = {
        "run_control": {"deterministic_mode": {"value": False}},
        "simulation_controls": {"deterministic_mode": {"value": False}},
    }

    _force_deterministic_hta_base_case(config)

    assert config["run_control"]["deterministic_mode"]["value"] is True
    assert config["simulation_controls"]["deterministic_mode"]["value"] is True


def test_baseline_year_coverage_flags_freeze_year_partial_row():
    df = pd.DataFrame(
        {
            "year": [2024.0, 2024.25, 2024.5, 2024.75, 2025.0],
            "new_infections": [10, 11, 12, 13, 14],
        }
    )

    coverage = baseline_year_coverage(df)
    by_year = coverage.set_index("year_int")

    assert infer_steps_per_year(df["year"]) == 4
    assert by_year.loc[2024, "timesteps_in_year"] == 4
    assert bool(by_year.loc[2024, "complete_year"]) is True
    assert by_year.loc[2025, "timesteps_in_year"] == 1
    assert bool(by_year.loc[2025, "complete_year"]) is False
