# Task brief — A-2: what is the ΔQALY actually measuring?

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-13 · **Owner:** Rishi Malhotra
**Type:** diagnostic. **Implements nothing in production.** Ends in a characterisation and a recommendation.
**Batching:** fully independent of A-22, A-9 and A-23 — different code path, different outputs. Run in parallel.

---

## 1. The observation

Post-CRN, across 15 CRN-paired seeds:

| correlation with ΔQALY | r |
|---|---:|
| Δ discounted person-years | **+0.998** |
| infections averted | **+0.12** |

**Your health gain tracks how long people live almost perfectly, and how many infections you prevented
almost not at all.**

For a PrEP model that ordering is backwards. Preventing infections is supposed to be the *mechanism* by
which people live longer and better. A ΔQALY nearly uncorrelated with infections averted means the QALY
signal is being produced by something other than the intervention's stated effect — or that the link
between the two is being swamped.

**Related and unresolved (A-3).** ΔQALY was positive in only **12 of 15** seeds while infections averted
was positive in **all 15**. The A-4/A-5a review established that 12/15 is exactly what the reported
mean and SD predict (expected 11.81) and therefore **carries no information** — the sign count is not
evidence of anything and must not be used as a closure criterion. A-3 stays open on a restated
criterion, and **A-2 is the task that can actually close it.**

**This is the remaining critical path for the estimator.** The A-4/A-5a review flagged it on 10
September as more important than A-3 and it has not been picked up since.

---

## 2. What this is not

- **Not a Monte Carlo noise problem.** A-4 cut the ΔQALY SD by 97.14% (41,917.9 → 1,200.1) with common
  random numbers. The correlation structure is measured *after* that fix.
- **Not the allocation defect.** A-21's mechanism is a level bias in deterministic mode; the base case
  is the CRN-paired stochastic mean, which has no allocation step for these events.
- **Not a calibration problem.** This is about what the estimator computes at a fixed parameter point.

---

## 3. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A, and the A-2/A-3 entries.
2. `outputs/hta_results/a4_common_random_numbers_20260910_170048/` — the report **and** `ADDENDUM_2026-09-10_review_A4_A5a.md`, which is where the correlation finding is recorded.
3. `outputs/hta_results/a16_stochastic_headline_20260911_030744/` — the current headline economics.
4. `economics.py` — the QALY and cost accumulation path. Read it before running anything.
5. `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/` — A-13 found deterministic mode misallocates deaths across stages, which is a candidate mechanical cause. **A-23 is testing that independently; do not duplicate it.**

---

## 4. Step 1 — decompose the ΔQALY. No new runs if the retained outputs suffice.

Using the existing A-4 / A-16 CRN-paired runs (seeds 42–56), decompose the incremental QALY into
additive components and report each with its across-seed SD and its correlation with both
`infections_averted` and `delta_person_years`:

| component | question it answers |
|---|---|
| life-years gained by **HIV-negative** people | how much of the gain is people never infected |
| life-years gained by **HIV-positive** people | how much is people living longer *with* HIV |
| quality-weight differences **within** HIV-positive states (stage, ART, diagnosed) | how much is utility reweighting rather than survival |
| **PrEP-state disutility** (including lenacapavir AE) | how much the intervention costs in utility |
| any component that is **not** attributable to an HIV state | **this is the one to watch** |

**The diagnostic question.** If the dominant component is life-years among people whose HIV status never
changed between arms, the ΔQALY is being driven by **demographic churn** — differences in who is alive
for reasons unrelated to the intervention — and the estimator is measuring noise with a small signal on
top. **State which component dominates, with numbers.**

**Pre-specify before looking:** the components must be **exhaustive and additive to the total ΔQALY**
within 1e-6. If they do not sum, the decomposition is wrong and must be fixed before anything is read.
That reconciliation is the control.

---

## 5. Step 2 — is the infection channel present at all?

For each of the 15 CRN-paired seeds, compute the **QALY gain per infection averted** and report the
distribution.

- A coherent model gives a roughly stable value with a plausible magnitude. Published Dutch/European
  estimates of lifetime QALY loss per HIV infection are of order **5–15 discounted QALYs**; cite
  whatever you use, do not assume this range.
- **A-16 already produced an incoherent figure here — 183 QALYs per averted infection.** That number is
  the reason this task exists. Establish whether it is an artefact of a small denominator, of the
  decomposition in Step 1, or real.
- Report the distribution, not just the mean: if the denominator is small in some seeds the ratio is
  unstable by construction, and **that instability is itself the finding.**

**Then the key test.** Regress ΔQALY on `infections_averted` **and** `delta_person_years` jointly across
the 15 seeds. Report both coefficients with SEs, and the partial correlation of ΔQALY with infections
averted **controlling for** person-years. **If that partial correlation is near zero, the infection
channel is absent from the estimator and the r = +0.12 is not a scaling problem but a structural one.**

n=15 is small; **state the resolution** (an SE on a correlation at n=15 is roughly 0.27, so only
|r| ≳ 0.55 is distinguishable from zero). Fifth occurrence of the criterion-resolution lesson. **If the
test cannot resolve the question at n=15, say so and report what n would be needed** rather than
reporting an underpowered result as a finding.

---

## 6. Step 3 — only if Steps 1–2 show the estimator is dominated by non-infection channels

Characterise the cause. Candidates, **none assumed**:

- **Horizon and discounting.** Infections averted accrue early; survival gains accrue late and are
  discounted hardest. Report the undiscounted decomposition alongside.
- **Background demography swamping the signal.** Total population differences between arms that have
  nothing to do with HIV. Quantify.
- **Stage misallocation** feeding survival (A-13's mechanism). **Cross-reference A-23; do not duplicate it.**
- **The estimand itself.** If ΔQALY at the population level is mostly demographic, the fix may be to
  report a **per-protocol or cohort-based estimand** rather than a population one. That is a modelling
  decision, not a bug fix.

---

## 7. Acceptance

- [ ] Decomposition **sums to the total ΔQALY within 1e-6** — stated before the components are read.
- [ ] Every component reported with across-seed SD and correlations with both channels.
- [ ] QALY per infection averted reported as a **distribution**, and A-16's 183 either explained or reproduced.
- [ ] Joint regression reported with both coefficients, SEs, and the partial correlation.
- [ ] **Resolution stated for every statistic at n=15**, and an explicit statement where n=15 cannot resolve the question.
- [ ] A-3 addressed: state whether A-2's results close it, and on what criterion.
- [ ] A recommendation on the estimand, with its cost.
- [ ] Full suite run; failure/error set compared exactly.
- [ ] `git diff` on tracked files empty, or explained.

## 8. Escalate immediately if

- The components do not sum. Stop; the decomposition is wrong.
- The dominant component is not attributable to any HIV state. **That is the headline and it should lead the report**, not appear in a subsection.
- QALY per infection averted is outside any published range by an order of magnitude and Step 1 does not explain it.
- **n=15 cannot resolve the central question.** Say so and name the n required. Do not report an underpowered correlation as a finding — A-16's CV<0.50 criterion is the cautionary precedent.

## 9. Do not

- Do not change any utility weight, cost, discount rate or horizon. **§1A.4.** These are exactly the
  parameters that would "fix" the symptom while hiding the cause.
- Do not re-run the calibration or touch any allocator.
- Do not recompute or republish an ICER in this task. A-2 characterises the estimator; what the paper
  reports is a separate decision that depends on the answer.
- Do not use the 12/15 sign count as evidence of anything — the A-4/A-5a review established it is
  exactly what the mean and SD predict.
