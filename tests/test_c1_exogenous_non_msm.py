"""
C-1 — exogenous non-MSM diagnosis forcing.

These tests pin the properties that make C-1 defensible rather than merely
functional:

  * the injected series IS the observed series (no free parameter anywhere);
  * the pre-1996 lump is NOT distributed under the default rule;
  * each C-3 end rule is explicit, runnable and distinguishable from the others;
  * the orientation split is integer-preserving and never leaks into MSM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exogenous_diagnoses import (
    ExogenousDiagnosisConfigError,
    ExogenousNonMsmForcing,
)

# Observed SHM Table 1.1 values, read from the workbook 2026-09-14.
OBSERVED = {
    1996: 259, 1997: 326, 1998: 281, 1999: 309, 2000: 406, 2001: 469,
    2002: 515, 2003: 535, 2004: 532, 2005: 521, 2006: 427, 2007: 421,
    2008: 398, 2009: 396, 2010: 378, 2011: 350, 2012: 339, 2013: 288,
    2014: 284, 2015: 298, 2016: 250, 2017: 239, 2018: 214, 2019: 251,
    2020: 168, 2021: 171, 2022: 198, 2023: 212, 2024: 199,
}


def _cfg(**overrides):
    block = {
        "enabled": {"value": True},
        "annual_counts": {"value": {str(y): c for y, c in OBSERVED.items()}},
        "annual_male_share": {"value": {str(y): 0.55 for y in OBSERVED}},
        "pre_start_rule": {"value": "burn_in"},
        "pre_start_lump": {"value": 1280},
        "pre_start_span": {"value": {"start": 1982, "end": 1995}},
        "post_end_rule": {"value": "freeze"},
        "freeze_window": {"value": 5},
        "trend_window": {"value": {"start": 2004, "end": 2020}},
    }
    for key, value in overrides.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = {"value": value}
    return {"disease": {"exogenous_non_msm": block}}


# ── the series is the data ───────────────────────────────────────────────────


@pytest.mark.parametrize("year,expected", sorted(OBSERVED.items()))
def test_every_observed_year_is_reproduced_exactly(year, expected):
    """By construction: the model emits the published count, not a fitted one."""
    assert ExogenousNonMsmForcing(_cfg()).count_for_year(year) == expected


def test_the_mechanism_is_off_unless_explicitly_enabled():
    assert ExogenousNonMsmForcing(None).count_for_year(2010) == 0
    assert ExogenousNonMsmForcing({}).count_for_year(2010) == 0
    assert ExogenousNonMsmForcing(_cfg(enabled=False)).count_for_year(2010) == 0


def test_config_block_is_found_under_the_config_reader_shape():
    """run_v3 sees the translated shape; the raw shape is what lives on disk."""
    raw = _cfg()
    translated = {"transmission_model": raw}
    assert ExogenousNonMsmForcing(translated).count_for_year(2003) == 535


# ── the pre-1996 lump ────────────────────────────────────────────────────────


def test_burn_in_rule_does_not_distribute_the_1995_lump():
    """
    The default must inject nothing before 1996. The lump has no published
    annual shape, so any distribution of it is invented.
    """
    forcing = ExogenousNonMsmForcing(_cfg())
    assert sum(forcing.count_for_year(y) for y in range(1975, 1996)) == 0


def test_uniform_rule_distributes_the_lump_exactly_and_only_in_its_span():
    forcing = ExogenousNonMsmForcing(_cfg(pre_start_rule="uniform"))
    injected = {y: forcing.count_for_year(y) for y in range(1975, 1996)}
    assert sum(injected.values()) == 1280, "the lump must be conserved, not approximated"
    assert all(v == 0 for y, v in injected.items() if y < 1982)
    assert all(v > 0 for y, v in injected.items() if y >= 1982)


def test_uniform_rule_refuses_to_run_without_a_lump_to_distribute():
    with pytest.raises(ExogenousDiagnosisConfigError, match="no lump"):
        ExogenousNonMsmForcing(_cfg(pre_start_rule="uniform", pre_start_lump=0))


# ── C-3 end rules ────────────────────────────────────────────────────────────


def test_freeze_holds_the_mean_of_the_last_five_observed_years():
    forcing = ExogenousNonMsmForcing(_cfg())
    expected = round(sum(OBSERVED[y] for y in range(2020, 2025)) / 5)
    assert forcing.count_for_year(2025) == expected == 190
    assert forcing.count_for_year(2070) == expected, "freeze must not drift"


def test_trend_rule_declines_because_the_fitted_window_declines():
    forcing = ExogenousNonMsmForcing(_cfg(post_end_rule="trend"))
    assert forcing.count_for_year(2030) < forcing.count_for_year(2025)


def test_the_three_end_rules_are_actually_distinguishable():
    """
    If the rules agreed, reporting a spread would be theatre. They must not.
    """
    freeze = ExogenousNonMsmForcing(_cfg()).count_for_year(2040)
    trend = ExogenousNonMsmForcing(_cfg(post_end_rule="trend")).count_for_year(2040)
    assert freeze != trend


def test_net_migration_rule_refuses_to_run_without_its_series():
    with pytest.raises(ExogenousDiagnosisConfigError, match="not runnable"):
        ExogenousNonMsmForcing(_cfg(post_end_rule="net_migration"))


def test_net_migration_rule_scales_the_frozen_level():
    cfg = _cfg(post_end_rule="net_migration")
    block = cfg["disease"]["exogenous_non_msm"]
    block["net_migration_series"] = {"value": {str(y): 100_000.0 for y in range(2020, 2025)}}
    block["net_migration_base_window"] = {"value": {"start": 2020, "end": 2024}}
    # A projected year at twice the base-window mean doubles the frozen level.
    block["net_migration_series"]["value"]["2030"] = 200_000.0
    forcing = ExogenousNonMsmForcing(cfg)
    assert forcing.count_for_year(2030) == 2 * forcing._frozen_level()


# ── allocation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("year", sorted(OBSERVED))
def test_split_is_integer_preserving(year):
    forcing = ExogenousNonMsmForcing(_cfg())
    split = forcing.split_for_year(year)
    assert sum(split.values()) == forcing.count_for_year(year)
    assert all(v >= 0 for v in split.values())


def test_split_never_allocates_to_msm():
    """C-1 exists so that the MSM epidemic stays endogenous."""
    split = ExogenousNonMsmForcing(_cfg()).split_for_year(2024)
    assert set(split) == {"Heterosexual_Male", "Female"}


def test_male_share_is_read_off_the_data_inside_the_observed_window():
    cfg = _cfg()
    cfg["disease"]["exogenous_non_msm"]["annual_male_share"]["value"]["2024"] = 0.5578
    forcing = ExogenousNonMsmForcing(cfg)
    assert forcing.male_share_for_year(2024) == pytest.approx(0.5578)
    split = forcing.split_for_year(2024)
    assert split["Heterosexual_Male"] == round(199 * 0.5578)


# ── configuration is validated, not assumed ──────────────────────────────────


def test_enabled_but_empty_series_is_rejected():
    with pytest.raises(ExogenousDiagnosisConfigError, match="nothing"):
        ExogenousNonMsmForcing(_cfg(annual_counts={}))


def test_unknown_rules_are_rejected_rather_than_silently_defaulted():
    with pytest.raises(ExogenousDiagnosisConfigError, match="pre_start_rule"):
        ExogenousNonMsmForcing(_cfg(pre_start_rule="whatever"))
    with pytest.raises(ExogenousDiagnosisConfigError, match="post_end_rule"):
        ExogenousNonMsmForcing(_cfg(post_end_rule="whatever"))


def test_negative_counts_and_out_of_range_shares_are_rejected():
    with pytest.raises(ExogenousDiagnosisConfigError, match="negative"):
        ExogenousNonMsmForcing(_cfg(annual_counts={"2000": -1}))
    with pytest.raises(ExogenousDiagnosisConfigError, match=r"\[0, 1\]"):
        ExogenousNonMsmForcing(_cfg(annual_male_share={"2000": 1.4}))


def test_inverted_windows_are_rejected():
    with pytest.raises(ExogenousDiagnosisConfigError, match="inverted"):
        ExogenousNonMsmForcing(_cfg(pre_start_span={"start": 1995, "end": 1982}))
    with pytest.raises(ExogenousDiagnosisConfigError, match="at least two years"):
        ExogenousNonMsmForcing(_cfg(trend_window={"start": 2020, "end": 2004}))


# ── the config on disk must match the workbook ───────────────────────────────


def test_shipped_config_series_matches_the_published_table():
    """
    Guards against the series being edited by hand. If this fails, someone has
    changed observed data -- which is a §1A.3 problem, not a test problem.
    """
    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"
    if not cfg_path.exists():
        pytest.skip(f"{cfg_path.name} not present")
    raw = json.loads(cfg_path.read_text())
    block = raw.get("transmission_model", raw).get("disease", {}).get("exogenous_non_msm")
    if block is None:
        pytest.skip("exogenous_non_msm not yet wired into this config")
    counts = {int(y): int(v) for y, v in block["annual_counts"]["value"].items()}
    assert counts == OBSERVED


# ── the translation layer must not silently drop the block ───────────────────


def test_config_reader_preserves_the_block():
    """
    ConfigReader.disease() copies an ALLOWLIST. A block that is present,
    well-formed and validated in the raw config is silently discarded if its key
    is missing from that list, and the engine then disables the mechanism with no
    error -- the A-25 / C-4 silent-default failure mode exactly. This test is the
    guard: it failed before `exogenous_non_msm` was added to the allowlist.
    """
    from config_reader import ConfigReader

    raw = {
        "transmission_model": {
            "disease": {
                "diagnosis": {"annual_rate": {"value": 0.1}},
                "exogenous_non_msm": {
                    "enabled": {"value": True},
                    "annual_counts": {"value": {"2000": 100}},
                },
            }
        }
    }
    translated = ConfigReader(raw).disease()
    assert "exogenous_non_msm" in translated, (
        "the block was dropped by the translation layer; the engine would see "
        "no config and silently disable C-1"
    )
    assert ExogenousNonMsmForcing({"disease": translated}).count_for_year(2000) == 100


def test_shipped_config_survives_translation_and_is_enabled():
    from config_reader import ConfigReader

    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"
    if not cfg_path.exists():
        pytest.skip(f"{cfg_path.name} not present")
    raw = json.loads(cfg_path.read_text())
    if "exogenous_non_msm" not in raw.get("transmission_model", {}).get("disease", {}):
        pytest.skip("exogenous_non_msm not wired into this config")
    forcing = ExogenousNonMsmForcing(ConfigReader(raw).as_v6_dict())
    assert forcing.enabled
    assert forcing.count_for_year(2024) == 199


# ── the allocation the placement relies on ───────────────────────────────────
#
# C-1 places people with stochastic_allocation.deterministic_allocate_total, the
# companion to the existing deterministic_allocate_events. These tests pin the
# properties the placement actually depends on.


def _allocate(total, weights):
    import numpy as np

    from stochastic_allocation import deterministic_allocate_total

    return deterministic_allocate_total(total, np.asarray(weights, dtype=float))


def test_allocation_conserves_the_total_exactly():
    for total in (0, 1, 7, 19, 20):
        assert _allocate(total, [10.0, 5.0, 3.0, 2.0]).sum() == total


def test_allocation_never_exceeds_cell_capacity_when_total_is_clamped():
    """
    The C-1 placement clamps take <= available and allocates proportionally to
    the susceptible counts themselves. Under that contract no cell can be
    over-drawn -- which is what makes the tensor subtraction safe without a
    second capacity pass.
    """
    import numpy as np

    rng = np.random.default_rng(20260914)
    for _ in range(300):
        capacity = rng.integers(0, 40, size=6).astype(float)
        available = int(capacity.sum())
        if available == 0:
            continue
        take = int(rng.integers(0, available + 1))
        out = _allocate(take, capacity)
        assert out.sum() == take
        assert (out <= capacity).all(), f"over-drawn: {out} from {capacity}"


def test_allocation_consumes_no_randomness_and_is_reproducible():
    """
    The C-1 placement must not draw. A draw there would sit mid-step and re-key
    every later draw in that process stream, which is the A-24 failure mode.
    """
    import numpy as np

    first = _allocate(7, [10.0, 5.0, 3.0, 2.0])
    for _ in range(5):
        assert np.array_equal(_allocate(7, [10.0, 5.0, 3.0, 2.0]), first)


def test_allocation_handles_an_empty_pool():
    # No structure to allocate against: place nobody rather than fabricating
    # stock in empty cells.
    assert _allocate(5, [0.0, 0.0, 0.0]).sum() == 0
    assert _allocate(0, [1.0, 1.0]).sum() == 0
