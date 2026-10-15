# Robust-NROY production-readiness package — 2026-10-15

## Verdict

- **Code acceptance: ACCEPT.** Current robust-NROY fail-closed code remains
  accepted; the path reconciliation is covered by guarded tests.
- **Production readiness: NOT READY.** The exact current plan hash is
  `740846e686fde6a932906304a85a4a78c8b169264b33a113f64454cc78adb5ab`
  and validation reports 42 unsigned required decisions: 28 proposal-backed
  and 14 requiring new decisions.
- No identity gate, pilot, positive-p confirmation, or production simulation was
  run. No historical result was resealed.
- Final isolated no-engine suite: **2589 passed, 29 failed, 284 skipped, 0
  errors**. Every failure is an intentionally stale historical-lineage check.

## Package contents

- `DECISION_REGISTER.csv`: all 42 decisions, split 28/14, with ranges,
  dependencies, consequences, evidence needs, suggested owner role and status.
- `EXTERNAL_INPUT_RECONCILIATION.md`: exact locations/hashes and path fixes.
- `HISTORICAL_ARTIFACT_DISPOSITION.md`: old/current code-map hashes,
  consequences and dispositions for stale scientific lineages.
- `PRODUCTION_CONFIG_ASSESSMENT.md`: proposed-config rejection and exact
  `ProductionEvaluator.config_for` route.
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
- `REPOSITORY_HANDOFF_CLEANUP_PLAN.md`: preservation-first repository and
  artifact cleanup sequence for a reproducible researcher handoff.

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
  --out <new-empty-evidence-directory>
```

The command refuses an unsigned plan unless `--draft` is explicit and refuses
to overwrite a non-empty directory.

## Other evidence-generation commands

The following are commands to run only after their preceding gates are complete;
angle-bracketed values must come from the signed plan/current machine. They are
not authorizations.

```bash
# Final validation only; no engine.
.venv/bin/python run_robust_nroy_freeze.py validate --plan <signed-plan.json>

# Identity gate: requires explicit authorization before running.
.venv/bin/python run_robust_nroy_freeze.py identity \
  --plan <signed-plan.json> --out <identity-evidence.json> <reviewed gate args>

# M1 discovery preparation after positive-p confirmation exists; preparation
# validates the typed evidence before consuming the anchor CSV.
.venv/bin/python run_robust_nroy_freeze.py prepare \
  --plan <signed-plan.json> --model M1 --stage discovery \
  --m0-confirmation-dir <sealed-m0-confirmation> \
  --anchors <confirmed-positive-p-m1.csv> \
  --m1-anchor-evidence <m1-anchor-confirmation.json> \
  --out <new-m1-discovery-dir>
```

Pilot, pricing and seed-audit generation remain intentionally unimplemented as
automatic “passed” commands: pilot measurements require authorization and real
hardware; pricing requires current account data and approval; the seed audit
must enumerate the agreed complete source set. The templates state the required
bindings and cannot pass the production validator as stored.

## Exact remaining approvals

1. Assign owners and decide/sign the 42 register rows.
2. Approve a reviewed stochastic M1-capable base configuration (the recorded
   proposal is unsuitable as-is).
3. Choose whether a full preserved backlog edition should be restored as
   authoritative documentation.
4. Approve retirement-only versus current-code rerun for the two stale
   historical lineages; no rerun is necessary merely to preserve history.
5. Approve the complete seed-source scope, then reserve the five discovery and
   20 confirmation seeds plus proposal RNG seed.
6. Separately authorize the identity gate.
7. Separately authorize the measured pilot and approve its machine profile.
8. Approve the priced budget derived from that pilot.
9. Approve candidate-selection rules and separately authorize the 20-seed
   positive-p M1 confirmation.
10. Review/freeze the regenerated observed/B_i evidence, including underivable
    V_obs limitations and the declared 10% discrepancy assumption.
11. After validation returns zero blockers, separately authorize production
    dispatch.
