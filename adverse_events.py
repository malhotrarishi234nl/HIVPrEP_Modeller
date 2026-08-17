"""
adverse_events.py — Adverse Events Module for HTA Economic Evaluation.

V11.8.0: Removed legacy flat-format support and hta_config_compat shim (BACKLOG Finding 3).
         Only v2 JSON format (_format_version: v2) is accepted. Passing old flat-format
         configs raises ValueError at construction.

V11.6.2: Full refactor to read v2 adverse_events.items[] directly.
         Adds per-item computation, billing_moment routing (ongoing / initiation),
         state machine parameters from item JSON, and by_item output dict.

Two computation modes:
  1. PREVALENCE MODE (default):
     For each AE item:
       ongoing    → abs(decrement) × prevalence × users × step_fraction per step
       initiation → abs(decrement) × prevalence × new_initiators (one-time, no step_fraction)

  2. STATE MACHINE MODE (advanced, for sensitivity analysis):
     Tracks AE state per item via onset/resolution rates and severity grades.
     Parameters read from item["state_machine_params"] in v2 format.
     Each step: draw new onsets from non-AE pool, draw resolutions from AE pool,
     distribute new cases into severity grades, compute cost/utility from current state.

Return interface:
  compute_adjustments() → {
    "utility_decrement": float,   # positive quantity subtracted from QALYs
    "cost_addition":    float,    # positive cost added to total
    "by_item":          dict,     # per-item breakdown by item id
  }
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from config_utils import extract_value, validate_hta_config


# ---------------------------------------------------------------------------
# Arm-name ↔ PrEP-state-name mapping (mirrors economics.py)
# Defined locally to avoid circular import.
# ---------------------------------------------------------------------------
_ARM_TO_PREP_STATE: Dict[str, str] = {
    "oral_prep":   "Oral_PrEP",
    "lenacapavir": "Lenacapavir",
}

_SEVERITY_GRADES: List[str] = ["mild", "moderate", "severe"]


class AdverseEventModule:
    """
    Manages AE tracking and cost/utility impact accumulation.

    V11.8.0: Only v2 JSON format (_format_version: v2) is accepted.
    V11.6.2: Reads v2 adverse_events.items[] directly.
             billing_moment routing: ongoing / initiation per step.
             State machine parameters read from item["state_machine_params"].
             compute_adjustments() returns "by_item" breakdown.

    Attributes:
        hta_config:     Raw v2 HTA config dict.
        groups:         PopulationGroups with hiv_to_idx, prep_to_idx, prep_states.
        rng:            numpy RandomState for stochastic draws.
        mode:           "prevalence" or "state_machine".
        _v2_config:     Alias for hta_config (always set; retained for internal clarity).
        _prev_ae_users: arm_key → user count at last step (initiation delta tracking).
        ae_state_v2:    item_id → {mild,moderate,severe} counts (state machine path).
    """

    def __init__(
        self,
        hta_config: Dict,
        groups,
        step_fraction: float,
        rng: Optional[np.random.RandomState] = None,
    ):
        """
        Initialise the Adverse Events module.

        Args:
            hta_config:    v2 HTA config dict (_format_version: v2 required).
            groups:        PopulationGroups with hiv_to_idx, prep_to_idx, prep_states.
            step_fraction: Fraction of a year per timestep (e.g., 0.25 for quarterly).
                           Required — must match the engine's years_per_step.
            rng:           Optional RandomState. Defaults to a fresh RandomState().

        Raises:
            ValueError: If hta_config is not in v2 format, or if step_fraction is invalid.
        """
        # Validate step_fraction (V11.7.2)
        if not (0.0 < step_fraction <= 1.0):
            raise ValueError(
                f"AdverseEventModule: step_fraction must be in (0, 1], "
                f"got {step_fraction!r}. Expected 0.25 for a quarterly timestep."
            )
        self.step_fraction = step_fraction

        # ── V11.8.0: v2 format required ───────────────────────────────────────
        if not isinstance(hta_config, dict) or \
                hta_config.get("_format_version") != "v2":
            raise ValueError(
                "AdverseEventModule requires hta_config in v2 format "
                "(_format_version: 'v2'). Old flat-format configs (V11.5.x and earlier) "
                "are no longer supported. Migrate your config to the v2 structure."
            )

        validate_hta_config(hta_config)
        self._v2_config = hta_config
        self.hta_config = hta_config
        self.groups     = groups
        self.rng        = rng if rng is not None else np.random.RandomState()

        ae_config = hta_config.get("adverse_events", {})
        self.mode = extract_value(ae_config.get("mode", "prevalence"))

        if self.mode not in ("prevalence", "state_machine"):
            raise ValueError(
                f"Unknown AE mode: {self.mode!r}. Must be 'prevalence' or 'state_machine'."
            )

        self.deterministic = extract_value(
            hta_config.get("simulation_controls", {}).get("deterministic_mode", False)
        )

        # ── Delta tracking and state machine initialisation ───────────────────
        self._prev_ae_users: Dict[str, int] = {}

        if self.mode == "state_machine":
            self._init_state_machine_v2()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def compute_adjustments(
        self,
        population: np.ndarray,
        step: int,
        year: float,
        prep_initiations_by_arm: Optional[Dict[str, int]] = None,
    ) -> Dict:
        """
        Compute cost and utility adjustments due to adverse events for one step.

        Args:
            population: Full population tensor (age × orient × act × hiv × diag × art × prep).
            step:       Current timestep index (0-based, informational).
            year:       Current year (float, for time-varying params if needed).
            prep_initiations_by_arm: Gross Susceptible No_PrEP→PrEP initiations this
                       step, keyed by HTA arm name. Required for initiation-billed
                       AE items.

        Returns:
            {
                "utility_decrement": float,  # positive → subtract from QALYs
                "cost_addition":    float,   # positive → add to costs
                "by_item":          dict,    # V11.6.2: {item_id: {utility_decrement, cost_addition}}
            }

        Values are step-fraction-adjusted (ongoing = × 0.25; initiation = not × 0.25).
        Caller applies discount factors before accumulation.
        """
        return self._compute_v2(
            population, year,
            prep_initiations_by_arm=prep_initiations_by_arm
        )

    def get_state_summary(self) -> Dict:
        """
        Return current AE state for diagnostics (state machine mode only).

        Returns per-item counts keyed by item id.
        """
        if self.mode != "state_machine":
            return {}

        return {
            item_id: {**counts, "total": sum(counts.values())}
            for item_id, counts in self.ae_state_v2.items()
        }

    # ──────────────────────────────────────────────────────────────────────────
    # V11.6.2: v2 JSON computation paths
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_v2(
        self,
        population: np.ndarray,
        year: float,
        prep_initiations_by_arm: Optional[Dict[str, int]] = None,
    ) -> Dict:
        """
        Compute AE adjustments by iterating over adverse_events.items[] directly.

        Routing:
          billing_moment="ongoing"    → per-step prevalence or state machine
          billing_moment="initiation" → one-time per new PrEP initiator (no step_fraction)

        Note: "diagnosis" and "once" are not valid billing_moment values for AE items.
        Diagnosis costs belong in disease_costs and are routed by economics.py.
        The validator enforces this; any other value raises ValueError at runtime.

        Returns:
            {utility_decrement, cost_addition, by_item}
        """
        step_fraction = self.step_fraction  # V11.7.2: dynamic from engine, replaces hardcoded 0.25
        susc_idx      = self.groups.hiv_to_idx["Susceptible"]
        items         = self._v2_config.get("adverse_events", {}).get("items", [])

        # ── Current users per arm ─────────────────────────────────────────────
        current_users_by_arm: Dict[str, float] = {}
        for arm_key, prep_state in _ARM_TO_PREP_STATE.items():
            if prep_state in self.groups.prep_to_idx:
                prep_idx = self.groups.prep_to_idx[prep_state]
                current_users_by_arm[arm_key] = float(
                    np.sum(population[:, :, :, susc_idx, :, :, prep_idx])
                )
            else:
                current_users_by_arm[arm_key] = 0.0

        # ── Iterate items ─────────────────────────────────────────────────────
        total_util:  float = 0.0
        total_cost:  float = 0.0
        by_item:     Dict  = {}

        for item in items:
            item_id        = item.get("id", "unknown")
            arm_key        = item.get("arm", "")
            billing_moment = item.get("billing_moment", "ongoing")
            current_users  = current_users_by_arm.get(arm_key, 0.0)

            if billing_moment == "ongoing":
                if self.mode == "prevalence":
                    u_imp, c_imp = self._item_prevalence_ongoing(
                        item, current_users, step_fraction
                    )
                else:  # state_machine
                    u_imp, c_imp = self._item_state_machine_ongoing(
                        item, current_users, step_fraction
                    )

            elif billing_moment == "initiation":
                if prep_initiations_by_arm is None:
                    raise ValueError(
                        "AdverseEventModule requires gross prep_initiations_by_arm "
                        "for initiation-billed adverse events."
                    )
                new_initiators = float(prep_initiations_by_arm.get(arm_key, 0))
                u_imp, c_imp  = self._item_initiation(item, new_initiators)

            else:
                # Should not be reached: validator now rejects any billing_moment
                # other than "ongoing" or "initiation" for AE items.
                # "diagnosis" costs belong in disease_costs (economics.py).
                raise ValueError(
                    f"AE item '{item_id}': unhandled billing_moment "
                    f"'{billing_moment}'. Only 'ongoing' and 'initiation' are "
                    f"supported by the AE module."
                )

            total_util += u_imp
            total_cost += c_imp
            by_item[item_id] = {
                "utility_decrement": u_imp,
                "cost_addition":     c_imp,
            }

        # ── Update delta-tracking state AFTER all items processed ─────────────
        for arm_key, count in current_users_by_arm.items():
            self._prev_ae_users[arm_key] = int(count)

        return {
            "utility_decrement": total_util,
            "cost_addition":     total_cost,
            "by_item":           by_item,
        }

    def _item_prevalence_ongoing(
        self,
        item: Dict,
        users: float,
        step_fraction: float,
    ) -> Tuple[float, float]:
        """
        Prevalence mode impact for a single ongoing AE item.

        Formula:
          utility_impact = abs(utility_decrement) × prevalence × users × step_fraction
          cost_impact    = cost × prevalence × users × step_fraction
        """
        if users <= 0:
            return 0.0, 0.0

        prevalence = extract_value(item.get("prevalence", {"value": 0.0}))
        util_dec   = extract_value(item.get("utility_decrement", {"value": 0.0}))
        cost       = extract_value(item.get("cost", {"value": 0.0}))

        u_impact = abs(util_dec) * prevalence * users * step_fraction
        c_impact = cost          * prevalence * users * step_fraction
        return u_impact, c_impact

    def _item_state_machine_ongoing(
        self,
        item: Dict,
        users: float,
        step_fraction: float,
    ) -> Tuple[float, float]:
        """
        State machine impact for a single ongoing AE item.

        Reads onset/resolution rates and severity distributions from
        item["state_machine_params"]. Tracks per-item AE state in ae_state_v2.
        """
        if users <= 0:
            return 0.0, 0.0

        item_id   = item["id"]
        sm_params = item.get("state_machine_params", {})

        onset_rate_q      = min(1.0, max(0.0,
            sm_params.get("onset_rate_per_year", 0.0) * step_fraction
        ))
        resolution_rate_q = min(1.0, max(0.0,
            sm_params.get("resolution_rate_per_year", 0.0) * step_fraction
        ))
        severity_dist     = sm_params.get("severity_distribution", {})
        utility_decrements = sm_params.get("utility_decrements", {})
        cost_additions     = sm_params.get("cost_additions", {})

        state      = self.ae_state_v2.get(item_id, {sev: 0 for sev in _SEVERITY_GRADES})
        current_ae = sum(state.values())
        non_ae     = max(0, int(users) - current_ae)

        # ── Stochastic / deterministic draws ─────────────────────────────────
        if self.deterministic:
            new_ae   = int(non_ae     * onset_rate_q)
            resolved = int(current_ae * resolution_rate_q)
        else:
            new_ae   = int(self.rng.binomial(non_ae,     onset_rate_q))      if non_ae     > 0 else 0
            resolved = int(self.rng.binomial(current_ae, resolution_rate_q)) if current_ae > 0 else 0

        # ── Distribute new AE into severity grades ────────────────────────────
        if new_ae > 0 and severity_dist:
            remaining = new_ae
            for i, sev in enumerate(_SEVERITY_GRADES):
                frac = severity_dist.get(sev, 0.0)
                remaining_grades = _SEVERITY_GRADES[i:]
                denom = sum(severity_dist.get(s, 0.0) for s in remaining_grades)
                if frac > 0 and remaining > 0 and denom > 0:
                    n = min(int(remaining * frac / denom), remaining)
                    state[sev] += n
                    remaining  -= n
            if remaining > 0:
                state["mild"] += remaining  # assign leftover to mild

        # ── Apply resolutions proportionally ──────────────────────────────────
        if resolved > 0 and current_ae > 0:
            for sev in _SEVERITY_GRADES:
                if state[sev] > 0:
                    frac = state[sev] / current_ae
                    to_remove = min(int(resolved * frac), state[sev])
                    state[sev] -= to_remove

        self.ae_state_v2[item_id] = state

        # ── Compute cost/utility from current state ───────────────────────────
        total_u = 0.0
        total_c = 0.0
        for sev in _SEVERITY_GRADES:
            count = state[sev]
            if count <= 0:
                continue
            u_dec  = utility_decrements.get(sev, 0.0)
            c_add  = cost_additions.get(sev, 0.0)
            total_u += abs(u_dec) * count * step_fraction
            total_c += c_add     * count * step_fraction

        return total_u, total_c

    def _item_initiation(self, item: Dict, new_initiators: float) -> Tuple[float, float]:
        """
        Compute one-time AE cost/utility for new PrEP initiators.

        Initiation items are not annualised (no step_fraction) — they fire once
        per new initiator on the step they appear.

        Formula:
          affected   = new_initiators × prevalence
          util_impact = abs(utility_decrement) × affected
          cost_impact = cost × affected
        """
        if new_initiators <= 0:
            return 0.0, 0.0

        prevalence = extract_value(item.get("prevalence", {"value": 0.0}))
        util_dec   = extract_value(item.get("utility_decrement", {"value": 0.0}))
        cost       = extract_value(item.get("cost", {"value": 0.0}))

        affected  = new_initiators * prevalence
        u_impact  = abs(util_dec) * affected
        c_impact  = cost          * affected
        return u_impact, c_impact

    def _init_state_machine_v2(self) -> None:
        """
        Initialise per-item AE state arrays for v2 state machine mode.

        Validates that every ongoing item has state_machine_params. Raises ValueError
        at init time so configuration errors surface before the simulation runs.
        """
        self.ae_state_v2: Dict[str, Dict[str, int]] = {}
        items = self._v2_config.get("adverse_events", {}).get("items", [])

        for item in items:
            billing_moment = item.get("billing_moment", "ongoing")
            if billing_moment != "ongoing":
                continue  # initiation / diagnosis items need no state array

            item_id = item.get("id", "unknown")
            if "state_machine_params" not in item:
                raise ValueError(
                    f"AE item '{item_id}': mode='state_machine' requires a "
                    f"'state_machine_params' block but none was found. "
                    f"Add state_machine_params or switch to mode='prevalence'."
                )
            self.ae_state_v2[item_id] = {sev: 0 for sev in _SEVERITY_GRADES}
