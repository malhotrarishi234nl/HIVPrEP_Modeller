"""Focused guards for A-22's opt-in background-mortality grouping."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import demography  # noqa: E402
from demography import DemographyModule  # noqa: E402
from stochastic_allocation import deterministic_allocate_events  # noqa: E402
from tools.a22_background_mortality_grouping import (  # noqa: E402
    LEVELS,
    allocate_background_events,
    grouped_target,
    grouping_slices,
)
from tools.run_a22_background_mortality_grouping import (  # noqa: E402
    A22Recorder,
    BackgroundGroupingPatch,
)


def _fixture() -> tuple[np.ndarray, float]:
    rng = np.random.default_rng(20260913)
    counts = np.zeros((3, 4, 2, 2, 2), dtype=np.int64)
    counts[:, 0, 0, 0, :] = rng.integers(10_000, 50_000, size=(3, 2))
    counts[:, 1:, :, :, :] = rng.integers(0, 80, size=(3, 3, 2, 2, 2))
    return counts, 0.013


def test_level0_is_literal_production_allocation():
    counts, p = _fixture()
    expected = deterministic_allocate_events(counts, p)
    actual = allocate_background_events(counts, p, 0)
    assert actual.dtype == expected.dtype
    assert np.array_equal(actual, expected)


def test_level0_delegates_exactly_once():
    counts, p = _fixture()
    calls = []

    def spy(n, probability):
        calls.append((np.asarray(n).shape, probability))
        return deterministic_allocate_events(n, probability)

    allocate_background_events(counts, p, 0, allocator=spy)
    assert calls == [(counts.shape, p)]


def test_level1_splits_hiv_status_not_each_positive_stage():
    counts, p = _fixture()
    calls = []

    def spy(n, probability):
        calls.append(np.asarray(n).shape)
        return deterministic_allocate_events(n, probability)

    events = allocate_background_events(counts, p, 1, allocator=spy)
    assert calls == [(3, 1, 2, 2, 2), (3, 3, 2, 2, 2)]
    for key in grouping_slices(counts.shape, 1):
        assert int(events[key].sum()) == round(float(counts[key].sum()) * p)


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4, 5])
def test_every_level_preserves_each_declared_group_target(level):
    counts, p = _fixture()
    events = allocate_background_events(counts, p, level)
    assert int(events.sum()) == grouped_target(counts, p, level)
    for key in grouping_slices(counts.shape, level):
        expected_target = min(
            round(float(counts[key].sum()) * p), int(counts[key].sum())
        )
        assert int(events[key].sum()) == expected_target


def test_levels_2_to_4_add_exact_semantic_axes():
    shape = _fixture()[0].shape
    assert len(grouping_slices(shape, 2)) == 4
    assert len(grouping_slices(shape, 3)) == 4 * 2
    assert len(grouping_slices(shape, 4)) == 4 * 2 * 2


def test_level5_is_exact_cellwise_rounding_control():
    counts, p = _fixture()
    actual = allocate_background_events(counts, p, 5)
    assert np.array_equal(actual, np.round(counts * p).astype(np.int64))


def test_probability_is_unchanged_in_every_subcall():
    counts, p = _fixture()
    observed = []

    def spy(n, probability):
        observed.append(probability)
        return deterministic_allocate_events(n, probability)

    allocate_background_events(counts, p, 4, allocator=spy)
    assert observed
    assert observed == [p] * len(observed)


def test_allocations_never_exceed_population():
    counts, p = _fixture()
    for level in LEVELS:
        events = allocate_background_events(counts, p, level)
        assert np.all(events >= 0)
        assert np.all(events <= counts)


@pytest.mark.parametrize("bad_level", [-1, 6])
def test_unknown_level_is_fatal(bad_level):
    counts, p = _fixture()
    with pytest.raises(ValueError):
        allocate_background_events(counts, p, bad_level)


def test_wrong_rank_is_fatal():
    with pytest.raises(ValueError):
        allocate_background_events(np.ones((2, 3), dtype=np.int64), 0.1, 1)


def test_patch_is_restored_after_exception():
    original_allocator = demography.deterministic_allocate_events
    original_method = DemographyModule._calculate_life_table_mortality
    with pytest.raises(RuntimeError, match="sentinel"):
        with BackgroundGroupingPatch(1, recorder=object()):
            assert demography.deterministic_allocate_events is not original_allocator
            assert DemographyModule._calculate_life_table_mortality is not original_method
            raise RuntimeError("sentinel")
    assert demography.deterministic_allocate_events is original_allocator
    assert DemographyModule._calculate_life_table_mortality is original_method


def test_patch_does_not_group_calls_outside_life_table_method():
    original_allocator = demography.deterministic_allocate_events
    with BackgroundGroupingPatch(4, recorder=object()):
        observed = demography.deterministic_allocate_events(
            np.array([3, 3, 3, 3], dtype=np.int64), 0.1
        )
    expected = original_allocator(np.array([3, 3, 3, 3], dtype=np.int64), 0.1)
    assert np.array_equal(observed, expected)


def test_eng01_audit_flags_systematic_cellwise_erasure():
    groups = SimpleNamespace(
        activities=["low", "high"],
        hiv_stages=["negative", "acute", "chronic", "aids"],
        diagnosis_states=["undiagnosed", "diagnosed"],
        art_states=["off", "on"],
        prep_states=["none", "prep"],
        age_groups=["15-19"],
        orientations=["MSM"],
    )
    counts = np.full((2, 4, 2, 2, 2), 3, dtype=np.int64)
    p = 0.01
    events = allocate_background_events(counts, p, 5)
    recorder = A22Recorder(5, groups)
    recorder.record(
        year=1975.0,
        age_index=0,
        orientation_index=0,
        n=counts,
        p=p,
        allocated=events,
    )
    classes = recorder.class_rows()
    positive = next(
        row
        for row in classes
        if row["class_axis"] == "hiv_status" and row["class_label"] == "positive"
    )
    assert positive["expected"] > 0
    assert positive["allocation_ratio"] == 0.0
    assert positive["below_50pct_expectation"] is True
    joint = [row for row in classes if row["class_axis"] == "joint_semantic"]
    assert joint
    assert all(row["below_50pct_expectation"] for row in joint)
