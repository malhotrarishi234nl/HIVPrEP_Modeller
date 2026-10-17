"""
HTA Excel Report Writer — V11.7.0

Produces a structured 10-tab Excel workbook from HTA pipeline outputs.
Replaces multiple CSV files with a single shareable report.

Tabs:
  1.  Study Overview
  2.  CEA Results Summary
  3.  Costs, QALYs & Events Detail
  4.  Parameters & Assumptions
  5.  Time-Series: Scenario A
  6.  Time-Series: Scenario B
  7.  Time-Series: Scenario D
  8.  Price Sensitivity Analysis
  9.  Simulation Baseline: Key Years
  10. Simulation Baseline: Full Data  (togglable via hta_config outputs block)

Usage:
    writer = HTAReportWriter(
        all_results=all_results,
        comparator=comparator,
        hta_config=hta_config,
        base_config=base_config,
        baseline_csv_path="outputs/baseline_run_001.csv",
        output_path="outputs/hta_results/hta_report_20260414.xlsx"
    )
    writer.write()
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import LineChart, BarChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.drawing.line import LineProperties
from openpyxl.worksheet.table import Table, TableStyleInfo

from scenario_comparator import ScenarioComparator, ScenarioResult
from config_utils import extract_value
from hta_reliability import baseline_year_coverage, calibration_handoff_rows


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette
# ─────────────────────────────────────────────────────────────────────────────
NAVY       = "1F3864"
WHITE      = "FFFFFF"
LIGHT_GREY = "F2F2F2"
MID_GREY   = "D9D9D9"
BLUE_TEXT  = "0000FF"
GREY_TEXT  = "808080"

# ICER decision fills (light variants — readable with black text)
FILL_DOMINANT   = "C6EFCE"   # green  — cheaper and better
FILL_DOMINATED  = "FFC7CE"   # red    — more expensive and worse
FILL_CE_YES     = "E2EFDA"   # pale green — ICER <= WTP
FILL_CE_NO      = "FFEB9C"   # amber  — ICER > WTP
FILL_LENA_PRIMARY  = "C6EFCE"   # stronger green — primary comparison (lena vs oral)
FILL_LENA_SECONDARY = "E2EFDA"  # pale green — other lena comparison rows

# ZIN three-tier WTP thresholds (EUR/QALY) — fixed Dutch policy values.
# Source: Zorginstituut Nederland (2022). Richtlijn voor economische evaluaties
# in de gezondheidszorg. Tiers: low proportional shortfall (€20k),
# medium (€50k), high ≥0.70 (€80k). HIV/AIDS qualifies for tier 3.
_ZIN_WTP_TIERS = [20_000, 50_000, 80_000]

# Arm display names and series colours
ARM_LABELS = {
    "no_prep":             "No PrEP",
    "oral_prep":           "Oral PrEP",
    "oral_prep_expansion": "Oral PrEP Expansion",
    "lenacapavir":         "Lenacapavir",
    "oral_prep_plus_lena": "Oral PrEP + Lenacapavir",  # V12.10: expansion arm
}
SCENARIO_LABELS = {
    "scenario_a_base":      "Scenario A — Universal Access (All MSM)",
    "scenario_b_targeted":  "Scenario B — Targeted: High-Activity MSM",
    "scenario_b_expansion": "Scenario B — Expansion: Adding Lenacapavir to PrEP Programme",  # V12.10
    "scenario_d_price":     "Scenario D — Price Sensitivity Analysis",
}

SCENARIO_TAB_SUFFIXES = {
    "scenario_a_base":      "A",
    "scenario_b_targeted":  "B",
    "scenario_b_expansion": "B",  # V12.10: replaces targeted in tab naming
    "scenario_d_price":     "D",
}


# ─────────────────────────────────────────────────────────────────────────────
# Style helpers
# ─────────────────────────────────────────────────────────────────────────────

def _font(bold=False, size=11, color="000000", italic=False):
    return Font(name="Arial", bold=bold, size=size, color=color, italic=italic)


def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def _border_thin():
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)


def _border_bottom():
    s = Side(style="thin", color="BFBFBF")
    return Border(bottom=s)


def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)


def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


def _right():
    return Alignment(horizontal="right", vertical="center")


def _style_section_header(cell, text):
    """Dark navy background, white bold text — major section title."""
    cell.value = text
    cell.font = _font(bold=True, size=12, color=WHITE)
    cell.fill = _fill(NAVY)
    cell.alignment = _left()


def _style_col_header(cell, text):
    """Mid-grey background, black bold — column header row."""
    cell.value = text
    cell.font = _font(bold=True, size=10)
    cell.fill = _fill(MID_GREY)
    cell.alignment = _center()
    cell.border = _border_thin()


def _style_label(cell, text):
    """Row label — plain black, left-aligned."""
    cell.value = text
    cell.font = _font(size=10)
    cell.alignment = _left()
    cell.border = _border_thin()


def _style_value(cell, value, fmt=None, blue=False):
    """Data cell — optionally blue for hardcoded inputs, black for formulas."""
    cell.value = value
    cell.font = _font(size=10, color=BLUE_TEXT if blue else "000000")
    cell.alignment = _right()
    cell.border = _border_thin()
    if fmt:
        cell.number_format = fmt


def _style_source(cell, text):
    """Source citation — grey italic, smaller font."""
    cell.value = text
    cell.font = _font(size=9, color=GREY_TEXT, italic=True)
    cell.alignment = _left()


def _style_icer_cell(cell, icer_value, wtp: float, nmb: float = None):
    """Apply colour fill to ICER cell based on cost-effectiveness outcome.

    For numeric ICERs the fill colour is determined by the sign of NMB rather
    than by comparing the ICER directly to the WTP threshold.  The two
    approaches are equivalent in the NE quadrant (higher cost, higher QALYs)
    but diverge in the SW quadrant (cheaper, fewer QALYs): there the ICER is
    still positive but cost-effectiveness requires ICER >= WTP, which is the
    same as NMB > 0.  Using NMB is therefore correct in all four quadrants.

    Args:
        cell:       openpyxl Cell object to style.
        icer_value: Numeric ICER (EUR/QALY) or string label ("Dominant",
                    "Dominated", "No QALY difference").
        wtp:        WTP threshold — kept as a parameter for the fallback path
                    (used only when nmb is not supplied).
        nmb:        Net monetary benefit (WTP × ΔQ − ΔC).  When provided,
                    this governs the green/red fill decision for numeric ICERs.
                    If None, falls back to the legacy icer_value <= wtp rule.
    """
    cell.border = _border_thin()
    cell.alignment = _right()
    if isinstance(icer_value, str):
        cell.value = icer_value
        cell.font = _font(size=10, bold=True)
        if icer_value == "Dominant":
            cell.fill = _fill(FILL_DOMINANT)
        elif icer_value == "Dominated":
            cell.fill = _fill(FILL_DOMINATED)
        else:
            cell.fill = _fill(LIGHT_GREY)
    else:
        cell.value = icer_value
        cell.number_format = u'\u20ac#,##0'
        cell.font = _font(size=10)
        # Prefer NMB-based decision; fall back to ICER <= WTP if nmb absent.
        if nmb is not None:
            cell.fill = _fill(FILL_CE_YES if nmb > 0 else FILL_CE_NO)
        else:
            cell.fill = _fill(FILL_CE_YES if icer_value <= wtp else FILL_CE_NO)


def _set_col_widths(ws, widths: Dict[int, float]):
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width




# ─────────────────────────────────────────────────────────────────────────────
# Chart styling helpers
# ─────────────────────────────────────────────────────────────────────────────

# Arm colours — consistent across all charts in the report.
# Chosen from the standard Excel 2016 palette for recognisability.
_ARM_COLORS = {
    "no_prep":             "4472C4",   # Excel blue
    "oral_prep":           "ED7D31",   # Excel orange
    "oral_prep_expansion": "FFC000",   # Excel gold
    "lenacapavir":         "70AD47",   # Excel green
    "oral_prep_plus_lena": "A9D18E",   # lighter green — V12.10: combo arm
}
_WTP_COLOR   = "C00000"        # dark red — WTP reference lines
_ARM_ORDER   = [
    "no_prep",
    "oral_prep",
    "oral_prep_expansion",
    "lenacapavir",
    "oral_prep_plus_lena",
]  # V13.3: include Scenario C oral expansion benchmark


def _add_styled_series(chart, ref, name: str, color: str,
                       width_emu: int = 25000, dash: str = "solid") -> None:
    """Add a series to *chart* with an explicit title and line style.

    Using SeriesLabel directly (instead of titles_from_data=True) avoids the
    openpyxl/Excel bug where set_categories() causes category labels to appear
    as legend entries instead of series names.

    Args:
        chart:     openpyxl chart object (LineChart or BarChart).
        ref:       Reference pointing to the data column (no header row).
        name:      Series display name shown in the legend.
        color:     6-char hex RGB string (e.g. "4472C4").
        width_emu: Line width in EMUs (25 400 EMU = 2 pt).
        dash:      prstDash value: "solid", "lgDash", "dash", "dot", etc.
    """
    chart.add_data(ref)
    s = chart.series[-1]
    sl = SeriesLabel()
    sl.v = name
    s.tx = sl
    s.graphicalProperties.line.solidFill = color
    s.graphicalProperties.line.width     = width_emu
    s.graphicalProperties.line.prstDash  = dash

def _merge_header(ws, row, col_start, col_end, text, navy=True):
    ws.merge_cells(
        start_row=row, start_column=col_start,
        end_row=row, end_column=col_end
    )
    cell = ws.cell(row=row, column=col_start)
    if navy:
        _style_section_header(cell, text)
    else:
        cell.value = text
        cell.font = _font(bold=True, size=11)
        cell.fill = _fill(LIGHT_GREY)
        cell.alignment = _left()
    return cell


def _blank_row(ws, row, n_cols):
    for c in range(1, n_cols + 1):
        ws.cell(row=row, column=c).value = None


def _safe_table_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"T_{cleaned}"
    return cleaned[:240]


def _add_excel_table(ws, name: str, start_row: int, start_col: int,
                     end_row: int, end_col: int) -> None:
    """Add a filterable Excel table when there is at least one data row."""
    if end_row <= start_row or end_col < start_col:
        return
    ref = (
        f"{get_column_letter(start_col)}{start_row}:"
        f"{get_column_letter(end_col)}{end_row}"
    )
    tab = Table(displayName=_safe_table_name(name), ref=ref)
    tab.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(tab)


def _write_table(ws, start_row: int, headers: List[str], rows: List[List],
                 table_name: str, widths: Optional[Dict[int, float]] = None,
                 formats: Optional[Dict[int, str]] = None) -> int:
    """Write a compact filterable table and return the next blank row."""
    for ci, header in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=ci)
        _style_col_header(cell, header)
    for ri, row_vals in enumerate(rows, start_row + 1):
        for ci, val in enumerate(row_vals, 1):
            cell = ws.cell(row=ri, column=ci)
            cell.value = val
            cell.font = _font(size=10)
            cell.border = _border_thin()
            cell.alignment = _right() if isinstance(val, (int, float)) else _left()
            if formats and ci in formats:
                cell.number_format = formats[ci]
    _add_excel_table(
        ws, table_name, start_row, 1,
        start_row + len(rows), len(headers)
    )
    if widths:
        _set_col_widths(ws, widths)
    return start_row + len(rows) + 2


# ─────────────────────────────────────────────────────────────────────────────
# HTAReportWriter
# ─────────────────────────────────────────────────────────────────────────────

class HTAReportWriter:
    """
    Writes a 10-tab HTA Excel report from pipeline outputs.

    Args:
        all_results:       Dict[scenario_name -> Dict[arm_name -> ScenarioResult]]
        comparator:        ScenarioComparator instance
        hta_config:        Parsed hta_config.json dict
        base_config:       Parsed v11_configuration.json dict
        baseline_csv_path: Path to baseline_run_001.csv (pre-freeze simulation)
        output_path:       Target .xlsx file path
    """

    ARM_ORDER = [
        "no_prep",
        "oral_prep",
        "oral_prep_expansion",
        "lenacapavir",
        "oral_prep_plus_lena",
    ]  # V13.3: include Scenario C oral expansion benchmark
    PAYER_RECONCILIATION_TOLERANCE = 1.0

    def __init__(
        self,
        all_results: Dict[str, Dict[str, ScenarioResult]],
        comparator: ScenarioComparator,
        hta_config: dict,
        base_config: dict,
        baseline_csv_path: str,
        output_path: str,
    ):
        self.all_results    = all_results
        self.comparator     = comparator
        self.hta_config     = hta_config
        self.base_config    = base_config
        self.baseline_csv   = baseline_csv_path
        self.output_path    = output_path

        # Resolve output toggles with defaults
        outputs_cfg = hta_config.get("outputs", {})
        self.include_full_baseline = outputs_cfg.get(
            "baseline_full_tab", {}
        ).get("enabled", True)
        self.include_scenario_full_data = outputs_cfg.get(
            "scenario_full_data_tabs", {}
        ).get("enabled", self.include_full_baseline)
        self.include_price_sensitivity = outputs_cfg.get(
            "price_sensitivity_tab", {}
        ).get("enabled", True)
        self.include_transparency_tabs = outputs_cfg.get(
            "transparency_tabs", {}
        ).get("enabled", True)
        self.baseline_summary_years = outputs_cfg.get(
            "baseline_summary_years", {}
        ).get("value", list(range(1970, 2030, 5)))

        self.wtp = extract_value(hta_config["wtp_threshold_eur_per_qaly"])  # V11.7.1: required key; no silent fallback
        self.freeze_year   = hta_config["time_horizon"]["freeze_year"]
        self.eval_years    = hta_config["time_horizon"]["evaluation_years"]

        # BACKLOG-20: perspective flag and payer-breakdown-separately flag.
        # Defaults to "payer" / False so reports built without the block behave
        # identically to V11.9.10.
        _persp_cfg = hta_config.get("perspective", {})
        self._perspective          = _persp_cfg.get("primary", "payer")
        self._report_payer_sep     = _persp_cfg.get("report_payer_component_separately", False)

        # Ordered scenario names present in results
        self.scenario_names = list(all_results.keys())

        self.wb = Workbook()
        # Remove default sheet
        if "Sheet" in self.wb.sheetnames:
            del self.wb["Sheet"]

    @staticmethod
    def _payer_breakdown_components(res: ScenarioResult) -> Dict[str, float]:
        """
        Return non-overlapping payer cost components for report reconciliation.

        The broad ``drug`` category is retained as a memo row, but this component
        set splits it into PrEP product costs, HIV ART drug costs, and any
        remaining/unallocated drug costs to avoid double-counting.
        """
        bd = res.cost_breakdown_disc
        prep_oral = bd.get("prep_drug_oral", 0.0)
        prep_lena = bd.get("prep_drug_lenacapavir", 0.0)
        hiv_art = bd.get("hiv_art_drug", 0.0)
        other_drug = bd.get("drug", 0.0) - prep_oral - prep_lena - hiv_art
        if abs(other_drug) < 1e-6:
            other_drug = 0.0
        return {
            "prep_drug_oral": prep_oral,
            "prep_drug_lenacapavir": prep_lena,
            "hiv_art_drug": hiv_art,
            "other_drug": other_drug,
            "monitoring": bd.get("monitoring", 0.0),
            "hiv_care": bd.get("hiv_care", 0.0),
            "adverse_events": bd.get("adverse_events", 0.0),
            "terminal_care": bd.get("terminal_care", 0.0),
        }

    @classmethod
    def _payer_breakdown_sum(cls, res: ScenarioResult) -> float:
        return sum(cls._payer_breakdown_components(res).values())

    @classmethod
    def _payer_breakdown_residual(cls, res: ScenarioResult) -> float:
        return res.total_costs_disc - cls._payer_breakdown_sum(res)

    def _validate_payer_reconciliation(self) -> None:
        for scenario_name, scenario_results in self.all_results.items():
            for arm_name, res in scenario_results.items():
                residual = self._payer_breakdown_residual(res)
                if abs(residual) > self.PAYER_RECONCILIATION_TOLERANCE:
                    raise ValueError(
                        "HTA payer cost breakdown does not reconcile for "
                        f"{scenario_name}/{arm_name}: residual €{residual:,.2f}. "
                        "Expose the missing payer cost category before writing "
                        "a production workbook."
                    )

    @staticmethod
    def _is_cost_saving_tradeoff(comp: dict) -> bool:
        """Return True for the SW CEA quadrant: cheaper but less effective."""
        delta_cost = comp.get("delta_cost")
        delta_qaly = comp.get("delta_qaly")
        return (
            delta_cost is not None
            and delta_qaly is not None
            and delta_cost < 0
            and delta_qaly < 0
        )

    def _ce_status_from_comparison(self, comp: dict) -> str:
        """Human-readable CE status using NMB for the decision rule."""
        nmb = comp.get("nmb")
        dominance = comp.get("dominance")
        if dominance == "dominant":
            return "Dominant"
        if dominance == "dominated":
            return "Dominated"
        if self._is_cost_saving_tradeoff(comp):
            if nmb is not None and nmb > 0:
                return "Cost-effective; cost-saving trade-off"
            return "Cost-saving trade-off; not CE"
        if nmb is not None and nmb > 0:
            return "Cost-effective"
        return "Not cost-effective"

    @staticmethod
    def _is_ce_status(status: str) -> bool:
        return status in {"Dominant", "Cost-effective"} or (
            isinstance(status, str) and status.startswith("Cost-effective;")
        )

    def _split_reviewer_reading(self, row: dict) -> str:
        if self._is_ce_status(row["ce_status"]):
            return "Favorable at current price"
        comp = {
            "delta_cost": row.get("delta_cost"),
            "delta_qaly": row.get("delta_qaly"),
        }
        if self._is_cost_saving_tradeoff(comp):
            return "Costs fall, but QALY loss exceeds WTP tolerance"
        return "Requires lower price or stronger effect"

    def _threshold_status(
        self,
        scenario_results: Dict[str, ScenarioResult],
        wtp_tier: float,
        base_annual_price: float,
    ) -> Dict[str, object]:
        lena_res = scenario_results.get("lenacapavir")
        oral_res = scenario_results.get("oral_prep")
        if lena_res is None or oral_res is None:
            return {
                "threshold": None,
                "is_ce_at_base": False,
                "note": "Requires Lenacapavir and Oral PrEP results",
                "delta_qaly": None,
            }

        delta_qaly = lena_res.total_qalys_disc - oral_res.total_qalys_disc
        try:
            threshold = self.comparator.find_threshold_price(
                scenario_results,
                wtp=wtp_tier,
                base_annual_price=base_annual_price,
            )
        except (ValueError, KeyError) as exc:
            return {
                "threshold": None,
                "is_ce_at_base": False,
                "note": f"Cannot compute threshold: {exc}",
                "delta_qaly": delta_qaly,
            }

        try:
            lena_product = self.comparator._get_lena_price_sensitive_cost(lena_res)
        except ValueError:
            lena_product = None

        if self.comparator.perspective == "societal":
            lena_base = lena_res.total_costs_disc_societal
            oral_base = oral_res.total_costs_disc_societal
        else:
            lena_base = lena_res.total_costs_disc
            oral_base = oral_res.total_costs_disc

        if threshold is not None:
            note = ""
            if delta_qaly < 0:
                note = (
                    "Finite threshold despite negative QALYs: cost savings at "
                    "low prices can offset the QALY loss under NMB."
                )
            return {
                "threshold": threshold,
                "is_ce_at_base": base_annual_price <= threshold,
                "note": note,
                "delta_qaly": delta_qaly,
            }

        if lena_product is not None:
            delta_cost_at_zero = (lena_base - lena_product) - oral_base
            nmb_at_zero = wtp_tier * delta_qaly - delta_cost_at_zero
            if nmb_at_zero > 0:
                return {
                    "threshold": None,
                    "is_ce_at_base": True,
                    "note": "NMB remains positive above the 10x search range.",
                    "delta_qaly": delta_qaly,
                }

        return {
            "threshold": None,
            "is_ce_at_base": False,
            "note": "Not cost-effective even if Lenacapavir product cost is zero.",
            "delta_qaly": delta_qaly,
        }

    @staticmethod
    def _discounted_person_years(res: ScenarioResult) -> float:
        total = 0.0
        for rec in res.timestep_records:
            step_fraction = rec.get("step_fraction", 0.25)
            discount = rec.get("discount_factor_health", 1.0)
            total += rec.get("total_population", 0.0) * step_fraction * discount
        return total

    @staticmethod
    def _sum_sim_metric(res: ScenarioResult, key: str) -> float:
        return sum(float(rec.get(key, 0.0) or 0.0) for rec in res.simulation_records)

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def write(self):
        """Build and save the workbook. No-op if excel_report.enabled is False."""
        outputs_cfg = self.hta_config.get("outputs", {})
        if not outputs_cfg.get("excel_report", {}).get("enabled", True):
            return

        self._validate_payer_reconciliation()
        if self.hta_config.get("_runtime_config"):
            self._write_split_runtime_report()
            self.wb.save(self.output_path)
            print(f"\n  ✓ HTA Excel report: {self.output_path}")
            return

        self._write_overview()
        self._write_guide()          # Tab 2 — Navigator / guide for reviewers
        self._write_key_figures()
        self._write_cea_summary()
        self._write_detail()
        self._write_parameters()

        # V12.10: scenario_b_expansion preferred over scenario_b_targeted for "Time-Series B"
        # slot. Both entries share the same tab_name; the guard below ensures only the first
        # present one is written, preventing a duplicate-sheet error.
        _ts_b_written = False
        ts_scenarios = [
            ("scenario_a_base",     "Time-Series A", "Scenario A — Universal Access"),
            ("scenario_b_expansion","Time-Series B",
             "Scenario B — Expansion: Adding Lenacapavir to PrEP Programme"),
            ("scenario_b_targeted", "Time-Series B", "Scenario B — Targeted: High-Activity MSM"),
            ("scenario_d_price",    "Time-Series D", "Scenario D — Price Sensitivity"),
        ]
        for scenario_key, tab_name, scenario_label in ts_scenarios:
            if scenario_key not in self.all_results:
                continue
            if tab_name == "Time-Series B":
                if _ts_b_written:
                    continue
                _ts_b_written = True
            ws = self.wb.create_sheet(tab_name)
            self._write_timeseries(ws, scenario_key, scenario_label)

        if self.include_price_sensitivity:
            self._write_price_sensitivity()
        self._write_baseline_summary()
        if self.include_full_baseline:
            self._write_baseline_full()
        if self.include_scenario_full_data:
            # V12.12: full-data tabs are scenario-specific. Earlier workbooks
            # used one arm tab populated without scenario provenance,
            # which mixed Scenario A full data into Scenario B interpretation.
            for scenario_key in self.all_results:
                self._write_scenario_full_data(
                    scenario_key, self._scenario_full_data_tab_name(scenario_key)
                )

        # V11.8.2/V11.8.3: Transparency tabs
        if self.include_transparency_tabs:
            self._write_prep_dynamics_tab()
            self._write_incidence_subgroup_tab()

        self.wb.save(self.output_path)
        print(f"\n  ✓ HTA Excel report: {self.output_path}")

    # ─────────────────────────────────────────────────────────────────────────
    # Split runtime workbook — reviewer-facing report plus appendices
    # ─────────────────────────────────────────────────────────────────────────

    def _write_split_runtime_report(self):
        """Build the reviewer-oriented workbook for split runtime HTA configs."""
        comparison_rows = self._split_comparison_rows()
        self._write_split_executive_summary(comparison_rows)
        self._write_split_run_manifest()
        self._write_split_variant_register(comparison_rows)
        self._write_baseline_summary()
        self.wb["Baseline Key Years"].title = "Baseline Plausibility"
        self._retarget_chart_sheet_refs(
            self.wb["Baseline Plausibility"],
            "Baseline Key Years",
            "Baseline Plausibility",
        )
        self._write_split_baseline_forecast()
        self._write_split_main_cea_results(comparison_rows)
        self._write_split_cost_outcome_drivers(comparison_rows)
        self._write_split_budget_impact(comparison_rows)
        if self.include_price_sensitivity:
            self._write_split_price_sensitivity(comparison_rows)
        self._write_split_assumptions_sources()
        self._write_split_appendix_index()

        if self.include_transparency_tabs:
            self._write_prep_dynamics_tab()
            self.wb["PrEP Population Dynamics"].title = "Appendix - Annual PrEP"
            self._format_appendix_table("Appendix - Annual PrEP", "PrEPDynamicsAppendix")

            self._write_incidence_subgroup_tab()
            self.wb["Incidence by Subgroup"].title = "Appendix - Annual Incidence"
            self._format_appendix_table("Appendix - Annual Incidence", "IncidenceAppendix")

        if self.include_full_baseline:
            self._write_baseline_full()
        if self.include_scenario_full_data:
            for scenario_key in self.all_results:
                self._write_scenario_full_data(
                    scenario_key, self._scenario_full_data_tab_name(scenario_key)
                )

    def _split_scenario_config(self, scenario_name: str) -> dict:
        for scenario in self.hta_config.get("analysis_scenarios", []):
            if scenario.get("name") == scenario_name:
                return scenario
        return {}

    def _split_variant_label(self, scenario_name: str, scenario: dict) -> str:
        label = scenario.get("label") or self._scenario_label(scenario_name)
        if "(" in label and label.endswith(")"):
            return label[label.rfind("(") + 1:-1]
        if "__" in scenario_name:
            return scenario_name.split("__", 1)[1].replace("_", " ")
        return label

    def _split_scenario_code(self, scenario_name: str, scenario: dict) -> str:
        comparison_type = scenario.get("comparison_type", "")
        if scenario_name == "base_case_context" or comparison_type == "attribution":
            return "Base"
        if scenario_name.startswith("scenario_a_") or comparison_type == "substitution":
            return "A"
        if scenario_name.startswith("scenario_b_") or comparison_type == "add_on":
            return "B"
        if scenario_name.startswith("scenario_c_") or comparison_type == "oral_expansion_benchmark":
            return "C"
        return ""

    def _split_decision_role(self, scenario: dict) -> str:
        if scenario.get("primary_comparison"):
            return "Primary"
        if scenario.get("comparison_type") == "attribution":
            return "Context"
        if scenario.get("comparison_type") == "oral_expansion_benchmark":
            return "Benchmark"
        return "Secondary"

    def _split_comparison_rows(self) -> List[dict]:
        rows = []
        for order, scenario_name in enumerate(self.scenario_names):
            scenario = self._split_scenario_config(scenario_name)
            results = self.all_results.get(scenario_name, {})
            comparator_arm = scenario.get("comparator_arm")
            intervention_arm = scenario.get("intervention_arm")
            arm_order = scenario.get("arm_order", list(results.keys()))

            if not comparator_arm or not intervention_arm:
                if len(arm_order) >= 2:
                    comparator_arm, intervention_arm = arm_order[0], arm_order[1]
                else:
                    continue
            if comparator_arm not in results or intervention_arm not in results:
                continue

            comp = self.comparator.compare_pair(
                results[comparator_arm], results[intervention_arm]
            )
            label = scenario.get("label") or self._scenario_label(scenario_name)
            nmb = comp.get("nmb")
            dominance = comp.get("dominance")
            ce_status = self._ce_status_from_comparison(comp)

            rows.append({
                "order": order,
                "scenario": scenario_name,
                "code": self._split_scenario_code(scenario_name, scenario),
                "label": label,
                "variant": self._split_variant_label(scenario_name, scenario),
                "comparison_type": scenario.get("comparison_type", ""),
                "role": self._split_decision_role(scenario),
                "primary": bool(scenario.get("primary_comparison", False)),
                "comparator_arm": comparator_arm,
                "intervention_arm": intervention_arm,
                "comparator_label": ARM_LABELS.get(comparator_arm, comparator_arm),
                "intervention_label": ARM_LABELS.get(intervention_arm, intervention_arm),
                "delta_cost": comp.get("delta_cost"),
                "delta_qaly": comp.get("delta_qaly"),
                "icer": comp.get("icer"),
                "nmb": nmb,
                "infections_averted": comp.get("infections_averted"),
                "dominance": dominance,
                "cost_per_infection_averted": comp.get("cost_per_infection_averted"),
                "ce_status": ce_status,
                "scenario_config": scenario,
                "results": results,
            })

        role_order = {"Primary": 0, "Secondary": 1, "Benchmark": 2, "Context": 3}
        return sorted(rows, key=lambda r: (role_order.get(r["role"], 9), r["order"]))

    def _write_split_executive_summary(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("Executive Summary")
        ws.sheet_view.showGridLines = False
        _set_col_widths(ws, {
            1: 24, 2: 26, 3: 18, 4: 18, 5: 18, 6: 20, 7: 18, 8: 34
        })
        _merge_header(
            ws, 1, 1, 8,
            "Executive Summary — Lenacapavir PrEP HTA",
        )
        ws["A2"] = (
            "Decision-facing summary. Run provenance, variant definitions, "
            "budget impact, and audit appendices are separated into dedicated tabs."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=8)

        runtime = self.hta_config.get("_runtime_config", {})
        context = [
            ["Perspective", self._perspective.title()],
            ["WTP threshold", self.wtp],
            ["Freeze year", self.freeze_year],
            ["Evaluation horizon", f"{self.eval_years} years"],
            ["Economic inputs", runtime.get("study_defaults", {}).get("economic_inputs_file", "")],
        ]
        row = 4
        for label, value in context:
            _style_label(ws.cell(row=row, column=1), label)
            _style_value(ws.cell(row=row, column=2), value,
                         u'\u20ac#,##0' if label == "WTP threshold" else None)
            row += 1

        primary_rows = [r for r in comparison_rows if r["role"] == "Primary"]
        primary_thresholds = []
        primary_base_price = None
        for r in primary_rows:
            if not r["scenario_config"].get("price_sensitivity", {}).get("enabled"):
                continue
            _, base_price = self._split_price_input(r["scenario_config"])
            if not base_price:
                continue
            primary_base_price = primary_base_price or base_price
            threshold = self.comparator.find_threshold_price(
                r["results"],
                base_annual_price=base_price,
                comparator_arm=r["comparator_arm"],
                intervention_arm=r["intervention_arm"],
            )
            if threshold is not None:
                primary_thresholds.append(threshold)
        any_primary_ce = any(self._is_ce_status(r["ce_status"]) for r in primary_rows)
        price_context = ""
        if primary_base_price:
            price_context = f" Configured annual Lena price: €{primary_base_price:,.0f}."
            if primary_thresholds:
                price_context += (
                    f" Best primary threshold: about €{max(primary_thresholds):,.0f}/year."
                )
        bottom_line = (
            "At the configured price, at least one primary comparison variant "
            "is cost-effective at the configured Dutch WTP threshold."
            if any_primary_ce else
            "At the configured price, the primary comparison variants are not "
            "cost-effective at the configured Dutch WTP threshold."
        ) + price_context
        _merge_header(ws, 4, 4, 8, "Bottom Line", navy=False)
        ws["D5"] = bottom_line
        ws["D5"].alignment = _left()
        ws["D5"].font = _font(size=11, bold=True)
        ws.merge_cells(start_row=5, start_column=4, end_row=7, end_column=8)

        table_row = 10
        _merge_header(ws, table_row, 1, 8, "Primary Decision Results")
        headers = [
            "Scenario", "Comparator", "Intervention", "ICER", "NMB",
            "Infections Averted (rounded)", "CE Status", "Reviewer Reading"
        ]
        rows = []
        for r in primary_rows:
            rows.append([
                r["label"], r["comparator_label"], r["intervention_label"],
                r["icer"], r["nmb"], r["infections_averted"], r["ce_status"],
                self._split_reviewer_reading(r),
            ])
        next_row = _write_table(
            ws, table_row + 1, headers, rows, "DecisionPrimaryResults",
            formats={4: u'\u20ac#,##0', 5: u'\u20ac#,##0', 6: "#,##0"},
        )
        for rr in range(table_row + 2, table_row + 2 + len(rows)):
            _style_icer_cell(ws.cell(row=rr, column=4),
                             ws.cell(row=rr, column=4).value, self.wtp,
                             ws.cell(row=rr, column=5).value)
            status_cell = ws.cell(row=rr, column=7)
            status_cell.fill = _fill(
                FILL_CE_YES
                if self._is_ce_status(status_cell.value)
                else FILL_CE_NO
            )

        if rows:
            ws.cell(row=next_row, column=1).value = (
                "Secondary and benchmark comparisons are shown in the CEA Results tab."
            )
            ws.cell(row=next_row, column=1).font = _font(
                size=10, color=GREY_TEXT, italic=True
            )
            ws.merge_cells(start_row=next_row, start_column=1,
                           end_row=next_row, end_column=8)

        ws.freeze_panes = "A10"

    def _write_split_run_manifest(self) -> None:
        ws = self.wb.create_sheet("Run Manifest")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 6, "Run Manifest — Provenance And Audit Trail")
        ws["A2"] = (
            "Static workbook generated from model outputs. Re-run the HTA pipeline "
            "after changing model inputs; do not edit report cells as model inputs."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)

        meta = self.base_config.get("meta", {})
        rc = self.base_config.get("run_control", {})
        runtime = self.hta_config.get("_runtime_config", {})
        output_dir = os.path.dirname(self.output_path)
        manifest_rows = [
            ["Workbook", "Output path", self.output_path, "hta_report_writer"],
            ["Workbook", "Output folder", output_dir, "hta_report_writer"],
            ["Model", "Model name", extract_value(meta.get("model_name", "")), "base_config.meta"],
            ["Model", "Version", extract_value(meta.get("version", "")), "base_config.meta"],
            ["Run", "Start year", extract_value(rc.get("start_year", "")), "base_config.run_control"],
            ["Run", "Step size", extract_value(rc.get("step_size", "")), "base_config.run_control"],
            ["HTA", "Perspective", self._perspective, "hta_config.perspective.primary"],
            ["HTA", "WTP threshold", self.wtp, "hta_config.wtp_threshold_eur_per_qaly"],
            ["HTA", "Freeze year", self.freeze_year, "hta_config.time_horizon.freeze_year"],
            ["HTA", "Evaluation horizon", self.eval_years, "hta_config.time_horizon.evaluation_years"],
            [
                "HTA",
                "Economic inputs file",
                runtime.get("study_defaults", {}).get("economic_inputs_file", ""),
                "hta_runtime_config.study_defaults",
            ],
            ["Report", "Static/interactivity", "Static output report", "Use source configs for model input changes"],
        ]
        next_row = _write_table(
            ws, 4,
            ["Area", "Item", "Value", "Source"],
            manifest_rows,
            "RunManifest",
            widths={1: 16, 2: 28, 3: 70, 4: 44},
        )
        for rr in range(5, 5 + len(manifest_rows)):
            if ws.cell(row=rr, column=2).value == "WTP threshold":
                ws.cell(row=rr, column=3).number_format = u'\u20ac#,##0'

        csv_rows = [
            ["scenario_register.csv", "Scenario and arm metadata", "Audit CSV"],
            ["comparison_table.csv", "Pairwise ICER/NMB comparison rows", "Audit CSV"],
            ["arm_summary.csv", "One row per scenario arm", "Audit CSV"],
            ["budget_impact.csv", "Annual and cumulative budget impact", "Audit CSV plus workbook summary"],
            ["price_sensitivity.csv", "Price grid by scenario", "Audit CSV plus workbook summary"],
            ["timestep_*.csv", "Quarterly economic timestep records", "Raw audit CSV"],
        ]
        _merge_header(ws, next_row, 1, 4, "CSV Inventory", navy=False)
        _write_table(
            ws, next_row + 1,
            ["File", "Content", "Workbook Role"],
            csv_rows,
            "CSVInventory",
            widths={1: 30, 2: 54, 3: 30},
        )
        ws.freeze_panes = "A4"

    def _write_split_variant_register(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("Scenario & Variant Register")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 11, "Scenario And Variant Register")
        ws["A2"] = (
            "One row per actual comparison variant included in the workbook. "
            "Use this tab to trace CEA, price, budget, and appendix outputs."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=11)

        rows = []
        for r in comparison_rows:
            scenario = r["scenario_config"]
            price_enabled = bool(scenario.get("price_sensitivity", {}).get("enabled"))
            files = [
                "comparison_table.csv",
                "arm_summary.csv",
                "budget_impact.csv",
            ]
            if price_enabled:
                files.append("price_sensitivity.csv")
            files.append("timestep_*.csv")
            rows.append([
                r["code"], r["role"], r["scenario"], r["comparison_type"],
                r["variant"], r["label"], r["comparator_arm"],
                r["intervention_arm"], "Yes" if price_enabled else "No",
                "Yes", "; ".join(files),
            ])

        _write_table(
            ws, 4,
            ["Code", "Role", "Scenario ID", "Family", "Variant", "Label",
             "Comparator Arm", "Intervention Arm", "Price Sensitivity",
             "Budget Impact", "Source Files"],
            rows, "ScenarioVariantRegister",
            widths={1: 10, 2: 14, 3: 46, 4: 24, 5: 24, 6: 42,
                    7: 22, 8: 24, 9: 18, 10: 16, 11: 64},
        )
        ws.freeze_panes = "A5"

    def _write_split_main_cea_results(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("CEA Results")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 14, "Cost-Effectiveness Results")
        ws["A2"] = (
            "One row per comparison. Positive infections-averted values are favorable; "
            "negative values indicate additional infections."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=14)

        rows = [[
            r["code"], r["role"], r["scenario"], r["variant"],
            r["label"], r["comparison_type"],
            r["comparator_label"], r["intervention_label"],
            r["delta_cost"], r["delta_qaly"], r["icer"], r["nmb"],
            r["infections_averted"], r["dominance"], r["ce_status"],
        ] for r in comparison_rows]
        next_row = _write_table(
            ws, 4,
            ["Code", "Role", "Scenario ID", "Variant", "Label", "Type",
             "Comparator", "Intervention", "ΔCost", "ΔQALY", "ICER", "NMB",
             "Infections Averted (+) / Added (-)", "Dominance", "CE Status"],
            rows, "MainCEAResults",
            widths={1: 10, 2: 14, 3: 46, 4: 24, 5: 42, 6: 20,
                    7: 20, 8: 24, 9: 18, 10: 14, 11: 18, 12: 18,
                    13: 24, 14: 16, 15: 20},
            formats={9: u'\u20ac#,##0', 10: "#,##0", 11: u'\u20ac#,##0',
                     12: u'\u20ac#,##0', 13: "#,##0"},
        )
        for rr in range(5, 5 + len(rows)):
            _style_icer_cell(ws.cell(row=rr, column=11),
                             ws.cell(row=rr, column=11).value, self.wtp,
                             ws.cell(row=rr, column=12).value)
            status = ws.cell(row=rr, column=15).value
            ws.cell(row=rr, column=15).fill = _fill(
                FILL_CE_YES if self._is_ce_status(status) else FILL_CE_NO
            )
        legend_row = next_row
        _merge_header(ws, legend_row, 1, 8, "Interpretation Key", navy=False)
        legend = [
            ["Dominant", "Lower cost and higher QALYs than comparator.", FILL_DOMINANT],
            ["Dominated", "Higher cost and lower QALYs than comparator.", FILL_DOMINATED],
            ["Cost-effective", "Positive net monetary benefit at configured WTP.", FILL_CE_YES],
            ["Not cost-effective", "Negative net monetary benefit at configured WTP.", FILL_CE_NO],
            ["Trade-off", "Higher cost and higher QALYs; judge by NMB/ICER threshold.", LIGHT_GREY],
        ]
        for offset, (label, note, fill) in enumerate(legend, 1):
            row = legend_row + offset
            _style_label(ws.cell(row=row, column=1), label)
            ws.cell(row=row, column=1).fill = _fill(fill)
            ws.cell(row=row, column=2).value = note
            ws.cell(row=row, column=2).alignment = _left()
            ws.cell(row=row, column=2).font = _font(size=10)
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=8)
        ws.freeze_panes = "A5"

    def _split_cost_total(self, res: ScenarioResult) -> float:
        return (
            res.total_costs_disc_societal
            if self._perspective == "societal" else res.total_costs_disc
        )

    def _split_cost_components(self, res: ScenarioResult) -> Dict[str, float]:
        payer = self._payer_breakdown_components(res)
        return {
            "Oral PrEP product": payer.get("prep_drug_oral", 0.0),
            "Lenacapavir product": payer.get("prep_drug_lenacapavir", 0.0),
            "HIV ART drug": payer.get("hiv_art_drug", 0.0),
            "Monitoring": payer.get("monitoring", 0.0),
            "HIV care": payer.get("hiv_care", 0.0),
            "Adverse events": payer.get("adverse_events", 0.0),
            "Productivity losses": res.societal_breakdown_disc.get("productivity_loss", 0.0),
            "Patient time": res.societal_breakdown_disc.get("patient_time", 0.0),
        }

    def _write_split_cost_outcome_drivers(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("Cost & Outcome Drivers")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 11, "Cost And Outcome Drivers")
        ws["A2"] = (
            "Component deltas are intervention minus comparator. This sheet is "
            "filterable by role, scenario family, and variant."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=11)

        rows = []
        for r in comparison_rows:
            comparator = r["results"][r["comparator_arm"]]
            intervention = r["results"][r["intervention_arm"]]
            comp_components = self._split_cost_components(comparator)
            intv_components = self._split_cost_components(intervention)
            total_delta = r["delta_cost"] or 0.0
            for component in comp_components:
                delta = intv_components.get(component, 0.0) - comp_components.get(component, 0.0)
                rows.append([
                    r["code"], r["role"], r["variant"], r["label"], "Cost", component,
                    comp_components.get(component, 0.0),
                    intv_components.get(component, 0.0),
                    delta,
                    delta / total_delta if total_delta else None,
                    "",
                ])
            rows.extend([
                [r["code"], r["role"], r["variant"], r["label"], "Outcome", "Discounted QALYs",
                 comparator.total_qalys_disc, intervention.total_qalys_disc,
                 r["delta_qaly"], None, ""],
                [r["code"], r["role"], r["variant"], r["label"], "Outcome", "Cumulative infections",
                 comparator.cumul_new_infections, intervention.cumul_new_infections,
                 -r["infections_averted"], None, "Negative delta means infections averted"],
            ])

        _write_table(
            ws, 4,
            ["Code", "Role", "Variant", "Scenario", "Domain", "Component",
             "Comparator", "Intervention", "Delta", "% of ΔCost", "Note"],
            rows, "CostOutcomeDrivers",
            widths={1: 10, 2: 14, 3: 24, 4: 44, 5: 14, 6: 28,
                    7: 18, 8: 18, 9: 18, 10: 14, 11: 36},
        )
        for rr in range(5, 5 + len(rows)):
            domain = ws.cell(row=rr, column=5).value
            if domain == "Cost":
                for cc in (7, 8, 9):
                    ws.cell(row=rr, column=cc).number_format = u'\u20ac#,##0'
                ws.cell(row=rr, column=10).number_format = "0.0%"
            else:
                for cc in (7, 8, 9):
                    ws.cell(row=rr, column=cc).number_format = "#,##0"
                ws.cell(row=rr, column=10).value = None
        ws.freeze_panes = "A5"

    def _split_annual_budget_frame(self, result: ScenarioResult) -> pd.DataFrame:
        """Annual cost, QALY, infection, and PrEP stock summary for reports."""
        if not result.timestep_records:
            return pd.DataFrame()

        econ = pd.DataFrame(result.timestep_records).copy()
        if "year" not in econ.columns:
            return pd.DataFrame()
        econ["year"] = econ["year"].astype(float).apply(lambda value: int(value // 1))

        required = [
            "step_costs_disc",
            "step_costs_undisc",
            "step_qalys_disc",
            "step_qalys_undisc",
            "step_new_infections",
            "total_population",
        ]
        for col in required:
            if col not in econ.columns:
                econ[col] = 0.0

        annual = (
            econ.groupby("year", as_index=False)
            .agg(
                annual_cost_disc=("step_costs_disc", "sum"),
                annual_cost_undisc=("step_costs_undisc", "sum"),
                annual_qalys_disc=("step_qalys_disc", "sum"),
                annual_qalys_undisc=("step_qalys_undisc", "sum"),
                annual_new_infections=("step_new_infections", "sum"),
                total_population_hcc=("total_population", "mean"),
            )
            .sort_values("year")
        )
        annual["cumul_cost_disc"] = annual["annual_cost_disc"].cumsum()
        annual["cumul_cost_undisc"] = annual["annual_cost_undisc"].cumsum()
        annual["cumul_qalys_disc"] = annual["annual_qalys_disc"].cumsum()
        annual["cumul_qalys_undisc"] = annual["annual_qalys_undisc"].cumsum()
        annual["cumul_new_infections"] = annual["annual_new_infections"].cumsum()

        if result.simulation_records:
            sim = pd.DataFrame(result.simulation_records).copy()
            if "year" in sim.columns:
                sim["year"] = sim["year"].astype(float).apply(lambda value: int(value // 1))
                agg_spec = {}
                for col in ("msm_on_oral", "msm_on_lena"):
                    if col in sim.columns:
                        agg_spec[f"{col}_hcc"] = (col, "mean")
                for col in ("prep_oral_initiations", "prep_lena_initiations"):
                    if col in sim.columns:
                        agg_spec[col] = (col, "sum")
                if agg_spec:
                    sim_annual = sim.groupby("year", as_index=False).agg(**agg_spec)
                    annual = annual.merge(sim_annual, on="year", how="left")

        return annual

    def _write_split_budget_impact(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("Budget Impact")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 14, "Budget Impact")
        ws["A2"] = (
            "Annual and cumulative intervention-minus-comparator impact. "
            "Rows are derived from timestep records and mirror budget_impact.csv."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=14)

        rows = []
        for r in comparison_rows:
            comparator = r["results"].get(r["comparator_arm"])
            intervention = r["results"].get(r["intervention_arm"])
            if comparator is None or intervention is None:
                continue
            comp = self._split_annual_budget_frame(comparator)
            intv = self._split_annual_budget_frame(intervention)
            if comp.empty or intv.empty:
                continue
            merged = comp.merge(intv, on="year", suffixes=("_comparator", "_intervention"))
            for _, row in merged.iterrows():
                rows.append([
                    r["code"], r["role"], r["variant"], r["label"], int(row["year"]),
                    row["annual_cost_disc_intervention"] - row["annual_cost_disc_comparator"],
                    row["cumul_cost_disc_intervention"] - row["cumul_cost_disc_comparator"],
                    row["annual_cost_undisc_intervention"] - row["annual_cost_undisc_comparator"],
                    row["cumul_cost_undisc_intervention"] - row["cumul_cost_undisc_comparator"],
                    row["annual_qalys_disc_intervention"] - row["annual_qalys_disc_comparator"],
                    row["cumul_qalys_disc_intervention"] - row["cumul_qalys_disc_comparator"],
                    row["annual_new_infections_comparator"] - row["annual_new_infections_intervention"],
                    row["cumul_new_infections_comparator"] - row["cumul_new_infections_intervention"],
                    row.get("msm_on_oral_hcc_intervention"),
                    row.get("msm_on_lena_hcc_intervention"),
                ])

        _write_table(
            ws, 4,
            ["Code", "Role", "Variant", "Scenario", "Year",
             "Annual Budget Impact Disc", "Cumulative Budget Impact Disc",
             "Annual Budget Impact Undisc", "Cumulative Budget Impact Undisc",
             "Annual ΔQALY Disc", "Cumulative ΔQALY Disc",
             "Annual Infections Averted", "Cumulative Infections Averted",
             "Oral Users HCC Intervention", "Lena Users HCC Intervention"],
            rows, "BudgetImpact",
            widths={1: 10, 2: 14, 3: 24, 4: 44, 5: 10, 6: 20, 7: 22,
                    8: 22, 9: 24, 10: 16, 11: 18, 12: 20, 13: 22,
                    14: 22, 15: 22},
            formats={5: "0", 6: u'\u20ac#,##0', 7: u'\u20ac#,##0', 8: u'\u20ac#,##0',
                     9: u'\u20ac#,##0', 10: "#,##0", 11: "#,##0",
                     12: "#,##0", 13: "#,##0", 14: "#,##0", 15: "#,##0"},
        )
        ws.freeze_panes = "A5"

    def _split_forecast_source_results(self) -> List[Tuple[str, str, ScenarioResult]]:
        """Return the compact no-PrEP and SoC PrEP arms for forecast context."""
        candidates = []
        preferred = self.all_results.get("base_case_context", {})
        if preferred:
            candidates.append(("base_case_context", preferred))
        candidates.extend(
            (scenario_name, results)
            for scenario_name, results in self.all_results.items()
            if scenario_name != "base_case_context"
        )

        for scenario_name, results in candidates:
            no_prep = results.get("no_prep")
            oral_prep = results.get("oral_prep")
            if no_prep is not None and oral_prep is not None:
                return [
                    ("Attribution No PrEP", scenario_name, no_prep),
                    ("SoC Oral PrEP (no expansion)", scenario_name, oral_prep),
                ]
        return []

    def _split_forecast_frame(self, result: ScenarioResult) -> pd.DataFrame:
        """Aggregate post-freeze simulation records into annual forecast rows."""
        records = result.simulation_records or []
        if not records:
            return pd.DataFrame()

        import math as _math

        df = pd.DataFrame(records).copy()
        if "year" not in df.columns:
            return pd.DataFrame()
        df["year_int"] = df["year"].apply(lambda y: int(_math.floor(float(y))))

        agg = {}
        for col in ("total_population", "prevalence", "adult_hiv_pos"):
            if col in df.columns:
                agg[col] = "last"
        for col in ("new_infections", "hiv_deaths"):
            if col in df.columns:
                agg[col] = "sum"
        if not agg:
            return pd.DataFrame()

        annual = (
            df.sort_values("year")
              .groupby("year_int", as_index=False)
              .agg(agg)
              .sort_values("year_int")
        )
        if "prevalence" not in annual.columns:
            if {"adult_hiv_pos", "total_population"}.issubset(annual.columns):
                annual["prevalence"] = annual.apply(
                    lambda row: (
                        row["adult_hiv_pos"] / row["total_population"]
                        if row["total_population"] else None
                    ),
                    axis=1,
                )
            else:
                annual["prevalence"] = None
        for col in ("total_population", "new_infections", "hiv_deaths"):
            if col not in annual.columns:
                annual[col] = None
        return annual

    @staticmethod
    def _five_year_forecast_years(years: List[int]) -> List[int]:
        if not years:
            return []
        ordered = sorted({int(year) for year in years})
        start = ordered[0]
        selected = [year for year in ordered if year == start or (year - start) % 5 == 0]
        if ordered[-1] not in selected:
            selected.append(ordered[-1])
        return selected

    def _write_split_baseline_forecast(self) -> None:
        ws = self.wb.create_sheet("Baseline Plausibility Forecast")
        ws.sheet_view.showGridLines = False
        _set_col_widths(ws, {1: 30, 2: 34, 3: 10, 4: 20, 5: 18, 6: 16, 7: 14})
        _merge_header(
            ws, 1, 1, 7,
            "Baseline Plausibility Forecast — Attribution Arms",
        )
        ws["A2"] = (
            "Compact model-only forecast context before economic projections. "
            "Rows show 5-year key years for the no-PrEP attribution arm and "
            "standard-of-care oral PrEP without expansion."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws["A2"].alignment = _left()
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=7)

        rows = []
        for arm_label, scenario_name, result in self._split_forecast_source_results():
            annual = self._split_forecast_frame(result)
            if annual.empty:
                continue
            years = self._five_year_forecast_years(annual["year_int"].dropna().astype(int).tolist())
            annual = annual[annual["year_int"].isin(years)]
            for _, row in annual.iterrows():
                rows.append([
                    arm_label,
                    scenario_name,
                    int(row["year_int"]),
                    row.get("total_population"),
                    row.get("new_infections"),
                    row.get("prevalence"),
                    row.get("hiv_deaths"),
                ])

        if not rows:
            ws["A4"] = "No attribution no-PrEP and SoC oral-PrEP forecast records available."
            ws["A4"].font = _font(size=10, color=GREY_TEXT, italic=True)
            return

        _write_table(
            ws, 4,
            ["Arm", "Source Scenario", "Year", "Total Population",
             "New Infections", "HIV Prevalence", "HIV Deaths"],
            rows,
            "BaselinePlausibilityForecast",
            formats={3: "0", 4: "#,##0", 5: "#,##0", 6: "0.000%", 7: "#,##0"},
        )
        ws.freeze_panes = "A5"

    def _split_price_input(self, scenario: dict) -> Tuple[Optional[str], Optional[float]]:
        def deep_get(node, dot_path):
            for key in dot_path.split("."):
                if not isinstance(node, dict) or key not in node:
                    return None
                node = node[key]
            return node

        ps_cfg = scenario.get("price_sensitivity", {})
        input_ref = ps_cfg.get(
            "input_ref",
            "prevention_costs.arm_specific.lenacapavir.injection_subsequent",
        )
        value = extract_value(deep_get(self.hta_config, input_ref))
        return input_ref, value

    def _split_threshold_interpretation(self, row: dict, threshold: Optional[float]) -> str:
        if threshold is not None:
            if row.get("delta_cost", 0) < 0 and row.get("delta_qaly", 0) < 0:
                return (
                    "Cost-saving trade-off becomes CE below about "
                    f"€{threshold:,.0f}/year"
                )
            return f"Cost-effective below about €{threshold:,.0f}/year"
        if row.get("dominance") == "dominated":
            return "Dominated at base comparison; no threshold"
        if row.get("delta_cost", 0) < 0 and row.get("delta_qaly", 0) < 0:
            return "Cost saving does not offset QALY loss in search range"
        return "No threshold found in search range"

    def _write_split_price_sensitivity(self, comparison_rows: List[dict]) -> None:
        ws = self.wb.create_sheet("Price & Threshold Analysis")
        ws.sheet_view.showGridLines = False
        _set_col_widths(ws, {
            1: 10, 2: 42, 3: 24, 4: 18, 5: 18,
            6: 18, 7: 18, 8: 34, 9: 18, 10: 18,
            11: 24, 12: 18,
        })
        _merge_header(ws, 1, 1, 12, "Price And Threshold Analysis")
        ws["A2"] = (
            "Top table summarizes threshold behavior. Detailed grid rows remain "
            "filterable by scenario and are color-coded by cost-effectiveness."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=12)

        ps_rows = [r for r in comparison_rows
                   if r["scenario_config"].get("price_sensitivity", {}).get("enabled")]
        summary_rows = []
        detail_rows = []
        for r in ps_rows:
            scenario = r["scenario_config"]
            input_ref, base_price = self._split_price_input(scenario)
            if not base_price:
                continue
            ps_cfg = scenario.get("price_sensitivity", {})
            threshold = self.comparator.find_threshold_price(
                r["results"],
                base_annual_price=base_price,
                comparator_arm=r["comparator_arm"],
                intervention_arm=r["intervention_arm"],
            )
            pct_values = [float(pct) for pct in ps_cfg.get("percent_values", [])]
            price_range = [round(pct * base_price, 2) for pct in pct_values]
            try:
                ps_df = self.comparator.price_sensitivity(
                    r["results"], price_range,
                    base_annual_price=base_price,
                    comparator_arm=r["comparator_arm"],
                    intervention_arm=r["intervention_arm"],
                )
            except ValueError:
                continue
            ce_at_base = self._is_ce_status(r["ce_status"])
            summary_rows.append([
                r["code"], r["label"], r["variant"], r["role"], base_price, threshold,
                "Yes" if ce_at_base else "No",
                self._split_threshold_interpretation(r, threshold),
                r["icer"], r["nmb"],
            ])
            for pct, (_, prow) in zip(pct_values, ps_df.iterrows()):
                detail_rows.append([
                    r["code"], r["scenario"], r["variant"], r["label"],
                    r["comparison_type"], pct, prow["price"], prow["delta_qaly"],
                    prow["delta_cost"], prow["icer"], prow["nmb"],
                    "Yes" if prow["cost_effective"] else "No",
                ])

        row = 4
        row = _write_table(
            ws, row,
            ["Code", "Scenario", "Variant", "Role", "Base Annual Price",
             "Threshold Annual Lena Price (EUR)", "CE at Base Price",
             "Threshold Interpretation", "Base ICER", "Base NMB"],
            summary_rows, "PriceSensitivitySummary",
            formats={5: u'\u20ac#,##0', 6: u'\u20ac#,##0',
                     9: u'\u20ac#,##0', 10: u'\u20ac#,##0'},
        )
        for rr in range(5, 5 + len(summary_rows)):
            ce_cell = ws.cell(row=rr, column=7)
            ce_cell.fill = _fill(FILL_CE_YES if ce_cell.value == "Yes" else FILL_DOMINATED)
        _merge_header(ws, row, 1, 12, "Detailed Price Grid")
        _write_table(
            ws, row + 1,
            ["Code", "Scenario ID", "Variant", "Scenario", "Type",
             "% of Base Price", "Annual Lena Price", "ΔQALY", "ΔCost",
             "ICER", "NMB", "Cost-Effective"],
            detail_rows, "PriceSensitivityDetail",
            formats={6: "0%", 7: u'\u20ac#,##0', 8: "#,##0",
                     9: u'\u20ac#,##0', 10: u'\u20ac#,##0',
                     11: u'\u20ac#,##0'},
        )
        detail_start = row + 2
        for rr in range(detail_start, detail_start + len(detail_rows)):
            ce_cell = ws.cell(row=rr, column=12)
            ce_cell.fill = _fill(FILL_CE_YES if ce_cell.value == "Yes" else FILL_DOMINATED)
        ws.freeze_panes = "A5"

    def _write_split_assumptions_sources(self) -> None:
        ws = self.wb.create_sheet("Assumptions & Sources")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 7, "Assumptions And Sources")
        ws["A2"] = (
            "Filter by domain to review scenario assumptions separately from "
            "economic inputs."
        )
        ws["A2"].font = _font(size=10, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=7)

        rows = []
        runtime = self.hta_config.get("_runtime_config", {})
        rows.extend([
            ["Runtime", "Perspective", self._perspective, "", "hta_runtime_config", "", ""],
            ["Runtime", "WTP threshold", self.wtp, "EUR/QALY", "hta_costing_config", "", ""],
            ["Runtime", "Evaluation horizon", self.eval_years, "years", "hta_runtime_config", "", ""],
            ["Runtime", "Freeze year", self.freeze_year, "year", "hta_runtime_config", "", ""],
        ])
        for pid, pathway in runtime.get("prep_target_pathways", {}).items():
            if not isinstance(pathway, dict):
                continue
            mode = pathway.get("mode", "")
            source = (
                pathway.get("source", "")
                or pathway.get("target_stock", {}).get("source", "")
                or pathway.get("additional_target_stock", {}).get("source", "")
                or pathway.get("switch_share_of_eligible_oral_users", {}).get("source", "")
            )
            references = ", ".join(
                pathway.get("benchmark_references", [])
                or pathway.get("target_stock", {}).get("benchmark_references", [])
            )
            status = (
                pathway.get("distribution_status", "")
                or pathway.get("target_stock", {}).get("distribution_status", "")
                or pathway.get("additional_target_stock", {}).get("distribution_status", "")
                or pathway.get("switch_share_of_eligible_oral_users", {}).get("distribution_status", "")
            )
            if mode == "target_stock":
                for year, stock in pathway.get("target_stock", {}).get("value", {}).items():
                    rows.append([
                        "Scenario pathway", f"{pid}.{year}", stock,
                        "target stock", source, references, status,
                    ])
            elif mode == "switch_share":
                shares = pathway.get("switch_share_of_eligible_oral_users", {}).get("value", [])
                if not isinstance(shares, list):
                    shares = [shares]
                for share in shares:
                    rows.append([
                        "Scenario pathway", f"{pid}.switch_{float(share):.0%}",
                        float(share), "eligible oral users switched",
                        source, references, status,
                    ])
            elif mode == "additional_target_stock":
                variants = pathway.get("additional_target_stock", {}).get("value", [])
                if isinstance(variants, dict):
                    # Single-case pathways store the schedule directly, as run_scenarios expects.
                    variants = [{"label": "schedule", "target_stock": variants}]
                for variant in variants:
                    label = variant.get("label", "variant")
                    for year, stock in variant.get("target_stock", {}).items():
                        rows.append([
                            "Scenario pathway", f"{pid}.{label}.{year}", stock,
                            "additional target stock", source, references, status,
                        ])
            else:
                rows.append([
                    "Scenario pathway", pid, "", mode, source, references, status,
                ])

        for key, val in self.hta_config.get("utilities", {}).items():
            if isinstance(val, dict):
                rows.append([
                    "Utility", key, extract_value(val), "",
                    val.get("source", ""), self._format_distribution(val.get("distribution", {})),
                    val.get("notes", ""),
                ])

        for item in self.hta_config.get("adverse_events", {}).get("items", []):
            item_id = item.get("id", "")
            label = item.get("label", item_id)
            for field, unit in (
                ("prevalence", "prevalence"),
                ("utility_decrement", "QALY decrement"),
                ("cost", "EUR"),
            ):
                block = item.get(field, {})
                if not isinstance(block, dict):
                    continue
                rows.append([
                    "Adverse event",
                    f"{item_id}.{field}",
                    extract_value(block),
                    unit,
                    block.get("source", ""),
                    self._format_distribution(block.get("distribution", {})),
                    block.get("notes", label),
                ])

        for arm, block in self.hta_config.get("prevention_costs", {}).get("arm_specific", {}).items():
            for key, val in block.items():
                if isinstance(val, dict):
                    rows.append([
                        "Prevention cost", f"{arm}.{key}", extract_value(val),
                        "EUR", val.get("source", ""),
                        self._format_distribution(val.get("distribution", {})),
                        val.get("notes", ""),
                    ])

        for key, val in self.hta_config.get("disease_costs", {}).items():
            if isinstance(val, dict):
                rows.append([
                    "Disease cost", key, extract_value(val), "EUR",
                    val.get("source", ""),
                    self._format_distribution(val.get("distribution", {})),
                    val.get("notes", ""),
                ])

        _write_table(
            ws, 4,
            ["Domain", "Parameter", "Value", "Unit/Mode", "Source",
             "Uncertainty / Reference", "Reviewer Note"],
            rows, "AssumptionsSources",
            widths={1: 20, 2: 40, 3: 24, 4: 16, 5: 48, 6: 30, 7: 48},
        )
        ws.freeze_panes = "A5"

    def _write_split_appendix_index(self) -> None:
        ws = self.wb.create_sheet("Appendix Index")
        ws.sheet_view.showGridLines = False
        _merge_header(ws, 1, 1, 5, "Appendix Index")
        rows = [
            [
                "Baseline Plausibility",
                "Historical model-only baseline plausibility checks",
                "Included",
                "Reviewer context",
                "Use to understand whether the calibrated epidemic trajectory is plausible before observed-data overlays.",
            ],
            [
                "Baseline Plausibility Forecast",
                "Five-year forecast key years for attribution no PrEP and SoC oral PrEP",
                "Included",
                "Reviewer context",
                "Use to inspect epidemiology trajectory before interpreting cost projections.",
            ],
            [
                "Appendix - Annual PrEP",
                "Annual PrEP stock, uptake, discontinuation, and coverage measures",
                "Included" if self.include_transparency_tabs else "Disabled",
                "Audit appendix",
                "Use with timestep_*.csv when checking how stock targets and switches affect PrEP coverage.",
            ],
            [
                "Appendix - Annual Incidence",
                "Annual incidence by scenario, arm, and PrEP subgroup",
                "Included" if self.include_transparency_tabs else "Disabled",
                "Audit appendix",
                "Use when reviewers need to trace epidemiology behind the ICERs.",
            ],
            [
                "Full Data tabs",
                "Quarterly scenario output records",
                "Included" if self.include_scenario_full_data else "Disabled",
                "Raw audit",
                "Keep off for reviewer workbooks unless a supervisor asks for every timestep in Excel.",
            ],
            [
                "CSV audit files",
                "scenario_register.csv; comparison_table.csv; arm_summary.csv; budget_impact.csv; price_sensitivity.csv; timestep_*.csv",
                "Written when csv_files.enabled is true",
                "Raw audit",
                f"Output directory: {os.path.dirname(self.output_path)}",
            ],
        ]
        _write_table(
            ws, 3,
            ["Workbook Area", "Content", "Status", "Role", "How To Use"],
            rows, "AppendixIndex",
            widths={1: 28, 2: 48, 3: 14, 4: 18, 5: 72},
        )
        ws.freeze_panes = "A4"

    def _format_appendix_table(self, sheet_name: str, table_name: str) -> None:
        ws = self.wb[sheet_name]
        ws.freeze_panes = "A3"
        if ws.max_row > 2 and ws.max_column > 1:
            _add_excel_table(ws, table_name, 2, 1, ws.max_row, ws.max_column)
            for col in range(1, ws.max_column + 1):
                if ws.cell(row=2, column=col).value == "Year":
                    for row in range(3, ws.max_row + 1):
                        ws.cell(row=row, column=col).number_format = "0"

    def _retarget_chart_sheet_refs(self, ws, old_name: str, new_name: str) -> None:
        old_ref = f"'{old_name}'!"
        new_ref = f"'{new_name}'!"
        for chart in getattr(ws, "_charts", []):
            for series in getattr(chart, "series", []):
                for source in (getattr(series, "val", None), getattr(series, "cat", None)):
                    if source is None:
                        continue
                    for attr in ("numRef", "strRef"):
                        ref = getattr(source, attr, None)
                        if ref is not None and getattr(ref, "f", None):
                            ref.f = ref.f.replace(old_ref, new_ref)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1 — Study Overview
    # ─────────────────────────────────────────────────────────────────────────

    def _write_overview(self):
        ws = self.wb.create_sheet("Study Overview")
        ws.sheet_view.showGridLines = False
        _set_col_widths(ws, {1: 32, 2: 28, 3: 45, 4: 18})

        meta   = self.base_config.get("meta", {})
        rc     = self.base_config.get("run_control", {})
        disc   = self.hta_config.get("discounting", {})
        gen_dt = datetime.now().strftime("%Y-%m-%d %H:%M")

        r = 1

        # ── Title bar ────────────────────────────────────────────────────────
        _merge_header(ws, r, 1, 4,
            "HIV Prevention Cost-Effectiveness Analysis — Netherlands", navy=True)
        ws.row_dimensions[r].height = 28
        r += 1
        _merge_header(ws, r, 1, 4,
            f"Report generated: {gen_dt}  |  Model: {extract_value(meta.get('model_name', {'value': 'V11'}))}  |  "
            f"Version: {extract_value(meta.get('version', {'value': '?'}))}",
            navy=False)
        ws.row_dimensions[r].height = 18
        r += 2

        # ── Section A: Simulation Setup ───────────────────────────────────────
        _merge_header(ws, r, 1, 4, "A.  Simulation Setup")
        ws.row_dimensions[r].height = 20
        r += 1

        sim_rows = [
            ("Start year",          extract_value(rc.get("start_year", {"value": ""})),      "run_control.start_year"),
            ("Baseline freeze year",self.freeze_year,                                          "hta_config — time_horizon.freeze_year"),
            ("Evaluation horizon",  f"{self.eval_years} years",                               "hta_config — time_horizon.evaluation_years"),
            ("Step size",           f"{extract_value(rc.get('step_size',{'value':3}))} {extract_value(rc.get('unit',{'value':'months'}))} (quarterly)",
                                                                                               "run_control.step_size + unit"),
            ("Run mode",            "Deterministic base case" if extract_value(
                                       self.base_config.get("simulation_controls", {})
                                                       .get("deterministic_mode", {"value": False})
                                     ) else "Stochastic single draw — not for base-case decision interpretation",
                                                                                               "simulation_controls.deterministic_mode"),
            ("RNG seed",            extract_value(self.base_config.get("simulation_controls",
                                       self.base_config.get("run_control", {})).get("seed", {"value": 42})),
                                                                                               "simulation_controls.seed"),
        ]
        sim_rows.extend(calibration_handoff_rows(self.base_config))
        _style_col_header(ws.cell(row=r, column=1), "Parameter")
        _style_col_header(ws.cell(row=r, column=2), "Value")
        _style_col_header(ws.cell(row=r, column=3), "Source / Config Key")
        r += 1
        for label, val, src in sim_rows:
            _style_label(ws.cell(row=r, column=1), label)
            _style_value(ws.cell(row=r, column=2), val, blue=True)
            _style_source(ws.cell(row=r, column=3), src)
            r += 1
        r += 1

        # ── Section B: Economic Parameters ────────────────────────────────────
        _merge_header(ws, r, 1, 4, "B.  Economic Parameters")
        ws.row_dimensions[r].height = 20
        r += 1

        disc_h = extract_value(disc.get("health_effects_rate", {"value": 0.015}))
        disc_c = extract_value(disc.get("cost_rate",           {"value": 0.040}))
        disc_y = extract_value(disc.get("base_year",           {"value": 2025}))

        econ_rows = [
            ("Health effects discount rate", f"{disc_h*100:.1f}% per annum",
             disc.get("health_effects_rate", {}).get("source", "ZIN guideline 2022")),
            ("Cost discount rate",           f"{disc_c*100:.1f}% per annum",
             disc.get("cost_rate", {}).get("source", "ZIN guideline 2022")),
            ("Discount base year",           disc_y,
             "hta_config — discounting.base_year"),
            ("WTP threshold",                f"€{self.wtp:,.0f} / QALY",
             self.hta_config.get("wtp_threshold_eur_per_qaly", {}).get("source",
                 "Zorginstituut Nederland guideline")),
        ]
        _style_col_header(ws.cell(row=r, column=1), "Parameter")
        _style_col_header(ws.cell(row=r, column=2), "Value")
        _style_col_header(ws.cell(row=r, column=3), "Source")
        r += 1
        for label, val, src in econ_rows:
            _style_label(ws.cell(row=r, column=1), label)
            _style_value(ws.cell(row=r, column=2), val, blue=True)
            _style_source(ws.cell(row=r, column=3), src)
            r += 1
        r += 1

        # ── Section C: Analysis Design Grid ───────────────────────────────────
        _merge_header(ws, r, 1, 4, "C.  Analysis Design")
        ws.row_dimensions[r].height = 20
        r += 1

        # Column headers: arm names
        _style_col_header(ws.cell(row=r, column=1), "Scenario")
        _style_col_header(ws.cell(row=r, column=2), "No PrEP")
        _style_col_header(ws.cell(row=r, column=3), "Oral PrEP")
        _style_col_header(ws.cell(row=r, column=4), "Lenacapavir")
        r += 1

        for sc in self.hta_config.get("analysis_scenarios", []):
            lena_ov = sc.get("lenacapavir_overrides", {})
            lena_note = lena_ov.get("notes", "")
            _style_label(ws.cell(row=r, column=1), sc.get("label", sc["name"]))
            _style_label(ws.cell(row=r, column=2), "Reference — no prevention intervention")
            _style_label(ws.cell(row=r, column=3), "TDF/FTC daily oral PrEP")
            _style_label(ws.cell(row=r, column=4), lena_note if lena_note else "Lenacapavir biannual injection")
            ws.row_dimensions[r].height = 36
            r += 1
        r += 1

        # ── Section D: Author / model notes ───────────────────────────────────
        _merge_header(ws, r, 1, 4, "D.  Model Notes")
        ws.row_dimensions[r].height = 20
        r += 1
        model_notes = meta.get("model_notes", {})
        notes_text  = model_notes.get("academic_framing", "")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        c = ws.cell(row=r, column=1)
        c.value = notes_text
        c.font  = _font(size=9, color=GREY_TEXT, italic=True)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 48

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 2 — Guide  (reviewer navigator)
    # ─────────────────────────────────────────────────────────────────────────

    def _write_guide(self):  # noqa: C901
        """
        Write a self-contained Guide tab explaining the HTA pipeline, every tab,
        all key metrics, colour coding, scenarios, arms, and a full glossary.
        Styled two-column (A: label/term, B: explanation) matching the calibration
        runner's Guide tab pattern.
        """
        ws = self.wb.create_sheet("Guide")
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 92

        # ── Local style helpers ───────────────────────────────────────────────
        def _title(row, text):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row=row, column=1, value=text)
            c.font = _font(bold=True, size=13, color=WHITE)
            c.fill = _fill(NAVY)
            c.alignment = Alignment(horizontal="left", vertical="center")

        def _subheading(row, text):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row=row, column=1, value=text)
            c.font = _font(bold=True, size=11, color=WHITE)
            c.fill = _fill("2E5E8E")
            c.alignment = Alignment(horizontal="left", vertical="center")

        def _label_text(row, label, text, min_height=30):
            a = ws.cell(row=row, column=1, value=label)
            a.font = _font(bold=True, size=10)
            a.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            b = ws.cell(row=row, column=2, value=text)
            b.font = _font(size=10)
            b.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            ws.row_dimensions[row].height = max(min_height, len(text) // 6)

        def _colour_row(row, label, hex_colour, meaning):
            a = ws.cell(row=row, column=1, value=label)
            a.font = _font(bold=True, size=10)
            a.fill = _fill(hex_colour)
            a.alignment = Alignment(horizontal="center", vertical="top")
            a.border = _border_thin()
            b = ws.cell(row=row, column=2, value=meaning)
            b.font = _font(size=10)
            b.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            b.border = _border_thin()
            ws.row_dimensions[row].height = 24

        def _blank(row):
            ws.row_dimensions[row].height = 6

        r = 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 1 — About This Report
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "About This Report"); r += 1
        _blank(r); r += 1

        _label_text(r, "What is this?",
            "This is the HTA Cost-Effectiveness Report for the analysis of Lenacapavir PrEP "
            "versus Oral PrEP (and no PrEP) for HIV prevention in the Netherlands. It is "
            "produced automatically by the HTA pipeline (run_scenarios.py) after each "
            "simulation run and replaces the previous set of separate CSV output files. "
            "All inputs, results, and supporting analyses are consolidated into a single "
            "shareable Excel workbook."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Intended audience",
            "Thesis supervisors, academic reviewers, Erasmus University examiners, and "
            "anyone evaluating the cost-effectiveness of Lenacapavir PrEP in the Dutch "
            "healthcare context. No prior knowledge of the model code is required — this "
            "workbook is designed to be self-contained for peer review."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "How to navigate",
            "Start with 'CEA Results Summary' for the headline ICER and NMB results. "
            "'Costs, QALYs & Events' gives the detailed breakdown. 'Parameters & Assumptions' "
            "documents every input value and its source. The three Time-Series tabs show "
            "how outcomes evolve over the evaluation horizon. 'Price Sensitivity' maps "
            "cost-effectiveness across a range of Lenacapavir prices. The Baseline and "
            "Full Data tabs allow independent verification of the simulation trajectory. "
            "Transparency tabs at the end document PrEP uptake dynamics and incidence "
            "disaggregated by subgroup."
        ); r += 1
        _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 2 — The HTA Pipeline
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "The HTA Pipeline — How It Works"); r += 1
        _blank(r); r += 1

        _label_text(r, "Overall flow",
            "1. A population model (engine_v3.py) simulates HIV transmission dynamics in the "
            "Netherlands over a user-defined evaluation horizon (freeze_year → freeze_year + "
            "evaluation_years). The model is a discrete-time stochastic agent of the MSM "
            "population, seeded with the 2024 HIV epidemic state. "
            "2. Three intervention arms are run: No PrEP (counterfactual), Oral PrEP "
            "(current standard of care), and Lenacapavir PrEP (intervention). "
            "3. For each arm, the economics module (economics.py) computes discounted "
            "costs (drug, monitoring, HIV care, adverse events) and QALYs. "
            "4. ScenarioComparator performs incremental analysis: ΔCost, ΔQALY, ICER, NMB. "
            "5. HTAReportWriter (hta_report.py) produces this workbook."
        , min_height=80); r += 1
        _blank(r); r += 1

        _label_text(r, "Scenarios vs Arms",
            "A SCENARIO is a policy or uptake configuration (e.g. universal access vs. "
            "targeted to high-activity MSM). Each scenario runs all three arms under the "
            "same configuration. An ARM is the intervention being evaluated within a "
            "scenario (No PrEP, Oral PrEP, or Lenacapavir). The primary comparison is "
            "Lenacapavir vs Oral PrEP — this is the incremental analysis of interest for "
            "the Dutch ZIN submission context."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Freeze year",
            "The model uses a 'freeze year' design: a calibrated historical simulation runs "
            "from the start year up to the freeze year (default: 2024), establishing the "
            "epidemic state at baseline. From the freeze year onward, the evaluation "
            "simulation runs with each arm's intervention in place. This ensures all three "
            "arms start from the same epidemic state and any differences in outcomes are "
            "attributable solely to the intervention."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Discounting",
            "Both costs and health effects are discounted to the freeze year using separate "
            "discount rates per ZIN 2022 guidelines. The default rates are 4% for costs "
            "and 1.5% for health effects (QALYs). Undiscounted values are also recorded "
            "for sensitivity analysis. All monetary values are in Euros (€)."
        ); r += 1
        _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 3 — Tab-by-Tab Guide
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Tab-by-Tab Guide"); r += 1
        _blank(r); r += 1

        tabs = [
            ("Study Overview",
             "Run metadata: model version, configuration file, evaluation horizon, WTP "
             "threshold, discount rates, perspective (payer vs societal), and scenario "
             "definitions. This is the 'cover sheet' — it documents the exact conditions "
             "under which this report was generated."),
            ("Guide  ← you are here",
             "This tab. A self-contained reference for reviewers explaining the pipeline, "
             "all tabs, key metrics, colour coding, scenarios, arms, and a glossary."),
            ("Key Figures",
             "A front-of-workbook visual summary focused on the main decision question. "
             "It shows incremental Lenacapavir-vs-Oral PrEP NMB, cumulative infections "
             "averted, and price sensitivity using chart scales that make the policy signal "
             "visible. Detailed backing tables remain on the scenario and price tabs."),
            ("CEA Results Summary",
             "The primary results tab. Contains: (a) arm-level totals for discounted QALYs, "
             "costs, and cumulative infections averted; (b) the incremental analysis table "
             "comparing each arm to its comparator — ΔCost, ΔQALY, ICER, NMB; (c) a "
             "ZIN three-tier WTP summary showing CE status at each Dutch threshold. "
             "ICER cells are colour-coded (see Colour Coding section below)."),
            ("Costs, QALYs & Events",
             "Detailed breakdown of costs by category (drug, monitoring, HIV care, AE) "
             "and QALYs, undiscounted and discounted, for each arm across all scenarios. "
             "Also reports cumulative new HIV infections per arm. Use this tab to verify "
             "the component-level drivers of any ICER."),
            ("Parameters & Assumptions",
             "Every model input parameter with its value, unit, source citation, and "
             "PSA distribution (where applicable). Blue cells = hardcoded inputs. "
             "Covers clinical parameters (efficacy, adherence, waning), economic parameters "
             "(drug prices, unit costs, discount rates), and Dutch-specific values "
             "(ZIN WTP tiers, productivity costs for societal perspective)."),
            ("Time-Series A",
             "Annual time-series of key outcomes (new infections, PrEP coverage, QALYs, "
             "costs) for Scenario A — Universal Access (all MSM eligible). Separate "
             "series for each arm. Use to inspect the trajectory of divergence between "
             "arms over the evaluation horizon and identify when cost-effectiveness is "
             "established."),
            ("Time-Series B",
             "Same as Time-Series A but for Scenario B — Targeted: High-Activity MSM only. "
             "Typically shows faster ICER improvement due to higher baseline risk in the "
             "targeted population."),
            ("Time-Series D",
             "Same as Time-Series A but for Scenario D — Price Sensitivity base run. "
             "This time-series uses the same population as Scenario A but may use a "
             "different price assumption to illustrate trajectory sensitivity to drug cost."),
            ("Price Sensitivity",
             "A systematic analysis of how the ICER and NMB change as the annual "
             "Lenacapavir price varies from 10% to 200% of the base price "
             "configured in hta_config.json. Each row shows: % of base, absolute price (€), ΔCost, "
             "ΔQALY, ICER, NMB, cost per infection averted, and CE flag. "
             "The threshold chart plots NMB against annual price with a zero-NMB reference "
             "line, so it remains readable even when ICER values are labelled Dominated. "
             "The CE flag uses NMB > 0 (correct in all four CEA quadrants)."),
            ("Baseline Key Years",
             "The pre-intervention (No PrEP) simulation trajectory at 5-year intervals from "
             "the start year. Reports total population, HIV+ count, PrEP coverage, births, "
             "deaths, and other demographic indicators. Used to verify that the model's "
             "baseline trajectory is clinically and demographically plausible before "
             "interpreting intervention results."),
            ("Baseline Full Data",
             "Full quarterly records of the baseline (No PrEP) simulation — every time "
             "step from freeze year to end of evaluation horizon. Columns cover all "
             "compartments, costs, QALYs, and epidemiological events per step. Togglable "
             "via hta_config outputs block (baseline_full_tab.enabled). Use for "
             "independent verification or secondary analysis."),
            ("Full Data A/B/D",
             "Scenario-specific full quarterly records across the evaluation period. "
             "Each row includes explicit scenario and arm columns, preventing Scenario A "
             "arm trajectories from being interpreted as Scenario B results."),
            ("PrEP Population Dynamics",
             "Transparency tab (V12.12): annual PrEP population counts and uptake/dropout "
             "flows keyed by scenario and arm. Used to verify that PrEP coverage ramps up "
             "correctly and that scenario-specific targeting is reflected in the reported "
             "arm trajectory."),
            ("Incidence by Subgroup",
             "Transparency tab (V12.12): annual HIV incidence metrics keyed by scenario "
             "and arm. Infections averted are calculated against the no-PrEP arm from the "
             "same scenario, not from a different scenario's reference arm."),
        ]

        for tab_name, description in tabs:
            _label_text(r, tab_name, description)
            r += 1
            _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 4 — Key Metrics
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Key Metrics — Definitions and Formulas"); r += 1
        _blank(r); r += 1

        metrics = [
            ("ICER  (Incremental Cost-Effectiveness Ratio)",
             "ICER = ΔCost / ΔQALY  where Δ = intervention minus comparator. "
             "Units: €/QALY. In the NE quadrant (more costly, more effective), the ICER "
             "is positive and compared to the WTP threshold. In the SW quadrant (less costly, "
             "fewer QALYs), a positive ICER is cost-effective only if ICER ≥ WTP — i.e., "
             "the cost saving is worth the QALY loss. The NMB rule handles all quadrants "
             "correctly; the ICER alone does not. Special labels: 'Dominant' = cheaper AND "
             "more effective (always cost-effective); 'Dominated' = more costly AND less "
             "effective (never cost-effective)."),
            ("NMB  (Net Monetary Benefit)",
             "NMB = WTP × ΔQALY − ΔCost. Positive NMB means the intervention is "
             "cost-effective at the specified WTP threshold. NMB is preferred over the ICER "
             "for decision-making because it is defined in all four quadrants of the CEA "
             "plane and handles dominant/dominated cases without special rules. "
             "All CE flags in this report use NMB > 0."),
            ("QALY  (Quality-Adjusted Life Year)",
             "One year of perfect health = 1 QALY. A year lived with a health condition is "
             "weighted by a utility score (0 = death, 1 = full health). QALYs are the "
             "standard effectiveness measure for Dutch HTA submissions (ZIN guideline 2022). "
             "In this model, utility decrements are applied for HIV+ stages (Acute, Chronic, "
             "AIDS) and for PrEP-related adverse events."),
            ("WTP Threshold  (Willingness-To-Pay)",
             "The maximum amount the Dutch healthcare system is willing to pay per additional "
             "QALY. ZIN uses a three-tier threshold based on proportional disease burden "
             "(see ZIN Three-Tier section below). The primary analysis uses the HIV/AIDS "
             "tier (€80,000/QALY). The default WTP is configurable in hta_config.json "
             "(wtp_threshold_eur_per_qaly)."),
            ("Incremental analysis",
             "All comparisons in this report are incremental: each arm is compared to "
             "its defined comparator (typically the next-less-effective arm). The primary "
             "comparison for this study is Lenacapavir vs Oral PrEP. Secondary comparisons "
             "include Oral PrEP vs No PrEP. Pairwise comparisons are presented in the "
             "CEA Results Summary tab."),
            ("Cost-effectiveness plane",
             "A scatter plot of ΔCost (y-axis) vs ΔQALY (x-axis) where each point "
             "represents a comparison. NE quadrant: trade-off (compare ICER to WTP). "
             "SW quadrant: trade-off (compare ICER to WTP, reversed). "
             "NW quadrant: dominated (avoid). SE quadrant: dominant (prefer)."),
        ]

        for label, text in metrics:
            _label_text(r, label, text, min_height=40)
            r += 1
            _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 5 — ZIN Three-Tier WTP
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "ZIN Three-Tier WTP Thresholds (Netherlands)"); r += 1
        _blank(r); r += 1

        _label_text(r, "Policy basis",
            "Zorginstituut Nederland (ZIN) applies a three-tier willingness-to-pay threshold "
            "based on the proportional shortfall (PS) of a disease — a measure of how much "
            "of the expected remaining healthy life is lost due to the condition. The greater "
            "the burden, the higher the ceiling price society is willing to pay per QALY. "
            "Source: Zorginstituut Nederland (2022). Richtlijn voor economische evaluaties "
            "in de gezondheidszorg. https://www.zorginstituutnederland.nl/"
        ); r += 1
        _blank(r); r += 1

        zin_tiers = [
            ("Tier 1  — PS < 0.40",  "D9D9D9", "€20,000/QALY  — Low disease burden. Example: mild chronic conditions."),
            ("Tier 2  — PS 0.40–0.69", "FFEB9C", "€50,000/QALY  — Moderate disease burden."),
            ("Tier 3  — PS ≥ 0.70",  "C6EFCE", "€80,000/QALY  — High disease burden. HIV/AIDS qualifies for Tier 3. "
                                                 "This is the primary threshold used in this analysis."),
        ]
        for label, hex_c, meaning in zin_tiers:
            _colour_row(r, label, hex_c, meaning); r += 1
        _blank(r); r += 1

        _label_text(r, "HIV/AIDS classification",
            "HIV/AIDS has a proportional shortfall ≥ 0.70 in the Dutch context due to "
            "lifelong disease, ART dependence, and residual mortality and morbidity. "
            "It therefore qualifies for the Tier 3 ceiling of €80,000/QALY. The CEA "
            "Results Summary tab shows CE status at all three tiers for completeness."
        ); r += 1
        _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 6 — Colour Coding
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Colour Coding Reference"); r += 1
        _blank(r); r += 1

        _label_text(r, "ICER cells  (CEA Results Summary)", "Colour applied to ICER cells based on cost-effectiveness outcome:"); r += 1
        _colour_row(r, "Dominant",  FILL_DOMINANT,
            "Intervention is both cheaper AND more effective than comparator. "
            "Always cost-effective. Green fill."); r += 1
        _colour_row(r, "Dominated", FILL_DOMINATED,
            "Intervention is more expensive AND less effective. Never cost-effective. "
            "Red fill."); r += 1
        _colour_row(r, "CE  ✓  (NMB > 0)", FILL_CE_YES,
            "Numeric ICER: NMB is positive at the specified WTP — cost-effective. "
            "Pale green fill."); r += 1
        _colour_row(r, "CE  ✗  (NMB ≤ 0)", FILL_CE_NO,
            "Numeric ICER: NMB is zero or negative — not cost-effective at this WTP. "
            "Amber fill. Note: amber (not red) because cost-effectiveness depends on "
            "the WTP chosen; this is not the same as 'dominated'. In the cheaper-but-less-"
            "effective quadrant, split-runtime reports label this as a cost-saving trade-off."); r += 1
        _blank(r); r += 1

        _label_text(r, "Parameters tab  (blue text)", "Blue cell values = inputs taken directly from configuration files or literature. "
            "Black cell values = derived or calculated from other inputs. This distinction "
            "helps auditors identify which parameters can be changed in the config vs which "
            "are computed quantities."); r += 1
        _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 7 — Scenarios
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Scenarios"); r += 1
        _blank(r); r += 1

        scenarios = [
            ("Scenario A — Universal Access",
             "All MSM in the Netherlands are eligible for and offered PrEP (Oral or "
             "Lenacapavir) according to the configured uptake rate. This is the broadest "
             "coverage scenario and represents the most optimistic case for infections "
             "averted. It is also the primary scenario for the ZIN submission, as Dutch "
             "PrEP policy is population-wide for MSM."),
            ("Scenario B — Targeted: High-Activity MSM",
             "PrEP is offered only to the high-sexual-activity MSM subgroup (those with "
             "the highest number of partners per year). This reflects a risk-stratified "
             "targeting strategy. Because the targeted group has higher per-capita "
             "transmission risk, the NNT (number needed to treat per infection averted) "
             "is lower, often producing a more favourable ICER than Scenario A."),
            ("Scenario D — Price Sensitivity",
             "A dedicated scenario for systematic price sensitivity analysis. The pipeline "
             "re-runs the full model at varying Lenacapavir price points and re-computes "
             "the ICER at each price. Results are tabulated in the 'Price Sensitivity' tab. "
             "This scenario uses the same population assumptions as Scenario A unless "
             "separately configured."),
        ]

        for label, text in scenarios:
            _label_text(r, label, text, min_height=50)
            r += 1
            _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 8 — Arms
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Intervention Arms"); r += 1
        _blank(r); r += 1

        arms = [
            ("No PrEP  (counterfactual)",
             "No pre-exposure prophylaxis is provided. HIV transmission proceeds under "
             "background prevention measures (condom use, ART-as-prevention). This is the "
             "counterfactual baseline and is used as the reference arm for all incremental "
             "comparisons. It does not represent current Dutch practice — it represents the "
             "absence of any PrEP programme."),
            ("Oral PrEP  (standard of care)",
             "Daily oral tenofovir/emtricitabine (TDF/FTC), the current standard of care "
             "for PrEP in the Netherlands. Efficacy ~86% (MSM RCT data). Costs include "
             "drug cost (~€700/year generic), quarterly monitoring visits (HIV test, renal "
             "function, STI screen), and any adverse events (primarily renal). This arm "
             "represents the current Dutch PrEP programme as comparator."),
            ("Lenacapavir  (intervention)",
             "Twice-yearly subcutaneous injection of lenacapavir (cabotegravir-LA "
             "equivalent in the modelling context, pending Dutch registration). Efficacy "
             "~99.9% in PURPOSE 1/2 trial data. Drug cost ~€20,555/year (base case). "
             "Monitoring visits are less frequent than oral PrEP due to the injected "
             "formulation. The Lenacapavir Waning state models the gradual decline in "
             "protection in the weeks before each injection (implemented in V12.2)."),
        ]

        for label, text in arms:
            _label_text(r, label, text, min_height=50)
            r += 1
            _blank(r); r += 1

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 9 — Glossary
        # ══════════════════════════════════════════════════════════════════════
        _title(r, "Glossary"); r += 1
        _blank(r); r += 1

        glossary = [
            ("ART  (Antiretroviral Therapy)",
             "Drug regimen that suppresses HIV viral load to undetectable levels. "
             "Modelled in three stages: first-line ART, second-line ART, and off-ART "
             "(lost to follow-up). ART-suppressed individuals have near-zero transmission "
             "probability (U=U: Undetectable = Untransmittable)."),
            ("CEA  (Cost-Effectiveness Analysis)",
             "A health economic technique comparing the incremental cost per unit of "
             "health gain between two interventions. The standard method for Dutch HTA "
             "submissions. Results are expressed as an ICER (€/QALY)."),
            ("Discount rate",
             "The annual rate at which future costs and health effects are discounted to "
             "present value. Dutch ZIN guideline 2022 specifies 4% for costs and 1.5% for "
             "health effects. Discounting reflects the time preference of society — a QALY "
             "gained today is worth more than one gained in 20 years."),
            ("Freeze year",
             "The calendar year at which the historical calibration simulation stops and "
             "the prospective evaluation begins. Default: 2024. All arms start from the "
             "same epidemic state at the freeze year, ensuring a fair comparison."),
            ("HIV stages (model compartments)",
             "Acute HIV: first ~3 months post-infection; high viral load, high "
             "transmissibility (~26× chronic), often undiagnosed. "
             "Chronic HIV: years 1–10 (approximately); lower viral load, manageable with "
             "ART. AIDS: CD4 < 200 cells/µL; high morbidity and mortality if untreated. "
             "Each stage has a distinct utility weight, ART uptake rate, and cost."),
            ("ICER  (Incremental Cost-Effectiveness Ratio)",
             "See Key Metrics section above. ICER = ΔCost / ΔQALY (€/QALY)."),
            ("MSM  (Men who have Sex with Men)",
             "The primary risk group modelled. HIV incidence in the Netherlands is "
             "concentrated in this population (~75% of new diagnoses). The model "
             "distinguishes low-activity and high-activity MSM subgroups based on annual "
             "partner count."),
            ("NMB  (Net Monetary Benefit)",
             "See Key Metrics section above. NMB = WTP × ΔQALY − ΔCost (€)."),
            ("NNT  (Number Needed to Treat)",
             "The number of individuals who need to receive the intervention to prevent "
             "one additional HIV infection compared to the comparator. Derived as "
             "1 / (absolute risk reduction). Lower NNT = more efficient infection "
             "prevention. Reported in Costs, QALYs & Events tab."),
            ("PrEP  (Pre-Exposure Prophylaxis)",
             "Antiretroviral medication taken by HIV-negative individuals to prevent "
             "infection. Reduces acquisition risk by 86–99.9% depending on formulation and "
             "adherence. Two formulations are compared in this analysis: daily oral TDF/FTC "
             "and twice-yearly injectable lenacapavir."),
            ("Proportional Shortfall  (PS)",
             "A Dutch HTA concept measuring the fraction of expected remaining healthy "
             "life that is lost due to a disease condition. PS = (QALE_healthy − "
             "QALE_sick) / QALE_healthy. Higher PS → higher ZIN WTP tier. "
             "HIV/AIDS PS ≥ 0.70 → Tier 3 (€80,000/QALY)."),
            ("PSA  (Probabilistic Sensitivity Analysis)",
             "A method that propagates parameter uncertainty through the model by sampling "
             "each parameter from its specified distribution (Beta, Gamma, etc.) in "
             "thousands of Monte Carlo iterations. Produces a distribution of ICERs and "
             "cost-effectiveness acceptability curves (CEACs). PSA results are produced by "
             "a separate runner (run_psa_hta.py) and stored in the PSA HTA output workbook."),
            ("QALY  (Quality-Adjusted Life Year)",
             "See Key Metrics section above."),
            ("Simulation window",
             "The full calendar period covered by the model run: from run_control.start_year "
             "(demographic calibration) through the evaluation horizon end. The evaluation "
             "horizon relevant for HTA outputs is freeze_year to freeze_year + evaluation_years."),
            ("Utility / QALY weight",
             "A value between 0 (equivalent to death) and 1 (full health) representing the "
             "health-related quality of life in a given health state. Used to convert "
             "life-years into QALYs. HIV-stage-specific utility values are sourced from "
             "published Dutch/European literature and documented in the Parameters tab."),
            ("WTP  (Willingness-To-Pay threshold)",
             "See Key Metrics section above and ZIN Three-Tier section above."),
        ]

        for label, text in glossary:
            _label_text(r, label, text, min_height=30)
            r += 1
            _blank(r); r += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 3 — Key Figures
    # ─────────────────────────────────────────────────────────────────────────

    def _write_key_figures(self):
        """
        Front-of-workbook visual summary of the main HTA results.

        This tab intentionally uses incremental Lenacapavir-vs-Oral PrEP views
        instead of absolute cumulative costs/QALYs.  The absolute totals remain
        available on the detailed time-series tabs, but they compress the actual
        decision signal on population-scale axes.
        """
        ws = self.wb.create_sheet("Key Figures")
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = "A4"
        _set_col_widths(ws, {
            1: 3, 2: 13, 3: 13, 4: 13, 5: 13, 6: 13, 7: 13, 8: 13, 9: 13, 10: 13, 11: 13,
            12: 4, 13: 24, 14: 24, 15: 24, 16: 24,
            18: 12, 19: 18, 20: 18, 21: 18, 22: 12, 23: 18, 24: 18, 25: 18, 26: 18,
        })

        _merge_header(ws, 1, 1, 16, "Key Figures — HTA Decision Summary")
        ws.row_dimensions[1].height = 24
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=16)
        intro = ws.cell(row=2, column=1)
        intro.value = (
            "Key economic and epidemiological results for Lenacapavir compared with oral PrEP. "
            "Net monetary benefit is shown at the configured willingness-to-pay threshold; "
            "values above zero indicate cost-effectiveness."
        )
        intro.font = _font(size=12, color=GREY_TEXT, italic=True)
        intro.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[2].height = 36

        def _figure_note(row: int, title: str, lines: List[str]) -> None:
            ws.merge_cells(start_row=row, start_column=13, end_row=row, end_column=16)
            title_cell = ws.cell(row=row, column=13)
            title_cell.value = title
            title_cell.font = _font(bold=True, size=14, color=WHITE)
            title_cell.fill = _fill("2E5E8E")
            title_cell.alignment = _left()
            title_cell.border = _border_thin()
            ws.row_dimensions[row].height = 24

            body = "\n".join(lines)
            ws.merge_cells(start_row=row + 1, start_column=13, end_row=row + 9, end_column=16)
            body_cell = ws.cell(row=row + 1, column=13)
            body_cell.value = body
            body_cell.font = _font(size=14)
            body_cell.fill = _fill(LIGHT_GREY)
            body_cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            body_cell.border = _border_thin()
            for rr in range(row + 1, row + 10):
                ws.row_dimensions[rr].height = 24

        def _make_line_chart(title: str, y_title: str, y_fmt: str,
                             cats: Reference, series_defs: List[Tuple[Reference, str, str, str]],
                             anchor: str, height: int = 15,
                             y_bounds: Optional[Tuple[float, float]] = None,
                             x_title: str = "Year",
                             x_fmt: str = "0",
                             x_tick_skip: Optional[int] = None,
                             show_legend: bool = False,
                             y_min_zero: bool = False) -> None:
            chart = LineChart()
            chart.title = title
            chart.style = 2
            chart.y_axis.title = y_title
            chart.y_axis.numFmt = y_fmt
            chart.y_axis.number_format = y_fmt
            chart.y_axis.delete = False
            chart.y_axis.tickLblPos = "low"
            if y_bounds:
                chart.y_axis.scaling.min = y_bounds[0]
                chart.y_axis.scaling.max = y_bounds[1]
                chart.y_axis.majorUnit = (y_bounds[1] - y_bounds[0]) / 4
            elif y_min_zero:
                chart.y_axis.scaling.min = 0
            chart.x_axis.title = x_title
            chart.x_axis.numFmt = x_fmt
            chart.x_axis.number_format = x_fmt
            chart.x_axis.delete = False
            chart.x_axis.tickLblPos = "low"
            if x_tick_skip:
                chart.x_axis.tickLblSkip = x_tick_skip
                chart.x_axis.tickMarkSkip = x_tick_skip
            chart.width = 24
            chart.height = height
            for data_ref, name, color, dash in series_defs:
                _add_styled_series(chart, data_ref, name, color, dash=dash)
            chart.set_categories(cats)
            if show_legend and chart.legend is not None:
                chart.legend.position = "b"
            else:
                chart.legend = None
            ws.add_chart(chart, anchor)

        def _symmetric_axis_bounds(values: List[float], pad: float = 1.10) -> Tuple[float, float]:
            vals = [v for v in values if v is not None]
            if not vals:
                return (-1, 1)
            bound = max(abs(min(vals)), abs(max(vals)))
            if bound == 0:
                bound = 1
            bound *= pad
            return (-bound, bound)

        def _scenario_incremental_rows(
            scenario_key: str,
            intervention_arm: str = "lenacapavir",
            reference_arm: str = "oral_prep",
        ) -> List[dict]:
            # V12.10: parameterised so expansion scenario (oral_prep_plus_lena vs oral_prep)
            # can reuse the same logic as the standard lena vs oral_prep comparison.
            annual = self._aggregate_timeseries(self.all_results.get(scenario_key, {}))
            if not annual:
                return []
            int_pfx = self._arm_timeseries_prefix(intervention_arm)
            ref_pfx = self._arm_timeseries_prefix(reference_arm)
            rows = []
            cumul_inf_averted = 0
            for year in sorted(annual):
                d = annual[year]
                required = [
                    f"{int_pfx}_cumul_cost", f"{ref_pfx}_cumul_cost",
                    f"{int_pfx}_cumul_qaly", f"{ref_pfx}_cumul_qaly",
                    f"{int_pfx}_infections", f"{ref_pfx}_infections",
                ]
                if any(k not in d for k in required):
                    continue
                delta_cost = d[f"{int_pfx}_cumul_cost"] - d[f"{ref_pfx}_cumul_cost"]
                delta_qaly = d[f"{int_pfx}_cumul_qaly"] - d[f"{ref_pfx}_cumul_qaly"]
                cumul_inf_averted += d[f"{ref_pfx}_infections"] - d[f"{int_pfx}_infections"]
                rows.append({
                    "year": year,
                    "nmb": self.wtp * delta_qaly - delta_cost,
                    "infections_averted": cumul_inf_averted,
                    "delta_cost": delta_cost,
                    "delta_qaly": delta_qaly,
                })
            return rows

        data_row = 125
        _merge_header(ws, data_row - 2, 18, 26, "Backing Data for Key Figures", navy=False)
        ws.cell(row=data_row - 1, column=18).value = (
            "These tables drive the charts above. They are kept visible for auditability."
        )
        ws.cell(row=data_row - 1, column=18).font = _font(size=9, color=GREY_TEXT, italic=True)
        data_row += 1

        eur_b_fmt = u'\u20ac#,##0,,,"B";-\u20ac#,##0,,,"B";-'
        eur_fmt = u'\u20ac#,##0;(\u20ac#,##0);-'

        # Scenario A backing data and charts
        scen_a_rows = _scenario_incremental_rows("scenario_a_base")
        scen_a_start = data_row + 1
        if scen_a_rows:
            for ci, header in enumerate(["Year", "Cumulative NMB", "Cumul. Infections Averted", "Cumul. ΔCost", "NMB = 0"], 18):
                _style_col_header(ws.cell(row=data_row, column=ci), header)
            r = scen_a_start
            for item in scen_a_rows:
                vals = [item["year"], item["nmb"], item["infections_averted"], item["delta_cost"], 0]
                fmts = ["0", eur_fmt, "#,##0", eur_fmt, eur_fmt]
                for ci, (val, fmt) in enumerate(zip(vals, fmts), 18):
                    _style_value(ws.cell(row=r, column=ci), val, fmt=fmt)
                r += 1
            scen_a_end = r - 1

            cats = Reference(ws, min_col=18, min_row=scen_a_start, max_row=scen_a_end)
            scen_a_nmb_bounds = _symmetric_axis_bounds([row["nmb"] for row in scen_a_rows])
            _make_line_chart(
                "Scenario A — Cumulative NMB: Lenacapavir vs Oral PrEP",
                "Net Monetary Benefit (€ billions)",
                eur_b_fmt,
                cats,
                [
                    (Reference(ws, min_col=19, max_col=19, min_row=scen_a_start, max_row=scen_a_end),
                     "NMB at configured WTP", _ARM_COLORS["lenacapavir"], "solid"),
                    (Reference(ws, min_col=22, max_col=22, min_row=scen_a_start, max_row=scen_a_end),
                     "NMB = 0 threshold", _WTP_COLOR, "lgDash"),
                ],
                "B5",
                y_bounds=scen_a_nmb_bounds,
                x_tick_skip=5,
            )
            _figure_note(5, "Net monetary benefit over time",
                         [
                             "Lenacapavir is compared with oral PrEP at the configured willingness-to-pay threshold.",
                             "Values above zero indicate cost-effectiveness.",
                         ])

            _make_line_chart(
                "Scenario A — Cumulative Infections Averted vs Oral PrEP",
                "Cumulative infections averted",
                "#,##0",
                cats,
                [
                    (Reference(ws, min_col=20, max_col=20, min_row=scen_a_start, max_row=scen_a_end),
                     "Infections averted", _ARM_COLORS["lenacapavir"], "solid"),
                ],
                "B34",
                y_min_zero=True,
                x_tick_skip=5,
            )
            _figure_note(34, "Cumulative infections averted",
                         [
                             "The cumulative prevention effect increases over time.",
                             "The plateau shows annual differences narrowing later in the horizon.",
                         ])
        else:
            ws.cell(row=5, column=2).value = "Scenario A data unavailable for key figures."

        # Scenario B backing data and chart
        # V12.10: prefer expansion framing; fall back to targeted if not present.
        if "scenario_b_expansion" in self.all_results:
            scen_b_rows = _scenario_incremental_rows(
                "scenario_b_expansion",
                intervention_arm="oral_prep_plus_lena",
                reference_arm="oral_prep",
            )
            scen_b_chart_title = (
                "Scenario B — Cumulative NMB: Oral PrEP + Lenacapavir vs Oral PrEP"
            )
            scen_b_note_title = "Expansion scenario (primary comparison)"
            scen_b_note_lines = [
                "Marginal value of adding Lenacapavir to the existing oral PrEP programme.",
                "Values above zero indicate the expansion is cost-effective at the configured WTP.",
            ]
            scen_b_arm_color = _ARM_COLORS["oral_prep_plus_lena"]
        else:
            scen_b_rows = _scenario_incremental_rows("scenario_b_targeted")
            scen_b_chart_title = (
                "Scenario B — Cumulative NMB: Targeted Lenacapavir vs Oral PrEP"
            )
            scen_b_note_title = "Targeted access scenario"
            scen_b_note_lines = [
                "This shows the same economic decision metric under high-activity MSM targeting.",
                "Values above zero indicate cost-effectiveness.",
            ]
            scen_b_arm_color = _ARM_COLORS["lenacapavir"]

        scen_b_start = data_row + 1
        if scen_b_rows:
            for ci, header in enumerate(["Year", "Cumulative NMB", "NMB = 0"], 23):
                _style_col_header(ws.cell(row=data_row, column=ci), header)
            r = scen_b_start
            for item in scen_b_rows:
                _style_value(ws.cell(row=r, column=23), item["year"], fmt="0")
                _style_value(ws.cell(row=r, column=24), item["nmb"], fmt=eur_fmt)
                _style_value(ws.cell(row=r, column=25), 0, fmt=eur_fmt)
                r += 1
            scen_b_end = r - 1
            cats = Reference(ws, min_col=23, min_row=scen_b_start, max_row=scen_b_end)
            scen_b_nmb_bounds = _symmetric_axis_bounds([row["nmb"] for row in scen_b_rows])
            _make_line_chart(
                scen_b_chart_title,
                "Net Monetary Benefit (€ billions)",
                eur_b_fmt,
                cats,
                [
                    (Reference(ws, min_col=24, max_col=24, min_row=scen_b_start, max_row=scen_b_end),
                     "NMB at configured WTP", scen_b_arm_color, "solid"),
                    (Reference(ws, min_col=25, max_col=25, min_row=scen_b_start, max_row=scen_b_end),
                     "NMB = 0 threshold", _WTP_COLOR, "lgDash"),
                ],
                "B63",
                y_bounds=scen_b_nmb_bounds,
                x_tick_skip=5,
            )
            _figure_note(63, scen_b_note_title, scen_b_note_lines)
        else:
            ws.cell(row=63, column=2).value = "Scenario B data unavailable for key figures."

        # Price sensitivity backing data and chart
        ps_rows = []
        ps_scenario = None
        for sc in self.hta_config.get("analysis_scenarios", []):
            if "price_sensitivity_range" in sc:
                ps_scenario = sc
                break
        base_annual_price = extract_value(
            self.hta_config
                .get("prevention_costs", {})
                .get("arm_specific", {})
                .get("lenacapavir", {})
                .get("injection_subsequent", {"value": None})
        )
        if ps_scenario and ps_scenario["name"] in self.all_results and base_annual_price:
            ps_range_cfg = ps_scenario["price_sensitivity_range"]
            if isinstance(ps_range_cfg, dict):
                price_range = [
                    round(pct / 100.0 * base_annual_price, 2)
                    for pct in range(ps_range_cfg["min_pct"],
                                     ps_range_cfg["max_pct"] + ps_range_cfg["step_pct"],
                                     ps_range_cfg["step_pct"])
                ]
            else:
                price_range = ps_range_cfg
            try:
                ps_df = self.comparator.price_sensitivity(
                    self.all_results[ps_scenario["name"]],
                    price_range,
                    base_annual_price=base_annual_price,
                )
                ps_rows = ps_df.to_dict("records")
            except ValueError:
                ps_rows = []

        price_start = data_row + 1
        if ps_rows:
            for ci, header in enumerate(["Annual Price", "NMB", "NMB = 0"], 26):
                _style_col_header(ws.cell(row=data_row, column=ci), header)
            r = price_start
            for item in ps_rows:
                _style_value(ws.cell(row=r, column=26), item["price"], fmt=eur_fmt)
                _style_value(ws.cell(row=r, column=27), item["nmb"], fmt=eur_fmt)
                _style_value(ws.cell(row=r, column=28), 0, fmt=eur_fmt)
                r += 1
            price_end = r - 1

            chart = LineChart()
            chart.title = "Price Sensitivity — NMB vs Lenacapavir Annual Price"
            chart.style = 2
            chart.y_axis.title = "Net Monetary Benefit (€ billions)"
            chart.y_axis.numFmt = eur_b_fmt
            chart.y_axis.number_format = eur_b_fmt
            price_nmb_bounds = _symmetric_axis_bounds([row["nmb"] for row in ps_rows])
            chart.y_axis.scaling.min = price_nmb_bounds[0]
            chart.y_axis.scaling.max = price_nmb_bounds[1]
            chart.y_axis.majorUnit = (price_nmb_bounds[1] - price_nmb_bounds[0]) / 4
            chart.y_axis.delete = False
            chart.y_axis.tickLblPos = "low"
            chart.x_axis.title = "Annual price (€/year)"
            chart.x_axis.numFmt = u'\u20ac#,##0'
            chart.x_axis.number_format = u'\u20ac#,##0'
            chart.x_axis.delete = False
            chart.x_axis.tickLblPos = "low"
            chart.x_axis.tickLblSkip = 2
            chart.x_axis.tickMarkSkip = 2
            chart.width = 24
            chart.height = 15
            _add_styled_series(
                chart,
                Reference(ws, min_col=27, max_col=27, min_row=price_start, max_row=price_end),
                f"NMB at WTP €{self.wtp:,.0f}/QALY",
                "4472C4",
            )
            _add_styled_series(
                chart,
                Reference(ws, min_col=28, max_col=28, min_row=price_start, max_row=price_end),
                "NMB = 0 threshold",
                _WTP_COLOR,
                width_emu=18000,
                dash="lgDash",
            )
            chart.set_categories(Reference(ws, min_col=26, min_row=price_start, max_row=price_end))
            chart.legend = None
            ws.add_chart(chart, "B92")
            _figure_note(92, "Price threshold",
                         [
                             "Net monetary benefit declines as annual price increases.",
                             "The zero crossing is the maximum price consistent with cost-effectiveness.",
                             "When ΔQALY is negative, a finite threshold means cost savings can offset the QALY loss.",
                         ])
        else:
            ws.cell(row=92, column=2).value = "Price sensitivity data unavailable for key figures."

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 4 — CEA Results Summary  (was Tab 2 pre-V12.5)
    # ─────────────────────────────────────────────────────────────────────────

    def _write_cea_summary(self):
        ws = self.wb.create_sheet("CEA Results Summary")
        ws.sheet_view.showGridLines = False
        # V11.7.1: extended to 10 incremental columns (was 7)
        _set_col_widths(ws, {1: 28, 2: 22, 3: 22, 4: 22, 5: 20,
                              6: 22, 7: 26, 8: 26, 9: 26, 10: 22})

        r = 1
        # WTP anchor cell (B1) — referenced by NMB formulas on this sheet
        ws.cell(row=r, column=1).value = "WTP Threshold (€/QALY)"
        ws.cell(row=r, column=1).font = _font(bold=True, size=10)
        wtp_cell = ws.cell(row=r, column=2)
        _style_value(wtp_cell, self.wtp, fmt=u'\u20ac#,##0', blue=True)
        wtp_ref = f"$B$1"
        r += 2

        for scenario_name, scenario_results in self.all_results.items():
            label = SCENARIO_LABELS.get(scenario_name, scenario_name)

            # ── Scenario header ───────────────────────────────────────────────
            _merge_header(ws, r, 1, 10, label)
            ws.row_dimensions[r].height = 22
            r += 1

            # ── Arm summary table ─────────────────────────────────────────────
            # BACKLOG-20: header and displayed cost depend on perspective.
            if self._perspective == "societal":
                cost_hdr = "Total Cost — Societal, Discounted (€)"
            else:
                cost_hdr = "Total Cost — Payer, Discounted (€)"

            arm_hdrs = [
                "Arm", cost_hdr, "Total QALYs, Discounted",
                "Avg. Cost / QALY (€)", "Cumulative Infections"
            ]
            for ci, h in enumerate(arm_hdrs, 1):
                _style_col_header(ws.cell(row=r, column=ci), h)
            r += 1

            arm_data_rows = {}  # arm_name -> row number (for formula references)
            for arm_name in self.ARM_ORDER:
                if arm_name not in scenario_results:
                    continue
                res = scenario_results[arm_name]
                cost_col = 2
                qaly_col = 3
                arm_data_rows[arm_name] = r

                # Select perspective-appropriate cost total (BACKLOG-20)
                if self._perspective == "societal":
                    cost_val = res.total_costs_disc_societal
                else:
                    cost_val = res.total_costs_disc

                _style_label(ws.cell(row=r, column=1), ARM_LABELS.get(arm_name, arm_name))
                _style_value(ws.cell(row=r, column=cost_col), cost_val,
                             fmt=u'€#,##0;(€#,##0);-')
                _style_value(ws.cell(row=r, column=qaly_col), res.total_qalys_disc,
                             fmt="#,##0.0")
                # Avg Cost/QALY formula
                cpq_cell = ws.cell(row=r, column=4)
                cpq_cell.value = f"={get_column_letter(cost_col)}{r}/{get_column_letter(qaly_col)}{r}"
                cpq_cell.font = _font(size=10)
                cpq_cell.number_format = u'€#,##0'
                cpq_cell.alignment = _right()
                cpq_cell.border = _border_thin()
                _style_value(ws.cell(row=r, column=5), res.cumul_new_infections,
                             fmt="#,##0")
                r += 1

            # Perspective footnote (BACKLOG-20)
            if self._perspective == "societal":
                _persp_note = (
                    "ICER based on societal perspective "
                    "(payer + productivity losses + patient time costs). "
                    "Payer-only and societal cost breakdown shown in Tab 3."
                )
            else:
                _persp_note = "ICER based on healthcare payer perspective only."
            _fn_cell = ws.cell(row=r, column=1)
            _fn_cell.value = _persp_note
            _fn_cell.font  = _font(italic=True, size=9)
            _fn_cell.alignment = Alignment(horizontal="left", wrap_text=True)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
            ws.row_dimensions[r].height = 28
            r += 2

            # ── Detect whether this is a price-sensitivity scenario ──────────
            # V12.9.1: price-sensitivity scenarios get a compact ZIN threshold
            # mini-table instead of the standard incremental analysis (which
            # would be an exact duplicate of scenario_a_base at the base price).
            _ps_cfg_entry = next(
                (sc for sc in self.hta_config.get("analysis_scenarios", [])
                 if sc.get("name") == scenario_name
                 and "price_sensitivity_range" in sc),
                None
            )

            if _ps_cfg_entry is not None:
                # ── Price-sensitivity mini-table (ZIN 3-tier threshold) ───────
                _merge_header(ws, r, 1, 4,
                              "Price Sensitivity Summary — ZIN Threshold Prices "
                              "(see Price Sensitivity tab for full sweep)",
                              navy=False)
                ws.row_dimensions[r].height = 22
                r += 1

                _tp_hdrs = [
                    "ZIN WTP Tier (\u20ac/QALY)",
                    "Threshold Annual Lena Product Price",
                    "Cost-Effective at Base Case?",
                    "Interpretation",
                ]
                for ci, h in enumerate(_tp_hdrs, 1):
                    _style_col_header(ws.cell(row=r, column=ci), h)
                r += 1

                _base_annual_price = extract_value(
                    self.hta_config
                        .get("prevention_costs", {})
                        .get("arm_specific", {})
                        .get("lenacapavir", {})
                        .get("injection_subsequent", {"value": None})
                )
                for _wtp_tier in _ZIN_WTP_TIERS:
                    _status = self._threshold_status(
                        scenario_results,
                        _wtp_tier,
                        _base_annual_price,
                    )
                    _threshold = _status["threshold"]
                    _is_ce_at_base = bool(_status["is_ce_at_base"])

                    _tier_cell = ws.cell(row=r, column=1)
                    _tier_cell.value = f"\u20ac{_wtp_tier:,.0f}/QALY"
                    _tier_cell.font = _font(size=10)
                    _tier_cell.alignment = _center()
                    _tier_cell.border = _border_thin()

                    _price_cell = ws.cell(row=r, column=2)
                    if _threshold is not None:
                        _style_value(_price_cell, _threshold,
                                     fmt=u'\u20ac#,##0', blue=True)
                    else:
                        _price_cell.value = (
                            "N/A \u2014 CE above search range"
                            if _is_ce_at_base else
                            "N/A \u2014 not CE at any price"
                        )
                        _price_cell.font = _font(size=10, color=GREY_TEXT)
                        _price_cell.alignment = _center()
                        _price_cell.border = _border_thin()

                    _ce_cell = ws.cell(row=r, column=3)
                    _ce_cell.value = "Yes" if _is_ce_at_base else "No"
                    _ce_cell.font = _font(size=10, bold=True)
                    _ce_cell.alignment = _center()
                    _ce_cell.border = _border_thin()
                    _ce_cell.fill = _fill(FILL_CE_YES if _is_ce_at_base
                                          else FILL_DOMINATED)

                    _note_cell = ws.cell(row=r, column=4)
                    _note_cell.value = _status.get("note", "")
                    _note_cell.font = _font(size=9, color=GREY_TEXT)
                    _note_cell.alignment = Alignment(wrap_text=True)
                    _note_cell.border = _border_thin()
                    r += 1

            else:
                # ── Standard incremental analysis table ───────────────────────
                _merge_header(ws, r, 1, 10,
                              "Incremental Analysis (ZIN Sequential  \u2014  "
                              "\u2020 Primary Comparison)",
                              navy=False)
                ws.row_dimensions[r].height = 20
                r += 1

                # V11.7.1: added cols 7-9 (per-person metrics)
                incr_hdrs = [
                    "Comparison",
                    "\u0394Cost (\u20ac)", "\u0394QALY",
                    "ICER (\u20ac/QALY)", "NMB at WTP (\u20ac)",
                    "Infections Averted",
                    "Cost per Infection Averted (\u20ac)",
                    "\u0394QALY per 1,000 Person-Years",
                    "\u0394Cost per Person-Year (\u20ac)",
                    "Dominance",
                ]
                for ci, h in enumerate(incr_hdrs, 1):
                    _style_col_header(ws.cell(row=r, column=ci), h)
                r += 1

                comp_table = self.comparator.build_comparison_table(scenario_results)
                if not comp_table.empty:
                    for _, row_data in comp_table.iterrows():
                        is_primary = row_data.get("is_primary_comparison") is True
                        involves_lena = "lenacapavir" in str(row_data["comparison"])
                        row_fill = (
                            FILL_LENA_PRIMARY   if is_primary else
                            FILL_LENA_SECONDARY if involves_lena else
                            None
                        )

                        # Label cell — bold for primary comparison
                        label_cell = ws.cell(row=r, column=1)
                        label_val = row_data["comparison"]
                        if is_primary:
                            label_val = "\u2020 " + label_val
                        _style_label(label_cell, label_val)
                        if is_primary:
                            label_cell.font = _font(bold=True, size=10)

                        dc_cell  = ws.cell(row=r, column=2)
                        dq_cell  = ws.cell(row=r, column=3)
                        nmb_cell = ws.cell(row=r, column=5)

                        _style_value(dc_cell, row_data["delta_cost"],
                                     fmt=u'\u20ac#,##0;(\u20ac#,##0);-')
                        _style_value(dq_cell, row_data["delta_qaly"],
                                     fmt="#,##0.0;(#,##0.0);-")

                        # ICER with colour — pass NMB for correct SW-quadrant colouring
                        icer_cell = ws.cell(row=r, column=4)
                        _style_icer_cell(icer_cell, row_data["icer"], self.wtp,
                                         nmb=row_data.get("nmb"))

                        # NMB formula: =WTP * delta_qaly - delta_cost
                        nmb_cell.value = (
                            f"={wtp_ref}*{get_column_letter(3)}{r}"
                            f"-{get_column_letter(2)}{r}"
                        )
                        nmb_cell.font  = _font(size=10)
                        nmb_cell.number_format = u'\u20ac#,##0;(\u20ac#,##0);-'
                        nmb_cell.alignment = _right()
                        nmb_cell.border = _border_thin()

                        _style_value(ws.cell(row=r, column=6),
                                     row_data.get("infections_averted", ""),
                                     fmt="#,##0;(#,##0);-")

                        # V11.7.1: per-person metrics (cols 7–9)
                        cia = row_data.get("cost_per_infection_averted")
                        _style_value(ws.cell(row=r, column=7),
                                     cia if cia is not None else "N/A",
                                     fmt=u'\u20ac#,##0;(\u20ac#,##0);-' if cia is not None else None)

                        dqpy = row_data.get("delta_qaly_per_1000_person_years")
                        _style_value(ws.cell(row=r, column=8),
                                     dqpy if dqpy is not None else "N/A",
                                     fmt="#,##0.000" if dqpy is not None else None)

                        dcpy = row_data.get("incremental_cost_per_person_year")
                        _style_value(ws.cell(row=r, column=9),
                                     dcpy if dcpy is not None else "N/A",
                                     fmt=u'\u20ac#,##0.00;(\u20ac#,##0.00);-' if dcpy is not None else None)

                        _style_label(ws.cell(row=r, column=10),
                                     row_data.get("dominance", ""))

                        # V12.9.1: apply row fill for lena comparisons.
                        # Skip col 4 (ICER) — _style_icer_cell owns that
                        # cell's colour to reflect CE status correctly.
                        if row_fill:
                            for ci in range(1, 11):
                                if ci == 4:
                                    continue  # ICER cell colour preserved
                                ws.cell(row=r, column=ci).fill = _fill(row_fill)

                        r += 1

                # V12.9.1: footnote explaining the dagger symbol
                _fn2_cell = ws.cell(row=r, column=1)
                _fn2_cell.value = (
                    "\u2020 Primary comparison (Lenacapavir vs Oral PrEP) — "
                    "shown regardless of ZIN sequential dominance status; "
                    "this is the central policy question."
                )
                _fn2_cell.font = _font(italic=True, size=9, color=GREY_TEXT)
                _fn2_cell.alignment = Alignment(horizontal="left", wrap_text=True)
                ws.merge_cells(start_row=r, start_column=1,
                               end_row=r, end_column=10)
                ws.row_dimensions[r].height = 28
                r += 1

            r += 2

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 3 — Costs, QALYs & Events Detail
    # ─────────────────────────────────────────────────────────────────────────

    def _write_detail(self):
        ws = self.wb.create_sheet("Costs, QALYs & Events")
        ws.sheet_view.showGridLines = False

        # Columns: label | then per arm*scenario block
        scenarios = list(self.all_results.keys())
        arms      = self.ARM_ORDER

        # Build column map: (scenario, arm) -> col index (1-based)
        base_col = 2
        col_map = {}
        for sc in scenarios:
            for arm in arms:
                if arm in self.all_results.get(sc, {}):
                    col_map[(sc, arm)] = base_col
                    base_col += 1

        total_cols = base_col - 1
        _set_col_widths(ws, {1: 34})
        for col_idx in range(2, total_cols + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 22

        r = 1

        # ── Scenario/arm column headers (two-row) ─────────────────────────────
        # Row 1: scenario spans
        cur_sc = None
        sc_start_col = None
        for (sc, arm), col in col_map.items():
            if sc != cur_sc:
                if cur_sc is not None:
                    ws.merge_cells(start_row=r, start_column=sc_start_col,
                                   end_row=r, end_column=col - 1)
                    sc_cell = ws.cell(row=r, column=sc_start_col)
                    sc_label = SCENARIO_LABELS.get(cur_sc, cur_sc)
                    _style_section_header(sc_cell, sc_label)
                cur_sc = sc
                sc_start_col = col
        # last scenario
        if cur_sc is not None:
            last_col = max(col_map.values())
            ws.merge_cells(start_row=r, start_column=sc_start_col,
                           end_row=r, end_column=last_col)
            sc_cell = ws.cell(row=r, column=sc_start_col)
            _style_section_header(sc_cell, SCENARIO_LABELS.get(cur_sc, cur_sc))
        r += 1

        # Row 2: arm headers
        ws.cell(row=r, column=1).value = ""
        for (sc, arm), col in col_map.items():
            _style_col_header(ws.cell(row=r, column=col),
                              ARM_LABELS.get(arm, arm))
        r += 1

        # ── Section A: Cost Breakdown (Discounted) ────────────────────────────
        _merge_header(ws, r, 1, total_cols, "A.  Cost Breakdown — Discounted (€)")
        ws.row_dimensions[r].height = 20
        r += 1

        # Broad drug is a memo row only: it mixes PrEP product costs and HIV ART.
        memo_cats = [
            ("drug", "Drug costs (broad category — memo)"),
        ]
        for cat_key, cat_label in memo_cats:
            _style_label(ws.cell(row=r, column=1), cat_label)
            for (sc, arm), col in col_map.items():
                res = self.all_results[sc].get(arm)
                val = res.cost_breakdown_disc.get(cat_key, 0) if res else 0
                _style_value(ws.cell(row=r, column=col), val,
                             fmt=u'\u20ac#,##0;(\u20ac#,##0);-')
            r += 1

        cost_cats = [
            ("prep_drug_oral", "Oral PrEP product costs"),
            ("prep_drug_lenacapavir", "Lenacapavir product costs"),
            ("hiv_art_drug", "HIV ART drug costs"),
            ("other_drug", "Other/unallocated drug costs"),
            ("monitoring", "Monitoring costs"),
            ("hiv_care", "HIV care costs"),
            ("adverse_events", "Adverse event costs"),
            ("terminal_care", "Terminal care costs"),
        ]
        for cat_key, cat_label in cost_cats:
            _style_label(ws.cell(row=r, column=1), cat_label)
            for (sc, arm), col in col_map.items():
                res = self.all_results[sc].get(arm)
                val = (
                    self._payer_breakdown_components(res).get(cat_key, 0.0)
                    if res else 0.0
                )
                _style_value(ws.cell(row=r, column=col), val,
                             fmt=u'\u20ac#,##0;(\u20ac#,##0);-')
            r += 1

        # Total row uses the actual payer total, not a fragile worksheet SUM.
        _style_label(ws.cell(row=r, column=1), "TOTAL — Payer Perspective (Discounted)")
        ws.cell(row=r, column=1).font = _font(bold=True, size=10)
        for (sc, arm), col in col_map.items():
            res = self.all_results[sc].get(arm)
            tot_cell = ws.cell(row=r, column=col)
            tot_cell.value = res.total_costs_disc if res else 0.0
            tot_cell.font  = _font(bold=True, size=10)
            tot_cell.number_format = u'\u20ac#,##0;(\u20ac#,##0);-'
            tot_cell.alignment = _right()
            tot_cell.border = _border_thin()
        r += 1

        _style_label(ws.cell(row=r, column=1), "Displayed payer breakdown residual")
        for (sc, arm), col in col_map.items():
            res = self.all_results[sc].get(arm)
            residual = self._payer_breakdown_residual(res) if res else 0.0
            _style_value(ws.cell(row=r, column=col), residual,
                         fmt=u'\u20ac#,##0;(\u20ac#,##0);-')
        r += 2

        # ── Section A2: Societal Cost Additions (BACKLOG-20) ───────────────────
        # Only written when perspective = societal OR report_payer_separately = True.
        if self._perspective == "societal" or self._report_payer_sep:
            _merge_header(ws, r, 1, total_cols,
                          "A2.  Societal Cost Components — Additional (€, Discounted)")
            ws.row_dimensions[r].height = 20
            r += 1

            societal_cats = [
                ("productivity_loss", "Productivity losses (friction cost method, ZIN 2022)"),
                ("patient_time",      "Patient time costs (opportunity cost of PrEP clinic visits)"),
            ]
            soc_cat_rows = {}
            for cat_key, cat_label in societal_cats:
                _style_label(ws.cell(row=r, column=1), cat_label)
                soc_cat_rows[cat_key] = r
                for (sc, arm), col in col_map.items():
                    res = self.all_results[sc].get(arm)
                    val = res.societal_breakdown_disc.get(cat_key, 0) if res else 0
                    _style_value(ws.cell(row=r, column=col), val,
                                 fmt=u'€#,##0;(€#,##0);-')
                r += 1

            # Subtotal: societal additions only
            _style_label(ws.cell(row=r, column=1), "Subtotal — Societal Additions")
            ws.cell(row=r, column=1).font = _font(bold=True, size=10)
            first_soc_row = min(soc_cat_rows.values())
            last_soc_row  = max(soc_cat_rows.values())
            for (sc, arm), col in col_map.items():
                col_letter = get_column_letter(col)
                soc_sub_cell = ws.cell(row=r, column=col)
                soc_sub_cell.value = f"=SUM({col_letter}{first_soc_row}:{col_letter}{last_soc_row})"
                soc_sub_cell.font  = _font(bold=True, size=10)
                soc_sub_cell.number_format = u'€#,##0;(€#,##0);-'
                soc_sub_cell.alignment = _right()
                soc_sub_cell.border = _border_thin()
            r += 1

            # Grand total: full societal perspective (payer + additions)
            _style_label(ws.cell(row=r, column=1),
                         "TOTAL — Full Societal Perspective (Discounted)")
            ws.cell(row=r, column=1).font = _font(bold=True, size=10)
            for (sc, arm), col in col_map.items():
                res = self.all_results[sc].get(arm)
                val = res.total_costs_disc_societal if res else 0
                soc_tot_cell = ws.cell(row=r, column=col)
                _style_value(soc_tot_cell, val, fmt=u'€#,##0;(€#,##0);-')
                soc_tot_cell.font = _font(bold=True, size=10)
            r += 2

        # ── Section B: QALY Breakdown ──────────────────────────────────────────
        _merge_header(ws, r, 1, total_cols, "B.  QALY Breakdown")
        ws.row_dimensions[r].height = 20
        r += 1

        qaly_rows_map = {}
        qaly_fields = [
            ("disc",   "QALYs — Discounted"),
            ("undisc", "QALYs — Undiscounted"),
        ]
        for fld, lbl in qaly_fields:
            _style_label(ws.cell(row=r, column=1), lbl)
            qaly_rows_map[fld] = r
            for (sc, arm), col in col_map.items():
                res = self.all_results[sc].get(arm)
                val = (res.total_qalys_disc if fld == "disc"
                       else res.total_qalys_undisc) if res else 0
                _style_value(ws.cell(row=r, column=col), val, fmt="#,##0.0")
            r += 1

        # Discounting impact row (formula)
        _style_label(ws.cell(row=r, column=1), "Discounting impact (Undisc − Disc)")
        for (sc, arm), col in col_map.items():
            col_letter = get_column_letter(col)
            imp_cell = ws.cell(row=r, column=col)
            imp_cell.value = (
                f"={col_letter}{qaly_rows_map['undisc']}"
                f"-{col_letter}{qaly_rows_map['disc']}"
            )
            imp_cell.font  = _font(size=10)
            imp_cell.number_format = "#,##0.0;(#,##0.0);-"
            imp_cell.alignment = _right()
            imp_cell.border = _border_thin()
        r += 2

        diagnostic_rows = []
        for sc, scenario_results in self.all_results.items():
            lena = scenario_results.get("lenacapavir")
            oral = scenario_results.get("oral_prep")
            if lena is None or oral is None:
                continue
            lena_py = self._discounted_person_years(lena)
            oral_py = self._discounted_person_years(oral)
            delta_qaly = lena.total_qalys_disc - oral.total_qalys_disc
            delta_py = lena_py - oral_py
            diagnostic_rows.append((
                SCENARIO_LABELS.get(sc, sc),
                delta_qaly,
                delta_py,
                delta_qaly - delta_py,
                lena.cumul_new_infections - oral.cumul_new_infections,
                self._sum_sim_metric(lena, "hiv_deaths") - self._sum_sim_metric(oral, "hiv_deaths"),
                self._sum_sim_metric(lena, "background_deaths") - self._sum_sim_metric(oral, "background_deaths"),
                self._sum_sim_metric(lena, "childhood_deaths") - self._sum_sim_metric(oral, "childhood_deaths"),
                self._sum_sim_metric(lena, "births") - self._sum_sim_metric(oral, "births"),
            ))

        if diagnostic_rows:
            _merge_header(ws, r, 1, 9, "B2.  Lenacapavir vs Oral PrEP QALY Driver Diagnostics")
            ws.row_dimensions[r].height = 20
            r += 1
            diag_hdrs = [
                "Scenario", "\u0394 discounted QALYs", "\u0394 discounted person-years",
                "\u0394 utility deficit", "\u0394 infections", "\u0394 HIV deaths",
                "\u0394 background deaths", "\u0394 childhood deaths", "\u0394 births",
            ]
            for ci, h in enumerate(diag_hdrs, 1):
                _style_col_header(ws.cell(row=r, column=ci), h)
            r += 1
            for row_vals in diagnostic_rows:
                _style_label(ws.cell(row=r, column=1), row_vals[0])
                for ci, val in enumerate(row_vals[1:], 2):
                    fmt = "#,##0.0;(#,##0.0);-" if ci <= 4 else "#,##0;(#,##0);-"
                    _style_value(ws.cell(row=r, column=ci), val, fmt=fmt)
                r += 1
            note = ws.cell(row=r, column=1)
            note.value = (
                "\u0394 utility deficit approximates residual discounted QALYs after "
                "discounted person-time differences; positive event deltas mean more "
                "events in Lenacapavir than Oral PrEP."
            )
            note.font = _font(size=9, italic=True, color=GREY_TEXT)
            note.alignment = Alignment(wrap_text=True)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
            r += 2

        # ── Section C: Epidemiological Events ─────────────────────────────────
        _merge_header(ws, r, 1, total_cols, "C.  Epidemiological Events")
        ws.row_dimensions[r].height = 20
        r += 1

        # Cumulative infections
        _style_label(ws.cell(row=r, column=1), "Cumulative new infections")
        infect_row = r
        for (sc, arm), col in col_map.items():
            res = self.all_results[sc].get(arm)
            val = res.cumul_new_infections if res else 0
            _style_value(ws.cell(row=r, column=col), val, fmt="#,##0")
        r += 1

        # Infections averted vs no_prep (formula)
        _style_label(ws.cell(row=r, column=1), "Infections averted vs No PrEP")
        for (sc, arm), col in col_map.items():
            no_prep_col = col_map.get((sc, "no_prep"))
            cell = ws.cell(row=r, column=col)
            if no_prep_col and arm != "no_prep":
                cell.value = (
                    f"={get_column_letter(no_prep_col)}{infect_row}"
                    f"-{get_column_letter(col)}{infect_row}"
                )
                cell.font  = _font(size=10)
                cell.number_format = "#,##0;(#,##0);-"
                cell.alignment = _right()
                cell.border = _border_thin()
            else:
                _style_value(cell, "—")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 4 — Parameters & Assumptions
    # ─────────────────────────────────────────────────────────────────────────

    def _write_parameters(self):
        ws = self.wb.create_sheet("Parameters & Assumptions")
        ws.sheet_view.showGridLines = False
        _set_col_widths(ws, {1: 36, 2: 14, 3: 45, 4: 48, 5: 28})

        r = 1

        def _param_header_row(row):
            for ci, lbl in enumerate(["Parameter", "Value", "Notes", "Source", "PSA Distribution"], 1):
                _style_col_header(ws.cell(row=row, column=ci), lbl)

        # ── Section A: Health State Utilities ─────────────────────────────────
        _merge_header(ws, r, 1, 5, "A.  Health State Utilities")
        ws.row_dimensions[r].height = 20
        r += 1
        _param_header_row(r)
        r += 1

        STATE_LABELS = {
            "susceptible":              "HIV-negative, no PrEP",
            "susceptible_oral_prep":    "HIV-negative, on Oral PrEP",
            "susceptible_lenacapavir":  "HIV-negative, on Lenacapavir",
            "hiv_acute":                "HIV — Acute infection",
            "hiv_chronic_on_art":       "HIV — Chronic, on ART",
            "hiv_chronic_off_art":      "HIV — Chronic, off ART",
            "aids_on_art":              "AIDS (CD4 <200), on ART",
            "aids_off_art":             "AIDS (CD4 <200), off ART",
        }
        utils = self.hta_config.get("utilities", {})
        for key, label in STATE_LABELS.items():
            if key not in utils:
                continue
            u = utils[key]
            dist = u.get("distribution", {})
            dist_str = self._format_distribution(dist)
            _style_label(ws.cell(row=r, column=1), label)
            _style_value(ws.cell(row=r, column=2), extract_value(u), fmt="0.000", blue=True)
            _style_source(ws.cell(row=r, column=3), u.get("notes", ""))
            _style_source(ws.cell(row=r, column=4), u.get("source", ""))
            _style_source(ws.cell(row=r, column=5), dist_str)
            r += 1
        r += 1

        # ── Section B: Adverse Events ──────────────────────────────────────────
        _merge_header(ws, r, 1, 5, "B.  Adverse Events")
        ws.row_dimensions[r].height = 20
        r += 1

        ae_hdrs = ["AE / Event", "Arm", "Billing", "Prevalence", "Utility Decrement",
                   "Annual Cost (€)", "Source"]
        _set_col_widths(ws, {1: 36, 2: 14, 3: 12, 4: 12, 5: 18, 6: 18, 7: 48})
        for ci, h in enumerate(ae_hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)
        r += 1

        for item in self.hta_config.get("adverse_events", {}).get("items", []):
            prev = extract_value(item.get("prevalence", {"value": ""}))
            ud   = extract_value(item.get("utility_decrement", {"value": ""}))
            cost = extract_value(item.get("cost", {"value": ""}))

            _style_label(ws.cell(row=r, column=1), item.get("label", item.get("id", "")))
            _style_label(ws.cell(row=r, column=2), ARM_LABELS.get(item.get("arm",""), item.get("arm","")))
            _style_label(ws.cell(row=r, column=3), item.get("billing_moment", ""))
            _style_value(ws.cell(row=r, column=4), prev, fmt="0.00", blue=True)
            _style_value(ws.cell(row=r, column=5), ud,   fmt="0.000", blue=True)
            _style_value(ws.cell(row=r, column=6), cost, fmt=u'\u20ac#,##0', blue=True)
            _style_source(ws.cell(row=r, column=7),
                          item.get("cost", {}).get("source", ""))
            ws.row_dimensions[r].height = 30
            r += 1
        r += 1

        # Reset widths for remaining sections
        _set_col_widths(ws, {1: 36, 2: 14, 3: 12, 4: 48, 5: 28})

        # ── Section C: Prevention Costs ────────────────────────────────────────
        _merge_header(ws, r, 1, 5, "C.  Prevention Costs")
        ws.row_dimensions[r].height = 20
        r += 1

        pc_hdrs = ["Item", "Annual Cost (€)", "Applies To", "Notes", "Source"]
        for ci, h in enumerate(pc_hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)
        r += 1

        pc = self.hta_config.get("prevention_costs", {})
        for shared in pc.get("shared", []):
            _style_label(ws.cell(row=r, column=1), shared.get("label", shared.get("id", "")))
            _style_value(ws.cell(row=r, column=2), shared.get("value", ""), fmt=u'\u20ac#,##0', blue=True)
            _style_label(ws.cell(row=r, column=3), ", ".join(shared.get("applies_to", [])))
            _style_source(ws.cell(row=r, column=4), shared.get("notes", ""))
            _style_source(ws.cell(row=r, column=5), shared.get("source", ""))
            r += 1

        for arm_name, arm_costs in pc.get("arm_specific", {}).items():
            for item_key, item in arm_costs.items():
                lbl = item.get("notes", item_key).split("—")[0].strip()
                _style_label(ws.cell(row=r, column=1),
                             f"{ARM_LABELS.get(arm_name, arm_name)}: {lbl}")
                _style_value(ws.cell(row=r, column=2), item.get("value", ""),
                             fmt=u'\u20ac#,##0', blue=True)
                _style_label(ws.cell(row=r, column=3), arm_name)
                _style_source(ws.cell(row=r, column=4), item.get("notes", ""))
                _style_source(ws.cell(row=r, column=5), item.get("source", ""))
                r += 1
        r += 1

        # ── Section D: Disease Costs ───────────────────────────────────────────
        _merge_header(ws, r, 1, 5, "D.  Disease Costs")
        ws.row_dimensions[r].height = 20
        r += 1

        dc_hdrs = ["Item", "Annual Cost (€)", "Applies To Stages", "Notes", "Source"]
        for ci, h in enumerate(dc_hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)
        r += 1

        for item_key, item in self.hta_config.get("disease_costs", {}).items():
            stages = ", ".join(item.get("applies_to_stages", []))
            _style_label(ws.cell(row=r, column=1), item.get("notes", item_key).split("—")[0].strip()
                         if "—" in item.get("notes","") else item_key.replace("_"," ").title())
            _style_value(ws.cell(row=r, column=2), item.get("value", ""),
                         fmt=u'\u20ac#,##0', blue=True)
            _style_label(ws.cell(row=r, column=3), stages)
            _style_source(ws.cell(row=r, column=4), item.get("notes", ""))
            _style_source(ws.cell(row=r, column=5), item.get("source", ""))
            r += 1
        r += 1

        # ── Section E: Formulae (plain language) ───────────────────────────────
        _merge_header(ws, r, 1, 5, "E.  Formulae Used")
        ws.row_dimensions[r].height = 20
        r += 1

        formulae = [
            ("QALY per timestep",
             "Sum across all persons of:  utility weight × timestep duration (years) × discount factor (health)"),
            ("Cost per timestep",
             "Sum across applicable persons of:  unit cost × timestep duration × discount factor (costs), "
             "where applicability is determined by health state, ART status, and arm"),
            ("Discount factor — health",
             "1 / (1 + r_h)^t   where r_h = 1.5% and t = years elapsed since base year 2025"),
            ("Discount factor — costs",
             "1 / (1 + r_c)^t   where r_c = 4.0% and t = years elapsed since base year 2025"),
            ("ICER",
             "ΔCost / ΔQALY   (incremental cost-effectiveness ratio). Special cases: "
             "Dominant when ΔCost < 0 and ΔQALY > 0; Dominated when ΔCost > 0 and ΔQALY < 0"),
            ("NMB (Net Monetary Benefit)",
             "WTP × ΔQALY − ΔCost   Positive NMB = cost-effective at the given threshold"),
            ("ZIN sequential analysis",
             "Arms ordered by ascending total discounted cost. ICERs computed between adjacent "
             "non-dominated arms. Dominated arms are excluded from further comparisons."),
        ]
        for term, definition in formulae:
            _style_label(ws.cell(row=r, column=1), term)
            ws.cell(row=r, column=1).font = _font(bold=True, size=10)
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
            def_cell = ws.cell(row=r, column=2)
            def_cell.value = definition
            def_cell.font  = _font(size=10)
            def_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.row_dimensions[r].height = 36
            r += 1

        # ── Section E: Societal Perspective Parameters (BACKLOG-20) ─────────────
        # Only rendered when the perspective block is present in hta_config.
        _persp_cfg_e = self.hta_config.get("perspective", {})
        if _persp_cfg_e:
            _merge_header(ws, r, 1, 5, "E.  Societal Perspective Parameters")
            ws.row_dimensions[r].height = 20
            r += 1

            # Reset column widths for this section
            _set_col_widths(ws, {1: 36, 2: 14, 3: 45, 4: 48, 5: 28})
            _param_header_row(r)
            r += 1

            def _soc_param_row(label, val_dict_or_scalar, notes="", source="", dist_str=""):
                """Write one parameter row in the societal section."""
                if isinstance(val_dict_or_scalar, dict):
                    val  = extract_value(val_dict_or_scalar)
                    src  = val_dict_or_scalar.get("source", source)
                    note = val_dict_or_scalar.get("notes", notes)
                    dist = self._format_distribution(val_dict_or_scalar.get("distribution", {}))
                else:
                    val  = val_dict_or_scalar
                    src  = source
                    note = notes
                    dist = dist_str
                _style_label(ws.cell(row=r, column=1), label)
                fmt = "0.000" if isinstance(val, float) and val < 100 else "0"
                _style_value(ws.cell(row=r, column=2), val, fmt=fmt, blue=True)
                _style_source(ws.cell(row=r, column=3), note)
                _style_source(ws.cell(row=r, column=4), src)
                _style_source(ws.cell(row=r, column=5), dist)

            # Perspective mode row
            _style_label(ws.cell(row=r, column=1), "Analysis perspective (primary)")
            ws.cell(row=r, column=2).value = _persp_cfg_e.get("primary", "payer")
            ws.cell(row=r, column=2).font  = _font(bold=True, size=10)
            _style_source(ws.cell(row=r, column=3),
                          "Societal perspective per ZIN 2022 guidelines (BACKLOG-20)")
            _style_source(ws.cell(row=r, column=4), "ZIN 2022 Richtlijn economische evaluaties")
            r += 1

            _prod = _persp_cfg_e.get("productivity_loss", {})
            if _prod:
                # Friction cost method parameters
                _soc_param_row("Method", "Friction cost (ZIN 2022 default)",
                               notes="Not human capital — friction cap limits per-step contribution",
                               source="ZIN 2022 Richtlijn economische evaluaties")
                r += 1
                _soc_param_row("Friction period (weeks)",
                               _prod.get("friction_period_weeks", {"value": 21}))
                r += 1
                _soc_param_row("Average hourly wage (€/hr)",
                               _prod.get("average_hourly_wage", {"value": 36.50}))
                r += 1
                _soc_param_row("Annual hours worked",
                               _prod.get("annual_hours_worked", {"value": 1720}))
                r += 1
                _soc_param_row("Employment rate modifier",
                               _prod.get("employment_rate_modifier", {"value": 1.0}))
                r += 1
                _soc_param_row("Working-age range",
                               f"{_prod.get('working_age_min', 15)}–{_prod.get('working_age_max', 65)} yrs",
                               notes="Age bands included in productivity loss computation",
                               source="ZIN 2022 standard")
                r += 1

                # Loss rates by stage
                _lr = _prod.get("loss_rate_by_stage", {})
                stage_labels = {
                    "acute":           "Loss rate — Acute HIV",
                    "chronic_off_art": "Loss rate — Chronic, off ART",
                    "chronic_on_art":  "Loss rate — Chronic, on ART (confirmed)",
                    "aids_off_art":    "Loss rate — AIDS, off ART",
                    "aids_on_art":     "Loss rate — AIDS, on ART",
                }
                for stage_key, stage_label in stage_labels.items():
                    if stage_key in _lr:
                        _soc_param_row(stage_label, _lr[stage_key])
                        r += 1

            _pt = _persp_cfg_e.get("patient_time_costs", {})
            if _pt:
                _soc_param_row("Oral PrEP — visits per year",
                               _pt.get("oral_prep_visits_per_year", {"value": 4}))
                r += 1
                _soc_param_row("Lenacapavir — visits per year",
                               _pt.get("lenacapavir_visits_per_year", {"value": 2}))
                r += 1
                _soc_param_row("Time per clinic visit (hours)",
                               _pt.get("time_per_visit_hours", {"value": 2.5}))
                r += 1

            # Informal care note row
            _ic = _persp_cfg_e.get("informal_care", {})
            _style_label(ws.cell(row=r, column=1), "Informal caregiving costs")
            ws.cell(row=r, column=2).value = "Excluded"
            ws.cell(row=r, column=2).font  = _font(italic=True, size=10)
            _style_source(ws.cell(row=r, column=3),
                          _ic.get("notes", "Excluded — pending supervisor input (BACKLOG-20)"))
            r += 2

    # ─────────────────────────────────────────────────────────────────────────
    # Tabs 5–7 — Time-Series per Scenario
    # ─────────────────────────────────────────────────────────────────────────

    def _write_timeseries(self, ws, scenario_name: str, scenario_label: str):
        ws.sheet_view.showGridLines = False
        scenario_results = self.all_results.get(scenario_name, {})
        ts_arms = self._scenario_timeseries_arms(scenario_results)
        total_cols = max(10, 1 + len(ts_arms) * 3)
        widths = {1: 10}
        for col_idx in range(2, total_cols + 1):
            widths[col_idx] = 24 if col_idx <= 1 + len(ts_arms) * 2 else 20
        _set_col_widths(ws, widths)

        # Aggregate timestep records to annual data
        annual = self._aggregate_timeseries(scenario_results)
        if annual is None or len(annual) == 0:
            ws.cell(row=1, column=1).value = f"No timestep data for {scenario_name}"
            return

        r = 1
        _merge_header(ws, r, 1, total_cols, scenario_label)
        ws.row_dimensions[r].height = 22
        r += 1

        # ── Data table ─────────────────────────────────────────────────────────
        _merge_header(ws, r, 1, total_cols, "Annual Summary — Discounted Cumulative Values", navy=False)
        ws.row_dimensions[r].height = 20
        r += 1

        data_hdrs = ["Year"]
        for metric_label in [
            "Cumul. Cost (€)",
            "Cumul. QALYs",
            "New Infections",
        ]:
            for arm_name in ts_arms:
                data_hdrs.append(f"{ARM_LABELS.get(arm_name, arm_name)}: {metric_label}")
        for ci, h in enumerate(data_hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)
        data_header_row = r
        r += 1

        data_start_row = r
        years_sorted = sorted(annual.keys())
        for yr in years_sorted:
            d = annual[yr]
            vals = [yr]
            fmts = [None]
            for suffix, fmt in [
                ("cumul_cost", u'\u20ac#,##0'),
                ("cumul_qaly", "#,##0.0"),
                ("infections", "#,##0"),
            ]:
                for arm_name in ts_arms:
                    prefix = self._arm_timeseries_prefix(arm_name)
                    vals.append(d.get(f"{prefix}_{suffix}", ""))
                    fmts.append(fmt)
            for ci, (v, fmt) in enumerate(zip(vals, fmts), 1):
                _style_value(ws.cell(row=r, column=ci), v, fmt=fmt)
            r += 1
        data_end_row = r - 1
        n_data_rows = data_end_row - data_start_row + 1
        r += 2

        if n_data_rows < 2:
            return

        # ── Charts ────────────────────────────────────────────────────────────
        _merge_header(ws, r, 1, total_cols, "Charts", navy=False)
        ws.row_dimensions[r].height = 20
        r += 1

        cost_start_col = 2
        qaly_start_col = cost_start_col + len(ts_arms)
        inf_start_col = qaly_start_col + len(ts_arms)
        chart_arms = [
            (
                idx,
                ARM_LABELS.get(arm_name, arm_name),
                _ARM_COLORS.get(arm_name, "808080"),
            )
            for idx, arm_name in enumerate(ts_arms)
        ]

        # ── Chart 1: Cumulative Discounted Costs ───────────────────────────────
        chart1 = LineChart()
        chart1.title              = "Cumulative Discounted Costs by Arm"
        chart1.style              = 2
        chart1.y_axis.title       = "Cumulative Cost (\u20ac billions)"
        chart1.y_axis.numFmt      = '\u20ac#,##0,,,"B"'
        chart1.y_axis.number_format = '\u20ac#,##0,,,"B"'
        chart1.x_axis.title       = "Year"
        chart1.x_axis.numFmt      = "0"
        chart1.x_axis.number_format = "0"
        chart1.width              = 28
        chart1.height             = 14

        cats = Reference(ws, min_col=1, min_row=data_start_row, max_row=data_end_row)
        for idx, label, color in chart_arms:
            col_idx = cost_start_col + idx
            data_ref = Reference(ws,
                                 min_col=col_idx, max_col=col_idx,
                                 min_row=data_start_row, max_row=data_end_row)
            _add_styled_series(chart1, data_ref, label, color)
        chart1.set_categories(cats)
        ws.add_chart(chart1, f"A{r}")
        r += 22

        # ── Chart 2: Cumulative Discounted QALYs ──────────────────────────────
        chart2 = LineChart()
        chart2.title              = "Cumulative Discounted QALYs by Arm"
        chart2.style              = 2
        chart2.y_axis.title       = "Cumulative QALYs (millions)"
        chart2.y_axis.numFmt      = '#,##0,,"M"'
        chart2.y_axis.number_format = '#,##0,,"M"'
        chart2.x_axis.title       = "Year"
        chart2.x_axis.numFmt      = "0"
        chart2.x_axis.number_format = "0"
        chart2.width              = 28
        chart2.height             = 14

        for idx, label, color in chart_arms:
            col_idx = qaly_start_col + idx
            data_ref = Reference(ws,
                                 min_col=col_idx, max_col=col_idx,
                                 min_row=data_start_row, max_row=data_end_row)
            _add_styled_series(chart2, data_ref, label, color)
        chart2.set_categories(cats)
        ws.add_chart(chart2, f"A{r}")
        r += 1
        r += 21

        # ── Chart 3: Annual New Infections ────────────────────────────────────
        # LineChart chosen over BarChart: 40+ years × 3 arms = 120 bars,
        # too dense to read.  A line chart shows the trajectory clearly.
        chart3 = LineChart()
        chart3.title              = "Annual New Infections by Arm"
        chart3.style              = 2
        chart3.y_axis.title       = "New Infections"
        chart3.y_axis.numFmt      = "#,##0"
        chart3.y_axis.number_format = "#,##0"
        chart3.x_axis.title       = "Year"
        chart3.x_axis.numFmt      = "0"
        chart3.x_axis.number_format = "0"
        chart3.width              = 28
        chart3.height             = 14

        for idx, label, color in chart_arms:
            col_idx = inf_start_col + idx
            data_ref = Reference(ws,
                                 min_col=col_idx, max_col=col_idx,
                                 min_row=data_start_row, max_row=data_end_row)
            _add_styled_series(chart3, data_ref, label, color)
        chart3.set_categories(cats)
        ws.add_chart(chart3, f"A{r}")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 8 — Price Sensitivity
    # ─────────────────────────────────────────────────────────────────────────

    def _write_price_sensitivity(self):
        ws = self.wb.create_sheet("Price Sensitivity")
        ws.sheet_view.showGridLines = False
        # Col widths: %Base | Price | ΔCost | ΔQALY | ICER | NMB | Cost/IA | CE | (WTP hidden)
        _set_col_widths(ws, {1: 18, 2: 24, 3: 24, 4: 16, 5: 24, 6: 24, 7: 30, 8: 22, 9: 1})

        split_ps_scenarios = [
            sc for sc in self.hta_config.get("analysis_scenarios", [])
            if sc.get("price_sensitivity", {}).get("enabled")
            and sc.get("name") in self.all_results
        ]
        if split_ps_scenarios:
            self._write_price_sensitivity_multi(ws, split_ps_scenarios)
            return

        r = 1
        _merge_header(ws, r, 1, 8,
                      "Price Sensitivity Analysis — Lenacapavir vs Oral PrEP (Scenario D)")
        ws.row_dimensions[r].height = 22
        r += 1

        # WTP ref cell (B2 — referenced in NMB formula)
        ws.cell(row=r, column=1).value = "WTP Threshold (€/QALY)"
        ws.cell(row=r, column=1).font = _font(bold=True, size=10)
        wtp_cell = ws.cell(row=r, column=2)
        _style_value(wtp_cell, self.wtp, fmt=u'\u20ac#,##0', blue=True)
        wtp_ref = "$B$2"
        r += 2

        # ── Locate scenario D and base annual price ────────────────────────────
        ps_scenario = None
        for sc in self.hta_config.get("analysis_scenarios", []):
            if "price_sensitivity_range" in sc:
                ps_scenario = sc
                break

        if ps_scenario is None or ps_scenario["name"] not in self.all_results:
            ws.cell(row=r, column=1).value = "No price sensitivity scenario found in results."
            return

        # Extract base annual per-person price from config (injection_subsequent)
        base_annual_price = extract_value(
            self.hta_config
                .get("prevention_costs", {})
                .get("arm_specific", {})
                .get("lenacapavir", {})
                .get("injection_subsequent", {"value": None})
        )
        if not base_annual_price:
            ws.cell(row=r, column=1).value = (
                "Cannot compute price sensitivity: injection_subsequent cost not found in config."
            )
            return

        # Generate percentage-based price range from config spec
        ps_range_cfg = ps_scenario["price_sensitivity_range"]
        if isinstance(ps_range_cfg, dict):
            min_pct  = ps_range_cfg["min_pct"]
            max_pct  = ps_range_cfg["max_pct"]
            step_pct = ps_range_cfg["step_pct"]
            price_range = [
                round(pct / 100.0 * base_annual_price, 2)
                for pct in range(min_pct, max_pct + step_pct, step_pct)
            ]
        else:
            # Backward-compatible: old list format treated as absolute prices
            price_range = ps_range_cfg

        scenario_results = self.all_results[ps_scenario["name"]]

        try:
            ps_df = self.comparator.price_sensitivity(
                scenario_results, price_range, base_annual_price=base_annual_price
            )
        except ValueError as e:
            ws.cell(row=r, column=1).value = (
                f"Price sensitivity analysis could not be computed: {e}"
            )
            return

        # ── Column headers (8 visible + 1 hidden WTP helper) ──────────────────
        hdrs = [
            "% of Base Price",
            "Lenacapavir Price (€/year)",
            "ΔCost vs Oral PrEP (€)",
            "ΔQALY",
            "ICER (€/QALY)",
            f"NMB at WTP (€)",
            "Cost per Infection Averted (€)",
            "Cost-Effective at WTP",
        ]
        for ci, h in enumerate(hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)

        # Hidden col 9: zero-NMB reference series for chart
        wtp_hdr_cell = ws.cell(row=r, column=9)
        wtp_hdr_cell.value = "NMB = 0"
        wtp_hdr_cell.font  = _font(bold=True, size=9)

        hdr_row = r
        r += 1

        # ── Data rows ──────────────────────────────────────────────────────────
        data_start = r
        for _, row_data in ps_df.iterrows():
            price    = row_data["price"]
            pct_base = price / base_annual_price  # fraction (e.g. 0.5 for 50%)
            is_base  = abs(pct_base - 1.0) < 0.001

            # Col 1: % of base price (stored as decimal, formatted as %)
            pct_cell = ws.cell(row=r, column=1)
            pct_cell.value         = pct_base
            pct_cell.number_format = "0%"
            pct_cell.font          = _font(size=10)
            pct_cell.alignment     = _center()
            pct_cell.border        = _border_thin()

            # Col 2: absolute annual price
            _style_value(ws.cell(row=r, column=2), price, fmt=u'\u20ac#,##0', blue=True)

            # Col 3: ΔCost
            _style_value(ws.cell(row=r, column=3), row_data["delta_cost"],
                         fmt=u'\u20ac#,##0;(\u20ac#,##0);-')

            # Col 4: ΔQALY (constant across rows — price does not affect epi)
            _style_value(ws.cell(row=r, column=4), row_data["delta_qaly"],
                         fmt="#,##0.0;(#,##0.0);-")

            # Col 5: ICER with NMB-based colour coding
            icer_cell = ws.cell(row=r, column=5)
            _style_icer_cell(icer_cell, row_data["icer"], self.wtp,
                             nmb=row_data.get("nmb"))

            # Col 6: NMB — Excel formula so it recalculates if WTP cell is changed
            # Col layout: C=ΔCost, D=ΔQALY → NMB = WTP × D - C
            nmb_cell = ws.cell(row=r, column=6)
            nmb_cell.value         = f"={wtp_ref}*D{r}-C{r}"
            nmb_cell.font          = _font(size=10)
            nmb_cell.number_format = u'\u20ac#,##0;(\u20ac#,##0);-'
            nmb_cell.alignment     = _right()
            nmb_cell.border        = _border_thin()

            # Col 7: Cost per infection averted
            cpia = row_data.get("cost_per_infection_averted")
            if cpia is not None:
                _style_value(ws.cell(row=r, column=7), cpia,
                             fmt=u'\u20ac#,##0;(\u20ac#,##0);-')
            else:
                na_cell = ws.cell(row=r, column=7)
                na_cell.value     = "N/A"
                na_cell.font      = _font(size=10, color=GREY_TEXT)
                na_cell.alignment = _center()
                na_cell.border    = _border_thin()

            # Col 8: Cost-Effective flag
            ce_cell = ws.cell(row=r, column=8)
            ce_cell.value     = "Yes" if row_data["cost_effective"] else "No"
            ce_cell.font      = _font(size=10, bold=True)
            ce_cell.alignment = _center()
            ce_cell.border    = _border_thin()
            ce_cell.fill      = _fill(FILL_CE_YES if row_data["cost_effective"]
                                      else FILL_DOMINATED)

            # Col 9 (hidden): zero-NMB reference series for chart
            wtp_ref_cell = ws.cell(row=r, column=9)
            wtp_ref_cell.value = 0

            # Highlight base-case row (100%) in amber
            if is_base:
                for ci in range(1, 9):
                    ws.cell(row=r, column=ci).fill = _fill("FFF2CC")

            r += 1
        data_end = r - 1

        # Hide the zero-line helper column (chart data only).
        # Note: openpyxl preserves data references from hidden columns in chart
        # series, and Excel renders hidden-column data in charts by default.
        # Verified: both series are retained after save/reload.
        ws.column_dimensions["I"].hidden = True

        # ── Footnote ──────────────────────────────────────────────────────────
        r += 1
        note_cell = ws.cell(row=r, column=1)
        note_cell.value = (
            "Note: ΔQALY is constant across all rows — price affects costs only, "
            "not transmission dynamics or health outcomes.  "
            "Highlighted row (amber) = base-case price (100%)."
        )
        note_cell.font      = _font(size=9, italic=True, color=GREY_TEXT)
        note_cell.alignment = Alignment(wrap_text=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 2

        # ── ZIN Three-Tier Threshold Price Summary ────────────────────────────
        # Call find_threshold_price() once per ZIN WTP tier and present results
        # in a compact table.  Defensive handling covers both None cases:
        #   (a) never CE — NMB ≤ 0 even at zero drug cost
        #   (b) always CE — no finite threshold in [0, 10× base] range
        # The two cases are distinguished by probing NMB at zero price so that
        # a peer reviewer running alternative scenarios sees correct output.
        _merge_header(ws, r, 1, 4,
                      "ZIN Threshold Price Analysis — Three WTP Tiers",
                      navy=True)
        ws.row_dimensions[r].height = 18
        r += 1

        tp_hdrs = [
            "ZIN WTP Tier (\u20ac/QALY)",
            "Threshold Annual Lena Product Price",
            "Cost-Effective at Base Case Price?",
            "Interpretation",
        ]
        for ci, h in enumerate(tp_hdrs, 1):
            _style_col_header(ws.cell(row=r, column=ci), h)
        r += 1

        for wtp_tier in _ZIN_WTP_TIERS:
            status = self._threshold_status(
                scenario_results,
                wtp_tier,
                base_annual_price,
            )
            threshold = status["threshold"]
            is_ce_at_base = bool(status["is_ce_at_base"])

            # Col 1: WTP tier
            tier_cell = ws.cell(row=r, column=1)
            tier_cell.value     = f"\u20ac{wtp_tier:,.0f}/QALY"
            tier_cell.font      = _font(size=10)
            tier_cell.alignment = _center()
            tier_cell.border    = _border_thin()

            # Col 2: Threshold price — distinguish the two None cases
            price_cell = ws.cell(row=r, column=2)
            if threshold is not None:
                _style_value(price_cell, threshold, fmt=u'\u20ac#,##0', blue=True)
            else:
                if is_ce_at_base:
                    # Case (b): always CE — threshold above 10× base price
                    price_cell.value     = "N/A \u2014 cost-effective above search range (>10\u00d7 base)"
                else:
                    # Case (a): never CE even at zero drug cost
                    price_cell.value     = "N/A \u2014 not cost-effective at any price"
                price_cell.font      = _font(size=10, color=GREY_TEXT)
                price_cell.alignment = _center()
                price_cell.border    = _border_thin()

            # Col 3: Yes / No with fill
            ce_cell = ws.cell(row=r, column=3)
            ce_cell.value     = "Yes" if is_ce_at_base else "No"
            ce_cell.font      = _font(size=10, bold=True)
            ce_cell.alignment = _center()
            ce_cell.border    = _border_thin()
            ce_cell.fill      = _fill(FILL_CE_YES if is_ce_at_base else FILL_DOMINATED)

            # Col 4: dynamic interpretation, including negative-QALY threshold cases
            note_cell = ws.cell(row=r, column=4)
            note_cell.value = status.get("note", "")
            note_cell.font = _font(size=9, color=GREY_TEXT)
            note_cell.alignment = Alignment(wrap_text=True)
            note_cell.border = _border_thin()

            r += 1

        r += 2  # spacer before chart

        # ── Threshold Analysis Chart: NMB vs Price with Zero-NMB Reference ────
        # Standard one-way sensitivity analysis diagram (Drummond et al. 2015,
        # Sanders et al. 2016 ISPOR CHEERS). NMB is preferred for the chart
        # because it remains numeric in all four CEA quadrants, while ICER can be
        # labelled Dominant/Dominated and therefore render as gaps.
        #
        # Legend fix: titles_from_data=True with set_categories() causes Excel to
        # render each category (price) label as a separate legend entry, producing
        # ~20 legend items instead of 2.  We point data refs at the data rows only
        # (no header row) and set each series title explicitly via SeriesLabel.
        if data_end >= data_start:
            chart = LineChart()
            chart.title              = "Threshold Analysis — NMB vs Lenacapavir Annual Price"
            chart.style              = 2
            chart.y_axis.title       = "Net Monetary Benefit (\u20ac billions)"
            chart.y_axis.numFmt      = '\u20ac#,##0,,,"B"'
            chart.y_axis.number_format = '\u20ac#,##0,,,"B"'
            chart.x_axis.title       = "Annual Price (€/year)"
            chart.x_axis.numFmt      = "€#,##0"
            chart.x_axis.number_format = "€#,##0"
            chart.width              = 28
            chart.height             = 18

            # Series 1: NMB — data rows only (no header row), explicit title
            nmb_ref = Reference(ws, min_col=6, max_col=6,
                                min_row=data_start, max_row=data_end)
            _add_styled_series(chart, nmb_ref,
                               name=f"NMB at WTP €{self.wtp:,.0f}/QALY",
                               color="4472C4",
                               width_emu=25000,
                               dash="solid")

            # Series 2: zero-NMB reference (col 9, hidden) — dashed dark red
            zero_series_ref = Reference(ws, min_col=9, max_col=9,
                                        min_row=data_start, max_row=data_end)
            _add_styled_series(chart, zero_series_ref,
                               name="NMB = 0 threshold",
                               color=_WTP_COLOR,
                               width_emu=18000,
                               dash="lgDash")

            # X-axis categories: annual price values (col 2)
            price_ref = Reference(ws, min_col=2,
                                  min_row=data_start, max_row=data_end)
            chart.set_categories(price_ref)

            ws.add_chart(chart, f"A{r}")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 9 — Simulation Baseline: Key Years
    # ─────────────────────────────────────────────────────────────────────────

    def _write_price_sensitivity_multi(self, ws, ps_scenarios: list):
        """Write one long-format price sensitivity sheet for split runtime configs."""
        _set_col_widths(ws, {
            1: 42, 2: 18, 3: 18, 4: 18, 5: 24, 6: 24,
            7: 16, 8: 24, 9: 24, 10: 30, 11: 18,
        })
        r = 1
        _merge_header(ws, r, 1, 11, "Price Sensitivity Analysis — All Lenacapavir Scenarios")
        ws.row_dimensions[r].height = 22
        r += 1

        ws.cell(row=r, column=1).value = "WTP Threshold (€/QALY)"
        ws.cell(row=r, column=1).font = _font(bold=True, size=10)
        _style_value(ws.cell(row=r, column=2), self.wtp, fmt=u'\u20ac#,##0', blue=True)
        r += 2

        headers = [
            "Scenario", "Type", "Comparator", "Intervention", "% of Base Price",
            "Annual Lena Price", "ΔQALY", "ΔCost", "ICER", "NMB",
            "Cost-Effective",
        ]
        for c, header in enumerate(headers, 1):
            _style_col_header(ws.cell(row=r, column=c), header)
        r += 1

        def deep_get(config: dict, dot_path: str):
            node = config
            for key in dot_path.split("."):
                if not isinstance(node, dict) or key not in node:
                    return None
                node = node[key]
            return node

        for scenario in ps_scenarios:
            ps_cfg = scenario.get("price_sensitivity", {})
            input_ref = ps_cfg.get(
                "input_ref",
                "prevention_costs.arm_specific.lenacapavir.injection_subsequent",
            )
            base_annual_price = extract_value(deep_get(self.hta_config, input_ref))
            if not base_annual_price:
                continue
            pct_values = [float(pct) for pct in ps_cfg.get("percent_values", [])]
            price_range = [
                round(pct * base_annual_price, 2)
                for pct in pct_values
            ]
            if not price_range:
                continue
            try:
                ps_df = self.comparator.price_sensitivity(
                    self.all_results[scenario["name"]],
                    price_range,
                    base_annual_price=base_annual_price,
                    comparator_arm=scenario.get("comparator_arm", "oral_prep"),
                    intervention_arm=scenario.get("intervention_arm"),
                )
            except ValueError:
                continue

            for pct, (_, row) in zip(pct_values, ps_df.iterrows()):
                values = [
                    scenario.get("label", scenario["name"]),
                    scenario.get("comparison_type", ""),
                    scenario.get("comparator_arm", ""),
                    scenario.get("intervention_arm", ""),
                    pct,
                    row["price"],
                    row["delta_qaly"],
                    row["delta_cost"],
                    row["icer"],
                    row["nmb"],
                    "Yes" if row["cost_effective"] else "No",
                ]
                for c, value in enumerate(values, 1):
                    cell = ws.cell(row=r, column=c)
                    cell.value = value
                    if c == 5:
                        _style_value(cell, value, fmt="0%")
                    elif c in (6, 8, 10):
                        _style_value(cell, value, fmt=u'\u20ac#,##0')
                    elif c == 7:
                        _style_value(cell, value, fmt="#,##0.0")
                    elif c == 9 and not isinstance(value, str):
                        _style_value(cell, value, fmt=u'\u20ac#,##0')
                    else:
                        _style_label(cell, str(value) if value is not None else "")
                if row["cost_effective"]:
                    ws.cell(row=r, column=11).fill = _fill(FILL_CE_YES)
                else:
                    ws.cell(row=r, column=11).fill = _fill(FILL_CE_NO)
                r += 1

        ws.freeze_panes = "A4"
        ws.auto_filter.ref = f"A4:K{max(r - 1, 4)}"

    def _write_baseline_summary(self):
        ws = self.wb.create_sheet("Baseline Key Years")
        ws.sheet_view.showGridLines = False

        baseline_df = self._load_baseline()
        if baseline_df is None:
            ws.cell(row=1, column=1).value = "Baseline CSV not found."
            return
        # Baseline CSVs are wide and may arrive with a fragmented pandas block
        # layout.  Consolidate once before adding the derived year column.
        baseline_df = baseline_df.copy()

        # V12.5.2: fixed two aggregation bugs —
        #   (1) round() misassigned Q4 (t=YYYY.75) to next year; use floor() instead.
        #   (2) drop_duplicates picked only Q1; replaced with groupby().agg() that
        #       sums flow columns and takes end-of-year snapshot for stock/rate columns.
        #   Consistent with _quarterly_to_annual and _aggregate_timeseries which both
        #   use floor/int. Fully timestep-agnostic (no hardcoded dt).
        import math as _math
        baseline_df["year_int"] = baseline_df["year"].apply(lambda y: int(_math.floor(y)))
        coverage = baseline_year_coverage(baseline_df)

        _FLOW_COLS = [
            "new_infections", "hiv_deaths", "background_deaths", "childhood_deaths",
            "births", "net_migration",
            "prep_oral_initiations", "prep_lena_initiations",
            "prep_oral_dropouts",    "prep_lena_dropouts",
        ]
        _STOCK_COLS = [
            "total_population", "sexually_active_population",
            "prevalence", "incidence_rate",
            "prep_coverage_Oral_PrEP", "prep_coverage_Lenacapavir",
        ]

        yr_df = baseline_df[baseline_df["year_int"].isin(self.baseline_summary_years)].copy()

        _agg_dict = {}
        for _col in _FLOW_COLS:
            if _col in yr_df.columns:
                _agg_dict[_col] = "sum"
        for _col in _STOCK_COLS:
            if _col in yr_df.columns:
                _agg_dict[_col] = "last"   # last step in year = end-of-year snapshot

        summary = (yr_df.sort_values("year")
                        .groupby("year_int", sort=True)
                        .agg(_agg_dict)
                        .reset_index())
        if not coverage.empty:
            summary = summary.merge(coverage, on="year_int", how="left")
            summary["complete_year"] = summary["complete_year"].map(
                {True: "Complete", False: "Partial"}
            )

        display_cols = [
            ("year_int",               "Year"),
            ("total_population",       "Total Population"),
            ("sexually_active_population", "Sexually Active Pop"),
            ("new_infections",         "New Infections"),
            ("prevalence",             "HIV Prevalence"),
            ("incidence_rate",         "Incidence Rate"),
            ("hiv_deaths",             "HIV Deaths"),
            ("prep_coverage_Oral_PrEP","Oral PrEP Coverage"),
            ("prep_coverage_Lenacapavir","Lena Coverage"),
            ("timesteps_in_year",      "Timesteps"),
            ("expected_timesteps",     "Expected Timesteps"),
            ("complete_year",          "Year Coverage"),
        ]
        display_cols = [(k, v) for k, v in display_cols if k in summary.columns or k == "year_int"]

        _set_col_widths(ws, {i+1: 20 for i in range(len(display_cols))})

        r = 1
        _merge_header(ws, r, 1, len(display_cols),
                      "Simulation Baseline — Key Year Snapshots (Pre-Freeze)")
        ws.row_dimensions[r].height = 22
        r += 1

        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(display_cols))
        note = ws.cell(row=r, column=1)
        note.value = (
            "Model-only diagnostic: these charts show the simulated Dutch epidemic baseline. "
            "They are useful for face-validity review, but they do not yet overlay observed "
            "Dutch surveillance/validation data. Rows marked Partial do not contain a full "
            "annual flow total and should not be compared directly with annual surveillance "
            "targets."
        )
        note.font = _font(size=9, color=GREY_TEXT, italic=True)
        note.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 30
        r += 1

        for ci, (_, hdr) in enumerate(display_cols, 1):
            _style_col_header(ws.cell(row=r, column=ci), hdr)
        hdr_row = r
        r += 1

        data_start = r
        for _, row_data in summary.iterrows():
            for ci, (col_key, _) in enumerate(display_cols, 1):
                val = row_data.get(col_key, "")
                if col_key in ("prevalence", "incidence_rate",
                               "prep_coverage_Oral_PrEP", "prep_coverage_Lenacapavir"):
                    _style_value(ws.cell(row=r, column=ci), val, fmt="0.000%"
                                 if col_key in ("prevalence","incidence_rate") else "0.0%")
                elif col_key == "year_int":
                    c = ws.cell(row=r, column=ci)
                    c.value = int(val)
                    c.font  = _font(bold=True, size=10)
                    c.alignment = _center()
                    c.border = _border_thin()
                    c.number_format = "0"
                elif col_key == "complete_year":
                    _style_label(ws.cell(row=r, column=ci), str(val))
                else:
                    _style_value(ws.cell(row=r, column=ci), val, fmt="#,##0")
            r += 1
        data_end = r - 1
        r += 2

        # ── Charts ─────────────────────────────────────────────────────────────
        _merge_header(ws, r, 1, len(display_cols), "Charts", navy=False)
        ws.row_dimensions[r].height = 20
        r += 1

        chart_defs = [
            ("HIV Incidence Rate 1970–2025", "Incidence Rate", 6,  "0.000%"),
            ("HIV Prevalence 1970–2025",     "Prevalence",     5,  "0.00%"),
            ("New Infections per Year",      "Infections",     4,  "#,##0"),
        ]
        anchors = [f"A{r}", f"A{r + 20}", f"A{r + 40}"]

        for (title, y_title, col_idx, y_fmt), anchor in zip(chart_defs, anchors):
            if col_idx > len(display_cols):
                continue
            chart = LineChart() if "Infections" not in title else BarChart()
            chart.title    = title
            chart.style    = 10
            chart.y_axis.title = y_title
            if y_fmt:
                chart.y_axis.numFmt = y_fmt
                chart.y_axis.number_format = y_fmt
            chart.x_axis.title = "Year"
            chart.x_axis.numFmt = "0"
            chart.x_axis.number_format = "0"
            chart.width    = 24
            chart.height   = 12

            data_ref = Reference(ws, min_col=col_idx, max_col=col_idx,
                                 min_row=hdr_row, max_row=data_end)
            chart.add_data(data_ref, titles_from_data=True)
            cats = Reference(ws, min_col=1, min_row=data_start, max_row=data_end)
            chart.set_categories(cats)
            ws.add_chart(chart, anchor)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 10 — Simulation Baseline: Full Data
    # ─────────────────────────────────────────────────────────────────────────

    # Column names that should display as whole numbers (no decimal places).
    # These are count/stock/flow fields that are always integers in the model.
    _INTEGER_COL_NAMES = {
        "step",
        "new_infections", "hiv_deaths", "background_deaths", "childhood_deaths",
        "total_population", "sexually_active_population", "childhood_population",
        "births", "sexual_debuts", "continuous_debuts", "flush_debuts", "net_migration",
        "msm_total", "msm_susceptible", "msm_eligible_prep",
        "msm_high_risk", "msm_high_risk_susceptible", "msm_high_risk_on_prep",
        "msm_hiv_pos", "msm_on_oral", "msm_on_lena",
        "prep_oral_initiations", "prep_lena_initiations",
        "prep_oral_dropouts", "prep_lena_dropouts",
        "new_infections_msm", "new_infections_msm_on_prep",
    }

    @staticmethod
    def _col_number_format(col_name: str) -> str:
        """
        Return an Excel number format string for the given column name.

        Rules (in priority order):
          1. prep_coverage_* columns  → percentage with 4 d.p.
          2. prevalence / incidence   → 6 significant decimal places
          3. year                     → 2 d.p. (shows quarters as .00/.25/.50/.75)
          4. integer count columns    → comma-separated whole number
          5. everything else (float)  → 2 d.p. fallback
        """
        if col_name.startswith("prep_coverage"):
            return "0.0000%"
        if col_name in ("prevalence", "incidence_rate"):
            return "0.000000"
        if col_name == "year":
            return "0.00"
        if col_name in HTAReportWriter._INTEGER_COL_NAMES:
            return "#,##0"
        return "#,##0.00"

    def _write_full_data_sheet(self, ws, df: pd.DataFrame, title: str) -> None:
        """
        Populate *ws* with a full quarterly record table from *df*.

        Shared by _write_baseline_full (historical baseline) and
        _write_arm_full_data (per-arm evaluation records).
        Applies column-aware number formatting so integer counts show as
        whole numbers, rates show appropriate decimal places, and the
        step/year columns are not cluttered with trailing zeros.
        """
        ws.sheet_view.showGridLines = False

        _merge_header(ws, 1, 1, len(df.columns), title)
        ws.row_dimensions[1].height = 22

        # Header row
        for ci, col_name in enumerate(df.columns, 1):
            _style_col_header(ws.cell(row=2, column=ci),
                              col_name.replace("_", " ").title())
            ws.column_dimensions[get_column_letter(ci)].width = 20

        # Pre-build per-column format strings (index-aligned, 1-based)
        col_fmts = {
            ci: self._col_number_format(col_name)
            for ci, col_name in enumerate(df.columns, 1)
        }

        # Data rows
        for ri, (_, row_data) in enumerate(df.iterrows(), 3):
            shade = LIGHT_GREY if ri % 2 == 0 else WHITE
            for ci, (col_name, val) in enumerate(
                zip(df.columns, row_data), 1
            ):
                cell = ws.cell(row=ri, column=ci)
                # Convert integer-class floats to int so Excel stores them
                # as a true integer (avoids hidden decimal storage)
                if col_name in self._INTEGER_COL_NAMES and isinstance(val, float):
                    val = int(round(val))
                cell.value = val
                cell.font      = _font(size=9)
                cell.alignment = _right()
                cell.fill      = _fill(shade)
                cell.number_format = col_fmts[ci]

    def _write_baseline_full(self):
        ws = self.wb.create_sheet("Baseline Full Data")

        baseline_df = self._load_baseline()
        if baseline_df is None:
            ws.cell(row=1, column=1).value = "Baseline CSV not found."
            return

        self._write_full_data_sheet(
            ws, baseline_df,
            "Simulation Baseline — Full Quarterly Record (1970 → Freeze Year)"
        )

    def _scenario_tab_suffix(self, scenario_key: str) -> str:
        """Return a short, Excel-safe suffix for scenario-specific tabs."""
        if scenario_key in SCENARIO_TAB_SUFFIXES:
            return SCENARIO_TAB_SUFFIXES[scenario_key]
        return str(scenario_key).replace("scenario_", "").replace("_", " ")[:12].title()

    def _scenario_label(self, scenario_key: str) -> str:
        """Resolve a human-readable scenario label from hta_config when possible."""
        for sc in self.hta_config.get("analysis_scenarios", []):
            if sc.get("name") == scenario_key:
                return SCENARIO_LABELS.get(
                    scenario_key,
                    f"{scenario_key} — {sc.get('label', scenario_key)}"
                )
        return SCENARIO_LABELS.get(scenario_key, scenario_key)

    @staticmethod
    def _arm_timeseries_prefix(arm_name: str) -> str:
        """Return the key prefix used by _aggregate_timeseries for one arm."""
        return "lena" if arm_name == "lenacapavir" else arm_name

    def _scenario_timeseries_arms(
        self, scenario_results: Dict[str, ScenarioResult]
    ) -> List[str]:
        """Return reportable arms present in a scenario, in workbook order."""
        return self._scenario_arm_order(scenario_results)

    def _scenario_arm_order(
        self, scenario_results: Dict[str, ScenarioResult]
    ) -> List[str]:
        """Return all scenario arms without silently dropping new split arms."""
        known = [
            arm_name for arm_name in self.ARM_ORDER
            if arm_name in scenario_results
        ]
        extra = [
            arm_name for arm_name in scenario_results
            if arm_name not in self.ARM_ORDER
        ]
        return known + extra

    def _all_report_arm_order(self) -> List[str]:
        """Return workbook arm order plus any arms present in loaded results."""
        arms = list(self.ARM_ORDER)
        for scenario_results in self.all_results.values():
            for arm_name in scenario_results:
                if arm_name not in arms:
                    arms.append(arm_name)
        return [
            arm_name for arm_name in arms
            if any(arm_name in results for results in self.all_results.values())
        ]

    def _scenario_full_data_tab_name(self, scenario_key: str) -> str:
        """Return a stable full-data tab name, avoiding B-targeted/expansion collisions."""
        suffix = self._scenario_tab_suffix(scenario_key)
        if (
            scenario_key == "scenario_b_expansion"
            and "scenario_b_targeted" in self.all_results
        ):
            suffix = "B Expansion"
        return f"Full Data {suffix}"

    def _write_scenario_full_data(self, scenario_key: str, tab_name: str) -> None:
        """
        Write full quarterly records for all arms in one scenario-specific tab.

        The output is keyed by scenario x arm. This avoids the previous
        first-available-scenario lookup, which could show Scenario A Lenacapavir
        records while the user was reviewing Scenario B CEA results.
        """
        ws = self.wb.create_sheet(tab_name[:31])
        scenario_results = self.all_results.get(scenario_key, {})
        rows = []

        for arm_name in self._scenario_arm_order(scenario_results):
            res = scenario_results.get(arm_name)
            if not res or not res.simulation_records:
                continue
            for rec in res.simulation_records:
                rows.append({
                    "scenario": scenario_key,
                    "arm": arm_name,
                    **rec,
                })

        if not rows:
            ws.cell(row=1, column=1).value = (
                f"No simulation records available for scenario: {scenario_key}"
            )
            return

        df = pd.DataFrame(rows)
        self._write_full_data_sheet(
            ws, df,
            f"{self._scenario_label(scenario_key)} — Full Quarterly Records by Arm"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _load_baseline(self) -> Optional[pd.DataFrame]:
        if not self.baseline_csv or not os.path.exists(self.baseline_csv):
            return None
        return pd.read_csv(self.baseline_csv)

    def _aggregate_timeseries(
        self, scenario_results: Dict[str, ScenarioResult]
    ) -> Optional[Dict[int, dict]]:
        """
        Aggregate per-arm quarterly timestep records into a dict keyed by year (int).
        Returns cumulative discounted costs and QALYs, plus annual infections.
        """
        arm_dfs = {}
        for arm_name in self._scenario_arm_order(scenario_results):
            res = scenario_results.get(arm_name)
            if res and res.timestep_records:
                df = pd.DataFrame(res.timestep_records)
                df["year_int"] = df["year"].apply(lambda y: int(y))
                arm_dfs[arm_name] = df

        if not arm_dfs:
            return None

        # Build per-year dict — get all years from union of all arms
        all_years = sorted(set().union(*[set(df["year_int"]) for df in arm_dfs.values()]))

        annual = {}
        for arm_name, df in arm_dfs.items():
            prefix = self._arm_timeseries_prefix(arm_name)
            cumul_cost = 0.0
            cumul_qaly = 0.0
            for yr in all_years:
                yr_rows = df[df["year_int"] == yr]
                if yr not in annual:
                    annual[yr] = {}
                annual[yr][f"{prefix}_cumul_cost"] = (
                    cumul_cost + yr_rows["step_costs_disc"].sum()
                )
                annual[yr][f"{prefix}_cumul_qaly"] = (
                    cumul_qaly + yr_rows["step_qalys_disc"].sum()
                )
                annual[yr][f"{prefix}_infections"] = int(yr_rows["step_new_infections"].sum())
                cumul_cost = annual[yr][f"{prefix}_cumul_cost"]
                cumul_qaly = annual[yr][f"{prefix}_cumul_qaly"]

        return annual

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 11 — PrEP Population Dynamics  (V11.8.2)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _quarterly_to_annual(records: List[dict]) -> List[dict]:
        """
        Aggregate per-timestep simulation records (quarterly) into annual rows.

        Aggregation rules:
          - Stock variables: start-of-year = first quarterly step; HCC mean =
            arithmetic mean of all steps in that year; end-of-year = last step.
          - Flow variables (initiations, dropouts, infections): sum of all steps.

        Returns list of dicts, one per integer calendar year, sorted ascending.
        """
        if not records:
            return []

        import math

        df = pd.DataFrame(records)
        df["year_int"] = df["year"].apply(lambda y: int(math.floor(y)))

        STOCK_COLS = [
            "total_population",
            "msm_total", "msm_susceptible", "msm_eligible_prep",
            "msm_high_risk", "msm_high_risk_susceptible", "msm_high_risk_on_prep",
            "msm_hiv_pos", "msm_on_oral", "msm_on_lena",
        ]
        FLOW_COLS = [
            "prep_oral_initiations", "prep_lena_initiations",
            "prep_oral_dropouts", "prep_lena_dropouts",
            "new_infections", "new_infections_msm", "new_infections_msm_on_prep",
        ]

        annual_rows = []
        for yr, group in df.groupby("year_int"):
            group_sorted = group.sort_values("year")
            row = {"year": int(yr)}

            for col in STOCK_COLS:
                if col in group.columns:
                    vals = group_sorted[col].values
                    row[f"{col}_start"] = int(vals[0])
                    row[f"{col}_hcc"]   = float(vals.mean())
                    row[f"{col}_end"]   = int(vals[-1])
                else:
                    row[f"{col}_start"] = 0
                    row[f"{col}_hcc"]   = 0.0
                    row[f"{col}_end"]   = 0

            for col in FLOW_COLS:
                if col in group.columns:
                    row[col] = int(group[col].sum())
                else:
                    row[col] = 0

            annual_rows.append(row)

        return sorted(annual_rows, key=lambda r: r["year"])

    def _write_prep_dynamics_tab_legacy_unused(self):
        """
        Tab 11: PrEP Population Dynamics — annual per-arm PrEP coverage and
        turnover metrics with half-cycle corrected (HCC) paired columns.
        V11.8.2.
        """
        ws = self.wb.create_sheet("PrEP Population Dynamics")
        ws.sheet_view.showGridLines = False

        # Colour constants for coverage thresholds (RIVM NL PrEP targets)
        FILL_COV_LOW  = "FFC7CE"   # <10%   red
        FILL_COV_MID  = "FFEB9C"   # 10-30% amber
        FILL_COV_HIGH = "C6EFCE"   # >30%   green

        # Column widths
        ws.column_dimensions["A"].width = 8   # Year
        for col_idx in range(2, 80):
            ws.column_dimensions[get_column_letter(col_idx)].width = 14

        # ── Title ────────────────────────────────────────────────────────────
        title_cell = ws.cell(row=1, column=1)
        title_cell.value = "PrEP Population Dynamics — Annual Summary by Scenario and Arm"
        title_cell.font = _font(bold=True, size=13, color=WHITE)
        title_cell.fill = _fill(NAVY)
        title_cell.alignment = _left()
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=39)
        ws.row_dimensions[1].height = 22

        subtitle = ws.cell(row=2, column=1)
        subtitle.value = (
            "Stock columns shown as paired: Start of Year | HCC Mean (arithmetic mean of 4 quarterly steps). "
            "Flow columns are annual sums. Evaluation horizon only."
        )
        subtitle.font = _font(size=9, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=39)

        current_row = 4

        for scenario_key, scenario_results in self.all_results.items():
            _merge_header(
                ws, current_row, 1, 31,
                self._scenario_label(scenario_key),
                navy=True,
            )
            ws.row_dimensions[current_row].height = 20
            current_row += 1

            for arm_name in self.ARM_ORDER:
                res = scenario_results.get(arm_name)
                arm_sim_records = (
                    res.simulation_records
                    if res and res.simulation_records else []
                )

                annual = self._quarterly_to_annual(arm_sim_records)

                # ── Arm section header ────────────────────────────────────────
                arm_label = ARM_LABELS.get(arm_name, arm_name)
                arm_hdr = ws.cell(row=current_row, column=1)
                arm_hdr.value = f"ARM: {arm_label}"
                arm_hdr.font = _font(bold=True, size=11, color=WHITE)
                arm_hdr.fill = _fill("2E75B6")
                arm_hdr.alignment = _left()
                ws.merge_cells(
                    start_row=current_row, start_column=1,
                    end_row=current_row, end_column=39
                )
                ws.row_dimensions[current_row].height = 18
                current_row += 1

            # ── Group header row 1 (category labels, merged) ──────────────────
            groups = [
                (1,  1,  "Year"),
                (2,  3,  "Total Population"),
                (4,  7,  "MSM Population"),
                (8,  11, "PrEP Users — MSM"),
                (12, 15, "PrEP Users — End of Year"),
                (16, 19, "Annual Flows"),
                (20, 23, "Net Change"),
                (24, 29, "Coverage (%)"),
                (30, 31, "CEA Metrics"),
            ]
            for (start_c, end_c, label) in groups:
                cell = ws.cell(row=current_row, column=start_c)
                if start_c == end_c:
                    cell.value = label
                    cell.font = _font(bold=True, size=9)
                    cell.fill = _fill(MID_GREY)
                    cell.alignment = _center()
                    cell.border = _border_thin()
                else:
                    cell.value = label
                    cell.font = _font(bold=True, size=9)
                    cell.fill = _fill(MID_GREY)
                    cell.alignment = _center()
                    cell.border = _border_thin()
                    ws.merge_cells(
                        start_row=current_row, start_column=start_c,
                        end_row=current_row, end_column=end_c
                    )
            current_row += 1

            # ── Column header row ─────────────────────────────────────────────
            col_headers = [
                "Year",
                # Total Population (cols 2-3) — includes all age groups
                "Total Pop\n(Start)", "Total Pop\n(HCC)",
                # MSM Population (cols 4-7)
                "Total MSM\n(Start)", "Total MSM\n(HCC)",
                "HIV-Neg MSM\n(Start)", "HIV-Neg MSM\n(HCC)",
                # PrEP Users at start (cols 8-11)
                "Oral PrEP\nUsers (Start)", "Oral PrEP\nUsers (HCC)",
                "Lena Users\n(Start)", "Lena Users\n(HCC)",
                # PrEP Users at end (cols 12-15)
                "Oral PrEP\nUsers (End)", "Lena Users\n(End)",
                "Any PrEP\n(End)", "MSM Eligible\n(Start)",
                # Annual flows (cols 16-19)
                "New Oral\nInitiations", "New Lena\nInitiations",
                "Oral\nDropouts", "Lena\nDropouts",
                # Net change (cols 20-23)
                "Net Oral\nChange", "Net Lena\nChange",
                "High-Risk MSM\n(Start)", "High-Risk MSM\non PrEP (Start)",
                # Coverage cols (24-29)
                "Cov: Eligible\nMSM (Start)", "Cov: Eligible\nMSM (HCC)",
                "Cov: Total\nMSM (Start)", "Cov: Total\nMSM (HCC)",
                "Cov: High-Risk\nMSM (Start)", "Cov: High-Risk\nMSM (HCC)",
                # CEA metrics (30-31)
                "Persistence\nRate (%)", "PrEP Turnover\nRatio",
            ]
            for c_idx, hdr in enumerate(col_headers, start=1):
                cell = ws.cell(row=current_row, column=c_idx)
                _style_col_header(cell, hdr)
                ws.row_dimensions[current_row].height = 36
            current_row += 1

            # ── Data rows ─────────────────────────────────────────────────────
            if not annual:
                # No simulation records: show placeholder row
                cell = ws.cell(row=current_row, column=1)
                cell.value = "No simulation records available"
                cell.font = _font(size=9, color=GREY_TEXT, italic=True)
                ws.merge_cells(
                    start_row=current_row, start_column=1,
                    end_row=current_row, end_column=31
                )
                current_row += 1
            else:
                for arow in annual:
                    yr = arow["year"]
                    total_pop_start = arow.get("total_population_start", 0)
                    total_pop_hcc   = arow.get("total_population_hcc", 0.0)
                    oral_start = arow.get("msm_on_oral_start", 0)
                    oral_hcc   = arow.get("msm_on_oral_hcc", 0.0)
                    oral_end   = arow.get("msm_on_oral_end", 0)
                    lena_start = arow.get("msm_on_lena_start", 0)
                    lena_hcc   = arow.get("msm_on_lena_hcc", 0.0)
                    lena_end   = arow.get("msm_on_lena_end", 0)
                    any_end    = oral_end + lena_end
                    msm_total_start = arow.get("msm_total_start", 0)
                    msm_total_hcc   = arow.get("msm_total_hcc", 0.0)
                    msm_susc_start  = arow.get("msm_susceptible_start", 0)
                    msm_susc_hcc    = arow.get("msm_susceptible_hcc", 0.0)
                    msm_elig_start  = arow.get("msm_eligible_prep_start", 0)
                    msm_hr_start    = arow.get("msm_high_risk_start", 0)
                    msm_hr_on_prep  = arow.get("msm_high_risk_on_prep_start", 0)
                    oral_init  = arow.get("prep_oral_initiations", 0)
                    lena_init  = arow.get("prep_lena_initiations", 0)
                    oral_drop  = arow.get("prep_oral_dropouts", 0)
                    lena_drop  = arow.get("prep_lena_dropouts", 0)
                    net_oral   = oral_init - oral_drop
                    net_lena   = lena_init - lena_drop

                    # Coverage percentages (Start and HCC)
                    any_start = oral_start + lena_start
                    any_hcc   = oral_hcc + lena_hcc
                    cov_elig_start = any_start / msm_elig_start  if msm_elig_start > 0 else None
                    cov_elig_hcc   = any_hcc   / max(arow.get("msm_eligible_prep_hcc", 0.0), 1e-9) if msm_elig_start > 0 else None
                    cov_total_start = any_start / msm_total_start if msm_total_start > 0 else None
                    cov_total_hcc   = any_hcc   / msm_total_hcc   if msm_total_hcc > 0 else None
                    # V11.8.5: numerator = high-risk MSM on PrEP (not total MSM on PrEP)
                    msm_hr_on_prep_hcc = arow.get("msm_high_risk_on_prep_hcc", 0.0)
                    cov_hr_start = msm_hr_on_prep / msm_hr_start if msm_hr_start > 0 else None
                    # High-risk HCC denominator
                    msm_hr_hcc = arow.get("msm_high_risk_hcc", 0.0)
                    cov_hr_hcc = msm_hr_on_prep_hcc / msm_hr_hcc if msm_hr_hcc > 0 else None

                    # CEA metrics
                    mean_users = (oral_start + lena_start + oral_end + lena_end) / 2
                    total_drop = oral_drop + lena_drop
                    persistence = (1 - total_drop / mean_users) if mean_users > 0 else None
                    total_init  = oral_init + lena_init
                    turnover = total_init / total_drop if total_drop > 0 else None

                    row_vals = [
                        yr,
                        total_pop_start, total_pop_hcc,
                        msm_total_start, msm_total_hcc,
                        msm_susc_start,  msm_susc_hcc,
                        oral_start, oral_hcc,
                        lena_start, lena_hcc,
                        oral_end, lena_end, any_end, msm_elig_start,
                        oral_init, lena_init, oral_drop, lena_drop,
                        net_oral, net_lena, msm_hr_start, msm_hr_on_prep,
                        cov_elig_start,  cov_elig_hcc,
                        cov_total_start, cov_total_hcc,
                        cov_hr_start,    cov_hr_hcc,
                        persistence, turnover,
                    ]

                    bg = LIGHT_GREY if annual.index(arow) % 2 == 0 else WHITE
                    for c_idx, val in enumerate(row_vals, start=1):
                        cell = ws.cell(row=current_row, column=c_idx)
                        cell.font = _font(size=10)
                        cell.border = _border_thin()
                        # Number formats
                        if c_idx == 1:
                            # Year
                            cell.value = val
                            cell.alignment = _center()
                            cell.fill = _fill(MID_GREY)
                            cell.font = _font(bold=True, size=10)
                        elif c_idx in (3, 5, 7, 9, 11):
                            # HCC mean columns — float (Total Pop HCC=3, MSM HCC=5, HIV-neg HCC=7, Oral HCC=9, Lena HCC=11)
                            cell.value = round(val, 1) if val is not None else None
                            cell.number_format = "#,##0.0"
                            cell.alignment = _right()
                            cell.fill = _fill(bg)
                        elif isinstance(val, float) and val is not None and c_idx >= 24 and c_idx <= 29:
                            # Coverage % columns (shifted +2: now 24-29)
                            cell.value = val
                            cell.number_format = "0.0%"
                            cell.alignment = _right()
                            # Conditional colour: <10% red, 10-30% amber, >30% green
                            if val < 0.10:
                                cell.fill = _fill(FILL_COV_LOW)
                            elif val < 0.30:
                                cell.fill = _fill(FILL_COV_MID)
                            else:
                                cell.fill = _fill(FILL_COV_HIGH)
                        elif c_idx == 30 and val is not None:
                            # Persistence rate (shifted +2: was 28)
                            cell.value = val
                            cell.number_format = "0.0%"
                            cell.alignment = _right()
                            cell.fill = _fill(FILL_COV_LOW if val < 0.80 else FILL_COV_HIGH)
                        elif c_idx == 31 and val is not None:
                            # Turnover ratio (shifted +2: was 29)
                            cell.value = round(val, 2)
                            cell.number_format = "0.00"
                            cell.alignment = _right()
                            cell.fill = _fill(
                                FILL_COV_HIGH if val > 1.0 else
                                FILL_COV_MID  if abs(val - 1.0) < 0.05 else
                                FILL_COV_LOW
                            )
                        elif val is None:
                            cell.value = "—"
                            cell.alignment = _center()
                            cell.fill = _fill(bg)
                        else:
                            cell.value = int(val) if isinstance(val, float) and val == int(val) else val
                            cell.number_format = "#,##0"
                            cell.alignment = _right()
                            cell.fill = _fill(bg)
                    current_row += 1

            # blank separator between arms
            current_row += 2

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 12 — Incidence by Subgroup  (V11.8.3)
    # ─────────────────────────────────────────────────────────────────────────

    def _write_incidence_subgroup_tab_legacy_unused(self):
        """
        Tab 12: Incidence by Subgroup — annual per-arm new infections stratified
        by PrEP status, MSM incidence rates, prevalence, and NNT.
        V11.8.3.
        """
        ws = self.wb.create_sheet("Incidence by Subgroup")
        ws.sheet_view.showGridLines = False

        FILL_AVERTED_POS = "C6EFCE"   # green — infections prevented
        FILL_AVERTED_NEG = "FFC7CE"   # red   — infections increased

        ws.column_dimensions["A"].width = 8
        for col_idx in range(2, 20):
            ws.column_dimensions[get_column_letter(col_idx)].width = 15

        # ── Title ────────────────────────────────────────────────────────────
        title_cell = ws.cell(row=1, column=1)
        title_cell.value = "Incidence by Subgroup — Annual Summary by Arm"
        title_cell.font = _font(bold=True, size=13, color=WHITE)
        title_cell.fill = _fill(NAVY)
        title_cell.alignment = _left()
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=14)
        ws.row_dimensions[1].height = 22

        subtitle = ws.cell(row=2, column=1)
        subtitle.value = (
            "Incidence rates per 100 person-years. HCC mean denominators used. "
            "'Infections averted' = no_prep arm − this arm (blank if no_prep unavailable). "
            "NNT = 1 / (off-PrEP rate − on-PrEP rate) per 100 PY."
        )
        subtitle.font = _font(size=9, color=GREY_TEXT, italic=True)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=14)

        current_row = 4

        # Pre-build no_prep annual data for infections-averted column
        no_prep_annual_by_year: dict = {}
        for scenario_results in self.all_results.values():
            res = scenario_results.get("no_prep")
            if res and res.simulation_records:
                for arow in self._quarterly_to_annual(res.simulation_records):
                    no_prep_annual_by_year[arow["year"]] = arow
                break

        for arm_name in self.ARM_ORDER:
            arm_sim_records = []
            for scenario_results in self.all_results.values():
                res = scenario_results.get(arm_name)
                if res and res.simulation_records:
                    arm_sim_records = res.simulation_records
                    break

            annual = self._quarterly_to_annual(arm_sim_records)

            # ── Arm section header ────────────────────────────────────────────
            arm_label = ARM_LABELS.get(arm_name, arm_name)
            arm_hdr = ws.cell(row=current_row, column=1)
            arm_hdr.value = f"ARM: {arm_label}"
            arm_hdr.font = _font(bold=True, size=11, color=WHITE)
            arm_hdr.fill = _fill("2E75B6")
            arm_hdr.alignment = _left()
            ws.merge_cells(
                start_row=current_row, start_column=1,
                end_row=current_row, end_column=14
            )
            ws.row_dimensions[current_row].height = 18
            current_row += 1

            # ── Column headers ─────────────────────────────────────────────────
            col_headers = [
                "Year",
                "New Inf\n(All Pop.)",
                "New Inf\n— MSM",
                "New Inf\n— MSM on PrEP",
                "New Inf\n— MSM off PrEP",
                "MSM Incidence\n(per 100 PY)",
                "PrEP MSM\nIncidence\n(per 100 PY)",
                "Non-PrEP MSM\nIncidence\n(per 100 PY)",
                "MSM HIV\nPrevalence (%)",
                "Cumul. MSM\nInfections",
                "Infections\nAverted vs\nNo-PrEP",
                "Cumul.\nInfections\nAverted",
                "NNT\n(per 100 PY)",
            ]
            for c_idx, hdr in enumerate(col_headers, start=1):
                cell = ws.cell(row=current_row, column=c_idx)
                _style_col_header(cell, hdr)
                ws.row_dimensions[current_row].height = 48
            current_row += 1

            # ── Data rows ─────────────────────────────────────────────────────
            if not annual:
                cell = ws.cell(row=current_row, column=1)
                cell.value = "No simulation records available"
                cell.font = _font(size=9, color=GREY_TEXT, italic=True)
                ws.merge_cells(
                    start_row=current_row, start_column=1,
                    end_row=current_row, end_column=13
                )
                current_row += 1
            else:
                cumul_msm_inf = 0
                cumul_averted = 0

                for arow in annual:
                    yr = arow["year"]
                    new_inf_all    = arow.get("new_infections", 0)
                    new_inf_msm    = arow.get("new_infections_msm", 0)
                    new_inf_on_prep = arow.get("new_infections_msm_on_prep", 0)
                    new_inf_off_prep = max(new_inf_msm - new_inf_on_prep, 0)

                    # Incidence rates using HCC denominators (per 100 PY)
                    msm_susc_hcc   = arow.get("msm_susceptible_hcc", 0.0)
                    any_prep_hcc   = arow.get("msm_on_oral_hcc", 0.0) + arow.get("msm_on_lena_hcc", 0.0)
                    off_prep_hcc   = max(msm_susc_hcc - any_prep_hcc, 0.0)

                    incidence_msm      = (new_inf_msm / msm_susc_hcc * 100)    if msm_susc_hcc > 0  else None
                    incidence_on_prep  = (new_inf_on_prep / any_prep_hcc * 100) if any_prep_hcc > 0 else None
                    incidence_off_prep = (new_inf_off_prep / off_prep_hcc * 100) if off_prep_hcc > 0 else None

                    # Prevalence
                    msm_total_hcc = arow.get("msm_total_hcc", 0.0)
                    msm_hiv_pos   = arow.get("msm_hiv_pos_hcc", 0.0)
                    prevalence    = (msm_hiv_pos / msm_total_hcc * 100) if msm_total_hcc > 0 else None

                    # Cumulative
                    cumul_msm_inf += new_inf_msm

                    # Infections averted vs no_prep (blank if unavailable or same arm)
                    if arm_name == "no_prep":
                        averted = None
                    elif yr in no_prep_annual_by_year:
                        np_row = no_prep_annual_by_year[yr]
                        averted = np_row.get("new_infections_msm", 0) - new_inf_msm
                    else:
                        averted = None

                    if averted is not None:
                        cumul_averted += averted
                        cumul_averted_disp = cumul_averted
                    else:
                        cumul_averted_disp = None

                    # NNT — blank for no_prep or if rates unavailable
                    if (arm_name == "no_prep" or
                            incidence_on_prep is None or
                            incidence_off_prep is None or
                            incidence_off_prep <= incidence_on_prep):
                        nnt = None
                    else:
                        nnt_raw = 100 / (incidence_off_prep - incidence_on_prep)
                        nnt = round(nnt_raw)

                    row_vals = [
                        yr,
                        new_inf_all,
                        new_inf_msm,
                        new_inf_on_prep,
                        new_inf_off_prep,
                        incidence_msm,
                        incidence_on_prep,
                        incidence_off_prep,
                        prevalence,
                        cumul_msm_inf,
                        averted,
                        cumul_averted_disp,
                        nnt,
                    ]

                    bg = LIGHT_GREY if annual.index(arow) % 2 == 0 else WHITE
                    for c_idx, val in enumerate(row_vals, start=1):
                        cell = ws.cell(row=current_row, column=c_idx)
                        cell.font = _font(size=10)
                        cell.border = _border_thin()

                        if c_idx == 1:
                            cell.value = val
                            cell.alignment = _center()
                            cell.fill = _fill(MID_GREY)
                            cell.font = _font(bold=True, size=10)

                        elif c_idx in (6, 7, 8):
                            # Incidence rates — 2 dp
                            if val is None:
                                cell.value = "—"
                                cell.alignment = _center()
                                cell.fill = _fill(bg)
                            else:
                                cell.value = round(val, 2)
                                cell.number_format = "0.00"
                                cell.alignment = _right()
                                cell.fill = _fill(bg)

                        elif c_idx == 9:
                            # Prevalence — 2 dp with %
                            if val is None:
                                cell.value = "—"
                                cell.alignment = _center()
                                cell.fill = _fill(bg)
                            else:
                                cell.value = round(val, 2)
                                cell.number_format = "0.00"
                                cell.alignment = _right()
                                cell.fill = _fill(bg)

                        elif c_idx == 11:
                            # Infections averted — conditional colour
                            if val is None:
                                cell.value = "—"
                                cell.alignment = _center()
                                cell.fill = _fill(bg)
                            else:
                                cell.value = int(val)
                                cell.number_format = "#,##0"
                                cell.alignment = _right()
                                cell.fill = _fill(
                                    FILL_AVERTED_POS if val >= 0 else FILL_AVERTED_NEG
                                )

                        elif c_idx == 12:
                            # Cumulative infections averted
                            if val is None:
                                cell.value = "—"
                                cell.alignment = _center()
                                cell.fill = _fill(bg)
                            else:
                                cell.value = int(val)
                                cell.number_format = "#,##0"
                                cell.alignment = _right()
                                cell.fill = _fill(
                                    FILL_AVERTED_POS if val >= 0 else FILL_AVERTED_NEG
                                )

                        elif c_idx == 13:
                            # NNT
                            if val is None:
                                cell.value = "—"
                                cell.alignment = _center()
                                cell.fill = _fill(bg)
                            else:
                                cell.value = val
                                cell.number_format = "#,##0"
                                cell.alignment = _right()
                                cell.fill = _fill(bg)

                        elif val is None:
                            cell.value = "—"
                            cell.alignment = _center()
                            cell.fill = _fill(bg)

                        else:
                            cell.value = int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else val
                            cell.number_format = "#,##0"
                            cell.alignment = _right()
                            cell.fill = _fill(bg)

                    current_row += 1

            # blank separator between arms
            current_row += 2

    def _write_prep_dynamics_tab(self):
        """
        Scenario-specific PrEP dynamics table.

        V12.12 replaces the prior arm-only transparency layout. Every annual
        row is keyed by scenario and arm so Scenario B cannot accidentally show
        Scenario A Lenacapavir records.
        """
        ws = self.wb.create_sheet("PrEP Population Dynamics")
        ws.sheet_view.showGridLines = False

        headers = [
            "Scenario", "Arm", "Year",
            "Total Pop Start", "Total Pop HCC",
            "MSM Start", "MSM HCC",
            "HIV-Neg MSM Start", "HIV-Neg MSM HCC",
            "Oral Users Start", "Oral Users HCC", "Oral Users End",
            "Lena Users Start", "Lena Users HCC", "Lena Users End",
            "Any PrEP End", "MSM Eligible Start",
            "New Oral Initiations", "New Lena Initiations",
            "Oral Dropouts", "Lena Dropouts",
            "Net Oral Change", "Net Lena Change",
            "High-Risk MSM Start", "High-Risk MSM on PrEP Start",
            "Coverage Eligible Start", "Coverage Eligible HCC",
            "Coverage Total MSM Start", "Coverage Total MSM HCC",
            "Coverage High-Risk Start", "Coverage High-Risk HCC",
            "Persistence Rate", "Turnover Ratio",
        ]

        _merge_header(
            ws, 1, 1, len(headers),
            "PrEP Population Dynamics — Annual Summary by Scenario and Arm",
        )
        ws.row_dimensions[1].height = 22
        for ci, header in enumerate(headers, 1):
            _style_col_header(ws.cell(row=2, column=ci), header)
            ws.column_dimensions[get_column_letter(ci)].width = 16

        r = 3
        for scenario_key, scenario_results in self.all_results.items():
            scenario_label = self._scenario_label(scenario_key)
            for arm_name in self._scenario_arm_order(scenario_results):
                res = scenario_results.get(arm_name)
                records = res.simulation_records if res and res.simulation_records else []
                for arow in self._quarterly_to_annual(records):
                    oral_start = arow.get("msm_on_oral_start", 0)
                    oral_hcc = arow.get("msm_on_oral_hcc", 0.0)
                    oral_end = arow.get("msm_on_oral_end", 0)
                    lena_start = arow.get("msm_on_lena_start", 0)
                    lena_hcc = arow.get("msm_on_lena_hcc", 0.0)
                    lena_end = arow.get("msm_on_lena_end", 0)
                    any_start = oral_start + lena_start
                    any_hcc = oral_hcc + lena_hcc
                    any_end = oral_end + lena_end

                    msm_elig_start = arow.get("msm_eligible_prep_start", 0)
                    msm_elig_hcc = arow.get("msm_eligible_prep_hcc", 0.0)
                    msm_total_start = arow.get("msm_total_start", 0)
                    msm_total_hcc = arow.get("msm_total_hcc", 0.0)
                    msm_hr_start = arow.get("msm_high_risk_start", 0)
                    msm_hr_hcc = arow.get("msm_high_risk_hcc", 0.0)
                    msm_hr_on_start = arow.get("msm_high_risk_on_prep_start", 0)
                    msm_hr_on_hcc = arow.get("msm_high_risk_on_prep_hcc", 0.0)

                    oral_init = arow.get("prep_oral_initiations", 0)
                    lena_init = arow.get("prep_lena_initiations", 0)
                    oral_drop = arow.get("prep_oral_dropouts", 0)
                    lena_drop = arow.get("prep_lena_dropouts", 0)
                    total_drop = oral_drop + lena_drop
                    total_init = oral_init + lena_init
                    mean_users = (any_start + any_end) / 2

                    row_vals = [
                        scenario_label,
                        ARM_LABELS.get(arm_name, arm_name),
                        arow["year"],
                        arow.get("total_population_start", 0),
                        arow.get("total_population_hcc", 0.0),
                        msm_total_start,
                        msm_total_hcc,
                        arow.get("msm_susceptible_start", 0),
                        arow.get("msm_susceptible_hcc", 0.0),
                        oral_start, oral_hcc, oral_end,
                        lena_start, lena_hcc, lena_end,
                        any_end, msm_elig_start,
                        oral_init, lena_init, oral_drop, lena_drop,
                        oral_init - oral_drop, lena_init - lena_drop,
                        msm_hr_start, msm_hr_on_start,
                        any_start / msm_elig_start if msm_elig_start > 0 else None,
                        any_hcc / msm_elig_hcc if msm_elig_hcc > 0 else None,
                        any_start / msm_total_start if msm_total_start > 0 else None,
                        any_hcc / msm_total_hcc if msm_total_hcc > 0 else None,
                        msm_hr_on_start / msm_hr_start if msm_hr_start > 0 else None,
                        msm_hr_on_hcc / msm_hr_hcc if msm_hr_hcc > 0 else None,
                        1 - total_drop / mean_users if mean_users > 0 else None,
                        total_init / total_drop if total_drop > 0 else None,
                    ]

                    shade = LIGHT_GREY if r % 2 == 0 else WHITE
                    for ci, val in enumerate(row_vals, 1):
                        cell = ws.cell(row=r, column=ci)
                        cell.value = "—" if val is None else val
                        cell.font = _font(size=9)
                        cell.border = _border_thin()
                        cell.fill = _fill(shade)
                        if ci in (26, 27, 28, 29, 30, 31, 32):
                            cell.number_format = "0.0%"
                        elif ci == 33:
                            cell.number_format = "0.00"
                        elif ci == 3:
                            cell.number_format = "0"
                        elif ci > 2:
                            cell.number_format = "#,##0.0" if isinstance(val, float) else "#,##0"
                        cell.alignment = _right() if ci > 2 else _left()
                    r += 1

        if r == 3:
            ws.cell(row=3, column=1).value = "No simulation records available"

    def _write_incidence_subgroup_tab(self):
        """
        Scenario-specific incidence table.

        V12.12 keys no-PrEP references by the same scenario as the arm being
        reported. This prevents infections-averted calculations from using a
        no-PrEP record from another scenario.
        """
        ws = self.wb.create_sheet("Incidence by Subgroup")
        ws.sheet_view.showGridLines = False

        headers = [
            "Scenario", "Arm", "Year",
            "New Infections All", "New Infections MSM",
            "New Infections MSM on PrEP", "New Infections MSM off PrEP",
            "MSM Incidence per 100 PY", "PrEP MSM Incidence per 100 PY",
            "Non-PrEP MSM Incidence per 100 PY", "MSM HIV Prevalence (%)",
            "Cumulative MSM Infections", "Infections Averted vs No PrEP",
            "Cumulative Infections Averted", "NNT per 100 PY",
        ]

        _merge_header(
            ws, 1, 1, len(headers),
            "Incidence by Subgroup — Annual Summary by Scenario and Arm",
        )
        ws.row_dimensions[1].height = 22
        for ci, header in enumerate(headers, 1):
            _style_col_header(ws.cell(row=2, column=ci), header)
            ws.column_dimensions[get_column_letter(ci)].width = 18

        r = 3
        for scenario_key, scenario_results in self.all_results.items():
            no_prep_res = scenario_results.get("no_prep")
            no_prep_annual = {
                row["year"]: row
                for row in self._quarterly_to_annual(
                    no_prep_res.simulation_records
                    if no_prep_res and no_prep_res.simulation_records else []
                )
            }
            scenario_label = self._scenario_label(scenario_key)

            for arm_name in self._scenario_arm_order(scenario_results):
                res = scenario_results.get(arm_name)
                records = res.simulation_records if res and res.simulation_records else []
                cumul_msm_inf = 0
                cumul_averted = 0

                for arow in self._quarterly_to_annual(records):
                    yr = arow["year"]
                    new_inf_all = arow.get("new_infections", 0)
                    new_inf_msm = arow.get("new_infections_msm", 0)
                    new_inf_on_prep = arow.get("new_infections_msm_on_prep", 0)
                    new_inf_off_prep = max(new_inf_msm - new_inf_on_prep, 0)
                    cumul_msm_inf += new_inf_msm

                    msm_susc_hcc = arow.get("msm_susceptible_hcc", 0.0)
                    any_prep_hcc = (
                        arow.get("msm_on_oral_hcc", 0.0)
                        + arow.get("msm_on_lena_hcc", 0.0)
                    )
                    off_prep_hcc = max(msm_susc_hcc - any_prep_hcc, 0.0)
                    incidence_msm = (
                        new_inf_msm / msm_susc_hcc * 100
                        if msm_susc_hcc > 0 else None
                    )
                    incidence_on = (
                        new_inf_on_prep / any_prep_hcc * 100
                        if any_prep_hcc > 0 else None
                    )
                    incidence_off = (
                        new_inf_off_prep / off_prep_hcc * 100
                        if off_prep_hcc > 0 else None
                    )
                    msm_total_hcc = arow.get("msm_total_hcc", 0.0)
                    prevalence = (
                        arow.get("msm_hiv_pos_hcc", 0.0) / msm_total_hcc * 100
                        if msm_total_hcc > 0 else None
                    )

                    if arm_name == "no_prep" or yr not in no_prep_annual:
                        averted = None
                    else:
                        averted = (
                            no_prep_annual[yr].get("new_infections_msm", 0)
                            - new_inf_msm
                        )
                        cumul_averted += averted

                    nnt = None
                    if (
                        arm_name != "no_prep"
                        and incidence_on is not None
                        and incidence_off is not None
                        and incidence_off > incidence_on
                    ):
                        nnt = round(100 / (incidence_off - incidence_on))

                    row_vals = [
                        scenario_label,
                        ARM_LABELS.get(arm_name, arm_name),
                        yr,
                        new_inf_all, new_inf_msm,
                        new_inf_on_prep, new_inf_off_prep,
                        incidence_msm, incidence_on, incidence_off,
                        prevalence,
                        cumul_msm_inf,
                        averted,
                        cumul_averted if averted is not None else None,
                        nnt,
                    ]

                    shade = LIGHT_GREY if r % 2 == 0 else WHITE
                    for ci, val in enumerate(row_vals, 1):
                        cell = ws.cell(row=r, column=ci)
                        cell.value = "—" if val is None else val
                        cell.font = _font(size=9)
                        cell.border = _border_thin()
                        cell.fill = _fill(shade)
                        if ci in (8, 9, 10, 11):
                            cell.number_format = "0.00"
                        elif ci == 3:
                            cell.number_format = "0"
                        elif ci > 2:
                            cell.number_format = "#,##0"
                        cell.alignment = _right() if ci > 2 else _left()
                    r += 1

        if r == 3:
            ws.cell(row=3, column=1).value = "No simulation records available"

    @staticmethod
    def _format_distribution(dist: dict) -> str:
        """Format a distribution block as a readable string."""
        dtype = dist.get("type", "")
        if not dtype or dtype == "fixed":
            return "Fixed"
        if dtype == "beta":
            return f"Beta(α={dist.get('alpha','?')}, β={dist.get('beta','?')})"
        if dtype == "gamma":
            return f"Gamma(mean={dist.get('mean','?')}, SE={dist.get('se','?')})"
        return dtype.title()
