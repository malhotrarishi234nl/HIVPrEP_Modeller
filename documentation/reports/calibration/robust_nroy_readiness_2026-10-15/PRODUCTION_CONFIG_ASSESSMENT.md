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

A minimal 1975 derivative was prepared for owner review:

`configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json`

- source SHA-256: `c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187`
- prepared derivative SHA-256: `fd50dd1a98965c9ea6b946c634060b0bd8ddac9334eb62ffc8d9cac67a63e7cf`

Relative to the original, it changes only descriptive metadata, switches
`run_control.deterministic_mode.value` from `true` to `false`, and adds the
complete optional diagnosis block with `enabled=false`,
`routine_hazard_scale=1.0`, `aids_symptom_annual_probability=0.0`, and
`symptom_start_year=1985`. All calibrated scientific parameter values are
unchanged. That minimal mechanical diff is not evidence that the source values
are scientifically suitable for production: the inherited config starts in
1975, uses a 1975 population/age-sex distribution and seed year, and labels the
period-specific demographic inputs as unvalidated diagnostic burn-in settings;
its own notes say production uses the 1980 backbone. The 1975-versus-1980
backbone must therefore be reconciled before selection or signature. The exact
file hash must be recorded after that review.

`PRODUCTION_BACKBONE_RECONCILIATION.md` records the preserved 1980 candidates,
their exact hashes, and the current-space compatibility audit. The owner chose
Option B on 2026-10-15: retain the V13.22 mechanisms, restore only traceable
1980 initialization/backbone fields, and recalibrate.

The unsigned plan now proposes this calibration candidate:

`configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json`

- candidate SHA-256: `49c3953683fac96aafdecdfb54c3195102984a0a29cc32ed3a423238a6498b30`
- derivation manifest:
  `configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.derivation.json`
- manifest SHA-256: `70893d733ba16efd524b3a2b473fd6edc6a62dd616eb21c5e99ddfd02626a36d`

The manifest proves the exact 56-scalar-leaf diff. Apart from four status
metadata leaves, changes are confined to the complete 1980 `start_year`,
`total_size`, `age_sex_distribution`, and epidemic `seed_year` objects copied
from the preserved V13.1 rank-1 artifact. No fitted V13.1 value was
transplanted. The candidate is **REQUIRES_RECALIBRATION**, not a fitted or
validated config and not production eligible.

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

Rerun calibration on the Option B candidate and independently review the
resulting fit before creating any production proposal. Only a later reviewed
fitted config may be hash-bound in the signed plan and `CONFIG_REGISTRY.md`.
After that, separately authorize and pass the real M0/M1 identity gate and
measured pilot. The current plan remains unsigned; this report records a
calibration direction, not scientific acceptance or production authorization.
