"""
Corrected HIV Transmission Module - Authoritative Implementation

Two-step stochastic formulation:
1. Draw HIV+ contacts: n_pos_contacts ~ Binomial(n_contacts, prevalence_stage)
2. Draw infections from HIV+ contacts: new_inf ~ Binomial(n_pos_contacts, p_trans)

CRITICAL: Prevalence ONLY in step 1, NOT in p_trans calculation.
"""

import numpy as np
from typing import Dict, Tuple
from config_utils import extract_value


class TransmissionEngine:
    """
    Corrected transmission calculation using two-step stochastic formulation.
    
    Prevents double-counting of prevalence and correctly handles:
    - Stage-specific prevalence (acute/chronic/AIDS)
    - ART stratification (on ART vs off ART)
    - PrEP stratification (by PrEP state)
    """
    
    def __init__(self, config: Dict, groups, time_system):
        self.config = config
        self.groups = groups
        self.time = time_system

        # Extract beta values by stage — route-specific or legacy
        disease_config = config["disease"]
        trans_config = disease_config["transmission"]
        adv_config = trans_config.get("advanced", {})
        adv_enabled = adv_config.get("enabled", False)

        if adv_enabled and "beta_per_route" in adv_config:
            self.route_specific = True
            self._init_route_specific_betas(adv_config)
        else:
            self.route_specific = False
            self.beta = {
                "Acute": extract_value(trans_config["beta_acute"]),
                "Chronic": extract_value(trans_config["beta_chronic"]),
                "AIDS": extract_value(trans_config["beta_aids"])
            }
        
        # ART transmission reduction
        self.art_transmission_reduction = extract_value(
            disease_config["treatment"]["art_transmission_reduction"]
        )
        
        # Condom modifiers by activity
        self.condom_modifiers = extract_value(
            config["mixing"]["condom_usage"]
        )
        
        # PrEP efficacies
        self.prep_efficacy = {}
        for intervention_name, intervention_config in config.get("interventions", {}).items():
            if extract_value(intervention_config.get("enabled", {"value": True})):
                self.prep_efficacy[intervention_name] = extract_value(
                    intervention_config["efficacy"]
                )

        # V12.2: Inject Lenacapavir_Waning efficacy.
        # Lenacapavir_Waning is a synthetic engine state (not a config intervention),
        # so it is not picked up by the loop above. Efficacy = waning_efficacy param
        # (default 0.99, same as active lena). Source: Bekker et al. NEJM Evidence 2024 —
        # C_trough remains >4x EC95 throughout the full 6-month dosing interval.
        lena_cfg = config.get("interventions", {}).get("Lenacapavir", {})
        if lena_cfg and extract_value(lena_cfg.get("enabled", {"value": True})):
            waning_eff = extract_value(lena_cfg.get("waning_efficacy", {"value": 0.99}))
            self.prep_efficacy["Lenacapavir_Waning"] = waning_eff
    
    # ── V11.3 route-specific beta initialisation ──────────────────────────
    def _init_route_specific_betas(self, adv_config: Dict):
        """Build the 12-beta lookup table and dynamic route mapping."""
        mult_acute = extract_value(adv_config["stage_multiplier_acute"])
        mult_aids = extract_value(adv_config["stage_multiplier_aids"])
        self.msm_versatility = extract_value(
            adv_config.get("msm_versatility", {"value": 0.5})
        )

        # Build beta_by_route: {route_name: {"Chronic": x, "Acute": x, "AIDS": x}}
        self.beta_by_route = {}
        for route_name, route_cfg in adv_config["beta_per_route"].items():
            if isinstance(route_cfg, str):
                continue  # skip any metadata keys
            b_chronic = extract_value(route_cfg["chronic"])
            self.beta_by_route[route_name] = {
                "Chronic": b_chronic,
                "Acute": b_chronic * mult_acute,
                "AIDS": b_chronic * mult_aids,
            }

        # Build _route_lookup DYNAMICALLY from self.groups.orientations
        orient_names = self.groups.orientations  # list of strings
        female_key = next(
            (k for k in orient_names if "female" in k.lower()), None
        )
        hetmale_key = next(
            (k for k in orient_names
             if "het" in k.lower() and "male" in k.lower()), None
        )
        msm_key = next(
            (k for k in orient_names if "msm" in k.lower()), None
        )

        if not all([female_key, hetmale_key, msm_key]):
            raise ValueError(
                f"Cannot resolve orientation keys for route-specific betas. "
                f"Found orientations: {orient_names}"
            )

        # Map (susceptible_orientation, infectious_orientation) → route name
        self._route_lookup = {
            (female_key, hetmale_key): "vaginal_mtf",    # F susceptible ← M infectious
            (hetmale_key, female_key): "vaginal_ftm",    # M susceptible ← F infectious
            (msm_key, msm_key): "msm",                   # split into URAI/UIAI at runtime
        }

        # Store orientation index for MSM (used in inner loop)
        self._msm_orient_idx = self.groups.orient_to_idx[msm_key]

    def _select_beta(self, orient_from: int, orient_to: int, stage_name: str):
        """
        Return the beta for a (susceptible, infectious, stage) triple.

        For non-MSM pairs: returns a scalar beta.
        For MSM↔MSM: returns None (handled separately in inner loop).
        For cross-type pairs not in the lookup: returns 0 (no transmission).
        """
        orient_from_name = self.groups.orientations[orient_from]
        orient_to_name = self.groups.orientations[orient_to]
        route_key = (orient_from_name, orient_to_name)

        route = self._route_lookup.get(route_key)
        if route is None:
            return 0.0  # No transmission between these orientations
        if route == "msm":
            return None  # Signal: caller must handle URAI/UIAI split
        return self.beta_by_route[route][stage_name]

    def _calc_msm_infections(self, n_pos_contacts: int, stage_name: str,
                             condom_modifier: float, prep_efficacy: float,
                             art_modifier: float, deterministic: bool,
                             rng: np.random.RandomState) -> int:
        """
        V11.3: Split MSM contacts into receptive/insertive sub-passes.

        Uses Bernoulli draw governed by msm_versatility to split n_pos_contacts
        into receptive (URAI beta) and insertive (UIAI beta) fractions, then runs
        the standard two-step binomial on each fraction independently.
        """
        beta_r = self.beta_by_route["anal_receptive"][stage_name]
        beta_i = self.beta_by_route["anal_insertive"][stage_name]

        # Split contacts by role
        if deterministic:
            n_receptive = int(n_pos_contacts * self.msm_versatility)
        else:
            n_receptive = rng.binomial(n_pos_contacts, self.msm_versatility)
        n_insertive = n_pos_contacts - n_receptive

        total = 0
        for n_c, beta in [(n_receptive, beta_r), (n_insertive, beta_i)]:
            if n_c <= 0:
                continue
            p_trans = beta * condom_modifier * (1 - prep_efficacy) * art_modifier
            p_trans = np.clip(p_trans, 0, 1)
            if deterministic:
                total += int(n_c * p_trans)
            else:
                total += rng.binomial(n_c, p_trans)
        return total

    def _calc_msm_expected_probability(self, stage_name: str,
                                       condom_modifier: float,
                                       prep_efficacy: float,
                                       art_modifier: float) -> float:
        """
        Deterministic MSM transmission probability per HIV-positive contact.

        The stochastic path still splits contacts into receptive/insertive
        sub-passes. In deterministic mode, use the expected mixture directly so
        sparse expected infections are not lost to role-split flooring.
        """
        beta_r = self.beta_by_route["anal_receptive"][stage_name]
        beta_i = self.beta_by_route["anal_insertive"][stage_name]
        beta = (
            self.msm_versatility * beta_r
            + (1.0 - self.msm_versatility) * beta_i
        )
        p_trans = beta * condom_modifier * (1 - prep_efficacy) * art_modifier
        return float(np.clip(p_trans, 0, 1))

    @staticmethod
    def _deterministic_allocate_expected_infections(
        expected: np.ndarray,
        capacity: np.ndarray,
    ) -> np.ndarray:
        """
        Convert expected infection counts to integer events without local floors.

        Cellwise truncation can erase rare transmission when expected counts are
        spread over many cells. This preserves the rounded aggregate expected
        total, then assigns residual events to cells with the largest fractional
        expected counts, never exceeding susceptible capacity.
        """
        expected_arr = np.asarray(expected, dtype=float)
        capacity_arr = np.asarray(capacity).astype(np.int64, copy=False)
        if expected_arr.shape != capacity_arr.shape:
            raise ValueError(
                "Expected infections and capacity must have the same shape, "
                f"got {expected_arr.shape} and {capacity_arr.shape}"
            )
        if expected_arr.size == 0:
            return np.zeros_like(capacity_arr, dtype=np.int64)
        if np.any(~np.isfinite(expected_arr)) or np.any(expected_arr < 0):
            raise ValueError("Expected infections must be finite and non-negative")

        capped_expected = np.minimum(expected_arr, capacity_arr.astype(float))
        base = np.floor(capped_expected).astype(np.int64)
        base = np.minimum(base, capacity_arr)
        target = min(int(np.round(capped_expected.sum())), int(capacity_arr.sum()))
        remainder = target - int(base.sum())

        if remainder <= 0:
            return base

        infections = base.copy()
        fractional = (capped_expected - base).ravel()
        residual_capacity = (capacity_arr - base).ravel()
        eligible = np.flatnonzero(residual_capacity > 0)
        if eligible.size == 0:
            return infections

        order = eligible[np.argsort(-fractional[eligible], kind="stable")]
        take = min(remainder, order.size)
        flat = infections.ravel()
        flat[order[:take]] += 1
        return infections

    def calculate_transmission(self,
                              population: np.ndarray,
                              contacts: np.ndarray,
                              rng: np.random.RandomState,
                              deterministic: bool = False) -> Tuple[int, np.ndarray]:
        """
        Calculate new HIV infections using corrected two-step formulation.
        
        Args:
            population: Population state tensor
            contacts: Contact matrix [orient_from, act_from, orient_to, act_to]
            rng: Random number generator
            deterministic: If True, use expected values
        
        Returns:
            (total_new_infections, infections_array)
            infections_array shape: [age, orient, activity, prep]
        """
        # Storage for new infections by susceptible group
        infections = np.zeros((
            self.groups.n_age_groups,
            self.groups.n_orientations,
            self.groups.n_activities,
            self.groups.n_prep
        ), dtype=np.int64)
        expected_infections = np.zeros_like(infections, dtype=float)
        
        total_new_infections = 0
        
        # Get indices
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        acute_idx = self.groups.hiv_to_idx["Acute"]
        chronic_idx = self.groups.hiv_to_idx["Chronic"]
        aids_idx = self.groups.hiv_to_idx["AIDS"]
        
        # Iterate over contact pairs
        for orient_to in range(self.groups.n_orientations):  # Partner orientation
            for act_to in range(self.groups.n_activities):      # Partner activity
                
                # Calculate partner group population and stage-specific prevalence
                partner_total = np.sum(population[:, orient_to, act_to, :, :, :, :])
                
                if partner_total == 0:
                    continue  # Skip if no partners
                
                # Stage-specific prevalence in partner group
                stage_prevalence = {}
                for stage_name, stage_idx in [("Acute", acute_idx), 
                                               ("Chronic", chronic_idx), 
                                               ("AIDS", aids_idx)]:
                    
                    # Split by ART status for accurate transmission probability
                    infected_off_art = np.sum(
                        population[:, orient_to, act_to, stage_idx, :, 0, :]
                    )
                    infected_on_art = np.sum(
                        population[:, orient_to, act_to, stage_idx, :, 1, :]
                    )
                    
                    stage_prevalence[stage_name] = {
                        'off_art': infected_off_art / partner_total if partner_total > 0 else 0,
                        'on_art': infected_on_art / partner_total if partner_total > 0 else 0
                    }
                
                # Now iterate over susceptible groups
                for orient_from in range(self.groups.n_orientations):
                    for act_from in range(self.groups.n_activities):
                        
                        # Get number of contacts from this susceptible group to partner group
                        n_contacts = contacts[orient_from, act_from, orient_to, act_to]
                        
                        if n_contacts == 0:
                            continue
                        
                        # Get condom modifier for susceptible group
                        activity_name_from = self.groups.activities[act_from]
                        condom_modifier = self.condom_modifiers.get(activity_name_from, 1.0)
                        
                        # Iterate over ages and PrEP states in susceptible group
                        for age_idx in range(self.groups.n_age_groups):
                            for prep_idx in range(self.groups.n_prep):
                                
                                # Get susceptible population in this cell
                                susceptible_pop = population[
                                    age_idx, orient_from, act_from, 
                                    susc_idx, 0, 0, prep_idx
                                ]
                                
                                if susceptible_pop == 0:
                                    continue
                                
                                # Get PrEP efficacy for this cell
                                prep_name = self.groups.prep_states[prep_idx]
                                prep_efficacy = self.prep_efficacy.get(prep_name, 0.0)
                                
                                # Proportion of contacts from this cell
                                # (distribute contacts proportionally by susceptible count)
                                total_susceptible_in_group = np.sum(
                                    population[:, orient_from, act_from, susc_idx, 0, 0, :]
                                )
                                
                                if total_susceptible_in_group == 0:
                                    continue
                                
                                cell_proportion = susceptible_pop / total_susceptible_in_group
                                # V13.22: deterministic transmission keeps the
                                # fractional contact expectation until final
                                # aggregate infection allocation. Stochastic
                                # mode keeps the V11.4 floor + Bernoulli
                                # contact split.
                                contacts_float = n_contacts * cell_proportion
                                if deterministic:
                                    n_contacts_cell = contacts_float
                                else:
                                    n_contacts_cell = int(contacts_float)
                                    frac = contacts_float - n_contacts_cell
                                    if frac > 0 and rng.random() < frac:
                                        n_contacts_cell += 1

                                if n_contacts_cell == 0:
                                    continue
                                
                                # Calculate infections by stage
                                cell_infections = 0

                                for stage_name in ["Acute", "Chronic", "AIDS"]:
                                    # ── V11.3: route-specific beta selection ──
                                    if self.route_specific:
                                        beta_stage = self._select_beta(
                                            orient_from, orient_to, stage_name
                                        )
                                        if beta_stage == 0.0:
                                            continue  # no transmission between these orientations
                                        is_msm_pair = (beta_stage is None)
                                    else:
                                        beta_stage = self.beta[stage_name]
                                        is_msm_pair = False

                                    # Process off-ART partners
                                    prev_off = stage_prevalence[stage_name]['off_art']
                                    if prev_off > 0:
                                        # STEP 1: Draw HIV+ contacts
                                        if deterministic:
                                            n_pos_contacts_off = n_contacts_cell * prev_off
                                        else:
                                            n_pos_contacts_off = rng.binomial(
                                                n_contacts_cell, prev_off
                                            )

                                        if n_pos_contacts_off > 0:
                                            if is_msm_pair:
                                                if deterministic:
                                                    p_trans_off = self._calc_msm_expected_probability(
                                                        stage_name, condom_modifier,
                                                        prep_efficacy, 1.0
                                                    )
                                                    inf_off = n_pos_contacts_off * p_trans_off
                                                else:
                                                    # V11.3 MSM split: two sub-passes
                                                    inf_off = self._calc_msm_infections(
                                                        n_pos_contacts_off, stage_name,
                                                        condom_modifier, prep_efficacy,
                                                        1.0, deterministic, rng
                                                    )
                                            else:
                                                # STEP 2: Draw infections from HIV+ contacts
                                                # p_trans = beta × condom × (1 - prep) × 1.0 (no ART)
                                                p_trans_off = beta_stage * condom_modifier * (1 - prep_efficacy)
                                                p_trans_off = np.clip(p_trans_off, 0, 1)

                                                if deterministic:
                                                    inf_off = n_pos_contacts_off * p_trans_off
                                                else:
                                                    inf_off = rng.binomial(
                                                        n_pos_contacts_off, p_trans_off
                                                    )

                                            if deterministic:
                                                expected_infections[
                                                    age_idx, orient_from, act_from, prep_idx
                                                ] += inf_off
                                            else:
                                                cell_infections += inf_off

                                    # Process on-ART partners
                                    prev_on = stage_prevalence[stage_name]['on_art']
                                    if prev_on > 0:
                                        # STEP 1: Draw HIV+ contacts
                                        if deterministic:
                                            n_pos_contacts_on = n_contacts_cell * prev_on
                                        else:
                                            n_pos_contacts_on = rng.binomial(
                                                n_contacts_cell, prev_on
                                            )

                                        if n_pos_contacts_on > 0:
                                            art_modifier = 1 - self.art_transmission_reduction
                                            if is_msm_pair:
                                                if deterministic:
                                                    p_trans_on = self._calc_msm_expected_probability(
                                                        stage_name, condom_modifier,
                                                        prep_efficacy, art_modifier
                                                    )
                                                    inf_on = n_pos_contacts_on * p_trans_on
                                                else:
                                                    # V11.3 MSM split: two sub-passes (with ART modifier)
                                                    inf_on = self._calc_msm_infections(
                                                        n_pos_contacts_on, stage_name,
                                                        condom_modifier, prep_efficacy,
                                                        art_modifier, deterministic, rng
                                                    )
                                            else:
                                                # STEP 2: Draw infections from HIV+ contacts
                                                # p_trans = beta × condom × (1 - prep) × (1 - art_reduction)
                                                p_trans_on = beta_stage * condom_modifier * (1 - prep_efficacy) * art_modifier
                                                p_trans_on = np.clip(p_trans_on, 0, 1)

                                                if deterministic:
                                                    inf_on = n_pos_contacts_on * p_trans_on
                                                else:
                                                    inf_on = rng.binomial(
                                                        n_pos_contacts_on, p_trans_on
                                                    )

                                            if deterministic:
                                                expected_infections[
                                                    age_idx, orient_from, act_from, prep_idx
                                                ] += inf_on
                                            else:
                                                cell_infections += inf_on
                                
                                if deterministic:
                                    continue

                                # Cap at susceptible population (per partner-group pass)
                                cell_infections = min(cell_infections, susceptible_pop)

                                if cell_infections > 0:
                                    # B2 (V11.4): += not = so infections from multiple partner
                                    # groups accumulate correctly. Per-cell cap enforced in
                                    # apply_infections_to_population at application time.
                                    infections[age_idx, orient_from, act_from, prep_idx] += cell_infections
                                    total_new_infections += cell_infections

        if deterministic:
            capacity = population[:, :, :, susc_idx, 0, 0, :]
            infections = self._deterministic_allocate_expected_infections(
                expected_infections,
                capacity,
            )
            total_new_infections = int(np.sum(infections))
            return total_new_infections, infections

        # V12.7: final cap after accumulation across all partner groups. The
        # per-pass cap above prevents any one partner group from exceeding the
        # susceptible cell, but multiple partner groups can still accumulate
        # above the live susceptible count. Return reported infections and the
        # age/PrEP array after the same cap that application will enforce.
        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for act_idx in range(self.groups.n_activities):
                    for prep_idx in range(self.groups.n_prep):
                        available = population[
                            age_idx, orient_idx, act_idx, susc_idx, 0, 0, prep_idx
                        ]
                        if infections[age_idx, orient_idx, act_idx, prep_idx] > available:
                            infections[age_idx, orient_idx, act_idx, prep_idx] = available

        total_new_infections = int(np.sum(infections))
        return total_new_infections, infections


    def apply_infections_to_population(self,
                                       population: np.ndarray,
                                       infections_array: np.ndarray) -> np.ndarray:
        """
        Move newly infected individuals from Susceptible to Acute.

        All new infections:
        - Move from Susceptible → Acute
        - Lose PrEP status (become prep_idx=0 "None")
        - Remain undiagnosed (dx_idx=0)
        - Not on ART (art_idx=0)

        V11.4 (B2): Per-cell cap applied here against the live susceptible count.
        Prevents over-removal if infections accumulated from multiple partner groups
        (not possible in the current 3-orientation config but enforced for robustness).
        """
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        acute_idx = self.groups.hiv_to_idx["Acute"]

        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for act_idx in range(self.groups.n_activities):
                    for prep_idx in range(self.groups.n_prep):

                        inf_count = infections_array[age_idx, orient_idx, act_idx, prep_idx]

                        if inf_count > 0:
                            # B2 (V11.4): Cap against live susceptible count before removal.
                            available = population[
                                age_idx, orient_idx, act_idx, susc_idx, 0, 0, prep_idx
                            ]
                            inf_count = min(inf_count, available)

                            if inf_count <= 0:
                                continue

                            # Remove from susceptible with this PrEP status
                            population[
                                age_idx, orient_idx, act_idx,
                                susc_idx, 0, 0, prep_idx
                            ] -= inf_count

                            # Add to acute, no PrEP, undiagnosed, no ART
                            population[
                                age_idx, orient_idx, act_idx,
                                acute_idx, 0, 0, 0
                            ] += inf_count

        return population
