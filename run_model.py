#!/usr/bin/env python3
"""Run the ordinary model with an explicit M0 or M1 diagnosis route.

This is the small operator-facing wrapper around ``SimulationEngineV3``.  It
does not calibrate, search, or choose parameters.  Collaborators can edit a
copy of the JSON config, validate the chosen route, and run it without editing
Python code.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import platform
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

from config_reader import ConfigReader
from config_utils import extract_value
import stage_structured_diagnosis as ssd


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "configs" / (
    "V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
)
PARAMETER_CONTRACT_PATH = ROOT / "configs" / "collaborator_parameter_contract.json"
PARAMETER_CONTRACT_SCHEMA = "collaborator_parameter_contract/1.0.0"
REQUIREMENTS_OPERATOR_PATH = ROOT / "requirements-operator.txt"
CANONICAL_CONFIG_RELATIVE = (
    "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
)
UINT32_MAX = 2**32 - 1


class OperatorConfigError(ValueError):
    """A plain-language problem in the collaborator-facing run request."""


PROTECTED_ROOTS = {
    "_readme", "meta", "run_control", "output", "v13_14_diagnostic",
    "calibration_metadata", "production_approval",
}

PRODUCTION_APPROVAL_SCHEMA = "model_config_production_approval/1.2.0"
PRODUCTION_APPROVAL_FIELDS = {
    "schema", "status", "production_eligible", "approved", "approved_by",
    "approved_utc", "collaborator_parameter_contract_sha256",
    "scientific_files_sha256", "requirements_operator_sha256",
}

ANALYSIS_RUN_SHEET_SCHEMA = "collaborator_analysis_run_sheet/1.1.0"
ANALYSIS_RUN_SHEET_FIELDS = {
    "schema", "analysis_config_sha256",
    "collaborator_parameter_contract_sha256", "model", "p", "seeds",
    "output_directory",
    "scientific_files_sha256", "requirements_operator_sha256",
    "scientific_approved_by", "scientific_approved_utc",
    "dispatch_authorized_by", "dispatch_authorized_utc",
}

EFFECTIVE_CONFIG_FILENAME = "effective_config.json"
RUN_MANIFEST_FILENAME = "run_reproducibility_manifest.json"
RUN_SHEET_SNAPSHOT_FILENAME = "approved_analysis_run_sheet.json"
RUN_MANIFEST_SCHEMA = "model_run_reproducibility/1.1.0"
PROTECTED_SCIENTIFIC_OUTPUT = "baseline_run_001.csv"
RESEARCH_RUN_TAG = "research_not_production_eligible"
RESEARCH_SCIENTIFIC_OUTPUT = f"{RESEARCH_RUN_TAG}_run_001.csv"
RESEARCH_NOTICE_FILENAME = "RESEARCH_NOT_PRODUCTION_ELIGIBLE.json"
RESEARCH_NOTICE_SCHEMA = "model_research_run_notice/1.0.0"
RESEARCH_CLASSIFICATION = "RESEARCH_NOT_PRODUCTION_ELIGIBLE"

# Direct scientific implementation inputs for the ordinary model route.  The
# sidecar hashes every file, rather than treating a Git commit as sufficient
# when a researcher is deliberately working from a dirty checkout.
MODEL_CODE_FILES = (
    "run_model.py", "run_v3.py", "engine_v3.py", "config_reader.py",
    "config_utils.py", "transmission_corrected.py", "demography.py",
    "migration.py", "schedule_utils.py", "aids_observation.py",
    "exogenous_diagnoses.py", "output_writer.py",
    "stage_structured_diagnosis.py", "stochastic_allocation.py",
    "counter_based_rng.py",
)

OPERATOR_REQUIREMENT_DISTRIBUTIONS = ("numpy", "pandas", "scipy")


class _DuplicateJSONKey(ValueError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class _NonstandardJSONConstant(ValueError):
    def __init__(self, value: str):
        super().__init__(value)
        self.value = value


def _strict_json_loads(raw: str | bytes, *, label: str) -> Any:
    """Parse authority JSON, rejecting duplicates and every non-finite value.

    Python's default decoder silently keeps the last duplicate object key and
    accepts non-standard numeric constants. Neither behaviour is safe for a
    signed/approved operator input.
    """

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJSONKey(key)
            result[key] = value
        return result

    def strict_constant(value: str) -> Any:
        raise _NonstandardJSONConstant(value)

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _NonstandardJSONConstant(value)
        return parsed

    try:
        return json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=strict_constant,
            parse_float=finite_float,
        )
    except _DuplicateJSONKey as exc:
        raise OperatorConfigError(
            f"{label} is not strict JSON: duplicate object key {exc.key!r}"
        ) from exc
    except _NonstandardJSONConstant as exc:
        raise OperatorConfigError(
            f"{label} is not strict JSON: non-standard constant {exc.value!r}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        line = getattr(exc, "lineno", "unknown")
        message = getattr(exc, "msg", str(exc))
        raise OperatorConfigError(
            f"{label} is not valid JSON (line {line}): {message}"
        ) from exc


def load_shared_config(path: str | Path) -> dict:
    """Load and validate the protected shared-config fields without an engine."""
    payload, _ = _load_shared_config_and_hash(path)
    return payload


def _load_shared_config_and_hash(path: str | Path) -> tuple[dict, str]:
    """Read once so the returned payload and input-file hash describe one file."""
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(f"cannot read config file: {config_path}") from exc
    payload = _strict_json_loads(raw, label=f"operator config {config_path}")
    if not isinstance(payload, dict):
        raise OperatorConfigError("config must contain one JSON object")

    try:
        canonical_raw = DEFAULT_CONFIG.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(
            f"canonical config is unavailable or unreadable: {DEFAULT_CONFIG}"
        ) from exc
    canonical = _strict_json_loads(
        canonical_raw, label=f"canonical config {DEFAULT_CONFIG}"
    )
    if not isinstance(canonical, dict):
        raise OperatorConfigError("canonical config must contain one JSON object")
    parameter_rules, _ = _load_parameter_contract(canonical)
    forbidden = _first_forbidden_edit(
        payload, canonical, editable_paths=frozenset(parameter_rules)
    )
    if forbidden is not None:
        raise OperatorConfigError(
            f"forbidden config edit at {_format_path(forbidden)}; collaborator "
            "copies may change only existing scientific parameter value fields. "
            f"Start from a fresh canonical copy: {DEFAULT_CONFIG}"
        )
    range_problem = _first_declared_range_problem(payload, canonical)
    if range_problem is not None:
        raise OperatorConfigError(range_problem)
    contract_problem = _first_parameter_contract_problem(
        payload, canonical, parameter_rules
    )
    if contract_problem is not None:
        raise OperatorConfigError(contract_problem)

    deterministic = payload.get("run_control", {}).get("deterministic_mode")
    deterministic = extract_value(deterministic)
    if deterministic is not False:
        raise OperatorConfigError(
            "run_control.deterministic_mode.value must be false in the shared "
            "stochastic config"
        )
    try:
        profile = ssd.validate_shared_base_config(payload)
        # Exercise the same V10-to-engine config translation used at runtime.
        translated = ConfigReader(payload).as_v6_dict()
        translated_profile = ssd.load_stage_structured_diagnosis(translated)
    except (ssd.StageStructuredDiagnosisConfigError, KeyError, TypeError, ValueError) as exc:
        raise OperatorConfigError(str(exc)) from exc
    if translated_profile != profile:
        raise OperatorConfigError(
            "ConfigReader changed the stage_structured_diagnosis block; the config "
            "is not safe to run"
        )
    return payload, hashlib.sha256(raw).hexdigest()


def _first_forbidden_edit(
    candidate: Any, canonical: Any, path: tuple[str | int, ...] = (),
    *, editable_paths: frozenset[tuple[str | int, ...]] = frozenset(),
) -> tuple[str | int, ...] | None:
    """Return the first edit outside the exact collaborator contract paths."""

    if isinstance(canonical, dict):
        if not isinstance(candidate, dict):
            return path
        for key in canonical:
            if key not in candidate:
                return path + (key,)
        for key in sorted(set(candidate).difference(canonical)):
            return path + (key,)
        for key, canonical_value in canonical.items():
            child_path = path + (key,)
            found = _first_forbidden_edit(
                candidate[key], canonical_value, child_path,
                editable_paths=editable_paths,
            )
            if found is not None:
                return found
        return None

    if isinstance(canonical, list):
        if not isinstance(candidate, list) or len(candidate) != len(canonical):
            return path
        for index, canonical_value in enumerate(canonical):
            found = _first_forbidden_edit(
                candidate[index], canonical_value, path + (index,),
                editable_paths=editable_paths,
            )
            if found is not None:
                return found
        return None

    if _same_json_scalar_value(candidate, canonical):
        return None
    if path in editable_paths:
        return None
    return path


def _protected_path(path: tuple[str | int, ...]) -> bool:
    if path and path[0] in PROTECTED_ROOTS:
        return True
    profile = ssd.PROFILE_PATH
    return len(path) >= len(profile) and tuple(path[:len(profile)]) == profile


def _same_json_scalar_type(candidate: Any, canonical: Any) -> bool:
    if isinstance(canonical, bool):
        return isinstance(candidate, bool)
    if isinstance(canonical, (int, float)):
        return isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
    if canonical is None:
        return candidate is None
    return type(candidate) is type(canonical)


def _same_json_scalar_value(candidate: Any, canonical: Any) -> bool:
    return _same_json_scalar_type(candidate, canonical) and candidate == canonical


def _first_declared_range_problem(
    candidate: Any, canonical: Any, path: tuple[str | int, ...] = (),
) -> str | None:
    """Validate changed editable values against co-located declared limits.

    No limit is inferred from notes, names, probabilities, or neighbouring
    fields.  Only explicit numeric ``bounds``, ``range``, ``min``/``max`` or
    ``minimum``/``maximum`` metadata in the canonical parameter object counts.
    """
    if isinstance(canonical, dict):
        if isinstance(candidate, dict) and "value" in canonical and "value" in candidate:
            value_path = path + ("value",)
            if not _protected_path(value_path) and candidate["value"] != canonical["value"]:
                try:
                    interval = _declared_interval(canonical, path)
                except OperatorConfigError as exc:
                    return str(exc)
                if interval is not None:
                    lower, upper, label = interval
                    value = candidate["value"]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) \
                            or not math.isfinite(float(value)):
                        return (
                            f"edited {_format_path(value_path)} must be a finite number to "
                            f"validate its declared {label}"
                        )
                    numeric = float(value)
                    if (lower is not None and numeric < lower) or \
                            (upper is not None and numeric > upper):
                        lower_text = "-inf" if lower is None else repr(lower)
                        upper_text = "+inf" if upper is None else repr(upper)
                        return (
                            f"edited {_format_path(value_path)}={value!r} is outside its "
                            f"declared {label} [{lower_text}, {upper_text}]"
                        )
        if not isinstance(candidate, dict):
            return None
        for key, canonical_value in canonical.items():
            if key in candidate:
                problem = _first_declared_range_problem(
                    candidate[key], canonical_value, path + (key,)
                )
                if problem is not None:
                    return problem
    elif isinstance(canonical, list) and isinstance(candidate, list):
        for index, canonical_value in enumerate(canonical):
            if index < len(candidate):
                problem = _first_declared_range_problem(
                    candidate[index], canonical_value, path + (index,)
                )
                if problem is not None:
                    return problem
    return None


def _declared_interval(
    parameter: Mapping[str, Any], path: tuple[str | int, ...],
) -> tuple[float | None, float | None, str] | None:
    declarations: list[tuple[float | None, float | None, str]] = []
    for key in ("bounds", "range"):
        if key not in parameter:
            continue
        raw = parameter[key]
        if isinstance(raw, Mapping):
            lower_raw = next((raw[name] for name in ("min", "minimum", "lower")
                              if name in raw), None)
            upper_raw = next((raw[name] for name in ("max", "maximum", "upper")
                              if name in raw), None)
            if lower_raw is None and upper_raw is None:
                raise OperatorConfigError(
                    f"canonical {_format_path(path)}.{key} has no numeric endpoints; "
                    "the edited value cannot be validated"
                )
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            lower_raw, upper_raw = raw
        else:
            raise OperatorConfigError(
                f"canonical {_format_path(path)}.{key} is not a two-endpoint range; "
                "the edited value cannot be validated"
            )
        declarations.append((
            _numeric_endpoint(lower_raw, path + (key, 0), allow_none=True),
            _numeric_endpoint(upper_raw, path + (key, 1), allow_none=True),
            key,
        ))

    lower_key = next((key for key in ("min", "minimum") if key in parameter), None)
    upper_key = next((key for key in ("max", "maximum") if key in parameter), None)
    if lower_key is not None or upper_key is not None:
        lower = _numeric_endpoint(
            parameter.get(lower_key) if lower_key else None,
            path + (lower_key or "min",), allow_none=True,
        )
        upper = _numeric_endpoint(
            parameter.get(upper_key) if upper_key else None,
            path + (upper_key or "max",), allow_none=True,
        )
        declarations.append((lower, upper, "min/max"))

    if not declarations:
        return None
    first = declarations[0]
    for other in declarations[1:]:
        if other[:2] != first[:2]:
            raise OperatorConfigError(
                f"canonical {_format_path(path)} has conflicting declared ranges; "
                "the edited value cannot be validated"
            )
    lower, upper, label = first
    if lower is not None and upper is not None and lower > upper:
        raise OperatorConfigError(
            f"canonical {_format_path(path)} has reversed declared {label}; "
            "the edited value cannot be validated"
        )
    return lower, upper, label


def _numeric_endpoint(
    value: Any, path: tuple[str | int, ...], *, allow_none: bool,
) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise OperatorConfigError(
            f"canonical declared range endpoint {_format_path(path)} is not a finite number"
        )
    return float(value)


def _format_path(path: tuple[str | int, ...]) -> str:
    if not path:
        return "<root>"
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + part
    return text


def _config_without_approval_sha256(config: Mapping[str, Any]) -> str:
    payload = dict(config)
    payload.pop("production_approval", None)
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise OperatorConfigError(
            "canonical config cannot be hashed as strict JSON for the collaborator "
            "parameter contract"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _contract_path(raw: Any, *, field: str = "path") -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        raise OperatorConfigError(f"parameter contract {field} must be a non-empty path")
    parts = tuple(raw.split("."))
    if any(not part for part in parts):
        raise OperatorConfigError(f"parameter contract {field} is malformed: {raw!r}")
    return parts


def _value_at_path(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = payload
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            raise OperatorConfigError(
                f"parameter contract path is absent from the canonical config: "
                f"{_format_path(path)}"
            )
        node = node[part]
    return node


def _load_parameter_contract(
    canonical: Mapping[str, Any],
) -> tuple[dict[tuple[str, ...], dict[str, Any]], str]:
    """Validate and return the exact bounded collaborator-edit contract."""
    try:
        raw = PARAMETER_CONTRACT_PATH.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(
            f"collaborator parameter contract is missing or unreadable: "
            f"{PARAMETER_CONTRACT_PATH}"
        ) from exc
    payload = _strict_json_loads(raw, label="collaborator parameter contract")
    if not isinstance(payload, dict):
        raise OperatorConfigError("collaborator parameter contract must be one JSON object")
    expected_top = {
        "schema", "canonical_config", "canonical_config_without_approval_sha256",
        "approval_scope", "parameters",
    }
    if set(payload) != expected_top:
        raise OperatorConfigError(
            "collaborator parameter contract has unexpected or missing top-level fields"
        )
    if payload["schema"] != PARAMETER_CONTRACT_SCHEMA:
        raise OperatorConfigError(
            f"collaborator parameter contract schema must be {PARAMETER_CONTRACT_SCHEMA!r}"
        )
    if payload["canonical_config"] != CANONICAL_CONFIG_RELATIVE:
        raise OperatorConfigError(
            "collaborator parameter contract names the wrong canonical config"
        )
    scope = payload["approval_scope"]
    if not isinstance(scope, str) or not scope.strip():
        raise OperatorConfigError(
            "collaborator parameter contract approval_scope must be non-empty"
        )
    declared_config_hash = payload["canonical_config_without_approval_sha256"]
    actual_config_hash = _config_without_approval_sha256(canonical)
    if declared_config_hash != actual_config_hash:
        raise OperatorConfigError(
            "collaborator parameter contract does not match the canonical config "
            f"without production_approval (declared {declared_config_hash!r}, "
            f"actual {actual_config_hash!r})"
        )
    raw_rules = payload["parameters"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise OperatorConfigError(
            "collaborator parameter contract parameters must be a non-empty list"
        )

    rules: dict[tuple[str, ...], dict[str, Any]] = {}
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise OperatorConfigError(
                f"parameter contract rule {index} must be one JSON object"
            )
        required = {"path", "type", "minimum", "maximum", "unit", "notes"}
        allowed = required | {"must_equal"}
        if not required.issubset(raw_rule) or not set(raw_rule).issubset(allowed):
            raise OperatorConfigError(
                f"parameter contract rule {index} has unexpected or missing fields"
            )
        path = _contract_path(raw_rule["path"])
        if path in rules:
            raise OperatorConfigError(
                f"parameter contract repeats path {_format_path(path)}"
            )
        if path[-1] != "value" or _protected_path(path):
            raise OperatorConfigError(
                f"parameter contract path is not an editable scientific value: "
                f"{_format_path(path)}"
            )
        if raw_rule["type"] != "number":
            raise OperatorConfigError(
                f"parameter contract {_format_path(path)} supports only type 'number'"
            )
        lower = raw_rule["minimum"]
        upper = raw_rule["maximum"]
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in (lower, upper)
        ) or float(lower) > float(upper):
            raise OperatorConfigError(
                f"parameter contract {_format_path(path)} has invalid closed bounds"
            )
        if not isinstance(raw_rule["unit"], str) or not raw_rule["unit"].strip() \
                or not isinstance(raw_rule["notes"], str) or not raw_rule["notes"].strip():
            raise OperatorConfigError(
                f"parameter contract {_format_path(path)} needs non-empty unit and notes"
            )
        parent = _value_at_path(canonical, path[:-1])
        if isinstance(parent, Mapping) and parent.get("tier") == "fixed":
            raise OperatorConfigError(
                f"fixed-tier parameter cannot enter the collaborator contract: "
                f"{_format_path(path)}"
            )
        canonical_value = _value_at_path(canonical, path)
        if isinstance(canonical_value, bool) or not isinstance(
            canonical_value, (int, float)
        ) or not math.isfinite(float(canonical_value)):
            raise OperatorConfigError(
                f"canonical {_format_path(path)} must be a finite number"
            )
        if not float(lower) <= float(canonical_value) <= float(upper):
            raise OperatorConfigError(
                f"canonical {_format_path(path)} is outside the collaborator contract"
            )
        rules[path] = dict(raw_rule)

    for path, rule in rules.items():
        if "must_equal" not in rule:
            continue
        companion = _contract_path(rule["must_equal"], field="must_equal")
        if companion not in rules:
            raise OperatorConfigError(
                f"parameter contract {_format_path(path)} must_equal target is not "
                "an editable contract path"
            )
        if _value_at_path(canonical, path) != _value_at_path(canonical, companion):
            raise OperatorConfigError(
                f"canonical coupled values differ: {_format_path(path)} and "
                f"{_format_path(companion)}"
            )

    return rules, hashlib.sha256(raw).hexdigest()


def collaborator_parameter_contract_sha256() -> str:
    try:
        raw = DEFAULT_CONFIG.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(
            f"canonical config is unavailable or unreadable: {DEFAULT_CONFIG}"
        ) from exc
    canonical = _strict_json_loads(raw, label=f"canonical config {DEFAULT_CONFIG}")
    if not isinstance(canonical, dict):
        raise OperatorConfigError("canonical config must contain one JSON object")
    _, digest = _load_parameter_contract(canonical)
    return digest


def _first_parameter_contract_problem(
    candidate: Mapping[str, Any], canonical: Mapping[str, Any],
    rules: Mapping[tuple[str, ...], Mapping[str, Any]],
) -> str | None:
    for path, rule in rules.items():
        value = _value_at_path(candidate, path)
        canonical_value = _value_at_path(canonical, path)
        if _same_json_scalar_value(value, canonical_value):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            return f"edited {_format_path(path)} must be a finite number"
        lower = float(rule["minimum"])
        upper = float(rule["maximum"])
        if not lower <= float(value) <= upper:
            return (
                f"edited {_format_path(path)}={value!r} is outside its declared "
                f"collaborator contract interval "
                f"[{rule['minimum']!r}, {rule['maximum']!r}]"
            )
    for path, rule in rules.items():
        if "must_equal" not in rule:
            continue
        companion = _contract_path(rule["must_equal"], field="must_equal")
        if _value_at_path(candidate, path) != _value_at_path(candidate, companion):
            return (
                f"edited {_format_path(path)} must equal "
                f"{_format_path(companion)}"
            )
    return None


def _validate_seed(seed: Any, *, required: bool) -> int | None:
    if seed is None:
        if required:
            raise OperatorConfigError(
                "--seed is required for every run; validation-only is the only mode "
                "that may omit it"
            )
        return None
    if type(seed) is not int or not 0 <= seed <= UINT32_MAX:
        raise OperatorConfigError(
            f"--seed must be an integer in [0, {UINT32_MAX}]"
        )
    return seed


def _timezone_qualified_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OperatorConfigError(f"{field} must be a timezone-qualified timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorConfigError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperatorConfigError(f"{field} must include a timezone")
    return parsed


def _operator_requirements_contract() -> tuple[dict[str, str], str]:
    """Return the exact direct operator pins and requirements-file digest."""
    try:
        raw = REQUIREMENTS_OPERATOR_PATH.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(
            f"operator requirements file is missing or unreadable: "
            f"{REQUIREMENTS_OPERATOR_PATH}"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OperatorConfigError(
            f"operator requirements file is not UTF-8: {REQUIREMENTS_OPERATOR_PATH}"
        ) from exc

    versions: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if separator != "==" or not name or not version or "==" in version:
            raise OperatorConfigError(
                "requirements-operator.txt must contain only exact name==version "
                f"pins (line {line_number})"
            )
        if name in versions:
            raise OperatorConfigError(
                f"requirements-operator.txt repeats distribution {name!r}"
            )
        versions[name] = version
    if tuple(versions) != OPERATOR_REQUIREMENT_DISTRIBUTIONS:
        raise OperatorConfigError(
            "requirements-operator.txt must pin exactly, in order: "
            + ", ".join(OPERATOR_REQUIREMENT_DISTRIBUTIONS)
        )
    return versions, hashlib.sha256(raw).hexdigest()


def _require_production_approval(config: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless this canonical config is approved for real runs.

    Validation-only intentionally does not call this function.  The approval
    block is a protected config root, so an operator copy cannot add or edit it
    independently of the canonical config selected by this runner.
    """
    approval = config.get("production_approval")
    if not isinstance(approval, Mapping):
        raise OperatorConfigError(
            "actual runs require a protected production_approval block; this "
            "config may be checked with --validate-only but is not authorized "
            "for simulation"
        )
    fields = set(approval)
    if fields != PRODUCTION_APPROVAL_FIELDS:
        missing = sorted(PRODUCTION_APPROVAL_FIELDS - fields)
        unexpected = sorted(fields - PRODUCTION_APPROVAL_FIELDS)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise OperatorConfigError(
            "production_approval has the wrong fields (" + ", ".join(details) + ")"
        )
    if approval["schema"] != PRODUCTION_APPROVAL_SCHEMA:
        raise OperatorConfigError(
            "production_approval.schema must be "
            f"{PRODUCTION_APPROVAL_SCHEMA!r}"
        )
    if approval["status"] != "APPROVED":
        raise OperatorConfigError("production_approval.status must be 'APPROVED'")
    if approval["production_eligible"] is not True:
        raise OperatorConfigError(
            "production_approval.production_eligible must be true"
        )
    if approval["approved"] is not True:
        raise OperatorConfigError("production_approval.approved must be true")
    approved_by = approval["approved_by"]
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise OperatorConfigError(
            "production_approval.approved_by must name the approving owner"
        )
    _timezone_qualified_timestamp(
        approval["approved_utc"], field="production_approval.approved_utc"
    )
    contract_sha256 = collaborator_parameter_contract_sha256()
    if approval["collaborator_parameter_contract_sha256"] != contract_sha256:
        raise OperatorConfigError(
            "production_approval.collaborator_parameter_contract_sha256 must match "
            f"the reviewed contract {PARAMETER_CONTRACT_PATH} ({contract_sha256})"
        )
    requirements_versions, requirements_sha256 = _operator_requirements_contract()
    if approval["requirements_operator_sha256"] != requirements_sha256:
        raise OperatorConfigError(
            "production_approval.requirements_operator_sha256 must match "
            f"{REQUIREMENTS_OPERATOR_PATH} ({requirements_sha256})"
        )
    code_identity = _code_identity()
    scientific_sha256 = code_identity["scientific_files_sha256"]
    if approval["scientific_files_sha256"] != scientific_sha256:
        raise OperatorConfigError(
            "production_approval.scientific_files_sha256 must match the exact "
            f"MODEL_CODE_FILES aggregate ({scientific_sha256})"
        )
    return {
        "parameter_contract_sha256": contract_sha256,
        "requirements_operator_sha256": requirements_sha256,
        "requirements_versions": requirements_versions,
        "code_identity": code_identity,
        "production_approved_utc": approval["approved_utc"],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise OperatorConfigError(f"cannot hash file: {path}") from exc
    return digest.hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OperatorConfigError("cannot encode reproducibility data as strict JSON") from exc
    return (text + "\n").encode()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_identity() -> dict[str, str]:
    def version(distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return "missing"

    return {
        "python_implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "pandas": version("pandas"),
        "scipy": version("scipy"),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def _require_operator_runtime(
    expected_versions: Mapping[str, str],
) -> dict[str, str]:
    """Require the pinned CPython/operator environment before engine import."""
    identity = _runtime_identity()
    if identity["python_implementation"] != "CPython":
        raise OperatorConfigError(
            "actual runs require CPython 3.11; found "
            f"{identity['python_implementation']} {identity['python']}"
        )
    try:
        major, minor = (int(part) for part in identity["python"].split(".")[:2])
    except (TypeError, ValueError) as exc:
        raise OperatorConfigError(
            f"cannot verify the Python runtime version: {identity['python']!r}"
        ) from exc
    if (major, minor) != (3, 11):
        raise OperatorConfigError(
            f"actual runs require CPython 3.11.x; found {identity['python']}"
        )
    for distribution, expected in expected_versions.items():
        actual = identity.get(distribution, "missing")
        if actual != expected:
            raise OperatorConfigError(
                f"actual runs require {distribution}=={expected}; found {actual}. "
                "Recreate the environment from requirements-operator.txt"
            )
    return identity


def _code_identity() -> dict[str, Any]:
    file_hashes: dict[str, str] = {}
    for name in MODEL_CODE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise OperatorConfigError(
                f"cannot bind code identity because a model source file is missing: {path}"
            )
        file_hashes[name] = _sha256_file(path)
    scientific_hash = hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    commit = "unavailable"
    dirty = None
    diff_sha256 = None
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False,
            capture_output=True, text=True,
        )
        if commit_result.returncode == 0 and commit_result.stdout.strip():
            commit = commit_result.stdout.strip()
        diff_result = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", *MODEL_CODE_FILES],
            cwd=ROOT, check=False, capture_output=True,
        )
        if diff_result.returncode == 0:
            dirty = bool(diff_result.stdout)
            diff_sha256 = hashlib.sha256(diff_result.stdout).hexdigest()
    except OSError:
        pass
    return {
        "scientific_files_sha256": scientific_hash,
        "files": file_hashes,
        "git_commit": commit,
        "git_scientific_files_dirty": dirty,
        "git_scientific_diff_sha256": diff_sha256,
    }


def production_approval_binding_values() -> dict[str, str]:
    """Compute the three file/content bindings required at promotion time."""
    _, requirements_sha256 = _operator_requirements_contract()
    return {
        "collaborator_parameter_contract_sha256": (
            collaborator_parameter_contract_sha256()
        ),
        "scientific_files_sha256": _code_identity()["scientific_files_sha256"],
        "requirements_operator_sha256": requirements_sha256,
    }


def _load_analysis_run_sheet(
    path: str | Path, *, input_config_sha256: str,
    parameter_contract_sha256: str, scientific_files_sha256: str,
    requirements_operator_sha256: str, model: str, p: float | None, seed: int,
    output_dir: str | Path,
    production_approved_utc: str,
) -> tuple[dict[str, Any], str]:
    """Load one exact, scientifically approved and dispatched analysis sheet."""
    sheet_path = Path(path)
    try:
        raw = sheet_path.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(
            f"analysis run sheet does not exist or is unreadable: {sheet_path}"
        ) from exc
    payload = _strict_json_loads(raw, label=f"analysis run sheet {sheet_path}")
    if not isinstance(payload, dict):
        raise OperatorConfigError("analysis run sheet must contain one JSON object")
    fields = set(payload)
    if fields != ANALYSIS_RUN_SHEET_FIELDS:
        missing = sorted(ANALYSIS_RUN_SHEET_FIELDS - fields)
        unexpected = sorted(fields - ANALYSIS_RUN_SHEET_FIELDS)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise OperatorConfigError(
            "analysis run sheet has the wrong fields (" + ", ".join(details) + ")"
        )
    if payload["schema"] != ANALYSIS_RUN_SHEET_SCHEMA:
        raise OperatorConfigError(
            f"analysis run sheet schema must be {ANALYSIS_RUN_SHEET_SCHEMA!r}"
        )

    exact_bindings = {
        "analysis_config_sha256": input_config_sha256,
        "collaborator_parameter_contract_sha256": parameter_contract_sha256,
        "scientific_files_sha256": scientific_files_sha256,
        "requirements_operator_sha256": requirements_operator_sha256,
    }
    for field, expected in exact_bindings.items():
        if payload[field] != expected:
            raise OperatorConfigError(
                f"analysis run sheet {field} does not match the current run "
                f"({payload[field]!r} != {expected!r})"
            )
    if payload["model"] != model:
        raise OperatorConfigError(
            f"analysis run sheet model {payload['model']!r} does not match --model {model}"
        )
    sheet_p = payload["p"]
    if model == "M0":
        if sheet_p is not None or p is not None:
            raise OperatorConfigError("an M0 analysis run sheet must set p to null")
    else:
        if isinstance(sheet_p, bool) or not isinstance(sheet_p, (int, float)) \
                or not math.isfinite(float(sheet_p)) or float(sheet_p) != p:
            raise OperatorConfigError(
                f"analysis run sheet p {sheet_p!r} does not match --p {p!r}"
            )

    seeds = payload["seeds"]
    if not isinstance(seeds, list) or len(seeds) != 1:
        raise OperatorConfigError(
            "analysis run sheet seeds must contain exactly one uint32 integer; "
            "one sheet authorizes one invocation"
        )
    if any(type(value) is not int or not 0 <= value <= UINT32_MAX for value in seeds):
        raise OperatorConfigError(
            "analysis run sheet seeds must contain only uint32 integers"
        )
    if seed != seeds[0]:
        raise OperatorConfigError(
            f"--seed {seed} is not authorized by the analysis run sheet"
        )

    authorized_output = payload["output_directory"]
    if not isinstance(authorized_output, str) or not authorized_output.strip():
        raise OperatorConfigError(
            "analysis run sheet output_directory must be a non-empty absolute path"
        )
    authorized_path = Path(authorized_output)
    if not authorized_path.is_absolute():
        raise OperatorConfigError(
            "analysis run sheet output_directory must be an absolute path"
        )
    canonical_authorized_path = authorized_path.resolve()
    if authorized_path != canonical_authorized_path:
        raise OperatorConfigError(
            "analysis run sheet output_directory must be canonical and must not "
            "contain symlink or '..' aliases"
        )
    requested_path = Path(output_dir).resolve()
    if canonical_authorized_path != requested_path:
        raise OperatorConfigError(
            "analysis run sheet output_directory does not match --output-dir "
            f"({canonical_authorized_path} != {requested_path})"
        )

    for field in ("scientific_approved_by", "dispatch_authorized_by"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise OperatorConfigError(f"analysis run sheet {field} must name an owner")
    production_time = _timezone_qualified_timestamp(
        production_approved_utc,
        field="production_approval.approved_utc",
    )
    scientific_time = _timezone_qualified_timestamp(
        payload["scientific_approved_utc"],
        field="analysis run sheet scientific_approved_utc",
    )
    dispatch_time = _timezone_qualified_timestamp(
        payload["dispatch_authorized_utc"],
        field="analysis run sheet dispatch_authorized_utc",
    )
    if scientific_time < production_time:
        raise OperatorConfigError(
            "analysis run sheet scientific_approved_utc cannot precede "
            "production_approval.approved_utc"
        )
    if dispatch_time < scientific_time:
        raise OperatorConfigError(
            "analysis run sheet dispatch_authorized_utc cannot precede "
            "scientific_approved_utc"
        )
    return payload, hashlib.sha256(raw).hexdigest()


def write_reproducibility_bundle(
    *, input_config_path: str | Path, input_config_sha256: str,
    effective_config: Mapping[str, Any], output_dir: str | Path,
    model: str, p: float | None, seed: int, command_argv: Sequence[str],
    parameter_contract_sha256: str, scientific_files_sha256: str,
    requirements_operator_sha256: str, runtime_identity: Mapping[str, str],
    run_sheet_path: str | Path | None, run_sheet_sha256: str | None,
    execution_mode: str = "production",
) -> Path:
    """Write the effective config and PREPARED sidecar before engine import."""
    if execution_mode not in {"production", "research"}:
        raise OperatorConfigError(f"invalid execution mode: {execution_mode!r}")
    if execution_mode == "production" and (
        run_sheet_path is None or run_sheet_sha256 is None
    ):
        raise OperatorConfigError("production runs require an approved analysis run sheet")
    if execution_mode == "research" and (
        run_sheet_path is not None or run_sheet_sha256 is not None
    ):
        raise OperatorConfigError("research runs cannot claim an approved analysis run sheet")

    input_path = Path(input_config_path)
    if _sha256_file(input_path) != input_config_sha256:
        raise OperatorConfigError(
            f"input config changed after validation: {input_path}; validate a stable copy again"
        )
    if _sha256_file(PARAMETER_CONTRACT_PATH) != parameter_contract_sha256:
        raise OperatorConfigError(
            f"collaborator parameter contract changed after run validation: "
            f"{PARAMETER_CONTRACT_PATH}"
        )
    _, current_requirements_sha256 = _operator_requirements_contract()
    if current_requirements_sha256 != requirements_operator_sha256:
        raise OperatorConfigError(
            f"operator requirements changed after run validation: "
            f"{REQUIREMENTS_OPERATOR_PATH}"
        )
    sheet_path: Path | None = None
    sheet_bytes: bytes | None = None
    if execution_mode == "production":
        assert run_sheet_path is not None
        assert run_sheet_sha256 is not None
        sheet_path = Path(run_sheet_path)
        try:
            sheet_bytes = sheet_path.read_bytes()
        except OSError as exc:
            raise OperatorConfigError(
                f"analysis run sheet became unreadable after validation: {sheet_path}"
            ) from exc
        if hashlib.sha256(sheet_bytes).hexdigest() != run_sheet_sha256:
            raise OperatorConfigError(
                f"analysis run sheet changed after validation: {sheet_path}"
            )

    # Build all potentially failing provenance before creating the run directory.
    code_identity = _code_identity()
    if code_identity["scientific_files_sha256"] != scientific_files_sha256:
        raise OperatorConfigError(
            "MODEL_CODE_FILES changed after run validation"
        )
    effective_bytes = _json_bytes(dict(effective_config))

    output_path = Path(output_dir)
    if output_path != output_path.resolve():
        raise OperatorConfigError(
            "internal output directory must be the canonical authorized path"
        )
    output_path.mkdir(parents=True, exist_ok=True)
    if output_path != output_path.resolve():
        raise OperatorConfigError(
            "output directory acquired a symlink alias before provenance was written"
        )
    effective_path = output_path / EFFECTIVE_CONFIG_FILENAME
    run_sheet_snapshot_path = output_path / RUN_SHEET_SNAPSHOT_FILENAME
    research_notice_path = output_path / RESEARCH_NOTICE_FILENAME
    manifest_path = output_path / RUN_MANIFEST_FILENAME
    try:
        with effective_path.open("xb") as handle:
            handle.write(effective_bytes)
    except FileExistsError as exc:
        raise OperatorConfigError(
            f"refusing to overwrite reproducibility file: {effective_path}"
        ) from exc
    if execution_mode == "production":
        assert sheet_bytes is not None
        try:
            with run_sheet_snapshot_path.open("xb") as handle:
                handle.write(sheet_bytes)
        except FileExistsError as exc:
            raise OperatorConfigError(
                f"refusing to overwrite reproducibility file: {run_sheet_snapshot_path}"
            ) from exc

    research_notice: dict[str, Any] | None = None
    research_notice_bytes: bytes | None = None
    if execution_mode == "research":
        research_notice = {
            "schema": RESEARCH_NOTICE_SCHEMA,
            "classification": RESEARCH_CLASSIFICATION,
            "production_eligible": False,
            "statement": (
                "This output is an explicitly requested research run. It was not "
                "authorized by a production approval or analysis run sheet and must "
                "not be represented as production-eligible or as an accepted final result."
            ),
            "route": {"model": model, "p": p, "seed": int(seed)},
            "input_config_sha256": input_config_sha256,
            "scientific_files_sha256": scientific_files_sha256,
        }
        research_notice_bytes = _json_bytes(research_notice)
        try:
            with research_notice_path.open("xb") as handle:
                handle.write(research_notice_bytes)
        except FileExistsError as exc:
            raise OperatorConfigError(
                f"refusing to overwrite research notice: {research_notice_path}"
            ) from exc

    manifest = {
        "schema": RUN_MANIFEST_SCHEMA,
        "status": "PREPARED",
        "prepared_utc": _utc_now(),
        "completed_utc": None,
        "run_class": "PRODUCTION" if execution_mode == "production" else "RESEARCH",
        "classification": (
            "PRODUCTION_AUTHORIZED"
            if execution_mode == "production"
            else RESEARCH_CLASSIFICATION
        ),
        "production_eligible": execution_mode == "production",
        "command": ["run_model.py", *command_argv],
        "input_config": {
            "path": str(input_path.resolve()),
            "sha256": input_config_sha256,
        },
        "effective_config": {
            "path": str(effective_path.resolve()),
            "sha256": hashlib.sha256(effective_bytes).hexdigest(),
        },
        "collaborator_parameter_contract": {
            "path": str(PARAMETER_CONTRACT_PATH.resolve()),
            "sha256": parameter_contract_sha256,
        },
        "analysis_run_sheet": (
            {
                "source_path": str(sheet_path.resolve()),
                "sealed_path": str(run_sheet_snapshot_path.resolve()),
                "sha256": run_sheet_sha256,
            }
            if sheet_path is not None else None
        ),
        "research_notice": (
            {
                "path": str(research_notice_path.resolve()),
                "sha256": hashlib.sha256(research_notice_bytes).hexdigest(),
            }
            if research_notice_bytes is not None else None
        ),
        "operator_requirements": {
            "path": str(REQUIREMENTS_OPERATOR_PATH.resolve()),
            "sha256": requirements_operator_sha256,
        },
        "route": {"model": model, "p": p, "seed": int(seed)},
        "runtime_identity": dict(runtime_identity),
        "code_identity": code_identity,
        "output_path": str(output_path.resolve()),
        "scientific_output": None,
    }
    try:
        with manifest_path.open("xb") as handle:
            handle.write(_json_bytes(manifest))
    except FileExistsError as exc:
        raise OperatorConfigError(
            f"refusing to overwrite reproducibility file: {manifest_path}"
        ) from exc
    return manifest_path


def finalize_run_manifest(
    manifest_path: str | Path, *, status: str, failure_type: str | None = None,
    scientific_output: Mapping[str, Any] | None = None,
) -> None:
    if status not in {"COMPLETE", "FAILED"}:
        raise OperatorConfigError(f"invalid run-manifest final status: {status}")
    path = Path(manifest_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OperatorConfigError(f"cannot read run manifest: {path}") from exc
    payload = _strict_json_loads(raw, label=f"run manifest {path}")
    if not isinstance(payload, dict):
        raise OperatorConfigError(f"run manifest must contain one JSON object: {path}")
    if payload.get("schema") != RUN_MANIFEST_SCHEMA or payload.get("status") != "PREPARED":
        raise OperatorConfigError(f"run manifest is not a prepared current artifact: {path}")
    if status == "COMPLETE" and scientific_output is None:
        raise OperatorConfigError(
            "a run manifest cannot become COMPLETE without the protected scientific output"
        )
    if status == "FAILED" and scientific_output is not None:
        raise OperatorConfigError(
            "a FAILED run manifest cannot claim a protected scientific output"
        )
    payload["status"] = status
    payload["completed_utc"] = _utc_now()
    payload["scientific_output"] = (
        dict(scientific_output) if scientific_output is not None else None
    )
    if failure_type is not None:
        payload["failure_type"] = failure_type
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(payload))
    temporary.replace(path)


def _verify_protected_scientific_output(output_dir: str | Path) -> dict[str, Any]:
    """Require the exact non-empty regular result from the protected route."""
    return _verify_scientific_output(
        output_dir, filename=PROTECTED_SCIENTIFIC_OUTPUT
    )


def _verify_scientific_output(
    output_dir: str | Path, *, filename: str,
) -> dict[str, Any]:
    """Require one exact non-empty regular result from the selected route."""
    path = Path(output_dir) / filename
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise OperatorConfigError(
            f"engine returned without required scientific output: "
            f"{filename}"
        ) from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise OperatorConfigError(
            f"required scientific output is not a regular file: {path}"
        )
    if metadata.st_size <= 0:
        raise OperatorConfigError(f"required scientific output is empty: {path}")
    return {
        "relative_path": filename,
        "size_bytes": metadata.st_size,
        "sha256": _sha256_file(path),
    }


def config_for_model(
    base_config: Mapping[str, Any], *, model: str,
    aids_symptom_annual_probability: float | None,
    output_dir: str | Path | None = None,
) -> dict:
    """Create an explicit runtime config without mutating ``base_config``."""
    if model == "M0" and aids_symptom_annual_probability is not None:
        raise OperatorConfigError("--p is only valid with --model M1")
    if model == "M1" and aids_symptom_annual_probability is None:
        raise OperatorConfigError(
            "--model M1 requires --p AIDS_SYMPTOM_ANNUAL_PROBABILITY"
        )
    try:
        config = ssd.route_shared_config(
            base_config, model=model, p=aids_symptom_annual_probability
        )
    except ssd.StageStructuredDiagnosisConfigError as exc:
        raise OperatorConfigError(str(exc)) from exc

    if output_dir is not None:
        output = config["output"]
        _set_value(output, "enabled", True)
        _set_value(output, "directory", str(Path(output_dir)))
        _set_value(output, "terminal_summary", True)
    return config


def _set_value(block: dict, key: str, value: Any) -> None:
    if key not in block:
        raise OperatorConfigError(f"config is missing output.{key}")
    current = block[key]
    if isinstance(current, dict):
        if "value" not in current:
            raise OperatorConfigError(f"output.{key} must contain a value field")
        current["value"] = value
    else:
        block[key] = value


def _require_protected_output_contract(
    runtime: Mapping[str, Any], *, output_dir: str | Path,
) -> None:
    """Freeze the ordinary one-run output contract expected after execution."""
    _require_output_contract(
        runtime, output_dir=output_dir, expected_run_tag="baseline"
    )


def _require_output_contract(
    runtime: Mapping[str, Any], *, output_dir: str | Path,
    expected_run_tag: str,
) -> None:
    """Freeze the common one-run CSV output contract and its exact run tag."""
    output = runtime.get("output")
    if not isinstance(output, Mapping):
        raise OperatorConfigError("runtime config has no protected output object")
    expected = {
        "enabled": True,
        "format": "csv",
        "run_tag": expected_run_tag,
        "write_per_run_files": True,
    }
    expected_output = f"{expected_run_tag}_run_001.csv"
    for field, expected_value in expected.items():
        if field not in output or extract_value(output[field]) != expected_value:
            raise OperatorConfigError(
                f"protected output.{field} must remain {expected_value!r} for "
                f"{expected_output}"
            )
    if "directory" not in output or Path(
        str(extract_value(output["directory"]))
    ) != Path(output_dir):
        raise OperatorConfigError(
            "protected output.directory does not match --output-dir"
        )


def _shape_research_output(runtime: dict) -> None:
    """Give research results a durable non-production filename label."""
    output = runtime.get("output")
    if not isinstance(output, dict):
        raise OperatorConfigError("runtime config has no research output object")
    _set_value(output, "run_tag", RESEARCH_RUN_TAG)


def _require_research_output_contract(
    runtime: Mapping[str, Any], *, output_dir: str | Path,
) -> None:
    """Freeze the one-run output contract for non-production research runs."""
    _require_output_contract(
        runtime, output_dir=output_dir, expected_run_tag=RESEARCH_RUN_TAG
    )


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(
        description="Validate or run one explicit M0/M1 model configuration."
    )
    out.add_argument("--config", default=str(DEFAULT_CONFIG), help="model config JSON")
    out.add_argument("--model", required=True, choices=("M0", "M1"))
    out.add_argument(
        "--p", type=float, default=None, metavar="PROBABILITY",
        help="AIDS-symptom annual probability; required for M1 and forbidden for M0",
    )
    out.add_argument(
        "--seed", type=int, default=None,
        help=(
            f"explicit RNG seed in [0, {UINT32_MAX}]; required for a run and "
            "optional only with --validate-only"
        ),
    )
    out.add_argument(
        "--output-dir", default=None,
        help="directory for model outputs; required unless --validate-only is used",
    )
    out.add_argument(
        "--run-sheet", default=None,
        help=(
            "approved collaborator analysis run sheet; required for an actual run "
            "and not needed for --validate-only"
        ),
    )
    out.add_argument(
        "--research-run", action="store_true",
        help=(
            "execute without production approval or a run sheet; output is "
            "permanently labelled research-only and not production-eligible"
        ),
    )
    out.add_argument(
        "--validate-only", action="store_true",
        help="validate the config and route without constructing the engine",
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    command_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(command_argv)
    try:
        if args.research_run and args.validate_only:
            raise OperatorConfigError(
                "--research-run and --validate-only are separate modes; choose one"
            )
        if args.research_run and args.run_sheet is not None:
            raise OperatorConfigError(
                "--research-run cannot use --run-sheet or claim production authorization"
            )
        args.seed = _validate_seed(args.seed, required=not args.validate_only)
        base, input_config_sha256 = _load_shared_config_and_hash(args.config)
        output_path = (
            None if args.output_dir is None else Path(args.output_dir).resolve()
        )
        runtime = config_for_model(
            base, model=args.model,
            aids_symptom_annual_probability=args.p, output_dir=output_path,
        )
        # Re-check the shaped config through ConfigReader.  M0 deliberately has
        # no optional block; M1 must remain enabled after translation.
        translated = ConfigReader(runtime).as_v6_dict()
        profile = ssd.load_stage_structured_diagnosis(translated)
        if args.model == "M0" and profile.enabled:
            raise OperatorConfigError("M0 route unexpectedly enabled M1 diagnosis")
        if args.model == "M1" and not profile.enabled:
            raise OperatorConfigError("M1 route did not enable stage-structured diagnosis")
        if args.validate_only:
            print(
                f"VALID: {args.model} config route; stochastic=true; "
                "engine was not constructed"
            )
            return 0
        if args.output_dir is None:
            raise OperatorConfigError(
                "--output-dir is required for a run so results are not silently discarded"
            )
        assert output_path is not None
        if output_path.exists() and not output_path.is_dir():
            raise OperatorConfigError(f"output path is not a directory: {output_path}")
        if output_path.is_dir() and any(output_path.iterdir()):
            raise OperatorConfigError(
                f"output directory is not empty: {output_path}; choose a new run directory"
            )
        run_sheet_sha256: str | None = None
        if args.research_run:
            _shape_research_output(runtime)
            _require_research_output_contract(runtime, output_dir=output_path)
            requirements_versions, requirements_sha256 = (
                _operator_requirements_contract()
            )
            approval_bindings = {
                "parameter_contract_sha256": (
                    collaborator_parameter_contract_sha256()
                ),
                "requirements_operator_sha256": requirements_sha256,
                "requirements_versions": requirements_versions,
                "code_identity": _code_identity(),
            }
        else:
            _require_protected_output_contract(runtime, output_dir=output_path)
            approval_bindings = _require_production_approval(base)
            if args.run_sheet is None:
                raise OperatorConfigError(
                    "--run-sheet is required for an actual run; scientific approval "
                    "and dispatch authorization must be recorded separately"
                )
            _, run_sheet_sha256 = _load_analysis_run_sheet(
                args.run_sheet,
                input_config_sha256=input_config_sha256,
                parameter_contract_sha256=(
                    approval_bindings["parameter_contract_sha256"]
                ),
                scientific_files_sha256=approval_bindings["code_identity"][
                    "scientific_files_sha256"
                ],
                requirements_operator_sha256=approval_bindings[
                    "requirements_operator_sha256"
                ],
                model=args.model,
                p=args.p,
                seed=args.seed,
                output_dir=output_path,
                production_approved_utc=approval_bindings[
                    "production_approved_utc"
                ],
            )
        runtime_identity = _require_operator_runtime(
            approval_bindings["requirements_versions"]
        )
        manifest_path = write_reproducibility_bundle(
            input_config_path=args.config,
            input_config_sha256=input_config_sha256,
            effective_config=runtime,
            output_dir=output_path,
            model=args.model,
            p=args.p,
            seed=args.seed,
            command_argv=command_argv,
            parameter_contract_sha256=(
                approval_bindings["parameter_contract_sha256"]
            ),
            scientific_files_sha256=approval_bindings["code_identity"][
                "scientific_files_sha256"
            ],
            requirements_operator_sha256=approval_bindings[
                "requirements_operator_sha256"
            ],
            runtime_identity=runtime_identity,
            run_sheet_path=args.run_sheet,
            run_sheet_sha256=run_sheet_sha256,
            execution_mode=("research" if args.research_run else "production"),
        )
    except OperatorConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        from run_v3 import SimulationEngineV3  # engine import only after validation

        engine = SimulationEngineV3(runtime, seed=args.seed)
        engine.run(run_number=1)
        scientific_output = _verify_scientific_output(
            output_path,
            filename=(
                RESEARCH_SCIENTIFIC_OUTPUT
                if args.research_run else PROTECTED_SCIENTIFIC_OUTPUT
            ),
        )
    except Exception as exc:
        finalize_run_manifest(
            manifest_path, status="FAILED", failure_type=type(exc).__name__
        )
        if isinstance(exc, OperatorConfigError):
            print(f"ERROR: {exc}")
            return 2
        raise
    finalize_run_manifest(
        manifest_path, status="COMPLETE", scientific_output=scientific_output
    )
    if args.research_run:
        print(
            f"COMPLETE RESEARCH RUN ({RESEARCH_CLASSIFICATION}): {args.model} "
            f"results written to {output_path}; reproducibility manifest: "
            f"{manifest_path}"
        )
    else:
        print(
            f"COMPLETE: {args.model} results written to {output_path}; "
            f"reproducibility manifest: {manifest_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
