"""Guards for A-20's opt-in process-decomposition harness."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

from calib_extractor import extract_annual_outputs
from demography import DemographyModule
from run_v3 import SimulationEngineV3
from transmission_corrected import TransmissionEngine
from tools.a20_process_decomposition import (
    PROCESS_FAMILIES,
    ProcessModeSwitch,
    process_family,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json"
TEST_END_YEAR = 1985


def _run(*, deterministic: bool, seed: int, forced=()):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["run_control"]["deterministic_mode"] = {"value": deterministic}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    config["run_control"]["seed"] = {"value": seed}
    config["run_control"]["end_year"]["value"] = TEST_END_YEAR
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with ProcessModeSwitch(forced) as switch:
            result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    annual = extract_annual_outputs(result, start_year=1975, end_year=TEST_END_YEAR - 1)
    return annual, switch


def _assert_identical(left, right):
    assert left.keys() == right.keys()
    for metric in left:
        assert left[metric].keys() == right[metric].keys()
        for year in left[metric]:
            assert float(left[metric][year]) == float(right[metric][year])


@pytest.mark.slow
def test_no_family_forced_reproduces_deterministic_corner_exactly():
    production, _ = _run(deterministic=True, seed=7001)
    hybrid, _ = _run(deterministic=True, seed=7001, forced=())
    _assert_identical(production, hybrid)


@pytest.mark.slow
def test_every_family_forced_reproduces_stochastic_corner_exactly():
    production, _ = _run(deterministic=False, seed=7001)
    hybrid, _ = _run(
        deterministic=True, seed=7001, forced=PROCESS_FAMILIES
    )
    _assert_identical(production, hybrid)


def test_forced_named_draw_uses_crn_factory_directly():
    class Factory:
        def __init__(self):
            self.calls = []

        def for_process(self, **kwargs):
            self.calls.append(kwargs)
            return np.random.default_rng(11)

    engine = object.__new__(SimulationEngineV3)
    engine.deterministic = True
    engine._current_year = 2001.25
    engine._current_step = 105
    engine._crn_factory = Factory()
    engine.rng = np.random.RandomState(99)
    with ProcessModeSwitch({"diagnosis"}):
        result = engine._draw_events_for(
            "diagnosis_all_orientations", np.array([10, 20]), 0.2
        )
    assert result.shape == (2,)
    assert engine._crn_factory.calls == [
        {
            "year": 2001.25,
            "timestep": 105,
            "process_id": "diagnosis_all_orientations",
        }
    ]


def test_context_restores_every_binding_after_exception():
    originals = {
        (SimulationEngineV3, "_draw_events_for"): SimulationEngineV3._draw_events_for,
        (SimulationEngineV3, "_process_rng"): SimulationEngineV3._process_rng,
        (TransmissionEngine, "calculate_transmission"): TransmissionEngine.calculate_transmission,
        (DemographyModule, "calculate_births"): DemographyModule.calculate_births,
    }
    with pytest.raises(RuntimeError):
        with ProcessModeSwitch({"diagnosis", "births"}):
            raise RuntimeError("boom")
    for (owner, name), original in originals.items():
        assert getattr(owner, name) is original


@pytest.mark.parametrize(
    ("process_id", "family"),
    [
        ("diagnosis_msm", "diagnosis"),
        ("art_dropout", "art"),
        (("hiv_mortality", "AIDS", "off_art"), "hiv_mortality"),
        (("prep_uptake", "Oral_PrEP", 2, 1), "prep"),
        ("childhood_residence_aging", "ageing_sexual_debut"),
        ("not_a_real_process", None),
    ],
)
def test_process_id_mapping(process_id, family):
    assert process_family(process_id) == family


def test_unknown_family_is_refused():
    with pytest.raises(ValueError, match="unknown A-20 process families"):
        ProcessModeSwitch({"made_up"})


def test_current_switch_surface_inventory_is_complete_and_unique():
    from tools.run_a20_process_decomposition import inventory_sites

    sites = inventory_sites()
    assert len(sites) == 31
    assert len({(row["module"], row["line"]) for row in sites}) == 31
    assert {row["module"] for row in sites} == {
        "run_v3.py",
        "demography.py",
        "migration.py",
        "transmission_corrected.py",
        "adverse_events.py",
    }
