"""
migration.py — Net Migration Module (Population Stabilizer)
V9 Patch: Net Migration

Design principles:
  - Demographic correction layer only; NOT an epidemiological driver.
  - All entrants enter as Susceptible / Undiagnosed / No-ART / No-PrEP.
  - Negative migration: proportional removal — preserves HIV/ART/risk structure.
  - Tensor mass balance preserved to within integer-rounding tolerance.
  - Must be called AFTER adult mortality and BEFORE aging each timestep.

Quantity modes  (mutually exclusive):
  year_specific  — explicit net migration per calendar year (World Bank table)
  rate           — net migration as fraction of current total population per year
  flat           — constant net migration number applied every year

Entry-profile modes  (mutually exclusive):
  mirror         — proportional to current age-sex structure (default, safe)
  working_age    — restricted to configurable adult age band [min_age, max_age]
  custom         — user-supplied age-sex weight vector (auto-normalised)

Debut rule (automatic):
  entrant age < 15  → childhood pool
  entrant age ≥ 15  → adult tensor (Susceptible/Undiag/No-ART/No-PrEP)
"""

import warnings
from typing import Dict, Tuple

import numpy as np
from config_utils import extract_value as _ev


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class MigrationConfigError(ValueError):
    """Raised for invalid migration configuration."""
    pass


class MigrationIntegrityError(RuntimeError):
    """Raised when a migration step violates tensor integrity."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# MigrationModule
# ─────────────────────────────────────────────────────────────────────────────

class MigrationModule:
    """
    Net migration module for long-run demographic stabilisation.

    Usage (inside SimulationEngineV3.step_simulation):
        net_change = self.migration.apply(
            self.state.population,
            self.demography.childhood,
            current_year,
            self.time,
            self.rng,
            self.deterministic,
        )
    Returns the ACTUAL integer net population change for mass-balance accounting.
    """

    # ChildhoodPool sex indices (matches demography.py)
    _FEMALE_SEX = 0
    _MALE_SEX   = 1

    # Childhood bin labels in age order
    _CHILD_BIN_LABELS = ["0-4", "5-9", "10-14"]
    _DEFAULT_MIGRANT_HIV_FALLBACK_START_YEAR = 2018
    _DEFAULT_MIGRANT_HIV_FALLBACK_END_YEAR = 2021

    def __init__(self, config: dict, groups, demography_module):
        """
        Parameters
        ----------
        config            : Full simulation config dict.
        groups            : GroupSystem instance (index lookups).
        demography_module : DemographyModule instance (p_msm, etc.).
        """
        mig_cfg = config.get("migration", {})
        self._groups = groups
        self.last_migrant_hiv_stats = self._empty_migrant_hiv_stats()
        self.enabled = bool(_ev(mig_cfg.get("enabled", False)))

        if not self.enabled:
            return

        # ── Quantity mode ─────────────────────────────────────────────────
        qty = mig_cfg.get("quantity", {})
        self.quantity_mode = str(_ev(qty.get("mode", "flat")))

        if self.quantity_mode == "year_specific":
            raw = _ev(qty.get("year_specific", {}))
            if not isinstance(raw, dict) or len(raw) == 0:
                raise MigrationConfigError(
                    "quantity.mode='year_specific' requires a non-empty "
                    "quantity.year_specific dict."
                )
            self._year_values: Dict[int, float] = {
                int(k): float(v) for k, v in raw.items()
            }
            # ENG-F09: cache range for gap-year detection
            self._year_min: int = min(self._year_values)
            self._year_max: int = max(self._year_values)
            self._warned_gap_years: set = set()

        elif self.quantity_mode == "rate":
            self._rate = float(_ev(qty.get("rate", 0.0)))

        elif self.quantity_mode == "flat":
            self._flat = float(_ev(qty.get("flat", 0.0)))

        else:
            raise MigrationConfigError(
                f"Unknown migration quantity mode: '{self.quantity_mode}'. "
                "Must be 'year_specific', 'rate', or 'flat'."
            )

        # ── Profile mode ──────────────────────────────────────────────────
        prof = mig_cfg.get("profile", {})
        self.profile_mode = str(_ev(prof.get("mode", "mirror")))

        if self.profile_mode == "working_age":
            wa = prof.get("working_age", {})
            self._wa_min = int(_ev(wa.get("min_age", 20)))
            self._wa_max = int(_ev(wa.get("max_age", 40)))
            if self._wa_min >= self._wa_max:
                raise MigrationConfigError(
                    f"working_age.min_age ({self._wa_min}) must be < max_age ({self._wa_max})."
                )

        elif self.profile_mode == "custom":
            raw_w = _ev(prof.get("custom_weights", {}))
            if not raw_w:
                raise MigrationConfigError(
                    "profile.mode='custom' requires a non-empty "
                    "profile.custom_weights dict."
                )
            _valid = set(groups.age_to_idx.keys()) | set(self._CHILD_BIN_LABELS)
            self._custom_weights = _parse_and_normalise_custom_weights(raw_w, _valid)

        elif self.profile_mode != "mirror":
            raise MigrationConfigError(
                f"Unknown migration profile mode: '{self.profile_mode}'. "
                "Must be 'mirror', 'working_age', or 'custom'."
            )

        # ── Index caches (from groups) ────────────────────────────────────
        self._female_idx    = groups.orient_to_idx["Female"]
        self._hetero_idx    = groups.orient_to_idx["Heterosexual_Male"]
        self._msm_idx       = groups.orient_to_idx["MSM"]
        self._susc_idx      = groups.hiv_to_idx["Susceptible"]
        # Undiagnosed=0, No-ART=0, No-PrEP=0 (first index in each dimension)

        # Adult age bins in tensor-index order  [(label, tensor_idx), ...]
        self._adult_bins: list = sorted(
            groups.age_to_idx.items(), key=lambda x: x[1]
        )
        # Parse [min, max] age for each adult bin
        self._adult_bin_ages: Dict[str, Tuple[int, int]] = {}
        for label, _ in self._adult_bins:
            if label.endswith("+"):
                # Open-ended terminal bin (e.g. "65+", "110+").
                # Use a large sentinel for hi so overlap checks work correctly.
                lo = int(label[:-1])
                self._adult_bin_ages[label] = (lo, 999)
            else:
                lo, hi = label.split("-")
                self._adult_bin_ages[label] = (int(lo), int(hi))

        # ── MSM proportion ────────────────────────────────────────────────
        self._p_msm = float(demography_module.p_msm)

        # ── Activity weights (aligned with groups.activity_to_idx order) ──
        # _ev() unwraps the {"value": {...}} wrapper used throughout the config
        act_dist_raw = _ev(config.get("population", {}).get(
            "activity_distribution", {}
        ))
        acts_ordered = sorted(
            groups.activity_to_idx.items(), key=lambda x: x[1]
        )  # [(name, 0), (name, 1), ...]
        if act_dist_raw:
            raw_w = np.array(
                [float(_ev(act_dist_raw.get(name, 0.0)))
                 for name, _ in acts_ordered],
                dtype=float,
            )
            if raw_w.sum() <= 0:
                raise MigrationConfigError(
                    "Activity distribution weights sum to zero."
                )
            self._act_weights = raw_w / raw_w.sum()
        else:
            n = groups.n_activities
            self._act_weights = np.ones(n, dtype=float) / n

        # ── V13.1 Migrant HIV entry configuration ─────────────────────────
        self._init_migrant_hiv_config(mig_cfg)

    def _empty_migrant_hiv_stats(self) -> Dict[str, int]:
        return {
            "migrant_hiv_requested_entries": 0,
            "migrant_hiv_entries": 0,
            "migrant_hiv_capped_entries": 0,
            "migrant_hiv_cap_shortfall": 0,
            "migrant_hiv_on_art_entries": 0,
            "migrant_hiv_msm_entries": 0,
            "migrant_hiv_hetero_male_entries": 0,
            "migrant_hiv_female_entries": 0,
        }

    def _init_migrant_hiv_config(self, mig_cfg: dict) -> None:
        mh = mig_cfg.get("migrant_hiv", {})
        self._migrant_hiv_enabled = bool(_ev(mh.get("enabled", False)))
        self._migrant_hiv_count_mode = str(_ev(mh.get("count_mode", "year_specific")))

        raw_year_counts = _ev(mh.get("year_specific_counts", {}))
        self._migrant_hiv_year_counts: Dict[int, float] = {}
        if raw_year_counts:
            if not isinstance(raw_year_counts, dict):
                raise MigrationConfigError(
                    "migrant_hiv.year_specific_counts must be a dict keyed by year."
                )
            self._migrant_hiv_year_counts = {
                int(k): float(v) for k, v in raw_year_counts.items()
            }

        self._migrant_hiv_fallback_count = float(_ev(mh.get("fallback_count", 0.0)))
        self._migrant_hiv_fallback_start_year, self._migrant_hiv_fallback_end_year = (
            self._parse_migrant_hiv_fallback_years(mh)
        )
        self._migrant_hiv_rate = float(_ev(mh.get("rate_per_positive_migrant", 0.0)))

        self._migrant_hiv_orient_weights = self._parse_distribution(
            _ev(mh.get("orientation_distribution", {})),
            {
                "MSM": self._msm_idx,
                "Heterosexual_Male": self._hetero_idx,
                "Female": self._female_idx,
            },
            "migrant_hiv.orientation_distribution",
            default={"MSM": 0.53, "Heterosexual_Male": 0.21, "Female": 0.26},
        )
        self._migrant_hiv_stage_weights = self._parse_distribution(
            _ev(mh.get("hiv_stage_distribution", {})),
            self._groups.hiv_to_idx,
            "migrant_hiv.hiv_stage_distribution",
            default={"Chronic": 1.0},
        )
        if self._susc_idx in set(self._migrant_hiv_stage_weights[0].tolist()):
            raise MigrationConfigError(
                "migrant_hiv.hiv_stage_distribution cannot include Susceptible."
            )
        self._migrant_hiv_diag_weights = self._parse_distribution(
            _ev(mh.get("diagnosis_distribution", {})),
            {"Undiagnosed": 0, "Diagnosed": 1},
            "migrant_hiv.diagnosis_distribution",
            default={"Diagnosed": 1.0},
        )
        self._migrant_hiv_art_weights = self._parse_distribution(
            _ev(mh.get("art_distribution", {})),
            {"Not_on_ART": 0, "On_ART": 1},
            "migrant_hiv.art_distribution",
            default={"On_ART": 0.90, "Not_on_ART": 0.10},
        )
        prep_state = str(_ev(mh.get("prep_state", "None")))
        self._migrant_hiv_prep_idx = self._groups.prep_to_idx.get(prep_state, 0)

        if self._migrant_hiv_count_mode not in {"year_specific", "flat", "rate_per_positive_migrant"}:
            raise MigrationConfigError(
                "migrant_hiv.count_mode must be 'year_specific', 'flat', "
                "or 'rate_per_positive_migrant'."
            )

    def _parse_migrant_hiv_fallback_years(self, mh: dict) -> Tuple[int, int]:
        fallback_years = _ev(mh.get("fallback_years", None))
        start = self._DEFAULT_MIGRANT_HIV_FALLBACK_START_YEAR
        end = self._DEFAULT_MIGRANT_HIV_FALLBACK_END_YEAR

        if fallback_years is not None:
            if isinstance(fallback_years, dict):
                start = int(_ev(fallback_years.get("start", fallback_years.get("start_year", start))))
                end = int(_ev(fallback_years.get("end", fallback_years.get("end_year", end))))
            elif isinstance(fallback_years, (list, tuple)) and len(fallback_years) == 2:
                start = int(_ev(fallback_years[0]))
                end = int(_ev(fallback_years[1]))
            else:
                raise MigrationConfigError(
                    "migrant_hiv.fallback_years must be {'start': year, 'end': year} "
                    "or a two-item [start, end] list."
                )

        start = int(_ev(mh.get("fallback_start_year", start)))
        end = int(_ev(mh.get("fallback_end_year", end)))
        if end < start:
            raise MigrationConfigError(
                "migrant_hiv fallback end year must be greater than or equal to start year."
            )
        return start, end

    def _parse_distribution(
        self,
        raw: dict,
        mapping: Dict[str, int],
        label: str,
        default: dict,
    ) -> Tuple[np.ndarray, np.ndarray]:
        values = raw or default
        idxs = []
        weights = []
        for key, raw_weight in values.items():
            if key not in mapping:
                raise MigrationConfigError(
                    f"{label} references unknown key '{key}'. "
                    f"Valid keys: {sorted(mapping)}"
                )
            weight = float(_ev(raw_weight))
            if weight < 0:
                raise MigrationConfigError(f"{label}.{key} must be non-negative.")
            if weight > 0:
                idxs.append(mapping[key])
                weights.append(weight)

        if not weights or sum(weights) <= 0:
            raise MigrationConfigError(f"{label} weights must sum to > 0.")

        weights_arr = np.array(weights, dtype=np.float64)
        weights_arr /= weights_arr.sum()
        return np.array(idxs, dtype=np.int64), weights_arr

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def apply(
        self,
        population: np.ndarray,
        childhood,
        current_year: float,
        time_system,
        rng: np.random.RandomState,
        deterministic: bool,
    ) -> int:
        """
        Apply one timestep of net migration.

        Call AFTER adult mortality, BEFORE aging.

        Returns
        -------
        int : Actual net population change (positive=added, negative=removed).
              Pass this value to validate_global_population_balance as
              migration_change so the mass-balance formula remains exact.
        """
        self.last_migrant_hiv_stats = self._empty_migrant_hiv_stats()

        if not self.enabled:
            return 0

        # Guard: if population is entirely zero (extreme stress / PSA edge case),
        # migration has nowhere to distribute people proportionally — skip gracefully.
        total_pop = int(np.sum(population)) + childhood.get_total()
        if total_pop == 0:
            warnings.warn(
                f"[MigrationModule] Population is zero at year {current_year:.3f} — "
                "skipping migration step.",
                RuntimeWarning,
                stacklevel=2,
            )
            return 0

        annual_net  = self._get_annual_net(population, childhood, current_year)
        step_net    = annual_net * time_system.years_per_step

        # Use round() first — Python 3 banker's rounding means round(0.5)=0,
        # so the guard must check the *rounded integer*, not the float directly.
        if int(round(abs(step_net))) < 1:
            return 0  # Negligible — skip to avoid wasteful integer draws

        pop_before = int(np.sum(population)) + childhood.get_total()

        if step_net > 0:
            n_add = int(round(step_net))
            migrant_hiv_requested = self._get_step_migrant_hiv_requested(
                n_add, current_year, time_system.years_per_step
            )
            migrant_hiv_applied = min(migrant_hiv_requested, n_add)
            self.last_migrant_hiv_stats["migrant_hiv_requested_entries"] = migrant_hiv_requested
            self.last_migrant_hiv_stats["migrant_hiv_entries"] = migrant_hiv_applied
            self.last_migrant_hiv_stats["migrant_hiv_capped_entries"] = migrant_hiv_applied
            self.last_migrant_hiv_stats["migrant_hiv_cap_shortfall"] = (
                migrant_hiv_requested - migrant_hiv_applied
            )
            self._apply_positive(
                n_add, population, childhood, rng, deterministic,
                migrant_hiv_count=migrant_hiv_applied,
            )
        else:
            n_remove = int(round(abs(step_net)))
            self._apply_negative(n_remove, population, childhood)

        pop_after  = int(np.sum(population)) + childhood.get_total()
        net_actual = pop_after - pop_before

        # ── Integrity: discrepancy check ──────────────────────────────────
        expected   = int(round(step_net))
        discrepancy = abs(net_actual - expected)
        # Tolerance: max(5 people, 0.1% of requested) — accounts for integer
        # rounding across many tensor cells.
        tolerance = max(5, int(0.001 * abs(step_net)) + 1)
        if discrepancy > tolerance:
            warnings.warn(
                f"[MigrationModule] Rounding discrepancy at year "
                f"{current_year:.3f}: expected {expected:+d}, "
                f"got {net_actual:+d} (diff={discrepancy}).",
                RuntimeWarning,
                stacklevel=2,
            )

        # ── Post-step integrity: no negatives ─────────────────────────────
        if np.any(population < 0):
            raise MigrationIntegrityError(
                f"Negative tensor cells after migration at year {current_year:.3f}."
            )
        if np.any(childhood.population < 0):
            raise MigrationIntegrityError(
                f"Negative childhood-pool cells after migration at year {current_year:.3f}."
            )

        return net_actual

    # ─────────────────────────────────────────────────────────────────────
    # Quantity resolution
    # ─────────────────────────────────────────────────────────────────────

    def _get_annual_net(
        self,
        population: np.ndarray,
        childhood,
        current_year: float,
    ) -> float:
        """Return annual net migration for this calendar year."""
        if self.quantity_mode == "year_specific":
            year_int = int(np.floor(current_year))
            result = self._year_values.get(year_int, None)
            if result is None:
                # ENG-F09: warn for gap years (within declared table range but missing)
                if (self._year_min < year_int < self._year_max
                        and year_int not in self._warned_gap_years):
                    warnings.warn(
                        f"[MigrationModule] year_specific table has no entry for year "
                        f"{year_int}; net migration set to 0. "
                        f"(Table range: {self._year_min}–{self._year_max})",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    self._warned_gap_years.add(year_int)
                result = 0.0
            return float(result)

        elif self.quantity_mode == "rate":
            total_pop = int(np.sum(population)) + childhood.get_total()
            return float(total_pop) * self._rate

        elif self.quantity_mode == "flat":
            return self._flat

        return 0.0  # Should never reach here after __init__ validation

    def _get_step_migrant_hiv_requested(
        self,
        positive_migrants: int,
        current_year: float,
        years_per_step: float,
    ) -> int:
        """Return requested V13.1 migrant HIV entries for this positive-migration step."""
        if not getattr(self, "_migrant_hiv_enabled", False) or positive_migrants <= 0:
            return 0

        year_int = int(np.floor(current_year))
        if self._migrant_hiv_count_mode == "rate_per_positive_migrant":
            return int(round(positive_migrants * self._migrant_hiv_rate))

        if self._migrant_hiv_count_mode == "flat":
            annual_count = self._migrant_hiv_fallback_count
        else:
            if year_int in self._migrant_hiv_year_counts:
                annual_count = self._migrant_hiv_year_counts[year_int]
            elif self._migrant_hiv_fallback_start_year <= year_int <= self._migrant_hiv_fallback_end_year:
                annual_count = self._migrant_hiv_fallback_count
            else:
                annual_count = 0.0

        return self._annual_count_to_step_count(annual_count, current_year, years_per_step)

    def _annual_count_to_step_count(
        self,
        annual_count: float,
        current_year: float,
        years_per_step: float,
    ) -> int:
        """
        Split integer annual migrant HIV source counts exactly across timesteps.

        Example for quarterly steps and annual_count=322:
        81, 81, 80, 80 rather than round(322 * 0.25)=80 every quarter.
        """
        annual_int = max(0, int(round(annual_count)))
        if annual_int == 0:
            return 0

        if years_per_step <= 0:
            return annual_int

        steps_float = 1.0 / years_per_step
        steps_per_year = int(round(steps_float))
        if steps_per_year <= 0 or not np.isclose(steps_float, steps_per_year, atol=1e-9):
            return max(0, int(round(annual_count * years_per_step)))

        year_start = np.floor(current_year)
        step_in_year = int(round((current_year - year_start) / years_per_step))
        step_in_year = min(max(step_in_year, 0), steps_per_year - 1)

        base = annual_int // steps_per_year
        remainder = annual_int % steps_per_year
        return base + (1 if step_in_year < remainder else 0)

    # ─────────────────────────────────────────────────────────────────────
    # Positive migration
    # ─────────────────────────────────────────────────────────────────────

    def _apply_positive(
        self,
        n_total: int,
        population: np.ndarray,
        childhood,
        rng: np.random.RandomState,
        deterministic: bool,
        migrant_hiv_count: int = 0,
    ) -> None:
        """Distribute n_total entrants across age-sex bins and tensor cells."""
        if n_total <= 0:
            return

        n_hiv = min(max(int(migrant_hiv_count), 0), n_total)
        if n_hiv > 0:
            self._place_migrant_hiv_entrants(n_hiv, population, rng, deterministic)

        n_susceptible = n_total - n_hiv
        if n_susceptible <= 0:
            return

        # Compute bin-level allocation {bin_label: {female: N, male: N}}
        allocation = self._build_allocation(
            n_susceptible, population, childhood, rng, deterministic
        )

        for bin_label, sex_counts in allocation.items():
            n_f = int(sex_counts["female"])
            n_m = int(sex_counts["male"])
            if n_f <= 0 and n_m <= 0:
                continue

            if bin_label in self._CHILD_BIN_LABELS:
                # Under-15 → childhood pool
                bin_idx = self._CHILD_BIN_LABELS.index(bin_label)
                childhood.population[bin_idx, self._FEMALE_SEX] += n_f
                childhood.population[bin_idx, self._MALE_SEX]   += n_m
            else:
                # 15+ → adult tensor as Susceptible/Undiag/No-ART/No-PrEP
                age_idx = self._groups.age_to_idx[bin_label]
                self._place_adult_entrants(
                    n_f, n_m, age_idx, population, rng, deterministic
                )

    def _place_adult_entrants(
        self,
        n_female: int,
        n_male: int,
        age_idx: int,
        population: np.ndarray,
        rng: np.random.RandomState,
        deterministic: bool,
    ) -> None:
        """
        Place adult entrants into population tensor.

        Epidemiological state:  Susceptible / Undiagnosed / No-ART / No-PrEP
        Orientation:            Female → Female orient
                                Male  → MSM (p_msm) or Heterosexual_Male (1-p_msm)
        Activity:               Distributed by population activity weights
        """
        s, d, a, p = self._susc_idx, 0, 0, 0  # state indices (all = 0 for baseline)

        # ── Female entrants ───────────────────────────────────────────────
        if n_female > 0:
            counts = _distribute_int(n_female, self._act_weights, rng, deterministic)
            for act_idx, cnt in enumerate(counts):
                if cnt > 0:
                    population[age_idx, self._female_idx, act_idx, s, d, a, p] += cnt

        # ── Male entrants: split MSM / Heterosexual_Male ──────────────────
        if n_male > 0:
            n_msm    = int(round(n_male * self._p_msm))
            n_hetero = n_male - n_msm

            if n_hetero > 0:
                counts = _distribute_int(n_hetero, self._act_weights, rng, deterministic)
                for act_idx, cnt in enumerate(counts):
                    if cnt > 0:
                        population[age_idx, self._hetero_idx, act_idx, s, d, a, p] += cnt

            if n_msm > 0:
                counts = _distribute_int(n_msm, self._act_weights, rng, deterministic)
                for act_idx, cnt in enumerate(counts):
                    if cnt > 0:
                        population[age_idx, self._msm_idx, act_idx, s, d, a, p] += cnt

    def _place_migrant_hiv_entrants(
        self,
        n_total: int,
        population: np.ndarray,
        rng: np.random.RandomState,
        deterministic: bool,
    ) -> None:
        """
        Place V13.1 pre-arrival diagnosed migrant HIV entries.

        These entries are already included in positive net migration. They are
        allocated only to adult tensor cells because the model has no pediatric
        HIV state.
        """
        if n_total <= 0:
            return

        orient_idxs, orient_weights = self._migrant_hiv_orient_weights
        orient_counts = _distribute_int(n_total, orient_weights, rng, deterministic)

        age_labels, age_idxs, age_weights = self._adult_migrant_hiv_age_weights(population)
        state_idxs, state_weights = self._migrant_hiv_state_weights()

        for orient_idx, orient_count in zip(orient_idxs, orient_counts):
            orient_count = int(orient_count)
            if orient_count <= 0:
                continue

            if orient_idx == self._msm_idx:
                self.last_migrant_hiv_stats["migrant_hiv_msm_entries"] += orient_count
            elif orient_idx == self._hetero_idx:
                self.last_migrant_hiv_stats["migrant_hiv_hetero_male_entries"] += orient_count
            elif orient_idx == self._female_idx:
                self.last_migrant_hiv_stats["migrant_hiv_female_entries"] += orient_count

            state_counts = _distribute_int(orient_count, state_weights, rng, deterministic)
            for (hiv_idx, diag_idx, art_idx), state_count in zip(state_idxs, state_counts):
                state_count = int(state_count)
                if state_count <= 0:
                    continue

                age_counts = _distribute_int(state_count, age_weights, rng, deterministic)
                for age_idx, age_count in zip(age_idxs, age_counts):
                    age_count = int(age_count)
                    if age_count <= 0:
                        continue

                    act_counts = _distribute_int(age_count, self._act_weights, rng, deterministic)
                    for act_idx, act_count in enumerate(act_counts):
                        act_count = int(act_count)
                        if act_count <= 0:
                            continue
                        population[
                            age_idx, int(orient_idx), act_idx,
                            int(hiv_idx), int(diag_idx), int(art_idx),
                            self._migrant_hiv_prep_idx
                        ] += act_count
                        if int(art_idx) == 1:
                            self.last_migrant_hiv_stats["migrant_hiv_on_art_entries"] += act_count

    def _adult_migrant_hiv_age_weights(
        self,
        population: np.ndarray,
    ) -> Tuple[list, np.ndarray, np.ndarray]:
        """Return adult age-bin weights for migrant HIV entries."""
        weights: list = []
        labels: list = []
        idxs: list = []

        if self.profile_mode == "custom":
            for label, age_idx in self._adult_bins:
                if label not in self._custom_weights:
                    continue
                w = self._custom_weights[label]["female"] + self._custom_weights[label]["male"]
                labels.append(label)
                idxs.append(age_idx)
                weights.append(w)
        else:
            for label, age_idx in self._adult_bins:
                if self.profile_mode == "working_age":
                    lo, hi = self._adult_bin_ages[label]
                    if hi < self._wa_min or lo > self._wa_max:
                        continue
                labels.append(label)
                idxs.append(age_idx)
                weights.append(float(np.sum(population[age_idx])))

        if not weights or sum(weights) <= 0:
            labels = [label for label, _ in self._adult_bins]
            idxs = [age_idx for _, age_idx in self._adult_bins]
            weights = [float(np.sum(population[age_idx])) for _, age_idx in self._adult_bins]

        if sum(weights) <= 0:
            weights = [1.0 for _ in idxs]

        weights_arr = np.array(weights, dtype=np.float64)
        weights_arr /= weights_arr.sum()
        return labels, np.array(idxs, dtype=np.int64), weights_arr

    def _migrant_hiv_state_weights(self) -> Tuple[list, np.ndarray]:
        """Return combined HIV stage x diagnosis x ART weights."""
        h_idxs, h_w = self._migrant_hiv_stage_weights
        d_idxs, d_w = self._migrant_hiv_diag_weights
        a_idxs, a_w = self._migrant_hiv_art_weights

        states = []
        weights = []
        for hiv_idx, hw in zip(h_idxs, h_w):
            for diag_idx, dw in zip(d_idxs, d_w):
                for art_idx, aw in zip(a_idxs, a_w):
                    states.append((int(hiv_idx), int(diag_idx), int(art_idx)))
                    weights.append(float(hw * dw * aw))

        weights_arr = np.array(weights, dtype=np.float64)
        weights_arr /= weights_arr.sum()
        return states, weights_arr

    # ─────────────────────────────────────────────────────────────────────
    # Negative migration
    # ─────────────────────────────────────────────────────────────────────

    def _apply_negative(
        self,
        n_remove: int,
        population: np.ndarray,
        childhood,
    ) -> None:
        """
        Remove n_remove people via proportional scaling with LRM add-back.

        Step 1 — Floor scaling:
            new = floor(old * scale)  for every cell.
            floor() prevents negatives but systematically over-removes because
            each cell's fractional part is silently truncated.

        Step 2 — LRM add-back (Hamilton method):
            excess = actual_removed - n_remove
            The cells with the largest fractional remainders (those that
            "lost the most" to floor) are each incremented by 1 until the
            excess is exactly cancelled.  This is the same Largest-Remainder
            Method used for positive migration in _distribute_int().

        Result: actual net removal == n_remove exactly (±0 people).

        Preserves HIV / ART / risk structure: proportional scaling keeps
        relative cell weights intact; add-back perturbs at most one person
        per top-remainder cell, which is the minimum possible disturbance.

        Safety cap: removal capped at 90 % of total population with a
        RuntimeWarning rather than an error, to allow long runs to continue
        with a diagnostic signal.
        """
        total_adult = int(np.sum(population))
        total_child = childhood.get_total()
        total_pop   = total_adult + total_child

        if total_pop == 0:
            warnings.warn(
                "[MigrationModule] Cannot remove migrants: population is zero.",
                RuntimeWarning,
                stacklevel=3,
            )
            return

        # ── Safety cap at 90 % ────────────────────────────────────────────
        max_remove = int(0.90 * total_pop)
        if n_remove > max_remove:
            warnings.warn(
                f"[MigrationModule] Negative net migration ({n_remove:,}) exceeds "
                f"90 % of total population ({total_pop:,}). "
                f"Capping removal at {max_remove:,}.",
                RuntimeWarning,
                stacklevel=3,
            )
            n_remove = max_remove

        removal_frac = n_remove / total_pop  # in [0, 0.9]
        scale        = 1.0 - removal_frac

        # ── Step 1: floor scaling ─────────────────────────────────────────
        pop_float   = population.astype(np.float64) * scale
        child_float = childhood.population.astype(np.float64) * scale

        new_pop   = np.floor(pop_float).astype(np.int64)
        new_child = np.floor(child_float).astype(np.int64)

        # Defensive clamp (should never fire with scale ∈ [0.1, 1.0])
        if np.any(new_pop < 0):
            new_pop = np.maximum(new_pop, 0)
            warnings.warn(
                "[MigrationModule] Unexpected negative cells after scaling — "
                "clamped to 0.",
                RuntimeWarning,
                stacklevel=3,
            )
        if np.any(new_child < 0):
            new_child = np.maximum(new_child, 0)
            warnings.warn(
                "[MigrationModule] Unexpected negative childhood cells after "
                "scaling — clamped to 0.",
                RuntimeWarning,
                stacklevel=3,
            )

        # ── Step 2: LRM add-back ──────────────────────────────────────────
        # Compute how many too many we removed due to floor truncation.
        actual_removed = (
            (total_adult + total_child)
            - (int(np.sum(new_pop)) + int(np.sum(new_child)))
        )
        excess = actual_removed - n_remove  # always ≥ 0 by construction of floor

        if excess > 0:
            # Fractional remainders: what each cell lost to floor().
            # Cells with larger remainders are most deserving of a +1 add-back.
            pop_rem   = pop_float   - np.floor(pop_float)    # same shape as population
            child_rem = child_float - np.floor(child_float)  # same shape as childhood

            # Flatten both arrays into a single 1-D remainder vector.
            # First n_pop_flat indices → tensor; remainder → childhood pool.
            n_pop_flat  = pop_rem.size
            combined    = np.concatenate([pop_rem.ravel(), child_rem.ravel()])

            # argsort descending: largest remainder cells first.
            top_indices = np.argsort(-combined)[:excess]

            for idx in top_indices:
                if idx < n_pop_flat:
                    new_pop.flat[idx] += 1      # .flat provides writable 1-D view
                else:
                    new_child.flat[idx - n_pop_flat] += 1

        # ── Write back ────────────────────────────────────────────────────
        np.copyto(population, new_pop)
        np.copyto(childhood.population, new_child)

    # ─────────────────────────────────────────────────────────────────────
    # Allocation helpers
    # ─────────────────────────────────────────────────────────────────────

    def _build_allocation(
        self,
        n_total: int,
        population: np.ndarray,
        childhood,
        rng: np.random.RandomState,
        deterministic: bool,
    ) -> Dict[str, Dict[str, int]]:
        """Compute per-bin integer allocation summing to n_total."""
        if self.profile_mode == "mirror":
            weights = self._mirror_weights(population, childhood)
        elif self.profile_mode == "working_age":
            weights = self._working_age_weights(population)
        else:  # custom
            weights = self._custom_weights

        return _allocate_by_weights(n_total, weights, rng, deterministic)

    def _mirror_weights(
        self, population: np.ndarray, childhood
    ) -> Dict[str, Dict[str, float]]:
        """Weights proportional to existing age-sex distribution (all bins)."""
        w: Dict[str, Dict[str, float]] = {}

        # Adult bins
        for label, age_idx in self._adult_bins:
            f = float(np.sum(population[age_idx, self._female_idx]))
            m = float(
                np.sum(population[age_idx, self._hetero_idx])
                + np.sum(population[age_idx, self._msm_idx])
            )
            w[label] = {"female": f, "male": m}

        # Childhood bins
        for bin_idx, label in enumerate(self._CHILD_BIN_LABELS):
            f = float(childhood.population[bin_idx, self._FEMALE_SEX])
            m = float(childhood.population[bin_idx, self._MALE_SEX])
            w[label] = {"female": f, "male": m}

        return _normalise_weights(w)

    def _working_age_weights(
        self, population: np.ndarray
    ) -> Dict[str, Dict[str, float]]:
        """Weights from adult bins overlapping [wa_min, wa_max]. No children."""
        w: Dict[str, Dict[str, float]] = {}

        for label, age_idx in self._adult_bins:
            lo, hi = self._adult_bin_ages[label]
            # Include if the bin overlaps [wa_min, wa_max]
            if hi < self._wa_min or lo > self._wa_max:
                continue
            f = float(np.sum(population[age_idx, self._female_idx]))
            m = float(
                np.sum(population[age_idx, self._hetero_idx])
                + np.sum(population[age_idx, self._msm_idx])
            )
            w[label] = {"female": f, "male": m}

        total = sum(v["female"] + v["male"] for v in w.values())
        if total == 0:
            warnings.warn(
                f"[MigrationModule] Working-age profile "
                f"[{self._wa_min}–{self._wa_max}] has zero resident population. "
                "Falling back to mirror profile.",
                RuntimeWarning,
                stacklevel=4,
            )
            # Fall back: mirror over all adult bins only
            w = {}
            for label, age_idx in self._adult_bins:
                f = float(np.sum(population[age_idx, self._female_idx]))
                m = float(
                    np.sum(population[age_idx, self._hetero_idx])
                    + np.sum(population[age_idx, self._msm_idx])
                )
                w[label] = {"female": f, "male": m}

        return _normalise_weights(w)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

# _ev is imported from config_utils (aliased as _ev for brevity)


def _parse_and_normalise_custom_weights(
    raw: dict,
    valid_bins: set,
) -> Dict[str, Dict[str, float]]:
    """
    Parse and normalise user-supplied custom weights.

    Accepts format::

        {
            "20-24": {"female": 0.10, "male": 0.12},
            "25-29": {"female": 0.15, "male": 0.18},
            ...
        }

    ``valid_bins`` should be the full set of recognised bin labels
    (childhood + all adult bins from the groups config).

    Raises MigrationConfigError on unknown bin or negative weight.
    """
    w: Dict[str, Dict[str, float]] = {}
    for bin_label, sv in raw.items():
        if bin_label not in valid_bins:
            raise MigrationConfigError(
                f"Custom weight references unknown age bin '{bin_label}'. "
                f"Valid bins: {sorted(valid_bins)}"
            )
        f = float(_ev(sv.get("female", 0.0)))
        m = float(_ev(sv.get("male",   0.0)))
        if f < 0 or m < 0:
            raise MigrationConfigError(
                f"Custom weights must be non-negative (bin '{bin_label}': "
                f"female={f}, male={m})."
            )
        w[bin_label] = {"female": f, "male": m}

    return _normalise_weights(w)


def _normalise_weights(
    w: Dict[str, Dict[str, float]],
) -> Dict[str, Dict[str, float]]:
    """Normalise so all female+male values across bins sum to 1.0."""
    total = sum(v["female"] + v["male"] for v in w.values())
    if total <= 0:
        raise MigrationConfigError(
            "Cannot normalise weights: all age-sex bins sum to zero."
        )
    return {
        k: {"female": v["female"] / total, "male": v["male"] / total}
        for k, v in w.items()
    }


def _distribute_int(
    n: int,
    weights: np.ndarray,
    rng: np.random.RandomState,
    deterministic: bool,
) -> np.ndarray:
    """
    Distribute n people among len(weights) bins, summing exactly to n.

    Deterministic : largest-remainder method (no randomness, exact sum).
    Stochastic    : multinomial draw (expected sum = n, actual = n).
    """
    if n <= 0:
        return np.zeros(len(weights), dtype=np.int64)

    if deterministic:
        floats  = n * weights
        floors  = np.floor(floats).astype(np.int64)
        deficit = n - int(floors.sum())
        if deficit > 0:
            # Add 1 to the bins with the largest fractional remainders
            fracs      = floats - floors
            top_idxs   = np.argsort(-fracs)[:deficit]
            floors[top_idxs] += 1
        return floors
    else:
        return rng.multinomial(n, weights).astype(np.int64)


def _allocate_by_weights(
    n_total: int,
    weights: Dict[str, Dict[str, float]],
    rng: np.random.RandomState,
    deterministic: bool,
) -> Dict[str, Dict[str, int]]:
    """
    Allocate n_total people to (bin_label, sex) slots using weight dict.

    Guarantees: sum of all allocated counts == n_total exactly.
    """
    # Flatten to ordered (bin_label, sex) list and weight array
    keys:         list  = []
    flat_weights: list  = []

    for bin_label, sv in weights.items():
        keys.append((bin_label, "female"))
        flat_weights.append(sv["female"])
        keys.append((bin_label, "male"))
        flat_weights.append(sv["male"])

    flat_w = np.array(flat_weights, dtype=np.float64)
    total  = flat_w.sum()
    if total <= 0:
        raise MigrationConfigError("Allocation weights sum to zero.")
    flat_w /= total

    counts = _distribute_int(n_total, flat_w, rng, deterministic)

    allocation: Dict[str, Dict[str, int]] = {}
    for (bin_label, sex), cnt in zip(keys, counts):
        if bin_label not in allocation:
            allocation[bin_label] = {"female": 0, "male": 0}
        allocation[bin_label][sex] = int(cnt)

    return allocation
