"""
History matching -- implausibility measure.

This module is deliberately free of I/O, configuration and simulator concerns.
Every function here is pure so that the acceptance rule at the centre of the
calibration can be unit-tested against hand-computed cases.

Method reference
----------------
Andrianakis I, Vernon IR, McCreesh N, McKinley TJ, Oakley JE, Nsubuga RN,
Goldstein M, White RG (2015). "Bayesian History Matching of Complex
Infectious Disease Models Using Emulation: A Tutorial and a Case Study on HIV
in Uganda." PLoS Comput Biol 11(1):e1003968.

The implausibility of a candidate parameter vector ``x`` for a single scalar
target ``i`` is

    I_i(x) = | E[f_i(x)] - z_i | / sqrt( V_obs,i + V_code,i + V_ensemble,i + V_disc,i )

Andrianakis et al. write equation (8) in squared form with the same denominator;
the square root form used here is the standard presentation and is the one that
makes the I <= 3 cutoff a *number of standard deviations*, which is what the
Pukelsheim justification below actually bounds.  The two forms are monotone
transformations of one another, so they rule out identical parameter sets when
the cutoffs are squared/unsquared consistently.  This module uses the
standard-deviation form throughout; do not mix the two.

Cutoff
------
``IMPLAUSIBILITY_CUTOFF = 3.0``.

Justification: Pukelsheim F (1994), "The Three Sigma Rule", The American
Statistician 48(2):88-91.  For any *unimodal* distribution the
Vysochanskii-Petunin inequality gives

    Pr( |X - mu| >= 3 sigma )  <=  4/81  <  0.05

i.e. at least ~95.06% of the probability mass lies within three standard
deviations of the mean, whatever the shape of the distribution.  (For an
arbitrary, not-necessarily-unimodal distribution the Bienayme-Chebyshev bound
is weaker: 1/9, i.e. at least ~88.9%.)

NOTE FOR THE MANUSCRIPT: ``HISTORY_MATCHING_BUILD_SPEC.md`` sec. 2.3 states this
bound as "at least 93%".  That figure is wrong.  The unimodal bound is
1 - 4/81 = 95.06%, and the distribution-free bound is 1 - 1/9 = 88.9%.  Neither
is 93%.  Verified against Pukelsheim (1994) directly.

Combination across targets
--------------------------
The primary rule is the *maximum* over targets, which is non-compensatory: an
excellent fit to one target cannot offset a failure on another.  The second and
third largest are computed for robustness reporting only and must not be used
as the primary rule to enlarge the NROY set (spec sec. 8.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np


IMPLAUSIBILITY_CUTOFF = 3.0

#: Probability-mass bound behind the cutoff, for reporting.  See module docstring.
PUKELSHEIM_UNIMODAL_COVERAGE = 1.0 - 4.0 / 81.0          # 0.950617...
CHEBYSHEV_COVERAGE = 1.0 - 1.0 / 9.0                     # 0.888888...


class VarianceBudgetError(ValueError):
    """Raised when a variance budget cannot support an implausibility."""


def implausibility(
    simulated: float | np.ndarray,
    observed: float,
    total_variance: float,
) -> float | np.ndarray:
    """Implausibility of ``simulated`` against ``observed`` given a variance budget.

    Parameters
    ----------
    simulated
        Simulator output ``E[f_i(x)]``.  Scalar or array over candidates.
    observed
        The target value ``z_i``.
    total_variance
        ``V_obs + V_code + V_ensemble + V_disc`` for this target.  Must be
        strictly positive: a zero budget asserts the target is known exactly and
        the model is perfect, under which every candidate is infinitely
        implausible.  That is a specification error, not a result, so it raises.

    Returns
    -------
    ``|simulated - observed| / sqrt(total_variance)``.

    Notes
    -----
    ``np.nan`` in ``simulated`` propagates to ``np.nan``.  Callers decide
    whether a missing simulator output means "exclude the candidate" or "drop
    the target"; this function does not decide that for them.
    """
    variance = float(total_variance)
    if not np.isfinite(variance):
        raise VarianceBudgetError(f"Total variance must be finite, got {total_variance!r}")
    if variance <= 0.0:
        raise VarianceBudgetError(
            "Total variance must be strictly positive; a zero budget claims the "
            "target is observed exactly and the model is perfect. "
            f"Got {total_variance!r}."
        )
    return np.abs(np.asarray(simulated, dtype=float) - float(observed)) / np.sqrt(variance)


def implausibility_matrix(
    simulated: Mapping[str, Sequence[float]] | np.ndarray,
    observed: Mapping[str, float] | Sequence[float],
    total_variance: Mapping[str, float] | Sequence[float],
    *,
    target_order: Sequence[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Per-candidate, per-target implausibility.

    Returns ``(matrix, target_names)`` where ``matrix`` has shape
    ``(n_candidates, n_targets)`` and column ``j`` corresponds to
    ``target_names[j]``.
    """
    if isinstance(simulated, Mapping):
        names = list(target_order) if target_order is not None else list(simulated.keys())
        missing = [name for name in names if name not in simulated]
        if missing:
            raise KeyError(f"Simulated outputs missing targets: {missing}")
        columns = [np.asarray(simulated[name], dtype=float) for name in names]
        lengths = {column.shape[0] for column in columns}
        if len(lengths) != 1:
            raise ValueError(f"Simulated target columns have unequal lengths: {lengths}")
        matrix_sim = np.column_stack(columns) if columns else np.empty((0, 0))
        z = np.array([float(observed[name]) for name in names], dtype=float)
        v = np.array([float(total_variance[name]) for name in names], dtype=float)
    else:
        matrix_sim = np.asarray(simulated, dtype=float)
        if matrix_sim.ndim != 2:
            raise ValueError("Array form of `simulated` must be 2-D (candidates x targets)")
        names = (
            list(target_order)
            if target_order is not None
            else [f"target_{j}" for j in range(matrix_sim.shape[1])]
        )
        z = np.asarray(observed, dtype=float)
        v = np.asarray(total_variance, dtype=float)

    if matrix_sim.size and matrix_sim.shape[1] != len(names):
        raise ValueError(
            f"Simulated matrix has {matrix_sim.shape[1]} columns but {len(names)} target names"
        )
    if z.shape[0] != len(names) or v.shape[0] != len(names):
        raise ValueError("observed and total_variance must have one entry per target")
    if not np.all(np.isfinite(v)):
        raise VarianceBudgetError("All total variances must be finite")
    if np.any(v <= 0.0):
        bad = [names[j] for j in np.flatnonzero(v <= 0.0)]
        raise VarianceBudgetError(f"Targets with non-positive total variance: {bad}")

    return np.abs(matrix_sim - z) / np.sqrt(v), names


@dataclass(frozen=True)
class CombinedImplausibility:
    """Order statistics of the per-target implausibilities, per candidate."""

    i_max: np.ndarray
    i_second: np.ndarray
    i_third: np.ndarray
    binding_target: list[str | None]
    n_targets: int

    def nroy_mask(self, cutoff: float = IMPLAUSIBILITY_CUTOFF, rule: str = "max") -> np.ndarray:
        """Boolean mask of candidates not ruled out under ``rule``."""
        values = {"max": self.i_max, "second": self.i_second, "third": self.i_third}
        if rule not in values:
            raise ValueError(f"rule must be one of {sorted(values)}, got {rule!r}")
        series = np.asarray(values[rule], dtype=float)
        # np.nan (target undeterminable) and np.inf (simulator failure) both fall
        # outside NROY.  A candidate we could not score is not a candidate that fits.
        return np.isfinite(series) & (series <= float(cutoff))


def combine(
    matrix: np.ndarray,
    target_names: Sequence[str],
    *,
    nan_policy: str = "implausible",
) -> CombinedImplausibility:
    """Reduce a candidate-by-target implausibility matrix to order statistics.

    ``nan_policy``:
      - ``"implausible"`` (default): a missing simulator output makes the
        candidate maximally implausible.  A candidate the simulator could not
        evaluate is not evidence that the candidate fits.
      - ``"omit"``: drop missing entries from the order statistics for that
        candidate.  Use only when a target is genuinely undefined for that
        candidate for a reason you have written down.

    Where a candidate has fewer than two (three) finite targets, ``i_second``
    (``i_third``) is ``np.nan``.  ``binding_target`` names the target attaining
    ``i_max``, or ``None`` if undetermined.
    """
    work = np.asarray(matrix, dtype=float)
    if work.ndim != 2:
        raise ValueError("matrix must be 2-D (candidates x targets)")
    if work.shape[1] != len(target_names):
        raise ValueError("matrix column count must match target_names")
    if nan_policy not in {"implausible", "omit"}:
        raise ValueError(f"nan_policy must be 'implausible' or 'omit', got {nan_policy!r}")

    if nan_policy == "implausible":
        work = np.where(np.isnan(work), np.inf, work)

    n_candidates, n_targets = work.shape
    i_max = np.full(n_candidates, np.nan)
    i_second = np.full(n_candidates, np.nan)
    i_third = np.full(n_candidates, np.nan)
    binding: list[str | None] = [None] * n_candidates

    for row_index in range(n_candidates):
        row = work[row_index]
        usable = np.flatnonzero(~np.isnan(row))
        if usable.size == 0:
            continue
        values = row[usable]
        order = np.argsort(-values, kind="stable")
        i_max[row_index] = values[order[0]]
        binding[row_index] = target_names[usable[order[0]]]
        if usable.size >= 2:
            i_second[row_index] = values[order[1]]
        if usable.size >= 3:
            i_third[row_index] = values[order[2]]

    return CombinedImplausibility(
        i_max=i_max,
        i_second=i_second,
        i_third=i_third,
        binding_target=binding,
        n_targets=n_targets,
    )


def nroy_fraction(
    combined: CombinedImplausibility,
    *,
    cutoff: float = IMPLAUSIBILITY_CUTOFF,
    rule: str = "max",
) -> float:
    """Fraction of candidates not ruled out.  Not a probability -- see spec sec. 11."""
    mask = combined.nroy_mask(cutoff=cutoff, rule=rule)
    if mask.size == 0:
        return 0.0
    return float(mask.sum()) / float(mask.size)


def implausibility_matrix_pointwise(
    simulated: Mapping[str, Sequence[float]],
    observed: Mapping[str, float],
    variance: Mapping[str, Sequence[float]],
    *,
    target_order: Sequence[str],
) -> tuple[np.ndarray, list[str]]:
    """Per-candidate implausibility with a candidate-specific total variance.

    Used when V_ensemble depends on the input, ``s(x)(1 + 1/K)``.  A candidate
    whose simulated value is missing gets ``np.nan`` (ruled out by ``combine``).
    A finite simulated value with a non-finite or non-positive variance is a
    specification error and raises.
    """
    names = list(target_order)
    sim = np.column_stack([np.asarray(simulated[name], dtype=float) for name in names])
    var = np.column_stack([np.asarray(variance[name], dtype=float) for name in names])
    if sim.shape != var.shape:
        raise ValueError(f"simulated {sim.shape} and variance {var.shape} shapes differ")
    z = np.array([float(observed[name]) for name in names], dtype=float)
    scorable = np.isfinite(sim)
    bad = scorable & ~(np.isfinite(var) & (var > 0.0))
    if bad.any():
        columns = sorted({names[j] for j in np.flatnonzero(bad.any(axis=0))})
        raise VarianceBudgetError(f"Non-positive or non-finite pointwise variance for: {columns}")
    with np.errstate(invalid="ignore", divide="ignore"):
        matrix = np.where(scorable, np.abs(sim - z) / np.sqrt(np.where(scorable, var, 1.0)), np.nan)
    return matrix, names
