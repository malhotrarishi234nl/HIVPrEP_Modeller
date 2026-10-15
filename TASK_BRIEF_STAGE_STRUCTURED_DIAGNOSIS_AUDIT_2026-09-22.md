# Task brief: stage-structured diagnosis audit

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

Date: 2026-09-22  
Status: ready for an implementation-and-run agent  
Purpose: test a bounded structural diagnosis hypothesis; this is not recalibration and must not alter the production baseline.

## Plain-language question

Does the current diagnosis mechanism remove undiagnosed MSM too readily during asymptomatic infection, while providing too little additional diagnosis when people reach clinical AIDS?

Test whether a lower routine diagnosis hazard plus one AIDS symptom-driven hazard can simultaneously:

1. preserve enough undiagnosed MSM to improve the 2024 HIV-diagnosis shortfall;
2. improve the post-2000 MSM AIDS-diagnosis proxy;
3. retain an acceptable 1996-2019 MSM diagnosis trajectory; and
4. avoid damaging infections, ART stock, mortality, or the 2024/2025 latent state.

The diagnosis year/era schedule and the MSM-relative diagnosis multiplier already exist. They must remain in the mechanism and must be preserved candidate by candidate. Do not replace them with a new flat diagnosis parameter.

## Existing mechanism that must be respected

Read the implementation before changing it:

- `run_v3.py::_build_diagnosis_schedule`
- `run_v3.py::_build_msm_relative_diagnosis_schedule`
- `run_v3.py::_step_diagnosis`
- `engine_v3.py::TimeSystem.annual_to_step_rate`
- `aids_observation.py`

The current endogenous diagnosis path is:

1. obtain the calendar-year base annual diagnosis probability `p_base(t)` from `disease.diagnosis.schedule`;
2. use multiplier `m_g(t) = 1` for non-MSM and the enabled `msm_relative_diagnosis` schedule for MSM;
3. form `p_current(g,t) = clip(p_base(t) * m_g(t), 0, 1)`; and
4. convert this annual probability to the timestep probability with `annual_to_step_rate`.

The active schedules are candidate-specific after parameter injection. Never substitute the static values in a production JSON for the injected candidate values. Preserve every base diagnosis anchor, every MSM-relative diagnosis anchor, and their interpolation settings for candidates 1326 and 1788.

Also preserve the C-1 exogenous non-MSM diagnosis stream. The structural experiment applies only to endogenous diagnosis. Report endogenous and exogenous non-MSM diagnoses separately wherever non-MSM totals are discussed.

## Structural mechanism to implement behind a feature flag

Add a diagnostic-only, disabled-by-default block such as:

```text
disease.diagnosis.stage_structured_diagnosis.enabled
disease.diagnosis.stage_structured_diagnosis.routine_hazard_scale
disease.diagnosis.stage_structured_diagnosis.aids_symptom_annual_probability
```

Names may be adjusted to match repository conventions, but document the final paths. With the feature disabled, behavior and random-number streams must remain unchanged.

For each population group and calendar time, first recover the existing effective annual routine probability:

```text
p_current(g,t) = clip(p_base(t) * m_g(t), 0, 1)
```

Convert it to a continuous-time annual hazard:

```text
lambda_current(g,t) = -log(1 - p_current(g,t))
```

For profile parameters `rho` and `p_symptom_aids`, define:

```text
lambda_routine(g,t) = rho * lambda_current(g,t)
lambda_symptom      = -log(1 - p_symptom_aids)

lambda_total(g,s,t) = lambda_routine(g,t)
                    + I(s == AIDS) * lambda_symptom

p_step(g,s,t) = 1 - exp(-lambda_total(g,s,t) * years_per_step)
```

Scientific interpretation:

- the existing calendar schedule represents changes in routine testing and ascertainment by era;
- the existing MSM multiplier modifies routine testing only;
- `rho` is a bounded structural sensitivity on routine diagnosis, not a new fitted time trend;
- the AIDS term represents symptom-driven diagnosis and is common across population groups in this first test;
- the MSM multiplier must **not** multiply the AIDS symptom term;
- Acute and Chronic receive no separate symptom uplift in this first test;
- add no group-by-stage interaction, false-negative parameter, testing-history state, or time-since-infection state.

Handle `p_current == 1` safely. Reject invalid probabilities and nonfinite hazards rather than silently repairing them.

### Required identity checks

The profile `(rho=1, p_symptom_aids=0)` must reproduce the current model exactly under paired seeds, including:

- annual outputs;
- state summaries;
- diagnosis draws by population;
- AIDS observation counters; and
- counter-based RNG behavior.

Do not accept approximate agreement where exact agreement is expected.

## Identifiability constraints

This audit intentionally adds only two profile coordinates, and neither is calibrated here:

- `rho`: one common scale on the already calibrated routine hazard;
- `p_symptom_aids`: one common AIDS symptom-driven annual probability.

Do not estimate separate symptom hazards by group, calendar era, activity, age, or sex. Do not change the existing base schedule or MSM multiplier during this experiment. This prevents the routine schedule, MSM multiplier, and AIDS uplift from freely trading off.

The paired surface is still capable of a ridge: a lower `rho` can be offset by a higher AIDS uplift in total diagnoses. Therefore total diagnoses alone are not sufficient. The report must show diagnosis by HIV stage and the existing AIDS proxy components separately.

## Frozen candidates and stochastic design

Use candidate IDs 1326 and 1788 from:

- search: `outputs/calibration/coupled_forecast_windows_20260921_search`
- confirmation: `outputs/calibration/coupled_forecast_windows_20260921_confirmation`

These are the two original fresh-seed primary passers that were not variance-stability flagged and were used in the post-2015 decomposition.

Use ten common-random-number seeds for every profile. Reuse seeds `200600` through `200609` if they remain compatible with the current runner and provenance; reuse is appropriate for this paired structural comparison. Record overlap explicitly. Any later independent confirmation must use disjoint fresh seeds.

Run this frozen 12-profile grid:

| `rho` | `p_symptom_aids` |
|---:|---:|
| 1.00 | 0.00 |
| 1.00 | 0.35 |
| 1.00 | 0.65 |
| 1.00 | 0.85 |
| 0.80 | 0.00 |
| 0.80 | 0.35 |
| 0.80 | 0.65 |
| 0.80 | 0.85 |
| 0.60 | 0.00 |
| 0.60 | 0.35 |
| 0.60 | 0.65 |
| 0.60 | 0.85 |

Budget: `2 candidates * 12 profiles * 10 seeds = 240 trajectories`.

This grid is deliberately a coarse falsification surface. Do not interpolate an optimum, run an adaptive search, or expand the grid after seeing results. If every useful point lies on a boundary, report that fact and recommend a separately predeclared follow-up; do not extend the range in the same analysis.

## Parameters that remain frozen

Apart from `rho` and `p_symptom_aids`, preserve the complete injected candidate configuration, including:

- the base diagnosis year/era anchors and interpolation;
- the MSM-relative diagnosis multiplier anchors and interpolation;
- transmission betas, contacts, mixing and orientation parameters;
- disease progression and HIV mortality;
- ART initiation, dropout and effectiveness;
- PrEP uptake, dropout and effectiveness;
- epidemic seeding;
- demography, migration and activity structure;
- exogenous non-MSM diagnoses; and
- timestep, event ordering and RNG configuration.

Hash the full configuration for every candidate/profile. Fail if profiles differ anywhere outside the new diagnostic-only block.

## Outputs required

Save full seed-level annual outputs for 1996-2025. At minimum include:

- new MSM infections;
- total endogenous HIV diagnoses;
- endogenous MSM diagnoses;
- endogenous non-MSM diagnoses;
- exogenous non-MSM diagnoses;
- diagnoses from Acute, Chronic and AIDS, for total/MSM/non-MSM;
- `hiv_diagnoses_in_aids_stage_msm`;
- `aids_progression_while_diagnosed_msm`;
- `aids_first_diagnosis_proxy_msm`;
- MSM undiagnosed stock by Acute, Chronic and AIDS;
- MSM undiagnosed stock by activity stratum;
- diagnosed off-ART and on-ART stocks;
- HIV deaths by diagnosis/ART state where available; and
- 2024 year-end and 2025 starting-state snapshots.

If stage-specific diagnosis counters do not exist, add passive counters derived from the diagnosis draw. Counters must not change draws or population updates. Test their sums against total diagnosis counts.

Carry forward the exact post-2015 undiagnosed MSM stock-flow ledger for each profile so that any improvement can be attributed to infections, diagnoses, background mortality, or HIV mortality. Maximum balance residual must remain zero.

## Scoring and decision rules

This is a structural audit, not a new calibration. Apply the already frozen target contract without changing targets, weights, discrepancy, or observation definitions:

- primary HIV diagnoses from 1996 onward at the selected anchors;
- primary AIDS targets from 2000 onward at the selected anchors;
- pre-2000 AIDS observations remain cross-validation only;
- 10% target-relative discrepancy is primary;
- 20% is sensitivity only;
- `I_max <= 3` remains the primary acceptance threshold.

Do not equate a surveillance late diagnosis defined by CD4 `<350` with the model AIDS compartment. This audit evaluates the established `aids_first_diagnosis_proxy_msm` and its components. If a suitable CD4-based late-diagnosis series is available, discuss it as an observation-mapping limitation unless an independently justified mapping already exists.

For each candidate/profile report:

1. target-level means, SDs, signed residuals and implausibilities;
2. four group summaries used by the forecast-window contract;
3. primary and 20% sensitivity `I_max`;
4. absolute error for MSM diagnoses in 2019 and 2024;
5. absolute error for the MSM AIDS proxy in 2019 and 2022;
6. cumulative 2015-2024 MSM infections, diagnoses and deaths;
7. change in undiagnosed MSM stock from start-2015 to start-2025;
8. the fraction of endogenous MSM diagnoses arising from each HIV stage; and
9. paired seed-level changes from the identity baseline with uncertainty intervals.

A profile is **promising**, not validated, only if all of the following hold:

- improves 2024 MSM diagnosis absolute error;
- does not worsen the 1996-2019 MSM diagnosis RMSE by more than 10%;
- improves or leaves unchanged the combined 2019/2022 AIDS-proxy absolute error;
- does not worsen primary `I_max`;
- does not create a material increase in HIV deaths or an implausible 2025 latent state;
- works in the same qualitative direction for both candidates; and
- does not rely on a single stochastic replicate.

If no profile meets all conditions, conclude that this two-parameter structural mechanism does not resolve the observed conflict over the tested range. Do not soften gates after inspecting results.

## Automated tests required before simulation

1. Feature disabled gives exact legacy behavior.
2. `(rho=1, p_symptom_aids=0)` gives exact identity behavior with the feature enabled.
3. Existing calendar-era and MSM-multiplier values are preserved after profile injection.
4. MSM multiplier affects routine hazard but not the AIDS symptom hazard.
5. Acute and Chronic have no symptom uplift; AIDS has exactly the configured uplift.
6. Annual-hazard-to-step conversion agrees with hand calculations at quarterly timestep.
7. Probabilities remain finite and within `[0,1]`; invalid inputs fail closed.
8. Stage-specific diagnosis counters sum to total endogenous diagnoses for total/MSM/non-MSM.
9. Existing AIDS proxy components remain internally consistent.
10. C-1 exogenous non-MSM diagnosis behavior is unchanged.
11. Only the new diagnostic block differs between profiles.
12. Candidate IDs, profile IDs and seed IDs are unique and the trajectory budget equals 240.
13. Validate-only constructs no engines and starts no simulations.
14. Output directories cannot be silently overwritten or mixed across provenance.
15. Post-2015 stock-flow ledgers close exactly.

Run relevant existing diagnosis, AIDS observation, counter-based RNG, temporal diagnosis audit, stock-flow and coupled-calibration tests. Record exact commands and outcomes. Unit-test success establishes implementation consistency, not scientific validity.

## Run discipline

1. First perform a read-only source audit and write the exact implementation plan.
2. Implement the feature, passive counters, runner, configuration manifest and tests.
3. Run validate-only and the relevant test suite.
4. Run a two-profile smoke test: identity baseline and one nontrivial profile, both candidates, one seed each. Identity outputs must reproduce the stored/current baseline where provenance permits.
5. Estimate runtime and memory from the smoke test.
6. Then run the frozen 240-trajectory experiment. The user has requested this bounded test, but do not launch any calibration, adaptive extension, or additional confirmation.
7. Use explicit workers, default 8 if memory permits. Use checkpoints and resumability. No hidden background process or automatic retries.
8. Preserve failed tasks and explain them; do not silently replace seeds.

Write to a new output directory, suggested:

```text
outputs/calibration/stage_structured_diagnosis_audit_20260922/
```

## Required deliverables

- `IMPLEMENTATION_NOTE.md`: exact code/config changes and identity guarantees.
- `manifest.json`: source hashes, candidates, profiles, seeds, budget and commands.
- `validation.json`: validate-only result with zero simulations started.
- `smoke/`: smoke outputs and comparison.
- `seed_level_annual.csv`: all required annual outputs.
- `profile_summary.csv`: candidate/profile summaries and decision flags.
- `target_residuals.csv`: target-level scoring under the unchanged contract.
- `stage_diagnosis_decomposition.csv`: diagnoses by stage and population.
- `post2015_stock_flow.csv`: exact MSM undiagnosed ledger.
- `figures/`: at most four decision-relevant figures.
- `REPORT.md`: plain-language result followed by technical evidence.
- reproducible tests and a terminal command for resuming the frozen run.

The first page of `REPORT.md` must answer:

1. Did lowering routine diagnosis preserve the reservoir and raise later diagnoses?
2. Did the AIDS uplift improve the late-stage proxy, or merely move diagnoses between counters?
3. Was any improvement obtained without damaging the earlier diagnosis trajectory?
4. Is there a stable region across both candidates, or only a trade-off/ridge?
5. Does the evidence justify a later recalibration with this mechanism?

End with exactly one recommendation:

- **Mechanism supported for a separately specified recalibration**;
- **Mechanism informative but insufficient; one bounded follow-up is required**; or
- **Mechanism not supported over the tested range**.

Do not promote any audit profile into the production baseline or HTA scenarios.
