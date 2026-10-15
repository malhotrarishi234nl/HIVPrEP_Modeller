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

Use Python 3.11 and the pinned test environment. This is deliberately separate
from `requirements-operator.txt`: an operator validating or running M0/M1 does
not need pytest, workbook, plotting, or table-rendering dependencies.

```bash
python3.11 --version
python3.11 -m venv .venv-test
.venv-test/bin/python -m pip install -r requirements-test.txt
.venv-test/bin/python -m pip check
```

The first command must report Python `3.11.x`. `requirements-test.txt` is a
strict, pinned superset of the operator runtime contract. The robust-NROY
focused subset is listed in the root README.

The configuration-inventory drift check belongs to this lane and runs no
engine:

```bash
.venv-test/bin/python tools/build_config_registry.py --check
.venv-test/bin/python -m pytest -p pytest_no_engine_guard -W error -q
```

That second command is the complete guarded repository gate. In a source-only
clone, tests whose declared external-data or retained-artifact mounts are
absent must skip with an explicit reason; they are not counted as passed. Real
engine construction is also skipped by the guard. Report the exact pass/skip
counts and the tested Git commit or bundle hash.

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
