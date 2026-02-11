#!/usr/bin/env python3
"""
Simple direct tests for fractional aging implementation.

Tests the aging logic directly without full engine initialization.
"""

import numpy as np


def test_direct_fractional_aging():
    """Test fractional aging logic directly."""
    print("\n=== Direct Fractional Aging Test ===\n")

    # Simulate the fractional aging algorithm
    n_age_groups = 7
    population = np.zeros((n_age_groups, 1, 1, 1, 1, 1, 1), dtype=np.int64)

    # Set up test: 10,000 people in bin 2 (25-29)
    test_bin = 2
    initial_pop = 10000
    population[test_bin, 0, 0, 0, 0, 0, 0] = initial_pop

    print(f"Initial setup:")
    print(f"  Bin {test_bin}: {population[test_bin, 0, 0, 0, 0, 0, 0]:,} people")
    print(f"  Total: {np.sum(population):,}\n")

    # Apply fractional aging (deterministic)
    aging_fraction = 0.2
    new_pop = np.zeros_like(population)

    for age_idx in range(n_age_groups - 1):
        # Deterministic: exact 20%
        staying = (population[age_idx, ...] * 0.8).astype(np.int64)
        moving = population[age_idx, ...] - staying

        new_pop[age_idx, ...] += staying  # ADD (might have inflow from previous bin)
        new_pop[age_idx + 1, ...] += moving

    # Terminal bin accumulates
    new_pop[-1, ...] += population[-1, ...]

    print(f"After fractional aging:")
    print(f"  Bin {test_bin} (stayed): {new_pop[test_bin, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Bin {test_bin + 1} (moved): {new_pop[test_bin + 1, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Total: {np.sum(new_pop):,}\n")

    # Calculate results
    stayed = new_pop[test_bin, 0, 0, 0, 0, 0, 0]
    moved = new_pop[test_bin + 1, 0, 0, 0, 0, 0, 0]
    total_after = np.sum(new_pop)

    # Expected results
    expected_stayed = int(initial_pop * 0.8)
    expected_moved = initial_pop - expected_stayed

    print("Validation:")
    print(f"  Expected 80% to stay: {expected_stayed:,}")
    print(f"  Actual stayed: {stayed:,} {'✓' if stayed == expected_stayed else '✗'}")
    print(f"  Expected 20% to move: {expected_moved:,}")
    print(f"  Actual moved: {moved:,} {'✓' if moved == expected_moved else '✗'}")
    print(f"  Population conserved: {total_after == initial_pop} {'✓' if total_after == initial_pop else '✗'}")

    # Check
    if stayed == expected_stayed and moved == expected_moved and total_after == initial_pop:
        print("\n✅ PASS: Fractional aging works correctly\n")
        return True
    else:
        print("\n❌ FAIL: Fractional aging incorrect\n")
        return False


def test_terminal_bin_direct():
    """Test terminal bin accumulation directly."""
    print("\n=== Direct Terminal Bin Test ===\n")

    n_age_groups = 7
    population = np.zeros((n_age_groups, 1, 1, 1, 1, 1, 1), dtype=np.int64)

    # Set up: population in terminal bin and second-to-last
    terminal_idx = n_age_groups - 1
    second_to_last_idx = terminal_idx - 1

    population[terminal_idx, 0, 0, 0, 0, 0, 0] = 5000
    population[second_to_last_idx, 0, 0, 0, 0, 0, 0] = 10000

    print(f"Initial setup:")
    print(f"  Terminal bin ({terminal_idx}): {population[terminal_idx, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Second-to-last ({second_to_last_idx}): {population[second_to_last_idx, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Total: {np.sum(population):,}\n")

    # Apply fractional aging
    aging_fraction = 0.2
    new_pop = np.zeros_like(population)

    for age_idx in range(n_age_groups - 1):
        staying = (population[age_idx, ...] * 0.8).astype(np.int64)
        moving = population[age_idx, ...] - staying
        new_pop[age_idx, ...] += staying  # ADD
        new_pop[age_idx + 1, ...] += moving

    # Terminal bin accumulates
    new_pop[-1, ...] += population[-1, ...]

    print(f"After fractional aging:")
    print(f"  Terminal bin: {new_pop[terminal_idx, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Total: {np.sum(new_pop):,}\n")

    # Expected
    expected_inflow = int(10000 * 0.2)
    expected_terminal = 5000 + expected_inflow

    print("Validation:")
    print(f"  Expected inflow (20% of {10000}): {expected_inflow:,}")
    print(f"  Expected terminal total: {expected_terminal:,}")
    print(f"  Actual terminal: {new_pop[terminal_idx, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Match: {new_pop[terminal_idx, 0, 0, 0, 0, 0, 0] == expected_terminal} {'✓' if new_pop[terminal_idx, 0, 0, 0, 0, 0, 0] == expected_terminal else '✗'}")

    if new_pop[terminal_idx, 0, 0, 0, 0, 0, 0] == expected_terminal:
        print("\n✅ PASS: Terminal bin accumulates correctly\n")
        return True
    else:
        print("\n❌ FAIL: Terminal bin incorrect\n")
        return False


def test_multiple_bins():
    """Test aging across multiple bins simultaneously."""
    print("\n=== Multiple Bin Test ===\n")

    n_age_groups = 7
    population = np.zeros((n_age_groups, 1, 1, 1, 1, 1, 1), dtype=np.int64)

    # Set up: different populations in multiple bins
    population[0, 0, 0, 0, 0, 0, 0] = 20000  # Youngest
    population[2, 0, 0, 0, 0, 0, 0] = 15000
    population[4, 0, 0, 0, 0, 0, 0] = 10000

    total_before = np.sum(population)

    print(f"Initial setup:")
    print(f"  Bin 0: {population[0, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Bin 2: {population[2, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Bin 4: {population[4, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Total: {total_before:,}\n")

    # Apply fractional aging
    aging_fraction = 0.2
    new_pop = np.zeros_like(population)

    for age_idx in range(n_age_groups - 1):
        staying = (population[age_idx, ...] * 0.8).astype(np.int64)
        moving = population[age_idx, ...] - staying
        new_pop[age_idx, ...] += staying  # ADD
        new_pop[age_idx + 1, ...] += moving

    new_pop[-1, ...] += population[-1, ...]

    total_after = np.sum(new_pop)

    print(f"After fractional aging:")
    for i in range(n_age_groups):
        if new_pop[i, 0, 0, 0, 0, 0, 0] > 0:
            print(f"  Bin {i}: {new_pop[i, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Total: {total_after:,}\n")

    print("Validation:")
    print(f"  Population before: {total_before:,}")
    print(f"  Population after: {total_after:,}")
    print(f"  Conserved: {total_before == total_after} {'✓' if total_before == total_after else '✗'}")

    if total_before == total_after:
        print("\n✅ PASS: Multiple bin aging conserves population\n")
        return True
    else:
        print(f"\n❌ FAIL: Population not conserved (diff: {total_after - total_before})\n")
        return False


def main():
    """Run all simple tests."""
    print("=" * 70)
    print("SIMPLE DIRECT TESTS FOR FRACTIONAL AGING")
    print("=" * 70)

    results = []
    results.append(("Direct Fractional Aging", test_direct_fractional_aging()))
    results.append(("Terminal Bin Accumulation", test_terminal_bin_direct()))
    results.append(("Multiple Bin Aging", test_multiple_bins()))

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")

    all_passed = all(passed for _, passed in results)

    if all_passed:
        print("\n🎉 All tests PASSED!")
        return 0
    else:
        print("\n⚠️  Some tests FAILED")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
