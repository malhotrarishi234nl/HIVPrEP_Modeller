"""
test_hta_costing_patch_v1117.py — V11.11.7 HTA costing patch validation tests.

Implements tests T1–T9 defined in documentation/HTA_COSTING_PATCH_PLAN_V1111.md.

Test groups:
  T1 — Oral PrEP cost decomposition (new key names, drug + monitoring + renal)
  T2 — Lenacapavir cost decomposition (monitoring shared; renal NOT applied)
  T3 — Lenacapavir year-1 initiation cost (injection + loading + monitoring)
  T4 — AIDS hospitalisation fires for off_art only (architecture decision)
  T5 — Terminal care contributes €0 at death step
  T6 — WTP threshold reads as €80,000 from production config
  T7 — Productivity loss uses updated friction_weeks=16.4 and wage=39.88
  T8 — Regression: core economics tests still pass after config changes
  T9 — PSA smoke test: art_first_line samples cluster around €7,352

All expected values are derived from first principles.  No magic numbers without
an explanatory comment.  Source: HTA_COSTING_PATCH_PLAN_V1111.md (2026-04-29).
"""

import copy
import json
import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from economics import EconomicsAccumulator
from scenario_comparator import ScenarioComparator, ScenarioResult
from config_utils import extract_value

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants
# ─────────────────────────────────────────────────────────────────────────────

STEP_FRACTION = 0.25   # Quarterly timestep used throughout

# Post-patch annual direct cost totals (excl. AE and patient time).
# These are the headline figures from HTA_COSTING_PATCH_PLAN_V1111.md Section 3.
ORAL_PREP_ANNUAL    = 2_180.97   # drug 1322.21 + monitoring 854.50 + renal 4.26
LENA_ONGOING_ANNUAL = 41_964.94  # injection_subsequent 41110.44 + monitoring 854.50
LENA_YEAR1_ANNUAL   = 42_804.92  # injection_year1 41110.44 + loading 839.98 + monitoring 854.50


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────────────

class _MockGroups:
    """Minimal PopulationGroups mock — no age groups (productivity loss disabled)."""
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
    n_hiv_stages = 4
    age_groups   = []   # empty → _working_age_indices = None → no productivity loss


class _MockGroupsWithAge:
    """
    PopulationGroups mock with one working-age group (30-34).
    Used for T7 where productivity losses must fire.
    Population tensor first dimension = 1.
    """
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
    n_hiv_stages = 4
    age_groups   = ["30-34"]   # one working-age bin; index 0 will be in [15, 65)


def _empty_pop(n_age=1) -> np.ndarray:
    """Zero population tensor: (age, orient=1, act=1, hiv=4, diag=2, art=2, prep=3)."""
    return np.zeros((n_age, 1, 1, 4, 2, 2, 4))


def _make_acc(arm_name="no_prep", hta_config=None, groups=None) -> EconomicsAccumulator:
    return EconomicsAccumulator(
        hta_config=hta_config if hta_config is not None else _make_patch_config(),
        groups=groups if groups is not None else _MockGroups(),
        freeze_year=2025.0,
        step_fraction=STEP_FRACTION,
        arm_name=arm_name,
    )


def _current_prep_users(pop: np.ndarray, prep_idx: int) -> float:
    return float(np.sum(pop[:, :, :, 0, :, :, prep_idx]))


def _accumulate(acc: EconomicsAccumulator, *args, **kwargs) -> None:
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
# Minimal v2 hta_config with V11.11.7 production values
# ─────────────────────────────────────────────────────────────────────────────

def _make_patch_config() -> dict:
    """
    Minimal v2 hta_config reproducing the V11.11.7 production prevention_costs
    and disease_costs structure.

    Values are taken directly from configs/hta_config.json post-patch and
    from HTA_COSTING_PATCH_PLAN_V1111.md Section 3.

    Distribution blocks are stripped to keep tests concise — unit tests
    use deterministic values only.
    """
    return {
        "_format_version": "v2",
        "time_horizon":   {"freeze_year": 2025, "evaluation_years": 20},
        "wtp_threshold_eur_per_qaly": {"value": 80_000},
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate":           {"value": 0.04},
        },
        "utilities": {
            "susceptible":             {"value": 1.000},
            "susceptible_oral_prep":   {"value": 0.990},
            "susceptible_lenacapavir": {"value": 0.995},
            "hiv_acute":               {"value": 0.700},
            "hiv_chronic_on_art":      {"value": 0.850},
            "hiv_chronic_off_art":     {"value": 0.780},
            "aids_on_art":             {"value": 0.600},
            "aids_off_art":            {"value": 0.400},
        },
        "adverse_events": {"mode": "prevalence", "items": []},
        "prevention_costs": {
            "shared": [
                {
                    # V11.11.7 A1: replaces sti_screening_all_prep (€300).
                    # MDR PrEP v4.5 standard monitoring bundle: 2 × (€120 visit + €307.25 lab)
                    # = €854.50/year.  Applies identically to both arms.
                    "id":           "prep_monitoring_visits_and_sti",
                    "label":        "PrEP monitoring visits + STI screening — all active PrEP users",
                    "applies_to":   ["oral_prep", "lenacapavir"],
                    "frequency":    "annual",
                    "billing_moment": "ongoing",
                    "category":     "monitoring",
                    "value":        854.50,
                },
                {
                    # V11.11.7 A1: renal creatinine monitoring for TDF users.
                    # 2 × NZA 70419 (€2.13) = €4.26/year.  oral_prep only.
                    "id":           "prep_renal_monitoring_oral",
                    "label":        "Renal function monitoring (creatinine) — oral PrEP only",
                    "applies_to":   ["oral_prep"],
                    "frequency":    "annual",
                    "billing_moment": "ongoing",
                    "category":     "monitoring",
                    "value":        4.26,
                },
            ],
            "arm_specific": {
                "oral_prep": {
                    # V11.11.7 A2: replaces drug_and_monitoring (opaque bundle).
                    # Farmacotherapeutisch Kompas: €3.62/tablet × 365.25 = €1,322.21/year.
                    "drug_tdf_ftc_generic": {
                        "value":          1322.21,
                        "frequency":      "annual",
                        "billing_moment": "ongoing",
                        "cohort":         "all",
                        "category":       "drug",
                    },
                },
                "lenacapavir": {
                    # Unchanged from pre-patch (production values confirmed).
                    "injection_year1": {
                        "value":          41_110.44,   # 4 vials × €10,277.61
                        "frequency":      "annual",
                        "billing_moment": "initiation",
                        "cohort":         "new_initiators",
                        "category":       "drug",
                    },
                    "oral_loading_year1": {
                        "value":          839.98,    # 2 × 300mg tablet × €419.99
                        "frequency":      "once",    # NOT multiplied by step_fraction
                        "billing_moment": "initiation",
                        "cohort":         "new_initiators",
                        "category":       "drug",
                    },
                    "injection_subsequent": {
                        "value":          41_110.44,
                        "frequency":      "annual",
                        "billing_moment": "ongoing",
                        "cohort":         "continuing",
                        "category":       "drug",
                    },
                },
            },
        },
        "disease_costs": {
            "art_first_line": {
                "value":                7352,
                "billing_moment":       "ongoing",
                "frequency":            "annual",
                "applies_to_stages":    ["Acute", "Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":             "drug",
            },
            "hiv_monitoring_on_art": {
                "value":                2500,
                "billing_moment":       "ongoing",
                "frequency":            "annual",
                "applies_to_stages":    ["Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":             "monitoring",
            },
            # V11.11.7 C1: applies_to_art_status restricted to ["off_art"]; value €22,000.
            "aids_hospitalization": {
                "value":                22_000,
                "billing_moment":       "ongoing",
                "frequency":            "annual",
                "applies_to_stages":    ["AIDS"],
                "applies_to_art_status": ["off_art"],   # architecture decision 2026-04-29
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
            # V11.11.7 C2: value set to €0; billing_moment=death.
            "hiv_terminal_care": {
                "value":                0,
                "billing_moment":       "death",
                "frequency":            "once",
                "applies_to_stages":    ["AIDS"],
                "applies_to_art_status": ["off_art"],
                "category":             "hiv_care",
            },
        },
        "analysis_scenarios": [],
    }


def _make_patch_config_with_perspective() -> dict:
    """
    Patch config extended with V11.11.7 productivity_loss values.
    Used for T7 only.
    """
    cfg = _make_patch_config()
    cfg["perspective"] = {
        "primary": "societal",
        "productivity_loss": {
            "enabled": True,
            "method": "human_capital_capped",  # T7 tests parameter values via prevalent path
            "working_age_min": 15,
            "working_age_max": 65,
            "employment_rate_modifier": {"value": 1.0},
            # V11.11.7 D1: 21 → 16.4 weeks (iMTA 2024 Table 6-1)
            "friction_period_weeks":    {"value": 16.4},
            # V11.11.7 D2: €36.50 → €39.88/hr (iMTA 2024 Table 6-2)
            "average_hourly_wage":      {"value": 39.88},
            "annual_hours_worked":      {"value": 1720},
            "loss_rate_by_stage": {
                "acute":           {"value": 0.15},
                "chronic_off_art": {"value": 0.10},
                "chronic_on_art":  {"value": 0.03},
                "aids_off_art":    {"value": 0.50},
                "aids_on_art":     {"value": 0.20},
            },
        },
        "patient_time_costs": {"enabled": False},  # disable for isolation
        "informal_care":      {"enabled": False},
    }
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# T1 — Oral PrEP cost decomposition
# ─────────────────────────────────────────────────────────────────────────────

class TestT1OralPrEPCostDecomposition:
    """
    T1: Single oral PrEP user, one year.
    Total direct prevention cost = drug_tdf_ftc_generic + monitoring bundle + renal.

    Expected (excl. AE, excl. patient time):
      drug_tdf_ftc_generic           : €1,322.21 × N × step_fraction
      prep_monitoring_visits_and_sti : €854.50   × N × step_fraction
      prep_renal_monitoring_oral     : €4.26     × N × step_fraction
      ─────────────────────────────────────────────────────────────
      Total per user per step        : ORAL_PREP_ANNUAL × step_fraction
    """

    def test_oral_prep_total_step_cost(self):
        """100 oral PrEP users: total step cost = ORAL_PREP_ANNUAL × 100 × step_fraction."""
        acc = _make_acc(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100   # Susceptible, Oral_PrEP index=1
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]
        expected = ORAL_PREP_ANNUAL * 100 * STEP_FRACTION
        assert rec["step_costs_undisc"] == pytest.approx(expected, rel=1e-4), (
            f"Oral PrEP step cost: expected €{expected:,.2f}, "
            f"got €{rec['step_costs_undisc']:,.2f}"
        )

    def test_oral_prep_drug_component(self):
        """
        drug_tdf_ftc_generic (category='drug') = €1,322.21 × 100 × 0.25 = €33,055.25.
        Verifies category routing for the new explicit drug key.
        """
        acc = _make_acc(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]
        # At freeze_year discount_cost = 1.0, so disc == undisc
        expected_drug = 1322.21 * 100 * STEP_FRACTION
        assert bd["drug"] == pytest.approx(expected_drug, rel=1e-4)

    def test_oral_prep_monitoring_component(self):
        """
        monitoring category = prep_monitoring_visits_and_sti + prep_renal_monitoring_oral
        = (854.50 + 4.26) × 100 × 0.25 = €21,469.00.
        """
        acc = _make_acc(arm_name="oral_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 1] = 100
        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]
        expected_monitoring = (854.50 + 4.26) * 100 * STEP_FRACTION
        assert bd["monitoring"] == pytest.approx(expected_monitoring, rel=1e-4)

    def test_oral_prep_renal_item_applies_to_oral_only(self):
        """
        prep_renal_monitoring_oral has applies_to=['oral_prep'].
        Verifies €4.26 appears in oral step cost but NOT in the lenacapavir step cost.

        We compare single-step per-user monitoring costs directly from timestep records:
          oral step 0 total  = (1322.21 + 854.50 + 4.26) × 1 × 0.25 = €545.24
          lena step 1 total  = (41110.44 + 854.50)        × 1 × 0.25 = €10,491.24
          renal contribution to oral = 4.26 × 0.25 = €1.065

        Lena step 1 (continuing, no renal): if renal were wrongly applied, cost would be
          (41110.44 + 854.50 + 4.26) × 0.25 = €10,492.30 (vs correct €10,491.24).
        """
        acc_oral = _make_acc(arm_name="oral_prep")
        pop_oral = _empty_pop()
        pop_oral[0, 0, 0, 0, 0, 0, 1] = 1   # Susceptible, Oral_PrEP
        _accumulate(acc_oral, step=0, year=2025.0, population=pop_oral, new_infections=0)

        pop_lena = _empty_pop()
        pop_lena[0, 0, 0, 0, 0, 0, 2] = 1   # Susceptible, Lenacapavir
        acc_lena = _make_acc(arm_name="lenacapavir")
        _accumulate(acc_lena, step=0, year=2025.0,  population=pop_lena, new_infections=0)
        _accumulate(acc_lena, step=1, year=2025.25, population=pop_lena, new_infections=0)

        oral_rec  = acc_oral.get_timestep_records()[0]
        lena_rec1 = acc_lena.get_timestep_records()[1]

        oral_expected = (1322.21 + 854.50 + 4.26) * 1 * STEP_FRACTION
        lena_expected = (41_110.44 + 854.50) * 1 * STEP_FRACTION
        lena_wrong    = (41_110.44 + 854.50 + 4.26) * 1 * STEP_FRACTION  # if renal wrongly applied

        assert oral_rec["step_costs_undisc"] == pytest.approx(oral_expected, rel=1e-4), (
            f"Oral PrEP step cost should include renal €4.26; "
            f"expected {oral_expected:.4f}, got {oral_rec['step_costs_undisc']:.4f}"
        )
        assert lena_rec1["step_costs_undisc"] == pytest.approx(lena_expected, rel=1e-4), (
            f"Lenacapavir step cost must NOT include renal €4.26; "
            f"expected {lena_expected:.4f}, got {lena_rec1['step_costs_undisc']:.4f}. "
            f"If renal wrongly applied: {lena_wrong:.4f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T2 — Lenacapavir cost decomposition (continuing year ≥2)
# ─────────────────────────────────────────────────────────────────────────────

class TestT2LenacapavirCostDecomposition:
    """
    T2: Single lenacapavir continuing user (year ≥2), one step.
    Renal item must NOT appear.

    Step 0: 100 new users → all injection_year1; monitoring shared.
    Step 1: same 100 users → all injection_subsequent (continuing); monitoring shared.
             renal item must not fire for lenacapavir.

    Step-1 expected:
      injection_subsequent (continuing): €41,110.44 × 100 × 0.25 = €1,027,761.00
      prep_monitoring_visits_and_sti   : €854.50    × 100 × 0.25 = €21,362.50
      prep_renal_monitoring_oral       : NOT applied (applies_to=['oral_prep'])
      ─────────────────────────────────────────────────────────────────────────
      Total                            : LENA_ONGOING_ANNUAL × 100 × 0.25
    """

    def test_lena_continuing_step_cost(self):
        """Step 1 (100 continuing) = LENA_ONGOING_ANNUAL × 100 × step_fraction."""
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100   # Susceptible, Lenacapavir

        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)

        rec1 = acc.get_timestep_records()[1]
        expected = LENA_ONGOING_ANNUAL * 100 * STEP_FRACTION
        assert rec1["step_costs_undisc"] == pytest.approx(expected, rel=1e-4), (
            f"Lena continuing step cost: expected €{expected:,.2f}, "
            f"got €{rec1['step_costs_undisc']:,.2f}"
        )

    def test_lena_renal_not_applied(self):
        """
        Lenacapavir arm must not accumulate any renal monitoring cost.
        prep_renal_monitoring_oral.applies_to = ['oral_prep'] — lenacapavir excluded.
        """
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)

        rec1 = acc.get_timestep_records()[1]

        # If renal were incorrectly applied: step1 cost would be higher by 4.26 × 100 × 0.25
        expected_without_renal = LENA_ONGOING_ANNUAL * 100 * STEP_FRACTION
        erroneous_with_renal   = (LENA_ONGOING_ANNUAL + 4.26) * 100 * STEP_FRACTION

        assert rec1["step_costs_undisc"] != pytest.approx(erroneous_with_renal, rel=1e-4), (
            "Renal item must not be applied to lenacapavir arm"
        )
        assert rec1["step_costs_undisc"] == pytest.approx(expected_without_renal, rel=1e-4)

    def test_lena_drug_in_drug_category(self):
        """
        injection_subsequent (category='drug') must appear in the 'drug' breakdown.
        Monitoring must appear in 'monitoring' breakdown.

        We run step 0 + step 1 and verify the breakdown keys are populated correctly:
        - drug contains injection costs (year1 loading + year1 annual + subsequent)
        - monitoring contains shared monitoring bundle (both steps)
        - renal monitoring must NOT be in monitoring (applies_to='oral_prep' only)
        """
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)

        summary = acc.get_summary()
        bd = summary["cost_breakdown_disc"]

        # 'drug' key must exist and be positive (injection costs are large)
        assert "drug" in bd, "Lenacapavir 'drug' category missing from breakdown"
        assert bd["drug"] > 0, "Lenacapavir drug breakdown must be positive"

        # 'monitoring' key must exist (shared monitoring bundle applies to lenacapavir)
        assert "monitoring" in bd, "Lenacapavir 'monitoring' category missing from breakdown"
        assert bd["monitoring"] > 0, "Lenacapavir monitoring breakdown must be positive"

        # Drug must be >> monitoring (injection ~41k/yr vs monitoring ~854.50/yr)
        assert bd["drug"] > bd["monitoring"] * 40, (
            f"Drug ({bd['drug']:.0f}) should be far larger than monitoring ({bd['monitoring']:.0f}) "
            f"for lenacapavir"
        )

        # Monitoring must NOT include renal (4.26/yr × 2 steps × 100 users × step_fraction)
        # If renal were wrongly applied: monitoring would be larger by 4.26 × 100 × 0.25 × 2 = 213
        monitoring_without_renal = 854.50 * 100 * STEP_FRACTION  # step 0 (disc=1.0)
        # step 1 is discounted by 1/1.04^0.25; we just check that monitoring ≈ 2×step×monitoring
        # and not the +renal version (a relative check — renal is small but must be zero for lena)
        renal_contribution = 4.26 * 100 * STEP_FRACTION  # ~106.5 per step
        # monitoring breakdown should be < 2 × monitoring_without_renal + small tolerance
        # but definitely less than if renal were added to both steps
        assert bd["monitoring"] < monitoring_without_renal * 2 + 100, (
            "Lenacapavir monitoring breakdown is unexpectedly large — renal may have been applied"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T3 — Lenacapavir year-1 initiation cost
# ─────────────────────────────────────────────────────────────────────────────

class TestT3LenacapavirYear1InitiationCost:
    """
    T3: 100 new initiators at step 0.

    Step-0 expected:
      injection_year1      (new_initiators, frequency=annual) : €41,110.44 × 100 × 0.25
      oral_loading_year1   (new_initiators, frequency=once)   : €839.98    × 100        ← NO step_fraction
      prep_monitoring_sti  (shared, frequency=annual)          : €854.50    × 100 × 0.25
      ─────────────────────────────────────────────────────────────────────────────────
      Total                                                    : LENA_YEAR1_ANNUAL × 100 × 0.25
                                                                 + 839.98 × 100 × (1 − 0.25)
                                                                 [the extra from frequency='once']

    NOTE on expected arithmetic:
      LENA_YEAR1_ANNUAL = injection_year1 + oral_loading + monitoring
                        = 41110.44 + 839.98 + 854.50 = 42,804.92 (annual basis)
      But oral_loading is frequency='once', so NOT multiplied by step_fraction.
      Correct step-0 total:
        = 41110.44 × 100 × 0.25      (injection, annual)
        + 839.98   × 100             (loading, once — full amount)
        + 854.50   × 100 × 0.25      (monitoring, annual)
    """

    LENA_STEP0_EXPECTED = (
        41_110.44 * 100 * STEP_FRACTION   # injection_year1 (annual)
        + 839.98  * 100                   # oral_loading_year1 (once — no step_fraction)
        + 854.50  * 100 * STEP_FRACTION   # monitoring (annual)
    )

    def test_year1_step_cost(self):
        """Step 0 with 100 new initiators equals computed year-1 initiation total."""
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        assert rec["step_costs_undisc"] == pytest.approx(self.LENA_STEP0_EXPECTED, rel=1e-4), (
            f"Year-1 initiation: expected €{self.LENA_STEP0_EXPECTED:,.2f}, "
            f"got €{rec['step_costs_undisc']:,.2f}"
        )

    def test_oral_loading_not_multiplied_by_step_fraction(self):
        """
        oral_loading_year1 has frequency='once' — the full €839.98 fires per initiator.
        Verify that the step total exceeds what it would be if step_fraction were applied.
        """
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 1   # single user for clarity

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        # If step_fraction were incorrectly applied to loading: 839.98 × 0.25 = 209.995
        # Correct: 839.98 × 1 = 839.98
        loading_correct    = 839.98
        loading_if_scaled  = 839.98 * STEP_FRACTION

        total_expected = (
            41_110.44 * 1 * STEP_FRACTION    # injection_year1
            + loading_correct                 # oral loading (once)
            + 854.50  * 1 * STEP_FRACTION    # monitoring
        )
        total_wrong = (
            41_110.44 * 1 * STEP_FRACTION
            + loading_if_scaled               # (erroneous)
            + 854.50  * 1 * STEP_FRACTION
        )

        assert rec["step_costs_undisc"] == pytest.approx(total_expected, rel=1e-4), (
            f"Expected {total_expected:.4f} (loading once), got {rec['step_costs_undisc']:.4f}. "
            f"If erroneously scaled: {total_wrong:.4f}."
        )
        assert rec["step_costs_undisc"] != pytest.approx(total_wrong, rel=1e-4)

    def test_loading_fires_only_at_initiation(self):
        """
        oral_loading_year1 (cohort='new_initiators') fires at step 0 for all 100 users.
        At step 1 (no new users), loading must not re-fire.
        """
        acc = _make_acc(arm_name="lenacapavir")
        pop = _empty_pop()
        pop[0, 0, 0, 0, 0, 0, 2] = 100

        _accumulate(acc, step=0, year=2025.0,  population=pop, new_infections=0)
        _accumulate(acc, step=1, year=2025.25, population=pop, new_infections=0)

        rec1 = acc.get_timestep_records()[1]
        # Step 1: no new initiators → only continuing injection + monitoring
        expected_step1 = LENA_ONGOING_ANNUAL * 100 * STEP_FRACTION
        assert rec1["step_costs_undisc"] == pytest.approx(expected_step1, rel=1e-4), (
            "Oral loading must not re-fire at step 1 (no new initiators)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T4 — AIDS hospitalisation fires for off_art only
# ─────────────────────────────────────────────────────────────────────────────

class TestT4AIDSHospitalisationOffArtOnly:
    """
    T4: Architecture decision — aids_hospitalization applies to off_art only.

    100 AIDS on_art:
      aids_hospitalization (applies_to_art_status=['off_art']): €0
      hiv_monitoring_on_art (AIDS, on_art): €2,500 × 100 × 0.25

    100 AIDS off_art:
      aids_hospitalization (AIDS, off_art): €22,000 × 100 × 0.25
      hiv_monitoring_on_art → off_art not in ['on_art'] → €0
      art_first_line → off_art not in ['on_art'] → €0
    """

    def test_aids_on_art_no_hospitalisation_cost(self):
        """AIDS on-ART: hospitalisation must NOT fire (applies_to_art_status=['off_art'])."""
        acc = _make_acc(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 3, 1, 1, 0] = 100   # AIDS, diagnosed, on_art=1

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        # Only hiv_monitoring_on_art should fire:
        #   art_first_line: AIDS stage is in applies_to_stages=['Acute','Chronic','AIDS'],
        #                   on_art OK → fires: €7,352 × 100 × 0.25
        #   hiv_monitoring_on_art: AIDS, on_art → fires: €2,500 × 100 × 0.25
        #   aids_hospitalization: on_art NOT in ['off_art'] → 0
        expected = (7352 + 2500) * 100 * STEP_FRACTION
        assert rec["step_costs_undisc"] == pytest.approx(expected, rel=1e-4), (
            f"AIDS on-ART: expected ART + monitoring only (€{expected:,.2f}), "
            f"got €{rec['step_costs_undisc']:,.2f}. "
            f"Hospitalisation must not fire for on-ART AIDS patients."
        )

    def test_aids_off_art_hospitalisation_fires(self):
        """AIDS off-ART: hospitalisation fires at €22,000; no ART costs."""
        acc = _make_acc(arm_name="no_prep")
        pop = _empty_pop()
        pop[0, 0, 0, 3, 1, 0, 0] = 100   # AIDS, diagnosed, off_art=0

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        rec = acc.get_timestep_records()[0]

        expected = 22_000 * 100 * STEP_FRACTION   # hospitalisation only
        assert rec["step_costs_undisc"] == pytest.approx(expected, rel=1e-4), (
            f"AIDS off-ART: expected hospitalisation only (€{expected:,.2f}), "
            f"got €{rec['step_costs_undisc']:,.2f}"
        )

    def test_aids_hospitalisation_value_is_22000_not_40000(self):
        """
        Production config regression guard: aids_hospitalization value must be
        €22,000 (V11.11.7) not the old placeholder of €40,000.
        Failure here means the config change was reverted.
        """
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)

        hosp = production_cfg["disease_costs"]["aids_hospitalization"]
        assert hosp["value"] == pytest.approx(22_000), (
            f"aids_hospitalization value: expected €22,000, found €{hosp['value']}"
        )
        assert hosp["applies_to_art_status"] == ["off_art"], (
            f"aids_hospitalization applies_to_art_status must be ['off_art'], "
            f"found {hosp['applies_to_art_status']}"
        )

    def test_aids_on_art_vs_off_art_cost_differential(self):
        """
        Head-to-head: same number of AIDS on-ART vs off-ART → off-ART must be costlier.
        Old config (both arms): cost difference = 0 in disease_costs structure.
        New config (off_art only): on-ART gets ART drug + monitoring; off-ART gets hospitalisation.
        """
        pop_on  = _empty_pop(); pop_on [0, 0, 0, 3, 1, 1, 0] = 100   # on_art
        pop_off = _empty_pop(); pop_off[0, 0, 0, 3, 1, 0, 0] = 100   # off_art

        acc_on  = _make_acc(arm_name="no_prep")
        acc_off = _make_acc(arm_name="no_prep")

        _accumulate(acc_on,  step=0, year=2025.0, population=pop_on,  new_infections=0)
        _accumulate(acc_off, step=0, year=2025.0, population=pop_off, new_infections=0)

        cost_on  = acc_on .get_timestep_records()[0]["step_costs_undisc"]
        cost_off = acc_off.get_timestep_records()[0]["step_costs_undisc"]

        # off_art cost (hosp only) = 22000 × 100 × 0.25 = 550,000
        # on_art cost (ART + mon)  = (7352+2500) × 100 × 0.25 = 246,300
        assert cost_off > cost_on, (
            f"AIDS off-ART must have higher disease cost than on-ART. "
            f"off={cost_off:.0f}, on={cost_on:.0f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T5 — Terminal care contributes €0 at death step
# ─────────────────────────────────────────────────────────────────────────────

class TestT5TerminalCareZero:
    """
    T5: hiv_terminal_care value=0, billing_moment='death'.

    With value=0, even if billing_moment=death fires correctly, the cost is zero.
    Tests that:
    1. Zero AIDS deaths → zero terminal care cost (trivially true).
    2. Non-zero AIDS deaths → cost still zero (value=0 validated correctly).
    3. Production config has value=0.
    """

    def test_zero_aids_deaths_zero_terminal_care(self):
        """No AIDS deaths → no terminal care cost fired."""
        acc = _make_acc(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop,
                       new_infections=0, new_aids_deaths=0)
        rec = acc.get_timestep_records()[0]
        assert rec["step_costs_undisc"] == pytest.approx(0.0)

    def test_aids_deaths_with_zero_value_yields_zero_cost(self):
        """
        5 AIDS deaths × €0 terminal care = €0 total.
        Confirms value=0 is correctly multiplied through the billing_moment='death' path.
        """
        acc = _make_acc(arm_name="no_prep")
        pop = _empty_pop()
        _accumulate(acc, step=0, year=2025.0, population=pop,
                       new_infections=0, new_aids_deaths=5)
        rec = acc.get_timestep_records()[0]
        # hiv_terminal_care: 5 × 0 = 0.  No other disease_costs apply with empty pop.
        assert rec["step_costs_undisc"] == pytest.approx(0.0), (
            f"5 AIDS deaths × €0 terminal care must yield €0, "
            f"got €{rec['step_costs_undisc']:.2f}"
        )

    def test_terminal_care_value_zero_in_production_config(self):
        """
        Production config regression guard: hiv_terminal_care.value must be 0.
        Failure means V11.11.7 config change was reverted.
        """
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)

        tc = production_cfg["disease_costs"]["hiv_terminal_care"]
        assert tc["value"] == pytest.approx(0.0), (
            f"hiv_terminal_care.value must be 0 (V11.11.7); found {tc['value']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T6 — WTP threshold reads as €80,000 from production config
# ─────────────────────────────────────────────────────────────────────────────

class TestT6WTPThresholdPropagation:
    """
    T6: WTP threshold must be €80,000 (ZIN 2022 proportional shortfall tier 3).

    Tests:
    1. Production config value reads correctly.
    2. ScenarioComparator with WTP=80,000 marks ICER below threshold as cost-effective.
    3. The old value (€30,000) would mark the same ICER as NOT cost-effective.
    """

    def _load_production_wtp(self) -> float:
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            cfg = json.load(f)
        return extract_value(cfg["wtp_threshold_eur_per_qaly"])

    def test_production_wtp_is_80000(self):
        """Production config wtp_threshold_eur_per_qaly.value == 80,000."""
        wtp = self._load_production_wtp()
        assert wtp == pytest.approx(80_000), (
            f"WTP threshold must be €80,000 (ZIN 2022 PS tier 3); found €{wtp:,.0f}"
        )

    def _make_scenario_result(self, name: str, costs_disc: float, qalys_disc: float,
                               infections: int) -> ScenarioResult:
        """Build a minimal ScenarioResult for comparator tests."""
        return ScenarioResult(
            name=name,
            analysis_scenario="test_scenario",
            total_costs_disc=costs_disc,
            total_costs_undisc=costs_disc,   # simplified: disc == undisc for this test
            total_qalys_disc=qalys_disc,
            total_qalys_undisc=qalys_disc,
            cumul_new_infections=infections,
        )

    def test_icer_50000_is_cost_effective_at_80k_wtp(self):
        """
        ICER = €50,000/QALY is cost-effective at €80,000 WTP.
        NMB = 80,000 × delta_qaly − delta_cost > 0 when ICER < WTP.

        delta_cost = 50, delta_qaly = 0.001 → ICER = 50,000.
        NMB at 80k = 80,000 × 0.001 − 50 = 80 − 50 = 30 > 0.
        """
        comparator = ScenarioComparator(wtp_threshold=80_000, step_fraction=STEP_FRACTION)

        ref_result = self._make_scenario_result("oral_prep", 100_000, 10.0,    50)
        int_result = self._make_scenario_result("lenacapavir", 100_050, 10.001, 40)
        # delta_cost=50, delta_qaly=0.001 → ICER = 50,000 EUR/QALY

        comparison = comparator.compare_pair(reference=ref_result, intervention=int_result)
        nmb = comparison["nmb"]
        # NMB at 80k = 80,000 × 0.001 − 50 = 80 − 50 = 30 > 0  → cost-effective
        assert nmb > 0, (
            f"ICER €50,000 must be cost-effective at WTP €80,000 (NMB > 0). "
            f"Got NMB={nmb:.2f}"
        )

    def test_icer_50000_not_cost_effective_at_old_30k_wtp(self):
        """
        Regression guard: ICER = €50,000/QALY is NOT cost-effective at old WTP of €30,000.
        Confirms that the change from €30,000 → €80,000 meaningfully changes CE classification
        for ICERs in the 30,000–80,000 range (where lenacapavir is likely to fall).

        NMB at 30k = 30,000 × 0.001 − 50 = 30 − 50 = −20 < 0.
        """
        comparator = ScenarioComparator(wtp_threshold=30_000, step_fraction=STEP_FRACTION)

        ref_result = self._make_scenario_result("oral_prep",    100_000, 10.0,    50)
        int_result = self._make_scenario_result("lenacapavir",  100_050, 10.001,  40)
        # Same ICER = 50,000 — above old threshold of 30,000

        comparison = comparator.compare_pair(reference=ref_result, intervention=int_result)
        nmb = comparison["nmb"]
        # NMB at 30k = 30,000 × 0.001 − 50 = 30 − 50 = −20 < 0 → not cost-effective
        assert nmb < 0, (
            "At old WTP €30,000, ICER €50,000 should NOT be cost-effective (NMB < 0). "
            f"Got NMB={nmb:.2f}. "
            "This test confirms why the WTP update matters for lenacapavir CE classification."
        )


# ─────────────────────────────────────────────────────────────────────────────
# T7 — Productivity loss uses updated friction_weeks=16.4 and wage=39.88
# ─────────────────────────────────────────────────────────────────────────────

class TestT7ProductivityLossParameters:
    """
    T7: Productivity loss formula with V11.11.7 values (friction=16.4 wks, wage=€39.88).

    Friction cost formula (ZIN 2022, iMTA 2024):
      loss = count × employ_rate × loss_rate × hourly_wage × annual_hours
             × step_fraction × min(1, friction_weeks / 52)

    Using:
      count          = 100 (AIDS off-ART, working-age)
      employ_rate    = 1.0
      loss_rate      = 0.50 (aids_off_art)
      hourly_wage    = 39.88 (V11.11.7 value)
      annual_hours   = 1720
      step_fraction  = 0.25
      friction_cap   = min(1.0, 16.4/52) = 16.4/52

    Old values would give:
      friction_cap_old = min(1.0, 21/52) = 21/52
      hourly_wage_old  = 36.50

    We verify both the absolute value (with new parameters) and that it differs
    from the old-parameter calculation.
    """

    # V11.11.7 expected productivity loss: 100 AIDS off-ART, one step
    NEW_FRICTION   = 16.4
    NEW_WAGE       = 39.88
    OLD_FRICTION   = 21.0
    OLD_WAGE       = 36.50
    ANNUAL_HOURS   = 1720
    EMPLOY_RATE    = 1.0
    LOSS_RATE_AIDS = 0.50
    N_USERS        = 100

    @classmethod
    def _expected_loss(cls, friction_wks, wage):
        return (
            cls.N_USERS
            * cls.EMPLOY_RATE
            * cls.LOSS_RATE_AIDS
            * wage
            * cls.ANNUAL_HOURS
            * STEP_FRACTION
            * min(1.0, friction_wks / 52.0)
        )

    def test_productivity_loss_with_new_parameters(self):
        """
        Societal productivity loss for 100 AIDS off-ART users matches
        the V11.11.7 friction_weeks=16.4 and wage=€39.88 calculation.
        """
        cfg = _make_patch_config_with_perspective()
        acc = _make_acc(arm_name="no_prep", hta_config=cfg, groups=_MockGroupsWithAge())
        pop = _empty_pop(n_age=1)
        pop[0, 0, 0, 3, 1, 0, 0] = self.N_USERS   # AIDS, diagnosed, off_art=0

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)

        summary = acc.get_summary()
        societal_bd = summary.get("societal_breakdown_disc", {})
        prod_loss_disc = societal_bd.get("productivity_loss", 0.0)

        expected = self._expected_loss(self.NEW_FRICTION, self.NEW_WAGE)
        assert prod_loss_disc == pytest.approx(expected, rel=1e-4), (
            f"Productivity loss with V11.11.7 params: expected €{expected:,.2f}, "
            f"got €{prod_loss_disc:,.2f}"
        )

    def test_productivity_loss_differs_from_old_parameters(self):
        """
        New productivity loss must differ meaningfully from the old-parameter value.
        Confirms that the config changes have a real impact on societal costs.

        Direction of change:
          friction weeks: 21 → 16.4 (shorter) → LOWER productivity loss
          hourly wage:    36.50 → 39.88 (higher) → HIGHER productivity loss
        Net effect depends on magnitudes: 16.4/21 = 0.781 vs 39.88/36.50 = 1.093 → net ↓.
        """
        cfg = _make_patch_config_with_perspective()
        acc = _make_acc(arm_name="no_prep", hta_config=cfg, groups=_MockGroupsWithAge())
        pop = _empty_pop(n_age=1)
        pop[0, 0, 0, 3, 1, 0, 0] = self.N_USERS

        _accumulate(acc, step=0, year=2025.0, population=pop, new_infections=0)
        summary = acc.get_summary()
        prod_loss_new = summary.get("societal_breakdown_disc", {}).get("productivity_loss", 0.0)

        expected_new = self._expected_loss(self.NEW_FRICTION, self.NEW_WAGE)
        expected_old = self._expected_loss(self.OLD_FRICTION, self.OLD_WAGE)

        assert prod_loss_new == pytest.approx(expected_new, rel=1e-4)
        assert prod_loss_new != pytest.approx(expected_old, rel=1e-3), (
            "New parameters must produce a different productivity loss from old parameters. "
            f"New: €{expected_new:,.2f}, Old: €{expected_old:,.2f}"
        )
        # Net direction: new < old (friction reduction dominates wage increase)
        assert prod_loss_new < expected_old, (
            f"Net effect: new loss (€{prod_loss_new:,.2f}) should be less than "
            f"old loss (€{expected_old:,.2f}) — friction reduction dominates wage rise"
        )

    def test_friction_period_weeks_in_production_config(self):
        """Production config regression guard: friction_period_weeks.value == 16.4."""
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)
        fw = production_cfg["perspective"]["productivity_loss"]["friction_period_weeks"]["value"]
        assert fw == pytest.approx(16.4), (
            f"friction_period_weeks must be 16.4 (iMTA 2024); found {fw}"
        )

    def test_average_hourly_wage_in_production_config(self):
        """Production config regression guard: average_hourly_wage.value == 39.88."""
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)
        aw = production_cfg["perspective"]["productivity_loss"]["average_hourly_wage"]["value"]
        assert aw == pytest.approx(39.88), (
            f"average_hourly_wage must be €39.88 (iMTA 2024); found €{aw}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T8 — Regression: core economics tests still pass
# ─────────────────────────────────────────────────────────────────────────────

class TestT8Regression:
    """
    T8: Verify that the V11.11.7 config changes do not break the existing
    economics test suite (test_economics_v2.py).

    The economics engine iterates prevention_costs dynamically — renaming keys
    in production config must not break any tests that use their own test configs.

    We run test_economics_v2.py as a subprocess and assert all tests pass.
    """

    def test_economics_v2_tests_still_pass(self):
        """
        Run test_economics_v2.py via subprocess.
        All tests must pass — config-key renaming must not break engine logic.
        """
        v11_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_economics_v2.py",
             "-q", "--tb=short"],
            cwd=v11_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        passed = result.returncode == 0
        assert passed, (
            f"test_economics_v2.py failures detected after V11.11.7 config changes.\n"
            f"STDOUT:\n{result.stdout[-3000:]}\n"
            f"STDERR:\n{result.stderr[-1000:]}"
        )

    def test_prevention_costs_key_names_in_production_config(self):
        """
        Structural regression: production config must have the new key names
        (not the old ones that were replaced).

        Old keys (must be absent): sti_screening_all_prep, drug_and_monitoring
        New keys (must be present): prep_monitoring_visits_and_sti, drug_tdf_ftc_generic
        """
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            cfg = json.load(f)

        pc = cfg["prevention_costs"]
        shared_ids    = [item["id"] for item in pc.get("shared", [])]
        oral_arm_keys = list(pc.get("arm_specific", {}).get("oral_prep", {}).keys())

        # New keys must be present
        assert "prep_monitoring_visits_and_sti" in shared_ids, (
            "New shared key 'prep_monitoring_visits_and_sti' not found in production config"
        )
        assert "prep_renal_monitoring_oral" in shared_ids, (
            "New shared key 'prep_renal_monitoring_oral' not found in production config"
        )
        assert "drug_tdf_ftc_generic" in oral_arm_keys, (
            "New oral_prep key 'drug_tdf_ftc_generic' not found in production config"
        )

        # Old keys must be absent
        assert "sti_screening_all_prep" not in shared_ids, (
            "Old key 'sti_screening_all_prep' must have been removed from production config"
        )
        assert "drug_and_monitoring" not in oral_arm_keys, (
            "Old key 'drug_and_monitoring' must have been removed from production config"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T9 — PSA smoke test: art_first_line samples cluster around €8,907
# ─────────────────────────────────────────────────────────────────────────────

class TestT9PSASmokeStest:
    """
    T9: PSA distribution guard — art_first_line distribution.mean must track
    the production ART point estimate. V12.17 moved the Dovato base-case
    cost into production at €8,907/year.

    HTASampler uses a Gamma(mean=8907, se=800) distribution.
    With 500 LHS draws, the sample mean should be within ±5% of €8,907.
    """

    def _make_minimal_psa_config(self) -> dict:
        return {
            "sampling": {
                "method":              {"value": "lhs"},
                "tiers_to_sample":     {"value": ["uncertain"]},
                "include_parameters":  {"value": []},
                "exclude_parameters":  {"value": []},
            },
            "hta_sampling": {
                "exclude_placeholders": {"value": False},  # include placeholders
                "exclude_parameters":   {"value": []},
            },
        }

    def test_art_first_line_distribution_mean_is_8907(self):
        """
        Production config: art_first_line distribution.mean == 8907.
        Failure means the production Dovato ART-cost update was not reflected in PSA.
        """
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)

        art = production_cfg["disease_costs"]["art_first_line"]
        dist_mean = art.get("distribution", {}).get("mean")
        assert dist_mean == pytest.approx(8907), (
            f"art_first_line distribution.mean must be 8907 (V12.17 Dovato base case); "
            f"found {dist_mean}."
        )

    def test_art_first_line_deterministic_value_matches_distribution_mean(self):
        """
        Production config: art_first_line.value matches distribution.mean.
        Verifies deterministic value matches PSA distribution mean.
        """
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)

        art_value = production_cfg["disease_costs"]["art_first_line"]["value"]
        art_dist_mean = production_cfg["disease_costs"]["art_first_line"]["distribution"]["mean"]
        assert art_value == pytest.approx(art_dist_mean), (
            f"art_first_line.value ({art_value}) must equal distribution.mean ({art_dist_mean}). "
            "Mismatch causes PSA upward bias."
        )

    def test_hta_sampler_art_first_line_samples_cluster_around_8907(self):
        """
        HTASampler generates 500 draws for art_first_line.
        Sample mean must be within ±5% of €8,907 (i.e. in [8,462, 9,352]).
        """
        from hta_sampler import HTASampler

        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(cfg_path) as f:
            production_cfg = json.load(f)

        psa_cfg  = self._make_minimal_psa_config()
        sampler  = HTASampler(production_cfg, psa_cfg)

        # Find the art_first_line parameter path
        art_paths = [p for p in sampler.param_paths if "art_first_line" in p]
        if not art_paths:
            pytest.skip(
                "art_first_line not sampled by HTASampler — check distribution_status or "
                "distribution type. Manual config check passed in test_art_first_line_distribution_mean_is_8907."
            )

        draws   = sampler.generate_draws(n=500, seed=42)
        samples = draws[art_paths[0]].values

        sample_mean = samples.mean()
        target      = 8907.0
        tolerance   = 0.05   # 5%

        assert abs(sample_mean - target) / target < tolerance, (
            f"art_first_line PSA sample mean: {sample_mean:.0f}. "
            f"Expected within 5% of {target:.0f} (range [{target * 0.95:.0f}, {target * 1.05:.0f}]). "
            f"If this fails, the PSA distribution is no longer aligned with the production ART cost."
        )
