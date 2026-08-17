"""
calibration_output_writer.py — Demographic Calibration Report Writer

Produces a structured 8-tab Excel workbook from the output of run_calibration.py.
Styled consistently with hta_report.py.

Tabs:
  1. Summary           — overall pass/fail, run metadata, MAPE headline
  2. Guide             — checks explanation, colour coding, glossary
  3. Config_Audit      — config year_specific vs WB source, discrepancies
  4. Pop_Trajectory    — model vs HMD total population, tiered bands, chart
  5. Age_Structure     — age-sex distribution at reference years, Spearman + chi-sq
  6. Migration_Coverage — WB year coverage table, gap flags
  7. Vital_Stats       — annual births, deaths (HIV/background/childhood), net migration
  8. Pop_Forecast      — model vs CBS forecast 2026+, 67%/95% CI bands, chart

V12.5 — new module
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.series import SeriesLabel


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette  (matches hta_report.py)
# ─────────────────────────────────────────────────────────────────────────────
NAVY       = "1F3864"
WHITE      = "FFFFFF"
LIGHT_GREY = "F2F2F2"
MID_GREY   = "D9D9D9"

# Band colours
GREEN_FILL  = "C6EFCE"   # ±5%   — good
AMBER_FILL  = "FFEB9C"   # ±10%  — monitor
RED_FILL    = "FFC7CE"   # >±10% — review
PASS_FILL   = "C6EFCE"
FAIL_FILL   = "FFC7CE"
INFO_FILL   = "DDEBF7"   # light blue — informational


# ─────────────────────────────────────────────────────────────────────────────
# Style helpers
# ─────────────────────────────────────────────────────────────────────────────

def _font(bold=False, size=11, color="000000", italic=False):
    return Font(name="Arial", bold=bold, size=size, color=color, italic=italic)

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=False)

def _center():
    return Alignment(horizontal="center", vertical="center")

def _border_thin():
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)

def _set_col_widths(ws, widths: Dict[int, float]):
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

def _header_row(ws, row: int, labels: List[str], start_col: int = 1):
    """Write a styled navy header row."""
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=start_col + i, value=label)
        c.font = _font(bold=True, color=WHITE, size=10)
        c.fill = _fill(NAVY)
        c.alignment = _center()
        c.border = _border_thin()

def _section_title(ws, row: int, col: int, text: str, n_cols: int = 6):
    ws.merge_cells(
        start_row=row, start_column=col,
        end_row=row, end_column=col + n_cols - 1
    )
    c = ws.cell(row=row, column=col, value=text)
    c.font = _font(bold=True, size=12, color=WHITE)
    c.fill = _fill(NAVY)
    c.alignment = _left()

def _data_cell(ws, row: int, col: int, value, fmt=None, bg=None, bold=False, center=False):
    c = ws.cell(row=row, column=col, value=value)
    c.font = _font(bold=bold)
    c.border = _border_thin()
    c.alignment = _center() if center else _left()
    if fmt:
        c.number_format = fmt
    if bg:
        c.fill = _fill(bg)
    return c

def _band_fill(pct_dev: float) -> str:
    """Return fill colour based on percentage deviation."""
    abs_dev = abs(pct_dev)
    if abs_dev <= 5.0:
        return GREEN_FILL
    elif abs_dev <= 10.0:
        return AMBER_FILL
    else:
        return RED_FILL

def _band_label(pct_dev: float) -> str:
    abs_dev = abs(pct_dev)
    if abs_dev <= 5.0:
        return "GREEN"
    elif abs_dev <= 10.0:
        return "AMBER"
    else:
        return "RED"


# ─────────────────────────────────────────────────────────────────────────────
# CalibrationReportWriter
# ─────────────────────────────────────────────────────────────────────────────

class CalibrationReportWriter:
    """
    Writes the 8-tab calibration Excel report.

    Parameters
    ----------
    results : dict
        Output from run_calibration.run_all_checks(), containing keys:
          "config_audit"       — list of dicts per year
          "pop_trajectory"     — list of dicts per year
          "age_structure"      — dict of reference year results
          "migration_coverage" — list of dicts per WB year
          "pop_forecast"       — list of dicts per forecast year (Check 5)
          "meta"               — run metadata dict
    output_path : str
        Where to write the .xlsx file.
    """

    def __init__(self, results: dict, output_path: str):
        self.results = results
        self.output_path = output_path
        self.wb = Workbook()
        self.wb.remove(self.wb.active)   # remove default sheet

    def write(self) -> str:
        """Write all tabs and save. Returns the output path."""
        self._write_summary()
        self._write_guide()
        self._write_config_audit()
        self._write_pop_trajectory()
        self._write_age_structure()
        self._write_migration_coverage()
        self._write_vital_stats()
        self._write_pop_forecast()

        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        self.wb.save(self.output_path)
        return self.output_path


    # ─────────────────────────────────────────────────────────────────────────
    # Tab 2: Guide
    # ─────────────────────────────────────────────────────────────────────────

    def _write_guide(self):
        ws = self.wb.create_sheet("Guide")
        _set_col_widths(ws, {1: 26, 2: 90})
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 90

        def _title(row, text):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row=row, column=1, value=text)
            c.font = _font(bold=True, size=13, color=WHITE)
            c.fill = _fill(NAVY)
            c.alignment = _left()

        def _subheading(row, text):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row=row, column=1, value=text)
            c.font = _font(bold=True, size=11, color=WHITE)
            c.fill = _fill("2E5E8E")
            c.alignment = _left()

        def _label_text(row, label, text, label_bg=None):
            a = ws.cell(row=row, column=1, value=label)
            a.font = _font(bold=True, size=10)
            a.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            if label_bg:
                a.fill = _fill(label_bg)
            b = ws.cell(row=row, column=2, value=text)
            b.font = _font(size=10)
            b.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            ws.row_dimensions[row].height = max(30, len(text) // 5)

        def _blank(row):
            ws.row_dimensions[row].height = 6

        def _colour_row(row, label, hex_colour, meaning):
            a = ws.cell(row=row, column=1, value=label)
            a.font = _font(bold=True, size=10)
            a.fill = _fill(hex_colour)
            a.alignment = _center()
            a.border = _border_thin()
            b = ws.cell(row=row, column=2, value=meaning)
            b.font = _font(size=10)
            b.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            b.border = _border_thin()
            ws.row_dimensions[row].height = 22

        r = 1

        # ── Purpose ──────────────────────────────────────────────────────────
        _title(r, "About This Report"); r += 1
        _blank(r); r += 1
        _label_text(r, "What is this?",
            "This is the Demographic Calibration Report for the HIV Transmission Model "
            "(Netherlands, Lenacapavir PrEP vs Oral PrEP cost-effectiveness analysis). "
            "It checks whether the model's simulated population behaviour is consistent "
            "with independent external reference datasets: the Human Mortality Database "
            "(HMD) for Netherlands population, and World Bank/CBS references for net migration figures."
        ); r += 1
        _blank(r); r += 1
        _label_text(r, "Why does it matter?",
            "A cost-effectiveness model depends on having a realistic population base. "
            "If the model's total population drifts far from historical Netherlands figures, "
            "or if its age distribution is structurally wrong, then estimates of infections "
            "prevented, QALYs gained, and costs incurred will be affected. This report "
            "provides an auditable, reproducible record of how well the demographic layer "
            "is calibrated — suitable for inclusion as a supplementary validation check "
            "in a thesis or HTA submission."
        ); r += 1
        _blank(r); r += 1

        # ── Data Sources ─────────────────────────────────────────────────────
        _subheading(r, "Reference Data Sources"); r += 1
        _blank(r); r += 1
        _label_text(r, "HMD — Human Mortality Database",
            "www.mortality.org  |  NLD (Netherlands), single-year-of-age, annual population "
            "counts (Type O = 1 January snapshot). Covers 1950–2024. Used for total "
            "population trajectory (Check 2) and age-sex structure (Check 3). "
            "Accessed: May 2025."
        ); r += 1
        _blank(r); r += 1
        _label_text(r, "Migration Reference — WB/CBS",
            "data.worldbank.org/indicator/SM.POP.NETM?locations=NL  |  "
            "Net migration (arrivals minus departures) for the Netherlands, 1960–2025. "
            "Raw source is World Bank SM.POP.NETM; 2025 is overridden to the CBS "
            "population forecast central value of 100,815. Used for Check 1 "
            "(config audit) and Check 4 (migration coverage)."
        ); r += 1
        _blank(r); r += 1
        _label_text(r, "CBS — Bevolkingsprognose",
            "opendata.cbs.nl/statline  |  CBS StatLine population forecast for the "
            "Netherlands, 2026–2070. Central estimate with 67% and 95% confidence "
            "intervals. Units: millions of persons. Used for Check 5 (population forecast). "
            "Accessed: May 2026."
        ); r += 1
        _blank(r); r += 1

        # ── The Four Checks ───────────────────────────────────────────────────
        _subheading(r, "The Five Checks — What They Test"); r += 1
        _blank(r); r += 1

        _label_text(r, "Check 1 — Config Audit  (tab: Config_Audit)",
            "Compares every net migration value hard-coded in the model config "
            "(demography → migration → year_specific) against the migration reference. "
            "Flags any year where the config value differs from the reference by more than 1 "
            "(rounding tolerance). Also lists reference years that are absent from the config but "
            "have significant migration (|net| > 500). "
            "STATUS: PASS = no discrepancies found."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Check 2 — Population Trajectory  (tab: Pop_Trajectory)",
            "Runs the full simulation over the calibration window and compares the modelled "
            "total population (adults + children) at the end of each calendar year against "
            "the HMD Netherlands figure for that year. Deviations are expressed as a "
            "percentage of the HMD reference value and assigned to one of three bands "
            "(see Colour Coding section below). The MAPE (Mean Absolute Percentage Error) "
            "across all years is the headline summary statistic."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Check 3 — Age-Sex Structure  (tab: Age_Structure)",
            "At 2–3 reference years spaced across the simulation window, the model's adult "
            "population (ages 15+) is broken down by 5-year age bin and sex, then compared "
            "to the same breakdown from HMD. Two statistical tests are run: "
            "  • Spearman rank correlation (ρ): measures whether the model's age bins rank "
            "in the same order as HMD (e.g. are younger adults more common than older adults "
            "in the same way?). Scale-independent — it does not penalise the model for "
            "having a different total population to HMD. Pass threshold: ρ ≥ 0.80. "
            "  • Chi-square goodness-of-fit (χ²): tests whether the model's proportions are "
            "statistically consistent with the HMD distribution as a reference. A very low "
            "p-value (p < 0.05) indicates a meaningful structural difference. Chi-square is "
            "reported for context — it is not used for the PASS/FAIL decision."
        ); r += 1
        ws.row_dimensions[r - 1].height = 90
        _blank(r); r += 1

        _label_text(r, "Check 4 — Migration Coverage  (tab: Migration_Coverage)",
            "Lists every year in the migration reference and shows whether it is "
            "covered in the model config and whether it falls inside the simulation window. "
            "Years outside the simulation window are informational only — they show as "
            "'NO' for In Sim Window but are not flagged as problems unless they fall within "
            "the window AND have a large net migration value (|net| > 5,000)."
        ); r += 1
        _blank(r); r += 1

        _label_text(r, "Check 5 — Population Forecast  (tab: Pop_Forecast)",
            "Compares the model's total population trajectory for years 2026+ against the "
            "CBS (Centraal Bureau voor de Statistiek) central forecast and confidence "
            "intervals. For each overlapping year the model total (converted to millions) "
            "is checked against the 67% and 95% CBS confidence bands. "
            "This is a forward-looking external plausibility check, not a hard calibration "
            "target — it shows whether the model's future population is demographically "
            "reasonable given the national forecast. A FAIL indicates the model's future "
            "population diverges from CBS projections, typically due to population "
            "multiplier settings or structural demographic assumptions that differ from "
            "the CBS projection model. This should be documented as a known limitation if "
            "the model is submitted for academic review. "
            "STATUS: PASS = ≥80% of overlapping forecast years within CBS 95% CI  |  "
            "WARN = 50–79%  |  FAIL = <50%  |  N/A = no overlap or CBS file missing."
        ); r += 1
        ws.row_dimensions[r - 1].height = 90
        _blank(r); r += 1

        # ── Colour Coding ─────────────────────────────────────────────────────
        _subheading(r, "Colour Coding Reference"); r += 1
        _blank(r); r += 1

        _label_text(r, "Population Trajectory bands", "Applied per calendar year in the Pop_Trajectory tab:"); r += 1
        _colour_row(r, "GREEN  (≤ ±5%)",  GREEN_FILL,
            "Model total population is within 5% of the HMD reference value. "
            "Good calibration — no action needed."); r += 1
        _colour_row(r, "AMBER  (±5–10%)", AMBER_FILL,
            "Deviation between 5% and 10%. Worth monitoring, especially if "
            "clustered in a particular period. May reflect simplified birth/mortality "
            "assumptions rather than a model error."); r += 1
        _colour_row(r, "RED    (> ±10%)",  RED_FILL,
            "Deviation exceeds 10%. Flag for review. Likely indicates a "
            "miscalibrated parameter or an unmodelled demographic shock."); r += 1
        _blank(r); r += 1

        _label_text(r, "Check status", "Applied in the Summary tab and Age_Structure tab:"); r += 1
        _colour_row(r, "PASS", PASS_FILL,
            "The check met its threshold. For trajectory: all years in GREEN or AMBER. "
            "For age structure: Spearman ρ ≥ 0.80 for both sexes at all reference years."); r += 1
        _colour_row(r, "WARN", AMBER_FILL,
            "The check passed its primary criterion but flagged something worth reviewing "
            "(e.g. some AMBER years in trajectory, or a borderline ρ value)."); r += 1
        _colour_row(r, "FAIL", FAIL_FILL,
            "The check did not meet its threshold. Does not mean the model is wrong — "
            "it means the specific criterion was not met and the finding should be "
            "interpreted and documented."); r += 1
        _blank(r); r += 1

        # ── Glossary ──────────────────────────────────────────────────────────
        _subheading(r, "Glossary"); r += 1
        _blank(r); r += 1

        glossary = [
            ("MAPE",
             "Mean Absolute Percentage Error. The average of |model − HMD| / HMD × 100% "
             "across all years in the trajectory. A single number summarising how far off "
             "the model's population is on average. 2–3% is excellent for a model of this "
             "type; above 10% warrants investigation."),
            ("Spearman ρ  (rho)",
             "A rank correlation coefficient ranging from −1 to +1. A value of +1 means "
             "the model's age bins rank in exactly the same order as HMD (e.g. the 20–24 "
             "bin is the biggest, then 25–29, etc.). A value near 0 means no relationship. "
             "Negative values mean the model's ranking is inverted relative to HMD. "
             "The threshold ρ ≥ 0.80 is a standard threshold for 'good agreement' in "
             "demographic shape comparisons."),
            ("Chi-square (χ²)",
             "A statistical test of whether two distributions are consistent with each other. "
             "Here it tests whether the model's age-bin proportions could plausibly have "
             "come from the HMD distribution. A p-value below 0.05 indicates a statistically "
             "significant difference in shape. In a large population model, chi-square is "
             "almost always significant — it is reported for context, not as a pass/fail gate."),
            ("Net migration",
             "Arrivals into the Netherlands minus departures from the Netherlands in a given "
             "year. A positive number means more people arrived than left. A negative number "
             "(e.g. 2004–2007) means net emigration. Source: World Bank SM.POP.NETM."),
            ("HMD Type O",
             "Human Mortality Database population counts recorded on 1 January each year "
             "(Type O = 'opening' of the year). Used here rather than 31 December counts "
             "(Type E) because Type O data is available for the full 1950–2024 range for "
             "the Netherlands."),
            ("Reference years (Check 3)",
             "The specific calendar years at which the age-sex structure comparison is made. "
             "These are derived automatically as the 25th, 50th, and 75th percentile of "
             "the simulation window, snapped to the nearest 5-year mark. They change if "
             "you run the calibration runner over a different time window."),
            ("Simulation window",
             "The start and end year used for the internal calibration simulation run. "
             "Read directly from the model config (demography → time → start_year / end_year). "
             "All trajectory and age structure comparisons are made within this window only."),
        ]

        for label, text in glossary:
            _label_text(r, label, text)
            r += 1
            _blank(r); r += 1

        # ── Interpretation notes ──────────────────────────────────────────────
        _subheading(r, "How to Interpret Results — Key Notes"); r += 1
        _blank(r); r += 1

        notes = [
            ("A FAIL on Check 3 is expected",
             "This model is an HIV transmission model calibrated to reproduce epidemic "
             "dynamics in the Dutch MSM and heterosexual population. Its age distribution "
             "is initialised from relative weights and maintains demographic stability — it "
             "is not designed to reproduce the exact Dutch age pyramid at each historical "
             "year. A Check 3 FAIL means the age shape differs from HMD; it should be "
             "documented as a known limitation, not treated as a model error."),
            ("Total population vs. adult model population",
             "HMD provides total Netherlands population (all ages). The model simulates "
             "adults (15+) plus a simplified childhood pool. The trajectory comparison "
             "uses model total (adult + childhood) vs. HMD total. Some discrepancy is "
             "expected because the model's childhood pool uses simplified birth rates."),
            ("Migration 'NO' in sim window column",
             "Years shown as 'NO' in the In Sim Window column are not missing or wrong — "
             "they simply fall outside the calibration window. If the model's production "
             "run extends beyond the calibration window (e.g. to 2035), those years are "
             "still used by the model but are not part of the external validation comparison."),
            ("This report does not validate HIV outcomes",
             "Epidemiological outputs (HIV prevalence, incidence, ART coverage) are not "
             "validated here — there is no external HIV time-series in the validation folder. "
             "This report validates the demographic layer only."),
        ]

        for label, text in notes:
            _label_text(r, label, text)
            r += 1
            _blank(r); r += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1: Summary
    # ─────────────────────────────────────────────────────────────────────────

    def _write_summary(self):
        ws = self.wb.create_sheet("Summary")
        meta = self.results.get("meta", {})
        _set_col_widths(ws, {1: 34, 2: 22, 3: 22, 4: 22})

        r = 1
        _section_title(ws, r, 1, "Demographic Calibration Report — Summary", n_cols=4)
        r += 2

        # Metadata block
        meta_rows = [
            ("Run date",           meta.get("run_date", "—")),
            ("Model config",       meta.get("config_path", "—")),
            ("Simulation window",  meta.get("sim_window", "—")),
            ("HMD source",         "Human Mortality Database — NLD (Type O, Jan 1)"),
            ("WB source",          "World Bank SM.POP.NETM — Netherlands (1960–2025)"),
            ("CBS source",         "CBS StatLine — Bevolkingsprognose 2024–2070"),
            ("HMD file",           meta.get("hmd_path", "—")),
            ("WB file",            meta.get("wb_path", "—")),
            ("CBS file",           meta.get("cbs_path", "—")),
        ]
        for label, val in meta_rows:
            ws.cell(row=r, column=1, value=label).font = _font(bold=True)
            ws.cell(row=r, column=2, value=val).font = _font()
            r += 1
        r += 1

        # Check status table
        _header_row(ws, r, ["Check", "Status", "Detail"], start_col=1)
        r += 1

        checks = [
            ("1 — Config Audit",         self._summary_config_audit()),
            ("2 — Population Trajectory", self._summary_trajectory()),
            ("3 — Age-Sex Structure",     self._summary_age_structure()),
            ("4 — Migration Coverage",    self._summary_coverage()),
            ("5 — Population Forecast",   self._summary_pop_forecast()),
        ]
        for check_name, (status, detail) in checks:
            bg = PASS_FILL if status == "PASS" else (AMBER_FILL if status == "WARN" else FAIL_FILL)
            _data_cell(ws, r, 1, check_name, bold=True)
            _data_cell(ws, r, 2, status, bg=bg, center=True, bold=True)
            _data_cell(ws, r, 3, detail)
            r += 1

        r += 1
        # MAPE headline
        traj = self.results.get("pop_trajectory", [])
        if traj:
            valid = [row for row in traj if row["pct_dev"] is not None]
            mape = sum(abs(row["pct_dev"]) for row in valid) / len(valid) if valid else 0
            sim_win_label = self.results.get("meta", {}).get("sim_window", "—")
            ws.cell(row=r, column=1, value=f"Population MAPE ({sim_win_label})").font = _font(bold=True)
            ws.cell(row=r, column=2, value=f"{mape:.2f}%").font = _font(bold=True)

    def _summary_config_audit(self):
        audit = self.results.get("config_audit", [])
        discrepancies = [a for a in audit if a.get("discrepancy", False)]
        within_window_gaps = [a for a in self.results.get("migration_coverage", [])
                              if a.get("in_window") and not a.get("in_config")]
        if discrepancies:
            return ("FAIL", f"{len(discrepancies)} config/WB discrepancy(ies) found")
        return ("PASS", "All config values match WB source")

    def _summary_trajectory(self):
        traj = self.results.get("pop_trajectory", [])
        if not traj:
            return ("WARN", "No trajectory data")
        valid = [t for t in traj if t["pct_dev"] is not None]
        red   = sum(1 for t in valid if abs(t["pct_dev"]) > 10)
        amber = sum(1 for t in valid if 5 < abs(t["pct_dev"]) <= 10)
        mape  = sum(abs(t["pct_dev"]) for t in valid) / len(valid) if valid else 0
        if red > 0:
            return ("WARN", f"MAPE={mape:.1f}%, {red} year(s) in RED band (>±10%)")
        if amber > 0:
            return ("PASS", f"MAPE={mape:.1f}%, {amber} year(s) in AMBER band (±5–10%)")
        return ("PASS", f"MAPE={mape:.1f}%, all years in GREEN band (≤±5%)")

    def _summary_age_structure(self):
        age_s = self.results.get("age_structure", {})
        if not age_s:
            return ("WARN", "No age structure data")
        failures = []
        for yr, data in sorted(age_s.items()):
            for sex in ("male", "female"):
                rho = data.get(f"spearman_{sex}", 1.0)
                if rho < 0.80:
                    failures.append(f"{yr} {sex} ρ={rho:.3f}")
        if failures:
            return ("FAIL", "; ".join(failures))
        rhos = [data.get(f"spearman_{s}", 1.0)
                for data in age_s.values() for s in ("male", "female")]
        min_rho = min(rhos)
        return ("PASS", f"All Spearman ρ ≥ 0.80 (min={min_rho:.3f})")

    def _summary_coverage(self):
        cov = self.results.get("migration_coverage", [])
        in_window_gaps = [c for c in cov if c.get("in_window") and not c.get("in_config")
                          and abs(c.get("wb_value", 0)) > 5000]
        pre_window_info = [c for c in cov if not c.get("in_window") and not c.get("in_config")
                           and abs(c.get("wb_value", 0)) > 500]
        if in_window_gaps:
            return ("WARN", f"{len(in_window_gaps)} within-window gap(s) with |net|>5,000")
        if pre_window_info:
            return ("PASS", f"{len(pre_window_info)} pre-window informational gap(s) — expected")
        return ("PASS", "Full coverage within simulation window")

    def _summary_pop_forecast(self):
        """Check 5 pass/fail: ≥80% within CBS 95% CI = PASS, 50-79% = WARN, <50% = FAIL."""
        rows = self.results.get("pop_forecast", [])
        if not rows:
            return ("N/A", "No overlapping forecast years (sim ends before 2026) or CBS file missing")
        total      = len(rows)
        within_95  = sum(1 for r in rows if r.get("within_95", False))
        within_67  = sum(1 for r in rows if r.get("within_67", False))
        pct_95     = within_95 / total * 100
        if pct_95 >= 80:
            return ("PASS", f"{within_95}/{total} forecast years within CBS 95% CI ({pct_95:.0f}%)")
        if pct_95 >= 50:
            return ("WARN", f"{within_95}/{total} forecast years within CBS 95% CI ({pct_95:.0f}%)")
        return ("FAIL",
                f"{within_95}/{total} forecast years within CBS 95% CI ({pct_95:.0f}%) — "
                f"model population diverges from CBS forecast")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 2: Config Audit
    # ─────────────────────────────────────────────────────────────────────────

    def _write_config_audit(self):
        ws = self.wb.create_sheet("Config_Audit")
        _set_col_widths(ws, {1: 8, 2: 18, 3: 18, 4: 16, 5: 16})

        r = 1
        _section_title(ws, r, 1, "Check 1 — Config Year-Specific vs Migration Reference", n_cols=5)
        r += 2

        ws.cell(row=r, column=1, value=(
            "Compares every config year_specific migration entry against the migration "
            "reference for the Netherlands. The 2025 reference is CBS 100,815 rather "
            "than raw WB 104,681. Discrepancy threshold: |diff| > 1."
        )).font = _font(italic=True, size=10)
        r += 2

        meta = self.results.get("meta", {})
        sim_start = meta.get("sim_start")
        sim_end   = meta.get("sim_end")
        has_window = sim_start is not None and sim_end is not None

        _set_col_widths(ws, {1: 8, 2: 18, 3: 18, 4: 16, 5: 16, 6: 16})
        _header_row(ws, r, ["Year", "Config Value", "Reference Value", "Difference", "In Sim Window", "Status"])
        r += 1

        for row in self.results.get("config_audit", []):
            yr   = row["year"]
            cfg  = row["config_value"]
            wb   = row["wb_value"]
            diff = row["diff"]
            disc = row.get("discrepancy", False)
            in_win = row.get("in_window", True)  # True if flag absent (legacy compat)

            # Row background: FAIL > pre/post-window > OK
            if disc:
                row_bg = FAIL_FILL
                status = "DISCREPANCY"
            elif not in_win:
                row_bg = INFO_FILL
                status = "OK"
            else:
                row_bg = PASS_FILL
                status = "OK"

            win_label = "YES" if in_win else ("PRE-WINDOW" if (sim_start and yr < sim_start) else "POST-WINDOW")
            win_bg    = None if in_win else INFO_FILL

            _data_cell(ws, r, 1, yr,   center=True, bg=row_bg if not in_win else None)
            _data_cell(ws, r, 2, cfg,  fmt="#,##0", center=True)
            _data_cell(ws, r, 3, wb if wb is not None else "—", fmt="#,##0", center=True)
            _data_cell(ws, r, 4, diff if diff is not None else "—",
                       fmt="+#,##0;-#,##0;0", center=True)
            _data_cell(ws, r, 5, win_label, bg=win_bg, center=True,
                       bold=(not in_win))
            _data_cell(ws, r, 6, status, bg=FAIL_FILL if disc else None,
                       center=True, bold=disc)
            r += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 3: Population Trajectory
    # ─────────────────────────────────────────────────────────────────────────

    def _write_pop_trajectory(self):
        ws = self.wb.create_sheet("Pop_Trajectory")
        _set_col_widths(ws, {1: 8, 2: 18, 3: 18, 4: 16, 5: 12, 6: 12})

        r = 1
        _section_title(ws, r, 1,
            f"Check 2 — Total Population Trajectory: Model vs HMD ({self.results.get('meta', {}).get('sim_window', '')})", n_cols=6)
        r += 2

        ws.cell(row=r, column=1, value=(
            "Model total population (childhood pool + adult tensor) vs HMD Netherlands "
            "(Type O, Jan 1). Bands: GREEN ≤±5%  |  AMBER ±5–10%  |  RED >±10%."
        )).font = _font(italic=True, size=10)
        r += 2

        _header_row(ws, r, ["Year", "HMD Total", "Model Total", "% Deviation", "Band", ""])
        r += 1

        data_start_row = r
        for row in self.results.get("pop_trajectory", []):
            yr  = row["year"]
            hmd = row["hmd_total"]
            mdl = row["model_total"]
            pct = row["pct_dev"]
            _data_cell(ws, r, 1, yr, center=True)
            if pct is None:
                # No HMD reference data for this year
                _data_cell(ws, r, 2, "N/A", center=True)
                ws.cell(row=r, column=2).font = _font(italic=True)
                _data_cell(ws, r, 3, mdl, fmt="#,##0")
                _data_cell(ws, r, 4, "N/A", center=True)
                ws.cell(row=r, column=4).font = _font(italic=True)
                _data_cell(ws, r, 5, "N/A", center=True)
                ws.cell(row=r, column=5).font = _font(italic=True)
            else:
                band = _band_label(pct)
                bg   = _band_fill(pct)
                _data_cell(ws, r, 2, hmd, fmt="#,##0")
                _data_cell(ws, r, 3, mdl, fmt="#,##0")
                _data_cell(ws, r, 4, pct / 100, fmt='0.0%', center=True)
                _data_cell(ws, r, 5, band, bg=bg, center=True, bold=True)
            r += 1
        data_end_row = r - 1

        # Chart
        if data_end_row >= data_start_row:
            chart = LineChart()
            chart.title = "Total Population: Model vs HMD"
            chart.style = 2
            chart.y_axis.title = "Population"
            chart.x_axis.title = "Year"
            chart.height = 14
            chart.width = 22

            hmd_ref = Reference(ws, min_col=2, min_row=data_start_row,
                                 max_row=data_end_row)
            mdl_ref = Reference(ws, min_col=3, min_row=data_start_row,
                                 max_row=data_end_row)
            chart.add_data(hmd_ref)
            chart.add_data(mdl_ref)
            chart.series[0].title = SeriesLabel(v="HMD Reference")
            chart.series[1].title = SeriesLabel(v="Model")
            chart.series[0].graphicalProperties.line.solidFill = "1F3864"
            chart.series[1].graphicalProperties.line.solidFill = "C00000"

            cats = Reference(ws, min_col=1, min_row=data_start_row, max_row=data_end_row)
            chart.set_categories(cats)
            ws.add_chart(chart, f"H{data_start_row}")

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 4: Age Structure
    # ─────────────────────────────────────────────────────────────────────────

    def _write_age_structure(self):
        ws = self.wb.create_sheet("Age_Structure")
        _set_col_widths(ws, {1: 12, 2: 16, 3: 16, 4: 16, 5: 16, 6: 16, 7: 16})

        r = 1
        _section_title(ws, r, 1,
            "Check 3 — Age-Sex Structure at Reference Years (Adult Population 15+)",
            n_cols=7)
        r += 2

        ws.cell(row=r, column=1, value=(
            "Spearman rank correlation (ρ) and chi-square goodness-of-fit between model "
            "and HMD age-bin proportions. Threshold: ρ ≥ 0.80 per sex per reference year."
        )).font = _font(italic=True, size=10)
        r += 2

        for ref_year, data in sorted(self.results.get("age_structure", {}).items()):
            # Sub-header for this reference year
            _section_title(ws, r, 1, f"Reference Year: {ref_year}", n_cols=7)
            r += 1

            # Stats row
            for sex in ("male", "female"):
                rho = data.get(f"spearman_{sex}", None)
                chi2 = data.get(f"chi2_{sex}", None)
                pval = data.get(f"chi2_pval_{sex}", None)
                pass_fail = "PASS" if rho is not None and rho >= 0.80 else "FAIL"
                bg = PASS_FILL if pass_fail == "PASS" else FAIL_FILL

                ws.cell(row=r, column=1, value=sex.capitalize()).font = _font(bold=True)
                ws.cell(row=r, column=2,
                        value=f"Spearman ρ = {rho:.4f}" if rho is not None else "—").font = _font()
                ws.cell(row=r, column=3,
                        value=f"χ² = {chi2:.2f}" if chi2 is not None else "—").font = _font()
                ws.cell(row=r, column=4,
                        value=f"p = {pval:.4f}" if pval is not None else "—").font = _font()
                c = ws.cell(row=r, column=5, value=pass_fail)
                c.font = _font(bold=True)
                c.fill = _fill(bg)
                r += 1

            # Proportion table
            _header_row(ws, r, ["Age Bin",
                                  "HMD Male %", "Model Male %",
                                  "HMD Female %", "Model Female %",
                                  "Male Diff", "Female Diff"])
            r += 1

            bins = data.get("bins", [])
            hmd_m = data.get("hmd_male_props", {})
            mdl_m = data.get("model_male_props", {})
            hmd_f = data.get("hmd_female_props", {})
            mdl_f = data.get("model_female_props", {})

            for b in bins:
                hm = hmd_m.get(b, 0)
                mm = mdl_m.get(b, 0)
                hf = hmd_f.get(b, 0)
                mf = mdl_f.get(b, 0)
                _data_cell(ws, r, 1, b, center=True)
                _data_cell(ws, r, 2, hm, fmt="0.00%", center=True)
                _data_cell(ws, r, 3, mm, fmt="0.00%", center=True)
                _data_cell(ws, r, 4, hf, fmt="0.00%", center=True)
                _data_cell(ws, r, 5, mf, fmt="0.00%", center=True)
                diff_m = mm - hm
                diff_f = mf - hf
                _data_cell(ws, r, 6, diff_m, fmt="+0.00%;-0.00%;0%", center=True,
                           bg=AMBER_FILL if abs(diff_m) > 0.03 else None)
                _data_cell(ws, r, 7, diff_f, fmt="+0.00%;-0.00%;0%", center=True,
                           bg=AMBER_FILL if abs(diff_f) > 0.03 else None)
                r += 1
            r += 2

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 5: Migration Coverage
    # ─────────────────────────────────────────────────────────────────────────

    def _write_migration_coverage(self):
        ws = self.wb.create_sheet("Migration_Coverage")
        _set_col_widths(ws, {1: 8, 2: 18, 3: 14, 4: 18, 5: 16, 6: 22})

        r = 1
        _section_title(ws, r, 1,
            "Check 4 — Migration Reference Coverage (all years 1960–2025)", n_cols=6)
        r += 2

        sim_win = self.results.get("meta", {}).get("sim_window", "unknown")
        ws.cell(row=r, column=1, value=(
            f"All migration reference years. Simulation window = {sim_win}. "
            "The 2025 reference is CBS 100,815; earlier years come from WB SM.POP.NETM. "
            "Flag = within-window gap with |net|>5,000 OR pre-window informational with |net|>500. "
            "Post-window years (beyond sim end) are shown for completeness — not flagged."
        )).font = _font(italic=True, size=10)
        r += 2

        _header_row(ws, r, ["Year", "Reference Value", "In Config", "In Sim Window", "Flag", "Note"])
        r += 1

        for row in self.results.get("migration_coverage", []):
            yr = row["year"]
            wb_val = row["wb_value"]
            in_cfg = row.get("in_config", False)
            in_win = row.get("in_window", False)
            flag = row.get("flag", "")
            note = row.get("note", "")

            if flag:
                bg = AMBER_FILL if in_win else INFO_FILL
            elif not in_cfg:
                bg = INFO_FILL
            else:
                bg = None

            _data_cell(ws, r, 1, yr, center=True)
            _data_cell(ws, r, 2, wb_val, fmt="#,##0", center=True)
            _data_cell(ws, r, 3, "YES" if in_cfg else "NO",
                       bg=None if in_cfg else INFO_FILL, center=True)
            _data_cell(ws, r, 4, "YES" if in_win else "NO", center=True)
            _data_cell(ws, r, 5, flag or "", bg=bg if flag else None, center=True,
                       bold=bool(flag))
            _data_cell(ws, r, 6, note)
            r += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 7: Vital Stats
    # ─────────────────────────────────────────────────────────────────────────

    def _write_vital_stats(self):
        """
        Annual vital statistics derived from the full simulation run:
        births, HIV deaths, background deaths, childhood deaths, total deaths,
        net migration, and end-of-year total population.

        Also shows HMD Total Population reference column (from pop_trajectory results).

        Source: summary DataFrame stored in results["summary_df"] by run_calibration.
        Quarterly steps are aggregated to calendar years (sum for flows, last for stocks).

        Note: HMD births/deaths are NOT available — HMD_NL_Pop.txt contains population
        snapshots only (Type O, Jan 1). Separate HMD Births.txt / Deaths.txt would be
        needed to add those reference series.
        """
        import numpy as np

        ws = self.wb.create_sheet("Vital_Stats")
        _set_col_widths(ws, {
            1: 8,   # Year
            2: 16,  # Model Total Pop
            3: 16,  # HMD Total Pop (reference)
            4: 14,  # Births
            5: 14,  # HIV Deaths
            6: 14,  # BG Deaths
            7: 14,  # Child Deaths
            8: 14,  # Total Deaths
            9: 14,  # Net Migration
        })

        r = 1
        _section_title(ws, r, 1,
            "Vital Statistics — Annual Births, Deaths & Population over Simulation Run",
            n_cols=9)
        r += 2

        ws.cell(row=r, column=1, value=(
            "Quarterly model outputs aggregated to calendar years. "
            "Births, deaths, and net migration are annual totals (sum of quarterly steps). "
            "Total Population is the end-of-year stock. "
            "HMD Reference = Human Mortality Database NLD (Type O, Jan 1 snapshot). "
            "Note: HMD births/deaths not available in current reference file (population snapshots only)."
        )).font = _font(italic=True, size=10)
        r += 2

        summary_df = self.results.get("summary_df")

        if summary_df is None or len(summary_df) == 0:
            ws.cell(row=r, column=1,
                    value="No simulation data available — run calibration to populate this tab.").font = _font(italic=True)
            return

        # ── Build HMD lookup from pop_trajectory ──────────────────────────
        # pop_trajectory already has hmd_total per year
        hmd_by_year = {
            row["year"]: row["hmd_total"]
            for row in self.results.get("pop_trajectory", [])
            if row.get("hmd_total") is not None
        }

        # ── Aggregate quarterly steps → annual ────────────────────────────
        df = summary_df.copy()
        df["year_int"] = df["year"].apply(lambda y: int(np.floor(y + 1e-6)))

        flow_cols  = ["births", "hiv_deaths", "background_deaths",
                      "childhood_deaths", "net_migration"]
        stock_cols = ["total_population"]

        flow_cols  = [c for c in flow_cols  if c in df.columns]
        stock_cols = [c for c in stock_cols if c in df.columns]

        agg_dict = {c: "sum" for c in flow_cols}
        agg_dict.update({c: "last" for c in stock_cols})

        annual = df.groupby("year_int").agg(agg_dict).reset_index()
        annual = annual.rename(columns={"year_int": "year"})
        annual = annual.sort_values("year").reset_index(drop=True)

        death_cols = [c for c in ["hiv_deaths", "background_deaths", "childhood_deaths"]
                      if c in annual.columns]
        annual["total_deaths"] = annual[death_cols].sum(axis=1)

        baseline_pop = int(annual["total_population"].iloc[0]) if "total_population" in annual.columns else None
        baseline_year = int(annual["year"].iloc[0])

        # ── Baseline callout ──────────────────────────────────────────────
        if baseline_pop is not None:
            ws.cell(row=r, column=1, value=f"Baseline ({baseline_year}) population:").font = _font(bold=True)
            c = ws.cell(row=r, column=2, value=baseline_pop)
            c.font = _font(bold=True)
            c.number_format = "#,##0"
            r += 2

        # ── Column headers ────────────────────────────────────────────────
        headers = [
            "Year",
            "Model Total Pop",
            "HMD Ref Pop",
            "Births",
            "HIV Deaths",
            "BG Deaths",
            "Child Deaths",
            "Total Deaths",
            "Net Migration",
        ]
        _header_row(ws, r, headers)
        r += 1

        data_start_row = r

        for _, row_data in annual.iterrows():
            yr      = int(row_data["year"])
            pop     = int(row_data["total_population"])   if "total_population"   in row_data else None
            hmd_pop = hmd_by_year.get(yr)                 # None if outside HMD range
            births  = int(row_data["births"])             if "births"             in row_data else None
            hiv_d   = int(row_data["hiv_deaths"])         if "hiv_deaths"         in row_data else None
            bg_d    = int(row_data["background_deaths"])  if "background_deaths"  in row_data else None
            ch_d    = int(row_data["childhood_deaths"])   if "childhood_deaths"   in row_data else None
            tot_d   = int(row_data["total_deaths"])       if "total_deaths"       in row_data else None
            net_mig = int(row_data["net_migration"])      if "net_migration"      in row_data else None

            # Colour-code HMD ref cell if it exists
            hmd_bg = None
            if hmd_pop is not None and pop is not None:
                pct_dev = abs(pop - hmd_pop) / hmd_pop * 100
                hmd_bg = _band_fill(pct_dev)

            _data_cell(ws, r, 1, yr,      center=True)
            _data_cell(ws, r, 2, pop,     fmt="#,##0")
            _data_cell(ws, r, 3, hmd_pop if hmd_pop is not None else "N/A",
                       fmt="#,##0", bg=hmd_bg, center=hmd_pop is None)
            _data_cell(ws, r, 4, births,  fmt="#,##0",
                       bg=GREEN_FILL if births and births > 0 else None)
            _data_cell(ws, r, 5, hiv_d,   fmt="#,##0",
                       bg=AMBER_FILL if hiv_d and hiv_d > 100 else None)
            _data_cell(ws, r, 6, bg_d,    fmt="#,##0")
            _data_cell(ws, r, 7, ch_d,    fmt="#,##0")
            _data_cell(ws, r, 8, tot_d,   fmt="#,##0", bold=True)
            _data_cell(ws, r, 9, net_mig, fmt="+#,##0;-#,##0;0", center=True)
            r += 1

        data_end_row = r - 1

        # ── Two charts: (1) Population stock  (2) Vital flows ─────────────
        if data_end_row >= data_start_row:
            from openpyxl.chart import LineChart, Reference
            from openpyxl.chart.series import SeriesLabel

            chart_anchor_row = data_end_row + 3

            # Chart 1 — Population: Model vs HMD
            c1 = LineChart()
            c1.title = "Total Population — Model vs HMD Reference"
            c1.style = 2
            c1.y_axis.title = "Population"
            c1.x_axis.title = "Year"
            c1.height = 13
            c1.width  = 24

            model_pop_ref = Reference(ws, min_col=2, min_row=data_start_row, max_row=data_end_row)
            hmd_pop_ref   = Reference(ws, min_col=3, min_row=data_start_row, max_row=data_end_row)
            c1.add_data(model_pop_ref)
            c1.add_data(hmd_pop_ref)
            c1.series[0].title = SeriesLabel(v="Model Total Pop")
            c1.series[1].title = SeriesLabel(v="HMD Reference")
            c1.series[0].graphicalProperties.line.solidFill = "C00000"   # red — model
            c1.series[1].graphicalProperties.line.solidFill = "1F3864"   # navy — HMD
            c1.series[1].graphicalProperties.line.dashDot = "dash"
            cats = Reference(ws, min_col=1, min_row=data_start_row, max_row=data_end_row)
            c1.set_categories(cats)
            ws.add_chart(c1, f"A{chart_anchor_row}")

            # Chart 2 — Vital flows: Births, Total Deaths, Net Migration
            c2 = LineChart()
            c2.title = "Annual Vital Flows — Births, Deaths & Net Migration"
            c2.style = 2
            c2.y_axis.title = "Annual Count"
            c2.x_axis.title = "Year"
            c2.height = 13
            c2.width  = 24

            births_ref  = Reference(ws, min_col=4, min_row=data_start_row, max_row=data_end_row)
            deaths_ref  = Reference(ws, min_col=8, min_row=data_start_row, max_row=data_end_row)
            mig_ref     = Reference(ws, min_col=9, min_row=data_start_row, max_row=data_end_row)
            c2.add_data(births_ref)
            c2.add_data(deaths_ref)
            c2.add_data(mig_ref)
            c2.series[0].title = SeriesLabel(v="Births")
            c2.series[1].title = SeriesLabel(v="Total Deaths")
            c2.series[2].title = SeriesLabel(v="Net Migration")
            c2.series[0].graphicalProperties.line.solidFill = "70AD47"   # green
            c2.series[1].graphicalProperties.line.solidFill = "C00000"   # red
            c2.series[2].graphicalProperties.line.solidFill = "ED7D31"   # orange
            cats2 = Reference(ws, min_col=1, min_row=data_start_row, max_row=data_end_row)
            c2.set_categories(cats2)
            ws.add_chart(c2, f"A{chart_anchor_row + 22}")


    # ─────────────────────────────────────────────────────────────────────────
    # Tab 8: Population Forecast vs CBS
    # ─────────────────────────────────────────────────────────────────────────

    def _write_pop_forecast(self):
        """
        Check 5 — Model total population (2026+) vs CBS central forecast
        and 67%/95% confidence intervals.

        Colour coding per row:
            GREEN  = model within CBS 67% CI
            AMBER  = model within CBS 95% CI but outside 67% CI
            RED    = model outside CBS 95% CI
        """
        ws = self.wb.create_sheet("Pop_Forecast")
        _set_col_widths(ws, {1: 24, 2: 14, 3: 16, 4: 13, 5: 13, 6: 13, 7: 13, 8: 12})

        r = 1
        meta = self.results.get("meta", {})
        sim_window = meta.get("sim_window", "—")
        _section_title(ws, r, 1,
            f"Check 5 — Population Forecast vs CBS Projection 2026–2070  (sim window: {sim_window})",
            n_cols=8)
        r += 2

        ws.cell(row=r, column=1, value=(
            "Model total population (millions) compared against CBS central forecast and "
            "67%/95% confidence intervals. "
            "GREEN = within CBS 67% CI  |  AMBER = within 95% CI, outside 67%  |  RED = outside 95% CI. "
            "Source: CBS StatLine Bevolkingsprognose 2024–2070."
        )).font = _font(italic=True, size=10)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, horizontal="left")
        ws.row_dimensions[r].height = 36
        r += 2

        forecast_rows = self.results.get("pop_forecast", [])

        if not forecast_rows:
            ws.cell(row=r, column=1,
                    value="Simulation window does not overlap with CBS forecast period (2026+), "
                          "or CBS file was not found. No comparison available.").font = _font(italic=True)
            return

        # ── Summary stats ─────────────────────────────────────────────────
        total     = len(forecast_rows)
        w95       = sum(1 for x in forecast_rows if x.get("within_95", False))
        w67       = sum(1 for x in forecast_rows if x.get("within_67", False))
        pct_95    = w95 / total * 100
        pct_67    = w67 / total * 100
        devs      = [x["deviation_from_central"] for x in forecast_rows]
        mean_dev  = sum(devs) / len(devs)

        stats = [
            ("Forecast years compared",          total),
            ("Years within CBS 95% CI",          f"{w95}/{total}  ({pct_95:.0f}%)"),
            ("Years within CBS 67% CI",          f"{w67}/{total}  ({pct_67:.0f}%)"),
            ("Mean deviation from CBS central",  f"{mean_dev:+.1f}%"),
        ]
        for label, val in stats:
            ws.cell(row=r, column=1, value=label).font = _font(bold=True, size=10)
            ws.cell(row=r, column=2, value=str(val)).font = _font(size=10)
            r += 1
        r += 1

        # ── Data table ────────────────────────────────────────────────────
        headers = [
            "Year",
            "Model (M)",
            "CBS Central (M)",
            "CBS 67% Lo",
            "CBS 67% Hi",
            "CBS 95% Lo",
            "CBS 95% Hi",
            "CI Status",
        ]
        _header_row(ws, r, headers)
        r += 1

        data_start_row = r
        for row in forecast_rows:
            yr     = row["year"]
            mdl_m  = row["model_millions"]
            cen_m  = row["cbs_central_millions"]
            lo67   = row.get("ci67_lo_millions")
            hi67   = row.get("ci67_hi_millions")
            lo95   = row.get("ci95_lo_millions")
            hi95   = row.get("ci95_hi_millions")
            w67r   = row.get("within_67", False)
            w95r   = row.get("within_95", False)

            if w67r:
                row_bg  = GREEN_FILL
                ci_label = "Within 67% CI"
            elif w95r:
                row_bg  = AMBER_FILL
                ci_label = "Within 95% CI"
            else:
                row_bg  = RED_FILL
                ci_label = "Outside 95% CI"

            _data_cell(ws, r, 1, yr,     center=True)
            _data_cell(ws, r, 2, mdl_m,  fmt="0.000", bg=row_bg)
            _data_cell(ws, r, 3, cen_m,  fmt="0.000")
            _data_cell(ws, r, 4, lo67 if lo67 is not None else "N/A",
                       fmt="0.000" if lo67 is not None else None, center=lo67 is None)
            _data_cell(ws, r, 5, hi67 if hi67 is not None else "N/A",
                       fmt="0.000" if hi67 is not None else None, center=hi67 is None)
            _data_cell(ws, r, 6, lo95 if lo95 is not None else "N/A",
                       fmt="0.000" if lo95 is not None else None, center=lo95 is None)
            _data_cell(ws, r, 7, hi95 if hi95 is not None else "N/A",
                       fmt="0.000" if hi95 is not None else None, center=hi95 is None)
            _data_cell(ws, r, 8, ci_label, bg=row_bg, center=True, bold=True)
            r += 1

        data_end_row = r - 1

        # ── % Deviation chart ─────────────────────────────────────────────
        # Write deviation values into auxiliary columns J(10), K(11), L(12)
        # so the chart has clean data to reference without touching the main table.
        # Columns: Year | Model dev% | CBS 95% Lo dev% | CBS 95% Hi dev%
        # "dev%" = (value - CBS central) / CBS central * 100
        if data_end_row >= data_start_row:
            AUX_YEAR_COL    = 10   # J
            AUX_MDL_COL     = 11   # K  model % deviation from CBS central
            AUX_LO95_COL    = 12   # L  CBS 95% lo % deviation from central
            AUX_HI95_COL    = 13   # M  CBS 95% hi % deviation from central

            # Header
            aux_hdr = data_start_row - 1
            for col, label in [
                (AUX_YEAR_COL,  "Year"),
                (AUX_MDL_COL,   "Model dev%"),
                (AUX_LO95_COL,  "95% Lo dev%"),
                (AUX_HI95_COL,  "95% Hi dev%"),
            ]:
                c = ws.cell(row=aux_hdr, column=col, value=label)
                c.font = _font(bold=True, size=9, color=WHITE)
                c.fill = _fill("2E5E8E")
                c.alignment = _center()
                ws.column_dimensions[get_column_letter(col)].width = 12

            aux_r = data_start_row
            for row in forecast_rows:
                cen   = row["cbs_central_millions"]
                lo95  = row.get("ci95_lo_millions")
                hi95  = row.get("ci95_hi_millions")
                dev   = row["deviation_from_central"]           # already %
                lo_dev = (lo95 - cen) / cen * 100 if lo95 is not None else None
                hi_dev = (hi95 - cen) / cen * 100 if hi95 is not None else None

                ws.cell(row=aux_r, column=AUX_YEAR_COL, value=row["year"])
                _data_cell(ws, aux_r, AUX_MDL_COL,  dev,    fmt="0.0")
                _data_cell(ws, aux_r, AUX_LO95_COL, lo_dev, fmt="0.0")
                _data_cell(ws, aux_r, AUX_HI95_COL, hi_dev, fmt="0.0")
                aux_r += 1

            aux_end = aux_r - 1

            # % deviation line chart
            chart = LineChart()
            chart.title = "Model vs CBS Forecast — % Deviation from CBS Central"
            chart.style = 2
            chart.y_axis.title = "% deviation from CBS central"
            chart.x_axis.title = "Year"
            chart.y_axis.numFmt = '0.0"%"'
            chart.height = 14
            chart.width  = 26

            dev_series = [
                (AUX_MDL_COL,  "Model deviation",  "C00000", None,     2.5),
                (AUX_LO95_COL, "CBS 95% lower CI", "4472C4", "dash",   1.5),
                (AUX_HI95_COL, "CBS 95% upper CI", "4472C4", "dash",   1.5),
            ]
            cats = Reference(ws, min_col=AUX_YEAR_COL,
                             min_row=data_start_row, max_row=aux_end)
            for col, title, colour, dash, width in dev_series:
                ref = Reference(ws, min_col=col,
                                min_row=data_start_row, max_row=aux_end)
                chart.add_data(ref)
                s = chart.series[-1]
                s.title = SeriesLabel(v=title)
                s.graphicalProperties.line.solidFill = colour
                s.graphicalProperties.line.width = int(width * 12700)  # EMUs
                if dash:
                    s.graphicalProperties.line.dashDot = dash
            chart.set_categories(cats)
            ws.add_chart(chart, f"A{data_end_row + 3}")

        # ── Methodological note ───────────────────────────────────────────
        note_row = data_end_row + 2
        ws.cell(row=note_row, column=1,
                value="Note: CBS forecasts cover total Netherlands population (all ages). "
                      "Model total = adult tensor + childhood pool. Units: millions of persons. "
                      "A FAIL indicates structural demographic divergence and should be documented — "
                      "it does not invalidate the CEA results if the divergence is explained."
                ).font = _font(italic=True, size=9)
        ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=8)
        ws.cell(row=note_row, column=1).alignment = Alignment(wrap_text=True, horizontal="left")
        ws.row_dimensions[note_row].height = 32
