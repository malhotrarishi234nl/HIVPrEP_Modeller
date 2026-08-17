"""
Test Suite: Engine Hardening V12.5.1 (T-EH1 through T-EH8)

Covers ENG-F04 through ENG-F09 and ENG-F11 from the 2026-05-04 audit.
ENG-F10 was reverted — the V11 production config legitimately uses 'run_control'
(ConfigReader translates it to 'time' in the v6 dict); a warning on every
production call would be harmful noise.

  T-EH1: ENG-F04 — misnamed orientation raises KeyError in life-table mortality
  T-EH2: ENG-F04 — standard orientations work (regression)
  T-EH3: ENG-F05 — HIV+ count within tolerance after _seed_initial_hiv
  T-EH4: ENG-F05 — total population unchanged after seeding
  T-EH5: ENG-F06 — third PrEP type loaded from prep_schedules dynamically
  T-EH6: ENG-F07 — missing total_size emits UserWarning
  T-EH7: ENG-F08 — fractional year key survives round-trip in multipliers
  T-EH8: ENG-F09 — gap year within table range emits RuntimeWarning
  T-EH9: Deterministic event allocation preserves sparse aggregate events
  T-EH10: Deterministic event allocation preserves capacity, shape, and reproducibility

Run from v11/:
    pytest tests/test_engine_hardening_v124.py -v
"""

import sys, os, json, copy, warnings
import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine_v3 import PopulationState, GroupSystem
from config_reader import ConfigReader
from config_utils import extract_value
from demography import ChildhoodPool
from migration import MigrationModule

BASE_CONFIG_PATH = os.path.join(ROOT, "configs", "v11_configuration.json")


def load_raw():
    with open(BASE_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def make_v6(raw=None):
    """Return ConfigReader-translated v6 dict from raw config."""
    if raw is None:
        raw = load_raw()
    return ConfigReader(raw).as_v6_dict()


def make_groups_and_state(v6=None):
    """
    Return (GroupSystem, PopulationState, ChildhoodPool) from v6 dict.

    Replicates the engine's intervention-registration sequence:
      1. Build GroupSystem from v6["groups"]
      2. Register enabled PrEP interventions from v6["interventions"]
      3. Register Lenacapavir_Waning if Lenacapavir is enabled (V12.2)
      4. Call finalize_prep_states() — required before PopulationState
      5. Build PopulationState (now n_prep is set)
      6. Build ChildhoodPool (required by initialize_from_relative_weights)
    """
    if v6 is None:
        v6 = make_v6()
    g = GroupSystem(v6["groups"])

    # Register interventions (v6 keys are already CamelCase after ConfigReader)
    for intervention_name, intervention_config in v6.get("interventions", {}).items():
        if extract_value(intervention_config.get("enabled", {"value": True})):
            g.add_prep_intervention(intervention_name)

    # V12.2 synthetic waning state
    if "Lenacapavir" in g.prep_states:
        g.add_prep_intervention("Lenacapavir_Waning")

    g.finalize_prep_states()

    s = PopulationState(g)
    cp = ChildhoodPool(v6)
    return g, s, cp


# ---------------------------------------------------------------------------
# T-EH9/T-EH10 — deterministic event allocation
# ---------------------------------------------------------------------------

def test_EH9_deterministic_event_allocator_preserves_sparse_aggregate():
    """
    Sparse expected events distributed across many cells must not be rounded
    away cell-by-cell in deterministic mode.
    """
    from run_v3 import SimulationEngineV3

    population = np.ones(100, dtype=np.int64)
    events = SimulationEngineV3._deterministic_allocate_events(population, 0.49)

    assert events.sum() == 49
    assert events.shape == population.shape
    assert np.all(events <= population)


def test_EH10_deterministic_event_allocator_guardrails():
    """
    The allocator must preserve shape, cap events by source population, and be
    reproducible without RNG tie-breaking.
    """
    from run_v3 import SimulationEngineV3

    population = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    first = SimulationEngineV3._deterministic_allocate_events(population, 0.34)
    second = SimulationEngineV3._deterministic_allocate_events(population, 0.34)

    assert first.shape == population.shape
    assert np.array_equal(first, second)
    assert np.all(first <= population)
    assert first.sum() == round(float((population * 0.34).sum()))

    assert SimulationEngineV3._deterministic_allocate_events(population, 0.0).sum() == 0
    assert np.array_equal(
        SimulationEngineV3._deterministic_allocate_events(population, 1.0),
        population,
    )

    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        SimulationEngineV3._deterministic_allocate_events(population, -0.01)

    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        SimulationEngineV3._deterministic_allocate_events(population, 1.01)


# ---------------------------------------------------------------------------
# T-EH1 — ENG-F04: misnamed orientation raises KeyError in life-table mortality
# ---------------------------------------------------------------------------

def test_EH1_misnamed_orientation_raises_keyerror():
    """
    After ENG-F04, _calculate_life_table_mortality uses direct dict access.
    Removing 'Female' from orient_to_idx must immediately raise KeyError
    rather than silently applying mortality to whatever index 0 is.
    """
    from run_v3 import SimulationEngineV3

    raw = load_raw()
    raw_copy = copy.deepcopy(raw)
    raw_copy["run_control"]["end_year"] = {"value": 1971}

    eng = SimulationEngineV3(raw_copy, seed=42)

    assert eng.demography.life_table_enabled, "life_table must be enabled for this test"

    original = eng.demography.groups.orient_to_idx.copy()
    eng.demography.groups.orient_to_idx = {
        k: v for k, v in original.items() if k != "Female"
    }

    with pytest.raises(KeyError, match="Female"):
        eng.demography._calculate_life_table_mortality(
            eng.state.population,
            current_year=1970.0,
            rng=np.random.RandomState(42),
            deterministic=True,
        )

    eng.demography.groups.orient_to_idx = original


# ---------------------------------------------------------------------------
# T-EH2 — ENG-F04: standard orientation names work (regression)
# ---------------------------------------------------------------------------

def test_EH2_standard_orientations_no_error():
    """
    Standard orientation names must not raise in _calculate_life_table_mortality.
    """
    from run_v3 import SimulationEngineV3

    raw = load_raw()
    raw_copy = copy.deepcopy(raw)
    raw_copy["run_control"]["end_year"] = {"value": 1971}

    eng = SimulationEngineV3(raw_copy, seed=42)
    result = eng.demography._calculate_life_table_mortality(
        eng.state.population,
        current_year=1970.0,
        rng=np.random.RandomState(42),
        deterministic=True,
    )
    assert result is not None
    assert result.shape == eng.state.population.shape


# ---------------------------------------------------------------------------
# T-EH3 — ENG-F05: HIV+ count within tolerance after _seed_initial_hiv
# ---------------------------------------------------------------------------

def test_EH3_post_seed_hiv_count_within_tolerance():
    """
    After seeding at 1% prevalence, _validate_post_seed (called automatically
    inside initialize_from_config) must pass — meaning HIV+ count is within
    rounding tolerance of the target.
    """
    v6 = make_v6()
    v6_copy = copy.deepcopy(v6)
    v6_copy["population"]["initial_hiv_prevalence"] = {"value": 0.01}

    groups, state, cp = make_groups_and_state(v6_copy)
    # Should not raise (would raise ValueError if post-seed check fails)
    state.initialize_from_config(v6_copy["population"], childhood_pool=cp)

    susc_idx = groups.hiv_to_idx["Susceptible"]
    total    = int(np.sum(state.population))
    hiv_pos  = int(np.sum(state.population[:, :, :, susc_idx + 1:, :, :, :]))
    expected  = round(total * 0.01)
    tolerance = max(5, int(0.005 * expected) + 1)

    assert abs(hiv_pos - expected) <= tolerance, (
        f"HIV+ {hiv_pos} deviates from expected {expected} "
        f"by {abs(hiv_pos - expected)} (tol ±{tolerance})"
    )


# ---------------------------------------------------------------------------
# T-EH4 — ENG-F05: total population unchanged after seeding
# ---------------------------------------------------------------------------

def test_EH4_total_population_unchanged_after_seeding():
    """
    _seed_initial_hiv is a compartmental move (Susceptible → Chronic).
    Total population must be identical before and after.
    """
    v6 = make_v6()
    v6_copy = copy.deepcopy(v6)

    groups, state, cp = make_groups_and_state(v6_copy)
    state.initialize_from_relative_weights(v6_copy["population"], childhood_pool=cp)
    total_before = int(np.sum(state.population))

    state._seed_initial_hiv(0.02)
    total_after = int(np.sum(state.population))

    assert total_before == total_after, (
        f"Total population changed after seeding: {total_before} → {total_after}"
    )


# ---------------------------------------------------------------------------
# T-EH5 — ENG-F06: third PrEP type loaded dynamically
# ---------------------------------------------------------------------------

def test_EH5_third_prep_type_loaded_dynamically():
    """
    A third PrEP type injected into prep_schedules must appear in
    ScheduleManager.prep_initiation. The old hardcoded list silently dropped it.
    """
    from schedule_utils import ScheduleManager

    v6 = make_v6()
    v6_copy = copy.deepcopy(v6)

    v6_copy["prep_schedules"]["Cabotegravir"] = {
        "initiation":     {"schedule": {"2026": 0.02}},
        "discontinuation":{"schedule": {"2026": 0.10}},
    }

    sm = ScheduleManager(v6_copy)

    assert "Oral_PrEP"    in sm.prep_initiation, "Oral_PrEP missing"
    assert "Lenacapavir"  in sm.prep_initiation, "Lenacapavir missing"
    assert "Cabotegravir" in sm.prep_initiation, (
        "Cabotegravir silently dropped — ENG-F06 regression"
    )


# ---------------------------------------------------------------------------
# T-EH6 — ENG-F07: missing total_size emits UserWarning
# ---------------------------------------------------------------------------

def test_EH6_missing_total_size_warns():
    """
    If total_size is absent from pop_config, a UserWarning must be emitted
    before the fallback value is used. Previously it fired silently.
    """
    v6 = make_v6()
    v6_copy = copy.deepcopy(v6)
    v6_copy["population"].pop("total_size", None)

    groups, state, cp = make_groups_and_state(v6_copy)

    with pytest.warns(UserWarning, match="total_size"):
        try:
            state.initialize_from_relative_weights(v6_copy["population"], childhood_pool=cp)
        except Exception:
            pass  # may fail on missing age_sex_distribution — warning is what we test


# ---------------------------------------------------------------------------
# T-EH7 — ENG-F08: fractional year key survives round-trip in multipliers
# ---------------------------------------------------------------------------

def test_EH7_fractional_year_key_survives():
    """
    A multiplier dict keyed with '1995.5' must not raise KeyError and must
    return the correct multiplier. The old str(int(y)) round-trip would
    produce '1995' — missing the key entirely.
    """
    from run_v3 import SimulationEngineV3

    raw = load_raw()
    raw_copy = copy.deepcopy(raw)
    raw_copy["run_control"]["end_year"] = {"value": 1971}

    eng = SimulationEngineV3(raw_copy, seed=42)

    eng.demography.pop_adj_enabled = True
    eng.demography.pop_adj_config = {
        "year_multipliers": {"value": {"1995.5": 1.005, "2000": 1.0}},
    }

    population = eng.state.population.copy()
    result = eng.demography.apply_population_adjustments(population, current_year=1995.5)
    assert result is not None
    assert result.dtype == np.int64
    assert int(np.sum(result)) >= int(np.sum(population))


# ---------------------------------------------------------------------------
# T-EH8 — ENG-F09: gap year within table range emits RuntimeWarning
# ---------------------------------------------------------------------------

def test_EH8_gap_year_emits_runtime_warning():
    """
    A year_specific migration table covering 2000–2020 with no 2010 entry
    must emit a RuntimeWarning the first time 2010 is queried, then stay
    silent for the same gap year on subsequent calls (dedup).
    Years outside the table range must not warn.
    """
    from run_v3 import SimulationEngineV3

    raw = load_raw()
    raw_copy = copy.deepcopy(raw)
    raw_copy["run_control"]["end_year"] = {"value": 1971}

    eng = SimulationEngineV3(raw_copy, seed=42)

    gap_table = {
        "2000": 5000,
        "2005": 4000,
        # 2010 deliberately absent
        "2015": 3000,
        "2020": 2000,
    }
    v6_mig = copy.deepcopy(eng.config["migration"])
    v6_mig["quantity"]["mode"]         = {"value": "year_specific"}
    v6_mig["quantity"]["year_specific"] = {"value": gap_table}

    mm = MigrationModule(
        {**eng.config, "migration": v6_mig},
        eng.groups,
        eng.demography,
    )

    dummy_pop       = eng.state.population.copy()
    dummy_childhood = eng.demography.childhood

    # 1. Gap year inside range → RuntimeWarning on first call
    with pytest.warns(RuntimeWarning, match="2010"):
        mm._get_annual_net(dummy_pop, dummy_childhood, 2010.3)

    # 2. Same gap year → no second warning (dedup guard)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        mm._get_annual_net(dummy_pop, dummy_childhood, 2010.7)  # must not raise

    # 3. Year before table range → no warning
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        mm._get_annual_net(dummy_pop, dummy_childhood, 1999.0)

    # 4. Year after table range → no warning
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        mm._get_annual_net(dummy_pop, dummy_childhood, 2021.0)
