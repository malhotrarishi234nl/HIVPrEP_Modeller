"""Unit tests for the history-matching implausibility measure (HM-2).

These are hand-computed cases.  The acceptance rule for the whole calibration is
this arithmetic, so it is checked against numbers worked out by hand rather than
against the implementation's own output.
"""

from __future__ import annotations

import numpy as np
import pytest

import hm_implausibility as hm


# --------------------------------------------------------------------------
# Scalar implausibility
# --------------------------------------------------------------------------

def test_implausibility_hand_computed():
    # |12 - 10| / sqrt(4) = 2 / 2 = 1
    assert hm.implausibility(12.0, 10.0, 4.0) == pytest.approx(1.0)
    # |4 - 10| / sqrt(4) = 6 / 2 = 3  -- exactly on the cutoff
    assert hm.implausibility(4.0, 10.0, 4.0) == pytest.approx(3.0)


def test_implausibility_is_symmetric_in_sign_of_error():
    assert hm.implausibility(13.0, 10.0, 9.0) == hm.implausibility(7.0, 10.0, 9.0)


def test_implausibility_vectorises():
    result = hm.implausibility(np.array([10.0, 13.0, 4.0]), 10.0, 9.0)
    assert result == pytest.approx([0.0, 1.0, 2.0])


def test_zero_variance_is_a_specification_error_not_infinity():
    # A zero budget asserts the target is exact AND the model is perfect.
    with pytest.raises(hm.VarianceBudgetError):
        hm.implausibility(1.0, 1.0, 0.0)


def test_negative_and_nonfinite_variance_rejected():
    with pytest.raises(hm.VarianceBudgetError):
        hm.implausibility(1.0, 1.0, -1.0)
    with pytest.raises(hm.VarianceBudgetError):
        hm.implausibility(1.0, 1.0, float("nan"))


def test_nan_simulator_output_propagates():
    assert np.isnan(hm.implausibility(float("nan"), 1.0, 1.0))


# --------------------------------------------------------------------------
# Cutoff provenance
# --------------------------------------------------------------------------

def test_cutoff_and_pukelsheim_coverage_figures():
    assert hm.IMPLAUSIBILITY_CUTOFF == 3.0
    # Vysochanskii-Petunin at 3 sigma: Pr(|X - mu| >= 3 sigma) <= 4/81.
    assert hm.PUKELSHEIM_UNIMODAL_COVERAGE == pytest.approx(1.0 - 4.0 / 81.0)
    assert hm.PUKELSHEIM_UNIMODAL_COVERAGE == pytest.approx(0.95062, abs=1e-5)
    # Chebyshev, distribution-free, at 3 sigma.
    assert hm.CHEBYSHEV_COVERAGE == pytest.approx(1.0 - 1.0 / 9.0)
    # The build spec's "at least 93%" matches neither bound.
    assert not (0.929 < hm.PUKELSHEIM_UNIMODAL_COVERAGE < 0.931)


# --------------------------------------------------------------------------
# Matrix construction
# --------------------------------------------------------------------------

def test_matrix_from_mapping_hand_computed():
    simulated = {"a": [10.0, 16.0], "b": [5.0, 5.0]}
    observed = {"a": 10.0, "b": 11.0}
    variance = {"a": 4.0, "b": 9.0}
    matrix, names = hm.implausibility_matrix(simulated, observed, variance, target_order=["a", "b"])
    assert names == ["a", "b"]
    # candidate 0: |10-10|/2 = 0 ; |5-11|/3 = 2
    # candidate 1: |16-10|/2 = 3 ; |5-11|/3 = 2
    assert matrix == pytest.approx(np.array([[0.0, 2.0], [3.0, 2.0]]))


def test_matrix_from_array():
    matrix, names = hm.implausibility_matrix(
        np.array([[10.0, 5.0]]), [10.0, 11.0], [4.0, 9.0], target_order=["a", "b"]
    )
    assert matrix == pytest.approx(np.array([[0.0, 2.0]]))
    assert names == ["a", "b"]


def test_matrix_rejects_non_positive_variance_and_names_the_target():
    with pytest.raises(hm.VarianceBudgetError, match="b"):
        hm.implausibility_matrix({"a": [1.0], "b": [1.0]}, {"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 0.0})


def test_matrix_rejects_ragged_columns():
    with pytest.raises(ValueError):
        hm.implausibility_matrix({"a": [1.0, 2.0], "b": [1.0]}, {"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 1.0})


def test_matrix_rejects_missing_target():
    with pytest.raises(KeyError):
        hm.implausibility_matrix({"a": [1.0]}, {"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 1.0}, target_order=["a", "b"])


# --------------------------------------------------------------------------
# Combination -- max / 2nd / 3rd
# --------------------------------------------------------------------------

def test_order_statistics_hand_computed():
    matrix = np.array([
        [1.0, 5.0, 3.0, 2.0],   # sorted desc: 5, 3, 2, 1
        [0.5, 0.4, 0.3, 0.2],   # sorted desc: 0.5, 0.4, 0.3, 0.2
    ])
    combined = hm.combine(matrix, ["w", "x", "y", "z"])
    assert combined.i_max == pytest.approx([5.0, 0.5])
    assert combined.i_second == pytest.approx([3.0, 0.4])
    assert combined.i_third == pytest.approx([2.0, 0.3])
    assert combined.binding_target == ["x", "w"]


def test_max_rule_is_non_compensatory():
    """A perfect fit on three targets cannot offset one catastrophic failure.

    This is the structural reason for the switch away from the weighted sum:
    under a weighted sum an excellent MSM fit compensated for a zero non-MSM
    fit, which is how the submitted base case scored acceptably.
    """
    matrix = np.array([[0.0, 0.0, 0.0, 50.0]])
    combined = hm.combine(matrix, ["a", "b", "c", "catastrophe"])
    assert combined.i_max == pytest.approx([50.0])
    assert combined.nroy_mask().tolist() == [False]
    assert combined.binding_target == ["catastrophe"]


def test_second_max_rule_tolerates_exactly_one_bad_target():
    matrix = np.array([[0.1, 0.2, 0.3, 99.0]])
    combined = hm.combine(matrix, ["a", "b", "c", "bad"])
    assert combined.nroy_mask(rule="max").tolist() == [False]
    assert combined.nroy_mask(rule="second").tolist() == [True]


def test_nan_defaults_to_implausible_not_to_fitting():
    matrix = np.array([[np.nan, 0.1]])
    combined = hm.combine(matrix, ["missing", "fine"])
    assert np.isinf(combined.i_max[0])
    assert combined.nroy_mask().tolist() == [False]


def test_nan_policy_omit_drops_the_target():
    matrix = np.array([[np.nan, 0.1]])
    combined = hm.combine(matrix, ["missing", "fine"], nan_policy="omit")
    assert combined.i_max == pytest.approx([0.1])
    assert np.isnan(combined.i_second[0])
    assert combined.nroy_mask().tolist() == [True]


def test_all_nan_candidate_is_ruled_out_under_omit():
    combined = hm.combine(np.array([[np.nan, np.nan]]), ["a", "b"], nan_policy="omit")
    assert np.isnan(combined.i_max[0])
    assert combined.nroy_mask().tolist() == [False]


def test_fewer_than_three_targets_leaves_higher_order_statistics_undefined():
    combined = hm.combine(np.array([[1.0, 2.0]]), ["a", "b"])
    assert combined.i_max == pytest.approx([2.0])
    assert combined.i_second == pytest.approx([1.0])
    assert np.isnan(combined.i_third[0])


def test_cutoff_boundary_is_inclusive():
    combined = hm.combine(np.array([[3.0], [3.0000001]]), ["a"])
    assert combined.nroy_mask().tolist() == [True, False]


def test_invalid_rule_and_policy_rejected():
    combined = hm.combine(np.array([[1.0]]), ["a"])
    with pytest.raises(ValueError):
        combined.nroy_mask(rule="fourth")
    with pytest.raises(ValueError):
        hm.combine(np.array([[1.0]]), ["a"], nan_policy="ignore")


def test_matrix_shape_validation():
    with pytest.raises(ValueError):
        hm.combine(np.array([1.0, 2.0]), ["a", "b"])
    with pytest.raises(ValueError):
        hm.combine(np.array([[1.0, 2.0]]), ["a"])


def test_nroy_fraction():
    combined = hm.combine(np.array([[1.0], [2.0], [9.0], [4.0]]), ["a"])
    assert hm.nroy_fraction(combined) == pytest.approx(0.5)
