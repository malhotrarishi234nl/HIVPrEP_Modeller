"""Guards for the focused stage-structured diagnosis calibration (2026-09-22).

Every test here uses synthetic fixtures, mocked engines (``object.__new__``) or
configuration-only code. No SimulationEngineV3 is constructed and no real
trajectory is run: the synthetic worker below replaces the simulator in the
end-to-end pipeline test.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import coupled_calibration_search as ccs
import run_post2015_stock_flow_decomposition as ledger_module
import run_stage_structured_diagnosis_audit as audit
import run_stage_structured_diagnosis_focused_calibration as fc
from run_v3 import SimulationEngineV3
from schedule_utils import TimeSchedule
from stage_structured_diagnosis import (
    StageStructuredDiagnosis,
    StageStructuredDiagnosisConfigError,
    load_stage_structured_diagnosis,
    stage_step_probability,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def context():
    """Real frozen inputs and the full design, under the engine guard."""
    with fc.EngineGuard():
        plan = fc.load_plan()
        source, search, confirmation, state, bases, raw = fc._source(plan)
        design = fc.build_design(plan, bases)
    return SimpleNamespace(plan=plan, state=state, bases=bases, raw=raw, design=design)


@pytest.fixture(scope="module")
def validation():
    with fc.EngineGuard():
        return fc.validate(fc.DEFAULT_PLAN)


def _mock_engine(years_per_step=0.25, multiplier=2.0, profile=None):
    e = object.__new__(SimulationEngineV3)
    e.state = SimpleNamespace(population=np.ones((1, 2, 1, 4, 2, 1, 1), dtype=int))
    e.state.population[:, :, :, 0] = 0
    e.groups = SimpleNamespace(orient_to_idx={"Female": 0, "MSM": 1},
                               hiv_to_idx={"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3})
    e.time = SimpleNamespace(
        years_per_step=years_per_step,
        annual_to_step_rate=lambda p: 1 - (1 - p) ** years_per_step,
    )
    e.msm_relative_diagnosis_schedule = TimeSchedule(
        {"1996": multiplier}, interpolation="stepwise", default_before=1.0)
    e.diagnosis_schedule = TimeSchedule({"1985": 0.2}, interpolation="stepwise", default_before=0.0)
    e.config = {"disease": {"diagnosis": {"annual_rate": {"value": 0.35}}}}
    if profile is not None:
        e.stage_structured_diagnosis = profile
    calls = []

    def draw(name, pool, probability):
        calls.append((name, float(probability)))
        return np.zeros_like(pool)

    e._draw_events_for = draw
    return e, calls


# --------------------------------------------------------------------------
# 1. Disabled and enabled identity are exact legacy behaviour
# --------------------------------------------------------------------------

@pytest.mark.parametrize("year", [1984.75, 1985.0, 2000.0])
def test_disabled_identity_and_identity_with_boundary_take_the_legacy_draws(year):
    profiles = [None, StageStructuredDiagnosis(False, 0.6, 0.4, 1985.0),
                StageStructuredDiagnosis(True, 1.0, 0.0, None),
                StageStructuredDiagnosis(True, 1.0, 0.0, 1985.0)]
    observed = []
    for profile in profiles:
        engine, calls = _mock_engine(profile=profile)
        engine._step_diagnosis(year)
        observed.append(calls)
    assert all(calls == observed[0] for calls in observed)
    assert [name for name, _ in observed[0]] == ["diagnosis_non_msm", "diagnosis_msm"]


def test_identity_profile_is_identity_regardless_of_boundary():
    assert StageStructuredDiagnosis(True, 1.0, 0.0, 1985.0).is_identity
    assert not StageStructuredDiagnosis(True, 1.0, 0.1, 1985.0).is_identity


# --------------------------------------------------------------------------
# 2. Symptom uplift is zero before 1985 and active at/after it
# --------------------------------------------------------------------------

def _rates(year, profile):
    engine, calls = _mock_engine(profile=profile)
    engine._step_stage_structured_diagnosis(year, 0.2, 1, profile)
    return dict(calls)


def test_symptom_uplift_is_zero_before_boundary_and_active_at_boundary():
    profile = StageStructuredDiagnosis(True, 1.0, 0.35, 1985.0)
    before, at = _rates(1984.75, profile), _rates(1985.0, profile)
    for population in ("msm", "non_msm"):
        stem = f"stage_structured_diagnosis_{population}"
        assert before[f"{stem}_aids"] == before[f"{stem}_chronic"]
        assert at[f"{stem}_aids"] > at[f"{stem}_chronic"]
    assert profile.symptom_probability_at(1984.999) == 0.0
    assert profile.symptom_probability_at(1985.0) == 0.35
    assert StageStructuredDiagnosis(True, 1.0, 0.35, None).symptom_probability_at(1970) == 0.35


@pytest.mark.parametrize("raw", [{"value": True}, {"value": float("nan")},
                                 {"value": 1800}, {"value": "late"}])
def test_invalid_symptom_start_year_fails_closed(raw):
    block = {"enabled": {"value": True}, "aids_symptom_annual_probability": {"value": 0.2},
             "symptom_start_year": raw}
    with pytest.raises(StageStructuredDiagnosisConfigError):
        load_stage_structured_diagnosis({"disease": {"diagnosis": {"stage_structured_diagnosis": block}}})


def test_symptom_start_year_parses_and_absence_keeps_audit_semantics():
    block = {"enabled": {"value": True}, "aids_symptom_annual_probability": {"value": 0.2},
             "symptom_start_year": {"value": 1985}}
    wrap = lambda b: {"disease": {"diagnosis": {"stage_structured_diagnosis": b}}}  # noqa: E731
    assert load_stage_structured_diagnosis(wrap(block)).symptom_start_year == 1985.0
    del block["symptom_start_year"]
    assert load_stage_structured_diagnosis(wrap(block)).symptom_start_year is None


# --------------------------------------------------------------------------
# 3. The MSM multiplier modifies routine hazard only
# --------------------------------------------------------------------------

def test_msm_multiplier_scales_routine_hazard_only():
    symptom, dt = 0.35, 0.25
    rates = _rates(2000.0, StageStructuredDiagnosis(True, 1.0, symptom, 1985.0))
    hazard = lambda p: -math.log1p(-p) / dt  # noqa: E731
    for population in ("msm", "non_msm"):
        stem = f"stage_structured_diagnosis_{population}"
        uplift = hazard(rates[f"{stem}_aids"]) - hazard(rates[f"{stem}_chronic"])
        assert uplift == pytest.approx(-math.log1p(-symptom), rel=1e-12)
        assert rates[f"{stem}_acute"] == rates[f"{stem}_chronic"]
    # routine: MSM uses clip(0.2 * 2.0) = 0.4, non-MSM 0.2
    assert hazard(rates["stage_structured_diagnosis_msm_chronic"]) == \
        pytest.approx(-math.log1p(-0.4), rel=1e-12)
    assert hazard(rates["stage_structured_diagnosis_non_msm_chronic"]) == \
        pytest.approx(-math.log1p(-0.2), rel=1e-12)


# --------------------------------------------------------------------------
# 4-6. Stage B changes only the four coordinates; schedules preserved
# --------------------------------------------------------------------------

def test_stage_b_fixes_rho_and_changes_only_focused_coordinates(context):
    checks = fc.check_design(context.plan, context.bases, context.design)
    assert checks["stage_b_rho_fixed_at_one"] and checks["only_focused_paths_differ"]
    for item in context.design["stage_b"]:
        block = item["config"]["transmission_model"]["disease"]["diagnosis"]["stage_structured_diagnosis"]
        assert block["routine_hazard_scale"]["value"] == 1.0
        assert block["symptom_start_year"]["value"] == 1985
        changed = fc.changed_paths(context.bases[item["backbone"]], item["config"])
        allowed = {".".join(fc.MSM_SCHEDULE_PATH + (y,)) for y in ("1996", "2004", "2019")}
        assert all(p in allowed or p.startswith(".".join(fc.PROFILE_PATH)) for p in changed)


def test_base_schedule_is_byte_for_byte_unchanged(context):
    for item in context.design["stage_a"] + context.design["stage_b"]:
        base = context.bases[item["backbone"]]
        assert json.dumps(fc._get(item["config"], fc.BASE_SCHEDULE_PATH), sort_keys=True) == \
            json.dumps(fc._get(base, fc.BASE_SCHEDULE_PATH), sort_keys=True)


def test_tampering_outside_focused_paths_is_rejected(context):
    design = copy.deepcopy(context.design)
    victim = design["stage_b"][7]
    victim["config"]["transmission_model"]["disease"]["diagnosis"]["schedule"]["value"]["1990"] += 1e-9
    with pytest.raises(ccs.ContractError, match="changed outside"):
        fc.check_design(context.plan, context.bases, design)
    design = copy.deepcopy(context.design)
    design["stage_b"][3]["config"]["transmission_model"]["disease"]["natural_history_marker"] = 1
    with pytest.raises(ccs.ContractError, match="changed outside"):
        fc.check_design(context.plan, context.bases, design)


def test_surgical_edit_matches_production_injection(context):
    space_json = json.loads(context.state["paths"]["calib_space"].read_text())
    checks = fc.check_design(context.plan, context.bases, context.design, space_json=space_json,
                             base_config=context.state["base_config"],
                             raw_params={c: context.raw[c]["params"] for c in context.bases})
    assert checks["stage_b_matches_production_injection"] is True


def test_design_composition_and_warm_proposals(context):
    for cid in context.plan["candidate_ids"]:
        rows = [i for i in context.design["stage_b"] if i["backbone"] == cid]
        assert len(rows) == 128
        assert rows[0]["is_identity"] and rows[0]["vector"] == fc.backbone_vector(context.bases[cid], context.plan)
        assert [r["origin"] for r in rows[:3]] == [
            "warm:identity", "warm:prior_rho1p00_aids0p35", "warm:prior_rho0p80_aids0p35"]
        assert sum(r["origin"] == "maximin_lhs" for r in rows) == 125
    warm = [w for w in context.design["warm_records"] if w["id"] == "prior_rho0p80_aids0p35"]
    assert all(w["valid"] and max(w["anchor_path_deviation"].values()) < 1e-12 for w in warm)


def test_warm_proposal_is_rejected_not_clipped(context):
    record = fc.warm_proposal(context.bases[1326], {"id": "x", "rho": 6.0, "p_symptom_aids": 0.6},
                              context.plan)
    assert record["valid"] is False
    assert any("outside" in r for r in record["rejection_reasons"])
    assert record["vector"][fc.SYMPTOM_NAME] == 0.6


def test_maximin_lhs_is_reproducible_and_latin():
    a = fc.maximin_lhs(25, 4, 7, 30)
    b = fc.maximin_lhs(25, 4, 7, 30)
    np.testing.assert_array_equal(a, b)
    for j in range(4):
        assert sorted(np.floor(a[:, j] * 25).astype(int)) == list(range(25))


# --------------------------------------------------------------------------
# 7. Annual-to-step hazard arithmetic
# --------------------------------------------------------------------------

def test_step_and_effective_path_arithmetic_match_hand_calculation(context):
    p = stage_step_probability(0.3, routine_hazard_scale=0.8, aids_symptom_annual_probability=0.2,
                               years_per_step=0.25, aids_stage=True)
    assert p == pytest.approx(1 - math.exp(-(0.8 * -math.log(0.7) - math.log(0.8)) * 0.25), rel=1e-14)
    q = stage_step_probability(0.3, routine_hazard_scale=1.0, aids_symptom_annual_probability=0.0,
                               years_per_step=0.25, aids_stage=False)
    assert q == pytest.approx(1 - 0.7 ** 0.25, rel=1e-14)
    base = context.bases[1326]
    table = ccs.msm_diagnosis_probability_table(base, [1985, 1996, 2004, 2010, 2019, 2024])
    rho1 = fc.effective_msm_routine_probability(base, [r["year"] for r in table], rho=1.0)
    np.testing.assert_array_equal(rho1, [r["clipped_msm_annual_probability"] for r in table])
    rho8 = fc.effective_msm_routine_probability(base, [2010], rho=0.8)[0]
    assert rho8 == pytest.approx(1 - (1 - rho1[3]) ** 0.8, rel=1e-13)


# --------------------------------------------------------------------------
# 8. Stage counters partition endogenous diagnoses; 1985 boundary counters
# --------------------------------------------------------------------------

def _synthetic_outputs(first=1975, last=2025, aids_pre=0.0):
    out = {}
    for suffix in ("", "_msm", "_non_msm"):
        for stage, v in zip(fc.DX_STAGES, (1.0, 5.0, 2.0)):
            out[f"hiv_diagnoses_in_{stage}_stage{suffix}"] = {
                y: (0.0 if y < 1985 else v) for y in range(first, last + 1)}
        out[f"new_diagnoses_endogenous{suffix}"] = {y: (0.0 if y < 1985 else 8.0)
                                                   for y in range(first, last + 1)}
        out[f"aids_progression_while_diagnosed{suffix}"] = {y: 3.0 for y in range(first, last + 1)}
        out[f"aids_first_diagnosis_proxy{suffix}"] = {
            y: 3.0 + out[f"hiv_diagnoses_in_aids_stage{suffix}"][y] for y in range(first, last + 1)}
    if aids_pre:
        out["hiv_diagnoses_in_aids_stage"][1984] = aids_pre
        out["new_diagnoses_endogenous"][1984] = aids_pre
        out["aids_first_diagnosis_proxy"][1984] = 3.0 + aids_pre
    return out


def test_trajectory_identities_and_boundary_check(context):
    config = context.design["stage_b"][2]["config"]
    result = fc.check_trajectory_outputs(_synthetic_outputs(), context.plan, config)
    assert result["pre_boundary_aids_stage_diagnoses"] == 0.0
    assert result["post_boundary_aids_stage_diagnoses_to_check_year"] == 6.0
    assert result["pre_boundary_zero_verifiable"] is True
    with pytest.raises(ccs.ContractError, match="before 1985"):
        fc.check_trajectory_outputs(_synthetic_outputs(aids_pre=2.0), context.plan, config)
    broken = _synthetic_outputs()
    broken["hiv_diagnoses_in_chronic_stage_msm"][2000] += 1.0
    with pytest.raises(ccs.ContractError, match="stage counters"):
        fc.check_trajectory_outputs(broken, context.plan, config)


# --------------------------------------------------------------------------
# 9. Exogenous non-MSM diagnosis is unchanged by the profile
# --------------------------------------------------------------------------

def test_exogenous_non_msm_is_called_unchanged_after_stage_diagnosis():
    seen = []
    for profile in (None, StageStructuredDiagnosis(True, 0.8, 0.3, 1985.0)):
        engine, _ = _mock_engine(profile=profile)
        engine._step_exogenous_non_msm = lambda year: seen.append(year) or 17
        result = engine._step_diagnosis_and_exogenous(2001.0)
        assert result["exogenous_non_msm"] == 17
    assert seen == [2001.0, 2001.0]


# --------------------------------------------------------------------------
# 10. Stock-flow ledgers close exactly
# --------------------------------------------------------------------------

def test_stock_flow_ledger_closes_or_fails():
    n_metrics = len(ledger_module.METRICS)
    stocks = {y: np.full((2, n_metrics), float(y - 2000)) for y in (2015, 2016, 2017)}
    flows = {(2015, "diagnosis"): np.ones((2, n_metrics)), (2016, "diagnosis"): np.ones((2, n_metrics))}
    recorder = SimpleNamespace(stocks=stocks, flows=flows, capture_stock=lambda year: None)
    engine = SimpleNamespace(groups=SimpleNamespace(activities=["Low", "High"]))
    rows = audit._ledger_rows(recorder, engine, 2015, 2016)
    assert max(abs(r["balance_residual"]) for r in rows) == 0.0
    flows[(2016, "diagnosis")] = np.full((2, n_metrics), 1.5)
    with pytest.raises(ccs.ContractError, match="residual"):
        audit._ledger_rows(recorder, engine, 2015, 2016)


# --------------------------------------------------------------------------
# 11. Seed sets
# --------------------------------------------------------------------------

def test_seed_sets_are_mutually_disjoint_and_overlap_is_refused(tmp_path):
    plan = fc.load_plan()
    sets = fc._seed_sets(plan)
    flat = [s for v in sets.values() for s in v]
    assert len(flat) == len(set(flat)) and len(sets["stage_c"]) == 20
    bad = copy.deepcopy(plan)
    bad["stage_c"]["seeds"][0] = bad["stage_a"]["seeds"][0]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ccs.ContractError, match="overlap"):
        fc.load_plan(path)


def test_seed_preflight_detects_recorded_use_and_skips_own_design(tmp_path):
    plan = fc.load_plan()
    outputs, configs = tmp_path / "outputs", tmp_path / "configs"
    (outputs / "own").mkdir(parents=True)
    configs.mkdir()
    (outputs / "own/manifest.json").write_text(json.dumps({"design": fc.DESIGN, "seeds": {"x": [201010]}}))
    result = fc.seed_preflight(plan, outputs_root=outputs, configs_root=configs)
    assert result["overlap"] == [] and result["n_own_skipped"] == 1
    (outputs / "other").mkdir()
    (outputs / "other/manifest.json").write_text(json.dumps({"replicate_seeds": [201031]}))
    with pytest.raises(ccs.ContractError, match="201031"):
        fc.seed_preflight(plan, outputs_root=outputs, configs_root=configs)


# --------------------------------------------------------------------------
# 12. Duplicate effective routine paths
# --------------------------------------------------------------------------

def test_duplicate_effective_routine_paths_are_detected(context):
    groups = context.design["duplicate_routine_paths"]["stage_b"]
    assert ["B_1326_v000", "B_1326_v001"] in groups and ["B_1788_v000", "B_1788_v001"] in groups
    keys = [i["routine_path_key"] for i in context.design["stage_b"] if i["backbone"] == 1326]
    assert len(set(keys)) == 127  # only the identity/prior-rho1 pair shares a path


def _discovery_frame():
    rows = []
    for cid in (1326, 1788):
        for k in range(8):
            rows.append({
                "tuple_id": f"B_{cid}_v{k:03d}", "backbone": cid, "is_identity": k == 0,
                "primary_pass": k in (0, 1, 2, 3, 4, 5), "primary_i_max": 2.0 + k / 10,
                "search_j": 1.0 + k / 10, "search_mean_r": 1.0, "dx2024_abs_error": 60.0 - k,
                "R_aids_after_2015": 1.0 + k / 7, "p_symptom_aids": [0, .05, .15, .3, .45, .2, .1, .4][k],
                "routine_path_key": f"{cid}-path-{k if k != 2 else 1}",
                "config_sha256": f"sha{cid}{k}",
                **{n: 0.5 for n in fc.FOCUSED_NAMES}})
    return pd.DataFrame(rows)


def test_shortlist_excludes_duplicates_prefers_passes_and_caps_at_ten():
    plan = fc.load_plan()
    entries, exclusions = fc.select_shortlist(_discovery_frame(), plan)
    ids = [e["tuple_id"] for e in entries]
    assert len(ids) == len(set(ids)) <= 10
    assert [e["role"] for e in entries[:2]] == ["backbone_comparator"] * 2
    mech = [e for e in entries if e["role"].startswith("mechanism")]
    paths = [e["routine_path_key"] for e in mech]
    assert len(paths) == len(set(paths))
    assert any(x["reason"] == "duplicate effective MSM routine path" for x in exclusions)
    first_near = next((i for i, e in enumerate(mech) if e["role"] == "mechanism_near_miss"), len(mech))
    assert all(e["discovery_primary_pass"] for e in mech[:first_near])
    assert {e["backbone"] for e in mech} == {1326, 1788}
    assert mech[0]["selected_as"] == "best_2024_diagnosis_primary_pass"


# --------------------------------------------------------------------------
# 13. Validate-only constructs no engine, starts nothing, writes nothing
# --------------------------------------------------------------------------

def test_validate_only_constructs_no_engine_and_writes_nothing(validation, monkeypatch):
    assert validation["ok"] and validation["engines_constructed"] == 0
    assert validation["simulations_started"] == 0 and validation["artifacts_written"] == 0
    assert validation["stage_a_tuples"] == 60 and validation["stage_b_tuples"] == 256
    assert [r["trajectories"] for r in validation["budget"]] == [300, 1280, 200, 1780]
    import concurrent.futures  # noqa: PLC0415

    def forbidden(*args, **kwargs):
        raise AssertionError("validate-only must not start workers or engines")

    monkeypatch.setattr(SimulationEngineV3, "__init__", forbidden)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", forbidden)
    monkeypatch.setattr(fc, "ProcessPoolExecutor", forbidden)
    monkeypatch.setattr(fc, "run_trajectory", forbidden)
    before = fc.DEFAULT_OUTPUT.exists()
    again = fc.validate(fc.DEFAULT_PLAN, injection_crosscheck=False)
    assert again["design_sha256"] == validation["design_sha256"]
    assert fc.DEFAULT_OUTPUT.exists() == before


def test_engine_guard_blocks_construction():
    with fc.EngineGuard():
        with pytest.raises(ccs.ContractError, match="forbidden"):
            SimulationEngineV3({}, seed=1)


# --------------------------------------------------------------------------
# 15. Decision classification: three labels, three distinct fixtures
# --------------------------------------------------------------------------

def _evaluation(tid="B_1326_v010", **overrides):
    base = {k: True for k, _ in fc.CLAUSES}
    base.update({"tuple_id": tid, "primary_i_max": 2.0, "primary_i_max_improved_vs_comparator": True,
                 "material_hiv_death_increase": False, "material_msm_infection_increase": False})
    base.update(overrides)
    return base


def test_decision_supported_fixture():
    decision = fc.classify_decision([_evaluation(), _evaluation("B_1788_v011", c6_not_boundary_point=False)],
                                    design_complete=True, discovery_apparent_improvement=True)
    assert decision["label"] == fc.LABEL_SUPPORTED and decision["tuple_id"] == "B_1326_v010"


def test_decision_informative_fixture():
    decision = fc.classify_decision(
        [_evaluation(c5_no_mortality_or_latent_pathology=False, material_hiv_death_increase=True)],
        design_complete=True, discovery_apparent_improvement=True)
    assert decision["label"] == fc.LABEL_INFORMATIVE
    assert decision["failing_clauses"] == ["c5_no_mortality_or_latent_pathology"]
    assert decision["follow_up"] == fc.FOLLOW_UPS["c5_no_mortality_or_latent_pathology"]


def test_decision_not_supported_fixture():
    none_stable = _evaluation(c1_primary_pass_fresh_seeds=False, c2_material_2024_improvement=False,
                              primary_i_max_improved_vs_comparator=False)
    decision = fc.classify_decision([none_stable], design_complete=True,
                                    discovery_apparent_improvement=True)
    assert decision["label"] == fc.LABEL_NOT_SUPPORTED
    pathological = _evaluation(c1_primary_pass_fresh_seeds=False, material_msm_infection_increase=True)
    decision = fc.classify_decision([pathological], design_complete=True,
                                    discovery_apparent_improvement=True)
    assert decision["label"] == fc.LABEL_NOT_SUPPORTED


def test_incomplete_design_is_never_not_supported():
    none_stable = _evaluation(c1_primary_pass_fresh_seeds=False, c2_material_2024_improvement=False,
                              primary_i_max_improved_vs_comparator=False)
    for evaluations in ([], [none_stable]):
        decision = fc.classify_decision(evaluations, design_complete=False,
                                        discovery_apparent_improvement=False)
        assert decision["label"] == fc.LABEL_INFORMATIVE
        assert decision["follow_up"] == fc.FOLLOW_UPS["incomplete"]


def test_clause_evaluation_thresholds():
    plan = fc.load_plan()
    z = {"rivm_aids_msm@2019": 100.0, "rivm_aids_msm@2022": 100.0}
    comparator = {"cum_hiv_deaths_2015_2024": 1000.0, "cum_msm_infections_2015_2024": 2000.0,
                  "undiagnosed_msm_start2025": 500.0, "on_art_stock_start2025": 10000.0,
                  "dx2024_abs_error": 100.0, "aids2019_abs_error": 5.0, "aids2022_abs_error": 5.0,
                  "R_diagnoses_1996_2015": 1.0, "primary_i_max": 2.5}
    row = {**comparator, "primary_pass": True, "dx2024_abs_error": 70.0,
           "paired_seeds_improved_2024": 15, "diagnosed_off_art_start2025": 10.0,
           "cum_hiv_deaths_2015_2024": 1101.0, "aids2019_abs_error": 16.0, "aids2022_abs_error": 15.5,
           "primary_i_max": 2.0}
    disc = {"boundary_point": False, "has_nearby_region": True}
    e = fc.evaluate_clauses(row, comparator, disc, z, plan, True)
    assert e["c2_material_2024_improvement"] is True
    assert e["material_hiv_death_increase"] and not e["c5_no_mortality_or_latent_pathology"]
    assert e["c3_post2015_aids_not_both_materially_worse"] is False
    row["paired_seeds_improved_2024"] = 14
    assert fc.evaluate_clauses(row, comparator, disc, z, plan, True)["c2_material_2024_improvement"] is False


# --------------------------------------------------------------------------
# 14 + end-to-end: prepare, resume guards, time stop, synthetic pipeline
# --------------------------------------------------------------------------

def _synthetic_worker(targets):
    z = {t.key: t.z for t in targets}
    dx_years = sorted(t.year for t in targets if t.model_output == "new_diagnoses_msm")
    aids_years = sorted(t.year for t in targets if t.model_output == "aids_first_diagnosis_proxy_msm")

    def interp(years, series, year):
        return float(np.interp(year, years, [z[f"{series}@{y}"] for y in years]))

    def work(payload):
        config, seed, plan = payload["config"], int(payload["seed"]), payload["plan"]
        rho, symptom, _ = fc._profile_values(config)
        eff = fc.effective_msm_routine_probability(config, [2019])[0]
        rng = np.random.default_rng(seed + int(payload["backbone"]))
        dx_scale = 1.0 + 0.6 * (0.35 - eff) + 0.3 * (1 - rho) + 0.05 * rng.standard_normal()
        aids_scale = 0.9 + 0.8 * symptom + 0.05 * rng.standard_normal()
        out = {}
        years = range(plan["annual_start_year"], plan["annual_end_year"] + 1)
        for suffix, share in (("_msm", 1.0), ("_non_msm", 0.25)):
            dx = {y: (0.0 if y < 1985 else share * dx_scale *
                      interp(dx_years, "shm_new_diagnoses_msm", y)) for y in years}
            aids_dx = {y: (0.0 if y < 1985 else dx[y] * (0.08 + 0.3 * symptom)) for y in years}
            acute = {y: 0.2 * (dx[y] - aids_dx[y]) for y in years}
            out[f"hiv_diagnoses_in_aids_stage{suffix}"] = aids_dx
            out[f"hiv_diagnoses_in_acute_stage{suffix}"] = acute
            out[f"hiv_diagnoses_in_chronic_stage{suffix}"] = {
                y: dx[y] - aids_dx[y] - acute[y] for y in years}
            progression = {y: max(share * aids_scale * interp(aids_years, "rivm_aids_msm", y)
                                  - aids_dx[y], 0.0) for y in years}
            out[f"aids_progression_while_diagnosed{suffix}"] = progression
            out[f"aids_progression_while_undiagnosed{suffix}"] = {y: 1.0 for y in years}
            out[f"new_diagnoses_endogenous{suffix}"] = {
                y: sum(out[f"hiv_diagnoses_in_{s}_stage{suffix}"][y] for s in fc.DX_STAGES)
                for y in years}
        for name in ("hiv_diagnoses_in_acute_stage", "hiv_diagnoses_in_chronic_stage",
                     "hiv_diagnoses_in_aids_stage", "aids_progression_while_diagnosed",
                     "aids_progression_while_undiagnosed"):
            out[name] = {y: out[name + "_msm"][y] + out[name + "_non_msm"][y] for y in years}
        out["new_diagnoses_endogenous"] = {
            y: sum(out[f"hiv_diagnoses_in_{s}_stage"][y] for s in fc.DX_STAGES) for y in years}
        for suffix in ("", "_msm", "_non_msm"):
            out["aids_first_diagnosis_proxy" + suffix] = {
                y: out["aids_progression_while_diagnosed" + suffix][y] +
                out["hiv_diagnoses_in_aids_stage" + suffix][y] for y in years}
        out["new_diagnoses_msm"] = dict(out["new_diagnoses_endogenous_msm"])
        out["new_diagnoses_exogenous_non_msm"] = {y: (0.0 if y < 2000 else 50.0) for y in years}
        out["new_infections_msm"] = {y: 400.0 * (1.5 - rho) for y in years}
        boundary = fc.check_trajectory_outputs(out, plan, config)
        kept = {s: {y: v for y, v in vals.items()
                    if y >= plan["full_output_start_year"] or s in fc.EARLY_SERIES}
                for s, vals in out.items()}
        snapshots = []
        for year in (2015, 2025):
            for stage in ("acute", "chronic", "aids"):
                snapshots.append({"snapshot_year": year, "series": f"msm_undiagnosed_{stage}",
                                  "value": 300.0 * (1.5 - rho), "activity": "All"})
            snapshots += [{"snapshot_year": year, "series": "on_art_stock_state", "value": 20000.0,
                           "activity": "All"},
                          {"snapshot_year": year, "series": "diagnosed_off_art_stock", "value": 500.0,
                           "activity": "All"}]
        ledger = [{"year": 2015, "activity": "Low", "metric": "m", "stock_start": 1.0, "stock_end": 2.0,
                   "stock_change": 1.0, "flow_sum": 1.0, "balance_residual": 0.0, "delta__x": 1.0}]
        return {k: payload[k] for k in ("stage", "tuple_id", "backbone", "seed")} | {
            "annual_outputs": kept, "boundary": boundary, "snapshots": snapshots,
            "mortality": [{"year": y, "series": "hiv_deaths_diagnosed_on_art", "value": 100.0 + 20 * symptom}
                          for y in range(2015, 2026)],
            "ledger": ledger, "summary_sha256": "s", "final_state_sha256": "f",
            "wall_seconds": 0.0, "error": ""}

    return work


@pytest.fixture(scope="module")
def prepared_dir(tmp_path_factory, validation, context):
    root = tmp_path_factory.mktemp("focused") / "run"
    fc.prepare(fc.DEFAULT_PLAN, root, validation=validation, bases=context.bases)
    return root


def _load(root, context):
    return fc.load_prepared(root, fc.DEFAULT_PLAN, context=(context.state, context.bases))


def test_prepare_refuses_existing_directory(prepared_dir, validation, context):
    with pytest.raises(ccs.ContractError, match="never overwritten"):
        fc.prepare(fc.DEFAULT_PLAN, prepared_dir, validation=validation, bases=context.bases)


def test_resume_refuses_plan_and_provenance_mismatch(prepared_dir, context, tmp_path, monkeypatch):
    other = tmp_path / "plan.json"
    plan = json.loads(fc.DEFAULT_PLAN.read_text())
    plan["stage_c"]["trade_off_symptom_bins"] = [0.0, 0.2, 0.5]
    other.write_text(json.dumps(plan))
    with pytest.raises(ccs.ContractError, match="frozen plan"):
        fc.load_prepared(prepared_dir, other, context=(context.state, context.bases))
    hashes = fc.runtime_hashes()
    monkeypatch.setattr(fc, "runtime_hashes", lambda: {**hashes, "run_v3.py": "changed"})
    with pytest.raises(ccs.ContractError, match="runtime code changed"):
        _load(prepared_dir, context)


def test_time_stop_marks_partial_and_dispatches_nothing_after_stop(tmp_path, validation, context):
    root = tmp_path / "timed"
    fc.prepare(fc.DEFAULT_PLAN, root, validation=validation, bases=context.bases)
    prepared = _load(root, context)
    ticks = iter(range(0, 10_000_000, 400))
    result = fc.run_stage(prepared, "stage_a", workers=1, resume=False,
                          worker_fn=_synthetic_worker(context.state["targets"]),
                          executor_factory=fc.SerialExecutor, clock=lambda: float(next(ticks)))
    assert result["status"] == fc.STATUS_PARTIAL
    assert 0 < result["completed"] < 300
    assert result["last_invocation"]["not_dispatched"] == 300 - result["completed"]
    prepared = _load(root, context)
    with pytest.raises(ccs.ContractError, match="dispatch stop"):
        fc.run_stage(prepared, "stage_a", workers=1, resume=True,
                     worker_fn=_synthetic_worker(context.state["targets"]),
                     executor_factory=fc.SerialExecutor)


def test_synthetic_end_to_end_pipeline(prepared_dir, context):
    worker = _synthetic_worker(context.state["targets"])
    prepared = _load(prepared_dir, context)
    with pytest.raises(ccs.ContractError, match="COMPLETE Stage A"):
        fc.run_stage(prepared, "stage_b", workers=1, resume=False, worker_fn=worker,
                     executor_factory=fc.SerialExecutor)
    result = fc.run_stage(prepared, "stage_a", workers=1, resume=False, worker_fn=worker,
                          executor_factory=fc.SerialExecutor)
    assert result["status"] == fc.STATUS_COMPLETE and result["completed"] == 300
    prepared = _load(prepared_dir, context)
    with pytest.raises(ccs.ContractError, match="already COMPLETE"):
        fc.run_stage(prepared, "stage_a", workers=1, resume=True, worker_fn=worker,
                     executor_factory=fc.SerialExecutor)
    with pytest.raises(ccs.ContractError, match="COMPLETE Stage B"):
        fc.freeze_shortlist(prepared)
    fc.run_stage(prepared, "stage_b", workers=1, resume=False, worker_fn=worker,
                 executor_factory=fc.SerialExecutor)
    prepared = _load(prepared_dir, context)
    with pytest.raises(ccs.ContractError, match="frozen shortlist"):
        fc.run_stage(prepared, "stage_c", workers=1, resume=False, worker_fn=worker,
                     executor_factory=fc.SerialExecutor)
    frozen = fc.freeze_shortlist(prepared)
    assert frozen["n_entries"] <= 10 and frozen["trajectories"] <= 200
    prepared = _load(prepared_dir, context)
    with pytest.raises(ccs.ContractError, match="already frozen"):
        fc.freeze_shortlist(prepared)
    with pytest.raises(ccs.ContractError, match="shortlist-sha256"):
        fc.run_stage(prepared, "stage_c", workers=1, resume=False, worker_fn=worker,
                     executor_factory=fc.SerialExecutor, shortlist_sha256="wrong")
    result = fc.run_stage(prepared, "stage_c", workers=1, resume=False, worker_fn=worker,
                          executor_factory=fc.SerialExecutor,
                          shortlist_sha256=frozen["shortlist_sha256"])
    assert result["status"] == fc.STATUS_COMPLETE
    report = fc.write_report(_load(prepared_dir, context))
    assert report["design_complete"] is True
    assert report["decision"]["label"] in fc.DECISION_LABELS
    for name in ("stage_a_response_surface.csv", "discovery_candidates.csv",
                 "discovery_target_residuals.csv", "frozen_shortlist.json", "confirmation_summary.csv",
                 "confirmation_target_residuals.csv", "stage_diagnosis_decomposition.csv",
                 "late_presentation_context.csv", "post2015_stock_flow.csv", "latent_state_2025.csv",
                 "REPORT.md", "manifest.json", "frozen_plan.json", "validation.json",
                 "effective_msm_routine_paths.csv", "identifiability.json"):
        assert (prepared_dir / name).is_file(), name
    text = (prepared_dir / "REPORT.md").read_text()
    assert sum(text.rstrip().endswith(f"**{label}**") for label in fc.DECISION_LABELS) == 1
    assert len(list((prepared_dir / "figures").glob("*.png"))) <= 4
    surface = pd.read_csv(prepared_dir / "stage_a_response_surface.csv")
    assert len(surface) == 60 and surface.pre_boundary_aids_stage_dx.eq(0).all()
    confirmation = pd.read_csv(prepared_dir / "confirmation_summary.csv")
    assert (confirmation.n_seeds == 20).all()
    assert json.loads((prepared_dir / "manifest.json").read_text())["status"] == "REPORTED_COMPLETE"
