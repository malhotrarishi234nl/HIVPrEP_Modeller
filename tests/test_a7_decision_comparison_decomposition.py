from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_a7_decision_comparison_decomposition import (  # noqa: E402
    EXPECTED_SWITCHES,
    QALY_COMPONENT_KEYS,
    _decompose_pair,
    _select_scenarios,
)


def _fake_result(name, total_population, infections, component_values, flows):
    record = {
        "step": 100,
        "year": 2025.0,
        "step_fraction": 0.25,
        "discount_factor_health": 1.0,
        "total_population": total_population,
        "step_new_infections": infections,
    }
    for component in QALY_COMPONENT_KEYS:
        record[f"step_qaly_{component}_disc"] = component_values.get(component, 0.0)
    record["step_qalys_disc"] = sum(
        record[f"step_qaly_{component}_disc"] for component in QALY_COMPONENT_KEYS
    )
    simulation = {
        "step": 100,
        "year": 2025.0,
        "new_infections": infections,
        "total_population": total_population,
        "sexually_active_population": total_population,
        "childhood_population": 0,
        "adult_hiv_pos": 10,
        "on_art_stock": 8,
        "msm_on_oral": 10,
        "msm_on_lena": 0,
        "prep_lena_switches": 0,
        "prep_lena_switch_shortfall": 0,
        **{column: flows.get(column, 0) for column in (
            "hiv_deaths", "aids_deaths", "background_deaths", "childhood_deaths",
            "births", "net_migration", "new_aids_cases", "new_diagnoses",
            "art_initiations",
        )},
    }
    return SimpleNamespace(
        name=name,
        analysis_scenario="scenario_a_lena_substitution__switch_10",
        timestep_records=[record],
        simulation_records=[simulation],
        total_qalys_disc=record["step_qalys_disc"],
        cumul_new_infections=infections,
    )


def test_select_scenarios_requires_complete_ordered_grid():
    scenarios = [
        {
            "name": f"scenario_a_lena_substitution__switch_{switch}",
            "arm_order": ["oral_prep", "oral_prep_plus_lena"],
        }
        for switch in reversed(EXPECTED_SWITCHES)
    ]
    selected = _select_scenarios(scenarios)
    assert [s["name"] for s in selected] == [
        f"scenario_a_lena_substitution__switch_{switch}"
        for switch in EXPECTED_SWITCHES
    ]


def test_person_time_identity_reconciles_named_components():
    reference_components = {
        "susceptible_no_prep": 15.0,
        "susceptible_oral_prep": 5.0,
        "hiv_chronic_on_art": 2.0,
        "utility_decrement_art_initiation": -0.1,
    }
    intervention_components = {
        "susceptible_no_prep": 14.0,
        "susceptible_oral_prep": 3.0,
        "susceptible_lenacapavir": 3.0,
        "hiv_chronic_on_art": 2.2,
        "utility_decrement_art_initiation": -0.05,
        "utility_decrement_adverse_events": -0.02,
    }
    reference = _fake_result(
        "oral_prep", 100, 4, reference_components,
        {"births": 2, "background_deaths": 1},
    )
    intervention = _fake_result(
        "oral_prep_plus_lena", 101, 3, intervention_components,
        {"births": 2, "background_deaths": 0},
    )

    summary, components, flows = _decompose_pair(reference, intervention)

    assert summary["infections_averted"] == 1
    assert summary["delta_person_years"] == pytest.approx(0.25)
    assert summary["delta_discounted_person_years"] == pytest.approx(0.25)
    assert summary["person_time_identity_sum"] == pytest.approx(summary["delta_qaly"])
    assert summary["component_delta_sum"] == pytest.approx(summary["delta_qaly"])
    assert len(components) == len(QALY_COMPONENT_KEYS)
    assert {row["flow"] for row in flows} >= {"background_deaths", "hiv_deaths"}


def test_infection_window_mismatch_is_rejected():
    components = {"susceptible_no_prep": 25.0}
    reference = _fake_result("oral_prep", 100, 4, components, {})
    intervention = _fake_result("oral_prep_plus_lena", 100, 3, components, {})
    intervention.simulation_records[0]["new_infections"] = 2

    with pytest.raises(AssertionError, match="infection window mismatch"):
        _decompose_pair(reference, intervention)
