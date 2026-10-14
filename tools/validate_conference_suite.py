#!/usr/bin/env python3
"""Validate a completed conference suite and its calibration provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class ValidationError(ValueError):
    pass


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise ValidationError(message)
    checks.append(message)


def validate(repo: Path, suite: Path, calibration: Path) -> dict:
    repo, suite, calibration = repo.resolve(), suite.resolve(), calibration.resolve()
    checks: list[str] = []
    _require((suite / "SUITE_STATUS.txt").read_text().startswith("COMPLETE"),
             "supervisor status COMPLETE", checks)

    case_summary = {}
    for case in ("break_2008", "break_2010"):
        root = calibration / "cases" / case / "calibration"
        search = _read(root / "search/manifest.json")
        confirm = _read(root / "confirmation/manifest.json")
        sb, cb = search["budget"], confirm["budget"]
        _require(search["status"] == "SEARCH_COMPLETE", f"{case} search complete", checks)
        _require(sb["finished"] == sb["cap"] and sb["failed"] == 0 and
                 sb["lost_in_flight"] == 0, f"{case} search budget complete", checks)
        _require(confirm["status"] == "CONFIRMATION_COMPLETE",
                 f"{case} confirmation complete", checks)
        _require(cb["finished"] == cb["cap"] and cb["failed"] == 0 and
                 cb["lost_in_flight"] == 0, f"{case} confirmation budget complete", checks)
        _require(confirm["n_confirmation_primary_pass"] > 0,
                 f"{case} has confirmed primary passers", checks)
        case_summary[case] = {
            "search_finished": sb["finished"],
            "confirmation_finished": cb["finished"],
            "confirmed_primary_passers": confirm["n_confirmation_primary_pass"],
        }

    comparison = _read(suite / "calibration_comparison/manifest.json")
    _require(comparison["status"] == "COMPARISON_COMPLETE", "comparison complete", checks)
    for case_files in comparison["source_files"].values():
        for item in case_files.values():
            path = repo / item["path"]
            _require(path.is_file() and _sha(path) == item["sha256"],
                     f"comparison source verified: {item['path']}", checks)

    binding_root = suite / "bound_models"
    binding = _read(binding_root / "binding_manifest.json")
    _require(binding["status"] == "CALIBRATION_MODELS_BOUND", "models bound", checks)
    for item in binding["models"].values():
        path = binding_root / item["file"]
        _require(path.is_file() and _sha(path) == item["sha256"],
                 f"bound model verified: {item['file']}", checks)
        _require(bool(item["confirmation_primary_pass"]),
                 f"bound model confirmed: {item['file']}", checks)

    for name, expected_scenarios in (("primary_2008", 19), ("bridge_2010", 5)):
        root = suite / f"policy_{name}"
        manifest = _read(root / "final_manifest.json")
        _require(manifest["status"] == "POLICY_MATRIX_FINALIZED",
                 f"policy {name} finalized", checks)
        _require(manifest["scenarios"] == expected_scenarios,
                 f"policy {name} scenario count", checks)
        for filename, expected in manifest["outputs"].items():
            _require(_sha(root / filename) == expected,
                     f"policy output verified: {name}/{filename}", checks)

        root = suite / f"psa_{name}"
        manifest = _read(root / "final_manifest.json")
        expected_tasks = expected_scenarios * 1000
        _require(manifest["status"] == "PSA_MATRIX_COMPLETE",
                 f"PSA {name} complete", checks)
        _require(manifest["completed_tasks"] == expected_tasks and
                 manifest["missing_tasks"] == 0 and not manifest["allow_partial"],
                 f"PSA {name} task contract", checks)
        for filename, item in manifest["outputs"].items():
            path = root / filename
            _require(_sha(path) == item["sha256"] and path.stat().st_size == item["bytes"],
                     f"PSA output verified: {name}/{filename}", checks)

    combined_root = suite / "combined"
    combined = _read(combined_root / "manifest.json")
    _require(combined["status"] == "CONFERENCE_RESULTS_BOUND",
             "conference results bound", checks)
    for relative, expected in combined["source_sha256"].items():
        _require(_sha(suite / relative) == expected,
                 f"combined source verified: {relative}", checks)
    for filename, expected in combined["output_sha256"].items():
        _require(_sha(combined_root / filename) == expected,
                 f"combined output verified: {filename}", checks)

    failures = [str(path) for path in suite.rglob("failures/*") if path.is_file()]
    _require(not failures, "no retained failure artifacts", checks)
    return {
        "status": "CONFERENCE_SUITE_VALIDATED",
        "validated_utc": datetime.now(timezone.utc).isoformat(),
        "suite": str(suite),
        "calibration": str(calibration),
        "checks_passed": len(checks),
        "failure_artifacts": failures,
        "calibration_summary": case_summary,
        "policy_scenarios": {"primary_2008": 19, "bridge_2010": 5},
        "psa_tasks": {"primary_2008": 19000, "bridge_2010": 5000},
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = validate(args.repo, args.suite, args.calibration)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, args.output)
        print(json.dumps({k: payload[k] for k in (
            "status", "validated_utc", "checks_passed", "calibration_summary",
            "policy_scenarios", "psa_tasks",
        )}, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, KeyError, ValidationError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
