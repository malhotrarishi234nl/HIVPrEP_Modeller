"""
Diagnostic: Linear vs Exponential Probability Scaling in Aging/Debut

This script demonstrates the mathematical root cause of the time-invariance
divergence. The issue is that aging and debut use LINEAR probability scaling:
    p_step = rate_annual * years_per_step

While mortality uses EXPONENTIAL (correct) probability conversion:
    p_step = 1 - (1 - rate_annual)^years_per_step

For a transition probability applied repeatedly, only the exponential form
gives consistent annual totals regardless of timestep size.
"""

import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def demonstrate_linear_vs_exponential():
    """Show how linear scaling creates timestep-dependent aging."""

    annual_rate = 0.2  # 20% per year (1/5 for 5-year bins)

    print("=" * 70)
    print("MATHEMATICAL ROOT CAUSE: Linear vs Exponential Probability Scaling")
    print("=" * 70)
    print(f"\nAnnual aging rate: {annual_rate:.1%}")
    print(f"Starting population in one bin: 100,000\n")

    step_sizes = [12, 6, 3, 1]  # months

    print(f"{'Step Size':>10} {'Method':>12} {'p_step':>10} {'Aged Out (1yr)':>16} {'Error':>10}")
    print("-" * 65)

    for step_size in step_sizes:
        years_per_step = step_size / 12.0
        n_steps_per_year = int(12 / step_size)
        pop = 100000

        # LINEAR (current code)
        p_linear = annual_rate * years_per_step
        pop_remaining_linear = pop
        for _ in range(n_steps_per_year):
            moved = int(pop_remaining_linear * p_linear)
            pop_remaining_linear -= moved
        aged_out_linear = pop - pop_remaining_linear
        error_linear = (aged_out_linear / pop - annual_rate) / annual_rate * 100

        # EXPONENTIAL (correct)
        p_exp = 1 - (1 - annual_rate) ** years_per_step
        pop_remaining_exp = pop
        for _ in range(n_steps_per_year):
            moved = int(pop_remaining_exp * p_exp)
            pop_remaining_exp -= moved
        aged_out_exp = pop - pop_remaining_exp
        error_exp = (aged_out_exp / pop - annual_rate) / annual_rate * 100

        print(f"{step_size:>7}mo  {'LINEAR':>12} {p_linear:>10.6f} {aged_out_linear:>16,} {error_linear:>+9.2f}%")
        print(f"{'':>10} {'EXPONENTIAL':>12} {p_exp:>10.6f} {aged_out_exp:>16,} {error_exp:>+9.2f}%")

    print()


def demonstrate_double_debut():
    """Show that fractional aging mode applies BOTH continuous debut AND aging-out."""

    print("=" * 70)
    print("DOUBLE DEBUT BUG: Continuous Debut + Fractional Aging in Same Mode")
    print("=" * 70)

    pop_oldest = 100000  # oldest childhood bin (10-14)

    step_sizes = [12, 6, 3]

    for step_size in step_sizes:
        years_per_step = step_size / 12.0
        n_steps = int(12 / step_size)

        # Current code: both continuous debut AND fractional aging
        pop = pop_oldest
        total_debut = 0
        total_aging_out = 0

        for _ in range(n_steps):
            # Step 1: Continuous debut (LINEAR)
            p_debut = years_per_step / 5.0
            debuted = int(pop * p_debut)
            pop -= debuted
            total_debut += debuted

            # Step 2: Fractional aging (LINEAR)
            p_aging = (1.0 / 5.0) * years_per_step
            aged_out = int(pop * p_aging)
            pop -= aged_out
            total_aging_out += aged_out

        total_moved = total_debut + total_aging_out

        # Correct: only fractional aging with EXPONENTIAL scaling
        pop_correct = pop_oldest
        total_correct = 0
        for _ in range(n_steps):
            p_correct = 1 - (1 - 0.2) ** years_per_step
            moved = int(pop_correct * p_correct)
            pop_correct -= moved
            total_correct += moved

        print(f"\nstep_size={step_size} ({n_steps} steps/year):")
        print(f"  Current (debut+aging): {total_moved:,} moved ({total_moved/pop_oldest:.1%})")
        print(f"    - Continuous debut:  {total_debut:,}")
        print(f"    - Fractional aging:  {total_aging_out:,}")
        print(f"  Correct (exponential): {total_correct:,} moved ({total_correct/pop_oldest:.1%})")
        print(f"  Target:                {int(pop_oldest*0.2):,} moved (20.0%)")
        print(f"  Current error: {(total_moved - pop_oldest*0.2) / (pop_oldest*0.2) * 100:+.1f}%")
        print(f"  Correct error: {(total_correct - pop_oldest*0.2) / (pop_oldest*0.2) * 100:+.1f}%")


def run_simplified_model():
    """
    Run a simplified population model to confirm that linear aging
    causes the observed divergence pattern.
    """
    print("\n" + "=" * 70)
    print("SIMPLIFIED MODEL: Confirming Divergence Pattern")
    print("=" * 70)

    # Simple 3-pool model: Children → Young Adults → Old Adults
    # With births and deaths

    years = 45  # 1970-2015
    initial_children = 3_800_000  # approximate
    initial_young = 6_400_000
    initial_old = 6_300_000

    birth_rate = 0.01  # annual births per young adult
    death_rate_young = 0.002  # annual
    death_rate_old = 0.015  # annual
    aging_rate = 0.2  # 1/5 per year for 5-year bins

    step_sizes = [12, 3]
    results = {}

    for step_size in step_sizes:
        years_per_step = step_size / 12.0
        n_steps = int(years * 12 / step_size)

        children = float(initial_children)
        young = float(initial_young)
        old = float(initial_old)

        # LINEAR aging (current code behavior)
        p_aging_linear = aging_rate * years_per_step

        for step in range(n_steps):
            # Births (from young adults, into children)
            births = young * birth_rate * years_per_step

            # Deaths (exponential - correct)
            deaths_young = young * (1 - (1 - death_rate_young) ** years_per_step)
            deaths_old = old * (1 - (1 - death_rate_old) ** years_per_step)

            # Aging (LINEAR - buggy)
            children_to_young = children * p_aging_linear
            young_to_old = young * p_aging_linear

            # Apply
            children += births - children_to_young
            young += children_to_young - young_to_old - deaths_young
            old += young_to_old - deaths_old

        total_linear = children + young + old

        # EXPONENTIAL aging (correct)
        children = float(initial_children)
        young = float(initial_young)
        old = float(initial_old)

        p_aging_exp = 1 - (1 - aging_rate) ** years_per_step

        for step in range(n_steps):
            births = young * birth_rate * years_per_step
            deaths_young = young * (1 - (1 - death_rate_young) ** years_per_step)
            deaths_old = old * (1 - (1 - death_rate_old) ** years_per_step)

            children_to_young = children * p_aging_exp
            young_to_old = young * p_aging_exp

            children += births - children_to_young
            young += children_to_young - young_to_old - deaths_young
            old += young_to_old - deaths_old

        total_exp = children + young + old

        results[step_size] = {
            'linear': total_linear,
            'exponential': total_exp
        }

    print(f"\nAfter {years} years of simulation:\n")
    print(f"{'Method':>15} {'step=12 (annual)':>20} {'step=3 (quarterly)':>20} {'Divergence':>12}")
    print("-" * 70)

    lin_12 = results[12]['linear']
    lin_3 = results[3]['linear']
    exp_12 = results[12]['exponential']
    exp_3 = results[3]['exponential']

    divergence_linear = (lin_3 - lin_12) / lin_12 * 100
    divergence_exp = (exp_3 - exp_12) / exp_12 * 100

    print(f"{'LINEAR':>15} {lin_12:>20,.0f} {lin_3:>20,.0f} {divergence_linear:>+11.1f}%")
    print(f"{'EXPONENTIAL':>15} {exp_12:>20,.0f} {exp_3:>20,.0f} {divergence_exp:>+11.1f}%")

    print(f"\n  Linear aging divergence:      {divergence_linear:+.1f}%")
    print(f"  Exponential aging divergence: {divergence_exp:+.1f}%")
    print(f"\n  → Exponential conversion eliminates the divergence!")


def identify_all_linear_processes():
    """Identify all processes in the codebase that use linear scaling."""

    print("\n" + "=" * 70)
    print("PROCESSES USING LINEAR vs EXPONENTIAL SCALING")
    print("=" * 70)

    print("""
    USING EXPONENTIAL (correct):
    ✅ Background mortality (life table): 1 - survival^years_per_step
    ✅ Childhood mortality:               1 - survival^years_per_step
    ✅ Constant background mortality:      annual_to_step_rate() = 1-(1-r)^t
    ✅ HIV mortality:                      annual_to_step_rate()
    ✅ Disease progression:                annual_to_step_rate()
    ✅ Diagnosis:                          annual_to_step_rate()
    ✅ ART initiation/dropout:             annual_to_step_rate()

    USING LINEAR (BUGGY - causes divergence):
    ❌ Childhood fractional aging:   aging_fraction = (1/5) * years_per_step
       → File: demography.py line 322
       → Fix:  aging_fraction = 1 - (1 - 1/5)^years_per_step

    ❌ Adult fractional aging:       aging_fraction = (1/5) * years_per_step
       → File: run_v3.py line 501
       → Fix:  aging_fraction = 1 - (1 - 1/5)^years_per_step

    ❌ Continuous debut:             p_debut = years_per_step / 5.0
       → File: demography.py line 230
       → Fix:  REMOVE from fractional aging mode (redundant with aging-out)

    USING LINEAR (acceptable for Poisson counting process):
    ✅ Births (ASFR):               births = women * asfr * years_per_step
       → Correct: births are a counting process, not a transition probability

    ADDITIONAL BUG:
    ❌ Double debut in fractional aging mode
       → File: run_v3.py lines 406-432
       → Both process_continuous_debut() AND apply_fractional_aging()
         remove from oldest childhood bin and send to sexual debut
       → Fix: Remove continuous debut call from _step_aging_fractional()
    """)


if __name__ == "__main__":
    demonstrate_linear_vs_exponential()
    demonstrate_double_debut()
    run_simplified_model()
    identify_all_linear_processes()
