# Task brief: interim abstract evidence package

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

Date: 2026-09-23  
Status: ready for agent handoff  
Output root: `outputs/abstract_evidence_20260923/`  
Purpose: produce an auditable interim evidence package for an abstract submission due at the end of the week. This is also a dry run of the final-results evidence workflow.

## 1. Objective

Assemble, validate and organize the current epidemiological and economic evidence into a compact package from which the user can write an abstract.

The package must answer:

1. How well do the legacy and stage-dependent-diagnosis epidemic structures reproduce the historical evidence?
2. How much does the epidemic structure change projected intervention benefits?
3. How do lenacapavir substitution and add-on expansion differ?
4. Does the cost-effectiveness conclusion change across epidemic structures, deployment strategies or the symmetric no-ISR sensitivity?
5. Is product price still the binding economic constraint?
6. Which results are sufficiently mature for an interim abstract, and which must remain sensitivities, context or future work?

The package is not a thesis rewrite, a new broad calibration, or a declaration that either model is globally valid. It must preserve uncertainty and inconvenient findings.

## 2. Scientific framing fixed for this task

### 2.1 Epidemic structures

Use two historical epidemic structures:

- **Legacy:** `D_1788_v000`.
- **Late diagnosis:** `D_1788_v109`.

The legacy structure is the current primary baseline for the interim package. The late-diagnosis structure is a structural epidemic sensitivity unless the unfinished whole-model review produces strong evidence for another classification.

Both structures pass the frozen 13-target history-matching rule, but this means only that neither is ruled out by that contract. It does not establish unique identification, global validity, predictive validity, or validation of downstream economic assumptions.

### 2.2 Forward policy structure

Keep these roles distinct:

- **No-PrEP counterfactual:** attribution context only. It resets PrEP at the forecast fork and is not a realistic policy comparator.
- **Oral-PrEP standard of care:** decision comparator. It carries the observed 2025 stock forward and follows the target-stock pathway 13,000 in 2025, 14,000 in 2027 and 15,000 in 2030, remaining at 15,000 thereafter.
- **Lenacapavir substitution:** five variants switching 10%, 25%, 50%, 75% or 100% of eligible oral-PrEP users to lenacapavir while keeping total PrEP coverage broadly comparable.
- **Lenacapavir add-on expansion:** three variants adding 500, 1,500 or 3,000 high-risk lenacapavir users by 2030 on top of oral SOC.
- **Full oral-PrEP expansion benchmark:** contextual benchmark increasing oral PrEP to 28,000 by 2030. Do not describe it as a forecast.

The five substitution and three add-on variants are the eight lenacapavir deployment scenarios. Do not call all eight “substitution levels.”

### 2.3 Adverse-event convention

The current economic configuration applies lenacapavir ISR/nodule disutility and zero oral-PrEP AE disutility. Evidence supports the ISR frequencies but not the exact utility weights. Evidence supports mild oral-PrEP AEs but does not provide a defensible preference-based renal or GI disutility.

The required alternative is therefore:

- **Current configured AE convention**, and
- **Symmetric no-ISR sensitivity**, setting the lenacapavir ISR/nodule utility decrement to zero while leaving epidemiology and costs otherwise unchanged.

Do not invent or headline an oral-PrEP renal disutility. A renal-AE case may only be proposed as explicitly exploratory future work with a separate evidence justification.

### 2.4 Forward PrEP uncertainty

The relevant corridor is prospective programme coverage and persistence after the 2025 fork. Do not reopen historical PrEP calibration in this task.

The current SOC pathway is a target-stock schedule, not a constant annual uptake-rate model. If growth is summarized, state that 13,000 to 15,000 over 2025-2030 is approximately 2.9% compound annual growth, while preserving the target-stock interpretation.

Persistence and target coverage are different quantities. Under target-stock control, higher dropout may increase replacement initiation and programme turnover without proportionally lowering maintained stock. Keep persistence as an unresolved forward sensitivity, not a historical calibration claim.

## 3. Run authority and scope limits

### Authorized now

- Read and analyze all existing outputs.
- Complete the unfinished late-diagnosis whole-model review from saved data.
- Build and test reporting/analysis code.
- Prepare frozen matched add-on and AE-sensitivity configurations.
- Produce dry-run validation, trajectory counts and runtime estimates.
- Produce exact user terminal commands.
- Create the evidence package, workbook and charts after required simulation outputs exist.

### Heavy runs

Heavy simulations must be launched by the user in their terminal. The agent must not launch them silently.

Before asking the user to run anything, provide:

- an immutable experiment/design JSON;
- exact configuration hashes;
- matched seed list;
- scenario/arm/trajectory count;
- maximum expected runtime;
- validation-only command and result;
- exact full-run command;
- tests for scenario construction, pairing and output identities;
- a new output directory that does not overwrite existing work.

### Not authorized in this task

- A broad recalibration or optimization.
- Changing targets, uncertainty terms, implausibility cutoffs or WTP thresholds.
- Widening parameter bounds to create favorable results.
- Moving, renaming or modifying completed raw-output directories.
- Replacing the production or HTA baseline.
- Treating the abstract deadline as justification for suppressing uncertainty.
- Inventing missing adverse-event utilities or persistence evidence.

## 4. Required sources

Read these completely before analysis or implementation:

### Historical diagnosis review

- `TASK_BRIEF_STAGE_DIAGNOSIS_CANDIDATE_BASELINE_REVIEW_2026-09-22.md`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/STATUS.md`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/DATA_INTEGRITY_NOTE.md`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/target_implausibility_table.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/external_evidence_comparison.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_vs_legacy_annual.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_vs_legacy_milestones.csv`
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/REPORT.md`
- `outputs/calibration/diagnosis_triangulation_orchestration_20260922/AIDS_DEFINITION_ADJUDICATION.md`
- `MODEL_LIMITATIONS.md`

### Completed matched HTA

- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/manifest.json`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/REPORT.md`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/per_seed_economics.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/decision_summary.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/paired_structure_difference_summary.csv`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/pooled_price_thresholds.csv`
- `tools/run_stage_diagnosis_matched_hta.py`
- `tests/test_stage_diagnosis_matched_hta.py`

### Economic and AE evidence

- `configs/hta_runtime_config.json`
- `configs/hta_costing_config.json`
- `outputs/hta_results/ae_adherence_corridor_20260914_225729/REPORT.md`
- `outputs/hta_results/ae_adherence_corridor_20260914_225729/SOURCE_VERIFICATION.md`
- `outputs/hta_results/ae_adherence_corridor_20260914_225729/PSA_COVERAGE_GAP.md`
- `outputs/hta_results/a30_dqaly_decomposition_20260914_190815/REPORT.md`
- `outputs/hta_results/a30_dqaly_decomposition_20260914_190815/analysis_summary.json`
- `outputs/hta_results/a16_stochastic_headline_20260911_030744/A16_STOCHASTIC_HEADLINE.md`
- `outputs/hta_results/a11_task0_thesis_config_current_code_20260910_041947/` for legacy expansion context only; do not headline these results without current matched confirmation.
- `outputs/hta_results/rivm_prep_targets_20260914_134653/RIVM_PrEP_target_admissibility.md`

Record every source actually used in `SOURCE_REGISTER.csv`. Treat prior generated prose and table labels as claims to verify, not authority.

## 5. Known controls that must reproduce

A mismatch stops the corresponding analysis until resolved.

### 5.1 Historical controls

Legacy versus late diagnosis:

- Both have 30 confirmation seeds and the identical confirmation seed set.
- Legacy `I_max = 1.919134...`.
- Late diagnosis `I_max = 2.634565...`.
- Late diagnosis is better on 6 of 13 primary targets; legacy on 7 of 13.
- Mean primary implausibility is approximately 1.109 late diagnosis and 1.096 legacy.
- 2024 MSM diagnoses: 305.67 late diagnosis, 195.47 legacy, observed 318.
- 2024 MSM infections: 292.97 late diagnosis, 184.97 legacy, Tier-2 interval 250-365.
- End-2024 undiagnosed MSM: 915.93 late diagnosis, 424.97 legacy, Tier-2 interval 690-900.
- Late-diagnosis mean reservoir exceeds 900 by 15.93, or 1.77%; 15 of 30 seeds lie inside 690-900.
- MSM HIV deaths 2015-2024: 1,202.73 late diagnosis, 991.00 legacy.

### 5.2 Completed matched substitution HTA controls

The completed run uses stochastic seeds 42-56, n=15 per epidemic structure. These are not the history-matching confirmation seeds and must not be merged as one uncertainty sample.

- 180 unique structure-seed-scenario rows.
- All seven manifest output hashes pass.
- NMB identity error no larger than floating-point roundoff.
- At €80,000/QALY, probability of positive NMB is zero at every substitution level under both structures.
- Configured annual lenacapavir price: approximately €41,110.44.

At 10% substitution:

- Legacy: Δcost €237.52m; ΔQALY 81.79; infections averted 10.47; NMB80 −€230.98m; pooled CE price €2,231.
- Late diagnosis: Δcost €230.61m; ΔQALY 125.74; infections averted 18.53; NMB80 −€220.55m; pooled CE price €3,035.

At 25% substitution:

- Legacy: Δcost €617.67m; ΔQALY 56.45 with its mean CI crossing zero; infections averted 20.00; NMB80 −€613.16m; pooled root €1,406.
- Late diagnosis: Δcost €602.92m; ΔQALY 105.17; infections averted 45.27; NMB80 −€594.51m; pooled CE price €1,797.

At 100% substitution:

- Legacy: Δcost €2,578.83m; ΔQALY −785.35; infections averted 99.20; NMB80 −€2,641.66m; pooled QALY-loss compensation price €178.
- Late diagnosis: Δcost €2,520.57m; ΔQALY −563.95; infections averted 195.07; NMB80 −€2,565.68m; pooled QALY-loss compensation price €522.

Candidate-minus-legacy differences at 100%:

- ΔΔcost −€58.26m.
- ΔΔQALY +221.40.
- Δ infections averted +95.87.
- ΔNMB80 +€75.98m.

The late-diagnosis structure materially changes benefits but does not change the decision at the configured price.

## 6. Work plan: four phases

## Phase 1 — provenance, validity and historical interpretation

### 1A. Freeze provenance

Update `STATUS.md` and populate `SOURCE_REGISTER.csv` with:

- exact path;
- artifact role;
- completion/supersession status;
- configuration or file hash where available;
- seeds and horizon where applicable;
- whether it may support a headline, sensitivity or context only.

Do not relocate source artifacts.

### 1B. Complete the late-diagnosis whole-model review

Finish Phases 4-6 of the existing stage-diagnosis brief using saved outputs:

- mortality by MSM/non-MSM, diagnosis and ART state, year and available activity stratum;
- AIDS-burden decomposition;
- deaths per PLHIV and per undiagnosed person-year where supported;
- timing of divergence;
- reservoir accumulation;
- diagnosis, infection and ART catch-up dynamics;
- non-MSM changes;
- abrupt-anchor/artifact checks;
- total PLHIV and ART comparisons;
- formal classification under the existing three permitted labels.

Do not run a new historical simulation solely to finish this review.

### 1C. Write the credibility ladder

Create a concise section distinguishing:

1. computational verification;
2. historical calibration/history matching;
3. internal trajectory checks;
4. external or cross-model triangulation;
5. construct validity;
6. predictive validation, which is not currently established;
7. decision robustness.

Use “cross-model triangulation” for Spectrum/ECDC-tool comparisons unless true independence is demonstrated. Overlapping surveillance inputs mean agreement is supportive but not proof of truth.

### Phase 1 gate

Proceed if integrity remains intact and late diagnosis is classified as ready for matched preliminary forecasts or retained as a structural sensitivity. Stop if it is rejected in the tested form.

## Phase 2 — unified matched deployment and AE analysis

### 2A. Extend the matched design

Build a frozen matched analysis containing:

- two epidemic structures;
- oral SOC comparator;
- five substitution variants;
- three add-on variants;
- oral expansion benchmark as context;
- stochastic seeds 42-56;
- current configured AE convention;
- identical economics, WTP, price, horizon and non-diagnosis inputs.

The new run must preserve the current substitution results or explain any difference before proceeding.

### 2B. Retain decomposition fields

Save, per structure, seed, scenario and arm:

- discounted and undiscounted cost;
- discounted and undiscounted QALYs;
- cost components;
- new infections, diagnoses and HIV deaths;
- oral-PrEP and lenacapavir person-time;
- current ISR/nodule QALY decrement;
- price-sensitive lenacapavir product cost;
- achieved target stock and any shortfall;
- comparator and intervention arm identities.

### 2C. Derive symmetric no-ISR results

Because the configured AE utilities are post-hoc accounting and do not feed transmission, calculate the no-ISR sensitivity from retained person-time and QALY components where the exact identity is verified. Do not call an approximation exact.

If exact reconstruction is not possible from retained outputs, prepare a minimal economic rerun with zero ISR/nodule utility decrements. Do not rerun transmission unnecessarily.

### 2D. Run preparation

Before the user runs the analysis, provide:

- frozen design JSON;
- config and code hashes;
- trajectory count;
- runtime estimate;
- validation-only output;
- focused tests;
- exact command in `USER_RUN_COMMANDS.md`;
- stop/go explanation.

### Phase 2 gate

All scenario-arm definitions, seeds and non-target settings must match. The add-on arm must increase total PrEP coverage; substitution must preserve total target stock within implementation tolerance. Any unexplained failure stops interpretation.

## Phase 3 — economic validation and evidence classification

### 3A. Required equations

Implausibility:

```text
I_i = |E[f_i(x)] - z_i| / sqrt(V_obs,i + V_disc,i + V_process_predictive,i)
I_max = max_i I_i
```

Net monetary benefit:

```text
NMB(lambda) = lambda * DeltaQALY - DeltaCost
```

Price root, scaling only the configured lenacapavir product bundle:

```text
DeltaCost_nonproduct = Cost_intervention - Cost_reference - LenaProductCost_at_p0
p_star = p0 * (lambda * DeltaQALY - DeltaCost_nonproduct) / LenaProductCost_at_p0
```

AE reweighting:

```text
DeltaAE = -d_lena * LenaPY + d_oral * (OralPY_reference - OralPY_intervention)
DeltaQALY = DeltaQALY_excluding_AE + DeltaAE
```

Verify each identity independently against representative raw rows.

### 3B. Naming rules

- If mean ΔQALY is positive and its confidence interval excludes zero, `p_star` may be called a cost-effectiveness annual price threshold.
- If ΔQALY is negative, call it a QALY-loss compensation price.
- If the ΔQALY confidence interval crosses zero, report the root with a sign-instability warning. Do not present a stable conventional ICER.
- Never average seed-level ICERs. Use a ratio of mean incremental cost to mean incremental QALYs only when interpretable.
- Report NMB and cost-effectiveness-plane/quadrant uncertainty even when an ICER is unavailable.

### 3C. Decision-invariance analysis

For every deployment scenario, answer:

- Does NMB remain negative at the configured price under both epidemic structures?
- How much does late diagnosis change infections averted, QALYs, costs and NMB?
- Does add-on expansion yield materially more benefit than substitution?
- Does symmetric no-ISR change only ΔQALY/dominance language, or the NMB decision?
- What price reduction is required relative to €41,110.44?
- Is a quoted price a CE threshold or compensation price?
- Is the price result stable enough to headline, or only a directional corridor?

### 3D. Evidence labels

Assign every result one status:

- `primary_interim_result`
- `structural_sensitivity`
- `economic_sensitivity`
- `context_only`
- `not_ready`

Populate `CLAIMS_REGISTER.csv` with approved and prohibited wording.

### Phase 3 gate

A result cannot become a primary interim result unless its seed pairing, arithmetic, uncertainty, source, causal interpretation and known limitations all pass. An abstract deadline is not an exception.

## Phase 4 — evidence package and abstract handoff

### 4A. Required tables

Create:

- `headline_results.csv`
- `historical_model_comparison.csv`
- `deployment_scenarios.csv`
- `economic_sensitivity.csv`
- `price_thresholds.csv`
- completed `SOURCE_REGISTER.csv`
- completed `CLAIMS_REGISTER.csv`
- `EQUATIONS_AND_DEFINITIONS.md`
- `EVIDENCE_STATUS.md`
- `ABSTRACT_INPUTS.md`

`headline_results.csv` must contain, at minimum:

- epidemic structure;
- deployment family and level;
- AE convention;
- comparator/intervention;
- n seeds;
- mean, SD and 95% CI for Δcost, ΔQALY and infections averted;
- NMB at all three WTP thresholds;
- probability NMB > 0;
- correctly named price quantity;
- current-price reduction required;
- evidence status;
- concise abstract-safe interpretation.

### 4B. Workbook

Create one audit-friendly workbook only after CSVs pass:

```text
abstract_evidence_pack.xlsx
```

Recommended tabs:

1. `Headline results`
2. `Historical comparison`
3. `Deployment scenarios`
4. `Economic sensitivity`
5. `Claims register`
6. `Sources and checks`

The workbook must link or formula-calculate summaries from retained source tables. Do not paste unexplained headline values. Include units, comparator, horizon and uncertainty in labels. Verify formulas, render every sheet and scan for errors before delivery.

### 4C. Figures

Maximum five:

1. Historical fit: selected targets under legacy and late diagnosis.
2. Epidemic trajectories: diagnoses, incidence and undiagnosed MSM.
3. Infections averted across five substitution and three add-on scenarios, by epidemic structure.
4. NMB at current price across deployment scenarios.
5. Current price versus valid price thresholds; optional AE decomposition only if more informative.

Do not place conventional CE thresholds and QALY-loss compensation prices on an unlabeled common series.

### 4D. Abstract inputs

`ABSTRACT_INPUTS.md` should provide evidence, not write the user's final prose. Include:

- one-sentence background;
- methods bullets;
- three to five numerical results with uncertainty;
- decision-invariance statement;
- limitations;
- approved terminology;
- alternative wording if expansion changes the conclusion;
- list of claims that are not defensible.

## 7. Claims discipline

### Allowed if controls pass

- Both epidemic structures satisfied the prespecified 13-target history-matching criterion.
- Late diagnosis improved recent diagnoses and several latent epidemic estimates while increasing epidemic size and mortality.
- The epidemic structure materially changed projected prevention benefits.
- At the configured price, lenacapavir substitution had negative NMB under both epidemic structures.
- The cost-effectiveness conclusion was invariant to epidemic structure for the tested substitution scenarios.
- Price was the binding constraint for the tested scenarios, if confirmed after add-on and AE analyses.

### Prohibited or qualified

- Do not say `I < 3` proves the model is valid.
- Do not say every economic scenario passed history matching.
- Do not say Spectrum independently validated the model without demonstrating independent inputs.
- Do not call 2024 diagnosis performance out-of-sample validation; it belongs to the fitting contract.
- Do not say late diagnosis is the true or preferred structure unless Phase 1 supports that judgment.
- Do not imply the papers identify the exact symptom probability or routine-diagnosis anchors.
- Do not call a negative-QALY root a cost-effectiveness threshold.
- Do not report a precise universal “threshold corridor” if the root is conditional, undefined or unstable across scenarios.
- Do not infer add-on expansion results by scaling substitution results.
- Do not use older deterministic expansion outputs as final matched stochastic evidence.
- Do not claim that removing ISR validates zero adverse-event burden; it is a convention sensitivity.
- Do not introduce oral renal disutility without a defensible preference-based source.

## 8. Principal concerns to investigate, not hide

1. **Late-diagnosis mortality:** the candidate increases deaths and the model's AIDS/CD4 mapping is imperfect.
2. **Structural identifiability:** routine and symptom diagnosis effects are not fully separated on backbone 1788.
3. **External-estimate dependence:** Spectrum/ECDC-tool estimates may share surveillance inputs with calibration targets.
4. **Price-root instability:** QALY sign changes make the threshold quantity conditional or change its meaning.
5. **AE asymmetry:** lenacapavir ISR utility is weakly sourced while oral AEs are assigned zero.
6. **Transient ISR duration:** annual application may overstate short-duration ISR burden.
7. **Lenacapavir persistence:** 5% annual dropout is an optimistic bound, not a defensible central real-world estimate.
8. **Oral effectiveness/dropout double counting:** the 0.86 effectiveness estimate may already embed imperfect adherence while the model also applies dropout.
9. **Target-stock semantics:** coverage targets may mask churn by replacing discontinuers.
10. **Forecast status:** the policy pathways are scenarios, not predictions of Dutch uptake.
11. **Uncertainty separation:** do not pool history-matching confirmation seeds with HTA seeds.
12. **Comparator interpretation:** oral SOC may itself have negative NMB versus no PrEP; no-PrEP remains attribution context, not the reimbursement comparator.

## 9. Stop conditions

Stop the affected phase and document the issue if:

- a saved-file hash fails;
- seed sets or arm definitions do not match;
- non-diagnosis configuration differences appear;
- the completed substitution run cannot be reproduced within numerical tolerance;
- add-on fails to increase total PrEP coverage as defined;
- stock targets are systematically missed without explanation;
- NMB or price-root identities fail;
- AE reweighting is not exact but is being presented as exact;
- an outcome required for a claim is unavailable;
- an external source is being treated as independent despite shared inputs;
- a scenario changes meaning between structures;
- a favorable result depends on a retrospective threshold or unapproved parameter change.

Do not repair a stop condition by suppressing rows, widening bounds or changing labels.

## 10. Required package structure

All curated products belong under:

```text
outputs/abstract_evidence_20260923/
```

Expected structure:

```text
README.md
STATUS.md
SOURCE_REGISTER.csv
CLAIMS_REGISTER.csv
DECISION_LOG.md
EVIDENCE_STATUS.md
EQUATIONS_AND_DEFINITIONS.md
ABSTRACT_INPUTS.md
USER_RUN_COMMANDS.md                 # only when a run is ready
frozen_design.json                   # only when a run is ready
headline_results.csv
historical_model_comparison.csv
deployment_scenarios.csv
economic_sensitivity.csv
price_thresholds.csv
abstract_evidence_pack.xlsx
figures/
matched_deployment/                  # new simulation products only
```

Do not copy large raw files merely for convenience. Register and link them by path and hash.

## 11. Completion criteria

The task is complete when:

- provenance is frozen and every used artifact is registered;
- the whole-model late-diagnosis judgment is complete;
- matched substitution and add-on results exist for both epidemic structures;
- the symmetric no-ISR sensitivity is computed consistently;
- all arithmetic and semantic checks pass;
- every headline claim has a source, status and caveat;
- figures and workbook trace to the validated tables;
- the user receives an abstract-input memo, not an overconfident ghostwritten conclusion;
- unresolved final-analysis work is clearly separated from abstract-ready interim evidence.

## 12. First agent checkpoint

Before preparing or running the matched deployment analysis, report:

1. reproduced historical and matched-substitution controls;
2. completed source register;
3. late-diagnosis whole-model classification and supporting evidence;
4. exact proposed scenario matrix;
5. exact fields to retain for AE reweighting;
6. validation results and test list;
7. trajectory count, runtime estimate and commands;
8. stop/go recommendation.

Wait for user approval before instructing or executing a heavy run.
