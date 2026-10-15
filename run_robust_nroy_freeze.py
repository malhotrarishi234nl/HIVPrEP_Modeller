#!/usr/bin/env python3
"""Runner for the robust-NROY freeze stage.

    .venv/bin/python run_robust_nroy_freeze.py validate  --plan configs/robust_nroy_freeze_production.json
    .venv/bin/python run_robust_nroy_freeze.py prepare   --plan <plan> --out <dir> --anchors <csv>
    .venv/bin/python run_robust_nroy_freeze.py evaluate  --stage-dir <dir> --shard 0 --of 4
    .venv/bin/python run_robust_nroy_freeze.py merge     --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py classify  --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py regions   --parent-dir <bridges dir> --out <dir>
    .venv/bin/python run_robust_nroy_freeze.py diagnose  --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py report    --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py resume    --stage-dir <dir>

The whole chain, for one model::

    prepare --stage discovery    -> evaluate -> merge -> classify
    prepare --stage promotion    -> evaluate -> merge -> classify   (parent: discovery)
    prepare --stage confirmation -> evaluate -> merge -> classify   (parent: promotion)
    prepare --stage bridges      -> evaluate -> merge               (parent: confirmation)
    regions                                                        (parent: bridges)

One command here runs the engine, and only one::

    .venv/bin/python run_robust_nroy_freeze.py identity-gate --out <dir>

That is the M0/M1-at-``p=0`` identity gate.  It is a **pre-production
command, not a test**: every pytest that checks the identity is skipped under
the repository's no-engine guard, so a green full suite does not establish it.
``prepare --model M1`` requires its passing artifact and refuses a missing,
failed, incomplete or stale one.  The gate's runs are short truncated
trajectories, about a second each; nothing is written to ``outputs/``.

Nothing else here starts a simulation on its own.

* ``validate`` never simulates.  It is the dry-run gate: it reports every
  unsigned decision and exits non-zero while any remain.
* Every other subcommand loads the plan through
  :meth:`nroy_contract.FrozenPlan.load`, which **refuses** while a required
  value is unsigned.  With the production plan as shipped, every subcommand
  below ``validate`` therefore stops before touching the engine.
* ``evaluate`` requires an explicit ``--evaluator``.  ``surrogate`` and
  ``surrogate_islands`` are for the verification suite and run no engine --
  the second is a closed-form field that is plausible only on a few isolated
  islands, which is how the suite builds a supported bridge between two points
  with no local support **without** varying the acceptance inputs between
  stages; ``production`` is the real simulator
  and is refused unless the plan is signed *and* ``--i-have-priced-the-budget``
  is passed, because the production spec requires a priced trajectory budget
  and a measured pilot before launch.
* ``prepare`` validates the approved FINAL acceptance package named by the
  signed plan and seals its exact observed values, base variance, component
  budget and review provenance into the stage. Workers revalidate that seal;
  ``merge`` cannot substitute late inputs.
* Every stage beyond the first also freezes its parent's complete
  **acceptance-input identity** at ``prepare`` -- target order, target-register
  hash, observed-value hash, base-variance hash, discrepancy, scoring version
  and acceptance rule -- and ``merge``, ``classify`` and ``regions`` each fail
  closed on any difference.  One lineage, one acceptance basis.

A worker may evaluate only the frozen ledger rows assigned to its shard.  It
cannot generate, substitute or re-draw a proposal: the design exists only in
the prepared stage directory and is loaded read-only.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import nroy_assess as nas
import nroy_acceptance_inputs as nai
import nroy_contract as nc
import nroy_design as nd
import nroy_execution as ne
import nroy_graph as ng
import nroy_identity_gate as nig
import nroy_report as nrp
import stage_structured_diagnosis as ssd
from coupled_calibration_search import ContractError
from tools import audit_robust_nroy_seeds as seed_audit_tool

ROOT = Path(__file__).resolve().parent

QUESTION = (
    "Are fresh-seed-confirmed parameter vectors supported by stable neighbouring "
    "parameter combinations, including thin correlated ridges, or is no such support "
    "detected under the declared finite design?"
)

M1_ANCHOR_EVIDENCE_SCHEMA = "robust_nroy_m1_anchor_confirmation/2.0.0"
M1_SEED_RESULT_SCHEMA = "robust_nroy_m1_anchor_seed_results/1.0.0"
M1_ANCHOR_EVIDENCE_FILENAME = "m1_anchor_confirmation_evidence.json"
M1_INDEPENDENT_ANCHORS_FILENAME = "m1_independent_anchors.csv"
FROZEN_ACCEPTANCE_IDENTITY_SCHEMA = "robust_nroy_frozen_acceptance_identity/1.0.0"
FROZEN_ACCEPTANCE_IDENTITY_FILENAME = "frozen_acceptance_input_identity.json"
MEASURED_PILOT_SCHEMA = "robust_nroy_measured_pilot/2.1.0"
RAW_PILOT_SCHEMA = "robust_nroy_raw_pilot_measurements/1.2.0"
PILOT_SELECTION_PROTOCOL_SCHEMA = "robust_nroy_pilot_vector_selection_protocol/1.0.0"
PILOT_SCIENTIFIC_APPROVAL_SCHEMA = "robust_nroy_pilot_scientific_approval/1.0.0"
PILOT_PARAMETER_SPACE_IDENTITY_SCHEMA = (
    "robust_nroy_pilot_parameter_space_identity/1.0.0"
)
PILOT_TIMING_STATISTIC = "arithmetic_mean_successful_elapsed_seconds"
PRICED_BUDGET_SCHEMA = "robust_nroy_priced_budget/2.0.0"
PRICING_SOURCE_SCHEMA = "robust_nroy_pricing_source/1.0.0"
PRICING_FORMULA = "resource_seconds_at_signed_stage_caps/v1"
PRICED_STAGE_KEYS = tuple(
    (model, stage)
    for model in nc.MODELS
    for stage in (nc.STAGE_DISCOVERY, nc.STAGE_CONFIRMATION, nc.STAGE_BRIDGES)
)


# ==========================================================================
# validate
# ==========================================================================

def _is_sha256(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _is_finite_json_number(value: object) -> bool:
    """True only for a finite native JSON number, never Boolean or string."""
    if type(value) not in (int, float):
        return False
    try:
        return bool(np.isfinite(float(value)))
    except (OverflowError, ValueError):
        return False


def _load_strict_json(path: Path) -> object:
    """Load authority-bearing JSON through the shared fail-closed policy."""
    return nc.strict_json_load(path)


def _evidence_path(value: object) -> Path | None:
    if not str(value or "").strip():
        return None
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def _paths_match(recorded: object, expected: Path) -> bool:
    path = _evidence_path(recorded)
    return path is not None and path.resolve() == expected.resolve()


def _portable_evidence_file(evidence_path: Path, recorded: object) -> Path | None:
    """Resolve a package-local file reference without allowing absolute/traversal paths."""
    text = str(recorded or "").strip()
    if not text:
        return None
    relative = Path(text)
    if relative.is_absolute():
        return None
    package_root = evidence_path.resolve().parent
    resolved = (package_root / relative).resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError:
        return None
    return resolved


def _referenced_evidence_file(evidence_path: Path, recorded: object) -> Path | None:
    """Resolve an evidence reference before and after it is packaged.

    Source evidence may name a repository-relative or absolute file.  A sealed
    stage instead rewrites the reference to a package-local path.  Prefer the
    package-local interpretation when that file exists, then fall back to the
    repository-relative convention used by signed production evidence.
    """
    text = str(recorded or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    packaged = _portable_evidence_file(evidence_path, recorded)
    if packaged is not None and packaged.exists():
        return packaged
    return ROOT / path


def _signed_prerequisite_path(plan: nc.FrozenPlan, key: str) -> Path:
    """Return one prerequisite path from the signed plan, fail-closed."""
    prerequisites = plan.get("production_prerequisites")
    if not isinstance(prerequisites, Mapping) or not str(
        prerequisites.get(key) or ""
    ).strip():
        raise ContractError(
            f"the signed plan declares no production_prerequisites.{key}"
        )
    path = _evidence_path(prerequisites[key])
    if path is None:
        raise ContractError(
            f"the signed plan declares no production_prerequisites.{key}"
        )
    return path


def _registry_bindings(text: str) -> tuple[str, dict[str, str], set[str]]:
    """Read the deliberately simple two-column production registry table."""
    status = ""
    bindings: dict[str, str] = {}
    duplicates: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("status:"):
            status = line.split(":", 1)[1].strip()
            continue
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 2 or cells[0] in {"Binding", "---"}:
            continue
        name, value = cells
        if value.startswith("`") and value.endswith("`") and len(value) >= 2:
            value = value[1:-1].strip()
        if name in bindings:
            duplicates.add(name)
        bindings[name] = value
    return status, bindings, duplicates


def _native_positive_int(value: object) -> int | None:
    """Return a positive native JSON integer, never a Boolean or float."""
    return int(value) if type(value) is int and value > 0 else None


def _native_uint32(value: object) -> int | None:
    """Return an exact native uint32, never a Boolean or numeric string."""
    return int(value) if type(value) is int and 0 <= value <= 2**32 - 1 else None


def _numbers_match(recorded: object, expected: float) -> bool:
    """Compare a recorded native JSON number with recomputed arithmetic."""
    return _is_finite_json_number(recorded) and bool(np.isclose(
        float(recorded), float(expected), rtol=1e-12, atol=1e-12
    ))


def _finite_product(*values: object) -> float | None:
    """Multiply finite numeric inputs without allowing an infinite result."""
    result = 1.0
    try:
        for value in values:
            result *= float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pilot_parameter_space_sha256(model: nc.ModelSpace) -> str:
    """Identity of the exact ordered bounds used to admit pilot vectors."""
    return ccs.sha256_json({
        "schema": PILOT_PARAMETER_SPACE_IDENTITY_SCHEMA,
        "model": model.model,
        "parameter_names": list(model.names),
        "lower": [float(value) for value in model.space.lower],
        "upper": [float(value) for value in model.space.upper],
        "config_paths": list(model.space.config_paths),
    })


def _load_pilot_companion(
    owner: Mapping,
    owner_path: Path,
    *,
    path_field: str,
    sha_field: str,
    label: str,
    problems: list[str],
) -> tuple[Path, Mapping] | None:
    """Load one exact hash-bound pilot authority artifact."""
    path = _referenced_evidence_file(owner_path, owner.get(path_field))
    if path is None or not path.is_file():
        problems.append(f"the measured-pilot {label} is missing")
        return None
    recorded_sha = owner.get(sha_field)
    if not _is_sha256(recorded_sha) or recorded_sha != ccs.sha256_file(path):
        problems.append(f"the measured-pilot {label} hash is stale")
        return None
    try:
        payload = _load_strict_json(path)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        problems.append(f"the measured-pilot {label} is not strict JSON: {exc}")
        return None
    if not isinstance(payload, Mapping):
        problems.append(f"the measured-pilot {label} is not a JSON object")
        return None
    return path, payload


def _pilot_vector_records(
    value: object,
    *,
    model_spaces: Mapping[str, nc.ModelSpace],
    label: str,
    problems: list[str],
) -> dict[str, list[dict[str, object]]] | None:
    """Validate and normalize exact model/vector identities for review."""
    if not isinstance(value, Mapping) or set(value) != set(nc.MODELS):
        problems.append(f"the measured-pilot {label} must contain exact M0 and M1 vectors")
        return None
    result: dict[str, list[dict[str, object]]] = {}
    valid = True
    for model in nc.MODELS:
        records = value.get(model)
        if not isinstance(records, list) or not records:
            problems.append(f"the measured-pilot {label} has no {model} vectors")
            valid = False
            continue
        normalized: list[dict[str, object]] = []
        seen_hashes: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                "parameter_vector", "parameter_vector_sha256"
            }:
                valid = False
                continue
            vector = record.get("parameter_vector")
            vector_sha = record.get("parameter_vector_sha256")
            model_space = model_spaces[model]
            if not isinstance(vector, Mapping) or set(vector) != set(model_space.names) \
                    or not _is_sha256(vector_sha) \
                    or any(not _is_finite_json_number(item) for item in vector.values()):
                valid = False
                continue
            try:
                actual_sha = nc.vector_hash(vector, names=model_space.names)
                values = model_space.space.as_vector(vector)
                invalid_reasons = ccs.invalid_reasons(model_space.space, values)
            except (ContractError, KeyError, TypeError, ValueError):
                valid = False
                continue
            if actual_sha != vector_sha or invalid_reasons or vector_sha in seen_hashes:
                valid = False
                continue
            seen_hashes.add(str(vector_sha))
            normalized.append({
                "parameter_vector": {
                    name: vector[name] for name in model_space.names
                },
                "parameter_vector_sha256": str(vector_sha),
            })
        result[model] = sorted(
            normalized, key=lambda record: str(record["parameter_vector_sha256"])
        )
    if not valid or any(len(result.get(model, ())) != len(value[model]) for model in nc.MODELS):
        problems.append(
            f"the measured-pilot {label} requires unique, exact, admissible "
            "model-specific vector identities"
        )
        return None
    return result


def _pilot_measurement_facts(
    pilot: Mapping,
    pilot_path: Path,
    *,
    plan: nc.FrozenPlan,
    problems: list[str],
) -> dict[str, object] | None:
    """Parse raw pilot rows and independently recompute the declared summary.

    The pilot summary is review metadata, not a source of timings.  Only the
    hash-bound raw rows are allowed to supply trajectory count, elapsed-time
    statistics or peak memory for downstream pricing.
    """
    raw_path = _referenced_evidence_file(pilot_path, pilot.get("raw_measurement_path"))
    if raw_path is None or not raw_path.is_file():
        problems.append("the measured-pilot raw measurement source is missing")
        return None
    recorded_raw_sha = pilot.get("raw_measurement_sha256")
    if not _is_sha256(recorded_raw_sha) \
            or recorded_raw_sha != ccs.sha256_file(raw_path):
        problems.append("the measured-pilot raw-measurement hash is stale")
        return None
    try:
        raw = _load_strict_json(raw_path)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        problems.append(f"the measured-pilot raw measurement is not strict JSON: {exc}")
        return None
    if not isinstance(raw, Mapping):
        problems.append("the measured-pilot raw measurement is not a JSON object")
        return None
    if raw.get("schema") != RAW_PILOT_SCHEMA:
        problems.append(
            f"the measured-pilot raw measurement has no current schema ({RAW_PILOT_SCHEMA})"
        )
    if raw.get("status") != "FINAL":
        problems.append("the measured-pilot raw measurement status is not FINAL")
    if raw.get("plan_sha256") != pilot.get("plan_sha256"):
        problems.append("the measured-pilot raw measurement is bound to a different plan")
    if raw.get("scientific_code_sha256") != pilot.get("scientific_code_sha256"):
        problems.append("the measured-pilot raw measurement scientific code identity differs")
    if raw.get("models") != list(nc.MODELS):
        problems.append("the measured-pilot raw measurement model identities are incomplete")
    if raw.get("discovery_seeds") != list(plan.seeds.discovery):
        problems.append("the measured-pilot raw measurement does not declare the signed seed block")
    machine = pilot.get("machine_profile")
    if not isinstance(machine, Mapping) or not machine or raw.get("machine_profile") != machine:
        problems.append("the measured-pilot raw measurement machine identity is missing or different")
    scheduler = pilot.get("scheduler_request")
    if not isinstance(scheduler, Mapping) or not scheduler \
            or raw.get("scheduler_request") != scheduler:
        problems.append("the measured-pilot raw measurement scheduler request is missing or different")
        return None
    elif _native_uint32(scheduler.get("billable_resources")) in (None, 0) \
            or not isinstance(scheduler.get("resource_unit"), str) \
            or not scheduler["resource_unit"].strip():
        problems.append(
            "the measured-pilot scheduler request needs positive uint32 "
            "billable_resources and a resource_unit"
        )
        return None

    rows = raw.get("rows")
    minimum_rows = 2 * len(plan.seeds.discovery)
    if not isinstance(rows, list) or len(rows) < minimum_rows:
        problems.append(
            "the measured-pilot raw measurement must contain every signed discovery "
            "seed once for each model"
        )
        return None
    try:
        model_spaces = _model_spaces(plan)
        expected_config_sha = {
            model: _scientific_config_sha256(plan, model_spaces[model])
            for model in nc.MODELS
        }
        expected_space_sha = {
            model: _pilot_parameter_space_sha256(model_spaces[model])
            for model in nc.MODELS
        }
        base_config_path = Path(str(plan.require("models.base_config")))
        if not base_config_path.is_absolute():
            base_config_path = ROOT / base_config_path
        expected_base_config_sha = ccs.sha256_file(base_config_path)
    except Exception as exc:  # noqa: BLE001 - identity cannot be inferred on failure
        problems.append(
            f"the measured-pilot scientific config identities could not be resolved: "
            f"{type(exc).__name__}: {exc}"
        )
        return None
    if raw.get("parameter_space_sha256_by_model") != expected_space_sha:
        problems.append(
            "the measured-pilot raw measurement is not bound to the exact signed "
            "M0/M1 parameter spaces"
        )

    selection_loaded = _load_pilot_companion(
        pilot,
        pilot_path,
        path_field="selection_protocol_path",
        sha_field="selection_protocol_sha256",
        label="pre-pilot vector-selection protocol",
        problems=problems,
    )
    selection_vectors: dict[str, list[dict[str, object]]] | None = None
    authorized_at: datetime | None = None
    if selection_loaded is not None:
        selection_path, selection = selection_loaded
        selection_required = {
            "schema", "status", "plan_sha256", "scientific_code_sha256",
            "base_config_sha256", "scientific_config_sha256_by_model",
            "parameter_space_sha256_by_model", "selected_vectors_by_model",
            "scientific_rationale", "pilot_authorized", "authorized_by",
            "authorized_utc",
        }
        if set(selection) != selection_required:
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol has an "
                "invalid typed shape"
            )
        if selection.get("schema") != PILOT_SELECTION_PROTOCOL_SCHEMA:
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol has no "
                f"current schema ({PILOT_SELECTION_PROTOCOL_SCHEMA})"
            )
        if selection.get("status") != "AUTHORIZED" \
                or selection.get("pilot_authorized") is not True:
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol is not authorized"
            )
        expected_bindings = {
            "plan_sha256": plan.plan_sha256,
            "scientific_code_sha256": _code_sha256(),
            "base_config_sha256": expected_base_config_sha,
            "scientific_config_sha256_by_model": expected_config_sha,
            "parameter_space_sha256_by_model": expected_space_sha,
        }
        if any(selection.get(key) != value for key, value in expected_bindings.items()):
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol has stale "
                "plan, code, config, or parameter-space bindings"
            )
        if not isinstance(selection.get("scientific_rationale"), str) \
                or not selection["scientific_rationale"].strip():
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol lacks a "
                "scientific rationale"
            )
        if not isinstance(selection.get("authorized_by"), str) \
                or not selection["authorized_by"].strip():
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol lacks authorized_by"
            )
        authorized_at = _utc_datetime(selection.get("authorized_utc"))
        if authorized_at is None:
            problems.append(
                "the measured-pilot pre-pilot vector-selection protocol has an invalid "
                "or naive authorized_utc"
            )
        selection_vectors = _pilot_vector_records(
            selection.get("selected_vectors_by_model"),
            model_spaces=model_spaces,
            label="pre-pilot vector-selection protocol",
            problems=problems,
        )
        raw_selection_path = _referenced_evidence_file(
            raw_path, raw.get("selection_protocol_path")
        )
        if raw_selection_path is None or raw_selection_path.resolve() != selection_path.resolve() \
                or raw.get("selection_protocol_sha256") != pilot.get(
                    "selection_protocol_sha256"
                ):
            problems.append(
                "the measured-pilot raw measurement does not bind the exact authorized "
                "vector-selection protocol"
            )
    parsed_rows: dict[str, list[dict[str, object]]] = {
        model: [] for model in nc.MODELS
    }
    trajectory_ids: list[str] = []
    vector_seed_identities: set[tuple[str, int]] = set()
    raw_vectors_by_model: dict[str, dict[str, dict[str, object]]] = {
        model: {} for model in nc.MODELS
    }
    raw_started_at: list[datetime] = []
    raw_completed_at: list[datetime] = []
    malformed = False
    failed = False
    duplicate_vector_seed = False
    inadmissible_space_rows: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            malformed = True
            continue
        row_malformed = False
        model = row.get("model")
        if model not in nc.MODELS:
            malformed = True
            continue
        trajectory_id = row.get("trajectory_id")
        if not isinstance(trajectory_id, str) or not trajectory_id.strip():
            row_malformed = True
        else:
            trajectory_ids.append(trajectory_id)
        seed = _native_uint32(row.get("seed"))
        vector = row.get("parameter_vector")
        vector_sha = row.get("parameter_vector_sha256")
        config_sha = row.get("scientific_config_sha256")
        vector_identity_valid = isinstance(vector, Mapping)
        vector_space_valid = False
        if vector_identity_valid:
            try:
                vector_identity_valid = (
                    set(vector) == set(model_spaces[model].names)
                    and
                    all(_is_finite_json_number(value) for value in vector.values())
                    and nc.vector_hash(vector, names=model_spaces[model].names) == vector_sha
                )
                if vector_identity_valid:
                    vector_values = model_spaces[model].space.as_vector(vector)
                    invalid_reasons = ccs.invalid_reasons(
                        model_spaces[model].space, vector_values
                    )
                    vector_space_valid = not invalid_reasons
                    if invalid_reasons:
                        inadmissible_space_rows.append(
                            f"{trajectory_id!r}/{model}: {invalid_reasons}"
                        )
            except (ContractError, KeyError, TypeError, ValueError):
                vector_identity_valid = False
        if seed is None or seed not in plan.seeds.discovery \
                or not _is_sha256(vector_sha) or not vector_identity_valid \
                or not vector_space_valid \
                or config_sha != expected_config_sha[model]:
            row_malformed = True
        if row.get("status") != "SUCCESS":
            failed = True
        seconds = row.get("elapsed_seconds")
        memory = row.get("peak_memory_bytes")
        started_at = _utc_datetime(row.get("started_utc"))
        completed_at = _utc_datetime(row.get("completed_utc"))
        if started_at is None or completed_at is None or completed_at < started_at:
            row_malformed = True
        if not _is_finite_json_number(seconds) or float(seconds) <= 0.0:
            row_malformed = True
        if _native_positive_int(memory) is None:
            row_malformed = True
        if row_malformed:
            malformed = True
        elif seed is not None:
            # Model is deliberately not part of this key: the same timed
            # vector/seed observation cannot be presented as fresh evidence
            # for both M0 and M1.  Their scientific-config hashes remain
            # separately and exactly checked above.
            identity = (str(vector_sha), seed)
            if identity in vector_seed_identities:
                duplicate_vector_seed = True
            vector_seed_identities.add(identity)
            raw_vectors_by_model[str(model)][str(vector_sha)] = {
                "parameter_vector": {
                    name: vector[name] for name in model_spaces[str(model)].names
                },
                "parameter_vector_sha256": str(vector_sha),
            }
            raw_started_at.append(started_at)
            raw_completed_at.append(completed_at)
            parsed_rows[str(model)].append({
                "trajectory_id": str(trajectory_id),
                "seed": seed,
                "elapsed_seconds": float(seconds),
                "peak_memory_bytes": int(memory),
            })
    if malformed or len(trajectory_ids) != len(rows) or sum(
        len(model_rows) for model_rows in parsed_rows.values()
    ) != len(rows):
        if inadmissible_space_rows:
            problems.append(
                "the measured-pilot raw measurement contains parameter vectors outside "
                "the exact signed model-specific space bounds or violate its declared "
                "joint constraints: " + "; ".join(inadmissible_space_rows)
            )
        problems.append(
            "the measured-pilot raw rows require model, non-empty trajectory_id, exact "
            "uint32 signed seed, complete finite parameter vector with matching SHA256, "
            "matching scientific-config "
            "SHA256, SUCCESS status, positive finite elapsed_seconds and positive integer "
            "peak_memory_bytes, plus valid UTC started_utc/completed_utc chronology"
        )
        return None
    if failed:
        problems.append("the measured-pilot raw measurement contains a failed trajectory")
        return None
    if len(set(trajectory_ids)) != len(trajectory_ids):
        problems.append("the measured-pilot raw measurement contains duplicate trajectory ids")
        return None
    if duplicate_vector_seed:
        problems.append(
            "the measured-pilot raw measurement repeats a vector/seed trajectory"
        )
        return None

    normalized_raw_vectors = {
        model: sorted(
            raw_vectors_by_model[model].values(),
            key=lambda record: str(record["parameter_vector_sha256"]),
        )
        for model in nc.MODELS
    }
    if selection_vectors != normalized_raw_vectors:
        problems.append(
            "the measured-pilot timed vectors differ from the exact pre-pilot "
            "vector-selection protocol"
        )
    if authorized_at is not None and raw_started_at + raw_completed_at \
            and any(authorized_at >= timestamp
                    for timestamp in raw_started_at + raw_completed_at):
        problems.append(
            "the measured-pilot authorization does not strictly precede every raw "
            "measurement timestamp"
        )

    approval_loaded = _load_pilot_companion(
        pilot,
        pilot_path,
        path_field="scientific_approval_path",
        sha_field="scientific_approval_sha256",
        label="post-pilot scientific approval",
        problems=problems,
    )
    if approval_loaded is not None:
        _, approval = approval_loaded
        approval_required = {
            "schema", "status", "approved", "raw_measurement_sha256",
            "timed_vectors_by_model", "scientific_rationale", "approved_by",
            "approved_utc",
        }
        if set(approval) != approval_required:
            problems.append(
                "the measured-pilot post-pilot scientific approval has an invalid "
                "typed shape"
            )
        if approval.get("schema") != PILOT_SCIENTIFIC_APPROVAL_SCHEMA:
            problems.append(
                "the measured-pilot post-pilot scientific approval has no current "
                f"schema ({PILOT_SCIENTIFIC_APPROVAL_SCHEMA})"
            )
        if approval.get("status") != "APPROVED" or approval.get("approved") is not True:
            problems.append(
                "the measured-pilot post-pilot scientific approval is not approved"
            )
        if approval.get("raw_measurement_sha256") != recorded_raw_sha:
            problems.append(
                "the measured-pilot post-pilot scientific approval has a stale raw "
                "measurement hash"
            )
        approved_vectors = _pilot_vector_records(
            approval.get("timed_vectors_by_model"),
            model_spaces=model_spaces,
            label="post-pilot scientific approval",
            problems=problems,
        )
        if approved_vectors != normalized_raw_vectors:
            problems.append(
                "the measured-pilot post-pilot scientific approval does not cover the "
                "exact timed vectors"
            )
        if not isinstance(approval.get("scientific_rationale"), str) \
                or not approval["scientific_rationale"].strip():
            problems.append(
                "the measured-pilot post-pilot scientific approval lacks a scientific "
                "rationale"
            )
        if not isinstance(approval.get("approved_by"), str) \
                or not approval["approved_by"].strip():
            problems.append(
                "the measured-pilot post-pilot scientific approval lacks approved_by"
            )
        approved_at = _utc_datetime(approval.get("approved_utc"))
        if approved_at is None:
            problems.append(
                "the measured-pilot post-pilot scientific approval has an invalid or "
                "naive approved_utc"
            )
        elif raw_started_at + raw_completed_at and any(
            approved_at <= timestamp for timestamp in raw_started_at + raw_completed_at
        ):
            problems.append(
                "the measured-pilot scientific approval does not strictly follow every "
                "raw measurement timestamp"
            )

    facts: dict[str, object] = {
        "models": {},
        "billable_resources": int(scheduler["billable_resources"]),
        "resource_unit": str(scheduler["resource_unit"]),
    }
    declared_models = pilot.get("model_summaries")
    if not isinstance(declared_models, Mapping) or set(declared_models) != set(nc.MODELS):
        problems.append("the measured-pilot artifact lacks exact M0 and M1 model summaries")
        declared_models = {}
    for model in nc.MODELS:
        model_rows = parsed_rows[model]
        seeds = [int(row["seed"]) for row in model_rows]
        if len(model_rows) != len(plan.seeds.discovery) \
                or len(set(seeds)) != len(seeds) \
                or set(seeds) != set(plan.seeds.discovery):
            problems.append(
                f"the measured-pilot {model} rows do not cover every signed discovery seed "
                "exactly once"
            )
        elapsed = [float(row["elapsed_seconds"]) for row in model_rows]
        memory = [int(row["peak_memory_bytes"]) for row in model_rows]
        if not elapsed or not memory:
            continue
        try:
            total_elapsed = math.fsum(elapsed)
        except OverflowError:
            total_elapsed = math.inf
        if not math.isfinite(total_elapsed):
            problems.append(
                f"the measured-pilot {model} elapsed-time aggregate is not finite"
            )
            continue
        model_facts = {
            "n_trajectories": len(model_rows),
            "trajectory_ids": [str(row["trajectory_id"]) for row in model_rows],
            "seeds": seeds,
            "mean_elapsed_seconds": total_elapsed / len(elapsed),
            "max_elapsed_seconds": max(elapsed),
            "total_elapsed_seconds": total_elapsed,
            "peak_memory_bytes": max(memory),
        }
        facts["models"][model] = model_facts
        declared = declared_models.get(model)
        if not isinstance(declared, Mapping):
            problems.append(f"the measured-pilot artifact has no typed {model} summary")
            continue
        if type(declared.get("n_trajectories")) is not int \
                or declared.get("n_trajectories") != model_facts["n_trajectories"]:
            problems.append(
                f"the measured-pilot {model} n_trajectories does not match its raw rows"
            )
        if declared.get("trajectory_ids") != model_facts["trajectory_ids"]:
            problems.append(
                f"the measured-pilot {model} trajectory ids do not match its raw rows"
            )
        if declared.get("seeds") != model_facts["seeds"]:
            problems.append(f"the measured-pilot {model} seeds do not match its raw rows")
        summary = declared.get("timing_summary")
        if not isinstance(summary, Mapping):
            problems.append(f"the measured-pilot {model} has no typed timing_summary")
        else:
            if summary.get("statistic") != PILOT_TIMING_STATISTIC:
                problems.append(
                    f"the measured-pilot {model} timing statistic is not the declared mean"
                )
            for key in (
                "mean_elapsed_seconds", "max_elapsed_seconds", "total_elapsed_seconds"
            ):
                if not _numbers_match(summary.get(key), float(model_facts[key])):
                    problems.append(
                        f"the measured-pilot {model} {key} does not match its raw rows"
                    )
            if type(summary.get("n_successful_trajectories")) is not int \
                    or summary.get("n_successful_trajectories") != model_facts[
                        "n_trajectories"
                    ]:
                problems.append(
                    f"the measured-pilot {model} successful trajectory count does not "
                    "match its raw rows"
                )
        if type(declared.get("peak_memory_bytes")) is not int \
                or declared.get("peak_memory_bytes") != model_facts["peak_memory_bytes"]:
            problems.append(
                f"the measured-pilot {model} peak memory does not match its raw rows"
            )
    return facts


def _validate_priced_budget(
    priced: Mapping,
    priced_path: Path,
    *,
    plan: nc.FrozenPlan,
    pilot: Mapping,
    pilot_facts: Mapping[str, object] | None,
    pilot_path: Path | None,
    problems: list[str],
) -> None:
    """Recompute every stage cost and the entire-workflow totals."""
    if priced.get("cap_scope") != "per_stage_ledger":
        problems.append("the priced-budget artifact does not declare per-stage ledger caps")
    if priced.get("cost_scope") != "entire_workflow":
        problems.append("the priced-budget artifact does not price the entire workflow separately")
    if priced.get("cost_formula") != PRICING_FORMULA:
        problems.append("the priced-budget artifact has an unknown or arbitrary cost formula")
    if pilot_path is not None:
        if not _paths_match(priced.get("measured_pilot_path"), pilot_path):
            problems.append("the priced-budget artifact records the wrong measured-pilot path")
        recorded_pilot_sha = priced.get("measured_pilot_sha256")
        if not _is_sha256(recorded_pilot_sha) \
                or recorded_pilot_sha != ccs.sha256_file(pilot_path):
            problems.append("the priced-budget artifact is not bound to the measured pilot")
    if not _has_evidence_value(priced.get("machine_profile")):
        problems.append("the priced-budget artifact records no priced machine profile")
    elif pilot and priced.get("machine_profile") != pilot.get("machine_profile"):
        problems.append("the priced-budget machine profile differs from the measured pilot")
    if not isinstance(priced.get("approved_by"), str) \
            or not priced["approved_by"].strip():
        problems.append("the priced-budget artifact has no approved_by")
    if not _valid_utc_timestamp(priced.get("approved_utc")):
        problems.append("the priced-budget artifact has no valid approved_utc")

    source_path = _referenced_evidence_file(priced_path, priced.get("pricing_source"))
    source: Mapping | None = None
    if source_path is None or not source_path.is_file():
        problems.append("the priced-budget pricing source is missing")
    elif not _is_sha256(priced.get("pricing_source_sha256")) \
            or priced.get("pricing_source_sha256") != ccs.sha256_file(source_path):
        problems.append("the priced-budget pricing-source hash is stale")
    else:
        try:
            parsed_source = _load_strict_json(source_path)
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            problems.append(f"the priced-budget pricing source is not strict JSON: {exc}")
        else:
            if not isinstance(parsed_source, Mapping):
                problems.append("the priced-budget pricing source is not a JSON object")
            else:
                source = parsed_source

    cost_inputs = priced.get("cost_inputs")
    if not isinstance(cost_inputs, Mapping):
        problems.append("the priced-budget artifact has no typed cost_inputs")
        cost_inputs = {}
    if source is not None:
        if source.get("schema") != PRICING_SOURCE_SCHEMA:
            problems.append(
                f"the priced-budget pricing source has no current schema ({PRICING_SOURCE_SCHEMA})"
            )
        if source.get("status") != "FINAL":
            problems.append("the priced-budget pricing source status is not FINAL")
        if source.get("machine_profile") != priced.get("machine_profile"):
            problems.append("the priced-budget pricing source is for a different machine profile")
        if not isinstance(source.get("source_reference"), str) \
                or not source["source_reference"].strip():
            problems.append("the priced-budget pricing source has no source reference")
        if not _valid_utc_timestamp(source.get("effective_utc")):
            problems.append("the priced-budget pricing source has no valid effective_utc")
        expected_inputs = {
            "currency_or_credit_unit": source.get("currency_or_credit_unit"),
            "resource_unit": source.get("resource_unit"),
            "billing_unit": source.get("billing_unit"),
            "seconds_per_billing_unit": source.get("seconds_per_billing_unit"),
            "unit_price": source.get("unit_price"),
        }
        if dict(cost_inputs) != expected_inputs:
            problems.append("the priced-budget cost_inputs differ from the hashed pricing source")
    currency = cost_inputs.get("currency_or_credit_unit")
    resource_unit = cost_inputs.get("resource_unit")
    billing_unit = cost_inputs.get("billing_unit")
    unit_seconds = cost_inputs.get("seconds_per_billing_unit")
    unit_price = cost_inputs.get("unit_price")
    if not isinstance(currency, str) or not currency.strip():
        problems.append("the priced-budget artifact records no currency or credit unit")
    if not isinstance(resource_unit, str) or not resource_unit.strip():
        problems.append("the priced-budget artifact records no resource unit")
    if billing_unit != "resource_hour":
        problems.append("the priced-budget billing unit must be resource_hour")
    if not _numbers_match(unit_seconds, 3600.0):
        problems.append("the priced-budget billing unit must contain 3600 seconds")
    if not _is_finite_json_number(unit_price) or float(unit_price) <= 0.0:
        problems.append("the priced-budget artifact has no positive finite unit price")

    timing_inputs = priced.get("timing_inputs")
    if not isinstance(timing_inputs, Mapping) or set(timing_inputs) != set(nc.MODELS):
        problems.append("the priced-budget artifact needs exact M0 and M1 timing_inputs")
        timing_inputs = {}
    pilot_models = pilot_facts.get("models") if pilot_facts is not None else None
    if pilot_facts is not None:
        if resource_unit != pilot_facts.get("resource_unit"):
            problems.append("the priced-budget resource unit differs from the measured pilot")
        if not isinstance(pilot_models, Mapping) or set(pilot_models) != set(nc.MODELS):
            problems.append("the measured pilot has no complete model-specific timing basis")
            pilot_models = None
        else:
            for model in nc.MODELS:
                measured = pilot_models[model]
                declared = timing_inputs.get(model)
                if not isinstance(declared, Mapping) or declared.get(
                    "statistic"
                ) != PILOT_TIMING_STATISTIC \
                        or declared.get("n_successful_trajectories") != measured[
                            "n_trajectories"
                        ] or not _numbers_match(
                            declared.get("expected_seconds_per_trajectory"),
                            float(measured["mean_elapsed_seconds"]),
                        ) or not _numbers_match(
                            declared.get("observed_max_seconds_per_trajectory"),
                            float(measured["max_elapsed_seconds"]),
                        ):
                    problems.append(
                        f"the priced-budget {model} timing_inputs differ from the "
                        "measured pilot"
                    )

    rows = priced.get("stage_costs")
    if not isinstance(rows, list):
        problems.append("the priced-budget artifact has no typed per-stage cost rows")
        return
    by_key: dict[tuple[str, str], Mapping] = {}
    malformed_rows = False
    for row in rows:
        if not isinstance(row, Mapping):
            malformed_rows = True
            continue
        key = (str(row.get("model", "")), str(row.get("stage", "")))
        if key in by_key:
            malformed_rows = True
        by_key[key] = row
    if malformed_rows or set(by_key) != set(PRICED_STAGE_KEYS) or len(rows) != len(
        PRICED_STAGE_KEYS
    ):
        problems.append(
            "the priced-budget stage rows must cover each M0/M1 discovery, confirmation "
            "and bridges stage exactly once"
        )

    if pilot_models is None or not _is_finite_json_number(unit_price) \
            or float(unit_price) <= 0.0 or not _numbers_match(unit_seconds, 3600.0):
        return
    max_trajectories = int(plan.require("budget.max_trajectories"))
    max_attempts = int(plan.require("budget.max_attempts"))
    wall_cap = float(plan.require("budget.wall_time_cap_seconds"))
    billable_resources = int(pilot_facts["billable_resources"])
    valid_expected_costs: list[float] = []
    valid_worst_costs: list[float] = []
    for key in PRICED_STAGE_KEYS:
        row = by_key.get(key)
        if row is None:
            continue
        model, stage = key
        measured = pilot_models[model]
        expected_resource_seconds = _finite_product(
            max_trajectories,
            measured["mean_elapsed_seconds"],
            billable_resources,
        )
        # Observed pilot maxima are measurements, not hard runtime bounds.  The
        # signed wall-clock cap is the only cap-derived worst-case billing
        # bound for a stage under the declared billable scheduler allocation.
        worst_resource_seconds = _finite_product(wall_cap, billable_resources)
        if expected_resource_seconds is None or worst_resource_seconds is None:
            problems.append(
                f"the priced-budget {model}/{stage} resource-second arithmetic "
                "is not finite"
            )
            continue
        if expected_resource_seconds > worst_resource_seconds:
            problems.append(
                f"the signed {model}/{stage} wall-time cap cannot fund the expected "
                "full-capacity workload under the measured model-specific timing"
            )
        expected_cost = _finite_product(
            expected_resource_seconds / float(unit_seconds), unit_price
        )
        worst_cost = _finite_product(
            worst_resource_seconds / float(unit_seconds), unit_price
        )
        if expected_cost is None or worst_cost is None:
            problems.append(
                f"the priced-budget {model}/{stage} cost arithmetic is not finite"
            )
            continue
        if type(row.get("max_trajectories")) is not int \
                or row.get("max_trajectories") != max_trajectories \
                or type(row.get("max_attempts")) is not int \
                or row.get("max_attempts") != max_attempts \
                or not _numbers_match(row.get("wall_time_cap_seconds"), wall_cap):
            problems.append(
                f"the priced-budget {model}/{stage} row does not match the signed "
                "per-stage caps"
            )
        expected_values = {
            "expected_resource_seconds": expected_resource_seconds,
            "worst_case_resource_seconds": worst_resource_seconds,
            "expected_cost": expected_cost,
            "worst_case_cost": worst_cost,
        }
        for field, expected in expected_values.items():
            if not _numbers_match(row.get(field), expected):
                problems.append(
                    f"the priced-budget {model}/{stage} {field} is not recomputable"
                )
        valid_expected_costs.append(expected_cost)
        valid_worst_costs.append(worst_cost)

    try:
        valid_expected_total = math.fsum(valid_expected_costs)
        valid_worst_total = math.fsum(valid_worst_costs)
    except OverflowError:
        problems.append("the priced-budget entire-workflow cost arithmetic is not finite")
        return
    if not math.isfinite(valid_expected_total) or not math.isfinite(valid_worst_total):
        problems.append("the priced-budget entire-workflow cost arithmetic is not finite")
        return

    totals = priced.get("workflow_totals")
    if not isinstance(totals, Mapping):
        problems.append("the priced-budget artifact has no typed entire-workflow totals")
    else:
        if type(totals.get("n_priced_stages")) is not int \
                or totals.get("n_priced_stages") != len(PRICED_STAGE_KEYS):
            problems.append("the priced-budget workflow stage count is not recomputable")
        if not _numbers_match(totals.get("expected_cost"), valid_expected_total):
            problems.append("the priced-budget entire-workflow expected cost is not recomputable")
        if not _numbers_match(totals.get("worst_case_cost"), valid_worst_total):
            problems.append("the priced-budget entire-workflow worst-case cost is not recomputable")


def _has_evidence_value(value: object) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return bool(value)
    return bool(str(value or "").strip())


def _valid_utc_timestamp(value: object) -> bool:
    text = str(value or "")
    if not text.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _utc_datetime(value: object) -> datetime | None:
    """Return one timezone-aware UTC instant, rejecting naive or non-UTC text."""
    if not _valid_utc_timestamp(value):
        return None
    return datetime.fromisoformat(str(value)[:-1] + "+00:00")


def _valid_iso_date(value: object) -> bool:
    try:
        date.fromisoformat(str(value or ""))
    except ValueError:
        return False
    return True


def _production_readiness_problems(plan: nc.FrozenPlan) -> list[str]:
    """Validate every non-decision prerequisite without running the engine."""
    problems: list[str] = []
    base = Path(str(plan.require("models.base_config")))
    if not base.is_absolute():
        base = ROOT / base
    if not base.exists():
        problems.append(f"base config is missing: {base}")
        base_sha = ""
    else:
        base_sha = ccs.sha256_file(base)
        payload = _load_strict_json(base)
        deterministic = payload.get("run_control", {}).get("deterministic_mode", False)
        if isinstance(deterministic, Mapping):
            deterministic = deterministic.get("value", False)
        if bool(deterministic):
            problems.append("base config has deterministic_mode=true")
        try:
            ssd.validate_shared_base_config(payload)
        except ssd.StageStructuredDiagnosisConfigError as exc:
            problems.append(f"base config is not a safe shared M0/M1 config: {exc}")

    m0_space = Path(str(plan.require("models.m0.calib_space")))
    if not m0_space.is_absolute():
        m0_space = ROOT / m0_space
    if not m0_space.exists():
        problems.append(f"M0 calibration space is missing: {m0_space}")
        m0_sha = ""
    else:
        m0_sha = ccs.sha256_file(m0_space)

    prerequisites = plan.get("production_prerequisites")
    if not isinstance(prerequisites, Mapping):
        return problems + [
            "production_prerequisites is missing from the signed plan"
        ]
    paths: dict[str, Path] = {}
    for key in (
        "config_registry", "acceptance_inputs", "measured_pilot", "priced_budget",
        "seed_disjointness_audit", "identity_gate", "m1_anchor_confirmation",
    ):
        declared = prerequisites.get(key)
        candidate = Path(str(declared)) if declared else None
        if candidate is not None and not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate is None or not candidate.exists():
            problems.append(f"production prerequisite {key!r} is missing")
        else:
            paths[key] = candidate

    registry = paths.get("config_registry")
    if registry is not None:
        if registry.name != "CONFIG_REGISTRY.md":
            problems.append("config_registry must be named CONFIG_REGISTRY.md")
        else:
            text = registry.read_text()
            lowered = text.lower()
            if "placeholder" in lowered or "fixture only" in lowered:
                problems.append("CONFIG_REGISTRY.md declares placeholder/fixture evidence")
            status, bindings, duplicates = _registry_bindings(text)
            if status != "FINAL":
                problems.append("CONFIG_REGISTRY.md status is not FINAL")
            if duplicates:
                problems.append(
                    f"CONFIG_REGISTRY.md repeats binding(s) {sorted(duplicates)}"
                )
            expected_paths = {
                "plan path": Path(plan.plan_path),
                "base-config path": base,
                "M0 calibration-space path": m0_space,
            }
            target_register = _evidence_path(plan.targets.register_path)
            if target_register is not None:
                expected_paths["target-register path"] = target_register
            for label, expected in expected_paths.items():
                if not _paths_match(bindings.get(label), expected):
                    problems.append(f"CONFIG_REGISTRY.md {label} does not match the signed plan")
            expected_scalars = {
                "plan SHA-256": plan.plan_sha256,
                "base-config SHA-256": base_sha,
                "M0 calibration-space SHA-256": m0_sha,
                "target-register SHA-256": plan.targets.register_sha256,
                "scientific-code SHA-256": _code_sha256(),
            }
            for label, expected in expected_scalars.items():
                if bindings.get(label) != expected:
                    problems.append(f"CONFIG_REGISTRY.md {label} does not match current evidence")
            json_bindings: dict[str, object] = {
                "target order": list(plan.targets.frozen_order),
                "scientific runtime": nig.scientific_runtime_fingerprint(),
                "discovery seeds": list(plan.seeds.discovery),
                "confirmation seeds": list(plan.seeds.confirmation),
            }
            for label, expected in json_bindings.items():
                try:
                    actual = nc.strict_json_loads(
                        bindings.get(label, ""), what=f"CONFIG_REGISTRY.md {label}"
                    )
                except (TypeError, ContractError):
                    actual = None
                if actual != expected:
                    problems.append(f"CONFIG_REGISTRY.md {label} does not match current evidence")
            for label in ("producing command", "reviewer"):
                if not str(bindings.get(label, "")).strip():
                    problems.append(f"CONFIG_REGISTRY.md {label} is missing")
            if not _valid_utc_timestamp(bindings.get("generated UTC")):
                problems.append("CONFIG_REGISTRY.md generated UTC is missing or invalid")
            if not _valid_iso_date(bindings.get("review date")):
                problems.append("CONFIG_REGISTRY.md review date is missing or invalid")

    acceptance_path = paths.get("acceptance_inputs")
    validated_acceptance: dict | None = None
    if acceptance_path is not None:
        try:
            validated_acceptance = nai.validate_package(
                plan,
                acceptance_path,
                root=ROOT,
                scientific_code_sha256=_code_sha256(),
                scientific_runtime=nig.scientific_runtime_fingerprint(),
            )
        except ContractError as exc:
            problems.append(f"production prerequisite 'acceptance_inputs' failed: {exc}")

    m1_model: nc.ModelSpace | None = None
    try:
        m1_model = _model_spaces(plan)[nc.MODEL_M1]
    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
        problems.append(f"the production M1 model space could not be loaded: {exc}")

    identity_gate_path = paths.get("identity_gate")
    if identity_gate_path is not None and m1_model is not None:
        try:
            nig.assert_gate_satisfied(
                identity_gate_path,
                root=ROOT,
                expected_base_config=str(plan.require("models.base_config")),
                expected_plan_sha256=plan.plan_sha256,
                expected_scientific_config_sha256=_scientific_config_sha256(
                    plan, m1_model
                ),
            )
        except (ContractError, OSError, json.JSONDecodeError) as exc:
            problems.append(f"production prerequisite 'identity_gate' failed: {exc}")

    m1_confirmation_path = paths.get("m1_anchor_confirmation")
    if (
        m1_confirmation_path is not None
        and m1_model is not None
        and validated_acceptance is not None
    ):
        try:
            confirmation_payload = _load_strict_json(m1_confirmation_path)
            if not isinstance(confirmation_payload, Mapping):
                raise ContractError(
                    "M1 anchor-confirmation evidence must be a JSON object"
                )
            anchors_path = _referenced_evidence_file(
                m1_confirmation_path, confirmation_payload.get("anchors_path")
            )
            if anchors_path is None or not anchors_path.is_file():
                raise ContractError(
                    "M1 anchor-confirmation evidence references no existing anchor table"
                )
            anchors = _load_anchors(anchors_path, m1_model)
            _validate_m1_anchor_evidence(
                m1_confirmation_path,
                anchors_path=anchors_path,
                anchors=anchors,
                plan=plan,
                model=m1_model,
                acceptance_identity=validated_acceptance[
                    "acceptance_input_identity"
                ],
                observed=_load_strict_json(
                    Path(validated_acceptance["observed_path"])
                ),
                base_variance=_load_strict_json(
                    Path(validated_acceptance["base_variance_path"])
                ),
            )
        except (ContractError, OSError, json.JSONDecodeError) as exc:
            problems.append(
                f"production prerequisite 'm1_anchor_confirmation' failed: {exc}"
            )

    loaded: dict[str, Mapping] = {}
    for key in ("measured_pilot", "priced_budget", "seed_disjointness_audit"):
        path = paths.get(key)
        if path is None:
            continue
        try:
            if key in {"measured_pilot", "priced_budget"}:
                payload = _load_strict_json(path)
            else:
                payload = seed_audit_tool.strict_json_load(path)
        except (OSError, json.JSONDecodeError, ContractError, ValueError) as exc:
            problems.append(f"production prerequisite {key!r} is not strict JSON: {exc}")
            continue
        if not isinstance(payload, Mapping):
            problems.append(f"production prerequisite {key!r} is not a JSON object")
            continue
        required_schema = {
            "measured_pilot": MEASURED_PILOT_SCHEMA,
            "priced_budget": PRICED_BUDGET_SCHEMA,
            "seed_disjointness_audit": seed_audit_tool.SCHEMA,
        }[key]
        if str(payload.get("schema", "")) != required_schema:
            problems.append(
                f"production prerequisite {key!r} has no current evidence schema "
                f"({required_schema})"
            )
        expected_status = "FINAL"
        if payload.get("status") != expected_status:
            problems.append(
                f"production prerequisite {key!r} status is not {expected_status}"
            )
        if payload.get("production_eligible") is not True:
            problems.append(f"production prerequisite {key!r} is not production-eligible")
        if not _valid_utc_timestamp(payload.get("generated_utc")):
            problems.append(
                f"production prerequisite {key!r} has no valid UTC generation timestamp"
            )
        if not str(payload.get("command", "")).strip():
            problems.append(f"production prerequisite {key!r} records no producing command")
        verdict = payload.get("approved") if key == "priced_budget" else payload.get("passed")
        if verdict is not True:
            problems.append(f"production prerequisite {key!r} did not pass")
        if str(payload.get("plan_sha256", "")) != plan.plan_sha256:
            problems.append(f"production prerequisite {key!r} is not bound to this plan")
        loaded[key] = payload

    audit = loaded.get("seed_disjointness_audit", {})
    if audit:
        scope_payload: Mapping | None = None
        scope_path: Path | None = None
        scope_root: Path | None = None
        resolution_payload: Mapping | None = None
        resolution_path: Path | None = None
        candidate_path: Path | None = None
        recorded_artifact_sha = str(audit.get("artifact_sha256", ""))
        audit_without_sha = dict(audit)
        audit_without_sha.pop("artifact_sha256", None)
        actual_artifact_sha = hashlib.sha256(json.dumps(
            audit_without_sha, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        if recorded_artifact_sha != actual_artifact_sha:
            problems.append("the seed-disjointness audit artifact hash is missing or stale")
        if not _paths_match(audit.get("plan_path"), Path(plan.plan_path)):
            problems.append("the seed-disjointness audit records the wrong signed-plan path")
        if audit.get("deterministic") is not True:
            problems.append("the seed-disjointness audit is not declared deterministic")
        scope = audit.get("scope_manifest")
        if not isinstance(scope, Mapping):
            problems.append("the seed-disjointness audit has no scope_manifest")
        else:
            scope_path = _evidence_path(scope.get("path"))
            if scope_path is None or not scope_path.is_file():
                problems.append("the seed-disjointness audit scope manifest is missing")
            elif str(scope.get("sha256", "")) != ccs.sha256_file(scope_path):
                problems.append("the seed-disjointness audit scope-manifest hash is stale")
            else:
                try:
                    scope_root = (
                        ROOT if scope_path.resolve().is_relative_to(ROOT.resolve())
                        else scope_path.parent
                    )
                    scope_payload = seed_audit_tool.load_scope(
                        scope_path, root=scope_root
                    )
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    scope_payload = None
                    problems.append(
                        "the seed-disjointness scope manifest is invalid: "
                        f"{exc}"
                    )
                if not isinstance(scope_payload, Mapping) or scope_payload.get(
                    "schema"
                ) != seed_audit_tool.SCOPE_SCHEMA:
                    problems.append("the seed-disjointness scope manifest has no current schema")
                else:
                    owner_scope = scope_payload.get("owner_scope")
                    if not isinstance(owner_scope, Mapping) or owner_scope.get(
                        "status"
                    ) != "APPROVED" or not str(owner_scope.get("approved_by", "")).strip() \
                            or not _valid_utc_timestamp(owner_scope.get("approved_utc")) \
                            or not str(owner_scope.get("scope_statement", "")).strip():
                        problems.append(
                            "the seed-disjointness source scope lacks complete owner approval"
                        )
                    source_coverage = scope_payload.get("source_coverage")
                    if not isinstance(source_coverage, Mapping) or source_coverage.get(
                        "status"
                    ) != "COMPLETE":
                        problems.append(
                            "the seed-disjointness source scope lacks complete coverage review"
                        )
            if scope.get("owner_scope_status") != "APPROVED":
                problems.append("the seed-disjointness audit scope is not owner-approved")
            if scope.get("source_coverage_status") != "COMPLETE":
                problems.append("the seed-disjointness audit source coverage is incomplete")
        resolutions = audit.get("finding_resolutions")
        if not isinstance(resolutions, Mapping):
            problems.append("the seed-disjointness audit has no finding_resolutions")
        else:
            resolution_path = _evidence_path(resolutions.get("path"))
            if resolution_path is None or not resolution_path.is_file():
                problems.append("the seed-disjointness resolution document is missing")
            elif str(resolutions.get("file_sha256", "")) != ccs.sha256_file(
                    resolution_path):
                problems.append("the seed-disjointness resolution-file hash is stale")
            else:
                try:
                    scope_record = audit.get("scope_manifest")
                    scope_sha256 = (
                        str(scope_record.get("sha256", ""))
                        if isinstance(scope_record, Mapping) else ""
                    )
                    resolution_payload = seed_audit_tool.load_resolutions(
                        resolution_path,
                        scope_sha256=scope_sha256,
                    )
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    problems.append(
                        "the seed-disjointness resolution document is invalid: "
                        f"{exc}"
                    )
                else:
                    if resolutions.get("payload_sha256") != \
                            seed_audit_tool.canonical_sha256(resolution_payload):
                        problems.append(
                            "the seed-disjointness resolution payload hash is stale"
                        )
            if resolutions.get("status") != "REVIEWED":
                problems.append("the seed-disjointness resolutions are not reviewed")
            if resolutions.get("unresolved") != 0:
                problems.append("the seed-disjointness resolutions leave findings unresolved")
            if not _is_sha256(resolutions.get("payload_sha256")):
                problems.append("the seed-disjointness resolution payload hash is invalid")
        inventory = audit.get("source_inventory")
        if not isinstance(inventory, Mapping):
            problems.append("the seed-disjointness audit has no source inventory")
        else:
            if not isinstance(inventory.get("counts_by_kind"), Mapping) or not inventory.get(
                "counts_by_kind"
            ):
                problems.append("the seed-disjointness audit has no source-inventory counts")
            for field in (
                "inventory_sha256", "scanned_content_index_sha256",
                "excluded_output_index_sha256",
            ):
                if not _is_sha256(inventory.get(field)):
                    problems.append(f"the seed-disjointness audit has no valid {field}")
            for field in (
                "scanned_files", "excluded_output_files", "excluded_output_bytes"
            ):
                try:
                    count = int(inventory.get(field, -1))
                except (TypeError, ValueError):
                    count = -1
                if count < 0:
                    problems.append(f"the seed-disjointness audit has invalid {field}")
            if not isinstance(inventory.get("excluded_output_index"), list):
                problems.append(
                    "the seed-disjointness audit has no excluded-output content index"
                )
        parsed_prior = audit.get("parsed_prior_seeds")
        if not isinstance(parsed_prior, list):
            problems.append("the seed-disjointness audit has no parsed-prior-seed list")
            parsed_prior = []
        try:
            parsed_count = int(audit.get("parsed_prior_seed_count", -1))
        except (TypeError, ValueError):
            parsed_count = -1
        if parsed_count != len(parsed_prior):
            problems.append("the seed-disjointness audit parsed-prior count is inconsistent")
        prior_seeds: set[int] = set()
        malformed_prior = False
        for row in parsed_prior:
            try:
                seed = int(row["seed"] if isinstance(row, Mapping) else row)
            except (KeyError, TypeError, ValueError):
                malformed_prior = True
                continue
            if seed in prior_seeds:
                malformed_prior = True
            prior_seeds.add(seed)
        if malformed_prior:
            problems.append("the seed-disjointness audit parsed-prior list is malformed")
        signed_seeds = set(plan.seeds.discovery) | set(plan.seeds.confirmation) | {
            plan.seeds.proposal_rng
        }
        if prior_seeds & signed_seeds:
            problems.append("the seed-disjointness audit signed blocks overlap prior seeds")
        for field in ("ambiguous_references", "unparseable_references"):
            evidence = audit.get(field)
            if not isinstance(evidence, Mapping) or evidence.get("count") != 0 \
                    or list(evidence.get("examples") or ()) \
                    or evidence.get("examples_truncated") != 0:
                problems.append(f"the seed-disjointness audit has unresolved {field}")
        candidate_blocks = audit.get("candidate_blocks")
        if not isinstance(candidate_blocks, Mapping):
            problems.append("the seed-disjointness audit has no candidate_blocks")
        else:
            if candidate_blocks.get("status") != "SUPPLIED":
                problems.append("the seed-disjointness audit candidate blocks are not supplied")
            candidate_path = _evidence_path(candidate_blocks.get("path"))
            if candidate_path is None or not candidate_path.is_file():
                problems.append("the seed-disjointness candidate-block file is missing")
            elif str(candidate_blocks.get("file_sha256", "")) != ccs.sha256_file(
                    candidate_path):
                problems.append("the seed-disjointness candidate-block file hash is stale")
            else:
                try:
                    candidate_payload = seed_audit_tool.load_candidate_blocks(candidate_path)
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    problems.append(
                        "the seed-disjointness candidate-block file is invalid: "
                        f"{exc}"
                    )
                else:
                    for field in ("discovery", "confirmation", "proposal_rng", "sha256"):
                        if candidate_blocks.get(field) != candidate_payload.get(field):
                            problems.append(
                                "the seed-disjointness candidate-block provenance is stale"
                            )
                            break
            try:
                discovery = tuple(int(seed) for seed in candidate_blocks.get("discovery", ()))
                confirmation = tuple(
                    int(seed) for seed in candidate_blocks.get("confirmation", ())
                )
                proposal_rng = int(candidate_blocks.get("proposal_rng"))
            except (TypeError, ValueError):
                discovery, confirmation, proposal_rng = (), (), -1
            if discovery != plan.seeds.discovery or confirmation != plan.seeds.confirmation \
                    or proposal_rng != plan.seeds.proposal_rng:
                problems.append("the seed-disjointness audit does not cover the signed seed blocks")
            expected_candidate_sha = hashlib.sha256(json.dumps({
                "schema": "robust_nroy_candidate_seed_blocks/1.0.0",
                "discovery": list(plan.seeds.discovery),
                "confirmation": list(plan.seeds.confirmation),
                "proposal_rng": plan.seeds.proposal_rng,
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if candidate_blocks.get("sha256") != expected_candidate_sha:
                problems.append("the seed-disjointness candidate-block hash is not exact")
        disjointness = audit.get("disjointness")
        if not isinstance(disjointness, Mapping) or disjointness.get("status") != "PROVEN":
            problems.append("the seed-disjointness audit does not prove disjointness")
        else:
            checks = disjointness.get("checks")
            required_checks = {
                "discovery_vs_confirmation", "proposal_vs_discovery",
                "proposal_vs_confirmation", "discovery_vs_prior",
                "confirmation_vs_prior", "proposal_vs_prior",
            }
            if not isinstance(checks, Mapping) or set(checks) != required_checks \
                    or any(list(values or ()) for values in checks.values()):
                problems.append("the seed-disjointness audit records a non-empty overlap check")
        if list(audit.get("overlaps", [])):
            problems.append("the seed-disjointness audit records overlapping seeds")
        if list(audit.get("unresolved", [])):
            problems.append("the seed-disjointness audit records unresolved findings")
        if not isinstance(audit.get("limitations"), list):
            problems.append("the seed-disjointness audit has no limitations list")
        if (scope_payload is not None and scope_path is not None and scope_root is not None
                and resolution_payload is not None and resolution_path is not None
                and candidate_path is not None and candidate_path.is_file()):
            try:
                rebuilt_audit = seed_audit_tool.build_audit(
                    scope_root,
                    scope_path,
                    candidate_path,
                    plan_path=Path(plan.plan_path),
                    resolution_path=resolution_path,
                    generated_utc=str(audit.get("generated_utc", "")),
                )
            except (OSError, ValueError, nc.ContractError, json.JSONDecodeError) as exc:
                problems.append(
                    "the seed-disjointness audit cannot be rebuilt from current sources: "
                    f"{exc}"
                )
            else:
                if rebuilt_audit != audit:
                    problems.append(
                        "the seed-disjointness audit differs from a deterministic rebuild "
                        "of the current scope, resolutions, candidates, plan, and mounted sources"
                    )
    pilot = loaded.get("measured_pilot", {})
    pilot_path = paths.get("measured_pilot")
    pilot_facts: dict[str, object] | None = None
    if pilot:
        if not _paths_match(pilot.get("base_config_path"), base):
            problems.append("the measured-pilot artifact records the wrong base-config path")
        if str(pilot.get("base_config_sha256", "")) != base_sha:
            problems.append("the measured-pilot artifact is not bound to the base config")
        if str(pilot.get("m0_calibration_space_sha256", "")) != m0_sha:
            problems.append("the measured-pilot artifact is not bound to the M0 calibration space")
        if str(pilot.get("target_register_sha256", "")) != plan.targets.register_sha256:
            problems.append("the measured-pilot artifact is not bound to the target register")
        if tuple(pilot.get("target_order") or ()) != plan.targets.frozen_order:
            problems.append("the measured-pilot target order differs from the signed contract")
        pilot_seeds = pilot.get("discovery_seeds")
        if pilot_seeds != list(plan.seeds.discovery) or any(
            _native_uint32(seed) is None for seed in pilot_seeds
        ):
            problems.append("the measured-pilot discovery seeds differ from the signed block")
        if str(pilot.get("scientific_code_sha256", "")) != _code_sha256():
            problems.append("the measured-pilot artifact is not bound to the current scientific code")
        if pilot.get("scientific_runtime") != nig.scientific_runtime_fingerprint():
            problems.append("the measured-pilot scientific runtime differs from the current runtime")
        if pilot_path is not None:
            pilot_facts = _pilot_measurement_facts(
                pilot, pilot_path, plan=plan, problems=problems
            )

    priced = loaded.get("priced_budget", {})
    priced_path = paths.get("priced_budget")
    if priced and priced_path is not None:
        _validate_priced_budget(
            priced,
            priced_path,
            plan=plan,
            pilot=pilot,
            pilot_facts=pilot_facts,
            pilot_path=pilot_path,
            problems=problems,
        )

    # Exercise the exact configuration-construction route production will use,
    # including the generated M1 calibration space and the diagnosis block.
    # Merely parsing the raw base JSON is insufficient: the historical bug was
    # precisely that validation and execution took different routes.
    try:
        evaluator = ProductionEvaluator(plan)
        m1 = _model_spaces(plan)[nc.MODEL_M1]
        params = {
            name: float((lo + hi) / 2.0)
            for name, lo, hi in zip(m1.names, m1.space.lower, m1.space.upper)
        }
        params[str(plan.require("p_parameter.name"))] = 0.0
        unit = ne.WorkUnit(
            stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M1,
            design_id="readiness-route-probe", seed=int(plan.seeds.discovery[0]),
            vector_hash=nc.vector_hash(params, names=m1.names),
            kernel=nc.KERNEL_ANCHOR, parent_ids=("readiness-route-probe",),
            params=params, parent_manifest_sha256="", code_sha256="readiness",
            config_sha256="readiness", target_schema=plan.targets.frozen_order,
        )
        evaluator.config_for(unit, params)
    except Exception as exc:  # noqa: BLE001 - every route mismatch is a readiness failure
        problems.append(f"the production M1 config route failed: {type(exc).__name__}: {exc}")
    return problems

def command_validate(args: argparse.Namespace) -> int:
    """Dry-run validation.  Runs no simulation under any circumstances."""
    path = Path(args.plan)
    if not path.is_absolute():
        path = ROOT / path
    raw = nc.load_plan_json(path)
    pending = nc.unsigned_values(raw)
    sign_off = raw.get("sign_off") if isinstance(raw, Mapping) else None
    unsigned_sign_off = [
        field for field in ("signed_by", "signed_date", "spec_version")
        if not isinstance(sign_off, Mapping) or nc.is_unsigned(sign_off.get(field))
    ]

    print(f"run plan: {path}")
    print(f"plan sha256: {ccs.sha256_file(path)}")
    print()
    if not pending and unsigned_sign_off:
        print("FAIL CLOSED: all decision values are present, but the signature gate is incomplete.")
        print("No production simulation may start.\n")
        for field in unsigned_sign_off:
            print(f"  sign_off.{field}")
        return 2
    if not pending:
        plan = nc.FrozenPlan.load(path)
        print("All required decisions are signed.")
        print(f"  targets:           {plan.targets.n} ({plan.targets.hash()[:16]})")
        print(f"  discovery seeds:   K={plan.k_discovery}")
        print(f"  confirmation seeds: K={plan.k_confirmation}")
        print(f"  kernel quotas:     {plan.kernel_quotas()}")
        print()
        problems = _production_readiness_problems(plan)
        if problems:
            print("FAIL CLOSED: signed decisions are not production-ready:")
            for problem in problems:
                print(f"  - {problem}")
            return 3
        print("All production prerequisites are present, passed, and bound to this plan.")
        return 0

    print(f"FAIL CLOSED: {len(pending)} required value(s) are unsigned.")
    print("No production simulation may start.\n")
    if unsigned_sign_off:
        print("-- separate signature gate (not counted as scientific/numerical decisions) --")
        for field in unsigned_sign_off:
            print(f"  sign_off.{field}")
        print()
    without = [item for item in pending if item.proposal is None]
    with_proposal = [item for item in pending if item.proposal is not None]
    if with_proposal:
        print(f"-- {len(with_proposal)} with a proposal recorded in the specifications --")
        for item in with_proposal:
            print(f"  {item.path}")
            print(f"      proposal: {item.proposal!r}")
    if without:
        print(f"\n-- {len(without)} with NO proposal in either specification "
              "(these need a decision, not a transcription) --")
        for item in without:
            print(f"  {item.path}: {item.description}")
    return 2


# ==========================================================================
# Acceptance-input identity: one acceptance basis per lineage
# ==========================================================================

def _normalised_acceptance_package(validated: Mapping) -> dict:
    """Return the path-independent identity of a validated input package."""
    payload = validated["payload"]
    return {
        "schema": FROZEN_ACCEPTANCE_IDENTITY_SCHEMA,
        "plan_sha256": str(payload["plan_sha256"]),
        "approved_by": str(payload["approved_by"]),
        "approved_utc": str(payload["approved_utc"]),
        "provenance_sha256": str(validated["provenance_sha256"]),
        "observed_sha256": str(validated["observed_sha256"]),
        "base_variance_sha256": str(validated["base_variance_sha256"]),
        "component_table_sha256": str(validated["component_table_sha256"]),
        "acceptance_input_identity": nc.json_safe(
            validated["acceptance_input_identity"]
        ),
    }


def _validate_declared_acceptance_package(plan: nc.FrozenPlan) -> dict:
    """Validate the signed plan's approved FINAL package before preparation."""
    prerequisites = plan.get("production_prerequisites")
    if not isinstance(prerequisites, Mapping) or not prerequisites.get(
        "acceptance_inputs"
    ):
        raise ContractError(
            "the signed plan declares no production_prerequisites.acceptance_inputs; "
            "observed values and non-process variances must be reviewed before prepare"
        )
    return nai.validate_package(
        plan,
        prerequisites["acceptance_inputs"],
        root=ROOT,
        scientific_code_sha256=_code_sha256(),
        scientific_runtime=nig.scientific_runtime_fingerprint(),
    )


def _freeze_acceptance_package(out: Path, validated: Mapping) -> dict:
    """Copy exact reviewed inputs into a stage and write their normalised identity."""
    payload = validated["payload"]
    expected_outputs = {
        "observed_values": nai.OBSERVED_FILENAME,
        "base_variance": nai.BASE_VARIANCE_FILENAME,
        "component_table": nai.COMPONENT_FILENAME,
    }
    for key, filename in expected_outputs.items():
        declared = Path(str(payload["outputs"][key]["path"]))
        if declared != Path(filename):
            raise ContractError(
                f"approved acceptance package must use canonical output path {filename!r}; "
                f"found {str(declared)!r}"
            )
    sources = {
        nai.PROVENANCE_FILENAME: Path(validated["provenance_path"]),
        nai.OBSERVED_FILENAME: Path(validated["observed_path"]),
        nai.BASE_VARIANCE_FILENAME: Path(validated["base_variance_path"]),
        nai.COMPONENT_FILENAME: Path(validated["component_table_path"]),
    }
    for filename, source in sources.items():
        target = out / filename
        if target.exists():
            raise ContractError(
                f"refusing to replace existing frozen acceptance input: {target}"
            )
        shutil.copyfile(source, target)
    identity = _normalised_acceptance_package(validated)
    ne.atomic_write_json(out / FROZEN_ACCEPTANCE_IDENTITY_FILENAME, identity)
    return identity


def _recorded_acceptance_package_identity(manifest: Mapping) -> dict | None:
    extra = manifest.get("extra") or {}
    recorded = extra.get("acceptance_package_identity")
    return dict(recorded) if recorded else None


def _validate_frozen_acceptance_package(
    stage_dir: Path, plan: nc.FrozenPlan, *, what: str, manifest: Mapping | None = None
) -> dict:
    """Revalidate the sealed package and its normalised identity in a stage."""
    stage_dir = Path(stage_dir)
    validated = nai.validate_package(
        plan,
        stage_dir / nai.PROVENANCE_FILENAME,
        root=ROOT,
        scientific_code_sha256=_code_sha256(),
        scientific_runtime=nig.scientific_runtime_fingerprint(),
    )
    expected = _normalised_acceptance_package(validated)
    identity_path = stage_dir / FROZEN_ACCEPTANCE_IDENTITY_FILENAME
    try:
        frozen = _load_strict_json(identity_path)
    except (OSError, UnicodeDecodeError, ContractError) as exc:
        raise ContractError(
            f"{what}: frozen acceptance identity is missing or invalid: "
            f"{identity_path}: {exc}"
        ) from exc
    if nc.json_safe(frozen) != nc.json_safe(expected):
        raise ContractError(
            f"{what}: {identity_path.name} does not match the sealed acceptance files"
        )
    if manifest is None:
        manifest = _load_strict_json(stage_dir / "manifest.json")
    recorded = _recorded_acceptance_package_identity(manifest)
    if recorded is None:
        raise ContractError(
            f"{what}: stage manifest records no acceptance_package_identity"
        )
    if nc.json_safe(recorded) != nc.json_safe(expected):
        raise ContractError(
            f"{what}: manifest acceptance_package_identity does not match the sealed package"
        )
    recorded_acceptance = _recorded_acceptance_identity(manifest)
    if nc.json_safe(recorded_acceptance) != nc.json_safe(
        expected["acceptance_input_identity"]
    ):
        raise ContractError(
            f"{what}: manifest acceptance_input_identity does not match the sealed package"
        )
    return expected

#: Name of the file a child stage writes at ``prepare`` to freeze its parent's
#: complete acceptance-input identity.  It is written before the manifest, so
#: it is covered by the stage's own artifact index and cannot be edited later
#: without that index noticing.
PARENT_IDENTITY_FILENAME = "parent_acceptance_identity.json"


def _recorded_acceptance_identity(manifest: Mapping) -> dict | None:
    """The acceptance-input identity a stage sealed at prepare."""
    extra = manifest.get("extra") or {}
    recorded = extra.get("acceptance_input_identity")
    return dict(recorded) if recorded else None


def _freeze_parent_acceptance_identity(
    out: Path, parent_manifest: Mapping, *, parent_dir: Path,
    current_package_identity: Mapping,
) -> dict:
    """Freeze the parent's complete acceptance-input identity into the child.

    A child stage's verdicts are only comparable with its parent's if both were
    scored against the same observed values, the same frozen base variances,
    the same target register and order, the same discrepancy, the same scoring
    arithmetic and the same acceptance rule.  Recording the parent *manifest*
    hash does not establish that: the manifest hash changes for a hundred
    irrelevant reasons and did not cover the two acceptance-input files at all.

    So the identity is frozen here, once, at the moment the parent is
    validated, and re-checked at the child's own merge and before any
    classification or edge verdict.
    """
    identity = _recorded_acceptance_identity(parent_manifest)
    if not identity:
        raise ContractError(
            f"the parent stage at {parent_dir} records no acceptance-input identity, so "
            "there is nothing for this stage to freeze and no way to show the two share "
            "one acceptance basis. Re-prepare the parent under the current code."
        )
    package_identity = _recorded_acceptance_package_identity(parent_manifest)
    if not package_identity:
        raise ContractError(
            f"the parent stage at {parent_dir} records no acceptance_package_identity; "
            "its observed values, base variances, component budget and review provenance "
            "cannot be shown to match this child"
        )
    if nc.json_safe(package_identity) != nc.json_safe(current_package_identity):
        raise ContractError(
            f"the approved acceptance package for this child differs from the sealed "
            f"package used by its parent at {parent_dir}"
        )
    ne.atomic_write_json(out / PARENT_IDENTITY_FILENAME, {
        "frozen_from": str(parent_dir),
        "parent_stage": str(parent_manifest.get("stage") or ""),
        "parent_manifest_sha256": ccs.sha256_json(nc.json_safe(parent_manifest)),
        "acceptance_input_identity": identity,
        "acceptance_package_identity": package_identity,
        "meaning": (
            "the parent's complete acceptance-input and reviewed-package identities, "
            "frozen at prepare. This stage's merge, classification and edge verdicts "
            "must all match them exactly."
        ),
    })
    return identity


def _frozen_parent_acceptance_identity(stage_dir: Path) -> dict | None:
    """The parent identity this stage froze at prepare, if it has a parent."""
    path = Path(stage_dir) / PARENT_IDENTITY_FILENAME
    manifest_path = Path(stage_dir) / "manifest.json"
    manifest = _load_strict_json(manifest_path) if manifest_path.exists() else {}
    has_parent = bool(manifest.get("parent_manifest_sha256")) or (
        nc.STAGE_PARENT.get(str(manifest.get("stage") or ""))
        not in (None, nc.STAGE_PREPARE)
    )
    if not path.exists():
        if has_parent:
            raise ContractError(
                f"{path} is required for stage {manifest.get('stage')!r} but is missing; "
                "refusing to discard the frozen parent acceptance identity"
            )
        return None
    payload = _load_strict_json(path)
    identity = payload.get("acceptance_input_identity")
    if not identity:
        raise ContractError(
            f"{path} exists but records no acceptance-input identity; refusing to treat "
            "an empty freeze as agreement"
        )
    return dict(identity)


def _frozen_parent_acceptance_package_identity(stage_dir: Path) -> dict | None:
    """The complete reviewed package identity frozen from a parent, if present."""
    path = Path(stage_dir) / PARENT_IDENTITY_FILENAME
    if not path.exists():
        return None
    payload = _load_strict_json(path)
    identity = payload.get("acceptance_package_identity")
    if not identity:
        raise ContractError(
            f"{path} records no acceptance_package_identity; refusing a lineage whose "
            "component budget or review provenance could have changed"
        )
    return dict(identity)


def _assert_acceptance_basis(
    stage_dir: Path, manifest: Mapping, plan: nc.FrozenPlan, *, what: str
) -> dict:
    """Re-check a merged stage's acceptance basis before it is interpreted.

    Two comparisons, both fail-closed:

    * against the **current plan**, for everything the plan fixes (target
      order, register hash, discrepancy, scoring version, acceptance rule), so
      a plan edited between ``merge`` and ``classify`` cannot slip through;
    * against the **frozen parent identity**, for the complete identity
      including the observed-value and base-variance hashes, so a child cannot
      be classified on a different acceptance basis than its parent.

    A stage with no recorded identity is refused: it was merged before the
    acceptance inputs were hashed, and nothing can establish what it was
    scored against.
    """
    package_identity = _validate_frozen_acceptance_package(
        stage_dir, plan, what=what, manifest=manifest
    )
    recorded = _recorded_acceptance_identity(manifest)
    if not recorded:
        raise ContractError(
            f"{stage_dir} records no acceptance-input identity, so what its scores mean "
            "cannot be established. Re-prepare the stage with an approved acceptance "
            f"package before {what}."
        )
    nc.assert_acceptance_identity(
        plan.plan_acceptance_fields(), recorded,
        what=f"{what}: this stage against the run plan it is being read with",
        fields=nc.PLAN_DERIVED_ACCEPTANCE_FIELDS,
    )
    frozen_parent = _frozen_parent_acceptance_identity(stage_dir)
    if frozen_parent is not None:
        nc.assert_acceptance_identity(
            frozen_parent, recorded,
            what=f"{what}: this stage against its frozen parent identity",
        )
    frozen_parent_package = _frozen_parent_acceptance_package_identity(stage_dir)
    if frozen_parent_package is not None and nc.json_safe(
        frozen_parent_package
    ) != nc.json_safe(package_identity):
        raise ContractError(
            f"{what}: this stage's reviewed acceptance package differs from its parent"
        )
    return recorded


def _validate_stage_artifacts(
    stage_dir: Path, *, what: str, validate_all_indexed: bool = False,
    allow_partial_runtime_growth: bool = False,
) -> None:
    """Verify the sealed index before any stage evidence is consumed."""
    immutable = {
        "ledger_units.csv", "ledger_meta.json", "design_proposals.csv",
        "design_vectors.csv", "anchors.csv", "kernel_outcomes.csv",
        nai.PROVENANCE_FILENAME, nai.OBSERVED_FILENAME,
        nai.BASE_VARIANCE_FILENAME, nai.COMPONENT_FILENAME,
        FROZEN_ACCEPTANCE_IDENTITY_FILENAME,
    }
    manifest = _load_strict_json(Path(stage_dir) / "manifest.json")
    if str(manifest.get("model") or "") == nc.MODEL_M1:
        immutable.add(nig.GATE_FILENAME)
    if (
        str(manifest.get("model") or "") == nc.MODEL_M1
        and str(manifest.get("stage") or "") == nc.STAGE_DISCOVERY
    ):
        immutable.update({
            M1_ANCHOR_EVIDENCE_FILENAME,
            M1_INDEPENDENT_ANCHORS_FILENAME,
        })
    if bool(manifest.get("parent_manifest_sha256")) or \
       nc.STAGE_PARENT.get(str(manifest.get("stage") or "")) not in (None, nc.STAGE_PREPARE):
        immutable.add(PARENT_IDENTITY_FILENAME)
    required_paths = None if validate_all_indexed else sorted(immutable)
    if allow_partial_runtime_growth:
        # A PARTIAL merge is a durable checkpoint, not a terminal freeze.
        # Scientific tables already written by that checkpoint remain sealed,
        # while journals, durable results, shard snapshots and caches are
        # expected to grow during a legitimate resume.  Validate every prior
        # indexed file except that explicitly mutable runtime surface.
        sealed = ne.validate_artifact_index(stage_dir, required_paths=[])
        required_paths = sorted(
            path for path in sealed["entries"]
            if not ne.is_mutable_stage_runtime_path(path)
        )
    try:
        ne.validate_artifact_index(
            stage_dir,
            required_paths=required_paths,
            require_exact=False,
        )
    except ContractError as exc:
        raise ContractError(f"{what}: stage artifact validation failed: {exc}") from exc

    if (
        str(manifest.get("model") or "") == nc.MODEL_M1
        and str(manifest.get("stage") or "") == nc.STAGE_DISCOVERY
    ):
        evidence_path = Path(stage_dir) / M1_ANCHOR_EVIDENCE_FILENAME
        evidence = _load_strict_json(evidence_path)
        seed_result_paths: list[str] = []
        for row in evidence.get("candidates", ()):
            candidate_id = str(row.get("candidate_id", "<unknown>"))
            resolved = _portable_evidence_file(
                evidence_path, row.get("seed_result_artifact_path")
            )
            if resolved is None or not resolved.is_file():
                raise ContractError(
                    f"{what}: {candidate_id} has no portable packaged seed-result artifact"
                )
            if ccs.sha256_file(resolved) != str(
                row.get("seed_result_artifact_sha256", "")
            ):
                raise ContractError(
                    f"{what}: {candidate_id} seed-result artifact hash does not match"
                )
            seed_result_paths.append(str(resolved.relative_to(Path(stage_dir).resolve())))
        try:
            ne.validate_artifact_index(
                stage_dir,
                required_paths=seed_result_paths,
                require_exact=False,
            )
        except ContractError as exc:
            raise ContractError(
                f"{what}: packaged M1 seed-result validation failed: {exc}"
            ) from exc


def _validate_frozen_m1_prerequisites(
    stage_dir: Path,
    *,
    plan: nc.FrozenPlan,
    manifest: Mapping,
    acceptance_package_identity: Mapping,
    what: str,
) -> None:
    """Re-run the scientific M1 certificate validators before dispatch."""
    if str(manifest.get("model") or "") != nc.MODEL_M1:
        return
    model = _model_spaces(plan)[nc.MODEL_M1]
    extra = manifest.get("extra") or {}
    gate_path = Path(stage_dir) / nig.GATE_FILENAME
    gate = nig.assert_gate_satisfied(
        gate_path,
        root=ROOT,
        expected_base_config=str(plan.require("models.base_config")),
        expected_plan_sha256=plan.plan_sha256,
        expected_scientific_config_sha256=_scientific_config_sha256(plan, model),
    )
    gate_sha = ccs.sha256_file(gate_path)
    if str(extra.get("identity_gate_sha256", "")) != gate_sha:
        raise ContractError(
            f"{what}: manifest identity_gate_sha256 does not match the sealed gate"
        )
    if str(extra.get("identity_gate_source_sha256", "")) != gate_sha:
        raise ContractError(
            f"{what}: sealed identity gate differs from its signed source identity"
        )
    if extra.get("identity_gate_schema") != gate.get("schema"):
        raise ContractError(
            f"{what}: manifest identity_gate_schema does not match the sealed gate"
        )

    if str(manifest.get("stage") or "") != nc.STAGE_DISCOVERY:
        return
    anchors_path = Path(stage_dir) / M1_INDEPENDENT_ANCHORS_FILENAME
    anchors = _load_anchors(anchors_path, model)
    evidence_path = Path(stage_dir) / M1_ANCHOR_EVIDENCE_FILENAME
    evidence = _validate_m1_anchor_evidence(
        evidence_path,
        anchors_path=anchors_path,
        anchors=anchors,
        plan=plan,
        model=model,
        acceptance_identity=acceptance_package_identity[
            "acceptance_input_identity"
        ],
        observed=_load_strict_json(Path(stage_dir) / nai.OBSERVED_FILENAME),
        base_variance=_load_strict_json(
            Path(stage_dir) / nai.BASE_VARIANCE_FILENAME
        ),
    )
    if str(extra.get("independently_confirmed_m1_evidence_sha256", "")) != \
            ccs.sha256_file(evidence_path):
        raise ContractError(
            f"{what}: manifest M1 confirmation SHA-256 does not match sealed evidence"
        )
    if extra.get("independently_confirmed_m1_evidence_schema") != evidence.get(
        "schema"
    ):
        raise ContractError(
            f"{what}: manifest M1 confirmation schema does not match sealed evidence"
        )


# ==========================================================================
# prepare
# ==========================================================================

def _load_anchors(path: Path, model: nc.ModelSpace) -> list[nd.Anchor]:
    """Load confirmed anchors as complete joint vectors.

    A missing coordinate is fatal.  Vectors are never reconstructed from
    marginals, defaults or filenames.
    """
    frame = ne.read_frame_exact(path)
    if "candidate_id" not in frame.columns:
        raise ContractError(f"{path}: anchor table needs a 'candidate_id' column")
    missing = [name for name in model.names if name not in frame.columns]
    if missing:
        raise ContractError(
            f"{path}: anchor table is missing parameter column(s) {missing}. "
            "Anchors must be complete joint vectors."
        )
    ids = frame["candidate_id"].astype(str)
    if ids.duplicated().any():
        raise ContractError(f"{path}: duplicate candidate_id {sorted(ids[ids.duplicated()])}")
    return [
        nd.Anchor.build(
            str(row["candidate_id"]),
            {name: float(row[name]) for name in model.names},
            model,
        )
        for _, row in frame.iterrows()
    ]


def _validate_m1_anchor_evidence(
    path: Path,
    *,
    anchors_path: Path,
    anchors: Sequence[nd.Anchor],
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    acceptance_identity: Mapping,
    observed: Mapping[str, float],
    base_variance: Mapping[str, float],
) -> dict:
    """Prove that caller-supplied positive-p M1 anchors were confirmed.

    A CSV is a vector transport, not confirmation evidence.  This typed
    artifact binds the verdict to the exact plan, production configuration,
    scientific code/runtime, target contract, reserved confirmation seeds and
    every candidate/vector.  Candidate-level aggregates are checked against
    a typed seed-result artifact and a fresh score recomputation using the
    exact acceptance basis being sealed into this stage.  Neither a generic
    ``passed=true`` nor a self-declared ``nominal_i_max`` is evidence.
    """
    if not path.exists():
        raise ContractError(f"M1 anchor-confirmation evidence does not exist: {path}")
    try:
        payload = _load_strict_json(path)
    except (OSError, ContractError) as exc:
        raise ContractError(f"{path}: invalid M1 anchor-confirmation JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ContractError(f"{path}: M1 anchor-confirmation evidence must be a JSON object")

    problems: list[str] = []
    base_config = _evidence_path(plan.require("models.base_config"))
    expected_scalars = {
        "schema": M1_ANCHOR_EVIDENCE_SCHEMA,
        "status": "FINAL",
        "model": nc.MODEL_M1,
        "plan_sha256": plan.plan_sha256,
        "anchors_sha256": ccs.sha256_file(anchors_path),
        "scientific_code_sha256": _code_sha256(),
        "scientific_config_sha256": _scientific_config_sha256(plan, model),
        "base_config_sha256": (
            ccs.sha256_file(base_config) if base_config is not None and base_config.is_file() else ""
        ),
        "target_register_sha256": plan.targets.register_sha256,
        "p_parameter": str(plan.require("p_parameter.name")),
    }
    for field, expected in expected_scalars.items():
        if payload.get(field) != expected:
            problems.append(f"{field} does not match the frozen {field}")
    recorded_anchors = _referenced_evidence_file(path, payload.get("anchors_path"))
    if recorded_anchors is None or recorded_anchors.resolve() != anchors_path.resolve():
        problems.append("anchors_path does not match the supplied anchor table")
    if payload.get("passed") is not True:
        problems.append("passed is not the Boolean true")
    if payload.get("production_eligible") is not True:
        problems.append("production_eligible is not the Boolean true")
    if payload.get("scientific_runtime") != nig.scientific_runtime_fingerprint():
        problems.append("scientific_runtime does not match the current scientific runtime")
    if not _valid_utc_timestamp(payload.get("generated_utc")):
        problems.append("generated_utc is missing or is not an ISO-8601 UTC timestamp")
    if not str(payload.get("command", "")).strip():
        problems.append("command is missing")
    if tuple(payload.get("target_order") or ()) != tuple(plan.targets.frozen_order):
        problems.append("target_order does not match the frozen target contract")
    try:
        recorded_seeds = tuple(
            nc.require_rng_seed(seed, what=f"confirmation_seeds[{index}]")
            for index, seed in enumerate(payload.get("confirmation_seeds", ()))
        )
    except (TypeError, ContractError):
        recorded_seeds = ()
    if recorded_seeds != plan.seeds.confirmation:
        problems.append("confirmation_seeds are not the exact reserved confirmation block")
    if nc.json_safe(payload.get("acceptance_input_identity")) != nc.json_safe(
        acceptance_identity
    ):
        problems.append(
            "acceptance_input_identity does not match the acceptance basis sealed for this stage"
        )

    target_order = tuple(plan.targets.frozen_order)
    if tuple(observed) != target_order:
        problems.append("observed values are not in the frozen target order")
    if tuple(base_variance) != target_order:
        problems.append("base variance is not in the frozen target order")
    try:
        if any(not np.isfinite(float(observed[name])) for name in target_order):
            problems.append("observed values contain a non-finite value")
        if any(
            not np.isfinite(float(base_variance[name]))
            or float(base_variance[name]) <= 0.0
            for name in target_order
        ):
            problems.append("base variance contains a non-finite or non-positive value")
    except (KeyError, TypeError, ValueError):
        problems.append("acceptance inputs are incomplete or malformed")

    rows = payload.get("candidates")
    if not isinstance(rows, list) or not rows:
        problems.append("candidates must be a non-empty list of candidate-level evidence")
        rows = []
    by_id: dict[str, Mapping] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            problems.append(f"candidates[{index}] is not an object")
            continue
        candidate_id = str(row.get("candidate_id", ""))
        if not candidate_id:
            problems.append(f"candidates[{index}] has no candidate_id")
            continue
        if candidate_id in by_id:
            problems.append(f"candidate evidence duplicates {candidate_id!r}")
            continue
        by_id[candidate_id] = row

    p_name = str(plan.require("p_parameter.name"))
    expected_by_id = {anchor.anchor_id: anchor for anchor in anchors}
    if set(by_id) != set(expected_by_id):
        problems.append(
            "candidate evidence ids do not exactly match the supplied anchor table "
            f"(missing={sorted(set(expected_by_id) - set(by_id))}, "
            f"extra={sorted(set(by_id) - set(expected_by_id))})"
        )
    for candidate_id in sorted(set(by_id) & set(expected_by_id)):
        row = by_id[candidate_id]
        anchor = expected_by_id[candidate_id]
        if str(row.get("vector_hash", "")) != anchor.vector_hash:
            problems.append(f"{candidate_id}: vector_hash does not match the anchor")
        try:
            if not _is_finite_json_number(row.get("p_value")):
                raise ValueError("p_value is not a finite JSON number")
            if not _is_finite_json_number(row.get("nominal_i_max")):
                raise ValueError("nominal_i_max is not a finite JSON number")
            p_value = float(row.get("p_value"))
            n_replicates = nc.require_native_integer(
                row.get("n_replicates"),
                what=f"{candidate_id}.n_replicates",
                minimum=1,
            )
            declared_i_max = float(row.get("nominal_i_max"))
        except (TypeError, ValueError, ContractError):
            problems.append(f"{candidate_id}: malformed p/replicate/I_max evidence")
            continue
        if not np.isfinite(p_value) or p_value != float(anchor.params[p_name]) or p_value <= 0.0:
            problems.append(f"{candidate_id}: p_value is not the anchor's exact positive p")
        if n_replicates != plan.k_confirmation:
            problems.append(
                f"{candidate_id}: n_replicates={n_replicates}, expected "
                f"K_confirmation={plan.k_confirmation}"
            )
        per_seed_hashes = row.get("per_seed_joint_vector_hashes")
        if not isinstance(per_seed_hashes, list) \
                or len(per_seed_hashes) != len(plan.seeds.confirmation) \
                or len(per_seed_hashes) != n_replicates:
            problems.append(
                f"{candidate_id}: per_seed_joint_vector_hashes must contain exactly "
                "one hash for each reserved confirmation seed"
            )
        elif any(str(value) != anchor.vector_hash for value in per_seed_hashes):
            problems.append(
                f"{candidate_id}: per_seed_joint_vector_hashes do not preserve the "
                "complete anchor vector on every seed"
            )
        seed_result_path = _portable_evidence_file(
            path, row.get("seed_result_artifact_path")
        )
        if seed_result_path is None:
            problems.append(
                f"{candidate_id}: seed_result_artifact_path must be a portable path "
                "inside the evidence package"
            )
        elif not seed_result_path.is_file():
            problems.append(f"{candidate_id}: seed-result artifact does not exist")
        elif str(row.get("seed_result_artifact_sha256", "")) != ccs.sha256_file(
            seed_result_path
        ):
            problems.append(f"{candidate_id}: seed-result artifact SHA-256 does not match")
        else:
            try:
                recomputed = _recompute_m1_anchor_seed_result(
                    seed_result_path,
                    candidate_id=candidate_id,
                    anchor=anchor,
                    plan=plan,
                    model=model,
                    acceptance_identity=acceptance_identity,
                    observed=observed,
                    base_variance=base_variance,
                )
            except ContractError as exc:
                problems.append(f"{candidate_id}: {exc}")
            else:
                if not np.isfinite(declared_i_max) or not np.isclose(
                    declared_i_max,
                    recomputed["nominal_i_max"],
                    rtol=1e-12,
                    atol=1e-12,
                ):
                    problems.append(
                        f"{candidate_id}: declared nominal_i_max={declared_i_max!r} "
                        f"does not match recomputed {recomputed['nominal_i_max']!r}"
                    )
                if row.get("nominal_pass") is not recomputed["nominal_pass"]:
                    problems.append(
                        f"{candidate_id}: nominal_pass does not match the recomputed verdict"
                    )
                if recomputed["nominal_pass"] is not True:
                    problems.append(
                        f"{candidate_id}: recomputed nominal_i_max="
                        f"{recomputed['nominal_i_max']!r} exceeds the primary cutoff "
                        f"{plan.cutoff}"
                    )

    if problems:
        raise ContractError(
            f"{path}: M1 anchor-confirmation evidence is invalid:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )
    return dict(payload)


def _recompute_m1_anchor_seed_result(
    path: Path,
    *,
    candidate_id: str,
    anchor: nd.Anchor,
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    acceptance_identity: Mapping,
    observed: Mapping[str, float],
    base_variance: Mapping[str, float],
) -> dict:
    """Parse one typed seed artifact and recompute its nominal verdict."""
    try:
        payload = _load_strict_json(path)
    except (OSError, UnicodeDecodeError, ContractError) as exc:
        raise ContractError(f"seed-result artifact is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ContractError("seed-result artifact must be a JSON object")

    problems: list[str] = []
    expected_scalars = {
        "schema": M1_SEED_RESULT_SCHEMA,
        "model": nc.MODEL_M1,
        "candidate_id": candidate_id,
        "vector_hash": anchor.vector_hash,
    }
    for field, expected in expected_scalars.items():
        if payload.get(field) != expected:
            problems.append(f"seed-result {field} must equal {expected!r}")
    if nc.json_safe(payload.get("acceptance_input_identity")) != nc.json_safe(
        acceptance_identity
    ):
        problems.append("seed-result acceptance_input_identity does not match")
    if tuple(payload.get("parameter_order") or ()) != tuple(model.names):
        problems.append("seed-result parameter_order is not the complete M1 order")
    if tuple(payload.get("target_order") or ()) != tuple(plan.targets.frozen_order):
        problems.append("seed-result target_order does not match the frozen target order")
    if tuple(payload.get("confirmation_seeds") or ()) != plan.seeds.confirmation:
        problems.append("seed-result confirmation_seeds are not the reserved block")
    if payload.get("n_replicates") != len(plan.seeds.confirmation):
        problems.append("seed-result n_replicates does not match the reserved block")

    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping) or set(parameters) != set(model.names):
        problems.append("seed-result parameters are not one complete M1 vector")
    else:
        try:
            vector = {name: float(parameters[name]) for name in model.names}
        except (TypeError, ValueError):
            problems.append("seed-result parameters contain a malformed value")
        else:
            if any(
                not _is_finite_json_number(parameters[name]) for name in model.names
            ):
                problems.append(
                    "seed-result parameters must be finite native JSON numbers"
                )
            elif nc.vector_hash(vector, names=model.names) != anchor.vector_hash:
                problems.append("seed-result complete parameter vector does not match the anchor")

    records = payload.get("seed_results")
    if not isinstance(records, list) or len(records) != len(plan.seeds.confirmation):
        problems.append("seed_results must contain exactly one record per reserved seed")
        records = []
    seed_rows: list[dict] = []
    actual_seeds: list[int] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            problems.append(f"seed_results[{index}] is not an object")
            continue
        seed = record.get("seed")
        if type(seed) is not int:
            problems.append(f"seed_results[{index}].seed is not a native JSON integer")
            continue
        actual_seeds.append(seed)
        if record.get("candidate_id") != candidate_id:
            problems.append(f"seed {seed}: candidate_id does not match")
        if record.get("vector_hash") != anchor.vector_hash:
            problems.append(f"seed {seed}: vector_hash does not match the complete anchor")
        outputs = record.get("outputs")
        if not isinstance(outputs, Mapping) or set(outputs) != set(plan.targets.frozen_order):
            problems.append(f"seed {seed}: outputs are not the complete frozen target set")
            continue
        if any(
            not _is_finite_json_number(outputs[name])
            for name in plan.targets.frozen_order
        ):
            problems.append(
                f"seed {seed}: outputs must be finite native JSON numbers"
            )
            continue
        values = {
            name: float(outputs[name]) for name in plan.targets.frozen_order
        }
        seed_rows.append({
            "design_id": candidate_id,
            "seed": seed,
            "vector_hash": anchor.vector_hash,
            **{f"target:{name}": values[name] for name in plan.targets.frozen_order},
        })

    if len(actual_seeds) != len(set(actual_seeds)):
        problems.append("seed_results contain duplicate seeds")
    if set(actual_seeds) != set(plan.seeds.confirmation):
        problems.append("seed_results do not contain the exact reserved confirmation seeds")
    if problems:
        raise ContractError(
            f"{path}: invalid typed seed-result evidence:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )

    scores = ne.recompute_candidate_scores(
        pd.DataFrame(seed_rows),
        targets=plan.targets,
        observed={name: float(observed[name]) for name in plan.targets.frozen_order},
        base_variance={
            name: float(base_variance[name]) for name in plan.targets.frozen_order
        },
        cutoff=plan.cutoff,
    )
    if len(scores) != 1 or str(scores.iloc[0]["design_id"]) != candidate_id:
        raise ContractError("seed-result recomputation did not produce exactly this candidate")
    score = scores.iloc[0]
    return {
        "nominal_i_max": float(score["i_max"]),
        "nominal_pass": bool(score["nominal_pass"]),
    }


def _package_m1_anchor_evidence(
    payload: Mapping,
    *,
    source_path: Path,
    anchors_source: Path,
    stage_dir: Path,
) -> dict:
    """Copy the exact independent anchors and seed evidence into the stage."""
    packaged = dict(payload)
    anchors_destination = stage_dir / M1_INDEPENDENT_ANCHORS_FILENAME
    if anchors_destination.exists():
        raise ContractError(
            f"refusing to replace existing frozen M1 anchors: {anchors_destination}"
        )
    shutil.copyfile(anchors_source, anchors_destination)
    packaged["anchors_path"] = M1_INDEPENDENT_ANCHORS_FILENAME
    packaged["anchors_sha256"] = ccs.sha256_file(anchors_destination)
    packaged_rows: list[dict] = []
    destination_dir = stage_dir / "m1_anchor_seed_results"
    destination_dir.mkdir(parents=True, exist_ok=False)
    for index, original in enumerate(payload.get("candidates", ())):
        row = dict(original)
        source = _portable_evidence_file(
            source_path, row.get("seed_result_artifact_path")
        )
        if source is None or not source.is_file():
            raise ContractError(
                f"{row.get('candidate_id', '<unknown>')}: validated seed-result "
                "artifact disappeared before packaging"
            )
        suffix = source.suffix if source.suffix else ".bin"
        relative = Path("m1_anchor_seed_results") / (
            f"{index:04d}_{ccs.sha256_file(source)[:16]}{suffix}"
        )
        destination = stage_dir / relative
        shutil.copyfile(source, destination)
        row["seed_result_artifact_path"] = relative.as_posix()
        packaged_rows.append(row)
    packaged["candidates"] = packaged_rows
    return packaged


def _selected_parent_design(
    parent_dir: Path, model: nc.ModelSpace, *, child_stage: str
) -> tuple[list[nd.Anchor], list[nd.Proposal], list[nd.KernelOutcome]]:
    """Carry exactly the parent's independently recomputed promoted set.

    Promotion and confirmation are allocation stages.  They do not accept a
    caller-selected CSV and do not generate a fresh neighbourhood.
    """
    scores = ne.read_frame_exact(parent_dir / "candidate_scores.csv")
    if "promoted" not in scores.columns:
        raise ContractError(f"{parent_dir}: parent scores do not contain 'promoted'")
    selected_ids = set(
        scores.loc[scores["promoted"].astype(bool), "design_id"].astype(str)
    )
    if not selected_ids:
        raise ContractError("the parent promoted no candidates; there is no child work to allocate")
    vectors = ne.read_frame_exact(parent_dir / "design_vectors.csv").set_index("design_id")
    rows = ne.read_frame_exact(parent_dir / "design_proposals.csv")
    by_id = {str(row["design_id"]): row for _, row in rows.iterrows()}
    missing = sorted(selected_ids - set(by_id))
    if missing:
        raise ContractError(f"parent promoted unknown design ids: {missing[:5]}")
    anchors = _load_anchors(parent_dir / "anchors.csv", model)
    proposals: list[nd.Proposal] = []
    for design_id in sorted(selected_ids):
        row = by_id[design_id]
        params = {name: float(vectors.loc[design_id, name]) for name in model.names}
        expected_hash = nc.vector_hash(params, names=model.names)
        if expected_hash != str(row["vector_hash"]):
            raise ContractError(f"{design_id}: parent vector/hash mismatch during handoff")
        proposals.append(nd.Proposal(
            design_id=design_id,
            stage=child_stage,
            model=model.model,
            kernel=str(row["kernel_id"]),
            kernel_version=str(row.get("kernel_version", "carried/1")),
            scale=float(row.get("scale", 0.0)),
            direction_class=str(row.get("direction_class", "carried")),
            parent_ids=tuple(str(row["parent_ids"]).split("|")),
            params=params,
            vector_hash=expected_hash,
            pre_constraint_vector_hash=str(row.get("pre_constraint_vector_hash", expected_hash)),
            distance_to_parent=float(row.get("distance_to_parent", 0.0)),
            rng_state_hash=str(row.get("rng_state_hash", "")),
            attempt_index=int(row.get("attempt_index", 0)),
            notes={"carried_from": str(parent_dir), "allocation_only": True},
        ))
    outcomes_path = parent_dir / "kernel_outcomes.csv"
    outcomes: list[nd.KernelOutcome] = []
    # The original outcomes are copied separately below; they must not be
    # reconstructed as if the selected subset were a newly generated design.
    return anchors, proposals, outcomes


def _load_archive(
    path: str | None, model: nc.ModelSpace
) -> tuple[list[dict[str, float]], list[str], list[float] | None]:
    """Load the declared archive pool of low-J vectors, or nothing.

    Archive vectors are complete joint vectors, exactly like anchors: a missing
    coordinate is fatal rather than filled in.  The ``j`` column is optional
    here and required by :func:`nroy_design.archive_directions` only when the
    signed neighbour rule orders by J, so a plan that signs the low-J rule
    without supplying J fails closed at design time.
    """
    if not path:
        return [], [], None
    frame = ne.read_frame_exact(Path(path))
    if "candidate_id" not in frame.columns:
        raise ContractError(f"{path}: archive table needs a 'candidate_id' column")
    missing = [name for name in model.names if name not in frame.columns]
    if missing:
        raise ContractError(
            f"{path}: archive table is missing parameter column(s) {missing}. Archive "
            "vectors must be complete joint vectors."
        )
    vectors = [
        {name: float(row[name]) for name in model.names} for _, row in frame.iterrows()
    ]
    ids = [str(value) for value in frame["candidate_id"]]
    j_values = (
        [float(value) for value in frame["j"]] if "j" in frame.columns else None
    )
    return vectors, ids, j_values


def _model_spaces(plan: nc.FrozenPlan) -> dict[str, nc.ModelSpace]:
    m0_path = Path(str(plan.require("models.m0.calib_space")))
    if not m0_path.is_absolute():
        m0_path = ROOT / m0_path
    m0_raw = _load_strict_json(m0_path)
    spaces = {nc.MODEL_M0: nc.load_model_space(m0_raw, model=nc.MODEL_M0)}
    p_name = str(plan.require("p_parameter.name"))
    p_min = float(plan.require("p_parameter.min"))
    p_max = float(plan.require("p_parameter.max"))
    m1_declared = plan.get("models.m1.calib_space")
    if nc.is_unsigned(m1_declared):
        m1_raw = nc.build_m1_calib_space(
            m0_raw,
            p_name=p_name,
            p_config_path=str(plan.require("p_parameter.config_path")),
            p_min=p_min,
            p_max=p_max,
        )
    else:
        m1_path = Path(str(m1_declared))
        if not m1_path.is_absolute():
            m1_path = ROOT / m1_path
        m1_raw = _load_strict_json(m1_path)
    spaces[nc.MODEL_M1] = nc.load_model_space(
        m1_raw, model=nc.MODEL_M1, p_name=p_name, p_min=p_min, p_max=p_max
    )
    return spaces


def build_designs(
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    anchors: Sequence[nd.Anchor],
    *,
    stage: str = nc.STAGE_DISCOVERY,
    archive: Sequence[Mapping[str, float]] = (),
    archive_ids: Sequence[str] = (),
    archive_j: Sequence[float] | None = None,
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> tuple[list[nd.Proposal], list[nd.KernelOutcome]]:
    """Generate every frozen probe for every anchor, then enforce the quotas.

    Quotas are checked here, *before* dispatch.  A kernel that cannot fill its
    quota raises :class:`nroy_design.KernelExhaustedError` and preparation
    fails; it never borrows another kernel's budget.
    """
    validator = nd.Validator(
        space=model.space,
        model=model,
        injection_check=injection_check,
    )
    seed = int(plan.seeds.proposal_rng)
    scales = plan.ambient_scales()
    quota_per_scale = int(plan.require("ambient.quota_per_scale"))
    generator = str(plan.require("ambient.direction_generator"))
    semantics = str(plan.require("ambient.scale_semantics"))
    min_anchor = float(plan.require("separation.min_anchor_neighbour"))
    min_neighbour = float(plan.require("separation.min_neighbour_neighbour"))
    max_attempts = int(plan.require("ambient.max_attempts_per_anchor"))

    # The declared correlation-following sources. `assert_cross_block_declared`
    # refuses a plan whose cross-block quota and declared pool disagree, so the
    # source pool cannot be a decorative list alongside a quota that ignores it.
    source_pool = [str(k) for k in plan.require("archive_following.source_pool")]
    run_cross_block = nd.assert_cross_block_declared(
        source_pool, int(plan.require("cross_block.quota"))
    )
    archive_rule = str(plan.require("archive_following.neighbour_rule"))
    archive_neighbours = int(plan.require("archive_following.neighbour_count"))

    proposals: list[nd.Proposal] = []
    outcomes: list[nd.KernelOutcome] = []

    for anchor in anchors:
        proposals.append(nd.Proposal(
            design_id=f"{stage}/{model.model}/{nc.KERNEL_ANCHOR}/{anchor.anchor_id}/0000",
            stage=stage, model=model.model, kernel=nc.KERNEL_ANCHOR,
            kernel_version="anchor_control/1.0.0", scale=0.0,
            direction_class="identity", parent_ids=(anchor.anchor_id,),
            params=dict(anchor.params), vector_hash=anchor.vector_hash,
            pre_constraint_vector_hash=anchor.vector_hash, distance_to_parent=0.0,
            rng_state_hash=ccs.sha256_json({"anchor": anchor.anchor_id, "seed": seed}),
            attempt_index=0, notes={"purpose": "independent endpoint confirmation"},
        ))
        offset = 0
        for scale in scales:
            outcome = nd.ambient_probes(
                anchor, model, validator, stage=stage, scale=scale,
                quota=quota_per_scale, generator=generator, scale_semantics=semantics,
                seed=seed, min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
                max_attempts=max_attempts, index_offset=offset,
            )
            outcomes.append(outcome)
            proposals.extend(outcome.accepted)
            offset += 1000

        directions = nd.correlation_following_directions(
            anchor, model,
            source_pool=source_pool,
            archive=archive, archive_ids=archive_ids, archive_j=archive_j,
            confirmed=[a.params for a in anchors],
            confirmed_ids=[a.anchor_id for a in anchors],
            neighbour_count=archive_neighbours,
            max_directions=int(plan.require("archive_following.max_directions")),
            neighbour_rule=archive_rule,
            confirmed_difference_rule=str(plan.require(
                "archive_following.confirmed_difference_rule"
            )),
        )
        archive_outcome = nd.directed_probes(
            anchor, model, validator, stage=stage, kernel=nc.KERNEL_ARCHIVE_FOLLOWING,
            directions=directions,
            scales=[float(s) for s in plan.require("archive_following.scales")],
            quota=int(plan.require("archive_following.quota")), seed=seed,
            min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
            max_attempts=max_attempts, index_offset=offset,
        )
        outcomes.append(archive_outcome)
        proposals.extend(archive_outcome.accepted)
        offset += 1000

        if run_cross_block:
            cross_scales = [float(s) for s in plan.require("cross_block.scales")]
            cross_quota = int(plan.require("cross_block.quota"))
            cross_direction_count = nd.direction_count_for_complete_quota(
                quota=cross_quota,
                scales=cross_scales,
                kernel=nc.KERNEL_CROSS_BLOCK,
            )
            cross = nd.cross_block_directions(
                anchor, model, seed=seed,
                count=cross_direction_count,
            )
            cross_outcome = nd.directed_probes(
                anchor, model, validator, stage=stage, kernel=nc.KERNEL_CROSS_BLOCK,
                directions=cross,
                scales=cross_scales, quota=cross_quota, seed=seed,
                min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
                max_attempts=max_attempts, index_offset=offset,
            )
            outcomes.append(cross_outcome)
            proposals.extend(cross_outcome.accepted)
        offset += 1000

        if model.p_name is not None:
            boundary = nd.p_boundary_probes(
                anchor, model, validator, stage=stage,
                p_bins=[float(b) for b in plan.require("p_parameter.bins")],
                extra_values=[
                    float(v) for v in (plan.get("p_parameter.extra_probe_values") or ())
                ],
                seed=seed, index_offset=offset,
            )
            outcomes.append(boundary)
            proposals.extend(boundary.accepted)

    quotas = {
        kernel: quota * len(anchors) for kernel, quota in plan.kernel_quotas().items()
    }
    nd.assert_quotas(outcomes, quotas, what=f"{stage}/{model.model} preparation")
    return proposals, outcomes


def build_bridge_designs(
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    anchors: Sequence[nd.Anchor],
    nodes: pd.DataFrame,
    *,
    stage: str = nc.STAGE_BRIDGES,
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> tuple[list[nd.Proposal], list[nd.KernelOutcome], list[ng.CandidateEdge],
           list[ng.CandidateEdge]]:
    """Freeze the candidate-edge graph and its bridge probes (spec sec. 4).

    Every graph decision is read from the signed plan: the complete-pairwise
    candidate-edge rule, the tie-breaking rule, the minimal-edge algorithm and
    the interpolation fractions.  None of them has a default here.

    Only the **minimal** edge set receives probes.  That is the smallest set of
    bridges whose evaluation can establish the component structure, so the
    simulation budget goes to edges that can actually change the answer; the
    full proposal set is still written for audit.
    """
    geometry = nas.node_geometry(nodes)
    edges = ng.candidate_edges(
        geometry,
        proposal_rule=str(plan.require("graph.edge_proposal_rule")),
        tie_breaking=str(plan.require("graph.tie_breaking")),
    )
    minimal = ng.minimal_edge_set(
        edges,
        algorithm=str(plan.require("graph.minimal_edge_algorithm")),
        node_ids=sorted(geometry),
    )
    expected_minimal_edges = max(0, len(geometry) - 1)
    if len(minimal) != expected_minimal_edges:
        raise ContractError(
            "the complete-pairwise Kruskal graph must yield exactly n-1 bridge edges; "
            f"got {len(minimal)} for {len(geometry)} nodes"
        )
    fractions = plan.bridge_fractions()
    validator = nd.Validator(space=model.space, model=model, injection_check=injection_check)
    by_id = {anchor.anchor_id: anchor for anchor in anchors}
    seed = int(plan.seeds.proposal_rng)

    proposals: list[nd.Proposal] = []
    outcomes: list[nd.KernelOutcome] = []
    for index, edge in enumerate(sorted(minimal, key=lambda e: e.edge_id)):
        for endpoint in (edge.left, edge.right):
            if endpoint not in by_id:
                raise ContractError(
                    f"edge {edge.edge_id} names {endpoint!r}, which is not a loaded anchor; "
                    "no stage may reconstruct a parent from a filename or a default"
                )
        outcome = nd.bridge_probes(
            by_id[edge.left], by_id[edge.right], model, validator,
            stage=stage, fractions=fractions, seed=seed, edge_id=edge.edge_id,
            index_offset=index * 1000,
        )
        outcomes.append(outcome)
        proposals.extend(outcome.accepted)

    if minimal:
        nd.assert_quotas(
            outcomes,
            {nc.KERNEL_BRIDGE: len(fractions) * len(minimal)},
            what=f"{stage}/{model.model} bridge preparation",
        )
    return proposals, outcomes, edges, minimal


def _parent_tables(parent_dir: Path, model: nc.ModelSpace) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anchors and their completed local assessment, from the parent stage."""
    anchors_path = parent_dir / "anchors.csv"
    support_path = parent_dir / "local_support.csv"
    if not anchors_path.exists():
        raise ContractError(f"parent stage has no anchors.csv: {anchors_path}")
    if not support_path.exists():
        raise ContractError(
            f"parent stage has no local_support.csv: {support_path}. Run 'classify' on the "
            "parent stage first: a confirmed point may not enter the graph or representative "
            "selection without a completed local assessment."
        )
    anchors = ne.read_frame_exact(anchors_path)
    support = ne.read_frame_exact(support_path)
    if "endpoint_confirmed" not in support.columns:
        raise ContractError(
            f"{support_path} has no independent endpoint-confirmation verdict"
        )
    confirmed = support[support["endpoint_confirmed"].astype(bool)].copy()
    confirmed_ids = set(confirmed["candidate_id"].astype(str))
    anchors = anchors[anchors["candidate_id"].astype(str).isin(confirmed_ids)].copy()
    if anchors.empty:
        raise ContractError(
            "the bridge graph has no endpoint independently confirmed on the reserved "
            "seed block"
        )
    if set(anchors["candidate_id"].astype(str)) != confirmed_ids:
        missing = sorted(confirmed_ids - set(anchors["candidate_id"].astype(str)))
        raise ContractError(f"confirmed graph endpoints are missing from anchors.csv: {missing}")
    return anchors, confirmed


def command_prepare(args: argparse.Namespace) -> int:
    plan = nc.FrozenPlan.load(args.plan)
    validated_acceptance = _validate_declared_acceptance_package(plan)
    spaces = _model_spaces(plan)
    model = spaces[args.model]
    is_bridges = args.stage == nc.STAGE_BRIDGES
    embedded_m0_anchors: list[nd.Anchor] | None = None
    m0_source_manifest: dict | None = None
    identity_gate_source: Path | None = None
    identity_gate_payload: dict | None = None

    if model.model == nc.MODEL_M1:
        nc.assert_m1_identity_preconditions(
            routine_hazard_scale=float(plan.require(
                "p_parameter.routine_hazard_scale_identity_value")),
            required_value=1.0,
        )
        # The signed prerequisite is authoritative.  A CLI path is only a
        # compatibility assertion: accepting another valid certificate would
        # silently replace a signed scientific input at prepare time.
        identity_gate_source = _signed_prerequisite_path(plan, "identity_gate")
        if args.identity_gate and not _paths_match(
            args.identity_gate, identity_gate_source
        ):
            raise ContractError(
                "--identity-gate may only assert the exact path signed at "
                "production_prerequisites.identity_gate; arbitrary replacement "
                "certificates are not accepted"
            )
        identity_gate_payload = nig.assert_gate_satisfied(
            identity_gate_source,
            root=ROOT,
            expected_base_config=str(plan.require("models.base_config")),
            expected_plan_sha256=plan.plan_sha256,
            expected_scientific_config_sha256=_scientific_config_sha256(plan, model),
        )
        print(
            "M0/M1 identity gate: PASSED "
            f"({identity_gate_payload['n_passed']}/"
            f"{identity_gate_payload['n_checks']} checks, "
            f"{identity_gate_payload['generated_utc']})"
        )
        if args.stage == nc.STAGE_DISCOVERY:
            if not args.m0_confirmed_dir:
                raise ContractError(
                    "M1 discovery requires --m0-confirmed-dir naming the COMPLETE M0 "
                    "confirmation stage"
                )
            m0_dir = _stage_directory(args.m0_confirmed_dir)
            m0_manifest = ne.validate_parent_stage(
                m0_dir, expected_stage=nc.STAGE_CONFIRMATION,
                expected_model=nc.MODEL_M0, targets=plan.targets,
                seeds=plan.seeds, child_stage=None,
            )
            _assert_plan_matches_manifest(
                plan, m0_manifest, what="M0-to-M1 embedding parent"
            )
            _assert_acceptance_basis(
                m0_dir, m0_manifest, plan, what="M0-to-M1 embedding"
            )
            m0_source_manifest = m0_manifest
            m0_scores = ne.read_frame_exact(m0_dir / "candidate_scores.csv")
            ids = set(m0_scores.loc[m0_scores["nominal_pass"].astype(bool), "design_id"].astype(str))
            m0_vectors = ne.read_frame_exact(m0_dir / "design_vectors.csv").set_index("design_id")
            embedded_m0_anchors = []
            for candidate_id in sorted(ids):
                m0_params = {name: float(m0_vectors.loc[candidate_id, name]) for name in spaces[nc.MODEL_M0].names}
                params = nc.embed_m0_in_m1(spaces[nc.MODEL_M0], model, m0_params, p_value=0.0)
                embedded_m0_anchors.append(nd.Anchor.build(candidate_id, params, model))
            if not embedded_m0_anchors:
                raise ContractError("the M0 confirmation stage contains no nominally confirmed vectors")

    # A stage beyond the first must descend from a validated, COMPLETE parent.
    # The guard existed and was tested but was not connected: prepare took the
    # parent hash as free text, so `--stage confirmation` could be run against
    # nothing at all, and a PARTIAL stage could parent the next one.
    # 'prepare' is a command, not a produced stage: the discovery stage is the
    # first thing written and has no predecessor directory. Every stage after
    # it must descend from a validated, COMPLETE parent directory.
    required_parent = nc.STAGE_PARENT.get(args.stage)
    if required_parent == nc.STAGE_PREPARE:
        required_parent = None
    parent_manifest = None
    parent_manifest_sha256 = ""
    if required_parent is not None:
        if not args.parent_dir:
            raise ContractError(
                f"stage {args.stage!r} descends from {required_parent!r}, so "
                "--parent-dir is required. No stage beyond 'prepare' may be produced "
                "without a validated parent."
            )
        parent_manifest = ne.validate_parent_stage(
            _stage_directory(args.parent_dir),
            expected_stage=required_parent,
            expected_model=model.model,
            targets=plan.targets,
            seeds=plan.seeds,
            child_stage=args.stage,
        )
        _assert_plan_matches_manifest(
            plan, parent_manifest, what=f"prepare {args.stage} parent"
        )
        parent_manifest_sha256 = ccs.sha256_json(nc.json_safe(parent_manifest))
        if args.parent_manifest_sha256 and args.parent_manifest_sha256 != parent_manifest_sha256:
            raise ContractError(
                f"declared parent manifest hash {args.parent_manifest_sha256} does not "
                f"match the validated parent ({parent_manifest_sha256})"
            )
        if model.model == nc.MODEL_M1 and str(
            parent_manifest.get("model") or ""
        ) == nc.MODEL_M1:
            if identity_gate_source is None:
                raise ContractError("M1 child preparation has no signed identity gate")
            parent_gate = _stage_directory(args.parent_dir) / nig.GATE_FILENAME
            if not parent_gate.is_file() or ccs.sha256_file(
                parent_gate
            ) != ccs.sha256_file(identity_gate_source):
                raise ContractError(
                    "the M1 child identity gate differs from its sealed parent; "
                    "one M1 lineage must preserve one exact gate certificate"
                )
    elif args.parent_dir:
        raise ContractError(
            f"stage {args.stage!r} has no parent, but --parent-dir was supplied"
        )
    if m0_source_manifest is not None:
        # M1 discovery is the first stage in the M1 stage order, but it is not
        # scientifically parentless: its entire anchor set comes from the M0
        # confirmation.  Bind every work unit and the manifest to that exact
        # validated source, and freeze its acceptance basis like any other
        # child handoff.
        parent_manifest = m0_source_manifest
        parent_manifest_sha256 = ccs.sha256_json(nc.json_safe(parent_manifest))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    acceptance_package_identity = _freeze_acceptance_package(
        out, validated_acceptance
    )

    extra: dict = {}
    if identity_gate_source is not None and identity_gate_payload is not None:
        frozen_gate = out / nig.GATE_FILENAME
        if frozen_gate.exists():
            raise ContractError(
                f"refusing to replace existing frozen identity gate: {frozen_gate}"
            )
        source_gate_sha = ccs.sha256_file(identity_gate_source)
        shutil.copyfile(identity_gate_source, frozen_gate)
        frozen_gate_sha = ccs.sha256_file(frozen_gate)
        if frozen_gate_sha != source_gate_sha:
            raise ContractError("the identity gate changed while it was being frozen")
        frozen_gate_payload = nig.assert_gate_satisfied(
            frozen_gate,
            root=ROOT,
            expected_base_config=str(plan.require("models.base_config")),
            expected_plan_sha256=plan.plan_sha256,
            expected_scientific_config_sha256=_scientific_config_sha256(plan, model),
        )
        extra.update({
            "identity_gate_source": str(identity_gate_source),
            "identity_gate_source_sha256": source_gate_sha,
            "identity_gate_sha256": frozen_gate_sha,
            "identity_gate_schema": frozen_gate_payload["schema"],
        })
    if is_bridges:
        # The bridges stage takes its node set from the parent's completed
        # local assessment, not from a hand-supplied anchor file: the graph
        # must be built over exactly the points the parent confirmed and
        # assessed, and nothing else.
        if args.anchors:
            raise ContractError(
                "--anchors is not accepted for the bridges stage. Its node set comes from "
                "the validated parent's anchors.csv and local_support.csv, so the graph "
                "cannot be built over a different set of points than the one assessed."
            )
        parent_anchors, parent_support = _parent_tables(
            _stage_directory(args.parent_dir), model
        )
        anchors = [
            nd.Anchor.build(
                str(row["candidate_id"]),
                {name: float(row[name]) for name in model.names},
                model,
            )
            for _, row in parent_anchors.iterrows()
        ]
        nodes = nas.graph_nodes_table(
            anchors=parent_anchors, local_support=parent_support, model=model
        )
        proposals, outcomes, edges, minimal = build_bridge_designs(
            plan, model, anchors, nodes, stage=args.stage
        )
        tables = ng.graph_tables(edges, minimal, [], [])
        ne.atomic_write_frame(out / "graph_nodes.csv", nodes)
        ne.atomic_write_frame(out / "candidate_edges.csv", tables["candidate_edges"])
        # Carried forward verbatim so the bridges package reports the same
        # local assessment the graph was built from.
        ne.atomic_write_frame(out / "local_support.csv", parent_support)
        eligible = sum(1 for value in nas.node_eligibility(nodes).values() if value)
        extra.update({
            "n_nodes": len(nodes),
            "n_eligible_nodes": eligible,
            "n_candidate_edges": len(edges),
            "n_minimal_edges": len(minimal),
            "bridge_fractions": list(plan.bridge_fractions()),
        })
        print(f"  graph: {len(nodes)} node(s), {eligible} eligible, "
              f"{len(edges)} proposed edge(s), {len(minimal)} in the minimal set")
    else:
        if args.stage in (nc.STAGE_PROMOTION, nc.STAGE_CONFIRMATION):
            if args.anchors or args.archive:
                raise ContractError(
                    f"stage {args.stage!r} derives its exact candidate set from the "
                    "parent's recomputed promoted column; --anchors/--archive are forbidden"
                )
            anchors, proposals, outcomes = _selected_parent_design(
                _stage_directory(args.parent_dir), model, child_stage=args.stage
            )
            extra["allocation_only"] = True
            extra["selected_parent_candidate_ids"] = [p.design_id for p in proposals]
            extra["selected_parent_vector_hashes"] = [p.vector_hash for p in proposals]
        else:
            if embedded_m0_anchors is not None:
                # M1 starts from the UNION of two independently meaningful
                # evidence sets: confirmed M0 vectors embedded at exact p=0,
                # and any independently confirmed positive-p M1 vectors the
                # caller supplied.  Replacing the latter with the former
                # would erase precisely the M1 evidence this stage is meant
                # to extend.  Candidate ids are evidence identities, so a
                # collision is accepted only when it denotes the same vector.
                if not args.anchors:
                    raise ContractError(
                        "M1 discovery requires --anchors naming independently confirmed "
                        "positive-p M1 vectors in addition to the sealed M0-at-p=0 "
                        "embedding source. An M0-only boundary design cannot establish "
                        "support for the scientifically distinct M1 mechanism."
                    )
                evidence_source = _signed_prerequisite_path(
                    plan, "m1_anchor_confirmation"
                )
                if args.m1_anchor_evidence and not _paths_match(
                    args.m1_anchor_evidence, evidence_source
                ):
                    raise ContractError(
                        "--m1-anchor-evidence may only assert the exact path signed at "
                        "production_prerequisites.m1_anchor_confirmation; arbitrary "
                        "replacement evidence is not accepted"
                    )
                independent_path = Path(args.anchors)
                independent_m1 = _load_anchors(independent_path, model)
                non_positive = sorted(
                    anchor.anchor_id for anchor in independent_m1
                    if not float(anchor.params[model.p_name]) > 0.0
                )
                if non_positive:
                    raise ContractError(
                        "independent M1 anchors must all have strictly positive p; "
                        f"exact-p=0 controls come only from the sealed M0 embedding: "
                        f"{non_positive}"
                    )
                evidence = _validate_m1_anchor_evidence(
                    evidence_source, anchors_path=independent_path,
                    anchors=independent_m1, plan=plan, model=model,
                    acceptance_identity=acceptance_package_identity[
                        "acceptance_input_identity"
                    ],
                    observed=_load_strict_json(out / nai.OBSERVED_FILENAME),
                    base_variance=_load_strict_json(
                        out / nai.BASE_VARIANCE_FILENAME
                    ),
                )
                by_id = {anchor.anchor_id: anchor for anchor in independent_m1}
                for anchor in embedded_m0_anchors:
                    prior = by_id.get(anchor.anchor_id)
                    if prior is not None and prior.vector_hash != anchor.vector_hash:
                        raise ContractError(
                            f"M1 anchor id {anchor.anchor_id!r} denotes both an "
                            "independently confirmed M1 vector and a different embedded "
                            "M0-at-p=0 vector; candidate ids must be unambiguous"
                        )
                    by_id.setdefault(anchor.anchor_id, anchor)
                ids_by_hash: dict[str, list[str]] = {}
                for anchor in by_id.values():
                    ids_by_hash.setdefault(anchor.vector_hash, []).append(anchor.anchor_id)
                duplicate_vectors = {
                    vector_hash: sorted(anchor_ids)
                    for vector_hash, anchor_ids in ids_by_hash.items()
                    if len(anchor_ids) > 1
                }
                if duplicate_vectors:
                    raise ContractError(
                        "M1 anchor union assigns different evidence ids to the same "
                        f"complete vector: {duplicate_vectors}. Deduplicate the confirmed "
                        "evidence explicitly rather than inflating anchor quotas."
                    )
                anchors = [by_id[candidate_id] for candidate_id in sorted(by_id)]
                evidence = _package_m1_anchor_evidence(
                    evidence,
                    source_path=evidence_source,
                    anchors_source=independent_path,
                    stage_dir=out,
                )
                ne.atomic_write_json(out / M1_ANCHOR_EVIDENCE_FILENAME, evidence)
                _validate_m1_anchor_evidence(
                    out / M1_ANCHOR_EVIDENCE_FILENAME,
                    anchors_path=out / M1_INDEPENDENT_ANCHORS_FILENAME,
                    anchors=_load_anchors(
                        out / M1_INDEPENDENT_ANCHORS_FILENAME, model
                    ),
                    plan=plan,
                    model=model,
                    acceptance_identity=acceptance_package_identity[
                        "acceptance_input_identity"
                    ],
                    observed=_load_strict_json(out / nai.OBSERVED_FILENAME),
                    base_variance=_load_strict_json(
                        out / nai.BASE_VARIANCE_FILENAME
                    ),
                )
                extra["m0_embedding_source"] = str(_stage_directory(args.m0_confirmed_dir))
                extra["m0_embedding_exact_p"] = 0.0
                extra["m0_embedding_source_manifest_sha256"] = parent_manifest_sha256
                extra["n_independently_confirmed_m1_anchors"] = len(independent_m1)
                extra["independently_confirmed_m1_source"] = str(independent_path)
                extra["independently_confirmed_m1_source_sha256"] = ccs.sha256_file(
                    independent_path
                )
                extra["independently_confirmed_m1_evidence_source"] = str(
                    evidence_source
                )
                extra["independently_confirmed_m1_evidence_source_sha256"] = (
                    ccs.sha256_file(evidence_source)
                )
                extra["independently_confirmed_m1_evidence"] = (
                    M1_ANCHOR_EVIDENCE_FILENAME
                )
                extra["independently_confirmed_m1_evidence_sha256"] = ccs.sha256_file(
                    out / M1_ANCHOR_EVIDENCE_FILENAME
                )
                extra["independently_confirmed_m1_evidence_schema"] = evidence["schema"]
                extra["n_embedded_m0_identity_anchors"] = len(embedded_m0_anchors)
                extra["m1_anchor_combination"] = "union_by_candidate_id"
                extra["independently_confirmed_m1_anchor_ids"] = [
                    anchor.anchor_id for anchor in independent_m1
                ]
                extra["independently_confirmed_m1_vector_hashes"] = [
                    anchor.vector_hash for anchor in independent_m1
                ]
                extra["embedded_m0_identity_anchor_ids"] = [
                    anchor.anchor_id for anchor in embedded_m0_anchors
                ]
                extra["embedded_m0_identity_vector_hashes"] = [
                    anchor.vector_hash for anchor in embedded_m0_anchors
                ]
            elif not args.anchors:
                raise ContractError(
                    f"stage {args.stage!r} requires --anchors: a CSV of complete joint "
                    "confirmed vectors"
                )
            else:
                anchors = _load_anchors(Path(args.anchors), model)
            archive, archive_ids, archive_j = _load_archive(args.archive, model)
            proposals, outcomes = build_designs(
                plan, model, anchors, stage=args.stage,
                archive=archive, archive_ids=archive_ids, archive_j=archive_j,
            )
    if not anchors:
        raise ContractError("no anchors supplied; there is nothing to assess")
    if args.stage == nc.STAGE_DISCOVERY:
        # Reach the already-defined contract from the production CLI.  Anchor
        # controls are retained: adding exact anchors cannot manufacture
        # variation, while excluding them could hide a frozen coordinate.
        nd.assert_coordinates_vary(proposals, model)

    seeds = () if args.stage == nc.STAGE_PROMOTION else plan.seeds.seeds_for(args.stage)
    code_hash = _code_sha256()
    units = tuple(
        ne.WorkUnit(
            stage=args.stage, model=model.model, design_id=p.design_id, seed=int(seed),
            vector_hash=p.vector_hash, kernel=p.kernel, parent_ids=p.parent_ids,
            params=p.params, parent_manifest_sha256=parent_manifest_sha256,
            code_sha256=code_hash, config_sha256=_scientific_config_sha256(plan, model),
            target_schema=plan.targets.frozen_order,
        )
        for p in proposals for seed in seeds
    )
    ledger = ne.Ledger(stage=args.stage, model=model.model, units=units)
    for shards in (1, 2, 3, 4, 7):
        ledger.assert_shard_partition(shards)
    budget = int(plan.require("budget.max_trajectories"))
    attempt_budget = int(plan.require("budget.max_attempts"))
    n_trajectories = len(ledger.unique_result_keys())
    if n_trajectories > budget or n_trajectories > attempt_budget:
        raise ContractError(
            f"the frozen design has {n_trajectories} unique trajectories "
            f"({len(units)} linked work units) but the declared budgets are "
            f"{budget} trajectories and {attempt_budget} attempts; refusing to prepare "
            "work that cannot complete within its signed budget"
        )

    ne.atomic_write_frame(
        out / "design_proposals.csv",
        pd.DataFrame([p.as_row() for p in proposals]),
    )
    ne.atomic_write_frame(
        out / "design_vectors.csv",
        pd.DataFrame([{"design_id": p.design_id, **p.params} for p in proposals]),
    )
    if args.stage in (nc.STAGE_PROMOTION, nc.STAGE_CONFIRMATION):
        ne.atomic_write_frame(
            out / "kernel_outcomes.csv",
            ne.read_frame_exact(_stage_directory(args.parent_dir) / "kernel_outcomes.csv"),
        )
    else:
        ne.atomic_write_frame(
            out / "kernel_outcomes.csv",
            pd.DataFrame([
                {
                    **row,
                    "rejection_counts": json.dumps(row["rejection_counts"]),
                    "rejected_cells": json.dumps(row["rejected_cells"]),
                }
                for row in (outcome.as_row() for outcome in outcomes)
            ]),
        )
    ne.atomic_write_frame(
        out / "anchors.csv",
        pd.DataFrame([{"candidate_id": a.anchor_id, "vector_hash": a.vector_hash,
                       **a.params} for a in anchors]),
    )
    ledger.save(out)
    frozen_identity = None
    if parent_manifest is not None:
        identity_parent_dir = _stage_directory(
            args.parent_dir or args.m0_confirmed_dir
        )
        frozen_identity = _freeze_parent_acceptance_identity(
            out, parent_manifest, parent_dir=identity_parent_dir,
            current_package_identity=acceptance_package_identity,
        )
    if args.stage == nc.STAGE_PROMOTION:
        parent_scores = ne.read_frame_exact(
            _stage_directory(args.parent_dir) / "candidate_scores.csv"
        )
        selected = parent_scores[
            parent_scores["design_id"].astype(str).isin([p.design_id for p in proposals])
        ].copy()
        ne.atomic_write_frame(out / "candidate_scores.csv", selected)
        ne.write_stage_manifest(
            out, stage=args.stage, model=model.model, status=nc.STATUS_COMPLETE,
            ledger=ledger, contract=plan.manifest_contract(),
            parent_manifest=parent_manifest,
            extra={
                "n_anchors": len(anchors), "n_proposals": len(proposals),
                "allocation_only": True,
                "acceptance_input_identity": acceptance_package_identity[
                    "acceptance_input_identity"
                ],
                "acceptance_package_identity": acceptance_package_identity,
                "selected_parent_candidate_ids": [p.design_id for p in proposals],
                "selected_parent_vector_hashes": [p.vector_hash for p in proposals],
                **extra,
            },
        )
        print(f"PROMOTED {len(proposals)} candidate(s) by frozen allocation only; "
              "no trajectory was dispatched")
        return 0
    ne.write_stage_manifest(
        out, stage=args.stage, model=model.model, status=nc.STATUS_PREPARED,
        ledger=ledger, contract=plan.manifest_contract(),
        parent_manifest=parent_manifest,
        extra={
            "n_anchors": len(anchors), "n_proposals": len(proposals),
            "acceptance_input_identity": acceptance_package_identity[
                "acceptance_input_identity"
            ],
            "acceptance_package_identity": acceptance_package_identity,
            **({"parent_acceptance_identity": frozen_identity} if frozen_identity else {}),
            **extra,
        },
    )
    print(f"PREPARED {args.stage}/{model.model} in {out}")
    print(f"  anchors:    {len(anchors)}")
    print(f"  proposals:  {len(proposals)}")
    print(f"  work units: {len(units)} (= proposals x K={len(seeds)})")
    print(f"  declared trajectory budget: {budget}")
    print("\nNo simulation has been run. Dispatch with 'evaluate'.")
    return 0


# ==========================================================================
# evaluate (worker)
# ==========================================================================

#: Modules whose content defines the scientific behaviour of a stage.  Hashed
#: at prepare, re-checked by every worker, and recorded in the manifest.
CODE_FILES = (
    "run_robust_nroy_freeze.py", "nroy_contract.py", "nroy_design.py",
    "nroy_execution.py", "nroy_graph.py", "nroy_assess.py",
    "nroy_acceptance_inputs.py", "run_v3.py",
    "run_lhs_calibration.py", "calib_extractor.py", "hm_target_register.py",
    "stage_structured_diagnosis.py", "stochastic_allocation.py", "counter_based_rng.py",
    # Direct and transitive production-engine dependencies.  Omitting one of
    # these lets prepared work survive a scientific code change with the same
    # recorded fingerprint, which is equivalent to running a different model
    # under old provenance.
    "engine_v3.py", "config_utils.py", "config_reader.py",
    "transmission_corrected.py", "demography.py", "migration.py",
    "schedule_utils.py", "aids_observation.py", "exogenous_diagnoses.py",
    "output_writer.py", "hm_variance_budget.py",
)


def _code_sha256() -> str:
    return ccs.sha256_json({
        "files": {name: ccs.sha256_file(ROOT / name) for name in CODE_FILES},
        "runtime": nig.scientific_runtime_fingerprint(),
    })


def _scientific_config_sha256(plan: nc.FrozenPlan, model: nc.ModelSpace) -> str:
    """Bind work to the plan, base config and exact calibration space contents."""
    base = Path(str(plan.require("models.base_config")))
    if not base.is_absolute():
        base = ROOT / base
    space = Path(str(plan.require("models.m0.calib_space")))
    if not space.is_absolute():
        space = ROOT / space
    register = Path(plan.targets.register_path)
    if not register.is_absolute():
        register = ROOT / register
    payload = {
        "plan_sha256": plan.plan_sha256,
        "base_config_sha256": ccs.sha256_file(base),
        "m0_calibration_space_sha256": ccs.sha256_file(space),
        "target_register_sha256": ccs.sha256_file(register),
        "model": model.model,
        "parameter_names": list(model.names),
        "lower": [float(x) for x in model.space.lower],
        "upper": [float(x) for x in model.space.upper],
    }
    return ccs.sha256_json(payload)


def _stage_directory(path: str) -> Path:
    """Resolve a prepared stage directory, refusing anything unusable.

    A missing directory or manifest is a clear contract error, not a traceback:
    the caller pointed at something that was never prepared.
    """
    stage_dir = Path(path)
    if not stage_dir.exists():
        raise ContractError(f"stage directory does not exist: {stage_dir}")
    manifest = stage_dir / "manifest.json"
    if not manifest.exists():
        raise ContractError(
            f"{stage_dir} has no manifest.json, so it is not a prepared stage. "
            "Run 'prepare' first."
        )
    return stage_dir


def _assert_plan_matches_manifest(plan: nc.FrozenPlan, manifest: Mapping, *, what: str) -> None:
    """A completed/prepared stage may only be interpreted under its frozen plan."""
    recorded = str((manifest.get("contract") or {}).get("plan_sha256") or "")
    if not recorded:
        raise ContractError(f"{what}: stage manifest records no run-plan hash")
    if recorded != plan.plan_sha256:
        raise ContractError(
            f"{what}: current plan hash {plan.plan_sha256} differs from the stage's "
            f"frozen plan hash {recorded}; re-prepare instead of reinterpreting evidence"
        )


def surrogate_evaluator(
    unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
) -> tuple[float, ...]:
    """A deterministic stand-in for the simulator.  Runs no engine.

    Used by the verification suite so that sharding, merge, resume and
    reporting can be tested end to end without a single production
    trajectory.  It depends only on the vector hash and the seed, so it is
    reproducible and shard-order independent.
    """
    rng = np.random.default_rng(
        [int(unit.seed), int(unit.vector_hash[:16], 16) % (2 ** 63)]
    )
    return tuple(100.0 + 10.0 * rng.random(len(targets)))


#: Plausible islands for the ``surrogate_islands`` evaluator, as positions of
#: the **first declared coordinate** in normalised space; every other
#: coordinate sits at 0.5.  Equally spaced on purpose, so that two anchors
#: placed one spacing beyond each end put the 25/50/75% bridge probes exactly
#: on the three islands.
ISLAND_CENTRES = (0.35, 0.5, 0.65)

#: Island radius in normalised Euclidean distance.  Small enough that no
#: ambient probe (declared scales 0.025 and 0.05) around an anchor one spacing
#: away can reach one.
ISLAND_RADIUS = 0.02


def island_field_i_max(u: Sequence[float]) -> float:
    """``I_max`` of a field that is plausible only on a few isolated islands.

    Closed form, deterministic in the vector, and arranged so that a point is
    plausible (``I_max <= 3``) only within :data:`ISLAND_RADIUS` of an island
    centre.  This is the honest way to build the "no local support but a
    supported bridge" case: *one* acceptance basis, applied identically at
    every stage, over a geometry where the plausible set lies between the
    anchors rather than at them.  It is also a fair caricature of the thing
    this stage exists to detect, and of the thing it must not overclaim:
    bridge probes confirming a straight path says nothing about continuity.
    """
    point = np.asarray(u, dtype=float)
    best = min(
        float(np.linalg.norm(point - island_centre(position, point.size)))
        for position in ISLAND_CENTRES
    )
    return 1.0 if best <= ISLAND_RADIUS else 1.0 + 1000.0 * best


def island_centre(position: float, n: int) -> np.ndarray:
    """One island centre: ``position`` on the first coordinate, 0.5 elsewhere."""
    centre = np.full(int(n), 0.5)
    centre[0] = float(position)
    return centre


def _island_surrogate(space: nc.ModelSpace) -> Callable:
    """The island field as an evaluator.  Runs no engine; verification only.

    With ``observed = 0`` and ``B_i = 1`` the recomputed ``I_max`` of the first
    target reproduces the field, which is the convention the rest of the
    verification suite already uses.  A tiny per-seed perturbation keeps the
    replicate variance non-degenerate without moving any verdict.
    """
    def evaluate(
        unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
    ) -> tuple[float, ...]:
        u = space.normalise([float(params[name]) for name in space.names])
        signal = island_field_i_max(u) + 1e-6 * float(int(unit.seed) % 3)
        return tuple([signal] + [0.0] * (len(targets) - 1))

    return evaluate


class ProductionEvaluator:
    """The real simulator, behind every fail-closed protection in this stage.

    Construction is the gate, and it is the only way to obtain a callable that
    touches :class:`run_v3.SimulationEngineV3`.  To build one, *all* of the
    following must already hold:

    * the plan loaded, which means every required decision is signed and the
      ``sign_off`` block is complete (:meth:`nroy_contract.FrozenPlan.load`);
    * the frozen production model config declared and present on disk;
    * the frozen target register resolving **every** target in the contract,
      in the contract's order -- a target that cannot be resolved is fatal
      here rather than dropped, because a silently shorter target vector is a
      weaker test wearing the same name;
    * the caller having passed ``--evaluator production`` explicitly (there is
      no default) and ``--i-have-priced-the-budget``;
    * a validated parent stage recorded on the work units it is handed.

    Each call injects one complete joint vector, runs one trajectory at the
    unit's frozen seed, and returns the 13 target values in the frozen order.
    It never redraws, never substitutes a failure and never writes.
    """

    def __init__(self, plan: nc.FrozenPlan, *, root: Path = ROOT):
        import hm_target_register as tr  # noqa: PLC0415 - heavy, production-only

        self.plan = plan
        config_path = Path(str(plan.require("models.base_config")))
        if not config_path.is_absolute():
            config_path = root / config_path
        if not config_path.exists():
            raise ContractError(
                f"the declared production model config does not exist: {config_path}"
            )
        self.config_path = config_path
        self.base_config = _load_strict_json(config_path)
        self.config_sha256 = ccs.sha256_file(config_path)

        space_path = Path(str(plan.require("models.m0.calib_space")))
        if not space_path.is_absolute():
            space_path = root / space_path
        self.m0_calib_space = _load_strict_json(space_path)

        register_path = Path(plan.targets.register_path)
        if not register_path.is_absolute():
            register_path = root / register_path
        register_payload = _load_strict_json(register_path)
        if not isinstance(register_payload, Mapping):
            raise ContractError(
                f"the frozen target register must be a JSON object: {register_path}"
            )
        register = tr.load_register(register_path)
        by_key = {target.key: target for target in register.targets}
        missing = [key for key in plan.targets.frozen_order if key not in by_key]
        if missing:
            raise ContractError(
                f"the frozen target register does not define {missing}; the production "
                "evaluator will not run against a shorter target vector than the contract"
            )
        self.targets = [by_key[key] for key in plan.targets.frozen_order]
        self.start_year = min(int(t.year) for t in self.targets)
        self.end_year = max(int(t.year) for t in self.targets)

    def calib_space_for(self, model: str) -> dict:
        """The calibration space this model injects through.

        M1 is *generated* from M0 plus the signed ``p`` range rather than read
        from a second file, which is what guarantees the injected M1 vector is
        M0 plus one coordinate and nothing else.
        """
        if model == nc.MODEL_M0:
            return self.m0_calib_space
        return nc.build_m1_calib_space(
            self.m0_calib_space,
            p_name=str(self.plan.require("p_parameter.name")),
            p_config_path=str(self.plan.require("p_parameter.config_path")),
            p_min=float(self.plan.require("p_parameter.min")),
            p_max=float(self.plan.require("p_parameter.max")),
        )

    def config_for(self, unit: ne.WorkUnit, params: Mapping[str, float]) -> dict:
        """The injected config for one work unit.  No simulation yet.

        Separated from :meth:`__call__` so the verification suite can assert
        that the right value lands at the right config path -- including the
        ``p`` path, whose silent-discard failure mode is why it is a signed
        decision -- without running a trajectory.
        """
        from run_lhs_calibration import inject_params  # noqa: PLC0415

        if unit.model == nc.MODEL_M1:
            # M1 pins the routine hazard scale; without it the engine's p=0
            # identity dispatch does not hold and M1 would not nest M0.
            nc.assert_m1_identity_preconditions(
                routine_hazard_scale=float(self.plan.require(
                    "p_parameter.routine_hazard_scale_identity_value")),
                required_value=1.0,
            )
        try:
            # The tracked file is a safe standalone M0 config (block present
            # but disabled).  Both production variants are chosen explicitly
            # on a deep-copied runtime config: M0 removes the block and M1
            # enables it.  No stored enabled state is trusted.
            config = ssd.route_shared_config(self.base_config, model=unit.model)
        except ssd.StageStructuredDiagnosisConfigError as exc:
            raise ContractError(f"invalid shared M0/M1 production config: {exc}") from exc
        try:
            config = inject_params(
                config,
                {name: float(value) for name, value in params.items()},
                self.calib_space_for(unit.model),
            )
        except KeyError as exc:
            raise ContractError(
                f"the shared production config cannot accept the complete "
                f"{unit.model} vector: {exc}"
            ) from exc

        if unit.model == nc.MODEL_M1:
            node = config
            for key in ssd.PROFILE_PATH:
                if key not in node:
                    raise ContractError(
                        f"the injected M1 config has no {'.'.join(ssd.PROFILE_PATH)} block"
                    )
                node = node[key]
            enabled = node.get("enabled")
            if isinstance(enabled, Mapping):
                enabled = enabled.get("value")
            if enabled is not True:
                raise ContractError(
                    "the production M1 route did not explicitly enable "
                    "stage_structured_diagnosis"
                )
            declared_start_year = int(self.plan.require(
                "p_parameter.symptom_start_year"
            ))
            start_year_node = node.get("symptom_start_year")
            if not isinstance(start_year_node, Mapping) or "value" not in start_year_node:
                raise ContractError(
                    "the shared M1 config has no explicit symptom_start_year.value to bind "
                    "to the signed scientific decision"
                )
            node["symptom_start_year"] = {
                **dict(start_year_node),
                "value": declared_start_year,
            }
            actual = node.get("routine_hazard_scale")
            if isinstance(actual, Mapping):
                actual = actual.get("value")
            declared = float(self.plan.require(
                "p_parameter.routine_hazard_scale_identity_value"
            ))
            if actual is None or float(actual) != declared:
                raise ContractError(
                    f"the injected config contains routine_hazard_scale={actual!r}, but "
                    f"the signed identity value is {declared!r}"
                )
        return config

    def __call__(
        self, unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
    ) -> tuple[float, ...]:
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415

        import hm_target_register as tr  # noqa: PLC0415
        from calib_extractor import extract_annual_outputs  # noqa: PLC0415
        from run_v3 import SimulationEngineV3  # noqa: PLC0415

        if tuple(targets) != self.plan.targets.frozen_order:
            raise ContractError(
                "the production evaluator was handed a target order that is not the frozen "
                "contract; a reordered target vector is fatal, not silently corrected"
            )
        config = self.config_for(unit, params)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            engine = SimulationEngineV3(config, seed=int(unit.seed))
            result = engine.run(run_number=1)
        outputs = extract_annual_outputs(result, self.start_year, self.end_year)
        row: dict[str, float] = {}
        for indicator, series in outputs.items():
            for year, value in series.items():
                row[f"{indicator}_{int(year)}"] = float(value)
        simulated, resolved, unresolved = tr.resolve_simulated(
            self.targets, pd.DataFrame([row])
        )
        if unresolved:
            raise ContractError(
                f"the simulator did not produce {[u['target_key'] for u in unresolved]}; "
                "a target that cannot be resolved is fatal rather than dropped"
            )
        self.plan.targets.assert_order(
            [t.key for t in resolved], what="production evaluator outputs"
        )
        return tuple(float(simulated[key][0]) for key in self.plan.targets.frozen_order)


def production_evaluator(
    unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
) -> tuple[float, ...]:
    """Refusal stub.  The real adapter is :class:`ProductionEvaluator`.

    This exists so that the module-level ``EVALUATORS`` table cannot hand out a
    working production callable without going through
    :func:`resolve_evaluator`, which is where the priced-budget
    acknowledgement and the plan are checked.  Reaching this function means a
    caller bypassed that gate.
    """
    raise ContractError(
        "the production evaluator must be constructed through resolve_evaluator(), which "
        "checks the signed plan, the declared production config, the frozen target "
        "register and the priced-budget acknowledgement. Reaching this stub means that "
        "gate was bypassed."
    )


EVALUATORS: dict[str, Callable] = {
    "surrogate": surrogate_evaluator,
    # Built per model space, because the field is defined on normalised
    # coordinates; resolved in resolve_evaluator rather than stored here.
    "surrogate_islands": None,
    "production": production_evaluator,
}


def resolve_evaluator(
    name: str,
    plan: nc.FrozenPlan,
    *,
    priced: bool,
    ledger: ne.Ledger,
) -> Callable:
    """The single gate between a frozen design and spent production compute.

    ``surrogate`` runs no engine and is what the verification suite uses.
    ``production`` is refused unless the budget has been explicitly priced and
    the frozen work carries a validated parent, in addition to the signed plan
    that :meth:`nroy_contract.FrozenPlan.load` has already insisted on.

    There is deliberately **no default**: ``--evaluator`` is a required
    argument, so no command line can reach the simulator by omission.
    """
    if name not in EVALUATORS:
        raise ContractError(f"unknown evaluator {name!r}; choose from {sorted(EVALUATORS)}")
    if name == "surrogate_islands":
        return _island_surrogate(_model_spaces(plan)[str(ledger.model)])
    if name != "production":
        return EVALUATORS[name]
    if not priced:
        raise ContractError(
            "refusing to launch the production evaluator without "
            "--i-have-priced-the-budget. The production specification requires a measured "
            "pilot and a priced trajectory budget first, and launch attempts consume "
            "budget that is never refunded."
        )
    stage = ledger.stage
    if nc.STAGE_PARENT.get(stage) not in (None, nc.STAGE_PREPARE):
        unparented = sorted({
            unit.design_id for unit in ledger.units if not unit.parent_manifest_sha256
        })
        if unparented:
            raise ContractError(
                f"stage {stage!r} has {len(unparented)} work unit(s) with no parent-manifest "
                "hash, so their parent artifacts were never validated. Re-prepare with "
                "--parent-dir before spending production compute."
            )
    problems = _production_readiness_problems(plan)
    if problems:
        raise ContractError(
            "production prerequisites failed validation:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )
    return ProductionEvaluator(plan)


def command_evaluate(args: argparse.Namespace) -> int:
    """Own one shard for the full invocation, including crash reconciliation."""
    stage_dir = _stage_directory(args.stage_dir)
    if int(args.of) < 1 or not (0 <= int(args.shard) < int(args.of)):
        raise ContractError(
            f"shard index {args.shard} is out of range for --of {args.of}"
        )
    shard_label = f"shard_{args.shard:03d}_of_{args.of:03d}"
    shard_lock_path = stage_dir / f".{shard_label}.worker.lock"
    shard_lock = shard_lock_path.open("a")
    try:
        try:
            fcntl.flock(shard_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError(
                f"{shard_label} already has a live worker; refusing concurrent ownership"
            ) from exc
        return _command_evaluate_locked(args)
    finally:
        try:
            fcntl.flock(shard_lock.fileno(), fcntl.LOCK_UN)
        finally:
            shard_lock.close()


def _command_evaluate_locked(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    _validate_stage_artifacts(stage_dir, what="evaluate")
    manifest = _load_strict_json(stage_dir / "manifest.json")
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    _assert_plan_matches_manifest(plan, manifest, what="evaluate")
    acceptance_package_identity = _validate_frozen_acceptance_package(
        stage_dir, plan, what="evaluate", manifest=manifest
    )
    _validate_frozen_m1_prerequisites(
        stage_dir,
        plan=plan,
        manifest=manifest,
        acceptance_package_identity=acceptance_package_identity,
        what="evaluate",
    )
    shard_label = f"shard_{args.shard:03d}_of_{args.of:03d}"
    # We now own this shard for the full command lifetime.  Repair only its
    # possible unterminated crash tail, then convert any prior RUNNING event
    # whose final event lives in this shard journal to LOST.  Other shards may
    # still be healthy and are never touched.
    owned_journal = stage_dir / ne.Ledger.ATTEMPTS_DIR / f"{shard_label}.jsonl"
    owned_records = (
        ne.read_jsonl_records(owned_journal, repair_truncated_tail=True)
        if owned_journal.exists() else []
    )
    ledger = ne.Ledger.load(stage_dir)
    owned_attempt_ids = {str(row.get("attempt_id", "")) for row in owned_records}
    lost_ids = {
        attempt.attempt_id for attempt in ledger.running_attempts()
        if attempt.attempt_id in owned_attempt_ids
    }
    for lost in ledger.reconcile_running_attempts_as_lost(
        lost_attempt_ids=lost_ids,
        failure_reason="previous owner of this shard ended without a terminal event",
        failure_class="LostShardWorker",
    ):
        ledger.save_attempt_record(stage_dir, shard_label, lost)
    targets = plan.targets

    evaluate = resolve_evaluator(
        args.evaluator, plan,
        priced=bool(args.i_have_priced_the_budget), ledger=ledger,
    )

    # A worker evaluates FROZEN work. If the plan or the code it just loaded is
    # not the one the ledger was frozen against, the results would carry the
    # stale frozen hash and the merge would see one uniform, wrong version.
    # Refuse here rather than launder the hash.
    frozen_config = {u.config_sha256 for u in ledger.units}
    expected_config = _scientific_config_sha256(plan, _model_spaces(plan)[ledger.model])
    if len(frozen_config) == 1 and expected_config not in frozen_config:
        raise ContractError(
            f"this worker loaded run plan {plan.plan_sha256}, but the ledger was frozen "
            f"against {next(iter(frozen_config))}. The plan changed after prepare; "
            "re-prepare rather than evaluating frozen work against an edited plan."
        )
    frozen_code = {u.code_sha256 for u in ledger.units}
    current_code = _code_sha256()
    if len(frozen_code) == 1 and current_code not in frozen_code:
        raise ContractError(
            f"this worker is running code {current_code}, but the ledger was frozen "
            f"against {next(iter(frozen_code))}. Re-prepare rather than mixing versions."
        )

    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv").set_index("design_id")
    meta = ne.worker_metadata()

    # Dispatch accounting is a small transactional database rather than a full
    # ledger reload before every trajectory.  The old implementation was
    # globally correct only by reparsing every attempt journal for every unit,
    # which made a 1,000-unit synthetic confirmation stage quadratic.  The
    # database is still protected by the cross-process dispatch lock and is
    # reconciled from the append-only attempt journal whenever a worker starts.
    # Thus it is an acceleration index, not a second source of scientific truth.
    budget_state_path = stage_dir / "execution_budget.json"
    execution_layout_path = stage_dir / "execution_layout.json"
    dispatch_db_path = stage_dir / "execution_dispatch.sqlite3"
    dispatch_lock_path = stage_dir / ".dispatch.lock"
    declared_cap = float(plan.require("budget.wall_time_cap_seconds"))
    requested_cap = float(args.wall_time_cap_seconds) if args.wall_time_cap_seconds is not None else declared_cap
    if requested_cap > declared_cap:
        raise ContractError(
            f"a worker may shorten but not expand the signed wall-clock cap "
            f"({requested_cap} > {declared_cap})"
        )
    with dispatch_lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        expected_layout = {
            "schema": "robust_nroy_execution_layout/1.0.0",
            "n_shards": int(args.of),
            "ledger_frozen_sha256": ledger.frozen_sha256,
            "plan_sha256": plan.plan_sha256,
        }
        if execution_layout_path.exists():
            recorded_layout = _load_strict_json(execution_layout_path)
            if recorded_layout != expected_layout:
                raise ContractError(
                    "this stage already froze a different execution layout; "
                    f"recorded={recorded_layout}, requested={expected_layout}. "
                    "Re-prepare a new stage to change shard count."
                )
        else:
            ne.atomic_write_json(execution_layout_path, expected_layout)
        if budget_state_path.exists():
            budget_state = _load_strict_json(budget_state_path)
        else:
            budget_state = {
                "started_utc": time.time(),
                "wall_time_cap_seconds": declared_cap,
            }
            ne.atomic_write_json(budget_state_path, budget_state)
        # Reload while holding the global lock.  A ledger loaded before this
        # lock can be stale because another shard may have durably launched an
        # attempt in the meantime.  Rebuilding from that stale object would
        # erase the other shard's reservation and refund global budget.
        ledger = ne.Ledger.load(stage_dir)
        # SQLite is an acceleration cache, never evidence.  Rebuild it exactly
        # from the now-current durable journal every startup; this both repairs
        # corruption and prevents an internally valid but stale cache from
        # blocking retry or undercounting dispatch.
        ne.rebuild_dispatch_index(dispatch_db_path, ledger)
    assigned = ledger.rows_for_shard(args.shard, args.of)
    done = ledger.completed_keys()
    cap = ne.WallClockCap(
        cap_seconds=float(
            requested_cap
        ),
        started_utc=float(budget_state["started_utc"]),
    )

    results: list[ne.SeedResult] = []
    shared_values: dict[str, tuple[float, ...]] = {}
    shard_dir = stage_dir / "shards"
    durable_results_dir = stage_dir / "results"
    for prior in _read_shards(stage_dir, targets):
        try:
            ne.validate_durable_seed_result(ledger, prior, targets=targets)
        except ne.MergeRefused:
            # A crash after the result file but before the SUCCESS event leaves
            # the same attempt durably LOST.  It is not reusable, but its
            # exact frozen row may be overwritten by the paid retry.  Every
            # other mismatch remains a hard provenance failure.
            ne.validate_orphaned_lost_seed_result(
                ledger, prior, targets=targets,
            )
            continue
        key = ne.result_key(
            prior.stage, prior.model, prior.vector_hash, int(prior.seed)
        )
        values = shared_values.get(key)
        if values is not None and values != prior.values:
            raise ContractError(
                f"validated durable rows disagree for scientific result key {key}"
            )
        shared_values[key] = prior.values
    for position, unit in enumerate(assigned):
        if unit.key in done:
            continue  # a completed work unit is never rerun
        params = {
            name: float(vectors.loc[unit.design_id, name])
            for name in vectors.columns
        }
        if nc.vector_hash(params, names=tuple(vectors.columns)) != unit.vector_hash:
            raise ContractError(
                f"{unit.design_id}: the design vector does not match the ledger hash; "
                "refusing to evaluate a mutated design"
            )
        lock_path = dispatch_lock_path
        if unit.result_key in shared_values:
            values = shared_values[unit.result_key]
            with lock_path.open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                db = sqlite3.connect(dispatch_db_path)
                row = db.execute(
                    "SELECT attempt_count, completed FROM unit_state WHERE unit_key=?",
                    (unit.key_string,),
                ).fetchone()
                if row is not None and int(row[1]):
                    db.close()
                    continue
                attempt_count = int(row[0]) if row is not None else 0
                attempt_id = ne.new_attempt_id(unit.design_id, unit.seed, attempt_count + 1)
                linked_result = ne.SeedResult(
                    design_id=unit.design_id, seed=unit.seed, stage=unit.stage,
                    model=unit.model, vector_hash=unit.vector_hash,
                    target_order=targets.frozen_order, values=values,
                    attempt_id=attempt_id, code_sha256=current_code,
                    config_sha256=expected_config,
                    parent_manifest_sha256=unit.parent_manifest_sha256,
                )
                linked = ledger.record_attempt(ne.AttemptRecord(
                    attempt_id=attempt_id, design_id=unit.design_id, seed=unit.seed,
                    status=ne.ATTEMPT_LINKED, started_utc=time.time(), ended_utc=time.time(),
                    vector_hash=unit.vector_hash, result_hash=linked_result.result_hash, **meta,
                ))
                durable_results_dir.mkdir(parents=True, exist_ok=True)
                ne.atomic_write_json(
                    durable_results_dir / f"{ccs.sha256_json({'unit': unit.key_string})}.json",
                    linked_result.as_row(), overwrite_completed=True,
                )
                ledger.save_attempt_record(stage_dir, shard_label, linked)
                db.execute(
                    "INSERT INTO unit_state VALUES (?, ?, 1) "
                    "ON CONFLICT(unit_key) DO UPDATE SET attempt_count=excluded.attempt_count, completed=1",
                    (unit.key_string, attempt_count + 1),
                )
                db.commit()
                db.close()
            results.append(linked_result)
            continue
        with lock_path.open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            db = sqlite3.connect(dispatch_db_path)
            unit_row = db.execute(
                "SELECT attempt_count, completed FROM unit_state WHERE unit_key=?",
                (unit.key_string,),
            ).fetchone()
            result_row = db.execute(
                "SELECT consumed, active, successful FROM result_state WHERE result_key=?",
                (unit.result_key,),
            ).fetchone()
            if (unit_row is not None and int(unit_row[1])) or \
               (result_row is not None and int(result_row[1])):
                db.close()
                continue
            if result_row is not None and int(result_row[2]):
                # A successful shared result is being durably materialised by
                # the worker that owns this shard.  A duplicate invocation
                # must not dispatch it again; a later resume will link it from
                # the shard result table.
                db.close()
                continue
            if not cap.may_start():
                db.close()
                cap.record_withheld(
                    sum(1 for later in assigned[position:] if later.key not in done)
                )
                break
            consumed_attempts = int(db.execute(
                "SELECT consumed_attempts FROM budget WHERE singleton=1"
            ).fetchone()[0])
            consumed_trajectories = int(db.execute(
                "SELECT count(*) FROM result_state WHERE consumed=1"
            ).fetchone()[0])
            already_consumed = bool(result_row and int(result_row[0]))
            if consumed_attempts >= int(plan.require("budget.max_attempts")) or (
                not already_consumed
                and consumed_trajectories >= int(plan.require("budget.max_trajectories"))
            ):
                db.close()
                break
            attempt_count = int(unit_row[0]) if unit_row is not None else 0
            attempt_id = ne.new_attempt_id(unit.design_id, unit.seed, attempt_count + 1)
            started = time.time()
            attempt = ledger.record_attempt(ne.AttemptRecord(
                attempt_id=attempt_id, design_id=unit.design_id, seed=unit.seed,
                status=ne.ATTEMPT_RUNNING, started_utc=started,
                vector_hash=unit.vector_hash, **meta,
            ))
            ledger.save_attempt_record(stage_dir, shard_label, attempt)
            db.execute(
                "INSERT INTO unit_state VALUES (?, ?, 0) "
                "ON CONFLICT(unit_key) DO UPDATE SET attempt_count=excluded.attempt_count",
                (unit.key_string, attempt_count + 1),
            )
            db.execute(
                "INSERT INTO result_state VALUES (?, 1, 1, 0) "
                "ON CONFLICT(result_key) DO UPDATE SET consumed=1, active=1",
                (unit.result_key,),
            )
            db.execute(
                "UPDATE budget SET consumed_attempts=consumed_attempts+1 WHERE singleton=1"
            )
            db.commit()
            db.close()
        cap.record_dispatch()
        try:
            values = evaluate(unit, params, targets.frozen_order)
            result = ne.SeedResult(
                design_id=unit.design_id, seed=unit.seed, stage=unit.stage, model=unit.model,
                vector_hash=unit.vector_hash, target_order=targets.frozen_order,
                values=tuple(float(v) for v in values), attempt_id=attempt_id,
                code_sha256=current_code, config_sha256=expected_config,
                parent_manifest_sha256=unit.parent_manifest_sha256,
            )
            shared_values[unit.result_key] = result.values
            attempt.status = ne.ATTEMPT_SUCCESS
            attempt.ended_utc = time.time()
            attempt.result_hash = result.result_hash
            results.append(result)
            cap.record_completion()
            with lock_path.open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                durable_results_dir.mkdir(parents=True, exist_ok=True)
                ne.atomic_write_json(
                    durable_results_dir / f"{ccs.sha256_json({'unit': unit.key_string})}.json",
                    result.as_row(), overwrite_completed=True,
                )
                ledger.save_attempt_record(stage_dir, shard_label, attempt)
                db = sqlite3.connect(dispatch_db_path)
                db.execute(
                    "UPDATE result_state SET active=0, successful=1 WHERE result_key=?",
                    (unit.result_key,),
                )
                db.execute(
                    "UPDATE unit_state SET completed=1 WHERE unit_key=?",
                    (unit.key_string,),
                )
                db.commit()
                db.close()
        except ContractError as exc:
            attempt.status = ne.ATTEMPT_FAILED
            attempt.ended_utc = time.time()
            attempt.failure_reason = str(exc)
            attempt.failure_class = type(exc).__name__
            with lock_path.open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                ledger.save_attempt_record(stage_dir, shard_label, attempt)
                db = sqlite3.connect(dispatch_db_path)
                db.execute("UPDATE result_state SET active=0 WHERE result_key=?", (unit.result_key,))
                db.commit()
                db.close()
            raise
        except Exception as exc:  # noqa: BLE001 - the failure is data, not a crash
            attempt.status = ne.ATTEMPT_FAILED
            attempt.ended_utc = time.time()
            attempt.failure_reason = str(exc)
            attempt.failure_class = type(exc).__name__
            cap.record_completion()
            with lock_path.open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                ledger.save_attempt_record(stage_dir, shard_label, attempt)
                db = sqlite3.connect(dispatch_db_path)
                db.execute("UPDATE result_state SET active=0 WHERE result_key=?", (unit.result_key,))
                db.commit()
                db.close()

    shard_dir.mkdir(parents=True, exist_ok=True)
    shard_path = shard_dir / f"shard_{args.shard:03d}_of_{args.of:03d}.csv"
    old = ne.read_frame_exact(shard_path) if shard_path.exists() else pd.DataFrame()
    frame = pd.concat([old, pd.DataFrame([r.as_row() for r in results])], ignore_index=True)
    if not frame.empty:
        keys = ["stage", "model", "design_id", "seed"]
        duplicated = frame.duplicated(keys, keep=False)
        if duplicated.any():
            raise ContractError(
                "resume would persist duplicate scientific results for "
                f"{frame.loc[duplicated, keys].to_dict('records')[:3]}"
            )
    ne.atomic_write_frame(
        shard_path, frame,
        overwrite_completed=True,
    )
    # Only this shard's own attempt file. Rewriting the shared ledger here
    # would clobber the other workers' audit rows (and did).
    ledger.save_attempts(stage_dir, f"shard_{args.shard:03d}_of_{args.of:03d}")
    ne.atomic_write_json(
        stage_dir / f"budget_audit_shard_{args.shard:03d}_of_{args.of:03d}.json",
        cap.as_row(), overwrite_completed=True,
    )
    print(f"shard {args.shard}/{args.of}: {len(results)} result(s) from "
          f"{len(assigned)} assigned unit(s) [{args.evaluator}]")
    return 0


# ==========================================================================
# merge
# ==========================================================================

def _read_shards(stage_dir: Path, targets: nc.TargetContract) -> list[ne.SeedResult]:
    out: dict[tuple[str, str, str, int], ne.SeedResult] = {}
    shard_dir = stage_dir / "shards"

    def add(row: Mapping, *, source: Path) -> None:
        result = ne.seed_result_from_durable_row(
            row, targets=targets, source=source,
        )
        key = (result.stage, result.model, result.design_id, int(result.seed))
        prior = out.get(key)
        if prior is not None and prior.as_row() != result.as_row():
            raise ContractError(
                f"conflicting durable/shard result rows for frozen key {key}"
            )
        out[key] = result

    if shard_dir.exists():
        for path in sorted(shard_dir.glob("shard_*.csv")):
            frame = ne.read_frame_exact(path)
            for row in frame.to_dict("records"):
                add(row, source=path)
    durable = stage_dir / "results"
    if durable.exists():
        for path in sorted(durable.glob("*.json")):
            add(_load_strict_json(path), source=path)
    return list(out.values())


def _handoff_audit_for_complete_stage(
    stage_dir: Path, *, stage: str, plan: nc.FrozenPlan, scores: pd.DataFrame,
) -> pd.DataFrame:
    """Reconcile generated, dispatched, completed and selected candidates."""
    proposals = ne.read_frame_exact(stage_dir / "design_proposals.csv")
    kernel_of = dict(zip(
        proposals["design_id"].astype(str), proposals["kernel_id"].astype(str)
    ))

    def counts(frame: pd.DataFrame, mask=None) -> dict[str, int]:
        selected = frame if mask is None else frame.loc[mask]
        values = selected["design_id"].astype(str).map(kernel_of)
        if values.isna().any():
            raise ContractError("handoff audit found a score with no frozen proposal")
        return {str(k): int(v) for k, v in values.value_counts().items()}

    dispatched = {
        str(k): int(v) for k, v in proposals["kernel_id"].astype(str).value_counts().items()
    }
    proposed = dict(dispatched)
    rejected: dict[str, int] = {}
    quotas: dict[str, int] = {}
    if stage in (nc.STAGE_DISCOVERY, nc.STAGE_BRIDGES):
        outcomes = ne.read_frame_exact(stage_dir / "kernel_outcomes.csv")
        if not outcomes.empty:
            proposed = {
                str(k): int(v) for k, v in outcomes.groupby("kernel_id")["n_proposed"].sum().items()
            }
            rejected = {
                str(k): int(v) for k, v in outcomes.groupby("kernel_id")["n_rejected"].sum().items()
            }
            for kernel, n in dispatched.items():
                if kernel not in proposed:
                    proposed[kernel] = n
                    rejected[kernel] = 0
            quotas = {
                str(k): int(v)
                for k, v in outcomes.groupby("kernel_id")["quota"].sum().items()
            }
        if stage == nc.STAGE_DISCOVERY:
            n_anchors = len(ne.read_frame_exact(stage_dir / "anchors.csv"))
            declared = {
                kernel: quota * n_anchors
                for kernel, quota in plan.kernel_quotas().items()
            }
            mismatched_declared = {
                kernel: (int(quotas.get(kernel, -1)), int(quota))
                for kernel, quota in declared.items()
                if int(quotas.get(kernel, -1)) != int(quota)
            }
            if mismatched_declared:
                raise ContractError(
                    f"frozen kernel outcome quotas {quotas} do not match the signed "
                    f"discovery quotas {declared}: {mismatched_declared}"
                )
            # Keep additional model-specific frozen families (notably the M1
            # p-boundary probes) from their explicit kernel outcomes.
            quotas = {**declared, **quotas}
            # One exact anchor control is frozen per anchor but it is not a
            # stochastic design-kernel quota in the plan.
            quotas[nc.KERNEL_ANCHOR] = int(dispatched.get(nc.KERNEL_ANCHOR, 0))
    else:
        # Allocation-only stages freeze their exact selected set.  That set is
        # the quota; leaving quotas empty used to make every observed family
        # appear to have an implicit quota of zero.
        quotas = dict(dispatched)

    completed = counts(scores)
    if stage == nc.STAGE_DISCOVERY:
        promoted = counts(scores, scores["promoted"].astype(bool))
        confirmed: dict[str, int] = {}
    else:
        promoted = dict(dispatched)
        confirmed = counts(scores, scores["nominal_pass"].astype(bool))
    return ne.handoff_audit(
        proposed=proposed, rejected=rejected, dispatched=dispatched,
        completed=completed, promoted=promoted, confirmed=confirmed,
        quotas=quotas,
    )


def command_merge(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    existing_manifest = _load_strict_json(stage_dir / "manifest.json")
    if (stage_dir / "seed_results.csv").exists() or (stage_dir / "candidate_scores.csv").exists():
        _validate_stage_artifacts(
            stage_dir, what="repeat merge",
            validate_all_indexed=str(existing_manifest.get("status")) != nc.STATUS_PARTIAL,
            allow_partial_runtime_growth=(
                str(existing_manifest.get("status")) == nc.STATUS_PARTIAL
            ),
        )
        if str(existing_manifest.get("status")) != nc.STATUS_PARTIAL:
            raise ContractError(
                "this COMPLETE stage has already been merged. Merged scientific evidence "
                "is immutable; prepare a new stage instead of silently re-indexing it"
            )
    _validate_stage_artifacts(stage_dir, what="merge")
    manifest = existing_manifest
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    frozen_parent_for_plan = _frozen_parent_acceptance_identity(stage_dir)
    if frozen_parent_for_plan is not None:
        nc.assert_acceptance_identity(
            plan.plan_acceptance_fields(), frozen_parent_for_plan,
            what=(
                f"merge {stage_dir}: current plan against the acceptance-input "
                "identity frozen from the parent"
            ),
            fields=nc.PLAN_DERIVED_ACCEPTANCE_FIELDS,
        )
    _assert_plan_matches_manifest(plan, manifest, what="merge")
    package_identity = _validate_frozen_acceptance_package(
        stage_dir, plan, what="merge", manifest=manifest
    )
    frozen_parent_package = _frozen_parent_acceptance_package_identity(stage_dir)
    if frozen_parent_package is not None and nc.json_safe(
        frozen_parent_package
    ) != nc.json_safe(package_identity):
        raise ContractError(
            "merge: this stage's reviewed acceptance package differs from its parent"
        )
    ledger = ne.Ledger.load(stage_dir)

    results = _read_shards(stage_dir, plan.targets)
    resume = ne.plan_resume(
        ledger,
        max_attempts=int(plan.require("budget.max_attempts")),
        max_trajectories=int(plan.require("budget.max_trajectories")),
    )
    complete = resume.status == nc.STATUS_COMPLETE
    seed_frame = ne.merge_seed_results(
        ledger, results, targets=plan.targets, expect_complete=complete
    )
    # The numerator and denominator of every implausibility were reviewed and
    # sealed at prepare.  Merge consumes only those stage-local copies.  The
    # legacy CLI paths remain optional compatibility assertions for operators
    # migrating old scripts; they can never substitute new bytes.
    observed_path = stage_dir / nai.OBSERVED_FILENAME
    variance_path = stage_dir / nai.BASE_VARIANCE_FILENAME
    if args.observed:
        try:
            asserted_observed_hash = ccs.sha256_file(args.observed)
        except OSError as exc:
            raise ContractError(
                f"--observed compatibility assertion cannot be read: {exc}"
            ) from exc
        if asserted_observed_hash != package_identity["observed_sha256"]:
            raise ContractError(
                "observed_sha256 compatibility assertion failed: --observed contains "
                "different observed target values than the package sealed at prepare"
            )
    if args.base_variance:
        try:
            asserted_variance_hash = ccs.sha256_file(args.base_variance)
        except OSError as exc:
            raise ContractError(
                f"--base-variance compatibility assertion cannot be read: {exc}"
            ) from exc
        if asserted_variance_hash != package_identity["base_variance_sha256"]:
            raise ContractError(
                "base_variance_sha256 compatibility assertion failed: --base-variance "
                "contains different base variances than the package sealed at prepare"
            )
    observed = _load_strict_json(observed_path)
    base_variance = _load_strict_json(variance_path)
    plan.targets.assert_order(
        sorted(observed, key=lambda name: list(plan.targets.frozen_order).index(name))
        if set(observed) == set(plan.targets.frozen_order) else list(observed),
        what="sealed observed values",
    )
    plan.targets.assert_order(
        sorted(base_variance, key=lambda name: list(plan.targets.frozen_order).index(name))
        if set(base_variance) == set(plan.targets.frozen_order) else list(base_variance),
        what="sealed base variance",
    )
    bad_observed = sorted(
        k for k, v in observed.items() if not np.isfinite(float(v))
    )
    if bad_observed:
        raise ContractError(
            f"sealed observed values must be finite; non-finite for {bad_observed}"
        )
    bad = sorted(
        k for k, v in base_variance.items()
        if not np.isfinite(float(v)) or not float(v) > 0.0
    )
    if bad:
        raise ContractError(
            f"sealed base variance must be strictly positive; non-positive for {bad}"
        )
    target_inputs_sha256 = {
        "observed": package_identity["observed_sha256"],
        "base_variance": package_identity["base_variance_sha256"],
    }
    # The complete acceptance-input identity, and the comparison that closes
    # the cross-stage hole: a child merged against different observed values or
    # different base variances than its confirmation parent would publish
    # support labels and edge verdicts on a different acceptance basis, and
    # nothing used to notice. The parent's identity was frozen at prepare.
    identity = dict(package_identity["acceptance_input_identity"])
    frozen_parent = frozen_parent_for_plan
    if frozen_parent is not None:
        nc.assert_acceptance_identity(
            frozen_parent, identity,
            what=f"merge {stage_dir}: these acceptance inputs against the frozen parent",
        )
    scoring_note = ""
    if seed_frame.empty:
        # No result exists to score. That is only ever legitimate for a stage
        # that did not complete -- a wall-clock cap or an exhausted budget --
        # and such a stage is PARTIAL and cannot become a parent. A COMPLETE
        # stage must carry acceptance arithmetic, so refuse that combination
        # rather than publish a parentable stage with no scores.
        if complete:
            raise ContractError(
                "the merge found no seed-level rows yet the ledger reports every unit "
                "complete; refusing to publish a COMPLETE stage with no acceptance "
                "arithmetic"
            )
        scoring_note = (
            "no seed-level rows exist, so no candidate could be scored. The stage is "
            f"{resume.status} and cannot become a parent. Every work unit is "
            "'work_not_run', which is execution bookkeeping, not a finding."
        )
        print(f"no scores recomputed: {scoring_note}")
        ne.atomic_write_frame(
            stage_dir / "seed_results.csv", seed_frame, overwrite_completed=True
        )
    elif not complete:
        scoring_note = (
            f"{len(seed_frame)} seed-level row(s) are retained for audit, but the stage is "
            f"{resume.status}; partial work is never scored or classified"
        )
        print(f"no scores recomputed: {scoring_note}")
        ne.atomic_write_frame(
            stage_dir / "seed_results.csv", seed_frame, overwrite_completed=True
        )
    else:
        scores = ne.recompute_candidate_scores(
            seed_frame, targets=plan.targets, observed=observed,
            base_variance=base_variance, cutoff=plan.cutoff,
            variance_alpha=float(plan.require("promotion.variance_alpha")),
        )
        handoff = _handoff_audit_for_complete_stage(
            stage_dir, stage=str(manifest["stage"]), plan=plan, scores=scores,
        )
        ne.atomic_write_frame(
            stage_dir / "seed_results.csv", seed_frame, overwrite_completed=True
        )
        ne.atomic_write_frame(
            stage_dir / "kernel_audit.csv", handoff, overwrite_completed=True
        )
        ne.atomic_write_frame(
            stage_dir / "candidate_scores.csv", scores, overwrite_completed=True
        )
        print(f"recomputed scores for {len(scores)} candidate(s) from seed-level rows")

    ne.write_stage_manifest(
        stage_dir, stage=manifest["stage"], model=manifest["model"],
        status=resume.status, ledger=ledger, contract=plan.manifest_contract(),
        parent_manifest=None,
        parent_manifest_sha256=str(manifest.get("parent_manifest_sha256") or "") or None,
        extra={
            **(manifest.get("extra") or {}),
            "resume": resume.as_row(),
            "target_inputs_sha256": target_inputs_sha256,
            "acceptance_input_identity": identity,
            **({"scores_not_recomputed_reason": scoring_note} if scoring_note else {}),
        },
        overwrite_completed=True,
    )
    print(f"merged {len(seed_frame)} seed-level row(s); stage status {resume.status}")
    if resume.status != nc.STATUS_COMPLETE:
        print(f"  {resume.reason}")
        print("  a PARTIAL stage cannot become a parent for the next stage")
    return 0


# ==========================================================================
# identity-gate: the M0/M1 pre-production command
# ==========================================================================

def command_identity_gate(args: argparse.Namespace) -> int:
    """Run the M0/M1-at-``p=0`` identity gate and write its artifact.

    **This command runs the engine on purpose.**  It is the one place in this
    stage that does, and it is not a pytest: the identity is a production
    prerequisite, and every pytest that checks it is skipped under the
    repository's no-engine guard, so a green full suite does not establish it.

    The runs are short truncated trajectories (about a second each), not
    production simulations: no calibration, no scenario and no PSA work is
    dispatched, and nothing is written to ``outputs/``.
    """
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = nc.FrozenPlan.load(args.plan)
    adapter = ProductionEvaluator(plan)
    spaces = _model_spaces(plan)
    shared_names = spaces[nc.MODEL_M0].names
    p_name = str(plan.require("p_parameter.name"))

    routed_vectors: dict[str, dict] = {}

    def routed_config(model_name: str, factor: float, rho: float, p: float) -> dict:
        """Build gate inputs through the exact production injection route."""
        m0 = spaces[nc.MODEL_M0]
        u = np.full(len(m0.names), 0.5, dtype=float)
        # The historical multiplier values become deterministic locations on
        # one genuine calibrated coordinate, keeping every probe in bounds.
        u[0] = min(0.95, max(0.05, 0.5 + (float(factor) - 1.0)))
        m0_values = m0.denormalise(u)
        m0_params = {name: float(value) for name, value in zip(shared_names, m0_values)}
        if model_name == nc.MODEL_M0:
            model = m0
            params = m0_params
        else:
            model = spaces[nc.MODEL_M1]
            params = nc.embed_m0_in_m1(m0, model, m0_params, p_value=float(p))
        vector_hash = nc.vector_hash(params, names=model.names)
        unit = ne.WorkUnit(
            stage=nc.STAGE_DISCOVERY, model=model_name,
            design_id=f"identity/{model_name}/f{factor:g}/p{p:g}/rho{rho:g}",
            seed=int(args.seeds[0]), vector_hash=vector_hash,
            kernel=nc.KERNEL_ANCHOR, parent_ids=("identity-gate",), params=params,
            parent_manifest_sha256="", code_sha256=_code_sha256(),
            config_sha256=_scientific_config_sha256(plan, model),
            target_schema=plan.targets.frozen_order,
        )
        config = adapter.config_for(unit, params)
        if model_name == nc.MODEL_M1 and float(rho) != 1.0:
            node = config
            for key in nig.PROFILE_PATH:
                node = node[key]
            node["routine_hazard_scale"] = {"value": float(rho)}
        config["run_control"]["end_year"] = {"value": int(args.end_year)}
        config["run_control"]["deterministic_mode"] = {"value": False}
        routed_vectors[unit.design_id] = {
            "model": model_name, "vector_hash": vector_hash,
            "params": params, "p": float(p), "rho": float(rho),
        }
        return config

    print("M0/M1 identity gate -- this runs the real engine on short truncated runs.")
    print(f"  base config: {plan.require('models.base_config')}  end year: {args.end_year}")
    print(f"  seeds:       {list(args.seeds)}")
    print()
    artifact = nig.run_gate(
        base_config=str(plan.require("models.base_config")),
        end_year=int(args.end_year),
        seeds=[int(s) for s in args.seeds],
        config_factory=routed_config,
        route_evidence={
            "plan_sha256": plan.plan_sha256,
            "scientific_config_sha256": _scientific_config_sha256(
                plan, spaces[nc.MODEL_M1]
            ),
            "target_register_sha256": ccs.sha256_file(
                (ROOT / plan.targets.register_path)
                if not Path(plan.targets.register_path).is_absolute()
                else Path(plan.targets.register_path)
            ),
            "vectors": routed_vectors,
        },
    )
    path = ne.atomic_write_json(
        out / nig.GATE_FILENAME, artifact, overwrite_completed=True
    )
    for check in artifact["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"  [{status}] {check['check']}: {check['detail']}")
    print()
    print(f"wrote {path}")
    if artifact["passed"]:
        print(f"GATE PASSED ({artifact['n_passed']}/{artifact['n_checks']} checks) in "
              f"{artifact['elapsed_seconds']:.1f}s")
        print("This is a prerequisite for launching M1, not an authorisation to run one.")
        return 0
    print(f"GATE FAILED ({artifact['n_passed']}/{artifact['n_checks']} checks)")
    print("No M1 design may be prepared.")
    return 5


# ==========================================================================
# classify: local support and p coverage
# ==========================================================================

def _stage_context(
    args: argparse.Namespace,
) -> tuple[Path, dict, nc.FrozenPlan, nc.ModelSpace, ne.Ledger]:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = _load_strict_json(stage_dir / "manifest.json")
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    model = _model_spaces(plan)[str(manifest["model"])]
    return stage_dir, manifest, plan, model, ne.Ledger.load(stage_dir)


def _scores_if_present(stage_dir: Path) -> pd.DataFrame | None:
    path = stage_dir / "candidate_scores.csv"
    return ne.read_frame_exact(path) if path.exists() else None


def command_classify(args: argparse.Namespace) -> int:
    """Turn merged rows into local-support labels and the ``p`` profile.

    Runs after ``merge`` on a local-design stage.  It writes
    ``design_evidence.csv``, ``local_support.csv`` and, for M1,
    ``p_coverage.csv``.  No simulation, no engine: this is classification of
    results that already exist.
    """
    stage_dir = _stage_directory(args.stage_dir)
    _validate_stage_artifacts(
        stage_dir, what="classification", validate_all_indexed=True,
    )
    manifest = _load_strict_json(stage_dir / "manifest.json")
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    model = _model_spaces(plan)[str(manifest["model"])]
    identity = _assert_acceptance_basis(
        stage_dir, manifest, plan, what="classification"
    )
    _assert_plan_matches_manifest(plan, manifest, what="classification")
    ledger = ne.Ledger.load(stage_dir)
    if str(manifest.get("stage")) == nc.STAGE_PROMOTION:
        raise ContractError(
            "promotion is an allocation-only handoff and has no fresh seed evidence to "
            "classify; prepare and evaluate the confirmation stage instead"
        )
    if str(manifest.get("status")) != nc.STATUS_COMPLETE or ledger.incomplete_units():
        raise ContractError(
            f"classification requires a COMPLETE stage with no incomplete work; got "
            f"status={manifest.get('status')!r}, incomplete={len(ledger.incomplete_units())}"
        )
    scores = _scores_if_present(stage_dir)
    if scores is None:
        raise ContractError(
            f"{stage_dir} has no candidate_scores.csv, so no acceptance decision exists "
            "to classify. Run 'merge' so it scores the sealed acceptance inputs first."
        )
    # Before any label is written: these scores must have been produced on the
    # same acceptance basis as the parent they descend from, and on the one the
    # current plan describes.
    # A classification is a completed artifact, and a child stage may already
    # have validated this stage's artifact index against it. Silently
    # reclassifying would leave that child's recorded parent hash describing a
    # file that no longer exists, which is the stale-parent case the handoff
    # contract exists to prevent. Refuse unless the caller says so explicitly.
    if (stage_dir / "local_support.csv").exists() and not args.reclassify:
        raise ContractError(
            f"{stage_dir} already carries local_support.csv. A classification is a "
            "completed artifact, and a child stage may already have validated this "
            "stage's artifact index against it. Pass --reclassify to replace it "
            "deliberately, and re-prepare any stage that already descends from this one."
        )
    evidence = nas.evidence_by_design(ledger, scores)
    ne.atomic_write_frame(
        stage_dir / "design_evidence.csv", nas.evidence_table(evidence),
        overwrite_completed=True,
    )

    anchors = ne.read_frame_exact(stage_dir / "anchors.csv")
    proposals = ne.read_frame_exact(stage_dir / "design_proposals.csv")
    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv")
    outcomes_path = stage_dir / "kernel_outcomes.csv"
    outcomes = ne.read_frame_exact(outcomes_path) if outcomes_path.exists() else None

    support = nas.local_support_table(
        anchors=anchors, proposals=proposals, design_vectors=vectors,
        kernel_outcomes=outcomes, evidence=evidence, model=model,
        smaller_ambient_scale=plan.smaller_ambient_scale(),
        min_confirming=int(plan.require("support.min_confirming_neighbours")),
        min_direction_classes=int(plan.require("separation.min_direction_classes")),
        max_abs_direction_cosine=float(plan.require(
            "separation.max_abs_direction_cosine"
        )),
        scores=scores,
    )
    controls = proposals[proposals["kernel_id"].astype(str) == nc.KERNEL_ANCHOR]
    score_pass = {
        str(row["design_id"]): bool(row["nominal_pass"])
        for _, row in scores.iterrows()
    }
    score_binding = {
        str(row["design_id"]): str(row.get("binding_target", ""))
        for _, row in scores.iterrows()
    }
    control_for = {
        str(row["parent_ids"]).split("|")[0]: str(row["design_id"])
        for _, row in controls.iterrows()
    }
    support["endpoint_confirmation_design_id"] = support["candidate_id"].astype(str).map(control_for).fillna("")
    support["endpoint_confirmed"] = support["endpoint_confirmation_design_id"].map(
        lambda design_id: bool(score_pass.get(str(design_id), False))
    )
    support["endpoint_binding_target"] = support["endpoint_confirmation_design_id"].map(
        lambda design_id: score_binding.get(str(design_id), "")
    )
    failed_endpoint = ~support["endpoint_confirmed"].astype(bool)
    support.loc[failed_endpoint, "local_support_label"] = nc.LABEL_UNRESOLVED
    support.loc[failed_endpoint, "eligible_for_representation"] = False
    support.loc[failed_endpoint, "reason"] = (
        "the anchor itself did not nominally confirm on the reserved seed block; "
        "neighbour evidence cannot make an unconfirmed endpoint eligible"
    )
    ne.atomic_write_frame(
        stage_dir / "local_support.csv", support, overwrite_completed=True
    )

    written = ["design_evidence.csv", "local_support.csv"]
    if model.p_name is not None:
        coverage = nas.p_coverage_from_tables(
            bins=[float(b) for b in plan.require("p_parameter.bins")],
            design_vectors=vectors, evidence=evidence, model=model,
        )
        ne.atomic_write_frame(
            stage_dir / "p_coverage.csv", coverage, overwrite_completed=True
        )
        written.append("p_coverage.csv")

    ne.write_stage_manifest(
        stage_dir, stage=manifest["stage"], model=manifest["model"],
        status=str(manifest["status"]), ledger=ledger,
        contract=plan.manifest_contract(), parent_manifest=None,
        parent_manifest_sha256=str(manifest.get("parent_manifest_sha256") or "") or None,
        extra={
            **(manifest.get("extra") or {}),
            "acceptance_input_identity": identity,
            "local_support_counts": {
                label: int((support["local_support_label"] == label).sum())
                for label in nc.LOCAL_SUPPORT_LABELS
            },
            "n_eligible_for_representation": int(
                support["eligible_for_representation"].astype(bool).sum()
            ),
        },
        overwrite_completed=True,
    )
    print(f"classified {len(support)} anchor(s) in {stage_dir}")
    for label in nc.LOCAL_SUPPORT_LABELS:
        print(f"  {label}: {int((support['local_support_label'] == label).sum())}")
    print(f"  wrote: {', '.join(written)}")
    print("\nThese are forecast-ensemble eligibility labels, not epidemiological "
          "acceptance, and 'no local support detected' is a statement about the "
          "declared finite design.")
    return 0


# ==========================================================================
# regions: edge verdicts, components, representatives
# ==========================================================================

def command_regions(args: argparse.Namespace) -> int:
    """Evaluate the frozen bridges and select representatives on geometry.

    Reads a **completed** bridges stage, labels each frozen minimal edge from
    its bridge probes' evidence, groups the nodes by sampled-path-supported
    edges only, and selects representatives from eligible members using
    normalised geometry alone.  Writes its own regions stage directory with a
    validated parent, so lineage runs unbroken from discovery to regions.

    Runs no simulation: every number comes from the bridges stage's own
    consolidated tables.
    """
    parent_dir = _stage_directory(args.parent_dir)
    parent_manifest = _load_strict_json(parent_dir / "manifest.json")
    plan = nc.FrozenPlan.load(args.plan or parent_manifest["contract"]["plan_path"])
    validated_acceptance = _validate_declared_acceptance_package(plan)
    model = _model_spaces(plan)[str(parent_manifest["model"])]

    validated = ne.validate_parent_stage(
        parent_dir, expected_stage=nc.STAGE_BRIDGES, expected_model=model.model,
        targets=plan.targets, seeds=plan.seeds, child_stage=nc.STAGE_REGIONS,
    )
    identity = _assert_acceptance_basis(
        parent_dir, validated, plan, what="edge verdicts"
    )
    _assert_plan_matches_manifest(plan, validated, what="regions parent")
    ledger = ne.Ledger.load(parent_dir)
    scores = _scores_if_present(parent_dir)
    if scores is None:
        raise ContractError(
            f"{parent_dir} has no candidate_scores.csv; bridge probes must be merged and "
            "scored before any edge can be labelled"
        )
    # Before the first edge verdict: the bridges stage must have been scored on
    # the same acceptance basis as the confirmation stage whose local assessment
    # the graph's nodes carry. Otherwise an edge could be "sampled-path
    # supported" under one acceptance rule while its endpoints were assessed
    # under another, and the region report would mix the two silently.
    nodes = ne.read_frame_exact(parent_dir / "graph_nodes.csv")
    edges_frame = ne.read_frame_exact(parent_dir / "candidate_edges.csv")
    proposals = ne.read_frame_exact(parent_dir / "design_proposals.csv")

    evidence = nas.evidence_by_design(ledger, scores)
    minimal = nas.minimal_edges_from_table(edges_frame)
    verdicts = nas.edge_verdict_table(
        edges=minimal, nodes=nodes, proposals=proposals, evidence=evidence,
        n_declared=len(plan.bridge_fractions()),
        pass_rule=str(plan.require("graph.bridge_pass_rule")),
    )
    node_ids = sorted(str(value) for value in nodes["candidate_id"])
    groups = ng.components(node_ids, verdicts)
    # Decision 7 and spec sec. 1 both read "local/REGION support". Region
    # support is what the graph exists to establish, so eligibility is
    # local-OR-region and the ground is recorded per candidate rather than
    # collapsed into a bare flag.
    eligible, ground = nas.representation_eligibility(nodes, groups)
    representatives = ng.select_representatives(
        groups, nas.node_geometry(nodes),
        n_per_component=int(plan.require("representative_selection.n_per_component")),
        rule=str(plan.require("representative_selection.rule")),
        eligible=eligible,
    )
    if not representatives.empty:
        representatives["eligibility_ground"] = [
            ground.get(str(value), "") if pd.notna(value) else ""
            for value in representatives["candidate_id"]
        ]
    local_only = nas.node_eligibility(nodes)
    region_only = nas.region_support(nodes, groups)
    labels = {
        str(row["candidate_id"]): str(row["local_support_label"])
        for _, row in nodes.iterrows()
    }
    eligibility = pd.DataFrame([
        {
            "candidate_id": node,
            "local_support_label": labels[node],
            "has_local_support": bool(local_only.get(node, False)),
            "has_region_support": bool(region_only.get(node, False)),
            "eligible_for_representation": bool(eligible[node]),
            "eligibility_ground": ground[node],
        }
        for node in sorted(eligible)
    ])
    blocking = ng.unresolved_changes_representation(groups, verdicts)
    region_status = nc.STATUS_PARTIAL if blocking else nc.STATUS_COMPLETE

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    acceptance_package_identity = _freeze_acceptance_package(
        out, validated_acceptance
    )
    tables = ng.graph_tables(nas.edges_from_table(edges_frame), minimal, verdicts, groups)
    ne.atomic_write_frame(out / "candidate_edges.csv", tables["candidate_edges"])
    ne.atomic_write_frame(out / "edge_verdicts.csv", tables["edge_verdicts"])
    ne.atomic_write_frame(out / "components.csv", tables["components"])
    if blocking:
        provisional = representatives.copy()
        provisional["provisional_reason"] = (
            "representation-changing unresolved edge(s); not a final shortlist"
        )
        ne.atomic_write_frame(out / "provisional_representatives.csv", provisional)
    else:
        ne.atomic_write_frame(out / "representatives.csv", representatives)
    ne.atomic_write_frame(out / "representation_eligibility.csv", eligibility)
    ne.atomic_write_frame(out / "graph_nodes.csv", nodes)
    anchors_path = parent_dir / "anchors.csv"
    if anchors_path.exists():
        ne.atomic_write_frame(out / "anchors.csv", ne.read_frame_exact(anchors_path))
    # Carried forward unchanged, not rebuilt: the regions package must report
    # the same local assessment the graph was built from.
    support_path = parent_dir / "local_support.csv"
    if support_path.exists():
        ne.atomic_write_frame(
            out / "local_support.csv", ne.read_frame_exact(support_path)
        )
    ne.atomic_write_json(out / "unresolved_blocking_edges.json", {
        "edge_ids": blocking,
        "meaning": (
            "unresolved edges that would merge two components if resolved, and therefore "
            "the only ones that can change which groups receive representatives"
        ),
        "escalation": (
            "these are the recorded unresolved geometry questions that justify a bounded "
            "archive-informed path search, and the only trigger for considering an "
            "emulator (spec sec. 4 and sec. 6). An emulator may never rule out a region "
            "or declare plausibility without simulator evaluation."
        ),
    })

    # A regions stage runs no simulation, so it has an empty frozen work list.
    # PARTIAL here means scientific representation is unresolved, not that a
    # simulation work unit is missing.
    empty = ne.Ledger(stage=nc.STAGE_REGIONS, model=model.model, units=())
    empty.save(out)
    _freeze_parent_acceptance_identity(
        out, validated, parent_dir=parent_dir,
        current_package_identity=acceptance_package_identity,
    )
    ne.write_stage_manifest(
        out, stage=nc.STAGE_REGIONS, model=model.model, status=region_status,
        ledger=empty, contract=plan.manifest_contract(),
        parent_manifest=validated,
        extra={
            "acceptance_input_identity": identity,
            "acceptance_package_identity": acceptance_package_identity,
            "n_nodes": len(node_ids),
            "n_minimal_edges": len(minimal),
            "n_sampled_path_supported_edges": sum(1 for v in verdicts if v.supported),
            "n_unresolved_edges": sum(1 for v in verdicts if not v.supported),
            "n_components": len(groups),
            "n_representatives": (
                int(representatives["candidate_id"].notna().sum())
                if not blocking and not representatives.empty else 0
            ),
            "n_provisional_representatives": (
                int(representatives["candidate_id"].notna().sum())
                if blocking and not representatives.empty else 0
            ),
            "representative_status": (
                "PROVISIONAL_UNRESOLVED" if blocking else "FINAL_FOR_THIS_STAGE"
            ),
            "eligibility_grounds": {
                ground_name: int((eligibility["eligibility_ground"] == ground_name).sum())
                for ground_name in sorted(set(eligibility["eligibility_ground"]))
            },
            "eligibility_rule": (
                "local OR region support, as production decision 7 and spec sec. 1 both "
                "state ('local/region support'). Region support means membership of a "
                "component held together by at least one sampled-path-supported edge; a "
                "singleton has none, so its only route is its own local support."
            ),
            "unresolved_edges_that_would_change_representation": blocking,
            "limitation": (
                "'sampled-path supported' is discrete evidence at the declared "
                "interpolation fractions; it is not continuous connectivity, and a "
                "component here is not a topological component of the NROY set"
            ),
        },
    )
    print(f"REGIONS {model.model} in {out}")
    print(f"  nodes:                {len(node_ids)}")
    print(f"  minimal edges:        {len(minimal)}")
    print(f"  sampled-path supported: {sum(1 for v in verdicts if v.supported)}")
    print(f"  unresolved edges:     {sum(1 for v in verdicts if not v.supported)}")
    print(f"  components:           {len(groups)}")
    if blocking:
        print(f"  {len(blocking)} unresolved edge(s) WOULD change representation: {blocking}")
        print("  regions status is PARTIAL; only provisional representatives were written")
        print("  that is a recorded unresolved geometry question (spec sec. 4)")
    return 0 if not blocking else 3


# ==========================================================================
# diagnose: the secondary correlated-output diagnostic (spec sec. 7)
# ==========================================================================

def command_diagnose(args: argparse.Namespace) -> int:
    """Run the correlated-output diagnostic.  It cannot change acceptance.

    Disabled unless the plan switches it on, and its numbers become required
    decisions exactly when it is switched on
    (:data:`nroy_contract.CONDITIONAL_DECISIONS`).  The result is written to
    its own file and is never merged into ``candidate_scores``.
    """
    stage_dir = _stage_directory(args.stage_dir)
    _validate_stage_artifacts(
        stage_dir, what="correlated-output diagnosis", validate_all_indexed=True,
    )
    stage_dir, manifest, plan, _model, _ledger = _stage_context(args)
    _assert_plan_matches_manifest(plan, manifest, what="correlated-output diagnosis")
    if not bool(plan.get("correlated_output_diagnostic.enabled", False)):
        raise ContractError(
            "correlated_output_diagnostic.enabled is false in the run plan. The diagnostic "
            "is secondary and off by default; enabling it also makes its shrinkage, "
            "bootstrap and stability decisions required, so it cannot run on defaults."
        )
    seed_path = stage_dir / "seed_results.csv"
    if not seed_path.exists():
        raise ContractError(
            f"{stage_dir} has no seed_results.csv; the diagnostic uses confirmation "
            "replicates and will not be reconstructed from candidate summaries"
        )
    diagnostic = nas.correlated_output_diagnostic(
        ne.read_frame_exact(seed_path),
        targets=plan.targets,
        shrinkage=float(plan.require("correlated_output.shrinkage")),
        n_bootstrap=int(plan.require("correlated_output.n_seed_block_bootstrap")),
        bootstrap_seed=int(plan.require("correlated_output.bootstrap_seed")),
        rank_tolerance=float(plan.require("correlated_output.stability_rank_tolerance")),
        direction_cosine=float(plan.require("correlated_output.stability_direction_cosine")),
        scores=_scores_if_present(stage_dir),
    )
    payload = {
        **diagnostic.as_dict(),
        "stage": manifest["stage"],
        "model": manifest["model"],
        "leading_directions": [list(d) for d in diagnostic.leading_directions],
        "target_order": list(plan.targets.frozen_order),
    }
    ne.atomic_write_json(
        stage_dir / "correlated_output_diagnostic.json", payload,
        overwrite_completed=True,
    )
    ne.write_stage_manifest(
        stage_dir, stage=str(manifest["stage"]), model=str(manifest["model"]),
        status=str(manifest["status"]), ledger=_ledger,
        contract=plan.manifest_contract(), parent_manifest=None,
        parent_manifest_sha256=str(manifest.get("parent_manifest_sha256") or "") or None,
        extra=manifest.get("extra") or {}, overwrite_completed=True,
    )
    print(f"correlated-output diagnostic: {diagnostic.status}")
    print(f"  {diagnostic.reason}")
    print(f"  seed blocks (effective stochastic sample size): {diagnostic.n_seed_blocks}")
    print(f"  effective rank: {diagnostic.effective_rank}")
    print(f"  rank correlation with pointwise I_max: "
          f"{diagnostic.rank_correlation_with_i_max}")
    print("\nSecondary by construction: this diagnostic carries no pass flag and cannot "
          "change the primary set.")
    return 0 if diagnostic.status == nas.DIAGNOSTIC_AVAILABLE else 4


# ==========================================================================
# report
# ==========================================================================

def command_report(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    _validate_stage_artifacts(
        stage_dir, what="report", validate_all_indexed=True,
    )
    manifest = _load_strict_json(stage_dir / "manifest.json")
    plan_path = args.plan or manifest["contract"]["plan_path"]
    plan = nc.FrozenPlan.load(plan_path)
    _assert_plan_matches_manifest(plan, manifest, what="report")
    unsigned = [item.path for item in nc.unsigned_values(plan.raw)]

    tables: dict[str, pd.DataFrame] = {}
    for name in (
        "design_proposals", "seed_results", "candidate_scores", "local_support",
        "candidate_edges", "edge_verdicts", "components", "representatives",
        "kernel_audit", "ledger_units", "ledger_attempts", "p_coverage",
        "design_evidence", "graph_nodes",
        "anchors", "design_vectors",
    ):
        path = stage_dir / f"{name}.csv"
        if path.exists():
            frame = ne.read_frame_exact(path)
            if not frame.empty or name in ("ledger_units", "ledger_attempts"):
                tables[name] = frame

    facts = nrp.summary_facts(
        tables, status=str(manifest["status"]), model=str(manifest["model"]),
        manifest=manifest,
    )
    model = _model_spaces(plan)[str(manifest["model"])]
    parameter_frame = tables.get("anchors")
    binding_targets = None
    scores = tables.get("candidate_scores")
    if scores is not None and "binding_target" in scores.columns:
        binding_targets = {
            str(row["design_id"]): str(row["binding_target"])
            for _, row in scores.iterrows()
        }
        proposals = tables.get("design_proposals")
        if proposals is not None:
            controls = proposals[
                proposals["kernel_id"].astype(str) == nc.KERNEL_ANCHOR
            ]
            for _, row in controls.iterrows():
                design_id = str(row["design_id"])
                parent = str(row["parent_ids"]).split("|")[0]
                if design_id in binding_targets:
                    binding_targets[parent] = binding_targets[design_id]
    support = tables.get("local_support")
    if support is not None and "endpoint_binding_target" in support.columns:
        if binding_targets is None:
            binding_targets = {}
        binding_targets.update({
            str(row["candidate_id"]): str(row["endpoint_binding_target"])
            for _, row in support.iterrows()
            if str(row.get("endpoint_binding_target", ""))
        })
    written = nrp.write_package(
        stage_dir / "package", tables, facts=facts, manifest=manifest,
        question=QUESTION, unsigned=unsigned,
        kernel_audit=tables.get("kernel_audit"),
        resume=(manifest.get("extra") or {}).get("resume"),
        recomputation_verified="candidate_scores" in tables,
        parameter_frame=parameter_frame,
        parameter_names=model.names,
        binding_targets=binding_targets,
        overwrite_completed=True,
    )
    print(f"wrote {len(written)} artifact(s) to {stage_dir / 'package'}")
    for name in sorted(written):
        print(f"  {name}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    _validate_stage_artifacts(stage_dir, what="resume")
    manifest = _load_strict_json(stage_dir / "manifest.json")
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    _assert_plan_matches_manifest(plan, manifest, what="resume")
    ledger = ne.Ledger.load(stage_dir)
    decision = ne.plan_resume(
        ledger,
        max_attempts=int(plan.require("budget.max_attempts")),
        max_trajectories=int(plan.require("budget.max_trajectories")),
    )
    for key, value in decision.as_row().items():
        print(f"  {key}: {value}")
    return 0 if decision.status == nc.STATUS_COMPLETE else 3


# ==========================================================================
# CLI
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    default_plan = "configs/robust_nroy_freeze_production.json"

    p = sub.add_parser("validate", help="dry-run validation; never simulates")
    p.add_argument("--plan", default=default_plan)
    p.set_defaults(func=command_validate)

    p = sub.add_parser("prepare", help="freeze the design and the ledger")
    p.add_argument("--plan", default=default_plan)
    p.add_argument("--out", required=True)
    p.add_argument("--anchors", default="",
                   help="CSV of complete joint confirmed vectors; required for every "
                        "discovery stage (including independent strictly-positive-p M1 "
                        "anchors) except 'bridges', whose node set comes from its parent")
    p.add_argument("--archive", default="",
                   help="CSV of complete joint archive vectors (with an optional 'j' "
                        "column) for the declared low-J correlation-following source")
    p.add_argument("--model", choices=list(nc.MODELS), default=nc.MODEL_M0)
    p.add_argument("--stage", choices=list(nc.STAGE_ORDER), default=nc.STAGE_DISCOVERY)
    p.add_argument("--parent-dir", default="",
                   help="the COMPLETE parent stage directory; required for any stage "
                        "after 'prepare'")
    p.add_argument("--parent-manifest-sha256", default="",
                   help="optional: assert the validated parent's manifest hash")
    p.add_argument("--identity-gate", default="",
                   help=f"optional compatibility assertion: must be the exact "
                        f"production_prerequisites.identity_gate path; M1 consumes "
                        f"and seals the signed {nig.GATE_FILENAME}")
    p.add_argument("--m0-confirmed-dir", default="",
                   help="COMPLETE M0 confirmation stage; required for M1 discovery so "
                        "every confirmed M0 vector is embedded at exact p=0")
    p.add_argument(
        "--m1-anchor-evidence", default="",
        help=(
            f"optional compatibility assertion: must be the exact signed "
            "production_prerequisites.m1_anchor_confirmation path containing typed "
            f"{M1_ANCHOR_EVIDENCE_SCHEMA} evidence"
        ),
    )
    p.set_defaults(func=command_prepare)

    p = sub.add_parser(
        "identity-gate",
        help="run the M0/M1-at-p=0 identity gate (RUNS THE ENGINE; required before M1)",
        description=(
            "RUNS THE ENGINE on short, bounded identity trajectories; this is a "
            "pre-production gate, not a production calibration run"
        ),
    )
    p.add_argument("--out", required=True, help="directory to write the gate artifact")
    p.add_argument(
        "--plan", required=True,
        help="signed plan whose ProductionEvaluator.config_for route the gate exercises",
    )
    p.add_argument("--end-year", type=int, default=nig.DEFAULT_END_YEAR)
    p.add_argument("--seeds", type=int, nargs="+", default=list(nig.DEFAULT_SEEDS))
    p.set_defaults(func=command_identity_gate)

    p = sub.add_parser("evaluate", help="worker: evaluate frozen rows of one shard")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--of", type=int, required=True)
    p.add_argument("--evaluator", choices=sorted(EVALUATORS), required=True)
    p.add_argument("--i-have-priced-the-budget", action="store_true")
    p.add_argument("--wall-time-cap-seconds", type=float, default=None,
                   help="override the plan's declared wall-clock cap for this worker; "
                        "the cap stops dispatch and lets in-flight work finish")
    p.set_defaults(func=command_evaluate)

    p = sub.add_parser("merge", help="validate and consolidate shard outputs")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument(
        "--observed", default=None,
        help="optional compatibility assertion: must hash-match the observed values "
             "sealed into the stage at prepare",
    )
    p.add_argument(
        "--base-variance", default=None,
        help="optional compatibility assertion: must hash-match frozen B_i sealed into "
             "the stage at prepare",
    )
    p.set_defaults(func=command_merge)

    p = sub.add_parser("classify", help="local-support labels and the p coverage profile")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--reclassify", action="store_true",
                   help="deliberately replace an existing classification; any stage "
                        "already descending from this one must be re-prepared")
    p.set_defaults(func=command_classify)

    p = sub.add_parser("regions", help="edge verdicts, components and representatives")
    p.add_argument("--plan", default=None)
    p.add_argument("--parent-dir", required=True,
                   help="the COMPLETE bridges stage directory")
    p.add_argument("--out", required=True, help="the regions stage directory to write")
    p.set_defaults(func=command_regions)

    p = sub.add_parser(
        "diagnose",
        help="secondary correlated-output diagnostic; cannot change acceptance",
    )
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_diagnose)

    p = sub.add_parser("report", help="write the human-readable artifact package")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_report)

    p = sub.add_parser("resume", help="report what a resume may relaunch")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_resume)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except nc.UnsignedConfigError as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    except ContractError as exc:
        print(f"CONTRACT ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
