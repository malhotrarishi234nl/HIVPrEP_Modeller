"""Observed calibration and comparison series for workbench charts.

The loader follows the tracked target register rather than maintaining a second
set of metric mappings in the UI.  Source intervals are retained when present;
they are never described as simulation confidence intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd


TARGET_REGISTER = "configs/hm_target_register_robust_nroy_production_20261015.json"
WAVE_PLAN = "configs/hm_wave_plan_forecast_windows_20260921.json"
EXTERNAL_INPUTS = "data/external_inputs.json"


@dataclass(frozen=True)
class ObservationLoad:
    frame: pd.DataFrame
    warnings: tuple[str, ...] = ()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["year", "observed", "lower", "upper", "role", "series", "source"]
    )


def _external_path(root: Path, logical_id: str) -> Path | None:
    registry_path = root / EXTERNAL_INPUTS
    if not registry_path.is_file():
        return None
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    for item in payload.get("inputs", []):
        if item.get("logical_id") == logical_id:
            candidate = Path(str(item.get("path", "")))
            return candidate if candidate.is_absolute() else (root / candidate).resolve()
    return None


def _workbook_series(workbook: Path, target: dict[str, Any]) -> pd.DataFrame:
    raw = pd.read_excel(workbook, sheet_name=target["source_sheet"], header=1)
    year_col = target["year_column"]
    value_col = target["estimate_column"]
    selected = pd.DataFrame({
        "year": pd.to_numeric(raw[year_col], errors="coerce"),
        "observed": pd.to_numeric(raw[value_col], errors="coerce"),
    })
    for output, key in (("lower", "lower_column"), ("upper", "upper_column")):
        column = target.get(key)
        selected[output] = (
            pd.to_numeric(raw[column], errors="coerce")
            if column and column in raw.columns
            else float("nan")
        )
    return selected.dropna(subset=["year", "observed"])


def _rivm_series(path: Path, group: str) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, float]] = []
    for item in payload.get("selected", []):
        try:
            year = int(item["period"])
            counts = item["counts"]
            total = float(item["total_derived_from_groups"])
            msm = float(counts["msm"])
        except (KeyError, TypeError, ValueError):
            continue
        observed = msm if group == "msm" else total - msm if group == "non_msm" else total
        rows.append({"year": year, "observed": observed, "lower": float("nan"), "upper": float("nan")})
    return pd.DataFrame(rows)


def load_observations(root: Path | str, metric: str) -> ObservationLoad:
    """Load registered observed series that map to one model output metric."""

    root = Path(root).resolve()
    register_path = root / TARGET_REGISTER
    if not register_path.is_file():
        return ObservationLoad(_empty(), ("The tracked target register is unavailable.",))
    register = json.loads(register_path.read_text(encoding="utf-8"))
    wave_path = root / WAVE_PLAN
    wave = json.loads(wave_path.read_text(encoding="utf-8")) if wave_path.is_file() else {}
    scored_years = wave.get("scored_years", {})
    validation_years = wave.get("cross_validation_years", {}) or {}
    workbook = _external_path(root, "history_matching_target_workbook")
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []

    for target in register.get("targets", []):
        role = target.get("role")
        if target.get("model_output") != metric or role not in {"calibration", "cross_validation"}:
            continue
        try:
            if target.get("source_sheet"):
                if workbook is None or not workbook.is_file():
                    warnings.append(
                        "The external validation workbook is not installed, so some observed data cannot be shown."
                    )
                    continue
                frame = _workbook_series(workbook, target)
            elif target.get("source_type") == "rivm_aids_candidate_json":
                source = root / target["candidate_path"]
                if not source.is_file():
                    warnings.append(
                        "The retained RIVM AIDS observation file is unavailable."
                    )
                    continue
                frame = _rivm_series(source, str(target.get("group", "total")))
            else:
                continue
        except (OSError, KeyError, ValueError, ImportError) as exc:
            warnings.append(f"Could not load {target.get('series_name', 'an observed series')}: {exc}")
            continue

        series = str(target.get("series_name", "Observed series"))
        frame["role"] = role
        frame["series"] = series
        frame["source"] = str(target.get("source_organisation") or target.get("source_sheet") or "Recorded source")

        # A calibration-design series is a calibration target only at the
        # explicitly scored years. Other recorded years remain visible as
        # comparison data rather than being silently relabelled as targets.
        scored = {int(year) for year in scored_years.get(series, [])}
        declared_validation = {int(year) for year in validation_years.get(series, [])}
        if role == "calibration" and scored:
            frame["role"] = frame["year"].map(
                lambda year: "calibration" if int(year) in scored else "cross_validation"
            )
        if declared_validation:
            frame.loc[frame["year"].astype(int).isin(declared_validation), "role"] = "cross_validation"
        frames.append(frame)

    if not frames:
        return ObservationLoad(_empty(), tuple(dict.fromkeys(warnings)))
    combined = pd.concat(frames, ignore_index=True)
    combined["year"] = combined["year"].astype(int)
    combined = combined.sort_values(["role", "series", "year"]).reset_index(drop=True)
    return ObservationLoad(combined, tuple(dict.fromkeys(warnings)))
