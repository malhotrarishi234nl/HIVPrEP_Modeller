# Robust-NROY rejection remediation — 2026-10-14

> **Current-authority notice (2026-10-15).** This report preserves the
> remediation evidence and transcripts for the revision audited on
> 2026-10-14. It is not the current readiness verdict. The authoritative
> package is
> `robust_nroy_readiness_2026-10-15/README.md`. The current unsigned plan has
> SHA-256
> `71152198b3c9d5173a629b6babf77948553a6c7ae188cb12759c5ca76596ad55`
> and 45 unsigned decisions (39 proposal-backed and 6 evidence-dependent),
> plus the separate signature gate. Code acceptance for the integrated
> revision requires a fresh guarded full-suite and clean-clone handoff audit;
> production readiness remains **NOT READY**. Later uses of “current,” the
> 43-decision count, plan hash `e3835812...`, and `Code acceptance: ACCEPT`
> below are historical claims about the audited revision, not claims about the
> current tree.

## Verdict

- **Code acceptance:** **ACCEPT** after independent third-pass reconstruction,
  direct inspection, adversarial mutation testing and remediation of the
  additional findings below. This accepts the fail-closed implementation, not
  the absent scientific inputs or any proposed production decision.
- **Production readiness:** **not ready**. The production plan contains 43
  unsigned decisions: 29 proposal-backed and 14 requiring new decisions, plus
  a separate three-field signature gate. The owner selected Option B and a
  hash-bound V13.22-on-1980 stochastic M0/M1 recalibration candidate is now
  prepared, but it is deliberately unfitted, unvalidated, and not production
  eligible. A new calibration and independent fit review remain mandatory.
  The repository has strict validators and generators for the remaining
  evidence, but no real approved acceptance package, complete seed audit,
  identity result, measured pilot, priced budget, or independently confirmed
  positive-`p` M1 evidence exists yet.
- **Production simulations run during remediation:** none.

Validation transcripts below are preserved as dated evidence and may show
earlier decision counts or configuration proposals. The current plan has 43
unsigned decisions (29 proposal-backed, 14 without proposals): the
non-load-bearing graph-neighbour-count decision was removed, the direct
one-sided `alpha=0.05` variance rule received an explicit proposal, and the
authoritative target-register content hash became a required decision. Earlier
references to deterministic or 1975 production proposals are historical. The
current Option B candidate closes the mechanical backbone choice only; it does
not replace recalibration or scientific acceptance.

## Closed review findings

| # | Finding | Remediation and fail-closed evidence |
|---:|---|---|
| 1 | Caller-controlled promotion/confirmation lineage | Both stages derive the exact promoted IDs and vector hashes from the validated parent; promotion is allocation-only and accepts neither anchors nor archives. |
| 2 | Seed counts not enforced | The contract requires exactly five discovery seeds and 20 fresh, disjoint confirmation seeds. |
| 3 | Partial stages could be scored/classified | Partial merges retain seed rows only; scoring and classification require a complete ledger. |
| 4 | Resume overwrote history | Attempt state is append-journaled and shard snapshots are unioned rather than replaced. |
| 5 | Results not bound to successful attempts | Merge requires exact attempt ID, result hash, code/config hashes and parent-manifest hash. Durable per-unit result records close the post-success/pre-snapshot crash window. |
| 6 | Budgets did not govern global dispatch | A locked SQLite dispatch index enforces plan-wide attempts, unique trajectories and wall-clock start time across shards and resumes; the journal remains the source of truth. |
| 7 | Production prerequisites were documentary | `validate` and the production evaluator share the same prerequisite validator and reject missing, failed, stale or plan-mismatched artifacts. |
| 8 | Parent acceptance identity was mutable/optional | Parent identity is mandatory for every child, covered by the artifact index and validated before evaluate, merge and classify. Acceptance checks precede scientific output writes. |
| 9 | M0-to-M1 embedding was unwired | M1 discovery requires a complete M0 confirmation and automatically embeds every nominally confirmed M0 vector at exact `p=0`; independently confirmed positive-`p` M1 anchors are retained by a deterministic union, with ambiguous IDs or duplicate vectors refused. |
| 10 | Provenance was incomplete | Work units bind the plan, base config, calibration-space contents, model bounds and expanded scientific code surface. |
| 11 | Deterministic production config was accepted | Production readiness rejects `deterministic_mode=true`. |
| 12 | Neutral MSM multiplier was not an identity | Multiplier `1.0` now dispatches through the exact legacy all-orientations allocation and RNG key. |
| 13 | DX-04 disposition was unsupported | Acute and chronic now share one non-AIDS allocation block per population. A 24-step cumulative test requires exact equality to the combined-block reference. |
| 14 | Identity gate could go stale | The gate hashes its expanded code dependency set and the actual base config; M1 preparation requires that hash to match the signed plan's base config and checks the injected routine-hazard value. |
| 15 | Claimed routes were ineffective/incomplete | Shared result keys are evaluated once; coordinate and post-merge count audits are CLI-reachable; graph endpoints require independent confirmation; region reports include ranges, correlations and binding targets (or explicit unavailability); bootstrap multiplicity and residual scoring are corrected. |
| 16 | Hygiene/report claims were inaccurate | The two validation JSONs are explicitly unignored for retention, stale test/source claims are removed, and the repository inventory reflects this remediation. |

## Second-pass rejection findings

| # | Blocking finding | Remediation and invariant |
|---:|---|---|
| 1 | Post-merge evidence could be changed and re-indexed | The manifest now seals a non-circular artifact index. Classification, diagnosis and reporting validate every indexed scientific input before reading it; complete merges are immutable; rewriting the index cannot bless a changed score table. Partial stages remain resumable. |
| 2 | M1 discarded independently confirmed M1 anchors | M1 discovery uses the union of positive-`p` M1 evidence and confirmed M0 vectors embedded at exact zero. IDs, hashes and source counts are recorded; ID/hash ambiguity and duplicate-vector quota inflation fail closed. |
| 3 | Contradictory identity certificates authorised M1 | Gate validation derives the verdict from four unique, strictly Boolean check rows and reconciles exact counts. A top-level `passed=true` cannot override a failed or missing check. |
| 4 | Stale durable results were laundered under current provenance | Durable rows have an exact schema including target order and result hash, and must match the frozen unit plus their original successful attempt. The worker never reduces an unverified row to bare values and rewraps it. |
| 5 | Crashed `RUNNING` attempts never became `LOST` | A full-lifetime shard lock proves prior ownership ended; only that shard's unterminated attempts transition to `LOST`, keep their budget charge, and receive a new attempt ID on retry. |
| 6 | Scientific fingerprints omitted engine dependencies | Prepared work and gate evidence now include the production engine, configuration, transmission, demography, migration, scheduling, diagnosis, AIDS observation, output and target-register dependencies. The scientific config hash also binds the target-register file. |
| 7 | Validation/gate bypassed the production config route | Readiness requires typed evidence schemas rather than generic `passed` placeholders and calls `ProductionEvaluator.config_for` for an M1 work unit. The identity command requires a signed plan and constructs complete M0/M1 vectors through that same route. |
| 8 | Bridge work was audited against quota zero | Every observed kernel family requires an explicit quota. Bridge quota comes from the frozen bridge outcomes; anchor-control and allocation-only quotas are recorded explicitly. |
| 9 | Journal/SQLite corruption was unrecoverable | Only an unterminated final JSONL fragment is repairable; durable/interior corruption is fatal. SQLite is rebuilt atomically from the journal on every worker start because it is an acceleration cache, not evidence. |
| 10 | HTA links failed open and exceeded declared bounds | Links must name an earlier active direct Beta/Gamma draw; missing, forward, fixed, excluded, nested, self and cyclic links fail. Source support must lie inside target support and runtime values are checked without clipping. The three HTA configs now declare the linked AIDS-on-ART upper bound as `1.0`, matching its source. |

### Additional sweep corrections

- Every stage consumer is bound to the exact plan hash frozen in its manifest;
  detailed acceptance-identity errors are emitted first where applicable.
- Merge and classification preserve the existing parent-manifest hash when
  resealing a stage, so lineage is not erased by a legitimate lifecycle step.
- Seed-result rows now persist target order explicitly rather than inferring it
  from JSON key order.
- Report packages are treated as reproducible presentation derivatives and do
  not invalidate a later parent handoff; the source tables they consume remain
  sealed.

## Independent third-pass findings and remediation

The third pass reconstructed the command lifecycle from plan loading through
reporting and did not treat the earlier test counts as proof. Findings are
ranked by the consequence that existed before this pass.

### High 1 — stale pre-lock ledger could erase another shard's reservation

- **Root cause:** `command_evaluate` loaded the durable ledger before taking
  the global dispatch lock, then rebuilt the SQLite cache from that stale
  snapshot after another shard could have appended an attempt.
- **Consequence:** the cache could forget a consumed global attempt/trajectory
  and permit budget oversubscription or duplicate dispatch.
- **Affected path:** `run_robust_nroy_freeze.py::command_evaluate` and
  `nroy_execution.py::rebuild_dispatch_index`.
- **Fix:** reload the authoritative journal/ledger inside the global lock
  immediately before every cache rebuild and recompute completed/assigned
  state from that locked snapshot.
- **Regression:**
  `test_dispatch_cache_rebuild_reloads_journal_after_prelock_snapshot` creates
  the exact stale-snapshot interleaving.
- **Remaining limitation:** SQLite remains process-local infrastructure; this
  design assumes all shards see the same lock-capable filesystem, which the
  production machine profile must confirm.

### High 2 — a partial merge sealed away legitimate resume growth

- **Root cause:** a `PARTIAL` merge indexed journals, shard snapshots and
  durable results as if the stage were terminal. Resume then changed those
  files and the next merge rejected the legitimate growth.
- **Consequence:** interrupted work could not complete without discarding its
  audit history or bypassing the seal.
- **Affected path:** `nroy_execution.py::is_mutable_stage_runtime_path` and
  `run_robust_nroy_freeze.py::_validate_stage_artifacts` / `command_merge`.
- **Fix:** retain exact validation for immutable scientific/preparation files
  while permitting only the enumerated runtime surface to grow during a
  partial stage. A complete merge still seals the exact inventory.
- **Regression:**
  `test_a_partial_merge_remains_resumable_and_can_be_merged_complete` exercises
  prepare, partial merge, resume and final complete merge.
- **Remaining limitation:** runtime mutability is deliberately path-based; a
  new runtime artifact must be explicitly classified or it will fail closed.

### High 3 — parent stages could be interpreted under another signed plan

- **Root cause:** parent checks compared model, stage, target and seed
  contracts but not the exact `plan_sha256` in every downstream command.
- **Consequence:** a scientifically complete parent could be reused under a
  different signed design while retaining superficially compatible fields.
- **Affected path:** `run_robust_nroy_freeze.py::_assert_plan_matches_manifest`,
  prepare, merge, classify, regions and resume.
- **Fix:** compare the current plan hash with the manifest's frozen plan before
  reading or writing scientific outputs. Detailed acceptance-input differences
  are still reported first where they are more informative.
- **Regression:**
  `test_child_refuses_a_complete_parent_from_a_different_signed_plan` plus the
  classify/regions plan-drift tests.
- **Remaining limitation:** none in the local stage contract; legitimate plan
  changes require a new prepared lineage.

### High 4 — “independently confirmed” positive-`p` M1 anchors were only a CSV label

- **Root cause:** M1 discovery required positive `p` after the second pass but
  accepted any caller-supplied anchor table without confirmation evidence.
- **Consequence:** unconfirmed M1 vectors could be presented as confirmed,
  inflate per-anchor quotas and determine the M1 support search.
- **Affected path:** `run_robust_nroy_freeze.py::command_prepare` and
  `_validate_m1_anchor_evidence`.
- **Fix:** M1 discovery now requires a typed
  `robust_nroy_m1_anchor_confirmation/2.0.0` artifact. It must bind the exact
  plan, anchor CSV, scientific code/runtime, production config, target register
  and order, reserved 20-seed confirmation block, and every candidate ID,
  vector hash, positive `p`, replicate count, recomputed nominal `I_max` and
  Boolean primary-rule pass. Each candidate also supplies typed seed evidence
  with the complete M1 vector, one unique complete finite target-output record
  per reserved seed, and the exact frozen acceptance-input identity. Aggregate
  scores are cross-checks only. The validated evidence and seed artifacts are
  copied into and sealed with the prepared stage.
- **Regression:**
  `test_m1_anchor_confirmation_evidence_is_typed_and_reconciled` mutates a
  generic pass file, plan, seeds, complete vector, replicate count, target
  outputs, aggregate override and primary verdict; the union and strict-positive
  tests cover valid M0-at-zero plus M1-positive composition.
- **Remaining limitation:** no real production evidence exists. The test
  artifact identifies itself as fabricated and cannot satisfy a production
  scientific handoff.

### High 5 — runtime changes were absent from scientific identity

- **Root cause:** code fingerprints covered repository files but not Python,
  NumPy, pandas, SciPy, operating-system or architecture versions.
- **Consequence:** prepared work or an identity certificate could survive a
  dependency/runtime change capable of altering RNG or floating-point results.
- **Affected path:**
  `nroy_identity_gate.py::scientific_runtime_fingerprint`, gate validation,
  `run_robust_nroy_freeze.py::_code_sha256` and stage manifests.
- **Fix:** bind the scientific runtime fingerprint into both work fingerprints
  and identity-gate evidence and reject a stale environment.
- **Regression:**
  `test_certificate_from_a_different_scientific_runtime_is_stale`.
- **Remaining limitation:** native-library build flags are represented through
  installed versions/platform, not a full container-image digest; production
  should retain the declared machine/environment image alongside the run.

### High 6 — syntactically valid journals admitted impossible histories

- **Root cause:** JSONL parsing checked syntax but did not reject a second
  terminal state, cross-shard reuse of an attempt ID, vector drift, orphaned
  attempts or terminal rows that disagreed with the running row.
- **Consequence:** a corrupted or manually edited audit trail could replace a
  successful attempt with a failure or make result provenance ambiguous.
- **Affected path:** `nroy_execution.py::Ledger.load`,
  `_assert_attempt_audit`, `record_attempt` and `save_attempt_record`.
- **Fix:** journals are authoritative, snapshots must agree, and only one
  `RUNNING -> SUCCESS|FAILED|LOST` transition (or one `LINKED` record) is
  legal per attempt. Immutable identity fields, times and result hashes are
  reconciled; ambiguous histories are fatal.
- **Regression:**
  `test_semantically_ambiguous_terminal_journal_transition_is_fatal` and the
  18-test recovery suite.
- **Remaining limitation:** deliberate loss of the whole shared filesystem is
  outside application-level recovery and belongs in infrastructure backup.

### High 7 — shard ownership proof was not stable across resume

- **Root cause:** callers could resume the same stage with a different `--of`
  value, changing shard ownership while prior locks/journals used the old
  layout.
- **Consequence:** “prior ownership is gone” was not provable and a running
  attempt could be marked lost by a worker from a different partition.
- **Affected path:** `run_robust_nroy_freeze.py::command_evaluate` and frozen
  `execution_layout.json`.
- **Fix:** the first dispatch freezes shard count, ledger hash and plan hash
  under the global lock; every later worker must match it exactly.
- **Regression:** `test_resume_refuses_a_different_shard_layout`.
- **Remaining limitation:** changing shard count requires a newly prepared
  stage rather than repartitioning an in-flight stage.

### Medium 8 — durable renames did not fsync their directory

- **Root cause:** atomic replacement flushed file contents but not the parent
  directory entry.
- **Consequence:** a host crash in the rename window could lose an apparently
  completed result or attempt journal entry.
- **Affected path:** `nroy_execution.py::atomic_write_text` and
  `Ledger.save_attempt_record`.
- **Fix:** fsync the target/attempt directory after durable replacement or
  append.
- **Regression:** exercised throughout the crash-window and resume recovery
  tests; persistence calls are monkeypatched to verify the boundary.
- **Remaining limitation:** durability still depends on the filesystem honoring
  POSIX fsync semantics, another machine-profile requirement.

### Medium 9 — resume and analysis commands read before validating the seal

- **Root cause:** resume did not validate the artifact index, and classify or
  diagnose could construct stage context before seal validation.
- **Consequence:** mutated scientific state could influence a resume decision
  or analysis before the command rejected the stage.
- **Affected path:** `command_resume`, `command_classify`, `command_diagnose`,
  `command_regions` and `command_merge` in `run_robust_nroy_freeze.py`.
- **Fix:** validate the correct complete/partial seal before loading ledger,
  scores or scientific tables; acceptance and exact-plan checks also precede
  output writes.
- **Regression:** artifact mutation, deleted parent-identity and re-index
  adversarial tests across merge/classify/diagnose/report/resume.
- **Remaining limitation:** report packages remain deliberately derivative;
  regenerating presentation does not alter the sealed scientific source.

### Medium 10 — non-finite inputs and malformed signed numerics could pass comparisons

- **Root cause:** observed values were not checked for finiteness, and several
  plan checks relied on inequalities under which `NaN` is false in both
  directions. Several positive counts/scales were only rejected later.
- **Consequence:** `NaN` could propagate into scores/labels, while zero or
  infinite design settings could produce vacuous or nonterminating work.
- **Affected path:** `nroy_contract.py::FrozenPlan.load` / `validate` and
  `run_robust_nroy_freeze.py::command_merge`.
- **Fix:** enforce finite observed values, finite positive base variances,
  ranges/scales/separations/wall time, and positive structural counts at plan
  load. The primary discrepancy is fixed to the repository's declared 0.10
  rule; supplied `B_i` must already include it and it is not added twice.
- **Regression:** expanded `test_cross_field_validation_fails_closed`,
  `test_plan_may_not_change_the_primary_discrepancy_rule`, and merge-input
  mutation tests.
- **Remaining limitation:** the runner verifies the exact `B_i` file, values
  and lineage hash but does not itself reproduce its observation/discrepancy
  component derivation. Real production therefore still needs a retained,
  reviewable variance-budget artifact; this is an external readiness blocker,
  not permission to invent one.

### Medium 11 — recovery/cache writes and completion evidence could diverge

- **Root cause:** result/cache updates occurred across several persistence
  boundaries and a repeat command could encounter a durable result before its
  shard CSV or SQLite completion bit.
- **Consequence:** stale results could be rewrapped, a completed unit could be
  relaunched, or the audit could disagree with the scientific row.
- **Affected path:** durable result loading, `seed_result_from_durable_row`,
  dispatch cache reconstruction and merge in `nroy_execution.py` and
  `run_robust_nroy_freeze.py`.
- **Fix:** accept a durable row only through its exact frozen unit and original
  successful-attempt proof, reconstruct SQLite from the journal, and link
  shared results without changing their provenance.
- **Regression:** stale-result, corrupt-cache, post-success crash and repeated
  resume tests in `test_nroy_execution_recovery.py`.
- **Remaining limitation:** none beyond the shared-filesystem assumptions
  already stated.

## Verification

- Remediation-focused guarded suite: **503 passed, 17 skipped**.
- Neutral-route/A-12 follow-up: **47 passed, 4 skipped**.
- Targeted M1 embedding, lineage, result-durability and resume regression set:
  **5 passed**.
- Python compilation and `git diff --check`: passed.
- Production-plan validation: exit 2, **43 unsigned decisions**, no engine run.
- Second-pass focused NROY/HTA/AID-observation sweep: **396 passed, 4 skipped**.
- Final current-tree lifecycle rerun (recovery, graph/report and pipeline stages):
  **162 passed**. The production-adapter distinction check also passed:
  M0 removes the stage-structured M1 diagnosis block and M1 injects it through
  the same route used at launch (**2 passed**).
- Dedicated execution-recovery adversarial suite: **16 passed**, including
  truncated-journal recovery, `RUNNING -> LOST`, corrupt-SQLite rebuild, stale
  durable-result refusal and post-merge re-index attacks.
- Identity artifact adversarial suite: **11 passed**; real-engine identity suite
  from the delegated check: **18 passed**.
- HTA validator/sampler sweep: **67 passed**; broader HTA/PSA-HTA sweep:
  **199 passed, 25 skipped**. All three HTA configs validated, and 4,000 draws
  per config preserved exact link equality within the corrected support.
- Repository-wide guarded suite: **2488 passed, 71 failed, 270 skipped,
  5 errors**. That run exposed one stale DX-04 assertion; its correction passed
  in isolation, leaving a pytest last-failed inventory of **70 failures and
  5 setup errors**. Those residuals are the known missing-input/frozen-artifact
  clusters described below, not a green full suite.

### Third-pass verification on the final current tree

All commands below were run from the repository root. No command used the
production evaluator or launched a production simulation.

```bash
.venv/bin/python -m pytest \
  tests/test_nroy_contract_design.py \
  tests/test_nroy_execution_sharding.py \
  tests/test_nroy_execution_recovery.py \
  tests/test_nroy_identity_gate_artifact.py \
  tests/test_nroy_graph_report.py \
  tests/test_nroy_pipeline_stages.py \
  tests/test_hta_config_validator.py \
  tests/test_hta_sampler.py \
  tests/test_aids_observation.py -q
```

Result on the final tree: **418 passed in 161.92 s**.

```bash
.venv/bin/python -m pytest -p pytest_no_engine_guard -q
```

Result in the third-pass broad run: **2554 passed, 70 failed, 270 skipped,
5 errors in 454.90 s**. The subsequent final change only added the M1-evidence
file to the already tested immutable preparation set; the final 418-test
focused rerun above includes that mutation test.
The 70 failures and five setup errors are not hidden or called green:

1. **Stale sealed historical searches/confirmations.** The ART geometry,
   forecast readiness, MSM-beta, post-2015, post-2019, recent-mechanism,
   temporal-diagnosis and related read-only audit tests correctly reject
   frozen artifacts whose recorded module hashes no longer match changed files
   including `config_utils.py`, `run_v3.py`, `aids_observation.py`,
   `calib_extractor.py` and `run_coupled_calibration.py`. Resealing those old
   results would falsely bless evidence produced by other code, so this pass
   did not do it.
2. **Missing repository inputs.** LHS/UNAIDS tests lack
   `Validation /HIV Data UNAIDS/ZIP Files/netherlands_estimates_2025.csv`;
   orientation and non-MSM diagnosis tests refer to absent root-level
   `1980_test.json`; validation-loader setup lacks
   `Validation /HMD_NL_Pop.txt` and `Validation /Migration_Net.csv`.
3. **Missing historical documentation.** One scenario regression expects the
   absent `documentation/BACKLOG.md` entry `BACKLOG-25`.

These are repository restoration/reconciliation tasks outside the NROY
remediation. None is fixed by weakening a scientific hash or fabricating an
input.

```bash
.venv/bin/python run_robust_nroy_freeze.py validate \
  --plan configs/robust_nroy_freeze_production.json
```

Result: exit **2**, exact plan hash
`7d113408058fda7832f9037227a6653b0d5f85a286c20dc2c52e230525fd09ea`,
and **43 unsigned required values** (27 carrying proposals and 16 requiring
new decisions). The command is validation-only and ran no engine.

```bash
.venv/bin/python -m py_compile \
  run_robust_nroy_freeze.py nroy_execution.py nroy_identity_gate.py \
  nroy_contract.py nroy_assess.py nroy_report.py config_utils.py hta_sampler.py \
  tests/nroy_fixtures.py tests/test_nroy_contract_design.py \
  tests/test_nroy_execution_recovery.py tests/test_nroy_identity_gate_artifact.py \
  tests/test_nroy_graph_report.py tests/test_nroy_pipeline_stages.py
git diff --check
```

Result: both passed with no output.

## Remaining launch blockers

These require scientific/user decisions or missing external evidence and were
not fabricated during code remediation:

1. Sign all 43 production-plan decisions and the separate three-field sign-off.
2. Recalibrate and independently review the selected Option B 1980 stochastic
   M0/M1 candidate before freezing a production-eligible fitted configuration.
3. Produce a plan-bound `CONFIG_REGISTRY.md` and schema-valid measured-pilot,
   priced-budget and production seed-disjointness artifacts. Placeholder or
   generic `{passed: true}` evidence is deliberately rejected.
4. Produce real typed positive-`p` M1 anchor-confirmation evidence matching the
   exact signed plan, runtime/config/code fingerprints, reserved confirmation
   seeds and candidate-level primary-rule results.
5. Retain and review the production observed-value and component variance-
   budget artifacts proving how each frozen `B_i` incorporates observation and
   the declared 10% model-discrepancy allowance; do not add discrepancy twice.
6. Restore the external validation inputs and reconcile frozen historical
   artifacts before treating the whole repository suite as green.

## Production-readiness continuation — 2026-10-15

No production, identity-gate, measured-pilot or positive-`p` confirmation
trajectory was run. Work was verified in the isolated no-hardlink clone
`/private/tmp/nroy-readiness-audit.HGPa01`; the live dirty tree and all existing
outputs were preserved.

### Re-established state

```bash
.venv/bin/python run_robust_nroy_freeze.py validate \
  --plan configs/robust_nroy_freeze_production.json
```

Still exits 2 with plan SHA-256
`7d113408058fda7832f9037227a6653b0d5f85a286c20dc2c52e230525fd09ea`
and exactly **43 unsigned required values**: 27 proposal-backed and 16 requiring
new decisions. A process-table check found no robust-NROY or multiprocessing
production process.

### External input reconciliation

The three previously missing validation datasets are present in the canonical
sibling `../data/raw/validation/` tree documented by `.gitignore`. Loaders and
diagnostic defaults now resolve that tree rather than the obsolete in-repository
`Validation ` path. The intended current config is the tracked
`configs/1980_test.json`; root-path assumptions were corrected. The full legacy
backlog is absent from Git history, so `documentation/BACKLOG.md` is a provenance
pointer to three preserved editions and the verified BACKLOG-25 entry, not a
fabricated reconstruction. Eleven diagnosis-triangulation PDFs likewise resolve
from the pinned sibling `../literature/` library.

Guarded path/input regression:

```text
66 passed, 14 skipped
```

This includes the acceptance-input generator tests, backlog regression, LHS and
validation-data tests, moved-config tests, and two production-adapter tests.

### Historical artifacts

The remaining stale checks reduce to two source lineages:

- `outputs/calibration/coupled_forecast_windows_20260921_search`: recorded
  code-map hash `dcf0b4f0...beafca`, current `5b03ba70...7c6d7e`; six changed
  scientific modules.
- `outputs/calibration/hm_focused_diagnoses_then_aids_20260916`: recorded
  code-map hash `864bb733...cdc5d`, current `d2e231ef...d169a`; five changed
  scientific modules.

They and their downstream analyses remain historical evidence and are retired
from current-code readiness claims. They were not resealed. Full paths,
module mismatches, consequences and dispositions are in
`documentation/reports/calibration/robust_nroy_readiness_2026-10-15/HISTORICAL_ARTIFACT_DISPOSITION.md`.

### Decision and evidence package

The complete package is under
`documentation/reports/calibration/robust_nroy_readiness_2026-10-15/` and
contains:

- a machine-readable 43-row decision register (verified 27/16 split);
- fail-closed templates for config registry, measured pilot, priced budget,
  seed audit and positive-`p` M1 confirmation;
- exact configuration-route assessment;
- a positive-`p` confirmation execution/evidence plan;
- a gated execution checklist and exact approval list;
- current-source observed values, component variance table and B_i draft bound
  to the current unsigned plan, target register, source workbook, config, space,
  code and runtime.

The acceptance-input generator refuses unsigned final evidence and refuses to
overwrite an existing package. Its pre-sign draft reconstructs
`B_i = V_obs + V_code + V_disc + extra non-process terms`, records target-
relative 10% discrepancy exactly once, and excludes process variance because
scoring adds `s_i^2(1+1/K)` separately. Several V_obs terms remain underivable
and zero, explicitly biasing toward over-exclusion.

### 2026-10-15 verification

Focused guarded NROY/HTA suite plus the new generator tests:

```text
417 passed, 4 skipped in 149.13 s
```

Diagnosis-triangulation suite with its canonical pinned literature root:

```text
46 passed in 175.38 s
```

Repository-wide guarded suite:

```text
2589 passed, 29 failed, 284 skipped, 0 errors in 463.46 s
```

All 29 failures are the two deliberately stale historical lineages above. The
repository-wide suite is therefore not claimed green; the failures are retained
as correct provenance refusals.

Final non-engine hygiene in the isolated clone:

```bash
.venv/bin/python -m py_compile \
  run_robust_nroy_freeze.py nroy_execution.py nroy_identity_gate.py \
  nroy_contract.py nroy_assess.py nroy_report.py config_utils.py hta_sampler.py \
  calib_data_loader.py validation_data_loader.py run_lhs_calibration.py \
  run_lhs_pipeline.py run_psa_hta.py \
  tools/prepare_robust_nroy_acceptance_inputs.py \
  tests/test_prepare_robust_nroy_acceptance_inputs.py
git diff --check
```

Both passed with no output. A final validation rerun reproduced the same plan
hash and exact 43-decision fail-closed result.

### Updated verdict

- **Code acceptance: ACCEPT.** The current fail-closed robust-NROY code and the
  input/evidence preparation additions pass focused verification.
- **Production readiness: NOT READY.** All 43 decisions remain unsigned. The
  Option B candidate is stochastic and M1-capable but requires a new 1980
  calibration and fit review; no complete approved seed audit, identity
  evidence, measured pilot, priced approval, independently confirmed
  positive-`p` M1 anchors, or approved production acceptance package exists.
  The observed/B_i package must be regenerated, reviewed, approved, and frozen
  after sign-off.

## 2026-10-15 final production-readiness pass

The later lock-in pass selected Option B as a recalibration direction, closed
the collaborator, signature, typed-evidence, seed-result, acceptance-lineage,
and signed-prerequisite defects found by an independent adversarial audit, and
classified every in-scope output artifact without deleting or resealing any
scientific result.

Final isolated no-engine verification, with warnings promoted to errors:

```text
2866 passed, 285 skipped, 0 failed, 0 errors, 0 warnings in 461.29 s
```

The current plan SHA-256 is
`e3835812876831af6381e96bc662b75731fe89b35ba261ef79e38e683f181c74`.
Validation exits 2 with exactly 43 unsigned scientific/numerical decisions
(29 proposal-backed and 14 new) plus a separate three-field signature gate.
The Option B candidate validates mechanically through both M0 and M1 operator
routes, but remains unfitted and not production eligible. No identity gate,
pilot, calibration, positive-p confirmation, or production simulation was run.

- **Code acceptance: ACCEPT.** The current no-engine implementation and
  collaborator handoff surface are internally consistent and fail closed.
- **Production readiness: NOT READY.** Recalibration and the real scientific,
  seed, timing, pricing, confirmation, acceptance, and approval evidence remain
  mandatory.

