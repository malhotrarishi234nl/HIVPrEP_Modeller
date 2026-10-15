# Coupled calibration search — runbook

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16). Do not use this runbook to
> start a new run.** It remains at its original path because frozen provenance
> records reference it. Start at `documentation/INDEX.md`.

Implementation of `CLAUDE_COUPLED_SEARCH_HANDOFF_2026-09-18.md`.

**Status, 18 September 2026.** The harness exists and is tested. The preparation is frozen and
the six-trajectory paired smoke check has passed. **The 2,560-trajectory discovery search has NOT
been launched**, as the handoff instructs. Everything below is either already done or is a command
for Rishi to run.

| Stage | State | Directory |
|---|---|---|
| Validation (`--validate-only`) | **run, passed**, 0 simulations | — (writes nothing) |
| Preparation (`--prepare`) | **run**, 0 simulations | `outputs/calibration/coupled_20260918_prepared` |
| Smoke (`--smoke`) | **run, SMOKE_PASSED**, 6 trajectories | `outputs/calibration/coupled_20260918_smoke` |
| Synthetic benchmark | **run** (no simulator) | `outputs/calibration/coupled_20260918_synthetic` |
| Discovery search (`--run-search`) | **not launched** | `outputs/calibration/coupled_20260918_search` |
| Confirmation (`--confirm`) | **not launched** | `outputs/calibration/coupled_20260918_confirmation` |
| Review (`tools/review_coupled_calibration.py`) | code tested end-to-end on a surrogate rehearsal | `outputs/calibration/coupled_20260918_review` |

## 1. What this search is, and what it is not

It is a bounded, multistart adaptive differential search of the **existing** 33-parameter model
against **all 15** focused targets simultaneously, under the existing observation and uncertainty
contracts. Every evaluation runs the continuous 1975–2025 history with the production
`SimulationEngineV3` and the production end-year convention.

It is **not** an emulator, gradient inference, a posterior sampler, a proof of efficient global
optimisation, or exhaustive coverage of 33 dimensions. The numerical settings (archive size, batch
counts, `F` values, pool composition) are **declared computational choices**, not empirical
parameter estimates and not claims of optimality.

No bound was widened, no mechanism added, no acceptance threshold relaxed and no simulator,
extraction, acceptance or input file was modified. New orchestration lives only in the new files
listed in section 8.

## 2. Search ranking versus acceptance — the one thing not to conflate

**Search statistic (ranking only).** For each target `i`, with replicate mean `mu_i`, observation
`z_i` and the **frozen non-process variance** `B_i`:

```
r_i = (mu_i - z_i) / sqrt(B_i)          signed; positive means overprediction
R_G = sqrt(mean(r_i^2)) within group G  four groups (3 / 4 / 2 / 6 targets)
J   = max_G R_G                         ties by mean_G R_G, then candidate id
```

`B_i` is read live from the register and built through the repository's own budget assembler:
observation term + `V_code` + the target-relative 0.1 discrepancy (+ any extra budget term). It is
**not** assumed to be zero — two of the fifteen targets carry a non-zero observation variance
(`shm_new_diagnoses_msm@2019` = 4, `@2024` = 1369), and the harness refuses to run if any `B_i` is
non-positive or non-finite. `B_i` deliberately omits the candidate's own process variance, so a
noisier candidate **cannot** improve its search rank.

Equal treatment of the four groups is a declared search heuristic. It is not a likelihood, not a
weight and not part of acceptance.

**Acceptance (unchanged).** The existing 15-target primary maximum-implausibility rule, with
predictive process variance `s_i^2 (1 + 1/K)`, target-relative discrepancy 0.1, cutoff 3 and the
repository's `<=` boundary convention. The 0.2 discrepancy is reported as sensitivity only and
never promotes a candidate. Every candidate carries four distinct fields: search rank, discovery
primary pass, fresh-confirmation primary pass, sensitivity pass.

The harness cross-checks its own acceptance arithmetic against `run_history_matching._score_matrix`
in `predictive_local` mode on every run and **refuses to finish** if the two disagree
(`repository_acceptance_crosscheck.csv`; agreement to < 1e-9 is required).

## 3. Budget

| Stage | Maximum full trajectories |
|---|---:|
| Three paired harness-versus-production smoke checks | 6 |
| 128 initial + 8×48 adaptive vectors, K=5 | 2,560 |
| At most 10 fresh confirmations, K=20 | 200 |
| **Total scientific/smoke maximum** | **2,766** |

Launch *attempts* consume budget, not successes: a candidate whose result is lost to a crash stays
spent and is never re-run on resume. Unit and full-suite test execution is separate engineering
work and is **not** capped by this table (see section 7).

Seeds: discovery 197000–197004 (K=5), fresh confirmation 197100–197119 (K=20), smoke 197200–197202,
proposal RNG 197300 in its own generator. The preflight scanned 294 manifests under `outputs/` plus
the declared foreign ranges and found **no collision**. A collision stops the run and reports; the
runner never silently picks another range.

## 4. Measured timing (an estimate, from a small sample)

The smoke check measured **12.58 s per full 1975–2025 trajectory** (6 trajectories, 3 pairs).

| Workers | Estimated discovery wall time | Estimated throughput |
|---:|---:|---:|
| 4 (default) | **≈ 2.24 h** | ≈ 1,145 trajectories/h |
| 8 (maximum) | ≈ 1.12 h | ≈ 2,290 trajectories/h |

These are estimates from six runs on one machine, not a guarantee. The 6-hour wall-time guard per
search invocation is comfortable at either worker count. On reaching the guard the runner stops
dispatching, finishes bounded in-flight work and marks `PARTIAL_TIME_LIMIT`; elapsed time may
therefore exceed the cutoff. A partial run writes no shortlist and no final report, and **cannot be
reported as completed negative evidence**.

## 5. Commands

Run from the repository root. Each stage writes its own new directory; an ordinary mode refuses a
non-empty output directory with a short message rather than a traceback. If any execution directory
already exists, choose a new prefix consistently across the remaining commands.

### 5.1 Validate (already run; zero simulations, writes nothing)

```bash
.venv/bin/python run_coupled_calibration.py --validate-only --plan configs/coupled_search_plan_20260918.json
```

### 5.2 Prepare (already run; zero simulations)

```bash
.venv/bin/python run_coupled_calibration.py --prepare --plan configs/coupled_search_plan_20260918.json --output-dir outputs/calibration/coupled_20260918_prepared
```

### 5.3 Smoke (already run; 6 trajectories, `SMOKE_PASSED`)

```bash
.venv/bin/python run_coupled_calibration.py --smoke --prepared outputs/calibration/coupled_20260918_prepared --output-dir outputs/calibration/coupled_20260918_smoke --workers 4
```

### 5.4 Discovery search — **this is the expensive one, not yet launched**

```bash
.venv/bin/python run_coupled_calibration.py --run-search --prepared outputs/calibration/coupled_20260918_prepared --smoke-source outputs/calibration/coupled_20260918_smoke --output-dir outputs/calibration/coupled_20260918_search --workers 4
```

2,560 trajectories, about 2.2 h at 4 workers. Its launch gates were verified without spending
budget: the preparation verifies against the current inputs and code, the smoke artifact verifies
against that same preparation and code, and the output directory is free.

If it is interrupted, resume the *same* directory — never re-run the earlier stages:

```bash
.venv/bin/python run_coupled_calibration.py --resume --output-dir outputs/calibration/coupled_20260918_search --workers 4
```

Resume re-verifies the input, plan and code hashes, restores the proposal RNG state, replays the
journals, and refuses outright if the directory is already `SEARCH_COMPLETE`.

### 5.5 Confirmation — only after a `SEARCH_COMPLETE` search

```bash
.venv/bin/python run_coupled_calibration.py --confirm --source outputs/calibration/coupled_20260918_search --output-dir outputs/calibration/coupled_20260918_confirmation --workers 4
```

Up to 10 shortlisted vectors × 20 fresh seeds = 200 trajectories, about 10 min at 4 workers. The
shortlist is frozen by the *search*, before any fresh simulation, and confirmation may not change
it. Confirmation writes the single decision report, `DECISION_REPORT.md`.

### 5.6 Independent review

```bash
.venv/bin/python tools/review_coupled_calibration.py --source outputs/calibration/coupled_20260918_search --confirmation outputs/calibration/coupled_20260918_confirmation --output-dir outputs/calibration/coupled_20260918_review
```

The reviewer imports no part of the harness and calls none of its report functions. It recomputes
means, ddof=1 variances, signed residuals, every `R_G`, `J`, every target implausibility (primary
and sensitivity) and the whole shortlist from the raw per-seed records, and verifies artifact
hashes, complete seed matrices and budget counts. Any disagreement is an error.

## 6. What each stage writes

`--prepare`: `manifest.json` (status `PREPARED`, input/module hashes, proposal-RNG state after
initialisation), `frozen_plan.json`, `initial_archive.json` (128 vectors with provenance and every
rejection reason), `frozen_targets.csv`, `parameter_bounds.csv` (generated from the authoritative
JSON), `effective_contact_products_anchor.csv`, `msm_diagnosis_probabilities_anchor.csv`,
`artifacts.json`.

`--run-search`: `journal/{dispatch,results,proposals}.jsonl` (append-only, fsynced), one atomically
written `raw/candidate_<id>.json` per candidate holding every replicate's full annual series and the
start-of-year 1990/2000/2010/2025 stock summaries by orientation × HIV stage × diagnosis × ART with
axis labels and denominators, `checkpoint.json` (RNG state, budget, lost-in-flight),
`archive_summary.csv`, `signed_search_residuals.csv`, `per_target_implausibility.csv`,
`repository_acceptance_crosscheck.csv`, `proposal_provenance.json` (parent pools, F, blocks,
rejection and fallback counts), and — only on `SEARCH_COMPLETE` — `shortlist.json` and
`SEARCH_REPORT.md`.

`--confirm`: `frozen_shortlist.json`, per-candidate raw records on the fresh seeds,
`states/candidate_<id>_seed<seed>.npz` with the full 2025 adult and childhood tensors and an
explicit `restart_safe: false` (these are diagnostic states, not restart checkpoints),
`confirmation_summary.csv`, `paired_discovery_confirmation.csv`,
`repository_acceptance_crosscheck.csv`, `cross_validation_per_target.csv` where comparators resolve,
and `DECISION_REPORT.md`.

Manifest statuses: `PREPARED`, `SMOKE_PASSED`, `SEARCH_COMPLETE`, `CONFIRMATION_COMPLETE`,
`PARTIAL_TIME_LIMIT`, `FAILED`. Only a completed stage gets its final report.

## 7. Engineering gates — what was run and what it showed

**Paired smoke (the declared 6-trajectory budget).** Three pairs, one production
`SimulationEngineV3` run and one observed harness run each, at identical parameters and seeds
(197200–197202), over warm vectors 0, 1 and 2 (source designs 620, 621, 548). For every pair: all
summary columns identical, all extracted annual series identical, and both the final adult and the
childhood state tensors identical. The observer captured 1990/2000/2010/2025 in every run,
`engine.deterministic` was `False` and the enabled MSM relative-diagnosis path was asserted in every
worker. This is what establishes that the observer is passive; it says nothing about model fit.

*Simulation accounting, stated plainly.* Twenty full or partial trajectories were run during
implementation. **Six are the smoke artifact of record.** The other fourteen were engineering: a
two-run paired observer probe on a truncated 1975–1980 horizon, and two earlier six-run smokes that
were discarded when the code changed. Each change (adding exact parameter-vector identities and
injected-config hashes; then carrying those identities into the frozen initial archive and
tightening the confirmation cross-check) re-freezes the module hashes, which by design invalidates
any earlier preparation and smoke — `--run-search` refuses a smoke artifact produced by different
code, so the pair had to be redone. Nothing else touched the simulator. The 2,766-trajectory table
caps scheduled scientific work; it was never claimed to cap engineering runs, and these fourteen are
recorded here rather than folded into it.

**Synthetic benchmark** (`outputs/calibration/coupled_20260918_synthetic/synthetic_benchmark.json`,
no simulator involved). Fixture: a narrow compensating valley on four coordinates, one from each
parameter block — `seed_prevalence`, `diagnosis_1990`, `orient_msm`, `art_init_2000` — coupled as
`v1 = dA+dB`, `v2 = dB−dC`, `v3 = dC+dD`, `v4 = dD−dA`, so every single-coordinate perturbation
helps one group and hurts another and the only joint solution is the known interior point. Five
**predeclared** proposal seeds (197300–197304), equal budget of 512 evaluations each.

| Proposal seed | Adaptive search best J | Equal-budget LHS best J |
|---:|---:|---:|
| 197300 | 0.963 | 1.930 |
| 197301 | 1.045 | 1.320 |
| 197302 | 0.964 | 0.970 |
| 197303 | 0.494 | 1.659 |
| 197304 | 0.796 | 1.069 |

Success (J ≤ 1.0): **4/5 for the adaptive search, 1/5 for equal-budget LHS**; median best J 0.963
versus 1.320; the search was better on 5/5 seeds. No seed was cherry-picked and no design revision
was needed. This is a synthetic optimisation comparison on a fixture with a known solution. **It
says nothing about whether the HIV model can fit its targets.**

A second fixture puts two basins either side of a discontinuity, with every warm-start vector in the
worse one; the test asserts the search leaves the warm-start coordinate range, samples the far
region repeatedly and retains the far basin. The 12 global full-domain draws per batch are what make
that possible.

**Full test suite.** Baseline established before the change and re-run after. See section 7.1.

### 7.1 Full-suite comparison

The full suite was run before any code was written and again after the harness was complete, and
the failure and error names were compared exactly.

| | Before | After |
|---|---:|---:|
| passed | 2,137 | 2,185 |
| failed | 46 | 46 |
| errors | 5 | 5 |
| skipped | 30 | 31 |

**The set of failing and erroring test names is byte-identical between the two runs: 51 before, 51
after, zero new, zero fixed.** No new relevant failure was introduced, so nothing blocks launch. The
48 extra passes are this change's own new tests. The one extra skip is
`tests/test_a25_silent_default_guards.py::test_no_config_names_an_age_bin_that_does_not_exist`,
which is parametrised over `configs/*.json` and now also skips the new plan file (it declares no age
bins).

Those 51 pre-existing failures are recorded, not inherited silently: they predate this work and
include the `test_lhs_calibration`, `test_orientation_seed_stage_distribution`,
`test_non_msm_diagnosis_pipeline_audit`, `test_validation_data_loader` and
`test_snpe_calibration` groups. Establishing this baseline rather than trusting earlier records is
what makes "no new relevant failure" a meaningful statement.

**Known exception, disclosed.** `tests/test_msm_relative_diagnosis_v13_10.py::TestStepDiagnosisBehavior::test_enabled_with_multiplier_one_matches_baseline`
still **fails**, with its assertion unchanged. The launch gate of this runner tolerates exactly that
one failure and nothing else; see `tests/KNOWN_TEST_EXCEPTIONS.md` E-2026-09-17-01 and
E-2026-09-18-01. The suite is **not** fully passing, and every smoke and confirmation manifest
records `suite_fully_passing: false` alongside the disclosure. The stochastic enabled MSM path this
search actually uses is established by the paired smoke comparison, not by that test.

## 8. Files

| File | Role |
|---|---|
| `coupled_calibration_search.py` | pure parameter/proposal/archive/objective/budget logic; builds no engine, writes no file |
| `run_coupled_calibration.py` | validated production runner, observer, checkpoint orchestration, CLI |
| `configs/coupled_search_plan_20260918.json` | the design in machine-readable form; verified against the module constants on every run |
| `tests/test_coupled_calibration_search.py` | scientific-contract and proposal tests |
| `tools/review_coupled_calibration.py` | independent read-only recomputation from raw records |
| `COUPLED_SEARCH_RUNBOOK.md` | this file |

## 9. Limitations — read before quoting any result

- **A low `J` is not acceptance.** `J` is a search ranking statistic. Acceptance is the unchanged
  15-target primary maximum rule.
- **The preparation being frozen and the smoke passing say nothing about the model.** They say the
  design is fixed and the observed harness reproduces production exactly.
- The 2026-09-16 focused experiment retained 2 of 622 vectors on diagnoses alone. That motivated
  this search; it establishes neither feasibility nor infeasibility.
- A primary pass with large mean residuals or high replicate variance is a **reported mathematical
  result whose variance dependence must be stated**, not a quietly different acceptance threshold.
- The ten shortlisted candidates are not a representative uncertainty ensemble and not a ready HTA
  baseline.
- `V_obs` is underivable (0) for thirteen of the fifteen targets; the register records this.
- The AIDS observation proxy remains an **unvalidated assumption**. A good numerical fit cannot
  validate its definition.
- K=5 gives `s(x)` four degrees of freedom in discovery; the chi-square stability diagnostic is
  reported, not corrected.
- This is a bounded first joint-search attempt. A negative result does **not** establish global
  impossibility, and no result here licenses widening a bound, adding a mechanism or launching a
  second search automatically. A later comparator must name the remaining mismatch and distinguish
  observation ambiguity, constraint sensitivity, temporal transmission flexibility and
  initialisation changes.
