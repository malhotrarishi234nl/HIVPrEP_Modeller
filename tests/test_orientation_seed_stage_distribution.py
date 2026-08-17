"""
Tests for V13.11 orientation-specific seed stage distribution.

Covers:
  - Parser: valid block, disabled block, partial block, invalid sums
  - Shared behavior unchanged when new block absent
  - inject_params produces correct stage_distribution_by_orientation block
  - _valid_draw_mask enforces simplex and monotonicity constraints
  - _with_derived_parameters computes derived acute shares
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_lhs_calibration import (
    _valid_draw_mask,
    _with_derived_parameters,
    inject_params,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_config():
    """Minimal config sufficient for inject_params / validation tests."""
    cfg_path = ROOT / "1980_test.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def _minimal_calib_space():
    """Calib space with only the orientation seed params (other params omitted)."""
    return {
        "parameters": [
            {"name": "seed_msm_chronic_share",     "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.MSM.Chronic",     "min": 0.0, "max": 0.35, "stages": ["joint"]},
            {"name": "seed_msm_aids_share",         "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.MSM.AIDS",         "min": 0.0, "max": 0.05, "stages": ["joint"]},
            {"name": "seed_non_msm_chronic_share",  "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.non_MSM.Chronic",  "min": 0.0, "max": 0.40, "stages": ["joint"]},
            {"name": "seed_non_msm_aids_share",     "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.non_MSM.AIDS",     "min": 0.0, "max": 0.12, "stages": ["joint"]},
        ],
        "fixed_parameters": [],
        "excluded_parameters": [
            {"name": "seed_msm_acute_share",     "config_path": "...", "value": "derived"},
            {"name": "seed_non_msm_acute_share", "config_path": "...", "value": "derived"},
        ],
        "constraints": [],
    }


# ---------------------------------------------------------------------------
# _parse_orientation_seed_stage_dist (engine parsing)
# ---------------------------------------------------------------------------


class TestParseOrientationSeedStageDist:
    """Test the engine parser via inject_params + inspect output config."""

    def _inject_and_read_block(self, msm_chronic, msm_aids, non_msm_chronic, non_msm_aids):
        cfg = _base_config()
        space = _minimal_calib_space()
        vector = {
            "seed_msm_chronic_share":    msm_chronic,
            "seed_msm_aids_share":       msm_aids,
            "seed_non_msm_chronic_share": non_msm_chronic,
            "seed_non_msm_aids_share":   non_msm_aids,
        }
        result = inject_params(cfg, vector, space)
        return result["population"]["epidemic_seed"]["stage_distribution_by_orientation"]

    def test_enabled_true_when_block_injected(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["enabled"]["value"] is True

    def test_msm_chronic_share_written(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["MSM"]["Chronic"] == pytest.approx(0.08)

    def test_msm_aids_share_written(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["MSM"]["AIDS"] == pytest.approx(0.02)

    def test_msm_acute_derived(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["MSM"]["Acute"] == pytest.approx(1.0 - 0.08 - 0.02)

    def test_non_msm_chronic_share_written(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["non_MSM"]["Chronic"] == pytest.approx(0.25)

    def test_non_msm_aids_share_written(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["non_MSM"]["AIDS"] == pytest.approx(0.05)

    def test_non_msm_acute_derived(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        assert block["value"]["non_MSM"]["Acute"] == pytest.approx(1.0 - 0.25 - 0.05)

    def test_msm_sums_to_one(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        msm = block["value"]["MSM"]
        total = msm["Acute"] + msm["Chronic"] + msm["AIDS"]
        assert total == pytest.approx(1.0)

    def test_non_msm_sums_to_one(self):
        block = self._inject_and_read_block(0.08, 0.02, 0.25, 0.05)
        non_msm = block["value"]["non_MSM"]
        total = non_msm["Acute"] + non_msm["Chronic"] + non_msm["AIDS"]
        assert total == pytest.approx(1.0)

    def test_msm_acute_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="seed_msm_acute_share"):
            self._inject_and_read_block(0.80, 0.30, 0.25, 0.05)

    def test_non_msm_acute_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="seed_non_msm_acute_share"):
            self._inject_and_read_block(0.08, 0.02, 0.80, 0.30)


# ---------------------------------------------------------------------------
# Shared behavior unchanged when orientation block absent
# ---------------------------------------------------------------------------


class TestSharedBehaviorUnchanged:
    def test_no_orient_block_injected_when_params_absent(self):
        cfg = _base_config()
        space = {
            "parameters": [
                {"name": "seed_chronic_share", "config_path": "population.epidemic_seed.stage_distribution.value.Chronic", "min": 0.0, "max": 0.40, "stages": ["joint"]},
                {"name": "seed_aids_share",    "config_path": "population.epidemic_seed.stage_distribution.value.AIDS",    "min": 0.0, "max": 0.05, "stages": ["joint"]},
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
            "constraints": [],
        }
        vector = {"seed_chronic_share": 0.08, "seed_aids_share": 0.02}
        result = inject_params(cfg, vector, space)
        ep = result.get("population", {}).get("epidemic_seed", {})
        assert "stage_distribution_by_orientation" not in ep

    def test_shared_stage_distribution_still_written(self):
        cfg = _base_config()
        space = {
            "parameters": [
                {"name": "seed_chronic_share", "config_path": "population.epidemic_seed.stage_distribution.value.Chronic", "min": 0.0, "max": 0.40, "stages": ["joint"]},
                {"name": "seed_aids_share",    "config_path": "population.epidemic_seed.stage_distribution.value.AIDS",    "min": 0.0, "max": 0.05, "stages": ["joint"]},
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
            "constraints": [],
        }
        vector = {"seed_chronic_share": 0.10, "seed_aids_share": 0.03}
        result = inject_params(cfg, vector, space)
        dist = result["population"]["epidemic_seed"]["stage_distribution"]["value"]
        assert dist["Chronic"] == pytest.approx(0.10)
        assert dist["AIDS"]    == pytest.approx(0.03)
        assert dist["Acute"]   == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# _with_derived_parameters
# ---------------------------------------------------------------------------


class TestWithDerivedParameters:
    def test_seed_msm_acute_derived(self):
        draws = pd.DataFrame([{"seed_msm_chronic_share": 0.08, "seed_msm_aids_share": 0.02}])
        result = _with_derived_parameters(draws)
        assert result["seed_msm_acute_share"].iloc[0] == pytest.approx(0.90)

    def test_seed_non_msm_acute_derived(self):
        draws = pd.DataFrame([{"seed_non_msm_chronic_share": 0.25, "seed_non_msm_aids_share": 0.05}])
        result = _with_derived_parameters(draws)
        assert result["seed_non_msm_acute_share"].iloc[0] == pytest.approx(0.70)

    def test_shared_acute_derived_unchanged(self):
        draws = pd.DataFrame([{"seed_chronic_share": 0.10, "seed_aids_share": 0.03}])
        result = _with_derived_parameters(draws)
        assert result["seed_acute_share"].iloc[0] == pytest.approx(0.87)

    def test_no_acute_added_when_shares_absent(self):
        draws = pd.DataFrame([{"some_other_param": 0.5}])
        result = _with_derived_parameters(draws)
        assert "seed_msm_acute_share"     not in result.columns
        assert "seed_non_msm_acute_share" not in result.columns

    def test_all_derived_columns_added_together(self):
        draws = pd.DataFrame([{
            "seed_msm_chronic_share": 0.09, "seed_msm_aids_share": 0.01,
            "seed_non_msm_chronic_share": 0.25, "seed_non_msm_aids_share": 0.05,
        }])
        result = _with_derived_parameters(draws)
        assert result["seed_msm_acute_share"].iloc[0]     == pytest.approx(0.90)
        assert result["seed_non_msm_acute_share"].iloc[0] == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# _valid_draw_mask
# ---------------------------------------------------------------------------


class TestValidDrawMask:
    def _draws(self, **kwargs):
        return pd.DataFrame([kwargs])

    def test_valid_orientation_shares_pass(self):
        draws = self._draws(
            seed_msm_chronic_share=0.08, seed_msm_aids_share=0.02,
            seed_non_msm_chronic_share=0.25, seed_non_msm_aids_share=0.05,
        )
        draws = _with_derived_parameters(draws)
        mask = _valid_draw_mask(draws)
        assert mask.iloc[0]

    def test_negative_msm_acute_rejected(self):
        """seed_msm_chronic=0.80 + seed_msm_aids=0.30 → acute=-0.10 → rejected."""
        draws = self._draws(
            seed_msm_chronic_share=0.80, seed_msm_aids_share=0.30,
            seed_non_msm_chronic_share=0.25, seed_non_msm_aids_share=0.05,
        )
        draws = _with_derived_parameters(draws)
        mask = _valid_draw_mask(draws)
        assert not mask.iloc[0]

    def test_negative_non_msm_acute_rejected(self):
        draws = self._draws(
            seed_msm_chronic_share=0.08, seed_msm_aids_share=0.02,
            seed_non_msm_chronic_share=0.80, seed_non_msm_aids_share=0.30,
        )
        draws = _with_derived_parameters(draws)
        mask = _valid_draw_mask(draws)
        assert not mask.iloc[0]

    def test_msm_aids_exceeds_non_msm_aids_rejected(self):
        """V13.11 constraint: MSM AIDS share <= non-MSM AIDS share."""
        draws = self._draws(
            seed_msm_chronic_share=0.08, seed_msm_aids_share=0.10,
            seed_non_msm_chronic_share=0.25, seed_non_msm_aids_share=0.05,
        )
        draws = _with_derived_parameters(draws)
        mask = _valid_draw_mask(draws)
        assert not mask.iloc[0]

    def test_msm_aids_equal_to_non_msm_aids_passes(self):
        """Boundary: MSM AIDS == non-MSM AIDS is allowed."""
        draws = self._draws(
            seed_msm_chronic_share=0.08, seed_msm_aids_share=0.05,
            seed_non_msm_chronic_share=0.25, seed_non_msm_aids_share=0.05,
        )
        draws = _with_derived_parameters(draws)
        mask = _valid_draw_mask(draws)
        assert mask.iloc[0]

    def test_orient_shares_absent_no_crash(self):
        """Draws without orientation shares: mask unaffected (no crash)."""
        draws = pd.DataFrame([{"some_param": 0.5}])
        mask = _valid_draw_mask(draws)
        assert mask.iloc[0]  # unaffected


# ---------------------------------------------------------------------------
# Engine parsing via _parse_orientation_seed_stage_dist
# ---------------------------------------------------------------------------


class TestEngineOrientationParsing:
    """Test the engine parser directly using a mock config structure."""

    def _make_ep_config(self, enabled, msm_dist=None, non_msm_dist=None):
        block = {"enabled": {"value": enabled}, "value": {}}
        if msm_dist:
            block["value"]["MSM"] = msm_dist
        if non_msm_dist:
            block["value"]["non_MSM"] = non_msm_dist
        return {"stage_distribution_by_orientation": block}

    def _parse(self, ep_config, orient_key):
        """Invoke the engine parser directly (construct engine just for parsing)."""
        import contextlib
        import io

        from run_v3 import SimulationEngineV3

        cfg = _base_config()
        # Inject the ep config block
        cfg.setdefault("population", {}).setdefault("epidemic_seed", {}).update(ep_config)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            eng = SimulationEngineV3(cfg, seed=0)
        if orient_key == "MSM":
            return eng._epidemic_seed_stage_dist_msm
        return eng._epidemic_seed_stage_dist_non_msm

    def test_disabled_block_returns_none(self):
        ep = self._make_ep_config(False, {"Acute": 0.90, "Chronic": 0.08, "AIDS": 0.02})
        assert self._parse(ep, "MSM") is None

    def test_absent_block_returns_none(self):
        assert self._parse({}, "MSM") is None

    def test_valid_msm_block_parsed(self):
        ep = self._make_ep_config(True, msm_dist={"Acute": 0.90, "Chronic": 0.08, "AIDS": 0.02})
        result = self._parse(ep, "MSM")
        assert result is not None
        assert result["Acute"]   == pytest.approx(0.90)
        assert result["Chronic"] == pytest.approx(0.08)
        assert result["AIDS"]    == pytest.approx(0.02)

    def test_valid_non_msm_block_parsed(self):
        ep = self._make_ep_config(True, non_msm_dist={"Acute": 0.70, "Chronic": 0.25, "AIDS": 0.05})
        result = self._parse(ep, "non_MSM")
        assert result is not None
        assert result["Acute"]   == pytest.approx(0.70)

    def test_invalid_sum_raises(self):
        ep = self._make_ep_config(True, msm_dist={"Acute": 0.90, "Chronic": 0.08, "AIDS": 0.05})
        with pytest.raises(ValueError, match="sum"):
            self._parse(ep, "MSM")

    def test_missing_orientation_key_returns_none(self):
        ep = self._make_ep_config(True, msm_dist={"Acute": 0.90, "Chronic": 0.08, "AIDS": 0.02})
        result = self._parse(ep, "non_MSM")
        assert result is None

    def test_negative_share_raises(self):
        ep = self._make_ep_config(True, msm_dist={"Acute": 1.10, "Chronic": -0.10, "AIDS": 0.00})
        with pytest.raises(ValueError, match="non-negative"):
            self._parse(ep, "MSM")

    def test_shared_dist_not_affected_when_block_absent(self):
        """stage_distribution_by_orientation absent → _epidemic_seed_stage_distribution unchanged."""
        import contextlib
        import io
        from run_v3 import SimulationEngineV3

        cfg = _base_config()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            eng = SimulationEngineV3(cfg, seed=0)
        assert eng._epidemic_seed_stage_dist_msm     is None
        assert eng._epidemic_seed_stage_dist_non_msm is None

    def test_msm_and_non_msm_can_differ(self):
        ep = self._make_ep_config(
            True,
            msm_dist={"Acute": 0.90, "Chronic": 0.09, "AIDS": 0.01},
            non_msm_dist={"Acute": 0.65, "Chronic": 0.25, "AIDS": 0.10},
        )
        msm     = self._parse(ep, "MSM")
        non_msm = self._parse(ep, "non_MSM")
        assert msm["AIDS"]     == pytest.approx(0.01)
        assert non_msm["AIDS"] == pytest.approx(0.10)
        assert msm["AIDS"] != non_msm["AIDS"]
