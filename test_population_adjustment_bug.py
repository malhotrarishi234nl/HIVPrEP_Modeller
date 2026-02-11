#!/usr/bin/env python3
"""
Test to reproduce population adjustment accounting error.

The bug: When population adjustments are enabled, they create/destroy people
but this isn't tracked in the mass balance validation, causing assertion failures.
"""

import json
from run_v3 import SimulationEngineV3

# Load config and enable population adjustments
with open('config_v6_rel_pop.json', 'r') as f:
    config = json.load(f)

# Enable population adjustments with a 5% increase in 1970
config['population_adjustments']['enabled']['value'] = True
config['population_adjustments']['year_multipliers']['value'] = {
    "1970": 1.05,  # 5% increase at start
    "2020": 1.05
}

# Save modified config
with open('config_test_pop_adj.json', 'w') as f:
    json.dump(config, f, indent=2)

print("\n" + "="*70)
print("TESTING POPULATION ADJUSTMENT ACCOUNTING")
print("="*70 + "\n")

print("Configuration:")
print("  - Population adjustments: ENABLED")
print("  - Multiplier at 1970: 1.05 (5% increase)")
print("  - This should create ~566k people at step 0")
print("\nExpected behavior WITHOUT fix:")
print("  ❌ Mass balance assertion should FAIL")
print("  ❌ Error: Population created but not accounted for")
print("\nExpected behavior WITH fix:")
print("  ✅ Mass balance should PASS")
print("  ✅ Adjustment tracked in equation\n")

try:
    sim = SimulationEngineV3('config_test_pop_adj.json', seed=42)

    # Run just 1 step to trigger the error
    print("Running simulation for 1 step...")
    sim.run(run_number=1)

    print("\n✅ SUCCESS: Simulation completed without accounting errors!")
    print("This means the fix is working correctly.\n")

except AssertionError as e:
    print("\n❌ ACCOUNTING ERROR DETECTED!")
    print("\nError details:")
    print(str(e))
    print("\nThis confirms the bug exists and needs to be fixed.\n")

except Exception as e:
    print(f"\n❌ Unexpected error: {e}\n")
