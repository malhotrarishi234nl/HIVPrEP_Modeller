# Model operator guide: explicit M0/M1 runs

## Audience and status

This is the parameter-focused route for collaborators who run the model but do
not edit Python. It is deliberately separate from calibration and robust-NROY
orchestration.

Passing `--validate-only` establishes that the JSON and M0/M1 config route are
well formed. It does **not** establish scientific validity. In particular, M1
must not be described as production-ready until the separately authorized
identity gate, measured pilot, and scientific review have passed.

The repository currently has no collaborator config approved for a real run.
That is intentional: draft and recalibration configs remain useful with
`--validate-only`, while an actual run fails before creating outputs or
importing the engine.

## Start from a copy

Use the shared candidate as the source and give each analysis its own named
copy. Do not edit the canonical file in place.

```bash
cp configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json \
  configs/my_analysis_config.json
```

The named file is the current Option B **recalibration starting point**, not an
approved fitted model. It is suitable for mechanical validation and for a
separately authorized calibration workflow; it is not yet eligible for real
collaborator simulations.

Edit only existing scientific parameter `value` payloads in
`configs/my_analysis_config.json` and record the purpose and source of every
change. Do not add, remove, or rename keys or list entries, and do not edit
notes, sources, tiers, bounds, distribution metadata, or provenance. The model
remains runnable without any calibration step. When a parameter object declares
numeric `bounds`, `range`, `min`/`max`, or `minimum`/`maximum`, an edited value
must remain inside that declared inclusive interval. The CLI does not invent a
range where the config declares none; absence of a range is not scientific
approval for an arbitrary value.

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
They may omit `--seed` because no stochastic trajectory is created.
Validation-only also does not require production approval; its success is a
mechanical preflight result, not permission to simulate.

The CLI checks that `p` is a mathematical probability in `[0,1]`. That is a
structural check, not approval of a scientific range. The robust-NROY plan
currently carries an **unsigned** search-range proposal of `[0,0.5]`; use only
the range approved for the analysis being run and do not treat CLI acceptance
as scientific acceptance.

## Run after the analysis is authorized

These commands become usable only after a maintainer promotes the canonical
config with the protected approval contract below. Until then they fail closed.
Once promoted, choose an explicit seed and a new, analysis-specific output
directory. A real run always requires `--seed`; the CLI never falls back to the
saved config seed.

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

Before importing the engine, the runner creates two files in the new output
directory:

- `effective_config.json`: the exact routed runtime config, including explicit
  M0/M1 choice, M1 `p` when applicable, and output route;
- `run_reproducibility_manifest.json`: input-config path and SHA-256,
  effective-config path and SHA-256, model, `p`, explicit seed, output path,
  Python/dependency/platform identity, Git identity, and hashes of the direct
  model implementation files.

The manifest starts as `PREPARED`, becomes `COMPLETE` only after the run
returns successfully, and becomes `FAILED` with the exception type if the
engine raises. Do not relabel a `PREPARED` or `FAILED` directory as a completed
result. The output-directory non-overwrite rule protects these sidecars along
with the scientific outputs.

## Protected production approval

Every actual `run_model.py` simulation requires the canonical config to carry
this exact maintainer-owned block:

```json
"production_approval": {
  "schema": "model_config_production_approval/1.0.0",
  "status": "APPROVED",
  "production_eligible": true,
  "approved": true,
  "approved_by": "Named approving owner",
  "approved_utc": "2026-10-15T09:00:00Z"
}
```

The approver and timestamp must be non-empty and the timestamp must include a
timezone. This block records a completed promotion; it must not be added merely
to make the command run. It is protected like provenance and cannot be added or
changed in a collaborator copy. There is no command-line bypass. Promotion
therefore requires a reviewed canonical-config change after calibration and
scientific acceptance; collaborators continue to edit only permitted parameter
values in copies of that promoted canonical config.

## Editable and protected inputs

Ordinary analysis inputs may be changed in a copied config when the change is
documented and scientifically justified. Examples include existing population,
transmission, progression, intervention, and costing parameter `value`
payloads. The validator preserves their object/list shape and scalar JSON type.
Declared numeric bounds/ranges are enforced for changed values. Values without
declared ranges still require scientific justification; the CLI does not infer
new bounds from names, notes, or neighbouring parameters. The seed, model
choice, M1 `p`, and output directory must be supplied on the command line so
the run intent is visible.

Do not edit these protected shared-route fields:

- any key name, list length, non-`value` field, `_readme`, `meta`,
  `v13_14_diagnostic`, or `calibration_metadata` content;
- any `run_control` or `output` field, including their `value` payloads;
- `run_control.deterministic_mode.value` must remain `false`;
- `stage_structured_diagnosis.enabled.value` must remain `false` in the saved
  shared config; `run_model.py` controls it on a runtime copy;
- `stage_structured_diagnosis.routine_hazard_scale.value` must remain `1.0`;
- `stage_structured_diagnosis.aids_symptom_annual_probability.value` must
  remain `0.0` in the saved shared config; use `--p` for M1;
- `stage_structured_diagnosis.symptom_start_year.value` must remain `1985`;
- calibration-space paths and robust-NROY acceptance settings are not ordinary
  collaborator inputs.
- the top-level `production_approval` block is maintainer-owned and must match
  the canonical config exactly.

The command fails with a plain `ERROR:` message if a protected field or model
choice is invalid. For a forbidden copy edit, the error names the first path
and directs the operator to start from a fresh canonical copy. M0 removes the
optional M1 block from its runtime copy; M1 explicitly enables the block and
injects `p`. Neither route mutates the saved JSON file.
