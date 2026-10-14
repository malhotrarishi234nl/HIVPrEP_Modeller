"""Run Phase-2 task A-10: lenacapavir adverse-event QALY sensitivity.

The runner executes the five deterministic substitution scenarios twice:
as configured and with both lenacapavir AE utility weights set to zero.  It
then uses the observed discounted active-lenacapavir person-time to evaluate
the configured one-way utility-weight distributions without re-running the
epidemiology, which is unaffected by prevalence-mode AE utility weights.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_distribution

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_utils import extract_value  # noqa: E402
from economics import QALY_COMPONENT_KEYS  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402
from scenario_comparator import ScenarioComparator, ScenarioResult  # noqa: E402


SCENARIO_PREFIX = "scenario_a_lena_substitution__switch_"
EXPECTED_SWITCHES = (10, 25, 50, 75, 100)
REFERENCE_ARM = "oral_prep"
INTERVENTION_ARM = "oral_prep_plus_lena"
TRANSIENT_ID = "lenacapavir_isr_ae"
PERSISTENT_ID = "lenacapavir_isr_persistent_nodule"
EXPECTED_AE_IDS = (TRANSIENT_ID, PERSISTENT_ID)
THESIS_SWITCH10_ICER = 346_408.0
THESIS_SWITCH10_THRESHOLD_PRICE = 9_987.0


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
        raise ValueError(f"not an A-10 substitution scenario: {name!r}")
    return int(name.removeprefix(SCENARIO_PREFIX))


def _select_scenarios(scenarios: Sequence[dict]) -> List[dict]:
    selected = [s for s in scenarios if s.get("name", "").startswith(SCENARIO_PREFIX)]
    selected.sort(key=lambda scenario: _scenario_switch(scenario["name"]))
    observed = tuple(_scenario_switch(scenario["name"]) for scenario in selected)
    if observed != EXPECTED_SWITCHES:
        raise ValueError(
            f"expected A-10 switch levels {EXPECTED_SWITCHES}, found {observed}"
        )
    for scenario in selected:
        if scenario.get("arm_order") != [REFERENCE_ARM, INTERVENTION_ARM]:
            raise ValueError(
                f"unexpected arm order for {scenario['name']}: "
                f"{scenario.get('arm_order')!r}"
            )
    return selected


def _ae_items_by_id(hta_config: dict) -> Dict[str, dict]:
    items = hta_config.get("adverse_events", {}).get("items", [])
    result = {}
    for item in items:
        item_id = item.get("id")
        if not item_id:
            raise ValueError("every adverse-event item must have an id")
        if item_id in result:
            raise ValueError(f"duplicate adverse-event id: {item_id}")
        result[item_id] = item
    return result


def audit_oral_prep_ae(hta_config: dict) -> dict:
    """Return and enforce the base-case oral-PrEP AE exclusion contract."""
    ae_config = hta_config.get("adverse_events", {})
    items = ae_config.get("items", [])
    active_oral_items = [item.get("id") for item in items if item.get("arm") == "oral_prep"]
    susceptible_utility = float(
        extract_value(hta_config["utilities"]["susceptible_oral_prep"])
    )
    exclusion_note = ae_config.get("_oral_prep_ae_exclusion_note", "")
    required_citation_tokens = (
        "10.1056/NEJMoa1011205",
        "10.1016/S0140-6736(15)00056-2",
        "10.1056/NEJMoa1506273",
    )
    missing_tokens = [token for token in required_citation_tokens if token not in exclusion_note]
    if active_oral_items:
        raise AssertionError(f"active oral-PrEP AE items found: {active_oral_items}")
    if susceptible_utility != 1.0:
        raise AssertionError(
            "oral-PrEP susceptible utility is not 1.0: "
            f"{susceptible_utility}"
        )
    if missing_tokens:
        raise AssertionError(
            "oral-PrEP AE exclusion note lacks complete trial citations: "
            f"{missing_tokens}"
        )
    return {
        "active_oral_ae_item_count": len(active_oral_items),
        "active_oral_ae_items": active_oral_items,
        "susceptible_oral_prep_utility": susceptible_utility,
        "exclusion_note": exclusion_note,
        "required_citation_tokens": list(required_citation_tokens),
        "missing_citation_tokens": missing_tokens,
        "evidence_status": "weakly_supported",
        "interpretation": (
            "The trials support a generally manageable oral TDF/FTC safety profile, "
            "but do not directly estimate zero incremental QALY or cost; zero is a "
            "base-case structural assumption and renal monitoring remains costed."
        ),
    }


def extract_lena_ae_inputs(hta_config: dict) -> Dict[str, dict]:
    ae_config = hta_config.get("adverse_events", {})
    if extract_value(ae_config.get("mode")) != "prevalence":
        raise AssertionError("A-10 analytical sensitivity requires prevalence AE mode")
    items = _ae_items_by_id(hta_config)
    if tuple(item_id for item_id in EXPECTED_AE_IDS if item_id in items) != EXPECTED_AE_IDS:
        raise AssertionError(
            f"expected lenacapavir AE items {EXPECTED_AE_IDS}, found {tuple(items)}"
        )

    result: Dict[str, dict] = {}
    for item_id in EXPECTED_AE_IDS:
        item = items[item_id]
        if item.get("arm") != "lenacapavir" or item.get("billing_moment") != "ongoing":
            raise AssertionError(f"unexpected routing for {item_id}")
        prevalence = float(extract_value(item["prevalence"]))
        signed_weight = float(extract_value(item["utility_decrement"]))
        magnitude = abs(signed_weight)
        distribution = item["utility_decrement"].get("distribution", {})
        if distribution.get("type") != "beta":
            raise AssertionError(f"{item_id} utility weight must use configured Beta uncertainty")
        alpha = float(distribution["alpha"])
        beta_value = float(distribution["beta"])
        quantiles = {
            "q025": float(beta_distribution.ppf(0.025, alpha, beta_value)),
            "q50": float(beta_distribution.ppf(0.5, alpha, beta_value)),
            "q975": float(beta_distribution.ppf(0.975, alpha, beta_value)),
        }
        result[item_id] = {
            "item_id": item_id,
            "prevalence": prevalence,
            "configured_signed_weight": signed_weight,
            "configured_weight_magnitude": magnitude,
            "distribution_type": "beta",
            "alpha": alpha,
            "beta": beta_value,
            "distribution_support_lower": 0.0,
            "distribution_support_upper": 1.0,
            "sweep_lower": 0.0,
            "sweep_upper": 1.0,
            "decision_focused_upper": quantiles["q975"],
            **quantiles,
            "distribution_status": item["utility_decrement"].get(
                "distribution_status", "unsupported"
            ),
            "source": item["utility_decrement"].get("source", ""),
            "range_status": (
                "full_configured_beta_support_stress_test_not_empirical_plausible_bounds"
            ),
        }
    return result


def set_zero_lena_ae_weights(hta_config: dict) -> None:
    items = _ae_items_by_id(hta_config)
    for item_id in EXPECTED_AE_IDS:
        items[item_id]["utility_decrement"]["value"] = 0.0


def delta_qaly_from_weights(
    no_ae_delta_qaly: float,
    discounted_active_lena_py: float,
    transient_prevalence: float,
    transient_weight: float,
    persistent_prevalence: float,
    persistent_weight: float,
) -> float:
    burden = (
        transient_prevalence * abs(transient_weight)
        + persistent_prevalence * abs(persistent_weight)
    )
    return no_ae_delta_qaly - discounted_active_lena_py * burden


def parameter_flip_weight(
    *,
    no_ae_delta_qaly: float,
    discounted_active_lena_py: float,
    varied_prevalence: float,
    fixed_prevalence: float,
    fixed_weight: float,
) -> float:
    if discounted_active_lena_py <= 0 or varied_prevalence <= 0:
        raise ValueError("positive exposure and varied prevalence are required")
    return (
        no_ae_delta_qaly / discounted_active_lena_py
        - fixed_prevalence * abs(fixed_weight)
    ) / varied_prevalence


def _component_total(result: ScenarioResult, component: str) -> float:
    column = f"step_qaly_{component}_disc"
    missing = [record for record in result.timestep_records if column not in record]
    if missing:
        raise KeyError(f"missing {column} in {len(missing)} timestep records")
    return math.fsum(float(record[column]) for record in result.timestep_records)


def _make_intervention_with_qaly(
    intervention: ScenarioResult, delta_qaly: float, reference: ScenarioResult
) -> ScenarioResult:
    return replace(
        intervention,
        total_qalys_disc=float(reference.total_qalys_disc + delta_qaly),
    )


def _comparator(hta_config: dict, model_config: dict) -> ScenarioComparator:
    return ScenarioComparator(
        wtp_threshold=float(extract_value(hta_config["wtp_threshold_eur_per_qaly"])),
        step_fraction=float(extract_value(model_config["run_control"]["step_size"])) / 12.0,
        perspective=hta_config["perspective"]["primary"],
    )


def _variant_row(
    *,
    variant: str,
    scenario: str,
    reference: ScenarioResult,
    intervention: ScenarioResult,
    comparator: ScenarioComparator,
    base_annual_price: float,
    notes: str,
) -> dict:
    pair = comparator.compare_pair(reference, intervention)
    threshold_price = comparator.find_threshold_price(
        {REFERENCE_ARM: reference, INTERVENTION_ARM: intervention},
        base_annual_price=base_annual_price,
        comparator_arm=REFERENCE_ARM,
        intervention_arm=INTERVENTION_ARM,
    )
    icer = pair["icer"]
    numeric_icer = float(icer) if not isinstance(icer, str) else math.nan
    return {
        "scenario": scenario,
        "switch_percent": _scenario_switch(scenario),
        "variant": variant,
        "delta_cost_eur": float(pair["delta_cost"]),
        "delta_qaly": float(pair["delta_qaly"]),
        "icer": icer,
        "icer_eur_per_qaly": numeric_icer,
        "icer_multiple_of_wtp": (
            numeric_icer / comparator.wtp if math.isfinite(numeric_icer) else math.nan
        ),
        "nmb_eur": float(pair["nmb"]),
        "dominance": pair["dominance"],
        "cost_effective_at_wtp": bool(pair["nmb"] > 0),
        "ce_threshold_annual_lena_price_eur": threshold_price,
        "base_annual_lena_price_eur": base_annual_price,
        "notes": notes,
    }


def _simulation_records_identical(left: ScenarioResult, right: ScenarioResult) -> bool:
    return left.simulation_records == right.simulation_records


def _run_model_pair(
    *,
    model_config_path: Path,
    hta_config_path: Path,
    output_dir: Path,
    seed: int,
    zero_lena_ae: bool,
    log_path: Path,
) -> MultiScenarioRunner:
    runner = MultiScenarioRunner(
        str(model_config_path), str(output_dir), str(hta_config_path)
    )
    runner.hta_config["analysis_scenarios"] = _select_scenarios(
        runner.hta_config["analysis_scenarios"]
    )
    audit_oral_prep_ae(runner.hta_config)
    extract_lena_ae_inputs(runner.hta_config)
    if zero_lena_ae:
        set_zero_lena_ae_weights(runner.hta_config)
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = True
    runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": True
    }
    runner.base_config["run_control"]["seed"] = {"value": seed}
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
            runner.run()
    return runner


def _sweep_points(ae_input: dict, flip: float) -> List[tuple[float, str]]:
    upper = ae_input["sweep_upper"]
    points: Dict[float, str] = {
        float(x): "full_support_grid" for x in np.linspace(0.0, upper, 1001)
    }
    labelled = {
        0.0: "zero",
        ae_input["q025"]: "configured_beta_q025",
        ae_input["q50"]: "configured_beta_median",
        ae_input["configured_weight_magnitude"]: "configured_base",
        ae_input["q975"]: "configured_beta_q975",
    }
    if 0.0 <= flip <= upper:
        labelled[flip] = "exact_dominance_flip"
    points.update({float(value): label for value, label in labelled.items()})
    return sorted(points.items())


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
    model_config_path = args.model_config.resolve()
    hta_config_path = args.hta_config.resolve()
    costing_config_path = ROOT / "configs/hta_costing_config.json"

    with model_config_path.open(encoding="utf-8") as handle:
        model_config = json.load(handle)

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
        "tools/run_a10_lena_ae_sensitivity.py",
    )
    manifest = {
        "task": "A-10 lenacapavir adverse-event utility sensitivity",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_status_short": _git(["status", "--short"]),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "model_config": str(model_config_path),
        "hta_config": str(hta_config_path),
        "deterministic": True,
        "seed_recorded": args.seed,
        "scenarios": [f"{SCENARIO_PREFIX}{switch}" for switch in EXPECTED_SWITCHES],
        "reference_arm": REFERENCE_ARM,
        "intervention_arm": INTERVENTION_ARM,
        "external_sources_verified_2026_09_10": {
            "iprex": "https://pubmed.ncbi.nlm.nih.gov/21091279/",
            "proud": "https://pubmed.ncbi.nlm.nih.gov/26364263/",
            "ipergay": "https://pubmed.ncbi.nlm.nih.gov/26624850/",
            "nice_ta1106": "https://www.nice.org.uk/guidance/ta1106/documents/committee-papers-2",
            "ema_yeytuo_epar": "https://www.ema.europa.eu/en/documents/assessment-report/yeytuo-epar-public-assessment-report_en.pdf",
        },
        "sha256": {
            str(model_config_path.relative_to(ROOT)): _sha256(model_config_path),
            str(hta_config_path.relative_to(ROOT)): _sha256(hta_config_path),
            str(costing_config_path.relative_to(ROOT)): _sha256(costing_config_path),
            **{path: _sha256(ROOT / path) for path in code_inputs},
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    started = time.time()
    as_configured = _run_model_pair(
        model_config_path=model_config_path,
        hta_config_path=hta_config_path,
        output_dir=raw_dir / "as_configured_runner",
        seed=args.seed,
        zero_lena_ae=False,
        log_path=raw_dir / "as_configured_stdout.log",
    )
    no_ae = _run_model_pair(
        model_config_path=model_config_path,
        hta_config_path=hta_config_path,
        output_dir=raw_dir / "zero_lena_ae_runner",
        seed=args.seed,
        zero_lena_ae=True,
        log_path=raw_dir / "zero_lena_ae_stdout.log",
    )

    oral_audit = audit_oral_prep_ae(as_configured.hta_config)
    ae_inputs = extract_lena_ae_inputs(as_configured.hta_config)
    transient = ae_inputs[TRANSIENT_ID]
    persistent = ae_inputs[PERSISTENT_ID]
    comparator = _comparator(as_configured.hta_config, model_config)
    base_annual_price = float(
        extract_value(
            as_configured.hta_config["prevention_costs"]["arm_specific"]
            ["lenacapavir"]["injection_subsequent"]
        )
    )

    variant_rows: List[dict] = []
    verification_rows: List[dict] = []
    arm_rows: List[dict] = []
    sweep_rows: List[dict] = []
    flip_rows: List[dict] = []
    thesis_rows: List[dict] = []

    for scenario_config in _select_scenarios(as_configured.hta_config["analysis_scenarios"]):
        scenario = scenario_config["name"]
        base_ref = as_configured.results[scenario][REFERENCE_ARM]
        base_intv = as_configured.results[scenario][INTERVENTION_ARM]
        zero_ref = no_ae.results[scenario][REFERENCE_ARM]
        zero_intv = no_ae.results[scenario][INTERVENTION_ARM]

        active_lena_py = _component_total(base_intv, "susceptible_lenacapavir")
        base_ae_component = _component_total(
            base_intv, "utility_decrement_adverse_events"
        )
        expected_base_ae_loss = active_lena_py * (
            transient["prevalence"] * transient["configured_weight_magnitude"]
            + persistent["prevalence"] * persistent["configured_weight_magnitude"]
        )
        direct_no_ae_delta = zero_intv.total_qalys_disc - zero_ref.total_qalys_disc
        analytical_no_ae_delta = (
            base_intv.total_qalys_disc - base_ref.total_qalys_disc - base_ae_component
        )
        if not math.isclose(
            -base_ae_component, expected_base_ae_loss, rel_tol=1e-12, abs_tol=1e-6
        ):
            raise AssertionError(f"configured AE formula mismatch for {scenario}")
        if not math.isclose(
            direct_no_ae_delta, analytical_no_ae_delta, rel_tol=1e-12, abs_tol=1e-6
        ):
            raise AssertionError(f"direct/analytical no-AE QALY mismatch for {scenario}")
        if not _simulation_records_identical(base_ref, zero_ref):
            raise AssertionError(f"reference epidemiology changed in no-AE run: {scenario}")
        if not _simulation_records_identical(base_intv, zero_intv):
            raise AssertionError(f"intervention epidemiology changed in no-AE run: {scenario}")
        for left, right, label in (
            (base_ref, zero_ref, "reference"),
            (base_intv, zero_intv, "intervention"),
        ):
            if left.total_costs_disc != right.total_costs_disc:
                raise AssertionError(f"payer cost changed for {scenario}/{label}")
            if left.total_costs_disc_societal != right.total_costs_disc_societal:
                raise AssertionError(f"societal cost changed for {scenario}/{label}")
            if left.cumul_new_infections != right.cumul_new_infections:
                raise AssertionError(f"infections changed for {scenario}/{label}")

        variant_rows.append(
            _variant_row(
                variant="as_configured",
                scenario=scenario,
                reference=base_ref,
                intervention=base_intv,
                comparator=comparator,
                base_annual_price=base_annual_price,
                notes="Configured lenacapavir ISR and persistent-nodule proxy decrements.",
            )
        )
        no_ae_row = _variant_row(
            variant="no_lenacapavir_ae",
            scenario=scenario,
            reference=zero_ref,
            intervention=zero_intv,
            comparator=comparator,
            base_annual_price=base_annual_price,
            notes="Both lenacapavir AE utility weights set to zero; AE costs already zero.",
        )
        variant_rows.append(no_ae_row)
        variant_rows.append(
            _variant_row(
                variant="symmetric_neither_arm_ae",
                scenario=scenario,
                reference=zero_ref,
                intervention=zero_intv,
                comparator=comparator,
                base_annual_price=base_annual_price,
                notes=(
                    "Symmetric structural sensitivity: neither arm carries AE disutility. "
                    "Numerically identical to no_lenacapavir_ae because oral PrEP has no "
                    "active AE disutility in the base case."
                ),
            )
        )

        verification_rows.append(
            {
                "scenario": scenario,
                "switch_percent": _scenario_switch(scenario),
                "discounted_active_lena_person_years": active_lena_py,
                "model_configured_ae_component_qaly": base_ae_component,
                "formula_configured_ae_component_qaly": -expected_base_ae_loss,
                "ae_formula_residual": base_ae_component + expected_base_ae_loss,
                "direct_no_ae_delta_qaly": direct_no_ae_delta,
                "analytical_no_ae_delta_qaly": analytical_no_ae_delta,
                "direct_analytical_residual": direct_no_ae_delta - analytical_no_ae_delta,
                "reference_simulation_records_identical": True,
                "intervention_simulation_records_identical": True,
                "payer_costs_identical": True,
                "societal_costs_identical": True,
                "infections_identical": True,
            }
        )

        for run_label, ref, intv in (
            ("as_configured", base_ref, base_intv),
            ("zero_lena_ae", zero_ref, zero_intv),
        ):
            for arm_label, result in ((REFERENCE_ARM, ref), (INTERVENTION_ARM, intv)):
                arm_rows.append(
                    {
                        "run": run_label,
                        "scenario": scenario,
                        "switch_percent": _scenario_switch(scenario),
                        "arm": arm_label,
                        "total_qalys_disc": result.total_qalys_disc,
                        "total_costs_disc_payer": result.total_costs_disc,
                        "total_costs_disc_societal": result.total_costs_disc_societal,
                        "prep_drug_lenacapavir_disc": result.cost_breakdown_disc.get(
                            "prep_drug_lenacapavir", 0.0
                        ),
                        "adverse_event_cost_disc": result.cost_breakdown_disc.get(
                            "adverse_events", 0.0
                        ),
                        "cumulative_new_infections": result.cumul_new_infections,
                    }
                )

        aggregate_flip = direct_no_ae_delta / active_lena_py
        configured_aggregate = (
            transient["prevalence"] * transient["configured_weight_magnitude"]
            + persistent["prevalence"] * persistent["configured_weight_magnitude"]
        )
        flip_rows.append(
            {
                "scenario": scenario,
                "switch_percent": _scenario_switch(scenario),
                "varied_parameter": "joint_proportional_multiplier",
                "fixed_parameter": "none",
                "flip_weight_or_multiplier": aggregate_flip / configured_aggregate,
                "aggregate_qaly_decrement_at_flip_per_active_lena_year": aggregate_flip,
                "within_sweep_range": True,
                "interpretation": (
                    "Below this multiplier delta QALY is positive; above it the "
                    "more-costly intervention is dominated."
                ),
            }
        )

        parameter_specs = (
            (TRANSIENT_ID, transient, PERSISTENT_ID, persistent),
            (PERSISTENT_ID, persistent, TRANSIENT_ID, transient),
        )
        for varied_id, varied, fixed_id, fixed in parameter_specs:
            flip = parameter_flip_weight(
                no_ae_delta_qaly=direct_no_ae_delta,
                discounted_active_lena_py=active_lena_py,
                varied_prevalence=varied["prevalence"],
                fixed_prevalence=fixed["prevalence"],
                fixed_weight=fixed["configured_weight_magnitude"],
            )
            in_range = 0.0 <= flip <= varied["sweep_upper"]
            flip_rows.append(
                {
                    "scenario": scenario,
                    "switch_percent": _scenario_switch(scenario),
                    "varied_parameter": varied_id,
                    "fixed_parameter": fixed_id,
                    "fixed_parameter_weight": fixed["configured_weight_magnitude"],
                    "flip_weight_or_multiplier": flip,
                    "aggregate_qaly_decrement_at_flip_per_active_lena_year": (
                        aggregate_flip
                    ),
                    "sweep_lower": varied["sweep_lower"],
                    "sweep_upper": varied["sweep_upper"],
                    "within_sweep_range": in_range,
                    "interpretation": (
                        "Exact one-way dominance flip with the other utility weight held "
                        "at its configured value. A negative value means the fixed item "
                        "alone already causes negative delta QALY, so no non-negative "
                        "one-way value can reverse dominance."
                    ),
                }
            )
            for weight, point_label in _sweep_points(varied, flip):
                weights = {
                    TRANSIENT_ID: transient["configured_weight_magnitude"],
                    PERSISTENT_ID: persistent["configured_weight_magnitude"],
                }
                weights[varied_id] = weight
                delta_qaly = delta_qaly_from_weights(
                    direct_no_ae_delta,
                    active_lena_py,
                    transient["prevalence"],
                    weights[TRANSIENT_ID],
                    persistent["prevalence"],
                    weights[PERSISTENT_ID],
                )
                synthetic_intv = _make_intervention_with_qaly(
                    zero_intv, delta_qaly, zero_ref
                )
                comparison = comparator.compare_pair(zero_ref, synthetic_intv)
                icer = comparison["icer"]
                sweep_rows.append(
                    {
                        "scenario": scenario,
                        "switch_percent": _scenario_switch(scenario),
                        "varied_parameter": varied_id,
                        "point_label": point_label,
                        "varied_weight_magnitude": weight,
                        "fixed_parameter": fixed_id,
                        "fixed_weight_magnitude": fixed["configured_weight_magnitude"],
                        "sweep_lower": varied["sweep_lower"],
                        "sweep_upper": varied["sweep_upper"],
                        "range_basis": varied["range_status"],
                        "delta_qaly": delta_qaly,
                        "delta_cost_eur": comparison["delta_cost"],
                        "icer": icer,
                        "icer_eur_per_qaly": (
                            float(icer) if not isinstance(icer, str) else math.nan
                        ),
                        "nmb_eur": comparison["nmb"],
                        "dominance": comparison["dominance"],
                        "cost_effective_at_wtp": comparison["nmb"] > 0,
                    }
                )

        if _scenario_switch(scenario) == 10:
            product_cost = zero_intv.cost_breakdown_disc["prep_drug_lenacapavir"]
            delta_cost = no_ae_row["delta_cost_eur"]
            non_product_delta_cost = delta_cost - product_cost
            required_qaly_for_thesis_threshold = (
                non_product_delta_cost
                + product_cost * (THESIS_SWITCH10_THRESHOLD_PRICE / base_annual_price)
            ) / comparator.wtp
            required_qaly_for_thesis_icer = delta_cost / THESIS_SWITCH10_ICER
            thesis_rows.extend(
                [
                    {
                        "metric": "switch10_icer_eur_per_qaly",
                        "earlier_thesis_value": THESIS_SWITCH10_ICER,
                        "current_no_ae_value": no_ae_row["icer_eur_per_qaly"],
                        "absolute_difference": (
                            no_ae_row["icer_eur_per_qaly"] - THESIS_SWITCH10_ICER
                        ),
                        "reproduced": False,
                        "status": "earlier_report_vs_current_derived_result",
                    },
                    {
                        "metric": "switch10_ce_threshold_annual_lena_price_eur",
                        "earlier_thesis_value": THESIS_SWITCH10_THRESHOLD_PRICE,
                        "current_no_ae_value": no_ae_row[
                            "ce_threshold_annual_lena_price_eur"
                        ],
                        "absolute_difference": (
                            no_ae_row["ce_threshold_annual_lena_price_eur"]
                            - THESIS_SWITCH10_THRESHOLD_PRICE
                        ),
                        "reproduced": False,
                        "status": "earlier_report_vs_current_derived_result",
                    },
                    {
                        "metric": "delta_qaly_required_at_current_cost_for_thesis_icer",
                        "earlier_thesis_value": math.nan,
                        "current_no_ae_value": required_qaly_for_thesis_icer,
                        "observed_current_no_ae_delta_qaly": direct_no_ae_delta,
                        "absolute_difference": (
                            required_qaly_for_thesis_icer - direct_no_ae_delta
                        ),
                        "reproduced": False,
                        "status": "derived_counterfactual_current_cost_structure",
                    },
                    {
                        "metric": "delta_qaly_required_for_thesis_threshold_price",
                        "earlier_thesis_value": math.nan,
                        "current_no_ae_value": required_qaly_for_thesis_threshold,
                        "observed_current_no_ae_delta_qaly": direct_no_ae_delta,
                        "absolute_difference": (
                            required_qaly_for_thesis_threshold - direct_no_ae_delta
                        ),
                        "reproduced": False,
                        "status": "derived_counterfactual_current_cost_structure",
                    },
                ]
            )

    ae_input_rows = list(ae_inputs.values())
    pd.DataFrame(ae_input_rows).to_csv(output_dir / "ae_input_audit.csv", index=False)
    variant_frame = pd.DataFrame(variant_rows)
    variant_frame.to_csv(output_dir / "variant_results.csv", index=False)
    pd.DataFrame(verification_rows).to_csv(
        output_dir / "direct_zero_ae_verification.csv", index=False
    )
    pd.DataFrame(arm_rows).to_csv(output_dir / "arm_results.csv", index=False)
    sweep_frame = pd.DataFrame(sweep_rows)
    sweep_frame.to_csv(output_dir / "one_way_sensitivity.csv", index=False)
    sweep_summary = (
        sweep_frame.groupby(["scenario", "switch_percent", "varied_parameter"], as_index=False)
        .agg(
            delta_qaly_min=("delta_qaly", "min"),
            delta_qaly_max=("delta_qaly", "max"),
            n_sweep_points=("delta_qaly", "size"),
            any_trade_off=("dominance", lambda s: bool((s == "trade-off").any())),
            any_dominated=("dominance", lambda s: bool((s == "dominated").any())),
            any_cost_effective=("cost_effective_at_wtp", "any"),
        )
    )
    sweep_summary.to_csv(output_dir / "one_way_sensitivity_summary.csv", index=False)
    flip_frame = pd.DataFrame(flip_rows)
    flip_frame.to_csv(output_dir / "dominance_flip_points.csv", index=False)
    four_variant_rows = []
    for scenario in sorted(
        variant_frame["scenario"].unique(), key=_scenario_switch
    ):
        variants = variant_frame[variant_frame["scenario"] == scenario].set_index("variant")
        sweep_group = sweep_frame[sweep_frame["scenario"] == scenario]
        sweep_numeric_icers = sweep_group["icer_eur_per_qaly"].dropna()
        aggregate_flip = flip_frame[
            (flip_frame["scenario"] == scenario)
            & (flip_frame["varied_parameter"] == "joint_proportional_multiplier")
        ].iloc[0]
        four_variant_rows.append(
            {
                "scenario": scenario,
                "switch_percent": _scenario_switch(scenario),
                "as_configured_delta_qaly": variants.loc["as_configured", "delta_qaly"],
                "as_configured_icer": variants.loc["as_configured", "icer"],
                "no_lena_ae_delta_qaly": variants.loc["no_lenacapavir_ae", "delta_qaly"],
                "no_lena_ae_icer_eur_per_qaly": variants.loc[
                    "no_lenacapavir_ae", "icer_eur_per_qaly"
                ],
                "no_lena_ae_ce_threshold_price_eur": variants.loc[
                    "no_lenacapavir_ae", "ce_threshold_annual_lena_price_eur"
                ],
                "symmetric_neither_delta_qaly": variants.loc[
                    "symmetric_neither_arm_ae", "delta_qaly"
                ],
                "symmetric_neither_icer_eur_per_qaly": variants.loc[
                    "symmetric_neither_arm_ae", "icer_eur_per_qaly"
                ],
                "one_way_full_support_delta_qaly_min": sweep_group["delta_qaly"].min(),
                "one_way_full_support_delta_qaly_max": sweep_group["delta_qaly"].max(),
                "one_way_numeric_icer_min": (
                    sweep_numeric_icers.min() if not sweep_numeric_icers.empty else math.nan
                ),
                "one_way_numeric_icer_max": (
                    sweep_numeric_icers.max() if not sweep_numeric_icers.empty else math.nan
                ),
                "one_way_any_dominated": bool(
                    (sweep_group["dominance"] == "dominated").any()
                ),
                "one_way_any_trade_off": bool(
                    (sweep_group["dominance"] == "trade-off").any()
                ),
                "joint_weight_multiplier_at_dominance_flip": aggregate_flip[
                    "flip_weight_or_multiplier"
                ],
                "aggregate_decrement_at_dominance_flip": aggregate_flip[
                    "aggregate_qaly_decrement_at_flip_per_active_lena_year"
                ],
                "range_warning": (
                    "One-way sweep spans full mathematical support [0,1] of the "
                    "configured Beta distributions. It is a stress test, not a "
                    "lenacapavir-specific empirically plausible interval."
                ),
            }
        )
    pd.DataFrame(four_variant_rows).to_csv(
        output_dir / "four_variant_summary.csv", index=False
    )
    pd.DataFrame(thesis_rows).to_csv(
        output_dir / "thesis_nonreproduction_check.csv", index=False
    )
    (output_dir / "oral_prep_ae_audit.json").write_text(
        json.dumps(oral_audit, indent=2) + "\n", encoding="utf-8"
    )

    manifest["elapsed_seconds"] = time.time() - started
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    output_files = sorted(
        path for path in output_dir.iterdir() if path.is_file() and path.name != "manifest.json"
    )
    manifest["output_sha256"] = {
        path.name: _sha256(path) for path in output_files
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-10 outputs written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
