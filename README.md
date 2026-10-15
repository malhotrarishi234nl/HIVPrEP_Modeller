# HIV PrEP model

Discrete-time stochastic HIV transmission and prevention model for the
Netherlands, developed for Rishi Malhotra's MSc thesis and publication work.

## Current status

- **Code status:** final integrated revision undergoing guarded full-suite and
  clean-clone handoff re-verification. The earlier robust-NROY baseline passed
  isolated guarded review; that result is not being reused as proof for newer
  collaborator, methods-contract, and vault-portability changes.
- **Scientific production status:** **not yet production-ready**. Do not launch
  production calibration, identity, pilot, M1 confirmation, PSA, or policy
  runs without the explicit gate described in the current readiness package.
- **Current workstream:** shared M0/M1 stochastic config, robust-NROY methods
  corrections, signed inputs, seed audit, measured pilot, and collaborator
  handoff cleanup.
- **Historical results:** preserved for their recorded code snapshots. They are
  not automatically current-code evidence.

Start with:

1. [documentation/INDEX.md](documentation/INDEX.md)
2. [robust-NROY readiness package](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/README.md)
3. [owner decision packet](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_DECISION_PACKET.md)
4. [owner and methods guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_LOCK_IN_AND_METHODS_GUIDE.md)
5. [model operator guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md)
6. [repository handoff cleanup plan](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/REPOSITORY_HANDOFF_CLEANUP_PLAN.md)
7. [configuration guide](configs/README.md)

Older root-level task briefs and runbooks are retained for provenance. A dated
document is not current authority unless the documentation index says so.

## Intended collaborator use

Collaborators are research operators, not code maintainers. `run_model.py`
now provides the prepared parameter-focused M0/M1 route without Python edits;
the [model operator guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md)
gives the ordinary commands and protected-field boundary. This is a tested
code interface, not production authorization: the shared config is unsigned
and the identity, pilot, scientific-review, and dispatch gates remain open.
Real runs require an explicit seed and a new output directory; the runner saves
the exact effective config and a SHA-bound runtime/code reproducibility
manifest alongside the results. Validation-only constructs no engine and
writes no run files. Actual simulations additionally require the canonical
config's protected machine-readable production approval; no current candidate
has it, and the collaborator CLI has no bypass. This leaves mechanical
validation usable while calibration and scientific acceptance are completed.
Collaborator edits are limited to the numeric paths and closed intervals in
`configs/collaborator_parameter_contract.json`; production approval must bind
that file's exact SHA-256. Seeds must be uint32 integers, and the approved
analysis run sheet—not the operator—defines their pairing and replication.

## Safety boundary

Code acceptance is not scientific acceptance, and scientific acceptance is not
authorization to spend compute.

The production sequence is fail-closed:

1. review and sign the complete plan;
2. freeze the stochastic M0/M1 config and acceptance inputs;
3. prove seed disjointness;
4. separately authorize and pass the M0/M1 identity gate;
5. separately authorize and measure a representative pilot;
6. price and approve the full design;
7. separately authorize positive-p M1 search and confirmation;
8. obtain zero-blocker validation and independent final review;
9. separately authorize production dispatch.

See the [gated checklist](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/GATED_EXECUTION_CHECKLIST.md).

## Repository layout

```text
configs/          model, calibration, target, HTA, and experiment declarations
data/reference/   small tracked reference inputs
diagnostics/      diagnostic analyses, not the primary production interface
documentation/    current guides, reports, and historical decision records
tests/            source, integration, artifact, and engine-facing verification
tools/            audits and evidence builders
run_v3.py         simulator implementation
run_robust_nroy_freeze.py
                  fail-closed robust-NROY preparation/execution interface
outputs/          ignored local scientific artifacts; not present in a clone
```

Python modules remain at the root because their paths participate in imports
and scientific provenance hashes. They will not be moved merely for tidiness.

## Environment

For ordinary collaborator M0/M1 use, run these commands from the repository
root with **Python 3.11**:

```bash
python3.11 --version
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-operator.txt
.venv/bin/python -c 'import sys, numpy, pandas, scipy; assert sys.version_info[:2] == (3, 11); print(sys.version.split()[0], numpy.__version__, pandas.__version__, scipy.__version__)'
```

The last command must print Python `3.11.x` followed by the versions pinned in
`requirements-operator.txt`. This small environment is sufficient for
`run_model.py` and its `run_v3.py` engine route. Development, tests,
calibration, robust-NROY, and long-running/external experiment lanes use their
own documented dependencies; `requirements.txt` and
`requirements-overnight.txt` are not the ordinary operator contract. Record
Python, NumPy, pandas, SciPy, platform, and machine versions with every
production artifact.

## External inputs

Large/raw inputs and the literature library are intentionally outside Git.
Active workflows currently expect sibling roots:

```text
../data/raw/
../literature/
```

Do not substitute similarly named files. Use the hashes and acquisition notes
in [data/README.md](data/README.md) and
[data/external_inputs.json](data/external_inputs.json). An ordinary approved
`run_model.py` M0/M1 run is source-only after the operator environment is
installed: it does not read the external validation workbooks, raw-data sibling
roots, or literature library. Calibration, robust-NROY, and data-validation
lanes still require their documented hash-pinned sibling inputs. Their
redistribution decisions remain unresolved, so do not copy or redistribute
those external inputs merely to set up an ordinary model operator.

## Testing

No-engine guarded tests patch real `SimulationEngineV3` construction to skip,
so a green guarded run does **not** prove engine identity or production
readiness.

From a source-only clone, create the pinned Python 3.11 test environment before
running any gate:

```bash
python3.11 -m venv .venv-test
.venv-test/bin/python -m pip install -r requirements-test.txt
```

```bash
# Current robust-NROY source gate; no scientific engine construction.
.venv-test/bin/python -m pytest -p pytest_no_engine_guard -W error -q \
  tests/test_nroy_contract_design.py \
  tests/test_nroy_pipeline_stages.py \
  tests/test_nroy_execution_sharding.py \
  tests/test_nroy_execution_recovery.py \
  tests/test_nroy_graph_report.py \
  tests/test_nroy_identity_gate_artifact.py \
  tests/test_prepare_robust_nroy_acceptance_inputs.py

# Complete guarded repository gate; external/artifact tests report skips when
# their separately documented mounted inputs are absent.
.venv-test/bin/python tools/build_config_registry.py --check
.venv-test/bin/python -m pytest -p pytest_no_engine_guard -W error -q
```

See [documentation/guides/TESTING.md](documentation/guides/TESTING.md) before
interpreting skipped tests or running artifact/engine lanes.

## Ordinary model use

The simulator accepts an ordinary configuration and does not require
calibration machinery. Following the owner's Option B direction, the current
**unsigned, unfitted, and not production-ready** shared starting config is
`configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json`.
It preserves the V13.22 mechanisms on a traceable 1980 initialization and must
be recalibrated before any production promotion. It is one stochastic config
with an optional stage-structured diagnosis block:

- M0: optional M1 mechanism disabled;
- M1: mechanism explicitly enabled and the additional probability injected;
- exact M1 `p=0` with routine hazard scale `1.0`: legacy-path identity boundary.

Use `run_model.py --validate-only` and the operator guide for code-level
preflight. Do not infer scientific acceptance from that result or infer a
canonical production config from filenames such as `model_final.json`. For M1,
the CLI's structural probability check `[0,1]` is not the unsigned robust-NROY
search-range proposal `[0,0.5]`; only a reviewed analysis contract may choose a
scientific range.

## Results and artifacts

`outputs/` is ignored and may contain historical, incomplete, superseded, or
current artifacts. Path presence is not evidence status. Use the artifact
catalog and each artifact's manifest/checksums. Never reseal an old artifact
against current code.

## Citation and licensing

Publication metadata and redistribution decisions are being prepared as part
of the handoff pass. Do not redistribute raw data, literature PDFs, or model
artifacts until their source-specific permissions are recorded.
