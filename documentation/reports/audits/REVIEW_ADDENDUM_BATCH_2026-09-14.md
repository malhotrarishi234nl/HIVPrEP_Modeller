# Independent review — the 2026-09-13/14 batch (A-22, A-9, A-23, A-2, A-8, A-24) and the three A-8 spin-off findings (A-25/A-26/A-27)

> Archived under `documentation/reports/audits/`; original filename retained.

**Reviewer:** Claude (Cowork), 2026-09-14. **Review only. No model run. No delivered file modified.**
Every figure below was recomputed from the reports' own retained artefacts or read from production source.

**Verdict: all six upheld. Two of my own priors were falsified and the reports were right to say so.
The batch's most important result is A-24's, and it was not one of the four questions asked.**

---

## 1. A-24 — verified, and it is the headline

Recomputed from `run_*/per_seed_decomposition.csv` (n=15, base case):

| configuration | ΔQALY SD | Var vs production | Δbirths SD | LY via debuts SD | LY via bg deaths SD |
|---|---:|---:|---:|---:|---:|
| production | 1,200.1 | 100.0% | 198.7 | 880.8 | 677.7 |
| multinomial coupling only | 812.6 | 45.8% | 7.9 (0.2%) | 34.0 (0.1%) | 783.1 (133.5%) |
| grouped background mortality only | 1,215.5 | 102.6% | 217.2 (119.5%) | 1,024.3 (135.2%) | 186.8 (7.6%) |
| **both** | **360.2** | **9.0%** | 9.8 (0.2%) | 32.7 (0.1%) | 188.7 (7.8%) |

**Variance ratio 0.0901, SD ratio 0.3001, equivalent seed multiplier 11.1×.** A-24's headline reproduces exactly.

**The sampler probe is the decisive evidence and it holds.** At a one-person weight change — the smallest
perturbation available — production `Generator.multinomial` gives mean L1 **0.351** and **max 188**,
against an expected shift of **0.0053**. The coupled inverse-CDF gives **0.016** and max 2. The production
sampler moves the allocation **66× more than the perturbation warrants on average**, and 35,000× in the
tail. That is a direct demonstration of non-monotonicity, not an inference from a downstream symptom.

**Three things the report does not put prominently enough, and the first is load-bearing.**

1. **The two couplings are not independently useful and each makes the other channel worse.** Multinomial
   alone leaves ΔQALY variance at 45.8% and *raises* background-death variance to 133.5%. Grouped
   background mortality alone gives **no ΔQALY improvement at all (102.6%)** and raises debut variance to
   135.2%. **The 9.0% exists only for the pair.** A-28 must ship both or neither; staging them across
   releases would produce a release that measures as no better than today.
2. **The mean moves 956.2 → 1,150.7 (+20%).** At production SD that is **0.63 SE** — unresolved, and
   exactly what an unbiased variance reduction looks like. But it must be confirmed at higher n before
   anyone treats 1,150.7 as the new central estimate. **Report it; do not adopt it.**
3. **`infections_averted` variance rises in every counterfactual** (134.5% / 154.3% / 119.8%). At n=15
   that is within chi-square sampling noise and I am **not** calling it a finding — but the fix reducing
   ΔQALY variance while the variance of its own driver rises is the kind of thing that should be watched
   rather than left unremarked.

**And the scope is better than it looks.** `tests/test_a13_crn_key_compatibility.py` pins the **key
derivation** — pickle payload, BLAKE2b digest, little-endian words, Philox stream at the key boundary —
**not the multinomial algorithm**. A coupling built on the same keyed uniforms leaves every pin intact.
The only multinomial test (`test_counter_based_rng.py:87`) requires exact total and reproducibility,
which an inverse-CDF coupling satisfies. **The fix is contained, and a failing golden pin would mean the
implementation is wrong rather than that the pins need rebaselining.** A-28 states this.

---

## 2. A-2 — upheld, and it rehabilitates the estimator

- Reconciliation control **227/227**, summing to production total within **4.6e-7** against a 1e-6
  tolerance not adjusted after the fact. The residual is traced to float accumulation in the production
  running total, not to a decomposition term. This is the correct way to run a decomposition.
- **The `r = +0.12` was a scaling artefact, not a structural absence.** Infection-coupled mean **+1,592.7**
  (SD 276, r with infections averted **+0.83**, positive in 15/15); non-HIV life-years mean −636.5 with
  **SD 1,203** carrying **97.6%** of the variance. Partial r controlling for person-years **+0.988**.
  **The brief's own hypothesis was refuted and the report says so plainly.**
- **A-16's 183 QALYs per averted infection is dead**, and correctly diagnosed: a ratio of means at
  switch-10% where the denominator is 0.667 with SE 5.50 and 6/15 seeds negative. That figure should be
  struck wherever it appears.
- Discounting excluded as a cause (undiscounted split 1.68% / 98.32%).
- **A-3 closes on this**, on a real criterion rather than the 12/15 sign count the A-4/A-5a review already
  established carries no information.

## 3. A-8 — upheld, with its own caveat respected

- **1.911** per averted infection against an independent cohort derivation of **1.832** — difference 0.079,
  **1.7 A-2 SE**, inside a pre-specified band. No accounting defect.
- Against literature harmonised to the same basis: model 1.600 vs evidence 2.29–2.93, worst case **2.76×**,
  under the 3× escalation threshold but close to it.
- **The explanation is the horizon, not the parameters**: averted infections spread over 2025–2064 give
  ~20 years each; the same model gives **4.01** lifetime and **6.28** for infection at 25–29.
- **The comparator caveat is the right call and should survive into the manuscript.** No directly
  comparable Dutch or Western-European published figure was found; the only direct comparator is US
  (Farnham 2013, abstract only). Everything else is a derived estimate using the model's own structure.
  That is close to "no usable comparator" and should be disclosed as such, not smoothed over.

## 4. A-23 — upheld, and it refuted my premise

- **The brief's cross-stage premise was wrong and A-23 says so.** Each HIV-mortality call covers one
  (stage, ART) stratum, so no cross-stage relocation is possible there; chronic-on-ART's 92.171% is
  call-level rounding to zero from 1996–2011. **I proposed that premise after A-21 and it was incorrect.**
- The conservation identity closes **exactly, 0 error in 1,224 calls**.
- **The +122% is transmission feedback, tested not argued:** hold transmission at the deterministic force
  of infection and the effect collapses, ratio **0.011**. The residual +21.27% is deterministic mode's own
  offset (A-20: +20.73%, r = 0.981).
- Deaths move **up** (17,285 vs 9,942), rate per PLHIV-year essentially unchanged. Not a failure-to-die effect.
- **A-13 and A-20 reconciled** by naming the quantity each measured — a between-arm difference over
  2025–2065 versus a level over 1975–2025.
- The trigger — 588 HIV deaths moved from non-MSM onto MSM cells — is labelled **supported, not tested**.
  Correct labelling; it should stay labelled that way.

## 5. A-22 — upheld, and it falsified the natural next step

- **NO_MATERIAL_LEVEL.** Best is level 1 (HIV-status split) at **0.10840** against **0.09419**. Closer,
  and not enough.
- It does halve the defect — HIV+ shortfall 22.1% → 11.3%, growth-phase starvation 97.3% → 85.1% — which
  makes the failure more informative, not less: **the mechanism is real and regrouping still cannot repair
  the calibration.**
- **ENG-01 fails at every level including production level 0 (9 starved classes)**, and finer grouping
  makes it worse (11, 11, 12). That is the ENG-01/A-13 dial behaving exactly as D-14 described, now
  measured at a third call site.
- Codex **superseded its own first run** on realising a one-axis audit could hide starvation inside joint
  classes. That was the right call and the superseded directory is correctly marked.
- **Taken with A-19, the deterministic repair space is now closed from both sides:** A-19 closed the rule
  family by proof, A-22 closes the granularity family by measurement.

## 6. A-9 — upheld, and it falsified my A-21 prior

- **My prior was that background mortality would repair the grid. It did not.** Its grid is monotone but
  the smallest increment is **0.72 SE**, below the pre-specified 2 SE bar. Not a repair, and A-9 says so
  without softening it.
- **HIV mortality meets the infections criterion** (25.2 / 55.4 / 107.3 / 145.1 / 220.7, weakest increment
  2.10 SE) **and breaks the mortality coherence check** — HIV-deaths-averted falls from 10% to 25%
  substitution — while overshooting the stochastic reference at mean |z| 3.63. **Recommend neither** was
  the pre-specified response and it was followed.
- Both reference gates passed exactly: deterministic **+38/+7/+34/+113/+96** at difference 0.0;
  all-stochastic **825/825** exact against A-15.
- **A-9 stays open, and it may not close.** The honest options remain: report the scenario grid from
  stochastic runs only, or drop the scenario analysis.

---

## 7. What the batch changes

| | before | after |
|---|---|---|
| the estimator | "ΔQALY may be structurally broken" | **sound; the signal was buried under a fixable noise source** |
| QALY per averted infection | A-16's incoherent 183 | **1.911, validated against an independent derivation** |
| deterministic repair | open question | **closed both ways — no rule (A-19), no grouping (A-22). Disclosure item.** |
| A-20's +122% anomaly | unexplained | **explained: transmission feedback, tested** |
| the main lever | unknown | **one sampler, pre-measured at 11.1× effective seeds** |
| A-9 | open | **still open, localised but not repaired** |

**Two of my own priors were falsified in this batch** — the A-21 background-mortality prior for A-9, and
the cross-stage premise in the A-23 brief. Both reports reported the falsification plainly rather than
finding a reading that preserved the prior. That is the behaviour that makes the rest of the evidence
trustworthy.

---

## 8. Items for the record

| # | item | status |
|---|---|---|
| 1 | A-24's 9.0% / 11.1× headline | **reproduced exactly** |
| 2 | Sampler probe: 0.351 mean L1 vs 0.0053 expected, max 188 | **verified** |
| 3 | The two couplings are not independently useful; each alone worsens the other channel | **new emphasis**; load-bearing for A-28 |
| 4 | Mean shift 956.2 → 1,150.7 = 0.63 SE, unresolved | **new**; report, do not adopt |
| 5 | `infections_averted` variance rises 120–154% in every counterfactual | **new**; unresolved at n=15, watch |
| 6 | A-13 golden pins cover the key path, not the multinomial algorithm | **verified from source**; makes A-28 contained |
| 7 | A-16's 183 must be struck wherever it appears | **action** |
| 8 | A-8's "no usable Dutch/European comparator" caveat | **must survive into the manuscript** |
| 9 | Deterministic repair space closed from both sides (A-19 proof + A-22 measurement) | **conclusion** |
| 10 | A-9 open; may be unclosable | **accepted** |

**Nothing in this addendum changes a production value, parameter, threshold or verdict.**


---

## 9. The three A-8 spin-off findings — read separately, they are a different class

A-8 surfaced three items out of scope of its own question. **They are not variance or arithmetic
findings; two of them change what the model computes, and one of them reaches the submitted thesis.**

**A-25 — the PrEP eligibility age cap is silently ignored. This is the most consequential item in the
whole batch after A-24, and possibly ahead of it.**

- `eligible_age_range.max = "64-69"`. **No age bin has that name** — the bin is `"65-69"`.
- `run_v3.py` resolves it with `age_to_idx.get(name, n_age_groups - 1)`, so an unknown name **silently
  falls back to the last bin**. Eligibility spans **15 to 110+** instead of 15–69, confirmed
  programmatically (`oral_prep_eligible_max_bin_resolved: "110+"`).
- **It is two defects, and the second is the worse one.** The typo is trivial. The **silent fallback** is
  a design fault that will do this again somewhere else — and it contradicts the project's own ENG-F04
  rule (`KeyError` on a name mismatch is correct; a silent wrong index is not). **Both must be fixed.**
- **It is in every config in `configs/`**, so **it predates and affects the submitted thesis.** That makes
  it a disclosure question of the same class as A-11, not merely a code fix.
- **Fixing it invalidates every PrEP-scenario run to date** (A-2, A-4, A-9, A-15, A-16). A measured
  before/after at fixed seeds is required before any decision.

**A-26 — infection risk has no age dimension.** Contacts are split within orientation × activity in
proportion to susceptible count; age plays no role. **This is a structural modelling choice, not a bug**,
and the report labels it correctly. Combined with A-25 it means averted infections are **old-skewed**
relative to Dutch surveillance, and older infections carry fewer remaining life-years.

**A-27 — HIV life-years lost fall with age; the evidence says they rise.** Model HIV mortality is
**additive and age-independent**, so a constant excess hazard accumulates over more remaining years at
young ages. At 25 the model loses 4.6 undiscounted life-years against van Sighem's 0.4; at 55, 1.0
against 1.4. The report is right that **the direction of the gradient is the finding, not the level** —
the van Sighem cohort is ART-naive, asymptomatic at diagnosis, and heavily extrapolated.

**The three interact, and A-8 says so — this is the most important inference in the batch and it is
correctly labelled unverified.** The model gives young infections **too large** a loss (A-27) while
averting **too few** young infections (A-25, A-26). **A-8's headline 1.911 may be approximately right in
level partly because these errors cancel.** Two consequences follow:

1. **Do not fix one of the three in isolation.** Repairing A-25 alone would shift the age distribution of
   averted infections without correcting the age gradient of loss per infection, and the headline
   per-infection value could move materially in either direction.
2. **A-8's validation is weaker than its agreement statistic suggests.** The 1.7-SE agreement with the
   cohort derivation is real, but both sides of that comparison share the same age-independent mortality
   structure. **It is an internal-consistency check, not an external validation**, and A-8's own
   comparator caveat (no usable Dutch or Western-European published figure) compounds this.

**Sequencing recommendation.** A-28 (the CRN coupling) is independent of all three and should go first —
it changes no parameter and no age structure, only the noise. **A-25/A-26/A-27 should then be scoped as a
single age-structure workstream with one combined before/after**, not as three separate fixes.
