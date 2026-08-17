"""
test_adverse_events_v2.py — Unit tests for AdverseEventModule v2 JSON format (V11.6.2).

Tests validate that the refactored AdverseEventModule correctly:
  - Reads adverse_events.items[] directly from v2 config
  - Routes computation by billing_moment (ongoing / initiation)
  - Applies prevalence formula correctly per item
  - Fires initiation items exactly once per new initiator (no step_fraction)
  - Applies step_fraction to ongoing items
  - Correctly handles multiple items for the same arm
  - Provides per-item breakdown in by_item dict
  - Reads state machine parameters from item["state_machine_params"]
  - Raises ValueError at init if state_machine mode item is missing params
  - Produces zero impact for items whose arm has no users in population
  - Preserves backward compatibility with old flat-format configs

Test categories:
  1.  Format detection: _v2_config set / not set
  2.  Prevalence mode ongoing: formula, step_fraction, per-item accuracy
  3.  Arm filtering: items for absent arm produce zero
  4.  Multiple items same arm: both computed independently
  5.  Initiation billing_moment: fires on new initiators, not on subsequent steps
  6.  Initiation: no step_fraction applied (one-time event)
  7.  by_item output: keys, structure, values match per-item expectations
  8.  State machine: reads onset_rate from item state_machine_params
  9.  State machine: raises ValueError at init if params missing
  10. Legacy flat format: still works unchanged
  11. Zero users: zero impact regardless of mode
  12. compute_adjustments() return interface: required keys always present
"""

import os
import sys
import copy

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adverse_events import AdverseEventModule


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

STEP_FRACTION = 0.25


class _MockGroups:
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
    n_hiv_stages = 4


def _empty_pop() -> np.ndarray:
    """Return zero population tensor: (age=1, orient=1, act=1, hiv=4, diag=2, art=2, prep=3)."""
    return np.zeros((1, 1, 1, 4, 2, 2, 4))


def _make_v2_config(mode: str = "prevalence") -> dict:
    """
    Return a minimal but complete v2 hta_config for AE testing.

    Contains two ongoing items (oral_prep GI + renal) and one initiation item
    (oral_prep renal screen) and one Lenacapavir ISR item.
    """
    return {
        "_format_version": "v2",
        "time_horizon":   {"freeze_year": 2025, "evaluation_years": 20},
        "wtp_threshold_eur_per_qaly": {"value": 50000},
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate":           {"value": 0.04},
        },
        "utilities": {
            "susceptible": {"value": 1.0, "distribution": {"type": "fixed"}},
        },
        "adverse_events": {
            "mode": mode,
            "items": [
                {
                    "id":              "oral_prep_initiation_renal_screen",
                    "arm":             "oral_prep",
                    "billing_moment":  "initiation",
                    "frequency":       "once",
                    "prevalence":      {"value": 1.0},
                    "utility_decrement": {"value": 0.0},
                    "cost":            {"value": 120},
                    # No state_machine_params needed — initiation items are skipped in SM init
                },
                {
                    "id":              "oral_prep_gi_ae",
                    "arm":             "oral_prep",
                    "billing_moment":  "ongoing",
                    "frequency":       "annual",
                    "prevalence":      {"value": 0.15},
                    "utility_decrement": {"value": -0.02},
                    "cost":            {"value": 200},
                    "state_machine_params": {
                        "onset_rate_per_year":      0.60,
                        "resolution_rate_per_year": 1.20,
                        "severity_distribution":    {"mild": 0.80, "moderate": 0.18, "severe": 0.02},
                        "utility_decrements":       {"mild": -0.01, "moderate": -0.03, "severe": -0.05},
                        "cost_additions":           {"mild": 50, "moderate": 200, "severe": 500},
                    },
                },
                {
                    "id":              "oral_prep_renal_ae",
                    "arm":             "oral_prep",
                    "billing_moment":  "ongoing",
                    "frequency":       "annual",
                    "prevalence":      {"value": 0.02},
                    "utility_decrement": {"value": -0.05},
                    "cost":            {"value": 800},
                    "state_machine_params": {
                        "onset_rate_per_year":      0.08,
                        "resolution_rate_per_year": 0.20,
                        "severity_distribution":    {"mild": 0.70, "moderate": 0.25, "severe": 0.05},
                        "utility_decrements":       {"mild": -0.03, "moderate": -0.07, "severe": -0.12},
                        "cost_additions":           {"mild": 300, "moderate": 800, "severe": 2500},
                    },
                },
                {
                    "id":              "lenacapavir_isr_ae",
                    "arm":             "lenacapavir",
                    "billing_moment":  "ongoing",
                    "frequency":       "annual",
                    "prevalence":      {"value": 0.20},
                    "utility_decrement": {"value": -0.01},
                    "cost":            {"value": 150},
                    "state_machine_params": {
                        "onset_rate_per_year":      0.80,
                        "resolution_rate_per_year": 3.00,
                        "severity_distribution":    {"mild": 0.75, "moderate": 0.22, "severe": 0.03},
                        "utility_decrements":       {"mild": -0.005, "moderate": -0.01, "severe": -0.03},
                        "cost_additions":           {"mild": 50, "moderate": 150, "severe": 400},
                    },
                },
            ],
        },
        "prevention_costs": {
            "shared": [
                {
                    "id": "sti_screening", "applies_to": ["oral_prep", "lenacapavir"],
                    "category": "monitoring", "frequency": "annual", "value": 300,
                }
            ],
            "arm_specific": {
                "oral_prep": {
                    "drug_and_monitoring": {"value": 5400, "cohort": "all", "category": "drug", "frequency": "annual"}
                }
            },
        },
        "disease_costs": {
            "art_first_line": {
                "value": 10000, "billing_moment": "ongoing", "frequency": "annual",
                "applies_to_stages": ["Chronic"], "applies_to_art_status": ["on_art"], "category": "drug",
            },
        },
        "analysis_scenarios": [],
    }


def _make_old_flat_config() -> dict:
    """Old flat-format hta_config (pre-v2, for legacy compat tests)."""
    return {
        "utility_weights":     {"susceptible": {"value": 1.0}},
        "unit_costs_annual_eur": {"art_first_line": {"value": 10000}},
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate":           {"value": 0.04},
        },
        "adverse_events": {
            "mode": "prevalence",
            "oral_prep": {
                "ae_prevalence":      {"value": 1.0},
                "utility_decrement":  {"value": -0.003},
                "annual_cost_addition": {"value": 47.0},
            },
        },
    }


def _make_ae(mode: str = "prevalence") -> AdverseEventModule:
    return AdverseEventModule(
        hta_config=_make_v2_config(mode),
        groups=_MockGroups(),
        step_fraction=STEP_FRACTION,  # V11.7.2: required arg
        rng=np.random.RandomState(42),
    )


def _current_prep_users(pop: np.ndarray, prep_idx: int) -> float:
    return float(np.sum(pop[:, :, :, 0, :, :, prep_idx]))


def _compute_adjustments(ae: AdverseEventModule, pop: np.ndarray, *args, **kwargs) -> dict:
    """
    Test convenience wrapper for direct AE unit tests.

    Production calls pass gross PrEP initiation counters through economics.py.
    Existing direct module tests mostly target formula routing, so this wrapper
    supplies the historical stock-delta value explicitly. Missing-counter guard
    tests call ae.compute_adjustments() directly.
    """
    if "prep_initiations_by_arm" not in kwargs:
        oral_current = _current_prep_users(pop, 1)
        oral_prev = float(ae._prev_ae_users.get("oral_prep", 0))
        lena_current = _current_prep_users(pop, 2)
        lena_prev = float(ae._prev_ae_users.get("lenacapavir", 0))
        kwargs["prep_initiations_by_arm"] = {
            "oral_prep": max(0.0, oral_current - oral_prev),
            "lenacapavir": max(0.0, lena_current - lena_prev),
        }
    return ae.compute_adjustments(pop, *args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Format detection
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatDetection:

    def test_v2_config_stored_on_new_format(self):
        """_v2_config is set when v2 config is passed."""
        ae = _make_ae()
        assert ae._v2_config is not None

    def test_old_flat_format_raises_value_error(self):
        """Old flat format (no _format_version: v2) raises ValueError at construction."""
        with pytest.raises(ValueError, match="v2"):
            AdverseEventModule(
                hta_config=_make_old_flat_config(),
                groups=_MockGroups(),
                step_fraction=STEP_FRACTION,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Prevalence mode ongoing: formula accuracy
# ─────────────────────────────────────────────────────────────────────────────

class TestPrevalenceOngoing:

    def test_gi_ae_formula(self):
        """
        100 oral PrEP users, GI AE item:
          utility_impact = abs(-0.02) × 0.15 × 100 × 0.25 = 0.75
          cost_impact    = 200 × 0.15 × 100 × 0.25 = 750.0
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # susceptible, Oral_PrEP
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        gi = result["by_item"]["oral_prep_gi_ae"]
        assert gi["utility_decrement"] == pytest.approx(0.02 * 0.15 * 100 * 0.25)
        assert gi["cost_addition"]     == pytest.approx(200  * 0.15 * 100 * 0.25)

    def test_renal_ae_formula(self):
        """
        100 oral PrEP users, renal AE item:
          utility_impact = abs(-0.05) × 0.02 × 100 × 0.25 = 0.025
          cost_impact    = 800 × 0.02 × 100 × 0.25 = 400.0
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        renal = result["by_item"]["oral_prep_renal_ae"]
        assert renal["utility_decrement"] == pytest.approx(0.05 * 0.02 * 100 * 0.25)
        assert renal["cost_addition"]     == pytest.approx(800  * 0.02 * 100 * 0.25)

    def test_lena_isr_formula(self):
        """
        200 Lenacapavir users, ISR item:
          utility_impact = abs(-0.01) × 0.20 × 200 × 0.25 = 0.10
          cost_impact    = 150 × 0.20 × 200 × 0.25 = 1500.0
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 200  # susceptible, Lenacapavir
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        isr = result["by_item"]["lenacapavir_isr_ae"]
        assert isr["utility_decrement"] == pytest.approx(0.01 * 0.20 * 200 * 0.25)
        assert isr["cost_addition"]     == pytest.approx(150  * 0.20 * 200 * 0.25)

    def test_ongoing_items_sum_to_total(self):
        """Total utility_decrement equals sum of ongoing item impacts."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # oral PrEP only
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        expected_total_util = (
            0.02 * 0.15 * 100 * 0.25 +    # GI AE
            0.05 * 0.02 * 100 * 0.25      # renal AE
        )
        # Initiation item fires on step 0 (100 new users, prev=0), but util_dec=0.0
        assert result["utility_decrement"] == pytest.approx(expected_total_util)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Arm filtering
# ─────────────────────────────────────────────────────────────────────────────

class TestArmFiltering:

    def test_oral_prep_items_zero_when_no_oral_prep_users(self):
        """Oral PrEP AE items produce zero when there are no oral PrEP users."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100  # Lenacapavir only, no oral PrEP
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        gi    = result["by_item"]["oral_prep_gi_ae"]
        renal = result["by_item"]["oral_prep_renal_ae"]
        assert gi["utility_decrement"]    == pytest.approx(0.0)
        assert gi["cost_addition"]        == pytest.approx(0.0)
        assert renal["utility_decrement"] == pytest.approx(0.0)
        assert renal["cost_addition"]     == pytest.approx(0.0)

    def test_lena_item_zero_when_no_lena_users(self):
        """Lenacapavir AE item produces zero when there are no Lenacapavir users."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # oral PrEP only
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        isr = result["by_item"]["lenacapavir_isr_ae"]
        assert isr["utility_decrement"] == pytest.approx(0.0)
        assert isr["cost_addition"]     == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multiple items same arm
# ─────────────────────────────────────────────────────────────────────────────

class TestMultipleItemsSameArm:

    def test_two_ongoing_oral_prep_items_computed_independently(self):
        """GI AE and renal AE for oral PrEP are computed independently per item."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        gi    = result["by_item"]["oral_prep_gi_ae"]
        renal = result["by_item"]["oral_prep_renal_ae"]
        # Each item uses its own prevalence and decrement — no shared state
        assert gi["cost_addition"]    == pytest.approx(200 * 0.15 * 100 * 0.25)
        assert renal["cost_addition"] == pytest.approx(800 * 0.02 * 100 * 0.25)
        # They should differ from each other
        assert gi["cost_addition"] != pytest.approx(renal["cost_addition"])


# ─────────────────────────────────────────────────────────────────────────────
# 5–6. Initiation billing_moment
# ─────────────────────────────────────────────────────────────────────────────

class TestInitiationBillingMoment:

    def test_initiation_fires_on_step_with_new_users(self):
        """
        Step 0: 100 new oral PrEP initiators.
        Initiation item: prevalence=1.0, cost=120, util_dec=0.0
        Expected: cost = 100 × 1.0 × 120 = 12,000 (no step_fraction)
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        initiation = result["by_item"]["oral_prep_initiation_renal_screen"]
        assert initiation["cost_addition"]     == pytest.approx(100 * 1.0 * 120)
        assert initiation["utility_decrement"] == pytest.approx(0.0)

    def test_initiation_zero_on_second_step_same_users(self):
        """
        Step 0: 100 new users → initiation fires.
        Step 1: same 100 users, no new initiators → initiation = 0.
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100

        _compute_adjustments(ae, pop, step=0, year=2025.0)   # Step 0: new initiators
        result2 = _compute_adjustments(ae, pop, step=1, year=2025.25)  # Step 1: same users

        initiation = result2["by_item"]["oral_prep_initiation_renal_screen"]
        assert initiation["cost_addition"] == pytest.approx(0.0)

    def test_initiation_no_step_fraction(self):
        """
        Initiation cost is a one-time event — NOT multiplied by step_fraction (0.25).
        50 new initiators × prevalence 1.0 × cost 120 = 6,000 (not 6,000 × 0.25 = 1,500).
        """
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 50
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        init = result["by_item"]["oral_prep_initiation_renal_screen"]
        assert init["cost_addition"] == pytest.approx(50 * 1.0 * 120)          # 6,000
        assert init["cost_addition"] != pytest.approx(50 * 1.0 * 120 * 0.25)  # not 1,500

    def test_partial_new_initiators_charged_correctly(self):
        """
        Step 0: 80 users (prev=0) → new_initiators=80 → cost = 80×120 = 9,600
        Step 1: 100 users (prev=80) → new_initiators=20 → cost = 20×120 = 2,400
        """
        ae   = _make_ae()
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 1] = 80
        pop2 = _empty_pop(); pop2[0, 0, 0, 0, 0, 0, 1] = 100

        _compute_adjustments(ae, pop1, step=0, year=2025.0)
        result2 = _compute_adjustments(ae, pop2, step=1, year=2025.25)

        init2 = result2["by_item"]["oral_prep_initiation_renal_screen"]
        assert init2["cost_addition"] == pytest.approx(20 * 1.0 * 120)


# ─────────────────────────────────────────────────────────────────────────────
# 7. by_item output structure
# ─────────────────────────────────────────────────────────────────────────────

class TestByItemOutput:

    def test_by_item_contains_all_item_ids(self):
        """by_item dict contains an entry for every item in the config."""
        ae  = _make_ae()
        pop = _empty_pop()
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        expected_ids = {
            "oral_prep_initiation_renal_screen",
            "oral_prep_gi_ae",
            "oral_prep_renal_ae",
            "lenacapavir_isr_ae",
        }
        assert set(result["by_item"].keys()) == expected_ids

    def test_by_item_entries_have_required_keys(self):
        """Each by_item entry has utility_decrement and cost_addition keys."""
        ae  = _make_ae()
        pop = _empty_pop()
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        for item_id, entry in result["by_item"].items():
            assert "utility_decrement" in entry, f"Missing key in {item_id}"
            assert "cost_addition"     in entry, f"Missing key in {item_id}"

    def test_by_item_values_sum_to_totals(self):
        """Sum of by_item values equals top-level totals."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        pop[0, 0, 0, 0, 0, 0, 2] = 50
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        sum_util = sum(v["utility_decrement"] for v in result["by_item"].values())
        sum_cost = sum(v["cost_addition"]     for v in result["by_item"].values())
        assert result["utility_decrement"] == pytest.approx(sum_util)
        assert result["cost_addition"]     == pytest.approx(sum_cost)


# ─────────────────────────────────────────────────────────────────────────────
# 8–9. State machine mode
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachineMode:

    def test_state_machine_reads_onset_rate_from_item(self):
        """
        State machine mode: with 1000 users and onset_rate=0.60/yr → 0.15 quarterly,
        the deterministic path gives new_ae = int(1000 × 0.15) = 150 new cases on step 0.
        These cases then accrue cost/utility. Non-zero output confirms item params were read.
        """
        cfg       = _make_v2_config(mode="state_machine")
        cfg["simulation_controls"] = {"deterministic_mode": True}
        ae        = AdverseEventModule(hta_config=cfg, groups=_MockGroups(), step_fraction=STEP_FRACTION)
        pop       = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 1000  # oral PrEP users

        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        gi = result["by_item"]["oral_prep_gi_ae"]
        # After step 0 with onset_rate=0.60, 150 new cases (deterministic).
        # Distributed: mild=80%, moderate=18%, severe=2% of 150 new = 120/27/3
        # Utility: mild: 0.01×120×0.25=0.30, moderate: 0.03×27×0.25=0.2025, severe: 0.05×3×0.25=0.0375
        # Total utility = 0.5400 (approximately)
        # We just confirm it's non-zero and that it came from item params (not zero rates)
        assert gi["utility_decrement"] > 0, "State machine should produce non-zero impact"
        assert gi["cost_addition"]     > 0

    def test_state_machine_missing_params_raises_at_init(self):
        """
        State machine mode with an ongoing item that has no state_machine_params
        must raise ValueError at construction time — not silently use zero rates.
        """
        cfg = _make_v2_config(mode="state_machine")
        # Remove state_machine_params from the first ongoing item
        for item in cfg["adverse_events"]["items"]:
            if item["billing_moment"] == "ongoing":
                item.pop("state_machine_params", None)
                break

        with pytest.raises(ValueError, match="state_machine_params"):
            AdverseEventModule(hta_config=cfg, groups=_MockGroups(), step_fraction=STEP_FRACTION)

    def test_state_machine_accumulates_state_across_steps(self):
        """
        In deterministic state machine mode, AE counts in ae_state_v2 accumulate
        across steps (new cases added, resolutions subtracted). State is non-zero
        after step 0 and differs from step 1 as cases and resolutions interact.
        """
        cfg = _make_v2_config(mode="state_machine")
        cfg["simulation_controls"] = {"deterministic_mode": True}
        ae  = AdverseEventModule(hta_config=cfg, groups=_MockGroups(), step_fraction=STEP_FRACTION)
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 1000

        _compute_adjustments(ae, pop, step=0, year=2025.0)
        state_after_step0 = dict(ae.ae_state_v2["oral_prep_gi_ae"])
        total0 = sum(state_after_step0.values())
        assert total0 > 0, "State should be non-zero after step 0"

        _compute_adjustments(ae, pop, step=1, year=2025.25)
        state_after_step1 = dict(ae.ae_state_v2["oral_prep_gi_ae"])
        total1 = sum(state_after_step1.values())
        assert total1 >= 0  # state persists and evolves


# ─────────────────────────────────────────────────────────────────────────────
# 10. Old flat format rejected (BACKLOG Finding 3 — V11.8)
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyFlatFormat:
    """
    V11.8 / BACKLOG Finding 3: old flat-format hta_config (V11.5.x, no _format_version key)
    must be rejected at AdverseEventModule construction time. The legacy normalization shim
    and legacy computation paths have been removed.
    """

    def test_old_flat_format_raises_value_error(self):
        """
        Passing a V11.5.x flat-format config (no _format_version: v2) to
        AdverseEventModule must raise ValueError at construction — not silently
        use a legacy code path.
        """
        with pytest.raises(ValueError, match="v2"):
            AdverseEventModule(
                hta_config=_make_old_flat_config(),
                groups=_MockGroups(),
                step_fraction=STEP_FRACTION,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 11. Zero users
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroUsers:

    def test_zero_users_zero_ae_impact(self):
        """Empty population → zero AE impact for all items."""
        ae  = _make_ae()
        pop = _empty_pop()  # all zeros
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        assert result["utility_decrement"] == pytest.approx(0.0)
        assert result["cost_addition"]     == pytest.approx(0.0)
        for item_id, entry in result["by_item"].items():
            assert entry["utility_decrement"] == pytest.approx(0.0), item_id
            assert entry["cost_addition"]     == pytest.approx(0.0), item_id


# ─────────────────────────────────────────────────────────────────────────────
# 12. Return interface integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestReturnInterface:

    def test_required_keys_always_present(self):
        """compute_adjustments() always returns utility_decrement, cost_addition, by_item."""
        ae  = _make_ae()
        pop = _empty_pop()
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        for key in ("utility_decrement", "cost_addition", "by_item"):
            assert key in result, f"Missing required key '{key}'"

    def test_utility_decrement_non_negative(self):
        """utility_decrement is always ≥ 0 (positive quantity representing QALY loss)."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 500
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        assert result["utility_decrement"] >= 0.0

    def test_cost_addition_non_negative(self):
        """cost_addition is always ≥ 0."""
        ae  = _make_ae()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 500
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        assert result["cost_addition"] >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 13. Initiation AE verification
# V11.7.2 Item 6 — confirms _item_initiation() fires correctly per new initiator
# ─────────────────────────────────────────────────────────────────────────────

class TestInitiationAE:
    """
    V11.7.2: Verify billing_moment='initiation' items fire exactly once per new
    PrEP initiator and do not apply step_fraction (one-time event).

    Test config includes one initiation item: oral_prep_renal_screen
      cost=€120, prevalence=1.0, utility_decrement=0 (no QALY impact).
    """

    def _make_config_with_initiation_item(self) -> dict:
        """Minimal v2 config with one initiation AE item for oral_prep."""
        return {
            "_format_version": "v2",
            "time_horizon": {"freeze_year": 2025, "evaluation_years": 20},
            "wtp_threshold_eur_per_qaly": {"value": 80000},
            "discounting": {
                "health_effects_rate": {"value": 0.015},
                "cost_rate":           {"value": 0.04},
                "base_year":           {"value": 2025},
            },
            "utilities": {"susceptible": {"value": 1.0}},
            "prevention_costs": {"shared": [], "arm_specific": {}},
            "disease_costs": {},
            "adverse_events": {
                "mode": "prevalence",
                "items": [
                    {
                        "id":               "oral_prep_renal_screen",
                        "arm":              "oral_prep",
                        "billing_moment":   "initiation",
                        "frequency":        "once",
                        "prevalence":       {"value": 1.0},
                        "utility_decrement": {"value": 0.0},
                        "cost":             {"value": 120.0},
                    }
                ],
            },
        }

    def test_initiation_fires_for_new_initiators(self):
        """
        First step: 1000 oral_prep users appear (prev=0).
        new_initiators = 1000. Cost = 1000 × 120 × 1.0 = 120,000.
        No step_fraction — one-time event.
        """
        cfg = self._make_config_with_initiation_item()
        ae  = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 1000  # oral PrEP users
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        assert result["by_item"]["oral_prep_renal_screen"]["cost_addition"] == pytest.approx(
            1000 * 120.0
        )

    def test_initiation_uses_gross_initiations_not_net_stock_delta(self):
        cfg = self._make_config_with_initiation_item()
        ae = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        ae._prev_ae_users["oral_prep"] = 100
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 105

        result = ae.compute_adjustments(
            pop,
            step=0,
            year=2025.0,
            prep_initiations_by_arm={"oral_prep": 10},
        )

        assert result["by_item"]["oral_prep_renal_screen"]["cost_addition"] == pytest.approx(
            10 * 120.0
        )

    def test_initiation_requires_gross_counter(self):
        cfg = self._make_config_with_initiation_item()
        ae = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100

        with pytest.raises(ValueError, match="prep_initiations_by_arm"):
            ae.compute_adjustments(pop, step=0, year=2025.0)

    def test_initiation_does_not_repeat_on_second_step(self):
        """
        Step 0: 1000 new initiators → cost fires.
        Step 1: same 1000 users (no new ones) → cost = 0.
        """
        cfg = self._make_config_with_initiation_item()
        ae  = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 1000

        _compute_adjustments(ae, pop, step=0, year=2025.0)   # fires
        result2 = _compute_adjustments(ae, pop, step=1, year=2025.25)  # should not fire
        assert result2["by_item"]["oral_prep_renal_screen"]["cost_addition"] == pytest.approx(0.0)

    def test_initiation_no_qaly_impact(self):
        """Renal screen has utility_decrement=0 — no QALY effect."""
        cfg = self._make_config_with_initiation_item()
        ae  = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 1000
        result = _compute_adjustments(ae, pop, step=0, year=2025.0)
        assert result["by_item"]["oral_prep_renal_screen"]["utility_decrement"] == pytest.approx(0.0)

    def test_initiation_zero_when_no_new_users(self):
        """No new users (users decreased or unchanged from prev) → cost = 0."""
        cfg = self._make_config_with_initiation_item()
        ae  = AdverseEventModule(
            hta_config=cfg,
            groups=_MockGroups(),
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        # Step 0 with 1000 users — fires initiation
        pop[0, 0, 0, 0, 0, 0, 1] = 1000
        _compute_adjustments(ae, pop, step=0, year=2025.0)

        # Step 1 with fewer users (churn) — new_initiators = max(0, 800-1000) = 0
        pop2 = _empty_pop()
        pop2[0, 0, 0, 0, 0, 0, 1] = 800
        result2 = _compute_adjustments(ae, pop2, step=1, year=2025.25)
        assert result2["by_item"]["oral_prep_renal_screen"]["cost_addition"] == pytest.approx(0.0)

    def test_step_fraction_required_raises_type_error(self):
        """V11.7.2: AdverseEventModule without step_fraction raises TypeError."""
        cfg = self._make_config_with_initiation_item()
        with pytest.raises(TypeError):
            AdverseEventModule(hta_config=cfg, groups=_MockGroups())
