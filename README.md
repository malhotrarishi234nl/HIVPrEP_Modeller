# HIV PrEP model

Discrete-time stochastic HIV transmission and prevention model for the
Netherlands, developed for Rishi Malhotra's MSc thesis and publication work.

## Current status

- **Code status:** robust-NROY implementation accepted after isolated guarded
  review, subject to the active production-readiness corrections below.
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

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Long-running/external experiment environments may additionally use
`requirements-overnight.txt`. Record Python, NumPy, pandas, SciPy, platform,
and machine versions with every production artifact.

## External inputs

Large/raw inputs and the literature library are intentionally outside Git.
Active workflows currently expect sibling roots:

```text
../data/raw/
../literature/
```

Do not substitute similarly named files. Use the hashes and acquisition notes
in [data/README.md](data/README.md) and
[data/external_inputs.json](data/external_inputs.json). A source-only clone can
run the portable test lane; external-data and artifact lanes require the
documented mounted inputs.

## Testing

No-engine guarded tests patch real `SimulationEngineV3` construction to skip,
so a green guarded run does **not** prove engine identity or production
readiness.

```bash
# Current robust-NROY source gate; no scientific engine construction.
.venv/bin/python -m pytest -p pytest_no_engine_guard -q \
  tests/test_nroy_contract_design.py \
  tests/test_nroy_pipeline_stages.py \
  tests/test_nroy_execution_sharding.py \
  tests/test_nroy_execution_recovery.py \
  tests/test_nroy_graph_report.py \
  tests/test_nroy_identity_gate_artifact.py \
  tests/test_prepare_robust_nroy_acceptance_inputs.py
```

See [documentation/guides/TESTING.md](documentation/guides/TESTING.md) before
interpreting skipped tests or running artifact/engine lanes.

## Ordinary model use

The simulator accepts an ordinary configuration and does not require
calibration machinery. The prepared, **unsigned and not production-ready**
shared candidate is
`configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json`.
It is one stochastic config with an optional stage-structured diagnosis block:

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
