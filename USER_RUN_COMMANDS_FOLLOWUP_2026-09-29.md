# Follow-up runs (2026-09-29): same-θ knot test, late-diagnosis engine, fresh-seed confirmation

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16). Do not launch these commands as
> a current workflow.** They preserve an unlaunched September follow-up design
> at its original provenance path. Start at `documentation/INDEX.md`.

Nothing here has been launched. Total: 1,160 trajectories, about 39 min at 8 workers (16.3 s per trajectory per worker, from the runbook). Allow 45 min.
The runner never modifies frozen tools. Output: `outputs/calibration/followup_knot_and_late_dx_20260929/`.

## 0. Setup
```bash
cd "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model"
mkdir -p logs
```

## 1. Gate (about 2 min). Run this first.
```bash
.venv/bin/python run_followup_knot_and_late_dx_20260929.py validate --workers 8 2>&1 | tee -a logs/followup_20260929.log
.venv/bin/python run_followup_knot_and_late_dx_20260929.py smoke --workers 2 2>&1 | tee -a logs/followup_20260929.log
```
`validate` checks input hashes, the frozen design and that the seeds are unused. `smoke` runs one legacy trajectory and one late-diagnosis trajectory on the real engine (seed 230299) and prints `SMOKE OK`. If either fails, stop and send me the log. The real-engine path for late-diagnosis configs could not be tested in my sandbox, so this is the gate.

## 2. Knot test (80 trajectories, about 3 min)
```bash
caffeinate -dimsu .venv/bin/python run_followup_knot_and_late_dx_20260929.py knot-test --workers 8 2>&1 | tee -a logs/followup_20260929.log
```
Look at the printed result before continuing. It answers: with θ held fixed, does moving the knot from 2010 to 2008 change the 2008 diagnosis level?

## 3. Rest (about 36 min)
```bash
caffeinate -dimsu bash -c '
P=.venv/bin/python; S=run_followup_knot_and_late_dx_20260929.py
$P $S late-dx-discover --workers 8 && $P $S late-dx-select && $P $S late-dx-confirm --workers 8 && $P $S report
' 2>&1 | tee -a logs/followup_20260929.log
```
Or everything in one go: `caffeinate -dimsu .venv/bin/python run_followup_knot_and_late_dx_20260929.py all --workers 8 2>&1 | tee -a logs/followup_20260929.log`

## Resume and status
Each trajectory is saved as its own file. If the laptop sleeps or you Ctrl-C, re-run the same command and finished trajectories are skipped.
```bash
.venv/bin/python run_followup_knot_and_late_dx_20260929.py status
```
The finalist shortlist freezes at `late-dx-select` and is not changed by later runs.

## Send back
`REPORT.md`, `knot_test_summary.csv`, `knot_test_paired_summary.csv`, `late_dx_confirmation_summary.csv`, `late_dx_paired_summary.csv`, the two `fig_*.png`, and `logs/followup_20260929.log`.
