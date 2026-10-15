#!/usr/bin/env python3
"""Build or verify the tracked catalog of top-level scientific outputs.

This tool is deliberately read-only with respect to ``outputs``.  It records
directory metadata and applies reviewable classifications from a tracked JSON
override file; it never moves, deletes, rewrites, or reseals an artifact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


ROOT_NAMES = ("calibration", "hta_results", "suites")
CATALOG_SCHEMA = "artifact_catalog/1.0.0"
ALLOWED_CLASSES = frozenset({
    "CURRENT_VALIDATED",
    "HISTORICAL_RETAINED",
    "SUPERSEDED",
    "FAILED_OR_INCOMPLETE",
    "PREPARED_NOT_RUN",
    "DERIVED_PRESENTATION",
    "DISPOSABLE_DUPLICATE",
    "REVIEW_REQUIRED",
})
TERMINAL_CLASSES = ALLOWED_CLASSES - {"REVIEW_REQUIRED"}
CATALOG_FIELDS = (
    "artifact_id",
    "original_path",
    "root",
    "name",
    "classification",
    "review_required",
    "scientific_status",
    "current_code_compatibility",
    "declared_status",
    "declared_stage",
    "classification_reason",
    "evidence_source",
    "superseded_by",
    "canonical_path",
    "logical_size_bytes",
    "file_count",
    "directory_count",
    "symlink_count",
    "modified_at_utc",
    "manifest_present",
    "manifest_count",
    "manifest_paths",
    "manifest_sha256s",
    "tracked_reference_count",
    "tracked_reference_files",
    "retention_rule",
)

DEFAULT_RETENTION = {
    "CURRENT_VALIDATED": "retain_read_only;eligible_for_current_claims",
    "HISTORICAL_RETAINED": "retain_read_only;exclude_from_current_claims",
    "SUPERSEDED": "retain_until_successor_and_restore_are_verified;then_archive",
    "FAILED_OR_INCOMPLETE": "retain_forensic;do_not_cite_as_scientific_result",
    "PREPARED_NOT_RUN": "retain_design_history;not_scientific_evidence",
    "DERIVED_PRESENTATION": "retain_with_named_parent",
    "DISPOSABLE_DUPLICATE": "remove_only_after_verified_canonical_copy_exists",
    "REVIEW_REQUIRED": "preserve_in_place;no_cleanup_until_terminal_review",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def top_level_directories(outputs_root: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    for root_name in ROOT_NAMES:
        root = outputs_root / root_name
        if not root.is_dir():
            raise FileNotFoundError(f"required output root is missing: {root}")
        rows.extend(
            (root_name, path)
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.is_dir()
        )
    return rows


def loose_root_files(outputs_root: Path) -> dict[str, list[str]]:
    return {
        root_name: sorted(
            path.name
            for path in (outputs_root / root_name).iterdir()
            if not path.is_dir()
        )
        for root_name in ROOT_NAMES
    }


def measure_directory(path: Path) -> dict[str, object]:
    logical_size = 0
    file_count = 0
    directory_count = 0
    symlink_count = 0
    newest_mtime = path.lstat().st_mtime
    manifests: list[Path] = []

    for current, directory_names, file_names in os.walk(path, followlinks=False):
        current_path = Path(current)
        traversable: list[str] = []
        for name in directory_names:
            child = current_path / name
            info = child.lstat()
            newest_mtime = max(newest_mtime, info.st_mtime)
            if stat.S_ISLNK(info.st_mode):
                symlink_count += 1
            else:
                directory_count += 1
                traversable.append(name)
        directory_names[:] = traversable

        for name in file_names:
            child = current_path / name
            info = child.lstat()
            newest_mtime = max(newest_mtime, info.st_mtime)
            file_count += 1
            logical_size += int(info.st_size)
            if stat.S_ISLNK(info.st_mode):
                symlink_count += 1
            if "manifest" in name.lower():
                manifests.append(child)

    manifest_paths = [str(item.relative_to(path)) for item in sorted(manifests)]
    manifest_hashes: list[str] = []
    for item in sorted(manifests):
        try:
            manifest_hashes.append(
                f"{item.relative_to(path)}={sha256_file(item)}"
            )
        except OSError:
            manifest_hashes.append(f"{item.relative_to(path)}=UNREADABLE")

    declared_status = ""
    declared_stage = ""
    top_manifest = path / "manifest.json"
    if top_manifest.is_file():
        try:
            payload = json.loads(top_manifest.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                declared_status = str(payload.get("status", "") or "")
                declared_stage = str(payload.get("stage", "") or "")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            declared_status = "UNREADABLE_MANIFEST"

    return {
        "logical_size_bytes": logical_size,
        "file_count": file_count,
        "directory_count": directory_count,
        "symlink_count": symlink_count,
        "modified_at_utc": datetime.fromtimestamp(
            newest_mtime, timezone.utc
        ).isoformat(),
        "manifest_present": bool(manifests),
        "manifest_count": len(manifests),
        "manifest_paths": ";".join(manifest_paths),
        "manifest_sha256s": ";".join(manifest_hashes),
        "declared_status": declared_status,
        "declared_stage": declared_stage,
    }


def tracked_text(repo_root: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    texts: list[tuple[str, str]] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        if relative.startswith("documentation/artifact_catalog/"):
            continue
        path = repo_root / relative
        try:
            if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        texts.append((relative, data.decode("utf-8", errors="ignore")))
    return texts


def references_for(paths: Iterable[str], texts: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    references = {path: [] for path in paths}
    for source, content in texts:
        for artifact_path in references:
            if artifact_path in content:
                references[artifact_path].append(source)
    return {key: sorted(set(value)) for key, value in references.items()}


def load_overrides(path: Path) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "artifact_catalog_overrides/1.0.0":
        raise ValueError(f"unsupported override schema in {path}")
    overrides = payload.get("overrides")
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object keyed by original path")
    return {str(key): dict(value) for key, value in overrides.items()}


def build_records(
    outputs_root: Path,
    repo_root: Path,
    overrides: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    directories = top_level_directories(outputs_root)
    paths = [f"outputs/{root_name}/{path.name}" for root_name, path in directories]
    references = references_for(paths, tracked_text(repo_root))
    unknown_overrides = sorted(set(overrides) - set(paths))
    if unknown_overrides:
        raise ValueError(f"overrides name absent artifacts: {unknown_overrides}")

    records: list[dict[str, object]] = []
    for (root_name, path), original_path in zip(directories, paths):
        measured = measure_directory(path)
        reference_files = references[original_path]
        override = dict(overrides.get(original_path, {}))
        classification = str(override.pop("classification", "REVIEW_REQUIRED"))
        if classification not in ALLOWED_CLASSES:
            raise ValueError(f"{original_path}: invalid classification {classification!r}")

        scientific_status = str(override.pop(
            "scientific_status",
            "CITED_BUT_NOT_REVALIDATED" if reference_files else "UNCLASSIFIED",
        ))
        reason = str(override.pop(
            "classification_reason",
            "No tracked terminal disposition or current-code compatibility evidence was "
            "found; preserve pending owner review.",
        ))
        record: dict[str, object] = {
            "artifact_id": f"artifact:{root_name}:{path.name}",
            "original_path": original_path,
            "root": root_name,
            "name": path.name,
            "classification": classification,
            "review_required": classification == "REVIEW_REQUIRED",
            "scientific_status": scientific_status,
            "current_code_compatibility": str(
                override.pop("current_code_compatibility", "UNKNOWN")
            ),
            "declared_status": measured["declared_status"],
            "declared_stage": measured["declared_stage"],
            "classification_reason": reason,
            "evidence_source": str(override.pop("evidence_source", "")),
            "superseded_by": str(override.pop("superseded_by", "")),
            "canonical_path": str(override.pop("canonical_path", "")),
            "logical_size_bytes": measured["logical_size_bytes"],
            "file_count": measured["file_count"],
            "directory_count": measured["directory_count"],
            "symlink_count": measured["symlink_count"],
            "manifest_present": measured["manifest_present"],
            "manifest_count": measured["manifest_count"],
            "manifest_paths": measured["manifest_paths"],
            "manifest_sha256s": measured["manifest_sha256s"],
            "tracked_reference_count": len(reference_files),
            "tracked_reference_files": ";".join(reference_files),
            "retention_rule": str(
                override.pop("retention_rule", DEFAULT_RETENTION[classification])
            ),
            "modified_at_utc": measured["modified_at_utc"],
        }
        if override:
            raise ValueError(
                f"{original_path}: unknown override fields {sorted(override)}"
            )
        records.append(record)
    return records


def csv_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def write_catalog(
    records: list[dict[str, object]],
    outputs_root: Path,
    catalog_json: Path,
    catalog_csv: Path,
) -> None:
    class_counts = Counter(str(record["classification"]) for record in records)
    root_counts = Counter(str(record["root"]) for record in records)
    payload = {
        "schema_version": CATALOG_SCHEMA,
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "logical_outputs_root": "outputs",
        "scope": [f"outputs/{name}/*/" for name in ROOT_NAMES],
        "record_count": len(records),
        "record_count_by_root": dict(sorted(root_counts.items())),
        "classification_counts": dict(sorted(class_counts.items())),
        "remaining_review_count": class_counts.get("REVIEW_REQUIRED", 0),
        "terminally_classified_count": sum(
            count for name, count in class_counts.items() if name in TERMINAL_CLASSES
        ),
        "loose_root_files": loose_root_files(outputs_root),
        "records": records,
    }
    catalog_json.parent.mkdir(parents=True, exist_ok=True)
    catalog_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with catalog_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CATALOG_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        for record in records:
            writer.writerow({field: csv_value(record.get(field, "")) for field in CATALOG_FIELDS})


def verify_catalog(
    outputs_root: Path,
    catalog_json: Path,
    catalog_csv: Path,
    overrides: Mapping[str, Mapping[str, object]],
) -> None:
    payload = json.loads(catalog_json.read_text(encoding="utf-8"))
    if payload.get("schema_version") != CATALOG_SCHEMA:
        raise ValueError("catalog JSON has an unsupported schema")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("catalog JSON records must be a list")
    paths = [str(record["original_path"]) for record in records]
    if len(paths) != len(set(paths)):
        raise ValueError("catalog contains duplicate original paths")
    live_paths = {
        f"outputs/{root_name}/{path.name}"
        for root_name, path in top_level_directories(outputs_root)
    }
    catalog_paths = set(paths)
    missing = sorted(live_paths - catalog_paths)
    extra = sorted(catalog_paths - live_paths)
    if missing or extra:
        raise ValueError(f"catalog coverage mismatch; missing={missing}; extra={extra}")
    if set(overrides) - catalog_paths:
        raise ValueError("an override has no catalog record")

    by_path = {str(record["original_path"]): record for record in records}
    for original_path, record in by_path.items():
        classification = str(record["classification"])
        if classification not in ALLOWED_CLASSES:
            raise ValueError(f"{original_path}: invalid classification {classification}")
        expected_review = classification == "REVIEW_REQUIRED"
        if bool(record["review_required"]) != expected_review:
            raise ValueError(f"{original_path}: review_required disagrees with classification")

    with catalog_csv.open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    if set(CATALOG_FIELDS) != set(csv_rows[0] if csv_rows else CATALOG_FIELDS):
        raise ValueError("catalog CSV columns differ from the declared schema")
    csv_by_path = {row["original_path"]: row for row in csv_rows}
    if set(csv_by_path) != catalog_paths or len(csv_rows) != len(records):
        raise ValueError("catalog CSV and JSON cover different artifacts")
    for original_path, record in by_path.items():
        csv_record = csv_by_path[original_path]
        for field in CATALOG_FIELDS:
            if csv_record[field] != csv_value(record.get(field, "")):
                raise ValueError(
                    f"{original_path}: CSV/JSON mismatch in {field}: "
                    f"{csv_record[field]!r} != {record.get(field)!r}"
                )

    counts = Counter(str(record["classification"]) for record in records)
    if int(payload["record_count"]) != len(records):
        raise ValueError("record_count does not match records")
    if int(payload["remaining_review_count"]) != counts.get("REVIEW_REQUIRED", 0):
        raise ValueError("remaining_review_count does not match records")
    print(
        f"COVERAGE OK: {len(records)} top-level directories; "
        f"{counts.get('REVIEW_REQUIRED', 0)} REVIEW_REQUIRED; "
        f"{len(records) - counts.get('REVIEW_REQUIRED', 0)} terminally classified"
    )


def parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    default_dir = repo_root / "documentation" / "artifact_catalog"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--outputs-root", required=True, type=Path)
    result.add_argument("--overrides", type=Path, default=default_dir / "catalog_overrides.json")
    result.add_argument("--catalog-json", type=Path, default=default_dir / "artifact_catalog.json")
    result.add_argument("--catalog-csv", type=Path, default=default_dir / "artifact_catalog.csv")
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    outputs_root = args.outputs_root.resolve()
    overrides = load_overrides(args.overrides)
    if args.write:
        records = build_records(outputs_root, repo_root, overrides)
        write_catalog(records, outputs_root, args.catalog_json, args.catalog_csv)
    verify_catalog(outputs_root, args.catalog_json, args.catalog_csv, overrides)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
