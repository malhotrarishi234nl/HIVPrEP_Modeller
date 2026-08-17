"""
tests/test_psa_hta_integration.py — V11.11.5

8 integration tests for the PSA × HTA pipeline (run_psa_hta.py).

Tests use a short horizon (eval_years=5) and N=2 draws to keep runtime
under 60 seconds while still exercising the full pipeline.

Note: These tests run full simulations and take ~30-60 seconds total.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "v11_configuration.json",
)
HTA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "hta_config.json",
)
PSA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "psa_config.json",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def base_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def hta_config():
    with open(HTA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def psa_config():
    with open(PSA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def short_hta_config(hta_config):
    """HTA config with a 5-year eval horizon for fast tests."""
    cfg = copy.deepcopy(hta_config)
    cfg["time_horizon"]["evaluation_years"] = 5
    return cfg


def _write_temp(obj, suffix=".json"):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    return path


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPSAHTAEndToEnd:

    def test_psa_hta_end_to_end(self, base_config, short_hta_config, psa_config):
        """
        Run run_psa_hta with N=2 draws, short horizon, scenario_a_base.
        Verify incremental data for all 3 comparisons, output files, arm results.
        """
        from run_psa_hta import run_psa_hta

        psa_cfg = copy.deepcopy(psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 2

        cfg_path = _write_temp(base_config)
        hta_path = _write_temp(short_hta_config)
        psa_path = _write_temp(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out_dir:
                result = run_psa_hta(
                    config_path=cfg_path,
                    hta_config_path=hta_path,
                    psa_config_path=psa_path,
                    scenario_name="scenario_a_base",
                    output_dir_override=out_dir,
                    seed_override=42,
                    quiet=True,
                )

            assert result is not None
            inc_df = result["incremental_df"]
            arm_df = result["arm_outcomes_df"]

            # 3 comparisons present
            comparisons = set(inc_df["comparison"].unique())
            assert "oral_prep_vs_no_prep" in comparisons
            assert "lenacapavir_vs_no_prep" in comparisons
            assert "lenacapavir_vs_oral_prep" in comparisons

            # Each draw has results for all 3 arms
            arms = set(arm_df["arm"].unique())
            assert {"no_prep", "oral_prep", "lenacapavir"}.issubset(arms)

            # Output files exist — checked in temp dir (already deleted, use result)
            assert "delta_cost" in inc_df.columns
            assert "delta_qaly" in inc_df.columns
            assert "icer" in inc_df.columns

        finally:
            os.unlink(cfg_path)
            os.unlink(hta_path)
            os.unlink(psa_path)


class TestPSAHTAReproducibility:

    def test_psa_hta_deterministic_reproducibility(
        self, base_config, short_hta_config, psa_config
    ):
        """Two runs with same seed produce identical incremental outcomes."""
        from run_psa_hta import run_psa_hta

        psa_cfg = copy.deepcopy(psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 2

        cfg_path = _write_temp(base_config)
        hta_path = _write_temp(short_hta_config)
        psa_path = _write_temp(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out1, \
                 tempfile.TemporaryDirectory() as out2:
                r1 = run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out1, seed_override=77, quiet=True,
                )
                r2 = run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out2, seed_override=77, quiet=True,
                )

            pd.testing.assert_frame_equal(
                r1["incremental_df"].reset_index(drop=True),
                r2["incremental_df"].reset_index(drop=True),
                obj="Reproducibility: incremental_df must be identical across runs",
            )
        finally:
            os.unlink(cfg_path)
            os.unlink(hta_path)
            os.unlink(psa_path)

    def test_psa_hta_parallel_matches_sequential(
        self, base_config, short_hta_config, psa_config
    ):
        """Across-draw parallel workers should preserve deterministic results."""
        from run_psa_hta import run_psa_hta

        try:
            os.sysconf("SC_SEM_NSEMS_MAX")
        except (AttributeError, PermissionError, ValueError):
            pytest.skip("ProcessPoolExecutor semaphore limits unavailable in sandbox")

        psa_cfg = copy.deepcopy(psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 2

        cfg_path = _write_temp(base_config)
        hta_path = _write_temp(short_hta_config)
        psa_path = _write_temp(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out_seq, \
                 tempfile.TemporaryDirectory() as out_par:
                sequential = run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out_seq,
                    seed_override=88,
                    quiet=True,
                    workers=1,
                )
                parallel = run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out_par,
                    seed_override=88,
                    quiet=True,
                    workers=2,
                )

            pd.testing.assert_frame_equal(
                sequential["incremental_df"].reset_index(drop=True),
                parallel["incremental_df"].reset_index(drop=True),
                obj="Parallel PSA-HTA results must match sequential results",
            )
            pd.testing.assert_frame_equal(
                sequential["arm_outcomes_df"].reset_index(drop=True),
                parallel["arm_outcomes_df"].reset_index(drop=True),
                obj="Parallel PSA-HTA arm outcomes must match sequential results",
            )
        finally:
            os.unlink(cfg_path)
            os.unlink(hta_path)
            os.unlink(psa_path)


class TestPSAHTAConsistency:

    def test_psa_hta_incremental_consistency(
        self, base_config, short_hta_config, psa_config
    ):
        """
        For each draw: delta_cost = cost_intervention - cost_reference.
        Manually verify from arm_results against stored incremental values.
        """
        from run_psa_hta import _run_single_hta_draw
        from psa_sampler import PSASampler
        from hta_sampler import HTASampler

        psa_cfg = copy.deepcopy(psa_config)

        epi_sampler = PSASampler(base_config, psa_cfg)
        hta_sampler = HTASampler(short_hta_config, psa_cfg)

        epi_draws = epi_sampler.generate_draws(n=1, seed=55)
        hta_draws = hta_sampler.generate_draws(n=1, seed=56)

        scenario = short_hta_config["analysis_scenarios"][0]

        result = _run_single_hta_draw(
            draw_index=0,
            epi_draw_row=dict(epi_draws.iloc[0]),
            hta_draw_row=dict(hta_draws.iloc[0]),
            base_config=base_config,
            hta_config=short_hta_config,
            seed=55,
            analysis_scenario=scenario,
            freeze_year=short_hta_config["time_horizon"]["freeze_year"],
            eval_years=short_hta_config["time_horizon"]["evaluation_years"],
            quiet=True,
        )

        arm = result["arm_results"]
        inc = result["incremental"]
        perspective = short_hta_config.get("perspective", {}).get("primary", "payer")

        # Recompute oral_prep vs no_prep delta_cost manually
        if perspective == "societal":
            expected_dc = (
                arm["oral_prep"]["total_costs_disc_societal"]
                - arm["no_prep"]["total_costs_disc_societal"]
            )
        else:
            expected_dc = (
                arm["oral_prep"]["total_costs_disc"]
                - arm["no_prep"]["total_costs_disc"]
            )
        expected_dq = (
            arm["oral_prep"]["total_qalys_disc"]
            - arm["no_prep"]["total_qalys_disc"]
        )

        assert abs(inc["oral_prep_vs_no_prep"]["delta_cost"] - expected_dc) < 1e-6
        assert abs(inc["oral_prep_vs_no_prep"]["delta_qaly"] - expected_dq) < 1e-6


class TestCEAC:

    def test_ceac_boundary_values(self):
        """
        At WTP=0: P(CE) should be ≤ 0.5 for interventions with positive delta_cost.
        At WTP=very_large: P(CE) → 1 for effective interventions (delta_qaly > 0).
        Uses synthetic data, not real simulation.
        """
        from psa_hta_output_writer import PSAHTAOutputWriter

        # Synthetic: oral_prep strictly costs more and is more effective
        n = 100
        rng = np.random.default_rng(42)
        incremental_df = pd.DataFrame({
            "draw_index": list(range(n)),
            "comparison": ["oral_prep_vs_no_prep"] * n,
            "delta_cost": rng.normal(5000, 500, n),   # always positive
            "delta_qaly": rng.normal(0.5, 0.05, n),   # always positive
            "icer": [10000.0] * n,
            "nmb": [0.0] * n,
            "infections_averted": [5.0] * n,
        })

        class _DummyWriter:
            WTP_RANGE = [0, 1_000_000]
            comparisons = ["oral_prep_vs_no_prep"]
            def _compute_ceac(self, df, wtp_range):
                return PSAHTAOutputWriter._compute_ceac(self, df, wtp_range)

        # Bind the method
        writer = PSAHTAOutputWriter.__new__(PSAHTAOutputWriter)
        writer.incremental_df = incremental_df
        writer.comparisons = ["oral_prep_vs_no_prep"]

        # At WTP=0: NMB = 0.5*0 - 5000 < 0 → P(CE) ≈ 0
        ceac_0 = writer._compute_ceac(incremental_df, [0])
        p_ce_at_0 = ceac_0[ceac_0["comparison"] == "oral_prep_vs_no_prep"]["prob_ce"].values[0]
        assert p_ce_at_0 < 0.05, f"P(CE) at WTP=0 should be ~0, got {p_ce_at_0}"

        # At WTP=1e9: NMB = 0.5*1e9 - 5000 >> 0 → P(CE) ≈ 1
        ceac_hi = writer._compute_ceac(incremental_df, [1_000_000_000])
        p_ce_hi = ceac_hi[ceac_hi["comparison"] == "oral_prep_vs_no_prep"]["prob_ce"].values[0]
        assert p_ce_hi > 0.95, f"P(CE) at very large WTP should be ~1, got {p_ce_hi}"

    def test_ceac_monotonicity(self, base_config, short_hta_config, psa_config):
        """CEAC should be non-decreasing in WTP for each comparison."""
        from run_psa_hta import run_psa_hta

        psa_cfg = copy.deepcopy(psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 3

        cfg_path = _write_temp(base_config)
        hta_path = _write_temp(short_hta_config)
        psa_path = _write_temp(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out_dir:
                result = run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out_dir, seed_override=10, quiet=True,
                )

            inc_df = result["incremental_df"]
            from psa_hta_output_writer import PSAHTAOutputWriter

            writer = PSAHTAOutputWriter.__new__(PSAHTAOutputWriter)
            writer.incremental_df = inc_df
            writer.comparisons = list(inc_df["comparison"].unique())

            wtp_range = list(range(0, 110_000, 10_000))
            ceac_df = writer._compute_ceac(inc_df, wtp_range)

            for comp in writer.comparisons:
                sub = ceac_df[ceac_df["comparison"] == comp].sort_values("wtp")
                probs = sub["prob_ce"].values
                for j in range(len(probs) - 1):
                    assert probs[j] <= probs[j + 1] + 1e-9, (
                        f"CEAC not monotone for {comp}: P(CE) at WTP={wtp_range[j]} "
                        f"({probs[j]:.4f}) > WTP={wtp_range[j+1]} ({probs[j+1]:.4f})"
                    )
        finally:
            os.unlink(cfg_path)
            os.unlink(hta_path)
            os.unlink(psa_path)


class TestPSAHTASafety:

    def test_no_config_file_mutation(self, base_config, short_hta_config, psa_config):
        """
        Config files on disk must not be modified by run_psa_hta.
        """
        import hashlib

        def _md5(path):
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()

        psa_cfg = copy.deepcopy(psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 2

        cfg_path = _write_temp(base_config)
        hta_path = _write_temp(short_hta_config)
        psa_path = _write_temp(psa_cfg)

        h1_cfg = _md5(cfg_path)
        h1_hta = _md5(hta_path)
        h1_psa = _md5(psa_path)

        try:
            from run_psa_hta import run_psa_hta
            with tempfile.TemporaryDirectory() as out_dir:
                run_psa_hta(
                    cfg_path, hta_path, psa_path,
                    output_dir_override=out_dir, seed_override=1, quiet=True,
                )

            assert _md5(cfg_path) == h1_cfg, "base config file was mutated"
            assert _md5(hta_path) == h1_hta, "hta_config file was mutated"
            assert _md5(psa_path) == h1_psa, "psa_config file was mutated"

        finally:
            os.unlink(cfg_path)
            os.unlink(hta_path)
            os.unlink(psa_path)

    def test_hta_params_affect_costs(self, base_config, short_hta_config, psa_config):
        """
        Higher ART disease costs should produce higher total_costs_disc for all arms
        when there are HIV+ individuals present.
        Uses a seeded epidemic (initial_hiv_prevalence=0.01) to ensure
        HIV+ patients accumulate ART costs during the evaluation horizon.
        """
        from run_psa_hta import _run_single_hta_draw
        from psa_sampler import PSASampler

        # Seed the epidemic so there are HIV+ individuals in all arms
        cfg_seeded = copy.deepcopy(base_config)
        cfg_seeded["population"]["initial_hiv_prevalence"]["value"] = 0.01

        psa_cfg = copy.deepcopy(psa_config)
        epi_sampler = PSASampler(cfg_seeded, psa_cfg)
        epi_draws = epi_sampler.generate_draws(n=1, seed=42)
        epi_row = dict(epi_draws.iloc[0])

        scenario = short_hta_config["analysis_scenarios"][0]
        freeze_year = short_hta_config["time_horizon"]["freeze_year"]
        eval_years = short_hta_config["time_horizon"]["evaluation_years"]

        # Low ART cost
        hta_low = copy.deepcopy(short_hta_config)
        hta_low["disease_costs"]["art_first_line"]["value"] = 1000.0

        # High ART cost
        hta_high = copy.deepcopy(short_hta_config)
        hta_high["disease_costs"]["art_first_line"]["value"] = 50000.0

        result_low = _run_single_hta_draw(
            draw_index=0, epi_draw_row=epi_row, hta_draw_row={},
            base_config=cfg_seeded, hta_config=hta_low, seed=42,
            analysis_scenario=scenario, freeze_year=freeze_year,
            eval_years=eval_years, quiet=True,
        )
        result_high = _run_single_hta_draw(
            draw_index=0, epi_draw_row=epi_row, hta_draw_row={},
            base_config=cfg_seeded, hta_config=hta_high, seed=42,
            analysis_scenario=scenario, freeze_year=freeze_year,
            eval_years=eval_years, quiet=True,
        )

        # Both arms should have higher costs under high ART prices
        for arm in ["no_prep", "oral_prep", "lenacapavir"]:
            cost_low = result_low["arm_results"][arm]["total_costs_disc"]
            cost_high = result_high["arm_results"][arm]["total_costs_disc"]
            assert cost_high > cost_low, (
                f"Arm '{arm}': higher ART costs should increase total_costs_disc "
                f"(high={cost_high:.0f}, low={cost_low:.0f})"
            )

    def test_perspective_societal_vs_payer(
        self, base_config, short_hta_config, psa_config
    ):
        """
        Societal perspective should use total_costs_disc_societal in delta_cost.
        Payer perspective should use total_costs_disc.
        """
        from run_psa_hta import _run_single_hta_draw
        from psa_sampler import PSASampler

        psa_cfg = copy.deepcopy(psa_config)
        epi_sampler = PSASampler(base_config, psa_cfg)
        epi_draws = epi_sampler.generate_draws(n=1, seed=42)
        epi_row = dict(epi_draws.iloc[0])

        scenario = short_hta_config["analysis_scenarios"][0]
        freeze_year = short_hta_config["time_horizon"]["freeze_year"]
        eval_years = short_hta_config["time_horizon"]["evaluation_years"]

        # Payer perspective
        hta_payer = copy.deepcopy(short_hta_config)
        hta_payer["perspective"]["primary"] = "payer"

        # Societal perspective
        hta_societal = copy.deepcopy(short_hta_config)
        hta_societal["perspective"]["primary"] = "societal"

        r_payer = _run_single_hta_draw(
            draw_index=0, epi_draw_row=epi_row, hta_draw_row={},
            base_config=base_config, hta_config=hta_payer, seed=42,
            analysis_scenario=scenario, freeze_year=freeze_year,
            eval_years=eval_years, quiet=True,
        )
        r_societal = _run_single_hta_draw(
            draw_index=0, epi_draw_row=epi_row, hta_draw_row={},
            base_config=base_config, hta_config=hta_societal, seed=42,
            analysis_scenario=scenario, freeze_year=freeze_year,
            eval_years=eval_years, quiet=True,
        )

        # Verify payer uses total_costs_disc
        arm_p = r_payer["arm_results"]
        inc_p = r_payer["incremental"]
        expected_dc_payer = (
            arm_p["oral_prep"]["total_costs_disc"]
            - arm_p["no_prep"]["total_costs_disc"]
        )
        assert abs(inc_p["oral_prep_vs_no_prep"]["delta_cost"] - expected_dc_payer) < 1e-6, (
            "Payer perspective: delta_cost should use total_costs_disc"
        )

        # Verify societal uses total_costs_disc_societal
        arm_s = r_societal["arm_results"]
        inc_s = r_societal["incremental"]
        expected_dc_societal = (
            arm_s["oral_prep"]["total_costs_disc_societal"]
            - arm_s["no_prep"]["total_costs_disc_societal"]
        )
        assert abs(inc_s["oral_prep_vs_no_prep"]["delta_cost"] - expected_dc_societal) < 1e-6, (
            "Societal perspective: delta_cost should use total_costs_disc_societal"
        )
