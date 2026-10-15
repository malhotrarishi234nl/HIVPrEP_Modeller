# Proposed production base-configuration assessment

## Finding

The recorded proposal
`configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json`
must **not** be selected as-is:

- `run_control.deterministic_mode.value` is `true`, so it is not a stochastic
  production configuration.
- It has no
  `transmission_model.disease.diagnosis.stage_structured_diagnosis` block, so
  the signed `p_parameter.config_path` cannot be injected through the proposed
  M1 route.

## Exact production route

`ProductionEvaluator.config_for` first injects the complete joint vector through
`run_lhs_calibration.inject_params` and a model-specific calibration space.
For M1, that space is generated from M0 plus p. It then:

1. removes the entire stage-structured diagnosis block for M0;
2. retains that block for M1 and requires it to exist;
3. requires the signed `routine_hazard_scale_identity_value` to equal exactly
   `1.0` and verifies the injected field equals it;
4. uses exact `p == 0.0` dispatch for identity; no epsilon and no positive-p
   snapping exist or are allowed by `FrozenPlan.validate`.

The no-engine production-adapter regression verifies the M0/M1 distinction.
The current proposal fails before this route can be approved because its M1
injection target is absent.

## Recommendation

Prepare a reviewed derivative configuration that is stochastic and explicitly
contains the stage-structured M1 diagnosis block with the signed p path and
`routine_hazard_scale = 1.0`. Diff it against the proposal, validate it through
`ProductionEvaluator.config_for`, and bind its exact hash in the signed plan and
`CONFIG_REGISTRY.md`. This report does not select or sign that derivative.
