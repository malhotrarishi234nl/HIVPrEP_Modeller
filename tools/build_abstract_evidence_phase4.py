"""Phase 4 of the interim abstract evidence package: tables, workbook, figures.

Brief: ``TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md`` section 4.

Read-only on every simulation output. Runs no simulation. Everything it writes
lands under ``outputs/abstract_evidence_20260923/``.

The workbook is formula-linked: each source table is written to its own
``Data_*`` sheet and the six presentation tabs reference those cells, so no
headline value is a pasted constant. Every formula is then **recalculated** with
the ``formulas`` engine and checked against an independently computed expected
value, and every sheet is scanned for Excel error strings.

    .venv/bin/python tools/build_abstract_evidence_phase4.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGE = ROOT / "outputs/abstract_evidence_20260923"
RUN = PACKAGE / "matched_deployment"

WTP_TIERS = (20, 50, 80)
BASE_PRICE = 41110.44

FAMILY_LABEL = {
    "attribution_context": "No-PrEP attribution context",
    "lena_substitution": "Lenacapavir substitution",
    "lena_add_on": "Lenacapavir add-on expansion",
    "oral_expansion_benchmark": "Full oral-PrEP expansion benchmark",
}
STRUCTURE_LABEL = {
    "legacy": "Legacy D_1788_v000 (primary interim baseline)",
    "candidate": "Late diagnosis D_1788_v109 (structural sensitivity)",
}
CONVENTION_LABEL = {
    "configured_isr_nodule": "Configured (lenacapavir ISR/nodule decrement, zero oral)",
    "symmetric_no_isr": "Symmetric no-ISR (zero decrement on both products)",
}


def scenario_label(row: pd.Series) -> str:
    family = row["deployment_family"]
    level = row["deployment_level"]
    if family == "lena_substitution":
        return f"Substitution {int(level)}%"
    if family == "lena_add_on":
        return f"Add-on {int(level):,} users by 2030"
    return FAMILY_LABEL[family]


def evidence_status_for(row: pd.Series) -> str:
    """Assign the brief's section 3D label to a result row."""
    family = row["deployment_family"]
    if family in ("attribution_context", "oral_expansion_benchmark"):
        return "context_only"
    if row["ae_convention"] == "symmetric_no_isr":
        return "economic_sensitivity"
    if row["structure"] == "candidate":
        return "structural_sensitivity"
    return "primary_interim_result"


def interpretation_for(row: pd.Series) -> str:
    """One abstract-safe sentence. Never asserts more than the row supports."""
    family = row["deployment_family"]
    if family == "attribution_context":
        return (
            "Attribution context only: oral-PrEP standard of care versus no PrEP. "
            "Not a reimbursement comparison."
        )
    if family == "oral_expansion_benchmark":
        return (
            "Contextual benchmark only: full oral-PrEP expansion to 28,000 by 2030. "
            "Not a forecast and not a lenacapavir comparison."
        )
    positive = float(row["probability_nmb_positive_80k"])
    quantity = row["pooled_price_quantity_name_80k"]
    if positive > 0:
        return "Positive net monetary benefit occurred in some seeds; review before use."
    if quantity == "no_non_negative_root":
        return (
            "Negative net monetary benefit in every seed at EUR 80,000/QALY, and no "
            "lenacapavir price including zero reaches break-even: the non-product "
            "cost increment alone exceeds the value of the QALYs gained."
        )
    stability = row["pooled_sign_stability_80k"]
    root = float(row["pooled_nmb_zero_price_80k"])
    reduction = float(row["price_reduction_required_80k"])
    if stability == "sign_unstable":
        return (
            "Negative net monetary benefit in every seed at EUR 80,000/QALY. The "
            f"NMB-zero root is EUR {root:,.0f}/year but the incremental-QALY "
            "confidence interval crosses zero, so the quantity is sign-unstable and "
            "is not a stable cost-effectiveness threshold."
        )
    named = (
        "cost-effectiveness annual price threshold"
        if quantity == "cost_effectiveness_annual_price_threshold"
        else "QALY-loss compensation annual price"
    )
    return (
        "Negative net monetary benefit in every seed at EUR 80,000/QALY; the "
        f"{named} is EUR {root:,.0f}/year, a {reduction:.0%} reduction from the "
        f"configured EUR {BASE_PRICE:,.2f}."
    )


# ---------------------------------------------------------------------------
# 4A. Tables
# ---------------------------------------------------------------------------
def build_headline(decision: pd.DataFrame) -> pd.DataFrame:
    d = decision.copy()
    d["scenario_label"] = d.apply(scenario_label, axis=1)
    d["epidemic_structure"] = d["structure"].map(STRUCTURE_LABEL)
    d["ae_convention_label"] = d["ae_convention"].map(CONVENTION_LABEL)
    d["evidence_status"] = d.apply(evidence_status_for, axis=1)
    d["abstract_safe_interpretation"] = d.apply(interpretation_for, axis=1)
    d["horizon"] = "2025-2064 (40 years), societal perspective"
    d["base_annual_lena_price_eur"] = BASE_PRICE

    columns = [
        "epidemic_structure", "structure", "deployment_family", "deployment_level",
        "scenario", "scenario_label", "ae_convention", "ae_convention_label",
        "reference_arm", "intervention_arm", "horizon", "n_seeds",
        "mean_delta_cost", "sd_delta_cost", "ci95_low_delta_cost", "ci95_high_delta_cost",
        "mean_delta_qaly", "sd_delta_qaly", "ci95_low_delta_qaly", "ci95_high_delta_qaly",
        "mean_infections_averted", "sd_infections_averted",
        "ci95_low_infections_averted", "ci95_high_infections_averted",
    ]
    for tier in WTP_TIERS:
        columns += [f"mean_nmb_{tier}k", f"probability_nmb_positive_{tier}k"]
    for tier in WTP_TIERS:
        columns += [
            f"pooled_nmb_zero_price_{tier}k", f"pooled_price_quantity_name_{tier}k",
            f"pooled_root_exists_{tier}k", f"pooled_sign_stability_{tier}k",
            f"price_reduction_required_{tier}k",
        ]
    columns += ["base_annual_lena_price_eur", "ratio_of_means_icer", "icer_status",
                "evidence_status", "abstract_safe_interpretation"]
    order = {"attribution_context": 0, "lena_substitution": 1, "lena_add_on": 2,
             "oral_expansion_benchmark": 3}
    d["_o"] = d["deployment_family"].map(order)
    d = d.sort_values(["ae_convention", "structure", "_o", "deployment_level"])
    return d[columns].reset_index(drop=True)


def build_deployment_scenarios(
    decision: pd.DataFrame, semantics: pd.DataFrame
) -> pd.DataFrame:
    current = decision[decision.ae_convention == "configured_isr_nodule"].copy()
    current["scenario_label"] = current.apply(scenario_label, axis=1)
    sem = semantics.set_index(["structure", "scenario"])
    records: list[dict[str, Any]] = []
    for _, row in current.iterrows():
        key = (row["structure"], row["scenario"])
        coverage = (
            float(sem.loc[key, "mean_relative_prep_person_time_change"])
            if key in sem.index else math.nan
        )
        cost_m = row["mean_delta_cost"] / 1e6
        records.append({
            "epidemic_structure": STRUCTURE_LABEL[row["structure"]],
            "structure": row["structure"],
            "deployment_family": FAMILY_LABEL[row["deployment_family"]],
            "deployment_family_id": row["deployment_family"],
            "deployment_level": row["deployment_level"],
            "scenario_label": row["scenario_label"],
            "comparator_arm": row["reference_arm"],
            "intervention_arm": row["intervention_arm"],
            "coverage_semantics": (
                "preserves total PrEP person-time"
                if row["deployment_family"] == "lena_substitution"
                else "expands total PrEP person-time"
                if row["deployment_family"] in ("lena_add_on", "oral_expansion_benchmark")
                else "resets PrEP at the fork (attribution only)"
            ),
            "relative_change_total_prep_person_time": coverage,
            "mean_delta_cost_eur_m": cost_m,
            "mean_infections_averted": row["mean_infections_averted"],
            "infections_averted_per_eur_100m": (
                row["mean_infections_averted"] / (cost_m / 100) if cost_m else math.nan
            ),
            "mean_delta_qaly": row["mean_delta_qaly"],
            "mean_nmb_80k_eur_m": row["mean_nmb_80k"] / 1e6,
            "probability_nmb_positive_80k": row["probability_nmb_positive_80k"],
            "evidence_status": evidence_status_for(row),
        })
    frame = pd.DataFrame(records)
    order = {"No-PrEP attribution context": 0, "Lenacapavir substitution": 1,
             "Lenacapavir add-on expansion": 2,
             "Full oral-PrEP expansion benchmark": 3}
    frame["_o"] = frame["deployment_family"].map(order)
    return frame.sort_values(["structure", "_o", "deployment_level"]).drop(
        columns="_o").reset_index(drop=True)


def build_economic_sensitivity(decision: pd.DataFrame) -> pd.DataFrame:
    """Configured versus symmetric no-ISR, and across the three WTP tiers."""
    pivot_keys = ["structure", "scenario", "deployment_family", "deployment_level"]
    cur = decision[decision.ae_convention == "configured_isr_nodule"].set_index(pivot_keys)
    alt = decision[decision.ae_convention == "symmetric_no_isr"].set_index(pivot_keys)
    records: list[dict[str, Any]] = []
    for key in cur.index:
        a, b = cur.loc[key], alt.loc[key]
        row: dict[str, Any] = {
            "epidemic_structure": STRUCTURE_LABEL[key[0]],
            "structure": key[0],
            "deployment_family": FAMILY_LABEL[key[2]],
            "deployment_level": key[3],
            "scenario_label": scenario_label(
                pd.Series({"deployment_family": key[2], "deployment_level": key[3]})
            ),
            "mean_delta_cost_eur_m": a["mean_delta_cost"] / 1e6,
            "delta_cost_identical_across_conventions": bool(
                abs(a["mean_delta_cost"] - b["mean_delta_cost"]) < 1e-6
            ),
            "mean_delta_qaly_configured": a["mean_delta_qaly"],
            "mean_delta_qaly_no_isr": b["mean_delta_qaly"],
            "delta_qaly_shift_from_removing_isr": b["mean_delta_qaly"] - a["mean_delta_qaly"],
            "qaly_sign_configured": (
                "positive" if a["mean_delta_qaly"] > 0 else "negative"
            ),
            "qaly_sign_no_isr": "positive" if b["mean_delta_qaly"] > 0 else "negative",
        }
        for tier in WTP_TIERS:
            row[f"nmb_{tier}k_configured_eur_m"] = a[f"mean_nmb_{tier}k"] / 1e6
            row[f"nmb_{tier}k_no_isr_eur_m"] = b[f"mean_nmb_{tier}k"] / 1e6
            row[f"p_nmb_positive_{tier}k_configured"] = a[f"probability_nmb_positive_{tier}k"]
            row[f"p_nmb_positive_{tier}k_no_isr"] = b[f"probability_nmb_positive_{tier}k"]
        row["decision_changed_by_convention"] = bool(
            any(
                (a[f"probability_nmb_positive_{t}k"] > 0)
                != (b[f"probability_nmb_positive_{t}k"] > 0)
                for t in WTP_TIERS
            )
        )
        row["decision_changed_by_wtp_tier"] = bool(
            len({a[f"probability_nmb_positive_{t}k"] > 0 for t in WTP_TIERS}) > 1
        )
        records.append(row)
    frame = pd.DataFrame(records)
    return frame.sort_values(
        ["structure", "deployment_family", "deployment_level"]
    ).reset_index(drop=True)


def build_price_thresholds(pooled: pd.DataFrame) -> pd.DataFrame:
    p = pooled.copy()
    p["epidemic_structure"] = p["structure"].map(STRUCTURE_LABEL)
    p["deployment_family_label"] = p["deployment_family"].map(FAMILY_LABEL)
    p["scenario_label"] = p.apply(scenario_label, axis=1)
    p["ae_convention_label"] = p["ae_convention"].map(CONVENTION_LABEL)
    p["base_annual_lena_price_eur"] = BASE_PRICE
    p["price_reduction_required"] = np.where(
        p["nmb_zero_price"].notna(), 1.0 - p["nmb_zero_price"] / BASE_PRICE, np.nan
    )
    p["safe_to_quote_as_a_price"] = p["root_exists"] & (
        p["sign_stability"] == "sign_stable"
    )
    columns = [
        "epidemic_structure", "structure", "deployment_family_label", "deployment_level",
        "scenario_label", "scenario", "ae_convention_label", "ae_convention", "wtp",
        "mean_delta_qaly", "mean_qaly_ci_crosses_zero", "root_exists", "sign_stability",
        "price_quantity_name", "nmb_zero_price", "ce_threshold_price",
        "qaly_loss_compensation_price", "base_annual_lena_price_eur",
        "price_reduction_required", "safe_to_quote_as_a_price", "reporting_rule", "method",
    ]
    return p[columns].sort_values(
        ["ae_convention", "structure", "deployment_family_label", "deployment_level", "wtp"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4B. Workbook
# ---------------------------------------------------------------------------
DATA_SHEETS = {
    "Data_headline": "headline_results.csv",
    "Data_deployment": "deployment_scenarios.csv",
    "Data_economic": "economic_sensitivity.csv",
    "Data_prices": "price_thresholds.csv",
    "Data_historical": "historical_model_comparison.csv",
    "Data_claims": "CLAIMS_REGISTER.csv",
    "Data_sources": "SOURCE_REGISTER.csv",
    "Data_controls": "controls_reproduction.csv",
}


#: A blank source cell must never be referenced bare: Excel renders a blank
#: reference as 0. Blank-capable cells are wrapped in IF(ISBLANK(...)) with an
#: explicit sentinel. Price sentinels depend on the row's price-quantity
#: classification, not merely on the column: ``not_applicable`` is distinct
#: from ``no_non_negative_root``.
NO_PRICE_SENTINEL = "no non-negative price exists"
NOT_APPLICABLE_SENTINEL = "not applicable"
PRICE_COLUMNS = frozenset({
    "pooled_nmb_zero_price_20k", "pooled_nmb_zero_price_50k", "pooled_nmb_zero_price_80k",
    "price_reduction_required_20k", "price_reduction_required_50k",
    "price_reduction_required_80k", "nmb_zero_price", "ce_threshold_price",
    "qaly_loss_compensation_price", "price_reduction_required",
})


def _col_letter(index_zero_based: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index_zero_based + 1)


def _write_data_sheet(workbook, name: str, frame: pd.DataFrame) -> dict[str, int]:
    sheet = workbook.create_sheet(name)
    sheet.append(list(frame.columns))
    for record in frame.itertuples(index=False):
        sheet.append(
            [None if (isinstance(v, float) and math.isnan(v)) else v for v in record]
        )
    sheet.freeze_panes = "A2"
    return {column: i for i, column in enumerate(frame.columns)}


def build_workbook(out: Path, tables: dict[str, pd.DataFrame]) -> tuple[Path, list[dict]]:
    """Write the workbook with formula-linked presentation tabs.

    Returns the path and the list of (sheet, cell, formula, expected) records that
    ``verify_workbook`` recalculates.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    workbook.remove(workbook.active)
    index: dict[str, dict[str, int]] = {}
    for sheet_name, filename in DATA_SHEETS.items():
        frame = tables[filename]
        index[sheet_name] = _write_data_sheet(workbook, sheet_name, frame)

    expectations: list[dict] = []

    def link(
        sheet, row_out, col_out, data_sheet, data_row_zero, column, expected,
        *, blank_sentinel=None,
    ):
        """Write a formula referencing one source cell, and record the expectation.

        When the source cell is blank the formula resolves to an explicit
        sentinel string rather than to Excel's implicit zero.
        """
        letter = _col_letter(index[data_sheet][column])
        reference = f"'{data_sheet}'!{letter}{data_row_zero + 2}"
        is_blank = expected is None or (
            isinstance(expected, float) and math.isnan(expected)
        )
        if is_blank:
            if column in PRICE_COLUMNS and blank_sentinel is None:
                raise ValueError(
                    f"blank price field {column!r} requires a row-aware sentinel"
                )
            sentinel = blank_sentinel or NOT_APPLICABLE_SENTINEL
            formula = f'=IF(ISBLANK({reference}),"{sentinel}",{reference})'
            expected = sentinel
        else:
            formula = f"={reference}"
        sheet.cell(row=row_out, column=col_out).value = formula
        expectations.append({
            "sheet": sheet.title, "cell": sheet.cell(row=row_out, column=col_out).coordinate,
            "formula": formula, "expected": expected,
            "blank_guarded": is_blank,
        })

    RECALC_NOTE = (
        " | This workbook stores formulas without cached results and is set to "
        "recalculate on open. If your viewer does not calculate, these cells will "
        "appear blank: the values are in the Data_ sheets."
    )

    def header(sheet, titles, note):
        sheet.append([note + RECALC_NOTE])
        sheet.cell(row=1, column=1).font = Font(italic=True, size=9)
        sheet.append([])
        sheet.append(titles)
        for i in range(1, len(titles) + 1):
            cell = sheet.cell(row=3, column=i)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "A4"

    # ---- Tab 1: Headline results -----------------------------------------
    head = tables["headline_results.csv"]
    sheet = workbook.create_sheet("Headline results", 0)
    header(sheet, [
        "Epidemic structure", "Deployment scenario", "AE convention", "Comparator",
        "Intervention", "Horizon", "n seeds",
        "ΔCost, EUR m (mean)", "ΔCost 95% CI low", "ΔCost 95% CI high",
        "ΔQALY (mean)", "ΔQALY 95% CI low", "ΔQALY 95% CI high",
        "Infections averted (mean)", "Infections averted 95% CI low",
        "Infections averted 95% CI high",
        "NMB EUR 20k/QALY, EUR m", "NMB EUR 50k/QALY, EUR m", "NMB EUR 80k/QALY, EUR m",
        "P(NMB>0) at 80k", "Price quantity at 80k", "Price value, EUR/yr",
        "Price reduction required", "Evidence status", "Abstract-safe interpretation",
    ], "Every value below is a formula referencing Data_headline. Costs discounted 4.0%/yr, "
       "QALYs 1.5%/yr, societal perspective, 2025-2064. Comparator is oral-PrEP standard of care "
       "except in the attribution row. Seeds 42-56 (HTA sample; NOT the history-matching seeds).")
    for i, (_, r) in enumerate(head.iterrows()):
        row = 4 + i
        for col, (column, expected, scale) in enumerate([
            ("epidemic_structure", r.epidemic_structure, 1),
            ("scenario_label", r.scenario_label, 1),
            ("ae_convention_label", r.ae_convention_label, 1),
            ("reference_arm", r.reference_arm, 1),
            ("intervention_arm", r.intervention_arm, 1),
            ("horizon", r.horizon, 1),
            ("n_seeds", r.n_seeds, 1),
        ], start=1):
            link(sheet, row, col, "Data_headline", i, column, expected)
        # scaled / derived cells carry real arithmetic, not a bare reference
        scaled = [
            ("mean_delta_cost", 8, r.mean_delta_cost / 1e6),
            ("ci95_low_delta_cost", 9, r.ci95_low_delta_cost / 1e6),
            ("ci95_high_delta_cost", 10, r.ci95_high_delta_cost / 1e6),
        ]
        for column, col, expected in scaled:
            letter = _col_letter(index["Data_headline"][column])
            formula = f"='Data_headline'!{letter}{i + 2}/1000000"
            sheet.cell(row=row, column=col).value = formula
            expectations.append({"sheet": sheet.title,
                                 "cell": sheet.cell(row=row, column=col).coordinate,
                                 "formula": formula, "expected": expected})
        for column, col, expected in [
            ("mean_delta_qaly", 11, r.mean_delta_qaly),
            ("ci95_low_delta_qaly", 12, r.ci95_low_delta_qaly),
            ("ci95_high_delta_qaly", 13, r.ci95_high_delta_qaly),
            ("mean_infections_averted", 14, r.mean_infections_averted),
            ("ci95_low_infections_averted", 15, r.ci95_low_infections_averted),
            ("ci95_high_infections_averted", 16, r.ci95_high_infections_averted),
        ]:
            link(sheet, row, col, "Data_headline", i, column, expected)
        for tier, col in zip(WTP_TIERS, (17, 18, 19)):
            column = f"mean_nmb_{tier}k"
            letter = _col_letter(index["Data_headline"][column])
            formula = f"='Data_headline'!{letter}{i + 2}/1000000"
            sheet.cell(row=row, column=col).value = formula
            expectations.append({"sheet": sheet.title,
                                 "cell": sheet.cell(row=row, column=col).coordinate,
                                 "formula": formula,
                                 "expected": getattr(r, column) / 1e6})
        for column, col, expected in [
            ("probability_nmb_positive_80k", 20, r.probability_nmb_positive_80k),
            ("pooled_price_quantity_name_80k", 21, r.pooled_price_quantity_name_80k),
            ("pooled_nmb_zero_price_80k", 22, r.pooled_nmb_zero_price_80k),
            ("price_reduction_required_80k", 23, r.price_reduction_required_80k),
            ("evidence_status", 24, r.evidence_status),
            ("abstract_safe_interpretation", 25, r.abstract_safe_interpretation),
        ]:
            expected = (
                None if (isinstance(expected, float) and math.isnan(expected)) else expected
            )
            blank_sentinel = None
            if expected is None and column in PRICE_COLUMNS:
                quantity = r.pooled_price_quantity_name_80k
                if quantity == "no_non_negative_root":
                    blank_sentinel = NO_PRICE_SENTINEL
                elif quantity == "not_applicable":
                    blank_sentinel = NOT_APPLICABLE_SENTINEL
                else:
                    raise ValueError(
                        f"blank {column!r} has unexpected price quantity {quantity!r}"
                    )
            link(sheet, row, col, "Data_headline", i, column,
                 expected, blank_sentinel=blank_sentinel)

    # ---- Tab 2: Historical comparison ------------------------------------
    hist = tables["historical_model_comparison.csv"]
    sheet = workbook.create_sheet("Historical comparison", 1)
    header(sheet, ["Measure", "Observed or interval", "Evidence tier", "Comparison type",
                   "Legacy mean", "Late-diagnosis mean", "Paired difference",
                   "95% CI low", "95% CI high", "CI excludes zero", "Note"],
           "Legacy D_1788_v000 versus late-diagnosis D_1788_v109 on the identical 30 "
           "history-matching confirmation seeds 202300-202329. These are NOT the HTA seeds. "
           "Tier-2 rows are cross-model triangulation, not independent validation.")
    for i, (_, r) in enumerate(hist.iterrows()):
        for col, column in enumerate(
            ["measure", "observed_or_interval", "evidence_tier", "comparison_type",
             "legacy_mean", "late_diagnosis_mean", "paired_difference_mean",
             "paired_ci95_low", "paired_ci95_high", "ci_excludes_zero", "note"], start=1
        ):
            value = r[column]
            link(sheet, 4 + i, col, "Data_historical", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)

    # ---- Tab 3: Deployment scenarios -------------------------------------
    dep = tables["deployment_scenarios.csv"]
    sheet = workbook.create_sheet("Deployment scenarios", 2)
    header(sheet, ["Epidemic structure", "Deployment family", "Scenario", "Comparator",
                   "Coverage semantics", "Relative change in total PrEP person-time",
                   "ΔCost, EUR m", "Infections averted",
                   "Infections averted per EUR 100m", "ΔQALY",
                   "NMB at EUR 80k/QALY, EUR m", "P(NMB>0) at 80k", "Evidence status"],
           "Configured AE convention. Five substitution plus three add-on variants are eight "
           "distinct lenacapavir deployment scenarios; they are not eight substitution levels. "
           "Add-on and the oral benchmark expand coverage, substitution preserves it.")
    for i, (_, r) in enumerate(dep.iterrows()):
        for col, column in enumerate(
            ["epidemic_structure", "deployment_family", "scenario_label", "comparator_arm",
             "coverage_semantics", "relative_change_total_prep_person_time",
             "mean_delta_cost_eur_m", "mean_infections_averted",
             "infections_averted_per_eur_100m", "mean_delta_qaly", "mean_nmb_80k_eur_m",
             "probability_nmb_positive_80k", "evidence_status"], start=1
        ):
            value = r[column]
            link(sheet, 4 + i, col, "Data_deployment", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)

    # ---- Tab 4: Economic sensitivity --------------------------------------
    eco = tables["economic_sensitivity.csv"]
    sheet = workbook.create_sheet("Economic sensitivity", 3)
    header(sheet, ["Epidemic structure", "Deployment family", "Scenario",
                   "ΔCost, EUR m (identical across conventions)",
                   "ΔQALY configured", "ΔQALY no-ISR", "Shift from removing ISR",
                   "Sign configured", "Sign no-ISR",
                   "NMB 80k configured, EUR m", "NMB 80k no-ISR, EUR m",
                   "P(NMB>0) 80k configured", "P(NMB>0) 80k no-ISR",
                   "Decision changed by convention?", "Decision changed by WTP tier?"],
           "The symmetric no-ISR sensitivity is a CONVENTION sensitivity: it applies the "
           "base case's own zero-disutility convention to both products instead of to oral "
           "PrEP alone. It is not evidence that adverse-event burden is zero. The AE cost "
           "channel is exactly zero, so incremental cost is unchanged.")
    for i, (_, r) in enumerate(eco.iterrows()):
        for col, column in enumerate(
            ["epidemic_structure", "deployment_family", "scenario_label",
             "mean_delta_cost_eur_m", "mean_delta_qaly_configured", "mean_delta_qaly_no_isr",
             "delta_qaly_shift_from_removing_isr", "qaly_sign_configured", "qaly_sign_no_isr",
             "nmb_80k_configured_eur_m", "nmb_80k_no_isr_eur_m",
             "p_nmb_positive_80k_configured", "p_nmb_positive_80k_no_isr",
             "decision_changed_by_convention", "decision_changed_by_wtp_tier"], start=1
        ):
            value = r[column]
            link(sheet, 4 + i, col, "Data_economic", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)

    # ---- Tab 5: Claims register -------------------------------------------
    claims = tables["CLAIMS_REGISTER.csv"]
    sheet = workbook.create_sheet("Claims register", 4)
    header(sheet, ["Claim", "Proposed claim", "Evidence status", "Metric or test",
                   "Sources", "Required caveat", "Allowed wording", "Prohibited wording",
                   "Review status"],
           "Approved and prohibited wording for every claim. A claim marked not_ready must "
           "not appear in the abstract. Prohibited wording is prohibited even where the "
           "underlying number is correct.")
    for i, (_, r) in enumerate(claims.iterrows()):
        for col, column in enumerate(
            ["claim_id", "proposed_claim", "evidence_status", "metric_or_test", "source_ids",
             "required_caveat", "allowed_wording", "prohibited_wording", "review_status"],
            start=1
        ):
            value = r[column]
            link(sheet, 4 + i, col, "Data_claims", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)

    # ---- Tab 6: Sources and checks ----------------------------------------
    sources = tables["SOURCE_REGISTER.csv"]
    controls = tables["controls_reproduction.csv"]
    sheet = workbook.create_sheet("Sources and checks", 5)
    header(sheet, ["Source", "Path", "Role", "Status", "sha256", "Seeds", "Horizon",
                   "Headline eligible", "Notes"],
           "Every artifact used, with its sha256 at the time of writing. Control "
           "reproduction results follow below the source list.")
    for i, (_, r) in enumerate(sources.iterrows()):
        for col, column in enumerate(
            ["source_id", "path", "role", "status", "config_or_file_sha256",
             "seeds_or_sample", "horizon", "headline_eligible", "notes"], start=1
        ):
            value = r[column]
            link(sheet, 4 + i, col, "Data_sources", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)
    start = 4 + len(sources) + 2
    sheet.cell(row=start, column=1).value = "Control reproduction (brief sections 5.1 and 5.2)"
    sheet.cell(row=start, column=1).font = Font(bold=True)
    for col, title in enumerate(
        ["Section", "Control", "Expected", "Observed", "Status"], start=1
    ):
        cell = sheet.cell(row=start + 1, column=col)
        cell.value = title
        cell.font = Font(bold=True)
    for i, (_, r) in enumerate(controls.iterrows()):
        for col, column in enumerate(
            ["section", "control", "expected", "observed", "status"], start=1
        ):
            value = r[column]
            link(sheet, start + 2 + i, col, "Data_controls", i, column,
                 None if (isinstance(value, float) and math.isnan(value)) else value)

    PROSE = {"Abstract-safe interpretation", "Required caveat", "Allowed wording",
             "Prohibited wording", "Metric or test", "Proposed claim", "Note", "Notes",
             "Path", "Role", "Control", "Coverage semantics", "Price quantity at 80k"}
    for sheet in workbook.worksheets:
        presentation = not sheet.title.startswith("Data_")
        titles = (
            [c.value for c in sheet[3]] if presentation and sheet.max_row >= 3 else []
        )
        for i, column_cells in enumerate(sheet.columns):
            letter = column_cells[0].column_letter
            title = titles[i] if i < len(titles) else None
            if presentation and title in PROSE:
                sheet.column_dimensions[letter].width = 52
                for cell in column_cells:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
            elif presentation and i == 0:
                sheet.column_dimensions[letter].width = 34
                for cell in column_cells:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                sheet.column_dimensions[letter].width = 20
        if presentation:
            for row in sheet.iter_rows(min_row=4):
                sheet.row_dimensions[row[0].row].height = None

    # The workbook stores formulas without cached results, so a viewer that does
    # not calculate would show blanks. Force a full recalculation on load.
    workbook.calculation.fullCalcOnLoad = True

    path = out / "abstract_evidence_pack.xlsx"
    workbook.save(path)
    return path, expectations


def verify_workbook(path: Path, expectations: list[dict]) -> pd.DataFrame:
    """Recalculate every formula and compare against the expected value."""
    import formulas

    model = formulas.ExcelModel().loads(str(path)).finish()
    solution = model.calculate()
    resolved: dict[str, Any] = {}
    for key, value in solution.items():
        name = key.upper()
        if "'!" not in name:
            continue
        try:
            resolved[name.split("]")[-1]] = value.value[0, 0]
        except Exception:
            continue

    checks: list[dict[str, Any]] = []
    for item in expectations:
        key = f"{item['sheet'].upper()}'!{item['cell']}"
        observed = resolved.get(key, "<not resolved>")
        expected = item["expected"]
        if isinstance(observed, str) and observed.startswith("#"):
            passes, note = False, f"Excel error {observed}"
        elif expected is None:
            # A bare blank is no longer permitted anywhere: every blank-capable
            # cell is written with an explicit sentinel. Zero is REJECTED here,
            # because a blank rendered as 0 is the defect this guards against.
            passes, note = False, "unguarded blank (must use a sentinel)"
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            try:
                passes = abs(float(observed) - float(expected)) <= max(
                    1e-9, 1e-9 * abs(float(expected))
                )
            except (TypeError, ValueError):
                passes = False
            note = "numeric"
        else:
            passes = str(observed).strip().upper() == str(expected).strip().upper()
            note = "text"
        checks.append({**{k: item[k] for k in ("sheet", "cell", "formula")},
                       "blank_guarded": item.get("blank_guarded", False),
                       "expected": expected, "observed": observed,
                       "kind": note, "passes": passes})
    return pd.DataFrame(checks)


# ---------------------------------------------------------------------------
# 4C. Figures (maximum five)
# ---------------------------------------------------------------------------
LEGACY_C, CAND_C = "#3B6EA5", "#C1642B"
SUB_C, ADD_C = "#3B6EA5", "#2E8B6F"


def build_figures(out: Path, tables: dict[str, pd.DataFrame], run: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    figures = out / "figures"
    figures.mkdir(exist_ok=True)
    written: list[Path] = []
    plt.rcParams.update({"font.size": 8, "axes.grid": True, "grid.alpha": 0.25,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "figure.dpi": 200})

    imp = pd.read_csv(
        ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
        / "target_implausibility_table.csv", float_precision="round_trip")
    annual = pd.read_csv(
        ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
        / "candidate_vs_legacy_annual.csv", float_precision="round_trip")

    # -- Figure 1: historical fit, all 13 primary targets --------------------
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    cand = imp[imp.tuple_id == "D_1788_v109"].set_index("target")
    leg = imp[imp.tuple_id == "D_1788_v000"].set_index("target")
    targets = list(leg.sort_values("primary_implausibility", ascending=False).index)
    y = np.arange(len(targets))
    ax.barh(y - 0.2, [leg.loc[t, "primary_implausibility"] for t in targets],
            height=0.38, color=LEGACY_C, label="Legacy D_1788_v000")
    ax.barh(y + 0.2, [cand.loc[t, "primary_implausibility"] for t in targets],
            height=0.38, color=CAND_C, label="Late diagnosis D_1788_v109")
    ax.axvline(3.0, color="#B03030", lw=1.2, ls="--")
    ax.text(3.03, -0.45, "frozen cutoff I = 3", color="#B03030", fontsize=7, va="bottom")
    ax.set_yticks(y); ax.set_yticklabels(targets, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(0, 3.35)
    ax.set_xlabel("Primary implausibility I (uncertainty-standardised discrepancy, not a percentage)")
    ax.set_title("Figure 1. Historical fit: all 13 primary targets, 30 confirmation seeds\n"
                 "Both structures satisfy I$_{max}$ \u2264 3. Compatibility with the frozen "
                 "contract, not validity.", fontsize=8.5, loc="left")
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout(); path = figures / "fig1_historical_fit.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig); written.append(path)

    # -- Figure 2: epidemic trajectories -------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 2.7), sharex=True)
    series = [("new_diagnoses_msm", "MSM diagnoses per year"),
              ("new_infections_msm", "MSM new infections per year"),
              ("msm_undiagnosed_end", "Undiagnosed MSM, end of year")]
    for ax, (name, title) in zip(axes, series):
        g = annual[annual.series == name].sort_values("year")
        for col, colour, label in (("legacy_mean", LEGACY_C, "Legacy"),
                                   ("candidate_mean", CAND_C, "Late diagnosis")):
            ax.plot(g.year, g[col], color=colour, lw=1.4, label=label)
            sd = g[col.replace("_mean", "_sd")]
            ax.fill_between(g.year, g[col] - sd, g[col] + sd, color=colour, alpha=0.15, lw=0)
        ax.set_title(title, fontsize=8)
        ax.set_xlabel("year")
    axes[0].legend(fontsize=7)
    fig.suptitle("Figure 2. Epidemic trajectories, 1996-2025, ensemble mean \u00b1 1 SD over "
                 "30 confirmation seeds", fontsize=8.5, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.9)); path = figures / "fig2_trajectories.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig); written.append(path)

    # -- Figure 3: infections averted, eight deployment scenarios ------------
    dep = tables["deployment_scenarios.csv"]
    dep = dep[dep.deployment_family_id.isin(["lena_substitution", "lena_add_on"])]
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0), sharey=True)
    for ax, structure, title in ((axes[0], "legacy", "Legacy D_1788_v000"),
                                 (axes[1], "candidate", "Late diagnosis D_1788_v109")):
        d = dep[dep.structure == structure]
        sub = d[d.deployment_family_id == "lena_substitution"].sort_values("deployment_level")
        add = d[d.deployment_family_id == "lena_add_on"].sort_values("deployment_level")
        x1 = np.arange(len(sub)); x2 = np.arange(len(add)) + len(sub) + 0.8
        ax.bar(x1, sub.mean_infections_averted, color=SUB_C, width=0.7)
        ax.bar(x2, add.mean_infections_averted, color=ADD_C, width=0.7)
        ax.set_xticks(list(x1) + list(x2))
        ax.set_xticklabels([f"{int(v)}%" for v in sub.deployment_level]
                           + [f"+{int(v):,}" for v in add.deployment_level], fontsize=7)
        ax.set_title(title, fontsize=8)
    axes[0].set_ylabel("Infections averted over 2025-2064 (mean of 15 seeds)")
    axes[0].legend(handles=[Line2D([0], [0], color=SUB_C, lw=6, label="Substitution (5 variants)"),
                            Line2D([0], [0], color=ADD_C, lw=6, label="Add-on expansion (3 variants)")],
                   fontsize=7, loc="upper left")
    fig.suptitle("Figure 3. Infections averted: five substitution and three add-on scenarios, "
                 "by epidemic structure\nSubstitution preserves PrEP coverage; add-on expands it. "
                 "These are different strategies, not one axis.", fontsize=8.5, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.88)); path = figures / "fig3_infections_averted.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig); written.append(path)

    # -- Figure 4: NMB at the configured price -------------------------------
    head = tables["headline_results.csv"]
    h = head[(head.ae_convention == "configured_isr_nodule")
             & head.deployment_family.isin(["lena_substitution", "lena_add_on"])]
    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    labels, offset = [], 0
    for structure, colour, marker in (("legacy", LEGACY_C, "o"), ("candidate", CAND_C, "s")):
        d = h[h.structure == structure].copy()
        d["_o"] = d.deployment_family.map({"lena_substitution": 0, "lena_add_on": 1})
        d = d.sort_values(["_o", "deployment_level"])
        xs = np.arange(len(d)) + (offset - 0.5) * 0.28
        ax.plot(xs, d.mean_nmb_80k / 1e6, marker, color=colour, ms=5,
                label=f"{structure.replace('candidate', 'late diagnosis')}")
        if not labels:
            labels = list(d.scenario_label)
        offset += 1
    ax.axhline(0, color="#B03030", lw=1.2, ls="--")
    ax.annotate("NMB = 0 (break-even)", xy=(0.012, 0.0), xycoords=("axes fraction", "data"),
                xytext=(0, 4), textcoords="offset points", color="#B03030", fontsize=7)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("Net monetary benefit at \u20ac80,000/QALY (\u20ac million)")
    ax.set_title("Figure 4. Net monetary benefit at the configured price of "
                 "\u20ac41,110.44/year\nEvery scenario is negative in every seed under both "
                 "epidemic structures: P(NMB>0) = 0.", fontsize=8.5, loc="left")
    ax.legend(fontsize=7)
    fig.tight_layout(); path = figures / "fig4_nmb_at_current_price.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig); written.append(path)

    # -- Figure 5: price quantities, with the two kinds kept apart -----------
    # "No non-negative price exists" must NOT be drawn on the price axis: a
    # marker at zero reads as a EUR 0 threshold, which is the opposite of the
    # finding. It gets its own indicator strip below the axis.
    prices = tables["price_thresholds.csv"]
    p = prices[(prices.wtp == 80000.0)
               & (prices.ae_convention == "configured_isr_nodule")
               & prices.deployment_family_label.str.contains("Lenacapavir")].copy()
    p["_s"] = p.structure.map({"legacy": 0, "candidate": 1})
    p["_o"] = p.deployment_family_label.map(
        {"Lenacapavir substitution": 0, "Lenacapavir add-on expansion": 1})
    p = p.sort_values(["_s", "_o", "deployment_level"]).reset_index(drop=True)

    fig, (ax, strip) = plt.subplots(
        2, 1, figsize=(8.4, 4.4), sharex=True,
        gridspec_kw={"height_ratios": [7, 1], "hspace": 0.08})
    style = {
        "cost_effectiveness_annual_price_threshold":
            ("o", "#2E6E3F", "CE price threshold ($\\Delta$QALY > 0, CI excludes 0)"),
        "qaly_loss_compensation_annual_price":
            ("^", "#8A5A2B", "QALY-loss compensation price ($\\Delta$QALY < 0)"),
    }
    seen = set()
    for i, r in p.iterrows():
        if not r.root_exists:
            strip.scatter(i, 0, marker="x", color="#B03030", s=46, linewidths=1.6, zorder=3)
            seen.add("none")
            continue
        marker, colour, _ = style[r.price_quantity_name]
        face = colour if r.sign_stability == "sign_stable" else "white"
        ax.scatter(i, r.nmb_zero_price, marker=marker, edgecolor=colour, facecolor=face,
                   s=46, zorder=3, linewidths=1.4)
        seen.add(r.price_quantity_name)
    ax.axhline(BASE_PRICE, color="#B03030", lw=1.3)
    ax.text(0.005, BASE_PRICE * 1.10, f"configured price \u20ac{BASE_PRICE:,.0f}/yr",
            color="#B03030", fontsize=7)
    ax.set_yscale("log")
    ax.set_ylim(80, BASE_PRICE * 2.4)
    ax.set_ylabel("Annual lenacapavir price\n(\u20ac/year, log scale)", fontsize=8)
    ax.grid(axis="x", alpha=0.15)

    strip.set_ylim(-0.6, 0.6); strip.set_yticks([0])
    strip.set_yticklabels(["no non-negative\nprice exists"], fontsize=6.5, color="#B03030")
    strip.grid(False); strip.set_facecolor("#FBF3F3")
    for spine in ("left", "right", "top"):
        strip.spines[spine].set_visible(False)
    strip.set_xticks(np.arange(len(p)))
    strip.set_xticklabels(
        [f"{'legacy' if r.structure == 'legacy' else 'late dx'} \u00b7 {r.scenario_label}"
         for _, r in p.iterrows()], rotation=40, ha="right", fontsize=6.5)

    handles = [Line2D([0], [0], marker=style[k][0], color="w", markerfacecolor=style[k][1],
                      markeredgecolor=style[k][1], markersize=7, label=style[k][2])
               for k in style if k in seen]
    handles += [Line2D([0], [0], marker="o", color="w", markerfacecolor="white",
                       markeredgecolor="#555", markersize=7,
                       label="hollow = sign-unstable (QALY CI crosses 0)")]
    ax.legend(handles=handles, fontsize=6.5, loc="upper right", framealpha=0.95)
    ax.set_title(
        "Figure 5. Configured price versus valid price quantities, \u20ac80,000/QALY, "
        "configured AE convention\nCE thresholds and QALY-loss compensation prices are "
        "different quantities and are marked separately.\nScenarios with no non-negative "
        "price are shown in the strip below, never as a price of zero.",
        fontsize=8.5, loc="left")
    fig.tight_layout(); path = figures / "fig5_price_quantities.png"
    fig.savefig(path, bbox_inches="tight"); plt.close(fig); written.append(path)
    return written


EXCEL_ERRORS = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NULL!", "#NUM!")


def scan_workbook_structure(path: Path, expectations: list[dict]) -> pd.DataFrame:
    """Static structural scan of the saved workbook.

    This is NOT a render test. It reopens the file with openpyxl and checks that
    every sheet is populated, that no cell contains an Excel error literal, and
    that each presentation tab carries the expected number of formula cells.
    Nothing is rasterised and no layout is measured.
    """
    from openpyxl import load_workbook

    workbook = load_workbook(path)
    formula_cells: dict[str, set[str]] = {}
    for item in expectations:
        formula_cells.setdefault(item["sheet"], set()).add(item["cell"])

    rows: list[dict[str, Any]] = []
    for sheet in workbook.worksheets:
        errors, populated, total = [], 0, 0
        for line in sheet.iter_rows():
            for cell in line:
                if cell.value is None:
                    continue
                total += 1
                populated += 1
                text = str(cell.value)
                if any(code in text for code in EXCEL_ERRORS):
                    errors.append(f"{cell.coordinate}:{text[:40]}")
        expected_formulas = len(formula_cells.get(sheet.title, ()))
        rows.append({
            "sheet": sheet.title,
            "is_presentation_tab": not sheet.title.startswith("Data_"),
            "max_row": sheet.max_row,
            "max_column": sheet.max_column,
            "populated_cells": populated,
            "formula_cells_verified": expected_formulas,
            "excel_error_cells": len(errors),
            "excel_errors": "; ".join(errors[:5]),
            "populated": populated > 0,
            "passes": populated > 0 and not errors,
        })
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-dir", type=Path, default=PACKAGE)
    parser.add_argument("--run-dir", type=Path, default=RUN)
    parser.add_argument("--tables-only", action="store_true")
    args = parser.parse_args(argv)
    out, run = args.output_dir, args.run_dir
    out.mkdir(parents=True, exist_ok=True)

    read = lambda name: pd.read_csv(run / name, float_precision="round_trip")  # noqa: E731
    decision = read("decision_summary.csv")
    pooled = read("pooled_price_thresholds.csv")
    semantics = read("gate_semantics.csv")

    headline = build_headline(decision)
    headline.to_csv(out / "headline_results.csv", index=False)
    deployment = build_deployment_scenarios(decision, semantics)
    deployment.to_csv(out / "deployment_scenarios.csv", index=False)
    economic = build_economic_sensitivity(decision)
    economic.to_csv(out / "economic_sensitivity.csv", index=False)
    prices = build_price_thresholds(pooled)
    prices.to_csv(out / "price_thresholds.csv", index=False)

    print(f"headline_results.csv      {len(headline):4d} rows")
    print(f"deployment_scenarios.csv  {len(deployment):4d} rows")
    print(f"economic_sensitivity.csv  {len(economic):4d} rows")
    print(f"price_thresholds.csv      {len(prices):4d} rows")
    if args.tables_only:
        return 0

    tables = {
        "headline_results.csv": headline,
        "deployment_scenarios.csv": deployment,
        "economic_sensitivity.csv": economic,
        "price_thresholds.csv": prices,
        "historical_model_comparison.csv": pd.read_csv(
            out / "historical_model_comparison.csv", float_precision="round_trip"),
        "CLAIMS_REGISTER.csv": pd.read_csv(out / "CLAIMS_REGISTER.csv"),
        "SOURCE_REGISTER.csv": pd.read_csv(out / "SOURCE_REGISTER.csv"),
        "controls_reproduction.csv": pd.read_csv(
            out / "controls_reproduction.csv", float_precision="round_trip"),
    }
    path, expectations = build_workbook(out, tables)
    print(f"\nworkbook written: {path.name} ({len(expectations)} formula cells)")
    checks = verify_workbook(path, expectations)
    checks.to_csv(out / "workbook_formula_verification.csv", index=False)
    failures = checks[~checks.passes]
    print(f"formula verification: {int(checks.passes.sum())}/{len(checks)} pass")
    if len(failures):
        print(failures.head(15).to_string(index=False))
        return 1

    scan = scan_workbook_structure(path, expectations)
    scan.to_csv(out / "workbook_sheet_scan.csv", index=False)
    print(f"structural scan: {int(scan.passes.sum())}/{len(scan)} sheets populated with no "
          f"Excel error literals (static check; not a render test)")
    print(scan[["sheet", "is_presentation_tab", "max_row", "populated_cells",
                "formula_cells_verified", "excel_error_cells"]].to_string(index=False))
    if not scan.passes.all():
        return 1

    figures = build_figures(out, tables, run)
    print(f"\nfigures written: {len(figures)} (maximum permitted 5)")
    for figure in figures:
        print(f"  {figure.name}")
    if len(figures) > 5:
        raise AssertionError("the brief permits at most five figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
