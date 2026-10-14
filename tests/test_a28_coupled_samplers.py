"""A-28: distribution-preservation and CRN-coupling proofs for the two A-28 samplers.

A coupling that changes the distribution silently changes the model, so these are
permanent tests rather than a one-off validation.  They reproduce the standard set by
A-24's ``raw/cf_sampler_validation.json`` and ``raw/multinomial_sampler_probe.json``
(``outputs/hta_results/a24_entry_pathway_crn_drift_20260914_010031/``), on the same
synthetic fixtures, against the production samplers.

**Fixtures are illustrative sampler inputs, not model parameters.**

Tolerance derivation (A-28 §4.1; no tolerance here is fitted to an observed value):

* Means.  With ``R`` replicates, ``z = (mean_emp - mean_th) / sqrt(var_th / R)`` is
  asymptotically standard normal.  ``MAX_ABS_Z_MEAN = 4.0`` is a two-sided per-category
  false-alarm rate of 6.3e-5; over the 11 categories exercised here the family-wise rate
  is about 7e-4.
* Variances and covariances.  The relative standard deviation of a sample variance at
  ``R = 20000`` for these near-binomial marginals is ``sqrt(2 / (R - 1)) = 1.00%``.
  ``MAX_REL_ERR_VARIANCE = 0.04`` is four of those.
* Off-diagonal correlations.  Under the independence this coupling must preserve, the
  standard error of a sample correlation is ``1 / sqrt(R) = 0.00707``.
  ``MAX_ABS_OFFDIAG_CORR = 0.03`` is four of those.

The exact figures A-24 achieved are additionally pinned below, because the fixtures and
the keys are deterministic: reproducing them is a bit-level statement that production now
computes what A-24's harness computed.
"""

import numpy as np
import pytest

from counter_based_rng import CounterBasedRNG


R = 20_000

MAX_ABS_Z_MEAN = 4.0
MAX_REL_ERR_VARIANCE = 0.04
MAX_ABS_OFFDIAG_CORR = 0.03

# --- A-24 cf_sampler_validation.json, reproduced exactly by the production samplers ---
A24_MULTINOMIAL_MAX_ABS_Z_MEAN = 1.4996320536372427
A24_MULTINOMIAL_MAX_REL_ERR_COV = 0.014262430661215575
A24_GROUPED_MAX_ABS_Z_MEAN = 2.4312743052105836
A24_GROUPED_MAX_REL_ERR_VAR = 0.023182280248005817
A24_GROUPED_MAX_ABS_OFFDIAG_CORR = 0.014510511164656787

MULTINOMIAL_N = 300
MULTINOMIAL_P = np.array([0.1, 0.25, 0.05, 0.3, 0.3])
GROUPED_CELLS = np.array([[120, 0, 35], [800, 4, 60]], dtype=np.int64)
GROUPED_P = 0.03


def _multinomial_replicates():
    return np.array(
        [
            CounterBasedRNG(7, 2030.0, t, "val_mult").multinomial(
                MULTINOMIAL_N, MULTINOMIAL_P
            )
            for t in range(R)
        ]
    )


def _grouped_replicates():
    return np.array(
        [
            CounterBasedRNG(7, 2030.0, t, "adult_background_mortality").binomial(
                GROUPED_CELLS, GROUPED_P
            )
            for t in range(R)
        ]
    ).reshape(R, -1)


@pytest.fixture(scope="module")
def multinomial_draws():
    return _multinomial_replicates()


@pytest.fixture(scope="module")
def grouped_draws():
    return _grouped_replicates()


# ---------------------------------------------------------------- coupled multinomial


def test_coupled_multinomial_means_match_theory(multinomial_draws):
    mean_theory = MULTINOMIAL_N * MULTINOMIAL_P
    var_theory = MULTINOMIAL_N * MULTINOMIAL_P * (1 - MULTINOMIAL_P)
    z = (multinomial_draws.mean(0) - mean_theory) / np.sqrt(var_theory / R)
    max_abs_z = float(np.max(np.abs(z)))
    assert max_abs_z < MAX_ABS_Z_MEAN, (
        f"coupled multinomial means depart from Multinomial({MULTINOMIAL_N}, p): "
        f"max |z| = {max_abs_z:.4f} at R = {R}. The coupling must not change the "
        "distribution."
    )
    assert max_abs_z == pytest.approx(A24_MULTINOMIAL_MAX_ABS_Z_MEAN, abs=1e-9)


def test_coupled_multinomial_covariance_matches_theory(multinomial_draws):
    cov_theory = MULTINOMIAL_N * (
        np.diag(MULTINOMIAL_P) - np.outer(MULTINOMIAL_P, MULTINOMIAL_P)
    )
    rel_err = float(
        np.max(np.abs(np.cov(multinomial_draws.T) - cov_theory))
        / np.max(np.abs(cov_theory))
    )
    assert rel_err < MAX_REL_ERR_VARIANCE, (
        f"coupled multinomial covariance departs from theory by {rel_err:.4%} at "
        f"R = {R}; the full covariance structure, not just the marginals, must be "
        "preserved."
    )
    assert rel_err == pytest.approx(A24_MULTINOMIAL_MAX_REL_ERR_COV, abs=1e-9)


def test_coupled_multinomial_total_is_exactly_n(multinomial_draws):
    assert set(multinomial_draws.sum(1).tolist()) == {MULTINOMIAL_N}
    assert (multinomial_draws >= 0).all()


@pytest.mark.parametrize(
    "n, pvals",
    [
        (0, [0.2, 0.3, 0.5]),
        (1, [0.2, 0.3, 0.5]),
        (7, [1.0]),
        (500, [0.0, 1.0, 0.0]),
        (500, [0.5, 0.0, 0.5]),
        (10**7, [0.25, 0.25, 0.25, 0.25]),
    ],
)
def test_coupled_multinomial_degenerate_inputs_keep_the_exact_total(n, pvals):
    draw = CounterBasedRNG(3, 2030.0, 5, "migration").multinomial(n, pvals)
    assert int(draw.sum()) == n
    assert (draw >= 0).all()
    assert draw.shape == (len(pvals),)


def test_coupled_multinomial_is_bit_reproducible_at_a_fixed_key():
    first = CounterBasedRNG(42, 2025.0, 200, "migration").for_stratum("m").multinomial(
        9_999, [0.11, 0.07, 0.42, 0.4]
    )
    second = CounterBasedRNG(42, 2025.0, 200, "migration").for_stratum("m").multinomial(
        9_999, [0.11, 0.07, 0.42, 0.4]
    )
    np.testing.assert_array_equal(first, second)


def test_multinomial_rejects_the_size_argument_the_model_never_uses():
    with pytest.raises(NotImplementedError):
        CounterBasedRNG(42, 2025.0, 200, "migration").multinomial(
            10, [0.5, 0.5], size=3
        )


# ------------------------------------------------- grouped adult background mortality


def test_grouped_background_mortality_means_match_independent_binomials(grouped_draws):
    cells = GROUPED_CELLS.ravel()
    populated = cells > 0
    mean_theory = cells * GROUPED_P
    var_theory = cells * GROUPED_P * (1 - GROUPED_P)
    z = (grouped_draws.mean(0) - mean_theory)[populated] / np.sqrt(
        var_theory[populated] / R
    )
    max_abs_z = float(np.max(np.abs(z)))
    assert max_abs_z < MAX_ABS_Z_MEAN, (
        f"grouped background mortality means depart from independent "
        f"Binomial(n_cell, p): max |z| = {max_abs_z:.4f} at R = {R}."
    )
    assert max_abs_z == pytest.approx(A24_GROUPED_MAX_ABS_Z_MEAN, abs=1e-9)
    assert (grouped_draws[:, ~populated] == 0).all()


def test_grouped_background_mortality_variances_match_independent_binomials(
    grouped_draws,
):
    cells = GROUPED_CELLS.ravel()
    populated = cells > 0
    var_theory = cells * GROUPED_P * (1 - GROUPED_P)
    rel_err = float(
        np.max(
            np.abs(grouped_draws.var(0, ddof=1) - var_theory)[populated]
            / var_theory[populated]
        )
    )
    assert rel_err < MAX_REL_ERR_VARIANCE, (
        f"grouped background mortality per-cell variance departs from "
        f"n_cell p (1 - p) by {rel_err:.4%} at R = {R}."
    )
    assert rel_err == pytest.approx(A24_GROUPED_MAX_REL_ERR_VAR, abs=1e-9)


def test_grouped_background_mortality_leaves_cells_uncorrelated(grouped_draws):
    """The coupling ties the *total*; it must not induce correlation between cells."""
    populated = GROUPED_CELLS.ravel() > 0
    corr = np.corrcoef(grouped_draws[:, populated].T)
    max_offdiag = float(np.max(np.abs(corr - np.eye(int(populated.sum())))))
    assert max_offdiag < MAX_ABS_OFFDIAG_CORR, (
        f"grouped background mortality induced between-cell correlation "
        f"{max_offdiag:.4f} at R = {R}; independent binomials are uncorrelated."
    )
    assert max_offdiag == pytest.approx(A24_GROUPED_MAX_ABS_OFFDIAG_CORR, abs=1e-9)


def test_grouped_background_mortality_total_variance_matches_theory(grouped_draws):
    cells = GROUPED_CELLS.ravel()
    var_theory = float(cells.sum() * GROUPED_P * (1 - GROUPED_P))
    var_emp = float(grouped_draws.sum(1).var(ddof=1))
    rel_err = abs(var_emp - var_theory) / var_theory
    assert rel_err < MAX_REL_ERR_VARIANCE, (
        f"grouped background mortality total variance {var_emp:.4f} departs from "
        f"Binomial(sum(n), p) variance {var_theory:.4f} by {rel_err:.4%} at R = {R}."
    )


def test_grouped_background_mortality_never_kills_more_people_than_a_cell_holds(
    grouped_draws,
):
    assert (grouped_draws <= GROUPED_CELLS.ravel()).all()
    assert (grouped_draws >= 0).all()


@pytest.mark.parametrize("p", [0.0, 1.0, 1e-9, 0.5])
@pytest.mark.parametrize(
    "cells",
    [
        np.zeros((2, 3), dtype=np.int64),
        np.array([[0, 0, 0], [0, 7, 0]], dtype=np.int64),
        np.array([[1, 1], [1, 1]], dtype=np.int64),
        np.full((2, 2, 2), 10_000, dtype=np.int64),
    ],
)
def test_grouped_background_mortality_boundary_inputs_stay_in_bounds(cells, p):
    draw = CounterBasedRNG(5, 2030.0, 11, "adult_background_mortality").binomial(
        cells, p
    )
    assert draw.shape == cells.shape
    assert (draw >= 0).all()
    assert (draw <= cells).all()
    if p == 1.0:
        np.testing.assert_array_equal(draw, cells)
    if p == 0.0:
        assert int(draw.sum()) == 0


def test_grouped_background_mortality_is_bit_reproducible_at_a_fixed_key():
    cells = np.array([[300, 12, 0], [58, 4_000, 9]], dtype=np.int64)
    first = CounterBasedRNG(42, 2025.0, 200, "adult_background_mortality").binomial(
        cells, 0.004
    )
    second = CounterBasedRNG(42, 2025.0, 200, "adult_background_mortality").binomial(
        cells, 0.004
    )
    np.testing.assert_array_equal(first, second)


def test_only_adult_background_mortality_takes_the_grouped_path():
    """Every other process must keep the per-cell binomial, unchanged by A-28."""
    cells = np.array([[300, 12, 0], [58, 4_000, 9]], dtype=np.int64)
    grouped = CounterBasedRNG(42, 2025.0, 200, "adult_background_mortality").binomial(
        cells, 0.004
    )
    for process in ("transmission", "hiv_mortality", "childhood_background_mortality"):
        per_cell = CounterBasedRNG(42, 2025.0, 200, process).binomial(cells, 0.004)
        assert not np.array_equal(grouped, per_cell)
    # 1-D and scalar background-mortality draws are not grouped either.
    one_d = CounterBasedRNG(42, 2025.0, 200, "adult_background_mortality").binomial(
        np.array([300, 12, 0, 58], dtype=np.int64), 0.004
    )
    assert one_d.shape == (4,)


# ------------------------------------------------------------ the CRN property itself


def test_coupled_multinomial_moves_smoothly_under_a_one_person_weight_change():
    """The regression test for the A-24 defect.

    Reproduces A-24's ``raw/multinomial_sampler_probe.json`` fixture: ten age-sex
    migration weights, one of them raised by a single person.  NumPy's rejection-based
    ``Generator.multinomial`` moved the allocation by a mean L1 of 0.351 and up to 188
    people -- 66x the 0.0053 the weight change itself warrants -- and agreed with the
    unperturbed draw in only 94.45% of trials.  The coupled sampler must stay at A-24's
    measured 99.2% / 0.016 / 2.
    """
    weights = np.array(
        [400e3, 410e3, 450e3, 470e3, 500e3, 520e3, 480e3, 500e3, 470e3, 490e3]
    )
    base = weights / weights.sum()
    perturbed = weights.copy()
    perturbed[9] += 1
    perturbed = perturbed / perturbed.sum()
    expected_shift = float(np.abs(14_000 * (base - perturbed)).sum())

    l1 = np.array(
        [
            int(
                np.abs(
                    CounterBasedRNG(42, 2030.0, 200 + t, "migration").multinomial(
                        14_000, base
                    )
                    - CounterBasedRNG(42, 2030.0, 200 + t, "migration").multinomial(
                        14_000, perturbed
                    )
                ).sum()
            )
            for t in range(2_000)
        ]
    )

    share_identical = float(np.mean(l1 == 0))
    mean_l1 = float(l1.mean())
    max_l1 = int(l1.max())

    assert expected_shift == pytest.approx(0.005346401176442583, abs=1e-12)
    # Pinned to A-24's coupled_inverse_cdf column, not to the production column.
    assert share_identical == pytest.approx(0.992, abs=1e-12)
    assert mean_l1 == pytest.approx(0.016, abs=1e-12)
    assert max_l1 == 2, (
        f"a one-person weight change moved the allocation by up to {max_l1} people. "
        "A rejection-based or otherwise non-monotone multinomial has been "
        "reintroduced; this is the A-24 defect and it destroys CRN pairing."
    )


def test_grouped_background_mortality_does_not_rerandomise_relocated_people():
    """Moving people between cells must not redraw who dies.

    The intervention moves people between PrEP slices without changing anything about
    them.  Under per-cell keying every relocated person is re-randomised; under the
    group-level coupling the total is drawn once, so the death count moves only as far
    as the relocation warrants.
    """
    reference = np.array([[500, 300, 0, 0], [900, 120, 40, 0]], dtype=np.int64)
    moved = reference.copy()
    moved[0, 2] += 10  # ten people relocated into an adjacent cell
    moved[0, 0] -= 10
    p = 0.01

    grouped_delta, per_cell_delta = [], []
    for t in range(400):
        a = CounterBasedRNG(9, 2030.0, t, "adult_background_mortality").binomial(
            reference, p
        )
        b = CounterBasedRNG(9, 2030.0, t, "adult_background_mortality").binomial(
            moved, p
        )
        grouped_delta.append(abs(int(a.sum()) - int(b.sum())))
        c = CounterBasedRNG(9, 2030.0, t, "transmission").binomial(reference, p)
        d = CounterBasedRNG(9, 2030.0, t, "transmission").binomial(moved, p)
        per_cell_delta.append(abs(int(c.sum()) - int(d.sum())))

    grouped_mean = float(np.mean(grouped_delta))
    per_cell_mean = float(np.mean(per_cell_delta))
    assert grouped_mean < per_cell_mean, (
        f"relocating ten people changed the grouped death total by {grouped_mean:.3f} "
        f"on average versus {per_cell_mean:.3f} for per-cell keying; the group-level "
        "coupling is not coupling."
    )
    assert max(grouped_delta) <= 1, (
        "the grouped total moved by more than one death when people were merely "
        "relocated between cells"
    )


def test_coupled_multinomial_rejects_negative_n():
    """``Generator.multinomial`` raised on negative n; the coupling must not silently
    hand the last category a negative count."""
    with pytest.raises(ValueError, match="non-negative"):
        CounterBasedRNG(42, 2025.0, 200, "migration").multinomial(-5, [0.5, 0.5])
