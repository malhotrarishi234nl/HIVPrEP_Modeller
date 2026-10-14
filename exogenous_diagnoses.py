"""
exogenous_diagnoses.py — C-1 exogenous non-MSM diagnosis forcing (D-03)

WHAT THIS IS
------------
The observed SHM non-MSM diagnosis series is fed into the model directly as an
exogenous forcing function. People entering through it join the PLHIV stock and
consume care costs; they are **not** produced by the transmission model.

WHAT THIS IS NOT
----------------
This is **not migrant seeding**. There is no migrant/domestic decomposition, no
import-volume parameter and no invented pre-2018 quantity. The design consumes
the *total* observed non-MSM diagnosis count, which is exactly why it estimates
nothing unobservable. The separate ``demography.migration.migrant_hiv``
mechanism (V13.1) is a different thing and stays where it is: it re-labels a
subset of positive net migration, it does not add population, and it is sourced
from a backcast average for 2018-2021. C-1 deliberately does not use it.

WHY IT IS A SEPARATE MODULE
---------------------------
``migration.py`` states in its own header that it is a "demographic correction
layer only; NOT an epidemiological driver". C-1 *is* an epidemiological driver:
it puts people into the PLHIV stock and onto the diagnosis counter. Bolting it
onto the migration module would break that module's stated contract and bury an
epidemiological assumption inside demography. Keeping it separate also keeps the
"generates no endogenous transmission" property auditable in one place.

FREE PARAMETERS
---------------
None. Every number this module uses is observed:
  - the annual counts are SHM Table 1.1 as published;
  - the male/female split is the published split for that same year, so the
    orientation allocation is read off the data rather than estimated.
The only *choices* are the two rules below, and both are explicit, switchable
and reported.

PRE-1996 (the ``<=1995`` lump)
------------------------------
SHM publishes 1,280 non-MSM diagnoses (719 "other men" + 561 women) as a single
``<=1995`` bucket. It is cohort-reconstructed and under-ascertains people who
died pre-ART or never entered care, so its annual shape is unknown and its level
is a floor rather than an estimate. Default rule is ``burn_in`` — the lump is
NOT distributed and NOT injected. NOTE: the pre-1996 window is NOT unscored, contrary
to an earlier version of this docstring. ``ecdc_hiv_confirmed_cases`` is
``calibration_restricted`` with ``restricted_years: [1990, 1995]`` and scores
``new_diagnoses`` — and it binds ~97% of prototype particles. That is precisely WHY the
lump must not be distributed: doing so would relieve the single most binding target by
construction. This
is the conservative choice: inventing an annual shape would manufacture exactly
the kind of unobservable quantity the design forbids. ``uniform`` is available
as a stated alternative for sensitivity analysis. **Do not fit to it.**

POST-2024 (C-3)
---------------
The observed series stops in 2024, so a rule has to govern the analytic horizon.
  freeze         — hold the mean of the last ``freeze_window`` observed years.
                   Base case.
  trend          — continue the log-linear trend fitted over a stated window.
                   Alternative only: the 2004-2020 decline REVERSED after 2020,
                   so projecting it forward is a judgement call, not an
                   extrapolation of a stable process.
  net_migration  — scale the frozen level by projected net migration relative to
                   its own base-window mean.
  recent_slope   — continue the linear slope of the post-COVID recovery, capped
                   at ``slope_cap_multiple`` x the frozen level.

WHY THERE ARE FOUR RULES AND NOT THREE
--------------------------------------
The brief specified three. Three is not enough, because all three are flat or
falling and the data's own recent signal rises: OLS on the 2020-2024 non-MSM
series gives **+10.30/yr (SE 3.71, t = +2.78)**, verified against the workbook
on 2026-09-14. A sensitivity set whose every member sits at or below the base
case is one-sided, and a one-sided sensitivity set understates the uncertainty
it exists to display. ``recent_slope`` is the upper member. It is an alternative,
not the base case: five points spanning a COVID collapse and its rebound do not
establish a durable trend either.

WHY THE FREEZE WINDOW IS NOT 2020-2024
--------------------------------------
The brief justified the base case with "2020-24 are flat (168 / 171 / 198 / 212 /
199), so this needs no trend assumption". That premise is false. Those five years
are a COVID trough and a recovery, not a plateau (slope above), and their mean
(189.6) is a level no year in the series actually represents -- it sits below
both the pre-COVID level (2018 = 214, 2019 = 251) and the 2023 value (212).
Averaging a dip with its own rebound manufactures a spurious "recent level".

The default window is therefore the **recovery** years 2022-2024 (mean 203.0),
which excludes the two COVID-suppressed testing years. Note the residual caveat,
which no window choice removes: SHM's reporting-delay adjustment on 2024 is
**+22.1%** (163 reported -> 199 used), ramping 0.0 / 0.4 / 1.2 / 3.0 / 4.8 / 9.3
/ 22.1% across 2018-2024, so the most recent point is also the most provisional.
Set ``freeze_window`` to 5 to recover the brief's original anchor.
All three are runnable and the spread across them is a reported result, not a
hidden assumption. Note the structural argument: an exogenous stream that is
identical in both arms cancels in the *incremental* analysis. That argument is
independent of any computed number, but it is an argument about the incremental
result only, and it is not a licence to skip reporting the spread.
"""

from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Tuple

import numpy as np

from config_utils import extract_value as _ev


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class ExogenousDiagnosisConfigError(ValueError):
    """Raised for an invalid exogenous non-MSM forcing configuration."""


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

VALID_PRE_START_RULES: frozenset = frozenset({"burn_in", "uniform"})
VALID_POST_END_RULES: frozenset = frozenset(
    {"freeze", "trend", "net_migration", "recent_slope"}
)

# Orientation labels this mechanism may allocate to. MSM is deliberately absent:
# the whole point of C-1 is that the MSM epidemic stays endogenous.
_MALE_ORIENTATION = "Heterosexual_Male"
_FEMALE_ORIENTATION = "Female"


class ExogenousNonMsmForcing:
    """
    Annual exogenous non-MSM diagnosis counts, with an explicit rule at each end
    of the observed window.

    The class is pure: it answers "how many, and split how" for a given year. It
    does not touch the population tensor. Placement lives with the caller so that
    the tensor write and the diagnosis-counter write happen together and cannot
    drift apart.
    """

    def __init__(self, config: Optional[Mapping] = None):
        self.enabled = False
        self.counts: Dict[int, int] = {}
        self.male_share: Dict[int, float] = {}
        self.pre_start_rule = "burn_in"
        self.post_end_rule = "freeze"
        self.freeze_window = 5
        self.trend_window: Tuple[int, int] = (2004, 2020)
        self.slope_window: Tuple[int, int] = (2020, 2024)
        self.slope_cap_multiple = 3.0
        self.pre_start_lump = 0
        self.pre_start_span: Tuple[int, int] = (1981, 1995)
        self.net_migration_series: Dict[int, float] = {}
        self.net_migration_base_window: Tuple[int, int] = (2020, 2024)

        if config is None:
            return

        block = self._locate_block(config)
        if block is None:
            return

        self.enabled = bool(_ev(block.get("enabled", {"value": False})))
        if not self.enabled:
            return

        self._load_counts(block)
        self._load_rules(block)
        self._validate()

    # ── configuration ────────────────────────────────────────────────────────

    @staticmethod
    def _locate_block(config: Mapping) -> Optional[Mapping]:
        """Find the config block under either the raw or ConfigReader shape."""
        for path in (
            ("disease", "exogenous_non_msm"),
            ("transmission_model", "disease", "exogenous_non_msm"),
        ):
            node: object = config
            for key in path:
                if not isinstance(node, Mapping) or key not in node:
                    node = None
                    break
                node = node[key]
            if isinstance(node, Mapping):
                return node
        return None

    def _load_counts(self, block: Mapping) -> None:
        raw_counts = _ev(block.get("annual_counts", {"value": {}})) or {}
        raw_shares = _ev(block.get("annual_male_share", {"value": {}})) or {}

        for year, value in raw_counts.items():
            iy = int(year)
            count = int(round(float(value)))
            if count < 0:
                raise ExogenousDiagnosisConfigError(
                    f"exogenous_non_msm.annual_counts[{year}] is negative ({count}); "
                    "a diagnosis count cannot be negative."
                )
            self.counts[iy] = count

        for year, value in raw_shares.items():
            share = float(value)
            if not 0.0 <= share <= 1.0:
                raise ExogenousDiagnosisConfigError(
                    f"exogenous_non_msm.annual_male_share[{year}] is {share}; "
                    "a share must lie in [0, 1]."
                )
            self.male_share[int(year)] = share

    def _load_rules(self, block: Mapping) -> None:
        self.pre_start_rule = str(_ev(block.get("pre_start_rule", {"value": "burn_in"})))
        self.post_end_rule = str(_ev(block.get("post_end_rule", {"value": "freeze"})))
        self.freeze_window = int(_ev(block.get("freeze_window", {"value": 5})))
        self.pre_start_lump = int(_ev(block.get("pre_start_lump", {"value": 0})))

        span = _ev(block.get("pre_start_span", {"value": {}})) or {}
        if span:
            self.pre_start_span = (int(span["start"]), int(span["end"]))

        window = _ev(block.get("trend_window", {"value": {}})) or {}
        if window:
            self.trend_window = (int(window["start"]), int(window["end"]))

        slope_window = _ev(block.get("slope_window", {"value": {}})) or {}
        if slope_window:
            self.slope_window = (int(slope_window["start"]), int(slope_window["end"]))
        self.slope_cap_multiple = float(
            _ev(block.get("slope_cap_multiple", {"value": 3.0}))
        )

        migration = _ev(block.get("net_migration_series", {"value": {}})) or {}
        self.net_migration_series = {int(y): float(v) for y, v in migration.items()}

        base = _ev(block.get("net_migration_base_window", {"value": {}})) or {}
        if base:
            self.net_migration_base_window = (int(base["start"]), int(base["end"]))

    def _validate(self) -> None:
        if self.pre_start_rule not in VALID_PRE_START_RULES:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.pre_start_rule must be one of "
                f"{sorted(VALID_PRE_START_RULES)}, got {self.pre_start_rule!r}."
            )
        if self.post_end_rule not in VALID_POST_END_RULES:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.post_end_rule must be one of "
                f"{sorted(VALID_POST_END_RULES)}, got {self.post_end_rule!r}."
            )
        if not self.counts:
            raise ExogenousDiagnosisConfigError(
                "exogenous_non_msm is enabled but annual_counts is empty. The "
                "mechanism exists to feed an observed series in; there is nothing "
                "to feed."
            )
        if self.freeze_window < 1:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.freeze_window must be >= 1, got {self.freeze_window}."
            )
        if self.pre_start_rule == "uniform" and self.pre_start_lump <= 0:
            raise ExogenousDiagnosisConfigError(
                "exogenous_non_msm.pre_start_rule is 'uniform' but pre_start_lump "
                "is not positive; there is no lump to distribute."
            )
        if self.post_end_rule == "net_migration" and not self.net_migration_series:
            raise ExogenousDiagnosisConfigError(
                "exogenous_non_msm.post_end_rule is 'net_migration' but "
                "net_migration_series is empty; the rule is not runnable."
            )
        lo, hi = self.pre_start_span
        if lo > hi:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.pre_start_span is inverted: {lo} > {hi}."
            )
        start, end = self.trend_window
        if start >= end:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.trend_window must span at least two years, "
                f"got {start}-{end}."
            )
        s_start, s_end = self.slope_window
        if s_start >= s_end:
            raise ExogenousDiagnosisConfigError(
                f"exogenous_non_msm.slope_window must span at least two years, "
                f"got {s_start}-{s_end}."
            )
        if self.slope_cap_multiple <= 0:
            raise ExogenousDiagnosisConfigError(
                "exogenous_non_msm.slope_cap_multiple must be positive, got "
                f"{self.slope_cap_multiple}."
            )

    # ── observed window ──────────────────────────────────────────────────────

    @property
    def first_observed_year(self) -> int:
        return min(self.counts)

    @property
    def last_observed_year(self) -> int:
        return max(self.counts)

    # ── the answer ───────────────────────────────────────────────────────────

    def count_for_year(self, year: float) -> int:
        """Exogenous non-MSM diagnoses to inject for the given calendar year."""
        if not self.enabled:
            return 0

        iy = int(math.floor(float(year)))

        if iy in self.counts:
            return self.counts[iy]
        if iy < self.first_observed_year:
            return self._pre_start_count(iy)
        return self._post_end_count(iy)

    def _pre_start_count(self, year: int) -> int:
        if self.pre_start_rule == "burn_in":
            # The lump is not distributed and not injected. Its annual shape is
            # unknown, so inventing one would manufacture an unobservable.
            return 0

        lo, hi = self.pre_start_span
        if not lo <= year <= hi:
            return 0
        n_years = hi - lo + 1
        base, remainder = divmod(self.pre_start_lump, n_years)
        # Deterministic remainder placement: the leftover goes to the latest
        # years, where ascertainment is least bad.
        return base + (1 if year > hi - remainder else 0)

    def _post_end_count(self, year: int) -> int:
        last = self.last_observed_year

        if self.post_end_rule == "freeze":
            return self._frozen_level()

        if self.post_end_rule == "trend":
            return self._trend_level(year)

        if self.post_end_rule == "recent_slope":
            return self._recent_slope_level(year)

        # net_migration
        frozen = self._frozen_level()
        base_lo, base_hi = self.net_migration_base_window
        base_values = [
            v for y, v in self.net_migration_series.items() if base_lo <= y <= base_hi
        ]
        if not base_values:
            return frozen
        base_mean = float(np.mean(base_values))
        if base_mean == 0:
            return frozen
        projected = self.net_migration_series.get(year)
        if projected is None:
            # Beyond the projection series, hold its final value.
            future = [y for y in self.net_migration_series if y > last]
            if not future:
                return frozen
            projected = self.net_migration_series[max(future)]
        scaled = frozen * (float(projected) / base_mean)
        return max(0, int(round(scaled)))

    def _frozen_level(self) -> int:
        """Mean of the last ``freeze_window`` observed years."""
        years = sorted(self.counts)[-self.freeze_window:]
        return int(round(float(np.mean([self.counts[y] for y in years]))))

    def _recent_slope_level(self, year: int) -> int:
        """
        Continue the linear slope fitted over ``slope_window``, capped.

        The cap is not a tuning knob. An unbounded linear extrapolation over a
        40-year horizon reaches levels the Dutch non-MSM epidemic has never
        approached, from a slope fitted on five points. The cap states that this
        rule is an upper alternative, not a forecast.
        """
        lo, hi = self.slope_window
        pairs = [(y, self.counts[y]) for y in sorted(self.counts) if lo <= y <= hi]
        if len(pairs) < 2:
            return self._frozen_level()
        xs = np.array([y for y, _ in pairs], dtype=float)
        ys = np.array([c for _, c in pairs], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        value = intercept + slope * year
        ceiling = self.slope_cap_multiple * self._frozen_level()
        return int(round(float(np.clip(value, 0.0, ceiling))))

    def _trend_level(self, year: int) -> int:
        """Log-linear trend fitted over ``trend_window``, evaluated at ``year``."""
        lo, hi = self.trend_window
        pairs = [(y, self.counts[y]) for y in sorted(self.counts) if lo <= y <= hi]
        pairs = [(y, c) for y, c in pairs if c > 0]
        if len(pairs) < 2:
            return self._frozen_level()
        xs = np.array([y for y, _ in pairs], dtype=float)
        ys = np.log(np.array([c for _, c in pairs], dtype=float))
        slope, intercept = np.polyfit(xs, ys, 1)
        value = math.exp(intercept + slope * year)
        return max(0, int(round(value)))

    # ── allocation ───────────────────────────────────────────────────────────

    def male_share_for_year(self, year: float) -> float:
        """
        Observed male share of non-MSM diagnoses for the year.

        Inside the observed window this is read off the published split. Outside
        it, the mean of the observed window is carried forward — the split is far
        more stable than the level, and carrying it is weaker than assuming a
        trend in it.
        """
        if not self.male_share:
            return 0.5
        iy = int(math.floor(float(year)))
        if iy in self.male_share:
            return self.male_share[iy]
        return float(np.mean(list(self.male_share.values())))

    def count_for_step(self, year: float, years_per_step: float) -> int:
        """
        Split the annual count exactly across the timesteps of that year.

        Mirrors ``migration._count_for_step``: quarterly steps on an annual count
        of 199 give 50/50/50/49, not round(199 * 0.25) four times. The annual
        total is conserved exactly, which is what makes "reproduces the observed
        series by construction" literally true rather than approximately true.
        """
        annual = self.count_for_year(year)
        if annual <= 0:
            return 0
        if years_per_step <= 0:
            return annual

        steps_float = 1.0 / years_per_step
        steps_per_year = int(round(steps_float))
        if steps_per_year <= 0 or not np.isclose(steps_float, steps_per_year, atol=1e-9):
            return max(0, int(round(annual * years_per_step)))

        step_in_year = int(round((float(year) - math.floor(float(year))) / years_per_step))
        step_in_year = min(max(step_in_year, 0), steps_per_year - 1)

        base, remainder = divmod(annual, steps_per_year)
        return base + (1 if step_in_year < remainder else 0)

    def split_for_step(self, year: float, years_per_step: float) -> Dict[str, int]:
        """Orientation split of this timestep's exogenous count."""
        total = self.count_for_step(year, years_per_step)
        return self._split_total(total, year)

    def split_for_year(self, year: float) -> Dict[str, int]:
        """
        Exogenous diagnoses for the year, split into the two non-MSM orientation
        strata. Integer-preserving: the two parts always sum to
        ``count_for_year(year)``.
        """
        return self._split_total(self.count_for_year(year), year)

    def _split_total(self, total: int, year: float) -> Dict[str, int]:
        if total <= 0:
            return {_MALE_ORIENTATION: 0, _FEMALE_ORIENTATION: 0}
        share = self.male_share_for_year(year)
        males = int(round(total * share))
        males = max(0, min(total, males))
        return {_MALE_ORIENTATION: males, _FEMALE_ORIENTATION: total - males}
