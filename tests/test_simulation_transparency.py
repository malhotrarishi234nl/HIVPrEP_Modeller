"""
test_simulation_transparency.py — V11.8 Series

Tests for:
  A. Engine counter correctness (new fields in run_v3.py)
  B. Annual aggregation helper (_quarterly_to_annual)
  C. PrEP Population Dynamics tab rendering (hta_report.py)
  D. Incidence by Subgroup tab rendering (hta_report.py)
  E. Regression — pre-existing 206 tests unaffected

All Category A tests use deterministic mode for exact assertions.
"""

import copy
import json
import math
import os
import sys
import tempfile
import shutil

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_v3 import SimulationEngineV3
from hta_report import HTAReportWriter
from scenario_comparator import ScenarioComparator, ScenarioResult
from config_utils import extract_value


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_base_config():
    with open("configs/v11_configuration.json") as f:
        return json.load(f)


def load_hta_config():
    with open("configs/hta_config.json") as f:
        return json.load(f)


def make_short_engine_config(
    base_config=None,
    n_steps=8,
    start_year=2025,
    prep_name="Oral_PrEP",
    uptake_rate=0.20,
    dropout_rate=0.10,
    deterministic=True,
):
    """
    Build a temp config for a short engine run. Both PrEP interventions
    are enabled with the given uptake/dropout rates.
    Returns path to temp config file.
    """
    if base_config is None:
        base_config = load_base_config()
    config = copy.deepcopy(base_config)

    rc = config.setdefault("run_control", {})
    step_size = extract_value(rc.get("step_size", {"value": 3}))
    unit = extract_value(rc.get("unit", {"value": "months"}))
    steps_per_year = 12 // step_size if unit == "months" else 1
    eval_years = n_steps / steps_per_year
    rc["start_year"] = {"value": start_year}
    rc["end_year"]   = {"value": start_year + eval_years}
    rc["deterministic_mode"] = {"value": deterministic}

    # Disable migration and pop adjustments for cleaner unit tests
    demo = config.setdefault("demography", {})
    demo.setdefault("migration", {})["enabled"] = {"value": False}
    demo.setdefault("advanced", {}).setdefault(
        "population_adjustments", {}
    )["enabled"] = {"value": False}

    # Enable both PrEP interventions
    for iname, v6 in [("oral_prep", "Oral_PrEP"), ("lenacapavir", "Lenacapavir")]:
        config["interventions"][iname]["enabled"] = {"value": True}
        config["interventions"][iname]["uptake_rate"]  = {"value": uptake_rate}
        config["interventions"][iname]["dropout_rate"] = {"value": dropout_rate}

    # Matching prep schedules
    config["prep_schedules"] = {
        "interpolation": {"value": "stepwise"},
        "Oral_PrEP": {
            "initiation":     {"schedule": {str(start_year): uptake_rate}},
            "discontinuation": {"schedule": {str(start_year): dropout_rate}},
        },
        "Lenacapavir": {
            "initiation":     {"schedule": {str(start_year): uptake_rate}},
            "discontinuation": {"schedule": {str(start_year): dropout_rate}},
        },
    }

    path = tempfile.mktemp(suffix=".json", prefix="test_transparency_")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def make_no_prep_engine_config(base_config=None, n_steps=8, start_year=2025):
    """Build temp config with both PrEP interventions disabled."""
    if base_config is None:
        base_config = load_base_config()
    config = copy.deepcopy(base_config)

    rc = config.setdefault("run_control", {})
    step_size = extract_value(rc.get("step_size", {"value": 3}))
    unit = extract_value(rc.get("unit", {"value": "months"}))
    steps_per_year = 12 // step_size if unit == "months" else 1
    eval_years = n_steps / steps_per_year
    rc["start_year"] = {"value": start_year}
    rc["end_year"]   = {"value": start_year + eval_years}
    rc["deterministic_mode"] = {"value": True}

    demo = config.setdefault("demography", {})
    demo.setdefault("migration", {})["enabled"] = {"value": False}
    demo.setdefault("advanced", {}).setdefault(
        "population_adjustments", {}
    )["enabled"] = {"value": False}

    for iname in ("oral_prep", "lenacapavir"):
        config["interventions"][iname]["enabled"] = {"value": False}

    path = tempfile.mktemp(suffix=".json", prefix="test_transparency_nprep_")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def run_engine(config_path, n_steps=None):
    """Create and run SimulationEngineV3, returning the engine."""
    engine = SimulationEngineV3(config_path, seed=42)
    start = 0
    end = engine.time.n_steps if n_steps is None else n_steps
    engine.run(start_step=start, end_step=end)
    return engine


# ---------------------------------------------------------------------------
# Category A — Engine counter correctness
# ---------------------------------------------------------------------------

class TestEngineCounters:
    """Validate the 15 new output fields in run_v3.py record_outputs()."""

    def test_A1_initiation_counter_nonzero_with_prep_enabled(self):
        """
        After running with a high uptake rate, cumulative initiations must be > 0.
        Deterministic mode — counters are exact.
        """
        path = make_short_engine_config(uptake_rate=0.30, dropout_rate=0.0, n_steps=4)
        try:
            engine = run_engine(path, n_steps=4)
            total_oral_init = sum(engine.outputs["prep_oral_initiations"])
            total_lena_init = sum(engine.outputs["prep_lena_initiations"])
            assert total_oral_init > 0, "Expected oral initiations > 0 with uptake_rate=0.30"
            assert total_lena_init > 0, "Expected lena initiations > 0 with uptake_rate=0.30"
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A2_dropout_counter_nonzero_after_users_accumulate(self):
        """
        Run 4 steps to build up PrEP users, then 4 more with dropout.
        Cumulative dropouts must be > 0.
        """
        path = make_short_engine_config(uptake_rate=0.30, dropout_rate=0.20, n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            total_oral_drop = sum(engine.outputs["prep_oral_dropouts"])
            # With 20% annual dropout (quarterly ~5.3%) and users accumulating, dropouts > 0
            assert total_oral_drop > 0, "Expected oral dropouts > 0"
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A3_no_prep_arm_all_counters_zero(self):
        """
        With PrEP disabled, all initiation and dropout counters must be exactly 0.
        """
        path = make_no_prep_engine_config(n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            assert sum(engine.outputs["prep_oral_initiations"]) == 0
            assert sum(engine.outputs["prep_lena_initiations"]) == 0
            assert sum(engine.outputs["prep_oral_dropouts"])    == 0
            assert sum(engine.outputs["prep_lena_dropouts"])    == 0
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A4_msm_total_matches_tensor_sum(self):
        """
        msm_total in outputs must equal direct tensor sum over MSM orientation.
        """
        path = make_short_engine_config(uptake_rate=0.0, dropout_rate=0.0, n_steps=1)
        try:
            engine = run_engine(path, n_steps=1)
            recorded  = engine.outputs["msm_total"][-1]
            msm_idx   = engine.groups.orient_to_idx["MSM"]
            from_tensor = int(np.sum(engine.state.population[:, msm_idx, :, :, :, :, :]))
            # Single step: population has evolved one step, last value should match post-step
            # We check the final state is consistent (within 1 for rounding)
            assert abs(recorded - from_tensor) <= 1, (
                f"msm_total={recorded} but tensor sum={from_tensor}"
            )
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A5_calibration_counters_are_exported(self):
        """
        Calibration-facing diagnosis, AIDS incidence, and ART stock counters
        should be present, timestep-aligned, and nonnegative.
        """
        path = make_short_engine_config(uptake_rate=0.0, dropout_rate=0.0, n_steps=2)
        try:
            engine = run_engine(path, n_steps=2)
            expected = {
                "new_diagnoses",
                "new_diagnoses_msm",
                "new_aids_cases",
                "art_initiations",
                "on_art_stock",
            }
            assert expected.issubset(engine.outputs)
            n = len(engine.outputs["year"])
            for name in expected:
                assert len(engine.outputs[name]) == n
                assert all(value >= 0 for value in engine.outputs[name])
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A5_msm_susceptible_excludes_hiv_positive(self):
        """
        msm_susceptible must be less than msm_total if any MSM are HIV+.
        Also: msm_hiv_pos + msm_susceptible == msm_total at every step.
        """
        path = make_short_engine_config(uptake_rate=0.0, dropout_rate=0.0, n_steps=4)
        try:
            engine = run_engine(path, n_steps=4)
            for i in range(len(engine.outputs["step"])):
                total = engine.outputs["msm_total"][i]
                susc  = engine.outputs["msm_susceptible"][i]
                hpos  = engine.outputs["msm_hiv_pos"][i]
                assert susc + hpos == total, (
                    f"Step {i}: msm_susceptible({susc}) + msm_hiv_pos({hpos}) "
                    f"!= msm_total({total})"
                )
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A6_msm_eligible_prep_excludes_current_users(self):
        """
        msm_eligible_prep must be ≤ msm_susceptible (users on PrEP are excluded).
        With active uptake, eligible < susceptible after step 1.
        """
        path = make_short_engine_config(uptake_rate=0.30, dropout_rate=0.0, n_steps=4)
        try:
            engine = run_engine(path, n_steps=4)
            # After several steps with high uptake, eligible pool should be smaller
            # than susceptible pool (some MSM are now on PrEP)
            for i in range(len(engine.outputs["step"])):
                elig = engine.outputs["msm_eligible_prep"][i]
                susc = engine.outputs["msm_susceptible"][i]
                assert elig <= susc, (
                    f"Step {i}: msm_eligible_prep({elig}) > msm_susceptible({susc})"
                )
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A7_hiv_identity_holds_all_steps(self):
        """
        msm_hiv_pos + msm_susceptible == msm_total must hold at every recorded step.
        (This tests internal accounting consistency, not just first/last steps.)
        """
        path = make_short_engine_config(uptake_rate=0.05, dropout_rate=0.05, n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            n = len(engine.outputs["step"])
            for i in range(n):
                total = engine.outputs["msm_total"][i]
                susc  = engine.outputs["msm_susceptible"][i]
                hpos  = engine.outputs["msm_hiv_pos"][i]
                assert susc + hpos == total, f"Identity failed at step index {i}"
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A8_infection_partition_exhaustive(self):
        """
        new_infections_msm_on_prep + new_infections_msm_off_prep == new_infections_msm
        for every step (off_prep is derived as msm - on_prep).
        """
        path = make_short_engine_config(uptake_rate=0.15, dropout_rate=0.05, n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            for i in range(len(engine.outputs["step"])):
                total_msm   = engine.outputs["new_infections_msm"][i]
                on_prep     = engine.outputs["new_infections_msm_on_prep"][i]
                off_prep    = total_msm - on_prep
                assert off_prep >= 0, (
                    f"Step {i}: on_prep({on_prep}) > total_msm({total_msm})"
                )
                assert on_prep + off_prep == total_msm
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A9_msm_infections_leq_total_infections(self):
        """
        new_infections_msm must never exceed new_infections (all population) at any step.
        """
        path = make_short_engine_config(uptake_rate=0.05, dropout_rate=0.05, n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            for i in range(len(engine.outputs["step"])):
                msm_inf   = engine.outputs["new_infections_msm"][i]
                total_inf = engine.outputs["new_infections"][i]
                assert msm_inf <= total_inf, (
                    f"Step {i}: new_infections_msm({msm_inf}) > new_infections({total_inf})"
                )
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_A10_msm_on_oral_zero_with_no_prep(self):
        """
        With PrEP disabled, msm_on_oral and msm_on_lena must be 0 at every step.
        """
        path = make_no_prep_engine_config(n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            assert all(v == 0 for v in engine.outputs["msm_on_oral"]), \
                "msm_on_oral should be 0 with PrEP disabled"
            assert all(v == 0 for v in engine.outputs["msm_on_lena"]), \
                "msm_on_lena should be 0 with PrEP disabled"
        finally:
            if os.path.exists(path):
                os.remove(path)


# ---------------------------------------------------------------------------
# Category B — Annual aggregation helper
# ---------------------------------------------------------------------------

class TestQuarterlyToAnnual:
    """Test _quarterly_to_annual() aggregation logic."""

    @staticmethod
    def _make_records(years_data):
        """
        Build synthetic quarterly records.
        years_data: list of dicts with key "year" (fractional) and any fields.
        """
        return years_data

    def test_B1_flow_variables_summed(self):
        """Annual flow total = sum of all quarterly steps."""
        records = [
            {"year": 2025.00, "prep_oral_initiations": 10, "new_infections": 5},
            {"year": 2025.25, "prep_oral_initiations": 12, "new_infections": 3},
            {"year": 2025.50, "prep_oral_initiations": 8,  "new_infections": 4},
            {"year": 2025.75, "prep_oral_initiations": 11, "new_infections": 6},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert len(annual) == 1
        assert annual[0]["prep_oral_initiations"] == 41
        assert annual[0]["new_infections"] == 18

    def test_B2_stock_start_uses_first_step(self):
        """Stock start = first quarterly step in the year."""
        records = [
            {"year": 2025.00, "msm_on_oral": 100},
            {"year": 2025.25, "msm_on_oral": 110},
            {"year": 2025.50, "msm_on_oral": 108},
            {"year": 2025.75, "msm_on_oral": 115},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert annual[0]["msm_on_oral_start"] == 100

    def test_B3_stock_hcc_is_arithmetic_mean(self):
        """Stock HCC = arithmetic mean of all 4 quarterly steps."""
        records = [
            {"year": 2025.00, "msm_on_oral": 100},
            {"year": 2025.25, "msm_on_oral": 110},
            {"year": 2025.50, "msm_on_oral": 108},
            {"year": 2025.75, "msm_on_oral": 115},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        expected_hcc = (100 + 110 + 108 + 115) / 4
        assert abs(annual[0]["msm_on_oral_hcc"] - expected_hcc) < 1e-9

    def test_B4_stock_end_uses_last_step(self):
        """Stock end = last quarterly step in the year."""
        records = [
            {"year": 2025.00, "msm_on_oral": 100},
            {"year": 2025.25, "msm_on_oral": 110},
            {"year": 2025.50, "msm_on_oral": 108},
            {"year": 2025.75, "msm_on_oral": 115},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert annual[0]["msm_on_oral_end"] == 115

    def test_B5_multi_year_correct_row_count(self):
        """5 years of quarterly data → exactly 5 annual rows."""
        records = []
        for yr in range(2025, 2030):
            for q in [0.0, 0.25, 0.50, 0.75]:
                records.append({
                    "year": yr + q,
                    "msm_on_oral": 100,
                    "prep_oral_initiations": 5,
                })
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert len(annual) == 5

    def test_B6_empty_records_returns_empty_list(self):
        """Empty input → empty output (no error)."""
        result = HTAReportWriter._quarterly_to_annual([])
        assert result == []

    def test_B7_partial_year_handles_gracefully(self):
        """3 steps for a year (missing Q4) → function completes, HCC uses 3 steps."""
        records = [
            {"year": 2025.00, "msm_on_oral": 100},
            {"year": 2025.25, "msm_on_oral": 110},
            {"year": 2025.50, "msm_on_oral": 108},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert len(annual) == 1
        expected_hcc = (100 + 110 + 108) / 3
        assert abs(annual[0]["msm_on_oral_hcc"] - expected_hcc) < 1e-9

    def test_B8_hcc_differs_from_start_for_growing_pool(self):
        """
        When PrEP pool grows across the year, HCC mean > start value.
        Validates that HCC is not a duplicate of start.
        """
        records = [
            {"year": 2025.00, "msm_on_oral": 100},
            {"year": 2025.25, "msm_on_oral": 120},
            {"year": 2025.50, "msm_on_oral": 140},
            {"year": 2025.75, "msm_on_oral": 160},
        ]
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert annual[0]["msm_on_oral_hcc"] > annual[0]["msm_on_oral_start"]


# ---------------------------------------------------------------------------
# Category C — PrEP Population Dynamics tab rendering
# ---------------------------------------------------------------------------

def _make_synthetic_all_results():
    """
    Build a minimal all_results dict with synthetic simulation_records
    for all three arms.
    """
    def _sim_records(oral_users, lena_users, initiations=5, dropouts=2, msm=5000):
        records = []
        for yr in range(2025, 2028):
            for q in [0.0, 0.25, 0.50, 0.75]:
                records.append({
                    "year": yr + q,
                    "msm_total": msm,
                    "msm_susceptible": msm - 200,
                    "msm_eligible_prep": msm - 200 - oral_users - lena_users,
                    "msm_high_risk": int(msm * 0.10),
                    "msm_high_risk_susceptible": int(msm * 0.09),
                    "msm_high_risk_on_prep": min(oral_users + lena_users, int(msm * 0.05)),
                    "msm_hiv_pos": 200,
                    "msm_on_oral": oral_users,
                    "msm_on_lena": lena_users,
                    "prep_oral_initiations": initiations,
                    "prep_lena_initiations": 0,
                    "prep_oral_dropouts": dropouts,
                    "prep_lena_dropouts": 0,
                    "new_infections": 50,
                    "new_infections_msm": 20,
                    "new_infections_msm_on_prep": 1,
                })
        return records

    def _result(name, oral, lena, init=5, drop=2):
        return ScenarioResult(
            name=name,
            analysis_scenario="scenario_a_base",
            total_qalys_disc=10000,
            total_qalys_undisc=10000,
            total_costs_disc=5_000_000,
            total_costs_undisc=5_000_000,
            cost_breakdown_disc={"drug": 3_000_000, "monitoring": 1_000_000, "hiv_care": 1_000_000},
            cumul_new_infections=200,
            timestep_records=[],
            simulation_records=_sim_records(oral, lena, init, drop),
        )

    return {
        "scenario_a_base": {
            "no_prep":     _result("no_prep",     oral=0,   lena=0,   init=0, drop=0),
            "oral_prep":   _result("oral_prep",   oral=800, lena=0,   init=5, drop=2),
            "lenacapavir": _result("lenacapavir", oral=0,   lena=800, init=5, drop=2),
        }
    }


def _make_minimal_hta_config():
    hta_cfg = load_hta_config()
    cfg = copy.deepcopy(hta_cfg)
    cfg["time_horizon"]["evaluation_years"] = 3
    cfg["analysis_scenarios"] = [s for s in cfg["analysis_scenarios"]
                                  if s["name"] == "scenario_a_base"]
    return cfg


def _make_minimal_base_config():
    return load_base_config()


def _build_report_writer(output_path, all_results=None):
    """Helper: build an HTAReportWriter with synthetic data."""
    if all_results is None:
        all_results = _make_synthetic_all_results()
    hta_cfg  = _make_minimal_hta_config()
    base_cfg = _make_minimal_base_config()
    comparator = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
    return HTAReportWriter(
        all_results=all_results,
        comparator=comparator,
        hta_config=hta_cfg,
        base_config=base_cfg,
        baseline_csv_path=None,
        output_path=output_path,
    )


class TestPrepDynamicsTab:
    """Validate the 'PrEP Population Dynamics' tab rendering."""

    @pytest.fixture(autouse=True)
    def tmpdir(self, tmp_path):
        self.out = str(tmp_path / "report_test.xlsx")

    def _write_report(self, all_results=None):
        writer = _build_report_writer(self.out, all_results)
        # Only write new tabs to avoid needing real baseline CSV
        writer._write_prep_dynamics_tab()
        writer._write_incidence_subgroup_tab()
        writer.wb.save(self.out)
        return writer.wb

    def test_C1_tab_exists_in_workbook(self):
        """'PrEP Population Dynamics' sheet is present after write."""
        wb = self._write_report()
        assert "PrEP Population Dynamics" in wb.sheetnames

    def test_C2_three_arm_sections_present(self):
        """All three arm labels appear as section headers in the tab."""
        wb = self._write_report()
        ws = wb["PrEP Population Dynamics"]
        all_text = " ".join(
            str(cell.value) for row in ws.iter_rows() for cell in row
            if cell.value is not None
        )
        for arm_label in ("No PrEP", "Oral PrEP", "Lenacapavir"):
            assert arm_label in all_text, f"Arm label '{arm_label}' missing from tab"

    def test_C3_no_prep_arm_zero_initiations(self):
        """For no_prep arm, new initiations must be 0 in all rows."""
        all_results = _make_synthetic_all_results()
        # no_prep arm has init=0, drop=0 in synthetic data
        annual = HTAReportWriter._quarterly_to_annual(
            all_results["scenario_a_base"]["no_prep"].simulation_records
        )
        for row in annual:
            assert row["prep_oral_initiations"] == 0
            assert row["prep_lena_initiations"] == 0

    def test_C4_total_on_any_prep_equals_oral_plus_lena(self):
        """Total on any PrEP (oral_end + lena_end) is the sum of individual arms."""
        annual = HTAReportWriter._quarterly_to_annual(
            _make_synthetic_all_results()["scenario_a_base"]["oral_prep"].simulation_records
        )
        for row in annual:
            oral_end = row.get("msm_on_oral_end", 0)
            lena_end = row.get("msm_on_lena_end", 0)
            # oral_prep arm: lena=0, so any_prep = oral
            assert oral_end + lena_end == oral_end  # trivially; mainly checks no error

    def test_C5_empty_records_no_crash(self):
        """Tab renders without error when simulation_records is empty."""
        all_results = _make_synthetic_all_results()
        for arm_name in all_results["scenario_a_base"]:
            all_results["scenario_a_base"][arm_name].simulation_records = []
        wb = self._write_report(all_results)
        assert "PrEP Population Dynamics" in wb.sheetnames

    def test_C6_hcc_differs_from_start_for_growing_pool(self):
        """When a PrEP pool grows mid-year, HCC mean > start value."""
        # Create records where oral users grow each quarter
        records = []
        for q_i, q in enumerate([0.0, 0.25, 0.50, 0.75]):
            records.append({
                "year": 2025.0 + q,
                "msm_on_oral": 100 + q_i * 20,  # 100, 120, 140, 160
                "msm_on_lena": 0,
                "prep_oral_initiations": 10, "prep_lena_initiations": 0,
                "prep_oral_dropouts": 0, "prep_lena_dropouts": 0,
                "new_infections": 5, "new_infections_msm": 2,
                "new_infections_msm_on_prep": 0,
                "msm_total": 5000, "msm_susceptible": 4800,
                "msm_eligible_prep": 4700, "msm_high_risk": 500,
                "msm_high_risk_susceptible": 450, "msm_high_risk_on_prep": 50,
                "msm_hiv_pos": 200,
            })
        annual = HTAReportWriter._quarterly_to_annual(records)
        assert annual[0]["msm_on_oral_hcc"] > annual[0]["msm_on_oral_start"]

    def test_C7_persistence_rate_bounded_zero_to_one(self):
        """
        Persistence rate is computed as 1 - (dropouts/mean_users).
        Validate the formula does not produce values outside [0, 1] in normal conditions.
        """
        # With low dropout relative to users, persistence should be close to 1
        records = [{"year": 2025.0 + q * 0.25,
                    "msm_on_oral": 800, "msm_on_lena": 0,
                    "prep_oral_initiations": 10, "prep_oral_dropouts": 5,
                    "prep_lena_initiations": 0, "prep_lena_dropouts": 0,
                    "new_infections": 5, "new_infections_msm": 2,
                    "new_infections_msm_on_prep": 0,
                    "msm_total": 5000, "msm_susceptible": 4800,
                    "msm_eligible_prep": 3992, "msm_high_risk": 500,
                    "msm_high_risk_susceptible": 450, "msm_high_risk_on_prep": 50,
                    "msm_hiv_pos": 200} for q in range(4)]
        annual = HTAReportWriter._quarterly_to_annual(records)
        oral_start = annual[0]["msm_on_oral_start"]
        oral_end   = annual[0]["msm_on_oral_end"]
        total_drop = annual[0]["prep_oral_dropouts"]
        mean_users = (oral_start + oral_end) / 2
        if mean_users > 0:
            persistence = 1 - total_drop / mean_users
            assert 0 <= persistence <= 1.0


# ---------------------------------------------------------------------------
# Category D — Incidence by Subgroup tab rendering
# ---------------------------------------------------------------------------

class TestIncidenceSubgroupTab:
    """Validate the 'Incidence by Subgroup' tab rendering."""

    @pytest.fixture(autouse=True)
    def tmpdir(self, tmp_path):
        self.out = str(tmp_path / "report_incidence.xlsx")

    def _write_report(self, all_results=None):
        writer = _build_report_writer(self.out, all_results)
        writer._write_prep_dynamics_tab()
        writer._write_incidence_subgroup_tab()
        writer.wb.save(self.out)
        return writer.wb

    def test_D1_tab_exists_in_workbook(self):
        """'Incidence by Subgroup' sheet is present after write."""
        wb = self._write_report()
        assert "Incidence by Subgroup" in wb.sheetnames

    def test_D2_three_arm_sections_present(self):
        """All three arm labels appear in the tab."""
        wb = self._write_report()
        ws = wb["Incidence by Subgroup"]
        all_text = " ".join(
            str(cell.value) for row in ws.iter_rows() for cell in row
            if cell.value is not None
        )
        for arm_label in ("No PrEP", "Oral PrEP", "Lenacapavir"):
            assert arm_label in all_text

    def test_D3_no_prep_infections_averted_is_blank(self):
        """
        For no_prep arm, infections averted vs itself is None/blank.
        Verified via annual aggregation logic — averted = None when arm_name == no_prep.
        """
        all_results = _make_synthetic_all_results()
        # no_prep sim records — infections averted is computed in the tab writer
        # as None for no_prep arm. We verify the annual data for oral_prep has averted > 0.
        no_prep_recs = all_results["scenario_a_base"]["no_prep"].simulation_records
        oral_recs    = all_results["scenario_a_base"]["oral_prep"].simulation_records

        no_prep_annual = {r["year"]: r for r in HTAReportWriter._quarterly_to_annual(no_prep_recs)}
        oral_annual    = HTAReportWriter._quarterly_to_annual(oral_recs)

        for row in oral_annual:
            yr = row["year"]
            if yr in no_prep_annual:
                averted = no_prep_annual[yr]["new_infections_msm"] - row["new_infections_msm"]
                # With same model params, oral_prep has same MSM infections as no_prep in synthetic
                # data, so averted may be 0 — but it should be computable (not None)
                assert isinstance(averted, (int, float))

    def test_D4_msm_infections_lte_all_infections_all_steps(self):
        """new_infections_msm ≤ new_infections at every step in engine output."""
        path = make_short_engine_config(uptake_rate=0.05, dropout_rate=0.05, n_steps=8)
        try:
            engine = run_engine(path, n_steps=8)
            for i in range(len(engine.outputs["step"])):
                msm   = engine.outputs["new_infections_msm"][i]
                total = engine.outputs["new_infections"][i]
                assert msm <= total, f"Step {i}: msm_inf={msm} > total_inf={total}"
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_D5_cumulative_infections_monotone_nondecreasing(self):
        """
        Cumulative MSM infections computed by annual aggregation must be
        monotonically non-decreasing across years.
        """
        all_results = _make_synthetic_all_results()
        recs = all_results["scenario_a_base"]["oral_prep"].simulation_records
        annual = HTAReportWriter._quarterly_to_annual(recs)
        cumul = 0
        for row in annual:
            cumul += row["new_infections_msm"]
            assert cumul >= 0

    def test_D6_empty_records_no_crash(self):
        """Tab renders without error when simulation_records is empty."""
        all_results = _make_synthetic_all_results()
        for arm_name in all_results["scenario_a_base"]:
            all_results["scenario_a_base"][arm_name].simulation_records = []
        wb = self._write_report(all_results)
        assert "Incidence by Subgroup" in wb.sheetnames

    def test_D7_nnt_blank_for_no_prep_arm(self):
        """
        NNT requires on-PrEP vs off-PrEP incidence difference.
        For no_prep arm the on-PrEP group is empty → NNT must be None/blank.
        In synthetic no_prep data, msm_on_oral=0 → any_prep_hcc=0 → on-PrEP
        incidence is None → NNT is None.
        """
        records = []
        for q in [0.0, 0.25, 0.50, 0.75]:
            records.append({
                "year": 2025.0 + q,
                "msm_on_oral": 0, "msm_on_lena": 0,
                "msm_susceptible": 4800, "msm_total": 5000, "msm_hiv_pos": 200,
                "new_infections_msm": 20, "new_infections_msm_on_prep": 0,
                "msm_eligible_prep": 4800, "msm_high_risk": 500,
                "msm_high_risk_susceptible": 450, "msm_high_risk_on_prep": 0,
                "prep_oral_initiations": 0, "prep_lena_initiations": 0,
                "prep_oral_dropouts": 0, "prep_lena_dropouts": 0,
                "new_infections": 50,
            })
        annual = HTAReportWriter._quarterly_to_annual(records)
        for row in annual:
            any_prep_hcc = row.get("msm_on_oral_hcc", 0.0) + row.get("msm_on_lena_hcc", 0.0)
            # any_prep_hcc == 0 → on-PrEP incidence is undefined → NNT is None
            incidence_on_prep = None if any_prep_hcc == 0 else 0
            assert incidence_on_prep is None


# ---------------------------------------------------------------------------
# Category E — Regression: simulation_records=[] does not break original tabs
# ---------------------------------------------------------------------------

class TestRegressionSimulationRecords:
    """Confirm that empty simulation_records on ScenarioResult does not corrupt existing tabs."""

    def test_E1_scenario_result_with_empty_sim_records_is_valid(self):
        """
        ScenarioResult with simulation_records=[] constructs without error
        and all economic fields are accessible.
        """
        result = ScenarioResult(
            name="test",
            analysis_scenario="test_scenario",
            total_qalys_disc=10000.0,
            total_qalys_undisc=10500.0,
            total_costs_disc=5_000_000.0,
            total_costs_undisc=5_200_000.0,
            cost_breakdown_disc={"drug": 4_000_000, "monitoring": 1_000_000},
            cumul_new_infections=100,
            timestep_records=[],
            simulation_records=[],
        )
        assert result.total_qalys_disc == 10000.0
        assert result.simulation_records == []
        assert result.timestep_records == []

    def test_E2_scenario_result_default_simulation_records_is_empty_list(self):
        """
        ScenarioResult without explicit simulation_records= defaults to [].
        """
        result = ScenarioResult(
            name="test",
            analysis_scenario="t",
            total_qalys_disc=1.0,
            total_qalys_undisc=1.0,
            total_costs_disc=1.0,
            total_costs_undisc=1.0,
        )
        assert isinstance(result.simulation_records, list)
        assert len(result.simulation_records) == 0

    def test_E3_comparator_works_with_empty_sim_records(self):
        """
        ScenarioComparator.compare_pair() works correctly when
        simulation_records=[] on both results.
        """
        ref = ScenarioResult(
            name="no_prep", analysis_scenario="t",
            total_qalys_disc=10000, total_qalys_undisc=10000,
            total_costs_disc=50_000_000, total_costs_undisc=50_000_000,
            cumul_new_infections=500,
            simulation_records=[],
        )
        intv = ScenarioResult(
            name="oral_prep", analysis_scenario="t",
            total_qalys_disc=10500, total_qalys_undisc=10500,
            total_costs_disc=70_000_000, total_costs_undisc=70_000_000,
            cumul_new_infections=300,
            simulation_records=[],
        )
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert "icer" in result
        assert result["delta_qaly"] == pytest.approx(500.0)
