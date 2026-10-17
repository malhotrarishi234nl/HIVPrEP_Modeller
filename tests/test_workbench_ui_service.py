import json
from pathlib import Path

import pytest

from workbench_ui.service import (
    AnalysisDraft,
    UIWorkbenchService,
    WorkbenchUIError,
    parse_number_series,
    parse_seed_series,
    validate_draft,
)
from workbench_ui.observations import load_observations
from model_workbench.workers import (
    ALL_AVAILABLE_WORKERS,
    effective_worker_count,
    probe_parallel_processes,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
)


def test_number_series_accepts_list_and_inclusive_decimal_grid():
    assert parse_number_series("0, 0.2, .5", label="p") == (0.0, 0.2, 0.5)
    assert parse_number_series("0:0.5:0.1", label="p") == (
        0.0, 0.1, 0.2, 0.3, 0.4, 0.5
    )


@pytest.mark.parametrize("raw", ["0:1", "1:0:0.1", "0:1:0", "not-a-number"])
def test_number_series_rejects_invalid_inputs(raw):
    with pytest.raises(WorkbenchUIError):
        parse_number_series(raw, label="p")


def test_seed_series_supports_ranges_and_rejects_duplicates():
    assert parse_seed_series("42-44, 50") == (42, 43, 44, 50)
    with pytest.raises(WorkbenchUIError, match="unique"):
        parse_seed_series("42, 42")


def test_draft_validation_enforces_model_p_and_seed_contract():
    bad = AnalysisDraft(
        name="test",
        config_path=CANONICAL,
        model="both",
        p_values=(),
        trajectories=2,
        seeds=(42,),
    )
    errors = validate_draft(bad, ROOT)
    assert "M1 requires at least one p value" in errors
    assert "the number of seeds must equal the number of trajectories" in errors


def test_all_available_worker_setting_is_portable_and_capped_to_work():
    draft = AnalysisDraft(
        name="portable workers",
        config_path=CANONICAL,
        model="M0",
        trajectories=2,
        seeds=(42, 43),
        workers=ALL_AVAILABLE_WORKERS,
    )
    assert validate_draft(draft, ROOT) == []
    assert effective_worker_count(draft.workers, draft.trajectories) <= 2


def test_all_available_worker_probe_is_bounded_and_uses_distinct_processes():
    result = probe_parallel_processes(ALL_AVAILABLE_WORKERS, max_probe_workers=2)
    assert result["status"] == "PASS"
    assert 1 <= result["tested"] <= 2
    assert result["child_processes"] == result["tested"]


def test_contract_controls_merge_must_equal_paths():
    service = UIWorkbenchService(ROOT)
    controls = service.contract_controls(CANONICAL)
    linked = [item for item in controls if len(item.paths) == 2]
    assert len(linked) == 1
    assert set(linked[0].paths) == {
        "population.p_msm.value",
        "demography.sexual_debut.p_msm.value",
    }
    exposed_paths = [path for control in controls for path in control.paths]
    assert len(exposed_paths) == len(set(exposed_paths))


def test_saved_recipe_round_trip_uses_ignored_operator_directory(tmp_path):
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "example.json").write_text("{}")
    service = UIWorkbenchService(root)
    draft = AnalysisDraft(
        name="M0 local check",
        config_path="configs/example.json",
        model="M0",
        trajectories=2,
        seeds=(42, 43),
        workers=1,
    )
    path = service.save_recipe(draft)
    assert path == root / "operator_configs/workbench_recipes/m0-local-check.json"
    payload = json.loads(path.read_text())
    assert payload["schema"] == "workbench_ui_recipe/1.0.0"
    assert service.load_recipe("m0-local-check") == draft
    with pytest.raises(WorkbenchUIError, match="already exists"):
        service.save_recipe(draft)


def test_available_results_uses_plain_labels_and_only_complete_runs(tmp_path):
    root = tmp_path / "repo"
    historical = root / "examples/workbench/historical_2008"
    historical.mkdir(parents=True)
    complete = root / "outputs/workbench/run-one"
    complete.mkdir(parents=True)
    (complete / "workbench_manifest.json").write_text(json.dumps({
        "status": "COMPLETE",
        "action": "benchmark",
        "elapsed_seconds": 8.5,
        "resolved_plan": {"controls": {"model": "M1", "p": 0.2}},
    }))
    failed = root / "outputs/workbench/run-two"
    failed.mkdir(parents=True)
    (failed / "workbench_manifest.json").write_text(json.dumps({
        "status": "FAILED",
        "resolved_plan": {"controls": {"model": "M0"}},
    }))

    rows = UIWorkbenchService(root).available_results()
    assert [row["kind"] for row in rows] == ["historical", "local"]
    assert rows[0]["label"] == "Default reference fit — 2008 treatment-era change (20 runs)"
    assert rows[1]["label"] == "Local benchmark: M1, p=0.2, 8.5s"


def test_configuration_registry_is_exposed_without_pandas():
    service = UIWorkbenchService(ROOT)
    rows = service.list_configurations()
    selected = next(row for row in rows if row["path"] == CANONICAL)
    assert selected["role_class"] == "current candidate"
    assert "REQUIRES_RECALIBRATION" in selected["scientific_status"]


def test_core_requests_preserve_consecutive_seed_and_p_selections():
    service = UIWorkbenchService(ROOT)
    draft = AnalysisDraft(
        name="paired routes",
        config_path=CANONICAL,
        model="both",
        p_values=(0.0, 0.2),
        trajectories=2,
        seeds=(42, 43),
    )
    requests = service.core_requests(draft)
    assert [item["label"] for item in requests] == ["M0", "M1 p=0", "M1 p=0.2"]
    assert all(item["overrides"]["seed"] == 42 for item in requests)
    assert all(item["overrides"]["replicates"] == 2 for item in requests)


def test_core_requests_expand_one_parameter_change_into_typed_controls():
    service = UIWorkbenchService(ROOT)
    path = "transmission_model.disease.transmission.advanced.beta_per_route.vaginal_mtf.chronic.value"
    draft = AnalysisDraft(
        name="higher transmission beta",
        config_path=CANONICAL,
        model="M0",
        trajectories=1,
        seeds=(42,),
        parameter_overrides={path: 0.001},
    )
    request = service.core_requests(draft)[0]
    assert request["overrides"][f"parameter:{path}"] == pytest.approx(0.001)
    assert "parameter:population.p_msm.value" in request["overrides"]


def test_nonconsecutive_seeds_are_rejected_instead_of_silently_changed():
    draft = AnalysisDraft(
        name="explicit seeds",
        config_path=CANONICAL,
        model="M0",
        trajectories=2,
        seeds=(42, 50),
    )
    assert "the current runner requires consecutive seeds" in validate_draft(draft, ROOT)


def test_benchmark_returns_full_requested_run_estimate(tmp_path):
    class Estimate:
        def to_dict(self):
            return {
                "available": True,
                "seconds_low": 17.0,
                "seconds_central": 20.0,
                "seconds_high": 27.0,
                "basis": "test benchmark",
                "requested_work_units": 2,
            }

    class Core:
        def __init__(self):
            self.resolved_actions = []

        def resolve(self, recipe_id, action, overrides, run_root):
            self.resolved_actions.append(action)
            return {"action": action, "run_root": str(run_root)}

        def execute(self, plan, *, workers=1):
            assert workers == 1
            return {
                "status": "COMPLETE",
                "elapsed_seconds": 10.0,
                "completed_work_units": 1,
            }

        def estimate(self, plan, manifest, *, workers=1):
            assert workers == 1
            assert plan["action"] == "run"
            assert manifest["completed_work_units"] == 1
            return Estimate()

    service = UIWorkbenchService(ROOT)
    service._core = Core()
    draft = AnalysisDraft(
        name="timed M0",
        config_path=CANONICAL,
        model="M0",
        trajectories=2,
        seeds=(42, 43),
    )
    result = service.execute_draft(draft, "benchmark", tmp_path / "benchmark")
    assert service._core.resolved_actions == ["benchmark", "run"]
    assert result[0]["estimate"]["seconds_central"] == 20.0
    assert result[0]["estimate"]["requested_work_units"] == 2


def test_external_input_and_workbook_metadata_are_visible_and_patch_is_read_only():
    service = UIWorkbenchService(ROOT)
    summary, rows = service.external_inputs_status()
    assert summary["status"] == "CURRENT_ACTIVE_WORKFLOWS"
    workbook = next(
        item for item in rows if item["logical_id"] == "history_matching_target_workbook"
    )
    assert workbook["available"] is True
    assert workbook["hash_status"] == "match"
    metadata = service.workbook_metadata("../data/raw/validation/DiseaseData.xlsx")
    assert metadata["sheets"]
    sheet = metadata["sheets"][0]["name"]
    preview = service.workbook_sheet_preview(
        "../data/raw/validation/DiseaseData.xlsx", sheet, max_rows=5, max_columns=8
    )
    assert preview["sheet"] == sheet
    assert len(preview["rows"]) <= 5
    assert len(preview["columns"]) <= 8
    patch = json.loads(service.point_patch(
        workbook_path="../data/raw/validation/DiseaseData.xlsx",
        sheet=sheet,
        values={"Year": 2025, "Value": 123},
        note="review first",
    ))
    assert patch["operation"] == "append_row_for_review"
    assert patch["applied"] is False
    assert patch["source_workbook_sha256"] == metadata["sha256"]


def test_observation_overlay_preserves_roles_and_source_intervals():
    infections = load_observations(ROOT, "new_infections").frame
    assert not infections.empty
    assert set(infections["role"]) == {"cross_validation"}
    assert infections[["lower", "upper"]].notna().all(axis=1).all()

    diagnoses = load_observations(ROOT, "new_diagnoses_msm").frame
    assert set(diagnoses.loc[diagnoses["year"] == 2000, "role"]) == {"calibration"}
    assert set(diagnoses.loc[diagnoses["year"] == 2001, "role"]) == {"cross_validation"}
    assert diagnoses[["lower", "upper"]].isna().all(axis=1).all()
