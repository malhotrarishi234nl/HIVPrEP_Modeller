# PATCH V4.3: Sexual Debut Guardrails (No Behavior Change)

**Date:** February 9, 2026
**Version:** V4.3
**Status:** ✅ COMPLETE & VALIDATED
**Builds on:** V4.2 (Continuous + Flush on Aging-Out)

---

## Purpose

Add safety checks and observability to the sexual debut mechanism **without changing model behavior**. This patch improves debugging, validation, and prevents future bugs after refactoring.

---

## Implementation

### 1. Assert Correct Debut Bin (Safety Check)

**Location:** `demography.py` - `ChildhoodPool.flush_remaining_debut_eligible()`

**Purpose:** Prevent accidental flushing of wrong age group after future refactors.

**Implementation:**
```python
def flush_remaining_debut_eligible(self) -> Tuple[int, int]:
    oldest_bin_idx = self.n_bins - 1

    # GUARDRAIL: Assert we're flushing the correct debut-eligible bin
    if oldest_bin_idx >= len(self.age_bins):
        raise ValueError(
            f"Debut bin index {oldest_bin_idx} out of range for {len(self.age_bins)} age bins."
        )

    debut_bin_label = self.age_bins[oldest_bin_idx]
    expected_debut_bin = "10-14"

    if debut_bin_label != expected_debut_bin:
        raise ValueError(
            f"SAFETY CHECK FAILED: Attempting to flush bin '{debut_bin_label}' but expected "
            f"debut-eligible bin '{expected_debut_bin}'."
        )

    # Proceed with flush...
```

**What it catches:**
- Configuration errors (wrong age bins)
- Indexing bugs (wrong bin accessed)
- Refactoring errors (bin structure changed)

**When it triggers:**
- If someone accidentally changes `age_bins` configuration
- If bin indexing logic is broken during refactor
- If `n_bins` doesn't match `age_bins` length

### 2. Track Flush vs Continuous Separately (Observability)

**Locations:**
- `run_v3.py` - `_step_aging_and_debut()`: Returns `(continuous, flush, total)`
- `run_v3.py` - `record_outputs()`: Records separate columns
- `run_v3.py` - Outputs dictionary: Added `continuous_debuts` and `flush_debuts`

**Purpose:** Improve debugging and validation by tracking debut types separately.

**Changes:**

**Before (V4.2):**
```python
def _step_aging_and_debut(self, step: int) -> int:
    n_debuts = 0
    # ... continuous debut ...
    n_debuts = continuous_debuts
    # ... flush ...
    n_debuts += flush_debuts
    return n_debuts
```

**After (V4.3):**
```python
def _step_aging_and_debut(self, step: int) -> Tuple[int, int, int]:
    continuous_debuts = 0
    flush_debuts = 0
    # ... continuous debut ...
    continuous_debuts = ...
    # ... flush ...
    flush_debuts = ...
    total_debuts = continuous_debuts + flush_debuts
    return continuous_debuts, flush_debuts, total_debuts
```

**Output columns:**
- `continuous_debuts`: Per-timestep binomial debuts (every timestep > 0)
- `flush_debuts`: Aging-boundary flush debuts (only at 5-year boundaries > 0)
- `sexual_debuts`: Total debuts (continuous + flush) - **unchanged from V4.2**

---

## Validation Results

### Test Configuration
- **Duration:** 50 years (1970-2019)
- **Timestep:** 1 year
- **Seed:** 42

### Guardrail Checks

✅ **1. Bin assertion works:**
- No errors during simulation
- Correct bin verified at every flush
- Would fail loudly if configuration wrong

✅ **2. Separate tracking works:**
- `continuous_debuts` column present in outputs
- `flush_debuts` column present in outputs
- All rows: `sexual_debuts = continuous_debuts + flush_debuts`

✅ **3. Continuous debuts every timestep:**
- 50/50 timesteps have `continuous_debuts > 0`
- No timesteps skipped

✅ **4. Flush only at boundaries:**
- `flush_debuts > 0` at steps: [5, 10, 15, 20, 25, 30, 35, 40, 45]
- Exactly matches 5-year aging boundaries
- Zero at all other timesteps

✅ **5. No behavior change:**
- Total debuts match V4.2 exactly (same seed)
- Population trajectories unchanged
- HIV prevalence unchanged

### Sample Data

```
Step  Year  Continuous  Flush  Total
   0  1970      32,260      0  32,260  (continuous only)
   1  1971      25,799      0  25,799  (continuous only)
   5  1975      10,554 42,217  52,771  (continuous + flush at boundary)
   6  1976      32,203      0  32,203  (continuous only, post-boundary)
  10  1980      13,173 52,694  65,867  (continuous + flush at boundary)
```

**Key observations:**
- Continuous debuts every step (no zeros)
- Flush only at aging boundaries (steps 5, 10, 15, ...)
- Total = continuous + flush (always)

---

## Files Modified

### 1. `demography.py`
**Changes:**
- Added bin assertion in `flush_remaining_debut_eligible()` (12 lines)
- Validates `oldest_bin_idx` is correct
- Checks `age_bins[oldest_bin_idx] == "10-14"`

### 2. `run_v3.py`
**Changes:**
- Added `from typing import Tuple` import
- Modified `_step_aging_and_debut()` return type: `int` → `Tuple[int, int, int]`
- Modified `record_outputs()` signature: accepts separate debuts
- Added `continuous_debuts` and `flush_debuts` to outputs dictionary
- Updated call site in `step_simulation()` to unpack tuple

**Line count:** ~30 lines changed/added

---

## Benefits

### 1. Safety (Bin Assertion)
**Prevents:**
- Accidental flushing of wrong age group
- Silent bugs from configuration changes
- Indexing errors after refactoring

**Example scenario prevented:**
```python
# Someone accidentally changes config:
"age_bins": ["0-4", "5-9", "10-19"]  # Wrong! Merged last two bins

# Without guardrail: Silently flushes 10-19 bin (wrong behavior)
# With guardrail: Raises clear error immediately
```

### 2. Observability (Separate Tracking)
**Enables:**
- Debugging debut patterns
- Validating continuous vs flush proportions
- Spotting anomalies in debut flow
- Performance analysis (e.g., "how much is flush contributing?")

**Example use cases:**
```python
# Check if continuous debuts are working
assert df['continuous_debuts'].min() > 0, "Continuous debuts stopped!"

# Verify flush proportion is reasonable
flush_pct = df['flush_debuts'].sum() / df['sexual_debuts'].sum()
assert 0.25 < flush_pct < 0.40, f"Flush proportion {flush_pct:.1%} unusual"

# Identify aging boundaries programmatically
aging_steps = df[df['flush_debuts'] > 0]['step'].tolist()
```

### 3. Documentation (Self-Documenting Code)
**The separate columns document:**
- When flush occurs (non-zero flush_debuts)
- That continuous happens every step (always non-zero)
- The relative contribution of each mechanism

---

## Constraints Satisfied

✅ **Do NOT change debut timing or counts**
- Total debuts identical to V4.2 (validated with same seed)
- Timing unchanged (continuous every step, flush at boundaries)
- Population flows identical

✅ **Do NOT alter population flows**
- Same individuals debut at same times
- Same orientation/activity assignments
- Same entry to sexually active population

✅ **Guardrails only; behavior identical**
- No changes to debut probability formula
- No changes to flush logic
- No changes to bin shifting
- Only added: assertions and separate tracking

---

## Testing the Guardrails

### Test 1: Verify Bin Assertion Works

**Modify config to break assumption:**
```json
"age_bins": {"value": ["0-4", "5-9", "10-19"]}  // Wrong!
```

**Expected result:**
```
ValueError: SAFETY CHECK FAILED: Attempting to flush bin '10-19' but expected
debut-eligible bin '10-14'. This indicates a configuration or indexing error.
```

✅ **Guardrail catches error before wrong behavior occurs**

### Test 2: Verify Separate Tracking

**Check output CSV:**
```python
df = pd.read_csv('outputs/baseline_run_001.csv')
assert 'continuous_debuts' in df.columns
assert 'flush_debuts' in df.columns
assert (df['sexual_debuts'] == df['continuous_debuts'] + df['flush_debuts']).all()
```

✅ **Separate tracking verified**

---

## Migration Notes

**From V4.2 to V4.3:**

**Code changes:**
- `_step_aging_and_debut()` now returns tuple instead of int
- Call sites must unpack: `cont, flush, total = _step_aging_and_debut(step)`

**Output changes:**
- CSV files now include `continuous_debuts` and `flush_debuts` columns
- `sexual_debuts` column unchanged (backward compatible)
- Downstream code using only `sexual_debuts` works without changes

**No configuration changes needed** - drop-in upgrade.

---

## Future Extensibility

This patch enables future enhancements:

1. **Adaptive flush thresholds:**
   ```python
   if flush_debuts / continuous_debuts > 0.5:
       warn("Large flush proportion - consider smaller timestep")
   ```

2. **Alternative debut models:**
   - Could add `hazard_debuts` for alternative formulations
   - Separate tracking makes comparison easy

3. **Performance optimization:**
   - Profile which component (continuous vs flush) is expensive
   - Optimize based on data

4. **Validation tests:**
   - Unit tests can verify continuous > 0 always
   - Integration tests can check flush only at boundaries

---

## Conclusion

V4.3 successfully adds safety and observability guardrails to the sexual debut mechanism without changing any model behavior:

✅ **Safety:** Bin assertion prevents future bugs
✅ **Observability:** Separate tracking improves debugging
✅ **No behavior change:** Identical results to V4.2
✅ **Backward compatible:** Existing code works unchanged
✅ **Self-documenting:** Output columns explain mechanism
✅ **Future-proof:** Enables extensions and validation

The guardrails provide "defense in depth" against refactoring errors and make the codebase more maintainable.

---

**Patch Status: COMPLETE & PRODUCTION-READY**

**Migration:** Drop-in replacement for V4.2, no config changes needed.
