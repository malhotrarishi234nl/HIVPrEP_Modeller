"""Tests for the history-matching target register (HM-1).

Two kinds of test here:
  * structural tests on synthetic fixtures, which run anywhere;
  * provenance tests against the real disease workbook, which assert specific
    workbook cells.  Those are the ones that would catch a silent change of
    source data, and they skip if the workbook is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import hm_target_register as tr
import hm_variance_budget as vb


WORKBOOK = (Path(__file__).resolve().parent.parent / "../data/raw/validation/DiseaseData.xlsx").resolve()
needs_workbook = pytest.mark.skipif(not WORKBOOK.exists(), reason="disease workbook not available")
HISTORICAL_FOCUSED_REGISTER = (
    Path(__file__).resolve().parent.parent
    / "configs/hm_target_register_focused_20260916.json"
)
ROBUST_NROY_PRODUCTION_REGISTER = (
    Path(__file__).resolve().parent.parent
    / "configs/hm_target_register_robust_nroy_production_20261015.json"
)


def test_historical_and_production_register_lineages_are_separate():
    historical = tr._strict_json_load(
        HISTORICAL_FOCUSED_REGISTER, label="historical focused register"
    )
    production = tr._strict_json_load(
        ROBUST_NROY_PRODUCTION_REGISTER, label="robust-NROY production register"
    )
    assert tr._sha256(HISTORICAL_FOCUSED_REGISTER) == (
        "87db68409e1a3e0c6ede762b37fd7f8c64008a1ba289b4965eb7a7be645e7a03"
    )
    assert tr._sha256(ROBUST_NROY_PRODUCTION_REGISTER) == (
        "57bed71174d2346419e250b2371995421c466f82c1b55c98104fcd2cfcce0af8"
    )
    assert "source_workbook_sha256" not in historical
    assert production["source_workbook_sha256"] == (
        "0ff530cea5cb2545b32c6c20ab40de324bfadf7846c2078e59eec6b79eb93404"
    )


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("1,280", 1280.0), ("517", 517.0), ("(NULL)", None), ("NULL", None), ("", None), ("..", None), ("abc", None)],
)
def test_to_float_handles_workbook_sentinels(raw, expected):
    assert tr._to_float(raw) == expected


@pytest.mark.parametrize("raw,expected", [("1996", 1996), ("2024.0", 2024), ("<=1995", None), ("Total", None), ("", None)])
def test_to_year_rejects_the_lump_and_total_rows(raw, expected):
    """'<=1995' is a cohort-reconstructed lump, not a year, and must never be scored."""
    assert tr._to_year(raw) == expected


def test_combine_add_and_subtract():
    row = pd.Series({"a": 10.0, "b": 3.0, "c": 2.0})
    assert tr._combine(row, "a", None, None) == 10.0
    assert tr._combine(row, "a", "b", None) == 13.0
    assert tr._combine(row, "a", None, "c") == 8.0
    assert tr._combine(row, "a", "b", "c") == 11.0


def test_combine_returns_none_if_any_component_is_missing():
    row = pd.Series({"a": 10.0, "b": None})
    assert tr._combine(row, "a", "b", None) is None
    assert tr._combine(row, "b", None, None) is None


def test_combine_raises_on_an_unknown_column():
    with pytest.raises(tr.RegisterError):
        tr._combine(pd.Series({"a": 1.0}), "nope", None, None)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('{"version": "first", "version": "second"}', "duplicate JSON object key"),
        ('{"value": NaN}', "non-finite JSON token"),
        ('{"value": Infinity}', "non-finite JSON token"),
        ('{"value": -Infinity}', "non-finite JSON token"),
        ('{"value": 1e999}', "overflows to a non-finite value"),
    ],
)
def test_target_register_json_is_strict(tmp_path, text, problem):
    path = tmp_path / "register.json"
    path.write_text(text)
    with pytest.raises(tr.RegisterError, match=problem):
        tr.load_register(path, workbook_path=tmp_path / "unused.xlsx")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('{"selected": [], "selected": []}', "duplicate JSON object key"),
        ('{"selected": NaN}', "non-finite JSON token"),
        ('{"selected": Infinity}', "non-finite JSON token"),
        ('{"selected": -Infinity}', "non-finite JSON token"),
        ('{"selected": 1e999}', "overflows to a non-finite value"),
    ],
)
def test_rivm_candidate_json_is_strict(tmp_path, text, problem):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(text)
    spec = {
        "series_name": "rivm_test",
        "candidate_path": str(candidate),
        "candidate_sha256": tr._sha256(candidate),
        "group": "msm",
    }
    with pytest.raises(tr.RegisterError, match=problem):
        tr._rivm_candidate_targets(spec, {}, tmp_path)


# --------------------------------------------------------------------------
# Register loading -- synthetic
# --------------------------------------------------------------------------

def _write_fixture(tmp_path: Path, targets: list[dict], defaults: dict | None = None) -> Path:
    frame = pd.DataFrame(
        {
            "Year": [2020, 2021, 2022],
            "Value": [100.0, 110.0, 120.0],
            "Low": [90.0, 99.0, 108.0],
            "High": [110.0, 121.0, 132.0],
            "Reported": [95.0, 100.0, 100.0],
            "Adjusted": [100.0, 110.0, 120.0],
        }
    )
    workbook = tmp_path / "fixture.xlsx"
    with pd.ExcelWriter(workbook) as writer:
        # header=1 in the loader means a spacer row above the header row.
        pd.DataFrame([["spacer"]]).to_excel(writer, sheet_name="S", index=False, header=False)
        frame.to_excel(writer, sheet_name="S", index=False, startrow=1)
    register = {
        "version": "test",
        "source_workbook": "unused",
        "defaults": defaults or {"v_disc": {"multiplier": 0.10}, "v_ensemble": {"reason": "test"}},
        "targets": targets,
    }
    path = tmp_path / "register.json"
    path.write_text(json.dumps(register), encoding="utf-8")
    return path


BASE_TARGET = {
    "series_name": "s",
    "model_output": "out",
    "source_sheet": "S",
    "source_organisation": "test",
    "year_column": "Year",
    "estimate_column": "Value",
    "year_coverage": [2020, 2022],
    "epistemic_tier": 1,
    "role": "calibration",
    "z_derivation": "test",
    "v_disc": {"method": "default"},
}


def test_published_interval_target_resolves(tmp_path):
    target = dict(BASE_TARGET, lower_column="Low", upper_column="High",
                  v_obs={"method": "published_interval", "divisor": 3.92})
    register = tr.load_register(_write_fixture(tmp_path, [target]), workbook_path=tmp_path / "fixture.xlsx")
    assert len(register.targets) == 3
    first = register.targets[0]
    assert first.year == 2020 and first.z == pytest.approx(100.0)
    assert first.v_obs.sigma == pytest.approx(20.0 / 3.92)
    assert first.v_obs.status == "derived"
    assert first.particle_column == "out_2020"


def test_register_bound_workbook_hash_rejects_a_different_valid_workbook(tmp_path):
    target = dict(
        BASE_TARGET,
        lower_column="Low",
        upper_column="High",
        v_obs={"method": "published_interval", "divisor": 3.92},
    )
    register_path = _write_fixture(tmp_path, [target])
    workbook = tmp_path / "fixture.xlsx"
    register_payload = json.loads(register_path.read_text())
    register_payload["source_workbook_sha256"] = tr._sha256(workbook)
    register_path.write_text(json.dumps(register_payload))

    wrong_workbook = tmp_path / "wrong-but-structurally-valid.xlsx"
    frame = pd.read_excel(workbook, sheet_name="S", header=1)
    frame.loc[0, "Value"] = 999.0
    with pd.ExcelWriter(wrong_workbook) as writer:
        pd.DataFrame([["spacer"]]).to_excel(
            writer, sheet_name="S", index=False, header=False
        )
        frame.to_excel(writer, sheet_name="S", index=False, startrow=1)

    with pytest.raises(tr.RegisterError, match="does not match the register-bound"):
        tr.load_register(register_path, workbook_path=wrong_workbook)


def test_delay_correction_target_resolves_and_flags_the_missing_component(tmp_path):
    target = dict(
        BASE_TARGET,
        v_obs={
            "method": "delay_correction",
            "reported_column": "Reported",
            "adjusted_column": "Adjusted",
            "fraction_key": "frac",
            "missing_component": {"name": "ascertainment", "status": "underivable", "reason": "no source"},
        },
    )
    path = _write_fixture(tmp_path, [target], defaults={"frac": 1.0, "v_disc": {"multiplier": 0.1}, "v_ensemble": {"reason": "t"}})
    register = tr.load_register(path, workbook_path=tmp_path / "fixture.xlsx")
    sigmas = {t.year: t.v_obs.sigma for t in register.targets}
    assert sigmas == {2020: pytest.approx(5.0), 2021: pytest.approx(10.0), 2022: pytest.approx(20.0)}
    # An incomplete V_obs must read as underivable, not as derived.
    assert all(t.v_obs.status == "underivable" for t in register.targets)
    assert all(t.v_obs.direction == "over_exclusion" for t in register.targets)
    assert register.escalations()


def test_overrides_reach_the_delay_fraction(tmp_path):
    target = dict(BASE_TARGET, v_obs={"method": "delay_correction", "reported_column": "Reported",
                                      "adjusted_column": "Adjusted", "fraction_key": "frac"})
    path = _write_fixture(tmp_path, [target], defaults={"frac": 1.0, "v_disc": {"multiplier": 0.1}, "v_ensemble": {"reason": "t"}})
    register = tr.load_register(path, workbook_path=tmp_path / "fixture.xlsx", overrides={"frac": 0.5})
    assert register.targets[0].v_obs.sigma == pytest.approx(2.5)


def test_restricted_years_narrow_the_scored_window(tmp_path):
    target = dict(BASE_TARGET, role="calibration_restricted", restricted_years=[2021, 2021],
                  v_obs={"method": "underivable", "reason": "none"})
    register = tr.load_register(_write_fixture(tmp_path, [target]), workbook_path=tmp_path / "fixture.xlsx")
    assert [t.year for t in register.targets] == [2021]


def test_unknown_role_and_tier_rejected(tmp_path):
    bad_role = dict(BASE_TARGET, role="whatever", v_obs={"method": "underivable", "reason": "x"})
    with pytest.raises(tr.RegisterError, match="unknown role"):
        tr.load_register(_write_fixture(tmp_path, [bad_role]), workbook_path=tmp_path / "fixture.xlsx")
    bad_tier = dict(BASE_TARGET, epistemic_tier=3, v_obs={"method": "underivable", "reason": "x"})
    with pytest.raises(tr.RegisterError, match="epistemic_tier"):
        tr.load_register(_write_fixture(tmp_path, [bad_tier]), workbook_path=tmp_path / "fixture.xlsx")


def test_a_target_resolving_zero_years_is_an_error_not_a_silent_skip(tmp_path):
    target = dict(BASE_TARGET, year_coverage=[1900, 1901], v_obs={"method": "underivable", "reason": "x"})
    with pytest.raises(tr.RegisterError, match="resolved zero years"):
        tr.load_register(_write_fixture(tmp_path, [target]), workbook_path=tmp_path / "fixture.xlsx")


def test_unknown_v_obs_method_rejected(tmp_path):
    target = dict(BASE_TARGET, v_obs={"method": "vibes"})
    with pytest.raises(tr.RegisterError, match="unknown v_obs method"):
        tr.load_register(_write_fixture(tmp_path, [target]), workbook_path=tmp_path / "fixture.xlsx")


def test_role_selection_splits_calibration_from_cross_validation(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "underivable", "reason": "x"})
    b = dict(BASE_TARGET, series_name="b", role="cross_validation", epistemic_tier=2,
             v_obs={"method": "underivable", "reason": "x"})
    c = dict(BASE_TARGET, series_name="c", role="excluded_pending_c1",
             v_obs={"method": "underivable", "reason": "x"})
    register = tr.load_register(_write_fixture(tmp_path, [a, b, c]), workbook_path=tmp_path / "fixture.xlsx")
    assert {t.series_name for t in register.calibration_targets()} == {"a"}
    assert {t.series_name for t in register.cross_validation_targets()} == {"b"}
    assert len(register.targets) == 9
    assert register.restrict_years(register.calibration_targets(), [2021]) == [
        t for t in register.calibration_targets() if t.year == 2021
    ]
    assert len(register.restrict_years(register.calibration_targets(), None)) == 3


def test_restrict_years_accepts_per_series_anchor_map(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "underivable", "reason": "x"})
    b = dict(BASE_TARGET, series_name="b", v_obs={"method": "underivable", "reason": "x"})
    register = tr.load_register(_write_fixture(tmp_path, [a, b]), workbook_path=tmp_path / "fixture.xlsx")
    selected = register.restrict_years(register.calibration_targets(), {"a": [2020, 2022], "b": [2021]})
    assert [(target.series_name, target.year) for target in selected] == [
        ("a", 2020), ("a", 2022), ("b", 2021)
    ]


# --------------------------------------------------------------------------
# Budget assembly
# --------------------------------------------------------------------------

def test_build_budgets_applies_the_tier2_increment_only_to_tier_2(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "published_interval"},
             lower_column="Low", upper_column="High")
    b = dict(BASE_TARGET, series_name="b", epistemic_tier=2, role="cross_validation",
             v_obs={"method": "published_interval"}, lower_column="Low", upper_column="High")
    defaults = {
        "v_disc": {"multiplier": 0.10, "source": "test"},
        "v_ensemble": {"reason": "A-5 not run"},
        "tier2_disc_increment_multiplier": 1.0,
        "tier2_disc_increment_rationale": "CSAVR is a model",
    }
    register = tr.load_register(_write_fixture(tmp_path, [a, b], defaults), workbook_path=tmp_path / "fixture.xlsx")
    simulated = {t.key: [1.0, 2.0, 3.0, 4.0] for t in register.targets}
    budgets = tr.build_budgets(register.targets, simulated, defaults=register.meta["defaults_effective"])
    tier1 = budgets["a@2020"]
    tier2 = budgets["b@2020"]
    assert [term.name for term in tier1.terms] == ["v_obs", "v_code", "v_ensemble", "v_disc"]
    assert "v_disc_tier2" in [term.name for term in tier2.terms]
    assert tier2.total_variance > tier1.total_variance
    assert tier1.v_ensemble.status == "underivable"
    assert "A-5 not run" in tier1.v_ensemble.derivation


def test_build_budgets_multiplier_override_changes_v_disc(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "published_interval"},
             lower_column="Low", upper_column="High")
    register = tr.load_register(_write_fixture(tmp_path, [a]), workbook_path=tmp_path / "fixture.xlsx")
    simulated = {t.key: [0.0, 10.0] for t in register.targets}
    low = tr.build_budgets(register.targets, simulated, defaults=register.meta["defaults_effective"], disc_multiplier=0.1)
    high = tr.build_budgets(register.targets, simulated, defaults=register.meta["defaults_effective"], disc_multiplier=1.0)
    assert high["a@2020"].v_disc.variance == pytest.approx(10.0 * low["a@2020"].v_disc.variance)


def test_build_budgets_supports_declared_target_relative_discrepancy(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "published_interval"},
             lower_column="Low", upper_column="High")
    register = tr.load_register(_write_fixture(tmp_path, [a]), workbook_path=tmp_path / "fixture.xlsx")
    simulated = {target.key: [0.0, 10.0] for target in register.targets}
    budgets = tr.build_budgets(
        register.targets,
        simulated,
        defaults=register.meta["defaults_effective"],
        disc_method="relative_to_target",
        disc_multiplier=0.10,
    )
    assert budgets["a@2020"].v_disc.variance == pytest.approx(100.0)
    assert "target-relative" in budgets["a@2020"].v_disc.derivation


def test_build_budgets_raises_when_a_target_has_no_simulator_output(tmp_path):
    a = dict(BASE_TARGET, series_name="a", v_obs={"method": "published_interval"},
             lower_column="Low", upper_column="High")
    register = tr.load_register(_write_fixture(tmp_path, [a]), workbook_path=tmp_path / "fixture.xlsx")
    with pytest.raises(tr.RegisterError, match="No simulator outputs"):
        tr.build_budgets(register.targets, {}, defaults=register.meta["defaults_effective"])


# --------------------------------------------------------------------------
# Provenance tests against the real workbook
# --------------------------------------------------------------------------

@needs_workbook
def test_production_register_loads_and_tiers_are_as_declared():
    register = tr.load_register()
    summary = register.series_summary().set_index("series_name")
    assert summary.loc["shm_new_diagnoses_total", "epistemic_tier"] == 1
    assert summary.loc["shm_new_diagnoses_total", "role"] == "cross_validation"
    assert summary.loc["shm_new_diagnoses_msm", "role"] == "calibration"
    assert summary.loc["unaids_plhiv_all_ages", "epistemic_tier"] == 2
    assert summary.loc["unaids_plhiv_all_ages", "role"] == "cross_validation"
    assert summary.loc["unaids_art_stock_15plus", "role"] == "cross_validation"
    assert summary.loc["shm_new_diagnoses_non_msm", "role"] == "excluded"
    # No tier-2 series may sit in the calibration set. Tier-1 total diagnoses are
    # deliberately held out because C-1 makes their residual input-driven and
    # collinear with the MSM target.
    assert all(t.tier == 1 for t in register.calibration_targets())
    assert all(t.series_name != "shm_new_diagnoses_total" or t.tier == 1
               for t in register.cross_validation_targets())


@needs_workbook
def test_production_register_z_values_match_named_workbook_cells():
    """Anchors on figures quoted in the handoff and the reachability audit."""
    register = tr.load_register()
    z = {(t.series_name, t.year): t.z for t in register.targets}
    assert z[("shm_new_diagnoses_total", 2024)] == pytest.approx(517.0)
    assert z[("shm_new_diagnoses_msm", 2024)] == pytest.approx(318.0)
    assert z[("shm_new_diagnoses_non_msm", 2024)] == pytest.approx(199.0)
    assert z[("shm_new_diagnoses_non_msm", 1996)] == pytest.approx(259.0)
    assert z[("shm_new_diagnoses_non_msm", 2020)] == pytest.approx(168.0)
    assert z[("unaids_plhiv_all_ages", 2023)] == pytest.approx(25461.26, abs=0.01)
    assert z[("unaids_art_stock_15plus", 2023)] == pytest.approx(22446.3, abs=0.01)
    # Adult-only: all-age mid minus child mid.
    assert z[("unaids_new_infections_15plus", 2023)] == pytest.approx(311.8895 - 2.03426, abs=0.01)
    assert z[("unaids_aids_deaths_15plus", 2023)] == pytest.approx(70.8932 - 2.19304, abs=0.01)


@needs_workbook
def test_production_register_excludes_the_pre_1996_lump_and_the_total_row():
    register = tr.load_register()
    shm_years = sorted({t.year for t in register.targets if t.series_name == "shm_new_diagnoses_total"})
    assert shm_years == list(range(1996, 2025))
    # 3390 is the '<=1995' lump for Total Adjusted (No <15); 27725 is the 'Total' row.
    assert all(t.z not in (3390.0, 27725.0) for t in register.targets)


@needs_workbook
def test_production_register_unaids_series_stop_at_2023_because_2024_is_null():
    register = tr.load_register()
    for series in ("unaids_plhiv_all_ages", "unaids_new_infections_15plus", "unaids_aids_deaths_15plus"):
        assert max(t.year for t in register.targets if t.series_name == series) == 2023


@needs_workbook
def test_production_shm_delay_sigma_decays_with_notification_lag():
    """The empirical handle the register uses: the correction shrinks as lag grows."""
    register = tr.load_register()
    sigma = {t.year: t.v_obs.sigma for t in register.targets if t.series_name == "shm_new_diagnoses_total"}
    assert sigma[2024] == pytest.approx(73.0)   # 517 - 444
    assert sigma[2023] == pytest.approx(34.0)   # 498 - 464
    assert sigma[2019] == pytest.approx(3.0)
    assert sigma[2018] == pytest.approx(0.0)
    assert sigma[2017] == pytest.approx(0.0)
    for earlier, later in zip(range(2019, 2024), range(2020, 2025)):
        assert sigma[earlier] <= sigma[later]


@needs_workbook
def test_production_register_escalations_name_every_underivable_source():
    register = tr.load_register()
    text = " ".join(register.escalations())
    for series in ("ecdc_aids_confirmed_cases", "ecdc_hiv_confirmed_cases", "unaids_art_stock_15plus"):
        assert series in text
