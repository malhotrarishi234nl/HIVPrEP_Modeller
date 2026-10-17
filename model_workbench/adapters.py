"""Allow-listed adapters that translate recipes into fixed argv plans."""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

from .recipes import RecipeError, resolve_controls
from .types import CheckResult, Recipe, ResolvedPlan, RuntimeEstimate, PlanStep
from .workers import effective_worker_count


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
PARAMETER_CONTROL_PREFIX = "parameter:"


class AdapterError(ValueError):
    """An adapter cannot safely resolve or validate the requested work."""


class WorkbenchAdapter(ABC):
    id: str
    version: str

    @abstractmethod
    def resolve(
        self,
        recipe: Recipe,
        action: str,
        overrides: Mapping[str, Any] | None,
        repo_root: Path,
        run_root: Path | None,
    ) -> ResolvedPlan:
        raise NotImplementedError

    @abstractmethod
    def preflight(
        self,
        plan: ResolvedPlan,
        *,
        runner: CommandRunner = subprocess.run,
        include_validation: bool = True,
    ) -> list[CheckResult]:
        raise NotImplementedError

    @abstractmethod
    def verify_step(self, step: PlanStep) -> list[CheckResult]:
        raise NotImplementedError

    def estimate(
        self, plan: ResolvedPlan, benchmark_manifest: Mapping[str, Any] | None,
        *, workers: int = 1,
    ) -> RuntimeEstimate:
        if not benchmark_manifest:
            return RuntimeEstimate(
                available=False, seconds_low=None, seconds_central=None,
                seconds_high=None, basis="Run a one-unit benchmark on this computer.",
                requested_work_units=plan.requested_work_units,
            )
        elapsed = benchmark_manifest.get("elapsed_seconds")
        units = benchmark_manifest.get("completed_work_units")
        if not isinstance(elapsed, (int, float)) or not isinstance(units, int) or units < 1:
            raise AdapterError("benchmark manifest has no usable elapsed work-unit result")
        parallelism = effective_worker_count(workers, plan.requested_work_units)
        batches = math.ceil(plan.requested_work_units / parallelism)
        central = float(elapsed) / units * batches
        return RuntimeEstimate(
            available=True, seconds_low=central * 0.85, seconds_central=central,
            seconds_high=central * 1.35,
            basis=(
                "Batch extrapolation from a local one-unit benchmark using up to "
                f"{parallelism} parallel workers; range is heuristic."
            ),
            requested_work_units=plan.requested_work_units,
        )


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], WorkbenchAdapter] = {}

    def register(self, adapter: WorkbenchAdapter) -> None:
        key = (adapter.id, adapter.version)
        if key in self._adapters:
            raise AdapterError(f"adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, adapter_id: str, version: str) -> WorkbenchAdapter:
        try:
            return self._adapters[(adapter_id, version)]
        except KeyError as exc:
            raise AdapterError(
                f"recipe requests unsupported adapter {adapter_id!r} version {version!r}"
            ) from exc


def _safe_repo_input(repo_root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise AdapterError("recipe input paths must be relative to the repository")
    path = (repo_root / relative).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise AdapterError(f"recipe input escapes repository: {relative}") from exc
    return path


class M0M1SimulationAdapter(WorkbenchAdapter):
    id = "simulation.m0_m1"
    version = "1"

    @staticmethod
    def _model(recipe: Recipe) -> str:
        try:
            model = recipe.controls["model"]
        except KeyError as exc:
            raise AdapterError("simulation recipe must declare a model control") from exc
        if model.type != "enum" or len(model.choices) != 1 or model.default not in {"M0", "M1"}:
            raise AdapterError("simulation recipe must fix model to exactly M0 or M1")
        return str(model.default)

    @staticmethod
    def _argv(
        repo_root: Path, config: Path, model: str, seed: int, p: float | None,
        output_dir: Path | None, parameter_values: Mapping[str, float],
    ) -> tuple[str, ...]:
        argv = [
            sys.executable, str(repo_root / "run_model.py"), "--config", str(config),
            "--model", model, "--seed", str(seed),
        ]
        if p is not None:
            argv.extend(("--p", format(p, ".17g")))
        for path, value in sorted(parameter_values.items()):
            argv.extend(("--set", f"{path}={format(value, '.17g')}"))
        if output_dir is None:
            argv.append("--validate-only")
        else:
            argv.extend(("--output-dir", str(output_dir), "--research-run"))
        return tuple(argv)

    @staticmethod
    def _parameter_controls(
        repo_root: Path, config: Path, overrides: Mapping[str, Any],
    ) -> dict[str, float]:
        """Resolve every contract parameter to an explicit, bounded plan value."""
        contract_path = repo_root / "configs" / "collaborator_parameter_contract.json"
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            base = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"cannot read collaborator parameter controls: {exc}") from exc
        rules = {
            str(item["path"]): item for item in contract.get("parameters", [])
            if isinstance(item, Mapping) and "path" in item
        }
        if not rules:
            raise AdapterError("collaborator parameter contract has no controls")
        unknown = set(overrides) - set(rules)
        if unknown:
            raise RecipeError(f"unsupported parameter controls: {sorted(unknown)}")

        def value_at(path: str) -> Any:
            node: Any = base
            for part in path.split("."):
                if not isinstance(node, Mapping) or part not in node:
                    raise AdapterError(f"configuration is missing parameter {path}")
                node = node[part]
            return node

        values: dict[str, float] = {}
        for path, rule in rules.items():
            raw = overrides.get(path, value_at(path))
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
                    or not math.isfinite(float(raw)):
                raise RecipeError(f"parameter control {path!r} must be finite numeric")
            value = float(raw)
            lower = float(rule["minimum"])
            upper = float(rule["maximum"])
            if not lower <= value <= upper:
                raise RecipeError(
                    f"parameter control {path!r} must be in [{lower}, {upper}]"
                )
            values[path] = value

        for path, rule in rules.items():
            linked = rule.get("must_equal")
            if not linked:
                continue
            linked = str(linked)
            path_explicit = path in overrides
            linked_explicit = linked in overrides
            if path_explicit and linked_explicit and values[path] != values[linked]:
                raise RecipeError(
                    f"parameter controls {path!r} and {linked!r} must be equal"
                )
            if path_explicit:
                values[linked] = values[path]
            elif linked_explicit:
                values[path] = values[linked]
            elif values[path] != values[linked]:
                raise AdapterError(
                    f"coupled configuration parameters differ: {path} and {linked}"
                )
        return values

    def resolve(
        self,
        recipe: Recipe,
        action: str,
        overrides: Mapping[str, Any] | None,
        repo_root: Path,
        run_root: Path | None,
    ) -> ResolvedPlan:
        if action not in {"check", "benchmark", "run"}:
            raise AdapterError(f"unsupported action: {action}")
        repo_root = repo_root.resolve()
        raw_overrides = dict(overrides or {})
        parameter_overrides = {
            key[len(PARAMETER_CONTROL_PREFIX):]: value
            for key, value in raw_overrides.items()
            if key.startswith(PARAMETER_CONTROL_PREFIX)
        }
        base_overrides = {
            key: value for key, value in raw_overrides.items()
            if not key.startswith(PARAMETER_CONTROL_PREFIX)
        }
        controls = resolve_controls(recipe, base_overrides)
        expected = {"model", "seed", "replicates"}
        model = self._model(recipe)
        if model == "M1":
            expected.add("p")
        if set(controls) != expected:
            raise AdapterError(
                f"simulation recipe controls must be exactly {sorted(expected)}"
            )
        if recipe.environment_profile != "operator":
            raise AdapterError("simulation adapter requires the operator environment profile")
        if set(recipe.inputs) != {"config"}:
            raise AdapterError("simulation recipe must declare exactly one config input")
        config = _safe_repo_input(repo_root, recipe.inputs["config"].path)
        parameter_values = self._parameter_controls(
            repo_root, config, parameter_overrides
        )
        controls.update({
            f"{PARAMETER_CONTROL_PREFIX}{path}": value
            for path, value in parameter_values.items()
        })
        seed = int(controls["seed"])
        requested = int(controls["replicates"])
        planned = 1 if action in {"check", "benchmark"} else requested
        if action != "check" and run_root is None:
            raise AdapterError(f"{action} requires a run_root")
        resolved_run_root = run_root.resolve() if run_root else None
        p = float(controls["p"]) if model == "M1" else None
        steps: list[PlanStep] = []
        for index in range(planned):
            step_seed = seed + index
            if step_seed > 2**32 - 1:
                raise RecipeError("replicate seeds exceed the uint32 maximum")
            output_dir = (
                None if action == "check" else
                resolved_run_root / "engine" / f"seed_{step_seed:010d}"  # type: ignore[operator]
            )
            steps.append(PlanStep(
                id=f"seed-{step_seed}",
                argv=self._argv(
                    repo_root, config, model, step_seed, p, output_dir,
                    parameter_values,
                ),
                cwd=repo_root,
                output_dir=output_dir,
                expected_files=(
                    "run_reproducibility_manifest.json",
                    "research_not_production_eligible_run_001.csv",
                ) if output_dir else (),
                seed=step_seed,
            ))
        return ResolvedPlan(
            schema="model_workbench_plan/1.0", recipe_id=recipe.id,
            recipe_title=recipe.title, adapter=recipe.adapter, action=action, controls=controls,
            inputs={"config": str(config)},
            input_sha256={"config": recipe.inputs["config"].sha256}, repo_root=repo_root,
            run_root=resolved_run_root, steps=tuple(steps),
            requested_work_units=requested, planned_work_units=planned,
            report_profile=recipe.report_profile,
        )

    def _validation_argv(self, plan: ResolvedPlan) -> Sequence[str]:
        model = str(plan.controls["model"])
        p = float(plan.controls["p"]) if model == "M1" else None
        parameter_values = {
            key[len(PARAMETER_CONTROL_PREFIX):]: float(value)
            for key, value in plan.controls.items()
            if key.startswith(PARAMETER_CONTROL_PREFIX)
        }
        return self._argv(
            plan.repo_root, Path(plan.inputs["config"]), model,
            int(plan.controls["seed"]), p, None, parameter_values,
        )

    def preflight(
        self,
        plan: ResolvedPlan,
        *,
        runner: CommandRunner = subprocess.run,
        include_validation: bool = True,
    ) -> list[CheckResult]:
        checks: list[CheckResult] = []
        executable = plan.repo_root / "run_model.py"
        checks.append(CheckResult(
            "entrypoint", "PASS" if executable.is_file() else "BLOCK",
            "run_model.py is available" if executable.is_file() else "run_model.py is missing",
            {"path": str(executable)},
        ))
        config = Path(plan.inputs["config"])
        expected_hash = plan.input_sha256.get("config")
        actual_hash = None
        if config.is_file():
            try:
                actual_hash = hashlib.sha256(config.read_bytes()).hexdigest()
            except OSError:
                actual_hash = None
        config_ok = config.is_file() and (
            expected_hash is None or actual_hash == expected_hash
        )
        checks.append(CheckResult(
            "config", "PASS" if config_ok else "BLOCK",
            "Configuration file and identity match" if config_ok else "Configuration file is missing or its identity changed",
            {"path": str(config), "expected_sha256": expected_hash,
             "actual_sha256": actual_hash},
        ))
        py_ok = sys.version_info[:2] == (3, 11)
        checks.append(CheckResult(
            "python", "PASS" if py_ok else "BLOCK",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            {"required": "CPython 3.11.x", "executable": sys.executable},
        ))
        requirements_path = plan.repo_root / "requirements-operator.txt"
        requirements: dict[str, str] = {}
        try:
            for line in requirements_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", stripped)
                if match is None:
                    raise ValueError(f"unsupported requirement line: {stripped}")
                requirements[match.group(1)] = match.group(2)
            if not requirements:
                raise ValueError("no pinned dependencies found")
            checks.append(CheckResult(
                "requirements", "PASS", "Operator requirements are exact pins",
                {"path": str(requirements_path), "packages": requirements},
            ))
        except (OSError, ValueError) as exc:
            checks.append(CheckResult(
                "requirements", "BLOCK", f"Cannot read exact operator requirements: {exc}",
                {"path": str(requirements_path)},
            ))
        for package, expected in requirements.items():
            try:
                actual = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                actual = None
            checks.append(CheckResult(
                f"dependency:{package}", "PASS" if actual == expected else "BLOCK",
                f"{package} {actual or 'is not installed'}",
                {"required": expected, "actual": actual},
            ))
        if plan.run_root is not None:
            root = plan.run_root
            usable = not root.exists() or (root.is_dir() and not any(root.iterdir()))
            checks.append(CheckResult(
                "run-root", "PASS" if usable else "BLOCK",
                "Run directory is fresh" if usable else "Run directory exists and is not empty",
                {"path": str(root)},
            ))
        if include_validation and executable.is_file() and config.is_file():
            try:
                result = runner(
                    list(self._validation_argv(plan)), cwd=str(plan.repo_root),
                    text=True, capture_output=True, timeout=120, shell=False,
                )
                level = "PASS" if result.returncode == 0 else "BLOCK"
                message = (
                    "Model route validated without constructing the engine"
                    if result.returncode == 0 else "Model route validation failed"
                )
                checks.append(CheckResult(
                    "model-validation", level, message,
                    {"returncode": result.returncode, "stdout": result.stdout[-2000:],
                     "stderr": result.stderr[-2000:]},
                ))
            except (OSError, subprocess.SubprocessError) as exc:
                checks.append(CheckResult(
                    "model-validation", "BLOCK", f"Could not validate model route: {exc}"
                ))
        return checks

    def verify_step(self, step: PlanStep) -> list[CheckResult]:
        if step.output_dir is None:
            return []
        checks: list[CheckResult] = []
        for relative in step.expected_files:
            path = step.output_dir / relative
            valid = path.is_file() and path.stat().st_size > 0
            checks.append(CheckResult(
                f"output:{relative}", "PASS" if valid else "BLOCK",
                f"Created {relative}" if valid else f"Missing or empty {relative}",
                {"path": str(path)},
            ))
        manifest_path = step.output_dir / "run_reproducibility_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                complete = manifest.get("status") == "COMPLETE"
            except (OSError, json.JSONDecodeError):
                complete = False
            checks.append(CheckResult(
                "child-manifest", "PASS" if complete else "BLOCK",
                "Engine manifest is COMPLETE" if complete else "Engine manifest is not COMPLETE",
                {"path": str(manifest_path)},
            ))
        return checks


def default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(M0M1SimulationAdapter())
    return registry
