from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = (
    ROOT
    / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15"
    / "PRODUCTION_BACKBONE_RECONCILIATION.md"
)

TRACKED_EVIDENCE = {
    "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json": (
        "c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187"
    ),
    "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json": (
        "fd50dd1a98965c9ea6b946c634060b0bd8ddac9334eb62ffc8d9cac67a63e7cf"
    ),
    "configs/calib_space_v13_25_unordered_orientation_20260921.json": (
        "82970c6d542ea7319ba9cc807ea221bb43f4558afdc5d8abb37e96d649649a12"
    ),
    "configs/1980_test.json": (
        "936dc7d1a50cc4107884715f62a0b83ee5af33afc925f1234387358658b9c1fe"
    ),
    "configs/model_final.json": (
        "2aa27627379018d6b0f8dcd708e241fff24483d06b211ff2b1bac2b96e9b3540"
    ),
}

BACKLINKS = [
    ROOT / "documentation/INDEX.md",
    ROOT
    / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15"
    / "README.md",
    ROOT
    / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15"
    / "GATED_EXECUTION_CHECKLIST.md",
    ROOT
    / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15"
    / "PRODUCTION_CONFIG_ASSESSMENT.md",
]


def test_production_backbone_report_references_current_tracked_evidence() -> None:
    report = REPORT.read_text(encoding="utf-8")

    for relative_path, expected_sha256 in TRACKED_EVIDENCE.items():
        evidence_path = ROOT / relative_path
        assert evidence_path.is_file(), relative_path
        assert relative_path in report
        assert expected_sha256 in report
        actual_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256

    for backlink in BACKLINKS:
        assert "PRODUCTION_BACKBONE_RECONCILIATION.md" in backlink.read_text(
            encoding="utf-8"
        )
