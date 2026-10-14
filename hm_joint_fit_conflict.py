"""Diagnostic helpers for the 2026-09-17 joint diagnoses/AIDS fit-conflict investigation.

Pure functions only (no I/O, no simulation). Nothing here is an acceptance rule:
the primary rule stays ``run_history_matching.score_wave`` (max implausibility,
cutoff 3, target-relative discrepancy 0.1, predictive-local V_ensemble).  The
residual table reproduces that rule so that it can be decomposed and audited;
``tests/test_hm_joint_fit_conflict_20260917.py`` checks it matches ``score_wave``.

Conventions (verified against the code, 2026-09-17):

- ``mcvar__<output>`` is the ddof=1 sample VARIANCE s2(x) of K replicate values
  (``run_history_matching._evaluate_worker``), not a standard deviation.
- V_ensemble(x) = s2(x) + s2(x)/K  (``pointwise_predictive_variance``).
- V_disc = (f * |z|)^2 with f = 0.1 primary, 0.2 sensitivity
  (``hm_variance_budget.v_disc_relative_to_target``; f is an SD fraction).
- V_obs is the register value (zero for 13 of the 15 joint anchors; SHM 2019 = 4
  and SHM 2024 = 1369 from the delay-adjustment rule).  Zero means unavailable,
  not error-free.
- V_code = 0 (no emulator).
- I = |mean - z| / sqrt(V_obs + V_ensemble + V_disc).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

PRIMARY_DISCREPANCY = 0.1
SENSITIVITY_DISCREPANCY = 0.2
CUTOFF = 3.0

DIAGNOSIS_SERIES = "shm_new_diagnoses_msm"
AIDS_SERIES = "rivm_aids_msm"
DIAGNOSIS_YEARS = (1996, 2000, 2004, 2010, 2015, 2019, 2024)
AIDS_YEARS = (1990, 1995, 2000, 2005, 2010, 2015, 2019, 2022)

#: Prespecified by the brief (Phase B2). Keys are target keys.
PERIODS: dict[str, tuple[str, ...]] = {
    "early_aids": tuple(f"{AIDS_SERIES}@{y}" for y in (1990, 1995)),
    "middle_diagnosis": tuple(f"{DIAGNOSIS_SERIES}@{y}" for y in (1996, 2000, 2004)),
    "later_diagnosis": tuple(f"{DIAGNOSIS_SERIES}@{y}" for y in (2010, 2015, 2019, 2024)),
    "later_aids": tuple(f"{AIDS_SERIES}@{y}" for y in (2000, 2005, 2010, 2015, 2019, 2022)),
}


class DataContractError(ValueError):
    """Raised when inputs would silently drop, duplicate or invent information."""


@dataclass(frozen=True)
class Anchor:
    key: str
    series: str
    block: str  # "diagnosis" | "aids"
    output_column: str
    z: float
    v_obs: float


def anchors_from_register(targets: Iterable) -> list[Anchor]:
    """Convert resolved ``hm_target_register.ScalarTarget`` objects to anchors."""
    out = []
    for t in targets:
        if t.series_name == DIAGNOSIS_SERIES:
            block = "diagnosis"
        elif t.series_name == AIDS_SERIES:
            block = "aids"
        else:
            raise DataContractError(f"Unexpected series in joint block: {t.series_name}")
        out.append(Anchor(t.key, t.series_name, block, f"{t.model_output}_{t.year}",
                          float(t.z), float(t.v_obs.variance)))
    return out


# ---------------------------------------------------------------------------
# Joins and fail-closed checks
# ---------------------------------------------------------------------------

def require_unique(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise DataContractError(f"{label}: missing key columns {missing}")
    duplicated = frame.duplicated(list(columns), keep=False)
    if duplicated.any():
        examples = frame.loc[duplicated, list(columns)].head(5).to_dict("records")
        raise DataContractError(f"{label}: duplicate keys {examples}")


def require_finite_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    """Every requested column must exist and be finite in every row. Never drop."""
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise DataContractError(f"{label}: missing required columns {missing[:10]}")
    values = frame[list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    bad = ~np.isfinite(values)
    if bad.any():
        rows, cols = np.nonzero(bad)
        examples = [(int(r), columns[c]) for r, c in list(zip(rows, cols))[:5]]
        raise DataContractError(f"{label}: non-finite values at (row, column) {examples}")


def join_on_id(left: pd.DataFrame, right: pd.DataFrame, id_col: str, label: str) -> pd.DataFrame:
    """One-to-one join that refuses duplicates and unmatched IDs (order-independent)."""
    require_unique(left, [id_col], f"{label} (left)")
    require_unique(right, [id_col], f"{label} (right)")
    lset, rset = set(left[id_col]), set(right[id_col])
    if lset != rset:
        raise DataContractError(
            f"{label}: ID sets differ (left-only {sorted(lset - rset)[:5]}, right-only {sorted(rset - lset)[:5]})"
        )
    return left.merge(right, on=id_col, how="inner", validate="one_to_one").sort_values(id_col).reset_index(drop=True)


# ---------------------------------------------------------------------------
# B1: residual and variance table
# ---------------------------------------------------------------------------

def residual_table(
    frame: pd.DataFrame,
    anchors: Sequence[Anchor],
    *,
    replicates: int | pd.Series,
    discrepancy: float = PRIMARY_DISCREPANCY,
    id_col: str = "design_id",
    extra_columns: Sequence[str] = (),
) -> pd.DataFrame:
    """Long table: one row per (vector, anchor) with every variance component.

    ``replicates`` is K (scalar or per-row series).  Fails closed on missing or
    non-finite means/variances, non-positive total variance, K < 2 and duplicate IDs.
    """
    require_unique(frame, [id_col], "residual_table")
    needed = [a.output_column for a in anchors] + [f"mcvar__{a.output_column}" for a in anchors]
    require_finite_columns(frame, needed, "residual_table")
    k = (pd.to_numeric(frame[replicates.name], errors="coerce") if isinstance(replicates, pd.Series)
         else pd.Series(float(replicates), index=frame.index)).to_numpy(dtype=float)
    if not np.all(np.isfinite(k)) or np.any(k < 2):
        raise DataContractError("predictive-local variance needs K >= 2 for every vector")
    if not (np.isfinite(discrepancy) and discrepancy >= 0):
        raise DataContractError(f"discrepancy fraction must be finite and >= 0, got {discrepancy}")

    parts = []
    for anchor in anchors:
        mean = frame[anchor.output_column].to_numpy(dtype=float)
        s2 = frame[f"mcvar__{anchor.output_column}"].to_numpy(dtype=float)
        if np.any(s2 < 0):
            raise DataContractError(f"negative sample variance for {anchor.key}")
        v_process = s2
        v_mc = s2 / k
        v_ens = v_process + v_mc
        v_disc = (discrepancy * abs(anchor.z)) ** 2
        v_obs = anchor.v_obs
        total = v_obs + v_ens + v_disc
        if np.any(~np.isfinite(total)) or np.any(total <= 0):
            raise DataContractError(f"non-positive total variance for {anchor.key}")
        error = mean - anchor.z
        nonpositive_target = anchor.z <= 0
        rel = np.full_like(mean, np.nan) if nonpositive_target else error / anchor.z
        part = pd.DataFrame({
            id_col: frame[id_col].to_numpy(),
            "target_key": anchor.key,
            "series": anchor.series,
            "block": anchor.block,
            "observed": anchor.z,
            "target_nonpositive": nonpositive_target,
            "replicates_k": k,
            "sim_mean": mean,
            "signed_error": error,
            "signed_rel_error": rel,
            "abs_rel_error": np.abs(rel),
            "sample_variance_s2": s2,
            "sample_sd": np.sqrt(s2),
            "mc_se_of_mean": np.sqrt(s2 / k),
            "v_obs": v_obs,
            "v_code": 0.0,
            "v_process_s2": v_process,
            "v_mc_s2_over_k": v_mc,
            "v_ensemble": v_ens,
            "v_disc": v_disc,
            "total_variance": total,
            "frac_v_obs": v_obs / total,
            "frac_v_process": v_process / total,
            "frac_v_mc": v_mc / total,
            "frac_v_disc": v_disc / total,
            "implausibility": np.abs(error) / np.sqrt(total),
        })
        for column in extra_columns:
            part[column] = frame[column].to_numpy()
        parts.append(part)
    long = pd.concat(parts, ignore_index=True)
    long["period"] = long["target_key"].map(
        {key: name for name, keys in PERIODS.items() for key in keys}
    )
    long["binding_rank_joint"] = long.groupby(id_col)["implausibility"].rank(ascending=False, method="first").astype(int)
    long["binding_rank_block"] = long.groupby([id_col, "block"])["implausibility"].rank(
        ascending=False, method="first").astype(int)
    return long


def vector_summary(long: pd.DataFrame, id_col: str = "design_id") -> pd.DataFrame:
    """Per-vector I_max (diagnosis, AIDS, joint) and mean-error shape statistics."""
    rows = []
    for vid, g in long.groupby(id_col, sort=True):
        row = {id_col: vid}
        for block, sub in (("diagnosis", g[g.block == "diagnosis"]), ("aids", g[g.block == "aids"]), ("joint", g)):
            if sub.empty:
                raise DataContractError(f"vector {vid} has no {block} anchors")
            top = sub.loc[sub["implausibility"].idxmax()]
            row[f"imax_{block}"] = float(top["implausibility"])
            row[f"binding_{block}"] = top["target_key"]
            row[f"binding_{block}_frac_v_ensemble"] = float(top["frac_v_process"] + top["frac_v_mc"])
            row[f"max_abs_rel_error_{block}"] = float(sub["abs_rel_error"].max())
            row[f"median_abs_rel_error_{block}"] = float(sub["abs_rel_error"].median())
        for period, keys in PERIODS.items():
            sub = g[g.target_key.isin(keys)]
            row[f"mean_signed_rel_error_{period}"] = float(sub["signed_rel_error"].mean())
            row[f"max_abs_rel_error_{period}"] = float(sub["abs_rel_error"].max())
        for _, r in g.iterrows():
            row[f"rel__{r.target_key}"] = float(r.signed_rel_error)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# B2: windows, over/under counts, Pareto frontier
# ---------------------------------------------------------------------------

def window_counts(summary: pd.DataFrame, windows: Sequence[float] = (0.1, 0.2, 0.3),
                  group_col: str | None = None) -> pd.DataFrame:
    """Descriptive counts of vectors whose every anchor mean is within +/-w. NOT acceptance."""
    groups = [("all", summary)] if group_col is None else [("all", summary)] + list(summary.groupby(group_col))
    rows = []
    for label, frame in groups:
        for w in windows:
            ok = {name: frame[f"max_abs_rel_error_{name}"] <= w
                  for name in ("diagnosis", "aids", *PERIODS)}
            rows.append({
                "group": label, "window": w, "n_vectors": len(frame),
                "diagnosis_block": int(ok["diagnosis"].sum()),
                "aids_block": int(ok["aids"].sum()),
                "diagnosis_and_aids": int((ok["diagnosis"] & ok["aids"]).sum()),
                **{f"period_{p}": int(ok[p].sum()) for p in PERIODS},
                "early_aids_and_middle_diagnosis": int((ok["early_aids"] & ok["middle_diagnosis"]).sum()),
                "early_aids_and_later_diagnosis": int((ok["early_aids"] & ok["later_diagnosis"]).sum()),
                "middle_and_later_diagnosis": int((ok["middle_diagnosis"] & ok["later_diagnosis"]).sum()),
                "later_diagnosis_and_later_aids": int((ok["later_diagnosis"] & ok["later_aids"]).sum()),
            })
    return pd.DataFrame(rows)


def over_under_counts(long: pd.DataFrame, group_col: str | None = None) -> pd.DataFrame:
    keys = ["target_key"] if group_col is None else [group_col, "target_key"]
    g = long.groupby(keys)
    out = pd.DataFrame({
        "observed": g["observed"].first(),
        "n": g.size(),
        "n_over": g["signed_error"].apply(lambda s: int((s > 0).sum())),
        "n_under": g["signed_error"].apply(lambda s: int((s < 0).sum())),
        "n_exact": g["signed_error"].apply(lambda s: int((s == 0).sum())),
        "median_signed_rel_error": g["signed_rel_error"].median(),
        "min_signed_rel_error": g["signed_rel_error"].min(),
        "max_signed_rel_error": g["signed_rel_error"].max(),
        "n_within_30pct": g["abs_rel_error"].apply(lambda s: int((s <= 0.3).sum())),
    })
    return out.reset_index()


def pareto_mask(x: Sequence[float], y: Sequence[float]) -> np.ndarray:
    """Non-dominated points when minimising both coordinates. Non-finite points are never on it."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.shape != y.shape:
        raise DataContractError("pareto_mask needs equal-length inputs")
    finite = np.isfinite(x) & np.isfinite(y)
    mask = np.zeros(x.shape, dtype=bool)
    for i in np.flatnonzero(finite):
        dominated = finite & (x <= x[i]) & (y <= y[i]) & ((x < x[i]) | (y < y[i]))
        mask[i] = not dominated.any()
    return mask


def within_factor(simulated, observed, factor: float) -> np.ndarray:
    """True where observed/factor <= simulated <= observed*factor (multiplicative window).

    Rev 1 (2026-09-17): replaces the erroneous ``abs relative error <= factor-1``,
    which for factor 2 admitted any value in [0, 2*observed].  Fails closed on a
    non-positive observed value or factor <= 1; non-finite simulated values are False.
    """
    if not (np.isfinite(factor) and factor > 1.0):
        raise DataContractError(f"factor must be finite and > 1, got {factor}")
    z = np.asarray(observed, dtype=float)
    if np.any(~np.isfinite(z)) or np.any(z <= 0):
        raise DataContractError("multiplicative window needs positive finite observed values")
    x = np.asarray(simulated, dtype=float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(x) & (x >= z / factor) & (x <= z * factor)


def dominates_reference(summary: pd.DataFrame, ref_x: float, ref_y: float,
                        x_col: str = "max_abs_rel_error_diagnosis",
                        y_col: str = "max_abs_rel_error_aids") -> pd.Series:
    """Strictly better on both block maxima than a reference point."""
    return (summary[x_col] < ref_x) & (summary[y_col] < ref_y)


# ---------------------------------------------------------------------------
# B3: ranking and bootstrap
# ---------------------------------------------------------------------------

def rank_comparison(summary: pd.DataFrame, a: str, b: str, top: int = 10,
                    id_col: str = "design_id") -> dict:
    from scipy.stats import kendalltau, spearmanr  # noqa: PLC0415

    ra = summary[a].rank(method="min")
    rb = summary[b].rank(method="min")
    top_a = set(summary.nsmallest(top, a)[id_col])
    top_b = set(summary.nsmallest(top, b)[id_col])
    return {
        "spearman": float(spearmanr(summary[a], summary[b])[0]),
        "kendall_tau": float(kendalltau(summary[a], summary[b])[0]),
        f"top{top}_overlap": len(top_a & top_b),
        f"top{top}_by_{a}": sorted(int(i) for i in top_a),
        f"top{top}_by_{b}": sorted(int(i) for i in top_b),
        "max_abs_rank_shift_in_top": float(max(
            abs(ra[summary[id_col] == i].iloc[0] - rb[summary[id_col] == i].iloc[0]) for i in top_a | top_b)),
    }


def bootstrap_whole_trajectories(
    raw: pd.DataFrame,
    anchors: Sequence[Anchor],
    *,
    vector_ids: Sequence[int],
    k_values: Sequence[int],
    n_boot: int,
    seed: int,
    discrepancy: float = PRIMARY_DISCREPANCY,
    id_col: str = "source_design_id",
    seed_col: str = "confirmation_seed",
) -> pd.DataFrame:
    """Resample whole seed-level rows (all years together) with replacement.

    The SAME resampled seed labels are used for every vector in a draw, so
    between-vector comparisons are paired.  Requires identical seed sets across
    vectors.  Returns one row per (draw, K, vector) with block I_max and labels.
    """
    columns = [a.output_column for a in anchors]
    require_unique(raw, [id_col, seed_col], "bootstrap raw")
    require_finite_columns(raw, columns, "bootstrap raw")
    seed_sets = {vid: sorted(raw.loc[raw[id_col] == vid, seed_col]) for vid in vector_ids}
    reference = next(iter(seed_sets.values()))
    if not reference or any(s != reference for s in seed_sets.values()):
        raise DataContractError("bootstrap needs identical, non-empty seed sets across vectors")
    seeds = np.asarray(reference)
    cube = np.stack([
        raw[raw[id_col] == vid].set_index(seed_col).loc[seeds, columns].to_numpy(dtype=float)
        for vid in vector_ids
    ])  # vectors x seeds x anchors
    z = np.array([a.z for a in anchors])
    v_obs = np.array([a.v_obs for a in anchors])
    v_disc = (discrepancy * np.abs(z)) ** 2
    is_diag = np.array([a.block == "diagnosis" for a in anchors])
    rng = np.random.default_rng(seed)
    records = []
    for k in k_values:
        if k < 2:
            raise DataContractError("K must be >= 2")
        for draw in range(n_boot):
            idx = rng.integers(0, len(seeds), size=int(k))
            sample = cube[:, idx, :]
            mean = sample.mean(axis=1)
            s2 = sample.var(axis=1, ddof=1)
            total = v_obs + s2 * (1 + 1 / k) + v_disc
            with np.errstate(divide="ignore", invalid="ignore"):
                imp = np.where(total > 0, np.abs(mean - z) / np.sqrt(total), np.inf)
            for vi, vid in enumerate(vector_ids):
                records.append({
                    "draw": draw, "k": int(k), id_col: vid,
                    "imax_diagnosis": float(imp[vi, is_diag].max()),
                    "imax_aids": float(imp[vi, ~is_diag].max()),
                    "imax_joint": float(imp[vi].max()),
                    "binding_joint": anchors[int(np.argmax(imp[vi]))].key,
                })
    out = pd.DataFrame(records)
    for block in ("diagnosis", "aids", "joint"):
        out[f"pass_{block}"] = out[f"imax_{block}"] <= CUTOFF
    return out


def paired_seed_differences(raw: pd.DataFrame, columns: Sequence[str], *, id_a, id_b,
                            id_col: str = "source_design_id", seed_col: str = "confirmation_seed") -> pd.DataFrame:
    """Per-seed b - a, aligned on seed label regardless of row order."""
    require_unique(raw, [id_col, seed_col], "paired differences")
    a = raw[raw[id_col] == id_a].set_index(seed_col)[list(columns)]
    b = raw[raw[id_col] == id_b].set_index(seed_col)[list(columns)]
    if set(a.index) != set(b.index) or a.empty:
        raise DataContractError("paired differences need identical non-empty seed sets")
    order = sorted(a.index)
    diff = b.loc[order].to_numpy(dtype=float) - a.loc[order].to_numpy(dtype=float)
    return pd.DataFrame(diff, index=pd.Index(order, name=seed_col), columns=list(columns)).reset_index()


# ---------------------------------------------------------------------------
# B4: mechanism grouping and leave-out rescoring
# ---------------------------------------------------------------------------

MECHANISM_GROUPS: dict[str, tuple[str, str]] = {
    # name: (group, rationale)
    "seed_prevalence": ("initial_burden_stage", "1975 seeded prevalence"),
    "seed_msm_fraction": ("initial_burden_stage", "share of seed infections in MSM"),
    "seed_msm_chronic_share": ("initial_burden_stage", "MSM seed stage composition"),
    "seed_msm_aids_share": ("initial_burden_stage", "MSM seed stage composition"),
    "seed_non_msm_chronic_share": ("initial_burden_stage", "non-MSM seed stage composition"),
    "seed_non_msm_aids_share": ("initial_burden_stage", "non-MSM seed stage composition"),
    "p_msm": ("transmission_mixing",
              "AMBIGUOUS: population share of MSM sets the at-risk denominator (demography) but acts on the epidemic through the MSM contact pool"),
    "orient_female": ("transmission_mixing", "orientation contact multiplier"),
    "orient_msm": ("transmission_mixing", "orientation contact multiplier"),
    "epsilon_hetero": ("transmission_mixing", "assortativity"),
    "epsilon_msm": ("transmission_mixing", "assortativity"),
    "activity_med": ("transmission_mixing", "activity-group shares"),
    "activity_high": ("transmission_mixing", "activity-group shares"),
    "contact_low": ("transmission_mixing", "partner-change rates"),
    "contact_med": ("transmission_mixing", "partner-change rates"),
    "contact_high": ("transmission_mixing", "partner-change rates"),
    "diagnosis_1985": ("diagnosis_timing", "base diagnosis schedule anchor (stage-independent hazard)"),
    "diagnosis_1990": ("diagnosis_timing", "base diagnosis schedule anchor (stage-independent hazard)"),
    "diagnosis_1997": ("diagnosis_timing", "base diagnosis schedule anchor (stage-independent hazard)"),
    "diagnosis_2019": ("diagnosis_timing", "base diagnosis schedule anchor (stage-independent hazard)"),
    "msm_diag_mult_1996": ("diagnosis_timing", "MSM multiplier; neutral 1.0 before 1996, linear to 2004"),
    "msm_diag_mult_2004": ("diagnosis_timing", "MSM multiplier anchor"),
    "msm_diag_mult_2019": ("diagnosis_timing", "MSM multiplier anchor"),
    "chronic_to_aids": ("untreated_progression_mortality", "off-ART Chronic->AIDS, all years"),
    "aids_mortality": ("untreated_progression_mortality",
                       "AMBIGUOUS: AIDS death hazard; reduced on ART by art_mortality_reduction after 1996"),
    "art_init_1996": ("art", "ART initiation schedule; zero before 1996 (fixed art_init_1995=0)"),
    "art_init_2000": ("art", "ART initiation schedule"),
    "art_init_2015": ("art", "ART initiation schedule"),
    "art_init_2020": ("art", "ART initiation schedule"),
    "art_dropout_rate": ("art", "ART dropout"),
    "chronic_to_aids_on_art_early": ("art_progression", "effective on-ART hazard <=2000, ramps to 2010; only acts on people on ART (>=1996)"),
    "chronic_to_aids_on_art_recent": ("art_progression", "effective on-ART hazard >=2010"),
    "art_mortality_reduction": ("art", "AMBIGUOUS: ART mortality effect; acts only on people on ART (>=1996)"),
}

#: Earliest calendar year from which a parameter can causally act on model state.
#: None = acts from simulation start (1975).
EARLIEST_ACTIVE_YEAR: dict[str, int | None] = {
    **{name: None for name in MECHANISM_GROUPS},
    "diagnosis_1985": 1985, "diagnosis_1990": 1985, "diagnosis_1997": 1990, "diagnosis_2019": 1997,
    "msm_diag_mult_1996": 1996, "msm_diag_mult_2004": 1996, "msm_diag_mult_2019": 2004,
    "art_init_1996": 1996, "art_init_2000": 1996, "art_init_2015": 2000, "art_init_2020": 2015,
    "art_dropout_rate": 1996, "chronic_to_aids_on_art_early": 1996, "chronic_to_aids_on_art_recent": 2000,
    "art_mortality_reduction": 1996,
}


def mechanism_table(parameter_names: Sequence[str]) -> pd.DataFrame:
    unknown = [n for n in parameter_names if n not in MECHANISM_GROUPS]
    if unknown:
        raise DataContractError(f"No documented mechanism group for {unknown}")
    return pd.DataFrame([
        {"parameter": n, "mechanism_group": MECHANISM_GROUPS[n][0],
         "ambiguous": MECHANISM_GROUPS[n][1].startswith("AMBIGUOUS"),
         "earliest_causal_year": EARLIEST_ACTIVE_YEAR[n], "rationale": MECHANISM_GROUPS[n][1]}
        for n in parameter_names
    ])


def leave_out_rescore(long: pd.DataFrame, drop_sets: Mapping[str, Sequence[str]],
                      id_col: str = "design_id") -> pd.DataFrame:
    """Joint I_max after removing target subsets. Explanatory only; never acceptance."""
    rows = []
    for label, drop in drop_sets.items():
        kept = long[~long.target_key.isin(set(drop))]
        imax = kept.groupby(id_col)["implausibility"].max()
        rows.append({"dropped": label, "n_targets_kept": kept.target_key.nunique(),
                     "n_vectors_imax_le_3": int((imax <= CUTOFF).sum()),
                     "min_imax": float(imax.min()), "argmin_id": int(imax.idxmin())})
    return pd.DataFrame(rows)


def boundary_occupancy(design: pd.DataFrame, bounds: Mapping[str, tuple[float, float]],
                       edge_fraction: float = 0.05) -> pd.DataFrame:
    rows = []
    for name, (lo, hi) in bounds.items():
        u = (design[name].to_numpy(dtype=float) - lo) / (hi - lo)
        rows.append({"parameter": name, "min": lo, "max": hi,
                     "frac_near_lower": float(np.mean(u <= edge_fraction)),
                     "frac_near_upper": float(np.mean(u >= 1 - edge_fraction))})
    return pd.DataFrame(rows)
