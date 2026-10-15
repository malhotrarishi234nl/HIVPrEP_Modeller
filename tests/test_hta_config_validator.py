"""
Tests for hta_config v2 validator (V11.8.0 — compat shim removed).

V11.8.0: Removed tests 1 (is_new_format), 9 (compat shim translation), and 10 (shim
         numerics) — hta_config_compat.py has been retired. Remaining tests cover the
         validate_hta_config() function and module acceptance.

Validates:
  - validate_hta_config(): structural rules for the new four-component format

Test categories:
  1. Validator: valid config passes silently
  2. Validator: missing top-level section raises ValueError
  3. Validator: AE item missing required field raises ValueError
  4. Validator: invalid billing_moment raises ValueError
  5. Validator: Beta distribution with alpha=0 raises ValueError
  7. Validator: Gamma distribution with se=0 raises ValueError
  8. Validator: invalid distribution type raises ValueError
  9. Compat shim: new format translates to old format correctly
  10. Compat shim: old format returned unchanged
  11. Compat shim: combined AE impact numerical equivalence
  12. EconomicsAccumulator: accepts new format config
  13. AdverseEventModule: accepts new format config
  14. Full hta_config.json on disk passes validation
"""

import os
import sys
import json
import copy
import pytest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_utils import validate_hta_config
from economics import EconomicsAccumulator
from adverse_events import AdverseEventModule


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

def minimal_valid_v2_config():
    """Return a minimal structurally valid v2 hta_config."""
    return {
        "_format_version": "v2",
        "time_horizon": {"freeze_year": 2025, "evaluation_years": 20},
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate": {"value": 0.04},
        },
        "wtp_threshold_eur_per_qaly": {"value": 50000},
        "utilities": {
            "susceptible": {
                "value": 1.0,
                "distribution": {"type": "fixed"},
            },
            "hiv_chronic_on_art": {
                "value": 0.85,
                "distribution": {"type": "beta", "alpha": 85, "beta": 15},
            },
        },
        "adverse_events": {
            "mode": "prevalence",
            "items": [
                {
                    "id": "oral_prep_gi_ae",
                    "arm": "oral_prep",
                    "billing_moment": "ongoing",
                    "frequency": "annual",
                    "prevalence": {"value": 0.15},
                    "utility_decrement": {"value": -0.02},
                    "cost": {"value": 200},
                }
            ],
        },
        "prevention_costs": {
            "shared": [
                {
                    "id": "sti_screening",
                    "value": 300,
                    "frequency": "annual",
                    "billing_moment": "ongoing",
                    "applies_to": ["oral_prep", "lenacapavir"],
                }
            ],
            "arm_specific": {
                "oral_prep": {
                    "drug_and_monitoring": {
                        "value": 5400,
                        "frequency": "annual",
                        "billing_moment": "ongoing",
                    }
                }
            },
        },
        "disease_costs": {
            "art_first_line": {
                "value": 10000,
                "frequency": "annual",
                "billing_moment": "ongoing",
                "applies_to_stages": ["Chronic"],
                "applies_to_art_status": ["on_art"],
                "category": "drug",
            }
        },
        "analysis_scenarios": [
            {
                "name": "scenario_a_base",
                "label": "Base case",
                "lenacapavir_overrides": {
                    "eligible_activity_classes": ["Low", "Medium", "High"]
                },
            }
        ],
    }


class MockGroups:
    """Minimal mock PopulationGroups for EconomicsAccumulator and AdverseEventModule."""
    def __init__(self):
        self.hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
        self.prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
        self.prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
        self.n_hiv_stages = 4


def make_population():
    return np.zeros((20, 3, 3, 4, 2, 2, 4))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Validator: valid config passes
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorValidConfig:
    def test_valid_minimal_config_passes(self):
        config = minimal_valid_v2_config()
        # Should not raise
        validate_hta_config(config)

    def test_valid_full_config_on_disk(self):
        """The actual hta_config.json on disk must pass validation."""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "hta_config.json"
        )
        with open(config_path) as f:
            config = json.load(f)
        # Should not raise
        validate_hta_config(config)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Validator: missing top-level section
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorMissingSections:
    def test_missing_utilities_raises(self):
        config = minimal_valid_v2_config()
        del config["utilities"]
        with pytest.raises(ValueError, match="utilities"):
            validate_hta_config(config)

    def test_missing_adverse_events_raises(self):
        config = minimal_valid_v2_config()
        del config["adverse_events"]
        with pytest.raises(ValueError, match="adverse_events"):
            validate_hta_config(config)

    def test_missing_prevention_costs_raises(self):
        config = minimal_valid_v2_config()
        del config["prevention_costs"]
        with pytest.raises(ValueError, match="prevention_costs"):
            validate_hta_config(config)

    def test_missing_disease_costs_raises(self):
        config = minimal_valid_v2_config()
        del config["disease_costs"]
        with pytest.raises(ValueError, match="disease_costs"):
            validate_hta_config(config)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Validator: AE item missing required field
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorAEItems:
    def test_ae_item_missing_id_raises(self):
        config = minimal_valid_v2_config()
        del config["adverse_events"]["items"][0]["id"]
        with pytest.raises(ValueError, match="'id'"):
            validate_hta_config(config)

    def test_ae_item_missing_billing_moment_raises(self):
        config = minimal_valid_v2_config()
        del config["adverse_events"]["items"][0]["billing_moment"]
        with pytest.raises(ValueError, match="'billing_moment'"):
            validate_hta_config(config)

    def test_ae_item_missing_prevalence_raises(self):
        config = minimal_valid_v2_config()
        del config["adverse_events"]["items"][0]["prevalence"]
        with pytest.raises(ValueError, match="'prevalence'"):
            validate_hta_config(config)

    def test_ae_item_missing_cost_raises(self):
        config = minimal_valid_v2_config()
        del config["adverse_events"]["items"][0]["cost"]
        with pytest.raises(ValueError, match="'cost'"):
            validate_hta_config(config)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Validator: invalid billing_moment
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorBillingMoment:
    def test_invalid_ae_billing_moment_raises(self):
        config = minimal_valid_v2_config()
        config["adverse_events"]["items"][0]["billing_moment"] = "monthly"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_valid_ae_billing_moments_pass(self):
        # Only "ongoing" and "initiation" are handled by adverse_events._compute_v2.
        # "once" and "diagnosis" were previously accepted by the validator but silently
        # returned (0.0, 0.0) at runtime — narrowed in B4 fix (V11.9.2).
        for moment in ("ongoing", "initiation"):
            config = minimal_valid_v2_config()
            config["adverse_events"]["items"][0]["billing_moment"] = moment
            validate_hta_config(config)  # Should not raise

    def test_invalid_ae_billing_once_raises(self):
        """'once' is not a valid AE billing_moment — no runtime implementation."""
        config = minimal_valid_v2_config()
        config["adverse_events"]["items"][0]["billing_moment"] = "once"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_invalid_ae_billing_diagnosis_raises(self):
        """'diagnosis' costs belong in disease_costs (economics.py), not AE items."""
        config = minimal_valid_v2_config()
        config["adverse_events"]["items"][0]["billing_moment"] = "diagnosis"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_invalid_disease_cost_billing_moment_raises(self):
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"]["billing_moment"] = "weekly"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_disease_cost_billing_initiation_raises(self):
        """ART-initiation disease costs use cohort routing, not billing_moment='initiation'."""
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"]["billing_moment"] = "initiation"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_disease_cost_billing_once_raises(self):
        """'once' is a frequency, not a disease_costs billing_moment."""
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"]["billing_moment"] = "once"
        with pytest.raises(ValueError, match="billing_moment"):
            validate_hta_config(config)

    def test_valid_art_initiation_disease_cohort_passes(self):
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"].update({
            "frequency": "once",
            "billing_moment": "ongoing",
            "cohort": "new_art_initiators",
            "applies_to_stages": ["Chronic"],
            "applies_to_art_status": ["on_art"],
        })
        validate_hta_config(config)

    def test_unknown_disease_cost_cohort_raises(self):
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"]["cohort"] = "new_patients"
        with pytest.raises(ValueError, match="cohort"):
            validate_hta_config(config)

    def test_art_cohort_without_on_art_raises(self):
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"].update({
            "cohort": "on_art_continuing",
            "applies_to_art_status": ["off_art"],
        })
        with pytest.raises(ValueError, match="on_art"):
            validate_hta_config(config)

    def test_art_cohort_without_stages_raises(self):
        config = minimal_valid_v2_config()
        config["disease_costs"]["art_first_line"].update({
            "cohort": "new_art_initiators",
            "applies_to_stages": [],
        })
        with pytest.raises(ValueError, match="applies_to_stages"):
            validate_hta_config(config)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Validator: Beta distribution with alpha=0
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorBetaDistribution:
    def test_beta_alpha_zero_raises(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"]["alpha"] = 0
        with pytest.raises(ValueError, match="alpha"):
            validate_hta_config(config)

    def test_beta_beta_zero_raises(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"]["beta"] = 0
        with pytest.raises(ValueError, match="beta"):
            validate_hta_config(config)

    def test_beta_missing_alpha_raises(self):
        config = minimal_valid_v2_config()
        del config["utilities"]["hiv_chronic_on_art"]["distribution"]["alpha"]
        with pytest.raises(ValueError, match="alpha"):
            validate_hta_config(config)

    def test_beta_negative_alpha_raises(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"]["alpha"] = -5
        with pytest.raises(ValueError, match="alpha"):
            validate_hta_config(config)

    def test_valid_beta_passes(self):
        config = minimal_valid_v2_config()
        # Distribution already has valid alpha=85, beta=15
        validate_hta_config(config)  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# 7. Validator: Gamma distribution with se=0
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorGammaDistribution:
    def test_gamma_se_zero_raises(self):
        config = minimal_valid_v2_config()
        config["prevention_costs"]["arm_specific"]["oral_prep"]["drug_and_monitoring"][
            "distribution"
        ] = {"type": "gamma", "mean": 5400, "se": 0}
        with pytest.raises(ValueError, match="se"):
            validate_hta_config(config)

    def test_gamma_mean_zero_raises(self):
        config = minimal_valid_v2_config()
        config["prevention_costs"]["arm_specific"]["oral_prep"]["drug_and_monitoring"][
            "distribution"
        ] = {"type": "gamma", "mean": 0, "se": 500}
        with pytest.raises(ValueError, match="mean"):
            validate_hta_config(config)

    def test_gamma_missing_se_raises(self):
        config = minimal_valid_v2_config()
        config["prevention_costs"]["arm_specific"]["oral_prep"]["drug_and_monitoring"][
            "distribution"
        ] = {"type": "gamma", "mean": 5400}
        with pytest.raises(ValueError, match="se"):
            validate_hta_config(config)

    def test_valid_gamma_passes(self):
        config = minimal_valid_v2_config()
        config["prevention_costs"]["arm_specific"]["oral_prep"]["drug_and_monitoring"][
            "distribution"
        ] = {"type": "gamma", "mean": 5400, "se": 500}
        validate_hta_config(config)  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# 8. Validator: invalid distribution type
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorDistributionTypes:
    def test_invalid_distribution_type_raises(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"]["type"] = "exponential"
        with pytest.raises(ValueError, match="exponential"):
            validate_hta_config(config)

    def test_all_valid_types_pass(self):
        for dist_type in ("fixed", "beta", "gamma", "lognormal", "normal", "linked"):
            config = minimal_valid_v2_config()
            if dist_type == "beta":
                config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
                    "type": "beta", "alpha": 85, "beta": 15
                }
            elif dist_type in ("gamma", "lognormal", "normal"):
                # These need mean/se but validator only strictly checks gamma
                config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
                    "type": dist_type, "mean": 0.85, "se": 0.05
                }
            elif dist_type == "linked":
                config["utilities"]["aids_on_art"] = {
                    "value": 0.85,
                    "bounds": {"lower": 0.0, "upper": 1.0},
                    "distribution": {
                        "type": "linked",
                        "source_path": "utilities.hiv_chronic_on_art",
                    },
                }
            else:
                config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
                    "type": "fixed"
                }
            validate_hta_config(config)  # Should not raise


class TestValidatorLinkedDistributions:
    @staticmethod
    def _add_link(config, source="utilities.hiv_chronic_on_art", bounds=None):
        config["utilities"]["aids_on_art"] = {
            "value": 0.85,
            "bounds": bounds or {"lower": 0.0, "upper": 1.0},
            "distribution": {"type": "linked", "source_path": source},
        }

    def test_valid_earlier_stochastic_source_passes(self):
        config = minimal_valid_v2_config()
        self._add_link(config)
        validate_hta_config(config)

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("utilities.missing", "does not name"),
            ("utilities.susceptible", "fixed"),
            ("utilities.hiv_chronic_on_art.value", "does not name"),
        ],
    )
    def test_missing_fixed_and_nested_field_sources_fail(self, source, message):
        config = minimal_valid_v2_config()
        self._add_link(config, source=source)
        with pytest.raises(ValueError, match=message):
            validate_hta_config(config)

    def test_forward_source_fails(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
            "type": "linked", "source_path": "utilities.aids_on_art"
        }
        config["utilities"]["aids_on_art"] = {
            "value": 0.7,
            "distribution": {"type": "beta", "alpha": 7, "beta": 3},
        }
        with pytest.raises(ValueError, match="must be an earlier"):
            validate_hta_config(config)

    def test_self_link_fails(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
            "type": "linked",
            "source_path": "utilities.hiv_chronic_on_art",
        }
        with pytest.raises(ValueError, match="cannot reference itself"):
            validate_hta_config(config)

    def test_nested_link_fails(self):
        config = minimal_valid_v2_config()
        config["utilities"]["first_link"] = {
            "value": 0.85,
            "bounds": {"lower": 0.0, "upper": 1.0},
            "distribution": {
                "type": "linked",
                "source_path": "utilities.hiv_chronic_on_art",
            },
        }
        self._add_link(config, source="utilities.first_link")
        with pytest.raises(ValueError, match="nested linked"):
            validate_hta_config(config)

    def test_cyclic_links_fail_with_explicit_diagnostic(self):
        config = minimal_valid_v2_config()
        config["utilities"]["hiv_chronic_on_art"]["distribution"] = {
            "type": "linked", "source_path": "utilities.aids_on_art"
        }
        self._add_link(config, source="utilities.hiv_chronic_on_art")
        with pytest.raises(ValueError, match="cyclic linked"):
            validate_hta_config(config)

    def test_source_support_must_fit_target_bounds(self):
        config = minimal_valid_v2_config()
        self._add_link(config, bounds={"lower": 0.0, "upper": 0.98})
        with pytest.raises(ValueError, match="exceed linked target bounds"):
            validate_hta_config(config)


# ─────────────────────────────────────────────────────────────────────────────
# 9. EconomicsAccumulator accepts new format
# ─────────────────────────────────────────────────────────────────────────────

class TestEconomicsAccumulatorNewFormat:
    def test_accumulator_accepts_new_format_config(self):
        """EconomicsAccumulator accepts v2 config and stores it in _v2_config."""
        config = minimal_valid_v2_config()
        groups = MockGroups()

        acc = EconomicsAccumulator(
            hta_config=config,
            groups=groups,
            freeze_year=2025,
            step_fraction=0.25,  # V11.7.2
        )
        # v2 config stored in _v2_config; hta_config retains the raw v2 structure
        assert acc._v2_config is not None
        assert acc._v2_config.get("_format_version") == "v2"

    def test_accumulator_runs_step_with_new_format(self):
        """Accumulator should complete a step without error after normalisation."""
        config = minimal_valid_v2_config()
        groups = MockGroups()

        acc = EconomicsAccumulator(
            hta_config=config,
            groups=groups,
            freeze_year=2025,
            step_fraction=0.25,  # V11.7.2
        )
        population = make_population()
        # Should not raise
        acc.accumulate(step=0, year=2025.0, population=population, new_infections=0)
        summary = acc.get_summary()
        assert "total_qalys_disc" in summary


# ─────────────────────────────────────────────────────────────────────────────
# 10. AdverseEventModule accepts new format
# ─────────────────────────────────────────────────────────────────────────────

class TestAdverseEventModuleNewFormat:
    def test_ae_module_accepts_new_format_config(self):
        """AdverseEventModule should normalise v2 config without raising."""
        config = minimal_valid_v2_config()
        groups = MockGroups()

        ae = AdverseEventModule(hta_config=config, groups=groups, step_fraction=0.25)  # V11.7.2
        assert ae.mode == "prevalence"

    def test_ae_module_compute_adjustments_with_new_format(self):
        """AE module should return numeric adjustments after normalisation."""
        config = minimal_valid_v2_config()
        groups = MockGroups()

        ae = AdverseEventModule(hta_config=config, groups=groups, step_fraction=0.25)  # V11.7.2
        population = make_population()
        population[:, :, :, 0, :, :, 1] = 1000 / (20 * 3 * 3 * 2 * 2)  # 1000 oral PrEP users

        adj = ae.compute_adjustments(population, 0, 2025.0)

        # Should return a dict with both keys
        assert "utility_decrement" in adj
        assert "cost_addition" in adj
        # Values should be numeric and non-negative
        assert adj["utility_decrement"] >= 0
        assert adj["cost_addition"] >= 0
