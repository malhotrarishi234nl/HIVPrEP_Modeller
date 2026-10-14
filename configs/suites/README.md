# Experiment suites

This directory contains version-controlled declarations of reproducible model analyses.
Suite files define cases and orchestration; they do not contain simulation results.

- `examples/` contains reviewed or in-progress suite declarations.
- `experiment_suite.py validate` checks a declaration without writing files.
- `experiment_suite.py prepare` resolves each case under `outputs/suites/` and records exact
  configuration diffs, hashes, commands, seeds, stages and source snapshots.
- Scientific runners remain ordinary Python entrypoints and must work without an agent.
- A suite with `launch_ready: false` is review material only; its generated shell script exits
  before running any stage.

Do not move legacy evidence files into this directory merely to make the tree look tidy.
Paths should be migrated only after their consumers and provenance references are inventoried.

The downstream economic PrEP matrix is defined in
`configs/hta_prep_policy_suite_20260929.overlay.json` because it is bound to the
selected outputs of the structural calibration suite. Its zero-simulation
preparer is `tools/prepare_prep_policy_suite.py`; the reviewed design and launch
gate are documented in `documentation/guides/PREP_POLICY_SUITE_20260929.md`.
