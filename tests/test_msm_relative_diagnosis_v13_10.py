"""
test_msm_relative_diagnosis_v13_10.py — V13.10

Tests for the opt-in MSM relative diagnosis multiplier layer.

Feature: disease.diagnosis.msm_relative_diagnosis block (optional).
When absent or enabled=false, _step_diagnosis is identical to V12.15 behavior.
When enabled=true, MSM undiagnosed pool uses base rate * era multiplier;
non-MSM pool uses base rate unchanged.

Tests:
  T1  - Absent block → msm_relative_diagnosis_schedule is None
  T2  - Block present, enabled=false → schedule is None (no-op)
  T3  - Block present, enabled=true → schedule is not None
  T4  - Disabled block: _step_diagnosis output matches existing path (deterministic)
  T5  - Enabled block with multiplier < 1: MSM diagnoses < non-multiplied MSM diagnoses
  T6  - Enabled block with multiplier = 1.0: MSM diagnoses match non-multiplied path
  T7  - Schedule stepwise lookup: 1996 uses early, 2004 uses mid, 2015 uses recent
  T8  - Calibration _valid_draw_mask accepts early > mid
  T9  - Calibration _valid_draw_mask accepts early > recent
  T10 - Calibration _valid_draw_mask accepts arbitrary era ordering
  T11 - Calibration space JSON: msm_relative_diagnosis_enabled is a fixed parameter
  T12 - MSM multiplier > 1.0 increases MSM diagnoses; final probability remains capped
  T13 - Deterministic mode: enabled path produces reproducible integer counts
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_v3 import SimulationEngineV3
from run_lhs_calibration import _valid_draw_mask, _with_derived_parameters
from schedule_utils import TimeSchedule


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_MAIN_CONFIG_PATH = ROOT / "configs" / "1980_test.json"


def _load_main_config() -> dict:
    with open(_MAIN_CONFIG_PATH) as f:
        return json.load(f)


def _short_deterministic_run(cfg: dict, start: int, end: int) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg["run_control"]["start_year"] = {"value": start}
    cfg["run_control"]["end_year"] = {"value": end}
    cfg["run_control"]["step_size"] = {"value": 3}
    cfg["run_control"]["deterministic_mode"] = {"value": True}
    return cfg


def _inject_msm_relative_diagnosis(
    cfg: dict,
    enabled: bool,
    schedule: dict | None = None,
    interpolation: str = "stepwise",
) -> dict:
    cfg = copy.deepcopy(cfg)
    diag = (
        cfg.setdefault("transmission_model", {})
           .setdefault("disease", {})
           .setdefault("diagnosis", {})
    )
    block: dict = {"enabled": {"value": enabled}}
    if schedule is not None:
        block["schedule"] = {
            "interpolation": {"value": interpolation},
            "value": {str(k): float(v) for k, v in schedule.items()},
        }
    diag["msm_relative_diagnosis"] = block
    return cfg


def _remove_msm_relative_diagnosis(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    (cfg.get("transmission_model", {})
        .get("disease", {})
        .get("diagnosis", {})
        .pop("msm_relative_diagnosis", None))
    return cfg


# ---------------------------------------------------------------------------
# T1–T3: Build helper returns correct schedule/None
# ---------------------------------------------------------------------------


class TestBuildMsmRelativeDiagnosisSchedule:
    def test_absent_block_returns_none(self):
        """T1: No msm_relative_diagnosis block → schedule is None."""
        cfg = _remove_msm_relative_diagnosis(_load_main_config())
        engine = SimulationEngineV3(cfg)
        assert engine.msm_relative_diagnosis_schedule is None

    def test_enabled_false_returns_none(self):
        """T2: Block present but enabled=false → schedule is None."""
        cfg = _inject_msm_relative_diagnosis(
            _load_main_config(),
            enabled=False,
            schedule={"1996": 0.35, "2004": 0.65, "2015": 0.70},
        )
        engine = SimulationEngineV3(cfg)
        assert engine.msm_relative_diagnosis_schedule is None

    def test_enabled_true_returns_schedule(self):
        """T3: Block present with enabled=true → schedule is a TimeSchedule."""
        cfg = _inject_msm_relative_diagnosis(
            _load_main_config(),
            enabled=True,
            schedule={"1996": 0.35, "2004": 0.65, "2015": 0.70},
        )
        engine = SimulationEngineV3(cfg)
        assert engine.msm_relative_diagnosis_schedule is not None
        assert isinstance(engine.msm_relative_diagnosis_schedule, TimeSchedule)


# ---------------------------------------------------------------------------
# T4–T6: Engine-level diagnosis behavior
# ---------------------------------------------------------------------------


class TestStepDiagnosisBehavior:
    def test_disabled_produces_same_total_diagnoses(self):
        """T4: Disabled block leaves total diagnosis behavior unchanged."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)

        # Reference: no block at all
        ref_cfg = _remove_msm_relative_diagnosis(base)
        ref_engine = SimulationEngineV3(ref_cfg)
        ref_engine.run()
        ref_total = sum(ref_engine.outputs["new_diagnoses"])

        # Disabled block
        dis_cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=False,
            schedule={"1996": 0.35, "2004": 0.65, "2015": 0.70},
        )
        dis_engine = SimulationEngineV3(dis_cfg)
        dis_engine.run()
        dis_total = sum(dis_engine.outputs["new_diagnoses"])

        assert ref_total == dis_total, (
            f"Disabled block should not change total diagnoses: "
            f"ref={ref_total}, disabled={dis_total}"
        )

    def test_enabled_with_low_multiplier_reduces_msm_diagnoses(self):
        """T5: MSM multiplier < 1 reduces MSM diagnoses vs no-multiplier baseline."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)

        # Reference: no multiplier
        ref_cfg = _remove_msm_relative_diagnosis(base)
        ref_engine = SimulationEngineV3(ref_cfg)
        ref_engine.run()
        ref_msm = sum(ref_engine.outputs["new_diagnoses_msm"])

        # Enabled with low multiplier
        low_cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=True,
            schedule={"1982": 0.20},  # Early low multiplier starting before run midpoint
        )
        low_engine = SimulationEngineV3(low_cfg)
        low_engine.run()
        low_msm = sum(low_engine.outputs["new_diagnoses_msm"])

        assert low_msm < ref_msm, (
            f"Low MSM multiplier should reduce MSM diagnoses; "
            f"baseline={ref_msm}, with_multiplier={low_msm}"
        )

    # KNOWN FAILURE, deliberately NOT xfail/skip (2026-09-17). Deterministic mode only: the split
    # MSM/non-MSM allocation is not bit-identical to the single all-orientation allocation.
    # Assertion preserved. The only tolerance is a documented launch-gate exception for the stochastic
    # run_joint_fit_comparison_20260917.py experiment; see tests/KNOWN_TEST_EXCEPTIONS.md.
    def test_enabled_with_multiplier_one_matches_baseline(self):
        """T6: MSM multiplier = 1.0 produces same MSM diagnoses as baseline."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)

        ref_cfg = _remove_msm_relative_diagnosis(base)
        ref_engine = SimulationEngineV3(ref_cfg)
        ref_engine.run()
        ref_msm = sum(ref_engine.outputs["new_diagnoses_msm"])

        one_cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=True,
            schedule={"1982": 1.0},
        )
        one_engine = SimulationEngineV3(one_cfg)
        one_engine.run()
        one_msm = sum(one_engine.outputs["new_diagnoses_msm"])

        assert ref_msm == one_msm, (
            f"Multiplier=1.0 should produce identical MSM diagnoses; "
            f"ref={ref_msm}, mult1={one_msm}"
        )


# ---------------------------------------------------------------------------
# T7: Schedule stepwise lookup by era
# ---------------------------------------------------------------------------


class TestScheduleYearLookup:
    """T7: Stepwise schedule returns correct era value for each anchor boundary."""

    _SCHEDULE = {"1996": 0.35, "2004": 0.65, "2015": 0.70}

    def setup_method(self):
        self.ts = TimeSchedule(self._SCHEDULE, interpolation="stepwise", default_before=0.0)

    def test_1996_uses_early(self):
        assert self.ts.get_value(1996) == pytest.approx(0.35)

    def test_2003_uses_early(self):
        # 2003 is between 1996 and 2004 anchors → stepwise carry-forward gives 1996 value
        assert self.ts.get_value(2003) == pytest.approx(0.35)

    def test_2004_uses_mid(self):
        assert self.ts.get_value(2004) == pytest.approx(0.65)

    def test_2010_uses_mid(self):
        # 2010 is between 2004 and 2015 → stepwise gives 2004 value
        assert self.ts.get_value(2010) == pytest.approx(0.65)

    def test_2015_uses_recent(self):
        assert self.ts.get_value(2015) == pytest.approx(0.70)

    def test_2024_uses_recent(self):
        # After last anchor → carry-forward
        assert self.ts.get_value(2024) == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# T8–T10: Calibration constraint validation
# ---------------------------------------------------------------------------


class TestCalibConstraints:
    def _make_draw(self, early: float, mid: float, recent: float) -> pd.DataFrame:
        # Minimal draw with only the MSM multiplier columns
        return pd.DataFrame([{
            "msm_diag_mult_1996": early,
            "msm_diag_mult_2004": mid,
            "msm_diag_mult_2015": recent,
        }])

    def test_accepts_early_greater_than_mid(self):
        """T8: early > mid is allowed; relative ascertainment need not be monotonic."""
        df = self._make_draw(early=1.60, mid=0.50, recent=0.65)
        df = _with_derived_parameters(df)
        mask = _valid_draw_mask(df)
        assert mask.iloc[0], "Should accept early > mid"

    def test_accepts_early_greater_than_recent(self):
        """T9: early > recent is allowed; convergence or inversion can be calibrated."""
        df = self._make_draw(early=1.80, mid=0.90, recent=0.50)
        df = _with_derived_parameters(df)
        mask = _valid_draw_mask(df)
        assert mask.iloc[0], "Should accept early > recent"

    def test_accepts_valid_ordering(self):
        """T10: early <= mid and early <= recent remains valid."""
        df = self._make_draw(early=0.30, mid=1.60, recent=0.65)
        df = _with_derived_parameters(df)
        mask = _valid_draw_mask(df)
        assert mask.iloc[0], "Should accept early <= mid and early <= recent"

    def test_accepts_equal_early_mid(self):
        """Edge: early == mid should pass (boundary is inclusive)."""
        df = self._make_draw(early=0.50, mid=0.50, recent=0.70)
        df = _with_derived_parameters(df)
        mask = _valid_draw_mask(df)
        assert mask.iloc[0], "Should accept early == mid"

    def test_accepts_mid_greater_than_recent(self):
        """No constraint between mid and recent; mid > recent is allowed."""
        df = self._make_draw(early=0.30, mid=1.80, recent=0.65)
        df = _with_derived_parameters(df)
        mask = _valid_draw_mask(df)
        assert mask.iloc[0], "Should accept mid > recent (no cross-era constraint)"


# ---------------------------------------------------------------------------
# T11: Calibration space JSON fixed parameters
# ---------------------------------------------------------------------------


class TestCalibSpaceJson:
    _CALIB_SPACE_PATH = (
        ROOT / "configs" / "calib_space_v13_10_msm_diag_multiplier_pilot.json"
    )

    def _load(self) -> dict:
        with open(self._CALIB_SPACE_PATH) as f:
            return json.load(f)

    def test_file_exists(self):
        """T11a: calib_space_v13_10 file exists."""
        assert self._CALIB_SPACE_PATH.exists(), (
            f"Expected calib space at {self._CALIB_SPACE_PATH}"
        )

    def test_msm_relative_diagnosis_enabled_is_fixed(self):
        """T11b: msm_relative_diagnosis_enabled appears in fixed_parameters with value=true."""
        space = self._load()
        fixed_names = {fp["name"]: fp for fp in space.get("fixed_parameters", [])}
        assert "msm_relative_diagnosis_enabled" in fixed_names, (
            "msm_relative_diagnosis_enabled must be a fixed parameter"
        )
        fp = fixed_names["msm_relative_diagnosis_enabled"]
        assert fp["value"] is True, "Fixed parameter must be value=true"

    def test_three_msm_multiplier_parameters_present(self):
        """T11c: Three era multiplier parameters (1996, 2004, 2015) are sampled."""
        space = self._load()
        param_names = {p["name"] for p in space.get("parameters", [])}
        for name in ("msm_diag_mult_1996", "msm_diag_mult_2004", "msm_diag_mult_2015"):
            assert name in param_names, f"Expected sampled parameter '{name}'"

    def test_early_era_range(self):
        """T11d: Early era multiplier range is [0.00, 2.00]."""
        space = self._load()
        params = {p["name"]: p for p in space.get("parameters", [])}
        p = params["msm_diag_mult_1996"]
        assert p["min"] == pytest.approx(0.00)
        assert p["max"] == pytest.approx(2.00)

    def test_mid_era_range(self):
        """T11e: Mid era multiplier range is [0.00, 2.00]."""
        space = self._load()
        params = {p["name"]: p for p in space.get("parameters", [])}
        p = params["msm_diag_mult_2004"]
        assert p["min"] == pytest.approx(0.00)
        assert p["max"] == pytest.approx(2.00)

    def test_recent_era_range(self):
        """T11f: Recent era multiplier range is [0.00, 2.00]."""
        space = self._load()
        params = {p["name"]: p for p in space.get("parameters", [])}
        p = params["msm_diag_mult_2015"]
        assert p["min"] == pytest.approx(0.00)
        assert p["max"] == pytest.approx(2.00)


# ---------------------------------------------------------------------------
# T12: Multiplier above 1.0
# ---------------------------------------------------------------------------


class TestMultiplierAboveOne:
    def test_multiplier_above_one_increases_msm_diagnoses(self):
        """T12: Multiplier > 1.0 increases MSM diagnoses relative to baseline."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)

        ref_cfg = _remove_msm_relative_diagnosis(base)
        ref_engine = SimulationEngineV3(ref_cfg)
        ref_engine.run()
        ref_msm = sum(ref_engine.outputs["new_diagnoses_msm"])

        high_cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=True,
            schedule={"1982": 2.0},
        )
        engine = SimulationEngineV3(high_cfg)
        result = engine.run()
        assert result is not None
        high_msm = sum(engine.outputs["new_diagnoses_msm"])
        assert high_msm > ref_msm, (
            f"Multiplier >1 should increase MSM diagnoses; baseline={ref_msm}, "
            f"with_multiplier={high_msm}"
        )

    def test_large_multiplier_saturates_without_invalid_counts(self):
        """T12b: Very large multiplier is allowed; final probability cap prevents invalid counts."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)
        cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=True,
            schedule={"1982": 100.0},
        )
        engine = SimulationEngineV3(cfg)
        result = engine.run()
        assert result is not None
        assert sum(engine.outputs["new_diagnoses_msm"]) >= 0


# ---------------------------------------------------------------------------
# T13: Deterministic mode
# ---------------------------------------------------------------------------


class TestDeterministicMode:
    def test_deterministic_enabled_path_is_reproducible(self):
        """T13: Two identical deterministic runs with MSM layer give same counts."""
        base = _short_deterministic_run(_load_main_config(), 1980, 2000)
        cfg = _inject_msm_relative_diagnosis(
            base,
            enabled=True,
            schedule={"1982": 0.35, "2000": 0.65},
        )

        engine_a = SimulationEngineV3(cfg)
        engine_a.run()
        total_a = sum(engine_a.outputs["new_diagnoses"])
        msm_a = sum(engine_a.outputs["new_diagnoses_msm"])

        engine_b = SimulationEngineV3(cfg)
        engine_b.run()
        total_b = sum(engine_b.outputs["new_diagnoses"])
        msm_b = sum(engine_b.outputs["new_diagnoses_msm"])

        assert total_a == total_b, f"Deterministic total mismatch: {total_a} vs {total_b}"
        assert msm_a == msm_b, f"Deterministic MSM mismatch: {msm_a} vs {msm_b}"
