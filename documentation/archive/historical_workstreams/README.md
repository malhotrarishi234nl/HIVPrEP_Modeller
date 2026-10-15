# Historical workstreams archive

**Archive status:** retained provenance; not current operating guidance.

This directory holds historical planning documents that could be moved without
changing any path embedded in source code, scientific configuration, tests, or
the artifact catalog. No scientific result, decision record, or substantive
historical content was deleted.

Do not treat an archived plan as current run authority. Start from
`documentation/INDEX.md` and the current guide named there.

## Exact old-path to new-path map

| Former repository-root path | Preserved path |
|---|---|
| `PHASE2_IMPLEMENTATION_PLAN.md` | `documentation/archive/historical_workstreams/phase2/PHASE2_IMPLEMENTATION_PLAN.md` |
| `IMPLEMENTATION_PLAN_STAGE_STRUCTURED_DIAGNOSIS_AUDIT.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/IMPLEMENTATION_PLAN_STAGE_STRUCTURED_DIAGNOSIS_AUDIT.md` |

## Why other historical documents remain at the root

The dated `TASK_BRIEF_*`, `PHASE2_*`, coupled-search, history-matching, and
user-run documents are referenced by frozen configs, code, tests, or the
tracked artifact catalog. Moving them would either break those references or
require changing scientific/code hashes used by preserved runs. They therefore
remain at their original paths with a prominent `HISTORICAL SNAPSHOT` or
`superseded` banner. This is intentional provenance preservation, not an
assertion that those documents are current.

## What was deliberately not done

- No Python module, scientific configuration, test, or artifact-catalog record
  was changed to accommodate a documentation move.
- No scientific result or output artifact was deleted or rewritten.
- No historical conclusion was promoted to a current conclusion.
- No redirect stub was left at either former path.
- No simulation was launched.
