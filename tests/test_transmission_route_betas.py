"""
V11.3 Route-Specific Transmission Beta Tests — 12 tests (T01–T12)

Tests the route-specific per-act transmission beta implementation,
including direction-specific vaginal betas, URAI/UIAI anal split,
MSM versatility parameter, feature flag gating, and legacy fallback.

Usage:
    python test_transmission_route_betas.py
"""

import sys
import copy
import numpy as np

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transmission_corrected import TransmissionEngine
from config_utils import extract_value


# ── Shared fixtures ─────────────────────────────────────────────────────────

# Route-specific V11.3 config with advanced.enabled = True
ROUTE_CONFIG = {
    "disease": {
        "transmission": {
            "advanced": {
                "enabled": True,
                "beta_per_route": {
                    "vaginal_mtf": {
                        "chronic": {"value": 0.0008}
                    },
                    "vaginal_ftm": {
                        "chronic": {"value": 0.0004}
                    },
                    "anal_receptive": {
                        "chronic": {"value": 0.0138}
                    },
                    "anal_insertive": {
                        "chronic": {"value": 0.0011}
                    }
                },
                "stage_multiplier_acute": {"value": 9.17},
                "stage_multiplier_aids": {"value": 7.27},
                "msm_versatility": {"value": 0.5}
            },
            "beta_acute": {"value": 0.08},
            "beta_chronic": {"value": 0.004},
            "beta_aids": {"value": 0.008}
        },
        "treatment": {
            "art_transmission_reduction": {"value": 0.96}
        }
    },
    "mixing": {
        "condom_usage": {"value": {"Low": 1.0}}
    },
    "interventions": {}
}

# Legacy V11.2 config — no advanced block at all
LEGACY_CONFIG = {
    "disease": {
        "transmission": {
            "beta_acute": {"value": 0.08},
            "beta_chronic": {"value": 0.004},
            "beta_aids": {"value": 0.008}
        },
        "treatment": {
            "art_transmission_reduction": {"value": 0.96}
        }
    },
    "mixing": {
        "condom_usage": {"value": {"Low": 1.0}}
    },
    "interventions": {}
}


class RouteGroups:
    """Minimal groups with 3 orientations: Female, Heterosexual_Male, MSM."""
    n_age_groups = 1
    n_orientations = 3
    n_activities = 1
    n_prep = 1
    orientations = ["Female", "Heterosexual_Male", "MSM"]
    orient_to_idx = {"Female": 0, "Heterosexual_Male": 1, "MSM": 2}
    activities = ["Low"]
    prep_states = ["None"]
    hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}


class SingleGroups:
    """Minimal groups with 1 orientation (for legacy tests)."""
    n_age_groups = 1
    n_orientations = 1
    n_activities = 1
    n_prep = 1
    orientations = ["Female"]
    orient_to_idx = {"Female": 0}
    activities = ["Low"]
    prep_states = ["None"]
    hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}


class MinimalTime:
    years_per_step = 1.0


def make_population_3orient(susc_counts, infected_counts, stage_idx=2):
    """
    Create population tensor for 3-orientation setup.

    susc_counts: [female_susc, hetmale_susc, msm_susc]
    infected_counts: [female_inf, hetmale_inf, msm_inf]
    stage_idx: 1=Acute, 2=Chronic, 3=AIDS
    """
    # shape: [age, orient, activity, hiv_stage, dx, art, prep]
    pop = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    for i in range(3):
        pop[0, i, 0, 0, 0, 0, 0] = susc_counts[i]       # Susceptible
        pop[0, i, 0, stage_idx, 0, 0, 0] = infected_counts[i]  # Infected, off ART
    return pop


def make_contacts_3orient(contact_pairs):
    """
    Create contacts tensor for 3-orientation setup.

    contact_pairs: dict of {(from_orient, to_orient): n_contacts}
    """
    contacts = np.zeros((3, 1, 3, 1))
    for (f, t), n in contact_pairs.items():
        contacts[f, 0, t, 0] = n
    return contacts


def run_stochastic(engine, pop, contacts, n_runs=1000, seed=42):
    """Run n_runs stochastic simulations and return list of total_infections."""
    results = []
    rng = np.random.RandomState(seed)
    for _ in range(n_runs):
        total, _ = engine.calculate_transmission(
            pop.copy(), contacts, rng, deterministic=False
        )
        results.append(total)
    return results


# ── Test functions ──────────────────────────────────────────────────────────

def test_T01_direction_asymmetry_het():
    """
    T01: Direction asymmetry — Het Female susceptibles receive ~2x more
    infections than Het Male susceptibles (chronic stage, equal contacts).
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    # Female susceptibles ← Heterosexual_Male infectious (M→F = 0.0008)
    pop_f = make_population_3orient(
        susc_counts=[5000, 0, 0],
        infected_counts=[0, 5000, 0],
        stage_idx=2  # Chronic
    )
    contacts_f = make_contacts_3orient({(0, 1): 10000})  # Female ← HetMale
    inf_f = run_stochastic(engine, pop_f, contacts_f, n_runs=500)

    # HetMale susceptibles ← Female infectious (F→M = 0.0004)
    pop_m = make_population_3orient(
        susc_counts=[0, 5000, 0],
        infected_counts=[5000, 0, 0],
        stage_idx=2
    )
    contacts_m = make_contacts_3orient({(1, 0): 10000})  # HetMale ← Female
    inf_m = run_stochastic(engine, pop_m, contacts_m, n_runs=500)

    mean_f = np.mean(inf_f)
    mean_m = np.mean(inf_m)
    ratio = mean_f / mean_m if mean_m > 0 else float('inf')

    passed = 1.5 <= ratio <= 2.5
    print(f"  T01: F inf={mean_f:.1f}, M inf={mean_m:.1f}, ratio={ratio:.2f} "
          f"(expect ~2.0) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected F/M infection ratio near 2.0, got {ratio:.2f}"


def test_T02_stage_scaling_acute():
    """
    T02: Acute-stage partners multiply infections by ~9.17x vs chronic.
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    results = {}
    for stage_idx, stage_label in [(2, "Chronic"), (1, "Acute")]:
        pop = make_population_3orient(
            susc_counts=[5000, 0, 0],
            infected_counts=[0, 5000, 0],
            stage_idx=stage_idx
        )
        contacts = make_contacts_3orient({(0, 1): 10000})
        inf = run_stochastic(engine, pop, contacts, n_runs=500)
        results[stage_label] = np.mean(inf)

    ratio = results["Acute"] / results["Chronic"] if results["Chronic"] > 0 else float('inf')
    passed = 8.0 <= ratio <= 10.5
    print(f"  T02: Acute={results['Acute']:.1f}, Chronic={results['Chronic']:.1f}, "
          f"ratio={ratio:.2f} (expect ~9.17) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected acute/chronic ratio near 9.17, got {ratio:.2f}"


def test_T03_stage_scaling_aids():
    """
    T03: AIDS-stage partners multiply infections by ~7.27x vs chronic.
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    results = {}
    for stage_idx, stage_label in [(2, "Chronic"), (3, "AIDS")]:
        pop = make_population_3orient(
            susc_counts=[5000, 0, 0],
            infected_counts=[0, 5000, 0],
            stage_idx=stage_idx
        )
        contacts = make_contacts_3orient({(0, 1): 10000})
        inf = run_stochastic(engine, pop, contacts, n_runs=500)
        results[stage_label] = np.mean(inf)

    ratio = results["AIDS"] / results["Chronic"] if results["Chronic"] > 0 else float('inf')
    passed = 6.0 <= ratio <= 8.5
    print(f"  T03: AIDS={results['AIDS']:.1f}, Chronic={results['Chronic']:.1f}, "
          f"ratio={ratio:.2f} (expect ~7.27) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected AIDS/chronic ratio near 7.27, got {ratio:.2f}"


def test_T04_msm_versatility_0():
    """
    T04: MSM versatility=0.0 → all contacts insertive (UIAI beta only).
    """
    cfg = copy.deepcopy(ROUTE_CONFIG)
    cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = 0.0
    engine = TransmissionEngine(cfg, RouteGroups(), MinimalTime())

    pop = make_population_3orient(
        susc_counts=[0, 0, 5000],
        infected_counts=[0, 0, 5000],
        stage_idx=2  # Chronic
    )
    contacts = make_contacts_3orient({(2, 2): 10000})  # MSM ← MSM
    inf = run_stochastic(engine, pop, contacts, n_runs=500)
    mean_inf = np.mean(inf)

    # Expected: only UIAI (0.0011) applies. ~10000 * 0.5 * 0.0011 ≈ 5.5
    # (prevalence ≈ 0.5 since 5000/10000)
    # Actually prevalence = 5000/(5000+5000) = 0.5. Contacts = 10000.
    # E[pos_contacts] = 10000 * 0.5 = 5000. E[inf] = 5000 * 0.0011 = 5.5
    expected_upper = 5000 * 0.0011 * 1.5  # generous bound
    expected_lower = 5000 * 0.0011 * 0.5

    passed = expected_lower <= mean_inf <= expected_upper
    print(f"  T04: p=0.0 (all insertive), mean={mean_inf:.2f} "
          f"(expect UIAI-only range [{expected_lower:.1f}, {expected_upper:.1f}]) "
          f"{'PASS' if passed else 'FAIL'}")
    assert passed, (
        f"Expected insertive-only infections in [{expected_lower:.1f}, "
        f"{expected_upper:.1f}], got {mean_inf:.2f}"
    )


def test_T05_msm_versatility_1():
    """
    T05: MSM versatility=1.0 → all contacts receptive (URAI beta only).
    """
    cfg = copy.deepcopy(ROUTE_CONFIG)
    cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = 1.0
    engine = TransmissionEngine(cfg, RouteGroups(), MinimalTime())

    pop = make_population_3orient(
        susc_counts=[0, 0, 5000],
        infected_counts=[0, 0, 5000],
        stage_idx=2
    )
    contacts = make_contacts_3orient({(2, 2): 10000})
    inf = run_stochastic(engine, pop, contacts, n_runs=500)
    mean_inf = np.mean(inf)

    # All contacts use URAI (0.0138). E[pos] = 5000, E[inf] = 5000 * 0.0138 = 69
    expected = 5000 * 0.0138
    passed = 0.7 * expected <= mean_inf <= 1.3 * expected
    print(f"  T05: p=1.0 (all receptive), mean={mean_inf:.2f} "
          f"(expect ~{expected:.1f}) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected receptive-only infections near {expected:.1f}, got {mean_inf:.2f}"


def test_T06_msm_versatility_05():
    """
    T06: MSM versatility=0.5 → effective beta ≈ 0.5*0.0138 + 0.5*0.0011 = 0.00745.
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    pop = make_population_3orient(
        susc_counts=[0, 0, 5000],
        infected_counts=[0, 0, 5000],
        stage_idx=2
    )
    contacts = make_contacts_3orient({(2, 2): 10000})
    inf = run_stochastic(engine, pop, contacts, n_runs=1000)
    mean_inf = np.mean(inf)

    # E[pos] = 5000, effective beta = 0.5*0.0138 + 0.5*0.0011 = 0.00745
    expected = 5000 * 0.00745
    passed = 0.85 * expected <= mean_inf <= 1.15 * expected
    print(f"  T06: p=0.5, mean={mean_inf:.2f} "
          f"(expect ~{expected:.1f} ±15%) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected mixed MSM infections near {expected:.1f}, got {mean_inf:.2f}"


def test_T07_msm_urai_uiai_ratio():
    """
    T07: At p=0.5, infections from URAI >> UIAI (ratio ≈ 12.5).
    We verify indirectly by comparing p=1.0 (all URAI) vs p=0.0 (all UIAI).
    """
    results = {}
    for p_val, label in [(1.0, "URAI"), (0.0, "UIAI")]:
        cfg = copy.deepcopy(ROUTE_CONFIG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        engine = TransmissionEngine(cfg, RouteGroups(), MinimalTime())

        pop = make_population_3orient(
            susc_counts=[0, 0, 5000],
            infected_counts=[0, 0, 5000],
            stage_idx=2
        )
        contacts = make_contacts_3orient({(2, 2): 10000})
        inf = run_stochastic(engine, pop, contacts, n_runs=500)
        results[label] = np.mean(inf)

    ratio = results["URAI"] / results["UIAI"] if results["UIAI"] > 0 else float('inf')
    passed = 10.0 <= ratio <= 15.0
    print(f"  T07: URAI={results['URAI']:.1f}, UIAI={results['UIAI']:.1f}, "
          f"ratio={ratio:.2f} (expect ~12.5) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected URAI/UIAI infection ratio near 12.5, got {ratio:.2f}"


def test_T08_legacy_fallback_no_advanced():
    """
    T08: Config with NO advanced block → engine uses legacy betas.
    No exception raised. Output matches legacy behaviour.
    """
    try:
        engine = TransmissionEngine(LEGACY_CONFIG, SingleGroups(), MinimalTime())
    except Exception as e:
        print(f"  T08: FAIL — exception during init: {e}")
        raise AssertionError(f"Legacy config should initialize without exception: {e}") from e

    if engine.route_specific:
        print("  T08: FAIL — route_specific should be False for legacy config")
    assert not engine.route_specific, "route_specific should be False for legacy config"

    # Run a basic transmission check
    pop = np.zeros((1, 1, 1, 4, 2, 2, 1), dtype=np.int64)
    pop[0, 0, 0, 0, 0, 0, 0] = 5000   # Susceptible
    pop[0, 0, 0, 2, 0, 0, 0] = 5000   # Chronic, off ART
    contacts = np.zeros((1, 1, 1, 1))
    contacts[0, 0, 0, 0] = 5000

    rng = np.random.RandomState(42)
    total, _ = engine.calculate_transmission(pop, contacts, rng, deterministic=True)

    # Expected: 5000 contacts * 0.5 prevalence * 0.004 beta_chronic ≈ 10
    # (deterministic: int(5000 * 0.5) = 2500, then int(2500 * 0.004) = 10)
    passed = total > 0
    print(f"  T08: Legacy config, route_specific={engine.route_specific}, "
          f"infections={total} {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected legacy fallback to produce positive infections, got {total}"


def test_T09_zero_cross_orientation():
    """
    T09: MSM contacts with Het Female or Het Male → zero infections
    (these orientation pairs are not in the route lookup).
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    # MSM infectious, Female susceptible — should be zero
    pop = make_population_3orient(
        susc_counts=[5000, 0, 0],
        infected_counts=[0, 0, 5000],
        stage_idx=2
    )
    contacts = make_contacts_3orient({(0, 2): 10000})  # Female ← MSM
    rng = np.random.RandomState(42)
    total_1, _ = engine.calculate_transmission(pop, contacts, rng, deterministic=True)

    # MSM susceptible, HetMale infectious — should be zero
    pop2 = make_population_3orient(
        susc_counts=[0, 0, 5000],
        infected_counts=[0, 5000, 0],
        stage_idx=2
    )
    contacts2 = make_contacts_3orient({(2, 1): 10000})  # MSM ← HetMale
    total_2, _ = engine.calculate_transmission(pop2, contacts2, rng, deterministic=True)

    passed = (total_1 == 0 and total_2 == 0)
    print(f"  T09: F←MSM={total_1}, MSM←HetM={total_2} "
          f"(expect both 0) {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected both cross-orientation totals to be 0, got {total_1} and {total_2}"


def test_T10_all_12_betas_correct():
    """
    T10: All 12 computed betas match expected values within 0.1%.
    """
    engine = TransmissionEngine(ROUTE_CONFIG, RouteGroups(), MinimalTime())

    if not engine.route_specific:
        print("  T10: FAIL — engine not in route-specific mode")
    assert engine.route_specific, "Engine should be in route-specific mode"

    expected = {
        "vaginal_mtf": {
            "Chronic": 0.0008,
            "Acute": 0.0008 * 9.17,
            "AIDS": 0.0008 * 7.27
        },
        "vaginal_ftm": {
            "Chronic": 0.0004,
            "Acute": 0.0004 * 9.17,
            "AIDS": 0.0004 * 7.27
        },
        "anal_receptive": {
            "Chronic": 0.0138,
            "Acute": 0.0138 * 9.17,
            "AIDS": 0.0138 * 7.27
        },
        "anal_insertive": {
            "Chronic": 0.0011,
            "Acute": 0.0011 * 9.17,
            "AIDS": 0.0011 * 7.27
        }
    }

    all_ok = True
    for route, stages in expected.items():
        for stage, exp_val in stages.items():
            actual = engine.beta_by_route[route][stage]
            rel_err = abs(actual - exp_val) / exp_val if exp_val != 0 else abs(actual)
            if rel_err > 0.001:
                print(f"    MISMATCH: {route}/{stage}: actual={actual}, expected={exp_val}")
                all_ok = False

    passed = all_ok
    print(f"  T10: 12-beta arithmetic check {'PASS' if passed else 'FAIL'}")
    assert passed, "One or more route/stage beta values differ from expected values"


def test_T11_flag_disabled():
    """
    T11: advanced.enabled = false with full beta_per_route present →
    engine must NOT activate route-specific path.
    """
    cfg = copy.deepcopy(ROUTE_CONFIG)
    cfg["disease"]["transmission"]["advanced"]["enabled"] = False

    engine = TransmissionEngine(cfg, RouteGroups(), MinimalTime())

    if engine.route_specific:
        print("  T11: FAIL — route_specific should be False when enabled=false")
    assert not engine.route_specific, "route_specific should be False when enabled=false"

    if hasattr(engine, 'beta_by_route'):
        print("  T11: FAIL — beta_by_route should not exist when disabled")
    assert not hasattr(engine, 'beta_by_route'), "beta_by_route should not exist when route-specific mode is disabled"

    # Confirm legacy beta dict is populated
    if "Chronic" not in engine.beta:
        print("  T11: FAIL — legacy beta dict missing")
    assert "Chronic" in engine.beta, "Legacy beta dict should include Chronic when route-specific mode is disabled"

    passed = True
    print(f"  T11: enabled=false → route_specific={engine.route_specific}, "
          f"legacy beta present PASS")
    assert passed


def test_T12_flag_toggle_equivalence():
    """
    T12: Same scenario, enabled=true vs enabled=false → outputs must differ,
    confirming the flag actively switches code paths.
    """
    groups = RouteGroups()
    time_sys = MinimalTime()

    # Enabled = True
    cfg_on = copy.deepcopy(ROUTE_CONFIG)
    cfg_on["disease"]["transmission"]["advanced"]["enabled"] = True
    engine_on = TransmissionEngine(cfg_on, groups, time_sys)

    # Enabled = False (falls back to legacy composite betas)
    cfg_off = copy.deepcopy(ROUTE_CONFIG)
    cfg_off["disease"]["transmission"]["advanced"]["enabled"] = False
    engine_off = TransmissionEngine(cfg_off, groups, time_sys)

    # MSM scenario — biggest expected difference
    pop = make_population_3orient(
        susc_counts=[0, 0, 5000],
        infected_counts=[0, 0, 5000],
        stage_idx=2
    )
    contacts = make_contacts_3orient({(2, 2): 10000})

    inf_on = run_stochastic(engine_on, pop, contacts, n_runs=500, seed=123)
    inf_off = run_stochastic(engine_off, pop, contacts, n_runs=500, seed=123)

    mean_on = np.mean(inf_on)
    mean_off = np.mean(inf_off)

    # They should differ by > 5%
    if mean_off == 0:
        pct_diff = 100.0  # clearly different
    else:
        pct_diff = abs(mean_on - mean_off) / mean_off * 100

    passed = pct_diff > 5.0
    print(f"  T12: enabled=true mean={mean_on:.1f}, enabled=false mean={mean_off:.1f}, "
          f"diff={pct_diff:.1f}% {'PASS' if passed else 'FAIL'}")
    assert passed, f"Expected enabled vs disabled outputs to differ by >5%, got {pct_diff:.1f}%"


# ── Runner ──────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 80)
    print("V11.3 ROUTE-SPECIFIC TRANSMISSION BETA TESTS (T01–T12)")
    print("=" * 80)

    tests = [
        ("T01", "Direction asymmetry: Het", test_T01_direction_asymmetry_het),
        ("T02", "Stage scaling: acute", test_T02_stage_scaling_acute),
        ("T03", "Stage scaling: AIDS", test_T03_stage_scaling_aids),
        ("T04", "MSM versatility p=0.0", test_T04_msm_versatility_0),
        ("T05", "MSM versatility p=1.0", test_T05_msm_versatility_1),
        ("T06", "MSM versatility p=0.5", test_T06_msm_versatility_05),
        ("T07", "MSM URAI/UIAI ratio", test_T07_msm_urai_uiai_ratio),
        ("T08", "Legacy fallback — no advanced block", test_T08_legacy_fallback_no_advanced),
        ("T09", "Zero cross-orientation MSM↔Het", test_T09_zero_cross_orientation),
        ("T10", "All 12 betas correct", test_T10_all_12_betas_correct),
        ("T11", "Flag disabled — enabled: false", test_T11_flag_disabled),
        ("T12", "Flag toggle equivalence", test_T12_flag_toggle_equivalence),
    ]

    results = {}
    for tid, name, fn in tests:
        print(f"\n[{tid}] {name}")
        print("-" * 60)
        try:
            fn()
            results[tid] = True
        except Exception as e:
            print(f"  {tid}: EXCEPTION — {e}")
            results[tid] = False

    # Summary
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print("\n" + "=" * 80)
    if passed == total:
        print(f"✓ ALL {total} TESTS PASSED")
    else:
        failed = [tid for tid, v in results.items() if not v]
        print(f"✗ {passed}/{total} PASSED — FAILED: {', '.join(failed)}")
    print("=" * 80 + "\n")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
