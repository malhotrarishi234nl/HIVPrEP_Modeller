# Bounded investigation: can the existing model reconcile MSM diagnoses and AIDS?

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

## Mission and required endpoint

Determine whether the observed conflict is primarily (a) sparse search coverage, (b) unstable stochastic variance/ranking, (c) an observation or implementation defect, or (d) an unresolved temporal/mechanistic trade-off within the current model and parameter bounds. Multiple causes may coexist.

This is a diagnostic investigation, not permission to produce a final calibration, alter the epidemic model, or manufacture survivors. Finish with one evidence-backed next action and an explicit account of what remains unidentified. An empty tested set is not proof that the entire parameter space is infeasible.

First complete an extensive analysis of existing outputs. Build and test a small conditional experiment only if that analysis cannot answer the question. Do not begin another broad history-matching wave.

## Established starting point — verify before relying on it

- Focused wave: 622 vectors (600 generated + 22 warm proposals), five replicates each, zero recorded errors. Only two primary diagnosis survivors, IDs 600 and 621. The minimum of ten survivors was not met, so wave 2 did not run.
- Fresh confirmation: those same two parameter vectors, 50 new replicates each, seeds 194000–194049. IDs are parameter-vector identifiers, NOT seed identifiers.
- Primary diagnosis Imax: 600 = 3.867894; 621 = 3.461314. Primary joint Imax: 7.831787 and 7.536748. Neither passes joint scoring even at the 20% discrepancy sensitivity (4.191507 and 4.026985).
- Representative confirmed means: MSM AIDS 1990 ≈45/56 versus 318; MSM diagnoses 2000 ≈1364/1410 versus 360; MSM diagnoses 2015 ≈276/261 versus 582. These characterize these two vectors, not all 622.
- Model changes already made: passive AIDS event accounting; ART/off-ART decomposition; two-era effective on-ART progression. No further mechanisms are authorized by this brief.

## Inputs

Paths below are relative to the repository root, `hiv-prep-model/`.

Primary evidence:

- `outputs/calibration/hm_focused_diagnoses_then_aids_20260916/manifest.json`
- Its `wave_01/`: `design.csv`, `design_sources.csv`, `evaluated.csv`, `per_target_implausibility.csv`, `variance_budget.csv`, `variance_contract_stability.json`, `cross_validation/`, and supplementary provenance.
- `outputs/calibration/hm_focused_confirmation_600_621_20260917/`: manifest, selected parameters, raw replicates, confirmed means, scores, trajectory diagnostics, variance stability.
- `configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json`
- `configs/hm_target_register_focused_20260916.json`
- `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json`
- `configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json`
- `run_history_matching.py`, `hm_implausibility.py`, `hm_variance_budget.py`, `hm_target_register.py`, `run_focused_confirmation_20260917.py`, `run_hm_confirmation.py`, `calib_extractor.py`, `aids_observation.py`.

Context, not automatically compatible pooled training data:

- `outputs/calibration/hm_msm_aids_diagnostic_20260916_analysis/DIAGNOSTIC_FINDINGS.md`
- `outputs/calibration/art_split_decomposition_20260916/FINDINGS.md`
- `ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md`
- `ECDC_EARLY_HIV_SOURCE_DECISION_2026-09-16.md`
- `HM_FOCUSED_DIAGNOSES_THEN_AIDS_RUN_2026-09-16.md`
- `HM_FOCUSED_CONFIRMATION_2026-09-17.md`

Read applicable AGENTS.md instructions. Record dirty worktree state; preserve unrelated changes. Read scientific source documents only where a specific unresolved definition requires it, rather than restarting the literature review.

## Non-negotiable contracts

1. Preserve the primary maximum-implausibility rule, cutoff 3, target-relative discrepancy 0.1, and predictive-local simulation variance `s2(x) + s2(x)/K`, where `s2` denotes sample variance, not standard deviation. Verify the exact code convention for discrepancy before writing its formula. Keep 0.2 as sensitivity only. Do not resurrect legacy global variance/V-over-K scoring.
2. Keep all 15 joint targets: SHM MSM diagnoses in 1996, 2000, 2004, 2010, 2015, 2019, 2024; RIVM MSM AIDS in 1990, 1995, 2000, 2005, 2010, 2015, 2019, 2022.
3. Primary zero observation variance means unavailable/underivable uncertainty under the current contract, NOT proven error-free surveillance. Report that limitation; do not invent replacement uncertainty.
4. Keep early ECDC HIV and non-MSM AIDS held out. Do not silently promote them to acceptance targets. Survivor restriction for ECDC is not established merely by analogy with RIVM.
5. No parameter-bound expansion, new ordering constraint, revised target, new presentation layer, economic rerun, or fitted discrepancy. Propose any such changes separately, with evidence and user approval required.
6. Diagnostic mean-error rankings, empirical coverage, and bootstrap frequencies are NOT replacement acceptance rules or posterior probabilities. No automatic NROY region update from this investigation.
7. Never overwrite prior outputs. Do not combine parameter IDs across runs without source-run identifiers and compatible provenance.

## Phase A — provenance, score reproduction, and observability (no new simulations)

### A1. Verify the data lineage

Validate manifest-recorded hashes, row counts, vector joins, seeds, parameter dimension, design source, configuration, target register, and runtime identity. Distinguish an intact historical artifact from compatibility with today's checkout. Hash mismatches do not invalidate the historical result automatically, but block presenting a current-code rerun as identical.

Assert one unique vector per design ID; all 33 parameters present; no silent missing-target drops; every scored value finite; reported K and error counts consistent. For confirmation, verify exactly 50 unique seeds for each of two identical-to-source vectors.

Do not assume the 622-vector output contains individual replicates: inventory what was saved. Aggregate moments cannot reconstruct paired trajectories or a valid replicate bootstrap. Explicitly mark unavailable tests.

### A2. Reproduce scoring independently of the saved label

Using the approved scorer, recover the two wave-1 survivors and the eight confirmation score rows within saved numerical precision (default tolerance 1e-6 if full precision is available). Independently hand-check one target denominator for each confirmed vector, listing every variance component and units. Recompute joint scores for all 622 vectors if the required AIDS outputs and variances were saved; mark this as retrospective diagnostic scoring, not a completed wave 2.

If inputs needed for joint scoring are absent, report exactly what is missing. Do not fill absent variance with zero or a pooled value.

### A3. Audit the observation path narrowly

For each target series trace source definition → register → extractor → counter → annual aggregation. Check MSM eligibility, event timing/year boundaries, first event versus repeated event, and clinical-AIDS proxy assumptions. Check whether AIDS at HIV diagnosis and progression while diagnosed are mutually exclusive for the same event. Use existing tests and small synthetic fixtures to test these identities. Where the representation cannot identify a unique person's first event, state that rather than claiming an untestable identity.

Check that both early/recent ART parameters reach the intended schedule and that diagnosis-era parameters affect their documented years. Prefer existing unit tests; do not run a full simulation just to inspect configuration plumbing.

**Gate A:** if a material score/export/observation implementation defect is found, stop scientific interpretation at the affected boundary, document the smallest reproducer, and request approval for a fix. Do not calibrate around a bug. Nonblocking evidence limitations must be recorded without restarting the entire project.

## Phase B — map the actual conflict across all 622 vectors

### B1. Build a target-level residual and variance table

For every vector and target export: source, ID, observed count, simulated mean, signed count error, signed relative error `(mean-target)/target`, sample SD, Monte Carlo SE `SD/sqrt(K)`, each variance component, component fraction of total variance, implausibility, and binding-target rank. Handle nonpositive targets explicitly if encountered.

Keep predictive spread distinct from uncertainty in the mean. A large simulation SD is not evidence that the mean trajectory agrees with data.

### B2. Quantify shape, without introducing new acceptance criteria

Compute maximum absolute relative error and median absolute relative error separately for diagnosis, AIDS, and the joint block. Also report signed residuals for these prespecified periods:

- Early AIDS: 1990 and 1995.
- Middle diagnosis: 1996, 2000, 2004.
- Later diagnosis: 2010, 2015, 2019, 2024.
- Later AIDS: 2000, 2005, 2010, 2015, 2019, 2022.

At descriptive ±10%, ±20%, and ±30% mean-error windows, count how many vectors meet each block and each block intersection. These are diagnostics only. Report over- and under-prediction counts for every anchor, not just the two confirmation candidates.

Construct a Pareto frontier minimizing diagnosis versus AIDS maximum absolute relative error. Show whether any vector improves both blocks relative to each confirmed candidate's original five-seed estimates; do not treat comparisons of K=5 and K=50 as equally precise. Report the frontier separately for generated and warm proposals.

Use three compact plots: signed-residual heatmap for a transparently selected shortlist; diagnosis-error versus AIDS-error scatter; annual trajectories for the shortlist with observed anchors and correctly labelled uncertainty. Never draw confidence bands from missing raw data.

### B3. Test whether low Imax is partly a variance-ranking effect

Compare primary Imax ranking against mean-error ranking, report rank agreement and rank reversals, and display the simulation-variance fraction for the binding targets of the best ten under each ranking. Identify candidates with low standardised error but large absolute errors. Do not call high variance intrinsically invalid: explain what the present predictive contract permits.

For the two K=50 confirmations, resample whole seed-level trajectories within each vector (2,000 bootstrap draws, fixed analysis seed). Estimate the sensitivity of Imax and pass/fail labels to replicate sampling at K=5, 10, 20, 50. Use paired seed indices across the two vectors when estimating between-vector comparisons. State that the empirical 50-seed distribution is itself uncertain; bootstrap pass frequencies are not probabilities of model validity.

Report exact original-five versus fresh-fifty changes in mean, SD and denominator components. Do not infer an actual seed-level mechanism from moments alone.

### B4. Test timing hypotheses and confounding, not just correlations

Read the actual parameter names and assign the 33 parameters to documented mechanism groups: initial burden/stage composition; transmission/mixing; diagnosis timing/era; untreated progression/mortality; ART progression; other. Export the mapping and explain ambiguous assignments.

Screen associations with signed residuals and period summaries. Separate generated design from selected warm proposals; report missingness and boundary occupancy. Rank correlations and optional cross-validated surrogate associations are hypothesis generators only, not causal effects. If using a surrogate, report out-of-sample performance and omit directional claims for poorly predicted outputs.

Answer explicitly:

- Is early AIDS underprediction widespread, or specific to diagnosis-favourable regions?
- Can any sampled vector reduce the 2000 diagnosis excess while increasing 2015 diagnoses?
- Does improving early AIDS systematically worsen diagnosis fit in the sampled design?
- Are the relevant candidates pressed against bounds, and which ones?
- Does ART-era progression plausibly explain a pre-ART 1990 mismatch? Respect model chronology: do not assign causal effects backwards in time based on correlations.
- Is the observed conflict localized to one target/era, or distributed across targets?

Perform leave-one-target-out and leave-one-period-out rescoring as explanatory diagnostics only; show what would change, but never use target removal to declare calibration success.

## Gate B — written decision before any simulation

Write `EXISTING_EVIDENCE_DECISION.md` with one of these conclusions:

1. Promising joint regions exist in saved outputs: nominate a diverse shortlist for fresh-seed confirmation, not an automatic full wave.
2. A specific parameter-direction hypothesis could resolve a measured trade-off: nominate a bounded controlled experiment below.
3. Evidence is insufficient because critical outputs were not saved: identify the minimal re-evaluation needed.
4. A definition/implementation issue blocks interpretation: stop and request the specific decision/fix.

Support the choice with counts and target-level residuals, not adjectives. Explain why the proposed experiment discriminates between explanations. Freeze the selected vectors, parameters, seed lists, and analysis rules in a manifest before launch.

## Phase C — conditional controlled experiment, maximum 300 simulations

Default to preparing a terminal runner and validate-only command. Run simulations only if the user explicitly authorizes execution when handing off this brief. Never assume this document grants permission to launch a long run. Analysis and unit tests are authorized.

If Gate B supports a mechanism experiment:

- Select up to three distinct anchor vectors: best diagnosis mean fit, best AIDS mean fit, and a balanced Pareto candidate. Deduplicate, then use parameter-space diversity to break ties. Do not select solely by Imax or default to IDs 600/621.
- Choose at most three scalar parameters, justified by B4 and source-code tracing. At each anchor perturb one parameter at a time by ±10% of its declared range, in its declared sampling coordinate (linear/log). Respect all joint constraints; record actual steps; use a documented feasible one-sided step near a boundary. Never silently clip or repair a vector.
- Baseline plus six perturbations = at most seven vectors per anchor; three anchors × seven vectors × ten fresh CRN seeds = at most 210 simulations.
- For each perturbation report paired seed-level changes in early AIDS, 2000 and 2015 diagnoses, all period summaries, and all joint targets. Give paired mean effects and 95% uncertainty intervals, labelling these local exploratory comparisons with no multiplicity-adjusted discovery claim.
- Verify CRN pairing is actually maintained by current execution, and disclose limits from differing event paths. Same seed labels alone do not prove useful coupling.
- Reserve up to 90 simulations for fresh-seed checks of at most three preselected promising evaluated vectors (30 each), OR one clearly specified interaction experiment within that budget. Freeze which branch is used and why before viewing its results. Do not spend the remainder automatically.
- If interactions are the main hypothesis, propose an alternative small factorial design within the same 300-simulation cap instead of pretending one-at-a-time testing establishes joint feasibility. Document and freeze the alternative before execution.

If Gate B supports confirmation only, confirm at most three diverse candidates with 30 fresh seeds each (90 simulations). Do not run the 210-simulation perturbation design unnecessarily.

Use seed ranges verified disjoint from all source/confirmation runs. Independent confirmation seeds must also be disjoint from experimental seeds. Preserve full seed-level annual outputs and available decomposition counters. Do not alter the simulator to add a counter without separately justified approval.

Budget: hard cap 300 new simulations across all pilot/confirmation work, not per stage. Measure runtime with the first few jobs, included in the cap. Set workers explicitly (default 4, user may choose 8); report memory/concurrency concerns rather than promise a two-hour finish. Stop scheduling new jobs if projected total runtime exceeds two hours; preserve completed jobs and report a revised plan. No automatic retry loops or hidden background run.

## Required machinery and tests

Create a reusable analysis script and, only if warranted, a separate experiment runner/manifest. Prefer existing simulation and scoring APIs. Save outputs to a new `outputs/calibration/hm_joint_fit_conflict_20260917/` directory, or a uniquely named successor if occupied.

Automated tests must cover:

1. Hand-computed residuals, sample variance, SE, predictive denominator and Imax; no SD/variance confusion.
2. Join correctness under shuffled rows and rejection of duplicate IDs/seeds.
3. Missing/nonfinite target fail-closed behavior; no implicit target dropping.
4. Pareto frontier on a known fixture; warm/generated source labels preserved.
5. Bootstrap resamples whole trajectories, not independent years; deterministic analysis seed.
6. Perturbations obey ranges, transforms and joint constraints; unvaried parameters unchanged.
7. Paired differences align seed IDs correctly, including shuffled rows.
8. Primary scoring remains unchanged by sensitivity/diagnostic options.
9. Validate-only starts no simulations; occupied output cannot be overwritten; checkpoints retain provenance and completed tasks.
10. Provenance mismatch blocks an allegedly identical rerun; existing result reproduction succeeds to declared precision.

If a component is not implemented because its phase was unnecessary, mark its tests not applicable, not passed. Run relevant existing HM tests; record exact commands and results. Do not assert scientific correctness from unit-test success.

## Deliverables and completion criteria

- `FINDINGS.md`: start with a one-page plain-language decision, then technical evidence. Separate established facts, associations, controlled local effects, and unresolved hypotheses.
- `EXISTING_EVIDENCE_DECISION.md`: frozen Gate B decision, including justification if no new simulations are warranted.
- Machine-readable target residuals, variance decomposition, block/intersection counts, Pareto shortlist, parameter/mechanism mapping, and candidate-selection rationale.
- Three interpretable plots and any additional figure essential to a decision; avoid a large uncurated plot dump.
- Reproducible scripts, tests, manifest/hashes, and terminal commands from the repository root. Commands must create log parent directories before `tee`, use `set -o pipefail`, and offer `caffeinate` for a Mac run. Validate commands without launching the production workload.
- If simulations run: planned versus completed count, failures, elapsed runtime, raw seed outputs, paired-effect table, and unchanged primary/sensitivity scores.

The final recommendation must be exactly one principal next action:

**Focused search justified:** nominate multiple regions, identify why they are promising, and prepare—but do not launch—a separate bounded search proposal. No claim that a good ensemble already exists.

**Specific conflict needs a model/data decision:** identify the exact target, event definition or mechanism, evidence that points to it, alternatives still consistent with results, and the smallest decision required. Do not implement speculative fixes.

**Insufficient information:** state the single most informative missing measurement or controlled comparison and its budget. Do not recommend simply adding more seeds or widening discrepancy without a testable reason.

Success here means resolving what to do next with reproducible evidence. Finding no validated vector can still be a successful diagnostic result. It is not evidence that history matching failed as an algorithm, and it is not permission to declare the model structurally impossible.
