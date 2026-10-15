# Scientific artifact storage after repository cleanup

**Status: CURRENT (2026-10-16).**

Large simulation outputs are no longer stored as extracted trees inside the
Git working directories. They are preserved in a sibling vault:

```text
/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model-artifacts/
```

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
