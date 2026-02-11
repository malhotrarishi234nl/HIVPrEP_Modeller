#!/usr/bin/env python3
"""
Comprehensive test of population adjustment accounting fix.

Tests multiple scenarios:
1. No adjustments (baseline)
2. Positive adjustments (5% growth)
3. Negative adjustments (10% decline)
4. Time-varying adjustments
"""

import json
from run_v3 import SimulationEngineV3

print("\n" + "="*70)
print("COMPREHENSIVE POPULATION ADJUSTMENT ACCOUNTING TEST")
print("="*70 + "\n")

# Load base config
with open('config_v6_rel_pop.json', 'r') as f:
    base_config = json.load(f)

# Test scenarios
scenarios = [
    {
        "name": "No adjustments (baseline)",
        "enabled": False,
        "multipliers": {"1970": 1.0, "2020": 1.0},
        "expected": "No adjustment_change, mass balance should pass"
    },
    {
        "name": "5% population increase",
        "enabled": True,
        "multipliers": {"1970": 1.05, "2020": 1.05},
        "expected": "Positive adjustment_change tracked, mass balance should pass"
    },
    {
        "name": "10% population decrease",
        "enabled": True,
        "multipliers": {"1970": 0.90, "2020": 0.90},
        "expected": "Negative adjustment_change tracked, mass balance should pass"
    },
    {
        "name": "Time-varying (growth then decline)",
        "enabled": True,
        "multipliers": {"1970": 1.0, "1990": 1.20, "2020": 0.95},
        "expected": "Varying adjustment tracked over time, mass balance should pass"
    }
]

results = []

for i, scenario in enumerate(scenarios):
    print(f"Test {i+1}: {scenario['name']}")
    print(f"  Config: enabled={scenario['enabled']}, multipliers={scenario['multipliers']}")
    print(f"  Expected: {scenario['expected']}")

    # Create config for this scenario
    config = json.loads(json.dumps(base_config))  # Deep copy
    config['population_adjustments']['enabled']['value'] = scenario['enabled']
    config['population_adjustments']['year_multipliers']['value'] = scenario['multipliers']

    # Limit to 5 steps for faster testing (end_year = start_year + 5)
    config['time']['end_year']['value'] = 1975

    config_file = f"config_test_adj_{i}.json"
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

    try:
        sim = SimulationEngineV3(config_file, seed=42)
        sim.run(run_number=1)
        print(f"  ✅ PASS - Mass balance maintained for all 5 steps\n")
        results.append(("PASS", scenario['name']))

    except AssertionError as e:
        print(f"  ❌ FAIL - Mass balance error:")
        # Print just the first few lines of error
        error_lines = str(e).split('\n')[:10]
        for line in error_lines:
            print(f"    {line}")
        print(f"  ...\n")
        results.append(("FAIL", scenario['name']))

    except Exception as e:
        print(f"  ❌ ERROR - Unexpected error: {e}\n")
        results.append(("ERROR", scenario['name']))

print("="*70)
print("SUMMARY")
print("="*70)

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
errors = sum(1 for r in results if r[0] == "ERROR")

for status, name in results:
    symbol = "✅" if status == "PASS" else "❌"
    print(f"  {symbol} {status}: {name}")

print(f"\nTotal: {passed}/{len(results)} passed")

if passed == len(results):
    print("\n🎉 ALL TESTS PASSED! Population adjustment accounting is working correctly.\n")
else:
    print(f"\n⚠️  {failed + errors} test(s) failed. Review errors above.\n")
