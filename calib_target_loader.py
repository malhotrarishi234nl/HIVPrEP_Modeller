"""
Disease calibration target loader.

The disease workbook is treated as a source-facing, read-only input.  This
module translates source-style sheets/columns into the normalized target records
used by the LHS scorer without requiring spreadsheet column-name changes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


PRIMARY_USES = {"primary", "secondary"}


def default_disease_workbook_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else Path(__file__).resolve().parent
    return base / "Validation " / "DiseaseData.xlsx"


def default_target_mapping_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else Path(__file__).resolve().parent
    return base / "configs" / "disease_target_mapping.json"


def load_mapping(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        mapping = json.load(f)
    if "targets" not in mapping or not isinstance(mapping["targets"], list):
        raise ValueError("Disease target mapping must contain a list field named 'targets'")
    return mapping


def _parse_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).strip().replace(",", "")
    if cleaned in {"", ".", "..", "...", "NA", "N/A", "nan"}:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _parse_year(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.startswith("<="):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_mapped_float(row: pd.Series, spec: dict, column_key: str) -> float | None:
    column = spec.get(column_key)
    if not column:
        return None
    value = _parse_float(row.get(column))
    subtract_column = spec.get(f"{column_key}_subtract_column")
    if subtract_column:
        subtract_value = _parse_float(row.get(subtract_column))
        if value is None:
            return None
        if subtract_value is not None:
            value -= subtract_value
    return value


def compute_sigma(estimate: float, lower: float | None, upper: float | None, cv: float = 0.10) -> float:
    lower_ok = lower is not None and math.isfinite(float(lower))
    upper_ok = upper is not None and math.isfinite(float(upper))
    if lower_ok and upper_ok and float(upper) > float(lower):
        sigma = (float(upper) - float(lower)) / 3.92
    else:
        sigma = abs(float(estimate)) * float(cv)
    return max(float(sigma), 1e-9)


def _read_sheet(workbook_path: Path, sheet_name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(workbook_path, sheet_name=sheet_name, header=1)
    except ValueError as exc:
        raise ValueError(f"Disease workbook missing sheet '{sheet_name}'") from exc


def _apply_filters(df: pd.DataFrame, filters: dict | None, target_name: str) -> pd.DataFrame:
    work = df
    for column, expected in (filters or {}).items():
        if column not in work.columns:
            raise ValueError(f"Target '{target_name}' filter column missing from workbook: {column}")
        work = work[work[column].astype(str).str.strip() == str(expected).strip()]
    return work


def load_disease_targets(
    workbook_path: str | Path,
    mapping_path: str | Path,
    stage: str = "joint",
    start_year: int | None = None,
    end_year: int | None = None,
    include_uses: Iterable[str] = PRIMARY_USES,
) -> list[dict]:
    """Load normalized target records from the disease workbook."""
    workbook_path = Path(workbook_path)
    mapping = load_mapping(mapping_path)
    if not workbook_path.exists():
        raise FileNotFoundError(f"Disease target workbook not found: {workbook_path}")

    include_uses = {str(value) for value in include_uses}
    sheet_cache: dict[str, pd.DataFrame] = {}
    records: list[dict] = []

    for spec in mapping["targets"]:
        spec_stage = spec.get("stages", ["joint"])
        if stage != "all" and stage not in spec_stage:
            continue
        calibration_use = spec.get("calibration_use", "primary")
        if calibration_use not in include_uses:
            continue

        sheet = spec["sheet"]
        if sheet not in sheet_cache:
            sheet_cache[sheet] = _read_sheet(workbook_path, sheet)
        df = _apply_filters(sheet_cache[sheet], spec.get("filters"), spec["name"])

        year_col = spec["year_column"]
        estimate_col = spec["estimate_column"]
        lower_col = spec.get("lower_column")
        upper_col = spec.get("upper_column")
        for col in (year_col, estimate_col):
            if col not in df.columns:
                raise ValueError(f"Target '{spec['name']}' column missing from workbook: {col}")
        for col in (
            spec.get("estimate_column_subtract_column"),
            spec.get("lower_column_subtract_column"),
            spec.get("upper_column_subtract_column"),
        ):
            if col and col not in df.columns:
                raise ValueError(f"Target '{spec['name']}' column missing from workbook: {col}")

        for source_row, row in df.iterrows():
            year = _parse_year(row.get(year_col))
            if year is None:
                continue
            if "start_year" in spec and year < int(spec["start_year"]):
                continue
            if "end_year" in spec and year > int(spec["end_year"]):
                continue
            if start_year is not None and year < start_year:
                continue
            if end_year is not None and year > end_year:
                continue

            estimate = _parse_mapped_float(row, spec, "estimate_column")
            if estimate is None:
                continue
            lower = _parse_mapped_float(row, spec, "lower_column") if lower_col else None
            upper = _parse_mapped_float(row, spec, "upper_column") if upper_col else None
            sigma = compute_sigma(estimate, lower, upper, spec.get("sigma_fallback_cv", 0.10))
            records.append(
                {
                    "target_name": spec["name"],
                    "source_sheet": sheet,
                    "source_row": int(source_row) + 3,
                    "target_family": spec["target_family"],
                    "model_output": spec["model_output"],
                    "calibration_use": calibration_use,
                    "year": int(year),
                    "estimate": float(estimate),
                    "lower": None if lower is None else float(lower),
                    "upper": None if upper is None else float(upper),
                    "sigma": sigma,
                    "weight": float(spec.get("weight", 1.0)),
                    "stage": stage,
                    "unit": str(row.get("Unit", spec.get("unit", ""))),
                }
            )

    if not records:
        raise ValueError(
            f"No disease calibration targets loaded for stage='{stage}' from {workbook_path}"
        )
    return records


def targets_to_nested(records: Iterable[dict]) -> dict[str, dict[int, dict]]:
    """Compatibility helper for legacy indicator/year target consumers."""
    nested: dict[str, dict[int, dict]] = {}
    for record in records:
        output = record["model_output"]
        year = int(record["year"])
        nested.setdefault(output, {})[year] = {
            "estimate": float(record["estimate"]),
            "lower": record.get("lower"),
            "upper": record.get("upper"),
            "sigma": float(record["sigma"]),
            "weight": float(record.get("weight", 1.0)),
            "target_family": record.get("target_family"),
            "calibration_use": record.get("calibration_use"),
            "target_name": record.get("target_name"),
        }
    return nested


def write_normalized_targets_csv(path: str | Path, records: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(list(records)).to_csv(path, index=False)
