"""Contract, design and scoring tests for the robust-NROY freeze stage.

Covers the "Design and scoring" block of ``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md``
section 9.  Nothing in this module constructs a simulation engine or runs a
trajectory: the simulator is replaced by the closed-form synthetic fields in
``nroy_fixtures``, which is what lets the correct local-support label be
asserted rather than merely a self-consistent one.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402
from nroy_fixtures import ROOT  # noqa: E402

import coupled_calibration_search as ccs  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402


# ==========================================================================
# Fail-closed configuration
# ==========================================================================

def test_shipped_production_plan_fails_closed():
    """The plan in the repository must refuse to load while unsigned."""
    with pytest.raises(nc.UnsignedConfigError) as excinfo:
        nc.FrozenPlan.load(fx.PRODUCTION_PLAN, root=ROOT)
    assert len(excinfo.value.unsigned) >= 30
    assert "no simulation may start" in str(excinfo.value)


def test_every_required_decision_is_reported_not_just_the_first():
    raw = json.loads(fx.PRODUCTION_PLAN.read_text())
    pending = {item.path for item in nc.unsigned_values(raw)}
    for path in (
        "target_contract.frozen_order", "p_parameter.min", "p_parameter.max",
        "budget.max_trajectories", "ambient.quota_per_scale", "graph.bridge_fractions",
        "representative_selection.rule", "seeds.discovery", "seeds.confirmation",
        "promotion.variance_alpha", "separation.min_direction_classes",
    ):
        assert path in pending, f"{path} must be reported as unsigned"


@pytest.mark.parametrize("placeholder", ["UNSIGNED", "TBD", "todo", "PROPOSED", "", None])
def test_placeholder_spellings_all_count_as_unsigned(tmp_path, placeholder):
    plan = fx.signed_plan_dict()
    plan["budget"]["max_trajectories"] = placeholder
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(nc.UnsignedConfigError):
        nc.FrozenPlan.load(path, root=ROOT)


def test_signed_plan_loads(tmp_path):
    plan = fx.signed_plan(tmp_path)
    assert plan.targets.n == 13
    assert plan.cutoff == ccs.IMPLAUSIBILITY_CUTOFF


def test_plan_may_not_change_the_acceptance_cutoff(tmp_path):
    with pytest.raises(ContractError, match="may not change the primary acceptance cutoff"):
        fx.signed_plan(tmp_path, **{"acceptance.cutoff": 4.0})


def test_missing_signature_block_is_refused(tmp_path):
    plan = fx.signed_plan_dict()
    plan["sign_off"]["signed_by"] = "UNSIGNED"
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ContractError, match="sign_off block is incomplete"):
        nc.FrozenPlan.load(path, root=ROOT)


@pytest.mark.parametrize(
    "override, message",
    [
        ({"p_parameter.min": 0.01}, "must start at exact 0"),
        ({"p_parameter.bins": [0.0, 0.9]}, "outside the declared range"),
        ({"ambient.scales": [0.05]}, "at least two ambient scales"),
        ({"ambient.scales": [0.05, 0.025]}, "ascending and distinct"),
        ({"ambient.scale_semantics": "guess"}, "radius"),
        ({"graph.bridge_fractions": [0.0, 0.5]}, "strictly in"),
        ({"promotion.variance_alpha": 1.5}, "must be in"),
        ({"p_parameter.routine_hazard_scale_identity_value": 0.8}, "routine_hazard_scale == 1.0"),
        ({"separation.min_anchor_neighbour": 0.5}, "not smaller than the smallest ambient scale"),
    ],
)
def test_cross_field_validation_fails_closed(tmp_path, override, message):
    with pytest.raises(ContractError, match=message):
        fx.signed_plan(tmp_path, **override)


def test_seed_blocks_must_be_disjoint(tmp_path):
    with pytest.raises(ContractError, match="overlap"):
        fx.signed_plan(tmp_path, **{"seeds.confirmation": [910000] + list(range(910100, 910119))})


def test_seeds_colliding_with_earlier_experiments_are_refused(tmp_path):
    """The coupled search's own seeds must not be reused in production."""
    with pytest.raises(ContractError, match="already used by an earlier experiment"):
        fx.signed_plan(tmp_path, **{"seeds.discovery": list(range(200000, 200005))})


# ==========================================================================
# Canonical hashing and normalisation
# ==========================================================================

def test_normalisation_round_trip_is_exact():
    space = fx.m0_space()
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        u = np.full(space.n, fraction)
        back = space.normalise(space.as_dict(space.denormalise(u)))
        np.testing.assert_allclose(back, u, rtol=0, atol=1e-12)


def test_vector_hash_is_sensitive_to_one_ulp():
    names = ("a", "b")
    base = {"a": 1.0, "b": 2.0}
    nudged = {"a": math.nextafter(1.0, 2.0), "b": 2.0}
    assert nc.vector_hash(base, names=names) != nc.vector_hash(nudged, names=names)


def test_vector_hash_normalises_negative_zero():
    names = ("a",)
    assert nc.vector_hash({"a": 0.0}, names=names) == nc.vector_hash({"a": -0.0}, names=names)


def test_vector_hash_refuses_incomplete_or_extra_coordinates():
    with pytest.raises(ContractError, match="missing"):
        nc.vector_hash({"a": 1.0}, names=("a", "b"))
    with pytest.raises(ContractError, match="extra"):
        nc.vector_hash({"a": 1.0, "b": 2.0, "c": 3.0}, names=("a", "b"))


def test_non_finite_coordinate_cannot_be_hashed():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ContractError, match="non-finite"):
            nc.canonical_float(bad)


# ==========================================================================
# Target contract
# ==========================================================================

def test_target_contract_detects_reordering_separately_from_a_wrong_set():
    targets = nc.TargetContract(
        frozen_order=("a", "b", "c"), register_path="x", register_sha256="h"
    )
    with pytest.raises(ContractError, match="reordered"):
        targets.assert_order(("c", "b", "a"))
    with pytest.raises(ContractError, match="missing"):
        targets.assert_order(("a", "b"))
    with pytest.raises(ContractError, match="extra"):
        targets.assert_order(("a", "b", "c", "d"))


def test_m0_and_m1_must_share_the_target_contract():
    left = nc.TargetContract(frozen_order=("a", "b"), register_path="x", register_sha256="h")
    right = nc.TargetContract(frozen_order=("a", "b"), register_path="x", register_sha256="OTHER")
    with pytest.raises(ContractError, match="register hash differs"):
        left.assert_matches(right, what="M0 vs M1")


def test_declared_target_count_must_match_the_listed_order(tmp_path):
    with pytest.raises(ContractError, match="declares n_targets"):
        fx.signed_plan(tmp_path, **{"target_contract.n_targets": 15})


# ==========================================================================
# M0 / M1 nesting
# ==========================================================================

def test_m1_is_a_pure_extension_of_m0_with_p_last():
    m0, m1 = fx.m0_space(), fx.m1_space()
    assert m1.n == m0.n + 1
    assert m1.names[-1] == fx.P_NAME
    assert m1.base_names() == m0.names
    assert m1.names[: m0.n] == m0.names


def test_m1_lower_bound_on_p_must_be_exactly_zero():
    with pytest.raises(ContractError, match="must start at exact 0"):
        fx.m1_space(p_min=0.01, p_max=0.5)


@pytest.mark.parametrize("index", range(5))
def test_confirmed_m0_vectors_embed_into_m1_at_exact_p_zero(index):
    """Multiple vectors: every confirmed M0 vector is carried in unchanged."""
    m0, m1 = fx.m0_space(), fx.m1_space()
    vector = fx.valid_interior_vectors(m0, 5, seed=1000 + index)[index]
    embedded = nc.embed_m0_in_m1(m0, m1, vector)
    assert nc.is_exact_p_zero(embedded, m1)
    assert embedded[fx.P_NAME] == 0.0
    # The complete joint vector survives bit-for-bit.
    for name in m0.names:
        assert nc.canonical_float(embedded[name]) == nc.canonical_float(vector[name])
    nc.assert_nesting_inputs_identical(vector, embedded, m1)


def test_embedding_refuses_a_vector_reconstructed_from_marginals():
    """An incomplete vector cannot be padded with defaults."""
    m0, m1 = fx.m0_space(), fx.m1_space()
    full = fx.interior_vector(m0)
    partial = {name: value for name, value in full.items() if name != m0.names[0]}
    with pytest.raises(ContractError, match="missing"):
        nc.embed_m0_in_m1(m0, m1, partial)


def test_identity_fails_deliberately_when_any_non_p_input_differs():
    m0, m1 = fx.m0_space(), fx.m1_space()
    vector = fx.interior_vector(m0)
    embedded = nc.embed_m0_in_m1(m0, m1, vector)
    for name in (m0.names[0], m0.names[7], m0.names[-1]):
        mutated = dict(embedded)
        mutated[name] = math.nextafter(float(mutated[name]), float("inf"))
        with pytest.raises(ContractError, match="bit-identical"):
            nc.assert_nesting_inputs_identical(vector, mutated, m1)


def test_identity_is_only_defined_at_exact_p_zero():
    m0, m1 = fx.m0_space(), fx.m1_space()
    vector = fx.interior_vector(m0)
    nudged = nc.embed_m0_in_m1(m0, m1, vector, p_value=1e-12)
    with pytest.raises(ContractError, match="exact p=0"):
        nc.assert_nesting_inputs_identical(vector, nudged, m1)


def test_m1_identity_requires_the_routine_hazard_scale_to_be_pinned():
    """``is_identity`` is ``rho == 1.0 AND p == 0.0``; both halves matter."""
    nc.assert_m1_identity_preconditions(routine_hazard_scale=1.0, required_value=1.0)
    with pytest.raises(ContractError, match="does not nest M0"):
        nc.assert_m1_identity_preconditions(routine_hazard_scale=0.8, required_value=1.0)


def test_probes_in_the_p_discontinuity_band_are_rejected_not_snapped():
    """The engine dispatches on exact ``p == 0``, so (0, eps) is discontinuous."""
    m1 = fx.m1_space()
    vector = nc.embed_m0_in_m1(fx.m0_space(), m1, fx.interior_vector(fx.m0_space()))
    assert nc.p_discontinuity_reason(vector, m1, identity_epsilon=1e-6) is None
    for bad in (1e-18, 1e-12, 9e-7):
        near = dict(vector)
        near[fx.P_NAME] = bad
        reason = nc.p_discontinuity_reason(near, m1, identity_epsilon=1e-6)
        assert reason is not None and nc.REJECT_P_DISCONTINUITY in reason
        # The vector itself is never modified by the check.
        assert near[fx.P_NAME] == bad


def test_p_boundary_probes_cover_bins_and_leave_the_discontinuity_band_empty():
    m1 = fx.m1_space()
    anchor = nd.Anchor.build(
        "A1", nc.embed_m0_in_m1(fx.m0_space(), m1, fx.interior_vector(fx.m0_space())), m1
    )
    validator = nd.Validator(space=m1.space, model=m1, identity_epsilon=1e-6)
    outcome = nd.p_boundary_probes(
        anchor, m1, validator, stage=nc.STAGE_DISCOVERY,
        p_bins=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5], identity_epsilon=1e-6, seed=11,
    )
    values = sorted(p.params[fx.P_NAME] for p in outcome.accepted)
    assert 0.0 in values, "an explicit p=0 probe is required"
    assert all(v == 0.0 or v >= 1e-6 for v in values)
    assert outcome.quota == len(outcome.accepted)


# ==========================================================================
# Design geometry
# ==========================================================================

def test_ambient_radius_scale_is_exact():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    for scale in (0.025, 0.05):
        outcome = nd.ambient_probes(
            anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=scale, quota=8,
            generator="gaussian_unit_sphere", scale_semantics="radius", seed=5,
            min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=1000,
        )
        assert len(outcome.accepted) == 8
        for proposal in outcome.accepted:
            assert proposal.distance_to_parent == pytest.approx(scale, abs=1e-12)


def test_coordinate_bound_semantics_bounds_each_coordinate():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    scale = 0.05
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=scale, quota=8,
        generator="uniform_coordinate_box", scale_semantics="coordinate_bound", seed=5,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=1000,
    )
    anchor_u = anchor.u(space)
    for proposal in outcome.accepted:
        delta = np.abs(space.normalise(proposal.params) - anchor_u)
        assert delta.max() <= scale + 1e-12


def test_designs_are_deterministic_and_independent_of_call_order():
    space = fx.m0_space()
    validator = nd.Validator(space=space.space, model=space)
    anchors = [
        nd.Anchor.build(f"A{i}", v, space)
        for i, v in enumerate(fx.valid_interior_vectors(space, 3, seed=9))
    ]

    def probe(anchor):
        return [
            p.vector_hash for p in nd.ambient_probes(
                anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.025, quota=6,
                generator="gaussian_unit_sphere", scale_semantics="radius", seed=777,
                min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=1000,
            ).accepted
        ]

    forward = {a.anchor_id: probe(a) for a in anchors}
    backward = {a.anchor_id: probe(a) for a in reversed(anchors)}
    assert forward == backward, "probes must not depend on the order anchors are processed"


def test_rng_state_is_recorded_and_streams_are_restorable():
    first = nd.design_rng(910300, "ambient", "hash", 0.025)
    second = nd.design_rng(910300, "ambient", "hash", 0.025)
    assert nd.rng_state_hash(first) == nd.rng_state_hash(second)
    np.testing.assert_array_equal(first.standard_normal(5), second.standard_normal(5))
    other = nd.design_rng(910300, "ambient", "hash", 0.05)
    assert nd.rng_state_hash(other) != nd.rng_state_hash(
        nd.design_rng(910300, "ambient", "hash", 0.025)
    )


def test_no_proposal_is_ever_clipped_to_the_bounds():
    """Out-of-bounds proposals are rejected and redrawn, never repaired."""
    space = fx.m0_space()
    at_bound = space.as_dict(space.space.lower.copy())
    anchor = nd.Anchor.build("AT_BOUND", at_bound, space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.025, quota=8,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=3,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=300,
    )
    assert outcome.rejected, "a corner anchor must produce rejections"
    assert nd.REASON_OUT_OF_BOUNDS in outcome.rejection_counts
    for proposal in outcome.rejected:
        # The rejected vector is preserved exactly as proposed.
        assert proposal.vector_hash == proposal.pre_constraint_vector_hash
    for proposal in outcome.accepted:
        vector = np.array([proposal.params[n] for n in space.names])
        assert np.all(vector >= space.space.lower) and np.all(vector <= space.space.upper)


def test_rejection_reason_codes_are_a_fixed_taxonomy():
    """Audit counts must have a stable schema, not one column per bad float."""
    space = fx.m0_space()
    anchor = nd.Anchor.build("AT_BOUND", space.as_dict(space.space.lower.copy()), space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.05, quota=4,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=3,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=200,
    )
    assert set(outcome.rejection_counts) <= set(nd.REASON_CODES)


def test_duplicate_vectors_do_not_count_as_two_neighbours():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    direction = np.zeros(space.n)
    direction[0] = 1.0
    # The same direction offered four times.
    outcome = nd.directed_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY,
        kernel=nc.KERNEL_ARCHIVE_FOLLOWING,
        directions=[(direction, "d", ("A1", "X"))] * 4,
        scales=[0.025], quota=4, seed=1,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=100,
    )
    hashes = [p.vector_hash for p in outcome.accepted]
    assert len(hashes) == len(set(hashes)), "duplicates must not fill a quota"
    assert nd.REASON_DUPLICATE in outcome.rejection_counts


def test_minimum_separation_is_enforced():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.025, quota=4,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=5,
        min_anchor_sep=0.05, min_neighbour_sep=1e-6, max_attempts=200,
    )
    # Every probe sits at radius 0.025, below the 0.05 anchor separation.
    assert not outcome.accepted
    assert outcome.exhausted
    assert nd.REASON_ANCHOR_SEPARATION in outcome.rejection_counts


def test_direction_classes_collapse_collinear_moves():
    base = np.array([1.0, 0.0, 0.0])
    assert nd.direction_classes([base, base * 2.0, -base]) == 1
    assert nd.direction_classes([base, np.array([0.0, 1.0, 0.0])]) == 2


def test_cross_block_directions_touch_exactly_two_blocks():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    index_of = {name: i for i, name in enumerate(space.names)}
    for direction, label, _ in nd.cross_block_directions(anchor, space, seed=1, count=6):
        touched = {
            block for block, members in ccs.BLOCKS.items()
            if any(direction[index_of[m]] != 0.0 for m in members if m in index_of)
        }
        assert len(touched) == 2, f"{label} touched {touched}"


def test_p_joins_the_diagnosis_block_in_m1():
    m1 = fx.m1_space()
    blocks = nc.model_blocks(m1)
    assert fx.P_NAME in blocks[nc.P_BLOCK]
    flat = [n for members in blocks.values() for n in members]
    assert sorted(flat) == sorted(m1.names), "the block partition must cover M1 exactly"


# ==========================================================================
# Quotas
# ==========================================================================

def test_exact_per_kernel_quotas_are_enforced():
    outcomes = [
        nd.KernelOutcome(kernel=nc.KERNEL_AMBIENT, anchor_id="A1", quota=4),
        nd.KernelOutcome(kernel=nc.KERNEL_ARCHIVE_FOLLOWING, anchor_id="A1", quota=2),
    ]
    with pytest.raises(nd.KernelExhaustedError, match="quota"):
        nd.assert_quotas(outcomes, {nc.KERNEL_AMBIENT: 4, nc.KERNEL_ARCHIVE_FOLLOWING: 2},
                         what="test")


def test_a_missing_kernel_family_is_fatal():
    outcome = nd.KernelOutcome(kernel=nc.KERNEL_AMBIENT, anchor_id="A1", quota=0)
    with pytest.raises(nd.KernelExhaustedError, match="entirely missing"):
        nd.assert_quotas([outcome], {nc.KERNEL_AMBIENT: 0, nc.KERNEL_CROSS_BLOCK: 4},
                         what="test")


def test_an_exhausted_kernel_cannot_silently_invoke_another():
    """Exhaustion fails preparation; it never borrows another kernel's budget."""
    space = fx.m0_space()
    anchor = nd.Anchor.build("AT_BOUND", space.as_dict(space.space.lower.copy()), space)
    validator = nd.Validator(space=space.space, model=space)
    ambient = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.05, quota=8,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=3,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=120,
    )
    assert ambient.exhausted
    assert all(p.kernel == nc.KERNEL_AMBIENT for p in ambient.accepted + ambient.rejected)
    with pytest.raises(nd.KernelExhaustedError, match="exhausted its"):
        nd.assert_quotas([ambient], {nc.KERNEL_AMBIENT: 8}, what="test")


def test_every_coordinate_intended_to_vary_does_vary():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.05, quota=40,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=8,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=4000,
    )
    nd.assert_coordinates_vary(outcome.accepted, space)


def test_fixed_coordinates_stay_bit_identical():
    """A p-boundary probe moves only p; every other coordinate is untouched."""
    m1 = fx.m1_space()
    anchor = nd.Anchor.build(
        "A1", nc.embed_m0_in_m1(fx.m0_space(), m1, fx.interior_vector(fx.m0_space())), m1
    )
    validator = nd.Validator(space=m1.space, model=m1, identity_epsilon=1e-6)
    outcome = nd.p_boundary_probes(
        anchor, m1, validator, stage=nc.STAGE_DISCOVERY,
        p_bins=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5], identity_epsilon=1e-6, seed=2,
    )
    nd.assert_coordinates_vary(
        outcome.accepted, m1,
        expected_varying=[fx.P_NAME],
        expected_fixed=[n for n in m1.names if n != fx.P_NAME],
    )


def test_unexpected_zero_variation_is_fatal():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    direction = np.zeros(space.n)
    direction[0] = 1.0
    outcome = nd.directed_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, kernel=nc.KERNEL_CROSS_BLOCK,
        directions=[(direction, "only_first", ("A1",))], scales=[0.02, 0.04], quota=4,
        seed=1, min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=100,
    )
    with pytest.raises(ContractError, match="intended to vary but is constant"):
        nd.assert_coordinates_vary(outcome.accepted, space)


def test_every_proposal_records_a_non_null_kernel_and_parent_lineage():
    space = fx.m0_space()
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.025, quota=4,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=1,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=200,
    )
    for proposal in outcome.accepted:
        assert proposal.kernel
        assert proposal.kernel_version == nd.KERNEL_VERSION
        assert proposal.parent_ids == ("A1",)
        assert proposal.rng_state_hash
        assert proposal.direction_class


def test_a_proposal_without_a_parent_is_refused():
    with pytest.raises(ContractError, match="no parent"):
        nd.Proposal(
            design_id="d", stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0,
            kernel=nc.KERNEL_AMBIENT, kernel_version=nd.KERNEL_VERSION, scale=0.025,
            direction_class="x", parent_ids=(), params={}, vector_hash="h",
            pre_constraint_vector_hash="h", distance_to_parent=0.0, rng_state_hash="r",
            attempt_index=1,
        )


def test_bridge_probes_retain_both_endpoints_as_parents():
    space = fx.m0_space()
    left, right = fx.valid_interior_vectors(space, 2, seed=55)
    a = nd.Anchor.build("L", left, space)
    b = nd.Anchor.build("R", right, space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.bridge_probes(
        a, b, space, validator, stage=nc.STAGE_BRIDGES, fractions=[0.25, 0.5, 0.75],
        seed=1, edge_id="L--R",
    )
    assert len(outcome.accepted) == 3
    for proposal in outcome.accepted:
        assert proposal.parent_ids == ("L", "R"), "a bridge must not invent a single anchor"
    fractions = sorted(p.scale for p in outcome.accepted)
    assert fractions == [0.25, 0.5, 0.75]


# ==========================================================================
# Scoring: exact reuse of the repository's acceptance rule
# ==========================================================================

def test_promotion_reuses_the_cutoff_with_an_inclusive_boundary():
    at = nc.promotion_decision(3.0, 3.0, cutoff=3.0)
    assert at.promoted and at.nominal_pass, "I_max == 3 passes under the <= convention"
    just_over = nc.promotion_decision(3.0001, 3.0001, cutoff=3.0)
    assert not just_over.promoted


def test_bound_promotion_is_flagged_and_is_not_acceptance():
    decision = nc.promotion_decision(3.4, 2.8, cutoff=3.0)
    assert decision.promoted
    assert not decision.nominal_pass
    assert decision.bound_pass
    assert decision.variance_unstable, "bound-only promotion must stay visible"


def test_a_non_conservative_variance_bound_is_a_contract_error():
    """A bounded I_max above nominal means the bound was applied backwards."""
    with pytest.raises(ContractError, match="must be conservative"):
        nc.promotion_decision(2.0, 2.5, cutoff=3.0)


def test_variance_bound_factor_matches_the_repository_formula():
    from scipy.stats import chi2
    for k, alpha in ((5, 0.05), (20, 0.05), (5, 0.1)):
        expected = (k - 1) / chi2.ppf(alpha / 2.0, k - 1)
        assert nc.variance_bound_factor(k, alpha) == pytest.approx(expected, rel=1e-12)
        assert nc.variance_bound_factor(k, alpha) > 1.0


def test_predictive_variance_uses_one_plus_one_over_k():
    out = nc.predictive_variance(
        {"t": 1.0}, {"t": 4.0}, 5, target_order=["t"]
    )
    assert out["t"] == pytest.approx(1.0 + 4.0 * (1.0 + 1.0 / 5.0))


def test_predictive_variance_refuses_a_single_replicate():
    with pytest.raises(ContractError, match="K>=2"):
        nc.predictive_variance({"t": 1.0}, {"t": 1.0}, 1, target_order=["t"])


# ==========================================================================
# Local-support classification on synthetic geometry
# ==========================================================================

def _neighbour(
    design_id, kernel, scale, direction_class, confirmed=True,
    evidence=None, vector_hash=None, direction=None,
):
    state = evidence or (nc.EVIDENCE_CONFIRMED if confirmed else nc.EVIDENCE_NO_SUPPORT)
    return nc.NeighbourEvidence(
        design_id=design_id, kernel=kernel, scale=scale,
        direction_class=direction_class, vector_hash=vector_hash or design_id,
        distance=scale, evidence=state, confirmed=confirmed, direction=direction,
    )


#: Mutually orthogonal directions, so a ridge test asserts real geometric
#: independence rather than two different label strings.
_E1 = (1.0, 0.0, 0.0)
_E2 = (0.0, 1.0, 0.0)
_E3 = (0.0, 0.0, 1.0)


def test_broad_basin_is_broadly_supported():
    neighbours = [
        _neighbour("n1", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT),
        _neighbour("n2", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT),
        _neighbour("n3", nc.KERNEL_AMBIENT, 0.05, nd.DIRCLASS_AMBIENT),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=3,
        feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_BROADLY_SUPPORTED
    assert support.eligible


def test_broad_support_requires_an_ambient_neighbour_at_the_smaller_scale():
    neighbours = [
        _neighbour("n1", nc.KERNEL_AMBIENT, 0.05, nd.DIRCLASS_AMBIENT),
        _neighbour("n2", nc.KERNEL_AMBIENT, 0.05, nd.DIRCLASS_AMBIENT),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.label != nc.LABEL_BROADLY_SUPPORTED


def test_thin_ridge_is_ridge_supported_not_broadly_supported():
    neighbours = [
        _neighbour("a1", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False),
        _neighbour("r1", nc.KERNEL_ARCHIVE_FOLLOWING, 0.025, "archive_difference",
                   direction=_E1),
        _neighbour("r2", nc.KERNEL_CROSS_BLOCK, 0.025, "cross_block:diagnosis+contacts_mixing",
                   direction=_E2),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=3, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_RIDGE_SUPPORTED
    assert support.eligible


def test_ridge_support_requires_geometric_not_label_independence():
    """Different labels on nearly collinear moves must not buy ridge support.

    The specification asks for independent direction classes "rather than
    duplicate or nearly collinear moves", which is a claim about geometry that
    a label string cannot establish.
    """
    nearly_collinear = [
        _neighbour("r1", nc.KERNEL_ARCHIVE_FOLLOWING, 0.025, "archive_difference",
                   direction=(1.0, 0.0, 0.0)),
        _neighbour("r2", nc.KERNEL_CROSS_BLOCK, 0.025, "cross_block:a+b",
                   direction=(1.0, 1e-6, 0.0)),
    ]
    support = nc.classify_local_support(
        nearly_collinear, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert support.n_direction_classes == 1
    assert not support.eligible


def test_ridge_support_is_withheld_when_directions_are_not_supplied():
    """Independence cannot be verified without the move geometry."""
    support = nc.classify_local_support(
        [
            _neighbour("r1", nc.KERNEL_ARCHIVE_FOLLOWING, 0.025, "archive_difference"),
            _neighbour("r2", nc.KERNEL_CROSS_BLOCK, 0.025, "cross_block:a+b"),
        ],
        smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2,
        designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert "geometric independence cannot be verified" in support.reason


def test_a_retried_failure_does_not_change_the_label_by_row_order():
    """Dedup is by evidence precedence, not arrival order."""
    failure = _neighbour(
        "f", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False,
        evidence=nc.EVIDENCE_EXECUTION_FAILURE, vector_hash="SAME",
    )
    retry = _neighbour("s", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT,
                       vector_hash="SAME")
    other = _neighbour("o", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT,
                       vector_hash="OTHER")
    results = [
        nc.classify_local_support(
            order, smaller_ambient_scale=0.025, min_confirming=2,
            min_direction_classes=2, designed_neighbours=2,
            feasible_direction_coverage=1.0,
        )
        for order in ([failure, retry, other], [retry, failure, other])
    ]
    assert results[0].label == results[1].label
    assert results[0].n_failed == results[1].n_failed == 0
    # The superseded failure stays visible rather than vanishing.
    assert results[0].n_superseded_failures == 1
    assert results[1].n_superseded_failures == 1


def test_ridge_support_needs_independent_direction_classes():
    """Two confirmations along one direction are not two independent classes."""
    neighbours = [
        _neighbour("r1", nc.KERNEL_ARCHIVE_FOLLOWING, 0.025, "same_direction",
                   direction=_E1),
        _neighbour("r2", nc.KERNEL_ARCHIVE_FOLLOWING, 0.05, "same_direction",
                   direction=_E1),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert "direction class" in support.reason


def test_isolated_point_reports_no_support_detected_only_on_a_complete_design():
    neighbours = [
        _neighbour(f"n{i}", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False)
        for i in range(8)
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=8, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_NO_LOCAL_SUPPORT
    assert not support.eligible
    assert "not a proof that" in support.reason


def test_execution_failure_is_never_read_as_absence_of_support():
    neighbours = [
        _neighbour("n1", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT,
                   confirmed=False, evidence=nc.EVIDENCE_EXECUTION_FAILURE),
        _neighbour("n2", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert support.n_failed == 1


def test_work_not_run_is_never_read_as_absence_of_support():
    neighbours = [
        _neighbour("n1", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT,
                   confirmed=False, evidence=nc.EVIDENCE_NOT_RUN),
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2,
        min_direction_classes=2, designed_neighbours=8,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert support.n_not_run == 1


def test_boundary_anchor_with_one_sided_coverage_is_unresolved_not_negative():
    """The headline false-negative guard of the specification."""
    support = nc.classify_local_support(
        [], smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2,
        designed_neighbours=16, feasible_direction_coverage=0.0, redraw_exhausted=True,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert not support.eligible


def test_partial_feasible_coverage_is_unresolved_not_negative():
    neighbours = [
        _neighbour(f"n{i}", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False)
        for i in range(4)
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2,
        designed_neighbours=4, feasible_direction_coverage=0.4,
    )
    assert support.label == nc.LABEL_UNRESOLVED
    assert "one-sided" in support.reason


def test_variance_unstable_only_evidence_is_unresolved():
    neighbours = [
        _neighbour(f"n{i}", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, confirmed=False)
        for i in range(4)
    ]
    support = nc.classify_local_support(
        neighbours, smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2,
        designed_neighbours=4, feasible_direction_coverage=1.0, variance_unstable_only=True,
    )
    assert support.label == nc.LABEL_UNRESOLVED


def test_only_unique_vector_hashes_count_as_neighbours():
    duplicated = [
        _neighbour("n1", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, vector_hash="SAME"),
        _neighbour("n2", nc.KERNEL_AMBIENT, 0.025, nd.DIRCLASS_AMBIENT, vector_hash="SAME"),
    ]
    support = nc.classify_local_support(
        duplicated, smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2,
        designed_neighbours=2, feasible_direction_coverage=1.0,
    )
    assert support.n_confirmed_ambient == 1
    assert support.label != nc.LABEL_BROADLY_SUPPORTED


def test_only_eligible_labels_permit_representation():
    assert nc.ELIGIBLE_LABELS == {nc.LABEL_BROADLY_SUPPORTED, nc.LABEL_RIDGE_SUPPORTED}
    for label in (nc.LABEL_NO_LOCAL_SUPPORT, nc.LABEL_UNRESOLVED):
        assert label not in nc.ELIGIBLE_LABELS


# ==========================================================================
# Independent score recomputation from seed-level rows
# ==========================================================================

def test_scores_are_recomputed_from_seed_rows_and_match_the_synthetic_field():
    space = fx.m0_space()
    targets = ccs.FROZEN_TARGET_ORDER
    observed, base_variance = fx.observed_and_variance(targets)
    field = fx.SyntheticField("broad_basin", [np.full(space.n, 0.5)], noise=0.0)

    vectors = {}
    proposals = []
    anchor = nd.Anchor.build("A1", fx.interior_vector(space), space)
    validator = nd.Validator(space=space.space, model=space)
    outcome = nd.ambient_probes(
        anchor, space, validator, stage=nc.STAGE_DISCOVERY, scale=0.025, quota=4,
        generator="gaussian_unit_sphere", scale_semantics="radius", seed=21,
        min_anchor_sep=1e-6, min_neighbour_sep=1e-6, max_attempts=500,
    )
    proposals.extend(outcome.accepted)
    for p in proposals:
        vectors[p.design_id] = p.params

    seeds = [910000, 910001, 910002, 910003, 910004]
    units = fx.work_units(
        proposals, seeds, stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, targets=targets
    )
    ledger = ne.Ledger(stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, units=units)
    results = fx.evaluate_units(units, field, space, targets, vectors=vectors, ledger=ledger)
    frame = ne.merge_seed_results(ledger, results, targets=nc.TargetContract(
        frozen_order=targets, register_path="x", register_sha256="h"))
    scores = ne.recompute_candidate_scores(
        frame, targets=nc.TargetContract(frozen_order=targets, register_path="x",
                                         register_sha256="h"),
        observed=observed, base_variance=base_variance, cutoff=3.0, variance_alpha=0.05,
    )
    for _, row in scores.iterrows():
        u = space.normalise(vectors[row["design_id"]])
        expected = field.i_max(u)
        # With zero replicate noise the sample variance vanishes, so the
        # recomputed implausibility is |mean - 0| / sqrt(B_i) = the field value.
        assert row["i_max"] == pytest.approx(expected, rel=1e-9)


def test_recomputation_refuses_a_single_replicate():
    targets = ("t1", "t2")
    tc = nc.TargetContract(frozen_order=targets, register_path="x", register_sha256="h")
    frame = __import__("pandas").DataFrame([{
        "design_id": "D1", "seed": 1, "vector_hash": "h",
        "target:t1": 1.0, "target:t2": 2.0,
    }])
    with pytest.raises(ContractError, match="at least 2"):
        ne.recompute_candidate_scores(
            frame, targets=tc, observed={"t1": 0.0, "t2": 0.0},
            base_variance={"t1": 1.0, "t2": 1.0}, cutoff=3.0,
        )
