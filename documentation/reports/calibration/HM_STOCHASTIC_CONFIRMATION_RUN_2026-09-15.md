# Stochastic confirmation of Wave 2 designs 25 and 342

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** validated and ready for Rishi to launch; no simulations run by Codex.

## What the IDs mean

`25` and `342` are row identifiers for two different 32-parameter vectors in
Wave 2's `evaluated.csv`. They are not random seeds. Both parameter vectors were
originally simulated with the common seeds 91000, 91001, and 91002. They were
the only Wave 2 vectors retained under both the 10% and 20% target-relative
discrepancy sensitivities.

The confirmation run holds each vector's 32 parameters fixed and simulates it
under 50 fresh seeds (191000 through 191049). This separates a stable expected
trajectory from a three-seed mean that may have looked favorable by chance.

## Outputs

- `selected_parameters.csv`: the exact two parameter vectors.
- `raw_replicates.csv`: one row for every parameter-vector/seed simulation.
- `confirmed_means.csv`: 50-seed means and within-vector sample variances.
- `original_vs_confirmed.csv`: original three-seed versus new 50-seed target means, including a
  standardized shift showing whether the original mean was unusually favorable relative to the
  newly estimated per-run variability.
- `target_scores_target_relative_10pct.csv` and `...20pct.csv`: target-level scores.
- `confirmation_summary.csv`: I_max, binding target, and NROY decision per vector/rule.
- `RUN_SUMMARY.md` and `manifest.json`: concise result and full provenance.

The recalculated variance budget uses A-5's independently measured per-run
V_ensemble divided by 50. The empirical standard error from the 50 confirmation
runs is also exported as a stability diagnostic; it is not silently substituted
for the prespecified A-5 term.

## Validate

Run from `hiv-prep-model/`:

```bash
./.venv/bin/python ./run_hm_confirmation.py \
  --source-run outputs/calibration/hm_focused_continuation_20260915 \
  --design-ids 25 342 \
  --source-sensitivity target_relative_10pct \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --register configs/hm_target_register.json \
  --v-ensemble outputs/hta_results/a5_ensemble_variance_20260914_150556/raw/recommended_v_ensemble.csv \
  --output-dir outputs/calibration/hm_confirmation_wave2_designs_25_342 \
  --replicates 50 \
  --simulation-seed 191000 \
  --workers 8 \
  --validate-only
```

## Launch

```bash
mkdir -p outputs/calibration
set -o pipefail

caffeinate -i -s ./.venv/bin/python ./run_hm_confirmation.py \
  --source-run outputs/calibration/hm_focused_continuation_20260915 \
  --design-ids 25 342 \
  --source-sensitivity target_relative_10pct \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --register configs/hm_target_register.json \
  --v-ensemble outputs/hta_results/a5_ensemble_variance_20260914_150556/raw/recommended_v_ensemble.csv \
  --output-dir outputs/calibration/hm_confirmation_wave2_designs_25_342 \
  --replicates 50 \
  --simulation-seed 191000 \
  --workers 8 \
  2>&1 | tee outputs/calibration/hm_confirmation_wave2_designs_25_342.console.log
```

Because each seed is dispatched independently, all eight workers can be used.
The source run is checksum-verified and remains read-only. The runner refuses a
non-empty output directory and rejects IDs outside the named sensitivity set.
