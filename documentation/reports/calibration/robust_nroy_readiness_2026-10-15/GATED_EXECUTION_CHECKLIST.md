# Gated robust-NROY execution checklist

Every gate is fail-closed. Completion of one gate does not authorize the next
engine-running gate.

- [ ] Directed-probe preparation constructs the complete declared
  `direction x scale x sign` design; no total quota can silently omit a scale.
- [ ] The misleading `rank_truncation` decision is replaced by an honestly
  named `max_directions` contract, unless a genuine rank-revealing algorithm is
  separately specified and tested.
- [ ] The graph contract uses a reviewed connectivity proposal rule; the
  preferred simple rule is a complete-distance deterministic MST rather than
  an arbitrary unvalidated `k=3` graph.
- [ ] The promotion variance bound's formula and confidence label agree; the
  preferred rule is a one-sided 95% upper bound at `alpha=0.05`.
- [ ] One canonical stochastic config supports standalone M0 and optional M1;
  the production adapter sets the model switch explicitly and its no-engine
  config-route tests pass.
- [ ] All 43 decisions reviewed and signed; decision register has no `UNSIGNED` rows.
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

Current stopping point: first two gates are open, so no engine gate may run.
