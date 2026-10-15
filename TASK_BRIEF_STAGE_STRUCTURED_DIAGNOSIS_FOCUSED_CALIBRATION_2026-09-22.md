# Task brief: focused stage-structured diagnosis calibration and decision harness

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

Date: 2026-09-22  
Status: prepare-only handoff; implementation, unit tests, and validate-only are authorized, but no simulation launch is authorized  
Wall-time budget: 45 minutes at up to 8 workers  
Scientific status: structural investigation, not production calibration and not an HTA baseline

## Decision to be made

Determine whether a literature-consistent diagnosis mechanism can materially improve the post-2015 MSM diagnosis trajectory while retaining acceptable historical HIV-diagnosis, clinical-AIDS-proxy, mortality, and 2025 latent-state behavior.

The previous 240-trajectory audit must not be summarized as a null result. It established that diagnosis timing has substantial leverage:

- candidate 1326, `rho=0.60 / AIDS=0.85`: mean 2024 MSM diagnoses 316.7 versus 318 observed;
- candidate 1788, the same profile: 300.0 versus 318 observed;
- `rho=0.80 / AIDS=0.35` improved the 2024 error for both candidates, improved primary `I_max`, and passed the frozen 13-target primary contract for both candidates;
- the extreme profiles overproduced the clinical-AIDS proxy, infections, and deaths.

The earlier conclusion “mechanism not supported” meant that no coarse profile passed every additional promotion safeguard. It did not mean that the mechanism failed to improve fit.

This follow-up must answer:

1. Is there a moderate, stable region—not just an extreme point—that improves 2024 diagnoses?
2. Can the existing MSM diagnosis-era multipliers readjust around the stage mechanism without an identifiability free-for-all?
3. Does the mechanism improve the unchanged primary target contract after fresh-seed confirmation?
4. What happens to infections, deaths, ART stock, and the 2025 starting state?
5. What can and cannot be said about late HIV presentation, given that clinical AIDS is narrower than CD4-based late diagnosis?

## Evidence and interpretation contract

Read before implementation:

- `TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_AUDIT_2026-09-22.md`
- `outputs/calibration/stage_structured_diagnosis_audit_20260922/REPORT.md`
- `outputs/calibration/stage_structured_diagnosis_audit_20260922/profile_summary.csv`
- `HM_PRESENTATION_EVIDENCE_AND_COMPOUND_DESIGN_2026-09-16.md`
- `RIVM_AIDS_TARGET_WINDOW_AUDIT_2026-09-21.md`
- `run_v3.py::_step_diagnosis`
- `stage_structured_diagnosis.py`
- `aids_observation.py`

The mechanistic literature in `../literature/` supports heterogeneous testing and missed diagnosis opportunities:

- `Bilsen(2021)-TestingPracticesLateStage.pdf`;
- `Berdet(2021)-ReasonsLateStageDiagnosis.pdf`;
- `Jordans(2024)-ProperTestingGuidnelinesHIV.pdf`;
- `Koole(2025)_PrEPBarriersMissedOpportunitiesMSM.pdf`.

Use those papers to justify the structure, not to invent precise national hazard values. They describe missed repeat testing, provider-level missed opportunities, low perceived risk, access barriers, and some window-period events. They do not identify one Dutch annual false-negative probability.

### Late presentation is not the current AIDS compartment

Maintain three distinct constructs:

1. HIV diagnosis while in the model's clinical-AIDS compartment;
2. the established RIVM AIDS proxy: diagnosed-stage progression plus HIV diagnosis in AIDS;
3. surveillance “late diagnosis,” commonly CD4 `<350` or an AIDS-defining illness.

Do not call construct 1 or 2 “all late diagnoses.” SHM 2022–2024 reports 238 late-stage diagnoses among 742 stage-classifiable MSM diagnoses (32.1%), but its group-specific clinical-AIDS share is not separately observed. Use 32.1% only as a one-sided contextual upper bound on the model fraction diagnosed in clinical AIDS, not as an equality target. The pooled 208 clinical-AIDS cases among 1,301 stage-classifiable diagnoses are cross-validation context only, not an MSM calibration target.

## Mechanism

Retain the implemented cause-specific hazard form:

```text
p_routine_MSM(t) = clip(p_base(t) * m_MSM(t), 0, 1)
lambda_routine(t) = -log(1 - p_routine_MSM(t))
lambda_symptom     = -log(1 - p_AIDS_symptom)

lambda_total(s,t) = lambda_routine(t)
                  + I(s == AIDS) * lambda_symptom

p_step(s,t) = 1 - exp(-lambda_total(s,t) * years_per_step)
```

For the focused calibration, set `routine_hazard_scale = 1`. Do not estimate it together with all MSM-era multipliers: that would introduce a redundant scale direction.

The MSM-relative multiplier applies only to routine diagnosis. It must not multiply the AIDS symptom hazard. Acute and Chronic receive no symptom uplift in this experiment.

### Availability boundary

Add and test an explicit symptom-diagnosis availability boundary:

```text
stage_structured_diagnosis.symptom_start_year = 1985
```

Before 1985, the AIDS symptom uplift is zero. The identity profile remains bit-for-bit identical to the legacy path. Report a sensitivity discussion if 1985 is not the preferred clinical-recognition boundary, but do not estimate the boundary in this run.

## Identifiability strategy

The primary target contract is MSM-focused. If both the four base diagnosis anchors and the three MSM multipliers were freely calibrated, the MSM data would mostly identify their product rather than the separate components.

Therefore use two fixed epidemiologic backbones, candidates 1326 and 1788, and for each backbone:

- hold the base/non-MSM diagnosis schedule fixed at the candidate-injected values;
- hold all epidemic, transmission, natural-history, ART, PrEP, demographic, and migration parameters fixed;
- estimate only the three existing MSM-relative diagnosis multiplier anchors and one AIDS symptom parameter;
- fix `routine_hazard_scale = 1`;
- retain the existing interpolation geometry;
- add no group-by-stage, activity-by-stage, age, false-negative, time-since-infection, or calendar-era symptom interactions.

The four focused coordinates are:

| Parameter | Config path | Range |
|---|---|---:|
| MSM routine multiplier, 1996 | `...msm_relative_diagnosis.schedule.value.1996` | 0.0–2.0 |
| MSM routine multiplier, 2004 | `...msm_relative_diagnosis.schedule.value.2004` | 0.0–2.0 |
| MSM routine multiplier, 2019 | `...msm_relative_diagnosis.schedule.value.2019` | 0.0–2.0 |
| AIDS symptom annual probability | `...stage_structured_diagnosis.aids_symptom_annual_probability` | 0.0–0.50 |

Use the existing candidate value and the previous `rho=0.80 / AIDS=0.35` effective path as warm proposals where they can be represented without changing the fixed base schedule. Do not silently clip warm proposals.

## Frozen target contract

Use the 13-target forecast-window contract already agreed on 2026-09-21:

- HIV diagnoses, primary: 1996, 2000, 2004, 2010, 2015, 2019, 2024;
- AIDS proxy, primary: 2000, 2005, 2010, 2015, 2019, 2022;
- AIDS 1990 and 1995: visible cross-validation only;
- target-relative model discrepancy: 10% primary, 20% sensitivity only;
- primary acceptance: `I_max <= 3`;
- four reporting groups: diagnoses 1996–2015, AIDS 2000–2015, diagnoses after 2015, AIDS after 2015;
- 2015 is counted once in the earlier window.

Do not change targets, weights, discrepancy, source windows, or the AIDS observation function after viewing results.

Mortality and latent-state checks are scientific diagnostics unless a vetted observation target already exists. Do not reject solely because deaths increase by an arbitrary percentage such as 5%; instead report the magnitude, mechanism, and any available empirical comparator. Flag increases over 10% as material for decision review.

## Execution boundary

The agent implementing this brief must **not** launch the smoke test, Stage A, Stage B, or Stage C. The user will run all simulation commands manually in their own terminal.

The implementation agent is authorized to:

- inspect existing code and outputs;
- implement the feature, focused harness, scoring, checkpointing, and reports;
- add or update configurations and automated tests;
- run unit tests that use synthetic fixtures or mocked engines;
- run `--validate-only`, which must construct no simulation engines and start zero trajectories;
- prepare terminal commands and estimate the declared trajectory budget analytically.

The implementation agent must stop after validation and provide separate commands for:

1. an optional user-launched smoke test;
2. the user-launched Stage-A response surface;
3. the user-launched Stage-B focused calibration;
4. the user-launched Stage-C confirmation after shortlist review; and
5. a resumable one-command orchestration path that respects the same frozen stages.

Do not combine preparation and execution in one command. Do not start a background process, use `caffeinate`, or dispatch even one real trajectory during implementation.

## Staged computational design

The 45-minute cap applies when the user later launches the response surface, discovery, and fresh confirmation. Unit tests and validate-only are outside the trajectory budget. The implementation agent must not start the clock or any simulation stage.

### Stage A — corrected moderate response surface

Purpose: verify continuity, locate the moderate response region, and check the 1985 availability correction before calibration.

For both candidate backbones, test the following fixed-profile grid with five shared CRN seeds:

- `rho`: 0.70, 0.75, 0.80, 0.85, 0.90, 0.95;
- `p_AIDS_symptom`: 0.00, 0.10, 0.20, 0.30, 0.40.

Budget: `2 * 30 * 5 = 300 trajectories`.

This stage may use `rho` because it is a response-surface diagnostic with all existing anchors frozen. It is not carried into Stage B.

Required Stage-A decision:

- establish whether 2024 diagnosis error, clinical-AIDS proxy error, infections, and deaths vary smoothly;
- identify Pareto-efficient profiles for explanation only;
- do not select a supposedly optimal continuous value from the coarse grid;
- if the corrected availability boundary materially changes the previous result, state that explicitly.

### Stage B — focused four-dimensional calibration

For each backbone, generate 128 distinct four-dimensional vectors:

- include the identity structural point;
- include valid warm proposals derived from the prior audit;
- fill the remainder with a reproducible maximin Latin hypercube over the declared ranges;
- use five shared discovery seeds, disjoint from Stage A and all earlier calibration/confirmation seeds.

Budget: `2 backbones * 128 vectors * 5 seeds = 1,280 trajectories`.

Rank using the unchanged four-group search statistic; accept only with the unchanged target-by-target `I_max` rule. Search ranking is not a likelihood or posterior.

Do not adaptively enlarge parameter ranges. Do not change the target contract. Do not run a full 33-parameter calibration in this task.

### Stage C — fresh confirmation

Freeze a shortlist before using confirmation seeds. Select at most ten distinct backbone-plus-parameter tuples:

- primary passes first;
- representation from both backbones where available;
- diversity across the diagnosis/symptom trade-off, not merely the ten lowest `I_max` rows;
- include the best 2024-diagnosis fit that remains a primary pass;
- include the best balanced four-group fit;
- exclude duplicate effective MSM routine paths.

Run 20 fresh seeds per shortlisted tuple, disjoint from all discovery, Stage-A, and previous experiment seeds.

Maximum Stage-C budget: `10 * 20 = 200 trajectories`.

Total planned maximum: **1,780 trajectories**. At the prior observed runtime of roughly 8 seconds per trajectory and 8 workers, this is approximately 30 minutes of pure simulation time, leaving margin for setup, scoring, plots, and checkpoints.

### Time stop for the user-launched run

- Hard wall-time cap: 45 minutes from the start of Stage A simulation.
- Stop dispatching new work at 42 minutes.
- Preserve checkpoints and mark the run `PARTIAL_TIME_LIMIT` if incomplete.
- Never interpret a partial discovery archive as if it were the frozen completed design.
- Confirmation has priority over optional plots if discovery completes late.
- No hidden background run, automatic range extension, or retry loop.

## Required implementation checks

1. Disabled feature and enabled identity remain exact legacy behavior.
2. Symptom uplift is exactly zero before 1985 and active at/after the declared boundary.
3. The MSM multiplier modifies routine hazard only.
4. Stage B fixes `rho=1` and changes only the three MSM multipliers plus symptom probability.
5. The candidate-specific base diagnosis schedule is unchanged byte-for-byte after injection.
6. All other candidate parameters remain unchanged.
7. Annual-to-step hazard arithmetic matches hand calculations.
8. Stage counters sum exactly to endogenous diagnoses.
9. Exogenous non-MSM diagnosis is unchanged.
10. Stock-flow ledgers close exactly.
11. Stage-A, discovery, and confirmation seed sets are mutually disjoint where required.
12. Duplicate effective routine paths are detected.
13. Validate-only starts no simulations and constructs no engines.
14. Resume refuses plan/provenance mismatches and never overwrites a completed run.
15. The prior report-classification bug is fixed: `supported`, `informative but insufficient`, and `not supported` must be reachable through distinct, unit-tested fixtures.

Run focused existing unit tests for diagnosis, AIDS observation, counter-based RNG, C-1, temporal diagnosis schedules, stock-flow decomposition, and coupled scoring only where they do not launch real trajectories. Provenance-guard failures caused by intentional source changes must be regenerated or explicitly separated; do not report a suite with failures as simply “passed.” Any integration or smoke test that constructs a real engine must be printed as a user command and not executed by the implementation agent.

## Required outputs

Save full seed-level annual outputs for 1996–2025 and sufficient earlier counters to verify the 1985 boundary. At minimum report:

- MSM infections and diagnoses;
- diagnoses by Acute, Chronic, and AIDS;
- diagnosed-stage and undiagnosed-stage AIDS progression;
- established AIDS proxy and its components;
- undiagnosed MSM stock by stage and activity;
- ART and diagnosed-off-ART stocks;
- HIV deaths by diagnosis/ART state;
- the exact post-2015 undiagnosed stock-flow ledger;
- 2024 year-end/2025 starting state;
- effective MSM routine diagnosis probability at every calendar anchor and target year.

Create:

- `IMPLEMENTATION_NOTE.md`;
- `validation.json`;
- `frozen_plan.json` and `manifest.json`;
- `stage_a_response_surface.csv`;
- `discovery_candidates.csv`;
- `discovery_target_residuals.csv`;
- `frozen_shortlist.json`;
- `confirmation_summary.csv`;
- `confirmation_target_residuals.csv`;
- `stage_diagnosis_decomposition.csv`;
- `late_presentation_context.csv`;
- `post2015_stock_flow.csv`;
- `latent_state_2025.csv`;
- no more than four decision-relevant figures;
- `REPORT.md`.

Before stopping, also create `USER_RUN_COMMANDS.md` containing absolute-path-safe commands from the repository root. Each command must:

- create any log parent directory before `tee`;
- use `set -o pipefail`;
- use `.venv/bin/python` explicitly;
- name a new output directory and refuse silent overwrite;
- state the expected trajectory count and approximate runtime;
- offer `caffeinate -dimsu` for the user-run calibration;
- include a resume command;
- keep Stage C separate so confirmation cannot start before the shortlist is frozen and reviewed.

Suggested output root:

```text
outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/
```

## Identifiability report

For confirmed or leading discovery vectors, report:

- the effective MSM routine probability at 1996, 2000, 2004, 2010, 2015, 2019, and 2024;
- boundary occupancy for all four coordinates;
- pairwise rank correlations among the leading/accepted vectors, labelled exploratory rather than posterior correlations;
- whether materially different multiplier/symptom combinations yield practically indistinguishable target outputs;
- the rank and condition number of a standardized local linear sensitivity matrix for the 13 target means, if numerically stable;
- whether the symptom parameter is informed by AIDS-stage diagnosis outputs or merely compensated by routine multipliers.

If only effective routine paths are identified while individual anchors remain diffuse, say so. Do not report precise parameter estimates from a non-identifiable ridge.

## Decision rules

### Mechanism supported for broader recalibration

Use only if at least one tuple:

- passes the 13-target primary contract on fresh seeds;
- materially improves 2024 MSM diagnosis error relative to its backbone;
- does not materially worsen both post-2015 AIDS targets;
- retains acceptable earlier diagnosis trajectory;
- has no unexplained mortality or latent-state pathology;
- is not an isolated boundary point;
- and has a nearby region with the same qualitative behavior.

This conclusion authorizes preparation of a later broader recalibration proposal, not automatic production adoption.

### Mechanism informative but insufficient

Use if it reliably improves recent diagnosis fit or primary `I_max` but remains in a transparent trade-off with AIDS, mortality, or latent state; or if only a narrow/boundary region works; or if the late-presentation observation mapping remains the binding uncertainty.

State the single smallest follow-up needed. This outcome must not be collapsed into “not supported.”

### Mechanism not supported over the tested focused space

Use only if the completed design shows no stable improvement in 2024 diagnosis fit or overall primary fit across either backbone, or any apparent improvement consistently fails on fresh seeds and produces clearly unacceptable epidemic behavior.

## Final report questions

The first page of `REPORT.md` must answer:

1. How many discovery and confirmed primary passes were found?
2. How much did confirmed 2024 MSM diagnosis error change?
3. What happened to the 2019/2022 AIDS proxy and its two components?
4. What happened to cumulative infections and HIV deaths?
5. What fraction of diagnoses occurred in clinical AIDS, and how does it relate to—not equal—the 32.1% late-stage context?
6. Is the useful region broad, narrow, or boundary-dependent?
7. Are the four focused parameters separately identified, or only their effective diagnosis path?
8. Does the evidence justify a broader recalibration?

End with exactly one of the three decision labels above and give the evidence for every clause. Do not modify the production configuration or HTA baseline.
