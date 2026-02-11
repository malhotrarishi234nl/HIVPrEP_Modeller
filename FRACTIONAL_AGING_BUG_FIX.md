# Fractional Aging Bug Fix

**Date:** 2026-02-11
**Bug:** Account errors - population loss in fractional aging
**Status:** ✅ FIXED AND VERIFIED

---

## Summary

Fixed critical accounting bug where ~230,000 people (7% of childhood population) were being lost every timestep due to fractional aging not properly handling children aging out of the 10-14 bin.

---

## The Bug

### Symptoms

When running the simulation with fractional aging, mass-balance validation failed:

```
======================================================================
GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED
======================================================================
Step: 0
Year: 1970.0

Expected: 11,328,667 people
Actual:   11,145,757 people
Difference: 182,910 people LOST
======================================================================
```

### Root Cause

**File:** `demography.py` - `ChildhoodPool.apply_fractional_aging()`
**Issue:** The 20% of children aging out of the 10-14 bin were being LOST instead of entering sexual debut

**How it happened:**
1. Childhood fractional aging calculated 20% aging out of 10-14 bin
2. These children were removed from the childhood pool
3. BUT they were never added to the sexually active population
4. Result: ~230,000 people disappearing every timestep

### Code Analysis

**Before (BROKEN):**

```python
def apply_fractional_aging(self, rng, deterministic: bool):
    """Apply fractional aging to childhood bins."""
    aging_fraction = 0.2
    new_pop = np.zeros_like(self.population)

    # ... process bins 0-4 and 5-9 ...

    # Oldest bin (10-14): 20% ages out
    if deterministic:
        staying_oldest = (self.population[-1, :] * 0.8).astype(np.int64)
    else:
        moving_oldest = rng.binomial(...)
        staying_oldest = self.population[-1, :] - moving_oldest

    new_pop[-1, :] += staying_oldest
    # PROBLEM: The 20% aging out just disappears!

    self.population = new_pop
    # No return value - aging-out population is LOST
```

**The problem:**
- Childhood population before: 3,304,320
- 20% of 10-14 bin ages out: ~229,703
- Childhood population after: 3,074,617
- Those 229,703 people were never added to adult population!

---

## The Fix

### Changes Made

**1. Modified `demography.py` - `ChildhoodPool.apply_fractional_aging()`**

Now returns the count of children aging out:

```python
def apply_fractional_aging(self, rng, deterministic: bool) -> Tuple[int, int]:
    """
    Apply fractional aging to childhood bins.

    Returns:
        (females_aging_out, males_aging_out) from 10-14 bin who need sexual debut
    """
    aging_fraction = 0.2
    new_pop = np.zeros_like(self.population)

    # ... process bins 0-4 and 5-9 ...

    # Oldest bin (10-14): 20% ages out to sexual debut
    if deterministic:
        staying_oldest = (self.population[-1, :] * 0.8).astype(np.int64)
        aging_out = self.population[-1, :] - staying_oldest  # ← CAPTURE THIS!
    else:
        aging_out = rng.binomial(...)
        staying_oldest = self.population[-1, :] - aging_out

    new_pop[-1, :] += staying_oldest
    self.population = new_pop

    # Return counts aging out for sexual debut
    females_aging_out = int(aging_out[self.FEMALE])
    males_aging_out = int(aging_out[self.MALE])

    return females_aging_out, males_aging_out  # ← NEW!
```

**2. Modified `run_v3.py` - `_step_aging_and_debut()`**

Now receives and processes the aging-out population:

```python
def _step_aging_and_debut(self, step: int) -> Tuple[int, int, int]:
    """Age populations and process sexual debut."""
    continuous_debuts = 0

    # 1. Continuous sexual debut (from 10-14 bin, probabilistic)
    debuting_females, debuting_males = self.demography.childhood.process_continuous_debut(...)
    if debuting_females > 0 or debuting_males > 0:
        self.state.population, continuous_debuts = self.demography.process_sexual_debut(...)

    # 2. Fractional aging
    self._apply_fractional_aging_adults()

    # CRITICAL FIX: Receive aging-out population from childhood aging
    aging_out_females, aging_out_males = self.demography.childhood.apply_fractional_aging(
        self.rng, self.deterministic
    )

    # Process sexual debut for children aging out of 10-14
    aging_debuts = 0
    if aging_out_females > 0 or aging_out_males > 0:
        self.state.population, aging_debuts = self.demography.process_sexual_debut(
            aging_out_females, aging_out_males,
            self.state.population,
            self.rng, self.deterministic
        )

    # Total debuts = continuous + aging-out
    total_debuts = continuous_debuts + aging_debuts
    return continuous_debuts, aging_debuts, total_debuts
```

---

## Verification

### Test Results

**Before fix:**
```
Step 0: ❌ FAILED
Expected: 11,328,667
Actual:   11,145,757
Lost:     182,910 people
```

**After fix:**
```
Step 0: ✅ PASS
Step 1: ✅ PASS
Step 2: ✅ PASS
...
Step 9: ✅ PASS

✅ SUCCESS: 10 steps completed without accounting errors!
```

### Population Flows Now Correct

**Childhood → Adult transition:**
- Continuous debut: ~some debuting probabilistically from 10-14
- Aging-out: 20% of 10-14 bin aging out naturally
- **Both flows** now properly enter sexually active population

**Conservation verified:**
```
Total population = Adult + Childhood
Births added ✓
Deaths subtracted ✓
Aging-out properly transferred ✓
No population created or destroyed ✓
```

---

## Files Modified

| File | Lines Changed | Type |
|------|---------------|------|
| `demography.py` | Lines 275-325 (~51 lines) | MODIFIED |
| `run_v3.py` | Lines 394-403 (~14 lines) | MODIFIED |
| **Total** | **~65 lines** | |

---

## Impact

### What Changed

**Before:**
- ❌ Fractional aging lost ~230k people/step
- ❌ Mass balance checks failed
- ❌ Model unusable

**After:**
- ✅ Fractional aging conserves population
- ✅ Mass balance checks pass
- ✅ Model runs correctly

### Behavior

**Sexual debut now has TWO sources:**
1. **Continuous debut:** Probabilistic debut from 10-14 bin (existing mechanism)
2. **Aging-out debut:** 20% of 10-14 bin aging out annually (NEW - was being lost!)

**Total debut flow:** Continuous + Aging-out

This is the correct behavior - children exit the 10-14 bin through both probabilistic debut AND natural aging.

---

## Lessons Learned

### Why This Bug Happened

1. **Incomplete design:** Initial fractional aging implementation had a comment saying "ages out to debut" but no code to actually do it
2. **Missing return value:** `apply_fractional_aging()` didn't return the aging-out population
3. **Silent failure:** Population just disappeared without immediate error (only caught by mass-balance check)

### Prevention

- ✅ Always use mass-balance validation checks
- ✅ Explicit return values for population flows
- ✅ Test conservation at every step
- ✅ Detailed debugging when population doesn't match expected

---

## Documentation Updates

Updated files:
- **PATCH_FRACTIONAL_AGING.md** - Added note about aging-out population return value
- **FRACTIONAL_AGING_BUG_FIX.md** - This comprehensive bug fix documentation

---

## Summary

**Bug:** Childhood fractional aging lost 20% of 10-14 bin (~230k people) every step

**Fix:** Modified `apply_fractional_aging()` to return aging-out population, process as sexual debuts

**Result:** Population conservation restored, mass-balance checks pass, model runs correctly

**Status:** ✅ FIXED, TESTED, AND VERIFIED

---

**Fixed by:** Claude
**Date:** 2026-02-11
**Tested:** 10 simulation steps, all pass mass-balance checks
