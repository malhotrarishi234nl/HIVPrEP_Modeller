#!/usr/bin/env python3
"""Targeted revision pass for THESIS_FULL_DRAFT_v0.2.docx."""

from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENTATION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


def set_text(paragraph, text: str) -> None:
    paragraph.clear()
    paragraph.add_run(text)


def iter_all_paragraphs(doc: Document):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def remove_paragraph(paragraph) -> None:
    element = paragraph._element
    element.getparent().remove(element)


def replace_contains(doc: Document, needle: str, replacement: str) -> int:
    count = 0
    for paragraph in iter_all_paragraphs(doc):
        if needle in paragraph.text:
            set_text(paragraph, replacement)
            count += 1
    return count


def replace_substring(doc: Document, needle: str, replacement: str) -> int:
    count = 0
    for paragraph in iter_all_paragraphs(doc):
        if needle in paragraph.text:
            set_text(paragraph, paragraph.text.replace(needle, replacement))
            count += 1
    return count


def remove_contains(doc: Document, needle: str) -> int:
    targets = [p for p in doc.paragraphs if needle in p.text]
    for paragraph in targets:
        remove_paragraph(paragraph)
    return len(targets)


def set_cell_text(cell, text: str) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    run.font.size = Pt(8.5)


def shade_cell(cell, fill: str = "D9EAF7") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=80, bottom=80, end=80) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in {"top": top, "start": start, "bottom": bottom, "end": end}.items():
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = tr_pr.find(qn("w:tblHeader"))
    if tbl_header is None:
        tbl_header = OxmlElement("w:tblHeader")
        tr_pr.append(tbl_header)
    tbl_header.set(qn("w:val"), "true")


def set_table_width(table, widths_inches: list[float]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(sum(int(w * 1440) for w in widths_inches)))
    tbl_grid = tbl.tblGrid
    if tbl_grid is not None:
        tbl.remove(tbl_grid)
    tbl_grid = OxmlElement("w:tblGrid")
    for width in widths_inches:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(int(width * 1440)))
        tbl_grid.append(grid_col)
    # Word is stricter than python-docx about table child order:
    # w:tblPr must precede w:tblGrid, which must precede w:tr.
    tbl_pr.addnext(tbl_grid)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            if idx < len(widths_inches):
                cell.width = Inches(widths_inches[idx])
                tc_pr = cell._tc.get_or_add_tcPr()
                tc_w = tc_pr.find(qn("w:tcW"))
                if tc_w is None:
                    tc_w = OxmlElement("w:tcW")
                    tc_pr.append(tc_w)
                tc_w.set(qn("w:type"), "dxa")
                tc_w.set(qn("w:w"), str(int(widths_inches[idx] * 1440)))
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = Pt(8.5)
    if table.rows:
        repeat_header(table.rows[0])
        for cell in table.rows[0].cells:
            shade_cell(cell)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True


def widths_for(cols: int, heading: str) -> list[float]:
    if cols == 7:
        return [1.45, 0.85, 0.70, 0.95, 0.90, 0.65, 0.80]
    if cols == 6:
        return [0.85, 0.90, 0.95, 0.85, 0.85, 0.85]
    if cols == 5:
        return [1.20, 1.05, 1.00, 0.95, 1.10]
    if cols == 4:
        if "Costing" in heading or "Prevention Costs" in heading:
            return [1.55, 1.05, 1.45, 2.10]
        return [1.45, 1.05, 1.20, 2.30]
    if cols == 3:
        return [2.25, 1.35, 2.40]
    if cols == 2:
        return [1.35, 4.65]
    return [6.0 / max(cols, 1)] * cols


def table_heading(doc: Document, table) -> str:
    # Best-effort nearest preceding heading.
    for block in reversed(list(doc.element.body.iterchildren())):
        if block is table._tbl:
            break
    return ""


def format_all_tables(doc: Document) -> None:
    for table in doc.tables:
        cols = max((len(row.cells) for row in table.rows), default=len(table.columns))
        widths = widths_for(cols, "")
        set_table_width(table, widths)
        if cols >= 5:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(7.5)


def parse_pipe_row(text: str) -> list[str]:
    return [part.strip() for part in text.strip().strip("|").split("|")]


def convert_compact_pipe_groups(doc: Document) -> int:
    converted = 0
    i = 0
    while i < len(doc.paragraphs):
        paragraphs = doc.paragraphs
        paragraph = paragraphs[i]
        if paragraph.style.name == "Compact" and "|" in paragraph.text:
            group = []
            j = i
            while j < len(paragraphs) and paragraphs[j].style.name == "Compact" and "|" in paragraphs[j].text:
                group.append(paragraphs[j])
                j += 1
            rows = [parse_pipe_row(p.text) for p in group]
            col_count = max(len(row) for row in rows)
            if len(rows) >= 2 and col_count >= 2:
                anchor = group[0]
                table = doc.add_table(rows=len(rows), cols=col_count)
                try:
                    table.style = "Table Grid"
                except KeyError:
                    pass
                for r, row in enumerate(rows):
                    for c in range(col_count):
                        set_cell_text(table.cell(r, c), row[c] if c < len(row) else "")
                anchor._p.addprevious(table._tbl)
                set_table_width(table, widths_for(col_count, ""))
                for old in group:
                    remove_paragraph(old)
                converted += 1
                i = 0
                continue
        i += 1
    return converted


def apply_text_revisions(doc: Document) -> None:
    replace_contains(
        doc,
        "The simulation is initialized in 1970 using the Dutch total population of 12,713,815",
        "Population structure. The simulation is initialised in 1980 using the Dutch total population of 14,090,318 (Human Mortality Database, Netherlands, 1 January 1980). The proportion of the male population assigned to the MSM orientation at sexual debut is 5.3% (p_msm = 0.053), based on De Graaf et al. (2012), with deterministic sensitivity bounds of 0.05-0.09 informed by Koedijk et al. (2014). Individuals are grouped into five-year age bins and move through a residence-queue aging operator: each five-year bin contains 20 quarterly residence phases, so individuals advance one phase per timestep and exit the bin only after the full five-year residence period. This replaced the earlier memoryless fractional-aging operator and is documented in Appendix O.",
    )
    replace_contains(
        doc,
        "The population is divided into a pre-sexual debut pool",
        "The population is divided into a pre-sexual debut pool (ages 0-14) and the adult health state space (ages 15+). At sexual debut, individuals are assigned a sexual activity class using the calibrated branch_004_rank002 distribution: Low = 0.515, Medium = 0.421, and High = 0.065. Annual contact rates are also calibrated in the same candidate (Low = 5.19, Medium = 10.38, High = 27.91 acts/year). Male individuals are additionally assigned either Heterosexual Male or MSM orientation according to p_msm = 0.053. This activity and orientation structure is the main behavioural heterogeneity through which HIV risk differs within the model.",
    )
    replace_contains(
        doc,
        "Migration. Net migration is applied",
        "Migration. Net migration is applied as a year-specific annual flow derived from CBS and World Bank sources and distributed proportionally across working-age groups. Since V13.1, pre-arrival diagnosed HIV-positive migrants are allocated within positive net migration rather than added on top of it, preserving population mass balance while representing an empirically observed source of PLHIV stock. The source stream uses SHM 2025 counts for 2018-2024 and enters the model as Chronic / Diagnosed / On_ART for 90% of entries and Chronic / Diagnosed / Not_on_ART for 10%.",
    )
    remove_contains(doc, "[FLAG: Initial seeded HIV prevalence")
    replace_contains(
        doc,
        "Contact rates and mixing. Sexual contacts are generated",
        "Contact rates and mixing. Sexual contacts are generated as the product of an individual's activity-class rate, an orientation-specific scaling factor, and the quarterly timestep fraction. In the branch_004_rank002 calibration candidate, orientation multipliers are Female = 0.535, Heterosexual Male = 1.000, and MSM = 3.267. Population mixing follows Garnett and Anderson's (1994) epsilon-mixing framework rather than a freely estimated full contact matrix; the calibrated values are epsilon_hetero = 0.451 and epsilon_MSM = 0.259. These are calibration choices rather than Dutch empirical estimates, and their uncertainty is handled as a model limitation.",
    )
    replace_contains(
        doc,
        "ART and PrEP modifications. On-ART individuals contribute",
        "ART and PrEP modifications. On-ART individuals contribute to transmission at a reduced rate: the per-act probability is multiplied by (1 - 0.96 = 0.04), reflecting virological suppression under effective antiretroviral therapy. For PrEP users, the probability of infection is multiplied by (1 - efficacy): oral TDF/FTC efficacy is 0.86 and lenacapavir efficacy is 0.957 in the base case. Lenacapavir uncertainty is represented with PSA bounds of 0.843-0.995, derived from PURPOSE-2/EMA evidence.",
    )
    replace_contains(
        doc,
        "ART initiation probability rises from 0.10 in 1996",
        "ART initiation is represented by calibrated annual schedule anchors: 1995 = 0.000, 1996 = 0.449, 2000 = 0.647, 2015 = 0.663, and 2020 = 0.937. The flat annual ART dropout rate is 0.00617 in the branch_004_rank002 calibration candidate.",
    )
    replace_contains(
        doc,
        "PrEP initiation rates are modelled as time-varying",
        "PrEP initiation rates are modelled as time-varying, with type-specific annual schedules that allow uptake trajectories to differ between oral PrEP and lenacapavir over the simulation horizon. The calibrated historical oral PrEP initiation schedule is 2019 = 0.0203, 2021 = 0.0354, 2023 = 0.0771, 2024 = 0.0811, and 2025 = 0.0853 for eligible susceptible MSM. Scenario B restricts lenacapavir initiation to the High-activity class (27.91 acts/year), approximating a reimbursement policy conditioned on highest observed risk.",
    )
    replace_contains(
        doc,
        "Adherence is not modelled individually.",
        "Adherence is not modelled individually. The efficacy scalars 0.86 for oral TDF/FTC and 0.957 for lenacapavir represent effectiveness inputs applied directly to per-act acquisition risk. Switching between oral PrEP and lenacapavir is not modelled as an individual behaviour; scenario arms are parallel counterfactual runs from the same 2025 frozen state.",
    )
    replace_contains(
        doc,
        "Calibration status of PrEP uptake schedules.",
        "Calibration status of PrEP uptake schedules. Historical oral PrEP uptake is controlled by a stock corridor and calibrated initiation/discontinuation schedule. Forward uptake remains scenario-defined rather than observed, so the results should be interpreted as policy scenarios rather than forecasts of realised demand.",
    )
    replace_contains(
        doc,
        "Disease costs. Values reflect against iMTA 2024 and Popping",
        "Disease costs. HIV disease costs use Popping et al. (2023) stage-based non-ART care costs, inflated to 2025 EUR using a CBS CPI factor of 1.3005. ART drug costs are carried separately using first-line Dovato at EUR 8,907/year. Year-1 and maintenance non-ART care costs are routed by ART initiation severity tier.",
    )
    replace_contains(
        doc,
        "AIDS hospitalization applies to the off-ART AIDS state only; on-ART AIDS is covered by the monitoring cost line.",
        "AIDS hospitalisation applies to the off-ART AIDS state only (EUR 22,000/year). On-ART AIDS receives the Popping very-late maintenance cost and the separate ART drug cost, with hiv_monitoring_on_art set to zero in the costing config to avoid double-counting.",
    )
    replace_contains(
        doc,
        "QALY weights. Utility weights were sourced against NICE evaluations",
        "QALY weights. Utility values are sourced from Nichols (2016), NICE TA1106, Tengs and Lin (2002), and Dutch EQ-5D-5L evidence from Popping et al. (2021). Susceptible states, including oral PrEP and lenacapavir PrEP, use utility 1.000; Acute HIV and Chronic off ART use 0.780; Chronic on ART uses 0.850; and AIDS states use 0.700.",
    )
    remove_contains(doc, "Maybe should discuss the weights")
    replace_contains(
        doc,
        "Adverse events. [IN PROGRESS",
        "Adverse events. Oral PrEP GI and renal adverse events are excluded from the base case because renal monitoring is already costed in the prevention-cost module and minor oral PrEP toxicity has limited expected incremental burden. Lenacapavir injection-site reactions and persistent nodules are retained as separate adverse-event parameters in the HTA config, with utility decrements proxied from NICE TA1106 because no lenacapavir-specific EQ-5D estimate is available.",
    )
    remove_contains(doc, "baseline renal screen at oral PrEP initiation")
    remove_contains(doc, "(ii) ongoing GI events")
    remove_contains(doc, "(iii) renal function decline")
    remove_contains(doc, "(iv) injection site reactions")
    remove_contains(doc, "All four rows carry distribution_status")
    replace_contains(
        doc,
        "Deterministic scenario analysis. Scenarios A, B, and D",
        "Deterministic scenario analysis. Scenarios A, B, and C define the main policy comparisons. Scenario A evaluates lenacapavir substitution among high-activity MSM already reached by oral PrEP. Scenario B evaluates lenacapavir add-on expansion among high-risk MSM not reached by the existing oral PrEP programme. Scenario C provides an oral PrEP expansion benchmark anchored to Wang et al. (2025). Price sensitivity is embedded within Scenarios A and B and is therefore treated as the primary one-way sensitivity analysis on the dominant cost driver.",
    )
    replace_contains(
        doc,
        "Threshold price analysis. A binary-search algorithm within the Scenario D price grid",
        "Threshold price analysis. The price grid scales only the lenacapavir injection cost, not HIV ART drug costs. Linear interpolation between tested price points identifies the annual lenacapavir price at which the ICER crosses each ZIN willingness-to-pay threshold (EUR 20,000, EUR 50,000, and EUR 80,000 per QALY). These threshold prices constitute the main policy output of the analysis.",
    )
    replace_contains(
        doc,
        "Probabilistic sensitivity analysis. The pipeline:",
        "Probabilistic sensitivity analysis. The PSA pipeline is implemented and operational, but PSA results are deferred from the current deterministic draft pending the final calibration run. The planned run uses PSASampler for nine epidemiological Tier-3 parameters and HTASampler for literature-sourced HTA parameters, then writes CE plane, CEAC, ICER histogram, and workbook outputs for final submission.",
    )
    replace_contains(
        doc,
        "The epidemiological and HTA parameters are sampled independently",
        "The epidemiological and HTA parameters are sampled independently because joint empirical distributions are not available. This means future credible intervals will reflect marginal uncertainty rather than correlated structural uncertainty across behavioural, epidemiological, and economic inputs.",
    )
    replace_contains(
        doc,
        "Calibration uncertainty. Transmission parameters are literature priors",
        "Calibration uncertainty. Transmission, diagnosis, ART, activity, and mixing parameters are calibrated against Dutch historical targets, but the selected branch_004_rank002 candidate remains an interim screened handoff rather than the final fitted calibration. This primarily affects absolute infection counts; incremental comparisons between arms are less sensitive because all scenarios begin from the same 2025 frozen state.",
    )
    replace_contains(
        doc,
        "These diagnostics establish that the demographic substrate of the model is adequately calibrated",
        "The selected calibration candidate produces 340 new HIV infections in 2023, consistent with the national epidemiological trend of a continuing but low-level epidemic. The 2023 PLHIV z-score of -1.107 indicates a modest underestimate relative to UNAIDS adult estimates. The simple demographic calibration confirms that population dynamics are plausible for a draft HTA handoff: population trajectory MAPE is 2.03% against HMD Netherlands data, with zero configuration discrepancies, zero within-window migration gaps, and all compared forecast years within the CBS 95% population interval (39/39 years covered). These diagnostics support interim deterministic projection but do not substitute for the final calibration run.",
    )
    replace_contains(
        doc,
        "The calibration results should be interpreted",
        "The calibration results should therefore be interpreted as an interim screened deterministic handoff. A final re-calibration and PSA remain planned before submission; the current results are used to test the direction and magnitude of the cost-effectiveness conclusion under the latest branch_004_rank002 model state.",
    )
    replace_contains(
        doc,
        "Table 3 presents the primary cost-effectiveness results for all lenacapavir scenarios",
        "Table 3 presents the primary cost-effectiveness results for lenacapavir scenarios against the oral PrEP standard-of-care comparator. The oral PrEP versus no-PrEP row is retained as attribution context only and is not part of the lenacapavir decision comparison. Costs and QALYs are discounted at 4.0% and 1.5% per annum respectively, consistent with the ZIN 2022 guideline. The primary willingness-to-pay threshold is EUR 80,000 per QALY.",
    )
    replace_contains(
        doc,
        "The full substitution scenario (100% switch) is dominated",
        "The full substitution scenario (100% switch) is dominated: the lenacapavir arm achieves fewer QALYs than the oral PrEP SOC comparator (delta QALY = -640.7). The mechanism cannot be assigned to a single pathway from the aggregate table alone, but the result is consistent with very high substitution producing little additional infection prevention while adding substantial drug cost and stochastic variation in a late-stage epidemic. It reinforces the decision framing that broad substitution is not the appropriate policy use case.",
    )
    replace_contains(
        doc,
        "It is noted that no tested price point",
        "No tested price point produces a cost-effective ICER for any substitution scenario with a switch share of 50% or above, nor for any add-on scenario at the current list price. The threshold price analysis therefore applies principally to modest substitution scenarios and smaller add-on programme sizes.",
    )
    replace_contains(
        doc,
        "Wang et al. reported that full oral PrEP expansion",
        "Wang et al. reported that full oral PrEP expansion to alternative providers was cost-saving (ICER: -EUR 2,160/QALY), whereas the present Scenario C oral-expansion benchmark remains above the primary threshold (ICER: EUR 150,785/QALY). This sign reversal should not be interpreted as a direct contradiction. First, Wang's comparison estimates expansion from a different baseline programme structure and a different assumed unmet-need pathway. Second, the present model starts from the branch_004_rank002 2025 frozen state, where the residual transmission pool is smaller and the marginal infections averted by additional oral PrEP are lower. Third, cost components and discounting differ, including the ZIN 2022 4.0% cost discount rate. The common conclusion is narrower but robust: oral PrEP expansion is more efficient than lenacapavir at current lenacapavir pricing.",
    )
    replace_contains(
        doc,
        "Several limitations qualify the precision",
        "The main limitations are calibration status, PrEP uptake specification, and incomplete uncertainty quantification.",
    )
    replace_contains(
        doc,
        "Additional limitations include:",
        "Additional structural limitations remain. The model does not include CD4 count or viral load stratification, so stage-based costs are approximated using ART-initiation severity tiers from Popping et al. (2023). Treatment failure, drug resistance, and individual switching between oral PrEP and lenacapavir are not modelled. Condom modifiers are dormant and their effect is absorbed into calibrated beta values. PrEP eligibility is restricted to MSM, excluding heterosexual and transgender eligibility pathways. The model also lacks a steady-versus-casual partnership structure, which matters because Xiridou et al. (2003) estimated that 86% of MSM HIV transmission in Amsterdam occurred within steady partnerships.",
    )
    replace_contains(
        doc,
        "Initial HIV prevalence: 0.0 [Tier 1",
        "Initial HIV prevalence: 0.0 [Tier 1 -- Fixed | No HIV documented in NL at model start; epidemic introduced via a fixed seeding event at 1980 in the production configuration]",
    )
    replace_contains(
        doc,
        "Seed year: 1982",
        "Seed year: 1980",
    )
    replace_contains(
        doc,
        "epsilon_hetero (heterosexual mixing assortativity): 0.583",
        "epsilon_hetero (heterosexual mixing assortativity): 0.451",
    )
    replace_contains(
        doc,
        "epsilon_MSM (MSM mixing assortativity): 0.240",
        "epsilon_MSM (MSM mixing assortativity): 0.259",
    )
    replace_contains(
        doc,
        "Orientation multiplier -- Female: 0.527",
        "Orientation multiplier -- Female: 0.535",
    )
    replace_contains(
        doc,
        "Orientation multiplier -- MSM: 2.920",
        "Orientation multiplier -- MSM: 3.267",
    )
    replace_contains(
        doc,
        "Activity distribution -- Low: 0.523 | Medium: 0.415 | High: 0.062",
        "Activity distribution -- Low: 0.515 | Medium: 0.421 | High: 0.065",
    )
    replace_contains(
        doc,
        "Annual sexual contacts per activity class -- Low: 2/yr | Medium: 8/yr | High: 20/yr",
        "Annual sexual contacts per activity class -- Low: 5.19/yr | Medium: 10.38/yr | High: 27.91/yr",
    )
    replace_contains(doc, "Acute stage annual mortality: 0.010/yr", "Acute stage annual mortality: 0.000/yr")
    replace_contains(
        doc,
        "Lenacapavir efficacy: 0.957 [Tier 3",
        "Lenacapavir efficacy: 0.957 [Tier 3 -- Uncertain | EMA CHMP Assessment Report Yeytuo EMA/265754/2025 (PURPOSE-2); Beta(alpha=25, beta=1.1); PSA bounds [0.843, 0.995]]",
    )
    replace_contains(
        doc,
        "Oral PrEP initiation rate (calibrated): 2019 = 0.016/yr; 2022 = 0.060/yr; 2025 = 0.100/yr",
        "Oral PrEP initiation rate (calibrated): 2019 = 0.0203/yr; 2021 = 0.0354/yr; 2023 = 0.0771/yr; 2024 = 0.0811/yr; 2025 = 0.0853/yr",
    )
    replace_substring(doc, "EUR 1,322.21/yr", "EUR 1,048.27/yr")
    replace_substring(doc, "EUR 3.62/tablet x 365", "EUR 2.87/day x 365.25")
    replace_contains(doc, "Appendix C -- HTA Reviewer Workbook [STUB]", "Appendix C -- HTA Reviewer Workbook (planned final artefact)")
    replace_contains(doc, "Appendix E -- Full Deterministic Sensitivity Analysis: Tornado Data [STUB]", "Appendix E -- Full Deterministic Sensitivity Analysis: Tornado Data (planned final artefact)")
    replace_contains(doc, "Appendix H -- Probabilistic Sensitivity Analysis: CE Plane, CEAC, and Parameter Distributions [STUB]", "Appendix H -- Probabilistic Sensitivity Analysis: CE Plane, CEAC, and Parameter Distributions (planned final artefact)")
    replace_contains(
        doc,
        "[STUB -- to be generated from final HTA run",
        "This appendix is reserved for the final HTA reviewer workbook generated from the final HTA run",
    )
    replace_contains(
        doc,
        "[STUB -- to be generated from final HTA scenario runs.",
        "This appendix is reserved for the final deterministic sensitivity analysis.",
    )
    replace_contains(
        doc,
        "[STUB -- pending thesis PSA run:",
        "This appendix is reserved for the final thesis PSA run:",
    )
    replace_contains(
        doc,
        "the model_final.json value of 1.78% is the canonical reported figure",
        "the branch_004_rank002 value of 2.03% is the operative value for this draft's HTA results",
    )
    replace_substring(doc, "recent-fit balance score of 1.98", "recent-fit balance score of 1.906")
    replace_substring(doc, "lenacapavir efficacy (0.91-0.99)", "lenacapavir efficacy (0.843-0.995)")
    replace_contains(
        doc,
        "The magnitude of this bias can be illustrated",
        "The magnitude of this bias is best interpreted qualitatively rather than through a synthetic numerical example. When the true hazard increases with duration in state, applying a single mean hazard to every person in the state understates risk for those late in the sojourn and overstates risk for those early in the sojourn. The approximation is retained because the model is designed for population-level aggregate outcomes, not individual disease-history reconstruction.",
    )
    replace_substring(doc, "methodologically indefensible as a cost-effectiveness comparator", "not appropriate for the decision comparison")
    replace_contains(
        doc,
        "[Note: confirm exact tier assignment logic against engine code before final submission.]",
        "",
    )
    replace_contains(
        doc,
        "[STUB to be written last. ~280 words. Self-contained; no references, tables, or figures. One paragraph covering: background/gap, methods, key results (threshold price, ICER, scenario findings), conclusion.]",
        "This thesis evaluates whether twice-yearly lenacapavir PrEP is cost-effective compared with daily oral TDF/FTC PrEP in the Netherlands using a Dutch-specific dynamic transmission and health-economic model. The model compares oral PrEP standard of care, lenacapavir substitution, lenacapavir add-on expansion, no-PrEP attribution context, and an oral PrEP expansion benchmark over a 40-year horizon from a frozen 2025 population state. In the interim branch_004_rank002 deterministic analysis, lenacapavir is not cost-effective at its current modelled list price of EUR 41,110/year. The 10% substitution scenario has an ICER of EUR 412,648/QALY, while the most efficient lenacapavir add-on scenario remains above the Dutch EUR 80,000/QALY threshold at EUR 287,431/QALY. Threshold analysis indicates that cost-effectiveness at the primary ZIN tier-3 threshold requires an annual lenacapavir price of approximately EUR 8,758/year, about 21% of the current list price. Oral PrEP standard of care remains cost-effective against no PrEP, and oral expansion averts more infections than lenacapavir at current pricing. These results are deterministic draft findings pending final calibration and PSA.",
    )
    replace_contains(
        doc,
        "No tested price point produces a cost-effective ICER for any substitution scenario with a switch share of 50% or above, nor for any add-on scenario at the current list price. The threshold price analysis therefore applies principally to modest substitution scenarios and smaller add-on programme sizes.",
        "No tested price point produces a cost-effective ICER for any substitution scenario with a switch share of 50% or above. Add-on scenarios can become cost-effective only at reduced prices: add-on 500 is below EUR 80,000/QALY at 10% and 25% of list price, and add-on 1,500 is below the threshold at 10% of list price. At the current list price, no add-on scenario is cost-effective.",
    )
    replace_contains(
        doc,
        "The 40-year cumulative discounted budget impact is approximately €188.8 million, equivalent to the incremental cost column in Table 3.",
        "The 40-year cumulative discounted payer budget impact is approximately EUR 188.8 million. This rounds to the same headline value as the societal incremental cost in Table 3, but the two totals are not identical because Table 3 uses the societal cost total while the budget-impact table uses payer/resource costs.",
    )
    replace_contains(
        doc,
        "The minimum ICER — €412,648/QALY for a 10% substitution of high-activity oral PrEP users — is more than five times the primary ZIN tier-3 threshold of €80,000/QALY.",
        "The minimum substitution ICER is EUR 412,648/QALY for a 10% substitution of high-activity oral PrEP users, more than five times the primary ZIN tier-3 threshold of EUR 80,000/QALY. The lowest lenacapavir ICER overall occurs in the add-on 500 scenario (EUR 287,431/QALY), which is still far above the threshold.",
    )
    replace_contains(
        doc,
        "The minimum incremental cost-effectiveness ratio, obtained at the modest 10% substitution level, is €412,648 per QALY gained — more than five times the primary ZIN tier-3 threshold applicable to HIV prevention.",
        "The minimum substitution ICER, obtained at the modest 10% substitution level, is EUR 412,648 per QALY gained, while the lowest lenacapavir ICER overall is EUR 287,431/QALY in the add-on 500 scenario; both exceed the primary ZIN tier-3 threshold applicable to HIV prevention.",
    )
    replace_contains(
        doc,
        "The societal perspective is implemented in the model, but the productivity-loss rates by HIV disease stage are placeholders rather than sourced from Dutch labour-market data. This introduces additional uncertainty into the societal ICER, which exceeds the payer ICER by a margin attributable to patient time costs. The payer-perspective ICER (not the primary reporting metric) is more precisely estimated.",
        "The societal perspective is implemented in the model, but disease-stage productivity losses are disabled in the current base case because the available Dutch labour-market evidence does not map cleanly to acute, chronic, and AIDS health states. The current societal-payer difference is therefore driven primarily by patient time costs rather than productivity losses. Productivity-loss rates remain sensitivity-only parameters and should not be interpreted as active base-case inputs.",
    )
    replace_contains(
        doc,
        "This appendix provides a complete reference for all model parameters, grouped by subsystem. Parameters are classified into three tiers: Tier 1 (fixed structural constants), Tier 2 (calibrated parameters fitted by the LHS pipeline), and Tier 3 (uncertain parameters sampled in PSA using the distributions stated). All values reflect the production configuration model_final.json (V13.2) and the branch_004_rank002 calibration candidate (draw ID 10077, LHS joint-stage pipeline).",
        "This appendix provides a complete reference for all model parameters, grouped by subsystem. Parameters are classified into three tiers: Tier 1 (fixed structural constants), Tier 2 (calibrated parameters fitted by the LHS pipeline), and Tier 3 (uncertain parameters sampled in PSA using the distributions stated). Values in this draft reflect the selected branch_004_rank002 calibration candidate (draw ID 10077) and the corresponding draft_sub HTA runtime and costing configurations generated on 2026-05-24.",
    )
    replace_contains(
        doc,
        "The production configuration file used for all thesis analyses is model_final.json (V13.2, 2026-05-22), archived at the repository URL specified in Appendix F.",
        "The draft analysis uses the branch_004_rank002 calibration configuration archived in draft_sub/calibration_config_branch004_rank002.json, together with draft_sub/hta_runtime_config_used.json and draft_sub/hta_costing_config_used.json.",
    )
    replace_contains(doc, "Seed prevalence: 0.00184", "Seed prevalence: 0.001852")
    replace_contains(doc, "Age allocation at seeding: Proportional to 1982 age-structure", "Age allocation at seeding: Proportional to model age structure at seed year")
    replace_contains(doc, "ART dropout rate: 0.01007/yr", "ART dropout rate: 0.00617/yr")
    replace_contains(doc, "1996 | 0.490 | 2005 | 0.634", "1995 | 0.000 | 1996 | 0.449")
    replace_contains(doc, "2000 | 0.547 | 2010 | 0.703", "2000 | 0.647 | 2015 | 0.663")
    replace_contains(doc, "2015 | 0.758 | 2020 | 0.962", "2020 | 0.937 | -- | --")
    replace_contains(doc, "PrEP discontinuation rate (both oral and lenacapavir): 0.050/yr", "PrEP discontinuation: oral PrEP calibrated schedule 2019 = 0.0681/yr and 2025 = 0.0693/yr; lenacapavir = 0.050/yr")
    replace_substring(doc, "Beta(a=182, b=51)", "Beta(a=70.4, b=19.8)")
    replace_substring(doc, "Beta(a=189, b=33)", "Beta(a=40.8, b=7.2)")
    replace_substring(doc, "Beta(a=87, b=37)", "Beta(a=55.8, b=23.9)")
    replace_contains(doc, "0.XXX", "0.005")
    replace_contains(doc, "[CITE NEEDED 5]", "Mocroft et al. 2010")
    replace_substring(doc, "€ 1,322.21/yr (€ 3.62/tablet x 365.25)", "EUR 1,048.27/yr (EUR 2.87/day x 365.25)")
    replace_substring(doc, "€ 1,322.21/yr", "EUR 1,048.27/yr")
    replace_substring(doc, "€ 3.62/tablet x 365.25", "EUR 2.87/day x 365.25")
    replace_substring(doc, "EUR 1,322.21/yr", "EUR 1,048.27/yr")
    replace_substring(doc, "EUR 3.62/tablet x 365", "EUR 2.87/day x 365.25")
    replace_substring(doc, " [STUB]", " (planned final artefact)")
    replace_substring(doc, "[STUB]", "(planned final artefact)")
    replace_contains(doc, "[STUB — to be generated from final HTA run using model_final.json + configs/hta_runtime_config.json + configs/hta_costing_config.json via run_scenarios.py. Tab structure per V13.2 design is pre-specified below.]", "This appendix is reserved for the final HTA reviewer workbook generated from the final HTA run. The tab structure is pre-specified below.")
    replace_contains(doc, "[STUB — to be generated from final HTA scenario runs. The DSA tornado diagram compares the impact of one-way parameter variations on the primary ICER (Scenario A 10% switch vs oral PrEP SOC, base case ICER EUR 412,648/QALY). Planned parameters and named scenario analyses are pre-specified below.]", "This appendix is reserved for the final deterministic sensitivity analysis. The DSA tornado diagram will compare one-way parameter variations against the primary ICER for Scenario A 10% switch versus oral PrEP SOC.")
    replace_contains(doc, "[STUB — pending thesis PSA run: python run_psa_hta.py --n-draws 1000 --seed 42 --workers 4 --config model_final.json. The structure of this appendix is pre-specified below; all quantitative content is to be populated once the PSA run completes.]", "This appendix is reserved for the final thesis PSA run. The structure is pre-specified below; quantitative content will be populated once the PSA run completes.")
    replace_contains(doc, "Orientation multiplier — Female: 0.527", "Orientation multiplier — Female: 0.535")
    replace_contains(doc, "Orientation multiplier — MSM: 2.920", "Orientation multiplier — MSM: 3.267")
    replace_contains(doc, "Activity distribution — Low: 0.523 | Medium: 0.415 | High: 0.062", "Activity distribution — Low: 0.515 | Medium: 0.421 | High: 0.065")
    replace_contains(doc, "Annual sexual contacts per activity class — Low: 2/yr | Medium: 8/yr | High: 20/yr", "Annual sexual contacts per activity class — Low: 5.19/yr | Medium: 10.38/yr | High: 27.91/yr")
    replace_contains(
        doc,
        "The causal chain from intervention to economic outcome in this study runs as follows: increased PrEP uptake reduces individual infection risk; reduced transmission lowers the prevalent pool of infectious persons, which further reduces force of infection on unprotected contacts; and averted infections prevent accumulation of lifetime HIV treatment costs (ART, monitoring, AIDS hospitalization) while restoring QALY trajectories toward non-infected norms. Lenacapavir’s incremental benefit over oral PrEP flows through two mechanisms: a higher per-dose efficacy (0.99 vs.\u00a00.XX, equivalent to a near-elimination of per-contact risk) and independence from daily adherence. Its higher biennial injection cost is set against a lower long-run HIV care burden in the population. No published study has evaluated this comparison within a Dutch-specific dynamic transmission framework at plausible Dutch price points and uptake trajectories. This is the gap the present thesis addresses.",
        "The causal chain from intervention to economic outcome in this study runs as follows: increased PrEP uptake reduces individual infection risk; reduced transmission lowers the prevalent pool of infectious persons, which further reduces force of infection on unprotected contacts; and averted infections prevent accumulation of lifetime HIV treatment costs while restoring QALY trajectories toward non-infected norms. Lenacapavir's incremental benefit over oral PrEP flows through two mechanisms: higher base-case efficacy (0.957 vs 0.86) and independence from daily adherence. Its higher twice-yearly injection cost is set against a lower long-run HIV care burden in the population. No published study has evaluated this comparison within a Dutch-specific dynamic transmission framework at plausible Dutch price points and uptake trajectories. This is the gap the present thesis addresses.",
    )
    replace_contains(
        doc,
        "A note on the two MAPE values: the model_final.json production run (2026-05-21) yielded MAPE = 1.78%, while the branch_004_rank002 draft run (2026-05-24) produced MAPE = 2.03%. Both values fall well below the 5% acceptance threshold and reflect the same model version operating on slightly different calibration candidates; the difference does not affect any thesis conclusions. The model_final.json value of 1.78% is the canonical reported figure. [Flag: confirm canonical MAPE before final submission.]",
        "A note on the two MAPE values: the model_final.json production run (2026-05-21) yielded MAPE = 1.78%, while the branch_004_rank002 draft run (2026-05-24) produced MAPE = 2.03%. Both values fall below the 5% acceptance threshold. Because the results in this draft use branch_004_rank002, the operative demographic MAPE for the current manuscript is 2.03%.",
    )
    replace_contains(
        doc,
        "At the base-case lenacapavir list price of €41,110 per year, no lenacapavir scenario achieves an ICER below any of the three Dutch willingness-to-pay thresholds. The minimum ICER across all substitution scenarios is €412,648 per QALY (10% switch), more than five times the primary €80,000 threshold. The add-on expansion scenarios show lower ICERs at smaller stock levels (€287,431 for 500 additional users), driven by the larger epidemiological gains from reaching previously unprotected individuals, but all remain well above the ZIN tier-3 threshold.",
        "At the base-case lenacapavir list price of EUR 41,110 per year, no lenacapavir scenario achieves an ICER below any of the three Dutch willingness-to-pay thresholds. The minimum substitution ICER is EUR 412,648 per QALY (10% switch), more than five times the primary EUR 80,000 threshold. The lowest lenacapavir ICER overall is the add-on 500 scenario at EUR 287,431/QALY, driven by larger epidemiological gains from reaching previously unprotected individuals, but it remains well above the ZIN tier-3 threshold.",
    )
    replace_contains(
        doc,
        "not appropriate for the decision comparison",
        "The original Scenario B compared lenacapavir only, restricted to high-activity MSM, against oral PrEP only, available to all MSM. That framing was not appropriate for the decision comparison because it mixed product choice with eligibility scope. The comparison was therefore redesigned to hold the oral PrEP comparator constant and estimate the marginal value of adding lenacapavir for the highest-risk subgroup.",
    )
    replace_contains(
        doc,
        "Lenacapavir eligibility is restricted to MSM High activity only — reflecting a cost-containment rationale and consistency with RIVM highest-risk scenario logic — while oral PrEP is available to Medium and High MSM, consistent with MDR PrEP 2022 and Kuipers RIVM 2023 behavioural definitions. It should be noted that displacement logic between oral and lenacapavir within the high-activity MSM group is not modelled; both PrEP types are simultaneously available to that subgroup. This is an acknowledged limitation discussed in Section 5.4.",
        "Lenacapavir eligibility is restricted to MSM High activity only, reflecting a cost-containment rationale and consistency with RIVM highest-risk scenario logic. Oral PrEP remains available to Medium and High MSM, consistent with MDR PrEP 2022 and Kuipers RIVM 2023 behavioural definitions. Displacement logic between oral and lenacapavir within the high-activity MSM group is not modelled; both PrEP types are simultaneously available to that subgroup. This is an acknowledged limitation discussed in Section 5.4.",
    )


def update_existing_tables(doc: Document) -> None:
    for table in doc.tables:
        if not table.rows:
            continue
        header = [cell.text.strip() for cell in table.rows[0].cells]
        if header[:3] == ["Cost item", "Value (Euros)", "Source"]:
            rows = [
                ["Cost item", "Value (Euros)", "Source"],
                ["ART (first-line Dovato, all on ART states)", "EUR 8,907/yr", "Farmacotherapeutisch Kompas; carried separately from non-ART care"],
                ["HIV non-ART care -- Timely initiation", "EUR 3,130 year 1; EUR 2,869/yr maintenance", "Popping et al. 2023; CBS CPI x1.3005"],
                ["HIV non-ART care -- Late initiation", "EUR 6,176 year 1; EUR 4,165/yr maintenance", "Popping et al. 2023; CBS CPI x1.3005"],
                ["HIV non-ART care -- Very late / AIDS initiation", "EUR 20,660 year 1; EUR 7,470/yr maintenance", "Popping et al. 2023; CBS CPI x1.3005"],
                ["AIDS hospitalisation", "EUR 22,000/yr off ART only", "iMTA 2024; Popping et al. 2023 corroboration"],
            ]
            while len(table.rows) < len(rows):
                table.add_row()
            while len(table.rows) > len(rows):
                table._tbl.remove(table.rows[-1]._tr)
            for r, row in enumerate(rows):
                for c, text in enumerate(row):
                    set_cell_text(table.cell(r, c), text)
        if header[:2] == ["Health state", "Utility"]:
            for row in table.rows:
                if row.cells[0].text.strip() == "AIDS off ART":
                    set_cell_text(row.cells[1], "0.70")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    doc = Document(args.input)
    for section in doc.sections:
        section.orientation = WD_ORIENTATION.PORTRAIT
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(0.85)
        section.right_margin = Inches(0.85)

    apply_text_revisions(doc)
    update_existing_tables(doc)
    converted = convert_compact_pipe_groups(doc)
    format_all_tables(doc)
    doc.save(args.output)
    print(f"saved={args.output}")
    print(f"converted_pipe_groups={converted}")


if __name__ == "__main__":
    main()
