"""Resolve retained artifact restores without downloading or extracting them.

The source-only collaborator workflow must not depend on the historical artifact
vault.  Historical consumers call this module explicitly and fail with a
restoration instruction when their retained input is unavailable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Mapping


CONFERENCE_ARTIFACT_ID = "conference_scaleway_20260929"
CONFERENCE_ARTIFACT_ENV = "HIV_PREP_ARTIFACT_ROOT"
CONFERENCE_MANIFEST_RELATIVE = Path(
    "documentation/artifact_catalog/vault_conference_scaleway_20260929.json"
)


class ArtifactVaultError(RuntimeError):
    """A retained artifact is unavailable or its tracked contract is invalid."""


def load_conference_manifest(repository_root: Path) -> dict[str, object]:
    """Load the tracked vault contract and verify its identifying fields."""

    manifest_path = Path(repository_root) / CONFERENCE_MANIFEST_RELATIVE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactVaultError(
            f"cannot load artifact-vault manifest {manifest_path}: {exc}"
        ) from exc
    if manifest.get("schema_version") != "artifact_vault_manifest/1.0.0":
        raise ArtifactVaultError(
            f"unsupported artifact-vault manifest schema in {manifest_path}"
        )
    if manifest.get("artifact_id") != CONFERENCE_ARTIFACT_ID:
        raise ArtifactVaultError(
            f"artifact-vault manifest {manifest_path} does not describe "
            f"{CONFERENCE_ARTIFACT_ID}"
        )
    return manifest


def configured_conference_artifact_root(
    repository_root: Path,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, str]:
    """Return the configured extracted root and how it was selected.

    ``HIV_PREP_ARTIFACT_ROOT`` points to the extracted artifact root containing
    ``outputs/`` and ``provenance_config_final/``.  When unset, the historical
    in-repository restore location remains an explicit legacy default.
    """

    manifest = load_conference_manifest(Path(repository_root))
    env = os.environ if environ is None else environ
    configured = str(env.get(CONFERENCE_ARTIFACT_ENV, "")).strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise ArtifactVaultError(
                f"{CONFERENCE_ARTIFACT_ENV} must be an absolute path to the "
                f"extracted {CONFERENCE_ARTIFACT_ID} artifact root"
            )
        return root, CONFERENCE_ARTIFACT_ENV

    restore = manifest.get("restore_contract")
    if not isinstance(restore, dict):
        raise ArtifactVaultError("artifact-vault manifest lacks restore_contract")
    legacy = restore.get("legacy_default_relative_path")
    if not isinstance(legacy, str) or not legacy:
        raise ArtifactVaultError(
            "artifact-vault manifest lacks legacy_default_relative_path"
        )
    return Path(repository_root) / legacy, "legacy_default"


def _safe_relative_subpath(relative_subpath: str | Path) -> Path:
    value = str(relative_subpath).replace("\\", "/")
    posix = PurePosixPath(value)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts:
        raise ArtifactVaultError(
            f"artifact subpath must be a safe relative path, got {relative_subpath!r}"
        )
    return Path(*posix.parts)


def require_conference_artifact_subpath(
    repository_root: Path,
    relative_subpath: str | Path,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve a required restored path or fail before a historical workflow runs."""

    relative = _safe_relative_subpath(relative_subpath)
    root, source = configured_conference_artifact_root(repository_root, environ)
    required = root / relative
    if not required.exists():
        raise ArtifactVaultError(
            f"historical artifact {CONFERENCE_ARTIFACT_ID!r} is not restored: "
            f"expected {required} (root selected by {source}). Set "
            f"{CONFERENCE_ARTIFACT_ENV} to the absolute extracted artifact root. "
            "No archive download or extraction is performed automatically."
        )
    return required


def conference_artifact_uri(relative_subpath: str | Path) -> str:
    """Return a stable provenance URI for a path inside the retained artifact."""

    relative = _safe_relative_subpath(relative_subpath)
    return f"artifact://{CONFERENCE_ARTIFACT_ID}/{relative.as_posix()}"
