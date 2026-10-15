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


@pytest.fixture(scope="module")
def anchors():
    def point(fraction):
        return {
            name: float(lo + fraction * (hi - lo))
            for name, (lo, hi) in BOUNDS.items()
        }

    return point(0.5), point(0.4)


@pytest.fixture
def current_synthetic_source(monkeypatch, tmp_path, anchors):
    """Checksum-valid current-code source with synthetic anchor rows."""
    import run_history_matching as rhm

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source_paths = {
        "base_config": ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json",
        "calib_space": ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json",
        "target_register": ROOT / "configs/hm_target_register_focused_20260916.json",
    }
    fixture_paths = {}
    for name, source_path in source_paths.items():
        fixture_path = inputs / source_path.name
        fixture_path.write_bytes(source_path.read_bytes())
        fixture_paths[name] = fixture_path

    source = tmp_path / "synthetic_source"
    wave = source / "wave_01"
    wave.mkdir(parents=True)
    a, b = anchors
    evaluated = pd.DataFrame([
        {"design_id": R.ANCHOR_A, **a},
        {"design_id": R.ANCHOR_B, **b},
    ])
    evaluated_path = wave / "evaluated.csv"
    evaluated.to_csv(evaluated_path, index=False)

    manifest = {
        "run_settings": {"v_ensemble_mode": "predictive_local"},
        "input_provenance": {
            **{
                name: {"path": str(path), "sha256": R.sha256(path)}
                for name, path in fixture_paths.items()
            },
            "runtime": {"modules": rhm.repository_provenance()["modules"]},
        },
        "artefacts": [{
            "path": "wave_01/evaluated.csv",
            "bytes": evaluated_path.stat().st_size,
            "sha256": R.sha256(evaluated_path),
        }],
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(R, "SOURCE", source)
    monkeypatch.setattr(R, "ROOT", tmp_path)
    return source


@pytest.fixture
def validation_only_engine(monkeypatch):
    """Exercise mode/path validation without constructing the model engine."""
    import run_v3

    class ValidationOnlyEngine:
        def __init__(self, config, seed):
            assert seed == R.SEEDS[0]
            R.assert_stochastic_msm_config(config, "synthetic")
            self.deterministic = False
            self.msm_relative_diagnosis_schedule = object()
            self.outputs = {name: [] for name in (*R.STOCK_COLUMNS, "year")}
            self._current_step = None

    monkeypatch.setattr(run_v3, "SimulationEngineV3", ValidationOnlyEngine)


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

def test_preserved_source_is_refused_by_current_code(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        raise AssertionError("simulation or executor started during stale-source validation")

    monkeypatch.setattr(R, "simulate_task", boom)
    monkeypatch.setattr(R, "run_tasks", boom)
    monkeypatch.setattr(R, "ProcessPoolExecutor", boom)
    monkeypatch.setattr(R, "ROOT", tmp_path)
    monkeypatch.setattr(R, "run_diagnosis_test_gate", lambda: {"ok": True, "stub": True})
    output = tmp_path / "out"
    assert R.main(["--validate-only", "--output-dir", str(output)]) == 2
    stdout = capsys.readouterr().out
    assert "source_provenance_and_module_compatibility" in stdout
    assert "Confirmation runtime differs from the source run" in stdout
    assert "VALIDATION FAILED; no simulations started" in stdout
    assert not output.exists()


def test_validate_only_starts_no_simulation_from_current_synthetic_source(
    monkeypatch, tmp_path, current_synthetic_source, validation_only_engine
):
    def boom(*a, **k):
        raise AssertionError("simulation or executor started during validate-only")
    monkeypatch.setattr(R, "simulate_task", boom)
    monkeypatch.setattr(R, "run_tasks", boom)
    monkeypatch.setattr(R, "ProcessPoolExecutor", boom)
    gate_calls = []
    monkeypatch.setattr(R, "run_diagnosis_test_gate", lambda: gate_calls.append(1) or {"ok": True, "stub": True})
    code = R.main(["--validate-only", "--output-dir", str(tmp_path / "out"), "--workers", "4"])
    assert code == 0 and gate_calls == [1]
    assert not (tmp_path / "out").exists()


def test_occupied_output_refused(
    tmp_path, current_synthetic_source, validation_only_engine
):
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(R.ValidationError, match="not empty"):
        R._assert_unoccupied(tmp_path)
    report = R.validate(tmp_path, 4, test_gate=lambda: {"ok": True, "stub": True})
    assert report["checks"]["output_directory_unoccupied"]["ok"] is False and report["ok"] is False
    assert [name for name, check in report["checks"].items() if not check["ok"]] == [
        "output_directory_unoccupied"
    ]
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

def test_module_hash_mismatch_blocks_validation(
    monkeypatch, tmp_path, current_synthetic_source
):
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
