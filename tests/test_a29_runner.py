"""Focused guards for A-29's diagnostic patch and level definitions."""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_a29_transmission_block_grouping import (  # noqa: E402
    AllocationRecorder,
    LEVEL_AXES,
    TransmissionGroupingPatch,
    cellwise_allocate,
)
from transmission_corrected import TransmissionEngine  # noqa: E402


def test_level_axes_match_the_predeclared_sweep():
    assert LEVEL_AXES == {
        0: (),
        1: (1,),
        2: (1, 2),
        3: (1, 2, 0),
        4: (1, 2, 0, 3),
    }


def test_level1_is_the_production_grouping():
    assert LEVEL_AXES[1] == TransmissionEngine.DETERMINISTIC_INFECTION_BLOCK_AXES


def test_level5_is_cellwise_rounding_capped_by_capacity():
    expected = np.array([0.49, 0.51, 1.8, 4.0])
    capacity = np.array([1, 1, 1, 0], dtype=np.int64)
    assert np.array_equal(
        cellwise_allocate(expected, capacity), np.array([0, 1, 1, 0])
    )


def test_patch_restores_production_method_after_exception():
    original = TransmissionEngine._deterministic_allocate_expected_infections
    recorder = AllocationRecorder(0)
    with pytest.raises(RuntimeError, match="sentinel"):
        with TransmissionGroupingPatch(0, recorder):
            assert TransmissionEngine._deterministic_allocate_expected_infections is not original
            raise RuntimeError("sentinel")
    assert TransmissionEngine._deterministic_allocate_expected_infections is original


def test_recorder_rejects_a_conservation_violation():
    expected = np.full((1, 2, 2, 1), 0.4)
    capacity = np.ones_like(expected, dtype=np.int64)
    bad = np.zeros_like(capacity)
    with pytest.raises(AssertionError, match="invariant"):
        AllocationRecorder(1).record(expected, capacity, bad)
