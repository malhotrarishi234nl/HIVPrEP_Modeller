# MSM-AIDS diagnostic wave: decision record and launch

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** validated (`--validate-only` passed), ready for Rishi to launch. No production simulation run by Claude.
**Plan:** `configs/hm_wave_plan_msm_aids_diagnostic_20260916.json`
**Register:** `configs/hm_target_register_rivm_msm_aids_20260916.json` (new file; the original register is unchanged)

## Decisions (Rishi, 2026-09-16)

1. **Variance contract: predictive.** V_ensemble(x) = s(x) + s(x)/K, where s(x) is a variance. The observed count and the simulator mean are treated as independent noisy realisations (Andrianakis et al. 2017, sections 3.2–3.3). s(x) is re-estimated at every design point from its own K = 10 replicates. The A-5 file is not used.
2. **The AIDS compartment is clinical AIDS. No recognition fraction is applied.** This was checked against source PDFs. `chronic_to_aids` is sourced to CASCADE (2000) and CASCADE (2003). Both use only the *clinical* part of the 1993 CDC definition: "people without an AIDS-defining disease but with a CD4-cell count below 200 cells/µL were not classified as having AIDS" (CASCADE 2000, methods). *Well-supported.*
3. **Scope: MSM only.** RIVM MSM AIDS is scored. RIVM non-MSM AIDS, total AIDS and ECDC AIDS are cross-validation only. No presentation layer is built.

## Correction to the earlier chat analysis

The A-5 variances were **not** inflated by the A-28 sampler defect. A-5 started at 2026-09-14 15:11 UTC. Its recorded `counter_based_rng.py` and `demography.py` hashes equal the current post-A-28 files. A-5 is still not used here, for different reasons:
- it is a p95 across three design points, with up to 127× variation across those points, so it is not s(x) at the scored point;
- it contains no variance for the new observed-AIDS proxy;
- the predictive contract needs an input-dependent s(x).

## What changed in code

| File | Change |
|---|---|
| `aids_observation.py` | `aids_first_diagnosis_proxy{,_msm,_non_msm}` = diagnosed-stage progression + endogenous HIV diagnosis in AIDS. Undiagnosed progression is excluded, which prevents double counting. A missing component is never zero-filled. This is an **assumption**, stated in the module. |
| `calib_extractor.py` | Adds the proxy per replicate, so its replicate variance includes the covariance between its two components. Existing outputs are untouched. |
| `hm_target_register.py` | New `rivm_aids_candidate_json` source. The candidate's SHA-256 is pinned. Flagged, incomplete or pooled records are refused. non-MSM = route total − MSM, an exact identity. |
| `hm_implausibility.py` | `implausibility_matrix_pointwise`: per-point variance. |
| `run_history_matching.py` | `run_settings.v_ensemble_mode = predictive_local`. Requires ≥ 5 replicates. Refuses to also name an A-5 file, and refuses derived outputs (they have no replicate variance). Refuses to drop an unresolved primary target. Writes a chi-square stability diagnostic, `variance_contract_stability.json`, plus a `variance_unstable` column in `nroy_members.csv`. The default (`a5_file`) behaviour is unchanged. |
| `tests/test_hm_predictive_variance_msm_aids_20260916.py` | 12 tests |

Verification:
- 248 focused tests passed.
- Synthetic end-to-end run of this exact plan and register completed, including cross-validation and sensitivities.
- One real two-seed simulation confirmed the new columns and `mcvar__` fields are produced.
- `tests/test_lhs_calibration.py` has 14 failures. They are identical with and without the extractor change, so they are pre-existing and not caused by it.

## Design

- **Sequence:** a new sequence starting at wave 1, over the **full prior box**. 400 LHS points × 10 CRN seeds (92000–92009) = **4,000 simulations**. A warm start from the 2026-09-15 NROY region was rejected: its bounding box spans 64–100% of every prior range, so it would buy little, and it would inherit a region selected under the ECDC AIDS construct mismatch.
- **Targets:** 21 primary.
  - SHM MSM diagnoses, 7 anchors (as before).
  - ECDC HIV 1990–95, 6 anchors (as before).
  - RIVM MSM AIDS, 8 anchors: 1990, 1995, 2000, 2005, 2010, 2015, 2019, 2022.
- **Discrepancy:** primary is 10% of the target, one sigma. Sensitivities are 20% of the target and the legacy 10% output-variance rule. The primary rule follows the 2026-09-15 audit recommendation. It is a declared assumption.
- **Expected runtime:** about 70–90 min at 8 workers. A-5 median was 7.9 s per run, and the 3,000-run waves took about 45–60 min.

## Things to read first when it finishes

1. `wave_01/variance_contract_stability.json`: how many primary decisions flip within the chi-square bounds of s(x).
2. `wave_01/per_target_implausibility.csv` for `rivm_aids_msm@1990/1995`. A two-seed pre-check at the old design 25 gave an MSM proxy of 78 (1990) and 175 (1995), against 318 and 314 observed. **This is not a result.** It shows the early-epidemic years are likely to bind. If they do, the next question is whether the cause is the pre-1996 diagnosis/progression dynamics or the observation assumption, not the variance allowance.
3. `wave_01/cross_validation/`: RIVM non-MSM AIDS is expected to be under-predicted, because C-1 entrants all enter Chronic. The size of that gap is the evidence that sizes the future presentation layer.
4. `wave_01/variance_budget.csv`: the median and p95 of s(x) per target, compared with Poisson scale.

## Validate (optional, already passed)

Run from `hiv-prep-model/`:

```bash
./.venv/bin/python ./run_history_matching.py \
  --wave-plan configs/hm_wave_plan_msm_aids_diagnostic_20260916.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --register configs/hm_target_register_rivm_msm_aids_20260916.json \
  --output-dir outputs/calibration/hm_msm_aids_diagnostic_20260916 \
  --workers 8 \
  --acknowledge-prerequisites \
  --validate-only
```

## Launch

```bash
mkdir -p outputs/calibration
set -o pipefail

caffeinate -i -s ./.venv/bin/python ./run_history_matching.py \
  --wave-plan configs/hm_wave_plan_msm_aids_diagnostic_20260916.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc.json \
  --register configs/hm_target_register_rivm_msm_aids_20260916.json \
  --output-dir outputs/calibration/hm_msm_aids_diagnostic_20260916 \
  --workers 8 \
  --acknowledge-prerequisites \
  2>&1 | tee outputs/calibration/hm_msm_aids_diagnostic_20260916.console.log
```

The output directory must not already exist or must be empty. The console log is written next to it, not inside it.
