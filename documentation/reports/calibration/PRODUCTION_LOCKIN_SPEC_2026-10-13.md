# Production Plan — Calibration, Scenarios, PSA

**Status:** DRAFT. Nothing is locked until Rishi signs off at the bottom.
**Sessions:** 2026-10-12 and 2026-10-13. **Purpose:** the one reference for what runs on the production server. Once signed, changes are dated amendments.

---

## 1. The plan

Two models, each run as a full calibration:
- **M0:** the current model, 33 calibrated parameters.
- **M1:** M0 plus one new parameter, the annual AIDS-symptom diagnosis probability (`p`). 34 parameters. At `p = 0` it is exactly M0. This is not the surveillance definition of late diagnosis (for example CD4 <350).

For each model:
1. **Calibrate** with history matching, the same method and a predeclared budget for both. Waves screen with I_max ≤ 3 only. Keep every retained parameter set and its per-target errors, not just the best one. Equal budgets make the computational comparison transparent; they do not make failure to find a set proof that no set exists.
2. **Define and confirm the plausible set:**
   - **A (acceptance):** I_max ≤ 3. Discovery uses five common seeds per candidate; final classification uses 20 disjoint fresh seeds.
   - **B (description and ranking):** error at the 2008 peak and at 2024, J, signed bias 2004–15, and hold-out checks. These generally reuse calibration data and do not silently become additional acceptance gates.
   - **C (external/downstream credibility):** HIV deaths, infections, undiagnosed and ART stocks, costs, and age distributions. A hard rejection requires a predeclared external bound, matched definition and stated consequence; otherwise the result is reported rather than used post hoc to tighten acceptance.
3. **Demonstrate region support before freezing the ensemble.** A confirmed point is not enough by itself. Use constrained local perturbations, fresh-seed confirmation and sampled bridge paths to distinguish ambient support, thin correlated-ridge support, no local support detected and unresolved evidence. Only region-supported points enter representative selection. The bounded implementation contract is [ROBUST_NROY_FREEZE_SPEC_2026-10-14.md](ROBUST_NROY_FREEZE_SPEC_2026-10-14.md).
4. **Preserve nesting, then compare M0 and M1.** Every confirmed M0 vector is inserted into the M1 design at exact `p=0` and rechecked through the M1 runner. This is a required identity/control design, not an optional warm start. Then compare how far apart the supported plausible regions and forecasts are. The comparison is informational and picks no winner. Each non-empty supported set goes forward. If no additional M1 region is found, report "none found under the declared search and budget," not structural impossibility.
5. **Freeze** the supported regions and a chosen set of representative parameter sets from each. Selection uses parameter/baseline geometry, not policy outcomes.
6. **Run the scenario matrix** on those representatives, then the **PSA**. Every representative retains its complete joint calibrated vector; calibration marginals are never independently recombined. Within-vector PSA and between-vector forecast spread are stored separately. Any pooled descriptive summary must state its weighting and is not a posterior average.

M0 and M1 are not compared by pass rates or plausible-region size, because the searches have different dimensions and effort. Compare best-attainable fit and forecast spread instead.

---

## 2. What we found (short)

- **Knot (2008 vs 2010):** at fixed parameters it changes the diagnosis curve by ≤ 1.3%. Differences between the two suite runs came from different parameters, not the knot. *Well-supported.* (`followup_knot_and_late_dx_20260929/REPORT.md`)
- **Late-diagnosis search:** it varied 4 parameters (3 existing multipliers + the new p) with the other 30 held fixed, in a local box. It doesn't prove M1 can't fit, since a joint calibration was never run. *Weakly supported* that "the miss is structural." Best legacy candidate misses 2008 by about 13% and 2024 by about 35%. The one decent late-diagnosis candidate has I_max 3.001, just over the cutoff.
- **Earlier error corrected:** the extension is one new parameter (34 total), not "4 added to 33."
- **M0 sits inside M1** (code check): with p = 0 the model runs the old diagnosis code. At any p > 0 the random draws differ from M0, so compare as distributions with enough seeds, not paired differences. This RNG re-keying does not justify excluding an interval above zero: exact `p=0` is retained as the identity boundary and every positive value in the signed range remains admissible.
- **PSA problem:** the PSA varies `msm_versatility` and the stage multipliers while the transmission betas and historical calibration are fixed. Baseline infections across draws run 272 to 109,153 (median 5,118) against about 3,100–4,400 at the calibrated point. The top 5% of draws supply 52% of the summed positive ΔQALY. **The current PSA can't be used in text.** The primary model will instead remain conditional on `msm_versatility = 0.5` and the literature point estimates for the acute and AIDS multipliers. They are excluded from ordinary PSA; no sensitivity is run without a decision-relevant question.
- **Literature:** no rule exists for picking one winner among passing candidates; carry the set forward. *Weakly supported* (abstracts only; SOURCE_REGISTRY section 8).
- **Not verified:** the lenacapavir ΔQALY penalty explanation, and the downstream policy numbers from the 09-30 chat.

---

## 3. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Run M0 and M1 as two full calibrations | Proposed |
| 2 | Tier A defines acceptance; B ranks/describes; C rejects only against predeclared, definition-matched external bounds | Working agreement |
| 3 | M0–M1 comparison is informational; each non-empty supported set proceeds separately and receives no implicit probability weight | Working agreement |
| 4 | **Transmission uncertainty.** Fix `msm_versatility = 0.5` and both stage multipliers at literature point estimates; exclude them from ordinary PSA; disclose conditioning; reopen only for a decision-relevant, recalibrated test | Working agreement |
| 5 | **Knot.** Use 2008 as the primary on-ART progression schedule convention and 2010 as a structural sensitivity; do not claim the data identify 2008 as superior | Working agreement |
| 6 | Report a plausible-fit ensemble, not a single winner; no tightening of bands (only as a labelled sensitivity); no posterior wording | Proposed |
| 7 | A confirmed point must have demonstrated local/region support before entering representative selection; points with no local support detected or unresolved evidence remain reported | Working agreement |
| 8 | Every simulation stage must state a question, contrast, output, decision consequence and stopping rule; if no possible result changes a decision, do not run it | Working agreement |

**Numbers still to set before running** (Rishi's call, written down before any selection):
- exact production target register and frozen target order (the current 13-target contract is the recommended choice because it implements the recorded 1996-onward HIV and 2000-onward AIDS source-window decision; older 15-target memos predate that decision)
- range of `p` (current proposal 0–0.5, with exact `p=0` designs included)
- budget and stopping rule per arm
- any externally justified Tier C thresholds
- how many representative parameter sets, and how they are drawn
- scenario list (currently 19 scenarios, 15 seeds)
- Monte Carlo precision/extension rule for the policy analysis

Local support and sampled-path connectivity checks are part of the base plan. An emulator is triggered only by a recorded unresolved geometry question. Correlated-output scoring remains diagnostic unless separately justified. ABC-SMC is not part of this plan.

The 2026-09-17 and 2026-09-18 direction memos are reconciled as follows: this plan adopts their joint adaptive search before making a structural-failure claim. Their proposed time-varying-transmission comparator is deferred rather than silently rejected; it is reconsidered only if the adequately budgeted M0/M1 searches fail their predeclared endpoint. M1 is a later, explicitly separated stage-diagnosis question, not evidence that the older memo established AIDS presentation as the missing mechanism. The five-seed discovery and 20-fresh-seed confirmation rule here supersedes the earlier `K=10` exploratory convention for these production runs.

---

## 4. Before any compute

Prerequisite audit as of this review:

- **A-28:** implemented and documented complete; retain its regression gates in the production suite.
- **C-4:** code defect fixed (`default_before=1.0`); the pre-1996 diagnosis level remains a calibration/modelling question, not an unfixed code bug.
- **A-25:** invalid age-bin fallback is guarded and current configs use `65-69`; final policy outputs must be regenerated on the frozen production build. The wider age-risk/mortality limitations remain disclosure/model-scope questions.
- **A-16:** the retained rerun artifact is not the final current-build economic result. The frozen representative scenario analysis replaces it rather than treating the old artifact as closed production evidence.
- **A-12/M1:** `_step_stage_structured_diagnosis` is currently an untriaged allocation-rounding site in the A-12 inventory. Classify it, test the six-pool allocation and conservation behaviour, and close or explicitly document the finding before M1 production use.
- **MSM-relative-diagnosis multiplier — BLOCKER, pre-calibration.** The neutral value of the MSM-relative diagnosis multiplier does not reproduce the baseline. The failing test is exactly:

  ```
  tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior
      ::test_enabled_with_multiplier_one_matches_baseline
  AssertionError: Multiplier=1.0 should produce identical MSM diagnoses;
      ref=93601, mult1=86772
  ```

  It is pre-existing, is already recorded as a tolerated failure by the joint-fit runner's validate gate (`"tolerated_failure_still_fails": true`), and it is **not** caused by the robust-NROY work. It is promoted to a blocker because it is an identity at a neutral parameter value that does not hold, in the same diagnosis mechanism the production calibration depends on, and because a 7% difference in MSM diagnoses is larger than several of the effects the calibration is being asked to resolve.

  Resolution requires a separate diagnostic — not a tolerance, not an allowlist entry — that distinguishes four possibilities and reports which one holds:

  1. **One-step mathematical/distributional neutrality at `1.0`.** With the same state and the same draws, does the multiplier path produce the same transition *distribution* as the baseline for a single step? This is an arithmetic question and is answered without running an epidemic.
  2. **RNG-stream coupling.** If the multiplier branch consumes draws in a different order, or re-keys a counter-based stream, the two runs are different samples from the same distribution. Diagnosed by comparing the two paths across a seed block distributionally rather than seed by seed, and by instrumenting draw counts per step.
  3. **Amplification through the longitudinal epidemic.** A per-step difference too small to see may compound over 1980→2024 through the force of infection. Diagnosed by reporting the per-step divergence as a function of year, starting from a common state.
  4. **An actual change in the transition distribution** — a real defect in the multiplier path, in which case the mechanism is wrong and the fix is a code fix.

  (1) and (4) are mutually exclusive answers to the same question; (2) and (3) are coupling artefacts that leave the mechanism correct but make a paired comparison meaningless. The pattern to follow is the M0/M1 identity gate's `check_deliberate_failures`, which exists precisely so that an identity test cannot pass vacuously. No production calibration starts while this is unresolved.

Then: write `CONFIG_REGISTRY.md` with frozen candidate identities; check seeds are disjoint from earlier runs; dry-run validation with no simulation; a small pilot to measure time per trajectory; price the full run. The robust-NROY stage must additionally pass shard, merge, resume and stage-handoff equivalence tests before distributed execution.

Before the production build is frozen, perform a bounded repository-hygiene pass. Inventory executable scripts, configs, tests and generated artifacts; identify the production entry points; and move superseded exploratory files into a clearly labelled historical/archive folder rather than deleting them. Preserve paths or update references for anything cited by manifests, tests or documentation, and retain provenance for prior results. Generated outputs and caches must not be mixed with source code. The pass ends with reference/link checks and the relevant regression suite, so tidying cannot silently change the scientific model. Ambiguous or still-referenced files remain in place and are listed for review; age or filename alone is never sufficient evidence that a file is stale.

The code may be built as a config-driven implementation before the numerical values are signed. Before production-run greenlight, the target contract, `p` range, search budgets/stopping rules and representative-selection rule must be frozen. Scenario counts and policy Monte Carlo extension rules may be frozen after calibration but before any policy result is inspected. No production simulation starts while a required value is still described only as a proposal.

**Order:** sign off → repository-hygiene pass → freeze configs → dry run and pilot → calibrate both → fresh-seed confirmation → robust-NROY freeze stage → compare supported regions → freeze representatives → scenarios → PSA → update logs.

---

## 5. Sign-off

| # | Locked value | Signed | Date |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| Numbers (section 3) | | | |

*Unresolved from the sessions:* "chained regression" weighting was not found in the code (the objective is the worst of four target groups, split at 2015); your "layers of tests" discussion couldn't be located, so section 1 is the reconstruction; Set B's identity isn't re-verified.
