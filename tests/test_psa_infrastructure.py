"""
tests/test_psa_infrastructure.py

V11.9.6 — PSA Infrastructure Groundwork

Tests for:
  BLOCKER-A: SimulationEngineV3 accepting dict input (in addition to file path)
  BLOCKER-B: _deep_set utility + _build_arm_config param_overrides refactor

All tests use the real v11_configuration.json and hta_config.json where needed,
with minimal time horizons and deterministic mode to keep the suite fast.
"""

import json
import os
import sys
import copy
import pytest
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3
from run_scenarios import (
    _deep_set,
    _validate_lenacapavir_schedule_active,
    MultiScenarioRunner,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json"
)
HTA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "hta_config.json"
)

@pytest.fixture(scope="module")
def base_config_dict():
    """Raw config loaded as a dict — the state SimulationEngineV3 now accepts."""
    with open(CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture
def short_config_dict(base_config_dict):
    """
    Deep copy of base config with a very short simulation horizon for speed.
    Deterministic mode on. Uses V11 config key paths (run_control block).
    """
    cfg = copy.deepcopy(base_config_dict)
    # Collapse to a 2-year run to keep tests fast (V11 key: run_control)
    cfg["run_control"]["start_year"] = {"value": 2020}
    cfg["run_control"]["end_year"] = {"value": 2022}
    cfg["run_control"]["deterministic_mode"] = {"value": True}
    return cfg


@pytest.fixture(scope="module")
def hta_runner():
    """
    A live MultiScenarioRunner instance initialised from real configs.
    Used to test _build_arm_config directly.
    """
    runner = MultiScenarioRunner.__new__(MultiScenarioRunner)
    with open(CONFIG_PATH) as f:
        runner.base_config = json.load(f)
    with open(HTA_CONFIG_PATH) as f:
        runner.hta_config = json.load(f)
    return runner


@pytest.fixture
def minimal_scenario():
    """Minimal analysis_scenario dict sufficient for _build_arm_config."""
    return {"name": "test_scenario", "label": "Test", "lenacapavir_overrides": {}}


# ─────────────────────────────────────────────────────────────────────────────
# Category A — SimulationEngineV3 dict input (BLOCKER-A)
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineAcceptsDict:

    def test_A1_dict_input_produces_identical_output_to_path_input(self, short_config_dict):
        """
        Primary regression: running the engine with a dict should produce byte-identical
        output to running with a file path (same config, same seed).
        """
        # Write the short config to a temp file for the path-based run
        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="test_A1_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(short_config_dict, f)

            engine_path = SimulationEngineV3(tmp_path, seed=42)
            engine_path.run()

            engine_dict = SimulationEngineV3(short_config_dict, seed=42)
            engine_dict.run()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        df_path = pd.DataFrame(engine_path.outputs)
        df_dict = pd.DataFrame(engine_dict.outputs)

        assert list(df_path.columns) == list(df_dict.columns), \
            "Column sets differ between path and dict inputs"
        assert df_path.shape == df_dict.shape, \
            f"Output shapes differ: path={df_path.shape}, dict={df_dict.shape}"
        pd.testing.assert_frame_equal(
            df_path.reset_index(drop=True),
            df_dict.reset_index(drop=True),
            check_exact=True,
            obj="Engine outputs: path vs dict input"
        )

    def test_A2_dict_input_smoke_no_crash(self, short_config_dict):
        """Dict input initialises and runs without error; key output columns are present."""
        engine = SimulationEngineV3(short_config_dict, seed=0)
        engine.run()
        outputs = engine.outputs
        required_cols = [
            "year", "new_infections", "prevalence",
            "incidence_rate", "total_population"
        ]
        for col in required_cols:
            assert col in outputs, f"Expected column '{col}' missing from outputs"

    def test_A3_invalid_input_type_raises_typeerror(self):
        """Passing a non-str, non-dict raises TypeError with an informative message."""
        with pytest.raises(TypeError) as exc_info:
            SimulationEngineV3(12345, seed=0)
        assert "SimulationEngineV3 expects" in str(exc_info.value)
        assert "int" in str(exc_info.value)

    def test_A4_path_input_still_works(self, short_config_dict):
        """
        Backward compat: existing string-path callers are unaffected.
        Write a temp config and confirm the engine loads it without error.
        """
        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="test_A4_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(short_config_dict, f)
            engine = SimulationEngineV3(tmp_path, seed=0)
            engine.run()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        assert len(engine.outputs["year"]) > 0

    def test_A5_dict_input_does_not_mutate_source_dict(self, short_config_dict):
        """
        The engine should not modify the caller's dict in place.
        The PSA runner passes the same base config repeatedly — mutation would
        corrupt subsequent draws.
        """
        cfg = copy.deepcopy(short_config_dict)
        original_end_year = cfg["run_control"]["end_year"]["value"]

        engine = SimulationEngineV3(cfg, seed=99)
        engine.run()

        assert cfg["run_control"]["end_year"]["value"] == original_end_year, \
            "Engine mutated the caller's config dict"


# ─────────────────────────────────────────────────────────────────────────────
# Category B — _deep_set utility (BLOCKER-B)
# ─────────────────────────────────────────────────────────────────────────────

class TestDeepSet:

    def test_B1_single_level_key(self):
        cfg = {"a": 1}
        _deep_set(cfg, "a", 99)
        assert cfg["a"] == 99

    def test_B2_four_level_nested_path(self):
        cfg = {"a": {"b": {"c": {"value": 0.5}}}}
        _deep_set(cfg, "a.b.c.value", 0.99)
        assert cfg["a"]["b"]["c"]["value"] == 0.99

    def test_B3_sibling_keys_unaffected(self):
        cfg = {"a": {"x": 1, "y": 2}}
        _deep_set(cfg, "a.x", 10)
        assert cfg["a"]["y"] == 2, "Sibling key 'y' was unexpectedly modified"

    def test_B4_dict_value_written_correctly(self):
        """Schedule injection use case — value is a dict, not a scalar."""
        cfg = {"prep_schedules": {"Lenacapavir": {"initiation": {"schedule": {}}}}}
        new_schedule = {"2025": 0.04, "2027": 0.08}
        _deep_set(cfg, "prep_schedules.Lenacapavir.initiation.schedule", new_schedule)
        assert cfg["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"] == new_schedule

    def test_B5_nonexistent_intermediate_key_raises_keyerror(self):
        cfg = {"a": {}}
        with pytest.raises(KeyError):
            _deep_set(cfg, "a.b.c", 1)

    def test_B6_overwrites_existing_nested_value(self):
        cfg = {"x": {"y": {"value": 0.1}}}
        _deep_set(cfg, "x.y.value", 0.9)
        assert cfg["x"]["y"]["value"] == 0.9

    def test_B7_single_segment_path(self):
        """A dot-path with no dots sets a top-level key."""
        cfg = {"top": 42}
        _deep_set(cfg, "top", 0)
        assert cfg["top"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Category C — _build_arm_config (BLOCKER-B)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildArmConfig:

    def test_C1_return_type_is_dict(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("oral_prep", minimal_scenario)
        assert isinstance(result, dict), \
            f"Expected dict, got {type(result).__name__}"

    def test_C2_no_temp_files_created(self, hta_runner, minimal_scenario, monkeypatch):
        """
        tempfile.mkstemp must not be called during _build_arm_config.
        The old implementation wrote to a temp file; the new one returns a dict.
        """
        import tempfile as tempfile_module

        def _should_not_be_called(*args, **kwargs):
            raise AssertionError("tempfile.mkstemp should not be called by _build_arm_config")

        monkeypatch.setattr(tempfile_module, "mkstemp", _should_not_be_called)
        # Should complete without triggering the patched mkstemp
        result = hta_runner._build_arm_config("oral_prep", minimal_scenario)
        assert isinstance(result, dict)

    def test_C3_no_prep_arm_disables_all_prep_interventions(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("no_prep", minimal_scenario)
        for intervention_name, intervention_cfg in result.get("interventions", {}).items():
            enabled = intervention_cfg.get("enabled", {})
            val = enabled.get("value", True) if isinstance(enabled, dict) else enabled
            assert val is False, \
                f"Intervention '{intervention_name}' should be disabled in no_prep arm"

    def test_C4_no_prep_arm_zeroes_prep_schedules(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("no_prep", minimal_scenario)
        for prep_type, prep_cfg in result.get("prep_schedules", {}).items():
            schedule = prep_cfg.get("initiation", {}).get("schedule", {})
            for year, rate in schedule.items():
                assert rate == 0.0, \
                    f"{prep_type} initiation rate for year {year} should be 0.0 in no_prep arm"

    def test_C5_param_overrides_scalar_injected_at_correct_path(self, hta_runner, minimal_scenario):
        overrides = {
            "transmission_model.disease.transmission.beta_chronic.value": 0.001
        }
        result = hta_runner._build_arm_config("oral_prep", minimal_scenario, param_overrides=overrides)
        actual = result["transmission_model"]["disease"]["transmission"]["beta_chronic"]["value"]
        assert actual == 0.001, f"Expected 0.001, got {actual}"

    def test_C6_param_overrides_dict_value_injected_correctly(self, hta_runner, minimal_scenario):
        """Schedule dict injection via param_overrides."""
        new_schedule = {"2025": 0.05, "2030": 0.10}
        overrides = {"prep_schedules.Oral_PrEP.initiation.schedule": new_schedule}
        result = hta_runner._build_arm_config("oral_prep", minimal_scenario, param_overrides=overrides)
        actual = result["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]
        assert actual == new_schedule

    def test_C7_param_overrides_does_not_mutate_base_config(self, hta_runner, minimal_scenario):
        """
        _build_arm_config must not modify runner.base_config.
        PSA runner calls this N=1000 times — any mutation would corrupt subsequent draws.
        """
        original_value = copy.deepcopy(
            hta_runner.base_config["transmission_model"]["disease"]["transmission"]["beta_chronic"]["value"]
        )
        overrides = {
            "transmission_model.disease.transmission.beta_chronic.value": 0.00001
        }
        hta_runner._build_arm_config("oral_prep", minimal_scenario, param_overrides=overrides)

        actual_base = hta_runner.base_config["transmission_model"]["disease"]["transmission"]["beta_chronic"]["value"]
        assert actual_base == original_value, \
            f"base_config was mutated: expected {original_value}, found {actual_base}"

    def test_C8_param_overrides_none_and_empty_dict_equivalent(self, hta_runner, minimal_scenario):
        result_none = hta_runner._build_arm_config("oral_prep", minimal_scenario, param_overrides=None)
        result_empty = hta_runner._build_arm_config("oral_prep", minimal_scenario, param_overrides={})
        assert result_none == result_empty, \
            "param_overrides=None and param_overrides={} produced different configs"

    def test_C9_arm_definitions_win_over_param_overrides(self, hta_runner, minimal_scenario):
        """
        param_overrides are applied BEFORE arm definitions.
        Attempting to re-enable lenacapavir in the no_prep arm via param_overrides
        must have no effect — the arm definition runs after and disables it again.
        """
        overrides = {"interventions.lenacapavir.enabled.value": True}
        result = hta_runner._build_arm_config("no_prep", minimal_scenario, param_overrides=overrides)
        actual = result["interventions"]["lenacapavir"]["enabled"]["value"]
        assert actual is False, \
            "Arm definition should override param_overrides (arm definitions always win)"

    def test_C10_unknown_arm_raises_value_error(self, hta_runner, minimal_scenario):
        with pytest.raises(ValueError, match="Unknown arm"):
            hta_runner._build_arm_config("invalid_arm", minimal_scenario)

    def test_C11_oral_prep_arm_enables_oral_disables_lena(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("oral_prep", minimal_scenario)
        oral_enabled = result["interventions"]["oral_prep"]["enabled"]["value"]
        lena_enabled = result["interventions"]["lenacapavir"]["enabled"]["value"]
        assert oral_enabled is True, "oral_prep should be enabled in oral_prep arm"
        assert lena_enabled is False, "lenacapavir should be disabled in oral_prep arm"

    def test_C12_lenacapavir_arm_enables_lena_disables_oral(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("lenacapavir", minimal_scenario)
        oral_enabled = result["interventions"]["oral_prep"]["enabled"]["value"]
        lena_enabled = result["interventions"]["lenacapavir"]["enabled"]["value"]
        assert oral_enabled is False, "oral_prep should be disabled in lenacapavir arm"
        assert lena_enabled is True, "lenacapavir should be enabled in lenacapavir arm"

    def test_C13_enabled_lena_empty_schedule_raises(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("lenacapavir", minimal_scenario)
        result["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"] = {}

        with pytest.raises(ValueError, match="Lenacapavir arm is enabled"):
            _validate_lenacapavir_schedule_active(
                result, minimal_scenario, freeze_year=2025, eval_years=40
            )

    def test_C14_enabled_lena_positive_schedule_passes(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("lenacapavir", minimal_scenario)
        result["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"] = {
            "2025": 0.10
        }

        _validate_lenacapavir_schedule_active(
            result, minimal_scenario, freeze_year=2025, eval_years=40
        )

    def test_C15_enabled_lena_zero_schedule_requires_explicit_optout(self, hta_runner, minimal_scenario):
        result = hta_runner._build_arm_config("lenacapavir", minimal_scenario)
        result["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"] = {}
        scenario = {
            **minimal_scenario,
            "allow_zero_lenacapavir_uptake": True,
        }

        _validate_lenacapavir_schedule_active(
            result, scenario, freeze_year=2025, eval_years=40
        )


# ─────────────────────────────────────────────────────────────────────────────
# Category D — Regression
# ─────────────────────────────────────────────────────────────────────────────

class TestRegression:

    def test_D1_engine_output_columns_unchanged(self, short_config_dict):
        """
        The set of output columns produced by the engine must not change.
        Guard against inadvertent column removal or renaming.
        """
        expected_cols = {
            "step", "year", "new_infections", "prevalence", "incidence_rate",
            "hiv_deaths", "background_deaths", "childhood_deaths",
            "total_population", "sexually_active_population", "childhood_population",
            "births", "sexual_debuts", "continuous_debuts", "flush_debuts",
            "net_migration",
        }
        engine = SimulationEngineV3(short_config_dict, seed=0)
        engine.run()
        actual_cols = set(engine.outputs.keys())
        missing = expected_cols - actual_cols
        assert not missing, f"Expected output columns missing: {missing}"

    def test_D2_build_arm_config_result_accepted_by_engine(self, hta_runner, minimal_scenario, short_config_dict):
        """
        The dict returned by _build_arm_config must be directly usable by
        SimulationEngineV3 (the primary integration point of the two blockers).
        Use a minimal horizon to keep the test fast.
        """
        # Build a short-horizon version of base_config to avoid full-run cost
        runner_short = MultiScenarioRunner.__new__(MultiScenarioRunner)
        runner_short.base_config = copy.deepcopy(short_config_dict)
        with open(HTA_CONFIG_PATH) as f:
            runner_short.hta_config = json.load(f)

        arm_dict = runner_short._build_arm_config("oral_prep", minimal_scenario)
        assert isinstance(arm_dict, dict)

        # Engine must accept the dict and run without error
        engine = SimulationEngineV3(arm_dict, seed=0)
        engine.run()
        assert len(engine.outputs["year"]) > 0
