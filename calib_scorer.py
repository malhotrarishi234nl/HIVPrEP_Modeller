"""
Weighted log-likelihood scoring for LHS epidemic calibration.
"""

from __future__ import annotations

import math
from typing import Dict


WEIGHTS = {
    "PLHIV": 2.0,
    "new_infections": 1.5,
    "aids_deaths": 1.0,
}


def is_collapsed(model_outputs: Dict[str, Dict[int, float]]) -> bool:
    """Return True for implausibly collapsed epidemic trajectories."""
    plhiv = model_outputs.get("PLHIV", {})
    for year, value in plhiv.items():
        if year >= 1995 and float(value) < 100.0:
            return True

    infections = model_outputs.get("new_infections", {})
    consecutive_zero = 0
    for year in range(1990, 2016):
        value = float(infections.get(year, 1.0))
        if value <= 0.0:
            consecutive_zero += 1
            if consecutive_zero >= 5:
                return True
        else:
            consecutive_zero = 0
    return False


def _point_ll(model_value: float, estimate: float, sigma: float) -> float:
    sigma = max(float(sigma), 1e-9)
    resid = (float(model_value) - float(estimate)) / sigma
    return -0.5 * math.log(2.0 * math.pi * sigma * sigma) - 0.5 * resid * resid


def _empty_indicator_scores(targets) -> dict:
    if isinstance(targets, list):
        return {record["model_output"]: float("-inf") for record in targets}
    return {indicator: float("-inf") for indicator in targets}


def _score_target_records(
    model_outputs: Dict[str, Dict[int, float]],
    target_records: list[dict],
    balance_families: bool = False,
) -> dict:
    ll_by_indicator = {}
    ll_by_family = {}
    n_by_family = {}
    n_points = 0
    total_ll = 0.0
    missing_outputs = set()

    for target in target_records:
        model_output = target["model_output"]
        year = int(target["year"])
        model_series = model_outputs.get(model_output, {})
        if year not in model_series:
            missing_outputs.add(model_output)
            continue
        weight = float(target.get("weight", 1.0))
        point_ll = _point_ll(
            model_series[year],
            target["estimate"],
            target["sigma"],
        )
        weighted_ll = weight * point_ll
        ll_by_indicator[model_output] = ll_by_indicator.get(model_output, 0.0) + weighted_ll
        family = str(target.get("target_family", model_output))
        ll_by_family[family] = ll_by_family.get(family, 0.0) + weighted_ll
        n_by_family[family] = n_by_family.get(family, 0) + 1
        if not balance_families:
            total_ll += weighted_ll
        n_points += 1

    if balance_families:
        total_ll = sum(
            family_ll / max(n_by_family[family], 1)
            for family, family_ll in ll_by_family.items()
        )

    return {
        "total_ll": float(total_ll),
        "ll_by_indicator": ll_by_indicator,
        "ll_by_family": ll_by_family,
        "n_by_family": n_by_family,
        "n_years_scored": n_points,
        "collapsed": False,
        "missing_outputs": sorted(missing_outputs),
    }


def _score_nested_targets(
    model_outputs: Dict[str, Dict[int, float]],
    targets: Dict[str, Dict[int, dict]],
) -> dict:
    ll_by_indicator = {}
    n_points = 0
    total_ll = 0.0

    for indicator, default_weight in WEIGHTS.items():
        indicator_ll = 0.0
        model_series = model_outputs.get(indicator, {})
        target_series = targets.get(indicator, {})
        for year, target in target_series.items():
            if year not in model_series:
                continue
            point_ll = _point_ll(
                model_series[year],
                target["estimate"],
                target["sigma"],
            )
            indicator_ll += float(target.get("weight", default_weight)) * point_ll
            n_points += 1
        ll_by_indicator[indicator] = indicator_ll
        total_ll += indicator_ll

    return {
        "total_ll": float(total_ll),
        "ll_by_indicator": ll_by_indicator,
        "n_years_scored": n_points,
        "collapsed": False,
        "missing_outputs": [],
    }


def score_draw(
    model_outputs: Dict[str, Dict[int, float]],
    targets,
    balance_families: bool = False,
) -> dict:
    """
    Score one draw against UNAIDS targets.

    Returns total log-likelihood, per-indicator log-likelihoods, scored point
    count, and collapse flag.  Higher total_ll is better.
    """
    apply_collapse_screen = True
    if isinstance(targets, list):
        apply_collapse_screen = any(
            target.get("target_family") == "epidemic_backbone"
            for target in targets
        )
    collapsed = apply_collapse_screen and is_collapsed(model_outputs)
    if collapsed:
        return {
            "total_ll": float("-inf"),
            "ll_by_indicator": _empty_indicator_scores(targets),
            "n_years_scored": 0,
            "collapsed": True,
            "missing_outputs": [],
        }

    if isinstance(targets, list):
        return _score_target_records(
            model_outputs,
            targets,
            balance_families=balance_families,
        )
    return _score_nested_targets(model_outputs, targets)
