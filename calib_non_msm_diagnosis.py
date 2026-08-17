"""
Non-MSM diagnosis calibration diagnostic (V13.9.5).

Provides opt-in support for evaluating how well the model reproduces the
*non-MSM* diagnosis series, derived as:

    non_msm = new_diagnoses - new_diagnoses_msm

Context
-------
The V13.9.4 post-hoc diagnostic showed that the V13.9.3 best candidate
assigns nearly all recent diagnoses to MSM (model non-MSM ≈ 0 in 2024,
target non-MSM ≈ 199).  Before adding an MSM-specific diagnosis multiplier,
this diagnostic tests whether the existing single-rate mechanism can fit the
non-MSM baseline when that target is made visible to calibration.

Stolte et al. (2008), Eur J Public Health, supports group-structured HIV
testing/diagnosis behaviour in the Netherlands; however that mechanism is not
implemented here.  This module characterises the observed composition gap only.

Design principles
-----------------
- Pure functions; no I/O, no model runner.
- Opt-in via ``enabled`` flag on ``NonMsmDiagnosisSettings``; default is
  transparent (gate always passes, distance contribution = 0).
- Does NOT change model mechanics or existing hard gates.
- Derived target only: no new source data is required.
- Small negative numerical artefacts are clamped to zero before any
  ratio/error computation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Mapping


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NonMsmDiagnosisSettings:
    """Thresholds for the V13.9.5 non-MSM diagnosis diagnostic gate.

    Default values are intentionally wide because this is a diagnostic layer,
    not a locked acceptance gate.  They should be tightened once a generation
    of candidates with credible non-MSM composition fit exists.
    """
    enabled: bool = False

    # Latest-year model/target ratio bounds
    latest_floor: float = 0.50
    latest_ceiling: float = 1.50

    # Recent window (2015-2024) MAPE
    recent_start: int = 2015
    recent_end: int = 2024
    recent_mape_ceiling: float = 35.0  # percent

    # Historical window (1996-2014) MAPE
    hist_start: int = 1996
    hist_end: int = 2014
    hist_mape_ceiling: float = 60.0    # percent (generous; data quality is variable)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_non_msm_diagnosis_series(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
) -> list[dict]:
    """Derive the non-MSM diagnosis series from model outputs and targets.

    For each year where *both* ``new_diagnoses`` and ``new_diagnoses_msm``
    target records exist, computes:

        model_non_msm  = max(0, model_new_diagnoses  - model_new_diagnoses_msm)
        target_non_msm = max(0, target_new_diagnoses - target_new_diagnoses_msm)

    Years where either model output is missing are silently skipped.

    Returns a list of row dicts sorted by year ascending, with keys:
        year, model_total, target_total, model_msm, target_msm,
        model_non_msm, target_non_msm.
    """
    targets = list(target_records or [])

    # Index target estimates by output → year.
    total_targets: dict[int, float] = {}
    msm_targets: dict[int, float] = {}
    for t in targets:
        output = t.get("model_output")
        try:
            year = int(t.get("year", -1))
        except (TypeError, ValueError):
            continue
        estimate = t.get("estimate")
        if not _finite(estimate):
            continue
        if output == "new_diagnoses":
            total_targets[year] = float(estimate)
        elif output == "new_diagnoses_msm":
            msm_targets[year] = float(estimate)

    rows: list[dict] = []
    for year in sorted(set(total_targets) & set(msm_targets)):
        target_total = total_targets[year]
        target_msm   = msm_targets[year]
        target_non_msm = max(0.0, target_total - target_msm)

        model_total = _model_value(model_outputs, "new_diagnoses", year)
        model_msm   = _model_value(model_outputs, "new_diagnoses_msm", year)
        if model_total is None or model_msm is None:
            continue

        model_non_msm = max(0.0, model_total - model_msm)

        rows.append({
            "year":           year,
            "model_total":    model_total,
            "target_total":   target_total,
            "model_msm":      model_msm,
            "target_msm":     target_msm,
            "model_non_msm":  model_non_msm,
            "target_non_msm": target_non_msm,
        })

    return rows


def evaluate_non_msm_diagnosis_gate(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: NonMsmDiagnosisSettings | None = None,
) -> dict:
    """Evaluate the non-MSM diagnosis gate for one candidate.

    Returns a dict with the same structure as other gate functions:
        {"passed": bool, "failures": [...], "warnings": [...], "metrics": {...}}

    When ``settings.enabled`` is False (the default), the gate always passes
    and records a warning so callers can detect that it was inactive.
    """
    settings = settings or NonMsmDiagnosisSettings()

    if not settings.enabled:
        return {
            "passed": True,
            "failures": [],
            "warnings": ["non_msm_diagnosis_gate:disabled"],
            "metrics": {"version": "v13.9.5", "enabled": False},
        }

    series = build_non_msm_diagnosis_series(model_outputs, target_records)
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict = {"version": "v13.9.5", "enabled": True}

    if not series:
        warnings.append("non_msm_diagnosis:no_comparable_points")
        return {
            "passed": True,
            "failures": failures,
            "warnings": warnings,
            "metrics": metrics,
        }

    # --- Latest-year model/target ratio --------------------------------
    latest = series[-1]  # list is sorted by year ascending
    latest_year        = latest["year"]
    target_non_msm_lat = latest["target_non_msm"]
    model_non_msm_lat  = latest["model_non_msm"]

    metrics["latest_year"]           = latest_year
    metrics["latest_model_non_msm"]  = model_non_msm_lat
    metrics["latest_target_non_msm"] = target_non_msm_lat

    if target_non_msm_lat > 0.0:
        latest_ratio = model_non_msm_lat / target_non_msm_lat
        metrics["latest_ratio"] = latest_ratio
        if latest_ratio < settings.latest_floor:
            failures.append(
                f"non_msm:{latest_year}:ratio_{latest_ratio:.3f}"
                f"<floor_{settings.latest_floor:.3f}"
            )
        if latest_ratio > settings.latest_ceiling:
            failures.append(
                f"non_msm:{latest_year}:ratio_{latest_ratio:.3f}"
                f">ceiling_{settings.latest_ceiling:.3f}"
            )
    else:
        warnings.append(f"non_msm:{latest_year}:target_non_msm_is_zero")

    # --- Recent window MAPE (2015-2024) --------------------------------
    recent_errors = _percent_errors_from_series(series, settings.recent_start, settings.recent_end)
    if not recent_errors:
        warnings.append(
            f"non_msm:recent_{settings.recent_start}-{settings.recent_end}"
            ":no_comparable_points"
        )
    else:
        recent_mape = median(abs(e) for e in recent_errors)
        metrics["recent_mape"]     = recent_mape
        metrics["recent_n_points"] = len(recent_errors)
        if recent_mape > settings.recent_mape_ceiling:
            failures.append(
                f"non_msm:recent_mape_{recent_mape:.1f}%"
                f">{settings.recent_mape_ceiling:.1f}%"
            )

    # --- Historical window MAPE (1996-2014) ----------------------------
    hist_errors = _percent_errors_from_series(series, settings.hist_start, settings.hist_end)
    if not hist_errors:
        warnings.append(
            f"non_msm:hist_{settings.hist_start}-{settings.hist_end}"
            ":no_comparable_points"
        )
    else:
        hist_mape = median(abs(e) for e in hist_errors)
        metrics["hist_mape"]     = hist_mape
        metrics["hist_n_points"] = len(hist_errors)
        if hist_mape > settings.hist_mape_ceiling:
            failures.append(
                f"non_msm:hist_mape_{hist_mape:.1f}%"
                f">{settings.hist_mape_ceiling:.1f}%"
            )

    return {
        "passed":   not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics":  metrics,
    }


def non_msm_diagnosis_distance(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: NonMsmDiagnosisSettings | None = None,
) -> dict:
    """Compute smooth non-negative distance for non-MSM diagnosis components.

    Returns a dict with ``distance`` (scalar total) and ``components``
    (per-component breakdown).  When ``settings.enabled`` is False, all
    components are 0.0 so the total does not pollute the V13.8.2 abc_distance.
    """
    settings = settings or NonMsmDiagnosisSettings()

    if not settings.enabled:
        return {"distance": 0.0, "components": {}}

    series = build_non_msm_diagnosis_series(model_outputs, target_records)

    # Latest-year ratio distance.
    latest_dist = 0.0
    if series:
        latest = series[-1]
        target_non_msm = latest["target_non_msm"]
        model_non_msm  = latest["model_non_msm"]
        if target_non_msm > 0.0:
            ratio = model_non_msm / target_non_msm
            latest_dist = _floor_ceiling_distance(
                ratio, settings.latest_floor, settings.latest_ceiling
            )

    # Recent-window MAPE distance.
    recent_errors = _percent_errors_from_series(series, settings.recent_start, settings.recent_end)
    recent_mape   = _safe_median_abs(recent_errors)
    recent_dist   = _threshold_distance(recent_mape, settings.recent_mape_ceiling)

    # Historical-window MAPE distance.
    hist_errors = _percent_errors_from_series(series, settings.hist_start, settings.hist_end)
    hist_mape   = _safe_median_abs(hist_errors)
    hist_dist   = _threshold_distance(hist_mape, settings.hist_mape_ceiling)

    components = {
        "latest_ratio": latest_dist,
        "recent_mape":  recent_dist,
        "hist_mape":    hist_dist,
    }
    return {"distance": sum(components.values()), "components": components}


def non_msm_diagnosis_row_fields(
    gate_result: dict,
    dist_result: dict | None = None,
) -> dict:
    """Flatten gate and distance results into CSV-friendly fields."""
    fields: dict = {
        "non_msm_diag_enabled":    bool(gate_result.get("metrics", {}).get("enabled", False)),
        "non_msm_diag_passed":     bool(gate_result.get("passed", True)),
        "non_msm_diag_n_failures": len(gate_result.get("failures", [])),
        "non_msm_diag_fail_reasons": ";".join(gate_result.get("failures", [])),
        "non_msm_diag_warning_reasons": ";".join(gate_result.get("warnings", [])),
    }
    for key, value in gate_result.get("metrics", {}).items():
        fields[f"non_msm_diag_{key}"] = value
    if dist_result is not None:
        fields["non_msm_diag_distance"] = dist_result.get("distance", 0.0)
        for name, value in dist_result.get("components", {}).items():
            fields[f"non_msm_diag_dist_{name}"] = value
    return fields


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _percent_errors_from_series(
    series: list[dict],
    start_year: int,
    end_year: int,
) -> list[float]:
    """Compute (model_non_msm - target_non_msm) / target_non_msm * 100 in window."""
    errors: list[float] = []
    for row in series:
        year = row["year"]
        if not start_year <= year <= end_year:
            continue
        target_non_msm = row["target_non_msm"]
        model_non_msm  = row["model_non_msm"]
        if target_non_msm == 0.0:
            continue
        errors.append((model_non_msm - target_non_msm) / target_non_msm * 100.0)
    return errors


def _safe_median_abs(values: list[float]) -> float | None:
    if not values:
        return None
    return median(abs(v) for v in values)


def _floor_ceiling_distance(value: float, floor: float, ceiling: float) -> float:
    """Quadratic penalty outside [floor, ceiling]; 0 within."""
    if not _finite(value):
        return 1.0
    if value < floor:
        return ((floor - value) / max(abs(floor), 1e-9)) ** 2
    if value > ceiling:
        return ((value - ceiling) / max(abs(ceiling), 1e-9)) ** 2
    return 0.0


def _threshold_distance(value: float | None, threshold: float) -> float:
    """(value / threshold)^2; 0 when value is None (treated as no data)."""
    if value is None or not _finite(value):
        return 0.0
    return (float(value) / max(float(threshold), 1e-9)) ** 2


def _model_value(
    model_outputs: Mapping[str, Mapping[int, float]],
    output: str,
    year: int,
) -> float | None:
    value = model_outputs.get(output, {}).get(year)
    return float(value) if _finite(value) else None


def _finite(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
