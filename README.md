# HIV PrEP model

Discrete-time stochastic HIV transmission and prevention model for the
Netherlands, developed for Rishi Malhotra's MSc thesis and publication work.

## Current status

- **Bounded Option-B research rehearsal:** **COMPLETE** on clean commit
  `ad129527f265bef48c9bb547c5993b989a6c467b`. The eight-worker run evaluated
  384 candidates using 1,920 discovery trajectories, then evaluated a frozen
  10-candidate shortlist using 200 fresh-seed confirmation trajectories. One
  candidate passed on the common discovery seeds; **zero candidates passed
  fresh-seed confirmation**. No PSA was run. This verifies the bounded E2E
  mechanics, not calibration adequacy or production readiness. See the
  [current Option-B research-E2E guide](documentation/guides/OPTION_B_RESEARCH_E2E_20261015.md).
- **Reviewer-readable calibration output:** implementation through commit
  `0231e5e` adds percentage-error bands and explicit parameter-boundary labels
  alongside the formal implausibility results. These additions did not rerun
  or reseal the preserved rehearsal; its exact code identity remains the one
  recorded in its manifests.
- **Collaborator source-code handoff:** **COMPLETE** at commit
  `68112bba96402dbbf43c4456289f4384b9635cbe` (tree
  `bcc902476f507d745765e9d9835e341651e70d21`). The mounted-evidence guarded
  suite passed 3,174 tests with 288 skips and no failures or errors; an
  independent adversarial audit accepted the exact commit; and the verified
  source bundle reproduced the commit in a clean clone. This accepts the code
  and ordinary M0/M1 operator surface, not a fitted production model.
- **Scientific production status:** **not yet production-ready**. Do not launch
  production calibration, identity, pilot, M1 confirmation, PSA, or policy
  runs without the explicit gate described in the current readiness package.
- **Next scientific sequence:** review the bounded rehearsal and decide whether
  to run the unchanged full search to test whether the miss was caused by
  limited search coverage. Any larger search still requires its own applicable
  authorization and resource controls. Select a fitted joint vector only after
  fresh-seed confirmation; a conditional PSA comes after that selection, not
  from the failed rehearsal shortlist. Production promotion still requires the
  separate readiness gates and the unresolved robust-NROY decisions.
- **Historical results:** preserved for their recorded code snapshots. They are
  not automatically current-code evidence.

Start with:

1. [documentation/INDEX.md](documentation/INDEX.md)
2. [Option-B research-E2E guide and completed rehearsal result](documentation/guides/OPTION_B_RESEARCH_E2E_20261015.md)
3. [robust-NROY readiness package](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/README.md)
4. [requirement-by-requirement closure matrix](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/PRODUCTION_READINESS_CLOSURE_MATRIX.md)
5. [owner decision packet](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_DECISION_PACKET.md)
6. [owner and methods guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_LOCK_IN_AND_METHODS_GUIDE.md)
7. [model operator guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md)
8. [repository handoff cleanup plan](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/REPOSITORY_HANDOFF_CLEANUP_PLAN.md)
9. [configuration guide](configs/README.md)

Older root-level task briefs and runbooks are retained for provenance. A dated
document is not current authority unless the documentation index says so.

## Intended collaborator use

Collaborators are research operators, not code maintainers. The source-code
handoff is complete: `run_model.py` provides the tested parameter-focused
M0/M1 route without Python edits;
the [model operator guide](documentation/reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md)
gives the ordinary commands and protected-field boundary. This is a tested
and clean-clone-verified code interface, not production authorization: the
shared config is unsigned and the identity, pilot, scientific-review, and
dispatch gates remain open.
Real runs require an explicit seed and a new output directory; the runner saves
the exact effective config and a SHA-bound runtime/code reproducibility
manifest alongside the results, and it refuses to mark a run complete unless
the protected `baseline_run_001.csv` is a nonempty regular file. Validation-only
constructs no engine and writes no run files. Actual simulations additionally
require the canonical config's protected machine-readable production approval
and a SHA-bound `collaborator_analysis_run_sheet/1.1.0` carrying both scientific
approval and separate dispatch authorization; no current candidate has these,
and the collaborator CLI has no bypass. This leaves mechanical validation
usable while calibration and scientific acceptance are completed.
The runner seals a byte-exact fixed-name copy of that sheet beside every
prepared run so later loss of the operator's source path cannot erase the
approval and dispatch record.
Collaborator edits are limited to the numeric paths and closed intervals in
`configs/collaborator_parameter_contract.json`; production approval must bind
that file's exact SHA-256, the scientific source aggregate, and the exact
operator-requirements file. Each approved analysis run sheet authorizes exactly
one model, `p` value, uint32 seed, and canonical absolute output destination. A separate
sheet is required for every additional invocation; this runner does not claim
to enforce cross-model pairing or an analysis-wide replication design.

## Safety boundary

Code acceptance is not scientific acceptance, and scientific acceptance is not
authorization to spend compute.

The production sequence is fail-closed:

1. review and authorize the exact Option-B inputs and disjoint seed blocks;
2. separately authorize preparation and its paired identity smoke;
3. bind a measured benchmark, current pricing and the live execution profile
   before coupled search or resume;
4. recalibrate Option B, independently review fit, and select the fitted config;
5. finalize the 45 robust-NROY decisions against that scientific base and
   complete the separate strict sign-off;
6. separately authorize and pass the robust M0/M1 identity gate;
7. separately authorize the measured pilot and approve the full priced design;
8. separately authorize positive-p M1 search and confirmation;
9. obtain zero-blocker validation and independent final scientific review;
10. separately authorize production dispatch.

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
production artifact. Actual `run_model.py` invocations independently enforce
CPython 3.11 and every exact operator pin before importing the engine;
validation-only remains usable without crossing that execution boundary.

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

This distinction is visible in testing. With the canonical external workbooks
and historical artifact mount available, the final guarded suite passed. In
the clean source-only clone it reported 3,019 passed, 327 skipped, 39 failed,
and 77 errors: those failures/errors require omitted evidence such as
`DiseaseData.xlsx`, the retained RIVM target candidate, and historical
calibration, smoke, abstract-evidence, HTA-audit, and prepared-suite output
trees. That lane is therefore an honest dependency diagnostic, not a green
repository-wide evidence test and not a blocker for ordinary source-code use.

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

Final handoff evidence for commit `68112bba96402dbbf43c4456289f4384b9635cbe`:

- mounted-evidence guarded suite: **3,174 passed, 288 skipped, 0 failed/errors**
  in 471.17 seconds, exit 0;
- config registry: **78 files**, SHA-256
  `e21c290072f88708356798725204d0ec5c6f0afed6629d6f89c4dcfdeaa994bc`;
- clean bundle clone: exact commit, clean status, strict `git fsck`, no tracked
  symlinks, `.venv`, `node_modules`, or `outputs`, and both M0 and M1-at-p=0
  validation-only routes passed without constructing an engine;
- source-only guarded suite: **3,019 passed, 327 skipped, 39 failed, 77
  errors** because external evidence was deliberately omitted;
- dependency locks: `requirements.txt` SHA-256
  `3a7ba47591451816350c1e4fb81e6e0e60dcf800bbdbb971eb43df01f2feaa74`
  and `requirements-test.txt` SHA-256
  `8bacaf9e55cd3c00dea363cc3fe4b3a0e752c8b81b67c91e75c6a6770da7a26f`.

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

For production work, do not sign the robust plan merely because the candidate
validates mechanically. First authorize the hash-bound Option-B preparation
plan and its seed evidence, run preparation and the paired identity smoke,
review the recalibration, and select the resulting fitted config. Only then
resolve the final 45 robust-NROY decisions against that fitted scientific
base. A coupled search or resume also refuses to start without the exact
measured benchmark, pricing source/approval, conservative cost ceiling, and
local-or-Slurm execution profile matching the live environment.

## Results and artifacts

`outputs/` is ignored and may contain historical, incomplete, superseded, or
current artifacts. Path presence is not evidence status. Use the artifact
catalog and each artifact's manifest/checksums. Never reseal an old artifact
against current code.

The verified complete-history code bundle is
`../hiv-prep-model-deliverables-20261015/hiv-prep-model-code-68112bb.bundle`
(11,117,987 bytes; SHA-256
`4a9708dba8946277eadf21ecaef9ac0cdcb9c4c7837b88df8050642e4b3bd371`).
`git bundle verify` passed. The bundle contains source history, not the ignored
scientific artifact vault or external workbooks.

## Citation and licensing

Publication metadata and redistribution decisions are being prepared as part
of the handoff pass. Do not redistribute raw data, literature PDFs, or model
artifacts until their source-specific permissions are recorded.
