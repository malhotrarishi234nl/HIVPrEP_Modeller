# Robust-NROY production-readiness closure matrix

## Purpose and verdict boundary

This is the requirement-by-requirement index for the 2026-10-15 robust-NROY
production-readiness pass. It maps every item in the initiating brief to the
evidence that proves it, the current state, and the exact condition needed to
close it. A passing test or a prepared template closes only the code-facing
part of a requirement; it does not stand in for measured evidence, scientific
review, or authorization.

The package therefore maintains two separate verdicts:

- **Collaborator source-code handoff:** **COMPLETE** at commit
  `68112bba96402dbbf43c4456289f4384b9635cbe`. The mounted-evidence guarded
  suite, independent adversarial audit, config-registry check, complete-history
  bundle verification, and clean-clone checks all passed. This closes code
  delivery for ordinary M0/M1 operation, not scientific production acceptance.
- **Scientific production readiness:** **NOT READY** until every scientific and
  operational gate below is supported by real, hash-bound evidence and the
  final signed-plan validator reports zero blockers.

No identity gate, measured pilot, positive-p confirmation, calibration, or
production simulation is authorized by this matrix.

## Original requirements

| # | Original requirement | Evidence and current finding | Current state | Exact closure condition |
|---:|---|---|---|---|
| 1 | Re-establish the current state and prove that production is not already running. | `README.md`, `NROY_REMEDIATION_2026-10-14.md`, and the readiness-package `README.md` identify the current authority and preserve earlier reports as historical. Final-integration validation of `configs/robust_nroy_freeze_production.json` reports plan SHA-256 `d147ec6c0422c28e1c2c885bcef2a83bac26f7c0630acf94469128cbdf760f27` with 45 unsigned decisions: 39 proposal-backed and 6 evidence-derived. A filtered operating-system process check matched only its own shell/search processes and no model, calibration, robust-NROY, PSA, or experiment runner. No engine was invoked during this pass. | **COMPLETE FOR CURRENT NO-ENGINE STATE** | Recheck immediately before any separately authorized engine activity; this state record does not authorize that activity. |
| 2 | Reconcile residual full-suite failures and external inputs without manufacturing data. | `EXTERNAL_INPUT_RECONCILIATION.md` records the located files, exact hashes, and unambiguous path corrections. With canonical mounts, the final guarded suite passed 3,174 tests with 288 skips and no failures/errors. The clean source-only clone instead reported 3,019 passed, 327 skipped, 39 failed and 77 errors from omitted external workbooks and historical artifact trees; that lane is disclosed rather than called green. | **COMPLETE FOR CODE HANDOFF; EXTERNAL EVIDENCE REMAINS SEPARATE** | Preserve the external-data/artifact boundary and mount the exact hash-pinned sources before claiming their evidence-dependent tests. The unresolved full legacy `documentation/BACKLOG.md` edition remains a documentation-owner choice. |
| 3 | Classify stale historical artifacts without resealing old outputs. | `HISTORICAL_ARTIFACT_DISPOSITION.md`, `ARTIFACT_TERMINAL_DISPOSITION_2026-10-15.md`, and the generated artifact catalog classify all 132 in-scope output directories. None is promoted as current validated evidence. `CONFERENCE_BACKUP_VAULT_AUDIT_2026-10-15.md` and the portable vault manifest preserve the conference lineage without deletion or scientific relabelling. | **SOURCE-HANDOFF CLASSIFICATION COMPLETE; STORAGE GOVERNANCE OPEN** | Keep all pre-Option-B results excluded from current claims unless separately revalidated or rerun. Before moving or retiring the full vault, record its durable storage location, backup status, and owner approval. This does not block a source-only handoff. |
| 4 | Prepare every unsigned production decision for review without signing it for the owner. | `DECISION_REGISTER.csv`, `OWNER_DECISION_PACKET.md`, and `OWNER_LOCK_IN_AND_METHODS_GUIDE.md` enumerate all 45 decisions, their dependencies, ranges, consequences, rationale needs, and proposed owner roles. Coupled budgets, quotas, seed blocks, graph coverage, support thresholds, and representative counts remain explicit. | **DECISION SUPPORT COMPLETE; SIGN-OFF OPEN** | Review all 45 rows when their dependent evidence exists, remove every `UNSIGNED` state through the governed signing workflow, and complete the separate strict sign-off block. Signing still does not authorize compute. |
| 5 | Assess and select a stochastic, M1-capable production base with an exact p=0 identity boundary. | `PRODUCTION_CONFIG_ASSESSMENT.md` traces `ProductionEvaluator.config_for`. `PRODUCTION_BACKBONE_RECONCILIATION.md` records the owner's Option B direction. The prepared V13.22-on-1980 candidate supports explicit standalone M0/M1 selection; M0 excludes and M1 alone receives the stage-structured diagnosis block; `routine_hazard_scale` remains exactly `1.0` at p=0; no epsilon or positive-p snapping is introduced. The candidate is labelled `REQUIRES_RECALIBRATION`. | **ROUTE ACCEPTED; SCIENTIFIC CONFIG SELECTION OPEN** | Recalibrate Option B, independently review fit and exact config/space hashes, then select and sign the resulting production proposal. Code-route tests cannot close this requirement. |
| 6 | Provide production evidence schemas that bind the signed plan, scientific code, runtime, inputs, configuration, targets, and seeds. | The config registry, model-specific measured-pilot 2.1, priced-budget 2.0, seed-audit 1.3, M1-anchor, acceptance-input, production-approval, collaborator-run-sheet, coupled benchmark/pricing, and live execution-profile contracts are integrated and passed final adversarial review. Coupled search/resume binds the exact benchmark, worker and trajectory counts, wall cap, rate source, conservative ceiling including the five-replicate in-flight tail, and a live-matched local or Slurm profile. Seed evidence is deterministically rebuilt from current sources. Deliberately incomplete templates cannot pass. No timings, prices, seed clearance, fit result, or approval was invented. | **CODE/SCHEMAS ACCEPTED; REAL SCIENTIFIC EVIDENCE ABSENT** | Generate each artifact from its separately authorized real activity, bind it to the exact selected fitted config, signed plan and current code/runtime, and obtain the required independent scientific and owner reviews. |
| 7 | Specify a positive-p M1 confirmation run without executing it. | `POSITIVE_P_M1_CONFIRMATION_PLAN.md` requires independently sourced complete joint vectors, strictly positive p, a reserved 20-seed block, candidate/vector hashes, replicate counts, nominal `I_max`, and nominal-pass status. M1 discovery must union exact-p=0 confirmed M0 embeddings with independently confirmed positive-p M1 vectors. | **SPECIFICATION COMPLETE; EXECUTION OPEN** | Approve the candidate-selection rule and seed block, separately authorize the confirmation, execute it through the governed route, and validate the resulting evidence. |
| 8 | Establish acceptance-input provenance and reconstruct every B_i without double-counting process variance. | The readiness `README.md` and acceptance-input generator define `B_i = V_obs + V_code + (0.10 * abs(z_i))^2 + declared extra non-process terms`. Candidate process variance `s_i^2(1+1/K)` stays outside B_i and enters scoring once. Underivable `V_obs=0` terms are labelled a model assumption that can bias toward over-exclusion relative to an otherwise identical positive missing-variance term. The preserved pre-sign package is explicitly non-production and stale. | **METHOD/GENERATOR COMPLETE; FINAL REVIEWED INPUTS ABSENT** | After plan/config sign-off, regenerate into a new directory, review every component and the 10% discrepancy assumption, approve through the generator's production path, and bind the resulting hashes into the signed prerequisites. |
| 9 | Supply a fail-closed, ordered execution checklist with independent authorization stops. | `GATED_EXECUTION_CHECKLIST.md` first closes Option-B inputs/seeds, preparation and smoke; then requires benchmark/pricing/live-profile evidence before calibration search; then requires fit review and fitted-config selection before the 45 robust decisions are finalized. Robust identity, pilot, positive-p confirmation, acceptance-input freeze, zero-blocker validation, and dispatch remain later independently authorized gates. | **CHECKLIST COMPLETE; MOST SCIENTIFIC GATES OPEN** | Close each checkbox only with its named evidence. Never infer permission for a later engine stage from completion of an earlier gate. |
| 10 | Verify and hand off the final integrated repository with exact commands and verdicts. | Commit `68112bba96402dbbf43c4456289f4384b9635cbe`, tree `bcc902476f507d745765e9d9835e341651e70d21`: mounted-evidence guarded suite 3,174 passed / 288 skipped / 0 failed or errors; independent audit ACCEPT with 160 focused tests; config registry 78 OK at SHA-256 `e21c290072f88708356798725204d0ec5c6f0afed6629d6f89c4dcfdeaa994bc`; complete-history bundle SHA-256 `4a9708dba8946277eadf21ecaef9ac0cdcb9c4c7837b88df8050642e4b3bd371`; clean clone exact, clean, strict-fsck-valid, free of tracked generated/dependency trees, and passing M0/M1-at-p=0 validate-only. | **COMPLETE FOR COLLABORATOR SOURCE-CODE HANDOFF** | Preserve the exact commit/bundle and do not broaden this result into scientific production acceptance. Evidence-dependent source-only test failures remain documented, and all scientific/authorization gates remain independent. |

## Gate ownership and authorization

The following actions remain outside this code-hardening pass and require real
evidence and/or the named human decision:

1. Approve the exact Option-B inputs and exhaustive seed-source scope, resolve
   every ambiguous or unparseable reference, and allocate reviewed disjoint
   seed blocks.
2. Separately authorize Option-B preparation and its paired identity smoke.
3. Before coupled search or resume, approve the exact measured benchmark,
   pricing/rate evidence, conservative ceiling, and live-matched local or Slurm
   execution profile.
4. Recalibrate and scientifically review Option B, then select the exact fitted
   config and parameter-space hashes.
5. Against that selected fitted base, review and sign all 45 production-plan
   decisions as their dependencies become available.
6. Separately authorize and review the robust M0/M1 identity gate.
7. Separately authorize the robust measured pilot, then price and approve the
   complete workflow from those measurements and current account data.
8. Approve and separately authorize positive-p M1 confirmation.
9. Review and approve regenerated observed-value and component-variance
   evidence, including the declared 10% discrepancy assumption and missing
   observation-variance limitations.
10. Approve the exact final production config and complete strict plan sign-off.
11. After every prerequisite validates, separately authorize production
   dispatch.

## No-surprise closure rule

The readiness-package `README.md` states the headline verdict; this matrix is
the authoritative completeness index. A requirement may be marked complete
only when the evidence named in its final column exists and has been checked at
the same scope as the claim. Missing, stale, indirect, template-only, or
proposal-only evidence leaves the corresponding requirement open.
