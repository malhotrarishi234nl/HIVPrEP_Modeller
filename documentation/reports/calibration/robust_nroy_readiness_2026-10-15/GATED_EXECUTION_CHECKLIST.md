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
- [ ] All 43 decisions reviewed and signed; decision register has no `UNSIGNED` rows.
- [x] The owner selected backbone Option B on 2026-10-15. A hash-bound,
  machine-diffed V13.22-on-1980 stochastic M0/M1 **calibration candidate** now
  records that direction without signing the plan or claiming fit validity;
  see `PRODUCTION_BACKBONE_RECONCILIATION.md`.
- [ ] Recalibrate the Option B 1980 candidate, review the resulting fit and
  exact config/space hashes, then select a production proposal. The current
  candidate is `REQUIRES_RECALIBRATION` and cannot itself close this gate.
- [x] External inputs located and stale path assumptions corrected; the full legacy backlog edition remains an explicit documentation-owner decision.
- [ ] Historical artifacts have an approved disposition; current claims exclude unrepeated stale lineages.
- [ ] `CONFIG_REGISTRY.md` generated from the exact signed plan/config/space/targets/runtime and independently reviewed.
- [x] A read-only deterministic `robust_nroy_seed_audit/1.0.0` scanner,
  explicit proposed source-scope manifest, and incomplete inventory artifact
  exist; no seed was allocated and no engine was run.
- [ ] The seed-source scope is owner-approved as exhaustive, all ambiguous and
  unparseable references are resolved, explicit candidate discovery,
  confirmation, and proposal blocks are supplied, and the audit proves no
  internal or prior-source overlap.
- [ ] Identity-gate run separately authorized, executed through the production route, and passed.
- [ ] Measured-pilot run separately authorized and completed on the declared machine profile.
- [ ] `robust_nroy_priced_budget/1.0.0` generated from the measured pilot, priced, and approved.
- [ ] Positive-p M1 anchors confirmed on the reserved 20-seed block under separate authorization.
- [ ] Observed values, component variance budget and B_i regenerated from the signed plan; hashes frozen; discrepancy appears exactly once.
- [ ] Repository handoff gate passes: current README/index, config and external-
  input registries, portable test lane, and artifact catalog contain no
  unclassified current dependency.
- [ ] `.venv/bin/python run_robust_nroy_freeze.py validate --plan <signed-plan>` returns zero blockers.
- [ ] Only then request a new, separate authorization for production dispatch.

Current stopping point: the five implementation gates and the Option B
direction choice are closed, but the calibration candidate is not fitted, the
design values remain unsigned, no production backbone is selected, and later
scientific/evidence gates are open. No engine gate may run merely from this
preparation work.
