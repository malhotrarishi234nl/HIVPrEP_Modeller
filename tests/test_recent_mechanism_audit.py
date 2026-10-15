from __future__ import annotations

from pathlib import Path

import pytest

import run_recent_mechanism_audit as audit
from coupled_calibration_search import ContractError


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/recent_mechanism_audit_20260921.json"
STALE_SEARCH_ERROR = (
    "code changed since the search: "
    "['aids_observation.py', 'calib_extractor.py', 'config_utils.py', "
    "'hm_target_register.py', 'hm_variance_budget.py', "
    "'run_coupled_calibration.py', 'run_lhs_calibration.py', 'run_v3.py']"
)


def _synthetic_base():
    """Minimal pure fixture for mechanism transforms, never a calibration claim."""
    return {
        "run_control": {"deterministic_mode": {"value": False}},
        "prep_schedules": {
            "Oral_PrEP": {
                "initiation": {
                    "schedule": {"2019": 0.01, "2025": 0.03, "2026": 0.04},
                },
                "advanced": {"historical_stock_corridor": {"enabled": True}},
            },
        },
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {"value": {"2015": 0.6, "2020": 0.8}},
                },
            },
        },
        "transmission_model": {
            "disease": {
                "advanced": {"art_transmission_reduction": {"value": 0.9}},
                "diagnosis": {
                    "msm_relative_diagnosis": {
                        "enabled": {"value": True},
                        "schedule": {
                            "value": {"1996": 0.7, "2004": 0.9, "2019": 1.0},
                        },
                    },
                },
            },
        },
    }


def test_historical_lineage_is_rejected_for_module_hash_mismatch(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    with pytest.raises(ContractError) as caught:
        audit.validate(PLAN)
    assert str(caught.value) == STALE_SEARCH_ERROR


def test_prep_variants_change_only_declared_historical_controls():
    base = _synthetic_base()
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
    base = _synthetic_base()
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
        audit.apply_variant(_synthetic_base(), "invented")
