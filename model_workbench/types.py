"""Typed, serialisable contracts for the local model workbench."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


Action = Literal["check", "benchmark", "run"]
CheckLevel = Literal["PASS", "WARN", "BLOCK"]


@dataclass(frozen=True)
class AdapterRef:
    id: str
    version: str


@dataclass(frozen=True)
class InputRef:
    path: str
    sha256: str | None = None


@dataclass(frozen=True)
class ControlSpec:
    type: Literal["integer", "number", "enum"]
    default: int | float | str
    label: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""


@dataclass(frozen=True)
class Recipe:
    schema: str
    id: str
    title: str
    category: str
    description: str
    adapter: AdapterRef
    inputs: Mapping[str, InputRef]
    controls: Mapping[str, ControlSpec]
    environment_profile: str
    report_profile: str


@dataclass(frozen=True)
class CheckResult:
    id: str
    level: CheckLevel
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanStep:
    id: str
    argv: tuple[str, ...]
    cwd: Path
    output_dir: Path | None = None
    expected_files: tuple[str, ...] = ()
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "expected_files": list(self.expected_files),
            "seed": self.seed,
        }


@dataclass(frozen=True)
class ResolvedPlan:
    schema: str
    recipe_id: str
    recipe_title: str
    adapter: AdapterRef
    action: Action
    controls: Mapping[str, int | float | str]
    inputs: Mapping[str, str]
    input_sha256: Mapping[str, str | None]
    repo_root: Path
    run_root: Path | None
    steps: tuple[PlanStep, ...]
    requested_work_units: int
    planned_work_units: int
    report_profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "recipe_id": self.recipe_id,
            "recipe_title": self.recipe_title,
            "adapter": asdict(self.adapter),
            "action": self.action,
            "controls": dict(self.controls),
            "inputs": dict(self.inputs),
            "input_sha256": dict(self.input_sha256),
            "repo_root": str(self.repo_root),
            "run_root": str(self.run_root) if self.run_root else None,
            "steps": [step.to_dict() for step in self.steps],
            "requested_work_units": self.requested_work_units,
            "planned_work_units": self.planned_work_units,
            "report_profile": self.report_profile,
        }


@dataclass(frozen=True)
class RuntimeEstimate:
    available: bool
    seconds_low: float | None
    seconds_central: float | None
    seconds_high: float | None
    basis: str
    requested_work_units: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
