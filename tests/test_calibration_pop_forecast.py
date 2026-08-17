"""
test_calibration_pop_forecast.py — Tests for CBS population forecast check
and Pop_Forecast tab (per PLAN_pop_forecast_cbs_tab.md, T11–T20).

Tests:
  check_pop_forecast():
    T11  Model exactly at CBS central — all within_95/67 True, deviation ~0%
    T12  Model 30% below CBS (known discrepancy) — all outside 95% CI
    T13  No overlapping years — returns []
    T14  Partial overlap — correct row count

  CalibrationReportWriter:
    T15  Pop_Forecast tab renders with valid data — correct structure
    T16  Pop_Forecast tab renders gracefully with empty data
    T17  Summary status logic (parametrized)

  Regression:
    T18  run_all_checks() signature backward compatible (no cbs_path kwarg)
    T19  All 8 tabs present after full write
    T20  _summary_pop_forecast N/A when no data
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from run_calibration import check_pop_forecast
from calibration_output_writer import CalibrationReportWriter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_summary_df(start_year=2024, end_year=2031, pop=18_000_000):
    """Build a minimal summary DataFrame with quarterly steps."""
    rows = []
    for yr in range(start_year, end_year + 1):
        for q in [0.0, 0.25, 0.50, 0.75]:
            rows.append({"year": yr + q, "total_population": pop})
    return pd.DataFrame(rows)


def _make_cbs_data(start=2026, end=2031, central=18_000_000,
                   ci95_lo=17_500_000, ci95_hi=18_500_000,
                   ci67_lo=17_800_000, ci67_hi=18_200_000):
    return {
        yr: {
            "central":  central,
            "ci95_lo":  ci95_lo,
            "ci95_hi":  ci95_hi,
            "ci67_lo":  ci67_lo,
            "ci67_hi":  ci67_hi,
        }
        for yr in range(start, end + 1)
    }


def _make_minimal_results(forecast_rows=None):
    """Minimal results dict for CalibrationReportWriter."""
    return {
        "meta": {
            "run_date": "2026-05-05", "config_path": "test",
            "sim_window": "2000-2035", "sim_start": 2000, "sim_end": 2035,
            "hmd_path": "hmd", "wb_path": "wb", "cbs_path": "cbs",
        },
        "config_audit": [], "pop_trajectory": [], "age_structure": {},
        "migration_coverage": [], "pop_forecast": forecast_rows or [], "summary_df": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# T11 — Model exactly at CBS central
# ─────────────────────────────────────────────────────────────────────────────

def test_check_pop_forecast_model_at_central():
    """T11: Model = CBS central → within_95=True, within_67=True, deviation ≈ 0%."""
    df  = _make_summary_df(2024, 2030, pop=18_000_000)
    cbs = _make_cbs_data(2026, 2030, central=18_000_000,
                         ci95_lo=17_500_000, ci95_hi=18_500_000,
                         ci67_lo=17_800_000, ci67_hi=18_200_000)
    rows = check_pop_forecast(df, cbs)

    assert len(rows) == 5, f"Expected 5 rows, got {len(rows)}"
    for row in rows:
        assert row["within_95"] is True, f"Year {row['year']}: expected within_95=True"
        assert row["within_67"] is True, f"Year {row['year']}: expected within_67=True"
        assert abs(row["deviation_from_central"]) < 0.01, \
            f"Year {row['year']}: deviation {row['deviation_from_central']} not near 0"


# ─────────────────────────────────────────────────────────────────────────────
# T12 — Model 30% below CBS (simulates known discrepancy)
# ─────────────────────────────────────────────────────────────────────────────

def test_check_pop_forecast_model_below_ci():
    """T12: Model 30% below CBS central → all outside 95% CI."""
    df  = _make_summary_df(2024, 2030, pop=12_600_000)  # 30% below 18M
    cbs = _make_cbs_data(2026, 2030, central=18_000_000,
                         ci95_lo=17_500_000, ci95_hi=18_500_000,
                         ci67_lo=17_800_000, ci67_hi=18_200_000)
    rows = check_pop_forecast(df, cbs)

    assert len(rows) == 5, f"Expected 5 rows, got {len(rows)}"
    for row in rows:
        assert row["within_95"] is False, f"Year {row['year']}: expected within_95=False"
        assert row["within_67"] is False, f"Year {row['year']}: expected within_67=False"
        assert row["deviation_from_central"] < -20, \
            f"Year {row['year']}: deviation {row['deviation_from_central']} should be < -20%"


# ─────────────────────────────────────────────────────────────────────────────
# T13 — No overlapping years
# ─────────────────────────────────────────────────────────────────────────────

def test_check_pop_forecast_no_overlap():
    """T13: summary_df ends 2025; cbs_data starts 2026 → returns []."""
    df  = _make_summary_df(2020, 2025, pop=17_800_000)
    cbs = _make_cbs_data(2026, 2030)
    rows = check_pop_forecast(df, cbs)
    assert rows == [], f"Expected [], got {rows}"


# ─────────────────────────────────────────────────────────────────────────────
# T14 — Partial overlap
# ─────────────────────────────────────────────────────────────────────────────

def test_check_pop_forecast_partial_overlap():
    """T14: summary_df 2024-2028; cbs_data 2026-2070 → 3 rows (2026, 2027, 2028)."""
    df  = _make_summary_df(2024, 2028, pop=18_000_000)
    cbs = _make_cbs_data(2026, 2070)
    rows = check_pop_forecast(df, cbs)

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    years = [r["year"] for r in rows]
    assert years == [2026, 2027, 2028], f"Expected [2026, 2027, 2028], got {years}"


# ─────────────────────────────────────────────────────────────────────────────
# T15 — Pop_Forecast tab renders with valid data
# ─────────────────────────────────────────────────────────────────────────────

def test_writer_pop_forecast_tab_renders():
    """T15: Pop_Forecast tab present, has correct headers, Summary has Check 5 row."""
    from openpyxl import load_workbook

    forecast_rows = []
    for yr in range(2026, 2031):
        forecast_rows.append({
            "year": yr, "model_total": 15_000_000, "model_millions": 15.0,
            "cbs_central_millions": 18.4, "ci67_lo_millions": 18.2, "ci67_hi_millions": 18.6,
            "ci95_lo_millions": 18.1, "ci95_hi_millions": 18.7,
            "within_67": False, "within_95": False, "deviation_from_central": -18.5,
        })

    results = _make_minimal_results(forecast_rows)
    out = "/tmp/test_pop_forecast_tab.xlsx"
    CalibrationReportWriter(results, out).write()

    wb = load_workbook(out)
    assert "Pop_Forecast" in wb.sheetnames, "Pop_Forecast tab missing"

    ws = wb["Pop_Forecast"]
    # Find header row (should contain "Year" and "Model (M)")
    header_found = False
    for row in ws.iter_rows(values_only=True):
        if row and "Year" in str(row[0] or "") and "Model" in str(row[1] or ""):
            header_found = True
            break
    assert header_found, "Header row with 'Year' / 'Model (M)' not found"

    # Data rows: 5 years
    data_rows = [
        r for r in ws.iter_rows(values_only=True)
        if r and isinstance(r[0], int) and 2026 <= r[0] <= 2030
    ]
    assert len(data_rows) == 5, f"Expected 5 data rows, got {len(data_rows)}"

    # Summary has Check 5 row
    ws_sum = wb["Summary"]
    check5_found = any(
        "5" in str(ws_sum.cell(row=i, column=1).value or "")
        and "Forecast" in str(ws_sum.cell(row=i, column=1).value or "")
        for i in range(1, ws_sum.max_row + 1)
    )
    assert check5_found, "Check 5 row not found in Summary tab"

    os.unlink(out)


# ─────────────────────────────────────────────────────────────────────────────
# T16 — Pop_Forecast tab renders gracefully with empty data
# ─────────────────────────────────────────────────────────────────────────────

def test_writer_pop_forecast_empty():
    """T16: pop_forecast=[] → tab present with info cell, no exception."""
    from openpyxl import load_workbook

    results = _make_minimal_results(forecast_rows=[])
    out = "/tmp/test_pop_forecast_empty.xlsx"
    CalibrationReportWriter(results, out).write()

    wb = load_workbook(out)
    assert "Pop_Forecast" in wb.sheetnames, "Pop_Forecast tab missing even when empty"

    ws = wb["Pop_Forecast"]
    # Should contain the "does not overlap" message somewhere
    found_msg = any(
        "overlap" in str(ws.cell(row=i, column=1).value or "").lower()
        for i in range(1, ws.max_row + 1)
    )
    assert found_msg, "Expected 'overlap' info message in empty Pop_Forecast tab"

    os.unlink(out)


# ─────────────────────────────────────────────────────────────────────────────
# T17 — Summary status logic (parametrized)
# ─────────────────────────────────────────────────────────────────────────────

def _make_forecast_rows_with_coverage(total, within_95_count):
    """Build synthetic forecast rows where within_95_count rows are inside CI."""
    rows = []
    for i in range(total):
        inside = i < within_95_count
        rows.append({
            "year": 2026 + i,
            "model_total": 18_000_000 if inside else 15_000_000,
            "model_millions": 18.0 if inside else 15.0,
            "cbs_central_millions": 18.4,
            "ci67_lo_millions": 18.2, "ci67_hi_millions": 18.6,
            "ci95_lo_millions": 17_500_000 / 1e6, "ci95_hi_millions": 18_500_000 / 1e6,
            "within_67": inside, "within_95": inside,
            "deviation_from_central": 0.0 if inside else -18.5,
        })
    return rows


@pytest.mark.parametrize("total,within_95,expected_status", [
    (5, 5, "PASS"),   # 100% within → PASS
    (5, 4, "PASS"),   # 80% within → PASS
    (5, 3, "WARN"),   # 60% within → WARN
    (4, 2, "WARN"),   # 50% (2/4) → WARN (boundary: 50% = WARN)
    (5, 2, "FAIL"),   # 40% (2/5) → FAIL (<50%)
    (5, 1, "FAIL"),   # 20% within → FAIL
    (5, 0, "FAIL"),   # 0% within → FAIL
])
def test_summary_pop_forecast_status(total, within_95, expected_status):
    """T17: _summary_pop_forecast returns correct status for various coverage rates."""
    rows = _make_forecast_rows_with_coverage(total, within_95)
    results = _make_minimal_results(rows)
    writer = CalibrationReportWriter(results, "/tmp/dummy.xlsx")
    status, detail = writer._summary_pop_forecast()
    assert status == expected_status, \
        f"total={total}, within_95={within_95}: expected {expected_status}, got {status} (detail: {detail})"


def test_summary_pop_forecast_na_when_empty():
    """T20: _summary_pop_forecast returns N/A when pop_forecast is empty."""
    results = _make_minimal_results(forecast_rows=[])
    writer = CalibrationReportWriter(results, "/tmp/dummy.xlsx")
    status, detail = writer._summary_pop_forecast()
    assert status == "N/A", f"Expected N/A, got {status}"


# ─────────────────────────────────────────────────────────────────────────────
# T18 — run_all_checks() backward compatible (no cbs_path kwarg)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_all_checks_signature_backward_compatible():
    """T18: run_all_checks() accepts call without cbs_path — no TypeError."""
    import inspect
    from run_calibration import run_all_checks
    sig = inspect.signature(run_all_checks)
    params = sig.parameters
    assert "cbs_path" in params, "cbs_path param not found in run_all_checks()"
    assert params["cbs_path"].default is None, "cbs_path should default to None"


# ─────────────────────────────────────────────────────────────────────────────
# T19 — All 8 tabs present after full writer run
# ─────────────────────────────────────────────────────────────────────────────

def test_all_8_tabs_present():
    """T19: All 8 expected tabs present in the workbook after write()."""
    from openpyxl import load_workbook

    results = _make_minimal_results(forecast_rows=[])
    out = "/tmp/test_all_8_tabs.xlsx"
    CalibrationReportWriter(results, out).write()

    wb = load_workbook(out)
    expected = [
        "Summary", "Guide", "Config_Audit", "Pop_Trajectory",
        "Age_Structure", "Migration_Coverage", "Vital_Stats", "Pop_Forecast"
    ]
    for tab in expected:
        assert tab in wb.sheetnames, f"Tab '{tab}' missing from workbook"

    os.unlink(out)
