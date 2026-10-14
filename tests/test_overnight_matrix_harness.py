from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import run_policy_matrix as policy
import run_psa_hta_matrix as psa_matrix
from tools.finalize_conference_suite import finalize as finalize_conference


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "configs/model_final.json"
BRIDGE = (
    ROOT
    / "outputs/suites/prep_policy_suite_20260929_prepared"
    / "hta_runtime_bridge_2010.json"
)
PSA = ROOT / "configs/psa_conference_20260929.json"


def test_psa_matrix_prepares_shared_draws_and_stable_shards(tmp_path):
    root = tmp_path / "psa"
    plan = psa_matrix.prepare(
        model_config=MODEL,
        hta_config=BRIDGE,
        psa_config=PSA,
        output_dir=root,
        n_draws=3,
        seed=12345,
        shard_count=2,
        scenario_names=["base_case_context", "scenario_oral_matched_add_1500"],
    )
    assert plan["simulations_started"] == 0
    assert plan["task_count"] == 6
    tasks = pd.read_csv(root / "tasks.csv")
    assert tasks["shard"].tolist() == [0, 1, 0, 1, 0, 1]
    assert tasks.groupby("draw_index")["scenario"].nunique().eq(2).all()


def test_psa_matrix_finalizes_compact_long_outputs(tmp_path):
    root = tmp_path / "psa"
    psa_matrix.prepare(
        model_config=MODEL,
        hta_config=BRIDGE,
        psa_config=PSA,
        output_dir=root,
        n_draws=2,
        seed=12345,
        shard_count=2,
        scenario_names=["base_case_context"],
    )
    tasks = pd.read_csv(root / "tasks.csv").to_dict("records")
    for task in tasks:
        shard = root / "shards" / f"shard_{int(task['shard']):03d}"
        result = {
            "status": "COMPLETE",
            **task,
            "incremental": {
                "oral_prep_vs_no_prep": {
                    "delta_cost": 10.0,
                    "delta_qaly": 1.0,
                    "icer": 10.0,
                    "nmb": 79_990.0,
                    "infections_averted": 2.0,
                }
            },
            "arm_results": {
                "no_prep": {
                    "total_costs_disc": 100.0,
                    "total_costs_disc_societal": 100.0,
                    "total_qalys_disc": 1000.0,
                    "cumul_new_infections": 20.0,
                }
            },
        }
        psa_matrix._atomic_json(psa_matrix._result_path(shard, task), result)
    manifest = psa_matrix.finalize(plan_path=root / "plan.json")
    assert manifest["status"] == "PSA_MATRIX_COMPLETE"
    summary = pd.read_csv(root / "psa_matrix_summary.csv")
    assert summary.loc[0, "draws"] == 2
    assert summary.loc[0, "probability_cost_effective"] == pytest.approx(1.0)


def test_policy_matrix_preparation_matches_reviewed_bridge_budget(tmp_path):
    plan = policy.prepare(MODEL, BRIDGE, tmp_path / "policy", [42, 43])
    assert plan["simulations_started"] == 0
    assert plan["scenario_count"] == 5
    assert plan["forward_arms_per_seed"] == 10
    assert plan["maximum_trajectories"] == 22
    saved = json.loads((tmp_path / "policy/plan.json").read_text())
    assert saved["seed_policy"] == "common_random_numbers_across_all_scenarios"


def test_conference_finalizer_pairs_identical_seeds_and_draws(tmp_path):
    root = tmp_path / "suite"
    for relative in (
        "policy_primary_2008", "policy_bridge_2010",
        "psa_primary_2008", "psa_bridge_2010",
    ):
        (root / relative).mkdir(parents=True)
    for relative in ("policy_primary_2008", "policy_bridge_2010"):
        (root / relative / "final_manifest.json").write_text(
            json.dumps({"status": "POLICY_MATRIX_FINALIZED"})
        )
    for relative in ("psa_primary_2008", "psa_bridge_2010"):
        (root / relative / "final_manifest.json").write_text(
            json.dumps({"status": "PSA_MATRIX_COMPLETE"})
        )
    policy_rows = pd.DataFrame([{
        "seed": 42,
        "scenario": "shared",
        "delta_cost": 10.0,
        "delta_qaly": 2.0,
        "nmb": 20.0,
        "infections_averted": 3.0,
        "reference_cost": 100.0,
        "intervention_cost": 110.0,
        "reference_qaly": 1000.0,
        "intervention_qaly": 1002.0,
    }])
    policy_rows.to_csv(root / "policy_primary_2008/policy_seed_results.csv", index=False)
    older = policy_rows.copy()
    older["delta_cost"] = 8.0
    older.to_csv(root / "policy_bridge_2010/policy_seed_results.csv", index=False)
    psa_rows = pd.DataFrame([{
        "draw_index": 0,
        "scenario": "shared",
        "comparison": "intervention_vs_reference",
        "delta_cost": 12.0,
        "delta_qaly": 1.5,
        "nmb": 30.0,
        "infections_averted": 2.0,
    }])
    psa_rows.to_csv(root / "psa_primary_2008/psa_matrix_incremental.csv", index=False)
    psa_older = psa_rows.copy()
    psa_older["nmb"] = 25.0
    psa_older.to_csv(root / "psa_bridge_2010/psa_matrix_incremental.csv", index=False)
    manifest = finalize_conference(root)
    assert manifest["status"] == "CONFERENCE_RESULTS_BOUND"
    paired = pd.read_csv(root / "combined/paired_policy_2008_minus_2010.csv")
    assert paired.loc[0, "difference__2008_minus_2010__delta_cost"] == pytest.approx(2.0)
    paired_psa = pd.read_csv(root / "combined/paired_psa_2008_minus_2010.csv")
    assert paired_psa.loc[0, "difference__2008_minus_2010__nmb"] == pytest.approx(5.0)
