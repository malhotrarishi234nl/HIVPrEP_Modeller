#!/usr/bin/env python3
"""Prepare transparent, agent-optional experiment suites.

This module deliberately does not import or construct the simulation engine.  It
validates a suite declaration and writes the exact resolved inputs, provenance,
ledger and terminal commands needed to run the existing research harnesses.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "0.1"


class SuiteError(ValueError):
    """A suite violates the preparation contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SuiteError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SuiteError(f"invalid JSON in {path}: {exc}") from exc


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pointer_parts(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise SuiteError(f"JSON pointer must start with '/': {pointer!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _pointer_parts(pointer)
    if not parts:
        raise SuiteError("patching the document root is not supported")
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise SuiteError(f"unresolved list path in {pointer!r}: {part!r}") from exc
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise SuiteError(f"unresolved JSON pointer: {pointer!r}")
    return current, parts[-1]


def _pointer_get(document: Any, pointer: str) -> Any:
    current = document
    for part in _pointer_parts(pointer):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise SuiteError(f"unresolved list path in {pointer!r}: {part!r}") from exc
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise SuiteError(f"unresolved JSON pointer: {pointer!r}")
    return current


def _pointer_set(document: Any, pointer: str, value: Any, *, require_existing: bool) -> None:
    parent, key = _pointer_parent(document, pointer)
    if isinstance(parent, list):
        try:
            index = int(key)
        except ValueError as exc:
            raise SuiteError(f"list index must be an integer in {pointer!r}") from exc
        if require_existing and not (0 <= index < len(parent)):
            raise SuiteError(f"replace target does not exist: {pointer!r}")
        if index == len(parent) and not require_existing:
            parent.append(value)
        elif 0 <= index < len(parent):
            parent[index] = value
        else:
            raise SuiteError(f"list index out of range: {pointer!r}")
    elif isinstance(parent, dict):
        if require_existing and key not in parent:
            raise SuiteError(f"replace target does not exist: {pointer!r}")
        parent[key] = value
    else:
        raise SuiteError(f"patch parent is not a container: {pointer!r}")


def _pointer_remove(document: Any, pointer: str) -> Any:
    parent, key = _pointer_parent(document, pointer)
    if isinstance(parent, list):
        try:
            return parent.pop(int(key))
        except (ValueError, IndexError) as exc:
            raise SuiteError(f"remove target does not exist: {pointer!r}") from exc
    if isinstance(parent, dict) and key in parent:
        return parent.pop(key)
    raise SuiteError(f"remove target does not exist: {pointer!r}")


def _resolve_special(value: Any, artifacts: Mapping[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict) and set(value) == {"$artifact"}:
        name = value["$artifact"]
        if name not in artifacts:
            raise SuiteError(f"artifact reference is not available yet: {name!r}")
        return artifacts[name]["output_path"]
    if isinstance(value, dict) and set(value) == {"$sha256"}:
        name = value["$sha256"]
        if name not in artifacts:
            raise SuiteError(f"artifact hash reference is not available yet: {name!r}")
        return artifacts[name]["sha256"]
    if isinstance(value, dict):
        return {key: _resolve_special(item, artifacts) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_special(item, artifacts) for item in value]
    return value


def _apply_patch(document: Any, patch: Mapping[str, Any], artifacts: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    operation = patch.get("op")
    path = patch.get("path")
    if operation not in {"test", "add", "replace", "remove", "move"}:
        raise SuiteError(f"unsupported patch operation: {operation!r}")
    if not isinstance(path, str):
        raise SuiteError("each patch needs a string 'path'")
    before = None
    if operation != "add":
        before = copy.deepcopy(_pointer_get(document, path))
    if operation == "test":
        expected = _resolve_special(patch.get("value"), artifacts)
        if before != expected:
            raise SuiteError(f"test failed at {path}: expected {expected!r}, found {before!r}")
        after = before
    elif operation in {"add", "replace"}:
        after = _resolve_special(patch.get("value"), artifacts)
        _pointer_set(document, path, copy.deepcopy(after), require_existing=operation == "replace")
    elif operation == "remove":
        _pointer_remove(document, path)
        after = None
    else:
        destination = patch.get("to")
        if not isinstance(destination, str):
            raise SuiteError("a move patch needs a string 'to' pointer")
        moved = _pointer_remove(document, path)
        _pointer_set(document, destination, moved, require_existing=False)
        after = {"moved_to": destination, "value": moved}
    return {"op": operation, "path": path, "before": before, "after": after}


def _git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.rstrip("\n")


def _validate_structure(suite: Mapping[str, Any], repo: Path) -> None:
    if suite.get("schema_version") != SCHEMA_VERSION:
        raise SuiteError(f"schema_version must be {SCHEMA_VERSION!r}")
    if not isinstance(suite.get("suite_id"), str) or not suite["suite_id"]:
        raise SuiteError("suite_id must be a non-empty string")
    artifacts = suite.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SuiteError("artifacts must be a non-empty ordered list")
    names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise SuiteError("each artifact must be an object")
        name = artifact.get("id")
        if not isinstance(name, str) or not name or name in names:
            raise SuiteError(f"artifact id must be non-empty and unique: {name!r}")
        names.add(name)
        source = repo / str(artifact.get("source", ""))
        if not source.is_file():
            raise SuiteError(f"artifact source does not exist: {source}")
        expected = artifact.get("expected_sha256")
        if expected and _sha256_file(source) != expected:
            raise SuiteError(f"source hash mismatch for artifact {name!r}")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SuiteError("cases must be a non-empty list")
    case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise SuiteError(f"case id must be non-empty and unique: {case_id!r}")
        case_ids.add(case_id)
        for patch in case.get("patches", []):
            if patch.get("artifact") not in names:
                raise SuiteError(f"case {case_id!r} patches unknown artifact {patch.get('artifact')!r}")
    stages = suite.get("stages", [])
    stage_ids: set[str] = set()
    for stage in stages:
        stage_id = stage.get("id") if isinstance(stage, dict) else None
        if not isinstance(stage_id, str) or not stage_id or stage_id in stage_ids:
            raise SuiteError(f"stage id must be non-empty and unique: {stage_id!r}")
        stage_ids.add(stage_id)
        if not isinstance(stage.get("command"), str):
            raise SuiteError(f"stage {stage_id!r} needs a command string")
    postprocess_ids: set[str] = set()
    for stage in suite.get("postprocess", []):
        stage_id = stage.get("id") if isinstance(stage, dict) else None
        if not isinstance(stage_id, str) or not stage_id or stage_id in postprocess_ids:
            raise SuiteError(f"postprocess id must be non-empty and unique: {stage_id!r}")
        postprocess_ids.add(stage_id)
        if not isinstance(stage.get("command"), str):
            raise SuiteError(f"postprocess {stage_id!r} needs a command string")
    for item in suite.get("provenance", {}).get("runtime_files", []):
        if not (repo / item).is_file():
            raise SuiteError(f"provenance runtime file does not exist: {item}")


def validate_suite(suite_path: Path, repo: Path) -> dict[str, Any]:
    suite = _read_json(suite_path)
    if not isinstance(suite, dict):
        raise SuiteError("suite document must be a JSON object")
    _validate_structure(suite, repo)
    # Resolve every case in memory so patch tests and dependencies are checked.
    for case in suite["cases"]:
        _resolve_case(suite, case, repo, Path(".validation") / case["id"], write=False)
    return {
        "ok": True,
        "suite_id": suite["suite_id"],
        "cases": [case["id"] for case in suite["cases"]],
        "artifacts": [artifact["id"] for artifact in suite["artifacts"]],
        "stages": [stage["id"] for stage in suite.get("stages", [])],
        "simulations_started": 0,
        "files_written": 0,
    }


def _resolve_case(
    suite: Mapping[str, Any], case: Mapping[str, Any], repo: Path, case_root: Path, *, write: bool
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    patches_by_artifact: dict[str, list[Mapping[str, Any]]] = {}
    for patch in case.get("patches", []):
        patches_by_artifact.setdefault(patch["artifact"], []).append(patch)
    resolved: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []
    for artifact in suite["artifacts"]:
        artifact_id = artifact["id"]
        source = repo / artifact["source"]
        document = _read_json(source)
        mutated = False
        for patch_index, patch in enumerate(patches_by_artifact.get(artifact_id, []), start=1):
            change = _apply_patch(document, patch, resolved)
            change.update({"case": case["id"], "artifact": artifact_id, "patch_index": patch_index})
            changes.append(change)
            mutated = mutated or patch.get("op") != "test"
        output_rel = case_root / "resolved_configs" / artifact.get("output_name", source.name)
        # Assertion-only artifacts remain byte-identical to their authoritative source.
        # This preserves hashes used by existing run manifests and publication archives.
        payload = _canonical_bytes(document) if mutated else source.read_bytes()
        record = {
            "source": artifact["source"],
            "source_sha256": _sha256_file(source),
            "output_path": output_rel.as_posix(),
            "sha256": _sha256_bytes(payload),
        }
        resolved[artifact_id] = record
        if write:
            output_path = repo / output_rel
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(payload)
    return resolved, changes


class _FormatValues(dict):
    def __missing__(self, key: str) -> str:
        raise SuiteError(f"unknown command placeholder: {{{key}}}")


def _render_command(template: str, values: Mapping[str, Any]) -> str:
    return template.format_map(_FormatValues({key: str(value) for key, value in values.items()}))


def _seed_rows(stage: Mapping[str, Any], artifact_docs: Mapping[str, Any]) -> list[tuple[str, Any]]:
    seed_source = stage.get("seed_source")
    if not seed_source:
        return [("", "")]
    artifact_id = seed_source.get("artifact")
    pointer = seed_source.get("path")
    if artifact_id not in artifact_docs or not isinstance(pointer, str):
        raise SuiteError(f"invalid seed_source for stage {stage.get('id')!r}")
    seeds = _pointer_get(artifact_docs[artifact_id], pointer)
    if not isinstance(seeds, list):
        raise SuiteError(f"seed source for stage {stage.get('id')!r} is not a list")
    return [(pointer.rsplit("/", 1)[-1], seed) for seed in seeds]


def prepare_suite(suite_path: Path, output_dir: Path, repo: Path) -> dict[str, Any]:
    suite = _read_json(suite_path)
    if not isinstance(suite, dict):
        raise SuiteError("suite document must be a JSON object")
    _validate_structure(suite, repo)
    if output_dir.exists():
        raise SuiteError(f"refusing to overwrite existing suite directory: {output_dir}")
    output_dir.mkdir(parents=True)
    output_rel = Path(os.path.relpath(output_dir, repo))
    all_resolved: dict[str, dict[str, dict[str, Any]]] = {}
    all_changes: list[dict[str, Any]] = []
    artifact_docs: dict[str, dict[str, Any]] = {}
    commands: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    for case in suite["cases"]:
        case_root = output_rel / "cases" / case["id"]
        resolved, changes = _resolve_case(suite, case, repo, case_root, write=True)
        all_resolved[case["id"]] = resolved
        all_changes.extend(changes)
        artifact_docs[case["id"]] = {
            artifact_id: _read_json(repo / record["output_path"])
            for artifact_id, record in resolved.items()
        }
        values: dict[str, Any] = {
            "case_id": case["id"],
            "case_root": case_root.as_posix(),
            "run_root": output_rel.as_posix(),
            "workers": suite.get("defaults", {}).get("workers", 1),
        }
        for artifact_id, record in resolved.items():
            values[f"artifact_{artifact_id}"] = record["output_path"]
        for order, stage in enumerate(suite.get("stages", []), start=1):
            command = _render_command(stage["command"], values)
            commands.append({
                "case": case["id"], "order": order, "stage": stage["id"],
                "command": command, "runnable": bool(suite.get("launch_ready", False)),
            })
            seed_rows = _seed_rows(stage, artifact_docs[case["id"]])
            shard_count = int(stage.get("shards", 1))
            if shard_count < 1:
                raise SuiteError(f"stage {stage['id']!r} shards must be at least 1")
            for seed_index, (seed_role, seed) in enumerate(seed_rows):
                ledger.append({
                    "case": case["id"], "stage": stage["id"], "order": order,
                    "seed_role": seed_role, "seed": seed,
                    "shard": seed_index % shard_count,
                    "shard_count": shard_count, "status": "PLANNED",
                })

    suite_values = {
        "run_root": output_rel.as_posix(),
        "workers": suite.get("defaults", {}).get("workers", 1),
    }
    for order, stage in enumerate(suite.get("postprocess", []), start=1):
        commands.append({
            "case": "suite", "order": order, "stage": stage["id"],
            "command": _render_command(stage["command"], suite_values),
            "runnable": bool(suite.get("launch_ready", False)),
        })

    (output_dir / "suite_source.json").write_bytes(_canonical_bytes(suite))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite["suite_id"],
        "suite_status": suite.get("status", "unspecified"),
        "launch_ready": bool(suite.get("launch_ready", False)),
        "launch_blockers": list(suite.get("launch_blockers", [])),
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "simulations_started": 0,
        "source_suite": os.path.relpath(suite_path, repo),
        "source_suite_sha256": _sha256_file(suite_path),
        "resolved_artifacts": all_resolved,
        "case_count": len(suite["cases"]),
        "stage_count": len(suite.get("stages", [])),
        "postprocess_count": len(suite.get("postprocess", [])),
        "command_count": len(commands),
        "ledger_rows": len(ledger),
    }
    (output_dir / "manifest.json").write_bytes(_canonical_bytes(manifest))
    (output_dir / "config_changes.json").write_bytes(_canonical_bytes(all_changes))

    with (output_dir / "config_changes.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["case", "artifact", "patch_index", "op", "path", "before", "after"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in all_changes:
            writer.writerow({**row, "before": json.dumps(row["before"]), "after": json.dumps(row["after"])})
    with (output_dir / "run_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["case", "stage", "order", "seed_role", "seed", "shard", "shard_count", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ledger)
    with (output_dir / "commands.sh").open("w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\nset -eu\n")
        handle.write(f"cd {shlex.quote(str(repo))}\n\n")
        if not suite.get("launch_ready", False):
            handle.write("echo 'REFUSED: suite is not launch-ready; inspect manifest.json launch_blockers.' >&2\n")
            handle.write("exit 2\n\n")
            handle.write("# Proposed commands below are retained for review only.\n")
        for row in commands:
            handle.write(f"# {row['case']} :: {row['stage']}\n{row['command']}\n\n")
    os.chmod(output_dir / "commands.sh", 0o755)
    (output_dir / "commands.json").write_bytes(_canonical_bytes(commands))

    git_status = _git(repo, "status", "--short") or ""
    provenance = {
        "git_commit": _git(repo, "rev-parse", "HEAD"),
        "git_branch": _git(repo, "branch", "--show-current"),
        "git_dirty": bool(git_status),
        "git_status_sha256": _sha256_bytes(git_status.encode()),
        "python": sys.version,
        "platform": platform.platform(),
        "runtime_files": {},
    }
    snapshot_root = output_dir / "source_snapshot"
    for item in suite.get("provenance", {}).get("runtime_files", []):
        source = repo / item
        destination = snapshot_root / item
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        provenance["runtime_files"][item] = _sha256_file(source)
    (output_dir / "provenance.json").write_bytes(_canonical_bytes(provenance))
    readme = f"""# Prepared experiment suite: {suite['suite_id']}

This directory was generated without constructing the model engine or starting a simulation.

- `suite_source.json`: immutable copy of the human-authored suite.
- `manifest.json`: hashes of all resolved case artifacts.
- `config_changes.csv`: human-readable case-by-case configuration changes.
- `run_ledger.csv`: declared stages, seeds and shard assignments.
- `commands.sh`: ordinary terminal commands; inspect and run them without an agent.
- `source_snapshot/`: runtime source files named by the suite's provenance contract.

Launch ready: `{bool(suite.get('launch_ready', False))}`.

The generated commands remain subject to each underlying runner's own validation, refusal,
budget and resume rules. Preparation is not scientific approval to launch the suite. When
launch-ready is false, `commands.sh` exits before any proposed command.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="check a suite; write nothing")
    validate.add_argument("--suite", type=Path, required=True)
    prepare = subparsers.add_parser("prepare", help="materialize a suite; run no simulations")
    prepare.add_argument("--suite", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parent
    try:
        if args.command == "validate":
            result = validate_suite(args.suite.resolve(), repo)
        else:
            result = prepare_suite(args.suite.resolve(), args.output_dir.resolve(), repo)
        print(json.dumps(result, indent=2))
        return 0
    except SuiteError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
