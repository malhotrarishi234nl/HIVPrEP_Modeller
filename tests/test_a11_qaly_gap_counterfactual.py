from dataclasses import dataclass
import sys

import pytest

from tools.run_a11_qaly_gap_counterfactual import (
    load_module_override,
    replace_economic_blocks,
    select_scenarios,
    simulation_delta_rows,
    summarize_pair,
)


def test_load_module_override_uses_exact_requested_file(tmp_path):
    module_path = tmp_path / "a11_override_fixture.py"
    module_path.write_text("MARKER = 'requested-file'\n", encoding="utf-8")
    try:
        load_module_override("a11_override_fixture", module_path)
        assert sys.modules["a11_override_fixture"].MARKER == "requested-file"
        assert sys.modules["a11_override_fixture"].__file__ == str(module_path)
    finally:
        sys.modules.pop("a11_override_fixture", None)


def test_replace_economic_blocks_removes_current_only_keys():
    composed = {
        "analysis_scenarios": [{"name": "base"}],
        "utilities": {"hiv": {"value": 0.98}},
        "utility_decrements": {"late": {"value": 0.65}},
        "discounting": {"health_effects_rate": {"value": 0.015}},
    }
    thesis_costing = {
        "utilities": {"hiv": {"value": 0.85}},
        "discounting": {"health_effects_rate": {"value": 0.015}},
    }
    replaced = replace_economic_blocks(composed, thesis_costing)
    assert replaced["analysis_scenarios"] == [{"name": "base"}]
    assert replaced["utilities"]["hiv"]["value"] == 0.85
    assert "utility_decrements" not in replaced
    assert composed["utilities"]["hiv"]["value"] == 0.98


def test_select_scenarios_preserves_requested_order_and_rejects_missing():
    scenarios = [{"name": "a"}, {"name": "b"}]
    assert [row["name"] for row in select_scenarios(scenarios, ["b", "a"])] == [
        "b",
        "a",
    ]
    with pytest.raises(ValueError, match="requested scenarios not found"):
        select_scenarios(scenarios, ["c"])


@dataclass
class FakeResult:
    total_qalys_disc: float
    cumul_new_infections: float
    timestep_records: list
    simulation_records: list | None = None


def test_summarize_pair_uses_reference_minus_intervention_infections():
    records_ref = [
        {
            "total_population": 100.0,
            "step_fraction": 0.25,
            "discount_factor_health": 1.0,
        }
    ]
    records_intv = [
        {
            "total_population": 104.0,
            "step_fraction": 0.25,
            "discount_factor_health": 0.8,
        }
    ]
    results = {
        "ref": FakeResult(1000.0, 100.0, records_ref, []),
        "intv": FakeResult(1012.0, 96.0, records_intv, []),
    }
    row = summarize_pair(
        {"name": "base", "arm_order": ["ref", "intv"]}, results
    )
    assert row["delta_qaly"] == 12.0
    assert row["infections_averted"] == 4.0
    assert row["qaly_per_averted_infection"] == 3.0
    assert row["delta_discounted_person_years"] == pytest.approx(-4.2)
    assert row["delta_undiscounted_person_years"] == 1.0


def test_simulation_delta_rows_reconciles_direction():
    results = {
        "ref": FakeResult(
            0.0,
            0.0,
            [],
            [{"step": 1, "year": 2025.0, "background_deaths": 10, "total_population": 100}],
        ),
        "intv": FakeResult(
            0.0,
            0.0,
            [],
            [{"step": 1, "year": 2025.0, "background_deaths": 8, "total_population": 102}],
        ),
    }
    rows = simulation_delta_rows(
        {"name": "base", "arm_order": ["ref", "intv"]}, results
    )
    assert rows[0]["delta_background_deaths"] == -2.0
    assert rows[0]["delta_total_population"] == 2.0
