#!/usr/bin/env python3
"""
Diagnose mortality rate issue: Are survival values 5-year or annual?
"""

import json
import numpy as np

print("\n" + "="*70)
print("MORTALITY RATE DIAGNOSIS")
print("="*70 + "\n")

# Load config
with open('config_v6_rel_pop.json', 'r') as f:
    config = json.load(f)

survival_data = config['life_table']['survival_by_age_limit']['value']

print("Analysis of survival_by_age_limit values:\n")

# Netherlands crude death rate 1970: ~0.8% per year (https://www.mortality.org)
# Life expectancy 1970: ~73.5 years

print("Age   | Male   | Female | Male Annual | Female Annual | Male 5yr | Female 5yr")
print("Group | Surv   | Surv   | Death Rate  | Death Rate    | Interp   | Interp")
print("-" * 85)

for entry in survival_data[:10]:  # First 10 entries
    age_limit = entry['age_limit']
    male_surv = entry.get('male_survival', 1.0)
    female_surv = entry.get('female_survival', 1.0)

    # What the code currently uses (treats as annual)
    male_mort_current = 1 - male_surv
    female_mort_current = 1 - female_surv

    # If these are 5-year survival, convert to annual
    male_surv_annual = male_surv ** (1/5)
    female_surv_annual = female_surv ** (1/5)
    male_mort_annual = 1 - male_surv_annual
    female_mort_annual = 1 - female_surv_annual

    print(f"{age_limit:2d}    | {male_surv:.5f} | {female_surv:.5f} | "
          f"{100*male_mort_current:.3f}%      | {100*female_mort_current:.3f}%       | "
          f"{100*male_mort_annual:.3f}%   | {100*female_mort_annual:.3f}%")

print("\n" + "="*70)
print("INTERPRETATION")
print("="*70 + "\n")

# Calculate what crude death rate would be
male_20_current = 1 - survival_data[4]['male_survival']  # Age 20
male_20_annual = 1 - (survival_data[4]['male_survival'] ** (1/5))

print("Example: Males age 15-19")
print(f"  Config value: {survival_data[4]['male_survival']}")
print(f"\n  If treated as ANNUAL survival (current code):")
print(f"    Annual mortality = {100*male_20_current:.3f}%")
print(f"\n  If treated as 5-YEAR survival (correct):")
print(f"    Annual mortality = {100*male_20_annual:.3f}%")
print(f"\n  Ratio: {male_20_current / male_20_annual:.1f}× too high")

print("\n" + "="*70)
print("EXPECTED vs ACTUAL DEATH RATES")
print("="*70 + "\n")

# Netherlands 1970 data (approximate)
print("Netherlands 1970 (source: Human Mortality Database):")
print("  Crude death rate: ~0.8% per year")
print("  Life expectancy: ~73.5 years")
print("  Infant mortality: ~1.3% (first year of life)")

print("\nYour simulation output (from earlier test):")
print("  Year 1970: Population 12,216,783, BG Deaths: 1,204,984")
print(f"  Crude death rate: {100*1204984/12216783:.2f}% per year")

print("\n⚠️  DIAGNOSIS:")
crude_rate = 1204984/12216783
expected_rate = 0.008

if crude_rate > 0.05:
    print(f"  Death rate is {crude_rate/expected_rate:.1f}× higher than expected!")
    print(f"\n  ROOT CAUSE:")
    print(f"    survival_by_age_limit contains 5-year survival probabilities")
    print(f"    but code treats them as annual survival probabilities")
    print(f"\n  SOLUTION:")
    print(f"    Convert to annual survival: annual = (five_year)^(1/5)")
elif crude_rate > 0.02:
    print(f"  Death rate is {crude_rate/expected_rate:.1f}× higher than expected")
    print(f"  This may be due to incorrect survival probability interpretation")
else:
    print(f"  Death rate appears reasonable (within 2× of expected)")

print("\n" + "="*70)
print("RECOMMENDED FIX")
print("="*70 + "\n")

print("Convert 5-year survival to annual survival in config:\n")
print("Example Python code to convert:")
print("```python")
print("import json")
print()
print("with open('config_v6_rel_pop.json', 'r') as f:")
print("    config = json.load(f)")
print()
print("# Convert 5-year to annual survival")
print("for entry in config['life_table']['survival_by_age_limit']['value']:")
print("    entry['male_survival'] = entry['male_survival'] ** (1/5)")
print("    entry['female_survival'] = entry['female_survival'] ** (1/5)")
print()
print("with open('config_v6_rel_pop_fixed.json', 'w') as f:")
print("    json.dump(config, f, indent=2)")
print("```")
print()
print("This will reduce death rates by ~5× to realistic levels.")
print()
