# Adverse Events Module — HTA Phase 3

## Overview

The `AdverseEventModule` tracks PrEP-related adverse events and computes their economic impact (cost additions and QALY decrements) for HTA analyses. It supports two operating modes:

1. **Prevalence Mode** (default, simple): Fixed AE prevalence applied to susceptible PrEP users
2. **State Machine Mode** (advanced, optional): Stochastic onset/resolution with severity stratification

Both modes output quarterly-adjusted cost and utility impacts ready for discount factor application and accumulation.

---

## Installation & Integration

### File Location
```
/sessions/sleepy-jolly-bohr/mnt/Modeller/v11/adverse_events.py
```

### Dependencies
- `numpy` (array operations, stochastic draws)
- `config_utils.extract_value()` (configuration parsing)

### Usage in Economics Accumulator

```python
from adverse_events import AdverseEventModule

# During initialization (in MultiScenarioRunner or Engine)
hta_config = {...}  # Full HTA configuration
groups = engine.groups  # PopulationGroups object
ae_module = AdverseEventModule(hta_config, groups, rng=engine.rng)

# Per step (in economics accumulation phase)
adjustments = ae_module.compute_adjustments(
    population=engine.state.population,
    step=step,
    year=current_year
)

# Use the adjustments
step_utility_loss = adjustments["utility_decrement"]
step_cost_addition = adjustments["cost_addition"]
```

---

## Configuration

### Prevalence Mode (Simple)

Minimal configuration: specify prevalence, utility decrement, and annual cost per PrEP type.

```json
{
  "adverse_events": {
    "mode": "prevalence",
    "oral_prep": {
      "ae_prevalence": {"value": 0.15, "notes": "% of users experiencing any AE in a year"},
      "utility_decrement": {"value": -0.02, "notes": "QALY decrement per affected user per year"},
      "annual_cost_addition": {"value": 200, "notes": "Additional cost per affected user per year"}
    },
    "lenacapavir": {
      "ae_prevalence": {"value": 0.20},
      "utility_decrement": {"value": -0.01},
      "annual_cost_addition": {"value": 150}
    }
  }
}
```

**Calculation (per step):**
```
prep_users = number of susceptible users on this PrEP
utility_decrement += prep_users × ae_prevalence × |utility_decrement| × 0.25
cost_addition += prep_users × ae_prevalence × annual_cost_addition × 0.25
```

### State Machine Mode (Advanced)

Full stochastic AE dynamics with severity stratification.

```json
{
  "adverse_events": {
    "mode": "state_machine",
    "oral_prep": {
      "onset_rate_per_year": 0.25,
      "resolution_rate_per_year": 0.50,
      "severity_distribution": {
        "mild": 0.70,
        "moderate": 0.25,
        "severe": 0.05
      },
      "utility_decrements": {
        "mild": -0.01,
        "moderate": -0.05,
        "severe": -0.15
      },
      "cost_additions": {
        "mild": 100,
        "moderate": 500,
        "severe": 2000
      }
    }
  }
}
```

**Per-Step Dynamics:**

1. **New Onsets** (stochastic, or expected value if `deterministic_mode=true`):
   - Draw new AE cases: `binomial(non_ae_users, onset_rate_quarterly)`
   - Distribute by severity according to `severity_distribution`
   
2. **Resolutions**:
   - Draw resolved cases: `binomial(current_ae_count, resolution_rate_quarterly)`
   - Remove proportionally from each severity grade

3. **Impact Calculation**:
   - For each severity grade: `count × |utility_decrement| × 0.25`
   - For each severity grade: `count × annual_cost × 0.25`

---

## Return Values

Both modes return:
```python
{
    "utility_decrement": float,    # Positive value subtracted from QALYs (quarterly-adjusted)
    "cost_addition": float         # Positive cost added to total (quarterly-adjusted)
}
```

**Important:** Values are already multiplied by `step_fraction = 0.25` (quarterly timestep). 
The caller applies discount factors before accumulation.

---

## Configuration Parsing

The module uses `config_utils.extract_value()` to handle both wrapped and direct config values:

```python
# Both of these work:
{"ae_prevalence": {"value": 0.15, "notes": "..."}}
{"ae_prevalence": 0.15}
```

Parameters can include optional metadata (notes, tier, source, bounds, distribution, etc.).

---

## Edge Cases & Defaults

| Scenario | Behavior |
|----------|----------|
| Zero population | Returns `{"utility_decrement": 0, "cost_addition": 0}` |
| Missing AE config for a PrEP type | Skips that PrEP type |
| `ae_prevalence = 0` | No AE impact for that PrEP type |
| Invalid mode (not "prevalence" or "state_machine") | Raises `ValueError` |
| State machine with `deterministic_mode=true` | Uses expected values, not binomial draws |

---

## Testing

Comprehensive test suite in `test_adverse_events.py`:

```bash
python3 test_adverse_events.py
```

Tests cover:
- Mode detection and initialization
- Prevalence calculations (single and multiple PrEP types)
- State machine onset/resolution dynamics
- Severity distribution
- Configuration parsing
- Edge cases and error handling

---

## Design Notes

### Why Quarterly Adjustments?
The HIV model uses quarterly timesteps. AE rates are specified annualized (per year) for readability. 
The module applies `step_fraction = 0.25` automatically. The caller (economics accumulator) applies 
discount factors **after** receiving these quarterly values.

### Why Prevalence Mode First?
Prevalence mode is defensible in health economic analysis. It requires fewer assumptions and parameters. 
State machine mode adds complexity but is appropriate for conditions where timing and remission matter.

### Stochastic vs. Deterministic
When `simulation_controls.deterministic_mode = true`, state machine mode uses expected values instead of 
binomial draws. This enables deterministic testing and scenario analysis without variability.

### Why Only Susceptible Users?
AE parameters are defined for PrEP-using susceptible individuals. Infected individuals (acute/chronic/AIDS) 
are assumed to have different AE profiles that would require separate parameterization. This is a design choice 
to keep the module focused and avoid over-parameterization.

---

## Example: Full HTA Config with AE

```json
{
  "simulation_controls": {
    "deterministic_mode": false
  },
  "adverse_events": {
    "mode": "prevalence",
    "oral_prep": {
      "ae_prevalence": {
        "value": 0.15,
        "tier": "uncertain",
        "source": "PrEP-G trial",
        "bounds": [0.10, 0.25]
      },
      "utility_decrement": {
        "value": -0.02,
        "tier": "fixed",
        "source": "expert elicitation"
      },
      "annual_cost_addition": {
        "value": 200,
        "tier": "uncertain",
        "source": "Dutch healthcare costs"
      }
    },
    "lenacapavir": {
      "ae_prevalence": {
        "value": 0.20,
        "tier": "uncertain",
        "source": "ECLAIR trial"
      },
      "utility_decrement": {
        "value": -0.01
      },
      "annual_cost_addition": {
        "value": 150
      }
    }
  }
}
```

---

## Debugging & Diagnostics

### State Machine Mode: Check Current State
```python
summary = ae_module.get_state_summary()
# Returns: {"Oral_PrEP": {"mild": 10, "moderate": 5, "severe": 2, "total": 17}, ...}
```

### Verify Configuration
```python
print(f"Mode: {ae_module.mode}")
print(f"Deterministic: {ae_module.deterministic}")
print(f"Configured PrEP types: {list(ae_module.prep_ae_config.keys())}")
```

---

## Future Extensions

1. **Time-Varying Parameters**: Modify config to support `ae_prevalence(year)` functions
2. **Comorbidity Interactions**: Track interactions between multiple AE types
3. **ART-Induced AE**: Extend to track ART-specific adverse events on infected cohorts
4. **Multi-Step AE Progression**: Model transitions between severity grades (mild → moderate → resolution)

---

## References

- Health economic evaluation: NICE guidelines on cost-effectiveness analysis
- AE epidemiology: PreExposure Prophylaxis (PrEP) literature (e.g., PROUD, IPERGAY, PrEP-G)
- Model architecture: HIVTransmissionModel V11, Phase 3 documentation

