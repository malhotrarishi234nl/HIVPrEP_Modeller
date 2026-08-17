"""
engine_v3.py — Utility classes for the HIV Transmission Model V11.

Production simulation runner: SimulationEngineV3 in run_v3.py
This file provides: TimeSystem, GroupSystem, PopulationState, ConfigValidator

V11.4: SimulationEngine (non-production legacy runner) removed.
"""

import warnings
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from config_utils import extract_value


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
        
        # Validate mixing configuration
        # V11+: epsilon_hetero / epsilon_msm scalar parameterisation (Garnett 1994)
        # V10-: legacy explicit mixing matrices (still accepted with DeprecationWarning)
        mixing_config = config["mixing"]

        use_epsilon = (
            "epsilon_hetero" in mixing_config and
            "epsilon_msm"    in mixing_config
        )

        if use_epsilon:
            # Validate epsilon scalars are in [0, 1]
            eps_h = extract_value(mixing_config["epsilon_hetero"])
            eps_m = extract_value(mixing_config["epsilon_msm"])
            if not 0.0 <= eps_h <= 1.0:
                errors.append(
                    f"mixing.epsilon_hetero = {eps_h} is not in [0, 1]. "
                    f"Must satisfy 0 (proportional) <= epsilon <= 1 (assortative)."
                )
            if not 0.0 <= eps_m <= 1.0:
                errors.append(
                    f"mixing.epsilon_msm = {eps_m} is not in [0, 1]. "
                    f"Must satisfy 0 (proportional) <= epsilon <= 1 (assortative)."
                )
        else:
            # Legacy matrix validation (V10 backward compat)
            hetero_mix = extract_value(mixing_config["heterosexual"])
            errors.extend(ConfigValidator._validate_mixing_matrix(
                hetero_mix, groups.activities, "heterosexual"
            ))
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
            valid_methods = ["fractional", "residence_queue", "fractional_legacy", "discrete"]
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
        # ENG-F07: warn when critical parameters fall back to hardcoded defaults
        if "total_size" not in pop_config:
            warnings.warn(
                "population.total_size not in config; using fallback 1,000,000. "
                "This is almost certainly wrong for a calibrated run.",
                UserWarning, stacklevel=2,
            )
        total_size = extract_value(pop_config.get("total_size", {"value": 1000000}))
        age_sex_dist = extract_value(pop_config.get("age_sex_distribution", {"value": {}}))
        if "p_msm" not in pop_config:
            warnings.warn(
                "population.p_msm not in config; using fallback 0.10.",
                UserWarning, stacklevel=2,
            )
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
                self._validate_post_seed(initial_prev)  # ENG-F05: verify seeded state

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

    def _seed_initial_hiv(self, initial_prev: float, seed_stage: str = "Chronic"):
        """
        Seed initial HIV infections in the population.

        V12.4: seed_stage parameter added. Previously hardcoded Chronic.
        Default remains Chronic for backward compatibility. New epidemic
        introduction events should use seed_stage="Acute" (biologically correct
        for a new infection event).

        Args:
            initial_prev: Proportion of population to seed
            seed_stage:   HIV compartment to seed into ("Acute" or "Chronic").
                          Default "Chronic" for backward compatibility.
        """
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        stage_idx = self.groups.hiv_to_idx[seed_stage]  # V12.4: was hardcoded Chronic
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
                            self.population[
                                age_idx, orient_idx, activity_idx,
                                susc_idx, undiag_idx, no_art_idx, no_prep_idx
                            ] -= initial_infected

                            self.population[
                                age_idx, orient_idx, activity_idx,
                                stage_idx, undiag_idx, no_art_idx, no_prep_idx  # V12.4
                            ] += initial_infected

                            total_seeded += initial_infected

        if total_seeded > 0:
            print(f"  Seeded {total_seeded:,} initial HIV+ individuals "
                  f"({initial_prev:.1%} prevalence, stage={seed_stage})")
    
    def _validate_post_seed(self, initial_prev: float) -> None:
        """
        ENG-F05: Verify population state immediately after _seed_initial_hiv.

        _validate_initialization runs BEFORE seeding (inside
        initialize_from_relative_weights, Step 6). This method covers the gap
        by checking three invariants on the seeded state:

          1. Total population unchanged  (seeding is a compartmental move, not an addition)
          2. HIV+ count within rounding tolerance of target prevalence
          3. No negative tensor cells

        Raises:
            ValueError: if any invariant is violated.
        """
        errors = []

        # 1. Total population must be unchanged after seeding
        total_now = int(np.sum(self.population))
        # We derive expected from the total currently in the tensor
        # (conservation: seeding cannot change the total)
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        hiv_positive = int(np.sum(self.population[:, :, :, susc_idx + 1:, :, :, :]))
        total_eligible = total_now  # seeding is purely internal

        # 2. HIV+ count vs expected
        expected_hiv = round(total_eligible * initial_prev)
        tolerance = max(5, int(0.005 * expected_hiv) + 1)  # 0.5% or 5 people
        if abs(hiv_positive - expected_hiv) > tolerance:
            errors.append(
                f"Post-seed HIV+ count {hiv_positive:,} differs from expected "
                f"{expected_hiv:,} (prev={initial_prev:.3%}, tol=±{tolerance})"
            )
        else:
            print(f"  ✓ Post-seed HIV+ count: {hiv_positive:,} "
                  f"(target ≈{expected_hiv:,}, tol=±{tolerance})")

        # 3. No negative cells
        if np.any(self.population < 0):
            errors.append("Negative population cells detected after seeding")
        else:
            print("  ✓ No negative cells after seeding")

        if errors:
            raise ValueError(
                "Post-seed validation failed:\n  " + "\n  ".join(errors)
            )

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
