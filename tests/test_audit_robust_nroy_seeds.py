from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import audit_robust_nroy_seeds as audit


ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / (
    "documentation/reports/calibration/robust_nroy_readiness_2026-10-15/"
    "seed_source_scope.json"
)


def _scope(*, complete: bool) -> dict:
    return {
        "schema": audit.SCOPE_SCHEMA,
        "owner_scope": {
            "status": "APPROVED" if complete else "UNRESOLVED",
            "approved_by": "Test owner" if complete else None,
            "approved_utc": "2026-10-15T00:00:00Z" if complete else None,
            "scope_statement": "All test sources are covered." if complete else "",
        },
        "source_coverage": {
            "status": "COMPLETE" if complete else "UNRESOLVED",
            "exclusions_reviewed": complete,
        },
        "tracked_repository": {"pathspecs": ["configs", "documentation"]},
        "outputs": {
            "root": "outputs",
            "json_basename_globs": ["*manifest*.json"],
            "json_lines_basename_globs": ["*results*.jsonl"],
            "text_extensions": [".md"],
            "max_scanned_file_bytes": 1024 * 1024,
        },
        "evidence_example_limit": 100,
        "parsed_seed_source_limit": 10,
        "limitations": [],
    }


def _write_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, complete: bool = True):
    (tmp_path / "outputs").mkdir()
    config = tmp_path / "configs" / "prior.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "seeds": {"discovery": [1, 2], "confirmation": [3], "proposal_rng": 9},
    }))
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(_scope(complete=complete)))
    monkeypatch.setattr(audit, "git_tracked_paths", lambda root, pathspecs: [config])
    return scope_path


def _candidate(tmp_path: Path, *, discovery=(10, 11), confirmation=(20, 21), proposal=30):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps({
        "schema": audit.CANDIDATE_SCHEMA,
        "discovery": list(discovery),
        "confirmation": list(confirmation),
        "proposal_rng": proposal,
    }))
    return path


def test_structured_scan_distinguishes_parsed_ambiguous_and_unparseable():
    findings = audit.Findings()
    audit.scan_structured({
        "replicate_seeds": "101;102",
        "proposal_rng": {"seed": 103},
        "trajectory_budget": {"seeds": 15},
        "proposal_rng_state": {"state": 999999999999999999},
        "seed_prevalence": 0.004,
        "random_seed": "range(200, 205)",
    }, source="fixture.json", findings=findings)

    assert [(row["seed"], row["roles"]) for row in findings.render_parsed()] == [
        (101, ["simulation"]),
        (102, ["simulation"]),
        (103, ["proposal_rng"]),
    ]
    assert findings.ambiguous_total == 2
    assert {row["location"] for row in findings.ambiguous_examples} == {
        "/seed_prevalence", "/trajectory_budget/seeds",
    }
    assert findings.unparseable_total == 1
    assert "range(200, 205)" in findings.unparseable_examples[0]["detail"]


def test_candidate_blocks_are_required_before_disjointness_is_evaluated(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    result = audit.build_audit(tmp_path, scope_path)
    assert result["passed"] is False
    assert result["candidate_blocks"]["status"] == "NOT_SUPPLIED"
    assert result["disjointness"]["status"] == "NOT_EVALUATED"


def test_complete_approved_scope_and_disjoint_candidates_can_prove_gate(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    candidate_path = _candidate(tmp_path)
    first = audit.build_audit(tmp_path, scope_path, candidate_path)
    second = audit.build_audit(tmp_path, scope_path, candidate_path)

    assert first == second
    assert first["passed"] is True
    assert first["status"] == "PASS"
    assert first["disjointness"]["status"] == "PROVEN"
    assert first["overlaps"] == []
    assert first["generated_utc"] is None


def test_overlap_is_computed_but_never_proven(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    candidate_path = _candidate(tmp_path, discovery=(3, 10), proposal=20)
    result = audit.build_audit(tmp_path, scope_path, candidate_path)

    assert result["passed"] is False
    assert result["disjointness"]["status"] == "COMPUTED_NOT_PROVEN"
    assert result["disjointness"]["checks"]["discovery_vs_prior"] == [3]
    assert result["disjointness"]["checks"]["proposal_vs_confirmation"] == [20]


def test_unresolved_owner_and_coverage_fail_closed_even_with_candidates(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch, complete=False)
    result = audit.build_audit(tmp_path, scope_path, _candidate(tmp_path))
    assert result["passed"] is False
    assert result["disjointness"]["status"] == "COMPUTED_NOT_PROVEN"
    assert any("owner has not approved" in item for item in result["unresolved"])
    assert any("source coverage" in item for item in result["unresolved"])


def test_mounted_output_metadata_and_seed_csv_are_read_without_raw_json(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    outputs = tmp_path / "outputs"
    (outputs / "run_manifest.json").write_text(json.dumps({"seed": 40}))
    (outputs / "candidate_001.json").write_text(json.dumps({"seed": 999}))
    (outputs / "rows.csv").write_text("replicate_seeds,value\n41;42,x\n")

    result = audit.build_audit(tmp_path, scope_path, _candidate(tmp_path))
    parsed = {row["seed"] for row in result["parsed_prior_seeds"]}
    assert {1, 2, 3, 9, 40, 41, 42} <= parsed
    assert 999 not in parsed
    assert result["source_inventory"]["counts_by_kind"]["output:excluded"] == 1


def test_repository_scope_is_explicitly_unresolved():
    scope = audit.load_scope(SCOPE)
    assert scope["owner_scope"]["status"] == "UNRESOLVED"
    assert scope["source_coverage"]["status"] == "UNRESOLVED"
    assert scope["outputs"]["root"] == "outputs"
    assert "private data" in scope["outputs"]["root_scope"]
