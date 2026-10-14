from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_scenarios import MultiScenarioRunner
from tools.prepare_prep_policy_suite import build_bridge_runtime, materialize, validate


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "configs/hta_prep_policy_suite_20260929.overlay.json"


def test_policy_suite_expands_to_reviewed_matrix():
    overlay, runtime = materialize(OVERLAY)
    plan = validate(overlay, runtime)
    assert plan["expanded_scenario_count"] == 19
    names = plan["expanded_scenarios"]
    assert "base_case_context" in names
    assert "scenario_oral_matched_add_1500" in names
    assert "scenario_oral_wang_28000" in names
    assert "scenario_lena_substitution__switch_50" in names
    assert "scenario_lena_add_on__add_on_1500" in names
    assert plan["trajectory_budget"]["maximum_total"] == 750
    assert not plan["launch_ready"]


def test_bridge_materializes_only_the_five_reviewed_scenarios():
    overlay, runtime = materialize(OVERLAY)
    bridge_runtime = build_bridge_runtime(runtime)
    plan = validate(overlay, runtime, bridge_runtime)
    assert plan["bridge_scenarios"] == [
        "base_case_context",
        "scenario_oral_matched_add_1500",
        "scenario_oral_wang_28000",
        "scenario_lena_substitution__switch_50",
        "scenario_lena_add_on__add_on_1500",
    ]


def test_oral_comparator_is_held_at_13000():
    _, runtime = materialize(OVERLAY)
    path = runtime["prep_target_pathways"]["oral_hold_13000"]
    assert set(path["target_stock"]["value"].values()) == {13000}
    assert runtime["base_cases"]["oral_prep_standard_of_care"]["interventions"] == [
        {"pathway": "oral_hold_13000"}
    ]


def test_persistence_uses_exact_annual_to_quarterly_conversion():
    overlay, runtime = materialize(OVERLAY)
    plan = validate(overlay, runtime)
    expected = overlay["persistence"]["quarterly_equivalents"]
    for annual_text, quarterly in expected.items():
        annual = float(annual_text)
        assert quarterly == pytest.approx(1 - (1 - annual) ** 0.25, abs=1e-15)
    assert expected["0.05"] == pytest.approx(0.012741455098566168)
    assert expected["0.05"] != pytest.approx(0.01227, abs=1e-6)
    assert {row["annual_probability"] for row in plan["persistence_scenarios"]} == {
        0.05,
        0.10,
        0.20,
        0.30,
    }


def test_loader_preserves_scenario_specific_discontinuation_override():
    overlay, runtime = materialize(OVERLAY)
    plan = validate(overlay, runtime)
    row = next(
        item
        for item in plan["persistence_scenarios"]
        if item["scenario"]
        == "scenario_lena_persistence_add_on_1500_annual_30"
    )
    assert row["annual_probability"] == 0.30
    assert row["quarterly_probability"] == pytest.approx(0.08530878077130559)


def test_runner_applies_persistence_override_to_lena_arm(tmp_path):
    _, runtime = materialize(OVERLAY)
    runtime_path = tmp_path / "hta_runtime_config.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    runner = MultiScenarioRunner(
        str(ROOT / "configs/model_final.json"),
        str(tmp_path / "unused_outputs"),
        str(runtime_path),
    )
    expected = {
        "scenario_lena_substitution__switch_50": 0.05,
        "scenario_lena_persistence_substitution_50_annual_20": 0.20,
        "scenario_lena_persistence_add_on_1500_annual_30": 0.30,
    }
    by_name = {item["name"]: item for item in runner.hta_config["analysis_scenarios"]}
    for name, annual_probability in expected.items():
        arm_config = runner._build_arm_config("oral_prep_plus_lena", by_name[name])
        assert (
            arm_config["prep_schedules"]["Lenacapavir"]["discontinuation"]
            ["schedule"]["2025"]
        ) == annual_probability


def test_overlay_is_plain_json_and_records_reporting_profile():
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    assert overlay["report_profile"]["workbook_tabs"][0] == "Executive summary"
    assert "Paired 2008 minus 2010 differences" in overlay["report_profile"]["figures"]
