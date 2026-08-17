"""
Tests for V11.2 parameter tier metadata.

Covers:
  1. Schema compliance  — validate_tier_schema() returns no violations
  2. Tier counts        — minimum number of parameters in each tier
  3. Known path lookup  — get_params_by_tier() finds expected parameters
  4. Distribution means — Tier 3 distribution.mean == entry.value (within 1%)
  5. Bounds validity    — all bounds[0] < bounds[1] and value within bounds
  6. No tier on structural params — run_control, output, enabled flags, etc.

References:
    Briggs AH et al. (2012) ISPOR-SMDM Modeling Good Research Practices
    Task Force. Med Decis Making 32(5):722-732.
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from config_utils import (
    extract_tier,
    get_params_by_tier,
    validate_tier_schema,
    VALID_TIERS,
    REQUIRED_SHAPE_PARAMS,
    POSITIVE_SHAPE_PARAMS,
)

TOL_MEAN_FRAC = 0.01   # distribution.mean must match value within 1%


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_v11_config():
    configs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")
    path = os.path.join(configs, "v11_configuration.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1: Schema compliance
# ---------------------------------------------------------------------------

def test_schema_compliance():
    """
    validate_tier_schema() must return an empty violation list for the
    canonical V11.2 config.

    This is the master gate: catches missing sources, missing bounds on
    Tier 2/3, malformed distributions, distribution.mean ≠ value, etc.
    """
    cfg = _load_v11_config()
    violations = validate_tier_schema(cfg)

    if violations:
        print(f"\nFAIL: {len(violations)} schema violation(s) found:")
        for v in violations:
            print(f"  {v}")
        return False

    print("PASS: validate_tier_schema() — no violations")
    return True


# ---------------------------------------------------------------------------
# Test 2: Tier counts are within expected ranges
# ---------------------------------------------------------------------------

def test_tier_counts():
    """
    Minimum parameter count per tier:
        fixed      >= 10  (progression rates, mortality, efficacy, art_tx_reduction,
                            art_dropout, total_size, fraction_male_newborns, …)
        estimated  >=  4  (epsilon_hetero, epsilon_msm, p_msm × 2)
        uncertain  >=  6  (beta_chronic, beta_acute, beta_aids,
                            chronic_to_aids, diagnosis.annual_rate,
                            art_mortality_reduction)
    """
    cfg = _load_v11_config()

    fixed     = get_params_by_tier(cfg, "fixed")
    estimated = get_params_by_tier(cfg, "estimated")
    uncertain = get_params_by_tier(cfg, "uncertain")

    MIN_FIXED, MIN_ESTIMATED, MIN_UNCERTAIN = 10, 4, 6

    ok = True
    if len(fixed) < MIN_FIXED:
        print(f"  FAIL: fixed count={len(fixed)} < {MIN_FIXED}")
        ok = False
    else:
        print(f"  PASS: fixed     count={len(fixed)} (>= {MIN_FIXED})")

    if len(estimated) < MIN_ESTIMATED:
        print(f"  FAIL: estimated count={len(estimated)} < {MIN_ESTIMATED}")
        ok = False
    else:
        print(f"  PASS: estimated count={len(estimated)} (>= {MIN_ESTIMATED})")

    if len(uncertain) < MIN_UNCERTAIN:
        print(f"  FAIL: uncertain count={len(uncertain)} < {MIN_UNCERTAIN}")
        ok = False
    else:
        print(f"  PASS: uncertain count={len(uncertain)} (>= {MIN_UNCERTAIN})")

    if ok:
        print("PASS: Tier counts meet minimums")
    return ok


# ---------------------------------------------------------------------------
# Test 3: get_params_by_tier returns expected known paths
# ---------------------------------------------------------------------------

def test_known_paths():
    """
    Assert specific parameters appear in the correct tier bucket.

    Tier 2 (estimated):
        epsilon_hetero, epsilon_msm, population.p_msm

    Tier 3 (uncertain):
        beta_chronic, beta_acute, beta_aids,
        chronic_to_aids, diagnosis.annual_rate, art_mortality_reduction

    Tier 1 (fixed):
        art_transmission_reduction, oral_prep efficacy, lenacapavir efficacy,
        acute_to_chronic, total_size
    """
    cfg = _load_v11_config()

    estimated_paths = {p for p, _ in get_params_by_tier(cfg, "estimated")}
    uncertain_paths = {p for p, _ in get_params_by_tier(cfg, "uncertain")}
    fixed_paths     = {p for p, _ in get_params_by_tier(cfg, "fixed")}

    failures = []

    # ── Tier 2 ────────────────────────────────────────────────────────────────
    expected_estimated = [
        "transmission_model.mixing.epsilon_hetero",
        "transmission_model.mixing.epsilon_msm",
        "population.p_msm",
    ]
    for p in expected_estimated:
        if not any(p in ep for ep in estimated_paths):
            failures.append(f"Expected '{p}' in tier 'estimated', not found")

    # ── Tier 3 ────────────────────────────────────────────────────────────────
    expected_uncertain = [
        "beta_chronic",
        "beta_acute",
        "beta_aids",
        "chronic_to_aids",
        "annual_rate",          # diagnosis.annual_rate
        "art_mortality_reduction",
    ]
    for p in expected_uncertain:
        if not any(p in up for up in uncertain_paths):
            failures.append(f"Expected '{p}' in tier 'uncertain', not found")

    # ── Tier 1 ────────────────────────────────────────────────────────────────
    expected_fixed = [
        "art_transmission_reduction",
        "oral_prep",            # contains efficacy
        "lenacapavir",          # contains efficacy
        "acute_to_chronic",
        "total_size",
    ]
    for p in expected_fixed:
        if not any(p in fp for fp in fixed_paths):
            failures.append(f"Expected '{p}' in tier 'fixed', not found")

    if failures:
        print(f"\nFAIL: {len(failures)} path assertion(s) failed:")
        for f in failures:
            print(f"  {f}")
        print(f"  estimated paths: {sorted(estimated_paths)}")
        print(f"  uncertain paths: {sorted(uncertain_paths)}")
        print(f"  fixed paths:     {sorted(fixed_paths)}")
        return False

    print("PASS: get_params_by_tier known-path assertions")
    return True


# ---------------------------------------------------------------------------
# Test 4: Tier 3 distribution.mean values match entry.value
# ---------------------------------------------------------------------------

def test_distribution_mean_matches_value():
    """
    For every Tier 3 (uncertain) entry that contains a distribution.mean,
    assert abs(mean - value) / abs(value) < TOL_MEAN_FRAC (1%).

    This guards against typos in the distribution params where the documented
    mean drifts from the actual model value — a violation that would make PSA
    results inconsistent with the base case.
    """
    cfg = _load_v11_config()
    uncertain_params = get_params_by_tier(cfg, "uncertain")

    failures = []
    checked = 0

    for path, entry in uncertain_params:
        val  = entry.get("value")
        dist = entry.get("distribution", {})
        mean = dist.get("mean")

        if mean is None or val is None:
            continue
        if not isinstance(val, (int, float)) or val == 0:
            continue

        rel_err = abs(mean - val) / abs(val)
        checked += 1

        if rel_err > TOL_MEAN_FRAC:
            failures.append(
                f"{path}: distribution.mean={mean} vs value={val} "
                f"(rel error {rel_err:.3%} > {TOL_MEAN_FRAC:.0%})"
            )

    if failures:
        print(f"\nFAIL: {len(failures)} distribution mean mismatch(es):")
        for f in failures:
            print(f"  {f}")
        return False

    print(f"PASS: distribution.mean matches value for all {checked} Tier 3 params")
    return True


# ---------------------------------------------------------------------------
# Test 5: Bounds validity — all annotated bounds are well-formed
# ---------------------------------------------------------------------------

def test_bounds_valid():
    """
    For ALL tier-annotated parameters (Tier 1, 2, 3) that carry a 'bounds' key:
      - bounds must be a 2-element list [lo, hi]
      - lo < hi
      - value must satisfy lo <= value <= hi (for numeric values only)
    """
    cfg = _load_v11_config()

    all_annotated = (
        get_params_by_tier(cfg, "fixed")
        + get_params_by_tier(cfg, "estimated")
        + get_params_by_tier(cfg, "uncertain")
    )

    failures = []
    checked  = 0

    for path, entry in all_annotated:
        bounds = entry.get("bounds")
        if bounds is None:
            continue

        val = entry.get("value")
        checked += 1

        if not (isinstance(bounds, list) and len(bounds) == 2):
            failures.append(f"{path}: bounds={bounds!r} must be a 2-element list")
            continue

        lo, hi = bounds
        if lo >= hi:
            failures.append(f"{path}: bounds[0]={lo} >= bounds[1]={hi}")
            continue

        if isinstance(val, (int, float)):
            if not (lo <= val <= hi):
                failures.append(
                    f"{path}: value={val} outside bounds [{lo}, {hi}]"
                )

    if failures:
        print(f"\nFAIL: {len(failures)} bounds violation(s):")
        for f in failures:
            print(f"  {f}")
        return False

    print(f"PASS: Bounds valid for all {checked} annotated parameters with bounds")
    return True


# ---------------------------------------------------------------------------
# Test 6: No tier annotation on structural / control parameters
# ---------------------------------------------------------------------------

def test_no_tier_on_structural_params():
    """
    Structural and control parameters must NOT carry a 'tier' field.

    Checked paths (by spot-inspection of config):
      - meta.version
      - run_control.start_year / end_year / step_size / seed / n_runs
      - output.enabled / output.format / output.directory
      - output.advanced.write_state_counts
      - transmission_model.groups.hiv_stages
      - transmission_model.groups.age_groups
      - interventions.art.enabled
      - interventions.oral_prep.enabled
      - interventions.lenacapavir.enabled
      - demography.enabled
    """
    cfg = _load_v11_config()

    structural_checks = [
        (["meta", "version"],                            "meta.version"),
        (["run_control", "start_year"],                  "run_control.start_year"),
        (["run_control", "end_year"],                    "run_control.end_year"),
        (["run_control", "step_size"],                   "run_control.step_size"),
        (["run_control", "seed"],                        "run_control.seed"),
        (["run_control", "n_runs"],                      "run_control.n_runs"),
        (["output", "enabled"],                          "output.enabled"),
        (["output", "format"],                           "output.format"),
        (["output", "directory"],                        "output.directory"),
        (["output", "advanced", "write_state_counts"],   "output.advanced.write_state_counts"),
        (["interventions", "art", "enabled"],            "interventions.art.enabled"),
        (["interventions", "oral_prep", "enabled"],      "interventions.oral_prep.enabled"),
        (["interventions", "lenacapavir", "enabled"],    "interventions.lenacapavir.enabled"),
        (["demography", "enabled"],                      "demography.enabled"),
    ]

    failures = []

    for keys, label in structural_checks:
        node = cfg
        try:
            for k in keys:
                node = node[k]
        except (KeyError, TypeError):
            # Path doesn't exist — that's fine, no tier to check
            continue

        if isinstance(node, dict) and "tier" in node:
            failures.append(f"Structural param '{label}' should not have 'tier' field")

    if failures:
        print(f"\nFAIL: {len(failures)} structural param(s) incorrectly annotated with 'tier':")
        for f in failures:
            print(f"  {f}")
        return False

    print(f"PASS: No tier annotation on structural/control parameters")
    return True


# ---------------------------------------------------------------------------
# Tests 7–14: Shape parameter validation (BACKLOG-19)
# ---------------------------------------------------------------------------

def _make_uncertain_entry(dist_type: str, extra_dist_fields: dict = None) -> dict:
    """Build a minimal valid uncertain entry with a given distribution type."""
    dist = {"type": dist_type, "mean": 0.04}
    if extra_dist_fields:
        dist.update(extra_dist_fields)
    return {
        "value": 0.04,
        "tier": "uncertain",
        "bounds": [0.01, 0.20],
        "source": "Synthetic test entry — BACKLOG-19",
        "distribution": dist,
    }


def _violations_for(entry: dict, label: str = "test_param") -> list:
    """Run validate_tier_schema on a synthetic single-entry config."""
    return validate_tier_schema({"test_param": entry})


def test_shape_param_presence_beta():
    """
    Beta entry missing 'alpha' must produce a violation naming the param and type.
    """
    entry = _make_uncertain_entry("Beta", {"beta": 48.0})   # alpha intentionally absent
    violations = _violations_for(entry)

    target = "missing shape parameter 'alpha' for Beta distribution"
    ok = any(target in v for v in violations)
    if ok:
        print("PASS: test_shape_param_presence_beta")
    else:
        print(f"FAIL: test_shape_param_presence_beta — expected violation containing:\n"
              f"  '{target}'\n  Got: {violations}")
    return ok


def test_shape_param_presence_lognormal():
    """
    LogNormal entry missing 'sigma' must produce a violation naming the param and type.
    """
    entry = _make_uncertain_entry("LogNormal", {"mu": -3.22})   # sigma intentionally absent
    violations = _violations_for(entry)

    target = "missing shape parameter 'sigma' for LogNormal distribution"
    ok = any(target in v for v in violations)
    if ok:
        print("PASS: test_shape_param_presence_lognormal")
    else:
        print(f"FAIL: test_shape_param_presence_lognormal — expected violation containing:\n"
              f"  '{target}'\n  Got: {violations}")
    return ok


def test_shape_param_presence_gamma():
    """
    Gamma entry missing both 'alpha' and 'beta' must produce exactly two violations,
    each naming the missing param and 'Gamma'.
    """
    entry = _make_uncertain_entry("Gamma")   # no alpha, no beta
    violations = _violations_for(entry)

    shape_violations = [v for v in violations if "Gamma distribution" in v and "missing shape parameter" in v]
    ok = len(shape_violations) == 2
    if ok:
        print("PASS: test_shape_param_presence_gamma")
    else:
        print(f"FAIL: test_shape_param_presence_gamma — expected 2 shape-param violations, "
              f"got {len(shape_violations)}: {shape_violations}")
    return ok


def test_shape_param_presence_normal():
    """
    Normal entry missing 'mu' must produce a violation naming the param and type.
    """
    entry = _make_uncertain_entry("Normal", {"sigma": 0.01})   # mu intentionally absent
    violations = _violations_for(entry)

    target = "missing shape parameter 'mu' for Normal distribution"
    ok = any(target in v for v in violations)
    if ok:
        print("PASS: test_shape_param_presence_normal")
    else:
        print(f"FAIL: test_shape_param_presence_normal — expected violation containing:\n"
              f"  '{target}'\n  Got: {violations}")
    return ok


def test_shape_param_uniform_no_required():
    """
    Uniform entry with no shape params beyond 'type' and 'mean' must produce
    zero shape-param violations (Uniform has no required shape params).
    """
    entry = _make_uncertain_entry("Uniform")
    violations = _violations_for(entry)

    shape_violations = [v for v in violations if "shape parameter" in v]
    ok = len(shape_violations) == 0
    if ok:
        print("PASS: test_shape_param_uniform_no_required")
    else:
        print(f"FAIL: test_shape_param_uniform_no_required — unexpected shape violations: {shape_violations}")
    return ok


def test_shape_param_invalid_range_alpha():
    """
    Beta entry with alpha=-1 must produce a violation naming 'alpha', 'Beta', and
    'expected a positive number'.
    """
    entry = _make_uncertain_entry("Beta", {"alpha": -1.0, "beta": 48.0})
    violations = _violations_for(entry)

    ok = any(
        "invalid value for shape parameter 'alpha' in Beta distribution" in v
        and "expected a positive number" in v
        for v in violations
    )
    if ok:
        print("PASS: test_shape_param_invalid_range_alpha")
    else:
        print(f"FAIL: test_shape_param_invalid_range_alpha — expected range violation for alpha.\n"
              f"  Got: {violations}")
    return ok


def test_shape_param_invalid_range_sigma():
    """
    LogNormal entry with sigma=0 must produce a violation naming 'sigma', 'LogNormal',
    and 'expected a positive number'.
    """
    entry = _make_uncertain_entry("LogNormal", {"mu": -3.22, "sigma": 0})
    violations = _violations_for(entry)

    ok = any(
        "invalid value for shape parameter 'sigma' in LogNormal distribution" in v
        and "expected a positive number" in v
        for v in violations
    )
    if ok:
        print("PASS: test_shape_param_invalid_range_sigma")
    else:
        print(f"FAIL: test_shape_param_invalid_range_sigma — expected range violation for sigma.\n"
              f"  Got: {violations}")
    return ok


def test_live_config_shape_params_valid():
    """
    All uncertain parameters in v11_configuration.json must pass the shape-param
    validation with zero violations.

    This is a regression guard: confirms all current uncertain params have the
    correct shape parameters after any future config edits.
    """
    cfg = _load_v11_config()
    violations = validate_tier_schema(cfg)

    # Filter to shape-param violations only (other failures are caught by test_schema_compliance)
    shape_violations = [v for v in violations if "shape parameter" in v]

    ok = len(shape_violations) == 0
    if ok:
        print("PASS: test_live_config_shape_params_valid — no shape-param violations in live config")
    else:
        print(f"FAIL: test_live_config_shape_params_valid — {len(shape_violations)} violation(s):")
        for v in shape_violations:
            print(f"  {v}")
    return ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = []
    results.append(("schema_compliance",              test_schema_compliance()))
    results.append(("tier_counts",                    test_tier_counts()))
    results.append(("known_paths",                    test_known_paths()))
    results.append(("distribution_mean_matches",      test_distribution_mean_matches_value()))
    results.append(("bounds_valid",                   test_bounds_valid()))
    results.append(("no_tier_on_structural_params",   test_no_tier_on_structural_params()))
    # BACKLOG-19: shape parameter validation
    results.append(("shape_param_presence_beta",      test_shape_param_presence_beta()))
    results.append(("shape_param_presence_lognormal", test_shape_param_presence_lognormal()))
    results.append(("shape_param_presence_gamma",     test_shape_param_presence_gamma()))
    results.append(("shape_param_presence_normal",    test_shape_param_presence_normal()))
    results.append(("shape_param_uniform_no_required",test_shape_param_uniform_no_required()))
    results.append(("shape_param_invalid_range_alpha",test_shape_param_invalid_range_alpha()))
    results.append(("shape_param_invalid_range_sigma",test_shape_param_invalid_range_sigma()))
    results.append(("live_config_shape_params_valid", test_live_config_shape_params_valid()))

    print("\n" + "=" * 60)
    passed = sum(1 for _, r in results if r)
    total  = len(results)
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n{passed}/{total} tests passed")

    if passed < total:
        sys.exit(1)
