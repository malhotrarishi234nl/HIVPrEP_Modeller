# Phase 2 — Implementation Review & Improvement Plan

> **ARCHIVED 2026-10-16.** This July plan is retained for provenance and is
> not the current implementation plan. Start at `documentation/INDEX.md`.

**Model:** `Modeller/v11` HIV transmission + CEA
**Goal:** raise the model to publication / peer-review standard, prioritised for a thesis on the CE of lenacapavir vs. oral PrEP (Netherlands)
**Date:** 2026-07-06

This plan turns the Phase 1 findings into an ordered work programme. It is scoped for someone finishing a thesis: **defensibility and reproducibility first, elegance last.** Each item has an effort estimate and a "why a reviewer cares" note.

---

## 0. Guiding principle

The code is already strong on *mechanics* (mass balance, discounting, ICER math, test coverage, sourcing) and weak on *uncertainty propagation and self-description*. So the highest-leverage work is **not** rewriting the engine — it is (a) closing the calibration→PSA uncertainty gap, (b) making the calibration method honest about what it is, and (c) making the manuscript's claims match the code. Do those three and the model becomes defensible.

---

## Priority tier A — Do before the defense / submission (defensibility-critical)

### A1. Close the calibration→PSA uncertainty gap (Phase 1 #1)
**Effort:** M (½–2 days depending on option) · **Reviewer cares:** this is the difference between a credible and a non-credible uncertainty analysis.

Options, best first:
1. **Ensemble PSA:** draw the calibrated epidemic parameters jointly from the *accepted near-fit set* (or SNPE posterior) instead of freezing them. Implementation: extend `PSASampler`/`run_psa_hta.py` to accept an empirical particle table and sample rows (preserving joint structure) for the `estimated` parameters, layering the independent `uncertain`/HTA draws on top.
2. **Marginal + correlation fallback:** add `"estimated"` to `psa_config.tiers_to_sample`, give each estimated parameter a defensible distribution (SE from the calibration spread), and impose at least block correlations (betas together, seed params together).
3. **Documented conditioning (minimum):** keep the frozen point but (i) state in the manuscript that PSA conditions on the point-calibrated epidemic and is a *lower bound* on decision uncertainty, and (ii) add a **multi-fit scenario analysis** (rank001 vs rank012 vs a few accepted particles) to show robustness.

**Acceptance test:** PSA ICER interval visibly reflects transmission-parameter uncertainty; CEAC regenerated; limitation text written.

### A2. Make the calibration method honest (Phase 1 #2)
**Effort:** S (rename + text) to L (reimplement) · **Reviewer cares:** "ABC-SMC posterior" is a checkable claim.

- **Fast path:** stop calling the distance-weighted resampler a Bayesian posterior. In the manuscript, describe it as "sequential distance-weighted calibration producing a best-fit and a near-fit ensemble", and use the ensemble only for scenario robustness (feeds A1 option 3).
- **Rigorous path (preferred if time):** use the existing **SNPE** pipeline (`run_snpe_calibration.py`, `sbi==0.26.1`) as the inferential backbone and draw A1's epidemic parameters from that posterior. SNPE *is* a principled posterior estimator, which simultaneously fixes A1 and A2.
- If you keep the ABC code as-is, implement the Toni et al. (2009) importance weights + Beaumont (2009) adaptive kernel (2× weighted empirical variance). This is the "correct ABC-SMC" and removes the objection entirely.

**Acceptance test:** every uncertainty claim in the text maps to a method that produces that kind of uncertainty.

### A3. Resolve the societal-perspective scope (Phase 1 #4)
**Effort:** S–M · **Reviewer cares:** ZIN mandates societal as primary; the label must match the content.

- Decide with your supervisor: either **enable** `productivity_loss` (friction method already implemented + iMTA-sourced) in the base case, or **relabel** the base case "payer + patient time" and present full societal as the primary scenario.
- Implement or formally scope-out **informal care** (`BACKLOG-20`); a one-line justification (small in the Dutch context) is acceptable if cited.
- Keep and foreground the existing "exclusion is conservative for prevention" argument — but demonstrate it with a sensitivity run, not just a config note.

### A4. Quantify the event-ordering bias (Phase 1 #3)
**Effort:** S (tooling exists) · **Reviewer cares:** AIDS deaths drive averted cost/QALY.

- Run `event_order_variant = "mortality_before_diagnosis_art"` and report the AIDS-death and ICER delta vs. production order.
- If material (> a few % on the ICER), change the default ordering or lag the ART mortality benefit for same-step initiators; if immaterial, document the test and move on.

### A5. Horizon consistency (Phase 1 #5)
**Effort:** S · Reconcile the manuscript's horizon language with the actual 40-year dynamic horizon; add a 60-year scenario to bound truncation bias.

---

## Priority tier B — Strengthens the analysis (do if time permits)

### B1. Competing-risk force of infection (Phase 1 #6)
Replace the additive per-source infection sum with `p = 1 − exp(−Σ λ)` in `transmission_corrected.py`. Low behavioural change at current betas, but removes a modelling objection and the reliance on the susceptible-cap to hide overshoot. **Effort:** S–M. Guard with the existing `test_transmission_accounting.py`.

### B2. Deterministic mortality consistency (Phase 1 #8)
Route background mortality through `_deterministic_allocate_events` so the analysis (deterministic) path treats all events consistently. Document HIV mortality params as *excess* rates. **Effort:** S.

### B3. Correlated PSA sampling (Phase 1 #9)
Add correlation groups to `PSASampler`/`HTASampler` (at least: transmission betas block, cost block). **Effort:** M. Ties into A1.

### B4. Extended dominance check (Phase 1 #10)
Add an explicit weak-dominance pass to `build_comparison_table`, or assert monotonic ICERs along the frontier and state it. **Effort:** S.

### B5. Report calibration gate widths and multi-fit robustness (Phase 1 #11)
Surface the acceptance-band widths in the calibration report and show the CE conclusion across several accepted fits. **Effort:** S (mostly reporting). Reuses `audit_calibration_acceptance.py`.

---

## Priority tier C — Reproducibility & engineering hygiene (raises the "publishable" bar)

### C1. One-command, pinned reproduction path
**Effort:** M · **Reviewer cares:** reproducibility is now a submission checkbox at most journals.

- Add a top-level `Makefile`/`run_all.sh`: calibrate → select fit → PSA/HTA → report, with the exact config paths and seeds used for the thesis result.
- Pin dependencies with exact versions (`requirements.txt` currently uses `>=`; produce a `requirements.lock` / `pip freeze` for the submission environment). `sbi==0.26.1` is already pinned — good.
- Record the git commit + config hash in every output directory (the ABC runner already captures git provenance; extend to PSA/HTA outputs).

### C2. Configuration consolidation
**Effort:** M · There are many overlapping configs (`hta_config.json`, `hta_config_adjusted.json`, `hta_runtime_config.json`, `hta_costing_config.json`, dozens of `calib_space_*`). Document the **canonical set** used for the thesis result in a single `THESIS_RUN_MANIFEST.md` (which config produced which table/figure). This is the traceability the project standards demand and prevents "which numbers are final?" ambiguity.

### C3. Test-suite triage
**Effort:** S–M · 70 test files is excellent coverage. Before submission:
- Run the full suite and record pass/fail; fix or explicitly quarantine known failures (README references `BACKLOG-6` mass-balance stress failure).
- Tag the subset of tests that guard the *thesis result path* (economics, transmission accounting, HTA e2e, PSA integration) as the regression gate.

### C4. Repository cleanup for archival
**Effort:** S · Remove/relocate `.cleanup_archive`, `BACKUP 0312`, `Opus Task`, and stale `outputs/*` scratch runs from the submission bundle; keep a clean `Modeller/v11` + a single `outputs/thesis_final/`. Update `README.md` (Phase 1 #12) to describe the real 7-D model and pipeline.

### C5. Separate "assumption" from "sourced" in outputs
**Effort:** S · The configs already carry `distribution_status` (`sourced` / `assumption_for_sensitivity` / `not_used_base_case_sensitivity_only`). Surface this in the parameter table of the manuscript so every input visibly declares its evidence status — directly satisfies the project's "no silent inference / label assumptions" rule and pre-empts reviewer questions.

---

## Suggested sequence (fits a thesis-defense timeline)

1. **A4, A5** (a few hours each; existing tooling) → immediate, concrete numbers.
2. **A3** (decide perspective scope with supervisor) → unblocks base-case definition.
3. **A1 + A2 together** (the core uncertainty story) → choose the SNPE route if at all possible; it fixes both.
4. **C1, C2, C5** (reproducibility + manifest + evidence-status table) → makes the whole thing submittable.
5. **B1–B5, C3, C4** as time allows.

---

## Bottom line

The engine is not the problem — the **uncertainty narrative and the perspective/label consistency** are. If you (1) propagate calibrated-parameter uncertainty into the PSA, (2) stop claiming a Bayesian posterior you don't compute (or compute one via SNPE), (3) make the "societal" label match its contents, and (4) reconcile the manuscript's horizon/method language with the code, the model moves from "smooth but contestable" to defensible under peer review. Everything else on this list is polish.
