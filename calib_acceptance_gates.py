"""
Acceptance-gate diagnostics for calibrated HIV epidemic candidates.

These gates are intentionally separate from LHS scoring and runner selection.
They provide an auditable pass/fail screen for candidate configs after model
outputs have already been generated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AcceptanceGateSettings:
    diagnosis_floor: float = 0.90
    diagnosis_ceiling: float = 1.50
    plhiv_year: int = 2023
    plhiv_recent_start: int = 2015
    plhiv_recent_end: int = 2023
    plhiv_recent_mape_ceiling: float = 10.0
    incidence_year: int = 2023
    incidence_recent_start: int = 2015
    incidence_recent_end: int = 2023
    incidence_recent_ratio_floor: float = 0.50
    incidence_recent_ratio_ceiling: float = 1.75
    incidence_year_upper_tolerance: float = 0.05
    aids_bounds_fraction_floor: float = 0.40
    aids_peak_start: int = 1990
    aids_peak_end: int = 1997
    aids_recent_start: int = 2015
    aids_recent_end: int = 2023
    aids_peak_to_recent_mean_floor: float = 2.0
    aids_death_hard_gate: bool = True
    art_recent_start: int = 2015
    art_recent_end: int = 2023
    art_recent_mape_ceiling: float = 10.0


def evaluate_acceptance_gates(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: AcceptanceGateSettings | None = None,
) -> dict:
    """Evaluate all hard acceptance gates for one candidate."""
    settings = settings or AcceptanceGateSettings()
    targets = list(target_records or [])
    gates = {
        "diagnosis_gate": _diagnosis_gate(model_outputs, targets, settings),
        "plhiv_gate": _plhiv_gate(model_outputs, targets, settings),
        "incidence_gate": _incidence_gate(model_outputs, targets, settings),
        "aids_death_gate": _aids_death_gate(model_outputs, targets, settings),
        "art_stock_gate": _art_stock_gate(model_outputs, targets, settings),
    }
    failures = []
    warnings = []
    metrics = {
        "version": "v13.8.2",
        "aids_death_hard_gate": bool(settings.aids_death_hard_gate),
    }
    for gate_name, result in gates.items():
        metrics.update({f"{gate_name}_{key}": value for key, value in result.get("metrics", {}).items()})
        gate_failures = [
            f"{gate_name}:{reason}" for reason in result.get("failures", [])
        ]
        if gate_name == "aids_death_gate" and not settings.aids_death_hard_gate:
            warnings.extend(gate_failures)
        else:
            failures.extend(gate_failures)
        warnings.extend(f"{gate_name}:{reason}" for reason in result.get("warnings", []))
    hard_gate_names = [
        name for name in gates
        if name != "aids_death_gate" or settings.aids_death_hard_gate
    ]
    return {
        "overall_accept": all(gates[name]["passed"] for name in hard_gate_names),
        "gates": gates,
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
    }


def acceptance_gate_row_fields(result: dict) -> dict:
    """Flatten an acceptance-gate result into CSV-friendly fields."""
    fields = {
        "acceptance_version": result.get("metrics", {}).get("version", "v13.6"),
        "overall_accept": bool(result.get("overall_accept", False)),
        "acceptance_n_failures": len(result.get("failures", [])),
        "acceptance_n_warnings": len(result.get("warnings", [])),
        "acceptance_fail_reasons": ";".join(result.get("failures", [])),
        "acceptance_warning_reasons": ";".join(result.get("warnings", [])),
    }
    for gate_name, gate_result in result.get("gates", {}).items():
        fields[f"{gate_name}_pass"] = bool(gate_result.get("passed", False))
        fields[f"{gate_name}_fail_reasons"] = ";".join(gate_result.get("failures", []))
        fields[f"{gate_name}_warning_reasons"] = ";".join(gate_result.get("warnings", []))
    for key, value in result.get("metrics", {}).items():
        fields[f"acceptance_{key}"] = value
    return fields


def _diagnosis_gate(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> dict:
    failures = []
    metrics = {
        "floor": settings.diagnosis_floor,
        "ceiling": settings.diagnosis_ceiling,
    }
    for output in ("new_diagnoses", "new_diagnoses_msm"):
        target = _latest_target(targets, output)
        if target is None:
            failures.append(f"{output}:missing_target")
            continue
        year = int(target["year"])
        model_value = _model_value(model_outputs, output, year)
        if model_value is None:
            failures.append(f"{output}:{year}:missing_model_output")
            continue
        observed = float(target["estimate"])
        ratio = model_value / observed if observed else float("inf")
        metrics[f"{output}_year"] = year
        metrics[f"{output}_model"] = model_value
        metrics[f"{output}_target"] = observed
        metrics[f"{output}_ratio"] = ratio
        if ratio < settings.diagnosis_floor:
            failures.append(f"{output}:{year}:ratio_{ratio:.3f}<floor_{settings.diagnosis_floor:.3f}")
        if ratio > settings.diagnosis_ceiling:
            failures.append(f"{output}:{year}:ratio_{ratio:.3f}>ceiling_{settings.diagnosis_ceiling:.3f}")
    return _gate_result(failures, [], metrics)


def _plhiv_gate(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> dict:
    failures = []
    warnings = []
    metrics = {}
    target = _target_for_year(targets, "PLHIV", settings.plhiv_year)
    if target is None:
        failures.append(f"PLHIV:{settings.plhiv_year}:missing_target")
    else:
        model_value = _model_value(model_outputs, "PLHIV", settings.plhiv_year)
        if model_value is None:
            failures.append(f"PLHIV:{settings.plhiv_year}:missing_model_output")
        else:
            lower, upper = _bounds(target)
            metrics["year"] = settings.plhiv_year
            metrics["model"] = model_value
            metrics["target"] = float(target["estimate"])
            metrics["lower"] = lower
            metrics["upper"] = upper
            metrics["within_bounds"] = _within_bounds(model_value, target)
            if not metrics["within_bounds"]:
                failures.append(f"PLHIV:{settings.plhiv_year}:outside_source_bounds")

    recent_pct = _percent_errors(
        model_outputs,
        targets,
        "PLHIV",
        settings.plhiv_recent_start,
        settings.plhiv_recent_end,
    )
    if not recent_pct:
        failures.append("PLHIV:recent:missing_comparable_points")
    else:
        med_abs = median(abs(value) for value in recent_pct)
        metrics["recent_median_abs_pct"] = med_abs
        if med_abs > settings.plhiv_recent_mape_ceiling:
            failures.append(
                f"PLHIV:recent_median_abs_pct_{med_abs:.2f}>"
                f"{settings.plhiv_recent_mape_ceiling:.2f}"
            )
    return _gate_result(failures, warnings, metrics)


def _incidence_gate(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> dict:
    failures = []
    metrics = {}
    target = _target_for_year(targets, "new_infections", settings.incidence_year)
    if target is None:
        failures.append(f"new_infections:{settings.incidence_year}:missing_target")
    else:
        model_value = _model_value(model_outputs, "new_infections", settings.incidence_year)
        if model_value is None:
            failures.append(f"new_infections:{settings.incidence_year}:missing_model_output")
        else:
            lower, upper = _bounds(target)
            effective_upper = (
                upper * (1.0 + settings.incidence_year_upper_tolerance)
                if upper is not None
                else None
            )
            within_bounds = (
                lower is not None
                and effective_upper is not None
                and lower <= float(model_value) <= effective_upper
            )
            metrics["year"] = settings.incidence_year
            metrics["model"] = model_value
            metrics["target"] = float(target["estimate"])
            metrics["lower"] = lower
            metrics["upper"] = upper
            metrics["upper_tolerance"] = settings.incidence_year_upper_tolerance
            metrics["effective_upper"] = effective_upper
            metrics["within_source_bounds"] = _within_bounds(model_value, target)
            metrics["within_bounds"] = within_bounds
            if not within_bounds:
                failures.append(
                    f"new_infections:{settings.incidence_year}:outside_source_bounds"
                    f"_with_upper_tolerance_{settings.incidence_year_upper_tolerance:.3f}"
                )

    ratios = _model_target_ratios(
        model_outputs,
        targets,
        "new_infections",
        settings.incidence_recent_start,
        settings.incidence_recent_end,
    )
    if not ratios:
        failures.append("new_infections:recent:missing_comparable_points")
    else:
        med_ratio = median(ratios)
        metrics["recent_median_model_target_ratio"] = med_ratio
        if med_ratio < settings.incidence_recent_ratio_floor:
            failures.append(
                f"new_infections:recent_median_ratio_{med_ratio:.3f}<"
                f"{settings.incidence_recent_ratio_floor:.3f}"
            )
        if med_ratio > settings.incidence_recent_ratio_ceiling:
            failures.append(
                f"new_infections:recent_median_ratio_{med_ratio:.3f}>"
                f"{settings.incidence_recent_ratio_ceiling:.3f}"
            )
    return _gate_result(failures, [], metrics)


def _aids_death_gate(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> dict:
    failures = []
    warnings = []
    metrics = {}
    aids_targets = [target for target in targets if target.get("model_output") == "aids_deaths"]
    if not aids_targets:
        failures.append("aids_deaths:missing_targets")
        return _gate_result(failures, warnings, metrics)

    comparable = []
    within = 0
    for target in aids_targets:
        year = int(target["year"])
        model_value = _model_value(model_outputs, "aids_deaths", year)
        if model_value is None:
            continue
        comparable.append((year, model_value, target))
        if _within_bounds(model_value, target):
            within += 1
    if not comparable:
        failures.append("aids_deaths:missing_model_outputs")
        return _gate_result(failures, warnings, metrics)

    bounds_fraction = within / len(comparable)
    metrics["bounds_fraction"] = bounds_fraction
    metrics["n_comparable"] = len(comparable)
    metrics["n_within_bounds"] = within
    if bounds_fraction < settings.aids_bounds_fraction_floor:
        failures.append(
            f"aids_deaths:bounds_fraction_{bounds_fraction:.3f}<"
            f"{settings.aids_bounds_fraction_floor:.3f}"
        )

    series = model_outputs.get("aids_deaths", {})
    peak_points = [
        (year, float(value))
        for year, value in series.items()
        if settings.aids_peak_start <= int(year) <= settings.aids_peak_end and _finite(value)
    ]
    recent_values = [
        float(value)
        for year, value in series.items()
        if settings.aids_recent_start <= int(year) <= settings.aids_recent_end and _finite(value)
    ]
    if not peak_points:
        failures.append("aids_deaths:early_peak_window_missing_model_outputs")
    else:
        peak_year, peak_value = max(peak_points, key=lambda item: item[1])
        metrics["early_peak_year"] = int(peak_year)
        metrics["early_peak_value"] = peak_value
    if not recent_values:
        failures.append("aids_deaths:recent_window_missing_model_outputs")
    elif peak_points:
        recent_mean = sum(recent_values) / len(recent_values)
        ratio = peak_value / recent_mean if recent_mean else float("inf")
        metrics["recent_mean"] = recent_mean
        metrics["peak_to_recent_mean_ratio"] = ratio
        if ratio < settings.aids_peak_to_recent_mean_floor:
            failures.append(
                f"aids_deaths:peak_to_recent_mean_ratio_{ratio:.3f}<"
                f"{settings.aids_peak_to_recent_mean_floor:.3f}"
            )
    return _gate_result(failures, warnings, metrics)


def _art_stock_gate(model_outputs, targets: list[dict], settings: AcceptanceGateSettings) -> dict:
    failures = []
    metrics = {}
    recent_pct = _percent_errors(
        model_outputs,
        targets,
        "on_art_stock",
        settings.art_recent_start,
        settings.art_recent_end,
    )
    if not recent_pct:
        failures.append("on_art_stock:recent:missing_comparable_points")
    else:
        med_abs = median(abs(value) for value in recent_pct)
        metrics["recent_median_abs_pct"] = med_abs
        if med_abs > settings.art_recent_mape_ceiling:
            failures.append(
                f"on_art_stock:recent_median_abs_pct_{med_abs:.2f}>"
                f"{settings.art_recent_mape_ceiling:.2f}"
            )
    return _gate_result(failures, [], metrics)


def _gate_result(failures: list[str], warnings: list[str], metrics: dict) -> dict:
    return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": metrics}


def _latest_target(targets: list[dict], output: str) -> dict | None:
    matches = [target for target in targets if target.get("model_output") == output]
    if not matches:
        return None
    latest_year = max(int(target["year"]) for target in matches)
    return _target_for_year(matches, output, latest_year)


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
    series = model_outputs.get(output, {})
    if year not in series:
        return None
    value = float(series[year])
    return value if _finite(value) else None


def _bounds(target: dict) -> tuple[float | None, float | None]:
    lower = target.get("lower")
    upper = target.get("upper")
    lower = float(lower) if _finite(lower) else None
    upper = float(upper) if _finite(upper) else None
    return lower, upper


def _within_bounds(model_value: float, target: dict) -> bool:
    lower, upper = _bounds(target)
    if lower is None or upper is None:
        return False
    return lower <= float(model_value) <= upper


def _percent_errors(model_outputs, targets: list[dict], output: str, start_year: int, end_year: int) -> list[float]:
    values = []
    for target in targets:
        if target.get("model_output") != output:
            continue
        year = int(target["year"])
        if year < start_year or year > end_year:
            continue
        estimate = float(target["estimate"])
        model_value = _model_value(model_outputs, output, year)
        if model_value is None or estimate == 0:
            continue
        values.append((model_value - estimate) / estimate * 100.0)
    return values


def _model_target_ratios(model_outputs, targets: list[dict], output: str, start_year: int, end_year: int) -> list[float]:
    ratios = []
    for target in targets:
        if target.get("model_output") != output:
            continue
        year = int(target["year"])
        if year < start_year or year > end_year:
            continue
        estimate = float(target["estimate"])
        model_value = _model_value(model_outputs, output, year)
        if model_value is None or estimate == 0:
            continue
        ratios.append(model_value / estimate)
    return ratios


def _finite(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
