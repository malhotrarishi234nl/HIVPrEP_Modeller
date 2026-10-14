"""Focused guards for A-9's opt-in process-switch grid runner."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from demography import DemographyModule
from run_v3 import SimulationEngineV3
from transmission_corrected import TransmissionEngine
from tools.a20_process_decomposition import ProcessModeSwitch
from tools.run_a9_switch_grid_process_decomposition import (
    SEEDS,
    SINGLE_PROCESS_ARMS,
    SWITCHES,
    increment_rows,
    monotone_non_decreasing,
    repair_verdict,
)
from tools.analyze_a9_switch_grid_step2 import (
    compartment_contrast,
    cumulative_pair,
    first_incorrect_crossover,
)


def _grid(values_by_seed):
    return pd.DataFrame(
        [
            {"seed": seed, "switch_percent": switch, "metric": value}
            for seed, values in values_by_seed.items()
            for switch, value in zip(SWITCHES, values)
        ]
    )


def test_design_is_pinned_to_brief():
    assert SEEDS == tuple(range(42, 57))
    assert SWITCHES == (10, 25, 50, 75, 100)
    assert set(SINGLE_PROCESS_ARMS) == {
        "transmission", "disease_progression", "diagnosis", "art",
        "hiv_mortality", "background_mortality", "births", "migration",
        "ageing_sexual_debut", "prep",
    }


def test_monotonicity_is_literal_non_decreasing():
    assert monotone_non_decreasing([1, 1, 2, 3, 3])
    assert not monotone_non_decreasing([1, 2, 1.999999, 4, 5])


def test_increment_statistics_are_within_seed_and_use_se():
    frame = _grid({42: [0, 2, 5, 9, 14], 43: [0, 4, 9, 15, 22]})
    rows = increment_rows(frame, arm="x", metric="metric")
    assert [row["mean_increment"] for row in rows] == pytest.approx([3, 4, 5, 6])
    assert rows[0]["se_increment"] == pytest.approx(1.0)
    assert rows[0]["increment_over_se"] == pytest.approx(3.0)


def test_monotone_grid_is_not_a_repair_when_unresolved():
    increments = [
        {"exceeds_2se": True, "increment_over_se": 3.0},
        {"exceeds_2se": False, "increment_over_se": 1.9},
        {"exceeds_2se": True, "increment_over_se": 4.0},
        {"exceeds_2se": True, "increment_over_se": 5.0},
    ]
    verdict = repair_verdict([0, 1, 2, 3, 4], increments)
    assert verdict["monotone_non_decreasing"] is True
    assert verdict["meets_repair_criterion"] is False
    assert verdict["minimum_increment_over_se"] == pytest.approx(1.9)


def test_repair_requires_monotonicity_and_all_four_increments_above_2se():
    increments = [
        {"exceeds_2se": True, "increment_over_se": value}
        for value in (2.1, 3.0, 4.0, 5.0)
    ]
    assert repair_verdict([0, 1, 2, 3, 4], increments)["meets_repair_criterion"]
    assert not repair_verdict([0, 2, 1, 3, 4], increments)["meets_repair_criterion"]


def test_zero_se_positive_increment_is_resolved():
    frame = _grid({seed: [0, 1, 2, 3, 4] for seed in SEEDS})
    rows = increment_rows(frame, arm="x", metric="metric")
    assert all(math.isinf(row["increment_over_se"]) for row in rows)
    assert all(row["exceeds_2se"] for row in rows)


def test_a20_context_restores_bindings_after_exception():
    originals = {
        (SimulationEngineV3, "_draw_events_for"): SimulationEngineV3._draw_events_for,
        (SimulationEngineV3, "_process_rng"): SimulationEngineV3._process_rng,
        (TransmissionEngine, "calculate_transmission"): TransmissionEngine.calculate_transmission,
        (DemographyModule, "calculate_births"): DemographyModule.calculate_births,
    }
    with pytest.raises(RuntimeError, match="adversarial"):
        with ProcessModeSwitch({"transmission", "births"}):
            raise RuntimeError("adversarial")
    for (owner, name), original in originals.items():
        assert getattr(owner, name) is original


def test_step2_finds_first_strictly_negative_cumulative_crossover():
    frame = pd.DataFrame(
        {
            "step": [1, 1, 2, 2, 3, 3],
            "year": [2025.0, 2025.0, 2025.25, 2025.25, 2025.5, 2025.5],
            "switch_percent": [10, 25] * 3,
            "infections_averted": [0, 0, 1, 1, 1, 0],
        }
    )
    path = cumulative_pair(frame, 10, 25)
    assert first_incorrect_crossover(path) == {
        "step": 3, "year": 2025.5, "high_minus_low": -1.0
    }


def test_step2_compartment_contrast_is_high_switch_minus_low_switch():
    frame = pd.DataFrame(
        {
            "step": [7, 7], "year": [2030.0, 2030.0],
            "switch_percent": [10, 25],
            "delta_adult_hiv_pos": [-28, -13],
            "delta_new_infections": [-1, 0],
        }
    )
    result = compartment_contrast(frame, step=7, year=2030.0, low=10, high=25)
    by_component = result.set_index("component")
    assert by_component.loc["adult_hiv_pos", "high_minus_low"] == pytest.approx(15)
    assert by_component.loc["new_infections", "high_minus_low"] == pytest.approx(1)
