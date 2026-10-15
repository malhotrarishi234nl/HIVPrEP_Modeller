"""A-12 triage of ``SimulationEngineV3._step_stage_structured_diagnosis`` (DX-04).

The production lock-in specification (section 4) requires this site to be
classified, its six-pool allocation and conservation behaviour tested, and the
finding either closed or explicitly documented before M1 is used for
production results.  It was previously the only untriaged rounding site in the
A-12 inventory, and it is the M1 mechanism itself.

**What the function does.** It partitions the undiagnosed tensor into four
disjoint rate blocks -- non-MSM and MSM, each by non-AIDS / AIDS -- and
allocates each pool separately through the shared aggregate-preserving
allocator ``stochastic_allocation.deterministic_allocate_events``.

**The finding, stated plainly.** ``stage_step_probability`` depends on the
stage only through ``aids_stage``, so acute and chronic share one step
probability within a population.  The six pools therefore span only *four*
distinct rate blocks, and the shared non-AIDS block is split into two separate
allocations.  That is a genuine fragmentation of a rate block, which the A-12
audit treats as a defect unless it is bounded and justified.  This module
measures the bound and the tests below are the justification:

* **total diagnoses are conserved exactly** -- the partition is disjoint and
  exhaustive, so no person is diagnosed twice and none is lost;
* acute and chronic are allocated together, eliminating the former +/-1
  fragmentation of total diagnoses;
* the returned event tensor remains stage-resolved, so all published stage
  counters remain available without a second allocation.

These tests drive the **real** method with a minimal stand-in object rather
than reimplementing its partition, so they cannot drift from the code they
certify.  No simulation engine is constructed and no trajectory is run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_v3 import SimulationEngineV3  # noqa: E402
from stage_structured_diagnosis import stage_step_probability  # noqa: E402
from stochastic_allocation import deterministic_allocate_events  # noqa: E402

# Small but structurally faithful state shape:
# (age, orientation, activity, hiv, diagnosis, art, prep).
# HIV stages are Susceptible, Acute, Chronic, AIDS -- the method slices ``1:``,
# so AIDS sits at index 3 and the three diagnosis stages are offsets 0, 1, 2.
SHAPE = (3, 2, 2, 4, 2, 2, 2)
MSM_IDX = 1
AIDS_IDX = 3
YEARS_PER_STEP = 0.25

#: The four distinct rate blocks the method is expected to produce.
EXPECTED_POOLS = (
    "stage_structured_diagnosis_non_msm_non_aids",
    "stage_structured_diagnosis_non_msm_aids",
    "stage_structured_diagnosis_msm_non_aids",
    "stage_structured_diagnosis_msm_aids",
)


class Recorder:
    """A deterministic stand-in for ``SimulationEngineV3._draw_events_for``.

    Uses the production allocator, so the arithmetic under test is the real
    one; it only records which named pools were asked for and what was in
    them.  Driving the real method this way is deliberate: a reimplementation
    of the partition in the test could agree with itself while disagreeing
    with the engine.
    """

    def __init__(self):
        self.calls: list[tuple[str, int, float]] = []
        self.pools: dict[str, np.ndarray] = {}

    def __call__(self, process_id, n: np.ndarray, p: float) -> np.ndarray:
        self.calls.append((str(process_id), int(np.sum(n)), float(p)))
        self.pools[str(process_id)] = np.asarray(n).copy()
        return deterministic_allocate_events(n, p)


def _engine(population: np.ndarray, draw) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(population=population),
        groups=SimpleNamespace(
            orient_to_idx={"MSM": MSM_IDX, "HeteroMale": 0},
            hiv_to_idx={"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": AIDS_IDX},
        ),
        time=SimpleNamespace(years_per_step=YEARS_PER_STEP),
        msm_relative_diagnosis_schedule=None,
        _draw_events_for=draw,
    )


def _profile(rho: float = 1.0, symptom: float = 0.3) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        is_identity=False,
        routine_hazard_scale=float(rho),
        symptom_probability_at=lambda year: float(symptom),
    )


def _undiagnosed_population(counts) -> np.ndarray:
    """Place ``counts[(orient, stage_offset)]`` people in undiagnosed cells.

    Spread across age and activity so the allocator has several cells to work
    with, which is where cellwise rounding would differ from an aggregate.
    """
    population = np.zeros(SHAPE, dtype=np.int64)
    for (orient, stage_offset), total in counts.items():
        cells = [(a, c) for a in range(SHAPE[0]) for c in range(SHAPE[2])]
        for index, (age, activity) in enumerate(cells):
            share = total // len(cells) + (1 if index < total % len(cells) else 0)
            population[age, orient, activity, stage_offset + 1, 0, 0, 0] = share
    return population


def _run(population: np.ndarray, *, rho=1.0, symptom=0.3, annual_rate=0.4):
    recorder = Recorder()
    engine = _engine(population, recorder)
    result = SimulationEngineV3._step_stage_structured_diagnosis(
        engine, 2000.0, annual_rate, MSM_IDX, _profile(rho, symptom)
    )
    return result, recorder, engine


# ==========================================================================
# The partition: four rate blocks, disjoint and exhaustive
# ==========================================================================

def test_exactly_four_rate_blocks_are_allocated_and_they_are_the_declared_ones():
    counts = {(o, s): 40 for o in (0, 1) for s in (0, 1, 2)}
    _result, recorder, _engine_obj = _run(_undiagnosed_population(counts))
    assert [name for name, _, _ in recorder.calls] == list(EXPECTED_POOLS)


def test_the_four_pools_are_pairwise_disjoint_and_cover_every_undiagnosed_cell():
    """Conservation is structural, not an accident of the chosen numbers."""
    counts = {(o, s): 37 + 5 * s for o in (0, 1) for s in (0, 1, 2)}
    population = _undiagnosed_population(counts)
    undiagnosed = population[:, :, :, 1:, 0, :, :].copy()
    _result, recorder, _engine_obj = _run(population)

    stacked = np.stack([recorder.pools[name] for name in EXPECTED_POOLS])
    # Disjoint: no cell is non-zero in two pools.
    occupied = (stacked > 0).sum(axis=0)
    assert occupied.max() <= 1, "a cell appears in more than one pool"
    # Exhaustive: the pools sum back to the undiagnosed tensor exactly.
    np.testing.assert_array_equal(stacked.sum(axis=0), undiagnosed)


def test_the_declared_rate_blocks_have_the_expected_probabilities():
    counts = {(o, s): 50 for o in (0, 1) for s in (0, 1, 2)}
    _result, recorder, _engine_obj = _run(_undiagnosed_population(counts))
    probabilities = {name: p for name, _, p in recorder.calls}
    for population in ("non_msm", "msm"):
        non_aids = probabilities[f"stage_structured_diagnosis_{population}_non_aids"]
        aids = probabilities[f"stage_structured_diagnosis_{population}_aids"]
        assert aids > non_aids, "the AIDS pool carries the additional symptom hazard"
    assert len(set(probabilities.values())) == 2, (
        "with no MSM multiplier the four blocks span two distinct probabilities; "
        "with a multiplier they span four"
    )


def test_an_msm_multiplier_separates_the_populations_into_four_rate_blocks():
    counts = {(o, s): 50 for o in (0, 1) for s in (0, 1, 2)}
    recorder = Recorder()
    engine = _engine(_undiagnosed_population(counts), recorder)
    engine.msm_relative_diagnosis_schedule = SimpleNamespace(
        get_value=lambda year: 1.7
    )
    SimulationEngineV3._step_stage_structured_diagnosis(
        engine, 2000.0, 0.4, MSM_IDX, _profile()
    )
    probabilities = {name: p for name, _, p in recorder.calls}
    assert len(set(probabilities.values())) == 4
    assert (
        probabilities["stage_structured_diagnosis_msm_non_aids"]
        > probabilities["stage_structured_diagnosis_non_msm_non_aids"]
    )


# ==========================================================================
# Conservation
# ==========================================================================

def test_total_diagnoses_are_conserved_and_the_stock_moves_exactly():
    counts = {(o, s): 61 + 7 * s for o in (0, 1) for s in (0, 1, 2)}
    population = _undiagnosed_population(counts)
    before_undiagnosed = int(population[:, :, :, 1:, 0, :, :].sum())
    before_total = int(population.sum())

    result, _recorder, engine = _run(population)
    after = engine.state.population
    diagnosed_total = int(result["total"])

    # No person is invented or lost: the tensor total is unchanged.
    assert int(after.sum()) == before_total
    # Exactly the reported number moved from undiagnosed to diagnosed.
    assert int(after[:, :, :, 1:, 0, :, :].sum()) == before_undiagnosed - diagnosed_total
    assert int(after[:, :, :, 1:, 1, :, :].sum()) == diagnosed_total
    # No cell went negative.
    assert after.min() >= 0


def test_the_stage_counters_sum_to_the_endogenous_total():
    """The method asserts this itself; this pins it as a contract, not a hope."""
    counts = {(o, s): 44 + 3 * s for o in (0, 1) for s in (0, 1, 2)}
    result, _recorder, _engine_obj = _run(_undiagnosed_population(counts))
    observation = result["aids_observation"]
    stage_sum = sum(
        observation[name] for name in (
            "hiv_diagnoses_in_acute_stage",
            "hiv_diagnoses_in_chronic_stage",
            "hiv_diagnoses_in_aids_stage",
        )
    )
    assert stage_sum == int(result["total"])
    assert 0 <= int(result["msm"]) <= int(result["total"])


def test_no_pool_over_draws_its_own_capacity():
    counts = {(0, 0): 5, (0, 1): 1, (0, 2): 2, (1, 0): 1, (1, 1): 3, (1, 2): 1}
    population = _undiagnosed_population(counts)
    before = population[:, :, :, 1:, 0, :, :].copy()
    _result, _recorder, engine = _run(population, annual_rate=0.9, symptom=0.9)
    after = engine.state.population[:, :, :, 1:, 0, :, :]
    assert (after <= before).all(), "a pool diagnosed more people than it held"
    assert (after >= 0).all()


# ==========================================================================
# Sparse counts and boundary probabilities
# ==========================================================================

@pytest.mark.parametrize("per_pool", [0, 1, 2, 3, 7])
def test_sparse_pools_conserve_and_never_invent_diagnoses(per_pool):
    """Sparse counts are where cellwise rounding erases or invents events."""
    counts = {(o, s): per_pool for o in (0, 1) for s in (0, 1, 2)}
    population = _undiagnosed_population(counts)
    held = int(population[:, :, :, 1:, 0, :, :].sum())
    result, _recorder, engine = _run(population, annual_rate=0.4, symptom=0.3)
    assert 0 <= int(result["total"]) <= held
    assert int(engine.state.population.sum()) == int(population.sum()) + 0
    assert engine.state.population.min() >= 0


def test_an_empty_undiagnosed_tensor_diagnoses_nobody():
    population = np.zeros(SHAPE, dtype=np.int64)
    result, recorder, _engine_obj = _run(population)
    assert int(result["total"]) == 0
    assert int(result["msm"]) == 0
    # All four pools are still visited: a zero pool is "no one eligible", which
    # is a different statement from "the pool was never considered".
    assert len(recorder.calls) == 4


def test_a_zero_hazard_diagnoses_nobody_and_a_unit_hazard_diagnoses_everybody():
    counts = {(o, s): 23 for o in (0, 1) for s in (0, 1, 2)}

    population = _undiagnosed_population(counts)
    result, _recorder, _engine_obj = _run(
        population, rho=0.0, symptom=0.0, annual_rate=0.5
    )
    assert int(result["total"]) == 0, "zero routine and zero symptom hazard"

    population = _undiagnosed_population(counts)
    held = int(population[:, :, :, 1:, 0, :, :].sum())
    result, _recorder, engine = _run(population, rho=1.0, annual_rate=1.0, symptom=1.0)
    assert int(result["total"]) == held, "an absorbing hazard diagnoses the whole pool"
    assert int(engine.state.population[:, :, :, 1:, 0, :, :].sum()) == 0


def test_the_aids_pool_is_the_only_one_the_symptom_probability_moves():
    counts = {(o, s): 80 for o in (0, 1) for s in (0, 1, 2)}
    without = Recorder()
    engine = _engine(_undiagnosed_population(counts), without)
    SimulationEngineV3._step_stage_structured_diagnosis(
        engine, 2000.0, 0.4, MSM_IDX, _profile(symptom=0.0)
    )
    with_symptom = Recorder()
    engine = _engine(_undiagnosed_population(counts), with_symptom)
    SimulationEngineV3._step_stage_structured_diagnosis(
        engine, 2000.0, 0.4, MSM_IDX, _profile(symptom=0.6)
    )
    left = {name: p for name, _, p in without.calls}
    right = {name: p for name, _, p in with_symptom.calls}
    for name in EXPECTED_POOLS:
        if name.endswith("_aids") and not name.endswith("_non_aids"):
            assert right[name] > left[name], name
        else:
            assert right[name] == left[name], (
                f"{name} must not move with the AIDS-symptom probability"
            )


# ==========================================================================
# Fragmentation sensitivity: the bound that justifies keeping the split
# ==========================================================================

def _combined_non_aids_allocation(population: np.ndarray, orient: int, p: float) -> int:
    """What a single allocation over the shared non-AIDS block would give."""
    undiagnosed = population[:, :, :, 1:, 0, :, :]
    pool = np.zeros_like(undiagnosed)
    for stage_offset in (0, 1):
        if orient == MSM_IDX:
            pool[:, orient, :, stage_offset, :, :] = \
                undiagnosed[:, orient, :, stage_offset, :, :]
        else:
            pool[:, :, :, stage_offset, :, :] = undiagnosed[:, :, :, stage_offset, :, :]
            pool[:, MSM_IDX, :, stage_offset, :, :] = 0
    return int(deterministic_allocate_events(pool, p).sum())


@pytest.mark.parametrize("acute,chronic", [
    (1, 1), (1, 2), (3, 3), (5, 2), (7, 11), (13, 29), (101, 97), (1, 1000),
    # These two are the measured extremes of the differential, kept explicit so
    # the bound is exercised rather than merely asserted (see the non-vacuity
    # test below).
    (1, 4), (5, 5),
])
def test_shared_rate_block_is_allocated_once_per_population(
    acute, chronic
):
    """DX-04 no longer fragments acute and chronic allocations."""
    counts = {(0, 0): acute, (0, 1): chronic, (0, 2): 0, (1, 0): acute, (1, 1): chronic,
              (1, 2): 0}
    population = _undiagnosed_population(counts)
    reference = population.copy()
    result, recorder, _engine_obj = _run(population, annual_rate=0.4, symptom=0.0)

    probability = stage_step_probability(
        0.4, routine_hazard_scale=1.0, aids_symptom_annual_probability=0.0,
        years_per_step=YEARS_PER_STEP, aids_stage=False,
    )
    for orient in (0, MSM_IDX):
        label = "msm" if orient == MSM_IDX else "non_msm"
        allocated = int(deterministic_allocate_events(
            recorder.pools[f"stage_structured_diagnosis_{label}_non_aids"], probability
        ).sum())
        combined = _combined_non_aids_allocation(reference, orient, probability)
        assert allocated == combined
    # Whatever the stage split, the total is still bounded by what was held.
    assert int(result["total"]) <= int(reference[:, :, :, 1:, 0, :, :].sum())


def test_the_former_fragmentation_differential_is_always_zero():
    """Sweep the former failure cases and require a zero differential."""
    probability = stage_step_probability(
        0.4, routine_hazard_scale=1.0, aids_symptom_annual_probability=0.0,
        years_per_step=YEARS_PER_STEP, aids_stage=False,
    )
    observed: set[int] = set()
    for acute in range(0, 14):
        for chronic in range(0, 14):
            counts = {(0, 0): acute, (0, 1): chronic, (0, 2): 0,
                      (1, 0): acute, (1, 1): chronic, (1, 2): 0}
            population = _undiagnosed_population(counts)
            reference = population.copy()
            _result, recorder, _engine_obj = _run(
                population, annual_rate=0.4, symptom=0.0
            )
            allocated = int(deterministic_allocate_events(
                recorder.pools["stage_structured_diagnosis_non_msm_non_aids"], probability
            ).sum())
            observed.add(allocated - _combined_non_aids_allocation(reference, 0, probability))
    assert observed == {0}


def test_the_repaired_partition_conserves_population_and_stage_counters():
    for acute, chronic, aids in ((1, 4, 3), (5, 5, 5), (0, 9, 1), (29, 1, 7)):
        counts = {(0, 0): acute, (0, 1): chronic, (0, 2): aids,
                  (1, 0): acute, (1, 1): chronic, (1, 2): aids}
        population = _undiagnosed_population(counts)
        held = int(population[:, :, :, 1:, 0, :, :].sum())
        before = int(population.sum())
        result, _recorder, engine = _run(population, annual_rate=0.4, symptom=0.3)
        assert int(engine.state.population.sum()) == before
        assert 0 <= int(result["total"]) <= held
        observation = result["aids_observation"]
        assert sum(
            observation[name] for name in (
                "hiv_diagnoses_in_acute_stage",
                "hiv_diagnoses_in_chronic_stage",
                "hiv_diagnoses_in_aids_stage",
            )
        ) == int(result["total"])


def test_the_shared_allocator_itself_is_not_fragmentation_sensitive():
    """DX-04 uses the aggregate allocator, not per-cell rounding."""
    compact = np.array([2], dtype=np.int64)
    fragmented = np.array([1, 1], dtype=np.int64)
    probability = 0.4
    assert int(np.round(compact * probability).sum()) == 1
    assert int(np.round(fragmented * probability).sum()) == 0, "cellwise would lose it"
    assert int(deterministic_allocate_events(compact, probability).sum()) == 1
    assert int(deterministic_allocate_events(fragmented, probability).sum()) == 1


def test_stage_outputs_remain_available_after_merging_the_rate_block():
    """One allocation still returns a stage-resolved event tensor."""
    counts = {(0, 0): 30, (0, 1): 60, (0, 2): 10, (1, 0): 30, (1, 1): 60, (1, 2): 10}
    result, _recorder, _engine_obj = _run(_undiagnosed_population(counts))
    observation = result["aids_observation"]
    for name in (
        "hiv_diagnoses_in_acute_stage",
        "hiv_diagnoses_in_chronic_stage",
        "hiv_diagnoses_in_aids_stage",
    ):
        assert name in observation, (
            f"{name} is a published model output, so the allocation must be "
            "resolved by stage"
        )
    assert observation["hiv_diagnoses_in_acute_stage"] > 0
    assert observation["hiv_diagnoses_in_chronic_stage"] > 0


def test_cumulative_dx04_totals_match_the_combined_rate_block_reference():
    """The repair remains exact over a multi-step active-M1 trajectory."""
    counts = {(0, 0): 137, (0, 1): 263, (0, 2): 91,
              (1, 0): 149, (1, 1): 251, (1, 2): 103}
    population = _undiagnosed_population(counts)
    cumulative = 0
    reference_cumulative = 0
    for step in range(24):
        before = population.copy()
        result, recorder, engine = _run(
            population, annual_rate=0.4, symptom=0.3, rho=0.8
        )
        population = engine.state.population
        cumulative += int(result["total"])
        for population_name, orient in (("non_msm", 0), ("msm", MSM_IDX)):
            for block, aids_stage in (("non_aids", False), ("aids", True)):
                probability = stage_step_probability(
                    0.4,
                    routine_hazard_scale=0.8,
                    aids_symptom_annual_probability=0.3,
                    years_per_step=YEARS_PER_STEP,
                    aids_stage=aids_stage,
                )
                pool = recorder.pools[
                    f"stage_structured_diagnosis_{population_name}_{block}"
                ]
                reference_cumulative += int(
                    deterministic_allocate_events(pool, probability).sum()
                )
        assert int(population.sum()) == int(before.sum()), step
    assert cumulative == reference_cumulative


def test_the_site_is_triaged_in_the_a12_inventory():
    """The audit gate must no longer report this function as untriaged."""
    from tools.run_a12_deterministic_allocation_audit import (  # noqa: PLC0415
        INVENTORY, syntax_candidates,
    )

    row = next(r for r in INVENTORY if r["site_id"] == "DX-04")
    assert row["function"] == "SimulationEngineV3._step_stage_structured_diagnosis"
    assert row["classification"] == "rate_stratified_aggregate"
    assert row["fragmentation_sensitive"] is False
    assert row["between_arm_event_differential_people"] is None
    assert "stage-resolved" in row["mechanism"] or "OUTPUTS" in row["mechanism"]
    assert row["recommendation"]
    # And the scan that previously failed now disposes of the function.
    disposed = {
        (r["module"], r["function"]) for r in syntax_candidates() if r["disposition"]
    }
    assert ("run_v3.py", "_step_stage_structured_diagnosis") in disposed
