#!/usr/bin/env python3
"""
Basic validation test for fractional aging implementation.

Tests:
1. Population conservation (no population created/destroyed by aging)
2. Correct transfer percentages (20% moves, 80% stays)
3. Terminal bin accumulation (receives inflow, no outflow)

Usage:
    python test_fractional_aging_basic.py
"""

import numpy as np
import json
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent))

from engine_v3 import SimulationEngine


def test_population_conservation():
    """Test that fractional aging conserves total population."""
    print("\n=== Test 1: Population Conservation ===")

    # Load minimal config
    config_path = str(Path(__file__).parent / "test_fractional_aging_config.json")

    # Create engine (deterministic mode set in config)
    engine = SimulationEngine(config_path, seed=42)

    # Record initial population
    pop_before_aging = np.sum(engine.state.population)

    # Run one step (which includes aging)
    engine.step()

    # Record population after aging (net of births/deaths)
    pop_after_aging = np.sum(engine.state.population)

    # Calculate births and deaths that occurred
    births = engine.outputs.births[-1] if len(engine.outputs.births) > 0 else 0
    deaths = engine.outputs.total_deaths[-1] if len(engine.outputs.total_deaths) > 0 else 0

    # Expected population = initial + births - deaths
    expected_pop = pop_before_aging + births - deaths

    # Check conservation (aging should not create/destroy population)
    # Allow small tolerance for rounding
    diff = abs(pop_after_aging - expected_pop)
    max_tolerance = engine.groups.n_age_groups  # ±1 per age bin

    print(f"Population before: {pop_before_aging:,}")
    print(f"Births: {births:,}")
    print(f"Deaths: {deaths:,}")
    print(f"Expected after: {expected_pop:,}")
    print(f"Actual after: {pop_after_aging:,}")
    print(f"Difference: {diff:,}")
    print(f"Max tolerance: {max_tolerance}")

    if diff <= max_tolerance:
        print("✅ PASS: Population conserved")
        return True
    else:
        print(f"❌ FAIL: Population not conserved (diff={diff} > {max_tolerance})")
        return False


def test_transfer_percentages():
    """Test that approximately 20% transfers and 80% stays each year."""
    print("\n=== Test 2: Transfer Percentages ===")

    # Load and modify config to disable births/deaths for cleaner test
    config_path = Path(__file__).parent / "test_fractional_aging_config.json"
    with open(config_path) as f:
        config = json.load(f)

    # Disable births/deaths for clean transfer test
    config["fertility"]["enabled"]["value"] = False
    config["life_table"]["enabled"]["value"] = False
    config["demography"]["background_mortality"]["value"] = 0.0

    # Write modified config to temp file
    temp_config_path = Path(__file__).parent / "temp_test_transfer.json"
    with open(temp_config_path, 'w') as f:
        json.dump(config, f)

    engine = SimulationEngine(str(temp_config_path), seed=42)

    # Manually set population in one age bin for testing
    test_age_idx = 1  # Second age bin (20-24)
    initial_pop = 10000

    # Clear population and set test bin
    engine.state.population[:] = 0
    engine.state.population[test_age_idx, 0, 0, 0, 0, 0, 0] = initial_pop

    pop_before = np.copy(engine.state.population)

    # Apply just the aging step
    engine._apply_fractional_aging_adults()

    pop_after = engine.state.population

    # Check results
    stayed = pop_after[test_age_idx, 0, 0, 0, 0, 0, 0]
    moved = pop_after[test_age_idx + 1, 0, 0, 0, 0, 0, 0]

    expected_stayed = int(initial_pop * 0.8)
    expected_moved = initial_pop - expected_stayed

    print(f"Initial population in bin {test_age_idx}: {initial_pop:,}")
    print(f"Expected to stay (80%): {expected_stayed:,}")
    print(f"Actual stayed: {stayed:,}")
    print(f"Expected to move (20%): {expected_moved:,}")
    print(f"Actual moved: {moved:,}")

    # Check conservation
    total_after = stayed + moved
    print(f"Total after: {total_after:,} (should equal {initial_pop:,})")

    if stayed == expected_stayed and moved == expected_moved and total_after == initial_pop:
        print("✅ PASS: Correct transfer percentages")
        return True
    else:
        print("❌ FAIL: Transfer percentages incorrect")
        return False


def test_terminal_bin_accumulation():
    """Test that terminal age bin accumulates (no outflow)."""
    print("\n=== Test 3: Terminal Bin Accumulation ===")

    # Load and modify config
    config_path = Path(__file__).parent / "test_fractional_aging_config.json"
    with open(config_path) as f:
        config = json.load(f)

    # Disable births/deaths
    config["fertility"]["enabled"]["value"] = False
    config["life_table"]["enabled"]["value"] = False
    config["demography"]["background_mortality"]["value"] = 0.0

    # Write modified config to temp file
    temp_config_path = Path(__file__).parent / "temp_test_terminal.json"
    with open(temp_config_path, 'w') as f:
        json.dump(config, f)

    engine = SimulationEngine(str(temp_config_path), seed=42)

    # Set population in terminal bin and second-to-last bin
    terminal_idx = engine.groups.n_age_groups - 1
    second_to_last_idx = terminal_idx - 1

    engine.state.population[:] = 0
    engine.state.population[terminal_idx, 0, 0, 0, 0, 0, 0] = 5000
    engine.state.population[second_to_last_idx, 0, 0, 0, 0, 0, 0] = 10000

    pop_before_terminal = engine.state.population[terminal_idx, 0, 0, 0, 0, 0, 0]
    pop_before_second = engine.state.population[second_to_last_idx, 0, 0, 0, 0, 0, 0]

    # Apply aging
    engine._apply_fractional_aging_adults()

    pop_after_terminal = engine.state.population[terminal_idx, 0, 0, 0, 0, 0, 0]

    # Terminal should receive 20% from second-to-last AND keep all existing
    expected_inflow = int(pop_before_second * 0.2)
    expected_terminal = pop_before_terminal + expected_inflow

    print(f"Terminal bin before: {pop_before_terminal:,}")
    print(f"Second-to-last before: {pop_before_second:,}")
    print(f"Expected inflow (20% of second-to-last): {expected_inflow:,}")
    print(f"Expected terminal after: {expected_terminal:,}")
    print(f"Actual terminal after: {pop_after_terminal:,}")

    if pop_after_terminal == expected_terminal:
        print("✅ PASS: Terminal bin accumulates correctly")
        return True
    else:
        print(f"❌ FAIL: Terminal bin incorrect (expected {expected_terminal}, got {pop_after_terminal})")
        return False


def main():
    """Run all basic validation tests."""
    print("=" * 70)
    print("BASIC VALIDATION TESTS FOR FRACTIONAL AGING")
    print("=" * 70)

    results = []

    # Test 1: Population conservation
    try:
        results.append(("Population Conservation", test_population_conservation()))
    except Exception as e:
        print(f"❌ FAIL: Test crashed with error: {e}")
        results.append(("Population Conservation", False))

    # Test 2: Transfer percentages
    try:
        results.append(("Transfer Percentages", test_transfer_percentages()))
    except Exception as e:
        print(f"❌ FAIL: Test crashed with error: {e}")
        results.append(("Transfer Percentages", False))

    # Test 3: Terminal bin
    try:
        results.append(("Terminal Bin Accumulation", test_terminal_bin_accumulation()))
    except Exception as e:
        print(f"❌ FAIL: Test crashed with error: {e}")
        results.append(("Terminal Bin Accumulation", False))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")

    all_passed = all(passed for _, passed in results)

    if all_passed:
        print("\n🎉 All basic validation tests PASSED!")
        return 0
    else:
        print("\n⚠️  Some tests FAILED - review output above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
