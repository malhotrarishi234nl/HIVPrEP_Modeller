import math

import pandas as pd
import pytest

from scenario_comparator import ScenarioComparator, ScenarioResult
from tools.run_a16_stochastic_headline import (
    BASE_SCENARIO,
    SCENARIOS,
    classified_price_roots,
    pair_arms,
    price_root,
    quadrant,
    ratio_of_means,
    select_scenarios,
    summarize,
)


def test_select_scenarios_is_exact_and_ordered():
    expanded = [
        {"name": "irrelevant", "arm_order": ["x", "y"]},
        *[
            {
                "name": name,
                "arm_order": list(pair_arms(name)),
            }
            for name in reversed(SCENARIOS)
        ],
    ]
    assert [row["name"] for row in select_scenarios(expanded)] == list(SCENARIOS)
    assert pair_arms(BASE_SCENARIO) == ("no_prep", "oral_prep")


def test_price_root_solves_nmb_equation_exactly():
    root = price_root(
        delta_qaly=100.0,
        reference_cost=40_000_000.0,
        intervention_cost=70_000_000.0,
        lena_product_cost=20_000_000.0,
        base_annual_price=40_000.0,
        wtp=80_000.0,
    )
    # non-product delta = 10m; WTP benefit = 8m, so the algebraic root is
    # negative and therefore no non-negative price can make NMB positive.
    assert root is None


def test_price_root_matches_production_comparator_for_positive_qalys():
    ref = ScenarioResult(
        name="oral_prep",
        analysis_scenario="test",
        total_qalys_disc=1_000,
        total_qalys_undisc=1_000,
        total_costs_disc=40_000_000,
        total_costs_undisc=40_000_000,
        cost_breakdown_disc={},
        total_costs_disc_societal=42_000_000,
    )
    intervention = ScenarioResult(
        name="oral_prep_plus_lena",
        analysis_scenario="test",
        total_qalys_disc=1_200,
        total_qalys_undisc=1_200,
        total_costs_disc=70_000_000,
        total_costs_undisc=70_000_000,
        cost_breakdown_disc={"prep_drug_lenacapavir": 20_000_000},
        total_costs_disc_societal=73_000_000,
    )
    comparator = ScenarioComparator(80_000, 0.25, perspective="societal")
    api = comparator.find_threshold_price(
        {"oral_prep": ref, "oral_prep_plus_lena": intervention},
        wtp=80_000,
        base_annual_price=40_000,
        comparator_arm="oral_prep",
        intervention_arm="oral_prep_plus_lena",
    )
    analytic = price_root(
        delta_qaly=200,
        reference_cost=42_000_000,
        intervention_cost=73_000_000,
        lena_product_cost=20_000_000,
        base_annual_price=40_000,
        wtp=80_000,
    )
    assert analytic == pytest.approx(10_000.0)
    assert api == pytest.approx(analytic, abs=1.0)


def test_price_names_are_mutually_exclusive_by_qaly_sign():
    common = dict(
        reference_cost=40_000_000,
        intervention_cost=55_000_000,
        lena_product_cost=20_000_000,
        base_annual_price=40_000,
        wtp=80_000,
    )
    positive = classified_price_roots(delta_qaly=200, **common)
    negative = classified_price_roots(delta_qaly=-20, **common)
    zero = classified_price_roots(delta_qaly=0, **common)
    assert positive["ce_threshold_price"] is not None
    assert positive["qaly_loss_compensation_price"] is None
    assert negative["ce_threshold_price"] is None
    assert negative["qaly_loss_compensation_price"] is not None
    assert zero["ce_threshold_price"] is None
    assert zero["qaly_loss_compensation_price"] is None


@pytest.mark.parametrize(
    ("cost", "qaly", "expected"),
    [
        (1, 1, "NE_more_qaly_more_cost"),
        (-1, 1, "SE_more_qaly_lower_cost"),
        (1, -1, "NW_less_qaly_more_cost"),
        (-1, -1, "SW_less_qaly_lower_cost"),
    ],
)
def test_quadrants(cost, qaly, expected):
    assert quadrant(cost, qaly) == expected


def test_summary_uses_sample_sd_t_interval_and_cv():
    result = summarize([1.0, 2.0, 3.0, math.nan])
    assert result["n"] == 3
    assert result["mean"] == pytest.approx(2.0)
    assert result["sd"] == pytest.approx(1.0)
    assert result["cv"] == pytest.approx(0.5)
    assert result["ci95_low"] < 0 < result["ci95_high"]


def test_ratio_of_means_never_averages_seed_level_ratios():
    # Mean of ratios = (1/1 + 9/3)/2 = 2, while ratio of means = 10/4 = 2.5.
    result = ratio_of_means([1.0, 9.0], [1.0, 3.0])
    # With only two observations the t interval crosses zero, so the ratio is
    # deliberately suppressed despite its arithmetic value being 2.5.
    assert result["ratio_of_means"] is None
    assert result["status"] == "undefined_qaly_mean_ci_crosses_zero"

    stable = ratio_of_means([100.0] * 15, [2.0] * 15)
    assert stable["ratio_of_means"] == pytest.approx(50.0)
    assert stable["ratio_of_means"] != pytest.approx((1.0 / 1.0 + 9.0 / 3.0) / 2)


def test_summarize_all_missing_is_explicitly_undefined():
    result = summarize([math.nan, math.nan])
    assert result["n"] == 0
    assert pd.isna(result["mean"])
    assert pd.isna(result["cv"])


def test_csv_scale_qaly_roundoff_is_not_a_scientific_reproduction_failure():
    # The retained reference CSV can differ from an in-memory subtraction by
    # one or two final decimal bits after decimal text round-tripping.
    observed = 121.98532932202031
    retained = 121.9853293220202
    assert observed != retained
    assert math.isclose(observed, retained, rel_tol=0.0, abs_tol=1e-9)
