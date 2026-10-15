# Scientific output artifact catalog

This directory records a read-only inventory of every immediate child directory
under `outputs/calibration`, `outputs/hta_results`, and `outputs/suites` in the
live research checkout. The snapshot was taken on 2026-10-15. It is an
inventory and triage aid, not a scientific validation certificate or an
instruction to delete data.

## Coverage and open review

The catalog contains 132 top-level directories: 78 calibration, 44 HTA, and 10
suite directories. Together they contain 15,586 files and 4,189,322,843
logical bytes. There are 330 manifest-named files across 104 directories; 89
directories are named by at least one tracked repository file. Loose files
directly inside the three roots are listed separately in the JSON summary so
they remain visible even though the row-level scope is directories.

Of the 132 directories, 34 have a terminal evidence-backed disposition and 98
remain `REVIEW_REQUIRED`. The terminal counts are:

- 12 `HISTORICAL_RETAINED`
- 11 `SUPERSEDED`
- 8 `PREPARED_NOT_RUN`
- 3 `FAILED_OR_INCOMPLETE`

No directory is currently classified `CURRENT_VALIDATED`,
`DERIVED_PRESENTATION`, or `DISPOSABLE_DUPLICATE`. That is deliberate: the
available evidence did not establish those stronger claims within this scope.
Known conference backup duplicates live under `outputs/backups`, outside the
three roots cataloged here, and are not silently imported into this catalog.

## Classification policy

- `CURRENT_VALIDATED`: supported by explicit current-code scientific and
  provenance evidence. Presence, successful completion, or citation alone is
  insufficient.
- `HISTORICAL_RETAINED`: scientifically or operationally useful history that is
  explicitly retired from current claims.
- `SUPERSEDED`: a named successor is supported by tracked evidence. Preserve
  until successor and recovery evidence have been reviewed; this label alone
  does not authorize deletion.
- `FAILED_OR_INCOMPLETE`: tracked evidence identifies a failed or incomplete
  attempt. Retain for forensic value and do not cite as a scientific result.
- `PREPARED_NOT_RUN`: design or launch material without executed scientific
  results.
- `DERIVED_PRESENTATION`: presentation-layer material with a named scientific
  parent.
- `DISPOSABLE_DUPLICATE`: byte-identity and a canonical retained copy have been
  established. No item in the current scope meets this bar.
- `REVIEW_REQUIRED`: no terminal disposition is supported yet. Preserve in
  place and do not clean up until owner review supplies evidence.

`manifest_present` means only that a manifest-named file exists. A manifest's
declared `status` or `stage` is recorded but is not independently trusted as
proof of compatibility or current scientific acceptance. Likewise,
`tracked_reference_count > 0` means the directory is cited somewhere in
tracked text; it does not mean the result is current, validated, or suitable
for a claim.

All explicit decisions live in `catalog_overrides.json` and cite their evidence.
Rows without an override fail conservatively to `REVIEW_REQUIRED`. The CSV is
the review-friendly flat view; the JSON is authoritative for typed values,
summary counts, and loose root files.

## Rebuild and verification

From the repository root, point the tool at the live output directory:

```bash
python3 tools/build_artifact_catalog.py \
  --outputs-root "/absolute/path/to/hiv-prep-model/outputs" \
  --write

python3 tools/build_artifact_catalog.py \
  --outputs-root "/absolute/path/to/hiv-prep-model/outputs" \
  --check
```

`--check` fails if any current top-level directory is missing or any catalog row
is extra, if the CSV and JSON disagree, or if review flags and classification
counts are inconsistent. The tool reads output files for metadata and manifest
hashes only. It does not move, delete, rewrite, or reseal output artifacts, and
it does not run the scientific engine.
