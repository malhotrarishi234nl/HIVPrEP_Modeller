# External-input and moved-file reconciliation

Checked 2026-10-15. No substitute data were manufactured and no archived file
was copied over a current source.

| Expected old path | Located source | SHA-256 | Classification | Resolution |
|---|---|---|---|---|
| `Validation /HIV Data UNAIDS/ZIP Files/netherlands_estimates_2025.csv` | `../data/raw/validation/HIV Data UNAIDS/ZIP Files/netherlands_estimates_2025.csv` | `e5104b6f89c47d65687e42bcf00c2f33d0503f7b22ac0ee869bcb96d6a8e5c56` | Available elsewhere; intentionally external/raw | `calib_data_loader.default_unaids_path` now follows the sibling-data contract documented in `.gitignore`. |
| `Validation /HMD_NL_Pop.txt` | `../data/raw/validation/HMD_NL_Pop.txt` | `17461a86c04bdebcb0ffbb1a1ea58fb1fee6044bf1425ed54ac902199f517c18` | Available elsewhere; intentionally external/raw | Default loader and diagnostic paths now use the canonical sibling store. |
| `Validation /Migration_Net.csv` | `../data/raw/validation/Migration_Net.csv` | `4caff391be60aec230f6131a3c0716825fbdd5cbabc7b3a59c52f10811d4636a` | Available elsewhere; intentionally external/raw | Default loader and demographic-audit paths now use the canonical sibling store. |
| target-register retained candidate | `outputs/calibration/rivm_aids_vintage_audit_20260916/candidate_newest_vetted_20260916.json` | `16be90cb791f02e5e69f4d7836ee1f0c0ffa8dff9d57c28d8b494a6923e12f03` (52,967 bytes) | Available in the ignored artifact mount; intentionally absent from a source-only clone | Declared in `data/external_inputs.json`. Acceptance-input and placeholder-readiness tests run only when this exact dependency and `DiseaseData.xlsx` are both mounted; absence yields an explicit external-data skip, never a pass. |
| root `1980_test.json` | `configs/1980_test.json` | `936dc7d1a50cc4107884715f62a0b83ee5af33afc925f1234387358658b9c1fe` | Available in repository; tracked at intended moved location | Current runner defaults and tests now use `configs/1980_test.json`. Archive copies differ and were not restored. |
| `documentation/BACKLOG.md` | Three preserved copies outside current Git history | See `documentation/BACKLOG.md` | Available externally, but authoritative edition unresolved; not recoverable from this repository's Git history | Added a provenance pointer containing the verified `BACKLOG-25` entry. Selecting/restoring a full edition remains a documentation-owner decision. |
| diagnosis-triangulation pinned PDFs | `../literature/` (11 exact pinned files) | Existing per-file pins in `run_diagnosis_triangulation_orchestration.py` all pass | Available elsewhere; intentionally external literature library | No code change. The isolated clone was given read-only access to the canonical sibling root; module result: 46 passed. |

## Verification

Final accepted commit:
`68112bba96402dbbf43c4456289f4384b9635cbe`, tree
`bcc902476f507d745765e9d9835e341651e70d21`.

With the canonical sibling inputs and historical artifact mount available:

```text
.venv/bin/python -m pytest -p pytest_no_engine_guard -W error -q
3174 passed, 288 skipped, 0 failed/errors in 471.17s; exit 0
```

No engine was constructed. This is the complete mounted-evidence guarded lane.

The verified code bundle was restored to the clean source-only clone
`/private/tmp/hiv-prep-handoff.Feb1Qm/repo`. It reproduced the exact commit,
had clean status, passed strict `git fsck`, and contained no tracked symlinks,
`.venv`, `node_modules`, or `outputs`. M0 and M1-at-p=0 validate-only passed
without constructing an engine, the config registry reported 78 OK, and the
robust validator reproduced the exact 45-decision refusal.

The source-only full suite is deliberately reported separately:

```text
3019 passed, 327 skipped, 39 failed, 77 errors in 232.99s
```

It is **not** a green lane. The omitted evidence includes, at minimum:

- `../data/raw/validation/DiseaseData.xlsx`;
- `outputs/calibration/rivm_aids_vintage_audit_20260916/`
  `candidate_newest_vetted_20260916.json`;
- historical coupled smoke and calibration trees such as
  `outputs/calibration/coupled_20260918_smoke/` and
  `outputs/calibration/hm_focused_diagnoses_then_aids_20260916/`;
- `outputs/abstract_evidence_20260923/` and the historical matched-deployment
  calibration evidence;
- retained HTA audit trees under `outputs/hta_results/`; and
- prepared historical suite artifacts under `outputs/suites/`.

These are evidence/data dependencies, not source files to copy into Git. Mount
and hash-check the exact sources before interpreting their dependent tests.
The collaborator source-code handoff is complete because ordinary M0/M1 use is
source-only after environment installation; claims about historical or
external-data evidence remain conditional on the corresponding mount.

Environment lock hashes used for the handoff are:

- `requirements.txt`:
  `3a7ba47591451816350c1e4fb81e6e0e60dcf800bbdbb971eb43df01f2feaa74`;
- `requirements-test.txt`:
  `8bacaf9e55cd3c00dea363cc3fe4b3a0e752c8b81b67c91e75c6a6770da7a26f`.
