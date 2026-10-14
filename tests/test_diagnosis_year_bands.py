"""
test_diagnosis_year_bands.py — V12.15

Tests for time-varying diagnosis rate year-band support.

Feature: disease.diagnosis.schedule block (optional). When present, _step_diagnosis
uses TimeSchedule for stepwise or linear interpolation of the annual diagnosis rate.
When absent, falls back to flat annual_rate (full backward compatibility).

TimeSchedule unit tests use schedule_utils directly (no engine needed).
Engine integration tests use 1980_test.json (epidemic seeding enabled, seed_year=1980).

Tests:
  T1  - No schedule block → diagnosis_schedule is None
  T2  - Flat fallback produces diagnoses over a run with seeded HIV
  T3  - Stepwise: exact anchor year values returned
  T4  - Stepwise: carry-forward between anchors
  T5  - Stepwise: last anchor carried forward after final year
  T6  - Linear: midpoint interpolation
  T7  - Linear: quarter-point interpolation
  T8  - Linear: flat after last anchor
  T9  - Before first anchor → rate = 0 (TimeSchedule unit test)
  T10 - Engine: zero diagnoses when run entirely before schedule's first anchor
  T11 - Engine: schedule produces diagnoses once anchor year is reached
  T12 - Engine: higher schedule rate produces more diagnoses (deterministic)
  T13 - Invalid interpolation raises ValueError at build time
"""

import sys
import os
import json
import copy

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from schedule_utils import TimeSchedule
from run_v3 import SimulationEngineV3
from config_utils import extract_value


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Unit tests use the standard test config (no HIV needed for TimeSchedule tests)
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "configs", "v11_configuration.json"
)

# Engine integration tests use the main run config — epidemic seeding enabled
_MAIN_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "configs", "1980_test.json"
)


def _load_base_config():
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def _load_main_config():
    with open(_MAIN_CONFIG_PATH) as f:
        return json.load(f)


def _short_run(cfg: dict, start: int, end: int) -> dict:
    """Override run window to [start, end] with quarterly steps."""
    cfg = copy.deepcopy(cfg)
    cfg["run_control"]["start_year"] = {"value": start}
    cfg["run_control"]["end_year"]   = {"value": end}
    cfg["run_control"]["step_size"]  = {"value": 3}
    return cfg


def _inject_diagnosis_schedule(cfg: dict, schedule_value: dict,
                                interpolation: str = "stepwise") -> dict:
    """Inject a diagnosis.schedule sub-block into the config dict (V10 path)."""
    cfg = copy.deepcopy(cfg)
    dis = cfg.setdefault("transmission_model", {}).setdefault("disease", {})
    if "diagnosis" not in dis:
        dis["diagnosis"] = {"annual_rate": {"value": 0.35}}
    dis["diagnosis"]["schedule"] = {
        "interpolation": {"value": interpolation},
        "value": schedule_value,
    }
    return cfg


def _remove_diagnosis_schedule(cfg: dict) -> dict:
    """Remove any diagnosis.schedule block from config."""
    cfg = copy.deepcopy(cfg)
    dis = cfg.get("transmission_model", {}).get("disease", {})
    if "diagnosis" in dis:
        dis["diagnosis"].pop("schedule", None)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# T1–T2 — Flat fallback (no schedule block)
# ─────────────────────────────────────────────────────────────────────────────

class TestFlatFallback:
    """Engine uses flat annual_rate path when no schedule block is present."""

    def test_no_schedule_diagnosis_schedule_is_none(self):
        """T1: _build_diagnosis_schedule() returns None when no schedule in config."""
        cfg = _remove_diagnosis_schedule(_load_base_config())
        engine = SimulationEngineV3(cfg)
        assert engine.diagnosis_schedule is None

    def test_flat_rate_produces_diagnoses(self):
        """T2: Flat annual_rate with seeded HIV accumulates diagnoses over the run."""
        cfg = _short_run(_load_main_config(), 1980, 1995)
        cfg = _remove_diagnosis_schedule(cfg)
        engine = SimulationEngineV3(cfg)
        engine.run()
        total_diag = sum(engine.outputs["new_diagnoses"])
        assert total_diag > 0, (
            f"Expected non-zero diagnoses with flat rate and seeded HIV; got {total_diag}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T3–T5 — TimeSchedule unit: stepwise
# ─────────────────────────────────────────────────────────────────────────────

class TestStepwiseSchedule:
    """Unit tests on TimeSchedule stepwise (carry-forward) behaviour."""

    _SCHEDULE = {"1985": 0.10, "1995": 0.25, "2010": 0.35}

    def setup_method(self):
        self.ts = TimeSchedule(self._SCHEDULE, interpolation="stepwise", default_before=0.0)

    def test_exact_anchor_years(self):
        """T3: Rate at an anchor year equals that anchor value exactly."""
        assert self.ts.get_value(1985) == pytest.approx(0.10)
        assert self.ts.get_value(1995) == pytest.approx(0.25)
        assert self.ts.get_value(2010) == pytest.approx(0.35)

    def test_between_anchors_carries_forward(self):
        """T4: Year between two anchors returns the earlier anchor value."""
        assert self.ts.get_value(1990) == pytest.approx(0.10)
        assert self.ts.get_value(2003) == pytest.approx(0.25)

    def test_after_last_anchor_carries_forward(self):
        """T5: Year after the last anchor returns the last anchor value."""
        assert self.ts.get_value(2020) == pytest.approx(0.35)
        assert self.ts.get_value(2050) == pytest.approx(0.35)


# ─────────────────────────────────────────────────────────────────────────────
# T6–T8 — TimeSchedule unit: linear interpolation
# ─────────────────────────────────────────────────────────────────────────────

class TestLinearInterpolation:
    """Unit tests on TimeSchedule linear interpolation behaviour."""

    def test_midpoint_interpolates(self):
        """T6: Midpoint between two anchors returns mean of anchor values."""
        ts = TimeSchedule({"2000": 0.10, "2010": 0.30}, interpolation="linear", default_before=0.0)
        assert ts.get_value(2005) == pytest.approx(0.20, abs=1e-9)

    def test_quarter_point_interpolates(self):
        """T7: Quarter-point returns linearly interpolated value."""
        ts = TimeSchedule({"2000": 0.00, "2020": 0.40}, interpolation="linear", default_before=0.0)
        # 5 years into a 20-year interval → 25% of the way → 0.10
        assert ts.get_value(2005) == pytest.approx(0.10, abs=1e-9)

    def test_after_last_anchor_flat(self):
        """T8: After the last anchor, value is flat (no extrapolation growth)."""
        ts = TimeSchedule({"2000": 0.10, "2010": 0.30}, interpolation="linear", default_before=0.0)
        assert ts.get_value(2025) == pytest.approx(0.30)


# ─────────────────────────────────────────────────────────────────────────────
# T9–T10 — Before first anchor
# ─────────────────────────────────────────────────────────────────────────────

class TestBeforeFirstAnchor:
    """Rate = 0.0 (default_before) before first anchor year."""

    def test_rate_zero_before_first_anchor(self):
        """T9: TimeSchedule returns 0 for years strictly before first anchor."""
        ts = TimeSchedule({"2000": 0.30, "2010": 0.40}, interpolation="stepwise", default_before=0.0)
        assert ts.get_value(1985) == pytest.approx(0.0)
        assert ts.get_value(1999) == pytest.approx(0.0)

    def test_engine_no_diagnoses_before_first_anchor(self):
        """T10: Engine run entirely before schedule's first anchor produces zero diagnoses."""
        # Schedule first anchor = 2010; run 1980–1990 → all steps are before anchor
        cfg = _short_run(_load_main_config(), 1980, 1990)
        cfg = _inject_diagnosis_schedule(cfg, {"2010": 0.30, "2020": 0.40},
                                         interpolation="stepwise")
        engine = SimulationEngineV3(cfg)
        engine.run()
        total_diag = sum(engine.outputs["new_diagnoses"])
        assert total_diag == 0, (
            f"Expected 0 diagnoses before schedule anchor; got {total_diag}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T11–T12 — Engine integration
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineScheduleIntegration:
    """Engine-level integration: diagnosis_schedule wired into _step_diagnosis."""

    def test_schedule_produces_nonzero_diagnoses(self):
        """T11: Schedule active with first anchor before end-year produces diagnoses."""
        cfg = _short_run(_load_main_config(), 1980, 1995)
        # Anchor starts 1982 — HIV seeded 1980, so diagnoses should accumulate
        cfg = _inject_diagnosis_schedule(cfg, {"1982": 0.30, "1995": 0.40},
                                         interpolation="stepwise")
        engine = SimulationEngineV3(cfg)
        engine.run()
        total_diag = sum(engine.outputs["new_diagnoses"])
        assert total_diag > 0, (
            f"Expected diagnoses after schedule anchor year is reached; got {total_diag}"
        )

    def test_higher_schedule_rate_increases_diagnoses(self):
        """T12: Higher rate schedule produces strictly more diagnoses than a low flat rate."""
        base = _short_run(_load_main_config(), 1980, 1995)
        base["run_control"]["deterministic_mode"] = {"value": True}

        # Low rate: flat 0.01
        low_cfg = copy.deepcopy(base)
        low_cfg = _remove_diagnosis_schedule(low_cfg)
        low_cfg["transmission_model"]["disease"]["diagnosis"]["annual_rate"] = {"value": 0.01}
        low_engine = SimulationEngineV3(low_cfg)
        low_engine.run()
        low_diag = sum(low_engine.outputs["new_diagnoses"])

        # High rate: schedule from 1982 at 0.90
        high_cfg = copy.deepcopy(base)
        high_cfg = _inject_diagnosis_schedule(high_cfg, {"1982": 0.90},
                                              interpolation="stepwise")
        high_engine = SimulationEngineV3(high_cfg)
        high_engine.run()
        high_diag = sum(high_engine.outputs["new_diagnoses"])

        assert high_diag > low_diag, (
            f"Higher schedule rate should produce more diagnoses; "
            f"high={high_diag}, low={low_diag}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T13 — Invalid interpolation raises ValueError
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidInterpolation:
    """TimeSchedule rejects unknown interpolation modes at construction time."""

    def test_invalid_interpolation_raises(self):
        """T13: Unknown interpolation string raises ValueError."""
        with pytest.raises(ValueError, match="interpolation must be"):
            TimeSchedule({"2000": 0.30}, interpolation="cubic", default_before=0.0)
