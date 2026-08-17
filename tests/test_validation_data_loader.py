"""
test_validation_data_loader.py — Tests for validation_data_loader.py

Tests (per PLAN_demographic_calibration_runner.md):

  Parser tests (HMD):
    T1  Totals match known HMD Type-O values (within 0.01%)
    T2  Bin sums equal reported total for each year
    T3  80+ bin captures all ages >= 80

  Parser tests (WB):
    T4  NLD values match known WB figures (spot-check)
    T5  Clean parse: 66 entries, no empty values for 1970-2020
    T6  Missing country returns empty dict + UserWarning

V12.3 — new test module
"""

import os
import sys
import tempfile
import warnings
import csv
import io

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from validation_data_loader import (
    load_hmd_population,
    load_wb_migration,
    default_hmd_path,
    default_wb_path,
    _ADULT_BINS,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
HMD_PATH = default_hmd_path(ROOT)
WB_PATH  = default_wb_path(ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def hmd_data():
    return load_hmd_population(HMD_PATH, year_range=(1975, 2020))


@pytest.fixture(scope="module")
def wb_data():
    return load_wb_migration(WB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# T1 — HMD totals match known Type-O values (within 0.01%)
# ─────────────────────────────────────────────────────────────────────────────

# Ground truth: sum of single-year-of-age Type-O rows in the actual HMD file.
# These are derived from the file itself (not the pre-aggregated TOT rows),
# so they reflect exactly what the loader computes.
_KNOWN_TOTALS = {
    1980: 14_090_318,
    1990: 14_891_976,
    2000: 15_863_207,
    2010: 16_574_989,
}

def test_hmd_totals_match_known_values(hmd_data):
    """T1: Parsed HMD totals match expected values within 0.01%."""
    for yr, expected in _KNOWN_TOTALS.items():
        actual = hmd_data[yr]["total"]
        pct_diff = abs(actual - expected) / expected * 100
        assert pct_diff < 0.01, (
            f"HMD total {yr}: expected {expected:,}, got {actual:,} "
            f"(diff {pct_diff:.4f}%)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T2 — Bin sums equal total for all loaded years
# ─────────────────────────────────────────────────────────────────────────────

def test_hmd_bin_sums_equal_total(hmd_data):
    """T2: sum(all by_age_bin values) == total for every loaded year."""
    for yr, data in hmd_data.items():
        bin_sum = sum(
            data["by_age_bin"][b][s]
            for b in data["by_age_bin"]
            for s in ("male", "female")
        )
        assert bin_sum == data["total"], (
            f"Year {yr}: bin sum {bin_sum:,} != total {data['total']:,}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T3 — 80+ bin captures all HMD ages >= 80
# ─────────────────────────────────────────────────────────────────────────────

def test_hmd_80plus_bin_captures_all_ages(hmd_data):
    """T3: 80+ bin matches manual sum of all single-year ages >= 80 in source file."""
    import csv as _csv

    # Re-read raw file for cross-check year
    check_year = 1990
    raw_80plus_m = 0
    raw_80plus_f = 0

    with open(HMD_PATH) as f:
        reader = _csv.DictReader(f)
        for row in reader:
            if row.get("Type") != "O":
                continue
            age_str = row.get("Age", "").strip()
            if age_str in ("TOT",):
                continue
            try:
                age = int(age_str)
            except ValueError:
                continue
            if age < 80:
                continue
            yr = int(row["Year"])
            if yr != check_year:
                continue
            pop_str = row.get("Population", "").strip()
            if pop_str in (".", ""):
                continue
            sex = row.get("Sex", "").strip()
            pop = int(pop_str)
            if sex == "m":
                raw_80plus_m += pop
            elif sex == "f":
                raw_80plus_f += pop

    loader_80plus_m = hmd_data[check_year]["by_age_bin"]["80+"]["male"]
    loader_80plus_f = hmd_data[check_year]["by_age_bin"]["80+"]["female"]

    assert loader_80plus_m == raw_80plus_m, (
        f"80+ male {check_year}: loader={loader_80plus_m:,}, raw={raw_80plus_m:,}"
    )
    assert loader_80plus_f == raw_80plus_f, (
        f"80+ female {check_year}: loader={loader_80plus_f:,}, raw={raw_80plus_f:,}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# T4 — WB NLD values match known figures
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_WB = {
    1990:  53_564.0,
    2005: -22_496.0,
    2016:  88_743.0,
}

def test_wb_known_values(wb_data):
    """T4: WB NLD values match spot-check figures from source."""
    for yr, expected in _KNOWN_WB.items():
        actual = wb_data[yr]
        assert abs(actual - expected) < 1.0, (
            f"WB {yr}: expected {expected:,.0f}, got {actual:,.0f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T5 — Clean parse: 66 entries, no empty values for 1970-2020
# ─────────────────────────────────────────────────────────────────────────────

def test_wb_clean_parse(wb_data):
    """T5: 66 WB entries loaded (1960-2025), no empty values for core window."""
    assert len(wb_data) == 66, f"Expected 66 WB entries, got {len(wb_data)}"
    assert min(wb_data) == 1960
    assert max(wb_data) == 2025

    # No missing values for 1970-2020
    for yr in range(1970, 2021):
        assert yr in wb_data, f"WB year {yr} missing"
        assert isinstance(wb_data[yr], float), f"WB {yr} is not float"


# ─────────────────────────────────────────────────────────────────────────────
# T6 — Missing country returns empty dict + UserWarning
# ─────────────────────────────────────────────────────────────────────────────

def test_wb_missing_country_returns_empty_with_warning():
    """T6: WB loader returns {} and raises UserWarning when NLD absent."""
    # Build a minimal WB-format CSV with no NLD row
    fake_csv = (
        '"Data Source","World Development Indicators",\n'
        '\n'
        '"Last Updated Date","2026-01-01",\n'
        '\n'
        '"Country Name","Country Code","Indicator Name","Indicator Code","2000","2001"\n'
        '"Belgium","BEL","Net migration","SM.POP.NETM","10000","12000"\n'
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", dir="/tmp", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(fake_csv)
        tmp_path = tf.name

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = load_wb_migration(tmp_path)

    os.unlink(tmp_path)

    assert result == {}, f"Expected empty dict, got {result}"
    user_warns = [w for w in caught if issubclass(w.category, UserWarning)]
    assert len(user_warns) >= 1, "Expected UserWarning for missing NLD"
    assert "NLD" in str(user_warns[0].message) or "Netherlands" in str(user_warns[0].message)



# ─────────────────────────────────────────────────────────────────────────────
# CBS Forecast loader tests (T7–T10, per PLAN_pop_forecast_cbs_tab.md)
# ─────────────────────────────────────────────────────────────────────────────

import csv as _csv
import io as _io

def _make_cbs_csv(rows):
    """
    Helper: build an in-memory CBS-style CSV string and write to a temp file.
    rows: list of (year, observation, forecast, ci95, ci67)
    """
    lines = ["Year;Observation;Forecast 2025-2070;Forecast-interval 95%;Forecast-interval 67%"]
    for year, obs, fcast, ci95, ci67 in rows:
        lines.append(f"{year};{obs};{fcast};{ci95};{ci67}")
    content = "\n".join(lines)
    import tempfile
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", dir="/tmp", delete=False,
        encoding="utf-8-sig", newline=""
    )
    tf.write(content)
    tf.close()
    return tf.name


def test_cbs_loader_normal_parse():
    """T7: Normal parse — 5 rows 2026-2030, valid European decimal, correct units."""
    rows_data = [
        (2024, "17,94", "", "", ""),              # observation only — should be skipped
        (2025, "18,04", "18,14", "18,09 - 18,18", "18,12 - 18,16"),  # 2025 → skipped (< 2026)
        (2026, "", "18,14", "18,09 - 18,18", "18,12 - 18,16"),
        (2027, "", "18,22", "18,12 - 18,31", "18,17 - 18,27"),
        (2028, "", "18,30", "18,14 - 18,46", "18,22 - 18,38"),
        (2029, "", "18,37", "18,14 - 18,59", "18,25 - 18,48"),
        (2030, "", "18,45", "18,13 - 18,73", "18,28 - 18,58"),
    ]
    tmp = _make_cbs_csv(rows_data)
    try:
        from validation_data_loader import load_cbs_forecasts
        data = load_cbs_forecasts(tmp)
    finally:
        os.unlink(tmp)

    assert len(data) == 5, f"Expected 5 keys (2026-2030), got {len(data)}: {list(data.keys())}"

    # Units must be in persons (not millions)
    for yr in range(2026, 2031):
        assert yr in data, f"Year {yr} missing"
        c = data[yr]["central"]
        assert c > 1_000_000, f"Year {yr} central {c} looks like millions, not persons"

    # 2026 spot-check: central = 18.14M = 18_140_000
    assert data[2026]["central"] == 18_140_000, f"2026 central: {data[2026]['central']}"

    # CI bounds ordered: lo < central < hi
    for yr, entry in data.items():
        if entry["ci95_lo"] is not None and entry["ci95_hi"] is not None:
            assert entry["ci95_lo"] < entry["central"] < entry["ci95_hi"], \
                f"Year {yr}: 95% CI not ordered: {entry}"
        if entry["ci67_lo"] is not None and entry["ci67_hi"] is not None:
            assert entry["ci67_lo"] < entry["central"] < entry["ci67_hi"], \
                f"Year {yr}: 67% CI not ordered: {entry}"


def test_cbs_loader_file_not_found():
    """T8: FileNotFoundError raised when path doesn't exist."""
    from validation_data_loader import load_cbs_forecasts
    import pytest
    with pytest.raises(FileNotFoundError):
        load_cbs_forecasts("/tmp/nonexistent_cbs_file_xyz.csv")


def test_cbs_loader_malformed_interval():
    """T9: Malformed interval strings are tolerated — CI fields set to None, no exception."""
    rows_data = [
        (2026, "", "18,14", "N/A - N/A",        "N/A - N/A"),   # unparseable
        (2027, "", "18,22", "18,12 - 18,31",    "18,17 - 18,27"),  # valid
    ]
    tmp = _make_cbs_csv(rows_data)
    try:
        from validation_data_loader import load_cbs_forecasts
        import warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            data = load_cbs_forecasts(tmp)
    finally:
        os.unlink(tmp)

    # Both years should be present (not skipped entirely)
    assert 2026 in data, "Year 2026 should still be present despite bad CI"
    assert 2027 in data, "Year 2027 should be present"

    # 2026 CI should be None
    assert data[2026]["ci95_lo"] is None, "Expected ci95_lo=None for malformed row"
    assert data[2026]["ci95_hi"] is None, "Expected ci95_hi=None for malformed row"

    # 2027 CI should be parsed correctly
    assert data[2027]["ci95_lo"] is not None, "2027 95% CI lo should be parsed"
    assert data[2027]["ci95_hi"] is not None, "2027 95% CI hi should be parsed"


def test_cbs_default_path():
    """T10: default_cbs_path returns path ending with correct filename."""
    from validation_data_loader import default_cbs_path
    p = default_cbs_path("/some/v11/root")
    assert p.endswith("NL_Population_Forecasts.csv"), \
        f"Expected path ending in NL_Population_Forecasts.csv, got: {p}"
    assert "Validation" in p, f"Expected 'Validation' in path, got: {p}"
