# Claude implementation handoff: bounded coupled calibration

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** This preserved September
> specification is not current run authority. It remains at its original path
> because frozen provenance records reference it. Start at `documentation/INDEX.md`.

Status: SPECIFICATION ONLY. No new harness exists and no simulations were launched by this handoff. Rishi requested detailed implementation instructions for Claude. This document supersedes the priority of temporal transmission changes in the 17 September direction; retain those results as evidence, not as a proposed production change.

## 1. Decision and scientific question

Implement a bounded, multistart adaptive search of the EXISTING 33-parameter model against all 15 focused targets simultaneously. The question is: can compensating parameter combinations reproduce the joint historical observations under the existing observation and uncertainty contracts?

The flexibility is in proposal generation and retention of useful trade-offs. It does not mean additional biological mechanisms, relaxed final acceptance, or independent resets between eras. Every evaluation runs the continuous 1975–2025 history using the production end-year convention. All parameters remain revisitable.

Verified in this planning session: the focused manifest records 622 vectors, seven diagnosis targets, two survivors, and termination before the joint wave. This supports revising the search; it does NOT establish model feasibility or infeasibility. The direction document contains the prior evidence and limitations. No new literature claim is needed to implement this engineering design.

The numerical search settings below are declared computational choices, not empirical parameter estimates or claims of optimality. This is a first bounded joint-search attempt, not exhaustive coverage of 33 dimensions.

## 2. Fixed contract and authoritative inputs

Repository root is the directory containing this file. Resolve paths from that root.

- Source experiment: `outputs/calibration/hm_focused_diagnoses_then_aids_20260916/manifest.json` and `wave_01/evaluated.csv`.
- Base configuration: `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json`.
- Space: `configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json`.
- Register: `configs/hm_target_register_focused_20260916.json`.
- Target selection: `scored_years` in `configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json`, selecting BOTH series from the beginning, not replaying its diagnoses-first wave sequence.
- Diagnosis years: 1996, 2000, 2004, 2010, 2015, 2019, 2024.
- AIDS years: 1990, 1995, 2000, 2005, 2010, 2015, 2019, 2022.

Load observed values and variance terms from the live register/workbook and freeze them with hashes. Do not copy stale prose saying all observation variances are zero. Exactly 15 targets must resolve, in a frozen order; an unresolved target is fatal, never silently dropped.

Preserve all bounds, fixed parameters, derived shares, probability conversions, route betas, event ordering, non-MSM exogenous diagnoses, historical PrEP, diagnosis schedules, and AIDS observation proxy. Preserve all existing scientific constraints, including ordering restrictions. Record their status as assumptions where appropriate. No 1990 initialization, AIDS presentation mechanism, or new transmission schedule in this implementation.

Use production `SimulationEngineV3`; do NOT use the historical contact-schedule subclass. Do not modify existing simulator, extraction, acceptance, or input files. Read and reuse their functions where sound; put new orchestration in separate files. Dirty git state is expected: hash live source/input dependencies, preserve unrelated edits, and do not reset the repository.

## 3. Parameters and coupled moves

Use original linear normalized coordinates u=(x-lower)/(upper-lower). No dimension reduction or log transformation in v1. Keep all 33 coordinates; multiplying parameters does not prove they are globally redundant.

Four exhaustive, disjoint proposal blocks:

1. Initial burden/composition (7): `seed_prevalence`, `seed_msm_fraction`, `p_msm`, `seed_msm_chronic_share`, `seed_msm_aids_share`, `seed_non_msm_chronic_share`, `seed_non_msm_aids_share`.
2. Contacts/mixing (9): `orient_female`, `orient_msm`, `epsilon_hetero`, `epsilon_msm`, `activity_med`, `activity_high`, `contact_low`, `contact_med`, `contact_high`.
3. Diagnosis (7): `diagnosis_1985`, `diagnosis_1990`, `diagnosis_1997`, `diagnosis_2019`, `msm_diag_mult_1996`, `msm_diag_mult_2004`, `msm_diag_mult_2019`.
4. Care/progression/survival (10): `art_init_1996`, `art_init_2000`, `art_init_2015`, `art_init_2020`, `art_dropout_rate`, `chronic_to_aids`, `chronic_to_aids_on_art_early`, `chronic_to_aids_on_art_recent`, `aids_mortality`, `art_mortality_reduction`.

Assert this partition equals the space's names exactly. Generate a table of bounds/config paths from the authoritative JSON during preparation.

Report effective contact products and clipped annual MSM diagnosis probabilities over calendar time using production schedule semantics. These are explanatory diagnostics, not substitute calibration parameters. Do not collapse activity classes or impose extra monotonicity on products.

## 4. Search ranking versus acceptance

For every evaluated vector retain all raw seed-level outputs. Let mu_i be the replicate mean, s_i^2 the unbiased sample variance (ddof=1), z_i the observation, and B_i the frozen non-process variance from the existing primary budget (including observation, code, and target-relative 0.1 discrepancy terms). Verify those terms through existing budget construction; do not assume code variance is zero.

SEARCH ONLY signed residual: r_i=(mu_i-z_i)/sqrt(B_i). This deliberately omits candidate-dependent process variance from the search denominator so increasing variability cannot improve search rank by itself. Positive means overprediction. Require positive finite B_i.

Define four reporting/objective groups: diagnoses through 2004 (3); later diagnoses (4); AIDS through 1995 (2); later AIDS (6). For each group G, R_G=sqrt(mean(r_i^2)). Rank by J=max_G(R_G), breaking ties by mean_G(R_G), then candidate ID. Equal group treatment is a declared search heuristic; it is NOT an empirical likelihood or acceptance weight. Also report maximum absolute r_i and the full signed vector.

ACCEPTANCE: reuse the existing 15-target primary maximum-implausibility rule with predictive process variance s_i^2*(1+1/K), primary discrepancy 0.1, cutoff 3, and the existing cutoff boundary convention. Report 0.2 discrepancy as sensitivity only. Verify independent arithmetic against repository scoring; never replace the maximum rule with J or group averages. Report uncertainty/variance stability and all failures, not just a scalar best score.

Keep distinct fields for search rank, discovery primary-pass, fresh-confirmation primary-pass, and sensitivity-pass. A low J is not acceptance. A primary-pass with large mean residuals/high variance remains a reported mathematical result, with its variance dependence made explicit; do not quietly add a different acceptance threshold.

## 5. Fully specified first search

Initial archive: 128 distinct valid vectors, all re-evaluated on fresh common random numbers (CRN). Include source vectors 620, 621 and 548, then valid distinct rows from `configs/hm_focused_warm_start_20260916.csv` in file order, up to 24 warm vectors in total. Use original parameters only; never reuse old output scores as fresh evaluations. Fill remaining slots with constrained LHS over the original full domain. Reject invalid warm vectors and record why; never clip them into validity.

Then perform eight synchronous batches of 48 NEW vectors each (maximum 512 discovery vectors including initialization). In every batch:

- 12 valid global LHS proposals from the original domain.
- 18 differential proposals varying all 33 coordinates.
- 18 differential proposals varying two complete blocks, cycling the six unordered block pairs three times.

Build a 64-member parent pool from the entire completed archive: best 24 by J; next best 8 per each of the four R_G rankings, skipping duplicates (32 additional); then 8 by greedy maximum minimum normalized Euclidean distance to the selected pool. Ties use candidate ID. Group specialists may be poor joint fits: they are proposal parents only. All archived vectors remain available in future selection.

For a differential proposal draw distinct pool parents a,b,c and use u_new=u_a+F*(u_b-u_c). Alternate F=0.5 and F=0.8 by proposal index; vary all coordinates for full moves, only the chosen two blocks for block moves, leaving other coordinates at a. No crossover, clipping, sorting of parameter values, or coordinatewise shrinking of the domain. Reject out-of-bounds/constraint-invalid/duplicate vectors and redraw. Require both selected blocks to actually change for block moves. Cap at 200 attempts per slot, then substitute a valid global draw, recording the fallback. Cap global candidate generation at 100,000 raw proposals per batch; failure stops with a constraint-generation diagnosis.

Use `_with_derived_parameters` before `_valid_draw_mask`, then explicit finite/bounds checks and production injection validation. Check fixed and derived constraints too; do not rely on the partial mask alone. Hash exact parameter vectors for cache identity; reject proposals within max-coordinate distance 1e-10 of an archived or same-batch normalized vector as computational duplicates. Keep this diversity tolerance separate from scientific criteria.

This is a deliberately simple adaptive differential search: parent selection changes with results, allowing coordinated moves across parameters and eras. It is not an emulator, gradient inference, posterior sampler, or proof of efficient global optimization. Retain provenance for every proposal (parents, F, blocks, RNG state, rejection/fallback counts). Do not stop because fewer than ten candidates pass or because none pass.

## 6. Seeds, budgets, and termination

Proposed discovery seeds: 197000–197004 (K=5), shared across every discovery candidate. Fresh confirmation seeds: 197100–197119 (K=20), shared across confirmation candidates but disjoint from discovery and previous scientific experiments. Engineering smoke seeds: 197200–197202. Proposal RNG seed: 197300, in a separate RNG object from simulation. Preflight must check recorded seed use before reserving these; on collision stop and report a replacement plan, never silently choose another range. Repeated use within the same declared CRN design is intentional.

Budget:

| Stage | Maximum full trajectories |
|---|---:|
| Three paired harness-versus-production smoke checks | 6 |
| 128 initial + 8×48 adaptive vectors, K=5 | 2,560 |
| At most 10 fresh confirmations, K=20 | 200 |
| Total scientific/smoke maximum | 2,766 |

Unit/full-suite test execution is additional engineering work, explicitly recorded separately; do not claim the table is a cap on every simulation instantiated by tests. Never add hidden pilot/stock-replay runs. Capture needed summaries during scheduled evaluations.

Default workers=4; enforce a maximum of 8. Use smoke timings to print estimated wall time and throughput, clearly an estimate. Set a 6-hour wall-time cap per search invocation as a computational guard. On reaching it, stop dispatch, finish bounded in-flight work and mark PARTIAL_TIME_LIMIT, so elapsed time may exceed the dispatch cutoff. Checkpoint all progress; do not reset the total simulation budget on resume. No automatic extension/restart. Fail on simulator exceptions or invalid/nonfinite outputs, preserving partial evidence; do not hide such results by substituting other candidates.

No early stopping for an apparently good discovery fit or an empty accepted set. Complete the declared batches unless a runtime, integrity, or validity failure intervenes. A partial run cannot be reported as completed negative evidence.

## 7. Confirmation and decisions

Freeze a shortlist before fresh simulations: up to five discovery primary-pass candidates ranked by J; then fill to ten with remaining lowest-J candidates. If fewer than five pass, fill the balance with near misses. No requirement to find ten passers before proceeding. Deduplicate and freeze all selections. Confirmation may therefore reveal that a near miss passes; label selection provenance accurately.

Run each shortlisted vector on the 20 fresh seeds once; score the confirmation replicates separately from discovery. Do not pool seeds to rescue a result, choose a favorable subset, or iteratively replace failed candidates. Report paired discovery/confirmation means, variances, each target's score, binding targets, and variance stability. Reuse existing cross-validation extraction where available, with missing outputs disclosed. Cross-validation is not silently added to or removed from the frozen 15-target rule.

Outcomes:

- One or more confirmed joint primary-pass candidates: feasibility demonstrated under the stated proxy/budget, followed by cross-validation and HTA-relevance assessment. These ten selected candidates are not a representative uncertainty ensemble or a ready HTA baseline.
- Search improves residuals but confirmation fails: report the best compromises and repeated signed conflicts, and that no adequate candidate was confirmed within this budget.
- No improvement: report the same bounded conclusion with coverage, invalid proposal, fallback and boundary-hit diagnostics. Do not infer global impossibility.

At the end, return a single decision report. Do not launch a second search, widen scientific bounds, or add mechanisms automatically. A later comparator must name the remaining mismatch and distinguish observation ambiguity, constraint sensitivity, temporal transmission flexibility, and initialization changes. Keep unresolved AIDS mapping visible; a good numerical fit cannot validate its definition.

## 8. Implementation artifacts and CLI contract

Create:

- `coupled_calibration_search.py`: pure parameter/proposal/archive/objective/budget logic.
- `run_coupled_calibration.py`: validated production runner and checkpoint orchestration.
- `configs/coupled_search_plan_20260918.json`: this design in machine-readable form.
- `tests/test_coupled_calibration_search.py`: scientific contract and proposal tests.
- `tools/review_coupled_calibration.py`: independent read-only recomputation from raw outputs.
- `documentation/archive/historical_workstreams/calibration/COUPLED_SEARCH_RUNBOOK.md`: tested commands, status and limitations.

Modes: no arguments and `--validate-only` perform zero engine construction/simulations and write nothing. `--prepare` writes a new frozen preparation directory, zero simulations. `--smoke` uses that preparation, writes a separate directory. `--run-search` requires a verified smoke artifact matching the current preparation/code; writes another directory. `--confirm` accepts only a COMPLETE search and creates a new directory. `--resume` resumes only a matching partial search and verifies hashes, RNG/checkpoint state and consumed budget; it must not overwrite completed artifacts. Ordinary modes reject nonempty output directories with a short actionable message, not an opaque traceback.

Manifest/status files must distinguish PREPARED, SMOKE_PASSED, SEARCH_COMPLETE, CONFIRMATION_COMPLETE, PARTIAL_TIME_LIMIT and FAILED. Only completed stages receive corresponding final reports. Save raw seed outputs atomically or append with recovery-safe journaling, candidate params and injected-config hashes, objective/acceptance tables, frozen target budgets, full RNG state, launch/finish counts, dependency hashes, environment, command, git dirty status, and error records.

Store annual available flow summaries and start-of-year 1990/2000/2010/2025 stock summaries by orientation × HIV stage × diagnosis × ART during scheduled runs. Store axis labels and population denominators. For confirmation also save the full 2025 adult/child tensors with explicit `restart_safe:false`; these are diagnostic states, not complete restart checkpoints. A passive observer must consume no RNG or mutate state and must pass paired equivalence checks. Missing desired diagnostics are implementation work, not permission to change the model's outcomes.

## 9. Required tests and engineering gates

Read existing functions before adapting them: `inject_params`, `_with_derived_parameters`, `_valid_draw_mask` in `run_lhs_calibration.py`; `_score_matrix`, `pointwise_predictive_variance`, `predictive_variance_stability` in `run_history_matching.py`; register/budget/implausibility modules. `run_joint_fit_comparison_20260917.py` supplies provenance, seed and stochastic-path examples. Its fixed experiment task/seed assumptions are not the new search contract. Read current files; do not blindly call a loader that freezes the wrong experiment.

Run the full suite before and after code changes, comparing exact failure/error names. Earlier records report existing failures; establish the current baseline rather than treating those records as a fresh test run. Any new relevant failure blocks launch. The known deterministic diagnosis multiplier-one issue must remain explicitly disclosed; establish the actual stochastic enabled path by smoke comparison.

Tests must cover:

1. No-run defaults and preparation, occupied-directory protection, target count/order, hashes, seed collision, invalid vectors and derived simplex constraints.
2. Objective/acceptance separation: worsening mean fit cannot improve J merely by increasing sample variance; excellent groups cannot hide a failed target in primary acceptance; no sensitivity-only promotion; zero/nonfinite budgets fail.
3. All blocks represented, cross-block proposals, full-domain global quota, retained near misses with zero passers, deterministic proposals independent of worker completion order.
4. Synthetic narrow compensating valley with conflicting individual-parameter perturbations but a known interior joint solution. Run a predeclared fixed set of proposal seeds, report success rate and evaluations versus equal-budget constrained random/LHS search. Do not cherry-pick a seed or claim this proves HIV feasibility. Also use a disconnected-region fixture to demonstrate exploration outside warm-start coordinate ranges. If performance is poor, fix/debug on synthetic tasks before the expensive run and record any design revision.
5. Raw-replicate recomputation of ddof=1 variance, primary and sensitivity scores; missing/failed replicate is not an accepted result; confirmation seeds remain separate.
6. Crash/resume matches uninterrupted proposal history and final raw results; no duplicate dispatch or budget reset. Handle in-flight tasks explicitly: consumed launch attempts count even if results were lost; do not silently exceed the cap to rerun them.
7. Three paired production-versus-harness trajectories: all extracted summaries and final adult/child states exactly equal at identical params/seeds, with observer enabled. Exercise at least two warm vectors and the intended stochastic MSM diagnosis path. This is the six-run smoke budget, not six plus hidden repeated gates.

Reviewer recomputes candidate means, J, all target implausibilities and shortlist from raw records; verifies artifact hashes, complete seed matrices and budget counts. It must not just call the same high-level report function and declare agreement.

## 10. Commands Claude must implement and verify

These are REQUIRED FUTURE interfaces, not commands available today. Preparation and each execution stage have distinct directories so the previous overwrite error does not recur. Run from the repository root; choose a new prefix if any execution directory already exists.

```sh
.venv/bin/python run_coupled_calibration.py --validate-only --plan configs/coupled_search_plan_20260918.json
.venv/bin/python run_coupled_calibration.py --prepare --plan configs/coupled_search_plan_20260918.json --output-dir outputs/calibration/coupled_20260918_prepared
.venv/bin/python run_coupled_calibration.py --smoke --prepared outputs/calibration/coupled_20260918_prepared --output-dir outputs/calibration/coupled_20260918_smoke --workers 4
.venv/bin/python run_coupled_calibration.py --run-search --prepared outputs/calibration/coupled_20260918_prepared --smoke-source outputs/calibration/coupled_20260918_smoke --output-dir outputs/calibration/coupled_20260918_search --workers 4
.venv/bin/python run_coupled_calibration.py --confirm --source outputs/calibration/coupled_20260918_search --output-dir outputs/calibration/coupled_20260918_confirmation --workers 4
.venv/bin/python tools/review_coupled_calibration.py --source outputs/calibration/coupled_20260918_search --confirmation outputs/calibration/coupled_20260918_confirmation --output-dir outputs/calibration/coupled_20260918_review
```

Claude's implementation deliverable: tested harness, frozen preparation, synthetic-test report, verified smoke result, and runnable search/confirmation instructions. Do not launch the 2,560-trajectory search merely to test the CLI; Rishi asked for the harness and what to run. If implementation requires a scientific-contract change, return the exact issue and smallest proposed change instead of quietly implementing it. Routine engineering details can be resolved autonomously within this specification.

## Preparation-session verification

Documentation checks only, 18 September 2026: verified all 33 block names occur exactly once and match the live space; the selection has 15 target years; the budget arithmetic is 2,766 maximum scheduled trajectories. The four input files still match their hashes in the focused source manifest. No simulator or test suite was run for this documentation-only change. Runtime dependency verification remains mandatory during implementation.

- `base_config` SHA-256: `c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187`.
- `calib_space` SHA-256: `fce261fe07449f3a17fd50fd72770740f1afc4e9cebd5b40425e836b0793bd5e`.
- `target_register` SHA-256: `87db68409e1a3e0c6ede762b37fd7f8c64008a1ba289b4965eb7a7be645e7a03`.
- `wave_plan` SHA-256: `d622647a1745b90af2dc578e61e7e3bb8a62eadfd1b300025a02d9fa809a70fb`.
