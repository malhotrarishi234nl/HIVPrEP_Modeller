# Shared M0/M1 production base-configuration assessment

## Finding

The original proposal
`configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json`
must **not** be selected as-is:

- `run_control.deterministic_mode.value` is `true`, so it is not a stochastic
  production configuration.
- It has no
  `transmission_model.disease.diagnosis.stage_structured_diagnosis` block, so
  the signed `p_parameter.config_path` cannot be injected through the proposed
  M1 route.

A minimal derivative is now prepared for owner review:

`configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json`

- source SHA-256: `c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187`
- prepared derivative SHA-256: `fd50dd1a98965c9ea6b946c634060b0bd8ddac9334eb62ffc8d9cac67a63e7cf`

Relative to the original, it changes only descriptive metadata, switches
`run_control.deterministic_mode.value` from `true` to `false`, and adds the
complete optional diagnosis block with `enabled=false`,
`routine_hazard_scale=1.0`, `aids_symptom_annual_probability=0.0`, and
`symptom_start_year=1985`. All calibrated scientific parameter values are
unchanged. The exact file hash must be recorded after review.

## Exact production route

`ProductionEvaluator.config_for` first creates an explicit model-specific
runtime copy and then injects the complete joint vector through
`run_lhs_calibration.inject_params`. For M1, that space is generated from M0
plus p. The route:

1. removes the entire stage-structured diagnosis block for M0;
2. explicitly enables that block for M1 rather than trusting the stored flag;
3. requires the complete shared block to exist and the tracked base to default
   to disabled M0 identity values;
4. requires the signed `routine_hazard_scale_identity_value` to equal exactly
   `1.0` and verifies the injected field equals it;
5. uses exact `p == 0.0` dispatch for identity; no epsilon and no positive-p
   snapping exist or are allowed by `FrozenPlan.validate`.

No-engine regressions verify standalone loading, M0 block removal, explicit M1
enablement and p injection, base immutability, and fail-closed missing-block
behaviour. They establish the code route only; they do not establish scientific
identity or runtime suitability.

## Recommendation

Review the prepared derivative and its minimal diff, then bind its exact hash
in the signed plan and `CONFIG_REGISTRY.md`. After that, separately authorize
and pass the real M0/M1 identity gate and measured pilot. This report prepares
the proposal; it does not select it, sign it, or claim scientific acceptance.
