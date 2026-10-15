"""No-engine contracts for the bounded Option-B research E2E route."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
import run_coupled_calibration as rcc  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402


PLAN = ROOT / "configs/coupled_search_plan_option_b_1980_research_e2e_20261015.json"


def _payload() -> dict:
    return json.loads(PLAN.read_text(encoding="utf-8"))


def test_research_plan_preserves_science_and_is_permanently_non_production():
    payload = _payload()
    report = rcc.assert_plan_matches(payload)
    plan = rcc.RunnerPlan(payload)
    declaration = plan.validate_research_execution()

    assert report["design"] == "option_b_1980_research_e2e_20261015"
    assert declaration["run_class"] == rcc.RESEARCH_RUN_CLASS
    assert declaration["production_eligible"] is False
    assert declaration["allowed_stages"] == list(rcc.RESEARCH_ALLOWED_STAGES)
    assert payload["inputs"]["base_config"] == (
        "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
    )
    assert payload["inputs"]["calib_space"] == (
        "configs/calib_space_v13_25_unordered_orientation_20260921.json"
    )
    assert payload["inputs"]["target_register"] == (
        "configs/hm_target_register_robust_nroy_production_20261015.json"
    )
    assert payload["calibration_window"] == {
        "simulation_start_year": 1980,
        "extract_start_year": 1980,
        "extract_end_year": 2025,
    }
    assert "seed_audit" not in payload["inputs"]
    assert "seed_audit" not in payload["inputs"]["expected_sha256"]


def test_research_seed_blocks_are_uint32_mutually_disjoint_and_expected_sizes():
    seeds = _payload()["seeds"]
    blocks = [seeds["discovery"], seeds["confirmation"], seeds["smoke"]]
    assert [len(block) for block in blocks] == [5, 20, 3]
    flat = [value for block in blocks for value in block] + [seeds["proposal_rng"]]
    assert len(flat) == len(set(flat))
    assert all(0 <= value <= 2**32 - 1 for value in flat)
    assert not set(flat).intersection(rcc.DECLARED_FOREIGN_SEEDS)


def test_research_seed_preflight_blocks_malformed_manifests(tmp_path):
    manifest = tmp_path / "foreign" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"seed": 12,,}\n', encoding="utf-8")
    with pytest.raises(ContractError, match="malformed seed-scan manifest is blocking"):
        rcc.seed_preflight(tmp_path, rcc.RunnerPlan(_payload()))


def test_research_seed_preflight_describes_only_active_own_manifest_rules(tmp_path):
    report = rcc.seed_preflight(tmp_path, rcc.RunnerPlan(_payload()))
    assert "comparison group None" not in report["limitation"]
    assert "design='option_b_1980_research_e2e_20261015'" in report["limitation"]


def test_research_declaration_binds_stage_plan_and_upstream_without_auth_claim():
    plan = rcc.RunnerPlan(_payload())
    upstream = {"prepared_manifest": "a" * 64}
    declaration = rcc.research_stage_declaration(
        stage="smoke",
        plan=plan,
        plan_sha256=rcc.ccs.sha256_file(PLAN),
        upstream_sha256=upstream,
    )
    assert declaration["status"] == "RESEARCH_ONLY"
    assert declaration["production_eligible"] is False
    assert declaration["upstream_sha256"] == upstream
    assert "approver_id" not in declaration
    assert "approved_utc" not in declaration
    with pytest.raises(ContractError, match="must not receive production"):
        rcc.research_stage_declaration(
            stage="smoke",
            plan=plan,
            plan_sha256=rcc.ccs.sha256_file(PLAN),
            upstream_sha256=upstream,
            authorization_path=tmp_path_placeholder(),
        )


def tmp_path_placeholder() -> Path:
    """A non-null path is sufficient: research rejects, rather than reading it."""
    return Path("production-authorization-must-not-be-used.json")


def test_bounded_cli_controls_require_explicit_research_mode():
    args = rcc.parse_args([
        "--run-search", "--plan", str(PLAN), "--n-batches", "2",
        "--wall-time-cap-seconds", "3600",
    ])
    with pytest.raises(ContractError, match="exposed only with --research-e2e"):
        rcc.validate_cli_execution_profile(args)

    research = rcc.parse_args([
        "--run-search", "--research-e2e", "--plan", str(PLAN),
        "--n-batches", "2", "--wall-time-cap-seconds", "3600", "--workers", "8",
    ])
    rcc.validate_cli_execution_profile(research)
    assert research.n_batches == 2
    assert research.wall_time_cap_seconds == 3600


def test_research_profile_rejects_production_evidence_and_hpc_override():
    args = rcc.parse_args([
        "--run-search", "--research-e2e", "--plan", str(PLAN),
        "--authorization", "authorization.json",
    ])
    with pytest.raises(ContractError, match="must not receive production"):
        rcc.validate_cli_execution_profile(args)

    hpc = rcc.parse_args([
        "--run-search", "--research-e2e", "--plan", str(PLAN),
        "--allow-hpc-workers",
    ])
    with pytest.raises(ContractError, match="does not allow --allow-hpc-workers"):
        rcc.validate_cli_execution_profile(hpc)


def test_production_defaults_and_gates_remain_the_defaults():
    signature = inspect.signature(rcc.run_search)
    assert signature.parameters["n_batches"].default == ccs.N_BATCHES
    assert signature.parameters["wall_time_cap"].default == ccs.WALL_TIME_CAP_SECONDS
    assert signature.parameters["research_e2e"].default is False
    assert rcc.research_labels(False) == {}
    with pytest.raises(ContractError, match="requires an explicit stage authorization"):
        rcc.require_stage_authorization(
            None,
            stage="search",
            plan=rcc.RunnerPlan(_payload()),
            plan_sha256=rcc.ccs.sha256_file(PLAN),
            upstream_sha256={},
        )


def test_recommended_one_hour_profile_is_bounded_and_explicit():
    initial_size = 256
    n_batches = 2
    vectors = initial_size + n_batches * ccs.BATCH_SIZE
    trajectories = vectors * ccs.DISCOVERY_REPLICATES
    assert vectors == 384
    assert trajectories == 1920
    assert vectors < ccs.MAX_DISCOVERY_VECTORS
    assert trajectories < ccs.DISCOVERY_TRAJECTORIES
