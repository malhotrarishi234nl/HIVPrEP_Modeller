"""No-engine tests for the retained conference artifact-vault contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.artifact_vault import (
    ArtifactVaultError,
    CONFERENCE_ARTIFACT_ENV,
    CONFERENCE_ARTIFACT_ID,
    conference_artifact_uri,
    configured_conference_artifact_root,
    load_conference_manifest,
    require_conference_artifact_subpath,
)


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_internally_consistent_and_fail_closed() -> None:
    manifest = load_conference_manifest(ROOT)
    assert manifest["artifact_id"] == CONFERENCE_ARTIFACT_ID
    assert manifest["classification"] == "HISTORICAL_RETAINED"
    assert "NOT_CURRENT_OPTION_B" in manifest["scientific_status"]
    assert manifest["source_only_collaborator_handoff_required"] is False
    assert manifest["full_artifact_vault_handoff_required"] is True

    canonical = manifest["canonical_archive_set"]
    archives = canonical["archives"]
    assert canonical["external_location"] is None
    assert canonical["external_location_status"] == "LOCATION_REQUIRED"
    assert sum(item["bytes"] for item in archives) == canonical["archive_payload_bytes"]
    assert (
        canonical["archive_payload_bytes"]
        + canonical["archive_checksum_manifest"]["bytes"]
        == canonical["archive_set_bytes_including_checksum_manifest"]
    )

    restore = manifest["restore_contract"]
    assert sum(item["extracted_file_count"] for item in archives) == restore["expected_file_count"]
    assert sum(item["extracted_logical_bytes"] for item in archives) == restore["expected_logical_bytes"]
    assert restore["implicit_download_or_extraction"] is False
    assert restore["expected_symlink_count"] == 0

    by_name = {item["name"]: item for item in archives}
    duplicates = manifest["disposition"]["exact_disposable_duplicates"]
    assert len(duplicates) == 3
    assert sum(item["bytes"] for item in duplicates) == manifest["disposition"]["exact_duplicate_total_bytes"]
    for duplicate in duplicates:
        canonical_item = by_name[duplicate["canonical_name"]]
        assert duplicate["classification"] == "DISPOSABLE_DUPLICATE"
        assert duplicate["sha256"] == canonical_item["sha256"]
        assert duplicate["bytes"] == canonical_item["bytes"]

    superseded = manifest["disposition"]["superseded_not_duplicate"]
    assert superseded["classification"] == "SUPERSEDED"
    assert superseded["sha256"] != by_name["provenance_config_final.tgz"]["sha256"]
    assert "nine absent AppleDouble" in superseded["warning"]

    gates = manifest["cleanup_gates"]
    assert gates["consumer_paths_migrated"] is True
    assert gates["canonical_external_location_recorded"] is False
    assert gates["backup_policy_approved"] is False
    assert gates["owner_cleanup_approved"] is False
    assert gates["destructive_cleanup_authorized"] is False


def test_legacy_default_is_explicit_but_not_embedded_in_consumers() -> None:
    manifest = load_conference_manifest(ROOT)
    legacy = manifest["restore_contract"]["legacy_default_relative_path"]
    root, source = configured_conference_artifact_root(ROOT, {})
    assert root == ROOT / legacy
    assert source == "legacy_default"

    for relative in (
        "tools/build_professor_packet_20260929.py",
        "run_followup_knot_and_late_dx_20260929.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert legacy not in text
        assert "require_conference_artifact_subpath" in text


def test_configured_absolute_root_resolves_required_subpath(tmp_path: Path) -> None:
    required_relative = Path("outputs/suites/example")
    required = tmp_path / required_relative
    required.mkdir(parents=True)

    environ = {CONFERENCE_ARTIFACT_ENV: str(tmp_path)}
    root, source = configured_conference_artifact_root(ROOT, environ)
    assert root == tmp_path
    assert source == CONFERENCE_ARTIFACT_ENV
    assert require_conference_artifact_subpath(ROOT, required_relative, environ) == required


def test_missing_restore_fails_clearly_without_creating_it(tmp_path: Path) -> None:
    required_relative = Path("outputs/suites/missing")
    environ = {CONFERENCE_ARTIFACT_ENV: str(tmp_path)}
    with pytest.raises(
        ArtifactVaultError,
        match=r"conference_scaleway_20260929.*not restored.*No archive download or extraction",
    ):
        require_conference_artifact_subpath(ROOT, required_relative, environ)
    assert not (tmp_path / required_relative).exists()


def test_relative_root_and_unsafe_subpaths_are_rejected() -> None:
    with pytest.raises(ArtifactVaultError, match="must be an absolute path"):
        configured_conference_artifact_root(
            ROOT, {CONFERENCE_ARTIFACT_ENV: "relative/artifact"}
        )
    for unsafe in ("../escape", "/absolute/path"):
        with pytest.raises(ArtifactVaultError, match="safe relative path"):
            conference_artifact_uri(unsafe)


def test_stable_uri_does_not_expose_machine_path() -> None:
    uri = conference_artifact_uri("outputs/suites/example/result.csv")
    assert uri == (
        "artifact://conference_scaleway_20260929/"
        "outputs/suites/example/result.csv"
    )
    assert str(ROOT) not in uri
