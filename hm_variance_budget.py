"""
History matching -- the four-term variance budget.

    I_i(x) = | E[f_i(x)] - z_i | / sqrt( V_obs,i + V_code,i + V_ensemble,i + V_disc,i )

This module assembles the denominator and, just as importantly, records *how*
each term was obtained.  Every term carries a ``derivation`` string and a
``status`` in {"derived", "assumption", "structural_zero", "underivable"}.  The
budget is emitted as a table so the whole denominator is auditable, because the
methodological claim of this build is that the tolerance is *derived from an
uncertainty budget* rather than *chosen to produce acceptance*
(HISTORY_MATCHING_BUILD_SPEC.md sec. 3, sec. 8.1; PHASE2_AGENT_HANDOFF sec. 1A.4).

The four terms
--------------
V_obs
    Observation uncertainty.  Derivations implemented:
      ``published_interval``  -- sigma = (high - low) / z_{0.975 - 0.025}, default
        divisor 3.92 for a 95% interval.  Verified for UNAIDS: the Methods annex
        states the published bounds "define the range within which the true value
        lies in 95% of cases".
      ``delay_correction``    -- sigma = fraction * |adjusted - reported|, the
        Stichting HIV Monitoring notification-delay adjustment (SHM adjusts
        counts "for the delay in notification to Stichting HIV Monitoring").
      ``explicit_sigma``      -- a sigma supplied directly by the register.
      ``underivable``         -- no source exists.  Contributes 0.0 and is
        flagged.  A zero contribution SHRINKS the denominator and therefore
        RULES OUT MORE, so the resulting NROY is a subset of the true NROY:
        an under-estimate, never an over-estimate.  See ``BudgetTerm.direction``.

V_code
    Emulator variance.  STRUCTURAL ZERO in this build: the simulator is run
    directly (~60 s/run), no Gaussian process emulator is constructed, so there
    is no emulator posterior variance to carry (spec sec. 3.2).  Any non-zero
    value here is a specification error and raises.

V_ensemble
    Simulator stochasticity.  Andrianakis et al. use either the mean or the 95th
    percentile of the sample variances across repeat stochastic runs at fixed
    input; both are supported and the choice is recorded.

    A-5 now supplies a post-fix, per-target data file based on 320 stochastic
    simulations. The runner loads those per-run variances and divides by ``k``
    when each parameter point is evaluated as a mean of ``k`` replicates.
    ``v_ensemble_unavailable`` remains as an explicit conservative fallback for
    synthetic tests and incomplete external registers; production-path plans
    are rejected unless they name a measured file.

V_disc
    Model discrepancy.  Andrianakis et al. set it to "10% of the variance of the
    simulator output training data obtained at each wave", declared openly as an
    illustrative choice; ``fraction_of_output_variance`` reproduces that.  A
    tier-2 (model-derived, e.g. Spectrum CSAVR) target may carry an additional
    increment because comparing this model to another model's back-calculation
    is a model-to-model comparison; that increment is a separate, separately
    reported term.

    ``relative_to_target`` is also supported for declared sensitivity analyses.
    It encodes a directly interpretable proportional model-error allowance,
    ``V_disc = (fraction * |z|)^2``.  It is an assumption, not an estimate, and
    must never be selected because it produces a convenient NROY size.

Guardrail
---------
No function in this module chooses a value in order to make anything pass.  The
multipliers are inputs, and ``sensitivity_curve`` in ``hm_diagnostics`` exists so
that the dependence of the NROY size on them is *reported* rather than tuned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np


#: Divisor converting the width of a symmetric 95% interval to a standard deviation.
#: 2 * 1.959964 = 3.919928.
NORMAL_95_INTERVAL_DIVISOR = 3.92

TermStatus = Literal["derived", "assumption", "structural_zero", "underivable"]

#: Which way an imperfectly-known term biases the NROY set.
#: "over_exclusion" -- the term is under-stated, denominator too small, NROY too small.
#: "under_exclusion" -- the term is over-stated, denominator too large, NROY too large.
BiasDirection = Literal["over_exclusion", "under_exclusion", "none"]


class VarianceSpecificationError(ValueError):
    """Raised when a variance term is specified in a way the method forbids."""


@dataclass(frozen=True)
class BudgetTerm:
    """One term of one target's variance budget, with its provenance."""

    name: str
    variance: float
    status: TermStatus
    derivation: str
    direction: BiasDirection = "none"

    def __post_init__(self) -> None:
        if not np.isfinite(self.variance):
            raise VarianceSpecificationError(f"{self.name}: variance must be finite, got {self.variance}")
        if self.variance < 0.0:
            raise VarianceSpecificationError(f"{self.name}: variance must be non-negative, got {self.variance}")

    @property
    def sigma(self) -> float:
        return float(np.sqrt(self.variance))


@dataclass(frozen=True)
class VarianceBudget:
    """The full four-term budget for one scalar target (one series, one year)."""

    target_key: str
    v_obs: BudgetTerm
    v_code: BudgetTerm
    v_ensemble: BudgetTerm
    v_disc: BudgetTerm
    extra_terms: tuple[BudgetTerm, ...] = field(default_factory=tuple)

    @property
    def terms(self) -> tuple[BudgetTerm, ...]:
        return (self.v_obs, self.v_code, self.v_ensemble, self.v_disc) + tuple(self.extra_terms)

    @property
    def total_variance(self) -> float:
        return float(sum(term.variance for term in self.terms))

    @property
    def total_sigma(self) -> float:
        return float(np.sqrt(self.total_variance))

    @property
    def dominant_term(self) -> str:
        """Name of the term contributing the most variance.  Ties go to the first."""
        return max(self.terms, key=lambda term: term.variance).name

    @property
    def underivable_terms(self) -> tuple[str, ...]:
        return tuple(term.name for term in self.terms if term.status == "underivable")

    @property
    def assumed_terms(self) -> tuple[str, ...]:
        return tuple(term.name for term in self.terms if term.status == "assumption")

    def share(self, name: str) -> float:
        """Fraction of the total variance contributed by term ``name``."""
        total = self.total_variance
        if total <= 0.0:
            return float("nan")
        for term in self.terms:
            if term.name == name:
                return float(term.variance / total)
        raise KeyError(f"{self.target_key}: no term named {name!r}")

    def as_row(self) -> dict:
        """One flat record for the reportable budget table (spec sec. 11)."""
        row: dict = {"target_key": self.target_key}
        for term in self.terms:
            row[f"{term.name}_variance"] = term.variance
            row[f"{term.name}_sigma"] = term.sigma
            row[f"{term.name}_status"] = term.status
            row[f"{term.name}_derivation"] = term.derivation
            row[f"{term.name}_bias_direction"] = term.direction
        row["total_variance"] = self.total_variance
        row["total_sigma"] = self.total_sigma
        row["dominant_term"] = self.dominant_term
        row["underivable_terms"] = ";".join(self.underivable_terms)
        row["assumed_terms"] = ";".join(self.assumed_terms)
        return row


# --------------------------------------------------------------------------
# V_obs
# --------------------------------------------------------------------------

def sigma_from_published_interval(
    low: float,
    high: float,
    *,
    divisor: float = NORMAL_95_INTERVAL_DIVISOR,
) -> float:
    """Standard deviation implied by a published uncertainty interval.

    ``divisor`` is 3.92 for a 95% interval.  Change it only with a source that
    says the published bounds are something other than 95%, and say so.

    Published UNAIDS bounds are asymmetric about the mid estimate; treating
    ``(high - low) / 3.92`` as a single sigma is a symmetric-normal
    *approximation* to an asymmetric interval.  Label it as such downstream.
    """
    if not (np.isfinite(low) and np.isfinite(high)):
        raise VarianceSpecificationError(f"Interval bounds must be finite, got ({low}, {high})")
    if high < low:
        raise VarianceSpecificationError(f"Interval upper bound below lower bound: ({low}, {high})")
    if divisor <= 0.0:
        raise VarianceSpecificationError(f"Interval divisor must be positive, got {divisor}")
    return float((float(high) - float(low)) / float(divisor))


def v_obs_from_interval(
    low: float,
    high: float,
    *,
    divisor: float = NORMAL_95_INTERVAL_DIVISOR,
    source: str = "published interval",
) -> BudgetTerm:
    sigma = sigma_from_published_interval(low, high, divisor=divisor)
    return BudgetTerm(
        name="v_obs",
        variance=sigma ** 2,
        status="derived",
        derivation=(
            f"sigma = (high - low) / {divisor} = ({high:g} - {low:g}) / {divisor} = {sigma:.6g}; "
            f"symmetric-normal approximation to a published 95% interval [{source}]"
        ),
        direction="none",
    )


def v_obs_from_delay_correction(
    reported: float | None,
    adjusted: float | None,
    *,
    fraction: float = 1.0,
    extrapolated: bool = False,
    extrapolation_rule: str = "",
    source: str = "Stichting HIV Monitoring notification-delay adjustment",
) -> BudgetTerm:
    """V_obs from the reported-vs-delay-adjusted gap of a surveillance count.

    The gap ``|adjusted - reported|`` is the *magnitude of the correction SHM
    itself applied*, not a published error on the corrected figure.  Using
    ``fraction * |adjusted - reported|`` as a standard deviation is therefore an
    ASSUMPTION about how well that correction is known, not a derived quantity.
    ``fraction = 1.0`` says the correction could be wrong by its own size, which
    is deliberately conservative.

    A larger ``fraction`` enlarges the denominator and therefore enlarges NROY,
    which is the direction the tuning prohibition guards against.  It is
    consequently reported in the sensitivity suite and must not be moved to make
    NROY non-empty.
    """
    if fraction < 0.0 or not np.isfinite(fraction):
        raise VarianceSpecificationError(f"delay-correction fraction must be finite and >= 0, got {fraction}")
    if reported is None or adjusted is None or not (np.isfinite(reported) and np.isfinite(adjusted)):
        if not extrapolated:
            return BudgetTerm(
                name="v_obs",
                variance=0.0,
                status="underivable",
                derivation=(
                    "no delay-adjusted column published for this year and no extrapolation "
                    "rule supplied; contributes 0.0"
                ),
                direction="over_exclusion",
            )
        return BudgetTerm(
            name="v_obs",
            variance=0.0,
            status="derived",
            derivation=(
                f"delay correction taken as zero by the stated extrapolation rule: "
                f"{extrapolation_rule} [{source}]"
            ),
            direction="none",
        )
    gap = abs(float(adjusted) - float(reported))
    sigma = float(fraction) * gap
    return BudgetTerm(
        name="v_obs",
        variance=sigma ** 2,
        # A non-zero gap turned into a sigma is an ASSUMPTION about how well SHM's own
        # correction is known, at any fraction including 1.0.  An observed gap of exactly
        # zero is a derived fact about this year: no correction was applied.
        status="assumption" if gap > 0 else "derived",
        derivation=(
            f"sigma = {fraction:g} * |adjusted - reported| = {fraction:g} * |{adjusted:g} - {reported:g}| "
            f"= {sigma:.6g}; the reporting-delay correction treated as carrying "
            f"{fraction:g}x its own magnitude as uncertainty [{source}]"
        ),
        direction="under_exclusion" if fraction > 1.0 else "none",
    )


def v_obs_underivable(reason: str) -> BudgetTerm:
    """No observation-uncertainty source exists for this target.

    Contributes 0.0.  This SHRINKS the denominator, so implausibilities computed
    with it are UPPER bounds and the resulting NROY is a SUBSET of the true NROY.
    Every target carrying this term must appear in the escalation list
    (spec sec. 10, third bullet).
    """
    return BudgetTerm(
        name="v_obs",
        variance=0.0,
        status="underivable",
        derivation=f"no observation-uncertainty source: {reason}; contributes 0.0",
        direction="over_exclusion",
    )


def v_obs_explicit(sigma: float, derivation: str, *, status: TermStatus = "assumption") -> BudgetTerm:
    if sigma < 0.0 or not np.isfinite(sigma):
        raise VarianceSpecificationError(f"explicit sigma must be finite and >= 0, got {sigma}")
    return BudgetTerm(name="v_obs", variance=float(sigma) ** 2, status=status, derivation=derivation)


# --------------------------------------------------------------------------
# V_code
# --------------------------------------------------------------------------

def v_code_no_emulator() -> BudgetTerm:
    """Structural zero: the simulator is evaluated directly, so there is no emulator.

    Spec sec. 3.2 / sec. 8.3.  If an emulator is ever introduced, this function
    must be replaced with the emulator's posterior variance, not amended in place.
    """
    return BudgetTerm(
        name="v_code",
        variance=0.0,
        status="structural_zero",
        derivation=(
            "no emulator is constructed; the simulator is evaluated directly at every "
            "design point (~60 s/run), so there is no emulator posterior variance "
            "[spec sec. 3.2]"
        ),
        direction="none",
    )


# --------------------------------------------------------------------------
# V_ensemble
# --------------------------------------------------------------------------

def v_ensemble_from_repeat_runs(
    replicate_values: Sequence[Sequence[float]] | np.ndarray,
    *,
    statistic: Literal["mean", "p95"] = "p95",
    source: str = "",
) -> BudgetTerm:
    """V_ensemble from repeat stochastic runs at fixed inputs.

    ``replicate_values`` is ``(n_design_points, n_replicates)``: the sample
    variance is computed at each design point and then summarised across design
    points.  Andrianakis et al. describe both summaries; ``"p95"`` is the
    conservative one they use in the case study, ``"mean"`` the simpler one.
    Both are recorded so the report can state which was used and show the other.

    The number of replicates is carried into ``derivation`` because a variance
    estimated from a handful of runs has a wide sampling distribution of its own
    (handoff sec. 1A.1, "State n").
    """
    array = np.asarray(replicate_values, dtype=float)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise VarianceSpecificationError("replicate_values must be 1-D or 2-D")
    n_points, n_reps = array.shape
    if n_reps < 2:
        raise VarianceSpecificationError(
            f"V_ensemble needs at least 2 replicates per design point, got {n_reps}"
        )
    sample_variances = np.var(array, axis=1, ddof=1)
    if statistic == "mean":
        value = float(np.mean(sample_variances))
    elif statistic == "p95":
        value = float(np.percentile(sample_variances, 95))
    else:
        raise VarianceSpecificationError(f"statistic must be 'mean' or 'p95', got {statistic!r}")
    return BudgetTerm(
        name="v_ensemble",
        variance=value,
        status="derived",
        derivation=(
            f"{statistic} of the per-design-point sample variances across "
            f"n_replicates={n_reps} at n_design_points={n_points} "
            f"(mean={np.mean(sample_variances):.6g}, p95={np.percentile(sample_variances, 95):.6g})"
            + (f" [{source}]" if source else "")
        ),
        direction="none",
    )


def v_ensemble_unavailable(reason: str) -> BudgetTerm:
    """No seed-variability estimate exists for this target.  Contributes 0.0.

    See the module docstring: the only seed-variability artefact in the repo
    holds economic outcomes, not calibration-target series, and is pre-fix.
    Omitting the term shrinks the denominator, so it biases towards
    OVER-exclusion -- the safe direction to be wrong, but still wrong.
    """
    return BudgetTerm(
        name="v_ensemble",
        variance=0.0,
        status="underivable",
        derivation=f"no seed-variability estimate available for this target: {reason}; contributes 0.0",
        direction="over_exclusion",
    )


# --------------------------------------------------------------------------
# V_disc
# --------------------------------------------------------------------------

def v_disc_fraction_of_output_variance(
    simulator_outputs: Sequence[float] | np.ndarray,
    *,
    multiplier: float = 0.10,
    source: str = "Andrianakis et al. 2015, PLoS Comput Biol 11(1):e1003968",
) -> BudgetTerm:
    """Andrianakis et al.'s discrepancy: a fraction of the wave's output variance.

    "equal to 10% of the variance of the simulator output training data obtained
    at each wave" -- declared by those authors as an illustrative choice, and
    declared here as the same.  It is recomputed per wave from that wave's own
    design, so it is not a fixed number carried between waves.
    """
    if multiplier < 0.0 or not np.isfinite(multiplier):
        raise VarianceSpecificationError(f"discrepancy multiplier must be finite and >= 0, got {multiplier}")
    values = np.asarray(simulator_outputs, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        raise VarianceSpecificationError(
            f"need at least 2 finite simulator outputs to take a variance, got {values.size}"
        )
    output_variance = float(np.var(values, ddof=1))
    return BudgetTerm(
        name="v_disc",
        variance=float(multiplier) * output_variance,
        status="assumption",
        derivation=(
            f"{multiplier:g} x Var(simulator output training data) = {multiplier:g} x "
            f"{output_variance:.6g} over n={values.size} evaluations "
            f"[method of {source}; the multiplier is a declared illustrative choice, "
            f"not a fitted quantity]"
        ),
        direction="none",
    )


def v_disc_relative_to_target(
    observed_target: float,
    *,
    fraction: float = 0.10,
    rationale: str = "declared proportional model-discrepancy sensitivity",
) -> BudgetTerm:
    """Model discrepancy equal to a declared fraction of the target scale.

    This is useful as a sensitivity analysis when the training-output-variance
    rule gives radically different effective percentage tolerances across
    targets.  ``fraction`` is a standard-deviation fraction: 0.10 means a
    one-sigma discrepancy allowance equal to 10% of ``|z|``.
    """
    if fraction < 0.0 or not np.isfinite(fraction):
        raise VarianceSpecificationError(
            f"target-relative discrepancy fraction must be finite and >= 0, got {fraction}"
        )
    target = float(observed_target)
    if not np.isfinite(target):
        raise VarianceSpecificationError(f"observed target must be finite, got {observed_target!r}")
    sigma = float(fraction) * abs(target)
    return BudgetTerm(
        name="v_disc",
        variance=sigma * sigma,
        status="assumption",
        derivation=(
            f"({fraction:g} x |z={target:.6g}|)^2 = {sigma * sigma:.6g} "
            f"[{rationale}; declared before scoring, not fitted to NROY]"
        ),
        direction="none",
    )


def v_disc_tier2_increment(
    base_variance: float,
    *,
    increment_multiplier: float,
    rationale: str,
) -> BudgetTerm:
    """Extra discrepancy for a target that is itself another model's output.

    UNAIDS PLHIV / new_infections / AIDS-deaths for the Netherlands are Spectrum
    CSAVR outputs -- a Bayesian back-calculation fitted to the same case
    surveillance and vital registration this model already uses.  Comparing this
    simulator to them is a model-to-model comparison, so it carries a
    discrepancy the published interval does not describe.

    This is reported as its own term rather than folded into ``v_disc`` so that
    its effect on the denominator is visible in the budget table.
    """
    if increment_multiplier < 0.0 or not np.isfinite(increment_multiplier):
        raise VarianceSpecificationError(
            f"tier-2 increment multiplier must be finite and >= 0, got {increment_multiplier}"
        )
    return BudgetTerm(
        name="v_disc_tier2",
        variance=float(increment_multiplier) * float(base_variance),
        status="assumption",
        derivation=(
            f"{increment_multiplier:g} x {base_variance:.6g}; model-to-model comparison "
            f"increment for a CSAVR-derived target. {rationale}"
        ),
        direction="under_exclusion" if increment_multiplier > 0 else "none",
    )


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def assemble(
    target_key: str,
    *,
    v_obs: BudgetTerm,
    v_ensemble: BudgetTerm,
    v_disc: BudgetTerm,
    v_code: BudgetTerm | None = None,
    extra_terms: Iterable[BudgetTerm] = (),
) -> VarianceBudget:
    """Assemble a four-term budget, enforcing the method's structural constraints."""
    v_code = v_code if v_code is not None else v_code_no_emulator()
    for term, expected in ((v_obs, "v_obs"), (v_code, "v_code"), (v_ensemble, "v_ensemble"), (v_disc, "v_disc")):
        if term.name != expected:
            raise VarianceSpecificationError(f"{target_key}: expected a {expected!r} term, got {term.name!r}")
    if v_code.variance != 0.0:
        raise VarianceSpecificationError(
            f"{target_key}: V_code must be zero in this build -- no emulator is used "
            f"(spec sec. 3.2, sec. 8.3). Got {v_code.variance}."
        )
    budget = VarianceBudget(
        target_key=target_key,
        v_obs=v_obs,
        v_code=v_code,
        v_ensemble=v_ensemble,
        v_disc=v_disc,
        extra_terms=tuple(extra_terms),
    )
    if budget.total_variance <= 0.0:
        raise VarianceSpecificationError(
            f"{target_key}: total variance is zero -- every term contributed nothing. "
            "That asserts the target is observed exactly and the model is perfect. "
            f"Underivable terms: {budget.underivable_terms}"
        )
    return budget


def budget_table(budgets: Iterable[VarianceBudget]) -> list[dict]:
    """The reportable variance-budget table: every target, every term, every derivation."""
    return [budget.as_row() for budget in budgets]


def budget_warnings(budgets: Iterable[VarianceBudget]) -> list[str]:
    """Human-readable warnings for every underivable or assumed term in the budget.

    These are escalation material (spec sec. 10), not decoration: an underivable
    V_obs is explicitly listed there as a stop-and-report condition.
    """
    messages: list[str] = []
    for budget in budgets:
        for term in budget.terms:
            if term.status == "underivable":
                messages.append(
                    f"[UNDERIVABLE] {budget.target_key}: {term.name} contributes 0.0 "
                    f"({term.derivation}). Bias direction: {term.direction}."
                )
            elif term.status == "assumption":
                messages.append(
                    f"[ASSUMPTION] {budget.target_key}: {term.name} = {term.variance:.6g} "
                    f"({term.derivation}). Bias direction: {term.direction}."
                )
    return messages
