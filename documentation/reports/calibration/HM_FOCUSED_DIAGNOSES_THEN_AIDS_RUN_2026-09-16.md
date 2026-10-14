# Focused staged run: diagnoses first, then AIDS

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** validated (`--validate-only` passed), ready for Rishi to launch.
**Question:** can the *same* parameter sets reproduce MSM HIV diagnoses and RIVM MSM AIDS under the declared assumptions?
**Success:** joint wave-2 NROY members with explainable mechanisms. An empty joint set is a reportable limitation. Tolerances are not widened.

## Declared inputs

| Item | File |
|---|---|
| Plan | `configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json` |
| Base config | `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json` |
| Space (33 params) | `configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json` |
| Register v1.2 | `configs/hm_target_register_focused_20260916.json` (ECDC HIV 1990–95 now cross-validation, one-sided) |
| Warm start (22 rows) | `configs/hm_focused_warm_start_20260916.csv`, built by `tools/build_focused_warm_start_20260916.py` |

## Decisions carried

- **On-ART progression:** B, a two-era effective rate. It is **unconstrained** in ordering; the implied ordering among survivors will be reported.
- **ECDC 1990–95:** hold-out (`ECDC_EARLY_HIV_SOURCE_DECISION_2026-09-16.md`).
- **Variance:** predictive local, K = 5.
- **Discrepancy:** 10% primary, 20% sensitivity.
- **Non-MSM AIDS:** validation only.

## Design

- **Wave 1: SHM MSM diagnoses only**, 7 anchors. 600 LHS points over the full box + 22 warm-start proposals, × 5 seeds (93000–93004).
  - Warm-start rows: 20 best diagnosis-block points from the diagnostic run (selected without using AIDS fit), plus designs 25 and 342 from 2026-09-15.
  - 14 of those rows had their on-ART value clipped into the new recent range.
- **Wave 2: diagnoses + RIVM MSM AIDS**, 15 anchors. 400 points inside wave 1's NROY bounding box, thinned by proximity, × 5 seeds.
  - It runs only if wave 1 keeps ≥ 10 points. Otherwise the sequence stops for review.
- **Size:** about 5,100 simulations, roughly 1.5–2 h at 8 workers. It stops after about 55 min if wave 1 keeps fewer than 10.

## Code changes

- **`run_history_matching.py`:** `run_settings.supplementary_design_file` with a pinned SHA-256.
  - Rows are appended to wave 1 of a new sequence only.
  - Every parameter must be present, inside the box, and pass the LHS draw constraints.
  - Writes `design_sources.csv` and a provenance JSON.
- **Tests:** `tests/test_hm_supplementary_design_20260916.py` (6), including a synthetic two-wave staged run. 255 focused tests passed.

## Required post-run report

1. Joint NROY and binding targets per wave.
2. Implied `early` vs `recent` ordering among survivors (declining / flat / increasing).
3. One-sided ECDC 1990–95 check: share of survivors with model diagnoses ≥ observed.
4. Non-MSM AIDS cross-validation gap.
5. `variance_contract_stability` per wave.

## Launch (run from `hiv-prep-model/`, charger connected, lid open)

```bash
set -o pipefail
caffeinate -dims ./.venv/bin/python ./run_history_matching.py \
  --wave-plan configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json \
  --base-config configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json \
  --calib-space configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json \
  --register configs/hm_target_register_focused_20260916.json \
  --output-dir outputs/calibration/hm_focused_diagnoses_then_aids_20260916 \
  --workers 8 \
  --acknowledge-prerequisites \
  2>&1 | tee outputs/calibration/hm_focused_diagnoses_then_aids_20260916.console.log
```
