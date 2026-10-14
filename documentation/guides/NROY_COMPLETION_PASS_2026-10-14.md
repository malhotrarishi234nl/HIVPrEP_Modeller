# Robust-NROY completion pass — requirement, code and test matrix

**2026-10-14.** Branch `production-lockin-nroy`.

**No production simulation was run.** No calibration, robust-NROY, scenario or
PSA stage was dispatched; `outputs/` contains no run directory from this work.
The only engine runs were the `identity-gate` command's short truncated
trajectories (about a second each, 1980→1990) and the equivalent pytests.
Those are a documented production *prerequisite*, not production compute, and
they write nothing to `outputs/`.

**The production plan remains unsigned and fails closed.** `validate` exits
non-zero, and no subcommand below it can load the plan.

---

## 1. Requirement → code → test

### 1.1 The prohibited `identity_epsilon` exclusion band

| requirement | code | test |
|---|---|---|
| Remove `p_parameter.identity_epsilon` as a required decision | `nroy_contract.REQUIRED_DECISIONS` (removed); `FrozenPlan.validate` **refuses** a plan that still declares the field | `test_nroy_contract_design.py::test_the_prohibited_identity_epsilon_band_is_gone_from_the_contract` |
| Remove all logic rejecting valid positive `p` | `p_discontinuity_reason`, `REJECT_P_DISCONTINUITY` and `REASON_P_DISCONTINUITY` deleted; `nroy_design.Validator.reasons` has no `p`-proximity rule; `p_boundary_probes` probes every declared bin edge plus declared extras | `test_arbitrarily_small_positive_p_values_remain_admissible` (down to `5e-324`); `test_p_boundary_probes_cover_every_declared_bin_edge_and_extra_values` |
| Preserve exact `p=0` as the M0 identity control | `nc.is_exact_p_zero`, `embed_m0_in_m1` (literal float comparison), `ModelSpace` requires the `p` lower bound to be exactly 0 | `test_nroy_m0_m1_engine_identity.py::test_identity_holds_for_every_vector_and_seed_combination` and friends; plus the `identity-gate` command's own 9 vector×seed comparisons |
| Allow every positive value inside the signed range | `nc.assert_p_admissible` — bounds and finiteness only, with no epsilon argument to supply a proximity rule | `test_arbitrarily_small_positive_p_values_remain_admissible`; `test_end_to_end_m1_includes_explicit_p_zero_and_a_tiny_positive_probe` (a declared `1e-18` probe is proposed **and scored**) |
| Compare `p>0` distributionally | `nc.comparison_mode_for_p`, `assert_comparison_mode`, `distributional_summary` (no per-seed field to difference by accident) | `test_paired_comparison_is_refused_above_zero_but_the_value_is_not`; `test_a_tiny_positive_p_runs_and_is_compared_distributionally_not_paired` (real engine, 3 seeds) |

The constraint moved from the parameter range to the *comparison*, which is
where it belonged. A paired, seed-by-seed comparison is refused above zero; the
value itself is never refused, clipped or snapped.

### 1.2 The previously disconnected stages

| requirement | code | test |
|---|---|---|
| Wire local-support classification into a runner stage | `run_robust_nroy_freeze.command_classify`; `nroy_assess.local_support_table`, `neighbour_evidence_for_anchor` (move directions **recomputed** from the frozen vectors, not read back) | `test_classify_produces_local_support_from_a_merged_stage` |
| Produce `local_support.csv` | `classify` writes it; carried forward verbatim into bridges and regions | same, plus `test_the_whole_chain_is_reachable_and_leaves_every_required_table` |
| Distinguish the five evidence states | `nroy_assess.evidence_by_design` — ledger first, score table second | `test_the_five_evidence_states_are_distinguished_from_the_ledger`; `test_a_merge_without_acceptance_arithmetic_cannot_be_assessed` |
| Wire candidate-edge construction and frozen bridge-design generation | `build_bridge_designs`; `prepare --stage bridges` writes `graph_nodes.csv` and `candidate_edges.csv`, then probes only the **frozen minimal** edge set | `test_bridges_prepare_freezes_the_graph_and_its_probes`; `test_the_minimal_edge_set_is_read_back_not_recomputed` |
| Evaluate and merge bridge work through the same ledger/sharding contracts | bridges `prepare` writes an ordinary `Ledger`; `evaluate` and `merge` are unchanged | `test_bridges_use_the_reserved_confirmation_seed_block`; the whole-chain test runs 2 shards through bridges |
| Produce edge verdicts and sampled-path-supported components | `command_regions`; `nroy_assess.edge_verdict_table`, `nroy_graph.components` | `test_regions_produces_verdicts_components_and_representatives`; `test_regions_refuses_a_bridges_stage_that_was_not_scored` |
| Produce `p_coverage.csv` | `command_classify`; `nroy_assess.p_coverage_from_tables` | `test_classify_writes_the_p_coverage_profile_for_m1`; `test_p_coverage_never_counts_work_that_was_not_run` |
| Wire geometry-only representative selection; produce `representatives.csv` | `command_regions` passes `nroy_assess.node_geometry` (the `u:` columns) and nothing else; `nroy_graph.select_representatives` validates the signed rule | `test_regions_produces_...`; `test_a_point_with_neither_local_nor_region_support_gets_no_representative` |
| Eligibility is "local/**region** support" (spec sec. 1; decision 7) | `nroy_assess.representation_eligibility` — local OR region support, with the ground recorded per candidate in `representation_eligibility.csv`; `node_assessed` keeps the edge verdict off local eligibility so the region route stays reachable | `test_region_support_is_reachable_without_local_support`; `test_eligibility_records_its_ground_rather_than_a_bare_flag`; `test_a_singleton_component_has_no_region_support` |
| Every signed graph / archive-following / representative decision has a real consumer; `DECISIONS_PENDING_WIRING` reduced, ideally empty | **empty** (`frozenset()`) | `test_every_signed_decision_is_either_consumed_or_declared_unwired`; `test_the_pending_wiring_set_is_empty_and_cannot_rot` |

Decisions that gained a genuine consumer in this pass:

| decision | consumer | what it now changes |
|---|---|---|
| `graph.neighbour_rule`, `graph.neighbour_count`, `graph.tie_breaking` | `ng.candidate_edges` via bridges `prepare` | which edges are proposed at all |
| `graph.minimal_edge_algorithm` | `ng.minimal_edge_set` | which edges get paid for |
| `graph.bridge_pass_rule` | `ng.classify_edge` — **checked**, not assumed; any other declared rule is refused rather than silently given this behaviour | whether an edge can be supported |
| `representative_selection.rule` / `n_per_component` | `ng.select_representatives` via `regions` | which vectors enter the forecast ensemble |
| `archive_following.source_pool` | `nd.assert_source_pool`, `correlation_following_directions`, `assert_cross_block_declared` | a declared source that cannot contribute is a **contract error**, not a quietly shorter direction list; an undeclared source contributes nothing; `cross_block.quota` must agree (>0 iff declared) |
| `archive_following.neighbour_rule` | `nd.archive_directions` | the low-J rule **requires** a `j` column and refuses to fall back to a distance-only ordering |
| `archive_following.neighbour_count` | `nd.correlation_following_directions` | **new decision.** The kernel was reading `graph.neighbour_count`, which is a different frozen choice. That was a defect, not a convention |
| `budget.wall_time_cap_seconds` | `ne.WallClockCap` via `evaluate` | when dispatch stops |

### 1.3 Wall-clock cap

| requirement | code | test |
|---|---|---|
| Implement the declared cap | `nroy_execution.WallClockCap`; `command_evaluate` checks `may_start()` before each frozen unit | `test_the_wall_clock_cap_stops_dispatch_and_bounds_in_flight_work`; `test_a_positive_cap_is_required` |
| Bounded in-flight completion | the cap stops **dispatch**; the unit already running finishes, and at most one may overrun, recorded in `overran_by_seconds` | `test_the_wall_clock_cap_stops_dispatch_and_bounds_in_flight_work` |
| Partial-stage behaviour | remaining units stay incomplete, so the ordinary resume path reports `PARTIAL`; no new status and no shortcut | `test_the_cap_leaves_a_partial_non_parentable_stage` (the next stage then refuses to descend from it); `test_work_withheld_at_the_cap_is_not_run_or_scored` |
| Resume equivalence | a capped-then-resumed run and an uninterrupted one produce the identical scored table | `test_resuming_after_a_cap_completes_the_same_scientific_table` |

Killing a unit mid-flight would spend a launch attempt and leave no result —
the budget is consumed either way, so letting it finish is strictly better
science for the same money.

### 1.4 Correlated-output diagnostic (spec section 7)

| requirement | code | test |
|---|---|---|
| Estimate from existing confirmation seed rows | `nroy_assess.correlated_output_diagnostic` centres **within candidate** before any covariance | the `_seed_frame` fixtures with a known shared-factor structure |
| Declared shrinkage regularisation | `_shrunk_covariance`, toward the covariance's own **diagonal** (not the identity: the 13 targets are on wildly different scales, so an identity target would be a statement about units) | `test_shrinkage_outside_the_unit_interval_is_refused` |
| Report effective rank, condition number, dominant directions | `_effective_rank` — participation ratio, so no arbitrary "eigenvalue is zero" threshold is needed | `test_the_diagnostic_reports_rank_and_directions_when_stable` |
| Treat shared CRN seeds as blocks; no pseudo-replication | every resample resamples **whole seed blocks**; the effective sample size is the number of blocks | `test_the_diagnostic_refuses_to_run_on_too_few_seed_blocks` (6 candidates × 2 seeds is **2**, not 12) |
| Stability-gated | seed-block bootstrap **and** leave-one-candidate-out; otherwise `unavailable_unstable` | `test_an_unstable_diagnostic_is_marked_unavailable_not_repaired` |
| Never repaired by dropping targets or switching estimator | the only outcome of instability is the unavailable status, stated in the reason text | same test asserts both phrases |
| Compare its ranking with pointwise `I_max` | `_rank_correlation_with_i_max` (Spearman), reported so a material contradiction is visible | `test_the_diagnostic_compares_its_ranking_with_pointwise_i_max` |
| Secondary; cannot change primary acceptance | **structural**: the result object has no pass, promotion or implausibility field, and `diagnose` writes its own file | `test_the_diagnostic_carries_no_route_to_acceptance`; `test_diagnose_runs_and_writes_its_own_file_when_signed` (asserts `candidate_scores.csv` is byte-identical afterwards) |
| Cannot run on defaults | its five numbers are required **exactly when** it is enabled (`nc.CONDITIONAL_DECISIONS`) | `test_conditional_decisions_are_required_exactly_when_switched_on`; `test_diagnose_is_refused_while_the_plan_leaves_it_disabled` |

### 1.5 Emulator

| requirement | code | test |
|---|---|---|
| Remains absent unless a recorded unresolved geometry question triggers it | no emulator exists. `regions` writes `unresolved_blocking_edges.json` — the unresolved edges that would merge two components and therefore change which groups receive representatives, which is the only thing the specification counts as such a question | `test_an_unresolved_edge_that_would_change_representation_is_recorded` |

The trigger is now evidence-driven rather than discretionary. No such question
has been recorded, because no stage has been run.

### 1.6 Production evaluator adapter

| requirement | code | test |
|---|---|---|
| Fully signed plan | `FrozenPlan.load` is the only way to obtain a plan, and it refuses while any required value is unsigned | `test_shipped_production_plan_fails_closed` |
| Explicit production evaluator selection | `--evaluator` is a **required** argument with no default | `test_there_is_no_default_route_to_the_production_evaluator` |
| Priced-budget acknowledgement | `resolve_evaluator(..., priced=...)` | `test_production_is_refused_without_the_priced_budget_acknowledgement` |
| Valid parent artifacts | `resolve_evaluator` refuses production work whose units carry no parent-manifest hash | `test_production_is_refused_when_the_frozen_work_has_no_validated_parent` |
| No default route to production | the module-level `production_evaluator` is a **refusal stub**; the working adapter exists only as `ProductionEvaluator`, reachable only through `resolve_evaluator` | `test_the_production_evaluator_stub_cannot_be_called_directly` |
| Declared frozen production config | `models.base_config` — **new required decision**; the adapter refuses a missing file | `test_the_production_adapter_refuses_a_missing_production_config` |
| Every contract target resolved, in order | the adapter refuses a register that does not define every target, and refuses a reordered target vector at call time | `test_the_production_adapter_resolves_every_target_in_the_frozen_order`; `test_the_production_adapter_refuses_a_reordered_target_vector` |
| M1 is M0 plus one coordinate | the adapter **generates** the M1 space from M0 plus the signed `p` range rather than reading a second file | `test_the_production_adapter_generates_m1_as_m0_plus_one_coordinate` |
| Tested with mocks or bounded fixtures; no real calibration | every test stops before the engine call; injection is tested through `config_for` | `test_the_production_adapter_injects_each_value_at_its_declared_path` (≥20 coordinates verified at their declared config paths) |

### 1.7 A-12 triage of `_step_stage_structured_diagnosis`

| requirement | code | test |
|---|---|---|
| Classify it; do not merely allowlist it | `DX-04` row in `tools/run_a12_deterministic_allocation_audit.py` `INVENTORY`, classified `rate_stratified_aggregate`, `fragmentation_sensitive=True`, differential 1 person | `test_a12_stage_structured_diagnosis_allocation.py::test_the_site_is_triaged_in_the_a12_inventory` |
| Test conservation across all six pools | the six pools are pairwise disjoint and sum back to the undiagnosed tensor; totals, stock movement and stage counters all conserved | 4 conservation tests |
| Sparse counts | parametrised at 0, 1, 2, 3 and 7 people per pool | `test_sparse_pools_conserve_and_never_invent_diagnoses`; `test_an_empty_undiagnosed_tensor_diagnoses_nobody` |
| Boundary probabilities | zero hazard diagnoses nobody; an absorbing hazard diagnoses the whole pool; only the AIDS pool moves with the symptom probability | 3 tests |
| Fragmentation sensitivity | measured at **≤ 1 person per population per step**, both signs, occurring in ~21% of a random sweep, and provably unable to change the total | `test_splitting_the_shared_rate_block_costs_at_most_one_person_per_population` (10 cases); `test_the_fragmentation_is_real_and_both_signs_occur`; `test_the_fragmentation_can_never_change_the_total` |
| Document why acceptable, or fix it | documented: the stage-resolved diagnosis counters are model **outputs**, so the allocation must be resolved by stage; merging would mean splitting a merged total by pool weights, a different estimator. Both options are in the inventory row with which is which | `REPOSITORY_INVENTORY_2026-10-14.md` section 8 |

The 30 tests drive the **real method** with a minimal stand-in object rather
than reimplementing its partition, so they cannot drift from the code they
certify, and a non-vacuity gate requires the fragmentation to actually occur.

### 1.8 M0/M1 identity as an un-skippable pre-production command

| requirement | code | test |
|---|---|---|
| Explicit, documented pre-production command | `run_robust_nroy_freeze.py identity-gate --out <dir>`; `nroy_identity_gate.py` | `test_the_gate_command_is_documented_and_explicit` |
| Cannot be accidentally skipped by the no-engine guard | the gate is a **command**, not a pytest, so no pytest plugin can skip it; and `prepare --model M1` **requires** its passing artifact | `test_m1_prepare_refuses_without_the_identity_gate`; `test_m0_prepare_does_not_require_the_gate` |
| Retain the mechanism-precondition gates | `check_not_vacuous` requires infections to occur **and** a non-empty undiagnosed AIDS pool; `check_deliberate_failures` requires the comparison to fail for a differing non-`p` input and an unpinned routine hazard scale; `check_positive_p_is_live` requires a positive `p` to change the trajectory. All four are required for the gate to pass | `test_the_gate_requires_all_four_checks_not_only_the_identity` |
| Refuse a stale certification | `assert_gate_satisfied` refuses a missing, failed, incomplete, schema-mismatched or **stale-code** artifact | `test_the_gate_check_refuses_a_missing_failed_incomplete_or_stale_artifact` |
| The pytest and the command cannot diverge | the pytest module delegates every helper to the gate module, and builds engines only through it | `test_this_module_and_the_cli_gate_share_one_implementation` (runs **under** the no-engine guard) |

Why this exists: every pytest that checks the identity builds a real engine, so
every one is skipped under the no-engine guard. That is correct for the guard
and a dangerous way to hold a production prerequisite — a green full suite says
nothing about the identity. The first revision of those tests passed while
three of its deliberate-failure guards were red, for exactly that reason.

---

### 1.9 One acceptance basis per lineage

The hole this closes: `merge` hashed the observed-value and base-variance files
into the manifest, and `validate_parent_stage` checked the target order and the
register hash, but **nothing compared the acceptance inputs across a
parent/child pair**. A bridges stage merged against different observed values
than its confirmation parent would publish edge verdicts on a different
acceptance basis, and the merge gate would not notice.

| requirement | code | test |
|---|---|---|
| Freeze the parent's **complete** acceptance-input identity into every child | `run_robust_nroy_freeze._freeze_parent_acceptance_identity`, writing `parent_acceptance_identity.json` at `prepare`, before the manifest, so the stage's own artifact index covers it | `test_the_parent_acceptance_identity_is_frozen_into_the_child_at_prepare` (also asserts the file is in `artifacts.json`) |
| The identity holds target order, target-register hash, observed-value hash, base-variance hash, discrepancy, scoring version and acceptance rule | `nc.ACCEPTANCE_IDENTITY_FIELDS`, `nc.acceptance_input_identity`, `nc.SCORING_VERSION`, `nc.acceptance_rule_text`, `FrozenPlan.acceptance_identity` | `test_the_identity_names_every_input_the_specification_requires` |
| Compare at **child merge**, fail closed on any difference | `command_merge`: builds the identity from the two files and the plan, then `nc.assert_acceptance_identity` against the frozen parent before the manifest is written | `test_a_child_merged_against_different_observed_values_is_refused`; `..._different_base_variances_is_refused`; `..._a_changed_discrepancy_is_refused`; `..._changed_register_contents_is_refused` |
| Compare **before any classification** | `_assert_acceptance_basis` in `command_classify`, before the first label is written | `test_classification_is_refused_when_the_plan_changed_after_the_merge` (asserts no `local_support.csv` is left behind) |
| Compare **before any edge verdict** | `_assert_acceptance_basis` in `command_regions`, before `edge_verdict_table` | `test_edge_verdicts_are_refused_when_the_plan_changed_after_the_bridge_merge`; `test_the_regions_package_records_the_basis_its_verdicts_were_reached_on` |
| No field is decorative | field-by-field mutation of a real identity | `test_every_field_of_the_identity_is_compared_not_only_the_summary_hash` (all eight fields, one at a time) |
| A reordered target contract cannot reach a scored table | the ledger's frozen per-unit target schema in `ne.merge_seed_results` fires **first**; `target_order` and `target_contract_sha256` are the identity's backstop behind it | `test_a_child_merged_against_a_reordered_target_order_is_refused` — recorded against the gate that actually fires, not attributed to the new check |
| A missing identity is a refusal, not a pass | `nc.assert_acceptance_identity` refuses an absent frozen *or* current identity, and `_assert_acceptance_basis` refuses a stage merged before identities were recorded | `test_an_absent_identity_is_a_refusal_not_a_pass` |

Two comparisons run, not one. Against the **frozen parent identity**, for the
complete identity including the two file hashes; and against the **current
plan**, for everything the plan fixes, so a plan edited between `merge` and
`classify` cannot slip through. `SCORING_VERSION` is in the identity because
two stages scored by different arithmetic are not comparable even when every
input file is byte-identical; bumping it deliberately invalidates every
parent/child pair that straddles the change.

**The test fixture no longer relies on the defect.** The
negative-local/confirming-bridge case used to be built by merging the
confirmation stage against everything-fails acceptance inputs and the bridges
stage against everything-confirms inputs — that is, by exploiting exactly the
hole above, which is how it came to light. A test may not require the defect it
guards against, so the geometry now does the work: a new verification-only
evaluator (`surrogate_islands`, which runs no engine) is plausible only within
`0.02` of three island centres spaced along the first normalised coordinate,
and the two anchors sit one spacing beyond each end. Every ambient probe around
an anchor is at least `0.10` from the nearest island and fails; the declared
25/50/75% bridge fractions interpolate *exactly* onto the three islands and
confirm. One observed-value file, one base-variance file, one register, one
acceptance rule, for every stage in the chain. The geometry itself is asserted
rather than assumed (`test_the_island_field_is_plausible_only_on_its_islands`),
so it cannot drift into testing nothing.

### 1.10 What the inspection step found in my own work

Recorded because the brief asked for an independent inspection against both
specifications, and it was worth having.

**A self-sealing error.** Both documents read "demonstrated local/**region**
support". My first implementation used *local* eligibility twice: to gate
representative selection, which simply ignored the region half; and to gate the
**edge verdict**, which made the region half *unreachable*. A point with no
local support could never acquire region support, because the very edge that
would establish it was marked unresolved on that point's account. The graph
existed and could not do the job it exists for. Fixed in section 1.2's last
row; the failure mode is written into `nroy_assess.node_assessed`'s docstring
so it cannot be reintroduced quietly.

**A vacuous verification path.** The pipeline tests were structurally green and
scientifically empty. `surrogate_evaluator` returns about 100–110 per target,
so with the repository's usual synthetic inputs (`observed = 0`, `B_i = 1`)
`I_max` is around 100 and **nothing ever confirms**: no `broadly_supported`
label, no `sampled_path_supported` edge, no representative selected, no region
support. Every positive path was unexercised through the CLI. This is the same
shape of mistake as an identity test run on an epidemic that never starts, and
it was caught the same way — by asking what the numbers actually were rather
than whether the tests were green.

---

## 2. Commands and results

```bash
# 1. Fail-closed validation. Runs no simulation under any circumstances.
.venv/bin/python run_robust_nroy_freeze.py validate
```

```
FAIL CLOSED: 43 required value(s) are unsigned.
No production simulation may start.
```

```bash
# 2. The production-relevant suite. No engine, no trajectories.
.venv/bin/python -m pytest \
    tests/test_nroy_contract_design.py \
    tests/test_nroy_execution_sharding.py \
    tests/test_nroy_graph_report.py \
    tests/test_nroy_pipeline_stages.py \
    tests/test_a12_stage_structured_diagnosis_allocation.py \
    tests/test_a12_deterministic_allocation_audit.py \
    tests/test_a12_review_guards.py -q
```

```
335 passed in 72.05s
```

Measured at the commit this revision records. The previous revision of this
document reported `322 passed` while printing a six-file command that collects
306; the seventh file (`test_a12_review_guards.py`, the A-12 fingerprint guard)
was in the run but had been dropped from the transcribed command. The command
above is the one that produces the number above.

```bash
# 3. The M0/M1 identity gate. RUNS THE ENGINE, on purpose, on short
#    truncated trajectories. Required before any M1 design.
.venv/bin/python run_robust_nroy_freeze.py identity-gate --out outputs/identity_gate
```

```
[PASS] not_vacuous: 1154 MSM infections and an undiagnosed AIDS pool of 1496
[PASS] identity_at_exact_p_zero: 9/9 (vector, seed) comparisons bit-identical
[PASS] deliberate_failures_do_fail: both deliberate failures differ from M0
[PASS] positive_p_changes_the_trajectory: every declared positive p changes it
GATE PASSED (4/4 checks) in 23.1s
```

Measured earlier in this pass and **not** re-run for this revision, because it
does not need to be: the gate's staleness check fingerprints `run_v3.py`,
`stage_structured_diagnosis.py` and `counter_based_rng.py`, and this branch has
**zero** commits touching any of the three. No gate artifact is on disk, so a
fresh `prepare --model M1` would still demand one.

```bash
# 4. The same checks as pytests (14 of 18 skipped under the no-engine guard).
.venv/bin/python -m pytest tests/test_nroy_m0_m1_engine_identity.py -q
```

```
18 passed
```

---

## 3. Full-suite reconciliation

Command, in both cases:

```bash
.venv/bin/python -m pytest -p pytest_no_engine_guard tests/ -q
```

| | before | after |
|---|---|---|
| passed | 2365 | 2475 |
| **failed** | **74** | **71** |
| skipped | 270 | 270 |
| errors | 5 | 5 |

Measured, not inferred: the "after" figures are from a run at commit `fa47f65`.
The pass count rose by exactly the 13 tests this revision adds, and the
failure, skip and error counts did not move at all — so nothing here broke a
test and nothing accidentally fixed one. The per-file failure breakdown was
re-measured at `fa47f65` and is identical to the one recorded at `b3ed35a`
below, which was itself confirmed by three independent runs.

The three closed failures are listed below, and the remaining 71 + 5 are
classified exhaustively, by root cause, in the table after that. The
classification sums to exactly 76 against the observed per-file breakdown,
with no residue.

Three pre-existing failures are **closed** by this pass, and no new one is
introduced:

| test | why it failed | how it closed |
|---|---|---|
| `test_a12_deterministic_allocation_audit.py::test_syntax_candidate_scan_is_nonempty_and_scoped_to_reviewed_files` | `untriaged rounding syntax functions: [('run_v3.py', '_step_stage_structured_diagnosis')]` | `DX-04` triaged into `INVENTORY` |
| `test_a12_review_guards.py::test_no_new_rounding_call_sites_since_the_audit` | the same function's three `int(np.sum(...))` counters were new since the fingerprint baseline froze | baseline regenerated, which the guard's own message authorises only **after** classification and a between-arm materiality judgement — both now recorded |
| `test_a12_review_guards.py::test_call_fingerprint_baseline_matches_the_audited_candidate_count` | baseline said 266 call sites, the code has 269 | count updated to 269 with the reason in the docstring |

### Classification of every remaining failure and error

All 76 are environmental or pre-existing. **None is attributable to the new
implementation or to the repository moves**, and the evidence is structural:
this work modified exactly one pre-existing executable file, by one line (the
`DX-04` inventory row), plus the regenerated fingerprint baseline and the
updated count in its guard. `run_v3.py`, `aids_observation.py`,
`calib_extractor.py`, `run_coupled_calibration.py` and `run_lhs_calibration.py`
— the modules every failure below points at — have **zero** commits on this
branch.

| cluster | root cause | count | files |
|---|---|---|---|
| **A** | **Missing root-level `1980_test.json`.** These tests resolve `ROOT / "1980_test.json"`; the file lives at `configs/1980_test.json`. | 39 | `test_orientation_seed_stage_distribution.py` (22), `test_lhs_calibration.py` (12), `test_non_msm_diagnosis_pipeline_audit.py` (5) |
| **B** | **Missing `Validation /` data directory** (note the trailing space in the expected name). | 6 | `test_validation_data_loader.py` (5 **errors**), `test_lhs_calibration.py` (1) |
| **C** | **Frozen run artifacts refuse a changed runtime**: `ContractError: code changed since the search: ['aids_observation.py', 'calib_extractor.py', 'run_coupled_calibration.py', 'run_v3.py']`. These audits replay a frozen coupled-search or LHS run and correctly refuse when the runtime differs. The drift predates this work. | 28 | `test_temporal_diagnosis_shape_audit.py` (7), `test_art_interpolation_shape_audit.py` (6), `test_recent_mechanism_audit.py` (4), `test_forecast_readiness_confirmation.py` (4), `test_msm_beta_response_audit.py` (3), `test_post2019_diagnosis_disruption_audit.py` (2), `test_post2015_stock_flow_decomposition.py` (1), `test_historical_transmission_probe.py` (1) |
| **D** | **Missing `documentation/BACKLOG.md`.** | 1 | `test_scenario_b_expansion.py` |
| **E** | **`utilities.aids_on_art` absent from the HTA parameter set.** A genuine pre-existing content gap, not a path problem. | 1 | `test_hta_sampler.py` |
| **F** | **Cascade from a real pre-existing engine failure** — see below. | 1 | `test_joint_fit_comparison_runner_20260917.py` |

### Cluster F, and one pre-existing finding worth your attention

`test_joint_fit_comparison_runner_20260917.py::test_validate_only_starts_no_simulation`
expects the runner's validate gate to exit 0. It exits 2, because the gate
shells out to `pytest` on two diagnosis test files — and that subprocess does
**not** inherit the no-engine guard, so it runs a test the guarded suite skips:

```
tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior
    ::test_enabled_with_multiplier_one_matches_baseline
AssertionError: Multiplier=1.0 should produce identical MSM diagnoses;
    ref=93601, mult1=86772
```

The runner's own gate already records this as a *tolerated* failure with
`"tolerated_failure_still_fails": true`, so it is a known, logged issue rather
than a discovery. It is pre-existing, and **this pass deliberately did not
attempt to fix it** — a 7% discrepancy in the diagnosis mechanism is not
something to resolve as a side effect of a patch about acceptance inputs.

It is now an explicit **pre-calibration blocker** in
`PRODUCTION_LOCKIN_SPEC_2026-10-13.md` section 4, with the exact failing test
recorded and a required four-way diagnostic: one-step mathematical neutrality
at `1.0`; RNG-stream coupling; amplification through the longitudinal epidemic;
or a real change in the transition distribution. The first and last are
mutually exclusive answers to the same question; the middle two leave the
mechanism correct but make a paired comparison meaningless. No production
calibration starts while it is unresolved.

It is the **same shape of question** as
the M0/M1 identity this pass just put behind a gate: an identity at a neutral
parameter value that does not hold. At `multiplier = 1.0` the MSM-relative
diagnosis path should reduce to the baseline, and it differs by 7%. If that is
the same class of cause — a different branch drawing from differently keyed
RNG streams rather than an arithmetic error — then it is a coupling artefact
and the test needs a distributional comparison. If it is not, it is a real
discrepancy in the diagnosis path. Either way it should be resolved before the
diagnosis mechanism carries production results, and the M0/M1 gate's
`check_deliberate_failures` is the pattern for telling the two apart.

---

## 4. Remaining gaps

Stated as gaps, not as work in progress.

1. **43 required decisions are unsigned.** `validate` splits them: **27 have a
   proposal** recorded in one of the specifications, and **16 have none** —
   those need a decision, not a transcription. Both specifications are still
   DRAFT and the sign-off table in the production spec is blank. Five further
   decisions become required if the correlated-output diagnostic is enabled.
2. **A signed plan would still not be a launch authorisation.** The production
   specification also requires a measured pilot, a priced trajectory budget,
   `CONFIG_REGISTRY.md` with frozen candidate identities, and a confirmation
   that the production seed block is disjoint from earlier runs. None exists.
3. **The frozen production base config does not carry the
   `stage_structured_diagnosis` block**, so M1 cannot be launched against the
   currently proposed `models.base_config` as it stands. Injection fails loudly
   rather than silently discarding `p`, which is the behaviour we want, but it
   is a prerequisite rather than a detail.
4. **The identity is demonstrated on a truncated 1980→1990 run**, not on the
   full production horizon, and for the recorded vectors and seeds only. The
   gate artifact records that limitation.
5. **The correlated-output diagnostic has never been run on real confirmation
   rows**, only on synthetic fixtures with a known covariance structure. Its
   five numbers are unsigned.
6. **No emulator exists**, correctly. If `regions` ever records an unresolved
   edge that would change representation, that is the trigger — and the
   specification's own constraint applies: an emulator may never rule out a
   region or declare plausibility without simulator evaluation.
7. **The A-12 `DX-04` disposition is "retained with a bounded differential".**
   That is a documented acceptance, not a fix. If the stage split matters for a
   published result, option (b) in the inventory row is the alternative, and it
   is a modelling decision.
8. **`archive_following.source_pool` with `low_j_archive_vectors` requires an
   archive table** passed to `prepare --archive`, with a `j` column if the
   low-J neighbour rule is signed. No such table has been assembled.
9. **The MSM-relative-diagnosis multiplier identity at `1.0` does not hold.**
   Pre-existing, untouched by this pass, and now an explicit pre-calibration
   **blocker** in `PRODUCTION_LOCKIN_SPEC_2026-10-13.md` section 4 with the
   exact failing test and a required four-way diagnostic. See section 3,
   cluster F.

**Not a gap — deliberate production behaviour.** A partially sharded stage
cannot be merged into a scored table, and that is the intended rule rather than
an unfinished implementation. Sharding is by *result key*, which includes the
seed, so a half-evaluated stage leaves some designs with one replicate, and
`recompute_candidate_scores` refuses the whole merge: a predictive variance
needs two draws. The stage is published `PARTIAL`, it is non-parentable, and no
selectively scored subset is produced. The alternative — score the designs that
happen to be complete and label the rest `work_not_run` — is rejected on
purpose, because choosing which under-replicated designs to drop is precisely
the silent-drop failure this module exists to prevent, and because a
selectively scored subset is a biased sample of the design: the designs that
finish first are not a random subset of the design when work is sharded by a
hash of the result key. Resume completes the stage and produces the same
scientific table
(`test_resuming_after_a_cap_completes_the_same_scientific_table`); the
classification rule for undispatched work is tested at library level
(`test_an_undispatched_probe_makes_its_anchor_unresolved_not_negative`). If a
future run genuinely needs partial scoring, that is a scientific decision about
acceptable bias in a design, not a convenience, and it belongs in the signed
plan.

**Closed by this pass.** The cross-stage acceptance-input identity — gap 10 in
the previous revision of this document — is implemented, tested and documented
in section 1.9. Every child stage freezes its parent's complete acceptance-input
identity at `prepare`, and `merge`, `classify` and `regions` each fail closed on
any difference.

---

## 5. Statement

No production simulation of any kind was launched. No calibration, robust-NROY,
scenario or PSA stage was dispatched, and `outputs/` holds no run directory
from this work. The production run plan is deliberately unsigned and fails
closed, so no subcommand below `validate` can load it. This work is **not** a
claim that the stage is ready to run.

For this final revision specifically: no engine ran at all. The only commands
executed were `validate` (which dispatches nothing by construction) and pytest.
`outputs/` contains no nroy run directory and no identity-gate artifact.
