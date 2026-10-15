# Model operator guide: explicit M0/M1 runs

## Audience and status

This is the parameter-focused route for collaborators who run the model but do
not edit Python. It is deliberately separate from calibration and robust-NROY
orchestration.

Passing `--validate-only` establishes that the JSON and M0/M1 config route are
well formed. It does **not** establish scientific validity. In particular, M1
must not be described as production-ready until the separately authorized
identity gate, measured pilot, and scientific review have passed.

## Start from a copy

Use the shared candidate as the source and give each analysis its own named
copy. Do not edit the canonical file in place.

```bash
cp configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json \
  configs/my_analysis_config.json
```

Edit scientific parameter `value` fields in `configs/my_analysis_config.json`
and record the purpose and source of every change. The model remains runnable
without any calibration step.

## Validate without running the engine

M0 uses the ordinary diagnosis route:

```bash
.venv/bin/python run_model.py \
  --config configs/my_analysis_config.json \
  --model M0 \
  --validate-only
```

M1 must be selected explicitly and requires an explicit AIDS-symptom annual
probability:

```bash
.venv/bin/python run_model.py \
  --config configs/my_analysis_config.json \
  --model M1 \
  --p 0.20 \
  --validate-only
```

These commands do not construct the simulation engine.

## Run after the analysis is authorized

Choose a new, analysis-specific output directory:

```bash
.venv/bin/python run_model.py \
  --config configs/my_analysis_config.json \
  --model M0 \
  --seed 42001 \
  --output-dir outputs/collaborator_runs/my_analysis_m0_seed42001
```

For M1:

```bash
.venv/bin/python run_model.py \
  --config configs/my_analysis_config.json \
  --model M1 \
  --p 0.20 \
  --seed 42001 \
  --output-dir outputs/collaborator_runs/my_analysis_m1_p020_seed42001
```

## Editable and protected inputs

Ordinary analysis inputs may be changed in a copied config when the change is
documented and scientifically justified. Examples include population,
transmission, progression, intervention, and costing parameter `value` fields.
The seed, model choice, M1 `p`, and output directory should be supplied on the
command line so the run intent is visible.

Do not edit these protected shared-route fields:

- `run_control.deterministic_mode.value` must remain `false`;
- `stage_structured_diagnosis.enabled.value` must remain `false` in the saved
  shared config; `run_model.py` controls it on a runtime copy;
- `stage_structured_diagnosis.routine_hazard_scale.value` must remain `1.0`;
- `stage_structured_diagnosis.aids_symptom_annual_probability.value` must
  remain `0.0` in the saved shared config; use `--p` for M1;
- `stage_structured_diagnosis.symptom_start_year.value` must remain `1985`;
- calibration-space paths and robust-NROY acceptance settings are not ordinary
  collaborator inputs.

The command fails with a plain `ERROR:` message if a protected field or model
choice is invalid. M0 removes the optional M1 block from its runtime copy; M1
explicitly enables the block and injects `p`. Neither route mutates the saved
JSON file.
