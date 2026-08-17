"""
tests/test_psa_sampler.py — V11.10.0

13 tests for PSA parameter sampling:
  - Parameter discovery
  - Distribution moment validation (Beta, LogNormal)
  - LogNormal parameterisation consistency
  - Bounds enforcement
  - LHS stratification
  - LHS vs random coverage (discrepancy)
  - Include / exclude filters
  - Reproducibility
  - Schema violation detection
  - Parameter summary shape
  - Draws DataFrame shape
  - No NaN in draws
"""

import json
import copy
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.stats import qmc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json",
)
PSA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "psa_config.json",
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def raw_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture
def psa_config():
    with open(PSA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture
def sampler(raw_config, psa_config):
    from psa_sampler import PSASampler
    return PSASampler(raw_config, psa_config)


# ── Known parameter paths (from plan §0.2) ─────────────────────────────────

KNOWN_UNCERTAIN_PATHS = {
    "transmission_model.disease.transmission.beta_acute",
    "transmission_model.disease.transmission.beta_chronic",
    "transmission_model.disease.transmission.beta_aids",
    "transmission_model.disease.transmission.advanced.stage_multiplier_acute",
    "transmission_model.disease.transmission.advanced.stage_multiplier_aids",
    "transmission_model.disease.transmission.advanced.msm_versatility",
    "transmission_model.disease.progression.chronic_to_aids",
    "transmission_model.disease.diagnosis.annual_rate",
    "transmission_model.disease.advanced.art_mortality_reduction",
}


# ── Tests ───────────────────────────────────────────────────────────────────


def test_discover_uncertain_params(sampler):
    """sampler.param_paths has exactly 9 entries, all matching known dot-paths."""
    assert len(sampler.param_paths) == 9
    assert set(sampler.param_paths) == KNOWN_UNCERTAIN_PATHS


def test_beta_sampling_moments():
    """
    Draw N=10000 from Beta(5, 5).
    Theoretical mean = 0.5, var ≈ 0.0227.
    """
    dist = stats.beta(a=5, b=5)
    samples = dist.rvs(size=10000, random_state=42)
    theoretical_mean = 0.5
    theoretical_var = 5 * 5 / ((5 + 5) ** 2 * (5 + 5 + 1))  # ≈ 0.02273

    assert abs(samples.mean() - theoretical_mean) / theoretical_mean < 0.05
    assert abs(samples.var() - theoretical_var) / theoretical_var < 0.10


def test_lognormal_sampling_moments():
    """
    Draw N=10000 from LogNormal(mu=-2.7057, sigma=0.6).
    Theoretical mean = exp(-2.7057 + 0.6^2 / 2) ≈ 0.08.
    """
    mu, sigma = -2.7057, 0.6
    dist = stats.lognorm(s=sigma, scale=math.exp(mu))
    samples = dist.rvs(size=10000, random_state=42)
    theoretical_mean = math.exp(mu + sigma ** 2 / 2)

    assert abs(samples.mean() - theoretical_mean) / theoretical_mean < 0.05


def test_lognormal_parameterisation_consistency(sampler):
    """
    For every LogNormal param in sampler._param_specs:
        theoretical_mean = exp(mu + sigma^2 / 2)
        config_mean = entry["distribution"]["mean"]
        Assert relative error < 10%.
    This catches gross mu/sigma convention errors (e.g., confusing log-space
    vs natural-space parameterisation). Some params have modest fitting
    discrepancies (mode-matching vs mean-matching) which are acceptable.
    """
    ln_count = 0
    for path, dist, bounds, entry in sampler._param_specs:
        dist_block = entry.get("distribution", {})
        if dist_block.get("type") != "LogNormal":
            continue
        ln_count += 1
        mu = dist_block["mu"]
        sigma = dist_block["sigma"]
        theoretical_mean = math.exp(mu + sigma ** 2 / 2)
        config_mean = dist_block["mean"]
        rel_err = abs(theoretical_mean - config_mean) / config_mean
        assert rel_err < 0.10, (
            f"{path}: theoretical mean {theoretical_mean:.6f} vs config mean "
            f"{config_mean:.6f} (rel error {rel_err:.4%} exceeds 10% — "
            f"likely a parameterisation convention error)"
        )
    # There should be 5 LogNormal params among the 9 uncertain
    assert ln_count == 5, f"Expected 5 LogNormal params, found {ln_count}"


def test_bounds_enforcement(sampler):
    """All draws must be within [lo, hi] for every parameter. Zero violations."""
    draws = sampler.generate_draws(n=1000, seed=1)
    for path, dist, bounds, _ in sampler._param_specs:
        lo, hi = bounds
        col = draws[path]
        assert col.min() >= lo, f"{path}: min={col.min()} < lower bound {lo}"
        assert col.max() <= hi, f"{path}: max={col.max()} > upper bound {hi}"


def test_lhs_stratification(sampler):
    """
    With N=100 and LHS, verify stratification on the unit-hypercube level.
    After bounds clipping, some strata may merge — so we test the raw
    LHS unit samples (before ppf transformation) instead.
    Each column in the unit hypercube should have exactly one sample per
    centile stratum.
    """
    N = 100
    K = len(sampler.param_paths)

    # Generate unit-hypercube samples directly via the LHS engine
    unit_samples = sampler._lhs_samples(N, K, seed=7)

    for j in range(K):
        col = unit_samples[:, j]
        strata = np.floor(col * N).astype(int)
        strata = np.clip(strata, 0, N - 1)
        unique_strata = np.unique(strata)
        assert len(unique_strata) == N, (
            f"Column {j}: expected {N} unique strata in unit hypercube, "
            f"got {len(unique_strata)}. LHS stratification is broken."
        )


def test_lhs_vs_random_coverage(raw_config, psa_config):
    """
    LHS should have lower discrepancy (better coverage) than random sampling.
    Compare using scipy.stats.qmc.discrepancy().
    """
    from psa_sampler import PSASampler

    N = 100

    # LHS draws
    psa_lhs = copy.deepcopy(psa_config)
    psa_lhs["sampling"]["method"]["value"] = "lhs"
    sampler_lhs = PSASampler(raw_config, psa_lhs)
    draws_lhs = sampler_lhs.generate_draws(n=N, seed=42)

    # Random draws
    psa_random = copy.deepcopy(psa_config)
    psa_random["sampling"]["method"]["value"] = "random"
    sampler_random = PSASampler(raw_config, psa_random)
    draws_random = sampler_random.generate_draws(n=N, seed=42)

    # Normalise both to [0, 1] using bounds
    def normalise(draws_df, specs):
        normed = np.empty_like(draws_df.values)
        for j, (path, _, bounds, _) in enumerate(specs):
            lo, hi = bounds
            normed[:, j] = (draws_df[path].values - lo) / (hi - lo)
        return normed

    normed_lhs = normalise(draws_lhs, sampler_lhs._param_specs)
    normed_random = normalise(draws_random, sampler_random._param_specs)

    disc_lhs = qmc.discrepancy(normed_lhs)
    disc_random = qmc.discrepancy(normed_random)

    assert disc_lhs < disc_random, (
        f"LHS discrepancy ({disc_lhs:.6f}) should be less than "
        f"random discrepancy ({disc_random:.6f})"
    )


def test_exclude_filter(raw_config, psa_config):
    """Excluding beta_acute should leave 8 parameters."""
    from psa_sampler import PSASampler

    psa_copy = copy.deepcopy(psa_config)
    psa_copy["sampling"]["exclude_parameters"]["value"] = [
        "transmission_model.disease.transmission.beta_acute"
    ]
    s = PSASampler(raw_config, psa_copy)
    assert "transmission_model.disease.transmission.beta_acute" not in s.param_paths
    assert len(s.param_paths) == 8


def test_include_filter(raw_config, psa_config):
    """Including only 2 specific params should return exactly those 2."""
    from psa_sampler import PSASampler

    psa_copy = copy.deepcopy(psa_config)
    psa_copy["sampling"]["include_parameters"]["value"] = [
        "transmission_model.disease.transmission.beta_acute",
        "transmission_model.disease.transmission.beta_chronic",
    ]
    s = PSASampler(raw_config, psa_copy)
    assert len(s.param_paths) == 2
    assert set(s.param_paths) == {
        "transmission_model.disease.transmission.beta_acute",
        "transmission_model.disease.transmission.beta_chronic",
    }


def test_reproducibility(sampler):
    """Same seed produces identical draws."""
    d1 = sampler.generate_draws(n=50, seed=99)
    d2 = sampler.generate_draws(n=50, seed=99)
    pd.testing.assert_frame_equal(d1, d2)


def test_schema_violation_caught(raw_config, psa_config):
    """Removing a distribution block from a Tier-3 param should raise ValueError."""
    from psa_sampler import PSASampler

    cfg_bad = copy.deepcopy(raw_config)
    node = cfg_bad["transmission_model"]["disease"]["transmission"]
    del node["beta_acute"]["distribution"]
    with pytest.raises(ValueError, match="violation"):
        PSASampler(cfg_bad, psa_config)


def test_get_parameter_summary_shape(sampler):
    """Summary DataFrame has 9 rows and required columns."""
    summary = sampler.get_parameter_summary()
    assert isinstance(summary, pd.DataFrame)
    assert len(summary) == 9
    required_cols = {"path", "tier", "value", "bounds_lo", "bounds_hi", "dist_type"}
    assert required_cols.issubset(set(summary.columns))


def test_draws_dataframe_shape(sampler):
    """Draws have correct shape and column order matches param_paths."""
    draws = sampler.generate_draws(n=50, seed=1)
    assert draws.shape == (50, 9)
    assert list(draws.columns) == sampler.param_paths


def test_no_nan_in_draws(sampler):
    """No NaN values in any draw."""
    draws = sampler.generate_draws(n=200, seed=5)
    assert not draws.isna().any().any()


# ── V11.11.1: Distribution moment validation ────────────────────────────────


def test_gamma_sampling_moments():
    """
    Draw N=10000 from Gamma(alpha=5, beta_rate=2).
    Theoretical mean = alpha / beta_rate = 2.5, var = alpha / beta_rate^2 = 1.25.
    """
    frozen = stats.gamma(a=5, scale=0.5)  # scale = 1 / beta_rate
    samples = frozen.rvs(size=10000, random_state=42)
    theoretical_mean = 2.5
    theoretical_var = 1.25

    assert abs(samples.mean() - theoretical_mean) / theoretical_mean < 0.05
    assert abs(samples.var() - theoretical_var) / theoretical_var < 0.10


def test_normal_sampling_moments():
    """
    Draw N=10000 from Normal(mu=10, sigma=2).
    Theoretical mean=10, var=4.
    """
    frozen = stats.norm(loc=10, scale=2)
    samples = frozen.rvs(size=10000, random_state=42)

    assert abs(samples.mean() - 10) / 10 < 0.05
    assert abs(samples.var() - 4) / 4 < 0.10


def test_uniform_sampling_moments():
    """
    Draw N=10000 from Uniform(lo=3, hi=7).
    Theoretical mean=5, var=16/12≈1.333.
    """
    frozen = stats.uniform(loc=3, scale=4)  # scale = hi - lo
    samples = frozen.rvs(size=10000, random_state=42)
    theoretical_mean = 5.0
    theoretical_var = 16.0 / 12.0

    assert abs(samples.mean() - theoretical_mean) / theoretical_mean < 0.05
    assert abs(samples.var() - theoretical_var) / theoretical_var < 0.10
