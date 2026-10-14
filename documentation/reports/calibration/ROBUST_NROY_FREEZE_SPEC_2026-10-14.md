# Robust NROY freeze stage

**Status:** DRAFT implementation contract. It does not authorise simulations and does not change the primary `I_max <= 3` acceptance rule.

**Purpose:** determine whether fresh-seed-confirmed parameter vectors have reproducible local and sampled-path support before representative vectors are selected for policy analysis.

## 1. Scientific question and consequence

**Question:** are confirmed accepted vectors supported by stable neighbouring parameter combinations, including thin correlated ridges, or is no such support detected under the declared finite design?

**Decision consequence:** confirmed points remain reported as plausible points. Only vectors with demonstrated local/region support are eligible for representative selection. Points with no local support detected or unresolved evidence do not silently enter the forecast ensemble.

The stage stops when every confirmed point has a completed local assessment, supported groups have enough sampled-path evidence for representative selection, unresolved connections cannot change regional representation, and the M1 `p` profile has no decision-relevant coverage gap.

This is a finite support-detection design, not a proof that the full NROY topology has been recovered. All conclusions are qualified by the declared probes, scales and simulation budget.

## 2. Required inputs and identities

The runner consumes immutable calibration and confirmation artifacts. For every candidate it requires:

- full and normalised parameter vectors, parameter names/order, bounds and constraints;
- proposal origin, batch, candidate id and parameter-vector hash;
- seed-level target outputs, signed residuals, target means/variances, per-target implausibility, `I_max`, J and binding target;
- discovery and confirmation seed identities;
- hashes of code, model config, calibration space, target register, run plan and parent manifests.

The manifest also freezes the canonical float serialisation used for vector hashes, simulator/dependency versions, seed registry, target order, normalisation metric, distance metric and all numerical tolerances. M0 and M1 use the same target contract. Every confirmed M0 vector is represented in M1 at exact `p=0`; an automated multi-vector, multi-seed identity test must show that this boundary executes the legacy path and gives identical scientific outputs before M1 is launched.

No stage may reconstruct a parent from filenames or mutable defaults. Missing, duplicated, reordered or hash-mismatched candidates, parameters, targets or seeds are fatal.

## 3. Local designs and interpretable classifications

For each confirmed point, freeze before simulation:

1. **Ambient probes:** constrained maximin joint perturbations at two declared scales (proposed: 16 at 2.5% and 16 at 5% in the frozen normalised metric). The implementation plan must define whether scale is a radius or coordinate-wise bound and the direction generator; it may not infer either from code defaults. Invalid proposals are redrawn, never clipped. At bounds, feasible-direction coverage and redraw exhaustion are reported so a one-sided neighbourhood is not mistaken for isolation.
2. **Correlation-following probes:** a predeclared number of paired moves derived reproducibly from nearby low-J archive vectors, differences among confirmed vectors and compensating cross-block directions already found by the coupled search. The source pool, neighbour rule, direction construction, scales, rank truncation and quota are frozen before outcomes are evaluated. "Low J" guides proposals but never establishes plausibility.
3. **M1 boundary coverage:** explicit `p=0` points, one-sided local probes at that boundary and recorded coverage over predeclared `p` bins. A bin is descriptive coverage, not evidence that every value in it is plausible. The implementation must not create an `identity_epsilon` exclusion band above zero: the RNG stream changes at any positive `p`, but that affects seed-wise coupling rather than defining a scientifically invalid parameter interval. Positive probes are compared distributionally and are never snapped to zero.

All probe vectors, RNG state, constraints, rejection reasons and distances are written before simulation. Discovery uses five common seeds. Promotion is frozen before execution: a probe is promoted if its nominal five-seed `I_max <= 3` or if rescoring with the predeclared upper confidence bound for process variance gives `I_max <= 3`. The latter is a conservative compute-allocation safeguard against five-seed false negatives, not acceptance. Final acceptance is recomputed nominally from the reserved 20-seed confirmation block, which was not inspected during promotion and is common to the anchor and its neighbours. The variance-stability result remains visible; if the only evidence for or against local support is variance-unstable, the local label is unresolved rather than negative.

Every proposal also records a non-null `kernel_id`, kernel version, scale, direction class, parent candidate ids and pre-/post-constraint vector hash. Declared quotas for ambient, archive-following, cross-block and bridge kernels are checked before dispatch and again after merge. A kernel that exhausts its proposal attempts fails preparation; it may not silently fall back to another kernel or fill its quota with duplicate/global draws.

Only unique vector hashes count as neighbours. The plan declares minimum anchor-neighbour and neighbour-neighbour separation, and correlation-following support must involve the required number of independent direction classes rather than duplicate or nearly collinear moves.

Report, with underlying counts, directions and distances:

- **broadly supported:** at least two distinct neighbours confirm, including an ambient neighbour at the smaller scale;
- **ridge-supported:** at least two correlation-following neighbours confirm but ambient support is absent;
- **no local support detected:** the point confirms but the complete declared design finds no qualifying support;
- **unresolved:** execution, coverage or precision is insufficient to classify.

These are forecast-ensemble eligibility labels, not replacements for epidemiological acceptance. "No local support detected" is not a mathematical claim that the point is isolated. A sparse or directionally inadequate design must be labelled unresolved instead of negative. The report includes the pass fraction by kernel and scale; the two-neighbour rule is a predeclared operational threshold, not a confidence statement.

## 4. Connectivity without Euclidean overclaiming

Euclidean distance is used only to propose candidate edges. It never establishes a region by itself.

1. Build a provisional nearest-neighbour graph in normalised parameter space.
2. Choose a minimal auditable edge set needed to establish components.
3. Freeze interpolation probes at 25%, 50% and 75% of each proposed straight bridge.
4. Evaluate and confirm bridge probes under the same contracts.
5. Mark an edge **sampled-path supported** only when both endpoints and every declared bridge probe confirm. A failed straight bridge is **unresolved**, not proof of disconnection, because a correlated region may curve.

If unresolved connectivity would change which groups receive representatives, run a bounded archive-informed path search. An emulator is considered only if direct paths remain inefficient or ambiguous. Representatives are selected across sampled-path-supported components using parameter/baseline geometry; policy outcomes are unavailable to selection.

The graph construction is frozen in the run plan: neighbour count or candidate-edge rule, tie-breaking, minimal-edge algorithm and the exact rule that all 25/50/75% probes must pass. A three-point bridge supplies discrete evidence along the proposed path; neither "sampled-path supported" nor a graph component claims continuous topological connectivity. This stage reports eligible components and their geometry but does not choose the final number of representatives.

## 5. Handoff, sharding and resume contract

Distributed execution must be scientifically identical to a single-process reference run.

### Frozen work units

Each scientific work unit is keyed by `(stage, model, design_id, seed)`. `design_id` is globally unique within the frozen run and resolves to one vector hash, one kernel and an ordered list of zero, one or multiple parent ids; bridge probes therefore retain both endpoints without inventing a single anchor. Reuse of the same vector by more than one local assessment is represented through links to one scientific result, not duplicate simulations. The unit also contains the complete parameter vector/hash, parent-manifest hash, code/config hashes and expected target schema. The coordinator writes the entire ledger before dispatch. Workers may evaluate only ledger rows assigned to their shard and may not generate proposals, substitute failures or infer defaults.

Shard assignment is a pure deterministic function of the frozen work-unit key and declared shard count. Changing worker count must not change assignments, parameters, seeds or results. Changing shard count produces a new declared execution layout but the merged scientific table must remain identical.

### Worker outputs

Every execution attempt has an `attempt_id`. Every row records status, start/end time, host/process metadata, parameter hash, seed, result hash and a structured failure reason. Writes are atomic. A work unit is complete only when exactly one successful scientific result validates against its ledger row; failed attempts remain in the audit trail and cannot masquerade as results.

### Merge gate

The merge refuses:

- missing or extra ledger keys;
- duplicate keys, even if values agree;
- parameter, seed, schema or parent-hash mismatches;
- mixed code/config versions;
- incomplete/failed rows presented as scientific results;
- silently reordered targets or parameters;
- stage outputs whose declared parent is not the exact completed predecessor.

The merger independently recomputes candidate means, variances, implausibility, promotion and region labels from seed-level rows. It never trusts shard summaries. Single-process and sharded merges must be bit-identical where arithmetic order is fixed and numerically identical under a declared tight tolerance otherwise.

### Stage handoff

Every stage writes a completion manifest and artifact index. The next stage validates both, plus candidate counts/hashes and seed disjointness, before producing any design. No partial stage can become a parent. Promotion and bridge designs are written once and treated as immutable parents.

The handoff audit reconciles every declared kernel quota and follows each child back to its exact parent vector(s). It reports proposed, rejected, dispatched, completed, promoted and confirmed counts by kernel. Count conservation must hold at every transition; a missing kernel family or an unexpected zero-variation coordinate is fatal rather than a scientific result.

### Crash/resume

Launch attempts consume budget. Lost or failed attempts are recorded. An incomplete work unit may be relaunched under the same frozen scientific key only with a new `attempt_id`; its vector and seed cannot change. Resume restores the frozen ledger and design RNG state, never regenerates proposals, never reruns a completed work unit and refuses to overwrite completed artifacts. If the attempt or trajectory budget expires with incomplete units, the stage is partial and cannot become a parent. A completed interrupted-and-resumed run must have the same consolidated scientific table as an uninterrupted reference; only its attempt audit may differ.

## 6. Emulator false-negative safeguards

An emulator is not part of the base freeze stage. It is triggered only by a recorded unresolved geometry question: inefficient tracing of a thin region, important untested gaps, ambiguous `p` coverage or unresolved connectivity that changes representation.

If triggered, it predicts signed target residuals separately, includes predictive and stochastic uncertainty, and is validated on held-out simulator runs enriched near `I=3`. Validation must report false-negative and false-positive classification rates, calibration/coverage and errors by target. Conservative uncertainty is used for proposal generation: the emulator may propose or decline priority, but it may never rule out a region or declare plausibility without simulator evaluation. Failure of the predeclared validation gate retires the emulator and returns the question to direct simulation.

## 7. Correlated-output diagnostic

Multivariate implausibility is secondary and cannot change the primary set in this stage.

Using confirmation replicates, centre outputs within candidate, estimate candidate-specific stochastic covariance with declared shrinkage regularisation, and report effective rank, condition number and dominant correlated directions. If information is combined across candidates, shared CRN seed ids are treated as blocks rather than independent candidate-by-seed rows; naive pooling that creates pseudo-replicates is forbidden. Observation and model-discrepancy covariance are not invented; absent justified covariance, the result is explicitly a correlated stochastic-residual diagnostic rather than a full multivariate acceptance calculation. Compare its ranking with pointwise `I_max` and investigate material contradictions.

Estimation is accepted for reporting only if seed-block bootstrap and leave-one-candidate-out checks show that effective rank and leading correlation directions are reasonably stable. The effective stochastic sample size is the number of independent seed blocks, not candidates times seeds. Otherwise the diagnostic is marked unavailable/unstable; it is never repaired by silently dropping targets or selecting a more favourable covariance estimator.

## 8. Human-readable artifact package

Raw data remain tidy machine-readable CSV/Parquet/JSON with a data dictionary. The package also writes:

- `EXECUTIVE_SUMMARY.md`: question, run status, supported regions, points with no local support detected or unresolved evidence, M0/M1 and `p` conclusions, blockers and next decision;
- `REGION_REPORT.md`: one concise section per sampled-path-supported component with members, parameter ranges/correlations, binding targets and baseline-state ranges;
- `RUN_AUDIT.md`: shard completeness, failures, resumes, hashes, seed use and independent recomputation status;
- `TABLE_INDEX.md`: plain-language description, row unit and important columns for every table;
- a small fixed figure set: normalised region map/projections, local-support plot, bridge graph, `p` profile and target-residual heatmap.

No result is reported only in logs, binary objects or hundreds of per-candidate files. The executive summaries are generated from verified consolidated tables, and every headline links to the underlying candidate/region ids.

## 9. Verification suite

Required before real distributed execution:

### Design and scoring

- normalisation round-trip; exact perturbation scales; constraint preservation; no clipping, duplicates or silent defaults;
- deterministic designs and RNG-state restoration;
- exact per-kernel quotas, scale/direction coverage and parent-child lineage; exhaustion of one kernel cannot invoke another silently;
- every parameter intended to vary does vary in synthetic fixtures, while fixed coordinates remain bit-identical;
- exact reuse of `I_max <= 3`, boundary promotion and explicit `p=0` handling;
- broad basin, thin correlated ridge, point with no detected local support, disconnected basins and failed-bridge synthetic fixtures;
- independent score recomputation from seed-level outputs.
- boundary anchors with one-sided feasible neighbourhoods; insufficient directional coverage must become unresolved rather than negative;
- exact M0 versus M1-at-`p=0` identity across multiple vectors and seeds, plus deliberate failure when any non-`p` input differs;
- arbitrarily small valid positive `p` values remain admissible and are not rejected merely because their RNG streams differ from exact `p=0`;

### Sharding and handoff

- one process versus 2, 3 and uneven shards produce identical merged scientific outputs;
- shuffled arrival order leaves the merge unchanged;
- missing, duplicate, extra, stale-hash, wrong-seed, wrong-parameter and wrong-target-order rows all fail closed;
- shard failure cannot be interpreted as zero output or a rejected candidate;
- confirmation cannot consume discovery summaries in place of raw seed outputs;
- every parent/child candidate hash and count survives discovery -> promotion -> confirmation -> bridges -> regions;
- changing worker count does not change the scientific design;
- each kernel's vector set is identical before and after sharding/merge, including uneven shards and kernels whose quotas are not divisible by shard count;
- interrupted/resumed and uninterrupted runs agree, with lost work explicitly accounted for;
- retrying an incomplete work key changes only `attempt_id`, never vector or seed, and exhausted retries yield a non-parent partial stage;
- completed and partial artifacts cannot be overwritten or used interchangeably.

### Reporting

- every executive-summary number is recomputed from consolidated tables in a test;
- table schemas/data dictionary agree; region ids and candidate ids resolve uniquely;
- the package clearly distinguishes no support, no simulation, simulation failure and unresolved evidence.

A small real-engine smoke test follows the synthetic suite. Production launch requires all gates, an independently verified manifest and a priced trajectory budget.
