"""Static integrity tests for the tracked scientific-output catalog."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest


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
    assert catalog["remaining_review_count"] == counts["REVIEW_REQUIRED"] == 98
    assert catalog["terminally_classified_count"] == len(records) - 98


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


def test_stale_readiness_lineages_are_explicitly_incompatible(
    catalog: dict[str, object],
) -> None:
    stale = [
        record
        for record in catalog["records"]
        if record["classification"] == "HISTORICAL_RETAINED"
    ]
    assert len(stale) == 12
    assert all(record["current_code_compatibility"] == "INCOMPATIBLE" for record in stale)
    assert all(
        record["scientific_status"] == "HISTORICAL_ONLY_RETIRED_FROM_CURRENT_CLAIMS"
        for record in stale
    )
