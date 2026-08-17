"""
Summary statistics for Sequential Neural Posterior Estimation (SNPE) calibration.

Converts model outputs from calib_extractor.extract_annual_outputs into a
fixed-length numpy vector that serves as x_sim for the sbi library.

Public API
----------
compute_summary_stats(model_outputs, spec=None) -> np.ndarray
    Build x_sim from a single simulation run's output dict.

observed_summary_stats(target_records, spec=None) -> np.ndarray
    Build x_obs from the RIVM/UNAIDS calibration target records.

summary_stat_labels(spec=None) -> list[str]
    Column names matching the vector (e.g. 'PLHIV_1990', 'on_art_stock_2023').

N_SUMMARY_STATS : int
    Length of the default vector.

Design notes
------------
- All values are log1p-transformed to prevent scale domination (PLHIV ~23 000
  vs AIDS deaths ~50).  The inverse is np.expm1.
- Collapsed simulations (missing or non-finite outputs) receive a finite
  sentinel rather than NaN/inf so neural network training stays stable.
- This module has no imports from any other v11 calibration module, so it can
  be imported by both run_snpe_calibration.py and test code without circular
  dependencies.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping

import numpy as np

# ---------------------------------------------------------------------------
# Default summary statistic specification.
# Each element: (model_output_key, list_of_target_years).
# These indicators and years match existing calibration targets and
# calib_extractor output keys where possible. The non-MSM diagnosis statistic
# is derived from total diagnoses minus MSM diagnoses, so no simulator change
# is required.
# ---------------------------------------------------------------------------
DEFAULT_SUMMARY_STAT_SPEC: list[tuple[str, list[int]]] = [
    ("PLHIV",             [1990, 1995, 2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023]),
    ("new_infections",    [1990, 1995, 2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023]),
    ("new_diagnoses",     [1990, 1995, 2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023, 2024]),
    ("new_diagnoses_msm", [1995, 2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023, 2024]),
    ("new_diagnoses_non_msm", [1996, 2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023, 2024]),
    ("aids_deaths",       [1990, 1995, 2000, 2005, 2010, 2015, 2020, 2023]),
    ("on_art_stock",      [2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023]),
]

# Sentinel: used when a model output for a given indicator/year is missing or
# non-finite.  A small positive value so log1p(COLLAPSE_SENTINEL) is finite
# and clearly distinguishable from plausible epidemic outputs.
COLLAPSE_SENTINEL: float = 1.0

# Column name prefix written into particle CSVs.
STAT_COLUMN_PREFIX: str = "x_"


def _resolve_spec(
    spec: list[tuple[str, list[int]]] | None,
) -> list[tuple[str, list[int]]]:
    return spec if spec is not None else DEFAULT_SUMMARY_STAT_SPEC


def summary_stat_labels(
    spec: list[tuple[str, list[int]]] | None = None,
) -> list[str]:
    """Return column names matching the summary statistic vector.

    Each label has the form ``"{indicator}_{year}"``, e.g. ``"PLHIV_1990"``.
    The order matches the vector returned by :func:`compute_summary_stats`.
    """
    labels: list[str] = []
    for indicator, years in _resolve_spec(spec):
        for year in years:
            labels.append(f"{indicator}_{year}")
    return labels


def _count_stats(spec: list[tuple[str, list[int]]] | None = None) -> int:
    return sum(len(years) for _, years in _resolve_spec(spec))


#: Total number of summary statistics under the default spec.
N_SUMMARY_STATS: int = _count_stats()


def _derived_non_msm_series(
    outputs: Mapping[str, Mapping[int, float]],
) -> dict[int, float]:
    """Return total diagnoses minus MSM diagnoses by year."""
    explicit = outputs.get("new_diagnoses_non_msm")
    if explicit:
        return {int(year): float(value) for year, value in explicit.items()}

    total = outputs.get("new_diagnoses", {})
    msm = outputs.get("new_diagnoses_msm", {})
    derived: dict[int, float] = {}
    for year in set(total) & set(msm):
        try:
            value = float(total[year]) - float(msm[year])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            derived[int(year)] = max(0.0, value)
    return derived


def _series_for_indicator(
    outputs: Mapping[str, Mapping[int, float]],
    indicator: str,
) -> Mapping[int, float]:
    if indicator == "new_diagnoses_non_msm":
        return _derived_non_msm_series(outputs)
    return outputs.get(indicator, {})


def compute_summary_stats(
    model_outputs: Mapping[str, Mapping[int, float]],
    spec: list[tuple[str, list[int]]] | None = None,
) -> np.ndarray:
    """Build a log1p-transformed summary statistic vector from model outputs.

    Parameters
    ----------
    model_outputs:
        Output dict from ``calib_extractor.extract_annual_outputs``.
        Keys are indicator names; values are ``{year: float}`` dicts.
    spec:
        Summary statistic specification.  Defaults to
        :data:`DEFAULT_SUMMARY_STAT_SPEC`.

    Returns
    -------
    np.ndarray
        Shape ``(N,)`` float32 array of log1p-transformed values.
        Missing or non-finite entries are replaced by
        ``math.log1p(COLLAPSE_SENTINEL)``.
    """
    spec = _resolve_spec(spec)
    values: list[float] = []
    sentinel_log = math.log1p(COLLAPSE_SENTINEL)

    for indicator, years in spec:
        series = _series_for_indicator(model_outputs, indicator)
        for year in years:
            raw = series.get(year, None)
            if raw is None:
                values.append(sentinel_log)
            else:
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    values.append(sentinel_log)
                    continue
                if not math.isfinite(v) or v < 0.0:
                    values.append(sentinel_log)
                else:
                    values.append(math.log1p(v))

    return np.array(values, dtype=np.float32)


def observed_summary_stats(
    target_records: Iterable[dict],
    spec: list[tuple[str, list[int]]] | None = None,
) -> np.ndarray:
    """Build the observed summary statistic vector *x_obs* from target records.

    Parameters
    ----------
    target_records:
        Records from ``calib_target_loader.load_disease_targets``.
        Each record must have ``'model_output'``, ``'year'``, and
        ``'estimate'`` keys.
    spec:
        Must match the spec used for :func:`compute_summary_stats`.

    Returns
    -------
    np.ndarray
        Same shape and dtype as :func:`compute_summary_stats`.
        Indicators/years absent from the target records receive the sentinel.
    """
    spec = _resolve_spec(spec)
    sentinel_log = math.log1p(COLLAPSE_SENTINEL)

    # Build a fast lookup: (indicator, year) -> estimate
    lookup: dict[tuple[str, int], float] = {}
    for rec in target_records:
        key = (str(rec["model_output"]), int(rec["year"]))
        try:
            val = float(rec["estimate"])
        except (TypeError, ValueError):
            continue
        if math.isfinite(val) and val >= 0.0:
            lookup[key] = val

    total_years = {
        year for indicator, year in lookup
        if indicator == "new_diagnoses"
    }
    msm_years = {
        year for indicator, year in lookup
        if indicator == "new_diagnoses_msm"
    }
    for year in total_years & msm_years:
        total = lookup[("new_diagnoses", year)]
        msm = lookup[("new_diagnoses_msm", year)]
        lookup.setdefault(("new_diagnoses_non_msm", year), max(0.0, total - msm))

    values: list[float] = []
    for indicator, years in spec:
        for year in years:
            raw = lookup.get((indicator, year), None)
            if raw is None:
                values.append(sentinel_log)
            else:
                values.append(math.log1p(raw))

    return np.array(values, dtype=np.float32)


def stat_row_fields(
    x: np.ndarray,
    spec: list[tuple[str, list[int]]] | None = None,
) -> dict[str, float]:
    """Flatten a summary stat vector into CSV-friendly column fields.

    Columns are prefixed with :data:`STAT_COLUMN_PREFIX` (``"x_"``), e.g.
    ``"x_PLHIV_1990"``.

    Parameters
    ----------
    x:
        Vector returned by :func:`compute_summary_stats`.
    spec:
        Must match the spec used to build *x*.

    Returns
    -------
    dict[str, float]
        Ready to merge into a particle CSV row dict.
    """
    labels = summary_stat_labels(spec)
    if len(x) != len(labels):
        raise ValueError(
            f"Vector length {len(x)} does not match spec length {len(labels)}"
        )
    return {f"{STAT_COLUMN_PREFIX}{label}": float(v) for label, v in zip(labels, x)}
