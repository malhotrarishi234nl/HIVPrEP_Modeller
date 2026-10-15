# Task brief: stage-diagnosis candidate baseline, whole-model review, and gated next experiment

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

Date: 2026-09-22  
Status: analysis-first handoff  
Primary candidate: `D_1788_v109`  
Matched legacy comparator: `D_1788_v000`  
Scientific status: candidate structural baseline; not yet the production or HTA baseline  
Run authority: analyze existing outputs and build reproducible reporting code now. Do not launch a new calibration, counterfactual simulation, or forecast without an explicit user instruction after the analysis checkpoint.

## Purpose

Walk the user through whether `D_1788_v109` should become the preliminary baseline diagnosis structure.

The previous orchestration established that the mechanism is useful but underconstrained:

- it substantially improves recent MSM diagnoses;
- it passes the frozen 13-target primary history-matching contract;
- it produces a plausible 2024 MSM incidence estimate;
- its mean end-2024 undiagnosed MSM stock is only slightly above the Tier-2 interval;
- it increases PLHIV, infections, AIDS presentations, and deaths;
- the model cannot directly represent CD4 at diagnosis or time since infection; and
- the useful parameter region is narrow and routine versus symptom effects are not fully separated.

This task must convert that diagnosis-focused result into a whole-model candidate-baseline assessment. The central question is:

> Does `D_1788_v109` improve the historical model as a whole, and are its larger reservoir, AIDS burden, and mortality scientifically interpretable enough to carry it into matched forecasts?

Do not treat this as another search for a lower objective value. Do not add a coefficient merely to improve fit.

## Plain-language framing for the user

At every checkpoint, explain the result in causal language:

1. Lower routine diagnosis leaves more people undiagnosed for longer.
2. A larger untreated infectious reservoir can generate additional infections.
3. Some people are then diagnosed through AIDS-stage symptoms.
4. More infections and later treatment can increase PLHIV, ART demand, AIDS burden, and deaths.
5. The model's broad disease states may exaggerate or blur some of those downstream effects because they do not encode CD4 or time since infection.

Keep separate:

- evidence that late presentation exists;
- evidence about its magnitude;
- the model mechanism used to approximate it; and
- downstream epidemic consequences of that approximation.

## User decisions and constraints already fixed

- The advanced-presentation construct of interest is CD4 <200 cells/µL or an AIDS-defining illness.
- CD4 200–349 is treated as Chronic for this work.
- The simulator does not explicitly model CD4 or time since infection.
- `D_1788_v109` is the leading candidate to review.
- The user considers the approximately 2% Tier-2 reservoir overshoot potentially acceptable and wants it interpreted continuously, not only as a binary gate failure.
- Heavy simulations are to be run by the user in their terminal, not silently by the agent.
- Avoid overfitting and do not reopen the full calibration unless the evidence review identifies a concrete need.
- Preserve all prior output directories and production configurations.

## Required sources

Read these completely before analysis:

- `TASK_BRIEF_DIAGNOSIS_TRIANGULATION_ORCHESTRATION_2026-09-22.md`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/REPORT.md`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/AIDS_DEFINITION_ADJUDICATION.md`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/IMPLEMENTATION_NOTE.md`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/frozen_plan.json`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/frozen_target_contract.json`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/frozen_shortlist.json`;
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/design.json`;
- `MODEL_LIMITATIONS.md`;
- `HISTORY_MATCHING_BUILD_SPEC.md`;
- `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`;
- `HM_MSM_AIDS_DIAGNOSTIC_RUN_2026-09-16.md`; and
- the four late-testing/missed-opportunity papers listed in the orchestration brief when interpreting mechanism plausibility.

Use, but do not modify, these machine-readable run products:

- `confirmation_summary.csv`;
- `confirmation_seed_level.csv`;
- `paired_seed_differences.csv`;
- `annual_trajectories_1996_2025.csv`;
- `seed_level_annual_1996_2025.csv.gz`;
- `target_residuals.csv`;
- `external_plausibility_checks.csv`;
- `latent_state_2025.csv`;
- `post2015_stock_flow.csv`;
- `stage_diagnosis_decomposition.csv`;
- `effective_msm_routine_paths.csv`;
- `decision_clauses.csv`;
- `identifiability.json`; and
- `empirical_constraint_register.csv`.

If a requested annual stock is not present, inspect the saved seed-level products and simulator output schema. Do not rerun the simulator merely to fill a reporting column. Mark it unavailable if it cannot be reconstructed exactly from saved output.

## Known reference results to reproduce before proceeding

The agent must independently reproduce these numbers from the saved files. A mismatch stops the analysis until resolved.

For `D_1788_v109`:

- 30 confirmation seeds;
- primary `I_max = 2.634565...`;
- binding target `rivm_aids_msm@2022`;
- all 13 primary targets pass the `I <= 3` rule;
- MSM diagnoses 2024: 305.67 versus observed 318;
- MSM new infections 2024: 292.97;
- end-2024 undiagnosed MSM: 915.93;
- end-2024 MSM PLHIV: 12,644.47;
- end-2024 MSM on ART: 11,589.37;
- MSM diagnoses in model AIDS state in 2024: 36.13;
- MSM fraction diagnosed in model AIDS state in 2024: 11.80%;
- MSM HIV deaths 2015–2024: 1,202.73.

Matched `D_1788_v000` comparator:

- MSM diagnoses 2024: 195.47;
- MSM new infections 2024: 184.97;
- end-2024 undiagnosed MSM: 424.97;
- end-2024 MSM PLHIV: 11,601.13;
- end-2024 MSM on ART: 11,064.80;
- MSM HIV deaths 2015–2024: 991.00.

The Tier-2 end-2024 undiagnosed MSM interval is 690–900. The candidate mean is therefore 15.93 above the upper bound, or about 1.77%. Confirm that 15 of 30 candidate seeds are individually compatible if the saved seed-level definition supports that calculation.

## Comparison discipline

Use `D_1788_v000` as the primary comparator because it uses the same backbone and confirmation seed set as `D_1788_v109`.

Do not mix:

- benchmark-stage `M0_1788` with confirmation-stage `D_1788_v109` for paired claims;
- backbone 1326 with backbone 1788 for causal attribution;
- ensemble means from one seed set with uncertainty from another; or
- direct surveillance targets with Tier-2 model-derived estimates.

All causal language must be supported by common-random-number paired differences or an exact accounting identity. Otherwise say “associated with” or “coincides with.”

## Phase 1: provenance and integrity audit

Before producing figures:

1. Verify that the candidate and comparator each have 30 unique confirmation seeds.
2. Verify exact shared seed IDs and common-random-number pairing.
3. Verify config hashes and extract the four diagnosis coordinates from the frozen design, rather than transcribing them by hand.
4. Confirm that non-diagnosis parameters are identical.
5. Recompute the headline values above.
6. Recompute paired mean changes and 95% confidence intervals where saved paired seed data permit.
7. Check stock-flow balance residuals and missing/duplicate rows.
8. Record any discrepancy in `DATA_INTEGRITY_NOTE.md` and stop if it could change the scientific conclusion.

Expected candidate diagnosis coordinates, to be verified:

- MSM diagnosis multiplier 1996: approximately 0.820236;
- MSM diagnosis multiplier 2004: approximately 0.388836;
- MSM diagnosis multiplier 2019: approximately 0.525189;
- AIDS symptom-driven annual diagnosis probability: approximately 0.136453.

## Phase 2: build the candidate-baseline comparison

Create a whole-model comparison for 1996–2025 using saved confirmation results.

### Required annual outcomes

Where available, report ensemble mean, standard deviation, and paired change for:

- total PLHIV;
- MSM PLHIV;
- non-MSM PLHIV;
- diagnosed and undiagnosed MSM stocks;
- diagnosed but off-ART stock;
- total and MSM ART stock;
- ART coverage among diagnosed MSM;
- total, MSM, and non-MSM new infections;
- total, MSM, and non-MSM diagnoses;
- MSM diagnoses by Acute, Chronic, and AIDS state;
- symptom-attributed diagnoses;
- biological AIDS progression by diagnosis/ART status;
- the established AIDS first-diagnosis proxy and its components;
- total and MSM HIV/AIDS deaths, disaggregated by diagnosis/ART status;
- oral PrEP stock/use and any saved lenacapavir variables; and
- the full 2025 starting state.

Include a compact milestone table for at least 1996, 2000, 2004, 2010, 2015, 2019, 2020, 2023, 2024, and 2025. Do not crowd the main report with every annual value; provide the complete annual table as CSV.

### Required stratification

Separate:

- MSM from non-MSM;
- total-population consequences from MSM-specific mechanism effects;
- stock measures from annual flows;
- direct model outputs from reconstructed quantities; and
- historical targets from descriptive external checks.

The report must explicitly answer whether non-MSM trajectories are effectively unchanged and whether total-population changes are driven almost entirely by MSM.

### External comparisons

For every registered external series, show:

- source and epistemic tier;
- observed value or interval;
- candidate mean;
- legacy mean;
- absolute and percentage gap;
- seed-level compatibility where meaningful;
- whether the comparison is primary, a one-sided safety bound, Tier-2 plausibility, or descriptive only; and
- whether the candidate moves toward or away from the evidence.

At minimum discuss:

- MSM infections in 2010, 2020, and 2024;
- MSM undiagnosed stock in 2010 and 2024;
- MSM PLHIV in 2024;
- total PLHIV in 2023;
- total ART in 2023;
- AIDS deaths in 2023;
- diagnosed-patient HIV deaths in 2010–2014;
- MSM diagnoses in the model AIDS state in 2024; and
- fractions diagnosed in the model AIDS state.

Do not imply that a candidate matches total or MSM PLHIV merely because it moves closer. Quantify the remaining gap.

## Phase 3: explain implausibility and the 2% result

Create a target-level table for all 13 primary targets containing:

- observed value;
- candidate mean and standard deviation;
- signed residual;
- primary implausibility;
- sensitivity implausibility;
- pass/fail at the frozen cutoff; and
- rank from most to least binding.

Explain in plain language that:

- implausibility is an uncertainty-standardized discrepancy, not a percentage;
- `I_max` is the maximum across targets and is non-compensatory;
- `I_max <= 3` passes the frozen contract;
- the candidate's `I_max` is approximately 2.63, binding on the 2022 MSM AIDS proxy;
- the candidate's 2024 MSM diagnosis implausibility is approximately 0.17; and
- the 1.77% reservoir overshoot is a separate Tier-2 interval comparison, not an implausibility score.

Treat the binary Tier-2 gate and continuous evidence separately. State that a mean of 915.93 fails a predeclared upper bound of 900 mechanically, while also reporting the small absolute gap and seed-level overlap. Do not silently widen the bound or retrospectively declare a pass.

## Phase 4: mortality and AIDS-burden decomposition

This is the principal scientific follow-up.

Quantify the candidate-minus-legacy change in deaths for 2015–2024 by:

- MSM versus non-MSM;
- undiagnosed and off ART;
- diagnosed and off ART;
- diagnosed and on ART;
- undiagnosed and on ART, if nonzero;
- calendar year; and
- activity stratum when supported.

Also compare:

- AIDS progression while undiagnosed;
- AIDS progression while diagnosed;
- diagnosis while already in the model AIDS state;
- symptom-attributed diagnosis;
- time from diagnosis to ART only if directly available; and
- ART initiation and stock changes.

Reconcile cumulative changes to the closest exact saved accounting identity. The report must distinguish:

1. more deaths because more people are living with HIV;
2. more deaths because more people remain untreated or are diagnosed later;
3. more deaths caused by the model's progression/mortality mapping; and
4. effects that cannot be separated without a new counterfactual.

Calculate useful normalized quantities where denominators are valid, including:

- deaths per 1,000 PLHIV person-years;
- deaths per 1,000 undiagnosed person-years;
- AIDS diagnoses as a fraction of all diagnoses; and
- ART coverage among diagnosed PLHIV.

Do not conclude that the diagnosis mechanism is invalid merely because absolute deaths rise in a larger epidemic. Conversely, do not dismiss a materially elevated death rate per PLHIV or an external AIDS-death discrepancy.

Explicitly revisit the AIDS-definition adjudication. The model AIDS state may be narrower than CD4 <200 or AIDS-defining illness, while mortality parameters may use CD4-related evidence. Identify any interpretation seam rather than fitting around it.

## Phase 5: historical trajectory-shape review

Inspect how the candidate reaches the 2024 endpoint.

Answer:

1. When do candidate and legacy PLHIV trajectories begin to diverge?
2. Is the divergence gradual, or aligned with a diagnosis-era anchor?
3. Does the undiagnosed reservoir accumulate smoothly?
4. Why is candidate MSM incidence below the external interval in 2010, above it in 2020, but inside it in 2024?
5. Are post-2015 diagnoses rising because of the carried reservoir, extra infections, symptom-driven diagnoses, or all three?
6. Does ART catch up, and if so, with what lag?
7. Do AIDS presentations and deaths rise proportionately or disproportionately?
8. Is there evidence of an abrupt artifact at 2004 or 2019?
9. Does the candidate improve total PLHIV/ART evidence or only the MSM diagnosis target?

Use aligned plots with identical axes and uncertainty bands. Avoid dual axes for quantities with different units.

## Phase 6: candidate-baseline judgment

Classify the candidate using one of these labels.

### Ready for matched preliminary forecasts

Use when:

- data integrity passes;
- the full primary target contract passes;
- the 2024 reservoir miss remains minor when interpreted continuously;
- whole-model trajectories remain coherent;
- mortality increases have a defensible explanation or a clearly bounded structural limitation;
- no external one-sided safety bound is violated; and
- uncertainty is carried forward rather than presenting the point estimate as truth.

### Retain as structural sensitivity only

Use when:

- diagnosis fit improves, but mortality or trajectory shape is questionable;
- the result is too dependent on a narrow ridge;
- external outcomes materially worsen without adequate explanation; or
- the AIDS/CD4 mapping prevents a defensible baseline interpretation.

### Reject in the tested form

Use only when there is a clear integrity failure, a serious predeclared constraint violation, pathological dynamics, or a scientifically irreconcilable downstream consequence.

Do not create a new category merely to force a preferred answer.

## Phase 7: gated targeted experiment, only if needed

Do not automatically run or build a broad calibration.

First state exactly which causal ambiguity cannot be resolved from saved results. If a new experiment is necessary, prefer a small paired factorial design:

| Arm | Routine MSM anchors | AIDS symptom probability |
|---|---|---:|
| A | legacy `D_1788_v000` | 0 |
| B | `D_1788_v109` routine anchors | 0 |
| C | legacy routine anchors | `D_1788_v109` symptom value |
| D | `D_1788_v109` routine anchors | `D_1788_v109` symptom value |

This separates, on the model's dynamic scale:

- the routine-anchor contribution;
- the symptom contribution; and
- their interaction.

It does not hold realized infections fixed. Do not claim that it does. A true infection-history replay may be proposed only if the repository already supports an exact event replay with verified semantics. Do not invent a pseudo-counterfactual by overwriting observed infections.

Default targeted design if approved by the user:

- backbone 1788 only;
- 30 fresh common seeds per arm;
- 120 trajectories total;
- same historical horizon and full output capture;
- no optimization or parameter selection;
- no use of the new seeds to retune the four arms.

Before any run, produce:

- a frozen experiment JSON;
- a trajectory-count and runtime estimate;
- validation-only output;
- unit tests for arm construction and pairing;
- exact user terminal commands;
- a separate output root; and
- a stop/go explanation in plain language.

The agent must wait for explicit user approval before executing or instructing an autonomous task to execute the experiment. The user may run the commands manually.

## Phase 8: matched forecast proposal

Only after the candidate-baseline judgment, prepare a matched forecast comparison:

- legacy `D_1788_v000`;
- candidate `D_1788_v109`;
- identical non-diagnosis configuration, intervention assumptions, seeds, and forecast horizon.

Do not mix structural diagnosis uncertainty with PrEP scenario uncertainty. First compare no-new-intervention/reference forecasts; then, if stable, compare intervention scenarios.

Prepare but do not launch:

- exact frozen configuration exports derived from the saved design;
- provenance hashes;
- matched seed list;
- output schema;
- terminal commands; and
- a maximum runtime estimate.

Forecast-readiness outcomes must include infections, diagnoses, undiagnosed stock, PLHIV, ART demand, AIDS burden, deaths, PrEP use, and any downstream economic inputs consumed by the HTA model.

## Required outputs

Write all new products under:

```text
outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/
```

Do not alter the completed orchestration directory.

Required files:

- `STATUS.md`;
- `DATA_INTEGRITY_NOTE.md`;
- `CANDIDATE_BASELINE_REPORT.md`;
- `candidate_vs_legacy_annual.csv`;
- `candidate_vs_legacy_milestones.csv`;
- `target_implausibility_table.csv`;
- `external_evidence_comparison.csv`;
- `mortality_decomposition.csv`;
- `aids_burden_decomposition.csv`;
- `paired_effect_summary.csv`;
- `candidate_configuration.json`;
- `legacy_configuration.json`;
- `DECISION.md`;
- `NEXT_EXPERIMENT.md`; and
- `USER_RUN_COMMANDS.md` only if a new targeted experiment or forecast run is recommended.

Maximum figures: six.

Recommended figures:

1. total and MSM PLHIV trajectories;
2. MSM infections and diagnoses;
3. undiagnosed, diagnosed-off-ART, and ART stocks;
4. AIDS proxy, AIDS-stage diagnoses, and progression;
5. annual deaths by diagnosis/ART state; and
6. target implausibility plus external-evidence dashboard.

All figures must identify candidate, comparator, ensemble size, uncertainty representation, and whether the evidence is Tier 1 or Tier 2.

## Required report structure

The first page of `CANDIDATE_BASELINE_REPORT.md` must answer, in bullets:

1. What changed in the candidate?
2. What improved?
3. What worsened?
4. What does the approximately 2% reservoir miss mean?
5. What is the binding implausibility target?
6. What happens to PLHIV, ART, incidence, AIDS burden, and deaths?
7. Are non-MSM outputs materially affected?
8. Is the mortality increase primarily population-size, treatment-timing, or structural-mapping driven?
9. Is the candidate ready for matched preliminary forecasts?
10. What is the smallest next experiment, if any?

The detailed report must clearly separate:

- reproduced facts;
- accounting identities;
- paired statistical comparisons;
- scientific interpretation;
- structural limitations; and
- recommendations.

## Interaction protocol with the user

This is intended to be a collaborative agent session. The agent should pause at three checkpoints and walk the user through the findings.

### Checkpoint 1: integrity and baseline reconstruction

Show:

- confirmation that the comparison is paired and clean;
- a five-to-ten-row milestone table;
- the 13 target implausibility scores; and
- the exact interpretation of the 2% miss.

Do not ask the user to make a baseline decision yet.

### Checkpoint 2: mortality and trajectory interpretation

Show:

- where the extra deaths occur;
- whether normalized death rates also worsen;
- when PLHIV/incidence trajectories diverge;
- non-MSM effects; and
- unresolved causal ambiguity.

Ask whether the user agrees with the scientific interpretation before preparing a run.

### Checkpoint 3: recommendation and optional commands

Present:

- one recommended classification;
- the strongest argument for it;
- the strongest argument against it;
- whether a 120-trajectory factorial experiment is needed;
- whether matched preliminary forecasts are justified; and
- exact commands if the user elects to run them.

Do not bury a user decision inside a long technical report.

## Guardrails

- Do not modify the production configuration.
- Do not overwrite prior calibration or orchestration outputs.
- Do not edit the raw validation workbook.
- Do not add CD4 or time-since-infection states in this task.
- Do not add a false-negative coefficient, diagnosis mixture, activity interaction, or extra era knot.
- Do not recalibrate transmission, progression, mortality, ART, PrEP, or population parameters.
- Do not promote Tier-2 model-derived evidence to a primary independent likelihood.
- Do not call a small interval overshoot a pass; describe it as a small predeclared-gate failure with continuous context.
- Do not call 2,430 simulations empirical validation. They establish numerical behavior and local robustness.
- Do not interpret the model AIDS state as identical to CD4 <200 unless the adjudicated evidence supports that statement.
- Do not use the new candidate in production HTA results until the user explicitly approves it after review.

## Completion criteria

The analysis phase is complete only when:

- all known reference values reproduce;
- candidate and comparator are verified as matched except for diagnosis structure;
- annual whole-model outputs are reported;
- all 13 implausibility scores are explained;
- the 2% reservoir overshoot is correctly separated from implausibility;
- PLHIV and ART gaps against external evidence are quantified;
- additional mortality is decomposed and normalized as far as saved data permit;
- non-MSM effects are explicitly assessed;
- trajectory shape is reviewed, not just 2024 endpoints;
- structural non-identifiability is stated without being used as a blanket excuse;
- the candidate receives one decision label; and
- any proposed new run is minimal, frozen, reproducible, and user-approved.

## Handoff back for independent verification

When finished, tell the user to return with:

- `CANDIDATE_BASELINE_REPORT.md`;
- `DECISION.md`;
- `DATA_INTEGRITY_NOTE.md`;
- `mortality_decomposition.csv`;
- `target_implausibility_table.csv`;
- `external_evidence_comparison.csv`;
- all figures;
- any proposed experiment JSON;
- `USER_RUN_COMMANDS.md`, if created; and
- the final `git diff --stat` plus a list of newly created files.

The independent verifier should then check numerical reproduction, scientific wording, evidence-tier discipline, and whether any proposed run is actually necessary before the user spends additional compute.
