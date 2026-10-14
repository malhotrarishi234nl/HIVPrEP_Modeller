# Forecast-window calibration run — 2026-09-21

> Archived under `documentation/reports/calibration/`; original filename retained.

## Decisions frozen before the run

- Keep `Heterosexual_Male = 1.0` as the reference.
- Estimate `orient_female` and `orient_msm` independently on `[0.2, 5.0]`; do not impose Female < Het_M < MSM.
- Do not add an orientation-by-activity interaction in this run.
- Use HIV diagnoses from 1996 onward as primary targets.
- Use AIDS diagnoses from 2000 onward as primary targets.
- Keep AIDS 1990 and 1995 as cross-validation/structural diagnostics; they never enter acceptance.
- Use target-relative model discrepancy sigma of 10% for primary scoring and report 20% as sensitivity only.
- Retain `I_max <= 3` as the primary pass rule.
- Treat 2015 as part of the earlier window only.

## Primary contract

The 13 primary targets are split into four search-ranking groups:

1. HIV diagnoses, 1996–2015: 1996, 2000, 2004, 2010, 2015.
2. AIDS diagnoses, 2000–2015: 2000, 2005, 2010, 2015.
3. HIV diagnoses after 2015: 2019, 2024.
4. AIDS diagnoses after 2015: 2019, 2022.

The four groups affect search ranking only. Acceptance remains target-by-target maximum implausibility across all 13 targets.

## What this run must show before adding interaction terms

The run records orientation-boundary diagnostics and activity-stratified infection and stock diagnostics. After discovery and fresh-seed confirmation, add an MSM activity-shape interaction only if all of the following are supported:

- meaningful primary tension remains after freeing both orientation multipliers;
- the remaining mismatch is systematically associated with MSM Low/Medium/High activity strata rather than a single year or the AIDS observation proxy;
- `orient_female` or `orient_msm` is not merely piling up at a newly chosen bound;
- the evidence indicates a shape mismatch that a single MSM scale cannot express.

If those conditions hold, the next structural sensitivity is restrained: fix `g_low = 1` and estimate only `g_medium` and `g_high`. If the current run fits without systematic activity-stratum tension, do not add those parameters.

## Frozen computational design

- 256 initial candidates plus 28 batches of 64 = 2,048 candidates.
- Five common-random-number discovery seeds per candidate = 10,240 discovery trajectories.
- Search has no early stopping based on success or failure.
- Up to 10 frozen-shortlist candidates may later receive 20 fresh confirmation seeds each.
- Discovery, smoke and confirmation seed sets are disjoint.
- The six-trajectory production smoke test passed before launch preparation.
- Smoke timing: 8.09 seconds per trajectory; with eight workers, the discovery run is expected to take roughly three hours, subject to machine load.
- Hard wall-time cap: six hours. A capped run is marked partial and must be resumed rather than silently treated as complete.

## Frozen artifacts

- Preparation: `outputs/calibration/coupled_forecast_windows_20260921_prepared`
- Passed smoke: `outputs/calibration/coupled_forecast_windows_20260921_smoke`
- Reserved search output: `outputs/calibration/coupled_forecast_windows_20260921_search`

## User-launched overnight command

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model"

caffeinate -dimsu .venv/bin/python run_coupled_calibration.py \
  --run-search \
  --prepared outputs/calibration/coupled_forecast_windows_20260921_prepared \
  --smoke-source outputs/calibration/coupled_forecast_windows_20260921_smoke \
  --output-dir outputs/calibration/coupled_forecast_windows_20260921_search \
  --workers 8
```

Do not launch confirmation until the search is complete and the frozen shortlist and diagnostics have been reviewed.
