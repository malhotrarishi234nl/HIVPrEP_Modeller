# Rounding/allocation method comparison — prep notes

> Archived under `documentation/reports/methods/`; original filename retained.

**Original status (2026-09-12, pre-A-17):** literature-derived prep notes, not model-verified.
**REVISED 2026-09-12 after A-17 ran.** A-17 tested four of the candidate rules directly. Rows that were
speculative are now measured, one whole column is now answerable from evidence, and the memo's **scope has
narrowed**. Revisions are marked **[A-17]**. Original text is preserved in intent; only claims A-17 settled
or contradicted have been changed.

**REVISED AGAIN 2026-09-12 after A-19 ran.** A-19 screened the two rules §4 named as the memo's remaining
value, plus D'Hondt as a pre-registered control
(`outputs/calibration/a19_divisor_controlled_rounding_20260912_183159/`). **Every untested row in the table
is now measured, and the memo has no open candidates left.** Revisions are marked **[A-19]**.

Sources are web literature (see §6) except where marked **[A-17]**, **[A-19]** or **[this project]**.

---

## 1. Scope — read this before using the table

**[A-17] This memo is about Defect 1 (A-13, compositional relocation). It is NOT a route to fixing Defect 2
(B-8, the +17–30% level inflation), and no method in the table below will fix Defect 3 (A-9).**

The memo was written when the D-14 synthesis memo named residual placement as the next probe for the level
inflation. **That probe has now run and was refuted** (`outputs/calibration/a17_residual_placement_20260912_173211/`):
residual placement biases in the **wrong direction** (−0.1277% *below* the exact trajectory, against a gap
that is *above*), is **~150× too small**, and replacing it with a verified-unbiased rule made the gap
**worse** (median |log ratio| 0.18838 → 0.28402).

**Defect 2 is now handled by A-18** (`TASK_BRIEF_JENSEN_MECHANISM_2026-09-12.md`) — the leading hypothesis is
Jensen's inequality on the force of infection, which is upstream of allocation entirely.

**What survives, and it is worth doing:** A-17 confirmed largest-remainder misplaces **severely in
rare-event regimes** — at p=1e-4 the bottom seven size-deciles received **zero** events while deciles 8–10
were over-allocated +57.5%/+17.6%/+2.8%. That is exactly A-13's compositional defect, and the table below is
the right survey of what to replace it with.

### [A-17] Defect 3 (A-9) cannot be fixed by any method here — provable from existing data

A-15 ran the switch grid at allocation level 4, **fully cellwise rounding, which has no residual-placement
step at all**. It is still non-monotone (`infections_averted` = −35; −42; −30; 54; 73, one descent).

**If the pathology persists when there are no remainders to place, placement is not its cause.** Every row's
"Fixes Defect 3?" entry is therefore **No**, and the original table's "Should, in principle" entries for the
divisor methods are withdrawn.

### [A-17] Correction to a citation claim in the original

The original attributed A-9's non-monotonicity to largest-remainder's failure of *consistency*
(Balinski–Young), "confirmed empirically here by A-15." **That attribution is withdrawn.** A-15 varied
*pooling granularity*, not apportionment method, and its level-4 result above **contradicts** the
attribution. Balinski–Young remains a correct and useful citation for what largest-remainder is and is not
guaranteed to do — it is simply not evidenced by A-15.

---

## 2. Comparison table

"Fixes Defect 1" = removes A-13's compositional relocation. **Defect 3 column deleted — answered "No" for
every row in §1.** "Divergence" is A-17's measured drift from the exact fractional trajectory over 300
synthetic steps, where measured.

| Method | Mechanism | Fixes Defect 1? | Measured divergence **[A-17]** | Known bias / drawback | Exactly reproducible without new RNG? | Builds on existing infra? |
|---|---|---|---|---|---|---|
| **Current — largest remainder / Hamilton** (production, level 0) | Floor every cell, hand leftovers to the largest fractional remainders | No — this *is* the defect | **−0.1277%** | Satisfies *quota* (each cell within 1 unit) but not *consistency* (Balinski–Young). **[A-17]** measured: catastrophic in rare-event regimes — bottom seven size-deciles get **zero** events at p=1e-4. **[A-17]** the general "error correlates with cell size" claim is **FALSE** (28 positive / 19 negative across 47 regimes) because `fractional(n·p)` is saw-toothed, not monotone — use the size-decile view, never the Pearson r | Yes | — |
| **Consequence-aware stratification** (level 1, tested) | Same rule, better pools ("same probability AND same consequence") | Yes — cuts stage-identity error ~100× | **−0.1277%** (identical to level 0) | Still largest-remainder underneath. **[A-17]** the size-independent tie-break variant gave *byte-identical* results, proving **the fractional ranking, not the tie-break, is the operative mechanism** | Yes | Fully — `A14AllocationEngine` |
| **Divisor — Sainte-Laguë/Webster** | Divisor sequence instead of remainder ranking; least-biased of the family | **[A-19] No — closed** | **[A-19] +0.8970%** | **[A-19] measured and closed.** Allocated the small stratum **0 against an expectation of 6 (−100%)**, identical to largest remainder, and drifted **7× worse** than it. The failure is **provable for the whole family** (§4.1), not specific to Webster | Yes — zero added randomness | New function, existing harness |
| **Divisor — Jefferson/D'Hondt, Huntington-Hill** | Same family, different sequences | **[A-19] No — closed** | **[A-19] +6.1060%** (D'Hondt) | **[A-19] prediction confirmed, now evidenced rather than asserted.** D'Hondt was the **worst rule in either screen**: 48× the incumbent's drift and 4× worse than the ENG-01 control, with the same −100% small-stratum erasure. Huntington-Hill and Adams are closed by §4.1 without testing | Yes | Same |
| **Keyed stochastic rounding** (Philox, reusing A-4's CRN) | Round up with probability = own fractional part, from a keyed draw | **[A-17] Likely yes — verified unbiased** | **+0.00093%** (n=500 seeds, max \|z\| 2.26 over 301 timepoints) | **[A-17] measured, not assumed:** unbiased by construction; does **not** reintroduce ENG-01 (10,000 cells at expectation 1e-4 each → allocated 0.9942 vs expected 1.0, where cellwise round-to-nearest allocated **0**); **bit-reproducible at fixed seed** (identical output hashes). Adds genuine per-run variance; "deterministic mode" becomes **seeded-reproducible mode** — a disclosure, not a defect. **Did NOT fix Defect 2** | **No** — reproducible given a seed, but not randomness-free | High — Philox/BLAKE2b already built and verified |
| **Rotating / systematic placement** | Residual offset advances each step so placement bias averages out | **[A-17] No** | **−1.3086%** — an order of magnitude worse than the current rule | **[A-17] Tested and rejected.** Do not pursue | Yes | Moderate |
| **Controlled rounding** (multi-dimensional, Cox–Ernst) | Totals hold simultaneously across several axes | **[A-19] Yes — the only deterministic rule that does**, but *conditionally*: see §4.2 | **[A-19] +0.2031%** | **[A-19] measured.** Gave the small stratum **exactly its expectation (0.000% error)** where every other deterministic rule gave −100%, and is **5.3× more accurate on column margins** than the incumbent. Price: **1.6× the incumbent's drift**, 61× its runtime (0.21 s per 300-step path), and protection only at the granularity of the margins declared. The plus-minus problem is not triggered here — expected event counts are non-negative; it would return if the rule were applied to signed net migration | Yes — solved by LP, bit-identical across runs | New machinery |
| **Fully cellwise rounding** (ENG-01 control) | Round each cell independently | No — this is ENG-01 | **−1.5372%** | **[A-17] Failed as required**, confirming the control works. Erases rare events entirely | Yes | — |
| **Full stochastic mode** (already the direction for headline results, A-16) | No allocation step for disease events — true binomial draws | Sidesteps it | reference (exact) | Doesn't reach migration's allocation step (§3); heavy for 4,000-particle calibration sweeps | No — inherent randomness, CRN-controlled | Already built and validated (A-4, A-16) |

---

## 3. [this project — VERIFIED 2026-09-12] A real, separate defect: out-migration ignores model mode

The original memo flagged this; **it is confirmed by reading the source.**

`migration.apply()` receives both `rng` and `deterministic` (`migration.py:340–348`) and passes both to
`_apply_positive(n_add, population, childhood, rng, deterministic, ...)`. But the negative branch calls

```python
self._apply_negative(n_remove, population, childhood)     # migration.py:405 — no rng, no deterministic
```

**So out-migration uses deterministic largest-remainder allocation even when the model is running
stochastically.** Every other event in a stochastic run is drawn; this one is apportioned.

**Materiality — measured and bounded, not assumed.** Net migration is negative only in 2004–2007 in the
configured series, and Δnet migration between arms is **0** in the base case, so the between-arm effect is
almost certainly nil. But it means **the "stochastic reference" used throughout A-4/A-5a/A-16/A-17 contains
a deterministic allocation step.** Small, but it should be disclosed rather than discovered by a reviewer.

**Lowest engineering cost of anything in this memo.** Fix: pass `rng`/`deterministic` through and draw
multinomially when stochastic. Should be bundled with the A-2/A-9 repair design, not done in isolation.

---

## 4. [A-17] What is already settled — do not re-run these

- Largest-remainder divergence, rare-event behaviour, and the falsity of the size-correlation claim.
- Keyed stochastic rounding: unbiasedness, ENG-01 safety, and fixed-seed reproducibility.
- Rotating/systematic placement: **rejected**, −1.3086%.
- Fully cellwise: fails, as the control should.
- Tie-breaking is not the mechanism; the fractional ranking is.
- **None of these fix Defect 2** (B-8's level inflation) — A-18 handles that.

### [A-19] Now also settled — the memo has no open candidates left

- **Sainte-Laguë/Webster: closed.** Measured, and closed by proof for the whole family (§4.1).
- **D'Hondt: closed.** The memo's dismissal was correct and is now evidenced.
- **Controlled rounding: measured.** It works, conditionally, at a known price (§4.2).

**Nothing in this memo is now untested.** Its survey value is spent; what remains is the decision in §4.2.

### [review 2026-09-12] Two additions to §4.1 and §4.2 — neither changes a conclusion

**(a) The monotonicity premise needs one qualifier before it becomes a manuscript theorem.**
Divisor methods — Sainte-Laguë, D'Hondt, Huntington-Hill, Adams — are **globally** size-monotone, so the
proof covers them unconditionally. **Largest remainder is size-monotone only in the sub-unit regime**, where
every expectation is below 1 so every base is 0 and ranking by fractional part coincides with ranking by
expectation. Globally it is not: `frac(10.99) = 0.99` beats `frac(11.01) = 0.01` despite being the smaller
cell, which is the same saw-tooth A-17 measured.

That regime *is* the rare-event regime, so **the conclusion is unaffected and the family is still closed**.
But a theorem stated in the manuscript must carry the qualifier, or a referee will produce the
counter-example above. Arithmetic re-derived in review: 600 × 0.99 = 594, 600 × 0.01 = 6, T = 600. ✓

**(b) A criterion-design lesson, and it is the second occurrence.**
A-19's pre-committed C1 could not certify its own winner. Controlled rounding's protection operates on
**6 rows** while C1 scored **5 deciles** — six does not nest in five, so correctly-allocated events smear
across deciles and one reads as a zero. And stochastic rounding's worst decile, 11.2%, is **2.59 SE** of
Monte Carlo noise against a 10% threshold that sits *below the resolution of the estimate itself* (a decile
expecting 1.2 events has an SE of 0.047 over 500 seeds, i.e. 3.9%).

A-19 reported both plainly and **adjusted no verdict**, which is the correct handling and should be said so.
But this is the **second** pre-registered criterion to lack the resolution to identify the right answer —
the first was A-16's CV < 0.50 rule, which "passed" NMB only because NMB ≈ −ΔCost.

> **Carry forward into every future brief: state the resolution of the criterion alongside its threshold.**
> A threshold finer than the estimate's standard error cannot discriminate, and pre-committing it does not
> make it informative.

### [A-19] §4.1 — why the divisor family is closed by proof, not just by measurement

Sainte-Laguë and D'Hondt were measured and both erased the small stratum entirely. But the measurement is
not the important part, because **the failure is provable for every rule in the family**:

> Every quota and divisor method is **size-monotone** — a cell with a larger expectation never receives
> fewer units. In the rare-event regime 600 cells have expectation 0.99, 600 have 0.01, and exactly 600
> units are allocated. If any 0.01-cell received a unit, every 0.99-cell must have received one too, giving
> ≥ 601 > 600. So the small cells receive nothing.

This covers largest remainder, Sainte-Laguë, D'Hondt, Huntington-Hill and Adams at once. **Do not test
further members of the family**, and do not read the divergence figures as though a better-behaved divisor
sequence might rescue it. The obstruction is monotonicity itself.

### [A-19] §4.2 — what controlled rounding actually guarantees, and the decision it forces

Controlled rounding may round any margin **down** to its floor, so a small stratum is never protected by
*its own* margin. What protects it is the **ceiling on the stratum-pure large margins**: those cannot
absorb more than `Σ ceil(margin)` between them, so the grand total forces the remainder elsewhere.

> **guaranteed small-stratum events = max(0, T − Σ ceil(pure-large-stratum margins))**

A-19 pre-registered this as a prediction from the layout alone and then solved the LP: predicted 6 / 0 / 0
events for a stratum-row, stratum-column and interleaved layout; observed **6 / 0 / 0**. It is asserted as
a unit test. The same condition independently explains the 300-step result — the two grid rows whose
per-step margin is below one whole event (0.473, 0.618) are exactly the two the method fails to protect.

**Controlled rounding protects a stratum if and only if the margin structure leaves it at least one whole
event.** That makes the margin design a *modelling choice that must be declared and disclosed*, not an
implementation detail — which is the real cost of the method, above its runtime.

**The decision this leaves:** stochastic rounding achieves at **every** granularity, with no margin design
and no LP, what controlled rounding achieves **only where a margin forces it**. If seeded-reproducible mode
is acceptable, it dominates. If a randomness-free rule is required, controlled rounding is the only
candidate that fixes Defect 1 at all. **That is the whole remaining question, and it is Rishi's to settle.**

### [A-19] §4.3 — the defect compounds, and the single-step decile table understates it

Over 300 steps of the synthetic process, the grid row holding the ten smallest cells ends at **1.8% of its
exact total** under largest remainder: those cells never win a residual and stay frozen at their initial
values for the entire run. A-13 is not only a one-step misplacement — it compounds into a large level error
in the smallest strata. This is the strongest evidence so far that Defect 1 is worth repairing, and it did
not require an epidemic model to see.

---

## 5. What testing this properly would look like — revised

**[A-17] Screen cheaply first.** A-17 built and validated a synthetic multiplicative-process harness that
compares any allocation rule against the exact fractional trajectory in seconds, with the ENG-01 control
built in. **Run every new candidate through that before spending a single model run.** The original plan
went straight to the switch grid; that is now unnecessary for screening.

Then, for survivors only, the A-14/A-15 harness — extended to accept a pluggable allocation *method*, not
just a pooling *level*:

1. Reproduce the switch grid (10/25/50/75/100%) and the base case under each surviving candidate.
2. Compare against the 15-seed CRN-paired stochastic reference (seeds 42–56), as A-15 did: mean |z| on
   `infections_averted` and `hiv_deaths_averted`, plus any negative or nonsensical values.
   **[A-17] Do not use monotonicity as a screening criterion** — §1 shows it is insensitive to allocation
   method, so it cannot discriminate between candidates.
3. Record bias **direction and magnitude explicitly** — a biased method is acceptable if disclosed and
   characterised; an unquantified one is not.
4. Record runtime cost.
5. **Pre-commit acceptance criteria before running anything**, in a `TASK_BRIEF_*` in the house style, to
   avoid the "convenient direction" trap A-15 §6 guarded against.

**[A-17] Add a step 0:** state in the brief which defect the candidate is meant to fix. Two of this memo's
three original targets are now known to be out of reach of every method in it.

---

## 6. Sources

- Apportionment methods (Stata Journal): https://journals.sagepub.com/doi/pdf/10.1177/1536867x1201200303
- Reduced-Precision Stochastic Simulation for Mathematical Biology (arXiv): https://arxiv.org/html/2605.00479
- Stay on Target: Population Projections and Microsimulation Design (International Journal of Microsimulation): https://microsimulation.pub/articles/00289
- SAS Macros for Constraining Arrays of Numbers (U.S. Census Bureau working paper): https://www.census.gov/library/working-papers/2015/econ/2015-coleman.html

**[A-19] Project evidence:** `outputs/calibration/a19_divisor_controlled_rounding_20260912_183159/`
(especially `A19_DIVISOR_AND_CONTROLLED_ROUNDING.md`, `raw/c1_rare_event_summary.csv`,
`raw/c1_layout_margins.csv`, `raw/c2_trajectory_divergence.csv`); brief
`TASK_BRIEF_DIVISOR_AND_CONTROLLED_ROUNDING_2026-09-12.md`. A-19 ran **no model runs**; its validity gate
reproduced all four of A-17's published divergence figures to better than 4e-8.

**[A-17] Project evidence:** `outputs/calibration/a17_residual_placement_20260912_173211/` (especially
`raw/step2_size_deciles.csv`, `raw/step3_final_divergence.csv`, `raw/step3_rare_event_check.json`);
`outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351/monotonicity_summary.csv`.

**Sources marked as web literature have not been independently verified in this session** — the four URLs
above were supplied with the original memo and should be checked before any of their claims reach the
manuscript.
