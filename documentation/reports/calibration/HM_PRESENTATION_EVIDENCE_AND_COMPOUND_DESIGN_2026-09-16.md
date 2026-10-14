# Presentation-stage evidence and a staged history match

> Archived under `documentation/reports/calibration/`; original filename retained.

Date: 2026-09-16. Research and design review; no simulations or production configuration changes.

## Findings that change the previous recommendation

1. The current AIDS target has an unresolved historical source conflict. RIVM's stored 2008 report gives 419 AIDS diagnoses in 1990, whereas the stored ECDC Atlas extract gives 48. Verifying that the workbook matches the extract did not validate national historical coverage. Do not explain away this conflict with model discrepancy or estimate presentation fractions from these counts yet.
2. AIDS on presentation and AIDS developing after an earlier HIV diagnosis can be represented separately. Their sum is only a valid surveillance comparator after aligning first-event rules, dates, population coverage, and notification. Merely relabelling biological progression as a diagnosis event is insufficient.
3. The cited stochastic history-matching literature retains process variance when comparing observations with a possible stochastic realization. The runner currently uses per-run variance divided by replicate count. That is a mean-matching contract, and is not automatically the same scientific model. The previous audit's conclusion that precision alone should remove the full stochastic allowance was too broad.
4. Stored evidence can constrain an uncertain presentation model but does not identify an annual non-MSM clinical-AIDS fraction. An estimated fraction is possible, provided its assumptions remain explicit and it is not presented as an observed annual series.

## Evidence inspected in this review

Paths are relative to the project root above `hiv-prep-model` unless stated otherwise. PDF page references below are the extracted PDF pages.

| Source | What it supplies | Appropriate use and limitation |
|---|---|---|
| `literature/STICHTING(2025)-HIVMonitoringReport2025NL.pdf`, pp.26–33, Tables 1.2–1.3 | Same-period group-specific late-stage counts and pooled clinical AIDS at diagnosis in 2022–2024 | Can constrain recent presentation composition. Late-stage includes low CD4 without AIDS; recent-infection classification takes precedence. No clinical-AIDS-by-group cross-tabulation. |
| `literature/Boender(2018)_ATHENANetherlandsHIVCohortProfile.pdf` | Prior repository evidence review identifies route-specific AIDS-at-diagnosis proportions among people in care at January 2017 | Historical plausibility information, not diagnosis-year proportions: survival and retention select the denominator. This review relied on the earlier detailed extraction for this source. |
| `literature/deCoul(2015-FactorsLateHIVPresentationsNL.pdf` | Prior repository review identifies pooled late/advanced presentation evidence, 1996–2014 | Supports presentation mechanisms, but CD4 composites cannot be treated as clinical AIDS. No annual non-MSM clinical-AIDS fraction established. |
| `literature/van der Sande(2008) MAYBE.pdf`, pp.68–69, Tables 6.17–6.18 | Annual AIDS diagnoses, including transmission-group breakdowns | Potentially valuable independent historical evidence for source reconciliation and a correctly defined AIDS observation model. Not the numerator for AIDS concurrent with HIV diagnosis. |
| `literature/OpDeCoul(2003)_HIVAidsNetherlandsSurveillanceReport.pdf`, pp.1–2 | National surveillance changed in 2002; older AIDS registration was replaced by SHM reporting | Concrete reason to investigate coverage/vintage before treating historical series as exchangeable. Does not itself establish the cause of the numerical discrepancy. |
| `literature/VanDerBij(2008)-TestingDifferencesHeteroHomoNL.pdf`, pp.1–4 | Testing uptake and awareness in an Amsterdam STI-clinic population | Mechanistic context for changing diagnosis practice; not a national diagnosis probability or presentation-stage series. |
| `literature/Popping(2023)_HIVCareCostsTimedDelayedPresentation.pdf`, p.3 | Adults initiating ART during 2013, followed for five years; costs grouped by CD4/presentation at ART initiation | Relevant costs, but not a national stage-at-diagnosis distribution. Costs start at ART initiation and include inpatient care during follow-up. |
| `data/raw/validation/HIV Data UNAIDS/EDC Europe Data Extract/ECDC_HIVAIDS_NL_20260514_150705.csv` | AIDS total and route-specific reported counts, 1990–2017 | Confirms the workbook values originate upstream. No linked timing between HIV diagnosis and AIDS diagnosis is provided in these aggregate rows. |

The previous availability assessment is at `hiv-prep-model/outputs/hta_results/aids_at_diagnosis_20260914_183434/AIDS_at_diagnosis_availability.md`. It remains useful, but absence of a perfect cross-tabulation does not prohibit explicitly uncertain estimation. Its earlier brief specifically excluded fitted/assumed conversions.

## Historical source conflict

| Year | Stored ECDC Atlas AIDS total | RIVM 2008 Table 6.17 |
|---|---:|---:|
| 1990 | 48 | 419 |
| 1995 | 302 | 533 |
| 2000 | 268 | 249 |
| 2005 | 383 | 337 |

In 1990 the MSM subgroup also differs: ECDC 34 versus RIVM 318. The discrepancy is therefore not explained simply by omission of non-MSM cases. Potential explanations include source population, survivor/registration coverage, historical backfill, or differing event definitions; none is established here. The ECDC raw CSV already contains the smaller counts, so the workbook transcription is not their origin.

The immediate provenance task is to establish whether early ECDC counts describe all historical diagnoses or a restricted reconstructed cohort. Also inspect early HIV counts for related coverage problems; an AIDS discrepancy does not prove that HIV counts are wrong. Do not automatically replace the ECDC series with RIVM or splice them.

## What partial identification can achieve

SHM Table 1.2 contains 1,301 stage-classifiable diagnoses during 2022–2024:

- Other men: 297 diagnoses, 183 late-stage.
- Women: 219 diagnoses, 127 late-stage.
- MSM: 742 diagnoses, 238 late-stage.
- Trans men and women: 43 diagnoses, 12 late-stage.

Table 1.3 splits the 560 late-stage diagnoses into 169 CD4 200–349 without AIDS, 183 CD4 below 200 without AIDS, and 208 clinical AIDS cases.

For these classifications, other men plus women have 516 diagnoses and 310 late-stage diagnoses. The other groups have 250 late-stage diagnoses. Without a route-specific clinical-AIDS split, anywhere from 0 to 208 of the clinical-AIDS cases could fall in the 516-person non-MSM group: a logical range of 0–40.3%. This wide bound demonstrates that these marginals alone do not identify the fraction.

An optional assumption would allocate the same fraction of late-stage diagnoses to AIDS across groups: 208/560. That gives approximately 115 AIDS presentations among other men plus women, or 22.3% of their 516 classifiable diagnoses. This is an illustrative exchangeability assumption, NOT a measured non-MSM fraction, a recommended base case, or an uncertainty interval. Differences in testing and severity could violate it. Missing-stage cases and the recency classification must be addressed before applying it to the full C-1 totals.

A defensible estimation specification should state: cohort eligibility, the clinical-AIDS event window, diagnosis versus ART-start time, missing-stage treatment, route mapping, source overlap, era choices, and uncertainty in transport across groups/years. Use as few eras as the evidence supports; do not fit an unconstrained fraction for every year or impose a monotone decline solely from a policy narrative.

## Model and cost implications

C-1 entry currently occurs after endogenous diagnosis and before ART, using Chronic/Diagnosed/off-ART. Later biological progression is possible. The diagnosis schedule does not determine the stage of these already diagnosed entrants.

A proposed presentation layer would preserve each observed diagnosis total D(g,t) and allocate D(g,t)*p(g,t) to AIDS at presentation, with the remainder without clinical AIDS. This is a conditional entry composition, not a reconstruction of infection dates or pre-diagnosis transmission. A genuine infection back-calculation would additionally require infection-to-diagnosis delays, migration history, and natural-history assumptions.

Keep separate outputs for first AIDS presentation at HIV diagnosis, first AIDS after previous HIV diagnosis, and biological progression. Align these with the source before forming a surveillance total. Endogenous people progressing and then being diagnosed in the same or later step must not be counted twice. The migrant and exogenous diagnosis pathways also require a cohort-overlap check.

The stored `configs/hta_costing_config.json` already maps Acute to Popping timely care, Chronic to late care, and AIDS to very-late care, including first-year and maintenance costs. These are proxies, not equivalences to Popping's CD4 categories. Consequently, all-Chronic entry may overcharge some timely presenters while undercharging severe presenters. A clinical-AIDS split alone cannot resolve the cost mapping. Review the active costing path and whether entry/presentation category needs to be retained separately from current disease stage. Avoid adding hospital costs already included in the care bundles.

## Compound history matching: proposed design

Represent a candidate as a joint tuple: epidemic parameters, presentation parameters, and any explicitly estimated observation parameters. Retain complete tuples and their dependence.

1. **Evidence constraints:** screen presentation parameters against compatible group, era, and aggregate evidence. Preserve ranges or alternative structural scenarios where identification is weak.
2. **Epidemic screen:** evaluate plausible joint tuples against vetted HIV diagnosis targets. Keep presentation parameters variable wherever they can affect these outputs.
3. **Joint refinement:** introduce correctly mapped AIDS/presentation constraints while retaining earlier constraints. Use additional seeds near decision boundaries and retain alternative regions.
4. **Confirmation and economics:** independently confirm surviving tuples, report held-out fit, and compare both total spending and incremental PrEP outcomes across surviving structural scenarios.

This is compatible with history matching. It is not safe to calibrate one block, freeze a single best value, and force another block to absorb the remaining residual. Nor can a retained region under the old model be assumed exhaustive after adding a new presentation mechanism. Previous vectors can seed part of a new design, alongside exploration outside that region.

Code inspection: `run_history_matching.py` supports multiple wave specifications and per-wave target-series lists. It scores the current list at each wave, so cumulative inclusion must be explicit; there is no automatic union of all earlier targets. Bounding-box/proximity proposals alone do not guarantee earlier constraints still hold. `--continue-from` checks identical base configuration, parameter space, target register, variance input, and compatible runtime. A revised presentation model therefore needs a new versioned run sequence. Nested evidence screening, joint presentation parameters, and adaptive seed promotion are proposed additions, not existing capabilities established by this review.

## Bootstrapping and stochastic variance

Resampling the existing 50 seed trajectories can diagnose uncertainty in means/variances and classification stability. Resample whole trajectories, preserving dependence among annual targets; preserve arm pairing for economics. It cannot generate missing presentation observations or repair source coverage. Bootstrapping surveillance data requires a justified sampling model and suitable underlying observations; one aggregate annual series is not an IID sample of patients. Bootstrapping NROY rows does not turn them into a posterior.

Andrianakis et al. (2017), equations 1–6, explicitly link reality to one stochastic simulator realization and retain its input-dependent per-run variance. Additional simulator seeds reduce uncertainty in estimating the mean; they do not remove real process variation. For direct simulation under that interpretation, process variance and Monte Carlo mean-estimation variance must be distinguished (schematically s(x) plus s(x)/K, subject to the actual observation model). Under a mean-matching interpretation, only s(x)/K may be appropriate, but that choice requires justification about what the observed target represents. Do not automatically adopt either formula or double-count process variation already included elsewhere.

The current runner's V/K policy and prior audit need this qualification. Local variance estimation remains useful, but shrinking the entire denominator with K does not by itself demonstrate scientific calibration failure. Source reconciliation and the stochastic observation contract precede another production wave.

Primary method reference: [Andrianakis et al. 2017, sections 3.2–3.3](https://eprints.whiterose.ac.uk/110612/1/VarEmu.pdf). Its expanding target set across waves supports the staged proposal; this repository currently evaluates simulations directly rather than using its Gaussian-process emulators.

## D76 extraction and source decision (2026-09-16 follow-up)

The user selected D76, `RIVM(2016)-STIandHIVinNL2015.pdf`, as the primary historical AIDS source. Its explicit Inspectorate/SHM provenance supports this choice; nationality alone is not the justification. The ECDC discrepancy remains unresolved and the original ECDC data are retained, not overwritten or pooled as independent evidence.

Extracted into `../data/raw/validation/DiseaseData.xlsx`, new tab **RIVM-D76 AIDS**:

- Rows 3–35: 33 annual records, 1983–2015. Year in E, source in F, annual AIDS diagnoses in G, cumulative AIDS in H, annual deaths among HIV patients in I, cumulative deaths in J.
- Rows 51–79: 29 transmission-group periods, starting with the pooled `≤1987` period and then annual 1988–2015. Counts and reported percentages occupy paired columns G:R. Row 80 preserves the published cumulative group totals.
- Rows 38–46: source filename, printed/PDF page locators, construct definitions and source caveats. Annual data are from printed pp.108–109; group data from Table 6.11, printed pp.110–111.
- All annual cumulative sums, all period-specific group totals, and all cumulative group counts reconcile. Cumulative totals are 9,583 AIDS diagnoses and 5,904 deaths among HIV patients. Original nine worksheet contents and styles were preserved, with existing worksheet XML and dependent parts unchanged.
- A recovery copy is `../data/raw/validation/DiseaseData.before_D76_20260916.xlsx.bak`.

Important qualifications: 2015 is incomplete; retain it as raw evidence but exclude it from complete-year primary matching pending later-vintage reconciliation. The methods place the Inspectorate/SHM change in 2000, while table footnotes place it in 1999. The group-table footnote ends SHM coverage at 2010 despite presenting data through 2015. Preserve these inconsistencies rather than silently resolving them. Deaths among HIV patients are not automatically AIDS-attributable deaths. Mother-to-child transmission is not an age-at-event classification and cannot simply be subtracted to produce an adult series.

### Next implementation boundary

1. **Historical target preparation:** use D76 as the candidate source through 2014, with source-era flags and no invented error bars. Do not silently splice ECDC 2016–2017 onto it. Reconcile later RIVM editions before extending coverage. This source preference does not by itself activate the target in the runner.
2. **Observation mapping:** define first observed AIDS diagnosis, distinguishing AIDS at initial HIV presentation and first AIDS diagnosis after earlier HIV diagnosis. Preserve biological Chronic-to-AIDS progression as a separate diagnostic output. Establish source eligibility and reporting timing before equating their sum with the RIVM table. Require conservation and no-double-counting tests across endogenous, exogenous and migrant pathways.
3. **Group mapping:** preserve all six published routes. Decide how MSM, other/unknown, injecting drug use, blood contacts and mother-to-child correspond to model eligibility. RIVM's MSM route is not automatically equivalent to the current SHM MSM-plus-trans calibration grouping. Use mutually exclusive compatible groups for matching; report their total as an accounting check rather than treating it as independent evidence.
4. **Presentation composition:** retain observed non-MSM HIV diagnosis totals. Estimate or bound their AIDS-at-presentation fraction using presentation evidence, not annual AIDS/HIV ratios. The D76 AIDS total constrains presentation plus subsequent AIDS, not either component separately. Do not fix a fraction or reconstruct infection dates from these marginal tables alone.
5. **New calibration sequence:** settle the process-variance versus Monte Carlo variance contract and observation discrepancy, then launch a newly versioned sequence with cumulative target constraints and broader exploration. Existing candidate vectors can be warm-start proposals, not an exhaustive plausible region under changed targets/mechanisms.

No simulator mechanism, active target register, production plan or run was changed in this extraction step. Appending a tab changes the workbook file hash even though old inputs are unchanged; old-run provenance must retain its original hash, and any whole-workbook hash guard must not be bypassed to claim an unchanged continuation.

## Passive observation diagnostics implemented (2026-09-16)

`aids_observation.py` now summarizes existing event draws without mutation or RNG
consumption. `run_v3.py` exports each of the following as total, `_msm`, and
`_non_msm`; `calib_extractor.py` sums them annually:

| Output stem | Event measured | Interpretation limit |
|---|---|---|
| `aids_progression_while_undiagnosed` | Chronic-to-AIDS transition before HIV diagnosis | Not an observed surveillance diagnosis |
| `aids_progression_while_diagnosed` | Chronic-to-AIDS transition with HIV diagnosis already recorded | Clinical recognition/reporting is not explicitly modelled |
| `hiv_diagnoses_in_aids_stage` | Endogenous HIV diagnosis while in the AIDS compartment | No clinical event window, reporting delay or source-eligibility adjustment |

The latter two could form an AIDS recognition proxy, conditional on additional
observation assumptions. No such sum has been activated as a calibration target.
Do not add all three: undiagnosed progression and later HIV diagnosis in AIDS can
refer to the same person. The current irreversible stage model avoids repeat
Chronic-to-AIDS transitions, but that would require reconsideration if recovery
or recurrent clinical events were introduced.

Existing `new_aids_cases` continues to equal the two progression counters summed.
It and its economic routing are unchanged. C-1 is still Chronic/Diagnosed at
entry. A future AIDS-entry split must add separately identified presentation
events; it cannot rely on the endogenous diagnosis draw to count already
diagnosed entrants. Already-diagnosed migrants arriving with AIDS likewise need
an eligibility/history policy rather than automatic assignment to domestic
incident AIDS. Imported undiagnosed AIDS can appear in the endogenous diagnosis
counter when subsequently diagnosed; this illustrates why these remain proxies.

Synthetic tests cover both diagnosis branches, on/off ART, group conservation,
pre-existing AIDS, same-step progression/diagnosis, repeat diagnosis prevention,
non-AIDS diagnosis, missing annual data and nonmutation. Two-step smoke tests
compare population and every legacy output with accounting enabled versus
disabled across all four event-order variants. No calibration wave is needed
for these checks. New fields are unavailable in old saved outputs and cannot be
recovered from biological progression totals alone.

Next substantive decision remains the joint presentation and observation
specification, including group/age compatibility with D76 and the stochastic
variance contract. These counters make that decision testable; they do not
resolve the missing evidence by assumption.

## Later RIVM vintage audit completed (2026-09-16)

Reproducible extraction: `tools/audit_rivm_aids_vintages.py`. Results and source
hashes: `outputs/calibration/rivm_aids_vintage_audit_20260916/REPORT.md` and
`source_extraction.json`. D76 remains unchanged in the evidence workbook.

The 2015 AIDS count evolves from 127 (D76, explicitly incomplete) to 210 (D77),
251 (D78), and 255 (D79). The 2014 count evolves from 186 to 191 to 218 to 223.
Thus older observations are revised too; excluding only the terminal year is
not a complete observation-uncertainty policy. D79 supplies annual group counts
through 2023, with 2023 explicitly incomplete. A recent extension through 2022
is possible, subject to construct/group checks and a declared vintage policy.

Two source issues were visually verified in the PDFs:

- D77 printed p.117, Table 6.9: pooled ≤2006 sums to 22,651, compared with D76
  cumulative AIDS through 2006 of 7,422. Do not use that pooled row or its grand
  total without reconciliation. The cause is not established.
- D79 p.129, Table 6.9: the 2016 row counts sum to 218; 100 MSM cases imply
  45.87%, whereas the published percentage is 46.9%. Both values are preserved
  and flagged. The audit does not decide which value the publisher intended.

Recommended source-policy decision before activating targets: either preserve
D76 through 2014 and use a separately labelled D79 recent extension, reporting
overlap-vintage sensitivity, or use the newest vetted edition for each available
year and retain D76 where no later annual evidence is available. The latter
changes the user's selected historical series in overlapping years and therefore
has not been applied automatically. Never treat editions as independent targets
or estimate a sampling variance directly from their differences.

### Minimal presentation-layer design for the next implementation

Keep the existing annual C-1 diagnosis totals fixed. Introduce an explicit
conditional clinical-AIDS-at-entry fraction only within a versioned evidence
scenario, allocating the existing converted persons between Chronic/Diagnosed
and AIDS/Diagnosed before ART. Preserve total population, observed diagnoses,
orientation totals and treatment eligibility timing. The fraction is not a
diagnosis-rate parameter and does not reconstruct historical infection events.

Record AIDS-at-entry separately from endogenous HIV diagnosis in AIDS. A proposed
recognition total would combine diagnosed-stage progression, endogenous HIV
diagnosis in AIDS, and eligible exogenous AIDS presentations. This is conditional
on a source-compatible observation model; imported already-diagnosed AIDS and
initial AIDS stocks are not automatically new domestic diagnoses. Keep the
existing biological progression output separate for economics until cost-event
timing is explicitly reviewed.

Do not select a production fraction yet. The joint search must retain the
presentation fraction alongside progression, diagnosis and treatment parameters,
not force the fraction to close every annual AIDS residual. Use few evidence-
supported eras and constrain them with independent presentation evidence. The
cost layer must separately address clinical AIDS versus CD4-based cost groups;
adding inpatient costs to bundles that already include them would double count.

Implementation gates are substantive: source/eligibility policy; presentation
evidence and scenario bounds; first-event/reporting assumptions; and the process
versus Monte Carlo variance contract. Passive diagnostics and the source audit
are complete, but these scientific choices are not encoded as invented defaults.

## Recommended order

First reconcile historical surveillance coverage and define the stochastic observation contract. Then specify the smallest evidence-supported presentation layer and its cost mapping. Implement staged joint screening only after those definitions are recorded. The evidence supports investigating this design; it does not yet justify a single numerical non-MSM AIDS-entry schedule.

## Approved source policy and run-readiness decision (2026-09-16)

The user approved **newest vetted RIVM edition per year**, retaining D76 where
later evidence is unavailable. This supersedes the earlier pending-policy wording
above, but does not approve any numerical presentation fraction or variance model.
Machine-readable record: `configs/rivm_aids_source_policy_20260916.json`.
The original D76 workbook tab and ECDC evidence remain unchanged.

`tools/build_rivm_aids_candidate.py` builds a separately versioned evidence
candidate from the audited extraction, retaining source filenames, PDF pages,
publication years, source hashes, reported fields, excluded rows and reasons.
Output: `outputs/calibration/rivm_aids_vintage_audit_20260916/candidate_newest_vetted_20260916.json`.
It contains 34 annual group-table records: D76 1988–2012, D78 2013,
D79 2014–2015 and 2017–2022. Earlier annual 1983–1987 totals remain in the
workbook; the pooled group-table period is not fabricated into annual records.

Conservative review holds are explicit, not claims that the observations are
false: D77 annual rows await separate review following the pooled-row anomaly;
2016 is held pending D79 count/percentage reconciliation, without silently
substituting an older edition. Incomplete terminal years are excluded.
Overlapping editions remain sensitivity evidence, not independent observations.
No revision-derived error bars or model-orientation mappings have been invented.

**Run readiness: blocked for a new AIDS-matching production wave.** Source
selection does not fix the following remaining scientific/implementation gaps:

1. The exported AIDS counters are event proxies, not an approved RIVM observation
   function. Eligibility, imported history and reporting assumptions remain open.
2. Non-MSM entry is still all Chronic; the conditional AIDS-at-entry layer and its
   evidence-supported bounds are not implemented. Re-fitting now could make
   progression or diagnosis compensate for that missing entry composition.
3. The current V/K denominator assumes mean matching; whether simulator
   variability represents real process variation remains to be specified.

Therefore no launch-ready plan or terminal command is issued for that wave, and
no production simulation was started. A diagnostics-only run could be separately
specified, but would not resolve these choices or produce a defensible new NROY
baseline by itself. Do not reuse the old baseline/focused plan as if it activates
this source policy: its ECDC targets and variance settings are unchanged.

Verification: 155 focused tests passed, covering candidate selection, passive
observations, C-1, target registration, wave logic and confirmation. This is not
a claim that the entire repository test suite passes.

## Blocker resolution for the MSM diagnostic wave (2026-09-16, later)

The user resolved the three run-readiness blockers for an **MSM-only diagnostic** wave. The resolution does **not** cover non-MSM AIDS matching.

1. **Variance.** The predictive contract s(x) + s(x)/K is approved, with s(x) estimated locally from each design point's replicates. The A-5 file is not used.
2. **Observation.** The AIDS compartment is clinical AIDS, because CASCADE 2000/2003 use the clinical 1993 CDC definition. No clinical-recognition fraction is needed. The MSM proxy is diagnosed-stage progression plus endogenous HIV diagnosis in AIDS. It remains an assumption (no reporting delay or completeness model).
3. **Presentation.** Deferred. Non-MSM AIDS is cross-validation only, and no AIDS-at-entry layer was built.

Plan and launch record: `HM_MSM_AIDS_DIAGNOSTIC_RUN_2026-09-16.md`.
