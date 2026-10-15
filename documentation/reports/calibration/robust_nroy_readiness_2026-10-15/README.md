# Robust-NROY production-readiness package — 2026-10-15

## Verdict

- **Code acceptance: RE-VERIFICATION REQUIRED FOR THIS REVISION.** The prior
  fail-closed baseline passed an independent adversarial audit. The current
  methods-contract corrections have focused no-engine coverage, but the final
  integrated branch must repeat the guarded repository suite before retaining
  the earlier code-acceptance verdict.
- **Production readiness: NOT READY.** The exact current plan hash is
  `e2a9f4e50ce155be88dacfc161d6ae391bcdbfec70a7613f4914c7fa418d7d02`
  and validation reports 45 unsigned required decisions: 39 proposal-backed
  and 6 budget/seed decisions that correctly remain evidence-dependent.
- No identity gate, pilot, positive-p confirmation, or production simulation was
  run. No historical result was resealed.
- Prior-revision isolated no-engine suite: **2866 passed, 285 skipped, 0
  failed, 0 errors, 0 warnings** in 461.29 seconds. This is retained as baseline
  evidence, not mislabelled as verification of the current revision.

## Package contents

- `PRODUCTION_READINESS_CLOSURE_MATRIX.md`: every requirement in the initiating
  ten-item brief mapped to its evidence, current state, and exact closure
  condition. This is the completeness index; a headline test result cannot
  override an open row.
- `DECISION_REGISTER.csv`: all 45 decisions, split 39 proposal-backed / 6
  evidence-dependent, with ranges,
  dependencies, consequences, evidence needs, suggested owner role and status.
- `EXTERNAL_INPUT_RECONCILIATION.md`: exact locations/hashes and path fixes.
- `HISTORICAL_ARTIFACT_DISPOSITION.md`: old/current code-map hashes,
  consequences and dispositions for stale scientific lineages.
- `PRODUCTION_CONFIG_ASSESSMENT.md`: proposed-config rejection and exact
  `ProductionEvaluator.config_for` route.
- `PRODUCTION_BACKBONE_RECONCILIATION.md`: exact 1975/1980 lineage evidence,
  compatibility audit, recorded Option B choice, and remaining recalibration
  boundary.
- `PRODUCTION_BACKBONE_RECONCILIATION_PRE_OPTION_B_2026-10-15.md`: immutable
  source snapshot for the Option B status-metadata derivation.
- `ARTIFACT_TERMINAL_DISPOSITION_2026-10-15.md`: conservative preservation
  disposition for the final nine formerly ambiguous outputs. The generated
  artifact catalog now classifies all 132 in-scope directories and promotes
  none to current validation.
- `POSITIVE_P_M1_CONFIRMATION_PLAN.md`: candidate, seed, vector and evidence
  contract without execution.
- `acceptance_inputs_pre_sign/`: preserved earlier-plan observed values,
  component table and B_i reconstruction; non-production and stale after the
  current plan/target-metadata revision.
- `templates/`: deliberately incomplete evidence templates. Their `.template`
  names and null verdicts prevent accidental acceptance.
- `GATED_EXECUTION_CHECKLIST.md`: ordered launch gates and authorization stops.
- `OWNER_LOCK_IN_AND_METHODS_GUIDE.md`: plain-language owner decisions,
  assumptions, methods rationale, reading list, and the surfaced choices that
  remain unsigned before launch.
- `OWNER_DECISION_PACKET.md`: all 45 unsigned rows grouped by closure route,
  with coupled numeric recommendations, bounded sensitivities, and explicit
  trajectory-cost consequences; it is decision support, not a signature.
- `REPOSITORY_HANDOFF_CLEANUP_PLAN.md`: preservation-first repository and
  artifact cleanup sequence for a reproducible researcher handoff.
- `MODEL_OPERATOR_GUIDE.md`: parameter-focused, no-Python-edit commands for
  explicit standalone M0/M1 validation and execution, with protected fields.
- `COLLABORATOR_ANALYSIS_RUN_SHEET.json.template`: deliberately incomplete
  typed authorization record binding one reviewed config, model, p/null,
  single uint32 seed, canonical absolute output destination, scientific code,
  operator requirements, scientific approval, and the later dispatch
  authorization. Actual runs seal a byte-exact copy.

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
plan hash `7d113408...`, original deterministic config proposal, and target
register hash `87db6840...`. The current target register is version 1.3 and now
describes the rule actually used by scoring: target-relative 10% discrepancy as
a project assumption. These contract changes intentionally make that draft
stale; do not relabel or promote it. Regenerate into a new empty evidence
directory only after the current plan is signed.

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
# consumes the identity and anchor-confirmation paths signed in
# production_prerequisites, seals their exact contents (including the
# independent-anchor and per-seed files), and revalidates them at dispatch.
.venv/bin/python run_robust_nroy_freeze.py prepare \
  --plan <signed-plan.json> --model M1 --stage discovery \
  --m0-confirmed-dir <sealed-m0-confirmation> \
  --anchors <confirmed-positive-p-m1.csv> \
  --out <new-m1-discovery-dir>
```

Optional `--identity-gate` and `--m1-anchor-evidence` arguments are compatibility
assertions only: each must resolve to the exact corresponding signed prerequisite
path. They cannot substitute another otherwise-valid certificate at prepare time.

Pilot and pricing generation remain intentionally unimplemented as automatic
“passed” commands: pilot measurements require authorization and real hardware,
and pricing requires current account data and approval. The validator does not
trust authored timing or cost totals. `robust_nroy_measured_pilot/2.0.0` must
hash-bind a strict `robust_nroy_raw_pilot_measurements/1.1.0` JSON artifact;
duplicate keys and non-standard `NaN`/`Infinity` tokens are rejected. Its rows
have globally unique trajectory ids, `SUCCESS` status, positive finite elapsed
seconds and positive integer peak memory under the exact declared machine and
scheduler request. Every row also carries M0/M1 identity, an exact native
uint32 from the signed discovery block, the complete finite timed parameter
vector and its recomputed model-specific vector SHA-256, plus the
recomputed model-specific scientific-config SHA-256. Every discovery seed must
appear exactly once for each model and a vector/seed identity cannot be
reused, including across model labels. The raw artifact also records exact M0
and M1 parameter-space identities covering the ordered names, inclusive lower
and upper bounds, and config paths. The validator recomputes those identities
from the signed plan and rejects every timed coordinate outside its exact
model-specific bounds, even when the vector and enclosing artifact hashes were
consistently regenerated. The validator independently recomputes
separate M0 and M1 counts, ids, seeds, arithmetic-mean elapsed time, observed
maximum, total elapsed time and peak memory. The model-specific arithmetic means are the simple
predeclared timing statistics used for expected full-capacity pricing; neither
is silently projected onto the other model or claimed to describe a population
timing distribution.

Bounds establish admissibility, not timing representativeness. No generic
vector-selection rule is invented here: the defensible rule depends on the
authorized pilot objective and the newly calibrated Option B population. Before
closing the measured-pilot checklist item, the scientific reviewer must approve
the predeclared selection rationale and the exact timed vectors. The resulting
budget remains conditional on those vectors; a mechanical PASS alone does not
establish that they span worst-case runtime behavior.

`robust_nroy_priced_budget/2.0.0` must also hash-bind a typed
`robust_nroy_pricing_source/1.0.0` for that machine profile. The source records
currency/credit unit, resource unit and a positive price per resource-hour.
The budget contains exactly six simulator-stage rows: discovery, confirmation
and bridges for M0 and M1. Promotion and regions are allocation/reporting
stages and do not dispatch trajectories. Every row repeats the exact signed
per-stage trajectory, attempt and wall-time caps. The validator recomputes:

```text
expected resource-seconds = max_trajectories
                            x model-specific measured mean elapsed seconds
                            x billable_resources
cap-bounded worst resource-seconds = wall_time_cap_seconds x billable_resources
cost = resource-seconds / 3600 x unit_price_per_resource_hour
workflow cost = sum of the six recomputed simulator-stage rows
```

The observed pilot maximum remains visible as a diagnostic but is not treated
as a hard bound. “Worst” is the hard billing ceiling under the declared
scheduler size and signed wall-time cap; expected full-capacity resource time
must fit beneath it. The attempt cap is still copied and checked exactly because
it limits dispatches, but no observed duration is mislabelled as an upper bound
for failed or future attempts. Human approval remains required, and a priced
artifact fails if its formula, source inputs, stage coverage, plan caps, row
totals or workflow totals differ from recomputation. The separate identity, positive-p anchor,
pilot itself and downstream policy/PSA costs are not hidden inside these six
robust-NROY stage rows and must be costed under their own authorization where
applicable.

The seed audit now has
a read-only deterministic v1.2 inventory tool, a proposed all-tracked-file
source-scope manifest, and a strict hash-bound reviewed-resolution contract.
Every output excluded from seed parsing is indexed by path, kind, size, and
content SHA-256 and requires an exact per-file or exact whole-index review;
external location classes require separate typed review records. Production
validation rebuilds the audit from current sources instead of trusting a
self-consistent JSON assertion. The committed v1.0 mounted-output artifact remains historical until v1.2 is
regenerated where that private output tree is present. The gate cannot pass
until the owner approves the complete source scope, every ambiguity has an
exact reviewed RNG/NON_RNG disposition, and candidate discovery, confirmation,
and proposal blocks are supplied. See
`SEED_AUDIT_SCOPE_AND_GAPS.md`.

## Exact remaining approvals

1. The owner selected backbone Option B. Recalibrate the prepared V13.22-on-
   1980 stochastic M0/M1 candidate and independently review the resulting fit
   before selecting or signing any production base. The current candidate is
   explicitly not fitted, validated, or production eligible; code-route tests
   do not replace calibration, identity, or scientific review.
2. Review and sign all 45 owner-assigned decision rows when their dependent
   evidence exists, then complete the separate strict sign-off block. A signed
   plan still does not authorize compute.
3. Review `seed_source_scope.json`, reconcile any remote,
   deleted, collaborator-held, binary, or otherwise excluded sources, resolve
   the inventory's ambiguous/unparseable references, and approve the complete
   seed-source scope. Only afterward supply candidate blocks for collision
   checking; this task does not choose or reserve them.
4. Separately authorize the identity gate.
5. Separately authorize the measured pilot, approve its machine profile, and
   approve the priced budget derived from it.
6. Approve candidate-selection rules and separately authorize the 20-seed
   positive-p M1 confirmation.
7. Review and approve the regenerated observed/B_i evidence, including underivable
    V_obs limitations and the declared 10% discrepancy assumption.
8. Generate and review the final signed-plan `CONFIG_REGISTRY.md`, then require
   zero-blocker validation of every exact signed prerequisite path and hash.
9. After scientific acceptance, promote the canonical collaborator config with
   its protected production-approval record.
10. Separately authorize production dispatch.
