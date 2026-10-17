"""Atomic workbench manifest and event recording."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .types import ResolvedPlan


MANIFEST_NAME = "workbench_manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def new_manifest(plan: ResolvedPlan) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": "model_workbench_run/1.0",
        "status": "PREPARED",
        "created_utc": now,
        "updated_utc": now,
        "recipe_id": plan.recipe_id,
        "recipe_title": plan.recipe_title,
        "action": plan.action,
        "resolved_plan": plan.to_dict(),
        "events": [{"utc": now, "type": "prepared", "message": "Run prepared"}],
        "steps": [],
        "completed_work_units": 0,
        "elapsed_seconds": None,
        "failure": None,
    }


def add_event(
    manifest: dict[str, Any], event_type: str, message: str, **details: Any
) -> None:
    event: dict[str, Any] = {"utc": utc_now(), "type": event_type, "message": message}
    if details:
        event["details"] = details
    manifest["events"].append(event)
    manifest["updated_utc"] = event["utc"]


def load_manifest(run_root: Path | str) -> dict[str, Any]:
    path = Path(run_root) / MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read workbench manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != "model_workbench_run/1.0":
        raise ValueError(f"not a current workbench manifest: {path}")
    return value
