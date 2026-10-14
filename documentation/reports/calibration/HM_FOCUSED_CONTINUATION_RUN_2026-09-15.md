# Focused history-matching continuation — decision record

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** validated and ready for Rishi to launch; no simulations run by Codex.

## Purpose

Continue from the 46 primary NROY members in
`outputs/calibration/hm_baseline_learning_20260915` without repeating wave 1.
The new wave is numbered wave 2 and writes to a separate output directory. The
parent run remains read-only.

The runner verifies every artefact hash in the parent manifest, the final NROY
row count and cutoff, the absence of parent simulation errors, the complete
base-config/calibration-space/target-register/V_ensemble identity, and runtime
module compatibility before generating a design. It also requires the new plan
to begin at exactly the next wave index.

## Focused design

- Parent primary NROY members: 46.
- Proposed wave-2 points: 1,000.
- Deterministic validation preview after proximity thinning: 974.
- Replicates per evaluated point: 3, with the same CRN seeds as wave 1.
- Primary rule: the parent's 0.10 output-variance discrepancy rule.
- Prespecified diagnostics: 10% and 20% target-relative discrepancy.
- Expected runtime at eight workers: approximately 45–60 minutes based on wave 1.

The bounding-box-plus-proximity design is a conservative superset of the
observed NROY cloud. Points outside the true NROY region may be evaluated and
ruled out; this is expected and must not be hidden by widening tolerances.

## Launch

Run from `hiv-prep-model/`. The validation command has already passed, but it is
safe to repeat:

```bash
./.venv/bin/python ./run_history_matching.py \
  --wave-plan configs/hm_wave_plan_focused_continuation_20260915.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --continue-from outputs/calibration/hm_baseline_learning_20260915 \
  --output-dir outputs/calibration/hm_focused_continuation_20260915 \
  --workers 8 \
  --acknowledge-prerequisites \
  --validate-only
```

Then launch the simulation:

```bash
mkdir -p outputs/calibration
set -o pipefail

caffeinate -i -s ./.venv/bin/python ./run_history_matching.py \
  --wave-plan configs/hm_wave_plan_focused_continuation_20260915.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --continue-from outputs/calibration/hm_baseline_learning_20260915 \
  --output-dir outputs/calibration/hm_focused_continuation_20260915 \
  --workers 8 \
  --acknowledge-prerequisites \
  2>&1 | tee outputs/calibration/hm_focused_continuation_20260915.console.log
```

Do not reuse a non-empty continuation output directory. The console log is kept
next to the output directory so `tee` does not make the directory non-empty
before the runner checks it.

## Future continuations

For a later wave, create a new declared plan whose first wave index is one above
the parent run's final wave, point `--continue-from` to the completed parent run,
and use a new output directory. Each continuation manifest records the parent
manifest and NROY hashes, giving the sequence a verifiable provenance chain.
