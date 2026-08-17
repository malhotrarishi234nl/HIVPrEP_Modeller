import copy
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3
from state_snapshot import StateSnapshotManager


def test_snapshot_captured_before_freeze_step_transitions():
    with open("configs/v11_configuration.json") as f:
        config = json.load(f)

    config = copy.deepcopy(config)
    config["run_control"]["start_year"] = {"value": 2025}
    config["run_control"]["end_year"] = {"value": 2026}
    config["run_control"]["deterministic_mode"] = {"value": True}

    engine = SimulationEngineV3(config, seed=123)
    manager = StateSnapshotManager()
    manager.configure(freeze_enabled=True, freeze_year=2025.0)
    engine._snapshot_manager = manager

    initial_pop = engine.state.population.copy()
    initial_childhood = engine.demography.childhood.population.copy()

    original_prep_step = engine._step_prep_transitions

    def move_one_susceptible_to_prep(current_year):
        original_prep_step(current_year)
        susc_idx = engine.groups.hiv_to_idx["Susceptible"]
        none_idx = engine.groups.prep_to_idx["None"]
        oral_idx = engine.groups.prep_to_idx.get("Oral_PrEP")
        if oral_idx is None:
            return
        source = engine.state.population[:, :, :, susc_idx, :, :, none_idx]
        nonzero = np.argwhere(source > 0)
        if len(nonzero) == 0:
            return
        age, orient, act, diag, art = nonzero[0]
        engine.state.population[age, orient, act, susc_idx, diag, art, none_idx] -= 1
        engine.state.population[age, orient, act, susc_idx, diag, art, oral_idx] += 1

    engine._step_prep_transitions = move_one_susceptible_to_prep

    engine.step_simulation(0)

    snapshot = manager.baseline_snapshot
    assert snapshot is not None
    assert snapshot.step == 0
    assert snapshot.year == 2025.0
    assert np.array_equal(snapshot.population, initial_pop)
    assert np.array_equal(snapshot.childhood_population, initial_childhood)
