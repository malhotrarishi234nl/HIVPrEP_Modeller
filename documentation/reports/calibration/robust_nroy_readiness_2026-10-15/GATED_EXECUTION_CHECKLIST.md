# Gated robust-NROY execution checklist

Every gate is fail-closed. Completion of one gate does not authorize the next
engine-running gate.

- [x] Directed-probe preparation constructs the complete declared
  `direction x scale x sign` design; incompatible partial quotas and attempt
  budgets fail closed, while boundary-infeasible cells remain explicit
  rejected/unresolved cells. This is code acceptance only; the numeric design
  choices remain unsigned.
- [x] The misleading `rank_truncation` decision is replaced by the honest
  `max_directions` count cap. No rank-revealing algorithm is claimed or added;
  the numeric cap remains a scientific/methods decision.
- [x] The graph implementation uses complete pairwise distances followed by a
  deterministic Kruskal MST; no arbitrary graph neighbour count remains. This
  is code acceptance only; the rule remains unsigned scientific/methods input.
- [x] The promotion variance formula is a direct one-sided 95% upper bound at
  proposed `alpha=0.05`, used only for allocation. Final nominal acceptance is
  unchanged; alpha remains unsigned scientific/methods input.
- [x] One canonical stochastic config supports standalone M0 and optional M1;
  the production adapter sets the model switch explicitly and its no-engine
  config-route tests pass. This is code acceptance only; the identity and pilot
  gates below remain open.
- [ ] All 45 decisions reviewed and signed; decision register has no `UNSIGNED` rows.
- [x] The owner selected backbone Option B on 2026-10-15. A hash-bound,
  machine-diffed V13.22-on-1980 stochastic M0/M1 **calibration candidate** now
  records that direction without signing the plan or claiming fit validity;
  see `PRODUCTION_BACKBONE_RECONCILIATION.md`.
- [ ] Recalibrate the Option B 1980 candidate, review the resulting fit and
  exact config/space hashes, then select a production proposal. The current
  candidate is `REQUIRES_RECALIBRATION` and cannot itself close this gate.
- [x] External inputs located and stale path assumptions corrected; the full legacy backlog edition remains an explicit documentation-owner decision.
- [x] Historical artifacts have preservation-first terminal dispositions;
  current claims exclude every pre-Option-B result unless it is explicitly
  revalidated or rerun. All 132 catalog rows are classified and none is marked
  current validated.
- [ ] `CONFIG_REGISTRY.md` generated from the exact signed plan/config/space/targets/runtime and independently reviewed.
- [x] A read-only deterministic `robust_nroy_seed_audit/1.2.0` scanner,
  expanded all-tracked-file source-scope manifest, strict reviewed-resolution
  schema/template, and preceding incomplete inventory artifact exist; no seed
  was allocated and no engine was run.
- [ ] Regenerate the v1.2 inventory where the private mounted `outputs/` tree
  is present; the committed v1.0 artifact remains historical until then.
- [ ] The seed-source scope is owner-approved as exhaustive, all ambiguous and
  unparseable references are resolved, explicit candidate discovery,
  confirmation, and proposal blocks are supplied, and the audit proves no
  internal or prior-source overlap.
- [ ] Identity-gate run separately authorized, executed through the production route, and passed.
- [ ] Measured-pilot run separately authorized and completed on the declared
  machine profile; a scientific reviewer approved the predeclared vector-
  selection rationale and exact timed vectors after Option B recalibration.
- [ ] `robust_nroy_measured_pilot/2.0.0` recomputed from its hash-bound
  `robust_nroy_raw_pilot_measurements/1.1.0` rows, with unique successful
  trajectory ids, exact uint32 signed-block seeds, unique vector/seed
  identities even across model labels, complete finite vectors whose hashes
  recompute in the model-specific coordinate order and whose coordinates lie
  inside the exact hash-bound signed M0/M1 spaces, positive finite elapsed
  measurements, exact M0/M1 scientific
  config hashes, and exact machine/scheduler identity. Strict JSON validation
  rejects duplicate keys and non-finite tokens.
- [ ] `robust_nroy_priced_budget/2.0.0` generated from that measured pilot and
  a hash-bound typed current pricing source. All six M0/M1 simulator-stage
  rows exactly match the signed per-stage caps, and the validator recomputes
  each row and the separately priced entire robust-NROY workflow before human
  approval.
- [ ] Positive-p M1 anchors confirmed on the reserved 20-seed block under separate authorization.
- [ ] Observed values, component variance budget and B_i regenerated from the signed plan; hashes frozen; discrepancy appears exactly once.
- [ ] Repository code-handoff gate passes. The README/index, collaborator
  contract, operator environment, config/external-input registries, portable
  test lane, and complete artifact catalog are prepared, but this checkbox may
  be closed only after the final integrated revision passes the guarded full
  suite, documentation-consistency audit, and fresh source-only clone/bundle
  verification. Mounted raw/literature/output lanes remain separate from a
  source-only clone. Even after closure, this gate is not scientific
  production acceptance.
- [ ] `.venv/bin/python run_robust_nroy_freeze.py validate --plan <signed-plan>` returns zero blockers.
- [ ] Only then request a new, separate authorization for production dispatch.

Current stopping point: the five scientific implementation gates and the
Option B direction choice are closed, while the repository code-handoff gate
is undergoing final re-verification. The calibration candidate is not fitted,
the design values remain unsigned, no fitted production proposal is selected,
and later scientific/evidence gates are open. No engine gate may run merely
from this preparation work.
