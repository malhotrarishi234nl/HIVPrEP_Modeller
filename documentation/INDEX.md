# Documentation index

## Current authority

- Bounded Option-B research-E2E route, completed rehearsal result and report
  interpretation:
  `guides/OPTION_B_RESEARCH_E2E_20261015.md`
- Production-readiness status and evidence:
  `reports/calibration/robust_nroy_readiness_2026-10-15/README.md`
- Requirement-by-requirement completion evidence and exact open conditions:
  `reports/calibration/robust_nroy_readiness_2026-10-15/PRODUCTION_READINESS_CLOSURE_MATRIX.md`
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
- Local browser workbench, setup, supported actions, result inspection, and
  first-version boundary: `guides/LOCAL_MODEL_WORKBENCH.md`
- Repository/artifact handoff cleanup:
  `reports/calibration/robust_nroy_readiness_2026-10-15/REPOSITORY_HANDOFF_CLEANUP_PLAN.md`
- Detailed remediation evidence:
  `reports/calibration/NROY_REMEDIATION_2026-10-14.md`
- Testing lanes and interpretation: `guides/TESTING.md`
- Optional environment-specific workbook builders:
  `guides/OPTIONAL_ARTIFACT_BUILDERS.md`
- Configuration guide: `../configs/README.md`
- Complete tracked configuration inventory and interpretation:
  `../CONFIG_REGISTRY.csv` and `guides/CONFIGURATION_REGISTRY.md`
- Current scientific artifact-vault location, verification and restoration:
  `guides/ARTIFACT_STORAGE_20261016.md`
- Workspace-level directory authority, retention lifecycle, and anti-bloat
  rules: `../../README.md`
- External-input contract: `../data/README.md`

## Historical guidance

Historical task briefs, handoffs, and runbooks are preserved under
`archive/historical_workstreams/`. Its README provides an exact
old-path-to-new-path map for frozen catalog and seed-audit references. The few
historical documents still at the repository root are retained there because
source code, tests, or scientific configuration values cite those exact paths.

Legacy version-specific model and GitHub guides are preserved under
`archive/guides/legacy_versions/`. They document earlier versions and must not
be used as current operating instructions.

`guides/HISTORY_MATCHING_GUIDE.md` is the historical operating guide for the
2026-09-15 19-target learning-wave workflow. It remains useful for interpreting
those artifacts, but it is not the current Option B/robust-NROY calibration
contract.

`BACKLOG.md` is a provenance pointer to preserved historical backlog editions;
it is not a reconstructed claim that every old backlog item remains current.

The completed bounded Option-B rehearsal does not promote older calibration
artifacts or supersede their recorded provenance. Its guide is current because
it records both the reusable research-only route and the exact 2026-10-15
outcome.

## Document status rule

New status documents should state one of `CURRENT`, `HISTORICAL SNAPSHOT`, or
`SUPERSEDED BY <path>` and identify the code/config/artifact snapshot they
describe when relevant.
