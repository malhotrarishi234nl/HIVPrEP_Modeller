"""
Scenario Comparator for HTA Economic Evaluation Module.

Implements Dutch ZIN guidelines for cost-effectiveness analysis of HIV prevention strategies
(no_prep, oral_prep, lenacapavir) in the Netherlands. Handles sequential analysis, ICER
computation with dominance detection, price sensitivity analysis, and threshold pricing.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union, Tuple
import pandas as pd
import numpy as np


@dataclass
class ScenarioResult:
    """
    Container for one arm's economic and epidemiological outputs.

    Attributes:
        name: Intervention name (e.g., "no_prep", "oral_prep", "lenacapavir").
        analysis_scenario: Analysis scenario label (e.g., "scenario_a_base").
        total_qalys_disc: Discounted total QALYs over horizon.
        total_qalys_undisc: Undiscounted total QALYs.
        total_costs_disc: Discounted total costs (EUR).
        total_costs_undisc: Undiscounted total costs.
        cost_breakdown_disc: Breakdown of discounted costs by category.
            Keys: "drug", "monitoring", "hiv_care", etc.
        cumul_new_infections: Cumulative incident infections over horizon.
        timestep_records: List of per-timestep records for sensitivity analysis.
        simulation_records: List of per-timestep simulation dynamics records
            (V11.8.0). Each dict contains one row of engine.outputs for that
            arm's evaluation horizon. Used to produce transparency tabs in the
            HTA Excel report. Pass simulation_records=[] for synthetic/test results.
    """
    name: str
    analysis_scenario: str
    total_qalys_disc: float
    total_qalys_undisc: float
    total_costs_disc: float
    total_costs_undisc: float
    cost_breakdown_disc: Dict[str, float] = field(default_factory=dict)
    cumul_new_infections: int = 0
    timestep_records: List[dict] = field(default_factory=list)
    simulation_records: List[dict] = field(default_factory=list)  # V11.8.0

    # ── Societal perspective fields (BACKLOG-20) ──────────────────────────────
    # All default to 0.0 / {} so that existing call sites that do not supply
    # these arguments remain fully valid. ScenarioComparator selects the
    # appropriate cost total via its own perspective flag.
    #
    # total_costs_disc_societal:     payer costs + productivity losses + patient time
    # total_societal_additions_disc: non-payer societal additions only (productivity + time)
    # societal_breakdown_disc:       per-category breakdown of societal additions only
    #                                Keys: "productivity_loss", "patient_time"
    total_costs_disc_societal:     float = 0.0
    total_societal_additions_disc: float = 0.0
    societal_breakdown_disc:       Dict[str, float] = field(default_factory=dict)


class ScenarioComparator:
    """
    Compares economic outcomes across HIV prevention strategies.

    Implements:
    - Pairwise cost-effectiveness analysis with ICER computation.
    - Sequential analysis per ZIN: no_prep → oral_prep → lenacapavir.
    - Dominance detection and dominated strategy exclusion.
    - Price sensitivity analysis (scenario D).
    - Threshold price calculation via binary search.

    WTP threshold: must be supplied explicitly (no default). ZIN 2022 values
    by disease burden category — €20,000 (low), €50,000 (medium), €80,000/QALY
    (high — HIV/AIDS is classified as high burden per ZIN "Kosteneffectiviteit
    in de praktijk", 2015).
    """

    # Tolerance for QALY difference (< this is treated as zero)
    QALY_TOLERANCE = 1e-6

    def __init__(self, wtp_threshold: float, step_fraction: float,
                 perspective: str = "payer"):
        """
        Initialize the comparator.

        Args:
            wtp_threshold: Willingness-to-pay in EUR/QALY.
                           Must be provided explicitly — read from
                           hta_config["wtp_threshold_eur_per_qaly"].
                           ZIN 2022 values by disease burden category:
                             €20,000/QALY (low), €50,000/QALY (medium),
                             €80,000/QALY (high — HIV/AIDS is classified high
                             per ZIN "Kosteneffectiviteit in de praktijk", 2015).
                           No default is provided; omitting this argument
                           raises TypeError to prevent silent use of a wrong value.
            step_fraction: Fraction of a year per simulation timestep
                           (e.g., 0.25 for quarterly). Must match the value
                           used in EconomicsAccumulator — a cross-check is
                           performed against stored timestep records.
                           V11.7.2: replaces hardcoded 0.25 in _compute_person_years().
            perspective:   "payer" or "societal". Controls which cost total is used
                           when computing delta_cost and the ICER in compare_pair().
                             "payer"    → uses ScenarioResult.total_costs_disc
                                          (healthcare payer only; default; backward compat)
                             "societal" → uses ScenarioResult.total_costs_disc_societal
                                          (payer + productivity losses + patient time)
                           BACKLOG-20: read from hta_config["perspective"]["primary"].
        """
        if not (0.0 < step_fraction <= 1.0):
            raise ValueError(
                f"ScenarioComparator: step_fraction must be in (0, 1], "
                f"got {step_fraction!r}."
            )
        if perspective not in ("societal", "payer"):
            raise ValueError(
                f"ScenarioComparator: perspective must be 'societal' or 'payer', "
                f"got {perspective!r}."
            )
        self.wtp           = wtp_threshold
        self.step_fraction = step_fraction
        self.perspective   = perspective

    def _compute_person_years(self, result: ScenarioResult) -> float:
        """
        Compute total person-years from timestep records.

        Person-years = sum(total_population × step_fraction) across all steps.
        step_fraction is read from self.step_fraction (V11.7.2: was hardcoded 0.25).

        Cross-check: if a timestep record stores a "step_fraction" key (written by
        EconomicsAccumulator V11.7.2+), this method verifies it matches self.step_fraction.
        A mismatch means the comparator was initialised with a different step size than
        was used to accumulate the results — raises ValueError to prevent silent errors.

        Returns 0.0 if timestep_records is empty or total_population key is absent
        (backward-compatible with ScenarioResult objects from before V11.7.1).
        """
        total = 0.0
        for ts in result.timestep_records:
            record_sf = ts.get("step_fraction")
            if record_sf is not None and abs(record_sf - self.step_fraction) > 1e-9:
                raise ValueError(
                    f"ScenarioComparator step_fraction mismatch: comparator was "
                    f"initialised with step_fraction={self.step_fraction}, but "
                    f"timestep record at step={ts.get('step')} stores "
                    f"step_fraction={record_sf}. The comparator and accumulator "
                    f"must use the same timestep size."
                )
            total += ts.get("total_population", 0.0) * self.step_fraction
        return total

    def compare_pair(self, reference: ScenarioResult,
                     intervention: ScenarioResult) -> Dict:
        """
        Perform pairwise cost-effectiveness comparison.

        Computes incremental outcomes of intervention vs. reference, including ICER,
        net monetary benefit (NMB), dominance classification, and per-person metrics.

        Args:
            reference: Baseline arm (e.g., no_prep or oral_prep).
            intervention: Comparator arm (e.g., oral_prep or lenacapavir).

        Returns:
            dict with keys:
                - delta_qaly: Incremental QALYs (discounted).
                - delta_cost: Incremental costs (discounted).
                - icer: ICER in EUR/QALY, or "Dominant", "Dominated",
                  "No QALY difference".
                - nmb: Net monetary benefit at WTP threshold.
                - infections_averted: Reduction in cumulative infections.
                - dominance: Classification ("dominant", "dominated",
                  "trade-off", "no_diff").
                - comparison: Human-readable comparison label.
                - cost_per_infection_averted: delta_cost / infections_averted,
                  or None if infections_averted <= 0.
                - delta_qaly_per_1000_person_years: (delta_qaly / intv_person_years) * 1000,
                  or None if intervention arm person-year data unavailable.
                  V11.7.2: denominator is intervention arm PY (was average of both arms).
                - incremental_cost_per_person_year: delta_cost / intv_person_years,
                  or None if intervention arm person-year data unavailable.
                  V11.7.2: denominator is intervention arm PY (was average of both arms).
                - ref_person_years: reference arm total person-years (V11.7.2).
                - intv_person_years: intervention arm total person-years (V11.7.2).
        """
        delta_qaly = intervention.total_qalys_disc - reference.total_qalys_disc

        # Route cost selection by perspective (BACKLOG-20).
        # "societal" uses the full societal total (payer + productivity + patient time).
        # "payer"    uses the healthcare-payer-only total (backward-compatible default).
        if self.perspective == "societal":
            delta_cost = (
                intervention.total_costs_disc_societal
                - reference.total_costs_disc_societal
            )
        else:
            delta_cost = intervention.total_costs_disc - reference.total_costs_disc

        infections_averted = reference.cumul_new_infections - intervention.cumul_new_infections

        icer = self.compute_icer(reference, intervention)
        nmb = self.wtp * delta_qaly - delta_cost

        # Determine dominance classification
        if isinstance(icer, str):
            if icer == "Dominant":
                dominance = "dominant"
            elif icer == "Dominated":
                dominance = "dominated"
            else:  # "No QALY difference"
                dominance = "no_diff"
        else:
            dominance = "trade-off"

        comparison = f"{intervention.name}_vs_{reference.name}"

        # ── Per-person metrics ──────────────────────────────────────────────
        # V11.7.2: denominator is intervention arm person-years, not the average.
        # The intervention arm's exposure base is the relevant denominator for
        # incremental metrics — consistent with standard epidemiological rate reporting.
        ref_py  = self._compute_person_years(reference)
        intv_py = self._compute_person_years(intervention)
        py_denominator = intv_py if intv_py > 0 else None

        cost_per_infection_averted = (
            delta_cost / infections_averted
            if infections_averted > 0 else None
        )
        delta_qaly_per_1000_py = (
            (delta_qaly / py_denominator) * 1000.0
            if py_denominator else None
        )
        incremental_cost_per_py = (
            delta_cost / py_denominator
            if py_denominator else None
        )

        return {
            "comparison":                       comparison,
            "delta_cost":                       delta_cost,
            "delta_qaly":                       delta_qaly,
            "icer":                             icer,
            "nmb":                              nmb,
            "infections_averted":               infections_averted,
            "dominance":                        dominance,
            "cost_per_infection_averted":       cost_per_infection_averted,
            "delta_qaly_per_1000_person_years": delta_qaly_per_1000_py,
            "incremental_cost_per_person_year": incremental_cost_per_py,
            "ref_person_years":                 ref_py,   # V11.7.2: per-arm PY for transparency
            "intv_person_years":                intv_py,
        }

    def compute_icer(self, ref: ScenarioResult,
                     intv: ScenarioResult) -> Union[float, str]:
        """
        Compute ICER with robust edge-case handling.

        Dominance rules (following standard HTA practice):
        - If delta_cost < 0 and delta_qaly > 0: intervention is dominant
          (cheaper and more effective).
        - If delta_cost > 0 and delta_qaly < 0: intervention is dominated
          (more expensive and less effective).
        - If |delta_qaly| < QALY_TOLERANCE: no meaningful QALY difference.
        - Else: ICER = delta_cost / delta_qaly.

        Args:
            ref: Reference arm.
            intv: Intervention arm.

        Returns:
            float ICER (EUR/QALY) or string ("Dominant", "Dominated",
            "No QALY difference").
        """
        delta_qaly = intv.total_qalys_disc - ref.total_qalys_disc

        # Route cost by perspective — mirrors the selection in compare_pair() (BACKLOG-20).
        if self.perspective == "societal":
            delta_cost = intv.total_costs_disc_societal - ref.total_costs_disc_societal
        else:
            delta_cost = intv.total_costs_disc - ref.total_costs_disc

        # Dominance: cheaper and better
        if delta_cost < 0 and delta_qaly > 0:
            return "Dominant"

        # Dominance: more expensive and worse
        if delta_cost > 0 and delta_qaly < 0:
            return "Dominated"

        # No meaningful QALY difference
        if abs(delta_qaly) < self.QALY_TOLERANCE:
            return "No QALY difference"

        # Standard ICER
        return delta_cost / delta_qaly

    def build_comparison_table(self, results: Dict[str, ScenarioResult]) -> pd.DataFrame:
        """
        Build a cost-effectiveness comparison table for sequential analysis.

        Follows Dutch ZIN sequential ordering:
        1. Order strategies by discounted cost (ascending).
        2. Compute ICERs between adjacent non-dominated strategies.
        3. Exclude dominated strategies from further comparison.

        For a typical 3-arm analysis:
        - Rows: oral_vs_no_prep, lena_vs_oral, lena_vs_no_prep
        - But if oral is dominated by no_prep, skip it and compare
          lenacapavir directly against no_prep.

        Args:
            results: Dict mapping arm names to ScenarioResult objects.
                Expected keys: "no_prep", "oral_prep", "lenacapavir".

        Returns:
            DataFrame with columns:
                - comparison
                - delta_cost
                - delta_qaly
                - icer
                - nmb
                - infections_averted
                - dominance
        """
        # Scenario B expansion is an add-on strategy: the primary decision is
        # oral_prep_plus_lena vs oral_prep. Keep that pair even when oral PrEP
        # is dominated by no PrEP, otherwise the report hides the mixed strategy.
        if "oral_prep_plus_lena" in results:
            comparisons = []
            if "no_prep" in results and "oral_prep" in results:
                comparisons.append(
                    self.compare_pair(results["no_prep"], results["oral_prep"])
                )
            if "oral_prep" in results:
                comparisons.append(
                    self.compare_pair(
                        results["oral_prep"], results["oral_prep_plus_lena"]
                    )
                )
            if "no_prep" in results:
                comparisons.append(
                    self.compare_pair(
                        results["no_prep"], results["oral_prep_plus_lena"]
                    )
                )
            return pd.DataFrame(comparisons)

        if "oral_prep_expansion" in results and "oral_prep" in results:
            return pd.DataFrame([
                self.compare_pair(results["oral_prep"], results["oral_prep_expansion"])
            ])

        # Standard order per ZIN: no_prep (reference) → oral_prep → lenacapavir
        ordered_arms = ["no_prep", "oral_prep", "lenacapavir"]

        # Filter to arms present in results
        available_arms = [arm for arm in ordered_arms if arm in results]

        if len(available_arms) < 2:
            # Need at least 2 arms for comparison
            return pd.DataFrame()

        # Build sequential pairs, excluding dominated strategies
        comparisons = []
        non_dominated = [available_arms[0]]  # Start with reference

        for i in range(1, len(available_arms)):
            arm = available_arms[i]
            ref_arm = non_dominated[-1]

            comparison = self.compare_pair(results[ref_arm], results[arm])
            comparisons.append(comparison)

            # Exclude dominated strategies from further comparisons
            if comparison["dominance"] != "dominated":
                non_dominated.append(arm)

        # V12.9.1: Always include lenacapavir_vs_oral_prep as the primary
        # policy comparison, regardless of whether oral PrEP was sequentially
        # excluded due to dominance.  This is the central thesis question and
        # must appear in the CEA Results Summary even when the ZIN sequential
        # chain skips it.  The row is flagged is_primary_comparison=True so
        # the report writer can highlight it distinctly.
        generated_pairs = {c["comparison"] for c in comparisons}
        if (
            "lenacapavir" in available_arms
            and "oral_prep" in available_arms
        ):
            if "lenacapavir_vs_oral_prep" not in generated_pairs:
                primary = self.compare_pair(
                    results["oral_prep"], results["lenacapavir"]
                )
                primary["is_primary_comparison"] = True
                comparisons.append(primary)
            else:
                # Already generated normally — just flag it
                for c in comparisons:
                    if c["comparison"] == "lenacapavir_vs_oral_prep":
                        c["is_primary_comparison"] = True

        return pd.DataFrame(comparisons)

    @staticmethod
    def _get_lena_price_sensitive_cost(result: ScenarioResult) -> float:
        """
        Return the discounted Lenacapavir prevention product bundle cost.

        Price sensitivity must vary only Lenacapavir acquisition costs
        (injection product plus year-1 oral loading), not the broad ``drug``
        category, which also contains HIV ART treatment drugs.
        """
        value = result.cost_breakdown_disc.get("prep_drug_lenacapavir")
        if value is None or value <= 0:
            raise ValueError(
                "lenacapavir price sensitivity requires a positive "
                "cost_breakdown_disc['prep_drug_lenacapavir']; refusing to "
                "scale broad 'drug' costs because that category includes HIV ART."
            )
        return value

    @staticmethod
    def _with_scaled_lena_product_cost(
        lena: ScenarioResult,
        original_lena_product_cost: float,
        new_lena_product_cost: float,
    ) -> ScenarioResult:
        """Return a synthetic Lenacapavir result with only product spend changed."""
        delta_product_cost = new_lena_product_cost - original_lena_product_cost
        new_payer_total = lena.total_costs_disc + delta_product_cost
        new_breakdown = dict(lena.cost_breakdown_disc)
        new_breakdown["prep_drug_lenacapavir"] = new_lena_product_cost
        if "prep_drug" in new_breakdown:
            new_breakdown["prep_drug"] = (
                new_breakdown.get("prep_drug", 0.0) + delta_product_cost
            )
        if "drug" in new_breakdown:
            new_breakdown["drug"] = (
                new_breakdown.get("drug", 0.0) + delta_product_cost
            )

        return ScenarioResult(
            name=lena.name,
            analysis_scenario=lena.analysis_scenario,
            total_qalys_disc=lena.total_qalys_disc,
            total_qalys_undisc=lena.total_qalys_undisc,
            total_costs_disc=new_payer_total,
            total_costs_undisc=lena.total_costs_undisc,
            cost_breakdown_disc=new_breakdown,
            cumul_new_infections=lena.cumul_new_infections,
            timestep_records=lena.timestep_records,
            simulation_records=[],
            total_costs_disc_societal=(
                new_payer_total + lena.total_societal_additions_disc
            ),
            total_societal_additions_disc=lena.total_societal_additions_disc,
            societal_breakdown_disc=dict(lena.societal_breakdown_disc),
        )

    def price_sensitivity(self, results: Dict[str, ScenarioResult],
                         price_range: List[float],
                         base_annual_price: float,
                         comparator_arm: str = "oral_prep",
                         intervention_arm: Optional[str] = None) -> pd.DataFrame:
        """
        Conduct price sensitivity analysis (Scenario D).

        For each price point, scales the discounted Lenacapavir prevention
        product bundle cost proportionally to the new annual price and recomputes
        ICER vs. oral PrEP. Used to identify the cost-effectiveness frontier.

        Scaling rule:
            price_ratio     = new_annual_price / base_annual_price
            new_lena_product_cost = original_lena_product_cost_disc * price_ratio
            new_total_disc = total_costs_disc + (new_lena_product_cost − original_lena_product_cost_disc)

        This preserves dimensional consistency: both price_range values and
        base_annual_price are annual per-person costs (EUR/year), while
        original_lena_product_cost_disc is the total discounted aggregate spend
        accumulated across all persons and all timesteps. The ratio cancels the
        per-person-year denominator, producing a correct proportional scaling of
        the aggregate.

        Args:
            results:           Dict mapping arm names to ScenarioResult (must include
                               "lenacapavir" and "oral_prep").
            price_range:       List of annual per-person prices to test (EUR/year).
            base_annual_price: The baseline annual per-person drug cost used in the
                               simulation (EUR/year). Read from
                               hta_config["prevention_costs"]["arm_specific"]
                               ["lenacapavir"]["injection_subsequent"]["value"].

        Returns:
            DataFrame with columns:
                - price                     — annual per-person price tested (EUR/year)
                - delta_cost                — incremental discounted cost vs oral PrEP (EUR)
                - delta_qaly                — incremental discounted QALYs (constant — price
                                              does not affect transmission dynamics)
                - icer                      — ICER (EUR/QALY), "Dominant", or "Dominated"
                - nmb                       — net monetary benefit at WTP threshold (EUR)
                - infections_averted        — reduction in cumulative infections vs oral PrEP
                                              (constant across rows)
                - cost_per_infection_averted — delta_cost / infections_averted (EUR), or None
                - cost_effective            — bool: NMB > 0.  Correct in all four quadrants
                                              of the CE plane; the legacy ICER ≤ WTP rule
                                              fails in the SW quadrant.
                  Reference: Drummond et al. (2015) Methods for the Economic Evaluation
                  of Health Care Programmes, 4th ed., Ch. 4.
        """
        if intervention_arm is None:
            intervention_arm = (
                "lenacapavir" if "lenacapavir" in results else "oral_prep_plus_lena"
            )

        if intervention_arm not in results:
            raise ValueError(
                f"price_sensitivity requires intervention arm '{intervention_arm}' in results"
            )

        if comparator_arm not in results:
            raise ValueError(
                f"price_sensitivity requires comparator arm '{comparator_arm}' in results"
            )

        if base_annual_price <= 0:
            raise ValueError(
                f"base_annual_price must be positive, got {base_annual_price}"
            )

        lena = results[intervention_arm]
        oral = results[comparator_arm]

        # Total discounted Lenacapavir product bundle cost accumulated across
        # all persons × all timesteps. This is NOT the annual per-person cost.
        original_lena_product_cost = self._get_lena_price_sensitive_cost(lena)

        rows = []

        for price in price_range:
            # Ratio of new annual price to base annual price — dimensionless.
            # Applying this ratio to the aggregate Lenacapavir product cost scales
            # the total spend without conflating per-person-year and aggregate units.
            price_ratio = price / base_annual_price

            new_lena_product_cost = original_lena_product_cost * price_ratio

            # Product price only affects payer costs; societal additions
            # (productivity loss, patient time) are unchanged across price points.
            lena_modified = self._with_scaled_lena_product_cost(
                lena,
                original_lena_product_cost,
                new_lena_product_cost,
            )

            # Compute incremental analysis
            comparison = self.compare_pair(oral, lena_modified)

            # Determine cost-effectiveness via NMB — correct in all four
            # quadrants of the cost-effectiveness plane.  The legacy rule
            # (ICER <= WTP) is equivalent to NMB > 0 only in the NE quadrant;
            # it produces the wrong answer in the SW quadrant (cheaper but
            # fewer QALYs) because the ICER inequality reverses there.
            cost_effective = comparison["nmb"] > 0

            rows.append({
                "price":                     price,
                "delta_cost":                comparison["delta_cost"],
                "delta_qaly":                comparison["delta_qaly"],
                "icer":                      comparison["icer"],
                "nmb":                       comparison["nmb"],
                "infections_averted":        comparison["infections_averted"],
                "cost_per_infection_averted": comparison["cost_per_infection_averted"],
                "cost_effective":            cost_effective,
            })

        return pd.DataFrame(rows)

    def find_threshold_price(self, results: Dict[str, ScenarioResult],
                            wtp: Optional[float] = None,
                            base_annual_price: float = None,
                            comparator_arm: str = "oral_prep",
                            intervention_arm: Optional[str] = None) -> Optional[float]:
        """
        Find the lenacapavir price at which NMB = 0 (the cost-effectiveness
        threshold price), expressed as an annual per-person cost (EUR/year).

        Uses binary search on NMB to identify the price where lenacapavir
        transitions from cost-effective (NMB > 0) to not cost-effective
        (NMB <= 0) relative to oral PrEP at the given WTP.

        Scaling rule (same as price_sensitivity):
            price_ratio     = price_annual / base_annual_price
            new_lena_product_cost = original_lena_product_cost_disc * price_ratio

        The search space is in annual per-person price (EUR/year).
        Upper bound = base_annual_price × 10.

        The search direction is governed by NMB > 0, not ICER <= WTP.  These
        are equivalent in the NE quadrant but have opposite ordering in the SW
        quadrant (cheaper but fewer QALYs), where the ICER-based rule would
        drive the binary search in the wrong direction.

        Returns None if:
        - Lenacapavir is never cost-effective (NMB <= 0 even at zero drug cost).
        - Lenacapavir is cost-effective throughout the search range (no finite
          threshold exists within 0 to base_annual_price × 10).

        Args:
            results:           Dict mapping arm names to ScenarioResult.
            wtp:               Willingness-to-pay threshold (default: self.wtp).
            base_annual_price: Baseline annual per-person drug cost (EUR/year).
                               Read from hta_config injection_subsequent value.

        Returns:
            float: Threshold annual price in EUR/year, or None.
        """
        if wtp is None:
            wtp = self.wtp

        if base_annual_price is None or base_annual_price <= 0:
            raise ValueError(
                "find_threshold_price requires a positive base_annual_price "
                "(EUR/year) — read from hta_config injection_subsequent value"
            )

        if intervention_arm is None:
            intervention_arm = (
                "lenacapavir" if "lenacapavir" in results else "oral_prep_plus_lena"
            )

        if intervention_arm not in results or comparator_arm not in results:
            raise ValueError(
                "find_threshold_price requires comparator and intervention arms "
                f"('{comparator_arm}', '{intervention_arm}')"
            )

        lena = results[intervention_arm]
        oral = results[comparator_arm]

        original_lena_product_cost = self._get_lena_price_sensitive_cost(lena)

        # ── NMB helper: compute NMB at a given annual price using the local wtp ──
        # IMPORTANT: self.compare_pair() hardcodes self.wtp, so it cannot be used
        # here when a caller passes a tier-specific wtp (e.g. 20_000 or 50_000).
        # Instead we compute NMB directly, respecting perspective consistently with
        # compare_pair() so that societal-perspective runs are also handled correctly.
        if self.perspective == "societal":
            lena_base_cost = lena.total_costs_disc_societal
            oral_base_cost = oral.total_costs_disc_societal
        else:
            lena_base_cost = lena.total_costs_disc
            oral_base_cost = oral.total_costs_disc

        delta_qaly = lena.total_qalys_disc - oral.total_qalys_disc
        # delta_cost_at_base_price (price-independent part + product-cost shift):
        #   new_lena_cost = lena_base_cost + (annual_price/base_annual_price - 1) * original_lena_product_cost
        #   delta_cost    = new_lena_cost - oral_base_cost
        _lena_non_product_cost = lena_base_cost - original_lena_product_cost

        def _nmb_at_price(annual_price: float) -> float:
            """NMB at a given annual per-person price using the caller-supplied wtp."""
            new_product = original_lena_product_cost * (annual_price / base_annual_price)
            delta_cost = (_lena_non_product_cost + new_product) - oral_base_cost
            return wtp * delta_qaly - delta_cost

        # Check if lenacapavir is cost-effective even at zero drug cost
        if _nmb_at_price(0.0) <= 0:
            return None  # Never cost-effective even at zero drug cost

        # Define bounds in annual per-person price space (EUR/year)
        price_lower = 0.0
        price_upper = base_annual_price * 10  # 10× base annual price as upper bound

        # Check that threshold exists within the search range
        if _nmb_at_price(price_upper) > 0:
            return None  # Cost-effective throughout range; no finite threshold found

        # Binary search for threshold
        tolerance = 1.0  # EUR/year tolerance
        max_iterations = 50
        iteration = 0

        while iteration < max_iterations:
            price_mid = (price_lower + price_upper) / 2.0

            # Binary search direction driven by NMB sign — correct in all
            # four quadrants.  If NMB > 0 the current price is still below
            # the threshold (raise lower bound); otherwise it is above the
            # threshold (lower upper bound).
            if _nmb_at_price(price_mid) > 0:
                price_lower = price_mid
            else:
                price_upper = price_mid

            # Check convergence
            if abs(price_upper - price_lower) < tolerance:
                return price_lower

            iteration += 1

        return price_lower

    def summary_table(self, results: Dict[str, ScenarioResult]) -> pd.DataFrame:
        """
        Generate a summary table of all arms.

        Args:
            results: Dict mapping arm names to ScenarioResult.

        Returns:
            DataFrame with columns:
                - arm
                - total_costs_disc
                - total_qalys_disc
                - cost_per_qaly (average)
                - cumul_infections
        """
        rows = []
        for arm_name, result in results.items():
            cost_per_qaly = (
                result.total_costs_disc / result.total_qalys_disc
                if result.total_qalys_disc > 0.01 else np.inf  # V11.7.2: stricter guard
            )
            rows.append({
                "arm": arm_name,
                "total_costs_disc": result.total_costs_disc,
                "total_qalys_disc": result.total_qalys_disc,
                "cost_per_qaly": cost_per_qaly,
                "cumul_infections": result.cumul_new_infections,
            })

        return pd.DataFrame(rows)
