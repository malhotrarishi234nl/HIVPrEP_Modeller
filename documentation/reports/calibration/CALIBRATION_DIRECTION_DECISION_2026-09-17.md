# Calibration direction decision — 17 September 2026

> Archived under `documentation/reports/calibration/`; original filename retained.

Analysis only. No simulations, model edits, target edits, bound changes or acceptance changes. This memo supplements the completed controlled comparison; it does not replace its findings or imply approval to implement a new mechanism.

## Recommendation

Retain the ambition to reconstruct the historical MSM epidemic for a dynamic PrEP evaluation, but stop treating every failed fit as a request for another adjustable parameter. The next deliverable should be a **small comparative model specification**: the current structure versus a parsimonious, time-varying effective MSM transmission rate, with the same observation assumptions and acceptance rules. Reduce redundant parameter freedom when introducing that rate. Do not add AIDS-specific presentation in the same change.

This is a methodological recommendation, not a finding that historical transmission is the cause. Existing searches have not exhausted the current model. A structural comparison needs a competently fitted current-model comparator, not a new model compared only with an old poorly fitting point.

The early observation audit is closed as **definition unresolved**. Retain the existing first-recognition proxy as an explicitly conditional modelling choice for the proposed comparison. It is neither certified like-for-like nor demonstrably wrong. Further inspection of the same reports is unlikely to supply the missing registry rules. Reopen only on new registry documentation or a concrete counter defect.

## 1. What the existing experiment actually established

I ran the existing read-only summarizer against the saved 90 simulations. It verified artifact hashes, mode, seed membership and all nine cells. The following are recomputed means, with ten stochastic replicates per cell; they are not new simulations.

| Setting | MSM AIDS 1990 | MSM diagnoses 2000 | MSM diagnoses 2004 | MSM AIDS 2022 |
|---|---:|---:|---:|---:|
| Current observed targets | 318 | 360 | 572 | 57 |
| A baseline | 84.6 | 524.8 | 800.6 | 111.5 |
| Earlier diagnosis only | 105.7 | 495.8 | 764.1 | 106.8 |
| Larger initial prevalence only | 136.6 | 860.4 | 1311.6 | 173.9 |
| Lower AIDS mortality only | 105.0 | 782.7 | 1278.0 | 209.4 |
| All three changes | 219.4 | 1218.8 | 1983.6 | 326.2 |
| B measurement anchor | 355.8 | 1679.5 | 744.7 | 209.7 |

**Well-supported local result:** the earlier-diagnosis change improves both sides of the early/middle conflict, but modestly: +24.94% in AIDS 1990 and −5.53% in diagnoses 2000. It does not establish that diagnosis is the missing mechanism. Larger seeding approximately raises the whole trajectory, including targets already too high.

**Derived diagnostic, not proposed correction factors:** A needs observed/model ratios of 3.759 for AIDS 1990, 0.686 for diagnoses 2000, 0.714 for diagnoses 2004 and 0.511 for AIDS 2022. A single scale adjustment cannot satisfy these four means. The problem includes temporal shape, not just epidemic size. This does not prove the model cannot change shape through its existing dynamics.

No tested cell meets the declared joint rule at either discrepancy setting. At baseline the primary Imax is 6.733; earlier diagnosis gives 5.882. At the 20% sensitivity setting, the corresponding binding target is AIDS 2022, with Imax 3.983 and 3.483. Repairing early AIDS alone is insufficient.

The earlier revision-1 analysis reports zero joint survivors among 622 vectors and only one vector within a factor of two of all three middle-period diagnosis anchors. Those counts are **reported prior findings**, not recomputed here. They show thin useful coverage, not global infeasibility. The saved fresh-seed confirmation report likewise records that neither candidate 600 nor 621 passes the primary diagnosis-only rule, and neither passes joint scoring at either discrepancy setting.

## 2. What we are fitting

The current focused plan searches 33 parameters against seven selected MSM HIV-diagnosis counts and eight selected MSM AIDS counts. It is not presently fitting every available data series simultaneously.

These observations are downstream consequences of several processes:

- HIV diagnoses depend on past infections, survival while undiagnosed, diagnosis probability and registration.
- AIDS diagnoses depend on past infections, progression, diagnosis timing, ART and recognition/registration.
- Current infectious prevalence and its distribution by activity, diagnosis and treatment determine prospective prevention benefits.

**Inference, well-supported as a structural concern:** more than one parameter combination can generate similar diagnosis/AIDS totals while implying different undiagnosed stocks and future transmission. Merely having 33 parameters and 15 mean targets does not quantify the uncertainty, but those 15 means cannot locally identify 33 independent continuous directions. Constraints, external evidence and other observables must supply information. Stochastic variances do not automatically solve this problem; the current score mainly uses them as uncertainty allowances.

The code also uses an MSM diagnosis probability derived from base diagnosis probability times an MSM calendar multiplier, clipped before conversion to timestep probability (`run_v3.py`, `_step_diagnosis`). This creates potentially compensating parameter effects. It is not a claim that every pair of full schedules is exactly interchangeable. Interpret and plot the effective MSM schedule, rather than interpreting both factors independently as measured behaviour.

**Recommended fitting contract:** infer a plausible joint history of MSM infections and care entry that reproduces the declared diagnosis/AIDS observations and yields a credible intervention-start population. Report the fit as conditional on the observation proxy. Check available registered burden and ART stocks with matched population/denominator definitions; do not claim that such a mapping has already been established here. Compare with UNAIDS estimated incidence/prevalence as dependent model-based benchmarks, not independent validation.

Keep non-MSM diagnoses as the explicitly exogenous background already chosen. Their agreement is input reproduction, not evidence of successful endogenous epidemic calibration. Their all-Chronic entry assumption and absent pre-diagnosis history remain limitations, particularly for late-presentation costs and any claim about prevention outside MSM. Retaining this choice is defensible only with that scope stated.

## 3. Observation-contract verdict and stopping point

I inspected the RIVM 2016 report text and rendered pages: printed p.29 / PDF p.30, and printed p.110 / PDF p.111. Table 6.11 explicitly reports AIDS patients by year of AIDS diagnosis and transmission risk group; the 1990 MSM cell is 318. The methods identify voluntary Inspectorate AIDS reporting and the later SHM replacement. The table and methods disagree around the 1999/2000 splice, a previously noted issue which does not alter the 1990 source.

The existing proxy is:

    progression to AIDS while already HIV-diagnosed
    + HIV diagnosis while already in the AIDS compartment.

It is a reasonable *approximation* to first AIDS recognition under the model's irreversible stage structure. Undiagnosed progression alone is not a diagnosis, and adding it to later recognition would count some people twice. Counting undiagnosed deaths as diagnoses is also not established by these sources.

The inspected reports do not specify sufficiently detailed rules for first/recurrent events, backdating onset versus recognition, post-mortem ascertainment, earlier diagnoses abroad, historical clinical case criteria, or overlap between behavioural orientation and surveillance transmission category. They do not supply an adjustment magnitude. The model likewise does not encode all those distinctions. Therefore:

- **Verdict: definition unresolved**, not mismatch established.
- **Recommended modelling choice:** retain the proxy conditionally; disclose limitations and preserve separate component outputs.
- **No numerical mapping correction is justified now.** Voluntary underreporting alone would not explain a model far below observed counts under otherwise aligned definitions: correcting an undercount upward would enlarge that shortfall.
- **Stop:** no more searches of the same source corpus and no presentation parameter introduced merely to fill the gap.

The earlier HIV-registration problem is different. Prior ECDC and SHM memos identify survivor/enrolment restrictions, especially before 1996 and around the original cohort's formation. Do not transfer that finding automatically to the older Inspectorate AIDS registry. Preserve the current primary scoring for an apples-to-apples model comparison and report the already motivated 1996-exclusion sensitivity separately; do not remove it to obtain acceptance.

## 4. What Spectrum does differently

The published CSAVR method can estimate incidence with single/double logistic or spline functions, or a model with time-varying transmission. Its 2021 development allows diagnosis to vary with time and disease stage, including an opportunistic-infection component. EPP also has published time-varying infection-risk formulations. These are precedents for parsimonious temporal flexibility, not proof that either mechanism will solve this Dutch model's residuals.

Sources: Mahiane et al. (2019), https://pubmed.ncbi.nlm.nih.gov/31385865/; Mahiane et al. (2021), https://pubmed.ncbi.nlm.nih.gov/34546641/; Hogan and Salomon (2012), https://pmc.ncbi.nlm.nih.gov/articles/PMC3512430/.

**Important limit:** I have not established which exact model, settings, input vintages or fitting targets generated the Dutch UNAIDS workbook estimates. Spectrum is a suite, not one calibrated Dutch comparator. RIVM 2016 p.29 itself mentions the ECDC modelling tool for estimating Dutch PLHIV in 2014. Consequently “Spectrum fits these exact 15 targets” is unverified.

Our current contact-rate inputs have no historical calendar schedule in `_step_transmission`; epidemic dynamics, treatment and PrEP can nevertheless produce changing incidence. Allowing testing to vary cannot be assumed to substitute for allowing the transmission environment to vary. Conversely, fitting an incidence curve does not by itself supply a causal model of PrEP benefits.

## 5. The smallest coherent next proposal

Prepare one reviewable specification, not another literature audit or calibration launch:

1. **Comparator:** existing simulator and observation proxy, with a reduced, interpretable parameterisation and multiple starts. Keep natural-history and care parameters evidence-constrained; any fixed uncertain inputs need declared sensitivity cases. Do not silently choose nuisance values because they fit best.
2. **One alternative:** replace redundant MSM transmission-scale freedom with one effective rate and a low-dimensional historical schedule. One coefficient must anchor its scale; do not freely estimate both an overall scale and every relative level. Proposed dates, smoothness and bounds must be stated as evidence-based choices or explicit assumptions before fitting. This memo does not supply invented empirical values.
3. **Preserve causal intervention dynamics:** apply the historical schedule to transmission opportunities/risk, common to both intervention arms. Do not prescribe the same future number of infections in both arms. State the future continuation rule and test its influence on incremental benefits.
4. **Do not simultaneously fit a new AIDS-presentation hazard.** A separate presentation proposal needs stage-specific evidence or explicit sensitivity assumptions. Such a hazard changes care entry, survival and economics; it is not a reporting correction.
5. **Use current primary targets and discrepancy for comparison.** Plot all available intervening years as additional trajectory checks; describe these as partly reused evidence, not pristine prospective validation. Examine early AIDS, middle diagnoses and recent AIDS together, plus intervention-start stocks. Matching annual totals alone is insufficient for an HTA baseline.

This does not require an identifiable estimate of every contact or biological parameter. It requires a credible range of baseline states and intervention effects under explicit assumptions.

## 6. Stop rules and usable endpoints

Before running the comparison, specify a finite evaluation/replicate budget and reserve confirmation seeds. The budget is a resource decision, not a claim of exhaustive search. Choose success criteria before observing results, including trajectory agreement and adequacy of the intervention-start state; retain the current primary implausibility rule.

- If the current model succeeds with credible states, retain it. Temporal flexibility was unnecessary.
- If only the temporal alternative succeeds, with credible states and acceptable trajectory checks, retain the simpler adequate specification. Do not claim the fitted schedule identifies a unique behavioural cause.
- If neither succeeds, or success depends on arbitrary assumptions/boundary values, close this historical model-comparison attempt. No automatic extra multipliers, wider discrepancies or repeated “last” sweeps.
- The next choice is then a deliberate scope change: initialise a modern PrEP model from observed care/burden data plus explicit uncertainty in undiagnosed and risk-group stocks, or undertake a larger historical-model redevelopment. Modern initialisation is a legitimate alternative research design, not a claim that the current historical model passed. It may suit the PrEP decision better, but should not happen silently.

## Reproducibility and limits of this analysis

Repository: `hiv-prep-model`; HEAD `0ffdbd5f93ec1d2057e6f814bb2adfc3580dfc1e`, extensively modified/untracked working tree. HEAD alone does not reproduce the working simulator. Existing experiment artifacts carry their own hashes and frozen specification.

Recompute the table and local scores, without simulations:

    .venv/bin/python tools/summarize_joint_fit_comparison_20260917.py

Inputs: `outputs/calibration/hm_joint_fit_comparison_20260917/`; nine configurations, seeds 195000–195009; focused register and plan dated 20260916. Source values were loaded through `hm_target_register.load_register` and restricted to the plan's years. Derived scaling ratios above are observed divided by the printed baseline means; percentage changes are 100 × (earlier/baseline − 1).

Other inspected records: `HM_JOINT_COMPARISON_FINDINGS_2026-09-17.md`; `outputs/calibration/hm_joint_fit_conflict_20260917_rev1/FINDINGS_REV1.md`; `outputs/calibration/hm_focused_confirmation_600_621_20260917/RUN_SUMMARY.md`; `MODEL_LIMITATIONS.md`; `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`; `aids_observation.py`; `run_v3.py`; current focused plan, space and register.

No full test suite was run because no executable code changed. No new feasibility claim, calibrated ensemble, final baseline, or economic result is produced. The completed deliverable is the bounded source verdict and the recommended model-comparison decision.
