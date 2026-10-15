# Documentation index

## Current authority

- Production-readiness status and evidence:
  `reports/calibration/robust_nroy_readiness_2026-10-15/README.md`
- Production-backbone evidence and the selected Option B 1980 recalibration direction:
  `reports/calibration/robust_nroy_readiness_2026-10-15/PRODUCTION_BACKBONE_RECONCILIATION.md`
- Ordered launch gates:
  `reports/calibration/robust_nroy_readiness_2026-10-15/GATED_EXECUTION_CHECKLIST.md`
- Owner decisions, assumptions, and methods reading:
  `reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_LOCK_IN_AND_METHODS_GUIDE.md`
- Owner packet for all 45 unsigned robust-NROY rows, grouped by evidence gate:
  `reports/calibration/robust_nroy_readiness_2026-10-15/OWNER_DECISION_PACKET.md`
- Collaborator-facing M0/M1 commands and protected parameter boundary:
  `reports/calibration/robust_nroy_readiness_2026-10-15/MODEL_OPERATOR_GUIDE.md`
- Repository/artifact handoff cleanup:
  `reports/calibration/robust_nroy_readiness_2026-10-15/REPOSITORY_HANDOFF_CLEANUP_PLAN.md`
- Detailed remediation evidence:
  `reports/calibration/NROY_REMEDIATION_2026-10-14.md`
- History-matching operating guide: `guides/HISTORY_MATCHING_GUIDE.md`
- Testing lanes and interpretation: `guides/TESTING.md`
- Optional environment-specific workbook builders:
  `guides/OPTIONAL_ARTIFACT_BUILDERS.md`
- Configuration guide: `../configs/README.md`
- Complete tracked configuration inventory and interpretation:
  `../CONFIG_REGISTRY.csv` and `guides/CONFIGURATION_REGISTRY.md`
- External-input contract: `../data/README.md`

## Historical guidance

Root-level `TASK_BRIEF_*.md`, dated handoffs, and runbooks are preserved because
scientific artifacts cite them. Treat them as historical snapshots unless a
current-authority document above explicitly adopts them.

`BACKLOG.md` is a provenance pointer to preserved historical backlog editions;
it is not a reconstructed claim that every old backlog item remains current.

## Document status rule

New status documents should state one of `CURRENT`, `HISTORICAL SNAPSHOT`, or
`SUPERSEDED BY <path>` and identify the code/config/artifact snapshot they
describe when relevant.
