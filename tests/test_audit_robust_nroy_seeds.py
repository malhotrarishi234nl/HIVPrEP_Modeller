from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import audit_robust_nroy_seeds as audit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import nroy_fixtures as fx  # noqa: E402

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
        },
        "tracked_repository": {
            "pathspecs": ["."],
            "text_extensions": [".md", ".py", ".sh", ".txt"],
            "explicit_exclusions": [],
        },
        "external_execution_locations": [
            {
                "kind": kind,
                "status": "REVIEWED_NONE" if complete else "UNRESOLVED",
                "coverage_decision": "Covered by test fixture." if complete else "Review needed.",
                "evidence": [],
                "reviewer": "Test owner" if complete else None,
                "reviewed_utc": "2026-10-15T00:00:00Z" if complete else None,
            }
            for kind in ("REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD")
        ],
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


def _write_external_reviews(tmp_path: Path, scope: dict) -> None:
    for item in scope["external_execution_locations"]:
        if item["status"] == "UNRESOLVED":
            continue
        kind = item["kind"]
        path = tmp_path / f"external_review_{kind.lower()}.json"
        path.write_text(json.dumps({
            "schema": audit.EXTERNAL_REVIEW_SCHEMA,
            "location_kind": kind,
            "review_status": item["status"],
            "reviewer": item["reviewer"],
            "reviewed_utc": item["reviewed_utc"],
            "searched_locations": [f"isolated-{kind.lower()}-fixture"],
            "searched_accounts_or_owners": ["fixture-owner"],
            "search_method": "Inspected the isolated fixture inventory.",
            "search_started_utc": "2026-10-15T00:00:00Z",
            "search_completed_utc": "2026-10-15T00:00:00Z",
            "indexed_sources": [],
            "no_additional_source_basis": "The isolated fixture contains no other source.",
        }))
        item["evidence"] = [{
            "path": path.name,
            "sha256": audit.sha256_file(path),
            "description": f"Typed {kind} review record.",
        }]


def _write_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, complete: bool = True):
    (tmp_path / "outputs").mkdir()
    config = tmp_path / "configs" / "prior.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "seeds": {"discovery": [1, 2], "confirmation": [3], "proposal_rng": 9},
    }))
    scope_path = tmp_path / "scope.json"
    payload = _scope(complete=complete)
    _write_external_reviews(tmp_path, payload)
    scope_path.write_text(json.dumps(payload))
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


def _resolution_document(scope_path: Path, *, exact=(), rules=(), status="REVIEWED"):
    return {
        "schema": audit.RESOLUTION_SCHEMA,
        "status": status,
        "scope_manifest_sha256": audit.sha256_file(scope_path),
        "exact_resolutions": list(exact),
        "reviewed_rules": list(rules),
        "excluded_output_dispositions": [],
        "excluded_output_index_review": None,
        "limitations": [],
    }


def _exact(example: dict, *, classification: str, integers=(), role=None):
    return {
        "finding_kind": "ambiguous",
        "source": example["source"],
        "location": example["location"],
        "source_sha256": example["source_sha256"],
        "value_sha256": example["value_sha256"],
        "classification": classification,
        "rng_role": role,
        "rng_integers": list(integers),
        "reviewer": "Test reviewer",
        "reviewed_utc": "2026-10-15T12:00:00Z",
        "rationale": "The fixture establishes the intended semantics.",
    }


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
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(scope_path)))
    first = audit.build_audit(
        tmp_path, scope_path, candidate_path, resolution_path=resolution_path
    )
    second = audit.build_audit(
        tmp_path, scope_path, candidate_path, resolution_path=resolution_path
    )

    assert first == second
    assert first["passed"] is False
    assert first["status"] == "INCOMPLETE"
    assert first["production_eligible"] is False
    assert first["disjointness"]["status"] == "PROVEN"
    assert first["overlaps"] == []
    assert first["generated_utc"] is None
    assert "signed production plan was not supplied" in first["unresolved"]


def test_active_signed_plan_candidate_declarations_do_not_self_collide(
        tmp_path, monkeypatch):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = audit.nc.FrozenPlan.load(plan_path, root=ROOT)
    candidate_path = _candidate(
        tmp_path,
        discovery=plan.seeds.discovery,
        confirmation=plan.seeds.confirmation,
        proposal=plan.seeds.proposal_rng,
    )
    (tmp_path / "outputs").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", plan_path.name], cwd=tmp_path, check=True)
    scope_payload = _scope(complete=True)
    _write_external_reviews(tmp_path, scope_payload)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope_payload))
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(scope_path)))

    current = audit.build_audit(
        tmp_path,
        scope_path,
        candidate_path,
        plan_path=plan_path,
        resolution_path=resolution_path,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert current["passed"] is False
    assert current["overlaps"] == []
    for comparison in (
        "discovery_vs_prior", "confirmation_vs_prior", "proposal_vs_prior"
    ):
        assert current["disjointness"]["checks"][comparison] == []
    assert current["source_inventory"][
        "active_plan_candidate_declaration_exclusion"
    ] == {
        "path": plan_path.name,
        "sha256": audit.sha256_file(plan_path),
        "excluded_json_pointers": list(audit.ACTIVE_PLAN_CANDIDATE_POINTERS),
        "reason": (
            "candidate declarations in this exact active signed plan are proposed "
            "future use, not evidence of prior RNG use"
        ),
    }

    monkeypatch.chdir(tmp_path)
    generated_from_relative_plan = audit.build_audit(
        tmp_path,
        scope_path,
        candidate_path,
        plan_path=Path(plan_path.name),
        resolution_path=resolution_path,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert generated_from_relative_plan == current
    assert generated_from_relative_plan["plan_path"] == str(plan_path.resolve())

    historical = tmp_path / "historical_plan.json"
    historical.write_bytes(plan_path.read_bytes())
    subprocess.run(["git", "add", historical.name], cwd=tmp_path, check=True)
    collided = audit.build_audit(
        tmp_path,
        scope_path,
        candidate_path,
        plan_path=plan_path,
        resolution_path=resolution_path,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert collided["passed"] is False
    assert collided["disjointness"]["status"] == "COMPUTED_NOT_PROVEN"
    assert collided["disjointness"]["checks"]["discovery_vs_prior"] == list(
        plan.seeds.discovery
    )
    assert collided["disjointness"]["checks"]["confirmation_vs_prior"] == list(
        plan.seeds.confirmation
    )
    assert collided["disjointness"]["checks"]["proposal_vs_prior"] == [
        plan.seeds.proposal_rng
    ]


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
    assert scope["tracked_repository"]["pathspecs"] == ["."]
    assert {item["kind"] for item in scope["external_execution_locations"]} == {
        "REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD",
    }


def test_exact_non_rng_resolution_requires_exact_hash_bound_match(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    config = tmp_path / "configs" / "prior.json"
    config.write_text(json.dumps({"seed_prevalence": 0.004}))
    first = audit.build_audit(tmp_path, scope_path)
    example = first["ambiguous_references"]["examples"][0]
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path, exact=[_exact(example, classification="NON_RNG")]
    )))

    result = audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)
    assert result["ambiguous_references"]["detected_count"] == 1
    assert result["ambiguous_references"]["count"] == 0
    assert result["finding_resolutions"]["resolved_non_rng"] == 1
    assert result["finding_resolutions"]["file_sha256"] == audit.sha256_file(
        resolution_path
    )
    assert result["finding_resolutions"]["path"] == str(resolution_path)

    payload = json.loads(resolution_path.read_text())
    payload["exact_resolutions"][0]["value_sha256"] = "0" * 64
    resolution_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="stale or missing"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_resolved_rng_integer_joins_prior_seed_collision_check(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    config = tmp_path / "configs" / "prior.json"
    config.write_text(json.dumps({"legacy_seed_label": "legacy RNG 444"}))
    first = audit.build_audit(tmp_path, scope_path)
    example = first["ambiguous_references"]["examples"][0]
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path,
        exact=[_exact(example, classification="RNG", integers=[444], role="simulation")],
    )))

    result = audit.build_audit(
        tmp_path, scope_path, _candidate(tmp_path, discovery=(444, 445)),
        resolution_path=resolution_path,
    )
    assert result["disjointness"]["checks"]["discovery_vs_prior"] == [444]
    assert any(row["seed"] == 444 for row in result["parsed_prior_seeds"])


def test_duplicate_exact_resolution_fails_closed(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    config = tmp_path / "configs" / "prior.json"
    config.write_text(json.dumps({"seed_prevalence": 0.004}))
    example = audit.build_audit(tmp_path, scope_path)["ambiguous_references"]["examples"][0]
    decision = _exact(example, classification="NON_RNG")
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path, exact=[decision, decision]
    )))
    with pytest.raises(ValueError, match="duplicate exact resolution"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_reviewed_rule_rejects_wildcards_and_stale_match_digest(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    source = tmp_path / "documentation" / "prior.md"
    source.parent.mkdir()
    source.write_text("seed reference one\nseed reference two\n")
    monkeypatch.setattr(audit, "git_tracked_paths", lambda root, pathspecs: [source])
    first = audit.build_audit(tmp_path, scope_path)
    examples = first["ambiguous_references"]["examples"]
    base_rule = {
        "rule_id": "review-lines-1-2",
        "finding_kind": "ambiguous",
        "source": "documentation/prior.md",
        "source_sha256": examples[0]["source_sha256"],
        "selector": {"type": "line_range", "start_line": 1, "end_line": 2},
        "expected_match_count": 2,
        "expected_matches_sha256": audit.canonical_sha256(sorted(
            item["finding_id"] for item in examples
        )),
        "classification": "NON_RNG",
        "rng_role": None,
        "rng_integers_by_finding": {},
        "reviewer": "Test reviewer",
        "reviewed_utc": "2026-10-15T12:00:00Z",
        "rationale": "Both exact lines are documentation, not RNG inputs.",
    }
    resolution_path = tmp_path / "resolutions.json"
    wildcard_rule = dict(base_rule, source="documentation/*.md")
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path, rules=[wildcard_rule]
    )))
    with pytest.raises(ValueError, match="without wildcards"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)

    stale_rule = dict(base_rule, expected_matches_sha256="f" * 64)
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path, rules=[stale_rule]
    )))
    with pytest.raises(ValueError, match="stale, missing, or overbroad"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_unresolved_findings_remain_blockers_with_partial_review(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    config = tmp_path / "configs" / "prior.json"
    config.write_text(json.dumps({"seed_alpha": 0.1, "seed_beta": 0.2}))
    first = audit.build_audit(tmp_path, scope_path)
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(_resolution_document(
        scope_path,
        exact=[_exact(first["ambiguous_references"]["examples"][0],
                      classification="NON_RNG")],
    )))
    result = audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)
    assert result["ambiguous_references"]["count"] == 1
    assert result["passed"] is False
    assert any("ambiguous seed-like" in item for item in result["unresolved"])


@pytest.mark.parametrize("text", [
    '{"seed": 1, "seed": 2}',
    '{"seed": NaN}',
    '{"seed": Infinity}',
    '{"seed": -Infinity}',
    '{"seed": 1e999}',
])
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(text):
    with pytest.raises(ValueError):
        audit.strict_json_loads(text)


def test_seed_control_artifacts_cannot_self_reference_scanned_sources(
    tmp_path, monkeypatch,
):
    tracked = tmp_path / "evidence" / "historical_seed_audit.json"
    tracked.parent.mkdir()
    tracked.write_text("{}")
    scope = {
        "tracked_repository": {"pathspecs": ["."]},
        "outputs": {"root": "outputs"},
    }
    monkeypatch.setattr(audit, "git_tracked_paths", lambda root, pathspecs: [tracked])

    with pytest.raises(ValueError, match="Git-tracked scanned source"):
        audit.ensure_control_path_outside_scanned_sources(
            tmp_path, scope, tracked, label="audit output"
        )
    with pytest.raises(ValueError, match="mounted-output scan"):
        audit.ensure_control_path_outside_scanned_sources(
            tmp_path,
            scope,
            tmp_path / "outputs" / "seed_audit.json",
            label="audit output",
        )
    safe = tmp_path / "evidence" / "robust_nroy" / "seed_audit.json"
    assert audit.ensure_control_path_outside_scanned_sources(
        tmp_path, scope, safe, label="audit output"
    ) == safe.resolve()


@pytest.mark.parametrize(("text", "message"), [
    ('{"seed": 1, "seed": 2}', "duplicate JSON object key"),
    ('{"seed": NaN}', "non-finite JSON numeric literal"),
])
def test_invalid_scanned_json_becomes_unresolved_not_silent(tmp_path, text, message):
    path = tmp_path / "bad.json"
    path.write_text(text)
    findings = audit.Findings()
    audit.scan_json(path, "bad.json", findings)
    assert findings.unparseable_total == 1
    assert message in findings.unparseable_examples[0]["detail"]


def test_scope_resolution_and_candidate_control_json_are_strict(tmp_path):
    scope = tmp_path / "scope.json"
    scope.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        audit.load_scope(scope)

    resolution = tmp_path / "resolution.json"
    resolution.write_text('{"schema":"x","status":NaN}')
    with pytest.raises(ValueError, match="non-finite JSON numeric literal"):
        audit.load_resolutions(resolution, scope_sha256="0" * 64)

    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"schema":"x","proposal_rng":NaN}')
    with pytest.raises(ValueError, match="non-finite JSON numeric literal"):
        audit.load_candidate_blocks(candidate)


def test_stale_tracked_exclusion_hash_is_fatal(tmp_path, monkeypatch):
    (tmp_path / "outputs").mkdir()
    source = tmp_path / "data" / "prior.bin"
    source.parent.mkdir()
    source.write_bytes(b"not a parsed format")
    scope_path = tmp_path / "scope.json"
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    payload["tracked_repository"]["explicit_exclusions"] = [{
        "path": "data/prior.bin",
        "sha256": "0" * 64,
        "reason": "Test exclusion.",
        "coverage_status": "REVIEWED_NON_SEED",
    }]
    scope_path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        audit, "git_tracked_paths", lambda root, pathspecs: [source]
    )
    with pytest.raises(ValueError, match="exclusion is stale"):
        audit.build_audit(tmp_path, scope_path)


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("configs/prior.json", '{"seed":77}'),
        ("configs/prior.csv", "seed\n77\n"),
        ("notes/prior.txt", "seed 77\n"),
    ],
)
def test_tracked_exclusion_cannot_suppress_parser_supported_seed_source(
        tmp_path, monkeypatch, relative, content):
    (tmp_path / "outputs").mkdir()
    source = tmp_path / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content)
    scope_path = tmp_path / "scope.json"
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    payload["tracked_repository"]["explicit_exclusions"] = [{
        "path": relative,
        "sha256": audit.sha256_file(source),
        "reason": "Attempted exact-hash exclusion of a parseable source.",
        "coverage_status": "REVIEWED_NON_SEED",
    }]
    scope_path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        audit, "git_tracked_paths", lambda root, pathspecs: [source]
    )

    with pytest.raises(ValueError, match="cannot suppress a parser-supported"):
        audit.build_audit(tmp_path, scope_path)


def _excluded_disposition(entry: dict) -> dict:
    return {
        **entry,
        "disposition": "REVIEWED_NON_SEED",
        "reviewer": "Test reviewer",
        "reviewed_utc": "2026-10-15T12:00:00Z",
        "rationale": "The exact file was reviewed and contains no RNG seed evidence.",
    }


def _index_review(inventory: dict) -> dict:
    return {
        "rule_id": "review-current-excluded-output-index",
        "expected_file_count": inventory["excluded_output_files"],
        "expected_total_bytes": inventory["excluded_output_bytes"],
        "expected_index_sha256": inventory["excluded_output_index_sha256"],
        "disposition": "REVIEWED_NON_SEED",
        "reviewer": "Test reviewer",
        "reviewed_utc": "2026-10-15T12:00:00Z",
        "rationale": "The exact content index was reviewed and contains no RNG seed evidence.",
    }


def test_same_size_excluded_output_byte_change_invalidates_disposition(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    excluded = tmp_path / "outputs" / "raw.bin"
    excluded.write_bytes(b"abcd")
    first = audit.build_audit(tmp_path, scope_path)
    entry = first["source_inventory"]["excluded_output_index"][0]
    resolution_path = tmp_path / "resolutions.json"
    payload = _resolution_document(scope_path, status="REVIEWED")
    payload["excluded_output_dispositions"] = [_excluded_disposition(entry)]
    resolution_path.write_text(json.dumps(payload))
    resolved = audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)
    assert resolved["finding_resolutions"]["excluded_output"]["unresolved"] == 0

    excluded.write_bytes(b"wxyz")
    with pytest.raises(ValueError, match="stale, missing, or extra"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_reviewed_output_dispositions_reject_missing_and_extra_files(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    for name in ("one.bin", "two.bin"):
        (tmp_path / "outputs" / name).write_bytes(name.encode())
    first = audit.build_audit(tmp_path, scope_path)
    entries = first["source_inventory"]["excluded_output_index"]
    resolution_path = tmp_path / "resolutions.json"
    missing = _resolution_document(scope_path, status="REVIEWED")
    missing["excluded_output_dispositions"] = [_excluded_disposition(entries[0])]
    resolution_path.write_text(json.dumps(missing))
    with pytest.raises(ValueError, match="lacks excluded-output dispositions"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)

    extra = _resolution_document(scope_path, status="REVIEWED")
    ghost = _excluded_disposition(entries[0])
    ghost["path"] = "outputs/ghost.bin"
    extra["excluded_output_dispositions"] = [
        *map(_excluded_disposition, entries), ghost,
    ]
    resolution_path.write_text(json.dumps(extra))
    with pytest.raises(ValueError, match="stale, missing, or extra"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_stale_excluded_output_index_review_is_fatal(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    (tmp_path / "outputs" / "raw.bin").write_bytes(b"content")
    first = audit.build_audit(tmp_path, scope_path)
    resolution_path = tmp_path / "resolutions.json"
    payload = _resolution_document(scope_path, status="REVIEWED")
    payload["excluded_output_index_review"] = _index_review(first["source_inventory"])
    resolution_path.write_text(json.dumps(payload))
    resolved = audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)
    assert resolved["finding_resolutions"]["excluded_output"] == {
        "status": "REVIEWED",
        "resolved": 1,
        "unresolved": 0,
        "coverage_mode": "EXACT_INDEX_REVIEW",
    }

    payload["excluded_output_index_review"]["expected_index_sha256"] = "f" * 64
    resolution_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="index review is stale"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)


def test_global_exclusions_reviewed_boolean_cannot_bypass_exact_dispositions(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    scope = json.loads(scope_path.read_text())
    scope["source_coverage"]["exclusions_reviewed"] = True
    scope_path.write_text(json.dumps(scope))
    (tmp_path / "outputs" / "raw.bin").write_bytes(b"content")
    result = audit.build_audit(tmp_path, scope_path)
    assert result["disjointness"]["status"] != "PROVEN"
    assert result["source_inventory"]["excluded_output_files"] == 1
    assert any("content-bound mounted-output exclusions" in item
               for item in result["unresolved"])


def test_resolved_external_location_requires_evidence_reviewer_and_time(tmp_path):
    scope_path = tmp_path / "scope.json"
    payload = _scope(complete=True)
    payload["external_execution_locations"][0]["evidence"] = []
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="exactly one typed location-review record"):
        audit.load_scope(scope_path)


def test_resolved_external_location_requires_hash_bound_existing_evidence(tmp_path):
    scope_path = tmp_path / "scope.json"
    payload = _scope(complete=True)
    payload["external_execution_locations"][0]["evidence"] = [{
        "path": "external_review.json",
        "sha256": "f" * 64,
        "description": "Typed external review fixture.",
    }]
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="external evidence file is missing"):
        audit.load_scope(scope_path, root=tmp_path)

    evidence = tmp_path / "external_review.json"
    evidence.write_text("reviewed bytes")
    with pytest.raises(ValueError, match="external evidence hash is stale"):
        audit.load_scope(scope_path, root=tmp_path)


def test_external_location_review_rejects_bare_prose_record(tmp_path):
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    first = payload["external_execution_locations"][0]
    review_path = tmp_path / first["evidence"][0]["path"]
    review_path.write_text(json.dumps({"review": "checked remote system"}))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="invalid typed shape"):
        audit.load_scope(scope_path, root=tmp_path)


def test_external_location_review_cannot_be_reused_across_location_classes(tmp_path):
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    payload["external_execution_locations"][1]["evidence"] = list(
        payload["external_execution_locations"][0]["evidence"]
    )
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="cannot be reused across locations"):
        audit.load_scope(scope_path, root=tmp_path)


def test_external_location_review_must_match_its_location_class(tmp_path):
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    first = payload["external_execution_locations"][0]
    review_path = tmp_path / first["evidence"][0]["path"]
    review = json.loads(review_path.read_text())
    review["location_kind"] = "CLOUD"
    review_path.write_text(json.dumps(review))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="different location/status"):
        audit.load_scope(scope_path, root=tmp_path)


def test_covered_external_location_rejects_remote_only_in_scope_source(tmp_path):
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    first = payload["external_execution_locations"][0]
    first["status"] = "COVERED"
    review_path = tmp_path / first["evidence"][0]["path"]
    review = json.loads(review_path.read_text())
    review["review_status"] = "COVERED"
    review["indexed_sources"] = [{
        "source_id": "snellius:/project/run-001/manifest.json",
        "sha256": "a" * 64,
        "disposition": "IN_SCOPE",
        "description": "Exact recovered run-manifest identity.",
        "materialized_path": None,
    }]
    review["no_additional_source_basis"] = None
    review_path.write_text(json.dumps(review))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="exact locally materialized path"):
        audit.load_scope(scope_path, root=tmp_path)


def test_external_in_scope_source_must_join_scanned_collision_index(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    payload = json.loads(scope_path.read_text())
    first = payload["external_execution_locations"][0]
    first["status"] = "COVERED"
    recovered = tmp_path / "recovered_remote.json"
    recovered.write_text(json.dumps({"seed": 77}))
    review_path = tmp_path / first["evidence"][0]["path"]
    review = json.loads(review_path.read_text())
    review["review_status"] = "COVERED"
    review["indexed_sources"] = [{
        "source_id": "snellius:/project/run-001/manifest.json",
        "sha256": audit.sha256_file(recovered),
        "disposition": "IN_SCOPE",
        "description": "Exact recovered run-manifest identity.",
        "materialized_path": recovered.name,
    }]
    review["no_additional_source_basis"] = None
    review_path.write_text(json.dumps(review))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path.write_text(json.dumps(payload))

    assert audit.load_scope(scope_path, root=tmp_path)[
        "_external_in_scope_sources"
    ][0]["materialized_path"] == recovered.name
    with pytest.raises(ValueError, match="not included in seed collision checks"):
        audit.build_audit(tmp_path, scope_path)


def test_external_in_scope_source_is_parsed_when_materialized_under_scan(
        tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    payload = json.loads(scope_path.read_text())
    first = payload["external_execution_locations"][0]
    first["status"] = "COVERED"
    scanned = tmp_path / "configs" / "prior.json"
    review_path = tmp_path / first["evidence"][0]["path"]
    review = json.loads(review_path.read_text())
    review["review_status"] = "COVERED"
    review["indexed_sources"] = [{
        "source_id": "snellius:/project/run-001/manifest.json",
        "sha256": audit.sha256_file(scanned),
        "disposition": "IN_SCOPE",
        "description": "Recovered source copied into the declared scan.",
        "materialized_path": "configs/prior.json",
    }]
    review["no_additional_source_basis"] = None
    review_path.write_text(json.dumps(review))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path.write_text(json.dumps(payload))

    result = audit.build_audit(tmp_path, scope_path)
    assert {row["seed"] for row in result["parsed_prior_seeds"]} >= {1, 2, 3, 9}


def test_external_reviewed_non_seed_source_is_an_explicit_disposition(tmp_path):
    payload = _scope(complete=True)
    _write_external_reviews(tmp_path, payload)
    first = payload["external_execution_locations"][0]
    first["status"] = "COVERED"
    review_path = tmp_path / first["evidence"][0]["path"]
    review = json.loads(review_path.read_text())
    review["review_status"] = "COVERED"
    review["indexed_sources"] = [{
        "source_id": "snellius:/project/run-001/readme.txt",
        "sha256": "a" * 64,
        "disposition": "REVIEWED_NON_SEED",
        "description": "Reviewed documentation without RNG values.",
        "materialized_path": None,
    }]
    review["no_additional_source_basis"] = None
    review_path.write_text(json.dumps(review))
    first["evidence"][0]["sha256"] = audit.sha256_file(review_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload))

    loaded = audit.load_scope(scope_path, root=tmp_path)
    assert loaded["_external_in_scope_sources"] == []


@pytest.mark.parametrize("field,value", [
    ("discovery", "10;11"),
    ("confirmation", [20, True]),
    ("proposal_rng", "30"),
])
def test_candidate_blocks_require_typed_integer_arrays_and_integer(
        tmp_path, field, value):
    candidate = {
        "schema": audit.CANDIDATE_SCHEMA,
        "discovery": [10, 11],
        "confirmation": [20, 21],
        "proposal_rng": 30,
    }
    candidate[field] = value
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="explicit integer"):
        audit.load_candidate_blocks(path)


def test_candidate_blocks_reject_extra_fields_and_record_raw_file_hash(tmp_path):
    path = _candidate(tmp_path)
    loaded = audit.load_candidate_blocks(path)
    assert loaded is not None
    assert loaded["file_sha256"] == audit.sha256_file(path)

    payload = json.loads(path.read_text())
    payload["comment"] = "not part of the typed contract"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="candidate seed blocks fields are invalid"):
        audit.load_candidate_blocks(path)


def test_resolution_scope_hash_mismatch_is_fatal(tmp_path, monkeypatch):
    scope_path = _write_inputs(tmp_path, monkeypatch)
    payload = _resolution_document(scope_path)
    payload["scope_manifest_sha256"] = "f" * 64
    resolution_path = tmp_path / "resolutions.json"
    resolution_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="scope manifest SHA-256 differs"):
        audit.build_audit(tmp_path, scope_path, resolution_path=resolution_path)
