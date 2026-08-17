"""
Historical-trajectory shape gates for V13.9 calibration.

These gates are supplementary to the V13.8.2 endpoint gates and detect
trajectories that are visually or epidemiologically implausible even when
recent endpoints are within bounds.  They are opt-in only: the default
settings have ``enabled=False`` so they do not affect V13.8.2 behaviour.

Design:
- Each gate function returns the same dict shape as calib_acceptance_gates:
  ``{"passed": bool, "failures": [...], "warnings": [...], "metrics": {...}}``.
- ``evaluate_historical_shape_gates`` is the combined entry point.
- ``evaluate_historical_shape_distance`` returns a smooth weighted distance for
  ABC-SMC search guidance (non-binding; gates remain the authority).
- Missing historical targets silently skip the MAPE/ratio check for that window
  rather than failing; at least one comparable point is needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Mapping


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalShapeGateSettings:
    """Thresholds for V13.9 historical trajectory shape gates.

    All ceilings are diagnostic starting-points derived from visual inspection
    of the V13.8.2 accepted ensemble.  They should be tightened incrementally
    once a generation of candidates with reasonable historical fit exists.
    """
    enabled: bool = False

    # PLHIV historical window (1990-2005)
    plhiv_hist_start: int = 1990
    plhiv_hist_end: int = 2005
    plhiv_hist_mape_ceiling: float = 40.0    # % MAPE ceiling
    plhiv_anchor_year: int = 1990
    plhiv_anchor_max_multiplier: float = 2.0  # model/target ratio ceiling for anchor year

    # Total diagnoses trajectory (1990-2014)
    diagnoses_hist_start: int = 1990
    diagnoses_hist_end: int = 2014
    diagnoses_hist_mape_ceiling: float = 40.0
    diagnoses_spike_year: int = 1997
    diagnoses_spike_ratio_ceiling: float = 2.5  # model/target ratio ceiling for spike year

    # MSM diagnoses trajectory (1996-2014)
    msm_diag_hist_start: int = 1996
    msm_diag_hist_end: int = 2014
    msm_diag_hist_mape_ceiling: float = 40.0
    msm_diag_early_end: int = 2003
    msm_diag_early_ratio_ceiling: float = 2.5  # model/target ratio ceiling for early years

    # AIDS cases trajectory (1990-2017; uses new_aids_cases model output)
    aids_cases_hist_start: int = 1990
    aids_cases_hist_end: int = 2017
    aids_cases_ratio_ceiling: float = 4.0       # model/target ratio ceiling (any year)
    aids_cases_hist_mape_ceiling: float = 80.0  # % MAPE ceiling (generous; data is sparse)


# ---------------------------------------------------------------------------
# Combined gate entry point
# ---------------------------------------------------------------------------


def evaluate_historical_shape_gates(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: HistoricalShapeGateSettings | None = None,
) -> dict:
    """Evaluate all historical shape gates for one candidate.

    Returns the same dict structure as ``evaluate_acceptance_gates`` in
    ``calib_acceptance_gates``.  When ``settings.enabled`` is False (the
    default), all gates are marked passed with a warning recorded so callers
    can detect that the gates were not actively enforced.
    """
    settings = settings or HistoricalShapeGateSettings()
    targets = list(target_records or [])

    if not settings.enabled:
        return {
            "overall_accept": True,
            "gates": {},
            "failures": [],
            "warnings": ["historical_shape_gates:disabled"],
            "metrics": {"version": "v13.9", "enabled": False},
        }

    gates = {
        "plhiv_hist_gate": _plhiv_hist_gate(model_outputs, targets, settings),
        "diagnoses_hist_gate": _diagnoses_hist_gate(model_outputs, targets, settings),
        "msm_diag_hist_gate": _msm_diag_hist_gate(model_outputs, targets, settings),
        "aids_cases_hist_gate": _aids_cases_hist_gate(model_outputs, targets, settings),
    }
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {"version": "v13.9", "enabled": True}
    for gate_name, result in gates.items():
        metrics.update(
            {f"{gate_name}_{key}": value for key, value in result.get("metrics", {}).items()}
        )
        failures.extend(f"{gate_name}:{reason}" for reason in result.get("failures", []))
        warnings.extend(f"{gate_name}:{reason}" for reason in result.get("warnings", []))

    return {
        "overall_accept": all(result["passed"] for result in gates.values()),
        "gates": gates,
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
    }


def historical_shape_gate_row_fields(result: dict) -> dict:
    """Flatten a historical shape gate result into CSV-friendly fields."""
    fields: dict = {
        "hist_version": result.get("metrics", {}).get("version", "v13.9"),
        "hist_enabled": bool(result.get("metrics", {}).get("enabled", False)),
        "hist_overall_accept": bool(result.get("overall_accept", False)),
        "hist_n_failures": len(result.get("failures", [])),
        "hist_n_warnings": len(result.get("warnings", [])),
        "hist_fail_reasons": ";".join(result.get("failures", [])),
        "hist_warning_reasons": ";".join(result.get("warnings", [])),
    }
    for gate_name, gate_result in result.get("gates", {}).items():
        fields[f"{gate_name}_pass"] = bool(gate_result.get("passed", False))
        fields[f"{gate_name}_fail_reasons"] = ";".join(gate_result.get("failures", []))
    for key, value in result.get("metrics", {}).items():
        fields[f"hist_metric_{key}"] = value
    return fields


# ---------------------------------------------------------------------------
# Continuous distance (for ABC-SMC search guidance)
# ---------------------------------------------------------------------------

DEFAULT_HISTORICAL_WEIGHTS: dict[str, float] = {
    "plhiv_hist": 1.0,
    "diagnoses_hist": 1.0,
    "msm_diag_hist": 1.0,
    "aids_cases_hist": 1.0,
}


def evaluate_historical_shape_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: HistoricalShapeGateSettings | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    """Compute smooth non-negative distance for historical shape components.

    Returns a dict with ``distance`` (weighted total), ``components``, and
    ``weighted_components``.  When ``settings.enabled`` is False, all
    components are 0.0 so the total does not pollute the V13.8.2 abc_distance.
    """
    settings = settings or HistoricalShapeGateSettings()
    targets = list(target_records or [])
    effective_weights = {**DEFAULT_HISTORICAL_WEIGHTS, **(weights or {})}

    if not settings.enabled:
        return {
            "distance": 0.0,
            "components": {},
            "weighted_components": {},
        }

    components = {
        "plhiv_hist": _plhiv_hist_distance(model_outputs, targets, settings),
        "diagnoses_hist": _diagnoses_hist_distance(model_outputs, targets, settings),
        "msm_diag_hist": _msm_diag_hist_distance(model_outputs, targets, settings),
        "aids_cases_hist": _aids_cases_hist_distance(model_outputs, targets, settings),
    }
    weighted = {
        name: float(value) * float(effective_weights.get(name, 1.0))
        for name, value in components.items()
    }
    return {
        "distance": sum(weighted.values()),
        "components": components,
        "weighted_components": weighted,
    }


def historical_shape_distance_row_fields(result: dict) -> dict:
    """Flatten historical shape distance result into CSV-friendly fields."""
    fields: dict = {"hist_distance": result.get("distance", 0.0)}
    for name, value in result.get("components", {}).items():
        fields[f"hist_distance_{name}"] = value
    for name, value in result.get("weighted_components", {}).items():
        fields[f"hist_weighted_{name}"] = value
    return fields


# ---------------------------------------------------------------------------
# Individual gate functions
# ---------------------------------------------------------------------------


def _plhiv_hist_gate(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {}

    # 1990-2005 MAPE
    pct_errors = _percent_errors(
        model_outputs, targets, "PLHIV",
        settings.plhiv_hist_start, settings.plhiv_hist_end,
    )
    if not pct_errors:
        warnings.append(
            f"PLHIV:{settings.plhiv_hist_start}-{settings.plhiv_hist_end}:no_comparable_targets"
        )
    else:
        med_abs = median(abs(e) for e in pct_errors)
        metrics["plhiv_hist_mape"] = med_abs
        metrics["plhiv_hist_n_points"] = len(pct_errors)
        if med_abs > settings.plhiv_hist_mape_ceiling:
            failures.append(
                f"PLHIV:hist_mape_{med_abs:.1f}%>{settings.plhiv_hist_mape_ceiling:.1f}%"
            )

    # Anchor year multiplier check
    anchor_target = _target_for_year(targets, "PLHIV", settings.plhiv_anchor_year)
    if anchor_target is None:
        warnings.append(f"PLHIV:{settings.plhiv_anchor_year}:no_anchor_target")
    else:
        anchor_model = _model_value(model_outputs, "PLHIV", settings.plhiv_anchor_year)
        if anchor_model is None:
            warnings.append(f"PLHIV:{settings.plhiv_anchor_year}:missing_model_output")
        else:
            anchor_estimate = float(anchor_target["estimate"])
            ratio = anchor_model / anchor_estimate if anchor_estimate > 0 else float("inf")
            metrics["plhiv_anchor_year"] = settings.plhiv_anchor_year
            metrics["plhiv_anchor_ratio"] = ratio
            if ratio > settings.plhiv_anchor_max_multiplier:
                failures.append(
                    f"PLHIV:{settings.plhiv_anchor_year}:ratio_{ratio:.2f}"
                    f">{settings.plhiv_anchor_max_multiplier:.2f}x"
                )

    return _gate_result(failures, warnings, metrics)


def _diagnoses_hist_gate(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {}

    # 1990-2014 MAPE on total diagnoses
    pct_errors = _percent_errors(
        model_outputs, targets, "new_diagnoses",
        settings.diagnoses_hist_start, settings.diagnoses_hist_end,
    )
    if not pct_errors:
        warnings.append(
            f"new_diagnoses:{settings.diagnoses_hist_start}-{settings.diagnoses_hist_end}"
            ":no_comparable_targets"
        )
    else:
        med_abs = median(abs(e) for e in pct_errors)
        metrics["diagnoses_hist_mape"] = med_abs
        metrics["diagnoses_hist_n_points"] = len(pct_errors)
        if med_abs > settings.diagnoses_hist_mape_ceiling:
            failures.append(
                f"new_diagnoses:hist_mape_{med_abs:.1f}%"
                f">{settings.diagnoses_hist_mape_ceiling:.1f}%"
            )

    # Spike-year ratio guard (artificial 1997 bump)
    spike_target = _target_for_year(targets, "new_diagnoses", settings.diagnoses_spike_year)
    if spike_target is not None:
        spike_model = _model_value(
            model_outputs, "new_diagnoses", settings.diagnoses_spike_year
        )
        if spike_model is not None:
            spike_est = float(spike_target["estimate"])
            ratio = spike_model / spike_est if spike_est > 0 else float("inf")
            metrics["diagnoses_spike_year"] = settings.diagnoses_spike_year
            metrics["diagnoses_spike_ratio"] = ratio
            if ratio > settings.diagnoses_spike_ratio_ceiling:
                failures.append(
                    f"new_diagnoses:{settings.diagnoses_spike_year}:spike_ratio_{ratio:.2f}"
                    f">{settings.diagnoses_spike_ratio_ceiling:.2f}"
                )

    return _gate_result(failures, warnings, metrics)


def _msm_diag_hist_gate(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {}

    # 1996-2014 MAPE
    pct_errors = _percent_errors(
        model_outputs, targets, "new_diagnoses_msm",
        settings.msm_diag_hist_start, settings.msm_diag_hist_end,
    )
    if not pct_errors:
        warnings.append(
            f"new_diagnoses_msm:{settings.msm_diag_hist_start}-{settings.msm_diag_hist_end}"
            ":no_comparable_targets"
        )
    else:
        med_abs = median(abs(e) for e in pct_errors)
        metrics["msm_diag_hist_mape"] = med_abs
        metrics["msm_diag_hist_n_points"] = len(pct_errors)
        if med_abs > settings.msm_diag_hist_mape_ceiling:
            failures.append(
                f"new_diagnoses_msm:hist_mape_{med_abs:.1f}%"
                f">{settings.msm_diag_hist_mape_ceiling:.1f}%"
            )

    # Early-window ratio ceiling (model/target must not exceed ceiling in 1996-2003)
    early_ratios = _model_target_ratios(
        model_outputs, targets, "new_diagnoses_msm",
        settings.msm_diag_hist_start, settings.msm_diag_early_end,
    )
    if not early_ratios:
        warnings.append(
            f"new_diagnoses_msm:{settings.msm_diag_hist_start}-{settings.msm_diag_early_end}"
            ":no_comparable_targets"
        )
    else:
        max_early_ratio = max(early_ratios)
        metrics["msm_diag_early_max_ratio"] = max_early_ratio
        metrics["msm_diag_early_n_points"] = len(early_ratios)
        if max_early_ratio > settings.msm_diag_early_ratio_ceiling:
            failures.append(
                f"new_diagnoses_msm:early_max_ratio_{max_early_ratio:.2f}"
                f">{settings.msm_diag_early_ratio_ceiling:.2f}"
            )

    return _gate_result(failures, warnings, metrics)


def _aids_cases_hist_gate(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {}

    # 1990-2017 ratio ceiling (any single year)
    ratios = _model_target_ratios(
        model_outputs, targets, "new_aids_cases",
        settings.aids_cases_hist_start, settings.aids_cases_hist_end,
    )
    if not ratios:
        warnings.append(
            f"new_aids_cases:{settings.aids_cases_hist_start}-{settings.aids_cases_hist_end}"
            ":no_comparable_targets"
        )
    else:
        max_ratio = max(ratios)
        metrics["aids_cases_hist_max_ratio"] = max_ratio
        metrics["aids_cases_hist_n_points"] = len(ratios)
        if max_ratio > settings.aids_cases_ratio_ceiling:
            failures.append(
                f"new_aids_cases:max_ratio_{max_ratio:.2f}"
                f">{settings.aids_cases_ratio_ceiling:.2f}"
            )

    # 1990-2017 MAPE
    pct_errors = _percent_errors(
        model_outputs, targets, "new_aids_cases",
        settings.aids_cases_hist_start, settings.aids_cases_hist_end,
    )
    if pct_errors:
        med_abs = median(abs(e) for e in pct_errors)
        metrics["aids_cases_hist_mape"] = med_abs
        if med_abs > settings.aids_cases_hist_mape_ceiling:
            failures.append(
                f"new_aids_cases:hist_mape_{med_abs:.1f}%"
                f">{settings.aids_cases_hist_mape_ceiling:.1f}%"
            )

    return _gate_result(failures, warnings, metrics)


# ---------------------------------------------------------------------------
# Distance components (smooth, non-negative; gate failures add quadratic cost)
# ---------------------------------------------------------------------------


def _plhiv_hist_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> float:
    mape = _median_abs_percent_error(
        model_outputs, targets, "PLHIV",
        settings.plhiv_hist_start, settings.plhiv_hist_end,
    )
    mape_dist = _threshold_distance(mape, settings.plhiv_hist_mape_ceiling)

    anchor_target = _target_for_year(targets, "PLHIV", settings.plhiv_anchor_year)
    anchor_model = _model_value(model_outputs, "PLHIV", settings.plhiv_anchor_year)
    anchor_dist = 0.0
    if anchor_target is not None and anchor_model is not None:
        est = float(anchor_target["estimate"])
        if est > 0:
            ratio = anchor_model / est
            anchor_dist = _ceiling_distance(ratio, settings.plhiv_anchor_max_multiplier)

    return mape_dist + anchor_dist


def _diagnoses_hist_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> float:
    mape = _median_abs_percent_error(
        model_outputs, targets, "new_diagnoses",
        settings.diagnoses_hist_start, settings.diagnoses_hist_end,
    )
    mape_dist = _threshold_distance(mape, settings.diagnoses_hist_mape_ceiling)

    spike_target = _target_for_year(targets, "new_diagnoses", settings.diagnoses_spike_year)
    spike_model = _model_value(model_outputs, "new_diagnoses", settings.diagnoses_spike_year)
    spike_dist = 0.0
    if spike_target is not None and spike_model is not None:
        est = float(spike_target["estimate"])
        if est > 0:
            ratio = spike_model / est
            spike_dist = _ceiling_distance(ratio, settings.diagnoses_spike_ratio_ceiling)

    return mape_dist + spike_dist


def _msm_diag_hist_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> float:
    mape = _median_abs_percent_error(
        model_outputs, targets, "new_diagnoses_msm",
        settings.msm_diag_hist_start, settings.msm_diag_hist_end,
    )
    mape_dist = _threshold_distance(mape, settings.msm_diag_hist_mape_ceiling)

    early_ratios = _model_target_ratios(
        model_outputs, targets, "new_diagnoses_msm",
        settings.msm_diag_hist_start, settings.msm_diag_early_end,
    )
    ratio_dist = 0.0
    if early_ratios:
        max_ratio = max(early_ratios)
        ratio_dist = _ceiling_distance(max_ratio, settings.msm_diag_early_ratio_ceiling)

    return mape_dist + ratio_dist


def _aids_cases_hist_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    settings: HistoricalShapeGateSettings,
) -> float:
    ratios = _model_target_ratios(
        model_outputs, targets, "new_aids_cases",
        settings.aids_cases_hist_start, settings.aids_cases_hist_end,
    )
    ratio_dist = 0.0
    if ratios:
        max_ratio = max(ratios)
        ratio_dist = _ceiling_distance(max_ratio, settings.aids_cases_ratio_ceiling)

    mape = _median_abs_percent_error(
        model_outputs, targets, "new_aids_cases",
        settings.aids_cases_hist_start, settings.aids_cases_hist_end,
    )
    mape_dist = _threshold_distance(mape, settings.aids_cases_hist_mape_ceiling)

    return ratio_dist + mape_dist


# ---------------------------------------------------------------------------
# Shared distance helpers
# ---------------------------------------------------------------------------


def _ceiling_distance(value: float, ceiling: float) -> float:
    """Quadratic penalty that grows when value exceeds ceiling; 0 when within."""
    if not _finite(value) or not _finite(ceiling):
        return 1.0
    if value <= ceiling:
        return 0.0
    return ((value - ceiling) / max(abs(ceiling), 1e-9)) ** 2


def _threshold_distance(value: float | None, threshold: float) -> float:
    """(value/threshold)^2; 0 when value is None (treated as no data)."""
    if value is None or not _finite(value):
        return 0.0
    return (float(value) / max(float(threshold), 1e-9)) ** 2


# ---------------------------------------------------------------------------
# Shared data-access helpers
# ---------------------------------------------------------------------------


def _gate_result(failures: list[str], warnings: list[str], metrics: dict) -> dict:
    return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": metrics}


def _target_for_year(targets: list[dict], output: str, year: int) -> dict | None:
    matches = [
        t for t in targets
        if t.get("model_output") == output and int(t.get("year", -1)) == int(year)
    ]
    if not matches:
        return None
    for t in matches:
        if t.get("calibration_use") == "primary":
            return t
    return matches[0]


def _model_value(
    model_outputs: Mapping[str, Mapping[int, float]],
    output: str,
    year: int,
) -> float | None:
    value = model_outputs.get(output, {}).get(year)
    return float(value) if _finite(value) else None


def _percent_errors(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output: str,
    start_year: int,
    end_year: int,
) -> list[float]:
    values: list[float] = []
    for t in targets:
        if t.get("model_output") != output:
            continue
        year = int(t["year"])
        if not start_year <= year <= end_year:
            continue
        estimate = float(t["estimate"])
        model_val = _model_value(model_outputs, output, year)
        if model_val is None or estimate == 0:
            continue
        values.append((model_val - estimate) / estimate * 100.0)
    return values


def _model_target_ratios(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output: str,
    start_year: int,
    end_year: int,
) -> list[float]:
    ratios: list[float] = []
    for t in targets:
        if t.get("model_output") != output:
            continue
        year = int(t["year"])
        if not start_year <= year <= end_year:
            continue
        estimate = float(t["estimate"])
        model_val = _model_value(model_outputs, output, year)
        if model_val is None or estimate == 0:
            continue
        ratios.append(model_val / estimate)
    return ratios


def _median_abs_percent_error(
    model_outputs: Mapping[str, Mapping[int, float]],
    targets: list[dict],
    output: str,
    start_year: int,
    end_year: int,
) -> float | None:
    errors = _percent_errors(model_outputs, targets, output, start_year, end_year)
    return median(abs(e) for e in errors) if errors else None


def _finite(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
