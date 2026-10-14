"""Pure diagnostic helpers for A-18's Jensen-mechanism test.

This module is diagnostic infrastructure only.  Nothing in the production
simulation imports it, and nothing in it mutates production behaviour outside
an explicit ``with`` block.

Three things live here:

1. ``parameterised_calculate_transmission`` -- a faithful copy of
   ``TransmissionEngine.calculate_transmission`` with the deterministic and
   stochastic branches split into two independent switches:

       contact_mode in {"expected", "drawn"}
           "expected" keeps the fractional per-cell contact expectation
           (production deterministic); "drawn" applies the production
           stochastic floor + Bernoulli split.

       draw_mode in {"expected", "binomial"}
           "expected" multiplies through by the stage prevalence and the
           per-contact transmission probability and defers integerisation to
           the aggregate residual allocator (production deterministic);
           "binomial" runs the production two-step binomial with the per-pass
           and final susceptible caps.

   The two "pure" corners must reproduce production byte for byte.  That
   equivalence is the guard on the copy and is asserted by
   ``tests/test_a18_jensen_mechanism.py`` and again by the runner on a full
   1975-2025 trajectory.  ("drawn", "expected") is the A-18 Step 1 hybrid.

2. ``HybridTransmission`` -- the opt-in context manager that routes an
   otherwise deterministic engine through one of the hybrid variants.

3. ``jensen_bias_from_trace`` -- the direct ``f(E[n]) - E[f(n)]`` arithmetic,
   evaluated both for the functional form the model actually uses and for the
   concave per-person form the A-18 brief hypothesises.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


CONTACT_MODES = ("expected", "drawn")

#: Curvature forms available to the A-18 remedy-C probe.
#:
#: "model"        -- the second derivative of the function the model actually
#:                   uses.  Expected infections per cell are n * lambda, which
#:                   is affine in n, so this is identically zero and the
#:                   correction is an exact no-op.  Run anyway, because a
#:                   measured no-op is evidence and an asserted one is not.
#: "hypothesised" -- the second derivative of the concave per-person form the
#:                   A-18 brief assumes, S * (1 - (1-lambda)^(n/S)).  Reported
#:                   to BOUND what remedy C could achieve, not to adopt it.
CORRECTION_FORMS = ("model", "hypothesised")
DRAW_MODES = ("expected", "binomial")

#: The only three combinations A-18 uses.  ("expected", "binomial") is refused
#: because the two-step binomial needs an integer trial count.
VALID_MODE_PAIRS = {
    ("expected", "expected"),   # production deterministic
    ("drawn", "binomial"),      # production stochastic
    ("drawn", "expected"),      # A-18 Step 1 hybrid: Jensen channel only
}


@dataclass
class TransmissionTrace:
    """Per-stratum record of the quantities the Jensen arithmetic needs."""

    trace_years: frozenset = field(default_factory=frozenset)
    rows: list = field(default_factory=list)
    year: float | None = None
    step: int | None = None

    @property
    def active(self) -> bool:
        return self.year is not None and float(self.year) in self.trace_years

    def record(self, **row) -> None:
        row["year"] = float(self.year)
        row["step"] = int(self.step)
        self.rows.append(row)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def parameterised_calculate_transmission(
    self,
    population: np.ndarray,
    contacts: np.ndarray,
    rng,
    deterministic: bool = False,
    *,
    contact_mode: str = "expected",
    draw_mode: str = "expected",
    correction_form: str | None = None,
    trace: TransmissionTrace | None = None,
):
    """Copy of the production transmission loop with two independent switches.

    ``deterministic`` is accepted and ignored for signature compatibility with
    the production method; the two switches carry all the behaviour.
    """
    if (contact_mode, draw_mode) not in VALID_MODE_PAIRS:
        raise ValueError(
            f"unsupported A-18 mode pair {(contact_mode, draw_mode)}; "
            f"allowed: {sorted(VALID_MODE_PAIRS)}"
        )
    expected_path = draw_mode == "expected"
    if correction_form is not None:
        if not expected_path:
            raise ValueError(
                "the second-order correction only applies to the deterministic "
                "expected-value path"
            )
        if correction_form not in CORRECTION_FORMS:
            raise ValueError(f"unknown correction form {correction_form!r}")

    infections = np.zeros((
        self.groups.n_age_groups,
        self.groups.n_orientations,
        self.groups.n_activities,
        self.groups.n_prep
    ), dtype=np.int64)
    expected_infections = np.zeros_like(infections, dtype=float)

    total_new_infections = 0

    susc_idx = self.groups.hiv_to_idx["Susceptible"]
    acute_idx = self.groups.hiv_to_idx["Acute"]
    chronic_idx = self.groups.hiv_to_idx["Chronic"]
    aids_idx = self.groups.hiv_to_idx["AIDS"]

    for orient_to in range(self.groups.n_orientations):
        for act_to in range(self.groups.n_activities):

            partner_total = np.sum(population[:, orient_to, act_to, :, :, :, :])

            if partner_total == 0:
                continue

            stage_prevalence = {}
            for stage_name, stage_idx in [("Acute", acute_idx),
                                          ("Chronic", chronic_idx),
                                          ("AIDS", aids_idx)]:
                infected_off_art = np.sum(
                    population[:, orient_to, act_to, stage_idx, :, 0, :]
                )
                infected_on_art = np.sum(
                    population[:, orient_to, act_to, stage_idx, :, 1, :]
                )
                stage_prevalence[stage_name] = {
                    'off_art': infected_off_art / partner_total if partner_total > 0 else 0,
                    'on_art': infected_on_art / partner_total if partner_total > 0 else 0
                }

            for orient_from in range(self.groups.n_orientations):
                for act_from in range(self.groups.n_activities):

                    n_contacts = contacts[orient_from, act_from, orient_to, act_to]

                    if n_contacts == 0:
                        continue

                    activity_name_from = self.groups.activities[act_from]
                    condom_modifier = self.condom_modifiers.get(activity_name_from, 1.0)

                    for age_idx in range(self.groups.n_age_groups):
                        for prep_idx in range(self.groups.n_prep):

                            susceptible_pop = population[
                                age_idx, orient_from, act_from,
                                susc_idx, 0, 0, prep_idx
                            ]

                            if susceptible_pop == 0:
                                continue

                            prep_name = self.groups.prep_states[prep_idx]
                            prep_efficacy = self.prep_efficacy.get(prep_name, 0.0)

                            total_susceptible_in_group = np.sum(
                                population[:, orient_from, act_from, susc_idx, 0, 0, :]
                            )

                            if total_susceptible_in_group == 0:
                                continue

                            cell_proportion = susceptible_pop / total_susceptible_in_group
                            contacts_float = n_contacts * cell_proportion
                            cell_stratum = (
                                orient_to,
                                act_to,
                                orient_from,
                                act_from,
                                age_idx,
                                prep_idx,
                            )
                            if contact_mode == "expected":
                                n_contacts_cell = contacts_float
                            else:
                                n_contacts_cell = int(contacts_float)
                                frac = contacts_float - n_contacts_cell
                                contact_rng = self._rng_for_stratum(
                                    rng, (cell_stratum, "fractional_contact")
                                )
                                if frac > 0 and contact_rng.random() < frac:
                                    n_contacts_cell += 1

                            if n_contacts_cell == 0:
                                continue

                            cell_infections = 0
                            trace_lambda = 0.0

                            for stage_name in ["Acute", "Chronic", "AIDS"]:
                                if self.route_specific:
                                    beta_stage = self._select_beta(
                                        orient_from, orient_to, stage_name
                                    )
                                    if beta_stage == 0.0:
                                        continue
                                    is_msm_pair = (beta_stage is None)
                                else:
                                    beta_stage = self.beta[stage_name]
                                    is_msm_pair = False

                                prev_off = stage_prevalence[stage_name]['off_art']
                                if prev_off > 0:
                                    off_stratum = (cell_stratum, stage_name, "off_art")
                                    if expected_path:
                                        n_pos_contacts_off = n_contacts_cell * prev_off
                                    else:
                                        n_pos_contacts_off = self._rng_for_stratum(
                                            rng, (off_stratum, "positive_contacts")
                                        ).binomial(
                                            n_contacts_cell, prev_off
                                        )

                                    if n_pos_contacts_off > 0:
                                        if is_msm_pair:
                                            if expected_path:
                                                p_trans_off = self._calc_msm_expected_probability(
                                                    stage_name, condom_modifier,
                                                    prep_efficacy, 1.0
                                                )
                                                inf_off = n_pos_contacts_off * p_trans_off
                                            else:
                                                p_trans_off = self._calc_msm_expected_probability(
                                                    stage_name, condom_modifier,
                                                    prep_efficacy, 1.0
                                                )
                                                inf_off = self._calc_msm_infections(
                                                    n_pos_contacts_off, stage_name,
                                                    condom_modifier, prep_efficacy,
                                                    1.0, False, rng,
                                                    stratum_id=off_stratum,
                                                )
                                        else:
                                            p_trans_off = beta_stage * condom_modifier * (1 - prep_efficacy)
                                            p_trans_off = np.clip(p_trans_off, 0, 1)

                                            if expected_path:
                                                inf_off = n_pos_contacts_off * p_trans_off
                                            else:
                                                inf_off = self._rng_for_stratum(
                                                    rng, (off_stratum, "infection")
                                                ).binomial(
                                                    n_pos_contacts_off, p_trans_off
                                                )

                                        trace_lambda += float(prev_off) * float(p_trans_off)
                                        if expected_path:
                                            expected_infections[
                                                age_idx, orient_from, act_from, prep_idx
                                            ] += inf_off
                                        else:
                                            cell_infections += inf_off

                                prev_on = stage_prevalence[stage_name]['on_art']
                                if prev_on > 0:
                                    on_stratum = (cell_stratum, stage_name, "on_art")
                                    if expected_path:
                                        n_pos_contacts_on = n_contacts_cell * prev_on
                                    else:
                                        n_pos_contacts_on = self._rng_for_stratum(
                                            rng, (on_stratum, "positive_contacts")
                                        ).binomial(
                                            n_contacts_cell, prev_on
                                        )

                                    if n_pos_contacts_on > 0:
                                        art_modifier = 1 - self.art_transmission_reduction
                                        if is_msm_pair:
                                            if expected_path:
                                                p_trans_on = self._calc_msm_expected_probability(
                                                    stage_name, condom_modifier,
                                                    prep_efficacy, art_modifier
                                                )
                                                inf_on = n_pos_contacts_on * p_trans_on
                                            else:
                                                p_trans_on = self._calc_msm_expected_probability(
                                                    stage_name, condom_modifier,
                                                    prep_efficacy, art_modifier
                                                )
                                                inf_on = self._calc_msm_infections(
                                                    n_pos_contacts_on, stage_name,
                                                    condom_modifier, prep_efficacy,
                                                    art_modifier, False, rng,
                                                    stratum_id=on_stratum,
                                                )
                                        else:
                                            p_trans_on = beta_stage * condom_modifier * (1 - prep_efficacy) * art_modifier
                                            p_trans_on = np.clip(p_trans_on, 0, 1)

                                            if expected_path:
                                                inf_on = n_pos_contacts_on * p_trans_on
                                            else:
                                                inf_on = self._rng_for_stratum(
                                                    rng, (on_stratum, "infection")
                                                ).binomial(
                                                    n_pos_contacts_on, p_trans_on
                                                )

                                        trace_lambda += float(prev_on) * float(p_trans_on)
                                        if expected_path:
                                            expected_infections[
                                                age_idx, orient_from, act_from, prep_idx
                                            ] += inf_on
                                        else:
                                            cell_infections += inf_on

                            correction = 0.0
                            if correction_form is not None and trace_lambda > 0.0:
                                correction = second_order_correction(
                                    contacts_float,
                                    trace_lambda,
                                    float(susceptible_pop),
                                    form=correction_form,
                                )
                                expected_infections[
                                    age_idx, orient_from, act_from, prep_idx
                                ] += correction
                                expected_infections[
                                    age_idx, orient_from, act_from, prep_idx
                                ] = max(
                                    0.0,
                                    expected_infections[
                                        age_idx, orient_from, act_from, prep_idx
                                    ],
                                )

                            if trace is not None and trace.active:
                                trace.record(
                                    second_order_correction=float(correction),
                                    orient_to=orient_to,
                                    act_to=act_to,
                                    orient_from=orient_from,
                                    act_from=act_from,
                                    age_idx=age_idx,
                                    prep_idx=prep_idx,
                                    susceptible_pop=float(susceptible_pop),
                                    contacts_float=float(contacts_float),
                                    n_contacts_cell=float(n_contacts_cell),
                                    lambda_per_contact=float(trace_lambda),
                                )

                            if expected_path:
                                continue

                            cell_infections = min(cell_infections, susceptible_pop)

                            if cell_infections > 0:
                                infections[age_idx, orient_from, act_from, prep_idx] += cell_infections
                                total_new_infections += cell_infections

    if expected_path:
        capacity = population[:, :, :, susc_idx, 0, 0, :]
        infections = self._deterministic_allocate_expected_infections(
            expected_infections,
            capacity,
        )
        total_new_infections = int(np.sum(infections))
        return total_new_infections, infections

    for age_idx in range(self.groups.n_age_groups):
        for orient_idx in range(self.groups.n_orientations):
            for act_idx in range(self.groups.n_activities):
                for prep_idx in range(self.groups.n_prep):
                    available = population[
                        age_idx, orient_idx, act_idx, susc_idx, 0, 0, prep_idx
                    ]
                    if infections[age_idx, orient_idx, act_idx, prep_idx] > available:
                        infections[age_idx, orient_idx, act_idx, prep_idx] = available

    total_new_infections = int(np.sum(infections))
    return total_new_infections, infections


def contact_rounding_variance(contacts_float: float) -> float:
    """Var(n) under the model's OWN contact integerisation.

    Production stochastic mode forms the integer contact count as
    ``floor(n) + Bernoulli(frac(n))``, whose variance is ``frac (1 - frac)``.
    This is the only contact-count variance the model supplies.  It is a
    property of the rounding step, NOT an epidemiological contact-heterogeneity
    parameter: the model carries no dispersion parameter for contacts, and
    ``_step_contacts_and_mixing`` produces one deterministic float per
    orientation/activity pair in both run modes.  See the A-18 report, remedy C.
    """
    frac = contacts_float - np.floor(contacts_float)
    return float(frac * (1.0 - frac))


def second_order_curvature(contacts_float: float, lam: float, susceptibles: float,
                           *, form: str) -> float:
    """d^2/dn^2 of the per-cell expected-infection function, at n = E[n]."""
    if form == "model":
        # d^2/dn^2 (n * lambda) = 0, exactly, for every n and every lambda.
        return 0.0
    if form == "hypothesised":
        if susceptibles <= 0.0 or lam <= 0.0 or lam >= 1.0:
            return 0.0
        m = contacts_float / susceptibles
        return float(
            -(1.0 / susceptibles)
            * np.power(1.0 - lam, m)
            * (np.log1p(-lam) ** 2)
        )
    raise ValueError(f"unknown correction form {form!r}")


def second_order_correction(contacts_float: float, lam: float, susceptibles: float,
                            *, form: str) -> float:
    """The half f''(mu) sigma^2 term of A-18 remedy C, per cell and partner pass."""
    curvature = second_order_curvature(
        contacts_float, lam, susceptibles, form=form
    )
    if curvature == 0.0:
        return 0.0
    return 0.5 * curvature * contact_rounding_variance(contacts_float)


# ── opt-in diagnostic variants ────────────────────────────────────────────

#: Variant name -> (contact_mode, draw_mode, uses_production_method)
VARIANTS = {
    # Control: the parameterised copy in its deterministic corner.  Must
    # reproduce production deterministic byte for byte.
    "copy_deterministic": ("expected", "expected", False),
    # Control: the parameterised copy in its stochastic corner.  Must
    # reproduce production stochastic byte for byte.
    "copy_stochastic": ("drawn", "binomial", False),
    # A-18 Step 1 hybrid A -- the brief's literal isolation test.  Contact
    # counts drawn; prevalence and transmission still taken in expectation;
    # every other process deterministic.
    "hybrid_contacts": ("drawn", "expected", False),
    # A-18 Step 1 hybrid B -- the generous variant.  The whole production
    # stochastic transmission path; every other process deterministic.
    "hybrid_transmission": (None, None, True),
}

#: Remedy-C probe variants: deterministic everywhere, with the second-order
#: term added to the deterministic force of infection.
CORRECTION_VARIANTS = {
    "jensen_corrected_model": "model",
    "jensen_corrected_hypothesised": "hypothesised",
}


class HybridTransmission(AbstractContextManager):
    """Route an otherwise deterministic engine through one A-18 variant.

    Production bindings are restored on exit.  The engine's own
    ``deterministic`` flag is left untouched, so progression, diagnosis, ART,
    mortality, demography and every allocation step keep running exactly as
    they do in a production deterministic run.
    """

    def __init__(self, variant: str, *, trace: TransmissionTrace | None = None) -> None:
        if variant not in VARIANTS and variant not in CORRECTION_VARIANTS:
            raise ValueError(f"unknown A-18 variant {variant!r}")
        self.variant = variant
        self.trace = trace

    def __enter__(self):
        from run_v3 import SimulationEngineV3
        from transmission_corrected import TransmissionEngine

        self._engine_cls = SimulationEngineV3
        self._transmission_cls = TransmissionEngine
        self._original_calculate = TransmissionEngine.calculate_transmission
        self._original_process_rng = SimulationEngineV3._process_rng
        self._original_step = SimulationEngineV3.step_simulation

        if self.variant in CORRECTION_VARIANTS:
            contact_mode, draw_mode, use_production = "expected", "expected", False
            correction_form = CORRECTION_VARIANTS[self.variant]
        else:
            contact_mode, draw_mode, use_production = VARIANTS[self.variant]
            correction_form = None
        trace = self.trace
        original_calculate = self._original_calculate
        original_process_rng = self._original_process_rng
        original_step = self._original_step

        def patched_step(engine, step):
            if trace is not None:
                trace.step = engine._current_step
                trace.year = engine._current_year
            return original_step(engine, step)

        def patched_process_rng(engine, process_id):
            """Hand transmission a counter-based stream even in deterministic mode."""
            if process_id != "transmission" or not engine.deterministic:
                return original_process_rng(engine, process_id)
            if engine._current_step is None or engine._current_year is None:
                raise RuntimeError(
                    "A-18 transmission stream requested outside a simulation timestep"
                )
            return engine._crn_factory.for_process(
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=process_id,
            )

        if use_production:
            def patched_calculate(engine_self, population, contacts, rng,
                                  deterministic=False):
                # Production code, forced onto its stochastic branch.
                return original_calculate(
                    engine_self, population, contacts, rng, False
                )
        else:
            def patched_calculate(engine_self, population, contacts, rng,
                                  deterministic=False):
                return parameterised_calculate_transmission(
                    engine_self, population, contacts, rng, deterministic,
                    contact_mode=contact_mode,
                    draw_mode=draw_mode,
                    correction_form=correction_form,
                    trace=trace,
                )

        TransmissionEngine.calculate_transmission = patched_calculate
        SimulationEngineV3._process_rng = patched_process_rng
        SimulationEngineV3.step_simulation = patched_step
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._transmission_cls.calculate_transmission = self._original_calculate
        self._engine_cls._process_rng = self._original_process_rng
        self._engine_cls.step_simulation = self._original_step
        return False


# ── the direct f(E[n]) - E[f(n)] arithmetic ───────────────────────────────

def model_form_bias(contacts: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """Jensen bias of the functional form the model ACTUALLY uses.

    The model's per-cell expected infections are

        g(n) = n * sum_stage sum_art  prevalence * p_transmission
             = n * lambda

    which is affine in ``n``.  Its second derivative is identically zero, so
    ``g(E[n]) - E[g(n)] = 0`` for ANY contact-count distribution.  Returned
    explicitly rather than asserted, so the runner can evidence it.
    """
    contacts = np.asarray(contacts, dtype=float)
    lam = np.asarray(lam, dtype=float)
    return np.zeros_like(contacts * lam)


def hypothesised_form_bias(
    contacts: np.ndarray, lam: np.ndarray, susceptibles: np.ndarray
) -> pd.DataFrame:
    """Jensen bias of the CONCAVE per-person form the A-18 brief hypothesises.

    The brief assumes a per-person infection probability

        f(m) = 1 - (1 - lambda)^m

    over ``m`` contacts per susceptible person, whose expected infections in a
    cell of ``S`` susceptibles are ``S * f(m)``.  The model instead uses the
    linearised ``S * m * lambda = n * lambda``.  The difference between the two
    is the bias the hypothesis predicts, evaluated here on the model's own
    contact counts and transmission probabilities.
    """
    contacts = np.asarray(contacts, dtype=float)
    lam = np.asarray(lam, dtype=float)
    susceptibles = np.asarray(susceptibles, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(susceptibles > 0.0, contacts / susceptibles, 0.0)
    linear = contacts * lam
    concave = susceptibles * (1.0 - np.power(1.0 - lam, m))
    with np.errstate(divide="ignore", invalid="ignore"):
        relative = np.where(concave > 0.0, linear / concave - 1.0, np.nan)
    return pd.DataFrame(
        {
            "contacts": contacts,
            "lambda_per_contact": lam,
            "susceptibles": susceptibles,
            "contacts_per_susceptible": m,
            "linear_expected_infections": linear,
            "concave_expected_infections": concave,
            "absolute_bias": linear - concave,
            "relative_bias": relative,
        }
    )


def monte_carlo_model_form_bias(
    contacts_float: float,
    lam: float,
    *,
    draws: int = 200_000,
    seed: int = 2026_09_12,
) -> dict:
    """Empirical ``g(E[n]) - E[g(n)]`` under the model's own contact rounding.

    ``n`` is integerised exactly as production stochastic mode does it:
    ``floor(n) + Bernoulli(frac(n))``.  The expectation is taken over that
    rounding.  Used to confirm the analytic zero numerically rather than
    asserting it.
    """
    rng = np.random.default_rng(seed)
    base = int(np.floor(contacts_float))
    frac = contacts_float - base
    drawn = base + (rng.random(draws) < frac)
    realised = drawn * lam
    mean = float(realised.mean())
    se = float(realised.std(ddof=1) / np.sqrt(draws))
    deterministic = float(contacts_float * lam)
    return {
        "contacts_float": float(contacts_float),
        "lambda_per_contact": float(lam),
        "draws": int(draws),
        "deterministic_g_of_expected_n": deterministic,
        "monte_carlo_expected_g_of_n": mean,
        "monte_carlo_se": se,
        "bias": deterministic - mean,
        "abs_z": abs(deterministic - mean) / se if se > 0 else 0.0,
    }


def per_step_bias_summary(trace: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a transmission trace to one row per timestep."""
    detail = hypothesised_form_bias(
        trace["contacts_float"].to_numpy(),
        trace["lambda_per_contact"].to_numpy(),
        trace["susceptible_pop"].to_numpy(),
    )
    detail["year"] = trace["year"].to_numpy()
    detail["step"] = trace["step"].to_numpy()
    grouped = detail.groupby(["step", "year"], sort=True).agg(
        n_strata=("contacts", "size"),
        total_contacts=("contacts", "sum"),
        linear_expected_infections=("linear_expected_infections", "sum"),
        concave_expected_infections=("concave_expected_infections", "sum"),
        max_contacts_per_susceptible=("contacts_per_susceptible", "max"),
        median_contacts_per_susceptible=("contacts_per_susceptible", "median"),
        max_lambda=("lambda_per_contact", "max"),
    ).reset_index()
    with np.errstate(divide="ignore", invalid="ignore"):
        grouped["model_form_relative_bias_per_step"] = 0.0
        grouped["hypothesised_form_relative_bias_per_step"] = np.where(
            grouped["concave_expected_infections"] > 0.0,
            grouped["linear_expected_infections"]
            / grouped["concave_expected_infections"]
            - 1.0,
            np.nan,
        )
    return grouped
