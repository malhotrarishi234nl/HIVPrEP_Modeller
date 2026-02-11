# Population Adjustment Accounting Bug - RESOLVED

**Date:** 2026-02-11
**Reported by:** User
**Fixed by:** Claude
**Status:** ✅ RESOLVED AND VERIFIED

---

## User Report

> "HI Claude-- there is an issue with the population multiplier causing an accounting issue when it is turned on. Please analyze the codebase, validate the issue, and then fix."

---

## Issue Analysis

### Problem Identified

When population adjustments (exogenous multipliers for calibration) were enabled, the simulation failed with a mass balance assertion error:

```
GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED
Expected: 11,746,395 people
Actual:   12,216,783 people
Difference: 470,388 people
```

### Root Cause

Population adjustments multiply the sexually active population by year-specific multipliers (e.g., 1.05 = 5% increase). This creates or destroys people, but the change was not being tracked in the mass balance equation.

**The bug:**
```python
# Step 1: Capture previous total
prev_total = 12,713,814

# Step 2: Apply 5% adjustment to 9.4M active population
# Creates 470k people - BUT NOT TRACKED!
population = population * 1.05  # +470k people

# Step 3: Process births/deaths
births = 253,165
deaths = 1,220,584

# Step 4: Validate
expected = prev_total + births - deaths  # 11,746,395 (missing +470k!)
actual = 12,216,783  # includes the +470k
# FAIL: difference = 470,388
```

---

## Solution Implemented

### Code Changes

**File:** `run_v3.py`

**1. Track adjustment change:**
```python
adjustment_change = 0
if pop_adj_enabled:
    before = population.sum()
    population = apply_adjustments(population)
    after = population.sum()
    adjustment_change = after - before  # Track the change!
```

**2. Include in mass balance formula:**
```python
expected = prev_total + births - deaths + adjustment_change
```

**3. Show in error messages:**
```python
"  + Adjustment change: {adjustment_change:,}\n"
```

### Mass Balance Equation

**Before (BROKEN):**
```
expected = prev_total + births - deaths
```

**After (FIXED):**
```
expected = prev_total + births - deaths + adjustment_change
```

Where `adjustment_change` can be:
- Positive (population growth from multiplier > 1.0)
- Negative (population decline from multiplier < 1.0)
- Zero (no adjustments or multiplier = 1.0)

---

## Verification

### Test Results

✅ **All test scenarios passed:**

| Scenario | Configuration | Result |
|----------|--------------|--------|
| No adjustments | `enabled=false` | ✅ PASS (adjustment_change=0) |
| +5% increase | `multiplier=1.05` | ✅ PASS (+470k tracked) |
| -10% decrease | `multiplier=0.90` | ✅ PASS (-941k tracked) |
| Time-varying | `1970:1.0 → 1990:1.2 → 2020:0.95` | ✅ PASS (varies by year) |
| **Full 50-step simulation** | Time-varying multipliers | ✅ PASS (all steps) |

### Validation Output

**Before fix:**
```
❌ FAIL: Accounting error at step 0
Mass balance assertion failed
```

**After fix:**
```
✅ PASS: 50 timesteps completed
Mass balance maintained throughout
No accounting errors
```

---

## Impact

### Before Fix
- ❌ Population adjustments unusable (assertion failure)
- ❌ Calibration feature broken
- ❌ Simulation halted immediately

### After Fix
- ✅ Population adjustments work correctly
- ✅ Calibration feature functional
- ✅ Mass balance maintained
- ✅ Changes tracked transparently

---

## Files Modified

| File | Changes | Lines |
|------|---------|-------|
| `run_v3.py` | Track adjustment_change | ~10 |
| `run_v3.py` | Pass to validation | ~1 |
| `run_v3.py` | Update validation method | ~5 |
| `run_v3.py` | Update formula | ~1 |
| `run_v3.py` | Update error message | ~3 |
| **Total** | | **~30 lines** |

---

## Documentation Created

1. **POPULATION_ADJUSTMENT_ACCOUNTING_FIX.md** (comprehensive)
   - Detailed bug analysis
   - Complete code changes
   - All test results
   - Scientific justification
   - FAQ and troubleshooting

2. **POPULATION_ADJUSTMENT_FIX_SUMMARY.md** (brief)
   - Quick reference
   - Key points only
   - Links to full documentation

3. **POPULATION_ADJUSTMENT_BUG_RESOLVED.md** (this file)
   - User-facing summary
   - Issue resolution record

---

## How to Use Population Adjustments

### Configuration

Edit `config_v6_rel_pop.json`:

```json
"population_adjustments": {
  "enabled": {
    "value": true
  },
  "year_multipliers": {
    "value": {
      "1970": 1.00,
      "1980": 1.05,
      "1990": 1.10,
      "2000": 1.08,
      "2010": 1.03,
      "2020": 1.00
    }
  }
}
```

**Notes:**
- Multipliers interpolated linearly between years
- Applied only to sexually active population
- Use for calibration to match empirical data
- Now properly tracked in mass balance equation ✅

### Running Simulation

```bash
python3 run_v3.py config_v6_rel_pop.json
```

The simulation will:
1. Apply adjustments at start of each timestep
2. Track the population change
3. Include it in mass balance validation
4. Complete successfully without assertion errors

---

## Backward Compatibility

✅ **Fully backward compatible**

- Existing simulations without adjustments: No change
- `adjustment_change` defaults to 0 when disabled
- Mass balance formula works with or without adjustments

---

## Testing Performed

### Unit Tests
- ✅ No adjustments (baseline)
- ✅ Positive adjustment (+5%)
- ✅ Negative adjustment (-10%)
- ✅ Time-varying adjustments

### Integration Tests
- ✅ 5-step simulation (quick test)
- ✅ 50-step simulation (full run)
- ✅ With fractional aging
- ✅ With discrete aging

### Regression Tests
- ✅ Existing tests still pass
- ✅ No changes to simulation without adjustments
- ✅ Mass balance for PrEP/ART unaffected

---

## Lessons Learned

### Why the Bug Occurred
1. Population adjustments added without updating mass balance equation
2. Missing test coverage for this feature
3. Implicit assumption that only births/deaths change population

### Prevention Going Forward
- ✅ Always update mass balance when adding population flows
- ✅ Test all features, especially calibration tools
- ✅ Explicit accounting for ALL population changes
- ✅ Document all modifications

---

## Summary

| Aspect | Status |
|--------|--------|
| **Issue** | Population adjustments caused accounting errors |
| **Root cause** | Changes not tracked in mass balance equation |
| **Fix** | Track `adjustment_change` and include in formula |
| **Testing** | 4 scenarios + full 50-step simulation ✅ |
| **Documentation** | Comprehensive + summary docs created ✅ |
| **Backward compatibility** | Fully compatible ✅ |
| **Status** | **✅ RESOLVED** |

---

## Next Steps

**For users:**
1. Update to latest code with this fix
2. Enable population adjustments in config if needed
3. Run simulations normally - adjustments now work correctly

**For developers:**
1. Review documentation for implementation details
2. Use as reference for similar accounting fixes
3. Maintain test coverage for all features

---

**Resolution Date:** 2026-02-11
**Verification:** Complete
**Ready for Production:** Yes ✅

---

**Related Documentation:**
- `documentation/POPULATION_ADJUSTMENT_ACCOUNTING_FIX.md` - Full technical details
- `documentation/POPULATION_ADJUSTMENT_FIX_SUMMARY.md` - Quick reference
- `documentation/FRACTIONAL_AGING_BUG_FIX.md` - Similar accounting fix
- `documentation/MASS_BALANCE_ASSERTION.md` - Mass balance validation system
