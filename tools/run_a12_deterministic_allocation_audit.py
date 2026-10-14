"""Run Phase-2 task A-12: deterministic-allocation inventory and base-case audit.

This is a diagnostic-only runner.  It does not alter model behaviour.  The
inventory is deliberately explicit so that each event/allocation pathway has a
reviewable classification, source anchor, base-case activity statement, and
recommendation.
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
from typing import Iterable, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_utils import extract_value  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402


MODEL_FILES = (
    "engine_v3.py",
    "run_v3.py",
    "transmission_corrected.py",
    "demography.py",
    "migration.py",
    "adverse_events.py",
    "stochastic_allocation.py",
)

VALID_CLASSIFICATIONS = {
    "aggregate_preserving",
    "rate_stratified_aggregate",
    "exact_total_assignment",
    "direct_exact_transfer",
    "cellwise",
}


def _row(
    site_id: str,
    process: str,
    module: str,
    function: str,
    anchor: str,
    mechanism: str,
    classification: str,
    fragmentation_sensitive: bool,
    base_case_status: str,
    differential: int | None,
    recommendation: str,
) -> dict:
    return {
        "site_id": site_id,
        "process": process,
        "module": module,
        "function": function,
        "source_anchor": anchor,
        "mechanism": mechanism,
        "classification": classification,
        "fragmentation_sensitive": fragmentation_sensitive,
        "base_case_status": base_case_status,
        "between_arm_event_differential_people": differential,
        "recommendation": recommendation,
        "claim_status": "well-supported (code inspection + deterministic run)",
    }


# One row per distinct allocation contract. Repeated calls with the same
# contract (for example progression by stage) are kept separate where they map
# to distinct model processes or distinct rate blocks.
INVENTORY = (
    _row("INIT-01", "initial population", "engine_v3.py", "PopulationState.initialize_from_relative_weights", "female_count = int(sex_weights['female'] * total_size)", "Truncate each age-sex weight independently.", "cellwise", True, "Constructor path executes, but its result is replaced by the identical frozen snapshot in both evaluation arms.", 0, "Convert to an exact fixed-total largest-remainder allocation before the production baseline is frozen."),
    _row("INIT-02", "initial orientation", "engine_v3.py", "PopulationState.initialize_from_relative_weights", "msm = int(male_count * p_msm)", "Split each age-specific male total; heterosexual men receive the exact residual.", "exact_total_assignment", False, "Historical initialization only; shared before the arm fork.", None, "Keep; optionally consolidate with the shared fixed-total allocator."),
    _row("INIT-03", "initial activity", "engine_v3.py", "PopulationState.initialize_from_relative_weights", "result[activity] = remaining", "Assign two truncated shares and give the final activity the exact residual.", "exact_total_assignment", False, "Historical initialization only; shared before the arm fork.", None, "Keep total-preserving contract; a shared largest-remainder helper would reduce bespoke code."),
    _row("INIT-04", "initial HIV prevalence", "engine_v3.py", "PopulationState._seed_initial_hiv", "initial_infected = int(susceptible * initial_prev)", "Truncate prevalence independently in every age-orientation-activity cell.", "cellwise", True, "Configured initial_hiv_prevalence is 0.0; no events in either arm, and arm constructors are overwritten by the frozen snapshot.", 0, "Convert to deterministic_allocate_events over the full eligible tensor if this legacy path is retained."),
    _row("INIT-05", "legacy automatic childhood initialization", "demography.py", "ChildhoodPool.initialize", "male_per_bin = int(avg_per_bin * frac_male)", "Truncate male and female counts independently, then repeat those counts in every childhood age bin.", "cellwise", True, "Dormant: the production relative-weights initializer writes explicit age-sex childhood counts and never calls DemographyModule.initialize_childhood.", 0, "Remove the unused auto initializer or replace it with an exact fixed-total age-sex allocation before reuse."),
    _row("SEED-01", "epidemic seed target", "run_v3.py", "SimulationEngineV3._apply_epidemic_seed", "n_seed_msm = int(n_msm_susc  * self._epidemic_seed_prev * msm_frac)", "Compute one scalar target per substantively different orientation seed block.", "rate_stratified_aggregate", False, "Fires in 1975 in the single shared historical trajectory; already fired before the 2025 arm fork.", None, "Keep scalar targets; document that orientation-specific seed weights define different blocks."),
    _row("SEED-02", "epidemic seed stage mix", "run_v3.py", "SimulationEngineV3._epidemic_seed_stage_counts", "remainder = n_to_seed - sum(counts.values())", "Largest-remainder stage allocation preserves the requested seed total.", "exact_total_assignment", False, "Shared historical trajectory only.", None, "Keep."),
    _row("SEED-03", "epidemic seed placement", "run_v3.py", "SimulationEngineV3._apply_epidemic_seed", "allocs[largest] += remainder", "Floor proportional cell allocations, then place the full residual in one eligible cell.", "exact_total_assignment", False, "Shared historical trajectory only.", None, "Preserve exact total; replace single-cell residual placement with stable largest remainder when this path is next revised."),
    _row("TX-01", "transmission", "transmission_corrected.py", "TransmissionEngine._deterministic_allocate_expected_infections", "target = min(", "Round expected infection totals independently within orientation blocks and allocate residuals by largest fractional expectation. The orientation boundary separates the structurally disjoint heterosexual and MSM transmission systems.", "aggregate_preserving", False, "Active in both evaluation arms; deterministic production groups by orientation. Stochastic transmission is unchanged.", None, "Keep the orientation boundary. Re-run the grouping decision if bridging transmission or a bisexual category is introduced."),
    _row("TX-02", "legacy MSM role-split transmission", "transmission_corrected.py", "TransmissionEngine._calc_msm_infections", "total += int(n_c * p_trans)", "Truncate infections separately after splitting positive contacts into receptive/insertive roles.", "cellwise", True, "Dormant in deterministic mode: calculate_transmission uses _calc_msm_expected_probability and TX-01 instead; this branch is reached only by the stochastic caller.", 0, "Delete the unreachable deterministic branch or return an unrounded expectation for final allocation through TX-01."),
    _row("PROG-01", "acute-to-chronic progression", "run_v3.py", "SimulationEngineV3._step_disease_progression", '"progression_acute_to_chronic", acute_pop', "Shared aggregate-preserving binomial approximation over the eligible tensor.", "aggregate_preserving", False, "Active in both evaluation arms.", None, "Keep."),
    _row("PROG-02", "chronic-to-AIDS off ART", "run_v3.py", "SimulationEngineV3._step_disease_progression", '"progression_chronic_to_aids_off_art"', "Aggregate within the off-ART rate block.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep."),
    _row("PROG-03", "chronic-to-AIDS on ART", "run_v3.py", "SimulationEngineV3._step_disease_progression", '"progression_chronic_to_aids_on_art"', "Aggregate within the on-ART rate block.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep."),
    _row("DX-01", "diagnosis non-MSM", "run_v3.py", "SimulationEngineV3._step_diagnosis", '"diagnosis_non_msm", non_msm_undiag', "Aggregate within the non-MSM diagnosis-rate block.", "rate_stratified_aggregate", False, "Active when the MSM-relative schedule is enabled.", None, "Keep."),
    _row("DX-02", "diagnosis MSM", "run_v3.py", "SimulationEngineV3._step_diagnosis", '"diagnosis_msm", msm_undiag', "Aggregate within the MSM diagnosis-rate block.", "rate_stratified_aggregate", False, "Active when the MSM-relative schedule is enabled.", None, "Keep."),
    _row("DX-03", "diagnosis common-rate fallback", "run_v3.py", "SimulationEngineV3._step_diagnosis", '"diagnosis_all_orientations", undiagnosed', "Aggregate over all cells sharing the fallback diagnosis rate.", "aggregate_preserving", False, "Alternative diagnosis path; activity depends on the composed config.", None, "Keep."),
    _row("ART-01", "ART initiation", "run_v3.py", "SimulationEngineV3._step_art_transitions", '"art_initiation", eligible', "Aggregate over the eligible tensor for the scheduled initiation rate.", "aggregate_preserving", False, "Active in both evaluation arms.", None, "Keep."),
    _row("ART-02", "ART dropout", "run_v3.py", "SimulationEngineV3._step_art_transitions", '"art_dropout", on_art', "Aggregate over the on-ART tensor for the dropout rate.", "aggregate_preserving", False, "Active in both evaluation arms.", None, "Keep."),
    _row("PREP-01", "PrEP uptake", "run_v3.py", "SimulationEngineV3._step_prep_transitions", '("prep_uptake", prep_name, orient_idx, act_idx)', "Aggregate within each orientation-activity block sharing an uptake modifier.", "rate_stratified_aggregate", False, "Oral-PrEP arm only; no-PrEP arm has no enabled PrEP product.", None, "Keep."),
    _row("PREP-02", "PrEP dropout", "run_v3.py", "SimulationEngineV3._step_prep_transitions", '("prep_dropout", prep_name)', "Aggregate within each product-specific dropout-rate block.", "rate_stratified_aggregate", False, "Oral-PrEP arm only.", None, "Keep."),
    _row("PREP-03", "PrEP target/corridor additions and removals", "run_v3.py", "SimulationEngineV3._allocate_fixed_events", "remainder = n_events - int(base.sum())", "Allocate an exact scheduled headcount subject to capacity.", "exact_total_assignment", False, "Forward target path is active for oral PrEP; historical corridor is outside the evaluation window.", None, "Keep; consider sharing this helper with migration and initialization."),
    _row("PREP-04", "PrEP product switch", "run_v3.py", "SimulationEngineV3._apply_prep_switch_share", "switches = self._allocate_fixed_events(capacity, needed, weights=weights)", "Round one aggregate switch target and allocate that exact count.", "exact_total_assignment", False, "Not active in base_case_context.", None, "Keep."),
    _row("PREP-05", "lenacapavir waning", "run_v3.py", "SimulationEngineV3._step_lena_waning_transition", "waning_pop = self.state.population", "Move the entire waning tensor to None without rounding.", "direct_exact_transfer", False, "Not active in base_case_context.", None, "Keep."),
    _row("BIRTH-01", "birth generation", "demography.py", "DemographyModule.calculate_births", "women_count = np.sum(population[age_idx, female_idx", "Aggregate women across all epidemiological cells before rounding within each age-specific fertility-rate block.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep; PrEP/HIV tensor fragmentation cannot change women_count within an age block."),
    _row("BIRTH-02", "birth sex assignment", "demography.py", "DemographyModule.add_births_to_childhood", "n_female = n_births - n_male", "Assign males by one scalar truncation and females as the exact residual.", "exact_total_assignment", False, "Active in both evaluation arms.", None, "Keep."),
    _row("BIRTH-03", "fixed births", "demography.py", "DemographyModule.calculate_fixed_births", "return int(annual_entrants * self.time.years_per_step)", "Truncate one aggregate fixed entrant count per timestep.", "aggregate_preserving", False, "Inactive because the base case uses fertility_table mode.", None, "Keep; use round rather than floor only if the fixed-net-addition convention is scientifically revised."),
    _row("CHMORT-01", "child background mortality", "demography.py", "ChildhoodPool.apply_mortality", "deaths = round(pop * mort_rate)", "One scalar draw per age-sex life-table rate block; childhood has no PrEP/HIV subcells.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep; optional consolidation with deterministic_allocate_events would standardize tie handling."),
    _row("CHMORT-02", "constant child background mortality fallback", "demography.py", "ChildhoodPool.apply_mortality", "mort_rate = 1 - (1 - self.mortality_annual)", "Round the same constant mortality probability independently in every child age-sex cell.", "cellwise", True, "Inactive because demography.life_table.enabled is true in the base case.", 0, "If the constant fallback is retained, allocate its events over the complete childhood array with deterministic_allocate_events."),
    _row("BGMORT-01", "adult constant background mortality", "demography.py", "DemographyModule._calculate_constant_mortality", "deaths = deterministic_allocate_events(population, mortality_rate)", "Aggregate over the full tensor because every cell has the same fallback rate.", "aggregate_preserving", False, "Inactive because the life table is enabled.", None, "Keep."),
    _row("BGMORT-02", "adult life-table background mortality", "demography.py", "DemographyModule._calculate_life_table_mortality", "deterministic_allocate_events(female_pop, female_mort_step)", "Aggregate separately within each age-sex mortality-rate block, including all PrEP/HIV cells.", "rate_stratified_aggregate", False, "Active in both evaluation arms; this is the ENG-01/A-11 repair.", None, "Keep."),
    _row("HIVMORT-01", "HIV mortality off ART", "run_v3.py", "SimulationEngineV3._step_hiv_mortality", '("hiv_mortality", stage_name, "off_art")', "Aggregate within each stage/off-ART mortality-rate block.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep."),
    _row("HIVMORT-02", "HIV mortality on ART", "run_v3.py", "SimulationEngineV3._step_hiv_mortality", '("hiv_mortality", stage_name, "on_art")', "Aggregate within each stage/on-ART mortality-rate block.", "rate_stratified_aggregate", False, "Active in both evaluation arms.", None, "Keep."),
    _row("MIG-01", "net migration quantity", "migration.py", "MigrationModule.apply", "n_add = int(round(step_net))", "Round one aggregate scheduled net-migration count per timestep.", "aggregate_preserving", False, "Active in both evaluation arms and arm-independent at the target-count level.", None, "Keep."),
    _row("MIG-02", "positive migration placement", "migration.py", "_distribute_int", "deficit = n - int(floors.sum())", "Largest-remainder allocations preserve each fixed entrant total exactly.", "exact_total_assignment", False, "Active in both evaluation arms.", None, "Keep."),
    _row("MIG-03", "migrant HIV annual-to-step split", "migration.py", "MigrationModule._annual_count_to_step_count", "return base + (1 if step_in_year < remainder else 0)", "Split the rounded annual total exactly across regular timesteps.", "exact_total_assignment", False, "Historical source can be active; forward activity follows configured years.", None, "Keep."),
    _row("MIG-04", "negative migration removal", "migration.py", "MigrationModule._apply_negative", "excess = actual_removed - n_remove", "Floor cell stocks, then add back largest remainders so removals equal the aggregate target exactly.", "aggregate_preserving", False, "Active in negative-migration years only; total event count remains exact.", None, "Keep; note that composition can be tie-order sensitive even though total removals cannot drift."),
    _row("AGE-01", "adult residence-queue aging", "run_v3.py", "SimulationEngineV3._apply_fractional_aging_adults", "outflow = self._adult_residence_population[age_idx, -1", "Move exact integer residence-phase cohorts; reconciliation preserves each live cell total.", "direct_exact_transfer", False, "Active production aging path in both evaluation arms.", None, "Keep."),
    _row("AGE-02", "child residence-queue aging", "demography.py", "ChildhoodPool.apply_fractional_aging", "aging_out = self.residence_phases[-1, -1, :].copy()", "Move exact integer residence-phase cohorts.", "direct_exact_transfer", False, "Active production aging path in both evaluation arms.", None, "Keep."),
    _row("AGE-03", "adult legacy fractional aging", "run_v3.py", "SimulationEngineV3._apply_fractional_aging_adults_legacy", "staying = np.round(self.state.population[age_idx", "Round every epidemiological tensor cell independently.", "cellwise", True, "Inactive: aging_method is fractional/residence_queue in both arms.", 0, "Remove the legacy path or convert each age-rate block with deterministic_allocate_events before it is used again."),
    _row("AGE-04", "child legacy fractional aging", "demography.py", "ChildhoodPool.apply_fractional_aging_legacy", "staying = np.round(self.population[bin_idx", "Round each child age-sex cell independently even though the aging probability is common.", "cellwise", True, "Inactive: aging_method is fractional/residence_queue in both arms.", 0, "Remove the legacy path or convert each age block with deterministic_allocate_events."),
    _row("AGE-05", "discrete-aging continuous debut", "demography.py", "ChildhoodPool.process_continuous_debut", "debuting_females = int(n_females * p_debut)", "Truncate female and male debut counts independently under the same probability.", "cellwise", True, "Inactive: the discrete aging path is not selected.", 0, "Use deterministic_allocate_events over the two-sex vector if the discrete path is retained."),
    _row("AGE-06", "discrete adult/child aging", "run_v3.py", "SimulationEngineV3._apply_discrete_aging_adults", "new_pop[age_idx + 1", "Move entire integer age bins without a probabilistic allocation.", "direct_exact_transfer", False, "Inactive: the discrete aging path is not selected.", None, "Keep only as a documented legacy sensitivity."),
    _row("AGE-07", "adult residence-phase initialization/reconciliation", "run_v3.py", "SimulationEngineV3._allocate_int_by_weights", "remainder = total - int(base.sum())", "Largest-remainder distribution preserves every adult cell target across residence phases.", "exact_total_assignment", False, "Active setup/reconciliation path in both evaluation arms.", None, "Keep."),
    _row("AGE-08", "child residence-phase initialization/reconciliation", "demography.py", "ChildhoodPool._allocate_int_by_weights", "remainder = total - int(base.sum())", "Largest-remainder distribution preserves every child age-sex target across residence phases.", "exact_total_assignment", False, "Active setup/reconciliation path in both evaluation arms.", None, "Keep."),
    _row("DEBUT-01", "sexual-debut orientation", "demography.py", "DemographyModule.process_sexual_debut", "n_hetero = debuting_males - n_msm", "One male total is split and the heterosexual category receives the exact residual.", "exact_total_assignment", False, "Active for residence-queue aging outflows in both arms.", None, "Keep; optionally use a shared exact fixed-total allocator."),
    _row("DEBUT-02", "sexual-debut activity", "demography.py", "DemographyModule._distribute_to_activities", "remainder = n - np.sum(result)", "Largest-remainder activity assignment preserves each debut total.", "exact_total_assignment", False, "Active in both evaluation arms.", None, "Keep."),
    _row("POPADJ-01", "population adjustment", "demography.py", "DemographyModule.apply_population_adjustments", ").astype(np.int64)", "Multiply and truncate every selected tensor cell independently.", "cellwise", True, "Inactive: demography.advanced.population_adjustments.enabled is false.", 0, "Do not enable as written; convert to an aggregate target plus capacity-safe largest-remainder allocation, or delete this calibration-only path."),
    _row("AE-01", "adverse-event onset/resolution", "adverse_events.py", "AdverseEventModule._item_state_machine_ongoing", "new_ae   = int(non_ae     * onset_rate_q)", "Apply scalar transition probabilities to each product-event state total, not to population tensor cells.", "rate_stratified_aggregate", False, "Economics-only; configured AE mode determines activity.", None, "Keep; it cannot acquire PrEP-state fragmentation within an item because users is already scalar."),
    _row("AE-02", "adverse-event severity", "adverse_events.py", "AdverseEventModule._item_state_machine_ongoing", "remaining = new_ae", "Allocate severity counts and assign the final category the exact residual.", "exact_total_assignment", False, "Economics-only; configured AE mode determines activity.", None, "Keep."),
    _row("AE-03", "adverse-event resolution by severity", "adverse_events.py", "AdverseEventModule._item_state_machine_ongoing", "to_remove = min(int(resolved * frac), state[sev])", "Truncate the aggregate resolved count independently across severity cells without allocating the residual.", "cellwise", True, "Inactive in base_case_context because adverse_events.mode is prevalence, not state_machine; neither arm executes this state transition.", 0, "Before state_machine mode is used, allocate the exact resolved total across severity capacities with a fixed-total largest-remainder allocator."),
    _row("ALLOC-01", "shared deterministic binomial allocation", "stochastic_allocation.py", "deterministic_allocate_events", "target = min(int(np.round(expected.sum()))", "Round one aggregate expectation and allocate residuals by largest fractional expectation subject to capacity.", "aggregate_preserving", False, "Active through progression, diagnosis, ART, PrEP, and mortality call sites.", None, "Keep as the canonical probability-event allocator."),
    _row("ALLOC-02", "shared deterministic total placement", "stochastic_allocation.py", "deterministic_allocate_total", "base = np.floor(raw).astype(np.int64)", "Place an exact integer total across cells by largest remainder on the supplied weights; no rate, no randomness.", "exact_total_assignment", False, "Added 2026-09-14 for C-1. Active only on the exogenous non-MSM forcing path.", 0, "Keep as the canonical fixed-total allocator. Deliberately NOT reusing SimulationEngineV3._allocate_int_by_weights, which falls back to UNIFORM weights when the pool is empty and would place people into cells holding nobody. Consolidating the four existing largest-remainder implementations onto this contract is the follow-up."),
    _row("C1-01", "exogenous non-MSM diagnosis forcing", "run_v3.py", "SimulationEngineV3._step_exogenous_non_msm", "counts = deterministic_allocate_total(take, susceptible)", "Convert an exact observed annual count of susceptible non-MSM people to Chronic/Diagnosed, allocated across age and activity by largest remainder on the current susceptible structure.", "exact_total_assignment", False, "Active from 1996 when exogenous_non_msm.enabled. Consumes no randomness, so it cannot re-key any process stream (A-24 failure mode designed out).", 0, "Keep. The annual total is observed data, not a rate, so exact assignment is the correct contract."),
)


# Rounding syntax is intentionally broader than event allocation. Every
# function found by the AST scan must either be represented in INVENTORY or be
# explicitly triaged here. This prevents an apparently complete grep from
# silently discarding casts used for time indices, validation, or reporting.
NON_ALLOCATION_FUNCTIONS = {
    ("adverse_events.py", "_compute_v2"): "scalar input normalization before AE costing",
    ("demography.py", "_ensure_residence_phases"): "time-grid phase-count integerization",
    ("demography.py", "flush_remaining_debut_eligible"): "read/clear exact stock",
    ("demography.py", "get_by_sex"): "output scalar conversion",
    ("demography.py", "get_total"): "output scalar conversion",
    ("demography.py", "initialize_residence_phases"): "supporting call covered by AGE-08",
    ("demography.py", "_reconcile_residence_phases"): "supporting call covered by AGE-08",
    ("demography.py", "register_positive_residence_delta"): "integer state bookkeeping",
    ("engine_v3.py", "__init__"): "random-seed scalar conversion",
    ("engine_v3.py", "_validate_post_seed"): "validation/output scalar conversion",
    ("engine_v3.py", "split_activities"): "nested helper covered by INIT-03",
    ("migration.py", "__init__"): "configuration parsing and index conversion",
    ("migration.py", "_allocate_by_weights"): "supporting call covered by MIG-02",
    ("migration.py", "_apply_positive"): "supporting scalar conversions covered by MIG-02",
    ("migration.py", "_get_annual_net"): "calendar-year lookup index",
    ("migration.py", "_get_step_migrant_hiv_requested"): "quantity logic covered by MIG-03",
    ("migration.py", "_init_migrant_hiv_config"): "configuration parsing",
    ("migration.py", "_migrant_hiv_state_weights"): "state-index construction",
    ("migration.py", "_parse_migrant_hiv_fallback_years"): "configuration parsing",
    ("migration.py", "_place_adult_entrants"): "exact residual/LRM placement covered by MIG-02",
    ("migration.py", "_place_migrant_hiv_entrants"): "nested LRM placement covered by MIG-02",
    ("run_v3.py", "_aids_state_counts"): "output scalar conversion",
    ("run_v3.py", "_apply_prep_forward_target_stock"): "supporting calls covered by PREP-03",
    ("run_v3.py", "_apply_prep_stock_corridor"): "supporting calls covered by PREP-03",
    ("run_v3.py", "_ensure_adult_residence_phases"): "time-grid phase-count integerization",
    ("run_v3.py", "_initialize_adult_residence_phases"): "supporting call covered by AGE-07",
    ("run_v3.py", "_prep_forward_target_stock"): "scheduled aggregate target conversion covered by PREP-03",
    ("run_v3.py", "_preserved_total_switch_stock_offset"): "exact stock summation",
    ("run_v3.py", "_reconcile_adult_residence_phases"): "supporting call covered by AGE-07",
    ("run_v3.py", "_register_adult_phase_entries"): "integer state bookkeeping",
    ("run_v3.py", "_step_aging_discrete"): "time-grid step-count integerization",
    ("run_v3.py", "apply_proportional_seed"): "nested helper covered by SEED-03",
    ("run_v3.py", "count_susceptibles"): "exact stock summation",
    ("run_v3.py", "record_outputs"): "output scalar conversion",
    ("run_v3.py", "scheduled_bound"): "scheduled aggregate target conversion covered by PREP-03",
    ("run_v3.py", "validate_art_mass_balance"): "validation scalar conversion",
    ("run_v3.py", "validate_prep_mass_balance"): "validation scalar conversion",
    ("transmission_corrected.py", "calculate_transmission"): "stochastic-only contact/event conversion; deterministic path is TX-01",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: Iterable[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return completed.stdout.rstrip()


def select_scenario(scenarios: Sequence[dict], name: str) -> list[dict]:
    selected = [scenario for scenario in scenarios if scenario.get("name") == name]
    if len(selected) != 1:
        raise ValueError(f"expected exactly one {name!r} scenario, found {len(selected)}")
    return selected


def locate_inventory(inventory: Sequence[dict]) -> list[dict]:
    located = []
    cache: dict[str, tuple[list[str], ast.AST]] = {}
    for row in inventory:
        if row["module"] not in cache:
            source = (ROOT / row["module"]).read_text(encoding="utf-8")
            cache[row["module"]] = (source.splitlines(), ast.parse(source))
        lines, tree = cache[row["module"]]
        path = row["function"].split(".")
        nodes = list(tree.body)
        target = None
        for part in path:
            target = next(
                (
                    node
                    for node in nodes
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == part
                ),
                None,
            )
            if target is None:
                raise AssertionError(
                    f"function path not found for {row['site_id']}: {row['function']}"
                )
            nodes = list(target.body)
        matches = [
            i + 1
            for i, line in enumerate(lines)
            if target.lineno <= i + 1 <= target.end_lineno
            and row["source_anchor"] in line
        ]
        if not matches:
            raise AssertionError(
                f"source anchor not found in {row['function']} for "
                f"{row['site_id']}: {row['source_anchor']!r}"
            )
        item = dict(row)
        item["source_line"] = matches[0]
        located.append(item)
    return located


def syntax_candidates() -> list[dict]:
    """Return every integerization/rounding call in the reviewed model files."""
    names = {"round", "int", "floor", "ceil"}
    attributes = {"round", "rint", "floor", "ceil", "astype"}
    rows = []
    for relative in MODEL_FILES:
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):  # noqa: N802
                function_stack.append(node.name)
                self.generic_visit(node)
                function_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):  # noqa: N802
                label = None
                if isinstance(node.func, ast.Name) and node.func.id in names:
                    label = node.func.id
                elif isinstance(node.func, ast.Attribute) and node.func.attr in attributes:
                    label = node.func.attr
                if label is not None:
                    rows.append(
                        {
                            "module": relative,
                            "function": function_stack[-1] if function_stack else "<module>",
                            "source_line": node.lineno,
                            "syntax": label,
                            "source": ast.get_source_segment(source, node) or "",
                        }
                    )
                self.generic_visit(node)

        Visitor().visit(tree)
    covered = {
        (row["module"], row["function"].split(".")[-1]): row["site_id"]
        for row in INVENTORY
    }
    observed = {(row["module"], row["function"]) for row in rows}
    untriaged = observed - set(covered) - set(NON_ALLOCATION_FUNCTIONS)
    if untriaged:
        raise AssertionError(f"untriaged rounding syntax functions: {sorted(untriaged)}")
    for row in rows:
        key = (row["module"], row["function"])
        if key in covered:
            row["disposition"] = f"allocation inventory: {covered[key]}"
        else:
            row["disposition"] = f"not a distinct allocation site: {NON_ALLOCATION_FUNCTIONS[key]}"
    return rows


# ==========================================================================
# A-12 REVIEW ADDITION (2026-09-10) -- call-site granularity
# ==========================================================================
#
# `syntax_candidates()` above disposes candidates by `(module, function)`.  That
# is the right granularity for the INVENTORY -- an allocation contract is a
# property of a function, not of a line -- but it is the wrong granularity for a
# REGRESSION GUARD: 54 of the audited functions hold between 2 and 12 rounding
# calls each (`migration.apply` 12, `run_v3._allocate_fixed_events` 10,
# `transmission_corrected._deterministic_allocate_expected_infections` 8), so a
# NEW cellwise `np.round(pop * p)` added inside an already-triaged function
# inherits that function's disposition and passes silently.  That is precisely
# the shape in which ENG-01 was introduced.
#
# The fix below leaves the inventory and `syntax_candidates()` untouched and adds
# a frozen fingerprint of every call site observed at audit time.  A new call in
# an already-triaged function is then a test failure naming the call, not a
# silent inheritance.  Fingerprints deliberately exclude line numbers, so moving
# or reformatting code does not trip the guard; only a genuinely new call does.

CALL_FINGERPRINT_BASELINE = ROOT / "tools" / "a12_call_fingerprints.json"


def _normalise_source(text: str) -> str:
    """Strip ALL whitespace so formatting changes do not read as new call sites.

    Python expression text is whitespace-insensitive outside string literals, so
    removing every space makes `int( n * p )` and `int(n*p)` the same fingerprint --
    which is what we want, since a reformat is not a new allocation decision.

    The one theoretical collision is two calls differing only by whitespace inside a
    string literal (`d["a b"]` vs `d["ab"]`). That would cause a MISSED detection, never
    a false alarm, and no rounding call in these modules indexes by a spaced literal.
    """
    return "".join((text or "").split())


def call_site_fingerprints() -> collections.Counter:
    """Multiset of (module, function, normalised source) over every rounding call."""
    return collections.Counter(
        (row["module"], row["function"], _normalise_source(row["source"]))
        for row in syntax_candidates()
    )


def load_call_fingerprint_baseline() -> collections.Counter:
    with CALL_FINGERPRINT_BASELINE.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return collections.Counter(
        {(row["module"], row["function"], row["source"]): int(row["count"]) for row in payload["fingerprints"]}
    )


def write_call_fingerprint_baseline(path: Path | None = None) -> Path:
    """Freeze the current call sites.  Run ONLY when a change has been triaged."""
    path = path or CALL_FINGERPRINT_BASELINE
    counts = call_site_fingerprints()
    payload = {
        "generated_by": "tools/run_a12_deterministic_allocation_audit.py (A-12 review addition)",
        "purpose": (
            "Frozen call-site fingerprint. A new rounding call inside an already-triaged "
            "function would otherwise inherit that function's disposition silently. "
            "Regenerate ONLY after classifying the new call in INVENTORY or "
            "NON_ALLOCATION_FUNCTIONS."
        ),
        "n_call_sites": int(sum(counts.values())),
        "n_distinct": len(counts),
        "fingerprints": [
            {"module": module, "function": function, "source": source, "count": count}
            for (module, function, source), count in sorted(counts.items())
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def diff_call_sites() -> dict:
    """Call sites added since the baseline, and those removed.

    Additions are the dangerous direction: a new rounding call in a triaged
    function is classified by nobody.  Removals are reported too, because an
    inventory row describing code that no longer exists is stale evidence.
    """
    current = call_site_fingerprints()
    baseline = load_call_fingerprint_baseline()
    added = current - baseline
    removed = baseline - current
    return {
        "added": [
            {"module": m, "function": f, "source": s, "count": n}
            for (m, f, s), n in sorted(added.items())
        ],
        "removed": [
            {"module": m, "function": f, "source": s, "count": n}
            for (m, f, s), n in sorted(removed.items())
        ],
    }


def person_years(result, *, discounted: bool) -> float:
    return math.fsum(
        float(record["total_population"])
        * float(record["step_fraction"])
        * (float(record["discount_factor_health"]) if discounted else 1.0)
        for record in result.timestep_records
    )


def base_case_path_facts(config: dict) -> dict:
    """Extract the flags that determine whether the cellwise paths can run."""
    pop = config["population"]
    total_size = int(extract_value(pop["total_size"]))
    age_sex = extract_value(pop["age_sex_distribution"])
    weight_total = sum(
        float(values.get("female", 0.0)) + float(values.get("male", 0.0))
        for values in age_sex.values()
    )
    initialized = sum(
        int(float(values.get(sex, 0.0)) / weight_total * total_size)
        for values in age_sex.values()
        for sex in ("female", "male")
    )
    demography = config["demography"]
    pop_adjustments = demography.get("population_adjustments", {})
    return {
        "configured_initial_population": total_size,
        "cellwise_initialized_population": initialized,
        "initial_population_shortfall_people": total_size - initialized,
        "initial_hiv_prevalence": float(
            extract_value(pop.get("initial_hiv_prevalence", {"value": 0.0}))
        ),
        "aging_method": str(
            extract_value(demography.get("aging_method", {"value": "fractional"}))
        ),
        "population_adjustments_enabled": bool(
            extract_value(pop_adjustments.get("enabled", {"value": False}))
        ),
        "demography_mode": str(extract_value(demography["mode"])),
        "life_table_enabled": bool(extract_value(demography["life_table"]["enabled"])),
        "arm_fork_contract": "both evaluation arms restore independent copies of one 2025 frozen snapshot",
    }


def summarize_base_case(scenario: dict, results: dict) -> tuple[list[dict], dict]:
    rows = []
    for arm_name in scenario["arm_order"]:
        result = results[arm_name]
        rows.append(
            {
                "arm": arm_name,
                "total_qalys_disc": float(result.total_qalys_disc),
                "cumul_new_infections": int(result.cumul_new_infections),
                "discounted_person_years": person_years(result, discounted=True),
                "undiscounted_person_years": person_years(result, discounted=False),
                "cumul_background_deaths": int(sum(float(r["background_deaths"]) for r in result.simulation_records)),
                "cumul_hiv_deaths": int(sum(float(r["hiv_deaths"]) for r in result.simulation_records)),
                "cumul_childhood_deaths": int(sum(float(r["childhood_deaths"]) for r in result.simulation_records)),
                "cumul_births": int(sum(float(r["births"]) for r in result.simulation_records)),
                "cumul_sexual_debuts": int(sum(float(r["sexual_debuts"]) for r in result.simulation_records)),
                "cumul_net_migration": int(sum(float(r["net_migration"]) for r in result.simulation_records)),
                "final_population": int(result.simulation_records[-1]["total_population"]),
            }
        )
    reference, intervention = (results[name] for name in scenario["arm_order"])
    delta = {
        "scenario": scenario["name"],
        "reference_arm": scenario["arm_order"][0],
        "intervention_arm": scenario["arm_order"][1],
        "delta_qaly": float(intervention.total_qalys_disc - reference.total_qalys_disc),
        "infections_averted": int(reference.cumul_new_infections - intervention.cumul_new_infections),
        "delta_discounted_person_years": person_years(intervention, discounted=True) - person_years(reference, discounted=True),
        "delta_undiscounted_person_years": person_years(intervention, discounted=False) - person_years(reference, discounted=False),
    }
    for column in (
        "background_deaths",
        "hiv_deaths",
        "childhood_deaths",
        "births",
        "sexual_debuts",
        "net_migration",
    ):
        delta[f"delta_{column}"] = int(
            sum(float(r[column]) for r in intervention.simulation_records)
            - sum(float(r[column]) for r in reference.simulation_records)
        )
    delta["delta_final_population"] = int(
        intervention.simulation_records[-1]["total_population"]
        - reference.simulation_records[-1]["total_population"]
    )
    return rows, delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_output = output_dir / "raw_runner"
    raw_output.mkdir()
    model_config = args.model_config.resolve()
    runtime_config = args.runtime_config.resolve()

    located_inventory = locate_inventory(INVENTORY)
    if {row["classification"] for row in located_inventory} - VALID_CLASSIFICATIONS:
        raise AssertionError("inventory contains an invalid classification")
    cellwise = [row for row in located_inventory if row["classification"] == "cellwise"]
    if any(row["between_arm_event_differential_people"] is None for row in cellwise):
        raise AssertionError("every cellwise site must have a quantified base-case differential")

    runner = MultiScenarioRunner(str(model_config), str(raw_output), str(runtime_config))
    runner.hta_config["analysis_scenarios"] = select_scenario(
        runner.hta_config["analysis_scenarios"], "base_case_context"
    )
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = True
    runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {"value": True}
    runner.base_config["run_control"]["seed"] = {"value": args.seed}
    path_facts = base_case_path_facts(runner.base_config)
    path_facts["adverse_events_mode"] = str(
        runner.hta_config.get("adverse_events", {}).get("mode", "prevalence")
    )
    path_facts["legacy_childhood_auto_initializer_called_by_production"] = False
    path_facts["legacy_msm_deterministic_role_split_called_by_production"] = False
    for row in cellwise:
        if row["site_id"] == "INIT-01":
            row["shared_or_dormant_aggregate_observation_people"] = -path_facts[
                "initial_population_shortfall_people"
            ]
        elif row["site_id"] == "INIT-04":
            row["shared_or_dormant_aggregate_observation_people"] = 0
        else:
            row["shared_or_dormant_aggregate_observation_people"] = None

    manifest = {
        "task": "A-12 deterministic-allocation audit",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "runtime_config": str(runtime_config),
        "scenario": "base_case_context",
        "seed": args.seed,
        "deterministic": True,
        "python": sys.version,
        "platform": platform.platform(),
        "sha256": {
            "model_config": sha256(model_config),
            "runtime_config": sha256(runtime_config),
            **{f"code/{name}": sha256(ROOT / name) for name in MODEL_FILES},
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    pd.DataFrame(located_inventory).to_csv(output_dir / "allocation_inventory.csv", index=False)
    pd.DataFrame(cellwise).to_csv(output_dir / "cellwise_site_materiality.csv", index=False)
    pd.DataFrame(syntax_candidates()).to_csv(output_dir / "syntax_candidates.csv", index=False)
    (output_dir / "base_case_path_facts.json").write_text(
        json.dumps(path_facts, indent=2) + "\n", encoding="utf-8"
    )

    started = time.time()
    log_path = output_dir / "runner_stdout.log"
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
            runner.run()

    scenario = runner.hta_config["analysis_scenarios"][0]
    arm_rows, delta = summarize_base_case(scenario, runner.results[scenario["name"]])
    pd.DataFrame(arm_rows).to_csv(output_dir / "base_case_arm_summary.csv", index=False)
    (output_dir / "base_case_incremental_summary.json").write_text(
        json.dumps(delta, indent=2) + "\n", encoding="utf-8"
    )

    manifest["elapsed_seconds"] = time.time() - started
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    artifact_names = (
        "allocation_inventory.csv",
        "cellwise_site_materiality.csv",
        "syntax_candidates.csv",
        "base_case_arm_summary.csv",
        "base_case_incremental_summary.json",
        "base_case_path_facts.json",
        "runner_stdout.log",
    )
    manifest["counts"] = {
        "inventory_sites": len(located_inventory),
        "cellwise_sites": len(cellwise),
        "syntax_candidates_reviewed": len(syntax_candidates()),
    }
    manifest["output_sha256"] = {
        name: sha256(output_dir / name) for name in artifact_names
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-12 audit written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
