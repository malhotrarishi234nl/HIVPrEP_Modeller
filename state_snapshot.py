"""
State Snapshot & Restoration (V5 Phase 2)

Enables freezing the entire simulation state at a policy year, then forking
multiple scenarios from that identical baseline for HTA comparability.

Captures:
- Main population tensor (all dimensions)
- Childhood pool (all age bins, both sexes)
- RNG state (for reproducible forking)
- Metadata (year, step, checksums)
"""

import numpy as np
import hashlib
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import pickle


@dataclass
class StateSnapshot:
    """
    Complete snapshot of simulation state at a specific timestep.

    Designed for policy-year baseline freezing: save state at year Y,
    then fork multiple policy scenarios from identical starting point.
    """

    # Core state
    population: np.ndarray  # Main population tensor (all dimensions)
    childhood_population: np.ndarray  # Childhood pool array

    # Temporal metadata
    step: int  # Timestep when snapshot was taken
    year: float  # Calendar year when snapshot was taken

    # Reproducibility
    rng_state: Optional[Dict] = None  # Random number generator state

    # Integrity verification
    population_checksum: str = field(default="")
    childhood_checksum: str = field(default="")
    total_population: int = field(default=0)

    def __post_init__(self):
        """Compute checksums and totals after initialization."""
        if not self.population_checksum:
            self.population_checksum = self._compute_checksum(self.population)
        if not self.childhood_checksum:
            self.childhood_checksum = self._compute_checksum(self.childhood_population)
        if not self.total_population:
            self.total_population = int(np.sum(self.population) + np.sum(self.childhood_population))

    @staticmethod
    def _compute_checksum(array: np.ndarray) -> str:
        """Compute SHA256 checksum of numpy array for verification."""
        return hashlib.sha256(array.tobytes()).hexdigest()[:16]

    def verify_integrity(self) -> bool:
        """
        Verify snapshot hasn't been corrupted.

        Returns:
            True if checksums match, False otherwise
        """
        current_pop_checksum = self._compute_checksum(self.population)
        current_child_checksum = self._compute_checksum(self.childhood_population)

        if current_pop_checksum != self.population_checksum:
            return False
        if current_child_checksum != self.childhood_checksum:
            return False

        return True

    def get_summary(self) -> Dict[str, Any]:
        """Get human-readable summary of snapshot."""
        return {
            "year": self.year,
            "step": self.step,
            "total_population": self.total_population,
            "sexually_active": int(np.sum(self.population)),
            "childhood": int(np.sum(self.childhood_population)),
            "population_checksum": self.population_checksum,
            "childhood_checksum": self.childhood_checksum,
            "has_rng_state": self.rng_state is not None
        }

    def clone(self) -> 'StateSnapshot':
        """
        Create a deep copy of this snapshot.

        Used when forking scenarios - each gets its own copy to avoid
        interference between parallel scenario runs.

        Returns:
            New StateSnapshot with copied arrays
        """
        return StateSnapshot(
            population=self.population.copy(),
            childhood_population=self.childhood_population.copy(),
            step=self.step,
            year=self.year,
            rng_state=self.rng_state.copy() if self.rng_state else None,
            population_checksum=self.population_checksum,
            childhood_checksum=self.childhood_checksum,
            total_population=self.total_population
        )

    def save_to_file(self, filepath: str):
        """Save snapshot to disk for later reuse."""
        with open(filepath, 'wb') as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load_from_file(cls, filepath: str) -> 'StateSnapshot':
        """Load snapshot from disk."""
        with open(filepath, 'rb') as f:
            snapshot = pickle.load(f)

        # Verify integrity after loading
        if not snapshot.verify_integrity():
            raise ValueError(
                f"Snapshot integrity check failed after loading from {filepath}. "
                f"File may be corrupted."
            )

        return snapshot


class StateSnapshotManager:
    """
    Manages state snapshots for baseline freezing and scenario forking.

    Usage pattern:
    1. Run simulation to policy_start_year
    2. freeze() to save baseline state
    3. For each scenario: restore() baseline and continue
    """

    def __init__(self):
        self.baseline_snapshot: Optional[StateSnapshot] = None
        self.freeze_enabled = False
        self.freeze_year = None

    def configure(self, freeze_enabled: bool, freeze_year: Optional[float] = None):
        """
        Configure snapshot manager from policy config.

        Args:
            freeze_enabled: Whether to enable baseline freezing
            freeze_year: Year at which to freeze (policy_start_year)
        """
        self.freeze_enabled = freeze_enabled
        self.freeze_year = freeze_year

    def should_freeze(self, current_year: float, current_step: int) -> bool:
        """
        Check if current timestep is the freeze point.

        Args:
            current_year: Current calendar year
            current_step: Current timestep

        Returns:
            True if should freeze at this timestep
        """
        if not self.freeze_enabled:
            return False

        if self.freeze_year is None:
            return False

        # Freeze when we first reach or pass the freeze year
        # Use >= to handle fractional years
        return current_year >= self.freeze_year and self.baseline_snapshot is None

    def freeze(self, population: np.ndarray, childhood_pool: np.ndarray,
               step: int, year: float, rng: Optional[np.random.RandomState] = None):
        """
        Freeze current state as baseline snapshot.

        Args:
            population: Main population tensor
            childhood_pool: Childhood pool array
            step: Current timestep
            year: Current year
            rng: Random number generator (optional, for reproducibility)
        """
        # Capture RNG state if provided
        rng_state = rng.get_state() if rng is not None else None

        # Create snapshot (makes copies automatically)
        self.baseline_snapshot = StateSnapshot(
            population=population.copy(),
            childhood_population=childhood_pool.copy(),
            step=step,
            year=year,
            rng_state=rng_state
        )

        print(f"\n  ✓ Baseline state frozen at year {year:.1f} (step {step})")
        print(f"    Total population: {self.baseline_snapshot.total_population:,}")
        print(f"    Checksum: {self.baseline_snapshot.population_checksum}")

    def restore(self, scenario_name: str = "") -> StateSnapshot:
        """
        Restore baseline snapshot for a new scenario run.

        Args:
            scenario_name: Name of scenario being restored (for logging)

        Returns:
            Deep copy of baseline snapshot

        Raises:
            RuntimeError: If no baseline has been frozen
        """
        if self.baseline_snapshot is None:
            raise RuntimeError(
                "Cannot restore baseline: no snapshot has been frozen. "
                "Ensure freeze() is called before restore()."
            )

        # Verify baseline integrity before restoring
        if not self.baseline_snapshot.verify_integrity():
            raise RuntimeError(
                "Baseline snapshot failed integrity check. "
                "Population state may have been corrupted."
            )

        # Return deep copy so each scenario has independent state
        restored = self.baseline_snapshot.clone()

        if scenario_name:
            print(f"  ✓ Restored baseline for scenario '{scenario_name}'")

        return restored

    def verify_baseline_identity(self, other_snapshot: Optional[StateSnapshot] = None) -> bool:
        """
        Verify that all scenarios start from identical baseline.

        Args:
            other_snapshot: Another snapshot to compare against baseline

        Returns:
            True if snapshots are identical
        """
        if self.baseline_snapshot is None:
            return False

        if other_snapshot is None:
            # Just verify baseline integrity
            return self.baseline_snapshot.verify_integrity()

        # Compare checksums
        if self.baseline_snapshot.population_checksum != other_snapshot.population_checksum:
            return False
        if self.baseline_snapshot.childhood_checksum != other_snapshot.childhood_checksum:
            return False

        return True

    def get_baseline_summary(self) -> Optional[Dict[str, Any]]:
        """Get summary of frozen baseline (for reporting)."""
        if self.baseline_snapshot is None:
            return None
        return self.baseline_snapshot.get_summary()


def validate_baseline_identity(snapshot1: StateSnapshot, snapshot2: StateSnapshot,
                                tolerance: float = 0.0) -> bool:
    """
    Validate that two snapshots are identical (for V5 comparability requirement).

    Args:
        snapshot1: First snapshot
        snapshot2: Second snapshot
        tolerance: Allowed difference (0.0 = exact match required)

    Returns:
        True if identical within tolerance

    Raises:
        ValueError: If snapshots differ beyond tolerance
    """
    # Check checksums first (fast)
    if snapshot1.population_checksum != snapshot2.population_checksum:
        raise ValueError(
            f"Population checksums differ: {snapshot1.population_checksum} vs {snapshot2.population_checksum}. "
            f"Scenarios do NOT start from identical baseline."
        )

    if snapshot1.childhood_checksum != snapshot2.childhood_checksum:
        raise ValueError(
            f"Childhood checksums differ. Scenarios do NOT start from identical baseline."
        )

    # Deep array comparison if tolerance > 0
    if tolerance > 0:
        pop_diff = np.abs(snapshot1.population - snapshot2.population).max()
        child_diff = np.abs(snapshot1.childhood_population - snapshot2.childhood_population).max()

        if pop_diff > tolerance or child_diff > tolerance:
            raise ValueError(
                f"State arrays differ beyond tolerance ({tolerance}): "
                f"max_pop_diff={pop_diff}, max_child_diff={child_diff}"
            )

    return True
