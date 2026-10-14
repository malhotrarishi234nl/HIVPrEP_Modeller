import json

import numpy as np
import pandas as pd
import pytest

from tools.run_a5a_epidemiological_seed_variability import (
    METRICS,
    metric_summary,
    parse_seeds,
    target_years_from_register,
    validate_tables,
    variance_rows,
)


def test_parse_seeds_enforces_specified_replicate_range():
    assert parse_seeds("7001-7040") == list(range(7001, 7041))
    assert len(parse_seeds("7001-7050")) == 50
    with pytest.raises(Exception, match="40--50"):
        parse_seeds("1-39")
    with pytest.raises(Exception, match="40--50"):
        parse_seeds("1-51")
    with pytest.raises(Exception, match="duplicates"):
        parse_seeds(",".join([str(i) for i in range(1, 40)] + ["39"]))


def test_target_years_uses_restricted_ranges_and_unions_outputs():
    register = {
        "targets": [
            {"model_output": metric, "role": "calibration", "year_coverage": [2000, 2001]}
            for metric in METRICS
        ] + [
            {"model_output": "new_diagnoses", "role": "calibration_restricted",
             "year_coverage": [1990, 2010], "restricted_years": [1998, 1999]},
            {"model_output": "PLHIV", "role": "excluded", "year_coverage": [1900, 1901]},
        ]
    }
    years = target_years_from_register(register)
    assert years["new_diagnoses"] == [1998, 1999, 2000, 2001]
    assert years["PLHIV"] == [2000, 2001]


def test_variance_rows_are_unbiased_and_ci_contains_estimate():
    frame = pd.DataFrame(
        [
            {"scenario": "s", "seed": seed, "metric": "PLHIV", "year": 2000,
             "value": value}
            for seed, value in enumerate([1.0, 2.0, 3.0, 4.0], start=1)
        ]
    )
    row = variance_rows(frame).iloc[0]
    assert row["sample_variance_v_ensemble"] == pytest.approx(5.0 / 3.0)
    assert row["sample_sd"] == pytest.approx(np.sqrt(5.0 / 3.0))
    assert row["variance_ci95_normal_theory_low"] < row["sample_variance_v_ensemble"]
    assert row["variance_ci95_normal_theory_high"] > row["sample_variance_v_ensemble"]


def test_zero_variance_has_zero_interval():
    frame = pd.DataFrame(
        [{"scenario": "s", "seed": seed, "metric": "PLHIV", "year": 2000,
          "value": 7.0} for seed in range(40)]
    )
    row = variance_rows(frame).iloc[0]
    assert row["sample_variance_v_ensemble"] == 0.0
    assert row["variance_ci95_normal_theory_low"] == 0.0
    assert row["variance_ci95_normal_theory_high"] == 0.0


def test_summary_and_validation_cover_all_metrics_and_years():
    seeds = list(range(40))
    target_years = {metric: [2000, 2001] for metric in METRICS}
    rows = []
    for seed in seeds:
        for metric_index, metric in enumerate(METRICS):
            for year in target_years[metric]:
                rows.append({"scenario": "s", "seed": seed, "metric": metric,
                             "year": year, "value": seed + metric_index + year})
    replicates = pd.DataFrame(rows)
    yearly = variance_rows(replicates)
    summary = metric_summary(yearly)
    validate_tables(replicates, yearly, summary, seeds, target_years)
    assert len(yearly) == 2 * len(METRICS)
    assert len(summary) == len(METRICS)
    assert (summary["mean_of_target_year_sample_variances"] ==
            summary["p95_of_target_year_sample_variances"]).all()


def test_real_register_resolves_expected_92_tier1_and_222_a5a_targets():
    register = json.loads(open("configs/hm_target_register.json", encoding="utf-8").read())
    years = target_years_from_register(register)
    assert sum(map(len, years.values())) == 222
    assert len(years["new_diagnoses"]) == 35
    assert len(years["new_diagnoses_msm"]) == 29
    assert len(years["new_aids_cases"]) == 28
    assert 35 + 29 + 28 == 92
