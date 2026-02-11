# SCOPE: Fractional Aging Implementation

**Date:** 2026-02-10
**Type:** Core demographic mechanism change
**Complexity:** HIGH
**Risk:** MEDIUM-HIGH (affects core population dynamics)

---

## Executive Summary

**What:** Replace discrete 5-year cohort aging with fractional annual aging (20% transfer per year)

**Why:** Eliminate artificial mortality spikes caused by synchronized cohort aging

**How:** Transfer 1/5 of each age bin to the next bin every year (instead of 100% every 5 years)

**Effort:** 8-12 hours (implementation + testing + validation)

**Risk Mitigation:** Comprehensive validation tests, backward comparison

---

## Problem: Current Discrete Aging

### Current Implementation

**Aging logic** (run_v3.py, lines 402-424):
```python
if step > 0 and step % steps_per_5years == 0:  # Every 5 years
    # 100% of population shifts up one bin
    new_pop = np.zeros_like(self.state.population)
    for age in range(self.groups.n_age_groups - 1):
        new_pop[age + 1, :, :, :, :, :, :] = self.state.population[age, :, :, :, :, :, :]
    new_pop[-1, :, :, :, :, :, :] += self.state.population[-1, :, :, :, :, :, :]
    self.state.population = new_pop
```

### The Problem: Mortality Spikes

**Timeline example** (population in 20-24 bin):
```
Year 0: 100,000 in 20-24 (mortality rate: 0.001 = 0.1%)
Year 1: 99,900 in 20-24 (100 deaths)
Year 2: 99,800 in 20-24 (100 deaths)
Year 3: 99,700 in 20-24 (100 deaths)
Year 4: 99,600 in 20-24 (100 deaths)
Year 5: AGING EVENT
        - 99,500 move from 20-24 → 25-29
        - New cohort (94,000) enters 20-24 from 15-19

Year 6: 94,000 in 25-29 (mortality rate: 0.002 = 0.2%)
        Deaths: 188 ← SPIKE! (was 100/year, now 188)
```

**Cause:** Entire cohort synchronized - everyone ages at once, suddenly experiencing higher mortality rate

**Result:**
- Sawtooth pattern in annual deaths
- Every 5th year shows spike
- Unrealistic for population-level modeling

---

## Solution: Fractional Aging

### Core Algorithm

**Every annual timestep:**
```
For each age bin a (except terminal):
  - Transfer 20% of population to bin a+1
  - Retain 80% in bin a

For terminal bin:
  - Receive 20% from previous bin
  - Retain 100% of existing population
```

### Example: Smooth Flow

**Timeline with fractional aging:**
```
Year 0: 100,000 in 20-24 (mortality: 0.001)
        Deaths: 100

Year 1: Population after mortality: 99,900
        Aging: 80% stay (79,920), 20% move to 25-29 (19,980)
        Remaining in 20-24: 79,920
        Inflow from 15-19: ~20,000
        New 20-24 population: ~99,920
        Deaths next year: ~100 (smooth!)

Year 2: Same pattern
        Deaths: ~100 (smooth!)

...no spikes, continuous flow
```

**Key insight:** Population constantly refreshes with younger (lower mortality) cohorts, smoothing the mortality curve

---

## Implementation Design

### 1. Adult Population Aging

**Current (lines 415-421):**
```python
# Discrete shift every 5 years
if step % steps_per_5years == 0:
    new_pop = np.zeros_like(self.state.population)
    for age in range(self.groups.n_age_groups - 1):
        new_pop[age + 1, ...] = self.state.population[age, ...]  # 100% shift
    new_pop[-1, ...] += self.state.population[-1, ...]
    self.state.population = new_pop
```

**Proposed (EVERY year):**
```python
# Fractional aging every timestep
aging_fraction = 0.2  # 1/5 = 20% per year

new_pop = np.zeros_like(self.state.population)

# For each age bin (except terminal)
for age_idx in range(self.groups.n_age_groups - 1):
    if self.deterministic:
        # Deterministic: exact 20%
        staying = (self.state.population[age_idx, ...] * 0.8).astype(np.int64)
        moving = self.state.population[age_idx, ...] - staying
    else:
        # Stochastic: binomial draw
        moving = self.rng.binomial(
            self.state.population[age_idx, ...].astype(int),
            aging_fraction
        )
        staying = self.state.population[age_idx, ...] - moving

    new_pop[age_idx, ...] = staying
    new_pop[age_idx + 1, ...] += moving

# Terminal age bin: receives inflow, no outflow
new_pop[-1, ...] += self.state.population[-1, ...] * 0.8

self.state.population = new_pop
```

**Complexity:** Medium
**Lines of code:** ~30
**Risk:** Medium (must handle all compartments correctly)

---

### 2. Childhood Pool Aging

**Current** (demography.py, `ChildhoodPool.age_and_debut()`):
```python
def age_and_debut(self):
    """Shift bins every 5 years: 0-4 → 5-9 → 10-14 → debut"""
    debuting_females = self.population[-1, self.FEMALE]
    debuting_males = self.population[-1, self.MALE]

    # Shift bins
    for i in range(self.n_bins - 1, 0, -1):
        self.population[i, :] = self.population[i - 1, :]
    self.population[0, :] = 0  # Cleared for new births

    return debuting_females, debuting_males
```

**Proposed (EVERY year):**
```python
def fractional_aging(self, rng, deterministic):
    """
    Transfer 20% of each childhood bin to next bin every year.

    Returns debuting children from oldest bin (10-14 → 15-19)
    """
    aging_fraction = 0.2
    new_pop = np.zeros_like(self.population)

    for bin_idx in range(self.n_bins - 1):
        if deterministic:
            staying = (self.population[bin_idx, :] * 0.8).astype(np.int64)
            moving = self.population[bin_idx, :] - staying
        else:
            moving = rng.binomial(
                self.population[bin_idx, :].astype(int),
                aging_fraction
            )
            staying = self.population[bin_idx, :] - moving

        new_pop[bin_idx, :] = staying
        new_pop[bin_idx + 1, :] += moving

    # Oldest bin (10-14): those aging out go to sexual debut
    if deterministic:
        debuting = (self.population[-1, :] * aging_fraction).astype(np.int64)
        staying = self.population[-1, :] - debuting
    else:
        debuting = rng.binomial(
            self.population[-1, :].astype(int),
            aging_fraction
        )
        staying = self.population[-1, :] - debuting

    new_pop[-1, :] = staying

    self.population = new_pop

    return debuting[self.FEMALE], debuting[self.MALE]
```

**Complexity:** Medium
**Lines of code:** ~40
**Risk:** Low-Medium

---

### 3. Sexual Debut Logic

**Current:**
- Continuous debut every timestep (uses probability)
- Flush-on-aging every 5 years (ensures complete debut before age-out)

**Proposed:**
- **Keep continuous debut** (unchanged, works fine)
- **Remove flush-on-aging** (no longer needed - fractional aging replaces it)
- Fractional aging from 10-14 → sexual debut happens naturally via 20% transfer

**Rationale:**
With fractional aging, 20% of 10-14 bin ages to 15-19 every year. Combined with continuous debut probability applied every timestep, we achieve smooth debut flow without needing a flush mechanism.

**Complexity:** Low (mostly removal of code)
**Lines of code:** -20 (deletion)
**Risk:** Low

---

### 4. Order of Operations

**CRITICAL:** Must preserve this order each timestep:

```python
def _step(self):
    # 1. Background mortality (age-specific, BEFORE aging)
    bg_deaths = self.demography.calculate_background_mortality(...)
    self.state.population -= bg_deaths

    # 2. HIV mortality (if applicable, BEFORE aging)
    hiv_deaths = self._step_hiv_mortality()

    # 3. FRACTIONAL AGING (after mortality, every timestep)
    self._step_fractional_aging()

    # 4. Births and childhood
    n_births = self.demography.calculate_births(...)
    self.demography.add_births_to_childhood(n_births)

    # 5. Sexual debut (continuous, every timestep)
    debuting_f, debuting_m = self.demography.childhood.process_continuous_debut(...)
    self.demography.process_sexual_debut(debuting_f, debuting_m, ...)

    # 6. Other transitions (transmission, progression, etc.)
    ...
```

**Key:** Mortality THEN aging (never the reverse)

---

## Validation Tests (Required)

### Test 1: Population Conservation

**Code:**
```python
def test_population_conservation():
    """Verify aging doesn't create or destroy population."""
    pop_before = np.sum(population)

    # Apply fractional aging
    population_after_aging = fractional_aging(population)

    pop_after = np.sum(population_after_aging)

    # Should be exactly equal (accounting for stochastic rounding)
    assert abs(pop_before - pop_after) <= n_age_bins  # Allow ±1 per bin for rounding
```

**Pass criteria:** Total population unchanged by aging (net of births/deaths)

---

### Test 2: Mortality Consistency

**Code:**
```python
def test_mortality_consistency():
    """
    Over 5 years, cumulative deaths should match discrete aging model.
    Test with: fertility=0, HIV=off
    """
    # Run discrete aging model (5-year shifts)
    discrete_deaths = run_model(aging="discrete", years=5)

    # Run fractional aging model (20% per year)
    fractional_deaths = run_model(aging="fractional", years=5)

    # 5-year cumulative deaths should be statistically indistinguishable
    assert abs(sum(discrete_deaths) - sum(fractional_deaths)) / sum(discrete_deaths) < 0.01  # <1% difference
```

**Pass criteria:** Cumulative 5-year deaths within 1% of discrete model

---

### Test 3: Spike Elimination

**Code:**
```python
def test_spike_elimination():
    """Annual deaths should be smooth, not spiked."""
    # Run fractional aging for 20 years
    annual_deaths = run_model(aging="fractional", years=20)

    # Calculate coefficient of variation (CV = std/mean)
    cv = np.std(annual_deaths) / np.mean(annual_deaths)

    # Should be much lower than discrete aging
    assert cv < 0.05  # Less than 5% variation year-to-year
```

**Pass criteria:** Annual death counts have low variance (smooth curve)

---

### Test 4: Age Distribution Stability

**Code:**
```python
def test_age_distribution_stability():
    """Under constant fertility/mortality, age pyramid should stabilize."""
    # Run for 100 years to reach equilibrium
    age_dist_over_time = run_model(years=100, track_age_dist=True)

    # Check for oscillations (compare year 95 vs 100)
    age_dist_95 = age_dist_over_time[95]
    age_dist_100 = age_dist_over_time[100]

    # Should be nearly identical (stable)
    relative_diff = abs(age_dist_100 - age_dist_95) / age_dist_95
    assert np.all(relative_diff < 0.01)  # <1% change over 5 years at equilibrium
```

**Pass criteria:** Age distribution converges smoothly, no 5-year oscillations

---

### Test 5: Epidemiological Invariance

**Code:**
```python
def test_epidemiological_invariance():
    """HIV dynamics should be unaffected (within stochastic tolerance)."""
    # Run 50 seeds with discrete aging
    discrete_prevalence = [run_hiv_model(aging="discrete", seed=s) for s in range(50)]

    # Run 50 seeds with fractional aging
    fractional_prevalence = [run_hiv_model(aging="fractional", seed=s) for s in range(50)]

    # Final prevalence should be statistically indistinguishable
    p_value = ttest_ind(discrete_prevalence, fractional_prevalence).pvalue
    assert p_value > 0.05  # No significant difference
```

**Pass criteria:** HIV prevalence/incidence within stochastic tolerance (p > 0.05)

---

## Files to Modify

| File | Component | Changes | LOC | Risk |
|------|-----------|---------|-----|------|
| `run_v3.py` | Adult aging | Replace discrete shift with fractional | ~40 | HIGH |
| `run_v3.py` | Debut logic | Remove flush-on-aging | -20 | MEDIUM |
| `demography.py` | Childhood aging | Add fractional_aging method | ~50 | MEDIUM |
| `demography.py` | Continuous debut | Update to work with fractional | ~10 | LOW |
| **Total** | | **Net: +80 LOC** | | |

**Test files:**
| File | Purpose | LOC |
|------|---------|-----|
| `test_fractional_aging.py` | All 5 validation tests | ~200 |
| `test_fractional_aging.json` | Test configuration | ~50 |

**Documentation:**
| File | Purpose | LOC |
|------|---------|-----|
| `PATCH_FRACTIONAL_AGING.md` | Complete documentation | ~500 |
| `README_MODEL_GUIDE.md` | Update Section 10 (Demography) | ~50 |

---

## Effort Estimate

| Phase | Task | Time |
|-------|------|------|
| **1. Implementation** | Adult fractional aging | 2 hours |
| | Childhood fractional aging | 1.5 hours |
| | Remove flush-on-aging | 0.5 hours |
| | Update continuous debut | 0.5 hours |
| **2. Testing** | Write 5 validation tests | 2 hours |
| | Debug and fix issues | 1.5 hours |
| | Run comparison analysis | 1 hour |
| **3. Documentation** | Patch documentation | 1.5 hours |
| | Update README | 0.5 hours |
| **TOTAL** | | **10-11 hours** |

---

## Risk Analysis

### HIGH RISK: Adult Population Aging
- **Risk:** Incorrect transfer logic breaks age structure
- **Mitigation:**
  - Test with simple 2-bin model first
  - Verify mass conservation at every step
  - Compare with analytical solution (stable age distribution)

### MEDIUM RISK: Interaction with Continuous Debut
- **Risk:** Removing flush might leave children "stuck" in 10-14 bin
- **Mitigation:**
  - Monitor 10-14 bin population over time
  - Verify near-complete debut by age 15
  - Test with different debut rates

### MEDIUM RISK: Stochastic vs Deterministic
- **Risk:** Binomial draws might create unexpected variance
- **Mitigation:**
  - Implement both deterministic and stochastic modes
  - Test deterministic first (easier to debug)
  - Use deterministic for validation tests

### LOW RISK: Backward Compatibility
- **Risk:** This is a breaking change (results will differ)
- **Mitigation:**
  - Document differences clearly
  - Provide comparison analysis showing improvements
  - Scientific justification for change

---

## Benefits

✅ **Eliminates mortality spikes** - Smooth annual death curves
✅ **More realistic** - Continuous aging, not synchronized cohorts
✅ **Numerically stable** - No sawtooth artifacts
✅ **Preserves total deaths** - Long-run mortality unchanged
✅ **Reviewer-acceptable** - Standard demographic modeling practice

---

## Scientific Framing (for thesis/papers)

> "We implement fractional cohort aging to eliminate artificial mortality oscillations arising from discrete 5-year age transitions. Under fractional aging, 20% of each age cohort transitions annually to the subsequent age bin, approximating continuous age progression while maintaining compatibility with discrete life-table mortality rates. This approach preserves long-run demographic equilibrium while eliminating numerical artifacts that arise from synchronized cohort aging."

**References to cite:**
- Caswell (2001) - Matrix Population Models
- Preston et al. (2001) - Demography: Measuring and Modeling Population Processes
- Keyfitz & Caswell (2005) - Applied Mathematical Demography

---

## Recommendation

**Proceed?** ✅ YES - Well-justified improvement

**Approach:**
1. Implement adult fractional aging first (test thoroughly)
2. Add childhood fractional aging
3. Update debut logic (remove flush)
4. Run all 5 validation tests
5. Compare with discrete model
6. Document thoroughly

**Timeline:** 10-11 hours for complete implementation + validation + documentation

---

**Ready to implement when approved.**
