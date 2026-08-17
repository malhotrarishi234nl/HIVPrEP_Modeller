"""
tests/test_psa_metrics.py — V11.10.1

6 tests for PSA metric extraction:
  - sum aggregation with year range
  - value_at_year (exact match)
  - value_at_year (nearest year)
  - missing column raises KeyError
  - year range outside simulation warns and returns 0.0
  - final aggregation
"""

import os
import sys
import warnings

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psa_metrics import MetricExtractor


# ── Helper to build synthetic DataFrames ────────────────────────────────────


def make_df(years, values, col_name="new_infections"):
    return pd.DataFrame({"year": years, col_name: values})


# ── Tests ───────────────────────────────────────────────────────────────────


def test_sum_aggregation():
    """Sum over year_range [2011, 2013] should equal 20+30+40 = 90."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "infections_sum",
            "column": "new_infections",
            "aggregation": "sum",
            "year_range": [2011, 2013],
        }
    ])
    result = extractor.extract(df)
    assert result["infections_sum"] == 90.0


def test_value_at_year():
    """value_at_year for year=2012 should return 30."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "infections_2012",
            "column": "new_infections",
            "aggregation": "value_at_year",
            "year": 2012,
        }
    ])
    result = extractor.extract(df)
    assert result["infections_2012"] == 30.0


def test_value_at_year_nearest():
    """
    When year=2011 is not in df (years=[2010, 2012, 2014]),
    should return value at nearest year (2010 or 2012).
    """
    df = make_df(
        years=[2010, 2012, 2014],
        values=[1, 2, 3],
    )
    extractor = MetricExtractor([
        {
            "name": "nearest_test",
            "column": "new_infections",
            "aggregation": "value_at_year",
            "year": 2011,
        }
    ])
    result = extractor.extract(df)
    # Nearest year is either 2010 (value=1) or 2012 (value=2) — both are distance 1
    assert result["nearest_test"] in (1.0, 2.0)


def test_missing_column_raises():
    """Referencing a column that doesn't exist should raise KeyError."""
    df = make_df(years=[2010], values=[10])
    extractor = MetricExtractor([
        {
            "name": "bad_metric",
            "column": "nonexistent_column",
            "aggregation": "sum",
            "year_range": [2010, 2010],
        }
    ])
    with pytest.raises(KeyError, match="not in the simulation output"):
        extractor.extract(df)


def test_year_range_outside_sim_warns():
    """year_range entirely outside simulation should warn and return 0.0."""
    df = make_df(
        years=[2010, 2011, 2012],
        values=[10, 20, 30],
    )
    extractor = MetricExtractor([
        {
            "name": "future_sum",
            "column": "new_infections",
            "aggregation": "sum",
            "year_range": [2013, 2015],
        }
    ])
    with pytest.warns(UserWarning):
        result = extractor.extract(df)
    assert result["future_sum"] == 0.0


def test_final_aggregation():
    """Final aggregation should return the last row's value."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "last_value",
            "column": "new_infections",
            "aggregation": "final",
        }
    ])
    result = extractor.extract(df)
    assert result["last_value"] == 50.0


# ── V11.11.1: Additional aggregation tests ───────────────────────────────────


def test_mean_aggregation():
    """Mean over year_range [2011, 2013] should equal (20+30+40)/3 = 30.0."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "infections_mean",
            "column": "new_infections",
            "aggregation": "mean",
            "year_range": [2011, 2013],
        }
    ])
    result = extractor.extract(df)
    assert result["infections_mean"] == 30.0


def test_max_aggregation():
    """Max over year_range [2011, 2013] should equal 40."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "infections_max",
            "column": "new_infections",
            "aggregation": "max",
            "year_range": [2011, 2013],
        }
    ])
    result = extractor.extract(df)
    assert result["infections_max"] == 40.0


def test_min_aggregation():
    """Min over year_range [2011, 2013] should equal 20."""
    df = make_df(
        years=[2010, 2011, 2012, 2013, 2014],
        values=[10, 20, 30, 40, 50],
    )
    extractor = MetricExtractor([
        {
            "name": "infections_min",
            "column": "new_infections",
            "aggregation": "min",
            "year_range": [2011, 2013],
        }
    ])
    result = extractor.extract(df)
    assert result["infections_min"] == 20.0
