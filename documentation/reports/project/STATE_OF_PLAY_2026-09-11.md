# State of play — updated 2026-09-14 (A-28 + A-25 + C-4 + **C-1/C-3** shipped; **first production changes since A-12**)

> Archived under `documentation/reports/project/`; original filename retained.

> **2026-09-14 — new: `MODEL_LIMITATIONS.md`.** Structural limitations are now recorded in one
> register with their evidence, rather than scattered across reports. Ten entries as of today,
> covering the C-1 stage and injection-point limitations, the contact/beta ridge, the
> block-diagonal market separation, the capped CSG PrEP series, the global-allocator coupling
> that survives at twelve call sites, and the unresolved ΔQALY decomposition. Assemble the
> thesis limitations section from that file.

---

## HEADER — C-1/C-3 landed 2026-09-14. Read this before quoting any figure.

**Digest lineage:** `8d186b35…a455` (A-20/21/22) → `0215d456…eeb8` (A-25) → `de6a2c63…4d80` (C-4)
→ **`c61a75eb…2015` (C-1)**. Anything computed before 2026-09-14 is stale.

**1. What the model IS, for the write-up.** A **dynamic transmission model for the Dutch MSM
population, coupled to an exogenously forced, non-transmitting non-MSM PLHIV cohort.** This is
forced by structure, not chosen: `transmission_corrected.py:117-121` defines exactly three routes
(F←HetM, HetM←F, MSM←MSM) and `_select_beta` returns 0.0 for every other pair. **There is no
MSM↔heterosexual route.** Endogenous incidence was already 100% MSM *before* C-1 (verified: 0 in
all 204 rows of `hta_baseline_prefreeze.csv`). C-1 did not impose this; it made the non-MSM burden
observed instead of absent. **"Population-level infections averted" is MSM infections averted and
must be labelled so.**

**2. The deterministic allocator couples strata — DETERMINISTIC MODE ONLY.** MSM infections
+1,805 (+6.5%) in deterministic mode: `deterministic_allocate_events` computes its target over the
whole tensor and ranks residuals across all cells, so every stratum competes for the same integer
units. Demonstrated in isolation. **In stochastic mode it is absent** (−5.0 ± 14.9, −0.0%), because
the A-28 coupled samplers key per stratum. The headline is stochastic, so it is not contaminated.
**Not created by C-1, only exposed by it.** D-14 / A-17. Left as a finding, not fixed.

**2b. The non-MSM epidemic is not dead in stochastic mode.** Deterministic gives exactly 0
endogenous non-MSM infections; stochastic gives ~72 even with C-1 off, and ~166 with it on — a
**~1.0% double count**, non-zero in 15/15 seeds. "Endogenous incidence is 100% MSM" is a
**deterministic-mode statement**. The structural claim (no MSM↔heterosexual route) is unaffected
and remains exact.

**2c. Headline, post-C-1 (A-16, n=15 CRN-paired).** **Every switch level now has a determinate
sign**; CI widths narrowed 4.0–9.4× versus 2026-09-11. Switch 10% QALY-positive (+96.4);
25%+ QALY-negative and worsening, so lenacapavir is **dominated at 25%+** on these inputs.
**Caveat: A-16 aborted in its own `validate_reproduction` gate** because A-15's reference
artefacts predate A-28/A-25/C-4/C-1. Figures are computed from its per-seed output; re-baselining
A-15 is an open decision.

**3. The silent-default pattern claimed a third victim.** A-25 (age-range key), C-4
(`default_before`), now `ConfigReader.disease()` — an allowlist of four keys that silently
discarded a present, well-formed, validated config block, so C-1 ran as a no-op with no error.
**When adding any disease-level config block, check the allowlist.** Guard tests now exist.

**4. The C-3 base case was resting on a false premise.** "2020-24 are flat" is wrong: slope
**+10.30/yr (SE 3.71, t = +2.78)**. Base case re-anchored to 2022-2024 (mean 203). A fourth rule
was added because the brief's three were all flat-or-falling — a one-sided sensitivity set.
**Spread across the four rules over the 40-year horizon is 7.41x**, and 100% of the ICER period is
governed by this rule since nothing observed extends past 2024.

**5. Do not report the C-1 acceptance quantities as fit quality.** Post-C-1,
`plhiv_female_15plus` is essentially the running sum of the fed-in female series net of mortality.
Its improvement is evidence about mortality and ART retention, not about the transmission model.
Likewise `shm_new_diagnoses_total` and `shm_new_diagnoses_msm` are now **collinear** — verified
exact, residual 0.0 in all 29 years — so they are one constraint counted twice.

**5b. THE VERIFICATION MACHINERY WAS BLIND, AND IS NOW PARTLY FIXED.** Until 2026-09-14
**no test in this repo asserted a model output**. Every green pin measured change-invariant
properties; every stored-reference harness was pinned to a pre-A-28 world. A regression in the
model would have been caught by nothing. **`tests/test_postchange_model_output_baseline.py`**
(+ `tools/postchange_baseline.py`) now pins the deterministic digest, 11 scalars and three
stochastic seed digests, with module hashes for attribution. Teeth verified. **It is a
regression detector, not a correctness certificate -- do not quote baseline values as
results.** The pre-change references are left failing deliberately; a test asserts the stale
digests are still in place so nobody "fixes" the flag away.

**5c. Three tools are SILENTLY stale -- the dangerous category.** `run_a13`, `run_a14`,
`run_a15` read pre-change references **without asserting**, so they blend fresh runs with stale
baselines and emit a clean-looking report. A loud failure (A-16, A-21, A-22, B-8, A-9) is the
system working. These three are not. Also: **A-13's own conclusions are dead** -- its 12.39 SE
mortality anomaly is now 1.03 SE, and the discrepancy has relocated to `infections_averted` at
8.35 SE, which corroborates the variance finding in 5d.

**5d. Two headline claims are FALSIFIED (both independently verified).**
(a) **A-3 closure criterion 1 flipped pass to fail**: deterministic ΔQALY sat 0.33 SE from the
stochastic mean, now **6.57 SE** (832.4 vs 329.5, SE 76.5). A-28 exposed it by working -- the
gap barely moved, the SE collapsed 4x. Closing A-3 on the mean is no longer available.
(b) **The A-16/A-11 corroboration is broken**: `STATE_OF_PLAY:271` and `handoff:911` say
"€595,113 ... factor 4.86 ... A-11 independently 4.06 -- different routes, same factor". The
ratio-of-means ICER is now **€1,771,229 = 14.46x**. The two routes no longer agree; the ~4x
overstatement claim drops to one route until explained.

**5e. The `infections_averted` variance rise belongs to A-28, not C-1 — measured, not assumed.**
The hypothesis that C-1's post-2025 stream adds non-cancelling noise was **refuted**: suppressing
all 8,120 post-fork injections changes the SD not at all (F-test p .80-.99), and the arm
difference in that channel is <= 1 infection in 450/450 rows, < 0.02% of variance. Reverting C-4
restores the 2026-09-11 **means** almost exactly, so C-4 owns the level shift -- but at matched
means the variance ratio is still 1.8-4.1x. By elimination A-28, and **A-28's own report already
recorded it**: `A28_CRN_COUPLING_FIX.md:231` shows `infections_averted` SD 136.351 -> 149.214
(119.8%) with `:237` stating "no mechanism is offered here". **New information: A-28 measured only
`base_case_context`; at the switch levels the effect is 2-4x and was never measured.** Production
CVs are 1.17/0.91/0.64/0.56 at switch 25/50/75/100 against a CV < 0.50 rule -- `infections_averted`
fails the stability rule at every switch level while ΔQALY passes. **Worth one targeted diagnostic
before it propagates into calibration acceptance.** (INFERRED by elimination: A-28 is code-only
and was not reverted directly.)

**6. Before any post-C-1 calibration run:** neutralise `calib_acceptance_gates.py:177` and
`calib_distance.py:159`, which still score all-group `new_infections` against what is now an
MSM-only quantity (B-5). The HM machinery is safe; the legacy path is not.

---


> ## What changed on 2026-09-14, and why it matters more than the individual fixes
>
> Three defects were fixed, and **two of them are the same defect**: a plausible default silently
> standing in for a missing value. That pattern, not any single bug, is the finding.
>
> | | the silent default | what it did |
> |---|---|---|
> | **A-25** | `age_to_idx.get("64-69", last_bin)` | PrEP eligibility ran **15–110+ instead of 15–69**, in all 10 configs, affecting the submitted thesis |
> | **C-4** | `TimeSchedule(default_before=0.0)` on a *multiplier* | **no MSM diagnosed 1985–1995** |
> | **A-28** | (different class) non-monotone samplers | ΔQALY SD **1,200 → 360** |
>
> Both silent-default classes are now structurally impossible: `default_before` is required and
> keyword-only with an AST test; `require_idx()` raises instead of guessing. The guard **immediately
> caught a third instance** (`"49-54"` in the test suite) the day it was added.
>
> **Problem A vs Problem B has blurred.** A-25 and C-4 were filed as Problem A (arithmetic) but both
> **change the calibrated baseline**: A-25 moves deterministic ΔQALY +79% and infections averted +27%;
> C-4 moves cumulative infections **−18%**. Every stochastic and calibrated number in the project is
> now stale. **Do not cite any pre-2026-09-14 figure without re-running it.**
>
> **Deterministic digest lineage:** `8d186b35…a455` (A-20/21/22) → `0215d456…eeb8` (A-25) →
> **`de6a2c63…4d80`** (C-4, current). `tools/run_a21_*` and `tools/run_a22_*` still assert the first
> value and will fail; left as-is, flagged, since they are artefacts of those tasks.
>
> **What is NOT fixed, deliberately:** the pre-1996 MSM diagnosis *number*. C-4's code is fixed to the
> neutral 1.0; the residual (~79% of SHM's MSM ≤1995) is **reported as a result**, because a fitted
> constant was evaluated and rejected on four independent grounds (see the C-4 entry). It is a
> recalibration question, and it should be re-tested after **C-1**.
>
> **Next:** C-1 (exogenous non-MSM forcing) → one joint calibration wave → then the Track A re-runs
> (A-16, A-9) that A-28 and A-25 both invalidated. Running A-16/A-9 before the wave means running them
> twice.

**Orientation for anyone (human or agent) opening this repository.** Read this before the task boards.

Detail lives in `PHASE2_AGENT_HANDOFF_2026-09-09.md` (task board) and `PHASE2_DEVELOPMENT_CHECKLIST.md` (gates and decision log). This file is the narrative those two don't give you.

---

## 1. The one thing to understand: there are TWO problems, not one

They are independent, they need different fixes, and confusing them is the main way to get lost.

| | **Problem A — the arithmetic** | **Problem B — the fit** |
|---|---|---|
| Question | Does the model compute its own answer correctly? | Is the model a good description of the Dutch epidemic? |
| Failure looks like | right model, wrong number | wrong model, computed perfectly |
| Work so far | **Extensive. Substantially resolved.** | **Machinery built. Nothing run. Untouched.** |
| Covers | rounding, allocation, the QALY estimator, random-number handling | calibration, history matching, non-MSM, target selection |

**Everything done in September 2026 was Problem A.** The rounding and allocation work, common random numbers, the mortality trace, and the A-16 results table — all Problem A.

**Problem B has had decisions made and nothing implemented.** The history-matching code exists and has never been run in production; the non-MSM fix is decided and not built; three decisions that drive the outcome are open.

Problem A was done first for a good reason: there is no point fitting a model whose arithmetic you don't trust. That reason has now been discharged.

---

## 2. Problem A — the arithmetic. What happened, and where it landed

The thesis was submitted 2026-06-08 and defended 2026-06-30. Converting it to a manuscript began with verification, and verification found **the thesis numbers do not reproduce**.

### The chain, in order

1. **ENG-01 / A-11 — a rounding bug.** The model moves whole people between boxes each quarter; the maths gives fractions, so it rounds. Rounding each box separately erased rare events. Worth a **4× error** in QALYs per averted infection (thesis 4.196 vs corrected 1.033). *This is what invalidated the thesis numbers.*
2. **A-12 — audit for more of the same.** 258 rounding sites, 51 allocation contracts. Found nothing else actively broken. Negative result, and it's what licensed step 3.
3. **A-4 — common random numbers.** The random ("stochastic") mode's two arms were consuming different random numbers, swamping the intervention effect. Fixed by keying the generator on (seed, year, timestep, process, stratum). **ΔQALY noise cut 97%.**
4. **A-13 / A-14 — the fix for step 1 caused a new bug.** Rounding the *total* and handing out leftovers sends them to the biggest boxes, starving rare states like advanced AIDS. **2.4× understatement** of deaths averted. Aggregates stayed exactly right, which is why two audits missed it.
5. **B-8 — the deterministic shortcut runs a bigger epidemic** than the model specifies: **+17–30%**, in 222 of 222 target-years, present by 1990. The frozen 2025 state every result forks from carries an HIV stock **+14.6%** above the honest mean. Mechanism unidentified.
6. **A-17 — the residual-placement hypothesis, refuted.** Two tasks had fingered the rule that decides *which* cells receive rounding leftovers. It is not the cause: it biases in the **wrong direction** (−0.13% below, against a gap that is above), is **~150× too small**, and replacing it with a verified-unbiased rule made the gap **worse**. It did produce the most useful measurement yet — see below.
7. **D-14 — the resolution.** Rather than chase the rounding further, **stop using the shortcut for results.** Report the honest (stochastic) mean with an interval. The rounding bias becomes a documented limitation of a mode no longer used for the headline.
8. **A-16 — the results table under the honest estimator.** Section 3.

### The generalisable lesson

**ENG-01 and A-13 are two ends of one dial.** Too fine, rare events are erased; too coarse, they're relocated to common states. Both compress the between-arm difference, which *is* the estimand in a cost-effectiveness analysis. **Allocation granularity is a modelling decision, not an implementation detail.**

The corollary that cost two audits: the right test for "are these cells interchangeable" is **"same event probability AND same downstream consequence"**, not "same event probability".

### The open mechanism: why deterministic mode runs a bigger epidemic

A-17's timing profile is still the best description of the symptom. The PLHIV log ratio (deterministic ÷ stochastic mean) rises **+0.0062 per quarterly step, almost perfectly linearly**, from +0.004 in 1975 to a peak of **+0.312 in 1990**, then plateaus and slowly declines — a **constant small per-step multiplicative bias accumulating through exponential growth and stopping at saturation**.

**The Jensen hypothesis was the leading explanation. A-18 refuted it, twice over.**

- **By inspection.** The model never computes `f(n) = 1 − (1−β)^n`. `transmission_corrected.py` runs a two-step binomial that composes to `Binomial(n, prevalence·p_trans)`, expectation `n·prevalence·p_trans` — **affine in the contact count**, so there is no Jensen gap in `n` to find. The hypothesised functional form is not in the model. *(Qualifier added in review: three `min()` caps in the same file are concave and would reintroduce a Jensen term if they bound. Measured on the model's own trace, they never do — **0 of 1,995 cells**, tightest at **0.16% of capacity**. Carry the qualified claim; it survives the objection.)*
- **By measurement.** The literal hybrid — contacts drawn, everything else deterministic, n=50 — moved the median |log ratio| from **0.188377 to 0.188279** against a pre-specified threshold of **<0.09419**. That is **0.104%** of the required reduction, and 0.0047× the median per-year SE. The +0.0062/step signature is untouched: slope 0.00607 before and after.

**So three mechanisms have now been proposed and three refuted:** residual placement (A-17), the entire deterministic apportionment family (A-19, by proof), and transmission nonlinearity in the contact count (A-18). **The cause of the level inflation is unidentified,** and A-18 was right to decline a fourth guess.

**The three remedies were probed anyway, and none is available on the current evidence.**

| remedy | verdict | why |
|---|---|---|
| **A — run calibration stochastically** | **conditional on D-11, which flips it** | Median implausibility falls 19.5% and 63/92 targets improve, but **`I_max` — which is what governs NROY — gets worse** (22.163 → 22.654), because the binding target is one the model *undershoots* 29-fold. Exclude that series and **nothing is excluded at all** and stochastic wins by 20.7%. Compute is cheaper than the spec says: **measured 6.40 s/run ⇒ 1.11 h on 96 vCPUs**, not 10.4 h. |
| **B — carry the gap as a derived discrepancy term** | **not worth building** | Derived before rescoring and never adjusted, per the boundary rule. **NROY stays 0/4,000.** The term is swamped by the Andrianakis `V_disc`. A usable NROY would need ~**10×** the measured discrepancy — reported, **not adopted**. |
| **C — second-order `½·f''(μ)·σ²` correction** | **blocked, and separately a no-op** | The model **has no contact-count `σ²`**: contacts are one deterministic float per orientation/activity pair in **both** run modes, with zero RNG calls. Supplying one would mean inventing a parameter. Bounded using the only variance the model does have, the correction is **1.3e-9 infections** and bit-identical to production. |

**The counter-argument on remedy A was itself wrong, and the correction matters.** The brief argued that averaging n seeds tightens the implausibility denominator by √n and therefore excludes *more*. But `V_ensemble` is currently **0.0, status `underivable`** — not carried at n=1 — so switching **adds** a term and the denominator **grows**. Either way it is negligible: median **0.034%** of the new total variance.

### A-20 localised it: background mortality

A-18's more generous arm — full stochastic transmission, everything else deterministic — overshot to −27.9%, and the review noticed it tracked the fully stochastic reference to ~1% through 1990 before separating from 1995. That pointed at the non-transmission processes and suggested diagnosis and ART. **A-20 ran the sweep and the pointer was wrong in its specifics and right in its direction.**

Twelve arms, n=50. **Background mortality is the only material one:**

| arm | median \|log ratio\| | displacement | in median SE |
|---|---:|---:|---:|
| **background mortality** | **0.04901** | **+0.13937** | **+6.76** |
| migration | 0.17590 | +0.01248 | +0.61 |
| diagnosis | 0.18530 | +0.00308 | +0.15 |
| *deterministic baseline* | *0.18838* | *0* | *0* |
| ART | 0.21485 | −0.02648 | −1.28 |
| transmission (A-18) | 0.33415 | −0.14578 | −7.07 |
| disease progression | 0.41206 | −0.22369 | −10.85 |
| HIV mortality | 0.79953 | −0.61115 | −29.65 |

Background mortality recovers **74%** of the gap. Diagnosis came 4th at +0.15 SE and ART was *adverse*. The effect opens in **1975**, not 1995 — **the starting condition the review supplied was falsified, and A-20 says so plainly.** That prior was labelled weak evidence when it was written, which is why it was safe to act on and safe to lose.

### A-21 confirmed the mechanism. Defect 1 and Defect 2 are the same defect.

A-20 stopped at "background mortality, mechanism unidentified." The review disputed that — it cited A-17's 0.13% residual-placement figure as the bound, when the relevant bound is A-19's compositional one (the smallest-cell row ending at 1.8% of its exact total). Looking at the live code gave a candidate. **A-21 traced it and it confirmed.**

`demography.py:_calculate_life_table_mortality` (lines 981/989) passes `population[age_idx, orient_idx, :, :, :, :, :]` to `deterministic_allocate_events` **at one shared rate** — a block holding the enormous HIV-negative cells and every tiny HIV-positive cell together. The allocator preserves the block total and hands residuals to the largest fractional parts. One deterministic run, 12,240 blocks traced:

| | |
|---|---:|
| HIV-positive expected background deaths | **8,029.74** |
| HIV-positive allocated | **6,259** |
| **shortfall** | **1,770.74 (22.1%)** |
| HIV-negative excess | 1,733.75 |
| block conservation error | **0 exactly** |
| occupied HIV+ cells receiving **zero** deaths | **93.7%** |

The identity closes: 1,770.74 − 1,733.75 = 36.996, the ordinary block rounding. Nothing residual.

**And the time profile is A-17's fingerprint.** Shortfall by era: **97.3%** (1975–1990), 54.8% (1991–2000), 28.3% (2001–2010), 14.9% (2011–2025). **For fifteen consecutive years, 1975–1989, HIV-positive people received literally zero background deaths.** A mechanism at full strength through the exponential growth phase, decaying as HIV+ cells grow out of the sub-unit regime, is exactly "+0.0062 per quarterly step, linear to a 1990 peak, then plateau." A-17 wrote that *nothing about allocation looks like that*. This does.

**And the size is right.** 1,770.7 deaths withheld against a deterministic-minus-stochastic PLHIV gap of **+2,243** at 2025 — 79% of the whole gap before any compounding. (Plausibility check only; the channels are non-additive and no share is being attributed.) Every previous candidate had to explain away a factor of 50–150. This one doesn't.

### Why four earlier allocation tests all came back clean

- **A-14 / A-15** swept the granularity dial at the transmission, diagnosis and ART call sites. **They never touched the background-mortality call.**
- **A-17** swapped the rule **globally** — and A-20 shows the allocation effect has **opposite signs across processes** (+0.139 background mortality against −0.224 progression, −0.611 HIV mortality, −0.146 transmission). A global swap nets out negative.
- **A-19** *proved* every size-monotone rule starves identically in the sub-unit regime. A-21 shows **97.8%** of occupied HIV+ cells sit exactly there. Changing the rule was never going to work.

**The three refutations refuted three proposed mechanisms, not allocation itself.** Defect 1 and Defect 2 are now one defect at one site.

**One thing still open:** A-20's HIV-mortality arm (+122% above the stochastic reference) is *not* explained — the aggregate under-allocation there is only 0.189%. But the side trace found cross-stage redistribution hiding under that near-exact aggregate (sub-unit AIDS-on-ART over-allocated at 101.7%, chronic-on-ART under at 92.2%), which is A-13's mechanism seen directly at the mortality call.

---

## 2B. The 2026-09-13/14 batch — Track A's diagnosis is now closed

Six reports. Independent review: `REVIEW_ADDENDUM_BATCH_2026-09-14.md`. Everything below was reproduced from the reports' own retained files.

### The headline came from a question nobody asked

**A-24 found a defect in the CRN implementation itself.** Common random numbers only work if each draw is a *monotone* function of a keyed uniform — perturb the parameter slightly between arms and the draw moves slightly. Every sampler satisfies that **except one**: `counter_based_rng.py:222–225` calls NumPy's `Generator.multinomial`, which uses rejection sampling and is **not monotone in its weights**. Migration allocation uses it, weighted by adult age × sex counts that differ between arms by a hair.

The probe is decisive. At a **one-person** weight change:

| sampler | mean L1 | max L1 |
|---|---:|---:|
| production `Generator.multinomial` | **0.351** | **188** |
| coupled inverse-CDF | 0.016 | 2 |
| *expected shift from the change itself* | *0.0053* | — |

~14,000 quarterly migrants get reallocated as if on fresh random numbers → female stock drifts → births drift → sexual debuts drift → **61.4% of Var(ΔQALY)**.

**The keys are fine** — 9.77M paired draws verified 100% bitwise aligned. It is a sampler problem, not a keying problem.

**The fix is pre-measured:** ΔQALY SD **1,200 → 360**, variance to **9.0%**, **11.1× equivalent seeds**. Brief: `TASK_BRIEF_A28_CRN_COUPLING_FIX_2026-09-14.md`. **Both couplings must ship together** — each alone is close to worthless and makes the other channel worse.

### The estimator was never broken

- **A-2:** the `r = +0.12` with infections averted was **swamping, not absence**. Infection-coupled mean **+1,592.7** (SD 276, r = +0.83, 15/15 positive) sitting under non-HIV noise of SD **1,203**. Partial r controlling for person-years: **+0.988**. Decomposition reconciled 227/227 to 4.6e-7.
- **A-8:** **1.911** discounted QALYs per averted infection, against an independent cohort derivation of **1.832** — 1.7 SE, inside the pre-specified band. Small because of the **horizon** (infections spread over 2025–2064, ~20 years each); the same model gives 4.01 lifetime and 6.28 at age 25–29.
- **A-16's "183 QALYs per averted infection" is dead** — a switch-10 ratio of means with denominator 0.667, SE 5.50, 6/15 seeds negative. Strike it wherever it appears.

### Two things closed, one still open

- **A-23:** the +122% HIV-mortality anomaly is **transmission feedback**, tested — hold transmission at the deterministic force of infection and it collapses (ratio 0.011). My cross-stage premise was wrong and the report said so.
- **A-22: NO_MATERIAL_LEVEL.** Best grouping (split on HIV status) reaches 0.10840 against a 0.09419 threshold. It halves the defect and still can't repair the calibration. **With A-19 (rules, by proof) the deterministic repair space is now closed from both sides. Deterministic mode is a disclosure item.**
- **A-9 stays open.** HIV mortality repairs the infections grid but breaks the mortality check; my background-mortality prior failed at 0.72 SE. It may not close — the honest fallbacks are to report the scenario grid from stochastic runs only, or drop it.

### And three findings that reach the thesis

A-8 surfaced these out of scope. **Treat them as one workstream, not three fixes.**

- **A-25 — the PrEP eligibility age cap is silently ignored.** `"64-69"` is not a bin name (`"65-69"` is), and `age_to_idx.get(name, n_age_groups - 1)` **silently falls back to the last bin**, so eligibility spans **15 to 110+**. **Two defects:** the typo and the silent fallback, which contradicts the project's own ENG-F04 rule and will recur elsewhere. **It is in every config, so it predates and affects the submitted thesis** — a disclosure question of the A-11 class.
- **A-26 — infection risk has no age dimension.** A structural modelling choice, not a bug. With A-25 it makes averted infections old-skewed.
- **A-27 — HIV life-years lost fall with age; the evidence says they rise.** Model HIV mortality is additive and age-independent.

**Why they are one workstream:** the model gives young infections **too large** a loss while averting **too few** young infections. **1.911 may be right in level partly because these cancel.** Fixing one alone could move the headline materially. It also means A-8's agreement is an **internal-consistency check, not an external validation** — both sides share the same age-independent mortality structure.

---

## 3. What Problem A bought you: the A-16 results

`outputs/hta_results/a16_stochastic_headline_20260911_030744/` — n=15, CRN-paired, seeds 42–56.

**The conclusion, and it is robust:** lenacapavir at list price is not cost-effective at any switch level or any ZIN threshold, by a margin of hundreds of millions to billions. NMB at €80k/QALY: −€492.6m [−547.2, −437.9] for oral PrEP vs none; −€4,548.6m at switch 100%. No confidence interval comes within €398m of zero.

**Why it's robust despite a noisy ΔQALY:** cost dominates. |ΔCost| is €569m–€4,441m while |ΔQALY × 80k| is only €6–107m — **cost is 88–99% of NMB**. ΔQALY could be wrong by an order of magnitude without touching the conclusion.

**Threshold price — the headline, and it collapsed. FIGURES STALE 2026-09-14.** ~~Pooled root at €80k/QALY: **€1,959** (switch 10%) down to **€159** (switch 100%)~~ **Post-change conditional means at €80k: cost-effectiveness threshold price €1,831 at switch 10% (defined in 14/15 seeds) and UNDEFINED at 25-100% (0/15), because ΔQALY is negative there; the QALY-loss compensation price runs €623 / €254 / €190 / €104 at switch 25/50/75/100.** **Note a pre-existing error in the struck text: €1,959 was a cost-effectiveness threshold price and €159 a QALY-loss compensation price — two different quantities, defined for opposite signs of ΔQALY, quoted as one range. They should never have been a range.** Formerly — **0.4–4.8% of the €41,110 list price**, where the thesis said €9,987 (~24%). **A 5–63× reduction.**

**Corroboration — WITHDRAWN 2026-09-14.** ~~A-16's oral-PrEP ICER is €595,113/QALY against the thesis's €122,477 — a factor of 4.86. A-11 independently put the thesis overstatement at 4.06. Different routes, same~~ **The post-change ratio-of-means ICER is €1,771,229/QALY = 14.46×, against A-11's 4.06×. The two routes NO LONGER AGREE.** The ~4× overstatement claim drops from 'two independent routes' to one (A-11) until the discrepancy is explained. Verified 2026-09-14 from `a16_stochastic_headline_20260914_100232_post_c1`. Original text struck, not deleted, so the withdrawal is visible. Same factor.

**Three traps in reading that table.** Don't quote the conditional CE price (€4,081 etc.) — it conditions on ΔQALY > 0, keeping only the favourable seeds, so it is biased **optimistic**; use the pooled root. Don't trust the CV<0.50 stability verdict — NMB passes only because NMB ≈ −ΔCost (its CV rises with WTP and converges to ΔCost's as WTP→0). And **don't just increase n**: at switch 10%, ΔQALY +122 with 0.67 infections averted is 183 QALYs per infection against a model value of 1.03 — incoherent, so more seeds would give *precisely estimated noise*. The coherence ratio is a better validity test than the CI. Base case passes (1.1); every substitution scenario fails.

**Every substitution scenario has positive infections averted and negative ΔQALY** — the lenacapavir adverse-event disutility (a cabotegravir proxy, no lenacapavir evidence, A-10) overwhelms the infection benefit. Every substitution threshold price is conditional on an unsupported parameter.

---

## 4. Problem B — the fit. Nothing here has been done

**This is the part that is still open, and it is where the remaining scientific risk lives.**

### What's established

- **NROY = 0 of 4,000.** The history-matching prototype rules out every parameter set tested, on a target set containing *only* directly observed surveillance counts. **Indicative, not established** — those particles are pre-fix and came from an ABC search, not a proper space-filling design. The real wave 1 has never run.
- **The model is effectively MSM-only.** 0.03 non-MSM diagnoses/yr against an observed 199 — 38% of real Dutch diagnoses. Reaching the observed level needs heterosexual transmission parameters ~15.6× calibrated, 11–40× outside the literature interval. Not fixable by tuning.
- **Modelled incidence runs ~2.7× the CSAVR estimate.**
- **UNAIDS PLHIV / incidence / AIDS-death series are not independent evidence.** They're Spectrum CSAVR outputs — a statistical fit to the same surveillance data already used as targets. Demoted to cross-validation comparators (D-07).
- **New from B-8:** calibration waves run in the deterministic mode that runs 17–30% hot. So the model would be fitted using a systematically inflated version of itself.

### What's built but never run

`hm_target_register.py`, `hm_variance_budget.py`, `hm_implausibility.py`, `hm_diagnostics.py`, `run_history_matching.py`, plus 105 tests. The runner refuses to start without `--acknowledge-prerequisites`, and the prerequisites are not met.

### What's decided but not implemented

- **D-03** — the non-MSM epidemic is treated as an **exogenous observed forcing function**: feed the real SHM non-MSM diagnosis series in directly, so those people enter the PLHIV stock and consume costs but generate no modelled transmission. **Decided. Task C-1. Not built.**
- **D-04** — calibration output is a history-matched plausible-fit ensemble. **No "posterior", no "credible interval"**, anywhere.

### What's open and drives the outcome

| | decision |
|---|---|
| **D-10** | How finely to score the fit. Moves NROY more than any other lever: 92 targets → 0/4,000; 10 targets → 2,272; 2 targets → 2,347. |
| **D-12** | Whether model discrepancy is set relative to ensemble spread or to each target's own scale. Same nominal 10% is a 4% tolerance on one target and ~5,300% on another. |
| **D-13** | Whether to proceed with observation error = 0 for every tier-1 target. No Dutch source for diagnosis-count ascertainment error has been found. |
| **B-1 / B-2** | Which parameters are in the calibration space at all (27 vs 32 across two config files). The prior box *is* the space being excluded from. |

---

## 5. What you can and cannot claim right now

| claim | status |
|---|---|
| Lenacapavir is not cost-effective at list price | **Robust.** Driven by the cost side, which barely depends on the epidemiology. |
| The thesis's health effect was overstated ~4× | **Well-supported**, two independent routes. |
| ~~Threshold price is €159–€1,959, not €9,987~~ **STALE — see §3; the two endpoints are different quantities and the values are pre-change** | **Direction robust, magnitude conditional.** It depends on infections averted, which depends on a parameter set that fails its own gates. |
| Any absolute ICER | **Not defensible.** No accepted parameter set exists. |
| Scenario ranking (which switch level is "best") | **Re-derive stochastically.** The deterministic ranking was an artifact. |

---

## 5A. C-4 — a confirmed bug found 2026-09-13, and it changes the Track B plan

**The model diagnoses zero MSM before 1996.** Literally zero, every year, 1975–1995 — then 438 in 1996. Total diagnoses *fall* 53 → 20 while the epidemic nearly triples, because what you are seeing is the non-MSM remainder alone.

**Cause:** the MSM relative-diagnosis multiplier lives on a schedule whose first anchor is 1996, and the schedule class returns **0.0** before its first anchor. The construction site never overrides that default. Multiplier 0 ⇒ MSM diagnosis rate 0.

**It is a bug, not a choice.** The neutral value for a *multiplier* is 1.0; the ART-mortality schedule in the same file passes `default_before` correctly; the 0.0 default is right for *availability* schedules (the base diagnosis schedule starting 1985 is correct — the antibody assay did not exist before then) and wrong for this reuse. And MSM were being tested in the Netherlands from **1984** — 1996 is when the *data* starts, not the diagnoses.

**This reverses the D-11 recommendation.** Those six ECDC 1990–95 targets were the only things in the register with I > 3, they ramp monotonically 5.23 → 22.16, and nothing exceeds 3 from 1996 onward. **They were detecting the bug.** Do not drop them until C-4 is fixed.

**And it reframes C-1.** The pre-1996 shortfall is not primarily a non-MSM modelling gap — it is that MSM are not diagnosed at all. Building C-1 first would have tuned a forcing term to paper over this.

**The fix is a modelling decision**, not a patch: most defensible is to add explicit pre-1996 anchors and calibrate them against testing-technology and policy eras, the same way the post-1996 anchors are justified. Note the code permits multipliers above 1.0, which the evidence may require. **Audit every other schedule for the same silent zero — only one of eight construction sites passes the argument.**

---

## 6. Priority order — and the Track B entry gate

**Track A's diagnostic work is done.** What remains in Track A is two implementation decisions and one open defect. **Track B cannot start clean until C-4 is fixed**, because every wave would otherwise be scored on a model that diagnoses no MSM for 21 years.

### Do these first — they are cheap and they unblock everything

1. **A-28 — couple the two samplers.** Pre-measured at 11.1× effective seeds. Changes no parameter, no age structure, only the noise. Contained: the golden CRN pins cover the key path, not the sampler. **Brief written.**
2. **C-4 — fix the MSM diagnosis schedule.** A modelling decision (recommended: explicit pre-1996 anchors tied to testing-technology and policy eras), plus a mandatory audit of all eight `TimeSchedule` construction sites — only one passes `default_before`. **This is the Track B blocker.**
3. **Then re-run A-16.** The headline economics with SD 360 instead of 1,200 is a different result, and it is what the paper reports.

### Then the age-structure workstream

4. **A-25 / A-26 / A-27 together**, with one combined before/after at fixed seeds. A-25 also raises a thesis-disclosure question of the A-11 class.

### Track B — the decision board

**Five decisions, none of them needing compute. All five must be declared before wave 1.**

| id | decision | state |
|---|---|---|
| **D-11** | role of `ecdc_hiv_confirmed_cases` (1990–95 / full overlap / cross-validation) | **Do not touch until C-4 is fixed.** Those six targets were detecting the bug. Re-decide on the post-fix wave. |
| **D-10** | scoring granularity — all 92 targets, anchor years, or a summary set | **The single biggest lever on the answer**: 92 targets → NROY 0/4,000; 10 recent → 2,272; 2 → 2,347. Must be declared before wave 1. |
| **D-12** | `V_disc` — fraction of ensemble output variance (Andrianakis) vs relative to each target's scale | On the prototype the same nominal 10% gives a 4% tolerance on one target and ~5,300% on another. Faithful vs defensible. |
| **D-13** | proceed with `V_obs = 0`, or find an ascertainment-uncertainty source first | Zero biases towards **over**-exclusion, so any NROY is a subset of the true one. Defensible, but must be declared. |
| **D-03 / C-1** | non-MSM exogenous forcing | Decided, never built. **C-4 reframes it** — smaller and better-defined than it looked, since the pre-1996 gap was mostly MSM-not-diagnosed. |

**What is already built for Track B:** the full history-matching machinery (implausibility, variance budget, target register, wave loop; 105 tests), 92 tier-1 scalar targets, and a documented tier-1/tier-2 epistemic split.

**What is not:** any production wave. And the prototype's 4,000 particles were generated with the pre-fix `demography.py` — **they test the machinery, not the model, and no NROY set derived from them may enter a result.**

### Still open, and not blocking

- **A-9** — localised but not repaired. May not close; fallbacks are stochastic-only scenario reporting, or dropping the scenario analysis.
- **F-1** — the perspective relabel. Zero code dependency. Deferred at Rishi's request.
- **D-09** — lenacapavir AE disutility as co-primary.

---

## 7. Where the evidence lives

| task | directory |
|---|---|
| A-12 allocation audit (+ review addendum & postscript) | `outputs/hta_results/a12_deterministic_allocation_audit_20260910_103000/` |
| A-13 HIV-mortality trace | `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/` |
| A-4 common random numbers (+ review addendum) | `outputs/hta_results/a4_common_random_numbers_20260910_170048/` |
| A-15 switch-grid hypothesis test | `outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351/` |
| D-14 synthesis memo | `outputs/hta_results/d14_synthesis_memo_20260911_040934/` |
| **A-16 headline economics** | `outputs/hta_results/a16_stochastic_headline_20260911_030744/` (the `_030204` dir is a superseded incomplete attempt — do not cite) |
| A-5a seed variability (**use the 0911 run — 0910 is pre-CRN**) | `outputs/calibration/a5a_epidemiological_seed_variability_20260911_042330/` |
| B-8 deterministic-vs-stochastic levels | `outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/` |
| **A-17 residual placement (refuted; carries the timing fingerprint)** | `outputs/calibration/a17_residual_placement_20260912_173211/` |
| **A-18 Jensen mechanism (refuted; + review addendum)** | `outputs/calibration/a18_jensen_mechanism_20260912_205053/` |
| A-18 remedy probes | `outputs/calibration/a18_remedy_probes_20260912_211631/` (the `_210713` dir is superseded — do not cite) |
| A-19 divisor & controlled rounding | `outputs/calibration/a19_divisor_controlled_rounding_20260912_183159/` |
| **A-20 process decomposition (+ review addendum)** | `outputs/calibration/a20_process_decomposition_20260912_221520/` |
| A-20 Step 2 localisation | `outputs/calibration/a20_step2_localisation_20260912_230327/` |
| A-20 consolidated tables | `outputs/calibration/a20_process_decomposition_analysis_20260912_231933/` (the `_231601` and `_231650` dirs are incomplete chart attempts — do not cite) |
| **A-21 rare-stratum starvation — CONFIRMED (+ review addendum)** | `outputs/calibration/a21_rare_stratum_starvation_20260913_001500/` |
| **A-22 block grouping (NO_MATERIAL_LEVEL)** | `outputs/calibration/a22_background_mortality_grouping_20260913_225630/` (the `_224437` dir is superseded) |
| **A-9 switch-grid decomposition** | `outputs/hta_results/a9_switch_grid_process_decomposition_20260913_231232/` |
| **A-23 HIV-mortality anomaly (explained)** | `outputs/calibration/a23_hiv_mortality_anomaly_20260913_235530/` |
| **A-2 estimator decomposition** | `outputs/hta_results/a2_estimator_decomposition_20260913_235517/` |
| **A-8 QALY per averted infection** | `outputs/hta_results/a8_qaly_per_infection_20260914_005530/` |
| **A-24 CRN drift — THE KEY RESULT** | `outputs/hta_results/a24_entry_pathway_crn_drift_20260914_010031/` |
| Batch review addendum | `REVIEW_ADDENDUM_BATCH_2026-09-14.md` |
| History-matching build (HM-1…HM-4) | `outputs/calibration/hm_prototype_20260910/` |

Task briefs for work not yet done: `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md`, `TASK_BRIEF_STOCHASTIC_HEADLINE_2026-09-11.md`, `TASK_BRIEF_RESIDUAL_PLACEMENT_2026-09-12.md` (**done — refuted**), `TASK_BRIEF_JENSEN_MECHANISM_2026-09-12.md` (**done — refuted**). `TASK_BRIEF_PROCESS_DECOMPOSITION_2026-09-12.md` (**A-20 — done**). **A-21 is complete from the specification registered in the handoff; no standalone brief was created.**

---

## 8. Standing rules that have repeatedly mattered

1. **Judge every allocation or rounding change on the INCREMENTAL between-arm difference, never an aggregate percentage.** ENG-01 was cleared on an aggregate check ("+0.014%, small") and was the dominant term in the analysis. A-13's aggregates were exactly right while the incremental was wrong 2.4×.
2. **Never tune a tolerance, threshold, bound or discrepancy term to make something pass.** An empty NROY, a failing gate and a wide interval are all results.
3. **Never invent a parameter value.** A missing value is a blocker to report.
4. **Never overwrite an existing `outputs/` directory.**
5. **Before starting anything: does this change a number in the paper, or license a sentence in the paper?** If neither, don't do it.
6. **A precise estimate of the wrong quantity is worse than an obviously noisy one.** Check internal coherence (QALYs per averted infection) before trusting a confidence interval.

---

## 9. Environment notes

- The project `.venv` (Python 3.11, macOS) is the reference interpreter. Full-suite baseline: **58 failed / 5 errors**, a known pre-existing set — mostly missing `torch`/`sbi`, plus tests opening `1980_test.json` at the repo root when it lives in `configs/`.
- Deterministic `base_case_context` reproduces exactly, to full double precision, across macOS/3.11 and Linux/3.10.12/numpy 2.2.6. Those eleven values are a **reproducibility** anchor — they sit on a biased mortality channel, so they are **not** a correctness anchor.
