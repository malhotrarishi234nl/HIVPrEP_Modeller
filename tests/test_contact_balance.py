"""
Tests for V11 contact balance and Garnett epsilon parameterisation.

Covers:
  1. Heterosexual contact balance — contacts[F,f,M,m] == contacts[M,m,F,f]
  2. MSM contact symmetry     — contacts[MSM,i,MSM,j] == contacts[MSM,j,MSM,i]
  3. epsilon=0 (proportional) — mixing weights match male/MSM contact availability
  4. epsilon=1 (assortative)  — only diagonal entries are non-zero
  5. Geometric heterosexual pool — balanced cells equal sqrt(female-side * male-side)
  6. Backward-compat legacy path — V10 matrix config triggers DeprecationWarning,
                                   produces same numerical output as old code

References:
    Garnett GP, Anderson RM (1994). J Acquir Immune Defic Syndr 7(11):1181-1192.
"""

import sys
import os
import json
import warnings
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_v3 import SimulationEngineV3
from config_utils import extract_value

TOL = 1e-10  # floating-point equality tolerance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_v11_config_path():
    configs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")
    return os.path.join(configs, "v11_configuration.json")


def _load_v6_config_path():
    """Return path to a V6-format config that has legacy matrix keys (no epsilon)."""
    configs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")
    # v10_v6_rel_pop.json is a V6-format config with the old matrix keys
    return os.path.join(configs, "v10_v6_rel_pop.json")


def _build_engine_and_contacts(config_path, epsilon_override=None):
    """
    Instantiate an engine from config_path, optionally override epsilon values,
    run one timestep of _step_contacts_and_mixing(), and return:
        contacts  — (n_orient, n_act, n_orient, n_act) ndarray
        engine    — the SimulationEngineV3 instance
    """
    engine = SimulationEngineV3(config_path)

    if epsilon_override is not None:
        # Patch the live config dict that the engine uses
        for key, val in epsilon_override.items():
            engine.config["mixing"][key] = {"value": val}
        # Remove legacy matrix keys if present so epsilon path is taken
        engine.config["mixing"].pop("heterosexual", None)
        engine.config["mixing"].pop("msm", None)

    contacts = engine._step_contacts_and_mixing()
    return contacts, engine


# ---------------------------------------------------------------------------
# Test 1: Heterosexual contact balance
# ---------------------------------------------------------------------------

def test_hetero_contact_balance():
    """
    contacts[Female, f_act, Hetero_Male, m_act]
      must equal
    contacts[Hetero_Male, m_act, Female, f_act]
    for ALL (f_act, m_act) combinations.
    """
    contacts, engine = _build_engine_and_contacts(_load_v11_config_path())

    f   = engine.groups.orient_to_idx.get("Female", 0)
    m   = engine.groups.orient_to_idx.get("Heterosexual_Male", 1)
    n   = engine.groups.n_activities

    max_err = 0.0
    for f_act in range(n):
        for m_act in range(n):
            c_fm = contacts[f, f_act, m, m_act]
            c_mf = contacts[m, m_act, f, f_act]
            err  = abs(c_fm - c_mf)
            if err > max_err:
                max_err = err
            assert err < TOL, (
                f"Contact balance violation at (f_act={f_act}, m_act={m_act}): "
                f"contacts[F,{f_act},M,{m_act}]={c_fm:.6g} != "
                f"contacts[M,{m_act},F,{f_act}]={c_mf:.6g}  diff={err:.2e}"
            )

    print(f"PASS: Heterosexual contact balance (max err={max_err:.2e})")
    return True


# ---------------------------------------------------------------------------
# Test 2: MSM contact symmetry
# ---------------------------------------------------------------------------

def test_msm_contact_symmetry():
    """
    contacts[MSM, i, MSM, j]  must equal  contacts[MSM, j, MSM, i]
    for ALL (i, j) activity class combinations.
    """
    contacts, engine = _build_engine_and_contacts(_load_v11_config_path())

    msm = engine.groups.orient_to_idx.get("MSM", 2)
    n   = engine.groups.n_activities

    max_err = 0.0
    for i in range(n):
        for j in range(n):
            c_ij = contacts[msm, i, msm, j]
            c_ji = contacts[msm, j, msm, i]
            err  = abs(c_ij - c_ji)
            if err > max_err:
                max_err = err
            assert err < TOL, (
                f"MSM symmetry violation at (i={i}, j={j}): "
                f"contacts[MSM,{i},MSM,{j}]={c_ij:.6g} != "
                f"contacts[MSM,{j},MSM,{i}]={c_ji:.6g}  diff={err:.2e}"
            )

    print(f"PASS: MSM contact symmetry (max err={max_err:.2e})")
    return True


# ---------------------------------------------------------------------------
# Test 3: epsilon=0 gives proportional mixing
# ---------------------------------------------------------------------------

def test_epsilon_zero_proportional_mixing():
    """
    With epsilon_hetero=0 and epsilon_msm=0, mixing should be fully proportional.

    For heterosexual: contacts[F, f, M, m] / sum_m(contacts[F, f, M, m])
    should equal  male_weight[m] = N_M(m)*c_M(m) / sum_k N_M(k)*c_M(k)
    for every f_act (to within TOL, ignoring zero-contact rows).
    """
    contacts, engine = _build_engine_and_contacts(
        _load_v11_config_path(),
        epsilon_override={"epsilon_hetero": 0.0, "epsilon_msm": 0.0}
    )

    f   = engine.groups.orient_to_idx.get("Female", 0)
    m   = engine.groups.orient_to_idx.get("Heterosexual_Male", 1)
    msm = engine.groups.orient_to_idx.get("MSM", 2)
    n   = engine.groups.n_activities

    # Recompute expected male weights
    active_pop    = np.sum(engine.state.population, axis=(0, 3, 4, 5, 6))
    orient_mults  = extract_value(
        engine.config.get("groups", {}).get("orientation_contact_multipliers", {"value": {}})
    )
    base_rates    = np.array([
        engine.groups.activity_contact_rates[a] for a in engine.groups.activities
    ])
    mult_m = orient_mults.get("Heterosexual_Male", 1.0)
    male_c = active_pop[m] * (base_rates * mult_m) * engine.time.years_per_step
    male_w = male_c / (male_c.sum() + 1e-10)

    for f_act in range(n):
        row = contacts[f, f_act, m, :]
        row_sum = row.sum()
        if row_sum < 1e-9:
            continue  # skip zero-contact rows (empty activity class)
        row_norm = row / row_sum
        np.testing.assert_allclose(
            row_norm, male_w, atol=1e-6,
            err_msg=f"epsilon=0 hetero: row f_act={f_act} not proportional to male weights"
        )

    # MSM proportional check
    mult_msm = orient_mults.get("MSM", 1.0)
    msm_c    = active_pop[msm] * (base_rates * mult_msm) * engine.time.years_per_step
    msm_w    = msm_c / (msm_c.sum() + 1e-10)

    for i in range(n):
        row = contacts[msm, i, msm, :]
        row_sum = row.sum()
        if row_sum < 1e-9:
            continue
        row_norm = row / row_sum
        np.testing.assert_allclose(
            row_norm, msm_w, atol=1e-6,
            err_msg=f"epsilon=0 MSM: row i={i} not proportional to MSM weights"
        )

    print("PASS: epsilon=0 gives proportional mixing")
    return True


# ---------------------------------------------------------------------------
# Test 4: epsilon=1 gives fully assortative mixing (diagonal only)
# ---------------------------------------------------------------------------

def test_epsilon_one_assortative_mixing():
    """
    With epsilon=1, all off-diagonal entries should be zero.
    """
    contacts, engine = _build_engine_and_contacts(
        _load_v11_config_path(),
        epsilon_override={"epsilon_hetero": 1.0, "epsilon_msm": 1.0}
    )

    f   = engine.groups.orient_to_idx.get("Female", 0)
    m   = engine.groups.orient_to_idx.get("Heterosexual_Male", 1)
    msm = engine.groups.orient_to_idx.get("MSM", 2)
    n   = engine.groups.n_activities

    for f_act in range(n):
        for m_act in range(n):
            val = contacts[f, f_act, m, m_act]
            if f_act != m_act:
                assert abs(val) < TOL, (
                    f"epsilon=1 hetero: off-diagonal [{f_act},{m_act}]={val:.4g} should be 0"
                )

    for i in range(n):
        for j in range(n):
            val = contacts[msm, i, msm, j]
            if i != j:
                assert abs(val) < TOL, (
                    f"epsilon=1 MSM: off-diagonal [{i},{j}]={val:.4g} should be 0"
                )

    print("PASS: epsilon=1 gives fully assortative (diagonal-only) mixing")
    return True


# ---------------------------------------------------------------------------
# Test 5: Geometric heterosexual pool
# ---------------------------------------------------------------------------

def test_geometric_hetero_pool_matches_bidirectional_sqrt():
    """
    V12.14: Heterosexual contacts use a geometric bidirectional pool:

      P_geo(f, m) = sqrt(P_F(f, m) * P_M(m, f))

    where P_F uses female contact supply and male partner-availability weights,
    and P_M uses male contact supply and female partner-availability weights.
    """
    contacts, engine = _build_engine_and_contacts(_load_v11_config_path())

    f = engine.groups.orient_to_idx.get("Female", 0)
    m = engine.groups.orient_to_idx.get("Heterosexual_Male", 1)
    n = engine.groups.n_activities

    # Recompute expected geometric pool independently from the engine.
    active_pop   = np.sum(engine.state.population, axis=(0, 3, 4, 5, 6))
    orient_mults = extract_value(
        engine.config.get("groups", {}).get("orientation_contact_multipliers", {"value": {}})
    )
    base_rates   = np.array([
        engine.groups.activity_contact_rates[a] for a in engine.groups.activities
    ])
    mult_f       = orient_mults.get("Female", 1.0)
    mult_m       = orient_mults.get("Heterosexual_Male", 1.0)
    female_c     = active_pop[f] * base_rates * mult_f * engine.time.years_per_step
    male_c       = active_pop[m] * base_rates * mult_m * engine.time.years_per_step
    female_w     = female_c / (female_c.sum() + 1e-10)
    male_w       = male_c / (male_c.sum() + 1e-10)
    epsilon      = float(extract_value(engine.config["mixing"]["epsilon_hetero"]))

    for f_act in range(n):
        for m_act in range(n):
            assort = 1.0 if f_act == m_act else 0.0
            rho_f = epsilon * assort + (1.0 - epsilon) * male_w[m_act]
            rho_m = epsilon * assort + (1.0 - epsilon) * female_w[f_act]
            female_side = female_c[f_act] * rho_f
            male_side = male_c[m_act] * rho_m
            expected = np.sqrt(female_side * male_side)
            actual = contacts[f, f_act, m, m_act]
            reverse = contacts[m, m_act, f, f_act]
            assert abs(actual - expected) < 1e-6, (
                f"Geometric hetero pool mismatch at f_act={f_act}, m_act={m_act}: "
                f"expected={expected:.6g} actual={actual:.6g}"
            )
            assert abs(actual - reverse) < TOL, (
                f"Geometric hetero pool is not balanced at f_act={f_act}, m_act={m_act}: "
                f"forward={actual:.6g} reverse={reverse:.6g}"
            )

    print("PASS: Geometric heterosexual pool matches bidirectional sqrt formula")
    return True


# ---------------------------------------------------------------------------
# Test 6: Backward-compat legacy path (V10 matrix config)
# ---------------------------------------------------------------------------

def test_legacy_matrix_backward_compat():
    """
    When loading a V6-format config with the old matrix keys (no epsilon keys),
    the engine should:
    (a) NOT crash
    (b) emit a DeprecationWarning
    (c) produce a contacts tensor where entries match the original matrix-driven formula:
        contacts[F, f, M, m] = total_contacts_female[f] * hetero_mix[f, m]
    """
    v6_path = _load_v6_config_path()
    if not os.path.exists(v6_path):
        print(f"SKIP: {v6_path} not found — skipping legacy backward-compat test")
        return True

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        contacts, engine = _build_engine_and_contacts(v6_path)

    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) > 0, (
        "Expected a DeprecationWarning for legacy matrix config, got none"
    )
    assert "epsilon" in str(dep_warnings[0].message).lower() or \
           "legacy" in str(dep_warnings[0].message).lower(), (
        f"DeprecationWarning message unexpected: {dep_warnings[0].message}"
    )

    # Verify the tensor is non-trivial (something was computed)
    assert contacts.sum() > 0, "contacts_allocated should be non-zero with legacy config"

    # Verify formula: contacts[F, f, M, m] = N_F(f)*c_F(f) * hetero_mix[f, m]
    # (this is what the old code computed — we reproduce it here independently)
    f   = engine.groups.orient_to_idx.get("Female", 0)
    m   = engine.groups.orient_to_idx.get("Heterosexual_Male", 1)
    n   = engine.groups.n_activities

    active_pop   = np.sum(engine.state.population, axis=(0, 3, 4, 5, 6))
    orient_mults = extract_value(
        engine.config.get("groups", {}).get("orientation_contact_multipliers", {"value": {}})
    )
    base_rates   = np.array([
        engine.groups.activity_contact_rates[a] for a in engine.groups.activities
    ])
    mult_f       = orient_mults.get("Female", 1.0)
    female_c     = active_pop[f] * (base_rates * mult_f) * engine.time.years_per_step

    hetero_mix_cfg = extract_value(engine.config["mixing"]["heterosexual"])
    activities     = engine.groups.activities
    for i, fa in enumerate(activities):
        for j, ta in enumerate(activities):
            expected = female_c[i] * hetero_mix_cfg.get(fa, {}).get(ta, 0)
            actual   = contacts[f, i, m, j]
            err      = abs(actual - expected)
            assert err < TOL, (
                f"Legacy path mismatch at [{fa},{ta}]: "
                f"expected={expected:.6g} actual={actual:.6g} diff={err:.2e}"
            )

    print("PASS: Backward-compat legacy matrix path (DeprecationWarning + correct output)")
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = []
    results.append(("hetero_contact_balance",       test_hetero_contact_balance()))
    results.append(("msm_contact_symmetry",          test_msm_contact_symmetry()))
    results.append(("epsilon_zero_proportional",     test_epsilon_zero_proportional_mixing()))
    results.append(("epsilon_one_assortative",       test_epsilon_one_assortative_mixing()))
    results.append(("geometric_hetero_pool",         test_geometric_hetero_pool_matches_bidirectional_sqrt()))
    results.append(("legacy_matrix_backward_compat", test_legacy_matrix_backward_compat()))

    print("\n" + "=" * 60)
    passed = sum(1 for _, r in results if r)
    total  = len(results)
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n{passed}/{total} tests passed")

    if passed < total:
        sys.exit(1)
