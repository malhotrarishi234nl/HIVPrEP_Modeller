"""Unit tests for the four-term variance budget (HM-2).

Spec section 7 (HM-2 acceptance) requires a case in which each of the four
variance terms in turn dominates the budget.  ``test_each_term_can_dominate_in_turn``
is that case.
"""

from __future__ import annotations

import numpy as np
import pytest

import hm_variance_budget as vb


# --------------------------------------------------------------------------
# V_obs -- published interval
# --------------------------------------------------------------------------

def test_sigma_from_95_interval_hand_computed():
    # UNAIDS PLHIV, NL, 2023: (28441.27926 - 22189.2858) / 3.92 = 6251.99346 / 3.92 = 1594.8963
    sigma = vb.sigma_from_published_interval(22189.2858, 28441.27926)
    assert sigma == pytest.approx((28441.27926 - 22189.2858) / 3.92)
    assert sigma == pytest.approx(1594.8963, abs=1e-3)


def test_interval_divisor_is_explicit_and_changeable():
    assert vb.NORMAL_95_INTERVAL_DIVISOR == 3.92
    assert vb.sigma_from_published_interval(0.0, 3.92) == pytest.approx(1.0)
    # A 90% interval would need 3.29; the divisor is an argument, not a constant.
    assert vb.sigma_from_published_interval(0.0, 3.29, divisor=3.29) == pytest.approx(1.0)


def test_inverted_or_nonfinite_interval_rejected():
    with pytest.raises(vb.VarianceSpecificationError):
        vb.sigma_from_published_interval(10.0, 5.0)
    with pytest.raises(vb.VarianceSpecificationError):
        vb.sigma_from_published_interval(float("nan"), 5.0)
    with pytest.raises(vb.VarianceSpecificationError):
        vb.sigma_from_published_interval(1.0, 2.0, divisor=0.0)


def test_v_obs_from_interval_records_its_derivation():
    term = vb.v_obs_from_interval(90.0, 110.0)
    assert term.name == "v_obs"
    assert term.status == "derived"
    assert term.sigma == pytest.approx(20.0 / 3.92)
    assert "3.92" in term.derivation


# --------------------------------------------------------------------------
# V_obs -- SHM delay correction
# --------------------------------------------------------------------------

def test_delay_correction_hand_computed_from_real_shm_row():
    # SHM 2024 MSM: reported 267 + trans 14 = 281 ; adjusted 301 + trans 17 = 318.
    term = vb.v_obs_from_delay_correction(281.0, 318.0, fraction=1.0)
    assert term.sigma == pytest.approx(37.0)
    assert term.variance == pytest.approx(37.0 ** 2)
    # Turning SHM's own correction into a sigma is an assumption, at any fraction.
    assert term.status == "assumption"


def test_delay_correction_of_exactly_zero_is_derived_not_assumed():
    # SHM 2018: reported 444 == adjusted 444.  No correction was applied.
    term = vb.v_obs_from_delay_correction(444.0, 444.0)
    assert term.variance == 0.0
    assert term.status == "derived"


def test_delay_correction_scales_linearly_with_the_declared_fraction():
    full = vb.v_obs_from_delay_correction(100.0, 140.0, fraction=1.0)
    half = vb.v_obs_from_delay_correction(100.0, 140.0, fraction=0.5)
    assert full.sigma == pytest.approx(40.0)
    assert half.sigma == pytest.approx(20.0)


def test_fraction_above_one_is_flagged_as_enlarging_nroy():
    term = vb.v_obs_from_delay_correction(100.0, 140.0, fraction=2.0)
    assert term.direction == "under_exclusion"


def test_missing_adjusted_column_without_a_rule_is_underivable():
    term = vb.v_obs_from_delay_correction(500.0, None)
    assert term.status == "underivable"
    assert term.variance == 0.0
    assert term.direction == "over_exclusion"


def test_missing_adjusted_column_with_a_stated_rule_is_derived():
    term = vb.v_obs_from_delay_correction(
        500.0, None, extrapolated=True, extrapolation_rule="zero at lag >= 7, observed"
    )
    assert term.status == "derived"
    assert term.variance == 0.0
    assert "zero at lag" in term.derivation


def test_negative_fraction_rejected():
    with pytest.raises(vb.VarianceSpecificationError):
        vb.v_obs_from_delay_correction(1.0, 2.0, fraction=-1.0)


# --------------------------------------------------------------------------
# V_obs -- underivable
# --------------------------------------------------------------------------

def test_underivable_v_obs_biases_towards_over_exclusion():
    term = vb.v_obs_underivable("ECDC publishes no uncertainty measure")
    assert term.variance == 0.0
    assert term.status == "underivable"
    assert term.direction == "over_exclusion"


# --------------------------------------------------------------------------
# V_code
# --------------------------------------------------------------------------

def test_v_code_is_a_structural_zero():
    term = vb.v_code_no_emulator()
    assert term.variance == 0.0
    assert term.status == "structural_zero"


def test_assemble_refuses_a_non_zero_v_code():
    bad = vb.BudgetTerm(name="v_code", variance=1.0, status="derived", derivation="an emulator appeared")
    with pytest.raises(vb.VarianceSpecificationError, match="no emulator"):
        vb.assemble(
            "t",
            v_obs=vb.v_obs_explicit(1.0, "test"),
            v_ensemble=vb.v_ensemble_unavailable("test"),
            v_disc=vb.v_disc_fraction_of_output_variance([1.0, 2.0, 3.0]),
            v_code=bad,
        )


# --------------------------------------------------------------------------
# V_ensemble
# --------------------------------------------------------------------------

def test_v_ensemble_mean_and_p95_hand_computed():
    # Two design points.  Point A: [1,2,3] -> ddof=1 variance 1.0.
    #                     Point B: [10,20,30] -> ddof=1 variance 100.0.
    replicates = [[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]]
    mean_term = vb.v_ensemble_from_repeat_runs(replicates, statistic="mean")
    assert mean_term.variance == pytest.approx(50.5)
    p95_term = vb.v_ensemble_from_repeat_runs(replicates, statistic="p95")
    assert p95_term.variance == pytest.approx(np.percentile([1.0, 100.0], 95))
    # n must be recorded -- handoff 1A.1 "State n".
    assert "n_replicates=3" in mean_term.derivation


def test_v_ensemble_needs_at_least_two_replicates():
    with pytest.raises(vb.VarianceSpecificationError):
        vb.v_ensemble_from_repeat_runs([[1.0]])


def test_v_ensemble_unavailable_is_zero_and_flagged():
    term = vb.v_ensemble_unavailable("A-5 has not run")
    assert term.variance == 0.0
    assert term.status == "underivable"
    assert term.direction == "over_exclusion"


# --------------------------------------------------------------------------
# V_disc
# --------------------------------------------------------------------------

def test_v_disc_is_a_fraction_of_the_output_variance():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]          # ddof=1 variance = 2.5
    term = vb.v_disc_fraction_of_output_variance(values, multiplier=0.10)
    assert term.variance == pytest.approx(0.25)
    assert term.status == "assumption"
    assert "Andrianakis" in term.derivation


def test_v_disc_ignores_nan_outputs_but_needs_two_finite_values():
    term = vb.v_disc_fraction_of_output_variance([1.0, np.nan, 3.0], multiplier=1.0)
    assert term.variance == pytest.approx(np.var([1.0, 3.0], ddof=1))
    with pytest.raises(vb.VarianceSpecificationError):
        vb.v_disc_fraction_of_output_variance([1.0, np.nan])


def test_v_disc_multiplier_must_be_non_negative():
    with pytest.raises(vb.VarianceSpecificationError):
        vb.v_disc_fraction_of_output_variance([1.0, 2.0], multiplier=-0.1)


def test_tier2_increment_is_a_separate_reported_term():
    term = vb.v_disc_tier2_increment(4.0, increment_multiplier=1.0, rationale="CSAVR is a model")
    assert term.name == "v_disc_tier2"
    assert term.variance == pytest.approx(4.0)
    assert term.status == "assumption"
    assert term.direction == "under_exclusion"


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _budget(v_obs_sigma, v_ens, v_disc_values, disc_multiplier, extra=()):
    return vb.assemble(
        "t",
        v_obs=vb.v_obs_explicit(v_obs_sigma, "test"),
        v_ensemble=v_ens,
        v_disc=vb.v_disc_fraction_of_output_variance(v_disc_values, multiplier=disc_multiplier),
        extra_terms=extra,
    )


def test_each_term_can_dominate_in_turn():
    """Spec section 7 HM-2 acceptance: one case per dominating variance term."""
    tiny = vb.v_ensemble_from_repeat_runs([[0.0, 0.001]], statistic="mean")
    flat = [0.0, 0.001]           # near-zero output variance
    wide = [0.0, 1000.0]          # large output variance

    # 1. V_obs dominates.
    obs_dominant = _budget(100.0, tiny, flat, 0.1)
    assert obs_dominant.dominant_term == "v_obs"
    assert obs_dominant.share("v_obs") > 0.99

    # 2. V_ensemble dominates.
    big_ensemble = vb.v_ensemble_from_repeat_runs([[0.0, 200.0]], statistic="mean")
    ens_dominant = _budget(0.001, big_ensemble, flat, 0.1)
    assert ens_dominant.dominant_term == "v_ensemble"

    # 3. V_disc dominates.
    disc_dominant = _budget(0.001, tiny, wide, 0.1)
    assert disc_dominant.dominant_term == "v_disc"

    # 4. The tier-2 discrepancy increment dominates.
    base = vb.v_disc_fraction_of_output_variance(wide, multiplier=0.1)
    tier2_dominant = vb.assemble(
        "t",
        v_obs=vb.v_obs_explicit(0.001, "test"),
        v_ensemble=tiny,
        v_disc=base,
        extra_terms=[vb.v_disc_tier2_increment(base.variance, increment_multiplier=10.0, rationale="test")],
    )
    assert tier2_dominant.dominant_term == "v_disc_tier2"

    # 5. V_code can never dominate: it is a structural zero and assemble enforces it.
    for budget in (obs_dominant, ens_dominant, disc_dominant, tier2_dominant):
        assert budget.v_code.variance == 0.0


def test_total_variance_is_the_sum_of_the_terms():
    budget = _budget(3.0, vb.v_ensemble_from_repeat_runs([[0.0, 2.0]], statistic="mean"), [0.0, 10.0], 0.1)
    assert budget.total_variance == pytest.approx(sum(term.variance for term in budget.terms))
    assert budget.total_sigma == pytest.approx(np.sqrt(budget.total_variance))
    assert budget.share("v_obs") + budget.share("v_ensemble") + budget.share("v_disc") == pytest.approx(1.0)


def test_assemble_rejects_an_all_zero_budget():
    with pytest.raises(vb.VarianceSpecificationError, match="total variance is zero"):
        vb.assemble(
            "t",
            v_obs=vb.v_obs_underivable("none"),
            v_ensemble=vb.v_ensemble_unavailable("none"),
            v_disc=vb.v_disc_fraction_of_output_variance([2.0, 2.0], multiplier=0.1),
        )


def test_assemble_rejects_mislabelled_terms():
    with pytest.raises(vb.VarianceSpecificationError, match="expected a 'v_obs' term"):
        vb.assemble(
            "t",
            v_obs=vb.v_ensemble_unavailable("wrong slot"),
            v_ensemble=vb.v_ensemble_unavailable("none"),
            v_disc=vb.v_disc_fraction_of_output_variance([1.0, 2.0]),
        )


def test_negative_variance_term_rejected_at_construction():
    with pytest.raises(vb.VarianceSpecificationError):
        vb.BudgetTerm(name="v_obs", variance=-1.0, status="derived", derivation="")


def test_budget_table_and_warnings_carry_provenance():
    budget = _budget(2.0, vb.v_ensemble_unavailable("A-5 not run"), [0.0, 10.0], 0.1)
    row = vb.budget_table([budget])[0]
    assert row["target_key"] == "t"
    assert row["v_code_status"] == "structural_zero"
    assert row["v_ensemble_status"] == "underivable"
    assert "A-5 not run" in row["v_ensemble_derivation"]
    assert row["total_variance"] == pytest.approx(budget.total_variance)
    warnings = vb.budget_warnings([budget])
    assert any(text.startswith("[UNDERIVABLE]") for text in warnings)
    assert any(text.startswith("[ASSUMPTION]") for text in warnings)


def test_share_of_unknown_term_raises():
    budget = _budget(1.0, vb.v_ensemble_unavailable("x"), [0.0, 1.0], 0.1)
    with pytest.raises(KeyError):
        budget.share("v_nonsense")
