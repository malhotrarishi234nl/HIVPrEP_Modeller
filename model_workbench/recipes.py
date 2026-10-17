"""Strict recipe loading and typed control resolution."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from .types import AdapterRef, ControlSpec, InputRef, Recipe


RECIPE_SCHEMA = "model_workbench_recipe/1.0"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_TOP_FIELDS = {
    "schema", "id", "title", "category", "description", "adapter", "inputs",
    "controls", "environment_profile", "report_profile",
}
_CONTROL_FIELDS = {
    "type", "default", "label", "minimum", "maximum", "choices", "help",
}


class RecipeError(ValueError):
    """A recipe is malformed or an override violates its declared contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecipeError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise RecipeError(f"non-finite JSON value is not allowed: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise RecipeError(f"non-finite JSON value is not allowed: {value}")
    return parsed


def _require_fields(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise RecipeError(f"{label} has unsupported fields: {sorted(unknown)}")


def _control_from_dict(name: str, raw: Any) -> ControlSpec:
    if not isinstance(raw, dict):
        raise RecipeError(f"control {name!r} must be an object")
    _require_fields(raw, _CONTROL_FIELDS, f"control {name!r}")
    control_type = raw.get("type")
    if control_type not in {"integer", "number", "enum"}:
        raise RecipeError(f"control {name!r} has unsupported type {control_type!r}")
    if "default" not in raw or not isinstance(raw.get("label"), str) or not raw["label"].strip():
        raise RecipeError(f"control {name!r} requires default and label")
    if not isinstance(raw.get("help", ""), str):
        raise RecipeError(f"control {name!r} help must be text")
    choices = raw.get("choices", [])
    if not isinstance(choices, list) or not all(isinstance(v, str) for v in choices):
        raise RecipeError(f"control {name!r} choices must be strings")
    minimum = raw.get("minimum")
    maximum = raw.get("maximum")
    for boundary_name, boundary in (("minimum", minimum), ("maximum", maximum)):
        if boundary is not None and (
            isinstance(boundary, bool) or not isinstance(boundary, (int, float))
            or not math.isfinite(float(boundary))
        ):
            raise RecipeError(f"control {name!r} {boundary_name} must be finite numeric")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise RecipeError(f"control {name!r} minimum exceeds maximum")
    if control_type == "enum" and not choices:
        raise RecipeError(f"enum control {name!r} requires choices")
    if control_type != "enum" and choices:
        raise RecipeError(f"numeric control {name!r} cannot declare choices")
    spec = ControlSpec(
        type=control_type,
        default=raw["default"],
        label=raw["label"],
        minimum=minimum,
        maximum=maximum,
        choices=tuple(choices),
        help=raw.get("help", ""),
    )
    _validate_control_value(name, spec.default, spec)
    return spec


def _validate_control_value(
    name: str, value: Any, spec: ControlSpec
) -> int | float | str:
    if spec.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise RecipeError(f"control {name!r} must be an integer")
        checked: int | float | str = value
    elif spec.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecipeError(f"control {name!r} must be a number")
        if not math.isfinite(float(value)):
            raise RecipeError(f"control {name!r} must be finite")
        checked = float(value)
    else:
        if not isinstance(value, str) or value not in spec.choices:
            raise RecipeError(
                f"control {name!r} must be one of {list(spec.choices)!r}"
            )
        checked = value
    if spec.minimum is not None and checked < spec.minimum:  # type: ignore[operator]
        raise RecipeError(f"control {name!r} must be >= {spec.minimum}")
    if spec.maximum is not None and checked > spec.maximum:  # type: ignore[operator]
        raise RecipeError(f"control {name!r} must be <= {spec.maximum}")
    return checked


def load_recipe(path: Path) -> Recipe:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"cannot load recipe {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RecipeError(f"recipe {path} must contain an object")
    _require_fields(raw, _TOP_FIELDS, f"recipe {path.name}")
    missing = _TOP_FIELDS - set(raw)
    if missing:
        raise RecipeError(f"recipe {path.name} is missing fields: {sorted(missing)}")
    if raw["schema"] != RECIPE_SCHEMA:
        raise RecipeError(f"recipe {path.name} has unsupported schema")
    if not isinstance(raw["id"], str) or not _ID_RE.fullmatch(raw["id"]):
        raise RecipeError(f"recipe {path.name} has invalid id")
    for field in (
        "title", "category", "description", "environment_profile", "report_profile"
    ):
        if not isinstance(raw[field], str) or not raw[field].strip():
            raise RecipeError(f"recipe {path.name} field {field!r} must be text")
    adapter = raw["adapter"]
    if not isinstance(adapter, dict) or set(adapter) != {"id", "version"}:
        raise RecipeError("adapter must contain exactly id and version")
    if not all(isinstance(adapter[key], str) and adapter[key] for key in adapter):
        raise RecipeError("adapter id and version must be strings")
    inputs: dict[str, InputRef] = {}
    if not isinstance(raw["inputs"], dict):
        raise RecipeError("inputs must be an object")
    for name, value in raw["inputs"].items():
        if not isinstance(value, dict) or not set(value) <= {"path", "sha256"}:
            raise RecipeError(f"input {name!r} must contain path and optional sha256")
        if not isinstance(value.get("path"), str) or not value["path"].strip():
            raise RecipeError(f"input {name!r} requires a path")
        sha256 = value.get("sha256")
        if sha256 is not None and (
            not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise RecipeError(f"input {name!r} has invalid sha256")
        inputs[name] = InputRef(path=value["path"], sha256=sha256)
    if not isinstance(raw["controls"], dict):
        raise RecipeError("controls must be an object")
    controls = {
        name: _control_from_dict(name, value)
        for name, value in raw["controls"].items()
    }
    return Recipe(
        schema=raw["schema"], id=raw["id"], title=raw["title"],
        category=raw["category"], description=raw["description"],
        adapter=AdapterRef(**adapter), inputs=inputs, controls=controls,
        environment_profile=raw["environment_profile"],
        report_profile=raw["report_profile"],
    )


def resolve_controls(
    recipe: Recipe, overrides: Mapping[str, Any] | None = None
) -> dict[str, int | float | str]:
    overrides = overrides or {}
    unknown = set(overrides) - set(recipe.controls)
    if unknown:
        raise RecipeError(f"unsupported controls for {recipe.id}: {sorted(unknown)}")
    return {
        name: _validate_control_value(name, overrides.get(name, spec.default), spec)
        for name, spec in recipe.controls.items()
    }


class RecipeCatalog:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.directory = Path(__file__).resolve().parent / "recipe_definitions"

    def list(self) -> list[Recipe]:
        recipes = [load_recipe(path) for path in sorted(self.directory.glob("*.json"))]
        seen: set[str] = set()
        for recipe in recipes:
            if recipe.id in seen:
                raise RecipeError(f"duplicate recipe id: {recipe.id}")
            seen.add(recipe.id)
        return recipes

    def get(self, recipe_id: str) -> Recipe:
        for recipe in self.list():
            if recipe.id == recipe_id:
                return recipe
        raise RecipeError(f"unknown recipe: {recipe_id}")
