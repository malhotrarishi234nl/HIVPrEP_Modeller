"""Candidate-baseline review of the stage-diagnosis mechanism (analysis only).

Brief: ``TASK_BRIEF_STAGE_DIAGNOSIS_CANDIDATE_BASELINE_REVIEW_2026-09-22.md``.

Compares the candidate ``D_1788_v109`` (routine + AIDS-symptom diagnosis) with its
matched legacy comparator ``D_1788_v000`` (same backbone 1788, same 30 confirmation
seeds) using ONLY saved outputs of the completed orchestration run
``outputs/calibration/diagnosis_triangulation_orchestration_20260922``.

What this script does and does not do:

* It never constructs a simulation engine and never runs a trajectory. The frozen
  design is rebuilt statically (configuration dictionaries only) so that the
  candidate and legacy configurations and their hashes can be verified and exported.
* Every input is read-only. All products are written under ``--output-dir``; the
  script refuses to write inside the orchestration root.
* Seed-level values are read from the per-trajectory journals (the primary record),
  then checked against the orchestration's derived CSVs.

Timing conventions (from ``run_diagnosis_triangulation_orchestration.install_observers``):
a snapshot labelled Y is the population at the START of calendar year Y, i.e. the end
of year Y-1. Stocks below are reported as END of year Y (snapshot Y+1). Flows are
calendar-year totals.

Usage (from ``hiv-prep-model/``)::

    .venv/bin/python run_stage_diagnosis_candidate_baseline_review.py
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

import run_diagnosis_triangulation_orchestration as orch
import run_stage_structured_diagnosis_focused_calibration as fc
import coupled_calibration_search as ccs

ROOT = Path(__file__).resolve().parent
ORCH = ROOT / "outputs/calibration/diagnosis_triangulation_orchestration_20260922"
DEFAULT_OUT = ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
BRIEF = "TASK_BRIEF_STAGE_DIAGNOSIS_CANDIDATE_BASELINE_REVIEW_2026-09-22.md"

CAND = "D_1788_v109"
LEG = "D_1788_v000"
ARMS = (CAND, LEG)
BACKBONE = 1788

MILESTONE_YEARS = (1996, 2000, 2004, 2010, 2015, 2019, 2020, 2023, 2024, 2025)

#: Reference values stated in the brief (rounded as printed there).
REFERENCE = {
    CAND: {
        "n_seeds": 30, "primary_i_max": 2.634565, "binding_target": "rivm_aids_msm@2022",
        "dx2024": 305.67, "msm_new_infections_2024": 292.97, "msm_undiagnosed_end2024": 915.93,
        "msm_plhiv_end2024": 12644.47, "msm_on_art_end2024": 11589.37, "msm_dx_in_aids_2024": 36.13,
        "msm_fraction_dx_in_aids_2024": 0.1180, "msm_hiv_deaths_2015_2024": 1202.73,
    },
    LEG: {
        "dx2024": 195.47, "msm_new_infections_2024": 184.97, "msm_undiagnosed_end2024": 424.97,
        "msm_plhiv_end2024": 11601.13, "msm_on_art_end2024": 11064.80, "msm_hiv_deaths_2015_2024": 991.00,
    },
}
EXPECTED_COORDINATES = {"msm_diag_mult_1996": 0.820236, "msm_diag_mult_2004": 0.388836,
                        "msm_diag_mult_2019": 0.525189, "aids_symptom_annual_probability": 0.136453}
TIER2_E4 = (690.0, 900.0)

DEATH_STATES = ("undiagnosed_off_art", "diagnosed_off_art", "diagnosed_on_art", "undiagnosed_on_art")


# ==========================================================================
# Small utilities
# ==========================================================================

def sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_text(out: Path, name: str, text: str) -> Path:
    path = out / name
    if ORCH.resolve() in path.resolve().parents:
        raise RuntimeError("refusing to write inside the orchestration root")
    path.write_text(text)
    return path


def write_csv(out: Path, name: str, frame: pd.DataFrame) -> Path:
    path = out / name
    if ORCH.resolve() in path.resolve().parents:
        raise RuntimeError("refusing to write inside the orchestration root")
    frame.to_csv(path, index=False)
    return path


def paired_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """Mean paired difference a-b with a t-based 95% CI (df = n-1)."""
    d = np.asarray(a, float) - np.asarray(b, float)
    n = len(d)
    mean = float(d.mean())
    if n < 2:
        return {"paired_diff_mean": mean, "paired_ci95_low": np.nan, "paired_ci95_high": np.nan,
                "paired_diff_sd": np.nan, "n_pairs": n}
    sd = float(d.std(ddof=1))
    half = float(stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n))
    return {"paired_diff_mean": mean, "paired_ci95_low": mean - half, "paired_ci95_high": mean + half,
            "paired_diff_sd": sd, "n_pairs": n}


# ==========================================================================
# Inputs
# ==========================================================================

def load_journals() -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {a: [] for a in ARMS}
    for path in sorted(glob.glob(str(ORCH / "confirmation/shard_*/journal/*.json"))):
        name = Path(path).name
        tuple_id = name.split("__seed_")[0]
        if tuple_id in by:
            item = json.loads(Path(path).read_text())
            item["_path"] = str(Path(path).relative_to(ROOT))
            by[tuple_id].append(item)
    for arm in ARMS:
        by[arm].sort(key=lambda i: int(i["seed"]))
    return by


def rebuild_configs() -> tuple[dict, dict, dict]:
    """(plan, design items by id, prior context) rebuilt statically; no engine."""
    plan = orch.load_plan(orch.DEFAULT_PLAN)
    ctx = orch.prior_context(plan)
    design = orch.build_design(plan, ctx)
    if orch.sha_json(orch.design_record(design)) != orch.sha_json(orch._read(ORCH / "design.json")):
        raise RuntimeError("rebuilt design differs from the frozen design.json")
    items = {i["tuple_id"]: i for i in design["discovery"]}
    return plan, items, ctx


# ==========================================================================
# Seed-level long frame from the journals
# ==========================================================================

def _snap_rows(item: dict) -> dict[tuple[str, str, int], float]:
    out = {}
    for r in item["snapshots"]:
        out[(r["series"], r["activity"], int(r["snapshot_year"]))] = float(r["value"])
    return out


def seed_long(item: dict, config: dict) -> list[dict]:
    """All annual quantities for one trajectory, with provenance labels.

    basis: direct_flow | direct_stock | exact_identity | accounting_attribution |
           approx_person_years
    """
    rows: list[dict] = []
    ao = item["annual_outputs"]

    def flow(name: str, year: int) -> float:
        v = ao.get(name, {})
        return float(v.get(str(year), v.get(year, np.nan)))

    def add(series, year, value, basis, unit="count", population="", kind="flow"):
        rows.append({"series": series, "year": int(year), "value": float(value), "basis": basis,
                     "unit": unit, "population": population, "kind": kind})

    snaps = _snap_rows(item)

    def snap(series, year, activity="All"):
        return snaps.get((series, activity, year), np.nan)

    mort = {(int(r["year"]), r["series"]): float(r["value"]) for r in item["mortality"]}
    mort_msm = {(int(r["year"]), r["series"]): float(r["value"]) for r in item["mortality_msm"]}

    years = range(1996, 2026)
    for y in years:
        # ---- flows: infections and diagnoses
        add("new_infections_total", y, flow("new_infections", y), "direct_flow", population="all")
        add("new_infections_msm", y, flow("new_infections_msm", y), "direct_flow", population="MSM")
        add("new_infections_non_msm", y, flow("new_infections_non_msm", y), "direct_flow", population="non-MSM")
        dx_tot, dx_msm = flow("new_diagnoses", y), flow("new_diagnoses_msm", y)
        add("new_diagnoses_total", y, dx_tot, "direct_flow", population="all")
        add("new_diagnoses_msm", y, dx_msm, "direct_flow", population="MSM")
        add("new_diagnoses_non_msm", y, dx_tot - dx_msm, "exact_identity", population="non-MSM")
        add("new_diagnoses_non_msm_endogenous", y, flow("new_diagnoses_endogenous_non_msm", y), "direct_flow",
            population="non-MSM")
        add("new_diagnoses_non_msm_exogenous_C1", y, flow("new_diagnoses_exogenous_non_msm", y), "direct_flow",
            population="non-MSM")
        for stage in ("acute", "chronic", "aids"):
            add(f"msm_dx_in_{stage}_stage", y, flow(f"hiv_diagnoses_in_{stage}_stage_msm", y), "direct_flow",
                population="MSM")
            add(f"non_msm_dx_in_{stage}_stage", y, flow(f"hiv_diagnoses_in_{stage}_stage_non_msm", y),
                "direct_flow", population="non-MSM")
        aids_dx = flow("hiv_diagnoses_in_aids_stage_msm", y)
        add("msm_fraction_dx_in_aids_stage", y, aids_dx / dx_msm if dx_msm else np.nan, "exact_identity",
            unit="fraction", population="MSM")
        share = orch.symptom_share(config, y)
        add("msm_dx_symptom_attributed", y, aids_dx * share, "accounting_attribution", population="MSM")
        add("non_msm_dx_symptom_attributed", y,
            flow("hiv_diagnoses_in_aids_stage_non_msm", y) * _symptom_share_non_msm(config, y),
            "accounting_attribution", population="non-MSM")
        # ---- AIDS progression and the established proxy
        for suffix, pop in (("_msm", "MSM"), ("_non_msm", "non-MSM"), ("", "all")):
            add(f"aids_progression_undiagnosed{suffix or '_total'}", y,
                flow(f"aids_progression_while_undiagnosed{suffix}", y), "direct_flow", population=pop)
            add(f"aids_progression_diagnosed_off_art{suffix or '_total'}", y,
                flow(f"aids_progression_while_diagnosed_off_art{suffix}", y), "direct_flow", population=pop)
            add(f"aids_progression_diagnosed_on_art{suffix or '_total'}", y,
                flow(f"aids_progression_while_diagnosed_on_art{suffix}", y), "direct_flow", population=pop)
        add("aids_proxy_msm", y, flow("aids_first_diagnosis_proxy_msm", y), "direct_flow", population="MSM")
        add("aids_proxy_msm_component_progression_while_diagnosed", y,
            flow("aids_progression_while_diagnosed_msm", y), "direct_flow", population="MSM")
        add("aids_proxy_msm_component_dx_in_aids_stage", y, aids_dx, "direct_flow", population="MSM")
        add("aids_proxy_non_msm", y, flow("aids_first_diagnosis_proxy_non_msm", y), "direct_flow",
            population="non-MSM")
        # ---- deaths by diagnosis/ART state (observer on the HIV-mortality step)
        for st in DEATH_STATES:
            t = mort.get((y, f"hiv_deaths_{st}"), np.nan)
            m = mort_msm.get((y, f"hiv_deaths_{st}"), np.nan)
            add(f"hiv_deaths_total_{st}", y, t, "direct_flow", population="all")
            add(f"hiv_deaths_msm_{st}", y, m, "direct_flow", population="MSM")
            add(f"hiv_deaths_non_msm_{st}", y, t - m, "exact_identity", population="non-MSM")
        tot = sum(mort.get((y, f"hiv_deaths_{st}"), 0.0) for st in DEATH_STATES)
        msm = sum(mort_msm.get((y, f"hiv_deaths_{st}"), 0.0) for st in DEATH_STATES)
        add("hiv_deaths_total", y, tot, "exact_identity", population="all")
        add("hiv_deaths_msm", y, msm, "exact_identity", population="MSM")
        add("hiv_deaths_non_msm", y, tot - msm, "exact_identity", population="non-MSM")
        add("hiv_deaths_in_aids_stage_total", y, flow("aids_deaths", y), "direct_flow", population="all")
        # ---- ART, PrEP, person-years
        add("art_initiations_total", y, flow("art_initiations", y), "direct_flow", population="all")
        for name in ("msm_on_oral_end", "msm_on_oral_mean", "prep_oral_initiations", "prep_oral_dropouts",
                     "prep_oral_annual_unique_approx", "prep_lena_initiations", "prep_lena_dropouts"):
            add(name, y, flow(name, y), "direct_flow", population="MSM" if "msm" in name else "all")
        for art in ("off", "on"):
            add(f"msm_chronic_diagnosed_{art}_art_person_years", y,
                flow(f"chronic_diagnosed_{art}_art_person_years_msm", y), "direct_flow", unit="person-years",
                population="MSM")
        add("plhiv_output_annual", y, flow("PLHIV", y), "direct_flow", population="all")
        # ---- stocks at END of year y (= snapshot y+1)
        s = y + 1
        und = {st: snap(f"msm_undiagnosed_{st}", s) for st in ("acute", "chronic", "aids")}
        und_all = sum(und.values())
        plhiv, mplhiv = snap("plhiv_state", s), snap("msm_plhiv_state", s)
        mdx, mdx_off, mart = snap("msm_diagnosed_state", s), snap("msm_diagnosed_off_art_state", s), \
            snap("msm_on_art_state", s)
        maids = snap("msm_aids_stage_state", s)
        art_tot, dxoff_tot = snap("on_art_stock_state", s), snap("diagnosed_off_art_stock", s)
        stock = lambda n, v, basis="direct_stock", pop="MSM", unit="count": add(  # noqa: E731
            n, y, v, basis, unit=unit, population=pop, kind="stock_end")
        stock("plhiv_total_end", plhiv, pop="all")
        stock("plhiv_msm_end", mplhiv)
        stock("plhiv_non_msm_end", plhiv - mplhiv, "exact_identity", pop="non-MSM")
        stock("msm_undiagnosed_end", und_all)
        for st, v in und.items():
            stock(f"msm_undiagnosed_{st}_end", v)
        for act in ("Low", "Medium", "High"):
            stock(f"msm_undiagnosed_end_{act.lower()}_activity",
                  sum(snap(f"msm_undiagnosed_{st}", s, act) for st in ("acute", "chronic", "aids")))
        stock("msm_diagnosed_end", mdx)
        stock("msm_diagnosed_off_art_end", mdx_off)
        stock("msm_on_art_end", mart)
        stock("msm_undiagnosed_on_art_end", und_all - (mplhiv - mart - mdx_off), "exact_identity")
        stock("msm_aids_stage_end", maids)
        stock("msm_diagnosed_aids_stage_end", maids - und["aids"], "exact_identity")
        stock("msm_art_coverage_of_diagnosed_end", mart / mdx if mdx else np.nan, "exact_identity",
              unit="fraction")
        stock("msm_undiagnosed_fraction_end", und_all / mplhiv if mplhiv else np.nan, "exact_identity",
              unit="fraction")
        stock("on_art_total_end", art_tot, pop="all")
        stock("on_art_non_msm_end", art_tot - mart, "exact_identity", pop="non-MSM")
        stock("diagnosed_off_art_total_end", dxoff_tot, pop="all")
        stock("diagnosed_off_art_non_msm_end", dxoff_tot - mdx_off, "exact_identity", pop="non-MSM")
        stock("art_coverage_of_diagnosed_total_end", art_tot / (art_tot + dxoff_tot), "exact_identity",
              pop="all", unit="fraction")
        stock("adult_population_end", snap("adult_population_state", s), pop="all")
        # ---- approximate person-years (trapezoid of start- and end-of-year stocks)
        for series, name in (("msm_plhiv_state", "msm_plhiv"), ("plhiv_state", "plhiv_total"),
                             ("msm_on_art_state", "msm_on_art"),
                             ("msm_diagnosed_off_art_state", "msm_diagnosed_off_art"),
                             ("msm_aids_stage_state", "msm_aids_stage")):
            py = 0.5 * (snap(series, y) + snap(series, s))
            add(f"{name}_person_years_approx", y, py, "approx_person_years", unit="person-years",
                population="all" if name == "plhiv_total" else "MSM")
        for st in ("acute", "chronic", "aids"):
            py = 0.5 * (snap(f"msm_undiagnosed_{st}", y) + snap(f"msm_undiagnosed_{st}", s))
            add(f"msm_undiagnosed_{st}_person_years_approx", y, py, "approx_person_years", unit="person-years",
                population="MSM")
        py_u = sum(0.5 * (snap(f"msm_undiagnosed_{st}", y) + snap(f"msm_undiagnosed_{st}", s))
                   for st in ("acute", "chronic", "aids"))
        add("msm_undiagnosed_person_years_approx", y, py_u, "approx_person_years", unit="person-years",
            population="MSM")
    return rows


def _symptom_share_non_msm(config: dict, year: int) -> float:
    """Expected symptom share of non-MSM AIDS-stage diagnoses (base schedule, no MSM multiplier)."""
    _, symptom, start = fc._profile_values(config)
    if symptom <= 0.0:
        return 0.0
    base = fc._schedule(fc._get(config, fc.PROFILE_PATH[:-1])["schedule"], 0.0)
    shares = []
    for q in range(4):
        t = year + 0.25 * q
        if start is not None and t < start:
            shares.append(0.0)
            continue
        p = float(np.clip(float(base.get_value(t)), 0.0, 1.0))
        routine = 0.0 if p <= 0 else (math.inf if p >= 1 else -math.log1p(-p))
        s = -math.log1p(-symptom)
        shares.append(0.0 if not math.isfinite(routine + s) else s / (routine + s))
    return float(np.mean(shares))


def build_seed_frame(journals: Mapping[str, list[dict]], configs: Mapping[str, dict]) -> pd.DataFrame:
    frames = []
    for arm in ARMS:
        for item in journals[arm]:
            f = pd.DataFrame(seed_long(item, configs[arm]))
            f.insert(0, "seed", int(item["seed"]))
            f.insert(0, "tuple_id", arm)
            frames.append(f)
    return pd.concat(frames, ignore_index=True)


# ==========================================================================
# Scalar (headline) metrics from journals, computed independently
# ==========================================================================

def scalar_metrics(item: dict, targets: Sequence) -> dict:
    ao = item["annual_outputs"]
    f = lambda n, y: float(ao[n][str(y)])  # noqa: E731
    snaps = _snap_rows(item)
    snap = lambda s, y: snaps[(s, "All", y)]  # noqa: E731
    mort_msm = {(int(r["year"]), r["series"]): float(r["value"]) for r in item["mortality_msm"]}
    mort = {(int(r["year"]), r["series"]): float(r["value"]) for r in item["mortality"]}
    und = lambda y: sum(snap(f"msm_undiagnosed_{st}", y) for st in ("acute", "chronic", "aids"))  # noqa: E731
    stage96 = {st: sum(f(f"hiv_diagnoses_in_{st}_stage_msm", y) for y in range(1996, 2015))
               for st in ("acute", "chronic", "aids")}
    out = {
        "dx2024": f("new_diagnoses_msm", 2024),
        "msm_new_infections_2010": f("new_infections_msm", 2010),
        "msm_new_infections_2020": f("new_infections_msm", 2020),
        "msm_new_infections_2024": f("new_infections_msm", 2024),
        "msm_undiagnosed_end2010": und(2011),
        "msm_undiagnosed_end2024": und(2025),
        "msm_plhiv_end2024": snap("msm_plhiv_state", 2025),
        "msm_on_art_end2024": snap("msm_on_art_state", 2025),
        "msm_diagnosed_end2024": snap("msm_diagnosed_state", 2025),
        "msm_art_coverage_of_diagnosed_end2024": snap("msm_on_art_state", 2025) / snap("msm_diagnosed_state", 2025),
        "msm_dx_in_aids_2024": f("hiv_diagnoses_in_aids_stage_msm", 2024),
        "msm_fraction_dx_in_aids_2024": f("hiv_diagnoses_in_aids_stage_msm", 2024) / f("new_diagnoses_msm", 2024),
        "msm_fraction_dx_in_aids_1996_2014": stage96["aids"] / sum(stage96.values()),
        "msm_hiv_deaths_2015_2024": sum(v for (y, _), v in mort_msm.items() if 2015 <= y <= 2024),
        "plhiv_all_2023": snap("plhiv_state", 2024),
        "on_art_all_2023": snap("on_art_stock_state", 2024),
        "new_infections_all_2023": f("new_infections", 2023),
        "aids_deaths_all_2023": f("aids_deaths", 2023),
        "hiv_deaths_all_2023": sum(v for (y, _), v in mort.items() if y == 2023),
        "hiv_deaths_diagnosed_all_2010_2014": sum(v for (y, s), v in mort.items()
                                                  if 2010 <= y <= 2014 and s.startswith("hiv_deaths_diagnosed_")),
    }
    for t in targets:
        out[f"target::{t.key}"] = f(t.model_output, t.year)
    return out


def implausibility_table(seed_scalars: pd.DataFrame, targets: Sequence, arm: str) -> pd.DataFrame:
    """Independent recomputation of the frozen 13-target contract (predictive variance s^2(1+1/K))."""
    x = seed_scalars[seed_scalars.tuple_id == arm]
    k = len(x)
    rows = []
    for t in targets:
        v = x[f"target::{t.key}"].to_numpy(float)
        mean, s2 = float(v.mean()), float(v.var(ddof=1))
        process = s2 * (1.0 + 1.0 / k)
        b10 = float(t.v_obs_variance + t.v_code_variance + t.extra_variance + t.v_disc_variance)
        b20 = float(t.v_obs_variance + t.v_code_variance + t.extra_variance + 4.0 * t.v_disc_variance)
        rows.append({
            "tuple_id": arm, "target": t.key, "group": t.group, "year": t.year, "model_output": t.model_output,
            "observed": float(t.z), "model_mean": mean, "model_sd": math.sqrt(s2), "n_seeds": k,
            "signed_residual": mean - float(t.z), "relative_residual": (mean - float(t.z)) / float(t.z),
            "v_obs": t.v_obs_variance, "v_disc_10pct": t.v_disc_variance, "v_process_predictive": process,
            "total_sd_primary": math.sqrt(b10 + process), "total_sd_sensitivity": math.sqrt(b20 + process),
            "primary_implausibility": abs(mean - t.z) / math.sqrt(b10 + process),
            "sensitivity_implausibility": abs(mean - t.z) / math.sqrt(b20 + process),
        })
    f = pd.DataFrame(rows)
    f["primary_pass_I_le_3"] = f.primary_implausibility <= 3.0
    f["rank_most_binding"] = f.primary_implausibility.rank(ascending=False, method="first").astype(int)
    return f.sort_values("rank_most_binding")


# ==========================================================================
# Integrity audit (Phase 1)
# ==========================================================================

class Audit:
    def __init__(self):
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "", severity: str = "stop") -> bool:
        self.rows.append({"check": name, "pass": bool(ok), "severity": severity, "detail": detail})
        return ok

    @property
    def blocking_failures(self) -> list[dict]:
        return [r for r in self.rows if not r["pass"] and r["severity"] == "stop"]


def integrity(journals, items, plan, ctx, seed_frame, seed_scalars, imp, targets) -> tuple[Audit, dict]:
    a = Audit()
    facts: dict = {}
    manifest = json.loads((ORCH / "manifest.json").read_text())
    conf_seeds = [int(s) for s in plan["confirmation"]["seeds"]]
    # --- seeds and records
    for arm in ARMS:
        recs = journals[arm]
        seeds = [int(r["seed"]) for r in recs]
        a.check(f"{arm}: 30 journal records", len(recs) == 30, f"{len(recs)} records")
        a.check(f"{arm}: seeds unique", len(set(seeds)) == len(seeds))
        a.check(f"{arm}: seeds equal frozen confirmation seeds 202300-202329", sorted(seeds) == conf_seeds)
        a.check(f"{arm}: no trajectory error", all(not r.get("error") for r in recs))
        a.check(f"{arm}: every record carries the frozen plan and design hashes",
                all(r["plan_sha256"] == manifest["plan_sha256"] and r["design_sha256"] == manifest["design_sha256"]
                    for r in recs),
                f"plan {manifest['plan_sha256'][:12]}, design {manifest['design_sha256'][:12]}")
        a.check(f"{arm}: stage = confirmation, backbone = 1788",
                all(r["stage"] == "confirmation" and int(r["backbone"]) == BACKBONE for r in recs))
    a.check("candidate and legacy share the identical seed set",
            sorted(int(r["seed"]) for r in journals[CAND]) == sorted(int(r["seed"]) for r in journals[LEG]))
    # --- configuration provenance
    shortlist = {e["tuple_id"]: e for e in json.loads((ORCH / "frozen_shortlist.json").read_text())["entries"]}
    summary = pd.read_csv(ORCH / "confirmation_summary.csv").set_index("tuple_id")
    for arm in ARMS:
        it = items[arm]
        sha = ccs.sha256_json(it["config"])
        facts[f"{arm}_config_sha256"] = sha
        a.check(f"{arm}: rebuilt config hash = design.json = frozen_shortlist = confirmation_summary",
                sha == it["config_sha256"] == shortlist[arm]["config_sha256"] == summary.loc[arm, "config_sha256"],
                sha)
    cand_vec = items[CAND]["vector"]
    facts["candidate_coordinates"] = dict(cand_vec)
    facts["legacy_coordinates"] = dict(items[LEG]["vector"])
    for k, v in EXPECTED_COORDINATES.items():
        a.check(f"candidate coordinate {k} ~ {v}", abs(cand_vec[k] - v) < 5e-7, f"{cand_vec[k]:.9f}")
    changed = fc.changed_paths(items[LEG]["config"], items[CAND]["config"])
    facts["changed_paths_legacy_to_candidate"] = changed
    allowed = [".".join(fc.PROFILE_PATH)] + [".".join(fc.MSM_SCHEDULE_PATH + (y,)) for y in ("1996", "2004", "2019")]
    a.check("candidate differs from legacy only in the 3 MSM anchors + stage-diagnosis block",
            all(fc._allowed(p, allowed) for p in changed), f"{len(changed)} changed leaves")
    a.check("legacy equals the 1788 backbone exactly", fc.changed_paths(ctx.bases[BACKBONE], items[LEG]["config"]) == [])
    rho, sym, start = fc._profile_values(items[CAND]["config"])
    a.check("candidate routine_hazard_scale = 1 and symptom start = 1985", rho == 1.0 and start == 1985.0,
            f"rho={rho}, start={start}, symptom={sym}")
    # --- common random numbers
    first_div = {}
    for c, l in zip(journals[CAND], journals[LEG]):
        mc = {(int(r["year"]), r["series"]): r["value"] for r in c["mortality"]}
        ml = {(int(r["year"]), r["series"]): r["value"] for r in l["mortality"]}
        ic = c["annual_outputs"]["new_infections_msm"]
        il = l["annual_outputs"]["new_infections_msm"]
        pre = [y for y in range(1975, 1985) if ic.get(str(y)) != il.get(str(y))] + \
              [k[0] for k in mc if k[0] < 1985 and mc[k] != ml.get(k)]
        a.rows  # noqa: B018
        if pre:
            first_div[int(c["seed"])] = min(pre)
        dx = c["annual_outputs"]["new_diagnoses_msm"]
        dl = l["annual_outputs"]["new_diagnoses_msm"]
        first_div.setdefault(("dx", int(c["seed"])), min([y for y in range(1975, 2026)
                                                         if dx.get(str(y)) != dl.get(str(y))] or [None]))
    pre85 = [k for k in first_div if not isinstance(k, tuple)]
    a.check("CRN: every paired seed is identical 1975-1984 (MSM infections, all HIV deaths)", not pre85,
            f"{len(pre85)} seeds differ before 1985")
    dxdiv = sorted({v for k, v in first_div.items() if isinstance(k, tuple)})
    facts["first_year_msm_diagnoses_differ"] = dxdiv
    a.check("CRN: first divergence year is the 1985 diagnosis-mechanism boundary", dxdiv == [1985],
            f"first differing year(s) of MSM diagnoses: {dxdiv}", severity="note")
    # --- identities per trajectory
    sf = seed_frame
    piv = sf.pivot_table(index=["tuple_id", "seed", "year"], columns="series", values="value")
    proxy_gap = (piv.aids_proxy_msm - piv.aids_proxy_msm_component_progression_while_diagnosed -
                 piv.aids_proxy_msm_component_dx_in_aids_stage).abs().max()
    a.check("AIDS proxy = progression while diagnosed + diagnosis in AIDS stage (every seed-year)", proxy_gap == 0,
            f"max |gap| = {proxy_gap}")
    stage_gap = (piv.new_diagnoses_msm - piv.msm_dx_in_acute_stage - piv.msm_dx_in_chronic_stage -
                 piv.msm_dx_in_aids_stage).abs().max()
    a.check("MSM diagnoses = acute + chronic + AIDS-stage diagnoses (every seed-year)", stage_gap == 0,
            f"max |gap| = {stage_gap}")
    nm_gap = (piv.new_diagnoses_non_msm - piv.new_diagnoses_non_msm_endogenous -
              piv.new_diagnoses_non_msm_exogenous_C1).abs().max()
    a.check("non-MSM diagnoses = endogenous + exogenous C-1 (every seed-year)", nm_gap == 0, f"max |gap| = {nm_gap}")
    undx_on = piv.msm_undiagnosed_on_art_end.abs().max()
    a.check("MSM stock identity: PLHIV = undiagnosed + diagnosed-off-ART + on-ART (undiagnosed on ART = 0)",
            undx_on == 0, f"max |undiagnosed on ART| = {undx_on}")
    dx_gap = (piv.plhiv_msm_end - piv.msm_undiagnosed_end - piv.msm_diagnosed_end).abs().max()
    a.check("MSM stock identity: PLHIV = undiagnosed + diagnosed", dx_gap == 0, f"max |gap| = {dx_gap}")
    ud_death = piv.hiv_deaths_msm_undiagnosed_on_art.abs().max()
    a.check("no deaths recorded in the undiagnosed-on-ART state", ud_death == 0, f"max = {ud_death}", severity="note")
    # ledger residuals
    worst = 0.0
    nrows = 0
    for arm in ARMS:
        for item in journals[arm]:
            for r in item["ledger"]:
                worst = max(worst, abs(float(r["balance_residual"])))
                nrows += 1
    a.check("post-2015 stock-flow ledger balances in every seed, stratum, metric and year", worst == 0.0,
            f"{nrows} ledger rows; max |residual| = {worst}")
    # duplicates / missing in saved products
    ann = pd.read_csv(ORCH / "annual_trajectories_1996_2025.csv")
    ann = ann[ann.tuple_id.isin(ARMS)]
    dup = int(ann.duplicated(["tuple_id", "series", "year"]).sum())
    size_ok = bool((ann["size"] == 30).all())
    a.check("annual_trajectories: no duplicate (tuple, series, year); every cell has 30 seeds",
            dup == 0 and size_ok, f"{len(ann)} rows, {dup} duplicates")
    sl = pd.read_csv(ORCH / "seed_level_annual_1996_2025.csv.gz")
    sl = sl[sl.tuple_id.isin(ARMS)]
    dup2 = int(sl.duplicated(["tuple_id", "seed", "series", "year"]).sum())
    expected = 2 * 30 * sl.series.nunique() * 30
    a.check("seed_level_annual: no duplicates, complete 2 x 30 seeds x series x 30 years",
            dup2 == 0 and len(sl) == expected and sl.value.notna().all(), f"{len(sl)} rows (expected {expected})")
    # journals reproduce the saved seed-level annual file exactly
    jl = []
    for arm in ARMS:
        for item in journals[arm]:
            for s, vals in item["annual_outputs"].items():
                for y, v in vals.items():
                    if 1996 <= int(y) <= 2025:
                        jl.append((arm, int(item["seed"]), s, int(y), float(v)))
            for key, prefix in (("mortality", "all_"), ("mortality_msm", "msm_")):
                for r in item[key]:
                    if 1996 <= int(r["year"]) <= 2025:
                        jl.append((arm, int(item["seed"]), prefix + r["series"], int(r["year"]), float(r["value"])))
    jl = pd.DataFrame(jl, columns=["tuple_id", "seed", "series", "year", "journal_value"])
    m = sl.merge(jl, on=["tuple_id", "seed", "series", "year"], how="outer", indicator=True)
    a.check("journals reproduce seed_level_annual exactly (all annual series)",
            (m._merge == "both").all() and np.allclose(m.value, m.journal_value, rtol=0, atol=1e-9),
            f"{len(m)} cells compared")
    # saved seed-level scalars reproduce
    csl = pd.read_csv(ORCH / "confirmation_seed_level.csv")
    csl = csl[csl.tuple_id.isin(ARMS)].set_index(["tuple_id", "seed"])
    mine = seed_scalars.set_index(["tuple_id", "seed"])
    worst_s, bad = 0.0, []
    for col in [c for c in mine.columns if c in csl.columns]:
        diff = float((mine[col] - csl.loc[mine.index, col]).abs().max())
        worst_s = max(worst_s, diff)
        if diff > 1e-9:
            bad.append(col)
    a.check("independent seed-level metrics equal confirmation_seed_level.csv", not bad,
            f"max |diff| = {worst_s:.3g}; mismatching: {bad}")
    # headline reference values
    means = seed_scalars.groupby("tuple_id").mean(numeric_only=True)
    ref_rows = []
    for arm, refs in REFERENCE.items():
        for key, ref in refs.items():
            if key == "n_seeds":
                val = float(len(journals[arm]))
            elif key == "primary_i_max":
                val = float(imp[arm].primary_implausibility.max())
            elif key == "binding_target":
                val = imp[arm].iloc[0]["target"]
            else:
                val = float(means.loc[arm, key])
            if isinstance(ref, str):
                ok = val == ref
            elif key == "primary_i_max":
                ok = abs(val - ref) < 5e-7
            elif key.startswith("msm_fraction"):
                ok = abs(val - ref) < 5e-4
            else:
                ok = abs(val - ref) < 0.006
            ref_rows.append({"tuple_id": arm, "quantity": key, "brief_value": ref, "reproduced": val, "match": ok})
            a.check(f"reference {arm} {key} = {ref}", ok, f"reproduced {val}")
    # every one of the 13 targets passes; binding target; saved residual file matches
    a.check("candidate: all 13 primary targets I <= 3", bool(imp[CAND].primary_pass_I_le_3.all()))
    tr = pd.read_csv(ORCH / "target_residuals.csv")
    for arm in ARMS:
        saved = tr[(tr.tuple_id == arm) & (tr.stage == "confirmation")].set_index("target")
        mine_i = imp[arm].set_index("target")
        d1 = float((mine_i.primary_implausibility - saved.loc[mine_i.index, "primary_implausibility"]).abs().max())
        d2 = float((mine_i.sensitivity_implausibility - saved.loc[mine_i.index, "sensitivity_implausibility"])
                   .abs().max())
        a.check(f"{arm}: recomputed implausibilities equal target_residuals.csv", max(d1, d2) < 1e-9,
                f"max |diff| primary {d1:.2g}, sensitivity {d2:.2g}")
        a.check(f"{arm}: recomputed I_max equals confirmation_summary",
                abs(float(mine_i.primary_implausibility.max()) - float(summary.loc[arm, "primary_i_max"])) < 1e-9)
    # fraction-in-AIDS definition
    facts["fraction_dx_in_aids_2024_mean_of_seed_ratios"] = float(means.loc[CAND, "msm_fraction_dx_in_aids_2024"])
    facts["fraction_dx_in_aids_2024_ratio_of_means"] = float(means.loc[CAND, "msm_dx_in_aids_2024"] /
                                                             means.loc[CAND, "dx2024"])
    # E4 seed compatibility
    u = seed_scalars[seed_scalars.tuple_id == CAND].msm_undiagnosed_end2024.to_numpy()
    n_in = int(((u >= TIER2_E4[0]) & (u <= TIER2_E4[1])).sum())
    facts["e4_candidate_seeds_inside"] = n_in
    facts["e4_candidate_seeds_above"] = int((u > TIER2_E4[1]).sum())
    facts["e4_candidate_seeds_below"] = int((u < TIER2_E4[0]).sum())
    ext = pd.read_csv(ORCH / "external_plausibility_checks.csv")
    saved_share = float(ext[(ext.tuple_id == CAND) & (ext.check_id == "E4")].share_of_seeds_compatible.iloc[0])
    a.check("E4: 15 of 30 candidate seeds individually inside 690-900", n_in == 15 and abs(saved_share - 0.5) < 1e-12,
            f"{n_in} inside; saved share {saved_share}")
    # paired changes reproduce
    psd = pd.read_csv(ORCH / "paired_seed_differences.csv")
    psd = psd[(psd.tuple_id == CAND) & (psd.comparator == LEG)].set_index("seed")
    c_ = mine.loc[CAND]
    l_ = mine.loc[LEG]
    worst_p = 0.0
    for col in [c for c in psd.columns if c.startswith("diff__")]:
        met = col[6:]
        if met in c_.columns:
            worst_p = max(worst_p, float(((c_[met] - l_[met]) - psd[col]).abs().max()))
    a.check("paired seed differences reproduce paired_seed_differences.csv", worst_p < 1e-9, f"max |diff| {worst_p:.2g}")
    # input hashes
    facts["manifest_confirmation_merged_sha256"] = manifest["gates"]["confirmation_merge"]["confirmation_merged_sha256"]
    a.check("confirmation_merged.csv hash equals manifest gate",
            sha_file(ORCH / "confirmation_merged.csv") == facts["manifest_confirmation_merged_sha256"])
    facts["reference_table"] = ref_rows
    return a, facts


# ==========================================================================
# Annual comparison tables (Phase 2)
# ==========================================================================

def annual_comparison(seed_frame: pd.DataFrame) -> pd.DataFrame:
    meta = seed_frame.drop_duplicates("series").set_index("series")[["basis", "unit", "population", "kind"]]
    rows = []
    for (series, year), g in seed_frame.groupby(["series", "year"], sort=False):
        c = g[g.tuple_id == CAND].sort_values("seed")
        l = g[g.tuple_id == LEG].sort_values("seed")
        if list(c.seed) != list(l.seed):
            raise RuntimeError(f"unpaired seeds for {series} {year}")
        cv, lv = c.value.to_numpy(float), l.value.to_numpy(float)
        if np.isnan(cv).all() and np.isnan(lv).all():
            continue
        p = paired_stats(cv, lv)
        rows.append({"series": series, "year": int(year), **meta.loc[series].to_dict(),
                     "candidate_mean": float(np.nanmean(cv)), "candidate_sd": float(np.nanstd(cv, ddof=1)),
                     "legacy_mean": float(np.nanmean(lv)), "legacy_sd": float(np.nanstd(lv, ddof=1)), **p,
                     "pct_change_vs_legacy": (float(np.nanmean(cv)) / float(np.nanmean(lv)) - 1.0)
                     if np.nanmean(lv) not in (0.0,) and np.isfinite(np.nanmean(lv)) else np.nan,
                     "ci_excludes_zero": bool(p["paired_ci95_low"] > 0 or p["paired_ci95_high"] < 0)})
    f = pd.DataFrame(rows)
    f.insert(2, "timing", np.where(f.kind == "stock_end", "end of year", "calendar-year flow"))
    return f.sort_values(["series", "year"]).reset_index(drop=True)


MILESTONE_SERIES = (
    "plhiv_total_end", "plhiv_msm_end", "plhiv_non_msm_end", "msm_undiagnosed_end", "msm_diagnosed_off_art_end",
    "msm_on_art_end", "msm_art_coverage_of_diagnosed_end", "new_infections_msm", "new_infections_non_msm",
    "new_diagnoses_msm", "new_diagnoses_non_msm", "msm_dx_in_aids_stage", "msm_fraction_dx_in_aids_stage",
    "aids_proxy_msm", "hiv_deaths_msm", "hiv_deaths_non_msm", "msm_on_oral_end",
)


def milestones(annual: pd.DataFrame) -> pd.DataFrame:
    m = annual[annual.series.isin(MILESTONE_SERIES) & annual.year.isin(MILESTONE_YEARS)].copy()
    m["series"] = pd.Categorical(m.series, MILESTONE_SERIES, ordered=True)
    return m.sort_values(["series", "year"])


# ==========================================================================
# External evidence (Phase 2)
# ==========================================================================

def external_comparison(seed_scalars: pd.DataFrame, annual: pd.DataFrame) -> pd.DataFrame:
    """Every predeclared external check (E1-E14) plus descriptive annual register series."""
    contract = json.loads((ORCH / "frozen_target_contract.json").read_text())
    reg = pd.read_csv(ORCH / "empirical_constraint_register.csv")
    means = seed_scalars.groupby("tuple_id").mean(numeric_only=True)
    sds = seed_scalars.groupby("tuple_id").std(numeric_only=True, ddof=1)
    source_of = {
        "E1": "SHM Monitoring Report 2025 (ECDC modelling tool estimate)", "E2": "SHM 2025 (ECDC tool)",
        "E3": "SHM 2025 (ECDC tool)", "E4": "SHM 2025 (ECDC tool, back-calculated from CD4 at diagnosis)",
        "E5": "SHM 2025 (ECDC tool)", "E6": "SHM 2025 (ECDC tool)", "E7": "SHM 2025 (ever started ART, MSM)",
        "E8": "SHM 2025 (cascade: on ART / diagnosed-linked)", "E9": "UNAIDS/Spectrum (workbook UNAIDS-PLHIV)",
        "E9a": "UNAIDS/Spectrum (workbook UNAIDS-HIV Infections)", "E10": "UNAIDS (workbook UNAIDS-ART)",
        "E11": "UNAIDS/Spectrum modelled AIDS deaths (workbook UNAIDS-AIDSDeath)",
        "E12": "RIVM D76: all-cause deaths among HIV patients, 2010-2014 summed",
        "E13": "SHM 2025 advanced presentation (CD4<200 or AIDS), MSM 56 + trans 2",
        "E13f": "SHM 2025 advanced-presentation fraction, MSM", "E14": "Op de Coul 2016, MSM 1996-2014 pooled",
    }
    role_of = {"interval": "Tier-2 plausibility interval", "one_sided_upper": "one-sided safety bound",
               "descriptive": "descriptive only"}
    seeds = {a: seed_scalars[seed_scalars.tuple_id == a] for a in ARMS}
    rows = []
    for e in contract["external_checks"]:
        metric = e["metric"]
        if metric not in means.columns:
            continue
        cm, lm = float(means.loc[CAND, metric]), float(means.loc[LEG, metric])
        lo, hi, obs = e["lower"], e["upper"], float(e["observed"])
        comp = {}
        for arm in ARMS:
            v = seeds[arm][metric].to_numpy(float)
            if e["type"] == "interval":
                comp[arm] = int(((v >= lo) & (v <= hi)).sum())
            elif e["type"] == "one_sided_upper":
                comp[arm] = int((v <= hi).sum())
            else:
                comp[arm] = None
        if e["type"] == "interval":
            status = lambda m: "inside" if lo <= m <= hi else ("above" if m > hi else "below")  # noqa: E731
            gap_to_interval = lambda m: 0.0 if lo <= m <= hi else (m - hi if m > hi else m - lo)  # noqa: E731
        elif e["type"] == "one_sided_upper":
            status = lambda m: "within_bound" if m <= hi else "violates_bound"  # noqa: E731
            gap_to_interval = lambda m: 0.0 if m <= hi else m - hi  # noqa: E731
        else:
            status = lambda m: "descriptive"  # noqa: E731
            gap_to_interval = lambda m: np.nan  # noqa: E731
        c_dist, l_dist = abs(cm - obs), abs(lm - obs)
        direction = "toward" if c_dist < l_dist else ("away" if c_dist > l_dist else "unchanged")
        if e["type"] == "one_sided_upper":
            direction = ("toward bound (less headroom)" if cm > lm else "away from bound (more headroom)")
        rows.append({
            "check_id": e["id"], "metric": metric, "source": source_of.get(e["id"], ""),
            "epistemic_tier": f"Tier {e['tier']}", "comparison_type": role_of[e["type"]],
            "decision_role_in_orchestration": e["decision_role"],
            "observed": obs, "lower": lo, "upper": hi,
            "candidate_mean": cm, "candidate_sd": float(sds.loc[CAND, metric]),
            "legacy_mean": lm, "legacy_sd": float(sds.loc[LEG, metric]),
            "candidate_minus_observed": cm - obs, "candidate_pct_gap_vs_observed": (cm - obs) / obs,
            "legacy_minus_observed": lm - obs, "legacy_pct_gap_vs_observed": (lm - obs) / obs,
            "candidate_gap_to_interval_or_bound": gap_to_interval(cm),
            "candidate_status": status(cm), "legacy_status": status(lm),
            "candidate_seeds_compatible_of_30": comp[CAND], "legacy_seeds_compatible_of_30": comp[LEG],
            "candidate_moves": direction,
        })
    # AIDS-stage deaths vs all HIV deaths against the UNAIDS 2023 value (label seam)
    for label, metric in (("E11-alt: all model HIV deaths 2023 vs UNAIDS AIDS deaths", "hiv_deaths_all_2023"),):
        e11 = next(e for e in contract["external_checks"] if e["id"] == "E11")
        cm, lm = float(means.loc[CAND, metric]), float(means.loc[LEG, metric])
        obs = float(e11["observed"])
        rows.append({"check_id": "E11b", "metric": metric, "source": label, "epistemic_tier": "Tier 2",
                     "comparison_type": "descriptive only", "decision_role_in_orchestration": "none",
                     "observed": obs, "lower": e11["lower"], "upper": e11["upper"], "candidate_mean": cm,
                     "candidate_sd": float(sds.loc[CAND, metric]), "legacy_mean": lm,
                     "legacy_sd": float(sds.loc[LEG, metric]), "candidate_minus_observed": cm - obs,
                     "candidate_pct_gap_vs_observed": (cm - obs) / obs, "legacy_minus_observed": lm - obs,
                     "legacy_pct_gap_vs_observed": (lm - obs) / obs,
                     "candidate_gap_to_interval_or_bound": cm - e11["upper"] if cm > e11["upper"] else 0.0,
                     "candidate_status": "descriptive", "legacy_status": "descriptive",
                     "candidate_seeds_compatible_of_30": None, "legacy_seeds_compatible_of_30": None,
                     "candidate_moves": "toward" if abs(cm - obs) < abs(lm - obs) else "away"})
    return pd.DataFrame(rows)


def annual_register_comparison(annual: pd.DataFrame) -> pd.DataFrame:
    """Descriptive: annual register series (Tier 1 counts, Tier 2 UNAIDS) vs model, by year."""
    reg = pd.read_csv(ORCH / "empirical_constraint_register.csv")
    mapping = {
        ("hiv_diagnoses", "MSM"): ("new_diagnoses_msm", "Tier 1 SHM MSM diagnoses"),
        ("aids_diagnoses_msm", None): ("aids_proxy_msm", "Tier 1 RIVM MSM AIDS (approximate proxy)"),
        ("plhiv_all_ages", None): ("plhiv_total_end", "Tier 2 UNAIDS PLHIV (end-of-year model stock)"),
        ("art_adults", None): ("on_art_total_end", "Tier 2 UNAIDS ART (end-of-year model stock)"),
        ("new_infections_all_ages", None): ("new_infections_total", "Tier 2 UNAIDS new infections"),
        ("aids_deaths_all_ages", None): ("hiv_deaths_total", "Tier 2 UNAIDS AIDS deaths vs all model HIV deaths"),
        ("deaths_among_hiv_patients", None): (
            "hiv_deaths_diagnosed_all", "Tier 1 RIVM all-cause deaths among HIV patients (upper bound on model "
            "HIV deaths among diagnosed)"),
    }
    a = annual.set_index(["series", "year"])
    rows = []
    for (metric, pop), (series, label) in mapping.items():
        r = reg[reg.metric == metric]
        if pop:
            r = r[r.population.astype(str).str.contains(pop)]
        for _, row in r.iterrows():
            try:
                year = int(str(row.period)[:4])
            except ValueError:
                continue
            if not 1996 <= year <= 2025:
                continue
            if series == "hiv_deaths_diagnosed_all":
                keys = [("hiv_deaths_total_diagnosed_off_art", year), ("hiv_deaths_total_diagnosed_on_art", year)]
                if not all(k in a.index for k in keys):
                    continue
                cm = sum(a.loc[k, "candidate_mean"] for k in keys)
                lm = sum(a.loc[k, "legacy_mean"] for k in keys)
            else:
                if (series, year) not in a.index:
                    continue
                cm, lm = a.loc[(series, year), "candidate_mean"], a.loc[(series, year), "legacy_mean"]
            obs = float(row.value)
            rows.append({"register_metric": metric, "register_population": row.population, "year": year,
                         "tier": row.tier, "register_role": row.role, "comparison": label, "model_series": series,
                         "observed": obs, "lower": row.lower, "upper": row.upper, "candidate_mean": cm,
                         "legacy_mean": lm, "candidate_minus_observed": cm - obs,
                         "legacy_minus_observed": lm - obs,
                         "candidate_moves": "toward" if abs(cm - obs) < abs(lm - obs) else "away"})
    return pd.DataFrame(rows)


# ==========================================================================
# Integrity note
# ==========================================================================

def integrity_note(a: Audit, facts: dict, inputs: dict) -> str:
    ok = not a.blocking_failures
    lines = [
        "# Data integrity note",
        "",
        f"Brief: `{BRIEF}`, Phase 1. Candidate `{CAND}` versus matched legacy `{LEG}`, backbone {BACKBONE}, "
        "confirmation stage.",
        "",
        f"**Result: {'PASS' if ok else 'STOP'}.** {sum(r['pass'] for r in a.rows)} of {len(a.rows)} checks pass; "
        f"{len(a.blocking_failures)} blocking failures.",
        "",
        "All values below were recomputed from the 60 per-trajectory journals (the primary record) and then "
        "compared with the orchestration's derived CSVs. No simulation was run; the frozen design was rebuilt as "
        "configuration dictionaries only.",
        "",
        "## Findings that qualify interpretation (not errors)",
        "",
        "1. **Divergence starts in 1985, not 1996.** Every paired seed is identical for 1975-1984 and first differs "
        "in 1985. Before 1996 the routine MSM probability is identical in both arms (the MSM multiplier is 1.0 "
        "before its first anchor; `run_v3.py` fix C-4), so the 1985-1995 differences come from the symptom hazard "
        "(active from 1985) and from the change of diagnosis random-number keys (finding 2). From 1996 the "
        "routine probabilities differ: the candidate's 1996 anchor (0.820) exceeds the legacy's (0.739), so the "
        "candidate tests MSM slightly *more* in 1996-1999 and less from about 2000 (effective annual probability "
        "2004: 0.159 vs 0.198; 2010: 0.198 vs 0.254; 2019 onward: 0.264 vs 0.350). Both arms step down at 1996, "
        "where the multiplier first applies (0.335 in 1995 to 0.288 / 0.260 in 1996).",
        "2. **Common random numbers are shared seeds, not identical diagnosis draws.** M0 draws diagnoses under "
        "event keys `diagnosis_msm` / `diagnosis_non_msm`; M1 draws them per population and stage under "
        "`stage_structured_diagnosis_*` keys (`run_v3.py::_step_stage_structured_diagnosis`). After 1985 the two "
        "arms therefore do not share diagnosis random numbers. The paired design remains valid: each seed pair "
        "differs only in configuration, so paired mean differences estimate the configuration effect without bias, "
        "and the paired t-intervals use the realised within-pair correlation. Pairing is less variance-reducing "
        "than it would be under full CRN.",
        "3. **The symptom hazard also acts on non-MSM.** `_step_stage_structured_diagnosis` applies the AIDS "
        "symptom hazard to undiagnosed AIDS-stage people in every population, not only MSM. Endogenous non-MSM "
        "AIDS-stage diagnoses are therefore genuinely affected. Non-MSM is *not* mechanically untouched; the size "
        "of the effect is quantified in the report.",
        "4. **Fraction diagnosed in AIDS (2024).** The brief's 11.80% is the mean of per-seed ratios "
        f"({facts['fraction_dx_in_aids_2024_mean_of_seed_ratios']:.4f}); the ratio of means is "
        f"{facts['fraction_dx_in_aids_2024_ratio_of_means']:.4f}. Both are reported where relevant.",
        "",
        "## Configuration provenance",
        "",
        f"- Candidate config sha256 `{facts[f'{CAND}_config_sha256']}`; legacy `{facts[f'{LEG}_config_sha256']}`. "
        "Each rebuilds from the frozen design and equals `design.json`, `frozen_shortlist.json` and "
        "`confirmation_summary.csv`.",
        "- Diagnosis coordinates, extracted from the frozen design (not transcribed):",
        "",
        "| coordinate | candidate | legacy |",
        "|---|---:|---:|",
    ]
    for k in EXPECTED_COORDINATES:
        lines.append(f"| `{k}` | {facts['candidate_coordinates'][k]:.6f} | {facts['legacy_coordinates'][k]:.6f} |")
    lines += [
        "",
        f"- Changed configuration leaves, legacy to candidate ({len(facts['changed_paths_legacy_to_candidate'])}):",
        "",
    ]
    lines += [f"  - `{p}`" for p in facts["changed_paths_legacy_to_candidate"]]
    lines += [
        "",
        "  `routine_hazard_scale = 1` is the identity, so only the three anchors and the symptom probability "
        "change behaviour. Every non-diagnosis parameter is identical.",
        "",
        "## Reference values from the brief",
        "",
        "| arm | quantity | brief | reproduced | match |",
        "|---|---|---:|---:|---|",
    ]
    for r in facts["reference_table"]:
        rep = r["reproduced"] if isinstance(r["reproduced"], str) else f"{r['reproduced']:.6g}"
        lines.append(f"| {r['tuple_id']} | {r['quantity']} | {r['brief_value']} | {rep} | "
                     f"{'yes' if r['match'] else '**NO**'} |")
    lines += [
        "",
        f"Tier-2 E4 seed-level: {facts['e4_candidate_seeds_inside']} of 30 candidate seeds inside 690-900, "
        f"{facts['e4_candidate_seeds_above']} above, {facts['e4_candidate_seeds_below']} below.",
        "",
        "## All checks",
        "",
        "| check | result | severity | detail |",
        "|---|---|---|---|",
    ]
    for r in a.rows:
        lines.append(f"| {r['check']} | {'pass' if r['pass'] else '**FAIL**'} | {r['severity']} | {r['detail']} |")
    lines += ["", "## Inputs read (sha256)", "", "| file | sha256 |", "|---|---|"]
    for name, digest in inputs.items():
        lines.append(f"| `{name}` | `{digest}` |")
    lines.append("")
    return "\n".join(lines)


# ==========================================================================
# Main
# ==========================================================================

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    plan, items, ctx = rebuild_configs()
    configs = {a: items[a]["config"] for a in ARMS}
    journals = load_journals()
    targets = list(ctx.state["targets"])
    frozen = json.loads((ORCH / "frozen_target_contract.json").read_text())["primary_targets"]
    if [(t.key, float(t.z), float(t.v_obs_variance), float(t.v_disc_variance)) for t in targets] != \
            [(t["key"], float(t["z"]), float(t["v_obs_variance"]), float(t["v_disc_variance"])) for t in frozen]:
        raise RuntimeError("rebuilt 13-target contract differs from frozen_target_contract.json")
    seed_frame = build_seed_frame(journals, configs)
    seed_scalars = pd.DataFrame([{"tuple_id": a, "seed": int(i["seed"]), **scalar_metrics(i, targets)}
                                 for a in ARMS for i in journals[a]])
    imp = {a: implausibility_table(seed_scalars, targets, a) for a in ARMS}

    inputs = {str(p.relative_to(ROOT)): sha_file(p) for p in sorted(
        [ORCH / n for n in ("manifest.json", "frozen_plan.json", "design.json", "frozen_shortlist.json",
                            "frozen_target_contract.json", "confirmation_summary.csv", "confirmation_seed_level.csv",
                            "paired_seed_differences.csv", "annual_trajectories_1996_2025.csv",
                            "seed_level_annual_1996_2025.csv.gz", "target_residuals.csv",
                            "external_plausibility_checks.csv", "latent_state_2025.csv", "post2015_stock_flow.csv",
                            "stage_diagnosis_decomposition.csv", "effective_msm_routine_paths.csv",
                            "decision_clauses.csv", "identifiability.json", "empirical_constraint_register.csv",
                            "confirmation_merged.csv")])}
    journal_digest = hashlib.sha256("".join(sha_file(ROOT / i["_path"]) for a in ARMS
                                            for i in journals[a]).encode()).hexdigest()
    inputs["60 confirmation journals (concatenated sha256 of each, candidate then legacy, by seed)"] = journal_digest

    audit_obj, facts = integrity(journals, items, plan, ctx, seed_frame, seed_scalars, imp, targets)
    write_text(out, "DATA_INTEGRITY_NOTE.md", integrity_note(audit_obj, facts, inputs))
    (out / "integrity_checks.json").write_text(json.dumps({"checks": audit_obj.rows, "facts": {
        k: v for k, v in facts.items() if k != "reference_table"}}, indent=2, default=str))
    if audit_obj.blocking_failures:
        print("STOP: blocking integrity failures:")
        for r in audit_obj.blocking_failures:
            print("  -", r["check"], r["detail"])
        return 2

    annual = annual_comparison(seed_frame)
    write_csv(out, "candidate_vs_legacy_annual.csv", annual)
    write_csv(out, "candidate_vs_legacy_milestones.csv", milestones(annual))
    tit = pd.concat([imp[CAND], imp[LEG]], ignore_index=True)
    write_csv(out, "target_implausibility_table.csv", tit)
    ext = external_comparison(seed_scalars, annual)
    write_csv(out, "external_evidence_comparison.csv", ext)
    write_csv(out, "external_evidence_annual_descriptive.csv", annual_register_comparison(annual))
    seed_frame.to_csv(out / "seed_level_long_1996_2025.csv.gz", index=False)
    seed_scalars.to_csv(out / "seed_level_scalars.csv", index=False)
    for arm, name in ((CAND, "candidate_configuration.json"), (LEG, "legacy_configuration.json")):
        payload = {"tuple_id": arm, "backbone": BACKBONE, "model": items[arm]["model"],
                   "config_sha256": ccs.sha256_json(configs[arm]), "vector": items[arm]["vector"],
                   "source": "rebuilt statically from outputs/calibration/diagnosis_triangulation_orchestration_"
                             "20260922/design.json via run_diagnosis_triangulation_orchestration.build_design; "
                             "hash-verified; NOT a production configuration",
                   "config": configs[arm]}
        write_text(out, name, json.dumps(payload, indent=2, sort_keys=True))
    print(f"integrity PASS ({len(audit_obj.rows)} checks); tables written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
