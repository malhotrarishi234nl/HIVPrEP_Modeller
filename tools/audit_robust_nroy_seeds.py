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
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "robust_nroy_seed_audit/1.0.0"
SCOPE_SCHEMA = "robust_nroy_seed_source_scope/1.0.0"
CANDIDATE_SCHEMA = "robust_nroy_candidate_seed_blocks/1.0.0"

INTEGER = re.compile(r"^[+-]?\d+$")
SPLIT_INTEGER_LIST = re.compile(r"^[+-]?\d+(?:\s*[,;]\s*[+-]?\d+)+$")
TEXT_SEED = re.compile(r"\bseed(?:s|ed|ing)?\b", re.IGNORECASE)

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

    def _add_example(self, kind: str, source: str, location: str, detail: str) -> None:
        attr_total = f"{kind}_total"
        attr_examples = f"{kind}_examples"
        setattr(self, attr_total, getattr(self, attr_total) + 1)
        examples = getattr(self, attr_examples)
        if len(examples) < self.example_limit:
            examples.append({"source": source, "location": location, "detail": detail})

    def add_ambiguous(self, source: str, location: str, detail: str) -> None:
        self._add_example("ambiguous", source, location, detail)

    def add_unparseable(self, source: str, location: str, detail: str) -> None:
        self._add_example("unparseable", source, location, detail)

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
                    )
                continue
            if _is_seed_like(child_parts) and role is None and not isinstance(child, Mapping):
                findings.add_ambiguous(
                    source, _json_pointer(child_parts),
                    f"seed-like field has no declared RNG semantics: {_preview(child)}",
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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        findings.add_unparseable(relative, "/", f"JSON parse failed: {exc}")
        return
    scan_structured(payload, source=relative, findings=findings)


def scan_json_lines(path: Path, relative: str, findings: Findings) -> None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    findings.add_unparseable(
                        relative, f"line:{line_number}", f"JSON-line parse failed: {exc}"
                    )
                    continue
                scan_structured(payload, source=relative, findings=findings,
                                parts=(f"line:{line_number}",))
    except (OSError, UnicodeDecodeError) as exc:
        findings.add_unparseable(relative, "/", f"JSON-lines read failed: {exc}")


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
                            )
            return True
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        findings.add_unparseable(relative, "/", f"CSV read failed: {exc}")
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
                    )
    except (OSError, UnicodeDecodeError) as exc:
        findings.add_unparseable(relative, "/", f"text read failed: {exc}")


def git_tracked_paths(root: Path, pathspecs: Sequence[str]) -> list[Path]:
    command = ["git", "ls-files", "-z", "--", *pathspecs]
    completed = subprocess.run(command, cwd=root, check=True, capture_output=True)
    return [root / item.decode("utf-8")
            for item in completed.stdout.split(b"\0") if item]


def _parser_kind(path: Path, *, output: bool, output_rules: Mapping[str, Any]) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if not output:
        if name.endswith(".json") or name.endswith(".json.template"):
            return "json"
        if suffix == ".csv":
            return "csv_text"
        if name.endswith(".md.template") or suffix in {
                ".md", ".txt", ".html", ".yaml", ".yml", ".toml"}:
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


def scan_scope(root: Path, scope: Mapping[str, Any]) -> tuple[Findings, dict[str, Any]]:
    findings = Findings(
        example_limit=int(scope.get("evidence_example_limit", 2000)),
        source_limit=int(scope.get("parsed_seed_source_limit", 40)),
    )
    inventory: list[dict[str, Any]] = []
    scanned_hashes: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    max_bytes = int(scope["outputs"]["max_scanned_file_bytes"])

    tracked = git_tracked_paths(root, scope["tracked_repository"]["pathspecs"])
    for path in sorted(tracked):
        relative = path.relative_to(root).as_posix()
        kind = _parser_kind(path, output=False, output_rules={})
        inventory.append(_path_record(root, path, f"tracked:{kind}"))
        counts[f"tracked:{kind}"] = counts.get(f"tracked:{kind}", 0) + 1
        if kind == "json":
            scan_json(path, relative, findings)
        elif kind == "csv_text":
            scan_csv(path, relative, findings, force_text=True)
        elif kind == "text":
            scan_text(path, relative, findings)
        else:
            findings.add_unparseable(
                relative, "/", "tracked config/document format is not parsed by this scanner"
            )
            continue
        scanned_hashes.append({"path": relative, "sha256": sha256_file(path)})

    output_root = root / str(scope["outputs"]["root"])
    if not output_root.exists():
        findings.add_unparseable(
            str(scope["outputs"]["root"]), "/", "declared mounted output root does not exist"
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
                continue
            if record["bytes"] > max_bytes:
                findings.add_unparseable(
                    relative, "/",
                    f"selected source exceeds max_scanned_file_bytes={max_bytes}",
                )
                continue
            if kind == "csv_probe":
                if not scan_csv(path, relative, findings):
                    record["kind"] = "output:csv_without_seed_column"
                    counts["output:csv_probe"] -= 1
                    counts["output:csv_without_seed_column"] = \
                        counts.get("output:csv_without_seed_column", 0) + 1
                    continue
            elif kind == "json":
                scan_json(path, relative, findings)
            elif kind == "json_lines":
                scan_json_lines(path, relative, findings)
            elif kind == "text":
                scan_text(path, relative, findings)
            scanned_hashes.append({"path": relative, "sha256": sha256_file(path)})

    inventory_digest = canonical_sha256(inventory)
    scanned_digest = canonical_sha256(scanned_hashes)
    excluded_output = sum(value for key, value in counts.items()
                          if key in {"output:excluded", "output:csv_without_seed_column"})
    return findings, {
        "counts_by_kind": dict(sorted((key, value) for key, value in counts.items() if value)),
        "inventory_sha256": inventory_digest,
        "scanned_content_index_sha256": scanned_digest,
        "scanned_files": len(scanned_hashes),
        "excluded_output_files": excluded_output,
    }


def load_scope(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
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
    if payload["source_coverage"]["status"] == "COMPLETE" and \
            payload["source_coverage"].get("exclusions_reviewed") is not True:
        raise ValueError("COMPLETE source coverage requires exclusions_reviewed=true")
    return payload


def load_candidate_blocks(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != CANDIDATE_SCHEMA:
        raise ValueError(f"candidate seed schema must be {CANDIDATE_SCHEMA!r}")
    discovery, rejected_d = _parse_integer_values(payload.get("discovery"))
    confirmation, rejected_c = _parse_integer_values(payload.get("confirmation"))
    proposal, rejected_p = _parse_integer_values(payload.get("proposal_rng"))
    if rejected_d or rejected_c or rejected_p or len(proposal) != 1:
        raise ValueError("candidate blocks must contain explicit integer lists and one proposal_rng")
    if not discovery or not confirmation:
        raise ValueError("candidate discovery and confirmation blocks must be non-empty")
    if len(discovery) != len(set(discovery)) or len(confirmation) != len(set(confirmation)):
        raise ValueError("candidate discovery and confirmation blocks must contain unique integers")
    return {"discovery": discovery, "confirmation": confirmation, "proposal_rng": proposal[0],
            "sha256": sha256_file(path)}


def build_audit(root: Path, scope_path: Path,
                candidate_path: Path | None = None) -> dict[str, Any]:
    scope = load_scope(scope_path)
    candidates = load_candidate_blocks(candidate_path)
    findings, inventory = scan_scope(root, scope)
    parsed = findings.render_parsed()
    prior = {row["seed"] for row in parsed}

    unresolved: list[str] = []
    if scope["owner_scope"]["status"] != "APPROVED":
        unresolved.append("owner has not approved the complete seed-source scope")
    if scope["source_coverage"]["status"] != "COMPLETE":
        unresolved.append("source coverage remains declared UNRESOLVED")
    if findings.ambiguous_total:
        unresolved.append(
            f"{findings.ambiguous_total} ambiguous seed-like references require review"
        )
    if findings.unparseable_total:
        unresolved.append(
            f"{findings.unparseable_total} selected or tracked references were unparseable"
        )
    if inventory["excluded_output_files"] and not (
            scope["source_coverage"]["status"] == "COMPLETE" and
            scope["source_coverage"].get("exclusions_reviewed") is True):
        unresolved.append(
            f"{inventory['excluded_output_files']} mounted-output files were inventoried but not "
            "content-scanned under the declared rules"
        )

    candidate_summary: dict[str, Any]
    overlap_rows: list[dict[str, Any]] = []
    if candidates is None:
        candidate_summary = {"status": "NOT_SUPPLIED", "discovery": [],
                             "confirmation": [], "proposal_rng": None, "sha256": None}
        disjointness = {"status": "NOT_EVALUATED", "reason":
                        "candidate discovery, confirmation and proposal blocks were not supplied"}
        unresolved.append("candidate seed blocks were not supplied; disjointness is not evaluated")
    else:
        candidate_summary = {"status": "SUPPLIED", **candidates}
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

    passed = candidates is not None and disjointness["status"] == "PROVEN"
    scope_relative = scope_path.relative_to(root).as_posix() if scope_path.is_relative_to(root) \
        else scope_path.name
    artifact = {
        "schema": SCHEMA,
        "status": "PASS" if passed else "INCOMPLETE",
        "production_eligible": False,
        "passed": passed,
        "deterministic": True,
        "generated_utc": None,
        "command": (
            f"python tools/audit_robust_nroy_seeds.py --scope {scope_relative}"
            + (f" --candidate-blocks {candidate_path.name}" if candidate_path else "")
        ),
        "scope_manifest": {
            "path": scope_relative,
            "sha256": sha256_file(scope_path),
            "owner_scope_status": scope["owner_scope"]["status"],
            "source_coverage_status": scope["source_coverage"]["status"],
        },
        "source_inventory": inventory,
        "parsed_prior_seeds": parsed,
        "parsed_prior_seed_count": len(parsed),
        "ambiguous_references": {
            "count": findings.ambiguous_total,
            "examples": findings.ambiguous_examples,
            "examples_truncated": max(0, findings.ambiguous_total -
                                      len(findings.ambiguous_examples)),
        },
        "unparseable_references": {
            "count": findings.unparseable_total,
            "examples": findings.unparseable_examples,
            "examples_truncated": max(0, findings.unparseable_total -
                                      len(findings.unparseable_examples)),
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
    parser.add_argument("--candidate-blocks", type=Path)
    parser.add_argument("--out", type=Path, help="write the deterministic JSON artifact")
    parser.add_argument("--check", type=Path,
                        help="compare the deterministic rendering with an existing artifact")
    parser.add_argument("--require-pass", action="store_true",
                        help="return exit 2 unless the artifact proves disjointness")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = build_audit(ROOT, args.scope.resolve(),
                               args.candidate_blocks.resolve() if args.candidate_blocks else None)
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
