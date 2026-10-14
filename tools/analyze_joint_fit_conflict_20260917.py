#!/usr/bin/env python3
"""Existing-evidence analysis for TASK_BRIEF_JOINT_FIT_CONFLICT_2026-09-17 (Phases A-B).

Read-only on both source runs; runs NO simulations.  Writes a new directory and
refuses to write into an existing non-empty one.

Sources
  outputs/calibration/hm_focused_diagnoses_then_aids_20260916      (622 vectors, K=5 moments only)
  outputs/calibration/hm_focused_confirmation_600_621_20260917      (2 vectors x 50 seed-level rows)

Nothing here is an acceptance rule, a posterior, or an NROY update.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import hm_joint_fit_conflict as jf  # noqa: E402
import hm_target_register as tr  # noqa: E402
import run_history_matching as rhm  # noqa: E402
from run_hm_confirmation import _verified_manifest, aggregate_replicates  # noqa: E402

SOURCE = REPO / "outputs/calibration/hm_focused_diagnoses_then_aids_20260916"
CONFIRM = REPO / "outputs/calibration/hm_focused_confirmation_600_621_20260917"
DEFAULT_OUT = REPO / "outputs/calibration/hm_joint_fit_conflict_20260917_rev1"
SERIES = [jf.DIAGNOSIS_SERIES, jf.AIDS_SERIES]
BOOTSTRAP_SEED = 20260917
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_K = (5, 10, 20, 50)
CONFIRMED_IDS = (600, 621)
SAVED_PRECISION_TOL = 1e-6


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------

def phase_a(out: Path) -> dict:
    report: dict = {}
    source_manifest = _verified_manifest(SOURCE.resolve())
    confirm_manifest = _verified_manifest(CONFIRM.resolve())
    report["artefact_hashes_verified"] = {"source": len(source_manifest["artefacts"]),
                                          "confirmation": len(confirm_manifest["artefacts"])}
    prov = source_manifest["input_provenance"]
    inputs = {}
    for name in ("wave_plan", "base_config", "calib_space", "target_register"):
        path = Path(prov[name]["path"])
        inputs[name] = {"path": str(path.relative_to(REPO)), "recorded": prov[name]["sha256"],
                        "current": sha(path), "match": sha(path) == prov[name]["sha256"]}
    warm = REPO / source_manifest["run_settings"]["supplementary_design_file"]
    inputs["supplementary_design"] = {"recorded": source_manifest["run_settings"]["supplementary_design_sha256"],
                                      "current": sha(warm)}
    inputs["supplementary_design"]["match"] = inputs["supplementary_design"]["recorded"] == inputs["supplementary_design"]["current"]
    report["inputs"] = inputs
    current_modules = rhm.repository_provenance()
    src_mods = prov["runtime"]["modules"]
    conf_mods = confirm_manifest["runtime"]["modules"]
    report["module_hash_differences"] = {
        "current_vs_source": sorted(m for m in set(src_mods) | set(current_modules["modules"])
                                    if src_mods.get(m) != current_modules["modules"].get(m)),
        "confirmation_vs_source": sorted(m for m in set(src_mods) | set(conf_mods) if src_mods.get(m) != conf_mods.get(m)),
    }
    report["confirmation_script_hashes_match"] = {
        "run_focused_confirmation_20260917.py": sha(REPO / "run_focused_confirmation_20260917.py") == confirm_manifest["script_sha256"],
        "run_hm_confirmation.py": sha(REPO / "run_hm_confirmation.py") == confirm_manifest["confirmation_helper_sha256"],
    }
    report["confirmation_links_to_source_manifest"] = sha(SOURCE / "manifest.json") == confirm_manifest["source_manifest_sha256"]
    report["git"] = {"source": prov["runtime"]["git"], "confirmation": confirm_manifest["runtime"]["git"],
                     "current": current_modules["git"],
                     "note": "Worktree dirty in all three; status hashes differ. Module hashes are the compatibility evidence."}
    report["python"] = {"source": prov["runtime"]["python"], "current": current_modules["python"]}

    space = json.loads(Path(prov["calib_space"]["path"]).read_text())
    names = [p["name"] for p in space["parameters"]]
    wave = SOURCE / "wave_01"
    evaluated = pd.read_csv(wave / "evaluated.csv")
    design = pd.read_csv(wave / "design.csv")
    sources = pd.read_csv(wave / "design_sources.csv")
    if len(names) != 33:
        raise SystemExit(f"Expected 33 calibrated parameters, found {len(names)}")
    jf.require_unique(evaluated, ["design_id"], "evaluated")
    jf.require_unique(sources, ["design_row"], "design_sources")
    jf.require_finite_columns(evaluated, names, "evaluated parameters")
    design = design.reset_index().rename(columns={"index": "design_id"})
    joined = jf.join_on_id(evaluated[["design_id", *names]], design[["design_id", *names]].rename(
        columns={n: f"{n}__design" for n in names}), "design_id", "evaluated-vs-design")
    param_diff = max(float(np.abs(joined[n] - joined[f"{n}__design"]).max()) for n in names)
    errors = evaluated["error"].fillna("").astype(str).str.strip().ne("").sum()
    seeds = evaluated["replicate_seeds"].unique().tolist()
    settings = source_manifest["run_settings"]
    expected_seeds = ";".join(str(settings["simulation_seed"] + i * settings["replicate_seed_stride"])
                              for i in range(settings["replicates_per_design_point"]))
    report["wave1"] = {
        "n_rows": len(evaluated), "unique_design_ids": int(evaluated.design_id.nunique()),
        "id_range": [int(evaluated.design_id.min()), int(evaluated.design_id.max())],
        "n_parameters": len(names), "max_abs_design_vs_evaluated_parameter_diff": param_diff,
        "source_counts": sources.source.value_counts().to_dict(),
        "supplementary_ids": sources.loc[sources.source == "supplementary", "design_row"].tolist(),
        "n_errors_recomputed": int(errors), "n_errors_manifest": source_manifest["history"][0]["n_errors"],
        "replicates_column": evaluated.n_replicates.unique().tolist(), "replicate_seeds": seeds,
        "replicate_seeds_match_manifest": seeds == [expected_seeds],
        "saved": "per-vector replicate MEANS and ddof=1 sample VARIANCES (mcvar__) for 97 annual output families x 36 years; NO seed-level replicate rows",
        "unavailable_tests": [
            "seed-level bootstrap for the 622 vectors (no replicate rows saved)",
            "paired seed-level trajectory comparison between wave-1 vectors",
            "empirical percentile/confidence bands for wave-1 trajectories",
            "within-vector covariance between targets (only marginal variances saved)",
        ],
    }

    raw = pd.read_csv(CONFIRM / "raw_replicates.csv")
    selected = pd.read_csv(CONFIRM / "selected_parameters.csv")
    jf.require_unique(raw, ["source_design_id", "confirmation_seed"], "confirmation raw")
    seed_table = raw.groupby("source_design_id").confirmation_seed.agg(["nunique", "min", "max", "count"])
    same_vector = float(np.abs(selected.set_index("design_id").loc[list(CONFIRMED_IDS), names].to_numpy()
                               - evaluated.set_index("design_id").loc[list(CONFIRMED_IDS), names].to_numpy()).max())
    raw_param_diff = max(float(np.abs(raw.loc[raw.source_design_id == i, names].to_numpy()
                                      - evaluated.set_index("design_id").loc[i, names].to_numpy()).max())
                         for i in CONFIRMED_IDS)
    seed_sets_equal = (sorted(raw.loc[raw.source_design_id == 600, "confirmation_seed"])
                       == sorted(raw.loc[raw.source_design_id == 621, "confirmation_seed"])
                       == list(range(194000, 194050)))
    report["confirmation"] = {
        "seeds_per_vector": seed_table.reset_index().to_dict("records"),
        "seed_sets_identical_and_194000_194049": seed_sets_equal,
        "selected_parameters_equal_source": same_vector == 0.0,
        "raw_row_parameters_equal_source": raw_param_diff == 0.0,
        "n_errors": int(raw.error.fillna("").astype(str).str.strip().ne("").sum()),
        "overlap_with_wave1_seeds": sorted(set(range(194000, 194050)) & {93000, 93001, 93002, 93003, 93004}),
        "note_partial_file": "raw_replicates.partial.csv has identical size but different row order/hash (unsorted checkpoint); not used.",
    }
    if not (seed_sets_equal and same_vector == 0.0 and raw_param_diff == 0.0 and errors == 0):
        raise SystemExit("Phase A lineage check failed; refusing interpretation")
    return {"report": report, "evaluated": evaluated, "sources": sources, "names": names, "space": space,
            "raw": raw, "selected": selected, "manifest": source_manifest}


def score_reproduction(ctx: dict, register: tr.TargetRegister) -> tuple[pd.DataFrame, dict]:
    manifest = ctx["manifest"]
    evaluated = ctx["evaluated"]
    years = manifest["scored_years"]
    rows = []
    wave = rhm.score_wave(evaluated, register, [jf.DIAGNOSIS_SERIES], disc_multiplier=0.1,
                          disc_method="relative_to_target", ensemble_mode="predictive_local",
                          replicates=5, scored_years=years)
    nroy = pd.read_csv(SOURCE / "wave_01/nroy_members.csv")
    reproduced_ids = sorted(int(i) for i in evaluated.design_id[wave["mask"]])
    for _, r in nroy.iterrows():
        i = int(r.design_id)
        rows.append({"source": "wave1_diagnoses_primary_K5", "design_id": i, "saved_i_max": r.i_max,
                     "recomputed_i_max": float(wave["combined"].i_max[i]),
                     "saved_binding": r.binding_target, "recomputed_binding": wave["combined"].binding_target[i],
                     "saved_retained": True, "recomputed_retained": bool(wave["mask"][i])})
    means = aggregate_replicates(ctx["raw"], ctx["selected"], ctx["names"])
    saved_means = pd.read_csv(CONFIRM / "confirmed_means.csv")
    numeric = [c for c in saved_means.columns if c not in ("replicate_seeds", "error")]
    means_diff = float(np.nanmax(np.abs(means[numeric].astype(float).to_numpy() - saved_means[numeric].astype(float).to_numpy())))
    summary = pd.read_csv(CONFIRM / "confirmation_summary.csv")
    for block, wanted in (("diagnoses", [jf.DIAGNOSIS_SERIES]), ("joint", SERIES)):
        for allowance in (0.1, 0.2):
            s = rhm.score_wave(means, register, wanted, disc_multiplier=allowance, disc_method="relative_to_target",
                               ensemble_mode="predictive_local", replicates=50, scored_years=years)
            for idx, did in enumerate(means.design_id):
                saved = summary[(summary.design_id == did) & (summary.block == block) & (summary.discrepancy == allowance)].iloc[0]
                rows.append({"source": f"confirmation_{block}_{allowance}_K50", "design_id": int(did),
                             "saved_i_max": saved.i_max, "recomputed_i_max": float(s["combined"].i_max[idx]),
                             "saved_binding": saved.binding_target, "recomputed_binding": s["combined"].binding_target[idx],
                             "saved_retained": bool(saved.retained), "recomputed_retained": bool(s["mask"][idx])})
    table = pd.DataFrame(rows)
    table["abs_diff"] = (table.saved_i_max - table.recomputed_i_max).abs()
    table["within_tolerance"] = table.abs_diff <= SAVED_PRECISION_TOL
    ok = bool(table.within_tolerance.all() and (table.saved_binding == table.recomputed_binding).all()
              and (table.saved_retained == table.recomputed_retained).all()
              and reproduced_ids == sorted(nroy.design_id.tolist()))
    info = {"wave1_primary_survivors_recomputed": reproduced_ids, "confirmation_means_max_abs_diff": means_diff,
            "all_rows_reproduced": ok, "max_abs_i_max_diff": float(table.abs_diff.max()),
            "tolerance": SAVED_PRECISION_TOL,
            "label_note": ("The brief's 'primary diagnosis Imax 3.867894 / 3.461314' are the K=50 CONFIRMATION "
                           "values. The wave-1 K=5 values that admitted 600/621 were 2.013998 / 1.989129.")}
    if not ok:
        raise SystemExit("Score reproduction failed; Gate A stop")
    return table, {"info": info, "confirmed_means": means}


# ---------------------------------------------------------------------------
# Phase B
# ---------------------------------------------------------------------------

def mechanism_correlations(summary: pd.DataFrame, params: pd.DataFrame, names: list[str], group: str) -> pd.DataFrame:
    from scipy.stats import spearmanr  # noqa: PLC0415

    outcome_cols = [c for c in summary.columns if c.startswith("rel__")] + \
                   [f"mean_signed_rel_error_{p}" for p in jf.PERIODS] + \
                   ["max_abs_rel_error_diagnosis", "max_abs_rel_error_aids", "max_abs_rel_error_joint"]
    merged = summary.merge(params, on="design_id", validate="one_to_one")
    rows = []
    for outcome in outcome_cols:
        y = merged[outcome].to_numpy(dtype=float)
        for n in names:
            x = merged[n].to_numpy(dtype=float)
            ok = np.isfinite(x) & np.isfinite(y)
            rho, p = spearmanr(x[ok], y[ok]) if ok.sum() > 3 else (np.nan, np.nan)
            rows.append({"group": group, "outcome": outcome.replace("rel__", "signed_rel_error__"),
                         "parameter": n, "mechanism_group": jf.MECHANISM_GROUPS[n][0],
                         "n": int(ok.sum()), "n_missing": int((~ok).sum()), "spearman_rho": rho, "p_value_unadjusted": p})
    return pd.DataFrame(rows)


def surrogate_performance(summary: pd.DataFrame, params: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """Cross-validated R^2 of a random forest on log(mean/target). Hypothesis generation only."""
    from sklearn.ensemble import RandomForestRegressor  # noqa: PLC0415
    from sklearn.model_selection import KFold, cross_val_score  # noqa: PLC0415

    merged = summary.merge(params, on="design_id", validate="one_to_one")
    X = merged[names].to_numpy(dtype=float)
    rows = []
    for col in [c for c in summary.columns if c.startswith("rel__")]:
        y = np.log1p(merged[col].to_numpy(dtype=float))  # log(mean/target)
        model = RandomForestRegressor(n_estimators=300, min_samples_leaf=3, random_state=0, n_jobs=-1)
        cv = cross_val_score(model, X, y, cv=KFold(5, shuffle=True, random_state=0), scoring="r2")
        model.fit(X, y)
        top = np.argsort(-model.feature_importances_)[:5]
        rows.append({"target": col.replace("rel__", ""), "cv_r2_mean": float(cv.mean()), "cv_r2_min": float(cv.min()),
                     "top5_impurity_importance": ";".join(f"{names[i]}={model.feature_importances_[i]:.3f}" for i in top),
                     "directional_claims_allowed": bool(cv.mean() >= 0.5)})
    return pd.DataFrame(rows)


def _partial_rank(y: np.ndarray, x: np.ndarray, z: np.ndarray) -> float:
    from scipy.stats import rankdata  # noqa: PLC0415

    ry, rx, rz = rankdata(y), rankdata(x), rankdata(z)
    design = np.column_stack([np.ones_like(rz), rz])
    resid = lambda v: v - design @ np.linalg.lstsq(design, v, rcond=None)[0]  # noqa: E731
    return float(np.corrcoef(resid(ry), resid(rx))[0, 1])


def conditional_tradeoff(evaluated: pd.DataFrame, summary: pd.DataFrame, names: list[str]):
    """Level-conditioned shape evidence and incidence shape.

    Associations within the sampled design only; not causal effects, not acceptance.

    Rev 1 (2026-09-17):
    - "within a factor of 2" is now the multiplicative window [z/2, 2z] per anchor
      (``jf.within_factor``); rev 0 used |rel error| <= 1, i.e. [0, 2z].
    - WITHDRAWN: same-year "capture ratio" (recognition_t / onset_t) and "total onset
      rel to 318". Recognition in year t includes people whose AIDS onset was in earlier
      years, so the ratio compares different cohorts: it is not a capture fraction and
      same-year onset is not an upper bound on recognition. No valid cohort bound can be
      formed from the saved outputs (no AIDS stocks by diagnosis status, nothing pre-1990).
    - WITHDRAWN: global log-linear "OAT step screen". Regression coefficients across the
      sampled box are associations, not bounds on local effects or interactions.
    """
    q = lambda s: [float(v) for v in np.round(np.quantile(np.asarray(s, float), [0, .05, .25, .5, .75, .95, 1]), 3)]  # noqa: E731
    ev = evaluated.set_index("design_id").loc[summary.design_id]
    sm = summary.set_index("design_id")
    dx = lambda y: ev[f"new_diagnoses_msm_{y}"]  # noqa: E731
    aids = lambda y: ev[f"aids_first_diagnosis_proxy_msm_{y}"]  # noqa: E731
    inc = lambda y: ev[f"new_infections_msm_{y}"]  # noqa: E731
    out: dict = {}

    ir = inc(2010) / inc(2000)
    rising = ir > 1
    dx_rise_ok = (dx(2010) / dx(2000)) >= 0.7 * (761 / 360)
    out["incidence_msm_2010_over_2000"] = {
        "quantiles_0_5_25_50_75_95_100": q(ir), "n_rising": int(rising.sum()), "n_ge_1_5": int((ir >= 1.5).sum()),
        "rising_ids": [int(i) for i in ev.index[rising]],
        "rising_median_rel_error_dx2000": float((dx(2000)[rising] / 360 - 1).median()),
        "n_dx2010_over_dx2000_ge_70pct_of_observed": int(dx_rise_ok.sum()),
        "of_those_incidence_declining": int((dx_rise_ok & ~rising).sum()),
        "of_those_min_diagnosis_max_abs_rel_error": float(sm.loc[dx_rise_ok[dx_rise_ok].index, "max_abs_rel_error_diagnosis"].min()),
        "of_those_aids2000_over_dx2000_within_30pct": int((dx_rise_ok & (((aids(2000) / dx(2000)) / (107 / 360) - 1).abs() <= .3)).sum()),
        "of_those_median_msm_diag_mult_2004": float(ev.loc[dx_rise_ok, "msm_diag_mult_2004"].median()),
        "all_median_msm_diag_mult_2004": float(ev["msm_diag_mult_2004"].median()),
        "spearman_incidence_ratio_vs_diagnosis_ratio": float(pd.Series(ir).corr(dx(2010) / dx(2000), method="spearman")),
    }
    peak_years = np.arange(1990, 2025)
    incidence = np.column_stack([inc(y) for y in peak_years])
    out["incidence_msm_peak_year_from_1990_counts"] = pd.Series(peak_years[np.argmax(incidence, axis=1)]).value_counts().sort_index().to_dict()
    out["incidence_msm_peak_note"] = "Outputs before 1990 were not saved (scoring_start_year=1990); a 1990 peak means 'at or before 1990'."

    early_ok = sm["rel__rivm_aids_msm@1990"].abs() <= 0.3
    mid_ok = pd.Series(
        jf.within_factor(dx(1996), 370, 2.0) & jf.within_factor(dx(2000), 360, 2.0) & jf.within_factor(dx(2004), 572, 2.0),
        index=ev.index)
    mid_ok_rev0 = sm["max_abs_rel_error_middle_diagnosis"] <= 1.0
    out["aids1990_within_30pct"] = {
        "n": int(early_ok.sum()), "ids": [int(i) for i in sm.index[early_ok]],
        "middle_diagnosis_max_abs_rel_error_quantiles": q(sm.loc[early_ok, "max_abs_rel_error_middle_diagnosis"]),
        "middle_diagnosis_min_sim_over_observed_quantiles": q(np.minimum.reduce([
            dx(1996)[early_ok.values] / 370, dx(2000)[early_ok.values] / 360, dx(2004)[early_ok.values] / 572])),
        "later_diagnosis_max_abs_rel_error_quantiles": q(sm.loc[early_ok, "max_abs_rel_error_later_diagnosis"]),
    }
    near = {
        "definition": "each of SHM MSM Dx 1996, 2000, 2004 in [observed/2, 2*observed] (multiplicative)",
        "n": int(mid_ok.sum()), "ids": [int(i) for i in ev.index[mid_ok]],
        "rev0_definition_abs_rel_error_le_1_ids": [int(i) for i in sm.index[mid_ok_rev0]],
        "rev0_ids_excluded_by_correction": sorted(int(i) for i in set(sm.index[mid_ok_rev0]) - set(ev.index[mid_ok])),
    }
    rows = []
    for i in ev.index[mid_ok]:
        rows.append({"design_id": int(i), "source": sm.loc[i, "source"],
                     **{f"dx{y}_over_obs": float(dx(y).loc[i] / z) for y, z in ((1996, 370), (2000, 360), (2004, 572))},
                     **{f"aids{y}_over_obs": float(aids(y).loc[i] / z) for y, z in ((1990, 318), (1995, 314), (2000, 107))},
                     "aids1990_within_factor_2": bool(jf.within_factor(aids(1990).loc[i], 318, 2.0)),
                     "imax_joint_K5": float(sm.loc[i, "imax_joint"]), "binding_joint": sm.loc[i, "binding_joint"]})
    near["members"] = rows
    near["n_aids1990_within_factor_2"] = int(sum(r["aids1990_within_factor_2"] for r in rows))
    near["n_aids1990_below_half"] = int(sum(r["aids1990_over_obs"] < 0.5 for r in rows))
    out["middle_diagnoses_each_within_factor_2"] = near
    both = mid_ok & pd.Series(jf.within_factor(aids(1990), 318, 2.0), index=ev.index)
    out["middle_dx_and_aids1990_each_within_factor_2"] = {"n": int(both.sum()), "ids": [int(i) for i in ev.index[both]]}
    out["withdrawn_rev1"] = [
        "capture_ratio_1990 and aids1990_total_onset_rel_to_318 (different cohorts; not a capture fraction or bound)",
        "oat_step_screen (global regression associations are not limits on local or interaction effects)",
    ]

    mid = np.cbrt(dx(1996) * dx(2000) * dx(2004))
    early = np.sqrt(aids(1990) * aids(1995))
    later = np.sqrt(dx(2010) * dx(2015))
    z_mid = np.cbrt(370 * 360 * 572)
    s_early, s_later = np.log(early / mid), np.log(later / mid)
    d_early, d_later = np.log(np.sqrt(318 * 314) / z_mid), np.log(np.sqrt(761 * 582) / z_mid)
    two_sided = ((s_early - d_early).abs() <= np.log(1.3)) & ((s_later - d_later).abs() <= np.log(1.3))
    out["size_free_shapes"] = {
        "definitions": {"early_to_middle": "log(geomean AIDS 1990,1995 / geomean Dx 1996,2000,2004)",
                        "later_to_middle": "log(geomean Dx 2010,2015 / geomean Dx 1996,2000,2004)",
                        "level": "geomean Dx 1996,2000,2004 / observed geomean"},
        "observed": {"early_to_middle": float(d_early), "later_to_middle": float(d_later)},
        "model_early_to_middle_quantiles": q(s_early), "n_early_ge_observed": int((s_early >= d_early).sum()),
        "model_later_to_middle_quantiles": q(s_later), "n_later_ge_observed": int((s_later >= d_later).sum()),
        "n_both_within_factor_1_3": int(two_sided.sum()), "ids_both_within_factor_1_3": [int(i) for i in ev.index[two_sided]],
        "their_level_over_observed": [float(v) for v in np.round((mid / z_mid)[two_sided], 2)],
        "their_msm_diag_mult_2004": [float(v) for v in np.round(ev.loc[two_sided, "msm_diag_mult_2004"], 3)],
        "level_over_observed_quantiles_all": q(mid / z_mid),
        "n_level_within_factor_2": int(((mid / z_mid) <= 2).sum() - ((mid / z_mid) < 0.5).sum()),
    }
    bins = pd.cut(dx(1996) / 370, [0, 0.5, 1, 2, 4, 10, 30, 100, np.inf])
    ratio = aids(1990) / dx(1996)
    level_bins = pd.DataFrame({
        "n": ratio.groupby(bins, observed=False).size(),
        "aids1990_over_dx1996_median": ratio.groupby(bins, observed=False).median(),
        "aids1990_over_dx1996_max": ratio.groupby(bins, observed=False).max(),
        "incidence1996_over_1990_median": (inc(1996) / inc(1990)).groupby(bins, observed=False).median(),
        "median_msm_diag_mult_1996": ev["msm_diag_mult_1996"].groupby(bins, observed=False).median(),
    }).reset_index().rename(columns={"new_diagnoses_msm_1996": "dx1996_over_observed_bin"})
    level_bins["observed_aids1990_over_dx1996"] = 318 / 370
    level_bins["dx1996_over_observed_bin"] = level_bins["dx1996_over_observed_bin"].astype(str)

    lhs = (summary.set_index("design_id").loc[ev.index, "source"] == "lhs").to_numpy()
    lvl = np.log(mid.to_numpy()[lhs])
    partial = pd.DataFrame([{
        "parameter": n, "mechanism_group": jf.MECHANISM_GROUPS[n][0],
        "partial_rank_rho_early_to_middle_given_level": _partial_rank(s_early.to_numpy()[lhs], ev[n].to_numpy()[lhs], lvl),
        "partial_rank_rho_later_to_middle_given_level": _partial_rank(s_later.to_numpy()[lhs], ev[n].to_numpy()[lhs], lvl),
        "n": int(lhs.sum()),
    } for n in names]).sort_values("partial_rank_rho_early_to_middle_given_level", key=np.abs, ascending=False)

    return out, level_bins, partial


def make_plots(out: Path, summary: pd.DataFrame, long: pd.DataFrame, evaluated: pd.DataFrame, shortlist: list[int],
               register: tr.TargetRegister, raw: pd.DataFrame, confirm_summary: pd.DataFrame) -> list[str]:
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.colors import TwoSlopeNorm  # noqa: PLC0415

    files = []
    # 1. signed residual heatmap (shortlist)
    keys = [f"{jf.DIAGNOSIS_SERIES}@{y}" for y in jf.DIAGNOSIS_YEARS] + [f"{jf.AIDS_SERIES}@{y}" for y in jf.AIDS_YEARS]
    mat = summary.set_index("design_id").loc[shortlist, [f"rel__{k}" for k in keys]].to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(11, 0.45 * len(shortlist) + 2.2))
    im = ax.imshow(np.log2(1 + mat), cmap="RdBu_r", norm=TwoSlopeNorm(0, -3, 3), aspect="auto")
    ax.set_xticks(range(len(keys)), [k.replace("shm_new_diagnoses_msm@", "Dx ").replace("rivm_aids_msm@", "AIDS ") for k in keys],
                  rotation=60, ha="right", fontsize=8)
    labels = [f"{i} ({summary.set_index('design_id').loc[i, 'source']})" for i in shortlist]
    ax.set_yticks(range(len(shortlist)), labels, fontsize=8)
    for r in range(mat.shape[0]):
        for c in range(mat.shape[1]):
            ax.text(c, r, f"{mat[r, c]:+.0%}" if abs(mat[r, c]) < 10 else f"x{1 + mat[r, c]:.0f}",
                    ha="center", va="center", fontsize=6)
    ax.axvline(6.5, color="k", lw=1)
    fig.colorbar(im, ax=ax, label="log2(simulated mean / observed)")
    ax.set_title("Signed mean residuals, shortlist (wave 1, K=5 means). Cells: (mean-obs)/obs")
    fig.tight_layout()
    fig.savefig(out / "fig1_signed_residual_heatmap.png", dpi=150)
    plt.close(fig)
    files.append("fig1_signed_residual_heatmap.png")

    # 2. diagnosis vs AIDS max abs relative error
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    for src, marker, color in (("lhs", "o", "#7f7f7f"), ("supplementary", "^", "#d62728")):
        s = summary[summary.source == src]
        ax.scatter(s.max_abs_rel_error_diagnosis, s.max_abs_rel_error_aids, s=14 if src == "lhs" else 36,
                   marker=marker, c=color, alpha=0.6 if src == "lhs" else 0.9, label=f"{src} (n={len(s)})")
    front = summary[summary.pareto_all].sort_values("max_abs_rel_error_diagnosis")
    ax.plot(front.max_abs_rel_error_diagnosis, front.max_abs_rel_error_aids, "k-", lw=1, label="Pareto frontier (all)")
    for i in CONFIRMED_IDS:
        r = summary.set_index("design_id").loc[i]
        ax.scatter(r.max_abs_rel_error_diagnosis, r.max_abs_rel_error_aids, s=90, facecolors="none", edgecolors="b")
        ax.annotate(f"{i} K=5", (r.max_abs_rel_error_diagnosis, r.max_abs_rel_error_aids), fontsize=8, color="b")
        c = confirm_summary.set_index("design_id").loc[i]
        ax.scatter(c.max_abs_rel_error_diagnosis, c.max_abs_rel_error_aids, s=90, marker="s", facecolors="none", edgecolors="g")
        ax.annotate(f"{i} K=50", (c.max_abs_rel_error_diagnosis, c.max_abs_rel_error_aids), fontsize=8, color="g")
    for w in (0.1, 0.2, 0.3):
        ax.plot([1e-2, w, w], [w, w, 1e-2], ":", lw=0.8, color="purple")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("max |mean-obs|/obs, 7 SHM MSM diagnosis anchors")
    ax.set_ylabel("max |mean-obs|/obs, 8 RIVM MSM AIDS anchors")
    ax.set_title("Mean-error trade-off (descriptive; dotted = ±10/20/30% boxes)")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "fig2_diagnosis_vs_aids_error.png", dpi=150)
    plt.close(fig)
    files.append("fig2_diagnosis_vs_aids_error.png")

    # 3. trajectories
    obs = {s: {t.year: t.z for t in register.targets if t.series_name == s} for s in SERIES}
    scored = {jf.DIAGNOSIS_SERIES: jf.DIAGNOSIS_YEARS, jf.AIDS_SERIES: jf.AIDS_YEARS}
    outputs = {jf.DIAGNOSIS_SERIES: "new_diagnoses_msm", jf.AIDS_SERIES: "aids_first_diagnosis_proxy_msm"}
    years = np.arange(1990, 2025)
    ev = evaluated.set_index("design_id")
    fig, axes = plt.subplots(2, 1, figsize=(10, 9), sharex=True)
    cmap = plt.get_cmap("tab10")
    for ax, series in zip(axes, SERIES):
        o = outputs[series]
        for j, i in enumerate([i for i in shortlist if i not in CONFIRMED_IDS]):
            m = np.array([ev.loc[i, f"{o}_{y}"] for y in years])
            sd = np.sqrt([ev.loc[i, f"mcvar__{o}_{y}"] for y in years])
            ax.plot(years, m, color=cmap(j % 10), lw=1.2, label=f"{i} mean (K=5)")
            ax.fill_between(years, np.maximum(m - sd, 1), m + sd, color=cmap(j % 10), alpha=0.08)
        for i, color in zip(CONFIRMED_IDS, ("navy", "darkgreen")):
            g = raw[raw.source_design_id == i]
            vals = np.stack([g[f"{o}_{y}"].to_numpy(float) for y in years], axis=1)
            ax.plot(years, vals.mean(0), color=color, lw=2, ls="--", label=f"{i} mean (K=50 fresh seeds)")
            ax.fill_between(years, np.quantile(vals, .05, axis=0), np.quantile(vals, .95, axis=0), color=color, alpha=0.12,
                            label=f"{i} 5-95% of 50 seed trajectories")
        oy = sorted(obs[series])
        ax.plot(oy, [obs[series][y] for y in oy], "k.", ms=4, label="register annual values (not all scored)")
        ax.plot(scored[series], [obs[series][y] for y in scored[series]], "ko", mfc="none", ms=8, label="scored anchors")
        ax.set_yscale("log")
        ax.set_ylabel("MSM diagnoses / yr" if series == jf.DIAGNOSIS_SERIES else "MSM AIDS proxy / yr")
        ax.set_title(series)
    axes[0].legend(fontsize=6.5, ncol=3, loc="upper right")
    fig.suptitle("Trajectories: faint bands for K=5 vectors are ±1 per-run SD from saved sample variance "
                 "(predictive spread, not a CI); K=5 raw trajectories were not saved", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "fig3_shortlist_trajectories.png", dpi=150)
    plt.close(fig)
    files.append("fig3_shortlist_trajectories.png")
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--bootstrap-draws", type=int, default=BOOTSTRAP_DRAWS)
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"REFUSING to write into non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    summary_json: dict = {"generated_utc": datetime.now(timezone.utc).isoformat(),
                          "status": "existing-evidence diagnostic; no simulations; no acceptance/NROY change"}

    # ---------------- Phase A
    ctx = phase_a(out)
    summary_json["phase_a_lineage"] = ctx["report"]
    manifest = ctx["manifest"]
    register = tr.load_register(Path(manifest["input_provenance"]["target_register"]["path"]))
    repro, conf = score_reproduction(ctx, register)
    repro.to_csv(out / "a2_score_reproduction.csv", index=False)
    summary_json["phase_a_score_reproduction"] = conf["info"]
    targets = register.restrict_years([t for t in register.calibration_targets() if t.series_name in SERIES],
                                      manifest["scored_years"])
    anchors = jf.anchors_from_register(targets)
    if len(anchors) != 15:
        raise SystemExit("Expected 15 joint anchors")
    evaluated = ctx["evaluated"].copy()
    evaluated["source"] = ctx["sources"].set_index("design_row").loc[evaluated.design_id, "source"].to_numpy()
    names = ctx["names"]

    # ---------------- B1 residual tables
    tables = {}
    for allowance, label in ((jf.PRIMARY_DISCREPANCY, "primary"), (jf.SENSITIVITY_DISCREPANCY, "sensitivity20")):
        long = jf.residual_table(evaluated, anchors, replicates=5, discrepancy=allowance, extra_columns=["source"])
        s = rhm.score_wave(evaluated, register, SERIES, disc_multiplier=allowance, disc_method="relative_to_target",
                           ensemble_mode="predictive_local", replicates=5, scored_years=manifest["scored_years"])
        wide = long.pivot(index="design_id", columns="target_key", values="implausibility")[[t.key for t in s["targets"]]]
        diff = float(np.abs(wide.to_numpy() - s["matrix"]).max())
        if diff > 1e-9:
            raise SystemExit(f"Residual-table implausibility differs from score_wave by {diff}")
        summary_json[f"b1_residual_table_matches_score_wave_{label}"] = diff
        tables[label] = (long, s)
    long, joint_primary = tables["primary"]
    long.to_csv(out / "b1_target_residuals_wave1_primary.csv", index=False)
    tables["sensitivity20"][0].to_csv(out / "b1_target_residuals_wave1_sensitivity20.csv", index=False)

    confirmed_means = conf["confirmed_means"]
    long_c = jf.residual_table(confirmed_means, anchors, replicates=50, discrepancy=jf.PRIMARY_DISCREPANCY)
    long_c.to_csv(out / "b1_target_residuals_confirmation_K50_primary.csv", index=False)

    # Hand check (A2): one denominator per confirmed vector, every component, units.
    hand = []
    for vid, key in ((600, "rivm_aids_msm@1990"), (621, "shm_new_diagnoses_msm@2015")):
        a = next(x for x in anchors if x.key == key)
        g = ctx["raw"][ctx["raw"].source_design_id == vid][a.output_column].to_numpy(float)
        k = len(g)
        mean = g.sum() / k
        s2 = ((g - mean) ** 2).sum() / (k - 1)
        vdisc = (0.1 * a.z) ** 2
        total = a.v_obs + s2 + s2 / k + vdisc
        imp = abs(mean - a.z) / np.sqrt(total)
        saved = pd.read_csv(CONFIRM / "implausibility_joint_0.1.csv").set_index("design_id").loc[vid, key]
        hand.append({"design_id": vid, "target": key, "units_count": "persons per calendar year",
                     "units_variance": "persons^2", "K": k, "observed_z": a.z, "mean_by_hand": mean,
                     "s2_ddof1_by_hand": s2, "sd_by_hand": np.sqrt(s2), "v_obs": a.v_obs, "v_code": 0.0,
                     "v_process_s2": s2, "v_mc_s2_over_K": s2 / k, "v_disc_(0.1*z)^2": vdisc,
                     "total_variance": total, "sigma": np.sqrt(total), "I_by_hand": imp, "I_saved": saved,
                     "abs_diff": abs(imp - saved)})
    pd.DataFrame(hand).to_csv(out / "a2_hand_checked_denominators.csv", index=False)
    summary_json["a2_hand_check_max_abs_diff"] = float(max(h["abs_diff"] for h in hand))

    # ---------------- B2 shape
    summary = jf.vector_summary(long)
    summary = summary.merge(evaluated[["design_id", "source"]], on="design_id", validate="one_to_one")
    summary["pareto_all"] = jf.pareto_mask(summary.max_abs_rel_error_diagnosis, summary.max_abs_rel_error_aids)
    summary["pareto_within_source"] = False
    for src, idx in summary.groupby("source").groups.items():
        summary.loc[idx, "pareto_within_source"] = jf.pareto_mask(
            summary.loc[idx, "max_abs_rel_error_diagnosis"], summary.loc[idx, "max_abs_rel_error_aids"])
    sens = jf.vector_summary(tables["sensitivity20"][0])[["design_id", "imax_diagnosis", "imax_aids", "imax_joint"]]
    summary = summary.merge(sens.rename(columns=lambda c: c if c == "design_id" else f"{c}_disc20"), on="design_id")
    for i in CONFIRMED_IDS:
        r = summary.set_index("design_id").loc[i]
        summary[f"strictly_better_both_blocks_than_{i}_K5"] = jf.dominates_reference(
            summary, r.max_abs_rel_error_diagnosis, r.max_abs_rel_error_aids)
    confirm_summary = jf.vector_summary(long_c)
    summary.to_csv(out / "b2_vector_summary_wave1.csv", index=False)
    confirm_summary.to_csv(out / "b2_vector_summary_confirmation_K50.csv", index=False)
    windows = jf.window_counts(summary, group_col="source")
    windows.to_csv(out / "b2_window_counts.csv", index=False)
    over_under = pd.concat([jf.over_under_counts(long).assign(group="all"),
                            jf.over_under_counts(long, "source").rename(columns={"source": "group"})], ignore_index=True)
    over_under.to_csv(out / "b2_over_under_counts.csv", index=False)
    pareto = summary[summary.pareto_all | summary.pareto_within_source].sort_values("max_abs_rel_error_diagnosis")
    pareto.to_csv(out / "b2_pareto_members.csv", index=False)
    summary_json["b2"] = {
        "joint_primary_nroy_retrospective": int(joint_primary["mask"].sum()),
        "joint_sensitivity20_nroy_retrospective": int(tables["sensitivity20"][1]["mask"].sum()),
        "min_joint_imax_primary": float(summary.imax_joint.min()),
        "argmin_joint_imax_primary": int(summary.loc[summary.imax_joint.idxmin(), "design_id"]),
        "min_joint_imax_disc20": float(summary.imax_joint_disc20.min()),
        "min_imax_aids_primary": float(summary.imax_aids.min()),
        "n_imax_aids_le_3_primary": int((summary.imax_aids <= 3).sum()),
        "n_imax_diagnosis_le_3_primary": int((summary.imax_diagnosis <= 3).sum()),
        "pareto_all_ids": summary.loc[summary.pareto_all, "design_id"].tolist(),
        "pareto_lhs_ids": summary.loc[summary.pareto_within_source & (summary.source == "lhs"), "design_id"].tolist(),
        "pareto_supplementary_ids": summary.loc[summary.pareto_within_source & (summary.source == "supplementary"), "design_id"].tolist(),
        **{f"n_strictly_better_both_than_{i}_K5": int(summary[f"strictly_better_both_blocks_than_{i}_K5"].sum())
           for i in CONFIRMED_IDS},
        "quantiles_max_abs_rel_error": {b: summary[f"max_abs_rel_error_{b}"].quantile([0, .05, .5, .95]).round(3).tolist()
                                        for b in ("diagnosis", "aids", "joint")},
    }

    # shape ratios: data vs model distribution
    ev = evaluated.set_index("design_id")
    ratio_defs = {
        "aids1990_over_dx1996": (("aids_first_diagnosis_proxy_msm_1990", 318), ("new_diagnoses_msm_1996", 370)),
        "aids1990_over_dx2000": (("aids_first_diagnosis_proxy_msm_1990", 318), ("new_diagnoses_msm_2000", 360)),
        "aids1995_over_aids1990": (("aids_first_diagnosis_proxy_msm_1995", 314), ("aids_first_diagnosis_proxy_msm_1990", 318)),
        "dx2010_over_dx2000": (("new_diagnoses_msm_2010", 761), ("new_diagnoses_msm_2000", 360)),
        "dx2015_over_dx2000": (("new_diagnoses_msm_2015", 582), ("new_diagnoses_msm_2000", 360)),
        "dx2024_over_dx2015": (("new_diagnoses_msm_2024", 318), ("new_diagnoses_msm_2015", 582)),
        "aids2000_over_dx2000": (("aids_first_diagnosis_proxy_msm_2000", 107), ("new_diagnoses_msm_2000", 360)),
        "aids2010_over_dx2010": (("aids_first_diagnosis_proxy_msm_2010", 128), ("new_diagnoses_msm_2010", 761)),
    }
    ratio_rows, ratio_ok = [], {}
    for name, ((num, zn), (den, zd)) in ratio_defs.items():
        model = ev[num] / ev[den]
        data = zn / zd
        ratio_ok[name] = (model / data - 1).abs() <= 0.3
        for src in ("all", "lhs", "supplementary"):
            m = model if src == "all" else model[evaluated.set_index("design_id").source == src]
            ratio_rows.append({"ratio": name, "group": src, "observed_ratio": data, "n": len(m),
                               "model_p05": m.quantile(.05), "model_median": m.median(), "model_p95": m.quantile(.95),
                               "model_max": m.max(), "n_model_ge_observed": int((m >= data).sum()),
                               "n_within_30pct_of_observed": int(((m / data - 1).abs() <= 0.3).sum())})
    ratios = pd.DataFrame(ratio_rows)
    ok = pd.DataFrame(ratio_ok)
    ratios.to_csv(out / "b2_shape_ratios.csv", index=False)
    summary_json["b2_shape_ratio_intersections_within_30pct"] = {
        "early_aids_to_middle_dx_and_dx_rise_2000_2010": int((ok.aids1990_over_dx1996 & ok.dx2010_over_dx2000).sum()),
        "dx_rise_2000_2010_and_2000_2015": int((ok.dx2010_over_dx2000 & ok.dx2015_over_dx2000).sum()),
        "all_three_early_aids_dx1996_dx2010rise_dx2015rise": int((ok.aids1990_over_dx1996 & ok.dx2010_over_dx2000 & ok.dx2015_over_dx2000).sum()),
        "all_ratios": int(ok.all(axis=1).sum()),
    }

    # ---------------- B3 ranking and bootstrap
    ranks = {
        "joint_imax_vs_joint_max_abs_rel_error": jf.rank_comparison(summary, "imax_joint", "max_abs_rel_error_joint"),
        "diagnosis_imax_vs_diagnosis_max_abs_rel_error": jf.rank_comparison(summary, "imax_diagnosis", "max_abs_rel_error_diagnosis"),
    }
    best = []
    for rank_col in ("imax_joint", "max_abs_rel_error_joint", "imax_diagnosis", "max_abs_rel_error_diagnosis"):
        for pos, (_, r) in enumerate(summary.nsmallest(10, rank_col).iterrows(), start=1):
            block = "diagnosis" if "diagnosis" in rank_col else "joint"
            best.append({"ranking": rank_col, "position": pos, "design_id": r.design_id, "source": r.source,
                         "imax_block": r[f"imax_{block}"], "max_abs_rel_error_block": r[f"max_abs_rel_error_{block}"],
                         "binding_target": r[f"binding_{block}"],
                         "binding_target_v_ensemble_fraction": r[f"binding_{block}_frac_v_ensemble"]})
    pd.DataFrame(best).to_csv(out / "b3_top10_rankings.csv", index=False)
    q10 = summary.imax_joint.quantile(0.10)
    low_std_large_abs = summary[(summary.imax_joint <= q10) & (summary.max_abs_rel_error_joint >= 1.0)]
    ranks["n_joint_imax_bottom_decile"] = int((summary.imax_joint <= q10).sum())
    ranks["n_bottom_decile_with_some_anchor_error_ge_100pct"] = len(low_std_large_abs)
    dx_q = summary.imax_diagnosis.quantile(0.10)
    ranks["n_diag_imax_bottom_decile_with_diag_anchor_error_ge_100pct"] = int(
        ((summary.imax_diagnosis <= dx_q) & (summary.max_abs_rel_error_diagnosis >= 1.0)).sum())
    summary_json["b3_rank_comparison"] = ranks

    boot = jf.bootstrap_whole_trajectories(ctx["raw"], anchors, vector_ids=list(CONFIRMED_IDS), k_values=BOOTSTRAP_K,
                                           n_boot=args.bootstrap_draws, seed=BOOTSTRAP_SEED)
    boot.to_csv(out / "b3_bootstrap_draws.csv.gz", index=False, compression="gzip")
    agg = boot.groupby(["k", "source_design_id"]).agg(
        pass_diagnosis_freq=("pass_diagnosis", "mean"), pass_joint_freq=("pass_joint", "mean"),
        imax_diagnosis_p05=("imax_diagnosis", lambda s: s.quantile(.05)),
        imax_diagnosis_median=("imax_diagnosis", "median"),
        imax_diagnosis_p95=("imax_diagnosis", lambda s: s.quantile(.95)),
        imax_joint_p05=("imax_joint", lambda s: s.quantile(.05)), imax_joint_median=("imax_joint", "median"),
        imax_joint_p95=("imax_joint", lambda s: s.quantile(.95)),
        binding_joint_modal=("binding_joint", lambda s: s.mode().iloc[0]),
        binding_joint_modal_freq=("binding_joint", lambda s: s.value_counts(normalize=True).iloc[0]),
    ).reset_index()
    wide = boot.pivot_table(index=["k", "draw"], columns="source_design_id", values=["imax_diagnosis", "imax_joint"])
    paired = []
    for k in BOOTSTRAP_K:
        w = wide.loc[k]
        for block in ("diagnosis", "joint"):
            d = w[(f"imax_{block}", 621)] - w[(f"imax_{block}", 600)]
            paired.append({"k": k, "block": block, "median_621_minus_600": d.median(),
                           "p025": d.quantile(.025), "p975": d.quantile(.975), "freq_621_lower": float((d < 0).mean())})
    agg.to_csv(out / "b3_bootstrap_summary.csv", index=False)
    pd.DataFrame(paired).to_csv(out / "b3_bootstrap_paired_600_vs_621.csv", index=False)
    summary_json["b3_bootstrap"] = {"draws": args.bootstrap_draws, "seed": BOOTSTRAP_SEED, "k_values": list(BOOTSTRAP_K),
                                    "note": ("Resamples whole 50-seed trajectories; the empirical 50-seed distribution is itself "
                                             "uncertain. Pass frequencies are NOT probabilities of model validity.")}

    # original five vs fresh fifty
    o5 = long[long.design_id.isin(CONFIRMED_IDS)].set_index(["design_id", "target_key"])
    f50 = long_c.set_index(["design_id", "target_key"])
    cols = ["sim_mean", "sample_sd", "v_process_s2", "v_mc_s2_over_k", "v_disc", "v_obs", "total_variance", "implausibility"]
    cmp_ = o5[cols].add_suffix("_K5").join(f50[cols].add_suffix("_K50"))
    cmp_["mean_change"] = cmp_.sim_mean_K50 - cmp_.sim_mean_K5
    cmp_["sd_ratio_K50_over_K5"] = cmp_.sample_sd_K50 / cmp_.sample_sd_K5
    cmp_["total_variance_ratio"] = cmp_.total_variance_K50 / cmp_.total_variance_K5
    cmp_.reset_index().to_csv(out / "b3_original5_vs_fresh50.csv", index=False)

    # ---------------- B4 mechanisms
    mech = jf.mechanism_table(names)
    mech.to_csv(out / "b4_parameter_mechanism_map.csv", index=False)
    params = evaluated[["design_id", *names]]
    corr = pd.concat([
        mechanism_correlations(summary, params, names, "all"),
        mechanism_correlations(summary[summary.source == "lhs"], params, names, "lhs"),
        mechanism_correlations(summary[summary.source == "supplementary"], params, names, "supplementary_n22_unreliable"),
    ], ignore_index=True)
    corr.to_csv(out / "b4_rank_correlations.csv", index=False)
    surrogate = surrogate_performance(summary[summary.source == "lhs"], params, names)
    surrogate.to_csv(out / "b4_surrogate_cv_performance_lhs.csv", index=False)
    bounds = {p["name"]: (float(p["min"]), float(p["max"])) for p in ctx["space"]["parameters"]}
    occ = pd.concat([jf.boundary_occupancy(evaluated[evaluated.source == s], bounds).assign(group=s)
                     for s in ("lhs", "supplementary")], ignore_index=True)
    occ.to_csv(out / "b4_boundary_occupancy.csv", index=False)

    # Q&A evidence
    qa: dict = {}
    dx_fav = summary.max_abs_rel_error_diagnosis <= summary.max_abs_rel_error_diagnosis.quantile(0.10)
    a90 = summary["rel__rivm_aids_msm@1990"]
    a95 = summary["rel__rivm_aids_msm@1995"]
    qa["q1_early_aids_under"] = {
        "all_n": len(summary), "all_n_under_1990": int((a90 < 0).sum()), "all_n_under_1995": int((a95 < 0).sum()),
        "diag_favourable_def": "best decile of max |rel error| over the 7 diagnosis anchors",
        "diag_favourable_n": int(dx_fav.sum()),
        "diag_favourable_n_under_1990": int((a90[dx_fav] < 0).sum()),
        "diag_favourable_n_under_1995": int((a95[dx_fav] < 0).sum()),
        "diag_favourable_median_rel_1990": float(a90[dx_fav].median()),
        "rest_median_rel_1990": float(a90[~dx_fav].median()),
        "n_1990_within_30pct_all": int((a90.abs() <= .3).sum()),
    }
    d00 = summary["rel__shm_new_diagnoses_msm@2000"]
    d15 = summary["rel__shm_new_diagnoses_msm@2015"]
    q2 = {"n_d2000_under_and_d2015_over_observed": int(((d00 < 0) & (d15 > 0)).sum()),
          "n_d2000_le_plus30_and_d2015_ge_minus30": int(((d00 <= .3) & (d15 >= -.3)).sum())}
    for i in CONFIRMED_IDS:
        r = summary.set_index("design_id").loc[i]
        better = (d00 < r["rel__shm_new_diagnoses_msm@2000"]) & (d15 > r["rel__shm_new_diagnoses_msm@2015"])
        q2[f"n_lower_d2000_and_higher_d2015_than_{i}_K5"] = int(better.sum())
        q2[f"of_those_also_d2000_rel_le_1_{i}"] = int((better & (d00 <= 1)).sum())
    from scipy.stats import spearmanr  # noqa: PLC0415
    q2["spearman_rel_d2000_vs_rel_d2015_all"] = float(spearmanr(d00, d15)[0])
    q2["spearman_log_ratio_d2015_over_d2000_vs_rel_d2000"] = float(
        spearmanr(np.log((1 + d15) * 582 / ((1 + d00) * 360)), d00)[0])
    qa["q2_d2000_down_d2015_up"] = q2
    early_err = summary.max_abs_rel_error_early_aids
    qa["q3_early_aids_vs_diagnosis"] = {
        "spearman_early_aids_maxerr_vs_diag_maxerr_all": float(spearmanr(early_err, summary.max_abs_rel_error_diagnosis)[0]),
        "spearman_signed_early_aids_vs_signed_middle_dx": float(spearmanr(summary.mean_signed_rel_error_early_aids, summary.mean_signed_rel_error_middle_diagnosis)[0]),
        "n_early_aids_within_30pct": int((early_err <= .3).sum()),
        "diag_maxerr_median_when_early_aids_within_30pct": float(summary.max_abs_rel_error_diagnosis[early_err <= .3].median()) if (early_err <= .3).any() else None,
        "diag_maxerr_min_when_early_aids_within_30pct": float(summary.max_abs_rel_error_diagnosis[early_err <= .3].min()) if (early_err <= .3).any() else None,
        "diag_maxerr_median_otherwise": float(summary.max_abs_rel_error_diagnosis[early_err > .3].median()),
        "early_aids_maxerr_median_in_diag_favourable": float(early_err[dx_fav].median()),
        "early_aids_maxerr_min_in_diag_favourable": float(early_err[dx_fav].min()),
    }
    qa["q5_chronology_negative_control"] = corr[(corr.group == "lhs") & (corr.outcome == "signed_rel_error__rivm_aids_msm@1990")
                                               & corr.parameter.isin(["chronic_to_aids_on_art_early", "chronic_to_aids_on_art_recent",
                                                                      "art_init_1996", "art_mortality_reduction", "art_dropout_rate"])
                                               ][["parameter", "spearman_rho", "p_value_unadjusted"]].to_dict("records")
    joint_binding = summary.binding_joint.value_counts()
    per_target_gt3 = long.groupby("target_key").implausibility.apply(lambda s: int((s > 3).sum()))
    qa["q6_localisation"] = {"joint_binding_counts": joint_binding.to_dict(),
                             "n_vectors_with_I_gt_3_by_target": per_target_gt3.to_dict(),
                             "n_targets_gt3_per_vector_quantiles": long.assign(gt=long.implausibility > 3).groupby("design_id").gt.sum().quantile([0, .1, .5, .9, 1]).tolist()}
    tradeoff, level_bins, partial = conditional_tradeoff(evaluated, summary, names)
    level_bins.to_csv(out / "b4_early_ratio_by_diagnosis_level.csv", index=False)
    partial.to_csv(out / "b4_level_adjusted_shape_partial_rank_correlations_lhs.csv", index=False)
    summary_json["b4_conditional_tradeoff"] = tradeoff
    drops = {"none": []}
    drops.update({f"drop_{a.key}": [a.key] for a in anchors})
    drops.update({f"drop_period_{p}": list(keys) for p, keys in jf.PERIODS.items()})
    loo = jf.leave_out_rescore(long, drops)
    loo.to_csv(out / "b4_leave_out_rescoring_EXPLANATORY_ONLY.csv", index=False)
    summary_json["b4_answers_evidence"] = qa

    # shortlist (declared rule; see docstring in FINDINGS)
    shortlist: list[int] = []
    def add(ids):
        for i in ids:
            if int(i) not in shortlist:
                shortlist.append(int(i))
    add(summary.nsmallest(3, "max_abs_rel_error_joint").design_id)
    add(summary[summary.pareto_all].nsmallest(8, "max_abs_rel_error_joint").design_id)
    add(summary.nsmallest(3, "imax_joint").design_id)
    add(CONFIRMED_IDS)
    rule = ("union, in order: best 3 by joint max|rel err|; Pareto(all) members, best 8 by joint max|rel err|; "
            "best 3 by primary joint I_max (K=5); reference IDs 600, 621")
    sl = summary.set_index("design_id").loc[shortlist].reset_index()
    near = {}
    for i in shortlist:
        row = evaluated.set_index("design_id").loc[i]
        near[i] = ";".join(f"{n}@{'min' if (row[n]-lo)/(hi-lo) <= .05 else 'max'}" for n, (lo, hi) in bounds.items()
                           if (row[n]-lo)/(hi-lo) <= .05 or (row[n]-lo)/(hi-lo) >= .95)
    sl["parameters_within_5pct_of_bound"] = sl.design_id.map(near)
    sl.insert(1, "selection_rule", rule)
    sl.to_csv(out / "b2_shortlist.csv", index=False)
    summary_json["shortlist"] = {"rule": rule, "ids": shortlist, "near_bounds": near}

    files = make_plots(out, summary, long, evaluated, shortlist, register, ctx["raw"], confirm_summary)
    summary_json["figures"] = files
    (out / "summary.json").write_text(json.dumps(jsonable(summary_json), indent=2) + "\n")
    artefacts = [{"path": str(p.relative_to(out)), "bytes": p.stat().st_size, "sha256": sha(p)}
                 for p in sorted(out.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    (out / "manifest.json").write_text(json.dumps({
        "generated_utc": summary_json["generated_utc"], "script": "tools/analyze_joint_fit_conflict_20260917.py",
        "script_sha256": sha(Path(__file__)), "helper_sha256": sha(REPO / "hm_joint_fit_conflict.py"),
        "source_manifest_sha256": sha(SOURCE / "manifest.json"), "confirmation_manifest_sha256": sha(CONFIRM / "manifest.json"),
        "runtime": rhm.repository_provenance(), "simulations_run": 0, "artefacts": artefacts}, indent=2) + "\n")
    print(json.dumps(jsonable({k: summary_json[k] for k in ("phase_a_score_reproduction", "b2", "shortlist")}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
