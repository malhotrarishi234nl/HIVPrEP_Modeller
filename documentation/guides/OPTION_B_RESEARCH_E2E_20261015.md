# Option-B bounded research E2E calibration

This route exercises the real coupled-calibration implementation without
claiming production authorization. Every artifact is permanently labelled:

```text
run_class = RESEARCH_E2E_NOT_PRODUCTION_ELIGIBLE
production_eligible = false
```

The route keeps the reviewed 33-parameter space, frozen 13-target order,
target-relative discrepancy rules, maximum-implausibility acceptance rule,
five common discovery seeds, twenty fresh confirmation seeds, three paired
smoke seeds, hash-pinned inputs, append-only journals, artifact indexes and
resume integrity. It does not manufacture a production seed audit, stage
authorization, benchmark, pricing approval or production claim.

## One-hour profile

The recommended local profile is a full 256-vector initial archive plus two
64-vector adaptive batches: 384 vectors and 1,920 discovery trajectories at
five replicates per vector. Historical timing of 12.58 seconds per trajectory
suggests about 50 minutes at eight workers, before a short six-trajectory smoke
and at most 200 fresh confirmation trajectories. Timing is an estimate, not a
guarantee. The search stops dispatch at 3,600 seconds and finishes only bounded
in-flight work.

If the wall cap is reached, status is `PARTIAL_TIME_LIMIT`, no shortlist is
frozen, and the confirmation command is not run. Resume the same search
directory with the exact same `--workers`, `--n-batches`, and wall-cap values.

## Required mounted inputs

The command must run from a full working repository, not a source-only ZIP.
Before launching, validation checks:

- `outputs/calibration/hm_focused_diagnoses_then_aids_20260916/manifest.json`
  and its indexed source experiment;
- `outputs/calibration/rivm_aids_vintage_audit_20260916/`
  `candidate_newest_vetted_20260916.json` (SHA-256
  `16be90cb791f02e5e69f4d7836ee1f0c0ffa8dff9d57c28d8b494a6923e12f03`);
- `../data/raw/validation/DiseaseData.xlsx` (SHA-256
  `0ff530cea5cb2545b32c6c20ab40de324bfadf7846c2078e59eec6b79eb93404`);
- the exact base config, calibration space, target register, wave plan and warm
  start hashes declared by
  `configs/coupled_search_plan_option_b_1980_research_e2e_20261015.json`.

Missing input or seed collision is a refusal, not a warning.

## Copy-paste launch

Run from the repository root:

```bash
set -euo pipefail

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

PYTHON="$PWD/.venv/bin/python"
PLAN="$PWD/configs/coupled_search_plan_option_b_1980_research_e2e_20261015.json"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$PWD/outputs/calibration/option_b_research_e2e_${RUN_STAMP}"
PREPARED="$RUN_ROOT/prepared"
SMOKE="$RUN_ROOT/smoke"
SEARCH="$RUN_ROOT/search"
CONFIRMATION="$RUN_ROOT/confirmation"

mkdir -p "$RUN_ROOT"

"$PYTHON" run_coupled_calibration.py \
  --validate-only \
  --research-e2e \
  --plan "$PLAN" \
  --workers 8

"$PYTHON" run_coupled_calibration.py \
  --prepare \
  --research-e2e \
  --plan "$PLAN" \
  --output-dir "$PREPARED" \
  --initial-size 256 \
  --workers 8

"$PYTHON" run_coupled_calibration.py \
  --smoke \
  --research-e2e \
  --plan "$PLAN" \
  --prepared "$PREPARED" \
  --output-dir "$SMOKE" \
  --workers 8

"$PYTHON" run_coupled_calibration.py \
  --run-search \
  --research-e2e \
  --plan "$PLAN" \
  --prepared "$PREPARED" \
  --smoke-source "$SMOKE" \
  --output-dir "$SEARCH" \
  --workers 8 \
  --n-batches 2 \
  --wall-time-cap-seconds 3600

"$PYTHON" run_coupled_calibration.py \
  --confirm \
  --research-e2e \
  --plan "$PLAN" \
  --source "$SEARCH" \
  --output-dir "$CONFIRMATION" \
  --workers 8

printf 'Research E2E artifacts: %s\n' "$RUN_ROOT"
```

## Resume after the wall cap

Use the exact `RUN_ROOT` printed by the original invocation:

```bash
"$PYTHON" run_coupled_calibration.py \
  --resume \
  --research-e2e \
  --plan "$PLAN" \
  --output-dir "$SEARCH" \
  --workers 8 \
  --n-batches 2 \
  --wall-time-cap-seconds 3600
```

Resume never resets the trajectory budget and never retries a launch whose
result was lost. Run confirmation only after the resumed search reports
`SEARCH_COMPLETE`.

## Interpretation

`SEARCH_COMPLETE` means the bounded 384-vector research design completed. It
does not mean the full 2,048-vector production design ran, and it does not prove
global feasibility or impossibility. `CONFIRMATION_COMPLETE` means the frozen
shortlist was evaluated on all twenty fresh confirmation seeds. Only candidates
with `confirmation_primary_pass=true` passed the stated 13-target contract;
even those remain research candidates pending academic review and any later
production governance.

PSA is deliberately not chained into this launch. A later step must first bind
one complete selected joint vector into a model config and freeze calibrated
coordinates before running a conditional PSA. The shortlist is not a posterior
and its marginals must not be recombined independently.
