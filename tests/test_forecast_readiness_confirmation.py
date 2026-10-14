from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_forecast_readiness_confirmation as frc
import run_coupled_calibration as rcc
from coupled_calibration_search import ContractError


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/forecast_readiness_confirmation_20260921.json"


def test_validation_is_read_only_and_selects_the_frozen_five(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct a simulation engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    report = frc.validate(PLAN)
    assert report["ok"]
    assert report["selected_candidate_ids"] == [1765, 1846, 1076, 1568, 1721]
    assert report["trajectory_budget"] == 100
    assert report["seed_preflight"]["overlap"] == []
    assert report["simulations_started"] == 0
    assert report["engines_constructed"] == 0
    assert report["artifacts_written"] == 0


def test_selection_is_recomputed_instead_of_trusting_declared_ids():
    plan = frc._load_plan(PLAN)
    source, _ = frc._source_paths(plan)
    selected, details = frc.derive_selection(source, plan)
    assert selected == plan["selected_candidate_ids"]
    assert len(details) == 5
    assert (details["shm_new_diagnoses_msm@2019"] <= 0.1).all()
    assert (details["shm_new_diagnoses_msm@2024"] <= 0.1).all()


def test_changed_selection_is_refused():
    plan = frc._load_plan(PLAN)
    source, _ = frc._source_paths(plan)
    plan["selected_candidate_ids"] = plan["selected_candidate_ids"][:-1]
    with pytest.raises(ContractError, match="recomputed candidate selection"):
        frc.derive_selection(source, plan)


def test_seed_preflight_detects_recorded_collision(tmp_path):
    manifest_dir = tmp_path / "experiment"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(json.dumps({"seeds": [200400]}))
    with pytest.raises(ContractError, match="recorded collisions"):
        frc.seed_preflight([200400, 200401], outputs_root=tmp_path)


def test_source_hash_change_is_refused(tmp_path):
    plan = frc._load_plan(PLAN)
    plan["source_manifest_sha256"] = "0" * 64
    with pytest.raises(ContractError, match="source search manifest differs"):
        frc._source_paths(plan)
