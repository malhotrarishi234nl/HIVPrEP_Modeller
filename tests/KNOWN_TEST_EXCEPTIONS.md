# Known test exceptions

A listed test still **fails** and its assertion is unchanged. An exception only lets **one named launch gate** proceed despite that failure. It does not mark the test as passing, xfail or skipped, and it does not make the suite "fully passing".

## E-2026-09-17-01: deterministic MSM multiplier neutrality

| Field | Value |
|---|---|
| Test | `tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior::test_enabled_with_multiplier_one_matches_baseline` |
| Assertion | Unchanged: bit-identical MSM diagnoses with the MSM relative-diagnosis layer disabled vs enabled at multiplier 1.0 |
| Status | **FAILS** (deterministic mode, 1980–2000: 93,601 disabled vs 86,772 enabled ×1.0) |
| First executed | 2026-09-17, after the stale fixture path was corrected. The fixture had been in `configs/` since the initial commit, so this test had not run since then. |
| Scope of exception | Launch gate of `run_joint_fit_comparison_20260917.py` only. The gate tolerates exactly this test failing, with its failure message containing "Multiplier=1.0 should produce identical MSM diagnoses". Any other failure, error or skip in `test_diagnosis_year_bands.py` or `test_msm_relative_diagnosis_v13_10.py` blocks launch. |
| Why the exception is acceptable for that experiment | Every cell is asserted to run in stochastic mode on the enabled MSM path, so the experiment never switches between the two allocation paths. Stochastic spot check (20 seed-paired runs; `outputs/calibration/hm_joint_fit_conflict_20260917_rev1/msm_multiplier_neutrality_check.py`): difference −630 MSM diagnoses. Paired-by-seed SE 554 gives a 95% t CI of [−1.97%, +0.58%] of the disabled mean. An unpaired Welch SE would be 9,369 (CI about ±21%). This is consistent with neutrality at that precision; it is not proof of it. |
| Suspected cause (code reading, unverified) | `stochastic_allocation.deterministic_allocate_events` rounds the aggregate expected total per call and assigns leftover events by largest fractional part. One all-orientation call and two orientation calls therefore give different integer allocations from the same state. Which path is closer to expectation is undetermined. |
| Not covered | Any deterministic-mode run with the MSM layer enabled; any other experiment or gate. |
| Resolution owner | Separate deterministic-mode issue. The allocator is not changed within the joint-fit experiment. |

## E-2026-09-18-01: the same deterministic neutrality failure, scoped to the coupled search

| Field | Value |
|---|---|
| Test | `tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior::test_enabled_with_multiplier_one_matches_baseline` (the same test as E-2026-09-17-01) |
| Assertion | Unchanged. The test still **FAILS** and is neither xfailed nor skipped. |
| Scope of exception | Extended to the launch gate of `run_coupled_calibration.py` (`--smoke`, `--run-search`, `--confirm`), which reuses `run_joint_fit_comparison_20260917.run_diagnosis_test_gate`. Any other failure, error or skip in `tests/test_diagnosis_year_bands.py` or `tests/test_msm_relative_diagnosis_v13_10.py` still blocks launch. |
| Authority | Handoff `CLAUDE_COUPLED_SEARCH_HANDOFF_2026-09-18.md` section 9: "The known deterministic diagnosis multiplier-one issue must remain explicitly disclosed; establish the actual stochastic enabled path by smoke comparison." |
| Why it is acceptable for this search | Every evaluation runs in stochastic mode on the enabled MSM relative-diagnosis path; the runner asserts both at config level and at engine level, in every worker. The deterministic allocator path this test exercises is never entered. |
| What replaces it | The paired production-versus-harness smoke check (`outputs/calibration/coupled_20260918_smoke`, 3 pairs / 6 trajectories, seeds 197200-197202). All extracted summary columns, all extracted annual series and both final adult and childhood state tensors are bit-identical between the production engine and the observed harness engine at identical parameters and seeds. |
| Not covered | Any deterministic-mode run with the MSM layer enabled. The suite is NOT fully passing, and this exception does not claim otherwise. |
| Disclosure | Recorded verbatim in every smoke and confirmation manifest under `diagnosis_test_gate.disclosure`, with `suite_fully_passing: false`. |
