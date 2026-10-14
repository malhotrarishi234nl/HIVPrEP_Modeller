#!/usr/bin/env python3
"""Bounded stochastic controlled comparison for the joint diagnoses/AIDS fit conflict (2026-09-17).

Design frozen in FINDINGS_REV1.md section 4 (hm_joint_fit_conflict_20260917_rev1):
  Part 1  anchors A=620, B=548 at their source parameters                         (2 cells x 10 seeds)
  Part 2  2^3 factorial at A: diagnosis_1990 up, aids_mortality down, seed_prevalence up,
          each by 25% of its declared range                                         (7 new cells x 10 seeds)
  90 simulations, common random-number seeds 195000-195009 shared by every cell.

Not a calibration wave, not an NROY update, no simulator or allocator change. Every cell is asserted
to run in STOCHASTIC mode on the ENABLED MSM relative-diagnosis path (the path with the known
deterministic-mode neutrality test failure is never switched within this experiment).

--validate-only runs every pre-launch check and starts no simulation.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "outputs/calibration/hm_focused_diagnoses_then_aids_20260916"
DEFAULT_OUTPUT = ROOT / "outputs/calibration/hm_joint_fit_comparison_20260917"
DESIGN_DOC = "outputs/calibration/hm_joint_fit_conflict_20260917_rev1/FINDINGS_REV1.md"

ANCHOR_A = 620
ANCHOR_B = 548
SEEDS = tuple(range(195000, 195010))
STEP_FRACTION = 0.25
FACTORS = (  # (label, parameter, direction)
    ("F1", "diagnosis_1990", +1),
    ("F2", "aids_mortality", -1),
    ("F3", "seed_prevalence", +1),
)
SIMULATION_START_YEAR = 1975
EXTRACT_START_YEAR = 1975
EXTRACT_END_YEAR = 2025
MAX_SIMULATIONS = 300
MAX_PROJECTED_HOURS = 2.0
MIN_JOBS_BEFORE_PROJECTION = 4
STOCK_COLUMNS = ("aids_undiagnosed_start", "aids_diagnosed_start", "aids_stock_start")

#: The ONLY test failure tolerated by the launch gate. Scoped to this stochastic experiment:
#: the assertion is preserved and still fails; the suite is NOT passing. See KNOWN_TEST_EXCEPTIONS.md.
DIAGNOSIS_TEST_FILES = ("tests/test_diagnosis_year_bands.py", "tests/test_msm_relative_diagnosis_v13_10.py")
TOLERATED_FAILURE = {
    "classname": "tests.test_msm_relative_diagnosis_v13_10.TestStepDiagnosisBehavior",
    "name": "test_enabled_with_multiplier_one_matches_baseline",
    "message_contains": "Multiplier=1.0 should produce identical MSM diagnoses",
}

#: Code-audited routes into/out of the UNDIAGNOSED AIDS compartment (for the Part 1 diagnostic balance).
UNDIAGNOSED_AIDS_ROUTES = {
    "in": ["Chronic->AIDS progression while undiagnosed (run_v3._step_disease_progression; counter aids_progression_while_undiagnosed)",
           "1975 epidemic seed stage distribution (initial stock only)"],
    "out": ["diagnosis while in AIDS stage (run_v3._step_diagnosis; counter hiv_diagnoses_in_aids_stage)",
            "HIV/AIDS mortality (run_v3 HIV mortality step; aids_deaths is NOT split by diagnosis status)",
            "background mortality, ageing out of the modelled range, emigration (demography/residence-queue adjustments; no split counter)"],
    "not_entry_routes": ["net-migration entrants are Susceptible (migration._place_adult_entrants)",
                         "migrant_hiv entrants are diagnosed Chronic, 2018+ only",
                         "exogenous non-MSM diagnoses enter diagnosed Chronic",
                         "ART transitions act on diagnosed people only"],
    "residual_label": "all other net flows (AIDS deaths, background deaths, ageing out, emigration, other); not a capture measure",
}


class ValidationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Design
# ---------------------------------------------------------------------------

def build_cells(anchor_a: dict, anchor_b: dict, space: dict) -> list[dict]:
    """Nine cells: A baseline + 7 factorial cells at A, and B baseline. Never clips or repairs."""
    from run_lhs_calibration import _valid_draw_mask, _with_derived_parameters  # noqa: PLC0415

    bounds = {p["name"]: (float(p["min"]), float(p["max"])) for p in space["parameters"]}
    names = [p["name"] for p in space["parameters"]]
    steps = {}
    for label, name, direction in FACTORS:
        lo, hi = bounds[name]
        step = direction * STEP_FRACTION * (hi - lo)
        target = float(anchor_a[name]) + step
        if not lo <= target <= hi:
            raise ValidationError(f"{label} {name}: {anchor_a[name]} {step:+g} -> {target} outside [{lo}, {hi}]; no clipping")
        steps[label] = (name, step, target)
    cells = []
    for code in range(8):
        levels = [(code >> (2 - k)) & 1 for k in range(3)]  # F1 is the most significant bit
        params = {n: float(anchor_a[n]) for n in names}
        varied = {}
        for (label, _, _), level in zip(FACTORS, levels):
            if level:
                name, step, target = steps[label]
                params[name] = target
                varied[name] = {"from": float(anchor_a[name]), "to": target, "step": step}
        cells.append({"cell_id": "A_" + "".join(map(str, levels)), "anchor": ANCHOR_A, "part": 1 if code == 0 else 2,
                      "F1": levels[0], "F2": levels[1], "F3": levels[2], "params": params, "varied": varied})
    cells.append({"cell_id": "B_000", "anchor": ANCHOR_B, "part": 1, "F1": 0, "F2": 0, "F3": 0,
                  "params": {n: float(anchor_b[n]) for n in names}, "varied": {}})

    frame = _with_derived_parameters(pd.DataFrame([c["params"] for c in cells]))
    valid = _valid_draw_mask(frame)
    for cell, ok, (_, row) in zip(cells, valid, frame.iterrows()):
        if not ok:
            raise ValidationError(f"cell {cell['cell_id']} violates draw constraints")
        for n in names:
            lo, hi = bounds[n]
            if not lo <= row[n] <= hi:
                raise ValidationError(f"cell {cell['cell_id']} parameter {n}={row[n]} outside [{lo}, {hi}]")
        reference = anchor_a if cell["anchor"] == ANCHOR_A else anchor_b
        for n in names:
            if n not in cell["varied"] and cell["params"][n] != float(reference[n]):
                raise ValidationError(f"cell {cell['cell_id']} changed unvaried parameter {n}")
    return cells


def build_tasks(cells: list[dict], seeds: Iterable[int]) -> list[dict]:
    tasks = []
    for cell in cells:
        for seed in seeds:
            tasks.append({"task_id": len(tasks), "cell_id": cell["cell_id"], "anchor": cell["anchor"], "seed": int(seed)})
    return tasks


# ---------------------------------------------------------------------------
# Mode / path assertions
# ---------------------------------------------------------------------------

def assert_stochastic_msm_config(config: dict, label: str) -> None:
    det = config.get("run_control", {}).get("deterministic_mode", {})
    if not isinstance(det, dict) or det.get("value") is not False:
        raise ValidationError(f"{label}: run_control.deterministic_mode must be explicitly False, got {det!r}")
    sim_det = config.get("simulation_controls", {}).get("deterministic_mode", {}).get("value", False) \
        if isinstance(config.get("simulation_controls"), dict) else False
    if sim_det is not False:
        raise ValidationError(f"{label}: simulation_controls.deterministic_mode is {sim_det!r}")
    block = config.get("transmission_model", {}).get("disease", {}).get("diagnosis", {}).get("msm_relative_diagnosis", {})
    if block.get("enabled", {}).get("value") is not True:
        raise ValidationError(f"{label}: msm_relative_diagnosis.enabled must be True")
    if not block.get("schedule", {}).get("value"):
        raise ValidationError(f"{label}: msm_relative_diagnosis schedule is empty")


def assert_stochastic_msm_engine(engine, label: str) -> None:
    if engine.deterministic is not False:
        raise ValidationError(f"{label}: engine.deterministic is {engine.deterministic!r}")
    if getattr(engine, "msm_relative_diagnosis_schedule", None) is None:
        raise ValidationError(f"{label}: engine is not on the enabled MSM diagnosis path")
    missing = [c for c in STOCK_COLUMNS if c not in engine.outputs]
    if missing:
        raise ValidationError(f"{label}: engine does not record {missing}")


def cell_config(cell: dict, base_config: dict, space: dict) -> dict:
    from run_lhs_calibration import inject_params  # noqa: PLC0415

    config = inject_params(base_config, cell["params"], space, deterministic_mode=False,
                           simulation_start_year=SIMULATION_START_YEAR)
    assert_stochastic_msm_config(config, cell["cell_id"])
    return config


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def _seeds_from_obj(obj, found: set[int]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lk = str(key).lower()
            if lk in {"seeds", "seed_range", "replicate_seeds"} and isinstance(value, list) and len(value) == 2 \
                    and all(isinstance(v, int) for v in value) and value[1] >= value[0] and value[1] - value[0] < 100000:
                found.update(range(value[0], value[1] + 1))
            elif "seed" in lk and isinstance(value, int) and not isinstance(value, bool):
                found.add(value)
            elif "seed" in lk and isinstance(value, list):
                found.update(v for v in value if isinstance(v, int) and not isinstance(v, bool))
            elif "seed" in lk and isinstance(value, str):
                found.update(int(v) for v in re.findall(r"\d+", value))
            _seeds_from_obj(value, found)
        settings = obj.get("run_settings") if isinstance(obj.get("run_settings"), dict) else None
        if settings and isinstance(settings.get("simulation_seed"), int):
            k = int(settings.get("replicates_per_design_point", 1))
            stride = int(settings.get("replicate_seed_stride", 1))
            found.update(settings["simulation_seed"] + stride * i for i in range(k))
    elif isinstance(obj, list):
        for v in obj:
            _seeds_from_obj(v, found)


def recorded_seeds(outputs_root: Path) -> tuple[set[int], int]:
    found: set[int] = set()
    n = 0
    for manifest in outputs_root.rglob("manifest.json"):
        try:
            _seeds_from_obj(json.loads(manifest.read_text()), found)
            n += 1
        except (OSError, json.JSONDecodeError):
            continue
    return found, n


def assert_seeds_disjoint(seeds: Iterable[int], outputs_root: Path, extra_known: Iterable[int] = ()) -> dict:
    seeds = [int(s) for s in seeds]
    if len(set(seeds)) != len(seeds):
        raise ValidationError("experiment seeds are not unique")
    found, n = recorded_seeds(outputs_root)
    found.update(int(s) for s in extra_known)
    overlap = sorted(set(seeds) & found)
    if overlap:
        raise ValidationError(f"experiment seeds overlap recorded seeds: {overlap}")
    return {"n_manifests_scanned": n, "n_recorded_seeds": len(found), "overlap": overlap,
            "limitation": "Only seeds recorded in manifest.json files (plus declared source/confirmation ranges) are checked."}


# ---------------------------------------------------------------------------
# Diagnosis test gate
# ---------------------------------------------------------------------------

def evaluate_junit(xml_text: str) -> dict:
    """Pass only if every test ran, none skipped/errored, and failures are exactly the tolerated one (or none)."""
    root = ET.fromstring(xml_text)
    cases = root.iter("testcase")
    passed, failures, errors, skipped = [], [], [], []
    for case in cases:
        ident = {"classname": case.get("classname"), "name": case.get("name")}
        if case.find("error") is not None:
            errors.append(ident)
        elif case.find("skipped") is not None:
            skipped.append(ident)
        elif case.find("failure") is not None:
            failure = case.find("failure")
            ident["message"] = (failure.get("message") or "") + (failure.text or "")
            failures.append(ident)
        else:
            passed.append(ident)
    unexpected = [f for f in failures if not (f["classname"] == TOLERATED_FAILURE["classname"]
                                              and f["name"] == TOLERATED_FAILURE["name"]
                                              and TOLERATED_FAILURE["message_contains"] in f["message"])]
    tolerated = [f for f in failures if f not in unexpected]
    ok = not errors and not skipped and not unexpected and len(passed) + len(failures) > 0
    return {"ok": ok, "n_passed": len(passed), "n_failed": len(failures), "n_errors": len(errors),
            "n_skipped": len(skipped), "tolerated_failures": [{k: f[k] for k in ("classname", "name")} for f in tolerated],
            "unexpected_failures": [{k: f[k] for k in ("classname", "name")} for f in unexpected],
            "errors": errors, "skipped": skipped,
            "tolerated_failure_still_fails": bool(tolerated),
            "suite_fully_passing": not failures and not errors and not skipped}


def run_diagnosis_test_gate() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", *DIAGNOSIS_TEST_FILES]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if not xml.exists():
            raise ValidationError(f"diagnosis test gate produced no junit xml:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        result = evaluate_junit(xml.read_text())
    result["command"] = " ".join(cmd[1:])
    result["pytest_exit_code"] = proc.returncode
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def load_inputs() -> dict:
    from run_hm_confirmation import _check_source_compatibility, _verified_manifest  # noqa: PLC0415

    manifest = _verified_manifest(SOURCE.resolve())
    prov = manifest["input_provenance"]
    paths = {k: Path(prov[k]["path"]) for k in ("base_config", "calib_space", "target_register")}
    _check_source_compatibility(manifest, base_config_path=paths["base_config"], calib_space_path=paths["calib_space"],
                                register_path=paths["target_register"], v_ensemble_path=None)
    space = json.loads(paths["calib_space"].read_text())
    names = [p["name"] for p in space["parameters"]]
    evaluated = pd.read_csv(SOURCE / "wave_01/evaluated.csv")
    if evaluated.design_id.duplicated().any():
        raise ValidationError("duplicate design ids in source evaluated.csv")
    ev = evaluated.set_index("design_id")
    for anchor in (ANCHOR_A, ANCHOR_B):
        if anchor not in ev.index:
            raise ValidationError(f"anchor {anchor} missing from source")
        if not np.isfinite(ev.loc[anchor, names].to_numpy(float)).all():
            raise ValidationError(f"anchor {anchor} has non-finite parameters")
    return {"manifest": manifest, "paths": paths, "space": space, "names": names,
            "base_config": json.loads(paths["base_config"].read_text()),
            "anchor_a": ev.loc[ANCHOR_A, names].to_dict(), "anchor_b": ev.loc[ANCHOR_B, names].to_dict()}


def validate(output_dir: Path, workers: int, *, run_test_gate: bool = True,
             test_gate: Callable[[], dict] | None = None) -> dict:
    report: dict = {"checks": {}}
    checks = report["checks"]

    def check(name, fn):
        try:
            checks[name] = {"ok": True, "detail": fn()}
        except Exception as exc:  # noqa: BLE001 - every failed check is reported, not raised mid-way
            checks[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    state: dict = {}
    check("source_provenance_and_module_compatibility", lambda: state.update(load_inputs()) or {
        "source_manifest_sha256": sha256(SOURCE / "manifest.json"), "anchors": [ANCHOR_A, ANCHOR_B]})
    if "space" in state:
        check("cells_ranges_constraints_unvaried", lambda: [
            {k: c[k] for k in ("cell_id", "anchor", "part", "F1", "F2", "F3", "varied")}
            for c in state.setdefault("cells", build_cells(state["anchor_a"], state["anchor_b"], state["space"]))])
    if "cells" in state:
        def modes():
            from run_v3 import SimulationEngineV3  # noqa: PLC0415
            out = []
            for cell in state["cells"]:
                config = cell_config(cell, state["base_config"], state["space"])
                with contextlib.redirect_stdout(io.StringIO()):
                    engine = SimulationEngineV3(copy.deepcopy(config), seed=SEEDS[0])  # construction only; no step
                assert_stochastic_msm_engine(engine, cell["cell_id"])
                if engine._current_step is not None or len(engine.outputs.get("year", [])):
                    raise ValidationError("engine construction advanced the simulation")
                out.append({"cell_id": cell["cell_id"], "deterministic": engine.deterministic,
                            "msm_relative_diagnosis_path": True})
            return out
        check("stochastic_mode_and_enabled_msm_path_every_cell", modes)
        state["tasks"] = build_tasks(state["cells"], SEEDS)
        check("budget", lambda: _budget(state["tasks"], workers))
    check("seeds_disjoint", lambda: assert_seeds_disjoint(
        SEEDS, ROOT / "outputs", extra_known=list(range(93000, 93005)) + list(range(194000, 194050)) + list(range(191000, 191050))))
    check("output_directory_unoccupied", lambda: _assert_unoccupied(output_dir))
    if run_test_gate:
        def gate():
            result = (test_gate or globals()["run_diagnosis_test_gate"])()
            if not result["ok"]:
                raise ValidationError(json.dumps(result))
            return result
        check("diagnosis_integration_test_gate", gate)
    else:
        checks["diagnosis_integration_test_gate"] = {"ok": False, "detail": "not run"}
    report["ok"] = all(c["ok"] for c in checks.values())
    report["state"] = state
    return report


def _budget(tasks: list[dict], workers: int) -> dict:
    if workers < 1:
        raise ValidationError("workers must be >= 1")
    if len(tasks) > MAX_SIMULATIONS:
        raise ValidationError(f"{len(tasks)} simulations exceed the {MAX_SIMULATIONS} cap")
    if len(tasks) != 90:
        raise ValidationError(f"frozen design expects 90 simulations, built {len(tasks)}")
    return {"simulations": len(tasks), "cap": MAX_SIMULATIONS, "workers": workers,
            "stop_rule": f"after >= {MIN_JOBS_BEFORE_PROJECTION} completed jobs, stop submitting if projected total wall time > {MAX_PROJECTED_HOURS} h"}


def _assert_unoccupied(output_dir: Path) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValidationError(f"output directory {output_dir} is not empty; refusing to overwrite")
    return {"output_dir": str(output_dir)}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def simulate_task(payload: dict) -> dict:
    """One seed of one cell. Stochastic, enabled MSM path, asserted before and after the run."""
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    row = {k: payload[k] for k in ("task_id", "cell_id", "anchor", "seed")}
    try:
        config = payload["config"]
        assert_stochastic_msm_config(config, payload["cell_id"])
        with contextlib.redirect_stdout(io.StringIO()):
            engine = SimulationEngineV3(copy.deepcopy(config), seed=int(payload["seed"]))
            assert_stochastic_msm_engine(engine, payload["cell_id"])
            result = engine.run(run_number=1)
        assert_stochastic_msm_engine(engine, payload["cell_id"])
        outputs = extract_annual_outputs(result, EXTRACT_START_YEAR, EXTRACT_END_YEAR)
        for indicator, series in outputs.items():
            for year, value in series.items():
                row[f"{indicator}_{year}"] = float(value)
        summary = result["summary"]
        years = summary["year"].astype(float).astype(int)
        for column in STOCK_COLUMNS:
            first = summary.groupby(years)[column].first()
            for year, value in first.items():
                if EXTRACT_START_YEAR <= year <= EXTRACT_END_YEAR:
                    row[f"{column}_startofyear_{year}"] = float(value)
        row["engine_deterministic"] = bool(engine.deterministic)
        row["msm_relative_diagnosis_path"] = True
        row["error"] = ""
    except Exception as exc:  # noqa: BLE001 - a failed run is recorded, never retried
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_seconds"] = time.time() - started
    return row


def run_tasks(tasks: list[dict], payload_for: Callable[[dict], dict], output_dir: Path, provenance: dict, *,
              workers: int, worker: Callable[[dict], dict] = simulate_task,
              executor_factory=ProcessPoolExecutor, clock: Callable[[], float] = time.time,
              max_projected_hours: float = MAX_PROJECTED_HOURS) -> dict:
    """Bounded submission, checkpoint after every completion, projection stop rule, no retries."""
    start = clock()
    pending = list(tasks)
    rows: list[dict] = []
    status = "RUNNING"
    stop_reason = ""

    def checkpoint():
        pd.DataFrame(rows).sort_values("task_id").to_csv(output_dir / "raw_seed_outputs.partial.csv", index=False)
        (output_dir / "checkpoint.json").write_text(json.dumps({
            "status": status, "stop_reason": stop_reason, "provenance": provenance,
            "planned_task_ids": [t["task_id"] for t in tasks],
            "completed_task_ids": sorted(r["task_id"] for r in rows),
            "failed_task_ids": sorted(r["task_id"] for r in rows if r.get("error")),
            "elapsed_seconds": clock() - start, "updated_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=2))

    with executor_factory(max_workers=workers) as pool:
        running = {}
        while pending or running:
            while pending and len(running) < workers and status == "RUNNING":
                task = pending.pop(0)
                running[pool.submit(worker, payload_for(task))] = task
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.pop(future)
                rows.append(future.result())
                print(f"comparison: {len(rows)}/{len(tasks)} simulations complete"
                      f"{' (error: ' + rows[-1]['error'] + ')' if rows[-1].get('error') else ''}", flush=True)
            if status == "RUNNING" and pending and rows and all(r.get("error") for r in rows[:1]) and len(rows) == len(done):
                # Fail fast: the very first completed job errored (e.g. extraction or path assertion).
                status = "STOPPED_FIRST_JOB_ERROR"
                stop_reason = f"first completed job failed: {rows[0]['error']}"
                print(f"comparison: STOPPING new submissions: {stop_reason}", flush=True)
                pending = []
            if status == "RUNNING" and len(rows) >= min(MIN_JOBS_BEFORE_PROJECTION, len(tasks)) and pending:
                elapsed = clock() - start
                projected = elapsed / len(rows) * len(tasks)
                if projected > max_projected_hours * 3600:
                    status = "STOPPED_PROJECTION"
                    stop_reason = f"projected {projected/3600:.2f} h > {max_projected_hours} h after {len(rows)} jobs"
                    print(f"comparison: STOPPING new submissions: {stop_reason}", flush=True)
                    pending = []
            checkpoint()
    if status == "RUNNING":
        status = "COMPLETE_WITH_ERRORS" if any(r.get("error") for r in rows) else "COMPLETE"
    checkpoint()
    return {"status": status, "stop_reason": stop_reason, "rows": rows, "elapsed_seconds": clock() - start}


def write_manifest(output_dir: Path, payload: dict) -> None:
    artefacts = [{"path": str(p.relative_to(output_dir)), "bytes": p.stat().st_size, "sha256": sha256(p)}
                 for p in sorted(output_dir.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    (output_dir / "manifest.json").write_text(json.dumps({**payload, "artefacts": artefacts}, indent=2, default=str))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)

    report = validate(output_dir, args.workers)
    printable = {k: v for k, v in report.items() if k != "state"}
    print(json.dumps(printable, indent=2, default=str), flush=True)
    if not report["ok"]:
        print("VALIDATION FAILED; no simulations started.", flush=True)
        return 2
    if args.validate_only:
        print(f"VALIDATION PASSED; validate-only: 0 simulations started. Would run {len(report['state']['tasks'])}.", flush=True)
        return 0

    import run_history_matching as rhm  # noqa: PLC0415

    state = report["state"]
    output_dir.mkdir(parents=True, exist_ok=True)
    _assert_unoccupied(output_dir)
    configs = {c["cell_id"]: cell_config(c, state["base_config"], state["space"]) for c in state["cells"]}
    spec = {
        "design_doc": DESIGN_DOC, "anchors": {"A": ANCHOR_A, "B": ANCHOR_B}, "seeds": list(SEEDS),
        "factors": [{"label": l, "parameter": p, "direction": d, "step_fraction_of_range": STEP_FRACTION} for l, p, d in FACTORS],
        "cells": [{k: c[k] for k in ("cell_id", "anchor", "part", "F1", "F2", "F3", "varied", "params")} for c in state["cells"]],
        "simulation_start_year": SIMULATION_START_YEAR, "extract_years": [EXTRACT_START_YEAR, EXTRACT_END_YEAR],
        "mode": {"deterministic": False, "msm_relative_diagnosis_enabled": True},
        "undiagnosed_aids_routes": UNDIAGNOSED_AIDS_ROUTES,
        "tolerated_test_failure": TOLERATED_FAILURE,
        "reading_rules": "Frozen in design_doc section 4 (R1a/R1b/R2/R3); descriptive only; no acceptance or NROY use.",
        "config_sha256": {cid: hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest() for cid, cfg in configs.items()},
    }
    (output_dir / "frozen_spec.json").write_text(json.dumps(spec, indent=2))
    provenance = {"validation": printable, "runtime": rhm.repository_provenance(), "runner_sha256": sha256(Path(__file__)),
                  "source_manifest_sha256": sha256(SOURCE / "manifest.json"),
                  "frozen_spec_sha256": sha256(output_dir / "frozen_spec.json")}
    result = run_tasks(state["tasks"], lambda t: {**t, "config": configs[t["cell_id"]]}, output_dir, provenance,
                       workers=args.workers)
    raw = pd.DataFrame(result["rows"]).sort_values("task_id")
    raw.to_csv(output_dir / "raw_seed_outputs.csv", index=False)
    write_manifest(output_dir, {"status": result["status"], "stop_reason": result["stop_reason"],
                                "planned_simulations": len(state["tasks"]), "completed_simulations": len(raw),
                                "failed_simulations": int(raw["error"].fillna("").astype(str).ne("").sum()),
                                "elapsed_seconds": result["elapsed_seconds"], "workers": args.workers,
                                "generated_utc": datetime.now(timezone.utc).isoformat(), **provenance})
    print(f"comparison finished: {result['status']} {result['stop_reason']}", flush=True)
    return 0 if result["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
