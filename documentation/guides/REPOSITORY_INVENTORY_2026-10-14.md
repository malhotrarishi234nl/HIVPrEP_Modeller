# Repository inventory and production entry points — 2026-10-14

Produced by the bounded repository-hygiene pass required by
`documentation/reports/calibration/PRODUCTION_LOCKIN_SPEC_2026-10-13.md` section 4.

**Scope and discipline.** The specification asks for an inventory, clearly
identified production entry points, and the movement of *only demonstrably
superseded* exploratory material into a labelled historical folder, with
ambiguous or still-referenced files left in place and listed for review. It
also states that "age or filename alone is never sufficient evidence that a
file is stale". This pass therefore moved two files and listed everything else.

## 1. What moved

Two files, into `archive/historical_2026-10-14/` (see its `README.md`):

| file | evidence it is superseded |
|---|---|
| `fix_thesis.py` | Zero inbound references. Hardcodes `/sessions/nifty-vigilant-mayer/mnt/Modeller/v11/THESIS_FINAL.docx`, an absolute path from another environment that does not exist here, so it cannot run. Unrelated to model, calibration or economics. |
| `__delete_test.txt` | Zero bytes, zero references, named `__delete_test`, present since the initial commit. |

Nothing was deleted. `archive/` is already excluded from pytest collection by
`pytest.ini` (`norecursedirs`), so the move cannot change test behaviour.

A reference scan of **every** top-level module (searching `*.py`, `*.md`,
`*.json`, `*.ini`, `*.txt`, `*.cfg`) found `fix_thesis.py` to be the only
top-level module with zero inbound references.

## 2. Generated outputs kept separate from source

| path | size | status |
|---|---|---|
| `outputs/` | ~12 GB | git-ignored; run artifacts, one directory per run |
| `logs/` | ~644 KB | **newly git-ignored in this pass**; generated run logs |
| `sbi-logs/` | ~2.9 MB | git-ignored |
| `__pycache__/` | ~5.3 MB | git-ignored |
| `diagnostics/` | ~220 KB | tracked; small diagnostic artifacts, still referenced |

`logs/` held generated run logs and had been picked up by the pre-existing
safety checkpoint commit. It is now ignored and untracked; **the files remain
on disk**. No other generated artifact is mixed into a source directory. The
only non-source files inside a source directory are
`configs/hm_focused_warm_start_20260916.csv` and
`configs/hm_forecast_windows_warm_start_20260921.csv`, which are warm-start
*inputs* read by the coupled-calibration runner. They are inputs rather than
outputs, so they were left in place.

## 3. Production entry points

### 3.1 Frozen for the production lock-in

| entry point | role |
|---|---|
| `run_robust_nroy_freeze.py` | **Robust-NROY freeze stage.** `validate` / `prepare` / `evaluate` / `merge` / `report` / `resume`. Fails closed while any required decision is unsigned. |
| `configs/robust_nroy_freeze_production.json` | The production run plan. **Deliberately unsigned**; every required decision holds the `UNSIGNED` sentinel. |
| `run_coupled_calibration.py` | Coupled joint calibration search (the established production calibration runner). |
| `run_history_matching.py` | History-matching wave loop and scoring. |
| `run_hm_confirmation.py` | Fresh-seed confirmation. |
| `run_scenarios.py` | Scenario matrix. |
| `run_psa_hta.py` | PSA / HTA. Note decision 4 of the production spec: `msm_versatility` and the stage multipliers are fixed and excluded from ordinary PSA, and the existing PSA artifact "can't be used in text". |
| `run_v3.py` | `SimulationEngineV3` — the simulator itself. 162 inbound references; the single most depended-on module. |

### 3.2 Supporting library modules (imported, not invoked)

`hm_implausibility.py` (acceptance arithmetic, `I_max`, cutoff 3.0),
`hm_target_register.py` (target register), `hm_variance_budget.py` (variance
budget), `calib_scorer.py`, `calib_distance.py`, `counter_based_rng.py`
(counter-based RNG, keyed per process name), `coupled_calibration_search.py`
(parameter space, blocks, canonical hashes, seeds, `ContractError`),
`economics.py`, `transmission_corrected.py`, `demography.py`.

New in this pass: `nroy_contract.py`, `nroy_design.py`, `nroy_execution.py`,
`nroy_graph.py`, `nroy_report.py`.

### 3.3 Verification entry points

```bash
# The robust-NROY verification suite (no engine, no trajectories)
.venv/bin/python -m pytest tests/test_nroy_contract_design.py \
    tests/test_nroy_execution_sharding.py tests/test_nroy_graph_report.py -q

# The M0/M1 engine identity test — MUST be run by the user before M1 launches
.venv/bin/python -m pytest tests/test_nroy_m0_m1_engine_identity.py -q

# The execution boundary: skips anything that builds a real engine
.venv/bin/python -m pytest -p pytest_no_engine_guard -rs tests/
```

## 4. Left in place and listed for review

These are exploratory in character but **not** demonstrably superseded: each
retains inbound references from tests, documentation or output manifests, and
several are the provenance for results cited in the reports. Moving them would
have risked breaking a reference or orphaning a cited result.

### 4.1 Dated one-off analysis runners

| file | inbound refs | note |
|---|---|---|
| `run_art_interpolation_shape_audit.py` | 1 | only its own test; **lowest-confidence keeper** |
| `run_stage_diagnosis_candidate_baseline_review.py` | 1 | provenance for the 2026-09-22 candidate review |
| `run_followup_knot_and_late_dx_20260929.py` | 2 | provenance for the knot / late-diagnosis finding cited in the production spec section 2 |
| `run_recent_mechanism_audit.py` | 2 | |
| `run_temporal_diagnosis_shape_audit.py` | 2 | |
| `run_msm_beta_response_audit.py` | 3 | |
| `run_policy_matrix.py` | 3 | |
| `run_psa_hta_matrix.py` | 3 | |
| `run_focused_confirmation_20260917.py` | 4 | |
| `run_historical_transmission_probe.py` | 4 | |
| `run_post2019_diagnosis_disruption_audit.py` | 4 | |
| `run_diagnosis_triangulation_orchestration.py` | 5 | 188 KB; provenance for the 2,430-trajectory triangulation run |
| `run_post2015_stock_flow_decomposition.py` | 5 | |
| `run_stage_structured_diagnosis_audit.py` | 6 | |
| `run_forecast_readiness_confirmation.py` | 7 | |
| `run_joint_fit_comparison_20260917.py` | 10 | |
| `run_stage_structured_diagnosis_focused_calibration.py` | 8 | 132 KB; defines the `p` coordinate and the harness identity smoke check |

### 4.2 `tools/` (79 scripts)

Left entirely in place. These are the `a*`/`audit_*`/`analyze_*`/`build_*`
scripts behind the numbered audits (A-12, A-16, A-17, A-25, A-28, A-29, C-4 and
the abstract evidence package). Several are cited by name in the reports and by
`tools/a12_call_fingerprints.json`, and the production spec explicitly requires
retaining A-28's regression gates. A per-script triage is a separate,
larger job and is **not** attempted here.

### 4.3 Untracked task documentation at the repository root

34 root-level `*.md` files, including the `TASK_BRIEF_*`, `PHASE2_*`,
`HISTORY_MATCHING_BUILD_SPEC.md`, `MODEL_LIMITATIONS.md` and
`COUPLED_SEARCH_RUNBOOK.md` set. All left in place. These are active working
documents, and the production spec's hygiene pass is about executable material
and generated artifacts rather than notes.

Note one documented inconsistency, already recorded in `hm_implausibility.py`:
`HISTORY_MATCHING_BUILD_SPEC.md` section 2.3 states the three-sigma coverage
bound as "at least 93%". The correct unimodal Vysochanskii–Petunin bound is
95.06% and the distribution-free Chebyshev bound is 88.9%. The code is right;
the build spec's figure is wrong and should be corrected when that document is
next revised.

## 5. Pre-existing test failures (not caused by this pass)

The full suite was run before and after. This pass modified **zero** existing
tracked source files, so none of the following is attributable to it.

Full suite, under the no-engine guard: **2339 passed, 74 failed, 268 skipped,
5 errors**.

The failures are dominated by two environmental path problems, not logic:

1. **Missing root-level `1980_test.json`** — numerous tests resolve
   `ROOT / "1980_test.json"`, but the file lives at `configs/1980_test.json`.
   This accounts for most of `test_orientation_seed_stage_distribution.py`
   (22) and `test_lhs_calibration.py` (13).
2. **Missing `Validation /` data directory** — note the trailing space in the
   expected directory name. Accounts for the
   `test_validation_data_loader.py` errors and some LHS failures.

One substantive pre-existing failure worth flagging:

- `tests/test_a12_deterministic_allocation_audit.py::test_syntax_candidate_scan_is_nonempty_and_scoped_to_reviewed_files`
  fails with `untriaged rounding syntax functions: [('run_v3.py', '_step_stage_structured_diagnosis')]`.
  The stage-structured diagnosis step (the M1 mechanism) performs allocation
  rounding and has not been added to the A-12 triage list. This is a real gap
  in the A-12 audit coverage of the new M1 code path and should be triaged
  before M1 is used for production results.

## 6. Checks run after the moves

- Reference scan for `fix_thesis` and `__delete_test` across all tracked file
  types: **no remaining references outside the archive**.
- `git check-ignore` confirms `logs/` is ignored and its files remain on disk.
- Robust-NROY verification suite: **181 passed**.
- Full suite: unchanged relative to the pre-move run (same 74 failures).
