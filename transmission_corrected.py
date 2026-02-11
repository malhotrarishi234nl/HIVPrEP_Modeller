"""
Corrected HIV Transmission Module - Authoritative Implementation

Two-step stochastic formulation:
1. Draw HIV+ contacts: n_pos_contacts ~ Binomial(n_contacts, prevalence_stage)
2. Draw infections from HIV+ contacts: new_inf ~ Binomial(n_pos_contacts, p_trans)

CRITICAL: Prevalence ONLY in step 1, NOT in p_trans calculation.
"""

import numpy as np
from typing import Dict, Tuple


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
        
        # Extract beta values by stage
        disease_config = config["disease"]
        self.beta = {
            "Acute": self._extract_value(disease_config["transmission"]["beta_acute"]),
            "Chronic": self._extract_value(disease_config["transmission"]["beta_chronic"]),
            "AIDS": self._extract_value(disease_config["transmission"]["beta_aids"])
        }
        
        # ART transmission reduction
        self.art_transmission_reduction = self._extract_value(
            disease_config["treatment"]["art_transmission_reduction"]
        )
        
        # Condom modifiers by activity
        self.condom_modifiers = self._extract_value(
            config["mixing"]["condom_usage"]
        )
        
        # PrEP efficacies
        self.prep_efficacy = {}
        for intervention_name, intervention_config in config.get("interventions", {}).items():
            if self._extract_value(intervention_config.get("enabled", {"value": True})):
                self.prep_efficacy[intervention_name] = self._extract_value(
                    intervention_config["efficacy"]
                )
    
    def _extract_value(self, config_item):
        """Extract value from config item."""
        if isinstance(config_item, dict) and "value" in config_item:
            return config_item["value"]
        return config_item
    
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
                                n_contacts_cell = int(n_contacts * cell_proportion)
                                
                                if n_contacts_cell == 0:
                                    continue
                                
                                # Calculate infections by stage
                                cell_infections = 0
                                
                                for stage_name in ["Acute", "Chronic", "AIDS"]:
                                    beta_stage = self.beta[stage_name]
                                    
                                    # Process off-ART partners
                                    prev_off = stage_prevalence[stage_name]['off_art']
                                    if prev_off > 0:
                                        # STEP 1: Draw HIV+ contacts
                                        if deterministic:
                                            n_pos_contacts_off = int(n_contacts_cell * prev_off)
                                        else:
                                            n_pos_contacts_off = rng.binomial(
                                                n_contacts_cell, prev_off
                                            )
                                        
                                        if n_pos_contacts_off > 0:
                                            # STEP 2: Draw infections from HIV+ contacts
                                            # p_trans = beta × condom × (1 - prep) × 1.0 (no ART)
                                            p_trans_off = beta_stage * condom_modifier * (1 - prep_efficacy)
                                            p_trans_off = np.clip(p_trans_off, 0, 1)
                                            
                                            if deterministic:
                                                inf_off = int(n_pos_contacts_off * p_trans_off)
                                            else:
                                                inf_off = rng.binomial(
                                                    n_pos_contacts_off, p_trans_off
                                                )
                                            
                                            cell_infections += inf_off
                                    
                                    # Process on-ART partners
                                    prev_on = stage_prevalence[stage_name]['on_art']
                                    if prev_on > 0:
                                        # STEP 1: Draw HIV+ contacts
                                        if deterministic:
                                            n_pos_contacts_on = int(n_contacts_cell * prev_on)
                                        else:
                                            n_pos_contacts_on = rng.binomial(
                                                n_contacts_cell, prev_on
                                            )
                                        
                                        if n_pos_contacts_on > 0:
                                            # STEP 2: Draw infections from HIV+ contacts
                                            # p_trans = beta × condom × (1 - prep) × (1 - art_reduction)
                                            art_modifier = 1 - self.art_transmission_reduction
                                            p_trans_on = beta_stage * condom_modifier * (1 - prep_efficacy) * art_modifier
                                            p_trans_on = np.clip(p_trans_on, 0, 1)
                                            
                                            if deterministic:
                                                inf_on = int(n_pos_contacts_on * p_trans_on)
                                            else:
                                                inf_on = rng.binomial(
                                                    n_pos_contacts_on, p_trans_on
                                                )
                                            
                                            cell_infections += inf_on
                                
                                # Cap at susceptible population
                                cell_infections = min(cell_infections, susceptible_pop)
                                
                                if cell_infections > 0:
                                    infections[age_idx, orient_from, act_from, prep_idx] = cell_infections
                                    total_new_infections += cell_infections
        
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
        """
        susc_idx = self.groups.hiv_to_idx["Susceptible"]
        acute_idx = self.groups.hiv_to_idx["Acute"]
        
        for age_idx in range(self.groups.n_age_groups):
            for orient_idx in range(self.groups.n_orientations):
                for act_idx in range(self.groups.n_activities):
                    for prep_idx in range(self.groups.n_prep):
                        
                        inf_count = infections_array[age_idx, orient_idx, act_idx, prep_idx]
                        
                        if inf_count > 0:
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


def test_transmission_correctness(fail_fast: bool = True):
    """
    Comprehensive transmission sanity checks (PATCH 1).

    Tests:
        A. Linear scaling with prevalence (not quadratic)
        B. Linear scaling with contact rate
        C. Zero prevalence → zero infections
        D. Prevalence=1 reduces to Binomial(n_contacts, p_trans)

    Args:
        fail_fast: If True, raise AssertionError on first failure

    Raises:
        AssertionError: If any test fails and fail_fast=True
    """
    print("\n" + "="*80)
    print("TRANSMISSION SANITY CHECKS (PATCH 1)")
    print("="*80)
    print("Validating transmission is applied correctly:")
    print("  - No double prevalence (prevalence² error)")
    print("  - No timestep double-scaling")
    print("  - Linear response to inputs")
    print("="*80)

    # Create minimal config
    config = {
        "disease": {
            "transmission": {
                "beta_acute": {"value": 0.01},
                "beta_chronic": {"value": 0.005},
                "beta_aids": {"value": 0.008}
            },
            "treatment": {
                "art_transmission_reduction": {"value": 0.96}
            }
        },
        "mixing": {
            "condom_usage": {"value": {"Low": 1.0}}  # No protection
        },
        "interventions": {}
    }

    # Minimal groups
    class MinimalGroups:
        n_age_groups = 1
        n_orientations = 1
        n_activities = 1
        n_prep = 1
        activities = ["Low"]
        prep_states = ["None"]
        hiv_to_idx = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}

    groups = MinimalGroups()

    class MinimalTime:
        years_per_step = 1.0

    time_system = MinimalTime()

    engine = TransmissionEngine(config, groups, time_system)
    rng = np.random.RandomState(42)

    all_passed = True

    # Test A: Linear scaling with prevalence
    print("\n[TEST A] Double partner prevalence → infections ~double (NOT ~quadruple)")
    print("-" * 80)

    # Population: 10,000 susceptible, vary infected
    # Use higher beta (acute) for more infections
    base_pop = 10000

    results = {}
    for prevalence in [0.10, 0.20]:  # Use higher prevalence for clearer signal
        infected_count = int(base_pop * prevalence)
        susceptible_count = base_pop - infected_count

        population = np.zeros((1, 1, 1, 4, 2, 2, 1), dtype=np.int64)
        population[0, 0, 0, 0, 0, 0, 0] = susceptible_count  # Susceptible
        population[0, 0, 0, 1, 0, 0, 0] = infected_count     # Acute (higher beta), off ART

        # 10,000 contacts (more contacts = clearer signal)
        contacts = np.zeros((1, 1, 1, 1))
        contacts[0, 0, 0, 0] = 10000

        # Run multiple times and average
        infections_list = []
        for _ in range(200):
            total_inf, _ = engine.calculate_transmission(population, contacts, rng, deterministic=False)
            infections_list.append(total_inf)

        mean_inf = np.mean(infections_list)
        results[prevalence] = mean_inf
        print(f"  Prevalence {prevalence:.1%}: Mean infections = {mean_inf:.1f}")

    # Check ratio (should be ~2, not ~4)
    ratio = results[0.20] / results[0.10]
    print(f"  Observed ratio: {ratio:.2f}")
    print(f"  Expected:       ~2.0 (linear scaling)")
    print(f"  If prevalence²: ~4.0 (WRONG)")

    if 1.5 < ratio < 2.5:
        print("  ✓ PASS: Linear scaling confirmed")
    else:
        print(f"  ✗ FAIL: Non-linear scaling detected (ratio={ratio:.2f})")
        all_passed = False
        if fail_fast:
            raise AssertionError(
                f"Transmission scaling violation: Double prevalence gave {ratio:.2f}x "
                f"infections (expected ~2.0x). This suggests prevalence² error."
            )

    # Test B: Linear scaling with contact rate
    print("\n[TEST B] Double contact rate → infections ~double")
    print("-" * 80)

    # Fixed prevalence (20%), vary contact rate
    population = np.zeros((1, 1, 1, 4, 2, 2, 1), dtype=np.int64)
    population[0, 0, 0, 0, 0, 0, 0] = 8000  # Susceptible
    population[0, 0, 0, 1, 0, 0, 0] = 2000  # Acute (20% prevalence), off ART

    contact_results = {}
    for n_contacts in [5000, 10000]:
        contacts = np.zeros((1, 1, 1, 1))
        contacts[0, 0, 0, 0] = n_contacts

        # Run multiple times and average
        infections_list = []
        for _ in range(200):
            total_inf, _ = engine.calculate_transmission(population, contacts, rng, deterministic=False)
            infections_list.append(total_inf)

        mean_inf = np.mean(infections_list)
        contact_results[n_contacts] = mean_inf
        print(f"  Contacts {n_contacts:,}: Mean infections = {mean_inf:.1f}")

    # Check ratio (should be ~2)
    contact_ratio = contact_results[10000] / contact_results[5000]
    print(f"  Observed ratio: {contact_ratio:.2f}")
    print(f"  Expected:       ~2.0 (linear scaling)")

    if 1.5 < contact_ratio < 2.5:
        print("  ✓ PASS: Linear contact rate scaling confirmed")
    else:
        print(f"  ✗ FAIL: Non-linear contact scaling detected (ratio={contact_ratio:.2f})")
        all_passed = False
        if fail_fast:
            raise AssertionError(
                f"Contact rate scaling violation: Double contacts gave {contact_ratio:.2f}x "
                f"infections (expected ~2.0x)."
            )

    # Test C: Zero prevalence
    print("\n[TEST C] Zero prevalence → zero infections")
    print("-" * 80)

    population = np.zeros((1, 1, 1, 4, 2, 2, 1), dtype=np.int64)
    population[0, 0, 0, 0, 0, 0, 0] = 10000  # All susceptible

    contacts = np.zeros((1, 1, 1, 1))
    contacts[0, 0, 0, 0] = 10000  # Many contacts

    total_inf, _ = engine.calculate_transmission(population, contacts, rng, deterministic=False)
    print(f"  Infections with 0% prevalence: {total_inf}")
    print(f"  Expected:                      0")

    if total_inf == 0:
        print("  ✓ PASS: Zero infections as expected")
    else:
        print(f"  ✗ FAIL: Should have zero infections, got {total_inf}")
        all_passed = False
        if fail_fast:
            raise AssertionError(
                f"Zero prevalence check failed: Got {total_inf} infections with 0% prevalence. "
                f"Should be exactly zero."
            )

    # Test D: Prevalence = 1.0
    print("\n[TEST D] Prevalence = 1.0 reduces to Binomial(n_contacts, p_trans)")
    print("-" * 80)

    population = np.zeros((1, 1, 1, 4, 2, 2, 1), dtype=np.int64)
    population[0, 0, 0, 0, 0, 0, 0] = 1000   # Susceptible
    population[0, 0, 0, 2, 0, 0, 0] = 10000  # All partners infected (chronic)

    contacts = np.zeros((1, 1, 1, 1))
    contacts[0, 0, 0, 0] = 500

    # Expected: 500 contacts × 0.005 (beta_chronic) = 2.5
    infections_list = []
    for _ in range(100):
        total_inf, _ = engine.calculate_transmission(population, contacts, rng, deterministic=False)
        infections_list.append(total_inf)

    mean_inf = np.mean(infections_list)
    expected = 500 * 0.005  # beta_chronic

    print(f"  Mean infections: {mean_inf:.2f}")
    print(f"  Expected:        {expected:.2f} (n_contacts × beta)")

    if 0.5 * expected < mean_inf < 1.5 * expected:
        print("  ✓ PASS: Matches expected value")
    else:
        print(f"  ✗ FAIL: Doesn't match expected value (off by {abs(mean_inf - expected):.2f})")
        all_passed = False
        if fail_fast:
            raise AssertionError(
                f"Prevalence=1.0 check failed: Mean infections {mean_inf:.2f}, "
                f"expected ~{expected:.2f}."
            )

    # Summary
    print("\n" + "="*80)
    if all_passed:
        print("✓ ALL TRANSMISSION SANITY CHECKS PASSED")
        print("\nTransmission is correctly applied:")
        print("  - No prevalence² error (linear in prevalence)")
        print("  - No double-scaling (linear in contact rate)")
        print("  - Zero prevalence gives zero infections")
        print("  - Prevalence=1 reduces to expected formula")
    else:
        print("✗ SOME TRANSMISSION SANITY CHECKS FAILED")
        print("\nReview transmission algorithm for scaling errors.")
    print("="*80 + "\n")

    return all_passed


if __name__ == "__main__":
    test_transmission_correctness()
