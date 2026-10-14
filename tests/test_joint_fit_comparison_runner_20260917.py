"""Tests for run_joint_fit_comparison_20260917.py (no simulations are run by these tests)."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

import run_joint_fit_comparison_20260917 as R

ROOT = Path(__file__).resolve().parents[1]
SPACE = json.loads((ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json").read_text())
NAMES = [p["name"] for p in SPACE["parameters"]]
BOUNDS = {p["name"]: (p["min"], p["max"]) for p in SPACE["parameters"]}
EVALUATED = ROOT / "outputs/calibration/hm_focused_diagnoses_then_aids_20260916/wave_01/evaluated.csv"


@pytest.fixture(scope="module")
def anchors():
    if not EVALUATED.exists():
        pytest.skip("source run not present")
    ev = pd.read_csv(EVALUATED).set_index("design_id")
    return ev.loc[R.ANCHOR_A, NAMES].to_dict(), ev.loc[R.ANCHOR_B, NAMES].to_dict()


# brief item 6: perturbations obey ranges, constraints; unvaried parameters unchanged ---------------

def test_cells_respect_ranges_constraints_and_unvaried(anchors):
    a, b = anchors
    cells = R.build_cells(a, b, SPACE)
    assert [c["cell_id"] for c in cells] == ["A_000", "A_001", "A_010", "A_011", "A_100", "A_101", "A_110", "A_111", "B_000"]
    for cell in cells:
        ref = a if cell["anchor"] == R.ANCHOR_A else b
        for n in NAMES:
            lo, hi = BOUNDS[n]
            assert lo <= cell["params"][n] <= hi
            if n not in cell["varied"]:
                assert cell["params"][n] == float(ref[n])
    a111 = next(c for c in cells if c["cell_id"] == "A_111")
    for label, name, direction in R.FACTORS:
        lo, hi = BOUNDS[name]
        assert a111["params"][name] == pytest.approx(float(a[name]) + direction * 0.25 * (hi - lo), abs=1e-15)
    assert set(next(c for c in cells if c["cell_id"] == "A_100")["varied"]) == {"diagnosis_1990"}
    assert next(c for c in cells if c["cell_id"] == "B_000")["varied"] == {}
    assert len(R.build_tasks(cells, R.SEEDS)) == 90


def test_infeasible_step_is_rejected_not_clipped(anchors):
    a, b = anchors
    a = dict(a)
    a["seed_prevalence"] = BOUNDS["seed_prevalence"][1] - 1e-6
    with pytest.raises(R.ValidationError, match="no clipping"):
        R.build_cells(a, b, SPACE)


def test_constraint_violation_is_rejected(anchors):
    a, b = anchors
    a = dict(a)
    # In bounds, ordered at baseline (0.17 <= 0.18), but the F1 step (+0.055) breaks diagnosis_1990 <= diagnosis_1997.
    a["diagnosis_1990"], a["diagnosis_1997"] = 0.17, 0.18
    with pytest.raises(R.ValidationError, match="draw constraints"):
        R.build_cells(a, b, SPACE)


# stochastic mode and enabled MSM path ---------------------------------------------------------

def _config(det=False, msm=True, schedule=True):
    block = {"enabled": {"value": msm}}
    if schedule:
        block["schedule"] = {"value": {"1996": 1.0}}
    return {"run_control": {"deterministic_mode": {"value": det}},
            "transmission_model": {"disease": {"diagnosis": {"msm_relative_diagnosis": block}}}}


def test_config_assertion_requires_stochastic_and_enabled_msm():
    R.assert_stochastic_msm_config(_config(), "ok")
    for bad in (_config(det=True), _config(msm=False), _config(schedule=False),
                {"run_control": {}, "transmission_model": _config()["transmission_model"]}):
        with pytest.raises(R.ValidationError):
            R.assert_stochastic_msm_config(bad, "bad")


def test_engine_assertion():
    class E:
        deterministic = False
        msm_relative_diagnosis_schedule = object()
        outputs = {c: [] for c in R.STOCK_COLUMNS}
    R.assert_stochastic_msm_engine(E(), "ok")
    e = E(); e.deterministic = True
    with pytest.raises(R.ValidationError):
        R.assert_stochastic_msm_engine(e, "det")
    e = E(); e.msm_relative_diagnosis_schedule = None
    with pytest.raises(R.ValidationError):
        R.assert_stochastic_msm_engine(e, "msm")


def test_real_cell_configs_are_stochastic_msm(anchors):
    a, b = anchors
    base = json.loads((ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json").read_text())
    for cell in R.build_cells(a, b, SPACE):
        R.cell_config(cell, base, SPACE)


# test gate ----------------------------------------------------------------------------------

def _junit(cases):
    body = []
    for classname, name, kind, message in cases:
        inner = "" if kind == "pass" else f'<{kind} message="{message}">{message}</{kind}>'
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    return f'<testsuites><testsuite>{"".join(body)}</testsuite></testsuites>'


TOL = (R.TOLERATED_FAILURE["classname"], R.TOLERATED_FAILURE["name"], "failure",
       "AssertionError: Multiplier=1.0 should produce identical MSM diagnoses; ref=1, mult1=2")


def test_gate_tolerates_only_the_exact_exception_and_never_claims_full_pass():
    ok = R.evaluate_junit(_junit([("x.A", "t1", "pass", ""), TOL]))
    assert ok["ok"] and ok["tolerated_failure_still_fails"] and not ok["suite_fully_passing"]
    assert not R.evaluate_junit(_junit([TOL, ("x.A", "t2", "failure", "boom")]))["ok"]
    assert not R.evaluate_junit(_junit([TOL, ("x.A", "t2", "error", "boom")]))["ok"]
    assert not R.evaluate_junit(_junit([TOL, ("x.A", "t2", "skipped", "skip")]))["ok"]
    wrong_message = (TOL[0], TOL[1], "failure", "AssertionError: something else")
    assert not R.evaluate_junit(_junit([wrong_message]))["ok"]
    same_name_other_class = ("x.Other", TOL[1], "failure", TOL[3])
    assert not R.evaluate_junit(_junit([same_name_other_class]))["ok"]
    assert not R.evaluate_junit(_junit([]))["ok"]


# seeds --------------------------------------------------------------------------------------

def test_seed_overlap_rejected(tmp_path):
    run = tmp_path / "outputs/calibration/x"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"seeds": [195003, 195020]}))
    with pytest.raises(R.ValidationError, match="overlap"):
        R.assert_seeds_disjoint(R.SEEDS, tmp_path / "outputs")
    (run / "manifest.json").write_text(json.dumps({"run_settings": {"simulation_seed": 195008, "replicates_per_design_point": 2}}))
    with pytest.raises(R.ValidationError, match="overlap"):
        R.assert_seeds_disjoint(R.SEEDS, tmp_path / "outputs")
    (run / "manifest.json").write_text(json.dumps({"seeds": [1, 2]}))
    assert R.assert_seeds_disjoint(R.SEEDS, tmp_path / "outputs")["overlap"] == []
    with pytest.raises(R.ValidationError):
        R.assert_seeds_disjoint([1, 1], tmp_path / "outputs")


# brief item 9: validate-only starts no simulations; occupied output refused; checkpoints --------

def test_validate_only_starts_no_simulation(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("simulation or executor started during validate-only")
    monkeypatch.setattr(R, "simulate_task", boom)
    monkeypatch.setattr(R, "run_tasks", boom)
    monkeypatch.setattr(R, "ProcessPoolExecutor", boom)
    import run_v3
    monkeypatch.setattr(run_v3.SimulationEngineV3, "run", boom)
    gate_calls = []
    monkeypatch.setattr(R, "run_diagnosis_test_gate", lambda: gate_calls.append(1) or {"ok": True, "stub": True})
    code = R.main(["--validate-only", "--output-dir", str(tmp_path / "out"), "--workers", "4"])
    assert code == 0 and gate_calls == [1]
    assert not (tmp_path / "out").exists()


def test_occupied_output_refused(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(R.ValidationError, match="not empty"):
        R._assert_unoccupied(tmp_path)
    report = R.validate(tmp_path, 4, run_test_gate=False)
    assert report["checks"]["output_directory_unoccupied"]["ok"] is False and report["ok"] is False
    assert (tmp_path / "keep.txt").read_text() == "x"


def _fake_worker(payload):
    return {"task_id": payload["task_id"], "cell_id": payload["cell_id"], "anchor": payload["anchor"],
            "seed": payload["seed"], "error": "boom" if payload["task_id"] in payload.get("fail_ids", (2,)) else "", "wall_seconds": 0.0}


def _tasks(n):
    return [{"task_id": i, "cell_id": "A_000", "anchor": 620, "seed": 195000 + i} for i in range(n)]


def test_checkpoint_retains_provenance_and_completed_tasks_without_retry(tmp_path):
    calls = []

    def worker(p):
        calls.append(p["task_id"])
        return _fake_worker(p)
    result = R.run_tasks(_tasks(6), lambda t: dict(t), tmp_path, {"runner_sha256": "abc"}, workers=2,
                         worker=worker, executor_factory=ThreadPoolExecutor)
    assert result["status"] == "COMPLETE_WITH_ERRORS"
    assert sorted(calls) == list(range(6))  # each task exactly once: no retry of the failed task
    cp = json.loads((tmp_path / "checkpoint.json").read_text())
    assert cp["provenance"] == {"runner_sha256": "abc"}
    assert cp["completed_task_ids"] == list(range(6)) and cp["failed_task_ids"] == [2]
    assert len(pd.read_csv(tmp_path / "raw_seed_outputs.partial.csv")) == 6


def test_projection_stop_rule_stops_new_submissions(tmp_path):
    t = {"now": 0.0}

    def clock():
        t["now"] += 1000.0  # every clock read advances ~17 min
        return t["now"]
    result = R.run_tasks(_tasks(90), lambda x: dict(x), tmp_path, {}, workers=1, worker=_fake_worker,
                         executor_factory=ThreadPoolExecutor, clock=clock, max_projected_hours=2.0)
    assert result["status"] == "STOPPED_PROJECTION"
    assert 4 <= len(result["rows"]) < 90
    cp = json.loads((tmp_path / "checkpoint.json").read_text())
    assert cp["status"] == "STOPPED_PROJECTION" and len(cp["completed_task_ids"]) == len(result["rows"])


# brief item 10: provenance mismatch blocks launch ---------------------------------------------------

def test_module_hash_mismatch_blocks_validation(monkeypatch, tmp_path):
    import run_history_matching as rhm
    real = rhm.repository_provenance

    def tampered():
        prov = real()
        prov["modules"]["run_v3.py"] = "0" * 64
        return prov
    monkeypatch.setattr(rhm, "repository_provenance", tampered)
    report = R.validate(tmp_path / "out", 4, run_test_gate=False)
    check = report["checks"]["source_provenance_and_module_compatibility"]
    assert check["ok"] is False and "run_v3.py" in check["detail"] and report["ok"] is False


def test_first_job_error_stops_new_submissions(tmp_path):
    result = R.run_tasks(_tasks(90), lambda t: {**t, "fail_ids": (0,)}, tmp_path, {}, workers=1, worker=_fake_worker,
                         executor_factory=ThreadPoolExecutor)
    assert result["status"] == "STOPPED_FIRST_JOB_ERROR" and len(result["rows"]) == 1
