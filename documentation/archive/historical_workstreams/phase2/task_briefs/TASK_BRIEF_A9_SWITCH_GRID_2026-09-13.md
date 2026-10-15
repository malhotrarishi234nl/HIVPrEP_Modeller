# Task brief — A-9: why is the substitution switch grid non-monotone?

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-13 · **Owner:** Rishi Malhotra
**Type:** measurement. **Implements nothing in production.** Ends in a localisation, or in an explicit statement that it does not localise.
**Follows:** A-15, which tested and eliminated the allocation-granularity explanation.
**Batching:** independent of A-22 and A-23; all three reuse the A-20 harness and can run in parallel.

---

## 1. The defect, and why it matters more than its size

Raise the share of oral-PrEP users switching to lenacapavir — 10%, 25%, 50%, 75%, 100% — and benefit
should rise with it. Deterministic mode gives **+38 / +7 / +34 / +113 / +96** infections averted. It
goes up, down, up, up, down.

**This is not a precision problem, it is a coherence problem.** A cost-effectiveness model whose
benefit does not increase with the size of the intervention cannot support a scenario analysis, and a
reviewer who plots that grid will stop reading. It is the one open Track A defect that directly
constrains what the paper can contain.

## 2. What is already established — do not re-test any of it

| finding | source |
|---|---|
| Level 0 reproduces the A-9 trigger **exactly** (+38/+7/+34/+113/+96, difference 0.0) | A-15 |
| **No allocation granularity repairs it.** Levels 0–4 are all non-monotone; level 1 makes it *worse*, adding negative infections averted at two switch levels and negative HIV deaths averted at three | A-15 |
| **The stochastic mean grid IS monotone**: 0.7 / 29.3 / 56.9 / 89.5 / 122.5 (SE 5.5–7.6, n=15) | A-15 |
| **14 of 15 individual stochastic seeds are monotone on their own** | A-15 |
| Within-seed increments are stable: +28.7 / +27.5 / +32.6 / +33.1 | A-15 |
| It is **not** A-13's stage defect — eliminating that at level 1 does not restore monotonicity | A-15 |
| Half the original A-9 trigger is weak: 4/15 stochastic seeds also give negative adult person-time at switch_100 | A-15 |

**The decisive fact: same baseline, same scenarios, same grid structure — 0 of 3 distinct deterministic
grids monotone against 14 of 15 individual stochastic seeds.** So it is specific to deterministic mode,
and it is not small-sample noise.

**What A-15 could not do** is say *which* deterministic process causes it, because the only instrument
available then was the granularity dial. **A-20 built the instrument that can.**

---

## 3. Read first

1. `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A.
2. `outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351/A15_A9_ALLOCATION_HYPOTHESIS.md` — the whole thing; it is the direct predecessor.
3. `outputs/calibration/a20_process_decomposition_20260912_221520/` — the harness and its switch-surface inventory.
4. `outputs/calibration/a21_rare_stratum_starvation_20260913_001500/` — the confirmed mechanism at one site, and the tracing pattern.
5. `outputs/hta_results/a7_*` (the grid runner A-15 reused) — reuse it again; do not rewrite the grid.

---

## 4. Method — A-20's decomposition, pointed at a different symptom

Run the **full switch grid (10/25/50/75/100)** once per arm, with **one process drawn stochastically
and every other process deterministic**, using A-20's process-switch harness and A-7's grid runner.

Arms: transmission, disease progression, diagnosis, ART, HIV mortality, **background mortality**, births,
migration, ageing & sexual debut, PrEP. Plus two references: **all-deterministic** (must reproduce A-9's
trigger exactly) and **all-stochastic** (must reproduce A-15's monotone grid).

**n=15, seeds 42–56, CRN-paired** — matching A-15 so the two are directly comparable. Do **not** switch
to A-20's 7001–7050: that would make this grid incomparable with the only prior measurement of it.

### The success criterion, pre-specified before any result is viewed

**An arm "repairs" the grid if its mean grid is monotone non-decreasing in infections averted across
all five switch levels.** Monotonicity is the defect; monotonicity is the criterion. No threshold is
needed and none should be invented.

**Resolution check, required.** A mean grid can be monotone by luck when increments are small relative
to their standard error. So also report, per arm: each of the four increments with its SE, and the
**minimum increment / SE** across the grid. **An arm is called a repair only if it is monotone AND its
smallest increment exceeds 2 SE.** A-15's stochastic reference gives increments +28.7/+27.5/+32.6/+33.1
(sd 24.4/17.3/23.4/18.0, n=15), i.e. minimum ≈ 27.5 / (17.3/√15) ≈ 6.2 SE — comfortably resolvable, so
the criterion can discriminate. Report this calculation for every arm.

**Secondary, reported for every arm whether or not it repairs:** `hiv_deaths_averted` monotonicity, the
count of switch levels with *negative* infections averted, and mean |z| against the stochastic grid.

---

## 5. Controls, all mandatory

- **All-deterministic arm must reproduce +38/+7/+34/+113/+96 exactly** (difference 0.0), and A-15's 25
  exact-match reference rows. **Runner raises if not.**
- **All-stochastic arm must reproduce A-15's 0.7/29.3/56.9/89.5/122.5** within its stated SEs.
- Switch-surface inventory (A-20's AST version) must be produced and every arm must own at least one
  reachable branch site on these configs. **An arm whose switch was never reached is not a null.**
- Opt-in, restoring harness with a restoration-after-exception test.
- `git diff` on tracked files empty, or explained.

---

## 6. If an arm repairs it — Step 2

1. **Which switch level breaks first** in the deterministic grid, and does that arm fix that level specifically?
2. **Trace the divergence**: at the earliest timestep where two switch levels cross over incorrectly, which compartments differ and by how much? A-21's tracing pattern applies directly.
3. **Is it the same mechanism as A-21?** Background mortality is in the arm list precisely because A-21's confirmed starvation is asymmetric between arms whenever the arms have different HIV-positive stocks — which is exactly what a switch grid creates. **This is the leading prior and it must be tested, not assumed.**

## 7. If no arm repairs it

That is a real and reportable outcome: the non-monotonicity would then be a property of the
*composition* of deterministic processes rather than of any one. **Report it plainly and stop.** Then
the honest options for the paper are (a) report the scenario grid from stochastic runs only, or (b)
drop the scenario analysis. Both are defensible; guessing a mechanism is not.

---

## 8. Escalate immediately if

- Either reference arm fails to reproduce its published grid. Stop.
- An arm repairs infections averted but breaks `hiv_deaths_averted`, or vice versa. Report both; recommend neither.
- More than one arm repairs it. **Do not pick the convenient one** — report all, and note that arms are non-additive so the combination is not determined by the singles.

## 9. Do not

- Do not sum arms or attribute shares. Non-additive.
- Do not change the scenario definitions, the switch levels, the seed set or the baseline.
- Do not re-test allocation granularity — A-15 did it across five levels and A-19 closed the rule family by proof.
- Do not compute or report an ICER.
- Do not implement anything.
