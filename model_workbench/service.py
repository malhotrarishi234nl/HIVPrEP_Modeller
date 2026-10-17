"""UI-agnostic workbench service for checking, planning, and executing recipes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from .adapters import AdapterRegistry, CommandRunner, default_registry
from .manifest import MANIFEST_NAME, add_event, atomic_write_json, load_manifest, new_manifest
from .recipes import RecipeCatalog
from .report import build_html_report
from .types import CheckResult, Recipe, ResolvedPlan, RuntimeEstimate
from .workers import effective_worker_count


class WorkbenchError(RuntimeError):
    """A safe workbench operation could not be completed."""


class WorkbenchService:
    def __init__(
        self,
        root: Path | str,
        *,
        catalog: RecipeCatalog | None = None,
        registry: AdapterRegistry | None = None,
        runner: CommandRunner = subprocess.run,
        output_root: Path | str | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.catalog = catalog or RecipeCatalog(self.root)
        self.registry = registry or default_registry()
        self.runner = runner
        self.output_root = (
            Path(output_root).resolve() if output_root is not None
            else self.root / "outputs" / "workbench"
        )

    def list_recipes(self) -> list[Recipe]:
        return self.catalog.list()

    def resolve(
        self,
        recipe_id: str,
        action: str = "run",
        overrides: Mapping[str, Any] | None = None,
        run_root: Path | str | None = None,
    ) -> ResolvedPlan:
        recipe = self.catalog.get(recipe_id)
        adapter = self.registry.get(recipe.adapter.id, recipe.adapter.version)
        path = Path(run_root) if run_root is not None else None
        if action in {"run", "benchmark"} and path is not None:
            resolved = path.resolve()
            try:
                resolved.relative_to(self.output_root)
            except ValueError as exc:
                raise WorkbenchError(
                    f"run directory must be inside {self.output_root}"
                ) from exc
            if resolved == self.output_root:
                raise WorkbenchError("run directory must be a named child of the workbench output root")
        return adapter.resolve(recipe, action, overrides, self.root, path)

    def check(
        self, recipe_id: str, overrides: Mapping[str, Any] | None = None
    ) -> list[CheckResult]:
        plan = self.resolve(recipe_id, "check", overrides)
        adapter = self.registry.get(plan.adapter.id, plan.adapter.version)
        return adapter.preflight(plan, runner=self.runner, include_validation=True)

    def preflight(self, plan: ResolvedPlan) -> list[CheckResult]:
        adapter = self.registry.get(plan.adapter.id, plan.adapter.version)
        return adapter.preflight(plan, runner=self.runner, include_validation=True)

    def estimate(
        self,
        plan: ResolvedPlan,
        benchmark_manifest: Mapping[str, Any] | Path | str | None = None,
        *,
        workers: int = 1,
    ) -> RuntimeEstimate:
        if isinstance(benchmark_manifest, (Path, str)):
            benchmark_manifest = load_manifest(benchmark_manifest)
        adapter = self.registry.get(plan.adapter.id, plan.adapter.version)
        return adapter.estimate(plan, benchmark_manifest, workers=workers)

    @staticmethod
    def _prepare_run_root(root: Path) -> None:
        if root.exists():
            if not root.is_dir() or any(root.iterdir()):
                raise WorkbenchError(f"run directory must be new or empty: {root}")
        else:
            root.mkdir(parents=True)
        (root / "logs").mkdir()

    def execute(self, plan: ResolvedPlan, *, workers: int = 1) -> dict[str, Any]:
        if plan.action not in {"run", "benchmark"} or plan.run_root is None:
            raise WorkbenchError("only resolved run or benchmark plans can be executed")
        canonical = self.resolve(
            plan.recipe_id, plan.action, plan.controls, plan.run_root
        )
        if canonical.to_dict() != plan.to_dict():
            raise WorkbenchError(
                "refusing a modified or externally constructed plan; resolve it again"
            )
        plan = canonical
        checks = self.preflight(plan)
        blockers = [check for check in checks if check.level == "BLOCK"]
        if blockers:
            messages = "; ".join(check.message for check in blockers)
            raise WorkbenchError(f"preflight blocked execution: {messages}")
        root = plan.run_root
        self._prepare_run_root(root)
        manifest_path = root / MANIFEST_NAME
        manifest = new_manifest(plan)
        manifest["preflight"] = [check.to_dict() for check in checks]
        parallelism = effective_worker_count(workers, len(plan.steps))
        manifest["execution"] = {
            "requested_workers": int(workers),
            "effective_workers": parallelism,
            "logical_cpus": max(1, int(os.cpu_count() or 1)),
        }
        atomic_write_json(manifest_path, manifest)
        adapter = self.registry.get(plan.adapter.id, plan.adapter.version)
        start = time.monotonic()
        manifest["status"] = "RUNNING"
        add_event(manifest, "run-started", "Execution started")
        atomic_write_json(manifest_path, manifest)
        try:
            step_records: list[dict[str, Any]] = []
            for step in plan.steps:
                stdout_path = root / "logs" / f"{step.id}.stdout.log"
                stderr_path = root / "logs" / f"{step.id}.stderr.log"
                step_record: dict[str, Any] = {
                    "id": step.id, "seed": step.seed, "status": "QUEUED",
                    "started_utc": manifest["updated_utc"],
                    "argv": list(step.argv), "cwd": str(step.cwd),
                    "output_dir": str(step.output_dir) if step.output_dir else None,
                    "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                }
                manifest["steps"].append(step_record)
                step_records.append(step_record)
            atomic_write_json(manifest_path, manifest)

            def execute_step(index: int) -> tuple[int, dict[str, Any]]:
                step = plan.steps[index]
                step_record = step_records[index]
                stdout_path = Path(step_record["stdout_log"])
                stderr_path = Path(step_record["stderr_log"])
                env = os.environ.copy()
                env.update({
                    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
                })
                with stdout_path.open("x", encoding="utf-8") as stdout, stderr_path.open(
                    "x", encoding="utf-8"
                ) as stderr:
                    result = self.runner(
                        list(step.argv), cwd=str(step.cwd), env=env, text=True,
                        stdout=stdout, stderr=stderr, shell=False,
                    )
                if result.returncode != 0:
                    raise WorkbenchError(
                        f"{step.id} exited with code {result.returncode}; see {stderr_path}"
                    )
                verification = adapter.verify_step(step)
                failures = [item for item in verification if item.level == "BLOCK"]
                if failures:
                    raise WorkbenchError(
                        f"{step.id} output verification failed: "
                        + "; ".join(item.message for item in failures)
                    )
                return index, {
                    "returncode": result.returncode,
                    "verification": [item.to_dict() for item in verification],
                }

            failures: list[BaseException] = []
            with ThreadPoolExecutor(
                max_workers=parallelism, thread_name_prefix="workbench-run"
            ) as pool:
                future_map = {}
                for index, step in enumerate(plan.steps):
                    step_records[index]["status"] = "RUNNING"
                    add_event(
                        manifest, "step-started", f"Started {step.id}", step_id=step.id
                    )
                    future_map[pool.submit(execute_step, index)] = index
                atomic_write_json(manifest_path, manifest)
                for future in as_completed(future_map):
                    index = future_map[future]
                    step = plan.steps[index]
                    record = step_records[index]
                    try:
                        _, outcome = future.result()
                    except BaseException as exc:
                        record["status"] = "FAILED"
                        record["failure"] = {
                            "type": type(exc).__name__, "message": str(exc)
                        }
                        add_event(
                            manifest, "step-failed", f"Failed {step.id}", step_id=step.id
                        )
                        failures.append(exc)
                    else:
                        record.update(outcome)
                        record["status"] = "COMPLETE"
                        manifest["completed_work_units"] += 1
                        add_event(
                            manifest, "step-complete", f"Completed {step.id}", step_id=step.id
                        )
                    atomic_write_json(manifest_path, manifest)
            if failures:
                raise failures[0]
            manifest["status"] = "COMPLETE"
            manifest["elapsed_seconds"] = round(time.monotonic() - start, 6)
            add_event(manifest, "run-complete", "Execution completed")
            atomic_write_json(manifest_path, manifest)
            build_html_report(root)
            return manifest
        except BaseException as exc:
            manifest["status"] = "FAILED"
            manifest["elapsed_seconds"] = round(time.monotonic() - start, 6)
            manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            for record in manifest["steps"]:
                if record["status"] in {"RUNNING", "QUEUED"}:
                    record["status"] = "FAILED"
            add_event(manifest, "run-failed", "Execution failed", failure_type=type(exc).__name__)
            atomic_write_json(manifest_path, manifest)
            build_html_report(root)
            raise

    def status(self, run_root: Path | str) -> dict[str, Any]:
        return load_manifest(run_root)

    def build_report(self, run_root: Path | str) -> Path:
        return build_html_report(run_root)
