#!/usr/bin/env python3
"""
Detailed debugging of childhood fractional aging.
"""

import numpy as np
from run_v3 import SimulationEngineV3

# Create simulation
sim = SimulationEngineV3('config_v6_rel_pop.json', seed=42)

childhood = sim.demography.childhood

print(f'\n{"="*70}')
print("DETAILED CHILDHOOD FRACTIONAL AGING DEBUG")
print(f'{"="*70}\n')

print(f'Childhood bins: {childhood.age_bins}')
print(f'Number of bins: {childhood.n_bins}\n')

print(f'Initial population by bin:')
total_before = 0
for i, bin_name in enumerate(childhood.age_bins):
    female = childhood.population[i, childhood.FEMALE]
    male = childhood.population[i, childhood.MALE]
    total = female + male
    print(f'  {bin_name}: F={female:,}, M={male:,}, Total={total:,}')
    total_before += total

print(f'\nTotal BEFORE aging: {total_before:,}\n')

# Manually trace through the algorithm
print(f'{"="*70}')
print("TRACING ALGORITHM STEP-BY-STEP")
print(f'{"="*70}\n')

aging_fraction = 0.2
new_pop = np.zeros_like(childhood.population)
deterministic = sim.deterministic

# Loop through bins 0 to n-2
print(f'Loop: range({childhood.n_bins} - 1) = range({childhood.n_bins - 1})\n')

for bin_idx in range(childhood.n_bins - 1):
    print(f'Processing bin {bin_idx} ({childhood.age_bins[bin_idx]}):')

    if deterministic:
        staying = (childhood.population[bin_idx, :] * 0.8).astype(np.int64)
        moving = childhood.population[bin_idx, :] - staying
    else:
        moving = sim.rng.binomial(
            childhood.population[bin_idx, :].astype(int),
            aging_fraction
        )
        staying = childhood.population[bin_idx, :] - moving

    print(f'  Original: F={childhood.population[bin_idx, childhood.FEMALE]:,}, M={childhood.population[bin_idx, childhood.MALE]:,}')
    print(f'  Staying (80%): F={staying[childhood.FEMALE]:,}, M={staying[childhood.MALE]:,}')
    print(f'  Moving (20%): F={moving[childhood.FEMALE]:,}, M={moving[childhood.MALE]:,}')

    new_pop[bin_idx, :] += staying
    new_pop[bin_idx + 1, :] += moving

    print(f'  new_pop[{bin_idx}] after += staying: F={new_pop[bin_idx, childhood.FEMALE]:,}, M={new_pop[bin_idx, childhood.MALE]:,}')
    print(f'  new_pop[{bin_idx + 1}] after += moving: F={new_pop[bin_idx + 1, childhood.FEMALE]:,}, M={new_pop[bin_idx + 1, childhood.MALE]:,}')
    print()

# Handle oldest bin specially
oldest_idx = childhood.n_bins - 1
print(f'Processing oldest bin {oldest_idx} ({childhood.age_bins[oldest_idx]}) AFTER loop:')
print(f'  Original: F={childhood.population[oldest_idx, childhood.FEMALE]:,}, M={childhood.population[oldest_idx, childhood.MALE]:,}')

if deterministic:
    staying_oldest = (childhood.population[oldest_idx, :] * 0.8).astype(np.int64)
else:
    moving_oldest = sim.rng.binomial(
        childhood.population[oldest_idx, :].astype(int),
        aging_fraction
    )
    staying_oldest = childhood.population[oldest_idx, :] - moving_oldest

print(f'  Staying (80%): F={staying_oldest[childhood.FEMALE]:,}, M={staying_oldest[childhood.MALE]:,}')
print(f'  new_pop[{oldest_idx}] BEFORE += staying: F={new_pop[oldest_idx, childhood.FEMALE]:,}, M={new_pop[oldest_idx, childhood.MALE]:,}')

new_pop[oldest_idx, :] += staying_oldest

print(f'  new_pop[{oldest_idx}] AFTER += staying: F={new_pop[oldest_idx, childhood.FEMALE]:,}, M={new_pop[oldest_idx, childhood.MALE]:,}')
print()

# Calculate totals
print(f'{"="*70}')
print(f'FINAL RESULT:')
print(f'{"="*70}\n')

total_after = 0
for i, bin_name in enumerate(childhood.age_bins):
    female = new_pop[i, childhood.FEMALE]
    male = new_pop[i, childhood.MALE]
    total = female + male
    print(f'  {bin_name}: F={female:,}, M={male:,}, Total={total:,}')
    total_after += total

print(f'\nTotal AFTER aging: {total_after:,}')
print(f'Total BEFORE aging: {total_before:,}')
print(f'Difference: {total_after - total_before:,}')

if total_after != total_before:
    print(f'\n❌ BUG CONFIRMED: Lost {total_before - total_after:,} people!')
else:
    print(f'\n✅ Population conserved')
