"""
HIV Simulation V10 - Main runner script

V10 features:
- Pre-sexual childhood pool (children 0-14, outside transmission system)
- Sexual debut mechanism (age 15: orientation + activity assignment)
- Corrected two-step transmission formulation
- File outputs (CSV/Excel), terminal summaries, multi-run aggregation
- Optional fertility/life tables
- 20 age bins (65+ expanded), net migration, ConfigReader native V10 config support

Population flow:
  Birth -> Childhood (0-4, 5-9, 10-14) -> Debut (age 15)
       -> Orientation + Activity assignment -> HIV transmission system
"""

import sys
import os
from typing import Optional, Tuple

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_v3 import (
    TimeSystem, GroupSystem, ConfigValidator,
    PopulationState
)
from config_utils import extract_value
from config_reader import ConfigReader
from transmission_corrected import TransmissionEngine
from output_writer import OutputWriter
from demography import DemographyModule
from schedule_utils import ScheduleManager, TimeSchedule, validate_schedule_before_intro  # V5, V12.15
from migration import MigrationModule                                       # V9
import json
import numpy as np
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# V11.8.4: PrEP naming bridge
#
# The config uses two different conventions that must be kept in sync:
#   interventions keys  (lowercase): "oral_prep", "lenacapavir"
#   prep_schedules keys (CamelCase): "Oral_PrEP", "Lenacapavir"
#
# All downstream code (schedule_utils, economics, output_writer, record_outputs)
# expects the CamelCase form as the canonical prep STATE name stored in the
# population tensor and GroupSystem.prep_states.
#
# This module-level mapping is the single source of truth for the conversion.
# Any new PrEP intervention added to the config must be registered here.
# ---------------------------------------------------------------------------
_INTERVENTION_TO_PREP_STATE: dict = {
    "oral_prep":   "Oral_PrEP",
    "lenacapavir": "Lenacapavir",
}
# Note: _PREP_STATE_TO_INTERVENTION is intentionally NOT defined here.
# self.config["interventions"] is translated to CamelCase by ConfigReader.as_v6_dict(),
# so prep state names ("Oral_PrEP", "Lenacapavir") can be looked up directly — no reverse
# mapping is needed in _step_prep_transitions or record_outputs.


class SimulationEngineV3:
    """
    V10 Simulation Engine with childhood pool, sexual debut,
    corrected two-step transmission, and robust outputs.

    Key V10 design principles:
    - Children (0-14) exist outside the HIV transmission system
    - No behavioral attributes (orientation, activity) assigned before debut
    - Sexual debut at configurable age with probabilistic assignment
    - All debutants enter as Susceptible/Undiagnosed/NoART/NoPrEP
    """

    def __init__(self, config_path_or_dict, seed: int = None, initial_snapshot=None):
        # Load config — accepts a file path (str) or a pre-loaded dict.
        # Dict input is used by the PSA runner to avoid N temp file writes per draw.
        # V11.9.6: BLOCKER-A fix.
        if isinstance(config_path_or_dict, dict):
            raw_config = config_path_or_dict
        elif isinstance(config_path_or_dict, str):
            with open(config_path_or_dict, 'r') as f:
                raw_config = json.load(f)
        else:
            raise TypeError(
                f"SimulationEngineV3 expects a file path (str) or a config dict, "
                f"got {type(config_path_or_dict).__name__}"
            )

        # Native config reading via ConfigReader (handles both V6 and V10)
        self.reader = ConfigReader(raw_config)
        detected_version = self.reader.get_config_version()
        fmt = "V10" if self.reader._is_v10 else "V6"
        print(f"  [ConfigReader] {fmt} config loaded (version={detected_version})")

        # Build V6-compatible view for modules that still read self.config
        self.config = self.reader.as_v6_dict()

        # Extract seed
        if seed is None:
            seed = extract_value(self.config["simulation_controls"].get("seed", {"value": 42}))
        self.seed = seed
        self.rng = np.random.RandomState(seed)

        # Initialize time system
        self.time = TimeSystem(self.config["time"])

        # Initialize group system
        self.groups = GroupSystem(self.config["groups"])

        # Add PrEP interventions (V11.8.4: map config key → canonical CamelCase state name)
        for intervention_name, intervention_config in self.config["interventions"].items():
            if extract_value(intervention_config.get("enabled", {"value": True})):
                state_name = _INTERVENTION_TO_PREP_STATE.get(intervention_name, intervention_name)
                self.groups.add_prep_intervention(state_name)

        # V12.2: Register synthetic Lenacapavir_Waning state immediately after Lenacapavir.
        # This state captures the 1-quarter residual protection after lena dropout (PK: t1/2 ~10-12 wks).
        # Ordering guarantee: Waning is always the last prep state when lena is enabled,
        # so the snapshot padder (shape-agnostic, pads zeros at end) handles HTA arm
        # baseline→arm transitions correctly (pre-arm snapshot has lena disabled, n_prep<4).
        if "Lenacapavir" in self.groups.prep_states:
            self.groups.add_prep_intervention("Lenacapavir_Waning")

        self.groups.finalize_prep_states()

        # V12.2: Guard — waning depth=1 is only valid for quarterly runs.
        if "Lenacapavir_Waning" in self.groups.prep_to_idx and self.time.years_per_step != 0.25:
            raise NotImplementedError(
                f"Lenacapavir_Waning (V12.2) uses a hardcoded depth of 1 timestep, "
                f"which is only valid for quarterly runs (step_fraction=0.25). "
                f"Got years_per_step={self.time.years_per_step:.4f}. "
                f"Generalization to other timestep sizes is a post-thesis enhancement."
            )

        # Validate configuration
        errors = ConfigValidator.validate(self.config, self.groups)
        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

        print(f"  Config validated")
        print(f"  {self.time}")
        print(f"  {self.groups}")

        # Initialize V4 modules (demography first to have childhood pool available)
        self.output_writer = OutputWriter(self.config.get("output", {}), self.time)
        self.demography = DemographyModule(self.config, self.groups, self.time)

        # Initialize population state (sexually active + childhood)
        self.state = PopulationState(self.groups, seed=seed)

        # Determine population initialization method — required field, no default.
        pop_config = self.config["population"]
        if "initialization_method" not in pop_config:
            raise ValueError(
                "population.initialization_method is required. "
                "Set to 'relative_weights' for standard initialization."
            )
        init_method = extract_value(pop_config["initialization_method"])
        if init_method == "relative_weights":
            self.state.initialize_from_config(pop_config, childhood_pool=self.demography.childhood)
        else:
            raise ValueError(
                f"Unknown population.initialization_method: '{init_method}'. "
                f"Only 'relative_weights' is supported."
            )

        # Initialize corrected transmission engine
        self.transmission_engine = TransmissionEngine(self.config, self.groups, self.time)

        # V5: Initialize schedule manager (backward compatible with V4 configs)
        self.schedule_manager = None
        if "art_schedule" in self.config or "prep_schedules" in self.config:
            self.schedule_manager = ScheduleManager(self.config)
            self.schedule_manager.validate_sanity()
            print(f"  V5: Time-based ART/PrEP schedules enabled")
            if self.schedule_manager.get_art_introduction_year():
                print(f"    ART introduction: {self.schedule_manager.get_art_introduction_year()}")
            for prep_type in ["Oral_PrEP", "Lenacapavir"]:
                intro_year = self.schedule_manager.get_prep_introduction_year(prep_type)
                if intro_year:
                    print(f"    {prep_type} introduction: {intro_year}")

        # V12.15: Diagnosis year-band schedule (optional; falls back to flat annual_rate)
        self.diagnosis_schedule = self._build_diagnosis_schedule()
        if self.diagnosis_schedule is not None:
            print(f"  V12.15: Diagnosis schedule ENABLED "
                  f"(interp={self.diagnosis_schedule.interpolation}, "
                  f"first_year={self.diagnosis_schedule.get_first_year()})")

        # V13.10: Optional MSM-relative diagnosis multiplier (diagnostic pilot; disabled by default)
        self.msm_relative_diagnosis_schedule = self._build_msm_relative_diagnosis_schedule()
        if self.msm_relative_diagnosis_schedule is not None:
            print(f"  V13.10: MSM relative diagnosis schedule ENABLED "
                  f"(interp={self.msm_relative_diagnosis_schedule.interpolation}, "
                  f"first_year={self.msm_relative_diagnosis_schedule.get_first_year()})")

        # V13.6 diagnostic: optional calendar-era ART mortality effect schedule.
        # Falls back to the existing constant art_mortality_reduction when absent.
        self.art_mortality_reduction_schedule = self._build_art_mortality_reduction_schedule()
        if self.art_mortality_reduction_schedule is not None:
            print(f"  V13.6: ART mortality reduction schedule ENABLED "
                  f"(interp={self.art_mortality_reduction_schedule.interpolation}, "
                  f"first_year={self.art_mortality_reduction_schedule.get_first_year()})")

        # V9: Net migration module (safe default: disabled if key absent)
        self.migration = MigrationModule(self.config, self.groups, self.demography)
        if self.migration.enabled:
            print(f"  Migration: ENABLED "
                  f"(mode={self.migration.quantity_mode}, "
                  f"profile={self.migration.profile_mode})")

        # Deterministic mode
        self.deterministic = extract_value(
            self.config["simulation_controls"].get("deterministic_mode", {"value": False})
        )

        # V13.16 diagnostic-only event ordering variants. Missing/empty/current
        # preserves production behavior exactly.
        raw_run_control = self.reader.raw.get("run_control", {})
        self._diagnostic_event_order_variant = str(extract_value(
            raw_run_control.get("diagnostic_event_order_variant", {"value": "current"})
        ) or "current")
        allowed_event_order_variants = {
            "current",
            "mortality_before_diagnosis_art",
            "art_lag_after_diagnosis",
            "mortality_before_progression",
        }
        if self._diagnostic_event_order_variant not in allowed_event_order_variants:
            raise ValueError(
                "run_control.diagnostic_event_order_variant must be one of "
                f"{sorted(allowed_event_order_variants)}, got "
                f"{self._diagnostic_event_order_variant!r}"
            )
        if self._diagnostic_event_order_variant != "current":
            print(
                "  V13.16 diagnostic event order variant ENABLED: "
                f"{self._diagnostic_event_order_variant}"
            )

        # Storage for outputs
        self.outputs = {
            'step': [],
            'year': [],
            'new_infections': [],
            'prevalence': [],
            'incidence_rate': [],  # V11.4: annualized, per susceptible per year
            'hiv_deaths': [],
            'aids_deaths': [],
            'new_aids_cases': [],
            'new_diagnoses': [],
            'new_diagnoses_msm': [],
            'art_initiations': [],
            'art_initiations_acute': [],
            'art_initiations_chronic': [],
            'art_initiations_aids': [],
            'on_art_stock': [],
            'adult_hiv_pos': [],
            'plhiv_female_15plus': [],
            'plhiv_male_15plus': [],
            'plhiv_child_0_14': [],
            'background_deaths': [],
            'childhood_deaths': [],
            'total_population': [],
            'sexually_active_population': [],
            'childhood_population': [],
            'births': [],
            'sexual_debuts': [],
            'continuous_debuts': [],  # GUARDRAIL: separate tracking for observability
            'flush_debuts': [],       # GUARDRAIL: separate tracking for observability
            'net_migration': [],      # V9: actual net migration each step
            # V13.1: documented pre-arrival HIV-positive migrant entries
            'migrant_hiv_entries': [],
            'migrant_hiv_on_art_entries': [],
            'migrant_hiv_msm_entries': [],
            'migrant_hiv_non_msm_entries': [],  # V13.10 diag: hetero_male + female
            'migrant_hiv_hetero_male_entries': [],
            'migrant_hiv_female_entries': [],
            'migrant_hiv_requested_entries': [],
            'migrant_hiv_capped_entries': [],
            'migrant_hiv_cap_shortfall': [],
            'prep_coverage_Oral_PrEP': [],
            'prep_coverage_Lenacapavir': [],
            # V11.8.0: MSM population sub-counts
            'msm_total': [],
            'msm_susceptible': [],
            'msm_eligible_prep': [],
            'msm_high_risk': [],
            'msm_high_risk_susceptible': [],
            'msm_high_risk_on_prep': [],
            'msm_hiv_pos': [],
            'non_msm_hiv_pos': [],           # V13.10 diag: total_hiv_pos - msm_hiv_pos
            'msm_undiagnosed_hiv_pos': [],   # V13.10 diag: MSM HIV+ and undiagnosed
            'non_msm_undiagnosed_hiv_pos': [],  # V13.10 diag: non-MSM HIV+ and undiagnosed
            'msm_on_oral': [],
            'msm_on_lena': [],
            # V11.8.0: PrEP flow counters (this timestep)
            'prep_oral_initiations': [],
            'prep_lena_initiations': [],
            'prep_oral_dropouts': [],
            'prep_lena_dropouts': [],
            'prep_oral_corridor_additions': [],
            'prep_lena_corridor_additions': [],
            'prep_oral_corridor_removals': [],
            'prep_lena_corridor_removals': [],
            'prep_oral_target_additions': [],
            'prep_lena_target_additions': [],
            'prep_lena_switches': [],
            'prep_lena_switch_shortfall': [],
            # V11.8.0: Stratified new infections
            'new_infections_msm': [],
            'new_infections_non_msm': [],    # V13.10 diag: total_new_infections - msm
            'new_infections_msm_on_prep': [],
            # V13.16 diagnostic event-order audit columns
            'diagnostic_event_order_variant': [],
            'aids_stock_start': [],
            'aids_off_art_start': [],
            'aids_on_art_start': [],
            'aids_undiagnosed_start': [],
            'aids_diagnosed_start': [],
            'aids_stock_pre_mortality': [],
            'aids_off_art_pre_mortality': [],
            'aids_on_art_pre_mortality': [],
            'aids_undiagnosed_pre_mortality': [],
            'aids_diagnosed_pre_mortality': [],
            'aids_art_initiations': [],
            'same_step_aids_art_rescue_proxy': [],
        }

        print(f"  V4 modules initialized")
        if self.demography.fertility_enabled:
            print(f"  Fertility: ENABLED (mode={self.demography.mode})")
        if self.demography.life_table_enabled:
            print(f"  Life table: ENABLED")
        if self.demography.pop_adj_enabled:
            print(f"  Population adjustments: ENABLED")
        print(f"  Childhood pool: ENABLED ({self.demography.childhood.n_bins} age bins)")

        # V4.1+ sexual debut (optional for backward compatibility)
        if 'sexual_debut' in self.config:
            print(f"  Sexual debut: age {extract_value(self.config['sexual_debut']['debut_age'])}, "
                  f"p_msm={self.demography.p_msm}")

        # V11.8.0: Per-step PrEP flow counters (reset each step in _step_prep_transitions)
        self._prep_step_initiations: dict = {}   # prep_name -> int
        self._prep_step_dropouts: dict = {}      # prep_name -> int
        self._prep_step_corridor_additions: dict = {}  # prep_name -> int
        self._prep_step_corridor_removals: dict = {}    # prep_name -> int
        self._prep_step_target_additions: dict = {}      # prep_name -> int
        self._prep_step_switches: dict = {}              # prep_name -> int
        self._prep_step_switch_shortfall: dict = {}      # prep_name -> int
        # V12.18: Per-step gross ART initiations by HIV stage for disease-cost routing.
        self._art_step_initiations_by_stage: dict = {}
        # V11.8.0: Last infections array (shape = population tensor) for stratified tracking
        self._last_infections_array = None

        # HTA hooks — set by MultiScenarioRunner, not constructed here
        self._snapshot_manager = None   # Phase 1: baseline freezing
        self._economics = None          # Phase 2: per-step QALY/cost accumulation

        # V12.4: Epidemic seeding — time-gated HIV introduction event
        try:
            _pop_cfg = self.config.get("population", {})
            _ep_raw = _pop_cfg.get("epidemic_seed", {})
            _ep = extract_value(_ep_raw) if _ep_raw else {}
        except Exception:
            _ep = {}
        if isinstance(_ep, dict) and _ep:
            self._epidemic_seed_enabled  = bool(extract_value(_ep.get("enabled",  {"value": False})))
            self._epidemic_seed_year     = float(extract_value(_ep.get("seed_year", {"value": 9999.0})))
            self._epidemic_seed_prev     = float(extract_value(_ep.get("seed_prevalence", {"value": 0.0})))
            self._epidemic_seed_msm_frac = float(extract_value(_ep.get("msm_fraction", {"value": 1.0})))
            self._epidemic_seed_stage    = str(extract_value(_ep.get("seed_stage", {"value": "Acute"})))
            self._epidemic_seed_stage_distribution = self._parse_epidemic_seed_stage_distribution(_ep)
            # V13.11: orientation-specific seed stage distributions (opt-in)
            self._epidemic_seed_stage_dist_msm     = self._parse_orientation_seed_stage_dist(_ep, "MSM")
            self._epidemic_seed_stage_dist_non_msm = self._parse_orientation_seed_stage_dist(_ep, "non_MSM")
        else:
            self._epidemic_seed_enabled  = False
            self._epidemic_seed_year     = 9999.0
            self._epidemic_seed_prev     = 0.0
            self._epidemic_seed_msm_frac = 1.0
            self._epidemic_seed_stage    = "Acute"
            self._epidemic_seed_stage_distribution = None
            self._epidemic_seed_stage_dist_msm     = None
            self._epidemic_seed_stage_dist_non_msm = None
        self._epidemic_seed_fired = False

        # HTA: Restore from snapshot if forking from a frozen baseline
        self._restored_from_snapshot = False
        if initial_snapshot is not None:
            snap_pop = initial_snapshot.population

            # Reconcile PrEP dimension: the baseline may run without any PrEP
            # interventions (n_prep=1, "None" only), while arm engines enable one
            # intervention (n_prep=2). Zero-pad the snapshot to match arm shape —
            # semantically correct since nobody is on PrEP at the freeze point.
            # Also handles the reverse (baseline had more PrEP states than arm).
            arm_n_prep = self.state.population.shape[6]
            snap_n_prep = snap_pop.shape[6]
            if snap_n_prep < arm_n_prep:
                pad = [(0, 0)] * 6 + [(0, arm_n_prep - snap_n_prep)]
                snap_pop = np.pad(snap_pop, pad, mode='constant', constant_values=0)
            elif snap_n_prep > arm_n_prep:
                snap_pop = snap_pop[..., :arm_n_prep]

            self.state.population = snap_pop.copy()
            self.demography.childhood.population = initial_snapshot.childhood_population.copy()
            if initial_snapshot.rng_state is not None:
                self.rng.set_state(initial_snapshot.rng_state)
            self._restored_from_snapshot = True
            # Mark seed as fired if snapshot is at or past the seed year.
            # Guards against spurious re-seeding when HTA arm engines are
            # restored from a post-seed-year snapshot. If seed_year is
            # configured beyond the freeze point (unusual), the flag stays
            # False so the seed still fires correctly during the arm run.
            if (self._epidemic_seed_enabled
                    and initial_snapshot.year >= self._epidemic_seed_year):
                self._epidemic_seed_fired = True
            print(f"\n  ✓ State restored from snapshot "
                  f"(year={initial_snapshot.year}, "
                  f"pop={initial_snapshot.total_population:,})"
                  f"{' [prep dim expanded]' if snap_n_prep < arm_n_prep else ''}")

        self._initialize_adult_residence_phases()
        self.demography.childhood.initialize_residence_phases(self.time)

        # Print initial state diagnostic
        self._print_initial_state()

    def _print_initial_state(self):
        """Print diagnostic information about initial population state."""
        total_active = self.state.get_total_population()
        hiv_pos = self.state.get_hiv_positive()
        susc = self.state.get_susceptible()
        childhood = self.demography.childhood.get_total()
        child_f, child_m = self.demography.childhood.get_by_sex()

        print(f"\n  Initial state:")
        print(f"    Sexually active: {total_active:,}")
        print(f"    HIV+ (seeded):   {hiv_pos:,} ({100*hiv_pos/total_active:.2f}%)")
        print(f"    Susceptible:     {susc:,}")
        print(f"    Childhood pool:  {childhood:,} (F:{child_f:,} M:{child_m:,})")
        print(f"    Total (all):     {total_active + childhood:,}")

        # Check stage distribution of HIV+
        for stage_name in ["Acute", "Chronic", "AIDS"]:
            stage_idx = self.groups.hiv_to_idx[stage_name]
            stage_count = np.sum(self.state.population[:, :, :, stage_idx, :, :, :])
            if stage_count > 0:
                print(f"    {stage_name}: {stage_count:,}")

        # ART status
        on_art = np.sum(self.state.population[:, :, :, 1:, :, 1, :])
        print(f"    On ART: {on_art:,}")

    def _draw_events(self, n: np.ndarray, p: float) -> np.ndarray:
        """
        Draw number of events from binomial distribution.
        In deterministic mode, allocate the aggregate expected event count
        across cells while preserving integer state tensors.
        """
        if self.deterministic:
            return self._deterministic_allocate_events(n, p)
        else:
            return self.rng.binomial(n, p)

    @staticmethod
    def _deterministic_allocate_events(n: np.ndarray, p: float) -> np.ndarray:
        """
        Deterministic integer approximation to a binomial event draw.

        Cellwise rounding can erase rare events when expected counts are spread
        across many tensor cells. This allocator preserves the rounded aggregate
        expected total, then assigns residual events to cells with the largest
        fractional expected counts.
        """
        p = float(p)
        if not np.isfinite(p) or p < 0.0 or p > 1.0:
            raise ValueError(
                f"Event probability must be finite and within [0, 1], got {p!r}"
            )

        n_arr = np.asarray(n)
        if n_arr.size == 0:
            return np.zeros_like(n_arr, dtype=np.int64)
        if p == 0.0:
            return np.zeros_like(n_arr, dtype=np.int64)
        if p == 1.0:
            return n_arr.astype(np.int64, copy=True)

        expected = n_arr.astype(float) * p
        base = np.floor(expected).astype(np.int64)
        base = np.minimum(base, n_arr.astype(np.int64, copy=False))
        target = min(int(np.round(expected.sum())), int(n_arr.sum()))
        remainder = target - int(base.sum())

        if remainder <= 0:
            return base

        events = base.copy()
        fractional = (expected - base).ravel()
        capacity = (n_arr.astype(np.int64, copy=False) - base).ravel()
        eligible = np.flatnonzero(capacity > 0)
        if eligible.size == 0:
            return events

        order = eligible[np.argsort(-fractional[eligible], kind="stable")]
        take = min(remainder, order.size)
        np.put(events, order[:take], events.ravel()[order[:take]] + 1)
        return events

    @staticmethod
    def _allocate_fixed_events(
        capacity: np.ndarray,
        target: int,
        weights: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Allocate an exact integer number of events across cells without exceeding capacity.

        Used for programme stock guardrails where the target is an observed headcount
        rather than a probability draw. Allocation is deterministic by construction so
        historical guardrails are reproducible in both deterministic and stochastic runs.
        """
        capacity_arr = np.asarray(capacity).astype(np.int64, copy=False)
        events = np.zeros_like(capacity_arr, dtype=np.int64)
        if capacity_arr.size == 0:
            return events

        max_events = int(capacity_arr.sum())
        n_events = min(max(int(round(target)), 0), max_events)
        if n_events == 0:
            return events
        if n_events == max_events:
            return capacity_arr.astype(np.int64, copy=True)

        if weights is None:
            weight_arr = capacity_arr.astype(float)
        else:
            weight_arr = np.asarray(weights, dtype=float)
            if weight_arr.shape != capacity_arr.shape:
                raise ValueError(
                    f"Fixed-event allocation weights shape {weight_arr.shape} "
                    f"does not match capacity shape {capacity_arr.shape}"
                )
            weight_arr = np.where(np.isfinite(weight_arr) & (weight_arr > 0), weight_arr, 0.0)
            weight_arr = weight_arr * capacity_arr

        if float(weight_arr.sum()) <= 0.0:
            weight_arr = capacity_arr.astype(float)

        expected = weight_arr / float(weight_arr.sum()) * n_events
        base = np.floor(expected).astype(np.int64)
        base = np.minimum(base, capacity_arr)
        remainder = n_events - int(base.sum())
        events = base.copy()

        if remainder <= 0:
            return events

        fractional = (expected - base).ravel()
        residual_capacity = (capacity_arr - base).ravel()
        eligible = np.flatnonzero(residual_capacity > 0)
        if eligible.size == 0:
            return events

        order = eligible[np.argsort(-fractional[eligible], kind="stable")]
        take = min(remainder, order.size)
        np.put(events, order[:take], events.ravel()[order[:take]] + 1)
        return events

    def run(self, run_number: int = 1, start_step: int = 0,
            end_step: int = None) -> dict:
        """
        Run simulation for one realization.

        Args:
            run_number: Run index (for output labeling).
            start_step: First step to execute (default 0). Used by HTA
                        scenario runner to start from the baseline freeze point.
            end_step: Last step (exclusive). Defaults to self.time.n_steps.
                      Used by HTA to limit evaluation horizon.
        """
        if end_step is None:
            end_step = self.time.n_steps
        end_step = min(end_step, self.time.n_steps)

        total_steps = end_step - start_step
        print(f"\nRunning simulation (run {run_number}, "
              f"steps {start_step}→{end_step})...")

        for step in range(start_step, end_step):
            self.step_simulation(step)

            # Validate PrEP mass balance after each timestep
            self.validate_prep_mass_balance(step)

            # PATCH 3: Validate ART mass balance after each timestep
            self.validate_art_mass_balance(step)

            completed = step - start_step + 1
            if completed % 10 == 0:
                print(f"  Step {completed}/{total_steps}", end='\r')

        print(f"  Completed {total_steps} timesteps.    ")

        # Convert to DataFrame
        df = pd.DataFrame(self.outputs)

        # Write outputs
        if self.output_writer.enabled:
            self.output_writer.write_run_output(df, run_number)

            # Write detailed state counts if enabled
            if self.output_writer.write_state_counts_enabled:
                # Get final state and write detailed breakdown
                final_step = self.time.n_steps - 1
                final_year = self.time.start_year + (final_step * self.time.years_per_step)

                # Extract main population (collapse diagnosis, ART, PrEP dimensions)
                main_population = np.sum(self.state.population, axis=(4, 5, 6))

                # PrEP status: sum over diagnosis and ART dimensions ONLY
                # Result shape: (age, orient, activity, hiv, prep)
                # This keeps PrEP at the same grain as HIV stage
                prep_population = np.sum(self.state.population, axis=(4, 5))

                # PATCH 3: ART status: sum over diagnosis and PrEP dimensions ONLY
                # Result shape: (age, orient, activity, hiv, art)
                # This keeps ART at the same grain as HIV stage
                art_population = np.sum(self.state.population, axis=(4, 6))

                self.output_writer.write_state_counts(
                    main_population,
                    self.groups,
                    final_step,
                    final_year,
                    run_number,
                    prep_status=prep_population,
                    art_status=art_population
                )

        # Print summary
        prep_coverage = {
            'Oral_PrEP': self.outputs['prep_coverage_Oral_PrEP'],
            'Lenacapavir': self.outputs['prep_coverage_Lenacapavir']
        }
        self.output_writer.print_terminal_summary(df, run_number, prep_coverage)

        return {
            'summary': df,
            'final_state': self.state.population.copy(),
            'childhood_state': self.demography.childhood.population.copy(),
            'seed': self.seed
        }

    def step_simulation(self, step: int):
        """
        Execute one timestep - V4 ordering with childhood pool and sexual debut.

        Order:
        1. Population adjustments (calibration)
        2. Contacts + mixing
        3. Transmission (corrected two-step)
        4. Disease progression
        5. Diagnosis
        6. ART transitions
        7. PrEP transitions
        8. Births -> childhood pool (NOT main tensor)
        9. Childhood mortality
        10. HIV mortality + adult background mortality
        11. Aging (both childhood and adult) + sexual debut
        12. Record outputs
        """
        current_year = self.time.start_year + step * self.time.years_per_step

        # PATCH 2: Capture total population at start of timestep for mass balance
        prev_active = self.state.get_total_population()
        prev_childhood = self.demography.childhood.get_total()
        prev_total = prev_active + prev_childhood
        aids_start_counts = self._aids_state_counts()

        # Validate before step
        self.state.validate()
        self.demography.childhood.validate()

        # HTA Phase 1: Baseline freeze captures the state at the start of the
        # freeze timestep. For freeze_year=2025, arms restart before any 2025
        # transitions, so policy-year mechanics are not applied twice.
        if self._snapshot_manager is not None:
            if self._snapshot_manager.should_freeze(current_year, step):
                self._snapshot_manager.freeze(
                    self.state.population,
                    self.demography.childhood.population,
                    step, current_year, self.rng
                )

        # --- 1. Population adjustments ---
        # Track adjustment change for mass balance accounting
        adjustment_change = 0
        if self.demography.pop_adj_enabled:
            active_before_adjustment = self.state.get_total_population()
            self.state.population = self.demography.apply_population_adjustments(
                self.state.population, current_year
            )
            active_after_adjustment = self.state.get_total_population()
            adjustment_change = active_after_adjustment - active_before_adjustment

        # V12.4: Epidemic seeding — one-time introduction event
        seed_change = 0
        if (self._epidemic_seed_enabled
                and not self._epidemic_seed_fired
                and current_year >= self._epidemic_seed_year):
            seed_change = self._apply_epidemic_seed(current_year)
            self._epidemic_seed_fired = True

        # --- 2. Contacts via mixing matrices ---
        contacts = self._step_contacts_and_mixing()

        # B4 (V11.4): Capture susceptible count BEFORE transmission for incidence denominator.
        # Using the post-step count was wrong: the denominator was shaped by mortality,
        # aging, and migration that happened AFTER the infections were drawn.
        susceptible_pre_tx = self.state.get_susceptible()

        # --- 3. Transmission (corrected two-step) ---
        new_infections, infections_array = self.transmission_engine.calculate_transmission(
            self.state.population, contacts, self.rng, self.deterministic
        )
        self.state.population = self.transmission_engine.apply_infections_to_population(
            self.state.population, infections_array
        )
        # V11.8.0: Store infections array for stratified MSM/PrEP tracking in record_outputs
        self._last_infections_array = infections_array

        # --- 4-6 and 10a. HIV event ordering ---
        # Default/current behavior is unchanged. V13.16 variants are
        # diagnostic-only and gated by run_control.diagnostic_event_order_variant.
        hiv_deaths = 0
        aids_deaths = 0
        aids_pre_mortality_counts = None
        variant = self._diagnostic_event_order_variant

        if variant == "current":
            # Current production order:
            # progression -> diagnosis -> ART -> HIV mortality later in step 10.
            _progression = self._step_disease_progression()
            diagnosis_result = self._step_diagnosis(current_year)
            art_result = self._step_art_transitions(current_year)
        elif variant == "mortality_before_diagnosis_art":
            # Diagnostic: progression -> HIV mortality -> diagnosis -> ART.
            # Tests whether same-step diagnosis/ART rescue suppresses AIDS deaths.
            _progression = self._step_disease_progression()
            aids_pre_mortality_counts = self._aids_state_counts()
            _mortality_result = self._step_hiv_mortality(current_year)
            hiv_deaths = _mortality_result["total"]
            aids_deaths = _mortality_result["aids"]
            diagnosis_result = self._step_diagnosis(current_year)
            art_result = self._step_art_transitions(current_year)
        elif variant == "art_lag_after_diagnosis":
            # Diagnostic: progression -> diagnosis -> HIV mortality -> ART.
            # Allows same-step diagnosis but blocks same-step ART mortality benefit.
            _progression = self._step_disease_progression()
            diagnosis_result = self._step_diagnosis(current_year)
            aids_pre_mortality_counts = self._aids_state_counts()
            _mortality_result = self._step_hiv_mortality(current_year)
            hiv_deaths = _mortality_result["total"]
            aids_deaths = _mortality_result["aids"]
            art_result = self._step_art_transitions(current_year)
        elif variant == "mortality_before_progression":
            # Diagnostic lower-bound sensitivity: HIV mortality -> progression
            # -> diagnosis -> ART.
            aids_pre_mortality_counts = self._aids_state_counts()
            _mortality_result = self._step_hiv_mortality(current_year)
            hiv_deaths = _mortality_result["total"]
            aids_deaths = _mortality_result["aids"]
            _progression = self._step_disease_progression()
            diagnosis_result = self._step_diagnosis(current_year)
            art_result = self._step_art_transitions(current_year)
        else:
            raise AssertionError(f"Unhandled diagnostic event order variant: {variant}")

        new_diagnoses = diagnosis_result["total"]
        new_diagnoses_msm = diagnosis_result["msm"]

        # --- 7. PrEP transitions (V5: time-scheduled) ---
        # V12.2: Fire waning→None FIRST so waning users (who received protection at step 3
        # transmission this step) move to None before new dropout/uptake is processed.
        self._step_lena_waning_transition()
        self._step_prep_transitions(current_year)

        # --- 8. Births -> childhood pool ---
        n_births = 0
        if self.demography.mode == "fertility_table" and self.demography.fertility_enabled:
            n_births = self.demography.calculate_births(
                self.state.population, current_year, self.rng, self.deterministic
            )
            self.demography.add_births_to_childhood(n_births, self.rng, self.deterministic)
        elif self.demography.mode == "fixed_net_addition":
            n_births = self.demography.calculate_fixed_births()
            self.demography.add_births_to_childhood(n_births, self.rng, self.deterministic)

        # --- 9. Childhood mortality ---
        childhood_deaths = self.demography.apply_childhood_mortality(
            self.rng, self.deterministic
        )

        # --- 10. HIV mortality + adult background mortality ---
        # V11.7.3: returns dict {"total": int, "aids": int}
        # "total" used for mass-balance and record_outputs; "aids" routed to economics
        if variant == "current":
            aids_pre_mortality_counts = self._aids_state_counts()
            _mortality_result = self._step_hiv_mortality(current_year)
            hiv_deaths  = _mortality_result["total"]
            aids_deaths = _mortality_result["aids"]
        bg_deaths_array = self.demography.calculate_background_mortality(
            self.state.population, current_year, self.rng, self.deterministic
        )
        self.state.population -= bg_deaths_array
        bg_deaths = np.sum(bg_deaths_array)

        # --- 10.5. Net migration (after deaths, before aging) ---
        # V9: Entrants are Susceptible/Undiagnosed/No-ART/No-PrEP.
        # Negative migration: proportional removal preserving HIV structure.
        # Returns ACTUAL integer net change for exact mass-balance accounting.
        pre_migration_population = self.state.population.copy()
        pre_migration_childhood = self.demography.childhood.population.copy()
        migration_change = self.migration.apply(
            self.state.population,
            self.demography.childhood,
            current_year,
            self.time,
            self.rng,
            self.deterministic,
        )
        migrant_hiv_stats = getattr(self.migration, "last_migrant_hiv_stats", {})
        self._register_positive_adult_residence_delta(pre_migration_population)
        self.demography.childhood.register_positive_residence_delta(pre_migration_childhood)

        # --- 11. Aging + sexual debut ---
        continuous_debuts, flush_debuts, total_debuts = self._step_aging_and_debut(step)

        # Validate after step
        self.state.validate()
        self.demography.childhood.validate()

        # PATCH 2: Validate global population mass balance
        # V9: migration_change is the ACTUAL net migration applied this step,
        # so the formula remains exact regardless of rounding in the module.
        self.validate_global_population_balance(
            step, prev_total, n_births, hiv_deaths, bg_deaths, childhood_deaths,
            adjustment_change, migration_change, seed_change  # V12.4
        )

        # --- 12. Record outputs ---
        self.record_outputs(
            step, current_year, new_infections, hiv_deaths,
            bg_deaths, childhood_deaths, n_births,
            continuous_debuts, flush_debuts, total_debuts,
            migration_change,
            migrant_hiv_stats=migrant_hiv_stats,
            susceptible_pre_tx=susceptible_pre_tx,
            aids_deaths=aids_deaths,
            new_aids_cases=_progression["new_aids"],
            new_diagnoses=new_diagnoses,
            new_diagnoses_msm=new_diagnoses_msm,
            art_initiations=art_result["initiations"],
            art_initiations_by_stage=art_result["initiations_by_stage"],
            aids_start_counts=aids_start_counts,
            aids_pre_mortality_counts=aids_pre_mortality_counts,
            event_order_variant=variant,
        )

        # --- HTA hooks (additive, no-op when not set) ---

        # Phase 2: Economics accumulation — per-step QALY/cost tracking
        if self._economics is not None:
            # V12.1 (BACKLOG-21): derive age-stratified new-infection vector from
            # the transmission array (shape [age, orient, activity, prep] → sum → (age,)).
            _new_inf_by_age = (
                self._last_infections_array.sum(axis=(1, 2, 3))
                if self._last_infections_array is not None
                else None
            )
            prep_initiations_by_arm = {
                "oral_prep": self._prep_step_initiations.get("Oral_PrEP", 0),
                "lenacapavir": self._prep_step_initiations.get("Lenacapavir", 0),
            }
            self._economics.accumulate(
                step, current_year, self.state.population, new_infections, new_diagnoses,
                new_aids_deaths=aids_deaths,
                new_chronic=_progression["new_chronic"],         # V11.11.9
                new_aids=_progression["new_aids"],               # V11.11.9
                new_infections_by_age=_new_inf_by_age,           # V12.1 BACKLOG-21
                new_chronic_by_age=_progression["new_chronic_by_age"],  # V12.1 BACKLOG-21
                new_aids_by_age=_progression["new_aids_by_age"],         # V12.1 BACKLOG-21
                prep_initiations_by_arm=prep_initiations_by_arm,
                art_initiations_by_stage=art_result["initiations_by_stage"],
            )

    def _step_aging_and_debut(self, step: int) -> Tuple[int, int, int]:
        """
        Age both childhood and adult populations and process sexual debut.

        Supports two aging methods (configurable via demography.aging_method):

        FRACTIONAL (default, recommended):
        - Aging happens EVERY timestep
        - 20% of each age bin transfers to next bin annually
        - 80% remains in current bin
        - Smooth mortality curves, no spikes

        DISCRETE (legacy):
        - Aging happens every 5 years
        - 100% of each age bin shifts to next bin
        - Flush-on-aging before shifts
        - Creates mortality spikes

        Returns:
            (continuous_debuts, flush_or_aging_debuts, total_debuts) for this timestep
        """
        # Get aging method from config (default to fractional)
        aging_method = extract_value(
            self.config.get("demography", {}).get("aging_method", {"value": "fractional"})
        )

        if aging_method in ("fractional", "residence_queue"):
            return self._step_aging_fractional(step)
        elif aging_method == "fractional_legacy":
            return self._step_aging_fractional_legacy(step)
        elif aging_method == "discrete":
            return self._step_aging_discrete(step)
        else:
            raise ValueError(
                f"Unknown aging_method: '{aging_method}'. "
                "Use 'fractional', 'residence_queue', 'fractional_legacy', or 'discrete'"
            )

    def _step_aging_fractional(self, step: int) -> Tuple[int, int, int]:
        """
        FRACTIONAL aging: residence-time queue through 5-year age bins.

        The config name remains 'fractional' for backward compatibility, but
        the production operator is now a residence queue: people advance one
        within-bin residence phase per timestep and only leave a 5-year bin
        after spending one full bin-width in it.
        """
        # 1. Residence-queue aging of adults (every timestep)
        self._apply_fractional_aging_adults()

        # 2. Residence-queue aging of children - returns those aging out of 10-14 bin
        aging_out_females, aging_out_males = self.demography.childhood.apply_fractional_aging(
            self.rng, self.deterministic, self.time
        )

        # 3. Process sexual debut for children aging out of oldest bin
        aging_debuts = 0
        if aging_out_females > 0 or aging_out_males > 0:
            before_debut_population = self.state.population.copy()
            self.state.population, aging_debuts = self.demography.process_sexual_debut(
                aging_out_females, aging_out_males,
                self.state.population,
                self.rng, self.deterministic
            )
            self._register_adult_phase_entries(self.state.population - before_debut_population)

        # continuous_debuts = 0 in fractional mode (no separate continuous debut)
        return 0, aging_debuts, aging_debuts

    def _step_aging_fractional_legacy(self, step: int) -> Tuple[int, int, int]:
        """Legacy memoryless fractional aging retained for diagnostic comparison."""
        self._apply_fractional_aging_adults_legacy()
        aging_out_females, aging_out_males = self.demography.childhood.apply_fractional_aging_legacy(
            self.rng, self.deterministic, self.time
        )

        aging_debuts = 0
        if aging_out_females > 0 or aging_out_males > 0:
            self.state.population, aging_debuts = self.demography.process_sexual_debut(
                aging_out_females, aging_out_males,
                self.state.population,
                self.rng, self.deterministic
            )

        return 0, aging_debuts, aging_debuts

    def _step_aging_discrete(self, step: int) -> Tuple[int, int, int]:
        """DISCRETE aging: 100% shift every 5 years, flush-on-aging."""
        continuous_debuts = 0
        flush_debuts = 0

        # 1. Continuous sexual debut (every timestep)
        debuting_females, debuting_males = self.demography.childhood.process_continuous_debut(
            self.time, self.rng, self.deterministic
        )

        if debuting_females > 0 or debuting_males > 0:
            self.state.population, continuous_debuts = self.demography.process_sexual_debut(
                debuting_females, debuting_males,
                self.state.population,
                self.rng, self.deterministic
            )

        # 2. Discrete aging (every 5 years)
        steps_per_5years = int(5.0 / self.time.years_per_step)
        if steps_per_5years <= 0:
            steps_per_5years = 1

        if step > 0 and step % steps_per_5years == 0:
            # 2a. FLUSH: Debut remaining children before aging out
            flushed_females, flushed_males = self.demography.childhood.flush_remaining_debut_eligible()

            if flushed_females > 0 or flushed_males > 0:
                self.state.population, flush_debuts = self.demography.process_sexual_debut(
                    flushed_females, flushed_males,
                    self.state.population,
                    self.rng, self.deterministic
                )

            # 2b. Age adult population (100% shift)
            self._apply_discrete_aging_adults()

            # 2c. Age childhood pool (100% shift)
            self.demography.childhood.age_and_debut()

        total_debuts = continuous_debuts + flush_debuts
        return continuous_debuts, flush_debuts, total_debuts

    @staticmethod
    def _allocate_int_by_weights(total: int, weights: np.ndarray) -> np.ndarray:
        """Allocate an integer total across weights using largest remainder."""
        total = int(total)
        weights = np.asarray(weights, dtype=float)
        if total <= 0:
            return np.zeros(len(weights), dtype=np.int64)
        if weights.sum() <= 0:
            weights = np.ones(len(weights), dtype=float) / len(weights)
        else:
            weights = weights / weights.sum()
        raw = total * weights
        base = np.floor(raw).astype(np.int64)
        remainder = total - int(base.sum())
        if remainder > 0:
            order = np.argsort(-(raw - base), kind="stable")
            base[order[:remainder]] += 1
        return base

    def _initialize_adult_residence_phases(self):
        """
        Initialize adult within-bin residence phases for queue aging.

        The engine's public state remains the aggregate population tensor.
        Residence phases are an auxiliary state used only to decide who is old
        enough to move into the next 5-year age bin at aging time.
        """
        self._adult_residence_phase_count = max(1, int(round(5.0 / self.time.years_per_step)))
        self._adult_residence_population = np.zeros(
            (self.groups.n_age_groups, self._adult_residence_phase_count) + self.state.population.shape[1:],
            dtype=np.int64,
        )
        even_weights = np.ones(self._adult_residence_phase_count, dtype=float)
        flat_population = self.state.population.reshape((self.groups.n_age_groups, -1))
        flat_phases = self._adult_residence_population.reshape(
            (self.groups.n_age_groups, self._adult_residence_phase_count, -1)
        )
        for age_idx in range(self.groups.n_age_groups):
            for cell_idx, count in enumerate(flat_population[age_idx]):
                flat_phases[age_idx, :, cell_idx] = self._allocate_int_by_weights(int(count), even_weights)

    def _initialize_adult_residence_queues(self):
        """Compatibility alias for residence-queue tests and diagnostics."""
        self._initialize_adult_residence_phases()

    def _initialize_residence_aging_queues(self):
        """Compatibility alias for residence-queue tests and diagnostics."""
        self._initialize_adult_residence_phases()

    def _ensure_adult_residence_phases(self):
        expected_phases = max(1, int(round(5.0 / self.time.years_per_step)))
        expected_shape = (self.groups.n_age_groups, expected_phases) + self.state.population.shape[1:]
        if not hasattr(self, "_adult_residence_population") or self._adult_residence_population.shape != expected_shape:
            self._initialize_adult_residence_phases()

    def _register_adult_phase_entries(self, delta: np.ndarray):
        """Register positive adult entrants into residence phase 0."""
        self._ensure_adult_residence_phases()
        positive = np.maximum(delta.astype(np.int64), 0)
        self._adult_residence_population[:, 0, ...] += positive

    def _register_positive_adult_residence_delta(self, before_population: np.ndarray):
        """Register positive exogenous adult additions, such as in-migration."""
        self._register_adult_phase_entries(self.state.population - before_population)

    def _seed_residence_aging_queues_from_population(self):
        """Seed all current adult population into residence phase 0."""
        self._ensure_adult_residence_phases()
        self._adult_residence_population.fill(0)
        self._adult_residence_population[:, 0, ...] = self.state.population

    def _sync_adult_residence_queues_from_population(self):
        """Compatibility alias that seeds current population as new entrants."""
        self._seed_residence_aging_queues_from_population()

    def _reconcile_adult_residence_phases(self):
        """
        Reconcile auxiliary residence phases to the aggregate population tensor.

        Disease transitions, mortality, ART, PrEP, and negative migration operate
        on the aggregate tensor. Before aging, this method scales each
        compartment's residence phases to exactly match the current aggregate
        count while preserving phase history where possible.
        """
        self._ensure_adult_residence_phases()
        flat_targets = self.state.population.reshape((self.groups.n_age_groups, -1))
        flat_phases = self._adult_residence_population.reshape(
            (self.groups.n_age_groups, self._adult_residence_phase_count, -1)
        )
        for age_idx in range(self.groups.n_age_groups):
            age_profile = flat_phases[age_idx].sum(axis=1)
            for cell_idx, target in enumerate(flat_targets[age_idx]):
                target = int(target)
                current = int(flat_phases[age_idx, :, cell_idx].sum())
                if current == target:
                    continue
                weights = flat_phases[age_idx, :, cell_idx]
                if weights.sum() <= 0:
                    weights = age_profile
                flat_phases[age_idx, :, cell_idx] = self._allocate_int_by_weights(target, weights)

    def _apply_fractional_aging_adults(self):
        """
        Apply residence-queue aging to adult population.

        People advance one residence phase per timestep inside each 5-year age
        bin. Only the final residence phase moves to the next age bin. This
        keeps smooth quarterly flow without allowing one cohort to traverse
        multiple 5-year bins too quickly.
        """
        self._reconcile_adult_residence_phases()

        new_phases = np.zeros_like(self._adult_residence_population)
        for age_idx in range(self.groups.n_age_groups):
            new_phases[age_idx, 1:, ...] += self._adult_residence_population[age_idx, :-1, ...]
            outflow = self._adult_residence_population[age_idx, -1, ...]
            if age_idx == self.groups.n_age_groups - 1:
                new_phases[age_idx, -1, ...] += outflow
            else:
                new_phases[age_idx + 1, 0, ...] += outflow

        self._adult_residence_population = new_phases
        self.state.population = self._adult_residence_population.sum(axis=1).astype(np.int64)

    def _apply_residence_queue_aging_adults(self):
        """Compatibility alias for residence-queue tests and diagnostics."""
        self._apply_fractional_aging_adults()

    def _apply_fractional_aging_adults_legacy(self):
        """
        Apply fractional aging to adult population (PATCH: Fractional Aging 2026-02-10).

        Every timestep:
        - Transfer fraction of each age bin to the next bin (scaled by timestep duration)
        - Retain remaining fraction in current bin
        - Terminal bin receives inflow but has no outflow (accumulates)

        For 5-year age bins, 1/5 = 20% should age out per year.
        This is scaled by timestep duration to maintain time-invariance.

        This eliminates mortality spikes from discrete 5-year cohort aging.
        Supports both deterministic and stochastic modes.

        See: PATCH_FRACTIONAL_AGING.md for scientific justification and validation.
        See: TIMESTEP_VALIDATION_ANALYSIS.md for time-invariance fix.
        """
        # CRITICAL FIX v2: Use EXPONENTIAL probability conversion for time-invariance
        # For 5-year bins, 20% should age per year.
        # Linear scaling (old: 0.2 * dt) gives wrong annual totals for dt != 1.
        # Exponential: p_step = 1 - (1 - p_annual)^dt ensures consistent annual rates.
        # See: diagnose_linear_vs_exponential.py for mathematical proof.
        aging_rate_annual = 1.0 / 5.0  # 20% per year for 5-year age bins
        aging_fraction = 1.0 - (1.0 - aging_rate_annual) ** self.time.years_per_step

        new_pop = np.zeros_like(self.state.population)

        # For each age bin (except terminal)
        for age_idx in range(self.groups.n_age_groups - 1):
            if self.deterministic:
                # Deterministic: use np.round for unbiased rounding (fixes truncation bias)
                # astype(int64) truncates toward zero, systematically over-aging with small timesteps
                staying = np.round(self.state.population[age_idx, ...] * (1.0 - aging_fraction)).astype(np.int64)
                moving = self.state.population[age_idx, ...] - staying
            else:
                # Stochastic: binomial draw for each compartment
                moving = self.rng.binomial(
                    self.state.population[age_idx, ...].astype(int),
                    aging_fraction
                )
                staying = self.state.population[age_idx, ...] - moving

            new_pop[age_idx, ...] += staying  # ADD to existing (might have inflow from previous bin)
            new_pop[age_idx + 1, ...] += moving

        # Terminal age bin: accumulates (receives inflow, no outflow)
        # Inflow from previous bin already added in loop above
        # Now add all existing population in terminal bin (100% stays)
        new_pop[-1, ...] += self.state.population[-1, ...]

        self.state.population = new_pop

    def _apply_discrete_aging_adults(self):
        """
        Apply discrete aging to adult population (DISCRETE AGING - LEGACY).

        Every 5 years:
        - 100% of each age bin shifts to next bin
        - Terminal bin accumulates (no outflow)
        - Creates synchronized cohorts → mortality spikes

        """
        # Discrete aging: 100% shift
        new_pop = np.zeros_like(self.state.population)

        # Shift all bins up by one
        for age_idx in range(self.groups.n_age_groups - 1):
            new_pop[age_idx + 1, :, :, :, :, :, :] = self.state.population[age_idx, :, :, :, :, :, :]

        # Terminal bin accumulates
        new_pop[-1, :, :, :, :, :, :] += self.state.population[-1, :, :, :, :, :, :]

        self.state.population = new_pop

    def _step_contacts_and_mixing(self) -> np.ndarray:
        """
        Compute contacts_allocated using Garnett (1994) epsilon parameterisation.

        V11+ (epsilon mode)
        -------------------
        Partnership balance is guaranteed:
          - Heterosexual: geometric bidirectional pool.
              contacts[F, f, M, m] = contacts[M, m, F, f] = P_geo(f, m)
              where P_geo(f, m) = sqrt(P_F(f, m) * P_M(m, f)).
              P_F uses female contact supply and male partner availability;
              P_M uses male contact supply and female partner availability.
          - MSM: symmetric averaged pool.
              contacts[MSM, i, MSM, j] = contacts[MSM, j, MSM, i] = P_sym(i, j)
              where P_sym = mean of both-directional raw partnership counts,
              ensuring each act is counted exactly once from both perspectives.

        V10 and earlier (legacy matrix mode)
        -------------------------------------
        Detected when epsilon_hetero / epsilon_msm are absent from mixing_config.
        Falls back to the original matrix-based logic (contacts NOT balanced --
        known double-counting bug) and emits a DeprecationWarning.
        Kept for backward compatibility with V10 test configs during rollout.
        BACKLOG (V11 retirement): Remove legacy matrix path once all V10 test
        configs (v10_v6_rel_pop.json, v6_golden_reference.json V6-format keys)
        are migrated to epsilon keys.

        V12.14: Heterosexual pool changed from female-driven to geometric mean.
        This preserves pair balance while allowing both Female and
        Heterosexual_Male contact supply to influence the number of heterosexual
        contacts formed.
        """
        mixing_config = self.config["mixing"]

        # -- Contact rates --------------------------------------------------
        base_contact_rates = np.array([
            self.groups.activity_contact_rates[activity]
            for activity in self.groups.activities
        ])

        groups_config = self.config.get("groups", {})
        orient_multipliers_config = extract_value(
            groups_config.get("orientation_contact_multipliers", {"value": {}})
        )
        orient_multipliers = np.array([
            orient_multipliers_config.get(orient, 1.0)
            for orient in self.groups.orientations
        ])

        # contact_rates_2d: (n_orientations, n_activities) -- scaled to this timestep
        contact_rates_2d = orient_multipliers[:, np.newaxis] * base_contact_rates[np.newaxis, :]
        contact_rates_2d = contact_rates_2d * self.time.years_per_step

        # active_pop: (n_orientations, n_activities) -- age/HIV/Dx/ART/PrEP collapsed
        active_pop     = np.sum(self.state.population, axis=(0, 3, 4, 5, 6))
        total_contacts = active_pop * contact_rates_2d

        n_orient = self.groups.n_orientations
        n_act    = self.groups.n_activities
        contacts_allocated = np.zeros((n_orient, n_act, n_orient, n_act))

        female_idx      = self.groups.orient_to_idx.get("Female", 0)
        hetero_male_idx = self.groups.orient_to_idx.get("Heterosexual_Male", 1)
        msm_idx         = self.groups.orient_to_idx.get("MSM", 2)

        # -- Detect config mode ---------------------------------------------
        use_epsilon = (
            "epsilon_hetero" in mixing_config and
            "epsilon_msm"    in mixing_config
        )

        if not use_epsilon:
            # -- LEGACY MATRIX MODE (V10 backward compat) -------------------
            import warnings
            warnings.warn(
                "mixing config uses legacy matrix format (heterosexual/msm dicts). "
                "Upgrade to V11 config (epsilon_hetero / epsilon_msm scalars) for "
                "partnership-balanced contacts. See IMPLEMENTATION_PLAN_MIXING_REFACTOR.md.",
                DeprecationWarning,
                stacklevel=3
            )
            hetero_mix_config = extract_value(mixing_config["heterosexual"])
            msm_mix_config    = extract_value(mixing_config["msm"])

            hetero_mix = np.zeros((n_act, n_act))
            msm_mix    = np.zeros((n_act, n_act))
            for i, fa in enumerate(self.groups.activities):
                for j, ta in enumerate(self.groups.activities):
                    hetero_mix[i, j] = hetero_mix_config.get(fa, {}).get(ta, 0)
                    msm_mix[i, j]    = msm_mix_config.get(fa, {}).get(ta, 0)

            for fa in range(n_act):
                for ma in range(n_act):
                    contacts_allocated[female_idx,      fa, hetero_male_idx, ma] = \
                        total_contacts[female_idx, fa]      * hetero_mix[fa, ma]
                    contacts_allocated[hetero_male_idx, fa, female_idx,      ma] = \
                        total_contacts[hetero_male_idx, fa] * hetero_mix[fa, ma]
            for i in range(n_act):
                for j in range(n_act):
                    contacts_allocated[msm_idx, i, msm_idx, j] = \
                        total_contacts[msm_idx, i] * msm_mix[i, j]
            return contacts_allocated

        # -- EPSILON MODE (V11+) --------------------------------------------
        epsilon_hetero = float(extract_value(mixing_config["epsilon_hetero"]))
        epsilon_msm    = float(extract_value(mixing_config["epsilon_msm"]))

        # Clamp to valid range [0, 1]
        epsilon_hetero = max(0.0, min(1.0, epsilon_hetero))
        epsilon_msm    = max(0.0, min(1.0, epsilon_msm))

        # -- HETEROSEXUAL: geometric bidirectional partnership pool ----------
        # Raw female-side and male-side demands are computed using the same
        # Garnett epsilon structure, then reconciled with a geometric mean.
        # This keeps pair balance without making either sex the sole binding
        # constraint on heterosexual partnership volume.
        female_contacts = total_contacts[female_idx]       # (n_act,)
        male_contacts   = total_contacts[hetero_male_idx]  # (n_act,)
        male_avail      = male_contacts.sum()
        female_avail    = female_contacts.sum()
        male_weight     = male_contacts / (male_avail + 1e-10)    # normalised
        female_weight   = female_contacts / (female_avail + 1e-10)  # normalised

        for f_act in range(n_act):
            for m_act in range(n_act):
                # Garnett (1994) eq. 5:
                # rho_F(f->m) = eps*delta(f,m) + (1-eps)*[N_M(m)*c_M(m)/sum_k N_M(k)*c_M(k)]
                assort = 1.0 if f_act == m_act else 0.0
                rho_f  = epsilon_hetero * assort + (1.0 - epsilon_hetero) * male_weight[m_act]
                rho_m  = epsilon_hetero * assort + (1.0 - epsilon_hetero) * female_weight[f_act]
                female_side_pool = female_contacts[f_act] * rho_f
                male_side_pool   = male_contacts[m_act] * rho_m
                pool = np.sqrt(female_side_pool * male_side_pool)
                # Same pool from both directions (contact balance guarantee)
                contacts_allocated[female_idx,      f_act, hetero_male_idx, m_act] = pool
                contacts_allocated[hetero_male_idx, m_act, female_idx,      f_act] = pool

        # -- MSM: symmetric averaged partnership pool -----------------------
        # Proportional term: MSM contact availability weights (population-dynamic)
        msm_contacts = total_contacts[msm_idx]  # (n_act,)
        msm_avail    = msm_contacts.sum()
        msm_weight   = msm_contacts / (msm_avail + 1e-10)

        for i in range(n_act):
            for j in range(n_act):
                assort = 1.0 if i == j else 0.0
                rho_ij = epsilon_msm * assort + (1.0 - epsilon_msm) * msm_weight[j]
                rho_ji = epsilon_msm * assort + (1.0 - epsilon_msm) * msm_weight[i]
                p_ij   = msm_contacts[i] * rho_ij
                p_ji   = msm_contacts[j] * rho_ji
                # Symmetrize: average conserves total partnership volume
                p_sym  = (p_ij + p_ji) * 0.5
                contacts_allocated[msm_idx, i, msm_idx, j] = p_sym
                contacts_allocated[msm_idx, j, msm_idx, i] = p_sym

        return contacts_allocated


    def _step_disease_progression(self):
        """Progress through HIV disease stages: Acute -> Chronic -> AIDS.

        Chronic -> AIDS progression is ART-dependent (PATCH: ART-dependent progression):
          - Off ART: uses 'chronic_to_aids' rate (default 5%/year)
          - On ART:  uses 'chronic_to_aids_on_art' rate (default 0.5%/year)
        Backward compatible: if 'chronic_to_aids_on_art' is absent from config,
        falls back to the same rate as off-ART (preserving pre-patch behaviour).
        Acute -> Chronic progression remains ART-independent (people in Acute
        have very recently been infected and ART is rarely initiated that early).
        """
        disease_config = self.config["disease"]

        acute_idx = self.groups.hiv_to_idx["Acute"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        aids_idx = self.groups.hiv_to_idx["AIDS"]

        # ART dimension indices
        no_art_idx = 0
        on_art_idx = 1

        # --- Acute -> Chronic (ART-independent) ---
        acute_to_chronic = self.time.annual_to_step_rate(
            extract_value(disease_config["progression"]["acute_to_chronic"])
        )
        acute_pop = self.state.population[:, :, :, acute_idx, :, :, :]
        progressions = self._draw_events(acute_pop, acute_to_chronic)
        self.state.population[:, :, :, acute_idx, :, :, :] -= progressions
        self.state.population[:, :, :, chronic_idx, :, :, :] += progressions

        # --- Chronic -> AIDS (ART-dependent) ---
        off_art_annual = extract_value(disease_config["progression"]["chronic_to_aids"])

        # Backward compatibility: fall back to off-ART rate if key absent
        on_art_annual = extract_value(
            disease_config["progression"].get(
                "chronic_to_aids_on_art", {"value": off_art_annual}
            )
        )

        chronic_to_aids_off_art = self.time.annual_to_step_rate(off_art_annual)
        chronic_to_aids_on_art  = self.time.annual_to_step_rate(on_art_annual)

        # Off-ART Chronic pool: all diagnosis states, ART=0
        chronic_off_art = self.state.population[:, :, :, chronic_idx, :, no_art_idx, :]
        prog_off_art = self._draw_events(chronic_off_art, chronic_to_aids_off_art)
        self.state.population[:, :, :, chronic_idx, :, no_art_idx, :] -= prog_off_art
        self.state.population[:, :, :, aids_idx,   :, no_art_idx, :] += prog_off_art

        # On-ART Chronic pool: all diagnosis states, ART=1
        chronic_on_art = self.state.population[:, :, :, chronic_idx, :, on_art_idx, :]
        prog_on_art = self._draw_events(chronic_on_art, chronic_to_aids_on_art)
        self.state.population[:, :, :, chronic_idx, :, on_art_idx, :] -= prog_on_art
        self.state.population[:, :, :, aids_idx,   :, on_art_idx, :] += prog_on_art

        # V11.11.9: return incident counts for friction cost economics
        # `progressions` = Acute→Chronic; `prog_off_art` + `prog_on_art` = Chronic→AIDS
        new_chronic = int(progressions.sum())
        new_aids    = int(prog_off_art.sum() + prog_on_art.sum())

        # V12.1 (BACKLOG-21): age-stratified vectors for friction cost precision.
        # progressions shape: (age, orient, activity, diag, art, prep) — sum non-age axes → (age,)
        new_chronic_by_age = progressions.sum(axis=(1, 2, 3, 4, 5))
        # prog_off_art / prog_on_art shape: (age, orient, activity, diag, prep) — sum → (age,)
        new_aids_by_age = (
            prog_off_art.sum(axis=(1, 2, 3, 4)) + prog_on_art.sum(axis=(1, 2, 3, 4))
        )

        return {
            "new_chronic":        new_chronic,
            "new_aids":           new_aids,
            "new_chronic_by_age": new_chronic_by_age,   # np.ndarray shape (n_age_groups,)
            "new_aids_by_age":    new_aids_by_age,       # np.ndarray shape (n_age_groups,)
        }

    def _build_diagnosis_schedule(self):
        """
        V12.15: Build a TimeSchedule for the diagnosis rate if a schedule block is present
        in config["disease"]["diagnosis"]["schedule"]. Returns None if absent (flat rate
        fallback applies).

        Config schema expected:
          disease.diagnosis.schedule.interpolation: {"value": "stepwise"|"linear"}
          disease.diagnosis.schedule.value: {year_str: rate, ...}
        """
        diag_config = self.config.get("disease", {}).get("diagnosis", {})
        schedule_block = diag_config.get("schedule", {})
        if not schedule_block:
            return None
        schedule_dict = extract_value(schedule_block.get("value", {}))
        if not schedule_dict:
            return None
        interp = extract_value(schedule_block.get("interpolation", {"value": "stepwise"}))
        return TimeSchedule(schedule_dict, interpolation=interp)

    def _build_msm_relative_diagnosis_schedule(self):
        """V13.10: Build a TimeSchedule for the optional MSM-relative diagnosis multiplier.

        Returns None when the block is absent or enabled=false (full backward compatibility).

        Config schema (under disease.diagnosis.msm_relative_diagnosis):
          enabled: {"value": true|false}
          schedule.interpolation: {"value": "stepwise"|"linear"}
          schedule.value: {year_str: multiplier, ...}
        """
        diag_config = self.config.get("disease", {}).get("diagnosis", {})
        msm_block = diag_config.get("msm_relative_diagnosis", {})
        if not msm_block:
            return None
        if not extract_value(msm_block.get("enabled", {"value": False})):
            return None
        schedule_block = msm_block.get("schedule", {})
        if not schedule_block:
            return None
        schedule_dict = extract_value(schedule_block.get("value", {}))
        if not schedule_dict:
            return None
        interp = extract_value(schedule_block.get("interpolation", {"value": "stepwise"}))
        return TimeSchedule({str(k): float(v) for k, v in schedule_dict.items()},
                            interpolation=interp)

    def _step_diagnosis(self, current_year: float) -> dict:
        """Diagnose undiagnosed HIV+ individuals.

        V12.15: When a diagnosis schedule is configured, the annual rate is looked up by
        calendar year (stepwise or linear interpolation). Falls back to the flat
        annual_rate when no schedule is present (full backward compatibility).

        V13.10/V13.21: When msm_relative_diagnosis is enabled, MSM and non-MSM
        undiagnosed pools are diagnosed at separate rates. Non-MSM use the base
        rate unchanged; MSM use the base rate multiplied by the era-specific
        multiplier. The multiplier may exceed 1.0; the derived annual
        probability and resulting timestep probability are clipped to [0, 1].
        Disabled by default; no behavior change unless the config block is
        present and enabled=true.

        Args:
            current_year: Current calendar year for schedule lookup.

        Returns:
            dict: Total and MSM new diagnoses this timestep.
        """
        if self.diagnosis_schedule is not None:
            annual_rate = self.diagnosis_schedule.get_value(current_year)
        else:
            annual_rate = extract_value(self.config["disease"]["diagnosis"]["annual_rate"])

        msm_idx = self.groups.orient_to_idx.get("MSM")

        if self.msm_relative_diagnosis_schedule is not None and msm_idx is not None:
            # V13.10: group-structured diagnosis path — draw MSM and non-MSM separately
            multiplier = max(
                float(self.msm_relative_diagnosis_schedule.get_value(current_year)),
                0.0,
            )
            base_step_rate = self.time.annual_to_step_rate(annual_rate)
            msm_annual_rate = float(np.clip(annual_rate * multiplier, 0.0, 1.0))
            msm_step_rate = float(np.clip(
                self.time.annual_to_step_rate(msm_annual_rate), 0.0, 1.0
            ))

            undiagnosed = self.state.population[:, :, :, 1:, 0, :, :]

            # Non-MSM orientations: use base rate
            non_msm_undiag = undiagnosed.copy()
            non_msm_undiag[:, msm_idx, :, :, :, :] = 0
            diagnosed_non_msm = self._draw_events(non_msm_undiag, base_step_rate)

            # MSM orientation: use multiplied rate
            msm_undiag = np.zeros_like(undiagnosed)
            msm_undiag[:, msm_idx, :, :, :, :] = undiagnosed[:, msm_idx, :, :, :, :]
            diagnosed_msm = self._draw_events(msm_undiag, msm_step_rate)

            diagnosed = diagnosed_non_msm + diagnosed_msm
            self.state.population[:, :, :, 1:, 0, :, :] -= diagnosed
            self.state.population[:, :, :, 1:, 1, :, :] += diagnosed
            msm_diagnoses = int(np.sum(diagnosed_msm[:, msm_idx, :, :, :, :]))
            return {
                "total": int(np.sum(diagnosed)),
                "msm": msm_diagnoses,
            }

        # Default path (unchanged behavior)
        diagnosis_rate = self.time.annual_to_step_rate(annual_rate)
        undiagnosed = self.state.population[:, :, :, 1:, 0, :, :]
        diagnosed = self._draw_events(undiagnosed, diagnosis_rate)
        self.state.population[:, :, :, 1:, 0, :, :] -= diagnosed
        self.state.population[:, :, :, 1:, 1, :, :] += diagnosed
        msm_diagnoses = int(np.sum(diagnosed[:, msm_idx, :, :, :, :])) if msm_idx is not None else 0
        return {
            "total": int(np.sum(diagnosed)),  # V11.6.1: new diagnoses counter
            "msm": msm_diagnoses,
        }

    def _step_art_transitions(self, current_year: float):
        """
        Manage ART initiation and dropout via time-based schedules.

        If a ScheduleManager is configured (art_schedule present in config), rates
        are looked up by calendar year. If no schedule is configured, both rates are
        zero — no ART transitions occur. This is the expected behaviour for test
        scenarios that deliberately omit the art_schedule block.

        Args:
            current_year: Current calendar year for schedule lookup
        """
        disease_config = self.config["disease"]

        # Use time-based schedules if configured; zero rates when no schedule is present.
        if self.schedule_manager:
            # Get rates from schedule based on current year
            initiation_annual = self.schedule_manager.get_art_initiation_rate(current_year)
            dropout_annual = self.schedule_manager.get_art_discontinuation_rate(current_year)

            # Validation: ART should be unavailable before introduction
            intro_year = self.schedule_manager.get_art_introduction_year()
            if intro_year and current_year < intro_year:
                validate_schedule_before_intro(
                    self.schedule_manager, current_year, intro_year,
                    initiation_annual, "ART initiation"
                )
        else:
            # No ART schedule configured — ART transitions disabled (zero rates).
            # This is a valid configuration for test scenarios that intentionally
            # omit the art_schedule block. Production configs always supply a schedule
            # via interventions.art, which ConfigReader exposes as art_schedule.
            initiation_annual = 0.0
            dropout_annual = 0.0

        # Convert annual rates to per-timestep rates
        initiation_rate = self.time.annual_to_step_rate(initiation_annual)
        dropout_rate = self.time.annual_to_step_rate(dropout_annual)

        # Apply transitions
        self._art_step_initiations_by_stage = {
            stage_name: 0
            for stage_name in self.groups.hiv_to_idx
            if stage_name != "Susceptible"
        }
        eligible = self.state.population[:, :, :, 1:, 1, 0, :]
        initiated = self._draw_events(eligible, initiation_rate)
        self.state.population[:, :, :, 1:, 1, 0, :] -= initiated
        self.state.population[:, :, :, 1:, 1, 1, :] += initiated

        stage_counts = {}
        for stage_name, stage_idx in self.groups.hiv_to_idx.items():
            if stage_name == "Susceptible":
                continue
            rel_idx = stage_idx - 1
            if 0 <= rel_idx < initiated.shape[3]:
                stage_counts[stage_name] = int(np.sum(initiated[:, :, :, rel_idx, :]))
            else:
                stage_counts[stage_name] = 0
        self._art_step_initiations_by_stage = stage_counts

        on_art = self.state.population[:, :, :, 1:, 1, 1, :]
        dropped = self._draw_events(on_art, dropout_rate)
        self.state.population[:, :, :, 1:, 1, 1, :] -= dropped
        self.state.population[:, :, :, 1:, 1, 0, :] += dropped
        return {
            "initiations": int(np.sum(initiated)),
            "dropouts": int(np.sum(dropped)),
            "initiations_by_stage": stage_counts,
        }

    def _build_art_mortality_reduction_schedule(self):
        """
        Optional calendar-era schedule for ART mortality reduction.

        Expected config schema in V10 raw config:
          transmission_model.disease.advanced.art_mortality_reduction.schedule.value:
            {"1996": 0.65, "2000": 0.75, "2005": 0.90}
          transmission_model.disease.advanced.art_mortality_reduction.schedule.interpolation:
            {"value": "stepwise"|"linear"}

        ConfigReader exposes this as:
          disease.treatment.art_mortality_reduction.schedule

        If the schedule is absent, the existing constant value is used.
        """
        treatment_config = self.config.get("disease", {}).get("treatment", {})
        art_mortality = treatment_config.get("art_mortality_reduction", {})
        if not isinstance(art_mortality, dict):
            return None
        schedule_block = art_mortality.get("schedule", {})
        if not schedule_block:
            return None
        schedule_dict = extract_value(schedule_block.get("value", {}))
        if not schedule_dict:
            schedule_dict = extract_value(schedule_block.get("schedule", {}))
        if not schedule_dict:
            return None
        interp = extract_value(schedule_block.get("interpolation", {"value": "stepwise"}))
        default_before = extract_value(art_mortality)
        return TimeSchedule(schedule_dict, interpolation=interp, default_before=default_before)

    def _step_prep_transitions(self, current_year: float):
        """
        Manage PrEP uptake and discontinuation.

        V11.5: Eligibility-filtered, risk-stratified, time-scheduled PrEP.

        Uptake is restricted to eligible individuals:
          - Orientation filter: only orientations in ``eligible_orientations``
          - Age filter: only age bins within ``eligible_age_range``
          - Activity filter: only activity classes in ``eligible_activity_classes``
          - Activity modifier: uptake_rate scaled by ``uptake_modifier_by_activity``

        Dropout applies to ALL current PrEP users (no eligibility filter).
        Existing users who age past the max eligible age keep their PrEP.

        Effective per-cell uptake rate:
            schedule_rate(year) × activity_modifier[activity] × eligibility(orient, age)

        Steady-state coverage per activity class among eligible population:
            coverage = effective_uptake / (effective_uptake + dropout)

        Args:
            current_year: Current calendar year for schedule lookup
        """
        interventions = self.config["interventions"]
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        none_idx = self.groups.prep_to_idx["None"]

        # V11.8.0: Reset per-step flow counters for all known prep types
        for prep_name in self.groups.prep_states[1:]:
            self._prep_step_initiations[prep_name] = 0
            self._prep_step_dropouts[prep_name] = 0
            self._prep_step_corridor_additions[prep_name] = 0
            self._prep_step_corridor_removals[prep_name] = 0
            self._prep_step_target_additions[prep_name] = 0
            self._prep_step_switches[prep_name] = 0
            self._prep_step_switch_shortfall[prep_name] = 0

        for prep_name in self.groups.prep_states[1:]:
            # V11.8.4: prep_name is CamelCase ("Oral_PrEP"); self.config["interventions"] is also
            # CamelCase (translated by ConfigReader.as_v6_dict()), so direct lookup works.
            if prep_name not in interventions:
                continue
            prep_config = interventions[prep_name]
            if not extract_value(prep_config.get("enabled", {"value": True})):
                continue

            prep_idx = self.groups.prep_to_idx[prep_name]

            # --- Rate lookup (V5: schedule-based, V4: fixed) ---
            if self.schedule_manager:
                uptake_annual = self.schedule_manager.get_prep_initiation_rate(prep_name, current_year)
                dropout_annual = self.schedule_manager.get_prep_discontinuation_rate(prep_name, current_year)

                intro_year = self.schedule_manager.get_prep_introduction_year(prep_name)
                if intro_year and current_year < intro_year:
                    validate_schedule_before_intro(
                        self.schedule_manager, current_year, intro_year,
                        uptake_annual, f"{prep_name} initiation"
                    )
            else:
                uptake_annual = extract_value(prep_config["uptake_rate"])
                dropout_annual = extract_value(prep_config["dropout_rate"])

            # Convert annual rates to per-timestep
            base_uptake_step = self.time.annual_to_step_rate(uptake_annual)
            dropout_rate = self.time.annual_to_step_rate(dropout_annual)
            corridor_active = self._prep_corridor_bounds(prep_name, current_year) != (None, None)
            target_stock = self._prep_forward_target_stock(prep_name, current_year)
            switch_share = self._prep_switch_share(prep_name, current_year)

            # Forward HTA target/switch modes are stock-based policy controls.
            # They replace ordinary rate-based uptake for that product so
            # arbitrary initiation probabilities do not drive HTA scenarios.
            if target_stock is not None or switch_share > 0:
                base_uptake_step = 0.0

            # Skip only if no scheduled transition and no stock corridor this step.
            if (
                base_uptake_step == 0
                and dropout_rate == 0
                and not corridor_active
                and target_stock is None
                and switch_share <= 0
            ):
                continue

            # --- V11.5: Resolve eligibility filters ---

            # Orientation filter (default: all orientations for backward compat)
            eligible_orient_raw = extract_value(
                prep_config.get("eligible_orientations",
                                {"value": list(self.groups.orientations)})
            )
            eligible_orient_indices = []
            for orient_name in eligible_orient_raw:
                if orient_name in self.groups.orient_to_idx:
                    eligible_orient_indices.append(self.groups.orient_to_idx[orient_name])

            # Age range filter (default: full range for backward compat)
            age_range_raw = extract_value(
                prep_config.get("eligible_age_range",
                                {"value": {"min": self.groups.age_groups[0],
                                           "max": self.groups.age_groups[-1]}})
            )
            age_min_idx = self.groups.age_to_idx.get(
                age_range_raw["min"], 0
            )
            age_max_idx = self.groups.age_to_idx.get(
                age_range_raw["max"], self.groups.n_age_groups - 1
            ) + 1  # exclusive upper bound for slicing

            # Activity modifiers (default: 1.0 for all if absent)
            modifiers_raw = extract_value(
                prep_config.get("uptake_modifier_by_activity",
                                {"value": {name: 1.0 for name in self.groups.activities}})
            )

            # Activity filter (default: all activities for backward compat)
            eligible_act_raw = extract_value(
                prep_config.get("eligible_activity_classes",
                                {"value": list(self.groups.activities)})
            )
            eligible_act_indices = [
                self.groups.activity_to_idx[name]
                for name in eligible_act_raw
                if name in self.groups.activity_to_idx
            ]

            # --- UPTAKE: eligible orientation × age × activity only ---
            if base_uptake_step > 0:
                for orient_idx in eligible_orient_indices:
                    for act_idx in eligible_act_indices:
                        act_name = self.groups.activities[act_idx]
                        modifier = modifiers_raw.get(act_name, 1.0)
                        effective_uptake = base_uptake_step * modifier
                        if effective_uptake <= 0:
                            continue

                        # Clip to valid probability
                        effective_uptake = min(effective_uptake, 1.0)

                        # Eligible non-PrEP susceptible population in this cell
                        no_prep_slice = self.state.population[
                            age_min_idx:age_max_idx, orient_idx, act_idx,
                            susc_idx, :, :, none_idx
                        ]
                        new_users = self._draw_events(no_prep_slice, effective_uptake)
                        self.state.population[
                            age_min_idx:age_max_idx, orient_idx, act_idx,
                            susc_idx, :, :, none_idx
                        ] -= new_users
                        self.state.population[
                            age_min_idx:age_max_idx, orient_idx, act_idx,
                            susc_idx, :, :, prep_idx
                        ] += new_users
                        # V11.8.0: accumulate initiation count
                        self._prep_step_initiations[prep_name] += int(np.sum(new_users))

            # --- DROPOUT: ALL current PrEP users (no eligibility filter) ---
            if dropout_rate > 0:
                prep_users = self.state.population[:, :, :, susc_idx, :, :, prep_idx]
                dropouts = self._draw_events(prep_users, dropout_rate)
                self.state.population[:, :, :, susc_idx, :, :, prep_idx] -= dropouts
                # V12.2: Lenacapavir dropouts → Lenacapavir_Waning (1-step residual protection).
                # All other PrEP dropouts → None as before.
                waning_idx = self.groups.prep_to_idx.get("Lenacapavir_Waning", -1)
                if prep_name == "Lenacapavir" and waning_idx >= 0:
                    self.state.population[:, :, :, susc_idx, :, :, waning_idx] += dropouts
                else:
                    self.state.population[:, :, :, susc_idx, :, :, none_idx] += dropouts
                # V11.8.0: accumulate dropout count (counts lena→waning as clinical dropout event)
                self._prep_step_dropouts[prep_name] += int(np.sum(dropouts))

            if switch_share > 0:
                self._apply_prep_switch_share(
                    target_prep_name=prep_name,
                    target_prep_idx=prep_idx,
                    switch_share=switch_share,
                    eligible_orient_indices=eligible_orient_indices,
                    eligible_act_indices=eligible_act_indices,
                    age_min_idx=age_min_idx,
                    age_max_idx=age_max_idx,
                    modifiers_raw=modifiers_raw,
                )

            if target_stock is not None:
                self._apply_prep_forward_target_stock(
                    prep_name=prep_name,
                    prep_idx=prep_idx,
                    target_stock=target_stock,
                    current_year=current_year,
                    eligible_orient_indices=eligible_orient_indices,
                    eligible_act_indices=eligible_act_indices,
                    age_min_idx=age_min_idx,
                    age_max_idx=age_max_idx,
                    modifiers_raw=modifiers_raw,
                )

            self._apply_prep_stock_corridor(
                prep_name=prep_name,
                prep_idx=prep_idx,
                current_year=current_year,
                prep_config=prep_config,
                eligible_orient_indices=eligible_orient_indices,
                eligible_act_indices=eligible_act_indices,
                age_min_idx=age_min_idx,
                age_max_idx=age_max_idx,
                modifiers_raw=modifiers_raw,
            )

    def _scheduled_advanced_prep_value(
        self,
        prep_name: str,
        advanced_key: str,
        schedule_key: str,
        current_year: float,
        default=None,
    ):
        advanced = (
            self.config.get("prep_schedules", {})
            .get(prep_name, {})
            .get("advanced", {})
            .get(advanced_key, {})
        )
        if not advanced or not bool(extract_value(advanced.get("enabled", False))):
            return default
        schedule = extract_value(advanced.get(schedule_key, {}))
        if not schedule:
            return default
        interpolation = str(extract_value(advanced.get("interpolation", {"value": "stepwise"})))
        return TimeSchedule(schedule, interpolation=interpolation).get_value(current_year)

    def _prep_forward_target_stock(
        self,
        prep_name: str,
        current_year: float,
    ) -> Optional[int]:
        value = self._scheduled_advanced_prep_value(
            prep_name,
            "forward_target_stock",
            "target_stock_schedule",
            current_year,
            default=None,
        )
        if value is None:
            return None
        if not np.isfinite(value):
            return None
        return max(int(round(value)), 0)

    def _prep_switch_share(
        self,
        prep_name: str,
        current_year: float,
    ) -> float:
        value = self._scheduled_advanced_prep_value(
            prep_name,
            "switch_share_from_prep",
            "share_schedule",
            current_year,
            default=0.0,
        )
        if value is None or not np.isfinite(value):
            return 0.0
        return max(0.0, min(float(value), 1.0))

    def _eligible_susceptible_capacity(
        self,
        source_prep_idx: int,
        eligible_orient_indices: list[int],
        eligible_act_indices: list[int],
        age_min_idx: int,
        age_max_idx: int,
        modifiers_raw: dict,
    ) -> Tuple[np.ndarray, np.ndarray]:
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        source = self.state.population[:, :, :, susc_idx, :, :, source_prep_idx]
        capacity = np.zeros_like(source, dtype=np.int64)
        weights = np.zeros_like(source, dtype=float)

        for orient_idx in eligible_orient_indices:
            for act_idx in eligible_act_indices:
                act_name = self.groups.activities[act_idx]
                modifier = float(modifiers_raw.get(act_name, 1.0))
                if modifier <= 0:
                    continue
                capacity[age_min_idx:age_max_idx, orient_idx, act_idx, :, :] = (
                    source[age_min_idx:age_max_idx, orient_idx, act_idx, :, :]
                )
                weights[age_min_idx:age_max_idx, orient_idx, act_idx, :, :] = modifier
        return capacity, weights

    def _preserved_total_switch_stock_offset(
        self,
        source_prep_name: str,
        current_year: float,
        susc_idx: int,
    ) -> int:
        """
        Count target-product users that should offset a source target stock.

        V13.3: split HTA substitution scenarios set Oral_PrEP as the source
        product and Lenacapavir as a preserve-total switch target. In that mode
        the oral target is an any-PrEP target, otherwise each product keeps its
        product-specific target semantics.
        """
        offset = 0
        for target_prep_name in self.groups.prep_states[1:]:
            if target_prep_name == source_prep_name:
                continue

            switch_cfg = (
                self.config.get("prep_schedules", {})
                .get(target_prep_name, {})
                .get("advanced", {})
                .get("switch_share_from_prep", {})
            )
            if not switch_cfg:
                continue
            if not bool(extract_value(switch_cfg.get("enabled", {"value": False}))):
                continue
            if not bool(extract_value(
                switch_cfg.get("preserve_total_prep_stock", {"value": False})
            )):
                continue
            if extract_value(
                switch_cfg.get("from_prep_state", {"value": "Oral_PrEP"})
            ) != source_prep_name:
                continue
            if self._prep_switch_share(target_prep_name, current_year) <= 0:
                continue

            target_idx = self.groups.prep_to_idx.get(target_prep_name)
            if target_idx is None:
                continue
            target_users = self.state.population[:, :, :, susc_idx, :, :, target_idx]
            offset += int(np.sum(target_users))
        return offset

    def _apply_prep_forward_target_stock(
        self,
        prep_name: str,
        prep_idx: int,
        target_stock: int,
        current_year: float,
        eligible_orient_indices: list[int],
        eligible_act_indices: list[int],
        age_min_idx: int,
        age_max_idx: int,
        modifiers_raw: dict,
    ) -> None:
        """Apply forward HTA target stock without forcing reductions by default."""
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        none_idx = self.groups.prep_to_idx["None"]
        prep_users = self.state.population[:, :, :, susc_idx, :, :, prep_idx]
        current_stock = int(np.sum(prep_users))
        current_stock += self._preserved_total_switch_stock_offset(
            prep_name,
            current_year,
            susc_idx,
        )

        advanced = (
            self.config.get("prep_schedules", {})
            .get(prep_name, {})
            .get("advanced", {})
            .get("forward_target_stock", {})
        )
        above_action = extract_value(
            advanced.get("above_target_action", {"value": "allow_attrition_only"})
        )
        if current_stock > target_stock:
            if above_action == "force_down_to_target":
                reductions = self._allocate_fixed_events(prep_users, current_stock - target_stock)
                self.state.population[:, :, :, susc_idx, :, :, prep_idx] -= reductions
                self.state.population[:, :, :, susc_idx, :, :, none_idx] += reductions
            return

        needed = target_stock - current_stock
        if needed <= 0:
            return

        capacity, weights = self._eligible_susceptible_capacity(
            none_idx,
            eligible_orient_indices,
            eligible_act_indices,
            age_min_idx,
            age_max_idx,
            modifiers_raw,
        )
        additions = self._allocate_fixed_events(capacity, needed, weights=weights)
        added = int(np.sum(additions))
        if added <= 0:
            return

        self.state.population[:, :, :, susc_idx, :, :, none_idx] -= additions
        self.state.population[:, :, :, susc_idx, :, :, prep_idx] += additions
        self._prep_step_initiations[prep_name] += added
        self._prep_step_target_additions[prep_name] += added

    def _apply_prep_switch_share(
        self,
        target_prep_name: str,
        target_prep_idx: int,
        switch_share: float,
        eligible_orient_indices: list[int],
        eligible_act_indices: list[int],
        age_min_idx: int,
        age_max_idx: int,
        modifiers_raw: dict,
    ) -> None:
        """Switch eligible oral PrEP users to the target PrEP state."""
        advanced = (
            self.config.get("prep_schedules", {})
            .get(target_prep_name, {})
            .get("advanced", {})
            .get("switch_share_from_prep", {})
        )
        source_name = extract_value(
            advanced.get("from_prep_state", {"value": "Oral_PrEP"})
        )
        source_idx = self.groups.prep_to_idx.get(source_name)
        if source_idx is None or source_idx == target_prep_idx:
            return

        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        source_users = self.state.population[:, :, :, susc_idx, :, :, source_idx]
        target_users = self.state.population[:, :, :, susc_idx, :, :, target_prep_idx]

        eligible_source = 0
        eligible_target = 0
        for orient_idx in eligible_orient_indices:
            for act_idx in eligible_act_indices:
                eligible_source += int(np.sum(
                    source_users[age_min_idx:age_max_idx, orient_idx, act_idx, :, :]
                ))
                eligible_target += int(np.sum(
                    target_users[age_min_idx:age_max_idx, orient_idx, act_idx, :, :]
                ))

        eligible_total = eligible_source + eligible_target
        if eligible_total <= 0:
            return

        desired_target = int(round(switch_share * eligible_total))
        current_target = eligible_target
        needed = max(desired_target - current_target, 0)
        if needed <= 0:
            return

        capacity, weights = self._eligible_susceptible_capacity(
            source_idx,
            eligible_orient_indices,
            eligible_act_indices,
            age_min_idx,
            age_max_idx,
            modifiers_raw,
        )
        switches = self._allocate_fixed_events(capacity, needed, weights=weights)
        switched = int(np.sum(switches))
        shortfall = max(needed - switched, 0)
        if switched <= 0:
            self._prep_step_switch_shortfall[target_prep_name] += shortfall
            return

        self.state.population[:, :, :, susc_idx, :, :, source_idx] -= switches
        self.state.population[:, :, :, susc_idx, :, :, target_prep_idx] += switches
        self._prep_step_initiations[target_prep_name] += switched
        self._prep_step_switches[target_prep_name] += switched
        self._prep_step_switch_shortfall[target_prep_name] += shortfall

    def _prep_corridor_bounds(
        self,
        prep_name: str,
        current_year: float,
    ) -> Tuple[Optional[int], Optional[int]]:
        """Return configured historical PrEP stock floor/ceiling for this PrEP type and year."""
        prep_schedule_config = self.config.get("prep_schedules", {}).get(prep_name, {})
        corridor = extract_value(
            prep_schedule_config.get("advanced", {})
            .get("historical_stock_corridor", {})
        )
        if not corridor or not bool(extract_value(corridor.get("enabled", False))):
            return None, None

        start_year = corridor.get("start_year")
        end_year = corridor.get("end_year")
        if start_year is not None and current_year < float(extract_value(start_year)):
            return None, None
        if end_year is not None and current_year >= float(extract_value(end_year)) + 1.0:
            return None, None

        interpolation = str(extract_value(corridor.get("interpolation", {"value": "stepwise"})))

        def scheduled_bound(key: str) -> Optional[int]:
            schedule = extract_value(corridor.get(key, {}))
            if not schedule:
                return None
            value = TimeSchedule(schedule, interpolation=interpolation).get_value(current_year)
            if not np.isfinite(value):
                return None
            return max(int(round(value)), 0)

        floor = scheduled_bound("stock_floor_schedule")
        ceiling = scheduled_bound("stock_ceiling_schedule")
        if floor is not None and ceiling is not None and floor > ceiling:
            raise ValueError(
                f"{prep_name} historical_stock_corridor has floor {floor} "
                f"above ceiling {ceiling} at year {current_year:.2f}"
            )
        return floor, ceiling

    def _apply_prep_stock_corridor(
        self,
        prep_name: str,
        prep_idx: int,
        current_year: float,
        prep_config: dict,
        eligible_orient_indices: list[int],
        eligible_act_indices: list[int],
        age_min_idx: int,
        age_max_idx: int,
        modifiers_raw: dict,
    ) -> None:
        """
        Apply a configured historical PrEP stock floor/ceiling after normal transitions.

        The corridor is an empirical programme guardrail for historical calibration.
        It is not a forward HTA uptake rule: configs should enable it only for the
        historical years where observed programme stock bounds are available.
        """
        floor, ceiling = self._prep_corridor_bounds(prep_name, current_year)
        if floor is None and ceiling is None:
            return

        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        none_idx = self.groups.prep_to_idx["None"]
        prep_users = self.state.population[:, :, :, susc_idx, :, :, prep_idx]
        current_stock = int(np.sum(prep_users))

        if ceiling is not None and current_stock > ceiling:
            excess = current_stock - ceiling
            reductions = self._allocate_fixed_events(prep_users, excess)
            self.state.population[:, :, :, susc_idx, :, :, prep_idx] -= reductions
            self.state.population[:, :, :, susc_idx, :, :, none_idx] += reductions
            self._prep_step_corridor_removals[prep_name] += int(np.sum(reductions))
            current_stock = int(np.sum(self.state.population[:, :, :, susc_idx, :, :, prep_idx]))

        if floor is None or current_stock >= floor:
            return

        needed = floor - current_stock
        if needed <= 0:
            return

        no_prep = self.state.population[:, :, :, susc_idx, :, :, none_idx]
        capacity = np.zeros_like(no_prep, dtype=np.int64)
        weights = np.zeros_like(no_prep, dtype=float)

        for orient_idx in eligible_orient_indices:
            for act_idx in eligible_act_indices:
                act_name = self.groups.activities[act_idx]
                modifier = float(modifiers_raw.get(act_name, 1.0))
                if modifier <= 0:
                    continue
                capacity[age_min_idx:age_max_idx, orient_idx, act_idx, :, :] = (
                    no_prep[age_min_idx:age_max_idx, orient_idx, act_idx, :, :]
                )
                weights[age_min_idx:age_max_idx, orient_idx, act_idx, :, :] = modifier

        additions = self._allocate_fixed_events(capacity, needed, weights=weights)
        if int(np.sum(additions)) == 0:
            return

        self.state.population[:, :, :, susc_idx, :, :, none_idx] -= additions
        self.state.population[:, :, :, susc_idx, :, :, prep_idx] += additions
        self._prep_step_initiations[prep_name] += int(np.sum(additions))
        self._prep_step_corridor_additions[prep_name] += int(np.sum(additions))

    def _step_lena_waning_transition(self):
        """
        V12.2: Deterministic Lenacapavir_Waning → None transition.

        Called at the START of step 7 (before _step_prep_transitions), so waning
        users receive their 1-quarter residual protection at step 3 (transmission)
        before being moved to None.

        Timing contract:
          - Last step 7: lena dropout fires → Lenacapavir → Lenacapavir_Waning
          - This step 3: transmission — Waning users have efficacy=0.99 (correct)
          - This step 7 (here): Waning → None
          - Next step 3: users are in None → efficacy=0.00

        No-op when Lenacapavir_Waning is not registered (lena disabled).
        Only operates on Susceptible slice: HIV+ individuals cannot be in a PrEP state.
        """
        waning_idx = self.groups.prep_to_idx.get("Lenacapavir_Waning", -1)
        if waning_idx < 0:
            return  # Lena disabled — no waning state

        none_idx  = self.groups.prep_to_idx["None"]
        susc_idx  = self.groups.hiv_to_idx["Susceptible"]

        waning_pop = self.state.population[:, :, :, susc_idx, :, :, waning_idx].copy()
        if np.sum(waning_pop) == 0:
            return  # Fast-path: nothing in waning state

        self.state.population[:, :, :, susc_idx, :, :, waning_idx] -= waning_pop
        self.state.population[:, :, :, susc_idx, :, :, none_idx]   += waning_pop

    def _parse_epidemic_seed_stage_distribution(self, epidemic_seed_config: dict):
        """
        Optional fixed-total HIV stage mix for epidemic seeding.

        Schema:
          population.epidemic_seed.stage_distribution.value:
            {"Acute": 0.90, "Chronic": 0.05, "AIDS": 0.05}

        If absent, the legacy single `seed_stage` behavior is used.
        """
        raw = epidemic_seed_config.get("stage_distribution")
        if not raw:
            return None
        distribution = extract_value(raw)
        if not distribution:
            return None
        if not isinstance(distribution, dict):
            raise ValueError("population.epidemic_seed.stage_distribution must be a dict")

        allowed = {"Acute", "Chronic", "AIDS"}
        parsed = {}
        for stage, share in distribution.items():
            if stage not in allowed:
                raise ValueError(
                    "population.epidemic_seed.stage_distribution contains "
                    f"unsupported HIV stage '{stage}'"
                )
            share = float(extract_value(share))
            if share < 0.0:
                raise ValueError(
                    "population.epidemic_seed.stage_distribution shares must be "
                    f"non-negative, got {stage}={share}"
                )
            parsed[stage] = share
        for stage in allowed:
            parsed.setdefault(stage, 0.0)
        total = sum(parsed.values())
        if not np.isclose(total, 1.0, rtol=0.0, atol=1e-9):
            raise ValueError(
                "population.epidemic_seed.stage_distribution shares must sum "
                f"to 1.0, got {total:.12f}"
            )
        return parsed

    def _parse_orientation_seed_stage_dist(self, epidemic_seed_config: dict, orientation_key: str):
        """
        V13.11: Optional per-orientation HIV stage mix for epidemic seeding.

        Schema (under population.epidemic_seed):
          stage_distribution_by_orientation:
            enabled: {value: true}
            value:
              MSM:     {Acute: 0.90, Chronic: 0.08, AIDS: 0.02}
              non_MSM: {Acute: 0.70, Chronic: 0.25, AIDS: 0.05}

        Returns a validated {stage: share} dict, or None if absent/disabled.
        orientation_key must be "MSM" or "non_MSM".
        """
        raw_block = epidemic_seed_config.get("stage_distribution_by_orientation")
        if not raw_block:
            return None
        enabled = bool(extract_value(raw_block.get("enabled", {"value": False})))
        if not enabled:
            return None
        orient_map = extract_value(raw_block.get("value", {}))
        if not isinstance(orient_map, dict):
            return None
        orient_dist_raw = orient_map.get(orientation_key)
        if not orient_dist_raw:
            return None
        if not isinstance(orient_dist_raw, dict):
            raise ValueError(
                "population.epidemic_seed.stage_distribution_by_orientation"
                f".value.{orientation_key} must be a dict"
            )
        allowed = {"Acute", "Chronic", "AIDS"}
        parsed = {}
        for stage, share in orient_dist_raw.items():
            if stage not in allowed:
                raise ValueError(
                    f"orientation seed distribution '{orientation_key}' contains "
                    f"unsupported HIV stage '{stage}'"
                )
            share = float(extract_value(share))
            if share < 0.0:
                raise ValueError(
                    f"orientation seed distribution '{orientation_key}' shares must be "
                    f"non-negative, got {stage}={share}"
                )
            parsed[stage] = share
        for stage in allowed:
            parsed.setdefault(stage, 0.0)
        total = sum(parsed.values())
        if not np.isclose(total, 1.0, rtol=0.0, atol=1e-9):
            raise ValueError(
                f"orientation seed distribution '{orientation_key}' shares must sum "
                f"to 1.0, got {total:.12f}"
            )
        return parsed

    def _epidemic_seed_stage_counts(self, n_to_seed: int, distribution=None) -> dict:
        """Return {stage: count} dict for n_to_seed individuals.

        distribution: explicit stage distribution override (V13.11 orientation-specific).
        Falls back to self._epidemic_seed_stage_distribution, then legacy single-stage.
        """
        dist = distribution if distribution is not None else self._epidemic_seed_stage_distribution
        if dist is None:
            return {self._epidemic_seed_stage: n_to_seed}
        raw = {
            stage: n_to_seed * share
            for stage, share in dist.items()
        }
        counts = {stage: int(value) for stage, value in raw.items()}
        remainder = n_to_seed - sum(counts.values())
        if remainder > 0:
            order = sorted(raw, key=lambda stage: raw[stage] - counts[stage], reverse=True)
            for stage in order[:remainder]:
                counts[stage] += 1
        return {stage: count for stage, count in counts.items() if count > 0}

    def _apply_epidemic_seed(self, current_year: float) -> int:
        """
        V12.4: One-time epidemic introduction event.

        Seeds a proportion of the MSM and/or heterosexual subpopulation with HIV
        at seed_year. Allocation across age groups and activity classes is
        proportional to the existing population tensor — no age band or activity
        class is imposed. Justified by van Griensven et al. 1987 finding that age
        was not a significant predictor of seropositivity in the early Dutch MSM
        epidemic.

        Returns:
            int: Total individuals seeded (for mass balance accounting).

        Raises:
            ValueError: If target cells have insufficient susceptibles.
        """
        susc_idx  = self.state.groups.hiv_to_idx["Susceptible"]
        undiag_idx = 0
        no_art_idx = 0
        no_prep_idx = 0

        msm_orient_idx    = self.state.groups.orient_to_idx.get("MSM")
        hetero_m_orient_idx = self.state.groups.orient_to_idx.get("Heterosexual_Male")
        female_orient_idx = self.state.groups.orient_to_idx.get("Female")

        msm_frac = self._epidemic_seed_msm_frac
        het_frac = 1.0 - msm_frac
        total_seeded = 0

        def count_susceptibles(orient_idx):
            if orient_idx is None:
                return 0
            return int(np.sum(
                self.state.population[:, orient_idx, :,
                                      susc_idx, undiag_idx, no_art_idx, no_prep_idx]
            ))

        n_msm_susc  = count_susceptibles(msm_orient_idx)
        n_hetm_susc = count_susceptibles(hetero_m_orient_idx)
        n_fem_susc  = count_susceptibles(female_orient_idx)
        n_het_susc  = n_hetm_susc + n_fem_susc

        n_seed_msm = int(n_msm_susc  * self._epidemic_seed_prev * msm_frac)
        n_seed_het = int(n_het_susc  * self._epidemic_seed_prev * het_frac)

        def apply_proportional_seed(orient_idx, n_to_seed, orientation_dist=None):
            """Distribute n_to_seed proportionally across age x activity cells.

            orientation_dist: V13.11 orientation-specific stage distribution override.
            When None, falls back to the shared distribution.
            """
            nonlocal total_seeded
            if orient_idx is None or n_to_seed == 0:
                return
            susc_slice = self.state.population[
                :, orient_idx, :, susc_idx, undiag_idx, no_art_idx, no_prep_idx
            ].copy()
            total_susc = int(np.sum(susc_slice))
            if total_susc == 0:
                raise ValueError(
                    f"_apply_epidemic_seed: orient_idx={orient_idx} has 0 "
                    f"susceptibles at year={current_year:.2f}. Cannot seed."
                )
            if n_to_seed > total_susc:
                raise ValueError(
                    f"_apply_epidemic_seed: requested seed ({n_to_seed:,}) exceeds "
                    f"susceptibles ({total_susc:,}) for orient_idx={orient_idx}."
                )
            weights = susc_slice / total_susc
            for stage_name, stage_n in self._epidemic_seed_stage_counts(n_to_seed, distribution=orientation_dist).items():
                stage_idx = self.state.groups.hiv_to_idx[stage_name]
                allocs = (weights * stage_n).astype(int)
                remainder = stage_n - int(np.sum(allocs))
                if remainder > 0:
                    largest = np.unravel_index(np.argmax(susc_slice), susc_slice.shape)
                    allocs[largest] += remainder
                for age_idx in range(self.state.groups.n_age_groups):
                    for act_idx in range(self.state.groups.n_activities):
                        n = int(allocs[age_idx, act_idx])
                        if n > 0:
                            self.state.population[
                                age_idx, orient_idx, act_idx,
                                susc_idx, undiag_idx, no_art_idx, no_prep_idx
                            ] -= n
                            self.state.population[
                                age_idx, orient_idx, act_idx,
                                stage_idx, undiag_idx, no_art_idx, no_prep_idx
                            ] += n
                            total_seeded += n

        apply_proportional_seed(msm_orient_idx, n_seed_msm,
                                self._epidemic_seed_stage_dist_msm)

        if n_seed_het > 0 and n_het_susc > 0:
            n_seed_fem  = int(n_seed_het * (n_fem_susc  / max(n_het_susc, 1)))
            n_seed_hetm = n_seed_het - n_seed_fem
            apply_proportional_seed(female_orient_idx,   n_seed_fem,
                                    self._epidemic_seed_stage_dist_non_msm)
            apply_proportional_seed(hetero_m_orient_idx, n_seed_hetm,
                                    self._epidemic_seed_stage_dist_non_msm)

        if self._epidemic_seed_stage_dist_msm is not None or self._epidemic_seed_stage_dist_non_msm is not None:
            def _dist_lbl(d):
                if not d:
                    return "shared"
                return ",".join(f"{s}={v:.3f}" for s, v in d.items() if v > 0)
            stage_label = (
                f"MSM=[{_dist_lbl(self._epidemic_seed_stage_dist_msm)}] "
                f"non_MSM=[{_dist_lbl(self._epidemic_seed_stage_dist_non_msm)}]"
            )
        elif self._epidemic_seed_stage_distribution is None:
            stage_label = self._epidemic_seed_stage
        else:
            stage_label = ", ".join(
                f"{stage}={share:.3f}"
                for stage, share in self._epidemic_seed_stage_distribution.items()
                if share > 0
            )
        print(f"  [V12.4] Epidemic seed fired at year={current_year:.2f}: "
              f"{total_seeded:,} → {stage_label} "
              f"(msm_frac={msm_frac:.2f}, seed_prev={self._epidemic_seed_prev:.4f})")
        return total_seeded

    def _step_hiv_mortality(self, current_year: float) -> dict:
        """Apply HIV-related mortality across all HIV stages.

        Returns:
            dict with keys:
              "total" (int) — total HIV deaths across Acute + Chronic + AIDS
              "aids"  (int) — deaths from AIDS stage only; used to route
                              terminal care cost in the economics accumulator
                              (V11.7.3: billing_moment="death", no step_fraction).
        """
        disease_config = self.config["disease"]
        hiv_deaths  = 0
        aids_deaths = 0

        hiv_mortality = {
            "Acute":   self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["acute"])),
            "Chronic": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["chronic"])),
            "AIDS":    self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["aids"])),
        }
        if self.art_mortality_reduction_schedule is not None:
            art_reduction = self.art_mortality_reduction_schedule.get_value(current_year)
        else:
            art_reduction = extract_value(disease_config["treatment"]["art_mortality_reduction"])
        art_reduction = float(art_reduction)
        if not 0.0 <= art_reduction <= 1.0:
            raise ValueError(
                "ART mortality reduction must be between 0 and 1, "
                f"got {art_reduction} at year {current_year:.2f}"
            )

        for stage_name, mortality_rate in hiv_mortality.items():
            stage_idx = self.groups.hiv_to_idx[stage_name]

            not_art = self.state.population[:, :, :, stage_idx, :, 0, :]
            deaths_not_art = self._draw_events(not_art, mortality_rate)
            self.state.population[:, :, :, stage_idx, :, 0, :] -= deaths_not_art

            on_art = self.state.population[:, :, :, stage_idx, :, 1, :]
            reduced_rate = mortality_rate * (1 - art_reduction)
            deaths_art = self._draw_events(on_art, reduced_rate)
            self.state.population[:, :, :, stage_idx, :, 1, :] -= deaths_art

            step_deaths  = int(np.sum(deaths_not_art) + np.sum(deaths_art))
            hiv_deaths  += step_deaths
            if stage_name == "AIDS":
                aids_deaths += step_deaths

        return {"total": hiv_deaths, "aids": aids_deaths}

    def _aids_state_counts(self) -> dict:
        """Return AIDS stock counts used by the V13.16 event-order audit."""
        aids_idx = self.groups.hiv_to_idx["AIDS"]
        aids = self.state.population[:, :, :, aids_idx, :, :, :]
        return {
            "stock": int(np.sum(aids)),
            "off_art": int(np.sum(aids[:, :, :, :, 0, :])),
            "on_art": int(np.sum(aids[:, :, :, :, 1, :])),
            "undiagnosed": int(np.sum(aids[:, :, :, 0, :, :])),
            "diagnosed": int(np.sum(aids[:, :, :, 1, :, :])),
        }

    def record_outputs(self, step: int, year: float, new_inf: int, hiv_d: int,
                       bg_d: int, child_d: int, births: int,
                       continuous_debuts: int, flush_debuts: int, total_debuts: int,
                       net_migration: int = 0,
                       migrant_hiv_stats: dict = None,
                       susceptible_pre_tx: int = None,
                       aids_deaths: int = 0,
                       new_aids_cases: int = 0,
                       new_diagnoses: int = 0,
                       new_diagnoses_msm: int = 0,
                       art_initiations: int = 0,
                       art_initiations_by_stage: dict = None,
                       aids_start_counts: dict = None,
                       aids_pre_mortality_counts: dict = None,
                       event_order_variant: str = "current"):
        """
        Record timestep outputs.

        GUARDRAILS (V4.3): Separately tracks continuous vs flush debuts for observability.
        V11.4 (B4): incidence_rate uses susceptible_pre_tx (captured before step 3) as
        denominator, annualized by dividing by years_per_step.
        Units: new infections per susceptible person per year.
        """
        active_pop = self.state.get_total_population()
        hiv_pos = self.state.get_hiv_positive()
        childhood_pop = self.demography.childhood.get_total()
        pop = self.state.population

        # Prevalence based on sexually active population only (NOT children)
        prevalence = hiv_pos / active_pop if active_pop > 0 else 0
        on_art_stock = int(np.sum(pop[:, :, :, 1:, :, 1, :]))
        female_idx = self.groups.orient_to_idx.get("Female")
        hetero_male_idx = self.groups.orient_to_idx.get("Heterosexual_Male")
        msm_idx = self.groups.orient_to_idx.get("MSM")
        plhiv_female = (
            int(np.sum(pop[:, female_idx, :, 1:, :, :, :]))
            if female_idx is not None else 0
        )
        plhiv_male = 0
        for orient_idx in (hetero_male_idx, msm_idx):
            if orient_idx is not None:
                plhiv_male += int(np.sum(pop[:, orient_idx, :, 1:, :, :, :]))
        # Children are not represented in HIV disease states in the current model.
        plhiv_child = 0

        # B4 (V11.4): Correct incidence denominator and annualize.
        # susceptible_pre_tx is the at-risk population at the moment infections were drawn.
        # Dividing by years_per_step converts per-step rate to per-year rate.
        # Fallback to post-step susceptible only if pre_tx was not captured (backward compat).
        denom = susceptible_pre_tx if (susceptible_pre_tx is not None and susceptible_pre_tx > 0) \
            else self.state.get_susceptible()
        incidence_rate = (new_inf / denom / self.time.years_per_step) if denom > 0 else 0

        # PrEP coverage
        prep_coverage = {}
        for prep_name in ['Oral_PrEP', 'Lenacapavir']:
            if prep_name in self.groups.prep_states:
                prep_idx = self.groups.prep_to_idx[prep_name]
                prep_count = np.sum(self.state.population[:, :, :, 0, :, :, prep_idx])
                prep_coverage[prep_name] = prep_count / active_pop if active_pop > 0 else 0
            else:
                prep_coverage[prep_name] = 0

        self.outputs['step'].append(step)
        self.outputs['year'].append(year)
        self.outputs['new_infections'].append(new_inf)
        self.outputs['prevalence'].append(prevalence)
        self.outputs['incidence_rate'].append(incidence_rate)
        self.outputs['hiv_deaths'].append(hiv_d)
        self.outputs['aids_deaths'].append(aids_deaths)
        self.outputs['new_aids_cases'].append(new_aids_cases)
        self.outputs['new_diagnoses'].append(new_diagnoses)
        self.outputs['new_diagnoses_msm'].append(new_diagnoses_msm)
        self.outputs['art_initiations'].append(art_initiations)
        art_stage_counts = art_initiations_by_stage or {}
        self.outputs['art_initiations_acute'].append(art_stage_counts.get("Acute", 0))
        self.outputs['art_initiations_chronic'].append(art_stage_counts.get("Chronic", 0))
        self.outputs['art_initiations_aids'].append(art_stage_counts.get("AIDS", 0))
        self.outputs['on_art_stock'].append(on_art_stock)
        self.outputs['adult_hiv_pos'].append(hiv_pos)
        self.outputs['plhiv_female_15plus'].append(plhiv_female)
        self.outputs['plhiv_male_15plus'].append(plhiv_male)
        self.outputs['plhiv_child_0_14'].append(plhiv_child)
        self.outputs['background_deaths'].append(bg_d)
        self.outputs['childhood_deaths'].append(child_d)
        self.outputs['total_population'].append(active_pop + childhood_pop)
        self.outputs['sexually_active_population'].append(active_pop)
        self.outputs['childhood_population'].append(childhood_pop)
        self.outputs['births'].append(births)
        self.outputs['sexual_debuts'].append(total_debuts)
        self.outputs['continuous_debuts'].append(continuous_debuts)
        self.outputs['flush_debuts'].append(flush_debuts)
        self.outputs['net_migration'].append(net_migration)   # V9
        mh = migrant_hiv_stats or {}
        self.outputs['migrant_hiv_entries'].append(mh.get("migrant_hiv_entries", 0))
        self.outputs['migrant_hiv_on_art_entries'].append(mh.get("migrant_hiv_on_art_entries", 0))
        self.outputs['migrant_hiv_msm_entries'].append(mh.get("migrant_hiv_msm_entries", 0))
        self.outputs['migrant_hiv_non_msm_entries'].append(
            mh.get("migrant_hiv_hetero_male_entries", 0) + mh.get("migrant_hiv_female_entries", 0)
        )
        self.outputs['migrant_hiv_hetero_male_entries'].append(mh.get("migrant_hiv_hetero_male_entries", 0))
        self.outputs['migrant_hiv_female_entries'].append(mh.get("migrant_hiv_female_entries", 0))
        self.outputs['migrant_hiv_requested_entries'].append(mh.get("migrant_hiv_requested_entries", 0))
        self.outputs['migrant_hiv_capped_entries'].append(mh.get("migrant_hiv_capped_entries", 0))
        self.outputs['migrant_hiv_cap_shortfall'].append(mh.get("migrant_hiv_cap_shortfall", 0))
        self.outputs['prep_coverage_Oral_PrEP'].append(prep_coverage['Oral_PrEP'])
        self.outputs['prep_coverage_Lenacapavir'].append(prep_coverage['Lenacapavir'])

        # V11.8.0: MSM sub-population counts ─────────────────────────────────
        susc_idx  = self.groups.hiv_to_idx["Susceptible"]
        msm_idx   = self.groups.orient_to_idx.get("MSM")
        high_idx  = self.groups.activity_to_idx.get("High")
        none_prep = self.groups.prep_to_idx.get("None", 0)
        oral_prep = self.groups.prep_to_idx.get("Oral_PrEP", -1)
        lena_prep = self.groups.prep_to_idx.get("Lenacapavir", -1)

        if msm_idx is not None:
            msm_total        = int(np.sum(pop[:, msm_idx, :, :, :, :, :]))
            msm_susceptible  = int(np.sum(pop[:, msm_idx, :, susc_idx, :, :, :]))
            msm_hiv_pos      = msm_total - msm_susceptible
            msm_undiagnosed_hiv_pos = int(np.sum(pop[:, msm_idx, :, 1:, 0, :, :]))

            # Eligible = HIV-neg, not already on PrEP.
            # Use Oral_PrEP eligibility age range as reference (both drugs share same range).
            # V11.8.4: self.config["interventions"] uses CamelCase (translated by ConfigReader)
            prep_cfg = self.config.get("interventions", {})
            if "Oral_PrEP" in prep_cfg:
                age_range_raw = extract_value(
                    prep_cfg["Oral_PrEP"].get(
                        "eligible_age_range",
                        {"value": {"min": self.groups.age_groups[0],
                                   "max": self.groups.age_groups[-1]}}
                    )
                )
                elig_age_min = self.groups.age_to_idx.get(age_range_raw["min"], 0)
                elig_age_max = self.groups.age_to_idx.get(
                    age_range_raw["max"], self.groups.n_age_groups - 1
                ) + 1
            else:
                elig_age_min = 0
                elig_age_max = self.groups.n_age_groups

            # V11.8.5: denominator = ALL HIV-neg MSM in eligible age range (on OR off PrEP).
            # Using only none_prep caused coverage > 100% as the not-on-PrEP pool shrinks.
            msm_eligible_prep = int(np.sum(
                pop[elig_age_min:elig_age_max, msm_idx, :, susc_idx, :, :, :]
            ))

            msm_high_risk = int(np.sum(pop[:, msm_idx, high_idx, :, :, :, :])) \
                if high_idx is not None else 0
            msm_high_risk_susceptible = int(np.sum(pop[:, msm_idx, high_idx, susc_idx, :, :, :])) \
                if high_idx is not None else 0

            # High-risk MSM on any PrEP (oral + lena)
            msm_high_risk_on_prep = 0
            if high_idx is not None:
                for pi in [oral_prep, lena_prep]:
                    if pi >= 0:
                        msm_high_risk_on_prep += int(np.sum(
                            pop[:, msm_idx, high_idx, susc_idx, :, :, pi]
                        ))

            msm_on_oral = int(np.sum(pop[:, msm_idx, :, susc_idx, :, :, oral_prep])) \
                if oral_prep >= 0 else 0
            msm_on_lena = int(np.sum(pop[:, msm_idx, :, susc_idx, :, :, lena_prep])) \
                if lena_prep >= 0 else 0
        else:
            msm_total = msm_susceptible = msm_hiv_pos = 0
            msm_undiagnosed_hiv_pos = 0
            msm_eligible_prep = msm_high_risk = msm_high_risk_susceptible = 0
            msm_high_risk_on_prep = msm_on_oral = msm_on_lena = 0

        non_msm_hiv_pos = int(hiv_pos) - msm_hiv_pos
        non_msm_undiagnosed_hiv_pos = int(np.sum(pop[:, :, :, 1:, 0, :, :])) - msm_undiagnosed_hiv_pos

        self.outputs['msm_total'].append(msm_total)
        self.outputs['msm_susceptible'].append(msm_susceptible)
        self.outputs['msm_eligible_prep'].append(msm_eligible_prep)
        self.outputs['msm_high_risk'].append(msm_high_risk)
        self.outputs['msm_high_risk_susceptible'].append(msm_high_risk_susceptible)
        self.outputs['msm_high_risk_on_prep'].append(msm_high_risk_on_prep)
        self.outputs['msm_hiv_pos'].append(msm_hiv_pos)
        self.outputs['non_msm_hiv_pos'].append(non_msm_hiv_pos)
        self.outputs['msm_undiagnosed_hiv_pos'].append(msm_undiagnosed_hiv_pos)
        self.outputs['non_msm_undiagnosed_hiv_pos'].append(non_msm_undiagnosed_hiv_pos)
        self.outputs['msm_on_oral'].append(msm_on_oral)
        self.outputs['msm_on_lena'].append(msm_on_lena)

        # V11.8.0: PrEP flow counters (set by _step_prep_transitions this step) ──
        self.outputs['prep_oral_initiations'].append(
            self._prep_step_initiations.get('Oral_PrEP', 0)
        )
        self.outputs['prep_lena_initiations'].append(
            self._prep_step_initiations.get('Lenacapavir', 0)
        )
        self.outputs['prep_oral_dropouts'].append(
            self._prep_step_dropouts.get('Oral_PrEP', 0)
        )
        self.outputs['prep_lena_dropouts'].append(
            self._prep_step_dropouts.get('Lenacapavir', 0)
        )
        self.outputs['prep_oral_corridor_additions'].append(
            self._prep_step_corridor_additions.get('Oral_PrEP', 0)
        )
        self.outputs['prep_lena_corridor_additions'].append(
            self._prep_step_corridor_additions.get('Lenacapavir', 0)
        )
        self.outputs['prep_oral_corridor_removals'].append(
            self._prep_step_corridor_removals.get('Oral_PrEP', 0)
        )
        self.outputs['prep_lena_corridor_removals'].append(
            self._prep_step_corridor_removals.get('Lenacapavir', 0)
        )
        self.outputs['prep_oral_target_additions'].append(
            self._prep_step_target_additions.get('Oral_PrEP', 0)
        )
        self.outputs['prep_lena_target_additions'].append(
            self._prep_step_target_additions.get('Lenacapavir', 0)
        )
        self.outputs['prep_lena_switches'].append(
            self._prep_step_switches.get('Lenacapavir', 0)
        )
        self.outputs['prep_lena_switch_shortfall'].append(
            self._prep_step_switch_shortfall.get('Lenacapavir', 0)
        )

        # V11.8.0: Stratified new infections from last transmission step ──────
        # infections_array shape is [age, orient, activity, prep] (4D, susceptibles only)
        if self._last_infections_array is not None and msm_idx is not None:
            arr = self._last_infections_array
            new_inf_msm = int(np.sum(arr[:, msm_idx, :, :]))
            new_inf_non_msm = new_inf - new_inf_msm
            new_inf_msm_on_prep = 0
            for pi in [oral_prep, lena_prep]:
                if pi >= 0:
                    new_inf_msm_on_prep += int(np.sum(arr[:, msm_idx, :, pi]))
        else:
            new_inf_msm = 0
            new_inf_non_msm = 0
            new_inf_msm_on_prep = 0

        self.outputs['new_infections_msm'].append(new_inf_msm)
        self.outputs['new_infections_non_msm'].append(new_inf_non_msm)
        self.outputs['new_infections_msm_on_prep'].append(new_inf_msm_on_prep)

        # V13.16: event-order audit observability. The rescue proxy is not an
        # exact person-level path; it is a conservative stock-flow indicator of
        # possible same-step AIDS progression followed by AIDS-stage ART start.
        start_counts = aids_start_counts or {}
        pre_mort_counts = aids_pre_mortality_counts or {}
        aids_art_inits = int(art_stage_counts.get("AIDS", 0))
        self.outputs['diagnostic_event_order_variant'].append(event_order_variant)
        self.outputs['aids_stock_start'].append(start_counts.get("stock", 0))
        self.outputs['aids_off_art_start'].append(start_counts.get("off_art", 0))
        self.outputs['aids_on_art_start'].append(start_counts.get("on_art", 0))
        self.outputs['aids_undiagnosed_start'].append(start_counts.get("undiagnosed", 0))
        self.outputs['aids_diagnosed_start'].append(start_counts.get("diagnosed", 0))
        self.outputs['aids_stock_pre_mortality'].append(pre_mort_counts.get("stock", 0))
        self.outputs['aids_off_art_pre_mortality'].append(pre_mort_counts.get("off_art", 0))
        self.outputs['aids_on_art_pre_mortality'].append(pre_mort_counts.get("on_art", 0))
        self.outputs['aids_undiagnosed_pre_mortality'].append(pre_mort_counts.get("undiagnosed", 0))
        self.outputs['aids_diagnosed_pre_mortality'].append(pre_mort_counts.get("diagnosed", 0))
        self.outputs['aids_art_initiations'].append(aids_art_inits)
        self.outputs['same_step_aids_art_rescue_proxy'].append(
            int(min(new_aids_cases, aids_art_inits))
        )

    def validate_global_population_balance(self, step: int,
                                           prev_total: int,
                                           births: int,
                                           hiv_deaths: int,
                                           bg_deaths: int,
                                           childhood_deaths: int,
                                           adjustment_change: int = 0,
                                           migration_change: int = 0,
                                           seed_change: int = 0):  # V12.4
        """
        Validate global population mass balance (PATCH 2 / V9 updated).

        Ensures no silent creation/loss of people due to ordering/transition bugs.

        Formula:
            next_total = prev_total
                         + births
                         - bg_deaths - hiv_deaths - childhood_deaths
                         + adjustment_change
                         + migration_change          ← V9 addition

        Args:
            step:              Current timestep number
            prev_total:        Total population before this timestep
            births:            Number of births this timestep
            hiv_deaths:        Number of HIV deaths this timestep
            bg_deaths:         Number of background deaths this timestep
            childhood_deaths:  Number of childhood deaths this timestep
            adjustment_change: Population change from exogenous adjustments
            migration_change:  ACTUAL net population change from migration
                               (returned by MigrationModule.apply — always 0
                               when migration is disabled)

        Raises:
            AssertionError: If mass balance is violated
        """
        current_year = self.time.start_year + (step * self.time.years_per_step)

        # Calculate current total population
        current_active = self.state.get_total_population()
        current_childhood = self.demography.childhood.get_total()
        current_total = current_active + current_childhood

        # Expected total based on flows
        expected_total = (
            prev_total
            + births
            - bg_deaths
            - hiv_deaths
            - childhood_deaths
            + adjustment_change
            + migration_change
            # seed_change intentionally excluded: seeding moves people between
            # compartments (Susceptible -> HIV stage), not new individuals.
            # prev_total already includes those people. V12.4.
        )

        # Check mass balance (allow small numerical errors)
        diff = abs(current_total - expected_total)

        if diff > 0.5:  # Tolerance for floating point rounding
            error_msg = (
                f"\n{'='*70}\n"
                f"GLOBAL POPULATION MASS-BALANCE ASSERTION FAILED\n"
                f"{'='*70}\n"
                f"Step: {step}\n"
                f"Year: {current_year:.1f}\n\n"
                f"Population accounting error detected!\n\n"
                f"Expected: {expected_total:,} people\n"
                f"Actual:   {current_total:,} people\n"
                f"Difference: {diff:,.0f} people\n\n"
                f"Components:\n"
                f"  Previous total:      {prev_total:,}\n"
                f"  + Births:            {births:,}\n"
                f"  - HIV deaths:        {hiv_deaths:,}\n"
                f"  - Background deaths: {bg_deaths:,}\n"
                f"  - Childhood deaths:  {childhood_deaths:,}\n"
                f"  + Adjustment change: {adjustment_change:,}\n"
                f"  + Migration change:  {migration_change:,}\n"
                f"  + Seed change:       {seed_change:,}\n"
                f"  = Expected:          {expected_total:,}\n\n"
                f"Current breakdown:\n"
                f"  Sexually active:    {current_active:,}\n"
                f"  Childhood pool:     {current_childhood:,}\n"
                f"  = Actual total:     {current_total:,}\n\n"
                f"{'='*70}\n"
                f"This indicates a model accounting error.\n"
                f"Check for silent population creation/loss in transitions.\n"
                f"{'='*70}\n"
            )
            raise AssertionError(error_msg)

    def validate_prep_mass_balance(self, step: int):
        """
        Validate PrEP mass balance at current timestep.

        Ensures that for every state slice:
        Total population = sum across all PrEP states

        This catches accounting errors immediately rather than at output time.

        Args:
            step: Current timestep number

        Raises:
            AssertionError: If mass balance is violated
        """
        # Sum population across all PrEP types
        pop_by_prep = np.sum(self.state.population, axis=(4, 5, 6))  # Sum over diagnosis, ART, PrEP

        # Sum across non-PrEP dimensions to check PrEP accounting
        # population shape: (age, orient, activity, hiv, diagnosis, art, prep)
        # Sum over diagnosis and ART to get (age, orient, activity, hiv, prep)
        pop_with_prep = np.sum(self.state.population, axis=(4, 5))

        # For each (age, orient, activity, hiv) slice, verify sum across PrEP equals total
        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for activity_idx in range(self.groups.n_activities):
                    for hiv_idx in range(self.groups.n_hiv_stages):
                        # Total population in this state (across all PrEP types)
                        total = pop_by_prep[age_idx, orient_idx, activity_idx, hiv_idx]

                        # Sum across PrEP dimension
                        prep_sum = np.sum(pop_with_prep[age_idx, orient_idx, activity_idx, hiv_idx, :])

                        # Check mass balance (allow small numerical errors)
                        diff = abs(total - prep_sum)
                        if diff > 0.5:  # Tolerance for floating point
                            current_year = self.time.start_year + (step * self.time.years_per_step)

                            # Get state names for error message
                            age_name = self.groups.age_groups[age_idx]
                            orient_name = self.groups.orientations[orient_idx]
                            activity_name = self.groups.activities[activity_idx]
                            hiv_name = self.groups.hiv_stages[hiv_idx]

                            # Get PrEP breakdown
                            prep_counts = pop_with_prep[age_idx, orient_idx, activity_idx, hiv_idx, :]
                            prep_breakdown = {
                                name: int(prep_counts[idx])
                                for idx, name in enumerate(self.groups.prep_states)
                            }

                            error_msg = (
                                f"\n{'='*70}\n"
                                f"MASS-BALANCE ASSERTION FAILED (Step {step}, Year {current_year:.1f})\n"
                                f"{'='*70}\n"
                                f"PrEP accounting error detected during simulation!\n\n"
                                f"State: {age_name}, {orient_name}, {activity_name}, {hiv_name}\n"
                                f"  Total population:     {total:.0f}\n"
                                f"  Sum across PrEP:      {prep_sum:.0f}\n"
                                f"  Difference:           {diff:.0f}\n\n"
                                f"PrEP breakdown:\n"
                            )
                            for prep_name, count in prep_breakdown.items():
                                error_msg += f"  {prep_name}: {count}\n"

                            error_msg += (
                                f"\n{'='*70}\n"
                                f"This indicates a model accounting error in PrEP transitions.\n"
                                f"Check _step_prep_transitions() and related logic.\n"
                                f"{'='*70}\n"
                            )

                            raise AssertionError(error_msg)

    def validate_art_mass_balance(self, step: int):
        """
        Validate ART mass balance at current timestep (PATCH 3).

        Ensures that for every state slice:
        Total population = pop_art_off + pop_art_on

        Parallel to PrEP mass balance, catches ART accounting errors.

        Args:
            step: Current timestep number

        Raises:
            AssertionError: If mass balance is violated
        """
        # Population shape: (age, orient, activity, hiv, diagnosis, art, prep)
        # Sum over diagnosis and PrEP to get (age, orient, activity, hiv, art)
        pop_with_art = np.sum(self.state.population, axis=(4, 6))

        # Sum over all dimensions except ART to check total
        pop_by_art = np.sum(self.state.population, axis=(4, 5, 6))

        # For each (age, orient, activity, hiv) slice, verify sum across ART equals total
        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for activity_idx in range(self.groups.n_activities):
                    for hiv_idx in range(self.groups.n_hiv_stages):
                        # Total population in this state
                        total = pop_by_art[age_idx, orient_idx, activity_idx, hiv_idx]

                        # Sum across ART dimension (off=0, on=1)
                        art_sum = np.sum(pop_with_art[age_idx, orient_idx, activity_idx, hiv_idx, :])

                        # Check mass balance (allow small numerical errors)
                        diff = abs(total - art_sum)
                        if diff > 0.5:  # Tolerance for floating point
                            current_year = self.time.start_year + (step * self.time.years_per_step)

                            # Get state names for error message
                            age_name = self.groups.age_groups[age_idx]
                            orient_name = self.groups.orientations[orient_idx]
                            activity_name = self.groups.activities[activity_idx]
                            hiv_name = self.groups.hiv_stages[hiv_idx]

                            # Get ART breakdown
                            art_counts = pop_with_art[age_idx, orient_idx, activity_idx, hiv_idx, :]
                            art_off = int(art_counts[0])
                            art_on = int(art_counts[1]) if len(art_counts) > 1 else 0

                            error_msg = (
                                f"\n{'='*70}\n"
                                f"ART MASS-BALANCE ASSERTION FAILED (Step {step}, Year {current_year:.1f})\n"
                                f"{'='*70}\n"
                                f"ART accounting error detected during simulation!\n\n"
                                f"State: {age_name}, {orient_name}, {activity_name}, {hiv_name}\n"
                                f"  Total population:     {total:.0f}\n"
                                f"  Sum across ART:       {art_sum:.0f}\n"
                                f"  Difference:           {diff:.0f}\n\n"
                                f"ART breakdown:\n"
                                f"  Off ART: {art_off}\n"
                                f"  On ART:  {art_on}\n\n"
                                f"{'='*70}\n"
                                f"This indicates a model accounting error in ART transitions.\n"
                                f"Check _step_art_transitions() and related logic.\n"
                                f"{'='*70}\n"
                            )

                            raise AssertionError(error_msg)


def simulate_v3(config_path: str, seed: int = None):
    """Run V10 simulation with all features."""
    with open(config_path, 'r') as f:
        raw_config = json.load(f)

    # Use ConfigReader for V6/V10 compatibility
    reader = ConfigReader(raw_config)
    config = reader.as_v6_dict()

    n_runs = extract_value(config["simulation_controls"].get("n_runs", {"value": 1}))
    base_seed = seed if seed is not None else extract_value(
        config["simulation_controls"].get("seed", {"value": 42})
    )

    all_results = []
    engine = None  # guard: engine is loop-bound; initialise so post-loop check is safe

    for run in range(n_runs):
        run_seed = base_seed + run
        engine = SimulationEngineV3(config_path, seed=run_seed)
        result = engine.run(run_number=run + 1)
        all_results.append(result['summary'])

    # Write aggregated outputs if multiple runs
    if engine is not None and n_runs > 1 and engine.output_writer.enabled:
        engine.output_writer.write_aggregated_outputs(all_results)
        engine.output_writer.print_multi_run_summary(all_results)

    return all_results


if __name__ == '__main__':
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.json'
    results = simulate_v3(config_path)
    print("\nV10 simulation complete!")
    print(f"  Results saved to outputs/")
