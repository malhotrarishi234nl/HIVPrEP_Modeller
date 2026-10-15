"""Static, no-engine proof for the Option B 1980 recalibration candidate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from config_reader import ConfigReader
import stage_structured_diagnosis as ssd


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / (
    "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_"
    "stochastic_m0_m1_20261015.json"
)
CANDIDATE = ROOT / (
    "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
)
MANIFEST = ROOT / (
    "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015."
    "derivation.json"
)
TRACKED_1980 = ROOT / "configs/1980_test.json"
PLAN = ROOT / "configs/robust_nroy_freeze_production.json"

PARENT_SHA = "fd50dd1a98965c9ea6b946c634060b0bd8ddac9334eb62ffc8d9cac67a63e7cf"
CANDIDATE_SHA = "49c3953683fac96aafdecdfb54c3195102984a0a29cc32ed3a423238a6498b30"
AUTHORITY_SHA = "f0f7efbcca6181fce76401a4604231b88927011b97eb736a5f0107a8848b9d02"
TRACKED_1980_SHA = "936dc7d1a50cc4107884715f62a0b83ee5af33afc925f1234387358658b9c1fe"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _scalar_leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _scalar_leaves(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_leaves(child, path + (index,))
    else:
        yield _pointer(path), value


def _node(payload: dict, path: tuple[str, ...]):
    current = payload
    for key in path:
        current = current[key]
    return current


def test_derivation_manifest_is_an_exact_complete_scalar_diff():
    parent = json.loads(PARENT.read_text())
    candidate = json.loads(CANDIDATE.read_text())
    manifest = json.loads(MANIFEST.read_text())

    assert _sha256(PARENT) == PARENT_SHA
    assert _sha256(CANDIDATE) == CANDIDATE_SHA
    assert manifest["parent_config"]["sha256"] == PARENT_SHA
    assert manifest["candidate_config"]["sha256"] == CANDIDATE_SHA

    old = dict(_scalar_leaves(parent))
    new = dict(_scalar_leaves(candidate))
    actual = {
        path: (old.get(path), new.get(path))
        for path in sorted(set(old) | set(new))
        if old.get(path) != new.get(path)
    }
    recorded = {
        change["json_path"]: (change["old_value"], change["new_value"])
        for change in manifest["changed_json_paths"]
    }
    assert len(recorded) == manifest["changed_json_path_count"] == 56
    assert recorded == actual
    assert all(change["source_path"] for change in manifest["changed_json_paths"])
    assert all(
        len(change["source_sha256"]) == 64
        for change in manifest["changed_json_paths"]
    )
    assert all(change["rationale"] for change in manifest["changed_json_paths"])

    allowed_prefixes = (
        "/_readme",
        "/meta/version/",
        "/meta/model_notes/calibration_status",
        "/run_control/start_year/",
        "/population/total_size/",
        "/population/age_sex_distribution/",
        "/population/epidemic_seed/seed_year/",
    )
    assert all(path.startswith(allowed_prefixes) for path in actual)


def test_1980_objects_match_both_recorded_authorities_exactly():
    candidate = json.loads(CANDIDATE.read_text())
    mirror = json.loads(TRACKED_1980.read_text())
    manifest = json.loads(MANIFEST.read_text())
    assert _sha256(TRACKED_1980) == TRACKED_1980_SHA

    paths = (
        ("run_control", "start_year"),
        ("population", "total_size"),
        ("population", "age_sex_distribution"),
        ("population", "epidemic_seed", "seed_year"),
    )
    for path in paths:
        assert _node(candidate, path) == _node(mirror, path)

    assert candidate["run_control"]["start_year"]["value"] == 1980
    assert candidate["population"]["total_size"]["value"] == 14_090_318
    assert candidate["population"]["epidemic_seed"]["seed_year"]["value"] == 1980.0
    weights = candidate["population"]["age_sex_distribution"]["value"]
    assert abs(sum(sex for age in weights.values() for sex in age.values()) - 1.0) < 1e-8

    primary = next(
        source for source in manifest["authoritative_sources"]
        if source["role"] == "primary_1980_initialization_authority"
    )
    assert primary["sha256"] == AUTHORITY_SHA
    external = Path(primary["path"])
    if external.is_file():
        assert _sha256(external) == AUTHORITY_SHA
        authoritative = json.loads(external.read_text())
        for path in paths:
            assert _node(candidate, path) == _node(authoritative, path)


def test_candidate_preserves_stochastic_safe_m0_m1_route_and_v13_22_mechanisms():
    parent = json.loads(PARENT.read_text())
    candidate = json.loads(CANDIDATE.read_text())

    assert candidate["run_control"]["deterministic_mode"]["value"] is False
    profile = ssd.validate_shared_base_config(candidate)
    assert profile.enabled is False
    assert profile.routine_hazard_scale == 1.0
    assert profile.aids_symptom_annual_probability == 0.0
    assert profile.symptom_start_year == 1985.0
    assert ssd.load_stage_structured_diagnosis(
        ConfigReader(candidate).as_v6_dict()
    ) == profile

    before = copy.deepcopy(candidate)
    m0 = ssd.route_shared_config(candidate, model="M0", p=None)
    m1 = ssd.route_shared_config(candidate, model="M1", p=0.2)
    diagnosis = candidate["transmission_model"]["disease"]["diagnosis"]
    assert "stage_structured_diagnosis" not in m0["transmission_model"]["disease"]["diagnosis"]
    assert m1["transmission_model"]["disease"]["diagnosis"][
        "stage_structured_diagnosis"
    ]["enabled"]["value"] is True
    assert m1["transmission_model"]["disease"]["diagnosis"][
        "stage_structured_diagnosis"
    ]["aids_symptom_annual_probability"]["value"] == 0.2
    assert candidate == before

    # The later V13.22 mechanisms are inherited, not reconstructed from V13.1.
    assert diagnosis == parent["transmission_model"]["disease"]["diagnosis"]
    assert candidate["transmission_model"]["disease"]["progression"] == (
        parent["transmission_model"]["disease"]["progression"]
    )


def test_candidate_and_plan_remain_explicitly_blocked():
    candidate = json.loads(CANDIDATE.read_text())
    manifest = json.loads(MANIFEST.read_text())
    plan = json.loads(PLAN.read_text())

    status = candidate["meta"]["model_notes"]["calibration_status"]
    assert "REQUIRES_RECALIBRATION" in status
    assert "NOT FITTED" in status
    assert "NOT VALIDATED" in status
    assert "NOT PRODUCTION ELIGIBLE" in status
    historical = candidate["calibration_metadata"]["abc_smc_calibration"]
    assert historical["simulation_start_year"] == 1975
    assert historical["overall_accept"] is False

    assert manifest["status"] == "REQUIRES_RECALIBRATION_NOT_PRODUCTION_ELIGIBLE"
    assert manifest["owner_decision"]["choice"] == "OPTION_B"
    assert manifest["owner_decision"]["does_not_sign_production_plan"] is True
    assert manifest["open_gates"]

    models = plan["models"]
    assert models["base_config"] == "UNSIGNED"
    assert models["base_config_proposal"] == str(CANDIDATE.relative_to(ROOT))
    assert models["base_config_proposal_sha256"] == CANDIDATE_SHA
    assert models["base_config_derivation_manifest_proposal"] == str(
        MANIFEST.relative_to(ROOT)
    )
    assert plan["sign_off"]["signed_by"] == "UNSIGNED"
    assert plan["sign_off"]["signed_date"] == "UNSIGNED"
