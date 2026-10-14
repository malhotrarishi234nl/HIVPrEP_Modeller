# Task brief: diagnosis-mechanism triangulation and staged orchestration

Date: 2026-09-22  
Status: prepare-only handoff. Evidence audit, implementation, unit tests, and validate-only are authorized. No real simulation may be launched by the implementation agent.  
User-run wall-time cap: 2 hours total, with checkpointed stages and independently resumable shards  
Scientific status: structural model comparison for a preliminary result, not automatic production adoption or an HTA baseline

## Decision to be made

Determine whether routine plus symptom-driven HIV diagnosis is a scientifically defensible structural improvement for the MSM model when judged jointly against:

1. directly observed HIV and AIDS surveillance targets;
2. the empirical occurrence of advanced/late presentation;
3. the implied infections, undiagnosed reservoir, PLHIV, ART stock, and deaths;
4. robustness across stochastic seeds and a local parameter neighborhood; and
5. the model's inability to represent CD4 or time since infection explicitly.

Do not frame the previous focused calibration as a failed mechanism. It showed that the mechanism can improve recent diagnoses and pass the frozen primary diagnosis/AIDS target contract, but often does so by increasing infections and the undiagnosed reservoir. This task determines whether that larger latent epidemic is empirically plausible and whether the useful result occupies a stable parameter region.

## User decisions already fixed

- The thesis-facing advanced/AIDS presentation definition of interest is **CD4 <200 cells/µL or an AIDS-defining illness**.
- CD4 200–349 is treated as Chronic for this model and is not part of the requested advanced/AIDS validation measure.
- The model does not explicitly track CD4. CD4 evidence is therefore an observation/validation construct, not a new simulated state in this task.
- Maximum user-run orchestration time is 2 hours.
- The run must be broken into resumable stages and shards. The user will execute commands manually.
- Prefer a single orchestration suite and output root that joins evidence, discovery, confirmation, and reporting in one provenance chain.
- Do not add loosely identified parameters merely to force agreement.

## Critical definition gate

Before designing or launching simulations, adjudicate the model-stage definition from code and sources.

Repository notes currently contain a potentially material seam:

- the requested empirical definition is CD4 <200 **or** AIDS-defining illness;
- `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`, `HM_MSM_AIDS_DIAGNOSTIC_RUN_2026-09-16.md`, and `MODEL_LIMITATIONS.md` describe the model compartment as clinical AIDS/AIDs-defining disease and state that CD4 <200 without an AIDS-defining condition may not be included;
- some reporting code and labels describe AIDS as CD4 <200.

The agent must not resolve this by label. Trace the actual transition parameter, its source definition, mortality logic, economic routing, observation counters, and report labels.

Create `AIDS_DEFINITION_ADJUDICATION.md` containing:

1. the operational simulator definition;
2. the source-paper definition used for Chronic-to-AIDS progression;
3. the definition used for mortality and economic parameters;
4. the definition of every surveillance comparator;
5. which comparisons are exact, approximate, lower bounds, upper bounds, or unavailable; and
6. the consequence for this experiment.

Do not redefine the biological AIDS compartment or progression rate in this task. If the model compartment is clinical AIDS rather than the requested composite, treat model diagnosis-in-AIDS as a narrower proxy and state that CD4 <200 without illness is structurally unobservable. If evidence establishes that the operative model stage genuinely represents the composite, document that evidence and use the composite label consistently.

Simulation plan freezing is prohibited until this adjudication passes review checks.

## Evidence sources and precedence

### Raw validation workbook

The principal workbook is outside the project subdirectory but inside the shared thesis workspace:

```text
../data/raw/validation/DiseaseData.xlsx
```

Relevant sheets currently include:

- `Stiching 1.1 (Diagnoses)`;
- `EDC- AIDS`;
- `EDC-HIV (Secondary)`;
- `RIVM-D76 AIDS`;
- `UNAIDS-HIV Infections`;
- `UNAIDS-PLHIV)`;
- `UNAIDS-AIDSDeath`;
- `UNAIDS-ART`.

Treat the workbook as raw/source-facing evidence. Do not edit it. Do not assume its absence of later methodological notes reverses decisions recorded after it was assembled.

### Decision notes that govern interpretation

Read completely before implementation:

- `HISTORY_MATCHING_BUILD_SPEC.md`;
- `configs/hm_target_register.json`;
- `RIVM_AIDS_TARGET_WINDOW_AUDIT_2026-09-21.md`;
- `HM_PRESENTATION_EVIDENCE_AND_COMPOUND_DESIGN_2026-09-16.md`;
- `MODEL_LIMITATIONS.md`;
- `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`;
- `HM_MSM_AIDS_DIAGNOSTIC_RUN_2026-09-16.md`;
- `TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_FOCUSED_CALIBRATION_2026-09-22.md`;
- `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/REPORT.md`;
- `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/REPORT_ADDENDUM_POSTHOC.md`;
- `outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/confirmation_summary.csv`.

Where the workbook and a later audited decision differ, preserve the raw value, identify the discrepancy, and apply the later frozen decision only when its provenance is documented.

### Mechanism literature

Use the four previously reviewed papers to justify testing heterogeneity and missed diagnosis opportunities, not to manufacture Dutch annual hazard estimates:

- `../literature/Bilsen(2021)-TestingPracticesLateStage.pdf`;
- `../literature/Berdet(2021)-ReasonsLateStageDiagnosis.pdf`;
- `../literature/Jordans(2024)-ProperTestingGuidnelinesHIV.pdf`;
- `../literature/Koole(2025)_PrEPBarriersMissedOpportunitiesMSM.pdf`.

Also inspect the stored SHM/RIVM reports used to construct the workbook and later AIDS-vintage audit. Record exact table, page, population, time window, numerator, denominator, missing-stage rule, and whether recent-infection classification takes precedence.

## Evidence register and epistemic roles

Create `empirical_constraint_register.csv` with one row per series/year or series/window and at least:

```text
metric | population | period | value | lower | upper | unit | source | source_location |
definition | direct_or_modelled | tier | role | model_output | mapping_quality |
overlap_warning | inclusion_decision | rationale
```

Apply the established distinction:

- **Tier 1:** directly observed surveillance/register counts. Eligible for primary calibration only when source window and model-output mapping are predeclared.
- **Tier 2:** model-derived estimates such as UNAIDS/Spectrum incidence, PLHIV, ART, and modelled AIDS deaths. Cross-validation/plausibility evidence only. Do not treat them as independent likelihood contributions because they are fitted partly to overlapping surveillance inputs.
- **Context/mechanism evidence:** testing behavior, missed opportunities, late-presentation fractions, and source-specific composition. Use for structural interpretation or bounded validation, not as annual counts unless the population and denominator align.

At minimum classify:

- MSM HIV diagnoses;
- total HIV diagnoses as an accounting comparator where appropriate;
- MSM AIDS diagnoses and the established AIDS proxy;
- diagnoses occurring while in the model AIDS state;
- advanced presentation defined as CD4 <200 or AIDS-defining illness, where observed;
- new infections;
- PLHIV;
- undiagnosed fraction or stock;
- ART stock/coverage;
- deaths.

Explicitly flag overlapping evidence so that multiple versions of one surveillance process do not receive duplicate weight.

## Observation constructs that must remain separate

The report must never collapse the following into one number:

1. biological Chronic-to-AIDS progression;
2. HIV diagnosis while already in the model AIDS state;
3. first AIDS recognition after an earlier HIV diagnosis;
4. the established `aids_first_diagnosis_proxy_msm`;
5. observed advanced presentation at HIV diagnosis: CD4 <200 or AIDS-defining illness;
6. observed clinical AIDS diagnosis by calendar year; and
7. the broader CD4 <350 late-presentation measure, which is context only and is excluded from the requested advanced/AIDS definition.

If an exact model analogue for item 5 does not exist, do not fit a free conversion coefficient in this task. Report a partial-identification comparison:

- the directly representable component;
- a justified lower or upper bound, if derivable;
- the unrepresented CD4-only component; and
- the resulting limitation.

## Models to compare

Use common random numbers and identical non-diagnosis parameters wherever comparisons permit.

### M0: legacy diagnosis structure

Existing diagnosis-era schedule and MSM relative multiplier, with no symptom component.

### M1: routine plus AIDS-symptom diagnosis

Retain the implemented cause-specific hazard structure and existing calendar/MSM multiplier geometry. Vary only the already authorized diagnosis coordinates:

- MSM routine multiplier anchors at 1996, 2004, and 2019;
- AIDS symptom-driven annual probability;
- `routine_hazard_scale = 1` fixed.

No activity, age, time-since-infection, false-negative, or group-by-stage interaction is added.

### M2: optional interpretable testing-mixture proposal

Do not implement or simulate M2 automatically. After the evidence audit, describe—but do not fit—an optional two-class mixture:

- regularly tested fraction;
- persistently under-tested fraction;
- shared symptom-driven recognition in advanced disease.

M2 may proceed only in a later task if the literature supplies a defensible bound on the under-tested fraction and M1 fails for a clearly diagnosed reason that M2 addresses. It is not a rescue coefficient for this run.

## Frozen primary and validation contract

Retain the previously agreed primary target windows unless the evidence audit finds a documented implementation error:

- MSM HIV diagnoses: 1996, 2000, 2004, 2010, 2015, 2019, 2024;
- MSM AIDS proxy: 2000, 2005, 2010, 2015, 2019, 2022;
- MSM AIDS 1990 and 1995: visible historical cross-validation only;
- target-relative model discrepancy: 10% primary, 20% structural sensitivity;
- primary acceptance: target-by-target `I_max <= 3`;
- explicitly incomplete terminal vintages excluded.

Add predeclared external checks without promoting Tier-2 series into primary calibration:

- incidence/new infections;
- PLHIV;
- ART stock or coverage;
- AIDS deaths or HIV-patient deaths with correct labeling;
- undiagnosed fraction/stock;
- advanced presentation defined as CD4 <200 or AIDS-defining illness, subject to the definition gate.

For every external check, predeclare before simulations whether it is:

- a numerical interval check;
- a direction/trend check;
- a one-sided bound;
- contextual only; or
- unavailable.

Do not invent a tolerance after seeing candidate results. If no defensible uncertainty or mapping exists, show the residual descriptively and mark it non-rejecting.

## Orchestration suite

Build one master CLI, preferably by extending the existing focused harness rather than duplicating simulator logic. It must expose independent checkpointed subcommands:

```text
audit-evidence
validate-plan
benchmark
discover --shard-index K --shard-count N
merge-discovery
freeze-shortlist
confirm --shard-index K --shard-count N
merge-confirmation
report
status
resume
```

The implementation agent may run only `audit-evidence` when it is strictly read-only/static extraction, unit tests, and `validate-plan`. No command may instantiate a real simulation engine during preparation.

The suite must:

- maintain a single top-level manifest and content hashes for code, configs, evidence register, plan, candidates, seeds, and shards;
- refuse to merge mismatched plans or duplicate trajectories;
- make every stage idempotent and refuse silent overwrite;
- write atomic checkpoints;
- report completed, missing, failed, and duplicate design/seed pairs;
- allow shards to run separately or sequentially on one machine;
- preserve all partial outputs after a time stop;
- never alter the source workbook, production configuration, or previous run outputs.

Suggested output root:

```text
outputs/calibration/diagnosis_triangulation_orchestration_20260922/
```

## User-run computational design

The exact trajectory count must be computed during `validate-plan` and written before launch. The default maximum design is:

### Stage 0: evidence and definition audit

No simulations. Produce the evidence register, AIDS definition adjudication, target contract, mapping table, and frozen plan.

### Stage 1: paired benchmark

Re-run with fresh shared seeds:

- M0 legacy backbone 1326;
- M0 legacy backbone 1788;
- `B_1326_v002`;
- `B_1788_v123`;
- `B_1788_v048` as an intentionally aggressive comparator.

Use 30 fresh seeds per tuple: at most 150 trajectories. Preserve seed-level paired differences.

### Stage 2: local discovery

Search bounded neighborhoods around the two balanced candidates, not the full original prior box.

- two backbones;
- 192 unique vectors per backbone, including identity and the confirmed candidates;
- five shared discovery seeds per vector;
- four deterministic shards per backbone unless runtime validation recommends more.

Maximum: `2 * 192 * 5 = 1,920 trajectories`.

Use a reproducible maximin design. Do not adapt ranges after viewing outcomes. Bounds must stay within the earlier authorized parameter ranges and should concentrate on the empirically useful region while retaining a documented outer ring that can reveal whether the region is isolated.

### Stage 3: fresh confirmation

Freeze at most 12 structurally diverse finalists before accessing confirmation seeds. Use 30 fresh seeds each, disjoint from every previous run and stage.

Maximum: `12 * 30 = 360 trajectories`.

### Maximum total

`150 + 1,920 + 360 = 2,430 trajectories`.

At the previous observed rate this should fit comfortably inside two hours with eight workers, but runtime must be benchmarked and the plan must stop dispatching at 110 minutes, leaving 10 minutes for merge/report. A partial run must be labelled `PARTIAL_TIME_LIMIT` and must not be interpreted as a completed design.

Confirmation has priority over optional figures. If discovery is slower than expected, complete fewer predeclared shards; do not reduce seeds selectively based on candidate performance.

## Scientific diagnostics

For every benchmark and confirmed tuple, report annual 1996–2025 trajectories and the 2025 starting state for:

- MSM infections;
- MSM diagnoses by Acute, Chronic, and AIDS stage;
- MSM undiagnosed stock by disease stage and activity stratum;
- diagnosed untreated stock;
- ART stock and coverage;
- total MSM PLHIV;
- HIV deaths by diagnosis/ART state;
- biological AIDS progression by prior diagnosis status;
- `aids_first_diagnosis_proxy_msm` and each component;
- fraction diagnosed while in model AIDS;
- all representable components of the CD4 <200 or AIDS-defining-illness validation measure;
- effective routine diagnosis probabilities at all schedule anchors and target years;
- complete post-2015 stock-flow ledger.

Decompose the change relative to the paired legacy backbone into:

1. altered diagnosis timing holding realized infections as a descriptive counterfactual where possible;
2. additional diagnoses caused by a larger undiagnosed stock;
3. additional diagnoses caused by increased infections;
4. symptom-driven diagnoses; and
5. downstream changes in ART and deaths.

If an exact counterfactual decomposition is not mathematically identifiable from saved draws, state that and use a clearly labelled accounting decomposition rather than inventing causality.

## Plausibility and identifiability checks

A candidate is not scientifically supported merely because it improves 2024 diagnoses over many simulations. Require:

- fresh-seed improvement in recent diagnoses;
- acceptable historical diagnosis trajectory;
- acceptable primary AIDS-proxy fit under the frozen discrepancy contract;
- no unexplained population or ledger failure;
- external outcomes lying within predeclared empirical ranges, or transparent explanation when only Tier-2/contextual evidence exists;
- a non-isolated neighborhood with the same qualitative behavior;
- no essential parameter pinned to a search boundary;
- a local sensitivity matrix with reported rank and condition number;
- assessment of whether symptom effects are distinguishable from routine-testing anchors;
- reporting of trade-offs between diagnosis improvement and infections/undiagnosed stock.

Do not treat 2,430 stable simulations as biological validation by themselves. Simulation count establishes numerical stability and maps the response surface; empirical correspondence establishes scientific plausibility.

## Decision classifications

### Supported as a preliminary structural improvement

Use only when at least one non-isolated M1 region:

- passes the frozen Tier-1 diagnosis/AIDS contract on fresh seeds;
- improves the 2024 diagnosis gap;
- is compatible with the predeclared advanced-presentation evidence to the extent the model can represent it;
- retains plausible latent epidemic states under independent or explicitly Tier-2 checks;
- has no unresolved directionally serious mortality/ART/population pathology; and
- is not identified only through a boundary or compensating ridge.

This permits M1 in a preliminary/base structural specification with its limitations documented. It does not authorize arbitrary retuning or production HTA adoption.

### Promising but underconstrained

Use when M1 robustly improves diagnoses and has a coherent mechanism, but the magnitude of the hidden epidemic cannot be validated independently, the advanced-presentation mapping is incomplete, or only a narrow region works. Retain M1 for structural sensitivity and state the smallest missing evidence or state variable.

### Unsupported in the tested form

Use when completed discovery and confirmation find no robust region that improves diagnoses without violating predeclared constraints, or when improvements consistently arise from empirically implausible epidemic expansion.

### Unresolvable with current disease states

Use when the decisive empirical construct is CD4/time-since-infection dependent and cannot be credibly mapped to Acute/Chronic/AIDS. Recommend a later structural extension, but do not build it in this task.

## Required files

Preparation outputs:

- `IMPLEMENTATION_NOTE.md`;
- `AIDS_DEFINITION_ADJUDICATION.md`;
- `empirical_constraint_register.csv`;
- `observation_mapping.csv`;
- `frozen_target_contract.json`;
- `frozen_plan.json`;
- `validation.json`;
- `USER_RUN_COMMANDS.md`;
- unit tests for evidence parsing, plan freezing, sharding, resume, and merge integrity.

Run outputs:

- `manifest.json` and `STATUS.md`;
- `benchmark_seed_level.csv` and `benchmark_summary.csv`;
- shard-level discovery outputs and `discovery_merged.csv`;
- `frozen_shortlist.json`;
- shard-level confirmation outputs and `confirmation_merged.csv`;
- `target_residuals.csv`;
- `external_plausibility_checks.csv`;
- `stage_diagnosis_decomposition.csv`;
- `post2015_stock_flow.csv`;
- `latent_state_2025.csv`;
- `identifiability.json`;
- no more than five decision-relevant figures;
- `REPORT.md` and, only if needed, a clearly labelled post-hoc addendum.

## Command handoff requirements

`USER_RUN_COMMANDS.md` must provide:

1. evidence audit and plan validation;
2. benchmark;
3. each discovery shard as a separate command;
4. discovery merge and shortlist freeze;
5. each confirmation shard as a separate command;
6. confirmation merge and final report;
7. a status command;
8. a safe resume command; and
9. an optional sequential `run-all` command that stops at the shortlist-review gate unless the user explicitly supplies an approval flag.

Every simulation command must:

- run from the repository root;
- use `.venv/bin/python` explicitly;
- use `set -o pipefail`;
- create log parents before `tee`;
- state trajectory count and expected runtime;
- support `caffeinate -dimsu` as a user choice;
- refuse silent overwrite;
- print the exact output directory and frozen-plan hash;
- preserve logs and checkpoints after interruption.

The implementation agent must stop after static audit, tests, and validate-only. It must not launch benchmark, discovery, or confirmation simulations.

## Final report questions

The first page of `REPORT.md` must answer in plain language:

1. What definition of AIDS/advanced presentation does the simulator actually represent?
2. Which empirical late/advanced-presentation measure can be compared directly, approximately, or not at all?
3. Does M1 improve diagnoses after fresh-seed confirmation?
4. How much of the improvement comes with additional infections and a larger undiagnosed reservoir?
5. Are those latent changes compatible with the predeclared evidence, with Tier-2 dependence stated?
6. What happens to ART, deaths, total PLHIV, and the 2025 starting state?
7. Is the acceptable region broad, narrow, isolated, or boundary-dependent?
8. Are routine and symptom parameters separately informed?
9. Is M1 supported, promising but underconstrained, unsupported, or unresolvable with current states?
10. What is the smallest scientifically motivated next step, if any?

Do not modify the production configuration, source workbook, previous calibration outputs, or HTA baseline.
