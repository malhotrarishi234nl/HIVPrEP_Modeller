"""
History matching -- diagnostics and required reporting artefacts.

Spec section 11 requires, for every wave:

  * per-target implausibility distributions -- which target is binding, and where
  * NROY volume per wave as a fraction of the prior box
  * the ensemble-size-versus-V_disc curve  (REQUIRED -- a build that reports an
    NROY size without it is incomplete, spec section 3.4)
  * the full variance budget table: every target, every term, every derivation

The V_disc curve exists for a specific reason and it is worth stating it in the
code: it is the evidence that the discrepancy term was NOT tuned to make the
NROY set non-empty.  If NROY size is flat across an order of magnitude of the
multiplier, the result is driven by the data; if it is steep, the result is
driven by the choice, and spec section 10 says to escalate rather than pick a
point on the curve.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import hm_implausibility as hm
import hm_variance_budget as vb
from hm_target_register import ScalarTarget


def per_target_summary(
    matrix: np.ndarray,
    targets: Sequence[ScalarTarget],
    budgets: Mapping[str, vb.VarianceBudget],
    *,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
) -> pd.DataFrame:
    """Distribution of I_i across the ensemble, one row per scalar target.

    ``frac_ruled_out`` is the fraction of candidates this target alone would
    exclude.  A target at 1.0 is single-handedly emptying the space.
    """
    work = np.asarray(matrix, dtype=float)
    rows = []
    for column, target in enumerate(targets):
        values = work[:, column]
        finite = values[np.isfinite(values)]
        budget = budgets.get(target.key)
        rows.append(
            {
                "target_key": target.key,
                "series_name": target.series_name,
                "model_output": target.model_output,
                "year": target.year,
                "epistemic_tier": target.tier,
                "role": target.role,
                "z": target.z,
                "n_finite": int(finite.size),
                "i_min": float(np.min(finite)) if finite.size else np.nan,
                "i_median": float(np.median(finite)) if finite.size else np.nan,
                "i_p95": float(np.percentile(finite, 95)) if finite.size else np.nan,
                "i_max": float(np.max(finite)) if finite.size else np.nan,
                "frac_ruled_out": float(np.mean(~(np.isfinite(values) & (values <= cutoff)))) if values.size else np.nan,
                "total_sigma": budget.total_sigma if budget else np.nan,
                "dominant_variance_term": budget.dominant_term if budget else "",
                "underivable_terms": ";".join(budget.underivable_terms) if budget else "",
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("frac_ruled_out", ascending=False, kind="stable").reset_index(drop=True)


def binding_target_counts(combined: hm.CombinedImplausibility) -> pd.DataFrame:
    """How often each target is the one attaining I_max -- 'which target is binding'."""
    series = pd.Series([name for name in combined.binding_target if name is not None], dtype=object)
    if series.empty:
        return pd.DataFrame(columns=["target_key", "n_binding", "frac_binding"])
    counts = series.value_counts()
    return pd.DataFrame(
        {
            "target_key": counts.index,
            "n_binding": counts.to_numpy(),
            "frac_binding": (counts / len(series)).to_numpy(),
        }
    ).reset_index(drop=True)


def nroy_summary(
    combined: hm.CombinedImplausibility,
    *,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
) -> pd.DataFrame:
    """NROY size under the max, second-max and third-max rules.

    I_max is the primary rule.  The other two are reported for robustness to a
    single badly-specified target and must NOT be adopted to enlarge NROY
    (spec section 8.7).
    """
    rows = []
    for rule, label in (("max", "I_max (PRIMARY)"), ("second", "I_(2) (report only)"), ("third", "I_(3) (report only)")):
        mask = combined.nroy_mask(cutoff=cutoff, rule=rule)
        rows.append(
            {
                "rule": label,
                "cutoff": cutoff,
                "n_candidates": int(mask.size),
                "n_nroy": int(mask.sum()),
                "frac_nroy": float(mask.sum()) / float(mask.size) if mask.size else np.nan,
            }
        )
    return pd.DataFrame(rows)


def discrepancy_sensitivity_curve(
    multipliers: Iterable[float],
    score_fn: Callable[[float], hm.CombinedImplausibility],
    *,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
    rules: Sequence[str] = ("max", "second", "third"),
) -> pd.DataFrame:
    """THE REQUIRED CURVE: NROY size as a function of the V_disc multiplier.

    ``score_fn(multiplier)`` must rebuild the budgets at that multiplier and
    return the combined implausibilities.  Sweep at least an order of magnitude
    (spec section 3.4).

    Reading it: a curve that is flat over an order of magnitude says the NROY set
    is determined by the data.  A curve that is steep says it is determined by
    the discrepancy choice -- which is a section 10 escalation, not a menu.
    """
    rows = []
    for multiplier in multipliers:
        combined = score_fn(float(multiplier))
        row: dict = {"disc_multiplier": float(multiplier)}
        for rule in rules:
            mask = combined.nroy_mask(cutoff=cutoff, rule=rule)
            row[f"n_nroy_{rule}"] = int(mask.sum())
            row[f"frac_nroy_{rule}"] = float(mask.sum()) / float(mask.size) if mask.size else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def generic_sensitivity_curve(
    label: str,
    values: Iterable[float],
    score_fn: Callable[[float], hm.CombinedImplausibility],
    *,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
    rule: str = "max",
) -> pd.DataFrame:
    """NROY size against any single declared quantity (e.g. the delay-uncertainty fraction).

    Used to expose the influence of terms that are assumptions rather than
    derivations, so their effect is reported rather than buried.
    """
    rows = []
    for value in values:
        combined = score_fn(float(value))
        mask = combined.nroy_mask(cutoff=cutoff, rule=rule)
        rows.append(
            {
                label: float(value),
                "rule": rule,
                "n_nroy": int(mask.sum()),
                "frac_nroy": float(mask.sum()) / float(mask.size) if mask.size else np.nan,
            }
        )
    return pd.DataFrame(rows)


def nroy_volume(
    parameters: pd.DataFrame,
    mask: np.ndarray,
    bounds: Mapping[str, tuple[float, float]],
) -> dict:
    """NROY volume as a fraction of the prior box (spec section 5, section 11).

    Two measures, because they answer different questions and disagree when NROY
    is non-convex:

    ``frac_candidates``
        the fraction of evaluated candidates retained.  Under a space-filling
        design over the prior box this is a Monte Carlo estimate of the retained
        volume fraction, and it is the honest one.

    ``frac_bounding_box``
        the volume of the axis-aligned bounding box of the retained points,
        relative to the prior box.  This is an UPPER BOUND on the retained
        volume, not an estimate of it: a thin diagonal ridge fills a large
        bounding box.  Reported because it shows which parameters were actually
        constrained, per ``per_parameter_range_fraction``.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.shape[0] != len(parameters):
        raise ValueError("mask length must match the number of candidate rows")
    n_total = int(mask.size)
    n_nroy = int(mask.sum())
    result: dict = {
        "n_candidates": n_total,
        "n_nroy": n_nroy,
        "frac_candidates": (n_nroy / n_total) if n_total else np.nan,
    }
    ranges: dict[str, float] = {}
    log_fraction = 0.0
    retained = parameters.loc[mask]
    for name, (low, high) in bounds.items():
        width = float(high) - float(low)
        if name not in parameters.columns or width <= 0 or retained.empty:
            ranges[name] = np.nan
            continue
        column = pd.to_numeric(retained[name], errors="coerce").dropna()
        if column.empty:
            ranges[name] = np.nan
            continue
        fraction = float((column.max() - column.min()) / width)
        ranges[name] = fraction
        log_fraction += np.log(max(fraction, 1e-300))
    finite_ranges = {k: v for k, v in ranges.items() if np.isfinite(v)}
    result["per_parameter_range_fraction"] = ranges
    result["frac_bounding_box"] = float(np.exp(log_fraction)) if finite_ranges else np.nan
    result["n_parameters_in_box_measure"] = len(finite_ranges)
    return result


def per_parameter_range_table(volume: Mapping[str, object]) -> pd.DataFrame:
    """Which parameters the NROY set actually constrains, most-constrained first."""
    ranges = dict(volume.get("per_parameter_range_fraction", {}) or {})
    frame = pd.DataFrame(
        {"parameter": list(ranges.keys()), "retained_range_fraction": list(ranges.values())}
    )
    return frame.sort_values("retained_range_fraction", kind="stable").reset_index(drop=True)


def granularity_comparison(
    score_fn: Callable[[Sequence[int] | None], hm.CombinedImplausibility],
    year_sets: Mapping[str, Sequence[int] | None],
    *,
    cutoff: float = hm.IMPLAUSIBILITY_CUTOFF,
) -> pd.DataFrame:
    """NROY size under different scoring granularities.

    Scoring every (series, year) pair makes ~300 separate outputs; the reference
    application used 18.  Under a maximum rule the number of outputs materially
    changes how stringent the cutoff is, so the granularity is a scientific
    decision and both options are reported rather than one being chosen here.
    """
    rows = []
    for label, years in year_sets.items():
        combined = score_fn(years)
        mask = combined.nroy_mask(cutoff=cutoff, rule="max")
        rows.append(
            {
                "granularity": label,
                "n_scalar_targets": combined.n_targets,
                "n_candidates": int(mask.size),
                "n_nroy_max": int(mask.sum()),
                "frac_nroy_max": float(mask.sum()) / float(mask.size) if mask.size else np.nan,
            }
        )
    return pd.DataFrame(rows)
