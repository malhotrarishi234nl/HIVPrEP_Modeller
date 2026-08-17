"""
validation_data_loader.py — Reference Data Parsers for Demographic Calibration

Provides clean loading of two external reference datasets:

  1. HMD Netherlands population (HMD_NL_Pop.txt)
     Source: Human Mortality Database — www.mortality.org
     File type: single-year-of-age, annual snapshots (Type O = Jan 1)

  2. World Bank net migration (Migration_Net.csv)
     Source: World Development Indicators — SM.POP.NETM, Netherlands
     URL: https://data.worldbank.org/indicator/SM.POP.NETM?locations=NL

Both functions are pure: no simulation state, no side effects.
Errors in parsing raise ValueError with a descriptive message.

V12.5 — new module
"""

import csv
import io
import logging
import warnings
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Age-bin helpers
# ─────────────────────────────────────────────────────────────────────────────

# Model 5-year bins (matches demography.py / group system)
_CHILD_BINS = ["0-4", "5-9", "10-14"]
_ADULT_BINS = [
    "15-19", "20-24", "25-29", "30-34", "35-39",
    "40-44", "45-49", "50-54", "55-59", "60-64",
    "65-69", "70-74", "75-79", "80+",
]
ALL_BINS = _CHILD_BINS + _ADULT_BINS


def _age_to_bin(age: int) -> str:
    """Map a single HMD age (int) to the model's 5-year bin label."""
    if age >= 80:
        return "80+"
    lo = (age // 5) * 5
    hi = lo + 4
    return f"{lo}-{hi}"


# ─────────────────────────────────────────────────────────────────────────────
# HMD loader
# ─────────────────────────────────────────────────────────────────────────────

def load_hmd_population(
    path: str,
    year_range: Optional[Tuple[int, int]] = None,
) -> Dict[int, dict]:
    """
    Parse HMD Netherlands population file (single-year-of-age, annual).

    Uses Type='O' records (Jan 1 snapshot), which are available 1950–2024
    in the current NLD file.  Type='E' (Dec 31) only extends to 2000 and
    has sparse coverage — not used here.

    Parameters
    ----------
    path : str
        Absolute path to HMD_NL_Pop.txt
    year_range : (start, end) inclusive, optional
        Restrict output to this year range.  Defaults to full file range.

    Returns
    -------
    dict keyed by year (int) with structure:
        {
          year: {
            "total":  int,           # male + female, all ages
            "male":   int,
            "female": int,
            "by_age_bin": {
              "0-4":   {"male": int, "female": int},
              "5-9":   ...,
              ...
              "80+":   ...,
            }
          }
        }

    Raises
    ------
    ValueError  if the file cannot be parsed or contains no usable rows.
    FileNotFoundError  if path does not exist.
    """
    # Accumulate per-year, per-age, per-sex counts
    # raw[year][age_bin]["male"|"female"] -> int
    raw: Dict[int, Dict[str, Dict[str, int]]] = {}

    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Type", "").strip() != "O":
                    continue
                if row.get("Age", "").strip() == "TOT":
                    continue

                age_str = row.get("Age", "").strip()
                sex_str = row.get("Sex", "").strip()
                pop_str = row.get("Population", "").strip()
                year_str = row.get("Year", "").strip()

                if not age_str or not sex_str or not pop_str or not year_str:
                    continue
                if pop_str in (".", ""):
                    continue
                if sex_str not in ("m", "f"):
                    continue  # skip 'b' (both sexes combined)

                try:
                    age = int(age_str)
                    year = int(year_str)
                    pop = int(pop_str)
                except ValueError:
                    continue

                if year_range and not (year_range[0] <= year <= year_range[1]):
                    continue

                bin_label = _age_to_bin(age)
                sex_key = "male" if sex_str == "m" else "female"

                if year not in raw:
                    raw[year] = {}
                if bin_label not in raw[year]:
                    raw[year][bin_label] = {"male": 0, "female": 0}
                raw[year][bin_label][sex_key] += pop

    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse HMD file at '{path}': {exc}") from exc

    if not raw:
        raise ValueError(
            f"No usable Type='O' records found in HMD file: '{path}'. "
            "Check that the file is the correct NLD HMD export."
        )

    # Build output dict
    result: Dict[int, dict] = {}
    for year, bins in sorted(raw.items()):
        male_total = sum(v["male"] for v in bins.values())
        female_total = sum(v["female"] for v in bins.values())

        # Ensure all model bins are present (fill with 0 if HMD lacks data)
        by_age_bin = {}
        for b in ALL_BINS:
            entry = bins.get(b, {"male": 0, "female": 0})
            by_age_bin[b] = {"male": entry["male"], "female": entry["female"]}

        result[year] = {
            "total": male_total + female_total,
            "male": male_total,
            "female": female_total,
            "by_age_bin": by_age_bin,
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# World Bank loader
# ─────────────────────────────────────────────────────────────────────────────

def load_wb_migration(path: str) -> Dict[int, float]:
    """
    Parse World Bank net migration CSV for Netherlands (NLD).

    The WB export format includes 4 metadata header rows before the actual
    column headers.  This function detects the real header line by looking
    for 'Country Name', then reads from there.

    Parameters
    ----------
    path : str
        Absolute path to Migration_Net.csv (WB SM.POP.NETM export)

    Returns
    -------
    dict keyed by year (int) -> net_migration (float)
    Returns {} with UserWarning if Netherlands row not found.

    Raises
    ------
    FileNotFoundError  if path does not exist.
    ValueError  if file cannot be parsed.
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.readlines()
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to open WB migration file at '{path}': {exc}") from exc

    # Find the real header line (contains "Country Name")
    header_idx = None
    for i, line in enumerate(lines):
        if "Country Name" in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            f"Could not find 'Country Name' header in WB file: '{path}'. "
            "File may not be a valid WB SM.POP.NETM export."
        )

    data_text = "".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_text))

    nld_row = None
    for row in reader:
        if row.get("Country Code", "").strip() == "NLD":
            nld_row = row
            break

    if nld_row is None:
        warnings.warn(
            "Netherlands (Country Code='NLD') not found in WB migration file. "
            f"Path: '{path}'. Returning empty dict.",
            UserWarning,
            stacklevel=2,
        )
        return {}

    result: Dict[int, float] = {}
    for key, val in nld_row.items():
        key_stripped = key.strip()
        if not key_stripped.isdigit():
            continue
        val_stripped = val.strip()
        if not val_stripped:
            continue
        try:
            result[int(key_stripped)] = float(val_stripped)
        except ValueError:
            continue

    if not result:
        raise ValueError(
            f"Found NLD row in WB file but extracted no year values from '{path}'."
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: default paths relative to the v11 folder
# ─────────────────────────────────────────────────────────────────────────────

def default_hmd_path(v11_root: str) -> str:
    import os
    return os.path.join(v11_root, "Validation ", "HMD_NL_Pop.txt")


def default_wb_path(v11_root: str) -> str:
    import os
    return os.path.join(v11_root, "Validation ", "Migration_Net.csv")


# ─────────────────────────────────────────────────────────────────────────────
# CBS (Centraal Bureau voor de Statistiek) forecast loader
# ─────────────────────────────────────────────────────────────────────────────

def load_cbs_forecasts(path: str) -> dict:
    """
    Parse CBS Netherlands population forecast CSV (2026–2070).

    Source: CBS StatLine — Bevolkingsprognose 2024–2070
    URL: https://opendata.cbs.nl/statline/#/CBS/nl/dataset/37672/table

    File format:
        - Delimiter: semicolon
        - BOM-prefixed UTF-8 (utf-8-sig)
        - European decimal comma (e.g. "18,14" = 18.14 million)
        - Columns: Year; Observation; Forecast 2025-2070;
                   Forecast-interval 95%; Forecast-interval 67%
        - Units: millions of persons
        - Interval format: "18,09 - 18,18" (space-dash-space separator)

    Only rows with Year >= 2026 and a non-empty Forecast column are returned.
    Malformed interval strings are tolerated — that year's CI fields are set
    to None and a warning is logged; parsing continues.

    Parameters
    ----------
    path : str
        Absolute path to NL_Population_Forecasts.csv

    Returns
    -------
    dict keyed by year (int):
        {
          year: {
            "central":   int,          # central forecast in persons
            "ci95_lo":   int or None,  # 95% CI lower bound in persons
            "ci95_hi":   int or None,
            "ci67_lo":   int or None,  # 67% CI lower bound in persons
            "ci67_hi":   int or None,
          }
        }

    Raises
    ------
    FileNotFoundError  if path does not exist.
    ValueError         if the file has no parseable forecast rows.
    """
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"CBS forecast file not found: '{path}'")

    def _parse_millions(s: str):
        """Convert European-decimal millions string to integer persons, or None."""
        s = s.strip()
        if not s:
            return None
        try:
            return int(round(float(s.replace(",", ".")) * 1_000_000))
        except (ValueError, AttributeError):
            return None

    def _parse_interval(s: str):
        """
        Parse CBS interval string like '18,09 - 18,18' into (lo_persons, hi_persons).
        Returns (None, None) on any parse failure.
        """
        s = s.strip()
        if not s:
            return None, None
        parts = s.split(" - ")
        if len(parts) != 2:
            return None, None
        lo = _parse_millions(parts[0])
        hi = _parse_millions(parts[1])
        return lo, hi

    result = {}
    rows_parsed = 0

    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                year_str     = row.get("Year", "").strip()
                forecast_str = row.get("Forecast 2025-2070", "").strip()
                ci95_str     = row.get("Forecast-interval 95%", "").strip()
                ci67_str     = row.get("Forecast-interval 67%", "").strip()

                if not year_str or not forecast_str:
                    continue

                try:
                    year = int(year_str)
                except ValueError:
                    continue

                if year < 2026:
                    continue

                central = _parse_millions(forecast_str)
                if central is None:
                    continue

                ci95_lo, ci95_hi = _parse_interval(ci95_str)
                ci67_lo, ci67_hi = _parse_interval(ci67_str)

                if ci95_lo is None or ci95_hi is None:
                    logger.warning(
                        "CBS forecast: could not parse 95%% CI for year %d "
                        "(raw: '%s') — CI fields set to None",
                        year, ci95_str,
                    )
                if ci67_lo is None or ci67_hi is None:
                    logger.warning(
                        "CBS forecast: could not parse 67%% CI for year %d "
                        "(raw: '%s') — CI fields set to None",
                        year, ci67_str,
                    )

                result[year] = {
                    "central":  central,
                    "ci95_lo":  ci95_lo,
                    "ci95_hi":  ci95_hi,
                    "ci67_lo":  ci67_lo,
                    "ci67_hi":  ci67_hi,
                }
                rows_parsed += 1

    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Failed to parse CBS forecast file at '{path}': {exc}"
        ) from exc

    if not result:
        raise ValueError(
            f"No usable CBS forecast rows (Year >= 2026) found in '{path}'. "
            "Check that the file is a valid CBS StatLine export."
        )

    return result


def default_cbs_path(v11_root: str) -> str:
    import os
    return os.path.join(v11_root, "Validation ", "NL_Population_Forecasts.csv")
