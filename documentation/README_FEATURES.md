# HIV Simulation Engine - Complete Feature Reference

**Version:** 3.0  
**Purpose:** External validation and feature documentation  
**Academic Context:** Thesis research on HIV prevention interventions

---

## Executive Summary

This is a discrete-time stochastic population model for HIV transmission dynamics and prevention interventions. The model is **inspired by STDSIM's conceptual framework** but reimplemented with modern computational best practices:

- Label-based configuration (not index-based)
- Dynamic dimensions (inferred from config)
- Flexible time resolution (days/months/years)
- Comprehensive demography (fertility, life tables)
- Robust file outputs
- Complete documentation

---

## Table of Contents

1. [Core Architecture](#1-core-architecture)
2. [Time System](#2-time-system)
3. [Population Representation](#3-population-representation)
4. [Group Definitions](#4-group-definitions)
5. [Sexual Mixing](#5-sexual-mixing)
6. [HIV Transmission](#6-hiv-transmission)
7. [Disease Progression](#7-disease-progression)
8. [Diagnosis & Treatment](#8-diagnosis--treatment)
9. [PrEP Interventions](#9-prep-interventions)
10. [Demography](#10-demography)
11. [Outputs](#11-outputs)
12. [Validation](#12-validation)
13. [Extensibility](#13-extensibility)

---

## 1. Core Architecture

### 1.1 Model Type
- **Population-based** (not individual/agent-based)
- **Discrete-time** (fixed timesteps, not continuous/event-driven)
- **Stochastic** (binomial/Poisson draws) with deterministic mode option

### 1.2 State Representation
Population stored as **counts in a multidimensional tensor**:
```
Shape: (n_age_groups, n_orientations, n_activities, n_hiv_stages, 
        n_diagnosis, n_art, n_prep)
```

All dimensions **inferred dynamically from configuration** - no hard-coded limits.

### 1.3 Configuration System
- **Single unified JSON file** (config.json)
- **Label-based** (human-readable names, not numeric indices)
- **Self-documenting** (inline notes on every parameter)
- **Hierarchical structure** ordered by conceptual importance

### 1.4 Programming Language
- **Python 3.10+**
- **Dependencies:** NumPy, Pandas only
- **No cloud services** (local execution)

---

## 2. Time System

### 2.1 Flexible Time Units
Supports three time units:
- **Days** (365 per year)
- **Months** (12 per year)
- **Years** (1 per year)

### 2.2 Configurable Timestep
```json
"time": {
  "unit": {"value": "months"},
  "step_size": {"value": 12},  // 12 months = yearly timestep
  "start_year": {"value": 1980},
  "end_year": {"value": 2020}
}
```

### 2.3 Rate Conversion
Annual rates automatically converted to per-timestep:
```
p_step = 1 - (1 - p_annual)^(years_per_step)
```

### 2.4 Number of Timesteps
Calculated as:
```
n_steps = (end_year - start_year) × (units_per_year / step_size)
```

**Example:** 
- 1980-2020 (40 years)
- Monthly units, 12-month steps
- Result: 40 timesteps

---

## 3. Population Representation

### 3.1 State Tensor Dimensions

| Dimension | Description | Examples | Configurable |
|-----------|-------------|----------|--------------|
| Age | 5-year bins | 15-19, 20-24, ..., 70-74 | ✅ Yes |
| Orientation | Sexual orientation | Female, Hetero_Male, MSM | ✅ Yes |
| Activity | Sexual activity level | Low, Medium, High | ✅ Yes |
| HIV Status | Disease stage | Susceptible, Acute, Chronic, AIDS | ⚠️ Fixed order |
| Diagnosis | HIV diagnosis status | Undiagnosed, Diagnosed | ❌ Fixed (2 states) |
| ART | Treatment status | Not on ART, On ART | ❌ Fixed (2 states) |
| PrEP | Prevention status | None, Oral_PrEP, Lenacapavir, ... | ✅ Dynamic |

### 3.2 Example State
```
A 25-year-old MSM with high sexual activity, chronic HIV, 
diagnosed, on ART, not on PrEP:

population[2, 2, 2, 2, 1, 1, 0] = count
           │  │  │  │  │  │  └─ PrEP: None
           │  │  │  │  │  └─ ART: On ART
           │  │  │  │  └─ Diagnosis: Diagnosed
           │  │  │  └─ HIV: Chronic
           │  │  └─ Activity: High
           │  └─ Orientation: MSM
           └─ Age: 25-29
```

### 3.3 Population Initialization
**Label-based** (not index-based):
```json
"initial_distribution": {
  "Female": {
    "15-19": {
      "Low": 5000,
      "Medium": 3000,
      "High": 1000
    }
  }
}
```

### 3.4 Validation
- No negative counts
- All values finite
- Mass balance preserved across transitions

---

## 4. Group Definitions

### 4.1 Age Groups
Fully configurable 5-year bins:
```json
"age_groups": [
  {"name": "15-19", "min": 15, "max": 19},
  {"name": "20-24", "min": 20, "max": 24},
  ...
]
```

**Features:**
- Any number of groups
- Any age ranges
- Used for age-specific rates (fertility, mortality)

### 4.2 Sexual Orientations
Default: Female, Heterosexual_Male, MSM

**Extensible** (can add orientations)

**Mixing rules:**
- Female ↔ Heterosexual_Male (heterosexual mixing)
- MSM ↔ MSM (MSM mixing)

### 4.3 Sexual Activity Groups
Configurable with contact rates:
```json
"sexual_activity_groups": [
  {"name": "Low", "contact_rate": 1.0},
  {"name": "Medium", "contact_rate": 3.0},
  {"name": "High", "contact_rate": 8.0}
]
```

**Features:**
- Any number of groups (not limited to 3)
- Contact rate = annual number of sexual partners
- Used in transmission calculation

### 4.4 HIV Disease Stages
**Fixed order** (do not modify):
1. Susceptible (HIV-negative)
2. Acute (recent infection, high viral load)
3. Chronic (established infection)
4. AIDS (advanced disease)

### 4.5 PrEP States
**Dynamically built from interventions:**
- Always includes "None"
- Adds enabled interventions (e.g., Oral_PrEP, Lenacapavir)

---

## 5. Sexual Mixing

### 5.1 Group-Based Mixing
Uses **mixing matrices**, not explicit partner matching.

### 5.2 Mixing Matrices
Define proportion of contacts from activity group *i* to activity group *j*:

```json
"heterosexual": {
  "Low": {
    "Low": 0.7,     // 70% within low activity
    "Medium": 0.25,
    "High": 0.05
  },
  "Medium": { ... },
  "High": { ... }
}
```

**Requirements:**
- Rows must sum to 1.0 (normalized probability distribution)
- Values in [0, 1]
- Validated on load

### 5.3 Contact Calculation
For each timestep:
```
total_contacts[orient, activity] = 
    population[orient, activity] × contact_rate × years_per_step
```

### 5.4 Contact Allocation
Contacts allocated via mixing matrix:
```
contacts_allocated[from_orient, from_act, to_orient, to_act] = 
    total_contacts[from_orient, from_act] × mixing_matrix[from_act, to_act]
```

### 5.5 Condom Usage
Activity-specific condom effectiveness modifier:
```json
"condom_usage": {
  "Low": 0.5,     // 50% effective
  "Medium": 0.7,
  "High": 0.9
}
```

---

## 6. HIV Transmission

### 6.1 Transmission Probability
Per-contact probability calculated as:
```
P = β(stage) × (1 - PrEP_efficacy) × condom_modifier × ART_modifier × prevalence
```

Where:
- `β(stage)` = Base transmission probability by HIV stage
- `PrEP_efficacy` = Weighted by PrEP coverage in susceptible population
- `condom_modifier` = Activity-based condom effectiveness
- `ART_modifier` = Reduction from ART coverage in infected population
- `prevalence` = Proportion HIV+ in partner group

### 6.2 Stage-Specific Transmission
```json
"transmission": {
  "beta_acute": 0.015,     // 1.5% per act (acute)
  "beta_chronic": 0.004,   // 0.4% per act (chronic)
  "beta_aids": 0.008       // 0.8% per act (AIDS)
}
```

### 6.3 ART Effect on Transmission
Treatment as Prevention (TasP / U=U):
```json
"art_transmission_reduction": 0.96  // 96% reduction when on ART
```

### 6.4 New Infections
Drawn stochastically:
```
n_infected ~ Binomial(n_contacts × prevalence, P)
```

Capped at susceptible population.

### 6.5 Infection Assignment
New infections:
- Move from Susceptible → Acute
- Remain Undiagnosed
- Not on ART
- Lose PrEP status (if any)
- Distributed across age groups proportionally

---

## 7. Disease Progression

### 7.1 HIV Stages
Progression pathway:
```
Susceptible → Acute → Chronic → AIDS
```

### 7.2 Progression Rates
Annual probabilities:
```json
"progression": {
  "acute_to_chronic": 0.5,    // 50% per year
  "chronic_to_aids": 0.05     // 5% per year
}
```

Converted to per-timestep automatically.

### 7.3 Implementation
Binomial draws:
```
progressions ~ Binomial(population[stage], progression_rate)
```

Move counts from current stage to next stage.

### 7.4 Both Progressions in One Timestep
Individuals can progress twice in one timestep:
- Acute → Chronic → AIDS

Example: 100 in Acute stage, 50% progression rate
- 50 move to Chronic
- Of those 50, ~2-3 may progress to AIDS (5% rate)
- Final: 50 Acute, 47 Chronic, 3 AIDS

---

## 8. Diagnosis & Treatment

### 8.1 Diagnosis Cascade
Undiagnosed → Diagnosed

Annual rate:
```json
"diagnosis": {
  "annual_rate": 0.15  // 15% diagnosed per year
}
```

Only HIV+ individuals can be diagnosed.

### 8.2 ART Initiation
Diagnosed, not on ART → On ART

```json
"treatment": {
  "art_initiation_rate": 0.8  // 80% start ART per year
}
```

### 8.3 ART Dropout
On ART → Not on ART

```json
"art_dropout_rate": 0.05  // 5% discontinue per year
```

### 8.4 ART Effects

**Transmission:**
```
transmission_modifier = 1 - (proportion_on_ART × 0.96)
```

**Mortality:**
```
hiv_mortality_on_ART = hiv_mortality × (1 - 0.90)  // 90% reduction
```

---

## 9. PrEP Interventions

### 9.1 Supported Interventions
- **Oral PrEP** (daily pills)
- **Lenacapavir** (injectable, 6-month duration)
- **Extensible** (can add new interventions)

### 9.2 PrEP Configuration
```json
"Oral_PrEP": {
  "enabled": true,
  "dosing_interval": {"value": 3, "unit": "months"},
  "efficacy": 0.86,           // 86% effective
  "uptake_rate": 0.05,        // 5% start per year
  "dropout_rate": 0.2,        // 20% stop per year
  "uptake_modifier_by_activity": {
    "Low": 0.5,     // Lower uptake in low-risk
    "Medium": 1.0,
    "High": 1.5     // Higher uptake in high-risk
  }
}
```

### 9.3 PrEP Transitions
**Susceptible only** can use PrEP:
- None → PrEP (uptake)
- PrEP → None (dropout)

**HIV infection removes PrEP:**
- Acute infections always have PrEP = None

### 9.4 PrEP Effect on Transmission
Reduces susceptibility:
```
prep_modifier = 1 - (proportion_on_prep × efficacy)
```

Applied in transmission probability calculation.

### 9.5 Dosing Intervals
Engine aligns dosing with timestep:
- 3-month oral prescription with 12-month timestep = annual
- 6-month injectable with 3-month timestep = every 2 steps

---

## 10. Demography

### 10.1 Demography Modes

Three modes supported:

**Closed:**
- No births
- Aging and mortality only
- Population declines over time

**Fixed Net Addition:**
- Constant annual entrants
- Added to entry age group
- Simple population maintenance

**Fertility Table:**
- ASFR-based births (age-specific fertility rates)
- Year-specific multipliers
- Most realistic demography

### 10.2 Fertility Implementation

**Age-Specific Fertility Rates (ASFR):**
```json
"asfr_by_age_limit": [
  {"age_limit": 20, "birth_rate": 0.0028},
  {"age_limit": 25, "birth_rate": 0.0850},
  {"age_limit": 30, "birth_rate": 0.1150},
  ...
]
```

**Birth Calculation:**
```
births = women_count × asfr × years_per_step × year_multiplier
```

**Year Multipliers:**
```json
"year_multipliers": {
  "values": {
    "1980": 1.0,
    "1990": 0.98,
    "2000": 0.95,
    "2010": 0.92
  }
}
```

Interpolated between years.

**Newborn Assignment:**
- Sex ratio at birth (default 51.2% male)
- Entry age group (usually 15-19 for sexual debut)
- Activity distribution (usually all "Low")
- Susceptible, undiagnosed, no ART, no PrEP

**Sexual Debut Modeling:**

Sexual debut is modeled as a continuous per-timestep transition applied to individuals in debut-eligible childhood age groups, ensuring smooth inflow into the sexually active population across all timesteps.

To maintain consistency with coarse age-bin definitions, individuals remaining in the debut-eligible childhood bin at the point they age out are transitioned to the sexually active population. This prevents an artificial exponential tail that can arise from discrete-time hazards while preserving continuous behavior and timestep robustness.

This approach:
- avoids discrete cohort spikes,
- ensures near-complete debut within the age-band window,
- remains stable across different timestep resolutions.

### 10.3 Life Tables

**Optional replacement for constant background mortality.**

**Structure:**
```json
"survival_by_age_limit": [
  {"age_limit": 1, "male_survival": 0.99622, "female_survival": 0.99687},
  {"age_limit": 5, "male_survival": 0.99913, "female_survival": 0.99943},
  ...
]
```

**Conversion to Mortality:**
```
survival_per_step = survival_annual ^ years_per_step
mortality_per_step = 1 - survival_per_step
```

**Application:**
- Sex-specific (male vs female)
- Age-specific (lookup by age midpoint)
- Applied to all HIV stages as background mortality

### 10.4 HIV Mortality Integration

Two modes:

**Excess (default):**
```
total_mortality = background_mortality + hiv_mortality
```

**Override:**
```
total_mortality = hiv_mortality (background ignored for HIV+)
```

HIV mortality by stage:
```json
"mortality": {
  "acute": 0.01,      // 1% per year
  "chronic": 0.02,    // 2% per year
  "aids": 0.25        // 25% per year
}
```

Reduced by 90% when on ART.

### 10.5 Population Adjustments

**Optional exogenous scaling for calibration:**

```json
"population_adjustments": {
  "enabled": true,
  "apply_to": "total",  // or by_age, by_orientation
  "year_multipliers": {
    "1980": 1.0,
    "1990": 1.02,    // 2% increase
    "2000": 1.05     // 5% increase
  }
}
```

Applied at start of each timestep before transmission.

### 10.6 Aging

**Every 5 years:**
- Population moves up one age group
- Oldest group remains (or dies via mortality)

**New entrants:**
- Added to youngest group
- Scaled by timestep (annual_entrants × years_per_step)
- Distributed across orientations and activities

---

## 11. Outputs

### 11.1 File Outputs

**Per-Run Files:**
```
outputs/baseline_run_001.csv
outputs/baseline_run_002.csv
...
```

**Aggregated Files:**
```
outputs/baseline_aggregated.csv
```

Contains mean + quantiles (Q05, Q50, Q95) across runs.

### 11.2 Output Formats
- **CSV** (default)
- **Excel** (.xlsx) - optional

### 11.3 Timeseries Columns

| Column | Description |
|--------|-------------|
| step | Timestep number (0-indexed) |
| year | Calendar year (floating point) |
| new_infections | HIV infections this timestep |
| prevalence | HIV+ / total population |
| incidence | New infections / susceptible |
| hiv_deaths | HIV-related deaths |
| background_deaths | Non-HIV deaths |
| total_population | Total population |
| births | Births this timestep (if fertility enabled) |
| prep_coverage_* | PrEP coverage by intervention |

### 11.4 Terminal Summaries

**Single Run:**
```
Year       Population HIV Prev %    New Inf   HIV Deaths    BG Deaths
1980        1,120,494       0.10          0            0        9,006
1985        1,076,649       0.10          0            0        8,756
...

Total new infections: 0
Final prevalence: 0.10%
PrEP Coverage: Oral_PrEP: 0.00%, Lenacapavir: 0.00%
```

**Multi-Run:**
```
Final Year (2020) Statistics:
Metric                       Mean          Q05          Q50          Q95
Total Population          819,320      819,114      819,170      819,631
HIV Prevalence (%)           0.10         0.10         0.10         0.10
```

### 11.5 State Counts (Optional)

Full population tensor at final timestep.

Size: `n_age × n_orient × n_activity × n_hiv × n_dx × n_art × n_prep`

**Large files** - disabled by default.

---

## 12. Validation

### 12.1 Configuration Validation

Checked on load:

**Mixing Matrices:**
- Rows sum to 1.0 (±1e-6 tolerance)
- All values in [0, 1]

**Probabilities:**
- All rates in [0, 1]
- Survival probabilities in [0, 1]
- Birth rates ≥ 0

**Population Structure:**
- All referenced groups exist
- Labels match definitions
- No orphaned entries

**Error Messages:**
```
ERROR: Mixing matrix 'MSM' for activity 'High' sums to 1.12 (expected 1.0)
ERROR: transmission.beta_acute = 1.5 is not in [0, 1]
ERROR: Population distribution has unknown orientation 'Unknown'
```

Clear, actionable, tells exactly what's wrong.

### 12.2 Runtime Validation

**Each timestep:**
- No negative population counts
- All values finite (no NaN/Inf)

**Raises ValueError if violated.**

### 12.3 Mass Balance

Population changes tracked:
```
Δpop = births - deaths + entrants
```

No "leakage" - all transitions preserve counts.

### 12.4 Deterministic Mode

For testing/debugging:
```json
"deterministic_mode": true
```

Replaces stochastic draws with expected values:
- `Binomial(n, p)` → `int(n × p)`
- `Poisson(λ)` → `int(λ)`

**Exact reproducibility:** Same seed → identical results

---

## 13. Extensibility

### 13.1 Adding Activity Groups

**Edit config.json:**
```json
"sexual_activity_groups": [
  {"name": "Low", "contact_rate": 1.0},
  {"name": "Medium", "contact_rate": 3.0},
  {"name": "High", "contact_rate": 8.0},
  {"name": "Very_High", "contact_rate": 25.0}  // NEW
]
```

**Update mixing matrices to 4×4**

**Update population distributions**

**No code changes needed** - engine detects 4 groups automatically.

### 13.2 Adding PrEP Interventions

```json
"New_PrEP": {
  "enabled": true,
  "dosing_interval": {"value": 12, "unit": "months"},
  "efficacy": 0.95,
  "uptake_rate": 0.10,
  "dropout_rate": 0.10
}
```

Engine adds "New_PrEP" to PrEP states automatically.

### 13.3 Changing Time Resolution

**Quarterly timesteps:**
```json
"time": {
  "unit": "months",
  "step_size": 3  // 3-month timesteps
}
```

All rates converted automatically.

### 13.4 Parameter Scenarios

**Copy config.json → scenario_high_prep.json**

Edit parameters, run:
```python
simulate_v3('scenario_high_prep.json')
```

No code changes.

---

## Appendix A: Simulation Loop Order

**V3 Timestep Sequence (executed once per timestep):**

1. Population adjustments (if enabled)
2. Contacts & mixing
3. Transmission
4. Disease progression
5. Diagnosis
6. ART transitions
7. PrEP transitions
8. Fertility/births (if enabled)
9. Mortality (background + HIV)
10. Aging (every 5 years)
11. Output recording

**Order is critical** and must not be changed.

---

## Appendix B: Mathematical Notation

**Rate Conversion:**
```
p_step = 1 - (1 - p_annual)^(t_step / 1 year)
```

**Transmission Probability:**
```
P = β_s × (1 - ε_PrEP × c_PrEP) × m_condom × (1 - ε_ART × c_ART) × π
```

Where:
- β_s = stage-specific transmission probability
- ε_PrEP = PrEP efficacy
- c_PrEP = PrEP coverage in susceptible
- m_condom = condom modifier
- ε_ART = ART transmission reduction
- c_ART = ART coverage in infected
- π = prevalence in partner group

**New Infections:**
```
I_new ~ Binomial(n_contacts × π, P)
```

**Fertility:**
```
B = Σ_a (N_a^F × ASFR_a × t_step × m_year)
```

Where:
- N_a^F = women in age group a
- ASFR_a = age-specific fertility rate
- t_step = years per timestep
- m_year = year-specific multiplier

---

## Appendix C: File Structure

```
hiv_sim_v3/
├── config.json               # Single unified configuration
├── run_v3.py                 # Main simulation runner
├── output_writer.py          # Output module
├── demography.py             # Demography module
├── outputs/                  # Generated outputs
│   ├── baseline_run_001.csv
│   ├── baseline_aggregated.csv
│   └── ...
└── README_FEATURES.md        # This document
```

---

## Appendix D: Configuration Hierarchy

```
config.json
├── meta (model info)
├── time (timestep system)
├── population (initial distribution)
├── demography (mode, mortality)
├── life_table (optional)
├── fertility (optional)
├── population_adjustments (optional)
├── groups (age, orientation, activity)
├── mixing (sexual contact patterns)
├── disease (transmission, progression)
├── interventions (PrEP configs)
├── output (file settings)
├── simulation_controls (n_runs, seed)
└── notes (documentation)
```

**Ordered by conceptual importance, not module.**

---

## Appendix E: Performance

**Typical performance** (modern CPU):

| Metric | Value |
|--------|-------|
| 40-year simulation | ~0.5 seconds |
| 100-seed ensemble | ~50 seconds |
| Memory usage | ~50 MB |
| Config validation | <50 ms |

**Optimizations:**
- NumPy vectorized operations
- No loops over individuals
- Efficient state tensor

---

## Appendix F: Limitations

**By Design:**
1. Discrete timesteps (not continuous-time)
2. Group-based mixing (not explicit partnerships)
3. Homogeneous groups (individuals in same state identical)
4. No superinfection
5. No viral load stratification
6. No STI cofactors

**These are intentional simplifications appropriate for population-level cost-effectiveness modeling.**

---

## Appendix G: Validation Checklist

For external validators:

- [ ] Configuration loads without errors
- [ ] Mixing matrices sum to 1.0
- [ ] All probabilities in [0, 1]
- [ ] Population never negative
- [ ] Mass balance preserved
- [ ] Deterministic mode reproducible
- [ ] Output files created
- [ ] Terminal summary displays
- [ ] Multiple runs aggregate correctly
- [ ] Fertility produces births (if enabled)
- [ ] Life tables apply mortality (if enabled)

---

## Version History

**V1.0** - Basic model with yearly timesteps
**V2.0** - Dynamic configuration, flexible time system
**V3.0** - Robust outputs, demography modules, life tables
**V4.x** - Sexual debut system, childhood pool, fertility integration
**V5.0** - Time-scheduled ART/PrEP, multi-scenario framework, comprehensive validation

---

## V5 Features (NEW)

### Time-Based Schedules
ART and PrEP initiation/discontinuation rates can now vary by calendar year:
```json
{
  "art_schedule": {
    "interpolation": {"value": "stepwise"},
    "initiation": {
      "schedule": {"1996": 0.1, "2000": 0.3, "2010": 0.7, "2020": 0.85}
    }
  }
}
```

### Multi-Scenario Framework
Run multiple policy scenarios with config overrides:
```bash
python run_scenarios.py config_v5.json
```

Scenarios support:
- Parameter sensitivity analysis
- Intervention comparisons (status quo, no PrEP, scale-up)
- Time-varying coverage scenarios
- Automated validation and scenario-labeled outputs

**For complete V5 documentation, see `V5_PATCH_SUMMARY.md`**

---

**End of Feature Reference**

For implementation details, see code documentation.
For V5 features and usage, see V5_PATCH_SUMMARY.md.
For academic framing, see thesis documentation.
