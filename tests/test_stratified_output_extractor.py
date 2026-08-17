"""
Tests for tools/stratified_output_extractor.py — pure logic only.

No simulation is run. All inputs are synthetic DataFrames.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stratified_output_extractor import (
    ALL_STRATIFIED_FIELDS,
    extract_stratified_outputs,
    probe_available_fields,
    _STRATIFIED_FLOW_FIELDS,
    _STRATIFIED_STOCK_FIELDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(years_quarters, extra_cols: dict = None) -> pd.DataFrame:
    """Build a synthetic summary DataFrame with one row per (year, quarter).

    years_quarters: list of float year values, e.g. [1995.0, 1995.25, 1995.5, 1995.75, 1996.0, ...]
    extra_cols: {col_name: list_of_values} — must have same length as years_quarters.
    """
    n = len(years_quarters)
    data = {"year": years_quarters}
    if extra_cols:
        for col, vals in extra_cols.items():
            assert len(vals) == n, f"col {col}: expected {n} values, got {len(vals)}"
            data[col] = vals
    return pd.DataFrame(data)


def _all_fields_df(n_years: int = 3, steps_per_year: int = 4) -> pd.DataFrame:
    """DataFrame with all stratified fields, n_years calendar years, quarterly steps."""
    quarters = [y + q * 0.25 for y in range(2000, 2000 + n_years) for q in range(steps_per_year)]
    n = len(quarters)
    extra = {f: list(range(1, n + 1)) for f in ALL_STRATIFIED_FIELDS}
    return _make_df(quarters, extra)


# ---------------------------------------------------------------------------
# Field registration
# ---------------------------------------------------------------------------


class TestFieldRegistry:
    def test_all_stratified_fields_is_union(self):
        assert set(ALL_STRATIFIED_FIELDS) == set(_STRATIFIED_STOCK_FIELDS) | set(_STRATIFIED_FLOW_FIELDS)

    def test_required_stock_fields_present(self):
        for f in ["msm_hiv_pos", "non_msm_hiv_pos", "msm_undiagnosed_hiv_pos", "non_msm_undiagnosed_hiv_pos"]:
            assert f in _STRATIFIED_STOCK_FIELDS

    def test_required_flow_fields_present(self):
        for f in ["new_infections_msm", "new_infections_non_msm",
                  "migrant_hiv_msm_entries", "migrant_hiv_non_msm_entries"]:
            assert f in _STRATIFIED_FLOW_FIELDS

    def test_no_overlap_between_stock_and_flow(self):
        assert set(_STRATIFIED_STOCK_FIELDS).isdisjoint(set(_STRATIFIED_FLOW_FIELDS))


# ---------------------------------------------------------------------------
# extract_stratified_outputs: input handling
# ---------------------------------------------------------------------------


class TestInputHandling:
    def test_accepts_dataframe_directly(self):
        df = _all_fields_df()
        outputs, missing = extract_stratified_outputs(df)
        assert isinstance(outputs, dict)
        assert isinstance(missing, list)

    def test_accepts_result_dict_with_summary_key(self):
        df = _all_fields_df()
        result = {"summary": df, "other_stuff": 42}
        outputs, missing = extract_stratified_outputs(result)
        assert isinstance(outputs, dict)

    def test_raises_on_unknown_type(self):
        with pytest.raises(TypeError):
            extract_stratified_outputs("not_a_dataframe")

    def test_raises_on_dict_without_summary(self):
        with pytest.raises((TypeError, KeyError)):
            extract_stratified_outputs({"no_summary_key": pd.DataFrame()})


# ---------------------------------------------------------------------------
# extract_stratified_outputs: all fields present
# ---------------------------------------------------------------------------


class TestAllFieldsPresent:
    def setup_method(self):
        self.df = _all_fields_df(n_years=3, steps_per_year=4)
        self.outputs, self.missing = extract_stratified_outputs(self.df, start_year=2000, end_year=2002)

    def test_no_missing_fields_when_all_present(self):
        assert self.missing == []

    def test_outputs_has_all_field_keys(self):
        for f in ALL_STRATIFIED_FIELDS:
            assert f in self.outputs

    def test_outputs_cover_requested_years(self):
        for year in [2000, 2001, 2002]:
            assert year in self.outputs["msm_hiv_pos"]

    def test_outputs_are_floats(self):
        for f in ALL_STRATIFIED_FIELDS:
            for v in self.outputs[f].values():
                assert isinstance(v, float)


# ---------------------------------------------------------------------------
# extract_stratified_outputs: year window filtering
# ---------------------------------------------------------------------------


class TestYearWindowFiltering:
    def test_years_outside_window_excluded(self):
        df = _all_fields_df(n_years=5, steps_per_year=4)  # years 2000–2004
        outputs, _ = extract_stratified_outputs(df, start_year=2001, end_year=2003)
        years = list(outputs["msm_hiv_pos"].keys())
        assert 2000 not in years
        assert 2004 not in years
        for y in [2001, 2002, 2003]:
            assert y in years

    def test_empty_window_returns_empty_dicts(self):
        df = _all_fields_df(n_years=3, steps_per_year=4)  # years 2000–2002
        outputs, _ = extract_stratified_outputs(df, start_year=2010, end_year=2020)
        for f in ALL_STRATIFIED_FIELDS:
            assert outputs[f] == {}


# ---------------------------------------------------------------------------
# extract_stratified_outputs: stock fields use year-end value
# ---------------------------------------------------------------------------


class TestStockFieldsUseYearEndValue:
    def test_stock_uses_last_row_of_year(self):
        """
        Each quarter has a distinct stock value; only the Q4 (last row) should appear.
        Stock values: Q1=10, Q2=20, Q3=30, Q4=40 for year 2000.
        """
        quarters = [2000.0, 2000.25, 2000.5, 2000.75]
        stock_values = [10, 20, 30, 40]
        df = _make_df(quarters, {"msm_hiv_pos": stock_values})
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        assert outputs["msm_hiv_pos"][2000] == pytest.approx(40.0)

    def test_stock_not_summed_over_year(self):
        """Stock field should NOT equal sum of all quarterly values."""
        quarters = [2000.0, 2000.25, 2000.5, 2000.75]
        stock_values = [5, 5, 5, 5]
        df = _make_df(quarters, {"non_msm_hiv_pos": stock_values})
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        # last row value = 5, NOT sum=20
        assert outputs["non_msm_hiv_pos"][2000] == pytest.approx(5.0)

    def test_all_stock_fields_use_year_end(self):
        """All stock fields return year-end value, not sum."""
        quarters = [2001.0, 2001.25, 2001.5, 2001.75]
        extra = {f: [1, 2, 3, 99] for f in _STRATIFIED_STOCK_FIELDS}
        df = _make_df(quarters, extra)
        outputs, _ = extract_stratified_outputs(df, start_year=2001, end_year=2001)
        for f in _STRATIFIED_STOCK_FIELDS:
            assert outputs[f][2001] == pytest.approx(99.0), f"{f} should be 99 (year-end), not sum"


# ---------------------------------------------------------------------------
# extract_stratified_outputs: flow fields are summed over year
# ---------------------------------------------------------------------------


class TestFlowFieldsSummedOverYear:
    def test_flow_is_annual_sum(self):
        """Flow field: Q1=3, Q2=5, Q3=7, Q4=9 → annual sum = 24."""
        quarters = [2000.0, 2000.25, 2000.5, 2000.75]
        df = _make_df(quarters, {"new_infections_msm": [3, 5, 7, 9]})
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        assert outputs["new_infections_msm"][2000] == pytest.approx(24.0)

    def test_flow_not_last_row(self):
        """Flow field should NOT equal just the last row value."""
        quarters = [2000.0, 2000.25, 2000.5, 2000.75]
        df = _make_df(quarters, {"migrant_hiv_non_msm_entries": [10, 10, 10, 1]})
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        # sum = 31, not 1 (last row)
        assert outputs["migrant_hiv_non_msm_entries"][2000] == pytest.approx(31.0)

    def test_all_flow_fields_are_summed(self):
        """All flow fields return annual sums."""
        quarters = [2003.0, 2003.25, 2003.5, 2003.75]
        extra = {f: [2, 4, 6, 8] for f in _STRATIFIED_FLOW_FIELDS}
        df = _make_df(quarters, extra)
        outputs, _ = extract_stratified_outputs(df, start_year=2003, end_year=2003)
        for f in _STRATIFIED_FLOW_FIELDS:
            assert outputs[f][2003] == pytest.approx(20.0), f"{f} should sum to 20"


# ---------------------------------------------------------------------------
# extract_stratified_outputs: partial / missing fields
# ---------------------------------------------------------------------------


class TestMissingFields:
    def test_missing_fields_listed(self):
        """When a field is absent from the DataFrame, it appears in missing_fields."""
        df = _make_df([2000.0, 2000.25], {"msm_hiv_pos": [100, 110]})
        _, missing = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        absent = [f for f in ALL_STRATIFIED_FIELDS if f != "msm_hiv_pos"]
        for f in absent:
            assert f in missing

    def test_missing_field_yields_empty_dict(self):
        """Output for a missing field is an empty dict, not a KeyError."""
        df = _make_df([2000.0])
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        for f in ALL_STRATIFIED_FIELDS:
            assert f in outputs
            assert outputs[f] == {}

    def test_present_field_not_in_missing(self):
        df = _make_df([2000.0, 2000.25], {"non_msm_hiv_pos": [50, 60]})
        _, missing = extract_stratified_outputs(df, start_year=2000, end_year=2000)
        assert "non_msm_hiv_pos" not in missing

    def test_no_crash_when_all_fields_absent(self):
        """DataFrame with no stratified columns → all missing, no exception."""
        df = _make_df([2005.0, 2005.25])
        outputs, missing = extract_stratified_outputs(df, start_year=2005, end_year=2005)
        assert set(missing) == set(ALL_STRATIFIED_FIELDS)
        for f in ALL_STRATIFIED_FIELDS:
            assert outputs[f] == {}

    def test_partial_fields_present(self):
        """Some fields present, others absent; mix handled cleanly."""
        present = {"msm_hiv_pos": [100, 120], "new_infections_msm": [5, 7]}
        df = _make_df([2010.0, 2010.25], present)
        outputs, missing = extract_stratified_outputs(df, start_year=2010, end_year=2010)
        assert "msm_hiv_pos" not in missing
        assert "new_infections_msm" not in missing
        assert outputs["msm_hiv_pos"][2010] == pytest.approx(120.0)  # stock → year-end
        assert outputs["new_infections_msm"][2010] == pytest.approx(12.0)  # flow → sum


# ---------------------------------------------------------------------------
# extract_stratified_outputs: multiple years
# ---------------------------------------------------------------------------


class TestMultipleYears:
    def test_each_year_computed_independently(self):
        """Stock/flow values are computed per calendar year."""
        quarters = [
            2000.0, 2000.25, 2000.5, 2000.75,
            2001.0, 2001.25, 2001.5, 2001.75,
        ]
        msm_stocks = [10, 20, 30, 40,   # year 2000 → year-end = 40
                      50, 60, 70, 80]   # year 2001 → year-end = 80
        msm_flows  = [1, 1, 1, 1,       # year 2000 → sum = 4
                      2, 2, 2, 2]       # year 2001 → sum = 8
        df = _make_df(quarters, {
            "msm_hiv_pos": msm_stocks,
            "new_infections_msm": msm_flows,
        })
        outputs, _ = extract_stratified_outputs(df, start_year=2000, end_year=2001)
        assert outputs["msm_hiv_pos"][2000] == pytest.approx(40.0)
        assert outputs["msm_hiv_pos"][2001] == pytest.approx(80.0)
        assert outputs["new_infections_msm"][2000] == pytest.approx(4.0)
        assert outputs["new_infections_msm"][2001] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# probe_available_fields
# ---------------------------------------------------------------------------


class TestProbeAvailableFields:
    def test_probe_all_present(self):
        df = _all_fields_df()
        result = probe_available_fields(df)
        assert set(result["present"]) == set(ALL_STRATIFIED_FIELDS)
        assert result["missing"] == []

    def test_probe_none_present(self):
        df = _make_df([2000.0])
        result = probe_available_fields(df)
        assert result["present"] == []
        assert set(result["missing"]) == set(ALL_STRATIFIED_FIELDS)

    def test_probe_partial(self):
        df = _make_df([2000.0], {"msm_hiv_pos": [100], "new_infections_non_msm": [5]})
        result = probe_available_fields(df)
        assert "msm_hiv_pos" in result["present"]
        assert "new_infections_non_msm" in result["present"]
        assert "non_msm_hiv_pos" in result["missing"]

    def test_probe_accepts_result_dict(self):
        df = _all_fields_df()
        result = probe_available_fields({"summary": df})
        assert set(result["present"]) == set(ALL_STRATIFIED_FIELDS)
