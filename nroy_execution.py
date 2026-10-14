#!/usr/bin/env python3
"""Ledger, sharding, merge gate and resume for the robust-NROY freeze stage.

Implements section 5 of ``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md``: distributed
execution must be scientifically identical to a single-process reference run.

The division of labour is the whole point and is enforced structurally:

* The **coordinator** freezes every work unit and writes the complete ledger
  *before* any dispatch.
* A **worker** may evaluate only ledger rows assigned to its shard.  It cannot
  generate a proposal, substitute a failure, infer a default or touch another
  shard's rows -- :func:`shard_of` is the only assignment function and
  :meth:`Ledger.rows_for_shard` the only way to obtain work.
* The **merger** independently recomputes every candidate statistic from
  seed-level rows.  It never reads a shard's own summary.

Two distinctions that the specification insists on, and that this module keeps
mechanically separate:

* a **failed attempt** is audit history, never a scientific result;
* a **partial stage** can never become a parent.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import hm_implausibility as hm
import nroy_contract as nc
from coupled_calibration_search import ContractError

LEDGER_SCHEMA_VERSION = "nroy_ledger/1.0.0"

# Attempt/row statuses.
ATTEMPT_PENDING = "pending"
ATTEMPT_RUNNING = "running"
ATTEMPT_SUCCESS = "success"
ATTEMPT_FAILED = "failed"
ATTEMPT_LOST = "lost"
ATTEMPT_STATUSES = (
    ATTEMPT_PENDING, ATTEMPT_RUNNING, ATTEMPT_SUCCESS, ATTEMPT_FAILED, ATTEMPT_LOST,
)
#: Statuses that consumed a launch attempt and therefore consumed budget.
ATTEMPT_CONSUMED = frozenset({ATTEMPT_SUCCESS, ATTEMPT_FAILED, ATTEMPT_LOST, ATTEMPT_RUNNING})


# ==========================================================================
# Atomic output
# ==========================================================================

def atomic_write_text(path: str | Path, text: str, *, overwrite_completed: bool = False) -> Path:
    """Write ``text`` to ``path`` atomically.

    The temporary file is created in the destination directory so the final
    ``os.replace`` is a same-filesystem rename, which is atomic: a reader
    never sees a half-written artifact, and a crash mid-write leaves the
    previous complete file intact.

    ``overwrite_completed`` must be set explicitly to replace an existing file.
    The default refusal is what stops a resumed run from overwriting a
    completed artifact (spec sec. 5 "Crash/resume").
    """
    target = Path(path)
    if target.exists() and not overwrite_completed:
        raise ContractError(
            f"refusing to overwrite existing artifact {target}; completed artifacts are "
            "immutable and a resume may never replace one"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def atomic_write_json(path: str | Path, payload, *, overwrite_completed: bool = False) -> Path:
    return atomic_write_text(
        path,
        json.dumps(nc.json_safe(payload), indent=1, sort_keys=True),
        overwrite_completed=overwrite_completed,
    )


def atomic_write_frame(
    path: str | Path, frame: pd.DataFrame, *, overwrite_completed: bool = False
) -> Path:
    return atomic_write_text(
        path, frame.to_csv(index=False), overwrite_completed=overwrite_completed
    )


def read_frame_exact(path: str | Path, **kwargs) -> pd.DataFrame:
    """Read a CSV without perturbing floats or turning ``""`` into ``NaN``.

    Two pandas defaults are actively dangerous for this stage:

    * ``read_csv`` uses a fast float converter that is **not** exact.  It can
      return a value one ulp away from what was written, which silently
      changes every vector hash computed from the restored frame.
      ``float_precision="round_trip"`` is required, not optional.
    * ``keep_default_na`` turns an empty string into ``NaN``, so a blank
      provenance field round-trips as the literal text ``"nan"`` and changes a
      hashed field.

    Every read of a frozen vector, ledger or seed-result table must go through
    this function.
    """
    target = Path(path)
    # A frame with no rows AND no columns serialises to an empty file, which
    # `read_csv` refuses outright. That happens legitimately -- a worker that
    # the wall-clock cap stopped before its first unit produces no results --
    # and "the shard ran and produced nothing" must be readable, not a crash.
    if target.exists() and not target.read_text().strip():
        return pd.DataFrame()
    options = {"float_precision": "round_trip", "keep_default_na": False}
    options.update(kwargs)
    return pd.read_csv(target, **options)


# ==========================================================================
# Work units
# ==========================================================================

def result_key(stage: str, model: str, vector_hash: str, seed: int) -> str:
    """Identity of one *scientific* result.

    Keyed by the vector, not by the design that proposed it.  Two local
    assessments that happen to propose the same vector therefore link to one
    result rather than paying for two identical simulations (spec sec. 5
    "Frozen work units").
    """
    payload = f"{stage}|{model}|{vector_hash}|{int(seed)}"
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class WorkUnit:
    """One frozen scientific work unit, keyed by ``(stage, model, design_id, seed)``."""

    stage: str
    model: str
    design_id: str
    seed: int
    vector_hash: str
    kernel: str
    parent_ids: tuple[str, ...]
    params: dict[str, float]
    parent_manifest_sha256: str
    code_sha256: str
    config_sha256: str
    target_schema: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.stage not in nc.STAGE_ORDER:
            raise ContractError(f"unknown stage {self.stage!r}")
        if self.model not in nc.MODELS:
            raise ContractError(f"unknown model {self.model!r}")
        if not self.design_id:
            raise ContractError("work unit has an empty design_id")
        if not self.kernel:
            raise ContractError(f"work unit {self.design_id} has a null kernel_id")
        if not self.parent_ids:
            raise ContractError(f"work unit {self.design_id} has no parent lineage")
        if not self.target_schema:
            raise ContractError(f"work unit {self.design_id} declares no target schema")

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.stage, self.model, self.design_id, int(self.seed))

    @property
    def key_string(self) -> str:
        return f"{self.stage}|{self.model}|{self.design_id}|{int(self.seed)}"

    @property
    def result_key(self) -> str:
        return result_key(self.stage, self.model, self.vector_hash, self.seed)

    def as_row(self) -> dict:
        return {
            "stage": self.stage,
            "model": self.model,
            "design_id": self.design_id,
            "seed": int(self.seed),
            "result_key": self.result_key,
            "vector_hash": self.vector_hash,
            "kernel_id": self.kernel,
            "parent_ids": "|".join(self.parent_ids),
            "n_parents": len(self.parent_ids),
            "parent_manifest_sha256": self.parent_manifest_sha256,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
            "target_schema": "|".join(self.target_schema),
        }


def shard_of(key_string: str, n_shards: int) -> int:
    """Deterministic shard assignment.

    A pure function of the frozen work-unit key and the declared shard count.
    It does **not** depend on worker count, dispatch order, arrival order or
    how many units exist, so changing the number of workers provably cannot
    change which parameters or seeds get evaluated (spec sec. 5).
    """
    count = int(n_shards)
    if count < 1:
        raise ContractError(f"shard count must be >= 1, got {n_shards!r}")
    digest = hashlib.sha256(key_string.encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


# ==========================================================================
# The ledger
# ==========================================================================

@dataclass
class AttemptRecord:
    """One execution attempt.  Failed attempts stay in the audit trail."""

    attempt_id: str
    design_id: str
    seed: int
    status: str
    started_utc: float | None = None
    ended_utc: float | None = None
    host: str = ""
    process: str = ""
    vector_hash: str = ""
    result_hash: str = ""
    failure_reason: str = ""
    failure_class: str = ""

    def __post_init__(self) -> None:
        if self.status not in ATTEMPT_STATUSES:
            raise ContractError(f"unknown attempt status {self.status!r}")

    def as_row(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "design_id": self.design_id,
            "seed": int(self.seed),
            "status": self.status,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "host": self.host,
            "process": self.process,
            "vector_hash": self.vector_hash,
            "result_hash": self.result_hash,
            "failure_reason": self.failure_reason,
            "failure_class": self.failure_class,
        }


def new_attempt_id(design_id: str, seed: int, ordinal: int) -> str:
    """A fresh attempt id for the same frozen scientific key.

    A retry changes only this id.  The vector and the seed are part of the key
    and cannot change (spec sec. 5 "Crash/resume").
    """
    return f"{design_id}#s{int(seed)}#a{int(ordinal):03d}"


@dataclass
class Ledger:
    """The immutable frozen work list, plus its mutable attempt audit.

    The *units* are frozen at preparation time and never change.  Only the
    attempt history grows.  That split is what makes a resume safe: restoring
    the ledger restores the science exactly, and replaying attempts cannot
    alter a single parameter.
    """

    stage: str
    model: str
    units: tuple[WorkUnit, ...]
    attempts: list[AttemptRecord] = field(default_factory=list)
    frozen_sha256: str = ""
    design_rng_state: dict = field(default_factory=dict)
    #: Attempts recorded by THIS process, as opposed to those restored from
    #: disk.  Only these are written to this worker's own attempt file.
    recorded_this_process: list[AttemptRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: dict[tuple[str, str, str, int], WorkUnit] = {}
        design_to_vector: dict[str, str] = {}
        for unit in self.units:
            if unit.stage != self.stage or unit.model != self.model:
                raise ContractError(
                    f"work unit {unit.design_id} belongs to {unit.stage}/{unit.model}, "
                    f"ledger is {self.stage}/{self.model}"
                )
            if unit.key in seen:
                raise ContractError(f"duplicate work-unit key: {unit.key_string}")
            seen[unit.key] = unit
            # A design_id must resolve to exactly one vector hash (spec sec. 5).
            previous = design_to_vector.setdefault(unit.design_id, unit.vector_hash)
            if previous != unit.vector_hash:
                raise ContractError(
                    f"design_id {unit.design_id!r} resolves to two different vectors "
                    f"({previous} and {unit.vector_hash})"
                )
        if not self.frozen_sha256:
            self.frozen_sha256 = self.compute_frozen_hash()

    def compute_frozen_hash(self) -> str:
        return ccs.sha256_json({
            "schema": LEDGER_SCHEMA_VERSION,
            "stage": self.stage,
            "model": self.model,
            "units": [unit.as_row() for unit in sorted(self.units, key=lambda u: u.key_string)],
        })

    @property
    def keys(self) -> set[tuple[str, str, str, int]]:
        return {unit.key for unit in self.units}

    def by_key(self) -> dict[tuple[str, str, str, int], WorkUnit]:
        return {unit.key: unit for unit in self.units}

    def unique_result_keys(self) -> dict[str, list[WorkUnit]]:
        """``result_key -> work units sharing it``.

        Several design ids may share one result key when they propose the same
        vector at the same seed.  Such a vector is simulated once; the other
        units link to that single scientific result.
        """
        out: dict[str, list[WorkUnit]] = {}
        for unit in self.units:
            out.setdefault(unit.result_key, []).append(unit)
        return out

    def rows_for_shard(self, shard_index: int, n_shards: int) -> list[WorkUnit]:
        """The only legitimate source of work for a worker.

        Sharding is applied to the *result* key, so two design ids sharing one
        vector land in the same shard and the duplicate is never simulated
        twice by two different workers.
        """
        count, index = int(n_shards), int(shard_index)
        if not (0 <= index < count):
            raise ContractError(f"shard index {index} out of range for {count} shards")
        groups = self.unique_result_keys()
        out: list[WorkUnit] = []
        for key in sorted(groups):
            if shard_of(key, count) == index:
                out.extend(sorted(groups[key], key=lambda u: u.key_string))
        return out

    def assert_shard_partition(self, n_shards: int) -> None:
        """Shards must partition the ledger: no gaps, no overlaps."""
        count = int(n_shards)
        covered: list[tuple[str, str, str, int]] = []
        for index in range(count):
            covered.extend(unit.key for unit in self.rows_for_shard(index, count))
        if len(covered) != len(set(covered)):
            raise ContractError(f"shard partition into {count} shards overlaps")
        if set(covered) != self.keys:
            missing = sorted(self.keys - set(covered))
            raise ContractError(f"shard partition into {count} shards misses {len(missing)} units")

    def attempts_for(self, design_id: str, seed: int) -> list[AttemptRecord]:
        return [
            a for a in self.attempts
            if a.design_id == design_id and int(a.seed) == int(seed)
        ]

    def next_attempt_id(self, design_id: str, seed: int) -> str:
        return new_attempt_id(design_id, seed, len(self.attempts_for(design_id, seed)) + 1)

    def consumed_attempts(self) -> int:
        """Launch attempts consume budget, including the ones that were lost."""
        return sum(1 for a in self.attempts if a.status in ATTEMPT_CONSUMED)

    def record_attempt(self, attempt: AttemptRecord) -> AttemptRecord:
        unit_keys = {(u.design_id, int(u.seed)) for u in self.units}
        if (attempt.design_id, int(attempt.seed)) not in unit_keys:
            raise ContractError(
                f"attempt {attempt.attempt_id} does not correspond to any frozen work unit; "
                "a worker may not invent work"
            )
        if any(a.attempt_id == attempt.attempt_id for a in self.attempts):
            raise ContractError(f"duplicate attempt_id {attempt.attempt_id}")
        if attempt.status == ATTEMPT_SUCCESS:
            already = [
                a for a in self.attempts_for(attempt.design_id, int(attempt.seed))
                if a.status == ATTEMPT_SUCCESS
            ]
            if already:
                raise ContractError(
                    f"work unit {attempt.design_id} seed {attempt.seed} already has a successful "
                    f"result ({already[0].attempt_id}); a completed unit is never rerun"
                )
        self.attempts.append(attempt)
        self.recorded_this_process.append(attempt)
        return attempt

    def completed_keys(self) -> set[tuple[str, str, str, int]]:
        return {
            (self.stage, self.model, a.design_id, int(a.seed))
            for a in self.attempts if a.status == ATTEMPT_SUCCESS
        }

    def incomplete_units(self) -> list[WorkUnit]:
        done = self.completed_keys()
        return [unit for unit in self.units if unit.key not in done]

    def is_complete(self) -> bool:
        return not self.incomplete_units()

    def attempt_frame(self) -> pd.DataFrame:
        if not self.attempts:
            return pd.DataFrame(columns=list(AttemptRecord(
                attempt_id="", design_id="", seed=0, status=ATTEMPT_PENDING).as_row()))
        return pd.DataFrame([a.as_row() for a in self.attempts])

    def unit_frame(self) -> pd.DataFrame:
        return pd.DataFrame([unit.as_row() for unit in self.units])

    # ---- persistence -------------------------------------------------

    #: Subdirectory holding one append-only attempt file per shard.
    ATTEMPTS_DIR = "attempts"

    def save_attempts(self, directory: str | Path, shard_label: str) -> Path:
        """Write THIS worker's attempts to its own file.

        Concurrent workers must never share one attempt file. Each loads the
        ledger, appends in memory, and a single shared ``ledger_attempts.csv``
        would then be overwritten from whichever stale copy saved last --
        silently destroying the other shards' audit rows. One file per shard is
        lock-free and append-only in the only sense that matters here: no
        worker ever rewrites another's rows.
        """
        base = Path(directory) / self.ATTEMPTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        mine = list(self.recorded_this_process)
        frame = (
            pd.DataFrame([a.as_row() for a in mine]) if mine else self.attempt_frame().iloc[0:0]
        )
        return atomic_write_frame(
            base / f"{shard_label}.csv", frame, overwrite_completed=True
        )

    def save(self, directory: str | Path, *, overwrite_completed: bool = False) -> dict:
        base = Path(directory)
        paths = {
            "ledger_units": atomic_write_frame(
                base / "ledger_units.csv", self.unit_frame(),
                overwrite_completed=overwrite_completed),
            "ledger_attempts": atomic_write_frame(
                base / "ledger_attempts.csv", self.attempt_frame(),
                overwrite_completed=True),  # single-process view only
            "ledger_meta": atomic_write_json(
                base / "ledger_meta.json",
                {
                    "schema": LEDGER_SCHEMA_VERSION,
                    "stage": self.stage,
                    "model": self.model,
                    "n_units": len(self.units),
                    "n_unique_result_keys": len(self.unique_result_keys()),
                    "frozen_sha256": self.frozen_sha256,
                    "design_rng_state": self.design_rng_state,
                },
                overwrite_completed=overwrite_completed),
        }
        return {name: str(path) for name, path in paths.items()}

    @classmethod
    def load(cls, directory: str | Path) -> "Ledger":
        """Restore a frozen ledger.  Never regenerates proposals."""
        base = Path(directory)
        meta = json.loads((base / "ledger_meta.json").read_text())
        # keep_default_na=False: an empty string must round-trip as "", not as
        # NaN. Otherwise str(NaN) == "nan" silently changes a hashed field and
        # the frozen-hash check fails on a ledger nobody touched.
        units_frame = read_frame_exact(base / "ledger_units.csv")
        units: list[WorkUnit] = []
        for _, row in units_frame.iterrows():
            schema = tuple(str(row["target_schema"]).split("|"))
            units.append(WorkUnit(
                stage=str(row["stage"]),
                model=str(row["model"]),
                design_id=str(row["design_id"]),
                seed=int(row["seed"]),
                vector_hash=str(row["vector_hash"]),
                kernel=str(row["kernel_id"]),
                parent_ids=tuple(str(row["parent_ids"]).split("|")),
                params={},  # vectors live in the design table, not the ledger index
                parent_manifest_sha256=str(row["parent_manifest_sha256"]),
                code_sha256=str(row["code_sha256"]),
                config_sha256=str(row["config_sha256"]),
                target_schema=schema,
            ))
        attempts: list[AttemptRecord] = []
        seen_ids: set[str] = set()
        # The shared file is the single-process view; attempts/*.csv are the
        # per-shard trails. Union them, de-duplicating by attempt_id, so a
        # distributed run reconstructs the COMPLETE audit history.
        sources = [base / "ledger_attempts.csv"]
        sources += sorted((base / cls.ATTEMPTS_DIR).glob("*.csv")) \
            if (base / cls.ATTEMPTS_DIR).exists() else []
        for attempts_path in sources:
            if not attempts_path.exists():
                continue
            frame = read_frame_exact(attempts_path)
            for _, row in frame.iterrows():
                if not str(row.get("attempt_id", "")):
                    continue
                if str(row["attempt_id"]) in seen_ids:
                    continue
                seen_ids.add(str(row["attempt_id"]))
                attempts.append(AttemptRecord(
                    attempt_id=str(row["attempt_id"]),
                    design_id=str(row["design_id"]),
                    seed=int(row["seed"]),
                    status=str(row["status"]),
                    started_utc=_opt_float(row.get("started_utc")),
                    ended_utc=_opt_float(row.get("ended_utc")),
                    host=str(row.get("host", "") or ""),
                    process=str(row.get("process", "") or ""),
                    vector_hash=str(row.get("vector_hash", "") or ""),
                    result_hash=str(row.get("result_hash", "") or ""),
                    failure_reason=str(row.get("failure_reason", "") or ""),
                    failure_class=str(row.get("failure_class", "") or ""),
                ))
        ledger = cls(
            stage=str(meta["stage"]),
            model=str(meta["model"]),
            units=tuple(units),
            attempts=attempts,
            frozen_sha256=str(meta["frozen_sha256"]),
            design_rng_state=dict(meta.get("design_rng_state") or {}),
        )
        recomputed = ledger.compute_frozen_hash()
        if recomputed != str(meta["frozen_sha256"]):
            raise ContractError(
                "restored ledger does not match its frozen hash "
                f"(recomputed {recomputed}, manifest {meta['frozen_sha256']}); "
                "the frozen work list was modified"
            )
        return ledger


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(number) else number


def worker_metadata() -> dict:
    return {
        "host": socket.gethostname(),
        "process": f"{platform.python_implementation()}-{os.getpid()}",
    }


# ==========================================================================
# Seed-level results
# ==========================================================================

@dataclass(frozen=True)
class SeedResult:
    """One worker's scientific output for one work unit at one seed."""

    design_id: str
    seed: int
    stage: str
    model: str
    vector_hash: str
    target_order: tuple[str, ...]
    values: tuple[float, ...]
    attempt_id: str
    code_sha256: str
    config_sha256: str
    parent_manifest_sha256: str = ""

    def __post_init__(self) -> None:
        if len(self.values) != len(self.target_order):
            raise ContractError(
                f"{self.design_id}: {len(self.values)} values for "
                f"{len(self.target_order)} targets"
            )

    @property
    def result_hash(self) -> str:
        return ccs.sha256_json({
            "design_id": self.design_id,
            "seed": int(self.seed),
            "target_order": list(self.target_order),
            "values": [nc.canonical_float(v) for v in self.values],
        })

    def as_row(self) -> dict:
        row = {
            "design_id": self.design_id,
            "seed": int(self.seed),
            "stage": self.stage,
            "model": self.model,
            "vector_hash": self.vector_hash,
            "attempt_id": self.attempt_id,
            "result_hash": self.result_hash,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
            "parent_manifest_sha256": self.parent_manifest_sha256,
        }
        for name, value in zip(self.target_order, self.values):
            row[f"target:{name}"] = float(value)
        return row


# ==========================================================================
# Merge gate
# ==========================================================================

class MergeRefused(ContractError):
    """The merge gate refused the shard outputs.  Never downgraded to a warning."""


def merge_seed_results(
    ledger: Ledger,
    results: Iterable[SeedResult],
    *,
    targets: nc.TargetContract,
    expect_complete: bool = True,
) -> pd.DataFrame:
    """Validate and consolidate seed-level rows (spec sec. 5 "Merge gate").

    Refuses, in this order: unknown keys, duplicates, parameter/seed/schema or
    hash mismatches, mixed code/config versions, reordered targets, and
    incomplete rows presented as results.  Arrival order is irrelevant: the
    output is sorted by the frozen key, so a shuffled merge is bit-identical.
    """
    units = ledger.by_key()
    collected: dict[tuple[str, str, str, int], SeedResult] = {}
    problems: list[str] = []
    code_versions: set[str] = set()
    config_versions: set[str] = set()

    for result in results:
        key = (result.stage, result.model, result.design_id, int(result.seed))
        unit = units.get(key)
        if unit is None:
            problems.append(f"extra ledger key not in the frozen work list: {key}")
            continue
        if key in collected:
            # Duplicates are refused even when the values agree (spec sec. 5).
            problems.append(f"duplicate key {key} (refused even if values agree)")
            continue
        if result.vector_hash != unit.vector_hash:
            problems.append(
                f"{key}: parameter hash mismatch (result {result.vector_hash}, "
                f"ledger {unit.vector_hash})"
            )
            continue
        if tuple(result.target_order) != targets.frozen_order:
            if set(result.target_order) == set(targets.frozen_order):
                problems.append(f"{key}: targets are silently reordered")
            else:
                problems.append(f"{key}: target schema does not match the contract")
            continue
        if tuple(unit.target_schema) != targets.frozen_order:
            problems.append(f"{key}: ledger row target schema does not match the contract")
            continue
        if not np.all(np.isfinite(np.asarray(result.values, dtype=float))):
            problems.append(
                f"{key}: non-finite outputs presented as a scientific result"
            )
            continue
        # Stale-hash rows must fail closed (spec sec. 9). Checking only that
        # the shards agree with EACH OTHER would accept a uniformly stale run,
        # so each row is compared against its own frozen ledger row.
        if result.code_sha256 != unit.code_sha256:
            problems.append(
                f"{key}: stale code hash (result {result.code_sha256}, "
                f"ledger {unit.code_sha256})"
            )
            continue
        if result.config_sha256 != unit.config_sha256:
            problems.append(
                f"{key}: stale config hash (result {result.config_sha256}, "
                f"ledger {unit.config_sha256})"
            )
            continue
        if (
            result.parent_manifest_sha256
            and result.parent_manifest_sha256 != unit.parent_manifest_sha256
        ):
            problems.append(
                f"{key}: parent-manifest hash mismatch (result "
                f"{result.parent_manifest_sha256}, ledger {unit.parent_manifest_sha256})"
            )
            continue
        code_versions.add(result.code_sha256)
        config_versions.add(result.config_sha256)
        collected[key] = result

    if len(code_versions) > 1:
        problems.append(f"mixed code versions across shards: {sorted(code_versions)}")
    if len(config_versions) > 1:
        problems.append(f"mixed config versions across shards: {sorted(config_versions)}")

    successful = {
        (ledger.stage, ledger.model, a.design_id, int(a.seed))
        for a in ledger.attempts if a.status == ATTEMPT_SUCCESS
    }
    for key in sorted(collected):
        if key not in successful:
            problems.append(
                f"{key}: a result was supplied but no successful attempt is recorded; "
                "a failed or in-flight attempt may not masquerade as a result"
            )

    if expect_complete:
        missing = sorted(ledger.keys - set(collected))
        if missing:
            problems.append(
                f"{len(missing)} ledger key(s) have no result, e.g. {missing[:3]}; "
                "a missing shard is not zero output and not a rejected candidate"
            )

    if problems:
        raise MergeRefused(
            "merge refused:\n" + "\n".join(f"  - {p}" for p in problems)
        )

    rows = [collected[key].as_row() for key in sorted(collected, key=lambda k: (k[2], k[3]))]
    frame = pd.DataFrame(rows)
    # Deterministic column order: identity columns, then targets in frozen order.
    identity = [
        "design_id", "seed", "stage", "model", "vector_hash", "attempt_id",
        "result_hash", "code_sha256", "config_sha256", "parent_manifest_sha256",
    ]
    ordered = identity + [f"target:{name}" for name in targets.frozen_order]
    return frame.reindex(columns=ordered).sort_values(["design_id", "seed"]).reset_index(drop=True)


# ==========================================================================
# Independent recomputation
# ==========================================================================

def recompute_candidate_scores(
    seed_frame: pd.DataFrame,
    *,
    targets: nc.TargetContract,
    observed: Mapping[str, float],
    base_variance: Mapping[str, float],
    cutoff: float,
    variance_alpha: float | None = None,
) -> pd.DataFrame:
    """Recompute means, variances, implausibility and promotion from seed rows.

    The merger never trusts a shard's summary (spec sec. 5): every number here
    is derived from the seed-level table.  Scoring reuses
    :mod:`hm_implausibility` so this stage cannot drift from the calibration's
    own acceptance arithmetic.

    Arithmetic order is fixed by sorting on ``(design_id, seed)`` before any
    reduction, which is what makes a sharded merge bit-identical to a
    single-process one.
    """
    if seed_frame.empty:
        raise ContractError("no seed-level rows to recompute")
    columns = [f"target:{name}" for name in targets.frozen_order]
    missing = [c for c in columns if c not in seed_frame.columns]
    if missing:
        raise ContractError(f"seed table is missing target columns: {missing}")

    frame = seed_frame.sort_values(["design_id", "seed"]).reset_index(drop=True)
    design_ids = sorted(frame["design_id"].unique())

    means: dict[str, list[float]] = {name: [] for name in targets.frozen_order}
    sample_var: dict[str, list[float]] = {name: [] for name in targets.frozen_order}
    replicate_counts: list[int] = []
    vector_hashes: list[str] = []

    for design_id in design_ids:
        block = frame[frame["design_id"] == design_id].sort_values("seed")
        k = int(len(block))
        if k < 2:
            raise ContractError(
                f"{design_id}: {k} replicate(s); a predictive variance needs at least 2"
            )
        hashes = set(block["vector_hash"].astype(str))
        if len(hashes) != 1:
            raise ContractError(f"{design_id}: seed rows disagree on the vector hash: {hashes}")
        vector_hashes.append(next(iter(hashes)))
        replicate_counts.append(k)
        for name in targets.frozen_order:
            series = block[f"target:{name}"].to_numpy(dtype=float)
            means[name].append(float(series.mean()))
            # ddof=1: the sample variance of the replicates, matching the
            # repository's s^2 convention for V_ensemble.
            sample_var[name].append(float(series.var(ddof=1)))

    k_values = set(replicate_counts)
    if len(k_values) != 1:
        raise ContractError(
            f"candidates have differing replicate counts {sorted(k_values)}; the confirmation "
            "block must be common to anchor and neighbours"
        )
    k = int(next(iter(k_values)))

    nominal = {
        name: np.asarray([
            float(base_variance[name]) + sample_var[name][i] * (1.0 + 1.0 / k)
            for i in range(len(design_ids))
        ])
        for name in targets.frozen_order
    }
    matrix, names = hm.implausibility_matrix_pointwise(
        {name: np.asarray(means[name]) for name in targets.frozen_order},
        dict(observed),
        nominal,
        target_order=list(targets.frozen_order),
    )
    targets.assert_order(names, what="recomputed implausibility matrix")
    combined = hm.combine(matrix, names)

    out = pd.DataFrame({
        "design_id": design_ids,
        "vector_hash": vector_hashes,
        "n_replicates": replicate_counts,
        "i_max": combined.i_max,
        "i_second": combined.i_second,
        "i_third": combined.i_third,
        "binding_target": combined.binding_target,
    })
    out["nominal_pass"] = combined.nroy_mask(cutoff=float(cutoff), rule="max")

    for name in targets.frozen_order:
        out[f"mean:{name}"] = means[name]
        out[f"var:{name}"] = sample_var[name]

    if variance_alpha is not None:
        factor = nc.variance_bound_factor(k, float(variance_alpha))
        bounded = {
            name: np.asarray([
                float(base_variance[name]) + sample_var[name][i] * factor * (1.0 + 1.0 / k)
                for i in range(len(design_ids))
            ])
            for name in targets.frozen_order
        }
        bound_matrix, bound_names = hm.implausibility_matrix_pointwise(
            {name: np.asarray(means[name]) for name in targets.frozen_order},
            dict(observed),
            bounded,
            target_order=list(targets.frozen_order),
        )
        bound_combined = hm.combine(bound_matrix, bound_names)
        out["i_max_variance_bound"] = bound_combined.i_max
        out["bound_pass"] = bound_combined.nroy_mask(cutoff=float(cutoff), rule="max")
        out["variance_bound_factor"] = factor
        out["promoted"] = out["nominal_pass"] | out["bound_pass"]
        out["variance_unstable"] = out["bound_pass"] & ~out["nominal_pass"]

    return out.sort_values("design_id").reset_index(drop=True)


# ==========================================================================
# Stage handoff
# ==========================================================================

def write_stage_manifest(
    directory: str | Path,
    *,
    stage: str,
    model: str,
    status: str,
    ledger: Ledger,
    contract: Mapping,
    parent_manifest: Mapping | None,
    extra: Mapping | None = None,
    overwrite_completed: bool = False,
) -> dict:
    """Write the completion manifest and artifact index for one stage."""
    if status not in nc.ALL_STATUSES:
        raise ContractError(f"unknown stage status {status!r}")
    base = Path(directory)
    manifest = {
        "schema": LEDGER_SCHEMA_VERSION,
        "stage": stage,
        "model": model,
        "status": status,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_units": len(ledger.units),
        "n_unique_result_keys": len(ledger.unique_result_keys()),
        "n_completed": len(ledger.completed_keys()),
        "n_incomplete": len(ledger.incomplete_units()),
        "consumed_attempts": ledger.consumed_attempts(),
        "ledger_frozen_sha256": ledger.frozen_sha256,
        "parent_manifest_sha256": (
            ccs.sha256_json(nc.json_safe(parent_manifest)) if parent_manifest else None
        ),
        "parent_stage": nc.STAGE_PARENT.get(stage),
        "contract": nc.json_safe(contract),
        "parentable": status in nc.PARENTABLE_STATUSES,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "platform": platform.platform(),
        },
    }
    if extra:
        manifest["extra"] = nc.json_safe(extra)
    manifest_path = atomic_write_json(
        base / "manifest.json", manifest, overwrite_completed=overwrite_completed
    )
    index = sorted(
        (
            {
                "path": str(p.relative_to(base)),
                "sha256": ccs.sha256_file(p),
                "bytes": p.stat().st_size,
            }
            for p in base.rglob("*")
            if p.is_file() and p.name != "artifacts.json"
        ),
        key=lambda row: row["path"],
    )
    atomic_write_json(
        base / "artifacts.json",
        {"generated_utc": manifest["generated_utc"], "files": index},
        overwrite_completed=True,
    )
    return {"manifest": str(manifest_path), "manifest_payload": manifest}


def validate_parent_stage(
    parent_directory: str | Path,
    *,
    expected_stage: str,
    expected_model: str,
    targets: nc.TargetContract,
    seeds: nc.SeedRegistry,
    child_stage: str,
) -> dict:
    """Validate a parent before any child design is produced (spec sec. 5).

    Checks the completion manifest, the artifact index, candidate counts and
    hashes, and seed disjointness.  **A partial stage can never become a
    parent**, which is the check that stops a half-finished search from
    silently defining the region report.
    """
    base = Path(parent_directory)
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        raise ContractError(f"parent stage has no completion manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())

    required_parent = nc.STAGE_PARENT.get(child_stage)
    if required_parent is not None and manifest.get("stage") != required_parent:
        raise ContractError(
            f"stage {child_stage!r} requires parent {required_parent!r}, "
            f"found {manifest.get('stage')!r}"
        )
    if manifest.get("stage") != expected_stage:
        raise ContractError(
            f"parent manifest declares stage {manifest.get('stage')!r}, expected {expected_stage!r}"
        )
    if manifest.get("model") != expected_model:
        raise ContractError(
            f"parent manifest declares model {manifest.get('model')!r}, expected {expected_model!r}"
        )
    status = manifest.get("status")
    if status not in nc.PARENTABLE_STATUSES:
        raise ContractError(
            f"parent stage {expected_stage!r} has status {status!r}; only a "
            f"{sorted(nc.PARENTABLE_STATUSES)} stage may be a parent. "
            "A partial stage cannot parent the next stage."
        )
    if int(manifest.get("n_incomplete", 0)) != 0:
        raise ContractError(
            f"parent stage reports {manifest.get('n_incomplete')} incomplete work units "
            "but claims to be complete"
        )

    index_path = base / "artifacts.json"
    if not index_path.exists():
        raise ContractError(f"parent stage has no artifact index: {index_path}")
    index = json.loads(index_path.read_text())
    for entry in index.get("files", []):
        path = base / entry["path"]
        if not path.exists():
            raise ContractError(f"artifact index references a missing file: {entry['path']}")
        actual = ccs.sha256_file(path)
        if actual != entry["sha256"]:
            raise ContractError(
                f"artifact {entry['path']} changed since the parent stage completed "
                f"(index {entry['sha256']}, found {actual})"
            )

    contract = manifest.get("contract") or {}
    parent_order = tuple(contract.get("target_order") or ())
    if parent_order and parent_order != targets.frozen_order:
        raise ContractError(
            "parent stage used a different target order; M0/M1 and every stage must share "
            f"one contract.\n  parent: {list(parent_order)}\n  child:  {list(targets.frozen_order)}"
        )

    parent_seeds = (contract.get("seeds") or {})
    if child_stage in (nc.STAGE_CONFIRMATION, nc.STAGE_BRIDGES):
        discovery_used = set(int(s) for s in (parent_seeds.get("discovery") or ()))
        overlap = sorted(discovery_used & set(seeds.confirmation))
        if overlap:
            raise ContractError(
                f"confirmation seeds overlap the parent's discovery seeds: {overlap}. "
                "The confirmation block must be reserved and uninspected."
            )
    return manifest


# ==========================================================================
# Handoff audit
# ==========================================================================

def handoff_audit(
    *,
    proposed: Mapping[str, int],
    rejected: Mapping[str, int],
    dispatched: Mapping[str, int],
    completed: Mapping[str, int],
    promoted: Mapping[str, int],
    confirmed: Mapping[str, int],
    quotas: Mapping[str, int],
) -> pd.DataFrame:
    """Per-kernel count reconciliation across the stage transitions.

    Count conservation must hold at every transition: dispatched work cannot
    exceed what survived rejection, and completions cannot exceed dispatch.
    A missing kernel family is fatal rather than a scientific result.
    """
    families = sorted(
        set(quotas) | set(proposed) | set(dispatched) | set(completed)
        | set(promoted) | set(confirmed)
    )
    rows = []
    problems: list[str] = []
    for kernel in families:
        n_proposed = int(proposed.get(kernel, 0))
        n_rejected = int(rejected.get(kernel, 0))
        n_dispatched = int(dispatched.get(kernel, 0))
        n_completed = int(completed.get(kernel, 0))
        n_promoted = int(promoted.get(kernel, 0))
        n_confirmed = int(confirmed.get(kernel, 0))
        quota = int(quotas.get(kernel, 0))
        if kernel in quotas and n_dispatched != quota:
            problems.append(
                f"kernel {kernel!r}: dispatched {n_dispatched} but quota is {quota}"
            )
        if n_dispatched > n_proposed - n_rejected:
            problems.append(
                f"kernel {kernel!r}: dispatched {n_dispatched} exceeds accepted proposals "
                f"({n_proposed} proposed - {n_rejected} rejected)"
            )
        if n_completed > n_dispatched:
            problems.append(
                f"kernel {kernel!r}: completed {n_completed} exceeds dispatched {n_dispatched}"
            )
        if n_promoted > n_completed:
            problems.append(
                f"kernel {kernel!r}: promoted {n_promoted} exceeds completed {n_completed}"
            )
        if n_confirmed > n_promoted:
            problems.append(
                f"kernel {kernel!r}: confirmed {n_confirmed} exceeds promoted {n_promoted}"
            )
        if quota > 0 and n_proposed == 0:
            problems.append(f"kernel family {kernel!r} produced nothing but has quota {quota}")
        rows.append({
            "kernel_id": kernel,
            "quota": quota,
            "n_proposed": n_proposed,
            "n_rejected": n_rejected,
            "n_dispatched": n_dispatched,
            "n_completed": n_completed,
            "n_promoted": n_promoted,
            "n_confirmed": n_confirmed,
            "conserved": True,
        })
    if problems:
        raise ContractError(
            "handoff audit failed count conservation:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    return pd.DataFrame(rows)


def assert_lineage(
    units: Sequence[WorkUnit],
    *,
    known_parent_ids: Iterable[str],
) -> None:
    """Every child must trace back to an exact known parent vector."""
    known = set(str(p) for p in known_parent_ids)
    orphans: list[str] = []
    for unit in units:
        unknown = [p for p in unit.parent_ids if p not in known]
        if unknown:
            orphans.append(f"{unit.design_id} -> unknown parent(s) {unknown}")
    if orphans:
        raise ContractError(
            "lineage broken; no stage may reconstruct a parent from filenames or defaults:\n"
            + "\n".join(f"  - {o}" for o in orphans)
        )


# ==========================================================================
# Resume
# ==========================================================================

@dataclass(frozen=True)
class ResumeDecision:
    """What a resume may do, and what it must refuse."""

    status: str
    n_incomplete: int
    n_completed: int
    consumed_attempts: int
    remaining_attempts: int
    remaining_trajectories: int
    relaunchable: tuple[tuple[str, int], ...]
    parentable: bool
    reason: str

    def as_row(self) -> dict:
        return {
            "status": self.status,
            "n_incomplete": self.n_incomplete,
            "n_completed": self.n_completed,
            "consumed_attempts": self.consumed_attempts,
            "remaining_attempts": self.remaining_attempts,
            "remaining_trajectories": self.remaining_trajectories,
            "n_relaunchable": len(self.relaunchable),
            "parentable": self.parentable,
            "reason": self.reason,
        }


def plan_resume(
    ledger: Ledger,
    *,
    max_attempts: int,
    max_trajectories: int,
) -> ResumeDecision:
    """Decide what a resumed run may relaunch.

    Launch attempts consume budget and stay spent: a lost attempt is not
    refunded.  If the attempt or trajectory budget expires while units remain
    incomplete, the stage is **partial** and cannot become a parent.
    A completed unit is never relaunched.
    """
    consumed = ledger.consumed_attempts()
    completed = ledger.completed_keys()
    incomplete = ledger.incomplete_units()
    remaining_attempts = max(0, int(max_attempts) - consumed)
    remaining_trajectories = max(0, int(max_trajectories) - consumed)

    relaunchable = tuple(
        (unit.design_id, int(unit.seed)) for unit in incomplete
    )
    if not incomplete:
        return ResumeDecision(
            status=nc.STATUS_COMPLETE,
            n_incomplete=0,
            n_completed=len(completed),
            consumed_attempts=consumed,
            remaining_attempts=remaining_attempts,
            remaining_trajectories=remaining_trajectories,
            relaunchable=(),
            parentable=True,
            reason="every frozen work unit has exactly one successful result",
        )
    budget_left = min(remaining_attempts, remaining_trajectories)
    if budget_left <= 0:
        return ResumeDecision(
            status=nc.STATUS_PARTIAL,
            n_incomplete=len(incomplete),
            n_completed=len(completed),
            consumed_attempts=consumed,
            remaining_attempts=remaining_attempts,
            remaining_trajectories=remaining_trajectories,
            relaunchable=(),
            parentable=False,
            reason=(
                f"budget exhausted with {len(incomplete)} incomplete work unit(s); "
                "the stage is PARTIAL and cannot become a parent"
            ),
        )
    return ResumeDecision(
        status=nc.STATUS_PARTIAL,
        n_incomplete=len(incomplete),
        n_completed=len(completed),
        consumed_attempts=consumed,
        remaining_attempts=remaining_attempts,
        remaining_trajectories=remaining_trajectories,
        relaunchable=relaunchable[:budget_left],
        parentable=False,
        reason=(
            f"{len(incomplete)} incomplete work unit(s) may be relaunched under the same "
            "frozen key with a new attempt_id"
        ),
    )


# ==========================================================================
# Wall-clock cap
# ==========================================================================

@dataclass
class WallClockCap:
    """The declared wall-clock cap, with *bounded in-flight completion*.

    The cap stops **dispatch**, not the work already running.  A worker checks
    :meth:`may_start` before taking the next frozen work unit and finishes the
    unit it has already begun, because killing a unit mid-flight would spend a
    launch attempt and leave no result -- the budget is consumed either way, so
    letting it finish is strictly better science for the same money.

    "Bounded" is the operative word: at most one unit may overrun, and
    :attr:`overran_by_seconds` records by how much.  The stage is then
    incomplete, which the ordinary resume path turns into ``PARTIAL``, and a
    ``PARTIAL`` stage can never become a parent.  Nothing here invents a new
    status or shortcut.
    """

    cap_seconds: float
    started_utc: float = field(default_factory=time.time)
    stopped_for_cap: bool = False
    n_dispatched: int = 0
    n_withheld: int = 0
    overran_by_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not (float(self.cap_seconds) > 0.0):
            raise ContractError(
                f"budget.wall_time_cap_seconds must be positive; got {self.cap_seconds!r}"
            )

    def elapsed(self, *, now: float | None = None) -> float:
        return float((time.time() if now is None else float(now)) - self.started_utc)

    def may_start(self, *, now: float | None = None) -> bool:
        """Whether another frozen work unit may be dispatched."""
        if self.elapsed(now=now) >= float(self.cap_seconds):
            self.stopped_for_cap = True
            return False
        return True

    def record_dispatch(self) -> None:
        self.n_dispatched += 1

    def record_withheld(self, count: int = 1) -> None:
        self.n_withheld += int(count)

    def record_completion(self, *, now: float | None = None) -> None:
        """Note any overrun of the unit that was already in flight."""
        over = self.elapsed(now=now) - float(self.cap_seconds)
        if over > 0.0:
            self.overran_by_seconds = max(self.overran_by_seconds, float(over))

    def as_row(self) -> dict:
        return {
            "wall_time_cap_seconds": float(self.cap_seconds),
            "elapsed_seconds": self.elapsed(),
            "stopped_for_wall_clock_cap": bool(self.stopped_for_cap),
            "n_units_dispatched": self.n_dispatched,
            "n_units_withheld_at_cap": self.n_withheld,
            "in_flight_overrun_seconds": float(self.overran_by_seconds),
            "consequence": (
                "the cap stops dispatch and lets bounded in-flight work finish; remaining "
                "units stay incomplete, so the stage is PARTIAL and cannot become a parent"
            ),
        }


def assert_retry_preserves_key(
    ledger: Ledger,
    design_id: str,
    seed: int,
    *,
    vector_hash: str,
) -> str:
    """A retry may change only the ``attempt_id`` (spec sec. 5)."""
    unit = next(
        (u for u in ledger.units if u.design_id == design_id and int(u.seed) == int(seed)), None
    )
    if unit is None:
        raise ContractError(f"no frozen work unit {design_id!r} at seed {seed}")
    if unit.vector_hash != vector_hash:
        raise ContractError(
            f"retry of {design_id!r} changed the vector "
            f"({vector_hash} vs frozen {unit.vector_hash}); only attempt_id may change"
        )
    if unit.key in ledger.completed_keys():
        raise ContractError(
            f"{design_id!r} seed {seed} is already complete; a completed work unit is never rerun"
        )
    return ledger.next_attempt_id(design_id, int(seed))


__all__ = [name for name in dir() if not name.startswith("_")]
