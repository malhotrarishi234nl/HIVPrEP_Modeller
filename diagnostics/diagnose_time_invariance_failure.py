#!/usr/bin/env python3
"""
Diagnostic Script: Identify Time-Invariance Failure

This script instruments the simulation to track exactly where
time-invariance breaks down.
"""

import json
import numpy as np
from pathlib import Path
from run_v3 import SimulationEngineV3


def diagnose_single_step(step_size: int):
    """
    Run ONE YEAR of simulation and track all flows.

    This isolates the issue by looking at a single year in detail.
    """
    print(f"\n{'='*80}")
    print(f"DIAGNOSING step_size={step_size} (Δt={step_size/12:.3f} years)")
    print('='*80)

    # Load config
    config_path = "configs/config_v5.json"
    with open(config_path, 'r') as f:
        config = json.load(f)

    # Modify for test
    config["time"]["step_size"]["value"] = step_size
    config["time"]["end_year"]["value"] = 1971  # Just 1 year

    # Save test config
    test_config = f"configs/test_diag_{step_size}.json"
    with open(test_config, 'w') as f:
        json.dump(config, f)

    # Create engine
    engine = SimulationEngineV3(test_config, seed=42)

    print(f"\nInitial state:")
    print(f"  Total population: {engine.state.get_total_population():,}")
    print(f"  Childhood pool: {engine.demography.childhood.get_total():,}")
    print(f"  Time.years_per_step: {engine.time.years_per_step}")

    # Calculate expected annual rates
    bg_mort_annual = 0.008  # From config
    bg_mort_step = engine.time.annual_to_step_rate(bg_mort_annual)

    print(f"\nExpected rates:")
    print(f"  Background mortality (annual): {bg_mort_annual:.4f} ({bg_mort_annual:.2%})")
    print(f"  Background mortality (per-step): {bg_mort_step:.4f} ({bg_mort_step:.2%})")

    # Track flows over the year
    steps_per_year = int(1.0 / engine.time.years_per_step)

    annual_births = 0
    annual_deaths = 0
    annual_debuts = 0

    initial_pop = engine.state.get_total_population()
    initial_childhood = engine.demography.childhood.get_total()

    print(f"\nRunning {steps_per_year} steps (1 year)...")

    for i in range(steps_per_year):
        step = i

        # Store pre-step population
        pre_pop = engine.state.get_total_population()
        pre_childhood = engine.demography.childhood.get_total()

        # Run one step
        engine.step_simulation(step)

        # Track changes
        post_pop = engine.state.get_total_population()
        post_childhood = engine.demography.childhood.get_total()

        # Extract flows from outputs (last entry)
        step_births = engine.outputs['births'][-1] if 'births' in engine.outputs else 0
        step_deaths = engine.outputs['background_deaths'][-1] if 'background_deaths' in engine.outputs else 0
        step_debuts = engine.outputs.get('sexual_debuts', [0])[-1] if 'sexual_debuts' in engine.outputs else 0

        annual_births += step_births
        annual_deaths += step_deaths
        annual_debuts += step_debuts

        if i == 0:  # Print details for first step
            print(f"\n  Step {i+1}:")
            print(f"    Adult pop: {pre_pop:,} → {post_pop:,} (Δ={post_pop-pre_pop:+,})")
            print(f"    Childhood: {pre_childhood:,} → {post_childhood:,} (Δ={post_childhood-pre_childhood:+,})")
            print(f"    Births: {step_births:,}")
            print(f"    Deaths: {step_deaths:,}")
            print(f"    Debuts: {step_debuts:,}")

            # Calculate implied rates
            if pre_pop > 0:
                implied_mort_rate = step_deaths / pre_pop
                print(f"    Implied mortality rate: {implied_mort_rate:.4f} ({implied_mort_rate:.2%})")
                print(f"    Expected mortality rate: {bg_mort_step:.4f} ({bg_mort_step:.2%})")
                if abs(implied_mort_rate - bg_mort_step) > 0.0001:
                    print(f"    ⚠ DISCREPANCY: {(implied_mort_rate/bg_mort_step - 1):+.1%}")

    final_pop = engine.state.get_total_population()
    final_childhood = engine.demography.childhood.get_total()

    # Summary
    print(f"\nANNUAL SUMMARY:")
    print(f"  Initial population (adult): {initial_pop:,}")
    print(f"  Initial population (child): {initial_childhood:,}")
    print(f"  Final population (adult): {final_pop:,}")
    print(f"  Final population (child): {final_childhood:,}")
    print(f"  Net change (adult): {final_pop - initial_pop:+,}")
    print(f"  Net change (child): {final_childhood - initial_childhood:+,}")
    print(f"\nFLOWS OVER THE YEAR:")
    print(f"  Total births: {annual_births:,}")
    print(f"  Total deaths: {annual_deaths:,}")
    print(f"  Total debuts: {annual_debuts:,}")
    print(f"  Net flow: {annual_births - annual_deaths:+,}")

    # Check against expected
    expected_deaths = initial_pop * bg_mort_annual
    print(f"\nVALIDATION:")
    print(f"  Expected deaths (0.8% of {initial_pop:,}): {expected_deaths:,.0f}")
    print(f"  Actual deaths: {annual_deaths:,}")
    print(f"  Discrepancy: {(annual_deaths/expected_deaths - 1):+.1%}")

    if abs(annual_deaths/expected_deaths - 1) > 0.05:
        print(f"  ✗ FAILED: Death rate deviates by more than 5%")
    else:
        print(f"  ✓ PASSED: Death rate within 5%")

    return {
        'step_size': step_size,
        'initial_pop': initial_pop,
        'final_pop': final_pop,
        'births': annual_births,
        'deaths': annual_deaths,
        'debuts': annual_debuts
    }


def main():
    """Run diagnostics for all timestep sizes."""
    print("="*80)
    print("TIME-INVARIANCE FAILURE DIAGNOSTIC")
    print("="*80)
    print("\nThis script runs ONE YEAR of simulation with different timesteps")
    print("and tracks all demographic flows to identify where invariance breaks.")

    results = {}

    for step_size in [12, 6, 3]:
        try:
            result = diagnose_single_step(step_size)
            results[step_size] = result
        except Exception as e:
            print(f"\nERROR with step_size={step_size}: {e}")
            import traceback
            traceback.print_exc()

    # Compare results
    if len(results) >= 2:
        print(f"\n{'='*80}")
        print("COMPARATIVE ANALYSIS")
        print('='*80)

        baseline = results.get(12) or results[min(results.keys())]

        print(f"\nComparing to baseline (step_size={baseline['step_size']}):")
        print(f"{'Timestep':<12} {'Births':>12} {'Deaths':>12} {'Debuts':>12} {'Final Pop':>15}")
        print("-"*80)

        for step_size in sorted(results.keys()):
            r = results[step_size]
            print(f"{step_size:<12} {r['births']:>12,} {r['deaths']:>12,} {r['debuts']:>12,} {r['final_pop']:>15,}")

        # Check invariance
        print(f"\nInvariance Check:")
        for step_size, r in results.items():
            if step_size == baseline['step_size']:
                continue

            birth_diff = abs(r['births'] - baseline['births']) / baseline['births']
            death_diff = abs(r['deaths'] - baseline['deaths']) / baseline['deaths']

            print(f"  step_size={step_size}:")
            print(f"    Births differ by: {birth_diff:+.1%}")
            print(f"    Deaths differ by: {death_diff:+.1%}")

            if birth_diff > 0.05 or death_diff > 0.05:
                print(f"    ✗ FAILED invariance test")
            else:
                print(f"    ✓ PASSED invariance test")


if __name__ == '__main__':
    main()
