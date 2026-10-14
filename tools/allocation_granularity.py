"""Opt-in stratified deterministic allocation — diagnostic harness for A-14/A-15.

This module is **diagnostic only**.  Nothing in production imports it, and it
changes no production behaviour:  `run_v3.py`, `stochastic_allocation.py` and
every production call site remain byte-identical.  The granularity level is
selected explicitly by a runner, and level 0 delegates straight to
`stochastic_allocation.deterministic_allocate_events`, so level 0 is
bit-identical to current production behaviour by construction.

Background: `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md` §1.  ENG-01 (too
fine: cellwise `np.round(n * p)` erases rare events) and A-13 (too coarse: one
aggregate across strata with different downstream consequences relocates rare
events) are the two ends of one dial.  This module parameterises that dial.

Semantic versus tensor axes
---------------------------
The sub-tensors handed to the allocator do **not** all have the same rank.
`diagnosis` receives `population[:, :, :, 1:, 0, :, :]` — 6-D, HIV stage on
axis 3, ART on axis 4, PrEP on axis 5.  `art_initiation` receives
`population[:, :, :, 1:, 1, 0, :]` — 5-D, HIV stage on axis 3, **ART already
collapsed to a scalar index** and therefore degenerate, PrEP on axis 4.
Stratifying "axis k" blindly would stratify the wrong thing.  `CALL_SITE_AXES`
therefore maps *semantic* axis -> *tensor* axis per call site, with `None`
meaning the semantic axis is already degenerate at that call site (a single
value), where stratifying on it is a no-op.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from stochastic_allocation import deterministic_allocate_events


#: Semantic stratum axes introduced at each granularity level.  Level 4 is the
#: fully cellwise limit and is handled separately (every tensor axis).
LEVEL_SEMANTIC_AXES: Mapping[int, tuple[str, ...]] = {
    0: (),
    1: ("hiv_stage",),
    2: ("hiv_stage", "art"),
    3: ("hiv_stage", "art", "prep"),
    4: ("__all__",),
}

LEVELS: tuple[int, ...] = (0, 1, 2, 3, 4)

LEVEL_LABELS: Mapping[int, str] = {
    0: "whole tensor (production)",
    1: "HIV stage",
    2: "HIV stage x ART status",
    3: "HIV stage x ART status x PrEP state",
    4: "fully cellwise (ENG-01 end)",
}


def _diagnosis_axes() -> dict[str, Optional[int]]:
    # population[:, :, :, 1:, 0, :, :] -> (age, orient, activity, stage, art, prep)
    return {"hiv_stage": 3, "art": 4, "prep": 5}


def _art_transition_axes() -> dict[str, Optional[int]]:
    # population[:, :, :, 1:, 1, 0, :] / [..., 1, 1, :]
    #   -> (age, orient, activity, stage, prep); ART collapsed to one index.
    return {"hiv_stage": 3, "art": None, "prep": 4}


#: process_id -> semantic axis map.  Keys match the `process_id` values passed
#: to `SimulationEngineV3._draw_events_for` in `run_v3.py`.  Tuple-keyed
#: processes (HIV mortality, PrEP uptake/dropout) are matched on their first
#: element by `axes_for_process`.
CALL_SITE_AXES: dict[Any, dict[str, Optional[int]]] = {
    # population[:, :, :, acute, :, :, :] -> (age, orient, activity, diag, art, prep)
    "progression_acute_to_chronic": {"hiv_stage": None, "art": 4, "prep": 5},
    # population[:, :, :, chronic, :, 0|1, :] -> (age, orient, activity, diag, prep)
    "progression_chronic_to_aids_off_art": {"hiv_stage": None, "art": None, "prep": 4},
    "progression_chronic_to_aids_on_art": {"hiv_stage": None, "art": None, "prep": 4},
    # diagnosis: population[:, :, :, 1:, 0, :, :]
    "diagnosis_all_orientations": _diagnosis_axes(),
    "diagnosis_msm": _diagnosis_axes(),
    "diagnosis_non_msm": _diagnosis_axes(),
    # ART transitions: ART axis already collapsed
    "art_initiation": _art_transition_axes(),
    "art_dropout": _art_transition_axes(),
}

#: Tuple-keyed process families, matched on element 0.
CALL_SITE_AXES_BY_FAMILY: dict[str, dict[str, Optional[int]]] = {
    # population[:, :, :, stage, :, 0|1, :] -> (age, orient, activity, diag, prep)
    "hiv_mortality": {"hiv_stage": None, "art": None, "prep": 4},
    # population[a:b, orient, act, susc, :, :, none] -> (age, diag, art)
    "prep_uptake": {"hiv_stage": None, "art": 2, "prep": None},
    # population[:, :, :, susc, :, :, prep_idx] -> (age, orient, activity, diag, art)
    "prep_dropout": {"hiv_stage": None, "art": 4, "prep": None},
}


class UnmappedProcessError(KeyError):
    """Raised when a call site has no declared semantic-axis map.

    Deliberately fatal: silently falling back to level-0 behaviour for an
    unmapped call site would make the sweep report a granularity it did not
    actually apply.
    """


def axes_for_process(process_id: Any) -> dict[str, Optional[int]]:
    """Return the semantic -> tensor axis map for one `_draw_events_for` site."""
    if isinstance(process_id, tuple):
        family = process_id[0]
        if family in CALL_SITE_AXES_BY_FAMILY:
            return CALL_SITE_AXES_BY_FAMILY[family]
    if process_id in CALL_SITE_AXES:
        return CALL_SITE_AXES[process_id]
    raise UnmappedProcessError(
        f"no semantic-axis map declared for allocation call site {process_id!r}; "
        "declare it in tools/allocation_granularity.CALL_SITE_AXES before sweeping"
    )


def stratum_axes_for(process_id: Any, level: int, ndim: int) -> tuple[int, ...]:
    """Tensor axes to hold separate for `process_id` at granularity `level`.

    Degenerate semantic axes (already collapsed to a scalar index at this call
    site) map to `None` and contribute nothing, which is the correct no-op.
    """
    if level not in LEVEL_SEMANTIC_AXES:
        raise ValueError(f"unknown granularity level {level!r}")
    semantic = LEVEL_SEMANTIC_AXES[level]
    if semantic == ("__all__",):
        return tuple(range(ndim))
    if not semantic:
        return ()
    axis_map = axes_for_process(process_id)
    axes: list[int] = []
    for name in semantic:
        if name not in axis_map:
            raise UnmappedProcessError(
                f"semantic axis {name!r} undeclared for call site {process_id!r}"
            )
        tensor_axis = axis_map[name]
        if tensor_axis is None:
            continue  # degenerate at this call site
        if not 0 <= tensor_axis < ndim:
            raise ValueError(
                f"declared tensor axis {tensor_axis} for {name!r} at {process_id!r} "
                f"is outside the received array rank {ndim}"
            )
        axes.append(tensor_axis)
    return tuple(sorted(set(axes)))


def stratified_allocate_events(
    n: np.ndarray, p: float, stratum_axes: Sequence[int] = ()
) -> np.ndarray:
    """Allocate deterministic events independently within each stratum.

    `stratum_axes` names the tensor axes whose index values define a stratum.
    Every combination of those index values is allocated by a separate call to
    `deterministic_allocate_events`, so the rounded aggregate expectation is
    preserved *within each stratum* rather than only across the whole array.

    With `stratum_axes = ()` this delegates to `deterministic_allocate_events`
    unchanged and is therefore bit-identical to current production behaviour.
    With every axis listed it is the fully cellwise limit (ENG-01).
    """
    n_arr = np.asarray(n)
    axes = tuple(sorted(set(int(a) for a in stratum_axes)))
    if not axes:
        return deterministic_allocate_events(n_arr, p)
    for axis in axes:
        if not 0 <= axis < n_arr.ndim:
            raise ValueError(f"stratum axis {axis} outside array rank {n_arr.ndim}")

    if len(axes) == n_arr.ndim:
        # Fully cellwise limit.  Proved equal to the general loop below by
        # tests/test_a14_allocation_granularity.py::test_level4_matches_general_stratifier
        # and equal to ENG-01's np.round(n * p) by ::test_level4_is_eng01_cellwise_round.
        return _cellwise_allocate_events(n_arr, p)

    events = np.zeros(n_arr.shape, dtype=np.int64)
    for combo in product(*(range(n_arr.shape[axis]) for axis in axes)):
        index: list[Any] = [slice(None)] * n_arr.ndim
        for axis, value in zip(axes, combo):
            index[axis] = slice(value, value + 1)  # keep rank stable
        key = tuple(index)
        events[key] = deterministic_allocate_events(n_arr[key], p)
    return events


def _cellwise_allocate_events(n_arr: np.ndarray, p: float) -> np.ndarray:
    """Vectorised single-cell limit of `deterministic_allocate_events`.

    For one cell with count `n` and 0 < p < 1: base = floor(n*p),
    target = min(round(n*p), n), remainder in {0, 1}, capacity = n - base > 0
    whenever n > 0, so the result is exactly `min(round(n*p), n)` — the ENG-01
    cellwise-rounding pattern.  p == 0 and p == 1 are delegated so that the
    edge-case contract is literally the shared allocator's.
    """
    p = float(p)
    if not np.isfinite(p) or p < 0.0 or p > 1.0:
        raise ValueError(
            f"Event probability must be finite and within [0, 1], got {p!r}"
        )
    if n_arr.size == 0 or p == 0.0 or p == 1.0:
        return deterministic_allocate_events(n_arr, p)
    counts = n_arr.astype(np.int64, copy=False)
    rounded = np.round(counts.astype(float) * p).astype(np.int64)
    return np.minimum(rounded, counts)


def allocate_for_process(
    process_id: Any, n: np.ndarray, p: float, level: int
) -> np.ndarray:
    """Deterministic allocation for one call site at one granularity level."""
    n_arr = np.asarray(n)
    axes = stratum_axes_for(process_id, level, n_arr.ndim)
    return stratified_allocate_events(n_arr, p, axes)


def describe_levels(process_ids: Iterable[Any], ndim_by_process: Mapping[Any, int]):
    """Return, per level and call site, the tensor axes actually held separate."""
    rows = []
    for level in LEVELS:
        for process_id in process_ids:
            ndim = ndim_by_process[process_id]
            axes = stratum_axes_for(process_id, level, ndim)
            rows.append(
                {
                    "level": level,
                    "level_label": LEVEL_LABELS[level],
                    "process_id": str(process_id),
                    "array_ndim": ndim,
                    "stratum_tensor_axes": ",".join(str(a) for a in axes),
                    "n_semantic_axes_requested": len(LEVEL_SEMANTIC_AXES[level]),
                    "n_tensor_axes_applied": len(axes),
                }
            )
    return rows
