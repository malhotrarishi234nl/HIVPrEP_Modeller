# Task brief — A-16: the headline economics under the stochastic estimand

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-11
**Owner:** Rishi Malhotra
**Purpose:** produce the numbers Rishi needs in order to choose which paper he is writing.
**Type:** deliverable, not diagnostic. This task should end in a table that could be pasted into a results section — not in a finding.

---

## 0. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — the verification and academic-integrity standard. Binding here as everywhere.
2. `outputs/hta_results/d14_synthesis_memo_20260911_040934/D14_SYNTHESIS_MEMO.md` — Recommendation 1 is why this task exists.
3. `outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351/` — the paired stochastic switch-grid runs this task builds on.

---

## 1. Why this task exists

Every number computed under the CRN-paired stochastic estimand so far is **epidemiological** — infections averted, deaths, QALYs, person-years. **There is no stochastic ICER, cost, net benefit or threshold price anywhere in the project.** Confirmed by search on 2026-09-11.

So the central economic quantities have only ever been computed in deterministic mode, which A-13/A-14/A-15/B-8 have now shown is biased in three separate ways, and which D-14 Recommendation 1 proposes to abandon as the base-case estimand.

**The question this task answers is not "what is the ICER".** It is: **does a stable headline quantity exist under the honest estimator, and if so which one?** Rishi has already flagged the threshold price as the better headline on the grounds that a ratio should be more stable across calibration variants than an absolute ICER. That is a reasonable prior and it has never been tested.

---

## 2. What this task is NOT, and must say so in its own report

**These numbers are not a defensible final result and must not be cited as one.** The parameter set is still `V13.22_20260604_nearfit_rank001_basecase`, which fails four of its own acceptance gates, and the history-matching prototype returns an NROY of 0 of 4,000 — there is currently no defensible parameter set for any economic result to be computed *at*.

**What these numbers are:** a decision aid about the *stability* of candidate headline quantities under an unbiased estimator. Put that sentence, or one like it, at the top of the report.

---

## 3. Method

### 3.1 Runs

- **Scenarios:** the lenacapavir substitution switch grid — 10 / 25 / 50 / 75 / 100 — plus `base_case_context` (oral PrEP vs no PrEP) as the reference comparison.
- **Mode:** CRN-paired stochastic, **n ≥ 15, seeds 42–56**, so results are directly comparable with A-4 and A-15.
- **Reuse before re-running.** If A-15's runs retained full cost streams, compute from those. If not, re-run — but say which you did.
- **Report the deterministic value alongside every stochastic one**, labelled as the biased comparator. The contrast is part of the deliverable.

### 3.2 Quantities

Per switch level, report the mean and an interval across seeds for: incremental cost, incremental QALYs, incremental infections averted, net monetary benefit at the ZIN thresholds, and the threshold price.

### 3.3 Three methodological traps — get these right, they are the substance of the task

**(a) Do not average ICERs across seeds.** An ICER is a ratio; the mean of ratios is not the ratio of means, and the ratio explodes when its denominator approaches zero. **Compute the ICER from the mean ΔCost and the mean ΔQALY**, and report its uncertainty through the net-benefit distribution rather than through a distribution of ICERs. If you produce a "mean ICER" or an "ICER standard deviation", you have done it wrong.

**(b) ΔQALY straddles zero at some switch levels.** At switch_10 the stochastic ΔQALY is +121.99 with SD 366.98 (n=15) — the interval spans zero, so the ICER is not merely uncertain, it is **undefined in the usual sense** and moves between quadrants of the cost-effectiveness plane across seeds. Report the **proportion of seeds falling in each quadrant** and use net benefit as the primary summary. Do not paper over a sign change with a point estimate.

**(c) Define both threshold prices explicitly — they are different quantities.** F-4 records this naming collision and it must not propagate:
- the **price at which lenacapavir becomes cost-effective at a stated WTP threshold** (the thesis's ~€9,987/yr figure), and
- the **price at which net benefit versus oral PrEP flips sign** (the probe's €49–€2,063 figures).

Name them distinctly, compute whichever are well-defined per switch level, and never use the bare term. Where ΔQALY < 0 the first is undefined — say so rather than reporting a number.

---

## 4. The verdict this task must deliver

A single explicit statement, with numbers behind it:

> **Is there a headline quantity that is stable across seeds under the honest estimator?**

Compare the coefficient of variation (SD / |mean|) across seeds for: ΔQALY, incremental cost, net benefit, and each threshold price, at each switch level. State plainly which — if any — is stable enough to headline a paper, and which are not.

For calibration, the known figures: ΔQALY has CV ≈ 1.25 at base case (mean 956.2, SD 1,200.1) and ≈ 3.0 at switch_10 (mean 122.0, SD 367.0). **A quantity is only worth headlining if it does materially better than that.**

---

## 5. Acceptance

- All six scenarios run or reused at n ≥ 15, with seeds recorded.
- A results table of publishable quality: per scenario, the stochastic mean and interval for every quantity in §3.2, with the deterministic value beside it, labelled biased.
- The three traps in §3.3 visibly handled — quadrant proportions reported, no averaged ICERs, both threshold prices named separately.
- The §4 verdict stated in one sentence with the CV table behind it.
- The §2 disclaimer at the top of the report.
- `manifest.json` with command, commit and dirty state, config and code hashes, seeds, environment, output hashes. Completion Log row added.

## 6. Escalate immediately if

- **Every candidate headline quantity is as unstable as ΔQALY.** That is a real possible outcome and it changes the paper: it would mean the honest conclusion is *"a precise answer is not currently available, and here is what would resolve it"*. Report it plainly and at the same prominence as any favourable finding. Do not go looking for a more flattering summary statistic.
- The threshold price is well-defined at some switch levels and undefined at others — report which and why, rather than restricting to the levels where it behaves.
- Reusing A-15's runs turns out to change any epidemiological figure it already reported. That would mean the runs are not reproducible and is a separate problem.

## 7. Do not

- Do not change any model parameter, price, discount rate, threshold or config to make a result more interpretable.
- Do not select a headline quantity because it is more favourable to lenacapavir. The selection criterion is stability across seeds, stated in §4 before the results.
- Do not present these numbers as a result. See §2.
