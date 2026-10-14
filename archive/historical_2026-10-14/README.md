# Historical / superseded material — archived 2026-10-14

Moved here during the bounded repository-hygiene pass required by
`documentation/reports/calibration/PRODUCTION_LOCKIN_SPEC_2026-10-13.md` section 4.

**Nothing here was deleted.** Everything is recoverable from git history, and
the files are preserved verbatim at their new paths.

The pass was deliberately conservative. The specification states that "age or
filename alone is never sufficient evidence that a file is stale", and that
"ambiguous or still-referenced files remain in place and are listed for
review". A reference scan over every top-level module found exactly one file
with **zero** inbound references from any script, test, config, manifest or
document, so only that file and one empty leftover were moved.

| file | why it is demonstrably superseded |
|---|---|
| `fix_thesis.py` | One-off script that rewrote `THESIS_FINAL.docx`. It hardcodes `/sessions/nifty-vigilant-mayer/mnt/Modeller/v11/THESIS_FINAL.docx`, an absolute path from a different sandbox environment that does not exist on this machine, so the script cannot run as written. Zero inbound references. Unrelated to the simulation model, calibration or economics. |
| `__delete_test.txt` | Zero-byte file named `__delete_test`, present since the initial commit. A leftover from a filesystem check; no content and no references. |

## What was deliberately NOT moved

Every other candidate was left in place and is listed in
`documentation/guides/REPOSITORY_INVENTORY_2026-10-14.md`. In particular the
dated one-off analysis runners (`run_*_20260917.py`, `run_*_20260922.py`,
`run_followup_knot_and_late_dx_20260929.py`, and the `tools/a*.py` audit
scripts) all retain inbound references from tests, documentation or output
manifests, and several are the provenance for results cited in the reports.
They are exploratory in character but **not** demonstrably superseded, so
moving them would have risked breaking a reference or losing provenance for a
cited result.
