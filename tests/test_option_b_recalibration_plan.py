"""No-engine contract tests for the unsigned Option B recalibration plan."""

from __future__ import annotations

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


PLAN_PATH = (
    ROOT / "configs/coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json"
)
EXPECTED_TARGET_ORDER = tuple(
    [f"shm_new_diagnoses_msm@{year}" for year in (1996, 2000, 2004, 2010, 2015, 2019, 2024)]
    + [f"rivm_aids_msm@{year}" for year in (2000, 2005, 2010, 2015, 2019, 2022)]
)


def _output_snapshot() -> list[tuple[str, int, int]]:
    root = ROOT / "outputs"
    return [
        (str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def test_option_b_validate_only_resolves_inputs_and_targets_without_engine_or_writes(monkeypatch):
    import run_v3  # noqa: PLC0415

    def forbidden(*args, **kwargs):
        raise AssertionError("Option B validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    before = _output_snapshot()

    report = rcc.validate(plan_path=PLAN_PATH)

    assert not report["ok"]
    assert report["checks"]["authoritative_inputs_and_source_integrity"]["ok"]
    targets = report["checks"]["frozen_targets_and_budgets"]
    assert targets["ok"]
    assert targets["detail"]["n_targets"] == 13
    assert tuple(targets["detail"]["order"]) == EXPECTED_TARGET_ORDER
    assert "unsigned" in report["checks"]["seed_preflight"]["detail"].lower()
    assert not report["checks"]["plan_execution_contract"]["ok"]
    assert not report["checks"]["execution_authorization"]["ok"]
    assert report["simulations_started"] == 0
    assert report["engines_constructed"] == 0
    assert report["artifacts_written"] == 0
    assert _output_snapshot() == before


def test_option_b_window_drives_injection_while_historical_default_stays_1975():
    payload = json.loads(PLAN_PATH.read_text())
    plan = rcc.RunnerPlan(payload)
    state = rcc.load_inputs(plan)
    params = state["space"].as_dict(
        state["source_frame"].loc[plan.source_vector_ids[0], list(state["space"].names)]
        .to_numpy(dtype=float)
    )
    space_json = json.loads(state["paths"]["calib_space"].read_text())

    option_b = rcc.injected_config(
        state["base_config"], params, space_json,
        simulation_start_year=plan.simulation_start_year,
    )
    historical_base = json.loads((ROOT / ccs.BASE_CONFIG_PATH).read_text())
    historical = rcc.injected_config(historical_base, params, space_json)

    assert plan.simulation_start_year == 1980
    assert plan.extract_start_year == 1980
    assert option_b["run_control"]["start_year"]["value"] == 1980
    assert historical["run_control"]["start_year"]["value"] == 1975


def test_option_b_initial_vector_provenance_is_plan_derived_and_carries_no_status():
    payload = json.loads(PLAN_PATH.read_text())
    plan = rcc.RunnerPlan(payload)
    state = rcc.load_inputs(plan)
    generator = ccs.ProposalGenerator(state["space"], 314159)
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    injection_check = rcc.injection_checker(
        state["base_config"], space_json,
        simulation_start_year=plan.simulation_start_year,
    )

    entries, diagnostics = ccs.build_initial_vectors(
        state["space"], state["source_frame"], state["warm_frame"], generator,
        size=24, injection_check=injection_check, source_vector_ids=plan.source_vector_ids,
        source_experiment=plan.inputs["source_experiment"],
        warm_start_path=plan.inputs["warm_start"],
        max_warm_vectors=plan.max_warm_vectors,
    )

    assert diagnostics["n_warm_accepted"] == 23
    for entry in entries:
        if entry["origin"] == "warm_source_vector":
            assert entry["provenance"]["source"] == plan.inputs["source_experiment"]
            assert entry["provenance"]["source_design_id"] in plan.source_vector_ids
            assert entry["provenance"]["prior_status_carried_forward"] is False
        elif entry["origin"] == "warm_start_csv":
            assert entry["provenance"]["source"] == plan.inputs["warm_start"]
            assert entry["provenance"]["prior_status_carried_forward"] is False


def test_option_b_prepare_fails_closed_before_creating_directory(tmp_path):
    output_dir = tmp_path / "must_not_exist"

    with pytest.raises(ContractError, match="execution_authorization|seed_preflight"):
        rcc.prepare(output_dir, plan_path=PLAN_PATH)

    assert not output_dir.exists()


def test_option_b_exact_bindings_and_unsigned_seed_cardinalities():
    payload = json.loads(PLAN_PATH.read_text())

    assert payload["execution_authorization"] == {
        "status": "PREPARATION_ONLY_NOT_AUTHORIZED",
        "allowed_modes": ["validate-only"],
    }
    assert payload["calibration_window"] == {
        "simulation_start_year": 1980,
        "extract_start_year": 1980,
        "extract_end_year": 2025,
    }
    assert payload["targets"]["n"] == 13
    assert tuple(payload["targets"]["frozen_order"]) == EXPECTED_TARGET_ORDER
    assert [len(payload["seeds"][key]) for key in ("discovery", "confirmation", "smoke")] \
        == [5, 20, 3]
    assert payload["seeds"]["proposal_rng"] == "UNSIGNED"
    assert payload["budget"]["total"] == 10_446
    assert payload["search"]["rejection"]["max_attempts_per_slot"] == 200
    assert payload["search"]["rejection"]["max_raw_global_proposals_per_batch"] == 100_000
    assert payload["search"]["early_stopping"].startswith("none")
    assert payload["budget"]["wall_time_cap_seconds"] == 21_600
    assert payload["budget"]["on_cap"].startswith("stop dispatch")
