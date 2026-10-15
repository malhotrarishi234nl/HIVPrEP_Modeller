"""Adversarial validation of the M0/M1 identity-gate certificate.

These tests do not construct the simulation engine.  They protect the
production authorisation boundary itself: redundant fields in the JSON must
agree, and malformed/truthy values must never be treated as scientific proof.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coupled_calibration_search as ccs  # noqa: E402
import nroy_identity_gate as nig  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402


def _passing_artifact(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A minimal internally and cryptographically consistent certificate."""
    base = root / "base.json"
    base.write_text("{}\n")
    fingerprints = {name: f"sha256:{name}" for name in nig.GATE_CODE_MODULES}
    monkeypatch.setattr(nig, "code_fingerprint", lambda _root: fingerprints)
    return {
        "schema": nig.GATE_VERSION,
        "passed": True,
        "base_config": "base.json",
        "base_config_sha256": ccs.sha256_file(base),
        "code_sha256": fingerprints,
        "environment": nig.scientific_runtime_fingerprint(),
        "checks": [
            {"check": name, "passed": True}
            for name in nig.REQUIRED_GATE_CHECKS
        ],
        "n_checks": len(nig.REQUIRED_GATE_CHECKS),
        "n_passed": len(nig.REQUIRED_GATE_CHECKS),
    }


def _write(root: Path, artifact: dict) -> Path:
    path = root / nig.GATE_FILENAME
    path.write_text(json.dumps(artifact))
    return path


def test_consistent_all_passed_certificate_is_accepted(tmp_path, monkeypatch):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    assert nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path) == artifact


def test_top_level_passed_cannot_override_a_failed_required_check(
    tmp_path, monkeypatch
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact["checks"][1]["passed"] = False
    artifact["n_passed"] = artifact["n_checks"] - 1

    with pytest.raises(ContractError, match="contradictory result evidence"):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("n_passed", 3, "n_passed=3"),
        ("n_checks", 5, "n_checks=5"),
    ],
)
def test_declared_counts_must_equal_recorded_results(
    tmp_path, monkeypatch, field, value, message
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact[field] = value

    with pytest.raises(ContractError, match=message):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


def test_required_check_must_appear_exactly_once(tmp_path, monkeypatch):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact["checks"].append(dict(artifact["checks"][0]))
    artifact["n_checks"] += 1
    artifact["n_passed"] += 1

    with pytest.raises(ContractError, match="repeats check"):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


@pytest.mark.parametrize(
    ("location", "value", "message"),
    [
        ("summary", "false", "non-boolean top-level"),
        ("check", "false", "non-boolean passed"),
        ("n_passed", True, "non-integer n_checks/n_passed"),
    ],
)
def test_truthy_or_ambiguous_types_are_rejected(
    tmp_path, monkeypatch, location, value, message
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    if location == "summary":
        artifact["passed"] = value
    elif location == "check":
        artifact["checks"][0]["passed"] = value
    else:
        artifact["n_passed"] = value

    with pytest.raises(ContractError, match=message):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


def test_failed_summary_cannot_disagree_with_all_passing_checks(tmp_path, monkeypatch):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact["passed"] = False

    with pytest.raises(ContractError, match="contradictory result evidence"):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


def test_certificate_from_a_different_scientific_runtime_is_stale(
    tmp_path, monkeypatch,
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact["environment"]["numpy"] = "different-version"
    with pytest.raises(ContractError, match="different scientific runtime"):
        nig.assert_gate_satisfied(_write(tmp_path, artifact), root=tmp_path)


def test_raw_config_certificate_cannot_authorise_a_signed_production_plan(
    tmp_path, monkeypatch,
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    with pytest.raises(ContractError, match="did not exercise ProductionEvaluator"):
        nig.assert_gate_satisfied(
            _write(tmp_path, artifact), root=tmp_path,
            expected_plan_sha256="plan", expected_scientific_config_sha256="science",
        )


def test_production_route_certificate_must_match_plan_and_scientific_config(
    tmp_path, monkeypatch,
):
    artifact = _passing_artifact(tmp_path, monkeypatch)
    artifact.update({
        "config_route": "ProductionEvaluator.config_for",
        "route_evidence": {
            "plan_sha256": "other-plan",
            "scientific_config_sha256": "science",
            "vectors": {"unit": {"vector_hash": "hash"}},
        },
    })
    with pytest.raises(ContractError, match="not bound to this signed run plan"):
        nig.assert_gate_satisfied(
            _write(tmp_path, artifact), root=tmp_path,
            expected_plan_sha256="plan", expected_scientific_config_sha256="science",
        )
