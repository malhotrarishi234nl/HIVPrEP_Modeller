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
import importlib.metadata
import json
import os
import platform
import sqlite3
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
ARTIFACT_INDEX_SCHEMA_VERSION = "nroy_artifact_index/1.0.0"

# Attempt/row statuses.
ATTEMPT_PENDING = "pending"
ATTEMPT_RUNNING = "running"
ATTEMPT_SUCCESS = "success"
ATTEMPT_FAILED = "failed"
ATTEMPT_LOST = "lost"
ATTEMPT_LINKED = "linked_existing_result"
ATTEMPT_STATUSES = (
    ATTEMPT_PENDING, ATTEMPT_RUNNING, ATTEMPT_SUCCESS, ATTEMPT_FAILED, ATTEMPT_LOST,
    ATTEMPT_LINKED,
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
        # Persist the directory entry as well as the file contents.  Without
        # this fsync a power loss can occur after the atomic rename appears to
        # succeed but before the new filename is durable.
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
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


def read_jsonl_records(
    path: str | Path, *, repair_truncated_tail: bool = False
) -> list[dict]:
    """Read a durable JSON-lines journal without laundering corruption.

    A process can be killed while its final ``write`` is in progress.  The
    resulting *unterminated final fragment* is the one recoverable condition:
    it was never a durable event and is ignored.  A complete final record does
    not need a trailing newline and is accepted.  Malformed UTF-8/JSON anywhere
    else -- including a newline-terminated final line -- proves that durable
    journal history is corrupt and fails closed.

    Returning mappings only is deliberate.  Attempt journals are event
    objects; accepting a scalar or array here would merely defer corruption to
    a less intelligible ``.get``/field error during ledger restoration.
    """
    journal = Path(path)
    raw = journal.read_bytes()
    records: list[dict] = []
    lines = raw.splitlines(keepends=True)
    durable_bytes = 0
    for index, encoded in enumerate(lines, start=1):
        terminated = encoded.endswith((b"\n", b"\r"))
        payload = encoded.rstrip(b"\r\n")
        try:
            if not payload.strip():
                raise ValueError("blank journal record")
            decoded = payload.decode("utf-8")
            record = nc.strict_json_loads(
                decoded,
                what=f"attempt journal {journal} line {index}",
            )
            if not isinstance(record, dict):
                raise ValueError(
                    f"journal record is {type(record).__name__}, expected an object"
                )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ContractError) as exc:
            is_final = index == len(lines)
            if is_final and not terminated:
                # The only safe recovery: bytes from the final event never
                # reached their newline/flush boundary, so the event did not
                # become part of the durable journal.
                if repair_truncated_tail:
                    # The caller must own this shard journal exclusively.  A
                    # physical truncate is necessary before appending the next
                    # event, or today's recoverable tail becomes tomorrow's
                    # fatal interior corruption.
                    with journal.open("r+b") as stream:
                        stream.truncate(durable_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                break
            raise ContractError(
                f"attempt journal {journal} is corrupt at durable line {index}: {exc}"
            ) from exc
        records.append(record)
        durable_bytes += len(encoded)
    return records


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
        self._assert_attempt_audit()

    def _assert_attempt_audit(self) -> None:
        """Reject duplicate, orphaned or contradictory restored attempt state."""
        units = {
            (unit.design_id, int(unit.seed)): unit for unit in self.units
        }
        seen_ids: set[str] = set()
        successful_units: set[tuple[str, int]] = set()
        for attempt in self.attempts:
            if attempt.attempt_id in seen_ids:
                raise ContractError(
                    f"attempt audit repeats attempt_id {attempt.attempt_id!r}"
                )
            seen_ids.add(attempt.attempt_id)
            key = (attempt.design_id, int(attempt.seed))
            unit = units.get(key)
            if unit is None:
                raise ContractError(
                    f"attempt {attempt.attempt_id!r} does not correspond to a frozen unit"
                )
            if attempt.vector_hash and attempt.vector_hash != unit.vector_hash:
                raise ContractError(
                    f"attempt {attempt.attempt_id!r} vector hash does not match its "
                    "frozen unit"
                )
            if attempt.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED):
                if key in successful_units:
                    raise ContractError(
                        f"work unit {attempt.design_id} seed {attempt.seed} has more than "
                        "one successful/linked attempt"
                    )
                successful_units.add(key)

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

    def consumed_result_keys(self) -> set[str]:
        """Unique scientific trajectories ever launched.

        Retries consume another *attempt* but do not invent another frozen
        vector-by-seed trajectory.  Conversely, two work units that share the
        same vector and seed share one trajectory even though both receive a
        separately bound result row.  Keeping these two budgets distinct is
        required for result-key de-duplication to remain scientifically and
        financially meaningful.
        """
        units = {
            (unit.design_id, int(unit.seed)): unit
            for unit in self.units
        }
        consumed: set[str] = set()
        for attempt in self.attempts:
            if attempt.status not in ATTEMPT_CONSUMED:
                continue
            unit = units.get((attempt.design_id, int(attempt.seed)))
            if unit is not None:
                consumed.add(unit.result_key)
        return consumed

    def successful_result_keys(self) -> set[str]:
        """Scientific result keys with at least one durable successful row."""
        units = {
            (unit.design_id, int(unit.seed)): unit
            for unit in self.units
        }
        successful: set[str] = set()
        for attempt in self.attempts:
            if attempt.status not in (ATTEMPT_SUCCESS, ATTEMPT_LINKED):
                continue
            unit = units.get((attempt.design_id, int(attempt.seed)))
            if unit is not None:
                successful.add(unit.result_key)
        return successful

    def running_attempts(self) -> tuple[AttemptRecord, ...]:
        """Return the durable attempts that have no terminal event yet."""
        return tuple(a for a in self.attempts if a.status == ATTEMPT_RUNNING)

    def reconcile_running_attempts_as_lost(
        self,
        *,
        lost_attempt_ids: Iterable[str],
        ended_utc: float | None = None,
        failure_reason: str = "worker ended without a terminal durable event",
        failure_class: str = "LostWorker",
    ) -> tuple[AttemptRecord, ...]:
        """Advance proven-dead ``RUNNING`` attempts to durable ``LOST`` events.

        The caller must supply the exact ids whose worker ownership is known
        to have ended (for example, after acquiring that worker shard's
        full-lifetime lock).  Marking every restored ``RUNNING`` attempt lost
        would race with healthy workers on other shards, so this method has no
        implicit "all" mode.

        ``LOST`` is a terminal state of the *same* launch attempt.  The method
        therefore replaces the in-memory state under the same ``attempt_id``
        and queues the replacement event in ``recorded_this_process`` for the
        caller to append with :meth:`save_attempt_record`.  Lost launches stay
        in :data:`ATTEMPT_CONSUMED`; reconciliation never refunds budget.
        """
        requested = {str(value) for value in lost_attempt_ids}
        if not requested:
            return ()
        by_id = {attempt.attempt_id: (index, attempt)
                 for index, attempt in enumerate(self.attempts)}
        missing = sorted(requested - set(by_id))
        if missing:
            raise ContractError(
                f"cannot mark unknown attempt id(s) LOST: {missing}"
            )
        not_running = sorted(
            attempt_id for attempt_id in requested
            if by_id[attempt_id][1].status != ATTEMPT_RUNNING
        )
        if not_running:
            states = {attempt_id: by_id[attempt_id][1].status
                      for attempt_id in not_running}
            raise ContractError(
                f"only RUNNING attempts may transition to LOST; found {states}"
            )
        stopped = float(time.time() if ended_utc is None else ended_utc)
        reconciled: list[AttemptRecord] = []
        for attempt_id in sorted(requested):
            index, running = by_id[attempt_id]
            if running.started_utc is not None and stopped < float(running.started_utc):
                raise ContractError(
                    f"attempt {attempt_id} cannot end at {stopped} before it started "
                    f"at {running.started_utc}"
                )
            lost = replace(
                running,
                status=ATTEMPT_LOST,
                ended_utc=stopped,
                failure_reason=str(failure_reason),
                failure_class=str(failure_class),
            )
            self.attempts[index] = lost
            self.recorded_this_process.append(lost)
            reconciled.append(lost)
        return tuple(reconciled)

    def record_attempt(self, attempt: AttemptRecord) -> AttemptRecord:
        unit_keys = {(u.design_id, int(u.seed)) for u in self.units}
        if (attempt.design_id, int(attempt.seed)) not in unit_keys:
            raise ContractError(
                f"attempt {attempt.attempt_id} does not correspond to any frozen work unit; "
                "a worker may not invent work"
            )
        if any(a.attempt_id == attempt.attempt_id for a in self.attempts):
            raise ContractError(f"duplicate attempt_id {attempt.attempt_id}")
        unit = next(
            u for u in self.units
            if u.design_id == attempt.design_id and int(u.seed) == int(attempt.seed)
        )
        if attempt.vector_hash and attempt.vector_hash != unit.vector_hash:
            raise ContractError(
                f"attempt {attempt.attempt_id} vector hash does not match its frozen unit"
            )
        if attempt.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED):
            already = [
                a for a in self.attempts_for(attempt.design_id, int(attempt.seed))
                if a.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED)
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
            for a in self.attempts if a.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED)
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

    def save_attempt_record(
        self, directory: str | Path, shard_label: str, attempt: AttemptRecord
    ) -> Path:
        """Durably append one attempt-state event without quadratic rewrites."""
        base = Path(directory) / self.ATTEMPTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{shard_label}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(nc.json_safe(attempt.as_row()), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(base, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return path

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
        target = base / f"{shard_label}.csv"
        rows: dict[str, dict] = {}
        if target.exists():
            for row in read_frame_exact(target).to_dict("records"):
                if str(row.get("attempt_id", "")):
                    rows[str(row["attempt_id"])] = row
        for attempt in self.recorded_this_process:
            row = attempt.as_row()
            # The row is an append-only logical attempt whose state advances
            # running -> success/failed. Rewriting that one row preserves the
            # launch record across a crash without inventing a second attempt.
            rows[attempt.attempt_id] = row
        frame = pd.DataFrame(list(rows.values()), columns=self.attempt_frame().columns)
        return atomic_write_frame(
            target, frame, overwrite_completed=True
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
        meta = nc.strict_json_load(base / "ledger_meta.json", what="ledger metadata")
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
        attempts_by_id: dict[str, AttemptRecord] = {}
        # The shared file is the single-process view; attempts/*.csv are the
        # per-shard trails. Union them, de-duplicating by attempt_id, so a
        # distributed run reconstructs the COMPLETE audit history.
        sources = [base / "ledger_attempts.csv"]
        sources += sorted((base / cls.ATTEMPTS_DIR).glob("*.csv")) \
            if (base / cls.ATTEMPTS_DIR).exists() else []
        def parsed_attempt(row: Mapping) -> AttemptRecord | None:
            if not str(row.get("attempt_id", "")):
                return None
            return AttemptRecord(
                attempt_id=str(row["attempt_id"]), design_id=str(row["design_id"]),
                seed=int(row["seed"]), status=str(row["status"]),
                started_utc=_opt_float(row.get("started_utc")),
                ended_utc=_opt_float(row.get("ended_utc")),
                host=str(row.get("host", "") or ""),
                process=str(row.get("process", "") or ""),
                vector_hash=str(row.get("vector_hash", "") or ""),
                result_hash=str(row.get("result_hash", "") or ""),
                failure_reason=str(row.get("failure_reason", "") or ""),
                failure_class=str(row.get("failure_class", "") or ""),
            )

        for attempts_path in sources:
            if not attempts_path.exists():
                continue
            frame = read_frame_exact(attempts_path)
            for _, row in frame.iterrows():
                attempt = parsed_attempt(row)
                if attempt is not None:
                    prior = attempts_by_id.get(attempt.attempt_id)
                    if prior is not None and prior.as_row() != attempt.as_row():
                        raise ContractError(
                            f"attempt snapshots disagree for {attempt.attempt_id!r}"
                        )
                    attempts_by_id[attempt.attempt_id] = attempt
        if (base / cls.ATTEMPTS_DIR).exists():
            journal_latest: dict[str, AttemptRecord] = {}
            journal_source: dict[str, Path] = {}
            unit_map = {
                (unit.design_id, int(unit.seed)): unit for unit in units
            }
            for journal in sorted((base / cls.ATTEMPTS_DIR).glob("*.jsonl")):
                for row in read_jsonl_records(journal):
                    attempt = parsed_attempt(row)
                    if attempt is None:
                        raise ContractError(f"{journal}: journal event has no attempt_id")
                    unit = unit_map.get((attempt.design_id, int(attempt.seed)))
                    if unit is None or attempt.vector_hash != unit.vector_hash:
                        raise ContractError(
                            f"{journal}: attempt {attempt.attempt_id!r} is not bound to "
                            "its exact frozen unit/vector"
                        )
                    prior = journal_latest.get(attempt.attempt_id)
                    if prior is None:
                        if attempt.status not in (ATTEMPT_RUNNING, ATTEMPT_LINKED):
                            raise ContractError(
                                f"{journal}: attempt {attempt.attempt_id!r} starts in "
                                f"terminal state {attempt.status!r}"
                            )
                        if attempt.started_utc is None:
                            raise ContractError(
                                f"{journal}: attempt {attempt.attempt_id!r} has no start time"
                            )
                        if attempt.status == ATTEMPT_RUNNING and (
                            attempt.ended_utc is not None or attempt.result_hash
                        ):
                            raise ContractError(
                                f"{journal}: RUNNING attempt {attempt.attempt_id!r} "
                                "already carries terminal fields"
                            )
                        if attempt.status == ATTEMPT_LINKED and (
                            attempt.ended_utc is None or not attempt.result_hash
                        ):
                            raise ContractError(
                                f"{journal}: linked attempt {attempt.attempt_id!r} lacks "
                                "a terminal time or result hash"
                            )
                        journal_source[attempt.attempt_id] = journal
                    else:
                        if journal_source[attempt.attempt_id] != journal:
                            raise ContractError(
                                f"attempt {attempt.attempt_id!r} appears in more than one "
                                "shard journal"
                            )
                        if prior.status != ATTEMPT_RUNNING or attempt.status not in {
                            ATTEMPT_SUCCESS, ATTEMPT_FAILED, ATTEMPT_LOST,
                        }:
                            raise ContractError(
                                f"{journal}: ambiguous attempt transition for "
                                f"{attempt.attempt_id!r}: {prior.status!r} -> "
                                f"{attempt.status!r}"
                            )
                        immutable_identity = (
                            prior.design_id, int(prior.seed), prior.started_utc,
                            prior.host, prior.process, prior.vector_hash,
                        )
                        terminal_identity = (
                            attempt.design_id, int(attempt.seed), attempt.started_utc,
                            attempt.host, attempt.process, attempt.vector_hash,
                        )
                        if immutable_identity != terminal_identity:
                            raise ContractError(
                                f"{journal}: attempt {attempt.attempt_id!r} changed identity "
                                "between RUNNING and terminal events"
                            )
                        if attempt.ended_utc is None or (
                            attempt.started_utc is not None
                            and float(attempt.ended_utc) < float(attempt.started_utc)
                        ):
                            raise ContractError(
                                f"{journal}: attempt {attempt.attempt_id!r} has an invalid "
                                "terminal time"
                            )
                        if attempt.status == ATTEMPT_SUCCESS and not attempt.result_hash:
                            raise ContractError(
                                f"{journal}: successful attempt {attempt.attempt_id!r} "
                                "has no result hash"
                            )
                    journal_latest[attempt.attempt_id] = attempt
            for attempt_id, attempt in journal_latest.items():
                snapshot = attempts_by_id.get(attempt_id)
                if snapshot is not None and snapshot.status not in {
                    ATTEMPT_RUNNING, attempt.status,
                }:
                    raise ContractError(
                        f"attempt snapshot for {attempt_id!r} contradicts its journal "
                        f"({snapshot.status!r} versus {attempt.status!r})"
                    )
                attempts_by_id[attempt_id] = attempt
        attempts = sorted(
            attempts_by_id.values(), key=lambda attempt: attempt.attempt_id
        )
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


def rebuild_dispatch_index(path: str | Path, ledger: Ledger) -> dict:
    """Atomically rebuild the SQLite dispatch cache from durable ledger truth.

    The append-only attempt journals and frozen ledger are authoritative;
    SQLite is only a concurrency/lookup acceleration index.  Rebuilding on
    worker startup is consequently safer than trying to repair a possibly
    corrupt database in place, and also corrects counters that are internally
    valid SQLite but disagree with the journal.

    The caller must hold the cross-process dispatch lock and ensure no other
    connection is open while the replacement occurs.  Any ``RUNNING`` events
    that are known to be abandoned must be reconciled to ``LOST`` *before*
    calling this function; live running attempts are intentionally preserved
    as active reservations.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(handle)
    temporary = Path(temporary_name)
    # sqlite3.connect expects either no file or a valid database, not the
    # zero-byte file created by mkstemp on every supported SQLite version.
    temporary.unlink()

    attempts_by_pair: dict[tuple[str, int], list[AttemptRecord]] = {}
    for attempt in ledger.attempts:
        attempts_by_pair.setdefault(
            (attempt.design_id, int(attempt.seed)), []
        ).append(attempt)

    by_result = ledger.unique_result_keys()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE unit_state "
            "(unit_key TEXT PRIMARY KEY, attempt_count INTEGER NOT NULL, "
            "completed INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE result_state "
            "(result_key TEXT PRIMARY KEY, consumed INTEGER NOT NULL, "
            "active INTEGER NOT NULL, successful INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE budget "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "consumed_attempts INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO budget VALUES (1, ?)", (ledger.consumed_attempts(),)
        )
        for unit in ledger.units:
            attempts = attempts_by_pair.get((unit.design_id, int(unit.seed)), ())
            completed = any(
                attempt.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED)
                for attempt in attempts
            )
            connection.execute(
                "INSERT INTO unit_state VALUES (?, ?, ?)",
                (unit.key_string, len(attempts), int(completed)),
            )
        for scientific_key, units in by_result.items():
            relevant = [
                attempt
                for unit in units
                for attempt in attempts_by_pair.get(
                    (unit.design_id, int(unit.seed)), ()
                )
            ]
            consumed = any(
                attempt.status in ATTEMPT_CONSUMED for attempt in relevant
            )
            successful = any(
                attempt.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED)
                for attempt in relevant
            )
            active = (
                any(attempt.status == ATTEMPT_RUNNING for attempt in relevant)
                and not successful
            )
            connection.execute(
                "INSERT INTO result_state VALUES (?, ?, ?, ?)",
                (scientific_key, int(consumed), int(active), int(successful)),
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ContractError(
                f"rebuilt dispatch index failed SQLite integrity_check: {integrity!r}"
            )
        connection.close()
        connection = None
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())

        # A corrupt WAL must not survive replacement of its main database.
        # The dispatch lock/no-open-connection precondition makes removal safe.
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)

    return {
        "path": str(target),
        "consumed_attempts": ledger.consumed_attempts(),
        "consumed_trajectories": len(ledger.consumed_result_keys()),
        "n_units": len(ledger.units),
        "n_running": len(ledger.running_attempts()),
    }


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
            "target_order": "|".join(self.target_order),
            "attempt_id": self.attempt_id,
            "result_hash": self.result_hash,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
            "parent_manifest_sha256": self.parent_manifest_sha256,
        }
        for name, value in zip(self.target_order, self.values):
            row[f"target:{name}"] = float(value)
        return row


_SEED_RESULT_METADATA_FIELDS = frozenset({
    "design_id", "seed", "stage", "model", "vector_hash", "attempt_id",
    "target_order", "result_hash", "code_sha256", "config_sha256",
    "parent_manifest_sha256",
})


def seed_result_from_durable_row(
    row: Mapping,
    *,
    targets: nc.TargetContract,
    source: str | Path = "durable result row",
) -> SeedResult:
    """Parse one durable result using its recorded provenance, never current values.

    The persisted schema is exact.  Unknown fields such as a self-described
    ``result_key`` or parameter vector are not trusted inputs
    and are refused rather than silently ignored.  Target values are keyed by
    the frozen target names, and the declared result hash must match a fresh
    recomputation before the row can be considered for linking.
    """
    expected = _SEED_RESULT_METADATA_FIELDS | {
        f"target:{name}" for name in targets.frozen_order
    }
    present = {str(key) for key in row}
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    if missing or extra:
        raise ContractError(
            f"{source}: durable seed-result schema mismatch; "
            f"missing={missing}, unexpected={extra}"
        )
    recorded_target_order = tuple(str(row["target_order"]).split("|"))
    if recorded_target_order != targets.frozen_order:
        raise ContractError(
            f"{source}: target schema does not match the contract; recorded "
            f"{list(recorded_target_order)}, expected {list(targets.frozen_order)}"
        )
    result = SeedResult(
        design_id=str(row["design_id"]),
        seed=int(row["seed"]),
        stage=str(row["stage"]),
        model=str(row["model"]),
        vector_hash=str(row["vector_hash"]),
        target_order=recorded_target_order,
        values=tuple(float(row[f"target:{name}"]) for name in targets.frozen_order),
        attempt_id=str(row["attempt_id"]),
        code_sha256=str(row["code_sha256"]),
        config_sha256=str(row["config_sha256"]),
        parent_manifest_sha256=str(row["parent_manifest_sha256"] or ""),
    )
    declared_hash = str(row["result_hash"])
    if result.result_hash != declared_hash:
        raise ContractError(
            f"{source}: declared result hash {declared_hash!r} does not match "
            f"recomputed {result.result_hash!r}"
        )
    return result


def validate_durable_seed_result(
    ledger: Ledger,
    result: SeedResult,
    *,
    targets: nc.TargetContract,
) -> None:
    """Require an exact frozen unit and its original terminal attempt event.

    This is the single-row form of the merge gate for evaluation-time result
    reuse.  It prevents a stale row from being reduced to bare values and then
    rewrapped with the current design/code/config/parent/attempt provenance.
    """
    merge_seed_results(ledger, [result], targets=targets, expect_complete=False)


def validate_orphaned_lost_seed_result(
    ledger: Ledger,
    result: SeedResult,
    *,
    targets: nc.TargetContract,
) -> None:
    """Validate the sole reusable-file exception: an exact row from a LOST attempt.

    A crash can occur after atomically writing the result but before appending
    the SUCCESS event.  Once shard reconciliation marks that RUNNING attempt
    LOST, the row must not be reused, but it may remain in place for the retry
    to overwrite.  This validator ensures the exception cannot become a way to
    ignore an arbitrary stale row merely because it borrowed a lost id.
    """
    key = (result.stage, result.model, result.design_id, int(result.seed))
    unit = ledger.by_key().get(key)
    problems: list[str] = []
    if unit is None:
        problems.append(f"no frozen work unit for key {key}")
    else:
        if result.vector_hash != unit.vector_hash:
            problems.append("vector hash does not match the frozen unit")
        if tuple(result.target_order) != targets.frozen_order:
            problems.append("target order does not match the frozen contract")
        if tuple(unit.target_schema) != targets.frozen_order:
            problems.append("frozen unit target schema does not match the contract")
        if result.code_sha256 != unit.code_sha256:
            problems.append("code hash does not match the frozen unit")
        if result.config_sha256 != unit.config_sha256:
            problems.append("configuration hash does not match the frozen unit")
        if result.parent_manifest_sha256 != unit.parent_manifest_sha256:
            problems.append("parent-manifest hash does not match the frozen unit")
    matching_attempts = [
        attempt for attempt in ledger.attempts
        if attempt.attempt_id == result.attempt_id
    ]
    if len(matching_attempts) != 1:
        problems.append(
            f"expected one durable attempt {result.attempt_id!r}, found "
            f"{len(matching_attempts)}"
        )
    else:
        attempt = matching_attempts[0]
        if attempt.status != ATTEMPT_LOST:
            problems.append(
                f"attempt {result.attempt_id!r} is {attempt.status!r}, not LOST"
            )
        if (
            attempt.design_id != result.design_id
            or int(attempt.seed) != int(result.seed)
            or attempt.vector_hash != result.vector_hash
        ):
            problems.append("LOST attempt identity does not match the durable result")
        if attempt.result_hash and attempt.result_hash != result.result_hash:
            problems.append("LOST attempt result hash contradicts the durable result")
    if not np.all(np.isfinite(np.asarray(result.values, dtype=float))):
        problems.append("durable result contains non-finite target values")
    if problems:
        raise ContractError(
            "durable result is not an exact orphan of a reconciled LOST attempt:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


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
        if result.parent_manifest_sha256 != unit.parent_manifest_sha256:
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
        (ledger.stage, ledger.model, a.design_id, int(a.seed)): a
        for a in ledger.attempts if a.status in (ATTEMPT_SUCCESS, ATTEMPT_LINKED)
    }
    for key in sorted(collected):
        if key not in successful:
            problems.append(
                f"{key}: a result was supplied but no successful attempt is recorded; "
                "a failed or in-flight attempt may not masquerade as a result"
            )
            continue
        attempt = successful[key]
        result = collected[key]
        if result.attempt_id != attempt.attempt_id:
            problems.append(
                f"{key}: result attempt_id {result.attempt_id!r} does not match the "
                f"recorded successful attempt {attempt.attempt_id!r}"
            )
        if not attempt.result_hash or result.result_hash != attempt.result_hash:
            problems.append(
                f"{key}: result hash {result.result_hash!r} does not match the "
                f"recorded successful attempt hash {attempt.result_hash!r}"
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
        "design_id", "seed", "stage", "model", "vector_hash", "target_order", "attempt_id",
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
        out[f"residual:{name}"] = np.asarray(means[name]) - float(observed[name])

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

_ARTIFACT_INDEX_SELF_PATHS = frozenset({"manifest.json", "artifacts.json"})


def is_mutable_stage_runtime_path(relative_path: str | Path) -> bool:
    """Whether a PARTIAL checkpoint may legitimately change this artifact.

    These files are execution journals, durable per-unit results, worker
    snapshots or reconstructable caches.  They remain authoritative inputs to
    the next merge, but a PARTIAL stage cannot freeze their old bytes and also
    remain resumable.  All other files already present at the checkpoint stay
    hash-sealed across the resume.
    """
    path = Path(str(relative_path))
    return (
        (path.parts and path.parts[0] in {Ledger.ATTEMPTS_DIR, "results", "shards"})
        or path.name.startswith("budget_audit_shard_")
        or path.name in {
            "execution_budget.json", "execution_dispatch.sqlite3",
            ".dispatch.lock",
        }
        or path.name.startswith("execution_dispatch.sqlite3-")
        or (path.name.startswith(".shard_") and path.name.endswith(".worker.lock"))
    )


def _belongs_in_stage_index(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    # The report package is a reproducible presentation derivative, can be
    # regenerated after sealing, and is never consumed by a child stage.
    # Keeping it outside the scientific index lets reporting remain harmless
    # to a later parent handoff while every source table stays sealed.
    return (
        path.is_file()
        and str(relative) not in _ARTIFACT_INDEX_SELF_PATHS
        and (not relative.parts or relative.parts[0] != "package")
    )


def _artifact_entries(base: Path) -> list[dict]:
    """Build the exact non-circular inventory sealed by a stage manifest."""
    return sorted(
        (
            {
                "path": str(path.relative_to(base)),
                "sha256": ccs.sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in base.rglob("*")
            if _belongs_in_stage_index(path, base)
        ),
        key=lambda row: row["path"],
    )


def validate_artifact_index(
    directory: str | Path,
    *,
    required_paths: Iterable[str] | None = None,
    require_exact: bool = False,
) -> dict:
    """Validate the manifest seal, index structure, and indexed file hashes.

    ``manifest.json`` binds the exact bytes of ``artifacts.json`` through
    ``artifacts_sha256``.  The two metadata files are excluded from the index,
    avoiding a circular hash.  Consumers must verify this seal *before*
    trusting any file entries; otherwise an edited artifact can be silently
    paired with a rewritten index.

    When ``required_paths`` is supplied, only those entries' file contents are
    checked after the seal.  This is useful for a PREPARED stage whose
    append-only runtime outputs may legitimately grow during concurrent work.
    A completed parent must use the default (all entries) together with
    ``require_exact=True`` so unindexed additions also fail closed.
    """
    base = Path(directory)
    manifest_path = base / "manifest.json"
    index_path = base / "artifacts.json"
    if not manifest_path.exists():
        raise ContractError(f"stage has no manifest: {manifest_path}")
    if not index_path.exists():
        raise ContractError(f"stage has no artifact index: {index_path}")
    try:
        manifest = nc.strict_json_load(manifest_path, what="stage manifest")
    except (UnicodeDecodeError, ContractError) as exc:
        raise ContractError(
            f"stage manifest is not valid strict JSON: {manifest_path}: {exc}"
        ) from exc
    expected_index_hash = str(manifest.get("artifacts_sha256") or "")
    if not expected_index_hash:
        raise ContractError(
            "stage manifest does not seal artifacts.json with artifacts_sha256; "
            "legacy/unsealed stages cannot authorize scientific reuse and must be regenerated"
        )
    actual_index_hash = ccs.sha256_file(index_path)
    if actual_index_hash != expected_index_hash:
        raise ContractError(
            "artifact index changed after the stage manifest sealed it "
            f"(manifest {expected_index_hash}, found {actual_index_hash})"
        )
    try:
        index = nc.strict_json_load(index_path, what="artifact index")
    except (UnicodeDecodeError, ContractError) as exc:
        raise ContractError(
            f"artifact index is not valid strict JSON: {index_path}: {exc}"
        ) from exc
    if index.get("schema") != ARTIFACT_INDEX_SCHEMA_VERSION:
        raise ContractError(
            f"artifact index schema is {index.get('schema')!r}, expected "
            f"{ARTIFACT_INDEX_SCHEMA_VERSION!r}"
        )
    raw_entries = index.get("files")
    if not isinstance(raw_entries, list):
        raise ContractError("artifact index 'files' must be a list")
    entries: dict[str, Mapping] = {}
    for position, entry in enumerate(raw_entries):
        if not isinstance(entry, Mapping):
            raise ContractError(f"artifact index entry {position} is not an object")
        relative = str(entry.get("path") or "")
        candidate = Path(relative)
        if (
            not relative
            or candidate.is_absolute()
            or ".." in candidate.parts
            or relative in _ARTIFACT_INDEX_SELF_PATHS
        ):
            raise ContractError(f"unsafe/circular artifact index path {relative!r}")
        if relative in entries:
            raise ContractError(f"duplicate artifact index path {relative!r}")
        if not str(entry.get("sha256") or ""):
            raise ContractError(f"artifact index entry {relative!r} has no sha256")
        entries[relative] = entry

    requested = set(entries) if required_paths is None else {
        str(path) for path in required_paths
    }
    absent = sorted(requested - set(entries))
    if absent:
        raise ContractError(f"required artifact(s) absent from sealed index: {absent}")
    for relative in sorted(requested):
        entry = entries[relative]
        path = base / relative
        if not path.is_file():
            raise ContractError(f"artifact index references a missing file: {relative}")
        actual_hash = ccs.sha256_file(path)
        if actual_hash != str(entry["sha256"]):
            raise ContractError(
                f"artifact {relative} changed since the parent stage completed "
                "or the current stage was sealed "
                f"(index {entry['sha256']}, found {actual_hash})"
            )
        if "bytes" in entry and int(entry["bytes"]) != path.stat().st_size:
            raise ContractError(
                f"artifact {relative} size changed since the stage was sealed"
            )

    if require_exact:
        actual_paths = {
            str(path.relative_to(base))
            for path in base.rglob("*")
            if _belongs_in_stage_index(path, base)
        }
        missing = sorted(set(entries) - actual_paths)
        unindexed = sorted(actual_paths - set(entries))
        if missing or unindexed:
            raise ContractError(
                "sealed artifact inventory is not exact: "
                f"missing={missing}, unindexed={unindexed}"
            )
    return {"manifest": manifest, "index": index, "entries": entries}

def write_stage_manifest(
    directory: str | Path,
    *,
    stage: str,
    model: str,
    status: str,
    ledger: Ledger,
    contract: Mapping,
    parent_manifest: Mapping | None,
    parent_manifest_sha256: str | None = None,
    extra: Mapping | None = None,
    overwrite_completed: bool = False,
) -> dict:
    """Write a non-circular artifact index and a manifest that seals it."""
    if status not in nc.ALL_STATUSES:
        raise ContractError(f"unknown stage status {status!r}")
    base = Path(directory)
    manifest_target = base / "manifest.json"
    if manifest_target.exists() and not overwrite_completed:
        # Preflight before replacing artifacts.json.  Otherwise an intended
        # immutable-manifest refusal would itself break the old manifest's
        # artifact-index seal.
        raise ContractError(
            f"refusing to overwrite existing artifact {manifest_target}; completed "
            "artifacts are immutable and a resume may never replace one"
        )
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
            ccs.sha256_json(nc.json_safe(parent_manifest)) if parent_manifest
            else (str(parent_manifest_sha256) if parent_manifest_sha256 else None)
        ),
        "parent_stage": nc.STAGE_PARENT.get(stage),
        "contract": nc.json_safe(contract),
        "parentable": status in nc.PARENTABLE_STATUSES,
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": importlib.metadata.version("scipy"),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }
    if extra:
        manifest["extra"] = nc.json_safe(extra)
    index = _artifact_entries(base)
    index_path = atomic_write_json(
        base / "artifacts.json",
        {
            "schema": ARTIFACT_INDEX_SCHEMA_VERSION,
            "generated_utc": manifest["generated_utc"],
            "files": index,
        },
        overwrite_completed=True,
    )
    manifest["artifacts_sha256"] = ccs.sha256_file(index_path)
    manifest_path = atomic_write_json(
        manifest_target, manifest, overwrite_completed=overwrite_completed
    )
    return {"manifest": str(manifest_path), "manifest_payload": manifest}


def validate_parent_stage(
    parent_directory: str | Path,
    *,
    expected_stage: str,
    expected_model: str,
    targets: nc.TargetContract,
    seeds: nc.SeedRegistry,
    child_stage: str | None,
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
    # Verify the manifest -> artifact-index seal before trusting even the
    # manifest's stage/status claims, then verify the exact completed inventory.
    sealed = validate_artifact_index(base, require_exact=True)
    manifest = sealed["manifest"]

    # ``None`` validates a complete immutable stage as an external scientific
    # source without pretending it is the immediately preceding stage in the
    # caller's own stage order.  M1 discovery uses this for its M0-confirmation
    # embedding source; ordinary within-model handoffs always pass a stage.
    required_parent = nc.STAGE_PARENT.get(child_stage) if child_stage is not None else None
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
    missing_quotas = sorted(set(families) - set(quotas))
    if missing_quotas:
        problems.append(
            "observed kernel family/families have no explicit quota: "
            f"{missing_quotas}. A missing quota may not default to zero"
        )
    for kernel in families:
        n_proposed = int(proposed.get(kernel, 0))
        n_rejected = int(rejected.get(kernel, 0))
        n_dispatched = int(dispatched.get(kernel, 0))
        n_completed = int(completed.get(kernel, 0))
        n_promoted = int(promoted.get(kernel, 0))
        n_confirmed = int(confirmed.get(kernel, 0))
        quota = int(quotas.get(kernel, 0))
        if quota < 0:
            problems.append(f"kernel {kernel!r}: quota must be non-negative, got {quota}")
        if kernel in quotas and kernel in nc.CELL_QUOTA_KERNELS:
            if n_proposed != quota:
                problems.append(
                    f"kernel {kernel!r}: proposed {n_proposed} design cells but cell "
                    f"quota is {quota}"
                )
            expected_dispatch = n_proposed - n_rejected
            if n_dispatched != expected_dispatch:
                problems.append(
                    f"kernel {kernel!r}: dispatched {n_dispatched} but "
                    f"{expected_dispatch} of {n_proposed} design cells survived rejection"
                )
        elif kernel in quotas and n_dispatched != quota:
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
            "quota_basis": (
                "design_cells" if kernel in nc.CELL_QUOTA_KERNELS else "accepted_proposals"
            ),
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
    consumed_trajectories = ledger.consumed_result_keys()
    successful_result_keys = ledger.successful_result_keys()
    remaining_trajectories = max(
        0, int(max_trajectories) - len(consumed_trajectories)
    )

    relaunchable = tuple((unit.design_id, int(unit.seed)) for unit in incomplete)
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
    free_links = [
        unit for unit in incomplete if unit.result_key in successful_result_keys
    ]
    if not free_links and (remaining_attempts <= 0 or (
        remaining_trajectories <= 0
        and not any(unit.result_key in consumed_trajectories for unit in incomplete)
    )):
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
    # A retry of an already-consumed result key needs attempt budget but no new
    # trajectory budget.  New result keys are selected as complete groups, so
    # every linked work unit is retained and raw WorkUnit ordering cannot split
    # a de-duplicated scientific result.
    selected_result_keys: set[str] = set()
    new_result_keys: set[str] = set()
    selected_units: list[tuple[str, int]] = []
    for unit in incomplete:
        if unit.result_key not in selected_result_keys:
            paid_selected = len(selected_result_keys - successful_result_keys)
            if (
                unit.result_key not in successful_result_keys
                and paid_selected >= remaining_attempts
            ):
                continue
            if (
                unit.result_key not in consumed_trajectories
                and len(new_result_keys) >= remaining_trajectories
            ):
                continue
            selected_result_keys.add(unit.result_key)
            if unit.result_key not in consumed_trajectories:
                new_result_keys.add(unit.result_key)
        if unit.result_key in selected_result_keys:
            selected_units.append((unit.design_id, int(unit.seed)))
    return ResumeDecision(
        status=nc.STATUS_PARTIAL,
        n_incomplete=len(incomplete),
        n_completed=len(completed),
        consumed_attempts=consumed,
        remaining_attempts=remaining_attempts,
        remaining_trajectories=remaining_trajectories,
        relaunchable=tuple(selected_units),
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
