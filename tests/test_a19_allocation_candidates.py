import math

import numpy as np
import pytest

from tools.a19_allocation_candidates import (
    DIVISOR_SEQUENCES,
    controlled_round_expected,
    divisor_allocate_expected,
    epidemic_shaped_counts,
    layout_order,
    margin_divergence,
)


def sequential_highest_averages(expected, target, method):
    """Literal seat-by-seat reference implementation of a divisor method."""
    divisor_of = DIVISOR_SEQUENCES[method]
    expected = np.asarray(expected, dtype=float)
    allocated = np.zeros(expected.size, dtype=np.int64)
    for _ in range(target):
        quotients = expected / np.array(
            [divisor_of(seats) for seats in allocated], dtype=float
        )
        allocated[int(np.argmax(quotients))] += 1
    return allocated


@pytest.mark.parametrize("method", sorted(DIVISOR_SEQUENCES))
def test_divisor_matches_sequential_reference(method):
    rng = np.random.default_rng(20260912)
    for _ in range(40):
        expected = rng.gamma(1.5, 2.0, size=rng.integers(2, 12))
        target = int(np.round(expected.sum()))
        if target <= 0:
            continue
        got = divisor_allocate_expected(expected, method)
        want = sequential_highest_averages(expected, target, method)
        np.testing.assert_array_equal(got, want)


@pytest.mark.parametrize("method", sorted(DIVISOR_SEQUENCES))
def test_divisor_handles_exact_ties_like_the_reference(method):
    expected = np.array([2.5, 2.5, 2.5, 2.5])  # target 10, repeated ties
    target = int(np.round(expected.sum()))
    np.testing.assert_array_equal(
        divisor_allocate_expected(expected, method),
        sequential_highest_averages(expected, target, method),
    )


@pytest.mark.parametrize("method", sorted(DIVISOR_SEQUENCES))
def test_divisor_preserves_the_rounded_total(method):
    rng = np.random.default_rng(7)
    for _ in range(25):
        expected = rng.uniform(0.0, 5.0, size=30)
        allocated = divisor_allocate_expected(expected, method)
        assert int(allocated.sum()) == int(np.round(expected.sum()))
        assert allocated.min() >= 0


def test_sainte_lague_and_dhondt_differ_on_the_textbook_case():
    # Four parties, seven seats: D'Hondt favours the largest, Sainte-Lague does not.
    expected = np.array([3.5, 2.1, 1.0, 0.4])
    sl = divisor_allocate_expected(expected, "sainte_lague")
    dh = divisor_allocate_expected(expected, "dhondt")
    assert sl.sum() == dh.sum() == 7
    assert dh[0] >= sl[0]
    assert dh[-1] <= sl[-1]


@pytest.mark.parametrize("method", sorted(DIVISOR_SEQUENCES))
def test_divisor_respects_capacity(method):
    expected = np.array([9.0, 0.5, 0.5, 0.5])
    capacity = np.array([2, 5, 5, 5])
    allocated = divisor_allocate_expected(expected, method, capacity)
    assert np.all(allocated <= capacity)
    assert int(allocated.sum()) == int(np.round(np.minimum(expected, capacity).sum()))


def test_divisor_rejects_negative_and_non_finite_input():
    with pytest.raises(ValueError):
        divisor_allocate_expected(np.array([1.0, -1.0]), "sainte_lague")
    with pytest.raises(ValueError):
        divisor_allocate_expected(np.array([1.0, np.inf]), "sainte_lague")
    with pytest.raises(ValueError):
        divisor_allocate_expected(np.array([1.0, 1.0]), "not_a_method")


def test_controlled_rounding_satisfies_every_cox_ernst_constraint():
    rng = np.random.default_rng(99)
    for _ in range(20):
        exact = rng.gamma(2.0, 1.5, size=(6, 5))
        rounded = controlled_round_expected(exact)
        assert rounded.dtype == np.int64
        # zero-restricted: each cell is its own floor or ceiling
        assert np.all(rounded >= np.floor(exact))
        assert np.all(rounded <= np.ceil(exact))
        # margins within one unit of the exact margins
        assert np.all(np.abs(rounded.sum(axis=1) - exact.sum(axis=1)) < 1.0 + 1e-9)
        assert np.all(np.abs(rounded.sum(axis=0) - exact.sum(axis=0)) < 1.0 + 1e-9)
        # grand total exact, matching every other rule in the screen
        assert int(rounded.sum()) == int(np.round(exact.sum()))


def test_controlled_rounding_is_exact_on_an_integral_table():
    exact = np.array([[1.0, 2.0], [3.0, 4.0]])
    np.testing.assert_array_equal(controlled_round_expected(exact), exact.astype(np.int64))


def test_controlled_rounding_protects_a_stratum_row_that_largest_remainder_erases():
    # Six rows of expectation 0.01 each: their row margin is exactly 1 event.
    exact = np.zeros((12, 100))
    exact[:6] = 9_900 * 1e-4
    exact[6:] = 100 * 1e-4
    rounded = controlled_round_expected(exact)
    assert rounded[6:].sum() == 6  # the small stratum keeps its expectation
    assert int(rounded.sum()) == 600


def test_controlled_rounding_rejects_a_one_dimensional_table():
    with pytest.raises(ValueError):
        controlled_round_expected(np.array([0.5, 0.5]))


def test_controlled_rounding_is_deterministic():
    rng = np.random.default_rng(3)
    exact = rng.gamma(2.0, 1.0, size=(7, 4))
    first = controlled_round_expected(exact)
    second = controlled_round_expected(exact)
    np.testing.assert_array_equal(first, second)


@pytest.mark.parametrize("layout", ["stratum_rows", "stratum_cols", "interleaved"])
def test_layouts_are_permutations_of_the_cells(layout):
    order = layout_order(layout)
    np.testing.assert_array_equal(np.sort(order), np.arange(1200))


def test_layout_stratum_purity_is_as_documented():
    counts = epidemic_shaped_counts()
    purity = {}
    for layout in ("stratum_rows", "stratum_cols", "interleaved"):
        grid = counts[layout_order(layout)].reshape(12, 100)
        large = grid > 1_000
        purity[layout] = (
            bool(np.all(large.all(axis=1) | (~large).all(axis=1))),
            bool(np.all(large.all(axis=0) | (~large).all(axis=0))),
        )
    assert purity["stratum_rows"] == (True, False)
    assert purity["stratum_cols"] == (False, True)
    assert purity["interleaved"] == (False, False)


def test_margin_divergence_is_zero_for_an_exact_match():
    exact = np.arange(1.0, 121.0)
    result = margin_divergence(exact, exact, (12, 10))
    assert all(math.isclose(value, 0.0, abs_tol=1e-12) for value in result.values())


def test_margin_prediction_matches_the_solved_allocation():
    """The layout-only prediction must match what the controlled-rounding LP does."""
    from tools.a19_allocation_candidates import (
        LAYOUTS,
        RARE_EVENT_P,
        controlled_round_expected,
        layout_margin_diagnostics,
    )

    counts = epidemic_shaped_counts()
    expected = counts.astype(float) * RARE_EVENT_P
    small = counts < 1_000
    predictions = layout_margin_diagnostics().set_index("layout")
    for layout, shape in LAYOUTS.items():
        order = layout_order(layout)
        rounded = controlled_round_expected(expected[order].reshape(shape)).ravel()
        restored = np.empty(counts.size, dtype=np.int64)
        restored[order] = rounded
        observed = float(restored[small].sum())
        predicted = float(predictions.loc[layout, "predicted_guaranteed_small_stratum_events"])
        assert observed == predicted, (layout, observed, predicted)
