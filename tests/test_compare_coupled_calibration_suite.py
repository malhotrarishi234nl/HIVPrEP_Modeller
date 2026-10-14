import json
from pathlib import Path

import pandas as pd
import pytest

import compare_coupled_calibration_suite as adapter
import run_coupled_calibration as runner


def _write_case(root: Path, case_id: str, design: str, means: list[float]) -> None:
    case = root / "cases" / case_id
    plan = runner.plan_payload()
    plan["design"]["name"] = design
    plan["design"]["question"] = f"Synthetic {case_id} test case"
    plan["seeds"].update({
        "comparison_group": "test_crn_group",
        "paired_designs": ["design_2010", "design_2008"],
        "reuse_policy": "common_random_numbers_across_declared_designs_only",
    })
    plan_path = case / "resolved_configs/coupled_search_plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan))
    search = case / "calibration/search"
    confirmation = case / "calibration/confirmation"
    search.mkdir(parents=True)
    confirmation.mkdir(parents=True)
    (search / "manifest.json").write_text(json.dumps({
        "design": design, "status": "SEARCH_COMPLETE",
        "seed_comparison_group": "test_crn_group",
    }))
    (confirmation / "manifest.json").write_text(json.dumps({
        "design": design, "status": "CONFIRMATION_COMPLETE",
        "seed_comparison_group": "test_crn_group", "n_confirmation_primary_pass": 1,
    }))
    pd.DataFrame([
        {"candidate_id": 1, "confirmation_primary_pass": False,
         "confirmation_primary_i_max": 4.0, "confirmation_j": 2.0},
        {"candidate_id": 2, "confirmation_primary_pass": True,
         "confirmation_primary_i_max": 2.0, "confirmation_j": 3.0},
    ]).to_csv(confirmation / "confirmation_summary.csv", index=False)
    pd.DataFrame([
        {"candidate_id": candidate, "target_key": key,
         "confirmation_mean": mean + candidate,
         "confirmation_sample_variance": 4.0,
         "confirmation_primary_implausibility": 1.0 + index}
        for candidate in (1, 2)
        for index, (key, mean) in enumerate(zip(
            ("shm_new_diagnoses_msm@2000", "rivm_aids_msm@2000"), means
        ))
    ]).to_csv(confirmation / "paired_discovery_confirmation.csv", index=False)
    raw_dir = confirmation / "raw"
    raw_dir.mkdir()
    seeds = plan["seeds"]["confirmation"]
    for candidate in (1, 2):
        per_seed = []
        for offset, seed in enumerate(seeds):
            per_seed.append({
                "seed": seed,
                "annual_outputs": {
                    "new_diagnoses_msm": {"2000": means[0] + candidate + offset},
                    "aids_first_diagnosis_proxy_msm": {
                        "2000": means[1] + candidate + 2 * offset
                    },
                },
            })
        (raw_dir / f"candidate_{candidate}.json").write_text(json.dumps({
            "candidate_id": candidate, "replicate_seeds": seeds, "per_seed": per_seed,
        }))


@pytest.fixture
def completed_suite(tmp_path: Path) -> Path:
    suite = {
        "comparison": {"id": "break_test", "cases": ["break_2010", "break_2008"]}
    }
    (tmp_path / "suite_source.json").write_text(json.dumps(suite))
    _write_case(tmp_path, "break_2010", "design_2010", [100.0, 200.0])
    _write_case(tmp_path, "break_2008", "design_2008", [110.0, 180.0])
    return tmp_path


def test_adapter_validates_and_writes_normalized_comparison(completed_suite, tmp_path):
    normalized = adapter.load_comparison(completed_suite)
    assert normalized["comparison_group"] == "test_crn_group"
    assert normalized["case_summary"]["selected_candidate_id"].tolist() == [2, 2]

    output = tmp_path / "comparison"
    manifest = adapter.write_comparison(completed_suite, output)
    assert manifest["status"] == "COMPARISON_COMPLETE"
    assert manifest["simulations_started"] == 0
    targets = pd.read_csv(output / "target_comparison.csv").set_index("target_key")
    delta = "delta_confirmation_mean__break_2008_minus_break_2010"
    assert targets.loc["shm_new_diagnoses_msm@2000", delta] == pytest.approx(10.0)
    assert targets.loc["rivm_aids_msm@2000", delta] == pytest.approx(-20.0)
    paired = pd.read_csv(output / "paired_seed_summary.csv").set_index("target_key")
    diagnosis = "shm_new_diagnoses_msm@2000"
    assert paired.loc[diagnosis, "mean_paired_difference"] == pytest.approx(10.0)
    assert paired.loc[diagnosis, "se_paired_difference"] == pytest.approx(0.0)
    assert paired.loc[diagnosis, "cross_case_correlation"] == pytest.approx(1.0)
    assert (output / "COMPARISON_REPORT.md").is_file()


def test_adapter_refuses_mismatched_crn(completed_suite):
    path = completed_suite / "cases/break_2008/resolved_configs/coupled_search_plan.json"
    plan = json.loads(path.read_text())
    plan["seeds"]["discovery"][0] += 10_000
    path.write_text(json.dumps(plan))
    with pytest.raises(adapter.ComparisonError, match="identical CRN"):
        adapter.load_comparison(completed_suite)
