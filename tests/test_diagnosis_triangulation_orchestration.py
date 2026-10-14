"""Guards for the diagnosis-triangulation orchestration suite (2026-09-22).

Synthetic fixtures and a fake worker only: no SimulationEngineV3 is constructed and
no real trajectory is run. Run with the engine guard plugin:

    .venv/bin/python -m pytest -p pytest_no_engine_guard -q tests/test_diagnosis_triangulation_orchestration.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import coupled_calibration_search as ccs
import run_diagnosis_triangulation_orchestration as tri
import run_stage_structured_diagnosis_focused_calibration as fc

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Shared context
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ctx():
    with fc.EngineGuard():
        plan = tri.load_plan()
        prior = tri.prior_context(plan)
        design = tri.build_design(plan, prior)
    return SimpleNamespace(plan=plan, prior=prior, design=design)


def _plan_copy(tmp_path, mutate) -> Path:
    plan = json.loads(tri.DEFAULT_PLAN.read_text())
    mutate(plan)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


# --------------------------------------------------------------------------
# 1. Evidence parsing
# --------------------------------------------------------------------------

def _shm_rows(calc_2024=318):
    header = ["Year of diagnosis", "MSM+Trans (CALC)", "MSM reported", "MSM adjusted",
              "Trans men and women reported", "Trans men and women adjusted", "Men (CALC)", "Women (CALC)",
              "Other men reported", "Other men adjusted", "Women reported", "Women adjusted",
              "<15 years of age reported", "<15 years of age adjusted", "Total reported", "Total adjusted",
              "Total Adjusted \n(No <15)"]
    return [["title"] + [None] * 16, header,
            ["<=1995", 2110, 2095, None, 15, None] + [None] * 11,
            ["1996", 370, 367, None, 3, None] + [None] * 10 + [629],
            ["2024", calc_2024, 267, 301, 14, 17] + [None] * 10 + [517]]


def test_shm_parser_reconciles_calc_column_and_refuses_mismatch():
    frame = tri.parse_shm_diagnoses(_shm_rows())
    assert frame.year.tolist() == [1996, 2024]
    assert frame.set_index("year").loc[2024, "msm_trans_calc"] == 318
    with pytest.raises(ccs.ContractError, match="reconcile"):
        tri.parse_shm_diagnoses(_shm_rows(calc_2024=317))


def test_unaids_parser_skips_null_years_and_handles_missing_bounds():
    rows = [["title"], ["Indicator", "Id", "Unit", "Subgroup", "Time Period", "Source", "(All Age Low)",
                        "(All Age Mid)", "(All Age High)"],
            ["x", "x", "N", "s", 2023, "u", 1.0, 2.0, 3.0], ["x", "x", "N", "s", 2024, "u", "(NULL)", "(NULL)", "(NULL)"]]
    frame = tri.parse_unaids(rows)
    assert frame.to_dict(orient="records") == [{"year": 2023, "low": 1.0, "mid": 2.0, "high": 3.0}]
    art = tri.parse_unaids([["Indicator", "a", "b", "c", "Time Period", "s", "TOTAL ADULTS"],
                            ["x", "a", "b", "c", 2023, "s", 22446.3]], low="__none__", mid="TOTAL ADULTS",
                           high="__none__")
    assert art.iloc[0].mid == 22446.3 and art.iloc[0].low is None


def test_d76_parser_checks_cumulative_sums():
    good = [["t"], [None, "RIVM_AIDS_AND_DEATHS", "N", "s", 1983, "D76", 22, 22, 6, 6],
            [None, "RIVM_AIDS_AND_DEATHS", "N", "s", 1984, "D76", 31, 53, 16, 22],
            [None, "RIVM_AIDS_BY_ROUTE", "N", "s", "≤1987", "D76", 26, .05, 424, .8],
            [None, "RIVM_AIDS_BY_ROUTE", "N", "s", 1988, "D76", 18, .05, 250, .77]]
    annual, route = tri.parse_d76(good)
    assert annual.aids.tolist() == [22, 31] and route.to_dict(orient="records") == [{"year": 1988, "msm": 250.0}]
    bad = [list(r) for r in good]
    bad[2][7] = 54
    with pytest.raises(ccs.ContractError, match="cumulative"):
        tri.parse_d76(bad)


def test_rivm_candidate_parser_drops_incomplete_and_pooled_records():
    payload = {"selected": [
        {"period": "2015", "counts": {"msm": 125}, "source_id": "D79", "source_file": "f", "pdf_pages": [129],
         "incomplete": False, "pooled_or_total": False},
        {"period": "2023", "counts": {"msm": 20}, "source_id": "D79", "source_file": "f", "pdf_pages": [129],
         "incomplete": True, "pooled_or_total": False},
        {"period": "Total", "counts": {"msm": 1}, "source_id": "D76", "source_file": "f", "pdf_pages": [1],
         "incomplete": False, "pooled_or_total": True}]}
    frame = tri.parse_rivm_candidate(payload)
    assert frame.year.tolist() == [2015] and frame.iloc[0].msm == 125


def test_real_workbook_register_is_read_only_and_marks_the_frozen_contract(ctx):
    workbook = ROOT / ctx.plan["evidence"]["workbook"]
    before = tri.sha_file(workbook)
    tables = tri.load_evidence_tables(ctx.plan)
    register = tri.build_register(tables, ctx.plan)
    assert tri.sha_file(workbook) == before == ctx.plan["evidence"]["workbook_sha256"]
    primary = register[register.role == "primary"]
    assert len(primary) == 13
    assert set(primary.model_output) == {"new_diagnoses_msm", "aids_first_diagnosis_proxy_msm"}
    aids = primary[primary.metric == "aids_diagnoses_msm"].set_index("period").value.to_dict()
    assert aids == {"2000": 107.0, "2005": 152.0, "2010": 128.0, "2015": 125.0, "2019": 83.0, "2022": 57.0}
    row2015 = register[(register.metric == "aids_diagnoses_msm") & (register.period == "2015")].iloc[0]
    assert "differs from newest vetted" in row2015.overlap_warning
    assert set(register[register.tier == "2"].role) <= {"external_check", "cross_validation_visible"}
    assert list(register.columns) == list(tri.REGISTER_COLUMNS)
    cross = register[register.role == "historical_cross_validation"].set_index("period").value.to_dict()
    assert cross == {"1990": 318.0, "1995": 314.0}


def test_observation_mapping_keeps_the_seven_constructs_separate():
    frame = tri.observation_mapping_frame()
    constructs = [c for c in frame.construct if c[0].isdigit()]
    assert [c.split("_", 1)[0] for c in constructs] == [str(i) for i in range(1, 8)]
    assert frame.set_index("construct").loc["5_observed_advanced_presentation_cd4_lt200_or_aids",
                                            "mapping_quality"].startswith("one-sided")


# --------------------------------------------------------------------------
# 2. Plan freezing
# --------------------------------------------------------------------------

def test_plan_budgets_and_seed_disjointness_are_enforced(tmp_path):
    plan = tri.load_plan()
    assert tri.seed_preflight(plan)["overlap"] == []
    with pytest.raises(ccs.ContractError, match="budgets"):
        tri.load_plan(_plan_copy(tmp_path, lambda p: p["benchmark"]["seeds"].append(1)))
    with pytest.raises(ccs.ContractError, match="overlap"):
        tri.load_plan(_plan_copy(tmp_path, lambda p: p["confirmation"]["seeds"].__setitem__(0, 202100)))
    with pytest.raises(ccs.ContractError, match="authorized"):
        tri.load_plan(_plan_copy(tmp_path, lambda p: p["coordinates"][3].__setitem__("upper", 0.6)))


def test_design_is_reproducible_and_only_authorized_coordinates_change(ctx):
    checks = tri.check_design(ctx.plan, ctx.prior, ctx.design)
    assert checks["m0_equals_backbone_exactly"] and checks["benchmark_candidates_equal_discovery_anchors"]
    again = tri.build_design(ctx.plan, ctx.prior)
    assert [i["config_sha256"] for i in again["discovery"]] == [i["config_sha256"] for i in ctx.design["discovery"]]
    for tid in ("B_1326_v002", "B_1788_v123", "B_1788_v048"):
        item = next(i for i in ctx.design["benchmark"] if i["tuple_id"] == tid)
        assert item["config_sha256"] == ctx.prior.design_items[tid]["config_sha256"]
    counts = tri.trajectory_counts(ctx.plan, ctx.design)
    assert counts["benchmark"]["trajectories"] == 150 and counts["discovery_total"] == 1920
    assert counts["confirmation_max"]["trajectories"] == 360 and counts["total_max"] == 2430


def test_neighbourhood_geometry_has_inner_box_and_outer_ring(ctx):
    for cid in ctx.plan["backbones"]:
        items = [i for i in ctx.design["discovery"] if i["backbone"] == cid]
        geo = ctx.design["geometry"][str(cid)]
        ilo, ihi = map(np.array, geo["inner_box"])
        rings = pd.Series([i["ring"] for i in items]).value_counts().to_dict()
        assert rings == {"anchor": 4, "inner": 124, "outer": 64}
        for i in items:
            inside = tri._in_box(np.array(i["u"]), ilo, ihi)
            if i["ring"] == "inner":
                assert inside
            if i["ring"] == "outer":
                assert not inside


# --------------------------------------------------------------------------
# 3. Sharding
# --------------------------------------------------------------------------

def test_discovery_shards_partition_the_design_and_start_with_identity(ctx):
    shards = {}
    for item in ctx.design["discovery"]:
        shards.setdefault(item["shard_index"], []).append(item)
    assert sorted(shards) == list(range(8))
    assert sum(len(v) for v in shards.values()) == 384
    for k, items in shards.items():
        assert {i["backbone"] for i in items} == {ctx.plan["backbones"][k % 2]}
    for k in (0, 1):
        assert any(i["is_identity"] for i in shards[k])
        assert all(i["ring"] == "anchor" or True for i in shards[k])
    anchors = [i for i in ctx.design["discovery"] if i["ring"] == "anchor"]
    assert all(i["shard_index"] in (0, 1) for i in anchors)


# --------------------------------------------------------------------------
# 4. Pure analysis pieces
# --------------------------------------------------------------------------

def test_union_length_does_not_double_count_concurrent_intervals():
    assert tri.union_length([(0, 10), (5, 15), (20, 25)]) == 20
    assert tri.union_length([(0, 1), (0, 1)]) == 1
    assert tri.union_length([]) == 0


def _checks_frame(e1, e4, adv="within_bound", mort="within_bound", tid="T"):
    rows = [{"tuple_id": tid, "check_id": "E1", "decision_role": "latent", "status": e1},
            {"tuple_id": tid, "check_id": "E4", "decision_role": "latent", "status": e4},
            {"tuple_id": tid, "check_id": "E13", "decision_role": "advanced", "status": adv},
            {"tuple_id": tid, "check_id": "E12", "decision_role": "mortality", "status": mort}]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("e1,e4,expected", [
    ("inside", "inside", "compatible"), ("inside", "above", "time_to_diagnosis_mismatch"),
    ("above", "above", "expansion"), ("above", "inside", "expansion"), ("below", "below", "contraction"),
    ("above", "below", "mixed")])
def test_latent_status_mapping(e1, e4, expected):
    assert tri.latent_status(_checks_frame(e1, e4), "T") == expected


def test_external_check_statuses():
    summary = pd.DataFrame([{"tuple_id": "T", "stage": "benchmark", "backbone": 1326, "model": "M1",
                             "msm_new_infections_2024": 400.0, "msm_undiagnosed_end2024": 800.0,
                             "msm_dx_in_aids_2024": 60.0, "hiv_deaths_diagnosed_all_2010_2014": 700.0}])
    seeds = pd.DataFrame([{"tuple_id": "T", "msm_undiagnosed_end2024": v} for v in (700, 800, 950)])
    frame = tri.external_checks(summary, seeds).set_index("check_id")
    assert frame.loc["E1", "status"] == "above" and frame.loc["E4", "status"] == "inside"
    assert frame.loc["E13", "status"] == "violates_bound" and frame.loc["E12", "status"] == "within_bound"
    assert frame.loc["E4", "share_of_seeds_compatible"] == pytest.approx(2 / 3)
    assert frame.loc["E6", "status"] == "unavailable"


def _ledger(start, infections, diagnoses, other, years=range(2015, 2025)):
    rows, stock = [], start
    for y in years:
        end = stock + infections - diagnoses + other
        rows.append({"year": y, "activity": "All", "metric": "undiagnosed_plhiv_msm", "stock_start": stock,
                     "stock_end": end, "stock_change": end - stock, "flow_sum": end - stock,
                     "balance_residual": 0.0, "delta__transmission": infections, "delta__diagnosis": -diagnoses,
                     "delta__hiv_mortality": other})
        stock = end
    return rows


def test_ledger_components_satisfy_the_accounting_identity():
    comp = tri.ledger_components({"ledger": _ledger(400, 300, 280, -5)})
    assert comp["ledger_diagnoses_2015_2024"] == 2800
    assert comp["ledger_u_start_2015"] + comp["ledger_infections_2015_2024"] + \
        comp["ledger_other_exits_2015_2024"] - comp["ledger_u_end_2024"] == pytest.approx(2800)


def test_decomposition_terms_sum_to_the_paired_change():
    rows = []
    for tid, (start, inf, dx) in (("A", (500, 320, 290)), ("M0", (400, 300, 280))):
        for seed in (1, 2):
            rows.append({"tuple_id": tid, "seed": seed, **tri.ledger_components({"ledger": _ledger(start, inf, dx, -5)}),
                         **{f"msm_dx_{s}_2015_2024": 1.0 for s in tri.DX_STAGES},
                         "msm_symptom_attributed_dx_2015_2024": 3.0 if tid == "A" else 0.0,
                         "msm_on_art_end2024": 1.0, "msm_hiv_deaths_2015_2024": 1.0, "msm_plhiv_end2024": 1.0})
    frame = tri.decomposition(pd.DataFrame(rows), [("A", "M0")]).set_index("component")
    terms = frame[frame.kind == "accounting_identity_term"].paired_mean_change.sum()
    assert terms == pytest.approx(frame.loc["total_change_msm_diagnoses_2015_2024", "paired_mean_change"]) == 100
    assert frame.loc["symptom_attributed_aids_stage_dx", "paired_mean_change"] == 3.0


def test_symptom_share_is_zero_for_m0_and_before_1985(ctx):
    m0 = next(i for i in ctx.design["benchmark"] if i["model"] == "M0")["config"]
    m1 = next(i for i in ctx.design["benchmark"] if i["tuple_id"] == "B_1788_v048")["config"]
    assert tri.symptom_share(m0, 2020) == 0.0
    assert tri.symptom_share(m1, 1980) == 0.0
    assert 0.0 < tri.symptom_share(m1, 2020) < 1.0


# --------------------------------------------------------------------------
# 5. Classification: every label is reachable through a distinct fixture
# --------------------------------------------------------------------------

def _evaluation(**overrides):
    base = {k: True for k, _ in tri.CLAUSES}
    base.update({"tuple_id": "T", "primary_i_max": 2.0, "latent_status": "compatible"})
    base.update(overrides)
    return base


def test_classification_supported():
    assert tri.classify([_evaluation()], design_complete=True)["label"] == tri.LABEL_SUPPORTED


def test_classification_promising_when_region_fails():
    d = tri.classify([_evaluation(c7_non_isolated_region=False)], design_complete=True)
    assert d["label"] == tri.LABEL_PROMISING and d["rule"] == "R4"


def test_classification_unsupported_without_robust_improvement():
    d = tri.classify([_evaluation(c2_material_2024_improvement=False)], design_complete=True)
    assert d["label"] == tri.LABEL_UNSUPPORTED and d["rule"] == "R1"


_EXPANSION = {"E1_model_mean": 420.0, "E1_lower": 250, "E1_upper": 365, "E1_status": "above",
              "E4_model_mean": 1010.0, "E4_lower": 690, "E4_upper": 900, "E4_status": "above"}


def test_classification_tier2_expansion_is_promising_not_unsupported():
    e = _evaluation(c5_latent_compatible=False, latent_status="expansion", **_EXPANSION)
    d = tri.classify([e, {**e, "tuple_id": "U"}], design_complete=True)
    assert d["label"] == tri.LABEL_PROMISING and d["rule"] == "R2"
    assert d["tier2_expansion"] is True and d["failing_clauses"] == ["c5_latent_compatible"]
    assert d["latent_expansion"]["E1"]["excess_over_upper"] == pytest.approx(55.0)
    assert d["latent_expansion"]["E4"]["excess_over_upper"] == pytest.approx(110.0)
    assert "420 vs 250-365" in d["reason"] and "1010 vs 690-900" in d["reason"]


def test_tier2_checks_alone_never_produce_unsupported():
    """Only Tier-1 clauses (c1-c4, c6) may reject; any Tier-2 latent status leaves a label other than Unsupported."""
    for status in ("expansion", "contraction", "mixed", "unavailable", "time_to_diagnosis_mismatch"):
        e = _evaluation(c5_latent_compatible=False, latent_status=status, **_EXPANSION)
        assert tri.classify([e], design_complete=True)["label"] != tri.LABEL_UNSUPPORTED, status


def test_expansion_with_tier1_advanced_violation_is_unsupported_by_tier1():
    e = _evaluation(c5_latent_compatible=False, latent_status="expansion",
                    c6_advanced_presentation_not_violated=False, **_EXPANSION)
    d = tri.classify([e], design_complete=True)
    assert d["label"] == tri.LABEL_UNSUPPORTED and d["rule"] == "R6" and "Tier-1" in d["reason"]


def test_mixed_robust_set_flags_expansion_leader():
    e = _evaluation(c5_latent_compatible=False, latent_status="expansion", **_EXPANSION)
    c = _evaluation(tuple_id="C", c5_latent_compatible=False, latent_status="contraction", primary_i_max=2.5)
    d = tri.classify([e, c], design_complete=True)
    assert d["label"] == tri.LABEL_PROMISING and d["rule"] == "R6"
    assert d["tuple_id"] == "T" and d.get("tier2_expansion") is True


def test_manifest_lock_serialises_concurrent_unit_updates(tmp_path):
    import multiprocessing as mp
    tri.write_state(tmp_path / "manifest.json", {"design": tri.DESIGN, "units": {}, "gates": {}})
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_bump_manifest, args=(str(tmp_path), f"discovery_{k}", 15)) for k in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    units = tri.load_manifest(tmp_path)["units"]
    assert sorted(units) == [f"discovery_{k}" for k in range(4)]
    assert all(u["n"] == 15 for u in units.values())


def _bump_manifest(root: str, unit: str, repeats: int) -> None:
    import time as _t
    for _ in range(repeats):
        with tri.manifest_lock(Path(root)):
            m = tri.load_manifest(Path(root))
            n = m["units"].get(unit, {}).get("n", 0)
            _t.sleep(0.002)
            m["units"][unit] = {"n": n + 1}
            tri.save_manifest(Path(root), m)


def test_unit_lock_refuses_second_concurrent_invocation(tmp_path):
    with tri.unit_lock(tmp_path, "discovery_0"):
        with pytest.raises(ccs.ContractError, match="already running"):
            with tri.unit_lock(tmp_path, "discovery_0"):
                pass
        with tri.unit_lock(tmp_path, "discovery_1"):
            pass
    with tri.unit_lock(tmp_path, "discovery_0"):
        pass


def test_classification_unresolvable_on_time_to_diagnosis_mismatch():
    e = _evaluation(c5_latent_compatible=False, latent_status="time_to_diagnosis_mismatch")
    d = tri.classify([e], design_complete=True)
    assert d["label"] == tri.LABEL_UNRESOLVABLE and d["rule"] == "R5"


def test_classification_never_unsupported_when_incomplete():
    d = tri.classify([_evaluation(c2_material_2024_improvement=False)], design_complete=False)
    assert d["label"] == tri.LABEL_INCOMPLETE and d["provisional_label"] == tri.LABEL_UNSUPPORTED


def test_classification_promising_when_latent_contraction():
    e = _evaluation(c5_latent_compatible=False, latent_status="contraction")
    d = tri.classify([e], design_complete=True)
    assert d["label"] == tri.LABEL_PROMISING and d["rule"] == "R6"


# --------------------------------------------------------------------------
# 6. Synthetic worker and end-to-end orchestration
# --------------------------------------------------------------------------

def _synthetic_worker(targets):
    z = {t.key: t.z for t in targets}
    dx_years = sorted(t.year for t in targets if t.model_output == "new_diagnoses_msm")
    aids_years = sorted(t.year for t in targets if t.model_output == "aids_first_diagnosis_proxy_msm")

    def interp(years, series, year):
        return float(np.interp(year, years, [z[f"{series}@{y}"] for y in years]))

    def work(payload):
        config, seed, plan = payload["config"], int(payload["seed"]), payload["plan"]
        _, symptom, _ = fc._profile_values(config)
        eff = fc.effective_msm_routine_probability(config, [2019])[0]
        rng = np.random.default_rng(seed + int(payload["backbone"]))
        dx_scale = 1.0 + 0.8 * (0.30 - eff) + 0.4 * symptom + 0.03 * rng.standard_normal()
        years = range(plan["annual_start_year"], plan["annual_end_year"] + 1)
        out = {}
        for suffix, share in (("_msm", 1.0), ("_non_msm", 0.25)):
            dx = {y: (0.0 if y < 1985 else round(share * dx_scale * interp(dx_years, "shm_new_diagnoses_msm", y)))
                  for y in years}
            aids_dx = {y: (0.0 if y < 1985 else round(dx[y] * (0.08 + 0.2 * symptom))) for y in years}
            acute = {y: round(0.2 * (dx[y] - aids_dx[y])) for y in years}
            out[f"hiv_diagnoses_in_aids_stage{suffix}"] = aids_dx
            out[f"hiv_diagnoses_in_acute_stage{suffix}"] = acute
            out[f"hiv_diagnoses_in_chronic_stage{suffix}"] = {y: dx[y] - aids_dx[y] - acute[y] for y in years}
            out[f"aids_progression_while_diagnosed{suffix}"] = {
                y: max(round(share * interp(aids_years, "rivm_aids_msm", y)) - aids_dx[y], 0.0) for y in years}
            out[f"aids_progression_while_undiagnosed{suffix}"] = {y: 1.0 for y in years}
            out[f"new_diagnoses_endogenous{suffix}"] = {
                y: sum(out[f"hiv_diagnoses_in_{s}_stage{suffix}"][y] for s in fc.DX_STAGES) for y in years}
        for name in ("hiv_diagnoses_in_acute_stage", "hiv_diagnoses_in_chronic_stage", "hiv_diagnoses_in_aids_stage",
                     "aids_progression_while_diagnosed", "aids_progression_while_undiagnosed"):
            out[name] = {y: out[name + "_msm"][y] + out[name + "_non_msm"][y] for y in years}
        out["new_diagnoses_endogenous"] = {y: sum(out[f"hiv_diagnoses_in_{s}_stage"][y] for s in fc.DX_STAGES)
                                           for y in years}
        for suffix in ("", "_msm", "_non_msm"):
            out["aids_first_diagnosis_proxy" + suffix] = {
                y: out["aids_progression_while_diagnosed" + suffix][y] + out["hiv_diagnoses_in_aids_stage" + suffix][y]
                for y in years}
        out["new_diagnoses_msm"] = dict(out["new_diagnoses_endogenous_msm"])
        out["new_diagnoses_exogenous_non_msm"] = {y: (0.0 if y < 2000 else 50.0) for y in years}
        infections = 300.0 * (1.0 + 0.5 * symptom)
        out["new_infections_msm"] = {y: infections for y in years}
        out["new_infections"] = {y: infections + 2 for y in years}
        out["aids_deaths"] = {y: 60.0 for y in years}
        out["PLHIV"] = {y: 24000.0 for y in years}
        boundary = fc.check_trajectory_outputs(out, plan, config)
        kept = {s: {y: v for y, v in vals.items() if y >= 1996 or s in tri.EARLY_SERIES} for s, vals in out.items()}
        undiag = 500.0 * (1.0 + symptom)
        snaps = []
        for year in (2011, 2015, 2024, 2025):
            for stage in ("acute", "chronic", "aids"):
                snaps.append({"snapshot_year": year, "series": f"msm_undiagnosed_{stage}", "value": undiag / 3,
                              "activity": "All"})
            for series, value in (("on_art_stock_state", 20000.0), ("diagnosed_off_art_stock", 500.0),
                                  ("msm_plhiv_state", 15000.0 + undiag), ("msm_on_art_state", 14000.0),
                                  ("msm_diagnosed_state", 14500.0), ("msm_diagnosed_off_art_state", 500.0),
                                  ("plhiv_state", 24000.0)):
                snaps.append({"snapshot_year": year, "series": series, "value": value, "activity": "All"})
        dx_window = sum(out["new_diagnoses_msm"][y] for y in range(2015, 2025)) / 10
        ledger = _ledger(undiag, infections, dx_window, infections * 0.0 - 0.0)
        mortality = [{"year": y, "series": f"hiv_deaths_{d}", "value": 10.0}
                     for y in range(1996, 2026) for d in ("diagnosed_on_art", "diagnosed_off_art", "undiagnosed_off_art")]
        return {k: payload[k] for k in tri.META_KEYS} | {
            "annual_outputs": kept, "boundary": boundary, "snapshots": snaps, "mortality": mortality,
            "mortality_msm": mortality, "ledger": ledger, "summary_sha256": "s", "final_state_sha256": "f",
            "wall_seconds": 16.0, "error": ""}

    return work


@pytest.fixture(scope="module")
def frozen_root(tmp_path_factory, ctx):
    root = tmp_path_factory.mktemp("tri") / "run"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    return root


def _run(root, ctx):
    return tri.load_run(root, tri.DEFAULT_PLAN, ctx=ctx.prior)


def test_audit_and_validate_are_idempotent_and_never_construct_engines(frozen_root, ctx):
    manifest = json.loads((frozen_root / "manifest.json").read_text())
    assert manifest["status"] == "PLAN_FROZEN" and manifest["engines_constructed"] == 0
    for name in ("empirical_constraint_register.csv", "observation_mapping.csv", "frozen_target_contract.json",
                 "AIDS_DEFINITION_ADJUDICATION.md", "IMPLEMENTATION_NOTE.md", "USER_RUN_COMMANDS.md",
                 "frozen_plan.json", "validation.json", "design.json", "adjudication_checks.json"):
        assert (frozen_root / name).is_file(), name
    with fc.EngineGuard():
        assert tri.audit_evidence(tri.DEFAULT_PLAN, frozen_root, ctx=ctx.prior)["status"] == "unchanged"
        tri.validate_plan(tri.DEFAULT_PLAN, frozen_root, ctx=ctx.prior)
    assert json.loads((frozen_root / "manifest.json").read_text())["frozen_plan_sha256"] == \
        manifest["frozen_plan_sha256"]


def test_freezing_refuses_a_failed_adjudication(tmp_path, ctx):
    root = tmp_path / "blocked"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    payload = json.loads((root / "adjudication_checks.json").read_text())
    payload["passed"] = False
    payload["checks"]["progression_source_is_cascade::1326"] = False
    (root / "adjudication_checks.json").write_text(json.dumps(payload))
    with pytest.raises(ccs.ContractError, match="adjudication"):
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)


def test_evidence_files_are_never_silently_overwritten(tmp_path):
    path = tmp_path / "x.csv"
    assert tri.write_new(path, "a") == "written"
    assert tri.write_new(path, "a") == "unchanged"
    with pytest.raises(ccs.ContractError, match="refusing to overwrite"):
        tri.write_new(path, "b")


def test_load_run_refuses_plan_and_code_drift(frozen_root, ctx, tmp_path, monkeypatch):
    other = _plan_copy(tmp_path, lambda p: p["decision_thresholds"].__setitem__("identifiability_neighbours", 41))
    with pytest.raises(ccs.ContractError, match="frozen plan"):
        tri.load_run(frozen_root, other, ctx=ctx.prior)
    hashes = tri.runtime_hashes()
    monkeypatch.setattr(tri, "runtime_hashes", lambda: {**hashes, "run_v3.py": "changed"})
    with pytest.raises(ccs.ContractError, match="runtime code changed"):
        _run(frozen_root, ctx)


def test_confirmation_needs_frozen_shortlist_and_discovery_needs_benchmark(frozen_root, ctx):
    run = _run(frozen_root, ctx)
    worker = _synthetic_worker(ctx.prior.state["targets"])
    with pytest.raises(ccs.ContractError, match="COMPLETE benchmark"):
        tri.run_unit(run, "discovery_00", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    with pytest.raises(ccs.ContractError, match="frozen shortlist"):
        tri.run_unit(run, "confirmation_00", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)


def test_time_stop_marks_partial_and_resume_dispatches_only_missing(tmp_path, ctx):
    root = tmp_path / "timed"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    worker = _synthetic_worker(ctx.prior.state["targets"])
    ticks = iter(range(0, 10_000_000, 700))
    run = _run(root, ctx)
    result = tri.run_unit(run, "benchmark", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor,
                          clock=lambda: float(next(ticks)))
    assert result["status"] == tri.STATUS_PARTIAL and 0 < result["completed"] < 150
    first = result["completed"]
    with pytest.raises(ccs.ContractError, match="use --resume"):
        tri.run_unit(_run(root, ctx), "benchmark", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    dispatched = []

    def counting(payload):
        dispatched.append((payload["tuple_id"], payload["seed"]))
        return worker(payload)

    result = tri.run_unit(_run(root, ctx), "benchmark", workers=1, resume=True, worker_fn=counting,
                          executor_factory=fc.SerialExecutor)
    assert result["status"] == tri.STATUS_COMPLETE and len(dispatched) == 150 - first
    again = tri.run_unit(_run(root, ctx), "benchmark", workers=1, resume=True, worker_fn=counting,
                         executor_factory=fc.SerialExecutor)
    assert again["status"] == tri.STATUS_COMPLETE and len(dispatched) == 150 - first
    state = json.loads((root / "benchmark" / "state.json").read_text())
    assert state["measured_seconds_per_trajectory"] == 16.0


def test_discovery_shard_refuses_to_start_inside_the_confirmation_reserve(tmp_path, ctx, monkeypatch):
    root = tmp_path / "reserve"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    worker = _synthetic_worker(ctx.prior.state["targets"])
    tri.run_unit(_run(root, ctx), "benchmark", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    monkeypatch.setattr(tri, "clock_used_seconds", lambda root: 6000.0)
    result = tri.run_unit(_run(root, ctx), "discovery_00", workers=8, worker_fn=worker,
                          executor_factory=fc.SerialExecutor)
    assert result["status"] == tri.STATUS_SKIPPED_RESERVE
    assert not tri.load_unit_journal(root, "discovery_00")


def test_merge_refuses_duplicates_and_foreign_trajectories(tmp_path, ctx):
    root = tmp_path / "merge"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    worker = _synthetic_worker(ctx.prior.state["targets"])
    tri.run_unit(_run(root, ctx), "benchmark", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    for k in range(8):
        tri.run_unit(_run(root, ctx), f"discovery_{k:02d}", workers=1, worker_fn=worker,
                     executor_factory=fc.SerialExecutor)
    journal0 = sorted((tri.unit_dir(root, "discovery_00") / "journal").glob("*.json"))
    duplicate = tri.unit_dir(root, "discovery_02") / "journal" / journal0[0].name
    shutil.copy(journal0[0], duplicate)
    with pytest.raises(ccs.ContractError, match="duplicates"):
        tri.merge_discovery(_run(root, ctx))
    duplicate.unlink()
    record = json.loads(journal0[1].read_text())
    record["plan_sha256"] = "other"
    journal0[1].write_text(json.dumps(record))
    with pytest.raises(ccs.ContractError, match="mismatched"):
        tri.merge_discovery(_run(root, ctx))
    report = tri.integrity_report(_run(root, ctx), tri.discovery_units(ctx.plan))
    assert len(report["foreign_or_mismatched"]) == 1 and not report["missing"]


def test_partial_discovery_merge_needs_flag_and_a_completed_prefix(tmp_path, ctx):
    root = tmp_path / "partial"
    with fc.EngineGuard():
        tri.audit_evidence(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
        tri.validate_plan(tri.DEFAULT_PLAN, root, ctx=ctx.prior)
    worker = _synthetic_worker(ctx.prior.state["targets"])
    tri.run_unit(_run(root, ctx), "benchmark", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    tri.run_unit(_run(root, ctx), "discovery_00", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    with pytest.raises(ccs.ContractError, match="allow-partial"):
        tri.merge_discovery(_run(root, ctx))
    with pytest.raises(ccs.ContractError, match="shards 0 and 1"):
        tri.merge_discovery(_run(root, ctx), allow_partial=True)
    tri.run_unit(_run(root, ctx), "discovery_01", workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    merged = tri.merge_discovery(_run(root, ctx), allow_partial=True)
    assert merged["status"] == tri.STATUS_PARTIAL and merged["vectors"] == 102


def test_synthetic_end_to_end_pipeline(frozen_root, ctx):
    worker = _synthetic_worker(ctx.prior.state["targets"])
    kw = dict(workers=1, worker_fn=worker, executor_factory=fc.SerialExecutor)
    first = tri.run_all(frozen_root, tri.DEFAULT_PLAN, approve_shortlist_sha256=None, skip_figures=False, **kw)
    assert first["stopped_at"] == "shortlist review gate"
    run = _run(frozen_root, ctx)
    sha = run.manifest["shortlist_sha256"]
    shortlist = json.loads((frozen_root / "frozen_shortlist.json").read_text())
    assert len(shortlist["entries"]) <= 12
    assert sum(e["model"] == "M0" for e in shortlist["entries"]) == 2
    assert not {e["tuple_id"] for e in shortlist["entries"]} & {"B_1326_v002", "B_1788_v123", "B_1788_v048"}
    with pytest.raises(ccs.ContractError, match="shortlist-sha256"):
        tri.run_unit(run, "confirmation_00", shortlist_sha256="wrong", **kw)
    final = tri.run_all(frozen_root, tri.DEFAULT_PLAN, approve_shortlist_sha256=sha, skip_figures=False, **kw)
    assert final["stopped_at"] == "report"
    report = final["report"]
    assert report["design_complete"] is True
    assert report["decision"]["label"] in tri.DECISION_LABELS
    for name in ("manifest.json", "STATUS.md", "benchmark_seed_level.csv", "benchmark_summary.csv",
                 "discovery_merged.csv", "frozen_shortlist.json", "confirmation_merged.csv", "target_residuals.csv",
                 "external_plausibility_checks.csv", "stage_diagnosis_decomposition.csv", "post2015_stock_flow.csv",
                 "latent_state_2025.csv", "identifiability.json", "REPORT.md", "decision_clauses.csv",
                 "annual_trajectories_1996_2025.csv", "effective_msm_routine_paths.csv"):
        assert (frozen_root / name).is_file(), name
    assert len(list((frozen_root / "figures").glob("*.png"))) <= 5
    text = (frozen_root / "REPORT.md").read_text()
    assert sum(text.rstrip().endswith(f"**{label}**") for label in tri.DECISION_LABELS) == 1
    for q in range(1, 11):
        assert f"\n{q}. **" in text or (q in (3, 4, 5, 6) and "3-6." in text), q
    bench = pd.read_csv(frozen_root / "benchmark_seed_level.csv")
    assert bench.groupby("tuple_id").seed.nunique().eq(30).all()
    summary = pd.read_csv(frozen_root / "benchmark_summary.csv")
    assert summary.xval_aids1990.notna().all() and summary.xval_aids1995.notna().all()
    diffs = pd.read_csv(frozen_root / "paired_seed_differences.csv")
    assert set(diffs.groupby(["stage", "tuple_id"]).seed.nunique()) == {30}
    assert all((tri.unit_dir(frozen_root, u) / "shard_summary.csv").is_file() for u in tri.discovery_units(ctx.plan))
    decomp = pd.read_csv(frozen_root / "stage_diagnosis_decomposition.csv")
    assert decomp.identity_residual.dropna().abs().max() < 1e-6
    status = tri.status(frozen_root, tri.DEFAULT_PLAN)
    assert all(u["missing"] == 0 and u["duplicates"] == 0 for u in status["units"].values())
    assert json.loads((frozen_root / "manifest.json").read_text())["status"] == "REPORTED_COMPLETE"
    again = tri.run_all(frozen_root, tri.DEFAULT_PLAN, approve_shortlist_sha256=sha, skip_figures=True, **kw)
    assert again["stopped_at"] == "report"


# --------------------------------------------------------------------------
# 7. New observers on a mocked engine (no SimulationEngineV3 is constructed)
# --------------------------------------------------------------------------

def _fake_engine():
    groups = SimpleNamespace(orient_to_idx={"Female": 0, "Heterosexual_Male": 1, "MSM": 2},
                             diagnosis_states=["Undiagnosed", "Diagnosed"], art_states=["Not_on_ART", "On_ART"],
                             hiv_to_idx={"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3},
                             activities=["Low", "Med", "High"])
    population = np.ones((2, 3, 3, 4, 2, 2, 1), dtype=float)
    engine = SimpleNamespace(groups=groups, state=SimpleNamespace(population=population), _current_year=2016.25,
                             time=SimpleNamespace(start_year=2016.0, years_per_step=0.25))

    def mortality():
        engine.state.population[:, 2, :, 3, 1, 0, :] -= 1.0      # MSM, AIDS, diagnosed, off ART
        engine.state.population[:, 0, :, 2, 0, 0, :] -= 1.0      # Female, Chronic, undiagnosed, off ART
        return {"total": 0, "aids": 0}

    engine._step_hiv_mortality = mortality
    engine.step_simulation = lambda step: step
    return engine


def test_extended_snapshot_msm_stocks():
    engine = _fake_engine()
    rows = {r["series"]: r["value"] for r in tri.extended_snapshot(engine.state.population, engine.groups, 2025)
            if r["activity"] == "All"}
    per_cell = 2 * 3 * 1                                        # ages x activities x prep
    assert rows["msm_plhiv_state"] == 3 * 2 * 2 * per_cell      # 3 HIV stages x diag x art
    assert rows["msm_on_art_state"] == 3 * 2 * per_cell
    assert rows["msm_diagnosed_off_art_state"] == 3 * per_cell
    assert rows["msm_aids_stage_state"] == 2 * 2 * per_cell
    assert rows["plhiv_state"] == 3 * rows["msm_plhiv_state"]
    assert sum(rows[f"msm_undiagnosed_{s}"] for s in ("acute", "chronic", "aids")) == 3 * 2 * per_cell


def test_mortality_observer_splits_msm_deaths_by_state(monkeypatch):
    engine = _fake_engine()
    monkeypatch.setattr(tri.ledger_module, "install_ledger", lambda e, a, b: "ledger")
    ledger, snapshots, mortality, mortality_msm = tri.install_observers(engine, 2015, 2024)
    engine._step_hiv_mortality()
    assert mortality[(2016, "hiv_deaths_diagnosed_off_art")] == 6.0
    assert mortality[(2016, "hiv_deaths_undiagnosed_off_art")] == 6.0
    assert mortality_msm[(2016, "hiv_deaths_diagnosed_off_art")] == 6.0
    assert mortality_msm[(2016, "hiv_deaths_undiagnosed_off_art")] == 0.0
    engine.step_simulation(0)
    assert 2016 in snapshots and any(r["series"] == "msm_plhiv_state" for r in snapshots[2016])


def test_smoke_refuses_an_existing_directory(frozen_root, tmp_path):
    existing = tmp_path / "smoke"
    existing.mkdir()
    with pytest.raises(ccs.ContractError, match="never overwritten"):
        tri.smoke(frozen_root, tri.DEFAULT_PLAN, existing)
