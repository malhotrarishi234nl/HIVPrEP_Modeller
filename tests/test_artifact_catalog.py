"""Static integrity tests for the tracked scientific-output catalog."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from tools.build_artifact_catalog import CATALOG_FIELDS, verify_live_records


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "documentation" / "artifact_catalog"
ALLOWED_CLASSES = {
    "CURRENT_VALIDATED",
    "HISTORICAL_RETAINED",
    "SUPERSEDED",
    "FAILED_OR_INCOMPLETE",
    "PREPARED_NOT_RUN",
    "DERIVED_PRESENTATION",
    "DISPOSABLE_DUPLICATE",
    "REVIEW_REQUIRED",
}


@pytest.fixture(scope="module")
def catalog() -> dict[str, object]:
    return json.loads((CATALOG_DIR / "artifact_catalog.json").read_text())


def test_catalog_summary_and_unique_scope(catalog: dict[str, object]) -> None:
    records = catalog["records"]
    assert catalog["schema_version"] == "artifact_catalog/1.0.0"
    assert catalog["record_count"] == len(records) == 132
    assert catalog["record_count_by_root"] == {
        "calibration": 78,
        "hta_results": 44,
        "suites": 10,
    }

    paths = [record["original_path"] for record in records]
    assert len(paths) == len(set(paths))
    assert all(
        path.startswith(
            ("outputs/calibration/", "outputs/hta_results/", "outputs/suites/")
        )
        for path in paths
    )

    counts = Counter(record["classification"] for record in records)
    assert set(counts) <= ALLOWED_CLASSES
    assert dict(sorted(counts.items())) == catalog["classification_counts"]
    assert catalog["remaining_review_count"] == counts["REVIEW_REQUIRED"] == 9
    assert catalog["terminally_classified_count"] == len(records) - 9 == 123


def test_every_record_is_safe_and_reviewable(catalog: dict[str, object]) -> None:
    for record in catalog["records"]:
        needs_review = record["classification"] == "REVIEW_REQUIRED"
        assert record["review_required"] is needs_review
        assert record["classification_reason"].strip()
        assert record["retention_rule"].strip()
        assert record["logical_size_bytes"] >= 0
        assert record["file_count"] >= 0
        assert record["manifest_count"] >= 0
        assert record["manifest_present"] is (record["manifest_count"] > 0)


def test_csv_and_json_have_identical_paths_and_classes(
    catalog: dict[str, object],
) -> None:
    with (CATALOG_DIR / "artifact_catalog.csv").open(newline="") as handle:
        csv_records = list(csv.DictReader(handle))

    json_by_path = {
        record["original_path"]: record["classification"]
        for record in catalog["records"]
    }
    csv_by_path = {
        record["original_path"]: record["classification"] for record in csv_records
    }
    assert csv_by_path == json_by_path


def test_all_explicit_overrides_are_present_and_applied(
    catalog: dict[str, object],
) -> None:
    overrides = json.loads(
        (CATALOG_DIR / "catalog_overrides.json").read_text()
    )["overrides"]
    by_path = {record["original_path"]: record for record in catalog["records"]}

    assert set(overrides) <= set(by_path)
    for path, override in overrides.items():
        assert by_path[path]["classification"] == override["classification"]
        assert by_path[path]["classification_reason"] == override["classification_reason"]
        assert by_path[path]["evidence_source"] == override["evidence_source"]


def test_historical_rows_never_claim_current_compatibility_or_validation(
    catalog: dict[str, object],
) -> None:
    historical = [
        record
        for record in catalog["records"]
        if record["classification"] == "HISTORICAL_RETAINED"
    ]
    assert len(historical) == 78
    assert all(
        record["current_code_compatibility"] in {"INCOMPATIBLE", "UNKNOWN", "NOT_APPLICABLE"}
        for record in historical
    )
    assert all(record["scientific_status"] != "CURRENT_VALIDATED" for record in historical)


def test_terminal_rows_have_evidence_and_required_relationships(
    catalog: dict[str, object],
) -> None:
    terminal = [
        record
        for record in catalog["records"]
        if record["classification"] != "REVIEW_REQUIRED"
    ]
    assert len(terminal) == 123
    assert all(record["evidence_source"].strip() for record in terminal)
    assert all(
        record["superseded_by"].strip()
        for record in terminal
        if record["classification"] == "SUPERSEDED"
    )
    assert all(
        record["canonical_path"].strip()
        for record in terminal
        if record["classification"] == "DERIVED_PRESENTATION"
    )


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("logical_size_bytes", 101),
        ("file_count", 2),
        ("manifest_sha256s", "manifest.json=changed"),
        ("classification", "SUPERSEDED"),
    ],
)
def test_live_snapshot_verification_rejects_drift(
    field: str,
    changed_value: object,
) -> None:
    catalog_record = {name: "" for name in CATALOG_FIELDS}
    catalog_record.update(
        original_path="outputs/calibration/example",
        logical_size_bytes=100,
        file_count=1,
        manifest_sha256s="manifest.json=original",
        classification="REVIEW_REQUIRED",
    )
    live_record = dict(catalog_record)
    live_record[field] = changed_value

    with pytest.raises(ValueError, match=field):
        verify_live_records([catalog_record], [live_record])


def test_live_snapshot_verification_accepts_exact_match() -> None:
    record = {name: "" for name in CATALOG_FIELDS}
    record["original_path"] = "outputs/suites/example"
    verify_live_records([record], [dict(record)])
