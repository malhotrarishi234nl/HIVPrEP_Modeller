import numpy as np
import pandas as pd
import pytest

from tools.a17_residual_placement import (
    DiagnosticStochasticRounding,
    compare_annual_levels,
    counter_based_stochastic_round,
    fully_cellwise_round_expected,
    largest_remainder_expected,
    rotating_systematic_expected,
    stochastic_round_expected,
    step1_falsifies_compounding,
    timing_summary,
)
from stochastic_allocation import deterministic_allocate_events


def _tables(det_values, stochastic_values):
    deterministic = pd.DataFrame(
        {
            "metric": ["PLHIV"] * len(det_values),
            "year": [1975 + i for i in range(len(det_values))],
            "value": det_values,
        }
    )
    rows = []
    for seed, values in enumerate(stochastic_values, start=1):
        for index, value in enumerate(values):
            rows.append(
                {"seed": seed, "metric": "PLHIV", "year": 1975 + index, "value": value}
            )
    return deterministic, pd.DataFrame(rows)


def test_comparison_computes_log_ratio_and_range_membership():
    deterministic, stochastic = _tables([12.0, 20.0], [[10.0, 18.0], [14.0, 22.0]])
    result = compare_annual_levels(deterministic, stochastic)
    assert result["det_inside_stochastic_range"].tolist() == [True, True]
    np.testing.assert_allclose(result["log_ratio_det_over_stoch_mean"], [0.0, 0.0])
    assert result["n_replicates"].tolist() == [2, 2]


def test_log_ratio_is_nan_when_mean_or_deterministic_is_zero():
    deterministic, stochastic = _tables([0.0, 1.0], [[0.0, 0.0], [0.0, 0.0]])
    result = compare_annual_levels(deterministic, stochastic)
    assert result["log_ratio_det_over_stoch_mean"].isna().all()


def test_stop_rule_fires_in_seed_or_first_full_year():
    deterministic, stochastic = _tables([15.0, 20.0], [[10.0, 19.0], [12.0, 21.0]])
    result = compare_annual_levels(deterministic, stochastic)
    assert step1_falsifies_compounding(result)


def test_stop_rule_does_not_use_later_years():
    deterministic, stochastic = _tables(
        [11.0, 20.0, 40.0], [[10.0, 19.0, 25.0], [12.0, 21.0, 30.0]]
    )
    result = compare_annual_levels(deterministic, stochastic)
    assert not step1_falsifies_compounding(result)
    summary = timing_summary(result)
    assert summary.loc[0, "first_year_outside_stochastic_range"] == 1977


def test_comparison_refuses_mismatched_metric_year_sets():
    deterministic, stochastic = _tables([10.0, 20.0], [[10.0], [11.0]])
    with pytest.raises(AssertionError, match="sets differ"):
        compare_annual_levels(deterministic, stochastic)


def test_generalised_largest_remainder_matches_production_for_scalar_probability():
    counts = np.array([1, 7, 13, 101, 999])
    expected = counts * 0.037
    np.testing.assert_array_equal(
        largest_remainder_expected(expected, counts),
        deterministic_allocate_events(counts, 0.037),
    )


def test_stochastic_rounding_uses_fraction_as_cellwise_cutoff():
    expected = np.array([0.1, 1.5, 2.9])
    uniforms = np.array([0.05, 0.6, 0.89])
    np.testing.assert_array_equal(
        stochastic_round_expected(expected, uniforms), np.array([1, 1, 3])
    )


def test_counter_based_candidate_is_fixed_key_reproducible():
    counts = np.arange(1, 101)
    kwargs = dict(seed=7001, year=1984.5, timestep=38, process_id="test")
    first = counter_based_stochastic_round(counts, 0.013, **kwargs)
    second = counter_based_stochastic_round(counts, 0.013, **kwargs)
    np.testing.assert_array_equal(first, second)
    assert 0 < first.sum() < counts.sum()


def test_rotating_rule_preserves_rounded_aggregate():
    expected = np.linspace(0.01, 0.99, 100)
    for step in (0, 1, 99, 100):
        result = rotating_systematic_expected(expected, step=step)
        assert result.sum() == round(expected.sum())


def test_cellwise_control_reproduces_eng01_rare_event_erasure():
    expected = np.full(10_000, 1e-4)
    assert expected.sum() == pytest.approx(1.0)
    assert fully_cellwise_round_expected(expected).sum() == 0


def test_diagnostic_context_restores_all_production_bindings():
    import demography
    from run_v3 import SimulationEngineV3
    from transmission_corrected import TransmissionEngine

    before = (
        SimulationEngineV3.step_simulation,
        SimulationEngineV3._draw_events_for,
        demography.deterministic_allocate_events,
        TransmissionEngine._deterministic_allocate_expected_infections,
    )
    with DiagnosticStochasticRounding():
        assert SimulationEngineV3._draw_events_for is not before[1]
    after = (
        SimulationEngineV3.step_simulation,
        SimulationEngineV3._draw_events_for,
        demography.deterministic_allocate_events,
        TransmissionEngine._deterministic_allocate_expected_infections,
    )
    assert after == before
