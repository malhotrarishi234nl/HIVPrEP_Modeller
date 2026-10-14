# Reproducible Experiment Suite Architecture

## Purpose

The experiment-suite layer makes model analyses inspectable, reproducible and usable
without an AI agent. Agents may explain assumptions, co-author suite files and interpret
results. They are not part of the execution dependency chain.

The suite layer wraps existing scientific runners. It does not replace calibration,
simulation, scenario or reporting code, and it does not move completed output archives.

## Component boundary

```text
scientific assumption
  -> human-readable suite case
  -> resolved configuration plus exact diff
  -> ordinary terminal command
  -> run/seed/shard ledger
  -> stable raw result contract
  -> optional table, figure and package builders
  -> reported claim
```

The components are deliberately separate:

1. **Model configuration** defines mechanisms and parameter values.
2. **Suite configuration** defines cases, stages, seeds, shards and comparisons.
3. **Scientific runners** perform calibration or simulation.
4. **Analysis adapters** normalize completed runner outputs into stable tables.
5. **Export profiles** select tables, figures and workbooks without rerunning simulation.

## Non-negotiable requirements

- Every scientific case is declared in version-controlled JSON.
- Derived configurations exist on disk before an engine is constructed.
- Every override records its JSON pointer, previous value and new value.
- An artifact touched only by `test` assertions remains byte-identical to its source, so an
  unchanged baseline retains the exact published or previously executed hash.
- Coupled files are changed together. A schedule-knot change, for example, must update
  both the model configuration and every calibration-space path that addresses the knot.
- Validation and preparation start zero simulations.
- Preparation emits commands that a researcher can inspect, copy and run directly.
- Seed lists and shard allocation are explicit in the prepared ledger.
- Existing runners keep their own validation, time-budget, resume and refusal rules.
- Raw simulation outputs are independent of figure and workbook choices.
- Every completed run records source state, input hashes and runtime entrypoints.
- A dirty source tree is disclosed; named runtime files are snapshotted and hashed.
- Completed raw-output directories are immutable inputs to later reporting stages.

## Initial implementation

`experiment_suite.py` intentionally implements only the preparation contract:

```bash
.venv/bin/python experiment_suite.py validate \
  --suite configs/suites/examples/on_art_break_year_2008_vs_2010.json

.venv/bin/python experiment_suite.py prepare \
  --suite configs/suites/examples/on_art_break_year_2008_vs_2010.json \
  --output-dir outputs/suites/on_art_break_year_2008_vs_2010_<date>
```

`validate` writes nothing. `prepare` refuses to overwrite an existing directory and writes:

- the source suite;
- resolved JSON artifacts for every case;
- JSON and CSV configuration-change ledgers;
- artifact and provenance hashes;
- a stage/seed/shard ledger;
- source snapshots named in the suite; and
- `commands.sh` plus a machine-readable command register.

Preparation is not authorization to run an analysis. The generated commands remain
subject to scientific review and the underlying runner's checks.

A suite must set `launch_ready` explicitly. When it is false, preparation preserves the
proposed commands for review but `commands.sh` exits before executing any of them. Blocking
conditions belong in `launch_blockers`; an agent or researcher must not work around them.

### Coupled-calibration adapter status

The initial break-year example exposed a legacy coupling in `run_coupled_calibration.py`.
The first runner adapter now reads experiment identity, authoritative input paths and hashes,
and seed values from the supplied plan. The reviewed method remains deliberately frozen:
targets, parameter blocks, proposal method, acceptance rule, replicate counts and budgets
must still match the code contract exactly. This is the intended boundary for the first
publishable sensitivity suite: configurable experiment data without an unchecked generic
calibration language.

The break-year example now declares one fresh CRN reservation shared only by its two named
case designs, with equal replicate counts and an explicit two-case maximum budget. The
runner records the comparison group in manifests and rejects undeclared reuse. The standalone
`compare_coupled_calibration_suite.py` adapter validates completed search and confirmation
artifacts and emits normalized case, candidate and target tables without starting a simulation.
The suite remains non-launchable until the final human review is recorded.

## Directory policy

New suite work has a stable directory boundary:

```text
configs/suites/
  README.md
  examples/                 reviewed, version-controlled suite declarations

outputs/suites/<suite_run>/
  suite_source.json         exact declaration used for preparation
  cases/<case>/
    resolved_configs/       exact model, calibration and runner inputs
    calibration/            prepared/smoke/search/confirmation stage outputs
  config_changes.{json,csv}
  run_ledger.csv
  commands.{sh,json}
  provenance.json
  source_snapshot/
```

Legacy dated configs, runners and completed outputs are not moved in this phase. They have
many path-based dependencies and are evidence-bearing artifacts. Their eventual migration
requires a reference inventory, compatibility aliases where needed, and hash-preserving
archive rules. New suite files should not add more top-level one-off launch scripts.

## Patch contract

Case changes use JSON Pointer paths and the operations `test`, `add`, `replace`, `remove`
and `move`. A `test` should precede scientifically material changes. References to earlier
resolved artifacts are supported:

```json
{"$artifact": "model_config"}
{"$sha256": "model_config"}
```

This allows a generated orchestration plan to name and hash the exact generated model and
calibration-space files rather than silently referring back to mutable originals.

Artifacts are resolved in the order declared. References can therefore point only to an
earlier artifact, keeping dependencies acyclic and easy to audit.

## Reporting and figure direction

The next layer should consume completed runs through named output adapters and expose a
registry such as:

```text
figures: none | all | calibration | projection | economic | [explicit figure IDs]
tables:  none | all | [explicit table IDs]
package: professor | manuscript | archive | custom
```

Figure generation must never alter raw results. A figure registry entry should declare
its required normalized tables, builder function, output files and scientific caveats.
This makes alternative figure designs reproducible choices rather than one-off agent work.

## Deferred work

- Execution and resume/status adapters for runners other than coupled calibration.
- A normalized raw-results contract across calibration and HTA runners.
- Declarative scenario primitives such as target-stock pathways.
- Comparison, figure and workbook registries.
- Publication archive validation from a clean tagged source tree.

These should be added against real analyses one adapter at a time. The suite schema should
not attempt to encode every possible scenario before those semantics are stable.
