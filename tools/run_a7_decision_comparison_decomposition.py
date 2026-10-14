"""Run Phase-2 task A-7 on deterministic lenacapavir substitution scenarios.

The diagnostic decomposes discounted incremental QALYs at all five switch
levels without changing the production comparator.  It also checks person-time,
event-flow accounting, and alignment of the infection and QALY windows.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_utils import extract_value  # noqa: E402
from economics import QALY_COMPONENT_KEYS  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402


SCENARIO_PREFIX = "scenario_a_lena_substitution__switch_"
EXPECTED_SWITCHES = (10, 25, 50, 75, 100)
REFERENCE_ARM = "oral_prep"
INTERVENTION_ARM = "oral_prep_plus_lena"

SUSCEPTIBLE_COMPONENTS = (
    "susceptible_no_prep",
    "susceptible_oral_prep",
    "susceptible_lenacapavir",
    "susceptible_lenacapavir_waning",
)
HIV_COMPONENTS = (
    "hiv_acute",
    "hiv_chronic_on_art",
    "hiv_chronic_off_art",
    "hiv_aids_on_art",
    "hiv_aids_off_art",
)
DECREMENT_COMPONENTS = (
    "utility_decrement_art_initiation",
    "utility_decrement_adverse_events",
)
FLOW_COLUMNS = (
    "new_infections",
    "hiv_deaths",
    "aids_deaths",
    "background_deaths",
    "childhood_deaths",
    "births",
    "net_migration",
    "new_aids_cases",
    "new_diagnoses",
    "art_initiations",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(args: Iterable[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return completed.stdout.rstrip()


def _scenario_switch(name: str) -> int:
    if not name.startswith(SCENARIO_PREFIX):
        raise ValueError(f"not an A-7 substitution scenario: {name!r}")
    return int(name.removeprefix(SCENARIO_PREFIX))


def _select_scenarios(scenarios: Sequence[dict]) -> List[dict]:
    selected = [s for s in scenarios if s.get("name", "").startswith(SCENARIO_PREFIX)]
    selected.sort(key=lambda scenario: _scenario_switch(scenario["name"]))
    observed = tuple(_scenario_switch(scenario["name"]) for scenario in selected)
    if observed != EXPECTED_SWITCHES:
        raise ValueError(
            f"expected A-7 switch levels {EXPECTED_SWITCHES}, found {observed}"
        )
    for scenario in selected:
        if scenario.get("arm_order") != [REFERENCE_ARM, INTERVENTION_ARM]:
            raise ValueError(
                f"unexpected arm order for {scenario['name']}: "
                f"{scenario.get('arm_order')!r}"
            )
    return selected


def _verify_susceptible_utilities(hta_config: dict) -> Dict[str, float]:
    utilities = hta_config["utilities"]
    keys = (
        "susceptible",
        "susceptible_oral_prep",
        "susceptible_lenacapavir",
    )
    values = {key: float(extract_value(utilities[key])) for key in keys}
    if any(value != 1.0 for value in values.values()):
        raise AssertionError(
            "A-7 identity requires all susceptible utilities to equal 1.0; "
            f"found {values}"
        )
    if "susceptible_lenacapavir_waning" in utilities:
        raise AssertionError(
            "A-7 assumes the waning state inherits susceptible utility, but an "
            "explicit susceptible_lenacapavir_waning utility is now configured"
        )
    return values


def _component_totals(result) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for component in QALY_COMPONENT_KEYS:
        column = f"step_qaly_{component}_disc"
        if any(column not in record for record in result.timestep_records):
            raise KeyError(
                f"{result.analysis_scenario}/{result.name} is missing {column}"
            )
        totals[component] = math.fsum(
            float(record[column]) for record in result.timestep_records
        )
    component_sum = math.fsum(totals.values())
    if not math.isclose(
        component_sum,
        float(result.total_qalys_disc),
        rel_tol=1e-12,
        abs_tol=1e-6,
    ):
        raise AssertionError(
            f"QALY components do not reconcile for "
            f"{result.analysis_scenario}/{result.name}: residual="
            f"{component_sum - result.total_qalys_disc:.12f}"
        )
    return totals


def _validate_windows(reference, intervention) -> dict:
    if not reference.timestep_records or not intervention.timestep_records:
        raise AssertionError("A-7 requires non-empty economics timestep records")
    if len(reference.timestep_records) != len(intervention.timestep_records):
        raise AssertionError("economics timestep counts differ between arms")
    ref_axis = [(r["step"], r["year"]) for r in reference.timestep_records]
    intv_axis = [(r["step"], r["year"]) for r in intervention.timestep_records]
    if ref_axis != intv_axis:
        raise AssertionError("economics timestep axes differ between arms")

    rows = {}
    for arm_name, result in ((REFERENCE_ARM, reference), (INTERVENTION_ARM, intervention)):
        econ_infections = math.fsum(
            float(record["step_new_infections"])
            for record in result.timestep_records
        )
        sim_eval = [
            record
            for record in result.simulation_records
            if float(record["year"]) >= float(result.timestep_records[0]["year"])
            and float(record["year"]) <= float(result.timestep_records[-1]["year"])
        ]
        sim_infections = math.fsum(float(record["new_infections"]) for record in sim_eval)
        if not math.isclose(econ_infections, float(result.cumul_new_infections), abs_tol=0.0):
            raise AssertionError(f"economics infection total mismatch in {arm_name}")
        if not math.isclose(sim_infections, econ_infections, abs_tol=0.0):
            raise AssertionError(f"simulation/economics infection window mismatch in {arm_name}")
        rows[arm_name] = {
            "start_step": int(result.timestep_records[0]["step"]),
            "end_step": int(result.timestep_records[-1]["step"]),
            "start_year": float(result.timestep_records[0]["year"]),
            "end_year": float(result.timestep_records[-1]["year"]),
            "n_steps": len(result.timestep_records),
            "economics_infections": econ_infections,
            "simulation_infections": sim_infections,
        }
    if rows[REFERENCE_ARM] != rows[INTERVENTION_ARM]:
        differing = {
            key: (rows[REFERENCE_ARM][key], rows[INTERVENTION_ARM][key])
            for key in rows[REFERENCE_ARM]
            if key not in {"economics_infections", "simulation_infections"}
            and rows[REFERENCE_ARM][key] != rows[INTERVENTION_ARM][key]
        }
        if differing:
            raise AssertionError(f"arm window metadata differs: {differing}")
    return rows


def _person_years(result, *, discounted: bool) -> float:
    return math.fsum(
        float(record["total_population"])
        * float(record["step_fraction"])
        * (float(record["discount_factor_health"]) if discounted else 1.0)
        for record in result.timestep_records
    )


def _sum_simulation_flow(result, column: str, start_year: float, end_year: float) -> float:
    return math.fsum(
        float(record[column])
        for record in result.simulation_records
        if start_year <= float(record["year"]) <= end_year
    )


def _decompose_pair(reference, intervention) -> tuple[dict, List[dict], List[dict]]:
    if reference.analysis_scenario != intervention.analysis_scenario:
        raise AssertionError("arm scenario names differ")
    scenario = reference.analysis_scenario
    switch = _scenario_switch(scenario)
    windows = _validate_windows(reference, intervention)
    start_year = windows[REFERENCE_ARM]["start_year"]
    end_year = windows[REFERENCE_ARM]["end_year"]

    ref_components = _component_totals(reference)
    intv_components = _component_totals(intervention)
    deltas = {
        component: intv_components[component] - ref_components[component]
        for component in QALY_COMPONENT_KEYS
    }
    delta_qaly = float(intervention.total_qalys_disc - reference.total_qalys_disc)
    component_delta_sum = math.fsum(deltas.values())
    if not math.isclose(component_delta_sum, delta_qaly, rel_tol=1e-12, abs_tol=1e-6):
        raise AssertionError(f"incremental QALY components do not reconcile for {scenario}")

    ref_dpys = _person_years(reference, discounted=True)
    intv_dpys = _person_years(intervention, discounted=True)
    ref_upys = _person_years(reference, discounted=False)
    intv_upys = _person_years(intervention, discounted=False)
    ref_susc = math.fsum(ref_components[key] for key in SUSCEPTIBLE_COMPONENTS)
    intv_susc = math.fsum(intv_components[key] for key in SUSCEPTIBLE_COMPONENTS)
    ref_hiv_qaly = math.fsum(ref_components[key] for key in HIV_COMPONENTS)
    intv_hiv_qaly = math.fsum(intv_components[key] for key in HIV_COMPONENTS)
    ref_hiv_py = ref_dpys - ref_susc
    intv_hiv_py = intv_dpys - intv_susc
    ref_hiv_shortfall = ref_hiv_qaly - ref_hiv_py
    intv_hiv_shortfall = intv_hiv_qaly - intv_hiv_py
    delta_decrements = math.fsum(deltas[key] for key in DECREMENT_COMPONENTS)
    delta_discounted_py = intv_dpys - ref_dpys
    delta_hiv_shortfall = intv_hiv_shortfall - ref_hiv_shortfall
    identity_sum = delta_discounted_py + delta_hiv_shortfall + delta_decrements
    if not math.isclose(identity_sum, delta_qaly, rel_tol=1e-12, abs_tol=1e-6):
        raise AssertionError(
            f"person-time/QALY identity does not reconcile for {scenario}: "
            f"residual={identity_sum - delta_qaly:.12f}"
        )

    row = {
        "scenario": scenario,
        "switch_percent": switch,
        "reference_arm": REFERENCE_ARM,
        "intervention_arm": INTERVENTION_ARM,
        "delta_qaly": delta_qaly,
        "infections_averted": (
            float(reference.cumul_new_infections)
            - float(intervention.cumul_new_infections)
        ),
        "reference_person_years": ref_upys,
        "intervention_person_years": intv_upys,
        "delta_person_years": intv_upys - ref_upys,
        "reference_discounted_person_years": ref_dpys,
        "intervention_discounted_person_years": intv_dpys,
        "delta_discounted_person_years": delta_discounted_py,
        "delta_hiv_utility_shortfall": delta_hiv_shortfall,
        "delta_utility_decrements": delta_decrements,
        "person_time_identity_sum": identity_sum,
        "person_time_identity_residual": identity_sum - delta_qaly,
        "component_delta_sum": component_delta_sum,
        "component_reconciliation_residual": component_delta_sum - delta_qaly,
        "window_start_year": start_year,
        "window_end_year": end_year,
        "window_n_steps": windows[REFERENCE_ARM]["n_steps"],
    }
    for component, value in deltas.items():
        row[f"delta_{component}"] = value

    component_rows = []
    for component in QALY_COMPONENT_KEYS:
        component_rows.append(
            {
                "scenario": scenario,
                "switch_percent": switch,
                "component": component,
                "reference_qaly_disc": ref_components[component],
                "intervention_qaly_disc": intv_components[component],
                "delta_qaly_disc": deltas[component],
            }
        )

    flow_rows = []
    for column in FLOW_COLUMNS:
        ref_value = _sum_simulation_flow(reference, column, start_year, end_year)
        intv_value = _sum_simulation_flow(intervention, column, start_year, end_year)
        flow_rows.append(
            {
                "scenario": scenario,
                "switch_percent": switch,
                "flow": column,
                "reference_total": ref_value,
                "intervention_total": intv_value,
                "delta_intervention_minus_reference": intv_value - ref_value,
            }
        )

    ref_sim = [r for r in reference.simulation_records if start_year <= float(r["year"]) <= end_year]
    intv_sim = [r for r in intervention.simulation_records if start_year <= float(r["year"]) <= end_year]
    if len(ref_sim) != len(intv_sim) or not ref_sim:
        raise AssertionError(f"simulation record windows do not align for {scenario}")
    final_population_delta = (
        float(intv_sim[-1]["total_population"]) - float(ref_sim[-1]["total_population"])
    )
    flow_map = {r["flow"]: r["delta_intervention_minus_reference"] for r in flow_rows}
    implied_population_delta = (
        flow_map["births"]
        + flow_map["net_migration"]
        - flow_map["hiv_deaths"]
        - flow_map["background_deaths"]
        - flow_map["childhood_deaths"]
    )
    row["final_total_population_delta"] = final_population_delta
    row["population_flow_implied_delta"] = implied_population_delta
    row["population_flow_balance_residual"] = final_population_delta - implied_population_delta
    return row, component_rows, flow_rows


def _timestep_rows(reference, intervention) -> List[dict]:
    rows = []
    for ref, intv in zip(reference.timestep_records, intervention.timestep_records):
        row = {
            "scenario": reference.analysis_scenario,
            "switch_percent": _scenario_switch(reference.analysis_scenario),
            "step": ref["step"],
            "year": ref["year"],
            "delta_total_population": float(intv["total_population"] - ref["total_population"]),
            "delta_person_years": (
                float(intv["total_population"] - ref["total_population"])
                * float(ref["step_fraction"])
            ),
            "delta_discounted_person_years": (
                float(intv["total_population"] - ref["total_population"])
                * float(ref["step_fraction"])
                * float(ref["discount_factor_health"])
            ),
            "delta_qaly_disc": float(intv["step_qalys_disc"] - ref["step_qalys_disc"]),
            "infections_averted": float(ref["step_new_infections"] - intv["step_new_infections"]),
        }
        for component in QALY_COMPONENT_KEYS:
            column = f"step_qaly_{component}_disc"
            row[f"delta_{component}"] = float(intv[column] - ref[column])
        rows.append(row)
    return rows


def _simulation_delta_rows(reference, intervention) -> List[dict]:
    start_year = float(reference.timestep_records[0]["year"])
    end_year = float(reference.timestep_records[-1]["year"])
    ref_rows = [r for r in reference.simulation_records if start_year <= float(r["year"]) <= end_year]
    intv_rows = [r for r in intervention.simulation_records if start_year <= float(r["year"]) <= end_year]
    if [(r["step"], r["year"]) for r in ref_rows] != [(r["step"], r["year"]) for r in intv_rows]:
        raise AssertionError("simulation timestep axes differ between arms")
    columns = list(FLOW_COLUMNS) + [
        "total_population",
        "sexually_active_population",
        "childhood_population",
        "adult_hiv_pos",
        "on_art_stock",
        "msm_on_oral",
        "msm_on_lena",
        "prep_lena_switches",
        "prep_lena_switch_shortfall",
    ]
    rows = []
    for ref, intv in zip(ref_rows, intv_rows):
        row = {
            "scenario": reference.analysis_scenario,
            "switch_percent": _scenario_switch(reference.analysis_scenario),
            "step": ref["step"],
            "year": ref["year"],
        }
        for column in columns:
            row[f"delta_{column}"] = float(intv[column] - ref[column])
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-config",
        type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--hta-config",
        type=Path,
        default=ROOT / "configs/hta_runtime_config.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir()
    runner_output = raw_dir / "runner_outputs"
    log_path = raw_dir / "runner_stdout.log"
    model_config = args.model_config.resolve()
    hta_config = args.hta_config.resolve()
    costing_config = ROOT / "configs/hta_costing_config.json"
    code_inputs = (
        "run_scenarios.py",
        "hta_config_loader.py",
        "run_v3.py",
        "economics.py",
        "adverse_events.py",
        "scenario_comparator.py",
        "state_snapshot.py",
        "demography.py",
        "migration.py",
        "transmission_corrected.py",
        "stochastic_allocation.py",
        "tools/run_a7_decision_comparison_decomposition.py",
    )
    manifest = {
        "task": "A-7 deterministic decision-comparison QALY decomposition",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_status_short": _git(["status", "--short"]),
        "python": sys.version,
        "platform": platform.platform(),
        "pandas_version": pd.__version__,
        "model_config": str(model_config),
        "hta_config": str(hta_config),
        "deterministic": True,
        "seed_recorded": args.seed,
        "scenarios": [f"{SCENARIO_PREFIX}{switch}" for switch in EXPECTED_SWITCHES],
        "reference_arm": REFERENCE_ARM,
        "intervention_arm": INTERVENTION_ARM,
        "sha256": {
            str(model_config.relative_to(ROOT)): _sha256(model_config),
            str(hta_config.relative_to(ROOT)): _sha256(hta_config),
            str(costing_config.relative_to(ROOT)): _sha256(costing_config),
            **{path: _sha256(ROOT / path) for path in code_inputs},
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    runner = MultiScenarioRunner(str(model_config), str(runner_output), str(hta_config))
    runner.hta_config["analysis_scenarios"] = _select_scenarios(
        runner.hta_config["analysis_scenarios"]
    )
    susceptible_utilities = _verify_susceptible_utilities(runner.hta_config)
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = True
    runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {"value": True}
    runner.base_config["run_control"]["seed"] = {"value": args.seed}

    started = time.time()
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
            runner.run()
    manifest["elapsed_seconds"] = time.time() - started
    manifest["susceptible_utilities_verified"] = susceptible_utilities

    summary_rows: List[dict] = []
    component_rows: List[dict] = []
    flow_rows: List[dict] = []
    timestep_rows: List[dict] = []
    simulation_rows: List[dict] = []
    for scenario in _select_scenarios(runner.hta_config["analysis_scenarios"]):
        name = scenario["name"]
        results = runner.results[name]
        reference = results[REFERENCE_ARM]
        intervention = results[INTERVENTION_ARM]
        summary, components, flows = _decompose_pair(reference, intervention)
        summary_rows.append(summary)
        component_rows.extend(components)
        flow_rows.extend(flows)
        timestep_rows.extend(_timestep_rows(reference, intervention))
        simulation_rows.extend(_simulation_delta_rows(reference, intervention))

    pd.DataFrame(summary_rows).to_csv(output_dir / "scenario_decomposition.csv", index=False)
    pd.DataFrame(component_rows).to_csv(output_dir / "component_decomposition_long.csv", index=False)
    pd.DataFrame(flow_rows).to_csv(output_dir / "event_flow_totals_long.csv", index=False)
    timestep_frame = pd.DataFrame(timestep_rows)
    timestep_frame.to_csv(output_dir / "timestep_qaly_deltas.csv", index=False)
    pd.DataFrame(simulation_rows).to_csv(output_dir / "timestep_simulation_deltas.csv", index=False)
    annual_columns = [column for column in timestep_frame if column.startswith("delta_") or column == "infections_averted"]
    annual = (
        timestep_frame.assign(year_int=timestep_frame["year"].astype(int))
        .groupby(["scenario", "switch_percent", "year_int"], as_index=False)[annual_columns]
        .sum()
        .rename(columns={"year_int": "year"})
    )
    annual.to_csv(output_dir / "annual_qaly_deltas.csv", index=False)

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-7 outputs written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
