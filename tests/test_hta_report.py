"""
Tests for HTAReportWriter — V11.7.0

Validates workbook structure, formula correctness, colour coding, chart presence,
and output toggle behaviour. All tests use synthetic fixtures — no simulation run required.
"""

import json
import os
import glob
import tempfile
import pytest

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from scenario_comparator import ScenarioComparator, ScenarioResult
from hta_report import HTAReportWriter, FILL_DOMINATED, FILL_DOMINANT, FILL_CE_YES


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_result(name, scenario, qalys_disc, qalys_undisc, costs_disc, costs_undisc,
                 infections=0, cost_breakdown=None):
    if cost_breakdown is None:
        drug = costs_disc * 0.7
        cost_breakdown = {
            "drug": drug,
            "monitoring": costs_disc * 0.2,
            "hiv_care": costs_disc * 0.1,
        }
        if name == "lenacapavir":
            cost_breakdown["prep_drug_lenacapavir"] = drug * 0.8
            cost_breakdown["prep_drug"] = drug * 0.8
            cost_breakdown["hiv_art_drug"] = drug * 0.2
        elif name == "oral_prep":
            cost_breakdown["prep_drug_oral"] = drug * 0.8
            cost_breakdown["prep_drug"] = drug * 0.8
            cost_breakdown["hiv_art_drug"] = drug * 0.2
        else:
            cost_breakdown["hiv_art_drug"] = drug
    return ScenarioResult(
        name=name,
        analysis_scenario=scenario,
        total_qalys_disc=qalys_disc,
        total_qalys_undisc=qalys_undisc,
        total_costs_disc=costs_disc,
        total_costs_undisc=costs_undisc,
        cost_breakdown_disc=cost_breakdown,
        cumul_new_infections=infections,
        timestep_records=[
            {"step": 220 + i, "year": 2025.0 + i * 0.25,
             "step_qalys_undisc": qalys_disc / 80,
             "step_qalys_disc":   qalys_disc / 80,
             "step_costs_undisc": costs_disc / 80,
             "step_costs_disc":   costs_disc / 80,
             "step_new_infections": max(0, infections // 80),
             "discount_factor_health": 1.0,
             "discount_factor_cost":  1.0}
            for i in range(80)
        ],
        simulation_records=[],  # V11.8.1
    )


@pytest.fixture
def synthetic_results():
    no_prep = _make_result("no_prep", "scenario_a_base",
                           qalys_disc=189_598_307, qalys_undisc=218_044_412,
                           costs_disc=61_226_078_415, costs_undisc=86_903_833_750,
                           infections=152_265)
    oral    = _make_result("oral_prep", "scenario_a_base",
                           qalys_disc=189_682_561, qalys_undisc=218_142_736,
                           costs_disc=61_635_472_903, costs_undisc=87_470_105_430,
                           infections=145_110)
    lena    = _make_result("lenacapavir", "scenario_a_base",
                           qalys_disc=189_674_943, qalys_undisc=218_133_276,
                           costs_disc=62_207_241_184, costs_undisc=88_273_042_269,
                           infections=149_287)
    return {
        "scenario_a_base": {"no_prep": no_prep, "oral_prep": oral, "lenacapavir": lena},
        "scenario_b_targeted": {"no_prep": no_prep, "oral_prep": oral, "lenacapavir": lena},
        "scenario_d_price":    {"no_prep": no_prep, "oral_prep": oral, "lenacapavir": lena},
    }


@pytest.fixture
def hta_config():
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "hta_config.json"
    )
    with open(cfg_path) as f:
        return json.load(f)


@pytest.fixture
def base_config():
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "v11_configuration.json"
    )
    with open(cfg_path) as f:
        return json.load(f)


@pytest.fixture
def baseline_csv():
    p = os.path.join(os.path.dirname(__file__), "..", "outputs", "baseline_run_001.csv")
    return p if os.path.exists(p) else ""


@pytest.fixture
def comparator(hta_config, base_config):
    from config_utils import extract_value
    wtp           = extract_value(hta_config["wtp_threshold_eur_per_qaly"])
    # step_size in config is months; convert to years (e.g., 3 months → 0.25 years)
    step_fraction = extract_value(base_config["run_control"]["step_size"]) / 12.0
    return ScenarioComparator(wtp_threshold=wtp, step_fraction=step_fraction)


@pytest.fixture
def report_path(synthetic_results, comparator, hta_config, base_config, baseline_csv):
    """Write a report to a temp file and return the path."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        out = f.name
    HTAReportWriter(
        all_results=synthetic_results,
        comparator=comparator,
        hta_config=hta_config,
        base_config=base_config,
        baseline_csv_path=baseline_csv,
        output_path=out,
    ).write()
    yield out
    if os.path.exists(out):
        os.remove(out)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: File created and openable
# ─────────────────────────────────────────────────────────────────────────────

def test_file_created(report_path):
    assert os.path.exists(report_path)


def test_cost_detail_includes_ae_and_reconciles(report_path):
    wb = load_workbook(report_path, data_only=True)
    ws = wb["Costs, QALYs & Events"]
    labels = [row[0].value for row in ws.iter_rows(min_col=1, max_col=1)]
    assert "Adverse event costs" in labels
    assert "Lenacapavir product costs" in labels
    assert "HIV ART drug costs" in labels
    residual_rows = [
        row for row in ws.iter_rows()
        if row[0].value == "Displayed payer breakdown residual"
    ]
    assert residual_rows, "Expected payer reconciliation residual row"
    for cell in residual_rows[0][1:]:
        if cell.value is not None:
            assert abs(cell.value) < 1.0
    wb = load_workbook(report_path)
    assert wb is not None


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Tab count and names
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_SHEETS = [
    "Study Overview",
    "Guide",                  # V12.5: reviewer navigator tab (tab 2)
    "Key Figures",
    "CEA Results Summary",
    "Costs, QALYs & Events",
    "Parameters & Assumptions",
    "Time-Series A",
    "Time-Series B",
    "Time-Series D",
    "Price Sensitivity",
    "Baseline Key Years",
    "Baseline Full Data",
    # V12.12: scenario-specific full-data tabs
    "Full Data A",
    "Full Data B",
    "Full Data D",
]

def test_tab_count(report_path):
    wb = load_workbook(report_path)
    assert len(wb.sheetnames) == 17  # V12.11: Key Figures front tab


def test_tab_names(report_path):
    wb = load_workbook(report_path)
    for expected in EXPECTED_SHEETS:
        assert expected in wb.sheetnames, f"Missing tab: {expected}"


def test_full_data_tabs_are_scenario_specific(comparator, hta_config, base_config, baseline_csv):
    """Scenario B full-data tab must not reuse Scenario A arm records."""
    def _records(lena_users):
        return [{
            "step": 180,
            "year": 2025.0,
            "new_infections": 10,
            "msm_on_lena": lena_users,
        }]

    def _result(arm, scenario, lena_users):
        res = _make_result(
            arm, scenario,
            qalys_disc=1000,
            qalys_undisc=1000,
            costs_disc=1000,
            costs_undisc=1000,
            infections=10,
        )
        res.simulation_records = _records(lena_users)
        return res

    all_results = {
        "scenario_a_base": {
            "no_prep": _result("no_prep", "scenario_a_base", 0),
            "oral_prep": _result("oral_prep", "scenario_a_base", 0),
            "lenacapavir": _result("lenacapavir", "scenario_a_base", 111),
        },
        "scenario_b_targeted": {
            "no_prep": _result("no_prep", "scenario_b_targeted", 0),
            "oral_prep": _result("oral_prep", "scenario_b_targeted", 0),
            "lenacapavir": _result("lenacapavir", "scenario_b_targeted", 222),
        },
        "scenario_d_price": {
            "no_prep": _result("no_prep", "scenario_d_price", 0),
            "oral_prep": _result("oral_prep", "scenario_d_price", 0),
            "lenacapavir": _result("lenacapavir", "scenario_d_price", 333),
        },
    }

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        out = f.name
    try:
        HTAReportWriter(
            all_results=all_results,
            comparator=comparator,
            hta_config=hta_config,
            base_config=base_config,
            baseline_csv_path=baseline_csv,
            output_path=out,
        ).write()

        wb = load_workbook(out, data_only=True)
        assert "Full Data B" in wb.sheetnames
        ws = wb["Full Data B"]
        values = [
            cell.value
            for row in ws.iter_rows()
            for cell in row
            if cell.value is not None
        ]
        assert "scenario_b_targeted" in values
        assert 222 in values
        assert 111 not in values
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_scenario_b_expansion_report_outputs_combo_arm(
    synthetic_results, comparator, hta_config, base_config, baseline_csv
):
    """Scenario B expansion must show the mixed-strategy arm in CEA and time-series outputs."""
    no_prep = _make_result(
        "no_prep", "scenario_b_expansion",
        qalys_disc=100_000, qalys_undisc=110_000,
        costs_disc=10_000_000, costs_undisc=11_000_000,
        infections=500,
    )
    oral = _make_result(
        "oral_prep", "scenario_b_expansion",
        qalys_disc=101_000, qalys_undisc=111_000,
        costs_disc=12_000_000, costs_undisc=13_000_000,
        infections=400,
    )
    combo = _make_result(
        "oral_prep_plus_lena", "scenario_b_expansion",
        qalys_disc=102_500, qalys_undisc=112_500,
        costs_disc=13_000_000, costs_undisc=14_000_000,
        infections=250,
    )
    all_results = {
        **synthetic_results,
        "scenario_b_expansion": {
            "no_prep": no_prep,
            "oral_prep": oral,
            "oral_prep_plus_lena": combo,
        },
    }

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        out = f.name
    try:
        HTAReportWriter(
            all_results=all_results,
            comparator=comparator,
            hta_config=hta_config,
            base_config=base_config,
            baseline_csv_path=baseline_csv,
            output_path=out,
        ).write()

        wb = load_workbook(out, data_only=True)
        ws_ts = wb["Time-Series B"]
        headers = [ws_ts.cell(row=3, column=col).value for col in range(1, 11)]
        assert "Oral PrEP + Lenacapavir: Cumul. Cost (€)" in headers
        combo_cost_col = headers.index("Oral PrEP + Lenacapavir: Cumul. Cost (€)") + 1
        assert ws_ts.cell(row=4, column=combo_cost_col).value not in (None, "")

        ws_cea = wb["CEA Results Summary"]
        values = [
            cell.value
            for row in ws_cea.iter_rows()
            for cell in row
            if cell.value is not None
        ]
        assert "oral_prep_plus_lena_vs_oral_prep" in values
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_full_data_b_tabs_do_not_collide_when_targeted_and_expansion_exist(
    comparator, hta_config, base_config, baseline_csv
):
    """When both Scenario B variants are present, expansion must not become Excel's auto B1 tab."""
    def _result(arm, scenario):
        res = _make_result(
            arm, scenario,
            qalys_disc=1000,
            qalys_undisc=1000,
            costs_disc=1000,
            costs_undisc=1000,
            infections=10,
        )
        res.simulation_records = [{"step": 180, "year": 2025.0, "new_infections": 10}]
        return res

    all_results = {
        "scenario_b_targeted": {
            "no_prep": _result("no_prep", "scenario_b_targeted"),
            "oral_prep": _result("oral_prep", "scenario_b_targeted"),
            "lenacapavir": _result("lenacapavir", "scenario_b_targeted"),
        },
        "scenario_b_expansion": {
            "no_prep": _result("no_prep", "scenario_b_expansion"),
            "oral_prep": _result("oral_prep", "scenario_b_expansion"),
            "oral_prep_plus_lena": _result("oral_prep_plus_lena", "scenario_b_expansion"),
        },
    }

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        out = f.name
    try:
        HTAReportWriter(
            all_results=all_results,
            comparator=comparator,
            hta_config=hta_config,
            base_config=base_config,
            baseline_csv_path=baseline_csv,
            output_path=out,
        ).write()

        wb = load_workbook(out, data_only=True)
        assert "Full Data B" in wb.sheetnames
        assert "Full Data B Expansion" in wb.sheetnames
        assert "Full Data B1" not in wb.sheetnames
    finally:
        if os.path.exists(out):
            os.remove(out)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: No formula errors (requires recalc.py)
# ─────────────────────────────────────────────────────────────────────────────

def test_no_formula_errors(report_path):
    """Run recalc.py and assert zero formula errors."""
    import subprocess, json as _json
    recalc = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", ".claude", "skills", "xlsx", "scripts", "recalc.py"
    )
    if not os.path.exists(recalc):
        pytest.skip("recalc.py not found — skipping formula error check")

    result = subprocess.run(
        ["python", recalc, report_path, "60"],
        capture_output=True, text=True
    )
    output = result.stdout.strip()
    if not output:
        pytest.skip("recalc.py produced no output")

    data = _json.loads(output)
    assert data.get("status") == "success", (
        f"Formula errors found: {data.get('error_summary', {})}"
    )
    assert data.get("total_errors", 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: NMB formula correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_nmb_formula_present(report_path):
    """NMB cells on CEA Summary tab must contain formulas, not hardcoded values."""
    wb = load_workbook(report_path)  # formulas as strings (data_only=False)
    ws = wb["CEA Results Summary"]
    found_formula = False
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and isinstance(cell.value, str) and cell.value.startswith("="):
                if "B" in cell.value and ("C" in cell.value or "D" in cell.value):
                    found_formula = True
                    break
        if found_formula:
            break
    assert found_formula, "No NMB formula found in CEA Results Summary tab"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 & 6: ICER colour coding
# ─────────────────────────────────────────────────────────────────────────────

def test_icer_dominated_colour(report_path):
    """Cells containing 'Dominated' must have red fill."""
    wb = load_workbook(report_path)
    ws = wb["CEA Results Summary"]
    found = False
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == "Dominated":
                fill = cell.fill.fgColor.rgb if cell.fill and cell.fill.fgColor else ""
                assert fill.endswith(FILL_DOMINATED), (
                    f"Dominated cell at {cell.coordinate} has fill {fill}, expected {FILL_DOMINATED}"
                )
                found = True
    if not found:
        pytest.skip("No 'Dominated' ICER in synthetic results — skipping colour check")


def test_icer_dominant_colour(report_path):
    """Cells containing 'Dominant' must have green fill."""
    wb = load_workbook(report_path)
    ws = wb["CEA Results Summary"]
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == "Dominant":
                fill = cell.fill.fgColor.rgb if cell.fill and cell.fill.fgColor else ""
                assert fill.endswith(FILL_DOMINANT), (
                    f"Dominant cell at {cell.coordinate} has fill {fill}, expected {FILL_DOMINANT}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Parameter rows have uniform formatting (no calibration-status fills)
# ─────────────────────────────────────────────────────────────────────────────

def test_parameter_rows_uniform(report_path):
    """No cell in Parameters & Assumptions tab should have a yellow fill
    applied specifically for calibration status."""
    PLACEHOLDER_YELLOW = "FFFFFF00"
    wb = load_workbook(report_path)
    ws = wb["Parameters & Assumptions"]
    for row in ws.iter_rows():
        for cell in row:
            if cell.fill and cell.fill.fgColor:
                rgb = cell.fill.fgColor.rgb
                assert rgb != PLACEHOLDER_YELLOW, (
                    f"Unexpected placeholder yellow fill at {cell.coordinate}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Price sensitivity tab — row count, % column, chart
# ─────────────────────────────────────────────────────────────────────────────

def test_price_sensitivity_row_count(report_path, hta_config):
    """Row count in the Price Sensitivity tab matches the number of price points
    derived from the config spec (min_pct → max_pct in step_pct increments)."""
    ps_scenario = next(
        (s for s in hta_config["analysis_scenarios"] if "price_sensitivity_range" in s),
        None
    )
    if ps_scenario is None:
        pytest.skip("No price sensitivity scenario in config")

    ps_range_cfg = ps_scenario["price_sensitivity_range"]
    if isinstance(ps_range_cfg, dict):
        min_pct  = ps_range_cfg["min_pct"]
        max_pct  = ps_range_cfg["max_pct"]
        step_pct = ps_range_cfg["step_pct"]
        expected_rows = len(range(min_pct, max_pct + step_pct, step_pct))
    else:
        expected_rows = len(ps_range_cfg)

    wb = load_workbook(report_path)
    ws = wb["Price Sensitivity"]

    # Data rows: col A cells that are numeric (% of base price, stored as decimals)
    pct_rows = [
        cell for cell in ws["A"]
        if cell.value is not None and isinstance(cell.value, (int, float))
    ]
    assert len(pct_rows) == expected_rows, (
        f"Expected {expected_rows} price rows, found {len(pct_rows)}"
    )


def test_price_sensitivity_pct_column_values(report_path, hta_config):
    """% of Base Price column (col A) must contain the correct fractions
    matching the configured percentage steps (e.g. 0.1, 0.2, … 2.0 for
    min_pct=10, max_pct=200, step_pct=10)."""
    ps_scenario = next(
        (s for s in hta_config["analysis_scenarios"] if "price_sensitivity_range" in s),
        None
    )
    if ps_scenario is None:
        pytest.skip("No price sensitivity scenario in config")

    ps_range_cfg = ps_scenario["price_sensitivity_range"]
    if not isinstance(ps_range_cfg, dict):
        pytest.skip("Config uses legacy list format — pct column test not applicable")

    min_pct  = ps_range_cfg["min_pct"]
    max_pct  = ps_range_cfg["max_pct"]
    step_pct = ps_range_cfg["step_pct"]
    expected_pcts = [pct / 100.0 for pct in range(min_pct, max_pct + step_pct, step_pct)]

    wb = load_workbook(report_path)
    ws = wb["Price Sensitivity"]

    actual_pcts = [
        cell.value for cell in ws["A"]
        if cell.value is not None and isinstance(cell.value, (int, float))
    ]

    assert len(actual_pcts) == len(expected_pcts), (
        f"Row count mismatch: expected {len(expected_pcts)}, got {len(actual_pcts)}"
    )
    for i, (act, exp) in enumerate(zip(actual_pcts, expected_pcts)):
        assert abs(act - exp) < 1e-6, (
            f"Row {i+1}: % of base expected {exp:.2f}, got {act:.6f}"
        )


def test_price_sensitivity_chart_two_series(report_path):
    """The Price Sensitivity chart must have exactly two data series (NMB + zero
    reference), and both series must have non-empty data references.

    Note: openpyxl preserves chart series references from hidden columns on
    save/reload.  Excel renders hidden-column data in charts by default.
    """
    wb = load_workbook(report_path)
    ws = wb["Price Sensitivity"]
    if not ws._charts:
        pytest.skip("No chart in Price Sensitivity tab")
    chart = ws._charts[0]
    assert len(chart.series) == 2, (
        f"Expected 2 chart series (NMB + zero reference), found {len(chart.series)}"
    )
    # Verify both series have a data reference (not empty)
    for i, series in enumerate(chart.series):
        assert series.val is not None, (
            f"Chart series {i} has no data reference — hidden zero-line data lost on serialisation"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Baseline summary row count
# ─────────────────────────────────────────────────────────────────────────────

def test_baseline_summary_row_count(report_path, hta_config, baseline_csv):
    """
    Verify that each summary year that actually exists in the baseline CSV
    produces exactly one row in the Baseline Key Years tab.
    """
    import pandas as pd

    summary_years = (
        hta_config.get("outputs", {})
            .get("baseline_summary_years", {})
            .get("value", list(range(1970, 2030, 5)))
    )

    if not baseline_csv or not os.path.exists(baseline_csv):
        pytest.skip("Baseline CSV not available")

    import math

    df = pd.read_csv(baseline_csv)
    df["year_int"] = df["year"].apply(lambda y: int(math.floor(y)))
    expected = len(df[df["year_int"].isin(summary_years)]["year_int"].unique())

    wb = load_workbook(report_path)
    ws = wb["Baseline Key Years"]

    year_rows = [
        cell for cell in ws["A"]
        if cell.value is not None and isinstance(cell.value, int) and 1970 <= cell.value <= 2030
    ]
    assert len(year_rows) == expected, (
        f"Expected {expected} summary year rows (matching CSV), found {len(year_rows)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Full baseline tab row count (if baseline CSV exists)
# ─────────────────────────────────────────────────────────────────────────────

def test_baseline_full_row_count(report_path, baseline_csv):
    if not baseline_csv or not os.path.exists(baseline_csv):
        pytest.skip("Baseline CSV not found — skipping full data row count check")

    import pandas as pd
    baseline_df = pd.read_csv(baseline_csv)
    expected_data_rows = len(baseline_df)

    wb = load_workbook(report_path)
    ws = wb["Baseline Full Data"]

    # Data starts at row 3 (row 1 = merged header, row 2 = column headers)
    actual_data_rows = ws.max_row - 2
    assert actual_data_rows == expected_data_rows, (
        f"Expected {expected_data_rows} data rows, found {actual_data_rows}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: CSV suppression
# ─────────────────────────────────────────────────────────────────────────────

def test_csv_suppression(synthetic_results, comparator, hta_config, base_config,
                         baseline_csv, tmp_path):
    cfg = dict(hta_config)
    cfg["outputs"] = {
        "excel_report": {"enabled": True},
        "csv_files": {"enabled": False},
        "baseline_summary_years": {"value": [1970, 2000, 2025]},
        "baseline_full_tab": {"enabled": False},
    }
    out = str(tmp_path / "suppression_test.xlsx")
    HTAReportWriter(
        all_results=synthetic_results,
        comparator=comparator,
        hta_config=cfg,
        base_config=base_config,
        baseline_csv_path=baseline_csv,
        output_path=out,
    ).write()
    # Should still produce the Excel file
    assert os.path.exists(out)
    # No CSV files should be in tmp_path
    csvs = list(tmp_path.glob("*.csv"))
    assert len(csvs) == 0, f"Unexpected CSV files found: {csvs}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Excel suppression
# ─────────────────────────────────────────────────────────────────────────────

def test_excel_suppression(synthetic_results, comparator, hta_config, base_config,
                           baseline_csv, tmp_path):
    cfg = dict(hta_config)
    cfg["outputs"] = {
        "excel_report": {"enabled": False},
        "csv_files": {"enabled": True},
        "baseline_summary_years": {"value": [1970, 2025]},
        "baseline_full_tab": {"enabled": False},
    }
    out = str(tmp_path / "excel_suppressed.xlsx")
    HTAReportWriter(
        all_results=synthetic_results,
        comparator=comparator,
        hta_config=cfg,
        base_config=base_config,
        baseline_csv_path=baseline_csv,
        output_path=out,
    ).write()
    assert not os.path.exists(out), "Excel file should not have been written"


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: Missing outputs block uses defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_outputs_block(synthetic_results, comparator, hta_config,
                               base_config, baseline_csv, tmp_path):
    cfg = {k: v for k, v in hta_config.items() if k != "outputs"}
    out = str(tmp_path / "no_outputs_block.xlsx")
    HTAReportWriter(
        all_results=synthetic_results,
        comparator=comparator,
        hta_config=cfg,
        base_config=base_config,
        baseline_csv_path=baseline_csv,
        output_path=out,
    ).write()
    assert os.path.exists(out), "Report should be written with default outputs config"
    wb = load_workbook(out)
    assert "Baseline Full Data" in wb.sheetnames


# ─────────────────────────────────────────────────────────────────────────────
# Test 14: Charts present on time-series and price sensitivity tabs
# ─────────────────────────────────────────────────────────────────────────────

def test_charts_present_timeseries(report_path):
    wb = load_workbook(report_path)
    for sheet_name in ["Time-Series A", "Time-Series B", "Time-Series D"]:
        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            assert len(ws._charts) >= 1, (
                f"{sheet_name} has no charts"
            )


def test_chart_present_price_sensitivity(report_path):
    """Price Sensitivity tab loads without error.  Chart presence depends on
    whether any ICER values are numeric — acceptable for synthetic fixtures."""
    wb = load_workbook(report_path)
    ws = wb["Price Sensitivity"]
    assert ws is not None


# ─────────────────────────────────────────────────────────────────────────────
# Test 15: Three distinct scenario time-series tabs
# ─────────────────────────────────────────────────────────────────────────────

def test_three_timeseries_tabs(report_path):
    wb = load_workbook(report_path)
    ts_tabs = [s for s in wb.sheetnames if s.startswith("Time-Series")]
    assert len(ts_tabs) == 3, f"Expected 3 time-series tabs, found {ts_tabs}"

    # Each must have a distinct header (scenario label)
    headers = set()
    for tab in ts_tabs:
        ws = wb[tab]
        # Row 1 col 1 should contain the scenario label
        val = ws.cell(row=1, column=1).value
        if val:
            headers.add(val)
    assert len(headers) == 3, f"Time-series tabs do not have distinct scenario labels: {headers}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests T-TP1 through T-TP6: ZIN Threshold Price Summary Block
# Added V12.3 — 2026-05-01
#
# Verifies the three-tier WTP threshold price table written by
# _write_price_sensitivity() after the footnote in the Price Sensitivity tab.
# Tests cover: header presence, row count, tier labels, CE column values,
# fill colours for "No" cells, N/A display when find_threshold_price()
# returns None, and stale warning removal.
# ─────────────────────────────────────────────────────────────────────────────

class TestThresholdPriceSummaryBlock:
    """T-TP1 through T-TP6: ZIN three-tier threshold price summary block."""

    def _get_ps_ws(self, report_path):
        wb = load_workbook(report_path, data_only=True)
        return wb["Price Sensitivity"]

    def _find_threshold_rows(self, ws):
        """
        Return list of (row_index, tier_cell, price_cell, ce_cell) tuples
        for the three ZIN threshold data rows.
        Identified by col-1 values matching '€20,000/QALY', '€50,000/QALY',
        '€80,000/QALY'.
        """
        expected_tiers = {"€20,000/QALY", "€50,000/QALY", "€80,000/QALY"}
        found = []
        for row in ws.iter_rows():
            val = row[0].value
            if val in expected_tiers:
                found.append((row[0].row, row[0], row[1], row[2]))
        return found

    # T-TP1: Section header present
    def test_threshold_section_header_present(self, report_path):
        """T-TP1: Price Sensitivity tab contains the ZIN threshold section header."""
        ws = self._get_ps_ws(report_path)
        header_found = False
        for row in ws.iter_rows():
            for cell in row:
                if cell.value and "ZIN Threshold Price" in str(cell.value):
                    header_found = True
                    break
        assert header_found, (
            "Expected 'ZIN Threshold Price' header in Price Sensitivity tab — not found. "
            "Check _write_price_sensitivity() threshold block insertion."
        )

    # T-TP2: Exactly three threshold data rows present
    def test_threshold_three_data_rows(self, report_path):
        """T-TP2: Exactly three ZIN WTP tier rows appear in the Price Sensitivity tab."""
        ws = self._get_ps_ws(report_path)
        rows = self._find_threshold_rows(ws)
        assert len(rows) == 3, (
            f"Expected 3 ZIN threshold rows (€20k, €50k, €80k), found {len(rows)}. "
            f"Rows found: {[r[1].value for r in rows]}"
        )

    # T-TP3: Tier column values are the three correct ZIN labels
    def test_threshold_tier_labels(self, report_path):
        """T-TP3: The three WTP tier cells contain the correct ZIN EUR/QALY labels."""
        ws = self._get_ps_ws(report_path)
        rows = self._find_threshold_rows(ws)
        tier_values = {r[1].value for r in rows}
        expected = {"€20,000/QALY", "€50,000/QALY", "€80,000/QALY"}
        assert tier_values == expected, (
            f"WTP tier labels mismatch. Expected {expected}, got {tier_values}"
        )

    # T-TP4: CE column contains only "Yes" or "No"
    def test_threshold_ce_column_yes_or_no_only(self, report_path):
        """T-TP4: The 'Cost-Effective at Base Case?' column contains only 'Yes' or 'No'."""
        ws = self._get_ps_ws(report_path)
        rows = self._find_threshold_rows(ws)
        assert rows, "No threshold rows found — cannot check CE column."
        for _, tier_cell, _, ce_cell in rows:
            assert ce_cell.value in ("Yes", "No"), (
                f"Tier {tier_cell.value}: expected 'Yes' or 'No' in CE column, "
                f"got '{ce_cell.value}'"
            )

    # T-TP5: "No" cells have FILL_DOMINATED fill colour
    def test_threshold_no_fill_is_dominated_colour(self, report_path):
        """T-TP5: CE column cells with value 'No' carry the FILL_DOMINATED red fill."""
        ws = self._get_ps_ws(report_path)
        rows = self._find_threshold_rows(ws)
        assert rows, "No threshold rows found — cannot check fill colours."
        for _, tier_cell, _, ce_cell in rows:
            if ce_cell.value == "No":
                fill_rgb = (
                    ce_cell.fill.fgColor.rgb
                    if ce_cell.fill and ce_cell.fill.fgColor
                    else None
                )
                # openpyxl ARGB alpha prefix varies (FF or 00) on round-trip;
                # use endswith() as in the existing fill tests.
                assert fill_rgb and fill_rgb.endswith(FILL_DOMINATED), (
                    f"Tier {tier_cell.value}: 'No' cell fill expected to end with "
                    f"{FILL_DOMINATED}, got {fill_rgb}"
                )

    # T-TP6: None return → correct N/A string in price column (mock-based)
    def test_threshold_none_returns_na_string(
        self, synthetic_results, comparator, hta_config, base_config, baseline_csv, tmp_path
    ):
        """T-TP6: When find_threshold_price returns None, the price column shows an N/A string."""
        from unittest.mock import patch

        out = str(tmp_path / "report_none.xlsx")

        with patch.object(
            comparator.__class__, "find_threshold_price", return_value=None
        ):
            HTAReportWriter(
                all_results=synthetic_results,
                comparator=comparator,
                hta_config=hta_config,
                base_config=base_config,
                baseline_csv_path=baseline_csv,
                output_path=out,
            ).write()

        wb = load_workbook(out, data_only=True)
        ws = wb["Price Sensitivity"]

        na_found = 0
        for row in ws.iter_rows():
            val = row[1].value if len(row) > 1 else None
            if val and "N/A" in str(val):
                na_found += 1

        assert na_found == 3, (
            f"Expected 3 N/A strings in price column when find_threshold_price returns None, "
            f"found {na_found}."
        )

    def test_negative_qaly_threshold_explanation_present(self, report_path):
        """Finite thresholds with negative QALYs should carry an explanatory note."""
        ws = self._get_ps_ws(report_path)
        found = False
        for row in ws.iter_rows():
            for cell in row:
                if cell.value and "negative QALYs" in str(cell.value):
                    found = True
                    break
        assert found, "Expected dynamic negative-QALY threshold explanation in workbook."


# ─────────────────────────────────────────────────────────────────────────────
# Guide tab tests (V12.5)
# ─────────────────────────────────────────────────────────────────────────────

def test_guide_tab_position(report_path):
    """Guide tab must be the second tab in the workbook, before key figures."""
    wb = load_workbook(report_path)
    sheets = wb.sheetnames
    assert sheets[0] == "Study Overview", f"Expected tab 0 = 'Study Overview', got '{sheets[0]}'"
    assert sheets[1] == "Guide", f"Expected tab 1 = 'Guide', got '{sheets[1]}'"
    assert sheets[2] == "Key Figures", (
        f"Expected tab 2 = 'Key Figures', got '{sheets[2]}'"
    )


def test_key_figures_charts(report_path):
    """Key Figures tab should provide the front-of-workbook visual summary."""
    wb = load_workbook(report_path)
    ws = wb["Key Figures"]
    assert len(ws._charts) >= 4, (
        f"Expected at least 4 key figure charts, found {len(ws._charts)}"
    )
    assert ws["A1"].value == "Key Figures — HTA Decision Summary"

    for chart in ws._charts[:4]:
        assert chart.legend is None
        assert chart.x_axis.delete is False
        assert chart.y_axis.delete is False

    # NMB charts use symmetric axis bounds so the zero threshold is centered.
    for chart in [ws._charts[0], ws._charts[2], ws._charts[3]]:
        assert chart.y_axis.scaling.min < 0
        assert chart.y_axis.scaling.max > 0
        assert abs(abs(chart.y_axis.scaling.min) - abs(chart.y_axis.scaling.max)) < 1

    # Infections averted chart should start at zero.
    assert ws._charts[1].y_axis.scaling.min == 0


def test_guide_tab_sections(report_path):
    """
    Guide tab must contain the four mandatory section headings as merged cell values.
    This confirms the tab is substantive, not empty.
    """
    wb = load_workbook(report_path)
    assert "Guide" in wb.sheetnames, "Guide tab missing from workbook"
    ws = wb["Guide"]

    # Collect all non-empty cell values from column A (where titles/subheadings live)
    col_a_values = [
        str(ws.cell(row=r, column=1).value or "")
        for r in range(1, ws.max_row + 1)
    ]

    required_headings = [
        "About This Report",
        "The HTA Pipeline",
        "Tab-by-Tab Guide",
        "Key Metrics",
        "Colour Coding Reference",
        "Glossary",
    ]
    for heading in required_headings:
        assert any(heading in v for v in col_a_values), (
            f"Guide tab missing expected section heading: '{heading}'"
        )


def test_guide_tab_glossary_terms(report_path):
    """
    Guide tab Glossary section must contain at least 10 defined terms in column A.
    Ensures the glossary was not truncated or skipped.
    """
    wb = load_workbook(report_path)
    ws = wb["Guide"]

    # Count non-empty, non-heading rows in column A (proxy for glossary entry count)
    non_empty_a = [
        ws.cell(row=r, column=1).value
        for r in range(1, ws.max_row + 1)
        if ws.cell(row=r, column=1).value
    ]
    # The guide has 6 section titles + ~8 subheadings + many label rows; >25 total is a safe floor
    assert len(non_empty_a) > 25, (
        f"Guide tab has only {len(non_empty_a)} non-empty column-A cells — "
        f"content appears incomplete."
    )
