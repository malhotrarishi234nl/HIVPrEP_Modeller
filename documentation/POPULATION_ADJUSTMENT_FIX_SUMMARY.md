# Population Adjustment Accounting Fix - Summary

**Date:** 2026-02-11
**Status:** ✅ FIXED AND TESTED

---

## Issue

Population adjustments (exogenous multipliers for calibration) were creating or destroying people without being tracked in the mass balance equation, causing assertion failures.

---

## Example Error (Before Fix)

```
GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED
Step: 0, Year: 1970.0

Expected: 11,746,395 people
Actual:   12,216,783 people
Difference: 470,388 people

Components:
  Previous total:     12,713,814
  + Births:           253,165
  - HIV deaths:       186
  - Background deaths: 1,204,984
  - Childhood deaths: 15,414
  = Expected:         11,746,395
```

The 470k difference = 5% adjustment applied to 9.4M sexually active population.

---

## Root Cause

Population adjustments multiply sexually active population by year-specific multipliers (e.g., 1.05 for 5% growth), creating or destroying people. This change wasn't tracked in the mass balance equation:

**OLD (Broken):**
```
expected = prev_total + births - deaths
```

**NEW (Fixed):**
```
expected = prev_total + births - deaths + adjustment_change
```

---

## The Fix

**File:** `run_v3.py`

1. **Track adjustment change** (lines 291-300):
   ```python
   adjustment_change = 0
   if pop_adj_enabled:
       before = population.sum()
       population = apply_adjustments(population)
       after = population.sum()
       adjustment_change = after - before
   ```

2. **Include in mass balance** (line 847):
   ```python
   expected_total = prev_total + births - deaths + adjustment_change
   ```

3. **Show in error messages** (line 868):
   ```python
   "  + Adjustment change: {adjustment_change:,}\n"
   ```

---

## Verification

✅ **All scenarios tested and passed:**
- No adjustments (baseline): `adjustment_change = 0`
- +5% increase: `adjustment_change = +470k`
- -10% decrease: `adjustment_change = -941k`
- Time-varying: `adjustment_change` varies by year

---

## Impact

**Before:**
- ❌ Population adjustments caused accounting errors
- ❌ Simulation halted with assertion failure

**After:**
- ✅ Population adjustments work correctly
- ✅ Mass balance maintained
- ✅ Changes tracked transparently

---

## Configuration

Enable in `config_v6_rel_pop.json`:

```json
"population_adjustments": {
  "enabled": {"value": true},
  "year_multipliers": {
    "value": {
      "1970": 1.00,
      "1990": 1.10,
      "2020": 1.05
    }
  }
}
```

Multipliers interpolated between years, applied only to sexually active population.

---

## Files Modified

- `run_v3.py`: ~30 lines changed
  - Track adjustment_change
  - Update mass balance formula
  - Update error messages

---

**For full details, see:** `POPULATION_ADJUSTMENT_ACCOUNTING_FIX.md`

**Related fixes:**
- `FRACTIONAL_AGING_BUG_FIX.md` - Similar accounting fix for fractional aging
- `MASS_BALANCE_ASSERTION.md` - PrEP mass balance validation
