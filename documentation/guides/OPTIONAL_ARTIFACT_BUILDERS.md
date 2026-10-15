# Optional workbook builders

The following scripts are retained because completed evidence packages cite
them:

- `scripts/build_hiv_diagnoses_excel.mjs`
- `tools/build_professor_workbook_20260929.mjs`

They depend on `@oai/artifact-tool`, which was supplied by the original Codex
workspace runtime. The former tracked `node_modules` entry was an absolute
symlink into one user's machine-local cache and has been removed because it
could never work in a collaborator clone.

These scripts are not required to run the epidemiological model, robust-NROY,
or HTA calculations. Their historical source is preserved, but they are
currently classified as environment-specific presentation builders rather than
supported collaborator commands.

If either becomes a maintained deliverable, add a real dependency manifest and
lockfile and test it in a clean environment. Do not recreate a tracked absolute
symlink.
