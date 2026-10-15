# Configuration guide

## Operator rule

Do not choose a configuration because its filename contains `final`,
`candidate`, or a high version number. Use only a configuration named by the
current operator guide/registry and run its preflight validator before any
simulation.

Collaborators should not edit Python. The supported handoff route will expose
model choice and approved parameter changes through an ordinary CLI and
reviewed JSON files.

## Current robust-NROY inputs

- `robust_nroy_freeze_production.json`: fail-closed production plan. It remains
  deliberately unsigned until every decision and evidence dependency is
  complete.
- `hm_target_register_focused_20260916.json`: proposed 13-target contract and
  source-workbook provenance.
- `calib_space_v13_25_unordered_orientation_20260921.json`: proposed M0
  calibration space. M1 is generated as M0 plus one final coordinate.
- A new shared stochastic M0/M1 base config is being prepared and tested. Until
  it is registered here, no existing base config is the approved production
  base merely by convention.

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

The collaborator package will distinguish:

- **operator-editable:** approved scenario values, run seed, run count, and
  exposed M0/M1 parameter/profile selections;
- **scientifically frozen:** target definitions/order, calibration bounds,
  acceptance rule, discrepancy and variance construction;
- **provenance protected:** config/schema version, source hashes, code/runtime
  identity, reserved seeds, and signed plan fields.

Preflight must reject edits outside the supported surface instead of silently
accepting or discarding them.

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
