import numpy as np
import pandas as pd

from stochastic_allocation import deterministic_allocate_events
from tools.run_a13_hiv_mortality_trace import (
    A13TraceEngine,
    ALL_TRACE_SLUGS,
    MORTALITY_SLUGS,
    TRACE_FIELDS,
    TRACE_PROCESSES,
    paired_trace,
)


def test_aggregate_allocator_can_preserve_total_while_erasing_a_stage():
    population = np.zeros((1, 1, 1, 3, 10), dtype=np.int64)
    population[..., 1, :] = 1  # Chronic: expected 1 event.
    population[..., 2, :] = 1  # AIDS: expected 1 event.

    events = deterministic_allocate_events(population, 0.1)

    assert int(events.sum()) == 2
    assert int(events[..., 1, :].sum()) == 2
    assert int(events[..., 2, :].sum()) == 0


def test_trace_engine_records_pool_expectation_actual_and_error():
    engine = A13TraceEngine.__new__(A13TraceEngine)
    engine._a13_step_trace = {}
    engine.deterministic = True
    engine._draw_events = lambda n, p: np.array([1, 2], dtype=np.int64)

    events = engine._draw_events_for(
        ("hiv_mortality", "AIDS", "off_art"),
        np.array([10, 20], dtype=np.int64),
        0.1,
    )

    np.testing.assert_array_equal(events, [1, 2])
    assert engine._a13_step_trace["mortality_aids_off_art"] == {
        "pool": 30,
        "probability": 0.1,
        "expected": 3.0,
        "actual": 3,
        "error": 0.0,
    }


def test_paired_trace_reconciles_total_mortality_calls_and_cumulative_benefit():
    rows = []
    for arm, multiplier in [("no_prep", 2.0), ("oral_prep", 1.0)]:
        for step in [100, 101]:
            row = {
                "mode": "deterministic",
                "seed": 42,
                "arm": arm,
                "step": step,
                "year": 2025.0 + (step - 100) * 0.25,
            }
            for column in (
                "new_infections",
                "new_aids_cases",
                "hiv_deaths",
                "aids_deaths",
                "new_diagnoses",
                "art_initiations",
                "art_initiations_aids",
                "aids_art_initiations",
                "same_step_aids_art_rescue_proxy",
            ):
                row[column] = multiplier
            for column in (
                "aids_stock_start",
                "aids_off_art_start",
                "aids_on_art_start",
                "aids_undiagnosed_start",
                "aids_diagnosed_start",
                "aids_stock_pre_mortality",
                "aids_off_art_pre_mortality",
                "aids_on_art_pre_mortality",
                "aids_undiagnosed_pre_mortality",
                "aids_diagnosed_pre_mortality",
            ):
                row[column] = multiplier
            for slug in ALL_TRACE_SLUGS:
                for field in TRACE_FIELDS:
                    row[f"a13_{slug}_{field}"] = (
                        0.1 if field == "probability" else multiplier
                    )
            rows.append(row)

    paired = paired_trace(pd.DataFrame(rows))
    assert paired["hiv_deaths_averted"].tolist() == [1.0, 1.0]
    assert paired["cumulative_hiv_deaths_averted"].tolist() == [1.0, 2.0]
    assert paired["a13_mortality_total_actual_averted"].tolist() == [
        float(len(MORTALITY_SLUGS)),
        float(len(MORTALITY_SLUGS)),
    ]
