# Historical 2008 calibration example

This small, tracked extract lets the workbench open a real result immediately.
It contains candidate 1787 from the confirmed 2008-break calibration run and
the exact selected model configuration that was bound to that result.

The candidate passed the recorded primary confirmation rule across 20 seeds
(`I_max = 2.5385477355491948`). It is retained evidence for the 2026-09-29
conference-suite lineage. It is **not** current Option-B production evidence,
and the selected configuration is not accepted by the current `run_model.py`
canonical-config contract. Use it to inspect and explain the recorded result;
do not present a current-code rerun as a reproduction of this artifact.

Files:

- `candidate_1787.json`: per-seed annual model outputs, fitted parameters,
  target order, target matrix, means, and sample variances.
- `confirmation_summary.csv`: confirmation metrics for the shortlist.
- `manifest.json`: the original confirmation-stage manifest.
- `selected_model_break_2008.json`: the exact bound model configuration.
- `provenance.json`: source archive, lineage status, and file hashes.
- `target_fit_candidate_1787.csv`: observed-versus-simulated values for the 13
  calibration targets used by the recorded confirmation rule.
- `TARGET_FIT_PROVENANCE.md`: derivation and interpretation boundary for that
  report-only target-fit extract.

The complete retained archive remains outside Git because its compressed size
is about 294 MB and its extracted calibration tree is several GB. This extract
is sufficient for the workbench's built-in example; the full archive remains
the authority for the complete run.
