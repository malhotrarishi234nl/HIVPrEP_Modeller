# Testing and evidence lanes

## Why the lanes are separate

The repository contains pure source tests, tests requiring external raw data,
tests validating retained historical artifacts, and tests that construct the
real stochastic engine. A single green number cannot stand for all four.

Code acceptance does not establish scientific acceptance. A guarded test run
also does not establish engine identity because `pytest_no_engine_guard.py`
skips real `SimulationEngineV3` construction.

## Lane 1: portable source gate

Purpose: source contracts, deterministic preparation, recovery logic,
provenance validation, and synthetic/mock calculations. It must not require
`outputs/`, sibling raw data, literature PDFs, or the real engine.

The robust-NROY subset is listed in the root README. The handoff pass will
expand and mark this lane so it can run from a source-only clone.

The configuration-inventory drift check belongs to this lane and runs no
engine:

```bash
.venv/bin/python tools/build_config_registry.py --check
.venv/bin/python -m pytest -q -p pytest_no_engine_guard tests/test_config_registry.py
```

## Lane 2: external-data integration

Purpose: verify target/workbook loaders, raw-data schemas, and literature pins.
It requires the exact mounted files from `data/external_inputs.json`.

Absence of an external input is an environment/setup result, not evidence that
the corresponding scientific test passed.

## Lane 3: retained-artifact validation

Purpose: verify archive integrity, manifests, and current-code refusal of stale
lineages. It requires the artifact vault.

Historical artifacts must be tested positively for their expected status:
their original hashes remain valid, and current production loaders refuse them
when code identity differs. Do not update old hashes or mark such refusals as
unexpected failures.

## Lane 4: real-engine scientific gates

Purpose: exact M0/M1 identity, measured pilots, current-code candidate search,
fresh-seed confirmation, and ultimately production execution.

These are commands with explicit authorization and durable evidence, not
ordinary unit tests. They must record config/code/runtime/input hashes and may
not be inferred from a guarded suite.

## Reporting convention

Every report gives results per lane, including skips and unavailable mounted
inputs. "All tests passed" is permitted only when the named lane and its input
contract are stated.
