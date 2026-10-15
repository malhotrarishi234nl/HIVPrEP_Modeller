# Robust-NROY production-readiness package — 2026-10-15

## Verdict

- **Code acceptance: ACCEPT.** Current robust-NROY fail-closed code remains
  accepted; the path reconciliation is covered by guarded tests.
- **Production readiness: NOT READY.** The exact current plan hash is
  `e3835812876831af6381e96bc662b75731fe89b35ba261ef79e38e683f181c74`
  and validation reports 43 unsigned required decisions: 29 proposal-backed
  and 14 requiring new decisions.
- No identity gate, pilot, positive-p confirmation, or production simulation was
  run. No historical result was resealed.
- Final isolated no-engine suite: **2589 passed, 29 failed, 284 skipped, 0
  errors**. Every failure is an intentionally stale historical-lineage check.

## Package contents

- `DECISION_REGISTER.csv`: all 43 decisions, split 29/14, with ranges,
  dependencies, consequences, evidence needs, suggested owner role and status.
- `EXTERNAL_INPUT_RECONCILIATION.md`: exact locations/hashes and path fixes.
- `HISTORICAL_ARTIFACT_DISPOSITION.md`: old/current code-map hashes,
  consequences and dispositions for stale scientific lineages.
- `PRODUCTION_CONFIG_ASSESSMENT.md`: proposed-config rejection and exact
  `ProductionEvaluator.config_for` route.
- `PRODUCTION_BACKBONE_RECONCILIATION.md`: exact 1975/1980 lineage evidence,
  compatibility audit, recorded Option B choice, and remaining recalibration
  boundary.
- `POSITIVE_P_M1_CONFIRMATION_PLAN.md`: candidate, seed, vector and evidence
  contract without execution.
- `acceptance_inputs_pre_sign/`: current-source observed values, component
  table and B_i reconstruction, explicitly marked non-production because the
  plan is unsigned.
- `templates/`: deliberately incomplete evidence templates. Their `.template`
  names and null verdicts prevent accidental acceptance.
- `GATED_EXECUTION_CHECKLIST.md`: ordered launch gates and authorization stops.
- `OWNER_LOCK_IN_AND_METHODS_GUIDE.md`: plain-language owner decisions,
  assumptions, methods rationale, reading list, and newly discovered design
  gaps that must close before signing.
- `OWNER_DECISION_PACKET.md`: all 43 unsigned rows grouped by closure route,
  with coupled numeric recommendations, bounded sensitivities, and explicit
  trajectory-cost consequences; it is decision support, not a signature.
- `REPOSITORY_HANDOFF_CLEANUP_PLAN.md`: preservation-first repository and
  artifact cleanup sequence for a reproducible researcher handoff.
- `MODEL_OPERATOR_GUIDE.md`: parameter-focused, no-Python-edit commands for
  explicit standalone M0/M1 validation and execution, with protected fields.

## Acceptance-input provenance

The authoritative values resolve from
`configs/hm_target_register_focused_20260916.json` and its bound
`../data/raw/validation/DiseaseData.xlsx` workbook via the existing
`coupled_calibration_search.load_frozen_targets` path. For every target:

```text
B_i = V_obs + V_code + (0.10 * |z_i|)^2 + declared extra non-process terms
```

Candidate-dependent process variance is excluded from B_i and added only during
scoring as `s_i^2 (1 + 1/K)`. The generator records that the discrepancy was
added exactly once and preserves every component/status in CSV form. Several
V_obs terms remain underivable and zero, which biases toward over-exclusion; the
package does not mislabel those zeros as exact observations.

After sign-off, regenerate rather than promote the pre-sign draft:

```bash
.venv/bin/python tools/prepare_robust_nroy_acceptance_inputs.py \
  --plan <signed-plan.json> \
  --out <new-empty-review-directory>
```

Review the generated component table. The command above deliberately writes a
`FINAL_REVIEW_REQUIRED` package, which cannot prepare work. After approval,
regenerate into a second empty directory and record the reviewer explicitly:

```bash
.venv/bin/python tools/prepare_robust_nroy_acceptance_inputs.py \
  --plan <signed-plan.json> \
  --out <new-empty-approved-directory> \
  --approve-for-production \
  --approved-by "<reviewer name>"
```

The generator refuses an unsigned plan unless `--draft` is explicit and
refuses to overwrite a non-empty directory. `prepare` then validates the
approved `FINAL` package declared by
`production_prerequisites.acceptance_inputs` and seals exact copies of the
observed values, base variances, component budget, and provenance into every
stage. `evaluate` revalidates those sealed inputs. `merge` consumes only the
stage copies; its optional `--observed` and `--base-variance` arguments are
hash assertions for older scripts, not replacement inputs.

The preserved `acceptance_inputs_pre_sign/` files remain bound to the earlier
plan hash `7d113408...` and original deterministic config proposal. The proposal
change intentionally makes that draft stale; do not relabel or promote it.
Regenerate into a new empty evidence directory only after the current plan is
signed.

## Other evidence-generation commands

The following are commands to run only after their preceding gates are complete;
angle-bracketed values must come from the signed plan/current machine. They are
not authorizations.

```bash
# Final validation only; no engine.
.venv/bin/python run_robust_nroy_freeze.py validate --plan <signed-plan.json>

# Identity gate: requires explicit authorization before running.
.venv/bin/python run_robust_nroy_freeze.py identity-gate \
  --plan <signed-plan.json> --out <identity-evidence-dir> <reviewed gate args>

# M1 discovery preparation after positive-p confirmation exists; preparation
# validates the typed evidence before consuming the anchor CSV.
.venv/bin/python run_robust_nroy_freeze.py prepare \
  --plan <signed-plan.json> --model M1 --stage discovery \
  --identity-gate <identity-evidence-dir>/m0_m1_identity_gate.json \
  --m0-confirmed-dir <sealed-m0-confirmation> \
  --anchors <confirmed-positive-p-m1.csv> \
  --m1-anchor-evidence <m1-anchor-confirmation.json> \
  --out <new-m1-discovery-dir>
```

Pilot and pricing generation remain intentionally unimplemented as automatic
“passed” commands: pilot measurements require authorization and real hardware,
and pricing requires current account data and approval. The seed audit now has
a read-only deterministic inventory tool, a proposed source-scope manifest, and
an explicit incomplete artifact. It cannot pass until the owner approves the
complete source scope, every ambiguity is resolved, and candidate discovery,
confirmation, and proposal blocks are supplied. See
`SEED_AUDIT_SCOPE_AND_GAPS.md`.

## Exact remaining approvals

1. Rishi Malhotra reviews and signs the 43 owner-assigned register rows.
2. The owner selected backbone Option B. Recalibrate the prepared V13.22-on-
   1980 stochastic M0/M1 candidate and independently review the resulting fit
   before selecting or signing any production base. The current candidate is
   explicitly not fitted, validated, or production eligible; code-route tests
   do not replace calibration, identity, or scientific review.
3. Choose whether a full preserved backlog edition should be restored as
   authoritative documentation.
4. Approve retirement-only versus current-code rerun for the two stale
   historical lineages; no rerun is necessary merely to preserve history.
5. Review `seed_source_scope.json`, reconcile any remote,
   deleted, collaborator-held, binary, or otherwise excluded sources, resolve
   the inventory's ambiguous/unparseable references, and approve the complete
   seed-source scope. Only afterward supply candidate blocks for collision
   checking; this task does not choose or reserve them.
6. Separately authorize the identity gate.
7. Separately authorize the measured pilot and approve its machine profile.
8. Approve the priced budget derived from that pilot.
9. Approve candidate-selection rules and separately authorize the 20-seed
   positive-p M1 confirmation.
10. Review/freeze the regenerated observed/B_i evidence, including underivable
    V_obs limitations and the declared 10% discrepancy assumption.
11. After validation returns zero blockers, separately authorize production
    dispatch.
