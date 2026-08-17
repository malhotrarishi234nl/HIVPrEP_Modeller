"""
test_migration.py — Test suite for V9 Net Migration Module

Tests:
  1.  Disabled module → no population change
  2.  Year-specific mode: correct value per year; missing year → 0
  3.  Rate mode: proportional to total population
  4.  Flat mode: constant annual migration
  5.  Negative year-specific values → proportional removal
  6.  Mirror profile: allocation proportional to population distribution
  7.  Working-age profile: only adult bins in range; no child bins
  8.  Custom profile: normalised correctly and applied
  9.  Custom profile: invalid bin name raises MigrationConfigError
 10.  Positive migration: all entrants are Susceptible/Undiag/No-ART/No-PrEP
 11.  Positive migration: childhood entrants go to childhood pool (not adult tensor)
 12.  Positive migration: adult entrants respect MSM proportion
 13.  Negative migration: proportional — no negative cells remain
 14.  Negative migration: capped at 90 % with RuntimeWarning
 15.  Conservation: net_actual returned by apply() == actual Δpopulation
 16.  Mass-balance passes with migration enabled (full simulation smoke test)
 17.  HIV prevalence is unchanged after migration (susceptible-only entrants)
"""

import json
import sys
import os
import warnings
import tempfile

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from migration import (
    MigrationModule,
    MigrationConfigError,
    MigrationIntegrityError,
    _distribute_int,
    _allocate_by_weights,
    _normalise_weights,
)
from run_v3 import SimulationEngineV3


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_base_config():
    path = os.path.join(ROOT, "configs", "v11_configuration.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_engine(config_override: dict = None):
    """Build a SimulationEngineV3 from a temp config file."""
    cfg = load_base_config()
    if config_override:
        # V11 stores migration under demography.migration (not top-level).
        # ConfigReader.migration() reads from cfg["demography"]["migration"],
        # so route any "migration" override to the correct V11 key path.
        if "migration" in config_override:
            cfg["demography"]["migration"] = config_override.pop("migration")
        # Remaining overrides are applied at top level.
        for k, v in config_override.items():
            cfg[k] = v
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", dir="/tmp", delete=False
    ) as tf:
        json.dump(cfg, tf)
        tf_path = tf.name
    return SimulationEngineV3(tf_path, seed=42)


def make_minimal_migration_config(
    mode="flat", flat=1000, profile="mirror"
) -> dict:
    """Return a minimal migration block with sensible defaults."""
    return {
        "migration": {
            "enabled": True,
            "quantity": {
                "mode": mode,
                "flat": flat,
                "rate": 0.003,
                "year_specific": {
                    "1970": 32438,
                    "1975": 68569,
                    "2004": -9230,
                    "2005": -22496,
                },
            },
            "profile": {
                "mode": profile,
                "working_age": {"min_age": 20, "max_age": 40},
                "custom_weights": {
                    "20-24": {"female": 0.10, "male": 0.12},
                    "25-29": {"female": 0.15, "male": 0.18},
                },
            },
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Disabled → no change
# ─────────────────────────────────────────────────────────────────────────────

def test_disabled_no_change():
    print("\nTEST 1: Disabled module → no population change")
    # V11 canonical config has migration enabled; explicitly disable to test the
    # disabled code path (apply() returns 0 and leaves population unchanged).
    eng = make_engine({"migration": {"enabled": False}})

    assert not eng.migration.enabled, "Migration should be disabled when enabled=False"

    pop_before = int(np.sum(eng.state.population))
    child_before = eng.demography.childhood.get_total()

    result = eng.migration.apply(
        eng.state.population,
        eng.demography.childhood,
        2000.0,
        eng.time,
        eng.rng,
        eng.deterministic,
    )

    assert result == 0
    assert int(np.sum(eng.state.population)) == pop_before
    assert eng.demography.childhood.get_total() == child_before
    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Year-specific mode values
# ─────────────────────────────────────────────────────────────────────────────

def test_year_specific_lookup():
    print("\nTEST 2: Year-specific mode: correct value per year; missing → 0")
    eng = make_engine(make_minimal_migration_config(mode="year_specific"))
    m = eng.migration

    assert m.quantity_mode == "year_specific"

    # Known year
    net_1970 = m._get_annual_net(eng.state.population, eng.demography.childhood, 1970.0)
    assert net_1970 == 32438.0, f"Expected 32438, got {net_1970}"

    # Sub-year (Q2 1975 → floor → 1975)
    net_1975q2 = m._get_annual_net(eng.state.population, eng.demography.childhood, 1975.25)
    assert net_1975q2 == 68569.0

    # Missing year → 0
    net_1980 = m._get_annual_net(eng.state.population, eng.demography.childhood, 1980.0)
    assert net_1980 == 0.0, f"Missing year should return 0, got {net_1980}"

    # Negative year
    net_neg = m._get_annual_net(eng.state.population, eng.demography.childhood, 2004.0)
    assert net_neg == -9230.0

    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Rate mode
# ─────────────────────────────────────────────────────────────────────────────

def test_rate_mode():
    print("\nTEST 3: Rate mode: net = rate × total_population")
    eng = make_engine(make_minimal_migration_config(mode="rate"))
    m = eng.migration

    total_pop = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    expected_annual = total_pop * 0.003

    got = m._get_annual_net(eng.state.population, eng.demography.childhood, 2000.0)
    assert abs(got - expected_annual) < 1.0, f"Expected ~{expected_annual:.0f}, got {got}"
    print(f"  Rate 0.3%/yr on {total_pop:,} → {got:,.0f}/yr")
    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Flat mode
# ─────────────────────────────────────────────────────────────────────────────

def test_flat_mode():
    print("\nTEST 4: Flat mode: constant annual migration")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=44000))
    m = eng.migration

    for yr in [1970.0, 1985.5, 2020.0]:
        got = m._get_annual_net(eng.state.population, eng.demography.childhood, yr)
        assert got == 44000.0, f"Flat 44000 expected at {yr}, got {got}"
    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Negative migration → proportional removal, no negative cells
# ─────────────────────────────────────────────────────────────────────────────

def test_negative_migration_no_negatives():
    print("\nTEST 5: Negative migration → proportional removal, no negative cells")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=-50000))

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    net = eng.migration.apply(
        eng.state.population,
        eng.demography.childhood,
        2000.0,
        eng.time,
        eng.rng,
        eng.deterministic,
    )

    # Population decreased
    pop_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    assert pop_after < pop_before, "Population should decrease with negative migration"
    assert net < 0, "Return value should be negative"

    # No negative cells
    assert np.all(eng.state.population >= 0), "Adult tensor has negative cells!"
    assert np.all(eng.demography.childhood.population >= 0), "Childhood pool has negative cells!"

    print(f"  Removed {abs(net):,} people. No negative cells. ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Mirror profile proportional allocation
# ─────────────────────────────────────────────────────────────────────────────

def test_mirror_profile_proportional():
    print("\nTEST 6: Mirror profile: allocation proportional to population")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=100000, profile="mirror"))
    m = eng.migration

    weights = m._mirror_weights(eng.state.population, eng.demography.childhood)

    # All weights sum to 1.0
    total_w = sum(v["female"] + v["male"] for v in weights.values())
    assert abs(total_w - 1.0) < 1e-9, f"Weights should sum to 1, got {total_w}"

    # Should include both adult and childhood bins
    all_labels = set(weights.keys())
    assert "15-19" in all_labels, "Adult bins should be in mirror weights"
    assert "5-9"   in all_labels, "Childhood bins should be in mirror weights"

    print(f"  {len(weights)} bins in mirror weights, sum = {total_w:.6f} ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Working-age profile — only adult bins in range, no children
# ─────────────────────────────────────────────────────────────────────────────

def test_working_age_profile_bounds():
    print("\nTEST 7: Working-age profile: only adult bins [20-40], no children")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=10000, profile="working_age"))
    m = eng.migration
    m._wa_min, m._wa_max = 20, 40

    weights = m._working_age_weights(eng.state.population)

    for label in weights:
        # No childhood bins
        assert label not in ["0-4", "5-9", "10-14"], (
            f"Childhood bin '{label}' should not appear in working_age profile"
        )
        # All bins within or overlapping [20, 40]
        lo, hi = m._adult_bin_ages[label]
        overlaps = not (hi < 20 or lo > 40)
        assert overlaps, f"Bin '{label}' ({lo}-{hi}) outside [20-40] but included"

    # Bins outside range excluded
    for excluded in ["15-19", "45-49", "50-54"]:
        assert excluded not in weights, f"Bin '{excluded}' should be excluded"

    total_w = sum(v["female"] + v["male"] for v in weights.values())
    assert abs(total_w - 1.0) < 1e-9
    print(f"  Included bins: {sorted(weights.keys())} ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Custom profile normalised correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_custom_profile_normalised():
    print("\nTEST 8: Custom profile: normalised correctly and applied")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=10000, profile="custom"))
    m = eng.migration

    # After init, custom_weights should be normalised
    total_w = sum(v["female"] + v["male"] for v in m._custom_weights.values())
    assert abs(total_w - 1.0) < 1e-9, f"Custom weights not normalised: {total_w}"

    # Only bins from custom spec
    assert "20-24" in m._custom_weights
    assert "25-29" in m._custom_weights
    assert "15-19" not in m._custom_weights

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    net = eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2000.0, eng.time, eng.rng, eng.deterministic
    )
    pop_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    step_expected = int(round(10000 * eng.time.years_per_step))
    assert abs(net - step_expected) <= 5, f"Net change {net} far from expected {step_expected}"
    print(f"  Custom weights sum=1.0, +{net} people added ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Custom profile — invalid bin raises error
# ─────────────────────────────────────────────────────────────────────────────

def test_custom_invalid_bin_raises():
    print("\nTEST 9: Custom profile: invalid bin name raises MigrationConfigError")
    cfg = load_base_config()
    # V11 stores migration under demography.migration — use the correct key path.
    cfg["demography"]["migration"] = {
        "enabled": True,
        "quantity": {"mode": "flat", "flat": 1000},
        "profile": {
            "mode": "custom",
            "custom_weights": {
                "value": {
                    "INVALID_BIN": {"female": 0.5, "male": 0.5}
                }
            },
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir="/tmp", delete=False) as tf:
        json.dump(cfg, tf)
        tf_path = tf.name

    raised = False
    try:
        SimulationEngineV3(tf_path, seed=42)
    except MigrationConfigError as e:
        assert "unknown age bin" in str(e).lower()
        raised = True
    assert raised, "Expected MigrationConfigError for invalid bin"
    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: All adult entrants are Susceptible/Undiag/No-ART/No-PrEP
# ─────────────────────────────────────────────────────────────────────────────

def test_entrants_epidemiological_state():
    print("\nTEST 10: Adult entrants are Susceptible/Undiag/No-ART/No-PrEP only")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=100000, profile="working_age"))

    # Snapshot HIV-positive count before
    hiv_before = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))

    eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2000.0, eng.time, eng.rng, eng.deterministic
    )

    # HIV-positive count must not increase from migration
    hiv_after = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))
    assert hiv_after == hiv_before, (
        f"HIV+ count changed from migration: before={hiv_before}, after={hiv_after}"
    )

    # All added people must be in susceptible (HIV idx 0), diag=0, art=0, prep=0
    susc_idx = eng.groups.hiv_to_idx["Susceptible"]
    all_susc_undiag_noart_noprep = int(
        np.sum(eng.state.population[:, :, :, susc_idx, 0, 0, 0])
    )
    total_adults = int(np.sum(eng.state.population))
    # All non-susc-undiag-noart-noprep must equal original HIV+ + original treated etc.
    other = total_adults - all_susc_undiag_noart_noprep
    assert other == hiv_before, (
        f"Non-susceptible/baseline count mismatch: {other} vs HIV before {hiv_before}"
    )
    print("  ✓ PASS: No HIV imported via migration")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tests 10b-10d: V13.1 migrant HIV entries
# ─────────────────────────────────────────────────────────────────────────────

def migrant_hiv_block(year_counts=None, fallback=0, fallback_years=None):
    block = {
        "enabled": True,
        "count_mode": "year_specific",
        "year_specific_counts": year_counts or {},
        "fallback_count": fallback,
        "orientation_distribution": {
            "MSM": 0.53,
            "Heterosexual_Male": 0.21,
            "Female": 0.26,
        },
        "hiv_stage_distribution": {"Chronic": 1.0},
        "diagnosis_distribution": {"Diagnosed": 1.0},
        "art_distribution": {"On_ART": 0.9, "Not_on_ART": 0.1},
        "prep_state": "None",
    }
    if fallback_years is not None:
        block["fallback_years"] = fallback_years
    return block


def test_migrant_hiv_configurable_fallback_window():
    print("\nTEST 10a: Migrant HIV fallback window is configurable")
    cfg = make_minimal_migration_config(mode="flat", flat=1000, profile="working_age")
    cfg["migration"]["migrant_hiv"] = migrant_hiv_block(
        {"2022": 100},
        fallback=322,
        fallback_years={"start": 2016, "end": 2017},
    )
    eng = make_engine(cfg)

    assert eng.migration._get_step_migrant_hiv_requested(250, 2015.0, 0.25) == 0
    assert eng.migration._get_step_migrant_hiv_requested(250, 2016.0, 0.25) == 81
    assert eng.migration._get_step_migrant_hiv_requested(250, 2017.0, 0.25) == 81
    assert eng.migration._get_step_migrant_hiv_requested(250, 2018.0, 0.25) == 0
    print("  ✓ PASS: fallback applies only to configured inclusive year range")
    return True


def test_migrant_hiv_positive_migration_mass_balance():
    print("\nTEST 10b: Migrant HIV entries are subset of positive net migration")
    cfg = make_minimal_migration_config(mode="flat", flat=1000, profile="working_age")
    cfg["migration"]["migrant_hiv"] = migrant_hiv_block({"2022": 100})
    eng = make_engine(cfg)
    eng.deterministic = True

    total_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    hiv_before = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))

    net = eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2022.0, eng.time, eng.rng, eng.deterministic
    )

    total_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    hiv_after = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))
    stats = eng.migration.last_migrant_hiv_stats

    assert net == 250, f"Expected quarterly net migration 250, got {net}"
    assert total_after - total_before == net
    assert stats["migrant_hiv_requested_entries"] == 25
    assert stats["migrant_hiv_entries"] == 25
    assert hiv_after - hiv_before == 25
    assert stats["migrant_hiv_on_art_entries"] in (22, 23), stats
    assert (
        stats["migrant_hiv_msm_entries"]
        + stats["migrant_hiv_hetero_male_entries"]
        + stats["migrant_hiv_female_entries"]
        == stats["migrant_hiv_entries"]
    )
    print("  ✓ PASS: HIV+ migrant entries do not double-count population")
    return True


def test_migrant_hiv_negative_migration_no_imports():
    print("\nTEST 10c: Negative net migration does not import migrant HIV")
    cfg = make_minimal_migration_config(mode="flat", flat=-1000, profile="working_age")
    cfg["migration"]["migrant_hiv"] = migrant_hiv_block({"2022": 100})
    eng = make_engine(cfg)
    eng.deterministic = True

    net = eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2022.0, eng.time, eng.rng, eng.deterministic
    )

    stats = eng.migration.last_migrant_hiv_stats
    assert net == -250, f"Expected quarterly net migration -250, got {net}"
    assert stats["migrant_hiv_requested_entries"] == 0
    assert stats["migrant_hiv_entries"] == 0
    print("  ✓ PASS: no migrant HIV entries on net-negative timestep")
    return True


def test_migrant_hiv_cap_and_backcast_window():
    print("\nTEST 10d: Migrant HIV cap and 2018-2021 backcast window")
    cfg = make_minimal_migration_config(mode="flat", flat=10, profile="working_age")
    cfg["migration"]["migrant_hiv"] = migrant_hiv_block({"2022": 1000}, fallback=322)
    eng = make_engine(cfg)
    eng.deterministic = True

    # Before 2018: fallback must not apply.
    eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2017.0, eng.time, eng.rng, eng.deterministic
    )
    assert eng.migration.last_migrant_hiv_stats["migrant_hiv_requested_entries"] == 0

    # 2022: requested count exceeds positive net migrants and is capped.
    hiv_before = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))
    net = eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2022.0, eng.time, eng.rng, eng.deterministic
    )
    hiv_after = int(np.sum(eng.state.population[:, :, :, 1:, :, :, :]))
    stats = eng.migration.last_migrant_hiv_stats

    assert net == 2
    assert stats["migrant_hiv_requested_entries"] == 250
    assert stats["migrant_hiv_entries"] == 2
    assert stats["migrant_hiv_cap_shortfall"] == 248
    assert hiv_after - hiv_before == 2
    print("  ✓ PASS: cap preserves net migration mass balance")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Children go to childhood pool, adults to adult tensor
# ─────────────────────────────────────────────────────────────────────────────

def test_childhood_routing():
    print("\nTEST 11: Childhood entrants → childhood pool; adults → adult tensor")
    # Use mirror profile so some migrants are allocated to child bins
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=200000, profile="mirror"))
    m = eng.migration

    # Get mirror weights — check there are child bin entries
    wts = m._mirror_weights(eng.state.population, eng.demography.childhood)
    child_weight = sum(wts[b]["female"] + wts[b]["male"]
                       for b in ["0-4", "5-9", "10-14"] if b in wts)
    print(f"  Child-bin weight in mirror profile: {child_weight:.4f}")

    child_before = eng.demography.childhood.get_total()
    adult_before = int(np.sum(eng.state.population))

    net = eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2000.0, eng.time, eng.rng, eng.deterministic
    )

    child_after = eng.demography.childhood.get_total()
    adult_after = int(np.sum(eng.state.population))

    child_gain = child_after - child_before
    adult_gain = adult_after - adult_before

    if child_weight > 0:
        assert child_gain > 0, "Childhood pool should have grown with child-age migrants"
    assert adult_gain > 0, "Adult tensor should have grown with adult-age migrants"
    assert child_gain + adult_gain == net, (
        f"Conservation: child_gain({child_gain}) + adult_gain({adult_gain}) != net({net})"
    )
    print(f"  +{child_gain:,} children, +{adult_gain:,} adults, net={net:,} ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: MSM proportion respected for male entrants
# ─────────────────────────────────────────────────────────────────────────────

def test_msm_proportion():
    print("\nTEST 12: Male entrants respect MSM proportion")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=500000, profile="working_age"))
    m = eng.migration

    msm_idx   = eng.groups.orient_to_idx["MSM"]
    hetero_idx = eng.groups.orient_to_idx["Heterosexual_Male"]
    susc_idx  = eng.groups.hiv_to_idx["Susceptible"]

    msm_before   = int(np.sum(eng.state.population[:, msm_idx,   :, susc_idx, 0, 0, 0]))
    hetero_before = int(np.sum(eng.state.population[:, hetero_idx, :, susc_idx, 0, 0, 0]))

    eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2000.0, eng.time, eng.rng, eng.deterministic
    )

    msm_after   = int(np.sum(eng.state.population[:, msm_idx,   :, susc_idx, 0, 0, 0]))
    hetero_after = int(np.sum(eng.state.population[:, hetero_idx, :, susc_idx, 0, 0, 0]))

    msm_added   = msm_after   - msm_before
    hetero_added = hetero_after - hetero_before
    total_male_added = msm_added + hetero_added

    if total_male_added > 0:
        actual_p_msm = msm_added / total_male_added
        expected_p_msm = m._p_msm
        # Allow ±2 pp tolerance for rounding
        assert abs(actual_p_msm - expected_p_msm) < 0.02, (
            f"MSM proportion: expected {expected_p_msm:.3f}, got {actual_p_msm:.3f}"
        )
        print(f"  p_msm={expected_p_msm:.3f}, actual={actual_p_msm:.3f} ✓ PASS")
    else:
        print("  (no male entrants in working-age bands — skipping proportion check)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: Negative migration 90 % cap fires warning
# ─────────────────────────────────────────────────────────────────────────────

def test_negative_cap_warning():
    print("\nTEST 13: Negative migration >90% cap → RuntimeWarning, no crash")
    eng = make_engine()
    m_cfg = make_minimal_migration_config(mode="flat", flat=-99999999)["migration"]
    m_cfg["enabled"] = True
    eng.config["migration"] = m_cfg
    # Re-init migration module directly
    from demography import DemographyModule
    eng.migration = MigrationModule(eng.config, eng.groups, eng.demography)

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        net = eng.migration.apply(
            eng.state.population, eng.demography.childhood,
            2000.0, eng.time, eng.rng, eng.deterministic
        )

    cap_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)
                    and "90" in str(w.message)]
    assert len(cap_warnings) >= 1, "Expected a cap RuntimeWarning"
    assert np.all(eng.state.population >= 0)
    assert np.all(eng.demography.childhood.population >= 0)
    print(f"  Cap warning fired, net={net:,}, no negative cells ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 14: Conservation — net_actual == Δpopulation
# ─────────────────────────────────────────────────────────────────────────────

def test_conservation():
    print("\nTEST 14: Conservation: net_actual from apply() == Δ(adults + children)")
    for flat_val in [10000, -30000, 55000]:
        eng = make_engine(make_minimal_migration_config(mode="flat", flat=flat_val))
        pop_before = (int(np.sum(eng.state.population))
                      + eng.demography.childhood.get_total())

        net = eng.migration.apply(
            eng.state.population, eng.demography.childhood,
            2000.0, eng.time, eng.rng, eng.deterministic
        )

        pop_after = (int(np.sum(eng.state.population))
                     + eng.demography.childhood.get_total())
        actual_delta = pop_after - pop_before

        assert net == actual_delta, (
            f"flat={flat_val}: returned net={net}, actual delta={actual_delta}"
        )
    print("  ✓ PASS: returned value matches actual population Δ for +/- migration")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 15: Mass-balance passes with migration in full simulation
# ─────────────────────────────────────────────────────────────────────────────

def test_full_simulation_mass_balance():
    print("\nTEST 15: Full simulation mass-balance with migration enabled")
    # Use flat 44k/yr (NL historical average) over 10 years
    cfg_override = make_minimal_migration_config(mode="flat", flat=44000, profile="mirror")
    # Shorten run to 5 years (quarterly = 20 steps)
    cfg_override["time"] = {
        "unit": "months", "step_size": 3,
        "start_year": 2000, "end_year": 2005
    }
    cfg_override["migration"]["enabled"] = True

    eng = make_engine(cfg_override)

    # Run should complete without AssertionError from validate_global_population_balance
    try:
        result = eng.run(run_number=1)
    except AssertionError as e:
        raise AssertionError(f"Mass-balance check failed: {e}")

    df = result["summary"]
    total_migration = sum(df["net_migration"]) if "net_migration" in df.columns else None
    print(f"  Cumulative net migration: {total_migration:,}")
    assert total_migration is not None and total_migration > 0
    print("  ✓ PASS: simulation completed, mass-balance maintained")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 16: HIV prevalence unchanged from migration (susceptible-only entrants)
# ─────────────────────────────────────────────────────────────────────────────

def test_hiv_prevalence_dilution_only():
    print("\nTEST 16: Migration only dilutes HIV prevalence (no new infections imported)")
    # Seed HIV at 5% prevalence, then add large migration pulse
    cfg = load_base_config()
    cfg["population"]["initial_hiv_prevalence"] = {"value": 0.05}
    cfg["migration"] = {
        "enabled": True,
        "quantity": {"mode": "flat", "flat": 500000},
        "profile": {"mode": "working_age",
                    "working_age": {"min_age": 20, "max_age": 40}},
    }
    cfg["time"] = {"unit": "months", "step_size": 3,
                   "start_year": 2000, "end_year": 2001}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir="/tmp", delete=False) as tf:
        json.dump(cfg, tf)
        tf_path = tf.name

    eng = SimulationEngineV3(tf_path, seed=42)

    hiv_before = eng.state.get_hiv_positive()
    pop_before = eng.state.get_total_population()
    prev_before = hiv_before / pop_before

    eng.migration.apply(
        eng.state.population, eng.demography.childhood,
        2000.0, eng.time, eng.rng, eng.deterministic
    )

    hiv_after = eng.state.get_hiv_positive()
    pop_after = eng.state.get_total_population()
    prev_after = hiv_after / pop_after

    # HIV+ count must not have increased from migration
    assert hiv_after == hiv_before, (
        f"Migration imported HIV: before={hiv_before}, after={hiv_after}"
    )
    # Prevalence must have decreased (dilution from susceptible entrants)
    assert prev_after < prev_before, (
        f"Prevalence should drop after adding susceptibles: {prev_before:.4f} → {prev_after:.4f}"
    )
    print(f"  Prevalence: {prev_before:.4f} → {prev_after:.4f} (diluted, no import) ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Test 17: _distribute_int helper — exact sum
# ─────────────────────────────────────────────────────────────────────────────

def test_distribute_int_exact_sum():
    print("\nTEST 17: _distribute_int: always sums to n (deterministic & stochastic)")
    rng = np.random.RandomState(0)
    weights = np.array([0.3, 0.5, 0.2])

    for n in [0, 1, 100, 1000, 7, 33]:
        det = _distribute_int(n, weights, rng, deterministic=True)
        assert det.sum() == n, f"Deterministic sum: {det.sum()} != {n}"
        stoch = _distribute_int(n, weights, rng, deterministic=False)
        assert stoch.sum() == n, f"Stochastic sum: {stoch.sum()} != {n}"
        assert np.all(det >= 0) and np.all(stoch >= 0)

    print("  ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tests 18-20: V9.1 — LRM add-back for negative migration (exact removal)
# ─────────────────────────────────────────────────────────────────────────────

def test_negative_lrm_exact_small():
    """Tiny negative target (annual=-304, WB 2007): add-back gives exact removal."""
    print("\nTEST 18: Negative LRM add-back — tiny target exact (WB 2007 case)")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=-304))

    dt = eng.time.years_per_step
    step_target = int(round(304 * dt))  # 76 people

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        net = eng.migration.apply(
            eng.state.population, eng.demography.childhood,
            2004.0, eng.time, eng.rng, eng.deterministic,
        )

    pop_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    actual_removed = pop_before - pop_after

    assert actual_removed == step_target, (
        f"Expected exactly {step_target} removed, got {actual_removed} "
        f"(overshoot={actual_removed - step_target})"
    )
    assert np.all(eng.state.population >= 0), "Negative tensor cells after add-back"
    assert np.all(eng.demography.childhood.population >= 0), "Negative childhood cells"
    print(f"  step_target={step_target}, actual_removed={actual_removed}, overshoot=0 ✓ PASS")
    return True


def test_negative_lrm_exact_normal():
    """Normal negative target (annual=-9230, WB 2004): add-back gives exact removal."""
    print("\nTEST 19: Negative LRM add-back — normal target exact (WB 2004 case)")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=-9230))

    dt = eng.time.years_per_step
    step_target = int(round(9230 * dt))  # 2308 people

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        net = eng.migration.apply(
            eng.state.population, eng.demography.childhood,
            2004.0, eng.time, eng.rng, eng.deterministic,
        )

    pop_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    actual_removed = pop_before - pop_after

    assert actual_removed == step_target, (
        f"Expected exactly {step_target} removed, got {actual_removed} "
        f"(overshoot={actual_removed - step_target})"
    )
    assert np.all(eng.state.population >= 0), "Negative tensor cells after add-back"
    print(f"  step_target={step_target}, actual_removed={actual_removed}, overshoot=0 ✓ PASS")
    return True


def test_negative_lrm_positive_unaffected():
    """Positive migration must still be exact after the negative LRM patch."""
    print("\nTEST 20: Negative LRM patch — positive migration path unaffected")
    eng = make_engine(make_minimal_migration_config(mode="flat", flat=100_000))

    dt = eng.time.years_per_step
    step_target = int(round(100_000 * dt))  # 25,000

    pop_before = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        net = eng.migration.apply(
            eng.state.population, eng.demography.childhood,
            1970.0, eng.time, eng.rng, eng.deterministic,
        )

    pop_after = int(np.sum(eng.state.population)) + eng.demography.childhood.get_total()
    actual_added = pop_after - pop_before

    assert actual_added == step_target, (
        f"Positive migration: expected {step_target}, got {actual_added}"
    )
    print(f"  step_target={step_target}, actual_added={actual_added}, discrepancy=0 ✓ PASS")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    ("Disabled no change",            test_disabled_no_change),
    ("Year-specific lookup",           test_year_specific_lookup),
    ("Rate mode",                      test_rate_mode),
    ("Flat mode",                      test_flat_mode),
    ("Negative migration no negatives",test_negative_migration_no_negatives),
    ("Mirror profile proportional",    test_mirror_profile_proportional),
    ("Working-age profile bounds",     test_working_age_profile_bounds),
    ("Custom profile normalised",      test_custom_profile_normalised),
    ("Custom invalid bin raises",      test_custom_invalid_bin_raises),
    ("Entrants epi state",             test_entrants_epidemiological_state),
    ("Migrant HIV configurable fallback", test_migrant_hiv_configurable_fallback_window),
    ("Migrant HIV mass balance",       test_migrant_hiv_positive_migration_mass_balance),
    ("Migrant HIV negative no import", test_migrant_hiv_negative_migration_no_imports),
    ("Migrant HIV cap/backcast",       test_migrant_hiv_cap_and_backcast_window),
    ("Childhood routing",              test_childhood_routing),
    ("MSM proportion",                 test_msm_proportion),
    ("Negative cap warning",           test_negative_cap_warning),
    ("Conservation",                   test_conservation),
    ("Full sim mass-balance",          test_full_simulation_mass_balance),
    ("HIV prevalence dilution only",   test_hiv_prevalence_dilution_only),
    ("Distribute int exact sum",       test_distribute_int_exact_sum),
    # V9.1 — LRM add-back for negative migration
    ("Negative LRM exact small",       test_negative_lrm_exact_small),
    ("Negative LRM exact normal",      test_negative_lrm_exact_normal),
    ("Negative LRM positive unaffected", test_negative_lrm_positive_unaffected),
]


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("MIGRATION MODULE TEST SUITE  (V9 Patch)")
    print("=" * 60)

    passed, failed = 0, []
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  ✗ FAIL: {exc}")
            failed.append(name)

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed}/{len(TESTS)} passed, {len(failed)} failed")
    print("=" * 60)
    if failed:
        print("Failed tests:")
        for f in failed:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print("\n✓ All migration tests passed.")
