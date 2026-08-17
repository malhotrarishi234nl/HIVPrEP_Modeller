"""
Unit tests for ConfigReader — verifies every accessor returns
output identical to the golden reference V6 dict (originally
produced by config_adapter.normalise_config()).

This is the correctness gate for V10 native config reading.
"""

import sys
import os
import json

# Ensure we can import from the v10 directory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from config_reader import ConfigReader, ConfigFormatError
from config_utils import extract_value
from schedule_utils import ScheduleManager


def _deep_equal(a, b, path=""):
    """
    Recursive deep comparison that gives helpful error messages.
    Returns list of differences (empty = identical).
    """
    diffs = []
    if type(a) != type(b):
        diffs.append(f"{path}: type mismatch {type(a).__name__} vs {type(b).__name__}")
        return diffs
    if isinstance(a, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            if k not in a:
                diffs.append(f"{path}.{k}: missing in ConfigReader output")
            elif k not in b:
                diffs.append(f"{path}.{k}: missing in golden reference")
            else:
                diffs.extend(_deep_equal(a[k], b[k], f"{path}.{k}"))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list length {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs.extend(_deep_equal(x, y, f"{path}[{i}]"))
    elif isinstance(a, float):
        if abs(a - b) > 1e-12:
            diffs.append(f"{path}: {a} != {b}")
    else:
        if a != b:
            diffs.append(f"{path}: {repr(a)} != {repr(b)}")
    return diffs


def _configs_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")


def load_v10_config():
    """Load the canonical V11 config (function name kept for backward compat)."""
    with open(os.path.join(_configs_dir(), "v11_configuration.json"), "r") as f:
        return json.load(f)


def load_golden_reference():
    """Load the golden V6 reference (produced by config_adapter before retirement)."""
    with open(os.path.join(_configs_dir(), "v6_golden_reference.json"), "r") as f:
        return json.load(f)


def test_full_v6_dict_matches_golden():
    """
    The master test: ConfigReader.as_v6_dict() must produce output
    identical to the golden V6 reference.
    """
    raw = load_v10_config()
    golden = load_golden_reference()
    reader = ConfigReader(raw)
    reader_output = reader.as_v6_dict()

    diffs = _deep_equal(reader_output, golden)
    if diffs:
        print(f"\nFAIL: {len(diffs)} differences found between ConfigReader and golden reference:")
        for d in diffs[:20]:
            print(f"  {d}")
        if len(diffs) > 20:
            print(f"  ... and {len(diffs) - 20} more")
    assert not diffs, (
        f"{len(diffs)} differences found between ConfigReader and golden reference: "
        f"{diffs[:20]}"
    )
    print("PASS: ConfigReader.as_v6_dict() matches golden reference exactly")


def test_section_by_section():
    """
    Test each section accessor individually against the golden reference.
    """
    raw = load_v10_config()
    golden = load_golden_reference()
    reader = ConfigReader(raw)

    sections = {
        "meta":                   reader.meta(),
        "time":                   reader.time(),
        "simulation_controls":    reader.simulation_controls(),
        "output":                 reader.output(),
        "population":             reader.population(),
        "demography":             reader.demography(),
        "childhood":              reader.childhood(),
        "sexual_debut":           reader.sexual_debut(),
        "life_table":             reader.life_table(),
        "fertility":              reader.fertility(),
        "population_adjustments": reader.population_adjustments(),
        "migration":              reader.migration(),
        "groups":                 reader.groups(),
        "mixing":                 reader.mixing(),
        "disease":                reader.disease(),
        "interventions":          reader.interventions(),
        "notes":                  reader.notes(),
    }

    # art_schedule may be None if no ART block
    art = reader.art_schedule()
    if art is not None:
        sections["art_schedule"] = art

    passed = 0
    failed = 0
    for section_name, reader_val in sections.items():
        golden_val = golden.get(section_name)
        if golden_val is None and reader_val is None:
            passed += 1
            continue
        if golden_val is None or reader_val is None:
            print(f"  FAIL [{section_name}]: one is None, other is not")
            failed += 1
            continue

        diffs = _deep_equal(reader_val, golden_val, section_name)
        if diffs:
            print(f"  FAIL [{section_name}]: {len(diffs)} differences")
            for d in diffs[:5]:
                print(f"    {d}")
            failed += 1
        else:
            print(f"  PASS [{section_name}]")
            passed += 1

    print(f"\nSection-by-section: {passed} passed, {failed} failed")
    assert failed == 0, f"{failed} ConfigReader sections differed from the golden reference"


def test_version_detection():
    """Test V10 format detection and version extraction."""
    raw = load_v10_config()
    reader = ConfigReader(raw)

    assert ConfigReader.is_v10(raw), "Should detect V11/V10 format"
    version = reader.get_config_version()
    assert version == "13.0", f"Expected '13.0', got '{version}'"

    # V6-like config should not pass is_v10 (does not have the required keys)
    v6_like = {"time": {}, "groups": {}, "disease": {}}
    assert not ConfigReader.is_v10(v6_like), "V6-like config should not be detected as V10"

    print("PASS: Version detection")


def test_extract_value_shared():
    """Test that config_utils.extract_value works correctly."""
    assert extract_value({"value": 42, "notes": "test"}) == 42
    assert extract_value({"value": True}) is True
    assert extract_value({"value": [1, 2, 3]}) == [1, 2, 3]
    assert extract_value(0.05) == 0.05
    assert extract_value("hello") == "hello"
    assert extract_value(None, default=1.0) == 1.0
    assert extract_value(None) is None
    print("PASS: extract_value shared utility")


def test_caching():
    """Test that repeated calls return cached (identical) objects."""
    raw = load_v10_config()
    reader = ConfigReader(raw)

    t1 = reader.time()
    t2 = reader.time()
    assert t1 is t2, "time() should return cached object"

    g1 = reader.groups()
    g2 = reader.groups()
    assert g1 is g2, "groups() should return cached object"

    print("PASS: Caching works")


def test_art_schedule_none_when_missing():
    """Test art_schedule() returns None when no ART block exists."""
    raw = {"run_control": {}, "transmission_model": {}, "interventions": {}}
    reader = ConfigReader(raw)
    assert reader.art_schedule() is None, "art_schedule() should be None with no ART block"
    print("PASS: art_schedule() None when missing")


def test_art_dropout_rate_bridged_to_discontinuation_schedule_at_art_intro_year():
    """advanced.art_dropout_rate becomes an ART discontinuation schedule at ART introduction."""
    raw = {
        "run_control": {},
        "transmission_model": {},
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {"value": {"1975": 0.0, "1996": 0.5}},
                    "interpolation": {"value": "stepwise"},
                },
                "advanced": {"art_dropout_rate": {"value": 0.05}},
            }
        },
    }
    art = ConfigReader(raw).art_schedule()
    assert art["discontinuation"]["schedule"] == {"1996": 0.05}

    manager = ScheduleManager({"art_schedule": art})
    assert manager.get_art_discontinuation_rate(1995.0) == 0.0
    assert manager.get_art_discontinuation_rate(1996.0) == 0.05
    assert manager.get_art_discontinuation_rate(2025.0) == 0.05


def test_linear_art_schedule_is_zero_before_first_positive_anchor():
    """Linear ART uptake must not leak into the pre-availability year."""
    manager = ScheduleManager(
        {
            "art_schedule": {
                "interpolation": {"value": "linear"},
                "initiation": {"schedule": {"1995": 0.0, "1996": 0.3, "2000": 0.8}},
            }
        }
    )

    assert manager.get_art_introduction_year() == 1996.0
    assert manager.get_art_initiation_rate(1995.0) == 0.0
    assert manager.get_art_initiation_rate(1995.75) == 0.0
    assert manager.get_art_initiation_rate(1996.0) == 0.3
    assert 0.3 < manager.get_art_initiation_rate(1998.0) < 0.8


def test_explicit_art_discontinuation_schedule_takes_precedence_over_flat_dropout():
    raw = {
        "run_control": {},
        "transmission_model": {},
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {"value": {"1996": 0.5}},
                    "discontinuation": {"value": {"2000": 0.02}},
                },
                "advanced": {"art_dropout_rate": {"value": 0.05}},
            }
        },
    }
    art = ConfigReader(raw).art_schedule()
    assert art["discontinuation"]["schedule"] == {"2000": 0.02}


def test_missing_art_dropout_leaves_discontinuation_absent():
    raw = {
        "run_control": {},
        "transmission_model": {},
        "interventions": {
            "art": {
                "schedule": {
                    "initiation": {"value": {"1996": 0.5}},
                },
            }
        },
    }
    art = ConfigReader(raw).art_schedule()
    assert "discontinuation" not in art


def test_intervention_key_renames():
    """Test that intervention keys are correctly renamed."""
    raw = load_v10_config()
    reader = ConfigReader(raw)
    interventions = reader.interventions()

    assert "Oral_PrEP" in interventions, "Should have Oral_PrEP key"
    assert "Lenacapavir" in interventions, "Should have Lenacapavir key"
    assert "oral_prep" not in interventions, "Should NOT have oral_prep key"
    assert "lenacapavir" not in interventions, "Should NOT have lenacapavir key"

    # Verify fields were promoted from advanced
    for name in ("Oral_PrEP", "Lenacapavir"):
        intv = interventions[name]
        assert "efficacy" in intv, f"{name} should have efficacy promoted from advanced"
        assert "enabled" in intv, f"{name} should have enabled"

    print("PASS: Intervention key renames")


def test_disease_treatment_reconstruction():
    """Test that disease.treatment is correctly reconstructed."""
    raw = load_v10_config()
    golden = load_golden_reference()
    reader = ConfigReader(raw)
    disease = reader.disease()

    assert "treatment" in disease, "disease should have treatment sub-dict"
    treatment = disease["treatment"]
    assert "art_transmission_reduction" in treatment
    assert "art_mortality_reduction" in treatment
    # Note: art_initiation_rate and art_dropout_rate were removed in V11.9.4 (Fix B2)
    # as dead keys — they were never consumed by the engine. The golden reference
    # predates that removal; filter them out before comparing.

    # Verify values match golden reference (excluding V11.9.4 dead keys)
    _dead_keys_v11_9_4 = {"art_initiation_rate", "art_dropout_rate"}
    golden_treatment = {
        k: v for k, v in golden["disease"]["treatment"].items()
        if k not in _dead_keys_v11_9_4
    }
    diffs = _deep_equal(treatment, golden_treatment, "disease.treatment")
    assert not diffs, f"Treatment mismatch: {diffs}"

    print("PASS: Disease treatment reconstruction")


def test_advanced_transparency():
    """Test that advanced fields are correctly promoted."""
    raw = load_v10_config()
    reader = ConfigReader(raw)

    # demography.advanced fields should appear at top level
    dem = reader.demography()
    assert "aging_method" in dem, "aging_method should be promoted from advanced"
    assert "background_mortality" in dem, "background_mortality should be promoted from advanced"

    # output.advanced fields should appear at top level
    out = reader.output()
    assert "write_state_counts" in out, "write_state_counts should be promoted from advanced"
    assert "summary_years" in out, "summary_years should be promoted from advanced"
    assert "quantiles" in out, "quantiles should be promoted from advanced"

    # mixing: V11 epsilon keys should be present; legacy matrix keys should NOT be
    mix = reader.mixing()
    assert "condom_usage" in mix, "condom_usage should be promoted from advanced"
    assert "epsilon_hetero" in mix, "epsilon_hetero should be present in V11 mixing"
    assert "epsilon_msm"    in mix, "epsilon_msm should be present in V11 mixing"
    assert "heterosexual"   not in mix, "Legacy heterosexual matrix should NOT be in V11 mixing"
    assert "msm"            not in mix, "Legacy msm matrix should NOT be in V11 mixing"

    # groups.advanced.orientation_contact_multipliers should be at top level
    grp = reader.groups()
    assert "orientation_contact_multipliers" in grp, "orientation_contact_multipliers should be promoted"

    print("PASS: Advanced block transparency")


def test_epsilon_mixing_keys():
    """Test that V11 epsilon parameterisation keys are correctly exposed."""
    raw = load_v10_config()
    reader = ConfigReader(raw)
    mix = reader.mixing()

    # Both epsilon keys must be present
    assert "epsilon_hetero" in mix, "epsilon_hetero must be in mixing()"
    assert "epsilon_msm"    in mix, "epsilon_msm must be in mixing()"

    # Values must be in valid range [0, 1]
    from config_utils import extract_value
    eps_h = extract_value(mix["epsilon_hetero"])
    eps_m = extract_value(mix["epsilon_msm"])
    assert 0.0 <= eps_h <= 1.0, f"epsilon_hetero={eps_h} out of [0,1] range"
    assert 0.0 <= eps_m <= 1.0, f"epsilon_msm={eps_m} out of [0,1] range"

    # Dutch-calibrated starting values (Netherlands model)
    assert abs(eps_h - 0.20) < 1e-9, f"epsilon_hetero expected 0.20, got {eps_h}"
    assert abs(eps_m - 0.42) < 1e-9, f"epsilon_msm expected 0.42, got {eps_m}"

    # Legacy matrix keys must NOT be present
    assert "heterosexual" not in mix, "V11 should not expose legacy heterosexual matrix"
    assert "msm"          not in mix, "V11 should not expose legacy msm matrix"

    # condom_usage still present
    assert "condom_usage" in mix, "condom_usage should still be present"

    print("PASS: V11 epsilon mixing keys")


def test_non_v10_config_raises_config_format_error():
    """Non-V10 configs (e.g. historic V6 flat format) must raise ConfigFormatError."""
    v6_like = {
        "time": {"unit": {"value": "months"}},
        "groups": {"orientations": {"value": []}},
        "disease": {},
        "population": {},
    }
    try:
        ConfigReader(v6_like)
        assert False, "Expected ConfigFormatError to be raised"
    except ConfigFormatError as e:
        assert "run_control" in str(e) or "transmission_model" in str(e), \
            f"Error message should mention required keys, got: {e}"

    # Also verify empty dict raises
    try:
        ConfigReader({})
        assert False, "Expected ConfigFormatError on empty dict"
    except ConfigFormatError:
        pass

    print("PASS: Non-V10 config raises ConfigFormatError")


if __name__ == "__main__":
    tests = [
        ("extract_value_shared", test_extract_value_shared),
        ("version_detection", test_version_detection),
        ("caching", test_caching),
        ("art_schedule_none_when_missing", test_art_schedule_none_when_missing),
        ("intervention_key_renames", test_intervention_key_renames),
        ("disease_treatment_reconstruction", test_disease_treatment_reconstruction),
        ("advanced_transparency", test_advanced_transparency),
        ("epsilon_mixing_keys", test_epsilon_mixing_keys),
        ("non_v10_raises_config_format_error", test_non_v10_config_raises_config_format_error),
        ("section_by_section", test_section_by_section),
        ("full_v6_dict_matches_golden", test_full_v6_dict_matches_golden),
    ]

    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True))
        except Exception as exc:
            print(f"FAIL: {name}: {exc}")
            results.append((name, False))

    print("\n" + "=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n{passed}/{total} tests passed")

    if passed < total:
        sys.exit(1)
