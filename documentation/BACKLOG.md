# Legacy backlog location and recovery note

The original full backlog is not present in this repository's Git history.
Three preserved workspace copies were found during the robust-NROY readiness
reconciliation on 2026-10-15:

| Copy | SHA-256 | Status |
|---|---|---|
| `../documentation-extra/v11_documentation/BACKLOG.md` | `9f9ff4b11099e57f8dbcae6d3ac6f6df0dce767a7787957b6b92457d9bdf2467` | Newest preserved copy; 2026-06-04 |
| `../archive/v13.20/documentation/BACKLOG.md` | `92a79209aa44415f7dd4ec0e1036dca736b061fb1c5b3a5d4f8709de03462d5e` | Historical v13.20 copy; 2026-05-23 |
| `../archive/v12.19/documentation/BACKLOG.md` | `a0f76f52dca1bbd43919e74f4f41c41b11806b546ec57e62487b4d4174b4d853` | Historical v12.19 copy; 2026-05-11 |

This file is intentionally a provenance pointer, not a silently reconstructed
full register. Selecting one preserved edition as the new authoritative backlog
requires a documentation-owner decision.

## Recovered entry needed by current configuration history

The newest preserved copy contains the following entry:

> **BACKLOG-25 (cleanup):** Remove deprecated `scenario_b_targeted` from
> `hta_config.json` once all existing run outputs referencing it have been
> archived.

Status on 2026-10-15: unresolved. The deprecated scenario remains documented as
superseded for backward compatibility; this readiness task did not remove it.
