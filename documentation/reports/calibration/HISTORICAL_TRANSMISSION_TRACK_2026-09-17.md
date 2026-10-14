# Historical transmission and 1990 initialisation — test track

> Archived under `documentation/reports/calibration/`; original filename retained.

**Update:** after preparation, the user authorised continuation. The fixed diagnostic
completed 50/50 simulations. See `HISTORICAL_TRANSMISSION_RESULT_2026-09-17.md`.
The preparation instructions below are retained as the declared pre-run protocol.

## What is ready

An isolated diagnostic adapter, a fixed experiment runner, 20 automated guards,
a non-neutral writer check, and a 1990 input-readiness contract. The production
engine, calibration spaces, target register and accepted observation proxy are
unchanged. This is preparation for a scientific model comparison, not that
comparison itself and not a calibrated baseline.

### Step 1 — completed engineering checks

- Unit/contract tests cover interpolation, endpoint rules, malformed/out-of-range
  schedules, unchanged non-MSM contacts, symmetric MSM contacts, no input mutation,
  passive capture before annual transitions, no simulation from validation,
  no output overwriting, refusal to turn annual diagnoses into stocks, and
  predictive-local scoring with correctly paired seeds.
- Neutral schedule: production vs experimental adapter, full 1975–2025 trajectories
  for three fixed seeds (six simulations). Exact identity is required for every
  summary column and final adult and childhood arrays.
- Non-neutral check: one full trajectory for writer/target/state extraction.
  This is an engineering test, not evidence that the alternative fits.

### Step 2 — prepared, not launched

Run a fixed **50-simulation local diagnostic**: two previously used reference
configurations (620 and 548), five profiles, five shared fresh diagnostic seeds
196000–196004. The command first repeats the six neutral regression simulations,
so the total command budget is **56 simulations**. Maximum eight workers; a
30-minute projected runtime guard stops further submissions (already running
jobs finish). The time guard is not a hard kill timer.

| Profile | Intended contrast |
|---|---|
| constant | Existing historical contact structure |
| early_up | Higher effective MSM contact supply through 1990; back to reference in 2000 |
| middle_down | Reference through 1990; lower in 2000; back to reference in 2010 |
| recent_down | Reference through 2000; lower from 2010 onward |
| combined | Early higher, middle and recent lower |

Levels move **25% of the distance from the reference MSM orientation multiplier
toward its existing lower or upper calibration bound**. No level exceeds the
existing bounds. This fraction and the 1975/1990/2000/2010/2025 windows are
**declared diagnostic design choices**, not estimates of Dutch behavioural change,
evidence-based priors, or optimised knots. At a bound a direction can be neutral;
inspect the frozen levels rather than assuming equally strong interventions.
For these two actual anchors, the early increase is approximately 76–79%,
whereas downward perturbations are only about 4–5%, because both anchors lie
near the existing lower bound. This asymmetry limits what a negative middle/recent
result can establish; it is not evidence against larger, separately justified
temporal changes.
All other parameters remain fixed at each reference configuration. These two
configurations are diagnostic anchors, not representative samples of uncertainty.

The adapter scales only the already constructed symmetric MSM-MSM contact block.
It draws no random numbers, retains endogenous infection generation and leaves
all other contact blocks unchanged. It does not inject infections, change testing,
add presentation hazards or alter the production engine. Its multiplier is an
effective contact-supply perturbation; it is not a measured per-act biological risk.
The final level is held beyond 2025 if evaluated, but no future-HTA continuation
assumption is validated by this experiment.

### Step 3 — one results decision, then stop

The runner preserves all 15 primary anchors, 10% primary discrepancy, 20%
sensitivity and predictive-local variance `s²(1+1/K)`. It reports means, paired
mean changes and SEs, and the binding target. With five replicates, variances and
SEs are noisy. No output is an accepted ensemble, posterior or fitted-model result.

Read the four questions together:

1. Does early AIDS rise without a worse middle-period diagnosis excess?
2. Does recent AIDS improve, rather than becoming the new failure?
3. Do changes operate through the intended calendar windows and remain plausible
   in the complete saved trajectory, not just selected targets?
4. What happens to the 2025 HIV-stage/diagnosis/ART population, including its age
   and activity distribution? These are model outputs, not validated observed stocks.

A useful pattern supports writing a **reduced-parameter fitting specification**
with a fairly fitted current-model comparator. A negative result rejects only
these perturbations at these two anchors. It does not prove that all temporal
schedules fail. Neither result authorises an automatic parameter sweep, changed
threshold, new presentation mechanism or switch to a 1990 start. The local
experiment is intentionally not a substitute for the fair model comparison
recommended in the decision memo.

Before a genuine fit, specify the effective transmission scale, which redundant
contact parameters are fixed/profiled, externally constrained nuisance parameters,
allowed temporal shape and future continuation rule. Do not estimate a free
baseline scale and unconstrained relative levels simultaneously. This package
does not invent empirical bounds for that scientific specification.

## Commands

Run from `hiv-prep-model/`, using its virtual environment. Every command that
writes refuses an occupied output directory; select a new directory to repeat it.

```bash
# Unit and preparation checks; no epidemic simulations.
.venv/bin/python -m pytest -q tests/test_historical_transmission_probe.py
.venv/bin/python run_historical_transmission_probe.py --validate-only

# Freeze a reviewable plan; no simulations.
.venv/bin/python run_historical_transmission_probe.py --prepare \
  --output-dir outputs/calibration/historical_transmission_prepared_20260917

# Engineering regression check only; six simulations.
.venv/bin/python run_historical_transmission_probe.py --smoke \
  --output-dir outputs/calibration/historical_transmission_smoke_repeat

# Non-neutral pipeline check only; one simulation.
.venv/bin/python tools/check_historical_transmission_writer.py --run \
  --output-dir outputs/calibration/historical_transmission_writer_repeat

# The prepared local scientific diagnostic; NOT launched during preparation.
.venv/bin/python run_historical_transmission_probe.py --run-diagnostic --workers 4 \
  --output-dir outputs/calibration/historical_transmission_diagnostic_20260917
```

No arguments, or `--validate-only`, starts zero simulations and writes nothing.
`--prepare` freezes the source hashes and design. `--run-diagnostic` reads and
validates live inputs again, freezes the exact design it executes, and checks
fresh diagnostic seeds against recorded manifests. `--smoke` intentionally
reuses fixed regression seeds: those results are equivalence tests, not fresh
scientific evidence. The diagnostic's internal neutral gate uses those same
fixed regression seeds.

Existing diagnosis tests are run before simulation. Their one narrowly recorded
deterministic multiplier-neutrality failure is preserved in the gate report;
this package stays stochastic on the enabled MSM diagnosis path. This is not a
claim that the repository's full suite passes. See `VALIDATION.md` in the prepared
artifact directory for the before/after full-suite comparison.

## Output files and interpretation

- `design.json`: exact parameters, absolute effective multiplier levels, seeds,
  target means/measurement variances, source hashes, git revision and dirty state.
- `raw_seed_outputs.partial.csv`, `checkpoint.json`: retained raw trajectories and
  progress, including errors. The CSV is the saved raw output even on completion.
- `RESULTS.json` and `RESULTS.md`: only generated after complete successful runs;
  no incomplete design is scored as a successful experiment.
- `states/<cell>_seed<seed>.npz`: joint seven-axis adult population and childhood
  arrays immediately before 1990/2025 transitions; load with `numpy.load`.
- Corresponding state JSON: axis labels, stock summaries, calendar contact trace
  and an explicit `restart_safe: false` marker.
- `manifest.json`: artifact hashes and final experiment status.

The writer check and neutral smoke have their own small result/manifest files.
The runner turns model output writing off to avoid side effects outside its
selected artifact directory.

## The 1990 branch is prepared separately

`configs/initialisation_1990_readiness.json` deliberately contains missing inputs,
not guessed values. Its checker requires definitions and provenance for:

- Adult population by age, orientation and activity.
- HIV stock by disease stage, diagnosis and ART.
- Childhood population.
- Residence-phase allocation within age groups.
- Calendar indexing and prevention of duplicate epidemic seeding.
- Uncertainty and source/assumption provenance.

Annual diagnoses (including 318 MSM AIDS diagnoses in 1990) are flows and cannot
be substituted for these stocks. Captured burn-in states can eventually supply
**model-derived sensitivity initialisations**, not independent empirical evidence.

The existing `StateSnapshot` stores aggregate adult/childhood arrays and legacy
RNG state. On restore, `run_v3.py` reinitialises adult and childhood residence
phases. Counter-based RNG keys also depend on timestep indexing. Therefore,
merely changing the start year and restoring the existing snapshot must not be
claimed equivalent to continuing the original simulation. The new state captures
are explicitly **not complete restart checkpoints**. A future 1990 implementation
needs a roundtrip test of all dynamic state and a deliberate choice between
continuation and independently initialised population history.

Filling the metadata contract only marks it ready for numerical design; the
checker never returns `simulation_ready: true`. No 1990 simulation is implemented
or launched by this package.
