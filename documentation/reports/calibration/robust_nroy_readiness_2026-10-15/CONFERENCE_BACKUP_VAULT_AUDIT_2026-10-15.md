# Conference backup vault audit — 2026-10-15

## Decision

Retain stable artifact `conference_scaleway_20260929` as
`HISTORICAL_RETAINED`. Its canonical local archive set is
`outputs/backups/conference_overnight_scaleway_20260929/archives/final_verified`.
It supports the recorded 2026-09-29 conference lineage only; it is not current
Option-B calibration or robust-NROY production evidence.

The machine-readable contract, hashes, restoration expectations, and cleanup
gates are in
`documentation/artifact_catalog/vault_conference_scaleway_20260929.json`.
No archive or output was moved, deleted, rewritten, or resealed in this pass.

## Restoration evidence

- All four canonical archive hashes match `ARCHIVE_SHA256SUMS`.
- A fresh temporary extraction contained 29,261 files and 3,540,897,373
  logical bytes and was byte-identical to `restore_final_verified`.
- Archive members use safe relative paths and contain only regular files and
  directories; the restore contains no symlinks.
- All 47 final provenance checksum entries pass.
- `LOCAL_FINAL_RESTORE_VALIDATION.json` records 59 passed checks and no failure
  artifacts. Ignoring the validation time and absolute restore paths, it is
  semantically identical to the validation record inside the provenance
  archive.
- SHA-256 establishes integrity, not signature authenticity or off-machine
  durability. No external canonical location or approved backup policy is yet
  recorded.

## Duplicate and superseded material

Three older archive copies are byte-identical to the canonical files and meet
the evidence definition of `DISPOSABLE_DUPLICATE`:

- `calibration_suite_2008_vs_2010.tgz`
- `conference_results_full.tgz`
- `prepared_policy_suite.tgz`

Together they contain 307,318,223 duplicate bytes. Classification does not
authorize deletion; owner approval and a verified destination copy remain
required.

The older `provenance_config_final.tgz` is **not** a duplicate. Its 56-line
internal checksum manifest lists nine absent AppleDouble files and fails. The
canonical provenance archive has a passing 47-line manifest. Classify the
older archive `SUPERSEDED`, preserving this warning in the vault record.

The two loose restore trees are cleanup candidates, not yet disposable as
whole directories. They share 29,260 byte-identical files, but their
`provenance_config_final/SHA256SUMS` files differ. Both must remain until the
external location, backup policy, destination verification, and owner cleanup
decision are complete.

## Consumer contract

Historical consumers now resolve an explicit extracted artifact root from
`HIV_PREP_ARTIFACT_ROOT`. The variable must be an absolute path whose root
contains `outputs/` and `provenance_config_final/`. When unset, the existing
`restore_final_verified` path is retained as a clearly labeled legacy default.
Missing required subpaths fail before output or simulation, with no implicit
download or extraction.

The migrated consumers are:

- `tools/build_professor_packet_20260929.py`
- `run_followup_knot_and_late_dx_20260929.py`

Derived packet and follow-up provenance uses
`artifact://conference_scaleway_20260929/...` rather than embedding a new
machine-specific vault path.

## Handoff and cleanup gates

Source-only collaborator handoff is **not blocked**. `outputs/` is ignored and
the current model-run workflow does not require this historical artifact.

Full artifact-vault handoff remains **blocked** until all of the following are
true:

1. a canonical external location and access policy are recorded;
2. a backup/retention policy is approved;
3. the destination copy passes archive checksums and a fresh restore check;
4. the owner approves destructive cleanup; and
5. the seed audit is regenerated after old paths are removed.

Recommended storage design: an immutable institutional external copy as the
canonical vault plus a local verified cache selected with
`HIV_PREP_ARTIFACT_ROOT`. This needs only the tracked manifest and explicit
restore procedure; it does not require DVC, a database, or implicit network
behavior in scientific commands.
