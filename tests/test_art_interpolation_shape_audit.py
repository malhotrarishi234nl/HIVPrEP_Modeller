from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_art_interpolation_shape_audit as audit
import run_coupled_calibration as rcc

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/art_interpolation_shape_audit_20260921.json"


def _base(candidate_id=1326):
    plan = audit.load_plan(PLAN)
    source, search, _ = audit._context(plan)
    prepared = rcc.load_prepared(Path(search["context"]["prepared_dir"]))
    raw = rcc._raw_record(source, candidate_id)
    space = json.loads(prepared["state"]["paths"]["calib_space"].read_text())
    return rcc.injected_config(prepared["state"]["base_config"], raw["params"], space)


def test_validation_is_read_only_and_budgeted(monkeypatch):
    import run_v3

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not construct an engine")

    monkeypatch.setattr(run_v3.SimulationEngineV3, "__init__", forbidden)
    result = audit.validate(PLAN)
    assert result["ok"]
    assert result["trajectory_budget"] == 70
    assert result["seed_preflight"]["overlap"] == []


@pytest.mark.parametrize("profile_id", [
    "art_hazard_linear", "art_smoothstep", "art_stepwise",
    "msm_log_plus_art_hazard", "msm_log_plus_art_smoothstep",
])
def test_every_art_geometry_preserves_original_anchors(profile_id):
    plan = audit.load_plan(PLAN)
    profile = next(value for value in plan["profiles"] if value["id"] == profile_id)
    base = _base()
    changed = audit.apply_profile(
        base, profile, quarter_step=plan["profile_contract"]["quarter_step"])
    assert audit._art_anchors(base).items() <= audit._art_anchors(changed).items()


def test_smoothstep_is_distinct_between_anchors_and_equal_at_endpoints():
    values = audit._densify_art({2000.0: 0.2, 2010.0: 0.8},
                                "smoothstep_probability", 0.25)
    assert values["2000"] == 0.2
    assert values["2010"] == 0.8
    assert values["2002.5"] != pytest.approx(0.35)
