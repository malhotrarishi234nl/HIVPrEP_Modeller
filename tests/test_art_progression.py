"""
Test Suite: ART-Dependent Disease Progression (V11-native)

Migrated from test_art_dependent_progression.py (V6-era config) to V11 config
format using synthetic config dicts. The SimulationEngineV3 accepts dict input
directly (added V11.9.6 BLOCKER-A). ART is schedule-based in V11 — the flat
art_initiation_rate key was removed as dead code in V11.9.4.

Population tensor shape: (age=20, orient=3, activity=3, hiv=4, diag=2, art=2, prep=1)
  - dim 4 (size 2): diagnosis status  [0=Undiagnosed, 1=Diagnosed]
  - dim 5 (size 2): ART status        [0=Not_on_ART,  1=On_ART]

Tests:
  1. Backward compatibility  — config without chronic_to_aids_on_art behaves
                               identically to one where on_art_rate == off_art_rate
  2. ART slows progression   — on-ART Chronic progresses ~10x slower (direct step)
  3. Off-ART rate unchanged  — off-ART pool unaffected by presence of on_art key
  4. Mass balance            — no people created/destroyed during one progression step
  5. Zero on-ART rate        — rate=0 → zero progressions from on-ART Chronic
  6. Equal rates symmetric   — equal rates produce identical progression from both pools
  7. Full run smoke test     — end-to-end simulation completes without crash

Run from model root (v11/):
  python tests/test_art_progression.py
"""

import sys
import os
import json
import copy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json"
)


def load_base_config():
    with open(BASE_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def make_zero_step_engine(hiv_prevalence=0.0, seed=42):
    """Return an initialised engine with zero timesteps (no simulation run).

    Used for tests that call engine methods directly on the population tensor.
    ART schedule is irrelevant here; we manipulate state directly.
    """
    config = load_base_config()
    config["run_control"]["start_year"]["value"] = 1990
    config["run_control"]["end_year"]["value"]   = 1990   # 0 steps
    config["run_control"]["n_runs"]["value"]      = 1
    config["run_control"]["deterministic_mode"]["value"] = True
    config["population"]["initial_hiv_prevalence"]["value"] = hiv_prevalence
    return SimulationEngineV3(config, seed=seed)


def test_art_initiations_by_stage_counter():
    """ART transition returns gross initiation counters stratified by HIV stage."""
    config = load_base_config()
    config["run_control"]["start_year"]["value"] = 2020
    config["run_control"]["end_year"]["value"] = 2020
    config["run_control"]["n_runs"]["value"] = 1
    config["run_control"]["deterministic_mode"]["value"] = True
    config["interventions"]["art"]["schedule"]["initiation"]["value"] = {"2020": 1.0}
    config["interventions"]["art"]["advanced"]["art_dropout_rate"]["value"] = 0.0

    engine = SimulationEngineV3(config, seed=42)
    pop = engine.state.population
    pop[:] = 0

    diag_idx = 1
    off_art_idx = 0
    on_art_idx = 1
    no_prep_idx = engine.groups.prep_to_idx["None"]

    pop[0, 0, 0, engine.groups.hiv_to_idx["Acute"], diag_idx, off_art_idx, no_prep_idx] = 100
    pop[0, 0, 0, engine.groups.hiv_to_idx["Chronic"], diag_idx, off_art_idx, no_prep_idx] = 200
    pop[0, 0, 0, engine.groups.hiv_to_idx["AIDS"], diag_idx, off_art_idx, no_prep_idx] = 300

    result = engine._step_art_transitions(2020.0)

    assert result["initiations"] == 600
    assert result["initiations_by_stage"] == {
        "Acute": 100,
        "Chronic": 200,
        "AIDS": 300,
    }
    assert engine._art_step_initiations_by_stage == result["initiations_by_stage"]
    assert np.sum(pop[:, :, :, 1:, diag_idx, off_art_idx, :]) == 0
    assert np.sum(pop[:, :, :, engine.groups.hiv_to_idx["Acute"], diag_idx, on_art_idx, :]) == 100
    assert np.sum(pop[:, :, :, engine.groups.hiv_to_idx["Chronic"], diag_idx, on_art_idx, :]) == 200
    assert np.sum(pop[:, :, :, engine.groups.hiv_to_idx["AIDS"], diag_idx, on_art_idx, :]) == 300


def run_short_sim(config_dict, years=10, seed=42):
    """Run a short deterministic simulation and return the engine."""
    config_dict = copy.deepcopy(config_dict)
    config_dict["run_control"]["end_year"]["value"] = (
        config_dict["run_control"]["start_year"]["value"] + years
    )
    config_dict["run_control"]["n_runs"]["value"] = 1
    config_dict["run_control"]["deterministic_mode"]["value"] = True
    engine = SimulationEngineV3(config_dict, seed=seed)
    engine.run(run_number=1)
    return engine


def get_chronic_and_aids(engine):
    """Return (chronic_off_art, chronic_on_art, aids_off_art, aids_on_art).

    Tensor layout: (age, orient, activity, hiv, diagnosis, art, prep)
      - art dim = 5  [0=Not_on_ART, 1=On_ART]
    """
    pop = engine.state.population
    chronic_idx = engine.groups.hiv_to_idx["Chronic"]
    aids_idx    = engine.groups.hiv_to_idx["AIDS"]
    ch_off  = int(np.sum(pop[:, :, :, chronic_idx, :, 0, :]))
    ch_on   = int(np.sum(pop[:, :, :, chronic_idx, :, 1, :]))
    ai_off  = int(np.sum(pop[:, :, :, aids_idx,    :, 0, :]))
    ai_on   = int(np.sum(pop[:, :, :, aids_idx,    :, 1, :]))
    return ch_off, ch_on, ai_off, ai_on


# ---------------------------------------------------------------------------
# Test 1: Backward compatibility
# ---------------------------------------------------------------------------

def test_backward_compatibility():
    """
    An engine config without chronic_to_aids_on_art must produce identical
    results to one where chronic_to_aids_on_art == chronic_to_aids.

    Both use a direct progression step on identical synthetic states so
    that stochastic elements (births, deaths, ART transitions) are excluded.
    """
    print("\n" + "="*60)
    print("TEST 1: Backward Compatibility")
    print("="*60)

    chronic_idx = None  # resolved per-engine

    # Engine A: key absent — engine falls back to off-ART rate
    eng_a = make_zero_step_engine()
    chronic_idx = eng_a.groups.hiv_to_idx["Chronic"]
    # Remove the on_art key from the V6-flat config
    eng_a.config["disease"]["progression"].pop("chronic_to_aids_on_art", None)
    off_art_rate = eng_a.config["disease"]["progression"]["chronic_to_aids"]["value"]
    # Place 5000 in Chronic off-ART, 5000 in Chronic on-ART
    eng_a.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 5000
    eng_a.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = 5000
    eng_a._step_disease_progression()
    ch_off_a, ch_on_a, ai_off_a, ai_on_a = get_chronic_and_aids(eng_a)

    # Engine B: key present and equal to off-ART rate — should give same result
    eng_b = make_zero_step_engine()
    chronic_idx = eng_b.groups.hiv_to_idx["Chronic"]
    eng_b.config["disease"]["progression"]["chronic_to_aids_on_art"] = {
        "value": off_art_rate,
        "notes": "Set equal for backward-compat test"
    }
    eng_b.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 5000
    eng_b.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = 5000
    eng_b._step_disease_progression()
    ch_off_b, ch_on_b, ai_off_b, ai_on_b = get_chronic_and_aids(eng_b)

    diff_off = abs(ai_off_a - ai_off_b)
    diff_on  = abs(ai_on_a  - ai_on_b)

    print(f"  AIDS off-ART  — no key: {ai_off_a}  | equal key: {ai_off_b}  (diff={diff_off})")
    print(f"  AIDS on-ART   — no key: {ai_on_a}   | equal key: {ai_on_b}   (diff={diff_on})")

    assert diff_off == 0, f"FAIL: off-ART AIDS differs {ai_off_a} vs {ai_off_b}"
    assert diff_on  == 0, f"FAIL: on-ART AIDS differs {ai_on_a} vs {ai_on_b}"
    print("  ✓ PASS: identical outcomes when key absent vs key=off-ART rate")
    return True


# ---------------------------------------------------------------------------
# Test 2: ART slows Chronic→AIDS progression
# ---------------------------------------------------------------------------

def test_art_slows_progression():
    """
    With chronic_to_aids = 0.05/yr and chronic_to_aids_on_art = 0.005/yr,
    one direct progression step on equal-sized pools should show ~10x fewer
    AIDS cases from the on-ART pool vs the off-ART pool.
    """
    print("\n" + "="*60)
    print("TEST 2: ART Slows Progression (direct step)")
    print("="*60)

    engine = make_zero_step_engine()
    chronic_idx = engine.groups.hiv_to_idx["Chronic"]

    N = 50_000  # large pool for stable proportions
    engine.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = N  # off-ART
    engine.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = N  # on-ART

    engine.config["disease"]["progression"]["chronic_to_aids"]["value"] = 0.05
    engine.config["disease"]["progression"]["chronic_to_aids_on_art"] = {"value": 0.005}

    engine._step_disease_progression()

    _, _, ai_off, ai_on = get_chronic_and_aids(engine)
    ratio = ai_off / ai_on if ai_on > 0 else float("inf")

    print(f"  AIDS from off-ART pool: {ai_off:,}  (rate 0.05/yr)")
    print(f"  AIDS from on-ART pool:  {ai_on:,}   (rate 0.005/yr)")
    print(f"  Ratio off/on: {ratio:.2f}x  (expected ~10x)")

    assert ai_off > ai_on, "FAIL: off-ART pool should produce more AIDS than on-ART"
    assert ratio > 5, f"FAIL: ratio {ratio:.2f} < 5 (insufficient separation for 10x rate difference)"
    print(f"  ✓ PASS: off-ART progresses {ratio:.1f}x more than on-ART pool")
    return True


# ---------------------------------------------------------------------------
# Test 3: Off-ART rate unchanged by presence of on-ART key
# ---------------------------------------------------------------------------

def test_off_art_rate_unchanged():
    """
    Adding chronic_to_aids_on_art to the config must not change the
    off-ART Chronic→AIDS progression rate.

    Compare two engines with identical off-ART Chronic pools and no
    on-ART population. They should produce identical AIDS counts.
    """
    print("\n" + "="*60)
    print("TEST 3: Off-ART Rate Unchanged")
    print("="*60)

    # Engine A: no on-ART key
    eng_a = make_zero_step_engine()
    chronic_idx = eng_a.groups.hiv_to_idx["Chronic"]
    eng_a.config["disease"]["progression"].pop("chronic_to_aids_on_art", None)
    eng_a.config["disease"]["progression"]["chronic_to_aids"]["value"] = 0.05
    # Only off-ART population (dim5=0)
    eng_a.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 10_000
    eng_a._step_disease_progression()

    # Engine B: on-ART key present with 0.005 (lower rate), but no on-ART population
    eng_b = make_zero_step_engine()
    eng_b.config["disease"]["progression"]["chronic_to_aids"]["value"] = 0.05
    eng_b.config["disease"]["progression"]["chronic_to_aids_on_art"] = {"value": 0.005}
    eng_b.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 10_000  # only off-ART
    eng_b._step_disease_progression()

    _, _, ai_off_a, _ = get_chronic_and_aids(eng_a)
    _, _, ai_off_b, _ = get_chronic_and_aids(eng_b)
    diff = abs(ai_off_a - ai_off_b)

    print(f"  AIDS off-ART (no key):     {ai_off_a:,}")
    print(f"  AIDS off-ART (key=0.005):  {ai_off_b:,}")
    print(f"  Difference: {diff}  (must be 0)")

    assert diff == 0, f"FAIL: off-ART AIDS changed by {diff} when on-ART key added"
    print(f"  ✓ PASS: off-ART progression rate unaffected by on-ART key")
    return True


# ---------------------------------------------------------------------------
# Test 4: Mass balance during one progression step
# ---------------------------------------------------------------------------

def test_mass_balance():
    """
    The progression step moves people between HIV stages but must never
    create or destroy individuals. Total population must be conserved.
    """
    print("\n" + "="*60)
    print("TEST 4: Mass Balance During Progression")
    print("="*60)

    engine = make_zero_step_engine()
    chronic_idx = engine.groups.hiv_to_idx["Chronic"]

    # Seed 1000 in Chronic off-ART and 1000 in Chronic on-ART
    engine.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 1000
    engine.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = 1000

    hiv_before   = int(np.sum(engine.state.population[:, :, :, 1:, :, :, :]))
    total_before = int(np.sum(engine.state.population))

    engine._step_disease_progression()

    hiv_after   = int(np.sum(engine.state.population[:, :, :, 1:, :, :, :]))
    total_after = int(np.sum(engine.state.population))

    print(f"  HIV+ before step:  {hiv_before:,}")
    print(f"  HIV+ after step:   {hiv_after:,}")
    print(f"  Total before:      {total_before:,}")
    print(f"  Total after:       {total_after:,}")

    assert hiv_before == hiv_after,     f"FAIL: HIV+ count changed {hiv_before}→{hiv_after}"
    assert total_before == total_after, f"FAIL: total pop changed {total_before}→{total_after}"
    print("  ✓ PASS: HIV+ and total population conserved during progression")
    return True


# ---------------------------------------------------------------------------
# Test 5: Zero on-ART rate → no progressions from on-ART Chronic
# ---------------------------------------------------------------------------

def test_zero_on_art_rate():
    """
    chronic_to_aids_on_art = 0 must produce zero AIDS from the on-ART
    Chronic pool regardless of pool size or simulation length.
    """
    print("\n" + "="*60)
    print("TEST 5: Zero On-ART Rate")
    print("="*60)

    N = 50_000
    engine = make_zero_step_engine()
    chronic_idx = engine.groups.hiv_to_idx["Chronic"]

    engine.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = N  # on-ART only

    engine.config["disease"]["progression"]["chronic_to_aids_on_art"] = {"value": 0.0}
    engine.config["disease"]["progression"]["chronic_to_aids"]["value"] = 0.05

    engine._step_disease_progression()

    aids_idx = engine.groups.hiv_to_idx["AIDS"]
    aids_on_art = int(np.sum(engine.state.population[:, :, :, aids_idx, :, 1, :]))

    print(f"  On-ART Chronic pool:         {N:,}")
    print(f"  AIDS from on-ART (must be 0): {aids_on_art:,}")

    assert aids_on_art == 0, f"FAIL: {aids_on_art} on-ART progressions despite rate=0"
    print("  ✓ PASS: zero progressions from on-ART Chronic when rate=0")
    return True


# ---------------------------------------------------------------------------
# Test 6: Equal rates → symmetric progression
# ---------------------------------------------------------------------------

def test_equal_rates_symmetric():
    """
    Equal off-ART and on-ART rates with equal pool sizes must produce
    identical AIDS counts from each pool (deterministic mode).
    """
    print("\n" + "="*60)
    print("TEST 6: Equal Rates Produce Symmetric Progression")
    print("="*60)

    engine = make_zero_step_engine()
    chronic_idx = engine.groups.hiv_to_idx["Chronic"]
    aids_idx    = engine.groups.hiv_to_idx["AIDS"]

    engine.state.population[0, 0, 0, chronic_idx, 0, 0, 0] = 5000  # off-ART
    engine.state.population[0, 0, 0, chronic_idx, 0, 1, 0] = 5000  # on-ART

    RATE = 0.05
    engine.config["disease"]["progression"]["chronic_to_aids"]["value"] = RATE
    engine.config["disease"]["progression"]["chronic_to_aids_on_art"] = {"value": RATE}

    engine._step_disease_progression()

    pop = engine.state.population
    aids_off = int(np.sum(pop[:, :, :, aids_idx, :, 0, :]))
    aids_on  = int(np.sum(pop[:, :, :, aids_idx, :, 1, :]))
    diff = abs(aids_off - aids_on)

    print(f"  AIDS from off-ART pool: {aids_off:,}")
    print(f"  AIDS from on-ART pool:  {aids_on:,}")
    print(f"  Difference:             {diff} (must be 0 in deterministic mode)")

    assert diff == 0, f"FAIL: asymmetric results ({aids_off} vs {aids_on}) with equal rates"
    print("  ✓ PASS: identical progression with equal rates")
    return True


# ---------------------------------------------------------------------------
# Test 7: Full end-to-end smoke test
# ---------------------------------------------------------------------------

def test_full_run_smoke():
    """
    A full 20-year simulation with known ART-dependent progression rates
    must complete without error and produce valid epidemiological outputs.
    """
    print("\n" + "="*60)
    print("TEST 7: Full Run Smoke Test (V11 config)")
    print("="*60)

    config = load_base_config()
    config["run_control"]["start_year"]["value"] = 1970
    config["population"]["initial_hiv_prevalence"]["value"] = 0.01
    config["transmission_model"]["disease"]["progression"]["chronic_to_aids"]["value"] = 0.05
    config["transmission_model"]["disease"]["progression"]["chronic_to_aids_on_art"]["value"] = 0.005

    engine = run_short_sim(config, years=20)
    outputs = engine.outputs

    prevalence  = outputs["prevalence"]
    incidence   = outputs["incidence_rate"]
    total_pop   = outputs["total_population"]

    print(f"  Steps completed:         {len(prevalence)}")
    print(f"  Final prevalence:        {prevalence[-1]:.4%}")
    print(f"  Peak incidence rate:     {max(incidence):.4%}")
    print(f"  Any negative prevalence: {any(p < 0 for p in prevalence)}")
    print(f"  Final total population:  {total_pop[-1]:,}")

    assert len(prevalence) > 0,                 "FAIL: no output steps"
    assert all(p >= 0 for p in prevalence),     "FAIL: negative prevalence"
    assert all(i >= 0 for i in incidence),      "FAIL: negative incidence"
    assert all(p <= 1 for p in prevalence),     "FAIL: prevalence > 1"
    assert total_pop[-1] > 0,                   "FAIL: zero population at end"

    print("  ✓ PASS: simulation completed, all outputs valid")
    return True


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    tests = [
        ("Backward Compatibility",       test_backward_compatibility),
        ("ART Slows Progression",         test_art_slows_progression),
        ("Off-ART Rate Unchanged",        test_off_art_rate_unchanged),
        ("Mass Balance",                  test_mass_balance),
        ("Zero On-ART Rate",              test_zero_on_art_rate),
        ("Equal Rates Symmetric",         test_equal_rates_symmetric),
        ("Full Run Smoke Test",           test_full_run_smoke),
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "="*60)
    print("ART-DEPENDENT PROGRESSION TEST SUITE (V11-native)")
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
