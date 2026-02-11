# HIV Transmission Model - V5

**Population-based discrete-time stochastic model for HIV transmission dynamics and prevention interventions**

---

## Quick Links

📘 **[Complete Model Guide](documentation/README_MODEL_GUIDE.md)** - Comprehensive documentation (1,100+ lines)

🚀 **[V5 Quick Start](documentation/V5_QUICK_START.md)** - Get started with V5 features

📊 **[V5 Patch Summary](documentation/V5_PATCH_SUMMARY.md)** - V5 features overview

✅ **[Validation Patches](documentation/VALIDATION_PATCHES.md)** - Validation system details

🔧 **[Population Initialization](documentation/POPULATION_INITIALIZATION_PATCH.md)** - Census-based initialization

---

## Model Overview

This is a discrete-time stochastic population model designed for HIV transmission dynamics and cost-effectiveness analysis of prevention interventions. Inspired by STDSIM's conceptual framework but reimplemented with modern best practices.

### Core Features

- **Label-based configuration** - Human-readable JSON, not index-based
- **Dynamic dimensions** - Age groups, activities, interventions all configurable
- **Flexible time resolution** - Days, months, or years
- **Comprehensive demography** - Fertility, life tables, sexual debut, aging
- **Robust validation** - Mass balance, conservation checks, sanity tests

### V5 Features (Latest)

- ✨ **Time-scheduled interventions** - ART/PrEP rates vary by year
- 🔀 **Multi-scenario framework** - Compare multiple policies in one run
- 📊 **Policy comparisons** - Baseline freeze & reuse for counterfactuals
- ✅ **Validation patches** - Transmission sanity, mass balance, ART balance
- 🌍 **Census-based initialization** - Relative weights with global MSM share
- 📈 **Extended age groups** - Support for 65+, 70+ open-ended bins

---

## Installation

**Requirements:**
- Python 3.10+
- NumPy
- Pandas

**Install dependencies:**
```bash
pip install numpy pandas
```

---

## Quick Start

### Basic Simulation

```bash
python run_v3.py config.json
```

### Multi-Scenario Analysis (V5)

```bash
python run_scenarios.py config_v5.json
```

### Custom Configuration

```bash
python run_v3.py my_custom_config.json
```

---

## Configuration Structure

```json
{
  "time": {
    "start_year": 1980,
    "end_year": 2020,
    "unit": "months",
    "step_size": 12
  },
  "population": {
    "initialization_method": "relative_weights",
    "target_population": 1000000,
    "p_msm": 0.10
  },
  "art_schedule": {
    "enabled": true,
    "initiation": {
      "schedule": {
        "1996": 0.10,
        "2010": 0.70,
        "2020": 0.85
      }
    }
  },
  "scenarios": {
    "enabled": true,
    "scenario_list": [...]
  }
}
```

See **[Complete Model Guide](documentation/README_MODEL_GUIDE.md)** for full configuration reference.

---

## Model Versions

| Version | Key Features |
|---------|-------------|
| **V1.0** | Basic model, yearly timesteps |
| **V2.0** | Dynamic config, flexible time |
| **V3.0** | Robust outputs, demography modules |
| **V4.0** | Sexual debut, childhood pool, guardrails |
| **V5.0** | Time-scheduled interventions, scenarios, validation patches, census initialization ✨ **CURRENT** |

---

## Documentation

### Essential Reading

1. **[README_MODEL_GUIDE.md](documentation/README_MODEL_GUIDE.md)** - Complete model documentation
   - Part I: Core Model (11 sections)
   - Part II: V4 Enhancements (4 sections)
   - Part III: V5 Features (3 sections)
   - Part IV: Advanced Customizations (4 sections)
   - Part V: Appendices (8 sections)

2. **[V5_QUICK_START.md](documentation/V5_QUICK_START.md)** - Hands-on V5 tutorial

3. **[VALIDATION_PATCHES.md](documentation/VALIDATION_PATCHES.md)** - Validation system

### Feature Documentation

- **[V5_PATCH_SUMMARY.md](documentation/V5_PATCH_SUMMARY.md)** - V5 features overview
- **[POPULATION_INITIALIZATION_PATCH.md](documentation/POPULATION_INITIALIZATION_PATCH.md)** - Initialization methods
- **[PATCH_OPEN_ENDED_AGE_BINS.md](documentation/PATCH_OPEN_ENDED_AGE_BINS.md)** - Extended age group support
- **[V5_VALIDATION_COMPLETE.md](documentation/V5_VALIDATION_COMPLETE.md)** - Validation test results

### Technical Documentation

- **[MODEL_REVIEW_REPORT.md](documentation/MODEL_REVIEW_REPORT.md)** - Pre-calibration review
- **[MASS_BALANCE_ASSERTION.md](documentation/MASS_BALANCE_ASSERTION.md)** - Mass balance details
- **[PHASE_4_STATUS.md](documentation/PHASE_4_STATUS.md)** - Development history

---

## File Structure

```
hiv_sim_v3_patch/
├── README.md                      # This file
├── config.json                    # Base configuration
├── config_v5_test.json           # V5 test configuration
├── run_v3.py                     # Main simulation engine
├── run_scenarios.py              # V5 multi-scenario runner
├── engine_v3.py                  # Population state & initialization
├── demography.py                 # Demography module
├── output_writer.py              # Output module
├── documentation/                # All documentation
│   ├── README_MODEL_GUIDE.md     # ← COMPREHENSIVE GUIDE
│   ├── V5_QUICK_START.md
│   ├── V5_PATCH_SUMMARY.md
│   ├── VALIDATION_PATCHES.md
│   └── ...
└── outputs/                      # Generated outputs
    ├── baseline/
    └── ...
```

---

## Usage Examples

### Example 1: Simple Calibration Run

```bash
# Run baseline scenario
python run_v3.py config.json
```

**Output:**
```
outputs/baseline_run_001.csv
outputs/baseline_aggregated.csv
```

### Example 2: Multi-Scenario Policy Analysis

```bash
# Compare status quo vs. PrEP scale-up
python run_scenarios.py config_v5.json
```

**Output:**
```
outputs/
├── baseline/
│   └── baseline_aggregated.csv
├── no_prep/
│   └── no_prep_aggregated.csv
└── prep_scaleup/
    └── prep_scaleup_aggregated.csv
```

### Example 3: Census-Based Initialization

**Config snippet:**
```json
{
  "population": {
    "initialization_method": "relative_weights",
    "target_population": 12713815,
    "p_msm": 0.10,
    "age_sex_distribution": {
      "15-19": {"female": 0.086, "male": 0.089},
      "20-24": {"female": 0.081, "male": 0.084},
      ...
      "65+": {"female": 0.089, "male": 0.075}
    }
  }
}
```

See **[Population Initialization Guide](documentation/POPULATION_INITIALIZATION_PATCH.md)** for details.

---

## Validation & Quality Assurance

### Automatic Validation (V5)

✅ **Transmission sanity checks** - Infections ≤ susceptible, probabilities valid
✅ **Mass balance** - Population accounting correct (births - deaths = change)
✅ **ART balance** - Diagnosis/treatment cascade conserved
✅ **Conservation checks** - Initialization totals match targets

### Testing

All validation patches include comprehensive test configurations:
- `test_mass_balance.json` - Mass balance validation
- `test_init_relative_weights.json` - Census initialization validation
- `config_v5_test.json` - V5 features integration test

---

## Performance

**Typical benchmarks** (modern CPU):

| Task | Time |
|------|------|
| 40-year simulation | 0.5-0.8s |
| 100-seed ensemble | 50-80s |
| 3 scenarios × 10 seeds | 150-240s |

**Memory:** ~50-100 MB

---

## Academic Context

This model is developed for thesis research on HIV prevention interventions. It is designed for:
- Cost-effectiveness analysis
- Policy scenario comparisons
- Intervention impact assessment
- Academic publication & external validation

**NOT designed for:**
- Real-time epidemic forecasting
- Individual-level predictions
- Clinical decision support

---

## Support & Troubleshooting

### Common Issues

**"ValueError: invalid literal for int() with base 10: '65+'"**
→ Update to V5 - open-ended age bins now supported

**"Conservation check failed: Total population mismatch"**
→ Check `age_sex_distribution` weights sum correctly
→ See [Population Initialization Guide](documentation/POPULATION_INITIALIZATION_PATCH.md)

**"Transmission sanity check failed"**
→ Check transmission parameters are in [0, 1]
→ See [Validation Patches](documentation/VALIDATION_PATCHES.md)

### Getting Help

1. Check **[Complete Model Guide](documentation/README_MODEL_GUIDE.md)** first
2. Review relevant feature documentation
3. Check test configurations for examples
4. Review error messages carefully - they're designed to be actionable

---

## License & Citation

**License:** MIT (or as specified by your institution)

**Citation:** If you use this model in academic work, please cite:
```
[Your Name]. (2025). HIV Transmission Model V5: A Population-Based
Discrete-Time Stochastic Model for HIV Prevention Intervention Analysis.
[Institution]. [Thesis/Publication details].
```

---

## Acknowledgments

Model inspired by STDSIM conceptual framework, reimplemented with modern computational practices.

Developed as part of thesis research at [Your Institution].

---

## Version

**Current Version:** 5.0 (February 2026)

**Last Updated:** 2026-02-10

**Status:** Production-ready with comprehensive validation

---

**For complete documentation, see [README_MODEL_GUIDE.md](documentation/README_MODEL_GUIDE.md)**
