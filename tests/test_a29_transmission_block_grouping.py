"""Regression and conservation guards for A-29 transmission grouping."""

from __future__ import annotations

from itertools import product
from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from transmission_corrected import TransmissionEngine  # noqa: E402


def _allocate(expected, capacity, axes=None):
    return TransmissionEngine._deterministic_allocate_expected_infections(
        expected, capacity, group_axes=axes
    )


def _block_keys(shape, axes):
    for combination in product(*(range(shape[axis]) for axis in axes)):
        key = [slice(None)] * len(shape)
        for axis, index in zip(axes, combination):
            key[axis] = slice(index, index + 1)
        yield tuple(key)


@pytest.mark.parametrize("axes", [(), (1,), (1, 2), (1, 2, 0), (1, 2, 0, 3)])
def test_each_declared_block_conserves_its_rounded_capped_expectation(axes):
    rng = np.random.default_rng(20260914)
    expected = rng.uniform(0.0, 2.0, size=(4, 3, 2, 2))
    capacity = rng.integers(0, 4, size=expected.shape, dtype=np.int64)
    allocated = _allocate(expected, capacity, axes)

    assert allocated.dtype == np.int64
    assert np.all(allocated >= 0)
    assert np.all(allocated <= capacity)
    for key in _block_keys(expected.shape, axes):
        capped = np.minimum(expected[key], capacity[key].astype(float))
        target = min(round(float(capped.sum())), int(capacity[key].sum()))
        assert int(allocated[key].sum()) == target


def test_production_orientation_blocks_remove_cross_system_coupling():
    capacity = np.ones((1, 2, 312, 1), dtype=np.int64)
    base = np.zeros(capacity.shape, dtype=float)
    base[0, 0, :300, 0] = 0.0012
    base[0, 1, 300:, 0] = 0.40
    scaled = base.copy()
    scaled[0, 0, :300, 0] *= 25.0

    base_allocated = TransmissionEngine._deterministic_allocate_expected_infections(
        base, capacity
    )
    scaled_allocated = TransmissionEngine._deterministic_allocate_expected_infections(
        scaled, capacity
    )

    assert int(base_allocated[:, 1].sum()) == 5
    assert int(scaled_allocated[:, 1].sum()) == 5
    assert int(base_allocated[:, 0].sum()) == 0
    assert int(scaled_allocated[:, 0].sum()) == 9


def test_level0_counterfactual_exhibits_the_bug_guarded_above():
    capacity = np.ones((1, 2, 312, 1), dtype=np.int64)
    base = np.zeros(capacity.shape, dtype=float)
    base[0, 0, :300, 0] = 0.0012
    base[0, 1, 300:, 0] = 0.40
    scaled = base.copy()
    scaled[0, 0, :300, 0] *= 25.0

    base_level0 = _allocate(base, capacity, ())
    scaled_level0 = _allocate(scaled, capacity, ())

    assert int(base_level0[:, 1].sum()) == 5
    assert int(scaled_level0[:, 1].sum()) == 12
    assert int(base_level0[:, 1].sum()) != int(scaled_level0[:, 1].sum())


def test_invalid_group_axes_are_rejected():
    expected = np.ones((2, 3, 4, 5), dtype=float)
    capacity = np.ones_like(expected, dtype=np.int64)
    for axes in ((1, 1), (-1,), (4,)):
        with pytest.raises(ValueError, match="group_axes"):
            _allocate(expected, capacity, axes)
