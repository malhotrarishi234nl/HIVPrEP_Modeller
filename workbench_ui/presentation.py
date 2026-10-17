"""Plain-language presentation helpers for the collaborator workbench.

The model and its saved artifacts use exact machine-readable status codes.  The
workbench should not make collaborators decode those codes, but it must not
rewrite the underlying records either.  These helpers are therefore display
only: raw values continue to live in manifests and provenance exports.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


_STATUS_LABELS = {
    "COMPLETE": "Run complete",
    "COMPLETED": "Run complete",
    "RUNNING": "Run in progress",
    "FAILED": "Run failed",
    "ERROR": "Run failed",
    "PARTIAL": "Partially complete",
    "REQUIRES_RECALIBRATION_NOT_PRODUCTION_ELIGIBLE": "Needs recalibration",
    "CONFIRMED_GOOD_FIT_FOR_RECORDED_LINEAGE": "Recorded historical fit",
}

_CLASSIFICATION_LABELS = {
    "HISTORICAL_RETAINED": "Historical result",
    "RESEARCH_NOT_PRODUCTION_ELIGIBLE": "Exploratory run",
    "CALIBRATION_CANDIDATE_ARTIFACT": "Calibration result",
    "DERIVED_PRESENTATION": "Derived result",
    "SUPERSEDED": "Previous result",
    "REVIEW_REQUIRED": "Review needed",
}


@dataclass(frozen=True)
class SemanticCue:
    """A text-first visual cue; ``tone`` may support, but never replace, its label."""

    label: str
    tone: str
    explanation: str


def _code(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _plain_code(value: str) -> str:
    words = re.sub(r"[_-]+", " ", value).strip().lower()
    return words[:1].upper() + words[1:] if words else "Not recorded"


def display_status(value: Any) -> str:
    """Return a neutral run-state label without exposing internal lineage names."""
    raw = _code(value)
    if not raw:
        return "Not recorded"
    normalized = raw.upper()
    if normalized in _STATUS_LABELS:
        return _STATUS_LABELS[normalized]
    if "OPTION_B" in normalized or "OPTION B" in normalized:
        return "Recorded historical result"
    if "COMPLETE" in normalized:
        return "Run complete"
    if "FAIL" in normalized or "ERROR" in normalized:
        return "Run failed"
    if "RUNNING" in normalized or "STARTED" in normalized:
        return "Run in progress"
    return _plain_code(raw)


def display_classification(value: Any) -> str:
    """Return the concise kind of result shown to a collaborator."""
    raw = _code(value)
    if not raw:
        return "Not recorded"
    normalized = raw.upper()
    if normalized in _CLASSIFICATION_LABELS:
        return _CLASSIFICATION_LABELS[normalized]
    if "OPTION_B" in normalized or "OPTION B" in normalized:
        return "Historical result"
    return _plain_code(raw)


def display_model(value: Any) -> str:
    raw = _code(value)
    if not raw or raw.lower() == "none":
        return "Not recorded"
    if raw.upper() == "M0":
        return "Routine diagnosis pathway (M0)"
    if raw.upper() == "M1":
        return "Adds symptom-triggered diagnosis (M1)"
    return raw


def display_probability(value: Any, *, model: Any = None) -> str:
    if _code(model).upper() == "M0":
        return "Not applicable"
    if value is None:
        return "Not recorded"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Not recorded"
    if not math.isfinite(number):
        return "Not recorded"
    return f"{number:.0%}"


def display_warning(value: Any) -> str:
    """Make warning text readable while keeping exact text in provenance files."""
    raw = _code(value)
    normalized = raw.upper()
    if "INCOMPATIBLE_WITH_CURRENT" in normalized and "PRODUCTION_ROUTE" in normalized:
        return "This result cannot be rerun by the current model version."
    if "VALID_FOR_RECORDED" in normalized and "NOT_CURRENT" in normalized:
        return "Recorded historical result; not current policy evidence."
    return re.sub(r"(?i)option[_ -]?b", "recorded lineage", raw)


def implausibility_cue(value: Any) -> SemanticCue:
    """Interpret the project's primary history-matching I-max threshold.

    This deliberately encodes only the documented ``I_max <= 3`` rule.  It is
    not a general-purpose traffic-light rule for percentages or other metrics.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if not math.isfinite(number):
        return SemanticCue(
            "Not available", "neutral", "No finite implausibility value was recorded."
        )
    if number <= 3:
        return SemanticCue(
            "Within the primary threshold",
            "within",
            f"I-max is {number:g}; the primary history-matching rule is I-max <= 3.",
        )
    return SemanticCue(
        "Outside the primary threshold",
        "outside",
        f"I-max is {number:g}; the primary history-matching rule is I-max <= 3.",
    )

