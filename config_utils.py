"""
Shared config utilities for V10 native config reading.

Consolidates extract_value() — the single canonical location.
All modules should import from here rather than defining their own copy.

V11.2 additions:
    extract_tier()         — read the tier field from a parameter entry
    get_params_by_tier()   — walk the config tree and collect (path, entry) tuples by tier
    validate_tier_schema() — assert schema compliance for all annotated parameters

V11.6.0 additions:
    validate_hta_config()  — validate the new v2 four-component HTA config format

Tier vocabulary
    "fixed"     — Tier 1: pinned from authoritative literature/data; not calibrated, not PSA-sampled
    "estimated" — Tier 2: calibrated to Dutch epidemiological data; requires bounds + calibration_target
    "uncertain" — Tier 3: genuinely unknown; sampled in future PSA runner; requires bounds + distribution
"""

from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

VALID_TIERS: frozenset = frozenset({"fixed", "estimated", "uncertain"})

# Valid distribution types for HTA config v2 (separate vocabulary from PSA tier system)
VALID_HTA_DIST_TYPES: frozenset = frozenset({"fixed", "beta", "gamma", "lognormal", "normal"})

KNOWN_DISTRIBUTION_TYPES: frozenset = frozenset(
    {"Beta", "Gamma", "LogNormal", "Uniform", "Normal"}
)

# Shape parameters required by each distribution type for the PSA sampler.
# Canonical reference — the JSON config carries a human-readable copy under
# the top-level "_psa_distribution_reference" key, but this dict is the
# authoritative definition used by _validate_entry() and psa_runner.py.
#
# Parameterisation notes:
#   Beta      — alpha (shape1) and beta (shape2); both must be > 0
#   LogNormal — mu and sigma are the mean/SD of the *underlying normal* (log-space);
#               sigma must be > 0; mu may be any real number
#   Gamma     — alpha = shape, beta = rate (NOT scale); scipy: gamma(a=alpha, scale=1/beta);
#               both must be > 0
#   Normal    — mu = mean (any real), sigma = SD; sigma must be > 0
#   Uniform   — no shape params; support is taken entirely from the parent entry's "bounds"
REQUIRED_SHAPE_PARAMS: dict = {
    "Beta":      {"alpha", "beta"},
    "LogNormal": {"mu", "sigma"},
    "Gamma":     {"alpha", "beta"},
    "Normal":    {"mu", "sigma"},
    "Uniform":   set(),
}

# Shape parameters that must be strictly positive (mu for Normal/LogNormal may be negative)
POSITIVE_SHAPE_PARAMS: frozenset = frozenset({"alpha", "beta", "sigma"})


# ──────────────────────────────────────────────────────────────────────────────
# Index-map accessor (ENG-F04: a name mismatch must raise, never resolve silently)
# ──────────────────────────────────────────────────────────────────────────────


class GroupNameError(KeyError):
    """A configured group name does not exist in the model's index map."""


def require_idx(index_map: dict, name: Any, what: str) -> int:
    """Resolve ``name`` to a tensor index, raising if the map does not contain it.

    ``mapping.get(name, <plausible default>)`` is the pattern that produced A-25: the
    config asked for the age bin ``"64-69"``, no such bin exists (it is ``"65-69"``), and
    ``age_to_idx.get(name, n_age_groups - 1)`` silently resolved it to the LAST bin. PrEP
    eligibility therefore spanned 15 to 110+ instead of 15-69, in every config in
    ``configs/``, undetected.

    This is the project's ENG-F04 rule made reusable: a ``KeyError`` on a name mismatch is
    correct; a silent wrong index is not. Use this wherever the name is *required* to
    exist. Where a state is genuinely *optional* -- Lenacapavir in an arm that does not
    model it -- the existing ``get(name, -1)`` sentinel guarded by ``if idx >= 0`` is the
    right idiom and must be left alone; the two questions are different.
    """
    try:
        return index_map[name]
    except (KeyError, TypeError):
        available = ", ".join(repr(k) for k in sorted(map(str, index_map)))
        raise GroupNameError(
            f"{what}: {name!r} is not a valid name. This is a configuration error, not a "
            f"missing default -- resolving it silently is how A-25 hid an eligibility "
            f"range of 15-110+ behind an intended 15-69. Available names: {available}"
        ) from None


# ──────────────────────────────────────────────────────────────────────────────
# Core accessor (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

def extract_value(config_item: Any, default=None) -> Any:
    """
    Extract value from config item.
    Handles both {"value": X, "notes": Y} wrapper and direct values.

    Examples:
        extract_value({"value": 42, "notes": "RNG seed"})  → 42
        extract_value(0.05)                                  → 0.05
        extract_value(None, default=1.0)                     → 1.0
    """
    if isinstance(config_item, dict) and "value" in config_item:
        return config_item["value"]
    return config_item if config_item is not None else default


# ──────────────────────────────────────────────────────────────────────────────
# V11.2 tier utilities
# ──────────────────────────────────────────────────────────────────────────────

def extract_tier(config_item: Any) -> Optional[str]:
    """
    Return the tier string from a parameter entry, or None if not annotated.

    Examples:
        extract_tier({"value": 0.04, "tier": "uncertain"})  → "uncertain"
        extract_tier({"value": 0.04})                         → None
        extract_tier(0.04)                                    → None
    """
    if isinstance(config_item, dict):
        return config_item.get("tier", None)
    return None


def get_params_by_tier(
    config_dict: Any,
    tier: str,
    _path: str = "",
    _results: Optional[List[Tuple[str, dict]]] = None,
) -> List[Tuple[str, dict]]:
    """
    Walk the config tree and return a list of (dot-path, entry) tuples for all
    parameter entries whose ``tier`` field matches *tier*.

    Rules:
    - A node is a *parameter entry* when it contains both ``"value"`` and ``"tier"``
      keys — recursion stops there (metadata sub-keys are not walked further).
    - Keys starting with ``"_"`` (deprecated/internal blocks) are skipped entirely.
    - List elements are walked; their index is appended to the path.

    Args:
        config_dict: The root (or sub-tree) of the config dict to walk.
        tier:        One of ``"fixed"``, ``"estimated"``, or ``"uncertain"``.

    Returns:
        List of ``(path, entry)`` tuples sorted by path.
    """
    if _results is None:
        _results = []

    if not isinstance(config_dict, dict):
        # Walk list elements
        if isinstance(config_dict, list):
            for i, item in enumerate(config_dict):
                get_params_by_tier(item, tier, f"{_path}[{i}]", _results)
        return _results

    for key, val in config_dict.items():
        if key.startswith("_"):          # skip _deprecated_*, _readme, etc.
            continue
        path = f"{_path}.{key}" if _path else key

        if isinstance(val, dict):
            if val.get("tier") == tier and "value" in val:
                # Found a matching parameter entry — record it, don't recurse in
                _results.append((path, val))
            else:
                get_params_by_tier(val, tier, path, _results)
        elif isinstance(val, list):
            for i, item in enumerate(val):
                get_params_by_tier(item, tier, f"{path}[{i}]", _results)

    return _results


def validate_tier_schema(config_dict: dict) -> List[str]:
    """
    Validate tier metadata schema compliance for all annotated parameters.

    Schema rules enforced:
    ┌──────────────┬─────────────────────────────────────────────────────────┐
    │ All tiers    │ tier value ∈ VALID_TIERS                                │
    │              │ "source" field present                                  │
    ├──────────────┼─────────────────────────────────────────────────────────┤
    │ "estimated"  │ "bounds" present (if value is numeric)                  │
    │              │ "calibration_target" present                            │
    ├──────────────┼─────────────────────────────────────────────────────────┤
    │ "uncertain"  │ "bounds" always present                                 │
    │              │ "distribution" present with "type" ∈ known set          │
    │              │ distribution.mean present and ≈ value (within 1%)       │
    ├──────────────┼─────────────────────────────────────────────────────────┤
    │ bounds       │ bounds[0] < bounds[1]                                   │
    │ (any tier)   │ bounds[0] ≤ value ≤ bounds[1] (numeric values only)     │
    └──────────────┴─────────────────────────────────────────────────────────┘

    Returns:
        List of human-readable violation strings. Empty list = fully compliant.
    """
    violations: List[str] = []
    _validate_recursive(config_dict, "", violations)
    return violations


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# V11.6.0 — HTA config v2 validator
# ──────────────────────────────────────────────────────────────────────────────

def validate_hta_config(config: dict) -> None:
    """
    Validate an hta_config dict in the new v2 four-component format.

    Raises ValueError with a descriptive message if any structural rule is violated.
    Returns silently if the config is valid.

    Rules enforced:
      - All four component sections present: utilities, adverse_events,
        prevention_costs, disease_costs
      - Each utility entry has a 'value' field
      - Each AE item has required fields: id, arm, billing_moment, frequency,
        prevalence, utility_decrement, cost
      - billing_moment is one of: "ongoing", "initiation"
        ("diagnosis" costs are routed via economics.py disease_costs; "once" is not implemented)
      - Each numeric distribution block has a valid 'type' field
      - Beta distributions have alpha > 0 and beta > 0
      - Gamma distributions have mean > 0 and se > 0
      - disease_costs entries have 'value', 'frequency', 'billing_moment'

    Args:
        config: Raw hta_config dict (must be in v2 format).

    Raises:
        ValueError: If any validation rule is violated. Message lists all violations.
    """
    violations: List[str] = []

    # ── Required top-level sections ───────────────────────────────────────────
    required_sections = ("utilities", "adverse_events", "prevention_costs", "disease_costs")
    for section in required_sections:
        if section not in config:
            violations.append(f"Missing required top-level section: '{section}'")

    if violations:
        # Cannot proceed with section-level checks if sections are missing
        raise ValueError(
            f"hta_config v2 validation failed with {len(violations)} error(s):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    # ── utilities ─────────────────────────────────────────────────────────────
    utilities = config["utilities"]
    if not isinstance(utilities, dict):
        violations.append("'utilities' must be a dict")
    else:
        for key, entry in utilities.items():
            if key.startswith("_"):
                continue
            if not isinstance(entry, dict):
                violations.append(f"utilities.{key}: must be a dict with a 'value' field")
                continue
            if "value" not in entry:
                violations.append(f"utilities.{key}: missing required 'value' field")
            if "distribution" in entry:
                _validate_distribution_block(
                    entry["distribution"], f"utilities.{key}.distribution", violations
                )

    # ── adverse_events ────────────────────────────────────────────────────────
    ae_section = config["adverse_events"]
    if not isinstance(ae_section, dict):
        violations.append("'adverse_events' must be a dict")
    else:
        if "mode" not in ae_section:
            violations.append("adverse_events: missing required 'mode' field")
        else:
            mode = ae_section["mode"]
            if mode not in ("prevalence", "state_machine"):
                violations.append(
                    f"adverse_events.mode: '{mode}' is not valid. "
                    "Must be 'prevalence' or 'state_machine'."
                )

        items = ae_section.get("items", [])
        if not isinstance(items, list):
            violations.append("adverse_events.items: must be a list")
        else:
            _validate_ae_items(items, violations)

    # ── prevention_costs ──────────────────────────────────────────────────────
    prev_costs = config["prevention_costs"]
    if not isinstance(prev_costs, dict):
        violations.append("'prevention_costs' must be a dict")
    else:
        shared = prev_costs.get("shared", [])
        if not isinstance(shared, list):
            violations.append("prevention_costs.shared: must be a list")
        else:
            for i, item in enumerate(shared):
                if "value" not in item:
                    violations.append(
                        f"prevention_costs.shared[{i}]: missing required 'value' field"
                    )
                if "frequency" not in item:
                    violations.append(
                        f"prevention_costs.shared[{i}]: missing required 'frequency' field"
                    )

        arm_specific = prev_costs.get("arm_specific", {})
        if not isinstance(arm_specific, dict):
            violations.append("prevention_costs.arm_specific: must be a dict")
        else:
            for arm_name, arm_costs in arm_specific.items():
                for cost_name, cost_entry in arm_costs.items():
                    path = f"prevention_costs.arm_specific.{arm_name}.{cost_name}"
                    if not isinstance(cost_entry, dict):
                        violations.append(f"{path}: must be a dict")
                        continue
                    if "value" not in cost_entry:
                        violations.append(f"{path}: missing required 'value' field")
                    if "frequency" not in cost_entry:
                        violations.append(f"{path}: missing required 'frequency' field")
                    if "distribution" in cost_entry:
                        _validate_distribution_block(
                            cost_entry["distribution"], f"{path}.distribution", violations
                        )

    # ── disease_costs ─────────────────────────────────────────────────────────
    disease = config["disease_costs"]
    if not isinstance(disease, dict):
        violations.append("'disease_costs' must be a dict")
    else:
        # Disease costs are routed by economics.py. Event-like ART initiation
        # costs use billing_moment="ongoing" plus cohort="new_art_initiators",
        # not billing_moment="initiation" or "once".
        valid_billing = {"ongoing", "diagnosis", "death"}
        valid_frequency = {"annual", "once"}
        valid_cohort = {"all", "new_art_initiators", "on_art_continuing"}
        for key, entry in disease.items():
            if key.startswith("_"):
                continue
            path = f"disease_costs.{key}"
            if not isinstance(entry, dict):
                violations.append(f"{path}: must be a dict")
                continue
            if "value" not in entry:
                violations.append(f"{path}: missing required 'value' field")
            if "frequency" not in entry:
                violations.append(f"{path}: missing required 'frequency' field")
            elif entry["frequency"] not in valid_frequency:
                violations.append(
                    f"{path}.frequency: '{entry['frequency']}' is not valid. "
                    f"Must be one of: {sorted(valid_frequency)}"
                )
            if "billing_moment" not in entry:
                violations.append(f"{path}: missing required 'billing_moment' field")
            elif entry["billing_moment"] not in valid_billing:
                violations.append(
                    f"{path}.billing_moment: '{entry['billing_moment']}' is not valid. "
                    f"Must be one of: {sorted(valid_billing)}"
                )
            cohort = entry.get("cohort", "all")
            if cohort not in valid_cohort:
                violations.append(
                    f"{path}.cohort: '{cohort}' is not valid. "
                    f"Must be one of: {sorted(valid_cohort)}"
                )
            elif cohort in {"new_art_initiators", "on_art_continuing"}:
                if entry.get("billing_moment") != "ongoing":
                    violations.append(
                        f"{path}.cohort: '{cohort}' requires billing_moment='ongoing'"
                    )
                stages = entry.get("applies_to_stages", [])
                if not stages:
                    violations.append(
                        f"{path}.cohort: '{cohort}' requires applies_to_stages"
                    )
                art_status = entry.get("applies_to_art_status", [])
                if "on_art" not in art_status:
                    violations.append(
                        f"{path}.cohort: '{cohort}' requires "
                        "applies_to_art_status to include 'on_art'"
                    )
            if "distribution" in entry:
                _validate_distribution_block(
                    entry["distribution"], f"{path}.distribution", violations
                )

    # ── utility_decrements (optional) ────────────────────────────────────────
    # Event-like QALY losses routed by economics.py. Current supported use:
    # one-time utility decrement for AIDS-stage ART initiation.
    if "utility_decrements" in config:
        decrements = config["utility_decrements"]
        valid_billing = {"art_initiation"}
        valid_frequency = {"once"}
        if not isinstance(decrements, dict):
            violations.append("'utility_decrements' must be a dict when present")
        else:
            for key, entry in decrements.items():
                if key.startswith("_"):
                    continue
                path = f"utility_decrements.{key}"
                if not isinstance(entry, dict):
                    violations.append(f"{path}: must be a dict")
                    continue
                if "value" not in entry:
                    violations.append(f"{path}: missing required 'value' field")
                if "frequency" not in entry:
                    violations.append(f"{path}: missing required 'frequency' field")
                elif entry["frequency"] not in valid_frequency:
                    violations.append(
                        f"{path}.frequency: '{entry['frequency']}' is not valid. "
                        f"Must be one of: {sorted(valid_frequency)}"
                    )
                if "billing_moment" not in entry:
                    violations.append(f"{path}: missing required 'billing_moment' field")
                elif entry["billing_moment"] not in valid_billing:
                    violations.append(
                        f"{path}.billing_moment: '{entry['billing_moment']}' is not valid. "
                        f"Must be one of: {sorted(valid_billing)}"
                    )
                if not entry.get("applies_to_stages", []):
                    violations.append(f"{path}: missing required 'applies_to_stages' field")
                if "distribution" in entry:
                    _validate_distribution_block(
                        entry["distribution"], f"{path}.distribution", violations
                    )

    # ── perspective (optional — only validated when present; BACKLOG-20) ─────────
    # The block is optional so that configs without it remain valid (backward compat).
    # When present, the primary value and productivity_loss sub-block are checked.
    if "perspective" in config:
        persp = config["perspective"]
        if not isinstance(persp, dict):
            violations.append("'perspective' must be a dict when present")
        else:
            primary = persp.get("primary", "payer")
            if primary not in ("societal", "payer"):
                violations.append(
                    f"perspective.primary must be 'societal' or 'payer', got {primary!r}"
                )
            prod = persp.get("productivity_loss", {})
            if isinstance(prod, dict) and prod.get("enabled", True):
                for req_key in (
                    "friction_period_weeks",
                    "average_hourly_wage",
                    "annual_hours_worked",
                    "loss_rate_by_stage",
                ):
                    if req_key not in prod:
                        violations.append(
                            f"perspective.productivity_loss.{req_key} is required "
                            f"when perspective.productivity_loss.enabled is true"
                        )
                loss_rates = prod.get("loss_rate_by_stage", {})
                if isinstance(loss_rates, dict):
                    required_stages = (
                        "acute", "chronic_off_art", "chronic_on_art",
                        "aids_off_art", "aids_on_art",
                    )
                    for stage in required_stages:
                        if stage not in loss_rates and not stage.startswith("_"):
                            violations.append(
                                f"perspective.productivity_loss.loss_rate_by_stage"
                                f".{stage} is missing"
                            )
                        elif stage in loss_rates:
                            entry = loss_rates[stage]
                            if isinstance(entry, dict) and "value" not in entry:
                                violations.append(
                                    f"perspective.productivity_loss.loss_rate_by_stage"
                                    f".{stage}: missing required 'value' field"
                                )
            pt = persp.get("patient_time_costs", {})
            if isinstance(pt, dict) and pt.get("enabled", True):
                for req_key in (
                    "oral_prep_visits_per_year",
                    "lenacapavir_visits_per_year",
                    "time_per_visit_hours",
                ):
                    if req_key not in pt:
                        violations.append(
                            f"perspective.patient_time_costs.{req_key} is required "
                            f"when perspective.patient_time_costs.enabled is true"
                        )

    # ── Raise if any violations found ─────────────────────────────────────────
    if violations:
        raise ValueError(
            f"hta_config v2 validation failed with {len(violations)} error(s):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


def _validate_ae_items(items: list, violations: List[str]) -> None:
    """Validate each AE item in adverse_events.items[]."""
    required_fields = ("id", "arm", "billing_moment", "frequency",
                       "prevalence", "utility_decrement", "cost")
    # Only "ongoing" and "initiation" are routed in adverse_events._compute_v2.
    # "diagnosis" costs belong in disease_costs (routed by economics.py).
    # "once" has no runtime implementation and is intentionally excluded.
    valid_billing = {"ongoing", "initiation"}

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            violations.append(f"adverse_events.items[{i}]: must be a dict")
            continue

        item_id = item.get("id", f"[index {i}]")
        path = f"adverse_events.items[{i}] (id={item_id!r})"

        for field in required_fields:
            if field not in item:
                violations.append(f"{path}: missing required field '{field}'")

        if "billing_moment" in item and item["billing_moment"] not in valid_billing:
            violations.append(
                f"{path}.billing_moment: '{item['billing_moment']}' is not valid. "
                f"Must be one of: {sorted(valid_billing)}. "
                f"Note: 'diagnosis' costs belong in disease_costs (economics.py); "
                f"'once' is not implemented for AE items."
            )

        # Validate sub-dicts that should have 'value'
        for sub in ("prevalence", "utility_decrement", "cost"):
            if sub in item:
                entry = item[sub]
                if isinstance(entry, dict):
                    if "value" not in entry:
                        violations.append(f"{path}.{sub}: missing required 'value' field")
                    if "distribution" in entry:
                        _validate_distribution_block(
                            entry["distribution"], f"{path}.{sub}.distribution", violations
                        )


def _validate_distribution_block(dist: Any, path: str, violations: List[str]) -> None:
    """Validate a distribution block: type, and type-specific required fields."""
    if not isinstance(dist, dict):
        violations.append(f"{path}: must be a dict")
        return

    dist_type = dist.get("type")
    if not dist_type:
        violations.append(f"{path}: missing required 'type' field")
        return

    if dist_type not in VALID_HTA_DIST_TYPES:
        violations.append(
            f"{path}.type: '{dist_type}' is not valid. "
            f"Must be one of: {sorted(VALID_HTA_DIST_TYPES)}"
        )
        return

    if dist_type == "fixed":
        return  # No additional fields required

    if dist_type == "beta":
        alpha = dist.get("alpha")
        beta = dist.get("beta")
        if alpha is None:
            violations.append(f"{path}: Beta distribution missing required 'alpha' field")
        elif not (isinstance(alpha, (int, float)) and alpha > 0):
            violations.append(f"{path}: Beta distribution 'alpha' must be > 0, got {alpha!r}")
        if beta is None:
            violations.append(f"{path}: Beta distribution missing required 'beta' field")
        elif not (isinstance(beta, (int, float)) and beta > 0):
            violations.append(f"{path}: Beta distribution 'beta' must be > 0, got {beta!r}")

    if dist_type == "gamma":
        mean = dist.get("mean")
        se = dist.get("se")
        if mean is None:
            violations.append(f"{path}: Gamma distribution missing required 'mean' field")
        elif not (isinstance(mean, (int, float)) and mean > 0):
            violations.append(f"{path}: Gamma distribution 'mean' must be > 0, got {mean!r}")
        if se is None:
            violations.append(f"{path}: Gamma distribution missing required 'se' field")
        elif not (isinstance(se, (int, float)) and se > 0):
            violations.append(f"{path}: Gamma distribution 'se' must be > 0, got {se!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers (existing)
# ──────────────────────────────────────────────────────────────────────────────

def _validate_recursive(obj: Any, path: str, violations: List[str]) -> None:
    """Recursively walk *obj*, collecting tier-schema violations."""
    if isinstance(obj, dict):
        if "tier" in obj:
            _validate_entry(obj, path, violations)
            return  # don't recurse into parameter-entry sub-keys

        for key, val in obj.items():
            if key.startswith("_"):
                continue
            new_path = f"{path}.{key}" if path else key
            _validate_recursive(val, new_path, violations)

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_recursive(item, f"{path}[{i}]", violations)


def _validate_entry(entry: dict, path: str, violations: List[str]) -> None:
    """Validate a single parameter entry that carries a ``tier`` field."""
    tier = entry["tier"]
    val  = entry.get("value")

    # ── Tier value must be in vocabulary ──────────────────────────────────────
    if tier not in VALID_TIERS:
        violations.append(
            f"{path}: invalid tier '{tier}' — allowed values: {sorted(VALID_TIERS)}"
        )
        return  # remaining checks are tier-specific; skip if tier is unknown

    # ── All annotated parameters must cite a source ───────────────────────────
    if "source" not in entry:
        violations.append(f"{path}: tier='{tier}' but missing required 'source' field")

    # ── Tier 2 (estimated) ────────────────────────────────────────────────────
    if tier == "estimated":
        if isinstance(val, (int, float)) and "bounds" not in entry:
            violations.append(
                f"{path}: tier='estimated' with numeric value but missing 'bounds'"
            )
        if "calibration_target" not in entry:
            violations.append(
                f"{path}: tier='estimated' but missing 'calibration_target'"
            )

    # ── Tier 3 (uncertain) ────────────────────────────────────────────────────
    if tier == "uncertain":
        if "bounds" not in entry:
            violations.append(f"{path}: tier='uncertain' but missing 'bounds'")

        if "distribution" not in entry:
            violations.append(f"{path}: tier='uncertain' but missing 'distribution'")
        else:
            dist = entry["distribution"]
            if not isinstance(dist, dict):
                violations.append(f"{path}.distribution: must be a dict object")
            else:
                dist_type = dist.get("type")
                if not dist_type:
                    violations.append(f"{path}.distribution: missing 'type' field")
                elif dist_type not in KNOWN_DISTRIBUTION_TYPES:
                    violations.append(
                        f"{path}.distribution: unknown type '{dist_type}' "
                        f"— known types: {sorted(KNOWN_DISTRIBUTION_TYPES)}"
                    )

                if "mean" not in dist:
                    violations.append(f"{path}.distribution: missing 'mean' field")
                elif isinstance(val, (int, float)) and val != 0:
                    mean = dist["mean"]
                    rel_err = abs(mean - val) / abs(val)
                    if rel_err > 0.01:
                        violations.append(
                            f"{path}: distribution.mean={mean} vs value={val} "
                            f"(relative error {rel_err:.3%} exceeds 1% threshold)"
                        )

                # ── Shape parameter presence and range ────────────────────
                if dist_type in REQUIRED_SHAPE_PARAMS:
                    required = REQUIRED_SHAPE_PARAMS[dist_type]
                    for param in sorted(required):
                        if param not in dist:
                            violations.append(
                                f"{path}: missing shape parameter '{param}' "
                                f"for {dist_type} distribution"
                            )
                        else:
                            v = dist[param]
                            if param in POSITIVE_SHAPE_PARAMS:
                                if not (isinstance(v, (int, float)) and v > 0):
                                    violations.append(
                                        f"{path}: invalid value for shape parameter "
                                        f"'{param}' in {dist_type} distribution "
                                        f"— expected a positive number, got {v!r}"
                                    )

    # ── Bounds validation (any tier) ──────────────────────────────────────────
    if "bounds" in entry:
        bounds = entry["bounds"]
        if not (isinstance(bounds, list) and len(bounds) == 2):
            violations.append(f"{path}: 'bounds' must be a 2-element list [min, max]")
        else:
            lo, hi = bounds[0], bounds[1]
            if lo >= hi:
                violations.append(
                    f"{path}: bounds[0]={lo} must be strictly less than bounds[1]={hi}"
                )
            elif isinstance(val, (int, float)):
                if not (lo <= val <= hi):
                    violations.append(
                        f"{path}: value={val} is outside bounds [{lo}, {hi}]"
                    )
