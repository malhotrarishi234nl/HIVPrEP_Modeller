import numpy as np
import pandas as pd
import pytest

from counter_based_rng import CounterBasedRNGFactory
from run_v3 import SimulationEngineV3
from tools.run_a4_common_random_numbers import _summary, parse_seeds


def _bare_engine(*, deterministic: bool, seed: int = 42):
    engine = SimulationEngineV3.__new__(SimulationEngineV3)
    engine.seed = seed
    engine.rng = np.random.RandomState(seed)
    engine._crn_factory = CounterBasedRNGFactory(seed)
    engine._current_step = 200
    engine._current_year = 2025.0
    engine.deterministic = deterministic
    return engine


def test_named_event_processes_are_independent_of_draw_order():
    engine = _bare_engine(deterministic=False)
    population = np.full((3, 4, 2), 100, dtype=np.int64)
    transmission_first = engine._draw_events_for("transmission-test", population, 0.1)
    engine._draw_events_for("unrelated-process", population, 0.9)
    transmission_after = engine._draw_events_for("transmission-test", population, 0.1)
    np.testing.assert_array_equal(transmission_first, transmission_after)


@pytest.mark.parametrize(
    "process",
    ["transmission", "hiv_mortality", "diagnosis", "childhood_background_mortality"],
)
def test_same_process_and_shared_tensor_coordinates_align_across_prep_expansion(process):
    """Per-cell keying: expanding the PrEP axis must not disturb the shared slice.

    A-28 note. This test previously used ``adult_background_mortality``. That process
    no longer draws per cell -- it is the one process A-28 §3.2 moved to a group-level
    coupling, precisely because per-cell keying re-randomises everybody the intervention
    relocates between cells. The property asserted here is unchanged and still holds for
    every process that still keys per cell; the new contract for background mortality is
    asserted in the two tests below.
    """
    no_prep = np.full((2, 3, 1), 100, dtype=np.int64)
    oral_prep = np.full((2, 3, 2), 100, dtype=np.int64)
    no_prep_draw = _bare_engine(deterministic=False)._draw_events_for(
        process, no_prep, 0.2
    )
    oral_draw = _bare_engine(deterministic=False)._draw_events_for(
        process, oral_prep, 0.2
    )
    np.testing.assert_array_equal(no_prep_draw[..., 0], oral_draw[..., 0])


def test_background_mortality_total_is_coupled_when_people_are_merely_redistributed():
    """A-28 §3.2: the contract that replaced per-cell alignment for this one process.

    What the intervention actually does is move people between PrEP slices without
    changing anything about them. Under the group-level coupling the death total for the
    group is drawn once, so redistributing the same people over an expanded PrEP axis
    leaves that total exactly unchanged -- which per-cell keying did not deliver.
    """
    no_prep = np.full((2, 3, 1), 100, dtype=np.int64)
    redistributed = np.zeros((2, 3, 2), dtype=np.int64)
    redistributed[..., 0] = 50
    redistributed[..., 1] = 50
    assert no_prep.sum() == redistributed.sum()

    no_prep_draw = _bare_engine(deterministic=False)._draw_events_for(
        "adult_background_mortality", no_prep, 0.2
    )
    redistributed_draw = _bare_engine(deterministic=False)._draw_events_for(
        "adult_background_mortality", redistributed, 0.2
    )
    assert int(no_prep_draw.sum()) == int(redistributed_draw.sum())


def test_background_mortality_shared_slice_moves_when_the_group_itself_changes():
    """The deliberate cost of the A-28 §3.2 coupling, recorded so it is not a surprise.

    Group-level coupling ties the *total*, so it cannot also hold every shared cell's
    stream fixed when the group's population genuinely changes. Doubling the population
    across an expanded PrEP axis is a real change in the group, not a relocation, and the
    shared slice moves. Nothing the intervention does to this model has that shape.
    """
    no_prep = np.full((2, 3, 1), 100, dtype=np.int64)
    doubled = np.full((2, 3, 2), 100, dtype=np.int64)
    assert doubled.sum() == 2 * no_prep.sum()

    no_prep_draw = _bare_engine(deterministic=False)._draw_events_for(
        "adult_background_mortality", no_prep, 0.2
    )
    doubled_draw = _bare_engine(deterministic=False)._draw_events_for(
        "adult_background_mortality", doubled, 0.2
    )
    assert not np.array_equal(no_prep_draw[..., 0], doubled_draw[..., 0])
    # Still a valid draw from the larger group.
    assert int(doubled_draw.sum()) > int(no_prep_draw.sum())
    assert (doubled_draw <= doubled).all()


def test_deterministic_event_path_retains_legacy_two_argument_contract(monkeypatch):
    engine = _bare_engine(deterministic=True)
    expected = np.array([7, 8], dtype=np.int64)
    called = []

    def legacy_draw(population, probability):
        called.append((population.copy(), probability))
        return expected.copy()

    monkeypatch.setattr(engine, "_draw_events", legacy_draw)
    actual = engine._draw_events_for("ignored-in-deterministic-mode", np.array([1, 2]), 0.3)
    np.testing.assert_array_equal(actual, expected)
    assert len(called) == 1
    assert called[0][1] == 0.3


def test_absolute_timestep_not_local_arm_draw_history_selects_stream():
    first = _bare_engine(deterministic=False)
    second = _bare_engine(deterministic=False)
    second._current_step = 201
    population = np.full(128, 100, dtype=np.int64)
    draw_a = first._draw_events_for("diagnosis", population, 0.2)
    draw_b = second._draw_events_for("diagnosis", population, 0.2)
    assert not np.array_equal(draw_a, draw_b)


def test_seed_parser_supports_ranges_and_rejects_duplicates():
    assert parse_seeds("42-44,50") == [42, 43, 44, 50]
    with pytest.raises(Exception, match="duplicate"):
        parse_seeds("42,42")


def test_summary_uses_unbiased_across_seed_variance():
    rows = []
    for seed, delta_qaly in [(1, 1.0), (2, 3.0), (3, 5.0)]:
        row = {
            "seed": seed,
            "delta_qaly": delta_qaly,
            "infections_averted": seed,
            "delta_discounted_person_years": delta_qaly,
            "delta_undiscounted_person_years": delta_qaly,
            "delta_background_deaths": seed,
            "delta_hiv_deaths": -seed,
            "delta_childhood_deaths": 0,
            "delta_births": 0,
            "delta_sexual_debuts": 0,
            "delta_net_migration": 0,
            "delta_final_population": seed,
            "elapsed_seconds": float(seed),
        }
        rows.append(row)
    summary = _summary(pd.DataFrame(rows))
    assert summary["n"] == 3
    assert summary["metrics"]["delta_qaly"]["mean"] == 3.0
    assert summary["metrics"]["delta_qaly"]["variance"] == 4.0
    assert summary["metrics"]["delta_qaly"]["sd"] == 2.0
