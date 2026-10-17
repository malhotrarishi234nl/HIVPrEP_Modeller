from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from model_workbench.recipes import RecipeError, load_recipe
from model_workbench.service import WorkbenchError, WorkbenchService


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_exposes_only_current_canonical_m0_m1_recipes() -> None:
    service = WorkbenchService(ROOT)
    recipes = service.list_recipes()
    assert [recipe.id for recipe in recipes] == [
        "basic-stochastic-m0", "basic-stochastic-m1"
    ]
    assert all(recipe.inputs["config"].sha256 for recipe in recipes)


def test_recipe_schema_rejects_arbitrary_command_fields(tmp_path: Path) -> None:
    source = ROOT / "model_workbench" / "recipe_definitions" / "basic_stochastic_m0.json"
    recipe = json.loads(source.read_text(encoding="utf-8"))
    recipe["command"] = "rm -rf anything"
    target = tmp_path / "unsafe.json"
    target.write_text(json.dumps(recipe), encoding="utf-8")
    with pytest.raises(RecipeError, match="unsupported fields"):
        load_recipe(target)


def test_resolution_builds_fixed_argv_and_separate_fresh_engine_dirs(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, output_root=tmp_path)
    plan = service.resolve(
        "basic-stochastic-m1", "run",
        {"p": 0.25, "seed": 91, "replicates": 2}, tmp_path / "run",
    )
    assert len(plan.steps) == 2
    assert [step.seed for step in plan.steps] == [91, 92]
    assert Path(plan.steps[0].argv[0]).resolve() == Path(sys.executable).resolve()
    assert "--research-run" in plan.steps[0].argv
    assert plan.steps[0].argv[plan.steps[0].argv.index("--p") + 1] == "0.25"
    assert plan.steps[0].output_dir == tmp_path / "run" / "engine" / "seed_0000000091"
    assert plan.steps[0].output_dir != plan.run_root
    assert all("shell" not in argument for argument in plan.steps[0].argv)


def test_resolution_applies_bounded_parameter_controls_to_the_run_command(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, output_root=tmp_path)
    path = "transmission_model.disease.transmission.advanced.beta_per_route.anal_receptive.chronic.value"
    plan = service.resolve(
        "basic-stochastic-m0", "run",
        {f"parameter:{path}": 0.016}, tmp_path / "beta-sensitivity",
    )
    argv = plan.steps[0].argv
    assignments = [argv[index + 1] for index, item in enumerate(argv) if item == "--set"]
    assert f"{path}=0.016" in assignments
    assert plan.controls[f"parameter:{path}"] == pytest.approx(0.016)
    with pytest.raises(RecipeError, match="must be in"):
        service.resolve(
            "basic-stochastic-m0", "run",
            {f"parameter:{path}": 0.5}, tmp_path / "invalid-beta",
        )


def test_unknown_override_and_seed_overflow_fail_before_execution(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, output_root=tmp_path)
    with pytest.raises(RecipeError, match="unsupported controls"):
        service.resolve(
            "basic-stochastic-m0", "run", {"command": "anything"}, tmp_path / "bad"
        )


def test_execute_rejects_modified_argv_plan(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, runner=SuccessfulFakeRunner(), output_root=tmp_path)
    plan = service.resolve("basic-stochastic-m0", "run", None, tmp_path / "forged")
    forged_step = replace(plan.steps[0], argv=("sh", "-c", "arbitrary command"))
    forged_plan = replace(plan, steps=(forged_step,))
    with pytest.raises(WorkbenchError, match="modified or externally constructed"):
        service.execute(forged_plan)
    with pytest.raises(RecipeError, match="uint32"):
        service.resolve(
            "basic-stochastic-m0", "run",
            {"seed": 2**32 - 1, "replicates": 2}, tmp_path / "overflow",
        )


class SuccessfulFakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if "--validate-only" not in argv:
            output = Path(argv[argv.index("--output-dir") + 1])
            output.mkdir(parents=True)
            (output / "run_reproducibility_manifest.json").write_text(
                json.dumps({"status": "COMPLETE"}), encoding="utf-8"
            )
            (output / "research_not_production_eligible_run_001.csv").write_text(
                "year,value\n1980,1\n2020,2\n", encoding="utf-8"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="VALID", stderr="")


def test_execute_records_manifest_events_verification_and_report(tmp_path: Path) -> None:
    runner = SuccessfulFakeRunner()
    service = WorkbenchService(ROOT, runner=runner, output_root=tmp_path)
    run_root = tmp_path / "run"
    plan = service.resolve("basic-stochastic-m0", "run", None, run_root)
    manifest = service.execute(plan)
    assert manifest["status"] == "COMPLETE"
    assert manifest["completed_work_units"] == 1
    saved = service.status(run_root)
    assert saved["status"] == "COMPLETE"
    assert saved["steps"][0]["verification"][-1]["id"] == "child-manifest"
    assert (run_root / "report" / "index.html").is_file()
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert "--validate-only" in runner.calls[0][0]


class ConcurrentFakeRunner(SuccessfulFakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if "--validate-only" in argv:
            return super().__call__(argv, **kwargs)
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return super().__call__(argv, **kwargs)
        finally:
            with self.lock:
                self.active -= 1


def test_execute_uses_requested_parallel_workers_and_preserves_step_order(tmp_path: Path) -> None:
    runner = ConcurrentFakeRunner()
    service = WorkbenchService(ROOT, runner=runner, output_root=tmp_path)
    plan = service.resolve(
        "basic-stochastic-m0", "run", {"replicates": 4, "seed": 42},
        tmp_path / "parallel"
    )
    manifest = service.execute(plan, workers=3)
    assert manifest["status"] == "COMPLETE"
    assert manifest["execution"]["effective_workers"] == 3
    assert runner.max_active == 3
    assert [row["seed"] for row in manifest["steps"]] == [42, 43, 44, 45]
    assert all(row["status"] == "COMPLETE" for row in manifest["steps"])


class FailingRunRunner(SuccessfulFakeRunner):
    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if "--validate-only" in argv:
            return super().__call__(argv, **kwargs)
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 7, stdout="", stderr="failed")


def test_failed_execution_is_preserved_in_manifest(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, runner=FailingRunRunner(), output_root=tmp_path)
    run_root = tmp_path / "failed"
    plan = service.resolve("basic-stochastic-m0", "benchmark", None, run_root)
    with pytest.raises(WorkbenchError, match="exited with code 7"):
        service.execute(plan)
    manifest = service.status(run_root)
    assert manifest["status"] == "FAILED"
    assert manifest["steps"][0]["status"] == "FAILED"
    assert (run_root / "report" / "index.html").is_file()


def test_estimate_scales_benchmark_by_requested_units(tmp_path: Path) -> None:
    service = WorkbenchService(ROOT, output_root=tmp_path)
    plan = service.resolve(
        "basic-stochastic-m0", "run", {"replicates": 4}, tmp_path / "target"
    )
    estimate = service.estimate(
        plan, {"elapsed_seconds": 10.0, "completed_work_units": 1}
    )
    assert estimate.available
    assert estimate.seconds_central == 40.0
    assert estimate.seconds_low == 34.0
    assert estimate.seconds_high == 54.0


def test_check_performs_validate_only_without_engine_output() -> None:
    runner = SuccessfulFakeRunner()
    service = WorkbenchService(ROOT, runner=runner)
    checks = service.check("basic-stochastic-m1", {"p": 0.1})
    assert not [check for check in checks if check.level == "BLOCK"]
    assert len(runner.calls) == 1
    assert "--validate-only" in runner.calls[0][0]
    assert "--research-run" not in runner.calls[0][0]
