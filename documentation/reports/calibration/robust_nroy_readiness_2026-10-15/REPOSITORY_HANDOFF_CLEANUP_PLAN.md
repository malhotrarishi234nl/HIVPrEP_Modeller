# Repository and artifact handoff cleanup plan — 2026-10-15

## Audit verdict

The repository is not handoff-ready. Its fail-closed scientific provenance is
strong, but a new researcher cannot currently clone the repository, determine
the current workflow, reconstruct its required inputs, or run one clearly
defined verification gate.

This plan is non-destructive. No file should be moved or deleted until the
current dirty state is preserved and the relevant references have been
enumerated.

## Immediate blockers

1. The active branch is 20 commits ahead of `main`, with 40 modified tracked
   files and 24 untracked files, and no configured Git remote.
2. The README mostly describes an older V11 workflow and includes broken or
   obsolete entry points.
3. Git tracks an absolute `node_modules` symlink into a machine-local Codex
   cache, with no JavaScript dependency manifest.
4. A fresh clone lacks required sibling `../data/raw` and `../literature`
   inputs and no complete acquisition/hash/licensing manifest exists.
5. The standard test suite does not distinguish portable tests, external-data
   integration, historical-artifact validation, and real-engine gates.
6. `outputs/` contains approximately 12 GB and 74,000 files, including about
   7 GB of redundant conference restore trees/archive copies.
7. Root-level code and documents are too flat to identify current entry points
   and authoritative guidance.
8. The 68 configuration files have no central current/proposed/historical/
   diagnostic/test-only registry.

## Preservation-first sequence

1. Preserve the current source state in thematic commits and a verified Git
   bundle or remote before cleanup.
2. Add an artifact catalog and retention policy before moving outputs.
3. Replace the README with a short current start-here guide and label the old
   workflow historical.
4. Remove the tracked machine-specific dependency symlink and either declare
   the real dependency or archive the helper that used it.
5. Add an external-input manifest with logical path, source, hash,
   acquisition/licensing status, and consuming workflow.
6. Add a configuration registry before moving or renaming any config.
7. Split tests into:
   - portable source/unit gate;
   - external-data integration gate;
   - retained-artifact validation gate;
   - explicit real-engine/production gate.
8. Refactor the 29 current stale-artifact failures into passing tests that
   assert current code refuses historical module maps, while using synthetic or
   current fixtures for ordinary scientific transformation coverage.
9. Move durable evidence out of generated `logs/` and into its report package.
10. Classify every top-level calibration, HTA, and suite artifact before
    archive consolidation.
11. Move large immutable bundles to a stable artifact vault only after archive
    and restore verification and reference migration.
12. Re-run the portable gate in a fresh no-hardlink clone without `.venv`,
    outputs, or machine-local symlinks.

Do not mix wholesale Python module/package movement with this cleanup. Module
paths participate in imports and provenance hashes. Any later `src/` refactor
is a real separately tested implementation change.

## Artifact terminal classes

Every top-level artifact receives exactly one terminal class:

- `CURRENT_VALIDATED`: current-code compatible and eligible for current claims.
- `HISTORICAL_RETAINED`: valid only for its recorded code snapshot.
- `SUPERSEDED`: replaced by a specifically named later artifact.
- `FAILED_OR_INCOMPLETE`: forensic record; prohibited from scientific citation.
- `PREPARED_NOT_RUN`: design history, not scientific evidence.
- `DERIVED_PRESENTATION`: workbook, figure, PDF, or presentation with a named
  parent artifact.
- `DISPOSABLE_DUPLICATE`: byte-identical copy or regenerable trace; removable
  only after a verified canonical copy exists.

No artifact may remain classified merely as "old," "probably unused," or
"expected failure."

## High-value consolidation candidate

The conference backup under
`outputs/backups/conference_overnight_scaleway_20260929` is about 7.7 GB. Its
two extracted restore trees differ only in one checksum file: the older copy
indexes nine AppleDouble metadata files removed from the verified final copy.
Three archive files are byte-identical to their `final_verified` counterparts.

The verified final archive set is about 293 MB and has complete checksums. It
is the likely canonical retained bundle, but two scripts still reference the
extracted `restore_final_verified` tree. Before removing any duplicate:

1. migrate those consumers to a stable artifact ID/path or explicit restore;
2. verify the final archives by extraction in a temporary directory;
3. verify their internal checksums and expected file count;
4. record old-path redirects and the owner disposition;
5. only then remove duplicate loose trees/copies.

## Minimal artifact catalog

A tracked CSV or JSON file is sufficient. Do not introduce DVC, a database, or
a new orchestration platform unless future scale demonstrates a need.

Each row should include:

- stable artifact ID and original path;
- canonical archive path/URI;
- terminal class and scientific status;
- current-code compatibility;
- archive and manifest hashes, bytes, and file count;
- recorded commit/module map and dirty-state hash;
- parent and `superseded_by` IDs;
- citing tests/reports;
- retention rule and restoration command;
- owner decision and date.

## Decisions still required before destructive cleanup

- Location and backup policy for the artifact vault.
- Whether any stale analysis is still required by a live thesis/manuscript
  claim and therefore merits a current-code rerun.
- Which raw data and literature may legally accompany a handoff.
- Whether the JavaScript spreadsheet helper remains supported project code.
- Which professor deliverable is canonical.
- Whether early suite revisions are retained individually or as one compact
  design-history bundle.
- Whether full artifact-vault validation runs routinely or as an optional
  mounted-data lane.

Default recommendation: retire stale analyses unless a live claim depends on
them; retain complete raw data for cited/current results; cold-archive
superseded and incomplete work; keep artifact-vault validation separate from
the portable source gate.

