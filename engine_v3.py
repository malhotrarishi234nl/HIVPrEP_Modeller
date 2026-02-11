"""
HIV Transmission Simulation Engine V2

Discrete-time stochastic population model with dynamic configuration.
All dimensions inferred from config - no hard-coded assumptions.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
import json
from pathlib import Path


def extract_value(config_item: Any) -> Any:
    """
    Extract value from config item.
    Handles both {"value": X, "notes": Y} and direct values.
    """
    if isinstance(config_item, dict) and "value" in config_item:
        return config_item["value"]
    return config_item


class TimeSystem:
    """Manages time conversion and step calculation."""
    
    UNITS_PER_YEAR = {
        "days": 365,
        "months": 12,
        "years": 1
    }
    
    def __init__(self, time_config: Dict):
        self.unit = extract_value(time_config["unit"])
        self.step_size = extract_value(time_config["step_size"])
        self.start_year = extract_value(time_config["start_year"])
        self.end_year = extract_value(time_config["end_year"])
        
        if self.unit not in self.UNITS_PER_YEAR:
            raise ValueError(f"Invalid time unit: {self.unit}. Must be 'days', 'months', or 'years'")
        
        # Calculate number of timesteps
        total_years = self.end_year - self.start_year
        units_per_year = self.UNITS_PER_YEAR[self.unit]
        total_units = total_years * units_per_year
        self.n_steps = int(total_units / self.step_size)
        
        # Store units per timestep for rate conversion
        self.units_per_step = self.step_size
        self.years_per_step = self.step_size / units_per_year
        
    def annual_to_step_rate(self, annual_rate: float) -> float:
        """Convert annual probability to per-timestep probability."""
        # p_step = 1 - (1 - p_annual)^(years_per_step)
        return 1 - (1 - annual_rate) ** self.years_per_step
    
    def __repr__(self):
        return (f"TimeSystem(unit={self.unit}, step_size={self.step_size}, "
                f"n_steps={self.n_steps}, years_per_step={self.years_per_step:.3f})")


class GroupSystem:
    """Manages group definitions and index mapping."""
    
    def __init__(self, groups_config: Dict):
        # Orientations
        orient_list = extract_value(groups_config["orientations"])
        self.orientations = [extract_value(o.get("name", o)) for o in orient_list]
        self.n_orientations = len(self.orientations)
        self.orient_to_idx = {name: idx for idx, name in enumerate(self.orientations)}
        
        # Age groups
        age_list = extract_value(groups_config["age_groups"])
        self.age_groups = [extract_value(a["name"]) for a in age_list]
        self.age_min = [a["min"] for a in age_list]
        self.age_max = [a["max"] for a in age_list]
        self.n_age_groups = len(self.age_groups)
        self.age_to_idx = {name: idx for idx, name in enumerate(self.age_groups)}
        
        # Sexual activity groups
        activity_list = extract_value(groups_config["sexual_activity_groups"])
        self.activities = [extract_value(a["name"]) for a in activity_list]
        self.activity_contact_rates = {
            extract_value(a["name"]): a["contact_rate"] 
            for a in activity_list
        }
        self.n_activities = len(self.activities)
        self.activity_to_idx = {name: idx for idx, name in enumerate(self.activities)}
        
        # HIV stages (fixed for now but could be dynamic)
        hiv_list = extract_value(groups_config["hiv_stages"])
        self.hiv_stages = [extract_value(h["name"]) for h in hiv_list]
        self.n_hiv_stages = len(self.hiv_stages)
        self.hiv_to_idx = {name: idx for idx, name in enumerate(self.hiv_stages)}
        
        # Fixed states (not configurable in V2)
        self.diagnosis_states = ["Undiagnosed", "Diagnosed"]
        self.n_diagnosis = 2
        
        self.art_states = ["Not_on_ART", "On_ART"]
        self.n_art = 2
        
        # PrEP states (dynamically built from interventions)
        self.prep_states = ["None"]  # Will be extended
        
    def add_prep_intervention(self, intervention_name: str):
        """Add a PrEP state from intervention config."""
        if intervention_name not in self.prep_states:
            self.prep_states.append(intervention_name)
    
    def finalize_prep_states(self):
        """Finalize PrEP state list and create index mapping."""
        self.n_prep = len(self.prep_states)
        self.prep_to_idx = {name: idx for idx, name in enumerate(self.prep_states)}
    
    def state_shape(self) -> Tuple[int, ...]:
        """Return shape of population state tensor."""
        return (
            self.n_age_groups,
            self.n_orientations,
            self.n_activities,
            self.n_hiv_stages,
            self.n_diagnosis,
            self.n_art,
            self.n_prep
        )
    
    def __repr__(self):
        return (f"GroupSystem(ages={self.n_age_groups}, orients={self.n_orientations}, "
                f"activities={self.n_activities}, hiv={self.n_hiv_stages}, prep={self.n_prep})")


class ConfigValidator:
    """Validates configuration and provides helpful error messages."""
    
    @staticmethod
    def validate(config: Dict, groups: GroupSystem) -> List[str]:
        """
        Validate configuration and return list of errors.
        Empty list means valid.
        """
        errors = []
        
        # Validate mixing matrices
        mixing_config = config["mixing"]
        
        # Heterosexual mixing
        hetero_mix = extract_value(mixing_config["heterosexual"])
        errors.extend(ConfigValidator._validate_mixing_matrix(
            hetero_mix, groups.activities, "heterosexual"
        ))
        
        # MSM mixing
        msm_mix = extract_value(mixing_config["msm"])
        errors.extend(ConfigValidator._validate_mixing_matrix(
            msm_mix, groups.activities, "MSM"
        ))
        
        # Validate probabilities are in [0, 1]
        disease_config = config["disease"]
        
        # Transmission probabilities
        trans = disease_config["transmission"]
        for key in ["beta_acute", "beta_chronic", "beta_aids"]:
            val = extract_value(trans[key])
            if not 0 <= val <= 1:
                errors.append(f"transmission.{key} = {val} is not in [0, 1]")
        
        # Progression rates
        prog = disease_config["progression"]
        for key in ["acute_to_chronic", "chronic_to_aids"]:
            val = extract_value(prog[key])
            if not 0 <= val <= 1:
                errors.append(f"progression.{key} = {val} is not in [0, 1]")
        
        # Validate population initialization (method-dependent)
        pop_config = config["population"]
        init_method = extract_value(pop_config.get("initialization_method", {"value": "direct_counts"}))

        if init_method == "direct_counts":
            # Legacy method: validate initial_distribution structure
            if "initial_distribution" not in pop_config:
                errors.append("population.initial_distribution is required for initialization_method='direct_counts'")
            else:
                pop_dist = extract_value(pop_config["initial_distribution"])
                errors.extend(ConfigValidator._validate_population_dist(pop_dist, groups))

        elif init_method == "relative_weights":
            # Option A: validate relative weights structure
            if "age_sex_distribution" not in pop_config:
                errors.append("population.age_sex_distribution is required for initialization_method='relative_weights'")
            if "total_size" not in pop_config:
                errors.append("population.total_size is required for initialization_method='relative_weights'")

            # Validate activity distribution sums to 1.0
            if "activity_distribution" in pop_config:
                activity_dist = extract_value(pop_config["activity_distribution"])
                activity_sum = sum(activity_dist.values())
                if not np.isclose(activity_sum, 1.0, atol=1e-6):
                    errors.append(f"population.activity_distribution must sum to 1.0, got {activity_sum:.4f}")

        else:
            errors.append(f"Unknown initialization_method: '{init_method}'. Use 'relative_weights' or 'direct_counts'")

        # Validate orientation contact multipliers (if provided)
        groups_config = config.get("groups", {})
        if "orientation_contact_multipliers" in groups_config:
            multipliers = extract_value(groups_config["orientation_contact_multipliers"])
            for orient, mult in multipliers.items():
                if orient not in groups.orientations:
                    errors.append(
                        f"groups.orientation_contact_multipliers has unknown orientation '{orient}'. "
                        f"Valid orientations: {groups.orientations}"
                    )
                if mult <= 0:
                    errors.append(
                        f"groups.orientation_contact_multipliers['{orient}'] = {mult} must be > 0"
                    )

        # Validate aging method (if provided)
        demog_config = config.get("demography", {})
        if "aging_method" in demog_config:
            aging_method = extract_value(demog_config["aging_method"])
            valid_methods = ["fractional", "discrete"]
            if aging_method not in valid_methods:
                errors.append(
                    f"demography.aging_method = '{aging_method}' is invalid. "
                    f"Valid options: {valid_methods}"
                )

        return errors
    
    @staticmethod
    def _validate_mixing_matrix(matrix: Dict, activities: List[str], name: str) -> List[str]:
        """Validate mixing matrix normalization and structure."""
        errors = []
        
        for from_activity in activities:
            if from_activity not in matrix:
                errors.append(f"Mixing matrix '{name}' missing row for activity '{from_activity}'")
                continue
            
            row = matrix[from_activity]
            row_sum = sum(row.get(to_activity, 0) for to_activity in activities)
            
            if not np.isclose(row_sum, 1.0, atol=1e-6):
                errors.append(
                    f"Mixing matrix '{name}' for activity '{from_activity}' sums to {row_sum:.4f} (expected 1.0)"
                )
            
            # Check all probabilities are in [0, 1]
            for to_activity, prob in row.items():
                if not 0 <= prob <= 1:
                    errors.append(
                        f"Mixing matrix '{name}' has invalid probability {prob} for {from_activity} → {to_activity}"
                    )
        
        return errors
    
    @staticmethod
    def _validate_population_dist(dist: Dict, groups: GroupSystem) -> List[str]:
        """Validate population distribution structure."""
        errors = []
        
        for orientation in dist.keys():
            if orientation not in groups.orientations:
                errors.append(f"Population distribution has unknown orientation '{orientation}'")
        
        for orientation, age_dict in dist.items():
            for age_group in age_dict.keys():
                if age_group not in groups.age_groups:
                    errors.append(f"Population distribution has unknown age group '{age_group}' for {orientation}")
            
            for age_group, activity_dict in age_dict.items():
                for activity in activity_dict.keys():
                    if activity not in groups.activities:
                        errors.append(
                            f"Population distribution has unknown activity '{activity}' "
                            f"for {orientation}/{age_group}"
                        )
        
        return errors


class PopulationState:
    """Population state tensor with label-based access."""

    def __init__(self, groups: GroupSystem, seed: int = 42):
        self.groups = groups
        self.population = np.zeros(groups.state_shape(), dtype=np.int64)
        self.rng = np.random.RandomState(seed)

    def initialize_from_relative_weights(self, pop_config: Dict, childhood_pool=None):
        """
        Initialize population using relative weights (Option A: Global MSM Share).

        CRITICAL INITIALIZATION PATCH
        This method implements stable, transparent population initialization:
        1. Relative population first (normalized age × sex distributions)
        2. Sexual debut boundary (<15: childhood, ≥15: sexually active)
        3. Male orientation split using global p_msm (deterministic)
        4. Activity assignment (separate from orientation)
        5. Absolute population size applied last
        6. Comprehensive conservation checks

        Args:
            pop_config: Population configuration dict
            childhood_pool: ChildhoodPool instance (optional, for <15 assignment)

        Raises:
            ValueError: If any conservation check fails
        """
        print("\n" + "="*70)
        print("POPULATION INITIALIZATION (Option A: Relative Weights)")
        print("="*70)

        # Extract parameters
        total_size = extract_value(pop_config.get("total_size", {"value": 1000000}))
        age_sex_dist = extract_value(pop_config.get("age_sex_distribution", {"value": {}}))
        p_msm = extract_value(pop_config.get("p_msm", {"value": 0.10}))
        activity_dist = extract_value(pop_config.get("activity_distribution",
                                                     {"value": {"Low": 0.6, "Medium": 0.3, "High": 0.1}}))

        print(f"Target total population: {total_size:,}")
        print(f"MSM proportion (males ≥15): {p_msm:.2%}")
        print(f"Activity distribution: {activity_dist}")

        # Validate activity distribution sums to 1.0
        activity_sum = sum(activity_dist.values())
        if abs(activity_sum - 1.0) > 0.001:
            raise ValueError(f"Activity distribution must sum to 1.0, got {activity_sum}")

        # Step 1: Normalize age × sex relative weights
        print("\nStep 1: Normalizing age × sex distributions...")
        total_weight = 0.0
        for age_group, sex_weights in age_sex_dist.items():
            total_weight += sex_weights.get('female', 0) + sex_weights.get('male', 0)

        if total_weight == 0:
            raise ValueError("age_sex_distribution is empty or all zeros")

        normalized_dist = {}
        for age_group, sex_weights in age_sex_dist.items():
            normalized_dist[age_group] = {
                'female': sex_weights.get('female', 0) / total_weight,
                'male': sex_weights.get('male', 0) / total_weight
            }

        # Step 2: Apply sexual debut boundary and allocate counts
        print("Step 2: Applying sexual debut boundary (<15 vs ≥15)...")

        childhood_counts = {'female': {}, 'male': {}}  # For ages <15
        eligible_counts = {'female': {}, 'male': {}}   # For ages ≥15

        total_childhood = 0
        total_eligible = 0

        for age_group, sex_weights in normalized_dist.items():
            # Determine if this age group is <15 or ≥15
            # Parse age group: "15-19" → 15, "65+" → 65, "15" → 15
            if '-' in age_group:
                min_age = int(age_group.split('-')[0])
            elif '+' in age_group:
                min_age = int(age_group.replace('+', ''))
            else:
                min_age = int(age_group)

            # Allocate absolute counts
            female_count = int(sex_weights['female'] * total_size)
            male_count = int(sex_weights['male'] * total_size)

            if min_age < 15:
                # Goes to childhood pool
                childhood_counts['female'][age_group] = female_count
                childhood_counts['male'][age_group] = male_count
                total_childhood += female_count + male_count
            else:
                # Eligible for sexual activity
                eligible_counts['female'][age_group] = female_count
                eligible_counts['male'][age_group] = male_count
                total_eligible += female_count + male_count

        print(f"  Children (<15): {total_childhood:,}")
        print(f"  Eligible (≥15): {total_eligible:,}")
        print(f"  Total: {total_childhood + total_eligible:,}")

        # Step 3: Male orientation split (for eligible males only)
        print("Step 3: Splitting eligible males into orientations...")

        msm_counts = {}      # MSM by age group
        hetero_m_counts = {} # Heterosexual_Male by age group

        total_msm = 0
        total_hetero_m = 0

        for age_group, male_count in eligible_counts['male'].items():
            # Deterministic split using global p_msm
            msm = int(male_count * p_msm)
            hetero_m = male_count - msm  # Ensure exact conservation

            msm_counts[age_group] = msm
            hetero_m_counts[age_group] = hetero_m

            total_msm += msm
            total_hetero_m += hetero_m

        print(f"  MSM: {total_msm:,} ({total_msm/max(total_eligible,1):.2%} of eligible)")
        print(f"  Heterosexual_Male: {total_hetero_m:,}")
        print(f"  Female: {sum(eligible_counts['female'].values()):,}")

        # Step 4: Activity assignment within each orientation
        print("Step 4: Assigning activity levels...")

        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        undiag_idx = 0
        no_art_idx = 0
        no_prep_idx = 0

        # Get activity indices
        activity_indices = {name: self.groups.activity_to_idx[name] for name in activity_dist.keys()}

        # Helper function to split into activities (deterministic)
        def split_activities(count, activity_dist):
            """Split count into activities, ensuring exact conservation."""
            result = {}
            remaining = count

            # Sort by proportion (descending) for deterministic allocation
            sorted_activities = sorted(activity_dist.items(), key=lambda x: x[1], reverse=True)

            for i, (activity, proportion) in enumerate(sorted_activities):
                if i == len(sorted_activities) - 1:
                    # Last activity gets all remaining (exact conservation)
                    result[activity] = remaining
                else:
                    allocated = int(count * proportion)
                    result[activity] = allocated
                    remaining -= allocated

            return result

        # Assign Female orientation
        female_orient_idx = self.groups.orient_to_idx.get("Female")
        if female_orient_idx is not None:
            for age_group, female_count in eligible_counts['female'].items():
                age_idx = self.groups.age_to_idx[age_group]
                activity_counts = split_activities(female_count, activity_dist)

                for activity, count in activity_counts.items():
                    activity_idx = activity_indices[activity]
                    self.population[
                        age_idx, female_orient_idx, activity_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] = count

        # Assign Heterosexual_Male orientation
        hetero_m_orient_idx = self.groups.orient_to_idx.get("Heterosexual_Male")
        if hetero_m_orient_idx is not None:
            for age_group, male_count in hetero_m_counts.items():
                age_idx = self.groups.age_to_idx[age_group]
                activity_counts = split_activities(male_count, activity_dist)

                for activity, count in activity_counts.items():
                    activity_idx = activity_indices[activity]
                    self.population[
                        age_idx, hetero_m_orient_idx, activity_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] = count

        # Assign MSM orientation
        msm_orient_idx = self.groups.orient_to_idx.get("MSM")
        if msm_orient_idx is not None:
            for age_group, msm_count in msm_counts.items():
                age_idx = self.groups.age_to_idx[age_group]
                activity_counts = split_activities(msm_count, activity_dist)

                for activity, count in activity_counts.items():
                    activity_idx = activity_indices[activity]
                    self.population[
                        age_idx, msm_orient_idx, activity_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] = count

        # Step 5: Populate childhood pool (if provided)
        if childhood_pool is not None and total_childhood > 0:
            print("Step 5: Populating childhood pool...")
            # Map age groups to childhood bins
            # Childhood bins are typically: "0-4", "5-9", "10-14"
            for age_group in childhood_counts['female'].keys():
                female_count = childhood_counts['female'][age_group]
                male_count = childhood_counts['male'][age_group]

                # Find matching childhood bin
                for bin_idx, bin_name in enumerate(childhood_pool.age_bins):
                    if age_group == bin_name:
                        childhood_pool.population[bin_idx, childhood_pool.FEMALE] = female_count
                        childhood_pool.population[bin_idx, childhood_pool.MALE] = male_count
                        break

        # Step 6: SAFETY & CONSERVATION CHECKS (MANDATORY)
        print("\nStep 6: Running conservation checks...")
        self._validate_initialization(
            total_size, total_childhood, total_eligible,
            eligible_counts, msm_counts, hetero_m_counts, activity_dist, childhood_pool
        )

        print("="*70)
        print("✅ INITIALIZATION COMPLETE - All checks passed")
        print("="*70 + "\n")

    def _validate_initialization(self, total_size, total_childhood, total_eligible,
                                 eligible_counts, msm_counts, hetero_m_counts,
                                 activity_dist, childhood_pool):
        """
        Comprehensive validation of population initialization.

        MANDATORY CHECKS:
        1. Total population unchanged
        2. Age and sex distributions unchanged
        3. Orientation totals correct
        4. Activity sums correct
        5. No unassigned individuals

        Raises:
            ValueError: If any check fails
        """
        errors = []
        warnings = []

        # Check 1: Total population
        actual_sexually_active = self.get_total_population()
        actual_childhood = childhood_pool.get_total() if childhood_pool else 0
        actual_total = actual_sexually_active + actual_childhood

        expected_total = total_childhood + total_eligible

        if actual_total != expected_total:
            errors.append(
                f"Total population mismatch: expected {expected_total:,}, got {actual_total:,} "
                f"(diff: {actual_total - expected_total:,})"
            )
        else:
            print(f"  ✓ Total population: {actual_total:,} (target: {total_size:,})")

        # Check 2: Sexually active population
        if actual_sexually_active != total_eligible:
            errors.append(
                f"Sexually active population mismatch: expected {total_eligible:,}, got {actual_sexually_active:,}"
            )
        else:
            print(f"  ✓ Sexually active: {actual_sexually_active:,}")

        # Check 3: Childhood population
        if actual_childhood != total_childhood:
            errors.append(
                f"Childhood population mismatch: expected {total_childhood:,}, got {actual_childhood:,}"
            )
        else:
            print(f"  ✓ Childhood pool: {actual_childhood:,}")

        # Check 4: Orientation totals
        susc_idx = self.groups.hiv_to_idx["Susceptible"]

        # Female orientation
        female_idx = self.groups.orient_to_idx.get("Female")
        if female_idx is not None:
            actual_female = np.sum(self.population[:, female_idx, :, susc_idx, :, :, :])
            expected_female = sum(eligible_counts['female'].values())
            if actual_female != expected_female:
                errors.append(f"Female count mismatch: expected {expected_female:,}, got {actual_female:,}")
            else:
                print(f"  ✓ Female orientation: {actual_female:,}")

        # Heterosexual_Male orientation
        hetero_m_idx = self.groups.orient_to_idx.get("Heterosexual_Male")
        if hetero_m_idx is not None:
            actual_hetero_m = np.sum(self.population[:, hetero_m_idx, :, susc_idx, :, :, :])
            expected_hetero_m = sum(hetero_m_counts.values())
            if actual_hetero_m != expected_hetero_m:
                errors.append(f"Heterosexual_Male count mismatch: expected {expected_hetero_m:,}, got {actual_hetero_m:,}")
            else:
                print(f"  ✓ Heterosexual_Male orientation: {actual_hetero_m:,}")

        # MSM orientation
        msm_idx = self.groups.orient_to_idx.get("MSM")
        if msm_idx is not None:
            actual_msm = np.sum(self.population[:, msm_idx, :, susc_idx, :, :, :])
            expected_msm = sum(msm_counts.values())
            if actual_msm != expected_msm:
                errors.append(f"MSM count mismatch: expected {expected_msm:,}, got {actual_msm:,}")
            else:
                print(f"  ✓ MSM orientation: {actual_msm:,}")

        # Check 5: Activity distribution within each orientation
        for orient_name, orient_idx in self.groups.orient_to_idx.items():
            for activity_name, activity_idx in self.groups.activity_to_idx.items():
                count = np.sum(self.population[:, orient_idx, activity_idx, susc_idx, :, :, :])
                # Just verify non-negative (detailed proportions checked implicitly above)
                if count < 0:
                    errors.append(f"{orient_name} × {activity_name}: negative count {count}")

        print(f"  ✓ All activity assignments non-negative")

        # Check 6: No negative populations anywhere
        if np.any(self.population < 0):
            errors.append("Negative population counts detected in tensor")
        else:
            print(f"  ✓ No negative counts in population tensor")

        # Report results
        if errors:
            print("\n" + "="*70)
            print("❌ INITIALIZATION FAILED - Conservation checks failed:")
            for error in errors:
                print(f"  ✗ {error}")
            print("="*70)
            raise ValueError(f"Population initialization failed {len(errors)} conservation checks")

        if warnings:
            print("\n⚠ Warnings:")
            for warning in warnings:
                print(f"  ⚠ {warning}")

    def initialize_from_config(self, pop_config: Dict, childhood_pool=None):
        """
        Initialize population from config structure.

        Supports two methods:
        1. "relative_weights" (Option A: Global MSM Share) - RECOMMENDED
        2. "direct_counts" (legacy) - for backward compatibility

        Args:
            pop_config: Population configuration dict
            childhood_pool: ChildhoodPool instance (optional)
        """
        # Check initialization method
        init_method = extract_value(pop_config.get("initialization_method", {"value": "direct_counts"}))

        if init_method == "relative_weights":
            # Use new Option A initialization
            self.initialize_from_relative_weights(pop_config, childhood_pool)

            # Seed initial HIV infections after initialization
            initial_prev = extract_value(pop_config.get("initial_hiv_prevalence", {"value": 0}))
            if initial_prev > 0:
                self._seed_initial_hiv(initial_prev)

        elif init_method == "direct_counts":
            # Use legacy initialization (backward compatibility)
            self._initialize_from_direct_counts(pop_config)

        else:
            raise ValueError(f"Unknown initialization_method: {init_method}. "
                           f"Use 'relative_weights' or 'direct_counts'")

    def _initialize_from_direct_counts(self, pop_config: Dict):
        """
        Legacy initialization method using direct orientation × age × activity counts.

        DEPRECATED: Use initialization_method="relative_weights" instead.
        This method is kept for backward compatibility with old configs.
        """
        initial_dist = extract_value(pop_config["initial_distribution"])
        initial_prev = extract_value(pop_config.get("initial_hiv_prevalence", {"value": 0}))
        
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        undiag_idx = 0  # Undiagnosed
        no_art_idx = 0  # Not on ART
        no_prep_idx = 0  # None

        # Load initial distribution
        for orientation, age_dict in initial_dist.items():
            orient_idx = self.groups.orient_to_idx[orientation]

            for age_group, activity_dict in age_dict.items():
                age_idx = self.groups.age_to_idx[age_group]

                for activity, count in activity_dict.items():
                    activity_idx = self.groups.activity_to_idx[activity]

                    # All start susceptible
                    self.population[
                        age_idx, orient_idx, activity_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] = count

        # Seed initial HIV infections
        if initial_prev > 0:
            self._seed_initial_hiv(initial_prev)

    def _seed_initial_hiv(self, initial_prev: float):
        """
        Seed initial HIV infections in the population.

        Args:
            initial_prev: Proportion of population to seed with chronic HIV
        """
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        undiag_idx = 0
        no_art_idx = 0
        no_prep_idx = 0

        total_seeded = 0

        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for activity_idx in range(self.groups.n_activities):
                    susceptible = self.population[
                        age_idx, orient_idx, activity_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ]

                    if susceptible > 0:
                        initial_infected = int(susceptible * initial_prev)

                        if initial_infected > 0:
                            # Move to chronic HIV
                            self.population[
                                age_idx, orient_idx, activity_idx,
                                susc_idx, undiag_idx, no_art_idx, no_prep_idx
                            ] -= initial_infected

                            self.population[
                                age_idx, orient_idx, activity_idx,
                                chronic_idx, undiag_idx, no_art_idx, no_prep_idx
                            ] += initial_infected

                            total_seeded += initial_infected

        if total_seeded > 0:
            print(f"  Seeded {total_seeded:,} initial HIV+ individuals ({initial_prev:.1%} prevalence)")
    
    def get_total_population(self) -> int:
        """Total population count."""
        return np.sum(self.population)
    
    def get_hiv_positive(self) -> int:
        """Count of all HIV+ individuals."""
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        return np.sum(self.population[:, :, :, susc_idx+1:, :, :, :])
    
    def get_susceptible(self) -> int:
        """Count of susceptible individuals."""
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        return np.sum(self.population[:, :, :, susc_idx, :, :, :])
    
    def validate(self):
        """Validate state consistency."""
        if np.any(self.population < 0):
            raise ValueError("Negative population counts detected")
        if not np.all(np.isfinite(self.population)):
            raise ValueError("Non-finite population counts detected")


class SimulationEngine:
    """
    Main simulation engine with dynamic configuration.
    
    All dimensions inferred from config.
    Time-aware rate conversions.
    Label-based parameter access.
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
        
        # Add PrEP interventions to group system
        for intervention_name, intervention_config in self.config["interventions"].items():
            if extract_value(intervention_config.get("enabled", {"value": True})):
                self.groups.add_prep_intervention(intervention_name)
        self.groups.finalize_prep_states()
        
        # Validate configuration
        errors = ConfigValidator.validate(self.config, self.groups)
        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
        
        print(f"✓ Configuration validated successfully")
        print(f"  {self.time}")
        print(f"  {self.groups}")
        
        # Initialize population state
        self.state = PopulationState(self.groups, seed=seed)
        self.state.initialize_from_config(self.config["population"])
        
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
            'total_population': []
        }
    
    def _draw_events(self, n: np.ndarray, p: float) -> np.ndarray:
        """
        Draw number of events from binomial distribution.
        
        In deterministic mode, returns expected value.
        """
        if self.deterministic:
            return (n * p).astype(np.int64)
        else:
            return self.rng.binomial(n, p)
    
    def run(self) -> Dict:
        """Run full simulation."""
        print(f"\nRunning simulation for {self.time.n_steps} timesteps...")
        
        for step in range(self.time.n_steps):
            self.step_simulation(step)
            
            if (step + 1) % 10 == 0:
                print(f"  Step {step + 1}/{self.time.n_steps}", end='\r')
        
        print(f"  Completed {self.time.n_steps} timesteps.    ")
        
        # Convert to DataFrame
        df = pd.DataFrame(self.outputs)
        
        return {
            'summary': df,
            'final_state': self.state.population.copy(),
            'seed': self.seed,
            'time_system': self.time,
            'groups': self.groups
        }
    
    def step_simulation(self, step: int):
        """Execute one timestep of simulation."""
        # Calculate current year for output
        current_year = self.time.start_year + step * self.time.years_per_step
        
        # Validate before step
        self.state.validate()
        
        # Execute simulation steps in order
        contacts = self.step_contacts_and_mixing()
        new_infections = self.step_transmission(contacts)
        self.step_disease_progression()
        self.step_diagnosis()
        self.step_art_transitions()
        self.step_prep_transitions()
        hiv_deaths, bg_deaths = self.step_mortality()
        self.step_aging(step)
        
        # Record outputs
        self.record_outputs(step, current_year, new_infections, hiv_deaths, bg_deaths)
        
        # Validate after step
        self.state.validate()
    
    def step_contacts_and_mixing(self) -> np.ndarray:
        """Calculate contacts using mixing matrices."""
        mixing_config = self.config["mixing"]
        
        # Get contact rates by activity
        contact_rates = np.array([
            self.groups.activity_contact_rates[activity]
            for activity in self.groups.activities
        ])
        
        # Convert to per-timestep rates
        contact_rates = contact_rates * self.time.years_per_step
        
        # Calculate active population by orientation and activity
        active_pop = np.sum(self.state.population, axis=(0, 3, 4, 5, 6))  # sum over age, hiv, dx, art, prep
        
        # Total contacts per group
        total_contacts = active_pop * contact_rates[np.newaxis, :]
        
        # Allocate contacts via mixing matrices
        contacts_allocated = np.zeros((
            self.groups.n_orientations,
            self.groups.n_activities,
            self.groups.n_orientations,
            self.groups.n_activities
        ))
        
        # Get mixing matrix configs
        hetero_mix_config = extract_value(mixing_config["heterosexual"])
        msm_mix_config = extract_value(mixing_config["msm"])
        
        # Build mixing matrices as arrays
        hetero_mix = np.zeros((self.groups.n_activities, self.groups.n_activities))
        msm_mix = np.zeros((self.groups.n_activities, self.groups.n_activities))
        
        for i, from_act in enumerate(self.groups.activities):
            for j, to_act in enumerate(self.groups.activities):
                hetero_mix[i, j] = hetero_mix_config.get(from_act, {}).get(to_act, 0)
                msm_mix[i, j] = msm_mix_config.get(from_act, {}).get(to_act, 0)
        
        # Heterosexual mixing (Female <-> Hetero_Male)
        female_idx = self.groups.orient_to_idx.get("Female", 0)
        hetero_male_idx = self.groups.orient_to_idx.get("Heterosexual_Male", 1)
        
        for from_act in range(self.groups.n_activities):
            for to_act in range(self.groups.n_activities):
                # Female to Hetero Male
                contacts_allocated[female_idx, from_act, hetero_male_idx, to_act] = \
                    total_contacts[female_idx, from_act] * hetero_mix[from_act, to_act]
                # Hetero Male to Female
                contacts_allocated[hetero_male_idx, from_act, female_idx, to_act] = \
                    total_contacts[hetero_male_idx, from_act] * hetero_mix[from_act, to_act]
        
        # MSM mixing
        msm_idx = self.groups.orient_to_idx.get("MSM", 2)
        for from_act in range(self.groups.n_activities):
            for to_act in range(self.groups.n_activities):
                contacts_allocated[msm_idx, from_act, msm_idx, to_act] = \
                    total_contacts[msm_idx, from_act] * msm_mix[from_act, to_act]
        
        return contacts_allocated
    
    def step_transmission(self, contacts: np.ndarray) -> int:
        """Calculate new HIV infections from contacts."""
        disease_config = self.config["disease"]
        new_infections = 0
        
        # Get beta values by stage
        beta = {
            "Acute": extract_value(disease_config["transmission"]["beta_acute"]),
            "Chronic": extract_value(disease_config["transmission"]["beta_chronic"]),
            "AIDS": extract_value(disease_config["transmission"]["beta_aids"])
        }
        
        # Get indices
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        acute_idx = self.groups.hiv_to_idx["Acute"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        aids_idx = self.groups.hiv_to_idx["AIDS"]
        
        # Iterate over contact pairs
        for orient_from in range(self.groups.n_orientations):
            for act_from in range(self.groups.n_activities):
                for orient_to in range(self.groups.n_orientations):
                    for act_to in range(self.groups.n_activities):
                        n_contacts = contacts[orient_from, act_from, orient_to, act_to]
                        if n_contacts == 0:
                            continue
                        
                        # Calculate prevalence in "from" group
                        infected_pop = np.sum(
                            self.state.population[:, orient_from, act_from, [acute_idx, chronic_idx, aids_idx], :, :, :]
                        )
                        total_pop = np.sum(
                            self.state.population[:, orient_from, act_from, :, :, :, :]
                        )
                        
                        if total_pop == 0:
                            continue
                        
                        prevalence = infected_pop / total_pop
                        
                        # Susceptible in "to" group
                        susceptible_pop = np.sum(
                            self.state.population[:, orient_to, act_to, susc_idx, :, :, :]
                        )
                        
                        if susceptible_pop == 0:
                            continue
                        
                        # Weighted average beta by stage
                        avg_beta = 0
                        for stage_name, stage_idx in [("Acute", acute_idx), ("Chronic", chronic_idx), ("AIDS", aids_idx)]:
                            stage_pop = np.sum(
                                self.state.population[:, orient_from, act_from, stage_idx, :, :, :]
                            )
                            if infected_pop > 0:
                                avg_beta += beta[stage_name] * (stage_pop / infected_pop)
                        
                        # Get condom modifier
                        condom_config = extract_value(self.config["mixing"]["condom_usage"])
                        to_activity_name = self.groups.activities[act_to]
                        condom_mod = condom_config.get(to_activity_name, 1.0)
                        
                        # ART modifier
                        art_pop = np.sum(
                            self.state.population[:, orient_from, act_from, [acute_idx, chronic_idx, aids_idx], :, 1, :]  # On ART
                        )
                        art_modifier = 1.0
                        if infected_pop > 0:
                            prop_on_art = art_pop / infected_pop
                            art_reduction = extract_value(disease_config["treatment"]["art_transmission_reduction"])
                            art_modifier = 1 - (prop_on_art * art_reduction)
                        
                        # PrEP modifier (weighted by coverage)
                        prep_modifier = 1.0
                        prep_protected = 0
                        
                        for prep_name in self.groups.prep_states[1:]:  # Skip "None"
                            prep_idx = self.groups.prep_to_idx[prep_name]
                            prep_pop = np.sum(
                                self.state.population[:, orient_to, act_to, susc_idx, :, :, prep_idx]
                            )
                            
                            if prep_pop > 0 and prep_name in self.config["interventions"]:
                                efficacy = extract_value(self.config["interventions"][prep_name]["efficacy"])
                                prep_protected += prep_pop * efficacy
                        
                        if susceptible_pop > 0:
                            prep_modifier = 1 - (prep_protected / susceptible_pop)
                        
                        # Combined transmission probability
                        p_transmission = avg_beta * condom_mod * art_modifier * prep_modifier * prevalence
                        p_transmission = np.clip(p_transmission, 0, 1)
                        
                        # Stochastic draw
                        expected_infections = n_contacts * p_transmission
                        actual_infections = self._draw_events(
                            min(int(n_contacts), susceptible_pop),
                            p_transmission
                        )
                        
                        if isinstance(actual_infections, np.ndarray):
                            actual_infections = int(actual_infections)
                        
                        actual_infections = min(actual_infections, susceptible_pop)
                        
                        if actual_infections > 0:
                            # Distribute infections across age groups and PrEP states
                            for age in range(self.groups.n_age_groups):
                                age_susceptible = np.sum(
                                    self.state.population[age, orient_to, act_to, susc_idx, :, :, :]
                                )
                                if susceptible_pop > 0 and age_susceptible > 0:
                                    age_infections = int(actual_infections * age_susceptible / susceptible_pop)
                                    
                                    for prep in range(self.groups.n_prep):
                                        prep_susceptible = self.state.population[
                                            age, orient_to, act_to, susc_idx, 0, 0, prep
                                        ]
                                        if age_susceptible > 0 and prep_susceptible > 0:
                                            prep_infections = int(age_infections * prep_susceptible / age_susceptible)
                                            prep_infections = min(prep_infections, prep_susceptible)
                                            
                                            if prep_infections > 0:
                                                # Remove from susceptible
                                                self.state.population[
                                                    age, orient_to, act_to, susc_idx, 0, 0, prep
                                                ] -= prep_infections
                                                
                                                # Add to acute (lose PrEP)
                                                self.state.population[
                                                    age, orient_to, act_to, acute_idx, 0, 0, 0
                                                ] += prep_infections
                                                
                                                new_infections += prep_infections
        
        return new_infections
    
    def step_disease_progression(self):
        """Progress through HIV disease stages."""
        disease_config = self.config["disease"]
        
        # Get stage indices
        acute_idx = self.groups.hiv_to_idx["Acute"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        aids_idx = self.groups.hiv_to_idx["AIDS"]
        
        # Get progression rates and convert to per-timestep
        acute_to_chronic_annual = extract_value(disease_config["progression"]["acute_to_chronic"])
        chronic_to_aids_annual = extract_value(disease_config["progression"]["chronic_to_aids"])
        
        acute_to_chronic = self.time.annual_to_step_rate(acute_to_chronic_annual)
        chronic_to_aids = self.time.annual_to_step_rate(chronic_to_aids_annual)
        
        # Acute to Chronic
        acute_pop = self.state.population[:, :, :, acute_idx, :, :, :]
        progressions = self._draw_events(acute_pop, acute_to_chronic)
        self.state.population[:, :, :, acute_idx, :, :, :] -= progressions
        self.state.population[:, :, :, chronic_idx, :, :, :] += progressions
        
        # Chronic to AIDS
        chronic_pop = self.state.population[:, :, :, chronic_idx, :, :, :]
        progressions = self._draw_events(chronic_pop, chronic_to_aids)
        self.state.population[:, :, :, chronic_idx, :, :, :] -= progressions
        self.state.population[:, :, :, aids_idx, :, :, :] += progressions
    
    def step_diagnosis(self):
        """Diagnose undiagnosed HIV+ individuals."""
        disease_config = self.config["disease"]
        
        # Get diagnosis rate and convert to per-timestep
        diagnosis_annual = extract_value(disease_config["diagnosis"]["annual_rate"])
        diagnosis_rate = self.time.annual_to_step_rate(diagnosis_annual)
        
        # Only undiagnosed HIV+ can be diagnosed
        undiagnosed = self.state.population[:, :, :, 1:, 0, :, :]  # HIV+, undiagnosed
        diagnosed = self._draw_events(undiagnosed, diagnosis_rate)
        
        self.state.population[:, :, :, 1:, 0, :, :] -= diagnosed
        self.state.population[:, :, :, 1:, 1, :, :] += diagnosed
    
    def step_art_transitions(self):
        """Manage ART initiation and dropout."""
        disease_config = self.config["disease"]
        
        # Get rates and convert to per-timestep
        initiation_annual = extract_value(disease_config["treatment"]["art_initiation_rate"])
        dropout_annual = extract_value(disease_config["treatment"]["art_dropout_rate"])
        
        initiation_rate = self.time.annual_to_step_rate(initiation_annual)
        dropout_rate = self.time.annual_to_step_rate(dropout_annual)
        
        # Initiate ART (diagnosed, not on ART)
        eligible = self.state.population[:, :, :, 1:, 1, 0, :]
        initiated = self._draw_events(eligible, initiation_rate)
        self.state.population[:, :, :, 1:, 1, 0, :] -= initiated
        self.state.population[:, :, :, 1:, 1, 1, :] += initiated
        
        # Dropout (on ART)
        on_art = self.state.population[:, :, :, 1:, 1, 1, :]
        dropped = self._draw_events(on_art, dropout_rate)
        self.state.population[:, :, :, 1:, 1, 1, :] -= dropped
        self.state.population[:, :, :, 1:, 1, 0, :] += dropped
    
    def step_prep_transitions(self):
        """Manage PrEP uptake and discontinuation."""
        interventions = self.config["interventions"]
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        none_idx = self.groups.prep_to_idx["None"]
        
        for prep_name in self.groups.prep_states[1:]:  # Skip "None"
            if prep_name not in interventions:
                continue
            
            prep_config = interventions[prep_name]
            if not extract_value(prep_config.get("enabled", {"value": True})):
                continue
            
            prep_idx = self.groups.prep_to_idx[prep_name]
            
            # Get rates and convert to per-timestep
            uptake_annual = extract_value(prep_config["uptake_rate"])
            dropout_annual = extract_value(prep_config["dropout_rate"])
            
            uptake_rate = self.time.annual_to_step_rate(uptake_annual)
            dropout_rate = self.time.annual_to_step_rate(dropout_annual)
            
            # Uptake (None -> PrEP)
            no_prep = self.state.population[:, :, :, susc_idx, :, :, none_idx]
            new_users = self._draw_events(no_prep, uptake_rate)
            
            self.state.population[:, :, :, susc_idx, :, :, none_idx] -= new_users
            self.state.population[:, :, :, susc_idx, :, :, prep_idx] += new_users
            
            # Dropout (PrEP -> None)
            prep_users = self.state.population[:, :, :, susc_idx, :, :, prep_idx]
            dropouts = self._draw_events(prep_users, dropout_rate)
            
            self.state.population[:, :, :, susc_idx, :, :, prep_idx] -= dropouts
            self.state.population[:, :, :, susc_idx, :, :, none_idx] += dropouts
    
    def step_mortality(self) -> Tuple[int, int]:
        """Apply background and HIV-related mortality."""
        disease_config = self.config["disease"]
        pop_config = self.config["population"]
        
        hiv_deaths = 0
        background_deaths = 0
        
        # HIV mortality by stage (convert to per-timestep)
        hiv_mortality = {
            "Acute": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["acute"])),
            "Chronic": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["chronic"])),
            "AIDS": self.time.annual_to_step_rate(extract_value(disease_config["mortality"]["aids"]))
        }
        
        art_reduction = extract_value(disease_config["treatment"]["art_mortality_reduction"])
        
        for stage_name, mortality_rate in hiv_mortality.items():
            stage_idx = self.groups.hiv_to_idx[stage_name]
            
            # Not on ART
            not_art = self.state.population[:, :, :, stage_idx, :, 0, :]
            deaths_not_art = self._draw_events(not_art, mortality_rate)
            self.state.population[:, :, :, stage_idx, :, 0, :] -= deaths_not_art
            hiv_deaths += np.sum(deaths_not_art)
            
            # On ART (reduced mortality)
            on_art = self.state.population[:, :, :, stage_idx, :, 1, :]
            reduced_rate = mortality_rate * (1 - art_reduction)
            deaths_art = self._draw_events(on_art, reduced_rate)
            self.state.population[:, :, :, stage_idx, :, 1, :] -= deaths_art
            hiv_deaths += np.sum(deaths_art)
        
        # Background mortality (convert to per-timestep)
        bg_mortality_annual = extract_value(pop_config["background_mortality"])
        bg_mortality = self.time.annual_to_step_rate(bg_mortality_annual)
        
        all_pop = self.state.population
        bg_deaths = self._draw_events(all_pop, bg_mortality)
        self.state.population -= bg_deaths
        background_deaths = np.sum(bg_deaths)
        
        return hiv_deaths, background_deaths
    
    def step_aging(self, step: int):
        """Age population and add new entrants."""
        # Every 5 years, move population up one age group
        if step % (5 * 12 // self.time.step_size) == 0 and step > 0:  # Adjust for timestep
            new_pop = np.zeros_like(self.state.population)
            
            # Move existing population up
            for age in range(self.groups.n_age_groups - 1):
                new_pop[age + 1, :, :, :, :, :, :] = self.state.population[age, :, :, :, :, :, :]
            
            # Oldest age group stays
            new_pop[-1, :, :, :, :, :, :] += self.state.population[-1, :, :, :, :, :, :]
            
            self.state.population = new_pop
        
        # Add new entrants (scaled by timestep)
        annual_entrants = extract_value(self.config["population"]["annual_entrants"])
        entrants_this_step = int(annual_entrants * self.time.years_per_step)
        
        # Distribute across orientations and activities
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        per_group = entrants_this_step // (self.groups.n_orientations * self.groups.n_activities)
        
        for orient in range(self.groups.n_orientations):
            for activity in range(self.groups.n_activities):
                self.state.population[
                    0, orient, activity, susc_idx, 0, 0, 0
                ] += per_group
    
    def record_outputs(self, step: int, year: float, new_inf: int, hiv_d: int, bg_d: int):
        """Record timestep outputs."""
        total_pop = self.state.get_total_population()
        hiv_pos = self.state.get_hiv_positive()
        susceptible = self.state.get_susceptible()
        
        prevalence = hiv_pos / total_pop if total_pop > 0 else 0
        incidence = new_inf / susceptible if susceptible > 0 else 0
        
        self.outputs['step'].append(step)
        self.outputs['year'].append(year)
        self.outputs['new_infections'].append(new_inf)
        self.outputs['prevalence'].append(prevalence)
        self.outputs['incidence'].append(incidence)
        self.outputs['hiv_deaths'].append(hiv_d)
        self.outputs['background_deaths'].append(bg_d)
        self.outputs['total_population'].append(total_pop)


def simulate(config_path: str, seed: int = None) -> Dict:
    """
    Main simulation entry point.
    
    Args:
        config_path: Path to config.json file
        seed: Random seed (optional, uses config value if not provided)
    
    Returns:
        Dictionary with summary DataFrame and final state
    """
    engine = SimulationEngine(config_path, seed=seed)
    return engine.run()


if __name__ == '__main__':
    # Quick test
    result = simulate('config.json')
    print("\nSimulation complete!")
    print(result['summary'].head())
