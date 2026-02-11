"""
Scenario Execution Framework (V5 Phase 4)

Manages multi-scenario runs with baseline freezing and forking:
1. Run baseline to policy_start_year
2. Freeze state snapshot
3. For each scenario: restore baseline and continue with scenario overrides
4. Collect scenario-labeled outputs

This ensures all scenarios start from identical baseline for HTA comparability.
"""

from typing import Dict, List, Optional, Any
import copy


def merge_config_override(base_config: Dict, override: Dict) -> Dict:
    """
    Deep merge scenario overrides into base config.
    
    Args:
        base_config: Base configuration dictionary
        override: Scenario-specific overrides
        
    Returns:
        Merged config with overrides applied
        
    Example:
        base = {"prep_schedules": {"Oral_PrEP": {"initiation": {"2020": 0.1}}}}
        override = {"prep_schedules": {"Oral_PrEP": {"initiation": {"2025": 0.3}}}}
        result = {"prep_schedules": {"Oral_PrEP": {"initiation": {"2020": 0.1, "2025": 0.3}}}}
    """
    # Start with deep copy of base
    merged = copy.deepcopy(base_config)
    
    # Recursively merge overrides
    _deep_merge(merged, override)
    
    return merged


def _deep_merge(target: Dict, source: Dict):
    """
    Recursively merge source into target (in-place).
    
    For nested dicts: recurse and merge
    For values: source overwrites target
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            # Both are dicts - recurse
            _deep_merge(target[key], value)
        else:
            # Overwrite (handles non-dict values and new keys)
            target[key] = copy.deepcopy(value)


def apply_prep_force_disable(config: Dict) -> Dict:
    """
    Special override: Force disable all PrEP (for no_prep scenario).
    
    Sets all PrEP initiation schedules to 0 and removes existing PrEP users.
    
    Args:
        config: Configuration to modify
        
    Returns:
        Modified config with PrEP disabled
    """
    # Make a copy to avoid mutating input
    modified = copy.deepcopy(config)
    
    # Disable PrEP interventions
    if "interventions" in modified:
        for prep_type in ["Oral_PrEP", "Lenacapavir"]:
            if prep_type in modified["interventions"]:
                modified["interventions"][prep_type]["enabled"] = {"value": False}
    
    # Set all PrEP schedules to 0
    if "prep_schedules" in modified:
        for prep_type in ["Oral_PrEP", "Lenacapavir"]:
            if prep_type in modified["prep_schedules"]:
                # Replace schedule with all zeros
                if "initiation" in modified["prep_schedules"][prep_type]:
                    schedule = modified["prep_schedules"][prep_type]["initiation"].get("schedule", {})
                    # Keep same years but set all to 0
                    modified["prep_schedules"][prep_type]["initiation"]["schedule"] = {
                        year: 0.0 for year in schedule.keys()
                    }
                
                if "discontinuation" in modified["prep_schedules"][prep_type]:
                    # Set discontinuation to 1.0 to remove existing users
                    schedule = modified["prep_schedules"][prep_type]["discontinuation"].get("schedule", {})
                    modified["prep_schedules"][prep_type]["discontinuation"]["schedule"] = {
                        year: 1.0 for year in schedule.keys()
                    }
    
    return modified


def load_scenarios(config: Dict) -> List[Dict[str, Any]]:
    """
    Load scenario definitions from config.
    
    Args:
        config: Full configuration dictionary
        
    Returns:
        List of scenario definitions, each with:
        - name: Scenario identifier
        - description: Human-readable description
        - overrides: Config overrides to apply
        
    If scenarios disabled or not present, returns single "baseline" scenario.
    """
    scenarios_config = config.get("scenarios", {})
    
    # Check if scenarios enabled
    enabled = scenarios_config.get("enabled", {})
    if isinstance(enabled, dict):
        enabled = enabled.get("value", False)
    
    if not enabled:
        # Return single baseline scenario
        return [{
            "name": "baseline",
            "description": "Single baseline run (scenarios disabled)",
            "overrides": {}
        }]
    
    # Load scenario definitions
    scenario_defs = scenarios_config.get("definitions", [])
    
    if not scenario_defs:
        # No scenarios defined - return baseline
        return [{
            "name": "baseline",
            "description": "Baseline (no scenarios defined)",
            "overrides": {}
        }]
    
    return scenario_defs


def get_policy_config(config: Dict) -> Dict[str, Any]:
    """
    Extract policy configuration (freeze year, reuse baseline, etc.).
    
    Args:
        config: Full configuration dictionary
        
    Returns:
        Dictionary with policy settings:
        - enabled: Whether policy freezing is enabled
        - freeze_year: Year at which to freeze baseline
        - reuse_baseline: Whether to reuse baseline across scenarios
    """
    policy = config.get("policy", {})
    
    # Extract values (handle {value: X} format)
    enabled = policy.get("enabled", {})
    if isinstance(enabled, dict):
        enabled = enabled.get("value", False)
    
    freeze_year = policy.get("baseline_freeze_year", {})
    if isinstance(freeze_year, dict):
        freeze_year = freeze_year.get("value", None)
    
    reuse_baseline = policy.get("reuse_baseline", {})
    if isinstance(reuse_baseline, dict):
        reuse_baseline = reuse_baseline.get("value", True)
    
    return {
        "enabled": enabled,
        "freeze_year": freeze_year,
        "reuse_baseline": reuse_baseline
    }


def validate_scenario_config(scenarios: List[Dict], policy: Dict):
    """
    Validate scenario configuration for common errors.
    
    Args:
        scenarios: List of scenario definitions
        policy: Policy configuration
        
    Raises:
        ValueError: If configuration is invalid
    """
    # Check scenario names are unique
    names = [s["name"] for s in scenarios]
    if len(names) != len(set(names)):
        raise ValueError(f"Scenario names must be unique. Got: {names}")
    
    # Check scenario names are valid identifiers (no spaces, special chars)
    for name in names:
        if not name.replace("_", "").isalnum():
            raise ValueError(
                f"Scenario name '{name}' contains invalid characters. "
                f"Use only letters, numbers, and underscores."
            )
    
    # If policy enabled, must have freeze_year
    if policy["enabled"] and policy["freeze_year"] is None:
        raise ValueError(
            "Policy freezing enabled but baseline_freeze_year not specified in config"
        )
    
    # If multiple scenarios, policy should be enabled
    if len(scenarios) > 1 and not policy["enabled"]:
        print("⚠️  Warning: Multiple scenarios defined but policy freezing disabled.")
        print("   Each scenario will run from different initial random states.")
        print("   Enable policy.enabled for scenario comparability.")


# Scenario override helpers for specific cases
def create_no_prep_override() -> Dict:
    """Create override for 'no PrEP' scenario."""
    return {"prep_force_disable": True}


def create_prep_scaleup_override(prep_type: str, schedule: Dict[str, float]) -> Dict:
    """
    Create override for PrEP scale-up scenario.
    
    Args:
        prep_type: "Oral_PrEP" or "Lenacapavir"
        schedule: Dictionary mapping year (str) to initiation rate
        
    Returns:
        Config override for this scenario
    """
    return {
        "prep_schedules": {
            prep_type: {
                "initiation": {
                    "schedule": schedule
                }
            }
        }
    }
