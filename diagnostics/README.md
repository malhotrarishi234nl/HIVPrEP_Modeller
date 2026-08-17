# Diagnostic Scripts

Analysis and debugging scripts for the HIV transmission model.

---

## Calibration Probes

Calibration-related one-off probe runners live in
`diagnostics/calibration_probes/`. These are diagnostics-only scripts for
auditing recent fit, seed-stage assumptions, ART-era mortality assumptions, and
small calibration sensitivity grids.

Run them from the project root, for example:

```bash
python diagnostics/calibration_probes/run_historical_seed_diagnostics.py --help
```

---

## Timestep Invariance Diagnostics

### convergence_test.py
**Purpose:** Comprehensive convergence proof for timestep-invariance fixes

**What it does:**
- Runs simulation with dt = 1.0, 0.5, 0.25, 0.167, 0.083 years
- Compares population trajectories over 45 years
- Aggregates vital rates by calendar year
- Proves monotonic convergence as dt → 0

**Usage:**
```bash
python diagnostics/convergence_test.py
```

**Expected output:**
- Population convergence table
- Births/deaths per year by dt
- Successive difference ratios
- Convergence verdict (✓ or ✗)

**Typical runtime:** ~30 seconds

---

### diagnose_linear_vs_exponential.py
**Purpose:** Mathematical proof of linear vs exponential time-scaling

**What it does:**
- Analytically compares linear and exponential scaling
- Shows why `p = r × Δt` is wrong for probabilities
- Demonstrates correct formula: `p = 1 - (1 - r)^Δt`
- Computes compound effects over multiple timesteps

**Usage:**
```bash
python diagnostics/diagnose_linear_vs_exponential.py
```

**Use case:** Understanding why Bug 1 (linear aging) caused divergence

---

### diagnose_mortality_rates.py
**Purpose:** Verify life-table mortality calculations

**What it does:**
- Validates life-table survival rates
- Checks age-specific mortality probabilities
- Compares discrete vs continuous formulations

**Usage:**
```bash
python diagnostics/diagnose_mortality_rates.py
```

---

### diagnose_time_invariance_failure.py
**Purpose:** Isolate which processes cause timestep divergence

**What it does:**
- Runs controlled experiments
- Toggles individual demographic processes on/off
- Identifies which module causes divergence
- Narrows down bug location

**Usage:**
```bash
python diagnostics/diagnose_time_invariance_failure.py
```

**Use case:** Bug hunting when new divergence appears

---

### quick_timestep_test.py
**Purpose:** Fast sanity check for timestep invariance

**What it does:**
- Quick test with step_size = 3, 6, 12 months
- Short duration (10 years)
- Basic convergence metrics

**Usage:**
```bash
python diagnostics/quick_timestep_test.py
```

**Typical runtime:** <10 seconds

---

## When to Use These Tools

| Scenario | Use This Script |
|----------|----------------|
| Verify fixes are working | `convergence_test.py` |
| New bug causing divergence | `diagnose_time_invariance_failure.py` |
| Understanding math of bug fix | `diagnose_linear_vs_exponential.py` |
| Quick sanity check | `quick_timestep_test.py` |
| Validate mortality rates | `diagnose_mortality_rates.py` |

---

## Expected Results (Post-Fix)

After Bug 1-3 fixes (Feb 2026):

**10-year divergence:** <1%
**45-year divergence:** ~8% (operator splitting artifact)

**Convergence pattern:** Monotonic decrease as dt → 0

**Births/deaths:** Nearly identical across timestep sizes

---

## Related Documentation

- Bug fixes: `../patches/TIME_INVARIANCE_FIX_V2.md`
- Order of operations: `../ORDER_OF_OPERATIONS.md`
- Test suite: `../tests/`

---

**Last Updated:** 2026-02-13
