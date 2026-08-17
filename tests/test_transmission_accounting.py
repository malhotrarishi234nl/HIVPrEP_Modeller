import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transmission_corrected import TransmissionEngine


class AccountingGroups:
    n_age_groups = 1
    n_orientations = 3
    n_activities = 1
    n_prep = 1
    orientations = ["Susceptible_Group", "Partner_A", "Partner_B"]
    orient_to_idx = {name: idx for idx, name in enumerate(orientations)}
    activities = ["High"]
    prep_states = ["None"]
    hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}


class MinimalTime:
    years_per_step = 1.0


CONFIG = {
    "disease": {
        "transmission": {
            "beta_acute": {"value": 1.0},
            "beta_chronic": {"value": 1.0},
            "beta_aids": {"value": 1.0},
        },
        "treatment": {
            "art_transmission_reduction": {"value": 0.0},
        },
    },
    "mixing": {
        "condom_usage": {"value": {"High": 1.0}},
    },
    "interventions": {},
}


def _population() -> np.ndarray:
    pop = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    pop[0, 0, 0, 0, 0, 0, 0] = 10
    pop[0, 1, 0, 2, 0, 0, 0] = 100
    pop[0, 2, 0, 2, 0, 0, 0] = 100
    return pop


def test_reported_infections_equal_applied_infections_after_multi_partner_cap():
    engine = TransmissionEngine(CONFIG, AccountingGroups(), MinimalTime())
    contacts = np.zeros((3, 1, 3, 1), dtype=np.int64)
    contacts[0, 0, 1, 0] = 10
    contacts[0, 0, 2, 0] = 10

    pop = _population()
    reported, infections = engine.calculate_transmission(
        pop, contacts, np.random.RandomState(1), deterministic=True
    )

    assert reported == 10
    assert int(np.sum(infections)) == 10
    assert infections[0, 0, 0, 0] == 10

    applied = engine.apply_infections_to_population(pop.copy(), infections)
    assert applied[0, 0, 0, 0, 0, 0, 0] == 0
    assert applied[0, 0, 0, 1, 0, 0, 0] == 10


def test_final_cap_preserves_non_saturated_cells():
    engine = TransmissionEngine(CONFIG, AccountingGroups(), MinimalTime())
    contacts = np.zeros((3, 1, 3, 1), dtype=np.int64)
    contacts[0, 0, 1, 0] = 4
    contacts[0, 0, 2, 0] = 3

    reported, infections = engine.calculate_transmission(
        _population(), contacts, np.random.RandomState(1), deterministic=True
    )

    assert reported == 7
    assert int(np.sum(infections)) == 7
    assert infections[0, 0, 0, 0] == 7
