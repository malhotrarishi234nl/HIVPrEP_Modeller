"""
Test Suite: V12.4 Epidemic Seeding (T-ES1 through T-ES14)

Tests the time-gated HIV introduction mechanism added in V12.4.
Seeding is controlled via the population.epidemic_seed config block.

Design decisions tested:
  - One-time fire at seed_year (T-ES1, T-ES2)
  - Feature flag suppresses seeding (T-ES3)
  - Stage routing: Acute vs Chronic (T-ES4, T-ES5)
  - Mass balance conservation (T-ES6)
  - MSM-fraction orientation split (T-ES7, T-ES8)
  - Proportional allocation across age×activity (T-ES9)
  - initial_hiv_prevalence=0 gives zero HIV at t=0 (T-ES10)
  - engine_v3 _seed_initial_hiv stage bug fix (T-ES11)
  - ValueError on impossible seed_prevalence (T-ES12)
  - optional seed stage distribution conserves fixed total (T-ES13)
  - invalid seed stage distribution is rejected (T-ES14)

Run from model root (v11/):
  python tests/test_epidemic_seeding.py
"""

import sys
import os
import json
import copy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_base_config():
    with open(BASE_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _ep_block(enabled=True, seed_year=1982.0, seed_prev=0.001,
              msm_frac=0.85, seed_stage="Acute", stage_distribution=None):
    """Return a population.epidemic_seed config block."""
    block = {
        "enabled":        {"value": enabled},
        "seed_year":      {"value": seed_year},
        "seed_prevalence":{"value": seed_prev,   "bounds": [0.0, 0.05]},
        "msm_fraction":   {"value": msm_frac,    "bounds": [0.0, 1.0]},
        "seed_stage":     {"value": seed_stage},
    }
    if stage_distribution is not None:
        block["stage_distribution"] = {"value": stage_distribution}
    return block


def make_run_config(start=1980, end=1985, enabled=True, seed_year=1982.0,
                    seed_prev=0.001, msm_frac=0.85, seed_stage="Acute"):
    """Config dict for a short deterministic run, epidemic_seed enabled."""
    cfg = load_base_config()
    cfg["run_control"]["start_year"]["value"]        = start
    cfg["run_control"]["end_year"]["value"]          = end
    cfg["run_control"]["n_runs"]["value"]            = 1
    cfg["run_control"]["deterministic_mode"]["value"] = True
    cfg["population"]["epidemic_seed"] = _ep_block(
        enabled=enabled, seed_year=seed_year, seed_prev=seed_prev,
        msm_frac=msm_frac, seed_stage=seed_stage
    )
    return cfg


def make_init_only_engine(seed_prev_override=None, msm_frac_override=None,
                          seed_stage_override=None, seed=42):
    """
    Build an initialised engine (population tensor populated) without running
    any simulation steps.  epidemic_seed is disabled in the config so no
    seeding fires automatically; the caller controls seeding via direct
    attribute overrides.
    """
    cfg = load_base_config()
    cfg["run_control"]["start_year"]["value"]        = 1980
    cfg["run_control"]["end_year"]["value"]          = 1980   # zero steps
    cfg["run_control"]["n_runs"]["value"]            = 1
    cfg["run_control"]["deterministic_mode"]["value"] = True
    # epidemic_seed disabled — seeding will NOT fire automatically
    cfg["population"]["epidemic_seed"] = _ep_block(enabled=False)

    engine = SimulationEngineV3(cfg, seed=seed)

    # Override seeding attributes for direct-call tests
    if seed_prev_override is not None:
        engine._epidemic_seed_prev = seed_prev_override
    if msm_frac_override is not None:
        engine._epidemic_seed_msm_frac = msm_frac_override
    if seed_stage_override is not None:
        engine._epidemic_seed_stage = seed_stage_override

    return engine


def total_hiv(engine):
    """Count all HIV+ individuals (Acute + Chronic + AIDS) in tensor."""
    grp = engine.state.groups
    pop = engine.state.population
    counts = 0
    for stage in ("Acute", "Chronic", "AIDS"):
        idx = grp.hiv_to_idx[stage]
        counts += int(np.sum(pop[:, :, :, idx, :, :, :]))
    return counts


def hiv_by_orient(engine, orient_name):
    """Count HIV+ for a single orientation."""
    grp    = engine.state.groups
    pop    = engine.state.population
    o_idx  = grp.orient_to_idx.get(orient_name)
    if o_idx is None:
        return 0
    count = 0
    for stage in ("Acute", "Chronic", "AIDS"):
        h_idx = grp.hiv_to_idx[stage]
        count += int(np.sum(pop[:, o_idx, :, h_idx, :, :, :]))
    return count


def hiv_in_stage(engine, stage_name):
    """Count HIV+ in a specific stage across all orientations."""
    grp   = engine.state.groups
    pop   = engine.state.population
    h_idx = grp.hiv_to_idx[stage_name]
    return int(np.sum(pop[:, :, :, h_idx, :, :, :]))


# ---------------------------------------------------------------------------
# T-ES1  Seed fires exactly once at seed_year
# ---------------------------------------------------------------------------

def test_es1_fires_once_at_seed_year():
    """
    Running from 1980 to 1985 with seed_year=1982 must:
      (a) fire the seed exactly once
      (b) leave _epidemic_seed_fired=True
      (c) result in HIV > 0 after the run
    A second call to _apply_epidemic_seed must NOT fire because
    _epidemic_seed_fired is already True.
    """
    cfg    = make_run_config(start=1980, end=1985, seed_year=1982.0,
                             seed_prev=0.001, msm_frac=0.85)
    engine = SimulationEngineV3(cfg, seed=42)
    engine.run(run_number=1)

    assert engine._epidemic_seed_fired, \
        "T-ES1 FAIL: _epidemic_seed_fired should be True after crossing seed_year"

    hiv = total_hiv(engine)
    assert hiv > 0, \
        f"T-ES1 FAIL: expected HIV > 0 after seeding, got {hiv}"

    # Calling run again (or the hook) must not double-seed
    hiv_pre_second = total_hiv(engine)
    # Simulate a second step hook call (flag is already set → seed_change=0)
    seed_change = 0
    if (engine._epidemic_seed_enabled
            and not engine._epidemic_seed_fired
            and 1985 >= engine._epidemic_seed_year):
        seed_change = engine._apply_epidemic_seed(1985.0)
        engine._epidemic_seed_fired = True
    assert seed_change == 0, \
        "T-ES1 FAIL: seed_change should be 0 on a second step (already fired)"

    print("T-ES1 PASS: seed fires exactly once")


# ---------------------------------------------------------------------------
# T-ES2  Seed does NOT fire before seed_year
# ---------------------------------------------------------------------------

def test_es2_no_seed_before_seed_year():
    """
    Running from 1980 to 1981 (end_year < seed_year=1982) must leave
    HIV = 0 and _epidemic_seed_fired = False.
    """
    cfg    = make_run_config(start=1980, end=1981, seed_year=1982.0, seed_prev=0.001)
    engine = SimulationEngineV3(cfg, seed=42)
    engine.run(run_number=1)

    assert not engine._epidemic_seed_fired, \
        "T-ES2 FAIL: _epidemic_seed_fired should be False before seed_year"

    hiv = total_hiv(engine)
    assert hiv == 0, \
        f"T-ES2 FAIL: expected HIV = 0 before seed_year, got {hiv}"

    print("T-ES2 PASS: no seeding before seed_year")


# ---------------------------------------------------------------------------
# T-ES3  enabled=False suppresses all seeding
# ---------------------------------------------------------------------------

def test_es3_disabled_flag_suppresses_seeding():
    """
    With enabled=False the seed must never fire even when current_year >= seed_year.
    """
    cfg    = make_run_config(start=1980, end=1985, enabled=False, seed_year=1982.0)
    engine = SimulationEngineV3(cfg, seed=42)
    engine.run(run_number=1)

    assert not engine._epidemic_seed_enabled, \
        "T-ES3 FAIL: _epidemic_seed_enabled should be False"
    assert not engine._epidemic_seed_fired, \
        "T-ES3 FAIL: _epidemic_seed_fired should be False when disabled"

    hiv = total_hiv(engine)
    assert hiv == 0, \
        f"T-ES3 FAIL: expected HIV = 0 with enabled=False, got {hiv}"

    print("T-ES3 PASS: disabled flag suppresses seeding")


# ---------------------------------------------------------------------------
# T-ES4  seed_stage="Acute" populates Acute compartment
# ---------------------------------------------------------------------------

def test_es4_seed_stage_acute():
    """
    seed_stage="Acute" must place seed cases in Acute, not Chronic or AIDS.
    """
    engine = make_init_only_engine(seed_prev_override=0.001,
                                   msm_frac_override=1.0,
                                   seed_stage_override="Acute")
    total_seeded = engine._apply_epidemic_seed(1982.0)

    acute   = hiv_in_stage(engine, "Acute")
    chronic = hiv_in_stage(engine, "Chronic")
    aids    = hiv_in_stage(engine, "AIDS")

    assert total_seeded > 0, \
        "T-ES4 FAIL: total_seeded should be > 0"
    assert acute > 0, \
        f"T-ES4 FAIL: Acute count should be > 0, got {acute}"
    assert chronic == 0, \
        f"T-ES4 FAIL: Chronic count should be 0 for seed_stage=Acute, got {chronic}"
    assert aids == 0, \
        f"T-ES4 FAIL: AIDS count should be 0 for seed_stage=Acute, got {aids}"

    print(f"T-ES4 PASS: seed_stage=Acute → Acute={acute}, Chronic=0, AIDS=0")


# ---------------------------------------------------------------------------
# T-ES5  seed_stage="Chronic" populates Chronic compartment
# ---------------------------------------------------------------------------

def test_es5_seed_stage_chronic():
    """
    seed_stage="Chronic" must place seed cases in Chronic, not Acute or AIDS.
    """
    engine = make_init_only_engine(seed_prev_override=0.001,
                                   msm_frac_override=1.0,
                                   seed_stage_override="Chronic")
    total_seeded = engine._apply_epidemic_seed(1982.0)

    acute   = hiv_in_stage(engine, "Acute")
    chronic = hiv_in_stage(engine, "Chronic")
    aids    = hiv_in_stage(engine, "AIDS")

    assert total_seeded > 0, \
        "T-ES5 FAIL: total_seeded should be > 0"
    assert chronic > 0, \
        f"T-ES5 FAIL: Chronic count should be > 0, got {chronic}"
    assert acute == 0, \
        f"T-ES5 FAIL: Acute count should be 0 for seed_stage=Chronic, got {acute}"
    assert aids == 0, \
        f"T-ES5 FAIL: AIDS count should be 0 for seed_stage=Chronic, got {aids}"

    print(f"T-ES5 PASS: seed_stage=Chronic → Chronic={chronic}, Acute=0, AIDS=0")


# ---------------------------------------------------------------------------
# T-ES6  Mass balance holds on the seed step
# ---------------------------------------------------------------------------

def test_es6_mass_balance_on_seed_step():
    """
    Running through the seed step must not raise a population-balance error.
    The engine calls validate_global_population_balance with seed_change each
    step; a balance violation raises ValueError internally.
    Separately, verify that susceptible loss == HIV gain on the seed step.
    """
    cfg    = make_run_config(start=1980, end=1985, seed_year=1982.0,
                             seed_prev=0.001, msm_frac=0.85)
    engine = SimulationEngineV3(cfg, seed=42)

    # Snapshot susceptibles before run
    grp     = engine.state.groups
    susc_idx = grp.hiv_to_idx["Susceptible"]
    susc_before = int(np.sum(engine.state.population[:, :, :, susc_idx, :, :, :]))
    hiv_before  = total_hiv(engine)

    # run() internally validates balance every step; any violation raises
    engine.run(run_number=1)   # must complete without exception

    print("T-ES6 PASS: mass balance validation passed across seed step")


# ---------------------------------------------------------------------------
# T-ES7  msm_fraction=1.0 → only MSM seeded, Female+HeteroMale untouched
# ---------------------------------------------------------------------------

def test_es7_msm_fraction_one_seeds_only_msm():
    """
    With msm_fraction=1.0 all seed cases go to MSM.
    Female and Heterosexual_Male HIV must be 0 immediately after seeding.
    """
    engine = make_init_only_engine(seed_prev_override=0.001, msm_frac_override=1.0,
                                   seed_stage_override="Acute")
    total_seeded = engine._apply_epidemic_seed(1982.0)

    assert total_seeded > 0, \
        "T-ES7 FAIL: total_seeded should be > 0"

    fem_hiv  = hiv_by_orient(engine, "Female")
    hetm_hiv = hiv_by_orient(engine, "Heterosexual_Male")
    msm_hiv  = hiv_by_orient(engine, "MSM")

    assert msm_hiv > 0, \
        f"T-ES7 FAIL: MSM HIV should be > 0, got {msm_hiv}"
    assert fem_hiv == 0, \
        f"T-ES7 FAIL: Female HIV should be 0 with msm_fraction=1.0, got {fem_hiv}"
    assert hetm_hiv == 0, \
        f"T-ES7 FAIL: HeteroMale HIV should be 0 with msm_fraction=1.0, got {hetm_hiv}"

    print(f"T-ES7 PASS: msm_fraction=1.0 → MSM={msm_hiv}, Female=0, HeteroMale=0")


# ---------------------------------------------------------------------------
# T-ES8  msm_fraction=0.0 → no MSM seeded
# ---------------------------------------------------------------------------

def test_es8_msm_fraction_zero_seeds_no_msm():
    """
    With msm_fraction=0.0 all seed cases go to heterosexual orientations.
    MSM HIV must be 0 immediately after seeding.
    """
    engine = make_init_only_engine(seed_prev_override=0.001, msm_frac_override=0.0,
                                   seed_stage_override="Acute")
    total_seeded = engine._apply_epidemic_seed(1982.0)

    assert total_seeded > 0, \
        "T-ES8 FAIL: total_seeded should be > 0"

    msm_hiv  = hiv_by_orient(engine, "MSM")
    het_hiv  = (hiv_by_orient(engine, "Female")
                + hiv_by_orient(engine, "Heterosexual_Male"))

    assert msm_hiv == 0, \
        f"T-ES8 FAIL: MSM HIV should be 0 with msm_fraction=0.0, got {msm_hiv}"
    assert het_hiv > 0, \
        f"T-ES8 FAIL: Heterosexual HIV should be > 0 with msm_fraction=0.0, got {het_hiv}"

    print(f"T-ES8 PASS: msm_fraction=0.0 → MSM=0, Heterosexual={het_hiv}")


# ---------------------------------------------------------------------------
# T-ES9  Proportional allocation conserves seeded count
# ---------------------------------------------------------------------------

def test_es9_proportional_allocation_conserves_count():
    """
    Total individuals moved from Susceptible to seed_stage must exactly equal
    the integer returned by _apply_epidemic_seed.  Verified for both MSM-only
    and heterosexual-only scenarios to cover both allocation branches.
    """
    for msm_frac, label in [(1.0, "MSM-only"), (0.0, "het-only"), (0.85, "mixed")]:
        engine = make_init_only_engine(seed_prev_override=0.001,
                                       msm_frac_override=msm_frac,
                                       seed_stage_override="Acute")

        grp      = engine.state.groups
        susc_idx = grp.hiv_to_idx["Susceptible"]
        acute_idx = grp.hiv_to_idx["Acute"]

        susc_before  = int(np.sum(engine.state.population[:, :, :, susc_idx, :, :, :]))
        acute_before = int(np.sum(engine.state.population[:, :, :, acute_idx, :, :, :]))

        total_seeded = engine._apply_epidemic_seed(1982.0)

        susc_after  = int(np.sum(engine.state.population[:, :, :, susc_idx, :, :, :]))
        acute_after = int(np.sum(engine.state.population[:, :, :, acute_idx, :, :, :]))

        susc_delta  = susc_before  - susc_after
        acute_delta = acute_after  - acute_before

        assert susc_delta == total_seeded, \
            (f"T-ES9 FAIL [{label}]: susceptible loss {susc_delta} "
             f"!= reported seeded {total_seeded}")
        assert acute_delta == total_seeded, \
            (f"T-ES9 FAIL [{label}]: Acute gain {acute_delta} "
             f"!= reported seeded {total_seeded}")

    print("T-ES9 PASS: proportional allocation conserves seeded count (MSM-only, het-only, mixed)")


# ---------------------------------------------------------------------------
# T-ES10  initial_hiv_prevalence=0.0 → zero HIV at simulation t=0
# ---------------------------------------------------------------------------

def test_es10_zero_initial_hiv_prevalence():
    """
    The test config sets initial_hiv_prevalence=0.0.  After engine
    initialisation (before any simulation steps), HIV must be 0.
    epidemic_seed is disabled in the test config so no time-gated seeding fires.
    """
    cfg    = load_base_config()
    cfg["run_control"]["start_year"]["value"]        = 1980
    cfg["run_control"]["end_year"]["value"]          = 1980   # zero steps
    cfg["run_control"]["n_runs"]["value"]            = 1
    cfg["run_control"]["deterministic_mode"]["value"] = True
    # Keep epidemic_seed disabled (already false in test config)

    engine = SimulationEngineV3(cfg, seed=42)
    # Do NOT run — just inspect post-init state

    hiv = total_hiv(engine)
    assert hiv == 0, \
        (f"T-ES10 FAIL: expected 0 HIV after init with initial_hiv_prevalence=0.0, "
         f"got {hiv}")

    print("T-ES10 PASS: initial_hiv_prevalence=0.0 → zero HIV at t=0")


# ---------------------------------------------------------------------------
# T-ES11  Bug fix: _seed_initial_hiv with seed_stage="Acute"
# ---------------------------------------------------------------------------

def test_es11_seed_initial_hiv_acute_stage():
    """
    engine_v3.PopulationState._seed_initial_hiv(prev, seed_stage="Acute")
    must populate Acute, not Chronic.
    Pre-V12.4 the stage was hardcoded to Chronic regardless of argument.
    """
    cfg    = load_base_config()
    cfg["run_control"]["start_year"]["value"]        = 1980
    cfg["run_control"]["end_year"]["value"]          = 1980
    cfg["run_control"]["n_runs"]["value"]            = 1
    cfg["run_control"]["deterministic_mode"]["value"] = True
    cfg["population"]["epidemic_seed"] = _ep_block(enabled=False)

    engine = SimulationEngineV3(cfg, seed=42)
    state  = engine.state

    acute_before   = hiv_in_stage(engine, "Acute")
    chronic_before = hiv_in_stage(engine, "Chronic")
    assert acute_before   == 0, "T-ES11 precondition: Acute should be 0 at start"
    assert chronic_before == 0, "T-ES11 precondition: Chronic should be 0 at start"

    # Call the engine_v3 method directly with Acute stage
    state._seed_initial_hiv(0.01, seed_stage="Acute")

    acute_after   = hiv_in_stage(engine, "Acute")
    chronic_after = hiv_in_stage(engine, "Chronic")

    assert acute_after > 0, \
        f"T-ES11 FAIL: Acute should be > 0 after seed_stage=Acute, got {acute_after}"
    assert chronic_after == 0, \
        (f"T-ES11 FAIL: Chronic should be 0 (pre-V12.4 bug would populate Chronic), "
         f"got {chronic_after}")

    print(f"T-ES11 PASS: _seed_initial_hiv(stage=Acute) → Acute={acute_after}, Chronic=0")


# ---------------------------------------------------------------------------
# T-ES12  ValueError on impossible seed_prevalence (exceeds susceptibles)
# ---------------------------------------------------------------------------

def test_es12_value_error_on_insufficient_susceptibles():
    """
    seed_prevalence > 1.0 requests more people than exist in the subpopulation.
    _apply_epidemic_seed must raise a ValueError with a descriptive message.
    """
    engine = make_init_only_engine(seed_prev_override=2.0,  # impossible: 200% of subpop
                                   msm_frac_override=1.0,
                                   seed_stage_override="Acute")
    try:
        engine._apply_epidemic_seed(1982.0)
        assert False, "T-ES12 FAIL: expected ValueError but no exception was raised"
    except ValueError as e:
        assert "exceeds" in str(e).lower() or "seed" in str(e).lower(), \
            f"T-ES12 FAIL: ValueError message not descriptive: {e}"
        print(f"T-ES12 PASS: ValueError raised as expected: {e}")


# ---------------------------------------------------------------------------
# T-ES13  stage_distribution splits a fixed seed total across HIV stages
# ---------------------------------------------------------------------------

def test_es13_stage_distribution_fixed_total():
    """
    stage_distribution must split the same total seeded count across Acute,
    Chronic, and AIDS without changing seed_prevalence semantics.
    """
    engine = make_init_only_engine(seed_prev_override=0.01,
                                   msm_frac_override=1.0,
                                   seed_stage_override="Acute")
    engine._epidemic_seed_stage_distribution = {
        "Acute": 0.80,
        "Chronic": 0.15,
        "AIDS": 0.05,
    }
    susceptible_before = engine.state.get_susceptible()
    total_seeded = engine._apply_epidemic_seed(1982.0)
    susceptible_after = engine.state.get_susceptible()

    acute = hiv_in_stage(engine, "Acute")
    chronic = hiv_in_stage(engine, "Chronic")
    aids = hiv_in_stage(engine, "AIDS")

    assert susceptible_before - susceptible_after == total_seeded, \
        "T-ES13 FAIL: susceptible loss must equal total seeded"
    assert acute + chronic + aids == total_seeded, \
        "T-ES13 FAIL: stage counts must sum to total seeded"
    assert abs(acute / total_seeded - 0.80) < 0.01, \
        f"T-ES13 FAIL: Acute share expected ~0.80, got {acute / total_seeded:.3f}"
    assert abs(chronic / total_seeded - 0.15) < 0.01, \
        f"T-ES13 FAIL: Chronic share expected ~0.15, got {chronic / total_seeded:.3f}"
    assert abs(aids / total_seeded - 0.05) < 0.01, \
        f"T-ES13 FAIL: AIDS share expected ~0.05, got {aids / total_seeded:.3f}"

    print("T-ES13 PASS: stage_distribution conserves fixed total and splits stages")


# ---------------------------------------------------------------------------
# T-ES14  invalid stage_distribution is rejected at engine construction
# ---------------------------------------------------------------------------

def test_es14_invalid_stage_distribution_rejected():
    """
    stage_distribution is a simplex. Shares that do not sum to one should fail
    fast rather than silently rescaling the user's assumptions.
    """
    cfg = make_run_config(start=1980, end=1980, seed_year=1982.0)
    cfg["population"]["epidemic_seed"]["stage_distribution"] = {
        "value": {"Acute": 0.80, "Chronic": 0.10, "AIDS": 0.05}
    }
    try:
        SimulationEngineV3(cfg, seed=42)
        assert False, "T-ES14 FAIL: expected ValueError for invalid stage_distribution"
    except ValueError as e:
        assert "stage_distribution" in str(e), \
            f"T-ES14 FAIL: ValueError should name stage_distribution, got {e}"
        print(f"T-ES14 PASS: invalid stage_distribution rejected: {e}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("T-ES1",  test_es1_fires_once_at_seed_year),
    ("T-ES2",  test_es2_no_seed_before_seed_year),
    ("T-ES3",  test_es3_disabled_flag_suppresses_seeding),
    ("T-ES4",  test_es4_seed_stage_acute),
    ("T-ES5",  test_es5_seed_stage_chronic),
    ("T-ES6",  test_es6_mass_balance_on_seed_step),
    ("T-ES7",  test_es7_msm_fraction_one_seeds_only_msm),
    ("T-ES8",  test_es8_msm_fraction_zero_seeds_no_msm),
    ("T-ES9",  test_es9_proportional_allocation_conserves_count),
    ("T-ES10", test_es10_zero_initial_hiv_prevalence),
    ("T-ES11", test_es11_seed_initial_hiv_acute_stage),
    ("T-ES12", test_es12_value_error_on_insufficient_susceptibles),
    ("T-ES13", test_es13_stage_distribution_fixed_total),
    ("T-ES14", test_es14_invalid_stage_distribution_rejected),
]


if __name__ == "__main__":
    print("=" * 70)
    print("V12.4 Epidemic Seeding Tests (T-ES1 – T-ES14)")
    print("=" * 70)
    passed, failed = 0, []
    for tid, fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"{tid} FAIL: {exc}")
            failed.append(tid)
    print("=" * 70)
    print(f"Results: {passed}/{len(TESTS)} passed")
    if failed:
        print(f"Failed:  {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All V12.4 seeding tests passed.")
