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

Of the 132 directories, 123 have a terminal evidence-backed disposition and 9
remain `REVIEW_REQUIRED`. The terminal counts are:

- 78 `HISTORICAL_RETAINED`
- 25 `SUPERSEDED`
- 9 `PREPARED_NOT_RUN`
- 7 `FAILED_OR_INCOMPLETE`
- 4 `DERIVED_PRESENTATION`

No directory is currently classified `CURRENT_VALIDATED` or
`DISPOSABLE_DUPLICATE`. That is deliberate: the available evidence did not
establish those stronger claims within this scope. Known conference backup
duplicates live under `outputs/backups`, outside the three roots cataloged
here, and are not silently imported into this catalog.

## Terminal-classification pass

The 2026-10-15 terminal pass reviewed all 98 formerly open rows against their
manifests, status/report files, manifest hashes, tracked references, and named
successors. It applied four conservative rules:

1. The project state-of-play explicitly says every result computed before
   2026-09-14 is stale after the A-28/A-25/C-4/C-1 changes. Those artifacts are
   retained as historical evidence, or marked superseded only when the same
   report names a later evidence directory.
2. A `COMPLETE`, `VALID`, or `REPORTED_COMPLETE` manifest is not scientific
   acceptance. Completed diagnostic/search artifacts are historical when their
   own reports say they are not a production baseline, when the production
   route has changed, or when an independently verified search found no primary
   pass. No such row was promoted to `CURRENT_VALIDATED`.
3. `RUNNING`, aborted, zero-sample, and explicitly incomplete artifacts are
   `FAILED_OR_INCOMPLETE`. Passed smoke, writer, and synthetic checks are
   retained as engineering history and explicitly excluded from scientific
   claims.
4. Read-only recomputations with a hash-identified scientific parent are
   `DERIVED_PRESENTATION`. This label preserves them with the named parent and
   does not make the derived summary independent scientific evidence.

No files under `outputs/` were moved, deleted, rewritten, or resealed during
this pass.

## Nine owner decisions still required

The following rows stay `REVIEW_REQUIRED` because they may still feed a live
thesis, conference, target-contract, or parameter claim. Their manifests and
citations establish presence, not the owner decision or current scientific
acceptance needed to retire or promote them.

1. Current diagnosis/conference chain (three rows):
   `outputs/calibration/diagnosis_triangulation_orchestration_20260922`,
   `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922`, and
   `outputs/calibration/followup_knot_and_late_dx_20260929`. Decide which, if
   any, remains the canonical live claim source. A live source needs an explicit
   current-code/current-config validation route; a retired source can be marked
   historical or superseded by the named canonical successor.
2. Active evidence/assumption inputs (five rows):
   `outputs/calibration/rivm_aids_vintage_audit_20260916`,
   `outputs/hta_results/ae_adherence_corridor_20260914_225729`,
   `outputs/hta_results/aids_at_diagnosis_20260914_183434`,
   `outputs/hta_results/b1_b2_calibration_space_20260914`, and
   `outputs/hta_results/rivm_prep_targets_20260914_134653`. For each, record
   whether its evidence/assumption is adopted in the current target or economic
   contract and has received the required scientific review; otherwise retire
   it as historical. Citation by a config or report is not approval.
3. Variance input (one row):
   `outputs/hta_results/a5_ensemble_variance_20260914_150556`. Decide whether
   its recorded variance estimate remains an approved input under the selected
   1980 Option-B recalibration, must be rerun for that backbone, or is retired
   as historical. Until then it must not be relabeled current or removed.

These nine decisions do not authorize destructive cleanup. Every open row keeps
the `preserve_in_place;no_cleanup_until_terminal_review` retention rule.

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

`--check` re-enumerates and remeasures the live directories. It fails if a
directory or loose root file was added or removed; if sizes, file/directory
counts, modification times, manifest paths or hashes, declared manifest fields,
tracked references, or evidence-based classifications drifted; if the CSV and
JSON disagree; or if review flags and classification counts are inconsistent.
Success therefore means the tracked catalog is an exact current snapshot for
the recorded fields, not merely that its path list is complete. When drift is
reported, review it before deliberately running `--write` to accept a new
snapshot. Neither mode validates scientific claims. The tool reads output files
for metadata and manifest hashes only. It does not move, delete, rewrite, or
reseal output artifacts, and it does not run the scientific engine.
