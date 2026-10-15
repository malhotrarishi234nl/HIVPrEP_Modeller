"""
hta_sampler.py — HTA parameter sampling for cost/utility/AE parameters.

Discovers distribution blocks in hta_config.json, builds scipy distributions,
and generates a parameter draw vector. Used alongside PSASampler for full
PSA × HTA integration.

Distribution parameterisations:
  - Gamma: specify mean and se in config → method of moments:
      alpha = (mean / se)^2,  scale = se^2 / mean
  - Beta: specify alpha and beta directly. If the configured value is negative
    (AE utility-decrement convention), draws keep the negative sign.
  - linked: copy one earlier active Beta/Gamma draw after validating that the
    source support fits the target's declared bounds (no silent clipping).
  - fixed: skipped (not sampled).

Array-indexed paths (adverse_events.items, prevention_costs.shared) use the
item's "id" field as the path segment rather than the numeric index, so paths
remain stable if items are reordered. Example path:
    adverse_events.items.oral_prep_gi_ae.cost.value

V11.11.2 — 2026-04-29

Known limitations (documented):
  - Correlation groups (BACKLOG-1): parameters sampled independently.
"""

import copy
import math
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import qmc


# Distribution types supported in hta_config (lowercase, as in the JSON)
_SUPPORTED_DIST_TYPES = {"gamma", "beta", "linked"}


class HTASampler:
    """
    Discovers and samples cost, utility, and AE parameters from hta_config.json.

    Parameters
    ----------
    hta_config : dict
        Loaded hta_config.json dict.
    psa_config : dict
        PSA settings dict. Uses:
          psa_config["hta_sampling"]["exclude_placeholders"]["value"]  (bool)
          psa_config["hta_sampling"]["exclude_parameters"]["value"]    (list)
          psa_config["sampling"]["method"]["value"]                    (str: lhs|random)
    """

    def __init__(self, hta_config: dict, psa_config: dict):
        self._hta_config = hta_config
        self._psa_config = psa_config

        hta_cfg = psa_config.get("hta_sampling", {})
        self._exclude_placeholders = _get_val(
            hta_cfg.get("exclude_placeholders", {}), default=False
        )
        self._exclude_paths = set(
            _get_val(hta_cfg.get("exclude_parameters", {}), default=[])
        )
        self._method = psa_config["sampling"]["method"]["value"].lower()

        # Discover all sampable parameters
        self._param_specs = []  # list of (dot_path, frozen_dist, (lo, hi), entry)
        self._discover(hta_config)

    # ── Public interface ─────────────────────────────────────────────────────

    @property
    def param_paths(self) -> list:
        """Ordered list of hta_config dot-paths for all active HTA PSA parameters."""
        return [path for path, _, _, _ in self._param_specs]

    def generate_draws(self, n: int, seed: int) -> pd.DataFrame:
        """
        Generate N parameter draw vectors for HTA parameters.

        Returns
        -------
        pd.DataFrame
            Shape (n, K_hta). Columns = hta_config dot-paths.
        """
        K = len(self._param_specs)
        if K == 0:
            return pd.DataFrame()

        sampled_positions = [
            j for j, (_path, _frozen, _bounds, entry) in enumerate(self._param_specs)
            if entry.get("distribution", {}).get("type") != "linked"
        ]
        sample_column = {position: column for column, position in enumerate(sampled_positions)}

        if self._method == "lhs":
            unit_samples = self._lhs_samples(n, len(sampled_positions), seed)
        else:
            rng = np.random.default_rng(seed)
            unit_samples = rng.uniform(0, 1, size=(n, len(sampled_positions)))

        draws_matrix = np.empty((n, K), dtype=np.float64)
        index_of = {path: index for index, (path, *_rest) in enumerate(self._param_specs)}
        for j, (path, frozen, bounds, entry) in enumerate(self._param_specs):
            lo, hi = bounds
            dist_block = entry.get("distribution", {})
            if dist_block.get("type") == "linked":
                source = str(dist_block.get("source_path", "")).strip()
                if source not in index_of or index_of[source] >= j:
                    raise ValueError(
                        f"linked HTA parameter '{path}' requires an earlier sampled "
                        f"source_path; got {source!r}"
                    )
                raw = draws_matrix[:, index_of[source]].copy()
                outside = (raw < lo) | (raw > hi) | ~np.isfinite(raw)
                if np.any(outside):
                    observed_lo = float(np.nanmin(raw))
                    observed_hi = float(np.nanmax(raw))
                    raise ValueError(
                        f"linked HTA parameter '{path}' received source draws outside "
                        f"its declared bounds [{lo}, {hi}] (observed "
                        f"[{observed_lo}, {observed_hi}]); refusing to clip a linked "
                        "uncertain quantity"
                    )
                draws_matrix[:, j] = raw
            else:
                raw = frozen.ppf(unit_samples[:, sample_column[j]])
                if _is_negative_beta(entry):
                    raw = -raw
                draws_matrix[:, j] = np.clip(raw, lo, hi)

        return pd.DataFrame(draws_matrix, columns=self.param_paths)

    def get_parameter_summary(self) -> pd.DataFrame:
        """Summary table: path, value, dist_type, dist_params, source, status."""
        rows = []
        for path, frozen, bounds, entry in self._param_specs:
            lo, hi = bounds
            dist_block = entry.get("distribution", {})
            rows.append({
                "path": path,
                "value": entry.get("value"),
                "bounds_lo": lo,
                "bounds_hi": hi,
                "dist_type": dist_block.get("type", "unknown"),
                "dist_params": str({k: v for k, v in dist_block.items() if k != "type"}),
                "source": entry.get("source", ""),
                "distribution_status": entry.get("distribution_status", "unknown"),
            })
        return pd.DataFrame(rows)

    def apply_draw_to_hta_config(self, hta_config: dict, draw_row) -> dict:
        """
        Apply one draw's values to a deep copy of hta_config.

        Parameters
        ----------
        hta_config : dict
            Original hta_config (not mutated).
        draw_row : dict or pd.Series
            Mapping of dot_path → sampled value.

        Returns
        -------
        dict
            Modified deep copy of hta_config.
        """
        cfg = copy.deepcopy(hta_config)
        for path, value in draw_row.items():
            self._set_hta_value(cfg, path, float(value))
        return cfg

    # ── Discovery ────────────────────────────────────────────────────────────

    def _discover(self, hta_config: dict):
        """Walk hta_config and register all sampable distribution blocks."""
        # Utilities
        for name, entry in hta_config.get("utilities", {}).items():
            if not isinstance(entry, dict):
                continue
            self._try_register(f"utilities.{name}", entry)

        # Utility decrements
        for name, entry in hta_config.get("utility_decrements", {}).items():
            if not isinstance(entry, dict):
                continue
            self._try_register(f"utility_decrements.{name}", entry)

        # Disease costs
        for name, entry in hta_config.get("disease_costs", {}).items():
            if not isinstance(entry, dict):
                continue
            self._try_register(f"disease_costs.{name}", entry)

        # Prevention costs — shared (array, use id field)
        for item in hta_config.get("prevention_costs", {}).get("shared", []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "")
            self._try_register(f"prevention_costs.shared.{item_id}", item)

        # Prevention costs — arm_specific
        arm_specific = hta_config.get("prevention_costs", {}).get("arm_specific", {})
        for arm_name, arm_items in arm_specific.items():
            if not isinstance(arm_items, dict):
                continue
            for item_name, item_entry in arm_items.items():
                if not isinstance(item_entry, dict):
                    continue
                self._try_register(
                    f"prevention_costs.arm_specific.{arm_name}.{item_name}",
                    item_entry,
                )

        # Adverse events — items (array, use id field)
        for item in hta_config.get("adverse_events", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "")
            for sub_key in ("prevalence", "cost", "utility_decrement"):
                if sub_key not in item:
                    continue
                sub_entry = item[sub_key]
                if not isinstance(sub_entry, dict):
                    continue
                self._try_register(
                    f"adverse_events.items.{item_id}.{sub_key}",
                    sub_entry,
                )

        # Perspective — productivity_loss.average_hourly_wage
        avg_wage = (
            hta_config.get("perspective", {})
            .get("productivity_loss", {})
            .get("average_hourly_wage", {})
        )
        if isinstance(avg_wage, dict):
            self._try_register(
                "perspective.productivity_loss.average_hourly_wage", avg_wage
            )

        # Perspective — productivity_loss.loss_rate_by_stage.*
        loss_rates = (
            hta_config.get("perspective", {})
            .get("productivity_loss", {})
            .get("loss_rate_by_stage", {})
        )
        for stage_name, stage_entry in loss_rates.items():
            if stage_name.startswith("_") or not isinstance(stage_entry, dict):
                continue
            self._try_register(
                f"perspective.productivity_loss.loss_rate_by_stage.{stage_name}",
                stage_entry,
            )

    def _try_register(self, path: str, entry: dict):
        """Register a parameter if it has a non-fixed distribution and passes filters."""
        dist_block = entry.get("distribution", {})
        dist_type = dist_block.get("type", "fixed")

        if dist_type == "fixed" or dist_type not in _SUPPORTED_DIST_TYPES:
            return

        # Placeholder filter
        if self._exclude_placeholders:
            status = entry.get("distribution_status", "")
            if "placeholder" in status:
                return

        # Explicit exclude list
        if path in self._exclude_paths:
            return

        if dist_type == "linked":
            # Link defects are configuration errors, never optional parameters.
            # They must abort sampler construction rather than turn into a
            # warning plus silent omission.
            frozen, bounds = self._build_link(path, entry)
            self._param_specs.append((path, frozen, bounds, entry))
            return

        try:
            frozen, bounds = self._build_distribution(path, entry)
        except Exception as e:
            import warnings
            warnings.warn(f"HTASampler: skipping '{path}' — {e}")
            return

        self._param_specs.append((path, frozen, bounds, entry))

    def _build_link(self, path: str, entry: dict):
        """Validate an identity link and return its target bounds."""
        dist_block = entry.get("distribution", {})
        raw_source = dist_block.get("source_path")
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise ValueError(
                f"linked HTA parameter '{path}' requires a non-empty source_path"
            )
        source = raw_source.strip()
        if source == path:
            raise ValueError(f"linked HTA parameter '{path}' cannot reference itself")

        known = {
            registered_path: (frozen, bounds, source_entry)
            for registered_path, frozen, bounds, source_entry in self._param_specs
        }
        if source not in known:
            reason = self._unavailable_link_source_reason(source)
            raise ValueError(
                f"linked HTA parameter '{path}' requires an earlier active stochastic "
                f"source_path; {source!r} is {reason}"
            )

        _source_frozen, source_bounds, source_entry = known[source]
        source_type = source_entry.get("distribution", {}).get("type")
        if source_type == "linked":
            raise ValueError(
                f"linked HTA parameter '{path}' cannot use nested link source {source!r}"
            )
        if source_type not in {"beta", "gamma"}:
            raise ValueError(
                f"linked HTA parameter '{path}' source {source!r} is not a directly "
                "sampled Beta/Gamma parameter"
            )

        target_bounds = _linked_bounds(path, entry)
        source_lo, source_hi = source_bounds
        target_lo, target_hi = target_bounds
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, (bool, np.bool_))
            and np.isfinite(value)
            for value in (source_lo, source_hi)
        ) or source_lo > source_hi:
            raise ValueError(
                f"linked HTA parameter '{path}' source {source!r} has invalid "
                f"sampled bounds [{source_lo}, {source_hi}]"
            )
        if source_lo < target_lo or source_hi > target_hi:
            raise ValueError(
                f"linked HTA parameter '{path}' source {source!r} sampled bounds "
                f"[{source_lo}, {source_hi}] exceed target bounds "
                f"[{target_lo}, {target_hi}]; align the bounds rather than silently "
                "clipping a shared uncertain quantity"
            )
        return None, target_bounds

    def _unavailable_link_source_reason(self, source: str) -> str:
        """Explain why a configured link source is not in the active registry."""
        source_entry = _find_hta_entry(self._hta_config, source)
        if source_entry is None:
            return "missing (or is a nested field rather than a parameter path)"
        source_type = source_entry.get("distribution", {}).get("type", "fixed")
        if source_type == "fixed":
            return "fixed, not stochastic"
        if source_type == "linked":
            return "another link (nested links are forbidden)"
        if source in self._exclude_paths:
            return "excluded by hta_sampling.exclude_parameters"
        status = source_entry.get("distribution_status", "")
        if self._exclude_placeholders and "placeholder" in status:
            return "excluded by hta_sampling.exclude_placeholders"
        if source_type not in _SUPPORTED_DIST_TYPES:
            return f"unsupported by HTASampler (distribution type {source_type!r})"
        return "forward or unavailable because its own distribution is invalid"

    # ── Distribution building ────────────────────────────────────────────────

    def _build_distribution(self, path: str, entry: dict):
        """
        Build a frozen scipy distribution from an hta_config entry.
        Returns (frozen_dist, (lo, hi)).
        """
        dist_block = entry.get("distribution", {})
        dist_type = dist_block.get("type")
        value = entry.get("value", 1.0)

        if dist_type == "gamma":
            mean = dist_block.get("mean")
            se = dist_block.get("se")
            if mean is None or se is None or se <= 0 or mean <= 0:
                raise ValueError(
                    f"Gamma at '{path}' requires mean>0 and se>0 "
                    f"(got mean={mean}, se={se})"
                )
            alpha = (mean / se) ** 2
            scale = se ** 2 / mean
            frozen = stats.gamma(a=alpha, scale=scale)
            # Practical clip range: [0, mean * 5]
            bounds_block = entry.get("bounds", {})
            lo = bounds_block.get("lower", 0.0)
            hi = bounds_block.get("upper", mean * 5.0)

        elif dist_type == "beta":
            alpha = dist_block.get("alpha")
            beta_param = dist_block.get("beta")
            if alpha is None or beta_param is None or alpha <= 0 or beta_param <= 0:
                raise ValueError(
                    f"Beta at '{path}' requires alpha>0 and beta>0 "
                    f"(got alpha={alpha}, beta={beta_param})"
                )
            frozen = stats.beta(a=alpha, b=beta_param)
            bounds_block = entry.get("bounds", {})
            lo, hi = _beta_bounds(bounds_block, negative=_is_negative_beta(entry))

        else:
            raise ValueError(f"Unsupported dist_type '{dist_type}' at '{path}'")

        return frozen, (lo, hi)

    # ── Applying draws to hta_config ─────────────────────────────────────────

    def _set_hta_value(self, cfg: dict, dot_path: str, value: float):
        """
        Navigate dot_path through cfg and set the terminal "value" key.

        Handles array-indexed segments: if the current node is a list, finds
        the item whose "id" matches the segment.
        """
        segments = dot_path.split(".")
        node = cfg

        for seg in segments[:-1]:
            if isinstance(node, list):
                # Find item by id
                found = False
                for item in node:
                    if isinstance(item, dict) and item.get("id") == seg:
                        node = item
                        found = True
                        break
                if not found:
                    raise KeyError(
                        f"HTASampler._set_hta_value: id='{seg}' not found in list "
                        f"(path='{dot_path}')"
                    )
            elif isinstance(node, dict):
                if seg not in node:
                    raise KeyError(
                        f"HTASampler._set_hta_value: key '{seg}' not found "
                        f"(path='{dot_path}')"
                    )
                node = node[seg]
            else:
                raise TypeError(
                    f"HTASampler._set_hta_value: unexpected node type {type(node)} "
                    f"at segment '{seg}' (path='{dot_path}')"
                )

        # Terminal segment
        leaf = segments[-1]
        if isinstance(node, list):
            # Terminal in a list — find item by id and set its value key
            for item in node:
                if isinstance(item, dict) and item.get("id") == leaf:
                    item["value"] = value
                    return
            raise KeyError(
                f"HTASampler._set_hta_value: id='{leaf}' not found in list "
                f"at terminal of path '{dot_path}'"
            )
        elif isinstance(node, dict) and leaf in node:
            if isinstance(node[leaf], dict) and "value" in node[leaf]:
                node[leaf]["value"] = value
            else:
                node[leaf] = value
        else:
            raise KeyError(
                f"HTASampler._set_hta_value: terminal key '{leaf}' not found "
                f"(path='{dot_path}')"
            )

    # ── LHS ─────────────────────────────────────────────────────────────────

    def _lhs_samples(self, n: int, k: int, seed: int) -> np.ndarray:
        """Latin Hypercube Sampling unit-hypercube samples, shape (n, k)."""
        try:
            sampler = qmc.LatinHypercube(d=k, seed=seed)
            return sampler.random(n=n)
        except (ImportError, AttributeError):
            rng = np.random.default_rng(seed)
            perms = np.empty((n, k), dtype=np.float64)
            for j in range(k):
                perm = rng.permutation(n)
                perms[:, j] = (perm + rng.uniform(size=n)) / n
            return perms


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_val(entry, default):
    """Extract .value from a config dict-entry, or return default."""
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return default


def _find_hta_entry(config: dict, dot_path: str):
    """Resolve a canonical sampler path, including list items addressed by id."""
    node = config
    for segment in dot_path.split("."):
        if isinstance(node, dict):
            if segment not in node:
                return None
            node = node[segment]
        elif isinstance(node, list):
            node = next(
                (
                    item for item in node
                    if isinstance(item, dict) and item.get("id") == segment
                ),
                None,
            )
            if node is None:
                return None
        else:
            return None
    return node if isinstance(node, dict) else None


def _linked_bounds(path: str, entry: dict) -> tuple:
    """Read a linked target's declared bounds without inventing clipping limits."""
    bounds = entry.get("bounds", {})
    if not isinstance(bounds, dict):
        raise ValueError(f"linked HTA parameter '{path}' bounds must be a dict")
    lower = bounds.get("lower", -np.inf)
    upper = bounds.get("upper", np.inf)
    if (
        not isinstance(lower, (int, float))
        or isinstance(lower, (bool, np.bool_))
        or not isinstance(upper, (int, float))
        or isinstance(upper, (bool, np.bool_))
        or np.isnan(lower)
        or np.isnan(upper)
    ):
        raise ValueError(f"linked HTA parameter '{path}' bounds must be numeric")
    lower, upper = float(lower), float(upper)
    if lower > upper:
        raise ValueError(
            f"linked HTA parameter '{path}' bounds are reversed: "
            f"{lower} > {upper}"
        )
    return lower, upper


def _is_negative_beta(entry: dict) -> bool:
    """Return True when a Beta-distributed parameter has a negative value."""
    dist_block = entry.get("distribution", {})
    return (
        dist_block.get("type") == "beta"
        and isinstance(entry.get("value"), (int, float))
        and entry["value"] < 0
    )


def _beta_bounds(bounds_block: dict, negative: bool = False) -> tuple:
    """
    Resolve Beta support bounds.

    Standard Beta parameters use [0, 1]. Negative-valued AE utility decrements
    represent utility losses, so the same positive Beta magnitude is mirrored
    onto [-1, 0]. If explicit non-negative bounds are supplied for a negative
    parameter, interpret them as magnitudes.
    """
    if not isinstance(bounds_block, dict):
        bounds_block = {}

    if not negative:
        return bounds_block.get("lower", 0.0), bounds_block.get("upper", 1.0)

    lower = bounds_block.get("lower")
    upper = bounds_block.get("upper")
    if lower is None and upper is None:
        return -1.0, 0.0

    lower = 0.0 if lower is None else lower
    upper = 1.0 if upper is None else upper

    if lower >= 0 and upper >= 0:
        return -max(lower, upper), -min(lower, upper)
    return min(lower, upper), max(lower, upper)
