"""Tests for the covariance-aware A-1 diagnostic analysis."""

import numpy as np
import pandas as pd
import pytest

from tools.run_a1_qaly_variance_decomposition import (
    _parse_seeds,
    _variance_summary,
)


def _synthetic_runs() -> pd.DataFrame:
    # Components deliberately move in opposite directions.  Treating their
    # standalone variances as additive would overstate total variance.
    rows = [
        {"mode": "deterministic", "delta_a": 3.0, "delta_b": -1.0, "delta_qaly": 2.0},
        {"mode": "stochastic", "delta_a": 10.0, "delta_b": -8.0, "delta_qaly": 2.0},
        {"mode": "stochastic", "delta_a": 20.0, "delta_b": -14.0, "delta_qaly": 6.0},
        {"mode": "stochastic", "delta_a": 30.0, "delta_b": -24.0, "delta_qaly": 6.0},
        {"mode": "stochastic", "delta_a": 40.0, "delta_b": -30.0, "delta_qaly": 10.0},
    ]
    return pd.DataFrame(rows)


def test_parse_seed_ranges_and_reject_duplicates():
    assert _parse_seeds("42-44,50") == [42, 43, 44, 50]
    with pytest.raises(Exception, match="duplicate"):
        _parse_seeds("42,42")


def test_variance_allocation_reconciles_with_correlated_components():
    runs = _synthetic_runs()
    summary, covariance = _variance_summary(
        runs,
        ["delta_a", "delta_b"],
        ["a", "b"],
        runs.iloc[0],
    )
    total_variance = runs.loc[runs["mode"] == "stochastic", "delta_qaly"].var(ddof=1)
    assert covariance.to_numpy().sum() == pytest.approx(total_variance)
    component_rows = summary.loc[summary["component"] != "TOTAL_DELTA_QALY"]
    assert component_rows["allocated_variance_share_percent"].sum() == pytest.approx(100.0)
    assert summary.iloc[-1]["standalone_sample_variance"] == pytest.approx(total_variance)


def test_variance_allocation_rejects_nonreconciling_total():
    runs = _synthetic_runs()
    stochastic = runs["mode"] == "stochastic"
    runs.loc[stochastic, "delta_qaly"] += np.array([0.0, 1.0, 0.0, 0.0])
    with pytest.raises(AssertionError, match="does not reconcile"):
        _variance_summary(
            runs,
            ["delta_a", "delta_b"],
            ["a", "b"],
            runs.iloc[0],
        )
