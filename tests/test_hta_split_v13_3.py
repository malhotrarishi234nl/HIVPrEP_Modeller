from collections import defaultdict
from types import SimpleNamespace

import numpy as np

from run_v3 import SimulationEngineV3


class _Groups:
    hiv_to_idx = {"Susceptible": 0}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
    prep_to_idx = {
        "None": 0,
        "Oral_PrEP": 1,
        "Lenacapavir": 2,
        "Lenacapavir_Waning": 3,
    }
    activities = ["High"]
    activity_to_idx = {"High": 0}


def _allocate_fixed_events(capacity, events, weights=None):
    allocations = np.zeros_like(capacity, dtype=np.int64)
    remaining = int(events)
    for idx in np.ndindex(capacity.shape):
        if remaining <= 0:
            break
        take = min(int(capacity[idx]), remaining)
        allocations[idx] = take
        remaining -= take
    return allocations


def _engine_with_switch(preserve_total=True):
    engine = SimulationEngineV3.__new__(SimulationEngineV3)
    engine.groups = _Groups()
    engine.config = {
        "prep_schedules": {
            "Lenacapavir": {
                "advanced": {
                    "switch_share_from_prep": {
                        "enabled": {"value": True},
                        "from_prep_state": {"value": "Oral_PrEP"},
                        "share_schedule": {"value": {"2025": 0.5}},
                        "interpolation": {"value": "stepwise"},
                        "preserve_total_prep_stock": {"value": preserve_total},
                    }
                }
            }
        }
    }
    engine.state = SimpleNamespace(
        population=np.zeros((1, 1, 1, 1, 1, 1, 4), dtype=np.int64)
    )
    engine._allocate_fixed_events = _allocate_fixed_events
    engine._prep_step_initiations = defaultdict(int)
    engine._prep_step_target_additions = defaultdict(int)
    return engine


def test_preserve_total_switch_offsets_oral_target_stock():
    engine = _engine_with_switch(preserve_total=True)
    pop = engine.state.population
    pop[0, 0, 0, 0, 0, 0, 0] = 100
    pop[0, 0, 0, 0, 0, 0, 1] = 80
    pop[0, 0, 0, 0, 0, 0, 2] = 20

    engine._apply_prep_forward_target_stock(
        prep_name="Oral_PrEP",
        prep_idx=1,
        target_stock=100,
        current_year=2025,
        eligible_orient_indices=[0],
        eligible_act_indices=[0],
        age_min_idx=0,
        age_max_idx=1,
        modifiers_raw={"High": 1.0},
    )

    assert pop[0, 0, 0, 0, 0, 0, 1] == 80
    assert pop[0, 0, 0, 0, 0, 0, 2] == 20
    assert pop[0, 0, 0, 0, 0, 0, 0] == 100
    assert engine._prep_step_target_additions["Oral_PrEP"] == 0


def test_non_preserve_switch_keeps_product_specific_oral_target_stock():
    engine = _engine_with_switch(preserve_total=False)
    pop = engine.state.population
    pop[0, 0, 0, 0, 0, 0, 0] = 100
    pop[0, 0, 0, 0, 0, 0, 1] = 80
    pop[0, 0, 0, 0, 0, 0, 2] = 20

    engine._apply_prep_forward_target_stock(
        prep_name="Oral_PrEP",
        prep_idx=1,
        target_stock=100,
        current_year=2025,
        eligible_orient_indices=[0],
        eligible_act_indices=[0],
        age_min_idx=0,
        age_max_idx=1,
        modifiers_raw={"High": 1.0},
    )

    assert pop[0, 0, 0, 0, 0, 0, 1] == 100
    assert pop[0, 0, 0, 0, 0, 0, 2] == 20
    assert pop[0, 0, 0, 0, 0, 0, 0] == 80
    assert engine._prep_step_target_additions["Oral_PrEP"] == 20
