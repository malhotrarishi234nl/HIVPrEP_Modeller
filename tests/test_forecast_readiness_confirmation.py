from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import run_forecast_readiness_confirmation as frc
from coupled_calibration_search import ContractError


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/forecast_readiness_confirmation_20260921.json"


def _synthetic_selection_source(tmp_path):
    """Current-code input tables exercising every selection-rule branch."""
    source = tmp_path / "selection_source"
    source.mkdir()
    pd.DataFrame([
        {"candidate_id": 11, "discovery_primary_pass": True, "search_j": 0.20,
         "search_rank": 1},
        {"candidate_id": 22, "discovery_primary_pass": True, "search_j": 0.30,
         "search_rank": 2},
        {"candidate_id": 33, "discovery_primary_pass": True, "search_j": 0.10,
         "search_rank": 3},
        {"candidate_id": 44, "discovery_primary_pass": False, "search_j": 0.05,
         "search_rank": 4},
        {"candidate_id": 55, "discovery_primary_pass": True, "search_j": 0.15,
         "search_rank": 5},
    ]).to_csv(source / "archive_summary.csv", index=False)
    targets = ["shm_new_diagnoses_msm@2019", "shm_new_diagnoses_msm@2024"]
    means = {
        11: (95.0, 105.0),
        22: (101.0, 99.0),
        33: (100.0, 100.0),
        44: (100.0, 100.0),
        55: (111.0, 100.0),
    }
    pd.DataFrame([
        {
            "candidate_id": candidate_id,
            "target_key": target,
            "replicate_mean": candidate_means[index],
            "observed_z": 100.0,
        }
        for candidate_id, candidate_means in means.items()
        for index, target in enumerate(targets)
    ]).to_csv(source / "signed_search_residuals.csv", index=False)
    pd.DataFrame([
        {"candidate_id": candidate_id, "within_5pct_of_bound": candidate_id == 33}
        for candidate_id in means
    ]).to_csv(source / "orientation_boundary_diagnostics.csv", index=False)
    plan = {
        "selection_rule": {
            "recent_targets": targets,
            "maximum_absolute_relative_mean_error": 0.10,
        },
        "selected_candidate_ids": [11, 22],
    }
    return source, plan


def test_historical_lineage_is_refused_for_module_hash_mismatch(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct a simulation engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    with pytest.raises(ContractError, match="code changed since the search") as caught:
        frc.validate(PLAN)
    assert "run_coupled_calibration.py" in str(caught.value)


def test_frozen_plan_contract_remains_budgeted():
    assert frc._load_plan(PLAN)["trajectory_budget"] == 100


def test_selection_is_recomputed_instead_of_trusting_declared_ids(tmp_path):
    source, plan = _synthetic_selection_source(tmp_path)
    selected, details = frc.derive_selection(source, plan)
    assert selected == [11, 22]
    assert len(details) == 2
    assert (details["shm_new_diagnoses_msm@2019"] <= 0.1).all()
    assert (details["shm_new_diagnoses_msm@2024"] <= 0.1).all()


def test_changed_selection_is_refused(tmp_path):
    source, plan = _synthetic_selection_source(tmp_path)
    plan["selected_candidate_ids"] = [11]
    with pytest.raises(ContractError, match="recomputed candidate selection"):
        frc.derive_selection(source, plan)


def test_seed_preflight_detects_recorded_collision(tmp_path):
    manifest_dir = tmp_path / "experiment"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(json.dumps({"seeds": [200400]}))
    with pytest.raises(ContractError, match="recorded collisions"):
        frc.seed_preflight([200400, 200401], outputs_root=tmp_path)
