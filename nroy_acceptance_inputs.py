"""Strict validation for frozen robust-NROY acceptance-input evidence.

The observed targets and non-process variances define the numerator and
denominator of every implausibility score.  They are therefore production
inputs, not late merge options.  This module validates one reviewable package
before work is prepared; callers then copy the validated files into the sealed
stage directory.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Mapping

import coupled_calibration_search as ccs
import nroy_contract as nc


SCHEMA = "robust_nroy_acceptance_inputs/1.0.0"
OBSERVED_FILENAME = "observed_values.json"
BASE_VARIANCE_FILENAME = "base_variance.json"
COMPONENT_FILENAME = "component_variance_budget.csv"
PROVENANCE_FILENAME = "acceptance_input_provenance.json"


def _path(root: Path, value: object) -> Path:
    candidate = Path(str(value))
    return candidate if candidate.is_absolute() else root / candidate


def _output_path(directory: Path, value: object, *, label: str) -> Path:
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts or not str(relative):
        raise nc.ContractError(f"acceptance-input {label} has unsafe path {value!r}")
    return directory / relative


def _read_mapping(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise nc.ContractError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise nc.ContractError(f"{label} must be a JSON object: {path}")
    return dict(payload)


def validate_package(
    plan: nc.FrozenPlan,
    provenance_path: str | Path,
    *,
    root: Path,
    scientific_code_sha256: str,
    scientific_runtime: Mapping,
) -> dict:
    """Validate and normalise a final, owner-reviewed acceptance package."""
    provenance_path = _path(root, provenance_path)
    payload = _read_mapping(provenance_path, label="acceptance-input provenance")
    problems: list[str] = []

    expected_scalars = {
        "schema": SCHEMA,
        "status": "FINAL",
        "production_eligible": True,
        "approved": True,
        "plan_sha256": plan.plan_sha256,
        "target_count": plan.targets.n,
        "scientific_code_sha256": scientific_code_sha256,
    }
    for field, expected in expected_scalars.items():
        if payload.get(field) != expected:
            problems.append(f"{field} must equal {expected!r}")
    for field in ("generated_utc", "command", "approved_by", "approved_utc"):
        if not str(payload.get(field, "")).strip():
            problems.append(f"{field} is missing")
    if nc.json_safe(payload.get("scientific_runtime")) != nc.json_safe(scientific_runtime):
        problems.append("scientific_runtime does not match the current runtime")
    if tuple(payload.get("target_order") or ()) != plan.targets.frozen_order:
        problems.append("target_order does not match the signed target contract")
    if payload.get("acceptance_rule") != "I_max <= 3 on B_i + s_i^2 (1 + 1/K)":
        problems.append("acceptance_rule is not the frozen primary rule")
    if payload.get("base_variance_formula") != (
        "B_i = V_obs + V_code + V_disc + declared extra non-process terms"
    ):
        problems.append("base_variance_formula is not the reviewed component sum")
    if "excluded from B_i" not in str(payload.get("process_variance", "")):
        problems.append("process_variance is not explicitly excluded from B_i")
    discrepancy = payload.get("discrepancy")
    if not isinstance(discrepancy, Mapping) or (
        discrepancy.get("method") != ccs.DISCREPANCY_METHOD
        or float(discrepancy.get("fraction", float("nan"))) != plan.discrepancy
        or discrepancy.get("added_exactly_once") is not True
    ):
        problems.append("discrepancy must bind the signed fraction and be added exactly once")

    base_config = _path(root, plan.require("models.base_config"))
    m0_space = _path(root, plan.require("models.m0.calib_space"))
    target_register = _path(root, plan.targets.register_path)
    expected_bindings = {
        "plan": (Path(plan.plan_path), plan.plan_sha256),
        "base_config": (base_config, ccs.sha256_file(base_config)),
        "m0_calibration_space": (m0_space, ccs.sha256_file(m0_space)),
        "target_register": (target_register, plan.targets.register_sha256),
    }
    bindings = payload.get("source_bindings")
    if not isinstance(bindings, Mapping):
        problems.append("source_bindings must be an object")
        bindings = {}
    for name, (_expected_path, expected_hash) in expected_bindings.items():
        binding = bindings.get(name)
        if not isinstance(binding, Mapping):
            problems.append(f"source_bindings.{name} is missing")
        elif str(binding.get("sha256", "")) != expected_hash:
            problems.append(f"source_bindings.{name} has the wrong SHA-256")
    for name in ("source_workbook", "wave_plan"):
        binding = bindings.get(name)
        if not isinstance(binding, Mapping):
            problems.append(f"source_bindings.{name} is missing")
            continue
        source = _path(root, binding.get("path", ""))
        if not source.is_file():
            problems.append(f"source_bindings.{name} file is missing: {source}")
        elif str(binding.get("sha256", "")) != ccs.sha256_file(source):
            problems.append(f"source_bindings.{name} has the wrong SHA-256")

    outputs = payload.get("outputs")
    if not isinstance(outputs, Mapping):
        problems.append("outputs must be an object")
        outputs = {}
    directory = provenance_path.parent
    resolved: dict[str, Path] = {}
    for name in ("observed_values", "base_variance", "component_table"):
        item = outputs.get(name)
        if not isinstance(item, Mapping):
            problems.append(f"outputs.{name} is missing")
            continue
        try:
            output_path = _output_path(directory, item.get("path", ""), label=name)
        except nc.ContractError as exc:
            problems.append(str(exc))
            continue
        if not output_path.is_file():
            problems.append(f"outputs.{name} file is missing: {output_path}")
        elif str(item.get("sha256", "")) != ccs.sha256_file(output_path):
            problems.append(f"outputs.{name} has the wrong SHA-256")
        else:
            resolved[name] = output_path

    observed: dict = {}
    variance: dict = {}
    if "observed_values" in resolved:
        observed = _read_mapping(resolved["observed_values"], label="observed values")
        try:
            plan.targets.assert_order(list(observed), what="observed values")
            if any(not math.isfinite(float(value)) for value in observed.values()):
                problems.append("observed values contain a non-finite number")
        except (TypeError, ValueError, nc.ContractError) as exc:
            problems.append(str(exc))
    if "base_variance" in resolved:
        variance = _read_mapping(resolved["base_variance"], label="base variance")
        try:
            plan.targets.assert_order(list(variance), what="base variance")
            if any(
                not math.isfinite(float(value)) or float(value) <= 0.0
                for value in variance.values()
            ):
                problems.append("base variance contains a non-finite or non-positive value")
        except (TypeError, ValueError, nc.ContractError) as exc:
            problems.append(str(exc))

    underivable_zero = False
    if "component_table" in resolved:
        try:
            with resolved["component_table"].open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            problems.append(f"component table is unreadable: {exc}")
            rows = []
        if [str(row.get("key", "")) for row in rows] != list(plan.targets.frozen_order):
            problems.append("component table order does not match the target contract")
        for row in rows:
            key = str(row.get("key", ""))
            try:
                z = float(row["z"])
                parts = [
                    float(row[name]) for name in (
                        "v_obs_variance", "v_code_variance", "v_disc_variance",
                        "extra_variance",
                    )
                ]
                b_value = float(row["b_variance"])
            except (KeyError, TypeError, ValueError):
                problems.append(f"{key or '<unknown>'}: malformed variance component row")
                continue
            if not all(math.isfinite(value) and value >= 0.0 for value in parts):
                problems.append(f"{key}: variance components must be finite and non-negative")
            if not math.isclose(sum(parts), b_value, rel_tol=0.0, abs_tol=1e-12):
                problems.append(f"{key}: B_i is not the exact declared component sum")
            expected_disc = (plan.discrepancy * abs(z)) ** 2
            if not math.isclose(parts[2], expected_disc, rel_tol=1e-12, abs_tol=1e-12):
                problems.append(f"{key}: discrepancy variance is not added exactly once")
            if key in observed and not math.isclose(z, float(observed[key]), rel_tol=0.0, abs_tol=0.0):
                problems.append(f"{key}: component-table z does not match observed values")
            if key in variance and not math.isclose(b_value, float(variance[key]), rel_tol=0.0, abs_tol=0.0):
                problems.append(f"{key}: component-table B_i does not match base variance")
            if str(row.get("v_obs_status", "")).lower() == "underivable" and parts[0] == 0.0:
                underivable_zero = True
    limitations = [str(value).lower() for value in payload.get("limitations", [])]
    if underivable_zero and not any("over-exclusion" in value for value in limitations):
        problems.append("underivable V_obs=0 rows must disclose bias toward over-exclusion")

    if problems:
        raise nc.ContractError(
            f"{provenance_path}: acceptance-input evidence is invalid:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )
    observed_hash = ccs.sha256_file(resolved["observed_values"])
    variance_hash = ccs.sha256_file(resolved["base_variance"])
    return {
        "provenance_path": provenance_path,
        "provenance_sha256": ccs.sha256_file(provenance_path),
        "observed_path": resolved["observed_values"],
        "observed_sha256": observed_hash,
        "base_variance_path": resolved["base_variance"],
        "base_variance_sha256": variance_hash,
        "component_table_path": resolved["component_table"],
        "component_table_sha256": ccs.sha256_file(resolved["component_table"]),
        "acceptance_input_identity": plan.acceptance_identity(
            observed_sha256=observed_hash,
            base_variance_sha256=variance_hash,
        ),
        "payload": payload,
    }

