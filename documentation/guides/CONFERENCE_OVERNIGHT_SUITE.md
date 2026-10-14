# Conference overnight suite

This is the operational wrapper for the conference-abstract evidence run. It
does not change the model or convert the calibration shortlist into a posterior.

## Evidence layers

1. Paired 2008-versus-2010 structural calibration: at most 20,892 trajectories.
2. Stochastic policy matrix: 15 CRN seeds, 19 primary scenarios plus the five-
   scenario structural bridge, at most 750 trajectories.
3. Conditional parameter PSA: 1,000 shared Latin-hypercube draws across all 24
   scenario/backbone combinations, 24,000 scenario-draw tasks and at most
   72,000 trajectories.

An optional H4D scaling benchmark adds 1,153 engineering trajectories, bringing
the maximum launched workload to 94,795 trajectories. The benchmark trajectories
are discarded and never pooled with scientific evidence. “Conditional” is
important: calibrated coordinates are held at the selected confirmed point.
The 2008/2010 bridge and any additional confirmed-fit comparisons carry
calibration/structural sensitivity separately.

## Reproducibility contracts

- Calibration uses paired CRN seeds and fresh-seed confirmation.
- Automatic binding refuses any case whose selected candidate does not pass
  fresh confirmation. A provisional non-passer requires an explicit override.
- Policy scenarios share stochastic seeds 42–56.
- PSA scenarios share the same LHS row and deterministic simulation seed for
  each draw index.
- Plans hash model, HTA, PSA, draw, task, and execution-code inputs.
- Policy outputs checkpoint per seed. PSA outputs checkpoint atomically per
  `(scenario, draw)` and can be resumed shard-by-shard.
- Complete finalization refuses missing work by default.

## Prepared entry points

- `outputs/suites/conference_overnight_h4d_20260929_prepared/01_h4d_benchmark_and_calibrations.sh`
- `outputs/suites/conference_overnight_h4d_20260929_prepared/02_run_downstream.sh`
- `outputs/suites/conference_overnight_h4d_20260929_prepared/VM_RUNBOOK.md`

The first script benchmarks the VM and runs the matched calibrations
concurrently. The second binds confirmed candidates, runs and finalizes both
stochastic policy matrices, runs the sharded PSA matrices, and creates paired
2008-minus-2010 long tables. The older non-H4D prepared directory remains an
eight-worker fallback and is not the recommended launcher for this machine.

## Compute profile

The reviewed target is an on-demand Google Compute Engine
`h4d-standard-192`: 192 physical vCPUs with SMT disabled, 720 GiB RAM, and a
250 GiB Hyperdisk Balanced volume. GPU acceleration is not used. The benchmark
measures 32, 64, 96, 128, 160, and 192 total workers. Calibration is capped at
64 workers per case because each adaptive wave has 64 candidates; both cases
run concurrently. PSA uses the benchmark's best whole-machine count, divided
into eight-worker shards. This changes scheduling only, not draws, seeds,
candidate budgets, or acceptance rules.

The observed engineering smoke timings on the development host were about six
to seven seconds for one two-arm conditional PSA task and about 61 seconds for
one complete five-scenario stochastic bridge seed. These are sizing evidence,
not guaranteed cloud runtimes.

## Output shape

The runners produce compact long CSVs, not a worksheet per scenario:

- seed-level policy results and policy summaries;
- draw-level PSA incremental and arm outcomes;
- PSA distribution and convergence summaries;
- paired 2008-minus-2010 policy and PSA tables;
- manifests with hashes, completion states, and source paths.

The formatted professor workbook and selected figures are bound from these
checked tables after the VM results return. This avoids making spreadsheet
formatting part of the expensive simulation job.

`SUITE_STATUS.txt` is written by the downstream launcher. It reports `COMPLETE`
only after policy, PSA, and paired finalization succeed, and reports `FAILED`
with the shell exit code if the launcher stops on an error.
