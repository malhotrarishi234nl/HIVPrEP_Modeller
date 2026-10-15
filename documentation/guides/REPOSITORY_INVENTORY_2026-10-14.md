# Repository inventory and production entry points — 2026-10-14

> **Current-handoff notice (2026-10-15).** Sections 1–8 preserve the bounded
> 2026-10-14 inventory and its then-current measurements. The collaborator
> source-code handoff is now complete at commit
> `68112bba96402dbbf43c4456289f4384b9635cbe`; section 9 records the final
> evidence. Scientific production remains **NOT READY** and the readiness
> package at
> `../reports/calibration/robust_nroy_readiness_2026-10-15/README.md` is the
> current authority. Older counts, paths, and status statements below are kept
> as provenance, not silently rewritten.

Produced by the bounded repository-hygiene pass required by
`documentation/reports/calibration/PRODUCTION_LOCKIN_SPEC_2026-10-13.md` section 4.

**Scope and discipline.** The specification asks for an inventory, clearly
identified production entry points, and the movement of *only demonstrably
superseded* exploratory material into a labelled historical folder, with
ambiguous or still-referenced files left in place and listed for review. It
also states that "age or filename alone is never sufficient evidence that a
file is stale". This pass therefore moved two files and listed everything else.

## 1. What moved

Two files, into `archive/historical_2026-10-14/` (see its `README.md`):

| file | evidence it is superseded |
|---|---|
| `fix_thesis.py` | Zero inbound references. Hardcodes `/sessions/nifty-vigilant-mayer/mnt/Modeller/v11/THESIS_FINAL.docx`, an absolute path from another environment that does not exist here, so it cannot run. Unrelated to model, calibration or economics. |
| `__delete_test.txt` | Zero bytes, zero references, named `__delete_test`, present since the initial commit. |

Nothing was deleted. `archive/` is already excluded from pytest collection by
`pytest.ini` (`norecursedirs`), so the move cannot change test behaviour.

A reference scan of **every** top-level module (searching `*.py`, `*.md`,
`*.json`, `*.ini`, `*.txt`, `*.cfg`) found `fix_thesis.py` to be the only
top-level module with zero inbound references.

## 2. Generated outputs kept separate from source

| path | size | status |
|---|---|---|
| `outputs/` | ~12 GB | git-ignored; run artifacts, one directory per run |
| `logs/` | ~644 KB | generated logs ignored, except two retained validation-evidence JSONs |
| `sbi-logs/` | ~2.9 MB | git-ignored |
| `__pycache__/` | ~5.3 MB | git-ignored |
| `diagnostics/` | ~220 KB | tracked; small diagnostic artifacts, still referenced |

`logs/` held generated run logs and had been picked up by the pre-existing
safety checkpoint commit. Generated contents remain ignored, but
`stage_structured_diagnosis_focused_calibration_20260922/validate_only.json`
and `validate_only_cap75.json` are explicitly unignored so their candidate IDs,
timing/budget estimates and seed-preflight provenance can be retained in a
fresh clone. The files remain on disk. No other generated artifact is mixed
into a source directory. The
only non-source files inside a source directory are
`configs/hm_focused_warm_start_20260916.csv` and
`configs/hm_forecast_windows_warm_start_20260921.csv`, which are warm-start
*inputs* read by the coupled-calibration runner. They are inputs rather than
outputs, so they were left in place.

## 3. Production entry points

### 3.1 Frozen for the production lock-in

| entry point | role |
|---|---|
| `run_robust_nroy_freeze.py` | **Robust-NROY freeze stage.** `validate` / `prepare` / `evaluate` / `merge` / `report` / `resume`. Fails closed while any required decision is unsigned. |
| `configs/robust_nroy_freeze_production.json` | The production run plan. **Deliberately unsigned**; every required decision holds the `UNSIGNED` sentinel. |
| `run_coupled_calibration.py` | Coupled joint calibration search (the established production calibration runner). |
| `run_history_matching.py` | History-matching wave loop and scoring. |
| `run_hm_confirmation.py` | Fresh-seed confirmation. |
| `run_scenarios.py` | Scenario matrix. |
| `run_psa_hta.py` | PSA / HTA. Note decision 4 of the production spec: `msm_versatility` and the stage multipliers are fixed and excluded from ordinary PSA, and the existing PSA artifact "can't be used in text". |
| `run_v3.py` | `SimulationEngineV3` — the simulator itself. 162 inbound references; the single most depended-on module. |

### 3.2 Supporting library modules (imported, not invoked)

`nroy_identity_gate.py` is listed separately below: it is the only module in
this stage that constructs an engine.

`hm_implausibility.py` (acceptance arithmetic, `I_max`, cutoff 3.0),
`hm_target_register.py` (target register), `hm_variance_budget.py` (variance
budget), `calib_scorer.py`, `calib_distance.py`, `counter_based_rng.py`
(counter-based RNG, keyed per process name), `coupled_calibration_search.py`
(parameter space, blocks, canonical hashes, seeds, `ContractError`),
`economics.py`, `transmission_corrected.py`, `demography.py`.

New: `nroy_contract.py`, `nroy_design.py`, `nroy_execution.py`,
`nroy_graph.py`, `nroy_assess.py`, `nroy_report.py` and
`nroy_identity_gate.py`.

`nroy_assess.py` is the step from merged rows to labels: evidence states,
local support, `p` coverage, graph glue and the correlated-output diagnostic.

`nroy_identity_gate.py` owns the M0/M1-at-`p=0` identity, including its engine
runs. It is the **only** module in this stage that builds an engine.

### 3.2a The stage's subcommands

| command | runs the engine? | produces |
|---|---|---|
| `validate` | no | the unsigned-decision report; exits non-zero while any remain |
| `prepare` | no | the frozen design, ledger, the parent's frozen acceptance-input identity and (for `bridges`) the graph |
| `evaluate` | only with `--evaluator production` | one shard's seed-level results |
| `merge` | no | `seed_results.csv`, `candidate_scores.csv`, stage status |
| `classify` | no | `design_evidence.csv`, `local_support.csv`, `p_coverage.csv` |
| `regions` | no | `edge_verdicts.csv`, `components.csv`, `representatives.csv` |
| `diagnose` | no | the secondary correlated-output diagnostic |
| `report` | no | the human-readable package and figures |
| `resume` | no | what a resume may relaunch |
| **`identity-gate`** | **yes, deliberately** | the M0/M1 identity gate artifact |

`identity-gate` is the one command that runs the engine. Its runs are short
truncated trajectories (about a second each) and nothing is written to
`outputs/`. `prepare --model M1` requires its passing artifact.

### 3.3 Verification entry points

```bash
# Production-relevant suite: everything this stage is responsible for.
# No engine, no trajectories.
.venv/bin/python -m pytest \
    tests/test_nroy_contract_design.py \
    tests/test_nroy_execution_sharding.py \
    tests/test_nroy_graph_report.py \
    tests/test_nroy_pipeline_stages.py \
    tests/test_a12_stage_structured_diagnosis_allocation.py \
    tests/test_a12_deterministic_allocation_audit.py -q
```

```bash
# The M0/M1 identity, as a pre-production COMMAND rather than a test.
# This one runs the engine on purpose and cannot be skipped by a pytest
# plugin. prepare --model M1 refuses to run without its passing artifact.
.venv/bin/python run_robust_nroy_freeze.py identity-gate --out outputs/identity_gate
```

```bash
# The same checks as pytests. SKIPPED under the no-engine guard, which is why
# the command above exists: a green full suite does not establish the identity.
.venv/bin/python -m pytest tests/test_nroy_m0_m1_engine_identity.py -q

# The execution boundary: skips anything that builds a real engine
.venv/bin/python -m pytest -p pytest_no_engine_guard -rs tests/
```

## 4. Left in place and listed for review

These are exploratory in character but **not** demonstrably superseded: each
retains inbound references from tests, documentation or output manifests, and
several are the provenance for results cited in the reports. Moving them would
have risked breaking a reference or orphaning a cited result.

### 4.1 Dated one-off analysis runners

| file | inbound refs | note |
|---|---|---|
| `run_art_interpolation_shape_audit.py` | 1 | only its own test; **lowest-confidence keeper** |
| `run_stage_diagnosis_candidate_baseline_review.py` | 1 | provenance for the 2026-09-22 candidate review |
| `run_followup_knot_and_late_dx_20260929.py` | 2 | provenance for the knot / late-diagnosis finding cited in the production spec section 2 |
| `run_recent_mechanism_audit.py` | 2 | |
| `run_temporal_diagnosis_shape_audit.py` | 2 | |
| `run_msm_beta_response_audit.py` | 3 | |
| `run_policy_matrix.py` | 3 | |
| `run_psa_hta_matrix.py` | 3 | |
| `run_focused_confirmation_20260917.py` | 4 | |
| `run_historical_transmission_probe.py` | 4 | |
| `run_post2019_diagnosis_disruption_audit.py` | 4 | |
| `run_diagnosis_triangulation_orchestration.py` | 5 | 188 KB; provenance for the 2,430-trajectory triangulation run |
| `run_post2015_stock_flow_decomposition.py` | 5 | |
| `run_stage_structured_diagnosis_audit.py` | 6 | |
| `run_forecast_readiness_confirmation.py` | 7 | |
| `run_joint_fit_comparison_20260917.py` | 10 | |
| `run_stage_structured_diagnosis_focused_calibration.py` | 8 | 132 KB; defines the `p` coordinate and the harness identity smoke check |

### 4.2 `tools/` (79 scripts)

Left entirely in place. These are the `a*`/`audit_*`/`analyze_*`/`build_*`
scripts behind the numbered audits (A-12, A-16, A-17, A-25, A-28, A-29, C-4 and
the abstract evidence package). Several are cited by name in the reports and by
`tools/a12_call_fingerprints.json`, and the production spec explicitly requires
retaining A-28's regression gates. A per-script triage is a separate,
larger job and is **not** attempted here.

### 4.3 Untracked task documentation at the repository root

34 root-level `*.md` files, including the `TASK_BRIEF_*`, `PHASE2_*`,
`HISTORY_MATCHING_BUILD_SPEC.md`, `MODEL_LIMITATIONS.md` and
`COUPLED_SEARCH_RUNBOOK.md` set. All left in place. These are active working
documents, and the production spec's hygiene pass is about executable material
and generated artifacts rather than notes.

Note one documented inconsistency, already recorded in `hm_implausibility.py`:
`HISTORY_MATCHING_BUILD_SPEC.md` section 2.3 states the three-sigma coverage
bound as "at least 93%". The correct unimodal Vysochanskii–Petunin bound is
95.06% and the distribution-free Chebyshev bound is 88.9%. The code is right;
the build spec's figure is wrong and should be corrected when that document is
next revised.

## 5. Repository-wide verification and residual failures

The review baseline was **2475 passed, 71 failed, 270 skipped, 5 errors** under
the no-engine guard. This remediation modifies existing source and tests, so
the earlier claim that zero tracked source files changed no longer applies.

The failures are dominated by two environmental path problems, not logic:

1. **Missing root-level `1980_test.json`** — numerous tests resolve
   `ROOT / "1980_test.json"`, but the file lives at `configs/1980_test.json`.
   This accounts for most of `test_orientation_seed_stage_distribution.py`
   (22) and `test_lhs_calibration.py` (13).
2. **Missing `Validation /` data directory** — note the trailing space in the
   expected directory name. Accounts for the
   `test_validation_data_loader.py` errors and some LHS failures.

The earlier A-12 guard findings are now **closed**:

- `test_a12_deterministic_allocation_audit.py::test_syntax_candidate_scan_is_nonempty_and_scoped_to_reviewed_files`
  — `untriaged rounding syntax functions: [('run_v3.py', '_step_stage_structured_diagnosis')]`.
  See section 8 for the triage.
- `test_a12_review_guards.py::test_no_new_rounding_call_sites_since_the_audit`
  — the same function's three `int(np.sum(...))` counters had been new since
  the call-fingerprint baseline froze. The guard's own message authorises
  regenerating the baseline **after** classification plus a between-arm
  materiality judgement on the incremental estimand; `DX-04` records both, so
  the baseline was regenerated.
- `test_a12_review_guards.py::test_call_fingerprint_baseline_matches_the_audited_candidate_count`
  — 266 → 271 call sites. The five additions are scalar conversions of
  already-integer event tensors for counters, not new allocation boundaries.

The remaining failures are environmental/provenance gaps: the missing
root-level `1980_test.json`, unavailable `Validation /` source data, frozen
historical artifacts that correctly reject the changed runtime, and the
missing `documentation/BACKLOG.md`. The HTA `utilities.aids_on_art` gap is
closed by linking it exactly to `utilities.hiv_chronic_on_art`, without adding
an independent PSA dimension. The neutral MSM diagnosis multiplier now takes
the exact legacy all-orientations allocation and RNG route.

The final guarded-suite totals are recorded in section 7.

## 6. Implementation status of the robust-NROY stage

Recorded here because the distinction matters for what may be claimed.

### 6.1 Wired and reachable from a subcommand

The whole chain runs, for one model, through the real CLI:

```
prepare --stage discovery    -> evaluate -> merge -> classify
prepare --stage promotion    -> evaluate -> merge -> classify   (parent: discovery)
prepare --stage confirmation -> evaluate -> merge -> classify   (parent: promotion)
prepare --stage bridges      -> evaluate -> merge               (parent: confirmation)
regions                                                         (parent: bridges)
```

That covers the fail-closed plan loader; the design kernels with their quotas
and lineage; the frozen ledger; deterministic sharding; atomic writes; the
merge gate; independent score recomputation from seed-level rows; the
attempt/resume audit; parent-stage validation; the cross-stage
acceptance-input identity; the five evidence states;
local-support classification; the `p` coverage profile; the candidate-edge
graph; the frozen minimal audited edge set; bridge probes; edge verdicts;
components; geometry-only representative selection; the wall-clock cap; the
secondary correlated-output diagnostic; and the reporting package.

Every one of these tables is now produced by a command rather than only by a
library function: `design_evidence.csv`, `local_support.csv`, `p_coverage.csv`,
`graph_nodes.csv`, `candidate_edges.csv`, `edge_verdicts.csv`,
`components.csv`, `representatives.csv` and
`unresolved_blocking_edges.json`.

`nroy_contract.DECISIONS_PENDING_WIRING` is **empty**, and a test asserts
that it is: every required decision of the base stage has a real consumer.
Two tests keep the set honest if an entry is ever added back.

### 6.2 Conditionally required, off by default

The correlated-output diagnostic of NROY spec section 7 is implemented
(`nroy_assess.correlated_output_diagnostic`, `diagnose`) and disabled in the
plan. Its five numerical decisions are required **exactly when** it is
enabled (`nroy_contract.CONDITIONAL_DECISIONS`), so it cannot run on defaults
and does not gate the base stage. It is stability-gated on seed-block
bootstrap and leave-one-candidate-out refits, treats shared CRN seeds as
blocks, and carries no field through which it could change acceptance.

### 6.3 Deliberately absent

The emulator of section 6 is correctly absent. It is triggered only by a
recorded unresolved geometry question, and the only thing that counts as one
is an unresolved edge that would change which groups receive representatives.
`regions` now writes exactly that list to `unresolved_blocking_edges.json`, so
the trigger is evidence-driven rather than discretionary. No such question has
been recorded, because no stage has been run.

### 6.4 What a signed plan still would not establish

A fully signed plan would still not be a launch authorisation. The production
specification additionally requires a measured pilot, a priced trajectory
budget, `CONFIG_REGISTRY.md` with frozen candidate identities, and a
confirmation that the production seed block is disjoint from earlier runs.
None of those exists. Both `validate` and the production evaluator now refuse
the launch unless all four plan-bound artifacts pass their content checks.

Two further prerequisites are specific to M1 and are recorded here because
they are easy to miss:

1. The **identity gate** must be run (section 3.3). It is a command, not a
   test, because every pytest that checks the identity is skipped under the
   no-engine guard.
2. The **frozen production base config must carry the
   `disease.diagnosis.stage_structured_diagnosis` block.** The config
   currently proposed for `models.base_config`
   (`coupled_calibration_search.BASE_CONFIG_PATH`) does not. Injection fails
   loudly in that case rather than silently discarding `p`, which is the
   behaviour we want — but it means M1 cannot be launched against that config
   as it stands. `tests/test_nroy_pipeline_stages.py::test_m1_injection_fails_loudly_if_the_config_lacks_the_p_block`
   pins this.

## 7. Checks run after the moves

- Reference scan for `fix_thesis` and `__delete_test` across all tracked file
  types: **no remaining references outside the archive**.
- `git check-ignore` confirms `logs/` is ignored and its files remain on disk.
- Remediation-focused suite: **503 passed, 17 skipped** (synthetic; no engine,
  no trajectories).
- Neutral-route and A-12 regression follow-up: **47 passed, 4 skipped**.
- Full guarded repository suite: **2488 passed, 71 failed, 270 skipped,
  5 errors** in 440.44s. One failure was a stale DX-04 assertion found by that
  run; it was corrected and its focused rerun passed. Pytest's remaining-failure
  inventory is therefore **70 failures and 5 setup errors**; the full 7-minute
  suite was not repeated a third time after that assertion-only correction.
- M0/M1 engine identity suite: **18 passed** (user-run; 14 of them skipped
  under `pytest_no_engine_guard`, which is why the `identity-gate` command
  exists).
- `identity-gate` run live: **4/4 checks passed** in 23s, with 1154 MSM
  infections and an undiagnosed AIDS pool of 1496 — so the identity was proved
  on a fixture where the mechanism can actually fire.
- Full suite: see section 5 for the before/after reconciliation.

### 7.1 Source files this remediation modified

The remediation changes the robust-NROY command and its contract, execution,
identity, assessment and reporting modules; `run_v3.py` for neutral MSM and
DX-04 allocation identity; HTA sampling/configuration for the linked AIDS
utility; the A-12 inventory/fingerprint; and their focused tests. It does not
alter historical output artifacts or run a production trajectory.

## 8. A-12 triage of `_step_stage_structured_diagnosis` (DX-04)

The production specification (section 4) requires this site to be classified,
its six-pool allocation and conservation behaviour tested, and the finding
closed or explicitly documented before M1 production use. It was the only
untriaged rounding site in the A-12 inventory, and it is the M1 mechanism.

**What the code does now.** It partitions the undiagnosed tensor into four
rate blocks — non-MSM and MSM, each by non-AIDS / AIDS — and allocates each
through the shared aggregate-preserving allocator
`stochastic_allocation.deterministic_allocate_events`.

**The finding and repair.** `stage_step_probability` depends on the stage only
through `aids_stage`, so acute and chronic share one step probability. They are
therefore allocated together. This removes the previous ±1-person-per-population
per-step rounding differential while preserving stage-resolved event tensors.

**Measured, not assumed** (`tests/test_a12_stage_structured_diagnosis_allocation.py`,
31 tests, no engine):

| property | result |
|---|---|
| partition disjoint and exhaustive | yes, asserted against the real tensor |
| total diagnoses conserved | **exactly**; tensor total unchanged, no negative cell |
| stage counters sum to the total | yes (the function also asserts this itself) |
| effect of the former extra split | **eliminated**; combined-reference differential is exactly zero |
| effect on the total | aggregate diagnoses now match the declared rate-block allocation exactly |
| per-pool allocator itself | not fragmentation-sensitive (that is `ALLOC-01`) |

**Classification:** `rate_stratified_aggregate`, `fragmentation_sensitive =
False`, differential 0 people. Not `cellwise`: each pool is allocated
aggregate-preservingly over many cells.

**Disposition.** The split was removed. The allocator returns a tensor, so
acute and chronic diagnoses remain directly countable after one combined
non-AIDS allocation; no post-hoc redistribution is required.

**Current activity.** Dormant in the production base case: that config carries
no `stage_structured_diagnosis` block, so the profile loads disabled and
`_step_diagnosis` never takes this branch. It becomes active only for M1, and
at exact `p=0` with `routine_hazard_scale=1.0` the identity dispatch sends M1
back to the legacy path — so even M1 reaches this code only for `p > 0`.

## 9. Final collaborator source-code handoff — 2026-10-15

The accepted source identity is commit
`68112bba96402dbbf43c4456289f4384b9635cbe`, tree
`bcc902476f507d745765e9d9835e341651e70d21`.

- Mounted-evidence guarded suite: 3,174 passed, 288 skipped, zero failures or
  errors in 471.17 seconds, exit 0.
- Independent adversarial audit: ACCEPT; 160 focused checks passed.
- Conservative config registry: 78 files, SHA-256
  `e21c290072f88708356798725204d0ec5c6f0afed6629d6f89c4dcfdeaa994bc`.
- Complete-history bundle:
  `/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model-deliverables-20261015/hiv-prep-model-code-68112bb.bundle`,
  11,117,987 bytes, SHA-256
  `4a9708dba8946277eadf21ecaef9ac0cdcb9c4c7837b88df8050642e4b3bd371`;
  `git bundle verify` passed.
- Clean clone `/private/tmp/hiv-prep-handoff.Feb1Qm/repo`: exact commit, clean
  status, strict `git fsck`, no tracked symlinks, `.venv`, `node_modules`, or
  `outputs`; M0 and M1-at-p=0 validation-only passed without constructing an
  engine; registry 78 OK; robust validation reproduced the 45-decision
  fail-closed result.
- Source-only full suite: 3,019 passed, 327 skipped, 39 failed and 77 errors in
  232.99 seconds. This result is not called green: those tests require omitted
  external workbooks and historical output artifacts documented in
  `EXTERNAL_INPUT_RECONCILIATION.md`.
- Dependency hashes: `requirements.txt`
  `3a7ba47591451816350c1e4fb81e6e0e60dcf800bbdbb971eb43df01f2feaa74`;
  `requirements-test.txt`
  `8bacaf9e55cd3c00dea363cc3fe4b3a0e752c8b81b67c91e75c6a6770da7a26f`.

The bundle is a source-code handoff, not an artifact-vault export. It
intentionally excludes ignored `outputs/`, external workbooks, raw data and
the literature library. Their storage, licensing and retention decisions
remain separate. For scientific production, the required order is: authorize
the exact Option-B inputs, seed blocks, preparation and smoke; provide the
measured benchmark, pricing and live execution profile before search; review
the recalibration and select the fitted config; then finalize the 45 robust
decisions and later evidence/dispatch gates.
