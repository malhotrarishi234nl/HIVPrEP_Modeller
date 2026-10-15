# Task brief — A-18: the Jensen hypothesis, and light probes of the three remedies

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-12
**Owner:** Rishi Malhotra
**Type:** diagnostic + three cheap remedy probes. **Implements nothing in production.**
**Follows:** A-17, which refuted the residual-placement hypothesis and redirected at the upstream deterministic transmission shortcut.

---

## 1. What is actually being claimed — and what is and is not a "modelling choice"

**Jensen's inequality is not a modelling choice. It is a mathematical fact** about concave functions: `E[f(X)] ≤ f(E[X])`.

**The modelling choice was to run the simulator deterministically** — substituting expected values for random draws. That choice is what exposes the model to Jensen. It was made for speed and reproducibility, it is written down, and **it is reversible.** The remedies in §5 are therefore genuine options, not mitigations of an immovable constraint.

### The hypothesis — UNVERIFIED, this task tests it

Per-person infection probability given `n` contacts is

```
f(n) = 1 − (1−β)^n          f''(n) = −(1−β)^n · [ln(1−β)]²  <  0   →  f is CONCAVE in n
```

Deterministic mode computes `f(E[n])`. The stochastic model computes `E[f(n)]`. By Jensen, for concave `f`, **`f(E[n]) ≥ E[f(n)]` — deterministic systematically overstates transmission**, by an amount that grows with `Var(n)`.

### Why it is the leading candidate

It matches every element of B-8's measured signature, and A-17's Step 1 supplied the decisive evidence — `raw/step1_yearly_log_ratio.csv`:

| observation | Jensen predicts |
|---|---|
| Deterministic **above** stochastic (+17–30%) | ✓ concave ⇒ overstatement |
| Confined to the **HIV compartment** (+14.6% HIV stock, +0.004% total population) | ✓ only transmission has this nonlinearity; births and deaths are linear |
| Log ratio rises **almost linearly, +0.0062 per quarterly step**, 1975 → 1990 | ✓ constant per-step multiplicative bias compounding through exponential growth |
| **Plateaus at 1990** (+0.312) then slowly declines | ✓ accumulation stops when growth stops |
| **Untouched by every allocation change tested** (A-13, A-14, A-15, A-17) | ✓ it is upstream of allocation entirely |
| Magnitude | ✓ ~0.6%/step over ~60 growth-phase steps ⇒ ~1.45×, against an observed peak of 1.37× |

**Six for six. That is why it is worth one test — and exactly why it must be tested rather than adopted.** A hypothesis that explains everything is the kind most in need of a falsification attempt.

---

## 2. Read first

1. `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding standard. §1A.4 (tuning prohibition) is critical for §5.2.
2. `outputs/calibration/a17_residual_placement_20260912_173211/A17_RESIDUAL_PLACEMENT.md` — especially Step 1 and its `raw/step1_yearly_log_ratio.csv`.
3. `outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/` — the gap.
4. `transmission_corrected.py` — where the deterministic/stochastic fork in transmission actually lives.

---

## 3. Step 1 — the isolation test. This is decisive; do it first and stop if it fails

**Method.** Run a hybrid: **contact counts drawn stochastically, everything else deterministic** (progression, diagnosis, ART, mortality, allocation all unchanged). Compare its 1975–2025 trajectory against (a) fully deterministic and (b) the n=50 stochastic ensemble.

This isolates the Jensen channel cleanly. If substituting drawn contacts for expected contacts closes most of the gap while nothing else changes, the mechanism is confirmed.

**Pre-specify before running, in the report:** the success criterion. Proposed — **the median absolute log ratio across the 222 scored target-years falls by at least half** (baseline 0.18838, so below 0.09419), matching the criterion A-17 used so the two are comparable. State and justify any departure before seeing results.

> **[added after A-19] Criterion resolution — check this for every threshold you commit to.** A-19 produced two pre-registered criteria that could not certify their own winners because the threshold sat below the resolution of the estimate. **This one is resolvable, and here is the number:** the median standard error of the per-year log ratio is **0.02058** (computed from `a17_residual_placement_.../raw/step1_yearly_log_ratio.csv` as `stochastic_se / stochastic_mean`, n=50 replicates). The 0.09419 threshold is therefore **4.6× the median SE** — comfortably discriminating. **Restate this check in the report**, and if you introduce any further threshold, state its resolution alongside it. A threshold finer than the estimate's standard error cannot discriminate, and pre-committing it does not make it informative.

**A second, even cheaper check worth running alongside:** compute `f(E[n]) − E[f(n)]` directly at each timestep from the retained traces, using the model's own contact distribution, and test whether it accounts for ~0.6% per step. If the arithmetic does not produce a bias of the right order, the hypothesis is wrong regardless of what the hybrid run shows.

> **[added after A-19] Independent corroboration that the mechanism is NOT allocation.** A-19 showed that over 300 steps largest-remainder freezes the smallest cells at **1.8% of their exact total** — they never win a residual, so they stay at their initial values. Deterministic allocation therefore pushes rare-stratum levels **DOWN**. B-8's gap is an **inflation**. So allocation is not merely too small to explain it (A-17) — it acts in the **wrong direction** on the very strata most at risk. That is a second, independent reason to look upstream, and it strengthens the case for testing Jensen.

**If Step 1 fails: stop. Report it, do not proceed to §4 or §5.** Two mechanisms have now been proposed and one refuted; a third failure means the cause is somewhere nobody has looked, and guessing again is not the answer.

---

## 4. Step 2 — quantify it, if Step 1 confirms

- The per-step bias in the force of infection, as a distribution across timesteps and strata.
- Its dependence on `Var(n)` — the prediction is that the bias scales with contact-count variance, so strata with more heterogeneous contact rates should show more.
- How much of B-8's 222-target-year gap the measured per-step bias accounts for, cumulatively.

---

## 5. Step 3 — light probes of the three remedies

**All three are deliberately cheap.** None is an implementation; each answers "is this worth building?"

### 5.1 Remedy A — run calibration stochastically

**Light probe, no new model runs.** Both trajectories already exist for the near-fit config: the deterministic one, and A-5a's n=50 stochastic ensemble — **use `outputs/calibration/a5a_epidemiological_seed_variability_20260911_042330/`. The `20260910_162000` run is PRE-CRN and superseded**; its means differ by <1% but its variances are ~35% larger, and mixing the two would be a silent error. Score the **same implausibility calculation** at that one parameter point, twice — once using the deterministic output, once using the stochastic mean.

**Answers:** does removing the level inflation materially reduce implausibility against the tier-1 targets? Since the model currently **overshoots** its AIDS and incidence targets, removing a +17–30% inflation should move it toward them — but that is a prediction, not a result.

**Also report** the compute arithmetic for the real thing: at n=15 per design point a 4,000-point wave is ~1,000 core-hours, i.e. ~10 hours on 96 vCPUs. Affordable. State it so the decision is informed.

**The counter-argument that must be reported alongside:** averaging n seeds tightens the implausibility denominator by √n, which excludes *more*. The two effects pull opposite ways and the probe should quantify both, not just the favourable one.

> **[added after A-19] A third consideration that only became visible once A-19 ran, and it favours this remedy.** Stochastic mode has **no allocation step at all** for disease events — it draws directly. So moving calibration to stochastic would resolve **Defect 1 (A-13) for free**, alongside whatever it does for Defect 2. A-19 established that Defect 1 is worse than previously understood (progressive erasure of rare strata, not one-step misplacement) and that fixing it deterministically requires giving up either determinism or size-monotonicity anyway. **Report this in the probe's verdict** — remedy A may buy two defects for one decision, which changes its cost-benefit materially.

### 5.2 Remedy B — carry the bias as a declared discrepancy term

**Light probe, pure rescoring of existing artefacts — seconds.** Add a model-discrepancy component to `V_disc` sized to the **measured** deterministic-versus-stochastic gap per target-year (available in A-17's `step1_yearly_log_ratio.csv` and B-8's outputs), then re-score the existing 4,000 particles from `outputs/calibration/hm_prototype_20260910/`.

> **⚠ §1A.4 BOUNDARY — read this twice.** Adding a discrepancy term until NROY becomes non-empty is precisely what the tuning prohibition forbids. What makes this legitimate is that the term is **derived from a measurement that already exists**, computed before rescoring, and **not adjusted afterwards for any reason.**
>
> Required procedure: (1) compute the term from the measured gap and **write the number in the report**; (2) rescore once; (3) report whatever NROY results. **If NROY is still 0, that is the answer.** Do not scale, widen, or re-derive the term to improve it. If you find yourself wanting to, stop and escalate.

**Note:** this remedy is **mechanism-agnostic** — the inflation is measured whether or not Jensen explains it. So 5.2 is worth running even if Step 1 fails, and is the one probe exempt from the §3 stop rule.

### 5.3 Remedy C — a second-order (Jensen) correction

**Light probe: one diagnostic run.** Add the second-order term `½·f''(μ)·σ²` to the deterministic force of infection as an **opt-in diagnostic variant**, production default unchanged. Run 1975–2025 once and compare the log-ratio profile against baseline and against the n=50 stochastic mean.

**Why this one is the most interesting outcome if it works:** it would keep deterministic mode's speed and exact reproducibility *and* remove the bias — no stochastic calibration, no discrepancy term, no compute bill. That makes it the remedy most worth a careful look, and equally the one most likely to be adopted on insufficient evidence.

**Report honestly, whatever happens:** the correction needs `σ²` of the contact distribution, which must come from the model's own parameterisation and **must not be fitted**. If `σ²` is not available without an assumption, say so — that is a blocker, not a value to supply. A second-order correction is also only an approximation; report the residual gap, not just the improvement.

---

## 6. Acceptance

- Step 1 delivered with its criterion stated **above** the results, plus the direct `f(E[n]) − E[f(n)]` arithmetic check.
- If confirmed: Step 2's per-step bias, its `Var(n)` dependence, and the cumulative share of B-8's gap it explains.
- Three remedy probes, each with a one-line verdict: **worth building / not worth building / blocked**, and why.
- For 5.2: the derived discrepancy term written down **before** the rescoring result.
- For 5.1: both directions of the effect quantified, not only the favourable one.
- `manifest.json`, Completion Log row, no production behaviour changed.

## 7. Escalate immediately if

- **Step 1 does not close the gap.** Third proposed mechanism, second refutation — stop and report rather than proposing a fourth. **[after A-19] the remaining hypothesis space is now materially narrower**: A-17 eliminated residual placement, and A-19 closed the entire deterministic apportionment family by proof. If transmission nonlinearity is also eliminated, the cause is somewhere nobody has yet looked, and the right response is to say so plainly rather than to guess again.
- **The direct arithmetic check disagrees with the hybrid run.** They should agree; if they do not, one of them is wrong and that matters more than the hypothesis.
- **Remedy B's derived term makes NROY non-empty only at a magnitude larger than the measured gap.** Report the required magnitude and stop. Do not adopt it.
- **Remedy C requires an assumed `σ²`.** A missing value is a blocker, not a number to supply.

## 8. Do not

- Do not change production transmission, allocation, or any parameter. Diagnostic variants only.
- Do not adopt a remedy in this task. Each probe answers *whether it is worth building*; building it is a separate, reviewed design with A-2 and A-9.
- Do not describe the inflation as a "bug" unless Step 1 confirms a defect. If Jensen is the mechanism, it is the **known cost of a declared approximation** — a disclosure item and a design decision, not an error.
