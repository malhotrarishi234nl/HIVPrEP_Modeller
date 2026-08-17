"""
Validation Framework (V5 Phase 5)

Comprehensive validation checks for V5 configurations and scenarios:
1. Schedule sanity checks (rates, years, coherence)
2. No-PrEP enforcement validation
3. Baseline identity validation (for frozen baseline mode)
4. Config consistency checks
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of a validation check."""
    passed: bool
    message: str
    severity: str = "error"  # "error", "warning", "info"
    
    def __str__(self):
        symbols = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
        symbol = symbols.get(self.severity, "•")
        status = "PASS" if self.passed else "FAIL"
        return f"{symbol} [{status}] {self.message}"


class ValidationSuite:
    """Collection of validation checks for V5 configs."""
    
    def __init__(self):
        self.results: List[ValidationResult] = []
    
    def add_result(self, passed: bool, message: str, severity: str = "error"):
        """Add a validation result."""
        self.results.append(ValidationResult(passed, message, severity))
    
    def has_errors(self) -> bool:
        """Check if any error-level validations failed."""
        return any(not r.passed and r.severity == "error" for r in self.results)
    
    def has_warnings(self) -> bool:
        """Check if any warnings were raised."""
        return any(not r.passed and r.severity == "warning" for r in self.results)
    
    def print_summary(self):
        """Print validation summary."""
        errors = sum(1 for r in self.results if not r.passed and r.severity == "error")
        warnings = sum(1 for r in self.results if not r.passed and r.severity == "warning")
        passed = sum(1 for r in self.results if r.passed)
        
        print("\n" + "=" * 70)
        print("VALIDATION SUMMARY")
        print("=" * 70)
        
        for result in self.results:
            print(f"  {result}")
        
        print("\n" + "-" * 70)
        print(f"Total checks: {len(self.results)}")
        print(f"  ✅ Passed: {passed}")
        if warnings > 0:
            print(f"  ⚠️  Warnings: {warnings}")
        if errors > 0:
            print(f"  ❌ Errors: {errors}")
        print("=" * 70)
        
        return errors == 0


def validate_schedule_sanity(
    schedule_dict: Dict[str, float],
    schedule_name: str,
    start_year: float,
    end_year: float
) -> List[ValidationResult]:
    """
    Validate schedule sanity (rates, years, coherence).
    
    Args:
        schedule_dict: Year -> rate mapping
        schedule_name: Name for error messages
        start_year: Simulation start year
        end_year: Simulation end year
        
    Returns:
        List of validation results
    """
    results = []
    
    if not schedule_dict:
        results.append(ValidationResult(
            True, 
            f"{schedule_name}: Empty schedule (valid - will use defaults)",
            "info"
        ))
        return results
    
    # Convert years to float for comparison
    years = [float(y) for y in schedule_dict.keys()]
    rates = [float(r) for r in schedule_dict.values()]
    
    # Check 1: Years are sorted
    if years != sorted(years):
        results.append(ValidationResult(
            False,
            f"{schedule_name}: Years not sorted: {list(schedule_dict.keys())}",
            "error"
        ))
    else:
        results.append(ValidationResult(
            True,
            f"{schedule_name}: Years correctly sorted",
            "info"
        ))
    
    # Check 2: No duplicate years
    if len(years) != len(set(years)):
        results.append(ValidationResult(
            False,
            f"{schedule_name}: Duplicate years found",
            "error"
        ))
    
    # Check 3: Rates in valid range [0, 1]
    invalid_rates = [r for r in rates if r < 0 or r > 1]
    if invalid_rates:
        results.append(ValidationResult(
            False,
            f"{schedule_name}: Rates outside [0,1]: {invalid_rates}",
            "error"
        ))
    else:
        results.append(ValidationResult(
            True,
            f"{schedule_name}: All rates in valid range [0,1]",
            "info"
        ))
    
    # Check 4: First year not before simulation start
    if years and years[0] < start_year:
        results.append(ValidationResult(
            False,
            f"{schedule_name}: First year {years[0]} before simulation start {start_year}",
            "warning"
        ))
    
    # Check 5: Last year not after simulation end
    if years and years[-1] > end_year:
        results.append(ValidationResult(
            False,
            f"{schedule_name}: Last year {years[-1]} after simulation end {end_year}",
            "warning"
        ))
    
    # Check 6: Reasonable number of points (not too sparse or dense)
    if years:
        span = years[-1] - years[0]
        n_points = len(years)
        if n_points > 1 and span / n_points < 0.5:
            results.append(ValidationResult(
                False,
                f"{schedule_name}: Very dense schedule (avg {span/n_points:.1f} years between points)",
                "warning"
            ))
    
    return results


def validate_prep_disabled(config: Dict, scenario_name: str) -> List[ValidationResult]:
    """
    Validate that PrEP is truly disabled in a no-PrEP scenario.
    
    Args:
        config: Configuration to check
        scenario_name: Scenario name for messages
        
    Returns:
        List of validation results
    """
    results = []
    
    # Check interventions disabled
    interventions = config.get("interventions", {})
    for prep_type in ["Oral_PrEP", "Lenacapavir"]:
        if prep_type in interventions:
            enabled = interventions[prep_type].get("enabled", {})
            if isinstance(enabled, dict):
                enabled = enabled.get("value", False)
            
            if enabled:
                results.append(ValidationResult(
                    False,
                    f"{scenario_name}: {prep_type} intervention not disabled",
                    "error"
                ))
            else:
                results.append(ValidationResult(
                    True,
                    f"{scenario_name}: {prep_type} intervention disabled",
                    "info"
                ))
    
    # Check schedules are zero
    prep_schedules = config.get("prep_schedules", {})
    for prep_type in ["Oral_PrEP", "Lenacapavir"]:
        if prep_type in prep_schedules:
            initiation = prep_schedules[prep_type].get("initiation", {})
            schedule = initiation.get("schedule", {})
            
            non_zero_rates = {year: rate for year, rate in schedule.items() if float(rate) != 0.0}
            
            if non_zero_rates:
                results.append(ValidationResult(
                    False,
                    f"{scenario_name}: {prep_type} initiation schedule has non-zero rates: {non_zero_rates}",
                    "error"
                ))
            else:
                results.append(ValidationResult(
                    True,
                    f"{scenario_name}: {prep_type} initiation schedule all zeros",
                    "info"
                ))
    
    return results


def validate_baseline_identity(
    snapshot1_checksum: str,
    snapshot2_checksum: str,
    scenario1: str,
    scenario2: str
) -> ValidationResult:
    """
    Validate that two scenarios started from identical baseline.
    
    Args:
        snapshot1_checksum: Checksum of first snapshot
        snapshot2_checksum: Checksum of second snapshot
        scenario1: Name of first scenario
        scenario2: Name of second scenario
        
    Returns:
        Validation result
    """
    if snapshot1_checksum == snapshot2_checksum:
        return ValidationResult(
            True,
            f"Baseline identity: {scenario1} and {scenario2} started from identical state",
            "info"
        )
    else:
        return ValidationResult(
            False,
            f"Baseline identity: {scenario1} and {scenario2} started from DIFFERENT states",
            "error"
        )


def validate_config_consistency(config: Dict) -> List[ValidationResult]:
    """
    Validate overall config consistency.
    
    Args:
        config: Full configuration dictionary
        
    Returns:
        List of validation results
    """
    results = []
    
    # Check time configuration exists — V10 configs use 'run_control', V5 configs use 'time'
    time_config = config.get("time") or config.get("run_control")
    if not time_config:
        results.append(ValidationResult(False, "Missing 'time' or 'run_control' section", "error"))
        return results

    start_year = time_config.get("start_year", {})
    end_year = time_config.get("end_year", {})
    
    if isinstance(start_year, dict):
        start_year = start_year.get("value")
    if isinstance(end_year, dict):
        end_year = end_year.get("value")
    
    if start_year is None or end_year is None:
        results.append(ValidationResult(
            False,
            "Missing start_year or end_year in time configuration",
            "error"
        ))
        return results
    
    # Check time range validity
    if end_year <= start_year:
        results.append(ValidationResult(
            False,
            f"end_year ({end_year}) must be after start_year ({start_year})",
            "error"
        ))
    else:
        results.append(ValidationResult(
            True,
            f"Time range valid: {start_year} to {end_year}",
            "info"
        ))
    
    # Check policy configuration if present
    if "policy" in config:
        policy = config["policy"]
        enabled = policy.get("enabled", {})
        freeze_year = policy.get("baseline_freeze_year", {})
        
        if isinstance(enabled, dict):
            enabled = enabled.get("value", False)
        if isinstance(freeze_year, dict):
            freeze_year = freeze_year.get("value")
        
        if enabled and freeze_year is not None:
            if freeze_year < start_year or freeze_year >= end_year:
                results.append(ValidationResult(
                    False,
                    f"Freeze year {freeze_year} outside simulation range [{start_year}, {end_year})",
                    "error"
                ))
            else:
                results.append(ValidationResult(
                    True,
                    f"Freeze year {freeze_year} within valid range",
                    "info"
                ))
    
    return results


def validate_v5_config(config: Dict, scenarios: Optional[List[Dict]] = None) -> ValidationSuite:
    """
    Run full validation suite on V5 configuration.
    
    Args:
        config: Full configuration dictionary
        scenarios: Optional list of scenarios to validate
        
    Returns:
        ValidationSuite with all results
    """
    suite = ValidationSuite()
    
    print("\n" + "=" * 70)
    print("V5 CONFIGURATION VALIDATION")
    print("=" * 70)
    
    # 1. Config consistency
    print("\n📋 Checking configuration consistency...")
    for result in validate_config_consistency(config):
        suite.results.append(result)
    
    # 2. Extract time range — V10 configs use 'run_control', V5 configs use 'time'
    time_config = config.get("time") or config.get("run_control", {})
    start_year = time_config.get("start_year", {})
    end_year = time_config.get("end_year", {})
    if isinstance(start_year, dict):
        start_year = start_year.get("value", 1980)
    if isinstance(end_year, dict):
        end_year = end_year.get("value", 2020)
    
    # 3. Validate ART schedules if present
    if "art_schedule" in config:
        print("\n📋 Validating ART schedules...")
        art_schedule = config["art_schedule"]
        
        if "initiation" in art_schedule:
            schedule = art_schedule["initiation"].get("schedule", {})
            for result in validate_schedule_sanity(
                schedule, "ART initiation", start_year, end_year
            ):
                suite.results.append(result)
        
        if "discontinuation" in art_schedule:
            schedule = art_schedule["discontinuation"].get("schedule", {})
            for result in validate_schedule_sanity(
                schedule, "ART discontinuation", start_year, end_year
            ):
                suite.results.append(result)
    
    # 4. Validate PrEP schedules if present
    if "prep_schedules" in config:
        print("\n📋 Validating PrEP schedules...")
        prep_schedules = config["prep_schedules"]
        
        for prep_type in ["Oral_PrEP", "Lenacapavir"]:
            if prep_type in prep_schedules:
                prep_config = prep_schedules[prep_type]
                
                if "initiation" in prep_config:
                    schedule = prep_config["initiation"].get("schedule", {})
                    for result in validate_schedule_sanity(
                        schedule, f"{prep_type} initiation", start_year, end_year
                    ):
                        suite.results.append(result)
                
                if "discontinuation" in prep_config:
                    schedule = prep_config["discontinuation"].get("schedule", {})
                    for result in validate_schedule_sanity(
                        schedule, f"{prep_type} discontinuation", start_year, end_year
                    ):
                        suite.results.append(result)
    
    # 5. Validate scenarios if provided
    if scenarios:
        print("\n📋 Validating scenarios...")
        
        # Check for no_prep scenarios
        # ENG-F11: validate_prep_disabled() requires a merged config (base + overrides),
        # which is not available here. Emit a deferred warning rather than a false pass.
        for scenario in scenarios:
            if "no_prep" in scenario["name"].lower():
                suite.add_result(
                    True,
                    f"Scenario '{scenario['name']}': no-PrEP scenario identified. "
                    f"Schedule validation deferred — requires merged config at runtime.",
                    "warning",
                )
    
    return suite


if __name__ == "__main__":
    # Example usage
    import json
    import sys
    
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        suite = validate_v5_config(config)
        passed = suite.print_summary()
        
        sys.exit(0 if passed else 1)
    else:
        print("Usage: python validation.py <config.json>")
