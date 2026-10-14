"""
economics.py — EconomicsAccumulator for HTA evaluation in HIV transmission model V11.

V11.8.0: Removed legacy flat-format support and hta_config_compat shim (BACKLOG Finding 3).
         Only v2 JSON format (_format_version: v2) is accepted. Passing old flat-format
         configs raises ValueError at construction.

V11.6.1: Full rewrite of accumulate() to read v2 JSON structure directly.
         Adds new_diagnoses counter, arm_name field, cohort-aware prevention costs,
         billing_moment routing for disease costs, and dynamic cost breakdown categories.

BACKLOG-20 (societal perspective): Added _compute_productivity_losses() and
         _compute_patient_time_costs(). Both are called every step when the
         hta_config["perspective"] block is present. Their discounted totals
         accumulate in _cumul_societal_additions_disc alongside (not replacing)
         the existing payer-only cost accumulators. get_summary() returns three
         new keys: total_societal_additions_disc, total_costs_disc_societal,
         societal_breakdown_disc. ScenarioComparator selects which total to use
         for ICER computation via its own perspective flag.

Key features:
  - v2 JSON format read directly in accumulate()
  - Billing moment routing: ongoing / initiation / diagnosis
  - Cohort-aware prevention costs: new_initiators / continuing / all
  - Dynamic cost breakdown categories driven by JSON 'category' field
  - new_diagnoses counter for one-time linkage-to-care cost accuracy
  - arm_name field for scenario identification (no_prep / oral_prep / lenacapavir)
  - Per-step accumulation with ZIN-guideline discount factors (1.5% health, 4.0% cost)
  - Societal perspective: productivity losses (friction cost) + patient time costs

Architecture:
  The accumulator reads the raw population tensor at each step and computes:
    1. Person-time in each health state → QALY contribution
    2. Prevention costs by arm (shared + arm-specific, cohort-resolved)
    3. Disease costs by stage/ART status (ongoing) and new diagnoses (one-time)
    4. Adverse event adjustments (via AE module)
    5. Societal additions: productivity losses + patient time costs (BACKLOG-20)
    6. Discount factor application and breakdown tracking

  Called by SimulationEngineV3.step_simulation() after record_outputs().
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from config_utils import extract_value, require_idx, validate_hta_config


# ---------------------------------------------------------------------------
# Arm-name ↔ PrEP-state-name mapping
# ---------------------------------------------------------------------------
_ARM_TO_PREP_STATE: Dict[str, str] = {
    "oral_prep":            "Oral_PrEP",
    "lenacapavir":          "Lenacapavir",
    "oral_prep_plus_lena":  None,   # combination — handled by explicit per-PrEP-state logic below
}

_PREP_STATE_TO_UTILITY_KEY: Dict[str, str] = {
    "Oral_PrEP":          "susceptible_oral_prep",
    "Lenacapavir":        "susceptible_lenacapavir",
    # V12.2: Waning users retain lena QALY weight — same pharmacological protection,
    # same perceived health state (still covered by subcutaneous depot).
    "Lenacapavir_Waning": "susceptible_lenacapavir",
}

# A-1 diagnostic contract.  These keys partition every QALY contribution made
# by ``EconomicsAccumulator``.  Positive entries are health-state person-time;
# utility decrements are stored as negative contributions so the component sum
# reconciles directly to ``step_qalys_*`` and ``total_qalys_*``.
QALY_COMPONENT_KEYS: Tuple[str, ...] = (
    "susceptible_no_prep",
    "susceptible_oral_prep",
    "susceptible_lenacapavir",
    "susceptible_lenacapavir_waning",
    "hiv_acute",
    "hiv_chronic_on_art",
    "hiv_chronic_off_art",
    "hiv_aids_on_art",
    "hiv_aids_off_art",
    "utility_decrement_art_initiation",
    "utility_decrement_adverse_events",
)

_PREP_STATE_TO_QALY_COMPONENT: Dict[str, str] = {
    "Oral_PrEP": "susceptible_oral_prep",
    "Lenacapavir": "susceptible_lenacapavir",
    "Lenacapavir_Waning": "susceptible_lenacapavir_waning",
}

_ART_STATUS_TO_IDX: Dict[str, int] = {
    "on_art":  1,
    "off_art": 0,
}


@dataclass
class EconomicsAccumulator:
    """
    Accumulates QALYs and costs over the simulation horizon.

    Attributes:
        hta_config:    Dictionary with HTA parameters. Accepts both v2 and legacy flat format.
        groups:        PopulationGroups with hiv_to_idx, prep_to_idx, prep_states, etc.
        freeze_year:   Base year for discounting (e.g., 2025.0).
        step_fraction: Fraction of a year per timestep (e.g., 0.25 for quarterly).
                       Required — must be derived from engine (arm_engine.time.years_per_step).
                       Must be in (0, 1]. V11.7.2: replaces hardcoded 0.25.
        arm_name:      Scenario arm identifier matching prevention_costs.arm_specific keys
                       (e.g., 'no_prep', 'oral_prep', 'lenacapavir'). Default: 'no_prep'.
    """

    hta_config:    dict
    groups:        Any
    freeze_year:   float
    step_fraction: float
    arm_name:      str   = "no_prep"

    # Internal accumulators (not passed by caller)
    _timesteps:           List[Dict]       = field(default_factory=list)
    _cumul_qalys_disc:    float            = 0.0
    _cumul_qalys_undisc:  float            = 0.0
    _cumul_costs_disc:    float            = 0.0
    _cumul_costs_undisc:  float            = 0.0
    _cost_breakdown_disc: Dict[str, float] = field(default_factory=dict)
    _qaly_component_breakdown_disc: Dict[str, float] = field(default_factory=dict)
    _qaly_component_breakdown_undisc: Dict[str, float] = field(default_factory=dict)
    _last_step_qaly_components: Dict[str, float] = field(default_factory=dict)
    _prev_prep_users:     Dict[str, int]   = field(default_factory=dict)
    _ae_module:           Optional[Any]    = None
    _v2_config:           Optional[dict]   = None  # always set to hta_config after __post_init__
    _last_step_utility_decrement: float    = 0.0

    # ── Societal perspective accumulators (BACKLOG-20) ────────────────────────
    # Tracks non-payer societal costs (productivity losses + patient time).
    # Stored separately so payer total is always available for secondary runs.
    # Keys in _societal_breakdown_disc: "productivity_loss", "patient_time".
    _cumul_societal_additions_disc:   float            = 0.0
    _cumul_societal_additions_undisc: float            = 0.0
    _societal_breakdown_disc:         Dict[str, float] = field(default_factory=dict)
    # Cached perspective config and working-age age-dim index list (set in __post_init__)
    _perspective_cfg:                 Optional[dict]   = None
    _working_age_indices:             Optional[List[int]] = None

    def __post_init__(self):
        """Validate v2 format and prepare internal config references."""
        # Validate step_fraction (V11.7.2)
        if not (0.0 < self.step_fraction <= 1.0):
            raise ValueError(
                f"EconomicsAccumulator: step_fraction must be in (0, 1], "
                f"got {self.step_fraction!r}. Expected 0.25 for a quarterly timestep."
            )

        # V11.8.0: only v2 format accepted; old flat format raises immediately.
        if not isinstance(self.hta_config, dict) or \
                self.hta_config.get("_format_version") != "v2":
            raise ValueError(
                "EconomicsAccumulator requires hta_config in v2 format "
                "(_format_version: 'v2'). Old flat-format configs (V11.5.x and earlier) "
                "are no longer supported. Migrate your config to the v2 structure."
            )

        validate_hta_config(self.hta_config)
        self._v2_config = self.hta_config

        # Ensure cost_breakdown_disc starts empty (dynamically populated)
        if not self._cost_breakdown_disc:
            self._cost_breakdown_disc = {}

        # ── Societal perspective setup (BACKLOG-20) ───────────────────────────
        # Cache the perspective sub-config (None when block absent → no-op).
        self._perspective_cfg = self.hta_config.get("perspective", None)

        # Pre-compute working-age age-dimension index list from groups.age_groups.
        # groups.age_groups is a list of name strings e.g. ["15-19", "20-24", ...].
        # Filter: lower-bound of bin >= working_age_min AND < working_age_max.
        # This is done once at init to avoid re-parsing strings on every step.
        if self._perspective_cfg is not None:
            prod_cfg  = self._perspective_cfg.get("productivity_loss", {})
            wa_min    = prod_cfg.get("working_age_min", 15)
            wa_max    = prod_cfg.get("working_age_max", 65)
            wa_indices: List[int] = []
            for idx, age_group_name in enumerate(self.groups.age_groups):
                # Age group names are strings: "15-19", "20-24", ..., "110+"
                # Extract lower bound of the 5-year bin.
                try:
                    lo = int(age_group_name.split("-")[0].replace("+", ""))
                except (ValueError, AttributeError):
                    continue  # skip unparseable entries
                if wa_min <= lo < wa_max:
                    wa_indices.append(idx)
            self._working_age_indices = wa_indices if wa_indices else None
        else:
            self._working_age_indices = None

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def set_ae_module(self, ae_module: Optional[Any]) -> None:
        """
        Attach an AdverseEventModule for Phase 3 AE cost/utility adjustments.

        Args:
            ae_module: Object with compute_adjustments(population, step, year) -> dict
                       returning 'utility_decrement' (positive = QALY loss) and 'cost_addition'.
        """
        self._ae_module = ae_module

    def accumulate(
        self,
        step:                   int,
        year:                   float,
        population:             np.ndarray,
        new_infections:         int,
        new_diagnoses:          int = 0,
        new_aids_deaths:        int = 0,
        new_chronic:            int = 0,
        new_aids:               int = 0,
        new_infections_by_age:  Optional[np.ndarray] = None,
        new_chronic_by_age:     Optional[np.ndarray] = None,
        new_aids_by_age:        Optional[np.ndarray] = None,
        prep_initiations_by_arm: Optional[Dict[str, int]] = None,
        art_initiations_by_stage: Optional[Dict[str, int]] = None,
    ) -> None:
        """
        Accumulate QALYs and costs for one timestep.

        Called after record_outputs() in SimulationEngineV3.step_simulation().

        Args:
            step:             Timestep index (0-based).
            year:             Current year (float, e.g., 2025.25 for Q2 2025).
            population:       Full population tensor (age × orient × activity × hiv ×
                              diagnosis × art × prep).
            new_infections:   Count of new infections this step (recorded for output).
                              Also used as Susceptible→Acute incident count for friction
                              cost productivity losses (V11.11.9).
            new_diagnoses:    Count of newly diagnosed HIV+ individuals this step (V11.6.1).
                              Used to fire one-time linkage-to-care costs accurately.
            new_aids_deaths:  Count of AIDS-stage deaths this step (V11.7.3).
                              Used to fire terminal care costs (billing_moment="death").
                              Default 0 — no terminal care cost if not provided.
            new_chronic:      Acute→Chronic transitions this step (V11.11.9).
                              Used by friction cost productivity loss method. Default 0.
            new_aids:         Chronic→AIDS transitions this step (V11.11.9).
                              Used by friction cost productivity loss method. Default 0.
            new_infections_by_age: Age-stratified new infection counts, shape (n_age_groups,).
                              When provided with the other *_by_age args and
                              _working_age_indices is set, replaces the prevalence-based
                              wa_frac proxy in the friction cost method (V12.1 BACKLOG-21).
            new_chronic_by_age:   Age-stratified Acute→Chronic counts, shape (n_age_groups,).
            new_aids_by_age:      Age-stratified Chronic→AIDS counts, shape (n_age_groups,).
            prep_initiations_by_arm: Gross Susceptible No_PrEP→PrEP initiations this step,
                              keyed by HTA arm name. Required when initiation-billed
                              prevention costs or adverse events are configured.
            art_initiations_by_stage: Gross diagnosed off-ART→on-ART initiations this
                              step, keyed by HIV stage name. Required when disease_costs
                              use ART-initiation cohorts (V12.18).
        """
        step_fraction = self.step_fraction  # V11.7.2: dynamic from engine, replaces hardcoded 0.25

        # ── Discount rates ─────────────────────────────────────────────────
        # V11.7.2: no silent fallbacks — missing keys raise immediately.
        # Validated at pipeline start by _validate_hta_config() in run_scenarios.py.
        disc_cfg = self._v2_config["discounting"]
        if "health_effects_rate" not in disc_cfg:
            raise KeyError(
                "hta_config missing required key: discounting.health_effects_rate. "
                "Expected per ZIN guidelines: 0.015 (1.5% per year)."
            )
        if "cost_rate" not in disc_cfg:
            raise KeyError(
                "hta_config missing required key: discounting.cost_rate. "
                "Expected per ZIN guidelines: 0.04 (4.0% per year)."
            )
        r_health = extract_value(disc_cfg["health_effects_rate"])
        r_cost   = extract_value(disc_cfg["cost_rate"])

        years_since_freeze = year - self.freeze_year
        discount_health = 1.0 / ((1.0 + r_health) ** years_since_freeze)
        discount_cost   = 1.0 / ((1.0 + r_cost)   ** years_since_freeze)

        # ── Compute step contributions ─────────────────────────────────────
        step_qalys, step_costs, step_breakdown = self._compute_step_v2(
            population, step, year, step_fraction, new_diagnoses, new_aids_deaths,
            prep_initiations_by_arm=prep_initiations_by_arm,
            art_initiations_by_stage=art_initiations_by_stage,
        )

        # ── Adverse event adjustments (Phase 3 module) ─────────────────────
        if self._ae_module is not None:
            ae_adj = self._ae_module.compute_adjustments(
                population, step, year,
                prep_initiations_by_arm=prep_initiations_by_arm
            )
            ae_util = ae_adj.get("utility_decrement", 0.0)
            ae_cost = ae_adj.get("cost_addition", 0.0)
            # utility_decrement is a positive quantity representing QALY loss
            step_qalys -= ae_util
            self._last_step_qaly_components[
                "utility_decrement_adverse_events"
            ] = -ae_util
            step_costs += ae_cost
            step_breakdown["adverse_events"] = (
                step_breakdown.get("adverse_events", 0.0) + ae_cost
            )

        # ── Apply discount factors ─────────────────────────────────────────
        step_qalys_disc = step_qalys * discount_health
        step_costs_disc = step_costs * discount_cost

        # ── Accumulate totals ──────────────────────────────────────────────
        self._cumul_qalys_undisc += step_qalys
        self._cumul_qalys_disc   += step_qalys_disc
        self._cumul_costs_undisc += step_costs
        self._cumul_costs_disc   += step_costs_disc

        for component in QALY_COMPONENT_KEYS:
            value = self._last_step_qaly_components[component]
            self._qaly_component_breakdown_undisc[component] = (
                self._qaly_component_breakdown_undisc.get(component, 0.0) + value
            )
            self._qaly_component_breakdown_disc[component] = (
                self._qaly_component_breakdown_disc.get(component, 0.0)
                + value * discount_health
            )

        for cat, val in step_breakdown.items():
            if cat not in self._cost_breakdown_disc:
                self._cost_breakdown_disc[cat] = 0.0
            self._cost_breakdown_disc[cat] += val * discount_cost

        # ── Societal additions (BACKLOG-20) ────────────────────────────────
        # Computed when a perspective block is present in hta_config.
        # Both sub-methods return (0.0, {}) when disabled or not applicable,
        # so this block is always safe to call — it is simply a no-op when
        # the perspective block is absent.
        if self._perspective_cfg is not None:
            step_prod_loss, prod_breakdown = self._compute_productivity_losses(
                population, step_fraction, new_infections, new_chronic, new_aids,
                new_infections_by_age=new_infections_by_age,    # V12.1 BACKLOG-21
                new_chronic_by_age=new_chronic_by_age,
                new_aids_by_age=new_aids_by_age,
            )
            step_time_cost, time_breakdown = self._compute_patient_time_costs(
                population, step_fraction
            )
            step_societal_additions = step_prod_loss + step_time_cost

            self._cumul_societal_additions_undisc += step_societal_additions
            self._cumul_societal_additions_disc   += step_societal_additions * discount_cost

            for cat, val in prod_breakdown.items():
                if cat not in self._societal_breakdown_disc:
                    self._societal_breakdown_disc[cat] = 0.0
                self._societal_breakdown_disc[cat] += val * discount_cost

            for cat, val in time_breakdown.items():
                if cat not in self._societal_breakdown_disc:
                    self._societal_breakdown_disc[cat] = 0.0
                self._societal_breakdown_disc[cat] += val * discount_cost

        # ── Record timestep ────────────────────────────────────────────────
        timestep_record = {
            "step":                   step,
            "year":                   year,
            "step_qalys_undisc":      step_qalys,
            "step_qalys_disc":        step_qalys_disc,
            "step_costs_undisc":      step_costs,
            "step_costs_disc":        step_costs_disc,
            "step_new_infections":    new_infections,
            "step_new_diagnoses":     new_diagnoses,
            "step_art_initiations":    sum((art_initiations_by_stage or {}).values()),
            "step_art_initiations_acute":   (art_initiations_by_stage or {}).get("Acute", 0),
            "step_art_initiations_chronic": (art_initiations_by_stage or {}).get("Chronic", 0),
            "step_art_initiations_aids":    (art_initiations_by_stage or {}).get("AIDS", 0),
            "step_utility_decrements_undisc": self._last_step_utility_decrement,
            "step_utility_decrements_disc": self._last_step_utility_decrement * discount_health,
            "discount_factor_health": discount_health,
            "discount_factor_cost":   discount_cost,
            "total_population":       float(np.sum(population)),  # V11.7.1: denominator for per-person metrics
            "step_fraction":          step_fraction,              # V11.7.2: stored for ScenarioComparator cross-check
        }
        for component in QALY_COMPONENT_KEYS:
            value = self._last_step_qaly_components[component]
            timestep_record[f"step_qaly_{component}_undisc"] = value
            timestep_record[f"step_qaly_{component}_disc"] = value * discount_health
        self._timesteps.append(timestep_record)

    def get_summary(self) -> Dict[str, Any]:
        """
        Return aggregate health economic summary.

        Returns:
            Dictionary with:
              total_qalys_disc, total_qalys_undisc  — discounted / undiscounted QALYs
              total_costs_disc, total_costs_undisc   — discounted / undiscounted costs (EUR)
                                                       Healthcare payer perspective only.
              cumul_new_infections                   — total infections over horizon
              cumul_new_diagnoses                    — total new diagnoses over horizon
              cost_breakdown_disc                    — dict of discounted costs by category
                                                       (keys present only when costs > 0; callers
                                                        should use .get(key, 0) for optional keys)
              cost_per_qaly_disc                     — average cost per QALY (if QALYs > 0)
              qaly_component_breakdown_disc           — discounted QALYs by A-1 component;
                                                       components sum to total_qalys_disc
              qaly_component_breakdown_undisc         — undiscounted equivalent
              ── Societal perspective keys (BACKLOG-20) ──────────────────────────────
              total_societal_additions_disc          — discounted productivity losses +
                                                       patient time costs (EUR). 0.0 when
                                                       perspective block absent in config.
              total_societal_additions_undisc        — undiscounted societal additions (EUR).
              total_costs_disc_societal              — full societal total: payer costs +
                                                       societal additions, discounted (EUR).
              societal_breakdown_disc                — dict of discounted societal additions
                                                       by category. Keys: "productivity_loss",
                                                       "patient_time". Empty when perspective
                                                       block absent in config.
        """
        cumul_infections  = sum(ts["step_new_infections"] for ts in self._timesteps)
        cumul_diagnoses   = sum(ts.get("step_new_diagnoses", 0) for ts in self._timesteps)

        breakdown = dict(self._cost_breakdown_disc)

        summary: Dict[str, Any] = {
            "total_qalys_disc":      self._cumul_qalys_disc,
            "total_qalys_undisc":    self._cumul_qalys_undisc,
            "total_costs_disc":      self._cumul_costs_disc,
            "total_costs_undisc":    self._cumul_costs_undisc,
            "cumul_new_infections":  cumul_infections,
            "cumul_new_diagnoses":   cumul_diagnoses,
            "cost_breakdown_disc":   breakdown,
            "qaly_component_breakdown_disc": (
                dict(self._qaly_component_breakdown_disc)
            ),
            "qaly_component_breakdown_undisc": (
                dict(self._qaly_component_breakdown_undisc)
            ),
            # Societal perspective additions (BACKLOG-20)
            "total_societal_additions_disc":   self._cumul_societal_additions_disc,
            "total_societal_additions_undisc": self._cumul_societal_additions_undisc,
            "total_costs_disc_societal":       (
                self._cumul_costs_disc + self._cumul_societal_additions_disc
            ),
            "societal_breakdown_disc":         dict(self._societal_breakdown_disc),
        }

        if self._cumul_qalys_disc > 0.01:  # V11.7.2: stricter guard (was > 0)
            summary["cost_per_qaly_disc"] = (
                self._cumul_costs_disc / self._cumul_qalys_disc
            )

        return summary

    def get_timestep_records(self) -> List[Dict[str, Any]]:
        """
        Return per-timestep records for detailed analysis or export.

        Returns:
            List of dicts, one per step, with keys:
              step, year, step_qalys_disc/undisc, step_costs_disc/undisc,
              step_new_infections, step_new_diagnoses,
              discount_factor_health, discount_factor_cost.
        """
        return self._timesteps.copy()

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: v2 computation
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_step_v2(
        self,
        population:      np.ndarray,
        step:            int,
        year:            float,
        step_fraction:   float,
        new_diagnoses:   int,
        new_aids_deaths: int = 0,
        prep_initiations_by_arm: Optional[Dict[str, int]] = None,
        art_initiations_by_stage: Optional[Dict[str, int]] = None,
    ):
        """
        Compute undiscounted step QALY and cost contributions from v2 JSON structure.

        Returns:
            (step_qalys: float, step_costs: float, step_breakdown: dict)
        """
        qalys     = 0.0
        costs     = 0.0
        breakdown: Dict[str, float] = {}
        qaly_components = {key: 0.0 for key in QALY_COMPONENT_KEYS}

        def add_cost(category: str, amount: float) -> None:
            if amount:  # skip zero contributions — keeps breakdown sparse and correct
                breakdown[category] = breakdown.get(category, 0.0) + amount

        cfg        = self._v2_config
        utilities  = cfg["utilities"]
        susc_idx   = require_idx(self.groups.hiv_to_idx, "Susceptible", "HIV state")
        no_prep_idx = require_idx(self.groups.prep_to_idx, "None", "PrEP state")

        # ── 1. UTILITY: SUSCEPTIBLE (no PrEP) ─────────────────────────────
        susc_no_prep = float(np.sum(population[:, :, :, susc_idx, :, :, no_prep_idx]))
        u_susc       = extract_value(utilities.get("susceptible", {"value": 1.0}))
        contribution = susc_no_prep * u_susc * step_fraction
        qaly_components["susceptible_no_prep"] = contribution
        qalys += contribution

        # ── 2. UTILITY: SUSCEPTIBLE (on PrEP, by type) ────────────────────
        for prep_state, util_key in _PREP_STATE_TO_UTILITY_KEY.items():
            if prep_state not in self.groups.prep_to_idx:
                continue
            prep_idx = self.groups.prep_to_idx[prep_state]
            count    = float(np.sum(population[:, :, :, susc_idx, :, :, prep_idx]))
            u_prep   = extract_value(utilities.get(util_key, {"value": 0.99}))
            contribution = count * u_prep * step_fraction
            qaly_components[_PREP_STATE_TO_QALY_COMPONENT[prep_state]] = contribution
            qalys += contribution

        # ── 3. UTILITY: HIV+ STATES ────────────────────────────────────────
        # (stage, art_dim, utility_key)
        # art_dim=None → sum over both ART states (used for Acute where no split needed)
        hiv_util_map = [
            ("Acute",   None, "hiv_acute",              "hiv_acute"),
            ("Chronic", 1,    "hiv_chronic_on_art",     "hiv_chronic_on_art"),
            ("Chronic", 0,    "hiv_chronic_off_art",    "hiv_chronic_off_art"),
            ("AIDS",    1,    "aids_on_art",             "hiv_aids_on_art"),
            ("AIDS",    0,    "aids_off_art",            "hiv_aids_off_art"),
        ]
        for stage, art_dim, util_key, component_key in hiv_util_map:
            if stage not in self.groups.hiv_to_idx:
                continue
            stage_idx = self.groups.hiv_to_idx[stage]
            if art_dim is None:
                count = float(np.sum(population[:, :, :, stage_idx, :, :, :]))
            else:
                count = float(np.sum(population[:, :, :, stage_idx, :, art_dim, :]))
            u_hiv  = extract_value(utilities.get(util_key, {"value": 0.7}))
            contribution = count * u_hiv * step_fraction
            qaly_components[component_key] = contribution
            qalys += contribution

        # ── 4. PREVENTION COSTS (arm-specific, only if not no_prep) ────────
        if self.arm_name != "no_prep":
            costs_v, breakdown_v = self._compute_prevention_costs_v2(
                population, susc_idx, step_fraction,
                prep_initiations_by_arm=prep_initiations_by_arm
            )
            costs += costs_v
            for cat, val in breakdown_v.items():
                add_cost(cat, val)

        # ── 5. DISEASE COSTS ───────────────────────────────────────────────
        costs_d, breakdown_d = self._compute_disease_costs_v2(
            population, step_fraction, new_diagnoses, new_aids_deaths,
            art_initiations_by_stage=art_initiations_by_stage,
        )
        costs += costs_d
        for cat, val in breakdown_d.items():
            add_cost(cat, val)

        # ── 6. EVENT UTILITY DECREMENTS ───────────────────────────────────
        # Positive quantities representing one-time QALY losses. This mirrors
        # disease_costs cohort routing but affects QALYs rather than costs.
        utility_loss = self._compute_utility_decrements_v2(
            art_initiations_by_stage=art_initiations_by_stage,
        )
        self._last_step_utility_decrement = utility_loss
        qalys -= utility_loss
        qaly_components["utility_decrement_art_initiation"] = -utility_loss
        self._last_step_qaly_components = qaly_components

        return qalys, costs, breakdown

    def _compute_utility_decrements_v2(
        self,
        art_initiations_by_stage: Optional[Dict[str, int]] = None,
    ) -> float:
        """
        Compute one-time QALY losses from v2 utility_decrements.

        Supported billing moment:
          'art_initiation' — applied once per gross ART initiator in the listed
                             HIV stages, using art_initiations_by_stage.
        """
        cfg = self._v2_config
        total_loss = 0.0

        for dec_id, item in cfg.get("utility_decrements", {}).items():
            if dec_id.startswith("_"):
                continue
            billing_moment = item.get("billing_moment")
            if billing_moment != "art_initiation":
                continue
            if art_initiations_by_stage is None:
                raise ValueError(
                    "EconomicsAccumulator requires art_initiations_by_stage when "
                    "utility_decrements use billing_moment='art_initiation'."
                )
            decrement = extract_value(item.get("value", 0.0))
            stages = item.get("applies_to_stages", [])
            count = sum(float(art_initiations_by_stage.get(stage, 0.0)) for stage in stages)
            total_loss += count * decrement

        return total_loss

    def _compute_prevention_costs_v2(
        self,
        population:    np.ndarray,
        susc_idx:      int,
        step_fraction: float,
        prep_initiations_by_arm: Optional[Dict[str, int]] = None,
    ):
        """
        Compute prevention costs for the current arm from v2 prevention_costs section.

        Handles:
          - Shared costs (STI screening, etc.) applied to all PrEP users of this arm
          - Arm-specific costs with cohort routing:
              'all'            → all current users
              'new_initiators' → delta from previous step (new entrants)
              'continuing'     → users present in previous step (continuing)

        Returns:
            (total_costs: float, breakdown: dict)
        """
        costs     = 0.0
        breakdown: Dict[str, float] = {}

        cfg       = self._v2_config
        prev_cfg  = cfg.get("prevention_costs", {})
        arm_key   = self.arm_name
        prep_state = _ARM_TO_PREP_STATE.get(arm_key)

        def _users_for_prep_state(state_name: str) -> float:
            prep_idx = self.groups.prep_to_idx.get(state_name)
            if prep_idx is None:
                return 0.0
            return float(np.sum(population[:, :, :, susc_idx, :, :, prep_idx]))

        def _component_arm_for_item(item_id: str, item: Dict) -> str:
            """Route a combo-arm cost item to the PrEP state it actually bills."""
            explicit = item.get("component_arm") or item.get("applies_to_arm")
            if explicit in ("oral_prep", "lenacapavir"):
                return explicit
            explicit_state = item.get("prep_state") or item.get("applies_to_prep_state")
            if explicit_state == "Oral_PrEP":
                return "oral_prep"
            if explicit_state == "Lenacapavir":
                return "lenacapavir"

            key_text = " ".join(
                str(x).lower()
                for x in (
                    item_id,
                    item.get("id", ""),
                    item.get("label", ""),
                    item.get("notes", ""),
                )
            )
            if any(token in key_text for token in ("injection", "lenacapavir", "lena", "loading")):
                return "lenacapavir"
            return "oral_prep"

        def _component_counts(component_arm: str) -> Tuple[float, float, float, str]:
            state_name = _ARM_TO_PREP_STATE.get(component_arm)
            current = _users_for_prep_state(state_name) if state_name else 0.0
            prev_key = (
                arm_key
                if arm_key == component_arm
                else f"{arm_key}:{component_arm}"
            )
            prev = float(self._prev_prep_users.get(prev_key, 0))
            if prep_initiations_by_arm is not None:
                new = float(prep_initiations_by_arm.get(component_arm, 0))
            elif self._arm_has_initiation_billed_costs(arm_key):
                raise ValueError(
                    "EconomicsAccumulator requires gross prep_initiations_by_arm "
                    "when initiation-billed prevention costs are configured. "
                    "Pass SimulationEngineV3._prep_step_initiations counters keyed "
                    "by HTA arm name."
                )
            else:
                new = max(0.0, current - prev)
            continuing_count = min(current, prev)
            return current, new, continuing_count, prev_key

        def _add_prevention_cost(category: str, amount: float,
                                 component_arm: Optional[str]) -> None:
            if not amount:
                return
            breakdown[category] = breakdown.get(category, 0.0) + amount
            if category != "drug":
                return
            breakdown["prep_drug"] = breakdown.get("prep_drug", 0.0) + amount
            if component_arm == "lenacapavir":
                breakdown["prep_drug_lenacapavir"] = (
                    breakdown.get("prep_drug_lenacapavir", 0.0) + amount
                )
            elif component_arm == "oral_prep":
                breakdown["prep_drug_oral"] = (
                    breakdown.get("prep_drug_oral", 0.0) + amount
                )

        # Resolve current user count and cohort splits. Single-PrEP arms retain
        # the legacy arm-level state. The mixed arm tracks each PrEP component
        # separately because oral costs must bill Oral_PrEP users while Lena
        # costs must bill Lenacapavir users.
        current_users = 0.0
        new_initiators = 0.0
        continuing = 0.0
        oral_current = oral_new = oral_continuing = 0.0
        lena_current = lena_new = lena_continuing = 0.0

        if arm_key == "oral_prep_plus_lena":
            oral_current, oral_new, oral_continuing, oral_prev_key = _component_counts("oral_prep")
            lena_current, lena_new, lena_continuing, lena_prev_key = _component_counts("lenacapavir")
            current_users = oral_current + lena_current
            new_initiators = oral_new + lena_new
            continuing = oral_continuing + lena_continuing
            self._prev_prep_users[oral_prev_key] = int(oral_current)
            self._prev_prep_users[lena_prev_key] = int(lena_current)
            self._prev_prep_users[arm_key] = int(current_users)
        else:
            if prep_state and prep_state in self.groups.prep_to_idx:
                current_users = _users_for_prep_state(prep_state)

            # Cohort split
            # continuing = min(current, prev): users present in both this step and the last.
            # When users drop off (current < prev), prev_users exceeds actual on-PrEP count,
            # so charging prev_users for "continuing" would bill ghost users. min() ensures
            # only people actually on PrEP this step are charged. (V11.9.10)
            prev_users = float(self._prev_prep_users.get(arm_key, 0))
            if prep_initiations_by_arm is not None:
                new_initiators = float(prep_initiations_by_arm.get(arm_key, 0))
            elif self._arm_has_initiation_billed_costs(arm_key):
                raise ValueError(
                    "EconomicsAccumulator requires gross prep_initiations_by_arm "
                    "when initiation-billed prevention costs are configured. "
                    "Pass SimulationEngineV3._prep_step_initiations counters keyed "
                    "by HTA arm name."
                )
            else:
                new_initiators = max(0.0, current_users - prev_users)
            continuing = min(current_users, prev_users)
            self._prev_prep_users[arm_key] = int(current_users)

        # ── Shared costs ──────────────────────────────────────────────────
        for item in prev_cfg.get("shared", []):
            applies_to = item.get("applies_to", [])
            item_cost = extract_value(item.get("value", 0.0))
            category  = item.get("category", "monitoring")
            if arm_key == "oral_prep_plus_lena":
                shared_parts: List[Tuple[str, float]] = []
                if "oral_prep" in applies_to or arm_key in applies_to:
                    shared_parts.append(("oral_prep", oral_current))
                if "lenacapavir" in applies_to or arm_key in applies_to:
                    shared_parts.append(("lenacapavir", lena_current))
                for component_arm, count in shared_parts:
                    if count <= 0:
                        continue
                    amount = count * item_cost * step_fraction
                    costs += amount
                    _add_prevention_cost(category, amount, component_arm)
            else:
                if arm_key not in applies_to:
                    continue
                if current_users <= 0:
                    continue
                amount = current_users * item_cost * step_fraction
                costs += amount
                _add_prevention_cost(category, amount, arm_key)

        # ── Arm-specific costs ─────────────────────────────────────────────
        # Change C (V12.9): warn if arm_specific entry missing for non-no_prep arm
        _arm_specific_section = prev_cfg.get("arm_specific", {})
        if arm_key not in _arm_specific_section and arm_key != "no_prep":
            import warnings
            warnings.warn(
                f"EconomicsAccumulator: prevention_costs.arm_specific has no entry "
                f"for arm '{arm_key}'. Prevention costs will be zero for this arm. "
                f"Add an 'arm_specific.{arm_key}' block to hta_config.json to fix.",
                UserWarning,
                stacklevel=2,
            )
        arm_specific = _arm_specific_section.get(arm_key, {})
        for _item_id, item in arm_specific.items():
            item_cost = extract_value(item.get("value", 0.0))
            category  = item.get("category", "drug")
            cohort    = item.get("cohort", "all")

            if arm_key == "oral_prep_plus_lena":
                component_arm = _component_arm_for_item(_item_id, item)
                if component_arm == "lenacapavir":
                    item_current = lena_current
                    item_new = lena_new
                    item_continuing = lena_continuing
                else:
                    item_current = oral_current
                    item_new = oral_new
                    item_continuing = oral_continuing
            else:
                item_current = current_users
                item_new = new_initiators
                item_continuing = continuing

            if cohort == "new_initiators":
                count = item_new
            elif cohort == "continuing":
                count = item_continuing
            else:  # "all" or unrecognised
                count = item_current

            # V11.9.11 — frequency:"once" items are one-time events (e.g. oral_loading_year1).
            # They must NOT be multiplied by step_fraction: the full item cost is charged per
            # person in the relevant cohort in the step they fire.
            # frequency:"annual" items represent annualised rates and ARE divided by step_fraction.
            # NOTE: billing_moment cannot be used as the discriminator here — injection_year1 also
            # has billing_moment:"initiation" but is an annual rate (correctly amortised quarterly).
            frequency = item.get("frequency", "annual")
            if frequency == "once":
                amount = count * item_cost
            else:
                amount = count * item_cost * step_fraction
            costs    += amount
            component_for_breakdown = (
                component_arm if arm_key == "oral_prep_plus_lena" else arm_key
            )
            _add_prevention_cost(category, amount, component_for_breakdown)

        return costs, breakdown

    def _arm_has_initiation_billed_costs(self, arm_key: str) -> bool:
        """Return True when this arm has prevention costs that require gross initiations."""
        prev_cfg = self._v2_config.get("prevention_costs", {}) if self._v2_config else {}
        arm_specific = prev_cfg.get("arm_specific", {}).get(arm_key, {})
        for item in arm_specific.values():
            if item.get("cohort") == "new_initiators":
                return True
            if item.get("billing_moment") == "initiation":
                return True
        return False

    def _compute_disease_costs_v2(
        self,
        population:      np.ndarray,
        step_fraction:   float,
        new_diagnoses:   int,
        new_aids_deaths: int = 0,
        art_initiations_by_stage: Optional[Dict[str, int]] = None,
    ):
        """
        Compute disease costs from v2 disease_costs section.

        Routing by billing_moment:
          'ongoing'   — applied every step, weighted by population in applicable states
          'diagnosis' — applied once per new diagnosis (new_diagnoses counter)
          'death'     — applied once per AIDS death (new_aids_deaths counter); V11.7.3
                        No step_fraction multiplication — one-time event per death.

        Returns:
            (total_costs: float, breakdown: dict)
        """
        costs     = 0.0
        breakdown: Dict[str, float] = {}

        cfg = self._v2_config
        for _cost_id, item in cfg.get("disease_costs", {}).items():
            billing_moment    = item.get("billing_moment", "ongoing")
            item_cost         = extract_value(item.get("value", 0.0))
            category          = item.get("category", "hiv_care")
            applies_to_stages = item.get("applies_to_stages", [])
            applies_to_art    = item.get("applies_to_art_status", ["on_art", "off_art"])
            cohort            = item.get("cohort", "all")

            def _amount_for_count(count: float) -> float:
                if item.get("frequency", "annual") == "once":
                    return count * item_cost
                return count * item_cost * step_fraction

            if billing_moment == "diagnosis":
                # One-time cost per newly diagnosed individual
                amount = new_diagnoses * item_cost
                costs += amount
                breakdown[category] = breakdown.get(category, 0.0) + amount
                if _cost_id == "art_first_line" and amount:
                    breakdown["hiv_art_drug"] = (
                        breakdown.get("hiv_art_drug", 0.0) + amount
                    )

            elif billing_moment == "death":
                # One-time cost per AIDS death — no step_fraction multiplication.
                # Fires once per event, not per person-year. Analogous to "diagnosis"
                # billing. Currently scoped to AIDS deaths only (V11.7.3).
                amount = new_aids_deaths * item_cost
                costs += amount
                breakdown[category] = breakdown.get(category, 0.0) + amount
                if _cost_id == "art_first_line" and amount:
                    breakdown["hiv_art_drug"] = (
                        breakdown.get("hiv_art_drug", 0.0) + amount
                    )

            elif billing_moment == "ongoing":
                if cohort in ("all", None):
                    for stage in applies_to_stages:
                        if stage not in self.groups.hiv_to_idx:
                            continue
                        stage_idx = self.groups.hiv_to_idx[stage]

                        for art_status in applies_to_art:
                            art_dim = _ART_STATUS_TO_IDX.get(art_status)
                            if art_dim is None:
                                continue
                            count  = float(np.sum(
                                population[:, :, :, stage_idx, :, art_dim, :]
                            ))
                            amount = _amount_for_count(count)
                            costs += amount
                            breakdown[category] = breakdown.get(category, 0.0) + amount
                            if _cost_id == "art_first_line" and amount:
                                breakdown["hiv_art_drug"] = (
                                    breakdown.get("hiv_art_drug", 0.0) + amount
                                )

                elif cohort in ("new_art_initiators", "on_art_continuing"):
                    if art_initiations_by_stage is None:
                        raise ValueError(
                            "EconomicsAccumulator requires art_initiations_by_stage "
                            "when disease_costs use cohort='new_art_initiators' or "
                            "cohort='on_art_continuing'. Pass "
                            "SimulationEngineV3._art_step_initiations_by_stage counters "
                            "keyed by HIV stage."
                        )
                    if "on_art" not in applies_to_art:
                        raise ValueError(
                            f"disease_costs.{_cost_id}: cohort='{cohort}' requires "
                            "applies_to_art_status to include 'on_art'."
                        )
                    for stage in applies_to_stages:
                        if stage not in self.groups.hiv_to_idx:
                            continue
                        stage_idx = self.groups.hiv_to_idx[stage]
                        new_stage = float(art_initiations_by_stage.get(stage, 0))
                        if cohort == "new_art_initiators":
                            count = new_stage
                        else:
                            current_on_art = float(np.sum(
                                population[:, :, :, stage_idx, :, _ART_STATUS_TO_IDX["on_art"], :]
                            ))
                            count = max(0.0, current_on_art - new_stage)

                        amount = _amount_for_count(count)
                        costs += amount
                        breakdown[category] = breakdown.get(category, 0.0) + amount
                        if _cost_id == "art_first_line" and amount:
                            breakdown["hiv_art_drug"] = (
                                breakdown.get("hiv_art_drug", 0.0) + amount
                            )

                else:
                    raise ValueError(
                        f"disease_costs.{_cost_id}: unsupported cohort '{cohort}'. "
                        "Supported disease_costs cohorts are 'all', "
                        "'new_art_initiators', and 'on_art_continuing'."
                    )

            # Unsupported billing_moment values are rejected by validate_hta_config().
            # 'death' is handled above; fires once per AIDS death event (V11.7.3).

        return costs, breakdown

    # ──────────────────────────────────────────────────────────────────────────
    # Societal perspective computations (BACKLOG-20)
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_productivity_losses(
        self,
        population:             np.ndarray,
        step_fraction:          float,
        new_infections:         int = 0,
        new_chronic:            int = 0,
        new_aids:               int = 0,
        new_infections_by_age:  Optional[np.ndarray] = None,
        new_chronic_by_age:     Optional[np.ndarray] = None,
        new_aids_by_age:        Optional[np.ndarray] = None,
    ) -> tuple:
        """
        Dispatcher: routes to incident (friction cost) or prevalent (human capital capped)
        implementation based on productivity_loss.method in hta_config.json.

        Valid method values:
          "friction_incident"    — ZIN 2022 compliant; fires once per new case entering
                                   each stage; implements Koopmanschap et al. (1995).
          "human_capital_capped" — prevalent-based; charges working-age HIV+ stock every
                                   step, capped at friction_period/52 per year. Retained
                                   for sensitivity comparison runs.

        The *_by_age arrays (V12.1 BACKLOG-21) are forwarded to the incident method only;
        the prevalent method is unaffected.

        Returns (0.0, {}) when perspective block absent, disabled, or no incident cases.
        """
        if self._perspective_cfg is None:
            return 0.0, {}
        prod_cfg = self._perspective_cfg.get("productivity_loss", {})
        if not prod_cfg.get("enabled", True):
            return 0.0, {}

        method = prod_cfg.get("method", "friction_incident")
        if method == "friction_incident":
            return self._compute_productivity_losses_incident(
                population, new_infections, new_chronic, new_aids, prod_cfg,
                new_infections_by_age=new_infections_by_age,    # V12.1 BACKLOG-21
                new_chronic_by_age=new_chronic_by_age,
                new_aids_by_age=new_aids_by_age,
            )
        elif method == "human_capital_capped":
            return self._compute_productivity_losses_prevalent(
                population, step_fraction, prod_cfg
            )
        else:
            raise ValueError(
                f"productivity_loss.method {method!r} is not recognised. "
                f"Valid options: 'friction_incident', 'human_capital_capped'."
            )

    def _sum_working_age_from_array(self, by_age: np.ndarray) -> float:
        """
        Return the count of incident cases in working-age bins directly from an
        age-stratified transition array.

        Used by the friction cost method (V12.1 BACKLOG-21) to eliminate the
        prevalence-based wa_frac proxy when age-stratified counts are available.

        Args:
            by_age: 1-D array of shape (n_age_groups,) with incident counts per bin.
                    Must have the same length as groups.age_groups. Raises ValueError
                    if length does not match _working_age_indices range.

        Returns:
            float: sum of by_age[_working_age_indices] when indices are set, otherwise
                   by_age.sum() (no age filtering possible without index list).

        Raises:
            ValueError: if by_age has fewer elements than the maximum working-age index,
                        indicating a mis-sized array (programming error).
        """
        if not self._working_age_indices:
            return float(by_age.sum())
        max_idx = max(self._working_age_indices)
        if len(by_age) <= max_idx:
            raise ValueError(
                f"_sum_working_age_from_array: by_age has length {len(by_age)} but "
                f"maximum working-age index is {max_idx}. Array length must equal "
                f"n_age_groups ({len(self.groups.age_groups)})."
            )
        return float(by_age[self._working_age_indices].sum())

    def _compute_productivity_losses_incident(
        self,
        population:            np.ndarray,
        new_infections:        int,
        new_chronic:           int,
        new_aids:              int,
        prod_cfg:              dict,
        new_infections_by_age: Optional[np.ndarray] = None,
        new_chronic_by_age:    Optional[np.ndarray] = None,
        new_aids_by_age:       Optional[np.ndarray] = None,
    ) -> tuple:
        """
        Friction cost method per Koopmanschap et al. (1995).

        Fires ONCE per new entrant to each HIV stage. Cost = replacement cost during the
        friction period. No step_fraction multiplication — this is an event-based cost
        analogous to billing_moment='diagnosis' that does not recur in later steps.

        Incident counts:
          new_infections → Susceptible→Acute (from transmission engine)
          new_chronic    → Acute→Chronic (from _step_disease_progression)
          new_aids       → Chronic→AIDS  (from _step_disease_progression)

        Formula per incident case in stage s:
            cost = employ_rate × loss_rate[s] × wage × annual_hours × (friction_weeks/52)

        new_chronic uses 'chronic_off_art' loss rate (conservative proxy — ART status at
        transition not tracked; off-ART rate is higher; see BACKLOG-22).
        new_aids uses 'aids_off_art' for the same reason.

        Age stratification (V12.1 BACKLOG-21):
        When all three *_by_age arrays are provided AND _working_age_indices is set,
        the working-age count is read directly from the transition array rather than
        approximated via the HIV+ prevalence distribution. Falls back to the prevalence
        proxy (wa_frac) when arrays are absent for backward compatibility.
        """
        friction_weeks = extract_value(prod_cfg.get("friction_period_weeks", {"value": 16.4}))
        hourly_wage    = extract_value(prod_cfg.get("average_hourly_wage",    {"value": 39.88}))
        annual_hours   = extract_value(prod_cfg.get("annual_hours_worked",    {"value": 1720}))
        employ_rate    = extract_value(prod_cfg.get("employment_rate_modifier", {"value": 1.0}))
        friction_frac  = friction_weeks / 52.0
        loss_rates     = prod_cfg.get("loss_rate_by_stage", {})

        # V12.1 BACKLOG-21: prefer direct age-array count; fall back to prevalence proxy.
        use_age_arrays = (
            new_infections_by_age is not None
            and new_chronic_by_age is not None
            and new_aids_by_age is not None
            and bool(self._working_age_indices)
        )

        if use_age_arrays:
            # Direct working-age count from transition arrays — eliminates proxy bias.
            incident_map = [
                (self._sum_working_age_from_array(new_infections_by_age), "acute"),
                (self._sum_working_age_from_array(new_chronic_by_age),    "chronic_off_art"),
                (self._sum_working_age_from_array(new_aids_by_age),       "aids_off_art"),
            ]
        else:
            # Legacy path: scalar counts × prevalence-based working-age fraction.
            wa_frac = self._compute_working_age_fraction(population)
            incident_map = [
                (new_infections * wa_frac, "acute"),
                (new_chronic    * wa_frac, "chronic_off_art"),
                (new_aids       * wa_frac, "aids_off_art"),
            ]

        total_loss = 0.0
        for n_wa, rate_key in incident_map:
            if n_wa <= 0 or rate_key not in loss_rates:
                continue
            loss_rate   = extract_value(loss_rates[rate_key])
            total_loss += n_wa * employ_rate * loss_rate * hourly_wage * annual_hours * friction_frac

        if total_loss == 0.0:
            return 0.0, {}
        return total_loss, {"productivity_loss": total_loss}

    def _compute_productivity_losses_prevalent(
        self,
        population:    np.ndarray,
        step_fraction: float,
        prod_cfg:      dict,
    ) -> tuple:
        """
        Capped human capital method (retained for sensitivity analysis).

        Charges the prevalent working-age HIV+ stock every step, capped at
        friction_period_weeks/52 of annual productive capacity. This was the
        original implementation (pre-V11.11.9) mislabelled as friction cost.
        Available via productivity_loss.method = "human_capital_capped".
        """
        if not self._working_age_indices:
            return 0.0, {}

        friction_weeks  = extract_value(prod_cfg.get("friction_period_weeks", {"value": 16.4}))
        hourly_wage     = extract_value(prod_cfg.get("average_hourly_wage",    {"value": 39.88}))
        annual_hours    = extract_value(prod_cfg.get("annual_hours_worked",    {"value": 1720}))
        employ_rate     = extract_value(prod_cfg.get("employment_rate_modifier", {"value": 1.0}))
        friction_cap    = min(1.0, friction_weeks / 52.0)
        loss_rates_cfg  = prod_cfg.get("loss_rate_by_stage", {})

        # Map config stage keys → (hiv_stage_name, art_dim)
        # art_dim: 0 = off_art, 1 = on_art, None = sum over both (Acute)
        stage_map = [
            ("acute",           "Acute",   None),
            ("chronic_off_art", "Chronic", 0),
            ("chronic_on_art",  "Chronic", 1),
            ("aids_off_art",    "AIDS",    0),
            ("aids_on_art",     "AIDS",    1),
        ]

        total_loss = 0.0
        for cfg_key, stage_name, art_dim in stage_map:
            if stage_name not in self.groups.hiv_to_idx:
                continue
            if cfg_key not in loss_rates_cfg:
                continue

            stage_idx = self.groups.hiv_to_idx[stage_name]
            loss_rate = extract_value(loss_rates_cfg[cfg_key])

            if art_dim is None:
                count = float(
                    population[self._working_age_indices, :, :, stage_idx, :, :, :].sum()
                )
            else:
                count = float(
                    population[self._working_age_indices, :, :, stage_idx, :, art_dim, :].sum()
                )

            total_loss += count * employ_rate * loss_rate * hourly_wage * annual_hours * step_fraction * friction_cap

        if total_loss == 0.0:
            return 0.0, {}
        return total_loss, {"productivity_loss": total_loss}

    def _compute_working_age_fraction(self, population) -> float:
        """
        Return the fraction of the HIV+ population that is working-age.

        Used as a proxy for the age distribution of incident cases when
        age-stratified transition counts are not available from the engine.
        If _working_age_indices is unset or population is None, returns 1.0
        (no age adjustment applied).
        """
        if not self._working_age_indices or population is None:
            return 1.0
        susc_idx = require_idx(self.groups.hiv_to_idx, "Susceptible", "HIV state")
        hiv_plus_total = float(population[:, :, :, susc_idx + 1:, :, :, :].sum())
        if hiv_plus_total <= 0:
            return 1.0
        hiv_plus_wa = float(
            population[self._working_age_indices, :, :, susc_idx + 1:, :, :, :].sum()
        )
        return hiv_plus_wa / hiv_plus_total

    def _compute_patient_time_costs(
        self,
        population:    np.ndarray,
        step_fraction: float,
    ) -> tuple:
        """
        Compute patient time costs for this timestep (opportunity cost of clinic visits).

        Two components, both active when the perspective block is configured:

        1. PrEP monitoring visits (unchanged from V11.8):
           Susceptible individuals on oral PrEP (4 visits/year) or lenacapavir
           (2 visits/year). The no_prep arm contributes zero from this component.

        2. HIV+ on-ART monitoring visits (V11.9.12):
           On-ART individuals across Acute/Chronic/AIDS stages attend quarterly
           outpatient monitoring (viral load, CD4, labs, clinical review) —
           4 visits/year per Dutch HIV treatment guidelines (SHM ATHENA protocol).
           Applied to ALL arms including no_prep: every arm carries HIV+ patients.
           Off-ART patients are excluded — they are not in structured monitoring.

        Formula (per step, both components):
            cost = n_people × visits_per_year × time_per_visit_hours
                   × average_hourly_wage × step_fraction

        Returns:
            (total_cost: float, breakdown: dict)
            breakdown has key "patient_time" when total_cost > 0, else {}.
            Returns (0.0, {}) when patient_time_costs block is absent or disabled.
        """
        if self._perspective_cfg is None:
            return 0.0, {}

        pt_cfg = self._perspective_cfg.get("patient_time_costs", {})
        if not pt_cfg.get("enabled", True):
            return 0.0, {}

        # Wage for opportunity-cost valuation (reuse from productivity_loss block)
        prod_cfg    = self._perspective_cfg.get("productivity_loss", {})
        hourly_wage = extract_value(prod_cfg.get("average_hourly_wage", {"value": 36.50}))

        if not pt_cfg.get("use_average_wage_for_time", True):
            # Placeholder for future patient-specific wage valuation
            return 0.0, {}

        time_per_visit = extract_value(pt_cfg.get("time_per_visit_hours", {"value": 2.5}))
        cost = 0.0

        # ── Component 1: PrEP monitoring visits ───────────────────────────────
        # oral_prep and lenacapavir arms only — no_prep has no PrEP clinic visits.
        # Change B (V12.9): independent if-blocks so oral_prep_plus_lena gets both.
        susc_idx = require_idx(self.groups.hiv_to_idx, "Susceptible", "HIV state")
        if self.arm_name in ("oral_prep", "oral_prep_plus_lena"):
            visits_per_year = extract_value(
                pt_cfg.get("oral_prep_visits_per_year", {"value": 4})
            )
            prep_idx = self.groups.prep_to_idx.get("Oral_PrEP", -1)
            if prep_idx >= 0:
                # Dimension order: age × orient × activity × hiv × diagnosis × art × prep
                n_users  = float(np.sum(population[:, :, :, susc_idx, :, :, prep_idx]))
                cost += n_users * visits_per_year * time_per_visit * hourly_wage * step_fraction

        if self.arm_name in ("lenacapavir", "oral_prep_plus_lena"):
            visits_per_year = extract_value(
                pt_cfg.get("lenacapavir_visits_per_year", {"value": 2})
            )
            prep_idx = self.groups.prep_to_idx.get("Lenacapavir", -1)
            if prep_idx >= 0:
                n_users  = float(np.sum(population[:, :, :, susc_idx, :, :, prep_idx]))
                cost += n_users * visits_per_year * time_per_visit * hourly_wage * step_fraction
        # ── Component 2: HIV+ on-ART monitoring visits (V11.9.12) ─────────────
        # Applied to ALL arms including no_prep.
        # art_dim=1 → On_ART only; off-ART patients are not in structured care.
        hiv_art_visits_py = extract_value(
            pt_cfg.get("hiv_art_visits_per_year", {"value": 4})
        )
        for stage in ["Acute", "Chronic", "AIDS"]:
            if stage not in self.groups.hiv_to_idx:
                continue
            stage_idx = self.groups.hiv_to_idx[stage]
            n_on_art  = float(np.sum(population[:, :, :, stage_idx, :, 1, :]))
            cost += n_on_art * hiv_art_visits_py * time_per_visit * hourly_wage * step_fraction

        if cost == 0.0:
            return 0.0, {}
        return cost, {"patient_time": cost}
