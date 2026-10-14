"""Tests for the A-14 stratified deterministic allocation wrapper.

The wrapper is diagnostic and opt-in; these tests pin the two properties the
A-14 brief requires of it:

  * level 0 is **bit-identical** to current production behaviour, so a sweep at
    level 0 cannot move the A-12/A-4 deterministic reference values; and
  * on a constructed fixture, level 1 preserves the expected total **within each
    HIV stage** while level 0 does not — the A-13 defect, reproduced in the
    small.

They also pin the ENG-01 end of the dial: level 4 is exactly cellwise
`np.round(n * p)`, which is the pattern ENG-01 removed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stochastic_allocation import deterministic_allocate_events  # noqa: E402
from tools.allocation_granularity import (  # noqa: E402
    LEVEL_SEMANTIC_AXES,
    UnmappedProcessError,
    allocate_for_process,
    axes_for_process,
    stratified_allocate_events,
    stratum_axes_for,
)


def _stage_fixture() -> tuple[np.ndarray, float]:
    """(age, orient, activity, stage, art, prep) with a rare AIDS stratum.

    Chronic (stage index 1) holds many large cells; AIDS (stage index 2) holds
    many small cells.  Both share one scalar `p`, exactly as `diagnosis`,
    `art_initiation` and `art_dropout` do in `run_v3.py`.
    """
    rng = np.random.default_rng(20260911)
    counts = np.zeros((6, 3, 3, 3, 2, 2), dtype=np.int64)
    counts[:, :, :, 0, :, :] = rng.integers(20, 60, size=(6, 3, 3, 2, 2))
    counts[:, :, :, 1, :, :] = rng.integers(4_000, 9_000, size=(6, 3, 3, 2, 2))
    counts[:, :, :, 2, :, :] = rng.integers(3, 12, size=(6, 3, 3, 2, 2))
    return counts, 0.037


# ---------------------------------------------------------------- level 0


@pytest.mark.parametrize("p", [0.0, 1e-6, 0.037, 0.5, 0.999, 1.0])
def test_level0_is_bit_identical_to_production_allocator(p):
    counts, _ = _stage_fixture()
    baseline = deterministic_allocate_events(counts, p)
    stratified = stratified_allocate_events(counts, p, ())
    assert stratified.dtype == baseline.dtype
    assert np.array_equal(stratified, baseline)


def test_level0_is_bit_identical_at_every_mapped_call_site():
    """Level 0 must be a no-op for every declared call site, whatever its rank."""
    rng = np.random.default_rng(7)
    shapes = {
        "progression_acute_to_chronic": (6, 3, 3, 2, 2, 2),
        "progression_chronic_to_aids_off_art": (6, 3, 3, 2, 2),
        "progression_chronic_to_aids_on_art": (6, 3, 3, 2, 2),
        "diagnosis_all_orientations": (6, 3, 3, 3, 2, 2),
        "diagnosis_msm": (6, 3, 3, 3, 2, 2),
        "diagnosis_non_msm": (6, 3, 3, 3, 2, 2),
        "art_initiation": (6, 3, 3, 3, 2),
        "art_dropout": (6, 3, 3, 3, 2),
        ("hiv_mortality", "AIDS", "off_art"): (6, 3, 3, 2, 2),
        ("prep_uptake", "Oral_PrEP", 1, 2): (4, 2, 2),
        ("prep_dropout", "Oral_PrEP"): (6, 3, 3, 2, 2),
    }
    for process_id, shape in shapes.items():
        counts = rng.integers(0, 5_000, size=shape).astype(np.int64)
        expected = deterministic_allocate_events(counts, 0.041)
        assert np.array_equal(
            allocate_for_process(process_id, counts, 0.041, 0), expected
        ), process_id


# ------------------------------------------------- level 1 vs level 0 (A-13)


def test_level1_preserves_stage_totals_and_level0_does_not():
    """The A-13 defect, constructed: level 0 relocates rare-stratum events."""
    counts, p = _stage_fixture()
    stage_axis = axes_for_process("diagnosis_all_orientations")["hiv_stage"]
    assert stage_axis == 3

    level0 = allocate_for_process("diagnosis_all_orientations", counts, p, 0)
    level1 = allocate_for_process("diagnosis_all_orientations", counts, p, 1)

    # Both preserve the rounded aggregate — this is why aggregate checks passed.
    aggregate_expected = round(float(counts.sum()) * p)
    assert abs(int(level0.sum()) - aggregate_expected) <= 1
    assert abs(int(level1.sum()) - aggregate_expected) <= 1

    errors0, errors1 = [], []
    for stage in range(counts.shape[stage_axis]):
        pool = float(np.take(counts, stage, axis=stage_axis).sum())
        expected = pool * p
        errors0.append(int(np.take(level0, stage, axis=stage_axis).sum()) - expected)
        errors1.append(int(np.take(level1, stage, axis=stage_axis).sum()) - expected)

    # Level 1 preserves every stage total to within one event, by construction.
    assert max(abs(e) for e in errors1) <= 1.0, errors1

    # Level 0 does not: the rare AIDS stratum (index 2) is under-allocated while
    # the common strata absorb exactly the same events -- the errors sum to zero
    # (up to the single-event aggregate rounding), which is why an aggregate-only
    # check cannot see this.
    aids_error, chronic_error = errors0[2], errors0[1]
    assert aids_error < -1.0, errors0
    assert chronic_error > 1.0, errors0
    assert abs(sum(errors0)) <= 1.0, errors0


def test_level1_rare_stratum_shortfall_is_materially_large():
    """Quantifies the shortfall so a regression cannot shrink it unnoticed."""
    counts, p = _stage_fixture()
    level0 = allocate_for_process("diagnosis_all_orientations", counts, p, 0)
    aids_pool = float(counts[:, :, :, 2, :, :].sum())
    aids_expected = aids_pool * p
    aids_actual = int(level0[:, :, :, 2, :, :].sum())
    shortfall_fraction = (aids_expected - aids_actual) / aids_expected
    assert shortfall_fraction > 0.20, (aids_expected, aids_actual)


# ---------------------------------------------------------------- level 4


def test_level4_matches_general_stratifier():
    """The vectorised cellwise fast path equals the explicit per-stratum loop."""
    rng = np.random.default_rng(99)
    counts = rng.integers(0, 400, size=(4, 3, 3, 2)).astype(np.int64)
    for p in (1e-4, 0.037, 0.5, 0.83):
        fast = stratified_allocate_events(counts, p, tuple(range(counts.ndim)))
        slow = np.zeros_like(counts, dtype=np.int64)
        for index in np.ndindex(counts.shape):
            cell = counts[index].reshape(())[np.newaxis]
            slow[index] = int(deterministic_allocate_events(cell, p).sum())
        assert np.array_equal(fast, slow), p


def test_level4_is_eng01_cellwise_round():
    rng = np.random.default_rng(5)
    counts = rng.integers(0, 5_000, size=(5, 4, 3)).astype(np.int64)
    for p in (1e-5, 0.037, 0.61):
        level4 = stratified_allocate_events(counts, p, tuple(range(counts.ndim)))
        assert np.array_equal(level4, np.round(counts * p).astype(np.int64))


def test_level4_erases_rare_events_that_level0_preserves():
    """The other end of the dial: fine granularity erases, coarse preserves."""
    counts = np.full((2_000,), 3, dtype=np.int64)
    p = 0.01  # per-cell expectation 0.03, rounds to zero everywhere
    level0 = stratified_allocate_events(counts, p, ())
    level4 = stratified_allocate_events(counts, p, (0,))
    assert int(level0.sum()) == 60
    assert int(level4.sum()) == 0


# --------------------------------------------------------- axis bookkeeping


def test_collapsed_art_axis_is_degenerate_for_art_transitions():
    """`art_initiation` receives a tensor whose ART axis is already collapsed."""
    assert axes_for_process("art_initiation")["art"] is None
    assert axes_for_process("art_dropout")["art"] is None
    # Level 2 (stage x ART) therefore applies only the stage axis there ...
    assert stratum_axes_for("art_initiation", 2, 5) == (3,)
    # ... while diagnosis, whose ART axis survives, gets both.
    assert stratum_axes_for("diagnosis_all_orientations", 2, 6) == (3, 4)


def test_level2_is_a_noop_relative_to_level1_for_art_transitions():
    rng = np.random.default_rng(11)
    counts = rng.integers(0, 3_000, size=(6, 3, 3, 3, 2)).astype(np.int64)
    level1 = allocate_for_process("art_initiation", counts, 0.05, 1)
    level2 = allocate_for_process("art_initiation", counts, 0.05, 2)
    assert np.array_equal(level1, level2)


def test_stage_axis_is_not_confused_across_ranks():
    """A naive 'stratify axis 3' would hit PrEP for progression call sites."""
    # progression_chronic_to_aids_off_art is 5-D (age, orient, activity, diag, prep):
    # HIV stage and ART are both already collapsed, so level 2 is a no-op there.
    assert stratum_axes_for("progression_chronic_to_aids_off_art", 2, 5) == ()
    # and level 3 adds only the PrEP axis, which is axis 4, not axis 3.
    assert stratum_axes_for("progression_chronic_to_aids_off_art", 3, 5) == (4,)


def test_unmapped_process_is_fatal_not_silently_level0():
    with pytest.raises(UnmappedProcessError):
        stratum_axes_for("some_new_process", 1, 4)
    # level 0 and level 4 need no map, by construction
    assert stratum_axes_for("some_new_process", 0, 4) == ()
    assert stratum_axes_for("some_new_process", 4, 4) == (0, 1, 2, 3)


def test_declared_axis_outside_rank_is_fatal():
    with pytest.raises(ValueError):
        stratum_axes_for("diagnosis_all_orientations", 3, 4)


def test_every_level_is_declared():
    assert set(LEVEL_SEMANTIC_AXES) == {0, 1, 2, 3, 4}


def test_stratified_allocation_never_exceeds_capacity():
    counts, p = _stage_fixture()
    for level in (0, 1, 2, 3, 4):
        events = allocate_for_process("diagnosis_all_orientations", counts, p, level)
        assert np.all(events >= 0)
        assert np.all(events <= counts), level


# -------------------------------- why levels 1, 2 and 3 coincide in this config


def test_axis_with_one_occupied_index_makes_stratification_a_noop():
    """The load-bearing fact behind `axis_occupancy_audit.csv`.

    Levels 2 and 3 were byte-identical to level 1 on the base case.  The reason
    is occupancy, not a harness defect: the ART axis inside the diagnosis tensor
    and the PrEP axis inside every HIV-positive tensor carry non-zero counts at
    exactly one index, so partitioning on them yields one non-empty block and
    some all-zero blocks, and the allocator returns zeros for an all-zero block.
    """
    rng = np.random.default_rng(2026)
    counts = np.zeros((6, 3, 3, 3, 2, 2), dtype=np.int64)
    # All mass at ART index 0 and PrEP index 0, as the real diagnosis tensor has.
    counts[:, :, :, :, 0, 0] = rng.integers(0, 4_000, size=(6, 3, 3, 3))
    level1 = allocate_for_process("diagnosis_all_orientations", counts, 0.031, 1)
    level2 = allocate_for_process("diagnosis_all_orientations", counts, 0.031, 2)
    level3 = allocate_for_process("diagnosis_all_orientations", counts, 0.031, 3)
    assert np.array_equal(level1, level2)
    assert np.array_equal(level2, level3)


def test_occupied_second_index_does_separate_level2_from_level1():
    """The converse: when the ART axis is genuinely occupied, level 2 differs.

    Recorded so that the 'levels 2 and 3 add nothing' finding is understood as
    configuration-dependent, not as a property of the wrapper.
    """
    rng = np.random.default_rng(4242)
    counts = np.zeros((6, 3, 3, 3, 2, 2), dtype=np.int64)
    counts[:, :, :, :, 0, 0] = rng.integers(1_000, 4_000, size=(6, 3, 3, 3))
    counts[:, :, :, :, 1, 0] = rng.integers(1, 9, size=(6, 3, 3, 3))
    level1 = allocate_for_process("diagnosis_all_orientations", counts, 0.031, 1)
    level2 = allocate_for_process("diagnosis_all_orientations", counts, 0.031, 2)
    assert not np.array_equal(level1, level2)
