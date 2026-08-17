"""
Time-Based Schedule Utilities (V5)

Handles year-based schedules for ART/PrEP availability and uptake rates.
Supports stepwise (carry-forward) and linear interpolation between years.

Default behavior: Before first scheduled year, value = 0 (unavailable).
"""

from typing import Dict, Optional, Any
import numpy as np
from config_utils import extract_value


class TimeSchedule:
    """
    Manages time-based schedules for rates/probabilities.

    Schedule format: {year: value, year: value, ...}
    Before first year: returns 0 (unavailable)
    After last year: returns last value
    Between years: stepwise or linear interpolation
    """

    def __init__(self, schedule: Dict[str, float], interpolation: str = "stepwise",
                 default_before: float = 0.0):
        """
        Initialize time schedule.

        Args:
            schedule: Dictionary mapping year (str or int) to value
            interpolation: "stepwise" (carry-forward) or "linear"
            default_before: Value to return before first scheduled year (default: 0.0)
        """
        if not schedule:
            self.years = []
            self.values = []
            self.is_empty = True
            self.interpolation = interpolation
            self.default_before = default_before
            return

        # Convert schedule to sorted arrays
        year_value_pairs = [(float(y), v) for y, v in schedule.items()]
        year_value_pairs.sort(key=lambda x: x[0])

        self.years = np.array([y for y, v in year_value_pairs])
        self.values = np.array([v for y, v in year_value_pairs])
        self.is_empty = False
        self.interpolation = interpolation
        self.default_before = default_before

        # Validate
        if interpolation not in ["stepwise", "linear"]:
            raise ValueError(f"interpolation must be 'stepwise' or 'linear', got '{interpolation}'")

        for v in self.values:
            if v < 0:
                raise ValueError(f"Schedule values must be non-negative, got {v}")

    def get_value(self, current_year: float) -> float:
        """
        Get value for a given calendar year.

        Args:
            current_year: Calendar year (float to handle fractional years)

        Returns:
            Scheduled value for that year
        """
        if self.is_empty:
            return self.default_before

        # Before first scheduled year
        if current_year < self.years[0]:
            return self.default_before

        # After last scheduled year
        if current_year >= self.years[-1]:
            return float(self.values[-1])

        # Between scheduled years
        if self.interpolation == "stepwise":
            # Carry forward last value before current_year
            idx = np.searchsorted(self.years, current_year, side='right') - 1
            return float(self.values[idx])
        else:  # linear
            # Linear interpolation
            return float(np.interp(current_year, self.years, self.values))

    def get_first_year(self) -> Optional[float]:
        """Get first year in schedule (when this becomes available)."""
        if self.is_empty:
            return None
        return float(self.years[0])

    def get_first_positive_year(self) -> Optional[float]:
        """Get first year whose scheduled value is positive."""
        if self.is_empty:
            return None
        positive = self.years[self.values > 0]
        if len(positive) == 0:
            return None
        return float(positive[0])

    def get_last_year(self) -> Optional[float]:
        """Get last year in schedule."""
        if self.is_empty:
            return None
        return float(self.years[-1])

    def is_available(self, current_year: float) -> bool:
        """Check if schedule has a non-zero value at given year."""
        return self.get_value(current_year) > 0

    def __repr__(self):
        if self.is_empty:
            return f"TimeSchedule(empty, default={self.default_before})"
        return f"TimeSchedule(years={list(self.years)}, interp={self.interpolation})"


class ScheduleManager:
    """
    Manages multiple time schedules (e.g., ART initiation, discontinuation, multiple PrEP types).
    """

    def __init__(self, config: Dict):
        """
        Initialize schedule manager from config.

        Expected config structure:
        {
          "art_schedule": {
            "interpolation": {"value": "stepwise"},
            "initiation": {"schedule": {year: value, ...}},
            "discontinuation": {"schedule": {year: value, ...}}
          },
          "prep_schedules": {
            "interpolation": {"value": "stepwise"},
            "Oral_PrEP": {
              "initiation": {"schedule": {year: value, ...}},
              "discontinuation": {"schedule": {year: value, ...}}
            },
            "Lenacapavir": {...}
          }
        }
        """
        self.config = config

        # Extract ART schedules
        art_config = config.get("art_schedule", {})
        art_interp = extract_value(art_config.get("interpolation", {"value": "stepwise"}))

        self.art_initiation = self._load_schedule(
            art_config.get("initiation", {}), "schedule", art_interp
        )
        self.art_discontinuation = self._load_schedule(
            art_config.get("discontinuation", {}), "schedule", art_interp
        )

        # Extract PrEP schedules
        prep_config = config.get("prep_schedules", {})
        prep_interp = extract_value(prep_config.get("interpolation", {"value": "stepwise"}))

        self.prep_initiation = {}
        self.prep_discontinuation = {}

        # ENG-F06: discover PrEP types dynamically; do not hardcode
        _SCHEDULE_SKIP_KEYS = {"interpolation"}
        for prep_name in [k for k in prep_config if k not in _SCHEDULE_SKIP_KEYS]:
            if prep_name in prep_config:
                prep_type_config = prep_config[prep_name]

                self.prep_initiation[prep_name] = self._load_schedule(
                    prep_type_config.get("initiation", {}), "schedule", prep_interp
                )
                self.prep_discontinuation[prep_name] = self._load_schedule(
                    prep_type_config.get("discontinuation", {}), "schedule", prep_interp
                )

    def _load_schedule(self, config_section: Dict, schedule_key: str,
                       interpolation: str) -> TimeSchedule:
        """Helper to load a single schedule from config."""
        schedule_dict = config_section.get(schedule_key, {})
        return TimeSchedule(schedule_dict, interpolation)

    def get_art_initiation_rate(self, current_year: float) -> float:
        """Get ART initiation rate for given year."""
        intro_year = self.art_initiation.get_first_positive_year()
        if intro_year is None or current_year < intro_year:
            return 0.0
        return self.art_initiation.get_value(current_year)

    def get_art_discontinuation_rate(self, current_year: float) -> float:
        """Get ART discontinuation rate for given year."""
        return self.art_discontinuation.get_value(current_year)

    def get_prep_initiation_rate(self, prep_type: str, current_year: float) -> float:
        """Get PrEP initiation rate for given type and year."""
        if prep_type not in self.prep_initiation:
            return 0.0
        intro_year = self.prep_initiation[prep_type].get_first_positive_year()
        if intro_year is None or current_year < intro_year:
            return 0.0
        return self.prep_initiation[prep_type].get_value(current_year)

    def get_prep_discontinuation_rate(self, prep_type: str, current_year: float) -> float:
        """Get PrEP discontinuation rate for given type and year."""
        if prep_type not in self.prep_discontinuation:
            return 0.0
        return self.prep_discontinuation[prep_type].get_value(current_year)

    def is_art_available(self, current_year: float) -> bool:
        """Check if ART is available at given year (initiation rate > 0)."""
        return self.art_initiation.is_available(current_year)

    def is_prep_available(self, prep_type: str, current_year: float) -> bool:
        """Check if specific PrEP type is available at given year."""
        if prep_type not in self.prep_initiation:
            return False
        return self.prep_initiation[prep_type].is_available(current_year)

    def get_art_introduction_year(self) -> Optional[float]:
        """Get year when ART was introduced (first non-zero year)."""
        return self.art_initiation.get_first_positive_year()

    def get_prep_introduction_year(self, prep_type: str) -> Optional[float]:
        """Get year when specific PrEP type was introduced."""
        if prep_type not in self.prep_initiation:
            return None
        return self.prep_initiation[prep_type].get_first_positive_year()

    def validate_sanity(self):
        """
        Run sanity checks on schedules.

        Checks:
        - All rates are in [0, 1]
        - Discontinuation rates <= initiation rates (warning only)
        """
        # Check ART rates
        if not self.art_initiation.is_empty:
            for val in self.art_initiation.values:
                if not (0 <= val <= 1):
                    raise ValueError(f"ART initiation rate {val} not in [0,1]")

        if not self.art_discontinuation.is_empty:
            for val in self.art_discontinuation.values:
                if not (0 <= val <= 1):
                    raise ValueError(f"ART discontinuation rate {val} not in [0,1]")

        # Check PrEP rates
        for prep_type in self.prep_initiation:
            if not self.prep_initiation[prep_type].is_empty:
                for val in self.prep_initiation[prep_type].values:
                    if not (0 <= val <= 1):
                        raise ValueError(f"{prep_type} initiation rate {val} not in [0,1]")

            if prep_type in self.prep_discontinuation:
                if not self.prep_discontinuation[prep_type].is_empty:
                    for val in self.prep_discontinuation[prep_type].values:
                        if not (0 <= val <= 1):
                            raise ValueError(f"{prep_type} discontinuation rate {val} not in [0,1]")

            prep_type_config = self.config.get("prep_schedules", {}).get(prep_type, {})
            corridor = extract_value(
                prep_type_config.get("advanced", {}).get("historical_stock_corridor", {})
            )
            if not corridor:
                continue
            floor_schedule = extract_value(corridor.get("stock_floor_schedule", {})) or {}
            ceiling_schedule = extract_value(corridor.get("stock_ceiling_schedule", {})) or {}

            for label, schedule in (
                ("floor", floor_schedule),
                ("ceiling", ceiling_schedule),
            ):
                for year, value in schedule.items():
                    if float(value) < 0:
                        raise ValueError(
                            f"{prep_type} historical_stock_corridor {label} "
                            f"for {year} must be non-negative, got {value}"
                        )

            interpolation = extract_value(corridor.get("interpolation", {"value": "stepwise"}))
            floor_ts = TimeSchedule(floor_schedule, interpolation=interpolation)
            ceiling_ts = TimeSchedule(ceiling_schedule, interpolation=interpolation)
            years = sorted({float(y) for y in floor_schedule} | {float(y) for y in ceiling_schedule})
            for year in years:
                floor = None if floor_ts.is_empty else floor_ts.get_value(year)
                ceiling = None if ceiling_ts.is_empty else ceiling_ts.get_value(year)
                if floor is not None and ceiling is not None and floor > ceiling:
                    raise ValueError(
                        f"{prep_type} historical_stock_corridor floor {floor} "
                        f"exceeds ceiling {ceiling} at year {year:g}"
                    )


def validate_schedule_before_intro(schedule_manager: ScheduleManager,
                                    current_year: float,
                                    intro_year: Optional[float],
                                    rate: float,
                                    name: str):
    """
    Validate that rate is 0 before introduction year.

    Args:
        schedule_manager: ScheduleManager instance
        current_year: Current simulation year
        intro_year: Year of introduction (None if never introduced)
        rate: Current rate value
        name: Name of what's being checked (for error message)

    Raises:
        ValueError: If rate > 0 before introduction year
    """
    if intro_year is None:
        # Never introduced - rate should always be 0
        if rate > 0:
            raise ValueError(
                f"{name} has non-zero rate ({rate}) but no introduction year defined. "
                f"This should not happen."
            )
    elif current_year < intro_year:
        # Before introduction
        if rate > 0:
            raise ValueError(
                f"{name} has non-zero rate ({rate}) in year {current_year}, "
                f"but introduction year is {intro_year}. "
                f"Rate should be 0 before introduction."
            )
