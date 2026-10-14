# Task brief — A-5: measure V_ensemble for the history-matching variance budget

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Type:** measurement + a recommendation. **Implements nothing in production** except, if accepted, a data file the register consumes.
**Blocks:** the history-matching wave. Decided by Rishi 2026-09-14 to be measured rather than assumed.

---

## 1. What this is and why it matters

History matching rules a parameter vector out when implausibility exceeds a cutoff:

```
I(x) = |E[f(x)] - z| / sqrt(V_obs + V_code + V_ensemble + V_disc)
```

`V_ensemble` is the simulator's own stochastic variability. **It is currently 0.0 for every
target.** `run_history_matching.py:387` never passes the optional `v_ensemble=`, so
`hm_target_register.py:501` falls through to `v_ensemble_unavailable`
(`hm_variance_budget.py:387-402`).

The register states the consequence in its own defaults block, verbatim:

> "Contributes 0.0, which shrinks the denominator and biases towards OVER-exclusion."

Zero is not a neutral choice. It is wrong for a stochastic simulator, and the wave will be run
**stochastically** (decided 2026-09-14), so the term is live rather than hypothetical. B-3's
prototype found NROY = 0 of 4,000 with this denominator; an unknown part of that is this gap.

**The purpose of this task is to establish the value by measurement, not to choose one that
produces a convenient NROY.** §1A.4 applies with full force: you must not select a seed count,
estimator, or smoothing choice because of what it does to NROY size.

---

## 2. Do not use

- **`outputs/hta_results/probe_diagnostics_20260906/raw/seed_variability_summary.csv`.** The
  register rejects it by name: it holds QALY / cost / infections-averted outcomes at n=15 on
  **pre-fix code** and carries no epidemiological series. It is the wrong quantity on a model
  that no longer exists.
- Any figure from A-16, A-24 or A-28. Those are HTA outcome metrics, not target series.

---

## 3. Code state this must run against

The model changed materially on 2026-09-14. Measure on the **current** tree, and record the
config hash and module hashes in your manifest:

1. **C-1** exogenous non-MSM forcing is live.
2. **A-29** — the deterministic transmission allocator is now blocked per orientation.
3. **C-1 call-site move** — exogenous diagnoses are now injected inside
   `_step_diagnosis_and_exogenous`, before ART transitions.

A measurement taken on an earlier tree is not usable.

---

## 4. What to measure

For **every series in `configs/hm_target_register.json`** (11 series, 319 scalar targets), and for
**every year that series declares**, estimate the variance of the model output across stochastic
replicates at a fixed parameter vector.

Base config: `configs/V13.22_20260604_nearfit_rank001_basecase.json`, run **stochastically**
(`run_control.deterministic_mode = false`), full window.

### 4.1 The stability analysis — this is the core of the task

A variance estimated from few replicates is itself very noisy: the relative standard error of a
sample variance is roughly `sqrt(2/(n-1))`, so n=15 gives about 38% and n=200 about 10%. **Do not
pick a seed count and report the number it gives.** Instead:

- Run a seed ladder and report how the estimate stabilises: for example n = 10, 20, 40, 80, and as
  far beyond as the machine allows.
- For a representative set of targets, plot or tabulate the estimate against n, with a bootstrap
  interval at each n.
- **State the n at which the estimate stops moving materially, and justify the threshold you used
  to call it stable in advance of looking at the answer.**
- If the budget does not reach stability, say so and report the estimate with its uncertainty
  rather than presenting a provisional number as settled.

### 4.2 Empirical versus parametric

Report both, and recommend one with reasons:

- **Empirical:** per-series, per-year sample variance.
- **Parametric:** these are counting processes, so a mean-variance relationship (Poisson-like
  `V = mean`, or over-dispersed `V = phi * mean`) may be far more stable than 319 independent
  per-year estimates, especially in early years where counts are small.

The parametric form is not automatically better. Test whether it holds: plot variance against
mean across targets and years, and report the fitted relationship with its residuals. If the data
reject it, say so.

### 4.3 The replicate-count question

Be explicit about what the wave will consume. If the wave averages `k` replicates per parameter
point, the variance of that **mean** is `V/k`, not `V`. Report:

- `V` — per-run output variance
- the implied `V/k` for k = 1, 3, 5, 10

and state which the register field expects, citing `hm_variance_budget.py`. Getting this wrong by
a factor of k is the most likely way this task produces a wrong answer that looks right.

### 4.4 Does it actually matter

For each target, report `V_ensemble` **as a fraction of the current `V_disc`** (10% of that wave's
own output-series variance, per Andrianakis et al. 2015). This is the decision-relevant number: if
`V_ensemble` is a few percent of `V_disc`, the gap was immaterial and the wave can proceed either
way. If it is comparable or larger, the current denominator is materially wrong.

Note that all 92 tier-1 budgets are currently **100% `V_disc`**, because `V_obs` is underivable for
five series and ECDC publishes no uncertainty at all. So `V_ensemble` and `V_disc` are, in
practice, the whole denominator.

---

## 5. Parameter dependence

Variance may depend on where in parameter space you stand. Measure at rank001, then repeat at
**two** further parameter vectors drawn from the prior box, and report whether the estimate moves
materially. If it does, say so plainly: a single global `V_ensemble` would then be an
approximation, and the report should recommend how to handle that rather than hiding it.

---

## 6. Hard constraints

- **Do not run a calibration wave.** No ABC-SMC, SNPE, LHS, or `run_history_matching.py`. Replicate
  simulation runs only.
- **Never rebaseline golden CRN vectors.**
- **Addendum only.** Do not edit or refresh any existing reference artefact or prior report. Write
  new files. The one permitted production-adjacent output is a new data file plus a proposed
  register patch, presented for Rishi to approve, **not applied**.
- **§1A.4 — no tuning.** Never choose a seed count, estimator, smoothing, or aggregation because of
  its effect on NROY size or on any gate. Fix your method before you look at the consequence, and
  say in the report that you did.
- **§1A.3 — cite at source.** Quote file:line for every claim about the codebase.
- **Memory.** A 30-seed run exhausted swap on this machine (21.3 GB of 22.5 GB, ~70 MB free RAM).
  **Run replicates sequentially, free results between runs, and write per-seed output to disk
  rather than accumulating in memory.** If you approach the limit, reduce the ladder and report the
  reduction rather than aborting silently.
- **Environment.** Project root `/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis ` — note
  the **trailing space**; quote all paths. Python must be `hiv-prep-model/.venv/bin/python`.

---

## 7. Deliverable

New directory `outputs/hta_results/a5_ensemble_variance_<timestamp>/` with `scripts/`, `raw/`,
`REPORT.md`, and a `manifest.json` recording config hash, module hashes, seeds and code state.

The report must state:

1. The recommended `V_ensemble`, per series and year, with the estimator used and why.
2. The stability evidence from §4.1, including the n at which it settles and the threshold you
   pre-committed to.
3. Empirical versus parametric comparison, with a recommendation.
4. The `V/k` table from §4.3 and which form the register expects, cited.
5. The `V_ensemble / V_disc` ratio per target, and a plain statement of whether the current 0.0 is
   materially wrong or merely formally wrong.
6. Parameter dependence from §5.
7. A proposed patch to `configs/hm_target_register.json`, written out but **not applied**.

If the honest answer is "it is immaterial and the wave can run with 0.0 provided that is
disclosed", that is a valid and useful result. Report what you measure.
