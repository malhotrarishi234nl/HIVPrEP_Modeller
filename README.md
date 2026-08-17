# HIV Transmission Model — V11

**Author:** Rishi Malhotra — Thesis Research
**Status:** Active development
**Version:** 11.2
**Last updated:** 2026-03-03

---

## Overview

Discrete-time stochastic population model of HIV transmission in the Netherlands,
built for thesis research on HIV prevention interventions (PrEP cost-effectiveness).
Population flows from birth → childhood pool (ages 0–14) → sexual debut (age 15) →
orientation and activity assignment → HIV transmission system.

Calibration target: Dutch HIV epidemic 1970–2020 (RIVM / ATHENA cohort data).

---

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| V10.0 | 2026-02-26 | Config restructure 17→7 keys; ConfigReader; config_utils.py |
| V11.0 | 2026-03-03 | Contact balance fix; Garnett ε parameterisation (18 mixing params → 2) |
| V11.2 | 2026-03-03 | Parameter tier metadata (fixed/estimated/uncertain) on 22 params |

Previous versions archived in ../v10/.

---

## Project Structure

```
v11/
├── run_v3.py                  # Main simulation engine (entry point)
├── engine_v3.py               # SimulationEngineV3 + ConfigValidator
├── demography.py              # Aging, fertility, migration, childhood pool
├── migration.py               # Net migration module
├── transmission_corrected.py  # Per-act transmission probability calculations
├── output_writer.py           # CSV/XLSX output writer
├── config_reader.py           # V10-native config reader (ConfigReader class)
├── config_utils.py            # extract_value(), extract_tier(), get_params_by_tier(),
│                              #   validate_tier_schema()
├── schedule_utils.py          # Time-varying ART/PrEP schedule interpolation
├── state_snapshot.py          # StateSnapshot (baseline freezing — BACKLOG-4)
├── scenario_executor.py       # Scenario comparison runner
├── run_scenarios.py           # CLI entry point for scenario runs
├── validation.py              # Post-run validation utilities
├── regression_test.py         # Quick smoke test against v11_configuration.json
├── requirements.txt
│
├── configs/
│   ├── v11_configuration.json   # ← CANONICAL config (V11.2)
│   ├── v6_golden_reference.json # Golden reference for ConfigReader tests
│   ├── v10_configuration.json   # Legacy V10 config (used by legacy tests)
│   ├── v10_v6_rel_pop.json      # Legacy V6-format config (backward-compat tests)
│   └── v10_*.json               # Other legacy test configs
│
├── tests/
│   ├── test_config_reader.py    # 11 tests — ConfigReader V11 correctness
│   ├── test_contact_balance.py  #  6 tests — Garnett ε contact balance
│   ├── test_parameter_tiers.py  #  6 tests — V11.2 tier metadata schema
│   ├── test_migration.py        # Migration module tests
│   ├── test_art_dependent_progression.py
│   ├── test_births_deaths_by_timestep.py
│   └── [other test files]
│
├── diagnostics/               # One-off diagnostic scripts (not part of test suite)
├── outputs/                   # Simulation output CSVs
└── documentation/
    ├── BACKLOG.md             # ← All deferred items with priority + blocker notes
    ├── MASTER_REFERENCES.md   # ← All literature citations + mathematical formulas
    ├── DOCUMENTATION_INDEX.md
    ├── model_notes/           # Design decisions, implementation plans, discussions
    ├── patches/               # Patch notes for each version increment
    ├── features/              # Feature design docs
    ├── guides/                # User/developer guides
    ├── investigations/        # Root cause analyses and calibration investigations
    ├── quick_reference/       # One-page cheat sheets
    └── validation/            # Validation reports and test results
```

---

## Quick Start

Run base case (1970-2020 Dutch HIV simulation):
```bash
python regression_test.py
```

Run full V11 test suite:
```bash
python tests/test_config_reader.py
python tests/test_contact_balance.py
python tests/test_parameter_tiers.py
```

Load and inspect config programmatically:
```python
import json
from config_reader import ConfigReader
from config_utils import get_params_by_tier, validate_tier_schema

with open("configs/v11_configuration.json") as f:
    raw = json.load(f)

reader = ConfigReader(raw)
print(reader.get_config_version())      # "11.2"

# List all Tier 3 (uncertain/PSA) parameters
uncertain = get_params_by_tier(raw, "uncertain")
for path, entry in uncertain:
    print(f"{path}: {entry['value']}  [{entry['distribution']['type']}]")

# Validate tier schema compliance
violations = validate_tier_schema(raw)
assert violations == [], violations
```

---

## Model Architecture

### Population Tensor
Shape: (orientations=3, age_bins=20, activities=3, hiv_stages=4, prep_states=2)

- Orientations: Female | Heterosexual_Male | MSM
- Age bins: 15-19 through 80+ (sexually active); 0-14 in childhood pool
- Activities: Low (3 acts/yr) | Medium (10 acts/yr) | High (50 acts/yr)
- HIV stages: Susceptible | Acute | Chronic | AIDS
- PrEP states: None | OnPrEP

### Timestep
Quarterly (3-month steps), 1970-2020 = 200 steps.

### Contact Mixing (V11 — Garnett ε)
```
ρ(i→j) = ε·δᵢⱼ + (1−ε) · [N(j)·c(j)] / Σₖ[N(k)·c(k)]

ε_hetero = 0.20  (Dutch heterosexual network — RIVM SOA Monitor)
ε_msm    = 0.42  (Amsterdam MSM cohort — Xiridou et al. 2003)
```
Heterosexual contacts use a female-driven partnership pool (contact balance fix).
MSM contacts use a symmetric averaged pool.
See documentation/MASTER_REFERENCES.md §1.1.

### Parameter Tiers (V11.2)
All epidemiological parameters carry tier metadata:
- fixed     (12 params) — pinned from literature; not varied
- estimated  (4 params) — calibrated against Dutch data; have bounds + calibration_target
- uncertain  (6 params) — genuinely unknown; have distribution for future PSA runner

See documentation/MASTER_REFERENCES.md §4 for full parameter-to-reference mapping.

---

## Key Files for Development

| Task | File |
|------|------|
| Change transmission biology | configs/v11_configuration.json → disease.* |
| Change mixing assortativity | configs/v11_configuration.json → mixing.epsilon_* |
| Change PrEP scenario | configs/v11_configuration.json → interventions.* |
| Engine step loop | run_v3.py → _step_* methods |
| Config reading / validation | config_reader.py, config_utils.py, engine_v3.py |
| Tier introspection | config_utils.py → get_params_by_tier(), validate_tier_schema() |
| Backlog / next steps | documentation/BACKLOG.md |
| Literature / formulas | documentation/MASTER_REFERENCES.md |

---

## Open Backlog (Top Items)

Full register with blockers and priorities: documentation/BACKLOG.md

1. BACKLOG-2 — Calibration runner (close the 12% population gap at 2015)
2. BACKLOG-1 — PSA runner (sample Tier 3 uncertain params; requires calibration first)
3. BACKLOG-4 — Wire StateSnapshot baseline freezing for counterfactual analysis
4. BACKLOG-3 — Zero-population guard in migration.py (before PSA)
5. BACKLOG-6 — Fix mass balance test failure in test_high_prevalence_stress.py

---

## Dependencies

```
numpy
pandas
scipy       (future PSA runner)
openpyxl    (xlsx output)
```

Install: pip install -r requirements.txt

---

## References

All literature citations and mathematical formulas: documentation/MASTER_REFERENCES.md

Key references:
- Garnett & Anderson (1994) — ε mixing parameterisation, contact balance identity
- Boily et al. (2009) — per-act transmission probabilities
- Cohen et al. (2011) — ART transmission reduction (HPTN052)
- Bekker et al. / PURPOSE-1 (2024) — Lenacapavir efficacy
- Briggs et al. (2012) — ISPOR-SMDM parameter tier framework
- van Sighem et al. (2015) — ATHENA Dutch HIV monitoring data
