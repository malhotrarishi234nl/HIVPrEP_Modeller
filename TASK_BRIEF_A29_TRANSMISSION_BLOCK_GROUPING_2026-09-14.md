# Task brief — A-29: block grouping for the deterministic transmission allocator

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Type:** production change + measurement. **This one does implement in production.**
**Sibling of:** A-22 (`TASK_BRIEF_D14_BLOCK_GROUPING_2026-09-13.md`), which asks the same question at the
background-mortality call site. Same governing rule, different call site, and this one is worse.
**Blocks:** the calibration wave, plus re-measurement of C-1's premise, B-8's mechanism, and the
identifiability of `epsilon_hetero` / `orient_female`.

---

## 1. The finding this task exists to fix

`transmission_corrected.py:_deterministic_allocate_expected_infections` (defined `:213-258`, called
`:527-533`) runs **one global largest-remainder pass** over the whole 4-D expected-infections array
`[age, orientation, activity, prep_state]`.

```python
target    = min(int(np.round(capped_expected.sum())), int(capacity_arr.sum()))   # :241  GLOBAL round
remainder = target - int(base.sum())
order     = eligible[np.argsort(-fractional[eligible], kind="stable")]           # :253  GLOBAL ranking
flat[order[:take]] += 1
```

Heterosexual cells carry fractional expectations around 1e-3. They never win a global residual contest
against MSM cells, but their expectation still inflates the integer `target`. The surplus is handed to MSM.

**Measured consequence:** deterministic mode produces **exactly zero heterosexual infections at all 204
steps** (stochastic gives roughly 171), and compounds to **+11.4% cumulative MSM infections** over a full
run. Source: `outputs/hta_results/ridge_identifiability_20260914_134151/REPORT.md` §5.

**Reproduced directly on the allocator** (300 het cells at 0.0012, 12 MSM cells at 0.40):

| het block scaled | het expectation | het allocated | MSM allocated |
|---:|---:|---:|---:|
| ×0 | 0.000 | 0 | 5 |
| ×1 | 0.360 | 0 | 5 |
| ×5 | 1.800 | 0 | 7 |
| ×25 | 9.000 | 2 | 12 |

Scaling the heterosexual epidemic 25× moves **MSM** from 5 to 12. Heterosexual parameters are driving
MSM outcomes. Reproduce this before you change anything; if you cannot, stop and report that.

**Why this is worse than A-22's case.** At A-22's call site the block mixes cells that at least share a
transmission system. Here it does not. The mixing matrix is **strictly block-diagonal**
(`run_v3.py:1397-1426`): the loops only ever write `Female↔Heterosexual_Male` and `MSM↔MSM`. There are
three orientations, no bisexual category, and no bridging term anywhere in the codebase. Heterosexual and
MSM are **structurally disjoint transmission systems**. Letting one spend the other's residuals is not a
rounding approximation, it is a category error.

---

## 2. Settled. Do not re-litigate, do not re-test

1. **A-19 — no change of allocation *rule* fixes this.** Largest remainder, Sainte-Laguë, D'Hondt,
   Huntington-Hill and Adams are all size-monotone and starve sub-unit strata identically. **Proved, not
   measured.** The only deterministic lever is **which cells share a block**. If you find yourself
   evaluating a different rounding rule, you have misread this brief.
2. **ENG-01 — too-fine allocation is the opposite failure.** Cellwise rounding *erases* rare events
   rather than relocating them. Finer is not automatically better and there is a real interior optimum.
3. **D-14's governing rule.** Cells may be treated as exchangeable only if they share **the same event
   probability AND the same downstream consequence.** On the orientation axis the second clause fails
   outright, per §1.
4. **Stochastic mode is not affected by this defect** and must not be changed. The stochastic path is
   `:419-421 / :450-452 / :471-473 / :503-505`. Leave it alone; it is the control.

---

## 3. Read before touching code

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding standard. §1A.2 (tests required), §1A.3 (never
   cite an unverified source), §1A.4 (tuning prohibition) all apply.
2. `TASK_BRIEF_D14_BLOCK_GROUPING_2026-09-13.md` — the sibling task, and where the governing rule is set out.
3. `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md` — ENG-01, the failure mode at the fine end.
4. `ROUNDING_METHOD_LITERATURE_COMPARISON_2026-09-12.md` — why rule changes were ruled out.
5. `outputs/hta_results/ridge_identifiability_20260914_134151/REPORT.md` §5 — the finding itself.
6. `stochastic_allocation.py` — read the allocator before changing what you feed it.

---

## 4. The grouping levels to test

The array is `[age, orientation, activity, prep_state]`. One deterministic run per level, full window,
`configs/V13.22_20260604_nearfit_rank001_basecase.json`.

| level | one allocator call per … | rationale |
|---:|---|---|
| **0** | whole array | **current production.** Baseline |
| **1** | orientation | the minimal split §1 implies: disjoint transmission systems |
| **2** | orientation × activity class | activity classes have different forward dynamics |
| **3** | orientation × activity × age | finer |
| **4** | orientation × activity × age × prep_state | **falsification control.** Expect ENG-01 erasure |
| **5** | cellwise `np.round`, no allocator | **second falsification control.** Must fail badly |

**Levels 4 and 5 are controls, not candidates.** If either comes out best, something is wrong with the
measurement. Stop and report that rather than recommending them.

**The brief expects level 1 or 2 to win. Test it; do not assume it.**

---

## 5. Acceptance criteria

1. **Conservation holds exactly at every level.** Total allocated infections equal the rounded total
   expectation within each block, and never exceed capacity. Assert it, do not eyeball it.
2. **Heterosexual infections are non-zero** at the recommended level, and their magnitude is reported
   against stochastic mode as the reference (roughly 171). Stochastic is the target to approach, not a
   number to match exactly.
3. **The cross-orientation coupling is gone.** Repeat the §1 reproduction at the recommended level:
   scaling the heterosexual block must leave MSM allocation **unchanged**. This is the single most
   important check in the task.
4. **A regression test with teeth.** It must FAIL against level 0. State how you verified that it fails,
   not merely that it passes.
5. **Report the downstream deltas** for the recommended level: cumulative MSM infections, cumulative
   non-MSM infections, `new_diagnoses_msm`, `new_diagnoses_non_msm`, and total PLHIV, each against
   level 0. Expect large movement; that is the point.

---

## 6. Hard constraints

- **Never rebaseline golden CRN vectors.** Under any circumstance.
- **Addendum only.** Do not edit, overwrite or refresh any pre-existing reference artefact or prior
  analysis report. Stale references that now fail are deliberate standing flags. Create new files.
- **§1A.4 — no tuning.** Never adjust a tolerance, threshold, epsilon or parameter to make a check pass.
  In particular: do **not** fix starvation by adding a floor, an epsilon, or a minimum-allocation rule.
  The lever is the block boundary and nothing else.
- **Do not run a calibration wave.** No ABC-SMC, no SNPE, no LHS, no `run_history_matching.py`.
  Diagnostic simulation runs only.
- **Do not touch C-1.** `exogenous_diagnoses.py` and its call site at `run_v3.py:798` are out of scope.
  A separate pending change moves that call site ahead of ART transitions; do not pre-empt it.
- **Expect A-12 tripwires.** `tools/run_a12_deterministic_allocation_audit.py` holds a call-site
  inventory. Changing allocator call sites will fire it. **Triage each new site into the INVENTORY
  properly and regenerate `tools/a12_call_fingerprints.json`. Do not bump the count to make it pass.**
- **Environment.** Project root is `/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis ` — note
  the **trailing space**; quote every path. Python must be `hiv-prep-model/.venv/bin/python`.
- **Memory.** A 30-seed run recently exhausted swap on this machine. Run sequentially; keep seed counts modest.

---

## 7. Deliverable

Report to a new `outputs/hta_results/a29_transmission_block_grouping_<timestamp>/`, with `scripts/` and
`raw/`. State plainly:

- which level is recommended and why, against the D-14 rule rather than against which number looks better
- the §5.3 decoupling check result
- every downstream delta from §5.5
- whether the deterministic and stochastic epidemics now agree better or worse than at level 0

**Then state, as open questions rather than answers**, whether the fix plausibly explains: B-8's
+17% to +30% deterministic excess, and C-1's founding premise that the model cannot generate a non-MSM
epidemic endogenously. Both are re-measured by separate tasks. Do not claim to have settled either.

If the recommended level does not clear §5.3, say so. "The coupling persists and here is what I ruled
out" is a valid and useful result. Do not manufacture a pass.
