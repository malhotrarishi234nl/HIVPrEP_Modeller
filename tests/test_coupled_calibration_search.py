"""Scientific-contract and proposal tests for the coupled calibration search.

Nothing in this module runs the production simulator.  The six paired
production-versus-harness trajectories are the declared smoke budget and are
executed once by ``run_coupled_calibration.py --smoke``; the test below verifies
that artifact rather than repeating those runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
import run_coupled_calibration as rcc  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402
from tools import audit_robust_nroy_seeds as seed_audit_tool  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import nroy_fixtures as nroy_fx  # noqa: E402


# ==========================================================================
# Helpers
# ==========================================================================

class SerialExecutor:
    """Deterministic in-process executor; ``wait`` sees already-resolved futures."""

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, payload):
        future: Future = Future()
        future.set_result(fn(payload))
        return future


def _surrogate_values(candidate_id: int, seed: int, n: int) -> np.ndarray:
    """Cheap deterministic stand-in for the simulator.  Depends only on id and seed."""
    rng = np.random.default_rng([int(candidate_id), int(seed)])
    return 100.0 + 50.0 * rng.random(n)


def surrogate_worker(payload: dict) -> dict:
    """A worker with the production return shape and no simulation."""
    seeds = [int(s) for s in payload["replicate_seeds"]]
    columns, years = payload["target_columns"], payload["target_years"]
    matrix = np.array([_surrogate_values(payload["candidate_id"], s, len(columns))
                       for s in seeds], dtype=float)
    per_seed = []
    for row, seed in zip(matrix, seeds):
        outputs: dict = {}
        for value, column, year in zip(row, columns, years):
            outputs.setdefault(column, {})[int(year)] = float(value)
        per_seed.append({"seed": seed, "annual_outputs": outputs, "observed_stocks": {},
                         "wall_seconds": 0.0, "engine_deterministic": False})
    return {"candidate_id": payload["candidate_id"], "batch": payload["batch"],
            "origin": payload["origin"], "error": "", "replicate_seeds": seeds,
            "target_matrix": matrix.tolist(), "means": matrix.mean(axis=0).tolist(),
            "sample_variances": matrix.var(axis=0, ddof=1).tolist(),
            "per_seed": per_seed, "wall_seconds": 0.0}


def stub_smoke_worker(payload: dict) -> dict:
    return {"pair_id": payload["pair_id"], "label": payload["label"], "seed": payload["seed"],
            "error": "", "trajectories": 2, "all_summary_columns_identical": True,
            "all_extracted_series_identical": True,
            "adult_and_childhood_final_states_identical": True,
            "observer_captured_years": [str(y) for y in ccs.STOCK_YEARS],
            "engine_deterministic": False, "msm_relative_diagnosis_path": True,
            "production_wall_seconds": 1.0, "harness_wall_seconds": 1.0,
            "observed_stocks": {}, "wall_seconds": 2.0}


SMALL_ARCHIVE = 32
OPTION_B_PLAN = (
    ROOT / "configs/coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json"
)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _real_seed_audit(root: Path, *, smoke: list[int]) -> tuple[Path, dict]:
    """Generate one FINAL audit through the repository's real 1.3 pipeline."""
    audit_root = root / "seed_audit_sources"
    audit_plan = nroy_fx.write_signed_plan(audit_root)
    bound_plan = rcc.nc.FrozenPlan.load(audit_plan, root=ROOT)
    candidate = _write_json(audit_root / "candidate_seed_blocks.json", {
        "schema": seed_audit_tool.CANDIDATE_SCHEMA,
        "discovery": list(bound_plan.seeds.discovery),
        "confirmation": list(bound_plan.seeds.confirmation),
        "smoke": smoke,
        "proposal_rng": bound_plan.seeds.proposal_rng,
    })
    source = _write_json(audit_root / "seed_scan_source.json", {"fixture": "no prior RNG use"})
    subprocess.run(["git", "init", "-q"], cwd=audit_root, check=True)
    subprocess.run(["git", "add", source.name], cwd=audit_root, check=True)
    (audit_root / "outputs").mkdir(exist_ok=True)

    external = []
    for kind in ("REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD"):
        review = _write_json(audit_root / f"external_{kind.lower()}.json", {
            "schema": seed_audit_tool.EXTERNAL_REVIEW_SCHEMA,
            "location_kind": kind,
            "review_status": "REVIEWED_NONE",
            "reviewer": "pytest-owner",
            "reviewed_utc": "2026-10-15T00:00:00Z",
            "searched_locations": [f"isolated-{kind.lower()}-fixture"],
            "searched_accounts_or_owners": ["pytest-owner"],
            "search_method": "Inspected the isolated no-engine fixture.",
            "search_started_utc": "2026-10-15T00:00:00Z",
            "search_completed_utc": "2026-10-15T00:00:00Z",
            "indexed_sources": [],
            "no_additional_source_basis": "The isolated fixture has no other source.",
        })
        external.append({
            "kind": kind,
            "status": "REVIEWED_NONE",
            "coverage_decision": "The isolated fixture has no external seed source.",
            "evidence": [{
                "path": review.name,
                "sha256": ccs.sha256_file(review),
                "description": "Typed external-location review.",
            }],
            "reviewer": "pytest-owner",
            "reviewed_utc": "2026-10-15T00:00:00Z",
        })
    scope = _write_json(audit_root / "scope.json", {
        "schema": seed_audit_tool.SCOPE_SCHEMA,
        "owner_scope": {
            "status": "APPROVED",
            "approved_by": "pytest-owner",
            "approved_utc": "2026-10-15T00:00:00Z",
            "scope_statement": "All isolated no-engine fixture sources are covered.",
        },
        "source_coverage": {"status": "COMPLETE"},
        "tracked_repository": {
            "pathspecs": [source.name],
            "text_extensions": [".json"],
            "explicit_exclusions": [],
        },
        "external_execution_locations": external,
        "outputs": {
            "root": "outputs",
            "json_basename_globs": ["*manifest*.json"],
            "json_lines_basename_globs": ["*results*.jsonl"],
            "text_extensions": [".md"],
            "max_scanned_file_bytes": 1024 * 1024,
        },
        "evidence_example_limit": 100,
        "parsed_seed_source_limit": 10,
        "limitations": [],
    })
    resolutions = _write_json(audit_root / "resolutions.json", {
        "schema": seed_audit_tool.RESOLUTION_SCHEMA,
        "status": "REVIEWED",
        "scope_manifest_sha256": ccs.sha256_file(scope),
        "exact_resolutions": [],
        "reviewed_rules": [],
        "excluded_output_dispositions": [],
        "excluded_output_index_review": None,
        "limitations": [],
    })
    artifact = seed_audit_tool.build_audit(
        audit_root,
        scope,
        candidate,
        plan_path=audit_plan,
        resolution_path=resolutions,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert artifact["passed"] is True, artifact["unresolved"]
    return _write_json(root / "seed_audit.json", artifact), {
        "discovery": list(bound_plan.seeds.discovery),
        "confirmation": list(bound_plan.seeds.confirmation),
        "proposal_rng": bound_plan.seeds.proposal_rng,
    }


def _runnable_plan(root: Path) -> Path:
    plan = rcc.strict_json_object(OPTION_B_PLAN, what="test Option B plan")
    plan["design"]["name"] = f"test_option_b_{root.name}"
    plan["seeds"]["smoke"] = list(range(4_000_000_200, 4_000_000_203))
    audit_path, audited = _real_seed_audit(root, smoke=plan["seeds"]["smoke"])
    plan["seeds"].update(audited)
    plan["inputs"]["seed_audit"] = str(audit_path)
    plan["inputs"]["seed_audit_status"] = "FINAL"
    plan["inputs"]["expected_sha256"]["seed_audit"] = ccs.sha256_file(audit_path)
    return _write_json(root / "runnable_plan.json", plan)


def _stage_authorization(root: Path, stage: str, plan_path: Path,
                         upstream: dict[str, str]) -> Path:
    decision = _write_json(root / f"decision_evidence_{stage}.json", {
        "decision": f"authorize {stage} for the exact bound inputs",
        "approved_by": "pytest-owner",
        "approved_utc": "2026-10-15T00:00:00Z",
    })
    return _write_json(root / f"authorize_{stage}.json", {
        "schema": rcc.STAGE_AUTHORIZATION_SCHEMA,
        "status": rcc.STAGE_AUTHORIZED,
        "stage": stage,
        "plan_sha256": ccs.sha256_file(plan_path),
        "upstream_sha256": upstream,
        "approver_id": "pytest-owner",
        "approved_utc": "2026-10-15T00:00:00Z",
        "decision_evidence_path": str(decision),
        "decision_evidence_sha256": ccs.sha256_file(decision),
    })


def _search_evidence(root: Path, plan_path: Path, prepared: Path,
                     smoke_dir: Path, *, workers: int = 1, n_batches: int = 1,
                     wall_time_cap: float = ccs.WALL_TIME_CAP_SECONDS,
                     seconds_per_trajectory: float = 1.0,
                     upper_bound_seconds_per_trajectory: float | None = None,
                     approved_cost_ceiling: float | None = None) -> tuple[Path, Path]:
    prepared_binding = rcc.artifact_binding(prepared, "prepared")
    smoke_binding = rcc.artifact_binding(
        smoke_dir, "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    upper_bound_seconds = (
        seconds_per_trajectory if upper_bound_seconds_per_trajectory is None
        else upper_bound_seconds_per_trajectory
    )
    benchmark = _write_json(root / "measured_benchmark.json", {
        "schema": rcc.MEASURED_BENCHMARK_SCHEMA,
        "status": "FINAL",
        "plan_sha256": ccs.sha256_file(plan_path),
        "prepared_manifest_sha256": prepared_binding["prepared_manifest"],
        "prepared_artifacts_sha256": prepared_binding["prepared_artifacts"],
        "smoke_manifest_sha256": smoke_binding["smoke_manifest"],
        "smoke_artifacts_sha256": smoke_binding["smoke_artifacts"],
        "machine_profile": "pytest-serial",
        "measured_utc": "2026-10-15T00:00:00Z",
        "sample_trajectories": 6,
        "seconds_per_trajectory": seconds_per_trajectory,
        "upper_bound_seconds_per_trajectory": upper_bound_seconds,
    })
    pricing_source = _write_json(root / "pricing_source.json", {
        "schema": rcc.PRICING_SOURCE_SCHEMA,
        "status": "FINAL",
        "machine_profile": "pytest-serial",
        "currency_or_credit_unit": "test-credit",
        "resource_unit": "worker",
        "billing_unit": "resource_hour",
        "seconds_per_billing_unit": 3600,
        "unit_price": 1.0,
        "source_reference": "pytest exact rate fixture",
        "effective_utc": "2026-10-15T00:00:00Z",
    })
    execution_profile = _write_json(root / "execution_profile.json", {
        "schema": rcc.EXECUTION_PROFILE_SCHEMA,
        "status": "FINAL",
        "profile_kind": "local",
        "machine_profile": "pytest-serial",
        "workers": workers,
        "generated_utc": "2026-10-15T00:00:00Z",
        "hostname": rcc.platform.node(),
        "operating_system": rcc.platform.system(),
        "architecture": rcc.platform.machine(),
        "worker_capacity": rcc.os.cpu_count(),
    })
    plan = rcc.RunnerPlan(rcc.strict_json_object(plan_path, what="runnable plan"))
    initial = rcc.strict_json_object(
        prepared / "initial_archive.json", what="initial archive"
    )["vectors"]
    trajectories = (len(initial) + n_batches * ccs.BATCH_SIZE) * len(plan.discovery_seeds)
    trajectories_per_worker = len(plan.discovery_seeds)
    expected_runtime = trajectories * seconds_per_trajectory / workers
    expected_resource_seconds = expected_runtime * workers
    expected_cost = expected_resource_seconds / 3600.0
    wall_cap_resource_seconds = wall_time_cap * workers
    wall_cap_cost = wall_cap_resource_seconds / 3600.0
    tail_resource_seconds = upper_bound_seconds * workers * trajectories_per_worker
    conservative_resource_seconds = wall_cap_resource_seconds + tail_resource_seconds
    conservative_cost = conservative_resource_seconds / 3600.0
    ceiling = (max(1.0, conservative_cost * 2.0) if approved_cost_ceiling is None
               else approved_cost_ceiling)
    pricing = _write_json(root / "pricing_approval.json", {
        "schema": rcc.PRICING_APPROVAL_SCHEMA,
        "status": "APPROVED",
        "plan_sha256": ccs.sha256_file(plan_path),
        "measured_benchmark_sha256": ccs.sha256_file(benchmark),
        "machine_profile": "pytest-serial",
        "worker_count": workers,
        "total_authorized_trajectories": trajectories,
        "wall_time_cap_seconds": wall_time_cap,
        "pricing_source_path": str(pricing_source),
        "pricing_source_sha256": ccs.sha256_file(pricing_source),
        "execution_profile_path": str(execution_profile),
        "execution_profile_sha256": ccs.sha256_file(execution_profile),
        "cost_formula": rcc.SEARCH_COST_FORMULA,
        "cost_inputs": {
            "currency_or_credit_unit": "test-credit",
            "resource_unit": "worker",
            "billing_unit": "resource_hour",
            "seconds_per_billing_unit": 3600,
            "unit_price": 1.0,
        },
        "expected_runtime_seconds": expected_runtime,
        "expected_resource_seconds": expected_resource_seconds,
        "expected_cost": expected_cost,
        "wall_cap_resource_seconds": wall_cap_resource_seconds,
        "wall_cap_cost": wall_cap_cost,
        "max_in_flight_workers": workers,
        "trajectories_per_in_flight_worker": trajectories_per_worker,
        "in_flight_tail_resource_seconds": tail_resource_seconds,
        "conservative_authorized_max_resource_seconds": conservative_resource_seconds,
        "conservative_authorized_max_cost": conservative_cost,
        "approved_cost_ceiling": ceiling,
        "approved_by": "pytest-owner",
        "approved_utc": "2026-10-15T00:00:00Z",
    })
    return benchmark, pricing


@pytest.fixture(scope="session")
def space() -> ccs.ParameterSpace:
    return ccs.ParameterSpace.from_calib_space(json.loads((ROOT / ccs.CALIB_SPACE_PATH).read_text()))


@pytest.fixture(scope="session")
def targets() -> list[ccs.TargetSpec]:
    return ccs.load_frozen_targets(ROOT / ccs.TARGET_REGISTER_PATH,
                                   json.loads((ROOT / ccs.WAVE_PLAN_PATH).read_text()))


def _stub_gate():
    """The real gate spawns pytest in a subprocess; the fast fixtures stub it."""
    saved = rcc.diagnosis_test_gate
    rcc.diagnosis_test_gate = lambda: {"ok": True, "stubbed_for_tests": True}
    return saved


def _prepare_and_smoke(root: Path) -> tuple[Path, Path, Path]:
    """An ENGINEERING preparation with a reduced initial archive, flagged as such."""
    prepared = root / "prepared"
    smoke_dir = root / "smoke"
    plan_path = _runnable_plan(root)
    prepare_auth = _stage_authorization(root, "prepare", plan_path, {})
    real_seed_preflight = rcc.seed_preflight
    rcc.seed_preflight = lambda *args, **kwargs: {  # repository outputs are out of fixture scope
        "reserved": {}, "overlap": [], "fixture_scope": True,
    }
    try:
        rcc.prepare(prepared, plan_path=plan_path, authorization_path=prepare_auth,
                    initial_size=SMALL_ARCHIVE)
    finally:
        rcc.seed_preflight = real_seed_preflight
    smoke_auth = _stage_authorization(
        root, "smoke", plan_path,
        rcc.artifact_binding(prepared, "prepared"),
    )
    rcc.smoke(prepared, smoke_dir, workers=1, worker=stub_smoke_worker,
              executor_factory=SerialExecutor, plan_path=plan_path,
              authorization_path=smoke_auth)
    return prepared, smoke_dir, plan_path


@pytest.fixture(scope="module")
def small_search(tmp_path_factory):
    """A complete miniature search driven by the surrogate worker."""
    saved = _stub_gate()
    try:
        root = tmp_path_factory.mktemp("coupled_small")
        prepared, smoke_dir, plan_path = _prepare_and_smoke(root)
        benchmark, pricing = _search_evidence(root, plan_path, prepared, smoke_dir)
        search_dir = root / "search"
        prepared_binding = rcc.artifact_binding(prepared, "prepared")
        smoke_binding = rcc.artifact_binding(
            smoke_dir, "smoke", extra_files=("SMOKE_RESULT.json",)
        )
        evidence = rcc.validate_search_evidence(
            benchmark, pricing, plan_sha256=ccs.sha256_file(plan_path),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )
        search_auth = _stage_authorization(
            root, "search", plan_path, {**prepared_binding, **smoke_binding, **evidence}
        )
        result = rcc.run_search(prepared, smoke_dir, search_dir, workers=1, n_batches=1,
                                worker=surrogate_worker, executor_factory=SerialExecutor,
                                plan_path=plan_path, authorization_path=search_auth,
                                measured_benchmark_path=benchmark,
                                pricing_approval_path=pricing)
        yield {"root": root, "prepared": prepared, "smoke": smoke_dir,
               "search": search_dir, "result": result, "plan": plan_path,
               "benchmark": benchmark, "pricing": pricing}
    finally:
        rcc.diagnosis_test_gate = saved


# ==========================================================================
# 1. Defaults, preparation, directories, targets, hashes, seeds, validity
# ==========================================================================

def test_default_mode_constructs_no_engine_and_writes_nothing(tmp_path, monkeypatch):
    import run_v3  # noqa: PLC0415

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct a simulation engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    before = sorted(p.name for p in (ROOT / "outputs/calibration").iterdir())
    report = rcc.validate(
        plan_path=ROOT / "configs/coupled_search_plan_unordered_orientation_activity_diagnostics_20260921.json"
    )
    assert report["ok"], {k: v for k, v in report["checks"].items() if not v["ok"]}
    assert report["simulations_started"] == 0
    assert report["engines_constructed"] == 0
    assert report["artifacts_written"] == 0
    assert sorted(p.name for p in (ROOT / "outputs/calibration").iterdir()) == before


def test_plan_file_matches_module_constants():
    plan = json.loads(
        (ROOT / "configs/coupled_search_plan_unordered_orientation_activity_diagnostics_20260921.json").read_text()
    )
    assert rcc.assert_plan_matches(plan)["plan_sections_verified"]
    broken = json.loads(json.dumps(plan))
    broken["acceptance"]["cutoff"] = 4.0
    with pytest.raises(ContractError):
        rcc.assert_plan_matches(broken)


def test_plan_contract_allows_suite_identity_inputs_and_fresh_seed_values():
    plan = rcc.plan_payload()
    plan["design"]["name"] = "suite_case_example"
    plan["design"]["question"] = "A declared structural sensitivity."
    plan["inputs"]["base_config"] = "outputs/suites/case/model_config.json"
    plan["inputs"]["expected_sha256"]["base_config"] = "a" * 64
    plan["seeds"]["discovery"] = list(range(300000, 300005))
    plan["seeds"]["confirmation"] = list(range(300100, 300120))
    plan["seeds"]["smoke"] = [300200, 300201, 300202]
    plan["seeds"]["proposal_rng"] = 300300

    report = rcc.assert_plan_matches(plan)

    assert report["design"] == "suite_case_example"
    assert "inputs" in report["overridable_fields"]


def test_plan_contract_rejects_seed_count_and_method_changes():
    wrong_count = rcc.plan_payload()
    wrong_count["seeds"]["discovery"] = [300000]
    with pytest.raises(ContractError, match="must contain 5"):
        rcc.assert_plan_matches(wrong_count)

    changed_method = rcc.plan_payload()
    changed_method["search"]["batch_size"] = 32
    with pytest.raises(ContractError, match="frozen methodological"):
        rcc.assert_plan_matches(changed_method)


def test_plan_contract_accepts_explicit_crn_comparison_group():
    plan = rcc.plan_payload()
    plan["seeds"].update({
        "comparison_group": "structural_sensitivity_v1",
        "paired_designs": [plan["design"]["name"], "alternative_design"],
        "reuse_policy": "common_random_numbers_across_declared_designs_only",
    })
    assert rcc.assert_plan_matches(plan)["design"] == plan["design"]["name"]

    broken = json.loads(json.dumps(plan))
    broken["seeds"]["reuse_policy"] = "reuse_anywhere"
    with pytest.raises(ContractError, match="reuse_policy"):
        rcc.assert_plan_matches(broken)


def test_seed_preflight_allows_only_declared_crn_group_reuse(tmp_path):
    payload = rcc.plan_payload()
    payload["seeds"].update({
        "comparison_group": "structural_sensitivity_v1",
        "paired_designs": [payload["design"]["name"], "alternative_design"],
        "reuse_policy": "common_random_numbers_across_declared_designs_only",
    })
    plan = rcc.RunnerPlan(payload)
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "manifest.json").write_text(json.dumps({
        "design": "alternative_design",
        "seed_comparison_group": "structural_sensitivity_v1",
        "simulation_seed": plan.discovery_seeds[0],
    }))
    assert rcc.seed_preflight(tmp_path, plan)["overlap"] == []

    (prior / "manifest.json").write_text(json.dumps({
        "design": "unrelated_design", "seed_comparison_group": "another_group",
        "simulation_seed": plan.discovery_seeds[0],
    }))
    with pytest.raises(ContractError, match="collide"):
        rcc.seed_preflight(tmp_path, plan)


def test_authoritative_input_hashes_are_unchanged():
    for name, path in rcc.input_paths().items():
        assert ccs.sha256_file(path) == ccs.EXPECTED_INPUT_SHA256[name], name


def test_occupied_output_directory_is_refused(tmp_path):
    occupied = tmp_path / "busy"
    occupied.mkdir()
    (occupied / "existing.txt").write_text("previous result")
    with pytest.raises(ContractError) as excinfo:
        rcc.assert_unoccupied(occupied)
    assert "choose a new --output-dir prefix" in str(excinfo.value)
    with pytest.raises(ContractError, match="explicit --plan"):
        rcc.prepare(occupied)


@pytest.mark.parametrize("text", [
    '{"design": {}, "design": {}}',
    '{"design": NaN}',
    '{"design": 1e999}',
])
def test_authoritative_plan_json_refuses_ambiguous_or_nonfinite_numbers(tmp_path, text):
    path = tmp_path / "invalid_plan.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        rcc.validate(plan_path=path)


def test_validation_never_falls_back_to_a_default_plan(tmp_path, capsys):
    with pytest.raises(ContractError, match="explicit --plan"):
        rcc.validate()
    with pytest.raises(SystemExit, match="explicit --plan"):
        rcc.main(["--validate-only"])
    missing = tmp_path / "does_not_exist.json"
    assert rcc.main(["--validate-only", "--plan", str(missing)]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_historical_plan_remains_read_only_but_is_execution_ineligible():
    path = ROOT / "configs/coupled_search_plan_unordered_orientation_activity_diagnostics_20260921.json"
    payload = rcc.strict_json_object(path, what="historical plan")
    plan = rcc.RunnerPlan(payload)
    assert rcc.assert_plan_matches(payload)["design"] == payload["design"]["name"]
    assert plan.validate_execution_authorization()["status"] == \
        "legacy_plan_without_explicit_gate"
    with pytest.raises(ContractError, match="source_workbook_sha256"):
        rcc.validate_executable_input_bindings(plan)


@pytest.mark.parametrize("mutation", ["missing", "uppercase", "malformed"])
def test_current_plan_requires_exact_lowercase_workbook_sha(mutation):
    payload = rcc.strict_json_object(OPTION_B_PLAN, what="Option B plan")
    if mutation == "missing":
        payload["inputs"].pop("source_workbook_sha256")
    elif mutation == "uppercase":
        payload["inputs"]["source_workbook_sha256"] = \
            payload["inputs"]["source_workbook_sha256"].upper()
    else:
        payload["inputs"]["source_workbook_sha256"] = "0" * 63
    with pytest.raises(ContractError, match="source_workbook_sha256"):
        rcc.assert_plan_matches(payload)


@pytest.mark.parametrize("field,value", [
    ("proposal_rng", -1),
    ("proposal_rng", 2**32),
    ("proposal_rng", True),
    ("discovery", 2**32),
])
def test_plan_rng_seeds_use_the_uint32_contract_domain(tmp_path, field, value):
    payload = rcc.strict_json_object(_runnable_plan(tmp_path), what="runnable plan")
    if field == "discovery":
        payload["seeds"][field][0] = value
    else:
        payload["seeds"][field] = value
    with pytest.raises(ContractError, match="seed"):
        rcc.assert_plan_matches(payload)


def test_exact_final_seed_audit_is_required_and_plan_bound(tmp_path):
    option_b = rcc.RunnerPlan(rcc.strict_json_object(OPTION_B_PLAN, what="Option B plan"))
    with pytest.raises(ContractError, match="FINAL"):
        rcc.validate_seed_audit_binding(option_b)

    plan_path = _runnable_plan(tmp_path)
    payload = rcc.strict_json_object(plan_path, what="runnable plan")
    assert rcc.validate_seed_audit_binding(rcc.RunnerPlan(payload))["status"] == "FINAL"
    audit_path = Path(payload["inputs"]["seed_audit"])
    audit = rcc.strict_json_object(audit_path, what="seed audit")
    audit["candidate_blocks"]["discovery"][0] += 1
    audit.pop("artifact_sha256")
    audit["artifact_sha256"] = ccs.sha256_json(audit)
    _write_json(audit_path, audit)
    payload["inputs"]["expected_sha256"]["seed_audit"] = ccs.sha256_file(audit_path)
    with pytest.raises(ContractError, match="deterministic rebuild"):
        rcc.validate_seed_audit_binding(rcc.RunnerPlan(payload))


def test_seed_audit_refuses_invented_inventory_and_unbound_plan_hash(tmp_path):
    plan_path = _runnable_plan(tmp_path)
    payload = rcc.strict_json_object(plan_path, what="runnable plan")
    audit_path = Path(payload["inputs"]["seed_audit"])

    invented = rcc.strict_json_object(audit_path, what="seed audit")
    assert invented["source_inventory"]["scanned_files"] > 0
    invented["source_inventory"]["scanned_files"] = 0
    invented["source_inventory"]["inventory_sha256"] = "f" * 64
    invented.pop("artifact_sha256")
    invented["artifact_sha256"] = ccs.sha256_json(invented)
    _write_json(audit_path, invented)
    payload["inputs"]["expected_sha256"]["seed_audit"] = ccs.sha256_file(audit_path)
    with pytest.raises(ContractError, match="deterministic rebuild"):
        rcc.validate_seed_audit_binding(rcc.RunnerPlan(payload))

    # A syntactically valid digest is not plan provenance.
    fresh_path = _runnable_plan(tmp_path / "fresh")
    fresh = rcc.strict_json_object(fresh_path, what="fresh runnable plan")
    audit_path = Path(fresh["inputs"]["seed_audit"])
    unbound = rcc.strict_json_object(audit_path, what="seed audit")
    unbound["plan_sha256"] = "f" * 64
    unbound.pop("artifact_sha256")
    unbound["artifact_sha256"] = ccs.sha256_json(unbound)
    _write_json(audit_path, unbound)
    fresh["inputs"]["expected_sha256"]["seed_audit"] = ccs.sha256_file(audit_path)
    with pytest.raises(ContractError, match="exact bound plan"):
        rcc.validate_seed_audit_binding(rcc.RunnerPlan(fresh))


def test_malformed_manifest_cannot_hide_reused_smoke_seed(tmp_path):
    plan_path = _runnable_plan(tmp_path)
    plan = rcc.RunnerPlan(rcc.strict_json_object(plan_path, what="runnable plan"))
    hidden = tmp_path / "foreign" / "manifest.json"
    hidden.parent.mkdir()
    hidden.write_text(
        '{"simulation_seed": ' + str(plan.smoke_seeds[0]) + ',,}\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="malformed seed-scan manifest is blocking"):
        rcc.seed_preflight(tmp_path, plan)


@pytest.mark.parametrize("stage", ["prepare", "smoke", "search", "resume", "confirmation"])
def test_every_write_or_engine_stage_requires_its_own_authorization(stage):
    plan = rcc.RunnerPlan(rcc.strict_json_object(OPTION_B_PLAN, what="Option B plan"))
    with pytest.raises(ContractError, match=f"{stage} requires"):
        rcc.require_stage_authorization(
            None, stage=stage, plan=plan, plan_sha256=ccs.sha256_file(OPTION_B_PLAN),
            upstream_sha256={},
        )


def test_stage_authorization_is_exactly_stage_plan_and_upstream_bound(tmp_path):
    plan_path = _runnable_plan(tmp_path)
    plan = rcc.RunnerPlan(rcc.strict_json_object(plan_path, what="runnable plan"))
    upstream = {"prepared_manifest": "a" * 64}
    auth = _stage_authorization(tmp_path, "smoke", plan_path, upstream)
    assert rcc.require_stage_authorization(
        auth, stage="smoke", plan=plan, plan_sha256=ccs.sha256_file(plan_path),
        upstream_sha256=upstream,
    )["status"] == rcc.STAGE_AUTHORIZED
    with pytest.raises(ContractError, match="not 'search'"):
        rcc.require_stage_authorization(
            auth, stage="search", plan=plan, plan_sha256=ccs.sha256_file(plan_path),
            upstream_sha256=upstream,
        )
    with pytest.raises(ContractError, match="prerequisites differ"):
        rcc.require_stage_authorization(
            auth, stage="smoke", plan=plan,
            plan_sha256=ccs.sha256_file(plan_path),
            upstream_sha256={"prepared_manifest": "b" * 64},
        )
    with pytest.raises(ContractError, match="exact plan"):
        rcc.require_stage_authorization(
            auth, stage="smoke", plan=plan, plan_sha256="b" * 64,
            upstream_sha256=upstream,
        )

    reformatted = tmp_path / "same_content_different_bytes.json"
    reformatted.write_text(
        json.dumps(rcc.strict_json_object(plan_path, what="runnable plan")),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="file hash differs"):
        rcc.require_matching_plan_path(
            reformatted, plan, expected_file_sha256=ccs.sha256_file(plan_path)
        )


@pytest.mark.parametrize("field", [
    "approver_id", "approved_utc", "decision_evidence_path", "decision_evidence_sha256",
])
def test_stage_authorization_requires_owner_provenance(tmp_path, field):
    plan_path = _runnable_plan(tmp_path)
    path = _stage_authorization(tmp_path, "prepare", plan_path, {})
    payload = rcc.strict_json_object(path, what="stage authorization")
    payload.pop(field)
    _write_json(path, payload)
    plan = rcc.RunnerPlan(rcc.strict_json_object(plan_path, what="runnable plan"))
    with pytest.raises(ContractError, match="fields differ"):
        rcc.require_stage_authorization(
            path, stage="prepare", plan=plan, plan_sha256=ccs.sha256_file(plan_path),
            upstream_sha256={},
        )


def test_stage_authorization_requires_exact_bound_owner_evidence(tmp_path):
    plan_path = _runnable_plan(tmp_path)
    plan = rcc.RunnerPlan(rcc.strict_json_object(plan_path, what="runnable plan"))
    auth_path = _stage_authorization(tmp_path, "prepare", plan_path, {})
    auth = rcc.strict_json_object(auth_path, what="stage authorization")
    evidence_path = Path(auth["decision_evidence_path"])
    original = rcc.strict_json_object(evidence_path, what="decision evidence")

    evidence_path.unlink()
    with pytest.raises(ContractError, match="decision evidence is missing"):
        rcc.require_stage_authorization(
            auth_path, stage="prepare", plan=plan,
            plan_sha256=ccs.sha256_file(plan_path), upstream_sha256={},
        )

    # Semantically equivalent JSON has different bytes and is not the file the owner bound.
    evidence_path.write_text(json.dumps(original, sort_keys=True), encoding="utf-8")
    with pytest.raises(ContractError, match="hash is stale"):
        rcc.require_stage_authorization(
            auth_path, stage="prepare", plan=plan,
            plan_sha256=ccs.sha256_file(plan_path), upstream_sha256={},
        )


def test_reindexed_tampered_preparation_cannot_reuse_old_grant(small_search, tmp_path):
    import shutil  # noqa: PLC0415

    prepared = tmp_path / "tampered_prepared"
    shutil.copytree(small_search["prepared"], prepared)
    old_binding = rcc.artifact_binding(prepared, "prepared")
    grant = _stage_authorization(tmp_path, "smoke", small_search["plan"], old_binding)
    diagnostic = prepared / "effective_contact_products_anchor.csv"
    diagnostic.write_text(diagnostic.read_text(encoding="utf-8") + "# tampered\n",
                          encoding="utf-8")
    rcc.write_artifact_index(prepared)
    assert rcc.verify_artifact_index(prepared)["artifacts_sha256"] != \
        old_binding["prepared_artifacts"]
    with pytest.raises(ContractError, match="prerequisites differ"):
        rcc.smoke(
            prepared, tmp_path / "must_not_run", workers=1,
            worker=lambda payload: pytest.fail("authorization must fail before any worker"),
            executor_factory=SerialExecutor, plan_path=small_search["plan"],
            authorization_path=grant,
        )


def test_search_evidence_is_required_and_exactly_bound(small_search, tmp_path):
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(ContractError, match="measured benchmark and pricing"):
        rcc.validate_search_evidence(
            None, None, plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )
    pricing = rcc.strict_json_object(small_search["pricing"], what="pricing")
    pricing["measured_benchmark_sha256"] = "0" * 64
    changed = _write_json(tmp_path / "wrong_pricing.json", pricing)
    with pytest.raises(ContractError, match="does not bind the exact plan and benchmark"):
        rcc.validate_search_evidence(
            small_search["benchmark"], changed,
            plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


def test_search_execution_spec_binds_cli_workers_and_exact_derived_cost(small_search):
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    trajectories = (SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5
    with pytest.raises(ContractError, match="worker count differs"):
        rcc.validate_search_evidence(
            small_search["benchmark"], small_search["pricing"],
            plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=2, total_authorized_trajectories=trajectories,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


def test_search_execution_spec_refuses_extreme_runtime_and_tiny_ceiling(
        small_search, tmp_path):
    evidence_root = tmp_path / "extreme_execution_spec"
    evidence_root.mkdir()
    benchmark, pricing = _search_evidence(
        evidence_root, small_search["plan"], small_search["prepared"],
        small_search["smoke"], seconds_per_trajectory=1e100,
        approved_cost_ceiling=1e-100,
    )
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(
        ContractError, match="expected runtime exceeds|conservative authorized maximum cost exceeds"
    ):
        rcc.validate_search_evidence(
            benchmark, pricing, plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


def test_search_execution_spec_refuses_cost_above_ceiling(small_search, tmp_path):
    evidence_root = tmp_path / "underpriced_execution_spec"
    evidence_root.mkdir()
    benchmark, pricing = _search_evidence(
        evidence_root, small_search["plan"], small_search["prepared"],
        small_search["smoke"], approved_cost_ceiling=1e-100,
    )
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(ContractError, match="conservative authorized maximum cost exceeds"):
        rcc.validate_search_evidence(
            benchmark, pricing, plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


def test_search_ceiling_covers_wall_cap_plus_1000x_in_flight_tail(
        small_search, tmp_path):
    evidence_root = tmp_path / "bounded_tail_execution_spec"
    evidence_root.mkdir()
    benchmark, pricing = _search_evidence(
        evidence_root, small_search["plan"], small_search["prepared"],
        small_search["smoke"], wall_time_cap=1.0,
        seconds_per_trajectory=0.001,
        upper_bound_seconds_per_trajectory=1.0,
        approved_cost_ceiling=1.0 / 3600.0,
    )
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(ContractError, match="conservative authorized maximum cost exceeds"):
        rcc.validate_search_evidence(
            benchmark, pricing, plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=1.0,
        )


def test_local_execution_profile_must_match_live_machine(small_search, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    pricing = rcc.strict_json_object(small_search["pricing"], what="pricing")
    profile = rcc.strict_json_object(
        Path(pricing["execution_profile_path"]), what="execution profile"
    )
    profile["hostname"] = profile["hostname"] + "-different"
    profile_path = _write_json(tmp_path / "wrong_local_profile.json", profile)
    pricing["execution_profile_path"] = str(profile_path)
    pricing["execution_profile_sha256"] = ccs.sha256_file(profile_path)
    pricing_path = _write_json(tmp_path / "wrong_local_pricing.json", pricing)
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(ContractError, match="does not match the live machine"):
        rcc.validate_search_evidence(
            small_search["benchmark"], pricing_path,
            plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


def test_slurm_execution_profile_must_match_live_job(small_search, tmp_path, monkeypatch):
    pricing = rcc.strict_json_object(small_search["pricing"], what="pricing")
    local = rcc.strict_json_object(
        Path(pricing["execution_profile_path"]), what="execution profile"
    )
    profile = {
        "schema": rcc.EXECUTION_PROFILE_SCHEMA,
        "status": "FINAL",
        "profile_kind": "slurm",
        "machine_profile": local["machine_profile"],
        "workers": 1,
        "generated_utc": "2026-10-15T00:00:00Z",
        "cluster": "pytest-cluster",
        "partition": "cpu",
        "cpus_per_task": 1,
    }
    profile_path = _write_json(tmp_path / "slurm_profile.json", profile)
    pricing["execution_profile_path"] = str(profile_path)
    pricing["execution_profile_sha256"] = ccs.sha256_file(profile_path)
    pricing_path = _write_json(tmp_path / "slurm_pricing.json", pricing)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("SLURM_CLUSTER_NAME", "pytest-cluster")
    monkeypatch.setenv("SLURM_JOB_PARTITION", "different-partition")
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "1")
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    with pytest.raises(ContractError, match="does not match the live job"):
        rcc.validate_search_evidence(
            small_search["benchmark"], pricing_path,
            plan_sha256=ccs.sha256_file(small_search["plan"]),
            prepared_binding=prepared_binding, smoke_binding=smoke_binding,
            workers=1,
            total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
            trajectories_per_in_flight_worker=5,
            wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
        )


@pytest.mark.parametrize("text", [
    '{"schema": "a", "schema": "b"}',
    '{"schema": NaN}',
    '{"schema": 1e999}',
])
def test_authorization_json_is_strict(tmp_path, text):
    path = tmp_path / "authorization.json"
    path.write_text(text, encoding="utf-8")
    plan = rcc.RunnerPlan(rcc.strict_json_object(OPTION_B_PLAN, what="Option B plan"))
    with pytest.raises(ContractError):
        rcc.require_stage_authorization(
            path, stage="prepare", plan=plan, plan_sha256=ccs.sha256_file(OPTION_B_PLAN),
            upstream_sha256={},
        )


@pytest.mark.parametrize("text", [
    '{"status": "PREPARED", "status": "PREPARED"}',
    '{"status": NaN}',
    '{"status": 1e999}',
])
def test_stage_artifact_json_is_strict(tmp_path, text):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "manifest.json").write_text(text, encoding="utf-8")
    with pytest.raises(ContractError):
        rcc.load_prepared(prepared)


def test_frozen_targets_count_order_and_positive_budgets(targets):
    assert len(targets) == ccs.N_TARGETS == 13
    assert [t.key for t in targets] == list(ccs.FROZEN_TARGET_ORDER)
    assert sum(len(v) for v in ccs.OBJECTIVE_GROUPS.values()) == 13
    for target in targets:
        assert np.isfinite(target.b_variance) and target.b_variance > 0.0
        assert target.v_code_status == "structural_zero"
        assert np.isclose(
            target.b_variance,
            target.v_obs_variance + target.v_code_variance + target.v_disc_variance
            + target.extra_variance)
    # The observation term is READ, not assumed: at least one target carries a non-zero V_obs.
    assert any(t.v_obs_variance > 0.0 for t in targets)


def test_unresolved_target_is_fatal():
    plan = json.loads((ROOT / ccs.WAVE_PLAN_PATH).read_text())
    plan["scored_years"][ccs.AIDS_SERIES] = [y for y in ccs.AIDS_YEARS if y != 2022]
    with pytest.raises(ContractError):
        ccs.load_frozen_targets(ROOT / ccs.TARGET_REGISTER_PATH, plan)


def test_block_partition_is_exhaustive_and_disjoint(space):
    names = [n for block in ccs.BLOCK_ORDER for n in ccs.BLOCKS[block]]
    assert len(names) == len(set(names)) == space.n == 33
    assert set(names) == set(space.names)
    assert [len(ccs.BLOCKS[b]) for b in ccs.BLOCK_ORDER] == [7, 9, 7, 10]
    assert len(ccs.BLOCK_PAIRS) == 6


def test_orientation_reference_is_normalization_not_ordering(space):
    bounds = {name: (space.lower[i], space.upper[i]) for i, name in enumerate(space.names)}
    assert bounds["orient_female"] == pytest.approx((0.2, 5.0))
    assert bounds["orient_msm"] == pytest.approx((0.2, 5.0))

    state = rcc.load_inputs()
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    check = rcc.injection_checker(state["base_config"], space_json)
    params = state["warm_frame"].iloc[0][list(space.names)].astype(float).to_dict()
    params.update(orient_female=1.4, orient_msm=0.7)
    vector = np.array([params[name] for name in space.names], dtype=float)

    assert ccs.invalid_reasons(space, vector, injection_check=check) == []
    injected = rcc.injected_config(state["base_config"], params, space_json)
    multipliers = injected["transmission_model"]["groups"]["advanced"][
        "orientation_contact_multipliers"
    ]["value"]
    assert multipliers == pytest.approx(
        {"Female": 1.4, "Heterosexual_Male": 1.0, "MSM": 0.7}
    )


def test_stock_summary_preserves_activity_axis():
    class Groups:
        orientations = ["Female", "Heterosexual_Male", "MSM"]
        activities = ["Low", "Medium", "High"]
        hiv_stages = ["Susceptible", "Acute", "Chronic", "AIDS"]
        diagnosis_states = ["Undiagnosed", "Diagnosed"]
        art_states = ["Off_ART", "On_ART"]

    population = np.arange(2 * 3 * 3 * 4 * 2 * 2 * 2, dtype=float).reshape(
        2, 3, 3, 4, 2, 2, 2
    )
    summary = rcc.stock_summary(population, Groups())
    aggregate = np.asarray(summary["orientation_hiv_diagnosis_art"])
    resolved = np.asarray(summary["orientation_activity_hiv_diagnosis_art"])
    denominators = np.asarray(summary["denominators"]["adult_by_orientation_activity"])

    assert aggregate.shape == (3, 4, 2, 2)
    assert resolved.shape == (3, 3, 4, 2, 2)
    assert denominators.shape == (3, 3)
    assert aggregate.sum() == pytest.approx(population.sum())
    assert resolved.sum() == pytest.approx(population.sum())
    assert denominators.sum() == pytest.approx(population.sum())


def test_seed_preflight_passes_and_a_collision_stops_the_run(tmp_path):
    report = rcc.seed_preflight()
    assert report["overlap"] == []
    assert set(report["reserved"]["discovery"]).isdisjoint(report["reserved"]["confirmation"])

    # A FOREIGN manifest that records one of the reserved seeds stops the run.
    foreign = tmp_path / "some_other_experiment"
    foreign.mkdir()
    (foreign / "manifest.json").write_text(json.dumps({"simulation_seed": ccs.DISCOVERY_SEEDS[0]}))
    with pytest.raises(ContractError) as excinfo:
        rcc.seed_preflight(tmp_path)
    assert "never silently chooses another range" in str(excinfo.value)

    # This design's OWN manifests are skipped: they are not a foreign collision.
    (foreign / "manifest.json").write_text(json.dumps(
        {"design": ccs.DESIGN_NAME, "proposal_rng": {"seed": ccs.PROPOSAL_RNG_SEED}}))
    own = rcc.seed_preflight(tmp_path)
    assert own["overlap"] == [] and own["n_own_manifests_skipped"] == 1


def test_invalid_vectors_and_derived_simplex_constraints(space):
    base = space.denormalise(np.full(space.n, 0.5))
    index = {name: i for i, name in enumerate(space.names)}
    assert ccs.is_valid(space, base) or ccs.invalid_reasons(space, base)

    def mutate(**changes):
        vector = base.copy()
        for name, value in changes.items():
            vector[index[name]] = value
        return vector

    # Derived MSM acute share must stay non-negative.  The declared box cannot express a
    # violation (chronic <= 0.5, aids <= 0.08), so the derived rule is exercised directly on a
    # vector outside the box: the derived column is computed, and the mask rejects it.
    simplex_breaker = mutate(seed_msm_chronic_share=0.95, seed_msm_aids_share=0.2)
    frame = ccs.derived_frame(space, simplex_breaker)
    assert frame["seed_msm_acute_share"].iloc[0] < 0.0
    assert not bool(ccs.constraint_mask(space, simplex_breaker)[0])
    # Production injection independently refuses it, so the partial mask is never relied on alone.
    state = rcc.load_inputs()
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    check = rcc.injection_checker(state["base_config"], space_json)
    with pytest.raises(ValueError):
        check(space.as_dict(simplex_breaker))
    assert any("production injection rejected" in r or "bound" in r
               for r in ccs.invalid_reasons(space, simplex_breaker, injection_check=check))
    # Declared ordering constraint: MSM AIDS seed share must not exceed non-MSM.
    reasons = ccs.invalid_reasons(space, mutate(seed_msm_aids_share=0.08,
                                                seed_non_msm_aids_share=0.0))
    assert any("draw constraints" in r for r in reasons)
    # Diagnosis schedule ordering.
    assert any("draw constraints" in r for r in
               ccs.invalid_reasons(space, mutate(diagnosis_1985=0.18, diagnosis_1990=0.08)))
    # ART initiation ordering.
    assert any("draw constraints" in r for r in
               ccs.invalid_reasons(space, mutate(art_init_1996=0.5, art_init_2000=0.3)))
    # Out of bounds and non-finite are reported explicitly and never clipped.
    assert any("above upper bound" in r for r in ccs.invalid_reasons(space, mutate(p_msm=0.5)))
    assert any("non-finite" in r for r in ccs.invalid_reasons(space, mutate(p_msm=np.nan)))


def test_warm_vectors_are_rejected_not_clipped(space):
    state = rcc.load_inputs()
    broken = state["warm_frame"].copy()
    broken.loc[0, "p_msm"] = 99.0  # far outside the declared bound
    # In-bounds but ordering-violating: art_init_2015 must not exceed art_init_2020.
    broken.loc[1, "art_init_2015"] = 0.98
    broken.loc[1, "art_init_2020"] = 0.75
    generator = ccs.ProposalGenerator(space, 1234)
    entries, diagnostics = ccs.build_initial_vectors(
        space, state["source_frame"], broken, generator, size=30)
    reasons = {r.get("warm_start_row"): r["reason"] for r in diagnostics["rejections"]}
    assert "above upper bound" in reasons[0]
    assert "draw constraints" in reasons[1]
    assert all(abs(e["params"]["p_msm"] - 99.0) > 1.0 for e in entries)
    assert len(entries) == 30
    assert diagnostics["n_warm_accepted"] == 22  # 24 valid warm vectors, minus the two broken


def test_prepared_directory_rejects_changed_code(small_search, monkeypatch):
    prepared = small_search["prepared"]
    manifest = json.loads((prepared / "manifest.json").read_text())
    assert manifest["status"] == ccs.STATUS_PREPARED
    assert manifest["simulations_started"] == 0
    real = rcc.module_hashes()
    monkeypatch.setattr(rcc, "module_hashes", lambda: {**real, "run_v3.py": "0" * 64})
    with pytest.raises(ContractError) as excinfo:
        rcc.load_prepared(prepared)
    assert "code changed since preparation" in str(excinfo.value)


# ==========================================================================
# 2. Objective versus acceptance
# ==========================================================================

def test_more_variance_cannot_improve_the_search_rank(targets):
    means = np.array([t.z + 2.0 * np.sqrt(t.b_variance) for t in targets])
    worse = np.array([t.z + 4.0 * np.sqrt(t.b_variance) for t in targets])
    quiet = np.full(len(targets), 1.0)
    noisy = np.full(len(targets), 1e6)

    def record(mu, s2, cid):
        return ccs.build_record(candidate_id=cid, batch=0, origin="test", provenance={},
                                params={}, space=_null_space(), replicate_seeds=(0,), means=mu,
                                sample_variances=s2, targets=targets, replicates=5)

    good_quiet, good_noisy = record(means, quiet, 0), record(means, noisy, 1)
    bad_noisy = record(worse, noisy, 2)
    # The search statistic ignores the replicate variance entirely.
    assert good_quiet.j == good_noisy.j == pytest.approx(2.0)
    # A worse mean fit ranks worse even when its replicate variance explodes.
    assert bad_noisy.j == pytest.approx(4.0)
    assert bad_noisy.j > good_quiet.j
    assert ccs.ranking_key(bad_noisy) > ccs.ranking_key(good_quiet)
    # Acceptance, which does use the process variance, behaves differently -- and that is
    # exactly why the two are kept apart.
    assert not record(worse, quiet, 3).primary_pass
    assert bad_noisy.primary_pass


def test_excellent_groups_cannot_hide_a_failed_target(targets):
    means = np.array([t.z for t in targets], dtype=float)
    means[0] = targets[0].z + 12.0 * np.sqrt(targets[0].b_variance)
    variances = np.full(len(targets), 1e-6)
    residuals = ccs.search_residuals(means, targets)
    values = ccs.group_rms(residuals, targets)
    j, _ = ccs.objective(values)
    assert values["diagnoses_after_2015"] == pytest.approx(0.0)
    assert j > 0
    result = ccs.acceptance(means, variances, targets, replicates=5)
    assert not result.passed
    assert result.binding_target == targets[0].key
    assert result.failed_targets == (targets[0].key,)


def test_sensitivity_pass_is_never_a_promotion(targets):
    scale = 4.0  # primary I = 4 > 3; at the 0.2 discrepancy the denominator roughly doubles
    means = np.array([t.z + scale * np.sqrt(t.b_variance) for t in targets])
    variances = np.full(len(targets), 1e-9)
    primary = ccs.acceptance(means, variances, targets, replicates=5,
                             discrepancy=ccs.PRIMARY_DISCREPANCY)
    sensitivity = ccs.acceptance(means, variances, targets, replicates=5,
                                 discrepancy=ccs.SENSITIVITY_DISCREPANCY)
    assert not primary.passed and sensitivity.passed
    record = ccs.build_record(
        candidate_id=0, batch=0, origin="test", provenance={}, params={}, space=_null_space(),
        replicate_seeds=(1,), means=means, sample_variances=variances, targets=targets,
        replicates=5)
    assert record.sensitivity_pass and not record.primary_pass
    shortlist = ccs.freeze_shortlist([record])
    assert shortlist[0]["selected_as"] == "lowest_J_near_miss"


def _null_space() -> ccs.ParameterSpace:
    return ccs.ParameterSpace(names=(), lower=np.array([]), upper=np.array([]),
                              parameters=(), config_paths=())


def test_zero_or_nonfinite_budget_fails(targets):
    broken = list(targets)
    broken[0] = ccs.TargetSpec(**{**targets[0].as_row(), "b_variance": 0.0})
    with pytest.raises(ContractError):
        ccs.target_arrays(broken)
    broken[0] = ccs.TargetSpec(**{**targets[0].as_row(), "b_variance": float("inf")})
    with pytest.raises(ContractError):
        ccs.target_arrays(broken)
    means = np.array([t.z for t in targets], dtype=float)
    with pytest.raises(ContractError):
        ccs.acceptance(means, np.full(len(targets), np.nan), targets, replicates=5)
    with pytest.raises(ContractError):
        ccs.acceptance(means, np.full(len(targets), 1.0), targets, replicates=1)


def test_cutoff_boundary_convention_matches_the_repository(targets):
    """I_max exactly at the cutoff passes, as the repository's ``nroy_mask`` does with ``<=``."""
    import hm_implausibility as hm  # noqa: PLC0415

    keys = [t.key for t in targets]
    at_cutoff = np.full((1, len(targets)), ccs.IMPLAUSIBILITY_CUTOFF)
    assert hm.combine(at_cutoff, keys).nroy_mask(cutoff=ccs.IMPLAUSIBILITY_CUTOFF)[0]
    above = at_cutoff.copy()
    above[0, 0] = np.nextafter(ccs.IMPLAUSIBILITY_CUTOFF, 4.0)
    assert not hm.combine(above, keys).nroy_mask(cutoff=ccs.IMPLAUSIBILITY_CUTOFF)[0]
    means = np.array([t.z + 3.0 * np.sqrt(t.b_variance) for t in targets])
    result = ccs.acceptance(means, np.zeros(len(targets)), targets, replicates=5)
    assert result.i_max == pytest.approx(3.0, rel=1e-12)
    assert result.passed == bool(np.all(np.asarray(result.implausibility)
                                        <= ccs.IMPLAUSIBILITY_CUTOFF))


def test_harness_acceptance_agrees_with_repository_scoring(targets):
    rng = np.random.default_rng(20260918)
    means = {}
    variances = {}
    expected = {}
    for cid in range(6):
        mu = np.array([t.z * (1.0 + 0.05 * rng.standard_normal()) for t in targets])
        s2 = np.array([max(1.0, (0.02 * t.z) ** 2) for t in targets])
        means[cid], variances[cid] = mu, s2
        expected[cid] = ccs.acceptance(mu, s2, targets, replicates=5)
    table = ccs.repository_acceptance_check(
        means, variances, targets, register_path=ROOT / ccs.TARGET_REGISTER_PATH, replicates=5)
    for _, row in table.iterrows():
        cid = int(row["candidate_id"])
        assert row["repository_i_max"] == pytest.approx(expected[cid].i_max, rel=1e-12)
        assert bool(row["repository_primary_pass"]) == expected[cid].passed
        assert row["repository_binding_target"] == expected[cid].binding_target


# ==========================================================================
# 3. Proposal structure and determinism
# ==========================================================================

def _synthetic_archive(space, targets, n=80, seed=11):
    generator = ccs.ProposalGenerator(space, seed)
    archive = ccs.Archive(space)
    draws, _ = generator.global_draws(n, exclude=lambda u: False)
    for cid, u in enumerate(draws):
        rng = np.random.default_rng([seed, cid])
        means = np.array([t.z * (1.0 + 0.3 * rng.standard_normal()) for t in targets])
        archive.register_known(cid, u)
        archive.add(ccs.build_record(
            candidate_id=cid, batch=0, origin="initial_lhs", provenance={},
            params=space.as_dict(space.denormalise(u)), space=space, replicate_seeds=(0,),
            means=means, sample_variances=np.maximum(1.0, (0.02 * means) ** 2),
            targets=targets, replicates=5))
    return archive


def test_batch_quota_blocks_and_cross_block_structure(space, targets):
    archive = _synthetic_archive(space, targets, n=256)
    pool = ccs.select_parent_pool(archive.ordered())
    assert len(pool.ids) == min(ccs.POOL_SIZE, len(archive))
    assert len(set(pool.ids)) == len(pool.ids)
    assert {"best_by_J", "maximin_diversity"} <= set(pool.provenance.values())
    generator = ccs.ProposalGenerator(space, ccs.PROPOSAL_RNG_SEED)
    proposals, diagnostics = generator.propose_batch(1, pool, archive, first_candidate_id=1000)
    assert len(proposals) == ccs.BATCH_SIZE == 64
    assert diagnostics["counts"] == {"global_lhs": 16, "differential_full": 24,
                                     "differential_block": 24}
    by_id = {r.candidate_id: r for r in archive.records.values()}
    block_moves = [p for p in proposals if p.provenance["origin"] == "differential_block"]
    pairs_used = [tuple(p.provenance["blocks"]) for p in block_moves]
    assert set(pairs_used) == set(ccs.BLOCK_PAIRS)
    assert all(pairs_used.count(pair) == 4 for pair in ccs.BLOCK_PAIRS)
    seen_blocks = {b for pair in pairs_used for b in pair}
    assert seen_blocks == set(ccs.BLOCK_ORDER)
    for proposal in block_moves:
        if proposal.provenance["fallback"]:
            continue
        parent = by_id[proposal.provenance["parents"][0]].u
        moved = {block for block in ccs.BLOCK_ORDER
                 if np.any(np.abs(proposal.u[space.block_indices(block)]
                                  - parent[space.block_indices(block)]) > 0)}
        assert moved == set(proposal.provenance["blocks"])
    full_moves = [p for p in proposals if p.provenance["origin"] == "differential_full"]
    assert {p.provenance["F"] for p in full_moves} == set(ccs.F_VALUES)
    for proposal in proposals:
        assert np.all(proposal.u >= 0.0) and np.all(proposal.u <= 1.0)
        assert ccs.is_valid(space, space.denormalise(proposal.u))


def test_global_quota_samples_the_original_full_domain(space, targets):
    archive = _synthetic_archive(space, targets, n=40, seed=5)
    pool = ccs.select_parent_pool(archive.ordered())
    generator = ccs.ProposalGenerator(space, ccs.PROPOSAL_RNG_SEED)
    globals_seen = []
    for batch in range(1, 5):
        proposals, _ = generator.propose_batch(batch, pool, archive,
                                               first_candidate_id=1000 + 100 * batch)
        globals_seen.extend(p.u for p in proposals if p.provenance["origin"] == "global_lhs")
        for proposal in proposals:
            archive.register_known(proposal.candidate_id, proposal.u)
    matrix = np.vstack(globals_seen)
    assert matrix.shape[0] == 4 * ccs.GLOBAL_PER_BATCH
    parent_matrix = np.vstack([archive.records[cid].u for cid in pool.ids])
    outside = ((matrix < parent_matrix.min(axis=0)) | (matrix > parent_matrix.max(axis=0))).any(axis=1)
    assert outside.any(), "global draws must be able to leave the current parent box"


def test_proposals_are_independent_of_completion_order(space, targets):
    forward = _synthetic_archive(space, targets, n=40, seed=7)
    shuffled = ccs.Archive(space)
    records = list(forward.records.values())
    for record in sorted(records, key=lambda r: -r.candidate_id):  # reversed insertion order
        shuffled.add(record)
    for record in sorted(records, key=lambda r: -r.candidate_id):
        shuffled.register_known(record.candidate_id, record.u)
    pool_a = ccs.select_parent_pool(forward.ordered())
    pool_b = ccs.select_parent_pool(shuffled.ordered())
    assert pool_a.ids == pool_b.ids
    a, _ = ccs.ProposalGenerator(space, ccs.PROPOSAL_RNG_SEED).propose_batch(
        1, pool_a, forward, first_candidate_id=500)
    b, _ = ccs.ProposalGenerator(space, ccs.PROPOSAL_RNG_SEED).propose_batch(
        1, pool_b, shuffled, first_candidate_id=500)
    assert [p.candidate_id for p in a] == [p.candidate_id for p in b]
    for left, right in zip(a, b):
        np.testing.assert_array_equal(left.u, right.u)
        assert left.provenance == right.provenance


def test_duplicates_are_rejected_within_tolerance(space, targets):
    archive = _synthetic_archive(space, targets, n=20, seed=3)
    existing = archive.records[0].u
    assert archive.is_duplicate(existing)
    assert archive.is_duplicate(existing + ccs.DUPLICATE_TOLERANCE / 2)
    assert not archive.is_duplicate(existing + 1e-6)


def _failing_records(targets, n=40):
    """Records that cannot pass acceptance at any variance this test supplies."""
    records = []
    for cid in range(n):
        means = np.array([t.z + (5.0 + 0.1 * cid) * np.sqrt(t.b_variance) for t in targets])
        records.append(ccs.build_record(
            candidate_id=cid, batch=0, origin="test", provenance={}, params={},
            space=_null_space(), replicate_seeds=(0,), means=means,
            sample_variances=np.full(len(targets), 1e-9), targets=targets, replicates=5))
    return records


def test_shortlist_retains_near_misses_when_nothing_passes(targets):
    records = _failing_records(targets)
    assert not any(r.primary_pass for r in records)
    shortlist = ccs.freeze_shortlist(records)
    assert len(shortlist) == ccs.SHORTLIST_SIZE
    assert all(entry["selected_as"] == "lowest_J_near_miss" for entry in shortlist)
    ranked = sorted(records, key=ccs.ranking_key)
    assert [e["candidate_id"] for e in shortlist] == [r.candidate_id for r in ranked[:10]]


def test_ranking_ties_break_by_mean_then_candidate_id(space, targets):
    means = np.array([t.z + np.sqrt(t.b_variance) for t in targets])
    variances = np.full(len(targets), 1.0)
    records = [ccs.build_record(candidate_id=cid, batch=0, origin="t", provenance={}, params={},
                                space=_null_space(), replicate_seeds=(0,), means=means,
                                sample_variances=variances, targets=targets, replicates=5)
               for cid in (7, 2, 5)]
    assert [r.candidate_id for r in sorted(records, key=ccs.ranking_key)] == [2, 5, 7]


# ==========================================================================
# 4. Synthetic benchmarks
# ==========================================================================

COUPLED_COORDINATES = ("seed_prevalence", "diagnosis_1990", "orient_msm", "art_init_2000")
#: v1 = dA + dB, v2 = dB - dC, v3 = dC + dD, v4 = dD - dA.  Every single-coordinate
#: move helps one group and hurts another; the only joint solution is d = 0.
VALLEY_COMBINATIONS = ((0, 1.0, 1, 1.0), (1, 1.0, 2, -1.0), (2, 1.0, 3, 1.0), (3, 1.0, 0, -1.0))
VALLEY_KAPPA = 20.0
VALLEY_SEEDS = (197300, 197301, 197302, 197303, 197304)
VALLEY_SUCCESS_J = 1.0


def _valley_fixture(space):
    rng = np.random.default_rng(4242)
    index = [space.names.index(n) for n in COUPLED_COORDINATES]
    while True:
        raw = rng.random((512, space.n))
        mask = ccs.constraint_mask(space, space.denormalise(raw))
        for i in np.flatnonzero(mask):
            if all(0.3 <= raw[i][k] <= 0.7 for k in index):
                return raw[i], index


def _valley_combinations(u, u_star, index):
    d = np.asarray(u) - u_star
    return np.array([d[index[i]] * wi + d[index[j]] * wj
                     for i, wi, j, wj in VALLEY_COMBINATIONS])


def _valley_j(u, u_star, index):
    return float(VALLEY_KAPPA * np.max(np.abs(_valley_combinations(u, u_star, index))))


def _valley_means(u, u_star, index, targets):
    values = _valley_combinations(u, u_star, index)
    return np.array([t.z + VALLEY_KAPPA * values[ccs.GROUP_ORDER.index(t.group)]
                     * np.sqrt(t.b_variance) for t in targets])


def _run_valley_search(space, targets, seed, u_star, index, n_batches=8):
    generator = ccs.ProposalGenerator(space, seed)
    archive = ccs.Archive(space)
    initial, _ = generator.global_draws(ccs.INITIAL_ARCHIVE_SIZE, exclude=archive.is_duplicate)

    def add(u, cid, batch, origin):
        archive.register_known(cid, u)
        archive.add(ccs.build_record(
            candidate_id=cid, batch=batch, origin=origin, provenance={},
            params=space.as_dict(space.denormalise(u)), space=space, replicate_seeds=(0,),
            means=_valley_means(u, u_star, index, targets),
            sample_variances=np.ones(len(targets)), targets=targets, replicates=5))

    for cid, u in enumerate(initial):
        add(u, cid, 0, "initial_lhs")
    history = [min(r.j for r in archive.ordered())]
    for batch in range(1, n_batches + 1):
        pool = ccs.select_parent_pool(archive.ordered())
        proposals, _ = generator.propose_batch(
            batch, pool, archive,
            first_candidate_id=ccs.INITIAL_ARCHIVE_SIZE + (batch - 1) * ccs.BATCH_SIZE)
        for proposal in proposals:
            add(proposal.u, proposal.candidate_id, batch, proposal.origin)
        history.append(min(r.j for r in archive.ordered()))
    return {"best_j": history[-1], "history": history, "evaluations": len(archive)}


def _equal_budget_lhs(space, seed, u_star, index, budget):
    generator = ccs.ProposalGenerator(space, seed)
    draws, _ = generator.global_draws(budget, exclude=lambda u: False, raw_budget=400_000)
    return float(min(_valley_j(u, u_star, index) for u in draws))


def synthetic_benchmark() -> dict:
    """Predeclared synthetic comparison.  Reported, never used to claim HIV feasibility."""
    space = ccs.ParameterSpace.from_calib_space(
        json.loads((ROOT / ccs.CALIB_SPACE_PATH).read_text()))
    targets = ccs.load_frozen_targets(ROOT / ccs.TARGET_REGISTER_PATH,
                                      json.loads((ROOT / ccs.WAVE_PLAN_PATH).read_text()))
    u_star, index = _valley_fixture(space)
    budget = ccs.INITIAL_ARCHIVE_SIZE + ccs.N_BATCHES * ccs.BATCH_SIZE
    rows = []
    for seed in VALLEY_SEEDS:
        search = _run_valley_search(space, targets, seed, u_star, index)
        baseline = _equal_budget_lhs(space, seed + 500, u_star, index, budget)
        rows.append({"proposal_seed": seed, "search_best_j": search["best_j"],
                     "search_history": search["history"],
                     "search_evaluations": search["evaluations"],
                     "equal_budget_lhs_best_j": baseline,
                     "search_success": search["best_j"] <= VALLEY_SUCCESS_J,
                     "lhs_success": baseline <= VALLEY_SUCCESS_J})
    search_js = [r["search_best_j"] for r in rows]
    lhs_js = [r["equal_budget_lhs_best_j"] for r in rows]
    return {
        "fixture": "narrow compensating valley; v1=dA+dB, v2=dB-dC, v3=dC+dD, v4=dD-dA over "
                   f"{list(COUPLED_COORDINATES)} (one coordinate per parameter block); the only "
                   "joint solution is the interior point d=0",
        "kappa": VALLEY_KAPPA, "success_threshold_J": VALLEY_SUCCESS_J,
        "equal_budget_evaluations": budget, "predeclared_seeds": list(VALLEY_SEEDS),
        "rows": rows,
        "search_success_rate": float(np.mean([r["search_success"] for r in rows])),
        "lhs_success_rate": float(np.mean([r["lhs_success"] for r in rows])),
        "median_search_best_j": float(np.median(search_js)),
        "median_lhs_best_j": float(np.median(lhs_js)),
        "n_seeds_search_better": int(sum(s < l for s, l in zip(search_js, lhs_js))),
        "interpretation": "A synthetic optimisation comparison on a fixture with a KNOWN solution. "
                          "It says nothing about whether the HIV model can fit its targets.",
    }


def test_synthetic_compensating_valley_beats_equal_budget_lhs():
    report = synthetic_benchmark()
    # Every single-coordinate perturbation of the solution must hurt at least one group.
    space = ccs.ParameterSpace.from_calib_space(
        json.loads((ROOT / ccs.CALIB_SPACE_PATH).read_text()))
    u_star, index = _valley_fixture(space)
    assert _valley_j(u_star, u_star, index) == pytest.approx(0.0)
    for k in index:
        for step in (+0.05, -0.05):
            perturbed = u_star.copy()
            perturbed[k] += step
            assert _valley_j(perturbed, u_star, index) > 0.0
    assert report["n_seeds_search_better"] >= 3, report
    assert report["median_search_best_j"] < report["median_lhs_best_j"], report
    assert report["search_success_rate"] >= report["lhs_success_rate"], report


def test_disconnected_regions_are_reachable_from_a_warm_start(space, targets):
    """A far basin outside the warm-start coordinate range must stay reachable."""
    far_index = space.names.index("orient_msm")
    barrier = 0.70

    def basin_j(u):
        """Two basins separated by a discontinuity: the better one is past the barrier."""
        t = float(u[far_index])
        if t <= barrier:
            return 3.0 + 2.0 * abs(t - 0.05)
        return 0.5 + 2.0 * abs(t - 0.95)

    warm = pd.read_csv(ROOT / ccs.WARM_START_PATH)[list(space.names)]
    warm_u = np.array([space.normalise(warm.iloc[i].to_numpy(float)) for i in range(len(warm))])
    assert warm_u[:, far_index].max() < barrier, "fixture assumes every warm vector is near-basin"

    archive = ccs.Archive(space)
    generator = ccs.ProposalGenerator(space, ccs.PROPOSAL_RNG_SEED)

    def add(u, cid, batch, origin):
        value = basin_j(u)
        means = np.array([t.z + value * np.sqrt(t.b_variance) for t in targets])
        archive.register_known(cid, u)
        archive.add(ccs.build_record(
            candidate_id=cid, batch=batch, origin=origin, provenance={},
            params=space.as_dict(space.denormalise(u)), space=space, replicate_seeds=(0,),
            means=means, sample_variances=np.ones(len(targets)), targets=targets, replicates=5))

    for cid, u in enumerate(warm_u):
        add(u, cid, 0, "warm_start_csv")
    for batch in range(1, 4):
        pool = ccs.select_parent_pool(archive.ordered())
        proposals, _ = generator.propose_batch(batch, pool, archive,
                                               first_candidate_id=1000 + 100 * batch)
        for proposal in proposals:
            add(proposal.u, proposal.candidate_id, batch, proposal.origin)
    coordinates = np.array([r.u[far_index] for r in archive.ordered()])
    assert coordinates.max() > barrier, "the search never left the warm-start coordinate range"
    assert (coordinates > barrier).sum() >= 3, "the far region was barely sampled"
    best = min(archive.ordered(), key=ccs.ranking_key)
    assert best.u[far_index] > barrier, "the far basin was reached but not retained"
    assert best.j < 3.0, "the retained best must come from the better basin"


# ==========================================================================
# 5. Recomputation from raw replicates
# ==========================================================================

def test_search_outputs_recompute_from_raw_replicates(small_search, targets):
    search = small_search["search"]
    manifest = json.loads((search / "manifest.json").read_text())
    assert manifest["status"] == ccs.STATUS_SEARCH_COMPLETE
    summary = pd.read_csv(search / "archive_summary.csv").set_index("candidate_id")
    implausibility = pd.read_csv(search / "per_target_implausibility.csv")
    residuals = pd.read_csv(search / "signed_search_residuals.csv")
    raw_files = sorted((search / "raw").glob("candidate_*.json"))
    assert len(raw_files) == len(summary) == manifest["n_candidates_evaluated"]
    keys = [t.key for t in targets]
    for path in raw_files:
        raw = json.loads(path.read_text())
        cid = raw["candidate_id"]
        matrix = np.array(raw["target_matrix"], dtype=float)
        assert matrix.shape == (ccs.DISCOVERY_REPLICATES, len(targets))
        assert raw["target_order"] == keys
        expected = rcc.strict_json_object(small_search["plan"], what="test plan")
        assert raw["replicate_seeds"] == expected["seeds"]["discovery"]
        np.testing.assert_allclose(matrix.mean(axis=0), raw["means"], rtol=0, atol=1e-12)
        np.testing.assert_allclose(matrix.var(axis=0, ddof=1), raw["sample_variances"],
                                   rtol=0, atol=1e-12)
        # ddof=1, not ddof=0: the two differ and the recorded value must be the unbiased one.
        assert not np.allclose(matrix.var(axis=0, ddof=0), raw["sample_variances"])
        primary = ccs.acceptance(raw["means"], raw["sample_variances"], targets,
                                 replicates=ccs.DISCOVERY_REPLICATES)
        sensitivity = ccs.acceptance(raw["means"], raw["sample_variances"], targets,
                                     replicates=ccs.DISCOVERY_REPLICATES,
                                     discrepancy=ccs.SENSITIVITY_DISCREPANCY)
        row = summary.loc[cid]
        assert row["discovery_primary_i_max"] == pytest.approx(primary.i_max, rel=1e-12)
        assert bool(row["discovery_primary_pass"]) == primary.passed
        assert bool(row["discovery_sensitivity_pass"]) == sensitivity.passed
        r = ccs.search_residuals(raw["means"], targets)
        values = ccs.group_rms(r, targets)
        j, mean_r = ccs.objective(values)
        assert row["search_j"] == pytest.approx(j, rel=1e-12)
        assert row["search_mean_group_rms"] == pytest.approx(mean_r, rel=1e-12)
        mine = residuals[residuals.candidate_id == cid].set_index("target_key")
        np.testing.assert_allclose([mine.loc[k, "signed_search_residual"] for k in keys], r,
                                   rtol=1e-12, atol=0)
        theirs = implausibility[implausibility.candidate_id == cid].set_index("target_key")
        np.testing.assert_allclose([theirs.loc[k, "primary_implausibility"] for k in keys],
                                   primary.implausibility, rtol=1e-12, atol=0)


def test_repository_crosscheck_artifact_agrees(small_search):
    crosscheck = pd.read_csv(small_search["search"] / "repository_acceptance_crosscheck.csv")
    assert len(crosscheck) > 0
    assert float(crosscheck["i_max_absolute_difference"].max()) < 1e-9
    assert (crosscheck["repository_primary_pass"] == crosscheck["harness_primary_pass"]).all()
    manifest = json.loads((small_search["search"] / "manifest.json").read_text())
    assert manifest["repository_acceptance_crosscheck"]["status"] == "agrees"


def test_a_failed_replicate_is_not_an_accepted_result(small_search, tmp_path, targets):
    """A simulator failure stops the stage with its evidence, never a substitution."""
    def failing_worker(payload):
        row = surrogate_worker(payload)
        return {**row, "error": "RuntimeError: simulator exploded"}

    prepared = rcc.load_prepared(small_search["prepared"])
    state = prepared["state"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    output_dir = tmp_path / "failing"
    output_dir.mkdir()
    budget = ccs.TrajectoryBudget(cap=100)
    proposal = dict(prepared["initial"][0])
    with pytest.raises(rcc.StageFailure) as excinfo:
        rcc.evaluate_candidates(
            [proposal], stage="initial", output_dir=output_dir,
            base_config=state["base_config"], space_json=space_json, targets=targets,
            seeds=ccs.DISCOVERY_SEEDS, budget=budget, workers=1, completed=set(),
            dispatched=set(), worker=failing_worker, executor_factory=SerialExecutor,
            clock=lambda: 0.0, deadline=None)
    assert "no other candidate is substituted" in str(excinfo.value)
    journal = rcc.read_journal(output_dir / "journal/results.jsonl")
    assert journal and journal[0]["error"].startswith("RuntimeError")
    assert rcc.read_journal(output_dir / "journal/dispatch.jsonl")  # evidence is preserved
    # A non-finite mean can never be scored as a pass.
    means = np.array([t.z for t in targets], dtype=float)
    means[3] = np.nan
    with pytest.raises(ContractError):
        ccs.acceptance(means, np.ones(len(targets)), targets, replicates=5)
    with pytest.raises(ContractError):
        ccs.search_residuals(means, targets)


def test_confirmation_seeds_are_separate_from_discovery():
    assert set(ccs.CONFIRMATION_SEEDS).isdisjoint(ccs.DISCOVERY_SEEDS)
    assert set(ccs.SMOKE_SEEDS).isdisjoint(ccs.DISCOVERY_SEEDS)
    assert set(ccs.SMOKE_SEEDS).isdisjoint(ccs.CONFIRMATION_SEEDS)
    assert ccs.PROPOSAL_RNG_SEED not in set(ccs.DISCOVERY_SEEDS) | set(ccs.CONFIRMATION_SEEDS)
    assert len(ccs.CONFIRMATION_SEEDS) == ccs.CONFIRMATION_REPLICATES == 20


def test_confirmation_requires_a_complete_search(small_search, tmp_path):
    partial = tmp_path / "partial"
    partial.mkdir()
    manifest = json.loads((small_search["search"] / "manifest.json").read_text())
    (partial / "manifest.json").write_text(json.dumps({**manifest,
                                                       "status": ccs.STATUS_PARTIAL_TIME_LIMIT}))
    with pytest.raises(ContractError) as excinfo:
        rcc.load_complete_search(partial)
    assert "Only a COMPLETE search may be confirmed" in str(excinfo.value)


def test_shortlist_is_frozen_by_a_complete_search(small_search):
    shortlist = json.loads((small_search["search"] / "shortlist.json").read_text())
    assert shortlist["size"] <= ccs.SHORTLIST_SIZE
    ids = [entry["candidate_id"] for entry in shortlist["entries"]]
    assert len(ids) == len(set(ids))
    summary = pd.read_csv(small_search["search"] / "archive_summary.csv").set_index("candidate_id")
    n_pass = int(summary["discovery_primary_pass"].sum())
    heads = [e for e in shortlist["entries"] if e["selected_as"] == "discovery_primary_pass"]
    assert len(heads) == min(n_pass, ccs.SHORTLIST_PRIMARY_PASS_HEAD)
    blocks = json.loads((small_search["prepared"] / "frozen_plan.json").read_text())["parameters"]
    declared = {name for block in blocks["blocks"].values() for name in block}
    for entry in shortlist["entries"]:
        assert set(entry["params"]) == declared and len(declared) == 33


# ==========================================================================
# 6. Crash and resume
# ==========================================================================

def test_journal_tolerates_only_a_physically_final_unterminated_fragment(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"candidate_id": 1}\n{"candidate_id":', encoding="utf-8")
    assert rcc.read_journal(path) == [{"candidate_id": 1}]

    path.write_text('{"candidate_id":\n{"candidate_id": 2}\n', encoding="utf-8")
    with pytest.raises(ContractError):
        rcc.read_journal(path)

    path.write_text('{"candidate_id": 1}\n{"candidate_id":}\n', encoding="utf-8")
    with pytest.raises(ContractError):
        rcc.read_journal(path)


@pytest.mark.parametrize("tail", [
    '{"candidate_id": 1, "candidate_id": 2}\n',
    '{"candidate_id": NaN}\n',
    '{"candidate_id": 1e999}\n',
])
def test_journal_refuses_ambiguous_or_nonfinite_records(tmp_path, tail):
    path = tmp_path / "journal.jsonl"
    path.write_text(tail, encoding="utf-8")
    with pytest.raises(ContractError):
        rcc.read_journal(path)


def test_resume_integrity_binding_changes_with_any_consumed_journal(tmp_path):
    (tmp_path / "journal").mkdir()
    _write_json(tmp_path / "search_context.json", {"fixture": True})
    (tmp_path / "journal" / "dispatch.jsonl").write_text(
        '{"candidate_id": 1}\n', encoding="utf-8"
    )
    before = rcc.resume_integrity_binding(tmp_path)
    (tmp_path / "journal" / "dispatch.jsonl").write_text(
        '{"candidate_id": 1}\n{"candidate_id": 2}\n', encoding="utf-8"
    )
    after = rcc.resume_integrity_binding(tmp_path)
    assert after["partial_search_journal_bundle"] != \
        before["partial_search_journal_bundle"]


def test_crash_and_resume_matches_an_uninterrupted_run(small_search, tmp_path, targets):
    prepared, smoke_dir = small_search["prepared"], small_search["smoke"]
    plan_path = small_search["plan"]
    reference = small_search["search"]
    crash_after = SMALL_ARCHIVE + 2  # crash inside batch 1, after its proposals were frozen

    class _Crash(BaseException):
        """Escapes the runner like a killed process would."""

    state = {"seen": 0}

    def crashing_worker(payload):
        if state["seen"] >= crash_after:
            raise _Crash("simulated process death")
        state["seen"] += 1
        return surrogate_worker(payload)

    interrupted = tmp_path / "interrupted"
    prepared_binding = rcc.artifact_binding(prepared, "prepared")
    smoke_binding = rcc.artifact_binding(
        smoke_dir, "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    evidence = rcc.validate_search_evidence(
        small_search["benchmark"], small_search["pricing"],
        plan_sha256=ccs.sha256_file(plan_path), prepared_binding=prepared_binding,
        smoke_binding=smoke_binding,
        workers=1,
        total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
        trajectories_per_in_flight_worker=5,
        wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
    )
    search_auth = _stage_authorization(
        tmp_path, "search", plan_path, {**prepared_binding, **smoke_binding, **evidence}
    )
    with pytest.raises(_Crash):
        rcc.run_search(prepared, smoke_dir, interrupted, workers=1, n_batches=1,
                       worker=crashing_worker, executor_factory=SerialExecutor,
                       plan_path=plan_path, authorization_path=search_auth,
                       measured_benchmark_path=small_search["benchmark"],
                       pricing_approval_path=small_search["pricing"])
    partial_dispatch = rcc.read_journal(interrupted / "journal/dispatch.jsonl")
    partial_results = rcc.read_journal(interrupted / "journal/results.jsonl")
    assert len(partial_dispatch) == crash_after + 1  # the lost launch was journaled first
    assert len(partial_results) == crash_after

    # A hard-killed run has no final index. The operator seals the preserved tree before
    # any owner can issue an exact resume grant.
    rcc.write_artifact_index(interrupted)
    resume_upstream = {
        **prepared_binding, **smoke_binding, **evidence,
        **rcc.artifact_binding(interrupted, "partial_search"),
        **rcc.resume_integrity_binding(interrupted),
    }
    resume_auth = _stage_authorization(tmp_path, "resume", plan_path, resume_upstream)
    with pytest.raises(ContractError, match="resume execution settings differ"):
        rcc.run_search(
            None, None, interrupted, workers=2, n_batches=1, resume=True,
            worker=lambda payload: pytest.fail("changed workers must fail before dispatch"),
            executor_factory=SerialExecutor, plan_path=plan_path,
            authorization_path=resume_auth,
            measured_benchmark_path=small_search["benchmark"],
            pricing_approval_path=small_search["pricing"],
        )
    resumed = rcc.run_search(
        None, None, interrupted, workers=1, n_batches=1, resume=True,
        worker=surrogate_worker, executor_factory=SerialExecutor,
        plan_path=plan_path, authorization_path=resume_auth,
        measured_benchmark_path=small_search["benchmark"],
        pricing_approval_path=small_search["pricing"],
    )
    assert resumed["status"] == ccs.STATUS_SEARCH_COMPLETE
    resume_records = rcc.read_journal(
        interrupted / "journal/resume_authorizations.jsonl"
    )
    assert len(resume_records) == 1
    assert resume_records[0]["stage"] == "resume"
    assert resume_records[0]["sha256"] == ccs.sha256_file(resume_auth)

    dispatch = rcc.read_journal(interrupted / "journal/dispatch.jsonl")
    ids = [record["candidate_id"] for record in dispatch]
    assert len(ids) == len(set(ids)), "a resumed run must never dispatch a candidate twice"
    checkpoint = json.loads((interrupted / "checkpoint.json").read_text())
    lost = checkpoint["lost_in_flight"]
    assert len(lost) == 1
    assert checkpoint["budget"]["launched"] == len(ids) * ccs.DISCOVERY_REPLICATES
    assert checkpoint["budget"]["launched"] > checkpoint["budget"]["finished"]
    assert checkpoint["budget"]["lost_in_flight"] == ccs.DISCOVERY_REPLICATES

    # The proposal history is identical to the uninterrupted run.
    def proposals(directory):
        return [{k: v for k, v in record.items() if k != "recorded_utc"}
                for record in rcc.read_journal(directory / "journal/proposals.jsonl")]

    assert proposals(interrupted) == proposals(reference)

    # Every candidate evaluated by both runs produced identical raw results.
    reference_raw = {p.name: json.loads(p.read_text())
                     for p in (reference / "raw").glob("candidate_*.json")}
    resumed_raw = {p.name: json.loads(p.read_text())
                   for p in (interrupted / "raw").glob("candidate_*.json")}
    assert set(resumed_raw) == set(reference_raw) - {f"candidate_{lost[0]}.json"}
    for name, record in resumed_raw.items():
        for field in ("means", "sample_variances", "target_matrix", "replicate_seeds"):
            assert record[field] == reference_raw[name][field], name

    # The budget is not reset by resuming: the lost launch stays spent.
    manifest = json.loads((interrupted / "manifest.json").read_text())
    assert manifest["budget"]["launched"] == manifest["budget"]["cap"]
    assert manifest["n_candidates_evaluated"] == len(reference_raw) - 1


def test_resume_refuses_a_completed_search(small_search):
    with pytest.raises(ContractError) as excinfo:
        rcc.run_search(None, None, small_search["search"], workers=1, n_batches=1, resume=True,
                       worker=surrogate_worker, executor_factory=SerialExecutor)
    assert "must not overwrite a completed artifact" in str(excinfo.value)


def test_wall_time_cap_marks_partial_and_writes_no_shortlist(small_search, tmp_path):
    prepared, smoke_dir = small_search["prepared"], small_search["smoke"]
    ticks = iter([0.0] + [1e9] * 10_000)  # the first check passes, every later one is past due
    evidence_root = tmp_path / "one_second_execution_spec"
    evidence_root.mkdir()
    benchmark, pricing = _search_evidence(
        evidence_root, small_search["plan"], prepared, smoke_dir,
        wall_time_cap=1.0, seconds_per_trajectory=0.001,
    )
    prepared_binding = rcc.artifact_binding(prepared, "prepared")
    smoke_binding = rcc.artifact_binding(
        smoke_dir, "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    evidence = rcc.validate_search_evidence(
        benchmark, pricing,
        plan_sha256=ccs.sha256_file(small_search["plan"]),
        prepared_binding=prepared_binding, smoke_binding=smoke_binding,
        workers=1,
        total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
        trajectories_per_in_flight_worker=5,
        wall_time_cap_seconds=1.0,
    )
    search_auth = _stage_authorization(
        tmp_path, "search", small_search["plan"],
        {**prepared_binding, **smoke_binding, **evidence},
    )
    result = rcc.run_search(prepared, smoke_dir, tmp_path / "timed", workers=1, n_batches=1,
                            worker=surrogate_worker, executor_factory=SerialExecutor,
                            clock=lambda: next(ticks), wall_time_cap=1.0,
                            plan_path=small_search["plan"], authorization_path=search_auth,
                            measured_benchmark_path=benchmark,
                            pricing_approval_path=pricing)
    assert result["status"] == ccs.STATUS_PARTIAL_TIME_LIMIT
    manifest = json.loads((tmp_path / "timed" / "manifest.json").read_text())
    assert "CANNOT be reported as completed negative evidence" in manifest["partial_run_warning"]
    assert not (tmp_path / "timed" / "shortlist.json").exists()
    assert not (tmp_path / "timed" / "SEARCH_REPORT.md").exists()


def test_search_does_not_stop_when_nothing_passes(small_search):
    manifest = json.loads((small_search["search"] / "manifest.json").read_text())
    summary = pd.read_csv(small_search["search"] / "archive_summary.csv")
    assert manifest["status"] == ccs.STATUS_SEARCH_COMPLETE
    assert manifest["n_discovery_primary_pass"] == 0, "fixture assumes the surrogate fits nothing"
    assert len(summary) == SMALL_ARCHIVE + ccs.BATCH_SIZE
    assert (small_search["search"] / "shortlist.json").exists()


# ==========================================================================
# 7. The six-run paired smoke budget
# ==========================================================================

def test_smoke_plan_is_three_pairs_of_two_trajectories(small_search):
    result = json.loads((small_search["smoke"] / "SMOKE_RESULT.json").read_text())
    assert result["pairs"] == 3
    assert result["trajectories"] == ccs.SMOKE_TRAJECTORIES == 6
    expected = rcc.strict_json_object(small_search["plan"], what="test plan")
    assert result["seeds"] == expected["seeds"]["smoke"]
    assert len(result["warm_vectors_exercised"]) >= 2
    prepared = json.loads((small_search["prepared"] / "initial_archive.json").read_text())
    origins = {v["candidate_id"]: v["origin"] for v in prepared["vectors"]}
    assert all(origins[cid] in ("warm_source_vector", "warm_start_csv")
               for cid in result["warm_vectors_exercised"])


SMOKE_ARTIFACT = ROOT / "outputs/calibration/coupled_20260918_smoke/SMOKE_RESULT.json"


@pytest.mark.skipif(not SMOKE_ARTIFACT.exists(),
                    reason="the production smoke artifact has not been produced yet")
def test_production_smoke_artifact_passed_with_the_observer_enabled():
    result = json.loads(SMOKE_ARTIFACT.read_text())
    assert result["status"] == ccs.STATUS_SMOKE_PASSED
    assert result["trajectories"] == ccs.SMOKE_TRAJECTORIES == 6
    assert result["pairs"] == 3 and not result["failures"]
    assert len(set(result["warm_vectors_exercised"])) >= 2
    for check in result["checks"]:
        assert check["all_summary_columns_identical"]
        assert check["all_extracted_series_identical"]
        assert check["adult_and_childhood_final_states_identical"]
        assert check["engine_deterministic"] is False
        assert check["msm_relative_diagnosis_path"] is True
        assert sorted(check["observer_captured_years"]) == sorted(str(y) for y in ccs.STOCK_YEARS)
    stocks = json.loads((SMOKE_ARTIFACT.parent / "observed_stocks.json").read_text())
    for pair in stocks.values():
        for year in map(str, ccs.STOCK_YEARS):
            summary = pair[year]
            array = np.array(summary["orientation_hiv_diagnosis_art"], dtype=float)
            assert array.shape == (len(summary["orientations"]), len(summary["hiv_stages"]),
                                   len(summary["diagnosis_states"]), len(summary["art_states"]))
            assert array.sum() == pytest.approx(summary["denominators"]["adult_total"])


def test_budget_table_arithmetic():
    table = {row["stage"]: row["maximum_full_trajectories"] for row in ccs.budget_table()}
    assert ccs.SMOKE_TRAJECTORIES == 6
    assert ccs.DISCOVERY_TRAJECTORIES == 2048 * 5 == 10240
    assert ccs.CONFIRMATION_TRAJECTORIES == 10 * 20 == 200
    assert table["total scientific/smoke maximum"] == 10446 == ccs.TOTAL_TRAJECTORY_BUDGET
    assert (ccs.INITIAL_ARCHIVE_SIZE + ccs.N_BATCHES * ccs.BATCH_SIZE
            == ccs.MAX_DISCOVERY_VECTORS == 2048)


def test_trajectory_budget_counts_launches_not_successes():
    budget = ccs.TrajectoryBudget(cap=10)
    budget.reserve(5, "initial")
    budget.reserve(5, "batch_01")
    assert budget.remaining == 0
    with pytest.raises(ContractError):
        budget.reserve(1, "batch_02")
    assert budget.as_dict()["by_stage"] == {"initial": 5, "batch_01": 5}


def test_explanatory_schedule_diagnostics_use_production_semantics():
    state = rcc.load_inputs()
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    params = state["space"].as_dict(
        state["source_frame"].loc[620, list(state["space"].names)].to_numpy(dtype=float))
    config = rcc.injected_config(state["base_config"], params, space_json)
    products = ccs.effective_contact_products(config)
    assert len(products) == 9  # three orientations x three activity classes, never collapsed
    assert all(row["time_varying"] is False for row in products)
    for row in products:
        assert row["effective_annual_contact_product"] == pytest.approx(
            row["orientation_multiplier"] * row["base_contact_rate"])
    table = ccs.msm_diagnosis_probability_table(config, range(1975, 2026))
    assert len(table) == 51
    assert table[0]["base_annual_diagnosis_probability"] == 0.0  # availability default_before
    for row in table:
        assert 0.0 <= row["clipped_msm_annual_probability"] <= 1.0
        assert 0.0 <= row["clipped_msm_step_probability"] <= 1.0
        assert row["msm_multiplier"] >= 0.0
    # Before the multiplier schedule's first anchor the NEUTRAL multiplier 1.0 applies.
    assert table[0]["msm_multiplier"] == pytest.approx(1.0)


# ==========================================================================
# 8. Confirmation
# ==========================================================================

def test_confirmation_runs_the_frozen_shortlist_on_fresh_seeds(small_search, tmp_path):
    saved = _stub_gate()
    try:
        output = tmp_path / "confirmation"
        confirmation_auth = _stage_authorization(
            tmp_path, "confirmation", small_search["plan"],
            rcc.artifact_binding(small_search["search"], "completed_search"),
        )
        manifest = rcc.confirm(small_search["search"], output, workers=1,
                               worker=surrogate_worker, executor_factory=SerialExecutor,
                               plan_path=small_search["plan"],
                               authorization_path=confirmation_auth)
    finally:
        rcc.diagnosis_test_gate = saved
    assert manifest["status"] == ccs.STATUS_CONFIRMATION_COMPLETE
    assert manifest["replicates"] == ccs.CONFIRMATION_REPLICATES == 20
    expected = rcc.strict_json_object(small_search["plan"], what="test plan")
    assert manifest["seeds"] == expected["seeds"]["confirmation"]
    assert manifest["budget"]["launched"] == manifest["n_candidates"] * 20
    assert manifest["n_candidates"] <= ccs.SHORTLIST_SIZE

    frozen = json.loads((output / "frozen_shortlist.json").read_text())
    source = json.loads((small_search["search"] / "shortlist.json").read_text())
    assert frozen == source, "the shortlist must be frozen before any fresh simulation"

    summary = pd.read_csv(output / "confirmation_summary.csv")
    assert len(summary) == manifest["n_candidates"]
    assert {"discovery_primary_pass_recorded", "confirmation_primary_pass",
            "confirmation_sensitivity_pass", "confirmation_j"} <= set(summary.columns)
    frozen_by_id = {int(entry["candidate_id"]): entry for entry in frozen["entries"]}
    for row in summary.itertuples(index=False):
        entry = frozen_by_id[int(row.candidate_id)]
        assert row.search_j == pytest.approx(entry["search_j"])
        assert row.discovery_primary_i_max == pytest.approx(entry["discovery_primary_i_max"])
        assert row.discovery_primary_pass == entry["discovery_primary_pass"]

    paired = pd.read_csv(output / "paired_discovery_confirmation.csv")
    assert len(paired) == manifest["n_candidates"] * ccs.N_TARGETS
    assert (paired["discovery_mean"] != paired["confirmation_mean"]).any(), "fresh seeds must differ"

    for path in (output / "raw").glob("candidate_*.json"):
        raw = json.loads(path.read_text())
        assert raw["replicate_seeds"] == expected["seeds"]["confirmation"]
        assert set(raw["replicate_seeds"]).isdisjoint(expected["seeds"]["discovery"])
        matrix = np.array(raw["target_matrix"], dtype=float)
        assert matrix.shape == (20, ccs.N_TARGETS)
        np.testing.assert_allclose(matrix.var(axis=0, ddof=1), raw["sample_variances"], atol=1e-12)

    report = (output / "DECISION_REPORT.md").read_text()
    assert "decision report" in report.lower()
    assert "a good numerical fit cannot validate its definition" in report
    assert manifest["cross_validation"]["status"] in ("scored", "unavailable")
    assert "n_variance_unstable" in manifest["variance_stability"]


def test_confirmation_state_tensors_are_labelled_not_restart_safe(tmp_path):
    trajectory = {"final_state": np.zeros((2, 3, 3, 4, 2, 2, 2)),
                  "childhood_state": np.zeros((15, 3))}
    name = rcc._save_states(tmp_path, candidate_id=7, seed=197100, trajectory=trajectory)
    assert name == "candidate_7_seed197100.npz"
    meta = json.loads((tmp_path / "candidate_7_seed197100.meta.json").read_text())
    assert meta["restart_safe"] is False
    assert "cannot restart a simulation" in meta["warning"]
    with np.load(tmp_path / name) as saved:
        assert set(saved) == {"adult_final_2025", "childhood_final_2025"}


# ==========================================================================
# 9. The independent reviewer
# ==========================================================================

REVIEWER = ROOT / "tools/review_coupled_calibration.py"


def _run_reviewer(source: Path, output: Path, confirmation: Path | None = None):
    import subprocess  # noqa: PLC0415

    command = [sys.executable, str(REVIEWER), "--source", str(source),
               "--output-dir", str(output)]
    if confirmation is not None:
        command += ["--confirmation", str(confirmation)]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def test_reviewer_verifies_a_complete_search(small_search, tmp_path):
    output = tmp_path / "review"
    result = _run_reviewer(small_search["search"], output)
    assert result.returncode == 0, result.stderr[-3000:]
    verification = json.loads((output / "verification.json").read_text())
    assert verification["status"] == "VERIFIED"
    summary = pd.read_csv(small_search["search"] / "archive_summary.csv")
    assert verification["search"]["n_candidates"] == len(summary)
    assert verification["budget"]["trajectories_launched"] == verification["budget"]["cap"]
    assert verification["shortlist"]["entries"] == [
        e["candidate_id"] for e in
        json.loads((small_search["search"] / "shortlist.json").read_text())["entries"]]
    assert "A low search J is not acceptance." in (output / "VERIFIED_RESULTS.md").read_text()


def test_reviewer_detects_a_tampered_score(small_search, tmp_path):
    import shutil  # noqa: PLC0415

    copied = tmp_path / "tampered"
    shutil.copytree(small_search["search"], copied)
    summary = pd.read_csv(copied / "archive_summary.csv")
    summary.loc[0, "discovery_primary_i_max"] = 0.001  # a number the raw replicates do not support
    summary.to_csv(copied / "archive_summary.csv", index=False)
    rcc.write_artifact_index(copied)  # re-index, so only the ARITHMETIC can expose the change
    result = _run_reviewer(copied, tmp_path / "review_tampered")
    assert result.returncode != 0
    assert "I_max" in result.stderr


def test_reviewer_detects_a_tampered_raw_record(small_search, tmp_path):
    import shutil  # noqa: PLC0415

    copied = tmp_path / "tampered_raw"
    shutil.copytree(small_search["search"], copied)
    path = sorted((copied / "raw").glob("candidate_*.json"))[0]
    raw = json.loads(path.read_text())
    raw["target_matrix"][0][0] *= 2.0
    path.write_text(json.dumps(raw))
    result = _run_reviewer(copied, tmp_path / "review_tampered_raw")
    assert result.returncode != 0
    assert any(marker in result.stderr for marker in ("sha256 mismatch", "size mismatch"))


@pytest.mark.parametrize("relative_path,text,marker", [
    ("shortlist.json", '{"entries": [], "entries": []}\n', "duplicate JSON object key"),
    ("shortlist.json", '{"entries": NaN}\n', "non-finite JSON token"),
    ("shortlist.json", '{"entries": 1e999}\n', "overflows to a non-finite value"),
    ("journal/dispatch.jsonl", '{"candidate_id": 1, "candidate_id": 2}\n',
     "duplicate JSON object key"),
])
def test_reviewer_strictly_rejects_ambiguous_json_and_jsonl(
        small_search, tmp_path, relative_path, text, marker):
    import shutil  # noqa: PLC0415

    copied = tmp_path / ("strict_" + relative_path.replace("/", "_"))
    shutil.copytree(small_search["search"], copied)
    (copied / relative_path).write_text(text, encoding="utf-8")
    rcc.write_artifact_index(copied)  # integrity is current; strict parsing must expose it
    result = _run_reviewer(copied, tmp_path / ("review_" + copied.name))
    assert result.returncode != 0
    assert marker in result.stderr


def test_a_simulator_failure_marks_the_search_failed(small_search, tmp_path):
    """The run stops with FAILED, keeps its evidence and writes no shortlist or report."""
    state = {"seen": 0}

    def failing_worker(payload):
        row = surrogate_worker(payload)
        state["seen"] += 1
        if state["seen"] > 5:
            return {**row, "error": "ValueError: non-finite target output in a replicate"}
        return row

    output = tmp_path / "failed"
    prepared_binding = rcc.artifact_binding(small_search["prepared"], "prepared")
    smoke_binding = rcc.artifact_binding(
        small_search["smoke"], "smoke", extra_files=("SMOKE_RESULT.json",)
    )
    evidence = rcc.validate_search_evidence(
        small_search["benchmark"], small_search["pricing"],
        plan_sha256=ccs.sha256_file(small_search["plan"]),
        prepared_binding=prepared_binding, smoke_binding=smoke_binding,
        workers=1,
        total_authorized_trajectories=(SMALL_ARCHIVE + ccs.BATCH_SIZE) * 5,
        trajectories_per_in_flight_worker=5,
        wall_time_cap_seconds=ccs.WALL_TIME_CAP_SECONDS,
    )
    search_auth = _stage_authorization(
        tmp_path, "search", small_search["plan"],
        {**prepared_binding, **smoke_binding, **evidence},
    )
    result = rcc.run_search(small_search["prepared"], small_search["smoke"], output, workers=1,
                            n_batches=1, worker=failing_worker, executor_factory=SerialExecutor,
                            plan_path=small_search["plan"], authorization_path=search_auth,
                            measured_benchmark_path=small_search["benchmark"],
                            pricing_approval_path=small_search["pricing"])
    assert result["status"] == ccs.STATUS_FAILED
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == ccs.STATUS_FAILED
    assert "no candidate was substituted" in manifest["failure_warning"]
    assert manifest["n_candidates_evaluated"] == 5
    assert not (output / "shortlist.json").exists()
    assert not (output / "SEARCH_REPORT.md").exists()
    assert len(list((output / "raw").glob("candidate_*.json"))) == 5  # evidence is preserved
    assert rcc.read_journal(output / "journal/results.jsonl")[-1]["error"].startswith("ValueError")
    with pytest.raises(ContractError):
        rcc.load_complete_search(output)


# ==========================================================================
# 10. Exact cache identity and injected-config provenance
# ==========================================================================

def test_vector_identity_is_exact_and_order_independent(space):
    base = space.as_dict(space.denormalise(np.full(space.n, 0.5)))
    shuffled = {name: base[name] for name in reversed(list(base))}
    assert ccs.vector_identity(base) == ccs.vector_identity(shuffled)
    nudged = dict(base)
    nudged["p_msm"] = np.nextafter(nudged["p_msm"], 1.0)
    assert ccs.vector_identity(nudged) != ccs.vector_identity(base)
    archive = ccs.Archive(space)
    u = space.normalise(base)
    archive.register_known(0, u, identity=ccs.vector_identity(base))
    archive.register_known(0, u, identity=ccs.vector_identity(base))  # same candidate: allowed
    with pytest.raises(ContractError):
        archive.register_known(1, u, identity=ccs.vector_identity(shuffled))


def test_raw_records_carry_vector_and_config_hashes(small_search):
    search = small_search["search"]
    identities = {}
    for path in sorted((search / "raw").glob("candidate_*.json")):
        raw = json.loads(path.read_text())
        assert len(raw["params_sha256"]) == 64
        assert raw["params_sha256"] == ccs.vector_identity(raw["params"])
        assert len(raw["injected_config_sha256"]) == 64
        identities.setdefault(raw["params_sha256"], []).append(raw["candidate_id"])
    assert all(len(v) == 1 for v in identities.values()), "no exact vector was evaluated twice"
    summary = pd.read_csv(search / "archive_summary.csv")
    assert summary["params_sha256"].is_unique
    for record in rcc.read_journal(search / "journal/dispatch.jsonl"):
        assert record["params_sha256"] in identities
