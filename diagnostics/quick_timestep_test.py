#!/usr/bin/env python3
"""
Quick Timestep Test - Verify fractional aging scales correctly

Tests that aging_fraction is properly scaled by years_per_step
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_v3 import TimeSystem
from demography import ChildhoodPool
import numpy as np

def test_fractional_aging_scaling():
    """Test that fractional aging scales with timestep duration."""

    print("="*80)
    print("FRACTIONAL AGING SCALING TEST")
    print("="*80)
    print()

    # Create a simple childhood pool with 1000 children in the oldest bin
    test_cases = [
        ("12-month (1 year)", 12, 1.0),
        ("6-month (0.5 year)", 6, 0.5),
        ("3-month (0.25 year)", 3, 0.25),
    ]

    for name, step_size, expected_years_per_step in test_cases:
        print(f"Testing {name}:")
        print(f"  step_size = {step_size} months")

        # Create TimeSystem
        time_config = {
            "unit": {"value": "months"},
            "step_size": {"value": step_size},
            "start_year": {"value": 1970},
            "end_year": {"value": 2020}
        }
        time_system = TimeSystem(time_config)

        print(f"  years_per_step = {time_system.years_per_step}")

        # Create childhood pool
        config = {
            "childhood": {
                "enabled": {"value": True},
                "age_bins": {"value": ["0-4", "5-9", "10-14"]},
                "background_mortality_annual": {"value": 0.005}
            }
        }
        childhood = ChildhoodPool(config)

        # Put 1000 children in oldest bin (10-14)
        childhood.population[2, childhood.FEMALE] = 1000
        childhood.population[2, childhood.MALE] = 1000
        initial_total = 2000

        print(f"  Initial population in 10-14 bin: {initial_total}")

        # Apply fractional aging
        rng = np.random.RandomState(42)
        females_out, males_out = childhood.apply_fractional_aging(
            rng, deterministic=True, time_system=time_system
        )

        total_aged_out = females_out + males_out
        remaining = childhood.population[2, :].sum()

        print(f"  Children aging out: {total_aged_out}")
        print(f"  Children remaining: {remaining}")

        # Calculate expected aging (20% per year, scaled by timestep)
        expected_aging_fraction = 0.2 * time_system.years_per_step
        expected_aged_out = int(initial_total * expected_aging_fraction)

        print(f"  Expected aging fraction: {expected_aging_fraction:.4f}")
        print(f"  Expected aged out: {expected_aged_out}")

        # Verify
        if total_aged_out == expected_aged_out:
            print(f"  ✓ PASS: Aging scaled correctly")
        else:
            print(f"  ✗ FAIL: Expected {expected_aged_out}, got {total_aged_out}")
            print(f"    Difference: {total_aged_out - expected_aged_out}")

        print()

    print("="*80)
    print("CONCLUSION")
    print("="*80)
    print()
    print("If all tests passed, fractional aging is now time-invariant.")
    print("Population dynamics should be consistent across timestep sizes.")
    print()

if __name__ == '__main__':
    try:
        test_fractional_aging_scaling()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
