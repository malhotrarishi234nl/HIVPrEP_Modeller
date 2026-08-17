"""
Model-output extraction for LHS epidemic calibration.
"""

from __future__ import annotations

from typing import Dict

import pandas as pd


def _summary_df(sim_results) -> pd.DataFrame:
    if isinstance(sim_results, pd.DataFrame):
        return sim_results.copy()
    if isinstance(sim_results, dict) and "summary" in sim_results:
        return sim_results["summary"].copy()
    raise TypeError("extract_annual_outputs expects a summary DataFrame or run_v3 result dict")


def extract_annual_outputs(
    sim_results,
    start_year: int = 1980,
    end_year: int = 2023,
) -> Dict[str, Dict[int, float]]:
    """
    Aggregate quarterly run_v3 outputs to annual calibration outputs.

    PLHIV is the year-end all-age-compatible stock.  The current model has
    adult HIV disease compartments only, so child PLHIV is zero unless a future
    summary column supplies it.  New infections and AIDS deaths are annual sums.
    """
    df = _summary_df(sim_results)
    required = {
        "year",
        "prevalence",
        "sexually_active_population",
        "new_infections",
        "aids_deaths",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Simulation summary missing calibration columns: {sorted(missing)}")

    work = df.sort_values("year").copy()
    work["calib_year"] = work["year"].astype(float).apply(int)
    work = work[(work["calib_year"] >= start_year) & (work["calib_year"] <= end_year)]

    outputs = {"PLHIV": {}, "new_infections": {}, "aids_deaths": {}}
    optional_flow_columns = [
        "new_diagnoses",
        "new_diagnoses_msm",
        "new_aids_cases",
        "art_initiations",
        "prep_oral_initiations",
        "prep_oral_dropouts",
        "prep_oral_corridor_additions",
        "prep_oral_corridor_removals",
        "prep_lena_initiations",
        "prep_lena_dropouts",
        "prep_lena_corridor_additions",
        "prep_lena_corridor_removals",
    ]
    optional_stock_columns = [
        "on_art_stock",
        "plhiv_female_15plus",
        "plhiv_male_15plus",
        "plhiv_child_0_14",
    ]
    for column in optional_flow_columns + optional_stock_columns:
        if column in work.columns:
            outputs[column] = {}
    if "msm_on_oral" in work.columns:
        outputs["msm_on_oral_end"] = {}
        outputs["msm_on_oral_mean"] = {}
        if "prep_oral_initiations" in work.columns:
            outputs["prep_oral_annual_unique_approx"] = {}

    previous_oral_end: float | None = None
    for year, year_df in work.groupby("calib_year", sort=True):
        final_row = year_df.iloc[-1]
        adult_plhiv = (
            float(final_row["adult_hiv_pos"])
            if "adult_hiv_pos" in final_row.index
            else float(final_row["prevalence"] * final_row["sexually_active_population"])
        )
        child_plhiv = (
            float(final_row["plhiv_child_0_14"])
            if "plhiv_child_0_14" in final_row.index
            else 0.0
        )
        outputs["PLHIV"][int(year)] = adult_plhiv + child_plhiv
        outputs["new_infections"][int(year)] = float(year_df["new_infections"].sum())
        outputs["aids_deaths"][int(year)] = float(year_df["aids_deaths"].sum())
        for column in optional_flow_columns:
            if column in outputs:
                outputs[column][int(year)] = float(year_df[column].sum())
        for column in optional_stock_columns:
            if column in outputs:
                outputs[column][int(year)] = float(final_row[column])
        if "msm_on_oral" in work.columns:
            oral_end = float(final_row["msm_on_oral"])
            outputs["msm_on_oral_end"][int(year)] = oral_end
            outputs["msm_on_oral_mean"][int(year)] = float(year_df["msm_on_oral"].mean())
            if "prep_oral_annual_unique_approx" in outputs:
                oral_start = (
                    previous_oral_end
                    if previous_oral_end is not None
                    else float(year_df["msm_on_oral"].iloc[0])
                )
                outputs["prep_oral_annual_unique_approx"][int(year)] = (
                    oral_start + float(year_df["prep_oral_initiations"].sum())
                )
            previous_oral_end = oral_end

    return outputs
