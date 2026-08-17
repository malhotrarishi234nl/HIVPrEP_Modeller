"""
UNAIDS target loader for the LHS epidemic calibration runner.

The parser uses stable UNAIDS GID columns rather than display labels.  The
Netherlands 2025 file stores PLHIV counts under Indicator_GId ``PLWH`` and new
infection counts under ``NEW_INFECTIONS``.  ``HIV_INCIDENCE`` is a rate and is
not used for count-based model calibration.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, Iterable


TARGET_MAP = {
    ("PLWH", "ESTIMATE_ALL_AGES"): "PLHIV",
    ("NEW_INFECTIONS", "ESTIMATE_ALL_AGES"): "new_infections",
    ("AIDS_DEATHS", "ESTIMATE_ALL_AGES"): "aids_deaths",
}

LOWER_SUBGROUP = "LOW_ESTIMATE_ALL_AGES"
UPPER_SUBGROUP = "HIGH_ESTIMATE_ALL_AGES"

_LOWER_KEYS = {
    (indicator_gid, LOWER_SUBGROUP): target_name
    for (indicator_gid, _), target_name in TARGET_MAP.items()
}
_UPPER_KEYS = {
    (indicator_gid, UPPER_SUBGROUP): target_name
    for (indicator_gid, _), target_name in TARGET_MAP.items()
}


def default_unaids_path(root: str | Path | None = None) -> Path:
    """Return the repository-default UNAIDS Netherlands estimates CSV path."""
    base = Path(root) if root is not None else Path(__file__).resolve().parent
    return base / "Validation " / "HIV Data UNAIDS" / "ZIP Files" / "netherlands_estimates_2025.csv"


def _parse_float(value: str | None) -> float | None:
    """Parse UNAIDS numeric cells; return None for blanks/non-numeric cells."""
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if cleaned in {"", ".", "..", "...", "NA", "N/A"}:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def compute_sigma(estimate: float, lower: float | None, upper: float | None) -> float:
    """Compute normal sigma from 95% CI bounds, falling back to 10% CV."""
    estimate = float(estimate)
    lower_ok = lower is not None and math.isfinite(float(lower))
    upper_ok = upper is not None and math.isfinite(float(upper))
    if lower_ok and upper_ok and float(upper) > float(lower):
        sigma = (float(upper) - float(lower)) / 3.92
    else:
        sigma = abs(estimate) * 0.10
    return max(float(sigma), 1e-9)


def load_unaids_targets(csv_path: str | Path) -> Dict[str, Dict[int, dict]]:
    """
    Load UNAIDS Netherlands 2025 targets.

    Returns:
        {
            "PLHIV": {1990: {"estimate": ..., "lower": ..., "upper": ..., "sigma": ...}},
            "new_infections": {...},
            "aids_deaths": {...},
        }
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"UNAIDS target file not found: {csv_path}")

    partial: Dict[str, Dict[int, dict]] = {name: {} for name in TARGET_MAP.values()}

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"Indicator_GId", "Subgroup_Val_GId", "Area ID", "Time Period", "Data value"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"UNAIDS CSV missing required columns: {sorted(missing)}")

        for row in reader:
            if row.get("Area ID") != "NLD":
                continue
            try:
                year = int(float(str(row.get("Time Period", "")).strip()))
            except ValueError:
                continue

            key = (row.get("Indicator_GId", ""), row.get("Subgroup_Val_GId", ""))
            value = _parse_float(row.get("Data value"))
            if value is None:
                continue

            if key in TARGET_MAP:
                target_name = TARGET_MAP[key]
                partial[target_name].setdefault(year, {})["estimate"] = value
            elif key in _LOWER_KEYS:
                target_name = _LOWER_KEYS[key]
                partial[target_name].setdefault(year, {})["lower"] = value
            elif key in _UPPER_KEYS:
                target_name = _UPPER_KEYS[key]
                partial[target_name].setdefault(year, {})["upper"] = value

    targets: Dict[str, Dict[int, dict]] = {name: {} for name in TARGET_MAP.values()}
    for indicator, by_year in partial.items():
        for year, values in sorted(by_year.items()):
            if "estimate" not in values:
                continue
            estimate = float(values["estimate"])
            lower = values.get("lower")
            upper = values.get("upper")
            targets[indicator][year] = {
                "estimate": estimate,
                "lower": lower,
                "upper": upper,
                "sigma": compute_sigma(estimate, lower, upper),
            }

    missing_series = [name for name, rows in targets.items() if not rows]
    if missing_series:
        raise ValueError(
            "UNAIDS target series missing or empty: "
            + ", ".join(missing_series)
            + ". No fallback targets are used without explicit approval."
        )

    return targets


def get_available_years(targets: Dict[str, Dict[int, dict]], indicator: str) -> list[int]:
    """Return sorted years available for one target indicator."""
    return sorted(targets.get(indicator, {}).keys())


def restrict_years(
    targets: Dict[str, Dict[int, dict]],
    start_year: int,
    end_year: int,
    indicators: Iterable[str] = ("PLHIV", "new_infections", "aids_deaths"),
) -> Dict[str, Dict[int, dict]]:
    """Return a copy of target rows within inclusive year bounds."""
    return {
        indicator: {
            year: dict(values)
            for year, values in targets.get(indicator, {}).items()
            if start_year <= year <= end_year
        }
        for indicator in indicators
    }
