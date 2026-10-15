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
- [ ] One canonical stochastic config supports standalone M0 and optional M1;
  the production adapter sets the model switch explicitly and its no-engine
  config-route tests pass.
- [ ] All 42 decisions reviewed and signed; decision register has no `UNSIGNED` rows.
- [ ] A stochastic M1-capable base configuration is selected and hashed.
- [x] External inputs located and stale path assumptions corrected; the full legacy backlog edition remains an explicit documentation-owner decision.
- [ ] Historical artifacts have an approved disposition; current claims exclude unrepeated stale lineages.
- [ ] `CONFIG_REGISTRY.md` generated from the exact signed plan/config/space/targets/runtime and independently reviewed.
- [ ] `robust_nroy_seed_audit/1.0.0` generated from all prior seed sources and passes with no overlaps.
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

Current stopping point: the first two implementation gates are closed, but the
design values remain unsigned and later scientific/evidence gates are open, so
no engine gate may run.
