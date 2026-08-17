#!/usr/bin/env python3
"""
Multi-Scenario Runner (V5 + HTA)

Orchestrates baseline freezing and scenario forking for HTA comparability.

Two modes:
  1. Legacy V5 mode (--output_dir): Independent scenario runs from base config
  2. HTA mode (--hta): Baseline freeze → fork three arms → economics → compare

HTA Workflow:
  1. Load base config and HTA config
  2. Run baseline simulation to freeze_year (2025)
  3. Freeze state snapshot at freeze_year
  4. For each analysis scenario (A, B, D):
     For each arm (no_prep, oral_prep, lenacapavir):
       - Restore frozen baseline
       - Apply arm-specific overrides (PrEP enable/disable)
       - Apply scenario-specific overrides (eligible_activity_classes)
       - Attach EconomicsAccumulator + AdverseEventModule
       - Continue simulation from freeze year for evaluation_years
       - Collect ScenarioResult
  5. Run ScenarioComparator (sequential ZIN analysis)
  6. Write output CSVs

Usage:
    # HTA mode
    python run_scenarios.py 1980_test.json \\
        --hta configs/hta_config.json \\
        --output_dir outputs/hta_results/

    # Legacy mode (no HTA)
    python run_scenarios.py config_v5.json --output_dir outputs/v5_run
"""

import json
import sys
import os
import copy
import tempfile
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from run_v3 import SimulationEngineV3
from state_snapshot import StateSnapshotManager
from economics import EconomicsAccumulator
from adverse_events import AdverseEventModule
from scenario_comparator import ScenarioComparator, ScenarioResult
from config_utils import extract_value
from hta_report import HTAReportWriter
from hta_config_loader import load_hta_config
from hta_reliability import format_calibration_handoff_for_console

# Legacy V5 imports — only needed for non-HTA mode
try:
    from scenario_executor import (
        load_scenarios,
        get_policy_config,
        merge_config_override,
        apply_prep_force_disable,
        validate_scenario_config
    )
    from validation import validate_v5_config, validate_prep_disabled
    _LEGACY_AVAILABLE = True
except ImportError:
    _LEGACY_AVAILABLE = False


def _validate_hta_config(config: dict) -> None:
    """
    Validate required HTA config keys before pipeline construction.

    Raises ValueError with a specific missing-key message so the error
    points at the config file, not deep inside economics.py or comparator code.
    All keys are required — no silent fallbacks exist in V11.7.2+.

    V11.7.2: added as part of HTA hardening (item 9 of audit plan).
    """
    required_paths = [
        ("wtp_threshold_eur_per_qaly",),
        ("discounting", "health_effects_rate"),
        ("discounting", "cost_rate"),
        ("discounting", "base_year"),
        ("time_horizon", "freeze_year"),
        ("time_horizon", "evaluation_years"),
    ]
    for path in required_paths:
        node = config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ValueError(
                    f"hta_config missing required key: {'.'.join(path)}. "
                    f"Check your hta_config.json."
                )
            node = node[key]


def _force_deterministic_hta_base_case(config: dict) -> None:
    """
    Force deterministic dynamics for the main HTA base-case workbook.

    PSA varies uncertainty separately. The deterministic base case keeps arm
    differences attributable to intervention mechanics rather than random draw
    divergence after the frozen baseline fork.
    """
    run_control = config.setdefault("run_control", {})
    deterministic = run_control.setdefault("deterministic_mode", {})
    if isinstance(deterministic, dict):
        deterministic["value"] = True
    else:
        run_control["deterministic_mode"] = {"value": True}

    # Keep the legacy V6-compatible key in sync for modules/reports that read
    # the already-translated section directly. ConfigReader derives this section
    # from run_control, so run_control is the authoritative HTA runtime flag.
    sim_controls = config.setdefault("simulation_controls", {})
    sim_controls["deterministic_mode"] = {"value": True}


def _payer_breakdown_components(result: ScenarioResult) -> Dict[str, float]:
    bd = result.cost_breakdown_disc
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


def _annual_arm_budget_frame(result: ScenarioResult) -> pd.DataFrame:
    """
    Annual payer-cost and epidemiology summary for budget-impact output.

    Uses economics timestep records as the source of payer costs/QALYs and
    simulation records for PrEP stock/flow fields when available.
    """
    if not result.timestep_records:
        return pd.DataFrame()

    econ = pd.DataFrame(result.timestep_records).copy()
    econ["year_int"] = np.floor(econ["year"]).astype(int)

    annual = (
        econ.groupby("year_int", as_index=False)
        .agg(
            annual_cost_disc=("step_costs_disc", "sum"),
            annual_cost_undisc=("step_costs_undisc", "sum"),
            annual_qalys_disc=("step_qalys_disc", "sum"),
            annual_qalys_undisc=("step_qalys_undisc", "sum"),
            annual_new_infections=("step_new_infections", "sum"),
            annual_new_diagnoses=("step_new_diagnoses", "sum"),
            total_population_hcc=("total_population", "mean"),
        )
        .rename(columns={"year_int": "year"})
    )

    annual["cumul_cost_disc"] = annual["annual_cost_disc"].cumsum()
    annual["cumul_cost_undisc"] = annual["annual_cost_undisc"].cumsum()
    annual["cumul_qalys_disc"] = annual["annual_qalys_disc"].cumsum()
    annual["cumul_qalys_undisc"] = annual["annual_qalys_undisc"].cumsum()
    annual["cumul_new_infections"] = annual["annual_new_infections"].cumsum()

    if result.simulation_records:
        sim = pd.DataFrame(result.simulation_records).copy()
        if "year" in sim.columns:
            sim["year"] = np.floor(sim["year"]).astype(int)
            stock_cols = [
                "msm_on_oral",
                "msm_on_lena",
                "msm_high_risk_on_prep",
                "msm_eligible_prep",
            ]
            flow_cols = [
                "prep_oral_initiations",
                "prep_lena_initiations",
                "prep_oral_dropouts",
                "prep_lena_dropouts",
            ]
            agg_spec = {}
            for col in stock_cols:
                if col in sim.columns:
                    agg_spec[f"{col}_hcc"] = (col, "mean")
                    agg_spec[f"{col}_end"] = (col, "last")
            for col in flow_cols:
                if col in sim.columns:
                    agg_spec[col] = (col, "sum")
            if agg_spec:
                sim_annual = (
                    sim.sort_values("year")
                    .groupby("year", as_index=False)
                    .agg(**agg_spec)
                )
                annual = annual.merge(sim_annual, on="year", how="left")

    return annual


def _deep_set(config: dict, dot_path: str, value) -> None:
    """
    Set a value at a dot-separated key path in a nested dict.

    The terminal segment is set to ``value`` directly.  The caller is
    responsible for including a ".value" suffix when targeting a
    ``{"value": ...}`` wrapped parameter entry.

    Examples:
        # Scalar PSA draw injection:
        _deep_set(cfg, "transmission_model.disease.transmission.beta_chronic.value", 0.003)

        # Schedule dict injection (BACKLOG-16 use case):
        _deep_set(cfg, "prep_schedules.Lenacapavir.initiation.schedule", {"2025": 0.04})

    Raises:
        KeyError: if any intermediate key does not exist in the config.

    V11.9.6: BLOCKER-B fix — enables param_overrides in _build_arm_config.
    """
    keys = dot_path.split(".")
    node = config
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


def _intervention_key_for_prep_product(prep_product: str) -> str:
    return {
        "Oral_PrEP": "oral_prep",
        "Lenacapavir": "lenacapavir",
    }.get(prep_product, prep_product)


def _runtime_interpolation_to_engine(interpolation: str) -> str:
    return {
        "linear_annual": "linear",
        "step_hold": "stepwise",
    }.get(interpolation, interpolation)


def _apply_prep_pathway_to_arm_config(
    config: dict,
    pathway: dict,
    runtime_config: Optional[dict] = None,
) -> None:
    """Inject a split-runtime PrEP pathway into the model config."""
    prep_product = pathway.get("product")
    if not prep_product:
        return

    intervention_key = _intervention_key_for_prep_product(prep_product)
    if intervention_key in config.get("interventions", {}):
        eligible = pathway.get("eligible_population", {})
        if "activity_classes" in eligible:
            config["interventions"][intervention_key]["eligible_activity_classes"] = {
                "value": eligible["activity_classes"]
            }
        if "risk_groups" in eligible:
            config["interventions"][intervention_key]["eligible_orientations"] = {
                "value": eligible["risk_groups"]
            }

    prep_schedule = config.setdefault("prep_schedules", {}).setdefault(prep_product, {})
    advanced = prep_schedule.setdefault("advanced", {})
    mode = pathway.get("mode")
    interpolation = (
        (runtime_config or {})
        .get("interpolation", {})
        .get("target_stock", {})
        .get("mode", "linear_annual")
    )

    if mode == "target_stock":
        target = pathway.get("target_stock", {}).get("value", {})
        advanced["forward_target_stock"] = {
            "enabled": {"value": True},
            "target_stock_schedule": {"value": target},
            "interpolation": {"value": _runtime_interpolation_to_engine(interpolation)},
            "above_target_action": {"value": "allow_attrition_only"},
        }
    elif mode == "additional_target_stock":
        target = pathway.get("additional_target_stock", {}).get("value", {})
        advanced["forward_target_stock"] = {
            "enabled": {"value": True},
            "target_stock_schedule": {"value": target},
            "interpolation": {"value": _runtime_interpolation_to_engine(interpolation)},
            "above_target_action": {"value": "allow_attrition_only"},
        }
    elif mode == "switch_share":
        share = pathway.get("switch_share_of_eligible_oral_users", {}).get("value", 0.0)
        start_year = (
            (runtime_config or {})
            .get("study_defaults", {})
            .get("start_year", 2025)
        )
        advanced["switch_share_from_prep"] = {
            "enabled": {"value": True},
            "from_prep_state": {"value": "Oral_PrEP"},
            "share_schedule": {"value": {str(start_year): float(share)}},
            "interpolation": {"value": "stepwise"},
            "preserve_total_prep_stock": {"value": True},
            "shortfall_action": {"value": "cap_at_eligible_oral_stock_and_report"},
        }


def _deep_get(config: dict, dot_path: str, default=None):
    node = config
    for key in dot_path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _schedule_has_positive_rate(
    schedule: dict,
    start_year: float,
    end_year: float,
) -> bool:
    """
    Return True when a stepwise schedule has a positive rate active at any
    point in [start_year, end_year].

    A positive anchor before start_year counts if no later non-positive anchor
    switches it off before the evaluation horizon.
    """
    anchors = []
    for year, rate in (schedule or {}).items():
        try:
            anchors.append((float(year), float(rate)))
        except (TypeError, ValueError):
            continue
    anchors.sort(key=lambda item: item[0])

    for idx, (year, rate) in enumerate(anchors):
        next_year = anchors[idx + 1][0] if idx + 1 < len(anchors) else float("inf")
        if rate <= 0:
            continue
        if year <= end_year and next_year >= start_year:
            return True
    return False


def _allows_zero_lenacapavir_uptake(analysis_scenario: dict) -> bool:
    """Read explicit opt-out flags for no-op Lenacapavir scenarios."""
    if extract_value(analysis_scenario.get("allow_zero_lenacapavir_uptake", False)):
        return True
    overrides = analysis_scenario.get("lenacapavir_overrides", {})
    return bool(extract_value(overrides.get("allow_zero_lenacapavir_uptake", False)))


def _validate_lenacapavir_schedule_active(
    config: dict,
    analysis_scenario: dict,
    freeze_year: float,
    eval_years: float,
) -> None:
    """
    Fail fast when a Lenacapavir arm is enabled but has no active initiation
    schedule during the HTA evaluation horizon.
    """
    lena_cfg = config.get("interventions", {}).get("lenacapavir", {})
    if not extract_value(lena_cfg.get("enabled", {"value": False})):
        return
    if _allows_zero_lenacapavir_uptake(analysis_scenario):
        return

    schedule = (
        config.get("prep_schedules", {})
              .get("Lenacapavir", {})
              .get("initiation", {})
              .get("schedule", {})
    )
    end_year = float(freeze_year) + float(eval_years)
    if _schedule_has_positive_rate(schedule, float(freeze_year), end_year):
        return

    scenario_name = analysis_scenario.get("name", "<unknown>")
    raise ValueError(
        "Lenacapavir arm is enabled but "
        "prep_schedules.Lenacapavir.initiation.schedule has no positive "
        "rate active during the HTA evaluation horizon "
        f"({float(freeze_year):g}-{end_year:g}) for scenario "
        f"'{scenario_name}'. Use the production model-run config "
        "1980_test.json or explicitly set "
        "allow_zero_lenacapavir_uptake=true for this scenario."
    )


def _collapse_prep_to_none(snapshot) -> None:
    """
    V11.10.7: Collapse all PrEP-state slots in a snapshot's population tensor
    into the None (index 0) slot, then zero the remaining slots.

    Called at the HTA fork point — immediately after each arm's snapshot is
    restored and before the arm engine is constructed — when the
    ``time_horizon.prep_reset_at_fork`` flag is true.

    Purpose: ensures all three arms start from an equal PrEP-naive stock at
    the HTA decision year (2025), regardless of how many oral PrEP users the
    baseline may have accumulated.  Historical PrEP effects on HIV prevalence
    are fully preserved in the snapshot's population distribution; only the
    PrEP headcount is cleared.

    No-op when n_prep == 1 (baseline ran without any PrEP, current default).

    Modifies the snapshot in-place and updates its population checksum.
    """
    pop = snapshot.population
    if pop.shape[-1] <= 1:
        return  # No PrEP dimension to collapse — safe no-op

    # Sum every PrEP slot (axis=-1) into slot 0 (None), zero the rest
    pop[..., 0] = pop.sum(axis=-1)
    pop[..., 1:] = 0

    # Recompute checksum so integrity checks pass on the modified array
    snapshot.population_checksum = snapshot._compute_checksum(pop)


class MultiScenarioRunner:
    """
    Manages multi-scenario simulation runs with baseline freezing.

    Supports two modes:
    - HTA mode (hta_config_path provided): Full health technology assessment pipeline
    - Legacy V5 mode (no hta_config_path): Independent or baseline-frozen scenario runs
    """

    def __init__(self, config_path: str, output_dir: str = "outputs",
                 hta_config_path: str = None):
        """
        Initialize multi-scenario runner.

        Args:
            config_path: Path to base model configuration file
            output_dir: Directory for output files
            hta_config_path: Path to HTA config (enables HTA mode)
        """
        self.config_path = os.path.abspath(config_path)
        self.output_dir = output_dir
        self.hta_config = None
        self.hta_mode = hta_config_path is not None

        # Load base config
        with open(config_path, 'r') as f:
            self.base_config = json.load(f)

        if self.hta_mode:
            # ── HTA Mode ──────────────────────────────────────────────────
            self.hta_config = load_hta_config(hta_config_path)

            _force_deterministic_hta_base_case(self.base_config)
            self.calibration_handoff_lines = (
                format_calibration_handoff_for_console(self.base_config)
            )

            # V11.7.2: validate required keys before any pipeline construction
            _validate_hta_config(self.hta_config)

            freeze_year = self.hta_config["time_horizon"]["freeze_year"]
            # V11.7.2: freeze_year must equal discounting.base_year — both anchor discounting
            freeze_year_val = extract_value(freeze_year)
            base_year_val   = extract_value(self.hta_config["discounting"]["base_year"])
            if int(freeze_year_val) != int(base_year_val):
                raise ValueError(
                    f"Configuration inconsistency: time_horizon.freeze_year "
                    f"({int(freeze_year_val)}) must equal discounting.base_year "
                    f"({int(base_year_val)}). Discount factors are anchored at "
                    f"freeze_year; both must refer to the same base year to apply "
                    f"ZIN cost and health discount rates correctly."
                )
            self.freeze_year = freeze_year
            self.eval_years = self.hta_config["time_horizon"]["evaluation_years"]

            self.snapshot_manager = StateSnapshotManager()
            self.snapshot_manager.configure(
                freeze_enabled=True,
                freeze_year=freeze_year
            )
        else:
            # ── Legacy V5 Mode ────────────────────────────────────────────
            if not _LEGACY_AVAILABLE:
                raise ImportError(
                    "Legacy scenario modules (scenario_executor, validation) "
                    "not found. Use --hta for HTA mode."
                )

            self.scenarios = load_scenarios(self.base_config)
            self.policy = get_policy_config(self.base_config)

            validation_suite = validate_v5_config(self.base_config, self.scenarios)
            if validation_suite.has_errors():
                print("\n❌ Configuration validation failed!")
                validation_suite.print_summary()
                raise ValueError("Invalid V5 configuration")
            elif validation_suite.has_warnings():
                print("\n⚠️  Configuration has warnings (continuing anyway)")

            validate_scenario_config(self.scenarios, self.policy)

            self.snapshot_manager = StateSnapshotManager()
            self.snapshot_manager.configure(
                freeze_enabled=self.policy["enabled"],
                freeze_year=self.policy["freeze_year"]
            )

        # Results storage
        self.results = {}

    # ──────────────────────────────────────────────────────────────────────
    # Dispatch
    # ──────────────────────────────────────────────────────────────────────

    def run(self):
        """
        Execute all scenarios.

        Returns:
            Dictionary mapping scenario names to output dictionaries
        """
        if self.hta_mode:
            self._run_hta_scenarios()
        elif self.policy["enabled"] and self.policy["reuse_baseline"]:
            self._run_with_reused_baseline()
        else:
            self._run_independent_scenarios()

        return self.results

    # ──────────────────────────────────────────────────────────────────────
    # HTA Pipeline (new)
    # ──────────────────────────────────────────────────────────────────────

    def _run_hta_scenarios(self):
        """
        Full HTA pipeline: baseline → freeze → fork arms → economics → compare.

        Implements the three-phase workflow specified in the HTA implementation
        plan (Section 4, Phase 5).
        """
        print("\n" + "=" * 70)
        print("HTA COST-EFFECTIVENESS ANALYSIS")
        print("=" * 70)
        print(f"\nBase config: {self.config_path}")
        print(f"Freeze year: {self.freeze_year}")
        print(f"Evaluation horizon: {self.eval_years} years")
        for line in getattr(self, "calibration_handoff_lines", []):
            print(line)
        print(f"Analysis scenarios: {len(self.hta_config['analysis_scenarios'])}")
        for s in self.hta_config["analysis_scenarios"]:
            print(f"  • {s['name']}: {s['label']}")

        # Extract seed from base config
        seed = self._get_seed()

        # ── PHASE 1: Baseline Run ────────────────────────────────────────
        print("\n" + "-" * 70)
        print("PHASE 1: Baseline Run (→ freeze year {})".format(self.freeze_year))
        print("-" * 70)

        baseline_engine = SimulationEngineV3(self.base_config, seed=seed)
        baseline_engine._snapshot_manager = self.snapshot_manager

        # Guard: when end_year == freeze_year, the last executed step lands at
        # (end_year - years_per_step), one quarter short of the freeze point.
        # Extend n_steps by 1 so the freeze fires at exactly freeze_year.
        freeze_step_target = round(
            (self.freeze_year - baseline_engine.time.start_year)
            / baseline_engine.time.years_per_step
        )
        if freeze_step_target >= baseline_engine.time.n_steps:
            baseline_engine.time.n_steps = freeze_step_target + 1

        baseline_engine.run()

        # Save baseline time-series immediately before arm engines can overwrite
        # the output_writer's CSV (arms share the same run_tag and run_number=1).
        _baseline_df = pd.DataFrame(baseline_engine.outputs)
        _parent_dir = os.path.dirname(os.path.normpath(self.output_dir))
        os.makedirs(_parent_dir, exist_ok=True)
        self._hta_baseline_csv = os.path.join(
            _parent_dir, "hta_baseline_prefreeze.csv"
        )
        _baseline_df.to_csv(self._hta_baseline_csv, index=False)
        print(f"  ✓ Baseline time-series saved: {self._hta_baseline_csv}")

        # Verify snapshot was captured
        snapshot = self.snapshot_manager.baseline_snapshot
        if snapshot is None:
            raise RuntimeError(
                f"Baseline freeze failed — no snapshot captured at year "
                f"{self.freeze_year}. Check that the simulation reaches "
                f"this year (config end_year must be >= freeze_year)."
            )

        # Calculate step range for scenario evaluation
        freeze_step = snapshot.step
        years_per_step = baseline_engine.time.years_per_step
        steps_per_year = round(1.0 / years_per_step)
        eval_steps = self.eval_years * steps_per_year
        end_step = freeze_step + eval_steps

        print(f"\n  ✓ Baseline snapshot captured")
        print(f"    Freeze step: {freeze_step} (year {self.freeze_year})")
        print(f"    Evaluation: {self.eval_years} years = {eval_steps} steps")
        print(f"    Scenario step range: {freeze_step} → {end_step}")
        print(f"    Total pop at freeze: {snapshot.total_population:,}")

        # ── PHASE 2: Scenario Forking & Economics ────────────────────────
        print("\n" + "-" * 70)
        print("PHASE 2: Scenario Forking & Economics Accumulation")
        print("-" * 70)

        # V11.10.7: Read prep_reset_at_fork flag once before the arm loop.
        # When true, all existing PrEP users in the baseline snapshot are
        # collapsed to None before each arm engine is initialised — ensuring
        # equal PrEP-naive starting stock across arms at the decision year.
        _prep_reset = extract_value(
            self.hta_config.get("time_horizon", {})
                           .get("prep_reset_at_fork", {"value": True})
        )
        print(f"  prep_reset_at_fork: {_prep_reset}")

        all_results = {}
        arm_structure = "head_to_head"  # default; may be overridden per scenario below

        arm_order = ["no_prep", "oral_prep", "lenacapavir"]  # default; overridden per scenario

        for scenario in self.hta_config["analysis_scenarios"]:
            scenario_name = scenario["name"]
            print(f"\n{'=' * 60}")
            print(f"  Analysis Scenario: {scenario['label']}")
            print(f"{'=' * 60}")

            scenario_results = {}

            # Scenario-driven arm_order. Split runtime configs provide an
            # explicit order; legacy configs fall back to arm_structure.
            if "arm_order" in scenario:
                arm_order = list(scenario["arm_order"])
            else:
                arm_structure = scenario.get("arm_structure", "head_to_head")
                if arm_structure == "expansion":
                    arm_order = ["no_prep", "oral_prep", "oral_prep_plus_lena"]
                else:
                    arm_order = ["no_prep", "oral_prep", "lenacapavir"]

            for arm_name in arm_order:
                print(f"\n  ┌─ Arm: {arm_name}")

                # Build arm-specific config dict (V11.9.6: returns dict, no temp file)
                arm_config_dict = self._build_arm_config(
                    arm_name, scenario
                )
                if arm_name in ("lenacapavir", "oral_prep_plus_lena"):
                    _validate_lenacapavir_schedule_active(
                        arm_config_dict,
                        scenario,
                        extract_value(self.freeze_year),
                        extract_value(self.eval_years),
                    )

                # Restore frozen baseline (each arm gets independent copy)
                restored_snapshot = self.snapshot_manager.restore(arm_name)

                # Per-arm prep_reset control (Change A — V12.9)
                # Per-arm override in scenario takes precedence over global flag.
                arm_prep_reset_map = scenario.get("arm_prep_reset", {})
                should_reset = arm_prep_reset_map.get(arm_name, _prep_reset)
                if should_reset:
                    _prep_users_before = int(
                        restored_snapshot.population[..., 1:].sum()
                        if restored_snapshot.population.shape[-1] > 1 else 0
                    )
                    _collapse_prep_to_none(restored_snapshot)
                    if _prep_users_before > 0:
                        print(f"  │  prep_reset: moved {_prep_users_before:,} "
                              f"PrEP users → None")
                    else:
                        print(f"  │  prep_reset: no-op (baseline had no PrEP users)")

                # Create engine from frozen baseline (dict input — no temp file needed)
                arm_engine = SimulationEngineV3(
                    arm_config_dict,
                    seed=seed,
                    initial_snapshot=restored_snapshot
                )

                # Guard: config end_year may equal freeze_year, giving
                # n_steps < end_step. Extend so run()'s clip does not
                # zero out the evaluation horizon.
                arm_engine.time.n_steps = max(
                    arm_engine.time.n_steps, end_step
                )

                # V11.7.2: derive step_fraction from engine; validate against config.
                # run_control.step_size is in months (e.g., 3 months → 0.25 years).
                step_fraction = arm_engine.time.years_per_step
                rc_step_months = extract_value(
                    self.base_config["run_control"]["step_size"]
                )
                rc_step_years = rc_step_months / 12.0
                if abs(step_fraction - rc_step_years) > 1e-9:
                    raise ValueError(
                        f"step_fraction mismatch: engine reports "
                        f"{step_fraction} years/step, but run_control.step_size = "
                        f"{rc_step_months} months ({rc_step_years} years/step). "
                        f"These must agree."
                    )

                # Wire EconomicsAccumulator + AdverseEventModule
                econ_arm_name = scenario.get("economics_arm_aliases", {}).get(
                    arm_name, arm_name
                )
                if arm_name == "oral_prep_expansion":
                    econ_arm_name = "oral_prep"
                econ = EconomicsAccumulator(
                    hta_config=self.hta_config,
                    groups=arm_engine.groups,
                    freeze_year=float(self.freeze_year),
                    step_fraction=step_fraction,  # V11.7.2
                    arm_name=econ_arm_name,  # V11.6.1: routes prevention costs to correct arm
                )
                ae = AdverseEventModule(
                    self.hta_config,
                    arm_engine.groups,
                    step_fraction=step_fraction,  # V11.7.2
                    rng=arm_engine.rng,
                )
                econ.set_ae_module(ae)
                arm_engine._economics = econ

                # Run from freeze point through evaluation horizon
                arm_engine.run(
                    start_step=freeze_step,
                    end_step=end_step
                )

                # Collect results
                summary = econ.get_summary()
                # V11.8.1: Build simulation dynamics records from engine outputs
                sim_records = pd.DataFrame(arm_engine.outputs).to_dict(orient='records')
                scenario_results[arm_name] = ScenarioResult(
                    name=arm_name,
                    analysis_scenario=scenario_name,
                    total_qalys_disc=summary["total_qalys_disc"],
                    total_qalys_undisc=summary["total_qalys_undisc"],
                    total_costs_disc=summary["total_costs_disc"],
                    total_costs_undisc=summary["total_costs_undisc"],
                    cost_breakdown_disc=summary["cost_breakdown_disc"],
                    cumul_new_infections=summary["cumul_new_infections"],
                    timestep_records=econ.get_timestep_records(),
                    simulation_records=sim_records,  # V11.8.1
                    # Societal perspective fields (BACKLOG-20)
                    # summary always contains these keys; they are 0.0 when the
                    # perspective block is absent from hta_config.
                    total_costs_disc_societal=summary["total_costs_disc_societal"],
                    total_societal_additions_disc=summary["total_societal_additions_disc"],
                    societal_breakdown_disc=summary["societal_breakdown_disc"],
                )

                # Console output: show societal total when perspective = societal
                _soc_additions = summary["total_societal_additions_disc"]
                _soc_total     = summary["total_costs_disc_societal"]
                print(f"  │  QALYs (disc):        {summary['total_qalys_disc']:>14,.0f}")
                print(f"  │  Costs (disc, payer):  €{summary['total_costs_disc']:>13,.0f}")
                if _soc_additions > 0:
                    print(f"  │  Societal additions:   €{_soc_additions:>13,.0f}")
                    print(f"  │  Costs (disc, total):  €{_soc_total:>13,.0f}")
                print(f"  │  Infections:           {summary['cumul_new_infections']:>14,}")
                print(f"  └─ ✓ Complete")

            all_results[scenario_name] = scenario_results

        # ── PHASE 3: Cost-Effectiveness Analysis ─────────────────────────
        print("\n" + "-" * 70)
        print("PHASE 3: Cost-Effectiveness Comparison (ZIN Sequential)")
        print("-" * 70)

        wtp = extract_value(self.hta_config["wtp_threshold_eur_per_qaly"])
        # V11.7.2: pass step_fraction from config (same for all arms).
        # step_size is in months; convert to years.
        step_fraction = extract_value(self.base_config["run_control"]["step_size"]) / 12.0

        # BACKLOG-20: read perspective flag; default "payer" when block absent
        # so that configs without the block run identically to V11.9.10.
        _perspective_mode = (
            self.hta_config
            .get("perspective", {})
            .get("primary", "payer")
        )
        print(f"\n  Perspective:  {_perspective_mode.upper()}")
        comparator = ScenarioComparator(
            wtp_threshold=wtp,
            step_fraction=step_fraction,
            perspective=_perspective_mode,   # BACKLOG-20
        )

        self._write_hta_outputs(all_results, comparator)

        self.results = all_results

    def _build_arm_config(self, arm_name: str,
                          analysis_scenario: dict,
                          param_overrides: dict = None) -> dict:
        """
        Build arm-specific configuration dict.

        Creates a modified copy of the base config with:
        - Optional param_overrides applied first (dot-path → value)
        - PrEP interventions enabled/disabled per arm definition (always wins)
        - Scenario-specific eligible_activity_classes for lenacapavir arm

        Ordering: param_overrides are applied BEFORE arm definitions.
        Arm definitions always take precedence — a caller cannot accidentally
        re-enable a disabled arm's PrEP via param_overrides.

        Arm definitions (Option A — Full Substitution):
          no_prep:     oral_prep=disabled, lenacapavir=disabled
          oral_prep:   oral_prep=enabled,  lenacapavir=disabled
          lenacapavir: oral_prep=disabled,  lenacapavir=enabled

        Args:
            arm_name: One of "no_prep", "oral_prep", "lenacapavir"
            analysis_scenario: Scenario dict from hta_config["analysis_scenarios"]
            param_overrides: Optional dict of {dot_path: value} to inject before
                arm definitions are applied. Used by the PSA runner to vary
                epidemiological parameters across draws. Default: None.

        Returns:
            Config dict for this arm (deep copy of base config with overrides applied).
            No temp file is created — pass directly to SimulationEngineV3.

        V11.9.6: BLOCKER-B fix — returns dict, adds param_overrides, removes temp file.
        """
        config = copy.deepcopy(self.base_config)

        # Apply param_overrides BEFORE arm definitions so arm definitions always win.
        if param_overrides:
            for dot_path, value in param_overrides.items():
                _deep_set(config, dot_path, value)

        if arm_name == "no_prep":
            # Disable all PrEP interventions
            if "interventions" in config:
                for intervention_name in config["interventions"]:
                    config["interventions"][intervention_name]["enabled"] = {
                        "value": False
                    }
            # Zero out all PrEP schedules.
            # Must use nested {"schedule": {year: 0.0}} shape — ScheduleManager reads
            # the "schedule" sub-key via _load_schedule(section, "schedule").
            # A flat year-keyed dict would cause _load_schedule to return an empty
            # TimeSchedule (which also produces 0.0 by coincidence, but is wrong shape).
            if "prep_schedules" in config:
                for prep_type in config["prep_schedules"]:
                    if "initiation" in config["prep_schedules"][prep_type]:
                        config["prep_schedules"][prep_type]["initiation"] = {
                            "schedule": {
                                str(year): 0.0
                                for year in range(1980, 2060)
                            }
                        }

        elif arm_name in ("oral_prep", "oral_prep_expansion"):
            # Oral PrEP on, Lenacapavir off
            if "interventions" in config:
                if "oral_prep" in config["interventions"]:
                    config["interventions"]["oral_prep"]["enabled"] = {
                        "value": True
                    }
                if "lenacapavir" in config["interventions"]:
                    config["interventions"]["lenacapavir"]["enabled"] = {
                        "value": False
                    }
            # Change C (V12.9): Apply oral_prep_overrides from scenario
            oral_overrides = analysis_scenario.get("oral_prep_overrides", {})
            if "eligible_activity_classes" in oral_overrides:
                if "oral_prep" in config.get("interventions", {}):
                    config["interventions"]["oral_prep"]["eligible_activity_classes"] = {
                        "value": oral_overrides["eligible_activity_classes"]
                    }

        elif arm_name == "lenacapavir":
            # Oral PrEP off, Lenacapavir on
            if "interventions" in config:
                if "oral_prep" in config["interventions"]:
                    config["interventions"]["oral_prep"]["enabled"] = {
                        "value": False
                    }
                if "lenacapavir" in config["interventions"]:
                    config["interventions"]["lenacapavir"]["enabled"] = {
                        "value": True
                    }

            # Apply scenario-specific eligible_activity_classes
            lena_overrides = analysis_scenario.get("lenacapavir_overrides", {})
            if "eligible_activity_classes" in lena_overrides:
                if "lenacapavir" in config.get("interventions", {}):
                    config["interventions"]["lenacapavir"][
                        "eligible_activity_classes"
                    ] = {
                        "value": lena_overrides["eligible_activity_classes"]
                    }

        elif arm_name == "oral_prep_plus_lena":
            # Change D (V12.9): Both oral PrEP and Lenacapavir enabled simultaneously
            if "interventions" in config:
                if "oral_prep" in config["interventions"]:
                    config["interventions"]["oral_prep"]["enabled"] = {"value": True}
                if "lenacapavir" in config["interventions"]:
                    config["interventions"]["lenacapavir"]["enabled"] = {"value": True}
            # Apply per-intervention eligibility
            oral_overrides = analysis_scenario.get("oral_prep_overrides", {})
            if "eligible_activity_classes" in oral_overrides:
                if "oral_prep" in config.get("interventions", {}):
                    config["interventions"]["oral_prep"]["eligible_activity_classes"] = {
                        "value": oral_overrides["eligible_activity_classes"]
                    }
            lena_overrides = analysis_scenario.get("lenacapavir_overrides", {})
            if "eligible_activity_classes" in lena_overrides:
                if "lenacapavir" in config.get("interventions", {}):
                    config["interventions"]["lenacapavir"]["eligible_activity_classes"] = {
                        "value": lena_overrides["eligible_activity_classes"]
                    }
        else:
            raise ValueError(f"Unknown arm: {arm_name}")

        # Change B (V12.9): prep_schedule_overrides injection (deep-merge)
        # The override structure is: {prep_type: {sched_key: {year: rate, ...}}}.
        # For sched_key=="initiation", year→rate pairs are injected into
        # prep_schedules[prep_type]["initiation"]["schedule"] so that
        # _validate_lenacapavir_schedule_active and ScheduleManager find them correctly.
        # Annotation keys (starting with "_") are skipped at all levels.
        schedule_overrides = analysis_scenario.get("prep_schedule_overrides", {})
        arm_schedule = schedule_overrides.get(arm_name, {})
        if arm_schedule:
            for prep_type, sched_block in arm_schedule.items():
                if prep_type not in config.get("prep_schedules", {}):
                    config.setdefault("prep_schedules", {})[prep_type] = {}
                for sched_key, sched_val in sched_block.items():
                    if sched_key.startswith("_"):
                        continue  # skip annotation keys
                    if not isinstance(sched_val, dict):
                        config["prep_schedules"][prep_type][sched_key] = sched_val
                        continue
                    # sched_val is a dict — check if it contains year→rate pairs
                    # (numeric string keys like "2025") vs a nested structure.
                    # If it has numeric year keys, inject into .schedule sub-dict.
                    year_keys = {k: v for k, v in sched_val.items()
                                 if not k.startswith("_") and k.isdigit()}
                    if year_keys:
                        # Inject year→rate pairs into prep_schedules[prep_type][sched_key]["schedule"]
                        target = config["prep_schedules"][prep_type].setdefault(sched_key, {})
                        if not isinstance(target, dict):
                            config["prep_schedules"][prep_type][sched_key] = {}
                            target = config["prep_schedules"][prep_type][sched_key]
                        schedule_dict = target.setdefault("schedule", {})
                        schedule_dict.update(year_keys)
                    else:
                        # No year keys — deep-merge non-annotation sub-keys directly
                        for sub_key, sub_val in sched_val.items():
                            if not sub_key.startswith("_"):
                                config["prep_schedules"][prep_type].setdefault(sched_key, {})[sub_key] = sub_val

        # Split runtime config pathway injection. These controls are HTA-forward
        # policy assumptions and intentionally separate from historical stock
        # corridors used in calibration/freeze-state construction.
        runtime_config = self.hta_config.get("_runtime_config")
        for pathway in analysis_scenario.get("arm_pathways", {}).get(arm_name, []):
            _apply_prep_pathway_to_arm_config(config, pathway, runtime_config)

        return config

    def _write_hta_outputs(self, all_results: Dict[str, Dict[str, ScenarioResult]],
                           comparator: ScenarioComparator):
        """
        Write all HTA output files.

        Primary output: HTA Excel report workbook (hta_report_<datestamp>.xlsx)
        Optional CSV outputs (controlled by hta_config outputs.csv_files.enabled):
        - arm_summary.csv: One row per arm per scenario
        - comparison_table.csv: Pairwise ICER table per scenario
        - timestep_{arm}_{scenario}.csv: Per-step economic data
        - price_sensitivity.csv: Scenario D price threshold curve (if applicable)
        """
        os.makedirs(self.output_dir, exist_ok=True)

        # ── Resolve output config flags ───────────────────────────────────────
        outputs_cfg    = self.hta_config.get("outputs", {})
        write_excel    = outputs_cfg.get("excel_report",  {}).get("enabled", True)
        write_csv      = outputs_cfg.get("csv_files",     {}).get("enabled", True)
        csv_outputs_cfg = outputs_cfg.get("csv_outputs", {})
        write_timestep = csv_outputs_cfg.get("timestep_detail", True)

        # ── Excel Report (primary output) ─────────────────────────────────────
        if write_excel:
            from datetime import datetime as _dt
            datestamp = _dt.now().strftime("%Y%m%d_%H%M")
            xlsx_path = os.path.join(self.output_dir, f"hta_report_{datestamp}.xlsx")

            # Use the baseline CSV saved at the end of Phase 1 (before arm
            # engines can overwrite the shared baseline_run_001.csv).
            baseline_csv = getattr(self, "_hta_baseline_csv", self._find_baseline_csv())

            HTAReportWriter(
                all_results=all_results,
                comparator=comparator,
                hta_config=self.hta_config,
                base_config=self.base_config,
                baseline_csv_path=baseline_csv,
                output_path=xlsx_path,
            ).write()

        if not write_csv:
            return

        # ── 1. Arm Summary ────────────────────────────────────────────────
        summary_rows = []
        for scenario_name, scenario_results in all_results.items():
            for arm_name, result in scenario_results.items():
                payer_components = _payer_breakdown_components(result)
                payer_breakdown_sum = sum(payer_components.values())
                payer_residual = result.total_costs_disc - payer_breakdown_sum
                if abs(payer_residual) > 1.0:
                    raise ValueError(
                        "HTA payer cost breakdown does not reconcile for "
                        f"{scenario_name}/{arm_name}: residual €{payer_residual:,.2f}."
                    )
                summary_rows.append({
                    "scenario": scenario_name,
                    "arm": arm_name,
                    "total_qalys_disc": result.total_qalys_disc,
                    "total_qalys_undisc": result.total_qalys_undisc,
                    # Payer-only costs (healthcare perspective)
                    "total_costs_disc": result.total_costs_disc,
                    "total_costs_undisc": result.total_costs_undisc,
                    "cost_drug_disc": result.cost_breakdown_disc.get("drug", 0),
                    "cost_monitoring_disc": result.cost_breakdown_disc.get("monitoring", 0),
                    "cost_hiv_care_disc": result.cost_breakdown_disc.get("hiv_care", 0),
                    "cost_adverse_events_disc": result.cost_breakdown_disc.get("adverse_events", 0),
                    "cost_terminal_care_disc": result.cost_breakdown_disc.get("terminal_care", 0),
                    "cost_prep_drug_oral_disc": payer_components["prep_drug_oral"],
                    "cost_prep_drug_lenacapavir_disc": payer_components["prep_drug_lenacapavir"],
                    "cost_hiv_art_drug_disc": payer_components["hiv_art_drug"],
                    "cost_other_drug_disc": payer_components["other_drug"],
                    "payer_breakdown_sum_disc": payer_breakdown_sum,
                    "payer_breakdown_residual_disc": payer_residual,
                    "cumul_new_infections": result.cumul_new_infections,
                    # Societal perspective costs (BACKLOG-20)
                    # These are 0.0 when perspective block absent from hta_config.
                    "total_costs_disc_societal": result.total_costs_disc_societal,
                    "total_societal_additions_disc": result.total_societal_additions_disc,
                    "cost_productivity_loss_disc": result.societal_breakdown_disc.get("productivity_loss", 0),
                    "cost_patient_time_disc": result.societal_breakdown_disc.get("patient_time", 0),
                })

        summary_df = pd.DataFrame(summary_rows)
        summary_path = os.path.join(self.output_dir, "arm_summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print(f"\n  ✓ Arm summary:        {summary_path}")

        # ── Scenario Register ─────────────────────────────────────────────
        register_rows = []
        for scenario in self.hta_config.get("analysis_scenarios", []):
            register_rows.append({
                "scenario": scenario.get("name"),
                "label": scenario.get("label"),
                "comparison_type": scenario.get("comparison_type"),
                "primary_comparison": scenario.get("primary_comparison", False),
                "comparator_arm": scenario.get("comparator_arm"),
                "intervention_arm": scenario.get("intervention_arm"),
                "arm_order": ",".join(scenario.get("arm_order", [])),
                "include_in_decision_summary": scenario.get("reporting", {}).get(
                    "include_in_decision_summary", True
                ),
                "include_price_sensitivity": scenario.get("reporting", {}).get(
                    "include_price_sensitivity", False
                ),
            })
        if register_rows and csv_outputs_cfg.get("scenario_register", True):
            register_path = os.path.join(self.output_dir, "scenario_register.csv")
            pd.DataFrame(register_rows).to_csv(register_path, index=False)
            print(f"  ✓ Scenario register:  {register_path}")

        # ── 2. Comparison Tables ──────────────────────────────────────────
        comparison_rows = []
        for scenario_name, scenario_results in all_results.items():
            table = comparator.build_comparison_table(scenario_results)
            if not table.empty:
                table.insert(0, "scenario", scenario_name)
                comparison_rows.append(table)

                # Print comparison summary
                print(f"\n  ─── {scenario_name} ───")
                for _, row in table.iterrows():
                    icer = row["icer"]
                    if isinstance(icer, str):
                        icer_str = icer
                    else:
                        icer_str = f"€{icer:,.0f}/QALY"
                    print(f"    {row['comparison']}: "
                          f"ΔQALY={row['delta_qaly']:+,.0f}  "
                          f"ΔCost=€{row['delta_cost']:+,.0f}  "
                          f"ICER={icer_str}  "
                          f"NMB=€{row['nmb']:+,.0f}")

        if comparison_rows:
            comparison_df = pd.concat(comparison_rows, ignore_index=True)
            comp_path = os.path.join(self.output_dir, "comparison_table.csv")
            comparison_df.to_csv(comp_path, index=False)
            print(f"\n  ✓ Comparison table:   {comp_path}")

        # ── 2b. Budget Impact ─────────────────────────────────────────────
        if csv_outputs_cfg.get("budget_impact", False):
            budget_rows = []
            scenarios_by_name = {
                scenario.get("name"): scenario
                for scenario in self.hta_config.get("analysis_scenarios", [])
            }
            for scenario_name, scenario_results in all_results.items():
                scenario = scenarios_by_name.get(scenario_name, {})
                comparator_arm = scenario.get("comparator_arm")
                intervention_arm = scenario.get("intervention_arm")
                if not comparator_arm or not intervention_arm:
                    continue
                if comparator_arm not in scenario_results or intervention_arm not in scenario_results:
                    continue

                ref = _annual_arm_budget_frame(scenario_results[comparator_arm])
                intv = _annual_arm_budget_frame(scenario_results[intervention_arm])
                if ref.empty or intv.empty:
                    continue

                merged = ref.merge(
                    intv,
                    on="year",
                    suffixes=("_comparator", "_intervention"),
                )
                for _, row in merged.iterrows():
                    budget_rows.append({
                        "scenario": scenario_name,
                        "comparison_type": scenario.get("comparison_type"),
                        "comparator_arm": comparator_arm,
                        "intervention_arm": intervention_arm,
                        "year": int(row["year"]),
                        "annual_cost_disc_comparator": row["annual_cost_disc_comparator"],
                        "annual_cost_disc_intervention": row["annual_cost_disc_intervention"],
                        "annual_budget_impact_disc": (
                            row["annual_cost_disc_intervention"]
                            - row["annual_cost_disc_comparator"]
                        ),
                        "cumul_cost_disc_comparator": row["cumul_cost_disc_comparator"],
                        "cumul_cost_disc_intervention": row["cumul_cost_disc_intervention"],
                        "cumul_budget_impact_disc": (
                            row["cumul_cost_disc_intervention"]
                            - row["cumul_cost_disc_comparator"]
                        ),
                        "annual_cost_undisc_comparator": row["annual_cost_undisc_comparator"],
                        "annual_cost_undisc_intervention": row["annual_cost_undisc_intervention"],
                        "annual_budget_impact_undisc": (
                            row["annual_cost_undisc_intervention"]
                            - row["annual_cost_undisc_comparator"]
                        ),
                        "cumul_cost_undisc_comparator": row["cumul_cost_undisc_comparator"],
                        "cumul_cost_undisc_intervention": row["cumul_cost_undisc_intervention"],
                        "cumul_budget_impact_undisc": (
                            row["cumul_cost_undisc_intervention"]
                            - row["cumul_cost_undisc_comparator"]
                        ),
                        "annual_qalys_disc_comparator": row["annual_qalys_disc_comparator"],
                        "annual_qalys_disc_intervention": row["annual_qalys_disc_intervention"],
                        "annual_delta_qalys_disc": (
                            row["annual_qalys_disc_intervention"]
                            - row["annual_qalys_disc_comparator"]
                        ),
                        "cumul_delta_qalys_disc": (
                            row["cumul_qalys_disc_intervention"]
                            - row["cumul_qalys_disc_comparator"]
                        ),
                        "annual_infections_comparator": row["annual_new_infections_comparator"],
                        "annual_infections_intervention": row["annual_new_infections_intervention"],
                        "annual_infections_averted": (
                            row["annual_new_infections_comparator"]
                            - row["annual_new_infections_intervention"]
                        ),
                        "cumul_infections_averted": (
                            row["cumul_new_infections_comparator"]
                            - row["cumul_new_infections_intervention"]
                        ),
                        "oral_users_hcc_comparator": row.get("msm_on_oral_hcc_comparator"),
                        "oral_users_hcc_intervention": row.get("msm_on_oral_hcc_intervention"),
                        "lena_users_hcc_comparator": row.get("msm_on_lena_hcc_comparator"),
                        "lena_users_hcc_intervention": row.get("msm_on_lena_hcc_intervention"),
                        "oral_initiations_comparator": row.get("prep_oral_initiations_comparator"),
                        "oral_initiations_intervention": row.get("prep_oral_initiations_intervention"),
                        "lena_initiations_comparator": row.get("prep_lena_initiations_comparator"),
                        "lena_initiations_intervention": row.get("prep_lena_initiations_intervention"),
                    })

            if budget_rows:
                budget_path = os.path.join(self.output_dir, "budget_impact.csv")
                pd.DataFrame(budget_rows).to_csv(budget_path, index=False)
                print(f"  ✓ Budget impact:      {budget_path}")

        # ── 3. Timestep Data ──────────────────────────────────────────────
        if write_timestep:
            for scenario_name, scenario_results in all_results.items():
                for arm_name, result in scenario_results.items():
                    if not result.timestep_records:
                        continue
                    ts_df = pd.DataFrame(result.timestep_records)
                    ts_df.insert(0, "scenario", scenario_name)
                    ts_df.insert(1, "arm", arm_name)
                    ts_path = os.path.join(
                        self.output_dir,
                        f"timestep_{arm_name}_{scenario_name}.csv"
                    )
                    ts_df.to_csv(ts_path, index=False)

            print(f"  ✓ Timestep data:      {self.output_dir}/timestep_*.csv")

        # ── 4. Price Sensitivity ──────────────────────────────────────────
        price_sensitivity_frames = []
        for scenario in self.hta_config["analysis_scenarios"]:
            ps_cfg = scenario.get("price_sensitivity")
            if ps_cfg and ps_cfg.get("enabled", False):
                scenario_name = scenario["name"]
                if scenario_name in all_results:
                    ps_range_cfg = [float(pct) for pct in ps_cfg.get("percent_values")]
                    scenario_results = all_results[scenario_name]
                    input_ref = ps_cfg.get(
                        "input_ref",
                        "prevention_costs.arm_specific.lenacapavir.injection_subsequent",
                    )
                    base_annual_price = extract_value(_deep_get(self.hta_config, input_ref))
                    if base_annual_price is None:
                        raise ValueError(
                            f"Price sensitivity input_ref not found: {input_ref}"
                        )
                    price_range = [
                        round(float(pct) * base_annual_price, 2)
                        for pct in ps_range_cfg
                    ]

                    try:
                        ps_df = comparator.price_sensitivity(
                            scenario_results,
                            price_range,
                            base_annual_price=base_annual_price,
                            comparator_arm=scenario.get("comparator_arm", "oral_prep"),
                            intervention_arm=scenario.get("intervention_arm"),
                        )
                        ps_df.insert(0, "scenario", scenario_name)
                        ps_df.insert(1, "comparison_type", scenario.get("comparison_type"))
                        ps_df.insert(2, "comparator_arm", scenario.get("comparator_arm"))
                        ps_df.insert(3, "intervention_arm", scenario.get("intervention_arm"))
                        ps_df.insert(4, "price_pct_of_base", ps_range_cfg)
                        price_sensitivity_frames.append(ps_df)

                        # Find threshold price
                        threshold = comparator.find_threshold_price(
                            scenario_results,
                            base_annual_price=base_annual_price,
                            comparator_arm=scenario.get("comparator_arm", "oral_prep"),
                            intervention_arm=scenario.get("intervention_arm"),
                        )
                        if threshold is not None:
                            print(f"    {scenario_name} threshold price: €{threshold:,.0f}/year")
                        else:
                            print(f"    {scenario_name} threshold price: N/A")

                    except ValueError as e:
                        print(f"  ⚠️  Price sensitivity skipped for {scenario_name}: {e}")
            elif "price_sensitivity_range" in scenario:
                # Legacy Scenario D format.
                scenario_name = scenario["name"]
                if scenario_name in all_results:
                    ps_range_cfg  = scenario["price_sensitivity_range"]
                    scenario_results = all_results[scenario_name]
                    base_annual_price = extract_value(
                        self.hta_config
                            .get("prevention_costs", {})
                            .get("arm_specific", {})
                            .get("lenacapavir", {})
                            .get("injection_subsequent", {"value": None})
                    )
                    if isinstance(ps_range_cfg, dict):
                        min_pct  = ps_range_cfg["min_pct"]
                        max_pct  = ps_range_cfg["max_pct"]
                        step_pct = ps_range_cfg["step_pct"]
                        price_range = [
                            round(pct / 100.0 * base_annual_price, 2)
                            for pct in range(min_pct, max_pct + step_pct, step_pct)
                        ]
                    else:
                        price_range = ps_range_cfg
                    try:
                        ps_df = comparator.price_sensitivity(
                            scenario_results, price_range,
                            base_annual_price=base_annual_price
                        )
                        ps_df.insert(0, "scenario", scenario_name)
                        price_sensitivity_frames.append(ps_df)
                    except ValueError as e:
                        print(f"  ⚠️  Price sensitivity skipped: {e}")

        if price_sensitivity_frames and csv_outputs_cfg.get("price_sensitivity", True):
            ps_path = os.path.join(self.output_dir, "price_sensitivity.csv")
            pd.concat(price_sensitivity_frames, ignore_index=True).to_csv(ps_path, index=False)
            print(f"  ✓ Price sensitivity:  {ps_path}")

        # ── 5. Summary Table ──────────────────────────────────────────────
        for scenario_name, scenario_results in all_results.items():
            summary_tbl = comparator.summary_table(scenario_results)
            if not summary_tbl.empty:
                stbl_path = os.path.join(
                    self.output_dir,
                    f"summary_table_{scenario_name}.csv"
                )
                summary_tbl.to_csv(stbl_path, index=False)

    def _find_baseline_csv(self) -> str:
        """
        Locate the most recent baseline run CSV relative to the output directory.

        Searches the parent of output_dir for files matching 'baseline_run_*.csv'.
        Falls back to output_dir itself. Returns the path or empty string if not found.
        """
        import glob
        search_dirs = [
            os.path.dirname(self.output_dir),
            self.output_dir,
        ]
        for search_dir in search_dirs:
            matches = sorted(glob.glob(os.path.join(search_dir, "baseline_run_*.csv")))
            if matches:
                return matches[-1]  # most recent (lexicographic)
        return ""

    def _get_seed(self) -> int:
        """
        Extract the required run seed from the canonical V10 run_control block.

        HTA and current main-run configs store this value at run_control.seed.
        A missing seed is a configuration error because falling back to an
        implicit default changes stochastic trajectories silently.
        """
        try:
            seed = self.base_config["run_control"]["seed"]
        except KeyError as exc:
            raise ValueError(
                "MultiScenarioRunner requires run_control.seed in the base "
                "configuration; simulation_controls.seed and implicit defaults "
                "are not accepted."
            ) from exc

        try:
            return int(extract_value(seed))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"MultiScenarioRunner requires run_control.seed to be "
                f"integer-compatible, got {seed!r}."
            ) from exc

    def _set_scenario_seed(self, scenario_config: dict, scenario_seed: int) -> None:
        """
        Record the scenario-specific seed in the canonical run_control block.

        Independent no-freeze scenario mode still executes with base_seed + i,
        but the temporary config now records the same seed read by V10 readers.
        """
        run_control = scenario_config.setdefault("run_control", {})
        if not isinstance(run_control, dict):
            raise ValueError("Scenario config run_control must be a dictionary")
        run_control["seed"] = {"value": int(scenario_seed)}

    # ──────────────────────────────────────────────────────────────────────
    # Legacy V5 Methods (preserved for backward compatibility)
    # ──────────────────────────────────────────────────────────────────────

    def _run_with_reused_baseline(self):
        """
        Legacy V5 baseline-reuse mode.

        For non-HTA scenario runs that use baseline freezing from the V5
        policy config. Preserved for backward compatibility.
        """
        print("\n⚠️  Legacy V5 baseline-reuse mode.")
        print("   For HTA analysis, use: --hta configs/hta_config.json")

        seed = self._get_seed()

        # Run baseline to freeze year
        print(f"\nRunning baseline to freeze year {self.policy['freeze_year']}...")
        baseline_engine = SimulationEngineV3(self.config_path, seed=seed)
        baseline_engine._snapshot_manager = self.snapshot_manager
        baseline_engine.run()

        snapshot = self.snapshot_manager.baseline_snapshot
        if snapshot is None:
            raise RuntimeError("Baseline freeze failed — no snapshot captured")

        freeze_step = snapshot.step

        # Fork scenarios
        for i, scenario in enumerate(self.scenarios, 1):
            print(f"\n[{i}/{len(self.scenarios)}] Running scenario: {scenario['name']}")
            scenario_config = self._apply_scenario_overrides(scenario)

            # Write scenario config to temp file
            fd, temp_path = tempfile.mkstemp(suffix=".json",
                                             prefix=f"v5_{scenario['name']}_")
            with os.fdopen(fd, 'w') as f:
                json.dump(scenario_config, f, indent=2)

            try:
                restored = self.snapshot_manager.restore(scenario['name'])
                engine = SimulationEngineV3(temp_path, seed=seed,
                                            initial_snapshot=restored)
                engine.run(start_step=freeze_step)

                self.results[scenario['name']] = engine.outputs
                print(f"  ✅ Scenario complete")
            except Exception as e:
                print(f"  ❌ Scenario failed: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    def _run_independent_scenarios(self):
        """
        Run scenarios independently (no baseline freezing).

        Each scenario runs from its own initial random state.
        """
        print("\n" + "-" * 70)
        print("Running scenarios independently (no baseline freeze)")
        print("-" * 70)

        base_seed = self._get_seed()

        for i, scenario in enumerate(self.scenarios, 1):
            print(f"\n[{i}/{len(self.scenarios)}] Running scenario: {scenario['name']}")
            print(f"  Description: {scenario['description']}")

            scenario_seed = base_seed + i
            scenario_config = self._apply_scenario_overrides(scenario)

            self._set_scenario_seed(scenario_config, scenario_seed)

            if "output" not in scenario_config:
                scenario_config["output"] = {}
            base_tag = scenario_config.get("output", {}).get("run_tag", {})
            if isinstance(base_tag, dict):
                base_tag = base_tag.get("value", "run")
            scenario_config["output"]["run_tag"] = {
                "value": f"{base_tag}_{scenario['name']}"
            }

            fd, temp_path = tempfile.mkstemp(suffix=".json",
                                             prefix=f"scenario_{scenario['name']}_")
            with os.fdopen(fd, 'w') as f:
                json.dump(scenario_config, f, indent=2)

            try:
                print(f"  ⏳ Running simulation...")
                engine = SimulationEngineV3(temp_path, seed=scenario_seed)

                for step in range(engine.time.n_steps):
                    engine.step_simulation(step)

                if engine.output_writer.enabled and engine.output_writer.write_per_run:
                    df = pd.DataFrame(engine.outputs)
                    df.insert(0, 'scenario', scenario['name'])
                    engine.output_writer.write_run_output(df, run_number=1)

                self.results[scenario['name']] = engine.outputs
                print(f"  ✅ Scenario complete")

            except Exception as e:
                print(f"  ❌ Scenario failed: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    def _apply_scenario_overrides(self, scenario: Dict) -> Dict:
        """
        Apply scenario-specific overrides to base config (legacy V5 mode).

        Args:
            scenario: Scenario definition with overrides

        Returns:
            Config with scenario overrides applied
        """
        overrides = scenario.get("overrides", {})

        if overrides.get("prep_force_disable", False):
            print("  ⚠️  Special override: PrEP force disabled")
            overrides_copy = overrides.copy()
            del overrides_copy["prep_force_disable"]

            scenario_config = merge_config_override(self.base_config, overrides_copy)
            scenario_config = apply_prep_force_disable(scenario_config)

            validation_results = validate_prep_disabled(
                scenario_config, scenario['name']
            )
            errors = [r for r in validation_results
                      if not r.passed and r.severity == "error"]
            if errors:
                print("  ❌ PrEP disable validation failed:")
                for error in errors:
                    print(f"     {error}")
                raise ValueError(
                    f"PrEP not properly disabled in scenario {scenario['name']}"
                )
            else:
                print("  ✓ PrEP disable validated")
        else:
            scenario_config = merge_config_override(self.base_config, overrides)

        return scenario_config


def main():
    """Main entry point for scenario runner."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  HTA mode:    python run_scenarios.py <config.json> --hta <hta_config.json> [--output_dir <dir>]")
        print("  Legacy mode: python run_scenarios.py <config.json> [--output_dir <dir>]")
        sys.exit(1)

    config_path = sys.argv[1]

    # Parse arguments
    output_dir = "outputs/hta_results"
    hta_config_path = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--hta" and i + 1 < len(args):
            hta_config_path = args[i + 1]
            i += 2
        elif args[i] == "--output_dir" and i + 1 < len(args):
            output_dir = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}")
            sys.exit(1)

    # Run
    runner = MultiScenarioRunner(
        config_path,
        output_dir=output_dir,
        hta_config_path=hta_config_path
    )
    results = runner.run()

    print("\n" + "=" * 70)
    print("✅ SCENARIO RUN COMPLETE")
    print("=" * 70)

    if runner.hta_mode:
        for scenario_name, scenario_results in results.items():
            print(f"\n  {scenario_name}:")
            for arm_name, result in scenario_results.items():
                print(f"    {arm_name}: "
                      f"QALYs={result.total_qalys_disc:,.0f}  "
                      f"Costs=€{result.total_costs_disc:,.0f}  "
                      f"Infections={result.cumul_new_infections}")
    else:
        print(f"\nResults for {len(results)} scenarios:")
        for name in results:
            print(f"  ✓ {name}")


if __name__ == "__main__":
    main()
