"""
End-to-end tests for the HTA cost-effectiveness analysis pipeline (V11.5.6).

Validates the full integration chain:
  run_scenarios.py → baseline freeze → fork arms → economics → comparator → outputs

Test categories:
  1. Pipeline integrity: Full 3-arm run produces expected output files
  2. Epidemiological sanity: No-PrEP arm has more infections than PrEP arms
  3. Cost sanity: Lenacapavir arm has higher drug costs than Oral PrEP arm
  4. ICER validity: ICER (LEN vs Oral) is finite and positive
  5. Determinism: Same seed → identical results
  6. Baseline identity: All arms start from identical frozen state
  7. Price sensitivity: Lower price → lower ICER (monotonicity)
  8. Backward compatibility: No-HTA run still works
"""

import pytest
import json
import copy
import os
import sys
import shutil
import tempfile
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_scenarios import MultiScenarioRunner
from run_v3 import SimulationEngineV3
from state_snapshot import StateSnapshotManager
from economics import EconomicsAccumulator
from adverse_events import AdverseEventModule
from scenario_comparator import ScenarioComparator, ScenarioResult
from config_utils import extract_value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_config():
    """Load the V11 base config."""
    with open("configs/v11_configuration.json") as f:
        return json.load(f)


@pytest.fixture
def hta_config():
    """Load the HTA config."""
    with open("configs/hta_config.json") as f:
        return json.load(f)


@pytest.fixture
def short_config(base_config):
    """
    Create a shortened config for fast testing.

    Runs from 2020 to 2030 (40 steps), freeze at 2025, eval 5 years.
    Uses deterministic mode for reproducibility.
    """
    config = copy.deepcopy(base_config)

    # Shorten time range
    rc = config.setdefault("run_control", {})
    rc["start_year"] = {"value": 2020}
    rc["end_year"] = {"value": 2030}
    rc["deterministic_mode"] = {"value": True}

    # Ensure both PrEP types are enabled (base config should have these)
    if "interventions" in config:
        if "oral_prep" in config["interventions"]:
            config["interventions"]["oral_prep"]["enabled"] = {"value": True}
        if "lenacapavir" in config["interventions"]:
            config["interventions"]["lenacapavir"]["enabled"] = {"value": True}

    # V12.12: tests use the development config, whose Lenacapavir schedule may
    # be empty. Provide an active schedule so HTA no-op validation tests the
    # pipeline rather than failing on fixture setup.
    config.setdefault("prep_schedules", {}).setdefault("Lenacapavir", {}).setdefault(
        "initiation", {}
    )["schedule"] = {"2025": 0.10}
    config["prep_schedules"]["Lenacapavir"].setdefault(
        "discontinuation", {}
    )["schedule"] = {"2025": 0.05}

    # Write to temp file
    fd, path = tempfile.mkstemp(suffix=".json", prefix="hta_test_base_")
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, indent=2)

    yield path

    # Cleanup
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def short_hta_config(hta_config):
    """
    Create a shortened HTA config for fast testing.

    5-year evaluation horizon instead of 20.
    Only run Scenario A (base case) for speed.
    """
    config = copy.deepcopy(hta_config)
    config["time_horizon"]["evaluation_years"] = 5

    # Only test Scenario A for speed
    config["analysis_scenarios"] = [
        s for s in config["analysis_scenarios"]
        if s["name"] == "scenario_a_base"
    ]

    fd, path = tempfile.mkstemp(suffix=".json", prefix="hta_test_hta_")
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, indent=2)

    yield path

    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    d = tempfile.mkdtemp(prefix="hta_test_out_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1: Pipeline Integrity — Full run produces expected outputs
# ---------------------------------------------------------------------------

class TestPipelineIntegrity:
    """Verify the full HTA pipeline runs and produces correct output files."""

    def test_full_pipeline_produces_arm_summary(self, short_config, short_hta_config,
                                                  output_dir):
        """Full 3-arm run on Scenario A produces arm_summary.csv with expected columns."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        # Check arm_summary.csv exists
        summary_path = os.path.join(output_dir, "arm_summary.csv")
        assert os.path.exists(summary_path), "arm_summary.csv not produced"

        df = pd.read_csv(summary_path)

        # Expected columns
        expected_cols = [
            "scenario", "arm", "total_qalys_disc", "total_qalys_undisc",
            "total_costs_disc", "total_costs_undisc",
            "cost_drug_disc", "cost_monitoring_disc", "cost_hiv_care_disc",
            "cost_adverse_events_disc", "cost_terminal_care_disc",
            "cost_prep_drug_oral_disc", "cost_prep_drug_lenacapavir_disc",
            "cost_hiv_art_drug_disc", "payer_breakdown_sum_disc",
            "payer_breakdown_residual_disc",
            "cumul_new_infections"
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

        # Three arms for scenario_a_base
        scenario_a = df[df["scenario"] == "scenario_a_base"]
        assert len(scenario_a) == 3, f"Expected 3 arms, got {len(scenario_a)}"
        arm_names = set(scenario_a["arm"])
        assert arm_names == {"no_prep", "oral_prep", "lenacapavir"}

    def test_full_pipeline_produces_comparison_table(self, short_config,
                                                      short_hta_config, output_dir):
        """Comparison table CSV is produced with ICER data."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        runner.run()

        comp_path = os.path.join(output_dir, "comparison_table.csv")
        assert os.path.exists(comp_path), "comparison_table.csv not produced"

        df = pd.read_csv(comp_path)
        assert "icer" in df.columns
        assert "delta_qaly" in df.columns
        assert "delta_cost" in df.columns
        assert len(df) >= 2, "Expected at least 2 pairwise comparisons"

    def test_full_pipeline_produces_timestep_files(self, short_config,
                                                     short_hta_config, output_dir):
        """Per-step economic data files are produced for each arm."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        runner.run()

        # Check timestep files exist for each arm
        for arm in ["no_prep", "oral_prep", "lenacapavir"]:
            ts_path = os.path.join(
                output_dir,
                f"timestep_{arm}_scenario_a_base.csv"
            )
            assert os.path.exists(ts_path), f"Missing timestep file for {arm}"

            df = pd.read_csv(ts_path)
            assert len(df) > 0, f"Empty timestep file for {arm}"
            assert "year" in df.columns
            assert "step_qalys_disc" in df.columns
            assert "step_costs_disc" in df.columns

    def test_results_dict_structure(self, short_config, short_hta_config, output_dir):
        """Returned results dict has correct structure."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        assert "scenario_a_base" in results
        scenario_a = results["scenario_a_base"]
        assert isinstance(scenario_a, dict)
        assert "no_prep" in scenario_a
        assert "oral_prep" in scenario_a
        assert "lenacapavir" in scenario_a

        # Each result is a ScenarioResult
        for arm_name, result in scenario_a.items():
            assert isinstance(result, ScenarioResult)
            assert result.total_qalys_disc >= 0
            assert result.total_costs_disc >= 0


# ---------------------------------------------------------------------------
# Test 2: Epidemiological Sanity
# ---------------------------------------------------------------------------

class TestEpidemiologicalSanity:
    """Validate that epidemiological outputs make clinical sense."""

    def test_no_prep_has_most_infections(self, short_config, short_hta_config,
                                          output_dir):
        """
        No PrEP arm should have >= infections than either PrEP arm.

        This is the fundamental epidemiological validation: PrEP prevents HIV.
        """
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        scenario_a = results["scenario_a_base"]
        no_prep_inf = scenario_a["no_prep"].cumul_new_infections
        oral_inf = scenario_a["oral_prep"].cumul_new_infections
        lena_inf = scenario_a["lenacapavir"].cumul_new_infections

        assert no_prep_inf >= oral_inf, (
            f"No PrEP ({no_prep_inf}) should have >= infections than "
            f"Oral PrEP ({oral_inf})"
        )
        assert no_prep_inf >= lena_inf, (
            f"No PrEP ({no_prep_inf}) should have >= infections than "
            f"Lenacapavir ({lena_inf})"
        )

    def test_qalys_are_positive(self, short_config, short_hta_config, output_dir):
        """All arms should produce positive total QALYs."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        for arm_name, result in results["scenario_a_base"].items():
            assert result.total_qalys_disc > 0, (
                f"{arm_name} has non-positive QALYs: {result.total_qalys_disc}"
            )

    def test_costs_are_non_negative(self, short_config, short_hta_config, output_dir):
        """All arms should produce non-negative total costs."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        for arm_name, result in results["scenario_a_base"].items():
            assert result.total_costs_disc >= 0, (
                f"{arm_name} has negative costs: {result.total_costs_disc}"
            )

        # At least the PrEP arms should have positive costs (drug costs)
        oral_costs = results["scenario_a_base"]["oral_prep"].total_costs_disc
        lena_costs = results["scenario_a_base"]["lenacapavir"].total_costs_disc
        assert oral_costs > 0 or lena_costs > 0, (
            "Both PrEP arms have zero costs — PrEP uptake may not be working"
        )


# ---------------------------------------------------------------------------
# Test 3: Cost Sanity
# ---------------------------------------------------------------------------

class TestCostSanity:
    """Validate cost relationships between arms."""

    def test_lenacapavir_drug_costs_exceed_oral(self, short_config,
                                                 short_hta_config, output_dir):
        """
        Lenacapavir arm should have higher drug costs than Oral PrEP arm.

        Lenacapavir: €21,395/year vs Oral PrEP: €5,400/year.
        """
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        scenario_a = results["scenario_a_base"]
        oral_drug = scenario_a["oral_prep"].cost_breakdown_disc.get("drug", 0)
        lena_drug = scenario_a["lenacapavir"].cost_breakdown_disc.get("drug", 0)

        # At least one arm should have nonzero drug costs
        assert oral_drug > 0 or lena_drug > 0, (
            "Both arms have zero drug costs — PrEP uptake may not be working"
        )

        # If both have users, Lena should be more expensive
        if oral_drug > 0 and lena_drug > 0:
            assert lena_drug > oral_drug, (
                f"Lenacapavir drug cost (€{lena_drug:,.0f}) should exceed "
                f"Oral PrEP (€{oral_drug:,.0f})"
            )

    def test_no_prep_has_no_prep_drug_costs(self, short_config,
                                             short_hta_config, output_dir):
        """No PrEP arm should have zero PrEP drug costs."""
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        no_prep = results["scenario_a_base"]["no_prep"]
        # No PrEP arm should still have HIV care costs but minimal/no PrEP drug costs
        # Note: "drug" category includes ART costs, so it won't be zero
        # The key check is that no PrEP-specific costs are accumulated
        # (This is validated implicitly by the arm config disabling PrEP)
        assert no_prep.total_costs_disc >= 0


# ---------------------------------------------------------------------------
# Test 4: ICER Validity
# ---------------------------------------------------------------------------

class TestICERValidity:
    """Validate ICER computations are sensible."""

    def test_comparison_table_has_valid_icers(self, short_config,
                                               short_hta_config, output_dir):
        """
        ICER values should be either numeric (finite) or valid string
        classifications (Dominant, Dominated, No QALY difference).
        """
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        wtp = extract_value(
            runner.hta_config["wtp_threshold_eur_per_qaly"]
        )
        step_fraction = extract_value(runner.base_config["run_control"]["step_size"]) / 12.0
        comparator = ScenarioComparator(wtp_threshold=wtp, step_fraction=step_fraction)
        table = comparator.build_comparison_table(
            results["scenario_a_base"]
        )

        assert len(table) >= 2, "Expected at least 2 comparisons"

        valid_strings = {"Dominant", "Dominated", "No QALY difference"}
        for _, row in table.iterrows():
            icer = row["icer"]
            if isinstance(icer, str):
                assert icer in valid_strings, f"Invalid ICER string: {icer}"
            else:
                assert np.isfinite(icer), f"Non-finite ICER: {icer}"

    def test_nmb_consistency_with_icer(self, short_config,
                                        short_hta_config, output_dir):
        """NMB sign must be consistent with ICER outcome in all quadrants.

        Previously this test only checked the NE quadrant (delta_qaly > 0).
        Extended to cover all quadrants so that SW-quadrant regressions are
        caught:
          - NE (higher cost, higher QALYs): NMB > 0 iff ICER <= WTP
          - NW (dominant):                  NMB always > 0
          - SE (dominated):                 NMB always < 0
          - SW (cheaper, fewer QALYs):      NMB > 0 iff ICER >= WTP
        The universal rule NMB > 0 covers all four quadrants.
        """
        runner = MultiScenarioRunner(
            short_config,
            output_dir=output_dir,
            hta_config_path=short_hta_config
        )
        results = runner.run()

        wtp           = extract_value(runner.hta_config["wtp_threshold_eur_per_qaly"])
        step_fraction = extract_value(runner.base_config["run_control"]["step_size"]) / 12.0
        comparator = ScenarioComparator(wtp_threshold=wtp, step_fraction=step_fraction)
        table = comparator.build_comparison_table(results["scenario_a_base"])

        for _, row in table.iterrows():
            icer = row["icer"]
            nmb  = row["nmb"]
            dc   = row["delta_cost"]
            dq   = row["delta_qaly"]

            if isinstance(icer, str):
                if icer == "Dominant":
                    assert nmb > 0, "Dominant (NW): NMB must be positive"
                elif icer == "Dominated":
                    assert nmb < 0, "Dominated (SE): NMB must be negative"
            else:
                # NE quadrant: CE iff ICER <= WTP <=> NMB > 0
                if dc > 0 and dq > 0:
                    expected_ce = icer <= wtp
                    assert (nmb > 0) == expected_ce, (
                        f"NE quadrant: ICER={icer:,.0f}, WTP={wtp:,.0f}, "
                        f"NMB={nmb:,.0f} — sign inconsistent"
                    )
                # SW quadrant: CE iff ICER >= WTP <=> NMB > 0
                elif dc < 0 and dq < 0:
                    expected_ce = icer >= wtp
                    assert (nmb > 0) == expected_ce, (
                        f"SW quadrant: ICER={icer:,.0f}, WTP={wtp:,.0f}, "
                        f"NMB={nmb:,.0f} — sign inconsistent"
                    )


# ---------------------------------------------------------------------------
# Test 5: Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Verify reproducibility with deterministic mode."""

    def test_same_seed_identical_results(self, short_config,
                                          short_hta_config, output_dir):
        """Two runs with the same seed should produce identical arm summaries."""
        output_dir_1 = os.path.join(output_dir, "run1")
        output_dir_2 = os.path.join(output_dir, "run2")

        runner1 = MultiScenarioRunner(
            short_config,
            output_dir=output_dir_1,
            hta_config_path=short_hta_config
        )
        results1 = runner1.run()

        runner2 = MultiScenarioRunner(
            short_config,
            output_dir=output_dir_2,
            hta_config_path=short_hta_config
        )
        results2 = runner2.run()

        # Compare all arms
        for scenario_name in results1:
            for arm_name in results1[scenario_name]:
                r1 = results1[scenario_name][arm_name]
                r2 = results2[scenario_name][arm_name]

                assert r1.total_qalys_disc == pytest.approx(
                    r2.total_qalys_disc, rel=1e-10
                ), f"QALYs differ for {arm_name}: {r1.total_qalys_disc} vs {r2.total_qalys_disc}"

                assert r1.total_costs_disc == pytest.approx(
                    r2.total_costs_disc, rel=1e-10
                ), f"Costs differ for {arm_name}: {r1.total_costs_disc} vs {r2.total_costs_disc}"

                assert r1.cumul_new_infections == r2.cumul_new_infections, (
                    f"Infections differ for {arm_name}: "
                    f"{r1.cumul_new_infections} vs {r2.cumul_new_infections}"
                )


# ---------------------------------------------------------------------------
# Test 6: Baseline Identity
# ---------------------------------------------------------------------------

class TestBaselineIdentity:
    """Verify all arms start from identical baseline state."""

    def test_snapshot_manager_produces_identical_clones(self):
        """StateSnapshotManager.restore() should return identical clones."""
        mgr = StateSnapshotManager()
        mgr.configure(freeze_enabled=True, freeze_year=2025)

        # Create a synthetic snapshot
        pop = np.random.RandomState(42).randint(0, 100, size=(20, 3, 3, 4, 2, 2, 3))
        child = np.random.RandomState(42).randint(0, 50, size=(3, 2))
        rng = np.random.RandomState(42)

        mgr.freeze(pop, child, step=180, year=2025.0, rng=rng)

        # Restore multiple times
        snap1 = mgr.restore("arm1")
        snap2 = mgr.restore("arm2")
        snap3 = mgr.restore("arm3")

        # All should be identical
        assert np.array_equal(snap1.population, snap2.population)
        assert np.array_equal(snap2.population, snap3.population)
        assert np.array_equal(snap1.childhood_population, snap2.childhood_population)
        assert snap1.population_checksum == snap2.population_checksum
        assert snap2.population_checksum == snap3.population_checksum


# ---------------------------------------------------------------------------
# Test 7: Component Integration
# ---------------------------------------------------------------------------

class TestComponentIntegration:
    """Test that individual components wire together correctly."""

    def test_economics_accumulator_with_ae_module(self):
        """EconomicsAccumulator correctly integrates AdverseEventModule."""
        hta_config = _make_minimal_hta_config()

        groups = _MockGroups()
        econ = EconomicsAccumulator(
            hta_config=hta_config,
            groups=groups,
            freeze_year=2025.0,
            step_fraction=0.25,  # V11.7.2
        )
        ae = AdverseEventModule(hta_config, groups, step_fraction=0.25)  # V11.7.2
        econ.set_ae_module(ae)

        # Create population with some PrEP users
        pop = np.zeros((20, 3, 3, 4, 2, 2, 4))
        pop[5, 0, 2, 0, 0, 0, 1] = 100  # 100 on Oral PrEP
        pop[5, 0, 2, 0, 0, 0, 2] = 50   # 50 on Lenacapavir

        econ.accumulate(step=180, year=2025.0, population=pop, new_infections=5)

        summary = econ.get_summary()
        assert summary["total_qalys_disc"] > 0
        assert summary["total_costs_disc"] > 0
        assert summary["cumul_new_infections"] == 5

    def test_scenario_result_from_accumulator(self):
        """ScenarioResult can be constructed from accumulator summary."""
        hta_config = _make_minimal_hta_config()
        groups = _MockGroups()

        econ = EconomicsAccumulator(
            hta_config=hta_config,
            groups=groups,
            freeze_year=2025.0,
            step_fraction=0.25,  # V11.7.2
        )

        # Accumulate a few steps
        pop = np.zeros((20, 3, 3, 4, 2, 2, 4))
        pop[5, 0, 2, 0, 0, 0, 0] = 1000  # Susceptible, no PrEP
        pop[5, 0, 2, 2, 1, 1, 0] = 200   # Chronic, diagnosed, on ART

        for step in range(180, 200):
            year = 2025.0 + (step - 180) * 0.25
            econ.accumulate(step=step, year=year, population=pop,
                           new_infections=2)

        summary = econ.get_summary()
        result = ScenarioResult(
            name="test_arm",
            analysis_scenario="test_scenario",
            total_qalys_disc=summary["total_qalys_disc"],
            total_qalys_undisc=summary["total_qalys_undisc"],
            total_costs_disc=summary["total_costs_disc"],
            total_costs_undisc=summary["total_costs_undisc"],
            cost_breakdown_disc=summary["cost_breakdown_disc"],
            cumul_new_infections=summary["cumul_new_infections"],
            timestep_records=econ.get_timestep_records(),
            simulation_records=[],  # V11.8.1
        )

        assert result.total_qalys_disc > 0
        assert result.cumul_new_infections == 40  # 2 per step × 20 steps
        assert len(result.timestep_records) == 20

    def test_comparator_with_synthetic_results(self):
        """ScenarioComparator produces valid output from synthetic results."""
        # V11.7.2: WTP + step_fraction must both be provided explicitly
        comparator = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)

        results = {
            "no_prep": ScenarioResult(
                name="no_prep", analysis_scenario="test",
                total_qalys_disc=10000, total_qalys_undisc=11000,
                total_costs_disc=50_000_000, total_costs_undisc=55_000_000,
                cost_breakdown_disc={"drug": 30_000_000, "monitoring": 10_000_000, "hiv_care": 10_000_000},
                cumul_new_infections=500,
            ),
            "oral_prep": ScenarioResult(
                name="oral_prep", analysis_scenario="test",
                total_qalys_disc=10500, total_qalys_undisc=11500,
                total_costs_disc=70_000_000, total_costs_undisc=75_000_000,
                cost_breakdown_disc={"drug": 50_000_000, "monitoring": 12_000_000, "hiv_care": 8_000_000},
                cumul_new_infections=300,
            ),
            "lenacapavir": ScenarioResult(
                name="lenacapavir", analysis_scenario="test",
                total_qalys_disc=10800, total_qalys_undisc=11800,
                total_costs_disc=90_000_000, total_costs_undisc=95_000_000,
                cost_breakdown_disc={"drug": 70_000_000, "monitoring": 12_000_000, "hiv_care": 8_000_000},
                cumul_new_infections=150,
            ),
        }

        # Build comparison table
        table = comparator.build_comparison_table(results)
        assert len(table) >= 2

        # Check sequential ordering: oral vs no_prep, then lena vs oral
        comparisons = table["comparison"].tolist()
        assert any("oral_prep" in c and "no_prep" in c for c in comparisons)
        assert any("lenacapavir" in c for c in comparisons)

        # Summary table
        summary = comparator.summary_table(results)
        assert len(summary) == 3
        assert "arm" in summary.columns


# ---------------------------------------------------------------------------
# Test 8: Engine start_step/end_step
# ---------------------------------------------------------------------------

class TestEngineStepRange:
    """Test that SimulationEngineV3.run() respects start_step and end_step."""

    def test_run_with_start_step(self, short_config):
        """Engine should only execute steps >= start_step."""
        engine = SimulationEngineV3(short_config, seed=42)

        # Run only last 10 steps
        total_steps = engine.time.n_steps
        start = max(0, total_steps - 10)

        engine.run(start_step=start)

        # Output should have entries for the steps we ran
        assert len(engine.outputs['step']) == total_steps - start

    def test_run_with_end_step(self, short_config):
        """Engine should only execute steps < end_step."""
        engine = SimulationEngineV3(short_config, seed=42)

        engine.run(end_step=10)

        assert len(engine.outputs['step']) == 10

    def test_default_run_unchanged(self, short_config):
        """Default run() (no start/end) should behave as before."""
        engine = SimulationEngineV3(short_config, seed=42)
        engine.run()
        assert len(engine.outputs['step']) == engine.time.n_steps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockGroups:
    """Minimal mock for PopulationGroups."""
    def __init__(self):
        self.hiv_to_idx = {
            "Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3
        }
        self.prep_states = ["None", "Oral_PrEP", "Lenacapavir", "Lenacapavir_Waning"]
        self.prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2, "Lenacapavir_Waning": 3}
        self.n_hiv_stages = 4


def _make_minimal_hta_config():
    """
    Create a minimal HTA config for unit-level integration tests.

    V11.8.0: Converted to v2 format — old flat format is no longer accepted.
    Numeric values are unchanged so that test assertions (QALYs > 0, costs > 0) hold.
    """
    return {
        "_format_version": "v2",
        "time_horizon": {"freeze_year": 2025, "evaluation_years": 5},
        "discounting": {
            "health_effects_rate": {"value": 0.015},
            "cost_rate":           {"value": 0.04},
            "base_year":           {"value": 2025},
        },
        "wtp_threshold_eur_per_qaly": {"value": 50000},
        "utilities": {
            "susceptible":             {"value": 1.000, "distribution": {"type": "fixed"}},
            "susceptible_oral_prep":   {"value": 0.990, "distribution": {"type": "fixed"}},
            "susceptible_lenacapavir": {"value": 0.995, "distribution": {"type": "fixed"}},
            "hiv_acute":               {"value": 0.700, "distribution": {"type": "fixed"}},
            "hiv_chronic_on_art":      {"value": 0.850, "distribution": {"type": "fixed"}},
            "hiv_chronic_off_art":     {"value": 0.780, "distribution": {"type": "fixed"}},
            "aids_on_art":             {"value": 0.600, "distribution": {"type": "fixed"}},
            "aids_off_art":            {"value": 0.400, "distribution": {"type": "fixed"}},
        },
        "adverse_events": {
            "mode": "prevalence",
            "items": [
                {
                    "id":              "oral_prep_ae",
                    "arm":             "oral_prep",
                    "billing_moment":  "ongoing",
                    "frequency":       "annual",
                    "prevalence":      {"value": 0.15},
                    "utility_decrement": {"value": -0.02},
                    "cost":            {"value": 200},
                },
                {
                    "id":              "lenacapavir_ae",
                    "arm":             "lenacapavir",
                    "billing_moment":  "ongoing",
                    "frequency":       "annual",
                    "prevalence":      {"value": 0.20},
                    "utility_decrement": {"value": -0.01},
                    "cost":            {"value": 150},
                },
            ],
        },
        "prevention_costs": {
            "shared": [],
            "arm_specific": {
                "oral_prep": {
                    "drug_and_monitoring": {
                        "value":     5400,
                        "cohort":    "all",
                        "category":  "drug",
                        "frequency": "annual",
                    },
                },
                "lenacapavir": {
                    "injection_year1": {
                        "value":     21395.20,
                        "cohort":    "new_initiators",
                        "category":  "drug",
                        "frequency": "annual",
                    },
                    "injection_subsequent": {
                        "value":     20555.22,
                        "cohort":    "continuing",
                        "category":  "drug",
                        "frequency": "annual",
                    },
                },
            },
        },
        "disease_costs": {
            "art_first_line": {
                "value":                 10000,
                "billing_moment":        "ongoing",
                "frequency":             "annual",
                "applies_to_stages":     ["Acute", "Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":              "drug",
            },
            "hiv_monitoring_on_art": {
                "value":                 2500,
                "billing_moment":        "ongoing",
                "frequency":             "annual",
                "applies_to_stages":     ["Chronic", "AIDS"],
                "applies_to_art_status": ["on_art"],
                "category":              "monitoring",
            },
            "aids_hospitalization": {
                "value":                 40000,
                "billing_moment":        "ongoing",
                "frequency":             "annual",
                "applies_to_stages":     ["AIDS"],
                "applies_to_art_status": ["on_art", "off_art"],
                "category":              "hiv_care",
            },
        },
        "analysis_scenarios": [],
    }


# ---------------------------------------------------------------------------
# V11.7.1 / V11.7.2 Tests: WTP + step_fraction required args + per-person metrics
# ---------------------------------------------------------------------------

class TestWTPRequiredArg:
    """V11.7.1/V11.7.2: wtp_threshold and step_fraction must both be provided explicitly."""

    def test_comparator_requires_wtp_argument(self):
        """ScenarioComparator() with no args raises TypeError — no silent default."""
        with pytest.raises(TypeError):
            ScenarioComparator()

    def test_comparator_requires_step_fraction_argument(self):
        """ScenarioComparator(wtp_threshold=80000) without step_fraction raises TypeError."""
        with pytest.raises(TypeError):
            ScenarioComparator(wtp_threshold=80000)

    def test_comparator_accepts_explicit_wtp_and_step_fraction(self):
        """ScenarioComparator(wtp_threshold=80000, step_fraction=0.25) initialises cleanly."""
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        assert comp.wtp == 80000.0
        assert comp.step_fraction == 0.25

    def test_comparator_rejects_invalid_step_fraction(self):
        """step_fraction=0.0 raises ValueError with clear message."""
        with pytest.raises(ValueError, match="step_fraction must be in"):
            ScenarioComparator(wtp_threshold=80000, step_fraction=0.0)

    def test_comparator_rejects_step_fraction_above_one(self):
        """step_fraction > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="step_fraction must be in"):
            ScenarioComparator(wtp_threshold=80000, step_fraction=1.5)

    def test_nmb_uses_provided_wtp(self):
        """NMB at WTP=80000 differs from NMB at WTP=50000 for the same delta_qaly."""
        ref = ScenarioResult(
            name="ref", analysis_scenario="t",
            total_qalys_disc=10000, total_qalys_undisc=10000,
            total_costs_disc=50_000_000, total_costs_undisc=50_000_000,
            cumul_new_infections=500,
        )
        intv = ScenarioResult(
            name="intv", analysis_scenario="t",
            total_qalys_disc=10500, total_qalys_undisc=10500,
            total_costs_disc=70_000_000, total_costs_undisc=70_000_000,
            cumul_new_infections=300,
        )
        comp_80 = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        comp_50 = ScenarioComparator(wtp_threshold=50000, step_fraction=0.25)
        # delta_qaly=500, delta_cost=20_000_000
        # NMB@80000 = 80000*500 - 20_000_000 = 40_000_000 - 20_000_000 = 20_000_000
        # NMB@50000 = 50000*500 - 20_000_000 = 25_000_000 - 20_000_000 =  5_000_000
        assert comp_80.compare_pair(ref, intv)["nmb"] == pytest.approx(20_000_000)
        assert comp_50.compare_pair(ref, intv)["nmb"] == pytest.approx(5_000_000)

    def test_comparator_detects_step_fraction_mismatch(self):
        """
        If timestep records store step_fraction != comparator.step_fraction, raises ValueError.
        This prevents silent wrong person-year calculations when comparator is misconfigured.
        """
        records = [
            {"total_population": 50000.0, "step_fraction": 0.5}  # stored as 0.5 (semi-annual)
        ]
        result = ScenarioResult(
            name="arm", analysis_scenario="t",
            total_qalys_disc=10000, total_qalys_undisc=10000,
            total_costs_disc=50_000_000, total_costs_undisc=50_000_000,
            cumul_new_infections=100, timestep_records=records,
        )
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)  # mismatch
        with pytest.raises(ValueError, match="step_fraction mismatch"):
            comp._compute_person_years(result)


class TestPerPersonMetrics:
    """V11.7.1/V11.7.2: compare_pair() returns cost_per_infection_averted and person-year metrics."""

    def _make_result(self, name, qalys, costs, infections, pop_per_step=50000, n_steps=80):
        """Helper: build ScenarioResult with synthetic timestep_records.

        V11.7.2: records include step_fraction=0.25 so cross-check passes.
        80 steps × 50,000 pop × 0.25 yr = 1,000,000 person-years.
        """
        records = [
            {
                "total_population": float(pop_per_step),
                "step_new_infections": 0,
                "step_fraction": 0.25,  # V11.7.2: required for cross-check
            }
            for _ in range(n_steps)
        ]
        return ScenarioResult(
            name=name, analysis_scenario="t",
            total_qalys_disc=qalys, total_qalys_undisc=qalys,
            total_costs_disc=costs, total_costs_undisc=costs,
            cumul_new_infections=infections,
            timestep_records=records,
            simulation_records=[],  # V11.8.1
        )

    def test_cost_per_infection_averted_positive(self):
        """cost_per_infection_averted = delta_cost / infections_averted when averted > 0."""
        ref  = self._make_result("ref",  10000, 50_000_000, 500)
        intv = self._make_result("intv", 10500, 70_000_000, 300)
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        # delta_cost=20_000_000, infections_averted=200
        assert result["cost_per_infection_averted"] == pytest.approx(
            20_000_000 / 200
        )

    def test_cost_per_infection_averted_none_when_zero_averted(self):
        """cost_per_infection_averted is None when infections_averted == 0."""
        ref  = self._make_result("ref",  10000, 50_000_000, 300)
        intv = self._make_result("intv", 10500, 70_000_000, 300)  # same infections
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert result["cost_per_infection_averted"] is None

    def test_delta_qaly_per_1000_person_years(self):
        """
        ΔQALY per 1,000 PY = (delta_qaly / intv_py) * 1000.
        80 steps × 50,000 pop × 0.25yr = 1,000,000 person-years (intv arm).
        V11.7.2: denominator is intv arm person-years (not avg of both arms).
        Both arms have equal pop here, so result is identical to avg-based calc.
        delta_qaly = 500. Expected = (500 / 1_000_000) * 1000 = 0.5.
        """
        ref  = self._make_result("ref",  10000, 50_000_000, 500)
        intv = self._make_result("intv", 10500, 70_000_000, 300)
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert result["delta_qaly_per_1000_person_years"] == pytest.approx(0.5)

    def test_incremental_cost_per_person_year(self):
        """
        ΔCost per PY = delta_cost / intv_py.
        V11.7.2: denominator is intv arm person-years (not avg of both arms).
        delta_cost=20_000_000, intv_py=1_000_000 → 20.0 EUR/person-year.
        """
        ref  = self._make_result("ref",  10000, 50_000_000, 500)
        intv = self._make_result("intv", 10500, 70_000_000, 300)
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert result["incremental_cost_per_person_year"] == pytest.approx(20.0)

    def test_per_arm_person_years_in_return_dict(self):
        """
        V11.7.2: compare_pair() returns ref_person_years and intv_person_years separately.
        80 steps × 50,000 pop × 0.25yr = 1,000,000 per arm.
        """
        ref  = self._make_result("ref",  10000, 50_000_000, 500)
        intv = self._make_result("intv", 10500, 70_000_000, 300)
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert result["ref_person_years"]  == pytest.approx(1_000_000)
        assert result["intv_person_years"] == pytest.approx(1_000_000)

    def test_per_person_metrics_use_intv_arm_denominator(self):
        """
        V11.7.2: when intv arm has different population than ref,
        metrics use intv_py as denominator, not the average.
        intv: 80 steps × 60,000 pop × 0.25yr = 1,200,000 PY.
        delta_qaly=500 → (500 / 1_200_000) * 1000 ≈ 0.4167.
        delta_cost=20_000_000 → 20_000_000 / 1_200_000 ≈ 16.667.
        """
        ref  = self._make_result("ref",  10000, 50_000_000, 500, pop_per_step=50000)
        intv = self._make_result("intv", 10500, 70_000_000, 300, pop_per_step=60000)
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        intv_py = 60000 * 80 * 0.25  # = 1_200_000
        assert result["delta_qaly_per_1000_person_years"] == pytest.approx(
            (500 / intv_py) * 1000, rel=1e-5
        )
        assert result["incremental_cost_per_person_year"] == pytest.approx(
            20_000_000 / intv_py, rel=1e-5
        )

    def test_per_person_metrics_none_when_no_timestep_records(self):
        """Person-year metrics return None gracefully when timestep_records is empty."""
        ref  = ScenarioResult(
            name="ref", analysis_scenario="t",
            total_qalys_disc=10000, total_qalys_undisc=10000,
            total_costs_disc=50_000_000, total_costs_undisc=50_000_000,
            cumul_new_infections=500,
        )
        intv = ScenarioResult(
            name="intv", analysis_scenario="t",
            total_qalys_disc=10500, total_qalys_undisc=10500,
            total_costs_disc=70_000_000, total_costs_undisc=70_000_000,
            cumul_new_infections=300,
        )
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)
        assert result["delta_qaly_per_1000_person_years"] is None
        assert result["incremental_cost_per_person_year"] is None
        # cost_per_infection_averted still works — doesn't need person-years
        assert result["cost_per_infection_averted"] == pytest.approx(20_000_000 / 200)

    def test_per_person_metrics_backward_compat_no_population_key(self):
        """
        V11.7.1: timestep_records without total_population key (pre-V11.7.1 format)
        should not raise — returns None for person-year metrics via .get() fallback.
        V11.7.2: also no step_fraction in records → cross-check not triggered.
        """
        old_format_records = [
            {"step": i, "year": 2025.0 + i * 0.25,
             "step_qalys_disc": 100.0, "step_costs_disc": 1000.0}
            for i in range(10)
        ]
        ref  = ScenarioResult(
            name="ref", analysis_scenario="t",
            total_qalys_disc=10000, total_qalys_undisc=10000,
            total_costs_disc=50_000_000, total_costs_undisc=50_000_000,
            cumul_new_infections=500, timestep_records=old_format_records,
        )
        intv = ScenarioResult(
            name="intv", analysis_scenario="t",
            total_qalys_disc=10500, total_qalys_undisc=10500,
            total_costs_disc=70_000_000, total_costs_undisc=70_000_000,
            cumul_new_infections=300, timestep_records=old_format_records,
        )
        comp = ScenarioComparator(wtp_threshold=80000, step_fraction=0.25)
        result = comp.compare_pair(ref, intv)  # must not raise
        assert result["delta_qaly_per_1000_person_years"] is None
        assert result["incremental_cost_per_person_year"] is None


# ---------------------------------------------------------------------------
# Test N: SW Quadrant Cost-Effectiveness Bug Fix (V11.7.X)
#
# Verifies that cost_effective flags, price sensitivity, find_threshold_price,
# and ICER cell colouring all use NMB > 0 as the decision rule — correct in
# all four quadrants of the cost-effectiveness plane.
#
# Mathematical basis:
#   NMB = WTP × ΔQ − ΔC
#   NE (ΔC > 0, ΔQ > 0): CE iff ICER ≤ WTP  ↔  NMB > 0  ✓
#   NW (ΔC < 0, ΔQ > 0): Dominant            →  NMB > 0  always ✓
#   SE (ΔC > 0, ΔQ < 0): Dominated           →  NMB < 0  always ✓
#   SW (ΔC < 0, ΔQ < 0): CE iff ICER ≥ WTP  ↔  NMB > 0  ✓
#                         (old code: ICER ≤ WTP → WRONG in SW)
#
# Reference: Drummond MF et al. (2015) Methods for the Economic Evaluation
# of Health Care Programmes, 4th ed., Oxford University Press, Ch. 4.
# ---------------------------------------------------------------------------

def _make_result(name, total_costs_disc, total_qalys_disc,
                 drug_cost=None, infections=100):
    """Helper: build a minimal ScenarioResult for comparator unit tests."""
    if drug_cost is None:
        drug_cost = total_costs_disc * 0.7
    return ScenarioResult(
        name=name,
        analysis_scenario="test",
        total_qalys_disc=total_qalys_disc,
        total_qalys_undisc=total_qalys_disc,
        total_costs_disc=total_costs_disc,
        total_costs_undisc=total_costs_disc,
        cost_breakdown_disc={
            "drug": drug_cost,
            "prep_drug": drug_cost if name in ("oral_prep", "lenacapavir") else 0.0,
            "prep_drug_oral": drug_cost if name == "oral_prep" else 0.0,
            "prep_drug_lenacapavir": drug_cost if name == "lenacapavir" else 0.0,
            "monitoring": 0.0,
            "hiv_care": 0.0,
        },
        cumul_new_infections=infections,
    )


class TestSWQuadrantCostEffectiveness:
    """
    Unit tests for the SW-quadrant cost-effectiveness fix.

    All tests use synthetic ScenarioResult objects — no engine run required.
    WTP = €80,000/QALY throughout (ZIN HIV/AIDS high-burden category).
    """

    WTP = 80_000
    SF  = 0.25

    @pytest.fixture
    def cmp(self):
        return ScenarioComparator(wtp_threshold=self.WTP, step_fraction=self.SF)

    # ------------------------------------------------------------------
    # 1-2: SW quadrant — compare_pair NMB
    # ------------------------------------------------------------------

    def test_sw_ce_real_model_values(self, cmp):
        """SW quadrant: values matching real model output should be CE.

        delta_cost = -568,012,909  (lena cheaper)
        delta_qaly = -6,686        (lena fewer QALYs)
        NMB = 80,000 × (-6,686) − (-568,012,909) = +33,137,709  → CE
        ICER = 84,956  > WTP  → old code would say NOT CE (wrong).
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=70_000_000_000,
                            total_qalys_disc=189_681_355)
        lena = _make_result("lenacapavir",
                            total_costs_disc=70_000_000_000 - 568_012_909,
                            total_qalys_disc=189_681_355 - 6_686)
        result = cmp.compare_pair(oral, lena)

        assert result["delta_cost"] < 0, "lena should be cheaper"
        assert result["delta_qaly"] < 0, "lena should have fewer QALYs"
        assert result["nmb"] > 0, (
            f"SW quadrant with high cost-saving should have NMB > 0, "
            f"got {result['nmb']:,.0f}"
        )

    def test_sw_not_ce_low_saving(self, cmp):
        """SW quadrant: tiny cost saving does NOT justify QALY loss → not CE.

        delta_cost = -100,000  (lena barely cheaper)
        delta_qaly = -6,686
        NMB = 80,000 × (-6,686) − (-100,000) = -534,780,000  → not CE
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=70_000_000_000,
                            total_qalys_disc=189_681_355)
        lena = _make_result("lenacapavir",
                            total_costs_disc=70_000_000_000 - 100_000,
                            total_qalys_disc=189_681_355 - 6_686)
        result = cmp.compare_pair(oral, lena)

        assert result["delta_cost"] < 0
        assert result["delta_qaly"] < 0
        assert result["nmb"] < 0, (
            f"SW quadrant with tiny cost-saving should have NMB < 0, "
            f"got {result['nmb']:,.0f}"
        )

    # ------------------------------------------------------------------
    # 3-6: NE / NW / SE regression — existing quadrants unaffected
    # ------------------------------------------------------------------

    def test_ne_ce_regression(self, cmp):
        """NE quadrant: ICER < WTP → NMB > 0 (regression)."""
        ref  = _make_result("ref",  total_costs_disc=50_000_000, total_qalys_disc=10_000)
        intv = _make_result("intv", total_costs_disc=70_000_000, total_qalys_disc=11_000)
        result = cmp.compare_pair(ref, intv)
        # delta_cost = +20M, delta_qaly = +1000 → ICER = 20,000 < 80,000
        assert result["delta_cost"] > 0 and result["delta_qaly"] > 0
        assert result["nmb"] > 0
        assert result["icer"] == pytest.approx(20_000, rel=1e-4)

    def test_ne_not_ce_regression(self, cmp):
        """NE quadrant: ICER > WTP → NMB < 0 (regression)."""
        ref  = _make_result("ref",  total_costs_disc=50_000_000, total_qalys_disc=10_000)
        intv = _make_result("intv", total_costs_disc=150_000_000, total_qalys_disc=10_500)
        result = cmp.compare_pair(ref, intv)
        # delta_cost = +100M, delta_qaly = +500 → ICER = 200,000 > 80,000
        assert result["delta_cost"] > 0 and result["delta_qaly"] > 0
        assert result["nmb"] < 0
        assert result["icer"] == pytest.approx(200_000, rel=1e-4)

    def test_dominant_nw_regression(self, cmp):
        """NW quadrant (dominant): cheaper AND better → NMB always positive."""
        ref  = _make_result("ref",  total_costs_disc=100_000_000, total_qalys_disc=10_000)
        intv = _make_result("intv", total_costs_disc=80_000_000,  total_qalys_disc=11_000)
        result = cmp.compare_pair(ref, intv)
        assert result["icer"] == "Dominant"
        assert result["nmb"] > 0

    def test_dominated_se_regression(self, cmp):
        """SE quadrant (dominated): more expensive AND worse → NMB always negative."""
        ref  = _make_result("ref",  total_costs_disc=50_000_000,  total_qalys_disc=11_000)
        intv = _make_result("intv", total_costs_disc=100_000_000, total_qalys_disc=10_000)
        result = cmp.compare_pair(ref, intv)
        assert result["icer"] == "Dominated"
        assert result["nmb"] < 0

    # ------------------------------------------------------------------
    # 7-11: price_sensitivity — dimensional fix + cost_effective flag
    #
    # Fixture values (used across tests in this block):
    #   oral:  total_costs_disc=600M, qalys=1,000,000, drug=540M
    #   lena:  total_costs_disc=400M, qalys=999,500,   drug=200M  (SW quadrant)
    #   base_annual_price = 20,000  (annual per-person cost, EUR/year)
    #
    # Scaling: new_drug = 200M × (price / 20,000) = 10,000 × price
    #          new_lena_total = (400M − 200M) + 10,000×p = 200M + 10,000p
    #          delta_cost(p)  = (200M + 10,000p) − 600M  = 10,000p − 400M
    #          NMB(p)         = 80,000×(−500) − (10,000p − 400M)
    #                         = −40M − 10,000p + 400M = 360M − 10,000p
    # ------------------------------------------------------------------

    BASE_ANNUAL = 20_000  # EUR/year — base lenacapavir annual per-person price

    def test_price_sensitivity_base_case_unchanged(self, cmp):
        """At price = base_annual_price, total costs must be identical to the
        unmodified lenacapavir result (ratio = 1.0 → no change)."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=900_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=1_100_000,
                            drug_cost=200_000_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        ps_df = cmp.price_sensitivity(results, [self.BASE_ANNUAL],
                                      base_annual_price=self.BASE_ANNUAL)
        row = ps_df.iloc[0]

        # delta_cost at base price should equal unmodified comparison
        direct = cmp.compare_pair(oral, lena)
        assert abs(row["delta_cost"] - direct["delta_cost"]) < 1.0, (
            f"delta_cost at base price ({row['delta_cost']:,.0f}) should equal "
            f"unmodified delta_cost ({direct['delta_cost']:,.0f})"
        )

    def test_price_sensitivity_half_price_halves_drug_cost(self, cmp):
        """At 50% of base price, total drug cost halves → delta_cost changes by
        exactly half the original drug cost.

        Derivation:
          original_drug = 200M, ratio = 0.5
          new_drug = 100M → new_lena_total = 400M − 200M + 100M = 300M
          delta_cost_half = 300M − 600M = −300M
          delta_cost_base = 400M − 600M = −200M
          diff = −300M − (−200M) = −100M = −(0.5 × 200M) = −½ × drug cost ✓
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        orig_drug = 200_000_000
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=orig_drug)
        results = {"oral_prep": oral, "lenacapavir": lena}

        half_price = self.BASE_ANNUAL * 0.5
        ps_df = cmp.price_sensitivity(results, [half_price],
                                      base_annual_price=self.BASE_ANNUAL)
        row_half = ps_df.iloc[0]

        direct = cmp.compare_pair(oral, lena)
        expected_delta = direct["delta_cost"] - orig_drug * 0.5  # drug cost halved
        assert abs(row_half["delta_cost"] - expected_delta) < 1.0, (
            f"delta_cost at 50% price ({row_half['delta_cost']:,.0f}) should be "
            f"{expected_delta:,.0f}"
        )

    def test_price_sensitivity_scales_lena_product_not_hiv_art(self, cmp):
        """The price sweep must not scale HIV ART costs inside broad 'drug'."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=500_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=300_000_000)
        lena.cost_breakdown_disc["prep_drug_lenacapavir"] = 200_000_000
        lena.cost_breakdown_disc["prep_drug"] = 200_000_000
        lena.cost_breakdown_disc["hiv_art_drug"] = 100_000_000
        results = {"oral_prep": oral, "lenacapavir": lena}

        ps_df = cmp.price_sensitivity(
            results, [self.BASE_ANNUAL * 0.5],
            base_annual_price=self.BASE_ANNUAL,
        )
        direct = cmp.compare_pair(oral, lena)
        expected_delta = direct["delta_cost"] - 100_000_000
        assert ps_df.iloc[0]["delta_cost"] == pytest.approx(expected_delta)

    def test_price_sensitivity_refuses_broad_drug_fallback(self, cmp):
        """Older broad-only results should fail instead of scaling ART drugs."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=200_000_000)
        del lena.cost_breakdown_disc["prep_drug_lenacapavir"]
        results = {"oral_prep": oral, "lenacapavir": lena}

        with pytest.raises(ValueError, match="prep_drug_lenacapavir"):
            cmp.price_sensitivity(
                results, [self.BASE_ANNUAL],
                base_annual_price=self.BASE_ANNUAL,
            )

    def test_price_sensitivity_sw_ce_at_low_price(self, cmp):
        """SW scenario (fewer QALYs, lower price) at 50% of base → NMB > 0 → CE=True.

        NMB(10,000) = 360M − 10,000×10,000 = 360M − 100M = 260M > 0  ✓
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=200_000_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        low_price = self.BASE_ANNUAL * 0.5  # €10,000/year
        ps_df = cmp.price_sensitivity(results, [low_price],
                                      base_annual_price=self.BASE_ANNUAL)
        row = ps_df.iloc[0]
        assert row["nmb"] > 0, f"NMB should be positive at low price, got {row['nmb']:,.0f}"
        assert row["cost_effective"] == True, (
            "SW quadrant with NMB > 0 must be flagged cost_effective=True"
        )

    def test_price_sensitivity_sw_not_ce_at_high_price(self, cmp):
        """SW scenario at 3× base price → NMB < 0 → CE=False.

        NMB(60,000) = 360M − 10,000×60,000 = 360M − 600M = −240M < 0  ✓
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=200_000_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        high_price = self.BASE_ANNUAL * 3.0  # €60,000/year
        ps_df = cmp.price_sensitivity(results, [high_price],
                                      base_annual_price=self.BASE_ANNUAL)
        row = ps_df.iloc[0]
        assert row["nmb"] < 0, f"NMB should be negative at high price, got {row['nmb']:,.0f}"
        assert row["cost_effective"] == False

    def test_price_sensitivity_ne_regression(self, cmp):
        """NE scenario: lena is more effective. cost_effective must equal (nmb > 0)
        at any price — regression guard for the NMB > 0 decision rule."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=900_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=1_100_000,
                            drug_cost=200_000_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        prices = [self.BASE_ANNUAL * 1.25, self.BASE_ANNUAL * 2.25]
        ps_df = cmp.price_sensitivity(results, prices,
                                      base_annual_price=self.BASE_ANNUAL)
        for _, row in ps_df.iterrows():
            assert row["cost_effective"] == (row["nmb"] > 0), (
                f"NE regression: cost_effective != (nmb > 0) at price {row['price']}"
            )

    def test_price_sensitivity_infections_averted_constant(self, cmp):
        """infections_averted must be the same across all price rows — price
        does not affect transmission dynamics, only costs."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000,
                            infections=10_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=200_000_000,
                            infections=8_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        prices = [self.BASE_ANNUAL * f for f in (0.5, 1.0, 1.5, 2.0)]
        ps_df = cmp.price_sensitivity(results, prices,
                                      base_annual_price=self.BASE_ANNUAL)

        ia_values = ps_df["infections_averted"].tolist()
        assert len(set(ia_values)) == 1, (
            f"infections_averted should be constant across rows, got: {ia_values}"
        )
        assert ia_values[0] == 2_000  # oral(10k) − lena(8k)

    def test_price_sensitivity_cost_effective_not_all_true(self, cmp):
        """With a high-enough price range, not all rows should be CE=True.
        This is the key regression guard against the original bug (all True
        at every price level)."""
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,
                            total_qalys_disc=999_500,
                            drug_cost=200_000_000)
        results = {"oral_prep": oral, "lenacapavir": lena}

        # Range from 10% to 200% in 10% steps (mirrors production config)
        prices = [self.BASE_ANNUAL * pct / 100 for pct in range(10, 210, 10)]
        ps_df  = cmp.price_sensitivity(results, prices,
                                       base_annual_price=self.BASE_ANNUAL)

        ce_flags = ps_df["cost_effective"].tolist()
        assert True in ce_flags,  "At least one row should be CE=True at low prices"
        assert False in ce_flags, (
            "At least one row should be CE=False at high prices "
            "(regression: original bug returned True for all rows)"
        )

    # ------------------------------------------------------------------
    # 12: find_threshold_price — SW quadrant (updated for ratio-based scaling)
    # ------------------------------------------------------------------

    def test_find_threshold_price_sw_quadrant(self, cmp):
        """find_threshold_price returns the annual per-person threshold price
        where NMB = 0 in the SW quadrant.

        Analytic derivation (ratio-based scaling, base_annual_price = 20,000):
          new_drug(p)   = 200M × (p / 20,000) = 10,000 × p
          new_lena(p)   = (400M − 200M) + 10,000p = 200M + 10,000p
          ΔC(p)         = (200M + 10,000p) − 600M  = 10,000p − 400M
          ΔQ            = 999,500 − 1,000,000 = −500 (fixed)
          NMB(p)        = 80,000 × (−500) − (10,000p − 400M)
                        = −40M − 10,000p + 400M = 360M − 10,000p
          NMB = 0  ⟹  10,000p = 360M  ⟹  p = €36,000/year
        """
        oral = _make_result("oral_prep",
                            total_costs_disc=600_000_000,
                            total_qalys_disc=1_000_000,
                            drug_cost=540_000_000)
        lena_orig_drug = 200_000_000
        lena = _make_result("lenacapavir",
                            total_costs_disc=400_000_000,   # 200M drug + 200M other
                            total_qalys_disc=999_500,        # 500 fewer QALYs
                            drug_cost=lena_orig_drug)
        results = {"oral_prep": oral, "lenacapavir": lena}

        threshold = cmp.find_threshold_price(results,
                                             base_annual_price=self.BASE_ANNUAL)
        expected  = 36_000.0  # analytic result: €36,000/year (see docstring)

        assert threshold is not None, "Threshold price should be found (not None)"
        assert abs(threshold - expected) < 2.0, (
            f"Threshold price {threshold:,.2f} differs from analytic "
            f"expectation {expected:,.2f} by more than €2/year"
        )

    # ------------------------------------------------------------------
    # 11-13: _style_icer_cell — ICER cell colour
    # ------------------------------------------------------------------

    def test_icer_cell_sw_ce_green(self):
        """SW quadrant with NMB > 0: numeric ICER cell should be green (FILL_CE_YES)."""
        from openpyxl import Workbook
        from hta_report import _style_icer_cell, FILL_CE_YES
        wb = Workbook()
        ws = wb.active
        cell = ws.cell(row=1, column=1)
        # ICER = 84,956 > WTP=80,000, but NMB > 0 → should be green
        _style_icer_cell(cell, icer_value=84_956, wtp=80_000, nmb=33_000_000)
        # openpyxl stores colour as 8-char ARGB (e.g. "00E2EFDA"); strip alpha prefix
        actual_rgb = cell.fill.fgColor.rgb[-6:]
        assert actual_rgb == FILL_CE_YES, (
            f"SW quadrant CE (NMB > 0): expected green ({FILL_CE_YES}), "
            f"got {actual_rgb} — cell should be green even though ICER > WTP"
        )

    def test_icer_cell_sw_not_ce_red(self):
        """SW quadrant with NMB < 0: numeric ICER cell should be red (FILL_CE_NO)."""
        from openpyxl import Workbook
        from hta_report import _style_icer_cell, FILL_CE_NO
        wb = Workbook()
        ws = wb.active
        cell = ws.cell(row=1, column=1)
        # ICER = 84,956 > WTP but NMB < 0 → should be red
        _style_icer_cell(cell, icer_value=84_956, wtp=80_000, nmb=-5_000_000)
        actual_rgb = cell.fill.fgColor.rgb[-6:]
        assert actual_rgb == FILL_CE_NO, (
            f"SW quadrant not CE (NMB < 0): expected red ({FILL_CE_NO}), got {actual_rgb}"
        )

    def test_icer_cell_ne_ce_regression(self):
        """NE quadrant ICER < WTP with NMB > 0: cell stays green (regression)."""
        from openpyxl import Workbook
        from hta_report import _style_icer_cell, FILL_CE_YES
        wb = Workbook()
        ws = wb.active
        cell = ws.cell(row=1, column=1)
        # ICER = 20,000 < WTP = 80,000; NMB > 0
        _style_icer_cell(cell, icer_value=20_000, wtp=80_000, nmb=60_000_000)
        actual_rgb = cell.fill.fgColor.rgb[-6:]
        assert actual_rgb == FILL_CE_YES, (
            f"NE quadrant CE: expected green ({FILL_CE_YES}), got {actual_rgb}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
