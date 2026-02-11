# REFERENCE: Discrete 5-Year Aging (Original Implementation)

**Date Documented:** 2026-02-10
**Status:** REPLACED by fractional aging (see PATCH_FRACTIONAL_AGING.md)
**Purpose:** Historical reference for discrete cohort aging implementation

---

## Overview

This document preserves the original discrete 5-year aging implementation for reference and comparison purposes.

### Key Characteristics

- **Aging frequency:** Every 5 simulation years
- **Transfer amount:** 100% of each age bin shifts to next bin
- **Result:** Synchronized cohort aging ("everyone ages at once")
- **Side effect:** Creates mortality spikes every 5 years due to cohort synchronization

---

## Adult Population Aging

### Implementation

**File:** `run_v3.py`
**Method:** `_step_aging_and_debut()`
**Lines:** 402-424 (original)

```python
def _step_aging_and_debut(self, step: int) -> Tuple[int, int, int]:
    """
    Age both childhood and adult populations every 5 years.
    Process continuous sexual debut EVERY timestep (PATCH: Sexual Debut Continuous + Flush).

    CRITICAL CHANGES (V4.2):
    - Aging (bin shifts) happens every 5 years
    - Sexual debut happens EVERY timestep using per-timestep probability
    - At aging boundaries, flush remaining debut-eligible children before bin shift
    - This ensures near-complete debut while maintaining continuous flow

    GUARDRAILS (V4.3):
    - Separately tracks continuous vs flush debuts for observability
    - Validates correct debut bin before flushing

    Returns:
        (continuous_debuts, flush_debuts, total_debuts) for this timestep
    """
    continuous_debuts = 0
    flush_debuts = 0

    # --- 1. Continuous sexual debut (EVERY timestep) ---
    # Process debuts from oldest childhood bin (10-14) using per-timestep probability
    debuting_females, debuting_males = self.demography.childhood.process_continuous_debut(
        self.time, self.rng, self.deterministic
    )

    if debuting_females > 0 or debuting_males > 0:
        self.state.population, continuous_debuts = self.demography.process_sexual_debut(
            debuting_females, debuting_males,
            self.state.population,
            self.rng, self.deterministic
        )

    # --- 2. Aging (bin shifts) every 5 years ---
    steps_per_5years = int(5.0 / self.time.years_per_step)
    if steps_per_5years <= 0:
        steps_per_5years = 1

    if step > 0 and step % steps_per_5years == 0:
        # 2a. FLUSH: Debut any remaining children in debut-eligible bin before they age out
        # This ensures cohorts do not leave the age band without debuting
        # GUARDRAIL: flush_remaining_debut_eligible() includes bin assertion
        flushed_females, flushed_males = self.demography.childhood.flush_remaining_debut_eligible()

        if flushed_females > 0 or flushed_males > 0:
            self.state.population, flush_debuts = self.demography.process_sexual_debut(
                flushed_females, flushed_males,
                self.state.population,
                self.rng, self.deterministic
            )

        # 2b. Age main tensor (shift up; empties 15-19)
        new_pop = np.zeros_like(self.state.population)
        for age in range(self.groups.n_age_groups - 1):
            new_pop[age + 1, :, :, :, :, :, :] = self.state.population[age, :, :, :, :, :, :]
        # Oldest age group accumulates
        new_pop[-1, :, :, :, :, :, :] += self.state.population[-1, :, :, :, :, :, :]
        self.state.population = new_pop

        # 2c. Age childhood pool (shift bins)
        self.demography.childhood.age_and_debut()

    total_debuts = continuous_debuts + flush_debuts
    return continuous_debuts, flush_debuts, total_debuts
```

### Algorithm

**Every 5 years (when `step % steps_per_5years == 0`):**

1. **Flush sexual debuts** - Empty remaining 10-14 bin before aging
2. **Shift adult bins** - 100% of each bin moves to next:
   ```
   15-19 → 20-24
   20-24 → 25-29
   25-29 → 30-34
   ...
   45-49 → 50-54 (if exists)
   Terminal bin → accumulates (no outflow)
   ```
3. **Shift childhood bins** - 100% of each childhood bin moves to next

**Characteristics:**
- Deterministic (no stochastic variation)
- Synchronized (all cohorts age simultaneously)
- Complete transfer (100% of bin moves)
- Empties youngest bin (15-19) to receive new debuts

---

## Childhood Pool Aging

### Implementation

**File:** `demography.py`
**Class:** `ChildhoodPool`
**Method:** `age_and_debut()`

```python
def age_and_debut(self):
    """
    Age childhood bins every 5 years: 0-4 → 5-9 → 10-14 → sexual debut.

    Called during aging events (every 5 simulation years).

    Flow:
    - Save 10-14 bin as debuting children (these will be added to 15-19 after shift)
    - Shift: 5-9 → 10-14, 0-4 → 5-9
    - Clear 0-4 (new births will refill it)

    Returns:
        (debuting_females, debuting_males) who age out of 10-14
    """
    debuting_females = int(self.population[-1, self.FEMALE])
    debuting_males = int(self.population[-1, self.MALE])

    # Shift bins: oldest to newest
    for i in range(self.n_bins - 1, 0, -1):
        self.population[i, :] = self.population[i - 1, :]

    # Clear youngest bin (0-4) for new births
    self.population[0, :] = 0

    return debuting_females, debuting_males
```

### Algorithm

**Every 5 years:**

1. **Save 10-14 bin** - These children will sexually debut
2. **Shift bins backward** (oldest first):
   ```
   5-9 → 10-14
   0-4 → 5-9
   ```
3. **Clear 0-4 bin** - Ready for new births over next 5 years
4. **Return debuting children** - Enter sexually active population

**Characteristics:**
- Deterministic
- Complete transfer (100% of bin moves)
- Empties youngest bin for births

---

## Flush-on-Aging Mechanism

### Implementation

**File:** `demography.py`
**Class:** `ChildhoodPool`
**Method:** `flush_remaining_debut_eligible()`

```python
def flush_remaining_debut_eligible(self):
    """
    Flush all remaining children in the debut-eligible bin (10-14) to sexual debut.

    Called at aging boundaries (every 5 years) BEFORE bin shifting.

    GUARDRAIL: Asserts that we're flushing the correct bin to prevent errors.

    Returns:
        (debuting_females, debuting_males)
    """
    oldest_bin_idx = self.n_bins - 1

    # GUARDRAIL: Assert we're flushing the correct debut-eligible bin
    # Expected: oldest childhood bin should be "10-14" (debut-eligible age range)
    if oldest_bin_idx >= len(self.age_bins):
        raise ValueError(
            f"Flush-on-aging error: oldest_bin_idx={oldest_bin_idx} but "
            f"only {len(self.age_bins)} bins exist"
        )

    debut_bin_label = self.age_bins[oldest_bin_idx]
    expected_debut_bin = "10-14"

    if debut_bin_label != expected_debut_bin:
        raise ValueError(
            f"Flush-on-aging guardrail violated: Expected oldest childhood bin "
            f"to be '{expected_debut_bin}' but got '{debut_bin_label}'. "
            f"This suggests a misconfiguration in childhood age bins."
        )

    # Extract all remaining individuals from debut-eligible bin
    debuting_females = int(self.population[oldest_bin_idx, self.FEMALE])
    debuting_males = int(self.population[oldest_bin_idx, self.MALE])

    # Empty the bin (they're all debuting)
    self.population[oldest_bin_idx, :] = 0

    return debuting_females, debuting_males
```

### Purpose

**Problem:** With continuous sexual debut happening every timestep, some children remain in the 10-14 bin due to probabilistic transitions. Without flushing, these individuals would age into 15-19 without ever sexually debuting.

**Solution:** At each 5-year aging boundary, flush (force debut) any remaining 10-14 children before the bin shift occurs.

**Guardrail:** Validates that the bin being flushed is indeed "10-14" to prevent configuration errors.

---

## Timeline Example: Discrete Aging

**Setup:**
- 100,000 people in 20-24 age bin
- Mortality rate 20-24: 0.1% per year (1 in 1,000)
- Mortality rate 25-29: 0.2% per year (2 in 1,000)
- Yearly timesteps

**Years 1-4: Population stays in 20-24**
```
Year 1: Start: 100,000 | Deaths: 100 | End: 99,900 (in 20-24)
Year 2: Start:  99,900 | Deaths:  99 | End: 99,801 (in 20-24)
Year 3: Start:  99,801 | Deaths:  99 | End: 99,702 (in 20-24)
Year 4: Start:  99,702 | Deaths:  99 | End: 99,603 (in 20-24)
Year 5: Start:  99,603 | Deaths:  99 | End: 99,504 (in 20-24)
```

**Year 5: AGING EVENT**
```
Before aging: 99,504 in 20-24
Apply mortality first: 99,504 - 99 = 99,405
Then age (100% shift): 0 in 20-24, 99,405 in 25-29
New cohort enters 20-24: ~94,000 (from 15-19)
```

**Year 6: First year in 25-29**
```
Start: 99,405 in 25-29
Deaths: 99,405 × 0.002 = 198 ← SPIKE!
End: 99,207 in 25-29
```

**Result:** Deaths jump from ~99/year to 198/year when cohort ages

---

## Mortality Spike Analysis

### Cause

**Synchronized cohort aging** → entire population experiences new mortality rate simultaneously

### Visualization

```
Annual Deaths:
Year:     1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
Deaths: 100  100  100  100  100  198  198  198  198  198  295  295  295  295  295
        ─────────────────── ┌────────────────────┐ ┌────────────────────┐
        20-24 bin          │    25-29 bin       │ │    30-34 bin       │
                           └ SPIKE at year 6   └─ SPIKE at year 11

Sawtooth pattern repeats every 5 years
```

### Magnitude

**Spike size depends on:**
- Mortality rate difference between age bins
- Population size in cohort
- For typical adult ages (20-50), spikes are 50-100% higher than baseline

**Example:** 20-24 (0.1%) → 25-29 (0.2%) = 2× mortality rate → ~2× spike in deaths

---

## Sexual Debut: Continuous + Flush Hybrid

### Continuous Debut (Every Timestep)

**Purpose:** Smooth inflow of children into sexually active population

**Implementation:**
- Applied every timestep to 10-14 bin
- Per-timestep probability: `p_debut = years_per_step / 5.0`
- Binomial draw: `debuts ~ Binomial(population[10-14], p_debut)`

### Flush-on-Aging (Every 5 Years)

**Purpose:** Ensure complete debut before cohort ages out

**Implementation:**
- Applied at aging boundaries (every 5 years)
- Deterministic: All remaining 10-14 children debut
- Happens BEFORE bin shift to prevent incomplete debuts

### Why Both?

**Continuous alone:** Some children would age to 15-19 without debuting (exponential tail)
**Flush alone:** Creates discrete debut spikes every 5 years
**Continuous + Flush:** Smooth debuts most timesteps, flush ensures completeness

---

## Mass Balance Under Discrete Aging

### Population Change Equation

```
Population[t+1] = Population[t] + Births[t] - Deaths[t] + Entrants[t]
```

**At aging events (every 5 years):**
- No population created or destroyed
- Pure bookkeeping: bins shift, population redistributes
- First bin empties (receives new debuts)
- Last bin accumulates (no outflow)

### Verification

```python
# Before aging
total_before = sum(population[all_bins])

# After aging (shift bins)
total_after = sum(new_population[all_bins])

# Should be equal
assert total_before == total_after
```

**Holds perfectly:** Discrete shifting preserves exact population count

---

## Pros and Cons

### ✅ Advantages

- **Simple:** Easy to understand and implement
- **Deterministic:** No stochastic variation in aging
- **Exact:** No rounding or approximation errors
- **Established:** Traditional demographic modeling approach

### ❌ Disadvantages

- **Mortality spikes:** Creates unrealistic 5-year oscillations in deaths
- **Cohort synchronization:** Entire population ages simultaneously (not realistic)
- **Numerical artifacts:** Sawtooth patterns in outputs
- **Flush complexity:** Requires flush-on-aging mechanism for sexual debut

---

## When Discrete Aging Was Appropriate

**Historical context:**
- Common in older demographic models (pre-1990s)
- Appropriate for:
  - Long-term equilibrium analysis (100+ years, spikes average out)
  - Coarse time resolution (5-year or 10-year timesteps)
  - Small models where computational cost matters

**Not ideal for:**
- Annual timestep models (spikes visible)
- Short-to-medium horizons (5-40 years)
- Model validation against annual mortality data
- Policy analysis requiring smooth trajectories

---

## Comparison with Fractional Aging

| Aspect | Discrete (Old) | Fractional (New) |
|--------|----------------|------------------|
| **Aging frequency** | Every 5 years | Every year |
| **Transfer amount** | 100% of bin | 20% of bin |
| **Mortality pattern** | Spiked (every 5 years) | Smooth (every year) |
| **Cohort sync** | Synchronized | Desynchronized |
| **Flush needed** | Yes (sexual debut) | No |
| **Numerical artifacts** | Sawtooth deaths | Smooth deaths |
| **Long-run totals** | Correct | Correct (same) |
| **Computational cost** | Low (every 5 years) | Higher (every year) |
| **Realism** | Lower | Higher |

---

## Legacy Configurations

**All configs before fractional aging patch:**
- Used discrete 5-year aging
- Results will differ after switching to fractional aging
- Cumulative deaths similar, but timing redistributed

**To reproduce old results:**
- Not possible with fractional aging (breaking change)
- Old code preserved in git history if needed
- Scientific justification for change documented

---

## References

This discrete aging implementation was based on:
- Traditional cohort-component demographic models
- STDSIM conceptual framework
- Standard practice in HIV modeling (pre-2000s)

**Replaced by:** Fractional aging (2026) - see PATCH_FRACTIONAL_AGING.md

---

**Status:** Historical reference only - DO NOT USE in new models
**See:** PATCH_FRACTIONAL_AGING.md for current implementation
