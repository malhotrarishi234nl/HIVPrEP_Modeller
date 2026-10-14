"""Tests for A-18's Jensen-mechanism diagnostic.

The load-bearing tests here are the two equivalence tests.  A-18's Step 1
conclusion rests on a parameterised COPY of the production transmission loop,
so the copy has to be proven to reproduce production exactly in both of its
pure corners before any hybrid number means anything.  See handoff section
1A.2: "refactoring a computation requires a proof of equivalence".
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from calib_extractor import extract_annual_outputs
from run_v3 import SimulationEngineV3
from transmission_corrected import TransmissionEngine
from tools.a18_jensen_mechanism import (
    VALID_MODE_PAIRS,
    HybridTransmission,
    TransmissionTrace,
    hypothesised_form_bias,
    model_form_bias,
    monte_carlo_model_form_bias,
    parameterised_calculate_transmission,
    per_step_bias_summary,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL_CONFIG = ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json"

#: Equivalence is asserted over a short window so the test suite stays fast.
#: The runner re-asserts it over the full 1975-2025 window before reading any
#: hybrid result.
TEST_END_YEAR = 1990


def _run(*, deterministic: bool, seed: int, variant: str | None, trace=None) -> dict:
    config = json.loads(MODEL_CONFIG.read_text(encoding="utf-8"))
    config["run_control"]["deterministic_mode"] = {"value": deterministic}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    config["run_control"]["seed"] = {"value": seed}
    config["run_control"]["end_year"]["value"] = TEST_END_YEAR
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    context = (
        HybridTransmission(variant, trace=trace)
        if variant is not None
        else contextlib.nullcontext()
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with context:
            result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return extract_annual_outputs(result, start_year=1975, end_year=TEST_END_YEAR - 1)


def _assert_identical(left: dict, right: dict, label: str) -> None:
    assert set(left) == set(right), label
    for metric in sorted(left):
        assert set(left[metric]) == set(right[metric]), f"{label}: {metric} years"
        for year in sorted(left[metric]):
            assert float(left[metric][year]) == float(right[metric][year]), (
                f"{label}: {metric} {year} "
                f"{left[metric][year]} != {right[metric][year]}"
            )


# ── equivalence of the parameterised copy ────────────────────────────────

@pytest.mark.slow
def test_copy_reproduces_production_deterministic_exactly():
    production = _run(deterministic=True, seed=7001, variant=None)
    copy = _run(deterministic=True, seed=7001, variant="copy_deterministic")
    _assert_identical(production, copy, "deterministic corner")


@pytest.mark.slow
def test_copy_reproduces_production_stochastic_exactly():
    production = _run(deterministic=False, seed=7001, variant=None)
    copy = _run(deterministic=False, seed=7001, variant="copy_stochastic")
    _assert_identical(production, copy, "stochastic corner")


def test_unsupported_mode_pair_is_refused():
    assert ("expected", "binomial") not in VALID_MODE_PAIRS
    with pytest.raises(ValueError, match="unsupported A-18 mode pair"):
        parameterised_calculate_transmission(
            object(), np.zeros(1), np.zeros(1), None, True,
            contact_mode="expected", draw_mode="binomial",
        )


# ── the hybrid actually changes what it claims to change ─────────────────

@pytest.mark.slow
def test_hybrid_contacts_draws_integer_contact_counts():
    """The knob control: a null result is only informative if the knob turned."""
    trace = TransmissionTrace(trace_years=frozenset({1985.0}))
    _run(deterministic=True, seed=7001, variant="hybrid_contacts", trace=trace)
    frame = trace.frame()
    assert len(frame) > 0
    drawn = frame["n_contacts_cell"].to_numpy()
    expected = frame["contacts_float"].to_numpy()
    assert np.all(drawn == np.floor(drawn)), "hybrid contact counts must be integers"
    assert np.any(drawn != expected), "hybrid must move at least one count off its expectation"
    assert np.max(np.abs(drawn - expected)) < 1.0, "rounding may never move a count by a whole contact"


@pytest.mark.slow
def test_deterministic_control_keeps_fractional_contact_counts():
    trace = TransmissionTrace(trace_years=frozenset({1985.0}))
    _run(deterministic=True, seed=7001, variant="copy_deterministic", trace=trace)
    frame = trace.frame()
    assert len(frame) > 0
    np.testing.assert_array_equal(
        frame["n_contacts_cell"].to_numpy(), frame["contacts_float"].to_numpy()
    )


# ── the context manager leaves no residue ────────────────────────────────

def test_context_manager_restores_production_bindings():
    original_calculate = TransmissionEngine.calculate_transmission
    original_process_rng = SimulationEngineV3._process_rng
    original_step = SimulationEngineV3.step_simulation
    with HybridTransmission("hybrid_contacts"):
        assert TransmissionEngine.calculate_transmission is not original_calculate
    assert TransmissionEngine.calculate_transmission is original_calculate
    assert SimulationEngineV3._process_rng is original_process_rng
    assert SimulationEngineV3.step_simulation is original_step


def test_context_manager_restores_bindings_after_exception():
    original_calculate = TransmissionEngine.calculate_transmission
    with pytest.raises(RuntimeError):
        with HybridTransmission("hybrid_transmission"):
            raise RuntimeError("boom")
    assert TransmissionEngine.calculate_transmission is original_calculate


def test_unknown_variant_is_refused():
    with pytest.raises(ValueError, match="unknown A-18 variant"):
        HybridTransmission("not_a_variant")


# ── the Jensen arithmetic ────────────────────────────────────────────────

def test_model_functional_form_has_exactly_zero_jensen_bias():
    contacts = np.array([0.0, 1.5, 1e5])
    lam = np.array([0.0, 1e-6, 0.3])
    np.testing.assert_array_equal(model_form_bias(contacts, lam), np.zeros(3))


def test_hypothesised_concave_form_bias_on_exact_fixture():
    """lambda = 0.1, two contacts per susceptible, one susceptible.

    linear  = n * lambda           = 2 * 0.1  = 0.2
    concave = S * (1 - (1-0.1)^2)  = 1 - 0.81 = 0.19
    """
    frame = hypothesised_form_bias(
        np.array([2.0]), np.array([0.1]), np.array([1.0])
    )
    assert frame["linear_expected_infections"].iloc[0] == pytest.approx(0.2)
    assert frame["concave_expected_infections"].iloc[0] == pytest.approx(0.19)
    assert frame["absolute_bias"].iloc[0] == pytest.approx(0.01)
    assert frame["relative_bias"].iloc[0] == pytest.approx(0.2 / 0.19 - 1.0)


def test_concave_bias_vanishes_as_lambda_times_m_goes_to_zero():
    """The bias is second order: halving lambda quarters the relative bias."""
    big = hypothesised_form_bias(np.array([100.0]), np.array([1e-3]), np.array([1.0]))
    small = hypothesised_form_bias(np.array([100.0]), np.array([5e-4]), np.array([1.0]))
    ratio = big["relative_bias"].iloc[0] / small["relative_bias"].iloc[0]
    assert ratio == pytest.approx(2.0, rel=1e-2)


def test_monte_carlo_confirms_the_model_form_zero():
    result = monte_carlo_model_form_bias(1234.6, 3e-5, draws=200_000, seed=11)
    assert result["abs_z"] < 4.0
    assert abs(result["bias"]) < 5.0 * result["monte_carlo_se"]


def test_per_step_summary_aggregates_one_row_per_step():
    trace = pd.DataFrame(
        {
            "step": [1, 1, 2],
            "year": [1980.0, 1980.0, 1980.25],
            "contacts_float": [10.0, 20.0, 30.0],
            "lambda_per_contact": [1e-3, 1e-3, 1e-3],
            "susceptible_pop": [5.0, 5.0, 5.0],
        }
    )
    summary = per_step_bias_summary(trace)
    assert len(summary) == 2
    assert summary["n_strata"].tolist() == [2, 1]
    assert (summary["model_form_relative_bias_per_step"] == 0.0).all()
    assert (summary["hypothesised_form_relative_bias_per_step"] > 0).all()


# ── remedy C: the second-order correction ────────────────────────────────

def test_model_form_curvature_is_exactly_zero_everywhere():
    from tools.a18_jensen_mechanism import second_order_curvature
    for n, lam, s in [(1.0, 0.5, 1.0), (1e6, 1e-9, 1e5), (0.3, 0.999, 2.0)]:
        assert second_order_curvature(n, lam, s, form="model") == 0.0


def test_hypothesised_form_curvature_is_negative_and_matches_closed_form():
    from tools.a18_jensen_mechanism import second_order_curvature
    n, lam, s = 10.0, 0.1, 2.0
    m = n / s
    expected = -(1.0 / s) * (1.0 - lam) ** m * (np.log(1.0 - lam) ** 2)
    assert second_order_curvature(n, lam, s, form="hypothesised") == pytest.approx(expected)
    assert expected < 0.0


def test_contact_rounding_variance_is_the_bernoulli_variance():
    from tools.a18_jensen_mechanism import contact_rounding_variance
    assert contact_rounding_variance(10.0) == pytest.approx(0.0)
    assert contact_rounding_variance(10.5) == pytest.approx(0.25)
    assert contact_rounding_variance(10.25) == pytest.approx(0.1875)


def test_model_form_correction_is_an_exact_no_op():
    from tools.a18_jensen_mechanism import second_order_correction
    assert second_order_correction(1234.5, 3e-5, 500.0, form="model") == 0.0


def test_correction_is_refused_on_the_stochastic_path():
    with pytest.raises(ValueError, match="only applies to the deterministic"):
        parameterised_calculate_transmission(
            object(), np.zeros(1), np.zeros(1), None, False,
            contact_mode="drawn", draw_mode="binomial", correction_form="model",
        )


@pytest.mark.slow
def test_model_form_corrected_run_reproduces_production_deterministic():
    """Remedy C with the model's own curvature must change nothing at all."""
    production = _run(deterministic=True, seed=7001, variant=None)
    corrected = _run(deterministic=True, seed=7001, variant="jensen_corrected_model")
    _assert_identical(production, corrected, "remedy C, model curvature")
