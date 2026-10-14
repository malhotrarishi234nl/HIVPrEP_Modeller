import numpy as np
import pandas as pd

import demography
from demography import DemographyModule
from stochastic_allocation import deterministic_allocate_events
from tools.run_a21_rare_stratum_starvation import (
    A21TraceEngine,
    BackgroundMortalityTrace,
    _aggregate_background,
    _aggregate_hiv_mortality_stage_art,
    parse_junit,
    summarise_mortality_call,
    summarise_status_block,
)


def test_status_block_exposes_exact_hiv_relocation():
    population = np.zeros((1, 2, 1, 1, 1), dtype=np.int64)
    population[0, 0, 0, 0, 0] = 995
    population[0, 1, 0, 0, 0] = 5
    events = deterministic_allocate_events(population, 0.01)
    positive = np.broadcast_to(
        np.array([False, True])[None, :, None, None, None], population.shape
    )

    summary = summarise_status_block(population, 0.01, events, positive)

    assert summary["allocated"] == summary["rounded_expected_target"] == 10
    assert summary["control_error"] == 0
    assert summary["hiv_positive_expected"] == 0.05
    assert summary["hiv_positive_allocated"] == 0
    assert summary["hiv_positive_shortfall"] == 0.05
    assert abs(summary["hiv_negative_excess"] - 0.05) < 1e-12
    assert abs(summary["relocation_balance_residual"]) < 1e-12


def test_status_block_reports_occupied_subunit_and_starved_shares():
    population = np.zeros((1, 3, 1, 1, 1), dtype=np.int64)
    population[0, 1, 0, 0, 0] = 2
    population[0, 2, 0, 0, 0] = 20
    events = np.zeros_like(population)
    positive = np.broadcast_to(
        np.array([False, True, True])[None, :, None, None, None], population.shape
    )

    summary = summarise_status_block(population, 0.1, events, positive)

    assert summary["hiv_positive_occupied_cells"] == 2
    assert summary["hiv_positive_subunit_cells"] == 1
    assert summary["hiv_positive_subunit_share_occupied"] == 0.5
    assert summary["hiv_positive_starved_share_occupied"] == 1.0


def test_hiv_mortality_call_separates_subunit_from_large_cells():
    population = np.array([1, 5, 20], dtype=np.int64)
    events = deterministic_allocate_events(population, 0.1)

    summary = summarise_mortality_call(population, 0.1, events)

    assert summary["rounded_expected_target"] == 3
    assert summary["allocated"] == 3
    assert summary["subunit_cells"] == 2
    assert summary["large_expected"] == 2.0
    assert summary["large_allocated"] == 2


def test_background_annual_aggregation_recomputes_ratios():
    rows = pd.DataFrame(
        [
            {
                "calendar_year": 1975,
                "population": 100,
                "expected": 10.4,
                "allocated": 10,
                "rounded_expected_target": 10,
                "control_error": 0,
                "rounding_error": -0.4,
                "hiv_positive_population": 2,
                "hiv_positive_expected": 0.2,
                "hiv_positive_allocated": 0,
                "hiv_positive_shortfall": 0.2,
                "hiv_negative_population": 98,
                "hiv_negative_expected": 10.2,
                "hiv_negative_allocated": 10,
                "hiv_negative_excess": -0.2,
                "relocation_balance_residual": 0,
                "hiv_positive_cells": 3,
                "hiv_positive_occupied_cells": 2,
                "hiv_positive_subunit_cells": 2,
                "hiv_positive_starved_cells": 2,
            },
            {
                "calendar_year": 1975,
                "population": 200,
                "expected": 20.6,
                "allocated": 21,
                "rounded_expected_target": 21,
                "control_error": 0,
                "rounding_error": 0.4,
                "hiv_positive_population": 4,
                "hiv_positive_expected": 0.8,
                "hiv_positive_allocated": 0,
                "hiv_positive_shortfall": 0.8,
                "hiv_negative_population": 196,
                "hiv_negative_expected": 19.8,
                "hiv_negative_allocated": 21,
                "hiv_negative_excess": 1.2,
                "relocation_balance_residual": 0,
                "hiv_positive_cells": 3,
                "hiv_positive_occupied_cells": 2,
                "hiv_positive_subunit_cells": 1,
                "hiv_positive_starved_cells": 1,
            },
        ]
    )

    annual = _aggregate_background(rows).iloc[0]

    assert annual["hiv_positive_expected"] == 1.0
    assert annual["hiv_positive_shortfall_fraction"] == 1.0
    assert annual["hiv_positive_subunit_share_occupied"] == 0.75
    assert annual["hiv_positive_starved_share_occupied"] == 0.75


def test_hiv_mortality_stage_art_summary_preserves_signs():
    calls = pd.DataFrame(
        [
            {
                "hiv_stage": "AIDS",
                "art": "off_art",
                "expected": 5.4,
                "allocated": 5,
                "allocation_error": -0.4,
                "subunit_expected": 1.4,
                "subunit_allocated": 1,
                "subunit_shortfall": 0.4,
                "large_expected": 4.0,
                "large_allocated": 4,
                "large_excess": 0.0,
            },
            {
                "hiv_stage": "AIDS",
                "art": "off_art",
                "expected": 4.6,
                "allocated": 5,
                "allocation_error": 0.4,
                "subunit_expected": 0.6,
                "subunit_allocated": 1,
                "subunit_shortfall": -0.4,
                "large_expected": 4.0,
                "large_allocated": 4,
                "large_excess": 0.0,
            },
        ]
    )

    summary = _aggregate_hiv_mortality_stage_art(calls).iloc[0]

    assert summary["expected"] == 10.0
    assert summary["allocated"] == 10
    assert abs(summary["allocation_error"]) < 1e-12
    assert summary["allocation_ratio"] == 1.0


def test_trace_engine_observes_hiv_mortality_after_production_draw():
    class Recorder:
        def __init__(self):
            self.calls = []

        def record_hiv_mortality(self, **kwargs):
            self.calls.append(kwargs)

    engine = A21TraceEngine.__new__(A21TraceEngine)
    engine._a21_recorder = Recorder()
    engine.deterministic = True
    engine._current_step = 4
    engine._current_year = 1976.0
    engine._draw_events = lambda n, p: np.array([1, 2], dtype=np.int64)

    result = engine._draw_events_for(
        ("hiv_mortality", "AIDS", "off_art"),
        np.array([10, 20], dtype=np.int64),
        0.1,
    )

    np.testing.assert_array_equal(result, [1, 2])
    call = engine._a21_recorder.calls[0]
    assert call["stage"] == "AIDS"
    assert call["art"] == "off_art"
    np.testing.assert_array_equal(call["allocated"], result)


def test_trace_context_restores_bindings_after_exception(tmp_path):
    class Recorder:
        pass

    original_allocator = demography.deterministic_allocate_events
    original_method = DemographyModule._calculate_life_table_mortality

    try:
        with BackgroundMortalityTrace(Recorder()):
            assert demography.deterministic_allocate_events is not original_allocator
            assert DemographyModule._calculate_life_table_mortality is not original_method
            raise RuntimeError("expected")
    except RuntimeError as exc:
        assert str(exc) == "expected"

    assert demography.deterministic_allocate_events is original_allocator
    assert DemographyModule._calculate_life_table_mortality is original_method


def test_parse_junit_reports_exact_problematic_ids(tmp_path):
    path = tmp_path / "suite.xml"
    path.write_text(
        '<testsuite tests="3" failures="1" errors="1" skipped="0">'
        '<testcase classname="a" name="ok" />'
        '<testcase classname="a" name="bad"><failure /></testcase>'
        '<testcase classname="b" name="broken"><error /></testcase>'
        "</testsuite>"
    )

    result = parse_junit(path)

    assert result["passed"] == 1
    assert result["problematic"] == ["error:b::broken", "failure:a::bad"]
