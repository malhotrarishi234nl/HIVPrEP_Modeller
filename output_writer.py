"""
Output module for HIV Simulation V3

Handles file writing, terminal summaries, and aggregation across runs.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional
from config_utils import extract_value


class OutputWriter:
    """
    Manages all output writing for simulation runs.
    
    Supports:
    - Per-run CSV/Excel files
    - Aggregated multi-run statistics
    - Terminal summaries
    - State count exports (optional)
    """
    
    def __init__(self, output_config: Dict, time_system):
        self.config = output_config
        self.time = time_system
        
        self.enabled = extract_value(output_config.get("enabled", {"value": True}))
        self.format = extract_value(output_config.get("format", {"value": "csv"}))
        self.directory = Path(extract_value(output_config.get("directory", {"value": "outputs"})))
        self.run_tag = extract_value(output_config.get("run_tag", {"value": "baseline"}))
        
        self.write_per_run = extract_value(output_config.get("write_per_run_files", {"value": True}))
        self.write_aggregated = extract_value(output_config.get("write_aggregated_files", {"value": True}))
        self.write_state_counts_enabled = extract_value(output_config.get("write_state_counts", {"value": False}))
        self.terminal_summary = extract_value(output_config.get("terminal_summary", {"value": True}))
        
        self.summary_years = extract_value(output_config.get("summary_years", {"value": []}))
        self.quantiles = extract_value(output_config.get("quantiles", {"value": [0.05, 0.5, 0.95]}))
        
        # Create output directory
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)
    
    def write_run_output(self, df: pd.DataFrame, run_number: int):
        """Write output for a single run."""
        if not self.enabled or not self.write_per_run:
            return
        
        filename = f"{self.run_tag}_run_{run_number:03d}"
        
        if self.format == "xlsx":
            filepath = self.directory / f"{filename}.xlsx"
            df.to_excel(filepath, index=False)
        else:  # csv
            filepath = self.directory / f"{filename}.csv"
            df.to_csv(filepath, index=False)
        
        return filepath
    
    def write_aggregated_outputs(self, all_results: List[pd.DataFrame]):
        """Write aggregated statistics across multiple runs."""
        if not self.enabled or not self.write_aggregated or len(all_results) <= 1:
            return
        
        # Combine all results
        combined = pd.concat(all_results, ignore_index=True)
        
        # Group by year and calculate statistics
        metrics = [
            'new_infections', 'prevalence', 'incidence',
            'hiv_deaths', 'background_deaths', 'total_population',
            'migrant_hiv_entries', 'migrant_hiv_on_art_entries',
            'migrant_hiv_msm_entries', 'migrant_hiv_hetero_male_entries',
            'migrant_hiv_female_entries', 'migrant_hiv_requested_entries',
            'migrant_hiv_capped_entries', 'migrant_hiv_cap_shortfall'
        ]
        
        # Calculate quantiles and mean
        agg_funcs = ['mean'] + [lambda x, q=q: x.quantile(q) for q in self.quantiles]
        agg_names = ['mean'] + [f'q{int(q*100):02d}' for q in self.quantiles]
        
        summary_data = []
        for year in combined['year'].unique():
            year_data = combined[combined['year'] == year]
            row = {'year': year}
            
            for metric in metrics:
                if metric in year_data.columns:
                    row[f'{metric}_mean'] = year_data[metric].mean()
                    for q, qname in zip(self.quantiles, [f'q{int(q*100):02d}' for q in self.quantiles]):
                        row[f'{metric}_{qname}'] = year_data[metric].quantile(q)
            
            summary_data.append(row)
        
        aggregated_df = pd.DataFrame(summary_data).sort_values('year')
        
        # Write aggregated file
        filename = f"{self.run_tag}_aggregated"
        if self.format == "xlsx":
            filepath = self.directory / f"{filename}.xlsx"
            aggregated_df.to_excel(filepath, index=False)
        else:
            filepath = self.directory / f"{filename}.csv"
            aggregated_df.to_csv(filepath, index=False)
        
        return filepath
    
    def print_terminal_summary(self, df: pd.DataFrame, run_number: Optional[int] = None, 
                               prep_coverage: Dict[str, List[float]] = None):
        """Print summary to terminal."""
        if not self.terminal_summary:
            return
        
        print("\n" + "="*80)
        if run_number is not None:
            print(f"SIMULATION SUMMARY - Run {run_number}")
        else:
            print("SIMULATION SUMMARY")
        print("="*80)
        
        # Filter to summary years if specified
        if self.summary_years:
            summary_df = df[df['year'].isin(self.summary_years)]
        else:
            # Show first, middle, and last
            indices = [0, len(df)//2, -1]
            summary_df = df.iloc[indices]
        
        print(f"\n{'Year':<8} {'Population':>12} {'HIV Prev %':>10} {'New Inf':>10} {'HIV Deaths':>12} {'BG Deaths':>12}")
        print("-"*80)
        
        for _, row in summary_df.iterrows():
            print(f"{row['year']:<8.0f} {row['total_population']:>12,.0f} "
                  f"{row['prevalence']*100:>10.2f} {row['new_infections']:>10,.0f} "
                  f"{row['hiv_deaths']:>12,.0f} {row['background_deaths']:>12,.0f}")
        
        # Summary statistics
        print("\n" + "-"*80)
        print(f"Total new infections: {df['new_infections'].sum():,.0f}")
        print(f"Total HIV deaths: {df['hiv_deaths'].sum():,.0f}")
        print(f"Total background deaths: {df['background_deaths'].sum():,.0f}")
        if 'migrant_hiv_entries' in df.columns:
            print(f"Total migrant HIV entries: {df['migrant_hiv_entries'].sum():,.0f}")
        print(f"Final prevalence: {df['prevalence'].iloc[-1]*100:.2f}%")
        print(f"Peak prevalence: {df['prevalence'].max()*100:.2f}%")
        
        # PrEP coverage if provided
        if prep_coverage:
            print("\nPrEP Coverage (final year):")
            for intervention, coverage_list in prep_coverage.items():
                if coverage_list:
                    print(f"  {intervention}: {coverage_list[-1]*100:.2f}%")
        
        print("="*80 + "\n")
    
    def print_multi_run_summary(self, all_results: List[pd.DataFrame]):
        """Print summary across multiple runs."""
        if not self.terminal_summary or len(all_results) <= 1:
            return
        
        print("\n" + "="*80)
        print(f"MULTI-RUN SUMMARY ({len(all_results)} runs)")
        print("="*80)
        
        # Combine results
        combined = pd.concat(all_results, ignore_index=True)
        
        # Calculate statistics for final year
        final_year = combined['year'].max()
        final_data = combined[combined['year'] == final_year]
        
        metrics = {
            'Total Population': 'total_population',
            'HIV Prevalence (%)': 'prevalence',
            'New Infections (final year)': 'new_infections'
        }
        
        print(f"\nFinal Year ({final_year:.0f}) Statistics:")
        print(f"{'Metric':<30} {'Mean':>12} {'Q05':>12} {'Q50':>12} {'Q95':>12}")
        print("-"*80)
        
        for label, col in metrics.items():
            if col in final_data.columns:
                mean_val = final_data[col].mean()
                q05 = final_data[col].quantile(0.05)
                q50 = final_data[col].quantile(0.50)
                q95 = final_data[col].quantile(0.95)
                
                if col == 'prevalence':
                    print(f"{label:<30} {mean_val*100:>12.2f} {q05*100:>12.2f} {q50*100:>12.2f} {q95*100:>12.2f}")
                else:
                    print(f"{label:<30} {mean_val:>12,.0f} {q05:>12,.0f} {q50:>12,.0f} {q95:>12,.0f}")
        
        # Total infections across entire simulation
        total_infections_per_run = combined.groupby('step')['new_infections'].sum()
        print(f"\n{'Total Infections (all years)':<30} {total_infections_per_run.mean():>12,.0f} "
              f"{total_infections_per_run.quantile(0.05):>12,.0f} "
              f"{total_infections_per_run.quantile(0.50):>12,.0f} "
              f"{total_infections_per_run.quantile(0.95):>12,.0f}")

        print("="*80 + "\n")

    def write_state_counts(self, population: np.ndarray, group_system, step: int,
                           year: float, run_number: int, prep_status: Optional[np.ndarray] = None,
                           art_status: Optional[np.ndarray] = None):
        """
        Write detailed population state counts to file.

        Creates a comprehensive breakdown of population by:
        - Age group
        - Sexual orientation
        - Activity level
        - HIV stage
        - PrEP status (three separate columns at same grain)
        - ART status (two separate columns at same grain) [PATCH 3]

        Args:
            population: Population tensor (age, orient, activity, hiv)
            group_system: GroupSystem with dimension labels
            step: Current timestep
            year: Current year
            run_number: Run number for filename
            prep_status: Optional PrEP status array (age, orient, activity, hiv, prep_type)
            art_status: Optional ART status array (age, orient, activity, hiv, art_status) [PATCH 3]
        """
        if not self.enabled or not self.write_state_counts_enabled:
            return

        # Build detailed state records
        records = []

        # Iterate through all dimensions
        for age_idx in range(population.shape[0]):
            for orient_idx in range(population.shape[1]):
                for activity_idx in range(population.shape[2]):
                    for hiv_idx in range(population.shape[3]):
                        count = population[age_idx, orient_idx, activity_idx, hiv_idx]

                        if count > 0:  # Only record non-zero populations
                            record = {
                                'step': step,
                                'year': year,
                                'age_group': group_system.age_groups[age_idx],
                                'orientation': group_system.orientations[orient_idx],
                                'activity': group_system.activities[activity_idx],
                                'hiv_stage': group_system.hiv_stages[hiv_idx],
                                'population': int(count)
                            }

                            # Add PrEP columns at same grain (age, orient, activity, hiv)
                            # Four explicit columns: pop_prep_none, pop_prep_oral, pop_prep_lenacapavir, pop_prep_lena_waning
                            # V12.2: pop_prep_lena_waning added for Lenacapavir_Waning state; zero when lena disabled.
                            if prep_status is not None:
                                # Extract PrEP counts for this exact state slice
                                prep_slice = prep_status[age_idx, orient_idx, activity_idx, hiv_idx, :]

                                # Map prep indices to column names
                                # prep_states[0] should be "None", then "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"
                                # V12.2: None is always index 0 by design.
                                record['pop_prep_none'] = int(prep_slice[0]) if len(prep_slice) > 0 else 0

                                # Find Oral_PrEP index
                                oral_idx = group_system.prep_to_idx.get('Oral_PrEP', -1)
                                record['pop_prep_oral'] = int(prep_slice[oral_idx]) if oral_idx >= 0 and oral_idx < len(prep_slice) else 0

                                # Find Lenacapavir index
                                len_idx = group_system.prep_to_idx.get('Lenacapavir', -1)
                                record['pop_prep_lenacapavir'] = int(prep_slice[len_idx]) if len_idx >= 0 and len_idx < len(prep_slice) else 0

                                # Find Lenacapavir_Waning index (V12.2 — zero when lena disabled or state not registered)
                                waning_idx = group_system.prep_to_idx.get('Lenacapavir_Waning', -1)
                                record['pop_prep_lena_waning'] = int(prep_slice[waning_idx]) if waning_idx >= 0 and waning_idx < len(prep_slice) else 0

                            # PATCH 3: Add ART columns at same grain (age, orient, activity, hiv)
                            # Two explicit columns: pop_art_off, pop_art_on
                            if art_status is not None:
                                # Extract ART counts for this exact state slice
                                art_slice = art_status[age_idx, orient_idx, activity_idx, hiv_idx, :]

                                # ART dimension: 0 = off ART, 1 = on ART
                                record['pop_art_off'] = int(art_slice[0]) if len(art_slice) > 0 else 0
                                record['pop_art_on'] = int(art_slice[1]) if len(art_slice) > 1 else 0

                            records.append(record)

        # Convert to DataFrame
        df = pd.DataFrame(records)

        # MASS-BALANCE ASSERTION: Verify PrEP columns sum to population
        if prep_status is not None and len(df) > 0:
            # Calculate sum of PrEP columns
            df['_prep_sum'] = df['pop_prep_none'] + df['pop_prep_oral'] + df['pop_prep_lenacapavir'] + df['pop_prep_lena_waning']
            df['_diff'] = df['_prep_sum'] - df['population']

            # Check for violations (allow small floating point errors)
            violations = df[df['_diff'].abs() > 0.5]  # tolerance of 0.5 for rounding

            if len(violations) > 0:
                # Format error message with details
                error_msg = (
                    f"\n{'='*70}\n"
                    f"MASS-BALANCE ASSERTION FAILED (Step {step}, Year {year:.1f})\n"
                    f"{'='*70}\n"
                    f"PrEP populations do NOT sum to total population!\n\n"
                    f"Requirement: population = pop_prep_none + pop_prep_oral + pop_prep_lenacapavir + pop_prep_lena_waning\n\n"
                    f"Found {len(violations)} violation(s):\n"
                )

                # Show first 5 violations
                for idx, row in violations.head(5).iterrows():
                    error_msg += (
                        f"\n  State: {row['age_group']}, {row['orientation']}, "
                        f"{row['activity']}, {row['hiv_stage']}\n"
                        f"    population:         {row['population']:.0f}\n"
                        f"    pop_prep_none:      {row['pop_prep_none']:.0f}\n"
                        f"    pop_prep_oral:      {row['pop_prep_oral']:.0f}\n"
                        f"    pop_prep_lenacapavir: {row['pop_prep_lenacapavir']:.0f}\n"
                        f"    pop_prep_lena_waning: {row['pop_prep_lena_waning']:.0f}\n"
                        f"    Sum:                {row['_prep_sum']:.0f}\n"
                        f"    Difference:         {row['_diff']:.0f}\n"
                    )

                if len(violations) > 5:
                    error_msg += f"\n  ... and {len(violations) - 5} more violations\n"

                error_msg += f"\n{'='*70}\n"
                error_msg += "This indicates a model accounting error. DO NOT PROCEED.\n"
                error_msg += "Check PrEP transition logic and population tensor operations.\n"
                error_msg += f"{'='*70}\n"

                # Clean up temp columns and raise error
                df.drop(['_prep_sum', '_diff'], axis=1, inplace=True)
                raise AssertionError(error_msg)

            # Clean up temporary columns if validation passed
            df.drop(['_prep_sum', '_diff'], axis=1, inplace=True)

        # PATCH 3: MASS-BALANCE ASSERTION for ART: Verify ART columns sum to population
        if art_status is not None and len(df) > 0:
            # Calculate sum of ART columns
            df['_art_sum'] = df['pop_art_off'] + df['pop_art_on']
            df['_diff'] = df['_art_sum'] - df['population']

            # Check for violations (allow small floating point errors)
            violations = df[df['_diff'].abs() > 0.5]  # tolerance of 0.5 for rounding

            if len(violations) > 0:
                # Format error message with details
                error_msg = (
                    f"\n{'='*70}\n"
                    f"ART MASS-BALANCE ASSERTION FAILED (Step {step}, Year {year:.1f})\n"
                    f"{'='*70}\n"
                    f"ART populations do NOT sum to total population!\n\n"
                    f"Requirement: population = pop_art_off + pop_art_on\n\n"
                    f"Found {len(violations)} violation(s):\n"
                )

                # Show first 5 violations
                for idx, row in violations.head(5).iterrows():
                    error_msg += (
                        f"\n  State: {row['age_group']}, {row['orientation']}, "
                        f"{row['activity']}, {row['hiv_stage']}\n"
                        f"    population:     {row['population']:.0f}\n"
                        f"    pop_art_off:    {row['pop_art_off']:.0f}\n"
                        f"    pop_art_on:     {row['pop_art_on']:.0f}\n"
                        f"    Sum:            {row['_art_sum']:.0f}\n"
                        f"    Difference:     {row['_diff']:.0f}\n"
                    )

                if len(violations) > 5:
                    error_msg += f"\n  ... and {len(violations) - 5} more violations\n"

                error_msg += f"\n{'='*70}\n"
                error_msg += "This indicates a model accounting error. DO NOT PROCEED.\n"
                error_msg += "Check ART transition logic and population tensor operations.\n"
                error_msg += f"{'='*70}\n"

                # Clean up temp columns and raise error
                df.drop(['_art_sum', '_diff'], axis=1, inplace=True)
                raise AssertionError(error_msg)

            # Clean up temporary columns if validation passed
            df.drop(['_art_sum', '_diff'], axis=1, inplace=True)

        # Write to file
        filename = f"{self.run_tag}_state_counts_run_{run_number:03d}"

        if self.format == "xlsx":
            filepath = self.directory / f"{filename}.xlsx"
            df.to_excel(filepath, index=False)
        else:
            filepath = self.directory / f"{filename}.csv"
            df.to_csv(filepath, index=False)

        return filepath
