"""
Shared deterministic event allocation.

Consolidates deterministic_allocate_events() so every module drawing binomial
events in deterministic mode (transmission, progression, diagnosis, ART,
demography's background mortality) rounds the same way. Cellwise
np.round(n * p) can erase rare events spread across many tensor cells; this
allocator preserves the rounded aggregate expected total instead.
"""

import numpy as np


def deterministic_allocate_events(n: np.ndarray, p: float) -> np.ndarray:
    """
    Deterministic integer approximation to a binomial event draw.

    Cellwise rounding can erase rare events when expected counts are spread
    across many tensor cells. This allocator preserves the rounded aggregate
    expected total, then assigns residual events to cells with the largest
    fractional expected counts.
    """
    p = float(p)
    if not np.isfinite(p) or p < 0.0 or p > 1.0:
        raise ValueError(
            f"Event probability must be finite and within [0, 1], got {p!r}"
        )

    n_arr = np.asarray(n)
    if n_arr.size == 0:
        return np.zeros_like(n_arr, dtype=np.int64)
    if p == 0.0:
        return np.zeros_like(n_arr, dtype=np.int64)
    if p == 1.0:
        return n_arr.astype(np.int64, copy=True)

    expected = n_arr.astype(float) * p
    base = np.floor(expected).astype(np.int64)
    base = np.minimum(base, n_arr.astype(np.int64, copy=False))
    target = min(int(np.round(expected.sum())), int(n_arr.sum()))
    remainder = target - int(base.sum())

    if remainder <= 0:
        return base

    events = base.copy()
    fractional = (expected - base).ravel()
    capacity = (n_arr.astype(np.int64, copy=False) - base).ravel()
    eligible = np.flatnonzero(capacity > 0)
    if eligible.size == 0:
        return events

    order = eligible[np.argsort(-fractional[eligible], kind="stable")]
    take = min(remainder, order.size)
    np.put(events, order[:take], events.ravel()[order[:take]] + 1)
    return events


def deterministic_allocate_total(total: int, weights: np.ndarray) -> np.ndarray:
    """
    Allocate an integer ``total`` across ``weights`` by largest remainder.

    Companion to :func:`deterministic_allocate_events`. That function answers
    "how many events occur in each cell at rate p"; this one answers "how do I
    place exactly this many people across these cells". Both are total-preserving
    and neither consumes randomness.

    Capacity note. Callers that pass the cell stocks themselves as ``weights``
    and clamp ``total <= weights.sum()`` cannot over-draw any cell: then
    ``raw_i = total * w_i / sum(w) <= w_i``, so ``floor(raw_i) <= w_i``, and the
    residual pass can only reach ``w_i + 1`` when ``frac_i == 0`` -- which is
    never selected while any positive fraction remains, and when every fraction
    is zero there is no residual to place. The C-1 exogenous forcing path relies
    on exactly this contract.

    Ties break on stable index order: arbitrary, but fixed and reproducible.
    """
    total = int(total)
    weights_arr = np.asarray(weights, dtype=float)
    if total <= 0 or weights_arr.size == 0:
        return np.zeros(weights_arr.shape, dtype=np.int64)

    flat = weights_arr.ravel()
    pool = flat.sum()
    if pool <= 0:
        # No structure to allocate against. Refuse rather than inventing one:
        # spreading people uniformly across empty cells would fabricate stock.
        return np.zeros(weights_arr.shape, dtype=np.int64)

    raw = flat * (total / pool)
    base = np.floor(raw).astype(np.int64)
    remainder = total - int(base.sum())
    if remainder > 0:
        order = np.argsort(-(raw - base), kind="stable")
        base[order[:remainder]] += 1
    return base.reshape(weights_arr.shape)
