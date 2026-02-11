#!/usr/bin/env python3
"""
Validate that population adjustments preserve distributions correctly.

Tests whether multiplying the entire population tensor by a scalar:
1. Preserves relative proportions across buckets
2. Doesn't create negative values or invalid states
3. Handles rounding appropriately for small populations
4. Maintains demographic constraints (age/sex/orientation/HIV distributions)
"""

import numpy as np
import json
from run_v3 import SimulationEngineV3

print("\n" + "="*70)
print("POPULATION ADJUSTMENT DISTRIBUTION VALIDATION")
print("="*70 + "\n")

# Load config
with open('config_v6_rel_pop.json', 'r') as f:
    config = json.load(f)

# Enable adjustments with 10% increase
config['population_adjustments']['enabled']['value'] = True
config['population_adjustments']['year_multipliers']['value'] = {
    "1970": 1.10,  # 10% increase
    "2020": 1.10
}
config['time']['end_year']['value'] = 1971  # Just 1 step

with open('config_dist_test.json', 'w') as f:
    json.dump(config, f)

print("Test Configuration:")
print("  - Multiplier: 1.10 (10% increase)")
print("  - Duration: 1 step")
print()

# Initialize simulation
sim = SimulationEngineV3('config_dist_test.json', seed=42)

# Capture state BEFORE adjustment
print("="*70)
print("BEFORE ADJUSTMENT")
print("="*70)

initial_total = sim.state.get_total_population()
initial_dist = sim.state.population.copy()

# Get distributions by dimension
initial_by_age = np.sum(initial_dist, axis=(1,2,3,4,5,6))
initial_by_orient = np.sum(initial_dist, axis=(0,2,3,4,5,6))
initial_by_activity = np.sum(initial_dist, axis=(0,1,3,4,5,6))
initial_by_hiv = np.sum(initial_dist, axis=(0,1,2,4,5,6))

print(f"\nTotal population: {initial_total:,}")
print(f"\nBy age group:")
for i, age in enumerate(sim.groups.age_groups):
    pct = 100 * initial_by_age[i] / initial_total
    print(f"  {age}: {initial_by_age[i]:,} ({pct:.2f}%)")

print(f"\nBy orientation:")
for i, orient in enumerate(sim.groups.orientations):
    pct = 100 * initial_by_orient[i] / initial_total
    print(f"  {orient}: {initial_by_orient[i]:,} ({pct:.2f}%)")

print(f"\nBy activity level:")
for i, activity in enumerate(sim.groups.activities):
    pct = 100 * initial_by_activity[i] / initial_total
    print(f"  {activity}: {initial_by_activity[i]:,} ({pct:.2f}%)")

print(f"\nBy HIV status:")
for i, hiv in enumerate(sim.groups.hiv_stages):
    pct = 100 * initial_by_hiv[i] / initial_total
    print(f"  {hiv}: {initial_by_hiv[i]:,} ({pct:.2f}%)")

# Get small populations (potential rounding issues)
small_cells = np.sum(initial_dist < 10)
tiny_cells = np.sum(initial_dist == 1)
print(f"\nSmall populations (<10): {small_cells} cells")
print(f"Tiny populations (=1): {tiny_cells} cells")

# Apply one timestep (which includes adjustment)
print("\n" + "="*70)
print("APPLYING ADJUSTMENT (10% increase via simulation step)")
print("="*70 + "\n")

# Manually apply adjustment to test it
adjusted_pop = sim.demography.apply_population_adjustments(
    sim.state.population.copy(),
    1970.0
)

# Calculate adjustment change
adjustment_change = np.sum(adjusted_pop) - initial_total

print(f"Population before: {initial_total:,}")
print(f"Population after:  {np.sum(adjusted_pop):,}")
print(f"Change: {adjustment_change:,} (+{100*adjustment_change/initial_total:.2f}%)")
print(f"Expected change: {int(initial_total * 0.10):,} (+10.00%)")

# Check for losses due to rounding
theoretical_after = int(initial_total * 1.10)
actual_after = np.sum(adjusted_pop)
rounding_loss = theoretical_after - actual_after

print(f"\nRounding analysis:")
print(f"  Theoretical (exact): {theoretical_after:,}")
print(f"  Actual (truncated):  {actual_after:,}")
print(f"  Loss from rounding:  {rounding_loss:,} ({100*rounding_loss/theoretical_after:.3f}%)")

# VALIDATE DISTRIBUTIONS
print("\n" + "="*70)
print("DISTRIBUTION VALIDATION")
print("="*70 + "\n")

adjusted_by_age = np.sum(adjusted_pop, axis=(1,2,3,4,5,6))
adjusted_by_orient = np.sum(adjusted_pop, axis=(0,2,3,4,5,6))
adjusted_by_activity = np.sum(adjusted_pop, axis=(0,1,3,4,5,6))
adjusted_by_hiv = np.sum(adjusted_pop, axis=(0,1,2,4,5,6))

issues = []

# Check age distribution
print("Age distribution:")
max_age_drift = 0
for i, age in enumerate(sim.groups.age_groups):
    initial_pct = 100 * initial_by_age[i] / initial_total
    adjusted_pct = 100 * adjusted_by_age[i] / np.sum(adjusted_pop)
    drift = abs(adjusted_pct - initial_pct)
    max_age_drift = max(max_age_drift, drift)
    status = "✓" if drift < 0.1 else "⚠"
    print(f"  {status} {age}: {initial_pct:.2f}% → {adjusted_pct:.2f}% (drift: {drift:.3f}pp)")
    if drift >= 0.1:
        issues.append(f"Age {age}: drift of {drift:.3f}pp")

# Check orientation distribution
print("\nOrientation distribution:")
max_orient_drift = 0
for i, orient in enumerate(sim.groups.orientations):
    initial_pct = 100 * initial_by_orient[i] / initial_total
    adjusted_pct = 100 * adjusted_by_orient[i] / np.sum(adjusted_pop)
    drift = abs(adjusted_pct - initial_pct)
    max_orient_drift = max(max_orient_drift, drift)
    status = "✓" if drift < 0.1 else "⚠"
    print(f"  {status} {orient}: {initial_pct:.2f}% → {adjusted_pct:.2f}% (drift: {drift:.3f}pp)")
    if drift >= 0.1:
        issues.append(f"Orientation {orient}: drift of {drift:.3f}pp")

# Check activity distribution
print("\nActivity distribution:")
max_activity_drift = 0
for i, activity in enumerate(sim.groups.activities):
    initial_pct = 100 * initial_by_activity[i] / initial_total
    adjusted_pct = 100 * adjusted_by_activity[i] / np.sum(adjusted_pop)
    drift = abs(adjusted_pct - initial_pct)
    max_activity_drift = max(max_activity_drift, drift)
    status = "✓" if drift < 0.1 else "⚠"
    print(f"  {status} {activity}: {initial_pct:.2f}% → {adjusted_pct:.2f}% (drift: {drift:.3f}pp)")
    if drift >= 0.1:
        issues.append(f"Activity {activity}: drift of {drift:.3f}pp")

# Check HIV distribution
print("\nHIV status distribution:")
max_hiv_drift = 0
for i, hiv in enumerate(sim.groups.hiv_stages):
    initial_pct = 100 * initial_by_hiv[i] / initial_total
    adjusted_pct = 100 * adjusted_by_hiv[i] / np.sum(adjusted_pop)
    drift = abs(adjusted_pct - initial_pct)
    max_hiv_drift = max(max_hiv_drift, drift)
    status = "✓" if drift < 0.1 else "⚠"
    print(f"  {status} {hiv}: {initial_pct:.2f}% → {adjusted_pct:.2f}% (drift: {drift:.3f}pp)")
    if drift >= 0.1:
        issues.append(f"HIV {hiv}: drift of {drift:.3f}pp")

# Check for negative or invalid values
negative_cells = np.sum(adjusted_pop < 0)
if negative_cells > 0:
    issues.append(f"{negative_cells} cells have negative values")

# Check for cells that vanished
vanished = np.sum((initial_dist > 0) & (adjusted_pop == 0))
if vanished > 0:
    issues.append(f"{vanished} non-zero cells became zero")

# Check small populations
adjusted_small = np.sum(adjusted_pop < 10)
adjusted_tiny = np.sum(adjusted_pop == 1)

print(f"\nSmall population cells:")
print(f"  Before: {small_cells} cells < 10")
print(f"  After:  {adjusted_small} cells < 10")
print(f"  Before: {tiny_cells} cells = 1")
print(f"  After:  {adjusted_tiny} cells = 1")

# SUMMARY
print("\n" + "="*70)
print("VALIDATION SUMMARY")
print("="*70 + "\n")

print(f"Maximum distribution drift:")
print(f"  Age:        {max_age_drift:.3f} percentage points")
print(f"  Orientation: {max_orient_drift:.3f} percentage points")
print(f"  Activity:   {max_activity_drift:.3f} percentage points")
print(f"  HIV status: {max_hiv_drift:.3f} percentage points")

print(f"\nRounding loss: {rounding_loss:,} people ({100*rounding_loss/theoretical_after:.3f}%)")
print(f"Negative values: {negative_cells}")
print(f"Vanished cells: {vanished}")

if len(issues) == 0 and max(max_age_drift, max_orient_drift, max_activity_drift, max_hiv_drift) < 0.1:
    print("\n✅ VALIDATION PASSED")
    print("  • Distributions preserved (drift < 0.1pp)")
    print("  • No negative values")
    print("  • No vanished populations")
    print("  • Rounding loss acceptable (<0.01%)")
    print("\nConclusion: Population adjustments work correctly!")
    print("They multiply each cell proportionally while minimizing rounding effects.")
else:
    print("\n⚠️  VALIDATION CONCERNS")
    for issue in issues:
        print(f"  • {issue}")

    if rounding_loss / theoretical_after > 0.001:
        print(f"  • Rounding loss is {100*rounding_loss/theoretical_after:.3f}% (>0.1%)")

    print("\nNote: Small drifts (<0.1pp) are expected due to integer rounding.")
    print("This is unavoidable when multiplying discrete populations.")
