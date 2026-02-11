# Validation Patches - Morning Implementation

Three comprehensive validation patches implemented to ensure model correctness and prevent silent errors.

---

## PATCH 1: Transmission Sanity Checks

### Goal
Add lightweight validation that transmission is applied correctly (no double prevalence, no timestep double-scaling).

### Implementation

**Location:** `transmission_corrected.py`

**Function:** `test_transmission_correctness(fail_fast=True)`

**Tests:**
1. **Double partner prevalence → infections ~double (NOT ~quadruple)**
   - Verifies prevalence is NOT applied twice (prevalence² error)
   - Tests at 10% and 20% prevalence, expects ratio ~2.0
   - ✓ PASS: Observed ratio 2.01

2. **Double contact rate → infections ~double**
   - Verifies linear scaling with contact rate
   - Tests at 5,000 and 10,000 contacts, expects ratio ~2.0
   - ✓ PASS: Observed ratio 2.02

3. **Zero prevalence → zero infections**
   - Verifies no infections when no HIV+ partners exist
   - ✓ PASS: Zero infections with 0% prevalence

4. **Prevalence = 1.0 reduces to Binomial(n_contacts, p_trans)**
   - Verifies correct limiting behavior
   - ✓ PASS: Mean infections 2.36, expected 2.50

### Usage

Run standalone diagnostics:
```bash
python transmission_corrected.py
```

Run with fail-fast mode (default):
```python
from transmission_corrected import test_transmission_correctness
test_transmission_correctness(fail_fast=True)  # Raises AssertionError on first failure
```

### Test Results
```
================================================================================
✓ ALL TRANSMISSION SANITY CHECKS PASSED

Transmission is correctly applied:
  - No prevalence² error (linear in prevalence)
  - No double-scaling (linear in contact rate)
  - Zero prevalence gives zero infections
  - Prevalence=1 reduces to expected formula
================================================================================
```

### Error Behavior
If a test fails with `fail_fast=True`, raises `AssertionError` with diagnostic message:
```
AssertionError: Transmission scaling violation: Double prevalence gave 3.85x
infections (expected ~2.0x). This suggests prevalence² error.
```

---

## PATCH 2: Global Population Mass-Balance Assertion

### Goal
Ensure no silent creation/loss of people due to ordering/transition bugs.

### Implementation

**Location:** `run_v3.py`

**Method:** `SimulationEngineV3.validate_global_population_balance()`

**Formula:**
```
next_total_population = current_total_population
                        + births
                        - background_deaths
                        - HIV_deaths
                        - childhood_deaths
```

**When Run:**
- After each timestep in `step_simulation()`
- Before recording outputs

**Integration Points:**
1. Line 268-270: Capture `prev_total` at start of timestep
2. Line 334-336: Validate mass balance after all flows

### Test Results
Ran 50-timestep simulation with no assertion errors:
- ✓ Population accounting correct at every timestep
- ✓ No silent population creation/loss detected

### Error Behavior
If mass balance violated (diff > 0.5), stops simulation with detailed diagnostics:
```
======================================================================
GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED
======================================================================
Step: 25
Year: 1995.0

Population accounting error detected!

Expected: 2,150,000 people
Actual:   2,150,500 people
Difference: 500 people

Components:
  Previous total:     2,100,000
  + Births:           75,000
  - HIV deaths:       15,000
  - Background deaths: 8,000
  - Childhood deaths: 2,000
  = Expected:         2,150,000

Current breakdown:
  Sexually active:    1,950,000
  Childhood pool:     200,500
  = Actual total:     2,150,500

======================================================================
This indicates a model accounting error.
Check for silent population creation/loss in transitions.
======================================================================
```

### Constraint
Does not "fix" differences by renormalizing. **Fails fast** to alert immediately.

---

## PATCH 3: ART State Mass-Balance Assertion

### Goal
Prevent silent ART miscounting that would bias transmission/mortality effects.

### Implementation

**Locations:**
- `run_v3.py`: `validate_art_mass_balance()` method
- `output_writer.py`: ART columns in state counts, output validation

**Formula (at row grain):**
```
population = pop_art_off + pop_art_on
```

**Grain:** age × orientation × activity × HIV stage

**When Run:**
1. **During simulation:** After each timestep (line 197-198 in `run_v3.py`)
2. **Before output:** Before writing state counts (lines 323-359 in `output_writer.py`)

### Output Columns Added

State counts file now includes ART columns:
```csv
step,year,age_group,orientation,activity,hiv_stage,population,pop_prep_none,pop_prep_oral,pop_prep_lenacapavir,pop_art_off,pop_art_on
49,2019.0,15-19,Female,Low,Susceptible,24704,24704,0,0,24704,0
49,2019.0,15-19,Female,Low,Chronic,6,6,0,0,4,2
```

### Test Results

**Timestep Validation:**
- ✓ All 50 timesteps validated
- ✓ No ART accounting errors detected

**Output Validation:**
- ✓ 158 rows validated at output time
- ✓ All rows show art_diff=0 (perfect mass balance)

Sample verification:
```
Row 1: pop=24704  art_sum=24704  art_diff=0
Row 2: pop=4      art_sum=4      art_diff=0
Row 3: pop=6      art_sum=6      art_diff=0
Row 4: pop=15621  art_sum=15621  art_diff=0
```

### Error Behavior

**During Simulation:**
If violated (diff > 0.5), stops with state-specific diagnostics:
```
======================================================================
ART MASS-BALANCE ASSERTION FAILED (Step 30, Year 2000.0)
======================================================================
ART accounting error detected during simulation!

State: 25-29, Female, Medium, Chronic
  Total population:     1,500
  Sum across ART:       1,485
  Difference:           15

ART breakdown:
  Off ART: 1,200
  On ART:  285

======================================================================
This indicates a model accounting error in ART transitions.
Check _step_art_transitions() and related logic.
======================================================================
```

**Before Output:**
If violated when writing state counts:
```
======================================================================
ART MASS-BALANCE ASSERTION FAILED (Step 49, Year 2019.0)
======================================================================
ART populations do NOT sum to total population!

Requirement: population = pop_art_off + pop_art_on

Found 3 violation(s):

  State: 30-34, MSM, High, Chronic
    population:     850
    pop_art_off:    600
    pop_art_on:     248
    Sum:            848
    Difference:     2

======================================================================
This indicates a model accounting error. DO NOT PROCEED.
Check ART transition logic and population tensor operations.
======================================================================
```

### Constraint
Output/validation only. **Does not modify ART transition logic.**

---

## Summary of Validations

| Patch | Type | When Checked | Fail Mode |
|-------|------|--------------|-----------|
| **PATCH 1** | Transmission scaling | On demand (diagnostic) | Fail-fast with ratio details |
| **PATCH 2** | Global population | After each timestep | Fail-fast with flow components |
| **PATCH 3 (timestep)** | ART accounting | After each timestep | Fail-fast with state details |
| **PATCH 3 (output)** | ART accounting | Before file write | Fail-fast with row violations |

All validations use **fail-fast** approach: stop immediately on error with detailed diagnostics.

---

## Integration Test Results

**Test Configuration:** `test_mass_balance.json`
- 50 timesteps (1970-2019)
- Childhood pool enabled
- Fertility and life tables enabled
- State counts output enabled

**Results:**
```
✓ PATCH 1: All 4 transmission tests passed
✓ PATCH 2: 50 timesteps, no global population errors
✓ PATCH 3 (timestep): 50 timesteps, no ART accounting errors
✓ PATCH 3 (output): 158 state count rows, perfect mass balance

Final state counts file:
  - 158 rows (all non-zero states)
  - 11 columns (including pop_art_off, pop_art_on)
  - All mass balances verified (prep_diff=0, art_diff=0)
```

**Simulation completed successfully:**
- Total new infections: 674,457
- Total HIV deaths: 390,477
- Total background deaths: 211,428
- Final prevalence: 12.15%
- No assertion errors

---

## Files Modified

1. **transmission_corrected.py**
   - Enhanced `test_transmission_correctness()` with 4 comprehensive tests
   - Added contact rate scaling test (NEW)
   - Added fail-fast mode with detailed error messages

2. **run_v3.py**
   - Added `validate_global_population_balance()` method
   - Added `validate_art_mass_balance()` method
   - Modified `step_simulation()` to capture prev_total and call validation
   - Modified `run()` to call ART validation after each timestep
   - Modified state counts extraction to include ART status

3. **output_writer.py**
   - Modified `write_state_counts()` signature to accept `art_status` parameter
   - Added `pop_art_off` and `pop_art_on` columns to output
   - Added ART mass-balance assertion before file write

---

## Usage Notes

### Running Transmission Diagnostics
```bash
# Standalone test
python transmission_corrected.py

# From Python
from transmission_corrected import test_transmission_correctness
test_transmission_correctness(fail_fast=True)
```

### Normal Simulation
All validations run automatically:
```bash
python run_v3.py config.json
```

Validations performed:
1. Global population balance: After each timestep
2. PrEP mass balance: After each timestep
3. ART mass balance: After each timestep
4. PrEP mass balance: Before state counts output
5. ART mass balance: Before state counts output

### If Validation Fails
1. **DO NOT IGNORE** - Assertion indicates real accounting error
2. Review the error message for:
   - Which timestep/year failed
   - Which state slice has error (if applicable)
   - Expected vs actual values
   - Component breakdown
3. Check the indicated transition logic for bugs
4. **DO NOT** try to "fix" by disabling assertions

---

## Performance Impact

**Negligible:** All validations use simple numpy operations
- Global population: 1 subtraction per timestep
- PrEP/ART balance: Loop over state slices (158 iterations max)
- Total overhead: <1% of simulation time

**Worth it:** Catches bugs immediately rather than producing silently wrong results.
