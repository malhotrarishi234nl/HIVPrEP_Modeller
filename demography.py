"""
Demography module for HIV Simulation V4

Handles:
- Pre-sexual childhood pool (age 0-14, biological sex only)
- Sexual debut mechanism (age 15: orientation + activity assignment)
- Fertility (ASFR with year multipliers)
- Life tables (age-specific survival for both children and adults)
- Population adjustments (calibration multipliers)
- Multiple demography modes

Population flow (V4):
  Birth -> Childhood pool (0-4, 5-9, 10-14) -> Sexual debut (age 15)
       -> Orientation + Activity assignment -> Main population tensor

Children are NOT part of the HIV transmission system.
They have only: age, biological sex, background mortality, aging.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional, Any


def extract_value(config_item: Any) -> Any:
    """Extract value from config item."""
    if isinstance(config_item, dict) and "value" in config_item:
        return config_item["value"]
    return config_item


class ChildhoodPool:
    """
    Pre-sexual population pool for children aged 0-14.

    Children have only two attributes:
    - Age bin: 0-4, 5-9, 10-14 (three 5-year bins)
    - Biological sex: female (0), male (1)

    Children are NOT eligible for:
    - HIV infection, diagnosis, ART, or PrEP
    - Sexual orientation or activity assignment
    - Participation in the HIV transmission system

    At sexual debut (age 15), children leave this pool and enter
    the main population tensor with assigned orientation and activity.
    """

    # Sex indices
    FEMALE = 0
    MALE = 1
    N_SEXES = 2

    def __init__(self, config: Dict):
        """Initialize childhood pool from config."""
        childhood_config = config.get("childhood", {})
        self.enabled = extract_value(childhood_config.get("enabled", {"value": True}))

        # Childhood age bins
        self.age_bins = extract_value(
            childhood_config.get("age_bins", {"value": ["0-4", "5-9", "10-14"]})
        )
        self.n_bins = len(self.age_bins)

        # Childhood mortality rate (annual, used if life table not available)
        self.mortality_annual = extract_value(
            childhood_config.get("background_mortality_annual", {"value": 0.005})
        )

        # Population array: (n_childhood_bins, 2) where dim 1 = sex (female=0, male=1)
        self.population = np.zeros((self.n_bins, self.N_SEXES), dtype=np.int64)

        # Track total childhood deaths for reporting
        self.last_step_deaths = 0

    def initialize(self, config: Dict, adult_population: np.ndarray, groups):
        """
        Initialize childhood pool.

        If config specifies 'auto', compute from adult population size.
        Otherwise use explicit per-bin counts.
        """
        if not self.enabled:
            return

        childhood_config = config.get("childhood", {})
        initial_per_bin = extract_value(
            childhood_config.get("initial_per_bin", {"value": "auto"})
        )

        if initial_per_bin == "auto":
            # Compute from adult population: average adults per 5-year bin
            total_adults = np.sum(adult_population)
            n_adult_bins = adult_population.shape[0]  # age dimension
            avg_per_bin = total_adults / max(n_adult_bins, 1)

            # Split by sex using fertility config sex ratio
            fertility_config = config.get("fertility", {})
            frac_male = extract_value(
                fertility_config.get("fraction_male_newborns", {"value": 0.512195})
            )

            male_per_bin = int(avg_per_bin * frac_male)
            female_per_bin = int(avg_per_bin * (1 - frac_male))

            for bin_idx in range(self.n_bins):
                self.population[bin_idx, self.FEMALE] = female_per_bin
                self.population[bin_idx, self.MALE] = male_per_bin
        else:
            # Explicit counts per bin
            male_count = initial_per_bin.get("male", 0)
            female_count = initial_per_bin.get("female", 0)

            for bin_idx in range(self.n_bins):
                self.population[bin_idx, self.FEMALE] = female_count
                self.population[bin_idx, self.MALE] = male_count

    def get_total(self) -> int:
        """Total children in the pool."""
        return int(np.sum(self.population))

    def get_by_sex(self) -> Tuple[int, int]:
        """Return (total_female, total_male) children."""
        return (
            int(np.sum(self.population[:, self.FEMALE])),
            int(np.sum(self.population[:, self.MALE]))
        )

    def add_births(self, n_female: int, n_male: int):
        """
        Add newborns to the youngest childhood bin (0-4).

        Newborns have only biological sex. No orientation,
        activity, or HIV attributes are assigned.
        """
        self.population[0, self.FEMALE] += n_female
        self.population[0, self.MALE] += n_male

    def apply_mortality(self, time_system, rng: np.random.RandomState,
                        deterministic: bool, life_table_config: Dict = None) -> int:
        """
        Apply background mortality to all children.

        Uses life table if available, otherwise constant childhood rate.
        Returns total childhood deaths.
        """
        total_deaths = 0
        life_table_enabled = extract_value(
            life_table_config.get("enabled", {"value": False})
        ) if life_table_config else False

        if life_table_enabled:
            # Use age-specific childhood survival from life table
            survival_data = extract_value(
                life_table_config.get("survival_by_age_limit", {"value": []})
            )
            # Childhood age midpoints: 2.5, 7.5, 12.5
            child_midpoints = [2.5, 7.5, 12.5]

            for bin_idx in range(self.n_bins):
                midpoint = child_midpoints[bin_idx] if bin_idx < len(child_midpoints) else 12.5

                # Find appropriate survival
                male_surv = 1.0
                female_surv = 1.0
                for entry in survival_data:
                    if midpoint < entry["age_limit"]:
                        male_surv = entry.get("male_survival", 1.0)
                        female_surv = entry.get("female_survival", 1.0)
                        break

                # Convert to per-timestep mortality
                male_mort = 1 - male_surv ** time_system.years_per_step
                female_mort = 1 - female_surv ** time_system.years_per_step

                # Apply to each sex
                for sex_idx, mort_rate in [(self.FEMALE, female_mort), (self.MALE, male_mort)]:
                    pop = self.population[bin_idx, sex_idx]
                    if pop > 0:
                        if deterministic:
                            deaths = int(pop * mort_rate)
                        else:
                            deaths = rng.binomial(pop, mort_rate)
                        self.population[bin_idx, sex_idx] -= deaths
                        total_deaths += deaths
        else:
            # Constant childhood mortality rate
            mort_rate = 1 - (1 - self.mortality_annual) ** time_system.years_per_step

            for bin_idx in range(self.n_bins):
                for sex_idx in range(self.N_SEXES):
                    pop = self.population[bin_idx, sex_idx]
                    if pop > 0:
                        if deterministic:
                            deaths = int(pop * mort_rate)
                        else:
                            deaths = rng.binomial(pop, mort_rate)
                        self.population[bin_idx, sex_idx] -= deaths
                        total_deaths += deaths

        self.last_step_deaths = total_deaths
        return total_deaths

    def process_continuous_debut(self, time_system, rng: np.random.RandomState,
                                  deterministic: bool = False) -> Tuple[int, int]:
        """
        Process continuous sexual debut every timestep (PATCH: Sexual Debut Continuous Flow).

        Sexual debut is modeled as a per-timestep probabilistic transition applied to
        the oldest childhood age bin (10-14), which represents children eligible for debut.

        For each timestep:
            p_debut = step_years / age_bin_width_years
            n_debut ~ Binomial(n_childhood, p_debut)

        This ensures smooth, continuous debuts decoupled from age-bin transitions.
        Over one full age bin period, approximately the full cohort will debut.

        Args:
            time_system: TimeSystem object with years_per_step
            rng: random number generator
            deterministic: if True, use expected values

        Returns:
            (debuting_females, debuting_males) for this timestep
        """
        # Age bin width (hardcoded as 5 years for childhood bins: 0-4, 5-9, 10-14)
        age_bin_width_years = 5.0

        # Probability of debut in this timestep
        p_debut = time_system.years_per_step / age_bin_width_years

        # Sample debuts from oldest childhood bin (10-14)
        oldest_bin_idx = self.n_bins - 1
        n_females = int(self.population[oldest_bin_idx, self.FEMALE])
        n_males = int(self.population[oldest_bin_idx, self.MALE])

        if n_females == 0 and n_males == 0:
            return 0, 0

        # Sample debuts
        if deterministic:
            debuting_females = int(n_females * p_debut)
            debuting_males = int(n_males * p_debut)
        else:
            debuting_females = rng.binomial(n_females, p_debut) if n_females > 0 else 0
            debuting_males = rng.binomial(n_males, p_debut) if n_males > 0 else 0

        # Remove debuting children from oldest bin
        self.population[oldest_bin_idx, self.FEMALE] -= debuting_females
        self.population[oldest_bin_idx, self.MALE] -= debuting_males

        return debuting_females, debuting_males

    def flush_remaining_debut_eligible(self) -> Tuple[int, int]:
        """
        Flush all remaining individuals from debut-eligible bin (DISCRETE AGING ONLY).

        Used with discrete 5-year aging to ensure children don't age out of the
        10-14 bin without sexually debuting. Called at aging boundaries BEFORE
        bin shifts.

        NOTE: Only used when aging_method='discrete'. Not needed for 'fractional' aging.

        See: REFERENCE_DISCRETE_AGING_OLD.md for details.

        Returns:
            (remaining_females, remaining_males) who are flushed to debut
        """
        oldest_bin_idx = self.n_bins - 1

        # GUARDRAIL: Assert we're flushing the correct debut-eligible bin
        if oldest_bin_idx >= len(self.age_bins):
            raise ValueError(
                f"Debut bin index {oldest_bin_idx} out of range for {len(self.age_bins)} age bins."
            )

        debut_bin_label = self.age_bins[oldest_bin_idx]
        expected_debut_bin = "10-14"

        if debut_bin_label != expected_debut_bin:
            raise ValueError(
                f"SAFETY CHECK FAILED: Attempting to flush bin '{debut_bin_label}' but expected "
                f"'{expected_debut_bin}'. Configuration error."
            )

        remaining_females = int(self.population[oldest_bin_idx, self.FEMALE])
        remaining_males = int(self.population[oldest_bin_idx, self.MALE])

        # Clear the bin (all remaining debut)
        self.population[oldest_bin_idx, self.FEMALE] = 0
        self.population[oldest_bin_idx, self.MALE] = 0

        return remaining_females, remaining_males

    def apply_fractional_aging(self, rng, deterministic: bool) -> Tuple[int, int]:
        """
        Apply fractional aging to childhood bins (PATCH: Fractional Aging 2026-02-10).

        Every timestep:
        - Transfer 20% of each childhood bin to next bin
        - Retain 80% in current bin
        - Oldest bin (10-14): 20% ages out and is returned for sexual debut

        Returns:
            (females_aging_out, males_aging_out) from 10-14 bin who need sexual debut

        See: PATCH_FRACTIONAL_AGING.md for details.
        """
        aging_fraction = 0.2  # 1/5 = 20% per year (for 5-year bins)

        new_pop = np.zeros_like(self.population)

        # For bins 0-4 and 5-9: transfer 20% to next bin
        for bin_idx in range(self.n_bins - 1):
            if deterministic:
                # Deterministic: exact 20% transfer
                staying = (self.population[bin_idx, :] * 0.8).astype(np.int64)
                moving = self.population[bin_idx, :] - staying
            else:
                # Stochastic: binomial draw
                moving = rng.binomial(
                    self.population[bin_idx, :].astype(int),
                    aging_fraction
                )
                staying = self.population[bin_idx, :] - moving

            new_pop[bin_idx, :] += staying
            new_pop[bin_idx + 1, :] += moving

        # Oldest bin (10-14): 20% ages out to sexual debut
        # We return these for immediate sexual debut processing
        if deterministic:
            staying_oldest = (self.population[-1, :] * 0.8).astype(np.int64)
            aging_out = self.population[-1, :] - staying_oldest
        else:
            aging_out = rng.binomial(
                self.population[-1, :].astype(int),
                aging_fraction
            )
            staying_oldest = self.population[-1, :] - aging_out

        new_pop[-1, :] += staying_oldest

        self.population = new_pop

        # Return counts aging out for sexual debut
        females_aging_out = int(aging_out[self.FEMALE])
        males_aging_out = int(aging_out[self.MALE])

        return females_aging_out, males_aging_out

    def age_and_debut(self) -> Tuple[int, int]:
        """
        Age children through childhood bins (DISCRETE AGING ONLY).

        Called at 5-year aging events to shift all bins upward by 100%.
        NOTE: Only used when aging_method='discrete'. Not called for 'fractional' aging.

        Flow:
        - Shift: 5-9 → 10-14, 0-4 → 5-9
        - Clear 0-4 (new births will refill it)
        - Debuts handled separately via flush_remaining_debut_eligible()

        See: REFERENCE_DISCRETE_AGING_OLD.md for details.

        Returns:
            (0, 0) - debuts handled separately
        """
        # Discrete aging: 100% shift every 5 years
        # Shift bins upward
        for bin_idx in range(self.n_bins - 1, 0, -1):
            self.population[bin_idx, :] = self.population[bin_idx - 1, :]

        # Clear youngest bin (births will fill it going forward)
        self.population[0, :] = 0

        # Return 0, 0 since debuts are handled via flush
        return 0, 0

    def validate(self):
        """Ensure no negative counts."""
        if np.any(self.population < 0):
            raise ValueError("Negative childhood population counts detected")


class DemographyModule:
    """
    Manages demographic processes: fertility, childhood, sexual debut,
    mortality, population adjustments.

    V4 population flow:
    Birth -> Childhood pool (0-14) -> Sexual debut (15) -> Main tensor

    Modes:
    - closed: No births, aging and mortality only
    - fixed_net_addition: Constant annual births into childhood pool
    - fertility_table: ASFR-based births into childhood pool
    """

    def __init__(self, config: Dict, groups, time_system):
        self.config = config
        self.groups = groups
        self.time = time_system

        # Extract demography config
        demo_config = config.get("demography", {})
        self.enabled = extract_value(demo_config.get("enabled", {"value": True}))
        self.mode = extract_value(demo_config.get("mode", {"value": "closed"}))

        # Fertility
        self.fertility_config = config.get("fertility", {})
        self.fertility_enabled = extract_value(self.fertility_config.get("enabled", {"value": False}))

        # Life table
        self.life_table_config = config.get("life_table", {})
        self.life_table_enabled = extract_value(self.life_table_config.get("enabled", {"value": False}))

        # Population adjustments
        self.pop_adj_config = config.get("population_adjustments", {})
        self.pop_adj_enabled = extract_value(self.pop_adj_config.get("enabled", {"value": False}))

        # Background mortality (if not using life table)
        demo_config_inner = config.get("demography", {})
        self.background_mortality = extract_value(
            demo_config_inner.get("background_mortality", {"value": 0.008})
        )

        # Sexual debut config
        self.debut_config = config.get("sexual_debut", {})
        self.p_msm = extract_value(self.debut_config.get("p_msm", {"value": 0.05}))

        # Feature flag: use population activity distribution or custom debut distribution
        use_pop_dist = extract_value(
            self.debut_config.get("use_population_activity_distribution", {"value": True})
        )

        if use_pop_dist:
            # Use the same distribution as population initialization
            pop_config = config.get("population", {})
            self.debut_activity_dist = extract_value(
                pop_config.get("activity_distribution",
                              {"value": {"Low": 0.60, "Medium": 0.30, "High": 0.10}})
            )
        else:
            # Use custom debut-specific distribution
            self.debut_activity_dist = extract_value(
                self.debut_config.get("debut_activity_distribution",
                                      {"value": {"Low": 0.55, "Medium": 0.35, "High": 0.10}})
            )

        # Sex ratio at birth
        self.fraction_male = extract_value(
            self.fertility_config.get("fraction_male_newborns", {"value": 0.512195})
        )

        # Initialize childhood pool
        self.childhood = ChildhoodPool(config)

        # Validate
        self._validate()

    def initialize_childhood(self, adult_population: np.ndarray):
        """Initialize childhood pool based on adult population."""
        self.childhood.initialize(self.config, adult_population, self.groups)

    def _validate(self):
        """Validate demography configuration."""
        # Check fertility rates
        if self.fertility_enabled:
            asfr_data = extract_value(self.fertility_config.get("asfr_by_age_limit", {"value": []}))
            for entry in asfr_data:
                birth_rate = entry.get("birth_rate", 0)
                if birth_rate < 0:
                    raise ValueError(f"ASFR birth_rate must be >= 0, got {birth_rate}")

        # Check debut activity distribution sums to 1
        total = sum(self.debut_activity_dist.values())
        if not np.isclose(total, 1.0, atol=1e-6):
            raise ValueError(f"Debut activity distribution must sum to 1.0, got {total}")

        # Check p_msm in [0, 1]
        if not 0 <= self.p_msm <= 1:
            raise ValueError(f"p_msm must be in [0,1], got {self.p_msm}")

        # Check life table survival
        if self.life_table_enabled:
            survival_data = extract_value(
                self.life_table_config.get("survival_by_age_limit", {"value": []})
            )
            for entry in survival_data:
                for key in ['male_survival', 'female_survival']:
                    if key in entry:
                        surv = entry[key]
                        if not (0 <= surv <= 1):
                            raise ValueError(f"Survival probability must be in [0,1], got {surv}")

        # Check population multipliers
        if self.pop_adj_enabled:
            multipliers = extract_value(
                self.pop_adj_config.get("year_multipliers", {"value": {}})
            )
            for year, mult in multipliers.items():
                if mult <= 0:
                    raise ValueError(f"Population multiplier must be > 0, got {mult} for year {year}")

    def apply_population_adjustments(self, population: np.ndarray, current_year: float) -> np.ndarray:
        """
        Apply exogenous population multipliers (for calibration).
        Returns adjusted population array.
        """
        if not self.pop_adj_enabled:
            return population

        multipliers = extract_value(self.pop_adj_config.get("year_multipliers", {"value": {}}))
        if not multipliers:
            return population

        years = sorted([float(y) for y in multipliers.keys()])
        values = [multipliers[str(int(y))] for y in years]

        if current_year <= years[0]:
            multiplier = values[0]
        elif current_year >= years[-1]:
            multiplier = values[-1]
        else:
            multiplier = np.interp(current_year, years, values)

        if multiplier == 1.0:
            return population

        adjusted = (population * multiplier).astype(np.int64)
        return adjusted

    def calculate_births(self, population: np.ndarray, current_year: float,
                         rng: np.random.RandomState, deterministic: bool = False) -> int:
        """
        Calculate number of births using fertility table.
        Returns number of births (these go into childhood pool, NOT main tensor).
        """
        if not self.enabled or not self.fertility_enabled:
            return 0

        asfr_data = extract_value(self.fertility_config.get("asfr_by_age_limit", {"value": []}))
        if not asfr_data:
            return 0

        # Get year multiplier if enabled
        multiplier = 1.0
        year_mult_config = self.fertility_config.get("year_multipliers", {})
        if extract_value(year_mult_config.get("enabled", {"value": False})):
            mult_values = extract_value(year_mult_config.get("values", {"value": {}}))
            if mult_values:
                years = sorted([float(y) for y in mult_values.keys()])
                values = [mult_values[str(int(y))] for y in years]

                if current_year <= years[0]:
                    multiplier = values[0]
                elif current_year >= years[-1]:
                    multiplier = values[-1]
                else:
                    multiplier = np.interp(current_year, years, values)

        # Count women of reproductive age by age group
        total_births = 0
        female_idx = self.groups.orient_to_idx.get("Female", 0)

        for age_idx, age_group_name in enumerate(self.groups.age_groups):
            age_min = self.groups.age_min[age_idx]
            age_max = self.groups.age_max[age_idx]
            age_midpoint = (age_min + age_max) / 2

            # Find appropriate ASFR for this age
            asfr = 0.0
            for i, entry in enumerate(asfr_data):
                if age_midpoint < entry["age_limit"]:
                    if i > 0:
                        asfr = asfr_data[i - 1]["birth_rate"]
                    break
            else:
                if asfr_data:
                    asfr = asfr_data[-1]["birth_rate"]

            if asfr == 0:
                continue

            # Count women in this age group (all HIV states)
            women_count = np.sum(population[age_idx, female_idx, :, :, :, :, :])

            if women_count == 0:
                continue

            births_expected = women_count * asfr * self.time.years_per_step * multiplier

            if deterministic:
                births = int(np.round(births_expected))
            else:
                births = rng.poisson(births_expected)

            total_births += births

        return max(0, total_births)

    def add_births_to_childhood(self, n_births: int, rng: np.random.RandomState,
                                deterministic: bool = False):
        """
        Add newborns to childhood pool (age 0-4 bin).

        Newborns receive only biological sex. No orientation,
        activity, or HIV attributes are assigned at birth.

        This is the ONLY entry point for new individuals.
        """
        if n_births <= 0:
            return

        # Split by biological sex
        if deterministic:
            n_male = int(n_births * self.fraction_male)
        else:
            n_male = rng.binomial(n_births, self.fraction_male)
        n_female = n_births - n_male

        self.childhood.add_births(n_female, n_male)

    def calculate_fixed_births(self) -> int:
        """Calculate births for fixed_net_addition mode."""
        if not self.enabled or self.mode != "fixed_net_addition":
            return 0

        demo_config = self.config.get("demography", {})
        fixed_config = demo_config.get("fixed_net_addition", {})
        annual_entrants = extract_value(fixed_config.get("annual_entrants", {"value": 0}))

        return int(annual_entrants * self.time.years_per_step)

    def apply_childhood_mortality(self, rng: np.random.RandomState,
                                  deterministic: bool = False) -> int:
        """
        Apply background mortality to childhood pool.
        Returns total childhood deaths.
        """
        return self.childhood.apply_mortality(
            self.time, rng, deterministic, self.life_table_config
        )

    def process_sexual_debut(self, debuting_females: int, debuting_males: int,
                             population: np.ndarray,
                             rng: np.random.RandomState,
                             deterministic: bool = False) -> Tuple[np.ndarray, int]:
        """
        Process sexual debut: assign orientation and activity to debuting children.

        Rules (NON-NEGOTIABLE):
        - Females -> always assigned "Female" orientation
        - Males -> "MSM" with probability p_msm, else "Heterosexual_Male"
        - Activity level assigned probabilistically from debut_activity_distribution
        - All enter as: Susceptible, Undiagnosed, Not_on_ART, No_PrEP

        Args:
            debuting_females: number of females aging out of childhood
            debuting_males: number of males aging out of childhood
            population: current main population tensor
            rng: random number generator
            deterministic: if True, use expected values

        Returns:
            (updated_population, total_debuts)
        """
        total_debuts = 0
        entry_age_idx = 0  # First age bin (15-19) in the main tensor

        # Get state indices
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        undiag_idx = 0
        no_art_idx = 0
        no_prep_idx = 0

        # Get orientation indices
        female_orient_idx = self.groups.orient_to_idx["Female"]
        hetero_male_idx = self.groups.orient_to_idx["Heterosexual_Male"]
        msm_idx = self.groups.orient_to_idx["MSM"]

        # Get activity distribution as ordered arrays
        activity_names = list(self.debut_activity_dist.keys())
        activity_probs = np.array([self.debut_activity_dist[name] for name in activity_names])
        activity_indices = [self.groups.activity_to_idx[name] for name in activity_names]

        # --- Process debuting females ---
        if debuting_females > 0:
            # All females get "Female" orientation
            female_by_activity = self._distribute_to_activities(
                debuting_females, activity_probs, rng, deterministic
            )

            for i, act_idx in enumerate(activity_indices):
                count = female_by_activity[i]
                if count > 0:
                    population[entry_age_idx, female_orient_idx, act_idx,
                               susc_idx, undiag_idx, no_art_idx, no_prep_idx] += count
                    total_debuts += count

        # --- Process debuting males ---
        if debuting_males > 0:
            # Assign MSM vs Heterosexual_Male
            if deterministic:
                n_msm = int(debuting_males * self.p_msm)
            else:
                n_msm = rng.binomial(debuting_males, self.p_msm)
            n_hetero = debuting_males - n_msm

            # Assign activity levels to heterosexual males
            if n_hetero > 0:
                hetero_by_activity = self._distribute_to_activities(
                    n_hetero, activity_probs, rng, deterministic
                )
                for i, act_idx in enumerate(activity_indices):
                    count = hetero_by_activity[i]
                    if count > 0:
                        population[entry_age_idx, hetero_male_idx, act_idx,
                                   susc_idx, undiag_idx, no_art_idx, no_prep_idx] += count
                        total_debuts += count

            # Assign activity levels to MSM
            if n_msm > 0:
                msm_by_activity = self._distribute_to_activities(
                    n_msm, activity_probs, rng, deterministic
                )
                for i, act_idx in enumerate(activity_indices):
                    count = msm_by_activity[i]
                    if count > 0:
                        population[entry_age_idx, msm_idx, act_idx,
                                   susc_idx, undiag_idx, no_art_idx, no_prep_idx] += count
                        total_debuts += count

        return population, total_debuts

    def _distribute_to_activities(self, n: int, probs: np.ndarray,
                                  rng: np.random.RandomState,
                                  deterministic: bool) -> np.ndarray:
        """
        Distribute n individuals across activity levels using multinomial.

        Args:
            n: number of individuals to distribute
            probs: probability array for each activity level (must sum to 1)
            rng: random number generator
            deterministic: if True, use expected values

        Returns:
            array of counts per activity level
        """
        if deterministic:
            # Allocate proportionally, rounding to nearest int
            raw = n * probs
            result = np.floor(raw).astype(np.int64)
            # Distribute remainder to largest fractional parts
            remainder = n - np.sum(result)
            if remainder > 0:
                fractional = raw - result
                indices = np.argsort(-fractional)
                for i in range(int(remainder)):
                    result[indices[i]] += 1
            return result
        else:
            return rng.multinomial(n, probs)

    def calculate_background_mortality(self, population: np.ndarray, current_year: float,
                                       rng: np.random.RandomState, deterministic: bool = False) -> np.ndarray:
        """
        Calculate background mortality for the sexually active population.
        Uses life table if enabled, otherwise uses constant background mortality rate.
        Returns array of deaths (same shape as population).
        """
        if self.life_table_enabled:
            return self._calculate_life_table_mortality(population, current_year, rng, deterministic)
        else:
            return self._calculate_constant_mortality(population, rng, deterministic)

    def _calculate_constant_mortality(self, population: np.ndarray,
                                      rng: np.random.RandomState, deterministic: bool) -> np.ndarray:
        """Calculate mortality using constant background rate."""
        mortality_rate = self.time.annual_to_step_rate(self.background_mortality)

        if deterministic:
            deaths = (population * mortality_rate).astype(np.int64)
        else:
            deaths = rng.binomial(population, mortality_rate)

        return deaths

    def _calculate_life_table_mortality(self, population: np.ndarray, current_year: float,
                                        rng: np.random.RandomState, deterministic: bool) -> np.ndarray:
        """Calculate mortality using life table survival probabilities (adults only)."""
        deaths = np.zeros_like(population)

        survival_data = extract_value(
            self.life_table_config.get("survival_by_age_limit", {"value": []})
        )

        if not survival_data:
            return deaths

        for age_idx in range(self.groups.n_age_groups):
            age_min = self.groups.age_min[age_idx]
            age_max = self.groups.age_max[age_idx]
            age_midpoint = (age_min + age_max) / 2

            # Find appropriate survival probabilities
            male_survival = 1.0
            female_survival = 1.0

            for entry in survival_data:
                if age_midpoint < entry["age_limit"]:
                    male_survival = entry.get("male_survival", 1.0)
                    female_survival = entry.get("female_survival", 1.0)
                    break

            # Convert annual survival to per-timestep
            male_surv_step = male_survival ** self.time.years_per_step
            female_surv_step = female_survival ** self.time.years_per_step

            male_mort_step = 1 - male_surv_step
            female_mort_step = 1 - female_surv_step

            # Apply to appropriate orientations
            female_idx = self.groups.orient_to_idx.get("Female", 0)
            male_idx = self.groups.orient_to_idx.get("Heterosexual_Male", 1)
            msm_idx = self.groups.orient_to_idx.get("MSM", 2)

            # Females
            female_pop = population[age_idx, female_idx, :, :, :, :, :]
            if deterministic:
                deaths[age_idx, female_idx, :, :, :, :, :] = (female_pop * female_mort_step).astype(np.int64)
            else:
                deaths[age_idx, female_idx, :, :, :, :, :] = rng.binomial(female_pop, female_mort_step)

            # Males (both heterosexual and MSM)
            for m_idx in [male_idx, msm_idx]:
                male_pop = population[age_idx, m_idx, :, :, :, :, :]
                if deterministic:
                    deaths[age_idx, m_idx, :, :, :, :, :] = (male_pop * male_mort_step).astype(np.int64)
                else:
                    deaths[age_idx, m_idx, :, :, :, :, :] = rng.binomial(male_pop, male_mort_step)

        return deaths
