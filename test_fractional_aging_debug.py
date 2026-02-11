#!/usr/bin/env python3
"""
Debug test to find where population is being lost in fractional aging.
"""

import numpy as np
from run_v3 import SimulationEngineV3
import sys

# Create simulation
sim = SimulationEngineV3('config_v6_rel_pop.json', seed=42)

print(f'\n{"="*70}')
print("FRACTIONAL AGING DEBUG TEST")
print(f'{"="*70}\n')

print(f'Initial state:')
print(f'  Adult population: {sim.state.population.sum():,}')
print(f'  Childhood population: {sim.demography.childhood.get_total():,}')
print(f'  Total: {sim.state.population.sum() + sim.demography.childhood.get_total():,}\n')

# Save initial state
initial_adult = sim.state.population.sum()
initial_child = sim.demography.childhood.get_total()

# Apply mortality (this is what happens in step_simulation)
print("Applying mortality...")
bg_deaths_adult = sim.demography.calculate_background_mortality(
    sim.state.population, sim.groups, sim.time
)
bg_deaths_child = sim.demography.calculate_childhood_mortality(sim.time)

print(f'  Adult deaths: {bg_deaths_adult:,}')
print(f'  Child deaths: {bg_deaths_child:,}')

sim.state.population -= bg_deaths_adult
sim.demography.childhood.population -= bg_deaths_child

adult_after_mort = sim.state.population.sum()
child_after_mort = sim.demography.childhood.get_total()

print(f'  Adult after mortality: {adult_after_mort:,}')
print(f'  Child after mortality: {child_after_mort:,}')
print(f'  Total after mortality: {adult_after_mort + child_after_mort:,}\n')

# Now apply fractional aging
print("Applying fractional aging to adults...")
print(f'  Population BEFORE aging: {sim.state.population.sum():,}')

# Save pre-aging state for verification
pre_aging = sim.state.population.sum()

# Apply adult fractional aging
sim._apply_fractional_aging_adults()

post_aging_adult = sim.state.population.sum()

print(f'  Population AFTER aging: {post_aging_adult:,}')
print(f'  Difference: {post_aging_adult - pre_aging:,}')

if post_aging_adult != pre_aging:
    print(f'  ❌ ERROR: Adult aging lost {pre_aging - post_aging_adult:,} people!')
else:
    print(f'  ✅ OK: Adult aging conserved population')

# Apply childhood fractional aging
print(f'\nApplying fractional aging to childhood...')
print(f'  Population BEFORE aging: {sim.demography.childhood.get_total():,}')

pre_aging_child = sim.demography.childhood.get_total()

sim.demography.childhood.apply_fractional_aging(sim.rng, sim.deterministic)

post_aging_child = sim.demography.childhood.get_total()

print(f'  Population AFTER aging: {post_aging_child:,}')
print(f'  Difference: {post_aging_child - pre_aging_child:,}')

if post_aging_child != pre_aging_child:
    print(f'  ❌ ERROR: Childhood aging lost {pre_aging_child - post_aging_child:,} people!')
else:
    print(f'  ✅ OK: Childhood aging conserved population')

# Total check
print(f'\nFinal verification:')
print(f'  Total before aging: {pre_aging + pre_aging_child:,}')
print(f'  Total after aging: {post_aging_adult + post_aging_child:,}')
total_lost = (pre_aging + pre_aging_child) - (post_aging_adult + post_aging_child)
print(f'  Total lost in aging: {total_lost:,}')

if total_lost > 0:
    print(f'\n❌ CRITICAL BUG: Fractional aging is losing {total_lost:,} people!')
    sys.exit(1)
else:
    print(f'\n✅ SUCCESS: Fractional aging conserves population')
    sys.exit(0)
