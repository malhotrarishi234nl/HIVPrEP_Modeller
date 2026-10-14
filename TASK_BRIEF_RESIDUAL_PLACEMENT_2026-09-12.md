# Task brief — A-17: the residual placement rule

**Created:** 2026-09-12
**Owner:** Rishi Malhotra
**Type:** diagnostic, ending in a recommendation. **Implements nothing in production.**
**Follows:** D-14 synthesis memo, Recommendation 3 — two tasks independently arrived at the residual placement rule as the remaining suspect, and both labelled it *unsupported*.

---

## 0. Read this before deciding to run it

**This task serves the calibration, not the headline.** If D-14 Recommendation 1 is adopted, the base-case results move to the CRN-paired stochastic mean and the deterministic allocator stops touching the headline numbers. What it still touches is **calibration**, which runs deterministically thousands of times per wave and which B-8 shows runs a 17–30% larger epidemic than the model specifies.

So: **run A-17 if the paper needs the calibration to stand up. Skip it if the plan is to document the bias and report a range.** It is not on the critical path for the A-16 result, which is robust to it.

## 1. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding verification and academic-integrity standard.
2. `STATE_OF_PLAY_2026-09-11.md` §1 and §2 — the two-problem split and the ENG-01/A-13 dial.
3. `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md`
4. `outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/` — the gap this task is trying to explain.
5. `stochastic_allocation.py` — 60 lines. Read all of it.

---

## 2. The suspect, stated precisely

`deterministic_allocate_events(n, p)` floors each cell's expectation, then awards the residual events to the cells with the **largest fractional expectation**, subject to capacity.

It preserves `round(Σ expected)` **exactly**. That is why every aggregate check in this project has passed. But preserving the total says nothing about *which cells* receive the residual, and the ranking is not neutral with respect to cell size.

**The hypothesis to test — currently unsupported, do not assume it:**

> Largest-remainder placement systematically favours the larger cells. In a multiplicative system — an epidemic, where a cell that receives an event generates more onward events next step — a placement bias that is merely redistributive at one timestep **compounds into a level bias** over many steps. That would explain B-8's signature: a gap established during the 1980s growth phase, HIV-compartment-specific rather than population-wide, eroding in ratio terms after saturation, and untouched by any stratification because it concerns which cell *within* a stratum receives the event.

### Preliminary evidence (reproduced during the 2026-09-11 review, not yet independently confirmed)

One call, one scalar `p`, two strata — 600 large-count cells and 600 small-count cells: the **small-count stratum was under-allocated by 9.2%** while the aggregate was preserved to within one event. Per-stratum allocation was exact to 0.1 events. Treat this as a starting point, not a result.

---

## 3. Method — four steps, in order, each cheap

### Step 1 — Locate the gap in time

**The single most informative missing measurement.** Nothing currently establishes *when* the B-8 level gap opens.

Extract a stochastic ensemble from **1975** rather than 1990 (the current extraction window starts at the first scored year, which is why this has never been visible), and compare against the deterministic trajectory over the same window. Use `tools/run_a5a_epidemiological_seed_variability.py` with a widened extraction window; n = 50, seeds 7001–7050, config `configs/V13.22_20260604_nearfit_rank001_basecase.json`.

**Report:** the year-by-year log ratio ln(deterministic / stochastic mean) from 1975, and the year the gap first exceeds the stochastic ensemble's own range.

**This step alone can falsify the hypothesis.** If the gap is already present at or near seeding, it is not a growth-phase compounding effect and steps 2–4 are aimed at the wrong thing — say so and stop.

### Step 2 — Characterise the placement bias, with no model at all

A pure property of the allocator; runs in seconds.

Over a wide range of `(n vector, p)` regimes — varying cell-count, the spread of `n` across cells, and `p` from rare (1e-4) to common (0.5) — measure per cell:

- `allocated_i − n_i·p`, and whether that error **correlates with `n_i`**;
- the same quantity aggregated by size decile;
- whether the sign of the correlation is stable across regimes.

**Include the regime that matters:** many cells, highly skewed `n`, small `p` — the epidemic's actual shape.

### Step 3 — Does it compound? Test candidates on a synthetic multiplicative process

Still no epidemic model. Build a minimal multiplicative process — a set of cells, each growing by its own count times a rate, with events allocated each step — and iterate it for a few hundred steps. Compare the total trajectory under each allocation rule against the **exact fractional (unrounded) trajectory**, which is the unbiased reference.

**Candidates to compare, at minimum:**

| rule | note |
|---|---|
| **largest remainder** | current production behaviour, the control |
| **stochastic rounding** | cell `i` receives +1 with probability equal to its fractional part. **Unbiased per cell by construction**, and can be made fully reproducible by keying the uniform on `(seed, year, timestep, process, cell)` exactly as `counter_based_rng.py` already does. |
| **rotating / systematic placement** | deterministic, but the residual offset advances each step so the placement bias averages out over time |
| **largest remainder with a size-independent tie-break** | isolates whether the bias is the ranking itself or the size correlation |
| **fully cellwise rounding** | the ENG-01 control. It should fail. If it does not, something is wrong with the harness. |

**Report the divergence from the exact fractional trajectory over time for each rule.**

### Step 4 — Does the best candidate close B-8's gap?

Implement the leading candidate as an **opt-in diagnostic variant** — production default unchanged — and re-run the 1975–2025 trajectory. Compare against the stochastic mean from Step 1.

**This is the payoff test.** If the gap closes, the mechanism is confirmed and the repair is identified. If it does not, the mechanism is elsewhere and that is the finding.

---

## 4. Pre-specify before running, in writing, in the report

- The success criterion for Step 4. Proposed: the median absolute log ratio across scored years falls **below half** its current value, and the deterministic trajectory sits within the stochastic ensemble range in a majority of years. State and justify any departure **before** you see results.
- The selection criterion among candidates. **It is demonstrated unbiasedness in Steps 2–3, not whichever produces the nicest epidemic or the best calibration fit.** A candidate that closes the B-8 gap but is shown biased in Step 2 is not the answer.

---

## 5. Two constraints any candidate must satisfy

1. **Reproducibility is non-negotiable.** The whole reason deterministic mode exists is that it gives the same answer every run. Any candidate must be **bit-reproducible at a fixed seed** — including stochastic rounding, which must draw from the counter-based generator, never from process-level randomness. Verify this explicitly with a repeat-run hash check.
2. **It must not reintroduce ENG-01.** Any per-cell rule has to be checked for rare-event erasure: confirm that across many cells with small expectations, the expected total is preserved and not floored away. This is exactly the failure at the other end of the dial.

---

## 6. A likely outcome worth naming in advance

**Stochastic rounding is unbiased per cell, and therefore unbiased at every grouping.** If it wins, **D-14's granularity question dissolves** — there is no dial to tune, because the rule is unbiased whether you call it once per tensor or once per cell. That would resolve ENG-01 and A-13 simultaneously, which is a strong point in its favour and should be stated if the evidence supports it.

The honest cost, to be reported alongside: the allocated total would no longer be exactly `round(Σ expected)`; it becomes a quantity with the correct mean and some variance. "Deterministic mode" would more accurately be called **"seeded-reproducible mode"**. That is a naming and documentation change, not a defect — but it must be disclosed, not slipped in, and it has implications for how the manuscript describes the calibration.

---

## 7. Acceptance

- Step 1 delivered: the log-ratio profile from 1975, and a stated year at which the gap opens — or a statement that it is present from seeding.
- Step 2 delivered: the size-correlation of placement error, across regimes, with the epidemic-shaped regime included.
- Step 3 delivered: divergence-over-time for all five rules against the exact fractional trajectory, with the ENG-01 control failing as expected.
- Step 4 delivered: whether the leading candidate closes the B-8 gap, against the pre-specified criterion.
- A recommendation with rationale — **a recommendation only. Rishi decides, and the repair is designed with A-2 and A-9.**
- Both §5 constraints demonstrated for the recommended candidate.
- `manifest.json`; Completion Log row; no production behaviour changed.

## 8. Escalate immediately if

- **Step 1 shows the gap is present at or near seeding** — the hypothesis is wrong, stop rather than proceeding through steps 2–4.
- **No candidate closes the gap** — the mechanism is not the placement rule. A real and useful outcome; report it and stop.
- **A candidate closes the gap but Step 2 shows it is biased** — report the conflict, recommend nothing, and escalate. Do not adopt a rule because it produces a better fit.
- The reproducibility check in §5.1 fails for the leading candidate.

## 9. Do not

- Do not change production allocation. Diagnostic variant only.
- Do not tune any rate, bound or threshold. If the gap only closes with a parameter change, that is not a fix and it is a §1A.4 violation.
- Do not judge candidates on ΔQALY, the ICER, or calibration fit quality.
