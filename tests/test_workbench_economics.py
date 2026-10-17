from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pandas as pd

from workbench_ui.economics import (
    ECONOMIC_MANIFEST,
    EconomicDraft,
    cost_scope_label,
    economic_controls,
    execute_economic_draft,
    load_economic_result,
    policy_scenario_matrix,
    validate_economic_draft,
)
from workbench_ui.service import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]


class EconomicFakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        output = Path(argv[argv.index("--output_dir") + 1])
        pd.DataFrame([{
            "scenario": "scenario_a_lena_substitution",
            "arm": "oral_prep",
            "total_qalys_disc": 1000,
            "total_costs_disc": 2000,
            "total_costs_disc_societal": 2100,
            "cumul_new_infections": 50,
            "cost_adverse_events_disc": 0,
        }]).to_csv(output / "arm_summary.csv", index=False)
        pd.DataFrame([{
            "scenario": "scenario_a_lena_substitution",
            "comparison": "lenacapavir_vs_oral_prep",
            "delta_cost": 100,
            "delta_qaly": 2,
            "icer": 50,
            "nmb": 159900,
            "infections_averted": 3,
            "dominance": "trade-off",
        }]).to_csv(output / "comparison_table.csv", index=False)
        return subprocess.CompletedProcess(argv, 0)


def test_economic_controls_include_costs_and_adverse_events():
    controls = economic_controls(ROOT)
    groups = {control.group for control in controls}
    assert "Prevention costs" in groups
    assert "HIV care costs" in groups
    assert "Adverse events" in groups
    assert "Patient time and productivity" in groups
    assert any("prevalence" in control.path for control in controls if control.group == "Adverse events")


def test_policy_matrix_exposes_all_reviewed_rows():
    rows = policy_scenario_matrix(ROOT)
    ids = {row.id for row in rows}
    assert len(rows) == 19
    assert "base_case_context" in ids
    assert {f"scenario_lena_substitution__switch_{value}" for value in (10, 25, 50, 75, 100)} <= ids
    assert {f"scenario_lena_add_on__add_on_{value}" for value in (500, 1500, 3000)} <= ids
    assert cost_scope_label(True, False) == "Healthcare payer + patient time"


def test_explicit_empty_policy_matrix_is_rejected():
    draft = EconomicDraft(
        name="empty matrix",
        base_config=DEFAULT_CONFIG,
        scenario_ids=(),
    )
    assert "select at least one policy or reference comparison" in " ".join(
        validate_economic_draft(ROOT, draft)
    )


def test_economic_draft_validation_and_execution_are_typed(tmp_path):
    runner = EconomicFakeRunner()
    draft = EconomicDraft(
        name="economic check",
        base_config=DEFAULT_CONFIG,
        seeds=(42, 43),
        costing_overrides={"wtp_threshold_eur_per_qaly.value": 90000},
        workers=2,
    )
    assert validate_economic_draft(ROOT, draft) == []
    output_root = ROOT / "outputs" / "workbench_economic"
    run_root = output_root / f"pytest-{tmp_path.name}"
    if run_root.exists():
        raise AssertionError(f"test output already exists: {run_root}")
    try:
        manifest = execute_economic_draft(ROOT, draft, run_root, runner=runner)
        assert manifest["status"] == "COMPLETE"
        assert len(runner.calls) == 2
        assert manifest["execution"]["effective_workers"] == 2
        assert all(call[1]["shell"] is False for call in runner.calls)
        costing = json.loads((run_root / "configs/costing.json").read_text())
        assert costing["wtp_threshold_eur_per_qaly"]["value"] == 90000
        assert json.loads((run_root / ECONOMIC_MANIFEST).read_text())["status"] == "COMPLETE"
        result = load_economic_result(run_root)
        assert set(result.comparisons["seed"]) == {42, 43}
        assert set(result.arms["seed"]) == {42, 43}
    finally:
        import shutil
        shutil.rmtree(run_root, ignore_errors=True)


def test_selected_matrix_and_cost_components_are_written_to_derived_configs(tmp_path):
    runner = EconomicFakeRunner()
    selected = (
        "base_case_context",
        "scenario_oral_matched_add_1500",
        "scenario_lena_substitution__switch_50",
    )
    draft = EconomicDraft(
        name="selected matrix",
        base_config=DEFAULT_CONFIG,
        seeds=(42,),
        scenario_ids=selected,
        primary_scenario_family="scenario_lena_substitution",
        include_patient_time=False,
        include_productivity_loss=True,
        evaluation_years=20,
    )
    assert validate_economic_draft(ROOT, draft) == []
    run_root = ROOT / "outputs" / "workbench_economic" / f"pytest-matrix-{tmp_path.name}"
    try:
        manifest = execute_economic_draft(ROOT, draft, run_root, runner=runner)
        runtime = json.loads((run_root / "configs/runtime.json").read_text())
        costing = json.loads((run_root / "configs/costing.json").read_text())
        assert runtime["study_defaults"]["evaluation_years"] == 20
        assert set(runtime["scenarios"]) == {
            "scenario_oral_matched_add_1500",
            "scenario_lena_substitution",
        }
        assert runtime["prep_target_pathways"]["lena_substitution_grid"]["switch_share_of_eligible_oral_users"]["value"] == [0.5]
        assert costing["perspective"]["primary"] == "societal"
        assert costing["perspective"]["patient_time_costs"]["enabled"] is False
        assert costing["perspective"]["productivity_loss"]["enabled"] is True
        assert manifest["cost_scope"] == "Healthcare payer + productivity loss"
        assert manifest["policy_period"] == {
            "start_year": 2025,
            "evaluation_years": 20,
        }
    finally:
        import shutil
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_configs_keep_document_order(tmp_path):
    """The HTA validator needs a distribution's source parameter to come earlier in the file."""
    import json

    from workbench_ui.economics import _write_json

    target = tmp_path / "costing.json"
    _write_json(target, {"utilities": {"hiv_chronic_on_art": 1, "aids_on_art": 2}})
    assert list(json.loads(target.read_text())["utilities"]) == ["hiv_chronic_on_art", "aids_on_art"]
