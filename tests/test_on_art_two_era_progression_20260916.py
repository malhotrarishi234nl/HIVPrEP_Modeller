"""Two-era effective on-ART Chronic->AIDS rate (Rishi decision B, 2026-09-16)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from run_v3 import SimulationEngineV3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json"
SPACE = ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json"


def _engine_with(block, year):
    e = object.__new__(SimulationEngineV3)
    e.config = {"disease": {"progression": {"chronic_to_aids_on_art": block}}}
    e.on_art_progression_schedule = e._build_on_art_progression_schedule()
    e._current_year = year
    return e


def _block(**schedule):
    base = {"value": {"2000": 0.02, "2010": 0.004}, "interpolation": {"value": "linear"},
            "supersedes_value": {"value": True}}
    base.update(schedule)
    return {"value": 0.011, "schedule": base}


@pytest.mark.parametrize("year,expected", [(1980.0, 0.02), (2000.0, 0.02), (2005.0, 0.012), (2010.0, 0.004), (2024.5, 0.004)])
def test_schedule_is_early_before_2000_linear_ramp_then_recent(year, expected):
    e = _engine_with(_block(), year)
    assert e.on_art_progression_schedule.get_value(year) == pytest.approx(expected)


def test_scalar_next_to_schedule_without_supersedes_flag_raises():
    block = _block()
    del block["schedule"]["supersedes_value"]
    with pytest.raises(ValueError, match="supersedes_value"):
        _engine_with(block, 2000.0)


def test_interpolation_must_be_declared():
    block = _block()
    del block["schedule"]["interpolation"]
    with pytest.raises(ValueError, match="interpolation"):
        _engine_with(block, 2000.0)


def test_schedule_absent_keeps_scalar_behaviour():
    e = _engine_with({"value": 0.011}, 2000.0)
    assert e.on_art_progression_schedule is None


def test_progression_step_uses_scheduled_rate_for_the_current_year():
    e = object.__new__(SimulationEngineV3)
    e.state = SimpleNamespace(population=np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=int))
    e.state.population[0, 1, 0, 2, 1, 1, 0] = 1000          # MSM, diagnosed, on ART, Chronic
    e.groups = SimpleNamespace(hiv_to_idx={"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3},
                               orient_to_idx={"Female": 0, "MSM": 1, "Heterosexual_Male": 2})
    e.config = {"disease": {"progression": {"acute_to_chronic": 0.0, "chronic_to_aids": 0.05,
                                            "chronic_to_aids_on_art": _block()}}}
    e.on_art_progression_schedule = e._build_on_art_progression_schedule()
    e.time = SimpleNamespace(annual_to_step_rate=lambda x: x, years_per_step=1.0)
    rates = {}
    e._draw_events_for = lambda name, pool, rate: (rates.__setitem__(name, rate), np.zeros_like(pool))[1]
    for year, expected in ((1999.0, 0.02), (2005.0, 0.012), (2015.0, 0.004)):
        e._current_year = year
        e._step_disease_progression()
        assert rates["progression_chronic_to_aids_on_art"] == pytest.approx(expected)


def test_calibration_space_injects_both_anchors_into_the_schedule():
    from run_lhs_calibration import inject_params, validate_config_paths
    base = json.loads(BASE.read_text())
    space = json.loads(SPACE.read_text())
    validate_config_paths(base, space)
    names = {p["name"]: p for p in space["parameters"]}
    assert "chronic_to_aids_on_art" not in names
    assert (names["chronic_to_aids_on_art_early"]["min"], names["chronic_to_aids_on_art_early"]["max"]) == (0.002, 0.030)
    assert (names["chronic_to_aids_on_art_recent"]["min"], names["chronic_to_aids_on_art_recent"]["max"]) == (0.002, 0.008)
    params = {p["name"]: (p["min"] + p["max"]) / 2 for p in space["parameters"]}
    params["chronic_to_aids_on_art_early"], params["chronic_to_aids_on_art_recent"] = 0.025, 0.003
    config = inject_params(copy.deepcopy(base), params, space, deterministic_mode=False, simulation_start_year=1975)
    schedule = config["transmission_model"]["disease"]["progression"]["chronic_to_aids_on_art"]["schedule"]["value"]
    assert float(schedule["2000"]) == 0.025 and float(schedule["2010"]) == 0.003
