"""
Test Suite: Fractional Aging Population Conservation (V11-native)

Migrated from test_fractional_aging_basic.py (V6-era config) to V11 config
format using a synthetic config dict. The SimulationEngineV3 accepts dict
input directly (V11.9.6 BLOCKER-A fix).

Coverage gap confirmed: no existing passing test validates per-step population
conservation across adult aging or verifies flow direction and terminal bin
accumulation. test_fractional_aging_simple.py tests the algorithm math in
isolation; this suite validates it through the engine API.

Population tensor shape: (age=20, orient=3, activity=3, hiv=4, diag=2, art=2, prep=1)

Tests:
  1. Adult aging conservation    — total population conserved by _apply_fractional_aging_adults()
  2. Transfer direction          — people flow younger→older only, never backward
  3. Terminal bin accumulation   — terminal bin receives inflow but has no outflow
  4. Childhood aging conservation — childhood pool conserved including sexual debut outflow

Run from model root (v11/):
  python tests/test_aging_conservation.py
"""

import sys
import os
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

BASE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json"
)


def make_zero_step_engine(seed=42):
    """Return an initialised engine with zero timesteps.

    HIV prevalence is set to zero so the population is entirely susceptible,
    giving clean conservation checks without disease dynamics.
    """
    with open(BASE_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    config["run_control"]["start_year"]["value"] = 1990
    config["run_control"]["end_year"]["value"]   = 1990   # 0 steps
    config["run_control"]["n_runs"]["value"]      = 1
    config["run_control"]["deterministic_mode"]["value"] = True
    config["population"]["initial_hiv_prevalence"]["value"] = 0.0
    return SimulationEngineV3(config, seed=seed)


# ---------------------------------------------------------------------------
# Test 1: Adult aging population conservation
# ---------------------------------------------------------------------------

def test_adult_aging_conservation():
    """
    _apply_fractional_aging_adults() redistributes population across age bins
    but must never create or destroy individuals.
    Total population must be identical before and after.
    """
    print("\n" + "="*60)
    print("TEST 1: Adult Aging Population Conservation")
    print("="*60)

    engine = make_zero_step_engine()

    before = int(engine.state.population.sum())
    engine._apply_fractional_aging_adults()
    after  = int(engine.state.population.sum())

    diff = after - before
    print(f"  Population before: {before:,}")
    print(f"  Population after:  {after:,}")
    print(f"  Difference:        {diff:,}  (must be 0)")

    assert diff == 0, f"FAIL: {abs(diff):,} people {'created' if diff > 0 else 'lost'} during adult aging"
    print("  ✓ PASS: Adult aging conserves total population")
    return True


# ---------------------------------------------------------------------------
# Test 2: Transfer direction (younger → older only)
# ---------------------------------------------------------------------------

def test_transfer_direction():
    """
    After _apply_fractional_aging_adults(), population may only flow from a
    bin into the next-older bin. No younger bin should ever gain people from
    aging, and the source bin must decrease.
    """
    print("\n" + "="*60)
    print("TEST 2: Transfer Direction (younger → older only)")
    print("="*60)

    engine = make_zero_step_engine()

    # Zero out all population, then place 10,000 in age bin 2 (susceptible,
    # undiagnosed, not on ART, no PrEP — first non-zero element per slice).
    engine.state.population[:] = 0
    test_bin = 2
    engine.state.population[test_bin, 0, 0, 0, 0, 0, 0] = 10_000

    before = engine.state.population.copy()
    engine._apply_fractional_aging_adults()
    after = engine.state.population

    younger_bins_gained = any(
        after[b, 0, 0, 0, 0, 0, 0] > before[b, 0, 0, 0, 0, 0, 0]
        for b in range(test_bin)
    )
    source_decreased = after[test_bin, 0, 0, 0, 0, 0, 0] < 10_000
    next_bin_gained  = after[test_bin + 1, 0, 0, 0, 0, 0, 0] > 0

    print(f"  Younger bins gained people:  {younger_bins_gained}  (must be False)")
    print(f"  Source bin decreased:        {source_decreased}   (must be True)")
    print(f"  Next-older bin gained:       {next_bin_gained}   (must be True)")
    print(f"  Remaining in bin {test_bin}:         {after[test_bin, 0, 0, 0, 0, 0, 0]:,}")
    print(f"  Moved to bin {test_bin + 1}:           {after[test_bin + 1, 0, 0, 0, 0, 0, 0]:,}")

    assert not younger_bins_gained, "FAIL: younger bin gained people during aging"
    assert source_decreased,        "FAIL: source bin did not decrease"
    assert next_bin_gained,         "FAIL: next-older bin received no inflow"
    print("  ✓ PASS: Population flows younger → older only")
    return True


# ---------------------------------------------------------------------------
# Test 3: Terminal bin accumulation
# ---------------------------------------------------------------------------

def test_terminal_bin_accumulation():
    """
    The terminal (oldest) age bin receives inflow from the penultimate bin
    after the penultimate cohort completes its residence time. The terminal
    bin itself must retain its occupants and accumulate inflow.
    """
    print("\n" + "="*60)
    print("TEST 3: Terminal Bin Accumulation")
    print("="*60)

    engine = make_zero_step_engine()
    n_age      = engine.groups.n_age_groups
    terminal   = n_age - 1
    penultimate = terminal - 1

    engine.state.population[:] = 0
    engine.state.population[terminal,    0, 0, 0, 0, 0, 0] = 5_000
    engine.state.population[penultimate, 0, 0, 0, 0, 0, 0] = 10_000
    engine._seed_residence_aging_queues_from_population()

    phases = int(round(5.0 / engine.time.years_per_step))
    for _ in range(phases):
        engine._apply_fractional_aging_adults()

    terminal_after    = int(engine.state.population[terminal,    0, 0, 0, 0, 0, 0])
    penultimate_after = int(engine.state.population[penultimate, 0, 0, 0, 0, 0, 0])
    total_after       = terminal_after + penultimate_after

    print(f"  Terminal bin   — before: 5,000   after: {terminal_after:,}  (must include inflow)")
    print(f"  Penultimate bin after:   {penultimate_after:,}  (cohort should have moved out)")
    print(f"  Two-bin total after:     {total_after:,}  (must equal 15,000)")

    assert terminal_after > 5_000, (
        f"FAIL: terminal bin did not grow ({terminal_after} ≤ 5,000); "
        "no inflow from penultimate bin"
    )
    assert total_after == 15_000, (
        f"FAIL: two-bin total {total_after} ≠ 15,000 (population not conserved)"
    )
    print("  ✓ PASS: Terminal bin accumulates correctly with no outflow")
    return True


# ---------------------------------------------------------------------------
# Test 4: Childhood aging conservation (including sexual debut outflow)
# ---------------------------------------------------------------------------

def test_childhood_aging_conservation():
    """
    apply_fractional_aging() on the childhood pool returns
    (females_debuting, males_debuting) — the oldest children who age into
    adulthood. They leave the childhood pool but are not lost; the caller
    transfers them to the adult tensor.

    Conservation: childhood_after + total_debuts == childhood_before
    """
    print("\n" + "="*60)
    print("TEST 4: Childhood Aging Conservation (incl. debuts)")
    print("="*60)

    engine = make_zero_step_engine()

    before = engine.demography.childhood.get_total()

    females_debut, males_debut = engine.demography.childhood.apply_fractional_aging(
        engine.rng, engine.deterministic, engine.time
    )
    total_debuts = females_debut + males_debut

    after     = engine.demography.childhood.get_total()
    accounted = after + total_debuts

    print(f"  Childhood before:          {before:,}")
    print(f"  Childhood after:           {after:,}")
    print(f"  Debuted to adulthood:      {total_debuts:,}")
    print(f"  Accounted (after+debuts):  {accounted:,}  (must equal {before:,})")

    assert accounted == before, (
        f"FAIL: {abs(before - accounted):,} people unaccounted for "
        f"(before={before}, after={after}, debuts={total_debuts})"
    )
    print("  ✓ PASS: Childhood aging conserves population (debut outflow accounted)")
    return True


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    tests = [
        ("Adult Aging Conservation",           test_adult_aging_conservation),
        ("Transfer Direction",                  test_transfer_direction),
        ("Terminal Bin Accumulation",           test_terminal_bin_accumulation),
        ("Childhood Aging Conservation",        test_childhood_aging_conservation),
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "="*60)
    print("FRACTIONAL AGING CONSERVATION TEST SUITE (V11-native)")
    print("="*60)

    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"\n  ✗ FAIL: {e}")
            failed += 1
            errors.append((name, str(e)))
        except Exception as e:
            import traceback
            print(f"\n  ✗ ERROR: {e}")
            traceback.print_exc()
            failed += 1
            errors.append((name, f"Exception: {e}"))

    print("\n" + "="*60)
    print(f"RESULTS: {passed}/{len(tests)} passed, {failed} failed")
    print("="*60)

    if errors:
        print("\nFailed tests:")
        for name, msg in errors:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("\n✓ All tests passed.")
        sys.exit(0)
