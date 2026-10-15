# Robust-NROY production-readiness closure matrix

## Purpose and verdict boundary

This is the requirement-by-requirement index for the 2026-10-15 robust-NROY
production-readiness pass. It maps every item in the initiating brief to the
evidence that proves it, the current state, and the exact condition needed to
close it. A passing test or a prepared template closes only the code-facing
part of a requirement; it does not stand in for measured evidence, scientific
review, or authorization.

The package therefore maintains two separate verdicts:

- **Repository code handoff:** open until the final integrated revision passes
  the guarded repository suite, a documentation-consistency review, and a
  clean source-only bundle/clone verification.
- **Scientific production readiness:** **NOT READY** until every scientific and
  operational gate below is supported by real, hash-bound evidence and the
  final signed-plan validator reports zero blockers.

No identity gate, measured pilot, positive-p confirmation, calibration, or
production simulation is authorized by this matrix.

## Original requirements

| # | Original requirement | Evidence and current finding | Current state | Exact closure condition |
|---:|---|---|---|---|
| 1 | Re-establish the current state and prove that production is not already running. | `README.md`, `NROY_REMEDIATION_2026-10-14.md`, and the readiness-package `README.md` identify the current authority and preserve earlier reports as historical. Final-integration validation of `configs/robust_nroy_freeze_production.json` reports plan SHA-256 `e2a9f4e50ce155be88dacfc161d6ae391bcdbfec70a7613f4914c7fa418d7d02` with 45 unsigned decisions: 39 proposal-backed and 6 evidence-dependent. A filtered operating-system process check matched only its own shell/search processes and no model, calibration, robust-NROY, PSA, or experiment runner. No engine was invoked during this pass. | **COMPLETE FOR CURRENT NO-ENGINE STATE** | Recheck immediately before any separately authorized engine activity; this state record does not authorize that activity. |
| 2 | Reconcile residual full-suite failures and external inputs without manufacturing data. | `EXTERNAL_INPUT_RECONCILIATION.md` records the located files, exact hashes, and unambiguous path corrections. The raw/private data mount remains intentionally separate from a source-only handoff. The full legacy `documentation/BACKLOG.md` edition remains a documentation-owner choice rather than a fabricated reconstruction. | **CODE-FACING WORK COMPLETE; FINAL VERIFICATION OPEN** | Run the final guarded full suite in the integrated tree and again through the clean handoff verification lane. Preserve the stated external-data boundary in collaborator instructions. |
| 3 | Classify stale historical artifacts without resealing old outputs. | `HISTORICAL_ARTIFACT_DISPOSITION.md`, `ARTIFACT_TERMINAL_DISPOSITION_2026-10-15.md`, and the generated artifact catalog classify all 132 in-scope output directories. None is promoted as current validated evidence. `CONFERENCE_BACKUP_VAULT_AUDIT_2026-10-15.md` and the portable vault manifest preserve the conference lineage without deletion or scientific relabelling. | **SOURCE-HANDOFF CLASSIFICATION COMPLETE; STORAGE GOVERNANCE OPEN** | Keep all pre-Option-B results excluded from current claims unless separately revalidated or rerun. Before moving or retiring the full vault, record its durable storage location, backup status, and owner approval. This does not block a source-only handoff. |
| 4 | Prepare every unsigned production decision for review without signing it for the owner. | `DECISION_REGISTER.csv`, `OWNER_DECISION_PACKET.md`, and `OWNER_LOCK_IN_AND_METHODS_GUIDE.md` enumerate all 45 decisions, their dependencies, ranges, consequences, rationale needs, and proposed owner roles. Coupled budgets, quotas, seed blocks, graph coverage, support thresholds, and representative counts remain explicit. | **DECISION SUPPORT COMPLETE; SIGN-OFF OPEN** | Review all 45 rows when their dependent evidence exists, remove every `UNSIGNED` state through the governed signing workflow, and complete the separate strict sign-off block. Signing still does not authorize compute. |
| 5 | Assess and select a stochastic, M1-capable production base with an exact p=0 identity boundary. | `PRODUCTION_CONFIG_ASSESSMENT.md` traces `ProductionEvaluator.config_for`. `PRODUCTION_BACKBONE_RECONCILIATION.md` records the owner's Option B direction. The prepared V13.22-on-1980 candidate supports explicit standalone M0/M1 selection; M0 excludes and M1 alone receives the stage-structured diagnosis block; `routine_hazard_scale` remains exactly `1.0` at p=0; no epsilon or positive-p snapping is introduced. The candidate is labelled `REQUIRES_RECALIBRATION`. | **ROUTE ACCEPTED; SCIENTIFIC CONFIG SELECTION OPEN** | Recalibrate Option B, independently review fit and exact config/space hashes, then select and sign the resulting production proposal. Code-route tests cannot close this requirement. |
| 6 | Provide production evidence schemas that bind the signed plan, scientific code, runtime, inputs, configuration, targets, and seeds. | The config registry, model-specific measured-pilot 2.0, priced-budget 2.0, seed-audit 1.1, M1-anchor, acceptance-input, production-approval, and collaborator-run-sheet tooling/templates are integrated and pass focused adversarial checks. Pilot rows recompute exact vectors/configs/seeds and timing summaries; pricing recomputes six capped M0/M1 stage rows and workflow totals; seed resolutions bind exact source/value hashes and feed RNG findings back into collision checks. Deliberately incomplete templates cannot pass as evidence. The pre-sign acceptance-input draft is stale by design. No timings, prices, machine profile, seed clearance, positive-p result, or variance review was invented. | **SCHEMAS IMPLEMENTED; FINAL REPOSITORY VERIFICATION OPEN; REAL EVIDENCE ABSENT** | Pass the final guarded repository and clean-bundle gates. Then generate each artifact from its real authorized activity, bind it to the exact signed plan and current code/runtime, and obtain the required independent review. |
| 7 | Specify a positive-p M1 confirmation run without executing it. | `POSITIVE_P_M1_CONFIRMATION_PLAN.md` requires independently sourced complete joint vectors, strictly positive p, a reserved 20-seed block, candidate/vector hashes, replicate counts, nominal `I_max`, and nominal-pass status. M1 discovery must union exact-p=0 confirmed M0 embeddings with independently confirmed positive-p M1 vectors. | **SPECIFICATION COMPLETE; EXECUTION OPEN** | Approve the candidate-selection rule and seed block, separately authorize the confirmation, execute it through the governed route, and validate the resulting evidence. |
| 8 | Establish acceptance-input provenance and reconstruct every B_i without double-counting process variance. | The readiness `README.md` and acceptance-input generator define `B_i = V_obs + V_code + (0.10 * abs(z_i))^2 + declared extra non-process terms`. Candidate process variance `s_i^2(1+1/K)` stays outside B_i and enters scoring once. Underivable `V_obs=0` terms are labelled a model assumption that can bias toward over-exclusion relative to an otherwise identical positive missing-variance term. The preserved pre-sign package is explicitly non-production and stale. | **METHOD/GENERATOR COMPLETE; FINAL REVIEWED INPUTS ABSENT** | After plan/config sign-off, regenerate into a new directory, review every component and the 10% discrepancy assumption, approve through the generator's production path, and bind the resulting hashes into the signed prerequisites. |
| 9 | Supply a fail-closed, ordered execution checklist with independent authorization stops. | `GATED_EXECUTION_CHECKLIST.md` orders decision sign-off, Option B recalibration/config selection, input/artifact/registry/seed closure, identity authorization, pilot authorization, priced-budget approval, positive-p confirmation, acceptance-input freeze, zero-blocker validation, and a final separate dispatch authorization. | **CHECKLIST COMPLETE; MOST GATES OPEN** | Close each checkbox only with its named evidence. Never infer permission for a later engine stage from completion of an earlier gate. |
| 10 | Verify and hand off the final integrated repository with exact commands and verdicts. | All planned hardening branches are integrated. Combined pilot/pricing/seed/documentation adversarial checks pass 153/153, the collaborator/config lane passes 97/97, and the broader pre-final lane passed 656/656 before the last two integrations. The prior accepted baseline recorded 2,866 passed / 285 skipped, but neither earlier result is being substituted for the required final full-suite run. The collaborator operator guide, pinned environments, cleanup plan, and source-bundle approach exist. | **FINAL FULL-SUITE, CLEAN-BUNDLE, AND INDEPENDENT AUDIT OPEN** | Run `py_compile`, registry/validator/adversarial checks, the final guarded full suite, and `git diff --check`; verify a clean no-hardlink source-only bundle clone; complete an independent audit; then record exact results and apply only the verified delta to the preserved live checkout. |

## Gate ownership and authorization

The following actions remain outside this code-hardening pass and require real
evidence and/or the named human decision:

1. Recalibrate and scientifically review the Option B candidate.
2. Review and sign all 45 production-plan decisions as their dependencies
   become available.
3. Approve the exhaustive seed-source scope and resolve every ambiguous or
   unparseable reference before allocating candidate blocks.
4. Separately authorize and review the identity gate.
5. Separately authorize the measured pilot, then price and approve the complete
   workflow from those measurements and current account data.
6. Approve and separately authorize positive-p M1 confirmation.
7. Review and approve regenerated observed-value and component-variance
   evidence, including the declared 10% discrepancy assumption and missing
   observation-variance limitations.
8. Approve the exact final production config and complete strict plan sign-off.
9. After every prerequisite validates, separately authorize production
   dispatch.

## No-surprise closure rule

The readiness-package `README.md` states the headline verdict; this matrix is
the authoritative completeness index. A requirement may be marked complete
only when the evidence named in its final column exists and has been checked at
the same scope as the claim. Missing, stale, indirect, template-only, or
proposal-only evidence leaves the corresponding requirement open.
