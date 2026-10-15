# Historical workstreams archive

**Archive status:** retained provenance; not current operating guidance.

This directory holds historical planning documents that could be moved without
changing a path embedded in source code, scientific configuration, or tests.
The tracked artifact catalog and seed audit remain immutable snapshots of the
files at their original paths; the map below is the lookup bridge from those
recorded paths to the preserved documents. No scientific result, decision
record, or substantive historical content was deleted.

Do not treat an archived plan as current run authority. Start from
`documentation/INDEX.md` and the current guide named there.

## Exact old-path to new-path map

| Former repository-root path | Preserved path |
|---|---|
| `PHASE2_IMPLEMENTATION_PLAN.md` | `documentation/archive/historical_workstreams/phase2/PHASE2_IMPLEMENTATION_PLAN.md` |
| `IMPLEMENTATION_PLAN_STAGE_STRUCTURED_DIAGNOSIS_AUDIT.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/IMPLEMENTATION_PLAN_STAGE_STRUCTURED_DIAGNOSIS_AUDIT.md` |
| `CLAUDE_COUPLED_SEARCH_HANDOFF_2026-09-18.md` | `documentation/archive/historical_workstreams/calibration/CLAUDE_COUPLED_SEARCH_HANDOFF_2026-09-18.md` |
| `COUPLED_SEARCH_RUNBOOK.md` | `documentation/archive/historical_workstreams/calibration/COUPLED_SEARCH_RUNBOOK.md` |
| `TASK_BRIEF_JOINT_FIT_CONFLICT_2026-09-17.md` | `documentation/archive/historical_workstreams/calibration/TASK_BRIEF_JOINT_FIT_CONFLICT_2026-09-17.md` |
| `USER_RUN_COMMANDS_FOLLOWUP_2026-09-29.md` | `documentation/archive/historical_workstreams/calibration/USER_RUN_COMMANDS_FOLLOWUP_2026-09-29.md` |
| `PHASE2_AGENT_HANDOFF_2026-09-09.md` | `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` |
| `PHASE2_STRATEGY_BRIEF_2026-09-06.md` | `documentation/archive/historical_workstreams/phase2/PHASE2_STRATEGY_BRIEF_2026-09-06.md` |
| `TASK_BRIEF_A23_HIV_MORTALITY_ANOMALY_2026-09-13.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A23_HIV_MORTALITY_ANOMALY_2026-09-13.md` |
| `TASK_BRIEF_A24_ENTRY_PATHWAY_CRN_DRIFT_2026-09-14.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A24_ENTRY_PATHWAY_CRN_DRIFT_2026-09-14.md` |
| `TASK_BRIEF_A28_CRN_COUPLING_FIX_2026-09-14.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A28_CRN_COUPLING_FIX_2026-09-14.md` |
| `TASK_BRIEF_A29_TRANSMISSION_BLOCK_GROUPING_2026-09-14.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A29_TRANSMISSION_BLOCK_GROUPING_2026-09-14.md` |
| `TASK_BRIEF_A2_ESTIMATOR_2026-09-13.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A2_ESTIMATOR_2026-09-13.md` |
| `TASK_BRIEF_A5_ENSEMBLE_VARIANCE_2026-09-14.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A5_ENSEMBLE_VARIANCE_2026-09-14.md` |
| `TASK_BRIEF_A8_QALY_PER_INFECTION_2026-09-14.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A8_QALY_PER_INFECTION_2026-09-14.md` |
| `TASK_BRIEF_A9_SWITCH_GRID_2026-09-13.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A9_SWITCH_GRID_2026-09-13.md` |
| `TASK_BRIEF_D14_BLOCK_GROUPING_2026-09-13.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_D14_BLOCK_GROUPING_2026-09-13.md` |
| `TASK_BRIEF_JENSEN_MECHANISM_2026-09-12.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_JENSEN_MECHANISM_2026-09-12.md` |
| `TASK_BRIEF_PROCESS_DECOMPOSITION_2026-09-12.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_PROCESS_DECOMPOSITION_2026-09-12.md` |
| `TASK_BRIEF_RESIDUAL_PLACEMENT_2026-09-12.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_RESIDUAL_PLACEMENT_2026-09-12.md` |
| `TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_AUDIT_2026-09-22.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_AUDIT_2026-09-22.md` |
| `TASK_BRIEF_STOCHASTIC_HEADLINE_2026-09-11.md` | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_STOCHASTIC_HEADLINE_2026-09-11.md` |

## Why other historical documents remain at the root

The historical documents still at the repository root are referenced by
source code, tests, or scientific configuration values. Moving them would
either break those references or require changing hashes used by preserved
runs. They therefore remain at their original paths with a prominent
`HISTORICAL SNAPSHOT` or `superseded` banner. This is intentional provenance
preservation, not an assertion that those documents are current.

## What was deliberately not done

- No Python module, active or lineage-bound scientific configuration, test, or
  artifact-catalog record was changed to accommodate a documentation move.
- No scientific result or output artifact was deleted or rewritten.
- No historical conclusion was promoted to a current conclusion.
- No redirect stub was left at either former path.
- No simulation was launched.
