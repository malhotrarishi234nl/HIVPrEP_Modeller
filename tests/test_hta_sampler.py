"""
tests/test_hta_sampler.py — V11.11.2

8 tests for HTASampler: parameter discovery, distribution moments,
draw application, mutation safety, placeholder filtering, shape validation.
"""

import copy
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HTA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "hta_config.json",
)
PSA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "psa_config.json",
)


@pytest.fixture(scope="module")
def hta_config():
    with open(HTA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def psa_config():
    with open(PSA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sampler(hta_config, psa_config):
    from hta_sampler import HTASampler
    return HTASampler(hta_config, psa_config)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_discover_utility_params(sampler):
    """
    HTASampler should discover at least 4 utility parameters
    (hiv_acute, hiv_chronic_on_art, hiv_chronic_off_art, aids_on_art)
    that have non-fixed Beta distribution blocks.
    """
    util_paths = [p for p in sampler.param_paths if p.startswith("utilities.")]
    assert len(util_paths) >= 4, (
        f"Expected >= 4 utility parameters, found {len(util_paths)}: {util_paths}"
    )
    expected = {
        "utilities.hiv_acute",
        "utilities.hiv_chronic_on_art",
        "utilities.hiv_chronic_off_art",
        "utilities.aids_on_art",
    }
    missing = expected - set(util_paths)
    assert not missing, f"Missing expected utility params: {missing}"


def test_aids_on_art_uses_the_same_psa_draw_as_chronic_on_art(sampler):
    """The derived utility is linked, never sampled as an independent belief."""
    draws = sampler.generate_draws(128, seed=20261014)
    np.testing.assert_array_equal(
        draws["utilities.aids_on_art"].to_numpy(),
        draws["utilities.hiv_chronic_on_art"].to_numpy(),
    )


def test_linked_parameter_respects_its_declared_bounds(sampler):
    draws = sampler.generate_draws(4000, seed=20261014)
    linked = draws["utilities.aids_on_art"]
    assert linked.between(0.85, 1.0, inclusive="both").all()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("utilities.missing", "missing"),
        ("utilities.susceptible", "fixed"),
        ("utilities.hiv_chronic_on_art.value", "nested field"),
        ("utilities.aids_on_art", "cannot reference itself"),
        ("utilities.aids_off_art", "forward"),
    ],
)
def test_invalid_link_sources_fail_closed(hta_config, psa_config, source, message):
    from hta_sampler import HTASampler

    bad_config = copy.deepcopy(hta_config)
    bad_config["utilities"]["aids_on_art"]["distribution"]["source_path"] = source
    with pytest.raises(ValueError, match=message):
        HTASampler(bad_config, psa_config)


def test_link_to_explicitly_excluded_source_fails_closed(hta_config, psa_config):
    from hta_sampler import HTASampler

    excluded = copy.deepcopy(psa_config)
    excluded["hta_sampling"]["exclude_parameters"]["value"] = [
        "utilities.hiv_chronic_on_art"
    ]
    with pytest.raises(ValueError, match="exclude_parameters"):
        HTASampler(hta_config, excluded)


def test_link_to_placeholder_filtered_source_fails_closed(hta_config, psa_config):
    from hta_sampler import HTASampler

    bad_config = copy.deepcopy(hta_config)
    bad_config["utilities"]["hiv_chronic_on_art"][
        "distribution_status"
    ] = "placeholder"
    filtered = copy.deepcopy(psa_config)
    filtered["hta_sampling"]["exclude_placeholders"]["value"] = True
    with pytest.raises(ValueError, match="exclude_placeholders"):
        HTASampler(bad_config, filtered)


def test_nested_link_fails_closed(hta_config, psa_config):
    from hta_sampler import HTASampler

    bad_config = copy.deepcopy(hta_config)
    chronic = bad_config["utilities"]["hiv_chronic_on_art"]
    chronic["bounds"] = {"lower": 0.7, "upper": 0.87}
    chronic["distribution"] = {
        "type": "linked",
        "source_path": "utilities.hiv_acute",
    }
    with pytest.raises(ValueError, match="nested link"):
        HTASampler(bad_config, psa_config)


def test_incompatible_link_bounds_fail_instead_of_clipping(hta_config, psa_config):
    from hta_sampler import HTASampler

    bad_config = copy.deepcopy(hta_config)
    bad_config["utilities"]["aids_on_art"]["bounds"]["upper"] = 0.98
    with pytest.raises(ValueError, match="align the bounds rather than silently clipping"):
        HTASampler(bad_config, psa_config)


def test_runtime_link_bound_guard_refuses_out_of_range_draws(hta_config, psa_config):
    """Defence in depth: even registry tampering cannot turn the link into a clip."""
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    link_index = sampler.param_paths.index("utilities.aids_on_art")
    path, frozen, _bounds, entry = sampler._param_specs[link_index]
    sampler._param_specs[link_index] = (path, frozen, (0.85, 0.98), entry)
    with pytest.raises(ValueError, match="refusing to clip"):
        sampler.generate_draws(4000, seed=20261014)


def test_discover_cost_params(sampler):
    """
    HTASampler should discover at least 5 cost parameters with Gamma distributions
    (disease costs + prevention costs).
    """
    cost_paths = [
        p for p in sampler.param_paths
        if p.startswith("disease_costs.") or p.startswith("prevention_costs.")
    ]
    assert len(cost_paths) >= 5, (
        f"Expected >= 5 cost parameters, found {len(cost_paths)}: {cost_paths}"
    )


def test_gamma_method_of_moments(hta_config, psa_config):
    """
    For art_first_line, the Gamma sampler should use the configured current
    mean and SE. This test intentionally reads the value from config so it
    does not go stale when the ART price source changes.
    """
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    path = "disease_costs.art_first_line"
    assert path in sampler.param_paths, (
        f"'{path}' not discovered — check discovery logic"
    )

    # Find the frozen distribution for art_first_line
    frozen = None
    for p, dist, bounds, entry in sampler._param_specs:
        if p == path:
            frozen = dist
            break

    assert frozen is not None
    expected_mean = hta_config["disease_costs"]["art_first_line"]["distribution"]["mean"]
    samples = frozen.rvs(size=5000, random_state=42)
    assert abs(samples.mean() - expected_mean) / expected_mean < 0.05, (
        f"art_first_line: sample mean {samples.mean():.1f} not within 5% "
        f"of configured mean {expected_mean}"
    )


def test_beta_utility_sampling(hta_config, psa_config):
    """
    For hiv_acute: Beta(alpha=70.4, beta=19.8).
    Theoretical mean = 70.4 / (70.4 + 19.8) ≈ 0.78.
    Draw N=5000. Assert |sample_mean - 0.78| / 0.78 < 0.05.
    """
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    path = "utilities.hiv_acute"
    assert path in sampler.param_paths

    frozen = None
    for p, dist, bounds, entry in sampler._param_specs:
        if p == path:
            frozen = dist
            break

    assert frozen is not None
    samples = frozen.rvs(size=5000, random_state=42)
    theoretical_mean = 70.4 / (70.4 + 19.8)  # ≈ 0.78
    assert abs(samples.mean() - theoretical_mean) / theoretical_mean < 0.05, (
        f"hiv_acute: sample mean {samples.mean():.4f} not within 5% of {theoretical_mean:.4f}"
    )


def test_negative_ae_utility_decrement_beta_draws_keep_sign(hta_config, psa_config):
    """
    AE utility decrements are stored as negative utility losses. Their Beta
    distributions describe the positive magnitude, but PSA draws must be
    injected back as negative values to preserve config semantics.
    """
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    decrement_paths = [
        path for path in sampler.param_paths
        if path.startswith("adverse_events.items.")
        and path.endswith(".utility_decrement")
    ]
    assert decrement_paths

    draws = sampler.generate_draws(n=5000, seed=123)
    modified = sampler.apply_draw_to_hta_config(hta_config, draws.iloc[0])

    for path in decrement_paths:
        values = draws[path]
        assert (values <= 0.0).all(), f"{path} produced positive decrement draws"
        assert (values >= -1.0).all(), f"{path} produced draws below Beta support"

        item_id = path.split(".")[2]
        item = next(
            item for item in modified["adverse_events"]["items"]
            if item["id"] == item_id
        )
        assert item["utility_decrement"]["value"] <= 0.0

    isr_mean = draws[
        "adverse_events.items.lenacapavir_isr_ae.utility_decrement"
    ].mean()
    nodule_mean = draws[
        "adverse_events.items.lenacapavir_isr_persistent_nodule.utility_decrement"
    ].mean()
    assert isr_mean == pytest.approx(-0.01, rel=0.10)
    assert nodule_mean == pytest.approx(-0.02, rel=0.10)


def test_apply_draw_to_hta_config(hta_config, psa_config):
    """
    Generate 1 draw. Apply to a copy of hta_config.
    Verify that at least some utility values differ from the originals
    (with high probability, sampled values differ from point estimates).
    """
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    draws = sampler.generate_draws(n=1, seed=99)
    draw_row = draws.iloc[0]

    modified = sampler.apply_draw_to_hta_config(hta_config, draw_row)

    # At least the utility parameters should change
    changed = False
    for path in sampler.param_paths:
        if path.startswith("utilities."):
            key = path.split(".")[1]
            orig_val = hta_config["utilities"][key]["value"]
            new_val = modified["utilities"][key]["value"]
            if abs(new_val - orig_val) > 1e-10:
                changed = True
                break

    assert changed, (
        "No utility values changed after applying HTA draw — "
        "apply_draw_to_hta_config may not be working"
    )


def test_apply_draw_does_not_mutate_original(hta_config, psa_config):
    """
    apply_draw_to_hta_config must not mutate the original hta_config.
    """
    from hta_sampler import HTASampler

    sampler = HTASampler(hta_config, psa_config)
    original_snapshot = copy.deepcopy(hta_config)

    draws = sampler.generate_draws(n=1, seed=77)
    draw_row = draws.iloc[0]

    _ = sampler.apply_draw_to_hta_config(hta_config, draw_row)

    assert hta_config == original_snapshot, (
        "apply_draw_to_hta_config mutated the original hta_config"
    )


def test_exclude_placeholders_filter(hta_config, psa_config):
    """
    With exclude_placeholders=True, entries whose distribution_status contains
    "placeholder" are filtered out. The production HTA config currently has no
    sampled placeholder parameters, so inject one into a copy to keep this
    behavior covered without reintroducing a real placeholder.
    """
    from hta_sampler import HTASampler

    hta_with_placeholder = copy.deepcopy(hta_config)
    hta_with_placeholder["disease_costs"]["art_first_line"][
        "distribution_status"
    ] = "placeholder"

    psa_include_all = copy.deepcopy(psa_config)
    psa_include_all["hta_sampling"]["exclude_placeholders"]["value"] = False
    sampler_all = HTASampler(hta_with_placeholder, psa_include_all)

    psa_lit_only = copy.deepcopy(psa_config)
    psa_lit_only["hta_sampling"]["exclude_placeholders"]["value"] = True
    sampler_lit = HTASampler(hta_with_placeholder, psa_lit_only)

    assert len(sampler_lit.param_paths) < len(sampler_all.param_paths), (
        f"exclude_placeholders=True should reduce parameter count "
        f"(lit={len(sampler_lit.param_paths)}, all={len(sampler_all.param_paths)})"
    )
    assert "disease_costs.art_first_line" not in sampler_lit.param_paths
    # All remaining paths should be non-placeholder.
    for _, _, _, entry in sampler_lit._param_specs:
        status = entry.get("distribution_status", "")
        assert "placeholder" not in status, (
            f"Placeholder parameter leaked through filter: status={status}"
        )


def test_production_hta_config_has_no_sampled_placeholders(hta_config, psa_config):
    """Current HTA PSA set should not contain sampled placeholder parameters."""
    from hta_sampler import HTASampler

    psa_filtering = copy.deepcopy(psa_config)
    psa_filtering["hta_sampling"]["exclude_placeholders"]["value"] = True
    sampler = HTASampler(hta_config, psa_filtering)

    for path, _, _, entry in sampler._param_specs:
        status = entry.get("distribution_status", "")
        assert "placeholder" not in status, (
            f"Production sampled HTA parameter is still placeholder: {path} ({status})"
        )


def test_hta_draw_shape(sampler):
    """
    draws = sampler.generate_draws(n=50, seed=42)
    Shape must be (50, K_hta). No NaN.
    """
    draws = sampler.generate_draws(n=50, seed=42)
    K = len(sampler.param_paths)
    assert draws.shape == (50, K), (
        f"Expected shape (50, {K}), got {draws.shape}"
    )
    assert not draws.isna().any().any(), "NaN values found in HTA draws"
