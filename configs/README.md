# Configuration guide

## Operator rule

Do not choose a configuration because its filename contains `final`,
`candidate`, or a high version number. Use only a configuration named by the
current operator guide/registry and run its preflight validator before any
simulation.

Collaborators should not edit Python. The prepared handoff route exposes model
choice through `run_model.py` and parameter changes through reviewed JSON
copies. Follow the
[model operator guide](../documentation/reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md).
The route is code-tested but is not a production approval.

## Current robust-NROY inputs

- `robust_nroy_freeze_production.json`: fail-closed production plan. It remains
  deliberately unsigned until every decision and evidence dependency is
  complete.
- `hm_target_register_focused_20260916.json`: proposed 13-target contract and
  source-workbook provenance.
- `calib_space_v13_25_unordered_orientation_20260921.json`: proposed M0
  calibration space. M1 is generated as M0 plus one final coordinate.
- `V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json`:
  prepared shared stochastic M0/M1 candidate. It defaults safely to standalone
  M0 and is the proposal recorded in the unsigned robust-NROY plan. It is
  **not production-ready** until it is selected, signed, hash-bound, and the
  identity, pilot, scientific-review, and dispatch gates pass.

## Broad file classes

- `v6_*`, `v10_*`, and `v11_*`: legacy compatibility/test material.
- `1980_test.json`: short/testing and identity-gate support; not automatically
  the production base.
- dated `*_audit_*`, diagnostic, wave, search, and confirmation plans:
  historical experiment declarations tied to their recorded code/artifacts.
- `hta_*`, `psa_*`, and suite files: HTA/PSA declarations; production use
  requires their own current registry entry and evidence chain.
- generic names such as `model_final.json`, `calib_space.json`, and
  `psa_config.json`: ambiguous historical convenience names; do not use them
  as implicit current authority.

## Parameter editing boundary

The collaborator route distinguishes:

- **operator-editable:** existing scientific parameter `value` payloads in a
  copied config, plus seed, M0/M1 selection, M1 `p`, and output directory via
  `run_model.py` command-line options;
- **scientifically frozen:** target definitions/order, calibration bounds,
  acceptance rule, discrepancy and variance construction;
- **provenance protected:** config/schema version, source hashes, code/runtime
  identity, reserved seeds, and signed plan fields.

Preflight must reject edits outside the supported surface instead of silently
accepting or discarding them.

The collaborator validator compares a supplied copy with the canonical
candidate. Object keys, list lengths, JSON value types, and every field outside
an existing scientific `value` payload must match. In particular, do not edit
`meta`, `run_control`, `output`, `_readme`, diagnostic/calibration provenance,
or the saved `stage_structured_diagnosis` block. A rejection names the first
forbidden path; start again from a fresh canonical copy rather than repairing
an uncertain schema by hand.

`run_model.py` structurally validates the M1 symptom probability as a
mathematical probability in `[0,1]`. That does not approve the entire interval
for robust-NROY or any other scientific analysis. The current robust-NROY plan
contains an **unsigned proposal** of `[0,0.5]`; it remains a decision until the
owner reviews and signs it. Do not hard-code or infer a scientific range from
the CLI's structural validation.

## Adding or promoting a config

Record:

1. stable purpose and status;
2. parent config and a human-readable diff;
3. SHA-256;
4. consuming command/workflow;
5. model mode and stochastic/deterministic semantics;
6. external inputs;
7. test/evidence result;
8. approving owner and date.

Promotion to production additionally requires the current gated checklist.
