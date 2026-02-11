"""
HIV Simulation V4 - Main runner script

V4 features:
- Pre-sexual childhood pool (children 0-14, outside transmission system)
- Sexual debut mechanism (age 15: orientation + activity assignment)
- Corrected two-step transmission formulation
- File outputs (CSV/Excel), terminal summaries, multi-run aggregation
- Optional fertility/life tables

Population flow (V4):
  Birth -> Childhood (0-4, 5-9, 10-14) -> Debut (age 15)
       -> Orientation + Activity assignment -> HIV transmission system
"""

import sys
import os
from typing import Tuple

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_v3 import (
    TimeSystem, GroupSystem, ConfigValidator,
    PopulationState, extract_value
)
from transmission_corrected import TransmissionEngine
from output_writer import OutputWriter
from demography import DemographyModule
from schedule_utils import ScheduleManager, validate_schedule_before_intro  # V5
import json
import numpy as np
import pandas as pd
from pathlib import Path


class SimulationEngineV3:
    """
    V4 Simulation Engine with childhood pool, sexual debut,
    corrected two-step transmission, and robust outputs.

    Key V4 design principles:
    - Children (0-14) exist outside the HIV transmission system
    - No behavioral attributes (orientation, activity) assigned before debut
    - Sexual debut at configurable age with probabilistic assignment
    - All debutants enter as Susceptible/Undiagnosed/NoART/NoPrEP
    """

    def __init__(self, config_path: str, seed: int = None):
        # Load config
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        # Extract seed
        if seed is None:
            seed = extract_value(self.config["simulation_controls"].get("seed", {"value": 42}))
        self.seed = seed
        self.rng = np.random.RandomState(seed)

        # Initialize time system
        self.time = TimeSystem(self.config["time"])

        # Initialize group system
        self.groups = GroupSystem(self.config["groups"])

        # Add PrEP interventions
        for intervention_name, intervention_config in self.config["interventions"].items():
            if extract_value(intervention_config.get("enabled", {"value": True})):
                self.groups.add_prep_intervention(intervention_name)
        self.groups.finalize_prep_states()

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

        # Check initialization method to determine childhood pool handling
        init_method = extract_value(self.config["population"].get("initialization_method", {"value": "direct_counts"}))

        if init_method == "relative_weights":
            # New method: pass childhood_pool for integrated initialization
            self.state.initialize_from_config(self.config["population"], childhood_pool=self.demography.childhood)
        else:
            # Legacy method: initialize adults first, then derive childhood
            self.state.initialize_from_config(self.config["population"])
            self.demography.initialize_childhood(self.state.population)

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

        # Deterministic mode
        self.deterministic = extract_value(
            self.config["simulation_controls"].get("deterministic_mode", {"value": False})
        )

        # Storage for outputs
        self.outputs = {
            'step': [],
            'year': [],
            'new_infections': [],
            'prevalence': [],
            'incidence': [],
            'hiv_deaths': [],
            'background_deaths': [],
            'childhood_deaths': [],
            'total_population': [],
            'sexually_active_population': [],
            'childhood_population': [],
            'births': [],
            'sexual_debuts': [],
            'continuous_debuts': [],  # GUARDRAIL: separate tracking for observability
            'flush_debuts': [],       # GUARDRAIL: separate tracking for observability
            'prep_coverage_Oral_PrEP': [],
            'prep_coverage_Lenacapavir': []
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
        In deterministic mode, returns expected value.
        """
        if self.deterministic:
            return (n * p).astype(np.int64)
        else:
            return self.rng.binomial(n, p)

    def run(self, run_number: int = 1) -> dict:
        """Run simulation for one realization."""
        print(f"\nRunning simulation (run {run_number})...")

        for step in range(self.time.n_steps):
            self.step_simulation(step)

            # Validate PrEP mass balance after each timestep
            self.validate_prep_mass_balance(step)

            # PATCH 3: Validate ART mass balance after each timestep
            self.validate_art_mass_balance(step)

            if (step + 1) % 10 == 0:
                print(f"  Step {step + 1}/{self.time.n_steps}", end='\r')

        print(f"  Completed {self.time.n_steps} timesteps.    ")

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

        # Validate before step
        self.state.validate()
        self.demography.childhood.validate()

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

        # --- 2. Contacts via mixing matrices ---
        contacts = self._step_contacts_and_mixing()

        # --- 3. Transmission (corrected two-step) ---
        new_infections, infections_array = self.transmission_engine.calculate_transmission(
            self.state.population, contacts, self.rng, self.deterministic
        )
        self.state.population = self.transmission_engine.apply_infections_to_population(
            self.state.population, infections_array
        )

        # --- 4. Disease progression (Acute -> Chronic -> AIDS) ---
        self._step_disease_progression()

        # --- 5. Diagnosis ---
        self._step_diagnosis()

        # --- 6. ART transitions (V5: time-scheduled) ---
        self._step_art_transitions(current_year)

        # --- 7. PrEP transitions (V5: time-scheduled) ---
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
        hiv_deaths = self._step_hiv_mortality()
        bg_deaths_array = self.demography.calculate_background_mortality(
            self.state.population, current_year, self.rng, self.deterministic
        )
        self.state.population -= bg_deaths_array
        bg_deaths = np.sum(bg_deaths_array)

        # --- 11. Aging + sexual debut ---
        continuous_debuts, flush_debuts, total_debuts = self._step_aging_and_debut(step)

        # Validate after step
        self.state.validate()
        self.demography.childhood.validate()

        # PATCH 2: Validate global population mass balance
        self.validate_global_population_balance(
            step, prev_total, n_births, hiv_deaths, bg_deaths, childhood_deaths, adjustment_change
        )

        # --- 12. Record outputs ---
        self.record_outputs(
            step, current_year, new_infections, hiv_deaths,
            bg_deaths, childhood_deaths, n_births,
            continuous_debuts, flush_debuts, total_debuts
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

        if aging_method == "fractional":
            return self._step_aging_fractional(step)
        elif aging_method == "discrete":
            return self._step_aging_discrete(step)
        else:
            raise ValueError(f"Unknown aging_method: '{aging_method}'. Use 'fractional' or 'discrete'")

    def _step_aging_fractional(self, step: int) -> Tuple[int, int, int]:
        """FRACTIONAL aging: 20% annual transfer, smooth mortality."""
        continuous_debuts = 0

        # 1. Continuous sexual debut (EVERY timestep)
        debuting_females, debuting_males = self.demography.childhood.process_continuous_debut(
            self.time, self.rng, self.deterministic
        )

        if debuting_females > 0 or debuting_males > 0:
            self.state.population, continuous_debuts = self.demography.process_sexual_debut(
                debuting_females, debuting_males,
                self.state.population,
                self.rng, self.deterministic
            )

        # 2. Fractional aging (every timestep)
        self._apply_fractional_aging_adults()

        # Childhood aging returns children aging out for sexual debut
        aging_out_females, aging_out_males = self.demography.childhood.apply_fractional_aging(
            self.rng, self.deterministic
        )

        # Process sexual debut for children aging out
        aging_debuts = 0
        if aging_out_females > 0 or aging_out_males > 0:
            self.state.population, aging_debuts = self.demography.process_sexual_debut(
                aging_out_females, aging_out_males,
                self.state.population,
                self.rng, self.deterministic
            )

        total_debuts = continuous_debuts + aging_debuts
        return continuous_debuts, aging_debuts, total_debuts

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

    def _apply_fractional_aging_adults(self):
        """
        Apply fractional aging to adult population (PATCH: Fractional Aging 2026-02-10).

        Every timestep:
        - Transfer 20% of each age bin to the next bin
        - Retain 80% in current bin
        - Terminal bin receives inflow but has no outflow (accumulates)

        This eliminates mortality spikes from discrete 5-year cohort aging.
        Supports both deterministic and stochastic modes.

        See: PATCH_FRACTIONAL_AGING.md for scientific justification and validation.
        """
        aging_fraction = 0.2  # 1/5 = 20% per year (for 5-year bins)

        new_pop = np.zeros_like(self.state.population)

        # For each age bin (except terminal)
        for age_idx in range(self.groups.n_age_groups - 1):
            if self.deterministic:
                # Deterministic: exact 20% transfer
                staying = (self.state.population[age_idx, ...] * 0.8).astype(np.int64)
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

        See: REFERENCE_DISCRETE_AGING_OLD.md for details.
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
        """Calculate contacts using mixing matrices."""
        mixing_config = self.config["mixing"]

        # Base contact rates by activity (1D array)
        base_contact_rates = np.array([
            self.groups.activity_contact_rates[activity]
            for activity in self.groups.activities
        ])

        # Orientation-specific multipliers (default to 1.0 for backward compatibility)
        groups_config = self.config.get("groups", {})
        orient_multipliers_config = extract_value(
            groups_config.get("orientation_contact_multipliers", {"value": {}})
        )

        orient_multipliers = np.array([
            orient_multipliers_config.get(orient, 1.0)
            for orient in self.groups.orientations
        ])

        # Build 2D contact rate matrix: (n_orientations, n_activities)
        # Each orientation gets its base rate × multiplier
        contact_rates_2d = orient_multipliers[:, np.newaxis] * base_contact_rates[np.newaxis, :]
        contact_rates_2d = contact_rates_2d * self.time.years_per_step

        # Calculate total contacts (now orientation-specific!)
        active_pop = np.sum(self.state.population, axis=(0, 3, 4, 5, 6))
        total_contacts = active_pop * contact_rates_2d

        contacts_allocated = np.zeros((
            self.groups.n_orientations,
            self.groups.n_activities,
            self.groups.n_orientations,
            self.groups.n_activities
        ))

        hetero_mix_config = extract_value(mixing_config["heterosexual"])
        msm_mix_config = extract_value(mixing_config["msm"])

        hetero_mix = np.zeros((self.groups.n_activities, self.groups.n_activities))
        msm_mix = np.zeros((self.groups.n_activities, self.groups.n_activities))

        for i, from_act in enumerate(self.groups.activities):
            for j, to_act in enumerate(self.groups.activities):
                hetero_mix[i, j] = hetero_mix_config.get(from_act, {}).get(to_act, 0)
                msm_mix[i, j] = msm_mix_config.get(from_act, {}).get(to_act, 0)

        female_idx = self.groups.orient_to_idx.get("Female", 0)
        hetero_male_idx = self.groups.orient_to_idx.get("Heterosexual_Male", 1)

        for from_act in range(self.groups.n_activities):
            for to_act in range(self.groups.n_activities):
                contacts_allocated[female_idx, from_act, hetero_male_idx, to_act] = \
                    total_contacts[female_idx, from_act] * hetero_mix[from_act, to_act]
                contacts_allocated[hetero_male_idx, from_act, female_idx, to_act] = \
                    total_contacts[hetero_male_idx, from_act] * hetero_mix[from_act, to_act]

        msm_idx = self.groups.orient_to_idx.get("MSM", 2)
        for from_act in range(self.groups.n_activities):
            for to_act in range(self.groups.n_activities):
                contacts_allocated[msm_idx, from_act, msm_idx, to_act] = \
                    total_contacts[msm_idx, from_act] * msm_mix[from_act, to_act]

        return contacts_allocated

    def _step_disease_progression(self):
        """Progress through HIV disease stages: Acute -> Chronic -> AIDS."""
        disease_config = self.config["disease"]

        acute_idx = self.groups.hiv_to_idx["Acute"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        aids_idx = self.groups.hiv_to_idx["AIDS"]

        acute_to_chronic = self.time.annual_to_step_rate(
            extract_value(disease_config["progression"]["acute_to_chronic"])
        )
        chronic_to_aids = self.time.annual_to_step_rate(
            extract_value(disease_config["progression"]["chronic_to_aids"])
        )

        acute_pop = self.state.population[:, :, :, acute_idx, :, :, :]
        progressions = self._draw_events(acute_pop, acute_to_chronic)
        self.state.population[:, :, :, acute_idx, :, :, :] -= progressions
        self.state.population[:, :, :, chronic_idx, :, :, :] += progressions

        chronic_pop = self.state.population[:, :, :, chronic_idx, :, :, :]
        progressions = self._draw_events(chronic_pop, chronic_to_aids)
        self.state.population[:, :, :, chronic_idx, :, :, :] -= progressions
        self.state.population[:, :, :, aids_idx, :, :, :] += progressions

    def _step_diagnosis(self):
        """Diagnose undiagnosed HIV+ individuals."""
        diagnosis_rate = self.time.annual_to_step_rate(
            extract_value(self.config["disease"]["diagnosis"]["annual_rate"])
        )
        undiagnosed = self.state.population[:, :, :, 1:, 0, :, :]
        diagnosed = self._draw_events(undiagnosed, diagnosis_rate)
        self.state.population[:, :, :, 1:, 0, :, :] -= diagnosed
        self.state.population[:, :, :, 1:, 1, :, :] += diagnosed

    def _step_art_transitions(self, current_year: float):
        """
        Manage ART initiation and dropout (V5: time-scheduled).

        Args:
            current_year: Current calendar year for schedule lookup
        """
        disease_config = self.config["disease"]

        # V5: Use time-based schedules if available, otherwise fall back to V4 fixed rates
        if self.schedule_manager:
            # Get rates from schedule based on current year
            initiation_annual = self.schedule_manager.get_art_initiation_rate(current_year)
            dropout_annual = self.schedule_manager.get_art_discontinuation_rate(current_year)

            # V5 Validation: ART should be unavailable before introduction
            intro_year = self.schedule_manager.get_art_introduction_year()
            if intro_year and current_year < intro_year:
                validate_schedule_before_intro(
                    self.schedule_manager, current_year, intro_year,
                    initiation_annual, "ART initiation"
                )
        else:
            # V4 backward compatibility: fixed rates from config
            initiation_annual = extract_value(disease_config["treatment"]["art_initiation_rate"])
            dropout_annual = extract_value(disease_config["treatment"]["art_dropout_rate"])

        # Convert annual rates to per-timestep rates
        initiation_rate = self.time.annual_to_step_rate(initiation_annual)
        dropout_rate = self.time.annual_to_step_rate(dropout_annual)

        # Apply transitions
        eligible = self.state.population[:, :, :, 1:, 1, 0, :]
        initiated = self._draw_events(eligible, initiation_rate)
        self.state.population[:, :, :, 1:, 1, 0, :] -= initiated
        self.state.population[:, :, :, 1:, 1, 1, :] += initiated

        on_art = self.state.population[:, :, :, 1:, 1, 1, :]
        dropped = self._draw_events(on_art, dropout_rate)
        self.state.population[:, :, :, 1:, 1, 1, :] -= dropped
        self.state.population[:, :, :, 1:, 1, 0, :] += dropped

    def _step_prep_transitions(self, current_year: float):
        """
        Manage PrEP uptake and discontinuation (V5: time-scheduled).

        Args:
            current_year: Current calendar year for schedule lookup
        """
        interventions = self.config["interventions"]
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        none_idx = self.groups.prep_to_idx["None"]

        for prep_name in self.groups.prep_states[1:]:
            if prep_name not in interventions:
                continue
            prep_config = interventions[prep_name]
            if not extract_value(prep_config.get("enabled", {"value": True})):
                continue

            prep_idx = self.groups.prep_to_idx[prep_name]

            # V5: Use time-based schedules if available, otherwise fall back to V4 fixed rates
            if self.schedule_manager:
                # Get rates from schedule based on current year
                uptake_annual = self.schedule_manager.get_prep_initiation_rate(prep_name, current_year)
                dropout_annual = self.schedule_manager.get_prep_discontinuation_rate(prep_name, current_year)

                # V5 Validation: PrEP should be unavailable before introduction
                intro_year = self.schedule_manager.get_prep_introduction_year(prep_name)
                if intro_year and current_year < intro_year:
                    validate_schedule_before_intro(
                        self.schedule_manager, current_year, intro_year,
                        uptake_annual, f"{prep_name} initiation"
                    )
            else:
                # V4 backward compatibility: fixed rates from config
                uptake_annual = extract_value(prep_config["uptake_rate"])
                dropout_annual = extract_value(prep_config["dropout_rate"])

            # Convert annual rates to per-timestep rates
            uptake_rate = self.time.annual_to_step_rate(uptake_annual)
            dropout_rate = self.time.annual_to_step_rate(dropout_annual)

            # Apply transitions
            no_prep = self.state.population[:, :, :, susc_idx, :, :, none_idx]
            new_users = self._draw_events(no_prep, uptake_rate)
            self.state.population[:, :, :, susc_idx, :, :, none_idx] -= new_users
            self.state.population[:, :, :, susc_idx, :, :, prep_idx] += new_users

            prep_users = self.state.population[:, :, :, susc_idx, :, :, prep_idx]
            dropouts = self._draw_events(prep_users, dropout_rate)
            self.state.population[:, :, :, susc_idx, :, :, prep_idx] -= dropouts
            self.state.population[:, :, :, susc_idx, :, :, none_idx] += dropouts

    def _step_hiv_mortality(self) -> int:
        """Apply HIV-related mortality. Returns total HIV deaths."""
        disease_config = self.config["disease"]
        hiv_deaths = 0

        hiv_mortality = {
            "Acute": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["acute"])),
            "Chronic": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["chronic"])),
            "AIDS": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["aids"]))
        }
        art_reduction = extract_value(disease_config["treatment"]["art_mortality_reduction"])

        for stage_name, mortality_rate in hiv_mortality.items():
            stage_idx = self.groups.hiv_to_idx[stage_name]

            not_art = self.state.population[:, :, :, stage_idx, :, 0, :]
            deaths_not_art = self._draw_events(not_art, mortality_rate)
            self.state.population[:, :, :, stage_idx, :, 0, :] -= deaths_not_art
            hiv_deaths += np.sum(deaths_not_art)

            on_art = self.state.population[:, :, :, stage_idx, :, 1, :]
            reduced_rate = mortality_rate * (1 - art_reduction)
            deaths_art = self._draw_events(on_art, reduced_rate)
            self.state.population[:, :, :, stage_idx, :, 1, :] -= deaths_art
            hiv_deaths += np.sum(deaths_art)

        return hiv_deaths

    def record_outputs(self, step: int, year: float, new_inf: int, hiv_d: int,
                       bg_d: int, child_d: int, births: int,
                       continuous_debuts: int, flush_debuts: int, total_debuts: int):
        """
        Record timestep outputs.

        GUARDRAILS (V4.3): Separately tracks continuous vs flush debuts for observability.
        """
        active_pop = self.state.get_total_population()
        hiv_pos = self.state.get_hiv_positive()
        susceptible = self.state.get_susceptible()
        childhood_pop = self.demography.childhood.get_total()

        # Prevalence based on sexually active population only (NOT children)
        prevalence = hiv_pos / active_pop if active_pop > 0 else 0
        incidence = new_inf / susceptible if susceptible > 0 else 0

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
        self.outputs['incidence'].append(incidence)
        self.outputs['hiv_deaths'].append(hiv_d)
        self.outputs['background_deaths'].append(bg_d)
        self.outputs['childhood_deaths'].append(child_d)
        self.outputs['total_population'].append(active_pop + childhood_pop)
        self.outputs['sexually_active_population'].append(active_pop)
        self.outputs['childhood_population'].append(childhood_pop)
        self.outputs['births'].append(births)
        self.outputs['sexual_debuts'].append(total_debuts)
        self.outputs['continuous_debuts'].append(continuous_debuts)
        self.outputs['flush_debuts'].append(flush_debuts)
        self.outputs['prep_coverage_Oral_PrEP'].append(prep_coverage['Oral_PrEP'])
        self.outputs['prep_coverage_Lenacapavir'].append(prep_coverage['Lenacapavir'])

    def validate_global_population_balance(self, step: int,
                                           prev_total: int,
                                           births: int,
                                           hiv_deaths: int,
                                           bg_deaths: int,
                                           childhood_deaths: int,
                                           adjustment_change: int = 0):
        """
        Validate global population mass balance (PATCH 2).

        Ensures no silent creation/loss of people due to ordering/transition bugs.

        Formula:
            next_total = prev_total + births - bg_deaths - hiv_deaths - childhood_deaths + adjustment_change

        Args:
            step: Current timestep number
            prev_total: Total population before this timestep
            births: Number of births this timestep
            hiv_deaths: Number of HIV deaths this timestep
            bg_deaths: Number of background deaths this timestep
            childhood_deaths: Number of childhood deaths this timestep
            adjustment_change: Population change from exogenous adjustments (can be positive or negative)

        Raises:
            AssertionError: If mass balance is violated
        """
        current_year = self.time.start_year + (step * self.time.years_per_step)

        # Calculate current total population
        current_active = self.state.get_total_population()
        current_childhood = self.demography.childhood.get_total()
        current_total = current_active + current_childhood

        # Expected total based on flows (including adjustment change)
        expected_total = prev_total + births - bg_deaths - hiv_deaths - childhood_deaths + adjustment_change

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
                f"  Previous total:     {prev_total:,}\n"
                f"  + Births:           {births:,}\n"
                f"  - HIV deaths:       {hiv_deaths:,}\n"
                f"  - Background deaths: {bg_deaths:,}\n"
                f"  - Childhood deaths: {childhood_deaths:,}\n"
                f"  + Adjustment change: {adjustment_change:,}\n"
                f"  = Expected:         {expected_total:,}\n\n"
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
    """Run V4 simulation with all features."""
    with open(config_path, 'r') as f:
        config = json.load(f)

    n_runs = extract_value(config["simulation_controls"].get("n_runs", {"value": 1}))
    base_seed = seed if seed is not None else extract_value(
        config["simulation_controls"].get("seed", {"value": 42})
    )

    all_results = []

    for run in range(n_runs):
        run_seed = base_seed + run
        engine = SimulationEngineV3(config_path, seed=run_seed)
        result = engine.run(run_number=run + 1)
        all_results.append(result['summary'])

    # Write aggregated outputs if multiple runs
    if n_runs > 1 and engine.output_writer.enabled:
        engine.output_writer.write_aggregated_outputs(all_results)
        engine.output_writer.print_multi_run_summary(all_results)

    return all_results


if __name__ == '__main__':
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.json'
    results = simulate_v3(config_path)
    print("\nV4 simulation complete!")
    print(f"  Results saved to outputs/")
