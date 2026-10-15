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

The shared stochastic M0/M1 candidate remains explicitly blocked. It inherits a
diagnostic 1975 backbone and unvalidated period inputs that must be reconciled
with the intended 1980 production backbone before selection or signature.

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
