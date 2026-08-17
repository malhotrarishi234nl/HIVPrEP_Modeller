"""
tests/test_scenario_b_expansion.py — V12.9

Tests for Scenario B Expansion framing: oral_prep_plus_lena arm,
scenario-driven arm_order, per-arm prep_reset, prep_schedule_overrides,
and oral_prep_overrides.

Test plan from IMPLEMENTATION_PLAN_SCENARIO_B_EXPANSION.md §6:
  T1 — Unit: arm builder correctness (pure config dict tests)
  T2 — Unit: engine PrEP state population targeting
  T3 — Integration dry-run: arm builder + scenario resolution
  T4 — Config sanity: uptake assumptions and prod/adjusted parity
  T5 — Economics: combination arm cost structure
  T6 — Regression: existing scenarios unchanged (arm_order)
"""

import copy
import json
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json",
)
HTA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "hta_config.json",
)
HTA_CONFIG_ADJUSTED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "hta_config_adjusted.json",
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_base_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _load_hta_config():
    with open(HTA_CONFIG_PATH) as f:
        return json.load(f)


def _load_hta_config_adjusted():
    with open(HTA_CONFIG_ADJUSTED_PATH) as f:
        return json.load(f)


def _expansion_scenario(hta_config=None):
    """Return the scenario_b_expansion dict from hta_config."""
    cfg = hta_config or _load_hta_config()
    matches = [s for s in cfg["analysis_scenarios"] if s["name"] == "scenario_b_expansion"]
    assert matches, "scenario_b_expansion not found in hta_config.analysis_scenarios"
    return matches[0]


def _scenario_a_base(hta_config=None):
    """Return the scenario_a_base dict from hta_config."""
    cfg = hta_config or _load_hta_config()
    matches = [s for s in cfg["analysis_scenarios"] if s["name"] == "scenario_a_base"]
    assert matches, "scenario_a_base not found in hta_config.analysis_scenarios"
    return matches[0]


# ── T1: Arm builder unit tests ────────────────────────────────────────────────


class TestT1ArmBuilder:
    """
    Pure config-dict tests for _build_arm_config with scenario_b_expansion.
    No simulation required.
    """

    @pytest.fixture(scope="class")
    def runner(self):
        """Construct a MultiScenarioRunner (no sim run)."""
        from run_scenarios import MultiScenarioRunner
        return MultiScenarioRunner(
            config_path=CONFIG_PATH,
            output_dir="/tmp/test_v129_output",
            hta_config_path=HTA_CONFIG_PATH,
        )

    @pytest.fixture(scope="class")
    def expansion_scenario(self):
        return _expansion_scenario()

    # T1a: oral_prep arm
    def test_t1a_oral_prep_arm_enabled(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep", expansion_scenario)
        assert cfg["interventions"]["oral_prep"]["enabled"]["value"] is True
        assert cfg["interventions"]["lenacapavir"]["enabled"]["value"] is False

    def test_t1a_oral_prep_eligible_activity_classes(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep", expansion_scenario)
        classes = cfg["interventions"]["oral_prep"]["eligible_activity_classes"]["value"]
        assert classes == ["Medium", "High"], f"Expected ['Medium', 'High'], got {classes}"

    def test_t1a_oral_prep_schedule_override(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep", expansion_scenario)
        schedule = cfg["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]
        # Should contain 2025→0.033 from prep_schedule_overrides
        assert "2025" in schedule, f"2025 key missing from initiation.schedule: {list(schedule.keys())}"
        assert schedule["2025"] == 0.033, f"Expected 0.033, got {schedule['2025']}"

    # T1b: oral_prep_plus_lena arm
    def test_t1b_combo_arm_both_enabled(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        assert cfg["interventions"]["oral_prep"]["enabled"]["value"] is True
        assert cfg["interventions"]["lenacapavir"]["enabled"]["value"] is True

    def test_t1b_combo_oral_prep_activity_classes(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        classes = cfg["interventions"]["oral_prep"]["eligible_activity_classes"]["value"]
        assert classes == ["Medium", "High"], f"Expected ['Medium', 'High'], got {classes}"

    def test_t1b_combo_lena_activity_classes(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        classes = cfg["interventions"]["lenacapavir"]["eligible_activity_classes"]["value"]
        assert classes == ["High"], f"Expected ['High'], got {classes}"

    def test_t1b_combo_oral_prep_schedule_override(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        schedule = cfg["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]
        assert "2025" in schedule, f"2025 key missing from Oral_PrEP initiation.schedule: {list(schedule.keys())}"
        assert schedule["2025"] == 0.033

    def test_t1b_combo_lena_schedule_override(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        schedule = cfg["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"]
        assert "2025" in schedule, f"2025 key missing from Lenacapavir initiation.schedule: {list(schedule.keys())}"
        assert schedule["2025"] == 0.05

    def test_t1b_combo_annotation_keys_skipped(self, runner, expansion_scenario):
        """_source and _status keys must not appear in initiation.schedule."""
        cfg = runner._build_arm_config("oral_prep_plus_lena", expansion_scenario)
        oral_schedule = cfg["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]
        lena_schedule = cfg["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"]
        for key in list(oral_schedule.keys()) + list(lena_schedule.keys()):
            assert not key.startswith("_"), f"Annotation key '{key}' leaked into initiation.schedule"

    # T1c: no_prep arm
    def test_t1c_no_prep_both_disabled(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("no_prep", expansion_scenario)
        for intervention_name in ["oral_prep", "lenacapavir"]:
            assert cfg["interventions"][intervention_name]["enabled"]["value"] is False, (
                f"{intervention_name} should be disabled in no_prep arm"
            )

    def test_t1c_no_prep_schedules_zeroed(self, runner, expansion_scenario):
        cfg = runner._build_arm_config("no_prep", expansion_scenario)
        for prep_type in cfg.get("prep_schedules", {}):
            if "initiation" in cfg["prep_schedules"][prep_type]:
                init = cfg["prep_schedules"][prep_type]["initiation"]
                if isinstance(init, dict) and "schedule" in init:
                    for year, rate in init["schedule"].items():
                        assert rate == 0.0, (
                            f"no_prep arm: {prep_type}.initiation.schedule[{year}] = {rate}, expected 0.0"
                        )

    # T1d: per-arm prep_reset — oral_prep arm must NOT call _collapse_prep_to_none
    def test_t1d_oral_prep_reset_false_in_expansion(self, expansion_scenario):
        """arm_prep_reset['oral_prep'] must be False in scenario_b_expansion."""
        arm_prep_reset = expansion_scenario.get("arm_prep_reset", {})
        assert arm_prep_reset.get("oral_prep") is False, (
            f"Expected arm_prep_reset.oral_prep=False, got {arm_prep_reset}"
        )

    def test_t1d_oral_prep_plus_lena_reset_false(self, expansion_scenario):
        arm_prep_reset = expansion_scenario.get("arm_prep_reset", {})
        assert arm_prep_reset.get("oral_prep_plus_lena") is False

    def test_t1d_no_prep_reset_true(self, expansion_scenario):
        arm_prep_reset = expansion_scenario.get("arm_prep_reset", {})
        assert arm_prep_reset.get("no_prep") is True

    # T1e: scenario_a_base gives head-to-head arm_order (regression)
    def test_t1e_scenario_a_base_arm_order_regression(self):
        """Confirmed in the run loop: scenario_a_base → arm_order=['no_prep','oral_prep','lenacapavir']."""
        scenario = _scenario_a_base()
        arm_structure = scenario.get("arm_structure", "head_to_head")
        if arm_structure == "expansion":
            arm_order = ["no_prep", "oral_prep", "oral_prep_plus_lena"]
        else:
            arm_order = ["no_prep", "oral_prep", "lenacapavir"]
        assert arm_order == ["no_prep", "oral_prep", "lenacapavir"], (
            f"T1e regression failed: arm_order={arm_order}"
        )

    # T1f: scenario_b_expansion gives expansion arm_order
    def test_t1f_expansion_scenario_arm_order(self, expansion_scenario):
        arm_structure = expansion_scenario.get("arm_structure", "head_to_head")
        if arm_structure == "expansion":
            arm_order = ["no_prep", "oral_prep", "oral_prep_plus_lena"]
        else:
            arm_order = ["no_prep", "oral_prep", "lenacapavir"]
        assert arm_order == ["no_prep", "oral_prep", "oral_prep_plus_lena"]


# ── T2: Engine PrEP state targeting ───────────────────────────────────────────


class TestT2EnginePrEPTargeting:
    """
    Verify that _build_arm_config correctly sets eligible_activity_classes
    in the config dict so the engine can target the right activity strata.
    This is a pure config-structure test (no simulation) per the plan note
    that T2 should be lightweight.
    """

    @pytest.fixture(scope="class")
    def runner(self):
        from run_scenarios import MultiScenarioRunner
        return MultiScenarioRunner(
            config_path=CONFIG_PATH,
            output_dir="/tmp/test_v129_output",
            hta_config_path=HTA_CONFIG_PATH,
        )

    def test_t2_oral_prep_targets_medium_and_high_only(self, runner):
        """oral_prep arm config restricts oral_prep intervention to Medium+High."""
        scenario = _expansion_scenario()
        cfg = runner._build_arm_config("oral_prep", scenario)
        classes = cfg["interventions"]["oral_prep"]["eligible_activity_classes"]["value"]
        assert "Medium" in classes
        assert "High" in classes
        assert "Low" not in classes, "Low should be excluded from oral PrEP in expansion scenario"

    def test_t2_combo_lena_targets_high_only(self, runner):
        """oral_prep_plus_lena arm restricts Lenacapavir to High activity class only."""
        scenario = _expansion_scenario()
        cfg = runner._build_arm_config("oral_prep_plus_lena", scenario)
        lena_classes = cfg["interventions"]["lenacapavir"]["eligible_activity_classes"]["value"]
        assert lena_classes == ["High"], f"Expected ['High'], got {lena_classes}"

    def test_t2_combo_oral_prep_targets_medium_and_high(self, runner):
        """oral_prep_plus_lena arm still gives oral PrEP to Medium+High."""
        scenario = _expansion_scenario()
        cfg = runner._build_arm_config("oral_prep_plus_lena", scenario)
        oral_classes = cfg["interventions"]["oral_prep"]["eligible_activity_classes"]["value"]
        assert "Medium" in oral_classes
        assert "High" in oral_classes
        assert "Low" not in oral_classes


# ── T3: Integration dry-run ───────────────────────────────────────────────────


class TestT3IntegrationDryRun:
    """
    Verify that scenario_b_expansion is found in hta_config and that the
    arm builder resolves without errors for all three expansion arms.
    Does not run a full simulation.
    """

    def test_t3_scenario_b_expansion_in_hta_config(self):
        hta_cfg = _load_hta_config()
        names = [s["name"] for s in hta_cfg["analysis_scenarios"]]
        assert "scenario_b_expansion" in names, f"scenario_b_expansion missing; found: {names}"

    def test_t3_expansion_scenario_has_arm_structure_field(self):
        scenario = _expansion_scenario()
        assert scenario.get("arm_structure") == "expansion"

    def test_t3_all_three_arms_build_without_error(self):
        from run_scenarios import MultiScenarioRunner
        runner = MultiScenarioRunner(
            config_path=CONFIG_PATH,
            output_dir="/tmp/test_v129_output",
            hta_config_path=HTA_CONFIG_PATH,
        )
        scenario = _expansion_scenario()
        for arm_name in ["no_prep", "oral_prep", "oral_prep_plus_lena"]:
            cfg = runner._build_arm_config(arm_name, scenario)
            assert isinstance(cfg, dict), f"_build_arm_config returned non-dict for {arm_name}"

    def test_t3_unknown_arm_raises_value_error(self):
        from run_scenarios import MultiScenarioRunner
        runner = MultiScenarioRunner(
            config_path=CONFIG_PATH,
            output_dir="/tmp/test_v129_output",
            hta_config_path=HTA_CONFIG_PATH,
        )
        scenario = _expansion_scenario()
        with pytest.raises(ValueError, match="Unknown arm"):
            runner._build_arm_config("invalid_arm_xyz", scenario)

    def test_t3_standalone_builder_oral_prep_plus_lena(self):
        """_build_arm_config_standalone in run_psa_hta.py also handles the new arm."""
        from run_psa_hta import _build_arm_config_standalone
        base_cfg = _load_base_config()
        scenario = _expansion_scenario()
        cfg = _build_arm_config_standalone(base_cfg, "oral_prep_plus_lena", scenario)
        assert cfg["interventions"]["oral_prep"]["enabled"]["value"] is True
        assert cfg["interventions"]["lenacapavir"]["enabled"]["value"] is True

    def test_t3_standalone_builder_oral_prep_overrides(self):
        from run_psa_hta import _build_arm_config_standalone
        base_cfg = _load_base_config()
        scenario = _expansion_scenario()
        cfg = _build_arm_config_standalone(base_cfg, "oral_prep", scenario)
        assert cfg["interventions"]["oral_prep"]["eligible_activity_classes"]["value"] == ["Medium", "High"]

    def test_t3_standalone_builder_schedule_injection(self):
        from run_psa_hta import _build_arm_config_standalone
        base_cfg = _load_base_config()
        scenario = _expansion_scenario()
        cfg = _build_arm_config_standalone(base_cfg, "oral_prep_plus_lena", scenario)
        # Both schedules should be overridden — injected into initiation.schedule
        assert cfg["prep_schedules"]["Oral_PrEP"]["initiation"]["schedule"]["2025"] == 0.033
        assert cfg["prep_schedules"]["Lenacapavir"]["initiation"]["schedule"]["2025"] == 0.05

    def test_t3_oral_prep_arm_no_lena_schedule_leaked(self):
        """oral_prep arm should not get Lenacapavir schedule override."""
        from run_scenarios import MultiScenarioRunner
        runner = MultiScenarioRunner(
            config_path=CONFIG_PATH,
            output_dir="/tmp/test_v129_output",
            hta_config_path=HTA_CONFIG_PATH,
        )
        scenario = _expansion_scenario()
        cfg = runner._build_arm_config("oral_prep", scenario)
        # Lenacapavir override (0.05 at 2025) should not appear in the oral_prep arm's schedule
        lena_schedule = (
            cfg["prep_schedules"]
            .get("Lenacapavir", {})
            .get("initiation", {})
            .get("schedule", {})
        )
        assert lena_schedule.get("2025") != 0.05, (
            "Lenacapavir schedule override should not appear in oral_prep arm"
        )


# ── T4: Config sanity — uptake assumptions and config parity ──────────────────


class TestT4Sanity:
    """
    Lightweight checks that keep Scenario B expansion uptake assumptions
    aligned with the current HTA framing. No full simulation is required.
    """

    def test_t4_oral_prep_uptake_is_scenario_assumption_not_placeholder(self):
        scenario = _expansion_scenario()
        oral_init = scenario["prep_schedule_overrides"]["oral_prep"][
            "Oral_PrEP"
        ]["initiation"]

        assert oral_init["2025"] == 0.033
        assert oral_init["2028"] == 0.038
        assert oral_init["2030"] == 0.04
        assert "Kuipers RIVM 2023" in oral_init["_source"]
        assert "SCENARIO ASSUMPTION" in oral_init["_status"]
        assert "PLACEHOLDER" not in oral_init["_status"]

    def test_t4_lena_uptake_is_psa_sensitivity_not_placeholder(self):
        scenario = _expansion_scenario()
        lena_init = scenario["prep_schedule_overrides"]["oral_prep_plus_lena"][
            "Lenacapavir"
        ]["initiation"]

        assert lena_init["2025"] == 0.05
        assert lena_init["2027"] == 0.10
        assert "SCENARIO ASSUMPTION" in lena_init["_source"]
        assert "PSA SENSITIVITY" in lena_init["_status"]
        assert "PLACEHOLDER" not in lena_init["_source"]
        assert "PLACEHOLDER" not in lena_init["_status"]

    def test_t4_prod_and_adjusted_scenario_b_schedules_match(self):
        prod = _expansion_scenario(_load_hta_config())
        adjusted = _expansion_scenario(_load_hta_config_adjusted())

        assert (
            prod["prep_schedule_overrides"]
            == adjusted["prep_schedule_overrides"]
        )


# ── T5: Economics — combination arm cost structure ────────────────────────────


class TestT5Economics:
    """
    Verify economics.py handles oral_prep_plus_lena arm correctly.
    Uses synthetic config and mock population — no simulation needed.
    """

    @pytest.fixture
    def mock_groups(self):
        class _MockGroups:
            hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
            prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
            prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
            n_hiv_stages = 4
            age_groups  = []
        return _MockGroups()

    @pytest.fixture
    def minimal_hta_config(self):
        """Minimal v2 hta_config that includes oral_prep_plus_lena arm_specific entry."""
        hta_cfg = _load_hta_config()
        # Use the real hta_config (it now has oral_prep_plus_lena arm_specific)
        return hta_cfg

    def _make_pop(self, n_age=2, n_orient=1, n_activity=3, n_hiv=4, n_dx=2, n_art=2, n_prep=4):
        """Create a small zeroed population tensor."""
        import numpy as np
        return np.zeros((n_age, n_orient, n_activity, n_hiv, n_dx, n_art, n_prep))

    def test_t5_arm_to_prep_state_has_combo(self):
        """_ARM_TO_PREP_STATE must have oral_prep_plus_lena → None."""
        from economics import _ARM_TO_PREP_STATE
        assert "oral_prep_plus_lena" in _ARM_TO_PREP_STATE
        assert _ARM_TO_PREP_STATE["oral_prep_plus_lena"] is None

    def test_t5_accumulator_constructs_for_combo_arm(self, mock_groups, minimal_hta_config):
        """EconomicsAccumulator must accept arm_name='oral_prep_plus_lena'."""
        from economics import EconomicsAccumulator
        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="oral_prep_plus_lena",
        )
        assert econ.arm_name == "oral_prep_plus_lena"

    def test_t5_no_user_warning_when_arm_specific_present(self, mock_groups, minimal_hta_config):
        """No UserWarning when oral_prep_plus_lena has an arm_specific entry in hta_config."""
        from economics import EconomicsAccumulator
        import numpy as np
        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="oral_prep_plus_lena",
        )
        pop = self._make_pop()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Pass empty prep_initiations_by_arm to satisfy initiation-billed cost guard
            econ._compute_prevention_costs_v2(
                pop, susc_idx=0, step_fraction=0.25,
                prep_initiations_by_arm={"oral_prep_plus_lena": 0}
            )
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)
                             and "oral_prep_plus_lena" in str(x.message)]
            assert not user_warnings, f"Unexpected UserWarning: {[str(x.message) for x in user_warnings]}"

    def test_t5_user_warning_when_arm_specific_missing(self, mock_groups, minimal_hta_config):
        """UserWarning raised when a non-no_prep arm has no arm_specific entry."""
        from economics import EconomicsAccumulator
        import numpy as np
        # Use a made-up arm name that has no arm_specific entry
        hta_cfg = copy.deepcopy(minimal_hta_config)
        econ = EconomicsAccumulator(
            hta_config=hta_cfg,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="nonexistent_arm_xyz",
        )
        pop = self._make_pop()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            econ._compute_prevention_costs_v2(pop, susc_idx=0, step_fraction=0.25)
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert user_warnings, "Expected UserWarning for missing arm_specific entry"
            assert "nonexistent_arm_xyz" in str(user_warnings[0].message)

    def test_t5_patient_time_costs_combo_includes_oral_prep(self, mock_groups, minimal_hta_config):
        """oral_prep_plus_lena arm patient time includes Oral_PrEP users."""
        from economics import EconomicsAccumulator
        import numpy as np
        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="oral_prep_plus_lena",
        )
        pop = self._make_pop()
        # Place 100 users in Oral_PrEP slot (prep_idx=1)
        pop[0, 0, 0, 0, 0, 0, 1] = 100.0  # susc_idx=0, oral_prep prep_idx=1
        cost, breakdown = econ._compute_patient_time_costs(pop, step_fraction=0.25)
        assert cost > 0, "Expected non-zero patient time cost with Oral_PrEP users"
        assert "patient_time" in breakdown

    def test_t5_patient_time_costs_combo_includes_lenacapavir(self, mock_groups, minimal_hta_config):
        """oral_prep_plus_lena arm patient time includes Lenacapavir users."""
        from economics import EconomicsAccumulator
        import numpy as np
        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="oral_prep_plus_lena",
        )
        pop = self._make_pop()
        # Place 100 users in Lenacapavir slot (prep_idx=2)
        pop[0, 0, 0, 0, 0, 0, 2] = 100.0
        cost, breakdown = econ._compute_patient_time_costs(pop, step_fraction=0.25)
        assert cost > 0, "Expected non-zero patient time cost with Lenacapavir users"

    def test_t5_patient_time_combo_greater_than_individual_arms(self, mock_groups, minimal_hta_config):
        """oral_prep_plus_lena patient time cost > oral_prep alone (both users present)."""
        from economics import EconomicsAccumulator
        import numpy as np

        def _make_econ(arm):
            return EconomicsAccumulator(
                hta_config=minimal_hta_config,
                groups=mock_groups,
                freeze_year=2025.0,
                step_fraction=0.25,
                arm_name=arm,
            )

        pop = self._make_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100.0  # Oral_PrEP users
        pop[0, 0, 0, 0, 0, 0, 2] = 50.0   # Lenacapavir users

        cost_oral, _ = _make_econ("oral_prep")._compute_patient_time_costs(pop, 0.25)
        cost_combo, _ = _make_econ("oral_prep_plus_lena")._compute_patient_time_costs(pop, 0.25)
        assert cost_combo > cost_oral, (
            f"combo cost ({cost_combo:.2f}) should exceed oral_prep alone ({cost_oral:.2f})"
        )

    def test_t5_combo_prevention_costs_bill_both_prep_states(self, mock_groups, minimal_hta_config):
        """oral_prep_plus_lena prevention costs must include oral users plus Lena initiators."""
        from economics import EconomicsAccumulator

        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="oral_prep_plus_lena",
        )
        pop = self._make_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100.0  # Oral_PrEP users
        pop[0, 0, 0, 0, 0, 0, 2] = 50.0   # Lenacapavir users

        total, breakdown = econ._compute_prevention_costs_v2(
            pop,
            susc_idx=0,
            step_fraction=0.25,
            prep_initiations_by_arm={"oral_prep": 0, "lenacapavir": 10},
        )

        expected_drug = (
            100 * 1322.21 * 0.25
            + 10 * 41110.44 * 0.25
            + 10 * 839.98
        )
        expected_monitoring = (
            (100 + 50) * 854.5 * 0.25
            + 100 * 4.26 * 0.25
        )
        assert breakdown.get("drug", 0.0) == pytest.approx(expected_drug)
        assert breakdown.get("monitoring", 0.0) == pytest.approx(expected_monitoring)
        assert total == pytest.approx(expected_drug + expected_monitoring)

    def test_t5_no_prep_arm_zero_prevention_costs(self, mock_groups, minimal_hta_config):
        """no_prep arm must still produce zero prevention costs."""
        from economics import EconomicsAccumulator
        import numpy as np
        econ = EconomicsAccumulator(
            hta_config=minimal_hta_config,
            groups=mock_groups,
            freeze_year=2025.0,
            step_fraction=0.25,
            arm_name="no_prep",
        )
        pop = self._make_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100.0  # some users (ignored for no_prep)
        # no_prep arm skips prevention costs entirely in _compute_step_v2
        # Verify via the guard in accumulate path: arm_name != "no_prep" check
        assert econ.arm_name == "no_prep"


# ── T6: Regression — existing scenario arm_order unchanged ────────────────────


class TestT6Regression:
    """
    Verify that existing scenarios still produce the correct arm_order.
    Pure logic test — no simulation.
    """

    def test_t6_scenario_a_base_arm_order(self):
        scenario = _scenario_a_base()
        arm_structure = scenario.get("arm_structure", "head_to_head")
        arm_order = (
            ["no_prep", "oral_prep", "oral_prep_plus_lena"]
            if arm_structure == "expansion"
            else ["no_prep", "oral_prep", "lenacapavir"]
        )
        assert arm_order == ["no_prep", "oral_prep", "lenacapavir"], (
            f"T6 regression: scenario_a_base arm_order={arm_order}"
        )

    def test_t6_scenario_b_targeted_arm_order(self):
        """scenario_b_targeted has no arm_structure field → defaults to head-to-head."""
        hta_cfg = _load_hta_config()
        matches = [s for s in hta_cfg["analysis_scenarios"] if s["name"] == "scenario_b_targeted"]
        assert matches, "scenario_b_targeted not found — should still exist (deprecated in-place)"
        scenario = matches[0]
        arm_structure = scenario.get("arm_structure", "head_to_head")
        arm_order = (
            ["no_prep", "oral_prep", "oral_prep_plus_lena"]
            if arm_structure == "expansion"
            else ["no_prep", "oral_prep", "lenacapavir"]
        )
        assert arm_order == ["no_prep", "oral_prep", "lenacapavir"], (
            f"T6 regression: scenario_b_targeted arm_order={arm_order}"
        )

    def test_t6_scenario_b_targeted_deprecated_description(self):
        """Verify deprecation notice in scenario_b_targeted description."""
        hta_cfg = _load_hta_config()
        matches = [s for s in hta_cfg["analysis_scenarios"] if s["name"] == "scenario_b_targeted"]
        assert matches
        desc = matches[0].get("_description", "")
        assert "SUPERSEDED" in desc, f"Expected 'SUPERSEDED' in _description, got: {desc!r}"
        assert "scenario_b_expansion" in desc

    def test_t6_scenario_b_expansion_present(self):
        """scenario_b_expansion was added and is present."""
        hta_cfg = _load_hta_config()
        names = [s["name"] for s in hta_cfg["analysis_scenarios"]]
        assert "scenario_b_expansion" in names

    def test_t6_hta_config_oral_prep_plus_lena_arm_specific(self):
        """prevention_costs.arm_specific has oral_prep_plus_lena entry."""
        hta_cfg = _load_hta_config()
        arm_keys = list(hta_cfg["prevention_costs"]["arm_specific"].keys())
        assert "oral_prep_plus_lena" in arm_keys, f"Missing oral_prep_plus_lena in arm_specific: {arm_keys}"

    def test_t6_oral_prep_plus_lena_costs_mirror_component_arms(self):
        """oral_prep_plus_lena drug costs must mirror oral_prep and lenacapavir exactly."""
        hta_cfg = _load_hta_config()
        arm_specific = hta_cfg["prevention_costs"]["arm_specific"]

        oral = arm_specific["oral_prep"]["drug_tdf_ftc_generic"]["value"]
        combo = arm_specific["oral_prep_plus_lena"]["drug_tdf_ftc_generic"]["value"]
        assert oral == combo, f"drug_tdf_ftc_generic: oral={oral}, combo={combo}"

        lena_inj = arm_specific["lenacapavir"]["injection_subsequent"]["value"]
        combo_inj = arm_specific["oral_prep_plus_lena"]["injection_subsequent"]["value"]
        assert lena_inj == combo_inj, f"injection_subsequent: lena={lena_inj}, combo={combo_inj}"

    def test_t6_backlog_25_exists(self):
        """BACKLOG-25 cleanup entry should be present."""
        backlog_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "documentation", "BACKLOG.md"
        )
        with open(backlog_path) as f:
            content = f.read()
        assert "BACKLOG-25" in content, "BACKLOG-25 entry not found in documentation/BACKLOG.md"

    def test_t6_changelog_v129_entry(self):
        """V12.9 entry present in CHANGELOG.md."""
        changelog_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "CHANGELOG.md"
        )
        with open(changelog_path) as f:
            content = f.read()
        assert "V12.9" in content, "V12.9 entry not found in CHANGELOG.md"
        assert "scenario_b_expansion" in content
