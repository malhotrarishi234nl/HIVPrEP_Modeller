#!/usr/bin/env python3
"""Validated production runner for the bounded coupled calibration search.

Modes, each writing its OWN directory so a previous stage can never be overwritten:

    (no arguments)   print the validation report; zero engine construction, zero
                     simulations, nothing written
    --validate-only  the same, explicitly
    --prepare        freeze the design into a new preparation directory; zero simulations
    --smoke          three paired production-versus-harness trajectories (6 runs)
    --run-search     the declared discovery search; requires a verified smoke artifact
    --confirm        fresh-seed confirmation of a frozen shortlist from a COMPLETE search
    --resume         continue a matching partial search without re-running spent budget

Scientific contract: see ``coupled_calibration_search``.  The search statistic J
ranks candidates; it is NOT acceptance.  Acceptance is the frozen 13-target
primary maximum-implausibility rule.  Nothing here widens a bound, adds a
mechanism, relaxes a threshold or launches a second search.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
from coupled_calibration_search import ContractError


# Scientific budgets and seed counts are independent of process concurrency.
# The reviewed local execution ceiling remains ccs.MAX_WORKERS. Larger pools
# require an explicit CLI acknowledgement and are always recorded as HPC mode.
ABSOLUTE_HPC_WORKER_CAP = 192

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PLAN = ROOT / "configs/coupled_search_plan_unordered_orientation_activity_diagnostics_20260921.json"

#: Modules whose content defines a result.  Frozen at preparation and re-verified
#: before every stage that spends simulation budget.
RUNTIME_MODULES = (
    "coupled_calibration_search.py", "run_coupled_calibration.py",
    "run_history_matching.py", "hm_target_register.py", "hm_variance_budget.py",
    "hm_implausibility.py", "hm_diagnostics.py", "run_lhs_calibration.py",
    "calib_extractor.py", "run_v3.py", "engine_v3.py", "demography.py",
    "migration.py", "transmission_corrected.py", "exogenous_diagnoses.py",
    "counter_based_rng.py", "stochastic_allocation.py", "schedule_utils.py",
    "config_reader.py", "config_utils.py", "aids_observation.py",
)

#: Ranges this project has already declared for other experiments, which the
#: manifest scan cannot always see.  Checked in addition to the scan, never
#: instead of it.
DECLARED_FOREIGN_SEEDS = (
    list(range(93000, 93005)) + list(range(191000, 191050)) + list(range(194000, 194050))
    + list(range(195000, 195010)) + list(range(196000, 196005)) + list(range(196100, 196103))
)


@dataclass(frozen=True)
class RunnerPlan:
    """Validated execution-facing view of a coupled-search plan.

    The reviewed search/scoring method remains code-frozen. A suite may change
    experiment identity, authoritative inputs and seed *values* while keeping
    every methodological section and every replicate count unchanged.
    """

    payload: dict

    @property
    def design_name(self) -> str:
        return str(self.payload["design"]["name"])

    @property
    def inputs(self) -> dict:
        return self.payload["inputs"]

    @property
    def simulation_start_year(self) -> int:
        window = self.payload.get("calibration_window")
        return (ccs.SIMULATION_START_YEAR if window is None
                else int(window["simulation_start_year"]))

    @property
    def extract_start_year(self) -> int:
        window = self.payload.get("calibration_window")
        return ccs.EXTRACT_START_YEAR if window is None else int(window["extract_start_year"])

    @property
    def extract_end_year(self) -> int:
        window = self.payload.get("calibration_window")
        return ccs.EXTRACT_END_YEAR if window is None else int(window["extract_end_year"])

    @property
    def source_vector_ids(self) -> tuple[int, ...]:
        return tuple(int(v) for v in self.payload["search"]["source_vector_ids"])

    @property
    def max_warm_vectors(self) -> int:
        return int(self.payload["search"]["max_warm_vectors"])

    @property
    def discovery_seeds(self) -> tuple[int, ...]:
        return self._seed_tuple("discovery")

    @property
    def confirmation_seeds(self) -> tuple[int, ...]:
        return self._seed_tuple("confirmation")

    @property
    def smoke_seeds(self) -> tuple[int, ...]:
        return self._seed_tuple("smoke")

    @property
    def proposal_rng_seed(self) -> int:
        value = self.payload["seeds"]["proposal_rng"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("plan seeds.proposal_rng is unsigned or not an integer")
        return value

    def _seed_tuple(self, field: str) -> tuple[int, ...]:
        values = self.payload["seeds"][field]
        if (not isinstance(values, list)
                or any(isinstance(v, bool) or not isinstance(v, int) for v in values)):
            raise ContractError(f"plan seeds.{field} contains unsigned or non-integer values")
        return tuple(values)

    @property
    def comparison_group(self) -> str | None:
        value = self.payload["seeds"].get("comparison_group")
        return str(value) if value is not None else None

    @property
    def paired_designs(self) -> tuple[str, ...]:
        return tuple(str(v) for v in self.payload["seeds"].get("paired_designs", ()))

    def validate_execution_authorization(self) -> dict:
        """Fail closed when a plan explicitly limits itself to validation."""
        authorization = self.payload.get("execution_authorization")
        if authorization is None:
            return {"status": "legacy_plan_without_explicit_gate", "allowed_modes": "legacy"}
        if set(authorization) != {"status", "allowed_modes"}:
            raise ContractError(
                "execution_authorization must contain exactly status and allowed_modes"
            )
        status = authorization["status"]
        modes = authorization["allowed_modes"]
        if status != "AUTHORIZED_FOR_PREPARATION_AND_ENGINE":
            raise ContractError(
                f"plan execution status is {status!r}; only validate-only is permitted"
            )
        if not isinstance(modes, list) or "prepare" not in modes or "engine" not in modes:
            raise ContractError("authorized plans must explicitly allow prepare and engine")
        return {"status": status, "allowed_modes": modes}


# ==========================================================================
# Small IO helpers
# ==========================================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def dump_json(path: Path, payload) -> None:
    atomic_write_text(Path(path), json.dumps(ccs.json_safe(payload), indent=2) + "\n")


def append_journal(path: Path, payload) -> None:
    """Append one recovery-safe record: written, flushed and fsynced before return."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ccs.json_safe(payload)) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn trailing line from an interrupted append is discarded, not repaired.
            break
    return records


def assert_unoccupied(output_dir: Path) -> None:
    path = Path(output_dir)
    if path.exists() and any(path.iterdir()):
        raise ContractError(
            f"output directory {path} already contains files. Each stage writes its own new "
            f"directory; choose a new --output-dir prefix rather than overwriting a result."
        )


def module_hashes() -> dict[str, str]:
    return {name: ccs.sha256_file(ROOT / name) for name in RUNTIME_MODULES if (ROOT / name).exists()}


def environment() -> dict:
    import platform  # noqa: PLC0415

    git: dict = {}
    try:
        git["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                       capture_output=True, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True,
                                capture_output=True, check=True).stdout
        git["dirty"] = bool(status.strip())
        git["status_sha256"] = ccs.sha256_json(status)
    except (OSError, subprocess.CalledProcessError) as exc:
        git["error"] = f"{type(exc).__name__}: {exc}"
    return {"python": sys.version, "platform": platform.platform(), "argv": list(sys.argv),
            "git": git, "cwd": str(Path.cwd()), "generated_utc": now()}


# ==========================================================================
# Inputs
# ==========================================================================

def input_paths(plan: RunnerPlan | None = None) -> dict[str, Path]:
    inputs = plan.inputs if plan is not None else plan_payload()["inputs"]
    return {
        "base_config": ROOT / inputs["base_config"],
        "calib_space": ROOT / inputs["calib_space"],
        "target_register": ROOT / inputs["target_register"],
        "wave_plan": ROOT / inputs["wave_plan"],
    }


def _config_year(config: dict, field: str) -> int:
    value = config.get("run_control", {}).get(field, {}).get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ContractError(f"base config run_control.{field}.value must be an integer")
    return int(value)


def load_inputs(plan: RunnerPlan | None = None) -> dict:
    """Read every authoritative input and verify its hash.  No engine is built."""
    plan = plan or RunnerPlan(plan_payload())
    paths = input_paths(plan)
    hashes = {}
    for name, path in paths.items():
        if not path.is_file():
            raise ContractError(f"authoritative input missing: {name} -> {path}")
        digest = ccs.sha256_file(path)
        expected = plan.inputs["expected_sha256"][name]
        if digest != expected:
            raise ContractError(
                f"{name} hash changed: expected {expected}, found {digest}. The frozen "
                f"scientific inputs must not move under this design."
            )
        hashes[name] = digest
    warm_path = ROOT / plan.inputs["warm_start"]
    source_dir = ROOT / plan.inputs["source_experiment"]
    warm_sha256 = ccs.sha256_file(warm_path)
    source_manifest_sha256 = ccs.sha256_file(source_dir / "manifest.json")
    expected_hashes = plan.inputs["expected_sha256"]
    for name, digest in (("warm_start", warm_sha256),
                         ("source_manifest", source_manifest_sha256)):
        expected = expected_hashes.get(name)
        if expected is not None and digest != expected:
            raise ContractError(
                f"{name} hash changed: expected {expected}, found {digest}. The frozen "
                f"proposal-source provenance must not move under this design."
            )
    space = ccs.ParameterSpace.from_calib_space(json.loads(paths["calib_space"].read_text()))
    wave_plan = json.loads(paths["wave_plan"].read_text())
    base_config = json.loads(paths["base_config"].read_text())
    targets = ccs.load_frozen_targets(paths["target_register"], wave_plan)
    base_start = _config_year(base_config, "start_year")
    base_end_exclusive = _config_year(base_config, "end_year")
    if base_start != plan.simulation_start_year:
        raise ContractError(
            f"plan simulation_start_year={plan.simulation_start_year} does not match the "
            f"hash-bound base config start_year={base_start}"
        )
    if not (plan.simulation_start_year <= plan.extract_start_year <= plan.extract_end_year):
        raise ContractError("calibration window must satisfy simulation start <= extract range")
    if plan.extract_end_year >= base_end_exclusive:
        raise ContractError(
            f"extract_end_year={plan.extract_end_year} must be before the base config's "
            f"exclusive end_year={base_end_exclusive}"
        )
    register = json.loads(paths["target_register"].read_text())
    expected_workbook = plan.inputs.get("source_workbook_sha256")
    if expected_workbook is not None and register.get("source_workbook_sha256") != expected_workbook:
        raise ContractError(
            "target register workbook hash does not match inputs.source_workbook_sha256"
        )

    from run_hm_confirmation import _verified_manifest  # noqa: PLC0415

    source_manifest = _verified_manifest(source_dir.resolve())
    evaluated = pd.read_csv(source_dir / "wave_01/evaluated.csv",
                            usecols=["design_id", *space.names])
    if evaluated["design_id"].duplicated().any():
        raise ContractError("duplicate design ids in the source experiment")
    source_frame = evaluated.set_index("design_id")
    warm_frame = pd.read_csv(warm_path)
    missing = [n for n in space.names if n not in warm_frame.columns]
    if missing:
        raise ContractError(f"warm-start file lacks parameters: {missing}")
    return {
        "paths": paths, "hashes": hashes, "space": space, "wave_plan": wave_plan,
        "base_config": base_config, "targets": targets, "source_frame": source_frame,
        "warm_frame": warm_frame, "source_manifest": source_manifest,
        "plan": plan, "warm_start_sha256": warm_sha256,
        "source_manifest_sha256": source_manifest_sha256,
    }


def injected_config(base_config: dict, params: dict, space_json: dict, *,
                    simulation_start_year: int = ccs.SIMULATION_START_YEAR) -> dict:
    """The production injection path, in stochastic mode, from the production start year."""
    from run_lhs_calibration import inject_params  # noqa: PLC0415

    config = inject_params(base_config, params, space_json, deterministic_mode=False,
                           simulation_start_year=int(simulation_start_year))
    if _config_year(config, "start_year") != int(simulation_start_year):
        raise ContractError("injected config did not retain the plan-declared simulation start")
    assert_stochastic_msm_config(config)
    return config


def assert_stochastic_msm_config(config: dict) -> None:
    """Config-level assertion of the intended path.  Builds no engine."""
    det = config.get("run_control", {}).get("deterministic_mode", {})
    if not isinstance(det, dict) or det.get("value") is not False:
        raise ContractError(f"run_control.deterministic_mode must be explicitly False, got {det!r}")
    controls = config.get("simulation_controls")
    if isinstance(controls, dict):
        if controls.get("deterministic_mode", {}).get("value", False) is not False:
            raise ContractError("simulation_controls.deterministic_mode is not False")
    block = (config.get("transmission_model", {}).get("disease", {})
             .get("diagnosis", {}).get("msm_relative_diagnosis", {}))
    if block.get("enabled", {}).get("value") is not True:
        raise ContractError("msm_relative_diagnosis.enabled must be True")
    if not block.get("schedule", {}).get("value"):
        raise ContractError("msm_relative_diagnosis schedule is empty")


def injection_checker(base_config: dict, space_json: dict, *,
                      simulation_start_year: int = ccs.SIMULATION_START_YEAR) -> Callable[[dict], None]:
    def check(params: dict) -> None:
        injected_config(base_config, params, space_json,
                        simulation_start_year=simulation_start_year)
    return check


# ==========================================================================
# The passive observer
# ==========================================================================

def stock_summary(population: np.ndarray, groups) -> dict:
    """Orientation/activity-resolved epidemiologic stocks, with axis labels.

    Pure: it reads a copy of the stock tensor, draws no random number and mutates
    nothing.  The original orientation-level table is retained, and a second table
    preserves activity so a later structural-sensitivity decision does not have to
    infer activity tension from aggregate MSM targets.
    """
    p = np.asarray(population, dtype=float)
    if p.ndim != 7 or not np.isfinite(p).all() or (p < 0).any():
        raise ContractError("invalid seven-axis population stock")
    collapsed = p.sum(axis=(0, 2, 6))  # -> orientation x hiv x diagnosis x art
    by_activity = p.sum(axis=(0, 6))  # -> orientation x activity x hiv x diagnosis x art
    return {
        "orientation_hiv_diagnosis_art": collapsed.tolist(),
        "orientation_activity_hiv_diagnosis_art": by_activity.tolist(),
        "axes": ["orientation", "hiv_stage", "diagnosis", "art"],
        "activity_resolved_axes": ["orientation", "activity", "hiv_stage", "diagnosis", "art"],
        "orientations": list(groups.orientations),
        "activities": list(groups.activities),
        "hiv_stages": list(groups.hiv_stages),
        "diagnosis_states": list(groups.diagnosis_states),
        "art_states": list(groups.art_states),
        "denominators": {
            "adult_total": float(p.sum()),
            "adult_by_orientation": p.sum(axis=(0, 2, 3, 4, 5, 6)).tolist(),
            "adult_by_orientation_activity": p.sum(axis=(0, 3, 4, 5, 6)).tolist(),
        },
        "meaning": "stock at the start of the labelled year, before that year's transitions",
    }


class ObservingEngine:
    """Passive start-of-year capture on top of the PRODUCTION engine.

    Built as a runtime subclass of ``run_v3.SimulationEngineV3`` so the observed
    run executes the production code path unchanged; the observer only copies
    state.  It consumes no random number and mutates nothing, which the paired
    smoke check verifies against an unobserved production run.
    """

    def __new__(cls, config, *, seed, stock_years=ccs.STOCK_YEARS):
        from run_v3 import SimulationEngineV3  # noqa: PLC0415

        class _Observed(SimulationEngineV3):
            def __init__(self, config, *, seed, stock_years):
                self.observed_stocks: dict[str, dict] = {}
                self.observed_activity_flows: dict[str, dict] = {}
                self._observer_years = tuple(int(y) for y in stock_years)
                super().__init__(config, seed=seed)

            def step_simulation(self, step: int):
                year = self.time.start_year + step * self.time.years_per_step
                for capture in self._observer_years:
                    if abs(year - capture) < 1e-9 and str(capture) not in self.observed_stocks:
                        self.observed_stocks[str(capture)] = stock_summary(
                            self.state.population.copy(), self.groups)
                result = super().step_simulation(step)
                infections = getattr(self, "_last_infections_array", None)
                if infections is not None:
                    annual_year = str(int(year))
                    # infections: age x orientation x activity x PrEP
                    counts = np.asarray(infections, dtype=float).sum(axis=(0, 3))
                    entry = self.observed_activity_flows.setdefault(annual_year, {
                        "new_infections_by_orientation_activity": np.zeros_like(counts).tolist(),
                        "orientations": list(self.groups.orientations),
                        "activities": list(self.groups.activities),
                        "axes": ["orientation", "activity"],
                    })
                    accumulated = np.asarray(
                        entry["new_infections_by_orientation_activity"], dtype=float
                    )
                    entry["new_infections_by_orientation_activity"] = (accumulated + counts).tolist()
                return result

        engine = _Observed(config, seed=seed, stock_years=stock_years)
        if engine.deterministic is not False:
            raise ContractError(f"engine.deterministic is {engine.deterministic!r}, expected False")
        if getattr(engine, "msm_relative_diagnosis_schedule", None) is None:
            raise ContractError("engine is not on the enabled MSM relative-diagnosis path")
        engine.output_writer.enabled = False
        return engine


def run_one_trajectory(config: dict, seed: int, *, observe: bool,
                       extract_start_year: int = ccs.EXTRACT_START_YEAR,
                       extract_end_year: int = ccs.EXTRACT_END_YEAR) -> dict:
    """One full plan-declared trajectory. Returns extracted outputs and states."""
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        if observe:
            engine = ObservingEngine(copy.deepcopy(config), seed=int(seed))
        else:
            engine = SimulationEngineV3(copy.deepcopy(config), seed=int(seed))
            engine.output_writer.enabled = False
        result = engine.run(run_number=1)
    outputs = extract_annual_outputs(result, int(extract_start_year), int(extract_end_year))
    observed = getattr(engine, "observed_stocks", {})
    activity_flows = getattr(engine, "observed_activity_flows", {})
    if observe:
        missing = [str(y) for y in ccs.STOCK_YEARS if str(y) not in observed]
        if missing:
            raise ContractError(f"observer did not capture start-of-year stocks for {missing}")
        missing_flow_years = [str(y) for y in range(int(extract_start_year),
                                                    int(extract_end_year) + 1)
                              if str(y) not in activity_flows]
        if missing_flow_years:
            raise ContractError(f"observer did not capture activity flows for {missing_flow_years}")
        for year, expected in outputs["new_infections"].items():
            actual = float(np.asarray(
                activity_flows[str(year)]["new_infections_by_orientation_activity"], dtype=float
            ).sum())
            if actual != float(expected):
                raise ContractError(
                    f"activity-stratified infections do not sum to annual total in {year}: "
                    f"{actual} != {expected}"
                )
    return {
        "seed": int(seed), "outputs": outputs, "observed_stocks": observed,
        "observed_activity_flows": activity_flows,
        "summary": result["summary"], "final_state": result["final_state"],
        "childhood_state": result["childhood_state"],
        "engine_deterministic": bool(engine.deterministic),
        "msm_relative_diagnosis_path": True,
        "wall_seconds": time.time() - started,
    }


# ==========================================================================
# Workers (module level so they can be pickled)
# ==========================================================================

def evaluate_candidate_worker(payload: dict) -> dict:
    """K replicates of one candidate on shared common random numbers."""
    row = {"candidate_id": int(payload["candidate_id"]), "batch": int(payload["batch"]),
           "origin": payload["origin"], "error": ""}
    started = time.time()
    try:
        config = payload["config"]
        assert_stochastic_msm_config(config)
        row["config_sha256"] = ccs.sha256_json(config)
        row["params_sha256"] = ccs.vector_identity(payload["params"])
        seeds = [int(s) for s in payload["replicate_seeds"]]
        per_seed = []
        for seed in seeds:
            trajectory = run_one_trajectory(
                config, seed, observe=True,
                extract_start_year=payload.get("extract_start_year", ccs.EXTRACT_START_YEAR),
                extract_end_year=payload.get("extract_end_year", ccs.EXTRACT_END_YEAR),
            )
            series = {name: {int(y): float(v) for y, v in values.items()}
                      for name, values in trajectory["outputs"].items()}
            per_seed.append({
                "seed": seed, "annual_outputs": series,
                "observed_stocks": trajectory["observed_stocks"],
                "observed_activity_flows": trajectory["observed_activity_flows"],
                "wall_seconds": trajectory["wall_seconds"],
                "engine_deterministic": trajectory["engine_deterministic"],
            })
            if payload.get("save_final_state"):
                per_seed[-1]["final_state_file"] = _save_states(
                    Path(payload["state_dir"]), payload["candidate_id"], seed, trajectory)
        columns = payload["target_columns"]
        years = payload["target_years"]
        matrix = np.array([[record["annual_outputs"][c][y] for c, y in zip(columns, years)]
                           for record in per_seed], dtype=float)
        if not np.isfinite(matrix).all():
            raise ContractError("non-finite target output in a replicate")
        row["replicate_seeds"] = seeds
        row["target_matrix"] = matrix.tolist()
        row["means"] = matrix.mean(axis=0).tolist()
        row["sample_variances"] = matrix.var(axis=0, ddof=1).tolist()
        row["per_seed"] = per_seed
    except Exception as exc:  # noqa: BLE001 - recorded as evidence, never retried or hidden
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_seconds"] = time.time() - started
    return row


def _save_states(state_dir: Path, candidate_id: int, seed: int, trajectory: dict) -> str:
    """Full 2025 adult/child tensors: diagnostic states, NOT restart checkpoints."""
    state_dir.mkdir(parents=True, exist_ok=True)
    stem = f"candidate_{int(candidate_id)}_seed{int(seed)}"
    np.savez_compressed(
        state_dir / f"{stem}.npz",
        adult_final_2025=np.asarray(trajectory["final_state"]),
        childhood_final_2025=np.asarray(trajectory["childhood_state"]),
    )
    dump_json(state_dir / f"{stem}.meta.json", {
        "candidate_id": int(candidate_id), "seed": int(seed),
        "adult_axes": ["age", "orientation", "activity", "hiv_stage", "diagnosis", "art", "prep"],
        "restart_safe": False,
        "warning": "Model-derived end-of-horizon stocks. Residence queues, RNG state and other "
                   "engine state are NOT captured; this cannot restart a simulation.",
    })
    return f"{stem}.npz"


def smoke_pair_worker(payload: dict) -> dict:
    """One paired production-versus-harness check: exactly two full trajectories."""
    row = {"pair_id": int(payload["pair_id"]), "label": payload["label"],
           "seed": int(payload["seed"]), "error": ""}
    started = time.time()
    try:
        config = payload["config"]
        assert_stochastic_msm_config(config)
        trajectory_kwargs = {
            "extract_start_year": payload.get("extract_start_year", ccs.EXTRACT_START_YEAR),
            "extract_end_year": payload.get("extract_end_year", ccs.EXTRACT_END_YEAR),
        }
        production = run_one_trajectory(config, payload["seed"], observe=False,
                                        **trajectory_kwargs)
        harness = run_one_trajectory(config, payload["seed"], observe=True,
                                     **trajectory_kwargs)
        pd.testing.assert_frame_equal(production["summary"], harness["summary"], check_exact=True)
        np.testing.assert_array_equal(production["final_state"], harness["final_state"])
        np.testing.assert_array_equal(production["childhood_state"], harness["childhood_state"])
        if set(production["outputs"]) != set(harness["outputs"]):
            raise ContractError("extracted output series differ between production and harness")
        for name, series in production["outputs"].items():
            other = harness["outputs"][name]
            if set(series) != set(other):
                raise ContractError(f"extracted years differ for {name}")
            for year, value in series.items():
                if not (value == other[year] or (np.isnan(value) and np.isnan(other[year]))):
                    raise ContractError(f"extracted {name}@{year} differs: {value} vs {other[year]}")
        row.update({
            "trajectories": 2,
            "all_summary_columns_identical": True,
            "all_extracted_series_identical": True,
            "adult_and_childhood_final_states_identical": True,
            "observer_captured_years": sorted(harness["observed_stocks"]),
            "engine_deterministic": harness["engine_deterministic"],
            "msm_relative_diagnosis_path": True,
            "production_wall_seconds": production["wall_seconds"],
            "harness_wall_seconds": harness["wall_seconds"],
            "observed_stocks": harness["observed_stocks"],
        })
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_seconds"] = time.time() - started
    return row


# ==========================================================================
# Seed reservation
# ==========================================================================

def seed_preflight(outputs_root: Path | None = None, plan: RunnerPlan | None = None) -> dict:
    """Check recorded seed use BEFORE reserving.  A collision stops the run.

    Manifests this design wrote itself are skipped: re-reading our own reserved
    ranges back out of our own artifacts is not a foreign collision.  Every other
    manifest under ``outputs/`` counts, as do the declared foreign ranges.
    """
    from run_joint_fit_comparison_20260917 import _seeds_from_obj  # noqa: PLC0415

    plan = plan or RunnerPlan(plan_payload())
    root = Path(outputs_root) if outputs_root is not None else ROOT / "outputs"
    found: set[int] = set()
    n_manifests = 0
    own: list[str] = []
    for manifest in sorted(root.rglob("manifest.json")):
        try:
            payload = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        n_manifests += 1
        same_design = isinstance(payload, dict) and payload.get("design") == plan.design_name
        same_comparison = (
            isinstance(payload, dict)
            and plan.comparison_group is not None
            and payload.get("seed_comparison_group") == plan.comparison_group
        )
        if same_design or same_comparison:
            own.append(str(manifest.relative_to(root)))
            continue
        _seeds_from_obj(payload, found)
    found = set(found) | set(DECLARED_FOREIGN_SEEDS)
    reserved = {
        "discovery": list(plan.discovery_seeds),
        "confirmation": list(plan.confirmation_seeds),
        "smoke": list(plan.smoke_seeds), "proposal_rng": [plan.proposal_rng_seed],
    }
    flat = [s for values in reserved.values() for s in values]
    if len(set(flat)) != len(flat):
        raise ContractError("the reserved seed ranges are not mutually disjoint")
    overlap = sorted(set(flat) & found)
    if overlap:
        raise ContractError(
            f"reserved seeds collide with recorded use: {overlap}. Stopping. A replacement plan "
            f"must be declared and recorded; this runner never silently chooses another range."
        )
    return {
        "reserved": reserved, "n_manifests_scanned": n_manifests,
        "n_own_manifests_skipped": len(own), "own_manifests": own,
        "n_recorded_seeds": len(found), "overlap": overlap,
        "limitation": "Only seeds recorded in manifest.json files under outputs/, plus the "
                      "declared foreign ranges listed in this runner, are visible to the scan. "
                      f"Manifests carrying design={plan.design_name!r}, or the explicitly paired "
                      f"comparison group {plan.comparison_group!r}, are this experiment's own "
                      "and are skipped.",
    }


# ==========================================================================
# Validation (no engine, no simulation, no writes)
# ==========================================================================

def validate(*, plan_path: Path | None = None, run_seed_preflight: bool = True) -> dict:
    checks: dict[str, dict] = {}
    state: dict = {}
    plan_payload_from_file = (
        json.loads(Path(plan_path).read_text()) if plan_path is not None else plan_payload()
    )
    plan = RunnerPlan(plan_payload_from_file)

    def check(name: str, fn):
        try:
            checks[name] = {"ok": True, "detail": ccs.json_safe(fn())}
        except Exception as exc:  # noqa: BLE001 - every check is reported, not raised mid-way
            checks[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def inputs():
        state.update(load_inputs(plan))
        return {"hashes": state["hashes"], "warm_start_sha256": state["warm_start_sha256"],
                "source_manifest_sha256": state["source_manifest_sha256"],
                "source_rows": int(len(state["source_frame"]))}

    check("authoritative_inputs_and_source_integrity", inputs)

    if "space" in state:
        check("block_partition_equals_space", lambda: {
            "n_parameters": state["space"].n,
            "blocks": {b: len(ccs.BLOCKS[b]) for b in ccs.BLOCK_ORDER},
            "block_pairs": [list(p) for p in ccs.BLOCK_PAIRS],
        })
        check("frozen_targets_and_budgets", lambda: {
            "n_targets": len(state["targets"]),
            "order": [t.key for t in state["targets"]],
            "groups": {g: list(ccs.OBJECTIVE_GROUPS[g]) for g in ccs.GROUP_ORDER},
            "targets": [t.as_row() for t in state["targets"]],
        })
        check("production_injection_of_source_vectors", lambda: [
            {"design_id": int(d), "injected": True,
             "config_sha256": ccs.sha256_json(injected_config(
                 state["base_config"],
                 state["space"].as_dict(state["source_frame"].loc[d, list(state["space"].names)]
                                        .to_numpy(dtype=float)),
                 json.loads(state["paths"]["calib_space"].read_text()),
                 simulation_start_year=plan.simulation_start_year))}
            for d in plan.source_vector_ids
        ])
        check("explanatory_schedule_diagnostics", lambda: {
            "note": "Explanatory diagnostics only; not calibration parameters.",
            "contact_products_first_rows": ccs.effective_contact_products(injected_config(
                state["base_config"],
                state["space"].as_dict(state["source_frame"].loc[plan.source_vector_ids[0],
                                                                 list(state["space"].names)]
                                       .to_numpy(dtype=float)),
                json.loads(state["paths"]["calib_space"].read_text()),
                simulation_start_year=plan.simulation_start_year))[:3],
        })

    check("budget_arithmetic", lambda: {
        "table": ccs.budget_table(),
        "total": ccs.TOTAL_TRAJECTORY_BUDGET,
        "checks_out": ccs.SMOKE_TRAJECTORIES + ccs.DISCOVERY_TRAJECTORIES
                      + ccs.CONFIRMATION_TRAJECTORIES == ccs.TOTAL_TRAJECTORY_BUDGET,
        "note": "Unit and full-suite test execution is separate engineering work and is NOT "
                "capped by this table.",
    })
    if run_seed_preflight:
        check("seed_preflight", lambda: seed_preflight(plan=plan))
    else:
        checks["seed_preflight"] = {"ok": False, "detail": "not run"}
    check("plan_execution_contract", lambda: assert_plan_matches(plan.payload))
    check("execution_authorization", plan.validate_execution_authorization)
    report = {"ok": all(c["ok"] for c in checks.values()), "checks": checks,
              "simulations_started": 0, "engines_constructed": 0,
              "artifacts_written": 0, "generated_utc": now()}
    return report | {"state": state}


def assert_plan_matches(plan: dict) -> dict:
    """Validate supported plan overrides and freeze all methodological sections.

    Design name/question, input locations/hashes and seed values are experiment
    data. Everything else must exactly match the reviewed implementation. Seed
    counts are also fixed because they determine variance formulas and budgets.
    """
    expected = plan_payload()
    required_sections = set(expected)
    optional_sections = {"calibration_window", "execution_authorization"}
    if not required_sections.issubset(plan) or set(plan) - required_sections - optional_sections:
        missing = sorted(required_sections - set(plan))
        extra = sorted(set(plan) - required_sections - optional_sections)
        raise ContractError(f"plan sections differ; missing={missing}, extra={extra}")
    frozen_sections = (
        "targets", "parameters", "objective", "acceptance", "search", "budget",
        "confirmation",
    )
    differences = [key for key in frozen_sections
                   if ccs.sha256_json(expected[key]) != ccs.sha256_json(plan.get(key))]
    if differences:
        raise ContractError(f"plan changes frozen methodological sections: {differences}")

    expected_diagnostics = copy.deepcopy(expected["diagnostics"])
    window = plan.get("calibration_window")
    if window is not None:
        expected_diagnostics["annual_activity_flows"]["years"] = [
            window["extract_start_year"], window["extract_end_year"]
        ]
    if ccs.sha256_json(expected_diagnostics) != ccs.sha256_json(plan.get("diagnostics")):
        raise ContractError(
            "plan changes diagnostics beyond the plan-declared extraction window"
        )

    expected_design = dict(expected["design"])
    actual_design = dict(plan["design"])
    for field in ("name", "question", "declared_as"):
        expected_design.pop(field, None)
        actual_design.pop(field, None)
    if ccs.sha256_json(expected_design) != ccs.sha256_json(actual_design):
        raise ContractError("plan design may change only name, question and declared_as")
    if not str(plan["design"].get("name", "")).strip():
        raise ContractError("plan design.name must be non-empty")
    if not str(plan["design"].get("declared_as", "")).strip():
        raise ContractError("plan design.declared_as must be non-empty")

    input_keys = set(expected["inputs"])
    optional_input_fields = {"source_workbook_sha256"}
    if (not input_keys.issubset(plan["inputs"])
            or set(plan["inputs"]) - input_keys - optional_input_fields):
        raise ContractError("plan inputs must retain reviewed fields and only known bindings")
    hash_keys = set(expected["inputs"]["expected_sha256"])
    optional_hash_keys = {"warm_start", "source_manifest"}
    actual_hash_keys = set(plan["inputs"]["expected_sha256"])
    if (not hash_keys.issubset(actual_hash_keys)
            or actual_hash_keys - hash_keys - optional_hash_keys):
        raise ContractError(
            "plan inputs.expected_sha256 must cover authoritative inputs and only known provenance"
        )
    for field in input_keys - {"expected_sha256"}:
        value = plan["inputs"][field]
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"plan inputs.{field} must be a non-empty path")
    for field in optional_hash_keys:
        if field in actual_hash_keys:
            value = plan["inputs"]["expected_sha256"][field]
            if not isinstance(value, str) or len(value) != 64:
                raise ContractError(f"plan expected_sha256.{field} must be a SHA-256 digest")
    if "source_workbook_sha256" in plan["inputs"]:
        value = plan["inputs"]["source_workbook_sha256"]
        if not isinstance(value, str) or len(value) != 64:
            raise ContractError("plan inputs.source_workbook_sha256 must be a SHA-256 digest")

    window = plan.get("calibration_window")
    if window is not None:
        required_window = {"simulation_start_year", "extract_start_year", "extract_end_year"}
        if set(window) != required_window:
            raise ContractError("calibration_window must declare simulation/extract start and end")
        if any(isinstance(window[k], bool) or not isinstance(window[k], int)
               for k in required_window):
            raise ContractError("calibration_window values must be integers")
        if not (window["simulation_start_year"] <= window["extract_start_year"]
                <= window["extract_end_year"]):
            raise ContractError("calibration_window ordering is invalid")

    seeds = plan["seeds"]
    optional_seed_fields = {"comparison_group", "paired_designs", "reuse_policy"}
    if set(seeds) - optional_seed_fields != set(expected["seeds"]):
        raise ContractError("plan seeds must retain the reviewed seed fields")
    for field in ("discovery", "confirmation", "smoke"):
        values = seeds[field]
        if len(values) != len(expected["seeds"][field]):
            raise ContractError(
                f"plan seeds.{field} must contain {len(expected['seeds'][field])} values"
            )
        if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
            raise ContractError(f"plan seeds.{field} must contain integers")
    if isinstance(seeds["proposal_rng"], bool) or not isinstance(seeds["proposal_rng"], int):
        raise ContractError("plan seeds.proposal_rng must be an integer")
    flat = [*seeds["discovery"], *seeds["confirmation"], *seeds["smoke"],
            seeds["proposal_rng"]]
    if len(flat) != len(set(flat)):
        raise ContractError("plan seed values must be mutually disjoint")
    comparison_group = seeds.get("comparison_group")
    paired_designs = seeds.get("paired_designs")
    if comparison_group is not None:
        if not isinstance(comparison_group, str) or not comparison_group.strip():
            raise ContractError("plan seeds.comparison_group must be a non-empty string")
        if not isinstance(paired_designs, list) or len(paired_designs) < 2:
            raise ContractError("paired CRN plans must declare at least two paired_designs")
        if plan["design"]["name"] not in paired_designs:
            raise ContractError("plan design.name must occur in seeds.paired_designs")
        if seeds.get("reuse_policy") != "common_random_numbers_across_declared_designs_only":
            raise ContractError("paired CRN plans need the reviewed explicit reuse_policy")
    elif paired_designs is not None or "reuse_policy" in seeds:
        raise ContractError("paired_designs/reuse_policy require seeds.comparison_group")
    return {
        "plan_sections_verified": sorted(plan),
        "overridable_fields": ["design.name", "design.question", "design.declared_as",
                               "inputs", "seed values", "calibration_window",
                               "execution_authorization"],
        "design": plan["design"]["name"],
    }


def plan_payload() -> dict:
    """The design, in machine-readable form, generated from the module constants."""
    return {
        "design": {
            "name": ccs.DESIGN_NAME,
            "question": "Can the unordered-orientation model jointly reproduce mature MSM "
                        "HIV-diagnosis and AIDS-diagnosis anchors while emphasizing the "
                        "2000-2015 treatment era and post-2015 forecasting state?",
            "status_vocabulary": list(ccs.ALL_STATUSES),
            "declared_as": "bounded forecast-window rerun under the user-approved source "
                           "eligibility policy; numerical settings are declared "
                           "computational choices, not empirical estimates or claims of optimality",
            "not": ["emulator", "gradient inference", "posterior sampler",
                    "proof of efficient global optimization", "exhaustive coverage of 33 dimensions"],
        },
        "inputs": {
            "source_experiment": ccs.SOURCE_EXPERIMENT,
            "base_config": ccs.BASE_CONFIG_PATH,
            "calib_space": ccs.CALIB_SPACE_PATH,
            "target_register": ccs.TARGET_REGISTER_PATH,
            "wave_plan": ccs.WAVE_PLAN_PATH,
            "warm_start": ccs.WARM_START_PATH,
            "expected_sha256": dict(ccs.EXPECTED_INPUT_SHA256),
        },
        "targets": {
            "n": ccs.N_TARGETS, "frozen_order": list(ccs.FROZEN_TARGET_ORDER),
            "diagnosis_series": ccs.DIAGNOSIS_SERIES, "diagnosis_years": list(ccs.DIAGNOSIS_YEARS),
            "aids_series": ccs.AIDS_SERIES, "aids_years": list(ccs.AIDS_YEARS),
            "reporting_groups": {g: list(ccs.OBJECTIVE_GROUPS[g]) for g in ccs.GROUP_ORDER},
            "unresolved_target_policy": "fatal; never silently dropped",
        },
        "parameters": {
            "n": 33, "coordinates": "original linear normalized u=(x-lower)/(upper-lower)",
            "transformations": "none", "blocks": {b: list(ccs.BLOCKS[b]) for b in ccs.BLOCK_ORDER},
            "block_pairs": [list(p) for p in ccs.BLOCK_PAIRS],
        },
        "objective": {
            "search_residual": "r_i = (mu_i - z_i)/sqrt(B_i)",
            "B_i": "frozen non-process variance: observation + code + target-relative "
                   f"{ccs.PRIMARY_DISCREPANCY} discrepancy (+ any extra budget term)",
            "group_statistic": "R_G = sqrt(mean(r_i^2))",
            "rank": "J = max_G R_G; ties by mean_G R_G, then candidate id",
            "purpose": "SEARCH RANKING ONLY; equal group treatment is a declared heuristic and "
                       "is not a likelihood, weight or acceptance rule",
        },
        "acceptance": {
            "rule": "frozen 13-target primary maximum implausibility",
            "process_variance": "s_i^2 (1 + 1/K)", "discrepancy": ccs.PRIMARY_DISCREPANCY,
            "cutoff": ccs.IMPLAUSIBILITY_CUTOFF, "boundary": "I_max <= cutoff passes",
            "sensitivity_discrepancy": ccs.SENSITIVITY_DISCREPANCY,
            "sensitivity_status": "reported only; never promotes a candidate",
            "recorded_fields": ["search_rank", "discovery_primary_pass",
                                "fresh_confirmation_primary_pass", "sensitivity_pass"],
        },
        "search": {
            "initial_archive": ccs.INITIAL_ARCHIVE_SIZE,
            "source_vector_ids": list(ccs.SOURCE_VECTOR_IDS),
            "max_warm_vectors": ccs.MAX_WARM_VECTORS,
            "batches": ccs.N_BATCHES, "batch_size": ccs.BATCH_SIZE,
            "per_batch": {"global_lhs": ccs.GLOBAL_PER_BATCH,
                          "differential_full": ccs.FULL_DIFFERENTIAL_PER_BATCH,
                          "differential_block": ccs.BLOCK_DIFFERENTIAL_PER_BATCH},
            "max_discovery_vectors": ccs.MAX_DISCOVERY_VECTORS,
            "parent_pool": {"size": ccs.POOL_SIZE, "best_by_J": ccs.POOL_BEST_J,
                            "per_group": ccs.POOL_PER_GROUP, "maximin_diversity": ccs.POOL_DIVERSITY},
            "differential": {"formula": "u_new = u_a + F (u_b - u_c)", "F_values": list(ccs.F_VALUES),
                             "F_rule": "alternating by proposal index",
                             "crossover": False, "clipping": False, "domain_shrinking": False},
            "rejection": {"max_attempts_per_slot": ccs.MAX_ATTEMPTS_PER_SLOT,
                          "fallback": "one valid global draw, recorded",
                          "max_raw_global_proposals_per_batch": ccs.MAX_RAW_PROPOSALS_PER_BATCH,
                          "duplicate_tolerance_max_coordinate": ccs.DUPLICATE_TOLERANCE},
            "early_stopping": "none; neither a good fit nor an empty accepted set stops the run",
        },
        "seeds": {
            "discovery": list(ccs.DISCOVERY_SEEDS), "confirmation": list(ccs.CONFIRMATION_SEEDS),
            "smoke": list(ccs.SMOKE_SEEDS), "proposal_rng": ccs.PROPOSAL_RNG_SEED,
            "design": "common random numbers shared across candidates within a stage; "
                      "confirmation seeds disjoint from discovery and from previous experiments",
        },
        "budget": {
            "table": ccs.budget_table(), "total": ccs.TOTAL_TRAJECTORY_BUDGET,
            "default_workers": ccs.DEFAULT_WORKERS, "max_workers": ccs.MAX_WORKERS,
            "wall_time_cap_seconds": ccs.WALL_TIME_CAP_SECONDS,
            "on_cap": "stop dispatch, finish bounded in-flight work, mark PARTIAL_TIME_LIMIT",
            "resume": "never resets the total simulation budget; spent launch attempts stay spent",
        },
        "confirmation": {
            "shortlist_size": ccs.SHORTLIST_SIZE,
            "primary_pass_head": ccs.SHORTLIST_PRIMARY_PASS_HEAD,
            "replicates": ccs.CONFIRMATION_REPLICATES,
            "rules": ["freeze the shortlist before any fresh simulation",
                      "never pool discovery and confirmation seeds",
                      "never choose a favourable seed subset",
                      "never iteratively replace a failed candidate"],
        },
        "diagnostics": {
            "stock_years": list(ccs.STOCK_YEARS),
            "stock_axes": ["orientation", "activity", "hiv_stage", "diagnosis", "art"],
            "annual_activity_flows": {
                "measure": "new infections",
                "axes": ["orientation", "activity"],
                "years": [ccs.EXTRACT_START_YEAR, ccs.EXTRACT_END_YEAR],
                "purpose": "structural sensitivity diagnostic only; no activity-stratified "
                           "observations enter calibration or acceptance",
            },
            "confirmation_state_tensors": {"years": [2025], "restart_safe": False},
            "observer": "passive; consumes no RNG, mutates no state, verified by paired smoke",
        },
    }


# ==========================================================================
# Stage: prepare
# ==========================================================================

def prepare(output_dir: Path, *, plan_path: Path | None = None,
            initial_size: int | None = None) -> dict:
    """Freeze the design.  ``initial_size`` below the declared archive size is an
    ENGINEERING reduction: it is recorded in the manifest and flagged, so a
    reduced preparation can never be mistaken for the declared design."""
    report = validate(plan_path=plan_path)
    if not report["ok"]:
        raise ContractError(json.dumps({k: v for k, v in report["checks"].items() if not v["ok"]},
                                       indent=2))
    state = report["state"]
    plan_contract = state["plan"]
    size = ccs.INITIAL_ARCHIVE_SIZE if initial_size is None else int(initial_size)
    space = state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    check = injection_checker(
        state["base_config"], space_json,
        simulation_start_year=plan_contract.simulation_start_year,
    )
    generator = ccs.ProposalGenerator(space, plan_contract.proposal_rng_seed)
    entries, diagnostics = ccs.build_initial_vectors(
        space, state["source_frame"], state["warm_frame"], generator, injection_check=check,
        size=size, source_vector_ids=plan_contract.source_vector_ids,
        source_experiment=plan_contract.inputs["source_experiment"],
        warm_start_path=plan_contract.inputs["warm_start"],
        max_warm_vectors=plan_contract.max_warm_vectors)
    rng_state_after_initialisation = generator.get_state()

    anchor_params = entries[0]["params"]
    anchor_config = injected_config(
        state["base_config"], anchor_params, space_json,
        simulation_start_year=plan_contract.simulation_start_year,
    )
    assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    initial = [
        {"candidate_id": i, "batch": 0, "origin": e["origin"], "provenance": e["provenance"],
         "params": e["params"], "params_sha256": e["params_sha256"]}
        for i, e in enumerate(entries)
    ]
    space.bounds_table().to_csv(output_dir / "parameter_bounds.csv", index=False)
    pd.DataFrame([t.as_row() for t in state["targets"]]).to_csv(
        output_dir / "frozen_targets.csv", index=False)
    pd.DataFrame(ccs.effective_contact_products(anchor_config)).to_csv(
        output_dir / "effective_contact_products_anchor.csv", index=False)
    pd.DataFrame(ccs.msm_diagnosis_probability_table(
        anchor_config, range(plan_contract.simulation_start_year,
                             plan_contract.extract_end_year + 1))).to_csv(
        output_dir / "msm_diagnosis_probabilities_anchor.csv", index=False)
    dump_json(output_dir / "initial_archive.json", {
        "vectors": initial, "diagnostics": diagnostics,
        "note": "Every vector is re-evaluated on fresh common random numbers. No previous "
                "output score, fit label or acceptance status is reused as an evaluation.",
    })
    plan = plan_contract.payload
    dump_json(output_dir / "frozen_plan.json", plan)
    manifest = {
        "design": plan_contract.design_name,
        "seed_comparison_group": plan_contract.comparison_group,
        "status": ccs.STATUS_PREPARED, "stage": "prepare",
        "simulations_started": 0,
        "generated_utc": now(), "input_sha256": state["hashes"],
        "warm_start_sha256": state["warm_start_sha256"],
        "source_manifest_sha256": state["source_manifest_sha256"],
        "module_sha256": module_hashes(), "environment": environment(),
        "plan_sha256": ccs.sha256_json(plan),
        "anchor_config_sha256": ccs.sha256_json(anchor_config),
        "proposal_rng": {"seed": plan_contract.proposal_rng_seed,
                         "state_after_initialisation": rng_state_after_initialisation},
        "initial_archive_size": len(initial),
        "declared_initial_archive_size": ccs.INITIAL_ARCHIVE_SIZE,
        "engineering_reduced_archive": len(initial) != ccs.INITIAL_ARCHIVE_SIZE,
        "validation": {k: v for k, v in report.items() if k != "state"},
        "budget": {"table": ccs.budget_table(), "total": ccs.TOTAL_TRAJECTORY_BUDGET},
        "limitations": [
            "PREPARED means the design is frozen and verified. It is not evidence about the model.",
            "The previous 512-vector unordered-orientation search found no primary passer under "
            "the superseded 15-target rule; its best candidates seed this larger search but their "
            "old scores are never reused as evaluations.",
            "The AIDS observation proxy remains an unvalidated assumption; a good numerical fit "
            "cannot validate its definition.",
        ],
    }
    dump_json(output_dir / "manifest.json", manifest)
    write_artifact_index(output_dir)
    return manifest


def write_artifact_index(output_dir: Path) -> None:
    files = [p for p in sorted(Path(output_dir).rglob("*"))
             if p.is_file() and p.name not in {"artifacts.json", "artifacts.json.tmp"}]
    dump_json(Path(output_dir) / "artifacts.json", {
        "generated_utc": now(),
        "files": [{"path": str(p.relative_to(output_dir)), "bytes": p.stat().st_size,
                   "sha256": ccs.sha256_file(p)} for p in files],
    })


def verify_artifact_index(directory: Path) -> dict:
    index = json.loads((Path(directory) / "artifacts.json").read_text())
    for entry in index["files"]:
        path = Path(directory) / entry["path"]
        if not path.is_file():
            raise ContractError(f"artifact missing from {directory}: {entry['path']}")
        if path.stat().st_size != entry["bytes"] or ccs.sha256_file(path) != entry["sha256"]:
            raise ContractError(f"artifact failed integrity verification: {entry['path']}")
    return {"n_files": len(index["files"])}


def load_prepared(prepared_dir: Path) -> dict:
    """Verify a preparation directory against the CURRENT inputs and code."""
    prepared_dir = Path(prepared_dir)
    manifest = json.loads((prepared_dir / "manifest.json").read_text())
    if manifest.get("status") != ccs.STATUS_PREPARED:
        raise ContractError(f"{prepared_dir} has status {manifest.get('status')!r}, expected PREPARED")
    verify_artifact_index(prepared_dir)
    plan_payload_frozen = json.loads((prepared_dir / "frozen_plan.json").read_text())
    assert_plan_matches(plan_payload_frozen)
    plan = RunnerPlan(plan_payload_frozen)
    plan.validate_execution_authorization()
    state = load_inputs(plan)
    if manifest["input_sha256"] != state["hashes"]:
        raise ContractError("prepared inputs differ from the current inputs")
    if manifest["warm_start_sha256"] != state["warm_start_sha256"]:
        raise ContractError("warm-start file changed since preparation")
    current = module_hashes()
    differences = sorted(n for n in set(manifest["module_sha256"]) | set(current)
                         if manifest["module_sha256"].get(n) != current.get(n))
    if differences:
        raise ContractError(f"code changed since preparation: {differences}")
    archive = json.loads((prepared_dir / "initial_archive.json").read_text())
    return {"manifest": manifest, "plan": plan.payload, "contract": plan,
            "initial": archive["vectors"],
            "initial_diagnostics": archive["diagnostics"], "state": state,
            "prepared_dir": prepared_dir,
            "prepared_sha256": ccs.sha256_file(prepared_dir / "manifest.json")}


# ==========================================================================
# Stage: smoke
# ==========================================================================

def build_smoke_pairs(prepared: dict) -> list[dict]:
    """Three pairs, each one production and one observed trajectory at the same seed."""
    state = prepared["state"]
    space = state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    warm = [entry for entry in prepared["initial"]
            if entry["origin"] in ("warm_source_vector", "warm_start_csv")]
    if len(warm) < 2:
        raise ContractError("the smoke check must exercise at least two warm vectors")
    seeds = prepared["contract"].smoke_seeds
    plan = prepared["contract"]
    chosen = warm[:len(seeds)]
    pairs = []
    for index, (entry, seed) in enumerate(zip(chosen, seeds)):
        config = injected_config(
            state["base_config"], entry["params"], space_json,
            simulation_start_year=plan.simulation_start_year,
        )
        pairs.append({
            "pair_id": index, "label": f"{entry['origin']}:{entry['candidate_id']}",
            "candidate_id": entry["candidate_id"], "origin": entry["origin"],
            "provenance": entry["provenance"], "seed": int(seed), "config": config,
            "params": entry["params"],
            "extract_start_year": plan.extract_start_year,
            "extract_end_year": plan.extract_end_year,
        })
    return pairs


def smoke(prepared_dir: Path, output_dir: Path, *, workers: int,
          worker: Callable[[dict], dict] = smoke_pair_worker,
          executor_factory=ProcessPoolExecutor) -> dict:
    prepared = load_prepared(prepared_dir)
    gate = diagnosis_test_gate()
    assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pairs = build_smoke_pairs(prepared)
    started = time.time()
    rows: list[dict] = []
    with executor_factory(max_workers=min(workers, len(pairs))) as pool:
        futures = [pool.submit(worker, pair) for pair in pairs]
        for future in futures:
            rows.append(future.result())
    rows.sort(key=lambda r: r["pair_id"])
    failures = [r for r in rows if r.get("error")]
    trajectories = 2 * len(pairs)
    per_trajectory = [r["wall_seconds"] / 2.0 for r in rows if not r.get("error")]
    result = {
        "status": ccs.STATUS_SMOKE_PASSED if not failures else ccs.STATUS_FAILED,
        "stage": "smoke", "pairs": len(pairs), "trajectories": trajectories,
        "workers_requested": int(workers),
        "workers_used": min(int(workers), len(pairs)),
        "execution_mode": "hpc_override" if workers > ccs.MAX_WORKERS else "reviewed_local",
        "declared_smoke_budget": ccs.SMOKE_TRAJECTORIES,
        "warm_vectors_exercised": sorted({p["candidate_id"] for p in pairs}),
        "seeds": [p["seed"] for p in pairs],
        "checks": [{k: v for k, v in r.items() if k != "observed_stocks"} for r in rows],
        "failures": failures,
        "mean_seconds_per_trajectory": float(np.mean(per_trajectory)) if per_trajectory else None,
        "elapsed_seconds": time.time() - started,
        "prepared_dir": str(prepared_dir), "prepared_sha256": prepared["prepared_sha256"],
        "module_sha256": module_hashes(), "input_sha256": prepared["state"]["hashes"],
        "diagnosis_test_gate": gate, "environment": environment(), "generated_utc": now(),
        "meaning": "Equivalence of the observed harness run and the production run at identical "
                   "parameters and seeds. It says nothing about model fit.",
    }
    dump_json(output_dir / "SMOKE_RESULT.json", result)
    dump_json(output_dir / "observed_stocks.json",
              {str(r["pair_id"]): r.get("observed_stocks", {}) for r in rows})
    dump_json(output_dir / "manifest.json", {
        "design": prepared["contract"].design_name,
        "seed_comparison_group": prepared["contract"].comparison_group,
        "status": result["status"], "stage": "smoke", "trajectories": trajectories,
        "prepared_sha256": prepared["prepared_sha256"], "module_sha256": module_hashes(),
        "input_sha256": prepared["state"]["hashes"], "environment": environment(),
        "generated_utc": now(),
    })
    write_artifact_index(output_dir)
    return result


def diagnosis_test_gate() -> dict:
    """Existing diagnosis integration gate, with the known exception disclosed.

    The deterministic multiplier-one neutrality test STILL FAILS and its assertion
    is unchanged (tests/KNOWN_TEST_EXCEPTIONS.md E-2026-09-17-01).  This search
    runs only the stochastic enabled MSM path, and the paired smoke check is what
    establishes that path.  The suite is not claimed to be fully passing.
    """
    from run_joint_fit_comparison_20260917 import run_diagnosis_test_gate  # noqa: PLC0415

    result = run_diagnosis_test_gate()
    if not result["ok"]:
        raise ContractError(f"diagnosis integration test gate failed: {json.dumps(result)}")
    result["disclosure"] = (
        "The deterministic MSM multiplier-one neutrality test still fails "
        "(KNOWN_TEST_EXCEPTIONS E-2026-09-17-01). The suite is NOT fully passing. Every "
        "evaluation in this search is stochastic on the enabled MSM path."
    )
    return result


def load_smoke(smoke_dir: Path, prepared: dict) -> dict:
    smoke_dir = Path(smoke_dir)
    result = json.loads((smoke_dir / "SMOKE_RESULT.json").read_text())
    if result.get("status") != ccs.STATUS_SMOKE_PASSED:
        raise ContractError(f"smoke artifact status is {result.get('status')!r}, expected SMOKE_PASSED")
    verify_artifact_index(smoke_dir)
    if result["prepared_sha256"] != prepared["prepared_sha256"]:
        raise ContractError("the smoke artifact was produced from a different preparation")
    current = module_hashes()
    differences = sorted(n for n in set(result["module_sha256"]) | set(current)
                         if result["module_sha256"].get(n) != current.get(n))
    if differences:
        raise ContractError(f"code changed since the smoke check: {differences}")
    if result["trajectories"] != ccs.SMOKE_TRAJECTORIES:
        raise ContractError(f"smoke artifact records {result['trajectories']} trajectories")
    return result


# ==========================================================================
# Evaluation engine shared by the search and the confirmation
# ==========================================================================

class StageFailure(ContractError):
    """A simulator exception or invalid output.  Partial evidence is preserved."""


def _payload_for(proposal: dict, *, config: dict, seeds: Sequence[int], targets, state_dir=None,
                 save_final_state: bool = False,
                 extract_start_year: int = ccs.EXTRACT_START_YEAR,
                 extract_end_year: int = ccs.EXTRACT_END_YEAR) -> dict:
    return {
        "candidate_id": int(proposal["candidate_id"]), "batch": int(proposal["batch"]),
        "origin": proposal["origin"], "config": config,
        "params": {k: float(v) for k, v in proposal["params"].items()},
        "replicate_seeds": [int(s) for s in seeds],
        "target_columns": [t.model_output for t in targets],
        "target_years": [t.year for t in targets],
        "save_final_state": bool(save_final_state),
        "state_dir": str(state_dir) if state_dir is not None else "",
        "extract_start_year": int(extract_start_year),
        "extract_end_year": int(extract_end_year),
    }


def evaluate_candidates(
    proposals: Sequence[dict],
    *,
    stage: str,
    output_dir: Path,
    base_config: dict,
    space_json: dict,
    targets,
    seeds: Sequence[int],
    budget: ccs.TrajectoryBudget,
    workers: int,
    completed: set[int],
    dispatched: set[int],
    worker: Callable[[dict], dict],
    executor_factory,
    clock: Callable[[], float],
    deadline: float | None,
    simulation_start_year: int = ccs.SIMULATION_START_YEAR,
    extract_start_year: int = ccs.EXTRACT_START_YEAR,
    extract_end_year: int = ccs.EXTRACT_END_YEAR,
    save_final_state: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """Dispatch one stage, journaling every launch BEFORE it happens.

    A launch attempt consumes budget even if its result is later lost, so a
    resumed run can never silently re-spend it.
    """
    journal_dir = Path(output_dir) / "journal"
    raw_dir = Path(output_dir) / "raw"
    state_dir = Path(output_dir) / "states"
    pending = [p for p in proposals
               if p["candidate_id"] not in completed and p["candidate_id"] not in dispatched]
    rows: list[dict] = []
    stopped_reason = ""
    started = clock()
    with executor_factory(max_workers=workers) as pool:
        running: dict = {}
        queue = list(pending)
        while queue or running:
            while queue and len(running) < workers and not stopped_reason:
                if deadline is not None and clock() >= deadline:
                    stopped_reason = (f"wall-time cap reached before dispatching "
                                      f"{len(queue)} remaining {stage} candidates")
                    print(f"coupled search: STOPPING dispatch: {stopped_reason}", flush=True)
                    break
                proposal = queue.pop(0)
                budget.reserve(len(seeds), stage)
                append_journal(journal_dir / "dispatch.jsonl", {
                    "candidate_id": proposal["candidate_id"], "batch": proposal["batch"],
                    "stage": stage, "origin": proposal["origin"],
                    "params_sha256": ccs.vector_identity(proposal["params"]),
                    "replicate_seeds": list(seeds), "trajectories": len(seeds),
                    "launched_utc": now(),
                })
                dispatched.add(proposal["candidate_id"])
                config = injected_config(
                    base_config, proposal["params"], space_json,
                    simulation_start_year=simulation_start_year,
                )
                proposal = {**proposal, "injected_config_sha256": ccs.sha256_json(config)}
                payload = _payload_for(proposal, config=config, seeds=seeds, targets=targets,
                                       state_dir=state_dir, save_final_state=save_final_state,
                                       extract_start_year=extract_start_year,
                                       extract_end_year=extract_end_year)
                running[pool.submit(worker, payload)] = proposal
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                proposal = running.pop(future)
                row = future.result()
                budget.finished += len(seeds)
                if row.get("error"):
                    budget.failed += len(seeds)
                    append_journal(journal_dir / "results.jsonl", {
                        "candidate_id": row["candidate_id"], "batch": row["batch"],
                        "stage": stage, "error": row["error"], "recorded_utc": now(),
                    })
                    raise StageFailure(
                        f"candidate {row['candidate_id']} failed: {row['error']}. Partial evidence "
                        f"is preserved in {output_dir}; no other candidate is substituted."
                    )
                reported = row.get("config_sha256")
                if reported and reported != proposal["injected_config_sha256"]:
                    raise StageFailure(
                        f"candidate {row['candidate_id']} ran a config whose hash ({reported}) "
                        f"differs from the one dispatched "
                        f"({proposal['injected_config_sha256']}). Partial evidence is preserved."
                    )
                raw_path = raw_dir / f"candidate_{row['candidate_id']}.json"
                dump_json(raw_path, {
                    "candidate_id": row["candidate_id"], "batch": row["batch"],
                    "stage": stage, "origin": row["origin"],
                    "provenance": proposal.get("provenance", {}), "params": proposal["params"],
                    "params_sha256": ccs.vector_identity(proposal["params"]),
                    "injected_config_sha256": proposal["injected_config_sha256"],
                    "replicate_seeds": row["replicate_seeds"],
                    "target_order": [t.key for t in targets],
                    "target_matrix": row["target_matrix"], "means": row["means"],
                    "sample_variances": row["sample_variances"],
                    "variance_estimator": "unbiased sample variance, ddof=1, over the K replicates",
                    "per_seed": row["per_seed"], "wall_seconds": row["wall_seconds"],
                })
                append_journal(journal_dir / "results.jsonl", {
                    "candidate_id": row["candidate_id"], "batch": row["batch"], "stage": stage,
                    "origin": row["origin"], "means": row["means"],
                    "params_sha256": ccs.vector_identity(proposal["params"]),
                    "injected_config_sha256": proposal["injected_config_sha256"],
                    "sample_variances": row["sample_variances"],
                    "replicate_seeds": row["replicate_seeds"],
                    "raw_file": str(raw_path.relative_to(output_dir)),
                    "raw_sha256": ccs.sha256_file(raw_path),
                    "wall_seconds": row["wall_seconds"], "error": "", "recorded_utc": now(),
                })
                completed.add(row["candidate_id"])
                rows.append(row)
                if on_result is not None:
                    on_result(row)
                print(f"coupled search [{stage}]: {len(completed)} candidates complete "
                      f"({budget.launched}/{budget.cap} trajectories launched)", flush=True)
    return {"rows": rows, "stopped_reason": stopped_reason, "elapsed_seconds": clock() - started}


# ==========================================================================
# Stage: search
# ==========================================================================

def _restore_archive(output_dir: Path, space, targets, replicates: int,
                     proposals_by_id: dict) -> tuple[ccs.Archive, set[int], set[int], int]:
    """Replay the journals.  Dispatched-without-result candidates are LOST, not retried."""
    archive = ccs.Archive(space)
    results = read_journal(Path(output_dir) / "journal/results.jsonl")
    dispatch = read_journal(Path(output_dir) / "journal/dispatch.jsonl")
    dispatched = {int(r["candidate_id"]) for r in dispatch}
    launched = sum(int(r["trajectories"]) for r in dispatch)
    completed: set[int] = set()
    for record in results:
        cid = int(record["candidate_id"])
        if record.get("error"):
            raise ContractError(
                f"the journal records a failed candidate {cid}: {record['error']}. A failed "
                f"search is not resumed; its evidence is preserved and reported."
            )
        raw_path = Path(output_dir) / record["raw_file"]
        if not raw_path.is_file() or ccs.sha256_file(raw_path) != record["raw_sha256"]:
            continue  # torn write: the launch stays spent, the result is not trusted
        proposal = proposals_by_id[cid]
        archive.add(ccs.build_record(
            candidate_id=cid, batch=int(record["batch"]), origin=record["origin"],
            provenance=proposal.get("provenance", {}), params=proposal["params"], space=space,
            replicate_seeds=record["replicate_seeds"], means=record["means"],
            sample_variances=record["sample_variances"], targets=targets, replicates=replicates,
        ))
        completed.add(cid)
    for cid in sorted(dispatched):
        archive.register_known(cid, space.normalise(proposals_by_id[cid]["params"]),
                               identity=ccs.vector_identity(proposals_by_id[cid]["params"]))
    return archive, completed, dispatched, launched


def run_search(
    prepared_dir: Path,
    smoke_dir: Path,
    output_dir: Path,
    *,
    workers: int = ccs.DEFAULT_WORKERS,
    resume: bool = False,
    worker: Callable[[dict], dict] = evaluate_candidate_worker,
    executor_factory=ProcessPoolExecutor,
    clock: Callable[[], float] = time.time,
    wall_time_cap: float = ccs.WALL_TIME_CAP_SECONDS,
    n_batches: int = ccs.N_BATCHES,
) -> dict:
    output_dir = Path(output_dir)
    if resume:
        context = json.loads((output_dir / "search_context.json").read_text())
        manifest = json.loads((output_dir / "manifest.json").read_text())
        if manifest.get("status") in (ccs.STATUS_SEARCH_COMPLETE, ccs.STATUS_CONFIRMATION_COMPLETE):
            raise ContractError(
                f"{output_dir} is already {manifest['status']}; --resume must not overwrite a "
                f"completed artifact."
            )
        prepared_dir = Path(context["prepared_dir"])
        smoke_dir = Path(context["smoke_dir"])
    prepared = load_prepared(prepared_dir)
    smoke_result = load_smoke(smoke_dir, prepared)
    if resume:
        if context["prepared_sha256"] != prepared["prepared_sha256"]:
            raise ContractError("the partial search was produced from a different preparation")
        if context["smoke_sha256"] != ccs.sha256_file(Path(smoke_dir) / "SMOKE_RESULT.json"):
            raise ContractError("the partial search was produced from a different smoke artifact")
        current = module_hashes()
        differences = sorted(n for n in set(context["module_sha256"]) | set(current)
                             if context["module_sha256"].get(n) != current.get(n))
        if differences:
            raise ContractError(f"code changed since the partial search: {differences}")
    else:
        assert_unoccupied(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    state = prepared["state"]
    plan = prepared["contract"]
    discovery_replicates = len(plan.discovery_seeds)
    space = state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    targets = state["targets"]
    initial = [dict(entry) for entry in prepared["initial"]]

    if not resume:
        dump_json(output_dir / "search_context.json", {
            "prepared_dir": str(Path(prepared_dir).resolve()),
            "prepared_sha256": prepared["prepared_sha256"],
            "smoke_dir": str(Path(smoke_dir).resolve()),
            "smoke_sha256": ccs.sha256_file(Path(smoke_dir) / "SMOKE_RESULT.json"),
            "module_sha256": module_hashes(), "input_sha256": state["hashes"],
            "n_batches": int(n_batches), "workers": int(workers),
            "execution_mode": (
                "hpc_override" if workers > ccs.MAX_WORKERS else "reviewed_local"
            ),
            "wall_time_cap_seconds": float(wall_time_cap),
            "environment": environment(), "started_utc": now(),
        })
        dump_json(output_dir / "manifest.json", {"design": plan.design_name,
                                                 "seed_comparison_group": plan.comparison_group,
                                                 "status": "RUNNING", "stage": "search",
                                                 "started_utc": now()})

    recorded_proposals = read_journal(output_dir / "journal/proposals.jsonl")
    proposals_by_id = {int(entry["candidate_id"]): entry for entry in recorded_proposals}
    for entry in initial:
        proposals_by_id.setdefault(int(entry["candidate_id"]), entry)
    archive, completed, dispatched, launched = _restore_archive(
        output_dir, space, targets, discovery_replicates, proposals_by_id)
    planned_vectors = len(initial) + int(n_batches) * ccs.BATCH_SIZE
    if planned_vectors > ccs.MAX_DISCOVERY_VECTORS:
        raise ContractError(
            f"{planned_vectors} discovery vectors exceed the declared maximum "
            f"{ccs.MAX_DISCOVERY_VECTORS}")
    budget = ccs.TrajectoryBudget(cap=planned_vectors * discovery_replicates)
    if prepared["manifest"].get("engineering_reduced_archive"):
        print("coupled search: WARNING -- this preparation uses a REDUCED initial archive "
              f"({len(initial)} of the declared {ccs.INITIAL_ARCHIVE_SIZE}). It is an "
              "engineering run, not the declared design.", flush=True)
    budget.launched = launched
    budget.lost = sum(discovery_replicates for cid in dispatched if cid not in completed)

    generator = ccs.ProposalGenerator(space, plan.proposal_rng_seed)
    checkpoint_path = output_dir / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {
        "rng_after_batch": {}, "batches_generated": [], "pool_history": {},
        "proposal_diagnostics": {},
    }
    generated = [int(b) for b in checkpoint["batches_generated"]]
    if generated:
        generator.set_state(checkpoint["rng_after_batch"][str(max(generated))])
    else:
        generator.set_state(prepared["manifest"]["proposal_rng"]["state_after_initialisation"])

    check = injection_checker(
        state["base_config"], space_json,
        simulation_start_year=plan.simulation_start_year,
    )
    per_trajectory = smoke_result.get("mean_seconds_per_trajectory")
    if per_trajectory:
        estimated = ccs.DISCOVERY_TRAJECTORIES * per_trajectory / max(workers, 1)
        print(f"coupled search: ESTIMATE ONLY -- {per_trajectory:.1f} s/trajectory from the smoke "
              f"check, {workers} workers, {ccs.DISCOVERY_TRAJECTORIES} trajectories "
              f"=> about {estimated/3600:.2f} h and {3600*workers/per_trajectory:.0f} "
              f"trajectories/h. Smoke timings are a small sample; treat this as an estimate.",
              flush=True)

    started = clock()
    deadline = started + float(wall_time_cap)
    status = ccs.STATUS_SEARCH_COMPLETE
    stopped_reason = ""

    def save_checkpoint() -> None:
        dump_json(checkpoint_path, {
            **checkpoint,
            "budget": budget.as_dict(), "completed": sorted(completed),
            "dispatched": sorted(dispatched),
            "lost_in_flight": sorted(cid for cid in dispatched if cid not in completed),
            "proposal_rng_state": generator.get_state(), "updated_utc": now(),
        })

    stages: list[tuple[str, list[dict]]] = [("initial", initial)]
    for batch in range(1, int(n_batches) + 1):
        stages.append((f"batch_{batch:02d}", []))

    failure = ""
    for index, (stage, proposals) in enumerate(stages):
        if stopped_reason or failure:
            break
        batch = index  # 0 for the initial archive
        if batch > 0:
            recorded = [p for p in recorded_proposals if int(p["batch"]) == batch]
            if recorded:
                proposals = recorded
            else:
                if clock() >= deadline:
                    stopped_reason = f"wall-time cap reached before generating {stage}"
                    break
                pool = ccs.select_parent_pool(archive.ordered())
                first_id = len(initial) + (batch - 1) * ccs.BATCH_SIZE
                generated_proposals, diagnostics = generator.propose_batch(
                    batch, pool, archive, first_candidate_id=first_id, injection_check=check)
                proposals = []
                for proposal in generated_proposals:
                    entry = {"candidate_id": proposal.candidate_id, "batch": batch,
                             "origin": proposal.origin, "provenance": proposal.provenance,
                             "params": proposal.params,
                             "params_sha256": proposal.params_sha256}
                    append_journal(output_dir / "journal/proposals.jsonl", entry)
                    proposals_by_id[proposal.candidate_id] = entry
                    proposals.append(entry)
                checkpoint["pool_history"][str(batch)] = pool.as_rows()
                checkpoint["proposal_diagnostics"][str(batch)] = ccs.json_safe(diagnostics)
                checkpoint["batches_generated"] = sorted(set(generated) | {batch})
                generated = checkpoint["batches_generated"]
                checkpoint["rng_after_batch"][str(batch)] = generator.get_state()
                save_checkpoint()
        for proposal in proposals:
            if proposal["candidate_id"] not in dispatched:
                archive.register_known(proposal["candidate_id"],
                                       space.normalise(proposal["params"]),
                                       identity=ccs.vector_identity(proposal["params"]))
        try:
            outcome = evaluate_candidates(
                proposals, stage=stage, output_dir=output_dir, base_config=state["base_config"],
                space_json=space_json, targets=targets, seeds=plan.discovery_seeds,
                budget=budget, workers=workers, completed=completed, dispatched=dispatched,
                worker=worker, executor_factory=executor_factory, clock=clock, deadline=deadline,
                simulation_start_year=plan.simulation_start_year,
                extract_start_year=plan.extract_start_year,
                extract_end_year=plan.extract_end_year,
                on_result=lambda row: archive.add(ccs.build_record(
                    candidate_id=row["candidate_id"], batch=row["batch"], origin=row["origin"],
                    provenance=proposals_by_id[row["candidate_id"]].get("provenance", {}),
                    params=proposals_by_id[row["candidate_id"]]["params"], space=space,
                    replicate_seeds=row["replicate_seeds"], means=row["means"],
                    sample_variances=row["sample_variances"], targets=targets,
                    replicates=discovery_replicates,
                )),
            )
        except StageFailure as exc:
            failure = str(exc)
            outcome = {"stopped_reason": ""}
            print(f"coupled search: FAILED during {stage}: {failure}", flush=True)
        budget.lost = sum(discovery_replicates for cid in dispatched if cid not in completed)
        save_checkpoint()
        if outcome["stopped_reason"]:
            stopped_reason = outcome["stopped_reason"]
            status = ccs.STATUS_PARTIAL_TIME_LIMIT

    if failure:
        status = ccs.STATUS_FAILED
    elif stopped_reason:
        status = ccs.STATUS_PARTIAL_TIME_LIMIT
    write_search_outputs(output_dir, archive, targets, status=status,
                         stopped_reason=stopped_reason or failure, budget=budget,
                         prepared=prepared, smoke_result=smoke_result,
                         elapsed_seconds=clock() - started, checkpoint=checkpoint,
                         n_batches=int(n_batches), failure=failure)
    return {"status": status, "stopped_reason": stopped_reason, "failure": failure,
            "budget": budget.as_dict(), "n_candidates": len(archive)}


def write_search_outputs(output_dir: Path, archive: ccs.Archive, targets, *, status: str,
                         stopped_reason: str, budget: ccs.TrajectoryBudget, prepared: dict,
                         smoke_result: dict, elapsed_seconds: float, checkpoint: dict,
                         n_batches: int, failure: str = "") -> None:
    output_dir = Path(output_dir)
    plan = prepared["contract"]
    discovery_replicates = len(plan.discovery_seeds)
    records = archive.ordered()
    summary = pd.DataFrame([r.summary_row() for r in records])
    if not summary.empty:
        summary = summary.sort_values(["search_j", "search_mean_group_rms", "candidate_id"])
        summary.insert(0, "search_rank", range(1, len(summary) + 1))
    summary.to_csv(output_dir / "archive_summary.csv", index=False)
    _write_activity_and_boundary_diagnostics(output_dir, records, prepared)

    residual_rows = []
    implausibility_rows = []
    for record in records:
        for i, target in enumerate(targets):
            residual_rows.append({
                "candidate_id": record.candidate_id, "target_key": target.key,
                "group": target.group, "observed_z": target.z,
                "replicate_mean": float(record.means[i]),
                "sample_variance_ddof1": float(record.sample_variances[i]),
                "frozen_non_process_variance_B": target.b_variance,
                "signed_search_residual": float(record.residuals[i]),
            })
            implausibility_rows.append({
                "candidate_id": record.candidate_id, "target_key": target.key,
                "primary_implausibility": record.primary.implausibility[i],
                "primary_total_variance": record.primary.total_variance[i],
                "sensitivity_implausibility": record.sensitivity.implausibility[i],
                "primary_target_failed": record.primary.implausibility[i] > ccs.IMPLAUSIBILITY_CUTOFF,
            })
    pd.DataFrame(residual_rows).to_csv(output_dir / "signed_search_residuals.csv", index=False)
    pd.DataFrame(implausibility_rows).to_csv(output_dir / "per_target_implausibility.csv", index=False)
    pd.DataFrame([t.as_row() for t in targets]).to_csv(output_dir / "frozen_targets.csv", index=False)
    dump_json(output_dir / "proposal_provenance.json", {
        "pool_history": checkpoint.get("pool_history", {}),
        "proposal_diagnostics": checkpoint.get("proposal_diagnostics", {}),
        "initial_archive_diagnostics": prepared["initial_diagnostics"],
    })

    crosscheck = {"status": "not_run", "reason": "no candidate evaluated"}
    if records:
        table = ccs.repository_acceptance_check(
            {r.candidate_id: r.means for r in records},
            {r.candidate_id: r.sample_variances for r in records},
            targets, register_path=prepared["state"]["paths"]["target_register"],
            replicates=discovery_replicates)
        harness = pd.DataFrame([{"candidate_id": r.candidate_id,
                                 "harness_i_max": r.primary.i_max,
                                 "harness_binding_target": r.primary.binding_target,
                                 "harness_primary_pass": r.primary_pass} for r in records])
        merged = table.merge(harness, on="candidate_id")
        merged["i_max_absolute_difference"] = (
            merged["repository_i_max"] - merged["harness_i_max"]).abs()
        merged.to_csv(output_dir / "repository_acceptance_crosscheck.csv", index=False)
        agree = bool((merged["repository_primary_pass"] == merged["harness_primary_pass"]).all()
                     and float(merged["i_max_absolute_difference"].max()) < 1e-9)
        crosscheck = {"status": "agrees" if agree else "DISAGREES",
                      "max_absolute_i_max_difference": float(merged["i_max_absolute_difference"].max()),
                      "n_candidates": int(len(merged))}
        if not agree:
            raise ContractError(f"harness acceptance disagrees with repository scoring: {crosscheck}")

    passers = [r for r in records if r.primary_pass]
    sensitivity_only = [r for r in records if r.sensitivity_pass and not r.primary_pass]
    manifest = {
        "design": plan.design_name,
        "seed_comparison_group": plan.comparison_group,
        "status": status, "stage": "search", "stopped_reason": stopped_reason,
        "n_candidates_evaluated": len(records), "n_batches_declared": n_batches,
        "budget": budget.as_dict(), "elapsed_seconds": elapsed_seconds,
        "n_discovery_primary_pass": len(passers),
        "n_sensitivity_only": len(sensitivity_only),
        "best_search_j": float(min((r.j for r in records), default=float("nan"))),
        "prepared_sha256": prepared["prepared_sha256"],
        "smoke_sha256": json.loads((output_dir / "search_context.json").read_text())["smoke_sha256"],
        "input_sha256": prepared["state"]["hashes"], "module_sha256": module_hashes(),
        "environment": environment(), "generated_utc": now(),
        "repository_acceptance_crosscheck": crosscheck,
        "limitations": [
            "A low J is not acceptance. J is a search ranking statistic only.",
            "Equal treatment of the four reporting groups is a declared search heuristic.",
            "Discovery passes are scored on the discovery CRN seeds; only fresh-seed "
            "confirmation tests stability.",
            "This is a bounded first attempt, not exhaustive coverage of 33 dimensions, and "
            "no result here demonstrates global impossibility.",
        ],
    }
    if failure:
        manifest["failure"] = failure
        manifest["failure_warning"] = (
            "FAILED. A simulator exception or invalid output stopped this run. The evidence "
            "collected before the failure is preserved; no candidate was substituted and no "
            "shortlist or final report is written."
        )
    if status == ccs.STATUS_PARTIAL_TIME_LIMIT:
        manifest["partial_run_warning"] = (
            "PARTIAL. This run did not complete the declared batches and CANNOT be reported as "
            "completed negative evidence. No shortlist is frozen and no final report is written."
        )
    dump_json(output_dir / "manifest.json", manifest)

    if status == ccs.STATUS_SEARCH_COMPLETE and records:
        shortlist = ccs.freeze_shortlist(records)
        by_id = {r.candidate_id: r for r in records}
        dump_json(output_dir / "shortlist.json", {
            "frozen_utc": now(), "size": len(shortlist),
            "rule": "up to five discovery primary passes ranked by J, then filled to ten with the "
                    "remaining lowest-J candidates; deduplicated and frozen before any fresh "
                    "simulation. There is no requirement to find ten passers.",
            "entries": [{**entry,
                         "params": by_id[entry["candidate_id"]].params,
                         "discovery_primary_pass": by_id[entry["candidate_id"]].primary_pass,
                         "discovery_primary_i_max": by_id[entry["candidate_id"]].primary.i_max,
                         "discovery_sensitivity_pass": by_id[entry["candidate_id"]].sensitivity_pass}
                        for entry in shortlist],
        })
        write_search_report(output_dir, records, targets, manifest)
    write_artifact_index(output_dir)


def _write_activity_and_boundary_diagnostics(output_dir: Path, records, prepared: dict) -> None:
    """Write non-scoring diagnostics for a later MSM activity-interaction decision."""
    ranked = sorted(records, key=ccs.ranking_key)
    space = prepared["state"]["space"]
    bounds = {name: (float(space.lower[i]), float(space.upper[i]))
              for i, name in enumerate(space.names)}
    boundary_rows = []
    for rank, record in enumerate(ranked, start=1):
        for name in ("orient_female", "orient_msm"):
            value = float(record.params[name])
            lower, upper = bounds[name]
            width = upper - lower
            boundary_rows.append({
                "search_rank": rank, "candidate_id": record.candidate_id,
                "search_j": record.j, "primary_i_max": record.primary.i_max,
                "parameter": name, "value": value, "lower": lower, "upper": upper,
                "fraction_from_lower": (value - lower) / width,
                "fraction_from_upper": (upper - value) / width,
                "within_5pct_of_bound": min(value - lower, upper - value) <= 0.05 * width,
            })
    pd.DataFrame(boundary_rows).to_csv(
        output_dir / "orientation_boundary_diagnostics.csv", index=False)

    infection_rows = []
    stock_rows = []
    for rank, record in enumerate(ranked[:20], start=1):
        raw = _raw_record(output_dir, record.candidate_id)
        for replicate in raw["per_seed"]:
            seed = int(replicate["seed"])
            for year, flow in replicate.get("observed_activity_flows", {}).items():
                values = np.asarray(flow["new_infections_by_orientation_activity"], dtype=float)
                for oi, orientation in enumerate(flow["orientations"]):
                    for ai, activity in enumerate(flow["activities"]):
                        infection_rows.append({
                            "search_rank": rank, "candidate_id": record.candidate_id,
                            "seed": seed, "year": int(year), "orientation": orientation,
                            "activity": activity, "new_infections": float(values[oi, ai]),
                        })
            for year, stock in replicate.get("observed_stocks", {}).items():
                values = np.asarray(
                    stock["orientation_activity_hiv_diagnosis_art"], dtype=float)
                susceptible = stock["hiv_stages"].index("Susceptible")
                undiagnosed = stock["diagnosis_states"].index("Undiagnosed")
                for oi, orientation in enumerate(stock["orientations"]):
                    for ai, activity in enumerate(stock["activities"]):
                        cell = values[oi, ai]
                        adult = float(cell.sum())
                        plhiv = float(adult - cell[susceptible].sum())
                        undiagnosed_plhiv = float(
                            cell[:, undiagnosed, :].sum()
                            - cell[susceptible, undiagnosed, :].sum()
                        )
                        stock_rows.append({
                            "search_rank": rank, "candidate_id": record.candidate_id,
                            "seed": seed, "year": int(year), "orientation": orientation,
                            "activity": activity, "adult_stock": adult, "plhiv": plhiv,
                            "hiv_prevalence": plhiv / adult if adult > 0 else np.nan,
                            "undiagnosed_plhiv": undiagnosed_plhiv,
                        })
    pd.DataFrame(infection_rows).to_csv(
        output_dir / "activity_infections_top20.csv", index=False)
    pd.DataFrame(stock_rows).to_csv(output_dir / "activity_stocks_top20.csv", index=False)


def write_search_report(output_dir: Path, records, targets, manifest: dict) -> None:
    ranked = sorted(records, key=ccs.ranking_key)
    lines = [
        "# Coupled calibration search -- discovery result", "",
        f"Status: `{manifest['status']}`. Candidates evaluated: {manifest['n_candidates_evaluated']}. "
        f"Trajectories launched: {manifest['budget']['launched']} of {manifest['budget']['cap']}.", "",
        "`J` is a SEARCH RANKING statistic computed with a frozen non-process variance. It is not "
        "an acceptance rule, a likelihood or a posterior. Acceptance is the frozen 13-target "
        "primary maximum-implausibility rule with predictive process variance s^2(1+1/K), "
        "target-relative discrepancy 0.1 and cutoff 3.", "",
        f"Discovery primary passes: **{manifest['n_discovery_primary_pass']}**. "
        f"Sensitivity-only (0.2 discrepancy) candidates: {manifest['n_sensitivity_only']} "
        f"-- reported, never promoted.", "",
        "## Best twenty by search rank", "",
        "| Rank | Candidate | Origin | J | mean R_G | max abs r | Primary I_max | Binding | Primary pass |",
        "|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for rank, record in enumerate(ranked[:20], start=1):
        lines.append(
            f"| {rank} | {record.candidate_id} | {record.origin} | {record.j:.3f} | "
            f"{record.mean_r:.3f} | {record.max_abs_residual:.3f} | {record.primary.i_max:.3f} | "
            f"{record.primary.binding_target} | {'yes' if record.primary_pass else 'no'} |")
    lines += ["", "## Group statistics of the best candidate", ""]
    if ranked:
        best = ranked[0]
        lines += ["| Group | R_G |", "|---|---:|"]
        for group in ccs.GROUP_ORDER:
            lines.append(f"| {group} | {best.group_values[group]:.3f} |")
        lines += ["", "### Signed search residuals of the best candidate", "",
                  "| Target | Observed | Replicate mean | r (signed) |", "|---|---:|---:|---:|"]
        for i, target in enumerate(targets):
            lines.append(f"| {target.key} | {target.z:g} | {best.means[i]:.1f} | "
                         f"{best.residuals[i]:+.3f} |")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in manifest["limitations"]]
    atomic_write_text(Path(output_dir) / "SEARCH_REPORT.md", "\n".join(lines) + "\n")


# ==========================================================================
# Stage: confirmation
# ==========================================================================

def load_complete_search(source: Path) -> dict:
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text())
    status = manifest.get("status")
    if status != ccs.STATUS_SEARCH_COMPLETE:
        raise ContractError(
            f"{source} has status {status!r}. Only a COMPLETE search may be confirmed; a partial "
            f"run cannot be reported as completed evidence and is not a confirmation source."
        )
    verify_artifact_index(source)
    context = json.loads((source / "search_context.json").read_text())
    current = module_hashes()
    differences = sorted(n for n in set(context["module_sha256"]) | set(current)
                         if context["module_sha256"].get(n) != current.get(n))
    if differences:
        raise ContractError(f"code changed since the search: {differences}")
    shortlist = json.loads((source / "shortlist.json").read_text())
    return {"manifest": manifest, "context": context, "shortlist": shortlist, "source": source}


def _raw_record(source: Path, candidate_id: int) -> dict:
    return json.loads((Path(source) / "raw" / f"candidate_{int(candidate_id)}.json").read_text())


def _annual_frame(raw_records: Sequence[dict]) -> pd.DataFrame:
    """Replicate means and ddof=1 variances of EVERY extracted annual series."""
    rows = []
    for raw in raw_records:
        row = {"design_id": int(raw["candidate_id"])}
        per_seed = raw["per_seed"]
        names = sorted(per_seed[0]["annual_outputs"])
        for name in names:
            years = sorted(int(y) for y in per_seed[0]["annual_outputs"][name])
            for year in years:
                values = np.array([float(record["annual_outputs"][name][str(year)])
                                   if str(year) in record["annual_outputs"][name]
                                   else float(record["annual_outputs"][name][year])
                                   for record in per_seed], dtype=float)
                key = f"{name}_{year}"
                row[key] = float(values.mean()) if np.isfinite(values).all() else np.nan
                row[f"mcvar__{key}"] = (float(values.var(ddof=1))
                                        if values.size >= 2 and np.isfinite(values).all() else 0.0)
        rows.append(row)
    return pd.DataFrame(rows)


def confirm(
    source: Path,
    output_dir: Path,
    *,
    workers: int = ccs.DEFAULT_WORKERS,
    worker: Callable[[dict], dict] = evaluate_candidate_worker,
    executor_factory=ProcessPoolExecutor,
    clock: Callable[[], float] = time.time,
) -> dict:
    search = load_complete_search(source)
    prepared = load_prepared(Path(search["context"]["prepared_dir"]))
    plan = prepared["contract"]
    discovery_replicates = len(plan.discovery_seeds)
    confirmation_replicates = len(plan.confirmation_seeds)
    state = prepared["state"]
    space = state["space"]
    space_json = json.loads(state["paths"]["calib_space"].read_text())
    targets = state["targets"]
    gate = diagnosis_test_gate()

    output_dir = Path(output_dir)
    assert_unoccupied(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = search["shortlist"]["entries"]
    if len(entries) > ccs.SHORTLIST_SIZE:
        raise ContractError(f"frozen shortlist has {len(entries)} entries, above the declared cap")
    proposals = [{"candidate_id": int(e["candidate_id"]), "batch": -1,
                  "origin": e["selected_as"], "params": e["params"],
                  "provenance": {"frozen_shortlist_entry": e}} for e in entries]
    dump_json(output_dir / "frozen_shortlist.json", search["shortlist"])

    budget = ccs.TrajectoryBudget(cap=len(proposals) * confirmation_replicates)
    completed: set[int] = set()
    dispatched: set[int] = set()
    started = clock()
    evaluate_candidates(
        proposals, stage="confirmation", output_dir=output_dir,
        base_config=state["base_config"], space_json=space_json, targets=targets,
        seeds=plan.confirmation_seeds, budget=budget, workers=workers, completed=completed,
        dispatched=dispatched, worker=worker, executor_factory=executor_factory, clock=clock,
        deadline=None, simulation_start_year=plan.simulation_start_year,
        extract_start_year=plan.extract_start_year,
        extract_end_year=plan.extract_end_year, save_final_state=True,
    )
    raw_records = [_raw_record(output_dir, p["candidate_id"]) for p in proposals]
    discovery_raw = {p["candidate_id"]: _raw_record(search["source"], p["candidate_id"])
                     for p in proposals}
    confirmed = []
    discovery_records = {}
    for proposal, raw in zip(proposals, raw_records):
        if sorted(raw["replicate_seeds"]) != sorted(plan.confirmation_seeds):
            raise ContractError(f"candidate {proposal['candidate_id']} did not use the fresh seeds")
        discovery = discovery_raw[proposal["candidate_id"]]
        discovery_records[proposal["candidate_id"]] = ccs.build_record(
            candidate_id=proposal["candidate_id"], batch=int(discovery["batch"]),
            origin=str(discovery["origin"]), provenance=discovery.get("provenance", {}),
            params=proposal["params"], space=space,
            replicate_seeds=discovery["replicate_seeds"], means=discovery["means"],
            sample_variances=discovery["sample_variances"], targets=targets,
            replicates=discovery_replicates)
        confirmed.append(ccs.build_record(
            candidate_id=proposal["candidate_id"], batch=-1, origin=proposal["origin"],
            provenance=proposal["provenance"], params=proposal["params"], space=space,
            replicate_seeds=raw["replicate_seeds"], means=raw["means"],
            sample_variances=raw["sample_variances"], targets=targets,
            replicates=confirmation_replicates))

    crosscheck = ccs.repository_acceptance_check(
        {r.candidate_id: r.means for r in confirmed},
        {r.candidate_id: r.sample_variances for r in confirmed},
        targets, register_path=state["paths"]["target_register"],
        replicates=confirmation_replicates)
    harness = pd.DataFrame([{"candidate_id": r.candidate_id, "harness_i_max": r.primary.i_max,
                             "harness_primary_pass": r.primary_pass} for r in confirmed])
    merged = crosscheck.merge(harness, on="candidate_id")
    merged["i_max_absolute_difference"] = (merged["repository_i_max"] - merged["harness_i_max"]).abs()
    merged.to_csv(output_dir / "repository_acceptance_crosscheck.csv", index=False)
    largest = float(merged["i_max_absolute_difference"].max())
    if not bool((merged["repository_primary_pass"] == merged["harness_primary_pass"]).all()) \
            or largest >= 1e-9:
        raise ContractError(
            f"confirmation acceptance disagrees with repository scoring "
            f"(largest |I_max| difference {largest:g})")

    paired_rows = []
    for record in confirmed:
        discovery = discovery_raw[record.candidate_id]
        for i, target in enumerate(targets):
            paired_rows.append({
                "candidate_id": record.candidate_id, "target_key": target.key,
                "group": target.group, "observed_z": target.z,
                "discovery_mean": float(discovery["means"][i]),
                "discovery_sample_variance": float(discovery["sample_variances"][i]),
                "confirmation_mean": float(record.means[i]),
                "confirmation_sample_variance": float(record.sample_variances[i]),
                "confirmation_signed_search_residual": float(record.residuals[i]),
                "confirmation_primary_implausibility": record.primary.implausibility[i],
                "confirmation_primary_target_failed":
                    record.primary.implausibility[i] > ccs.IMPLAUSIBILITY_CUTOFF,
            })
    pd.DataFrame(paired_rows).to_csv(output_dir / "paired_discovery_confirmation.csv", index=False)

    summary_rows = []
    for record in confirmed:
        discovery = discovery_records[record.candidate_id]
        summary_rows.append({
        **discovery.summary_row(),
        "shortlist_provenance": record.origin,
        "confirmation_j": record.j,
        "confirmation_mean_group_rms": record.mean_r,
        "confirmation_primary_pass": record.primary_pass,
        "confirmation_primary_i_max": record.primary.i_max,
        "confirmation_binding_target": record.primary.binding_target,
        "confirmation_failed_targets": ";".join(record.primary.failed_targets),
        "confirmation_sensitivity_pass": record.sensitivity_pass,
        "discovery_primary_pass_recorded": next(
            e["discovery_primary_pass"] for e in entries
            if int(e["candidate_id"]) == record.candidate_id),
        })
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "confirmation_summary.csv", index=False)

    stability, cross_validation = _confirmation_diagnostics(
        output_dir, raw_records, state, targets)
    passers = [r for r in confirmed if r.primary_pass]
    manifest = {
        "design": plan.design_name,
        "seed_comparison_group": plan.comparison_group,
        "status": ccs.STATUS_CONFIRMATION_COMPLETE, "stage": "confirmation",
        "n_candidates": len(confirmed), "n_confirmation_primary_pass": len(passers),
        "confirmed_primary_pass_ids": [r.candidate_id for r in passers],
        "budget": budget.as_dict(), "elapsed_seconds": clock() - started,
        "workers_requested": int(workers),
        "workers_used": min(int(workers), len(proposals)),
        "execution_mode": "hpc_override" if workers > ccs.MAX_WORKERS else "reviewed_local",
        "seeds": list(plan.confirmation_seeds), "replicates": confirmation_replicates,
        "source_search": str(Path(source).resolve()),
        "source_search_manifest_sha256": ccs.sha256_file(Path(source) / "manifest.json"),
        "prepared_sha256": prepared["prepared_sha256"], "input_sha256": state["hashes"],
        "module_sha256": module_hashes(), "diagnosis_test_gate": gate,
        "variance_stability": stability, "cross_validation": cross_validation,
        "environment": environment(), "generated_utc": now(),
        "limitations": [
            "These candidates are a frozen shortlist, not a representative uncertainty ensemble "
            "and not a ready HTA baseline.",
            "Confirmation replicates are scored separately from discovery; seeds are never pooled "
            "and no favourable subset is selected.",
            "The AIDS observation proxy remains unvalidated; a good numerical fit cannot validate "
            "its definition.",
            "Saved 2025 tensors are diagnostic states with restart_safe=false, not restart "
            "checkpoints.",
        ],
    }
    dump_json(output_dir / "manifest.json", manifest)
    write_decision_report(output_dir, confirmed, targets, manifest, search)
    write_artifact_index(output_dir)
    return manifest


def _confirmation_diagnostics(output_dir: Path, raw_records, state, targets) -> tuple[dict, dict]:
    """Predictive-variance stability and cross-validation, through existing code."""
    import hm_diagnostics as hd  # noqa: PLC0415
    import hm_target_register as tr  # noqa: PLC0415
    import run_history_matching as rhm  # noqa: PLC0415

    frame = _annual_frame(raw_records)
    register = tr.load_register(state["paths"]["target_register"])
    ordered = sorted([t for t in register.calibration_targets()
                      if t.key in set(ccs.FROZEN_TARGET_ORDER)],
                     key=lambda t: ccs.FROZEN_TARGET_ORDER.index(t.key))
    core = rhm._score_matrix(
        frame, ordered, register, disc_multiplier=ccs.PRIMARY_DISCREPANCY,
        disc_method=ccs.DISCREPANCY_METHOD, v_ensemble=None,
        ensemble_mode=rhm.PREDICTIVE_LOCAL, replicates=ccs.CONFIRMATION_REPLICATES)
    raw_stability = rhm.predictive_variance_stability(
        core, ccs.CONFIRMATION_REPLICATES, cutoff=ccs.IMPLAUSIBILITY_CUTOFF)
    unstable = np.flatnonzero(raw_stability["unstable_mask"])
    stability = {k: v for k, v in raw_stability.items() if k != "unstable_mask"}
    stability["candidate_ids_variance_unstable"] = [int(frame["design_id"].iloc[i])
                                                    for i in unstable]
    stability["note"] = ("Diagnostic only: it is reported, never used to change the primary "
                         "accepted set.")
    try:
        cv = rhm.score_cross_validation(
            frame, register, disc_multiplier=ccs.PRIMARY_DISCREPANCY,
            disc_method=ccs.DISCREPANCY_METHOD, v_ensemble=None,
            ensemble_mode=rhm.PREDICTIVE_LOCAL, replicates=ccs.CONFIRMATION_REPLICATES)
        frames = []
        if not cv["per_target"].empty:
            regular = cv["per_target"].copy()
            regular.insert(0, "holdout_type", "register_cross_validation")
            frames.append(regular)
        unresolved = list(cv["unresolved"])

        # A wave may hold out selected years from a series that is otherwise primary.
        # This preserves one source series and one model-output mapping while ensuring
        # the declared historical years never enter acceptance.
        wave_plan = json.loads(state["paths"]["wave_plan"].read_text())
        holdout_years = wave_plan.get("cross_validation_years")
        if holdout_years:
            held_out = register.restrict_years(register.calibration_targets(), holdout_years)
            held_out = [t for t in held_out if t.key not in set(ccs.FROZEN_TARGET_ORDER)]
            holdout_core = rhm._score_matrix(
                frame, held_out, register,
                disc_multiplier=ccs.PRIMARY_DISCREPANCY,
                disc_method=ccs.DISCREPANCY_METHOD, v_ensemble=None,
                ensemble_mode=rhm.PREDICTIVE_LOCAL,
                replicates=ccs.CONFIRMATION_REPLICATES)
            unresolved.extend(holdout_core["unresolved"])
            if holdout_core["resolved"]:
                held = hd.per_target_summary(
                    holdout_core["matrix"], holdout_core["resolved"],
                    holdout_core["budgets"])
                held.insert(0, "holdout_type", "declared_year_holdout")
                frames.append(held)

        per_target = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not per_target.empty:
            per_target.insert(0, "note", "cross-validation only; never enters acceptance")
            per_target.to_csv(output_dir / "cross_validation_per_target.csv", index=False)
        cross_validation = {
            "status": "scored", "n_targets": int(len(per_target)),
            "declared_year_holdouts": ccs.json_safe(holdout_years or {}),
            "unresolved": ccs.json_safe(unresolved),
            "note": "Cross-validation comparators are reported, never added to or removed from "
                    "the frozen 13-target acceptance rule.",
        }
    except Exception as exc:  # noqa: BLE001 - a missing comparator is disclosed, not hidden
        cross_validation = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    return ccs.json_safe(stability), cross_validation


def write_decision_report(output_dir: Path, confirmed, targets, manifest: dict, search: dict) -> None:
    ranked = sorted(confirmed, key=ccs.ranking_key)
    passers = [r for r in ranked if r.primary_pass]
    search_manifest = search["manifest"]
    if passers:
        outcome = (f"**{len(passers)} shortlisted candidate(s) passed the primary 13-target rule on "
                   f"fresh seeds.** Joint feasibility is demonstrated UNDER the stated AIDS "
                   f"observation proxy, the stated variance contract and this bounded budget. "
                   f"Cross-validation and HTA relevance are separate assessments that have not "
                   f"been made here.")
    elif search_manifest["n_discovery_primary_pass"] > 0 or search_manifest.get("best_search_j") is not None:
        outcome = ("**No shortlisted candidate was confirmed on fresh seeds within this budget.** "
                   "The best compromises and their repeated signed conflicts are reported below. "
                   "This does not establish global impossibility.")
    else:
        outcome = ("**No improvement was found and nothing was confirmed.** Reported with coverage, "
                   "invalid-proposal, fallback and boundary-hit diagnostics. This does not "
                   "establish global impossibility.")
    lines = [
        "# Coupled calibration search -- decision report", "",
        outcome, "",
        f"Search: `{search['source']}` ({search_manifest['n_candidates_evaluated']} candidates, "
        f"{search_manifest['budget']['launched']} discovery trajectories). "
        f"Confirmation: {manifest['n_candidates']} candidates x "
        f"{manifest['replicates']} fresh seeds "
        f"({manifest['budget']['launched']} trajectories).", "",
        "## Confirmation outcome per shortlisted candidate", "",
        "| Candidate | Shortlist provenance | Discovery pass | Confirmation I_max | Binding | "
        "Confirmation pass | Sensitivity (0.2) |",
        "|---:|---|---|---:|---|---|---|",
    ]
    discovery_pass = {int(e["candidate_id"]): e["discovery_primary_pass"]
                      for e in search["shortlist"]["entries"]}
    for record in ranked:
        lines.append(
            f"| {record.candidate_id} | {record.origin} | "
            f"{'yes' if discovery_pass[record.candidate_id] else 'no'} | "
            f"{record.primary.i_max:.3f} | {record.primary.binding_target} | "
            f"{'yes' if record.primary_pass else 'no'} | "
            f"{'yes' if record.sensitivity_pass else 'no'} |")
    lines += ["", "## Repeated signed conflicts", "",
              "Mean signed search residual across the shortlist, by target "
              "(positive = overprediction).", "",
              "| Target | Group | Observed | Mean confirmation residual | Sign agreement |",
              "|---|---|---:|---:|---:|"]
    for i, target in enumerate(targets):
        values = np.array([record.residuals[i] for record in ranked], dtype=float)
        agreement = float(max((values > 0).mean(), (values < 0).mean())) if values.size else float("nan")
        lines.append(f"| {target.key} | {target.group} | {target.z:g} | {values.mean():+.3f} | "
                     f"{agreement:.2f} |")
    lines += ["", "## Variance stability", "",
              f"```json\n{json.dumps(manifest['variance_stability'], indent=2)}\n```", "",
              "## Cross-validation", "",
              f"```json\n{json.dumps(manifest['cross_validation'], indent=2)}\n```", "",
              "## What happens next", "",
              "No second search is launched, no scientific bound is widened and no mechanism is "
              "added by this runner. A later comparator must name the remaining mismatch and "
              "distinguish observation ambiguity, constraint sensitivity, temporal transmission "
              "flexibility and initialisation changes. The unresolved AIDS mapping stays visible: "
              "a good numerical fit cannot validate its definition.", "",
              "## Limitations", ""] + [f"- {item}" for item in manifest["limitations"]]
    atomic_write_text(Path(output_dir) / "DECISION_REPORT.md", "\n".join(lines) + "\n")


# ==========================================================================
# CLI
# ==========================================================================

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true",
                      help="run every pre-launch check; build no engine and write nothing")
    mode.add_argument("--prepare", action="store_true", help="freeze the design; zero simulations")
    mode.add_argument("--smoke", action="store_true",
                      help="three paired production-versus-harness checks (6 trajectories)")
    mode.add_argument("--run-search", action="store_true", help="the declared discovery search")
    mode.add_argument("--confirm", action="store_true", help="fresh-seed confirmation")
    mode.add_argument("--resume", action="store_true", help="continue a matching partial search")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--prepared", type=Path, help="a PREPARED directory")
    parser.add_argument("--smoke-source", type=Path, help="a SMOKE_PASSED directory")
    parser.add_argument("--source", type=Path, help="a SEARCH_COMPLETE directory")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=ccs.DEFAULT_WORKERS)
    parser.add_argument(
        "--allow-hpc-workers",
        action="store_true",
        help=(
            "allow an execution-only process count above the reviewed local ceiling; "
            f"hard-capped at {ABSOLUTE_HPC_WORKER_CAP} and recorded in manifests"
        ),
    )
    return parser.parse_args(argv)


def _require(value, message: str):
    if value is None:
        raise SystemExit(f"error: {message}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    upper = ABSOLUTE_HPC_WORKER_CAP if args.allow_hpc_workers else ccs.MAX_WORKERS
    if not 1 <= args.workers <= upper:
        hint = " with --allow-hpc-workers" if not args.allow_hpc_workers else ""
        raise SystemExit(
            f"error: --workers must be between 1 and {upper}{hint}"
        )
    try:
        if not any((args.prepare, args.smoke, args.run_search, args.confirm, args.resume)):
            report = validate(plan_path=args.plan if Path(args.plan).exists() else None)
            printable = {k: v for k, v in report.items() if k != "state"}
            print(json.dumps(ccs.json_safe(printable), indent=2))
            print("\nVALIDATION " + ("PASSED" if report["ok"] else "FAILED")
                  + f"; 0 engines constructed, 0 simulations started, 0 files written. "
                  f"Declared maximum scheduled trajectories: {ccs.TOTAL_TRAJECTORY_BUDGET}.")
            return 0 if report["ok"] else 2
        if args.prepare:
            manifest = prepare(_require(args.output_dir, "--prepare needs --output-dir"),
                               plan_path=args.plan if Path(args.plan).exists() else None)
            print(json.dumps({k: manifest[k] for k in
                              ("status", "simulations_started", "initial_archive_size")}, indent=2))
            print(f"Prepared {args.output_dir}; 0 simulations started.")
            return 0
        if args.smoke:
            result = smoke(_require(args.prepared, "--smoke needs --prepared"),
                           _require(args.output_dir, "--smoke needs --output-dir"),
                           workers=args.workers)
            print(json.dumps({k: result[k] for k in
                              ("status", "pairs", "trajectories", "warm_vectors_exercised",
                               "mean_seconds_per_trajectory")}, indent=2))
            return 0 if result["status"] == ccs.STATUS_SMOKE_PASSED else 1
        if args.run_search or args.resume:
            output_dir = _require(args.output_dir, "this mode needs --output-dir")
            if args.run_search:
                _require(args.prepared, "--run-search needs --prepared")
                _require(args.smoke_source, "--run-search needs --smoke-source")
            result = run_search(args.prepared, args.smoke_source, output_dir,
                                workers=args.workers, resume=bool(args.resume))
            print(json.dumps(ccs.json_safe(result), indent=2))
            return 0 if result["status"] == ccs.STATUS_SEARCH_COMPLETE else 1
        if args.confirm:
            manifest = confirm(_require(args.source, "--confirm needs --source"),
                               _require(args.output_dir, "--confirm needs --output-dir"),
                               workers=args.workers)
            print(json.dumps({k: manifest[k] for k in
                              ("status", "n_candidates", "n_confirmation_primary_pass")}, indent=2))
            return 0
    except ContractError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
