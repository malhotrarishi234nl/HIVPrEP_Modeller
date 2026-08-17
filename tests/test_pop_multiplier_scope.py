"""
tests/test_pop_multiplier_scope.py

Tests for population multiplier compartment_scope feature (V12.6).

Tensor shape used throughout: [2 age, 3 orient, 4 hiv_stages, 2 art, 2 prep]
HIV stages idx: Susceptible=0, Acute=1, Chronic=2, AIDS=3
PrEP idx:       None=0, Oral_PrEP=1
"""

import numpy as np
import pytest
from unittest.mock import MagicMock


def _make_demography(scope, multipliers=None, enabled=True):
    """Build a minimal DemographyModule with only pop_adj fields populated."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from demography import DemographyModule

    if multipliers is None:
        multipliers = {"2000": 1.0, "2100": 1.0}  # neutral default

    config = {
        "demography": {"enabled": True, "mode": "closed"},
        "fertility": {"enabled": False},
        "life_table": {"enabled": False},
        "sexual_debut": {"p_msm": {"value": 0.05}},
        "population_adjustments": {
            "enabled": {"value": enabled},
            "compartment_scope": {"value": scope},
            "year_multipliers": {"value": multipliers},
        },
        "migration": {},
    }

    # Minimal groups mock
    groups = MagicMock()
    groups.hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    groups.prep_to_idx = {"None": 0, "Oral_PrEP": 1}
    groups.age_groups = ["15-24", "25-49"]
    groups.age_min = [15, 25]
    groups.age_max = [24, 49]
    groups.n_age_groups = 2
    groups.n_orientations = 3
    groups.n_hiv_stages = 4
    groups.n_prep = 2
    groups.orient_to_idx = {"Female": 0, "MSM": 1, "Heterosexual_Male": 2}

    # Minimal time mock
    time_sys = MagicMock()
    time_sys.years_per_step = 1.0  # annual steps by default

    dm = DemographyModule(config, groups, time_sys)
    return dm


def _base_population(fill=100):
    """Tensor shape [2, 3, 4, 2, 2], all cells = fill."""
    return np.full((2, 3, 4, 2, 2), fill, dtype=np.int64)


# ── T1 ────────────────────────────────────────────────────────────────────────
def test_T1_scope_all_scales_entire_tensor():
    """scope='all' must scale every cell — backward compatibility regression."""
    dm = _make_demography(scope="all", multipliers={"2000": 1.05, "2100": 1.05})
    pop = _base_population(100)
    result = dm.apply_population_adjustments(pop, current_year=2025.0)

    expected = int(100 * 1.05 ** dm.time.years_per_step)
    assert np.all(result == expected), (
        f"scope='all': expected every cell={expected}, got min={result.min()} max={result.max()}"
    )


# ── T2 ────────────────────────────────────────────────────────────────────────
def test_T2_scope_susceptible_only_targets_correct_slice():
    """scope='susceptible_only' scales [susc, no_prep] only; all other compartments untouched."""
    dm = _make_demography(scope="susceptible_only", multipliers={"2000": 1.1, "2100": 1.1})
    pop = _base_population(100)
    result = dm.apply_population_adjustments(pop, current_year=2025.0)

    susc = 0
    no_prep = 0
    oral_prep = 1

    # Scaled slice must be > 100
    assert np.all(result[:, :, susc, :, no_prep] > 100), \
        "Susceptible+no-PrEP slice was not scaled up"

    # PrEP slice on susceptible must be untouched
    assert np.all(result[:, :, susc, :, oral_prep] == 100), \
        "Susceptible+Oral_PrEP slice was incorrectly modified"

    # All HIV+ stages must be untouched
    for hiv_idx, name in [(1, "Acute"), (2, "Chronic"), (3, "AIDS")]:
        assert np.all(result[:, :, hiv_idx, :, :] == 100), \
            f"{name} compartment was incorrectly modified"


# ── T3 ────────────────────────────────────────────────────────────────────────
def test_T3_default_scope_is_susceptible_only():
    """When compartment_scope field is absent from config, default must be 'susceptible_only'."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from demography import DemographyModule

    config = {
        "demography": {"enabled": True, "mode": "closed"},
        "fertility": {"enabled": False},
        "life_table": {"enabled": False},
        "sexual_debut": {"p_msm": {"value": 0.05}},
        "population_adjustments": {
            "enabled": {"value": True},
            # compartment_scope deliberately absent
            "year_multipliers": {"value": {"2000": 1.0, "2100": 1.0}},
        },
        "migration": {},
    }
    groups = MagicMock()
    groups.hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    groups.prep_to_idx = {"None": 0, "Oral_PrEP": 1}
    groups.age_groups = ["15-49"]
    groups.age_min = [15]
    groups.age_max = [49]
    groups.n_age_groups = 1
    groups.n_orientations = 3
    groups.n_hiv_stages = 4
    groups.n_prep = 2
    groups.orient_to_idx = {"Female": 0, "MSM": 1, "Heterosexual_Male": 2}
    time_sys = MagicMock()
    time_sys.years_per_step = 1.0

    dm = DemographyModule(config, groups, time_sys)
    assert dm.pop_adj_scope == "susceptible_only", \
        f"Default scope should be 'susceptible_only', got '{dm.pop_adj_scope}'"


# ── T4 ────────────────────────────────────────────────────────────────────────
def test_T4_invalid_scope_raises_value_error():
    """An unrecognised compartment_scope value must raise ValueError at validation time."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from demography import DemographyModule

    config = {
        "demography": {"enabled": True, "mode": "closed"},
        "fertility": {"enabled": False},
        "life_table": {"enabled": False},
        "sexual_debut": {"p_msm": {"value": 0.05}},
        "population_adjustments": {
            "enabled": {"value": True},
            "compartment_scope": {"value": "hiv_negative"},  # invalid
            "year_multipliers": {"value": {"2000": 1.0, "2100": 1.0}},
        },
        "migration": {},
    }
    groups = MagicMock()
    groups.hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    groups.prep_to_idx = {"None": 0, "Oral_PrEP": 1}
    groups.age_groups = ["15-49"]
    groups.age_min = [15]
    groups.age_max = [49]
    groups.n_age_groups = 1
    groups.n_orientations = 3
    groups.n_hiv_stages = 4
    groups.n_prep = 2
    groups.orient_to_idx = {"Female": 0, "MSM": 1, "Heterosexual_Male": 2}
    time_sys = MagicMock()
    time_sys.years_per_step = 1.0

    with pytest.raises(ValueError, match="compartment_scope"):
        DemographyModule(config, groups, time_sys)


# ── T5 ────────────────────────────────────────────────────────────────────────
def test_T5_identity_multiplier_tensor_unchanged():
    """Multiplier = 1.0 must return a tensor identical to the input under susceptible_only."""
    dm = _make_demography(scope="susceptible_only", multipliers={"2000": 1.0, "2100": 1.0})
    pop = _base_population(200)
    result = dm.apply_population_adjustments(pop, current_year=2050.0)
    assert np.array_equal(result, pop), \
        "Identity multiplier (1.0) must leave tensor unchanged"


# ── T6 ────────────────────────────────────────────────────────────────────────
def test_T6_art_ghost_cells_remain_zero():
    """Susceptible cells with art_idx > 0 are zero in a valid model state and must stay zero."""
    dm = _make_demography(scope="susceptible_only", multipliers={"2000": 1.2, "2100": 1.2})
    pop = _base_population(100)
    susc = 0
    # In a valid model state susceptibles are not on ART; zero out art_idx=1 for susc
    pop[:, :, susc, 1, :] = 0
    result = dm.apply_population_adjustments(pop, current_year=2025.0)
    assert np.all(result[:, :, susc, 1, :] == 0), \
        "Zero cells in susceptible+ART>0 should remain zero after scaling"


# ── T7 ────────────────────────────────────────────────────────────────────────
def test_T7_timestep_scaling_preserved_susceptible_only():
    """
    Quarterly timestep (years_per_step=0.25): effective multiplier must be annual^0.25,
    not the raw annual value.
    """
    dm = _make_demography(scope="susceptible_only", multipliers={"2000": 1.08, "2100": 1.08})
    dm.time.years_per_step = 0.25

    pop = _base_population(10000)
    result = dm.apply_population_adjustments(pop, current_year=2040.0)

    susc = 0
    no_prep = 0
    expected_effective = 1.08 ** 0.25
    expected_cell = int(10000 * expected_effective)

    actual_cell = int(result[0, 0, susc, 0, no_prep])
    assert abs(actual_cell - expected_cell) <= 1, (
        f"Timestep scaling: expected ~{expected_cell} (1.08^0.25 × 10000), got {actual_cell}"
    )

    # Confirm annual rate is NOT directly applied
    wrong_cell = int(10000 * 1.08)
    assert actual_cell != wrong_cell, \
        "Timestep correction not applied — raw annual multiplier was used instead"


# ── T8 ────────────────────────────────────────────────────────────────────────
def test_T8_mass_balance_adjustment_change_accurate():
    """
    adjustment_change = total_after - total_before must equal exactly the increase
    in the susceptible-only slice. Validates that run_v3.py mass balance tracker
    needs no modification.
    """
    dm = _make_demography(scope="susceptible_only", multipliers={"2000": 1.15, "2100": 1.15})
    pop = _base_population(1000)

    total_before = int(pop.sum())
    result = dm.apply_population_adjustments(pop, current_year=2030.0)
    total_after = int(result.sum())

    adjustment_change = total_after - total_before

    susc = 0
    no_prep = 0
    susc_slice_before = int(pop[:, :, susc, :, no_prep].sum())
    susc_slice_after = int(result[:, :, susc, :, no_prep].sum())
    expected_change = susc_slice_after - susc_slice_before

    assert adjustment_change == expected_change, (
        f"Mass balance mismatch: total delta={adjustment_change}, "
        f"susceptible slice delta={expected_change}"
    )
    # Also confirm HIV+ total is unchanged
    hiv_plus_before = int(pop[:, :, 1:, :, :].sum())
    hiv_plus_after = int(result[:, :, 1:, :, :].sum())
    assert hiv_plus_before == hiv_plus_after, \
        f"HIV+ total changed: {hiv_plus_before} → {hiv_plus_after}"
