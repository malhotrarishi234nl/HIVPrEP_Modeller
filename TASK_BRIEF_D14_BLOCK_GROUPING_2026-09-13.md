# Task brief — A-22: how should the background-mortality block be grouped?

**Created:** 2026-09-13 · **Owner:** Rishi Malhotra
**Type:** measurement + a recommendation. **Implements nothing in production.**
**Follows:** A-21, which confirmed the mechanism. This asks what to do about it.
**Batching:** independent of A-9 and A-23; all three reuse the A-20 harness and can run in parallel.

---

## 1. What is settled, and what this task decides

**Settled by A-21 (do not re-litigate).** `demography.py:_calculate_life_table_mortality` (lines 981/989)
passes `population[age_idx, orient_idx, :, :, :, :, :]` to `deterministic_allocate_events` at **one
shared rate**. The block mixes the very large HIV-negative cells with every small HIV-positive cell.
The allocator preserves the block's rounded aggregate and awards residuals by fractional part, so the
sub-unit HIV-positive cells lose essentially every contest: **22.052%** of HIV-positive background
deaths are relocated to HIV-negative cells (**97.3%** shortfall 1975–1990, **93.7%** of occupied HIV+
cells receiving zero). Conservation and relocation identities close exactly.

**Settled by A-19 (do not re-test).** No change of *rule* can fix this. Largest remainder,
Sainte-Laguë, D'Hondt, Huntington-Hill and Adams are all size-monotone and starve sub-unit strata
identically — proved, not measured. The only deterministic lever is **which cells share a block**.

**What A-22 decides.** The block boundary. This is **D-14's dial at a call site D-14 never swept** —
A-14 and A-15 swept transmission, diagnosis and ART, never background mortality.

**The governing rule is D-14's own:** a set of cells may be treated as exchangeable only if they share
**the same event probability AND the same downstream consequence.** Everyone in the current block shares
the background-mortality rate, so the first half holds. **Being alive with HIV has a different
downstream consequence from being alive without it** — that is the entire content of an HIV model — so
the second half fails on the HIV axis. **The brief expects level 1 to win. Test it; do not assume it.**

---

## 2. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding standard. §1A.4 (tuning prohibition) applies.
2. `outputs/calibration/a21_rare_stratum_starvation_20260913_001500/` — report **and** addendum.
3. `outputs/calibration/a20_process_decomposition_20260912_221520/` — the arm this replaces, and the harness.
4. `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md` §§ on ENG-01 — the failure mode at the **fine** end.
5. `stochastic_allocation.py` — 30 lines; read the allocator before changing what you feed it.

---

## 3. The grouping levels

One deterministic run per level, full 1975–2025 window, `configs/V13.22_20260604_nearfit_rank001_basecase.json`.

| level | block = one call to the allocator per … | rationale |
|---:|---|---|
| **0** | age × orientation | **current production.** Baseline |
| **1** | age × orientation × HIV status (negative / positive) | the minimal split the D-14 rule implies |
| **2** | age × orientation × HIV stage (negative / acute / chronic / AIDS) | finer; stages have different mortality-relevant futures |
| **3** | age × orientation × HIV stage × diagnosis status | finer still |
| **4** | age × orientation × HIV stage × diagnosis × ART | **falsification control.** Expected to fail via ENG-01 |
| **5** | cellwise `np.round(n·p)`, no allocator | **second falsification control.** Must fail badly |

**Levels 4 and 5 are controls, not candidates.** ENG-01 established that too-fine allocation *erases*
rare events, which is the opposite failure and equally damaging. If level 4 or 5 comes out best,
something is wrong with the measurement — stop and report that rather than recommending them.

**Note the rate is unchanged at every level.** Splitting the block does not change anybody's mortality
probability; it changes only who competes for whole deaths. State that explicitly in the report, because
a reader will otherwise suspect the rate was tuned.

---

## 4. What to measure per level

**Primary (this is the decision variable).** Median absolute log ratio against the **n=50 post-CRN
stochastic ensemble** (seeds 7001–7050, `a5a_epidemiological_seed_variability_20260911_042330`) across
the 222 scored target-years. Deterministic baseline **0.18838**.

- **Material** if below **0.09419** (half the baseline — the threshold A-17, A-18 and A-20 all used, so
  the four are directly comparable).
- **Criterion resolution, stated with the threshold as the standing rule requires:** the median per-year
  log-ratio SE is **0.020609**, so 0.09419 is **4.57× the median SE** and can discriminate. State the
  resolution of any further threshold you introduce, in the same sentence as the threshold.

**Secondary, and mandatory — this is the A-21 identity, re-run per level.** For each level report:

| quantity | what it tells you |
|---|---|
| HIV-positive expected vs allocated background deaths, whole run | the shortfall the level leaves |
| the same by era (1975–1990 / 1991–2000 / 2001–2010 / 2011–2025) | whether the growth-phase starvation is gone |
| share of occupied HIV+ cells that are sub-unit, and that receive zero | the regime the level puts them in |
| **total** allocated deaths vs `round(Σ n·p)`, all cells | conservation — must still hold |
| **ENG-01 control: any cell class whose allocated total falls below 50% of expectation** | the fine-end failure, if the level over-splits |

A level that fixes the HIV+ shortfall but starves some *other* class has moved the defect, not removed
it. **The ENG-01 control is what catches that, and it is not optional.**

**Tertiary.** Runtime per level, and whether levels collapse to byte-identical outputs on this config
(A-14 found levels 2/3 were exact no-ops because the ART and PrEP axes carried mass at one index only —
check, record, and note the re-run trigger if the occupancy changes).

---

## 5. Controls

- **Level 0 must reproduce production deterministic bit-for-bit** over 1975–2025, and must reproduce
  A-21's shortfall figures (8,029.743 expected / 6,259 allocated) exactly. **The runner raises if not.**
- **Level 0 must reproduce B-8** in 222/222 scored target-years, maximum absolute difference 0.0.
- The harness is **opt-in and restoring**, with a test asserting restoration after an exception.
- `git diff` on tracked files empty at the end, or every entry explained.

---

## 6. What the report must contain

- The criterion and its resolution **above** the results.
- A table of all six levels on the primary measure, ranked.
- The A-21 identity table per level, including the era breakdown.
- The ENG-01 control result per level, stated even where it passes.
- **A recommendation with its cost stated**, in one line per level considered.
- **The limitation, prominently:** one parameter point, one deterministic trajectory. This establishes
  what the allocator does at this fit, not the magnitude at others.

**If two levels are indistinguishable on the primary measure, prefer the coarser one** and say so — it
is the smaller departure from production and the smaller ENG-01 risk. Do not break a tie on the ICER,
on ΔQALY, or on any economic output; those must not be computed in this task at all.

---

## 7. Escalate immediately if

- Level 0 fails either control. Stop.
- The best level is 4 or 5 (the falsification controls). Stop and report the measurement problem.
- A level fixes the HIV+ shortfall but trips the ENG-01 control. Report both, recommend neither.
- **No level is material.** That is a real outcome: it would mean the block boundary is not the whole
  story and deterministic calibration cannot be repaired by regrouping. Say so plainly.

---

## 8. Do not

- Do not change any mortality rate, life-table value, tolerance or bound. The rate is fixed; only the
  block boundary varies.
- Do not implement a production default. A-22 recommends; Rishi decides.
- Do not re-test alternative allocator rules — A-19 closed that family by proof.
- Do not compute or report any economic output.
- Do not extend the same regrouping to other call sites in this task. One site, one decision. Other
  sites are a follow-up **after** the D-14 rule has been applied once and its cost is known.
