> **Superseded for new runs (2026-09-22).** This file holds the commands for the **completed** focused stage-diagnosis calibration (1,780 trajectories, `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/`). It is kept because that run's report cites it. **Do not rerun these commands.** For the diagnosis triangulation run, use `documentation/diagnosis_triangulation_20260922/USER_RUN_COMMANDS.md`, which is also copied into `outputs/calibration/diagnosis_triangulation_orchestration_20260922/`.

# User-run commands: focused stage-structured diagnosis calibration (2026-09-22)

Every simulation below is **yours to launch**. The implementing agent constructed no engine
and ran no trajectory. Run each block in your own terminal (zsh). Each block starts with
`cd` into the repository root. The repository path contains spaces, including a trailing
space after `Thesis`, so it is quoted.

Each command:

- runs through `.venv/bin/python` explicitly;
- uses `set -o pipefail`, so a failure is not hidden by `tee`;
- creates its log directory before `tee`, and writes each log to a new timestamped file.

Outputs:

- the run: `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/`;
- the optional smoke test: `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922_smoke/`.

The harness **refuses to overwrite** either directory. It also refuses to rerun a COMPLETE
stage, to resume after any plan or code change, and to start Stage C unless you pass the
frozen shortlist hash.

**Runtime basis:** about 16.3 s per trajectory at 8 workers, measured on 2026-09-22 in an
interrupted first attempt. The brief assumed ~8 s. At your decision, the cap was raised from
45 to **75 minutes**. It counts cumulative active dispatch time: it starts at the first
Stage-A dispatch and excludes your review pause before Stage C. New work stops at
**72 minutes**, and the run is marked `PARTIAL_TIME_LIMIT` if incomplete. For multi-minute steps, `caffeinate -dimsu` is offered
in front of the Python command; delete it if you don't want it.

| Step | Trajectories | Approx. time at 8 workers |
|---|---:|---:|
| 0 prepare | 0 | < 1 min |
| 1 smoke (optional; outside the 1,780 budget) | 6 | ~2 min serial |
| 2 Stage A | 300 | ~10 min |
| 3 Stage B | 1,280 | ~44 min |
| 4 freeze shortlist | 0 | < 1 min |
| 5 Stage C (after review) | ≤ 200 | ≤ ~7 min |
| 6 report | 0 | ~1–2 min |
| **total** | **≤ 1,780** | **~60 min** |

---

## (Optional) Re-run validate-only yourself

This constructs no engine, runs no simulation, and writes only the log.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --validate-only 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/validate_$(date +%Y%m%d_%H%M%S).json"
```

## 0. Prepare (no simulation)

Creates the new output directory. It freezes `frozen_plan.json`, `design.json`,
`stage_b_design.csv`, `validation.json`, `manifest.json` and `IMPLEMENTATION_NOTE.md`, and
fails if the directory already exists.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --prepare --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/prepare_$(date +%Y%m%d_%H%M%S).log"
```

## 1. Optional smoke test: 6 trajectories, ~2 minutes, serial

For each backbone, on seed 201000, this runs three trajectories:

- legacy control;
- enabled identity with the 1985 boundary, which must be bit-identical to the legacy control;
- the ρ=0.80/AIDS=0.35 warm Stage-B vector, which must show zero AIDS-stage diagnoses before 1985.

It also prints a runtime estimate for the full design. It writes only the smoke directory.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --smoke --smoke-output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922_smoke 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/smoke_$(date +%Y%m%d_%H%M%S).log"
```

## 2. Stage A: corrected moderate response surface (300 trajectories, ~10 min)

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage a --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_a_$(date +%Y%m%d_%H%M%S).log"
```

Resume Stage A after an interruption. Journaled trajectories are kept, and a COMPLETE
stage is refused:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage a --resume --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_a_resume_$(date +%Y%m%d_%H%M%S).log"
```

## 3. Stage B: focused four-dimensional discovery (1,280 trajectories, ~44 min)

This requires a COMPLETE Stage A.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage b --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_b_$(date +%Y%m%d_%H%M%S).log"
```

Resume Stage B:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage b --resume --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_b_resume_$(date +%Y%m%d_%H%M%S).log"
```

## 4. Freeze the Stage-C shortlist (no simulation)

This requires a COMPLETE Stage B. It writes `frozen_shortlist.json`,
`discovery_candidates.csv` and `discovery_target_residuals.csv`, and prints
`shortlist_sha256`. It refuses a partial discovery archive and never overwrites a
frozen shortlist.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --freeze-shortlist --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/freeze_shortlist_$(date +%Y%m%d_%H%M%S).log"
```

An interim report is optional; it includes the Stage-A surface and discovery figures:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --report --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/report_interim_$(date +%Y%m%d_%H%M%S).log"
```

**Review `frozen_shortlist.json` before step 5.** It lists the two backbone-identity
comparators and at most eight mechanism tuples, with the rule that selected each one.

## 5. Stage C: fresh-seed confirmation (≤ 200 trajectories, ≤ ~7 min)

This is kept separate on purpose. Replace `PASTE_SHORTLIST_SHA256` with the hash printed in
step 4; it is also recorded as `shortlist_sha256` in `manifest.json`. The run refuses a
wrong or missing hash.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage c --shortlist-sha256 PASTE_SHORTLIST_SHA256 --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_c_$(date +%Y%m%d_%H%M%S).log"
```

Resume Stage C:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --stage c --resume --shortlist-sha256 PASTE_SHORTLIST_SHA256 --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/stage_c_resume_$(date +%Y%m%d_%H%M%S).log"
```

## 6. Final report (no simulation)

Writes `REPORT.md`, all the required CSVs, `identifiability.json` and at most four figures.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --report --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/report_$(date +%Y%m%d_%H%M%S).log"
```

---

## Alternative: resumable one-command orchestration

Run step 0 first; the orchestrator never prepares.

**First invocation (A → B → freeze → interim report, up to 1,580 trajectories, ~54 min).**
It stops after freezing the shortlist and prints the hash. It skips COMPLETE stages and
resumes incomplete ones, so the same command is also the resume command for A and B.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --orchestrate --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/orchestrate_$(date +%Y%m%d_%H%M%S).log"
```

**After reviewing the shortlist (C → final report, ≤ 200 trajectories).** The same
command with the hash also resumes C:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && caffeinate -dimsu .venv/bin/python run_stage_structured_diagnosis_focused_calibration.py --orchestrate --shortlist-sha256 PASTE_SHORTLIST_SHA256 --workers 8 --output-dir outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/orchestrate_confirm_$(date +%Y%m%d_%H%M%S).log"
```

---

## Engine-constructing tests the agent did not run (~1–3 min)

The command collects 20 tests: the 19 that build a real `SimulationEngineV3` plus one
non-engine test in the same class. They use short 1980-config engines, not
calibration. They include the prior audit's exact identity test.
`test_enabled_with_multiplier_one_matches_baseline` is the documented known failure
E-2026-09-17-01, so expect it to fail.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python -m pytest -q -rf tests/test_stage_structured_diagnosis_audit.py::test_disabled_and_enabled_identity_are_exact_under_paired_seed "tests/test_aids_observation.py::test_two_step_smoke_diagnostics_do_not_change_legacy_outputs" tests/test_diagnosis_year_bands.py::TestBeforeFirstAnchor tests/test_diagnosis_year_bands.py::TestEngineScheduleIntegration tests/test_diagnosis_year_bands.py::TestFlatFallback tests/test_msm_relative_diagnosis_v13_10.py::TestBuildMsmRelativeDiagnosisSchedule tests/test_msm_relative_diagnosis_v13_10.py::TestDeterministicMode tests/test_msm_relative_diagnosis_v13_10.py::TestMultiplierAboveOne tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/engine_tests_$(date +%Y%m%d_%H%M%S).log"
```

The synthetic and mocked suites the agent did run can be repeated with:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/stage_structured_diagnosis_focused_calibration_20260922 && .venv/bin/python -m pytest -p pytest_no_engine_guard -rs -q tests/test_stage_structured_diagnosis_focused_calibration.py 2>&1 | tee "logs/stage_structured_diagnosis_focused_calibration_20260922/unit_tests_$(date +%Y%m%d_%H%M%S).log"
```

## Notes

- **Do not edit code between `--prepare` and the report.** Every stage re-checks runtime
  module hashes, including the harness, `run_v3.py` and `stage_structured_diagnosis.py`, and
  refuses to continue on any drift. A code change needs a new output directory and a new
  `--prepare`.
- **If a trajectory fails,** its stage becomes `FAILED` and `failures_<stage>.csv` is written.
  Nothing is retried or substituted.
- **If the 72-minute stop is reached,** the stage and the manifest become
  `PARTIAL_TIME_LIMIT`. The report then marks the design incomplete and cannot issue
  "not supported".
