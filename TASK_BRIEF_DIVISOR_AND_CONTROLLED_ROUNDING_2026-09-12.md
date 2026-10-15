# Task brief — A-19: the two allocation rules A-17 left open

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-12
**Owner:** Rishi Malhotra
**Type:** diagnostic screen, ending in a recommendation. **Implements nothing in production.**
**Follows:** `ROUNDING_METHOD_LITERATURE_COMPARISON_2026-09-12.md` §4 — "Genuinely open and worth
testing: Sainte-Laguë/Webster, and controlled rounding. Those two are the memo's remaining value."
**Method mandated by:** that memo's §5 — "Screen cheaply first" on A-17's synthetic harness, before
any model run. This brief is the §5 step-5 pre-commitment, written before the harness was extended
and before any candidate was run.

---

## 0. Step 0 — which defect is this aimed at

Mandated by the memo's own added step 0. **This task targets Defect 1 only (A-13, compositional
relocation).** It is not aimed at Defect 2 (B-8 level inflation — A-18 owns that, and A-17 showed
allocation is the wrong layer) and not at Defect 3 (A-9 non-monotonicity — A-17 proved from A-15's
level-4 cellwise grid that no placement rule can touch it).

**If a candidate here looks like it improves the B-8 gap, that is not a reason to adopt it and it is
not what this screen measures.** No epidemic model is run.

---

## 1. What A-17 already settled — not re-run here

Largest-remainder's rare-event behaviour; keyed stochastic rounding's unbiasedness, ENG-01 safety and
fixed-seed reproducibility; rotating/systematic placement (rejected, −1.3086%); fully cellwise (fails,
as the control should); and that the fractional ranking, not the tie-break, is the operative mechanism.

The surviving, worth-fixing finding is the one this task acts on: at p=1e-4 in the epidemic-shaped
regime, largest remainder gave the **bottom seven size-deciles zero events** while deciles 8–10 took
+57.5%/+17.6%/+2.8%.

---

## 2. Candidates

| # | rule | status in the memo | why it is here |
|---|---|---|---|
| 1 | **Sainte-Laguë / Webster** | "best remaining unexplored candidate" | least-biased divisor method; trades quota for consistency; zero added randomness |
| 2 | **Controlled rounding (Cox–Ernst, zero-restricted, 2-D)** | "Yes, by construction" — untested | the only deterministic candidate that constrains *composition* rather than only the grand total |
| 3 | **Jefferson / D'Hondt** | "deprioritise; near-certain failure" | **pre-registered control.** A named prediction that is cheap to test is worth testing, precisely so the memo's dismissal is evidenced rather than asserted |

---

## 3. A harness limitation that must be fixed first, and stated

**A-17's synthetic harness is one-dimensional** — 120 independent cells under a single grand-total
constraint. **Controlled rounding cannot be screened on it.** In one dimension, "preserve the total
and keep every cell within one unit of its expectation" *is* the largest-remainder guarantee, so
controlled rounding degenerates to the incumbent and the comparison would be vacuous — a null result
that measured nothing.

So A-19 extends the harness to a **2-D cell layout with row and column margins**, which is what
controlled rounding actually constrains and what "compositional relocation" actually means.

**Validity gate, run first.** The 120 cells are reshaped into a 12×10 grid *in the original order*,
with identical initial values and identical rates, so every 1-D rule must be bit-identical to A-17.
**Before any new candidate is read, the harness must reproduce A-17's four published figures:**

| rule | required final relative divergence |
|---|---|
| largest remainder | −0.1277% |
| stochastic rounding (500-seed mean) | +0.00093% |
| rotating systematic | −1.3086% |
| fully cellwise | −1.5372% |

**If any of these does not reproduce, stop and report a harness defect.** Nothing downstream is
interpretable.

---

## 4. Pre-specified acceptance criteria — committed before any candidate was run

A candidate is **recommended for the A-14/A-15 switch grid** only if it passes all three.

**C1 — Defect 1, the criterion that matters.** In the epidemic-shaped rare-event regime (1,200 cells:
600 at n=9,900 and 600 at n=100; p=1e-4), where largest remainder gives the small stratum −100%:
- **(a)** no size decile with expected events ≥ 1 may receive **zero**, and
- **(b)** the worst decile relative error must satisfy |error| < 0.10.

**C2 — drift.** |final relative divergence from the exact fractional trajectory over 300 steps| must be
**no worse than the incumbent's 0.1277%**.

**C3 — reproducibility.** Two runs at fixed configuration must produce identical output hashes. All
three candidates are deterministic, so this should be trivially true; a failure means a defect.

**Tie-break between passing candidates:** lower row/column **margin divergence** on the 2-D grid, then
lower runtime. **Not** epidemic realism, fit quality, ΔQALY or the ICER — none of which is computed here.

### 4.1 Predictions registered in advance

Stated before running, so that confirmation cannot be read as hindsight:

1. **D'Hondt fails C1 at least as badly as largest remainder.** It rounds down, so it favours large
   cells more strongly. The memo predicts this; this screen either evidences it or withdraws it.
2. **Sainte-Laguë fails C1 too, and for a reason that generalises.** When every cell's expectation is
   below 1 and the total is T, any size-monotone deterministic rule must hand all T units to the T
   largest expectations. Sainte-Laguë is size-monotone. **If this prediction holds, it is the more
   important result than the measurement**, because it rules out the entire deterministic
   quota-and-divisor family at once, rather than one member of it.
3. **Controlled rounding passes C1, but only at the granularity of the margins it is given.** It should
   protect a small-cell stratum that forms its own row, and should *not* protect one that is scattered
   across rows. Both orientations are therefore tested, including an **interleaved layout where the
   size strata are aligned with no margin at all** — the honest control against over-claiming.

---

## 5. Acceptance

- Validity gate result, reported first, pass or fail.
- C1/C2/C3 for all three candidates, against the thresholds above.
- The interleaved-layout result for controlled rounding, reported whether or not it flatters it.
- Runtime for each rule.
- A recommendation with rationale — **a recommendation only. Rishi decides.**
- `manifest.json`, output hashes, unit tests; **no production behaviour changed.**

## 6. Escalate immediately if

- The validity gate fails.
- A candidate passes C1 but is shown to have acquired drift (C2 failure) — report the conflict,
  recommend nothing.
- The controlled-rounding LP returns a non-integral solution — the total-unimodularity argument it
  relies on would be wrong, and the method would need a different solver, not a rounded answer.

## 7. Do not

- Do not change production allocation. Diagnostic module only.
- Do not run the epidemic model. This is the cheap screen that the memo says must come first.
- Do not judge a candidate on anything downstream of allocation.
