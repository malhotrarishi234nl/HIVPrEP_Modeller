# ART-split decomposition rerun (to-do item 3)

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** validated, ready for Rishi to launch. It is not a history-matching wave and scores nothing.
**Script:** `tools/run_art_split_decomposition_20260916.py`
**Size:** 10 parameter vectors × seeds 92000–92009 = 100 simulations, about 15 min at 8 workers.

## Selection (declared in the script before running)

- **5 lowest-I_max vectors** from the diagnostic run: rows 248, 205, 191, 203, 130.
- **5 vectors nearest the 5/25/50/75/95% quantiles of `chronic_to_aids_on_art`**: rows 351, 396, 102, 43, 261. Values run from 0.0019 to 0.0143.

## Outputs (`outputs/calibration/art_split_decomposition_20260916/`)

- **`implied_progression_hazards.csv`**, per vector × group × year:
  - diagnosed Chronic→AIDS events and person-years, off and on ART;
  - the implied hazards;
  - the parameter values converted to hazards;
  - the on-ART share of diagnosed person-time and of post-diagnosis AIDS events.
- **`regression_check_against_diagnostic_run.csv`**: the new counters use no random numbers, so the 10-seed means of `new_diagnoses_msm`, `aids_first_diagnosis_proxy_msm` and `new_aids_cases` must reproduce the diagnostic run **exactly**. `manifest.json` reports `regression_exact`. If it is false, stop and report before interpreting anything.

## What this can and cannot answer

- **The implied hazards will closely match the parameter hazards by construction.** The model draws binomial progression from those rates. That agreement is a mechanics check, not evidence about the rates.
- **The informative quantities are compositional.** How much post-diagnosis AIDS comes from people on ART versus off ART, and how large the diagnosed off-ART pool is in each era.
- **What it sets up:** a comparison of absolute AIDS-in-care against external evidence, which is item 4.
- A one-seed smoke test ran without errors. At row 248 (MSM), on-ART events were 2% of post-diagnosis AIDS in 1996, 22% in 2000, 59% in 2010 and 73% in 2022. **This is illustrative only, not a result.**

## Validate (already passed)

```bash
./.venv/bin/python tools/run_art_split_decomposition_20260916.py --validate-only
```

## Launch (run from `hiv-prep-model/`, charger connected)

```bash
set -o pipefail
caffeinate -dims ./.venv/bin/python tools/run_art_split_decomposition_20260916.py --workers 8 \
  2>&1 | tee outputs/calibration/art_split_decomposition_20260916.console.log
```
