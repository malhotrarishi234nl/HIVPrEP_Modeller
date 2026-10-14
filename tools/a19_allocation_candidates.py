"""Pure analysis helpers for A-19's divisor / controlled-rounding screen.

This module is diagnostic infrastructure only.  Nothing in the production
simulation imports it.  It extends A-17's synthetic harness to a 2-D cell
layout so that controlled rounding -- which constrains margins, not just the
grand total -- can be screened at all.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from tools.a17_residual_placement import (
    RULES as A17_RULES,
    _synthetic_path,
    fully_cellwise_round_expected,
    largest_remainder_expected,
    rotating_systematic_expected,
    size_independent_tie_keys,
    stochastic_round_expected,
)
from counter_based_rng import CounterBasedRNGFactory


# ---------------------------------------------------------------------------
# Divisor methods
# ---------------------------------------------------------------------------

#: divisor sequence d(s) for the s-th unit already held by a cell.
DIVISOR_SEQUENCES = {
    "sainte_lague": lambda s: 2.0 * s + 1.0,  # Webster: round at the arithmetic mean
    "dhondt": lambda s: s + 1.0,  # Jefferson: round down
}


def _seats_at_divisor(expected: np.ndarray, divisor: float, method: str) -> np.ndarray:
    """Vectorised seat count for a trial divisor (non-increasing in `divisor`)."""
    if method == "sainte_lague":
        return np.floor(expected / divisor + 0.5).astype(np.int64)
    if method == "dhondt":
        return np.floor(expected / divisor).astype(np.int64)
    raise ValueError(f"unknown divisor method {method}")


def divisor_allocate_expected(
    expected: np.ndarray,
    method: str,
    capacity: np.ndarray | None = None,
) -> np.ndarray:
    """Allocate ``round(sum(expected))`` units by a divisor (highest-averages) rule.

    Implemented as a bisection on the divisor, then an exact repair of the
    overshoot that ties at the margin produce.  The repair removes units from
    the cells whose *last* seat had the lowest priority quotient, which is
    exactly the seat the sequential highest-averages procedure would not have
    awarded.  ``test_divisor_matches_sequential_reference`` checks this against
    a literal seat-by-seat implementation.
    """
    if method not in DIVISOR_SEQUENCES:
        raise ValueError(f"unknown divisor method {method}")
    expected_arr = np.asarray(expected, dtype=float)
    if np.any(~np.isfinite(expected_arr)) or np.any(expected_arr < 0.0):
        raise ValueError("expected events must be finite and non-negative")

    if capacity is None:
        capped = expected_arr
        capacity_arr = None
    else:
        capacity_arr = np.asarray(capacity, dtype=np.int64)
        if capacity_arr.shape != expected_arr.shape or np.any(capacity_arr < 0):
            raise ValueError("capacity must be non-negative and match expected shape")
        capped = np.minimum(expected_arr, capacity_arr.astype(float))

    target = int(np.round(capped.sum()))
    if capacity_arr is not None:
        target = min(target, int(capacity_arr.sum()))
    if target <= 0 or capped.sum() <= 0.0:
        return np.zeros(expected_arr.shape, dtype=np.int64)

    flat = capped.ravel()
    allocated = _divisor_allocate_flat(flat, target, method)
    if capacity_arr is not None:
        allocated = _respect_capacity(flat, allocated, capacity_arr.ravel(), target, method)
    return allocated.reshape(expected_arr.shape)


def _divisor_allocate_flat(flat: np.ndarray, target: int, method: str) -> np.ndarray:
    divisor_of = DIVISOR_SEQUENCES[method]

    # Bracket a divisor that awards at least `target` seats and one that awards
    # at most `target`.  Seat count is non-increasing in the divisor.
    low = max(flat.sum() / target, np.finfo(float).tiny) / 4.0
    for _ in range(200):
        if _seats_at_divisor(flat, low, method).sum() >= target:
            break
        low *= 0.5
    else:  # pragma: no cover - unreachable for finite non-negative input
        raise RuntimeError("could not bracket a divisor from below")
    high = max(low, flat.sum() / target) * 4.0
    for _ in range(200):
        if _seats_at_divisor(flat, high, method).sum() <= target:
            break
        high *= 2.0
    else:  # pragma: no cover
        raise RuntimeError("could not bracket a divisor from above")

    for _ in range(200):
        mid = 0.5 * (low + high)
        if mid <= 0.0 or mid == low or mid == high:
            break
        if _seats_at_divisor(flat, mid, method).sum() >= target:
            low = mid
        else:
            high = mid

    allocated = _seats_at_divisor(flat, low, method)
    excess = int(allocated.sum()) - target
    if excess <= 0:
        # Bisection guarantees >= target at `low`; a shortfall would mean the
        # seat-count step function skipped the target entirely, which cannot
        # happen because it decreases by whole units.
        if excess < 0:  # pragma: no cover
            raise RuntimeError("divisor bisection undershot the target")
        return allocated

    # Tie repair: withdraw the `excess` lowest-priority last seats.  Among
    # equal quotients the *higher* index gives up its seat, which mirrors the
    # sequential procedure awarding a contested seat to the lower index.
    held = np.flatnonzero(allocated > 0)
    last_quotient = flat[held] / np.array(
        [divisor_of(allocated[i] - 1) for i in held], dtype=float
    )
    order = held[np.lexsort((-held, last_quotient))]
    allocated[order[:excess]] -= 1
    return allocated


def _respect_capacity(
    flat: np.ndarray,
    allocated: np.ndarray,
    capacity: np.ndarray,
    target: int,
    method: str,
) -> np.ndarray:
    """Re-allocate surplus from saturated cells until every cell is within capacity."""
    result = np.minimum(allocated, capacity)
    for _ in range(capacity.size + 1):
        shortfall = target - int(result.sum())
        if shortfall <= 0:
            return result
        free = capacity - result
        open_cells = np.flatnonzero(free > 0)
        if open_cells.size == 0:
            return result
        sub_target = min(shortfall, int(free[open_cells].sum()))
        sub = _divisor_allocate_flat(flat[open_cells], sub_target, method)
        result[open_cells] += np.minimum(sub, free[open_cells])
    return result  # pragma: no cover - loop bound is generous


# ---------------------------------------------------------------------------
# Controlled rounding (Cox--Ernst, zero-restricted, two-dimensional)
# ---------------------------------------------------------------------------


def controlled_round_expected(expected: np.ndarray) -> np.ndarray:
    """Zero-restricted 2-D controlled rounding of a non-negative table.

    Returns integers ``X`` with, simultaneously:

    * ``floor(E_ij) <= X_ij <= ceil(E_ij)`` for every cell,
    * every row sum within one unit of that row's exact total,
    * every column sum within one unit of that column's exact total,
    * ``X.sum() == round(E.sum())`` exactly,

    minimising ``sum |X_ij - E_ij|``.  This is Cox's transportation formulation:
    the constraint matrix is the incidence matrix of a bipartite flow network
    and is therefore totally unimodular, so an LP vertex solution is integral.
    Integrality is asserted rather than assumed -- the brief's escalation rule
    requires it, and a non-integral answer must not be silently rounded.
    """
    exact = np.asarray(expected, dtype=float)
    if exact.ndim != 2:
        raise ValueError("controlled rounding requires a two-dimensional table")
    if np.any(~np.isfinite(exact)) or np.any(exact < 0.0):
        raise ValueError("expected events must be finite and non-negative")

    n_rows, n_cols = exact.shape
    base = np.floor(exact)
    fraction = exact - base
    grand_target = int(np.round(exact.sum()))
    if grand_target <= 0:
        return np.zeros(exact.shape, dtype=np.int64)

    row_exact = exact.sum(axis=1)
    col_exact = exact.sum(axis=0)
    n_cells = n_rows * n_cols
    # Variables: y_ij in [0, 1] (the +1 indicator), then row margins u_i, col margins v_j.
    n_vars = n_cells + n_rows + n_cols

    # |X - E| = fraction + y * (1 - 2 * fraction); the constant drops out.
    cost = np.zeros(n_vars, dtype=float)
    cost[:n_cells] = (1.0 - 2.0 * fraction).ravel()

    rows_eq: list[np.ndarray] = []
    rhs_eq: list[float] = []
    for i in range(n_rows):
        row = np.zeros(n_vars, dtype=float)
        row[i * n_cols : (i + 1) * n_cols] = 1.0
        row[n_cells + i] = -1.0
        rows_eq.append(row)
        rhs_eq.append(-base[i].sum())
    for j in range(n_cols):
        row = np.zeros(n_vars, dtype=float)
        row[j:n_cells:n_cols] = 1.0
        row[n_cells + n_rows + j] = -1.0
        rows_eq.append(row)
        rhs_eq.append(-base[:, j].sum())
    grand_row = np.zeros(n_vars, dtype=float)
    grand_row[n_cells : n_cells + n_rows] = 1.0
    rows_eq.append(grand_row)
    rhs_eq.append(float(grand_target))

    bounds: list[tuple[float, float]] = [(0.0, 1.0)] * n_cells
    bounds += [(math.floor(value), math.ceil(value)) for value in row_exact]
    bounds += [(math.floor(value), math.ceil(value)) for value in col_exact]

    solution = linprog(
        cost,
        A_eq=np.array(rows_eq, dtype=float),
        b_eq=np.array(rhs_eq, dtype=float),
        bounds=bounds,
        method="highs",
    )
    if not solution.success:
        raise RuntimeError(f"controlled rounding LP infeasible: {solution.message}")

    y = solution.x[:n_cells].reshape(exact.shape)
    if np.any(np.abs(y - np.rint(y)) > 1e-6):
        raise AssertionError(
            "controlled rounding LP returned a non-integral vertex; the total "
            "unimodularity argument does not hold for this formulation"
        )
    result = (base + np.rint(y)).astype(np.int64)
    if int(result.sum()) != grand_target:
        raise AssertionError("controlled rounding did not preserve the grand total")
    return result


def controlled_round_flat(expected: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Controlled-round a flat vector under a declared 2-D margin structure."""
    flat = np.asarray(expected, dtype=float)
    return controlled_round_expected(flat.reshape(shape)).ravel()


# ---------------------------------------------------------------------------
# The A-19 synthetic harness: A-17's process, on a 2-D grid
# ---------------------------------------------------------------------------

A19_RULES = ("sainte_lague", "dhondt", "controlled_rounding")
ALL_RULES = A17_RULES + A19_RULES

GRID_SHAPE = (12, 10)


def synthetic_design(steps: int = 300, design_seed: int = 1702) -> dict:
    """Reproduce A-17's synthetic design exactly, then declare the 2-D layout."""
    design_rng = np.random.default_rng(design_seed)
    initial = np.maximum(1, np.rint(np.geomspace(5.0, 5000.0, 120))).astype(np.int64)
    rates = np.geomspace(5e-4, 2e-2, initial.size)
    design_rng.shuffle(rates)
    return {"initial": initial, "rates": rates, "steps": steps, "shape": GRID_SHAPE}


def _a19_path(
    rule: str,
    initial: np.ndarray,
    rates: np.ndarray,
    steps: int,
    *,
    shape: tuple[int, int],
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Run one A-19 candidate over the same multiplicative process A-17 used."""
    state = np.asarray(initial, dtype=np.int64).copy()
    totals = np.empty(steps + 1, dtype=float)
    totals[0] = state.sum()
    for step in range(steps):
        expected = state.astype(float) * rates
        if rule in DIVISOR_SEQUENCES:
            events = divisor_allocate_expected(expected, rule)
        elif rule == "controlled_rounding":
            events = controlled_round_flat(expected, shape)
        else:
            raise ValueError(f"unknown A-19 rule {rule}")
        state += events
        totals[step + 1] = state.sum()
    return totals, state


def run_path(
    rule: str,
    initial: np.ndarray,
    rates: np.ndarray,
    steps: int,
    *,
    shape: tuple[int, int],
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Dispatch to A-17's path runner or A-19's, so both families share a driver."""
    if rule in A17_RULES:
        return _synthetic_path(rule, initial, rates, steps, seed=seed)
    return _a19_path(rule, initial, rates, steps, shape=shape, seed=seed)


def exact_trajectory(initial: np.ndarray, rates: np.ndarray, steps: int) -> np.ndarray:
    """The unrounded reference: cell-wise geometric growth, cells x (steps + 1)."""
    return initial.astype(float)[:, None] * (
        (1.0 + rates[:, None]) ** np.arange(steps + 1)[None, :]
    )


def margin_divergence(
    final_cells: np.ndarray, exact_final: np.ndarray, shape: tuple[int, int]
) -> dict:
    """Row/column margin error of a final state against the exact final state."""
    got = np.asarray(final_cells, dtype=float).reshape(shape)
    want = np.asarray(exact_final, dtype=float).reshape(shape)
    row_error = got.sum(axis=1) / want.sum(axis=1) - 1.0
    col_error = got.sum(axis=0) / want.sum(axis=0) - 1.0
    return {
        "max_abs_row_margin_relative_error": float(np.abs(row_error).max()),
        "mean_abs_row_margin_relative_error": float(np.abs(row_error).mean()),
        "max_abs_col_margin_relative_error": float(np.abs(col_error).max()),
        "mean_abs_col_margin_relative_error": float(np.abs(col_error).mean()),
    }


def trajectory_screen(
    *, steps: int = 300, stochastic_seeds: int = 500, design_seed: int = 1702
) -> tuple[pd.DataFrame, dict]:
    """C2: final divergence from the exact fractional trajectory, every rule."""
    design = synthetic_design(steps=steps, design_seed=design_seed)
    initial, rates, shape = design["initial"], design["rates"], design["shape"]
    exact_cells = exact_trajectory(initial, rates, steps)
    exact_final = exact_cells[:, -1]

    rows: list[dict] = []
    for rule in ALL_RULES:
        if rule == "stochastic_rounding":
            ensemble = np.empty((stochastic_seeds, initial.size), dtype=float)
            for seed in range(1, stochastic_seeds + 1):
                _, cells = run_path(rule, initial, rates, steps, shape=shape, seed=seed)
                ensemble[seed - 1] = cells
            final_cells = ensemble.mean(axis=0)
        else:
            _, cells = run_path(rule, initial, rates, steps, shape=shape, seed=1)
            final_cells = cells.astype(float)
        row = {
            "rule": rule,
            "family": "A-17" if rule in A17_RULES else "A-19",
            "final_total": float(final_cells.sum()),
            "exact_final_total": float(exact_final.sum()),
            "final_relative_divergence": float(final_cells.sum() / exact_final.sum() - 1.0),
            "mean_absolute_cell_error": float(np.mean(np.abs(final_cells - exact_final))),
        }
        row.update(margin_divergence(final_cells, exact_final, shape))
        rows.append(row)
    meta = {
        "n_cells": int(initial.size),
        "steps": steps,
        "grid_shape": list(shape),
        "stochastic_replicates": stochastic_seeds,
        "design_seed": design_seed,
    }
    return pd.DataFrame(rows), meta


# ---------------------------------------------------------------------------
# C1: the rare-event compositional screen
# ---------------------------------------------------------------------------

#: The epidemic-shaped regime A-17 found decisive: 600 large + 600 small cells.
RARE_EVENT_P = 1e-4


def epidemic_shaped_counts() -> np.ndarray:
    return np.concatenate(
        [np.full(600, 9_900, dtype=np.int64), np.full(600, 100, dtype=np.int64)]
    )


#: Margin layouts for controlled rounding.  "stratum_rows" puts each size
#: stratum in its own rows; "stratum_cols" in its own columns; "interleaved"
#: aligns the strata with no margin at all -- the honest control.
LAYOUTS = {
    "stratum_rows": (12, 100),
    "stratum_cols": (12, 100),
    "interleaved": (12, 100),
}


def layout_order(layout: str, n_cells: int = 1200) -> np.ndarray:
    """Permutation mapping table position -> cell index, for a declared layout.

    ``expected[order].reshape(shape)`` is the table handed to controlled
    rounding; ``restored[order] = rounded`` maps the result back to cell order.
    """
    if layout == "stratum_rows":
        # Cells 0-599 are the large stratum, 600-1199 the small one, so the
        # natural order already gives rows 1-6 large and rows 7-12 small.
        return np.arange(n_cells, dtype=np.int64)
    if layout == "stratum_cols":
        return _column_layout(n_cells)
    if layout == "interleaved":
        return _checkerboard_layout(n_cells)
    raise ValueError(f"unknown layout {layout}")


def _column_layout(n_cells: int) -> np.ndarray:
    """Place the two size strata in separate *columns* of a 12x100 table."""
    large = np.arange(n_cells // 2, dtype=np.int64)
    small = np.arange(n_cells // 2, n_cells, dtype=np.int64)
    grid = np.empty((12, 100), dtype=np.int64)
    grid[:, :50] = large.reshape(12, 50)
    grid[:, 50:] = small.reshape(12, 50)
    return grid.ravel()


def _checkerboard_layout(n_cells: int) -> np.ndarray:
    """Align the strata with no margin at all: every row and column is mixed.

    This is the control against over-claiming for controlled rounding.  On a
    12x100 checkerboard each row holds 50 cells of each stratum and each column
    holds 6, so neither margin carries any information about stratum identity.
    """
    grid = np.empty((12, 100), dtype=np.int64)
    rows, cols = np.indices(grid.shape)
    is_large = ((rows + cols) % 2) == 0
    large = np.arange(n_cells // 2, dtype=np.int64)
    small = np.arange(n_cells // 2, n_cells, dtype=np.int64)
    if int(is_large.sum()) != large.size:
        raise ValueError("checkerboard layout does not split the strata evenly")
    grid[is_large] = large
    grid[~is_large] = small
    return grid.ravel()


def rare_event_screen(seeds: int = 500) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Allocate one rare-event step under every rule; score by size decile."""
    counts = epidemic_shaped_counts()
    expected = counts.astype(float) * RARE_EVENT_P
    deciles = pd.qcut(pd.Series(counts).rank(method="first"), 10, labels=False).to_numpy()

    allocations: dict[str, np.ndarray] = {
        "largest_remainder": largest_remainder_expected(expected, counts),
        "largest_remainder_size_independent_tiebreak": largest_remainder_expected(
            expected, counts, tie_keys=size_independent_tie_keys(counts.size, seed=1, step=0)
        ),
        "rotating_systematic": rotating_systematic_expected(expected, step=0, capacity=counts),
        "fully_cellwise_rounding": fully_cellwise_round_expected(expected, counts),
        "sainte_lague": divisor_allocate_expected(expected, "sainte_lague", counts),
        "dhondt": divisor_allocate_expected(expected, "dhondt", counts),
    }
    # Stochastic rounding is a distribution, so it is scored on its mean over
    # `seeds` draws, exactly as A-17 scored it.
    ensemble = np.empty((seeds, counts.size), dtype=float)
    for seed in range(1, seeds + 1):
        uniforms = CounterBasedRNGFactory(seed).for_process(
            year=0.0, timestep=0, process_id="a19_rare_event"
        ).random(size=counts.shape)
        ensemble[seed - 1] = stochastic_round_expected(expected, uniforms, counts)
    allocations["stochastic_rounding"] = ensemble.mean(axis=0)

    for layout, shape in LAYOUTS.items():
        order = layout_order(layout)
        table = expected[order].reshape(shape)
        rounded = controlled_round_expected(table).ravel()
        restored = np.empty(counts.size, dtype=np.int64)
        restored[order] = rounded
        allocations[f"controlled_rounding__{layout}"] = restored

    small = counts < 1_000
    decile_rows: list[dict] = []
    summary_rows: list[dict] = []
    for rule, allocated in allocations.items():
        allocated = np.asarray(allocated, dtype=float)
        worst_error = 0.0
        zero_deciles = 0
        scored_deciles = 0
        for decile in range(10):
            mask = deciles == decile
            expected_sum = float(expected[mask].sum())
            allocated_sum = float(allocated[mask].sum())
            relative = (
                allocated_sum / expected_sum - 1.0 if expected_sum > 0.0 else math.nan
            )
            if rule == "stochastic_rounding":
                per_seed = ensemble[:, mask].sum(axis=1)
                standard_error = float(per_seed.std(ddof=1) / math.sqrt(per_seed.shape[0]))
            else:
                standard_error = 0.0
            decile_rows.append(
                {
                    "rule": rule,
                    "size_decile": decile + 1,
                    "mean_n": float(counts[mask].mean()),
                    "expected_events": expected_sum,
                    "allocated_events": allocated_sum,
                    "allocated_standard_error": standard_error,
                    "relative_error": relative,
                    "relative_error_in_standard_errors": (
                        (allocated_sum - expected_sum) / standard_error
                        if standard_error > 0.0
                        else math.nan
                    ),
                }
            )
            if expected_sum >= 1.0:
                scored_deciles += 1
                worst_error = max(worst_error, abs(relative))
                if allocated_sum == 0.0:
                    zero_deciles += 1
        # Supplementary to the pre-committed decile verdict: the same error at
        # *stratum* granularity.  Reported because controlled rounding's
        # guarantee is stated on the margins it is given, and the six protected
        # rows of the C1 regime do not nest inside the five small-size deciles.
        small_expected = float(expected[small].sum())
        small_allocated = float(allocated[small].sum())
        summary_rows.append(
            {
                "rule": rule,
                "allocated_total": float(allocated.sum()),
                "expected_total": float(expected.sum()),
                "n_scored_deciles": scored_deciles,
                "n_scored_deciles_with_zero_allocation": zero_deciles,
                "worst_scored_decile_abs_relative_error": worst_error,
                "passes_c1a_no_zero_decile": zero_deciles == 0,
                "passes_c1b_worst_error_below_10pct": worst_error < 0.10,
                "passes_c1": zero_deciles == 0 and worst_error < 0.10,
                "small_stratum_expected_events": small_expected,
                "small_stratum_allocated_events": small_allocated,
                "small_stratum_relative_error": small_allocated / small_expected - 1.0,
            }
        )
    return pd.DataFrame(summary_rows), pd.DataFrame(decile_rows)


def layout_margin_diagnostics() -> pd.DataFrame:
    """Predict, from the margin structure alone, what controlled rounding protects.

    A zero-restricted controlled rounding may round any margin down to its
    floor, so a small stratum is never protected by *its own* margin.  What
    protects it is the ceiling on the margins of the stratum-pure *large*
    rows or columns: those cannot absorb more than ``sum(ceil(margin))`` units
    between them, so the grand total forces the remainder elsewhere.

        guaranteed small-stratum events = max(0, T - sum of ceil(pure-large margins))

    This is a prediction made from the layout before the LP is solved;
    ``test_margin_prediction_matches_the_solved_allocation`` checks it against
    the solved allocation for every layout.
    """
    counts = epidemic_shaped_counts()
    expected = counts.astype(float) * RARE_EVENT_P
    small = counts < 1_000
    grand_target = int(np.round(expected.sum()))
    rows: list[dict] = []
    for layout, shape in LAYOUTS.items():
        order = layout_order(layout)
        table = expected[order].reshape(shape)
        small_table = small[order].reshape(shape)
        pure_large_rows = (~small_table).all(axis=1)
        pure_large_cols = (~small_table).all(axis=0)
        row_ceiling = float(np.ceil(table.sum(axis=1)[pure_large_rows]).sum())
        col_ceiling = float(np.ceil(table.sum(axis=0)[pure_large_cols]).sum())
        # The binding constraint is whichever axis caps the large stratum lowest.
        ceilings = [value for value, present in (
            (row_ceiling, pure_large_rows.any()),
            (col_ceiling, pure_large_cols.any()),
        ) if present]
        large_ceiling = min(ceilings) if ceilings else float(grand_target)
        rows.append(
            {
                "layout": layout,
                "grand_target": grand_target,
                "n_stratum_pure_large_rows": int(pure_large_rows.sum()),
                "n_stratum_pure_large_cols": int(pure_large_cols.sum()),
                "pure_large_row_margin_ceiling_sum": row_ceiling,
                "pure_large_col_margin_ceiling_sum": col_ceiling,
                "binding_large_stratum_ceiling": large_ceiling,
                "predicted_guaranteed_small_stratum_events": max(
                    0.0, grand_target - large_ceiling
                ),
                "small_stratum_expected_events": float(expected[small].sum()),
            }
        )
    return pd.DataFrame(rows)
