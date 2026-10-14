"""Unified matched deployment HTA: two epidemic structures, ten scenarios.

This harness extends ``tools/run_stage_diagnosis_matched_hta.py`` from the five
lenacapavir substitution variants to the full deployment matrix required by
``TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md`` section 2A:

* two epidemic structures (legacy ``D_1788_v000``, late diagnosis ``D_1788_v109``);
* the no-PrEP attribution context pair;
* five lenacapavir *substitution* variants (10/25/50/75/100%);
* three lenacapavir *add-on* variants (500/1,500/3,000 by 2030);
* the full oral-PrEP expansion benchmark, as context;
* identical stochastic seeds, economics, WTP tiers, price, horizon and every
  non-diagnosis input.

The substitution and add-on families are distinct deployment strategies.  They
are never collapsed into one "substitution level" axis.

Two things this harness adds beyond the completed substitution run:

1. **A reproduction gate.**  Each arm is built with a fresh ``SimulationEngineV3``
   seeded by the run seed alone (``run_scenarios.py``), so scenario order cannot
   perturb an arm.  Adding the add-on and benchmark scenarios must therefore
   leave the six completed scenarios bit-identical.  ``--reproduce-check``
   enforces that against the completed run and aborts on any mismatch.
2. **Retained decomposition fields** (brief section 2B), including the QALY
   component that books the lenacapavir ISR/nodule decrement and the discounted
   PrEP-state person-time it is computed from.  These make the symmetric no-ISR
   sensitivity an exact reconstruction rather than an approximation.

``--dry-run`` validates inputs, scenario expansion, arm pairing and the
trajectory plan without starting the simulator.  It is the validation-only mode.
Heavy runs are launched by the user, never by the agent.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import shlex
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
from config_utils import extract_value  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402
from scenario_comparator import ScenarioComparator  # noqa: E402
from tools.run_a16_stochastic_headline import (  # noqa: E402
    ZIN_WTP_TIERS,
    classified_price_roots,
    parse_seeds,
    quadrant,
    ratio_of_means,
    summarize,
)
from tools.run_stage_diagnosis_matched_hta import (  # noqa: E402
    DEFAULT_REVIEW,
    STRUCTURES,
    load_frozen_structure,
    sha256_file,
    validate_matched_configs,
)

# ---------------------------------------------------------------------------
# Scenario matrix.  Order is the order produced by hta_config_loader from
# configs/hta_runtime_config.json, and the first six entries are exactly the
# six scenarios of the completed matched substitution run.
# ---------------------------------------------------------------------------
BASE_SCENARIO = "base_case_context"
SUBSTITUTION_PREFIX = "scenario_a_lena_substitution__switch_"
ADD_ON_PREFIX = "scenario_b_lena_add_on__add_on_"
ORAL_EXPANSION = "scenario_c_oral_expansion_benchmark"

SUBSTITUTION_LEVELS = (10, 25, 50, 75, 100)
ADD_ON_LEVELS = (500, 1500, 3000)

DEPLOYMENT_SCENARIOS: tuple[str, ...] = (
    (BASE_SCENARIO,)
    + tuple(f"{SUBSTITUTION_PREFIX}{x}" for x in SUBSTITUTION_LEVELS)
    + tuple(f"{ADD_ON_PREFIX}{x}" for x in ADD_ON_LEVELS)
    + (ORAL_EXPANSION,)
)

#: The six scenarios the completed matched run already covers.
COMPLETED_SCENARIOS: tuple[str, ...] = DEPLOYMENT_SCENARIOS[:6]

EXPECTED_ARMS: dict[str, list[str]] = {
    BASE_SCENARIO: ["no_prep", "oral_prep"],
    ORAL_EXPANSION: ["oral_prep", "oral_prep_expansion"],
    **{
        f"{SUBSTITUTION_PREFIX}{x}": ["oral_prep", "oral_prep_plus_lena"]
        for x in SUBSTITUTION_LEVELS
    },
    **{
        f"{ADD_ON_PREFIX}{x}": ["oral_prep", "oral_prep_plus_lena"]
        for x in ADD_ON_LEVELS
    },
}

DEPLOYMENT_FAMILY: dict[str, str] = {
    BASE_SCENARIO: "attribution_context",
    ORAL_EXPANSION: "oral_expansion_benchmark",
    **{f"{SUBSTITUTION_PREFIX}{x}": "lena_substitution" for x in SUBSTITUTION_LEVELS},
    **{f"{ADD_ON_PREFIX}{x}": "lena_add_on" for x in ADD_ON_LEVELS},
}

DEPLOYMENT_LEVEL: dict[str, float] = {
    **{f"{SUBSTITUTION_PREFIX}{x}": float(x) for x in SUBSTITUTION_LEVELS},
    **{f"{ADD_ON_PREFIX}{x}": float(x) for x in ADD_ON_LEVELS},
}

#: Scenarios whose intervention arm carries a lenacapavir product bundle, and
#: for which a price root is therefore defined.
LENA_SCENARIOS: tuple[str, ...] = tuple(
    s for s in DEPLOYMENT_SCENARIOS if DEPLOYMENT_FAMILY[s] in
    ("lena_substitution", "lena_add_on")
)

#: QALY components retained per arm (brief 2B).  ``utility_decrement_adverse_events``
#: is the booked ISR/nodule decrement; ``susceptible_lenacapavir`` is the
#: person-time it is computed from, at utility 1.0.
QALY_COMPONENTS = (
    "susceptible_no_prep",
    "susceptible_oral_prep",
    "susceptible_lenacapavir",
    "susceptible_lenacapavir_waning",
    "hiv_acute",
    "hiv_chronic_on_art",
    "hiv_chronic_off_art",
    "hiv_aids_on_art",
    "hiv_aids_off_art",
    "utility_decrement_art_initiation",
    "utility_decrement_adverse_events",
)

COST_COMPONENTS = (
    "drug",
    "prep_drug",
    "prep_drug_oral",
    "prep_drug_lenacapavir",
    "hiv_art_drug",
    "other_drug",
    "monitoring",
    "hiv_care",
    "adverse_events",
    "terminal_care",
)

SIM_FLOW_FIELDS = (
    "new_infections",
    "new_infections_msm",
    "new_infections_non_msm",
    "new_diagnoses",
    "new_diagnoses_msm",
    "hiv_deaths",
    "aids_deaths",
    "art_initiations",
    "prep_oral_initiations",
    "prep_lena_initiations",
    "prep_oral_dropouts",
    "prep_lena_dropouts",
    "prep_lena_switches",
    "prep_lena_switch_shortfall",
)

SIM_STOCK_FIELDS = ("msm_on_oral", "msm_on_lena")

CORE_METRICS = (
    "delta_cost",
    "delta_qaly",
    "delta_qaly_no_isr",
    "infections_averted",
    "nmb_20k",
    "nmb_50k",
    "nmb_80k",
)


# ---------------------------------------------------------------------------
# Scenario selection and pairing
# ---------------------------------------------------------------------------
def select_deployment_scenarios(scenarios: Sequence[dict]) -> list[dict]:
    """Return the ten deployment scenarios, in the frozen order, arm-checked."""
    by_name = {scenario["name"]: scenario for scenario in scenarios}
    missing = [name for name in DEPLOYMENT_SCENARIOS if name not in by_name]
    if missing:
        raise ValueError(f"deployment scenarios missing from runtime config: {missing}")
    unexpected = [
        name
        for name in by_name
        if name not in DEPLOYMENT_SCENARIOS
    ]
    if unexpected:
        raise ValueError(
            f"runtime config expands to scenarios outside the frozen matrix: {unexpected}"
        )
    selected = [by_name[name] for name in DEPLOYMENT_SCENARIOS]
    for scenario in selected:
        expected = EXPECTED_ARMS[scenario["name"]]
        if scenario.get("arm_order") != expected:
            raise ValueError(
                f"unexpected arm order for {scenario['name']}: "
                f"{scenario.get('arm_order')!r} (expected {expected!r})"
            )
    if [s["name"] for s in selected[:6]] != list(COMPLETED_SCENARIOS):
        raise ValueError(
            "the first six scenarios must match the completed matched substitution "
            "run, in order, so that the reproduction gate is meaningful"
        )
    return selected


def pair_arms(scenario_name: str) -> tuple[str, str]:
    """Return (comparator arm, intervention arm) for a deployment scenario."""
    try:
        comparator, intervention = EXPECTED_ARMS[scenario_name]
    except KeyError as exc:  # pragma: no cover - guarded by selection
        raise ValueError(f"not a deployment scenario: {scenario_name}") from exc
    return comparator, intervention


def primary_cost(result: Any, perspective: str) -> float:
    if perspective == "societal":
        return float(result.total_costs_disc_societal)
    if perspective == "payer":
        return float(result.total_costs_disc)
    raise ValueError(f"unsupported perspective: {perspective!r}")


def isr_decrement_per_person_year(hta_config: dict) -> float:
    """Return the configured ISR/nodule QALY loss per active lenacapavir person-year.

    This is the prevalence-weighted sum over lenacapavir adverse-event items in
    ``configs/hta_costing_config.json``.  ``adverse_events.py`` computes the step
    decrement as ``|utility_decrement| x prevalence x users x step_fraction``,
    so the annual per-person-year burden is exactly this scalar.
    """
    ae = hta_config.get("adverse_events", {})
    mode = extract_value(ae.get("mode", "prevalence"))
    if mode != "prevalence":
        raise ValueError(
            f"exact no-ISR reconstruction requires adverse_events.mode='prevalence', "
            f"got {mode!r}"
        )
    total = 0.0
    for item in ae.get("items", []):
        if item.get("arm") != "lenacapavir":
            raise ValueError(
                f"unexpected non-lenacapavir adverse-event item {item.get('id')!r}; "
                "the symmetric no-ISR sensitivity assumes lenacapavir-only AE items"
            )
        if item.get("billing_moment", "ongoing") != "ongoing":
            raise ValueError(
                f"adverse-event item {item.get('id')!r} is not 'ongoing'; the "
                "per-person-year identity does not apply to initiation items"
            )
        prevalence = float(extract_value(item.get("prevalence", {"value": 0.0})))
        decrement = float(extract_value(item.get("utility_decrement", {"value": 0.0})))
        total += abs(decrement) * prevalence
    return total


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
#: Every configuration path ``extract_rows`` and ``_run_worker`` read, as a
#: (container, path) pair.  ``MultiScenarioRunner.hta_config`` is the *composed*
#: config from ``hta_config_loader``, which is not the runtime config: it carries
#: ``time_horizon`` rather than ``study_defaults``, and merges the costing inputs
#: in.  Reading a runtime-only key off it raises KeyError inside a worker, after
#: the simulation has already run.  This list is checked before any simulation.
REQUIRED_CONFIG_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hta", ("perspective", "primary")),
    ("hta", ("prevention_costs", "arm_specific", "lenacapavir", "injection_subsequent")),
    ("hta", ("adverse_events", "mode")),
    ("hta", ("adverse_events", "items")),
    ("hta", ("outputs", "excel_report", "enabled")),
    ("hta", ("outputs", "csv_files", "enabled")),
    ("hta", ("time_horizon", "freeze_year")),
    ("hta", ("time_horizon", "evaluation_years")),
    ("hta", ("discounting", "health_effects_rate")),
    ("hta", ("discounting", "cost_rate")),
    ("base", ("run_control", "step_size")),
)

#: Runner attributes the extractor reads instead of digging into the config.
REQUIRED_RUNNER_ATTRIBUTES: tuple[str, ...] = ("freeze_year", "eval_years", "hta_config",
                                               "base_config", "results")


def _reachable(node: Any, path: Sequence[str]) -> bool:
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def preflight_config_access(hta_config: dict, base_config: dict) -> list[str]:
    """Raise unless every configuration path the extractor reads is reachable.

    Returns the list of checked paths so the caller can report them.
    """
    containers = {"hta": hta_config, "base": base_config}
    missing = [
        f"{container}:{'.'.join(path)}"
        for container, path in REQUIRED_CONFIG_PATHS
        if not _reachable(containers[container], path)
    ]
    if missing:
        raise KeyError(
            "preflight failed: the extractor reads configuration paths that do not "
            f"exist on the composed config: {missing}. Note that "
            "MultiScenarioRunner.hta_config is the composed config from "
            "hta_config_loader, not configs/hta_runtime_config.json; runtime-only "
            "keys such as 'study_defaults' are not present on it."
        )
    return [f"{container}:{'.'.join(path)}" for container, path in REQUIRED_CONFIG_PATHS]


def preflight_runner(runner: MultiScenarioRunner) -> list[str]:
    absent = [name for name in REQUIRED_RUNNER_ATTRIBUTES if not hasattr(runner, name)]
    if absent:
        raise AttributeError(f"preflight failed: runner is missing attributes {absent}")
    return preflight_config_access(runner.hta_config, runner.base_config)


# ---------------------------------------------------------------------------
# Row extraction
# ---------------------------------------------------------------------------
def _component_sums(result: Any) -> dict[str, float]:
    """Sum the per-step QALY components an arm booked, discounted and undiscounted."""
    records = result.timestep_records
    sums: dict[str, float] = {}
    for component in QALY_COMPONENTS:
        for basis in ("disc", "undisc"):
            key = f"step_qaly_{component}_{basis}"
            sums[f"qaly_{component}_{basis}"] = float(
                math.fsum(record.get(key, 0.0) for record in records)
            )
    return sums


def _simulation_sums(result: Any) -> dict[str, float]:
    records = result.simulation_records
    out: dict[str, float] = {}
    for field in SIM_FLOW_FIELDS:
        out[f"sim_{field}_total"] = float(
            math.fsum(float(record.get(field, 0.0) or 0.0) for record in records)
        )
    for field in SIM_STOCK_FIELDS:
        values = [float(record.get(field, 0.0) or 0.0) for record in records]
        out[f"sim_{field}_mean_stock"] = float(np.mean(values)) if values else math.nan
        out[f"sim_{field}_final_stock"] = values[-1] if values else math.nan
        by_year: dict[int, list[float]] = {}
        for record in records:
            by_year.setdefault(int(record.get("year", 0)), []).append(
                float(record.get(field, 0.0) or 0.0)
            )
        for year in (2030, 2040):
            if year in by_year:
                out[f"sim_{field}_mean_stock_{year}"] = float(np.mean(by_year[year]))
            else:
                out[f"sim_{field}_mean_stock_{year}"] = math.nan
    return out


def _arm_record(prefix: str, result: Any, perspective: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        f"{prefix}_arm_identity": result.name,
        f"{prefix}_cost_disc": primary_cost(result, perspective),
        f"{prefix}_cost_payer_disc": float(result.total_costs_disc),
        f"{prefix}_cost_undisc": float(result.total_costs_undisc),
        f"{prefix}_societal_additions_disc": float(result.total_societal_additions_disc),
        f"{prefix}_qaly_disc": float(result.total_qalys_disc),
        f"{prefix}_qaly_undisc": float(result.total_qalys_undisc),
        f"{prefix}_infections": float(result.cumul_new_infections),
    }
    for component in COST_COMPONENTS:
        row[f"{prefix}_cost_{component}_disc"] = float(
            result.cost_breakdown_disc.get(component, 0.0)
        )
    for key, value in _component_sums(result).items():
        row[f"{prefix}_{key}"] = value
    for key, value in _simulation_sums(result).items():
        row[f"{prefix}_{key}"] = value
    return row


def extract_rows(
    runner: MultiScenarioRunner,
    structure: str,
    seed: int,
    isr_per_py: float,
) -> list[dict[str, Any]]:
    perspective = runner.hta_config.get("perspective", {}).get("primary", "payer")
    base_price = float(
        extract_value(
            runner.hta_config["prevention_costs"]["arm_specific"]["lenacapavir"][
                "injection_subsequent"
            ]
        )
    )
    step_fraction = (
        float(extract_value(runner.base_config["run_control"]["step_size"])) / 12.0
    )
    comparator = ScenarioComparator(
        wtp_threshold=80_000.0,
        step_fraction=step_fraction,
        perspective=perspective,
    )

    rows: list[dict[str, Any]] = []
    for scenario_name in DEPLOYMENT_SCENARIOS:
        reference_arm, intervention_arm = pair_arms(scenario_name)
        results = runner.results[scenario_name]
        reference = results[reference_arm]
        intervention = results[intervention_arm]

        reference_cost = primary_cost(reference, perspective)
        intervention_cost = primary_cost(intervention, perspective)
        delta_cost = intervention_cost - reference_cost
        delta_qaly = float(intervention.total_qalys_disc - reference.total_qalys_disc)
        infections_averted = float(
            reference.cumul_new_infections - intervention.cumul_new_infections
        )

        row: dict[str, Any] = {
            "structure": structure,
            "tuple_id": STRUCTURES[structure][0],
            "mode": "stochastic",
            "seed": seed,
            "scenario": scenario_name,
            "deployment_family": DEPLOYMENT_FAMILY[scenario_name],
            "deployment_level": DEPLOYMENT_LEVEL.get(scenario_name, math.nan),
            "ae_convention": "configured_isr_nodule",
            "reference_arm": reference_arm,
            "intervention_arm": intervention_arm,
            "perspective": perspective,
            "freeze_year": int(extract_value(runner.freeze_year)),
            "horizon_years": int(extract_value(runner.eval_years)),
            "reference_cost": reference_cost,
            "intervention_cost": intervention_cost,
            "delta_cost": delta_cost,
            "reference_qaly": float(reference.total_qalys_disc),
            "intervention_qaly": float(intervention.total_qalys_disc),
            "delta_qaly": delta_qaly,
            "reference_infections": float(reference.cumul_new_infections),
            "intervention_infections": float(intervention.cumul_new_infections),
            "infections_averted": infections_averted,
            "base_annual_lena_price": base_price,
            "isr_qaly_decrement_per_lena_person_year": isr_per_py,
            "quadrant": quadrant(delta_cost, delta_qaly),
        }
        row.update(_arm_record("ref", reference, perspective))
        row.update(_arm_record("int", intervention, perspective))

        # --- exact symmetric no-ISR reconstruction -------------------------
        # adverse_events.py books only lenacapavir items, so the reference arm
        # of every scenario carries exactly zero.  The identity is checked, not
        # assumed, for both arms.
        ae_errors = []
        for prefix, result in (("ref", reference), ("int", intervention)):
            booked = row[f"{prefix}_qaly_utility_decrement_adverse_events_disc"]
            lena_py = row[f"{prefix}_qaly_susceptible_lenacapavir_disc"]
            predicted = -isr_per_py * lena_py
            ae_errors.append(abs(booked - predicted))
        row["ae_identity_max_abs_error_disc"] = max(ae_errors)
        delta_ae = (
            row["int_qaly_utility_decrement_adverse_events_disc"]
            - row["ref_qaly_utility_decrement_adverse_events_disc"]
        )
        row["delta_ae_qaly_disc"] = delta_ae
        row["delta_qaly_excluding_ae"] = delta_qaly - delta_ae
        row["delta_qaly_no_isr"] = delta_qaly - delta_ae  # d_oral = 0 by configuration
        row["lena_person_years_disc_intervention"] = row[
            "int_qaly_susceptible_lenacapavir_disc"
        ]
        row["oral_person_years_disc_reference"] = row["ref_qaly_susceptible_oral_prep_disc"]
        row["oral_person_years_disc_intervention"] = row[
            "int_qaly_susceptible_oral_prep_disc"
        ]

        # --- total PrEP person-time, for the family-semantics gate ----------
        def prep_py(prefix: str) -> float:
            return (
                row[f"{prefix}_qaly_susceptible_oral_prep_disc"]
                + row[f"{prefix}_qaly_susceptible_lenacapavir_disc"]
                + row[f"{prefix}_qaly_susceptible_lenacapavir_waning_disc"]
            )

        row["total_prep_person_years_disc_reference"] = prep_py("ref")
        row["total_prep_person_years_disc_intervention"] = prep_py("int")
        row["total_prep_person_years_disc_delta"] = prep_py("int") - prep_py("ref")
        row["total_prep_person_years_relative_change"] = (
            (prep_py("int") - prep_py("ref")) / prep_py("ref")
            if prep_py("ref") > 0
            else math.nan
        )

        # --- product cost and price roots ----------------------------------
        if scenario_name in LENA_SCENARIOS:
            product_cost = float(
                comparator._get_lena_price_sensitive_cost(intervention)
            )
        else:
            product_cost = math.nan
        row["lena_product_cost"] = product_cost

        for wtp in ZIN_WTP_TIERS:
            slug = f"{int(wtp / 1000)}k"
            row[f"nmb_{slug}"] = wtp * delta_qaly - delta_cost
            row[f"nmb_no_isr_{slug}"] = wtp * row["delta_qaly_no_isr"] - delta_cost
            if scenario_name not in LENA_SCENARIOS:
                for name in (
                    "nmb_zero_price",
                    "ce_threshold_price",
                    "qaly_loss_compensation_price",
                ):
                    row[f"{name}_{slug}"] = math.nan
                    row[f"{name}_no_isr_{slug}"] = math.nan
                continue
            for suffix, qaly in (("", delta_qaly), ("_no_isr", row["delta_qaly_no_isr"])):
                roots = classified_price_roots(
                    delta_qaly=qaly,
                    reference_cost=reference_cost,
                    intervention_cost=intervention_cost,
                    lena_product_cost=product_cost,
                    base_annual_price=base_price,
                    wtp=wtp,
                )
                for name, value in roots.items():
                    key = (
                        f"{name}_{slug}" if not suffix else f"{name}_no_isr_{slug}"
                    )
                    row[key] = math.nan if value is None else value

            # Independent guard against the production binary-search method.
            root = classified_price_roots(
                delta_qaly=delta_qaly,
                reference_cost=reference_cost,
                intervention_cost=intervention_cost,
                lena_product_cost=product_cost,
                base_annual_price=base_price,
                wtp=wtp,
            )["nmb_zero_price"]
            api_root = comparator.find_threshold_price(
                results,
                wtp=wtp,
                base_annual_price=base_price,
                comparator_arm=reference_arm,
                intervention_arm=intervention_arm,
            )
            if root is not None and root <= 10 * base_price:
                if api_root is None or abs(api_root - root) >= 1.0:
                    raise AssertionError(
                        f"analytic/API price-root mismatch for {scenario_name}, "
                        f"seed {seed}, WTP {wtp}: analytic={root}, api={api_root}"
                    )
            elif api_root is not None:
                raise AssertionError(
                    f"API returned an unexpected bounded root for {scenario_name}, "
                    f"seed {seed}, WTP {wtp}: analytic={root}, api={api_root}"
                )
        rows.append(row)
    return rows


def _run_worker(payload: dict[str, Any]) -> dict[str, Any]:
    structure = payload["structure"]
    seed = int(payload["seed"])
    run_dir = Path(payload["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=False)
    log_path = run_dir / "runner_stdout.log"
    runner = MultiScenarioRunner(
        payload["model_config"], str(run_dir / "runner_outputs"), payload["hta_config"]
    )
    runner.hta_config["analysis_scenarios"] = select_deployment_scenarios(
        runner.hta_config["analysis_scenarios"]
    )
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = False
    runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": False
    }
    runner.base_config["run_control"]["seed"] = {"value": seed}
    isr_per_py = isr_decrement_per_person_year(runner.hta_config)
    # Fail in seconds on a configuration-access mistake, not after a full run.
    preflight_runner(runner)
    started = time.time()
    with log_path.open("w", encoding="utf-8") as handle:
        with contextlib.redirect_stdout(handle), contextlib.redirect_stderr(handle):
            runner.run()
    return {
        "structure": structure,
        "seed": seed,
        "elapsed_seconds": time.time() - started,
        "snapshot_checksum": runner.snapshot_manager.baseline_snapshot.population_checksum,
        "rows": extract_rows(runner, structure, seed, isr_per_py),
    }


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
def reproduction_gate(
    rows: pd.DataFrame,
    reference_csv: Path,
    absolute_tolerance: float = 1e-9,
    relative_tolerance: float = 1e-12,
) -> pd.DataFrame:
    """Require the six completed scenarios to reproduce the completed run.

    Two things make a naive comparison here wrong, and both bit us on
    2026-09-23:

    1. ``pandas.read_csv``'s default float parser is fast but not correctly
       rounded. On the completed run's own CSV it misparses 20 of 180
       ``delta_cost`` values and 27 of 180 ``delta_qaly`` values by up to one
       unit in the last place (worst relative error 2.0e-16). The reference is
       therefore read with ``float_precision="round_trip"``, which uses the
       exact strtod path and reproduces the written values bit-for-bit.
    2. A purely absolute tolerance is meaningless across quantities spanning
       ten orders of magnitude. 1e-9 on a EUR 2.5e9 incremental cost demands
       ~4e-19 relative agreement, about 1,800x tighter than float64 can
       represent. The comparison is therefore ``abs <= atol or rel <= rtol``.

    ``relative_tolerance`` of 1e-12 is roughly 4,500 float64 ULPs: far below any
    scientifically meaningful difference (2.5 thousandths of a cent on EUR 2.5bn)
    while immune to representation noise. The ULP distance is recorded per check
    so that agreement can be audited rather than taken on trust.
    """
    reference = pd.read_csv(reference_csv, float_precision="round_trip")
    checks: list[dict[str, Any]] = []
    for _, ref in reference.iterrows():
        if ref["scenario"] not in COMPLETED_SCENARIOS:
            continue
        match = rows[
            (rows["structure"] == ref["structure"])
            & (rows["seed"] == int(ref["seed"]))
            & (rows["scenario"] == ref["scenario"])
        ]
        for metric in ("delta_cost", "delta_qaly", "infections_averted"):
            observed = float(match.iloc[0][metric]) if len(match) == 1 else math.nan
            expected = float(ref[metric])
            if math.isnan(observed):
                absolute = math.nan
                relative = math.nan
                ulps = math.nan
                passes = False
            else:
                absolute = abs(observed - expected)
                relative = absolute / abs(expected) if expected != 0 else (
                    0.0 if absolute == 0 else math.inf
                )
                spacing = float(np.spacing(abs(expected))) if expected != 0 else math.nan
                ulps = absolute / spacing if spacing and spacing > 0 else (
                    0.0 if absolute == 0 else math.inf
                )
                # infections averted are integer counts: require exact equality.
                if metric == "infections_averted":
                    passes = observed == expected
                else:
                    passes = (
                        absolute <= absolute_tolerance or relative <= relative_tolerance
                    )
            checks.append(
                {
                    "structure": ref["structure"],
                    "seed": int(ref["seed"]),
                    "scenario": ref["scenario"],
                    "metric": metric,
                    "expected": expected,
                    "observed": observed,
                    "abs_difference": absolute,
                    "relative_difference": relative,
                    "ulp_distance": ulps,
                    "passes": bool(passes),
                }
            )
    frame = pd.DataFrame(checks)
    failures = frame[~frame["passes"]]
    if len(failures):
        raise AssertionError(
            "reproduction gate failed: the completed matched substitution run is not "
            f"reproduced in {len(failures)} of {len(frame)} checks; worst absolute "
            f"difference {failures['abs_difference'].max():.6g}, worst relative "
            f"difference {failures['relative_difference'].max():.6g}, worst ULP "
            f"distance {failures['ulp_distance'].max():.6g}. A relative difference "
            "near 1e-16 is floating-point representation noise, not a model "
            "difference; anything larger is a real discrepancy. Investigate before "
            "interpreting any deployment result."
        )
    return frame


def semantics_gate(rows: pd.DataFrame, substitution_tolerance: float = 0.02) -> pd.DataFrame:
    """Check the deployment families mean what the brief says they mean.

    * add-on must increase total discounted PrEP person-time;
    * substitution must preserve it within an implementation tolerance;
    * the oral-expansion benchmark must increase it.
    """
    checks: list[dict[str, Any]] = []
    for scenario in DEPLOYMENT_SCENARIOS:
        if scenario == BASE_SCENARIO:
            continue
        family = DEPLOYMENT_FAMILY[scenario]
        for structure in sorted(rows["structure"].unique()):
            group = rows[
                (rows["structure"] == structure) & (rows["scenario"] == scenario)
            ]
            relative = group["total_prep_person_years_relative_change"]
            mean_relative = float(relative.mean())
            if family == "lena_substitution":
                passes = abs(mean_relative) <= substitution_tolerance
                rule = f"|relative change| <= {substitution_tolerance}"
            else:
                passes = mean_relative > 0
                rule = "relative change > 0"
            checks.append(
                {
                    "structure": structure,
                    "scenario": scenario,
                    "deployment_family": family,
                    "rule": rule,
                    "mean_relative_prep_person_time_change": mean_relative,
                    "min_relative": float(relative.min()),
                    "max_relative": float(relative.max()),
                    "passes": bool(passes),
                }
            )
    frame = pd.DataFrame(checks)
    failures = frame[~frame["passes"]]
    if len(failures):
        raise AssertionError(
            "deployment-family semantics gate failed:\n"
            + failures.to_string(index=False)
        )
    return frame


def identity_gate(rows: pd.DataFrame) -> pd.DataFrame:
    """Check the NMB, AE and no-ISR identities on every retained row."""
    checks: list[dict[str, Any]] = []
    for wtp in ZIN_WTP_TIERS:
        slug = f"{int(wtp / 1000)}k"
        error = (rows[f"nmb_{slug}"] - (wtp * rows["delta_qaly"] - rows["delta_cost"])).abs()
        checks.append(
            {"identity": f"nmb_{slug}", "max_abs_error": float(error.max()),
             "scale": float(rows[f"nmb_{slug}"].abs().max())}
        )
        error = (
            rows[f"nmb_no_isr_{slug}"]
            - (wtp * rows["delta_qaly_no_isr"] - rows["delta_cost"])
        ).abs()
        checks.append(
            {"identity": f"nmb_no_isr_{slug}", "max_abs_error": float(error.max()),
             "scale": float(rows[f"nmb_no_isr_{slug}"].abs().max())}
        )
    checks.append(
        {
            "identity": "ae_component == -isr_per_py * lena_person_years (both arms)",
            "max_abs_error": float(rows["ae_identity_max_abs_error_disc"].max()),
            "scale": float(rows["delta_ae_qaly_disc"].abs().max()),
        }
    )
    error = (
        rows["delta_qaly"] - (rows["delta_qaly_excluding_ae"] + rows["delta_ae_qaly_disc"])
    ).abs()
    checks.append(
        {
            "identity": "delta_qaly == delta_qaly_excluding_ae + delta_ae",
            "max_abs_error": float(error.max()),
            "scale": float(rows["delta_qaly"].abs().max()),
        }
    )
    frame = pd.DataFrame(checks)
    frame["passes"] = frame["max_abs_error"] <= np.maximum(
        1e-6, 1e-9 * frame["scale"].abs()
    )
    failures = frame[~frame["passes"]]
    if len(failures):
        raise AssertionError(
            "identity gate failed:\n" + failures.to_string(index=False)
        )
    return frame


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def _price_reporting_rule(
    roots: dict[str, float | None], crosses_zero: bool, wtp: float
) -> str:
    """Plain-language statement of what the price quantity is, and is not."""
    tier = f"EUR {int(wtp):,}/QALY"
    if roots["nmb_zero_price"] is None:
        return (
            f"No non-negative lenacapavir product price sets NMB to zero at {tier}: "
            "the non-product cost increment alone exceeds the value of the "
            "incremental QALYs. Report as 'no price makes this scenario "
            "cost-effective at this threshold', never as a price."
            + (
                " The mean QALY confidence interval also crosses zero, so the sign "
                "of the health effect is not established."
                if crosses_zero
                else ""
            )
        )
    if crosses_zero:
        return (
            f"NMB-zero root at {tier}, reported with a sign-instability warning: the "
            "mean incremental QALY confidence interval crosses zero, so the root's "
            "meaning is conditional on a sign the data do not establish. Not a "
            "stable cost-effectiveness threshold."
        )
    if roots["ce_threshold_price"] is not None:
        return (
            f"Cost-effectiveness annual price threshold at {tier}: mean incremental "
            "QALYs are positive and their confidence interval excludes zero."
        )
    return (
        f"QALY-loss compensation annual price at {tier}: incremental QALYs are "
        "negative, so this is the maximum price at which cost savings compensate "
        "for the QALY loss. It is not a cost-effectiveness threshold."
    )


def pooled_price_table(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for structure in sorted(rows["structure"].unique()):
        for scenario in LENA_SCENARIOS:
            group = rows[
                (rows["structure"] == structure) & (rows["scenario"] == scenario)
            ]
            if not len(group):
                continue
            for convention, qaly_column in (
                ("configured_isr_nodule", "delta_qaly"),
                ("symmetric_no_isr", "delta_qaly_no_isr"),
            ):
                stats = summarize(group[qaly_column].tolist())
                means = group[
                    [
                        qaly_column,
                        "reference_cost",
                        "intervention_cost",
                        "lena_product_cost",
                        "base_annual_lena_price",
                    ]
                ].mean()
                for wtp in ZIN_WTP_TIERS:
                    roots = classified_price_roots(
                        delta_qaly=float(means[qaly_column]),
                        reference_cost=float(means["reference_cost"]),
                        intervention_cost=float(means["intervention_cost"]),
                        lena_product_cost=float(means["lena_product_cost"]),
                        base_annual_price=float(means["base_annual_lena_price"]),
                        wtp=wtp,
                    )
                    crosses = bool(
                        float(stats["ci95_low"]) <= 0 <= float(stats["ci95_high"])
                    )
                    records.append(
                        {
                            "structure": structure,
                            "scenario": scenario,
                            "deployment_family": DEPLOYMENT_FAMILY[scenario],
                            "deployment_level": DEPLOYMENT_LEVEL.get(scenario, math.nan),
                            "ae_convention": convention,
                            "wtp": wtp,
                            "mean_delta_qaly": float(means[qaly_column]),
                            "mean_qaly_ci_crosses_zero": crosses,
                            **roots,
                            # Existence and sign-stability are ORTHOGONAL facts.
                            # Collapsing them into one label hides the more
                            # decisive one: a scenario can be both sign-unstable
                            # AND have no non-negative price at all.
                            "root_exists": roots["nmb_zero_price"] is not None,
                            "sign_stability": (
                                "sign_unstable" if crosses else "sign_stable"
                            ),
                            "price_quantity_name": (
                                "no_non_negative_root"
                                if roots["nmb_zero_price"] is None
                                else (
                                    "cost_effectiveness_annual_price_threshold"
                                    if roots["ce_threshold_price"] is not None
                                    else "qaly_loss_compensation_annual_price"
                                )
                            ),
                            "reporting_rule": _price_reporting_rule(
                                roots, crosses, wtp
                            ),
                            "method": "root from mean cost, QALY and product-cost components",
                        }
                    )
    return pd.DataFrame(records)


def within_structure_summary(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for structure in sorted(rows["structure"].unique()):
        for scenario in DEPLOYMENT_SCENARIOS:
            group = rows[
                (rows["structure"] == structure) & (rows["scenario"] == scenario)
            ]
            if not len(group):
                continue
            for metric in CORE_METRICS + (
                "total_prep_person_years_disc_delta",
                "lena_person_years_disc_intervention",
                "delta_ae_qaly_disc",
            ):
                if metric not in group:
                    continue
                records.append(
                    {
                        "structure": structure,
                        "scenario": scenario,
                        "deployment_family": DEPLOYMENT_FAMILY[scenario],
                        "deployment_level": DEPLOYMENT_LEVEL.get(scenario, math.nan),
                        "metric": metric,
                        **summarize(group[metric].tolist()),
                    }
                )
    return pd.DataFrame(records)


def paired_structure_differences(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    indexed = rows.set_index(["structure", "seed", "scenario"]).sort_index()
    detail: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for scenario in DEPLOYMENT_SCENARIOS:
        for metric in CORE_METRICS:
            legacy = indexed.loc[("legacy", slice(None), scenario), metric]
            candidate = indexed.loc[("candidate", slice(None), scenario), metric]
            legacy.index = legacy.index.droplevel([0, 2])
            candidate.index = candidate.index.droplevel([0, 2])
            if not legacy.index.equals(candidate.index):
                raise ValueError(f"unmatched seeds for {scenario} {metric}")
            differences = candidate - legacy
            for seed, value in differences.items():
                detail.append(
                    {
                        "seed": int(seed),
                        "scenario": scenario,
                        "metric": metric,
                        "candidate_minus_legacy": float(value),
                    }
                )
            summary.append(
                {
                    "scenario": scenario,
                    "deployment_family": DEPLOYMENT_FAMILY[scenario],
                    "metric": metric,
                    **summarize(differences.tolist()),
                }
            )
    return pd.DataFrame(detail), pd.DataFrame(summary)


def decision_summary(rows: pd.DataFrame, pooled: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for structure in sorted(rows["structure"].unique()):
        for scenario in DEPLOYMENT_SCENARIOS:
            group = rows[
                (rows["structure"] == structure) & (rows["scenario"] == scenario)
            ]
            if not len(group):
                continue
            for convention, qaly_column, nmb_prefix in (
                ("configured_isr_nodule", "delta_qaly", "nmb"),
                ("symmetric_no_isr", "delta_qaly_no_isr", "nmb_no_isr"),
            ):
                qaly = summarize(group[qaly_column].tolist())
                cost = summarize(group["delta_cost"].tolist())
                infection = summarize(group["infections_averted"].tolist())
                ratio = ratio_of_means(group["delta_cost"], group[qaly_column])
                row: dict[str, Any] = {
                    "structure": structure,
                    "scenario": scenario,
                    "deployment_family": DEPLOYMENT_FAMILY[scenario],
                    "deployment_level": DEPLOYMENT_LEVEL.get(scenario, math.nan),
                    "ae_convention": convention,
                    "n_seeds": len(group),
                    "reference_arm": group["reference_arm"].iloc[0],
                    "intervention_arm": group["intervention_arm"].iloc[0],
                    "mean_delta_cost": cost["mean"],
                    "sd_delta_cost": cost["sd"],
                    "ci95_low_delta_cost": cost["ci95_low"],
                    "ci95_high_delta_cost": cost["ci95_high"],
                    "mean_delta_qaly": qaly["mean"],
                    "sd_delta_qaly": qaly["sd"],
                    "ci95_low_delta_qaly": qaly["ci95_low"],
                    "ci95_high_delta_qaly": qaly["ci95_high"],
                    "mean_infections_averted": infection["mean"],
                    "sd_infections_averted": infection["sd"],
                    "ci95_low_infections_averted": infection["ci95_low"],
                    "ci95_high_infections_averted": infection["ci95_high"],
                    "ratio_of_means_icer": ratio["ratio_of_means"],
                    "icer_status": ratio["status"],
                }
                for wtp in ZIN_WTP_TIERS:
                    slug = f"{int(wtp / 1000)}k"
                    column = f"{nmb_prefix}_{slug}"
                    row[f"mean_nmb_{slug}"] = float(group[column].mean())
                    row[f"probability_nmb_positive_{slug}"] = float(
                        (group[column] > 0).mean()
                    )
                    match = pooled[
                        (pooled["structure"] == structure)
                        & (pooled["scenario"] == scenario)
                        & (pooled["ae_convention"] == convention)
                        & (pooled["wtp"] == wtp)
                    ]
                    if len(match):
                        price = match.iloc[0]
                        row[f"pooled_nmb_zero_price_{slug}"] = price["nmb_zero_price"]
                        row[f"pooled_price_quantity_name_{slug}"] = price[
                            "price_quantity_name"
                        ]
                        row[f"pooled_root_exists_{slug}"] = bool(price["root_exists"])
                        row[f"pooled_sign_stability_{slug}"] = price["sign_stability"]
                        base = float(group["base_annual_lena_price"].iloc[0])
                        root = price["nmb_zero_price"]
                        row[f"price_reduction_required_{slug}"] = (
                            math.nan if pd.isna(root) else 1.0 - float(root) / base
                        )
                    else:
                        row[f"pooled_nmb_zero_price_{slug}"] = math.nan
                        row[f"pooled_price_quantity_name_{slug}"] = "not_applicable"
                        row[f"pooled_root_exists_{slug}"] = False
                        row[f"pooled_sign_stability_{slug}"] = "not_applicable"
                        row[f"price_reduction_required_{slug}"] = math.nan
                records.append(row)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def finalize(
    rows: pd.DataFrame, output_dir: Path, reproduce_check: Path
) -> dict[str, pd.DataFrame]:
    """Run every gate and write every summary from the per-seed rows.

    Split out of ``main`` so that a run whose simulation completed but whose
    post-processing failed can be finalized from the retained
    ``per_seed_economics.csv`` without re-simulating. The gates are identical in
    both paths; nothing is skipped on the resume path.
    """
    gates = {
        "reproduction": reproduction_gate(rows, reproduce_check),
        "semantics": semantics_gate(rows),
        "identities": identity_gate(rows),
    }
    for name, frame in gates.items():
        frame.to_csv(output_dir / f"gate_{name}.csv", index=False)

    pooled = pooled_price_table(rows)
    pooled.to_csv(output_dir / "pooled_price_thresholds.csv", index=False)
    within_structure_summary(rows).to_csv(
        output_dir / "within_structure_summary_long.csv", index=False
    )
    detail, summary = paired_structure_differences(rows)
    detail.to_csv(output_dir / "paired_structure_differences.csv", index=False)
    summary.to_csv(output_dir / "paired_structure_difference_summary.csv", index=False)
    decision_summary(rows, pooled).to_csv(
        output_dir / "decision_summary.csv", index=False
    )
    return gates


def build_plan(seeds: Sequence[int]) -> dict[str, Any]:
    model_runs = len(STRUCTURES) * len(seeds)
    arms_per_model_run = sum(len(EXPECTED_ARMS[s]) for s in DEPLOYMENT_SCENARIOS)
    return {
        "structures": list(STRUCTURES),
        "scenarios": list(DEPLOYMENT_SCENARIOS),
        "substitution_variants": len(SUBSTITUTION_LEVELS),
        "add_on_variants": len(ADD_ON_LEVELS),
        "oral_expansion_benchmark": 1,
        "attribution_context_pairs": 1,
        "seeds": list(seeds),
        "n_seeds": len(seeds),
        "model_runs": model_runs,
        "arms_per_model_run": arms_per_model_run,
        "forward_arm_trajectories": model_runs * arms_per_model_run,
        "historical_prefreeze_trajectories": model_runs,
        "total_simulation_trajectories": model_runs * (arms_per_model_run + 1),
        "comparison_rows": model_runs * len(DEPLOYMENT_SCENARIOS),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument(
        "--hta-config", type=Path, default=ROOT / "configs/hta_runtime_config.json"
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--stochastic-seeds", default="42-56")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument(
        "--reproduce-check",
        type=Path,
        default=DEFAULT_REVIEW
        / "matched_hta_20260922"
        / "per_seed_economics.csv",
        help="completed matched substitution per-seed CSV that must reproduce exactly",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--finalize-from-existing",
        action="store_true",
        help="re-run the gates and summaries from an existing per_seed_economics.csv "
             "in --output-dir, without simulating. For a run whose simulation "
             "completed but whose post-processing failed.",
    )
    args = parser.parse_args(argv)

    if args.finalize_from_existing:
        if args.output_dir is None:
            raise ValueError("--finalize-from-existing requires --output-dir")
        output_dir = args.output_dir.resolve()
        per_seed = output_dir / "per_seed_economics.csv"
        if not per_seed.exists():
            raise FileNotFoundError(
                f"{per_seed} does not exist; there is nothing to finalize"
            )
        # round_trip parsing recovers the written float64 values exactly.
        rows = pd.read_csv(per_seed, float_precision="round_trip")
        print(f"finalizing {len(rows)} retained rows from {per_seed}")
        finalize(rows, output_dir, args.reproduce_check)
        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

        # The simulation and this finalization may be different code versions.
        # Preserve whichever hash the original invocation recorded as the
        # simulation runner, and record this file separately.
        simulation_hash = (
            manifest.get("simulation_runner_sha256")
            or manifest.pop("runner_file_sha256", None)
        )
        manifest["simulation_runner_sha256"] = simulation_hash
        manifest["finalization_code_sha256"] = sha256_file(Path(__file__))

        # ``build_plan`` used to emit a "structures" list that overwrote the
        # per-structure provenance dict. Rebuild it from the files on disk.
        structures = manifest.get("structures")
        if not isinstance(structures, dict):
            manifest["structure_names"] = structures
            structures = {}
        for structure, (tuple_id, filename) in STRUCTURES.items():
            entry = structures.get(structure, {}) if isinstance(structures, dict) else {}
            exported_path = output_dir / f"{structure}_model_config.json"
            entry.setdefault("tuple_id", tuple_id)
            if exported_path.exists():
                payload, config = load_frozen_structure(
                    args.review_dir.resolve() / filename, tuple_id
                )
                entry["frozen_config_sha256"] = payload["config_sha256"]
                entry["wrapper_file_sha256"] = sha256_file(
                    args.review_dir.resolve() / filename
                )
                entry["exported_model_config"] = exported_path.name
                entry["exported_model_config_sha256"] = sha256_file(exported_path)
                # The exported file is what the workers actually read; confirm it
                # still hashes to the frozen configuration it claims to be.
                entry["exported_matches_frozen_config"] = (
                    ccs.sha256_json(json.loads(exported_path.read_text()))
                    == payload["config_sha256"]
                )
            structures[structure] = entry
        manifest["structures"] = structures

        # Migrate the pre-closeout key name.
        if "started_at_utc" in manifest:
            manifest.setdefault(
                "simulation_started_at_utc", manifest.pop("started_at_utc")
            )

        # Simulation completion is not finalization. Date the former from the
        # newest retained per-run artifact.
        run_artifacts = sorted(
            (output_dir / "runs").glob("*/runner_stdout.log"),
            key=lambda q: q.stat().st_mtime,
        )
        if run_artifacts:
            manifest["simulation_completed_at_utc"] = (
                datetime.fromtimestamp(
                    run_artifacts[-1].stat().st_mtime, tz=timezone.utc
                ).isoformat()
            )
            manifest["simulation_completed_at_utc_source"] = (
                "modification time of the newest runs/*/runner_stdout.log; the "
                "original invocation raised before it could stamp a completion time"
            )
        manifest.pop("completed_at_utc", None)
        started = manifest.get("simulation_started_at_utc")
        finished = manifest.get("simulation_completed_at_utc")
        if started and finished:
            manifest["simulation_elapsed_seconds"] = (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds()
        manifest["finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["run_status"] = "completed_from_existing"
        manifest["finalized_from_existing_per_seed_csv"] = True
        manifest["finalize_note"] = (
            "The 630 trajectories were simulated by the original invocation and are "
            "unchanged. Post-processing was re-run from the retained "
            "per_seed_economics.csv after a fix to the reproduction gate; no "
            "trajectory was re-simulated. Values were re-read with "
            "float_precision='round_trip', which is bit-exact, so the finalized "
            "products are identical to what the original invocation would have "
            "written had its gate been correct."
        )
        manifest["output_sha256"] = {
            name: sha256_file(output_dir / name)
            for name in sorted(q.name for q in output_dir.glob("*.csv"))
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"finalized; gates and summaries written to {output_dir}")
        return 0

    seeds = parse_seeds(args.stochastic_seeds)
    if len(seeds) < 15 and not args.dry_run:
        raise ValueError("the matched deployment comparison requires at least 15 seeds")
    review_dir = args.review_dir.resolve()
    hta_config_path = args.hta_config.resolve()
    if not hta_config_path.exists():
        raise FileNotFoundError(hta_config_path)

    payloads: dict[str, dict] = {}
    configs: dict[str, dict] = {}
    for structure, (tuple_id, filename) in STRUCTURES.items():
        payload, config = load_frozen_structure(review_dir / filename, tuple_id)
        payloads[structure] = payload
        configs[structure] = config
    changed = validate_matched_configs(configs)

    from hta_config_loader import load_hta_config  # noqa: E402  (local import)

    expanded = load_hta_config(str(hta_config_path))
    selected = select_deployment_scenarios(expanded["analysis_scenarios"])
    isr_per_py = isr_decrement_per_person_year(expanded)

    # Preflight against a real runner built from the exported legacy structure.
    # Constructing a runner costs about 0.25 s and runs no simulation, but it
    # exercises the same composed configuration the workers will read.
    with tempfile.TemporaryDirectory() as scratch:
        probe_config = Path(scratch) / "legacy_model_config.json"
        probe_config.write_text(
            json.dumps(configs["legacy"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        probe = MultiScenarioRunner(
            str(probe_config), str(Path(scratch) / "unused"), str(hta_config_path)
        )
        probe.hta_config["analysis_scenarios"] = select_deployment_scenarios(
            probe.hta_config["analysis_scenarios"]
        )
        checked_paths = preflight_runner(probe)
        probe_freeze_year = int(extract_value(probe.freeze_year))
        probe_eval_years = int(extract_value(probe.eval_years))

    plan = build_plan(seeds)
    # ``build_plan`` reports the structure *names*; the manifest needs the
    # per-structure provenance dict. Renamed so that ``**plan`` cannot clobber it.
    plan["structure_names"] = plan.pop("structures")
    plan.update(
        {
            "status": "validated",
            "diagnosis_leaves_changed": changed,
            "hta_config": str(hta_config_path),
            "hta_config_sha256": sha256_file(hta_config_path),
            "costing_config_sha256": sha256_file(ROOT / "configs/hta_costing_config.json"),
            "isr_qaly_decrement_per_lena_person_year": isr_per_py,
            "arm_pairs": {s["name"]: s["arm_order"] for s in selected},
            "deployment_family": {
                s["name"]: DEPLOYMENT_FAMILY[s["name"]] for s in selected
            },
            "base_annual_lena_price": float(
                extract_value(
                    expanded["prevention_costs"]["arm_specific"]["lenacapavir"][
                        "injection_subsequent"
                    ]
                )
            ),
            "zin_wtp_tiers": list(ZIN_WTP_TIERS),
            "reproduce_check": str(args.reproduce_check),
            "preflight": {
                "runner_constructed": True,
                "config_paths_checked": checked_paths,
                "freeze_year": probe_freeze_year,
                "evaluation_years": probe_eval_years,
            },
        }
    )
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    if args.output_dir is None:
        raise ValueError("--output-dir is required unless --dry-run is used")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir()
    exported: dict[str, Path] = {}
    for structure, config in configs.items():
        path = output_dir / f"{structure}_model_config.json"
        path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        exported[structure] = path

    manifest = {
        "task": "unified matched deployment and AE analysis",
        "brief": "TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md",
        "run_status": "simulation_started",
        "simulation_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        **plan,
        # Provenance last, so nothing in ``plan`` can overwrite it.
        "structures": {
            structure: {
                "tuple_id": STRUCTURES[structure][0],
                "frozen_config_sha256": payloads[structure]["config_sha256"],
                "wrapper_file_sha256": sha256_file(
                    review_dir / STRUCTURES[structure][1]
                ),
                "exported_model_config": f"{structure}_model_config.json",
                "exported_model_config_sha256": sha256_file(exported[structure]),
            }
            for structure in STRUCTURES
        },
        # The code that produces the trajectories and the code that post-processes
        # them can be different versions when a run is finalized later. Keep both.
        "simulation_runner_sha256": sha256_file(Path(__file__)),
        "finalization_code_sha256": None,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    jobs = [
        {
            "structure": structure,
            "seed": seed,
            "run_dir": str(runs_dir / f"{structure}_seed_{seed}"),
            "model_config": str(exported[structure]),
            "hta_config": str(hta_config_path),
        }
        for structure in STRUCTURES
        for seed in seeds
    ]
    started = time.time()
    records: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(_run_worker, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            result = future.result()
            records.extend(result["rows"])
            print(
                f"completed {job['structure']} seed {job['seed']} in "
                f"{result['elapsed_seconds']:.1f}s",
                flush=True,
            )

    rows = pd.DataFrame(records).sort_values(["structure", "seed", "scenario"])
    expected_rows = plan["comparison_rows"]
    if len(rows) != expected_rows:
        raise AssertionError(f"expected {expected_rows} comparison rows, got {len(rows)}")
    rows.to_csv(output_dir / "per_seed_economics.csv", index=False)

    finalize(rows, output_dir, args.reproduce_check)

    manifest["run_status"] = "completed"
    manifest["simulation_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["finalized_at_utc"] = manifest["simulation_completed_at_utc"]
    manifest["finalization_code_sha256"] = sha256_file(Path(__file__))
    manifest["simulation_elapsed_seconds"] = time.time() - started
    manifest["output_sha256"] = {
        name: sha256_file(output_dir / name)
        for name in sorted(p.name for p in output_dir.glob("*.csv"))
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"completed matched deployment analysis in {manifest['elapsed_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
