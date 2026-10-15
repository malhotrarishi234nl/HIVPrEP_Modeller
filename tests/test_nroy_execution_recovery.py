"""Adversarial tests for NROY execution persistence and audit sealing."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

import coupled_calibration_search as ccs
import nroy_contract as nc
import nroy_execution as ne
import run_robust_nroy_freeze as runner
from coupled_calibration_search import ContractError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nroy_fixtures as fx  # noqa: E402


def _unit(design_id: str = "D0", *, vector_hash: str = "vector-0") -> ne.WorkUnit:
    return ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY,
        model=nc.MODEL_M0,
        design_id=design_id,
        seed=910000,
        vector_hash=vector_hash,
        kernel=nc.KERNEL_AMBIENT,
        parent_ids=("A0",),
        params={},
        parent_manifest_sha256="parent",
        code_sha256="code",
        config_sha256="config",
        target_schema=("target",),
    )


def _ledger(*units: ne.WorkUnit) -> ne.Ledger:
    return ne.Ledger(
        stage=nc.STAGE_DISCOVERY,
        model=nc.MODEL_M0,
        units=tuple(units or (_unit(),)),
    )


def _prepared_cli_stage(tmp_path: Path) -> tuple[Path, Path]:
    observed = {name: 105.0 for name in ccs.FROZEN_TARGET_ORDER}
    variance = {name: 10000.0 for name in ccs.FROZEN_TARGET_ORDER}
    plan_path = fx.write_signed_plan(
        tmp_path, acceptance_observed=observed, acceptance_variance=variance
    )
    space = fx.m0_space()
    anchors = tmp_path / "anchors.csv"
    pd.DataFrame([
        {"candidate_id": f"A{index}", **vector}
        for index, vector in enumerate(
            fx.valid_interior_vectors(space, 2, seed=91)
        )
    ]).to_csv(anchors, index=False)
    stage = tmp_path / "stage"
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(stage),
        "--anchors", str(anchors), "--model", nc.MODEL_M0,
        "--stage", nc.STAGE_DISCOVERY,
    ]) == 0
    return plan_path, stage


def test_jsonl_reader_ignores_only_a_truncated_final_fragment(tmp_path):
    journal = tmp_path / "attempts.jsonl"
    first = {"attempt_id": "a1", "status": ne.ATTEMPT_RUNNING}
    second = {"attempt_id": "a1", "status": ne.ATTEMPT_SUCCESS}
    journal.write_bytes(
        (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
        + b'{"attempt_id":"torn'
    )

    assert ne.read_jsonl_records(journal) == [first, second]
    # A complete record is valid even if the process died before writing its
    # conventional trailing newline.
    journal.write_text(json.dumps(first))
    assert ne.read_jsonl_records(journal) == [first]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"attempt_id":"a1"}\n{"broken":\n{"attempt_id":"a2"}\n',
        b'{"attempt_id":"a1"}\n{"broken":\n',
    ],
)
def test_jsonl_reader_refuses_interior_or_terminated_corruption(tmp_path, payload):
    journal = tmp_path / "attempts.jsonl"
    journal.write_bytes(payload)
    with pytest.raises(ContractError, match="corrupt at durable line"):
        ne.read_jsonl_records(journal)


def test_truncated_tail_can_be_repaired_then_running_attempt_becomes_lost(tmp_path):
    ledger = _ledger()
    unit = ledger.units[0]
    running = ledger.record_attempt(ne.AttemptRecord(
        attempt_id=ledger.next_attempt_id(unit.design_id, unit.seed),
        design_id=unit.design_id,
        seed=unit.seed,
        status=ne.ATTEMPT_RUNNING,
        started_utc=100.0,
        host="dead-host",
        process="CPython-123",
        vector_hash=unit.vector_hash,
    ))
    ledger.save(tmp_path)
    journal = ledger.save_attempt_record(tmp_path, "shard_000_of_001", running)
    with journal.open("ab") as stream:
        stream.write(b'{"attempt_id":"torn')

    restored = ne.Ledger.load(tmp_path)
    assert [attempt.status for attempt in restored.attempts] == [ne.ATTEMPT_RUNNING]

    # The shard owner repairs the recoverable tail while holding its exclusive
    # lifetime lock, then appends the terminal event for the abandoned launch.
    ne.read_jsonl_records(journal, repair_truncated_tail=True)
    lost, = restored.reconcile_running_attempts_as_lost(
        lost_attempt_ids=[running.attempt_id], ended_utc=200.0
    )
    restored.save_attempt_record(tmp_path, "shard_000_of_001", lost)

    resumed = ne.Ledger.load(tmp_path)
    assert resumed.attempts[0].status == ne.ATTEMPT_LOST
    assert resumed.attempts[0].attempt_id == running.attempt_id
    assert resumed.consumed_attempts() == 1
    assert resumed.next_attempt_id(unit.design_id, unit.seed).endswith("a002")


def test_lost_reconciliation_requires_explicit_running_attempt_ids():
    ledger = _ledger()
    unit = ledger.units[0]
    success = ledger.record_attempt(ne.AttemptRecord(
        attempt_id="done",
        design_id=unit.design_id,
        seed=unit.seed,
        status=ne.ATTEMPT_SUCCESS,
        result_hash="result",
        vector_hash=unit.vector_hash,
    ))
    with pytest.raises(ContractError, match="only RUNNING"):
        ledger.reconcile_running_attempts_as_lost(
            lost_attempt_ids=[success.attempt_id]
        )
    with pytest.raises(ContractError, match="unknown attempt"):
        ledger.reconcile_running_attempts_as_lost(lost_attempt_ids=["invented"])


def test_corrupt_sqlite_acceleration_index_is_rebuilt_from_durable_ledger(tmp_path):
    first = _unit("D0", vector_hash="vector-0")
    second = _unit("D1", vector_hash="vector-1")
    ledger = _ledger(first, second)
    ledger.record_attempt(ne.AttemptRecord(
        attempt_id="lost",
        design_id=first.design_id,
        seed=first.seed,
        status=ne.ATTEMPT_LOST,
        vector_hash=first.vector_hash,
    ))
    ledger.record_attempt(ne.AttemptRecord(
        attempt_id="success",
        design_id=second.design_id,
        seed=second.seed,
        status=ne.ATTEMPT_SUCCESS,
        vector_hash=second.vector_hash,
        result_hash="result",
    ))
    database = tmp_path / "execution_dispatch.sqlite3"
    database.write_bytes(b"this is deliberately not a sqlite database")

    rebuilt = ne.rebuild_dispatch_index(database, ledger)
    assert rebuilt["consumed_attempts"] == 2
    assert rebuilt["consumed_trajectories"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT consumed_attempts FROM budget WHERE singleton=1"
        ).fetchone() == (2,)
        result_states = connection.execute(
            "SELECT consumed, active, successful FROM result_state ORDER BY result_key"
        ).fetchall()
        assert sorted(result_states) == [(1, 0, 0), (1, 0, 1)]


def test_durable_result_reuse_requires_original_exact_provenance_and_success():
    contract = nc.TargetContract(
        frozen_order=("target",), register_path="register", register_sha256="hash"
    )
    ledger = _ledger()
    unit = ledger.units[0]
    result = ne.SeedResult(
        design_id=unit.design_id,
        seed=unit.seed,
        stage=unit.stage,
        model=unit.model,
        vector_hash=unit.vector_hash,
        target_order=contract.frozen_order,
        values=(123.0,),
        attempt_id="success",
        code_sha256=unit.code_sha256,
        config_sha256=unit.config_sha256,
        parent_manifest_sha256=unit.parent_manifest_sha256,
    )
    ledger.record_attempt(ne.AttemptRecord(
        attempt_id=result.attempt_id,
        design_id=unit.design_id,
        seed=unit.seed,
        status=ne.ATTEMPT_SUCCESS,
        vector_hash=unit.vector_hash,
        result_hash=result.result_hash,
    ))
    restored = ne.seed_result_from_durable_row(result.as_row(), targets=contract)
    ne.validate_durable_seed_result(ledger, restored, targets=contract)

    stale = dict(result.as_row())
    stale["design_id"] = "STALE-DESIGN"
    stale["code_sha256"] = "STALE-CODE"
    # Keep its self-declared hash internally consistent: frozen-ledger and
    # attempt binding, not just a checksum, must still reject it.
    stale["result_hash"] = ne.SeedResult(
        design_id=stale["design_id"], seed=unit.seed, stage=unit.stage,
        model=unit.model, vector_hash=unit.vector_hash,
        target_order=contract.frozen_order, values=(123.0,),
        attempt_id="success", code_sha256="STALE-CODE",
        config_sha256=unit.config_sha256,
        parent_manifest_sha256=unit.parent_manifest_sha256,
    ).result_hash
    stale_result = ne.seed_result_from_durable_row(stale, targets=contract)
    with pytest.raises(ne.MergeRefused, match="extra ledger key"):
        ne.validate_durable_seed_result(ledger, stale_result, targets=contract)

    self_described = dict(result.as_row())
    self_described.update({
        "result_key": "WRONG",
        "target_order": ["target"],
        "params": {"tampered": 1.0},
    })
    with pytest.raises(ContractError, match="unexpected"):
        ne.seed_result_from_durable_row(self_described, targets=contract)


def test_only_exact_result_from_matching_lost_attempt_may_be_ignored():
    contract = nc.TargetContract(
        frozen_order=("target",), register_path="register", register_sha256="hash"
    )
    ledger = _ledger()
    unit = ledger.units[0]
    ledger.record_attempt(ne.AttemptRecord(
        attempt_id="lost",
        design_id=unit.design_id,
        seed=unit.seed,
        status=ne.ATTEMPT_LOST,
        vector_hash=unit.vector_hash,
    ))
    orphan = ne.SeedResult(
        design_id=unit.design_id, seed=unit.seed, stage=unit.stage, model=unit.model,
        vector_hash=unit.vector_hash, target_order=contract.frozen_order,
        values=(123.0,), attempt_id="lost", code_sha256=unit.code_sha256,
        config_sha256=unit.config_sha256,
        parent_manifest_sha256=unit.parent_manifest_sha256,
    )
    ne.validate_orphaned_lost_seed_result(ledger, orphan, targets=contract)

    borrowed_id = ne.SeedResult(
        design_id="STALE-DESIGN", seed=unit.seed, stage=unit.stage, model=unit.model,
        vector_hash=unit.vector_hash, target_order=contract.frozen_order,
        values=(123.0,), attempt_id="lost", code_sha256=unit.code_sha256,
        config_sha256=unit.config_sha256,
        parent_manifest_sha256=unit.parent_manifest_sha256,
    )
    with pytest.raises(ContractError, match="not an exact orphan"):
        ne.validate_orphaned_lost_seed_result(
            ledger, borrowed_id, targets=contract
        )


def test_cli_resume_repairs_truncated_tail_marks_running_lost_and_retries(tmp_path):
    plan_path, stage = _prepared_cli_stage(tmp_path)
    ledger = ne.Ledger.load(stage)
    target = ledger.units[0]
    shard_count = 100_000
    shard = ne.shard_of(target.result_key, shard_count)
    shard_label = f"shard_{shard:03d}_of_{shard_count:03d}"
    running = ne.AttemptRecord(
        attempt_id=ne.new_attempt_id(target.design_id, target.seed, 1),
        design_id=target.design_id,
        seed=target.seed,
        status=ne.ATTEMPT_RUNNING,
        started_utc=100.0,
        host="crashed-host",
        process="CPython-123",
        vector_hash=target.vector_hash,
    )
    journal = stage / ne.Ledger.ATTEMPTS_DIR / f"{shard_label}.jsonl"
    journal.parent.mkdir()
    journal.write_bytes(
        (json.dumps(running.as_row(), sort_keys=True) + "\n").encode()
        + b'{"attempt_id":"torn'
    )

    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", str(shard), "--of", str(shard_count),
        "--evaluator", "surrogate",
    ]) == 0
    restored = ne.Ledger.load(stage)
    statuses = [
        attempt.status
        for attempt in restored.attempts_for(target.design_id, target.seed)
    ]
    assert statuses == [ne.ATTEMPT_LOST, ne.ATTEMPT_SUCCESS]
    assert restored.consumed_attempts() == 2
    # The torn bytes were physically removed before the LOST/SUCCESS events
    # were appended, so the journal remains strictly readable on later resumes.
    ne.read_jsonl_records(journal)


def test_cli_recovers_a_corrupt_sqlite_cache_from_the_journal(tmp_path):
    plan_path, stage = _prepared_cli_stage(tmp_path)
    ledger = ne.Ledger.load(stage)
    target = ledger.units[0]
    shard_count = 100_000
    shard = ne.shard_of(target.result_key, shard_count)
    database = stage / "execution_dispatch.sqlite3"
    database.write_bytes(b"corrupt acceleration cache")

    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", str(shard), "--of", str(shard_count),
        "--evaluator", "surrogate",
    ]) == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_resume_refuses_a_different_shard_layout(tmp_path, capsys):
    plan_path, stage = _prepared_cli_stage(tmp_path)
    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "2", "--evaluator", "surrogate",
    ]) == 0
    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "3", "--evaluator", "surrogate",
    ]) == 1
    assert "different execution layout" in capsys.readouterr().err


def test_dispatch_cache_rebuild_reloads_journal_after_prelock_snapshot(
    tmp_path, monkeypatch,
):
    """A concurrent launch between initial load and lock acquisition stays charged."""
    plan_path, stage = _prepared_cli_stage(tmp_path)
    initial = ne.Ledger.load(stage)
    external_unit = initial.units[-1]
    injected = False
    original_resolve = runner.resolve_evaluator
    original_rebuild = ne.rebuild_dispatch_index

    def resolve_and_inject(*args, **kwargs):
        nonlocal injected
        if not injected:
            running = ne.AttemptRecord(
                attempt_id=ne.new_attempt_id(
                    external_unit.design_id, external_unit.seed, 1
                ),
                design_id=external_unit.design_id,
                seed=external_unit.seed,
                status=ne.ATTEMPT_RUNNING,
                started_utc=1.0,
                vector_hash=external_unit.vector_hash,
                host="external-host",
                process="external-process",
            )
            lost_row = {
                **running.as_row(),
                "status": ne.ATTEMPT_LOST,
                "ended_utc": 2.0,
                "failure_reason": "concurrent launch fixture",
                "failure_class": "LostWorker",
            }
            lost = ne.AttemptRecord(**lost_row)
            initial.save_attempt_record(stage, "external_shard", running)
            initial.save_attempt_record(stage, "external_shard", lost)
            injected = True
        return original_resolve(*args, **kwargs)

    def assert_fresh_rebuild(path, ledger):
        assert ledger.consumed_attempts() == 1, (
            "dispatch cache was rebuilt from the stale pre-lock ledger"
        )
        return original_rebuild(path, ledger)

    monkeypatch.setattr(runner, "resolve_evaluator", resolve_and_inject)
    monkeypatch.setattr(ne, "rebuild_dispatch_index", assert_fresh_rebuild)
    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "100000", "--evaluator", "surrogate",
    ]) == 0


def test_semantically_ambiguous_terminal_journal_transition_is_fatal(tmp_path):
    ledger = _ledger()
    unit = ledger.units[0]
    ledger.save(tmp_path)
    running = ne.AttemptRecord(
        attempt_id=ne.new_attempt_id(unit.design_id, unit.seed, 1),
        design_id=unit.design_id, seed=unit.seed, status=ne.ATTEMPT_RUNNING,
        started_utc=1.0, host="host", process="process",
        vector_hash=unit.vector_hash,
    )
    success_row = {
        **running.as_row(), "status": ne.ATTEMPT_SUCCESS,
        "ended_utc": 2.0, "result_hash": "result",
    }
    success = ne.AttemptRecord(**success_row)
    failed_row = {
        **running.as_row(), "status": ne.ATTEMPT_FAILED,
        "ended_utc": 3.0, "failure_reason": "contradictory terminal event",
    }
    failed_after_success = ne.AttemptRecord(**failed_row)
    for event in (running, success, failed_after_success):
        ledger.save_attempt_record(tmp_path, "shard_000_of_001", event)

    with pytest.raises(ContractError, match="ambiguous attempt transition"):
        ne.Ledger.load(tmp_path)


def test_cli_refuses_unrecorded_stale_durable_result_instead_of_rewrapping_it(tmp_path):
    plan_path, stage = _prepared_cli_stage(tmp_path)
    ledger = ne.Ledger.load(stage)
    target = ledger.units[0]
    shard_count = 100_000
    shard = ne.shard_of(target.result_key, shard_count)
    stale = ne.SeedResult(
        design_id="STALE-DESIGN",
        seed=target.seed,
        stage=target.stage,
        model=target.model,
        vector_hash=target.vector_hash,
        target_order=target.target_schema,
        values=tuple(123.0 for _ in target.target_schema),
        attempt_id="NO-RECORDED-SUCCESS",
        code_sha256="STALE-CODE",
        config_sha256="STALE-CONFIG",
        parent_manifest_sha256="STALE-PARENT",
    ).as_row()
    durable = stage / "results"
    durable.mkdir()
    (durable / f"{ccs.sha256_json({'unit': target.key_string})}.json").write_text(
        json.dumps(stale)
    )

    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", str(shard), "--of", str(shard_count),
        "--evaluator", "surrogate",
    ]) == 1
    restored = ne.Ledger.load(stage)
    assert not restored.attempts_for(target.design_id, target.seed)


def test_stage_manifest_seals_non_circular_exact_artifact_index(tmp_path):
    ledger = _ledger()
    ledger.save(tmp_path)
    written = ne.write_stage_manifest(
        tmp_path,
        stage=nc.STAGE_DISCOVERY,
        model=nc.MODEL_M0,
        status=nc.STATUS_PREPARED,
        ledger=ledger,
        contract={"target_order": ["target"], "seeds": {}},
        parent_manifest=None,
    )
    manifest = written["manifest_payload"]
    index = json.loads((tmp_path / "artifacts.json").read_text())
    indexed = {entry["path"] for entry in index["files"]}
    assert "manifest.json" not in indexed
    assert "artifacts.json" not in indexed
    assert manifest["artifacts_sha256"] == ccs.sha256_file(
        tmp_path / "artifacts.json"
    )
    ne.validate_artifact_index(tmp_path, require_exact=True)


def test_rewritten_index_cannot_launder_a_mutated_scientific_artifact(tmp_path):
    ledger = _ledger()
    ledger.save(tmp_path)
    ne.write_stage_manifest(
        tmp_path,
        stage=nc.STAGE_DISCOVERY,
        model=nc.MODEL_M0,
        status=nc.STATUS_PREPARED,
        ledger=ledger,
        contract={"target_order": ["target"], "seeds": {}},
        parent_manifest=None,
    )
    victim = tmp_path / "ledger_meta.json"
    victim.write_text('{"mutated":true}')
    index_path = tmp_path / "artifacts.json"
    index = json.loads(index_path.read_text())
    for entry in index["files"]:
        if entry["path"] == "ledger_meta.json":
            entry["sha256"] = ccs.sha256_file(victim)
            entry["bytes"] = victim.stat().st_size
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))

    with pytest.raises(ContractError, match="artifact index changed"):
        ne.validate_artifact_index(tmp_path, required_paths=["ledger_meta.json"])


def test_postmerge_scores_cannot_be_mutated_or_silently_reindexed(tmp_path):
    plan_path, stage = _prepared_cli_stage(tmp_path)
    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
    ]) == 0
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage),
    ]) == 0

    scores_path = stage / "candidate_scores.csv"
    scores = ne.read_frame_exact(scores_path)
    scores.loc[:, "nominal_pass"] = False
    scores.to_csv(scores_path, index=False)
    assert runner.main([
        "report", "--plan", str(plan_path), "--stage-dir", str(stage),
    ]) == 1

    # Rewriting the index to bless the edited table still cannot help: the
    # manifest seals the old index bytes.
    index_path = stage / "artifacts.json"
    index = json.loads(index_path.read_text())
    for entry in index["files"]:
        if entry["path"] == "candidate_scores.csv":
            entry["sha256"] = ccs.sha256_file(scores_path)
            entry["bytes"] = scores_path.stat().st_size
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    assert runner.main([
        "report", "--plan", str(plan_path), "--stage-dir", str(stage),
    ]) == 1
    assert not (stage / "package").exists()


def test_refused_manifest_overwrite_preserves_the_existing_index_seal(tmp_path):
    ledger = _ledger()
    ledger.save(tmp_path)
    ne.write_stage_manifest(
        tmp_path,
        stage=nc.STAGE_DISCOVERY,
        model=nc.MODEL_M0,
        status=nc.STATUS_PREPARED,
        ledger=ledger,
        contract={"target_order": ["target"], "seeds": {}},
        parent_manifest=None,
    )
    original_index = (tmp_path / "artifacts.json").read_bytes()
    with pytest.raises(ContractError, match="refusing to overwrite"):
        ne.write_stage_manifest(
            tmp_path,
            stage=nc.STAGE_DISCOVERY,
            model=nc.MODEL_M0,
            status=nc.STATUS_COMPLETE,
            ledger=ledger,
            contract={"target_order": ["target"], "seeds": {}},
            parent_manifest=None,
        )
    assert (tmp_path / "artifacts.json").read_bytes() == original_index
    ne.validate_artifact_index(tmp_path, require_exact=True)


def test_handoff_audit_refuses_observed_kernel_without_explicit_quota():
    with pytest.raises(ContractError, match="no explicit quota"):
        ne.handoff_audit(
            proposed={nc.KERNEL_BRIDGE: 2},
            rejected={},
            dispatched={nc.KERNEL_BRIDGE: 2},
            completed={nc.KERNEL_BRIDGE: 2},
            promoted={nc.KERNEL_BRIDGE: 1},
            confirmed={nc.KERNEL_BRIDGE: 1},
            quotas={},
        )

