from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_coupled_calibration as rcc
import run_recent_mechanism_audit as audit
from coupled_calibration_search import ContractError


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/recent_mechanism_audit_20260921.json"


def _candidate_base(candidate_id=1326):
    plan = audit.load_plan(PLAN)
    source, search, _ = audit.source_context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    raw = rcc._raw_record(source, candidate_id)
    space_json = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    return rcc.injected_config(prepared["state"]["base_config"], raw["params"], space_json)


def test_validation_is_read_only_and_uses_stable_confirmed_candidates(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    result = audit.validate(PLAN)
    assert result["ok"]
    assert result["candidate_ids"] == [1326, 1788]
    assert result["trajectory_budget"] == 180
    assert result["seed_preflight"]["overlap"] == []
    assert result["simulations_started"] == 0


def test_prep_variants_change_only_declared_historical_controls():
    base = _candidate_base()
    off = audit.apply_variant(base, "prep_corridor_off")
    assert off["prep_schedules"]["Oral_PrEP"]["advanced"][
        "historical_stock_corridor"]["enabled"] is False
    assert off["prep_schedules"]["Oral_PrEP"]["initiation"] == \
        base["prep_schedules"]["Oral_PrEP"]["initiation"]

    none = audit.apply_variant(base, "prep_none_historical")
    rates = none["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]
    assert all(float(value) == 0.0 for year, value in rates.items() if int(year) <= 2025)
    assert rates["2026"] == base["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]["2026"]


def test_art_and_diagnosis_variants_are_bounded_and_specific():
    base = _candidate_base()
    art = audit.apply_variant(base, "art_transmission_reduction_0_80")
    assert art["transmission_model"]["disease"]["advanced"][
        "art_transmission_reduction"]["value"] == 0.80
    diagnosis = audit.apply_variant(base, "msm_diagnosis_2019_anchor_1_25x")
    old = base["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]["value"]
    new = diagnosis["transmission_model"]["disease"]["diagnosis"][
        "msm_relative_diagnosis"]["schedule"]["value"]
    assert new["1996"] == old["1996"]
    assert new["2004"] == old["2004"]
    assert new["2019"] == pytest.approx(1.25 * old["2019"])


def test_unknown_variant_is_refused():
    with pytest.raises(ContractError, match="unknown audit variant"):
        audit.apply_variant(_candidate_base(), "invented")
