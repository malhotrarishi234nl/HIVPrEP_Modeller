"""
Diagnostic-only helper for stratified stock/flow output extraction (V13.10).

Works from the simulation result summary DataFrame.
If stratified columns are absent, reports them as missing rather than raising.
Does NOT modify the engine or add new outputs.

Missing fields are listed in the returned manifest so the caller can write
them as NA in output tables.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Field registries
# ---------------------------------------------------------------------------

_STRATIFIED_STOCK_FIELDS: list[str] = [
    "msm_hiv_pos",
    "non_msm_hiv_pos",
    "msm_undiagnosed_hiv_pos",
    "non_msm_undiagnosed_hiv_pos",
]

_STRATIFIED_FLOW_FIELDS: list[str] = [
    "new_infections_msm",
    "new_infections_non_msm",
    "migrant_hiv_msm_entries",
    "migrant_hiv_non_msm_entries",
]

ALL_STRATIFIED_FIELDS: list[str] = _STRATIFIED_STOCK_FIELDS + _STRATIFIED_FLOW_FIELDS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_stratified_outputs(
    sim_results,
    start_year: int = 1990,
    end_year: int = 2025,
) -> Tuple[Dict[str, Dict[int, float]], List[str]]:
    """Extract stratified outputs from simulation result summary.

    Stock fields are taken from the last timestep of each calendar year.
    Flow fields are summed across all timesteps within each calendar year.

    Returns:
        outputs: {field_name: {year: value}} where absent fields have empty dicts.
        missing_fields: list of field names not found in the summary.
    """
    df = _summary_df(sim_results)

    work = df.sort_values("year").copy()
    work["calib_year"] = work["year"].astype(float).apply(int)
    work = work[(work["calib_year"] >= start_year) & (work["calib_year"] <= end_year)]

    present_stocks = [f for f in _STRATIFIED_STOCK_FIELDS if f in work.columns]
    present_flows = [f for f in _STRATIFIED_FLOW_FIELDS if f in work.columns]
    missing = [f for f in ALL_STRATIFIED_FIELDS if f not in work.columns]

    outputs: Dict[str, Dict[int, float]] = {}
    for field in ALL_STRATIFIED_FIELDS:
        outputs[field] = {}

    for year, year_df in work.groupby("calib_year", sort=True):
        final_row = year_df.iloc[-1]
        for field in present_stocks:
            val = final_row[field]
            try:
                outputs[field][int(year)] = float(val)
            except (TypeError, ValueError):
                pass
        for field in present_flows:
            try:
                outputs[field][int(year)] = float(year_df[field].sum())
            except (TypeError, ValueError):
                pass

    return outputs, missing


def probe_available_fields(sim_results) -> dict:
    """Return a summary of which stratified fields are available.

    Useful for writing the manifest without running the full extraction.
    """
    df = _summary_df(sim_results)
    cols = set(df.columns)
    return {
        "present": [f for f in ALL_STRATIFIED_FIELDS if f in cols],
        "missing": [f for f in ALL_STRATIFIED_FIELDS if f not in cols],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _summary_df(sim_results) -> pd.DataFrame:
    if isinstance(sim_results, pd.DataFrame):
        return sim_results.copy()
    if isinstance(sim_results, dict) and "summary" in sim_results:
        return sim_results["summary"].copy()
    raise TypeError(
        "extract_stratified_outputs expects a summary DataFrame or run_v3 result dict"
    )
