# External-input and moved-file reconciliation

Checked 2026-10-15. No substitute data were manufactured and no archived file
was copied over a current source.

| Expected old path | Located source | SHA-256 | Classification | Resolution |
|---|---|---|---|---|
| `Validation /HIV Data UNAIDS/ZIP Files/netherlands_estimates_2025.csv` | `../data/raw/validation/HIV Data UNAIDS/ZIP Files/netherlands_estimates_2025.csv` | `e5104b6f89c47d65687e42bcf00c2f33d0503f7b22ac0ee869bcb96d6a8e5c56` | Available elsewhere; intentionally external/raw | `calib_data_loader.default_unaids_path` now follows the sibling-data contract documented in `.gitignore`. |
| `Validation /HMD_NL_Pop.txt` | `../data/raw/validation/HMD_NL_Pop.txt` | `17461a86c04bdebcb0ffbb1a1ea58fb1fee6044bf1425ed54ac902199f517c18` | Available elsewhere; intentionally external/raw | Default loader and diagnostic paths now use the canonical sibling store. |
| `Validation /Migration_Net.csv` | `../data/raw/validation/Migration_Net.csv` | `4caff391be60aec230f6131a3c0716825fbdd5cbabc7b3a59c52f10811d4636a` | Available elsewhere; intentionally external/raw | Default loader and demographic-audit paths now use the canonical sibling store. |
| root `1980_test.json` | `configs/1980_test.json` | `936dc7d1a50cc4107884715f62a0b83ee5af33afc925f1234387358658b9c1fe` | Available in repository; tracked at intended moved location | Current runner defaults and tests now use `configs/1980_test.json`. Archive copies differ and were not restored. |
| `documentation/BACKLOG.md` | Three preserved copies outside current Git history | See `documentation/BACKLOG.md` | Available externally, but authoritative edition unresolved; not recoverable from this repository's Git history | Added a provenance pointer containing the verified `BACKLOG-25` entry. Selecting/restoring a full edition remains a documentation-owner decision. |
| diagnosis-triangulation pinned PDFs | `../literature/` (11 exact pinned files) | Existing per-file pins in `run_diagnosis_triangulation_orchestration.py` all pass | Available elsewhere; intentionally external literature library | No code change. The isolated clone was given read-only access to the canonical sibling root; module result: 46 passed. |

## Verification

In a `git clone --no-hardlinks` snapshot with the current dirty source overlaid:

```text
.venv/bin/python -m pytest -p pytest_no_engine_guard \
  tests/test_lhs_calibration.py \
  tests/test_validation_data_loader.py \
  tests/test_orientation_seed_stage_distribution.py \
  tests/test_non_msm_diagnosis_pipeline_audit.py -q

60 passed, 14 skipped
```

The skips are the no-engine guard doing its job; no production or identity
trajectory ran.

The final guarded repository-wide result with all canonical sibling inputs
available was `2589 passed, 29 failed, 284 skipped` with no errors. The 29
failures are the stale historical lineages itemized in
`HISTORICAL_ARTIFACT_DISPOSITION.md`.
