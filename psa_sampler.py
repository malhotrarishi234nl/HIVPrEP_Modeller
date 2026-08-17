"""
psa_sampler.py — PSA parameter sampling for V11 HIV model.

Dynamically discovers Tier-3 ("uncertain") parameters from the config,
builds scipy distribution objects, and generates a parameter draw matrix
using Latin Hypercube Sampling (LHS) or simple random sampling.

All distribution parameterisations follow config_utils.REQUIRED_SHAPE_PARAMS.

V11.10.0 — 2026-04-27
"""

import copy
import math
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import qmc
from config_utils import (
    get_params_by_tier,
    validate_tier_schema,
    REQUIRED_SHAPE_PARAMS,
    KNOWN_DISTRIBUTION_TYPES,
)


class PSASampler:
    """
    Generates a parameter draw matrix for PSA.

    Parameters
    ----------
    raw_config : dict
        The fully loaded base config dict (not a file path).
    psa_config : dict
        The PSA settings dict (loaded from psa_config.json).
        Required keys used here:
          psa_config["sampling"]["tiers_to_sample"]["value"]   list of tier strings
          psa_config["sampling"]["exclude_parameters"]["value"] list of dot-paths
          psa_config["sampling"]["include_parameters"]["value"] list of dot-paths
    """

    def __init__(self, raw_config: dict, psa_config: dict):
        # 1. Pre-flight validation — raise if schema violations exist
        violations = validate_tier_schema(raw_config)
        if violations:
            raise ValueError(
                f"PSA pre-flight: config has {len(violations)} tier-schema violation(s):\n"
                + "\n".join(f"  - {v}" for v in violations)
            )

        self._raw_config = raw_config
        self._psa_config = psa_config

        # 2. Discover parameters from configured tiers
        tiers = psa_config["sampling"]["tiers_to_sample"]["value"]
        all_params = []
        for tier in tiers:
            all_params.extend(get_params_by_tier(raw_config, tier))

        # 3. Apply include/exclude filters
        filtered = self._apply_filters(all_params)

        # 4. Build distribution objects for each parameter
        self._param_specs = []  # list of (path, frozen_dist, (lo, hi), entry)
        for path, entry in filtered:
            dist, bounds = self._build_distribution(path, entry)
            self._param_specs.append((path, dist, bounds, entry))

        # 5. Store sampling method
        self._method = psa_config["sampling"]["method"]["value"].lower()

    @property
    def param_paths(self) -> list:
        """Ordered list of dot-paths for all active PSA parameters."""
        return [path for path, _, _, _ in self._param_specs]

    def generate_draws(self, n: int, seed: int) -> pd.DataFrame:
        """
        Generate N parameter draw vectors.

        Returns
        -------
        pd.DataFrame
            Shape (n, K) where K = number of active parameters.
            Columns = dot-paths. All values clipped to bounds.
            Method (LHS or random) determined by
            psa_config["sampling"]["method"]["value"].
        """
        K = len(self._param_specs)
        if K == 0:
            return pd.DataFrame()

        if self._method == "lhs":
            unit_samples = self._lhs_samples(n, K, seed)
        else:
            # Simple random (Monte Carlo fallback)
            rng = np.random.default_rng(seed)
            unit_samples = rng.uniform(0, 1, size=(n, K))

        # Transform through inverse CDF (ppf) and clip to bounds
        draws_matrix = np.empty((n, K), dtype=np.float64)
        for j, (path, dist, bounds, _) in enumerate(self._param_specs):
            lo, hi = bounds
            raw = dist.ppf(unit_samples[:, j])
            draws_matrix[:, j] = np.clip(raw, lo, hi)

        return pd.DataFrame(draws_matrix, columns=self.param_paths)

    def get_parameter_summary(self) -> pd.DataFrame:
        """
        Return a summary table of all active PSA parameters.

        Returns
        -------
        pd.DataFrame
            Columns: path, tier, value, bounds_lo, bounds_hi,
                     dist_type, dist_params, source
        """
        rows = []
        for path, dist, bounds, entry in self._param_specs:
            lo, hi = bounds
            dist_block = entry.get("distribution", {})
            dist_type = dist_block.get("type", "Uniform")

            # Collect shape parameters as a string representation
            shape_params = {}
            for param_name in REQUIRED_SHAPE_PARAMS.get(dist_type, set()):
                if param_name in dist_block:
                    shape_params[param_name] = dist_block[param_name]

            rows.append({
                "path": path,
                "tier": entry.get("tier", "unknown"),
                "value": entry.get("value"),
                "bounds_lo": lo,
                "bounds_hi": hi,
                "dist_type": dist_type,
                "dist_params": str(shape_params),
                "source": entry.get("source", ""),
            })
        return pd.DataFrame(rows)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _build_distribution(self, path: str, entry: dict):
        """
        Build a frozen scipy distribution object from a config entry.
        Returns the frozen dist and (lo, hi) bounds tuple.
        Raises ValueError with the dot-path if parameterisation is invalid.
        """
        bounds = entry.get("bounds")
        if bounds is None or len(bounds) != 2:
            raise ValueError(
                f"PSA: parameter '{path}' has missing or malformed bounds"
            )
        lo, hi = bounds[0], bounds[1]

        dist_block = entry.get("distribution")
        if dist_block is None:
            # Uniform fallback — no distribution block, use bounds directly
            frozen = stats.uniform(loc=lo, scale=hi - lo)
            return frozen, (lo, hi)

        dist_type = dist_block.get("type")
        if dist_type not in KNOWN_DISTRIBUTION_TYPES:
            raise ValueError(
                f"PSA: parameter '{path}' has unknown distribution type '{dist_type}'. "
                f"Known types: {sorted(KNOWN_DISTRIBUTION_TYPES)}"
            )

        if dist_type == "Beta":
            alpha = dist_block["alpha"]
            beta_param = dist_block["beta"]
            if alpha <= 0 or beta_param <= 0:
                raise ValueError(
                    f"PSA: parameter '{path}' Beta distribution requires alpha > 0 "
                    f"and beta > 0, got alpha={alpha}, beta={beta_param}"
                )
            frozen = stats.beta(a=alpha, b=beta_param)

        elif dist_type == "LogNormal":
            mu = dist_block["mu"]
            sigma = dist_block["sigma"]
            if sigma <= 0:
                raise ValueError(
                    f"PSA: parameter '{path}' LogNormal distribution requires sigma > 0, "
                    f"got sigma={sigma}"
                )
            frozen = stats.lognorm(s=sigma, scale=math.exp(mu))

        elif dist_type == "Gamma":
            alpha = dist_block["alpha"]
            beta_param = dist_block["beta"]
            if alpha <= 0 or beta_param <= 0:
                raise ValueError(
                    f"PSA: parameter '{path}' Gamma distribution requires alpha > 0 "
                    f"and beta > 0, got alpha={alpha}, beta={beta_param}"
                )
            frozen = stats.gamma(a=alpha, scale=1.0 / beta_param)

        elif dist_type == "Normal":
            mu = dist_block["mu"]
            sigma = dist_block["sigma"]
            if sigma <= 0:
                raise ValueError(
                    f"PSA: parameter '{path}' Normal distribution requires sigma > 0, "
                    f"got sigma={sigma}"
                )
            frozen = stats.norm(loc=mu, scale=sigma)

        elif dist_type == "Uniform":
            frozen = stats.uniform(loc=lo, scale=hi - lo)

        else:
            raise ValueError(f"PSA: unhandled distribution type '{dist_type}' at '{path}'")

        return frozen, (lo, hi)

    def _apply_filters(self, params: list) -> list:
        """Apply include/exclude filters from psa_config. Returns filtered list."""
        sampling = self._psa_config["sampling"]
        include = sampling["include_parameters"]["value"]
        exclude = sampling["exclude_parameters"]["value"]

        # If include is non-empty, keep only params whose path is in that list
        if include:
            params = [(p, e) for p, e in params if p in include]

        # Remove any param whose path is in exclude
        if exclude:
            params = [(p, e) for p, e in params if p not in exclude]

        return params

    def _lhs_samples(self, n: int, k: int, seed: int) -> np.ndarray:
        """
        Generate Latin Hypercube Sampling unit-hypercube samples.

        Returns shape (n, k) array with values in [0, 1).
        Falls back to stratified random sampling if scipy.stats.qmc
        is unavailable (scipy < 1.7).
        """
        try:
            sampler = qmc.LatinHypercube(d=k, seed=seed)
            return sampler.random(n=n)
        except (ImportError, AttributeError):
            # Fallback: stratified random sampling
            rng = np.random.default_rng(seed)
            strata_edges = np.linspace(0, 1, n + 1)
            unit_samples = np.column_stack([
                rng.uniform(strata_edges[:-1], strata_edges[1:], size=n)
                for _ in range(k)
            ])
            # Shuffle each column independently
            for j in range(k):
                rng.shuffle(unit_samples[:, j])
            return unit_samples
