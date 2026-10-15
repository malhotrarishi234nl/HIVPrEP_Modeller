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

PRODUCTION_APPROVAL_SCHEMA = "model_config_production_approval/1.1.0"
PRODUCTION_APPROVAL_FIELDS = {
    "schema", "status", "production_eligible", "approved", "approved_by",
    "approved_utc", "collaborator_parameter_contract_sha256",
}

EFFECTIVE_CONFIG_FILENAME = "effective_config.json"
RUN_MANIFEST_FILENAME = "run_reproducibility_manifest.json"
RUN_MANIFEST_SCHEMA = "model_run_reproducibility/1.0.0"

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


def load_shared_config(path: str | Path) -> dict:
    """Load and validate the protected shared-config fields without an engine."""
    payload, _ = _load_shared_config_and_hash(path)
    return payload


def _load_shared_config_and_hash(path: str | Path) -> tuple[dict, str]:
    """Read once so the returned payload and input-file hash describe one file."""
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError as exc:
        raise OperatorConfigError(f"config file does not exist: {config_path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorConfigError(
            f"config is not valid JSON ({config_path}, line {exc.lineno}): {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise OperatorConfigError("config must contain one JSON object")

    try:
        canonical = json.loads(DEFAULT_CONFIG.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise OperatorConfigError(
            f"canonical config is unavailable or invalid: {DEFAULT_CONFIG}"
        ) from exc
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
    except FileNotFoundError as exc:
        raise OperatorConfigError(
            f"collaborator parameter contract is missing: {PARAMETER_CONTRACT_PATH}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorConfigError(
            f"collaborator parameter contract is invalid JSON at line {exc.lineno}: "
            f"{exc.msg}"
        ) from exc
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
        canonical = json.loads(DEFAULT_CONFIG.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise OperatorConfigError(
            f"canonical config is unavailable or invalid: {DEFAULT_CONFIG}"
        ) from exc
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
                f"edited {_format_path(path)}={value!r} is outside its approved "
                f"collaborator interval [{rule['minimum']!r}, {rule['maximum']!r}]"
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


def _require_production_approval(config: Mapping[str, Any]) -> str:
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
    approved_utc = approval["approved_utc"]
    if not isinstance(approved_utc, str) or not approved_utc.strip():
        raise OperatorConfigError(
            "production_approval.approved_utc must be a timezone-qualified timestamp"
        )
    try:
        parsed = datetime.fromisoformat(approved_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorConfigError(
            "production_approval.approved_utc must be a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperatorConfigError(
            "production_approval.approved_utc must include a timezone"
        )
    contract_sha256 = collaborator_parameter_contract_sha256()
    if approval["collaborator_parameter_contract_sha256"] != contract_sha256:
        raise OperatorConfigError(
            "production_approval.collaborator_parameter_contract_sha256 must match "
            f"the reviewed contract {PARAMETER_CONTRACT_PATH} ({contract_sha256})"
        )
    return contract_sha256


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


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


def write_reproducibility_bundle(
    *, input_config_path: str | Path, input_config_sha256: str,
    effective_config: Mapping[str, Any], output_dir: str | Path,
    model: str, p: float | None, seed: int, command_argv: Sequence[str],
    parameter_contract_sha256: str,
) -> Path:
    """Write the effective config and PREPARED sidecar before engine import."""
    input_path = Path(input_config_path)
    if _sha256_file(input_path) != input_config_sha256:
        raise OperatorConfigError(
            f"input config changed after validation: {input_path}; validate a stable copy again"
        )
    if _sha256_file(PARAMETER_CONTRACT_PATH) != parameter_contract_sha256:
        raise OperatorConfigError(
            f"collaborator parameter contract changed after approval validation: "
            f"{PARAMETER_CONTRACT_PATH}"
        )

    # Build all potentially failing provenance before creating the run directory.
    code_identity = _code_identity()
    runtime_identity = _runtime_identity()
    effective_bytes = _json_bytes(dict(effective_config))

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    effective_path = output_path / EFFECTIVE_CONFIG_FILENAME
    manifest_path = output_path / RUN_MANIFEST_FILENAME
    try:
        with effective_path.open("xb") as handle:
            handle.write(effective_bytes)
    except FileExistsError as exc:
        raise OperatorConfigError(
            f"refusing to overwrite reproducibility file: {effective_path}"
        ) from exc

    manifest = {
        "schema": RUN_MANIFEST_SCHEMA,
        "status": "PREPARED",
        "prepared_utc": _utc_now(),
        "completed_utc": None,
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
        "route": {"model": model, "p": p, "seed": int(seed)},
        "runtime_identity": runtime_identity,
        "code_identity": code_identity,
        "output_path": str(output_path.resolve()),
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
) -> None:
    if status not in {"COMPLETE", "FAILED"}:
        raise OperatorConfigError(f"invalid run-manifest final status: {status}")
    path = Path(manifest_path)
    payload = json.loads(path.read_text())
    if payload.get("schema") != RUN_MANIFEST_SCHEMA or payload.get("status") != "PREPARED":
        raise OperatorConfigError(f"run manifest is not a prepared current artifact: {path}")
    payload["status"] = status
    payload["completed_utc"] = _utc_now()
    if failure_type is not None:
        payload["failure_type"] = failure_type
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(payload))
    temporary.replace(path)


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
        "--validate-only", action="store_true",
        help="validate the config and route without constructing the engine",
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    command_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(command_argv)
    try:
        args.seed = _validate_seed(args.seed, required=not args.validate_only)
        base, input_config_sha256 = _load_shared_config_and_hash(args.config)
        runtime = config_for_model(
            base, model=args.model,
            aids_symptom_annual_probability=args.p, output_dir=args.output_dir,
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
        output_path = Path(args.output_dir)
        if output_path.exists() and not output_path.is_dir():
            raise OperatorConfigError(f"output path is not a directory: {output_path}")
        if output_path.is_dir() and any(output_path.iterdir()):
            raise OperatorConfigError(
                f"output directory is not empty: {output_path}; choose a new run directory"
            )
        parameter_contract_sha256 = _require_production_approval(base)
        manifest_path = write_reproducibility_bundle(
            input_config_path=args.config,
            input_config_sha256=input_config_sha256,
            effective_config=runtime,
            output_dir=output_path,
            model=args.model,
            p=args.p,
            seed=args.seed,
            command_argv=command_argv,
            parameter_contract_sha256=parameter_contract_sha256,
        )
    except OperatorConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        from run_v3 import SimulationEngineV3  # engine import only after validation

        engine = SimulationEngineV3(runtime, seed=args.seed)
        engine.run(run_number=1)
    except Exception as exc:
        finalize_run_manifest(
            manifest_path, status="FAILED", failure_type=type(exc).__name__
        )
        raise
    finalize_run_manifest(manifest_path, status="COMPLETE")
    print(
        f"COMPLETE: {args.model} results written to {args.output_dir}; "
        f"reproducibility manifest: {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
