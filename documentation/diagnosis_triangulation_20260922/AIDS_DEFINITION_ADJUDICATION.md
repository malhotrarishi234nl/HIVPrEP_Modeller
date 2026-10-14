# AIDS definition adjudication

Date: 2026-09-22. Brief: `TASK_BRIEF_DIAGNOSIS_TRIANGULATION_ORCHESTRATION_2026-09-22.md`, section "Critical definition gate".

**Result.** The simulator's AIDS compartment is **clinical AIDS**: people enter it only through Chronic-to-AIDS progression, and that rate is sourced to CASCADE, which counts an AIDS-defining illness and nothing else.

Several downstream parameters use CD4 <200 constructs instead:

- mortality;
- the off-ART utility;
- the Popping "very late" cost tier.

Some report labels also call the compartment "CD4 <200".

The compartment does **not** represent the requested composite (CD4 <200 **or** AIDS-defining illness). This experiment therefore treats "diagnosis while in the model AIDS state" as a **narrower proxy** for advanced presentation. CD4 <200 without an AIDS-defining illness is **structurally unobservable** in this model.

Nothing in this task redefines the compartment or its progression rate.

This decision was traced through code and configuration, not through labels. Every factual claim below is also checked by `adjudication_checks()` in `run_diagnosis_triangulation_orchestration.py`. `validate-plan` refuses to freeze the simulation plan unless every check passes (`adjudication_checks.json`).

## 1. Operational simulator definition

- **Stage axis.** `Susceptible, Acute, Chronic, AIDS`.
- **The only way into AIDS is Chronic→AIDS progression** (`run_v3.py::_step_disease_progression`):
  - off ART: `disease.progression.chronic_to_aids`, calibrated per backbone (1326: 0.0574/yr; 1788: 0.0558/yr);
  - on ART: the two-era effective `chronic_to_aids_on_art` schedule (decision 2026-09-16).
- **No other pathway enters AIDS:**
  - exogenous non-MSM diagnoses (C-1) enter Chronic/Diagnosed (`MODEL_LIMITATIONS.md` L-01);
  - pre-arrival-diagnosed HIV-positive migrants enter Chronic (`demography.migration.migrant_hiv.hiv_stage_distribution = {"Chronic": 1.0}`).
- **The state is irreversible.** There is no recovery from AIDS and no CD4 or viral-load state.
- **Diagnosis while in AIDS.** This is an ordinary endogenous diagnosis event whose person is in the AIDS stage (`hiv_diagnoses_in_aids_stage*`).
  - Under M1, the symptom hazard adds to the routine hazard only for this stage, from 1985 onward.
- **Observation counters** (`aids_observation.py`) are passive. They count:
  - progression while undiagnosed;
  - progression while diagnosed;
  - diagnosis in each stage.
- **`aids_first_diagnosis_proxy_msm`** = progression while diagnosed + diagnosis while in AIDS.
  - Progression while undiagnosed is excluded to prevent double counting.

## 2. Source definition for Chronic-to-AIDS progression

- **Configuration source string.** "CASCADE Collaboration (2000) … ATHENA cohort off-ART progression data", with the notes "Median time to AIDS 9.8 yrs … CASCADE Table 2".
- **CASCADE 2000** (`literature/Progression/CASCADE(2000)_HIV1SeroconversionAIDSDeathIncubation.pdf`, PDF p.2, methods). Only the clinical part of the 1993 CDC revision was used: people without an AIDS-defining disease but with CD4 <200 cells/µL were not classified as having AIDS.
- **CASCADE 2003** (`…CASCADE(2003)_SurvivalDeterminantsPostHIVSeroconversion.pdf`, PDF p.2, methods). AIDS is defined by the clinical criteria of the 1993 CDC case definition.
- **The on-ART effective rate** is anchored to SHM first AIDS-defining events after entry into care, CDC category C (`ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`; SHM 2025 PDF p.180, Figure 6.1B). SHM explicitly excludes CD4 <200 without an AIDS-defining condition. This is also clinical AIDS.

**Conclusion.** The operative stage is clinical AIDS: an AIDS-defining illness, regardless of CD4. This agrees with:

- `HM_MSM_AIDS_DIAGNOSTIC_RUN_2026-09-16.md` (decision 2);
- `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md` (section 2);
- `MODEL_LIMITATIONS.md` L-01;
- the header comment in `aids_observation.py`.

## 3. Definitions used for mortality and economic parameters

| Parameter | Configured source | Construct | Relation to the compartment |
|---|---|---|---|
| `disease.mortality.aids` (1326: 0.2516/yr; 1788: 0.2336/yr) | "EU CASCADE + Glaubius"; distribution notes: "Glaubius 25-34, 3 band weighted chaining derivation" | Glaubius (2021) Spectrum stages are CD4 bands: >500, 350–500, 250–349, 200–249, 100–199, 50–99, <50 (PDF p.2). The three bands below 200 are CD4 <200. | **CD4 <200 mortality applied to a clinical-AIDS population.** The direction of bias is not identified here. |
| `disease.mortality.chronic` | "Glaubius 250-349 Band for age 25-34" | CD4 250–349 | Chronic is a CD4-free stage. |
| `utilities.aids_off_art` = 0.70 | Tengs & Lin 2002 pooled AIDS TTO; Nichols 2016 Table S1 "AIDS CD4 <200 = 0.70" | Mixed: AIDS (Tengs), CD4 <200 (Nichols) | Approximate |
| `disease_costs.hiv_non_art_care_tier_very_late_*` (AIDS stage, on ART) | Popping et al. 2023, Table 3 | "Very late presentation" = CD4 <200 at entry into care (PDF p.3). An AIDS-defining illness with CD4 >350 is classed as *late*, not very late. | **CD4-based cost tier applied to clinical-AIDS ART initiators** |
| `disease_costs.aids_hospitalization` (AIDS, off ART) | iMTA bottom-up opportunistic-infection episodes | Clinical AIDS | Consistent |
| `utility_decrements.late_presenter_art_initiation` | Popping 2021/2023 | Late/very-late presenters | Approximate |

**Report labels.** `hta_report.py` (glossary; `STATE_LABELS`) labels the compartment "AIDS (CD4 <200)" and describes AIDS as "CD4 < 200 cells/µL". These labels are inconsistent with the operative definition. They are recorded here and are **not** edited in this task, because the HTA path is out of scope. A later task should relabel them "clinical AIDS (AIDS-defining illness)".

## 4. Definition of every surveillance comparator

| Comparator | Definition | Source |
|---|---|---|
| RIVM MSM AIDS diagnoses (primary 2000–2022; historical 1990/1995) | AIDS patients by year of AIDS diagnosis, transmission route MSM. The AIDS registry (Health Inspectorate to 1999/2000) and SHM use CDC category C, i.e. clinical AIDS. | D76 (2016) Table 6.11 and D79 Table 6.9, via `candidate_newest_vetted_20260916.json` |
| RIVM total AIDS; deaths among HIV patients | Clinical AIDS, all routes. Deaths are **all-cause** deaths among HIV patients (CBS before 2002, SHM from 2002), not AIDS-attributable deaths. | Workbook `RIVM-D76 AIDS` |
| ECDC AIDS confirmed cases | Reported AIDS cases. Conflicts with RIVM in the early years (1990: 48 vs 419). | Workbook `EDC- AIDS` (retained raw, not used) |
| SHM advanced-stage HIV at diagnosis | CD4 <200 **or** an AIDS-defining event at diagnosis. People with evidence of infection in the previous 12 months are classified "recent", and recency takes precedence. The denominator excludes "stage unavailable". MSM 2024: 56 (21%); trans 2024: 2. | SHM 2025, PDF p.26 (definitions), PDF p.29 (Figure 1.11 caption) |
| SHM late-stage HIV at diagnosis | CD4 <350 or an AIDS-defining event. The same recency rule applies. MSM 2022–2024: 238/742. | SHM 2025, PDF p.31, Table 1.2 |
| SHM late-stage composition 2022–2024 (all groups) | 169 CD4 200–349 without AIDS; 183 CD4 <200 without AIDS; 208 AIDS-defining event | SHM 2025, PDF p.33, Table 1.3 |
| Op de Coul et al. advanced HIV disease | CD4 <200 (first CD4 within 3 months of diagnosis) or a CDC-C event, at diagnosis or entry into care. There is no recent-infection precedence. MSM 1996–2014 (pooled): 3,535/12,314 = 28.7%. Of all 7,331 advanced presenters, 26% had AIDS. | `deCoul(2015-…).pdf`, PDF p.2 (definitions), p.3 (results), p.4 (Table 1) |
| SHM first AIDS-defining events in care | CDC-C events after entry into care, per 1,000 PYFU | SHM 2025, PDF p.180 |

## 5. Exact, approximate, bounded or unavailable comparisons

- **Exact:** none. No surveillance series counts the model's "HIV diagnosis while in the model AIDS state" with the same event rule.
- **Approximate:**
  - `aids_first_diagnosis_proxy_msm` vs RIVM MSM AIDS diagnoses. This is the established proxy with its declared 10%/20% discrepancy. Its limitations are unchanged: no reporting delay, no notification completeness, and no prior reporting abroad.
- **One-sided upper bounds** on the representable component (model clinical AIDS at diagnosis ⊂ observed advanced presentation):
  - `hiv_diagnoses_in_aids_stage_msm` (2024) ≤ 58, i.e. 56 MSM + 2 trans, because the model MSM group includes trans people;
  - model fraction in 2024 ≤ 21%;
  - model MSM fraction pooled 1996–2014 ≤ 28.7% (Op de Coul).

  These bounds hold provided the model AIDS compartment is clinical AIDS. SHM's recency precedence removes people infected within the last 12 months from "advanced". In the model, such people can reach AIDS only by a rare path: at most about 4–6% of infections reach AIDS within a year of infection at the configured off-ART rate (0.056–0.057 per year), and fewer are then diagnosed in that window. The bound is therefore treated as holding up to this negligible leakage, which is stated rather than corrected.
- **Lower bound:** not identified.
  - The pooled Table 1.3 marginals allow anywhere from 0 to 208 clinical-AIDS presentations among MSM in 2022–2024.
  - The illustrative "exchangeable composition" figures are shares of advanced presentation that are AIDS: 208/391 = 53% (pooled, 2022–2024) and 26% (Op de Coul, 1996–2014). They are context, **not** a conversion coefficient. No free conversion is fitted.
- **Unavailable:**
  - CD4 <200 without an AIDS-defining illness. This is structurally unobservable, because the model has no CD4 state.
  - CD4 <350 late presentation. This is context only; the brief fixes CD4 200–349 as Chronic.

## 6. Consequence for this experiment

1. **Labelling.** The requested thesis-facing measure (CD4 <200 or AIDS) is reported as a **partial-identification comparison** with three parts:
   - the directly representable component (model diagnoses in clinical AIDS);
   - the one-sided bounds above;
   - the unrepresented CD4-only component, reported as observed advanced minus the model component. It is shown descriptively and never fitted.
2. **Separation.** The seven constructs of the brief are kept separate in `observation_mapping.csv` and in every output. Biological progression, diagnosis in AIDS, first AIDS after earlier diagnosis, the proxy, advanced presentation, calendar-year AIDS diagnoses and CD4 <350 late presentation are never collapsed.
3. **Decision role.** The advanced-presentation evidence can only **reject** a tuple, through a one-sided bound violation (clause c6). It cannot confirm one. A tuple can therefore pass c6 vacuously, and the report states this.
4. **Mortality and cost seam.** The mismatch between clinical-AIDS entry and CD4 <200 mortality and costs is a documented limitation for the HTA. It changes neither M0 nor M1 in this task: M0 and M1 share every non-diagnosis parameter.
5. **Why "unresolvable" is a possible outcome.** The discriminating evidence between "earlier/later diagnosis" and "larger hidden epidemic" is the time-since-infection or CD4-at-diagnosis distribution. The model does not represent it, and the SHM undiagnosed estimate is itself back-calculated from CD4 at diagnosis. The predeclared rule R5 (IMPLEMENTATION_NOTE.md) assigns "Unresolvable with current disease states" when:
   - MSM incidence agrees with the Tier-2 estimate;
   - the undiagnosed reservoir does not.

## M2 (described only; not implemented or simulated)

- **Structure.** A two-class testing mixture among MSM:
  - a regularly tested fraction φ with the current routine hazard;
  - a persistently under-tested fraction 1−φ with a reduced routine hazard κ·λ_routine, where κ<1;
  - both classes share the symptom-driven AIDS hazard.
- **Evidence.**
  - Bilsen et al. (2013–2015): 62% of late presenters had ever tested, vs 89% of early presenters.
  - Berdet et al.: qualitative missed opportunities.
  - Jordans et al.: 25% of GP indicator conditions tested.
  - Koole et al.: PrEP-access barriers.

  These support heterogeneity. **None bounds φ or κ for Dutch MSM.** Bilsen is mixed-population and survey-based.
- **Condition for a later task.** M2 may proceed only when both hold:
  1. the literature supplies a defensible bound on the under-tested fraction;
  2. M1 fails for a diagnosed reason that M2 addresses, e.g. an undiagnosed-reservoir or time-to-diagnosis mismatch (rule R5).

  It is not a rescue coefficient for this run.
