# Scientific artifact storage after repository cleanup

**Status: CURRENT (2026-10-16).**

Large simulation outputs are no longer stored as extracted trees inside the
Git working directories. They are preserved in a sibling vault:

```text
/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model-artifacts/
```

The cleaned current source repository remains at the familiar sibling path
`hiv-prep-model`.

Start with the vault's `README.md`, `ARTIFACT_INDEX.csv`, and `SHA256SUMS`.
The vault contains:

- a complete 835-file archive of the current bounded Option-B run plus a
  36-file readable report subset;
- one complete archive of all 132 legacy calibration, HTA, and suite artifact
  directories;
- the canonical verified conference archive set, its validation record, and
  the non-identical superseded provenance archive;
- complete historical professor/abstract deliverables plus readable copies;
- historical logs;
- a verified Git bundle and exact pre-cleanup live-worktree snapshot;
- the reusable local Python environment under `runtime/`.

Every scientific archive passed gzip validation and a fresh-extraction
comparison before its extracted source tree was removed. The conference set
also reproduced 29,261 files and passed all 47 provenance checksums. The vault
is local to this machine and is not an off-machine disaster-recovery backup.

The workspace-level `../../../README.md` is the authoritative map for the sibling
directories. In summary:

- `hiv-prep-model/` is the only active working source directory;
- `hiv-prep-model-artifacts/` holds preserved run evidence and readable copies;
- `hiv-prep-model-deliverables-20261015/` is a frozen handoff snapshot; and
- `hiv-prep-model-research-e2e-20261015/` is an isolated support checkout, not
  another place to launch or store runs.

## Restore an archived output tree

The output archives store repository-relative `outputs/...` paths. Restore an
archive into a repository root with:

```bash
tar -xzf /absolute/path/to/archive.tar.gz -C /absolute/path/to/repository
```

Do not extract an archive merely to read a headline report. Use the readable
copies under `vault/current/readable/` and `vault/deliverables/readable/`.

## Inputs retained for ordinary Option-B validation

Two historical inputs still required by the Option-B validator are extracted
under:

```text
hiv-prep-model-artifacts/runtime_inputs/outputs/calibration/
```

Local working checkouts may expose those two directories through ignored
symlinks at their original `outputs/calibration/...` paths. Those symlinks are
machine-local conveniences and are not part of Git. A collaborator receiving
only the source repository must also receive the required artifact/input
bundle described here.

## New runs

New runs may write to the usual ignored `outputs/` path. After review, archive
and verify them into the sibling vault rather than allowing the repository
working directory to accumulate permanent raw results again.

Use this minimum lifecycle for every substantial run:

1. choose a unique output directory and never overwrite an earlier run;
2. preserve the exact commit, effective config, seeds, manifest, and completion
   status with the output;
3. classify the result as current, historical, incomplete/failed, or safely
   regenerable;
4. for retained runs, create a complete vault archive and a small readable
   subset where useful;
5. verify the archive with `gzip -t`, fresh extraction, and recursive
   comparison;
6. update `ARTIFACT_INDEX.csv` and the vault's `SHA256SUMS`; and
7. remove the extracted bulk output from the working repository only after all
   verification succeeds.

Do not create another dated source-repository copy to preserve a run. Use a Git
branch or managed worktree for source isolation and the artifact vault for run
payloads. Do not put virtual environments, caches, raw logs, or temporary
archive extractions in the repository.

Before collaborator handoff or after a substantial run series, check:

```bash
du -sh . ../hiv-prep-model-artifacts
git status --short --ignored
```

Unexpected repository growth above roughly 250 MB should trigger an inventory
of ignored outputs and environments. This is a housekeeping warning threshold,
not a limit on scientific computation or artifact retention.
