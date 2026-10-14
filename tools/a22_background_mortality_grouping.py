"""Opt-in background-mortality grouping for the A-22 diagnostic.

Production code does not import this module.  A runner temporarily substitutes
``demography.deterministic_allocate_events`` only while the life-table method is
active, and restores the binding on exit.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Callable, Sequence

import numpy as np

from stochastic_allocation import deterministic_allocate_events


LEVELS = (0, 1, 2, 3, 4, 5)
LEVEL_LABELS = {
    0: "age x orientation (production)",
    1: "age x orientation x HIV status",
    2: "age x orientation x HIV stage",
    3: "age x orientation x HIV stage x diagnosis",
    4: "age x orientation x HIV stage x diagnosis x ART",
    5: "cellwise np.round(n*p)",
}

# The block supplied by production has axes:
# activity, HIV stage, diagnosis, ART, PrEP.
BACKGROUND_SHAPE_NDIM = 5


def _validate(n: np.ndarray, p: float, level: int) -> tuple[np.ndarray, float]:
    population = np.asarray(n)
    probability = float(p)
    if level not in LEVELS:
        raise ValueError(f"unknown A-22 grouping level {level!r}")
    if population.ndim != BACKGROUND_SHAPE_NDIM:
        raise ValueError(
            "A-22 expects the production background-mortality block "
            f"(activity, stage, diagnosis, ART, PrEP), got {population.shape}"
        )
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"event probability must lie in [0, 1], got {p!r}")
    return population, probability


def grouping_slices(shape: Sequence[int], level: int) -> list[tuple[Any, ...]]:
    """Return the independent allocation blocks for one production call."""
    if len(shape) != BACKGROUND_SHAPE_NDIM:
        raise ValueError(f"unexpected background block rank: {shape}")
    if level not in LEVELS:
        raise ValueError(f"unknown A-22 grouping level {level!r}")
    if level == 0:
        return [(slice(None),) * BACKGROUND_SHAPE_NDIM]
    if level == 1:
        # Status is not a tensor axis: stage 0 is HIV-negative and stages 1+
        # form one HIV-positive block.
        return [
            (slice(None), slice(0, 1), slice(None), slice(None), slice(None)),
            (slice(None), slice(1, None), slice(None), slice(None), slice(None)),
        ]
    axes = {
        2: (1,),
        3: (1, 2),
        4: (1, 2, 3),
        5: tuple(range(BACKGROUND_SHAPE_NDIM)),
    }[level]
    keys: list[tuple[Any, ...]] = []
    for combo in product(*(range(shape[axis]) for axis in axes)):
        key: list[Any] = [slice(None)] * BACKGROUND_SHAPE_NDIM
        for axis, value in zip(axes, combo):
            key[axis] = slice(value, value + 1)
        keys.append(tuple(key))
    return keys


def allocate_background_events(
    n: np.ndarray,
    p: float,
    level: int,
    *,
    allocator: Callable[[np.ndarray, float], np.ndarray] = deterministic_allocate_events,
) -> np.ndarray:
    """Allocate one age-orientation block at an explicitly selected level."""
    population, probability = _validate(n, p, level)
    if level == 0:
        # Delegate without copying so the level-0 call is literally production.
        return allocator(population, probability)
    if level == 5:
        if probability in (0.0, 1.0):
            return allocator(population, probability)
        counts = population.astype(np.int64, copy=False)
        return np.minimum(
            np.round(counts.astype(float) * probability).astype(np.int64), counts
        )
    events = np.zeros(population.shape, dtype=np.int64)
    for key in grouping_slices(population.shape, level):
        events[key] = allocator(population[key], probability)
    return events


def grouped_target(n: np.ndarray, p: float, level: int) -> int:
    """Sum the rounded targets of the blocks actually allocated at ``level``."""
    population, probability = _validate(n, p, level)
    if level == 5:
        counts = population.astype(np.int64, copy=False)
        targets = np.minimum(
            np.round(counts.astype(float) * probability).astype(np.int64), counts
        )
        return int(targets.sum())
    total = 0
    for key in grouping_slices(population.shape, level):
        block = population[key]
        expected = block.astype(float) * probability
        total += min(int(np.round(expected.sum())), int(block.sum()))
    return total
