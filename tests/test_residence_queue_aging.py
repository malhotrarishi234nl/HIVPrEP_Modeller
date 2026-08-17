"""
Executable specification for adult residence-time queue aging.

The production implementation is expected to add a queue-based aging method,
but final method names may still change. These tests therefore discover a
small set of likely private hooks and skip with an explicit assumption when no
hook is available yet.

Assumptions for the production implementation:
  - demography.aging_method accepts "residence_queue".
  - Adult age bins are 5 years wide and the default quarterly timestep uses
    20 residence phases per age bin.
  - Newly-entered adults are enqueued at residence phase 0.
  - A cohort exits an age bin only after completing all residence phases.
  - The oldest adult age bin accumulates and has no outflow.
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3


BASE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "v11_configuration.json",
)

ADULT_QUEUE_AGING_METHODS = (
    "_apply_residence_queue_aging_adults",
    "_apply_residence_time_aging_adults",
    "_apply_queue_aging_adults",
)

QUEUE_INIT_METHODS = (
    "_initialize_residence_aging_queues",
    "_initialize_adult_residence_queues",
    "_init_residence_queue_aging",
)

QUEUE_SYNC_METHODS = (
    "_sync_residence_aging_queues_from_population",
    "_sync_adult_residence_queues_from_population",
    "_seed_residence_aging_queues_from_population",
)


def make_residence_queue_engine(seed=42):
    with open(BASE_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    config["run_control"]["start_year"]["value"] = 1990
    config["run_control"]["end_year"]["value"] = 1990
    config["run_control"]["n_runs"]["value"] = 1
    config["run_control"]["deterministic_mode"]["value"] = True
    config["population"]["initial_hiv_prevalence"]["value"] = 0.0
    demography = config.setdefault("demography", {})
    if "aging_method" in demography:
        demography["aging_method"]["value"] = "residence_queue"
    else:
        advanced = demography.setdefault("advanced", {})
        advanced["aging_method"] = {"value": "residence_queue"}

    try:
        return SimulationEngineV3(config, seed=seed)
    except ValueError as exc:
        if "aging_method" in str(exc) or "residence_queue" in str(exc):
            pytest.skip(
                "production has not enabled demography.aging_method='residence_queue' yet"
            )
        raise


def find_bound_method(obj, names):
    for name in names:
        method = getattr(obj, name, None)
        if callable(method):
            return method
    return None


def residence_phase_count(engine):
    configured = getattr(engine, "adult_residence_queue_phases", None)
    if configured is not None:
        return int(configured)
    configured = getattr(engine, "residence_queue_phases", None)
    if configured is not None:
        return int(configured)
    return int(round(5.0 / engine.time.years_per_step))


def seed_new_entries(engine, age_idx, count):
    """
    Seed a single newly-entered adult cohort into one age bin.

    This intentionally requires an explicit queue sync/seed hook. Directly
    mutating engine.state.population is not enough for a residence queue unless
    the production implementation is told to enqueue those people at phase 0.
    """
    engine.state.population[:] = 0

    init = find_bound_method(engine, QUEUE_INIT_METHODS)
    if init is not None:
        init()

    engine.state.population[age_idx, 0, 0, 0, 0, 0, 0] = count

    sync = find_bound_method(engine, QUEUE_SYNC_METHODS)
    if sync is None:
        pytest.skip(
            "production residence queue needs a hook to seed/sync newly-entered "
            "population into phase 0"
        )
    sync()


def apply_adult_queue_aging(engine):
    method = find_bound_method(engine, ADULT_QUEUE_AGING_METHODS)
    if method is None:
        pytest.skip(
            "production adult residence-queue aging method name is not available yet"
        )
    method()


def test_new_adult_entries_wait_full_residence_time_before_aging_out():
    """
    A newly-entered adult cohort should remain in its current 5-year bin for
    all non-terminal residence phases. It should move to the next bin only when
    the final phase advances.
    """
    engine = make_residence_queue_engine()
    source_age = 2
    next_age = source_age + 1
    initial = 1_000
    phases = residence_phase_count(engine)

    seed_new_entries(engine, source_age, initial)

    for _ in range(phases - 1):
        apply_adult_queue_aging(engine)

    assert int(engine.state.population.sum()) == initial
    assert int(engine.state.population[source_age].sum()) == initial
    assert int(engine.state.population[next_age].sum()) == 0

    apply_adult_queue_aging(engine)

    assert int(engine.state.population.sum()) == initial
    assert int(engine.state.population[source_age].sum()) == 0
    assert int(engine.state.population[next_age].sum()) == initial


def test_residence_queue_terminal_bin_accumulates_without_outflow():
    """
    The oldest adult bin must keep all existing occupants while accepting
    completed residence-time outflow from the penultimate bin.
    """
    engine = make_residence_queue_engine()
    terminal = engine.groups.n_age_groups - 1
    penultimate = terminal - 1
    terminal_initial = 300
    penultimate_initial = 700
    phases = residence_phase_count(engine)

    seed_new_entries(engine, terminal, terminal_initial)

    # Add a second newly-entered cohort to the penultimate bin and resync both
    # cohorts into phase 0, matching the expected production queue contract.
    engine.state.population[penultimate, 0, 0, 0, 0, 0, 0] = penultimate_initial
    sync = find_bound_method(engine, QUEUE_SYNC_METHODS)
    if sync is None:
        pytest.skip(
            "production residence queue needs a hook to seed/sync newly-entered "
            "population into phase 0"
        )
    sync()

    for _ in range(phases):
        apply_adult_queue_aging(engine)

    assert int(engine.state.population.sum()) == terminal_initial + penultimate_initial
    assert int(engine.state.population[terminal].sum()) == terminal_initial + penultimate_initial
    assert int(engine.state.population[penultimate].sum()) == 0


def test_reference_residence_queue_differs_from_memoryless_fractional_leakage():
    """
    Guard the intended behavior independently of production hook names: over a
    single quarterly step, a phase-0 residence queue has no next-bin leakage,
    whereas the existing memoryless fractional operator leaks immediately.
    """
    count = 1_000
    quarters_per_bin = 20
    queue = np.zeros(quarters_per_bin, dtype=int)
    queue[0] = count

    next_queue = np.zeros_like(queue)
    next_queue[1:] = queue[:-1]
    queue_outflow = int(queue[-1])

    p_quarter = 1.0 - (1.0 - 1.0 / 5.0) ** 0.25
    memoryless_outflow = int(round(count * p_quarter))

    assert queue_outflow == 0
    assert int(next_queue.sum()) == count
    assert memoryless_outflow > 0
