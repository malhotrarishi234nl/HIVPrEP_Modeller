"""Sharding, handoff and crash-resume equivalence tests.

Covers the "Sharding and handoff" block of
``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md`` section 9.  The governing requirement
is that distributed execution be *scientifically identical* to a single-process
reference run, so most tests here assert equality of a merged table against a
reference, and the rest assert that each listed defect **fails closed** rather
than degrading quietly.

No simulation engine is constructed and no trajectory is run.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402

import coupled_calibration_search as ccs  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402

TARGETS = ccs.FROZEN_TARGET_ORDER
CONTRACT = nc.TargetContract(
    frozen_order=TARGETS, register_path="x", register_sha256="h"
)
DISCOVERY_SEEDS = [910000, 910001, 910002, 910003, 910004]


# ==========================================================================
# Helpers
# ==========================================================================

def build_design(n_anchors: int = 3, quota: int = 4, model=None, seed: int = 31):
    """A small multi-kernel design over several anchors."""
    space = model or fx.m0_space()
    validator = nd.Validator(space=space.space, model=space)
    anchors = [
        nd.Anchor.build(f"A{i}", v, space)
        for i, v in enumerate(fx.valid_interior_vectors(space, n_anchors, seed=seed))
    ]
    proposals: list[nd.Proposal] = []
    outcomes: list[nd.KernelOutcome] = []
    for anchor in anchors:
        offset = 0
        for scale in (0.025, 0.05):
            out = nd.ambient_probes(
                anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=scale,
                quota=quota, generator="gaussian_unit_sphere", scale_semantics="radius",
                seed=seed, min_anchor_sep=1e-6, min_neighbour_sep=1e-6,
                max_attempts=2000, index_offset=offset,
            )
            outcomes.append(out)
            proposals.extend(out.accepted)
            offset += 1000
        cross = nd.cross_block_directions(anchor, space, seed=seed, count=quota)
        out = nd.directed_probes(
            anchor, space, validator, stage=nc.STAGE_DISCOVERY,
            kernel=nc.KERNEL_CROSS_BLOCK, directions=cross, scales=[0.025],
            quota=quota, seed=seed, min_anchor_sep=1e-6, min_neighbour_sep=1e-6,
            max_attempts=2000, index_offset=offset,
        )
        outcomes.append(out)
        proposals.extend(out.accepted)
    return space, anchors, proposals, outcomes


def make_ledger(proposals, seeds=None, *, model=nc.MODEL_M0, stage=nc.STAGE_DISCOVERY):
    units = fx.work_units(
        proposals, seeds or DISCOVERY_SEEDS, stage=stage, model=model, targets=TARGETS
    )
    return ne.Ledger(stage=stage, model=model, units=units)


def field_for(space):
    return fx.SyntheticField("broad_basin", [np.full(space.n, 0.5)], noise=0.01)


def evaluate(ledger, units, space, vectors, *, fail=()):
    return fx.evaluate_units(
        units, field_for(space), space, TARGETS, vectors=vectors, ledger=ledger, fail=fail
    )


def reference_merge(proposals, space):
    """The single-process reference run."""
    vectors = {p.design_id: p.params for p in proposals}
    ledger = make_ledger(proposals)
    results = evaluate(ledger, list(ledger.units), space, vectors)
    return ledger, ne.merge_seed_results(ledger, results, targets=CONTRACT), vectors


# ==========================================================================
# Shard equivalence
# ==========================================================================

@pytest.mark.parametrize("n_shards", [1, 2, 3, 4, 5, 7, 11])
def test_shards_partition_the_ledger_exactly(n_shards):
    space, _, proposals, _ = build_design()
    ledger = make_ledger(proposals)
    ledger.assert_shard_partition(n_shards)


@pytest.mark.parametrize("n_shards", [2, 3, 4, 7])
def test_sharded_merge_is_identical_to_single_process(n_shards):
    space, _, proposals, _ = build_design()
    _, reference, vectors = reference_merge(proposals, space)

    ledger = make_ledger(proposals)
    rows = []
    for shard in range(n_shards):
        assigned = ledger.rows_for_shard(shard, n_shards)
        rows.extend(evaluate(ledger, assigned, space, vectors))
    merged = ne.merge_seed_results(ledger, rows, targets=CONTRACT)
    pd.testing.assert_frame_equal(merged, reference)


def test_uneven_shards_are_still_identical():
    """Shard counts that do not divide the work must not change the science."""
    space, _, proposals, _ = build_design(n_anchors=3, quota=5)
    _, reference, vectors = reference_merge(proposals, space)
    ledger = make_ledger(proposals)
    assert len(ledger.units) % 7 != 0, "this test needs an indivisible work count"
    rows = []
    sizes = []
    for shard in range(7):
        assigned = ledger.rows_for_shard(shard, 7)
        sizes.append(len(assigned))
        rows.extend(evaluate(ledger, assigned, space, vectors))
    assert len(set(sizes)) > 1, "shards should genuinely be uneven here"
    merged = ne.merge_seed_results(ledger, rows, targets=CONTRACT)
    pd.testing.assert_frame_equal(merged, reference)


def test_shuffled_arrival_order_leaves_the_merge_unchanged():
    space, _, proposals, _ = build_design()
    ledger, reference, vectors = reference_merge(proposals, space)
    results = evaluate(make_ledger(proposals), list(ledger.units), space, vectors)
    for seed in (1, 7, 99):
        shuffled = list(results)
        random.Random(seed).shuffle(shuffled)
        merged = ne.merge_seed_results(ledger, shuffled, targets=CONTRACT)
        pd.testing.assert_frame_equal(merged, reference)


def test_recomputed_scores_are_identical_across_shard_counts():
    space, _, proposals, _ = build_design()
    observed, base_variance = fx.observed_and_variance(TARGETS)
    _, reference, vectors = reference_merge(proposals, space)
    reference_scores = ne.recompute_candidate_scores(
        reference, targets=CONTRACT, observed=observed, base_variance=base_variance,
        cutoff=3.0, variance_alpha=0.05,
    )
    for n_shards in (2, 3, 7):
        ledger = make_ledger(proposals)
        rows = []
        for shard in range(n_shards):
            rows.extend(evaluate(ledger, ledger.rows_for_shard(shard, n_shards), space, vectors))
        merged = ne.merge_seed_results(ledger, rows, targets=CONTRACT)
        scores = ne.recompute_candidate_scores(
            merged, targets=CONTRACT, observed=observed, base_variance=base_variance,
            cutoff=3.0, variance_alpha=0.05,
        )
        pd.testing.assert_frame_equal(scores, reference_scores)


def test_changing_worker_count_does_not_change_the_design():
    """Assignment is a pure function of the key and the shard count."""
    space, _, proposals, _ = build_design()
    ledger = make_ledger(proposals)
    for n_shards in (2, 3, 7):
        first = [
            {u.key_string for u in ledger.rows_for_shard(s, n_shards)}
            for s in range(n_shards)
        ]
        second = [
            {u.key_string for u in ledger.rows_for_shard(s, n_shards)}
            for s in range(n_shards)
        ]
        assert first == second
    # The frozen ledger hash is independent of any sharding.
    assert ledger.frozen_sha256 == make_ledger(proposals).frozen_sha256


@pytest.mark.parametrize("n_shards", [2, 3, 4, 7])
def test_each_kernel_vector_set_survives_sharding_and_merge(n_shards):
    """Per-kernel vector sets must be identical before and after the merge."""
    space, _, proposals, _ = build_design(n_anchors=3, quota=5)
    before: dict[str, set[str]] = {}
    for proposal in proposals:
        before.setdefault(proposal.kernel, set()).add(proposal.vector_hash)

    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    rows = []
    for shard in range(n_shards):
        rows.extend(evaluate(ledger, ledger.rows_for_shard(shard, n_shards), space, vectors))
    merged = ne.merge_seed_results(ledger, rows, targets=CONTRACT)

    kernel_of = {u.design_id: u.kernel for u in ledger.units}
    after: dict[str, set[str]] = {}
    for _, row in merged.iterrows():
        after.setdefault(kernel_of[row["design_id"]], set()).add(row["vector_hash"])
    assert after == before


def test_quotas_are_rechecked_after_merge():
    """Quotas are checked before dispatch and again after merge (spec sec. 3)."""
    space, anchors, proposals, outcomes = build_design(n_anchors=2, quota=4)
    quotas = {nc.KERNEL_AMBIENT: 8 * len(anchors), nc.KERNEL_CROSS_BLOCK: 4 * len(anchors)}
    nd.assert_quotas(outcomes, quotas, what="before dispatch")

    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    merged = ne.merge_seed_results(ledger, results, targets=CONTRACT)
    kernel_of = {u.design_id: u.kernel for u in ledger.units}
    survived: dict[str, set[str]] = {}
    for _, row in merged.iterrows():
        survived.setdefault(kernel_of[row["design_id"]], set()).add(row["design_id"])
    for kernel, quota in quotas.items():
        assert len(survived[kernel]) == quota


# ==========================================================================
# Fail-closed defects
# ==========================================================================

def test_a_missing_shard_is_not_zero_output_and_not_a_rejected_candidate():
    space, _, proposals, _ = build_design()
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    rows = []
    for shard in range(3):
        if shard == 1:
            continue  # this worker never reported
        rows.extend(evaluate(ledger, ledger.rows_for_shard(shard, 3), space, vectors))
    with pytest.raises(ne.MergeRefused) as excinfo:
        ne.merge_seed_results(ledger, rows, targets=CONTRACT)
    message = str(excinfo.value)
    assert "have no result" in message
    assert "not zero output and not a rejected candidate" in message


def test_wrong_parameter_vector_fails_closed():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    tampered = [
        ne.SeedResult(**{**r.__dict__, "vector_hash": "STALE"}) if i == 0 else r
        for i, r in enumerate(results)
    ]
    with pytest.raises(ne.MergeRefused, match="parameter hash mismatch"):
        ne.merge_seed_results(ledger, tampered, targets=CONTRACT)


def test_wrong_seed_fails_closed():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    tampered = [ne.SeedResult(**{**results[0].__dict__, "seed": 999999})] + results[1:]
    with pytest.raises(ne.MergeRefused, match="extra ledger key"):
        ne.merge_seed_results(ledger, tampered, targets=CONTRACT)


def test_silently_reordered_targets_fail_closed():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    flipped = tuple(reversed(TARGETS))
    tampered = [
        ne.SeedResult(**{**results[0].__dict__, "target_order": flipped})
    ] + results[1:]
    with pytest.raises(ne.MergeRefused, match="silently reordered"):
        ne.merge_seed_results(ledger, tampered, targets=CONTRACT)


def test_duplicate_rows_are_refused_even_when_values_agree():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    with pytest.raises(ne.MergeRefused, match="duplicate key"):
        ne.merge_seed_results(ledger, results + [results[0]], targets=CONTRACT)


def test_mixed_code_or_config_versions_are_refused():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    results = evaluate(ledger, list(ledger.units), space, vectors)
    for field, message in (("code_sha256", "mixed code"), ("config_sha256", "mixed config")):
        tampered = [
            ne.SeedResult(**{**results[0].__dict__, field: "OTHER"})
        ] + results[1:]
        with pytest.raises(ne.MergeRefused, match=message):
            ne.merge_seed_results(ledger, tampered, targets=CONTRACT)


def test_a_failed_attempt_cannot_masquerade_as_a_result():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    units = list(ledger.units)
    failing = (units[0].design_id, int(units[0].seed))
    results = evaluate(ledger, units, space, vectors, fail=[failing])
    # The worker recorded a failure, but something supplies a result anyway.
    forged = ne.SeedResult(
        design_id=units[0].design_id, seed=int(units[0].seed), stage=units[0].stage,
        model=units[0].model, vector_hash=units[0].vector_hash, target_order=TARGETS,
        values=tuple(1.0 for _ in TARGETS), attempt_id="forged",
        code_sha256=units[0].code_sha256, config_sha256=units[0].config_sha256,
    )
    with pytest.raises(ne.MergeRefused, match="masquerade"):
        ne.merge_seed_results(ledger, results + [forged], targets=CONTRACT)


def test_an_execution_failure_leaves_the_unit_incomplete():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    units = list(ledger.units)
    failing = (units[0].design_id, int(units[0].seed))
    evaluate(ledger, units, space, vectors, fail=[failing])
    incomplete = {(u.design_id, int(u.seed)) for u in ledger.incomplete_units()}
    assert failing in incomplete
    assert not ledger.is_complete()


def test_a_worker_may_not_invent_work():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    with pytest.raises(ContractError, match="may not invent work"):
        ledger.record_attempt(ne.AttemptRecord(
            attempt_id="rogue", design_id="NOT_IN_LEDGER", seed=910000,
            status=ne.ATTEMPT_SUCCESS,
        ))


def test_a_design_id_must_resolve_to_exactly_one_vector():
    unit_a = ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id="D1", seed=1,
        vector_hash="h1", kernel=nc.KERNEL_AMBIENT, parent_ids=("A",), params={},
        parent_manifest_sha256="p", code_sha256="c", config_sha256="g",
        target_schema=TARGETS,
    )
    unit_b = ne.WorkUnit(**{**unit_a.__dict__, "seed": 2, "vector_hash": "h2"})
    with pytest.raises(ContractError, match="resolves to two different vectors"):
        ne.Ledger(stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, units=(unit_a, unit_b))


def test_a_vector_reused_by_two_designs_is_simulated_once():
    """Reuse links to one scientific result, not a duplicate simulation."""
    shared = "shared_vector_hash"
    units = tuple(
        ne.WorkUnit(
            stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id=design, seed=seed,
            vector_hash=shared, kernel=nc.KERNEL_AMBIENT, parent_ids=(parent,), params={},
            parent_manifest_sha256="p", code_sha256="c", config_sha256="g",
            target_schema=TARGETS,
        )
        for design, parent in (("D1", "A1"), ("D2", "A2"))
        for seed in (910000, 910001)
    )
    ledger = ne.Ledger(stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, units=units)
    groups = ledger.unique_result_keys()
    assert len(groups) == 2, "two seeds x one vector = two scientific results"
    for sharing in groups.values():
        assert {u.design_id for u in sharing} == {"D1", "D2"}
    # Both design ids land in the same shard, so neither is simulated twice.
    for n_shards in (2, 3, 7):
        for shard in range(n_shards):
            assigned = ledger.rows_for_shard(shard, n_shards)
            by_key: dict[str, set[str]] = {}
            for unit in assigned:
                by_key.setdefault(unit.result_key, set()).add(unit.design_id)
            for designs in by_key.values():
                assert designs == {"D1", "D2"}


# ==========================================================================
# Confirmation must not consume discovery summaries
# ==========================================================================

def test_confirmation_cannot_consume_discovery_summaries():
    """Confirmation must score from raw seed rows, never from a summary table."""
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    observed, base_variance = fx.observed_and_variance(TARGETS)
    ledger, seed_frame, _ = reference_merge(proposals, space)
    summary = ne.recompute_candidate_scores(
        seed_frame, targets=CONTRACT, observed=observed, base_variance=base_variance,
        cutoff=3.0,
    )
    # A summary table has mean:/var: columns but no per-seed rows.
    with pytest.raises(ContractError, match="missing target columns"):
        ne.recompute_candidate_scores(
            summary, targets=CONTRACT, observed=observed,
            base_variance=base_variance, cutoff=3.0,
        )


def test_confirmation_requires_a_common_replicate_count():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    observed, base_variance = fx.observed_and_variance(TARGETS)
    _, seed_frame, _ = reference_merge(proposals, space)
    design_ids = sorted(seed_frame["design_id"].unique())
    # Drop one seed from a single candidate: anchor and neighbours no longer
    # share the confirmation block.
    trimmed = seed_frame.drop(
        seed_frame[(seed_frame["design_id"] == design_ids[0])
                   & (seed_frame["seed"] == DISCOVERY_SEEDS[-1])].index
    )
    with pytest.raises(ContractError, match="differing replicate counts"):
        ne.recompute_candidate_scores(
            trimmed, targets=CONTRACT, observed=observed,
            base_variance=base_variance, cutoff=3.0,
        )


# ==========================================================================
# Persistence, stage handoff and lineage
# ==========================================================================

def test_ledger_round_trips_and_detects_tampering(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=2, quota=3)
    ledger = make_ledger(proposals)
    ledger.save(tmp_path)
    restored = ne.Ledger.load(tmp_path)
    assert restored.frozen_sha256 == ledger.frozen_sha256
    assert {u.key_string for u in restored.units} == {u.key_string for u in ledger.units}

    # Tampering with a single seed row breaks the "one design_id, one vector"
    # invariant and is caught before the hash is even recomputed.
    units = pd.read_csv(tmp_path / "ledger_units.csv")
    units.loc[0, "vector_hash"] = "TAMPERED"
    units.to_csv(tmp_path / "ledger_units.csv", index=False)
    with pytest.raises(ContractError, match="resolves to two different vectors"):
        ne.Ledger.load(tmp_path)

    # Tampering *consistently* across every seed of one design keeps that
    # invariant, so the frozen-hash check is what catches it.
    units = ledger.unit_frame().copy()
    target = units.loc[0, "design_id"]
    units.loc[units["design_id"] == target, "vector_hash"] = "CONSISTENTLY_TAMPERED"
    units.to_csv(tmp_path / "ledger_units.csv", index=False)
    with pytest.raises(ContractError, match="does not match its frozen hash"):
        ne.Ledger.load(tmp_path)


def test_atomic_write_refuses_to_overwrite_a_completed_artifact(tmp_path):
    path = tmp_path / "result.json"
    ne.atomic_write_json(path, {"a": 1})
    with pytest.raises(ContractError, match="refusing to overwrite"):
        ne.atomic_write_json(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}
    ne.atomic_write_json(path, {"a": 2}, overwrite_completed=True)
    assert json.loads(path.read_text()) == {"a": 2}


def test_atomic_write_leaves_no_partial_file_on_failure(tmp_path):
    path = tmp_path / "out.csv"

    class Exploding:
        def to_csv(self, index=False):
            raise RuntimeError("serialisation failed")

    with pytest.raises(RuntimeError):
        ne.atomic_write_frame(path, Exploding())
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_a_partial_stage_cannot_become_a_parent(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    units = list(ledger.units)
    evaluate(ledger, units[:-1], space, vectors)  # one unit never runs
    ledger.save(tmp_path)
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, status=nc.STATUS_PARTIAL,
        ledger=ledger, contract={"target_order": list(TARGETS), "seeds": {}},
        parent_manifest=None,
    )
    with pytest.raises(ContractError, match="cannot parent the next stage"):
        ne.validate_parent_stage(
            tmp_path, expected_stage=nc.STAGE_DISCOVERY, expected_model=nc.MODEL_M0,
            targets=CONTRACT,
            seeds=nc.SeedRegistry(
                discovery=tuple(DISCOVERY_SEEDS),
                confirmation=tuple(range(910100, 910120)), proposal_rng=910300,
            ),
            child_stage=nc.STAGE_PROMOTION,
        )


def test_a_complete_stage_validates_as_a_parent(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    ledger.save(tmp_path)
    seeds = nc.SeedRegistry(
        discovery=tuple(DISCOVERY_SEEDS), confirmation=tuple(range(910100, 910120)),
        proposal_rng=910300,
    )
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, status=nc.STATUS_COMPLETE,
        ledger=ledger,
        contract={"target_order": list(TARGETS), "seeds": seeds.as_dict()},
        parent_manifest=None,
    )
    manifest = ne.validate_parent_stage(
        tmp_path, expected_stage=nc.STAGE_DISCOVERY, expected_model=nc.MODEL_M0,
        targets=CONTRACT, seeds=seeds, child_stage=nc.STAGE_PROMOTION,
    )
    assert manifest["parentable"] is True


def test_parent_validation_detects_a_changed_artifact(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    ledger.save(tmp_path)
    seeds = nc.SeedRegistry(
        discovery=tuple(DISCOVERY_SEEDS), confirmation=tuple(range(910100, 910120)),
        proposal_rng=910300,
    )
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, status=nc.STATUS_COMPLETE,
        ledger=ledger, contract={"target_order": list(TARGETS), "seeds": seeds.as_dict()},
        parent_manifest=None,
    )
    (tmp_path / "ledger_meta.json").write_text('{"tampered": true}')
    with pytest.raises(ContractError, match="changed since the parent stage completed"):
        ne.validate_parent_stage(
            tmp_path, expected_stage=nc.STAGE_DISCOVERY, expected_model=nc.MODEL_M0,
            targets=CONTRACT, seeds=seeds, child_stage=nc.STAGE_PROMOTION,
        )


def test_a_stage_may_only_parent_its_exact_declared_successor(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    ledger.save(tmp_path)
    seeds = nc.SeedRegistry(
        discovery=tuple(DISCOVERY_SEEDS), confirmation=tuple(range(910100, 910120)),
        proposal_rng=910300,
    )
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, status=nc.STATUS_COMPLETE,
        ledger=ledger, contract={"target_order": list(TARGETS), "seeds": seeds.as_dict()},
        parent_manifest=None,
    )
    # discovery is not the declared parent of bridges.
    with pytest.raises(ContractError, match="requires parent"):
        ne.validate_parent_stage(
            tmp_path, expected_stage=nc.STAGE_DISCOVERY, expected_model=nc.MODEL_M0,
            targets=CONTRACT, seeds=seeds, child_stage=nc.STAGE_BRIDGES,
        )


def test_parent_with_a_different_target_order_is_refused(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    ledger.save(tmp_path)
    seeds = nc.SeedRegistry(
        discovery=tuple(DISCOVERY_SEEDS), confirmation=tuple(range(910100, 910120)),
        proposal_rng=910300,
    )
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, status=nc.STATUS_COMPLETE,
        ledger=ledger,
        contract={"target_order": list(reversed(TARGETS)), "seeds": seeds.as_dict()},
        parent_manifest=None,
    )
    with pytest.raises(ContractError, match="different target order"):
        ne.validate_parent_stage(
            tmp_path, expected_stage=nc.STAGE_DISCOVERY, expected_model=nc.MODEL_M0,
            targets=CONTRACT, seeds=seeds, child_stage=nc.STAGE_PROMOTION,
        )


def test_confirmation_seeds_overlapping_discovery_are_refused(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    ledger.save(tmp_path)
    ne.write_stage_manifest(
        tmp_path, stage=nc.STAGE_PROMOTION, model=nc.MODEL_M0, status=nc.STATUS_COMPLETE,
        ledger=ledger,
        contract={"target_order": list(TARGETS), "seeds": {"discovery": [910100, 910101]}},
        parent_manifest=None,
    )
    seeds = nc.SeedRegistry(
        discovery=tuple(DISCOVERY_SEEDS), confirmation=tuple(range(910100, 910120)),
        proposal_rng=910300,
    )
    with pytest.raises(ContractError, match="overlap the parent's discovery seeds"):
        ne.validate_parent_stage(
            tmp_path, expected_stage=nc.STAGE_PROMOTION, expected_model=nc.MODEL_M0,
            targets=CONTRACT, seeds=seeds, child_stage=nc.STAGE_CONFIRMATION,
        )


def test_lineage_must_resolve_to_known_parents():
    space, anchors, proposals, _ = build_design(n_anchors=2, quota=2)
    ledger = make_ledger(proposals)
    ne.assert_lineage(ledger.units, known_parent_ids=[a.anchor_id for a in anchors])
    with pytest.raises(ContractError, match="lineage broken"):
        ne.assert_lineage(ledger.units, known_parent_ids=["SOMETHING_ELSE"])


def test_handoff_audit_enforces_count_conservation():
    quotas = {nc.KERNEL_AMBIENT: 8}
    audit = ne.handoff_audit(
        proposed={nc.KERNEL_AMBIENT: 12}, rejected={nc.KERNEL_AMBIENT: 4},
        dispatched={nc.KERNEL_AMBIENT: 8}, completed={nc.KERNEL_AMBIENT: 8},
        promoted={nc.KERNEL_AMBIENT: 5}, confirmed={nc.KERNEL_AMBIENT: 3},
        quotas=quotas,
    )
    assert int(audit.loc[0, "n_confirmed"]) == 3

    with pytest.raises(ContractError, match="exceeds dispatched"):
        ne.handoff_audit(
            proposed={nc.KERNEL_AMBIENT: 12}, rejected={nc.KERNEL_AMBIENT: 4},
            dispatched={nc.KERNEL_AMBIENT: 8}, completed={nc.KERNEL_AMBIENT: 9},
            promoted={}, confirmed={}, quotas=quotas,
        )
    with pytest.raises(ContractError, match="entirely missing|produced nothing"):
        ne.handoff_audit(
            proposed={}, rejected={}, dispatched={}, completed={}, promoted={},
            confirmed={}, quotas={nc.KERNEL_CROSS_BLOCK: 4},
        )


# ==========================================================================
# Crash / resume
# ==========================================================================

def test_interrupted_and_resumed_run_matches_an_uninterrupted_reference():
    space, _, proposals, _ = build_design(n_anchors=2, quota=3)
    _, reference, vectors = reference_merge(proposals, space)

    ledger = make_ledger(proposals)
    units = list(ledger.units)
    cut = len(units) // 3
    first = evaluate(ledger, units[:cut], space, vectors)

    # Crash: everything in flight is lost.  Those attempts stay spent.
    for unit in units[cut:cut + 3]:
        ledger.record_attempt(ne.AttemptRecord(
            attempt_id=ledger.next_attempt_id(unit.design_id, unit.seed),
            design_id=unit.design_id, seed=int(unit.seed), status=ne.ATTEMPT_LOST,
            vector_hash=unit.vector_hash, failure_reason="worker lost",
            failure_class="LostWorker",
        ))
    decision = ne.plan_resume(ledger, max_attempts=100000, max_trajectories=100000)
    assert decision.status == nc.STATUS_PARTIAL
    assert not decision.parentable

    remaining = ledger.incomplete_units()
    second = evaluate(ledger, remaining, space, vectors)
    merged = ne.merge_seed_results(ledger, first + second, targets=CONTRACT)

    pd.testing.assert_frame_equal(
        merged.drop(columns=["attempt_id"]), reference.drop(columns=["attempt_id"])
    )
    assert ne.plan_resume(
        ledger, max_attempts=100000, max_trajectories=100000
    ).status == nc.STATUS_COMPLETE
    # Lost work is explicitly accounted for, not silently forgotten.
    assert sum(1 for a in ledger.attempts if a.status == ne.ATTEMPT_LOST) == 3
    assert ledger.consumed_attempts() == len(units) + 3


def test_a_retry_changes_only_the_attempt_id():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    unit = ledger.units[0]
    ledger.record_attempt(ne.AttemptRecord(
        attempt_id=ledger.next_attempt_id(unit.design_id, unit.seed),
        design_id=unit.design_id, seed=int(unit.seed), status=ne.ATTEMPT_FAILED,
        vector_hash=unit.vector_hash,
    ))
    retry = ne.assert_retry_preserves_key(
        ledger, unit.design_id, int(unit.seed), vector_hash=unit.vector_hash
    )
    assert retry.endswith("a002")
    assert unit.design_id in retry
    with pytest.raises(ContractError, match="only attempt_id may change"):
        ne.assert_retry_preserves_key(
            ledger, unit.design_id, int(unit.seed), vector_hash="DIFFERENT"
        )


def test_a_completed_work_unit_is_never_rerun():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units), space, vectors)
    unit = ledger.units[0]
    with pytest.raises(ContractError, match="already complete"):
        ne.assert_retry_preserves_key(
            ledger, unit.design_id, int(unit.seed), vector_hash=unit.vector_hash
        )
    with pytest.raises(ContractError, match="already has a successful result"):
        ledger.record_attempt(ne.AttemptRecord(
            attempt_id="second-success", design_id=unit.design_id, seed=int(unit.seed),
            status=ne.ATTEMPT_SUCCESS, vector_hash=unit.vector_hash,
        ))


def test_exhausted_budget_yields_a_non_parent_partial_stage():
    space, _, proposals, _ = build_design(n_anchors=2, quota=3)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    units = list(ledger.units)
    evaluate(ledger, units[:4], space, vectors)
    decision = ne.plan_resume(ledger, max_attempts=4, max_trajectories=4)
    assert decision.status == nc.STATUS_PARTIAL
    assert not decision.parentable
    assert decision.relaunchable == ()
    assert "budget exhausted" in decision.reason
    assert decision.remaining_attempts == 0


def test_attempts_consume_budget_and_are_never_refunded():
    space, _, proposals, _ = build_design(n_anchors=1, quota=2)
    ledger = make_ledger(proposals)
    unit = ledger.units[0]
    for status in (ne.ATTEMPT_FAILED, ne.ATTEMPT_LOST):
        ledger.record_attempt(ne.AttemptRecord(
            attempt_id=ledger.next_attempt_id(unit.design_id, unit.seed),
            design_id=unit.design_id, seed=int(unit.seed), status=status,
            vector_hash=unit.vector_hash,
        ))
    assert ledger.consumed_attempts() == 2
    decision = ne.plan_resume(ledger, max_attempts=10, max_trajectories=10)
    assert decision.remaining_attempts == 8


def test_resume_restores_the_frozen_design_and_never_regenerates_it(tmp_path):
    space, _, proposals, _ = build_design(n_anchors=2, quota=3)
    ledger = make_ledger(proposals)
    vectors = {p.design_id: p.params for p in proposals}
    evaluate(ledger, list(ledger.units)[:5], space, vectors)
    ledger.design_rng_state = {"seed": 910300, "note": "frozen design RNG"}
    ledger.save(tmp_path)

    restored = ne.Ledger.load(tmp_path)
    assert restored.frozen_sha256 == ledger.frozen_sha256
    assert restored.design_rng_state["seed"] == 910300
    assert {u.vector_hash for u in restored.units} == {u.vector_hash for u in ledger.units}
    assert len(restored.completed_keys()) == 5


# ==========================================================================
# Float fidelity of the frozen artifacts
# ==========================================================================

def test_csv_round_trip_preserves_parameter_vectors_bit_for_bit(tmp_path):
    """pandas' default CSV reader is NOT exact; the exact reader must be used.

    ``read_csv`` uses a fast float converter that can return a value one ulp
    away from what was written. A perturbed coordinate changes the vector hash,
    so every frozen vector read must go through ``read_frame_exact``.
    """
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 6, seed=1234)
    frame = pd.DataFrame([{"design_id": f"D{i}", **v} for i, v in enumerate(vectors)])
    path = tmp_path / "design_vectors.csv"
    ne.atomic_write_frame(path, frame)

    restored = ne.read_frame_exact(path).set_index("design_id")
    for index, original in enumerate(vectors):
        row = restored.loc[f"D{index}"]
        recovered = {name: float(row[name]) for name in space.names}
        assert nc.vector_hash(recovered, names=space.names) == nc.vector_hash(
            original, names=space.names
        ), "the frozen vector did not survive the CSV round trip"


def test_the_default_pandas_reader_is_demonstrably_unsafe_here(tmp_path):
    """Guard the reason ``read_frame_exact`` exists, so nobody 'simplifies' it."""
    value = 0.1234567890123456789
    path = tmp_path / "v.csv"
    ne.atomic_write_frame(path, pd.DataFrame([{"x": value}]))
    exact = float(ne.read_frame_exact(path).loc[0, "x"])
    assert exact == float(value)
    default = float(pd.read_csv(path).loc[0, "x"])
    if default == exact:
        pytest.skip("this pandas build's default reader happens to be exact")
    assert nc.canonical_float(default) != nc.canonical_float(exact)


def test_empty_strings_round_trip_as_empty_not_nan(tmp_path):
    path = tmp_path / "x.csv"
    ne.atomic_write_frame(path, pd.DataFrame([{"a": "", "b": "x"}]))
    restored = ne.read_frame_exact(path)
    assert restored.loc[0, "a"] == ""
    assert str(restored.loc[0, "a"]) != "nan"
