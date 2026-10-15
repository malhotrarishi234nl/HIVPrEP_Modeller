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

Run every command in this guide from the repository root. Create the dedicated
operator environment with Python 3.11:

```bash
python3.11 --version
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-operator.txt
.venv/bin/python -c 'import sys, numpy, pandas, scipy; assert sys.version_info[:2] == (3, 11); print(sys.version.split()[0], numpy.__version__, pandas.__version__, scipy.__version__)'
```

The first command must report Python `3.11.x`; the last must print that Python
version followed by the exact NumPy, pandas, and SciPy versions pinned in
`requirements-operator.txt`. If either check fails, stop and contact the
**Model Maintainer** before validating or running the model.
`--validate-only` remains usable for configuration review in another supported
development environment, but an actual run independently enforces CPython
3.11 and every exact pin in `requirements-operator.txt` before importing the
engine.

This ordinary `run_model.py` M0/M1 route is source-only once those dependencies
are installed. It does not need the external validation workbooks,
`../data/raw/`, or `../literature/`. Calibration, robust-NROY, and
data-validation are separate lanes and still require their documented,
hash-pinned sibling inputs. Redistribution decisions for those inputs remain
unresolved; do not copy them into a collaborator setup.

Next confirm that both M0 and M1 pass the validation commands below before
requesting permission for a run.

Use the shared candidate as the source and give each analysis its own named
copy. Operator copies live outside the controlled `configs/` registry so that
creating one cannot make the repository verification gate fail. Do not edit
the canonical file in place and do not put an analysis copy under `configs/`.

```bash
mkdir -p operator_configs
cp configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json \
  operator_configs/my_analysis_config.json
```

`operator_configs/` is intentionally ignored by Git. Preserve each reviewed
analysis config together with its run record and result package; do not treat
the ignored working copy as the only archival copy.

The named file is the current Option B **recalibration starting point**, not an
approved fitted model. It is suitable for mechanical validation and for a
separately authorized calibration workflow; it is not yet eligible for real
collaborator simulations.

The candidate's protected `calibration_metadata` preserves an absolute
`source_config_path` from the workstation where the earlier calibration was
recorded. That string is inert provenance: `run_model.py` does not open or
depend on it, and source-only validation works without that path. Do not edit
it in an operator copy. The maintainer must replace it with portable provenance
when the newly recalibrated config is reviewed and promoted; the current draft
must not be promoted unchanged.

The exact editable surface is the short, machine-readable
`configs/collaborator_parameter_contract.json`. Edit only a listed `path`, keep
the numeric value inside that path's closed `minimum`/`maximum` interval, and
record the purpose and source of every change in the analysis record supplied by
the maintainer. Every other field is protected, including fixed-tier values,
mode/enum strings, switches, indexed group definitions, notes, sources, tiers,
bounds, distribution metadata, and provenance. The coupled initial-stock and
sexual-debut `p_msm` values must remain equal.

Passing validation means the copy preserves the reviewed structure and bounded
parameter surface. It still does not establish scientific validity. To expand
the editable surface, a maintainer must review and change the contract; an
operator must not work around it by editing another JSON field.

## Validate without running the engine

M0 uses the ordinary diagnosis route:

```bash
.venv/bin/python run_model.py \
  --config operator_configs/my_analysis_config.json \
  --model M0 \
  --validate-only
```

M1 must be selected explicitly and requires an explicit AIDS-symptom annual
probability:

```bash
.venv/bin/python run_model.py \
  --config operator_configs/my_analysis_config.json \
  --model M1 \
  --p 0.20 \
  --validate-only
```

These commands do not construct the simulation engine.
They may omit `--seed` because no stochastic trajectory is created. If supplied,
the seed must be an integer in `[0, 4294967295]`, the domain accepted by the
NumPy/engine RNG.
Validation-only also does not require production approval; its success is a
mechanical preflight result, not permission to simulate.

Successful validation ends with one of these exact messages:

```text
VALID: M0 config route; stochastic=true; engine was not constructed
VALID: M1 config route; stochastic=true; engine was not constructed
```

Any `ERROR:` message, nonzero exit, traceback, or missing success line is a
failure. Do not proceed to a real run.

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

Do not reuse the draft copy made before promotion. After promotion, make a fresh
copy of the newly approved canonical config, reapply only the reviewed parameter
diff, and run `--validate-only` again. Obtain the seed and output destination
from the approved analysis run sheet; do not invent or casually reuse the
example seed below. One command and one sheet authorize exactly one stochastic
trajectory. Any additional seed, model, or destination requires its own sheet.
This single-run interface does not claim to enforce M0/M1 pairing or the wider
replication design; those belong in the reviewed analysis design from which the
individual dispatch sheets are issued.

The maintainer and approving owners must create the run sheet from
[`COLLABORATOR_ANALYSIS_RUN_SHEET.json.template`](COLLABORATOR_ANALYSIS_RUN_SHEET.json.template).
The template is deliberately incomplete and cannot authorize a run. Save the
completed sheet with the reviewed analysis record, for example as
`operator_configs/my_analysis_run_sheet.json`. It binds the exact analysis
config, collaborator parameter contract, scientific source aggregate, operator
requirements, M0/M1 choice, exact M1 `p` (or `null` for M0), exactly one uint32
seed, and the exact canonical absolute output directory. It also requires a named,
timezone-stamped
scientific approval and a separate named, timezone-stamped dispatch
authorization. These are separate records even if governance permits one
person to hold both roles. An operator must not fill placeholders or change the
sheet to make a rejected command run. Scientific approval cannot predate the
canonical production approval, and dispatch authorization cannot predate the
scientific approval.

```bash
.venv/bin/python run_model.py \
  --config operator_configs/my_analysis_config.json \
  --model M0 \
  --seed 42001 \
  --run-sheet operator_configs/my_analysis_run_sheet.json \
  --output-dir outputs/collaborator_runs/my_analysis_m0_seed42001
```

For M1:

```bash
.venv/bin/python run_model.py \
  --config operator_configs/my_analysis_config.json \
  --model M1 \
  --p 0.20 \
  --seed 42001 \
  --run-sheet operator_configs/my_analysis_run_sheet.json \
  --output-dir outputs/collaborator_runs/my_analysis_m1_p020_seed42001
```

Before importing the engine, the runner creates three files in the new output
directory:

- `effective_config.json`: the exact routed runtime config, including explicit
  M0/M1 choice, M1 `p` when applicable, and output route;
- `approved_analysis_run_sheet.json`: a byte-exact fixed-name copy of the
  approved and dispatched run sheet, so the run remains auditable if its source
  path later disappears;
- `run_reproducibility_manifest.json`: input-config path and SHA-256,
  effective-config path and SHA-256, model, `p`, explicit seed, output path,
  parameter-contract path and SHA-256, analysis-run-sheet source path, sealed
  path and SHA-256, operator-requirements path and SHA-256,
  Python/dependency/platform identity, Git identity, and hashes of the direct
  model implementation files.

With the protected canonical output settings, a completed one-run directory is:

```text
outputs/collaborator_runs/<analysis-and-seed>/
  approved_analysis_run_sheet.json
  baseline_run_001.csv
  effective_config.json
  run_reproducibility_manifest.json
```

The manifest starts as `PREPARED`. It becomes `COMPLETE` only after the engine
returns and `baseline_run_001.csv` exists as a nonempty regular file; the
manifest then records that file's relative path, byte size, and SHA-256. It
becomes `FAILED` if the engine raises or returns without that exact protected
scientific output. Do not relabel a `PREPARED` or `FAILED` directory as a
completed result. The output-directory non-overwrite rule protects these
sidecars along with the scientific output.

## Protected production approval

Every actual `run_model.py` simulation requires the canonical config to carry
this exact maintainer-owned block:

```json
"production_approval": {
  "schema": "model_config_production_approval/1.2.0",
  "status": "APPROVED",
  "production_eligible": true,
  "approved": true,
  "approved_by": "Named approving owner",
  "approved_utc": "2026-10-15T09:00:00Z",
  "collaborator_parameter_contract_sha256": "<exact reviewed contract SHA-256>",
  "scientific_files_sha256": "<exact MODEL_CODE_FILES aggregate SHA-256>",
  "requirements_operator_sha256": "<exact requirements-operator.txt SHA-256>"
}
```

After the reviewed files are frozen, a maintainer can print the three current
binding values without constructing the engine:

```bash
.venv/bin/python -c 'import json, run_model; print(json.dumps(run_model.production_approval_binding_values(), indent=2, sort_keys=True))'
```

Printing hashes is not approval. The named owner must still review the frozen
inputs and deliberately create the protected canonical block.

The approver and timestamp must be non-empty and the timestamp must include a
timezone. This block records a completed promotion; it must not be added merely
to make the command run. It is protected like provenance and cannot be added or
changed in a collaborator copy. There is no command-line bypass. Promotion
therefore requires a reviewed canonical-config change after calibration and
scientific acceptance; collaborators continue to edit only permitted parameter
values in copies of that promoted canonical config.

The three hashes are essential. The contract hash means the approver reviewed
the complete editable path-and-interval domain, not merely the canonical point
values. The scientific-files hash is the deterministic aggregate over the
individual SHA-256 values for the `MODEL_CODE_FILES` list in `run_model.py`;
the requirements hash binds the complete bytes of `requirements-operator.txt`.
A copy may vary only within the approved parameter domain. Changing any bound
file, changing the canonical config outside `production_approval`, or copying
an old approval into a different config invalidates the approval. There is no
Git-commit field in this block, avoiding a circular commit binding; the run
manifest separately records commit, dirty state, scientific file hashes, and
scientific diff hash. The current contract is a fail-closed code contract, not
scientific approval; it must be reviewed again after recalibration changes the
canonical parameter vector.

This JSON block is a provenance record and an accidental-run guard, not a
cryptographic authentication mechanism. Someone with write access to the
canonical repository can fabricate its text. For a real collaborator release,
the maintainer must distribute the reviewed canonical config from the named
reviewed commit/bundle and supply a separately approved analysis run sheet.
Reviewers must reconcile the run manifest's config and Git hashes to that
release. Neither the block alone nor possession of a runnable copy proves
scientific acceptance or dispatch authorization. The separate run sheet is
mandatory for every actual invocation; its sole seed must equal `--seed`. The
sheet must use the canonical absolute output path—a symlink or `..` spelling is
rejected—while a relative CLI path is resolved once to that same destination
and the canonical path is then used for validation, provenance, engine output,
and final verification. Because that directory must initially be empty and the
runner writes its sealed sheet and manifest before engine import, the same
dispatch cannot be replayed into that destination.

## Editable and protected inputs

Only numeric paths in `configs/collaborator_parameter_contract.json` may change
in a copied config, and every change must be documented and scientifically
justified. The validator enforces the contract interval plus any compatible
co-located canonical range. The seed, model choice, M1 `p`, and output directory
must be supplied on the command line so the run intent is visible.

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
choice is invalid. JSON inputs on this route reject duplicate keys at any
nested level and non-standard `NaN`/`Infinity` constants instead of silently
normalizing them. For a forbidden copy edit, the error names the first path
and directs the operator to start from a fresh canonical copy. M0 removes the
optional M1 block from its runtime copy; M1 explicitly enables the block and
injects `p`. Neither route mutates the saved JSON file.

## Failure handoff

Stop after a validation or run failure. Do not change protected fields, delete
the output directory, or rerun with a different seed to make the failure
disappear. Send the **Model Maintainer** this concise handoff:

1. the exact command you ran, from the repository root;
2. the config path and its hash
   (`shasum -a 256 operator_configs/my_analysis_config.json`);
3. the run-sheet path and its hash;
4. the complete `ERROR:` text or traceback;
5. the requested output path, whether `run_reproducibility_manifest.json`
   exists there, and its `status` (`PREPARED`, `FAILED`, or `COMPLETE`), attaching
   the manifest when present; and
6. `git rev-parse HEAD` plus the version line printed by the environment check
   above.

If no output directory or manifest was created, say that explicitly; it often
confirms that the command failed closed before engine import. The Model
Maintainer owns technical triage and escalates scientific or approval questions
to the **Scientific Approving Owner** rather than asking the operator to modify
code or approval fields.
