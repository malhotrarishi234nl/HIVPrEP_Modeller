"""
Continuous calibration distance for adaptive epidemic calibration.

Hard acceptance gates remain the authority for final candidate acceptance. This
module converts the same target concepts into a smooth, non-negative distance so
sequential calibration can learn from near misses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Mapping

from calib_acceptance_gates import AcceptanceGateSettings, evaluate_acceptance_gates


DEFAULT_COMPONENT_WEIGHTS = {
    "diagnosis": 1.0,
    "plhiv": 1.0,
    "plhiv_sex": 0.5,
    "incidence": 1.0,
    "aids_deaths": 1.0,
    "art_stock": 1.0,
}


@dataclass(frozen=True)
class CalibrationDistanceSettings:
    gate_settings: AcceptanceGateSettings = field(default_factory=AcceptanceGateSettings)
    component_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_COMPONENT_WEIGHTS))
    collapse_penalty: float = 1000.0
    missing_penalty: float = 100.0


def evaluate_calibration_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    *,
    settings: CalibrationDistanceSettings | None = None,
    acceptance: dict | None = None,
    collapsed: bool = False,
    error: str | None = None,
) -> dict:
    """Return total distance, component distances, and hard gate result."""
    settings = settings or CalibrationDistanceSettings()
    targets = list(target_records or [])
    acceptance = acceptance or evaluate_acceptance_gates(
        model_outputs,
        targets,
        settings=settings.gate_settings,
    )

    components = {
        "diagnosis": _diagnosis_distance(model_outputs, targets, settings.gate_settings),
        "plhiv": _plhiv_distance(model_outputs, targets, settings.gate_settings),
        "plhiv_sex": _plhiv_sex_distance(model_outputs, targets, settings.gate_settings),
        "incidence": _incidence_distance(model_outputs, targets, settings.gate_settings),
        "aids_deaths": _aids_death_distance(model_outputs, targets, settings.gate_settings),
        "art_stock": _art_stock_distance(model_outputs, targets, settings.gate_settings),
    }
    weighted = {
        name: float(value) * float(settings.component_weights.get(name, 1.0))
        for name, value in components.items()
    }
    total = sum(weighted.values())
    penalties = {}
    if collapsed:
        penalties["collapse"] = settings.collapse_penalty
        total += settings.collapse_penalty
    if error:
        penalties["error"] = settings.collapse_penalty
        total += settings.collapse_penalty

    return {
        "distance": float(total),
        "components": components,
        "weighted_components": weighted,
        "penalties": penalties,
        "overall_accept": bool(acceptance.get("overall_accept", False)),
        "acceptance": acceptance,
    }


def calibration_distance_row_fields(result: dict) -> dict:
    """Flatten distance result into CSV-friendly fields."""
    fields = {
        "abc_distance": result.get("distance", math.inf),
        "abc_overall_accept": bool(result.get("overall_accept", False)),
    }
    for name, value in result.get("components", {}).items():
        fields[f"abc_distance_{name}"] = value
    for name, value in result.get("weighted_components", {}).items():
        fields[f"abc_weighted_{name}"] = value
    for name, value in result.get("penalties", {}).items():
        fields[f"abc_penalty_{name}"] = value
    return fields


def _diagnosis_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    penalties = []
    ratios = []
    for output in ("new_diagnoses", "new_diagnoses_msm"):
        target = _latest_target(targets, output)
        if target is None:
            penalties.append(1.0)
            continue
        value = _model_value(model_outputs, output, int(target["year"]))
        estimate = _positive_estimate(target)
        if value is None or estimate is None:
            penalties.append(1.0)
            continue
        ratio = value / estimate
        ratios.append(ratio)
        penalties.append(_band_distance(ratio, settings.diagnosis_floor, settings.diagnosis_ceiling))
    if len(ratios) == 2:
        # Penalize composition imbalance even when each ratio is near the band.
        balance = abs(math.log(max(ratios[0], 1e-9) / max(ratios[1], 1e-9)))
        penalties.append(min(balance * balance, 25.0) * 0.25)
    return sum(penalties)


def _plhiv_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    target = _target_for_year(targets, "PLHIV", settings.plhiv_year)
    bound_penalty = _source_bound_distance(
        _model_value(model_outputs, "PLHIV", settings.plhiv_year),
        target,
    )
    med_abs = _median_abs_percent_error(
        model_outputs,
        targets,
        "PLHIV",
        settings.plhiv_recent_start,
        settings.plhiv_recent_end,
    )
    return bound_penalty + _scaled_threshold_distance(med_abs, settings.plhiv_recent_mape_ceiling)


def _plhiv_sex_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    outputs = ("plhiv_female_15plus", "plhiv_male_15plus")
    penalties = []
    for output in outputs:
        med_abs = _median_abs_percent_error(
            model_outputs,
            targets,
            output,
            settings.plhiv_recent_start,
            settings.plhiv_recent_end,
        )
        if med_abs is None:
            if any(target.get("model_output") == output for target in targets):
                penalties.append(1.0)
            continue
        penalties.append(_scaled_threshold_distance(med_abs, settings.plhiv_recent_mape_ceiling))
    return sum(penalties) / len(penalties) if penalties else 0.0


def _incidence_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    target = _target_for_year(targets, "new_infections", settings.incidence_year)
    bound_penalty = _source_bound_distance(
        _model_value(model_outputs, "new_infections", settings.incidence_year),
        target,
        upper_tolerance=settings.incidence_year_upper_tolerance,
    )
    med_ratio = _median_model_target_ratio(
        model_outputs,
        targets,
        "new_infections",
        settings.incidence_recent_start,
        settings.incidence_recent_end,
    )
    return bound_penalty + _band_distance(
        med_ratio,
        settings.incidence_recent_ratio_floor,
        settings.incidence_recent_ratio_ceiling,
    )


def _aids_death_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    aids_targets = [target for target in targets if target.get("model_output") == "aids_deaths"]
    if not aids_targets:
        return 1.0
    comparable = []
    within = 0
    for target in aids_targets:
        value = _model_value(model_outputs, "aids_deaths", int(target["year"]))
        if value is None:
            continue
        comparable.append((int(target["year"]), value, target))
        if _within_bounds(value, target):
            within += 1
    if not comparable:
        return 1.0
    bounds_fraction = within / len(comparable)
    bounds_penalty = _floor_distance(bounds_fraction, settings.aids_bounds_fraction_floor)

    series = model_outputs.get("aids_deaths", {})
    peak_values = [
        float(value)
        for year, value in series.items()
        if settings.aids_peak_start <= int(year) <= settings.aids_peak_end and _finite(value)
    ]
    recent_values = [
        float(value)
        for year, value in series.items()
        if settings.aids_recent_start <= int(year) <= settings.aids_recent_end and _finite(value)
    ]
    if not peak_values or not recent_values:
        return bounds_penalty + 1.0
    recent_mean = sum(recent_values) / len(recent_values)
    peak_ratio = max(peak_values) / recent_mean if recent_mean > 0 else math.inf
    peak_penalty = _floor_distance(peak_ratio, settings.aids_peak_to_recent_mean_floor)
    return bounds_penalty + peak_penalty


def _art_stock_distance(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> float:
    med_abs = _median_abs_percent_error(
        model_outputs,
        targets,
        "on_art_stock",
        settings.art_recent_start,
        settings.art_recent_end,
    )
    return _scaled_threshold_distance(med_abs, settings.art_recent_mape_ceiling)


def _band_distance(value: float | None, floor: float, ceiling: float) -> float:
    if value is None or not _finite(value):
        return 1.0
    value = float(value)
    if floor <= value <= ceiling:
        center = (floor + ceiling) / 2.0
        half_width = max((ceiling - floor) / 2.0, 1e-9)
        return 0.05 * ((value - center) / half_width) ** 2
    if value < floor:
        return ((floor - value) / max(abs(floor), 1e-9)) ** 2
    return ((value - ceiling) / max(abs(ceiling), 1e-9)) ** 2


def _floor_distance(value: float | None, floor: float) -> float:
    if value is None or not _finite(value):
        return 1.0
    value = float(value)
    if value >= floor:
        return 0.0
    return ((floor - value) / max(abs(floor), 1e-9)) ** 2


def _scaled_threshold_distance(value: float | None, threshold: float) -> float:
    if value is None or not _finite(value):
        return 1.0
    return (float(value) / max(float(threshold), 1e-9)) ** 2


def _source_bound_distance(
    value: float | None,
    target: dict | None,
    *,
    upper_tolerance: float = 0.0,
) -> float:
    if value is None or target is None:
        return 1.0
    lower, upper = _bounds(target)
    estimate = _positive_estimate(target)
    if lower is None or upper is None or estimate is None:
        return 1.0
    value = float(value)
    effective_upper = upper * (1.0 + float(upper_tolerance))
    if lower <= value <= effective_upper:
        return 0.0
    if value < lower:
        return ((lower - value) / estimate) ** 2
    return ((value - effective_upper) / estimate) ** 2


def _median_abs_percent_error(model_outputs, targets: list[dict], output: str, start_year: int, end_year: int):
    values = []
    for target in targets:
        if target.get("model_output") != output:
            continue
        year = int(target["year"])
        if not start_year <= year <= end_year:
            continue
        estimate = _positive_estimate(target)
        value = _model_value(model_outputs, output, year)
        if estimate is None or value is None:
            continue
        values.append(abs((value - estimate) / estimate * 100.0))
    return median(values) if values else None


def _median_model_target_ratio(model_outputs, targets: list[dict], output: str, start_year: int, end_year: int):
    values = []
    for target in targets:
        if target.get("model_output") != output:
            continue
        year = int(target["year"])
        if not start_year <= year <= end_year:
            continue
        estimate = _positive_estimate(target)
        value = _model_value(model_outputs, output, year)
        if estimate is None or value is None:
            continue
        values.append(value / estimate)
    return median(values) if values else None


def _latest_target(targets: list[dict], output: str) -> dict | None:
    matches = [target for target in targets if target.get("model_output") == output]
    if not matches:
        return None
    return _target_for_year(matches, output, max(int(target["year"]) for target in matches))


def _target_for_year(targets: list[dict], output: str, year: int) -> dict | None:
    matches = [
        target for target in targets
        if target.get("model_output") == output and int(target.get("year")) == int(year)
    ]
    if not matches:
        return None
    for target in matches:
        if target.get("calibration_use") == "primary":
            return target
    return matches[0]


def _model_value(model_outputs: Mapping[str, Mapping[int, float]], output: str, year: int) -> float | None:
    value = model_outputs.get(output, {}).get(year)
    return float(value) if _finite(value) else None


def _bounds(target: dict) -> tuple[float | None, float | None]:
    lower = target.get("lower")
    upper = target.get("upper")
    lower = float(lower) if _finite(lower) else None
    upper = float(upper) if _finite(upper) else None
    return lower, upper


def _within_bounds(value: float, target: dict) -> bool:
    lower, upper = _bounds(target)
    return lower is not None and upper is not None and lower <= float(value) <= upper


def _positive_estimate(target: dict) -> float | None:
    estimate = target.get("estimate") if target else None
    if not _finite(estimate) or float(estimate) <= 0.0:
        return None
    return float(estimate)


def _finite(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
