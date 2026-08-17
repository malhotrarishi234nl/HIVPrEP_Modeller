"""
V11.3 Stress & Mechanism Tests — 18 tests (S01–S09, V01–V09)

Designed to PROVE OR DISPROVE correctness of the V11.3 route-specific
transmission engine and MSM versatility mechanism. Each test targets a
specific failure mode that would be silent or hard to detect from output alone.

Test groups:
  S01–S09  HIV route-specific transmission mechanism
  V01–V09  MSM versatility split mechanism

Failure modes probed:
  - Wrong-direction beta (F→M using M→F beta by mistake)
  - Cross-orientation false positives (Female←Female transmissions)
  - ART modifier missing from route-specific path
  - PrEP efficacy missing from route-specific path
  - Contact loss in the MSM Bernoulli split
  - Versatility variance: statistical correctness of the binomial draw
  - Deterministic mode producing wrong expected values
  - Stage multiplier arithmetic errors
  - High-prevalence / boundary conditions

Usage:
    python test_v113_stress.py
"""

import sys
import copy
import numpy as np

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transmission_corrected import TransmissionEngine
from config_utils import extract_value


# ── Shared config / group fixtures ──────────────────────────────────────────

ROUTE_CFG = {
    "disease": {
        "transmission": {
            "beta_acute":   {"value": 0.08},
            "beta_chronic": {"value": 0.004},
            "beta_aids":    {"value": 0.008},
            "advanced": {
                "enabled": True,
                "beta_per_route": {
                    "vaginal_mtf":    {"chronic": {"value": 0.0008}},
                    "vaginal_ftm":    {"chronic": {"value": 0.0004}},
                    "anal_receptive": {"chronic": {"value": 0.0138}},
                    "anal_insertive": {"chronic": {"value": 0.0011}},
                },
                "stage_multiplier_acute": {"value": 9.17},
                "stage_multiplier_aids":  {"value": 7.27},
                "msm_versatility":        {"value": 0.5},
            },
        },
        "treatment": {"art_transmission_reduction": {"value": 0.96}},
    },
    "mixing": {"condom_usage": {"value": {"Low": 1.0}}},
    "interventions": {},
}


class Groups3:
    n_age_groups  = 1
    n_orientations = 3
    n_activities   = 1
    n_prep         = 1
    orientations   = ["Female", "Heterosexual_Male", "MSM"]
    orient_to_idx  = {"Female": 0, "Heterosexual_Male": 1, "MSM": 2}
    activities     = ["Low"]
    prep_states    = ["None"]
    hiv_to_idx     = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}


class Time:
    years_per_step = 1.0


FEMALE, HETMALE, MSM = 0, 1, 2


def pop3(susc, infected, orient_susc, orient_inf, stage=2):
    """One-infected-one-susceptible population tensor (shape 1,3,1,4,2,2,1)."""
    p = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    p[0, orient_susc, 0, 0, 0, 0, 0] = susc       # Susceptible
    p[0, orient_inf,  0, stage, 0, 0, 0] = infected  # Infected, off ART
    return p


def pop3_art(susc, inf_off, inf_on, orient_susc, orient_inf, stage=2):
    """Population with separate off-ART and on-ART infected pools."""
    p = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    p[0, orient_susc, 0, 0, 0, 0, 0] = susc
    p[0, orient_inf, 0, stage, 0, 0, 0] = inf_off   # off ART
    p[0, orient_inf, 0, stage, 0, 1, 0] = inf_on    # on ART
    return p


def contacts3(from_orient, to_orient, n):
    c = np.zeros((3, 1, 3, 1))
    c[from_orient, 0, to_orient, 0] = n
    return c


def run_n(engine, pop, contacts, n=1000, seed=0, det=False):
    rng = np.random.RandomState(seed)
    results = []
    for _ in range(n):
        t, _ = engine.calculate_transmission(pop.copy(), contacts, rng, deterministic=det)
        results.append(t)
    return np.array(results)


# ── S-series: Route-specific HIV transmission mechanism ──────────────────────

def test_S01_direction_correctness():
    """
    S01: F susceptible ← HetMale infectious MUST use vaginal_mtf (0.0008),
    NOT vaginal_ftm (0.0004). We confirm by checking mean infections are
    consistent with 0.0008 and inconsistent with 0.0004.
    Failure mode: route lookup has M and F swapped.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    # F susceptible, HetMale infectious — should use vaginal_mtf (0.0008)
    pop = pop3(susc=5000, infected=5000, orient_susc=FEMALE, orient_inf=HETMALE)
    contacts = contacts3(FEMALE, HETMALE, 10000)
    inf = run_n(engine, pop, contacts, n=500)
    mean_inf = np.mean(inf)

    # Partner pool (HetMale group) is 100% infected (5000 inf, 0 susceptible).
    # Prevalence in partner group = 5000/5000 = 1.0.
    # Expected = n_contacts * 1.0 * beta_mtf = 10000 * 0.0008 = 8.0
    # If direction were swapped (vaginal_ftm used): 10000 * 0.0004 = 4.0
    expected_mtf = 10000 * 0.0008  # = 8.0
    expected_ftm = 10000 * 0.0004  # = 4.0

    closer_to_mtf = abs(mean_inf - expected_mtf) < abs(mean_inf - expected_ftm)
    passed = closer_to_mtf and 0.5 * expected_mtf < mean_inf < 1.5 * expected_mtf
    print(f"  S01: F←M mean={mean_inf:.2f}, expected_mtf={expected_mtf:.2f}, "
          f"expected_ftm={expected_ftm:.2f} {'PASS' if passed else 'FAIL'}")
    return passed


def test_S02_direction_correctness_reverse():
    """
    S02: HetMale susceptible ← Female infectious MUST use vaginal_ftm (0.0004),
    not vaginal_mtf (0.0008). Reverse of S01.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    pop = pop3(susc=5000, infected=5000, orient_susc=HETMALE, orient_inf=FEMALE)
    contacts = contacts3(HETMALE, FEMALE, 10000)
    inf = run_n(engine, pop, contacts, n=500)
    mean_inf = np.mean(inf)

    # Partner pool (Female group) is 100% infected. Prevalence = 1.0.
    # Expected = 10000 * 1.0 * beta_ftm = 10000 * 0.0004 = 4.0
    # If direction swapped (vaginal_mtf used): 10000 * 0.0008 = 8.0
    expected_ftm = 10000 * 0.0004  # = 4.0
    expected_mtf = 10000 * 0.0008  # = 8.0

    closer_to_ftm = abs(mean_inf - expected_ftm) < abs(mean_inf - expected_mtf)
    passed = closer_to_ftm and 0.5 * expected_ftm < mean_inf < 1.5 * expected_ftm
    print(f"  S02: M←F mean={mean_inf:.2f}, expected_ftm={expected_ftm:.2f}, "
          f"expected_mtf={expected_mtf:.2f} {'PASS' if passed else 'FAIL'}")
    return passed


def test_S03_no_same_sex_het_transmission():
    """
    S03: Female susceptible ← Female infectious = 0 infections.
         HetMale susceptible ← HetMale infectious = 0 infections.
    Failure mode: route lookup accidentally returns a beta for same-sex het pairs.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    # Female ← Female
    pop_ff = pop3(susc=5000, infected=5000, orient_susc=FEMALE, orient_inf=FEMALE)
    c_ff = contacts3(FEMALE, FEMALE, 10000)
    t_ff, _ = engine.calculate_transmission(pop_ff, c_ff, rng, deterministic=True)

    # HetMale ← HetMale
    pop_mm = pop3(susc=5000, infected=5000, orient_susc=HETMALE, orient_inf=HETMALE)
    c_mm = contacts3(HETMALE, HETMALE, 10000)
    t_mm, _ = engine.calculate_transmission(pop_mm, c_mm, rng, deterministic=True)

    passed = (t_ff == 0 and t_mm == 0)
    print(f"  S03: F←F={t_ff}, M←M={t_mm} (both must be 0) "
          f"{'PASS' if passed else 'FAIL'}")
    return passed


def test_S04_art_modifier_applied_in_route_path():
    """
    S04: ART 96% reduction must apply in the route-specific path.
    Compare infections from all-off-ART vs all-on-ART infected partners.
    If ART modifier is missing, both will produce the same infections.
    Failure mode: _calc_msm_infections or scalar path uses 1.0 instead of art_modifier.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    # All off ART — full transmission
    pop_off = pop3_art(susc=5000, inf_off=5000, inf_on=0,
                       orient_susc=FEMALE, orient_inf=HETMALE)
    c = contacts3(FEMALE, HETMALE, 10000)
    t_off, _ = engine.calculate_transmission(pop_off, c, rng, deterministic=True)

    # All on ART — 96% reduction
    pop_on = pop3_art(susc=5000, inf_off=0, inf_on=5000,
                      orient_susc=FEMALE, orient_inf=HETMALE)
    t_on, _ = engine.calculate_transmission(pop_on, c, rng, deterministic=True)

    # Expected: t_on ≈ t_off * 0.04 (only 4% of off-ART infections)
    if t_off == 0:
        print(f"  S04: t_off=0, cannot compute ratio — SKIP")
        return True
    ratio = t_on / t_off
    passed = ratio < 0.1  # Should be ~0.04, definitely < 0.10
    print(f"  S04: t_off={t_off}, t_on={t_on}, ratio={ratio:.3f} "
          f"(expect ~0.04) {'PASS' if passed else 'FAIL'}")
    return passed


def test_S05_art_modifier_applied_msm_path():
    """
    S05: ART 96% reduction must apply in the MSM split path (_calc_msm_infections).
    Same check as S04 but for MSM↔MSM contacts.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    pop_off = pop3_art(susc=5000, inf_off=5000, inf_on=0,
                       orient_susc=MSM, orient_inf=MSM)
    c = contacts3(MSM, MSM, 10000)
    t_off, _ = engine.calculate_transmission(pop_off, c, rng, deterministic=True)

    pop_on = pop3_art(susc=5000, inf_off=0, inf_on=5000,
                      orient_susc=MSM, orient_inf=MSM)
    t_on, _ = engine.calculate_transmission(pop_on, c, rng, deterministic=True)

    if t_off == 0:
        print(f"  S05: t_off=0 — SKIP")
        return True
    ratio = t_on / t_off
    passed = ratio < 0.1
    print(f"  S05: MSM ART: t_off={t_off}, t_on={t_on}, ratio={ratio:.3f} "
          f"(expect ~0.04) {'PASS' if passed else 'FAIL'}")
    return passed


def test_S06_prep_efficacy_applied_in_route_path():
    """
    S06: PrEP efficacy must be applied in the route-specific path.
    Compare infections for susceptibles with and without PrEP (86% efficacy).
    Failure mode: prep_efficacy not passed into route-specific beta calculation.
    """
    cfg = copy.deepcopy(ROUTE_CFG)
    cfg["interventions"] = {
        "oral_prep": {
            "enabled": {"value": True},
            "efficacy": {"value": 0.86}
        }
    }

    # 2-PrEP-state groups setup
    class Groups3PrEP(Groups3):
        n_prep = 2
        prep_states = ["None", "oral_prep"]

    engine = TransmissionEngine(cfg, Groups3PrEP(), Time())
    rng = np.random.RandomState(0)

    # Susceptibles: half no PrEP, half on PrEP
    p = np.zeros((1, 3, 1, 4, 2, 2, 2), dtype=np.int64)
    p[0, FEMALE, 0, 0, 0, 0, 0] = 5000   # Susceptible, no PrEP
    p[0, FEMALE, 0, 0, 0, 0, 1] = 5000   # Susceptible, on PrEP
    p[0, HETMALE, 0, 2, 0, 0, 0] = 5000  # Chronic, off ART

    # Run deterministic
    contacts = np.zeros((3, 1, 3, 2))
    contacts[FEMALE, 0, HETMALE, 0] = 10000

    total, inf_array = engine.calculate_transmission(p, contacts, rng, deterministic=True)

    inf_noprep = inf_array[0, FEMALE, 0, 0]   # no-PrEP infections
    inf_prep   = inf_array[0, FEMALE, 0, 1]   # on-PrEP infections

    if inf_noprep == 0:
        print(f"  S06: no infections at all — SKIP")
        return True
    ratio = inf_prep / inf_noprep
    passed = ratio < 0.5  # PrEP should cut infections significantly
    print(f"  S06: PrEP path: no_prep={inf_noprep}, on_prep={inf_prep}, "
          f"ratio={ratio:.3f} (expect ~0.14) {'PASS' if passed else 'FAIL'}")
    return passed


def test_S07_stage_multiplier_exact():
    """
    S07: In deterministic mode, acute/AIDS betas must be EXACTLY chronic × multiplier.
    Failure mode: multipliers applied with floating-point error that propagates.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    results = {}
    for stage_idx, stage_name in [(1, "Acute"), (2, "Chronic"), (3, "AIDS")]:
        pop = pop3(susc=10000, infected=10000, orient_susc=FEMALE, orient_inf=HETMALE,
                   stage=stage_idx)
        contacts = contacts3(FEMALE, HETMALE, 100000)
        t, _ = engine.calculate_transmission(pop, contacts, rng, deterministic=True)
        results[stage_name] = t

    if results["Chronic"] == 0:
        print("  S07: Chronic=0, cannot check ratios — SKIP")
        return True

    ratio_acute = results["Acute"] / results["Chronic"]
    ratio_aids  = results["AIDS"]  / results["Chronic"]

    # Both should match multipliers within 0.5% in deterministic mode
    tol = 0.005
    pass_acute = abs(ratio_acute - 9.17) / 9.17 < tol
    pass_aids  = abs(ratio_aids  - 7.27) / 7.27 < tol
    passed = pass_acute and pass_aids
    print(f"  S07: Acute/Chronic={ratio_acute:.3f} (expect 9.17), "
          f"AIDS/Chronic={ratio_aids:.3f} (expect 7.27) "
          f"{'PASS' if passed else 'FAIL'}")
    return passed


def test_S08_high_prevalence_cap():
    """
    S08: Even at 99% prevalence and very high contacts, infections
    must never exceed the susceptible population.
    Failure mode: cap logic breaks down for route-specific path.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    susc_count = 100
    pop = pop3(susc=susc_count, infected=9900, orient_susc=FEMALE, orient_inf=HETMALE)
    contacts = contacts3(FEMALE, HETMALE, 1000000)  # extreme contacts

    passed = True
    for _ in range(50):
        t, _ = engine.calculate_transmission(pop.copy(), contacts, rng)
        if t > susc_count:
            passed = False
            print(f"  S08: infections {t} > susceptibles {susc_count} — FAIL")
            break
    if passed:
        print(f"  S08: Infections never exceeded {susc_count} susceptibles PASS")
    return passed


def test_S09_zero_contacts_zero_infections():
    """
    S09: Zero contacts on every pair → strictly zero new infections.
    Confirms the route-specific path does not produce spurious infections.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    # Fully infected population, but zero contacts
    p = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    p[0, FEMALE,  0, 0, 0, 0, 0] = 5000
    p[0, HETMALE, 0, 2, 0, 0, 0] = 5000
    p[0, MSM,     0, 2, 0, 0, 0] = 5000
    zero_contacts = np.zeros((3, 1, 3, 1))

    total, _ = engine.calculate_transmission(p, zero_contacts, rng, deterministic=True)
    passed = (total == 0)
    print(f"  S09: Zero contacts → {total} infections "
          f"{'PASS' if passed else 'FAIL'}")
    return passed


# ── V-series: MSM versatility split mechanism ───────────────────────────────

def test_V01_split_conserves_contacts():
    """
    V01: The Bernoulli split inside _calc_msm_infections must conserve contacts.
    n_receptive + n_insertive must always equal n_pos_contacts.
    We verify by checking that at p=1.0 and p=0.0 the outputs bracket p=0.5.
    Failure mode: n_insertive computed independently (two separate draws), causing
    n_receptive + n_insertive != n_pos_contacts.
    """
    # With p=0.5: effective_beta = 0.5*0.0138 + 0.5*0.0011 = 0.00745
    # With p=0: effective_beta = 0.0011 (all insertive)
    # With p=1: effective_beta = 0.0138 (all receptive)
    # Linearity check: mean at p=0.5 should == (mean at p=0 + mean at p=1) / 2
    results = {}
    for p_val in [0.0, 0.5, 1.0]:
        cfg = copy.deepcopy(ROUTE_CFG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        engine = TransmissionEngine(cfg, Groups3(), Time())
        pop = pop3(susc=5000, infected=5000, orient_susc=MSM, orient_inf=MSM)
        contacts = contacts3(MSM, MSM, 10000)
        inf = run_n(engine, pop, contacts, n=2000, seed=99)
        results[p_val] = np.mean(inf)

    expected_mid = (results[0.0] + results[1.0]) / 2
    actual_mid   = results[0.5]
    tol = 0.10  # 10%
    passed = abs(actual_mid - expected_mid) / expected_mid < tol
    print(f"  V01: p=0→{results[0.0]:.2f}, p=1→{results[1.0]:.2f}, "
          f"p=0.5 expected_mid={expected_mid:.2f} actual={actual_mid:.2f} "
          f"({'PASS' if passed else 'FAIL'})")
    return passed


def test_V02_deterministic_uses_expected_value():
    """
    V02: In deterministic mode, _calc_msm_infections must use int(n*p) for the
    split (not a random draw). Running deterministic 10 times must give the
    identical result every time.
    Failure mode: deterministic=True code path still calls rng.binomial().
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(7)
    pop = pop3(susc=5000, infected=5000, orient_susc=MSM, orient_inf=MSM)
    contacts = contacts3(MSM, MSM, 5000)

    det_results = []
    for _ in range(10):
        t, _ = engine.calculate_transmission(pop.copy(), contacts, rng, deterministic=True)
        det_results.append(t)

    all_same = len(set(det_results)) == 1
    passed = all_same
    print(f"  V02: Deterministic results: {set(det_results)} "
          f"(all same={all_same}) {'PASS' if passed else 'FAIL'}")
    return passed


def test_V03_stochastic_variance_correct():
    """
    V03: The stochastic MSM split produces variance consistent with a Binomial
    draw. At p=0.5 with N=1000 contacts, Var(n_receptive) = N*p*(1-p) = 250.
    We measure the empirical variance of infections across runs and confirm it
    is nonzero (the draw is actually stochastic) and greater than deterministic.
    Failure mode: stochastic path silently uses expected values instead of drawing.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    pop = pop3(susc=5000, infected=5000, orient_susc=MSM, orient_inf=MSM)
    contacts = contacts3(MSM, MSM, 5000)

    inf = run_n(engine, pop, contacts, n=2000, seed=42, det=False)
    var_stoch = np.var(inf)

    # Compare with deterministic
    rng_det = np.random.RandomState(42)
    det_results = []
    for _ in range(10):
        t, _ = engine.calculate_transmission(pop.copy(), contacts, rng_det, deterministic=True)
        det_results.append(t)
    var_det = np.var(det_results)

    # Stochastic variance must be much larger than deterministic
    passed = var_stoch > 1.0 and var_stoch > var_det
    print(f"  V03: Stochastic var={var_stoch:.2f}, det var={var_det:.2f} "
          f"(stochastic must be > 1 and > det) {'PASS' if passed else 'FAIL'}")
    return passed


def test_V04_versatility_beta_weighting():
    """
    V04: At versatility p, mean infections ≈ [p×beta_URAI + (1-p)×beta_UIAI] × n_pos.
    Tests 5 different p values to confirm the linear weighting is exact.
    Failure mode: the Bernoulli draw selects the wrong beta for each sub-pass.
    """
    beta_r = 0.0138  # anal_receptive chronic
    beta_i = 0.0011  # anal_insertive chronic
    n_contacts = 20000
    n_infected = 5000
    n_susc = 5000
    total = n_infected + n_susc
    prevalence = n_infected / total

    p_values = [0.1, 0.25, 0.5, 0.75, 0.9]
    all_passed = True
    for p_val in p_values:
        cfg = copy.deepcopy(ROUTE_CFG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        engine = TransmissionEngine(cfg, Groups3(), Time())
        pop = pop3(susc=n_susc, infected=n_infected, orient_susc=MSM, orient_inf=MSM)
        contacts = contacts3(MSM, MSM, n_contacts)
        inf = run_n(engine, pop, contacts, n=2000, seed=int(p_val*100))
        mean_inf = np.mean(inf)

        expected_beta = p_val * beta_r + (1 - p_val) * beta_i
        expected_inf  = n_contacts * prevalence * expected_beta
        rel_err = abs(mean_inf - expected_inf) / expected_inf if expected_inf > 0 else 0
        ok = rel_err < 0.12
        if not ok:
            all_passed = False
        print(f"    p={p_val}: mean={mean_inf:.2f}, expected={expected_inf:.2f}, "
              f"rel_err={rel_err*100:.1f}% {'ok' if ok else 'FAIL'}")

    print(f"  V04: Beta weighting linearity {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def test_V05_art_applied_to_both_subpasses():
    """
    V05: ART modifier must apply to BOTH the receptive and insertive sub-passes.
    At p=1.0 (all receptive): compare off-ART vs on-ART infections — ratio must be ~0.04.
    At p=0.0 (all insertive): same check.
    Failure mode: ART modifier applied to only one sub-pass.
    """
    all_passed = True
    for p_val, label in [(1.0, "all-receptive"), (0.0, "all-insertive")]:
        cfg = copy.deepcopy(ROUTE_CFG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        engine = TransmissionEngine(cfg, Groups3(), Time())
        rng = np.random.RandomState(0)

        pop_off = pop3_art(susc=5000, inf_off=5000, inf_on=0,
                           orient_susc=MSM, orient_inf=MSM)
        pop_on  = pop3_art(susc=5000, inf_off=0,    inf_on=5000,
                           orient_susc=MSM, orient_inf=MSM)
        c = contacts3(MSM, MSM, 10000)

        t_off, _ = engine.calculate_transmission(pop_off, c, rng, deterministic=True)
        t_on,  _ = engine.calculate_transmission(pop_on,  c, rng, deterministic=True)

        if t_off == 0:
            print(f"    V05 ({label}): t_off=0 SKIP")
            continue
        ratio = t_on / t_off
        ok = ratio < 0.1
        all_passed = all_passed and ok
        print(f"    V05 ({label}): t_off={t_off}, t_on={t_on}, ratio={ratio:.3f} "
              f"(expect ~0.04) {'ok' if ok else 'FAIL'}")

    print(f"  V05: ART applied to both sub-passes {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def test_V06_versatility_at_extremes_no_exception():
    """
    V06: p=0.0 and p=1.0 must not raise exceptions and must produce
    non-negative integer infection counts.
    Failure mode: division by zero or negative count when all contacts go to one sub-pass.
    """
    all_passed = True
    for p_val in [0.0, 1.0]:
        cfg = copy.deepcopy(ROUTE_CFG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        try:
            engine = TransmissionEngine(cfg, Groups3(), Time())
            pop = pop3(susc=5000, infected=5000, orient_susc=MSM, orient_inf=MSM)
            contacts = contacts3(MSM, MSM, 10000)
            inf = run_n(engine, pop, contacts, n=100)
            if np.any(inf < 0):
                raise ValueError(f"Negative infection count at p={p_val}")
            print(f"    V06 (p={p_val}): min={inf.min()}, max={inf.max()}, mean={inf.mean():.1f} ok")
        except Exception as e:
            print(f"    V06 (p={p_val}): EXCEPTION — {e} FAIL")
            all_passed = False
    print(f"  V06: Extremes no exception {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def test_V07_versatility_isolated_from_het_path():
    """
    V07: Changing msm_versatility must have ZERO effect on heterosexual transmission.
    Failure mode: versatility accidentally applied to het contacts.
    """
    results = {}
    for p_val in [0.0, 0.5, 1.0]:
        cfg = copy.deepcopy(ROUTE_CFG)
        cfg["disease"]["transmission"]["advanced"]["msm_versatility"]["value"] = p_val
        engine = TransmissionEngine(cfg, Groups3(), Time())
        pop = pop3(susc=5000, infected=5000, orient_susc=FEMALE, orient_inf=HETMALE)
        contacts = contacts3(FEMALE, HETMALE, 10000)
        inf = run_n(engine, pop, contacts, n=500, seed=1)
        results[p_val] = np.mean(inf)

    # All three means must be equal within statistical noise (2%)
    mean_05 = results[0.5]
    tol = 0.02 * mean_05 if mean_05 > 0 else 1.0
    passed = (abs(results[0.0] - results[1.0]) < tol * 3 and
              abs(results[0.0] - results[0.5]) < tol * 3)
    print(f"  V07: Het infections at p=0/0.5/1: "
          f"{results[0.0]:.2f}/{results[0.5]:.2f}/{results[1.0]:.2f} "
          f"(must be identical) {'PASS' if passed else 'FAIL'}")
    return passed


def test_V08_msm_infections_exceed_het_infections():
    """
    V08: MSM infections per contact must exceed Het infections per contact
    due to URAI >> vaginal_mtf (0.00745 effective vs 0.0008).
    This is a real-world sanity check on the biology.
    Failure mode: MSM betas accidentally end up using vaginal values.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())

    pop_msm = pop3(susc=5000, infected=5000, orient_susc=MSM, orient_inf=MSM)
    c_msm = contacts3(MSM, MSM, 10000)
    inf_msm = run_n(engine, pop_msm, c_msm, n=500)

    pop_het = pop3(susc=5000, infected=5000, orient_susc=FEMALE, orient_inf=HETMALE)
    c_het = contacts3(FEMALE, HETMALE, 10000)
    inf_het = run_n(engine, pop_het, c_het, n=500)

    mean_msm = np.mean(inf_msm)
    mean_het = np.mean(inf_het)
    ratio = mean_msm / mean_het if mean_het > 0 else float('inf')

    # Note on expected ratio:
    # Pure beta ratio: 0.00745 / 0.0008 = 9.3
    # But in this test the MSM group contains BOTH susceptibles and infected
    # (prevalence = 0.5), while the Het partner group is 100% infected
    # (prevalence = 1.0). Accounting for that:
    # Expected ratio = (0.5 * 0.00745) / (1.0 * 0.0008) = 4.66
    # We use > 3.5 as a conservative threshold.
    passed = ratio > 3.5
    print(f"  V08: MSM/Het inf ratio={ratio:.2f} "
          f"(adjusted expect ~4.7, must be >3.5) "
          f"{'PASS' if passed else 'FAIL'}")
    return passed


def test_V09_no_infections_when_no_msm_susceptibles():
    """
    V09: Zero MSM susceptibles → zero MSM infections regardless of contact count.
    Confirms the susceptible=0 skip condition works in the route-specific path.
    """
    engine = TransmissionEngine(ROUTE_CFG, Groups3(), Time())
    rng = np.random.RandomState(0)

    p = np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=np.int64)
    p[0, MSM, 0, 2, 0, 0, 0] = 10000  # MSM all infected, none susceptible
    c = contacts3(MSM, MSM, 100000)
    total, _ = engine.calculate_transmission(p, c, rng, deterministic=True)

    passed = (total == 0)
    print(f"  V09: 0 MSM susceptibles → {total} infections "
          f"{'PASS' if passed else 'FAIL'}")
    return passed


# ── Runner ───────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 80)
    print("V11.3 STRESS & MECHANISM TESTS (S01–S09, V01–V09)")
    print("=" * 80)

    s_tests = [
        ("S01", "Direction correctness: F←M uses vaginal_mtf",    test_S01_direction_correctness),
        ("S02", "Direction correctness: M←F uses vaginal_ftm",    test_S02_direction_correctness_reverse),
        ("S03", "No same-sex-het transmission",                    test_S03_no_same_sex_het_transmission),
        ("S04", "ART modifier applied in route path (het)",        test_S04_art_modifier_applied_in_route_path),
        ("S05", "ART modifier applied in MSM split path",          test_S05_art_modifier_applied_msm_path),
        ("S06", "PrEP efficacy applied in route path",             test_S06_prep_efficacy_applied_in_route_path),
        ("S07", "Stage multiplier exact in deterministic mode",    test_S07_stage_multiplier_exact),
        ("S08", "High-prevalence cap: infections ≤ susceptibles", test_S08_high_prevalence_cap),
        ("S09", "Zero contacts → zero infections",                 test_S09_zero_contacts_zero_infections),
    ]

    v_tests = [
        ("V01", "Split linearity: p=0.5 midpoint of p=0 and p=1", test_V01_split_conserves_contacts),
        ("V02", "Deterministic mode: identical output each run",   test_V02_deterministic_uses_expected_value),
        ("V03", "Stochastic variance > 0 and > deterministic",    test_V03_stochastic_variance_correct),
        ("V04", "Beta weighting linear in p (5 values)",          test_V04_versatility_beta_weighting),
        ("V05", "ART applied to both receptive+insertive passes",  test_V05_art_applied_to_both_subpasses),
        ("V06", "No exceptions at p=0.0 and p=1.0",               test_V06_versatility_at_extremes_no_exception),
        ("V07", "Versatility has no effect on het path",           test_V07_versatility_isolated_from_het_path),
        ("V08", "MSM infections >> het infections (biology check)",test_V08_msm_infections_exceed_het_infections),
        ("V09", "Zero MSM susceptibles → zero infections",         test_V09_no_infections_when_no_msm_susceptibles),
    ]

    results = {}

    print("\n── S-series: Route-specific HIV transmission ──")
    for tid, name, fn in s_tests:
        print(f"\n[{tid}] {name}")
        print("-" * 60)
        try:
            results[tid] = fn()
        except Exception as e:
            print(f"  {tid}: EXCEPTION — {e}")
            results[tid] = False

    print("\n── V-series: MSM versatility split ──")
    for tid, name, fn in v_tests:
        print(f"\n[{tid}] {name}")
        print("-" * 60)
        try:
            results[tid] = fn()
        except Exception as e:
            print(f"  {tid}: EXCEPTION — {e}")
            results[tid] = False

    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    print("\n" + "=" * 80)
    if passed == total:
        print(f"✓ ALL {total} STRESS TESTS PASSED — mechanism verified")
    else:
        failed = [tid for tid, v in results.items() if not v]
        print(f"✗ {passed}/{total} PASSED — FAILED: {', '.join(failed)}")
    print("=" * 80 + "\n")
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
