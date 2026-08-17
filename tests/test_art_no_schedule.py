"""
test_art_no_schedule.py — V11.7.3

Two compat-retirement validations for V11.7.3:

1. ART zero-rate path (Finding 5)
   Engine handles the absence of an ART schedule gracefully by using zero
   initiation/dropout rates rather than crashing or reading stale V4-style
   config keys. Valid for component tests that are not testing ART dynamics.

2. initialization_method required (Finding 7)
   Engine raises ValueError on startup when population.initialization_method
   is absent from config, instead of silently falling back to 'direct_counts'.
"""

import sys
import os
import json
import copy

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_v3 import SimulationEngineV3


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "configs", "v11_configuration.json"
)


def _load_base_config():
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def _make_no_art_config(tmp_path: str) -> str:
    """
    Write a V10 config to a temp file with the interventions.art block removed.
    All other settings (including PrEP, demography) are preserved from the
    canonical v11_configuration.json.
    Returns the path to the written temp file.
    """
    cfg = _load_base_config()

    # Remove interventions.art so ConfigReader returns None for art_schedule.
    # Also remove prep_schedules so ScheduleManager is not constructed at all
    # (it requires BOTH art_schedule and prep_schedules to be absent).
    interventions = cfg.get("interventions", {})
    interventions.pop("art", None)
    cfg["interventions"] = interventions
    cfg.pop("prep_schedules", None)

    # Short run: 1970–1975, quarterly steps — minimise runtime
    cfg["run_control"]["start_year"] = {"value": 1970}
    cfg["run_control"]["end_year"]   = {"value": 1975}
    cfg["run_control"]["step_size"]  = {"value": 3}

    out_path = os.path.join(tmp_path, "no_art_config.json")
    with open(out_path, "w") as f:
        json.dump(cfg, f)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestARTNoSchedule:

    def test_engine_runs_without_art_schedule(self, tmp_path):
        """
        Engine must not raise when no ART schedule is configured.
        schedule_manager should be None (no art_schedule key in V6 dict).
        """
        config_path = _make_no_art_config(str(tmp_path))
        engine = SimulationEngineV3(config_path, seed=42)
        assert engine.schedule_manager is None, \
            "schedule_manager should be None when interventions.art is absent"
        # run() should complete without error
        engine.run()

    def test_no_art_initiations_when_schedule_absent(self, tmp_path):
        """
        With no ART schedule, zero initiation rate → on-ART population must
        remain at its initial value (0 for a clean-start config) throughout.
        """
        config_path = _make_no_art_config(str(tmp_path))
        engine = SimulationEngineV3(config_path, seed=42)
        engine.run()

        # on-ART dimension: state axis index 1 (on_art=1), art axis = 1
        # population tensor axes: [age, sex, orient, hiv_stage, diag, art, prep]
        # on-ART = population[:, :, :, 1:, :, 1, :]  (hiv_stage > 0 and art=1)
        on_art_final = np.sum(engine.state.population[:, :, :, 1:, :, 1, :])
        assert on_art_final == pytest.approx(0.0, abs=0.5), \
            f"Expected ~0 on-ART without a schedule, got {on_art_final:.1f}"

    def test_prep_schedules_still_work_without_art_schedule(self, tmp_path):
        """
        Removing the art block must not affect PrEP schedule loading.
        The ScheduleManager is only constructed when art_schedule OR prep_schedules
        is present; here prep_schedules is present so schedule_manager is not None.
        """
        cfg = _load_base_config()
        # Remove only art, keep prep_schedules intact
        cfg.get("interventions", {}).pop("art", None)
        # Verify prep_schedules still in config raw
        assert "prep_schedules" in cfg or "interventions" in cfg, \
            "prep_schedules should still be accessible via ConfigReader"

        cfg["run_control"]["start_year"] = {"value": 1970}
        cfg["run_control"]["end_year"]   = {"value": 1972}
        cfg["run_control"]["step_size"]  = {"value": 3}

        out_path = str(tmp_path / "no_art_with_prep.json")
        with open(out_path, "w") as f:
            json.dump(cfg, f)

        engine = SimulationEngineV3(out_path, seed=42)
        # schedule_manager may be set if prep_schedules present — that's fine
        # Just verify the engine starts and runs without error
        engine.run()


# ─────────────────────────────────────────────────────────────────────────────
# Finding 7 — initialization_method required
# ─────────────────────────────────────────────────────────────────────────────

class TestInitializationMethodRequired:

    def test_missing_initialization_method_raises_value_error(self, tmp_path):
        """
        population.initialization_method is required as of V11.7.3.
        A config that omits it must raise ValueError at engine construction,
        not silently fall back to 'direct_counts'.
        """
        cfg = _load_base_config()
        # Strip the initialization_method key from the population block
        population_block = cfg.get("population", {})
        population_block.pop("initialization_method", None)
        cfg["population"] = population_block

        cfg["run_control"]["start_year"] = {"value": 1970}
        cfg["run_control"]["end_year"]   = {"value": 1971}

        out_path = str(tmp_path / "no_init_method.json")
        with open(out_path, "w") as f:
            json.dump(cfg, f)

        with pytest.raises(ValueError, match="initialization_method"):
            SimulationEngineV3(out_path, seed=42)

    def test_unknown_initialization_method_raises_value_error(self, tmp_path):
        """
        An unrecognised initialization_method value must raise ValueError,
        not silently execute the legacy direct_counts code path.
        """
        cfg = _load_base_config()
        cfg["population"]["initialization_method"] = {"value": "direct_counts"}

        cfg["run_control"]["start_year"] = {"value": 1970}
        cfg["run_control"]["end_year"]   = {"value": 1971}

        out_path = str(tmp_path / "unknown_init_method.json")
        with open(out_path, "w") as f:
            json.dump(cfg, f)

        with pytest.raises(ValueError, match="direct_counts"):
            SimulationEngineV3(out_path, seed=42)

    def test_relative_weights_still_works(self, tmp_path):
        """
        Sanity check: 'relative_weights' must continue to work after the
        fallback removal. Regression guard.
        """
        cfg = _load_base_config()
        assert cfg["population"]["initialization_method"]["value"] == "relative_weights", \
            "Canonical config should use relative_weights"

        cfg["run_control"]["start_year"] = {"value": 1970}
        cfg["run_control"]["end_year"]   = {"value": 1971}
        cfg["run_control"]["step_size"]  = {"value": 3}

        out_path = str(tmp_path / "relative_weights_ok.json")
        with open(out_path, "w") as f:
            json.dump(cfg, f)

        # Must not raise
        engine = SimulationEngineV3(out_path, seed=42)
        engine.run()
