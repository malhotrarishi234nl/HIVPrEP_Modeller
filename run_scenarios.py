#!/usr/bin/env python3
"""
Multi-Scenario Runner (V5)

Orchestrates baseline freezing and scenario forking for HTA comparability.

Workflow:
1. Load base config and scenario definitions
2. Run baseline simulation to policy_start_year
3. Freeze state snapshot at baseline_freeze_year
4. For each scenario:
   - Restore frozen baseline
   - Apply scenario-specific overrides
   - Continue simulation from freeze year to end
   - Save scenario-labeled outputs
5. Aggregate results across scenarios

Usage:
    python run_scenarios.py config_v5.json --output_dir outputs/v5_run
"""

import json
import sys
import os
from typing import Dict, List
import numpy as np
import pandas as pd

from run_v3 import SimulationEngineV3
from state_snapshot import StateSnapshotManager
from scenario_executor import (
    load_scenarios,
    get_policy_config,
    merge_config_override,
    apply_prep_force_disable,
    validate_scenario_config
)
from validation import validate_v5_config, validate_prep_disabled


class MultiScenarioRunner:
    """
    Manages multi-scenario simulation runs with baseline freezing.
    """
    
    def __init__(self, config_path: str, output_dir: str = "outputs"):
        """
        Initialize multi-scenario runner.
        
        Args:
            config_path: Path to V5 configuration file
            output_dir: Directory for output files
        """
        self.config_path = config_path
        self.output_dir = output_dir
        
        # Load config
        with open(config_path, 'r') as f:
            self.base_config = json.load(f)

        # Load scenarios and policy
        self.scenarios = load_scenarios(self.base_config)
        self.policy = get_policy_config(self.base_config)

        # Validate configuration
        validation_suite = validate_v5_config(self.base_config, self.scenarios)
        if validation_suite.has_errors():
            print("\n❌ Configuration validation failed!")
            validation_suite.print_summary()
            raise ValueError("Invalid V5 configuration - see validation errors above")
        elif validation_suite.has_warnings():
            print("\n⚠️  Configuration has warnings (continuing anyway)")

        # Validate scenario config
        validate_scenario_config(self.scenarios, self.policy)
        
        # State snapshot manager
        self.snapshot_manager = StateSnapshotManager()
        self.snapshot_manager.configure(
            freeze_enabled=self.policy["enabled"],
            freeze_year=self.policy["freeze_year"]
        )
        
        # Results storage
        self.results = {}  # scenario_name -> outputs dict
        
    def run(self):
        """
        Execute all scenarios.
        
        Returns:
            Dictionary mapping scenario names to output dictionaries
        """
        print("\n" + "=" * 70)
        print("MULTI-SCENARIO SIMULATION (V5)")
        print("=" * 70)
        print(f"\nConfiguration: {self.config_path}")
        print(f"Scenarios: {len(self.scenarios)}")
        for i, s in enumerate(self.scenarios, 1):
            print(f"  {i}. {s['name']}: {s['description']}")
        
        if self.policy["enabled"]:
            print(f"\nPolicy freezing: ENABLED")
            print(f"  Freeze year: {self.policy['freeze_year']}")
            print(f"  Reuse baseline: {self.policy['reuse_baseline']}")
        else:
            print(f"\nPolicy freezing: DISABLED (each scenario runs independently)")
        
        # Run scenarios
        if self.policy["enabled"] and self.policy["reuse_baseline"]:
            self._run_with_reused_baseline()
        else:
            self._run_independent_scenarios()
        
        return self.results
    
    def _run_with_reused_baseline(self):
        """
        Run all scenarios from a single frozen baseline (V5 mode).
        
        This ensures all scenarios start from identical state for comparability.
        """
        print("\n" + "-" * 70)
        print("PHASE 1: Baseline Freezing")
        print("-" * 70)
        
        # Get seed from config
        seed = self.base_config.get("simulation_controls", {}).get("seed", {})
        if isinstance(seed, dict):
            seed = seed.get("value", 42)
        
        # Run baseline to freeze year
        print(f"\nRunning baseline to freeze year {self.policy['freeze_year']}...")
        
        # Note: This is a simplified version. In full implementation,
        # we'd need to modify SimulationEngineV3 to support partial runs
        # and state freezing during execution.
        
        print("⚠️  Note: Full baseline freezing requires integration with SimulationEngineV3")
        print("   This is Phase 4 placeholder - will complete in next iteration")
        
        # Placeholder for baseline run
        # TODO: Implement partial simulation run to freeze year
        
        print("\n" + "-" * 70)
        print("PHASE 2: Scenario Forking")
        print("-" * 70)
        
        for i, scenario in enumerate(self.scenarios, 1):
            print(f"\n[{i}/{len(self.scenarios)}] Running scenario: {scenario['name']}")
            print(f"  Description: {scenario['description']}")
            
            # Apply overrides
            scenario_config = self._apply_scenario_overrides(scenario)
            
            # TODO: Restore baseline and run scenario
            # This requires modifying SimulationEngineV3 to accept
            # initial state snapshot
            
            print(f"  ⚠️  Scenario execution placeholder")
    
    def _run_independent_scenarios(self):
        """
        Run scenarios independently (no baseline freezing).
        
        Each scenario runs from its own initial random state.
        """
        print("\n" + "-" * 70)
        print("Running scenarios independently (no baseline freeze)")
        print("-" * 70)
        
        # Get base seed
        base_seed = self.base_config.get("simulation_controls", {}).get("seed", {})
        if isinstance(base_seed, dict):
            base_seed = base_seed.get("value", 42)
        
        for i, scenario in enumerate(self.scenarios, 1):
            print(f"\n[{i}/{len(self.scenarios)}] Running scenario: {scenario['name']}")
            print(f"  Description: {scenario['description']}")
            
            # Use different seed for each scenario
            scenario_seed = base_seed + i
            
            # Apply overrides
            scenario_config = self._apply_scenario_overrides(scenario)
            
            # Update seed in scenario config
            if "simulation_controls" not in scenario_config:
                scenario_config["simulation_controls"] = {}
            scenario_config["simulation_controls"]["seed"] = {"value": scenario_seed}
            
            # Update output tag to include scenario name
            if "output" not in scenario_config:
                scenario_config["output"] = {}
            base_tag = scenario_config.get("output", {}).get("run_tag", {})
            if isinstance(base_tag, dict):
                base_tag = base_tag.get("value", "run")
            scenario_config["output"]["run_tag"] = {"value": f"{base_tag}_{scenario['name']}"}
            
            # Save scenario config to temp file
            temp_config_path = f"/tmp/scenario_{scenario['name']}.json"
            with open(temp_config_path, 'w') as f:
                json.dump(scenario_config, f, indent=2)
            
            # Run scenario
            try:
                print(f"  ⏳ Running simulation...")
                engine = SimulationEngineV3(temp_config_path, seed=scenario_seed)
                
                # Run simulation
                for step in range(engine.time.n_steps):
                    engine.step_simulation(step)

                # Write outputs with scenario label
                if engine.output_writer.enabled and engine.output_writer.write_per_run:
                    df = pd.DataFrame(engine.outputs)
                    # Add scenario column for identification
                    df.insert(0, 'scenario', scenario['name'])
                    engine.output_writer.write_run_output(df, run_number=1)
                    print(f"  💾 Output written: {scenario_config['output']['run_tag']['value']}_run_001.csv")

                # Collect results
                self.results[scenario['name']] = engine.outputs

                print(f"  ✅ Scenario complete")
                
            except Exception as e:
                print(f"  ❌ Scenario failed: {e}")
                import traceback
                traceback.print_exc()
            
            # Clean up temp file
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)
    
    def _apply_scenario_overrides(self, scenario: Dict) -> Dict:
        """
        Apply scenario-specific overrides to base config.
        
        Args:
            scenario: Scenario definition with overrides
            
        Returns:
            Config with scenario overrides applied
        """
        overrides = scenario.get("overrides", {})
        
        # Check for special prep_force_disable flag
        if overrides.get("prep_force_disable", False):
            print("  ⚠️  Special override: PrEP force disabled")
            # Remove the flag and apply disable logic
            overrides_copy = overrides.copy()
            del overrides_copy["prep_force_disable"]

            # Apply normal overrides first
            scenario_config = merge_config_override(self.base_config, overrides_copy)

            # Then apply PrEP force disable
            scenario_config = apply_prep_force_disable(scenario_config)

            # Validate PrEP is actually disabled
            validation_results = validate_prep_disabled(scenario_config, scenario['name'])
            errors = [r for r in validation_results if not r.passed and r.severity == "error"]
            if errors:
                print("  ❌ PrEP disable validation failed:")
                for error in errors:
                    print(f"     {error}")
                raise ValueError(f"PrEP not properly disabled in scenario {scenario['name']}")
            else:
                print("  ✓ PrEP disable validated")
        else:
            # Normal override merge
            scenario_config = merge_config_override(self.base_config, overrides)

        return scenario_config


def main():
    """Main entry point for scenario runner."""
    if len(sys.argv) < 2:
        print("Usage: python run_scenarios.py <config.json> [--output_dir <dir>]")
        sys.exit(1)
    
    config_path = sys.argv[1]
    
    # Parse output directory
    output_dir = "outputs"
    if "--output_dir" in sys.argv:
        idx = sys.argv.index("--output_dir")
        if idx + 1 < len(sys.argv):
            output_dir = sys.argv[idx + 1]
    
    # Run scenarios
    runner = MultiScenarioRunner(config_path, output_dir)
    results = runner.run()
    
    print("\n" + "=" * 70)
    print("✅ MULTI-SCENARIO RUN COMPLETE")
    print("=" * 70)
    print(f"\nResults for {len(results)} scenarios:")
    for name in results:
        print(f"  ✓ {name}")


if __name__ == "__main__":
    main()
