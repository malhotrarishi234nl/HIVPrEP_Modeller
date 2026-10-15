# Task brief — allocation granularity: A-14, A-15, B-8

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-11
**Owner:** Rishi Malhotra
**For:** a multi-agent session. Three tasks, one shared decision, one synthesis step.
**Feeds decision:** **D-14 — the deterministic allocation granularity** (new; record it in the Decision Log of `PHASE2_DEVELOPMENT_CHECKLIST.md`).

---

## 0. Read first, in this order

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — verification, code review, and academic-integrity standard. **It applies to every task here.** §1A.4 (the tuning prohibition) is the most important section for this brief.
2. `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md` — the finding these tasks respond to.
3. `outputs/hta_results/a12_deterministic_allocation_audit_20260910_103000/ADDENDUM_2026-09-10_independent_review.md` — in particular the **POSTSCRIPT**, which states the taxonomy error and the ENG-01/A-13 dial.
4. `stochastic_allocation.py` — 60 lines. Read the whole thing.

---

## 1. The problem, stated once for all three tasks

The model allocates integer events in deterministic mode with `deterministic_allocate_events(n, p)`: floor the per-cell expectations, then hand the residual to the cells with the largest fractional expectation, subject to capacity. It preserves the rounded aggregate across whatever array it is given.

**Two defects of the same class have now been found, at opposite ends of one dial.**

| | granularity | failure | how it looked |
|---|---|---|---|
| **ENG-01** (2026-09-06) | too fine — cellwise `np.round(n*p)` | rare events spread across many cells are **erased** | totals visibly wrong |
| **A-13** (2026-09-10) | too coarse — one aggregate across strata with different downstream consequences | rare-stratum events are **relocated** to the common stratum | **totals exactly right** |

ENG-01 cost a 4× error in ΔQALY. A-13 costs a 2.4× understatement of HIV deaths averted (deterministic 41 vs stochastic 100). **A-13 was introduced by the fix for ENG-01.**

`diagnosis`, `art_initiation` and `art_dropout` each pass one tensor spanning Acute/Chronic/AIDS with a single scalar `p` (`run_v3.py` lines 1609, 1666, 1682). Deterministic AIDS diagnoses land **667 below** expectation in the no-PrEP arm and **903 below** in the oral-PrEP arm, with equal and opposite Chronic over-allocation. **The two arms are distorted by different amounts, so it does not cancel in the difference** — and the distortion is larger in the intervention arm, suppressing apparent benefit.

**Why both audits missed it.** A-12's equivalence class was *"cells with the same event probability or rate."* These cells genuinely do share `p`, so by that rule they are one class and splitting them is safe. **The correct equivalence class is "same event probability AND same downstream consequence."** An AIDS-stage diagnosis and a Chronic-stage diagnosis share a rate but lead to very different mortality.

**The open question, which all three tasks serve:** at what granularity should deterministic allocation be performed? Too fine erases, too coarse reassigns. **This is a modelling decision, not an implementation detail**, and "HIV stage" is not automatically the answer — ART status, age and orientation are candidates wherever they carry different downstream consequences.

---

## 2. Rules that bind every task in this brief

- **Change no production behaviour.** All three tasks are diagnostic. Stratified allocation is an **opt-in diagnostic variant**, selected explicitly by the runner, defaulting to current behaviour. If you find yourself editing `run_v3.py`'s production call sites so that a number improves, stop.
- **Do not choose a granularity because it produces a better ICER, a positive ΔQALY, or a nicer fit.** The selection criterion is fixed in §3 before any run: agreement with the stochastic mean on a pre-specified metric set. Nothing else.
- **Never invent a parameter value, bound or interval.** A missing value is a blocker to report.
- **Judge every allocation result on the INCREMENTAL between-arm difference, never on an aggregate percentage.** This is the ENG-01 lesson and A-13 is the proof: A-13's aggregates were exactly right while the incremental was wrong by 2.4×.
- **Label every inference** *assumption*, *approximation* or *derived estimate*; every claim *unsupported*, *weakly supported* or *well-supported*.
- **Never overwrite an existing `outputs/` directory.** New work goes to a new timestamped directory.
- **Reuse seeds 42–56** wherever a stochastic comparison is made, so results are directly comparable with A-4.
- **Report `n`** behind every variance, SD, SE or interval.
- Each task writes: a markdown report, the CSV/JSON artefacts named in its acceptance criteria, and a `manifest.json` carrying the exact command, git commit and dirty state, config and code hashes, seeds, environment, and output hashes. Add a row to the Completion Log in `PHASE2_DEVELOPMENT_CHECKLIST.md`.

### On why §3 is not tuning — read this before objecting to it

The model's stochastic mode draws binomial events. **That is the model as specified.** Deterministic mode is a computational *approximation* to it, adopted for speed and reproducibility. Calibrating an approximation to the thing it approximates is legitimate and standard; it is not the same as tuning a tolerance until a result passes.

The guardrail that keeps it legitimate: **the metric set and the acceptance band are pre-specified in writing, in the report, before the sweep is run** — and the ICER, ΔQALY sign, and cost-effectiveness conclusion are explicitly *excluded* from the selection criterion. If the chosen granularity happens to make the headline worse, it is still the chosen granularity.

---

## 3. A-14 — Allocation granularity sweep

**This is the primary task. A-15 depends on its harness. The D-14 decision rests on its output.**

### Objective

Determine empirically the granularity at which deterministic allocation reproduces the stochastic mean, and produce the curve that shows the dial.

### Method

**Step 1 — build the diagnostic harness.** Add an opt-in stratified allocation mode. Suggested shape: a wrapper that partitions the tensor along declared axes, calls `deterministic_allocate_events` once per stratum, and concatenates. It must be selected explicitly by the runner and default to current behaviour. Production call sites stay unchanged.

**Step 2 — pre-specify, in writing, in the report, before running:**
- the metric set (see below),
- the acceptance band (proposed: the deterministic value lies within **2 SE** of the 15-seed stochastic mean for every metric in the set; state and justify any departure),
- the stopping rule if more than one level passes (proposed: choose the **coarsest** passing level, on the grounds that coarser is more numerically stable and less prone to ENG-01-style erasure — state this before you see results).

**Step 3 — run the sweep.** `base_case_context`, deterministic, same configs and frozen state as A-4 and A-12:

| level | strata |
|---|---|
| 0 | whole tensor — current production behaviour |
| 1 | HIV stage |
| 2 | HIV stage × ART status |
| 3 | HIV stage × ART status × PrEP state |
| 4 | fully cellwise — reproduces ENG-01 |

Level 4 is included deliberately: it makes the erasure end of the dial visible, and it should *fail*. If it does not fail, that is itself a finding and you should report it rather than quietly dropping the level.

**Step 4 — compare** each level against the 15-seed stochastic mean already in `outputs/hta_results/a4_common_random_numbers_20260910_170048/stochastic_summary.json` (n=15, seeds 42–56). Report per level, per metric: deterministic value, stochastic mean, SE, and |z|.

**Metric set (pre-specified, and this is the whole set):**
`infections_averted`, `delta_hiv_deaths`, `delta_background_deaths`, `delta_undiscounted_person_years`, `delta_discounted_person_years`, `delta_final_population`, plus the A-13 within-arm stage-allocation identities for `diagnosis`, `art_initiation` and `art_dropout` (actual minus expected, by stage, within each arm).

**`delta_qaly` is reported but is NOT part of the selection criterion.** It is downstream of the person-time channel and is the quantity most exposed to motivated reasoning.

### Do not

- Do not change production allocation in this task. The repair is a separate, reviewed design with A-2 and A-9.
- Do not add a level beyond 3 hoping for a better match without saying why that stratification is epidemiologically meaningful.
- Do not select on ΔQALY or the ICER.

### Acceptance

- All five levels run and are reported with the |z| table.
- The pre-specified metric set, acceptance band and tie-break appear in the report **above** the results.
- The A-13 stage-allocation identities are reported per level, showing the −667/−903 AIDS deficit at level 0 and what it becomes at each other level.
- A recommendation for D-14 with rationale — **a recommendation only; the decision is Rishi's.**
- A plot or table of the dial: metric agreement versus granularity, level 0 through 4.

### Pre-committed failure case — decide this now, not after seeing results

**If no level reproduces the stochastic mean within the pre-specified band**, then deterministic mode is not a valid approximation for this estimand. Report that as the finding, recommend the base case move to the **stochastic mean with an interval**, and stop. Do not widen the band to make a level pass.

### Escalate immediately if

- No level passes (above).
- Level 4 (fully cellwise) passes — that would contradict ENG-01 and means one of the two findings is wrong.
- Two non-adjacent levels pass but the level between them fails — non-monotonicity in the dial is unexplained and must be diagnosed before a recommendation.
- The stratified harness changes level-0 results at all; level 0 must reproduce the eleven A-12 reference values exactly.

---

## 4. A-15 — Does A-13's mechanism explain A-9?

**Depends on:** A-14 Step 1 (the harness). Can start as soon as that lands; does not need A-14's conclusion.

### Background

A-9 is open: at switch_100 the intervention arm's adult person-time falls below oral PrEP, and infections averted are non-monotonic across the switch grid — **+38, +7, +34, +113, +96** for switch 10/25/50/75/100. The handoff already records A-13's cross-stage allocation mechanism as the leading concrete hypothesis.

### Objective

Test that hypothesis directly, and either close A-9 or eliminate the hypothesis.

### Method

Re-run the substitution switch grid (10/25/50/75/100) under allocation level 0 and under at least one stratified level — level 1 at minimum, and A-14's recommended level if it has landed. Reuse `tools/run_a7_decision_comparison_decomposition.py` rather than writing a new grid runner.

Report per switch level and per allocation level: infections averted, ΔQALY, adult person-time, HIV deaths averted, and the A-13 stage-allocation identities.

### Acceptance

- The grid is reported under at least two allocation levels.
- A stated verdict on the hypothesis: **explained**, **partly explained**, or **not explained**, with numbers.
- If monotonicity is restored, say so and by how much; if it is not, A-9 stays open and needs an independent diagnosis — say that plainly rather than reaching for a partial explanation.

### Do not

- Do not close A-9 on "looks better". Monotonic or not is a fact; state which.
- Do not conflate this with the repair. This is a hypothesis test.

---

## 5. B-8 — Does the allocation bias contaminate the calibration?

**Fully independent of A-14 and A-15. Start immediately and in parallel.**

### Why this matters

`new_aids_cases` is one of three tier-1 calibration target series in `configs/hm_target_register.json` — **28 of 92 scored target-years, 30% of the calibration** — and it is an AIDS-stage quantity. **Calibration waves run deterministically** by the standing run-mode decision. A-13 establishes that deterministic allocation distorts AIDS-stage composition. If it also distorts the *level* of AIDS-stage outputs, then the history-matching NROY result is partly an allocation artifact rather than an epidemiological finding.

### Objective

Determine whether deterministic and stochastic modes disagree on the **levels** of the calibration target series at a fixed parameter point.

### Method

Half the data already exists. `outputs/calibration/a5a_epidemiological_seed_variability_20260910_162000/raw/yearly_v_ensemble.csv` holds the **stochastic** mean and sample variance per year, n=50, seeds 7001–7050, at config `configs/V13.22_20260604_nearfit_rank001_basecase.json`.

Run the **same config deterministically**, extract the same annual series via `calib_extractor.extract_annual_outputs`, and compare year by year for all seven A-5a series: `PLHIV`, `new_diagnoses`, `new_diagnoses_msm`, `aids_deaths`, `on_art_stock`, `new_infections`, `new_aids_cases`.

Report per series per year: deterministic value, stochastic mean, stochastic SE (n=50), |z|, and the percentage difference. Then summarise: which series diverge, by how much, in which direction, and in which years.

### What is genuinely unknown here — do not prejudge it

A-13 measured the *composition* of diagnosis / ART-initiation / ART-dropout flows. It did **not** measure the level of chronic→AIDS progressions. So the direction of any effect on `new_aids_cases` is **unknown** and must be measured, not assumed.

One piece of context that matters for interpretation, and that you should verify rather than trust: the 2026-09-06 reachability audit reports modelled `new_aids_cases` running far **above** the ECDC target. If the deterministic bias is suppressing AIDS-stage output, correcting it moves that series **further from** its calibration target, not closer. **A finding that the calibration gets harder is a valid finding.** Report it at the same prominence as any other.

### Acceptance

- All seven series compared, every year, with |z| and percentage difference.
- A stated verdict: does deterministic mode materially distort any calibration target series? With numbers.
- If yes: a statement of which history-matching conclusions are affected and must be re-run after the repair — specifically whether the NROY = 0 result in `outputs/calibration/hm_prototype_20260910/` is implicated.
- The direction of any effect on fit quality stated explicitly, including if it makes the fit worse.

### Escalate immediately if

- Any calibration target series diverges by more than 2 SE in a majority of years — that would mean the deterministic run-mode decision for calibration waves needs revisiting before HM-5.
- `new_diagnoses` diverges. It is the model's best-constrained series and a divergence there would widen this well beyond the AIDS stage.

---

## 6. Synthesis step — after all three land

One agent, reading all three reports plus the A-13 report, produces a short decision memo for **D-14**:

1. The recommended allocation granularity, with the evidence from A-14.
2. Whether A-9 is explained (A-15).
3. Whether the calibration is contaminated and what must be re-run (B-8).
4. **What changes downstream once the repair lands**, specifically: the eleven A-12/A-4 deterministic reference values will move and must be regenerated — and relabelled as a *correctness* anchor rather than only a *reproducibility* anchor, since they are currently known to sit on a biased mortality channel.
5. The manuscript disclosure implied, whatever the outcome. A-13's judgement that the deterministic base case understates mortality-mediated health gain stands as a disclosure item regardless of whether the estimator is later redesigned.

**The memo recommends. It does not decide, and it does not implement.** The repair itself is designed with A-2 and A-9 afterwards, per A-13's own scope boundary.

---

## 7. Suggested parallelisation

| | task | depends on | can start |
|---|---|---|---|
| Agent 1 | **A-14** harness + sweep | — | immediately |
| Agent 2 | **B-8** calibration contamination | — | immediately |
| Agent 3 | **A-15** A-9 hypothesis | A-14 Step 1 only | when the harness lands |
| Agent 4 | **Synthesis** | all three | when all three land |

A-14 is the long pole. B-8 is the cheapest and is fully independent — half its data already exists.

---

## 8. Also worth building alongside the eventual repair — not in these tasks

A guard test asserting that, for every production call to `deterministic_allocate_events`, the allocation preserves the expected total **within each HIV stage** to within one event. It fails on current code and would have caught A-13 before A-4. It belongs with the repair design, not with these diagnostics.
