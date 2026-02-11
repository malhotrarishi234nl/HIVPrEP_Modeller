# PrEP Output Granularity Fix

**Date:** February 10, 2026  
**Status:** ✅ FIXED AND VALIDATED

---

## Problem

The original `prep_status` column was an aggregated string (e.g., `"None:24714"`) that:
- Mixed aggregation levels
- Repeated across multiple HIV stage rows
- Was unsuitable for analysis or publication
- Made validation impossible

**Example of the problem:**
```csv
hiv_stage,population,prep_status
Susceptible,64558,"None:64607"  ← Aggregated across HIV stages!
Chronic,46,"None:64607"          ← Same value repeated!
AIDS,3,"None:64607"              ← Misleading!
```

---

## Solution

Replaced the single `prep_status` column with **three explicit numeric columns**:
- `pop_prep_none`
- `pop_prep_oral`
- `pop_prep_lenacapavir`

Each column contains the population count at the **same grain** as the row:
- Age group
- Orientation
- Activity level
- **HIV stage** ← Key: NOT aggregated!

---

## Implementation

### Changes Made

**1. Updated `output_writer.py` (lines 205-270):**

```python
# OLD: Aggregated string
record['prep_status'] = ','.join(prep_counts) if prep_counts else 'None'

# NEW: Three explicit columns at correct grain
prep_slice = prep_status[age_idx, orient_idx, activity_idx, hiv_idx, :]
record['pop_prep_none'] = int(prep_slice[0])
record['pop_prep_oral'] = int(prep_slice[oral_idx]) if oral_idx >= 0 else 0
record['pop_prep_lenacapavir'] = int(prep_slice[len_idx]) if len_idx >= 0 else 0
```

**2. Updated `run_v3.py` (lines 215-217):**

```python
# OLD: Summed over HIV dimension (wrong grain)
prep_population = np.sum(self.state.population, axis=(3, 4, 5))  # (age, orient, activity, prep)

# NEW: Keep HIV dimension (correct grain)
prep_population = np.sum(self.state.population, axis=(4, 5))  # (age, orient, activity, hiv, prep)
```

---

## Validation Results ✅

### Test 1: Column Presence
```
✅ All three PrEP columns present:
   - pop_prep_none
   - pop_prep_oral
   - pop_prep_lenacapavir
```

### Test 2: Row-Level Consistency
```
✅ VALIDATION PASSED: For all 158 rows
   pop_prep_none + pop_prep_oral + pop_prep_lenacapavir = population
   Maximum difference: 0
```

### Test 3: Correct Grain (NOT Aggregated)
```
✅ PrEP values VARY by HIV stage

Sample: Female, 15-19, Low activity:
  hiv_stage    population  pop_prep_none  pop_prep_oral  pop_prep_lenacapavir
  Susceptible      64558          64558              0                     0
  Chronic             46             46              0                     0
  AIDS                 3              3              0                     0
```

Each HIV stage has its own PrEP breakdown - **no aggregation**!

---

## Output Format

### Before Fix:
```csv
age_group,orientation,activity,hiv_stage,population,prep_status
15-19,Female,Low,Susceptible,64558,"None:64607"
15-19,Female,Low,Chronic,46,"None:64607"
15-19,Female,Low,AIDS,3,"None:64607"
```
❌ Aggregated string, repeated values, wrong grain

### After Fix:
```csv
age_group,orientation,activity,hiv_stage,population,pop_prep_none,pop_prep_oral,pop_prep_lenacapavir
15-19,Female,Low,Susceptible,64558,64558,0,0
15-19,Female,Low,Chronic,46,46,0,0
15-19,Female,Low,AIDS,3,3,0,0
```
✅ Three numeric columns, correct grain, internally consistent

---

## Benefits

1. **Internally Consistent**
   - Each row: `pop_prep_none + pop_prep_oral + pop_prep_lenacapavir = population`
   - No ambiguity, no repeated aggregates

2. **Analysis Ready**
   - Direct input for cost calculations
   - Direct input for QALY calculations
   - No string parsing needed

3. **Transparent**
   - PrEP dynamics clearly visible
   - HIV stage-specific PrEP coverage
   - Suitable for publication

4. **Validation Friendly**
   - Easy to verify totals
   - Easy to check consistency
   - Easy for reviewers to audit

---

## Usage

No config changes needed! The fix applies automatically when:
```json
{
  "output": {
    "write_state_counts": {"value": true}
  }
}
```

Output file: `<run_tag>_state_counts_run_<number>.csv`

---

## Technical Details

### Row Grain Definition
Each output row represents a unique combination of:
- Age group (e.g., "15-19")
- Sexual orientation (e.g., "Female")
- Activity level (e.g., "Low")
- **HIV stage (e.g., "Susceptible")**

The three PrEP columns align **exactly** to this grain.

### Population Tensor Collapse
- **Main population**: Sum over (diagnosis, ART, PrEP) → shape (age, orient, activity, hiv)
- **PrEP columns**: Sum over (diagnosis, ART) only → shape (age, orient, activity, hiv, prep)

This ensures PrEP counts are at the same grain as the main population.

---

## Constraints Met

✅ No stringified summaries  
✅ No repeated aggregates across rows  
✅ No changes to model logic  
✅ Output-only reshaping fix  
✅ Validation check 1: Column sums = population  
✅ Validation check 2: Totals match global coverage  
✅ Validation check 3: Zero where no uptake  

---

## Files Modified

1. **output_writer.py**
   - Method: `write_state_counts()` (lines 238-260)
   - Changed from single string column to three numeric columns
   - Extracts PrEP at correct grain (includes HIV dimension)

2. **run_v3.py**
   - Method: `run()` (lines 215-217)
   - Changed prep_population tensor collapse
   - Now keeps HIV dimension for correct grain

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Column type** | String | Three numeric columns |
| **Grain** | Aggregated across HIV | Same as row (includes HIV) |
| **Validation** | Impossible | ✅ Passes all checks |
| **Analysis** | Requires parsing | ✅ Direct use |
| **Publication** | Unsuitable | ✅ Publication ready |

---

**Fix Complete: February 10, 2026**  
**All Validation Checks Passed ✅**
