#!/usr/bin/env python3
"""Inventory prior RNG seeds without selecting seeds or running the model.

The scanner is deliberately conservative.  It parses integer values only from
recognised RNG fields in structured files, records seed-like but scientifically
different fields as ambiguous, and records malformed recognised fields as
unparseable.  Textual references are never promoted to parsed seeds.

The source-scope manifest, not this program, declares what is in scope.  A
disjointness result can be PROVEN only when the owner has approved that scope,
coverage is declared complete, no ambiguity remains, and explicit candidate
discovery/confirmation/proposal blocks are supplied.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nroy_contract as nc  # noqa: E402

SCHEMA = "robust_nroy_seed_audit/1.2.0"
SCOPE_SCHEMA = "robust_nroy_seed_source_scope/1.2.0"
CANDIDATE_SCHEMA = "robust_nroy_candidate_seed_blocks/1.0.0"
RESOLUTION_SCHEMA = "robust_nroy_seed_finding_resolutions/1.1.0"
EXTERNAL_REVIEW_SCHEMA = "robust_nroy_external_seed_location_review/1.0.0"

INTEGER = re.compile(r"^[+-]?\d+$")
SPLIT_INTEGER_LIST = re.compile(r"^[+-]?\d+(?:\s*[,;]\s*[+-]?\d+)+$")
TEXT_SEED = re.compile(r"\bseed(?:s|ed|ing)?\b", re.IGNORECASE)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
WILDCARD = re.compile(r"[*?\[\]]")

RNG_ROLES = {"simulation", "proposal_rng", "other_rng"}
FINDING_KINDS = {"ambiguous", "unparseable"}
CLASSIFICATIONS = {"RNG", "NON_RNG"}
DEFAULT_TRACKED_TEXT_EXTENSIONS = {
    ".gitignore", ".html", ".ini", ".md", ".mjs", ".py", ".rst", ".sh",
    ".toml", ".txt", ".yaml", ".yml",
}

PROPOSAL_FIELDS = {
    "proposal_rng", "proposal_rng_seed", "proposal_seed", "proposal_seeds",
    "design_rng_seed", "design_seed",
}
SIMULATION_FIELDS = {
    "seed", "simulation_seed", "simulation_seeds", "replicate_seed",
    "replicate_seeds", "discovery_seed", "discovery_seeds", "confirmation_seed",
    "confirmation_seeds", "smoke_seed", "smoke_seeds",
}
OTHER_RNG_FIELDS = {
    "random_seed", "rng_seed", "rng_seeds", "bootstrap_seed", "base_seed",
    "master_seed", "numpy_seed", "torch_seed", "confirmation_seed",
}
CONTAINER_FIELDS = {"seeds", "seed_blocks", "random_seeds"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON numeric literal is forbidden: {value}")


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is forbidden: {key!r}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def strict_json_load(path: Path) -> Any:
    return strict_json_loads(path.read_text(encoding="utf-8"))


def valid_utc_timestamp(value: object) -> bool:
    text = str(value or "")
    if not text.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _json_pointer(parts: Sequence[str]) -> str:
    escaped = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _preview(value: Any, limit: int = 180) -> str:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return rendered if len(rendered) <= limit else rendered[:limit - 3] + "..."


def _normalised_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _role_for(parts: Sequence[str]) -> str | None:
    keys = [_normalised_key(part) for part in parts]
    leaf = keys[-1]
    if leaf in PROPOSAL_FIELDS:
        return "proposal_rng"
    if len(keys) >= 2 and keys[-2] in PROPOSAL_FIELDS and leaf == "seed":
        return "proposal_rng"
    if leaf in SIMULATION_FIELDS:
        return "simulation"
    if leaf in OTHER_RNG_FIELDS:
        return "other_rng"
    if len(keys) >= 2 and keys[-2] in CONTAINER_FIELDS:
        if leaf in {"discovery", "confirmation", "smoke", "known_used_elsewhere"}:
            return "simulation"
        if leaf in {"proposal", "proposal_rng", "design"}:
            return "proposal_rng"
    return None


def _is_seed_like(parts: Sequence[str]) -> bool:
    return bool(parts) and "seed" in _normalised_key(parts[-1])


def _parse_integer_values(value: Any) -> tuple[list[int], list[Any]]:
    if isinstance(value, bool) or value is None:
        return [], [value]
    if isinstance(value, int):
        return [value], []
    if isinstance(value, str):
        stripped = value.strip()
        if INTEGER.fullmatch(stripped):
            return [int(stripped)], []
        if SPLIT_INTEGER_LIST.fullmatch(stripped):
            return [int(item.strip()) for item in re.split(r"[,;]", stripped)], []
        return [], [value]
    if isinstance(value, (list, tuple)):
        parsed: list[int] = []
        rejected: list[Any] = []
        for item in value:
            good, bad = _parse_integer_values(item)
            parsed.extend(good)
            rejected.extend(bad)
        return parsed, rejected
    return [], [value]


class Findings:
    """Compact deterministic evidence accumulator."""

    def __init__(self, *, example_limit: int = 2000, source_limit: int = 40):
        self.example_limit = int(example_limit)
        self.source_limit = int(source_limit)
        self.parsed: dict[int, dict[str, Any]] = {}
        self.ambiguous_total = 0
        self.ambiguous_examples: list[dict[str, str]] = []
        self.unparseable_total = 0
        self.unparseable_examples: list[dict[str, str]] = []
        self.records: list[dict[str, Any]] = []

    def add_seed(self, seed: int, role: str, source: str, location: str) -> None:
        row = self.parsed.setdefault(int(seed), {
            "seed": int(seed), "roles": set(), "occurrences": 0,
            "sources": [], "source_count_truncated": 0,
        })
        row["roles"].add(role)
        row["occurrences"] += 1
        evidence = f"{source}#{location}"
        if evidence not in row["sources"]:
            if len(row["sources"]) < self.source_limit:
                row["sources"].append(evidence)
            else:
                row["source_count_truncated"] += 1

    def _add_example(self, kind: str, source: str, location: str, detail: str,
                     value: Any = None) -> None:
        attr_total = f"{kind}_total"
        attr_examples = f"{kind}_examples"
        setattr(self, attr_total, getattr(self, attr_total) + 1)
        if value is None:
            value = {"scanner_detail": detail}
        try:
            value_hash = canonical_sha256(value)
        except (TypeError, ValueError):
            value_hash = canonical_sha256(str(value))
        record: dict[str, Any] = {
            "kind": kind,
            "source": source,
            "location": location,
            "detail": detail,
            "value_sha256": value_hash,
            "source_sha256": None,
            "finding_id": None,
            "resolved": False,
        }
        self.records.append(record)
        examples = getattr(self, attr_examples)
        if len(examples) < self.example_limit:
            examples.append(record)

    def add_ambiguous(self, source: str, location: str, detail: str,
                      value: Any = None) -> None:
        self._add_example("ambiguous", source, location, detail, value)

    def add_unparseable(self, source: str, location: str, detail: str,
                        value: Any = None) -> None:
        self._add_example("unparseable", source, location, detail, value)

    def bind_source_hashes(self, source_hashes: Mapping[str, str]) -> None:
        """Bind each reviewable finding to the exact bytes that produced it."""
        for row in self.records:
            row["source_sha256"] = source_hashes.get(row["source"])
            identity = {
                "kind": row["kind"],
                "source": row["source"],
                "location": row["location"],
                "source_sha256": row["source_sha256"],
                "value_sha256": row["value_sha256"],
            }
            row["finding_id"] = canonical_sha256(identity)

    def unresolved(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self.records
                if row["kind"] == kind and row["resolved"] is not True]

    @staticmethod
    def render_examples(rows: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
        keys = ("finding_id", "source", "location", "source_sha256",
                "value_sha256", "detail")
        return [{key: row.get(key) for key in keys} for row in rows[:limit]]

    def render_parsed(self) -> list[dict[str, Any]]:
        rendered = []
        for seed in sorted(self.parsed):
            row = self.parsed[seed]
            rendered.append({
                "seed": seed,
                "roles": sorted(row["roles"]),
                "occurrences": row["occurrences"],
                "sources": sorted(row["sources"]),
                "source_count_truncated": row["source_count_truncated"],
            })
        return rendered


def scan_structured(value: Any, *, source: str, findings: Findings,
                    parts: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            child = value[raw_key]
            child_parts = (*parts, key)
            role = _role_for(child_parts)
            if role is not None and not isinstance(child, Mapping):
                parsed, rejected = _parse_integer_values(child)
                for seed in parsed:
                    findings.add_seed(seed, role, source, _json_pointer(child_parts))
                for item in rejected:
                    findings.add_unparseable(
                        source, _json_pointer(child_parts),
                        f"recognised {role} field is not an explicit integer or integer list: "
                        f"{_preview(item)}",
                        item,
                    )
                continue
            if _is_seed_like(child_parts) and role is None and not isinstance(child, Mapping):
                findings.add_ambiguous(
                    source, _json_pointer(child_parts),
                    f"seed-like field has no declared RNG semantics: {_preview(child)}",
                    child,
                )
                continue
            scan_structured(child, source=source, findings=findings, parts=child_parts)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            scan_structured(child, source=source, findings=findings,
                            parts=(*parts, str(index)))


def scan_json(path: Path, relative: str, findings: Findings) -> None:
    try:
        payload = strict_json_load(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        findings.add_unparseable(
            relative, "/", f"JSON parse failed: {exc}", {"parse_error": str(exc)}
        )
        return
    scan_structured(payload, source=relative, findings=findings)


def scan_json_lines(path: Path, relative: str, findings: Findings) -> None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = strict_json_loads(line)
                except (json.JSONDecodeError, ValueError) as exc:
                    findings.add_unparseable(
                        relative, f"line:{line_number}", f"JSON-line parse failed: {exc}",
                        line.rstrip("\r\n"),
                    )
                    continue
                scan_structured(payload, source=relative, findings=findings,
                                parts=(f"line:{line_number}",))
    except (OSError, UnicodeDecodeError) as exc:
        findings.add_unparseable(
            relative, "/", f"JSON-lines read failed: {exc}", {"read_error": str(exc)}
        )


def seed_columns(fieldnames: Sequence[str] | None) -> list[str]:
    return [name for name in (fieldnames or ()) if _is_seed_like((name,))]


def scan_csv(path: Path, relative: str, findings: Findings, *, force_text: bool = False) -> bool:
    """Scan explicit seed columns.  Return True when such columns existed."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = seed_columns(reader.fieldnames)
            if not columns:
                if force_text:
                    scan_text(path, relative, findings)
                return False
            roles = {column: _role_for((column,)) for column in columns}
            for column, role in roles.items():
                if role is None:
                    findings.add_ambiguous(
                        relative, f"column:{column}",
                        "seed-like CSV column has no declared RNG semantics; its values were not "
                        "promoted to RNG seeds",
                        {"column": column},
                    )
            for row_number, row in enumerate(reader, start=2):
                for column in columns:
                    value = row.get(column)
                    role = roles[column]
                    if role is None:
                        continue
                    parsed, rejected = _parse_integer_values(value)
                    for seed in parsed:
                        findings.add_seed(seed, role, relative,
                                          f"row:{row_number}/{column}")
                    for item in rejected:
                        if item not in (None, ""):
                            findings.add_unparseable(
                                relative, f"row:{row_number}/{column}",
                                f"recognised {role} CSV value is not explicit integers: "
                                f"{_preview(item)}",
                                item,
                            )
            return True
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        findings.add_unparseable(
            relative, "/", f"CSV read failed: {exc}", {"read_error": str(exc)}
        )
        return True


def scan_text(path: Path, relative: str, findings: Findings) -> None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if TEXT_SEED.search(line):
                    findings.add_ambiguous(
                        relative, f"line:{line_number}",
                        "textual seed reference requires owner interpretation: "
                        + " ".join(line.strip().split())[:180],
                        line.rstrip("\r\n"),
                    )
    except (OSError, UnicodeDecodeError) as exc:
        findings.add_unparseable(
            relative, "/", f"text read failed: {exc}", {"read_error": str(exc)}
        )


def git_tracked_paths(root: Path, pathspecs: Sequence[str]) -> list[Path]:
    command = ["git", "ls-files", "-z", "--", *pathspecs]
    try:
        completed = subprocess.run(command, cwd=root, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            "seed-source root is not a readable Git worktree: "
            + exc.stderr.decode("utf-8", errors="replace").strip()
        ) from exc
    return [root / item.decode("utf-8")
            for item in completed.stdout.split(b"\0") if item]


def _parser_kind(path: Path, *, output: bool, output_rules: Mapping[str, Any]) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if not output:
        if name.endswith(".json") or name.endswith(".json.template"):
            return "json"
        if suffix in {".jsonl", ".ndjson"}:
            return "json_lines"
        if suffix == ".csv":
            return "csv_text"
        text_extensions = set(output_rules.get(
            "text_extensions", DEFAULT_TRACKED_TEXT_EXTENSIONS
        ))
        if name.endswith(".md.template") or suffix in text_extensions or \
                (name == ".gitignore" and ".gitignore" in text_extensions):
            return "text"
        return "excluded"
    if suffix == ".json" and any(fnmatch.fnmatch(name, pattern.lower())
                                  for pattern in output_rules["json_basename_globs"]):
        return "json"
    if suffix in {".jsonl", ".ndjson"} and any(
            fnmatch.fnmatch(name, pattern.lower())
            for pattern in output_rules["json_lines_basename_globs"]):
        return "json_lines"
    if suffix == ".csv":
        return "csv_probe"
    if suffix in set(output_rules["text_extensions"]):
        return "text"
    return "excluded"


def _path_record(root: Path, path: Path, kind: str) -> dict[str, Any]:
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
            "kind": kind}


def _excluded_output_record(root: Path, path: Path, kind: str) -> dict[str, Any]:
    """Return the immutable identity of one output omitted from seed parsing."""
    record = _path_record(root, path, kind)
    record["sha256"] = sha256_file(path)
    return record


def scan_scope(root: Path, scope: Mapping[str, Any]) -> tuple[Findings, dict[str, Any]]:
    findings = Findings(
        example_limit=int(scope.get("evidence_example_limit", 2000)),
        source_limit=int(scope.get("parsed_seed_source_limit", 40)),
    )
    inventory: list[dict[str, Any]] = []
    scanned_hashes: list[dict[str, str]] = []
    excluded_output_index: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    max_bytes = int(scope["outputs"]["max_scanned_file_bytes"])

    tracked_rules = scope["tracked_repository"]
    exclusions = {item["path"]: item for item in tracked_rules.get(
        "explicit_exclusions", ())}
    seen_exclusions: set[str] = set()
    source_hashes: dict[str, str] = {}
    tracked = git_tracked_paths(root, tracked_rules["pathspecs"])
    for path in sorted(tracked):
        relative = path.relative_to(root).as_posix()
        current_hash = sha256_file(path)
        source_hashes[relative] = current_hash
        if relative in exclusions:
            exclusion = exclusions[relative]
            seen_exclusions.add(relative)
            kind = "excluded_declared"
            inventory.append(_path_record(root, path, f"tracked:{kind}"))
            counts[f"tracked:{kind}"] = counts.get(f"tracked:{kind}", 0) + 1
            if current_hash != exclusion["sha256"]:
                raise ValueError(
                    "declared tracked-file exclusion is stale: "
                    f"{relative} expected {exclusion['sha256']} but found {current_hash}"
                )
            continue
        kind = _parser_kind(path, output=False, output_rules=tracked_rules)
        inventory.append(_path_record(root, path, f"tracked:{kind}"))
        counts[f"tracked:{kind}"] = counts.get(f"tracked:{kind}", 0) + 1
        if kind == "json":
            scan_json(path, relative, findings)
        elif kind == "json_lines":
            scan_json_lines(path, relative, findings)
        elif kind == "csv_text":
            scan_csv(path, relative, findings, force_text=True)
        elif kind == "text":
            scan_text(path, relative, findings)
        else:
            findings.add_unparseable(
                relative, "/", "tracked file format is not parsed and lacks an exact exclusion",
                {"suffix": path.suffix.lower()},
            )
            continue
        scanned_hashes.append({"path": relative, "sha256": sha256_file(path)})

    missing_exclusions = sorted(set(exclusions) - seen_exclusions)
    if missing_exclusions:
        raise ValueError(
            "tracked_repository.explicit_exclusions contains missing or untracked paths: "
            + ", ".join(missing_exclusions)
        )

    output_root = root / str(scope["outputs"]["root"])
    if not output_root.exists():
        findings.add_unparseable(
            str(scope["outputs"]["root"]), "/", "declared mounted output root does not exist",
            {"root": str(scope["outputs"]["root"])},
        )
    else:
        rules = scope["outputs"]
        for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            kind = _parser_kind(path, output=True, output_rules=rules)
            record = _path_record(root, path, f"output:{kind}")
            inventory.append(record)
            counts[f"output:{kind}"] = counts.get(f"output:{kind}", 0) + 1
            if kind == "excluded":
                excluded_output_index.append(_excluded_output_record(
                    root, path, "output:excluded"
                ))
                continue
            # Even a selected file that is too large to parse must have a
            # stable source identity so a later reviewed disposition can bind
            # the exact bytes instead of an unresolvable path-only finding.
            source_hashes[relative] = sha256_file(path)
            if record["bytes"] > max_bytes:
                findings.add_unparseable(
                    relative, "/",
                    f"selected source exceeds max_scanned_file_bytes={max_bytes}",
                    {"bytes": record["bytes"], "max_scanned_file_bytes": max_bytes},
                )
                continue
            if kind == "csv_probe":
                if not scan_csv(path, relative, findings):
                    record["kind"] = "output:csv_without_seed_column"
                    counts["output:csv_probe"] -= 1
                    counts["output:csv_without_seed_column"] = \
                        counts.get("output:csv_without_seed_column", 0) + 1
                    excluded_output_index.append({
                        **record,
                        "sha256": source_hashes[relative],
                    })
                    continue
            elif kind == "json":
                scan_json(path, relative, findings)
            elif kind == "json_lines":
                scan_json_lines(path, relative, findings)
            elif kind == "text":
                scan_text(path, relative, findings)
            scanned_hashes.append({"path": relative, "sha256": source_hashes[relative]})

    findings.bind_source_hashes(source_hashes)

    inventory_digest = canonical_sha256(inventory)
    scanned_digest = canonical_sha256(scanned_hashes)
    excluded_output_index.sort(key=lambda item: item["path"])
    excluded_output_bytes = sum(int(item["bytes"]) for item in excluded_output_index)
    return findings, {
        "counts_by_kind": dict(sorted((key, value) for key, value in counts.items() if value)),
        "inventory_sha256": inventory_digest,
        "scanned_content_index_sha256": scanned_digest,
        "scanned_files": len(scanned_hashes),
        "excluded_output_files": len(excluded_output_index),
        "excluded_output_bytes": excluded_output_bytes,
        "excluded_output_index_sha256": canonical_sha256(excluded_output_index),
        "excluded_output_index": excluded_output_index,
    }


def _validate_external_evidence(
        values: object, *, label: str, root: Path | None, location_kind: str,
        location_status: str, reviewer: object, reviewed_utc: object,
        used_paths: set[str]) -> None:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    if location_status == "UNRESOLVED":
        if values:
            raise ValueError(f"{label} must be empty while the location is UNRESOLVED")
        return
    if len(values) != 1:
        raise ValueError(f"{label} must contain exactly one typed location-review record")
    if root is None:
        raise ValueError(f"{label} requires a root for typed-record verification")
    required = {"path", "sha256", "description"}
    for index, evidence in enumerate(values):
        item_label = f"{label}[{index}]"
        if not isinstance(evidence, Mapping) or set(evidence) != required:
            raise ValueError(
                f"{item_label} requires exactly path, sha256, and description"
            )
        source = _validate_exact_source(evidence["path"], f"{item_label}.path")
        if source in used_paths:
            raise ValueError(
                f"external location review evidence cannot be reused across locations: {source}"
            )
        used_paths.add(source)
        if not SHA256.fullmatch(str(evidence["sha256"])):
            raise ValueError(f"{item_label}.sha256 must be a lowercase SHA-256")
        if not str(evidence["description"]).strip():
            raise ValueError(f"{item_label}.description is required")
        resolved_root = root.resolve()
        evidence_path = (resolved_root / source).resolve()
        if not evidence_path.is_relative_to(resolved_root) or not evidence_path.is_file():
            raise ValueError(f"external evidence file is missing: {source}")
        actual = sha256_file(evidence_path)
        if actual != evidence["sha256"]:
            raise ValueError(
                f"external evidence hash is stale: {source} expected "
                f"{evidence['sha256']} but found {actual}"
            )
        try:
            review = strict_json_load(evidence_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"external location review record is not strict JSON: {source}: {exc}"
            ) from exc
        review_required = {
            "schema", "location_kind", "review_status", "reviewer", "reviewed_utc",
            "searched_locations", "searched_accounts_or_owners", "search_method",
            "search_started_utc", "search_completed_utc", "indexed_sources",
            "no_additional_source_basis",
        }
        if not isinstance(review, Mapping) or set(review) != review_required:
            raise ValueError(
                f"external location review record has an invalid typed shape: {source}"
            )
        if review["schema"] != EXTERNAL_REVIEW_SCHEMA:
            raise ValueError(f"external location review record has wrong schema: {source}")
        if review["location_kind"] != location_kind or \
                review["review_status"] != location_status:
            raise ValueError(
                f"external location review record is for a different location/status: {source}"
            )
        if review["reviewer"] != reviewer or review["reviewed_utc"] != reviewed_utc:
            raise ValueError(
                f"external location review record reviewer/time differs from scope: {source}"
            )
        start = review["search_started_utc"]
        completed = review["search_completed_utc"]
        if not valid_utc_timestamp(start) or not valid_utc_timestamp(completed) \
                or not valid_utc_timestamp(review["reviewed_utc"]):
            raise ValueError(
                f"external location review record requires UTC search/review times: {source}"
            )
        start_time = datetime.fromisoformat(str(start)[:-1] + "+00:00")
        completed_time = datetime.fromisoformat(str(completed)[:-1] + "+00:00")
        review_time = datetime.fromisoformat(str(review["reviewed_utc"])[:-1] + "+00:00")
        if start_time > completed_time or completed_time > review_time:
            raise ValueError(
                f"external location review record search/review chronology is invalid: {source}"
            )
        for field in ("searched_locations", "searched_accounts_or_owners"):
            entries = review[field]
            if (not isinstance(entries, list) or not entries
                    or any(not isinstance(value, str) or not value.strip()
                           for value in entries)
                    or len(entries) != len(set(entries))):
                raise ValueError(
                    f"external location review record requires unique non-empty {field}: {source}"
                )
        if not isinstance(review["search_method"], str) or not review["search_method"].strip():
            raise ValueError(
                f"external location review record requires search_method: {source}"
            )
        indexed = review["indexed_sources"]
        if not isinstance(indexed, list):
            raise ValueError(
                f"external location review record indexed_sources must be a list: {source}"
            )
        indexed_ids: set[str] = set()
        for source_index, indexed_source in enumerate(indexed):
            item_label = f"{source}.indexed_sources[{source_index}]"
            if not isinstance(indexed_source, Mapping) or set(indexed_source) != {
                "source_id", "sha256", "disposition", "description"
            }:
                raise ValueError(f"{item_label} has an invalid typed shape")
            source_id = str(indexed_source["source_id"])
            if not source_id.strip() or source_id in indexed_ids:
                raise ValueError(f"{item_label}.source_id is empty or duplicated")
            indexed_ids.add(source_id)
            if not SHA256.fullmatch(str(indexed_source["sha256"])):
                raise ValueError(f"{item_label}.sha256 must be a lowercase SHA-256")
            if indexed_source["disposition"] not in {"IN_SCOPE", "REVIEWED_NON_SEED"}:
                raise ValueError(f"{item_label}.disposition is invalid")
            if not str(indexed_source["description"]).strip():
                raise ValueError(f"{item_label}.description is required")
        basis = review["no_additional_source_basis"]
        if location_status == "COVERED":
            if not indexed or basis is not None:
                raise ValueError(
                    f"COVERED external review requires indexed sources and null "
                    f"no_additional_source_basis: {source}"
                )
        elif (not isinstance(basis, str) or not basis.strip()):
            raise ValueError(
                f"REVIEWED_NONE external review requires an explicit no-source basis: {source}"
            )


def load_scope(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    payload = strict_json_load(path)
    if payload.get("schema") != SCOPE_SCHEMA:
        raise ValueError(f"scope schema must be {SCOPE_SCHEMA!r}")
    if payload.get("owner_scope", {}).get("status") not in {"UNRESOLVED", "APPROVED"}:
        raise ValueError("owner_scope.status must be UNRESOLVED or APPROVED")
    if payload.get("source_coverage", {}).get("status") not in {"UNRESOLVED", "COMPLETE"}:
        raise ValueError("source_coverage.status must be UNRESOLVED or COMPLETE")
    if payload["owner_scope"]["status"] == "APPROVED":
        for field in ("approved_by", "approved_utc", "scope_statement"):
            if not str(payload["owner_scope"].get(field, "")).strip():
                raise ValueError(f"approved owner scope requires owner_scope.{field}")
        if not valid_utc_timestamp(payload["owner_scope"]["approved_utc"]):
            raise ValueError("owner_scope.approved_utc must be an ISO-8601 UTC timestamp")
    tracked = payload.get("tracked_repository", {})
    if not isinstance(tracked.get("pathspecs"), list) or not tracked["pathspecs"]:
        raise ValueError("tracked_repository.pathspecs must be a non-empty list")
    for pathspec in tracked["pathspecs"]:
        text = str(pathspec)
        if (not text or Path(text).is_absolute() or ".." in Path(text).parts
                or WILDCARD.search(text) or text.startswith(":")):
            raise ValueError(
                "tracked_repository.pathspecs must be explicit repository-relative paths"
            )
    exclusions = tracked.get("explicit_exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("tracked_repository.explicit_exclusions must be a list")
    exclusion_paths: set[str] = set()
    for item in exclusions:
        required = {"path", "sha256", "reason", "coverage_status"}
        if not isinstance(item, Mapping) or set(item) != required:
            raise ValueError(
                "each tracked exclusion requires exactly path, sha256, reason, coverage_status"
            )
        source = str(item["path"])
        if (not source or Path(source).is_absolute() or ".." in Path(source).parts
                or WILDCARD.search(source)):
            raise ValueError("tracked exclusions must use exact repository-relative paths")
        if source in exclusion_paths:
            raise ValueError(f"duplicate tracked exclusion: {source}")
        exclusion_paths.add(source)
        if not SHA256.fullmatch(str(item["sha256"])):
            raise ValueError(f"tracked exclusion has invalid SHA-256: {source}")
        if item["coverage_status"] not in {"UNRESOLVED", "REVIEWED_NON_SEED"}:
            raise ValueError(f"invalid tracked exclusion coverage_status: {source}")
        if not str(item["reason"]).strip():
            raise ValueError(f"tracked exclusion requires a reason: {source}")
    locations = payload.get("external_execution_locations")
    required_locations = {
        "REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD"
    }
    if not isinstance(locations, list) or len(locations) != len(required_locations) or {
            str(item.get("kind")) for item in locations if isinstance(item, Mapping)
    } != required_locations:
        raise ValueError(
            "external_execution_locations must contain exactly REMOTE_HPC, CLOUD, "
            "DELETED_LOCAL, and COLLABORATOR_HELD"
        )
    used_external_evidence_paths: set[str] = set()
    for item in locations:
        if set(item) != {
            "kind", "status", "coverage_decision", "evidence", "reviewer", "reviewed_utc"
        }:
            raise ValueError("external execution location records have an invalid shape")
        if item["status"] not in {"UNRESOLVED", "COVERED", "REVIEWED_NONE"}:
            raise ValueError(f"invalid external location status for {item['kind']}")
        if not str(item["coverage_decision"]).strip():
            raise ValueError(f"external location {item['kind']} requires a coverage decision")
        _validate_external_evidence(
            item["evidence"],
            label=f"external location {item['kind']} evidence",
            root=root,
            location_kind=str(item["kind"]),
            location_status=str(item["status"]),
            reviewer=item["reviewer"],
            reviewed_utc=item["reviewed_utc"],
            used_paths=used_external_evidence_paths,
        )
        if item["status"] != "UNRESOLVED":
            if not item["evidence"]:
                raise ValueError(
                    f"resolved external location {item['kind']} requires at least one "
                    "hash-bound evidence file"
                )
            if not str(item["reviewer"] or "").strip() or not valid_utc_timestamp(
                    item["reviewed_utc"]):
                raise ValueError(
                    f"resolved external location {item['kind']} requires reviewer and UTC time"
                )
        elif item["reviewer"] is not None or item["reviewed_utc"] is not None:
            raise ValueError(
                f"unresolved external location {item['kind']} cannot imply completed review"
            )
    if payload["source_coverage"]["status"] == "COMPLETE":
        if any(item["coverage_status"] != "REVIEWED_NON_SEED" for item in exclusions):
            raise ValueError("COMPLETE source coverage requires every exclusion to be reviewed")
        if any(item["status"] == "UNRESOLVED" for item in locations):
            raise ValueError(
                "COMPLETE source coverage cannot retain unresolved external locations"
            )
    outputs = payload.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("outputs must be an object")
    output_root = str(outputs.get("root", ""))
    if (not output_root or Path(output_root).is_absolute()
            or ".." in Path(output_root).parts or WILDCARD.search(output_root)):
        raise ValueError("outputs.root must be an exact repository-relative path")
    for field in ("json_basename_globs", "json_lines_basename_globs", "text_extensions"):
        if (not isinstance(outputs.get(field), list)
                or any(not isinstance(value, str) or not value
                       for value in outputs[field])):
            raise ValueError(f"outputs.{field} must be a list of non-empty strings")
    max_bytes = outputs.get("max_scanned_file_bytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("outputs.max_scanned_file_bytes must be a positive integer")
    return payload


def _require_exact_keys(value: Mapping[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        missing = sorted(required - set(value))
        extra = sorted(set(value) - required)
        raise ValueError(f"{label} fields are invalid; missing={missing}, extra={extra}")


def _validate_review_fields(item: Mapping[str, Any], label: str) -> None:
    if not str(item.get("reviewer", "")).strip():
        raise ValueError(f"{label}.reviewer is required")
    if not valid_utc_timestamp(item.get("reviewed_utc")):
        raise ValueError(f"{label}.reviewed_utc must be an ISO-8601 UTC timestamp")
    if not str(item.get("rationale", "")).strip():
        raise ValueError(f"{label}.rationale is required")


def _validate_exact_source(source: object, label: str) -> str:
    text = str(source)
    if (not text or Path(text).is_absolute() or ".." in Path(text).parts
            or WILDCARD.search(text)):
        raise ValueError(f"{label} must be an exact repository-relative path without wildcards")
    return text


def _validate_rng_payload(item: Mapping[str, Any], label: str,
                          *, mapping: bool = False) -> None:
    classification = item.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"{label}.classification must be RNG or NON_RNG")
    role = item.get("rng_role")
    values = item.get("rng_integers_by_finding") if mapping else item.get("rng_integers")
    if classification == "NON_RNG":
        if role is not None or values not in ({}, []):
            raise ValueError(f"{label} NON_RNG decisions cannot carry RNG data")
        return
    if role not in RNG_ROLES:
        raise ValueError(f"{label}.rng_role is invalid")
    if mapping:
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"{label} RNG rule requires rng_integers_by_finding")
        groups = values.values()
    else:
        if not isinstance(values, list) or not values:
            raise ValueError(f"{label} RNG decision requires rng_integers")
        groups = (values,)
    for group in groups:
        if (not isinstance(group, list) or not group
                or any(isinstance(value, bool) or not isinstance(value, int)
                       for value in group)
                or len(group) != len(set(group))):
            raise ValueError(f"{label} RNG integers must be non-empty unique integer lists")


def load_resolutions(path: Path, *, scope_sha256: str) -> dict[str, Any]:
    payload = strict_json_load(path)
    _require_exact_keys(
        payload,
        {"schema", "status", "scope_manifest_sha256", "exact_resolutions",
         "reviewed_rules", "excluded_output_dispositions",
         "excluded_output_index_review", "limitations"},
        "resolution document",
    )
    if payload.get("schema") != RESOLUTION_SCHEMA:
        raise ValueError(f"resolution schema must be {RESOLUTION_SCHEMA!r}")
    if payload.get("status") not in {"INCOMPLETE", "REVIEWED"}:
        raise ValueError("resolution status must be INCOMPLETE or REVIEWED")
    if payload.get("scope_manifest_sha256") != scope_sha256:
        raise ValueError("resolution document is stale: scope manifest SHA-256 differs")
    if not isinstance(payload["exact_resolutions"], list):
        raise ValueError("exact_resolutions must be a list")
    if not isinstance(payload["reviewed_rules"], list):
        raise ValueError("reviewed_rules must be a list")
    if not isinstance(payload["excluded_output_dispositions"], list):
        raise ValueError("excluded_output_dispositions must be a list")
    if not isinstance(payload["limitations"], list):
        raise ValueError("resolution limitations must be a list")

    output_paths: set[str] = set()
    output_required = {
        "path", "bytes", "kind", "sha256", "disposition", "reviewer",
        "reviewed_utc", "rationale",
    }
    for index, item in enumerate(payload["excluded_output_dispositions"]):
        label = f"excluded_output_dispositions[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be an object")
        _require_exact_keys(item, output_required, label)
        source = _validate_exact_source(item["path"], f"{label}.path")
        if source in output_paths:
            raise ValueError(f"duplicate excluded-output disposition: {source}")
        output_paths.add(source)
        if (isinstance(item["bytes"], bool) or not isinstance(item["bytes"], int)
                or item["bytes"] < 0):
            raise ValueError(f"{label}.bytes must be a non-negative integer")
        if item["kind"] not in {
            "output:excluded", "output:csv_without_seed_column"
        }:
            raise ValueError(f"{label}.kind is invalid")
        if not SHA256.fullmatch(str(item["sha256"])):
            raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
        if item["disposition"] != "REVIEWED_NON_SEED":
            raise ValueError(f"{label}.disposition must be REVIEWED_NON_SEED")
        _validate_review_fields(item, label)

    index_review = payload["excluded_output_index_review"]
    if index_review is not None:
        if not isinstance(index_review, Mapping):
            raise ValueError("excluded_output_index_review must be null or an object")
        label = "excluded_output_index_review"
        _require_exact_keys(index_review, {
            "rule_id", "expected_file_count", "expected_total_bytes",
            "expected_index_sha256", "disposition", "reviewer", "reviewed_utc",
            "rationale",
        }, label)
        rule_id = str(index_review["rule_id"])
        if not rule_id or WILDCARD.search(rule_id):
            raise ValueError(f"{label}.rule_id must be exact and non-empty")
        for field in ("expected_file_count", "expected_total_bytes"):
            value = index_review[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label}.{field} must be a non-negative integer")
        if not SHA256.fullmatch(str(index_review["expected_index_sha256"])):
            raise ValueError(f"{label}.expected_index_sha256 must be a lowercase SHA-256")
        if index_review["disposition"] != "REVIEWED_NON_SEED":
            raise ValueError(f"{label}.disposition must be REVIEWED_NON_SEED")
        _validate_review_fields(index_review, label)
        if payload["excluded_output_dispositions"]:
            raise ValueError(
                "excluded-output per-file dispositions and index review cannot overlap"
            )

    exact_keys: set[tuple[Any, ...]] = set()
    exact_required = {
        "finding_kind", "source", "location", "source_sha256", "value_sha256",
        "classification", "rng_role", "rng_integers", "reviewer", "reviewed_utc",
        "rationale",
    }
    for index, item in enumerate(payload["exact_resolutions"]):
        label = f"exact_resolutions[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be an object")
        _require_exact_keys(item, exact_required, label)
        if item["finding_kind"] not in FINDING_KINDS:
            raise ValueError(f"{label}.finding_kind is invalid")
        source = _validate_exact_source(item["source"], f"{label}.source")
        if not str(item["location"]):
            raise ValueError(f"{label}.location is required")
        for field in ("source_sha256", "value_sha256"):
            if not SHA256.fullmatch(str(item[field])):
                raise ValueError(f"{label}.{field} must be a lowercase SHA-256")
        _validate_review_fields(item, label)
        _validate_rng_payload(item, label)
        key = (item["finding_kind"], source, item["location"],
               item["source_sha256"], item["value_sha256"])
        if key in exact_keys:
            raise ValueError(f"duplicate exact resolution at {source}#{item['location']}")
        exact_keys.add(key)

    rule_ids: set[str] = set()
    rule_required = {
        "rule_id", "finding_kind", "source", "source_sha256", "selector",
        "expected_match_count", "expected_matches_sha256", "classification",
        "rng_role", "rng_integers_by_finding", "reviewer", "reviewed_utc",
        "rationale",
    }
    for index, item in enumerate(payload["reviewed_rules"]):
        label = f"reviewed_rules[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be an object")
        _require_exact_keys(item, rule_required, label)
        rule_id = str(item["rule_id"])
        if not rule_id or WILDCARD.search(rule_id) or rule_id in rule_ids:
            raise ValueError(f"{label}.rule_id is empty, duplicated, or wildcarded")
        rule_ids.add(rule_id)
        if item["finding_kind"] not in FINDING_KINDS:
            raise ValueError(f"{label}.finding_kind is invalid")
        _validate_exact_source(item["source"], f"{label}.source")
        if not SHA256.fullmatch(str(item["source_sha256"])):
            raise ValueError(f"{label}.source_sha256 must be a lowercase SHA-256")
        if (isinstance(item["expected_match_count"], bool)
                or not isinstance(item["expected_match_count"], int)
                or item["expected_match_count"] < 1):
            raise ValueError(f"{label}.expected_match_count must be a positive integer")
        if not SHA256.fullmatch(str(item["expected_matches_sha256"])):
            raise ValueError(f"{label}.expected_matches_sha256 must be a lowercase SHA-256")
        selector = item["selector"]
        if not isinstance(selector, Mapping):
            raise ValueError(f"{label}.selector must be an object")
        selector_type = selector.get("type")
        if selector_type == "line_range":
            _require_exact_keys(selector, {"type", "start_line", "end_line"},
                                f"{label}.selector")
            start, end = selector["start_line"], selector["end_line"]
            if (isinstance(start, bool) or isinstance(end, bool)
                    or not isinstance(start, int) or not isinstance(end, int)
                    or start < 1 or end < start or end - start + 1 > 1000):
                raise ValueError(f"{label} line_range must span 1..1000 positive lines")
        elif selector_type == "json_pointer_subtree":
            _require_exact_keys(selector, {"type", "prefix", "leaf_keys"},
                                f"{label}.selector")
            prefix = str(selector["prefix"])
            leaves = selector["leaf_keys"]
            if (not prefix.startswith("/") or prefix == "/" or WILDCARD.search(prefix)
                    or not isinstance(leaves, list) or not leaves or len(leaves) > 50
                    or any(not str(leaf) or "/" in str(leaf) or WILDCARD.search(str(leaf))
                           for leaf in leaves)
                    or len(leaves) != len(set(map(str, leaves)))):
                raise ValueError(f"{label} json_pointer_subtree selector is overbroad")
        elif selector_type == "csv_column":
            _require_exact_keys(selector, {"type", "column"}, f"{label}.selector")
            column = str(selector["column"])
            if not column or "/" in column or WILDCARD.search(column):
                raise ValueError(f"{label} csv_column selector is invalid")
        else:
            raise ValueError(f"{label}.selector.type is invalid")
        _validate_review_fields(item, label)
        _validate_rng_payload(item, label, mapping=True)
    return payload


def _rule_matches(row: Mapping[str, Any], selector: Mapping[str, Any]) -> bool:
    location = str(row["location"])
    if selector["type"] == "line_range":
        match = re.fullmatch(r"line:(\d+)", location)
        return bool(match and selector["start_line"] <= int(match.group(1))
                    <= selector["end_line"])
    if selector["type"] == "json_pointer_subtree":
        prefix = str(selector["prefix"]).rstrip("/")
        if not (location == prefix or location.startswith(prefix + "/")):
            return False
        leaf = location.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        return leaf in set(map(str, selector["leaf_keys"]))
    column = str(selector["column"])
    return location == f"column:{column}" or location.endswith(f"/{column}")


def apply_resolutions(findings: Findings, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "NOT_SUPPLIED", "resolved_rng": 0, "resolved_non_rng": 0,
            "unresolved": len(findings.records), "payload_sha256": None,
        }
    claimed: set[str] = set()
    resolved_rng = 0
    resolved_non_rng = 0

    for item in payload["exact_resolutions"]:
        matches = [row for row in findings.records if (
            row["kind"] == item["finding_kind"]
            and row["source"] == item["source"]
            and row["location"] == item["location"]
            and row["source_sha256"] == item["source_sha256"]
            and row["value_sha256"] == item["value_sha256"]
        )]
        if len(matches) != 1:
            raise ValueError(
                "exact resolution is stale or missing: "
                f"{item['source']}#{item['location']} matched {len(matches)} findings"
            )
        row = matches[0]
        finding_id = str(row["finding_id"])
        if finding_id in claimed:
            raise ValueError(f"finding resolved more than once: {finding_id}")
        claimed.add(finding_id)
        row["resolved"] = True
        if item["classification"] == "RNG":
            for seed in item["rng_integers"]:
                findings.add_seed(seed, item["rng_role"], row["source"], row["location"])
            resolved_rng += 1
        else:
            resolved_non_rng += 1

    for rule in payload["reviewed_rules"]:
        matches = [row for row in findings.records if (
            row["kind"] == rule["finding_kind"]
            and row["source"] == rule["source"]
            and row["source_sha256"] == rule["source_sha256"]
            and _rule_matches(row, rule["selector"])
        )]
        ids = sorted(str(row["finding_id"]) for row in matches)
        if (len(matches) != rule["expected_match_count"]
                or canonical_sha256(ids) != rule["expected_matches_sha256"]):
            raise ValueError(
                f"reviewed rule {rule['rule_id']!r} is stale, missing, or overbroad; "
                f"matched {len(matches)} findings"
            )
        if any(finding_id in claimed for finding_id in ids):
            raise ValueError(f"reviewed rule {rule['rule_id']!r} overlaps another resolution")
        claimed.update(ids)
        rng_map = rule["rng_integers_by_finding"]
        if rule["classification"] == "RNG" and set(rng_map) != set(ids):
            raise ValueError(
                f"reviewed RNG rule {rule['rule_id']!r} must map every exact finding id"
            )
        for row in matches:
            row["resolved"] = True
            if rule["classification"] == "RNG":
                for seed in rng_map[str(row["finding_id"])]:
                    findings.add_seed(seed, rule["rng_role"], row["source"], row["location"])
                resolved_rng += 1
            else:
                resolved_non_rng += 1

    return {
        "status": payload["status"],
        "resolved_rng": resolved_rng,
        "resolved_non_rng": resolved_non_rng,
        "unresolved": len(findings.records) - len(claimed),
        "payload_sha256": canonical_sha256(payload),
    }


def apply_excluded_output_dispositions(
        excluded_index: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require a byte-exact disposition for every output omitted from parsing."""
    entries = [dict(item) for item in excluded_index]
    expected_digest = canonical_sha256(entries)
    expected_bytes = sum(int(item["bytes"]) for item in entries)
    if payload is None:
        return {
            "status": "NOT_SUPPLIED",
            "resolved": 0,
            "unresolved": len(entries),
            "coverage_mode": None,
        }

    index_review = payload["excluded_output_index_review"]
    if index_review is not None:
        if (index_review["expected_file_count"] != len(entries)
                or index_review["expected_total_bytes"] != expected_bytes
                or index_review["expected_index_sha256"] != expected_digest):
            raise ValueError(
                "excluded-output index review is stale: current count, bytes, or "
                "content-index SHA-256 differs"
            )
        return {
            "status": payload["status"],
            "resolved": len(entries),
            "unresolved": 0,
            "coverage_mode": "EXACT_INDEX_REVIEW",
        }

    current = {str(item["path"]): dict(item) for item in entries}
    claimed: set[str] = set()
    for item in payload["excluded_output_dispositions"]:
        source = str(item["path"])
        expected = current.get(source)
        identity = {
            "path": source,
            "bytes": item["bytes"],
            "kind": item["kind"],
            "sha256": item["sha256"],
        }
        if expected != identity:
            raise ValueError(
                f"excluded-output disposition is stale, missing, or extra: {source}"
            )
        claimed.add(source)
    missing = sorted(set(current) - claimed)
    if payload["status"] == "REVIEWED" and missing:
        raise ValueError(
            "reviewed resolution document lacks excluded-output dispositions for: "
            + ", ".join(missing[:20])
            + (f" (and {len(missing) - 20} more)" if len(missing) > 20 else "")
        )
    return {
        "status": payload["status"],
        "resolved": len(claimed),
        "unresolved": len(missing),
        "coverage_mode": "EXACT_PER_FILE" if claimed else None,
    }


def load_candidate_blocks(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = strict_json_load(path)
    _require_exact_keys(
        payload,
        {"schema", "discovery", "confirmation", "proposal_rng"},
        "candidate seed blocks",
    )
    if payload.get("schema") != CANDIDATE_SCHEMA:
        raise ValueError(f"candidate seed schema must be {CANDIDATE_SCHEMA!r}")
    discovery = payload["discovery"]
    confirmation = payload["confirmation"]
    proposal = payload["proposal_rng"]
    for field, values in (("discovery", discovery), ("confirmation", confirmation)):
        if (not isinstance(values, list)
                or any(isinstance(value, bool) or not isinstance(value, int)
                       for value in values)):
            raise ValueError(f"candidate {field} must be an explicit integer array")
    if isinstance(proposal, bool) or not isinstance(proposal, int):
        raise ValueError("candidate proposal_rng must be one explicit integer")
    if not discovery or not confirmation:
        raise ValueError("candidate discovery and confirmation blocks must be non-empty")
    if len(discovery) != len(set(discovery)) or len(confirmation) != len(set(confirmation)):
        raise ValueError("candidate discovery and confirmation blocks must contain unique integers")
    return {
        "discovery": discovery,
        "confirmation": confirmation,
        "proposal_rng": proposal,
        "sha256": canonical_sha256(payload),
        "file_sha256": sha256_file(path),
    }


def build_audit(root: Path, scope_path: Path,
                candidate_path: Path | None = None, *,
                plan_path: Path | None = None,
                resolution_path: Path | None = None,
                generated_utc: str | None = None) -> dict[str, Any]:
    scope = load_scope(scope_path, root=root)
    candidates = load_candidate_blocks(candidate_path)
    findings, inventory = scan_scope(root, scope)
    resolution_payload = (
        load_resolutions(resolution_path, scope_sha256=sha256_file(scope_path))
        if resolution_path is not None else None
    )
    resolution_summary = apply_resolutions(findings, resolution_payload)
    excluded_output_summary = apply_excluded_output_dispositions(
        inventory["excluded_output_index"], resolution_payload
    )
    parsed = findings.render_parsed()
    prior = {row["seed"] for row in parsed}

    unresolved: list[str] = []
    if scope["owner_scope"]["status"] != "APPROVED":
        unresolved.append("owner has not approved the complete seed-source scope")
    if scope["source_coverage"]["status"] != "COMPLETE":
        unresolved.append("source coverage remains declared UNRESOLVED")
    for item in scope["tracked_repository"].get("explicit_exclusions", ()):
        if item["coverage_status"] != "REVIEWED_NON_SEED":
            unresolved.append(f"tracked exclusion remains unresolved: {item['path']}")
    for item in scope.get("external_execution_locations", ()):
        if item["status"] == "UNRESOLVED":
            unresolved.append(
                f"external seed-source coverage remains unresolved: {item['kind']}"
            )
    if resolution_summary["status"] == "NOT_SUPPLIED":
        unresolved.append("reviewed seed-finding resolution document was not supplied")
    elif resolution_summary["status"] != "REVIEWED":
        unresolved.append("seed-finding resolution document remains INCOMPLETE")
    if excluded_output_summary["unresolved"]:
        unresolved.append(
            f"{excluded_output_summary['unresolved']} content-bound mounted-output "
            "exclusions lack an exact reviewed disposition"
        )
    ambiguous_unresolved = findings.unresolved("ambiguous")
    unparseable_unresolved = findings.unresolved("unparseable")
    if ambiguous_unresolved:
        unresolved.append(
            f"{len(ambiguous_unresolved)} ambiguous seed-like references require review"
        )
    if unparseable_unresolved:
        unresolved.append(
            f"{len(unparseable_unresolved)} selected or tracked references were unparseable"
        )
    candidate_relative = None
    if candidate_path is not None:
        candidate_relative = (
            candidate_path.relative_to(root).as_posix()
            if root.resolve() == ROOT.resolve() and candidate_path.is_relative_to(root)
            else str(candidate_path)
        )
    candidate_summary: dict[str, Any]
    overlap_rows: list[dict[str, Any]] = []
    if candidates is None:
        candidate_summary = {
            "status": "NOT_SUPPLIED", "path": None, "file_sha256": None,
            "discovery": [], "confirmation": [], "proposal_rng": None,
            "sha256": None,
        }
        disjointness = {"status": "NOT_EVALUATED", "reason":
                        "candidate discovery, confirmation and proposal blocks were not supplied"}
        unresolved.append("candidate seed blocks were not supplied; disjointness is not evaluated")
    else:
        candidate_summary = {
            "status": "SUPPLIED", "path": candidate_relative, **candidates,
        }
        discovery = set(candidates["discovery"])
        confirmation = set(candidates["confirmation"])
        proposal = {candidates["proposal_rng"]}
        checks = {
            "discovery_vs_confirmation": sorted(discovery & confirmation),
            "proposal_vs_discovery": sorted(proposal & discovery),
            "proposal_vs_confirmation": sorted(proposal & confirmation),
            "discovery_vs_prior": sorted(discovery & prior),
            "confirmation_vs_prior": sorted(confirmation & prior),
            "proposal_vs_prior": sorted(proposal & prior),
        }
        for comparison, seeds in checks.items():
            if seeds:
                overlap_rows.append({"comparison": comparison, "seeds": seeds})
        proof_ready = not unresolved and not overlap_rows
        disjointness = {
            "status": "PROVEN" if proof_ready else "COMPUTED_NOT_PROVEN",
            "checks": checks,
            "reason": None if proof_ready else (
                "overlap found" if overlap_rows else
                "candidate comparisons were computed, but source-scope completeness is unresolved"
            ),
        }
        if overlap_rows:
            unresolved.append("candidate seed blocks overlap internally or with parsed prior seeds")

    plan: nc.FrozenPlan | None = None
    plan_error: str | None = None
    if plan_path is None:
        plan_error = "signed production plan was not supplied"
    else:
        try:
            plan = nc.FrozenPlan.load(plan_path, root=ROOT)
        except (OSError, ValueError, nc.ContractError, json.JSONDecodeError) as exc:
            plan_error = f"signed production plan is invalid: {exc}"
    plan_matches = False
    if plan is not None and candidates is not None:
        plan_matches = (
            tuple(candidates["discovery"]) == plan.seeds.discovery
            and tuple(candidates["confirmation"]) == plan.seeds.confirmation
            and int(candidates["proposal_rng"]) == plan.seeds.proposal_rng
        )
        if not plan_matches:
            plan_error = "candidate seed blocks do not exactly match the signed production plan"
    if plan_error:
        unresolved.append(plan_error)
    if not valid_utc_timestamp(generated_utc):
        unresolved.append("final audit generation timestamp is not a valid ISO-8601 UTC value")

    passed = (
        candidates is not None
        and disjointness["status"] == "PROVEN"
        and plan_matches
        and not unresolved
    )
    scope_relative = (
        scope_path.relative_to(root).as_posix()
        if root.resolve() == ROOT.resolve() and scope_path.is_relative_to(root)
        else str(scope_path)
    )
    resolution_relative = None
    if resolution_path is not None:
        resolution_relative = (
            resolution_path.relative_to(root).as_posix()
            if root.resolve() == ROOT.resolve() and resolution_path.is_relative_to(root)
            else str(resolution_path)
        )
    artifact = {
        "schema": SCHEMA,
        "status": "FINAL" if passed else "INCOMPLETE",
        "production_eligible": passed,
        "passed": passed,
        "deterministic": True,
        "generated_utc": generated_utc,
        "command": (
            f"python tools/audit_robust_nroy_seeds.py --scope {scope_relative}"
            + (f" --resolutions {resolution_relative}" if resolution_relative else "")
            + (f" --candidate-blocks {candidate_relative}" if candidate_relative else "")
            + (f" --plan {plan_path}" if plan_path else "")
            + (f" --generated-utc {generated_utc}" if generated_utc else "")
        ),
        "plan_path": str(plan_path) if plan_path is not None else None,
        "plan_sha256": plan.plan_sha256 if plan is not None else None,
        "scope_manifest": {
            "path": scope_relative,
            "sha256": sha256_file(scope_path),
            "owner_scope_status": scope["owner_scope"]["status"],
            "source_coverage_status": scope["source_coverage"]["status"],
        },
        "source_inventory": inventory,
        "finding_resolutions": {
            "path": resolution_relative,
            "file_sha256": sha256_file(resolution_path)
            if resolution_path is not None else None,
            **resolution_summary,
            "excluded_output": excluded_output_summary,
        },
        "parsed_prior_seeds": parsed,
        "parsed_prior_seed_count": len(parsed),
        "ambiguous_references": {
            "detected_count": findings.ambiguous_total,
            "count": len(ambiguous_unresolved),
            "resolved_count": findings.ambiguous_total - len(ambiguous_unresolved),
            "examples": findings.render_examples(
                ambiguous_unresolved, findings.example_limit
            ),
            "examples_truncated": max(0, len(ambiguous_unresolved) -
                                      findings.example_limit),
        },
        "unparseable_references": {
            "detected_count": findings.unparseable_total,
            "count": len(unparseable_unresolved),
            "resolved_count": findings.unparseable_total - len(unparseable_unresolved),
            "examples": findings.render_examples(
                unparseable_unresolved, findings.example_limit
            ),
            "examples_truncated": max(0, len(unparseable_unresolved) -
                                      findings.example_limit),
        },
        "candidate_blocks": candidate_summary,
        "disjointness": disjointness,
        "overlaps": overlap_rows,
        "unresolved": unresolved,
        "limitations": list(scope.get("limitations", ())),
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, type=Path)
    parser.add_argument(
        "--resolutions", type=Path,
        help="reviewed, scope-hash-bound classifications for ambiguous findings",
    )
    parser.add_argument("--candidate-blocks", type=Path)
    parser.add_argument(
        "--plan", type=Path,
        help="fully signed production plan whose exact candidate blocks must match",
    )
    parser.add_argument(
        "--generated-utc",
        help="explicit ISO-8601 evidence timestamp; required for a production-eligible FINAL",
    )
    parser.add_argument("--out", type=Path, help="write the deterministic JSON artifact")
    parser.add_argument("--check", type=Path,
                        help="compare the deterministic rendering with an existing artifact")
    parser.add_argument("--require-pass", action="store_true",
                        help="return exit 2 unless the artifact proves disjointness")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = build_audit(
            ROOT,
            args.scope.resolve(),
            args.candidate_blocks.resolve() if args.candidate_blocks else None,
            plan_path=args.plan.resolve() if args.plan else None,
            resolution_path=args.resolutions.resolve() if args.resolutions else None,
            generated_utc=args.generated_utc,
        )
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"SEED AUDIT ERROR: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if args.check:
        if args.check.read_text(encoding="utf-8") != rendered:
            print(f"SEED AUDIT ERROR: artifact drift: {args.check}", file=sys.stderr)
            return 1
    elif args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.require_pass and artifact["passed"] is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
