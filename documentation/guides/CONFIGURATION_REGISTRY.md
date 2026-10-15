# Configuration registry

## Status and boundary

[`CONFIG_REGISTRY.csv`](../../CONFIG_REGISTRY.csv) is the complete tracked-file
inventory for `configs/`, including its subdirectories and non-JSON workflow
inputs. It is an inventory and review aid, **not production evidence**. Listing
a file does not approve its scientific assumptions, authorize a run, or make it
the current model.

This inventory is deliberately separate from the future plan-bound
`CONFIG_REGISTRY.md` required by robust-NROY production validation. That
production evidence file can be created only after the plan and scientific
backbone are reviewed and signed.

The only excluded filesystem entries are untracked machine/cache debris such as
`.DS_Store`, `__pycache__`, and `.pyc` files. Every repository file under
`configs/` must have exactly one row and one role:

- `current candidate`: the actively prepared candidate, still subject to every
  stated blocker and gate;
- `proposed`: unsigned input to a current decision;
- `historical`: retained for its recorded code/workflow lineage only;
- `diagnostic`: bounded diagnostic input, not production evidence;
- `test/fixture`: test-only data that must never enter scientific runs;
- `suite/tool`: input to a named workflow that needs its own review;
- `review-required`: ambiguous provenance or intended use; do not use yet.

The owner selected Option B and the current shared stochastic M0/M1 file is an
explicit V13.22-on-1980 **recalibration candidate**. Its derivation is
machine-recorded, but its inherited fitted values have not been recalibrated on
that backbone. It is not fitted, validated, or production eligible and cannot
be selected or signed as the production model.

The separately classified
`coupled_search_plan_option_b_1980_research_e2e_20261015.json` is a
`suite/tool` input for an explicitly bounded research rehearsal. Its
`RESEARCH_E2E_ONLY` status is permanent: a completed run cannot promote that
plan, its inputs, or any resulting candidate into production evidence.

## Drift check

The checker is read-only. It fails if a config file is missing or unclassified,
an unexpected file appears, a file hash or extracted provenance/version changes,
references change, or the tracked CSV differs from the canonical rendering.

```bash
.venv/bin/python tools/build_config_registry.py --check
```

To inspect the canonical replacement without writing:

```bash
.venv/bin/python tools/build_config_registry.py --print
```

When adding or changing a config, review its scientific role first, update the
explicit classification/notes in the checker, inspect `--print`, and update the
CSV in the same commit. Never resolve drift by upgrading scientific status from
the filename alone.
