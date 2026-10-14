# History-matching baseline learning run — decision record

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** prepared for Rishi to launch; not run by Codex.  
**Purpose:** establish post-fix baseline fit diagnostics and test the complete stochastic
history-matching path before defining a final production ensemble.

## What this run decides

- History matching, not posterior inference. The output is an NROY set with no probability weights.
- One bounded wave: 1,000 Latin-hypercube points in the existing 32-parameter 1975 space.
- Three stochastic replicates per point, averaged before scoring, with the same three seeds at every
  point. This estimates the required `E[f(x)]` and uses CRN to reduce irrelevant between-point noise.
- A-5's independently measured per-run simulator variance enters as `V_ensemble / 3`.
- Primary targets are directly observed Tier-1 counts only: MSM diagnoses, the non-overlapping
  1990-1995 ECDC HIV series, and AIDS cases.
- SHM total diagnoses are excluded because C-1 makes their residual collinear with MSM diagnoses and
  partly scores an injected input. Non-MSM diagnoses are excluded because C-1 reproduces them by
  construction. UNAIDS/Spectrum outputs remain cross-validation comparators.
- Nineteen prespecified anchor years are scored instead of every correlated annual observation.
- `I_max <= 3` is the primary non-implausibility rule. An empty primary NROY is reported, never repaired
  by changing a tolerance after the run.

## What V_disc means in this run

`V_disc` is an allowance for the fact that even at the best parameter values, the model is a simplified
representation of the Dutch epidemic. It is not sampling error and it is not stochastic simulation
noise. Without it, the calculation implicitly claims that the model could reproduce every observed
target exactly if only the parameters were correct.

The run computes two prespecified readings from the same simulations:

1. **Primary learning rule:** `0.10 x Var(outputs across the wave)`, matching the documented illustrative
   rule from Andrianakis et al. This determines whether the loop would continue.
2. **Sensitivity only:** `(0.10 x observed target)^2`, an interpretable one-standard-deviation allowance
   of 10% of each target. It is written below each wave under `sensitivity_target_relative_10pct/` and
   cannot continue or rescue the primary run.

If these rules produce very different NROY sizes or binding targets, the run has done its job: the final
calibration is sensitive to model-discrepancy judgment, and that decision must be defended before a larger
wave. No additional simulation is needed to compare them.

## Why this is not the final production ensemble

- One thousand points provide only a first view of a 32-dimensional space.
- The existing 1975 space retains diagnostic bounds for the epidemic seed. The final space should adopt
  the wider union bounds recommended in the B-1/B-2 audit.
- Period-specific demographic inputs for 1975-1980 remain a recorded limitation.
- Three replicates are a compute-conscious estimator for this learning run, not evidence that three is
  the final optimal count.

## Expected exports

`wave_01/evaluated.csv` contains one row per parameter point, parameter values, replicate-mean annual
outputs, `mcvar__*` within-point variance columns, replicate count/seeds, and any error. The wave directory
also contains primary NROY members, per-target implausibility, binding-target counts, complete variance
budgets, retained parameter ranges, and a summary. The sensitivity subdirectory repeats the scoring files
under the target-relative discrepancy rule. The top-level manifest hashes every input and output.

## Launch contract

Run from `hiv-prep-model/` with the project virtual environment. Use a new, empty output directory.
Four workers are the conservative starting point given the prior memory-pressure observations.

First validate the complete contract without running a simulation:

```bash
.venv/bin/python run_history_matching.py \
  --wave-plan configs/hm_wave_plan_baseline_learning_20260915.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --output-dir outputs/calibration/hm_baseline_learning_20260915 \
  --workers 4 \
  --acknowledge-prerequisites \
  --validate-only
```

Then launch the actual bounded wave:

```bash
.venv/bin/python run_history_matching.py \
  --wave-plan configs/hm_wave_plan_baseline_learning_20260915.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --output-dir outputs/calibration/hm_baseline_learning_20260915 \
  --workers 4 \
  --acknowledge-prerequisites
```

Do not reuse that output directory after a partial or failed attempt; the runner refuses a non-empty
directory so a partial run cannot be mistaken for a complete one.
