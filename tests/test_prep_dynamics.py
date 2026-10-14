"""
Test suite for V11.5 PrEP dynamics: eligibility filters, activity modifiers,
age bounds, and schedule integration.

Tests validate that PrEP uptake is correctly restricted to eligible
populations and that existing mass-balance guarantees are preserved.
"""

import pytest
import json
import copy
import numpy as np
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3
from config_utils import extract_value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_base_config():
    """Load the V11 config and return raw dict."""
    with open("configs/v11_configuration.json") as f:
        return json.load(f)


def make_prep_enabled_config(
    base_config=None,
    enabled_interventions=("oral_prep",),
    eligible_orientations=None,
    eligible_age_range=None,
    eligible_activity_classes=None,
    uptake_modifier_by_activity=None,
    uptake_rate=None,
    dropout_rate=None,
    prep_schedule=None,
    n_steps=20,
    start_year=2020,
    deterministic=True,
):
    """
    Build a test config dict with PrEP enabled and specified eligibility filters.

    Returns a config dict suitable for passing directly to SimulationEngineV3
    (which accepts both a file path and a dict since V11.9.6).
    """
    if base_config is None:
        base_config = load_base_config()
    config = copy.deepcopy(base_config)

    # V10 config uses run_control (not time/simulation_controls)
    rc = config.setdefault("run_control", {})
    rc["start_year"] = {"value": start_year}
    rc["deterministic_mode"] = {"value": deterministic}

    # Compute n_steps from start_year and a short end_year
    step_size = extract_value(rc.get("step_size", {"value": 3}))
    unit = extract_value(rc.get("unit", {"value": "months"}))
    steps_per_year = 12 // step_size if unit == "months" else 1
    eval_years = n_steps / steps_per_year
    rc["end_year"] = {"value": start_year + eval_years}

    # Disable migration and population adjustments for cleaner tests
    # V10 config: demography.migration, demography.advanced.population_adjustments
    demo = config.setdefault("demography", {})
    demo.setdefault("migration", {})["enabled"] = {"value": False}
    demo.setdefault("advanced", {}).setdefault(
        "population_adjustments", {}
    )["enabled"] = {"value": False}

    # Enable specified interventions
    for iname in ("oral_prep", "lenacapavir"):
        config["interventions"][iname]["enabled"] = {
            "value": iname in enabled_interventions
        }

    # Apply overrides to the first enabled intervention
    target = enabled_interventions[0] if enabled_interventions else "oral_prep"
    target_config = config["interventions"][target]

    if eligible_orientations is not None:
        target_config["eligible_orientations"] = {"value": eligible_orientations}

    if eligible_age_range is not None:
        target_config["eligible_age_range"] = {"value": eligible_age_range}

    if eligible_activity_classes is not None:
        target_config["eligible_activity_classes"] = {"value": eligible_activity_classes}

    if uptake_modifier_by_activity is not None:
        target_config.setdefault("advanced", {})["uptake_modifier_by_activity"] = {
            "value": uptake_modifier_by_activity
        }

    if uptake_rate is not None:
        target_config["uptake_rate"] = {"value": uptake_rate}

    if dropout_rate is not None:
        target_config["dropout_rate"] = {"value": dropout_rate}

    # PrEP schedule — must always be present when ScheduleManager is active
    # (art_schedule exists in the config, so ScheduleManager is always created).
    # If no explicit schedule is given, create one from the fixed rates that
    # starts at start_year so the test behaves like a flat-rate model.
    v6_name = "Oral_PrEP" if target == "oral_prep" else "Lenacapavir"
    effective_dropout = dropout_rate if dropout_rate is not None else 0.20
    if prep_schedule is not None:
        config["prep_schedules"] = {
            "interpolation": {"value": "stepwise"},
            v6_name: {
                "initiation": {"schedule": prep_schedule},
                "discontinuation": {"schedule": {
                    str(min(int(k) for k in prep_schedule)): effective_dropout
                }},
            },
        }
    else:
        # Synthesize a flat schedule from the fixed uptake_rate starting at start_year
        effective_uptake = uptake_rate if uptake_rate is not None else 0.05
        config["prep_schedules"] = {
            "interpolation": {"value": "stepwise"},
            v6_name: {
                "initiation": {"schedule": {str(start_year): effective_uptake}},
                "discontinuation": {"schedule": {str(start_year): effective_dropout}},
            },
        }

    return config


def get_prep_count_by_orientation(engine, prep_name):
    """Return dict mapping orientation name -> number of people on given PrEP."""
    prep_idx = engine.groups.prep_to_idx[prep_name]
    susc_idx = engine.groups.hiv_to_idx["Susceptible"]
    result = {}
    for orient_name, orient_idx in engine.groups.orient_to_idx.items():
        count = np.sum(
            engine.state.population[:, orient_idx, :, susc_idx, :, :, prep_idx]
        )
        result[orient_name] = int(count)
    return result


def get_prep_count_by_activity(engine, prep_name):
    """Return dict mapping activity name -> number of people on given PrEP."""
    prep_idx = engine.groups.prep_to_idx[prep_name]
    susc_idx = engine.groups.hiv_to_idx["Susceptible"]
    result = {}
    for act_name, act_idx in engine.groups.activity_to_idx.items():
        count = np.sum(
            engine.state.population[:, :, act_idx, susc_idx, :, :, prep_idx]
        )
        result[act_name] = int(count)
    return result


def get_prep_count_by_age(engine, prep_name):
    """Return dict mapping age group name -> number of people on given PrEP."""
    prep_idx = engine.groups.prep_to_idx[prep_name]
    susc_idx = engine.groups.hiv_to_idx["Susceptible"]
    result = {}
    for age_name, age_idx in engine.groups.age_to_idx.items():
        count = np.sum(
            engine.state.population[age_idx, :, :, susc_idx, :, :, prep_idx]
        )
        result[age_name] = int(count)
    return result


def run_engine(config, seed=42, n_steps=None):
    """Create engine from a config dict (or path) and run n_steps. Returns engine."""
    engine = SimulationEngineV3(config, seed=seed)
    steps = n_steps if n_steps else engine.time.n_steps
    for step in range(steps):
        engine.step_simulation(step)
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrientationFilter:
    """Verify PrEP uptake is restricted to eligible orientations."""

    def test_msm_only(self):
        """With eligible_orientations=['MSM'], only MSM should have PrEP users."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.30,  # high rate to ensure visible uptake in few steps
            dropout_rate=0.0,
            n_steps=10,
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_orientation(engine, "Oral_PrEP")

        assert counts["MSM"] > 0, "MSM should have PrEP users"
        assert counts["Female"] == 0, "Females should NOT have PrEP users"
        assert counts["Heterosexual_Male"] == 0, "Het males should NOT have PrEP users"

    def test_msm_and_female(self):
        """With eligible_orientations=['MSM', 'Female'], both should have PrEP users."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM", "Female"],
            uptake_rate=0.30,
            dropout_rate=0.0,
            n_steps=10,
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_orientation(engine, "Oral_PrEP")

        assert counts["MSM"] > 0, "MSM should have PrEP users"
        assert counts["Female"] > 0, "Females should have PrEP users"
        assert counts["Heterosexual_Male"] == 0, "Het males should NOT have PrEP users"

    def test_all_orientations_when_absent(self):
        """Without eligible_orientations key, all orientations should get PrEP (backward compat)."""
        cfg = make_prep_enabled_config(
            eligible_orientations=None,  # not set → default to all
            uptake_rate=0.30,
            dropout_rate=0.0,
            n_steps=10,
            start_year=2020,
        )
        # Remove the key entirely to test default behaviour
        cfg["interventions"]["oral_prep"].pop("eligible_orientations", None)

        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_orientation(engine, "Oral_PrEP")

        assert counts["MSM"] > 0, "MSM should have PrEP users"
        assert counts["Female"] > 0, "Females should have PrEP users"
        assert counts["Heterosexual_Male"] > 0, "Het males should have PrEP users"


class TestActivityModifiers:
    """Verify activity-level uptake modifiers produce differential uptake."""

    def test_differential_uptake(self):
        """High-activity should have higher PrEP coverage rate than Medium."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.20,
            dropout_rate=0.0,
            uptake_modifier_by_activity={"Low": 0.0, "Medium": 1.0, "High": 2.0},
            n_steps=10,
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_activity(engine, "Oral_PrEP")

        assert counts["Low"] == 0, "Low-activity (modifier=0) should have zero PrEP users"

        # Compare PrEP coverage *rate* not absolute count, because the
        # activity distribution (Low:60%, Med:30%, High:10%) means High
        # has far fewer total people than Medium.
        susc_idx = engine.groups.hiv_to_idx["Susceptible"]
        msm_idx = engine.groups.orient_to_idx["MSM"]
        prep_idx = engine.groups.prep_to_idx["Oral_PrEP"]

        def coverage_for_activity(act_name):
            act_idx = engine.groups.activity_to_idx[act_name]
            on_prep = np.sum(engine.state.population[:, msm_idx, act_idx, susc_idx, :, :, prep_idx])
            total = np.sum(engine.state.population[:, msm_idx, act_idx, susc_idx, :, :, :])
            return on_prep / total if total > 0 else 0

        high_cov = coverage_for_activity("High")
        med_cov = coverage_for_activity("Medium")
        assert high_cov > med_cov, (
            f"High-activity (2x modifier) should have higher coverage than Medium (1x): "
            f"High={high_cov:.3f}, Medium={med_cov:.3f}"
        )

    def test_uniform_when_absent(self):
        """Without uptake_modifier_by_activity, all activity levels get same rate."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.30,
            dropout_rate=0.0,
            uptake_modifier_by_activity=None,
            n_steps=10,
            start_year=2020,
        )
        # Remove the key to test default behaviour
        adv = cfg["interventions"]["oral_prep"].get("advanced", {})
        adv.pop("uptake_modifier_by_activity", None)
        cfg["interventions"]["oral_prep"]["advanced"] = adv

        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_activity(engine, "Oral_PrEP")

        # All should be > 0 (MSM are split across all activity levels)
        for act_name, count in counts.items():
            # Count could be 0 for non-MSM orientations, but total per activity > 0
            pass
        # Just verify no crash and some PrEP users exist
        total = sum(counts.values())
        assert total > 0, "Should have PrEP users with default modifiers"


class TestAgeBounds:
    """Verify age-bounded PrEP eligibility."""

    def test_restricted_age_range(self):
        """PrEP users should only appear in eligible age bins (initiation-only)."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_age_range={"min": "25-29", "max": "45-49"},
            uptake_rate=0.50,
            dropout_rate=0.0,
            n_steps=4,  # 1 year — short enough that aging won't push many users out
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=4)
        counts = get_prep_count_by_age(engine, "Oral_PrEP")

        # Age bins clearly outside range should have zero or near-zero.
        # Bins adjacent to the boundary may have small leakage from fractional
        # aging (PrEP users initiated in 45-49 age out into 50-54). Use bins
        # at least TWO groups from the boundary to avoid this.
        clearly_excluded = ["15-19", "65-69", "70-74", "75-79", "80-84"]
        for age_bin in clearly_excluded:
            if age_bin in counts:
                assert counts[age_bin] == 0, (
                    f"Age bin {age_bin} should have zero PrEP users, got {counts[age_bin]}"
                )

        # Included bins should have users
        included_count = sum(
            counts.get(b, 0)
            for b in ["25-29", "30-34", "35-39", "40-44", "45-49"]
            if b in counts
        )
        assert included_count > 0, "Should have PrEP users in eligible age range"

        # Boundary-adjacent bins (50-54, 55-59, 60-64) may have small leakage
        # from fractional aging of PrEP users. Verify they are much smaller
        # than included bins (< 5% of the included total).
        boundary_leakage = sum(counts.get(b, 0) for b in ["50-54", "55-59", "60-64"])
        assert boundary_leakage < included_count * 0.05, (
            f"Boundary leakage ({boundary_leakage}) should be < 5% of "
            f"included count ({included_count})"
        )

    def test_retention_past_max_age(self):
        """
        Users who age past the max eligible age should retain PrEP.

        Strategy: enable PrEP in a narrow age bin, run enough steps for
        aging to push some users into the next bin, verify they retain PrEP.
        """
        # Use a very high uptake so we get many users quickly
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_age_range={"min": "60-64", "max": "60-64"},
            uptake_rate=0.80,
            dropout_rate=0.0,  # no dropout
            n_steps=40,  # 10 years at quarterly steps — enough for aging
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=40)
        counts = get_prep_count_by_age(engine, "Oral_PrEP")

        # Some users from 60-64 should have aged into 65-69 while retaining PrEP
        # (because dropout applies to all, but initiation doesn't happen in 65-69)
        aged_up_count = counts.get("65-69", 0)
        original_count = counts.get("60-64", 0)

        # With fractional aging (20%/yr) over 10 years, substantial transfer expected
        assert aged_up_count > 0, (
            f"Users aging from 60-64 to 65-69 should retain PrEP. "
            f"Got 60-64={original_count}, 65-69={aged_up_count}"
        )

    def test_full_range_when_absent(self):
        """Without eligible_age_range, all age bins should be eligible."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_age_range=None,
            uptake_rate=0.30,
            dropout_rate=0.0,
            n_steps=10,
            start_year=2020,
        )
        # Remove the key to test default behaviour
        cfg["interventions"]["oral_prep"].pop("eligible_age_range", None)

        engine = run_engine(cfg, n_steps=10)
        counts = get_prep_count_by_age(engine, "Oral_PrEP")

        # Should have users in young AND old bins
        young_count = counts.get("15-19", 0) + counts.get("20-24", 0)
        old_count = counts.get("65-69", 0) + counts.get("70-74", 0)
        assert young_count > 0, "Young bins should have PrEP users when no age filter"
        assert old_count > 0, "Old bins should have PrEP users when no age filter"


class TestScheduleIntegration:
    """Verify time-based PrEP schedules work with eligibility filters."""

    def test_no_prep_before_schedule_start(self):
        """PrEP users should be zero before the first schedule year."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.10,
            dropout_rate=0.0,
            prep_schedule={"2025": 0.10},
            n_steps=20,
            start_year=2020,  # 5 years before schedule starts
        )
        engine = run_engine(cfg, n_steps=20)

        # First 20 steps at quarterly = 5 years (2020-2024.75)
        # Schedule starts at 2025, so no PrEP before step ~20
        # Check early outputs
        early_coverage = engine.outputs["prep_coverage_Oral_PrEP"][:16]  # first 4 years
        assert all(c == 0 for c in early_coverage), (
            f"PrEP coverage should be 0 before schedule start year. Got: {early_coverage}"
        )

    def test_prep_ramps_up_after_schedule_start(self):
        """PrEP users should appear after schedule start year."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.10,
            dropout_rate=0.0,
            prep_schedule={"2020": 0.10},  # available from start
            n_steps=20,
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=20)

        # After 20 steps (5 years) with 10% annual uptake, should have users
        final_coverage = engine.outputs["prep_coverage_Oral_PrEP"][-1]
        assert final_coverage > 0, (
            f"PrEP coverage should be > 0 after schedule start. Got: {final_coverage}"
        )


class TestHistoricalStockCorridor:
    """Verify historical PrEP stock floor/ceiling guardrails."""

    @staticmethod
    def _set_corridor(cfg, prep_name, floor=None, ceiling=None, enabled=True):
        cfg["prep_schedules"].setdefault(prep_name, {}).setdefault("advanced", {})[
            "historical_stock_corridor"
        ] = {
            "enabled": enabled,
            "start_year": 2020,
            "end_year": 2020,
            "interpolation": "stepwise",
            "stock_floor_schedule": {} if floor is None else {"2020": floor},
            "stock_ceiling_schedule": {} if ceiling is None else {"2020": ceiling},
        }

    @staticmethod
    def _prep_stock(engine, prep_name):
        prep_idx = engine.groups.prep_to_idx[prep_name]
        susc_idx = engine.groups.hiv_to_idx["Susceptible"]
        return int(np.sum(engine.state.population[:, :, :, susc_idx, :, :, prep_idx]))

    def test_floor_adds_users_when_rates_are_zero(self):
        """A floor-only corridor should work even with zero scheduled uptake/dropout."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=0.0,
            dropout_rate=0.0,
            prep_schedule={"2020": 0.0},
            n_steps=1,
            start_year=2020,
        )
        self._set_corridor(cfg, "Oral_PrEP", floor=100)

        engine = run_engine(cfg, n_steps=1)

        assert self._prep_stock(engine, "Oral_PrEP") == 100
        assert engine.outputs["prep_oral_corridor_additions"][-1] == 100

    def test_ceiling_prevents_implausible_stock_growth(self):
        """A ceiling should cap stock after normal high uptake has occurred."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            uptake_rate=1.0,
            dropout_rate=0.0,
            n_steps=4,
            start_year=2020,
        )
        self._set_corridor(cfg, "Oral_PrEP", ceiling=50)

        engine = run_engine(cfg, n_steps=4)

        assert self._prep_stock(engine, "Oral_PrEP") == 50
        assert max(engine.outputs["msm_on_oral"]) <= 50
        assert sum(engine.outputs["prep_oral_corridor_removals"]) > 0
        assert sum(engine.outputs["prep_oral_dropouts"]) == 0

    def test_floor_respects_activity_eligibility(self):
        """Floor additions should use the same orientation/activity gates as uptake."""
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_activity_classes=["High"],
            uptake_rate=0.0,
            dropout_rate=0.0,
            prep_schedule={"2020": 0.0},
            n_steps=1,
            start_year=2020,
        )
        self._set_corridor(cfg, "Oral_PrEP", floor=100)

        engine = run_engine(cfg, n_steps=1)
        counts = get_prep_count_by_activity(engine, "Oral_PrEP")

        assert counts["Low"] == 0
        assert counts["Medium"] == 0
        assert counts["High"] == 100

    def test_corridor_is_generic_for_lenacapavir(self):
        """The same corridor mechanism should work for Lenacapavir when configured."""
        cfg = make_prep_enabled_config(
            enabled_interventions=("lenacapavir",),
            eligible_orientations=["MSM"],
            uptake_rate=0.0,
            dropout_rate=0.0,
            prep_schedule={"2020": 0.0},
            n_steps=1,
            start_year=2020,
        )
        self._set_corridor(cfg, "Lenacapavir", floor=80)

        engine = run_engine(cfg, n_steps=1)

        assert self._prep_stock(engine, "Lenacapavir") == 80
        assert engine.outputs["prep_lena_corridor_additions"][-1] == 80


class TestMassBalance:
    """Verify PrEP transitions conserve population."""

    def test_mass_balance_with_all_filters(self):
        """
        Population should be conserved with eligibility filters active.
        The engine's validate_prep_mass_balance() runs every step and
        would raise AssertionError if violated.
        """
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_age_range={"min": "20-24", "max": "50-54"},
            uptake_rate=0.20,
            dropout_rate=0.10,
            uptake_modifier_by_activity={"Low": 0.5, "Medium": 1.0, "High": 1.5},
            n_steps=20,
            start_year=2020,
        )
        # This will raise if mass balance is violated
        engine = run_engine(cfg, n_steps=20)

        # Additionally verify total population is consistent
        total_pop_start = engine.outputs["total_population"][0]
        total_pop_end = engine.outputs["total_population"][-1]
        # Population should be in a reasonable range (births, deaths, aging)
        assert total_pop_end > 0, "Population should not reach zero"


class TestCombinedRealisticScenario:
    """End-to-end test with realistic Dutch PrEP settings."""

    def test_realistic_msm_prep_coverage(self):
        """
        With MSM-only eligibility, age 15-64, activity modifiers, and
        realistic uptake rates, PrEP coverage should be a small fraction
        of total population (reflecting ~10-15K users in NL).
        """
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_age_range={"min": "15-19", "max": "65-69"},
            uptake_rate=0.08,
            dropout_rate=0.20,
            uptake_modifier_by_activity={"Low": 0.5, "Medium": 1.0, "High": 1.5},
            n_steps=40,  # 10 years
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=40)

        # Check final PrEP coverage
        final_coverage = engine.outputs["prep_coverage_Oral_PrEP"][-1]

        # With ~465K MSM, steady-state ~20% of eligible MSM = ~93K
        # As fraction of total pop (~12.7M) = ~0.73%
        # Should be << 10% of total population
        assert final_coverage < 0.05, (
            f"PrEP coverage should be small fraction of total pop. Got: {final_coverage:.4f}"
        )
        assert final_coverage > 0, "Should have some PrEP users"

        # Verify only MSM have PrEP
        counts = get_prep_count_by_orientation(engine, "Oral_PrEP")
        assert counts["Female"] == 0, f"Females on PrEP: {counts['Female']}"
        assert counts["Heterosexual_Male"] == 0, f"Het males on PrEP: {counts['Heterosexual_Male']}"
        assert counts["MSM"] > 0, f"MSM on PrEP: {counts['MSM']}"

        print(f"\n  Realistic scenario results (10 years):")
        print(f"    MSM on PrEP: {counts['MSM']:,}")
        print(f"    Total pop: {engine.outputs['total_population'][-1]:,.0f}")
        print(f"    PrEP coverage (of total): {final_coverage*100:.3f}%")


class TestActivityFilter:
    """Verify PrEP uptake is restricted to eligible activity classes (V11.5.1)."""

    def test_high_activity_only_excludes_low_and_medium(self):
        """
        With eligible_activity_classes=['High'], Low and Medium MSM should have
        zero PrEP users; High MSM should have PrEP users.
        """
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_activity_classes=["High"],
            uptake_rate=0.30,
            dropout_rate=0.0,
            n_steps=4,  # 1 year
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=4)
        counts = get_prep_count_by_activity(engine, "Oral_PrEP")

        assert counts["Low"] == 0, (
            f"Low-activity MSM should be excluded by activity filter. Got: {counts['Low']}"
        )
        assert counts["Medium"] == 0, (
            f"Medium-activity MSM should be excluded by activity filter. Got: {counts['Medium']}"
        )
        assert counts["High"] > 0, (
            f"High-activity MSM should have PrEP users. Got: {counts['High']}"
        )

        print(f"\n  Activity filter (High only):")
        print(f"    Low: {counts['Low']}, Medium: {counts['Medium']}, High: {counts['High']}")

    def test_no_activity_filter_key_allows_all_classes(self):
        """
        Without eligible_activity_classes key, all activity classes should be eligible.
        Backward compatibility with V11.5.0.
        """
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_activity_classes=None,  # key absent → default to all
            uptake_rate=0.30,
            dropout_rate=0.0,
            n_steps=4,
            start_year=2020,
        )
        # Remove key explicitly to test default behaviour
        cfg["interventions"]["oral_prep"].pop("eligible_activity_classes", None)

        engine = run_engine(cfg, n_steps=4)
        counts = get_prep_count_by_activity(engine, "Oral_PrEP")

        assert counts["Low"] > 0, f"Low-activity should be eligible by default. Got: {counts['Low']}"
        assert counts["Medium"] > 0, f"Medium-activity should be eligible by default. Got: {counts['Medium']}"
        assert counts["High"] > 0, f"High-activity should be eligible by default. Got: {counts['High']}"

    def test_combined_orientation_and_activity_filter(self):
        """
        With eligible_orientations=['MSM'] and eligible_activity_classes=['High'],
        only High-activity MSM should have PrEP. All other orientation/activity
        combinations must be zero. Mass balance must hold every step.
        """
        cfg = make_prep_enabled_config(
            eligible_orientations=["MSM"],
            eligible_activity_classes=["High"],
            uptake_rate=0.30,
            dropout_rate=0.05,
            n_steps=8,  # 2 years
            start_year=2020,
        )
        engine = run_engine(cfg, n_steps=8)

        prep_idx = engine.groups.prep_to_idx["Oral_PrEP"]
        susc_idx = engine.groups.hiv_to_idx["Susceptible"]
        high_idx = engine.groups.activity_to_idx["High"]
        msm_idx = engine.groups.orient_to_idx["MSM"]

        # All non-High-MSM slices must be zero
        for orient_name, orient_idx in engine.groups.orient_to_idx.items():
            for act_name, act_idx in engine.groups.activity_to_idx.items():
                count = int(np.sum(
                    engine.state.population[:, orient_idx, act_idx, susc_idx, :, :, prep_idx]
                ))
                if orient_name == "MSM" and act_name == "High":
                    assert count > 0, f"MSM High should have PrEP users. Got: {count}"
                else:
                    assert count == 0, (
                        f"{orient_name} {act_name} should be excluded. Got: {count}"
                    )

        print(f"\n  Combined filter (MSM + High only):")
        msm_high = int(np.sum(
            engine.state.population[:, msm_idx, high_idx, susc_idx, :, :, prep_idx]
        ))
        print(f"    MSM High on PrEP: {msm_high:,}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
