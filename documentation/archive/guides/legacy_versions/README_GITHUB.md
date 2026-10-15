# HIV Transmission Model v3 - Netherlands Calibration

An agent-based epidemiological model for HIV transmission dynamics with demographic processes, calibrated for the Netherlands population.

## Overview

This model simulates HIV transmission in a population with:
- **Demographic processes**: Births, aging, mortality, sexual debut
- **Stratified population**: Age groups, sexual orientation (Female, Heterosexual Male, MSM), activity levels
- **HIV progression**: Acute → Chronic → AIDS stages
- **Interventions**: ART (antiretroviral therapy), PrEP (pre-exposure prophylaxis)
- **Calibration features**: Population adjustments, fertility multipliers, life tables

## Recent Updates (2026-02-11)

- ✅ **Fractional aging implementation** - Smooth 20% annual transfers vs discrete 5-year aging
- ✅ **Population adjustment accounting fix** - Proper mass balance for calibration multipliers
- ✅ **Aging method toggle** - Switch between fractional and discrete aging via config
- ✅ **Distribution validation** - Confirmed adjustments preserve demographic structure

See `documentation/` folder for detailed patch notes.

## Quick Start

### Requirements

```bash
Python 3.8+
numpy
pandas
```

### Installation

```bash
# Clone repository
git clone <your-repo-url>
cd v3_patch

# Install dependencies
pip install numpy pandas --break-system-packages

# OR use virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install numpy pandas
```

### Running the Model

```bash
# Basic run with default config
python3 run_v3.py config_v6_rel_pop.json

# The simulation will:
# - Initialize population (~12.7M people, Netherlands 1970)
# - Run 50 timesteps (1970-2020, annual steps)
# - Apply demographic processes and HIV transmission
# - Output results to console and CSV files
```

### Expected Output

```
Running simulation (run 1)...
  Step 10/50  Step 20/50  Step 30/50  Step 40/50  Step 50/50
  Completed 50 timesteps.

================================================================================
SIMULATION SUMMARY - Run 1
================================================================================

Year       Population HIV Prev %    New Inf   HIV Deaths    BG Deaths
--------------------------------------------------------------------------------
1970       12,216,783       0.10        215          186    1,204,984
1975       14,000,080       0.38     27,961        1,062      341,899
...
2015       48,112,203       9.96    168,398       71,808    1,475,269

Total new infections: 9,038,464
Total HIV deaths: 3,189,552
Final prevalence: 9.81%
```

## Key Files

### Core Model
- `run_v3.py` - Main simulation engine
- `engine_v3.py` - State management and initialization
- `demography.py` - Demographic processes (births, deaths, aging)
- `transmission_v3.py` - HIV transmission calculations
- `mixing_matrices_v2.py` - Contact patterns between groups

### Configuration
- `config_v6_rel_pop.json` - **Main configuration file**
  - Population structure
  - ASFR (age-specific fertility rates)
  - Life tables (survival probabilities)
  - HIV parameters
  - Intervention schedules

### Outputs
- `output_writer.py` - CSV output generation
- `outputs/` - (created during run) Time series results

### Documentation
- `documentation/` - Comprehensive patch notes and validation reports
  - `POPULATION_ADJUSTMENT_ACCOUNTING_FIX.md` - Mass balance fix
  - `FRACTIONAL_AGING_BUG_FIX.md` - Aging implementation
  - `AGING_METHOD_TOGGLE.md` - Discrete vs fractional aging
  - `POPULATION_ADJUSTMENT_DISTRIBUTION_VALIDATION.md` - Distribution preservation

## Configuration Guide

### Key Parameters

**Population (config_v6_rel_pop.json):**
```json
"population": {
  "total": 12713815,  // Netherlands 1970
  "p_msm_males_over_15": 0.10
}
```

**Aging method:**
```json
"demography": {
  "aging_method": {
    "value": "fractional"  // or "discrete"
  }
}
```

**Fertility (ASFR):**
```json
"fertility": {
  "asfr_by_age_limit": [...],  // Baseline age-specific rates
  "year_multipliers": {
    "1970": 1.8,  // TFR adjustment by year
    "2020": 1.05
  }
}
```

**Mortality (Life table):**
```json
"life_table": {
  "enabled": true,
  "survival_by_age_limit": [
    {"age_limit": 20, "male_survival": 0.99357, ...}
  ]
}
```

**⚠️ Note:** If death rates are too high, see `diagnose_mortality_rates.py` - survival values may need conversion from 5-year to annual rates.

### Population Adjustments (Calibration)

```json
"population_adjustments": {
  "enabled": {"value": true},
  "year_multipliers": {
    "1970": 1.00,
    "1990": 1.10,  // 10% population increase
    "2020": 1.05
  }
}
```

Multipliers scale entire population proportionally for calibration to empirical data.

## Running Tests

```bash
# Distribution validation
python3 test_adjustment_distribution_validation.py

# Edge cases
python3 test_adjustment_edge_cases.py

# Mortality diagnosis
python3 diagnose_mortality_rates.py

# Comprehensive adjustment tests
python3 test_population_adjustment_comprehensive.py
```

## Project Structure

```
v3_patch/
├── run_v3.py                    # Main simulation engine
├── engine_v3.py                 # State management
├── demography.py                # Births, deaths, aging
├── transmission_v3.py           # HIV transmission
├── mixing_matrices_v2.py        # Contact patterns
├── output_writer.py             # Results output
├── config_v6_rel_pop.json       # Main configuration
├── documentation/               # Patch notes and validation
│   ├── POPULATION_ADJUSTMENT_ACCOUNTING_FIX.md
│   ├── FRACTIONAL_AGING_BUG_FIX.md
│   ├── AGING_METHOD_TOGGLE.md
│   └── ...
└── test_*.py                    # Validation tests
```

## Common Issues

### High Death Rates

**Symptom:** Crude death rate > 5% per year

**Cause:** Survival probabilities are 5-year values treated as annual

**Fix:** Run `diagnose_mortality_rates.py` and convert survival values:
```python
annual_survival = five_year_survival ** (1/5)
```

### Mass Balance Errors

**Symptom:** "GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED"

**Cause:** Usually population adjustments not tracked (fixed in recent patch)

**Fix:** Ensure using latest code with adjustment_change tracking

### Import Errors

**Symptom:** `ModuleNotFoundError: No module named 'numpy'`

**Fix:**
```bash
pip install numpy pandas --break-system-packages
# or use virtual environment
```

## Model Features

### Demographic Processes
- **Births**: Age-specific fertility rates (ASFR) with temporal multipliers
- **Mortality**: Life tables with age/sex-specific survival probabilities
- **Aging**: Fractional (20% annual) or discrete (5-year cohorts)
- **Sexual debut**: Transition from childhood pool at age 15

### HIV Dynamics
- **Transmission**: Activity-stratified contact matrices with MSM/heterosexual mixing
- **Progression**: Acute (high transmissibility) → Chronic → AIDS
- **Diagnosis**: Time-varying diagnosis rates
- **Treatment**: ART with mortality reduction and transmission suppression

### Interventions
- **ART**: Time-scheduled rollout (e.g., 10% coverage in 1996 → 85% by 2020)
- **PrEP**: Oral PrEP and Lenacapavir with uptake/dropout dynamics
- **Calibration**: Population adjustments to match empirical data

### Population Structure
- **Age groups**: 15-19, 20-24, ..., 65+
- **Orientations**: Female, Heterosexual Male, MSM
- **Activity levels**: Low, Medium, High (sexual activity)
- **HIV stages**: Susceptible, Acute, Chronic, AIDS
- **Treatment states**: Diagnosis, ART, PrEP

## Validation

All major features have been validated:
- ✅ Mass balance (population conservation)
- ✅ PrEP/ART accounting
- ✅ Distribution preservation (population adjustments)
- ✅ Fractional aging (no mortality spikes)
- ✅ Edge cases (extreme multipliers, small populations)

See `documentation/` for detailed validation reports.

## Contact & Support

For questions about:
- **Model implementation**: See code comments and `documentation/`
- **Calibration**: See `POPULATION_ADJUSTMENT_DISTRIBUTION_VALIDATION.md`
- **ASFR setup**: See fertility year_multipliers explanation
- **Mortality**: Run `diagnose_mortality_rates.py`

## License

[Add your license here]

## Citation

If you use this model in research, please cite:
[Add citation information]

## Acknowledgments

- Netherlands population data: Statistics Netherlands (CBS)
- Mortality data: Human Mortality Database (HMD)
- Fertility data: Human Fertility Collection
- Model development: [Add contributors]

---

**Last updated:** 2026-02-11
**Version:** v3 with fractional aging and population adjustment fixes
