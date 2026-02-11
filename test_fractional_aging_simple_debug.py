#!/usr/bin/env python3
"""
Simple debug test - just test fractional aging without mortality.
"""

import numpy as np
from run_v3 import SimulationEngineV3

# Create simulation
sim = SimulationEngineV3('config_v6_rel_pop.json', seed=42)

print(f'\n{"="*70}')
print("FRACTIONAL AGING CONSERVATION TEST")
print(f'{"="*70}\n')

# Test adult fractional aging
print("Testing ADULT fractional aging...")
pre_adult = sim.state.population.sum()
print(f'  Population BEFORE: {pre_adult:,}')

sim._apply_fractional_aging_adults()

post_adult = sim.state.population.sum()
print(f'  Population AFTER:  {post_adult:,}')
print(f'  Difference:        {post_adult - pre_adult:,}')

if post_adult != pre_adult:
    print(f'  ❌ FAIL: Lost {pre_adult - post_adult:,} people!')
    import sys
    sys.exit(1)
else:
    print(f'  ✅ PASS: Conserved\n')

# Test childhood fractional aging
print("Testing CHILDHOOD fractional aging...")
pre_child = sim.demography.childhood.get_total()
print(f'  Population BEFORE: {pre_child:,}')

sim.demography.childhood.apply_fractional_aging(sim.rng, sim.deterministic)

post_child = sim.demography.childhood.get_total()
print(f'  Population AFTER:  {post_child:,}')
print(f'  Difference:        {post_child - pre_child:,}')

if post_child != pre_child:
    print(f'  ❌ FAIL: Lost {pre_child - post_child:,} people!')
    import sys
    sys.exit(1)
else:
    print(f'  ✅ PASS: Conserved\n')

print(f'✅ All tests passed - fractional aging conserves population')
