# User-run commands: diagnosis triangulation orchestration

> **This is the command file for the diagnosis triangulation run (up to 2,430 trajectories).** The file `hiv-prep-model/USER_RUN_COMMANDS.md` in the repository root belongs to the **completed** focused stage-diagnosis calibration (1,780 trajectories). Do not run anything from it for this design.

Design `diagnosis_triangulation_orchestration_20260922`. Every command below runs from the repository root, `hiv-prep-model/`, and each one is a single copy-pasteable line.

Each line:

- `cd`s into the repository root;
- sets `pipefail`;
- creates the log directory before `tee`;
- calls `.venv/bin/python` explicitly;
- appends to its log (`tee -a`), so an interrupted run keeps its log.

`caffeinate -dimsu` is your choice. To run without it, delete `caffeinate -dimsu ` from the line.

- **Output root:** `outputs/calibration/diagnosis_triangulation_orchestration_20260922/`.
- **Startup print:** every simulation command prints the resolved output root and the frozen-plan SHA-256 before it dispatches anything.
- **Overwrite protection:** no command overwrites a completed unit or an existing file with different content. A COMPLETE unit is a no-op.
- **Interrupts:** Ctrl-C keeps every journaled trajectory. Continue with the unit's `--resume` form or the `resume` command.

## Budget and time

Seconds per trajectory is taken as 16.3 s at 8 workers, from the focused run. The benchmark then measures it.

| Unit | Trajectories | Approx. minutes at 8 workers |
|---|---:|---:|
| benchmark | 150 | 5 |
| discovery shards 0, 1 | 255 each | 9 each |
| discovery shards 2–7 | 235 each | 8 each |
| confirmation shards 0, 1 | ≤180 each | ≤6 each |
| **total** | **≤2,430** | **≈83** |

- **Clock:** the union of the wall-clock intervals of all dispatching invocations. Pauses between commands (for example the shortlist review) do not count.
- **Stops:** new work stops at 110 minutes on this clock; in-flight work is abandoned at 115 minutes.
- **Confirmation reserve:** discovery also stops early enough to leave the estimated confirmation time (×1.10). A discovery shard that cannot fit is marked `NOT_STARTED_TIME_RESERVE`.
- **Partial runs:** a partial run is labelled `PARTIAL_TIME_LIMIT` and is never reported as a completed design.

## 1. Evidence audit and plan validation (static; no simulation)

The implementing agent has already run both commands. Re-running them is an idempotent no-op that confirms nothing changed.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py audit-evidence 2>&1 | tee -a logs/diagnosis_triangulation_20260922/audit_evidence.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py validate-plan 2>&1 | tee -a logs/diagnosis_triangulation_20260922/validate_plan.log
```

## 1b. Optional smoke test (2 trajectories, about 40 s)

This runs M0_1326 and B_1788_v048 once each, on seed 202500, through the exact worker path. It writes to a separate directory outside the provenance chain and does not count toward the budget. It confirms that every metric, external check and the ledger identity can be computed from a real trajectory. Recommended before the benchmark, because a code-path failure inside a unit marks that unit FAILED.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py smoke 2>&1 | tee -a logs/diagnosis_triangulation_20260922/smoke.log
```

## 2. Benchmark: 150 trajectories, about 5 minutes

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py benchmark --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/benchmark.log
```

If interrupted, append `--resume` to the same line.

## 3. Discovery shards: run separately, in order 0 to 7

Discovery requires a COMPLETE benchmark. **Run the shards one at a time, in order 0 to 7.** This keeps a usable prefix if time runs out, and it is no slower on 8 cores. Concurrent shards are safe but not needed: manifest updates hold a file lock, a shard that is already running refuses a second start, and the clock does not double count overlapping intervals. If a shard is interrupted, append `--resume`.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 0 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_00.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 1 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_01.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 2 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_02.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 3 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_03.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 4 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_04.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 5 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_05.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 6 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_06.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py discover --shard-index 7 --shard-count 8 --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/discovery_07.log
```

## 4. Discovery merge and shortlist freeze (no simulation)

The merge refuses duplicate (tuple, seed) pairs, trajectories from another plan or design, failures, and incomplete discovery. If time stopped discovery early, add `--allow-partial-discovery`. That merges only the completed prefix (at least shards 0 and 1) and labels it `PARTIAL_TIME_LIMIT`.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py merge-discovery 2>&1 | tee -a logs/diagnosis_triangulation_20260922/merge_discovery.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py freeze-shortlist 2>&1 | tee -a logs/diagnosis_triangulation_20260922/freeze_shortlist.log
```

**Review gate.** Read `frozen_shortlist.json`: at most 12 tuples, two of them M0 comparators. Copy the printed `shortlist_sha256`. Passing it to the confirmation commands records that you reviewed the shortlist.

## 5. Confirmation shards: ≤180 trajectories each, about 6 minutes each

Replace `<SHA>` with the frozen shortlist hash.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py confirm --shard-index 0 --shard-count 2 --workers 8 --shortlist-sha256 <SHA> 2>&1 | tee -a logs/diagnosis_triangulation_20260922/confirmation_00.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py confirm --shard-index 1 --shard-count 2 --workers 8 --shortlist-sha256 <SHA> 2>&1 | tee -a logs/diagnosis_triangulation_20260922/confirmation_01.log
```

## 6. Confirmation merge and final report (no simulation)

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py merge-confirmation 2>&1 | tee -a logs/diagnosis_triangulation_20260922/merge_confirmation.log
```

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && .venv/bin/python run_diagnosis_triangulation_orchestration.py report 2>&1 | tee -a logs/diagnosis_triangulation_20260922/report.log
```

The report can also be run at any earlier point. It then labels the design incomplete and gives no final classification.

## 7. Status

The status command reports completed, missing, failed, duplicate and mismatched (tuple, seed) pairs per unit, plus the clock. It also rewrites `STATUS.md`.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && .venv/bin/python run_diagnosis_triangulation_orchestration.py status
```

## 8. Safe resume

`resume` continues the next incomplete dispatch unit, in the order benchmark → discovery 0 to 7 → confirmation 0 to 1. It never crosses the merge, freeze or review gates, and it never re-runs a COMPLETE unit. Confirmation units need `--shortlist-sha256 <SHA>`.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py resume --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/resume.log
```

A unit containing a failed trajectory is marked FAILED and is never retried silently. Inspect its `failures.csv` first.

## 9. Optional sequential run-all (stops at the shortlist gate)

The first invocation runs benchmark → discovery 0 to 7 → merge → freeze, then **stops** at the review gate. If discovery stopped on time, it merges the completed prefix as PARTIAL.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py run-all --workers 8 2>&1 | tee -a logs/diagnosis_triangulation_20260922/run_all.log
```

After reviewing the shortlist, run it again with the approval flag. Completed units are skipped, and the run continues through confirmation, merge and report.

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && set -o pipefail && mkdir -p logs/diagnosis_triangulation_20260922 && caffeinate -dimsu .venv/bin/python run_diagnosis_triangulation_orchestration.py run-all --workers 8 --approve-shortlist-sha256 <SHA> 2>&1 | tee -a logs/diagnosis_triangulation_20260922/run_all.log
```

## Tests

The new suite constructs no engine. It runs inside the execution boundary:

```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model" && .venv/bin/python -m pytest -p pytest_no_engine_guard -q tests/test_diagnosis_triangulation_orchestration.py
```
