# Model limitations register

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Purpose:** one place where structural limitations of the model are recorded with their evidence,
so the thesis limitations section is assembled from verified findings rather than reconstructed
from memory. Every entry cites a source that was opened and read.

**Status values:** `established` (measured, evidence cited) · `under investigation` ·
`accepted` (a deliberate decision, with the reason recorded).

---

## L-01 — Exogenous non-MSM diagnoses all enter at the Chronic stage

**Status:** accepted (2026-09-14)

C-1 injects the observed SHM non-MSM diagnosis series directly into the model. Every injected
person is placed in **Chronic**; none enters at AIDS stage. Real diagnoses are not homogeneous in
stage, and the Netherlands has a documented late-presentation pattern that is markedly stronger in
non-MSM than in MSM.

**Why it was not corrected.** The quantity required is the proportion of new diagnoses with a
*concurrent AIDS-defining event* — clinical AIDS, matching the model's AIDS stage, which is
sourced to CASCADE (`chronic_to_aids`, median time to AIDS 9.8 years). A systematic sweep of the
literature corpus (110 of 111 PDFs, full text) found **no non-MSM-specific, year-specific series**:

- **SHM HIV Monitoring Report 2025, Table 1.3 p.33** carries the correct definition (p.33 note:
  *"AIDS = AIDS-defining event"*) and is the same publication as the C-1 diagnosis series, so it
  would be internally consistent. But it is **all-routes and pooled over 2022–2024**. Both required
  dimensions are absent.
- **Boender et al. 2018 (ATHENA cohort profile), Table 2** is the only route split found:
  heterosexual men 21.4%, heterosexual women 12.7%, MSM 9.5%. It has **no year dimension** (a
  1 January 2017 cross-section), carries **survivor bias in the wrong direction** because people
  presenting with AIDS in the 1990s died before that denominator was drawn, and the paper
  **publishes no definition** for the row.
- SHM's route-by-year series (Figs 1.10D, 1.11C/D) are **plotted curves with no data table**, and
  are CD4-composite rather than clinical AIDS.

Adopting a split would convert C-1 from **zero free parameters to one**, applied flat across a
thirty-year window over which the quantity demonstrably fell. Zero free parameters is C-1's
principal methodological virtue.

**Consequence, stated honestly.** The non-MSM stream contributes no AIDS-at-diagnosis, so
`new_aids_cases` — a tier-1 target mapped to `ecdc_aids_confirmed_cases` — receives nothing from
it, and the economics never accrue the higher costs of late presentation for that cohort
(`economics.py` differentiates by stage and takes `art_initiations_by_stage`).

**Note for the write-up.** This is a *Dutch data gap, not a definitional one*: the same quantity is
published as an annual series in England (UKHSA, via NICE TA1106). Evidence:
`outputs/hta_results/aids_at_diagnosis_20260914_183434/AIDS_at_diagnosis_availability.md`.

---

## L-02 — C-1 injects at diagnosis, not at infection

**Status:** established (2026-09-14)

Injected people enter the model at the point of *diagnosis*. Their entire pre-diagnosis infectious
period, including the high-infectivity acute phase, is absent. For a series spanning 1996–2024 this
is a substantial quantity of unmodelled non-MSM transmission pressure.

**Direction.** The net effect on non-MSM transmission is therefore almost certainly **negative**,
which reframes the roughly 1% onward-transmission "leak" from injected cases as a small visible
artefact sitting on top of a larger invisible understatement. C-1 is a **diagnosis-level proxy for
an infection-level process**.

---

## L-03 — Contact rate, orientation multiplier and route beta form an exact ridge

**Status:** established (2026-09-14)

Expected infections are exactly multiplicative: contacts are
`active_pop × orient_multiplier × base_contact_rate × dt` (`run_v3.py:1322-1327`), and transmission
is `Binomial(n_contacts, prevalence)` then `Binomial(·, beta × modifiers)`
(`transmission_corrected.py:5-6`), which by binomial thinning is exactly
`Binomial(n_contacts, prevalence × beta)`.

**Measured:** four ridge arms (k = 2, 0.5, 1.7, 1/1.7) scaling contacts by k and betas by 1/k
produced **byte-identical output**, 0 of 79 columns differing across 204 steps, with negative
controls confirming the edits propagate.

**Stochastic mode does not break the degeneracy.** The per-contact probability is about 9.2e-06, so
the draw is effectively Poisson and depends only on the product. Measured variance ratio between
ridge arms: **1.0000046**, requiring on the order of 1e11 seeds to detect.

**Consequence.** The four route betas are held fixed in `calib_space.json` (`excluded_parameters`,
"handled in PSA, not pre-PSA calibration"), so production calibration never exercises the ridge.
But calibrated contact rates **absorb any error in those fixed literature betas one for one** and
are interpretable only jointly with them. A PSA that varies the betas while holding calibrated
contact rates fixed moves along a direction the calibration never constrained. This should be
reported as a **structural sensitivity**, not as probabilistic uncertainty.

Framework and terminology: Guillaume et al. (2019), *Environmental Modelling & Software* 119:418–432,
Source I (structural). Their §2.1 p.420: *"If the equations are not identifiable, then it does not
matter under what conditions the data are collected, how much is collected, or how accurate they
are."* Their §2.3.6 p.426 recommends switching from optimisation-based to set-based estimation,
which is what history matching does.

Evidence: `outputs/hta_results/ridge_identifiability_20260914_134151/REPORT.md`.

---

## L-04 — Heterosexual and MSM transmission are structurally disjoint

**Status:** established (2026-09-14)

The mixing matrix is strictly block-diagonal. The construction loops write only
`Female ↔ Heterosexual_Male` and `MSM ↔ MSM` (`run_v3.py:1397-1426`). There are three orientation
categories (Female, Heterosexual_Male, MSM), **no bisexual category**, and no bridging term
anywhere in the codebase.

The two epidemics share only a population denominator, the activity-class structure, the diagnosis
machinery, the ART cascade and mortality. `epsilon_hetero` and `orient_female` have **no path to
any MSM output**, and vice versa.

**Consequence post-C-1.** The heterosexual market's level is now pinned to data while the MSM
market is generated endogenously. The MSM epidemic is the object of inference; the non-MSM epidemic
is a boundary condition. Defensible for a thesis on MSM-targeted PrEP, but it should be stated
rather than left implicit.

---

## L-05 — No parameter in the prior box can materially raise heterosexual transmission

**Status:** established (2026-09-14)

Measured against a faithful replication of the mixing construction (`run_v3.py:1348-1426`):

- Heterosexual pool volume scales as **√(orient_female)** (×4 gives exactly ×2.0000), because the
  heterosexual side reconciles male and female demand with a geometric mean. MSM volume is
  **linear** in `orient_msm` (×4 gives ×4.0000).
- `orient_female` sits at **0.905** against a box ceiling of **1.0**. Moving it to the ceiling buys
  **×1.0511**, a 5% increase. The lever is effectively exhausted.
- `epsilon_hetero` **conserves total pool volume exactly** across its whole box [0, 1] (ratio
  1.0000 at every value tested). It is a pure *shape* parameter and can only act through
  concentration.
- `contact_low/med/high` are shared across all orientations, so raising them raises MSM too.
- The physical lever, `beta_vaginal_mtf/ftm`, is **excluded from calibration by construction**.

**Consequence.** C-1's premise is structural rather than merely empirical: the inability to generate
the observed non-MSM epidemic is a property of the parameterisation, not of one fitted point.
Orientation multipliers also **cancel out of the mixing weights** (which are normalised), so they
affect volume only, never mixing shape.

---

## L-06 — Deterministic mode couples strata through a global allocator

**Status:** established (2026-09-14); partially corrected

`deterministic_allocate_events` takes one global largest-remainder pass: the integer target is a
round of the summed expectation across every cell, and residuals are ranked globally. Sub-unit
strata never win a residual but still inflate the target, so their events are transferred to larger
strata.

**A-29 corrected this for transmission only** (`transmission_corrected.py`, now one pass per
orientation). Before that fix, deterministic mode produced **exactly zero heterosexual infections at
all 204 steps**, against roughly 171 in stochastic mode, compounding to **+11.4% cumulative MSM
infections**.

**Twelve further call sites retain the defect**, all via `_draw_events_for` in `run_v3.py`:
progression (3), diagnosis (3), ART initiation and dropout (2), PrEP uptake and dropout (2), HIV
mortality (2). The **cross-stage** version of this was already documented at
`tools/run_a13_hiv_mortality_trace.py:534` (*"hundreds of events are systematically shifted between
Chronic and AIDS"*); the **cross-orientation** axis was not.

**Stochastic mode is unaffected** — that path draws cellwise binomials and never allocates globally.
This is an argument for reporting stochastic results, independent of B-8.

---

## L-07 — The CSG PrEP series measures a capped subsidy scheme, not PrEP uptake

**Status:** established (2026-09-14)

The RIVM PrEP pilot was regulation-capped (6,500 then 8,500) and **full by June 2022**, so the stock
series measures the funding ceiling rather than uptake. RIVM labels the one traceable value
(`csg_stock_end` 2022 = 8,558, p6) *"naar schatting"* and states it **overstates** active users
because lost-to-follow-up registration lags. No uncertainty is published for any participant count.

Coverage is partial: the project's own workbook records **4,802 non-CSG unique persons in 2022
against 9,763 CSG**, so CSG captures roughly 67% of national use, while the mapped `csg_base`
scenario sets non-CSG to zero in every year. Among prior PrEP users entering the pilot the GP
channel (24.5%) exceeded the CSG channel (15.5%).

This is a **censoring** defect, not a noise defect, and is therefore not curable by widening a
sigma. The series is a **verified lower bound**.

**Consequence.** The seven `prep_oral_*` parameters should be fixed outside the calibration wave with
uncertainty carried in PSA. Note that the four CSG series **did** enter the ABC-SMC that selected the
current base case, via `calib_scorer` family balancing (`run_abc_smc_calibration.py:611`), supplying
roughly **one sixth** of that score. Evidence:
`outputs/hta_results/rivm_prep_targets_20260914_134653/RIVM_PrEP_target_admissibility.md`.

---

## L-08 — All tier-1 variance budgets are 100% discrepancy

**Status:** established (recorded in `configs/hm_target_register.json`)

`V_obs` is underivable for five series. SHM carries a notification-delay handle only (exactly zero
for 23 of 29 years) and **no ascertainment handle at all**; ECDC publishes **no uncertainty**;
`UNAIDS-ART` has no low/high columns. Consequently **all 92 tier-1 budgets are 100% `V_disc`**.

`V_ensemble` is additionally **0.0** for every target, which the register itself records as biasing
*"towards OVER-exclusion"*. Under measurement — see
`TASK_BRIEF_A5_ENSEMBLE_VARIANCE_2026-09-14.md`.

---

## L-09 — Period-specific demography for 1975–1980 is not separately validated

**Status:** accepted (recorded in the base config)

`V13.22_20260604_nearfit_rank001_basecase.json → v13_14_diagnostic.limitation`, verbatim: the 1975
age-sex distribution uses HMD observed data, but *"Life tables, fertility rates, and TFR multipliers
are unchanged from production (2015-2019 average life table, modern ASFR) ... period-specific
demographic inputs for 1975-1980 are not separately validated."*

All five live V13.x base configs start in 1975 and carry `"status": "diagnostic-only"`. If the 1975
line is used for production results, that label should be removed deliberately and this limitation
stated, rather than left contradicting the use.

---

## L-10 — ΔQALY is a 3.3 ppm difference of two whole-population totals

**Status:** under investigation (2026-09-14)

At switch 100%: `reference_qaly` 473,483,980.2 against `intervention_qaly` 473,482,411.0, giving
ΔQALY **−1,569.3**. The signal is **3.3 parts per million** of the accumulator, because QALYs are
accumulated across the whole population while the intervention touches only PrEP users and averted
infections.

The arithmetic also does not obviously close: averting **81.9** infections at A-8's reported 1.911
QALYs per averted infection implies roughly **+157** QALYs, against a reported **−1,569**. A negative
component roughly ten times the infection effect dominates and is not yet explained.

**This must be resolved before results are reported.** Under investigation as A-30.
