"""Diagnostic-only stage-structured HIV diagnosis hazard.

The production diagnosis schedule remains the source of the routine annual
probability.  This module only validates the audit coordinates and performs
the probability/hazard arithmetic; it owns no schedules and draws no events.

Symptom-diagnosis availability boundary (added 2026-09-22 for the focused
calibration): ``symptom_start_year`` is optional. When present, the AIDS
symptom hazard is exactly zero for calendar times strictly before it and active
at or after it; the routine hazard is untouched. When absent the symptom hazard
is active at every calendar time, which is the semantics the completed
2026-09-22 structural audit ran under and is kept so that audit stays
reproducible. The focused-calibration harness always sets the boundary
explicitly (1985) and refuses a configuration without it.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Any

from config_utils import extract_value


class StageStructuredDiagnosisConfigError(ValueError):
    """Raised when the diagnostic-only block is invalid."""


@dataclass(frozen=True)
class StageStructuredDiagnosis:
    enabled: bool = False
    routine_hazard_scale: float = 1.0
    aids_symptom_annual_probability: float = 0.0
    symptom_start_year: float | None = None

    @property
    def is_identity(self) -> bool:
        return self.routine_hazard_scale == 1.0 and \
            self.aids_symptom_annual_probability == 0.0

    def symptom_probability_at(self, current_year: float) -> float:
        """Annual AIDS symptom probability in force at ``current_year``.

        Zero strictly before ``symptom_start_year``; the configured value at or
        after it, or at every time when no boundary is declared.
        """
        if self.symptom_start_year is not None and \
                float(current_year) < self.symptom_start_year:
            return 0.0
        return self.aids_symptom_annual_probability


def load_stage_structured_diagnosis(config: Mapping[str, Any] | None) \
        -> StageStructuredDiagnosis:
    diagnosis = (config or {}).get("disease", {}).get("diagnosis", {})
    block = diagnosis.get("stage_structured_diagnosis", {})
    if not block:
        return StageStructuredDiagnosis()
    enabled = extract_value(block.get("enabled", {"value": False}))
    if not isinstance(enabled, (bool,)):
        raise StageStructuredDiagnosisConfigError("enabled must be boolean")
    rho = _probability_like(
        block.get("routine_hazard_scale", {"value": 1.0}),
        "routine_hazard_scale",
    )
    symptom = _probability_like(
        block.get("aids_symptom_annual_probability", {"value": 0.0}),
        "aids_symptom_annual_probability",
    )
    start = _optional_year(block.get("symptom_start_year"), "symptom_start_year")
    return StageStructuredDiagnosis(bool(enabled), rho, symptom, start)


def _optional_year(raw: Any, name: str) -> float | None:
    if raw is None:
        return None
    value = extract_value(raw)
    if value is None:
        return None
    if isinstance(value, bool):
        raise StageStructuredDiagnosisConfigError(f"{name} must be a calendar year")
    try:
        year = float(value)
    except (TypeError, ValueError) as exc:
        raise StageStructuredDiagnosisConfigError(f"{name} must be a calendar year") from exc
    if not math.isfinite(year) or not 1900.0 <= year <= 2100.0:
        raise StageStructuredDiagnosisConfigError(
            f"{name} must be a finite calendar year in [1900, 2100]"
        )
    return year


def _probability_like(raw: Any, name: str) -> float:
    try:
        value = float(extract_value(raw))
    except (TypeError, ValueError) as exc:
        raise StageStructuredDiagnosisConfigError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise StageStructuredDiagnosisConfigError(f"{name} must be finite and in [0, 1]")
    return value


def validate_effective_probability(value: float, name: str = "effective annual probability") \
        -> float:
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise StageStructuredDiagnosisConfigError(f"{name} must be numeric") from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise StageStructuredDiagnosisConfigError(f"{name} must be finite and in [0, 1]")
    return probability


def stage_step_probability(
    effective_routine_annual_probability: float,
    *,
    routine_hazard_scale: float,
    aids_symptom_annual_probability: float,
    years_per_step: float,
    aids_stage: bool,
) -> float:
    """Return the exact competing-hazard step probability for one stage.

    Annual probabilities equal to one are valid absorbing limits and return a
    step probability of one without evaluating ``log(0)``. Other nonfinite
    intermediate hazards fail closed.
    """
    routine = validate_effective_probability(effective_routine_annual_probability)
    rho = _probability_like(routine_hazard_scale, "routine_hazard_scale")
    symptom = _probability_like(
        aids_symptom_annual_probability, "aids_symptom_annual_probability"
    )
    try:
        dt = float(years_per_step)
    except (TypeError, ValueError) as exc:
        raise StageStructuredDiagnosisConfigError("years_per_step must be numeric") from exc
    if not math.isfinite(dt) or dt <= 0.0:
        raise StageStructuredDiagnosisConfigError("years_per_step must be finite and positive")

    if rho > 0.0 and routine == 1.0:
        return 1.0
    if aids_stage and symptom == 1.0:
        return 1.0
    routine_hazard = 0.0 if rho == 0.0 else rho * -math.log1p(-routine)
    symptom_hazard = -math.log1p(-symptom) if aids_stage and symptom < 1.0 else 0.0
    total_hazard = routine_hazard + symptom_hazard
    if not math.isfinite(total_hazard) or total_hazard < 0.0:
        raise StageStructuredDiagnosisConfigError("diagnosis hazard must be finite and nonnegative")
    probability = -math.expm1(-total_hazard * dt)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise StageStructuredDiagnosisConfigError("step probability is invalid")
    return probability
