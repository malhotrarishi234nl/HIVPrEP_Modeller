#!/usr/bin/env python3
"""
Convergence Test: Prove that residual divergence is operator-splitting artifact.

If results converge smoothly as dt→0, the residual is numerical integration error.
If results jump or oscillate, there's still a bug.

Also produces calendar-year aggregated births/year and bg_deaths/year.
"""

import json
import sys
import os
import io
import contextlib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_v3 import SimulationEngineV3


def create_config(step_size, end_year=2015, deterministic=True):
    """Create config with specific step_size."""
    with open("config_v6_rel_pop.json") as f:
        config = json.load(f)
    config["time"]["step_size"] = step_size
    config["time"]["end_year"] = end_year
    if "output" in config:
        config["output"]["enabled"] = {"value": False}
    config["simulation_controls"]["deterministic"] = {"value": deterministic}
    path = f"configs/temp_convergence_s{step_size}.json"
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def run_simulation(config_path, seed=42):
    """Run simulation quietly, return full summary DataFrame."""
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(config_path, seed=seed)
        result = engine.run(run_number=1)
    return result["summary"]


def aggregate_annual(summary_df, step_size):
    """
    Aggregate sub-annual data to calendar years.
    Returns DataFrame with one row per calendar year.
    """
    years_per_step = step_size / 12.0
    df = summary_df.copy()

    # Assign calendar year (floor of year column)
    df["calendar_year"] = np.floor(df["year"]).astype(int)

    # For population: take the LAST value in each calendar year
    pop_annual = df.groupby("calendar_year")["total_population"].last()

    # For births and deaths: SUM within each calendar year
    # These are per-step counts, summing gives annual totals
    births_annual = df.groupby("calendar_year")["births"].sum() if "births" in df.columns else None
    bg_deaths_annual = df.groupby("calendar_year")["background_deaths"].sum() if "background_deaths" in df.columns else None

    result = pd.DataFrame({"population": pop_annual})
    if births_annual is not None:
        result["births"] = births_annual
    if bg_deaths_annual is not None:
        result["bg_deaths"] = bg_deaths_annual

    return result


# ============================================================================
# TASK 1 & 3: Convergence as dt → 0
# ============================================================================
def run_convergence_test():
    print("=" * 80)
    print("CONVERGENCE TEST: Population in 2015 as dt → 0")
    print("=" * 80)
    print("Config: config_v6_rel_pop.json, 1970-2015, Deterministic, seed=42\n")

    # step_sizes: 12, 6, 3, 2, 1 months → dt: 1.0, 0.5, 0.25, 0.167, 0.083
    step_sizes = [12, 6, 3, 2, 1]
    results = {}

    for ss in step_sizes:
        dt = ss / 12.0
        print(f"  Running step_size={ss:>2} (dt={dt:.4f})...", end="", flush=True)
        cfg = create_config(ss)
        summary = run_simulation(cfg)
        final = summary.iloc[-1]

        # Also get totals
        total_births = summary["births"].sum() if "births" in summary.columns else 0
        total_bg_deaths = summary["background_deaths"].sum() if "background_deaths" in summary.columns else 0

        results[ss] = {
            "dt": dt,
            "population": final["total_population"],
            "total_births": total_births,
            "total_bg_deaths": total_bg_deaths,
            "summary": summary,
        }
        print(f" pop={final['total_population']:,.0f}")

    # Print convergence table
    ref_pop = results[12]["population"]
    print(f"\n{'dt':>8} {'step_size':>10} {'Population 2015':>18} {'vs dt=1':>10} "
          f"{'Total Births':>14} {'Total BG Deaths':>16}")
    print("-" * 82)

    prev_pop = None
    for ss in step_sizes:
        r = results[ss]
        div_vs_ref = (r["population"] - ref_pop) / ref_pop * 100
        line = (f"{r['dt']:>8.4f} {ss:>7} mo {r['population']:>18,.0f} {div_vs_ref:>+9.2f}% "
                f"{r['total_births']:>14,.0f} {r['total_bg_deaths']:>16,.0f}")
        print(line)
        prev_pop = r["population"]

    # Compute successive differences to show convergence rate
    print(f"\n{'dt':>8} {'Population':>18} {'Δ from prev dt':>16} {'Ratio':>8}")
    print("-" * 55)
    prev_pop = None
    prev_delta = None
    for ss in step_sizes:
        r = results[ss]
        if prev_pop is not None:
            delta = r["population"] - prev_pop
            ratio_str = ""
            if prev_delta is not None and prev_delta != 0:
                ratio = delta / prev_delta
                ratio_str = f"{ratio:.3f}"
            print(f"{r['dt']:>8.4f} {r['population']:>18,.0f} {delta:>+16,.0f} {ratio_str:>8}")
            prev_delta = delta
        else:
            print(f"{r['dt']:>8.4f} {r['population']:>18,.0f} {'(reference)':>16}")
        prev_pop = r["population"]

    print("\nConvergence analysis:")
    pops = [results[ss]["population"] for ss in step_sizes]
    diffs = [pops[i+1] - pops[i] for i in range(len(pops)-1)]
    if all(d < 0 for d in diffs):
        print("  ✓ Population MONOTONICALLY DECREASING as dt→0 (smooth convergence)")
    elif all(d > 0 for d in diffs):
        print("  ✓ Population MONOTONICALLY INCREASING as dt→0 (smooth convergence)")
    else:
        print("  ✗ Population NOT monotonic — possible remaining bug!")

    # Check convergence rate
    if len(diffs) >= 2:
        ratios = []
        for i in range(1, len(diffs)):
            if diffs[i-1] != 0:
                ratios.append(abs(diffs[i] / diffs[i-1]))
        if ratios:
            avg_ratio = np.mean(ratios)
            print(f"  Average successive difference ratio: {avg_ratio:.3f}")
            if avg_ratio < 1.0:
                print(f"  ✓ Differences SHRINKING (ratio < 1) → converging")
            else:
                print(f"  ✗ Differences NOT shrinking — possible issue")

    return results


# ============================================================================
# TASK 4: Calendar-year aggregated births/year and bg_deaths/year
# ============================================================================
def run_annual_vital_rates(results):
    print("\n\n" + "=" * 80)
    print("CALENDAR-YEAR AGGREGATED VITAL RATES (1970-2015)")
    print("=" * 80)

    step_sizes_for_rates = [12, 6, 3]
    annual_data = {}

    for ss in step_sizes_for_rates:
        dt = ss / 12.0
        summary = results[ss]["summary"]
        annual = aggregate_annual(summary, ss)
        annual_data[ss] = annual

    # Get common years
    common_years = sorted(set.intersection(*[set(annual_data[ss].index) for ss in step_sizes_for_rates]))
    common_years = [y for y in common_years if 1970 <= y <= 2014]  # Full calendar years only

    # Print births comparison
    print(f"\n--- BIRTHS PER YEAR ---")
    print(f"{'Year':>6}", end="")
    for ss in step_sizes_for_rates:
        print(f"  {'dt='+str(ss/12):>12}", end="")
    print(f"  {'Max Dev %':>10}")
    print("-" * 60)

    max_births_dev = 0
    births_devs_by_year = []
    for year in common_years:
        vals = []
        print(f"{year:>6}", end="")
        for ss in step_sizes_for_rates:
            b = annual_data[ss].loc[year, "births"] if "births" in annual_data[ss].columns else 0
            vals.append(b)
            print(f"  {b:>12,.0f}", end="")
        ref = vals[0]  # dt=1 as reference
        if ref > 0:
            max_dev = max(abs(v - ref) / ref * 100 for v in vals)
        else:
            max_dev = 0
        max_births_dev = max(max_births_dev, max_dev)
        births_devs_by_year.append(max_dev)
        print(f"  {max_dev:>9.2f}%")

    print(f"\n  MAX deviation in births across all years: {max_births_dev:.2f}%")

    # Print deaths comparison
    print(f"\n--- BACKGROUND DEATHS PER YEAR ---")
    print(f"{'Year':>6}", end="")
    for ss in step_sizes_for_rates:
        print(f"  {'dt='+str(ss/12):>12}", end="")
    print(f"  {'Max Dev %':>10}")
    print("-" * 60)

    max_deaths_dev = 0
    deaths_devs_by_year = []
    for year in common_years:
        vals = []
        print(f"{year:>6}", end="")
        for ss in step_sizes_for_rates:
            d = annual_data[ss].loc[year, "bg_deaths"] if "bg_deaths" in annual_data[ss].columns else 0
            vals.append(d)
            print(f"  {d:>12,.0f}", end="")
        ref = vals[0]
        if ref > 0:
            max_dev = max(abs(v - ref) / ref * 100 for v in vals)
        else:
            max_dev = 0
        max_deaths_dev = max(max_deaths_dev, max_dev)
        deaths_devs_by_year.append(max_dev)
        print(f"  {max_dev:>9.2f}%")

    print(f"\n  MAX deviation in bg_deaths across all years: {max_deaths_dev:.2f}%")

    # Summary
    print(f"\n--- SUMMARY ---")
    print(f"  Births max annual deviation:     {max_births_dev:.2f}%")
    print(f"  BG Deaths max annual deviation:  {max_deaths_dev:.2f}%")

    return annual_data


if __name__ == "__main__":
    results = run_convergence_test()
    annual_data = run_annual_vital_rates(results)
