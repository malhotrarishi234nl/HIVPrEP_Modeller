"""
test_economics_v2.py — Unit tests for EconomicsAccumulator v2 JSON format (V11.6.1).

Tests validate that the new accumulate() implementation correctly reads the layered
v2 hta_config structure, routes costs by billing_moment and cohort, applies the
new_diagnoses counter, and maintains backward compatibility with old flat format.

Test categories:
  1.  QALY: Susceptible states (no-PrEP, Oral PrEP, Lenacapavir)
  2.  QALY: HIV+ states (Acute, Chronic on/off ART, AIDS on/off ART)
  3.  Prevention costs: arm_name="no_prep" yields zero prevention costs
  4.  Prevention costs: oral_prep arm (shared STI + drug_and_monitoring, cohort=all)
  5.  Prevention costs: lenacapavir arm, first step (new_initiators cohort)
  6.  Prevention costs: lenacapavir arm, second step (continuing cohort)
  7.  Disease costs: billing_moment=diagnosis fires on new_diagnoses count
  8.  Disease costs: billing_moment=ongoing, Chronic on_art (drug + monitoring)
  9.  Disease costs: billing_moment=ongoing, AIDS off_art (hospitalization only)
  10. Disease costs: ART status filter — off_art gets zero drug cost
  11. Discounting: health and cost discount factors applied at correct year offset
  12. Cost breakdown: categories populated dynamically; legacy keys always present
  13. Step record: step_new_diagnoses stored per timestep
  14. Backward compatibility: old flat format uses legacy path (_v2_config=None)
  15. get_summary: cumul_new_diagnoses accumulated across steps

All expected values are computed from first principles using the config values defined
in _make_v2_config(). No magic numbers without derivation comment.
"""

import os
import sys
import copy

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from economics import EconomicsAccumulator


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

STEP_FRACTION = 0.25  # Quarterly timestep


class _MockGroups:
    """Minimal mock replicating PopulationGroups interface for testing."""
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
    n_hiv_stages = 4
    age_groups  = []  # empty → _working_age_indices = None; safe for all unit tests


def _make_v2_config() -> dict:
    """
    Return a minimal but complete v2 hta_config for unit testing.

    Values match hta_config.json so that expected calculations are directly
    portable to production. Distribution blocks stripped to keep tests concise.
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
            "susceptible":             {"value": 1.000, "distribution": {"type": "fixed"}},
            "susceptible_oral_prep":   {"value": 0.990, "distribution": {"type": "beta", "alpha": 99,  "beta": 1}},
            "susceptible_lenacapavir": {"value": 0.995, "distribution": {"type": "beta", "alpha": 199, "beta": 1}},
            "hiv_acute":               {"value": 0.700, "distribution": {"type": "beta", "alpha": 70,  "beta": 30}},
            "hiv_chronic_on_art":      {"value": 0.850, "distribution": {"type": "beta", "alpha": 85,  "beta": 15}},
            "hiv_chronic_off_art":     {"value": 0.780, "distribution": {"type": "beta", "alpha": 78,  "beta": 22}},
            "aids_on_art":             {"value": 0.600, "distribution": {"type": "beta", "alpha": 60,  "beta": 40}},
            "aids_off_art":            {"value": 0.400, "distribution": {"type": "beta", "alpha": 40,  "beta": 60}},
        },
        "adverse_events": {
            "mode": "prevalence",
            "items": [
                {
                    "id": "oral_prep_gi_ae", "arm": "oral_prep",
                    "billing_moment": "ongoing", "frequency": "annual",
                    "prevalence":        {"value": 0.15},
                    "utility_decrement": {"value": -0.02},
                    "cost":              {"value": 200},
                }
            ],
        },
        "prevention_costs": {
            "shared": [
                {
                    "id":         "sti_screening_all_prep",
                    "applies_to": ["oral_prep", "lenacapavir"],
                    "category":   "monitoring",
                    "frequency":  "annual",   # required by validator
                    "value":      300,
                }
            ],
            "arm_specific": {
                "oral_prep": {
                    "drug_and_monitoring": {
                        "value":     5400,
                        "cohort":    "all",
                        "category":  "drug",
                        "frequency": "annual",  # required by validator
                    }
                },
                "lenacapavir": {
                    "injection_year1": {
                        "value":     21395.20,
                        "cohort":    "new_initiators",
                        "category":  "drug",
                        "frequency": "annual",  # required by validator
                    },
                    "injection_subsequent": {
                        "value":     20555.22,
                        "cohort":    "continuing",
                        "category":  "drug",
                        "frequency": "annual",  # required by validator
                    },
                },
            },
        },
        "disease_costs": {
            "art_first_line": {
                "value":                10000,
                "billing_moment":       "ongoing",
                "frequency":            "annual",  # required by validator
                "applies_to_stages":    ["Acute", "Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":             "drug",
            },
            "hiv_monitoring_on_art": {
                "value":                2500,
                "billing_moment":       "ongoing",
                "frequency":            "annual",  # required by validator
                "applies_to_stages":    ["Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":             "monitoring",
            },
            "aids_hospitalization": {
                "value":                40000,
                "billing_moment":       "ongoing",
                "frequency":            "annual",  # required by validator
                "applies_to_stages":    ["AIDS"],
                "applies_to_art_status": ["on_art", "off_art"],
                "category":             "hiv_care",
            },
            "hiv_diagnosis_linkage": {
                "value":                500,
                "billing_moment":       "diagnosis",
                "frequency":            "once",
                "applies_to_stages":    ["Acute", "Chronic", "AIDS"],
                "applies_to_art_status": ["on_art", "off_art"],
                "category":             "hiv_care",
            },
        },
        "analysis_scenarios": [],
    }


def _make_old_flat_config() -> dict:
    """Return a minimal old flat-format hta_config (pre-v2)."""
    return {
        "utility_weights": {
            "susceptible":         {"value": 1.00},
            "hiv_chronic_on_art":  {"value": 0.85},
        },
        "unit_costs_annual_eur": {
            "oral_prep_drug_and_monitoring": {"value": 5400},
            "art_first_line":                {"value": 10000},
            "hiv_monitoring_on_art":         {"value": 2500},
            "aids_hospitalization":          {"value": 40000},
        },
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate":           {"value": 0.04},
        },
    }


def _empty_pop() -> np.ndarray:
    """Return zero population tensor: (age=1, orient=1, act=1, hiv=4, diag=2, art=2, prep=3)."""
    return np.zeros((1, 1, 1, 4, 2, 2, 4))


def _make_accumulator(arm_name="no_prep", freeze_year=2025.0, hta_config=None) -> EconomicsAccumulator:
    return EconomicsAccumulator(
        hta_config=hta_config if hta_config is not None else _make_v2_config(),
        groups=_MockGroups(),
        freeze_year=freeze_year,
        step_fraction=STEP_FRACTION,  # V11.7.2: required arg
        arm_name=arm_name,
    )


def _current_prep_users(pop: np.ndarray, prep_idx: int) -> float:
    return float(np.sum(pop[:, :, :, 0, :, :, prep_idx]))


def _accumulate(acc: EconomicsAccumulator, *args, **kwargs) -> None:
    """
    Test convenience wrapper for direct accumulator unit tests.

    Production HTA calls pass gross PrEP initiation counters from run_v3.py.
    Older direct unit tests predate that API and are mostly asserting unrelated
    cost categories, so this wrapper supplies the historical stock-delta value
    explicitly. Tests for the raw missing-counter guard call acc.accumulate()
    directly.
    """
    if "prep_initiations_by_arm" not in kwargs:
        population = kwargs.get("population")
        if population is None and len(args) >= 3:
            population = args[2]
        gross = {"oral_prep": 0, "lenacapavir": 0}
        if population is not None:
            if acc.arm_name == "oral_prep":
                current = _current_prep_users(population, 1)
                prev = float(acc._prev_prep_users.get("oral_prep", 0))
                gross["oral_prep"] = max(0.0, current - prev)
            elif acc.arm_name == "lenacapavir":
                current = _current_prep_users(population, 2)
                prev = float(acc._prev_prep_users.get("lenacapavir", 0))
                gross["lenacapavir"] = max(0.0, current - prev)
        kwargs["prep_initiations_by_arm"] = gross
    acc.accumulate(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 1. QALY: Susceptible states
# ─────────────────────────────────────────────────────────────────────────────

class TestQALYSusceptible:

    def test_susceptible_no_prep_qaly(self):
        """100 susceptible (no PrEP) at freeze_year → 100 × 1.00 × 0.25 = 25.0 undiscounted QALYs."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 0] = 100  # Susceptible, undiag, off_art, no_prep
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 1.00 * STEP_FRACTION)

    def test_susceptible_oral_prep_qaly(self):
        """100 susceptible on oral PrEP → 100 × 0.99 × 0.25 = 24.75 undiscounted QALYs."""
        acc = _make_accumulator(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # Susceptible, Oral_PrEP index=1
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.99 * STEP_FRACTION)

    def test_susceptible_lenacapavir_qaly(self):
        """100 susceptible on Lenacapavir → 100 × 0.995 × 0.25 = 24.875 undiscounted QALYs."""
        acc = _make_accumulator(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100  # Susceptible, Lenacapavir index=2
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.995 * STEP_FRACTION)


# ─────────────────────────────────────────────────────────────────────────────
# 2. QALY: HIV+ states
# ─────────────────────────────────────────────────────────────────────────────

class TestQALYHIV:

    def test_chronic_on_art_qaly(self):
        """100 Chronic on ART → 100 × 0.85 × 0.25 = 21.25 undiscounted QALYs."""
        acc = _make_accumulator()
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 100  # Chronic, diagnosed, on_art=1, no_prep
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.85 * STEP_FRACTION)

    def test_chronic_off_art_qaly(self):
        """100 Chronic off ART → 100 × 0.78 × 0.25 = 19.5 undiscounted QALYs."""
        acc = _make_accumulator()
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 0, 0] = 100  # Chronic, diagnosed, off_art=0, no_prep
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.78 * STEP_FRACTION)

    def test_aids_on_art_qaly(self):
        """100 AIDS on ART → 100 × 0.60 × 0.25 = 15.0 undiscounted QALYs."""
        acc = _make_accumulator()
        pop = _empty_pop()
        pop[0, 0, 0, 3, 1, 1, 0] = 100  # AIDS, diagnosed, on_art=1, no_prep
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.60 * STEP_FRACTION)

    def test_aids_off_art_qaly(self):
        """100 AIDS off ART → 100 × 0.40 × 0.25 = 10.0 undiscounted QALYs."""
        acc = _make_accumulator()
        pop = _empty_pop()
        pop[0, 0, 0, 3, 1, 0, 0] = 100  # AIDS, diagnosed, off_art=0, no_prep
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_undisc"] == pytest.approx(100 * 0.40 * STEP_FRACTION)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Prevention costs: no_prep arm → zero prevention costs
# ─────────────────────────────────────────────────────────────────────────────

class TestPreventionCostsNoPrEP:

    def test_no_prep_arm_zero_prevention_costs(self):
        """arm_name='no_prep' yields zero prevention costs regardless of PrEP pop."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # some people on Oral_PrEP in population
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        # All costs should be zero (no HIV+, arm_name excludes prevention costs)
        assert rec["step_costs_undisc"] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Prevention costs: oral_prep arm
# ─────────────────────────────────────────────────────────────────────────────

class TestPreventionCostsOralPrEP:

    def test_oral_prep_drug_and_monitoring_cost(self):
        """
        100 users on oral PrEP:
          drug_and_monitoring (cohort=all): 100 × 5400 × 0.25 = 135,000
          STI screening (shared):          100 × 300  × 0.25 =   7,500
          Total prevention                              =  142,500
        """
        acc = _make_accumulator(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100  # Susceptible, Oral_PrEP
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        expected_drug     = 100 * 5400 * STEP_FRACTION      # 135,000
        expected_sti      = 100 * 300  * STEP_FRACTION      # 7,500
        expected_total    = expected_drug + expected_sti     # 142,500
        assert rec["step_costs_undisc"] == pytest.approx(expected_total)

    def test_oral_prep_cost_breakdown_categories(self):
        """Drug cost in 'drug' category; STI screening in 'monitoring' category."""
        acc = _make_accumulator(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]
        # At freeze_year, discount_cost=1.0, so discounted == undiscounted
        assert bd["drug"]       == pytest.approx(100 * 5400 * STEP_FRACTION)
        assert bd["prep_drug"]  == pytest.approx(100 * 5400 * STEP_FRACTION)
        assert bd["prep_drug_oral"] == pytest.approx(100 * 5400 * STEP_FRACTION)
        assert "prep_drug_lenacapavir" not in bd
        assert bd["monitoring"] == pytest.approx(100 * 300  * STEP_FRACTION)


# ─────────────────────────────────────────────────────────────────────────────
# 5–6. Prevention costs: Lenacapavir cohort logic
# ─────────────────────────────────────────────────────────────────────────────

class TestPreventionCostsLenacapavir:

    def test_lena_first_step_all_new_initiators(self):
        """
        Step 1: 100 users, all new (prev=0):
          injection_year1 (new_initiators): 100 × 21395.20 × 0.25 = 534,880.00
          injection_subsequent (continuing): 0  × 20555.22 × 0.25 = 0.00
          STI screening (shared):           100 × 300      × 0.25 = 7,500.00
          Total                                                    = 542,380.00
        """
        acc = _make_accumulator(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100  # Susceptible, Lenacapavir
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        expected = (
            100 * 21395.20 * STEP_FRACTION +   # year1 for all 100 new initiators
            0   * 20555.22 * STEP_FRACTION +   # zero continuing
            100 * 300      * STEP_FRACTION     # STI screening
        )
        assert rec["step_costs_undisc"] == pytest.approx(expected)
        bd = acc.get_summary()["cost_breakdown_disc"]
        expected_lena_drug = 100 * 21395.20 * STEP_FRACTION
        assert bd["prep_drug"] == pytest.approx(expected_lena_drug)
        assert bd["prep_drug_lenacapavir"] == pytest.approx(expected_lena_drug)
        assert "prep_drug_oral" not in bd

    def test_lena_second_step_all_continuing(self):
        """
        Step 1: 100 new users → prev_prep_users["lenacapavir"] = 100
        Step 2: same 100 users (no new entrants):
          injection_year1 (new_initiators): 0   × 21395.20 × 0.25 = 0.00
          injection_subsequent (continuing): 100 × 20555.22 × 0.25 = 513,880.50
          STI screening:                     100 × 300      × 0.25 = 7,500.00
          Total                                                    = 521,380.50
        """
        acc = _make_accumulator(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)  # Step 1
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)  # Step 2

        rec2 = acc.get_timestep_records()[1]
        expected = (
            0   * 21395.20 * STEP_FRACTION +   # zero new initiators
            100 * 20555.22 * STEP_FRACTION +   # 100 continuing from step 1
            100 * 300      * STEP_FRACTION     # STI screening
        )
        assert rec2["step_costs_undisc"] == pytest.approx(expected)

    def test_lena_partial_new_initiators(self):
        """
        Step 1: 80 users → prev = 80
        Step 2: 100 users → new_initiators=20, continuing=80
          injection_year1:        20  × 21395.20 × 0.25 = 106,976.00
          injection_subsequent:   80  × 20555.22 × 0.25 = 411,104.40
          STI screening:          100 × 300      × 0.25 =   7,500.00
          Total step 2                                   = 525,580.40
        """
        acc = _make_accumulator(arm_name="lenacapavir")
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 2] = 80
        pop2 = _empty_pop(); pop2[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop1, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop2, new_infections=0)

        rec2 = acc.get_timestep_records()[1]
        expected = (
            20  * 21395.20 * STEP_FRACTION +
            80  * 20555.22 * STEP_FRACTION +
            100 * 300      * STEP_FRACTION
        )
        assert rec2["step_costs_undisc"] == pytest.approx(expected)

    # ── Dropout tests (V11.9.10 — bug fix: continuing = min(current, prev)) ──

    def test_lena_full_dropout_zero_cost(self):
        """
        V11.9.10 regression: all users drop off → zero prevention cost that step.

        Step 0: 100 new users → prev updated to 100
        Step 1: 0 users (all dropped off)
          new_initiators = max(0, 0 − 100) = 0
          continuing     = min(0, 100)      = 0   ← fix; old code gave prev_users=100
          STI screening applies_to active users only (current_users=0, loop skipped)
          Total step 1   = 0

        With the pre-fix code: continuing=100 → charged 100 × 20555.22 × 0.25 = €513,880.50
        """
        acc  = _make_accumulator(arm_name="lenacapavir")
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 2] = 100
        pop0 = _empty_pop()  # zero users

        _accumulate(acc, step=0, year=2025.0,  population=pop1, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop0, new_infections=0)

        rec1 = acc.get_timestep_records()[1]
        assert rec1["step_costs_undisc"] == pytest.approx(0.0), (
            f"Full dropout: expected zero prevention cost, got {rec1['step_costs_undisc']:.2f}. "
            f"Pre-fix code would charge {100 * 20555.22 * STEP_FRACTION:.2f} for ghost users."
        )

    def test_lena_partial_dropout_charges_only_active_users(self):
        """
        V11.9.10 regression: partial dropout charges only the users still active.

        Step 0: 100 users → prev = 100
        Step 1: 60 users (40 dropped off)
          new_initiators = max(0, 60 − 100) = 0
          continuing     = min(60, 100)      = 60   ← fix; old code gave 100
          injection_subsequent: 60  × 20555.22 × 0.25 = 308,328.30
          STI screening:        60  × 300      × 0.25 =   4,500.00
          Total step 1                                = 312,828.30

        With the pre-fix code: continuing=100 → overcharge of 40 × 20555.22 × 0.25 = €205,552.20
        """
        acc  = _make_accumulator(arm_name="lenacapavir")
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 2] = 100
        pop2 = _empty_pop(); pop2[0, 0, 0, 0, 0, 0, 2] = 60

        _accumulate(acc, step=0, year=2025.0,  population=pop1, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop2, new_infections=0)

        rec1 = acc.get_timestep_records()[1]
        expected = (
            60 * 20555.22 * STEP_FRACTION +   # continuing=60 (not 100)
            60 * 300      * STEP_FRACTION      # STI screening for 60 active users
        )
        assert rec1["step_costs_undisc"] == pytest.approx(expected), (
            f"Partial dropout: expected {expected:.2f}, got {rec1['step_costs_undisc']:.2f}. "
            f"Pre-fix code would charge {100 * 20555.22 * STEP_FRACTION + 60 * 300 * STEP_FRACTION:.2f}."
        )

    def test_lena_dropout_then_rejoin_charges_year1_on_return(self):
        """
        V11.9.10: three-step dropout-then-rejoin sequence validates both the fix
        and the documented re-initiation loading dose behaviour.

        Step 0: 100 join   → new_initiators=100, continuing=0  → year1 rate
        Step 1: 50 drop off (50 remain) → new_initiators=0, continuing=50 → subsequent rate
        Step 2: 30 rejoin  (80 total)   → new_initiators=30, continuing=50

        Step 2 expected (re-initiators get year1 rate — documented limitation):
          injection_year1:       30 × 21395.20 × 0.25 = 160,464.00
          injection_subsequent:  50 × 20555.22 × 0.25 = 256,940.25
          STI screening:         80 × 300      × 0.25 =   6,000.00
          Total step 2                                = 423,404.25

        Note: rejoining users are treated as new initiators (year1 rate) regardless
        of gap duration. This is a documented model limitation — the accumulator
        holds no memory of prior exposure beyond the previous step's user count.
        """
        acc  = _make_accumulator(arm_name="lenacapavir")
        pop0 = _empty_pop(); pop0[0, 0, 0, 0, 0, 0, 2] = 100
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 2] = 50
        pop2 = _empty_pop(); pop2[0, 0, 0, 0, 0, 0, 2] = 80

        _accumulate(acc, step=0, year=2025.0,  population=pop0, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop1, new_infections=0)
        _accumulate(acc, step=2, year=2025.5,  population=pop2, new_infections=0)

        rec1 = acc.get_timestep_records()[1]
        rec2 = acc.get_timestep_records()[2]

        # Step 1 sanity: 50 continuing, 0 new initiators, no dropout overcharge
        expected_step1 = (
            50 * 20555.22 * STEP_FRACTION +
            50 * 300      * STEP_FRACTION
        )
        assert rec1["step_costs_undisc"] == pytest.approx(expected_step1), (
            f"Step 1 (dropout): expected {expected_step1:.2f}, got {rec1['step_costs_undisc']:.2f}"
        )

        # Step 2: 30 re-initiators charged year1, 50 continuing charged subsequent
        expected_step2 = (
            30 * 21395.20 * STEP_FRACTION +
            50 * 20555.22 * STEP_FRACTION +
            80 * 300      * STEP_FRACTION
        )
        assert rec2["step_costs_undisc"] == pytest.approx(expected_step2), (
            f"Step 2 (rejoin): expected {expected_step2:.2f}, got {rec2['step_costs_undisc']:.2f}"
        )

    def test_lena_growth_regression_after_fix(self):
        """
        V11.9.10 regression guard: confirm fix does not alter growth-case behaviour.

        Identical to test_lena_partial_new_initiators — prev=80, current=100.
        continuing = min(100, 80) = 80, same as pre-fix for this direction.
        """
        acc  = _make_accumulator(arm_name="lenacapavir")
        pop1 = _empty_pop(); pop1[0, 0, 0, 0, 0, 0, 2] = 80
        pop2 = _empty_pop(); pop2[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop1, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop2, new_infections=0)

        rec2 = acc.get_timestep_records()[1]
        expected = (
            20  * 21395.20 * STEP_FRACTION +
            80  * 20555.22 * STEP_FRACTION +
            100 * 300      * STEP_FRACTION
        )
        assert rec2["step_costs_undisc"] == pytest.approx(expected)


class TestGrossInitiationCounters:

    def test_initiation_cost_uses_gross_initiations_not_net_stock_delta(self):
        acc = _make_accumulator(arm_name="lenacapavir")
        acc._prev_prep_users["lenacapavir"] = 100
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 105

        acc.accumulate(
            step=0,
            year=2025.0,
            population=pop,
            new_infections=0,
            prep_initiations_by_arm={"lenacapavir": 10},
        )

        rec = acc.get_timestep_records()[0]
        expected = (
            10 * 21395.20 * STEP_FRACTION +
            100 * 20555.22 * STEP_FRACTION +
            105 * 300 * STEP_FRACTION
        )
        assert rec["step_costs_undisc"] == pytest.approx(expected)

    def test_initiation_cost_requires_gross_counter_when_configured(self):
        acc = _make_accumulator(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        with pytest.raises(ValueError, match="prep_initiations_by_arm"):
            acc.accumulate(step=0, year=2025.0, population=pop, new_infections=0)

    def test_stock_delta_fallback_allowed_when_no_initiation_billed_costs(self):
        cfg = _make_v2_config()
        cfg["prevention_costs"]["arm_specific"]["lenacapavir"].pop("injection_year1")
        acc = _make_accumulator(arm_name="lenacapavir", hta_config=cfg)
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        acc.accumulate(step=0, year=2025.0, population=pop, new_infections=0)

        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(100 * 300 * STEP_FRACTION)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Disease costs: diagnosis billing_moment
# ─────────────────────────────────────────────────────────────────────────────

class TestDiseaseCostsDiagnosis:

    def test_diagnosis_linkage_cost_fires_on_new_diagnoses(self):
        """
        5 new diagnoses, linkage cost = €500 each.
        Total cost = 5 × 500 = 2,500 (no step_fraction: one-time event).
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop,
                       new_infections=0, new_diagnoses=5)
        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(5 * 500)

    def test_zero_diagnoses_zero_linkage_cost(self):
        """new_diagnoses=0 → no linkage cost fired."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop,
                       new_infections=0, new_diagnoses=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(0.0)

    def test_diagnosis_linkage_in_hiv_care_category(self):
        """Linkage cost (category='hiv_care') goes to hiv_care breakdown."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop,
                       new_infections=0, new_diagnoses=10)
        summary = acc.get_summary()
        # 10 × 500 = 5,000 → should be in hiv_care (at freeze_year discount=1.0)
        assert summary["cost_breakdown_disc"]["hiv_care"] == pytest.approx(10 * 500)


# ─────────────────────────────────────────────────────────────────────────────
# 8–10. Disease costs: ongoing billing_moment, ART status filter
# ─────────────────────────────────────────────────────────────────────────────

class TestDiseaseCostsOngoing:

    def test_chronic_on_art_disease_costs(self):
        """
        100 Chronic on ART:
          art_first_line (Chronic, on_art):       100 × 10000 × 0.25 = 250,000
          hiv_monitoring_on_art (Chronic, on_art): 100 × 2500  × 0.25 =  62,500
          Total disease cost                                           = 312,500
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 100  # Chronic, diagnosed, on_art=1
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        expected = 100 * (10000 + 2500) * STEP_FRACTION
        assert rec["step_costs_undisc"] == pytest.approx(expected)

    def test_aids_off_art_hospitalization_only(self):
        """
        100 AIDS off ART:
          art_first_line → not applies_to_art_status off_art → 0
          hiv_monitoring_on_art → not off_art → 0
          aids_hospitalization (AIDS, off_art): 100 × 40000 × 0.25 = 1,000,000
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 3, 1, 0, 0] = 100  # AIDS, diagnosed, off_art=0
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        expected = 100 * 40000 * STEP_FRACTION   # hospitalization only
        assert rec["step_costs_undisc"] == pytest.approx(expected)

    def test_art_status_filter_off_art_gets_zero_drug_cost(self):
        """
        Chronic off ART: art_first_line has applies_to_art_status=['on_art']
        → off_art Chronic individuals pay no drug cost.
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 0, 0] = 100  # Chronic, off_art=0
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        # No cost entries apply: art_first_line=on_art only, monitoring=on_art only,
        # hospitalization=AIDS only
        assert rec["step_costs_undisc"] == pytest.approx(0.0)


def _make_v2_config_with_art_cohort_cost(cost_entry: dict) -> dict:
    cfg = copy.deepcopy(_make_v2_config())
    cfg["disease_costs"] = {
        "popping_test_cost": cost_entry,
    }
    return cfg


class TestDiseaseCostsArtCohorts:

    def test_new_art_initiator_once_cost_by_stage(self):
        """
        ART-initiation disease cost with frequency='once' charges the full year-1
        bundle to gross ART initiators in the matching HIV stage.
        """
        cfg = _make_v2_config_with_art_cohort_cost({
            "value": 6176,
            "billing_moment": "ongoing",
            "frequency": "once",
            "cohort": "new_art_initiators",
            "applies_to_stages": ["Chronic"],
            "applies_to_art_status": ["on_art"],
            "category": "monitoring",
        })
        acc = _make_accumulator(arm_name="no_prep", hta_config=cfg)
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 10

        _accumulate(
            acc, step=0, year=2025.0, population=pop, new_infections=0,
            art_initiations_by_stage={"Chronic": 10}
        )

        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(10 * 6176)
        assert rec["step_art_initiations_chronic"] == 10

    def test_new_art_initiator_annual_cost_uses_step_fraction(self):
        """ART-initiation disease costs with frequency='annual' remain prorated."""
        cfg = _make_v2_config_with_art_cohort_cost({
            "value": 6176,
            "billing_moment": "ongoing",
            "frequency": "annual",
            "cohort": "new_art_initiators",
            "applies_to_stages": ["Chronic"],
            "applies_to_art_status": ["on_art"],
            "category": "monitoring",
        })
        acc = _make_accumulator(arm_name="no_prep", hta_config=cfg)
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 10

        _accumulate(
            acc, step=0, year=2025.0, population=pop, new_infections=0,
            art_initiations_by_stage={"Chronic": 10}
        )

        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(10 * 6176 * STEP_FRACTION)

    def test_on_art_continuing_excludes_same_step_initiators(self):
        """Continuing on-ART stock is current on-ART stock minus same-stage initiators."""
        cfg = _make_v2_config_with_art_cohort_cost({
            "value": 4165,
            "billing_moment": "ongoing",
            "frequency": "annual",
            "cohort": "on_art_continuing",
            "applies_to_stages": ["Chronic"],
            "applies_to_art_status": ["on_art"],
            "category": "monitoring",
        })
        acc = _make_accumulator(arm_name="no_prep", hta_config=cfg)
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 100

        _accumulate(
            acc, step=0, year=2025.0, population=pop, new_infections=0,
            art_initiations_by_stage={"Chronic": 10}
        )

        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(90 * 4165 * STEP_FRACTION)

    def test_missing_art_initiation_counters_raise_for_art_cohort_costs(self):
        cfg = _make_v2_config_with_art_cohort_cost({
            "value": 6176,
            "billing_moment": "ongoing",
            "frequency": "once",
            "cohort": "new_art_initiators",
            "applies_to_stages": ["Chronic"],
            "applies_to_art_status": ["on_art"],
            "category": "monitoring",
        })
        acc = _make_accumulator(arm_name="no_prep", hta_config=cfg)
        pop = _empty_pop()

        with pytest.raises(ValueError, match="art_initiations_by_stage"):
            acc.accumulate(step=0, year=2025.0, population=pop, new_infections=0)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Discounting
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscounting:

    def test_discount_health_applied_one_year_later(self):
        """
        year = freeze_year + 1.0  (one full year):
        discount_health = 1 / (1.015)^1 ≈ 0.98522
        100 susceptible → undisc = 100 × 1.0 × 0.25 = 25.0
        disc = 25.0 × (1/1.015)
        """
        acc = _make_accumulator(arm_name="no_prep", freeze_year=2025.0)
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 0] = 100
        _accumulate(acc, step=0, year=2026.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        undisc   = 100 * 1.0 * STEP_FRACTION
        expected_disc = undisc / (1.015 ** 1)
        assert rec["step_qalys_disc"]  == pytest.approx(expected_disc, rel=1e-5)
        assert rec["step_qalys_undisc"] == pytest.approx(undisc)

    def test_discount_cost_applied_one_year_later(self):
        """
        year = freeze_year + 1.0:
        discount_cost = 1 / (1.04)^1 ≈ 0.96154
        5 new diagnoses × €500 = €2,500 undiscounted
        disc = 2,500 / 1.04
        """
        acc = _make_accumulator(arm_name="no_prep", freeze_year=2025.0)
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2026.0, population=pop,
                       new_infections=0, new_diagnoses=5)
        rec = acc.get_timestep_records()[0]
        undisc = 5 * 500
        expected_disc = undisc / (1.04 ** 1)
        assert rec["step_costs_disc"]   == pytest.approx(expected_disc, rel=1e-5)
        assert rec["step_costs_undisc"] == pytest.approx(undisc)

    def test_no_discount_at_freeze_year(self):
        """At year=freeze_year, discount factors = 1.0 → disc == undisc."""
        acc = _make_accumulator(arm_name="no_prep", freeze_year=2025.0)
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 0] = 100
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0, new_diagnoses=3)
        rec = acc.get_timestep_records()[0]
        assert rec["step_qalys_disc"]  == pytest.approx(rec["step_qalys_undisc"])
        assert rec["step_costs_disc"]  == pytest.approx(rec["step_costs_undisc"])


# ─────────────────────────────────────────────────────────────────────────────
# 12. Cost breakdown categories
# ─────────────────────────────────────────────────────────────────────────────

class TestCostBreakdown:

    def test_breakdown_no_phantom_zero_keys(self):
        """
        cost_breakdown_disc must not contain zero-value phantom keys for categories
        with no costs in this run. Previously a setdefault() block force-inserted
        drug/monitoring/hiv_care = 0.0 unconditionally; that block was removed in
        V11.7.3. Callers use .get(key, 0) and are unaffected.
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        # Zero population — no costs accrue for any category
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]
        # With no population, no cost items should fire → breakdown should be empty
        # (or contain only keys that actually had non-zero values)
        for key in ("drug", "monitoring", "hiv_care"):
            assert key not in bd or bd[key] == 0.0, \
                f"Key '{key}' should either be absent or zero with empty population, got {bd.get(key)}"
        # The breakdown should be empty (no phantom keys injected)
        assert bd == {}, f"Expected empty breakdown with zero population, got: {bd}"

    def test_disease_cost_breakdown_by_category(self):
        """
        Chronic on ART (100 people):
          drug (art_first_line):       100 × 10000 × 0.25 = 250,000
          monitoring (monitoring_art): 100 × 2500  × 0.25 =  62,500
        At freeze_year discount=1.0, discounted equals undiscounted.
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 2, 1, 1, 0] = 100  # Chronic on ART
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]
        assert bd["drug"]       == pytest.approx(100 * 10000 * STEP_FRACTION)
        assert bd["hiv_art_drug"] == pytest.approx(100 * 10000 * STEP_FRACTION)
        assert bd["monitoring"] == pytest.approx(100 * 2500  * STEP_FRACTION)
        # hiv_care had zero costs — key must be absent (sparse breakdown, V11.7.3)
        assert "hiv_care" not in bd, \
            "Zero-cost categories must not appear in cost_breakdown_disc"


# ─────────────────────────────────────────────────────────────────────────────
# 13. Timestep records
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestepRecords:

    def test_step_new_diagnoses_stored_per_timestep(self):
        """step_new_diagnoses must be present in every timestep record."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0, new_diagnoses=3)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=1, new_diagnoses=7)
        records = acc.get_timestep_records()
        assert records[0]["step_new_diagnoses"] == 3
        assert records[1]["step_new_diagnoses"] == 7

    def test_timestep_records_copy_not_reference(self):
        """get_timestep_records() returns a copy — mutations don't affect internal state."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        records = acc.get_timestep_records()
        records.clear()
        assert len(acc.get_timestep_records()) == 1  # internal state unchanged

    def test_total_population_stored_in_timestep_record(self):
        """
        V11.7.1: total_population key must be present in every timestep record.
        Value must equal np.sum(population) at that step.
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 0] = 150   # 150 susceptible individuals
        pop[0, 0, 0, 2, 1, 1, 0] = 25    # 25 Chronic on ART
        expected_total = 175.0
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert "total_population" in rec, "total_population key missing from timestep record"
        assert rec["total_population"] == pytest.approx(expected_total)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Old flat format rejected (BACKLOG Finding 3 — V11.8)
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyFlatFormat:
    """
    V11.8 / BACKLOG Finding 3: old flat-format hta_config (pre-v2, no _format_version key)
    must be rejected at EconomicsAccumulator construction time.  The legacy normalization
    shim (hta_config_compat.py) and _compute_step_legacy() have been removed.
    """

    def test_old_flat_format_raises_value_error(self):
        """
        Passing a V11.5.x flat-format config (no _format_version: v2) to
        EconomicsAccumulator must raise ValueError at construction — not silently
        use a legacy code path.
        """
        with pytest.raises(ValueError, match="v2"):
            EconomicsAccumulator(
                hta_config=_make_old_flat_config(),
                groups=_MockGroups(),
                freeze_year=2025.0,
                step_fraction=STEP_FRACTION,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 15. get_summary: cumulative totals and diagnoses count
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSummary:

    def test_cumul_new_diagnoses_accumulated(self):
        """cumul_new_diagnoses sums new_diagnoses across all steps."""
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0, new_diagnoses=5)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0, new_diagnoses=3)
        _accumulate(acc, step=2, year=2025.5,  population=pop, new_infections=0, new_diagnoses=0)
        summary = acc.get_summary()
        assert summary["cumul_new_diagnoses"] == 8

    def test_cumul_costs_disc_accumulates_across_steps(self):
        """Total discounted costs add up correctly across two steps with new_diagnoses."""
        acc = _make_accumulator(arm_name="no_prep", freeze_year=2025.0)
        pop = _empty_pop()
        # Step 0 at freeze_year: discount=1.0
        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0, new_diagnoses=4)
        # Step 1 one year later: discount = 1/1.04
        _accumulate(acc, step=1, year=2026.0, population=pop, new_infections=0, new_diagnoses=2)
        summary = acc.get_summary()
        expected_disc = (4 * 500) * 1.0 + (2 * 500) / (1.04 ** 1)
        assert summary["total_costs_disc"] == pytest.approx(expected_disc, rel=1e-5)

    def test_v2_config_validated_on_init(self):
        """EconomicsAccumulator rejects a broken v2 config at construction time."""
        bad_config = _make_v2_config()
        del bad_config["utilities"]  # Remove required section
        with pytest.raises((ValueError, KeyError)):
            EconomicsAccumulator(
                hta_config=bad_config,
                groups=_MockGroups(),
                freeze_year=2025.0,
                step_fraction=STEP_FRACTION,
            )

    def test_step_fraction_required(self):
        """EconomicsAccumulator requires step_fraction — raises TypeError if omitted."""
        with pytest.raises(TypeError):
            EconomicsAccumulator(
                hta_config=_make_v2_config(),
                groups=_MockGroups(),
                freeze_year=2025.0,
                # step_fraction omitted
            )

    def test_step_fraction_invalid_raises_value_error(self):
        """step_fraction=0.0 raises ValueError with descriptive message."""
        with pytest.raises(ValueError, match="step_fraction must be in"):
            EconomicsAccumulator(
                hta_config=_make_v2_config(),
                groups=_MockGroups(),
                freeze_year=2025.0,
                step_fraction=0.0,
            )

    def test_step_fraction_stored_in_timestep_record(self):
        """
        V11.7.2: accumulate() stores step_fraction in each timestep record
        so ScenarioComparator can cross-check consistency.
        """
        acc = _make_accumulator(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        assert "step_fraction" in rec
        assert rec["step_fraction"] == pytest.approx(STEP_FRACTION)

    def test_discount_rates_required_fail_loudly(self):
        """
        V11.7.2: accumulate() raises KeyError if discounting.health_effects_rate
        or discounting.cost_rate is missing — no silent fallback.
        """
        bad_config = _make_v2_config()
        del bad_config["discounting"]["health_effects_rate"]
        acc = EconomicsAccumulator(
            hta_config=bad_config,
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        with pytest.raises(KeyError, match="health_effects_rate"):
            _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Shared costs: verification that STI screening applies to correct arms
# V11.7.2 Item 5 — confirms existing v2 code path handles shared costs correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestSharedCostRouting:
    """
    Verify that prevention_costs.shared items are included for the correct arms
    and excluded for no_prep. STI screening (€300/year) is the test case.

    Config: shared STI screening applies_to=['oral_prep', 'lenacapavir'], €300/yr.
    Expected per step (quarterly, pop=1000 susceptible on PrEP):
      shared_cost = 1000 × 300 × 0.25 = 75,000
    no_prep arm: shared_cost = 0 (excluded by applies_to filter).
    """

    def _make_acc_with_pop(self, arm_name: str, prep_idx: int) -> tuple:
        """Build accumulator and population with 1000 susceptible PrEP users."""
        pop = _empty_pop()
        # Shape: (age=1, orient=1, act=1, hiv=4, diag=2, art=2, prep=3)
        pop[0, 0, 0, 0, 0, 0, prep_idx] = 1000
        acc = _make_accumulator(arm_name=arm_name)
        return acc, pop

    def test_sti_screening_included_for_oral_prep(self):
        """
        oral_prep arm: shared STI screening (€300/yr) is included.
        cost = 1000 users × €300 × 0.25 step_fraction = €75,000.
        """
        acc, pop = self._make_acc_with_pop("oral_prep", prep_idx=1)
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        # Drug cost = 1000 × 5400 × 0.25 = 1_350_000 (arm_specific oral_prep drug_and_monitoring)
        # Monitoring cost = 1000 × 300 × 0.25 = 75_000 (shared STI screening)
        assert summary["total_costs_undisc"] == pytest.approx(
            1000 * 5400 * STEP_FRACTION + 1000 * 300 * STEP_FRACTION, rel=1e-5
        )
        # STI screening appears in "monitoring" breakdown category
        breakdown = summary["cost_breakdown_disc"]
        assert breakdown.get("monitoring", 0) == pytest.approx(
            1000 * 300 * STEP_FRACTION, rel=1e-5
        )

    def test_sti_screening_included_for_lenacapavir(self):
        """
        lenacapavir arm: shared STI screening (€300/yr) is included.
        First step: all 1000 users are new_initiators → year1 cost applies.
        Monitoring (STI) = 1000 × 300 × 0.25 = 75,000.
        """
        acc, pop = self._make_acc_with_pop("lenacapavir", prep_idx=2)
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        breakdown = acc.get_summary()["cost_breakdown_disc"]
        assert breakdown.get("monitoring", 0) == pytest.approx(
            1000 * 300 * STEP_FRACTION, rel=1e-5
        )

    def test_sti_screening_excluded_for_no_prep(self):
        """
        no_prep arm: shared STI screening does NOT apply (applies_to filter).
        Total cost should be zero (no prevention costs for no_prep).
        """
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 0] = 1000  # susceptible, no PrEP
        acc = _make_accumulator(arm_name="no_prep")
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        breakdown = acc.get_summary()["cost_breakdown_disc"]
        assert breakdown.get("monitoring", 0) == pytest.approx(0.0, abs=1e-6)

    def test_no_double_counting_shared_plus_arm_specific(self):
        """
        Shared STI screening and arm_specific drug_and_monitoring are separate categories.
        Drug category = arm_specific cost only. Monitoring category = shared STI only.
        No overlap between categories.
        """
        acc, pop = self._make_acc_with_pop("oral_prep", prep_idx=1)
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        breakdown = acc.get_summary()["cost_breakdown_disc"]
        drug_expected     = 1000 * 5400 * STEP_FRACTION
        monitor_expected  = 1000 * 300  * STEP_FRACTION
        assert breakdown.get("drug",       0) == pytest.approx(drug_expected,    rel=1e-5)
        assert breakdown.get("monitoring", 0) == pytest.approx(monitor_expected, rel=1e-5)
        # Together they equal total (no disease costs in susceptible-only pop)
        total = acc.get_summary()["total_costs_undisc"]
        assert total == pytest.approx(drug_expected + monitor_expected, rel=1e-5)


# ─────────────────────────────────────────────────────────────────────────────
# 17. Initiation AE verification (economics side)
# V11.7.2 Item 6 — renal screen fires correctly via EconomicsAccumulator + AE module
# The initiation AE tests for the AE module itself live in test_adverse_events_v2.py
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscountRateRequired:
    """
    V11.7.2 Item 2: missing discount rate keys raise KeyError loudly.
    No silent fallback to ZIN defaults.
    """

    def test_missing_cost_rate_raises(self):
        """discounting.cost_rate missing → KeyError on first accumulate() call."""
        bad_config = _make_v2_config()
        del bad_config["discounting"]["cost_rate"]
        acc = EconomicsAccumulator(
            hta_config=bad_config,
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
        )
        pop = _empty_pop()
        with pytest.raises(KeyError, match="cost_rate"):
            _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)


# ─────────────────────────────────────────────────────────────────────────────
# V11.7.3: Death billing moment — AIDS terminal care costs
# ─────────────────────────────────────────────────────────────────────────────

def _make_v2_config_with_terminal_care(cost_per_death: float = 1000.0) -> dict:
    """
    Return a v2 config that adds a billing_moment='death' item to disease_costs.
    Used by TestDeathBillingMoment only.

    cost_per_death: the per-AIDS-death terminal care cost; default 1000 for round maths.
    """
    cfg = _make_v2_config()
    cfg["disease_costs"]["hiv_terminal_care"] = {
        "value":           cost_per_death,
        "frequency":       "once",
        "billing_moment":  "death",
        "applies_to_stages": ["AIDS"],
        "category":        "terminal_care",
    }
    return cfg


class TestDeathBillingMoment:
    """
    V11.7.3: billing_moment='death' handler in _compute_disease_costs_v2().

    Tests:
      1. Cost fires when new_aids_deaths > 0
      2. Cost is independent of step_fraction (no multiplication)
      3. No cost when new_aids_deaths = 0 (default)
      4. Regression: existing 'diagnosis' billing still works after refactor
      5. 'terminal_care' is a separate breakdown key from 'hiv_care'
    """

    def test_death_billing_fires_for_aids_deaths(self):
        """
        Cost fires correctly: 5 AIDS deaths × €1000/death = €5000 in terminal_care.

        freeze_year == year to neutralise discount factor (factor = 1.0 exactly).
        """
        cfg = _make_v2_config_with_terminal_care(cost_per_death=1000.0)
        acc = EconomicsAccumulator(
            hta_config=cfg,
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
            arm_name="no_prep",
        )
        pop = _empty_pop()
        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_diagnoses=0,
            new_aids_deaths=5,
        )
        summary = acc.get_summary()
        # Undiscounted (freeze_year == year → discount factor = 1)
        terminal_cost = summary["cost_breakdown_disc"].get("terminal_care", 0.0)
        # Expected: 5 deaths × €1000 = €5000
        assert abs(terminal_cost - 5000.0) < 1e-6, (
            f"Expected terminal_care undiscounted = 5000.0, got {terminal_cost}"
        )

    def test_death_billing_independent_of_step_fraction(self):
        """
        The 'death' billing moment does NOT multiply by step_fraction.
        Two accumulators with different step_fractions must produce identical
        terminal care costs for the same new_aids_deaths count.
        """
        cfg = _make_v2_config_with_terminal_care(cost_per_death=2000.0)
        DEATHS = 10

        def _run(sf: float) -> float:
            acc = EconomicsAccumulator(
                hta_config=cfg,
                groups=_MockGroups(),
                freeze_year=2025.0,
                step_fraction=sf,
                arm_name="no_prep",
            )
            _accumulate(acc, 
                step=0, year=2025.0, population=_empty_pop(),
                new_infections=0, new_diagnoses=0,
                new_aids_deaths=DEATHS,
            )
            return acc.get_summary()["cost_breakdown_disc"].get("terminal_care", 0.0)

        cost_quarterly = _run(0.25)
        cost_annual    = _run(1.0)
        # Both should equal 10 × 2000 = 20000, irrespective of step_fraction
        assert abs(cost_quarterly - 20000.0) < 1e-6, (
            f"quarterly sf: expected 20000, got {cost_quarterly}"
        )
        assert abs(cost_annual - 20000.0) < 1e-6, (
            f"annual sf: expected 20000, got {cost_annual}"
        )
        assert abs(cost_quarterly - cost_annual) < 1e-9, (
            "terminal_care cost differs between step_fraction=0.25 and step_fraction=1.0; "
            "death billing must be step_fraction-independent"
        )

    def test_death_billing_zero_deaths_no_cost(self):
        """
        No cost accumulates when new_aids_deaths is not provided (default = 0).
        The 'terminal_care' key should be absent or zero in the breakdown.
        """
        cfg = _make_v2_config_with_terminal_care(cost_per_death=5000.0)
        acc = EconomicsAccumulator(
            hta_config=cfg,
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
            arm_name="no_prep",
        )
        pop = _empty_pop()
        # Call without new_aids_deaths — should default to 0
        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_diagnoses=0,
        )
        summary = acc.get_summary()
        terminal_cost = summary["cost_breakdown_disc"].get("terminal_care", 0.0)
        assert terminal_cost == 0.0, (
            f"Expected zero terminal_care with no AIDS deaths, got {terminal_cost}"
        )

    def test_diagnosis_billing_regression_after_death_refactor(self):
        """
        Regression guard: existing billing_moment='diagnosis' still fires correctly
        after _compute_disease_costs_v2() signature was extended with new_aids_deaths.

        3 new diagnoses × €500 linkage cost = €1500 in hiv_care.
        No terminal_care cost (new_aids_deaths=0).
        """
        # Use base config (no terminal care item) to isolate the diagnosis path
        acc = EconomicsAccumulator(
            hta_config=_make_v2_config(),
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
            arm_name="no_prep",
        )
        pop = _empty_pop()
        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_diagnoses=3,
            new_aids_deaths=0,
        )
        summary = acc.get_summary()
        breakdown = summary["cost_breakdown_disc"]
        # Linkage cost: 3 × 500 = 1500
        hiv_care = breakdown.get("hiv_care", 0.0)
        assert abs(hiv_care - 1500.0) < 1e-6, (
            f"Expected hiv_care = 1500 from diagnosis billing, got {hiv_care}"
        )
        # No terminal_care cost
        terminal_care = breakdown.get("terminal_care", 0.0)
        assert terminal_care == 0.0, (
            f"Expected zero terminal_care with no AIDS deaths, got {terminal_care}"
        )

    def test_terminal_care_separate_from_hiv_care_in_breakdown(self):
        """
        'terminal_care' and 'hiv_care' must be distinct keys in the cost breakdown.
        Critical for thesis reporting — these categories must not be conflated.

        Config has both:
          - hiv_diagnosis_linkage: billing_moment='diagnosis', value=500, category='hiv_care'
          - hiv_terminal_care:     billing_moment='death',     value=1000, category='terminal_care'

        Call with new_diagnoses=2, new_aids_deaths=3.
        Expected: hiv_care += 2×500 = 1000; terminal_care += 3×1000 = 3000
        """
        cfg = _make_v2_config_with_terminal_care(cost_per_death=1000.0)
        acc = EconomicsAccumulator(
            hta_config=cfg,
            groups=_MockGroups(),
            freeze_year=2025.0,
            step_fraction=STEP_FRACTION,
            arm_name="no_prep",
        )
        pop = _empty_pop()
        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_diagnoses=2,
            new_aids_deaths=3,
        )
        summary = acc.get_summary()
        breakdown = summary["cost_breakdown_disc"]

        hiv_care      = breakdown.get("hiv_care",      0.0)
        terminal_care = breakdown.get("terminal_care", 0.0)

        # hiv_care: diagnosis linkage only (2 × 500 = 1000; no ongoing because pop=0)
        assert abs(hiv_care - 1000.0) < 1e-6, (
            f"Expected hiv_care = 1000 (2 diagnoses × €500), got {hiv_care}"
        )
        # terminal_care: 3 deaths × €1000 = 3000
        assert abs(terminal_care - 3000.0) < 1e-6, (
            f"Expected terminal_care = 3000 (3 AIDS deaths × €1000), got {terminal_care}"
        )
        # Keys must be present and distinct
        assert "terminal_care" in breakdown, "terminal_care key missing from breakdown"
        assert "hiv_care"      in breakdown, "hiv_care key missing from breakdown"
        assert breakdown["terminal_care"] != breakdown["hiv_care"] or (
            breakdown["terminal_care"] == 0 and breakdown["hiv_care"] == 0
        ), "terminal_care and hiv_care must be separate categories"


# ─────────────────────────────────────────────────────────────────────────────
# V11.9.11 — frequency:"once" billing in prevention costs
# ─────────────────────────────────────────────────────────────────────────────
# Root cause: oral_loading_year1 had frequency:"annual" in hta_config.json but
# represents a one-time event (€839.98 for 2 oral tablets at initiation). The
# arm-specific loop in _compute_prevention_costs_v2() was multiplying all items
# by step_fraction unconditionally, charging €209.99 instead of €839.98 per new
# initiator (€630/person undercount at quarterly resolution).
#
# Fix: items with frequency:"once" skip step_fraction multiplication.
# Discriminator is frequency (not billing_moment): injection_year1 also has
# billing_moment:"initiation" but IS an annual rate correctly amortised quarterly.
#
# Tests:
#   1. frequency:"once" → full item cost charged (no step_fraction)
#   2. frequency:"annual" → cost × step_fraction (regression — existing behaviour)
#   3. Two-step oral_loading_year1 sequence: fires full value at initiation, zero
#      on subsequent step (cohort:new_initiators yields 0 when no new entrants)
# ─────────────────────────────────────────────────────────────────────────────

def _make_lena_accumulator_with_oral_loading():
    """
    Returns an EconomicsAccumulator for the lenacapavir arm with a minimal config
    that includes oral_loading_year1 (frequency:"once") alongside injection_year1
    (frequency:"annual"), mirroring the real hta_config.json structure post-V11.9.11.
    """
    import copy
    cfg = copy.deepcopy(_make_v2_config())
    cfg["prevention_costs"]["arm_specific"]["lenacapavir"]["oral_loading_year1"] = {
        "value":     839.98,
        "cohort":    "new_initiators",
        "category":  "drug",
        "frequency": "once",  # V11.9.11: one-time cost — no step_fraction
    }
    return EconomicsAccumulator(
        hta_config=cfg,
        groups=_MockGroups(),
        freeze_year=2025.0,
        step_fraction=STEP_FRACTION,
        arm_name="lenacapavir",
    )


class TestOnceBillingPrevention:
    """V11.9.11 — frequency:"once" prevention cost items must not be divided by step_fraction."""

    def test_once_frequency_charges_full_value(self):
        """
        frequency:"once" item with 100 new initiators must charge the full item
        cost per person — no step_fraction multiplication.

        Expected for oral_loading_year1 (once) with 100 new initiators:
          oral_loading_year1: 100 × 839.98 × 1.0  = 83,998.00  (no step_fraction)

        If bug were still present (step_fraction applied):
          100 × 839.98 × 0.25 = 20,999.50          (would fail this assertion)
        """
        acc = _make_lena_accumulator_with_oral_loading()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100  # 100 new Lenacapavir users

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        # Isolate oral_loading_year1 contribution by subtracting the known annual items
        oral_loading_expected = 100 * 839.98  # no step_fraction
        injection_year1       = 100 * 21395.20 * STEP_FRACTION
        sti_screening         = 100 * 300      * STEP_FRACTION
        total_expected        = oral_loading_expected + injection_year1 + sti_screening

        assert rec["step_costs_undisc"] == pytest.approx(total_expected), (
            f"Expected {total_expected:.2f} (oral_loading at full value), "
            f"got {rec['step_costs_undisc']:.2f}. "
            f"If step_fraction were applied: {100*839.98*STEP_FRACTION:.2f} would be used "
            f"instead of {100*839.98:.2f}."
        )

    def test_annual_frequency_still_uses_step_fraction(self):
        """
        Regression: frequency:"annual" items must still be multiplied by step_fraction.
        injection_year1 (annual, new_initiators) with 100 new users:
          Expected: 100 × 21395.20 × 0.25 = 534,880.00
        This test ensures the V11.9.11 change does not accidentally remove step_fraction
        from annual items.
        """
        acc = _make_accumulator(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        # Config has injection_year1 (annual, new_initiators) + STI screening (annual, shared)
        # No oral_loading_year1 in BASE_HTA_CONFIG — this uses the standard fixture
        expected = (
            100 * 21395.20 * STEP_FRACTION +  # injection_year1 — annual rate, step_fraction applied
            100 * 300      * STEP_FRACTION     # STI screening
        )
        assert rec["step_costs_undisc"] == pytest.approx(expected), (
            f"Annual items regression: expected {expected:.2f}, got {rec['step_costs_undisc']:.2f}."
        )

    def test_oral_loading_year1_two_step_sequence(self):
        """
        V11.9.11 two-step sequence confirming oral_loading_year1 fires exactly once
        per initiator cohort, at full value, and is zero on the subsequent step.

        Step 0: 100 new users → new_initiators=100, continuing=0
          oral_loading_year1 (once):  100 × 839.98 = 83,998.00
          injection_year1   (annual): 100 × 21395.20 × 0.25 = 534,880.00
          STI screening:              100 × 300 × 0.25 = 7,500.00
          Total step 0 = 626,378.00

        Step 1: same 100 users → new_initiators=0, continuing=100
          oral_loading_year1 (once, new_initiators=0): 0 × 839.98 = 0.00
          injection_subsequent (annual, continuing=100): 100 × 20555.22 × 0.25 = 513,880.50
          STI screening: 100 × 300 × 0.25 = 7,500.00
          Total step 1 = 521,380.50
        """
        acc = _make_lena_accumulator_with_oral_loading()
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)

        rec0 = acc.get_timestep_records()[0]
        rec1 = acc.get_timestep_records()[1]

        expected_step0 = (
            100 * 839.98              +   # oral_loading_year1 — once, no step_fraction
            100 * 21395.20 * STEP_FRACTION +  # injection_year1 — annual
            100 * 300      * STEP_FRACTION    # STI screening
        )
        expected_step1 = (
            0   * 839.98              +   # oral_loading_year1 — zero new initiators
            100 * 20555.22 * STEP_FRACTION +  # injection_subsequent — continuing
            100 * 300      * STEP_FRACTION    # STI screening
        )

        assert rec0["step_costs_undisc"] == pytest.approx(expected_step0), (
            f"Step 0: expected {expected_step0:.2f}, got {rec0['step_costs_undisc']:.2f}"
        )
        assert rec1["step_costs_undisc"] == pytest.approx(expected_step1), (
            f"Step 1: expected {expected_step1:.2f}, got {rec1['step_costs_undisc']:.2f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# V11.9.12 — HIV+ on-ART patient time costs in societal perspective
# ─────────────────────────────────────────────────────────────────────────────
# Root cause: _compute_patient_time_costs() only counted susceptible PrEP users.
# HIV+ individuals on ART attend quarterly outpatient monitoring (VL, CD4, labs,
# clinical review) — 4 visits/year per Dutch HIV treatment guidelines. Missing
# this component understates the societal burden in the no_prep arm (more HIV+)
# and makes PrEP interventions appear less societal-cost-effective than they are.
#
# Fix: add Component 2 to _compute_patient_time_costs() — iterates Acute/Chronic/AIDS
# on-ART (art_dim=1) for ALL arms. Off-ART patients excluded (not in structured care).
# ─────────────────────────────────────────────────────────────────────────────

# Shared constants for V11.9.12 tests
_HIV_ART_VISITS   = 4      # visits/year per NVHB 2024 / SHM ATHENA
_TIME_PER_VISIT   = 2.5    # hours
_HOURLY_WAGE      = 36.50  # €/hour


def _make_societal_config() -> dict:
    """
    V11.9.12: v2 config with perspective.patient_time_costs including
    hiv_art_visits_per_year. productivity_loss disabled to isolate patient_time.
    """
    import copy
    cfg = copy.deepcopy(_make_v2_config())
    cfg["perspective"] = {
        "primary": "societal",
        "productivity_loss": {
            "enabled": False,
            "average_hourly_wage":  {"value": _HOURLY_WAGE},
            "annual_hours_worked":  {"value": 1800},
            "friction_period_weeks": {"value": 12},
            "loss_rate_by_stage": {
                "acute":           {"value": 0.3},
                "chronic_off_art": {"value": 0.1},
                "chronic_on_art":  {"value": 0.05},
                "aids_off_art":    {"value": 0.4},
                "aids_on_art":     {"value": 0.2},
            },
        },
        "patient_time_costs": {
            "enabled": True,
            "oral_prep_visits_per_year":    {"value": 4},
            "lenacapavir_visits_per_year":  {"value": 2},
            "time_per_visit_hours":         {"value": _TIME_PER_VISIT},
            "hiv_art_visits_per_year":      {"value": _HIV_ART_VISITS},  # V11.9.12
            "use_average_wage_for_time": True,
        },
        "informal_care": {"enabled": False},
    }
    return cfg


def _per_person_hiv_art_cost() -> float:
    """Cost per on-ART HIV+ person per quarterly step (helper for test assertions)."""
    return _HIV_ART_VISITS * _TIME_PER_VISIT * _HOURLY_WAGE * STEP_FRACTION


class TestHIVPatientTimeCosts:
    """V11.9.12 — HIV+ on-ART patient time costs added to _compute_patient_time_costs()."""

    def test_hiv_art_patient_time_included_in_societal(self):
        """
        no_prep arm with 1,000 Chronic/on-ART patients and zero PrEP users.

        Expected patient_time per step:
            1000 × 4 visits × 2.5 h × €36.50/h × 0.25 = €91,250.00

        Pre-V11.9.12: no_prep returned 0.0 (early return).
        Post-V11.9.12: HIV+ on-ART costs apply to all arms.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_societal_config())
        pop = _empty_pop()
        # hiv_stage=2=Chronic, art=1=On_ART
        pop[0, 0, 0, 2, 0, 1, 0] = 1000

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()

        expected = 1000 * _per_person_hiv_art_cost()  # €91,250.00
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected), (
            f"Expected €{expected:,.2f} HIV+ patient time, "
            f"got €{summary['total_societal_additions_undisc']:,.2f}. "
            f"Pre-fix: no_prep arm returned 0 (early return removed in V11.9.12)."
        )

    def test_hiv_off_art_not_charged_patient_time(self):
        """
        Off-ART patients (art_dim=0) must contribute zero patient time cost.
        Only on-ART patients attend structured monitoring.

        Setup: 500 Chronic/off-ART, 0 on-ART, 0 PrEP users.
        Expected: patient_time = 0.0
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_societal_config())
        pop = _empty_pop()
        # hiv_stage=2=Chronic, art=0=Off_ART
        pop[0, 0, 0, 2, 0, 0, 0] = 500

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0), (
            f"Off-ART patients should not be charged patient time. "
            f"Got {summary['total_societal_additions_undisc']:.2f} (expected 0.0)."
        )

    def test_all_hiv_stages_on_art_contribute(self):
        """
        All three HIV stages (Acute/Chronic/AIDS) on-ART must contribute.

        Setup: 100 Acute/on-ART + 200 Chronic/on-ART + 50 AIDS/on-ART = 350 total.
        Expected: 350 × 4 × 2.5 × 36.50 × 0.25 = €31,937.50
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_societal_config())
        pop = _empty_pop()
        # hiv_stage indices: Acute=1, Chronic=2, AIDS=3; art=1=On_ART
        pop[0, 0, 0, 1, 0, 1, 0] = 100   # Acute/on-ART
        pop[0, 0, 0, 2, 0, 1, 0] = 200   # Chronic/on-ART
        pop[0, 0, 0, 3, 0, 1, 0] = 50    # AIDS/on-ART

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()

        expected = 350 * _per_person_hiv_art_cost()  # €31,937.50
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected), (
            f"Expected €{expected:,.2f} (all stages), "
            f"got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    def test_no_prep_arm_hiv_time_higher_than_prep_arm(self):
        """
        Directional check: no_prep arm (more HIV+) must have higher patient_time_disc
        than lenacapavir arm (fewer HIV+), across a two-step accumulation.

        no_prep:     500 Chronic/on-ART HIV+ patients, 0 PrEP users
        lenacapavir: 100 Chronic/on-ART HIV+ patients + 200 PrEP users
        """
        cfg = _make_societal_config()

        acc_no_prep = _make_accumulator(arm_name="no_prep",     hta_config=cfg)
        acc_lena    = _make_accumulator(arm_name="lenacapavir", hta_config=cfg)

        pop_no_prep = _empty_pop()
        pop_no_prep[0, 0, 0, 2, 0, 1, 0] = 500   # 500 Chronic/on-ART

        pop_lena = _empty_pop()
        pop_lena[0, 0, 0, 2, 0, 1, 0] = 100       # 100 Chronic/on-ART
        pop_lena[0, 0, 0, 0, 0, 0, 2] = 200       # 200 Lenacapavir PrEP users (Susceptible)

        for step, year in [(0, 2025.0), (1, 2025.25)]:
            _accumulate(acc_no_prep, step=step, year=year, population=pop_no_prep, new_infections=0)
            _accumulate(acc_lena,    step=step, year=year, population=pop_lena,    new_infections=0)

        summary_no_prep = acc_no_prep.get_summary()
        summary_lena    = acc_lena.get_summary()

        assert summary_no_prep["total_societal_additions_disc"] > summary_lena["total_societal_additions_disc"], (
            f"no_prep societal additions ({summary_no_prep['total_societal_additions_disc']:.2f}) "
            f"should exceed lenacapavir ({summary_lena['total_societal_additions_disc']:.2f}) "
            f"— no_prep carries more HIV+ patients."
        )

    def test_payer_perspective_unaffected(self):
        """
        When no perspective block is present (payer perspective), patient_time
        must remain 0.0. V11.9.12 changes must not affect payer-perspective runs.

        Uses the base v2 config (no 'perspective' key) with Chronic/on-ART patients.
        """
        acc = _make_accumulator(arm_name="no_prep")  # base config — no perspective block
        pop = _empty_pop()
        pop[0, 0, 0, 2, 0, 1, 0] = 1000  # 1,000 Chronic/on-ART

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0), (
            f"Payer-perspective run (no perspective block) should have zero societal additions. "
            f"Got {summary['total_societal_additions_undisc']:.4f}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Friction cost productivity losses (V11.11.9)
# ─────────────────────────────────────────────────────────────────────────────

# Constants for friction cost tests — match hta_config.json values (V11.11.7)
_FC_FRICTION_WEEKS = 16.4
_FC_WAGE           = 39.88
_FC_ANNUAL_HOURS   = 1720
_FC_EMPLOY_RATE    = 1.0
_FC_LOSS_ACUTE     = 0.15
_FC_LOSS_CHRONIC_OFF = 0.10
_FC_LOSS_AIDS_OFF    = 0.50


class _MockGroupsWithAges:
    """
    MockGroups with a real age_groups list so _working_age_indices is populated.
    Required by tests that exercise human_capital_capped (prevalent path), which
    filters the population tensor by working-age dimension.
    Age groups: 12 bins from "10-14" through "65-69". Bins "15-19".."60-64"
    (indices 1–10) fall within working_age_min=15 / working_age_max=65.
    """
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
    n_hiv_stages = 4
    age_groups  = [
        "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
        "40-44", "45-49", "50-54", "55-59", "60-64", "65-69",
    ]  # 12 bins; indices 1–10 are working-age (lo in [15,65))


def _make_accumulator_with_ages(arm_name="no_prep", hta_config=None) -> EconomicsAccumulator:
    """Accumulator backed by _MockGroupsWithAges so working_age_indices is set."""
    return EconomicsAccumulator(
        hta_config=hta_config if hta_config is not None else _make_v2_config(),
        groups=_MockGroupsWithAges(),
        freeze_year=2025.0,
        step_fraction=STEP_FRACTION,
        arm_name=arm_name,
    )


def _empty_pop_ages() -> np.ndarray:
    """Zero population tensor shaped for 12 age groups (matches _MockGroupsWithAges)."""
    # age × orient × activity × hiv × diagnosis × art × prep
    return np.zeros((12, 3, 3, 4, 2, 2, 4), dtype=float)


def _make_friction_config(method: str = "friction_incident") -> dict:
    """
    Societal config with productivity_loss enabled and method selectable.
    patient_time_costs disabled to isolate productivity loss in assertions.
    """
    import copy
    cfg = copy.deepcopy(_make_v2_config())
    cfg["perspective"] = {
        "primary": "societal",
        "productivity_loss": {
            "enabled": True,
            "method":  method,
            "working_age_min": 15,
            "working_age_max": 65,
            "employment_rate_modifier": {"value": _FC_EMPLOY_RATE},
            "friction_period_weeks":    {"value": _FC_FRICTION_WEEKS},
            "average_hourly_wage":      {"value": _FC_WAGE},
            "annual_hours_worked":      {"value": _FC_ANNUAL_HOURS},
            "loss_rate_by_stage": {
                "acute":           {"value": _FC_LOSS_ACUTE},
                "chronic_off_art": {"value": _FC_LOSS_CHRONIC_OFF},
                "chronic_on_art":  {"value": 0.03},
                "aids_off_art":    {"value": _FC_LOSS_AIDS_OFF},
                "aids_on_art":     {"value": 0.20},
            },
        },
        "patient_time_costs": {"enabled": False},
    }
    return cfg


def _friction_cost_per_incident(loss_rate: float) -> float:
    """
    Expected one-time cost per incident case at a given loss rate.
    Formula: employ_rate × loss_rate × wage × annual_hours × (friction_weeks/52)
    No wa_frac applied here — _MockGroups.age_groups=[] sets _working_age_indices=None
    so _compute_working_age_fraction returns 1.0.
    """
    return _FC_EMPLOY_RATE * loss_rate * _FC_WAGE * _FC_ANNUAL_HOURS * (_FC_FRICTION_WEEKS / 52.0)


class TestProductivityLossFrictionCost:
    """V11.11.9 — Incident-based friction cost method for productivity losses."""

    # ── T1: Formula correctness ──────────────────────────────────────────────

    def test_incident_formula_acute(self):
        """
        100 new infections (Susceptible→Acute), no downstream transitions.

        Expected:
            100 × 1.0 × 0.15 × 39.88 × 1720 × (16.4/52) = one-time charge
        No step_fraction multiplication — fires as event cost.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
        )
        summary = acc.get_summary()

        expected = 100 * _friction_cost_per_incident(_FC_LOSS_ACUTE)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"Acute incident formula wrong. Expected €{expected:,.2f}, "
            f"got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    def test_incident_formula_chronic(self):
        """
        50 Acute→Chronic transitions, no other incidents.

        Expected: 50 × chronic_off_art cost (conservative proxy for ART status at transition).
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=50, new_aids=0,
        )
        summary = acc.get_summary()

        expected = 50 * _friction_cost_per_incident(_FC_LOSS_CHRONIC_OFF)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"Chronic incident formula wrong. Expected €{expected:,.2f}, "
            f"got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    def test_incident_formula_aids(self):
        """
        25 Chronic→AIDS transitions, no other incidents.

        Expected: 25 × aids_off_art cost.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=0, new_aids=25,
        )
        summary = acc.get_summary()

        expected = 25 * _friction_cost_per_incident(_FC_LOSS_AIDS_OFF)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"AIDS incident formula wrong. Expected €{expected:,.2f}, "
            f"got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    def test_all_three_stages_fire_correctly(self):
        """
        T4: new_infections=100, new_chronic=50, new_aids=25 in a single step.
        Expected total = sum of three independent stage contributions.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=50, new_aids=25,
        )
        summary = acc.get_summary()

        expected = (
            100 * _friction_cost_per_incident(_FC_LOSS_ACUTE)
            + 50 * _friction_cost_per_incident(_FC_LOSS_CHRONIC_OFF)
            + 25 * _friction_cost_per_incident(_FC_LOSS_AIDS_OFF)
        )
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"Combined three-stage total wrong. Expected €{expected:,.2f}, "
            f"got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    # ── T2: No ongoing accumulation ──────────────────────────────────────────

    def test_no_ongoing_accumulation_after_incident_step(self):
        """
        T2: Incident fires once. Subsequent steps with zero new cases contribute nothing.

        Step 0: 100 new_infections → charges friction cost.
        Steps 1–3: same prevalent population, zero incident counts → each contributes 0.

        Cumulative after step 3 = same as after step 0.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())

        # Populate with the 100 Acute individuals who were just infected
        pop = _empty_pop()
        pop[0, 0, 0, 1, 0, 0, 0] = 100   # Acute/off-ART

        # Step 0: incident fires
        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
        )
        after_step0 = acc.get_summary()["total_societal_additions_undisc"]

        # Steps 1–3: same prevalent HIV+ stock, zero new incidents
        for s, y in [(1, 2025.25), (2, 2025.5), (3, 2025.75)]:
            _accumulate(acc, 
                step=s, year=y, population=pop,
                new_infections=0, new_chronic=0, new_aids=0,
            )

        after_step3 = acc.get_summary()["total_societal_additions_undisc"]

        assert after_step3 == pytest.approx(after_step0, rel=1e-9), (
            f"Friction cost must not accumulate on prevalent stock. "
            f"After step 0: €{after_step0:,.2f}. After step 3: €{after_step3:,.2f}. "
            f"Difference: €{after_step3 - after_step0:,.2f} (should be zero)."
        )

    # ── T3: Zero incidents → zero cost ───────────────────────────────────────

    def test_zero_incidents_zero_cost(self):
        """T3: All incident counts zero → total_societal_additions = 0."""
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()
        pop[0, 0, 0, 2, 0, 1, 0] = 500   # 500 Chronic/on-ART (prevalent, not incident)

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=0, new_aids=0,
        )
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0), (
            f"Zero incident counts must yield zero friction cost. "
            f"Got €{summary['total_societal_additions_undisc']:,.4f}."
        )

    # ── T5: method dispatch — human_capital_capped routes to prevalent path ──

    def test_human_capital_capped_fires_on_prevalent_stock(self):
        """
        T5: method='human_capital_capped' charges prevalent HIV+ stock every step,
        even when incident counts are zero.

        500 Chronic/off-ART in working-age bins, zero new incidents.
        Expected under human_capital_capped: > 0 (prevalent path).
        Expected under friction_incident:    = 0 (no incidents).

        Uses _MockGroupsWithAges so _working_age_indices is populated — the
        prevalent path returns 0.0 when working_age_indices is None.
        Population placed in age index 5 (35-39), which is working-age.
        """
        pop = _empty_pop_ages()
        pop[5, 0, 0, 2, 0, 0, 0] = 500   # age=35-39, Chronic/off-ART, prevalent

        acc_fc  = _make_accumulator_with_ages(
            arm_name="no_prep", hta_config=_make_friction_config("friction_incident"))
        acc_hca = _make_accumulator_with_ages(
            arm_name="no_prep", hta_config=_make_friction_config("human_capital_capped"))

        _accumulate(acc_fc,
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=0, new_aids=0,
        )
        _accumulate(acc_hca,
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=0, new_aids=0,
        )

        fc_total  = acc_fc.get_summary()["total_societal_additions_undisc"]
        hca_total = acc_hca.get_summary()["total_societal_additions_undisc"]

        assert fc_total == pytest.approx(0.0), (
            f"friction_incident with zero incidents must yield 0. Got {fc_total:.4f}."
        )
        assert hca_total > 0.0, (
            f"human_capital_capped with 500 working-age Chronic/off-ART must yield > 0. "
            f"Got {hca_total:.4f}."
        )

    # ── T6: Unknown method raises ValueError ─────────────────────────────────

    def test_unknown_method_raises_value_error(self):
        """T6: Unrecognised method string must raise ValueError immediately."""
        acc = _make_accumulator(arm_name="no_prep",
                                hta_config=_make_friction_config("unknown_method"))
        pop = _empty_pop()

        with pytest.raises(ValueError, match="not recognised"):
            _accumulate(acc, 
                step=0, year=2025.0, population=pop,
                new_infections=10, new_chronic=0, new_aids=0,
            )

    # ── T7: Direction — incident < prevalent over multi-year horizon ──────────

    def test_incident_less_than_prevalent_over_horizon(self):
        """
        T7: Friction cost (incident) cumulative total must be less than human capital
        (prevalent) over a 10-year horizon with constant HIV+ prevalence.

        Koopmanschap (1995) Table 3: friction cost < human capital for chronic illness
        lasting longer than the friction period.

        Setup: 100 new infections in step 0; same 100 individuals remain prevalent
        in Chronic/off-ART for steps 1–39 (10 years, quarterly).
        Uses _MockGroupsWithAges so human_capital_capped can read working-age stock.
        Population placed in age index 5 (35-39, working-age).
        """
        pop_incident = _empty_pop_ages()                    # step 0: no prevalent stock
        pop_prevalent = _empty_pop_ages()
        pop_prevalent[5, 0, 0, 2, 0, 0, 0] = 100           # 100 working-age Chronic/off-ART

        acc_fc  = _make_accumulator_with_ages(
            arm_name="no_prep", hta_config=_make_friction_config("friction_incident"))
        acc_hca = _make_accumulator_with_ages(
            arm_name="no_prep", hta_config=_make_friction_config("human_capital_capped"))

        # Step 0 — incident
        _accumulate(acc_fc,
            step=0, year=2025.0, population=pop_incident,
            new_infections=100, new_chronic=0, new_aids=0,
        )
        _accumulate(acc_hca,
            step=0, year=2025.0, population=pop_incident,
            new_infections=100, new_chronic=0, new_aids=0,
        )

        # Steps 1–39 — prevalent only, no new incidents
        for s in range(1, 40):
            yr = 2025.0 + s * 0.25
            _accumulate(acc_fc,
                step=s, year=yr, population=pop_prevalent,
                new_infections=0, new_chronic=0, new_aids=0,
            )
            _accumulate(acc_hca,
                step=s, year=yr, population=pop_prevalent,
                new_infections=0, new_chronic=0, new_aids=0,
            )

        fc_total  = acc_fc.get_summary()["total_societal_additions_undisc"]
        hca_total = acc_hca.get_summary()["total_societal_additions_undisc"]

        assert fc_total < hca_total, (
            f"Friction cost ({fc_total:,.2f}) must be less than human capital capped "
            f"({hca_total:,.2f}) over a 10-year horizon per Koopmanschap (1995)."
        )

    # ── T8: Backward compat — accumulate() without new_chronic/new_aids ──────

    def test_accumulate_backward_compat_missing_incident_counts(self):
        """
        T8: Calling accumulate() without new_chronic and new_aids (defaulting to 0)
        must not raise and must return zero friction cost.

        Confirms existing callers that don't pass the new params are unaffected.
        """
        acc = _make_accumulator(arm_name="no_prep", hta_config=_make_friction_config())
        pop = _empty_pop()

        # Omit new_chronic and new_aids entirely
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0), (
            f"Missing incident params should default to 0, yielding zero cost. "
            f"Got {summary['total_societal_additions_undisc']:.4f}."
        )

    # ── T9: Disabled block → zero ─────────────────────────────────────────────

    def test_productivity_loss_disabled_yields_zero(self):
        """
        T9: When productivity_loss.enabled=False, incident method must return 0.0
        even with non-zero incident counts.
        """
        import copy
        cfg = _make_friction_config()
        cfg["perspective"]["productivity_loss"]["enabled"] = False

        acc = _make_accumulator(arm_name="no_prep", hta_config=cfg)
        pop = _empty_pop()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=50, new_aids=25,
        )
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0), (
            f"Disabled productivity_loss block must yield 0. "
            f"Got {summary['total_societal_additions_undisc']:.4f}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# V12.1 BACKLOG-21 — Age-stratified incident tracking for friction cost
# ─────────────────────────────────────────────────────────────────────────────
# All tests use _make_accumulator_with_ages() so _working_age_indices is set
# (12 bins; indices 1–10 are working-age: "15-19" through "60-64").
# Population tensor matches: shape (12, 3, 3, 4, 2, 2, 3).


def _empty_pop_12() -> np.ndarray:
    """Zero population tensor for 12 age groups (matches _MockGroupsWithAges)."""
    return np.zeros((12, 3, 3, 4, 2, 2, 4), dtype=float)


def _by_age_zeros(n_ages: int = 12) -> np.ndarray:
    """All-zero age vector of given length."""
    return np.zeros(n_ages, dtype=float)


class TestFrictionCostAgeStratified:
    """V12.1 BACKLOG-21 — Age-stratified working-age count replaces wa_frac proxy."""

    # ── T-B21-1: All infections in working-age bins → full count used ─────────

    def test_all_infections_working_age(self):
        """
        T-B21-1: 100 infections placed entirely in working-age bins (indices 1–10).

        The age-array path should yield the same result as wa_frac=1.0 because
        all incident cases fall within working_age_min=15/working_age_max=65.
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())
        pop = _empty_pop_12()

        inf_by_age = _by_age_zeros()
        inf_by_age[1:11] = 10.0   # 100 total, all working-age (indices 1–10)

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
            new_infections_by_age=inf_by_age,
            new_chronic_by_age=_by_age_zeros(),
            new_aids_by_age=_by_age_zeros(),
        )
        summary = acc.get_summary()

        expected = 100.0 * _friction_cost_per_incident(_FC_LOSS_ACUTE)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"T-B21-1 FAIL: all-working-age infections. "
            f"Expected €{expected:,.2f}, got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    # ── T-B21-2: All infections in non-working-age bins → zero cost ───────────

    def test_all_infections_non_working_age(self):
        """
        T-B21-2: 100 infections placed in non-working-age bins only (indices 0 and 11).

        Working-age count = 0 → productivity loss must be 0.
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())
        pop = _empty_pop_12()

        inf_by_age = _by_age_zeros()
        inf_by_age[0]  = 50.0   # bin "10-14" (below working_age_min=15)
        inf_by_age[11] = 50.0   # bin "65-69" (at/above working_age_max=65)

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
            new_infections_by_age=inf_by_age,
            new_chronic_by_age=_by_age_zeros(),
            new_aids_by_age=_by_age_zeros(),
        )
        summary = acc.get_summary()

        assert summary["total_societal_additions_undisc"] == pytest.approx(0.0, abs=1e-9), (
            f"T-B21-2 FAIL: non-working-age infections must yield 0. "
            f"Got €{summary['total_societal_additions_undisc']:,.6f}."
        )

    # ── T-B21-3: Mixed split — age-array path gives different result than proxy ─

    def test_mixed_age_split_differs_from_proxy(self):
        """
        T-B21-3: 80 infections in working-age bins, 20 in non-working-age.

        Population tensor is set so HIV+ working-age fraction (wa_frac) ≠ 0.80,
        meaning the age-array path and the proxy path give different answers.

        Setup:
          - 1200 HIV+ working-age individuals (indices 1–10) → 100 per bin
          - 600 HIV+ non-working-age (indices 0, 11) → 300 per bin
          - wa_frac = 1200/1800 ≈ 0.6667
          - Age-array path: n_wa = 80 (direct)
          - Proxy path:     n_wa = 100 × 0.6667 = 66.67

        The test asserts the age-array result (80 cases) rather than the proxy (66.67).
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())

        # Build population: n_age=12, orient=3, activity=3, hiv=4, diag=2, art=2, prep=3
        pop = _empty_pop_12()
        acute_idx   = 1  # hiv stage index for Acute
        # Place HIV+ (non-Susceptible) individuals to set wa_frac ≈ 0.6667
        pop[1:11, 0, 0, acute_idx, 0, 0, 0] = 100.0   # 1000 working-age HIV+
        pop[0,    0, 0, acute_idx, 0, 0, 0] = 200.0   # 200 non-working-age
        pop[11,   0, 0, acute_idx, 0, 0, 0] = 400.0   # 400 non-working-age
        # wa_frac = 1000 / 1600 = 0.625

        inf_by_age = _by_age_zeros()
        inf_by_age[1:11] = 8.0    # 80 working-age infections
        inf_by_age[0]    = 10.0   # 10 non-working-age
        inf_by_age[11]   = 10.0   # 10 non-working-age

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
            new_infections_by_age=inf_by_age,
            new_chronic_by_age=_by_age_zeros(),
            new_aids_by_age=_by_age_zeros(),
        )
        summary = acc.get_summary()

        expected_age_array = 80.0 * _friction_cost_per_incident(_FC_LOSS_ACUTE)
        expected_proxy      = 100.0 * 0.625 * _friction_cost_per_incident(_FC_LOSS_ACUTE)

        # Verify age-array result is used (80 cases), not proxy (62.5 cases)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected_age_array, rel=1e-6), (
            f"T-B21-3 FAIL: expected age-array result €{expected_age_array:,.2f} "
            f"(80 wa cases), got €{summary['total_societal_additions_undisc']:,.2f}. "
            f"Proxy would give €{expected_proxy:,.2f} (62.5 cases)."
        )

    # ── T-B21-4: Chronic and AIDS transitions via age arrays ──────────────────

    def test_chronic_and_aids_age_arrays(self):
        """
        T-B21-4: new_chronic_by_age with 40 working-age, new_aids_by_age with 10
        working-age. Verifies age-array path for all three streams independently.
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())
        pop = _empty_pop_12()

        chronic_by_age = _by_age_zeros()
        chronic_by_age[2:6] = 10.0   # 40 working-age (indices 2–5)

        aids_by_age = _by_age_zeros()
        aids_by_age[3] = 10.0        # 10 working-age (index 3)

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=0, new_chronic=40, new_aids=10,
            new_infections_by_age=_by_age_zeros(),
            new_chronic_by_age=chronic_by_age,
            new_aids_by_age=aids_by_age,
        )
        summary = acc.get_summary()

        expected = (
            40.0 * _friction_cost_per_incident(_FC_LOSS_CHRONIC_OFF)
            + 10.0 * _friction_cost_per_incident(_FC_LOSS_AIDS_OFF)
        )
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"T-B21-4 FAIL: chronic+AIDS age-array streams wrong. "
            f"Expected €{expected:,.2f}, got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    # ── T-B21-5: Fallback when *_by_age arrays are None ──────────────────────

    def test_fallback_when_arrays_none(self):
        """
        T-B21-5: _make_accumulator_with_ages() sets _working_age_indices, but no
        *_by_age kwargs are passed. Should fall back to wa_frac proxy path.

        With all-zero population, _compute_working_age_fraction returns 1.0
        (no HIV+ individuals → fallback to 1.0 in the helper). So result equals
        new_infections × cost_per_incident, same as tests that bypass working-age.
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())
        pop = _empty_pop_12()

        _accumulate(acc, 
            step=0, year=2025.0, population=pop,
            new_infections=100, new_chronic=0, new_aids=0,
            # no *_by_age kwargs → defaults to None → uses legacy proxy path
        )
        summary = acc.get_summary()

        expected = 100.0 * _friction_cost_per_incident(_FC_LOSS_ACUTE)
        assert summary["total_societal_additions_undisc"] == pytest.approx(expected, rel=1e-6), (
            f"T-B21-5 FAIL: fallback path (arrays=None) should use wa_frac proxy. "
            f"Expected €{expected:,.2f}, got €{summary['total_societal_additions_undisc']:,.2f}."
        )

    # ── T-B21-6: Mis-sized array raises ValueError immediately ───────────────

    def test_mismatched_array_length_raises(self):
        """
        T-B21-6: Passing a *_by_age array with wrong length must raise ValueError.

        The accumulator has 12 age groups (working-age max index = 10).
        Passing an array of length 5 is a programming error and must fail fast.
        """
        acc = _make_accumulator_with_ages(hta_config=_make_friction_config())
        pop = _empty_pop_12()

        wrong_length = np.zeros(5, dtype=float)   # should be length 12
        wrong_length[1] = 50.0

        with pytest.raises(ValueError, match="by_age has length"):
            _accumulate(acc, 
                step=0, year=2025.0, population=pop,
                new_infections=50, new_chronic=0, new_aids=0,
                new_infections_by_age=wrong_length,
                new_chronic_by_age=_by_age_zeros(),
                new_aids_by_age=_by_age_zeros(),
            )
