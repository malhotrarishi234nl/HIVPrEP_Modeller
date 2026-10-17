"""Streamlit entry point for the local HIV PrEP model workbench.

Run from the repository root with ``streamlit run workbench_ui/app.py``.
Simulation actions remain behind an explicit review step and the allow-listed
``model_workbench`` service; this module never constructs shell commands.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from collaborator_workbench.model_diagrams import (
    ANALYSIS_WORKFLOW_SVG,
    DISEASE_PATHWAY_SVG,
    STATE_SPACE_SVG,
)
from collaborator_workbench.model_guide import (
    FORMULAS,
    HISTORY_MATCHING_COMPONENTS,
    HISTORY_MATCHING_INTRO,
    KEY_TERMS,
    MODEL_INTRO,
    MODEL_SCOPE,
    PARAMETER_FAMILIES,
    REFERENCES,
    VALIDATION_NOTE,
)
from collaborator_workbench.results import ResultImportError, build_report, inspect_output
from workbench_ui.presentation import (
    display_model,
    display_probability,
    display_status,
    display_warning,
    implausibility_cue,
)
from workbench_ui.observations import load_observations
from workbench_ui.register import (
    build_register,
    cost_matrix,
    distribution_curve,
    evidence_summary,
)
from workbench_ui.economics import (
    ECONOMIC_MANIFEST,
    EconomicDraft,
    EconomicWorkbenchError,
    available_economic_results,
    cost_scope_label,
    economic_controls,
    economic_setup,
    execute_economic_draft,
    list_economic_recipes,
    load_economic_result,
    save_economic_recipe,
    policy_scenario_matrix,
    validate_economic_draft,
)
from workbench_ui.service import (
    AnalysisDraft,
    DEFAULT_CONFIG,
    UIWorkbenchService,
    WorkbenchUIError,
    parse_number_series,
    validate_draft,
)
from model_workbench.workers import (
    ALL_AVAILABLE_WORKERS,
    effective_worker_count,
    logical_cpu_count,
    worker_label,
)


ROOT = Path(__file__).resolve().parents[1]
SERVICE = UIWorkbenchService(ROOT)
MODES = ("Historical fit", "Forecast and economics")
MODE_HELP = {
    MODES[0]: "Re-simulate 1980–2025 and compare the model with observed data to validate the fit.",
    MODES[1]: (
        "Project policy scenarios from 2025 and compute costs, QALYs, adverse events and "
        "cost-effectiveness."
    ),
}
PARAMETER_GROUPS = ("Transmission", "Mixing and epidemic start", "Progression and treatment")
HISTORICAL_MODEL_LABELS = {
    "Routine diagnosis": "M0",
    "Add symptom-triggered diagnosis": "M1",
    "Compare both pathways": "both",
}
HISTORICAL_RUN_OPTIONS = {
    "Smoke check — 1 run per setup": 1,
    "Parallel check — 2 runs per setup": 2,
    "Quick exploration — 5 runs per setup": 5,
    "Ensemble summary — 20 runs per setup": 20,
    "Custom": None,
}
HISTORICAL_2008 = ROOT / "examples" / "workbench" / "historical_2008"
HISTORICAL_2010 = ROOT / "examples" / "workbench" / "historical_2010"

KEY_METRICS = (
    "PLHIV",
    "new_infections",
    "new_diagnoses",
    "new_diagnoses_msm",
    "aids_deaths",
    "on_art_stock",
    "prevalence",
)

METRIC_LABELS = {
    "PLHIV": "People living with HIV",
    "new_infections": "New HIV infections",
    "new_diagnoses": "New HIV diagnoses",
    "new_diagnoses_msm": "New HIV diagnoses among MSM",
    "aids_deaths": "AIDS-related deaths",
    "on_art_stock": "People receiving ART",
    "prevalence": "HIV prevalence",
    "incidence_rate": "HIV incidence rate",
}

PARAMETER_LABELS = {
    "population.p_msm.value": "Initial share of the population in the MSM group",
    "population.epidemic_seed.seed_prevalence.value": "Initial HIV prevalence",
    "population.epidemic_seed.msm_fraction.value": "Initial HIV cases assigned to the MSM group",
    "transmission_model.mixing.epsilon_hetero.value": "Mixing within heterosexual activity groups",
    "transmission_model.mixing.epsilon_msm.value": "Mixing within MSM activity groups",
    "transmission_model.disease.transmission.advanced.beta_per_route.vaginal_mtf.chronic.value": "Transmission beta: vaginal sex, male to female",
    "transmission_model.disease.transmission.advanced.beta_per_route.vaginal_ftm.chronic.value": "Transmission beta: vaginal sex, female to male",
    "transmission_model.disease.transmission.advanced.beta_per_route.anal_receptive.chronic.value": "Transmission beta: anal sex, receptive partner",
    "transmission_model.disease.transmission.advanced.beta_per_route.anal_insertive.chronic.value": "Transmission beta: anal sex, insertive partner",
    "transmission_model.disease.transmission.advanced.stage_multiplier_acute.value": "Acute-stage transmission multiplier",
    "transmission_model.disease.transmission.advanced.stage_multiplier_aids.value": "AIDS-stage transmission multiplier",
    "transmission_model.disease.transmission.advanced.msm_versatility.value": "Share of MSM represented as sexually versatile",
    "transmission_model.disease.advanced.art_mortality_reduction.value": "Mortality reduction while receiving ART",
}


def _svg_data_url(svg: str) -> str:
    """Return a browser-safe data URL without asking Pillow to decode SVG bytes."""

    encoded = b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _draft_from_state() -> AnalysisDraft | None:
    payload = st.session_state.get("analysis_draft")
    return AnalysisDraft.from_dict(payload) if payload else None


def _base_draft_from_state() -> AnalysisDraft | None:
    payload = st.session_state.get("analysis_base_draft")
    return AnalysisDraft.from_dict(payload) if payload else None


def _output_period(draft: AnalysisDraft) -> str:
    """Return the inclusive calendar years represented by an exclusive end boundary."""

    if draft.start_year is None or draft.end_year is None:
        return "Not recorded"
    return f"{draft.start_year}–{draft.end_year - 1}"


def _show_status_text(row: dict[str, str]) -> None:
    st.write(row.get("scientific_status", ""))
    if row.get("safe_collaborator_use"):
        st.caption(row["safe_collaborator_use"])


def _core_ready(draft: AnalysisDraft) -> bool:
    return SERVICE.core_available


def _worker_options(current: int) -> tuple[int, ...]:
    del current
    return tuple(list(range(1, 9)) + [ALL_AVAILABLE_WORKERS])


def _worker_control(*, current: int, work_units: int, key_prefix: str) -> int:
    """Render a compact portable worker limit for one analysis."""

    with st.container(border=True):
        st.markdown("**Run speed**")
        options = _worker_options(current)
        current = current if current in options else ALL_AVAILABLE_WORKERS
        worker_key = f"{key_prefix}_workers"
        st.session_state.setdefault(worker_key, current)
        workers = int(st.selectbox(
            "Workers",
            options,
            format_func=worker_label,
            key=worker_key,
            help=(
                "This limits trajectories running at the same time. It does not change the "
                "number of stochastic runs or their random seeds."
            ),
        ))
        useful = effective_worker_count(workers, max(1, work_units))
        st.write(
            f"This analysis can use up to **{useful} worker"
            f"{'s' if useful != 1 else ''} at once** for {work_units} work unit"
            f"{'s' if work_units != 1 else ''}."
        )
        if workers != ALL_AVAILABLE_WORKERS and workers > logical_cpu_count():
            st.warning(
                f"This computer reports {logical_cpu_count()} logical processors. "
                "A higher fixed limit may make the run slower."
            )
        st.caption(
            "Workers change only how many independent runs start together; the number of "
            "runs and their random seeds do not change. Test this computer under Setup."
        )
    return workers


def _request_page(page: str, *, result_source: str | None = None) -> None:
    """Callback-safe page change; ``main`` performs the switch on the next run."""

    st.session_state["_goto"] = page
    if result_source is not None:
        st.session_state["result_source"] = result_source


def _open_completed_result(result_source: str) -> None:
    _request_page("results", result_source=result_source)


def _metric_strip(*items: tuple) -> None:
    """Metric tiles that wrap instead of truncating on narrow windows."""

    with st.container(horizontal=True):
        for label, value, *rest in items:
            st.metric(label, value, border=True, width="stretch", **(rest[0] if rest else {}))


def _set_status(title: str, *, detail: str, changes: int, errors: list[str]) -> None:
    """Publish the working-analysis summary shown in the sidebar."""

    st.session_state["wb_status"] = {
        "title": title, "detail": detail, "changes": changes, "ok": not errors,
        "problems": len(errors),
    }


@st.cache_data(show_spinner=False)
def _register() -> pd.DataFrame:
    return build_register(ROOT)


def _evidence_for(origin: str, path: str) -> dict[str, Any]:
    frame = _register()
    hit = frame[frame["Config path"] == f"{origin}::{path.removesuffix('.value')}"]
    return {} if hit.empty else hit.iloc[0].to_dict()


def _input_editor(rows: list[dict[str, Any]], *, key: str, origin: str, alt: str) -> dict[str, float]:
    """Edit a block of inputs in one table that shows the evidence beside each value.

    Each row needs ``path``, ``label``, ``unit``, ``start`` (recorded value),
    ``current`` (value to show), ``minimum`` and ``maximum``.  Returns only the
    values that differ from the recorded start, keyed by row ``path``.
    """

    evidence = [_evidence_for(origin, row["path"]) for row in rows]
    frame = pd.DataFrame({
        "Input": [row["label"] for row in rows],
        "Recorded value": [float(row["start"]) for row in rows],
        "This analysis": [float(row["current"]) for row in rows],
        "95% interval": [str(item.get("95% interval", "—")) for item in evidence],
        "Allowed range": [f"{row['minimum']:.6g} – {row['maximum']:.6g}" for row in rows],
        "Unit": [row["unit"] for row in rows],
        "Source": [str(item.get("Source", "Not recorded")) for item in evidence],
    })
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        key=key,
        num_rows="fixed",
        disabled=[column for column in frame.columns if column != "This analysis"],
        column_config={
            "Input": st.column_config.TextColumn(width="large"),
            "Recorded value": st.column_config.NumberColumn(format="%.6g"),
            "This analysis": st.column_config.NumberColumn(
                format="%.6g",
                help="Edit this column. Rows that differ from the recorded value are saved as changes.",
            ),
            "Source": st.column_config.TextColumn(width="large"),
        },
        alt=alt,
    )
    changed: dict[str, float] = {}
    for row, value in zip(rows, edited["This analysis"]):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        if not math.isclose(float(value), float(row["start"]), rel_tol=0, abs_tol=1e-15):
            changed[row["path"]] = float(value)
    return changed


def _stochastic_runs_svg(run_count: int) -> str:
    """Return a compact illustration of repeated stochastic trajectories."""

    display_count = min(max(int(run_count), 1), 20)
    paths: list[str] = []
    for index in range(display_count):
        offset = (index - (display_count - 1) / 2) * min(3.2, 22 / display_count)
        bend = ((index * 7) % 9) - 4
        paths.append(
            '<path d="M34 {start:.1f} C125 {mid1:.1f}, 195 {mid2:.1f}, '
            '286 {end:.1f}"/>'.format(
                start=82 + offset * 0.15,
                mid1=77 + offset + bend,
                mid2=42 - offset * 0.5 - bend,
                end=30 + offset * 0.7,
            )
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="150"
      viewBox="0 0 640 150" role="img" aria-labelledby="runs-title runs-desc">
      <title id="runs-title">{run_count} stochastic model runs</title>
      <desc id="runs-desc">Each blue line represents one repeated model run using the same assumptions and a different random seed.</desc>
      <rect x="1" y="1" width="638" height="148" rx="12" fill="#f7f8fa" stroke="#d8dde6"/>
      <g fill="none" stroke="#3678a8" stroke-width="2" opacity="{0.9 if display_count == 1 else 0.42}">
        {''.join(paths)}
      </g>
      <line x1="34" y1="111" x2="286" y2="111" stroke="#aeb7c4"/>
      <text x="322" y="52" fill="#253247" font-family="sans-serif" font-size="20" font-weight="600">{run_count} {'run' if run_count == 1 else 'runs'} per model variant</text>
      <text x="322" y="80" fill="#566176" font-family="sans-serif" font-size="15">Each line uses the same assumptions</text>
      <text x="322" y="103" fill="#566176" font-family="sans-serif" font-size="15">with a different random seed.</text>
      <text x="34" y="134" fill="#697386" font-family="sans-serif" font-size="13">Illustration only — not model output</text>
    </svg>"""


def _results_legend_svg() -> str:
    """Return a compact guide to the main result-chart encodings."""

    return """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="190"
      viewBox="0 0 640 190" role="img" aria-labelledby="result-title result-desc">
      <title id="result-title">How to read a stochastic result chart</title>
      <desc id="result-desc">A dark blue median line is surrounded by a pale blue stochastic range, with gold observed calibration points.</desc>
      <rect x="1" y="1" width="638" height="188" rx="12" fill="#f7f8fa" stroke="#d8dde6"/>
      <path d="M32 120 C125 112, 174 76, 270 86 S398 46, 478 59"
        fill="none" stroke="#b9d8ed" stroke-width="34" opacity="0.72"/>
      <path d="M32 120 C125 112, 174 76, 270 86 S398 46, 478 59"
        fill="none" stroke="#236a9b" stroke-width="4"/>
      <circle cx="126" cy="106" r="6" fill="#d69e2e" stroke="#72530e"/>
      <circle cx="274" cy="92" r="6" fill="#d69e2e" stroke="#72530e"/>
      <circle cx="421" cy="61" r="6" fill="#d69e2e" stroke="#72530e"/>
      <line x1="502" y1="48" x2="536" y2="48" stroke="#236a9b" stroke-width="4"/>
      <text x="546" y="54" fill="#253247" font-family="sans-serif" font-size="15">median</text>
      <rect x="502" y="72" width="34" height="16" rx="4" fill="#b9d8ed" opacity="0.8"/>
      <text x="546" y="85" fill="#253247" font-family="sans-serif" font-size="15">central 90% of runs</text>
      <circle cx="519" cy="112" r="6" fill="#d69e2e" stroke="#72530e"/>
      <text x="546" y="118" fill="#253247" font-family="sans-serif" font-size="15">observed target</text>
      <text x="32" y="166" fill="#697386" font-family="sans-serif" font-size="13">Illustration only — not model output</text>
    </svg>"""


def _parameter_group(path: str) -> str:
    if "beta_per_route" in path or "stage_multiplier" in path or "msm_versatility" in path:
        return "Transmission"
    if "art_mortality" in path:
        return "Progression and treatment"
    return "Mixing and epidemic start"


def _reset_parameter_editors(overrides: dict[str, float] | None = None) -> None:
    """Rebuild every parameter table from ``overrides`` (empty means recorded values)."""

    st.session_state["hist_loaded_overrides"] = dict(overrides or {})
    st.session_state["hist_nonce"] = st.session_state.get("hist_nonce", 0) + 1


def _clear_parameter_widgets() -> None:
    _reset_parameter_editors({})


def _apply_historical_draft_to_widgets(draft: AnalysisDraft) -> None:
    """Make a loaded recipe authoritative over stale widget state."""

    _reset_parameter_editors(dict(draft.parameter_overrides))
    model_label = next(
        label for label, model in HISTORICAL_MODEL_LABELS.items()
        if model == draft.model
    )
    run_label = next(
        (
            label for label, count in HISTORICAL_RUN_OPTIONS.items()
            if count == draft.trajectories
        ),
        "Custom",
    )
    default_name = (
        f"Historical simulation, {draft.trajectories} run"
        + ("s" if draft.trajectories != 1 else "")
    )
    st.session_state["historical_pathway"] = model_label
    st.session_state["historical_p_percent"] = (
        float(draft.p_values[0]) * 100 if draft.p_values else 20.0
    )
    st.session_state["historical_run_size"] = run_label
    st.session_state["historical_custom_runs"] = int(draft.trajectories)
    st.session_state["historical_first_seed"] = int(
        draft.seeds[0] if draft.seeds else 42001
    )
    st.session_state["historical_workers"] = int(draft.workers)
    st.session_state["historical_analysis_name"] = draft.name or default_name
    st.session_state["historical_auto_name"] = default_name


def _new_default_draft() -> AnalysisDraft:
    config_start, config_end = SERVICE.simulation_years(DEFAULT_CONFIG)
    return AnalysisDraft(
        name="",
        config_path=DEFAULT_CONFIG,
        model="M0",
        p_values=(),
        start_year=config_start,
        end_year=config_end,
        trajectories=1,
        seeds=(42001,),
        workers=1,
        parameter_overrides={},
    )


def _economic_summary_value(frame: pd.DataFrame, column: str) -> tuple[float | None, str]:
    values = pd.to_numeric(frame[column], errors="coerce").dropna() if column in frame else pd.Series(dtype=float)
    if values.empty:
        return None, "Not available"
    median = float(values.median())
    if len(values) == 1:
        return median, f"{median:,.2f}"
    return median, f"{median:,.2f} (5th–95th: {values.quantile(.05):,.2f} to {values.quantile(.95):,.2f})"


COST_COMPONENTS = {
    "cost_prep_drug_oral_disc": "Oral PrEP drug",
    "cost_prep_drug_lenacapavir_disc": "Lenacapavir drug",
    "cost_hiv_art_drug_disc": "HIV ART drug",
    "cost_monitoring_disc": "Monitoring",
    "cost_hiv_care_disc": "HIV care",
    "cost_adverse_events_disc": "Adverse events",
    "cost_terminal_care_disc": "Terminal care",
}


def _render_economic_result(path: Path | str) -> None:
    try:
        result = load_economic_result(ROOT / path if not Path(path).is_absolute() else path)
    except (EconomicWorkbenchError, OSError, ValueError) as exc:
        st.error(str(exc))
        return
    runs = int(result.comparisons["seed"].nunique()) if not result.comparisons.empty else 0
    setup = economic_setup(ROOT)
    policy_period = result.manifest.get("policy_period", {})
    freeze_year = int(policy_period.get("start_year", setup["freeze_year"]))
    evaluation_years = int(policy_period.get(
        "evaluation_years",
        result.manifest.get("draft", {}).get("evaluation_years", setup["evaluation_years"]),
    ))
    _metric_strip(
        ("Stochastic runs", runs),
        ("Economic horizon", f"{freeze_year}–{freeze_year + evaluation_years - 1}"),
        ("Cost scope", str(result.manifest.get("cost_scope") or setup["cost_scope"])),
    )
    if result.comparisons.empty:
        st.warning("The run completed but no comparison table was found.")
        return

    try:
        scenario_rows = {row.id: row for row in policy_scenario_matrix(ROOT)}
    except (EconomicWorkbenchError, OSError, KeyError, ValueError):
        scenario_rows = {}
    left, right = st.columns(2)
    scenario = left.selectbox(
        "Policy scenario", list(dict.fromkeys(result.comparisons["scenario"].astype(str))),
        key=f"econ_scenario_{result.root}",
        format_func=lambda v: (
            f"{scenario_rows[v].group} — {scenario_rows[v].setting}" if v in scenario_rows else v.replace("_", " ")
        ),
    )
    in_scenario = result.comparisons[result.comparisons["scenario"].astype(str) == scenario]
    comparison = right.selectbox(
        "Comparison", list(dict.fromkeys(in_scenario["comparison"].astype(str))),
        key=f"econ_comparison_{result.root}",
    )
    selected = in_scenario[in_scenario["comparison"].astype(str) == comparison]
    if scenario in scenario_rows:
        st.caption(f"Configured setting: {scenario_rows[scenario].setting}")
    scenario_arms = (
        result.arms[result.arms["scenario"].astype(str) == scenario] if not result.arms.empty else result.arms
    )

    headline_tab, cost_tab, ae_tab, epi_tab, budget_tab, tables_tab = st.tabs([
        "Headline", "Cost components", "Adverse events", "Epidemiology", "Budget impact", "Exact tables and export",
    ])

    with headline_tab:
        delta_cost, dc_text = _economic_summary_value(selected, "delta_cost")
        delta_qaly, dq_text = _economic_summary_value(selected, "delta_qaly")
        infections, inf_text = _economic_summary_value(selected, "infections_averted")
        nmb, nmb_text = _economic_summary_value(selected, "nmb")
        metrics = [*st.columns(2), *st.columns(2)]
        metrics[0].metric("Incremental cost", "n/a" if delta_cost is None else f"€{delta_cost:,.0f}", help=dc_text, border=True)
        metrics[1].metric("Incremental QALYs", "n/a" if delta_qaly is None else f"{delta_qaly:,.1f}", help=dq_text, border=True)
        metrics[2].metric("Infections averted", "n/a" if infections is None else f"{infections:,.1f}", help=inf_text, border=True)
        metrics[3].metric("Net monetary benefit", "n/a" if nmb is None else f"€{nmb:,.0f}", help=nmb_text, border=True)
        dominance = selected.get("dominance", pd.Series(dtype=str)).astype(str).mode()
        icer = pd.to_numeric(selected.get("icer", pd.Series(dtype=float)), errors="coerce").dropna()
        if not dominance.empty and dominance.iloc[0] in {"dominant", "dominated", "no_diff"}:
            verdict = dominance.iloc[0]
            message = f"Cost-effectiveness result: **{verdict.replace('_', ' ').title()}**"
            (st.success if verdict == "dominant" else st.info)(message)
        elif not icer.empty:
            st.info(f"Median ICER: **€{icer.median():,.0f} per QALY**")
        if runs > 1:
            st.caption("Ranges (hover a metric's help) are 5th–95th percentiles across stochastic runs, not confidence intervals.")
        if not scenario_arms.empty:
            summary = scenario_arms.groupby(["scenario", "arm"], as_index=False).agg(
                total_costs=("total_costs_disc", "median"),
                societal_costs=("total_costs_disc_societal", "median"),
                qalys=("total_qalys_disc", "median"),
                infections=("cumul_new_infections", "median"),
            )
            st.dataframe(
                summary.drop(columns="scenario").rename(columns={
                    "arm": "Arm", "total_costs": "Payer costs (€)", "societal_costs": "Payer + selected additions (€)",
                    "qalys": "Discounted QALYs", "infections": "Cumulative infections",
                }),
                width="stretch", hide_index=True, alt="Median costs and outcomes by arm",
                column_config={
                    "Payer costs (€)": st.column_config.NumberColumn(format="€%.0f"),
                    "Payer + selected additions (€)": st.column_config.NumberColumn(format="€%.0f"),
                },
            )

    with cost_tab:
        available = [c for c in COST_COMPONENTS if c in scenario_arms]
        if not available:
            st.info("This result does not record discounted cost components.")
        else:
            frame = scenario_arms.groupby("arm", as_index=False)[available].median().melt(
                id_vars="arm", var_name="Cost category", value_name="Discounted cost"
            )
            frame["Cost category"] = frame["Cost category"].map(COST_COMPONENTS)
            st.altair_chart(
                alt.Chart(frame).mark_bar().encode(
                    x=alt.X("Discounted cost:Q", title="Median discounted cost (€)"),
                    y=alt.Y("arm:N", title=None),
                    color=alt.Color("Cost category:N", title="Component"),
                    tooltip=["arm:N", "Cost category:N", alt.Tooltip("Discounted cost:Q", format=",.0f")],
                ),
                width="stretch", alt="Stacked cost components by arm",
            )
            wide = frame.pivot(index="Cost category", columns="arm", values="Discounted cost")
            st.dataframe(wide.style.format("€{:,.0f}"), width="stretch", alt="Cost component matrix by arm")
            st.caption("Component costs are medians across runs, so they need not add exactly to the median total.")

    with ae_tab:
        ae_cols = [c for c in scenario_arms.columns if "adverse" in c.lower() or c.lower().startswith("ae_")]
        if not ae_cols:
            st.info("This result does not record adverse-event outputs.")
        else:
            ae = scenario_arms.groupby("arm", as_index=False)[ae_cols].median()
            st.dataframe(ae, width="stretch", hide_index=True, alt="Adverse-event outputs by arm")
            cost_col = "cost_adverse_events_disc"
            if cost_col in ae and not (ae[cost_col].abs() > 0).any():
                st.info(
                    "Adverse-event costs are zero in both arms of this comparison. Arms that do not "
                    "include lenacapavir carry no modelled adverse events, so there is nothing to chart."
                )
            elif cost_col in ae:
                st.altair_chart(
                    alt.Chart(ae).mark_bar().encode(
                        x=alt.X(f"{cost_col}:Q", title="Median discounted adverse-event cost (€)"),
                        y=alt.Y("arm:N", title=None),
                        tooltip=["arm:N", alt.Tooltip(f"{cost_col}:Q", format=",.0f")],
                    ),
                    width="stretch", alt="Adverse-event cost by arm",
                )
        st.caption(
            "Inputs behind these values (prevalence, utility decrement, cost, source) are in "
            "Evidence → Parameter register → Adverse events."
        )

    with epi_tab:
        epi_cols = [c for c in scenario_arms.columns if c.startswith(("cumul_", "total_qalys"))]
        if epi_cols:
            st.dataframe(
                scenario_arms.groupby("arm", as_index=False)[epi_cols].median(),
                width="stretch", hide_index=True, alt="Epidemiological outcomes by arm",
            )
        else:
            st.info("No epidemiological summary columns were recorded.")

    with budget_tab:
        if result.budget.empty:
            st.info("No budget-impact table was recorded.")
        else:
            budget = result.budget
            if "scenario" in budget:
                budget = budget[budget["scenario"].astype(str) == scenario]
            numeric = [c for c in budget.select_dtypes("number").columns if c not in {"seed", "year"}]
            if "year" in budget and numeric:
                preferred = "cumul_budget_impact_disc"
                measure = st.selectbox(
                    "Measure", numeric, key=f"budget_measure_{result.root}",
                    index=numeric.index(preferred) if preferred in numeric else 0,
                )
                line = budget.groupby("year", as_index=False)[measure].median()
                st.altair_chart(
                    alt.Chart(line).mark_line(point=True).encode(
                        x=alt.X("year:Q", title="Year", axis=alt.Axis(format="d")),
                        y=alt.Y(f"{measure}:Q", title=measure.replace("_", " ")),
                        tooltip=["year:Q", alt.Tooltip(f"{measure}:Q", format=",.0f")],
                    ),
                    width="stretch", alt="Budget impact over time",
                )
            st.dataframe(budget, width="stretch", hide_index=True, alt="Budget impact table")

    with tables_tab:
        st.dataframe(result.comparisons, width="stretch", hide_index=True, alt="All comparison rows")
        st.dataframe(result.arms, width="stretch", hide_index=True, alt="All arm summary rows")
        workbooks = [
            result.root / relative
            for record in result.manifest.get("runs", []) for relative in record.get("workbooks", [])
            if (result.root / relative).is_file()
        ]
        if workbooks:
            chosen = st.selectbox(
                "Economic workbook run", workbooks, format_func=lambda item: item.parent.parent.name,
                key=f"economic_workbook_{result.root}",
            )
            st.download_button(
                "Download economic workbook", chosen.read_bytes(), file_name=chosen.name, type="primary",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:",
            )


def _timestamp_slug(name: str, fallback: str) -> tuple[str, str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback
    return stamp, slug


def _model_input_rows(controls: list[Any], overrides: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in PARAMETER_GROUPS}
    for control in controls:
        path = control.paths[0]
        grouped[_parameter_group(path)].append({
            "path": path,
            "label": PARAMETER_LABELS.get(path, path),
            "unit": control.unit,
            "start": control.value,
            "current": overrides.get(path, control.value),
            "minimum": control.minimum,
            "maximum": control.maximum,
        })
    return grouped


def _build_historical() -> None:
    if not any(row.get("path") == DEFAULT_CONFIG for row in SERVICE.list_configurations()):
        st.error(f"The executable configuration is unavailable: {DEFAULT_CONFIG}")
        return

    draft = _draft_from_state()
    if draft is None or draft.config_path != DEFAULT_CONFIG:
        draft = _new_default_draft()
        st.session_state["analysis_draft"] = draft.to_dict()
        st.session_state["analysis_base_draft"] = draft.to_dict()
        _apply_historical_draft_to_widgets(draft)

    saved = [item for item in SERVICE.list_recipes() if item.get("source") == "saved"]
    choices: dict[str, str | None] = {"Default configuration (recorded values)": None}
    choices.update({f"Saved · {item['name']}": item["id"] for item in saved})
    with st.container(border=True):
        pick, load = st.columns([4, 1], vertical_alignment="bottom")
        start_choice = pick.selectbox("Start from", list(choices), key="hist_start_choice")
        if load.button("Load", width="stretch", help="Replace the working analysis with this configuration."):
            recipe_id = choices[start_choice]
            try:
                loaded = _new_default_draft() if recipe_id is None else SERVICE.load_recipe(recipe_id)
            except WorkbenchUIError as exc:
                st.error(str(exc))
            else:
                st.session_state["analysis_draft"] = loaded.to_dict()
                st.session_state["analysis_base_draft"] = loaded.to_dict()
                _apply_historical_draft_to_widgets(loaded)
                st.rerun()
        st.caption(
            f"Simulation period **{_output_period(draft)}** · the start year is fixed because the "
            "starting population and epidemic seed are bundled with it."
        )

    controls = SERVICE.contract_controls(DEFAULT_CONFIG)
    inputs_tab, design_tab, review_tab = st.tabs(
        [":material/tune: 1 · Inputs", ":material/science: 2 · Run design", ":material/play_circle: 3 · Review and run"]
    )

    override_values: dict[str, float] = {}
    nonce = st.session_state.get("hist_nonce", 0)
    loaded_overrides = st.session_state.get("hist_loaded_overrides", dict(draft.parameter_overrides))
    with inputs_tab:
        st.write(
            f"{len(controls)} model parameters can be changed here. Each row shows the recorded "
            "value, the allowed range, the literature 95% interval and the source, so an edit "
            "can be judged against its evidence."
        )
        st.caption(
            "Allowed range is a safety guardrail for this tool, not a confidence interval. "
            "The 95% interval comes from the declared distribution or recorded bounds."
        )
        grouped = _model_input_rows(controls, loaded_overrides)
        for group, rows in grouped.items():
            if not rows:
                continue
            st.markdown(f"##### {group}")
            override_values.update(_input_editor(
                rows, key=f"hist_inputs_{group}_{nonce}", origin="model",
                alt=f"Editable {group.lower()} parameters with evidence",
            ))
        if st.button("Reset all parameter changes", disabled=not override_values, icon=":material/restart_alt:"):
            reset = AnalysisDraft.from_dict({**draft.to_dict(), "parameter_overrides": {}})
            st.session_state["analysis_draft"] = reset.to_dict()
            _clear_parameter_widgets()
            st.rerun()

    with design_tab:
        st.markdown("##### Diagnosis pathway")
        st.session_state.setdefault(
            "historical_pathway",
            next(label for label, model in HISTORICAL_MODEL_LABELS.items() if model == draft.model),
        )
        model_label = st.segmented_control(
            "Pathway", tuple(HISTORICAL_MODEL_LABELS), key="historical_pathway",
            required=True, label_visibility="collapsed",
        )
        model = HISTORICAL_MODEL_LABELS[model_label]
        p_values: tuple[float, ...] = ()
        if model != "M0":
            st.session_state.setdefault(
                "historical_p_percent", float((draft.p_values[0] * 100) if draft.p_values else 20.0)
            )
            p_percent = st.number_input(
                "Annual symptom-triggered diagnosis probability (%)",
                min_value=0.0, max_value=100.0, step=1.0, key="historical_p_percent",
            )
            p_values = (p_percent / 100,)

        st.markdown("##### Stochastic runs")
        st.session_state.setdefault(
            "historical_run_size",
            next((l for l, c in HISTORICAL_RUN_OPTIONS.items() if c == draft.trajectories), "Custom"),
        )
        run_label = st.selectbox("Runs per model setup", tuple(HISTORICAL_RUN_OPTIONS), key="historical_run_size")
        trajectories = HISTORICAL_RUN_OPTIONS[run_label]
        if trajectories is None:
            st.session_state.setdefault("historical_custom_runs", int(draft.trajectories))
            trajectories = int(st.number_input(
                "Custom runs per model setup", min_value=1, max_value=20, step=1,
                key="historical_custom_runs",
            ))
        with st.expander("Reproducibility and speed"):
            st.session_state.setdefault("historical_first_seed", int(draft.seeds[0] if draft.seeds else 42001))
            first_seed = int(st.number_input(
                "First random seed", min_value=0, max_value=2**32 - 1, step=1,
                key="historical_first_seed",
            ))
            workers = _worker_control(current=draft.workers, work_units=trajectories, key_prefix="historical")

        default_name = f"Historical simulation, {trajectories} run" + ("s" if trajectories != 1 else "")
        previous_auto = st.session_state.get("historical_auto_name")
        if (
            "historical_analysis_name" not in st.session_state
            or st.session_state.get("historical_analysis_name") == previous_auto
        ):
            st.session_state["historical_analysis_name"] = default_name
        st.session_state["historical_auto_name"] = default_name
        st.markdown("##### Name")
        name = st.text_input("Analysis name", key="historical_analysis_name", label_visibility="collapsed")

    model_variants = (1 if model in {"M0", "both"} else 0) + (len(p_values) if model in {"M1", "both"} else 0)
    total = model_variants * trajectories
    reviewed = AnalysisDraft.from_dict({
        **draft.to_dict(),
        "name": name.strip() or default_name,
        "model": model,
        "p_values": p_values,
        "trajectories": trajectories,
        "seeds": tuple(range(first_seed, first_seed + trajectories)),
        "workers": workers,
        "parameter_overrides": override_values,
    })
    st.session_state["analysis_draft"] = reviewed.to_dict()
    errors = list(dict.fromkeys(validate_draft(reviewed, ROOT)))
    try:
        SERVICE.core_requests(reviewed)
    except WorkbenchUIError as exc:
        errors.append(str(exc))
    errors = list(dict.fromkeys(errors))
    _set_status(
        reviewed.name,
        detail=f"Historical · {_output_period(reviewed)} · {total} trajectory" + ("" if total == 1 else "s"),
        changes=len(override_values), errors=errors,
    )

    with review_tab:
        diagnosis_text = {"M0": "routine diagnosis", "M1": "symptom-triggered diagnosis", "both": "both diagnosis pathways"}[model]
        workers_used = effective_worker_count(workers, trajectories)
        _metric_strip(
            ("Period", _output_period(reviewed)), ("Model setups", model_variants),
            ("Trajectories", total), ("Parameter changes", len(override_values)),
        )
        st.write(
            f"Historical simulation using **{diagnosis_text}**, **{trajectories}** "
            f"{'run' if trajectories == 1 else 'runs'} per setup, up to **{workers_used}** parallel "
            f"{'worker' if workers_used == 1 else 'workers'}."
        )
        control_by_path = {control.paths[0]: control for control in controls}
        if override_values:
            st.dataframe(
                [{
                    "Parameter": PARAMETER_LABELS.get(path, path),
                    "Recorded value": control_by_path[path].value,
                    "This analysis": value,
                    "Unit": control_by_path[path].unit,
                } for path, value in override_values.items()],
                width="stretch", hide_index=True, alt="Parameters changed from recorded values",
            )
        else:
            st.caption("No parameter changes: this run uses the recorded configuration.")
        for error in errors:
            st.error(error)
        disabled = bool(errors) or not _core_ready(reviewed)
        buttons = st.container(horizontal=True)
        check_col = estimate_col = run_col = save_col = buttons
        action: str | None = None
        if check_col.button("Check setup", disabled=disabled, icon=":material/rule:"):
            try:
                checks = SERVICE.check_draft(reviewed)
                blockers = [row for row in checks if row.get("level") == "BLOCK"]
                for row in blockers:
                    st.error(row.get("message", "Setup check failed"))
                if not blockers:
                    st.success("Setup check passed. No simulation was run.")
                with st.expander("Check details"):
                    st.dataframe(checks, width="stretch", hide_index=True)
            except Exception as exc:
                st.error(f"Setup check failed: {exc}")
        if estimate_col.button("Estimate time", disabled=disabled, icon=":material/timer:"):
            action = "benchmark"
        if run_col.button("Run analysis", type="primary", disabled=disabled, icon=":material/play_arrow:"):
            action = "run"
        if save_col.button("Save configuration", disabled=bool(errors), icon=":material/save:"):
            try:
                path = SERVICE.save_recipe(reviewed)
                st.success(f"Saved {path.relative_to(ROOT)}. Reopen it from Start from.")
            except WorkbenchUIError as exc:
                st.error(str(exc))

        if action:
            stamp, slug = _timestamp_slug(reviewed.name, "analysis")
            run_root = ROOT / "outputs" / "workbench" / f"{stamp}-{slug}-{action}"
            try:
                with st.spinner("Estimating time..." if action == "benchmark" else "Running analysis..."):
                    completed = SERVICE.execute_draft(reviewed, action, run_root)
                if action == "benchmark":
                    central = sum(float(row.get("estimate", {}).get("seconds_central", 0)) for row in completed)
                    st.success(f"Estimated total runtime: about {central:.1f} seconds on this computer.")
                else:
                    st.success("Analysis complete.")
                    by_label = {str(row["analysis"]): str(row["run_root"]) for row in completed}
                    chosen = next(iter(by_label))
                    if len(by_label) > 1:
                        chosen = st.selectbox("Result to open first", list(by_label))
                    st.session_state["last_result"] = by_label[chosen]
                    st.button(
                        f"Open results: {chosen}", type="primary", icon=":material/arrow_forward:",
                        on_click=_open_completed_result, args=(by_label[chosen],),
                    )
            except Exception as exc:
                st.error(f"Analysis failed: {type(exc).__name__}: {exc}")


def _load_economic_recipe(settings: dict[str, Any]) -> None:
    """Callback: make a saved forecast configuration authoritative over widget state."""

    ss = st.session_state
    ss["econ_loaded_ids"] = list(settings.get("scenario_ids") or [])
    ss["econ_loaded_cost"] = dict(settings.get("costing_overrides", {}))
    ss["econ_loaded_model"] = dict(settings.get("model_overrides", {}))
    ss["econ_nonce"] = ss.get("econ_nonce", 0) + 1
    ss["econ_patient_time"] = bool(settings.get("include_patient_time", True))
    ss["econ_productivity"] = bool(settings.get("include_productivity_loss", False))
    ss["econ_horizon"] = int(settings.get("evaluation_years", 40))
    ss["econ_runs"] = int(settings.get("runs", 1))
    ss["economic_first_seed"] = int(settings.get("first_seed", 42001))
    ss["economic_analysis_name"] = str(settings.get("name", "Policy and economic analysis"))
    ss["economic_workers"] = int(settings.get("workers", 1))


def _build_forecast() -> None:
    try:
        setup = economic_setup(ROOT)
        controls = economic_controls(ROOT)
        matrix = policy_scenario_matrix(ROOT)
    except (EconomicWorkbenchError, OSError, KeyError, ValueError) as exc:
        st.error(str(exc))
        return
    ss = st.session_state
    nonce = ss.get("econ_nonce", 0)

    recipes = list_economic_recipes(ROOT)
    choices = {"Default configuration (recorded values)": None}
    choices.update({f"Saved · {item['name']}": item["settings"] for item in recipes})
    with st.container(border=True):
        pick, load = st.columns([4, 1], vertical_alignment="bottom")
        choice = pick.selectbox("Start from", list(choices), key="econ_start_choice")
        settings = choices[choice] if choices[choice] is not None else {}
        load.button(
            "Load", width="stretch", on_click=_load_economic_recipe, args=(settings,),
            help="Replace the working analysis with this configuration.",
        )
        st.caption(
            f"Policy evaluation starts in **{setup['freeze_year']}** from the fitted 2025 model state. "
            "Every comparison reuses the same shared pre-policy history."
        )

    policy_tab, cost_tab, epi_tab, design_tab, review_tab = st.tabs([
        ":material/compare_arrows: 1 · Policy comparisons",
        ":material/euro: 2 · Costs and scope",
        ":material/coronavirus: 3 · Epidemiology",
        ":material/science: 4 · Run design",
        ":material/play_circle: 5 · Review and run",
    ])

    arm_labels = {
        "no_prep": "No PrEP", "oral_prep": "Oral PrEP held at 13,000",
        "oral_prep_expansion": "Oral PrEP expansion", "oral_prep_plus_lena": "Oral PrEP + lenacapavir",
    }
    loaded_ids = ss.get("econ_loaded_ids") or [row.id for row in matrix]
    with policy_tab:
        st.write(
            "Each ticked row is a two-arm comparison. Oral PrEP held at 13,000 is the common "
            "comparator; the no-PrEP row is attribution context and can be removed."
        )
        matrix_frame = pd.DataFrame([{
            "Run": row.id in loaded_ids, "Group": row.group, "Scenario": row.label,
            "Comparison": f"{arm_labels.get(row.intervention, row.intervention)} vs {arm_labels.get(row.comparator, row.comparator)}",
            "Setting": row.setting, "Scenario ID": row.id,
        } for row in matrix])
        edited_matrix = st.data_editor(
            matrix_frame, width="stretch", hide_index=True,
            disabled=["Group", "Scenario", "Comparison", "Setting", "Scenario ID"],
            column_config={"Run": st.column_config.CheckboxColumn("Run", width="small"), "Scenario ID": None},
            key=f"econ_matrix_{nonce}", alt="Policy comparison matrix",
        )
        selected_ids = tuple(edited_matrix.loc[edited_matrix["Run"].astype(bool), "Scenario ID"].astype(str))
        selected_rows = [row for row in matrix if row.id in selected_ids]
        families = list(dict.fromkeys(row.source_id for row in selected_rows if row.id != "base_case_context"))
        family_labels = {row.source_id: row.group for row in matrix if row.id != "base_case_context"}
        if families:
            default_family = families.index("scenario_lena_substitution") if "scenario_lena_substitution" in families else 0
            primary_family = st.selectbox(
                "Headline comparison family", families, index=default_family,
                format_func=lambda value: family_labels.get(value, value),
                help="Controls report ordering only; every ticked row is still calculated.",
            )
        else:
            primary_family = ""
        st.caption(
            f"{len(selected_ids)} comparisons · about {len(selected_ids) * 2 + 1} model trajectories "
            "per stochastic seed, including the shared pre-policy history."
        )

    with cost_tab:
        st.markdown("##### Cost scope")
        scope1, scope2 = st.columns(2)
        ss.setdefault("econ_patient_time", bool(setup["include_patient_time"]))
        ss.setdefault("econ_productivity", bool(setup["include_productivity_loss"]))
        include_patient_time = scope1.checkbox(
            "Include patient time", key="econ_patient_time",
            help="Values travel and appointment time for PrEP and HIV treatment visits.",
        )
        include_productivity_loss = scope2.checkbox(
            "Include productivity loss", key="econ_productivity",
            help="Friction-cost method with stage-specific loss assumptions. Review before interpretation.",
        )
        scope_label = cost_scope_label(include_patient_time, include_productivity_loss)
        st.dataframe(
            [
                {"Cost component": "Healthcare services, medicines and monitoring", "Included": "Yes"},
                {"Cost component": "Patient time", "Included": "Yes" if include_patient_time else "No"},
                {"Cost component": "Productivity loss", "Included": "Yes" if include_productivity_loss else "No"},
                {"Cost component": "Informal care", "Included": "Not implemented"},
            ],
            width="stretch", hide_index=True, alt="Cost components included in the analysis",
        )
        st.info(f"ICER and net monetary benefit use: **{scope_label}** costs.")

        st.markdown("##### Cost, utility and adverse-event inputs")
        st.caption(
            "Recorded values come from the costing configuration. Edit the right-hand column; "
            "source and 95% interval are shown beside each input."
        )
        loaded_cost = ss.get("econ_loaded_cost", {})
        cost_groups = list(dict.fromkeys(control.group for control in controls))
        costing_overrides: dict[str, float] = {}
        for group, group_tab in zip(cost_groups, st.tabs(cost_groups)):
            with group_tab:
                rows = [{
                    "path": c.path, "label": c.label, "unit": c.unit, "start": c.value,
                    "current": loaded_cost.get(c.path, c.value), "minimum": c.minimum, "maximum": c.maximum,
                } for c in controls if c.group == group]
                costing_overrides.update(_input_editor(
                    rows, key=f"econ_cost_{group}_{nonce}", origin="costing",
                    alt=f"Editable {group.lower()} inputs with evidence",
                ))

    model_overrides: dict[str, float] = {}
    model_changes = 0
    with epi_tab:
        st.write(
            "Optional: change the same bounded epidemiological inputs available in the historical "
            "runner. Leave unchanged to use the fitted model."
        )
        model_controls = SERVICE.contract_controls(DEFAULT_CONFIG)
        loaded_model = ss.get("econ_loaded_model", {})
        grouped = _model_input_rows(model_controls, loaded_model)
        by_path = {control.paths[0]: control for control in model_controls}
        for group, rows in grouped.items():
            if not rows:
                continue
            st.markdown(f"##### {group}")
            changed = _input_editor(
                rows, key=f"econ_model_{group}_{nonce}", origin="model",
                alt=f"Editable {group.lower()} model parameters",
            )
            model_changes += len(changed)
            for path, value in changed.items():
                for linked in by_path[path].paths:
                    model_overrides[linked] = value

    with design_tab:
        ss.setdefault("econ_horizon", int(setup["evaluation_years"]))
        horizon = int(st.number_input(
            "Economic evaluation horizon (years)", min_value=1, max_value=80,
            step=1, key="econ_horizon",
        ))
        period_end = int(setup["freeze_year"]) + horizon - 1
        st.caption(f"Policy evaluation period: {setup['freeze_year']}–{period_end}")
        ss.setdefault("econ_runs", 1)
        run_count = int(st.selectbox(
            "Stochastic runs", (1, 2, 5, 20), key="econ_runs",
            format_func=lambda v: f"{v} run" if v == 1 else f"{v} runs",
            help="Each run repeats the full policy comparison with a different random seed.",
        ))
        with st.expander("Reproducibility and speed"):
            ss.setdefault("economic_first_seed", 42001)
            first_seed = int(st.number_input(
                "First random seed", min_value=0, max_value=2**32 - 1, step=1,
                key="economic_first_seed",
            ))
            workers = _worker_control(current=1, work_units=run_count, key_prefix="economic")
        ss.setdefault("economic_analysis_name", "Policy and economic analysis")
        name = st.text_input("Analysis name", key="economic_analysis_name")

    draft = EconomicDraft(
        name=name, base_config=DEFAULT_CONFIG,
        seeds=tuple(range(first_seed, first_seed + run_count)),
        model_overrides=model_overrides, costing_overrides=costing_overrides,
        scenario_ids=selected_ids, primary_scenario_family=primary_family,
        include_patient_time=include_patient_time, include_productivity_loss=include_productivity_loss,
        evaluation_years=horizon, workers=workers,
    )
    errors = list(dict.fromkeys(validate_economic_draft(ROOT, draft)))
    change_count = len(costing_overrides) + model_changes
    _set_status(
        name, detail=f"Forecast · {len(selected_ids)} comparisons · {run_count} run{'s' if run_count != 1 else ''} · {horizon} years",
        changes=change_count, errors=errors,
    )

    with review_tab:
        _metric_strip(
            ("Comparisons", len(selected_ids)), ("Stochastic runs", run_count),
            ("Horizon", f"{setup['freeze_year']}–{period_end}"), ("Input changes", change_count),
        )
        st.write(
            f"**{scope_label}** costs for ICER and NMB, with up to "
            f"**{effective_worker_count(workers, run_count)}** parallel workers."
        )
        label_by_path = {c.path: c.label for c in controls}
        changes = [{"Input": label_by_path[p], "This analysis": v} for p, v in costing_overrides.items()]
        changes += [{"Input": PARAMETER_LABELS.get(p, p), "This analysis": v} for p, v in model_overrides.items()]
        if changes:
            st.dataframe(changes, width="stretch", hide_index=True, alt="Inputs changed from recorded values")
        else:
            st.caption("No input changes: this run uses the recorded cost and model configuration.")
        for error in errors:
            st.error(error)
        buttons = st.container(horizontal=True)
        check_col = save_col = run_col = buttons
        if check_col.button("Check setup", disabled=bool(errors), icon=":material/rule:"):
            st.success("Economic setup passed configuration checks. No simulation was run.")
        if save_col.button("Save configuration", disabled=bool(errors), icon=":material/save:"):
            try:
                path = save_economic_recipe(ROOT, name, {
                    "scenario_ids": list(selected_ids), "costing_overrides": costing_overrides,
                    "model_overrides": model_overrides, "include_patient_time": include_patient_time,
                    "include_productivity_loss": include_productivity_loss, "evaluation_years": horizon,
                    "runs": run_count, "first_seed": first_seed, "workers": workers, "name": name,
                })
                st.success(f"Saved {path.relative_to(ROOT)}. Reopen it from Start from.")
            except EconomicWorkbenchError as exc:
                st.error(str(exc))
        if run_col.button("Run analysis", type="primary", disabled=bool(errors), icon=":material/play_arrow:"):
            stamp, slug = _timestamp_slug(name, "economic-analysis")
            output = ROOT / "outputs" / "workbench_economic" / f"{stamp}-{slug}"
            try:
                with st.spinner("Running epidemiological policy arms and economic analysis..."):
                    execute_economic_draft(ROOT, draft, output)
                st.success("Policy and economic analysis complete.")
                relative = str(output.relative_to(ROOT))
                st.session_state["last_result"] = relative
                st.button(
                    "Open results", type="primary", icon=":material/arrow_forward:",
                    on_click=_open_completed_result, args=(relative,),
                )
            except Exception as exc:
                st.error(f"Economic analysis failed: {type(exc).__name__}: {exc}")


def _render_build() -> None:
    _apply_pending_load()
    st.title("Build and run")
    st.write(
        "Pick the analysis, start from the defaults or a saved configuration, adjust inputs against "
        "their evidence, then review and run. Everything here is input-driven; no config files to edit."
    )
    st.session_state.setdefault("wb_mode", MODES[0])
    mode = st.segmented_control(
        "Analysis mode", MODES, key="wb_mode", required=True, label_visibility="collapsed",
    )
    st.caption(MODE_HELP[mode])
    if mode == MODES[0]:
        _build_historical()
    else:
        _build_forecast()


def _render_runs() -> None:
    st.title("Run history")
    output_root = ROOT / "outputs" / "workbench"
    manifests = sorted(
        output_root.glob("**/workbench_manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if output_root.is_dir() else []
    if not manifests:
        st.write("No local workbench runs were found.")
        return
    rows = []
    output_locations: list[str] = []
    for path in manifests:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.append({
                "Run": path.parent.name,
                "Result": display_status(payload.get("status")),
                "Configuration": payload.get("recipe_title") or "Not recorded",
                "Action": str(payload.get("action") or "Not recorded").replace("_", " ").title(),
                "Completed trajectories": payload.get("completed_work_units"),
                "Elapsed time (seconds)": payload.get("elapsed_seconds"),
            })
            output_locations.append(str(path.parent.relative_to(ROOT)))
        except (OSError, json.JSONDecodeError):
            rows.append({"Run": path.parent.name, "Result": "Could not read this run"})
            output_locations.append(str(path.parent))
    st.dataframe(rows, width="stretch", hide_index=True)
    with st.expander("Output locations"):
        for location in output_locations:
            st.code(location)


@st.cache_data(show_spinner="Inspecting result files...")
def _cached_inspect(path: str, modified_ns: int):
    del modified_ns
    return inspect_output(path)


def _load_bundle(path_text: str):
    path = Path(path_text).expanduser()
    path = path if path.is_absolute() else ROOT / path
    marker = path.stat().st_mtime_ns if path.exists() else 0
    return _cached_inspect(str(path), marker)


def _metric_label(bundle: Any, metric: str) -> str:
    known = METRIC_LABELS.get(metric)
    if known:
        return known
    rows = bundle.annual_long[bundle.annual_long["metric"] == metric]
    if not rows.empty:
        return str(rows.iloc[0]["label"])
    return metric.replace("_", " ").title()


def _ordered_metrics(bundle: Any) -> list[str]:
    available = set(bundle.annual_long["metric"].unique())
    leading = [metric for metric in KEY_METRICS if metric in available]
    return leading + sorted(available - set(leading), key=lambda item: _metric_label(bundle, item))


def _ensemble_chart(
    bundle: Any,
    metric: str,
    observations: pd.DataFrame | None = None,
) -> None:
    summary = bundle.across_run_summary[
        bundle.across_run_summary["metric"] == metric
    ].copy()
    if summary.empty:
        return
    routes = summary["route_id"].nunique()
    color = alt.Color("route_id:N", title="Model") if routes > 1 else alt.value("#2f80ed")
    base = alt.Chart(summary).encode(
        x=alt.X("year:Q", title="Year", axis=alt.Axis(format="d")),
        color=color,
    )
    band = base.mark_area(opacity=0.18).encode(
        y=alt.Y("q05:Q", title=_metric_label(bundle, metric)),
        y2="q95:Q",
        tooltip=[
            alt.Tooltip("year:Q", format=".0f"),
            alt.Tooltip("q05:Q", title="5th percentile", format=",.3~g"),
            alt.Tooltip("median:Q", title="Median", format=",.3~g"),
            alt.Tooltip("q95:Q", title="95th percentile", format=",.3~g"),
        ],
    )
    line = base.mark_line(size=2.5).encode(
        y=alt.Y("median:Q", title=_metric_label(bundle, metric)),
        tooltip=[
            alt.Tooltip("year:Q", format=".0f"),
            alt.Tooltip("median:Q", title="Median", format=",.3~g"),
            alt.Tooltip("n:Q", title="Stochastic runs"),
        ],
    )
    chart: Any = band + line
    observations = observations if observations is not None else pd.DataFrame()
    if not observations.empty:
        observation_colors = alt.Color(
            "role:N",
            title="Observed data",
            scale=alt.Scale(
                domain=["calibration", "cross_validation"],
                range=["#d69e2e", "#7b61a8"],
            ),
            legend=alt.Legend(
                labelExpr="datum.label == 'calibration' ? 'Calibration target' : 'Validation / comparison'"
            ),
        )
        with_intervals = observations.dropna(subset=["lower", "upper"])
        if not with_intervals.empty:
            intervals = alt.Chart(with_intervals).mark_rule(strokeWidth=2).encode(
                x=alt.X("year:Q", title="Year"),
                y=alt.Y("lower:Q", title=_metric_label(bundle, metric)),
                y2="upper:Q",
                color=observation_colors,
                tooltip=[
                    alt.Tooltip("series:N", title="Observed series"),
                    alt.Tooltip("year:Q", format=".0f"),
                    alt.Tooltip("lower:Q", title="Source lower", format=",.3~g"),
                    alt.Tooltip("upper:Q", title="Source upper", format=",.3~g"),
                ],
            )
            chart = chart + intervals
        points = alt.Chart(observations).mark_point(
            filled=True, size=95, stroke="black", strokeWidth=0.45
        ).encode(
            x=alt.X("year:Q", title="Year"),
            y=alt.Y("observed:Q", title=_metric_label(bundle, metric)),
            color=observation_colors,
            shape=alt.Shape(
                "role:N",
                scale=alt.Scale(
                    domain=["calibration", "cross_validation"],
                    range=["circle", "diamond"],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("series:N", title="Observed series"),
                alt.Tooltip("year:Q", format=".0f"),
                alt.Tooltip("observed:Q", title="Observed", format=",.3~g"),
                alt.Tooltip("source:N", title="Source"),
            ],
        )
        chart = chart + points
    st.altair_chart(chart.interactive(), width="stretch")
    if int(summary["n"].max()) > 1:
        st.caption(
            f"Line: median. Shaded area: central 90% of "
            f"{int(summary['n'].max())} stochastic runs. This is not a confidence interval."
        )
    else:
        st.caption(
            "This result contains one stochastic run, so it has a single trajectory and no "
            "stochastic interval. Any vertical bars on observed data are ranges reported by "
            "that external source."
        )
    if not observations.empty:
        has_interval = observations[["lower", "upper"]].notna().all(axis=1).any()
        st.caption(
            "Circles are calibration targets; diamonds are validation or comparison data. "
            + (
                "Vertical bars are uncertainty ranges reported by the source. "
                if has_interval else
                "These sources provide point observations without an uncertainty interval. "
            )
            + "They are separate from the stochastic range of the simulation."
        )


def _observed_comparison_table(summary: pd.DataFrame, observations: pd.DataFrame) -> None:
    if observations.empty:
        return
    comparison = observations.merge(
        summary[["year", "median"]], on="year", how="left", validate="many_to_one"
    )
    comparison["difference"] = comparison["median"] - comparison["observed"]
    comparison["difference_percent"] = comparison["difference"].div(
        comparison["observed"].where(comparison["observed"] != 0)
    ) * 100
    interval_rows = comparison[["lower", "upper"]].notna().all(axis=1)
    comparison["inside_source_range"] = pd.NA
    comparison.loc[interval_rows, "inside_source_range"] = (
        (comparison.loc[interval_rows, "median"] >= comparison.loc[interval_rows, "lower"])
        & (comparison.loc[interval_rows, "median"] <= comparison.loc[interval_rows, "upper"])
    ).map({True: "Yes", False: "No"})
    comparison["role"] = comparison["role"].map({
        "calibration": "Calibration target",
        "cross_validation": "Validation / comparison",
    })
    display = comparison.rename(columns={
        "series": "Observed series", "role": "Use", "year": "Year",
        "observed": "Observed", "lower": "Source lower", "upper": "Source upper",
        "median": "Model median", "difference": "Difference",
        "difference_percent": "Difference (%)", "inside_source_range": "Inside source range",
    })
    with st.expander("Observed-data comparison"):
        st.dataframe(
            display[[
                "Observed series", "Use", "Year", "Observed", "Source lower",
                "Source upper", "Model median", "Difference", "Difference (%)",
                "Inside source range",
            ]],
            width="stretch", hide_index=True,
            column_config={
                "Difference (%)": st.column_config.NumberColumn(format="%.1f"),
                "Inside source range": st.column_config.TextColumn(
                    help="Available only when the external source reports lower and upper values."
                ),
            },
        )
        st.caption(
            "This table is descriptive. It does not apply the calibration implausibility rule "
            "or turn contextual comparison series into calibration targets."
        )


def _observed_difference_summary(
    summary: pd.DataFrame, observations: pd.DataFrame
) -> None:
    """Lead with a compact descriptive comparison before the detailed chart."""

    if observations.empty:
        return
    comparison = observations.merge(
        summary[["year", "median"]], on="year", how="inner", validate="many_to_one"
    )
    comparison = comparison[comparison["observed"] != 0].copy()
    if comparison.empty:
        return
    comparison["difference_percent"] = (
        (comparison["median"] - comparison["observed"]) / comparison["observed"] * 100
    )
    latest = comparison.sort_values("year").iloc[-1]
    typical = float(comparison["difference_percent"].abs().median())
    latest_difference = float(latest["difference_percent"])
    col1, col2, col3 = st.columns(3)
    col1.metric("Overlapping observed years", len(comparison), border=True)
    col2.metric(
        "Latest model difference",
        f"{latest_difference:+.1f}%",
        help="Model median minus the observed value, divided by the observed value.",
        border=True,
    )
    col3.metric(
        "Typical absolute difference",
        f"{typical:.1f}%",
        help="Median absolute percentage difference across overlapping observed years.",
        border=True,
    )
    if abs(latest_difference) >= 30 or typical >= 30:
        st.warning(
            "The model and displayed observations differ by at least 30% on this descriptive "
            "comparison. Inspect the years and data role before interpreting the trajectory.",
            title="Large observed-data difference",
        )
    st.caption(
        "The 30% cue is a visual review aid, not a calibration threshold, confidence interval "
        "or validation pass/fail rule."
    )


def _historical_fit_summary(path: Path) -> None:
    fit_path = path / "target_fit_candidate_1787.csv"
    if not fit_path.is_file():
        return
    fit = pd.read_csv(fit_path)
    st.subheader("Fit to the recorded calibration targets")
    col1, col2, col3 = st.columns(3)
    col1.metric("Calibration targets", len(fit))
    col2.metric("Targets passing the recorded rule", int(fit["primary_target_pass"].sum()))
    col3.metric("Largest target implausibility", f"{fit['primary_implausibility'].max():.2f}")
    st.warning(
        "These are fitted calibration targets, not held-out validation results. Passing the "
        "recorded implausibility rule does not mean every percentage error is small."
    )
    display = fit.copy()
    display[["Measure", "Year"]] = display["target_key"].str.split("@", n=1, expand=True)
    display["Measure"] = display["Measure"].map({
        "shm_new_diagnoses_msm": "New HIV diagnoses among MSM",
        "rivm_aids_msm": "AIDS diagnoses among MSM",
    }).fillna(display["Measure"])
    display = display.rename(columns={
        "observed": "Observed",
        "simulated_mean": "Model mean",
        "signed_percentage_error": "Difference (%)",
        "primary_implausibility": "Implausibility",
    })
    display["Threshold"] = display["Implausibility"].map(
        lambda value: implausibility_cue(value).label
    )

    def _highlight_implausibility(row: pd.Series) -> list[str]:
        cue = implausibility_cue(row["Implausibility"])
        color = "background-color: #e8f5e9" if cue.tone == "within" else "background-color: #fdecea"
        return [color] * len(row)

    st.caption(
        "Green rows are within the declared primary implausibility threshold; red rows are "
        "above it. The text in the Threshold column carries the same meaning without color."
    )
    st.dataframe(
        display[[
            "Measure", "Year", "Observed", "Model mean", "Difference (%)",
            "Implausibility", "Threshold",
        ]].style.apply(_highlight_implausibility, axis=1),
        width="stretch",
        hide_index=True,
        column_config={
            "Implausibility": st.column_config.NumberColumn(
                help="The primary history-matching rule is I-max ≤ 3.",
                format="%.2f",
            ),
            "Difference (%)": st.column_config.NumberColumn(
                help="Signed difference between the model mean and observation. No generic pass/fail threshold is applied.",
                format="%.1f",
            ),
            "Threshold": st.column_config.TextColumn(
                help="Text interpretation of the documented I-max threshold."
            ),
        },
    )
    with st.expander("Recorded limitation from the historical run"):
        st.write(
            "The retained manifest reports 38 passing diagnosis tests and one explicitly "
            "tolerated failing neutrality test. The suite was not fully passing. This does "
            "not change the recorded candidate selection, but it remains part of the evidence."
        )


def _bundle_header(bundle: Any) -> None:
    year_min = int(bundle.annual_long["year"].min()) if not bundle.annual_long.empty else None
    year_max = int(bundle.annual_long["year"].max()) if not bundle.annual_long.empty else None
    _metric_strip(
        ("Period", f"{year_min}–{year_max}" if year_min is not None else "Not available"),
        ("Stochastic runs", len(bundle.runs)),
        ("Recorded outcomes", int(bundle.annual_long["metric"].nunique()) if not bundle.annual_long.empty else 0),
    )
    for warning in bundle.warnings:
        st.warning(display_warning(warning))


def _trend_block(bundle: Any, *, validation: bool) -> None:
    """Outcome chart. ``validation`` overlays observed data; otherwise model output only."""

    if bundle.annual_long.empty:
        st.info("This result has no recognised annual model output.")
        return
    scope = "validation" if validation else "trend"
    metrics = _ordered_metrics(bundle)
    if validation:
        metrics = [m for m in metrics if not load_observations(ROOT, m).frame.empty]
        if not metrics:
            st.info(
                "No observed calibration or validation series are registered for this result's "
                "outcomes, so there is nothing to compare against."
            )
            return
    default_metric = (
        "new_diagnoses_msm"
        if "historical_2008" in bundle.source_path.parts and "new_diagnoses_msm" in metrics
        else metrics[0]
    )
    metric = st.selectbox(
        "Outcome", metrics, index=metrics.index(default_metric),
        format_func=lambda item: _metric_label(bundle, item),
        key=f"{scope}_metric_{bundle.source_path}",
    )
    selected = bundle.annual_long[bundle.annual_long["metric"] == metric]
    summary = bundle.across_run_summary[bundle.across_run_summary["metric"] == metric]
    displayed = pd.DataFrame()
    comparison_summary = summary
    if validation:
        observed = load_observations(ROOT, metric)
        observations = observed.frame
        available_roles = set(observations["role"])
        role1, role2 = st.columns(2)
        show_calibration = role1.checkbox(
            "Calibration targets (fitted)", value=True, disabled="calibration" not in available_roles,
            key=f"calibration_{bundle.source_path}_{metric}",
        )
        show_validation = role2.checkbox(
            "Validation and comparison data (held out)", value=True,
            disabled="cross_validation" not in available_roles,
            key=f"validation_{bundle.source_path}_{metric}",
        )
        roles = [r for r, on in (("calibration", show_calibration), ("cross_validation", show_validation)) if on]
        displayed = observations[observations["role"].isin(roles)]
        for warning in observed.warnings:
            st.caption(warning)
        if not displayed.empty and summary["route_id"].nunique() > 1:
            route_rows = summary[["route_id", "model", "p"]].drop_duplicates("route_id").set_index("route_id")

            def route_label(route_id: str) -> str:
                row = route_rows.loc[route_id]
                model = display_model(row["model"])
                probability = display_probability(row["p"], model=row["model"])
                return model if probability == "Not used" else f"{model} · {probability}"

            comparison_route = st.selectbox(
                "Model line for observed-data comparison", list(route_rows.index.astype(str)),
                format_func=route_label, key=f"observed_route_{bundle.source_path}_{metric}",
                help="Differences are calculated for one setup at a time so runs are not mixed across setups.",
            )
            comparison_summary = summary[summary["route_id"].astype(str) == comparison_route]
        _observed_difference_summary(comparison_summary, displayed)
    _ensemble_chart(bundle, metric, displayed)
    if validation:
        _observed_comparison_table(comparison_summary, displayed)
        return
    if len(bundle.runs) > 1 and st.toggle("Show individual stochastic runs", key=f"runs_{bundle.source_path}"):
        st.line_chart(
            selected.pivot_table(index="year", columns="run_id", values="value", aggfunc="first"),
        )
    with st.expander("Exact annual values"):
        exact_columns = ["year", "n", "median", "q05", "q95", "minimum", "maximum"]
        if summary["route_id"].nunique() > 1:
            exact_columns = ["route_id", "model", "p", *exact_columns]
        st.dataframe(
            summary[exact_columns].rename(columns={
                "route_id": "Model setup", "model": "Diagnosis pathway",
                "p": "Symptom-triggered diagnosis probability", "year": "Year", "n": "Runs",
                "median": "Median", "q05": "5th percentile", "q95": "95th percentile",
                "minimum": "Minimum", "maximum": "Maximum",
            }),
            width="stretch", hide_index=True, alt="Exact annual model values",
        )
    st.download_button(
        "Download normalized annual CSV", bundle.annual_long.to_csv(index=False).encode("utf-8"),
        file_name="annual_normalized.csv", mime="text/csv", icon=":material/download:",
    )


def _run_inventory(bundle: Any) -> None:
    run_rows = [{
        "run": display_warning(run.run_id),
        "model setup": display_model(run.model),
        "symptom-triggered diagnosis chance": display_probability(run.p, model=run.model),
        "seed": run.seed,
        "status": display_status(run.status),
    } for run in bundle.runs]
    if run_rows:
        st.dataframe(run_rows, width="stretch", hide_index=True, alt="Runs in this result")
    st.dataframe(
        [{
            "file": table.relative_path, "role": table.role, "rows": len(table.frame),
            "columns": len(table.frame.columns), "sha256": table.sha256,
        } for table in bundle.tables],
        width="stretch", hide_index=True, alt="Source files and hashes",
    )
    if bundle.tables:
        table_name = st.selectbox(
            "Preview source file", [t.relative_path for t in bundle.tables], key=f"table_{bundle.source_path}",
        )
        table = next(item for item in bundle.tables if item.relative_path == table_name)
        st.dataframe(table.frame.head(200), width="stretch", hide_index=True, alt="Source file preview")


def _export_block(bundle: Any, source: str) -> None:
    st.write(
        "The Excel workbook contains the summary, charts, exact annual values, individual runs, "
        "configuration, calibration fit when available, and provenance."
    )
    if st.button("Create Excel workbook", type="primary", icon=":material/table_view:"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = ROOT / "outputs" / "workbench_reports" / f"{bundle.source_path.name}-{stamp}"
        try:
            artifacts = build_report(bundle, output)
            st.session_state["latest_report"] = {
                "source": source, "workbook": str(artifacts.workbook_xlsx),
                "html": str(artifacts.index_html), "directory": str(artifacts.report_dir),
            }
            st.success("Excel workbook created.")
        except ResultImportError as exc:
            st.error(str(exc))
    latest = st.session_state.get("latest_report", {})
    if isinstance(latest, dict) and latest.get("source") == source:
        workbook_path = Path(str(latest.get("workbook", "")))
        if workbook_path.is_file():
            st.download_button(
                "Download Excel workbook", workbook_path.read_bytes(),
                file_name=f"{bundle.source_path.name}-model-results.xlsx", type="primary",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
            )
        st.caption(f"CSV, JSON, figures and the offline HTML report are stored in `{latest.get('directory', '')}`.")


def _result_kind_label(row: dict[str, str]) -> str:
    prefix = "Forecast and economics" if row.get("kind") == "economic" else "Historical"
    return f"{prefix} · {row['label']}"


def _render_results() -> None:
    st.title("Results")
    available = SERVICE.available_results()
    available.extend({**row, "kind": "economic"} for row in available_economic_results(ROOT))
    labels = {row["path"]: _result_kind_label(row) for row in available}
    paths = [row["path"] for row in available]
    default = st.session_state.get(
        "result_source", "examples/workbench/historical_2008" if HISTORICAL_2008.exists() else ""
    )
    if default and default not in paths:
        paths.append(default)
        tail = "/".join(Path(default).parts[-2:])
        labels[default] = (
            f"Your latest run · {tail}" if default == st.session_state.get("last_result")
            else f"Imported · {tail}"
        )
    source = st.selectbox(
        "Result", paths, index=paths.index(default) if default in paths else 0,
        format_func=lambda item: labels.get(item, item),
    ) if paths else ""
    if source:
        st.session_state["result_source"] = source
    with st.expander("Open a result from another folder"):
        custom = st.text_input("File or folder path", key="custom_result_path")
        if st.button("Open", disabled=not custom):
            st.session_state["result_source"] = custom
            st.rerun()
    source = st.session_state.get("result_source", source)
    if not source:
        st.info("No recognisable results yet. Run an analysis from Build and run.")
        return
    source_path = Path(source).expanduser()
    source_path = source_path if source_path.is_absolute() else ROOT / source_path
    if (source_path / ECONOMIC_MANIFEST).is_file():
        _render_economic_result(source_path)
        return
    try:
        bundle = _load_bundle(source)
    except (ResultImportError, OSError) as exc:
        st.error(str(exc))
        return
    if "historical_2008" in bundle.source_path.parts:
        st.info(
            "Lowest confirmed maximum implausibility in the retained 2008 shortlist; it passed the "
            "primary rule across 20 stochastic runs.",
            title="Default reference fit — 2008 treatment-era change",
        )
    _bundle_header(bundle)
    has_fit = (bundle.source_path / "target_fit_candidate_1787.csv").is_file()
    fit_tab, validation_tab, trend_tab, files_tab, export_tab = st.tabs(
        ["Calibration fit", "Validation against data", "Epidemic trends", "Runs and files", "Export"],
        default=None if has_fit else "Validation against data",
    )
    with fit_tab:
        if has_fit:
            _historical_fit_summary(bundle.source_path)
        else:
            st.info(
                "No calibration-fit table is stored with this result. Use the next tab to see the "
                "model against observed data."
            )
    with validation_tab:
        _trend_block(bundle, validation=True)
    with trend_tab:
        _trend_block(bundle, validation=False)
    with files_tab:
        _run_inventory(bundle)
    with export_tab:
        _export_block(bundle, source)


def _render_compare() -> None:
    st.title("Compare results")
    available = SERVICE.available_results()
    if len(available) < 2:
        st.info(
            "One compatible result is available. Run or import another result before comparing."
        )
    options = [row["path"] for row in available]
    labels = {row["path"]: row["label"] for row in available}
    with st.expander("Add results created outside the workbench"):
        first_custom = st.text_input("First imported file or folder", key="compare_custom_1")
        second_custom = st.text_input("Second imported file or folder", key="compare_custom_2")
        for value in (first_custom, second_custom):
            if value and value not in options:
                options.append(value)
                labels[value] = f"Imported result: {Path(value).name}"
    if not options:
        return
    col1, col2 = st.columns(2)
    left_path = col1.selectbox(
        "First result",
        options,
        format_func=lambda item: labels.get(item, item),
        key="compare_left",
    )
    right_options = [item for item in options if item != left_path]
    right_path = col2.selectbox(
        "Second result",
        right_options,
        format_func=lambda item: labels.get(item, item),
        key="compare_right",
    ) if right_options else ""
    if not st.button("Compare selected results", disabled=not right_path):
        return
    try:
        left = _load_bundle(left_path)
        right = _load_bundle(right_path)
    except (ResultImportError, OSError) as exc:
        st.error(str(exc))
        return
    left_label, right_label = labels.get(left_path, left.source_path.name), labels.get(
        right_path, right.source_path.name
    )
    st.dataframe([
        {"result": left_label, "files": len(left.tables), "trajectories": len(left.runs), "metrics": left.annual_long["metric"].nunique()},
        {"result": right_label, "files": len(right.tables), "trajectories": len(right.runs), "metrics": right.annual_long["metric"].nunique()},
    ], width="stretch", hide_index=True)
    if left.annual_long.empty or right.annual_long.empty:
        st.info("Both sources need recognized annual model results for a chart comparison.")
        return
    common = sorted(set(left.annual_long["metric"]) & set(right.annual_long["metric"]))
    if not common:
        st.info("The two sources have no normalized annual metric in common.")
        return
    metric = st.selectbox(
        "Outcome",
        common,
        format_func=lambda item: _metric_label(left, item),
        key="compare_metric",
    )
    parts = []
    for label, bundle in ((left_label, left), (right_label, right)):
        frame = bundle.across_run_summary[
            bundle.across_run_summary["metric"] == metric
        ].copy()
        frame["series"] = label
        parts.append(frame)
    combined = pd.concat(parts, ignore_index=True)
    comparison_chart = alt.Chart(combined).mark_line(size=2.5).encode(
        x=alt.X("year:Q", title="Year", axis=alt.Axis(format="d")),
        y=alt.Y("median:Q", title=_metric_label(left, metric)),
        color=alt.Color("series:N", title="Result"),
        tooltip=[
            alt.Tooltip("series:N", title="Result"),
            alt.Tooltip("year:Q", format=".0f"),
            alt.Tooltip("median:Q", title="Median", format=",.3~g"),
            alt.Tooltip("n:Q", title="Stochastic runs"),
        ],
    )
    st.altair_chart(comparison_chart.interactive(), width="stretch")
    st.caption(
        "Lines compare each result's median. Positive-p M1 and M0 are interpreted as "
        "distributional comparisons, not paired seed-level effects."
    )
    with st.expander("Exact comparison values"):
        st.dataframe(
            combined[["series", "year", "n", "median", "q05", "q95"]].rename(
                columns={
                    "series": "Result", "year": "Year", "n": "Runs",
                    "median": "Median", "q05": "5th percentile",
                    "q95": "95th percentile",
                }
            ),
            width="stretch",
            hide_index=True,
        )


def _render_configurations() -> None:
    st.title("Technical details")
    st.write(
        "Configuration identities, scientific status and full JSON are retained here for "
        "audit. Most users do not need this page to review or run the model."
    )
    rows = SERVICE.list_configurations()
    historical_config = HISTORICAL_2008 / "selected_model_break_2008.json"
    if historical_config.is_file():
        rows.append({
            "path": historical_config.relative_to(ROOT).as_posix(),
            "role_class": "historical retained",
            "scientific_status": (
                "Confirmed fit for the recorded 29 September 2026 analysis; "
                "not current policy evidence."
            ),
            "safe_collaborator_use": (
                "Inspect only. The current run_model.py contract does not rerun this configuration."
            ),
        })
    role_options = sorted({row.get("role_class", "") for row in rows})
    selected_roles = st.multiselect("Role", role_options)
    filtered = [row for row in rows if not selected_roles or row.get("role_class", "") in selected_roles]
    st.dataframe(filtered, width="stretch", hide_index=True)
    if not filtered:
        return
    selected = st.selectbox("Inspect configuration", [row["path"] for row in filtered])
    row = next(item for item in filtered if item["path"] == selected)
    _show_status_text(row)
    st.json(SERVICE.configuration(selected), expanded=False)


def _target_name(target: dict[str, Any]) -> str:
    return str(target.get("series_name", target.get("name", "Unnamed target")))


def _render_data_targets() -> None:
    st.title("Data and targets")
    try:
        summary, inputs = SERVICE.external_inputs_status()
    except WorkbenchUIError as exc:
        st.error(str(exc))
        return
    st.subheader("External inputs")
    st.json(summary, expanded=False)
    st.dataframe(inputs, width="stretch", hide_index=True)
    registers = SERVICE.target_registers()
    if not registers:
        st.write("No tracked target registers were found.")
        return
    st.subheader("Target register")
    register_path = st.selectbox("Register", registers)
    register = SERVICE.configuration(register_path)
    targets = register.get("targets", [])
    st.dataframe([{
        "target": _target_name(item),
        "model output": item.get("model_output"),
        "source sheet": item.get("source_sheet", item.get("sheet")),
        "role": item.get("role", item.get("calibration_use")),
        "year coverage": item.get("year_coverage"),
    } for item in targets], width="stretch", hide_index=True)
    if targets:
        selected_name = st.selectbox("Inspect target", [_target_name(item) for item in targets])
        target = next(item for item in targets if _target_name(item) == selected_name)
        st.json(target, expanded=False)
    else:
        selected_name = "target"
        target = None
    workbook_raw = register.get("source_workbook", "../data/raw/validation/DiseaseData.xlsx")
    workbook_path = (ROOT / workbook_raw).resolve()
    st.subheader("DiseaseData workbook")
    try:
        metadata = SERVICE.workbook_metadata(workbook_path)
        st.write({key: value for key, value in metadata.items() if key != "sheets"})
        st.dataframe(metadata["sheets"], width="stretch", hide_index=True)
    except WorkbenchUIError as exc:
        st.warning(str(exc))
        return
    st.subheader("Propose a new observed value")
    st.write(
        "This exports a proposal for review. It does not edit the workbook or change "
        "the model's calibration targets."
    )
    default_sheet = str(target.get("source_sheet", target.get("sheet", ""))) if target else ""
    sheet_names = [item["name"] for item in metadata["sheets"]]
    sheet_index = sheet_names.index(default_sheet) if default_sheet in sheet_names else 0
    sheet = st.selectbox("Workbook sheet", sheet_names, index=sheet_index)
    try:
        preview = SERVICE.workbook_sheet_preview(workbook_path, sheet)
        st.write(
            f"Worksheet size: {preview['worksheet_rows']} rows × "
            f"{preview['worksheet_columns']} columns."
        )
        if preview["truncated"]:
            st.caption("Preview is limited to the first 200 rows and 60 columns.")
        preview_frame = pd.DataFrame(
            preview["rows"], columns=preview["columns"]
        ).fillna("").astype(str)
        st.dataframe(
            preview_frame,
            width="stretch",
            hide_index=True,
        )
    except WorkbenchUIError as exc:
        st.warning(str(exc))
    year_column = str(target.get("year_column", "Year")) if target else "Year"
    value_column = str(target.get("estimate_column", "Value")) if target else "Value"
    year = st.number_input("Year", min_value=1900, max_value=2200, value=2025, step=1)
    value = st.number_input("Value", value=0.0, format="%.10g")
    note = st.text_area("Source or review note", key="point_patch_note")
    patch = SERVICE.point_patch(
        workbook_path=workbook_path,
        sheet=sheet,
        values={year_column: int(year), value_column: float(value)},
        note=note,
    )
    st.download_button(
        "Download point patch",
        patch.encode("utf-8"),
        file_name=f"{re.sub(r'[^a-z0-9]+', '-', selected_name.lower()).strip('-')}-{int(year)}-patch.json",
        mime="application/json",
    )


def _render_setup() -> None:
    st.title("Setup")
    st.write("Check that this installation can validate and run the model.")
    default_python = sys.executable
    python_path = st.text_input(
        "Model Python executable",
        value=default_python if Path(default_python).is_file() else "python3.11",
    )
    if st.button("Run setup checks"):
        st.dataframe(SERVICE.setup_checks(python_path), width="stretch", hide_index=True)

    st.subheader("Parallel worker check")
    st.write(
        "Confirm that this computer can launch independent Python child processes before "
        "using more than one worker. This check does not run the HIV model."
    )
    options = _worker_options(2)
    st.session_state.setdefault("setup_workers", 2)
    workers = int(st.selectbox(
        "Workers to test",
        options,
        format_func=worker_label,
        key="setup_workers",
        help="The quick check tests at most eight child processes.",
    ))
    if st.button("Test workers", icon=":material/memory:"):
        try:
            result = SERVICE.parallel_worker_check(workers)
            if result["status"] == "PASS":
                if result["tested"] == 1:
                    st.success("The child-process check passed. Choose 1 worker for serial runs.")
                else:
                    st.success(
                        f"Parallel child-process check passed with {result['tested']} workers."
                    )
            else:
                st.error("The child-process check failed. Choose 1 worker on this computer.")
            st.caption(
                f"Detected {result['available']} logical processors; "
                f"observed {result['child_processes']} independent child processes."
            )
        except Exception as exc:
            st.error(f"The child-process check failed: {exc}")
    st.caption(
        "The same launcher is used on Windows, macOS and Linux. Each model run writes to a "
        "separate seed folder."
    )


def _queue_saved_configuration(kind: str, key: str, label: str) -> None:
    st.session_state["_pending_load"] = (kind, key, label)
    _request_page("build")


def _apply_pending_load() -> None:
    """Load a configuration chosen on Home before the Build widgets are created."""

    pending = st.session_state.pop("_pending_load", None)
    if not pending:
        return
    kind, key, label = pending
    if kind == "historical":
        loaded = SERVICE.load_recipe(key)
        st.session_state["analysis_draft"] = loaded.to_dict()
        st.session_state["analysis_base_draft"] = loaded.to_dict()
        _apply_historical_draft_to_widgets(loaded)
        st.session_state["wb_mode"] = MODES[0]
        st.session_state["hist_start_choice"] = label
    else:
        recipe = next((r for r in list_economic_recipes(ROOT) if r["id"] == key), None)
        if recipe is not None:
            _load_economic_recipe(recipe["settings"])
            st.session_state["wb_mode"] = MODES[1]
            st.session_state["econ_start_choice"] = label


def _render_home() -> None:
    st.title("HIV PrEP model workbench")
    st.write(
        "Configure, run and review the Dutch HIV transmission and PrEP cost-effectiveness model "
        "from inputs alone. No config files, no code."
    )
    st.info(
        "This is a research tool. The model is not yet production-calibrated, so treat results as "
        "exploratory unless a result page states otherwise. Every input's source and uncertainty is "
        "in the Evidence register.",
        title="Scientific status",
    )

    saved_hist = [i for i in SERVICE.list_recipes() if i.get("source") == "saved"]
    saved_econ = list_economic_recipes(ROOT)
    left, middle, right = st.columns(3)
    with left.container(border=True, height="stretch"):
        st.subheader(":material/add_circle: New analysis")
        st.write("Start from the recorded configuration, choose historical or forecast, and edit inputs.")
        st.button(
            "Build and run", type="primary", width="stretch",
            on_click=_request_page, args=("build",),
        )
    with middle.container(border=True, height="stretch"):
        st.subheader(":material/folder_open: Saved configuration")
        options = {
            **{f"Historical · {i['name']}": ("historical", i["id"], f"Saved · {i['name']}") for i in saved_hist},
            **{f"Forecast · {i['name']}": ("economic", i["id"], f"Saved · {i['name']}") for i in saved_econ},
        }
        if options:
            choice = st.selectbox("Configuration", list(options), label_visibility="collapsed")
            st.button(
                "Open and edit", width="stretch", on_click=_queue_saved_configuration, args=options[choice],
            )
        else:
            st.caption("Nothing saved yet. Save a configuration from the review step of Build and run.")
    with right.container(border=True, height="stretch"):
        st.subheader(":material/monitoring: Existing result")
        st.write("Open the reference fit, or any completed historical or forecast run.")
        st.button(
            "Review results", width="stretch",
            on_click=_request_page, args=("results",),
            kwargs={"result_source": st.session_state.get("last_result", "examples/workbench/historical_2008")},
        )

    st.subheader("How the workbench flows")
    steps = [
        ("1 · Build", "Pick **historical fit** or **forecast and economics**, then load defaults or a saved configuration."),
        ("2 · Adjust", "Edit inputs in tables that show the recorded value, 95% interval and source side by side."),
        ("3 · Validate", "Review calibration fit and the model against observed data before trusting a forecast."),
        ("4 · Interpret", "Read headline ICER and NMB, cost components, adverse events and budget impact."),
    ]
    for column, (title, text) in zip(st.columns(4), steps):
        with column.container(border=True, height="stretch"):
            st.markdown(f"**{title}**")
            st.caption(text)

    st.subheader("Reference fit")
    _metric_strip(
        ("Retained fit", "2008 treatment-era"), ("Stochastic runs", "20"),
        ("Largest implausibility", "2.54", {"help": "Primary threshold: I-max ≤ 3"}),
    )
    st.caption(
        "Why is the historical start fixed? The starting population and epidemic seed are bundled "
        "with the start year; changing the year alone would create a mismatched initial state."
    )


def _render_evidence() -> None:
    st.title("Evidence register")
    st.write(
        "Every parameter the model uses, with its value, declared distribution, 95% interval and "
        "source. Built directly from the configuration files, so it cannot drift from what runs."
    )
    frame = _register()
    summary = evidence_summary(frame)
    if frame.empty:
        st.warning("No configuration files with parameter metadata were found.")
        return
    total = summary["parameters"]
    _metric_strip(
        ("Parameters", total),
        (f"With a source ({summary['with_source'] / total:.0%})", summary["with_source"]),
        (f"With an interval ({summary['with_interval'] / total:.0%})", summary["with_interval"]),
        (f"With a distribution ({summary['with_distribution'] / total:.0%})", summary["with_distribution"]),
    )

    register_tab, distribution_tab, cost_tab = st.tabs(["Parameter register", "Distributions", "Cost matrix"])
    with register_tab:
        area_col, search_col = st.columns([2, 1], vertical_alignment="bottom")
        areas = area_col.multiselect("Area", sorted(frame["Area"].unique()), placeholder="All areas")
        query = search_col.text_input("Search", placeholder="e.g. lenacapavir, beta, MDR")
        gaps = st.toggle("Only show gaps (no source or no interval)")
        shown = frame
        if areas:
            shown = shown[shown["Area"].isin(areas)]
        if query:
            shown = shown[shown.drop(columns="_dist").astype(str).apply(
                lambda col: col.str.contains(query, case=False, regex=False)
            ).any(axis=1)]
        if gaps:
            shown = shown[(shown["Source"] == "Not recorded") | (shown["Interval basis"] == "None recorded")]
        st.caption(f"{len(shown)} of {len(frame)} parameters")
        st.dataframe(
            shown.drop(columns=["_dist"]), width="stretch", hide_index=True, height=520,
            alt="Parameter evidence register",
            column_config={
                "Parameter": st.column_config.TextColumn(width="large"),
                "Source": st.column_config.TextColumn(width="large"),
                "Notes": st.column_config.TextColumn(width="large"),
                "Config path": st.column_config.TextColumn(width="medium"),
            },
        )
        st.download_button(
            "Download register (CSV)", shown.drop(columns=["_dist"]).to_csv(index=False).encode("utf-8"),
            file_name="parameter_register.csv", mime="text/csv", icon=":material/download:",
        )
        st.caption(
            "95% interval is derived from the declared distribution when one is parameterised, otherwise "
            "taken from recorded bounds. “Not recorded” is a real gap in the configuration, not a display fault."
        )
    with distribution_tab:
        with_dist = frame[frame["_dist"].apply(lambda d: distribution_curve(d) is not None)]
        if with_dist.empty:
            st.info("No parameterised distributions were found.")
        else:
            labels = (with_dist["Area"] + " · " + with_dist["Parameter"]).tolist()
            pick = st.selectbox("Parameter", labels)
            row = with_dist.iloc[labels.index(pick)]
            curve, (low, high) = distribution_curve(row["_dist"])
            band = pd.DataFrame({"low": [low], "high": [high]})
            density = alt.Chart(curve).mark_area(opacity=0.35, line=True).encode(
                x=alt.X("value:Q", title=f"{row['Parameter']} ({row['Unit'] or 'value'})"),
                y=alt.Y("density:Q", title="Density"),
            )
            interval = alt.Chart(band).mark_rect(opacity=0.15, color="#0b5c7a").encode(x="low:Q", x2="high:Q")
            point = alt.Chart(pd.DataFrame({"value": [float(row["Value"])]})).mark_rule(
                color="#b3261e", strokeWidth=2
            ).encode(x="value:Q")
            st.altair_chart(interval + density + point, width="stretch", alt=f"Distribution of {row['Parameter']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Base-case value", row["Value"], border=True)
            c2.metric("Distribution", row["Distribution"], border=True)
            c3.metric("95% interval", row["95% interval"], border=True)
            st.caption("Red line: value used in the base case. Shaded band: central 95% of the declared distribution.")
            st.markdown(f"**Source:** {row['Source']}")
            if row["Notes"]:
                st.caption(row["Notes"])
    with cost_tab:
        matrix = cost_matrix(ROOT)
        if matrix.empty:
            st.info("No costing configuration found.")
        else:
            groups = st.multiselect("Group", sorted(matrix["Group"].unique()), placeholder="All groups")
            shown = matrix[matrix["Group"].isin(groups)] if groups else matrix
            st.dataframe(
                shown, width="stretch", hide_index=True, alt="Unit cost matrix",
                column_config={
                    "Item": st.column_config.TextColumn(width="large"),
                    "Source": st.column_config.TextColumn(width="large"),
                    "Value (EUR)": st.column_config.NumberColumn(format="€%.2f"),
                },
            )
            st.download_button(
                "Download cost matrix (CSV)", shown.to_csv(index=False).encode("utf-8"),
                file_name="cost_matrix.csv", mime="text/csv", icon=":material/download:",
            )


def _render_model_guide() -> None:
    st.title("Model guide")
    st.write(MODEL_INTRO)
    st.caption(MODEL_SCOPE)
    pathway_tab, calibration_tab, terms_tab, formulas_tab = st.tabs(
        ["Disease pathway", "How it was calibrated", "Key terms and parameters", "Formulas and references"]
    )
    with pathway_tab:
        st.image(_svg_data_url(DISEASE_PATHWAY_SVG), width="stretch", caption="Author's schematic of implemented model logic.")
        st.image(_svg_data_url(STATE_SPACE_SVG), width="stretch", caption="Population state space.")
        st.image(_svg_data_url(ANALYSIS_WORKFLOW_SVG), width="stretch", caption="Analysis layers and hand-off.")
    with calibration_tab:
        st.write(HISTORY_MATCHING_INTRO)
        for title, explanation in HISTORY_MATCHING_COMPONENTS:
            st.markdown(f"- **{title}:** {explanation}")
        st.info(VALIDATION_NOTE)
    with terms_tab:
        st.dataframe(KEY_TERMS, width="stretch", hide_index=True, alt="Key terms")
        st.dataframe(PARAMETER_FAMILIES, width="stretch", hide_index=True, alt="Parameter families")
    with formulas_tab:
        st.dataframe(
            [{"Use": label, "Definition": formula} for label, formula in FORMULAS],
            width="stretch", hide_index=True, alt="Formulas",
        )
        for reference in REFERENCES:
            st.markdown(f"- [{reference['label']}]({reference['url']})")


def _sidebar_status() -> None:
    with st.sidebar:
        st.divider()
        st.caption("WORKING ANALYSIS")
        status = st.session_state.get("wb_status")
        if not status:
            st.write("Nothing configured yet.")
            st.caption("Open Build and run to start.")
        else:
            st.markdown(f"**{status['title']}**")
            st.caption(f"{status['detail']} · {status['changes']} input change{'s' if status['changes'] != 1 else ''}")
            if status["ok"]:
                st.badge("Ready to run", icon=":material/check_circle:", color="green")
            else:
                st.badge(f"{status['problems']} to fix", icon=":material/error:", color="red")
        last = st.session_state.get("last_result")
        if last:
            st.button(
                "Open latest result", icon=":material/arrow_forward:", width="stretch",
                on_click=_open_completed_result, args=(last,),
            )


def main() -> None:
    st.set_page_config(page_title="HIV PrEP model workbench", page_icon=":material/biotech:", layout="wide")
    pages = {
        "home": st.Page(_render_home, title="Home", icon=":material/home:", default=True),
        "build": st.Page(_render_build, title="Build and run", icon=":material/tune:", url_path="build"),
        "results": st.Page(_render_results, title="Results", icon=":material/monitoring:", url_path="results"),
        "compare": st.Page(_render_compare, title="Compare results", icon=":material/compare:", url_path="compare"),
        "evidence": st.Page(_render_evidence, title="Evidence register", icon=":material/fact_check:", url_path="evidence"),
        "targets": st.Page(_render_data_targets, title="Data and targets", icon=":material/database:", url_path="targets"),
        "guide": st.Page(_render_model_guide, title="Model guide", icon=":material/menu_book:", url_path="guide"),
        "technical": st.Page(_render_configurations, title="Technical details", icon=":material/code:", url_path="technical"),
        "history": st.Page(_render_runs, title="Run history", icon=":material/history:", url_path="history"),
        "setup": st.Page(_render_setup, title="Setup", icon=":material/settings:", url_path="setup"),
    }
    navigation = st.navigation({
        "Workflow": [pages["home"], pages["build"], pages["results"], pages["compare"]],
        "Evidence": [pages["evidence"], pages["targets"], pages["guide"]],
        "System": [pages["technical"], pages["history"], pages["setup"]],
    })
    target = st.session_state.pop("_goto", None)
    if target in pages and pages[target] is not navigation:
        st.switch_page(pages[target])
    navigation.run()
    _sidebar_status()


if __name__ == "__main__":
    main()
