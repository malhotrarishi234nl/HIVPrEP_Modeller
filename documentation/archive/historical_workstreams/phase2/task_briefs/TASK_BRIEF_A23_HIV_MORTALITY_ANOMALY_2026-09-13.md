# Task brief — A-23: why does stochastic HIV mortality inflate the model by 122%?

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-13 · **Owner:** Rishi Malhotra
**Type:** measurement. **Implements nothing in production.**
**Follows:** A-20 (found it), A-21 (eliminated the obvious explanation).
**Batching:** independent of A-22 and A-9; all three reuse the A-20 harness and can run in parallel.

---

## 1. The anomaly

A-20 ran twelve process arms. **The largest single effect in the whole sweep was not the winner.** It
was HIV mortality:

| arm | median \|log ratio\| | displacement | in median SE | median % vs stochastic |
|---|---:|---:|---:|---:|
| background mortality (A-20's winner) | 0.04901 | +0.13937 | +6.76 | +2.12% |
| **HIV mortality** | **0.79953** | **−0.61115** | **−29.65** | **+122.45%** |

Drawing HIV-specific deaths stochastically, with every other process deterministic, leaves the model
**more than twice** the fully stochastic reference — four times the winner's magnitude, in the wrong
direction.

**A-21 eliminated the obvious explanation.** At the HIV-mortality calls the deterministic aggregate is
almost exactly right: 10,282 allocated against 10,301.475 expected, an under-allocation of **0.1890%**.
Three orders of magnitude too small to produce a +122% level response by event-count arithmetic.

**But A-21 found something underneath it**, and did not pursue it. The aggregate is near-exact while the
*composition* moves:

| stage | ART | allocation / expected | **sub-unit** allocation / expected |
|---|---|---:|---:|
| AIDS | off ART | 99.985% | 96.765% |
| AIDS | on ART | 99.607% | **101.716%** |
| Chronic | off ART | 97.643% | 97.643% |
| Chronic | **on ART** | **92.171%** | **92.171%** |

Sub-unit AIDS-on-ART cells are **over**-allocated; chronic-on-ART cells are **under**-allocated by
nearly 8%. **A near-exact aggregate concealing cross-stage redistribution is exactly A-13's finding**
(cross-stage residual assignment in the diagnosis/ART calls), now visible at the mortality call.

**This is a lead, not an explanation.** Whether an 8% misallocation between two stages can produce a
122% level response over 50 years is unknown and is what A-23 measures.

---

## 2. Why it matters even though the base case is stochastic

Two reasons, and the second is the important one.

1. Deterministic HIV mortality is **wrong in a way nobody can yet characterise**. A-13 already found a
   12.4-SE deterministic/stochastic gap at this process and localised it to cross-stage residual
   assignment. A-20 found a second, larger symptom. Two independent symptoms at one process.
2. **It is the one place where a deterministic/stochastic difference could plausibly move a health
   outcome rather than a level.** HIV deaths feed life-years, and life-years are what A-2 shows your
   ΔQALY is actually tracking (r = +0.998 with person-years, +0.12 with infections averted). **If this
   process is misallocating deaths across stages, it is misallocating survival — and survival is the
   estimand.** A-23 does not compute economics, but it determines whether A-2 has a mechanical cause.

---

## 3. Read first

1. `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A.
2. `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md` — **the direct predecessor.** A-13 localised a deterministic defect at this process from a different symptom.
3. `outputs/calibration/a21_rare_stratum_starvation_20260913_001500/` — §3 and its `raw/hiv_mortality_stage_art_summary.csv`, `raw/hiv_mortality_cell_trace.csv.gz` (881,280 rows, already written — **start from these, do not re-trace what exists**).
4. `outputs/calibration/a20_process_decomposition_20260912_221520/` — the arm and its replicates.
5. `run_v3.py:_step_hiv_mortality` (line 2584) and its two `_draw_events_for` calls (2618, 2627).

---

## 4. Step 1 — characterise the arm before explaining it. No new runs.

A-20 retained 50 replicates of this arm and A-21 retained a full cell-level trace. **Exhaust them
first.** Answer, from existing files only:

1. **When does it open?** Per-year log ratio of the HIV-mortality arm against both the deterministic run
   and the stochastic reference, 1975–2025. A-20's winner opened in 1975; where does this one?
2. **Is it a level shift or a distribution effect?** Per-year min / quartiles / median / max / CV across
   the 50 seeds, and `(mean − median) / mean`. A-21's review applied this test to `hybrid_transmission`
   and ruled out fade-out in one table; do the same here.
3. **Which metric carries it?** All seven series, not just PLHIV. If PLHIV is up 122% but `aids_deaths`
   is down, that says something different from both being up.
4. **Which direction do deaths move?** Total HIV deaths in the arm vs deterministic vs stochastic
   reference, cumulatively and by era. **If the arm produces fewer deaths, the 122% is explained by
   people not dying; if it produces the same or more, it is not, and the answer is downstream.**
5. **Does it contradict A-13?** A-13 found deterministic *understates* HIV mortality (−41 vs −100 HIV
   deaths, 12.4 SE). A-20's arm implies the opposite direction. **A-13 measured a between-arm difference
   and A-20 a level, so they are not strictly comparable — say which quantity each measured before
   drawing any conclusion.** Resolving this sign is a required output of Step 1.

**Stop rule.** If Step 1 shows the arm produces materially fewer HIV deaths, the anomaly is an event-count
effect after all and Step 2 becomes a search for where those deaths went. If it produces the same number,
skip to Step 3.

---

## 5. Step 2 — the cross-stage redistribution, from A-21's existing trace

Using `raw/hiv_mortality_cell_trace.csv.gz` (no new run):

- Expected vs allocated per **(stage × ART status × year)** — A-21 gave cumulative totals only; produce the time series.
- The **sub-unit share** and **zero-allocation share** per stratum per year. A-21 reports 95.702% sub-unit and 87.504% zero-allocation overall; which strata carry that, and when?
- **Chronic-on-ART is the outlier at 92.171%.** Is that stable over time or concentrated in one era?
- **Whose deaths move where?** Deaths not taken from chronic-on-ART are, by conservation, taken from some other cell in the same allocator call. Identify the recipient strata explicitly. This is the A-21 relocation identity applied within the HIV-positive block.

**Report the identity, as A-21 did:** per call, `Σ allocated` vs `round(Σ expected)` must hold exactly;
per stratum, shortfalls and excesses must net to the rounding residual.

---

## 6. Step 3 — only if Steps 1–2 do not explain it

A misallocation of deaths between chronic-on-ART and AIDS strata changes **who is alive and in which
stage**, and stage governs onward transmission. So a small death misallocation can become a large level
effect through the transmission feedback rather than through arithmetic.

**Test:** re-run the HIV-mortality arm with transmission held at the deterministic trajectory's force of
infection (i.e. break the feedback loop). If the 122% collapses, the channel is the feedback, not the
deaths. **One new run, n=15, seeds 42–56.** Pre-specify what "collapses" means before running it, with
its resolution stated alongside.

---

## 7. Acceptance

- [ ] Steps 1 and 2 completed **with no new model runs**; every figure traced to an existing retained file.
- [ ] The A-13 / A-20 sign question resolved, with the quantity each measured named explicitly.
- [ ] Per-call and per-stratum conservation identities reported.
- [ ] Distribution test applied (the arm is or is not a fade-out / skew effect).
- [ ] Any new threshold stated **with its resolution in the same sentence** — fourth occurrence of this lesson (A-16 CV<0.50, A-19 C1, A-18, A-20).
- [ ] Full suite run; failure/error set compared exactly against the current baseline.
- [ ] `git diff` on tracked files empty, or explained.
- [ ] Limitations: one parameter point, n=50 (n=15 for Step 3), channels non-additive.

## 8. Escalate immediately if

- The conservation identity does **not** close at these call sites. That would be a different and more serious defect than the one being investigated. **Stop and report it.**
- Step 1 shows the arm's HIV deaths and the stochastic reference's agree closely while PLHIV differs by 122%. That would locate the anomaly entirely downstream and should be said plainly rather than pursued into a fourth guess.
- **Nothing explains it.** Say so. Three mechanisms were guessed and refuted for Defect 2 before A-20/A-21 measured their way to the answer; the same discipline applies here.

## 9. Do not

- Do not re-run A-21's trace — it exists, 881,280 rows, checksummed.
- Do not change any mortality rate, ART effect, or stage-transition parameter.
- Do not compute or report an ICER or any economic output.
- Do not conflate A-13's between-arm difference with A-20's level.
- Do not implement anything.
