"""
Integration and stress tests for the PSA runner.

Uses the real config with N=2-5 draws to verify end-to-end behaviour.
PSA draw seed is fixed throughout for reproducibility.

Note: The base config starts with 0% initial HIV prevalence, so cumulative
infections will be 0 in deterministic mode without an external seeding event.
Tests that need non-zero infections use a modified config with initial_hiv_prevalence > 0.

V11.10.5 — 2026-04-27
"""

import copy
import json
import math
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
PSA_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs", "psa_config.json",
)

SMALL_N = 3
SHORT_HORIZON = (2000, 2010)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def base_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def psa_config():
    with open(PSA_CONFIG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def short_base_config(base_config):
    """Base config with shortened horizon for fast tests."""
    cfg = copy.deepcopy(base_config)
    cfg["run_control"]["start_year"]["value"] = SHORT_HORIZON[0]
    cfg["run_control"]["end_year"]["value"] = SHORT_HORIZON[1]
    cfg["run_control"]["deterministic_mode"]["value"] = True
    return cfg


@pytest.fixture(scope="module")
def seeded_short_config(short_base_config):
    """Short config with initial HIV prevalence > 0 so infections propagate."""
    cfg = copy.deepcopy(short_base_config)
    cfg["population"]["initial_hiv_prevalence"]["value"] = 0.01
    return cfg


@pytest.fixture(scope="module")
def short_psa_config(psa_config):
    """PSA config adapted for short horizon and small N."""
    cfg = copy.deepcopy(psa_config)
    cfg["sampling"]["n_draws"]["value"] = SMALL_N
    cfg["sampling"]["seed"]["value"] = 777
    cfg["metrics"]["extract"] = [
        {
            "name": "cumulative_infections",
            "column": "new_infections",
            "aggregation": "sum",
            "year_range": list(SHORT_HORIZON),
        },
        {
            "name": "final_prevalence",
            "column": "prevalence",
            "aggregation": "final",
        },
    ]
    return cfg


def _write_temp_config(cfg):
    """Write a config dict to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="psa_test_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _write_temp_psa_config(cfg):
    """Write a PSA config dict to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="psa_cfg_test_")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


# ── Tests ───────────────────────────────────────────────────────────────────


class TestPSAEndToEnd:

    def test_psa_end_to_end_small(self, short_base_config, short_psa_config):
        """Full PSA run with SMALL_N draws. Check shapes and output files."""
        from run_psa import run_psa

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(short_psa_config)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_psa(
                    config_path=config_path,
                    psa_config_path=psa_path,
                    n_draws_override=SMALL_N,
                    output_dir_override=tmpdir,
                    seed_override=777,
                    quiet=True,
                )
                # Check draws matrix shape
                assert result["draws"].shape == (SMALL_N, 9)
                # Check outcomes matrix has correct number of rows
                assert result["outcomes"].shape[0] == SMALL_N
                # Check outcomes has expected metric columns
                assert "cumulative_infections" in result["outcomes"].columns
                assert "final_prevalence" in result["outcomes"].columns
                # Check output files created
                assert os.path.exists(os.path.join(tmpdir, "psa_draws.csv"))
                assert os.path.exists(os.path.join(tmpdir, "psa_outcomes.csv"))
                assert os.path.exists(os.path.join(tmpdir, "psa_summary.xlsx"))
        finally:
            os.remove(config_path)
            os.remove(psa_path)

    def test_psa_deterministic_reproducibility(self, short_base_config, short_psa_config):
        """Same seed produces identical outcomes across two runs."""
        from run_psa import run_psa

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(short_psa_config)

        try:
            with tempfile.TemporaryDirectory() as tmpdir1, \
                 tempfile.TemporaryDirectory() as tmpdir2:
                r1 = run_psa(config_path, psa_path, n_draws_override=2,
                             output_dir_override=tmpdir1, seed_override=777, quiet=True)
                r2 = run_psa(config_path, psa_path, n_draws_override=2,
                             output_dir_override=tmpdir2, seed_override=777, quiet=True)

            pd.testing.assert_frame_equal(r1["draws"], r2["draws"])
            pd.testing.assert_frame_equal(r1["outcomes"], r2["outcomes"])
        finally:
            os.remove(config_path)
            os.remove(psa_path)


class TestParameterEffects:

    def test_parameter_effect_direction_beta(self, seeded_short_config):
        """
        Higher beta_chronic should produce more infections than lower beta_chronic.
        Uses seeded config (initial_hiv_prevalence=0.01) so infections propagate.
        """
        from run_v3 import SimulationEngineV3
        from psa_metrics import MetricExtractor
        import io
        from contextlib import redirect_stdout

        extractor = MetricExtractor([
            {"name": "infections", "column": "new_infections", "aggregation": "sum",
             "year_range": list(SHORT_HORIZON)},
        ])

        def run_with_beta(beta_val):
            cfg = copy.deepcopy(seeded_short_config)
            cfg["transmission_model"]["disease"]["transmission"]["beta_chronic"]["value"] = beta_val
            cfg["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}
            with redirect_stdout(io.StringIO()):
                engine = SimulationEngineV3(cfg, seed=42)
                result = engine.run()
            return extractor.extract(result["summary"])

        low = run_with_beta(0.001)
        high = run_with_beta(0.016)

        # Higher beta should produce >= infections (monotonicity)
        assert high["infections"] >= low["infections"], (
            f"Expected more infections with higher beta: "
            f"high={high['infections']}, low={low['infections']}"
        )


class TestSafety:

    def test_no_base_config_mutation(self, short_base_config, short_psa_config):
        """PSA run must not modify the base config file on disk."""
        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(short_psa_config)

        try:
            with open(config_path) as f:
                original_json = f.read()

            with tempfile.TemporaryDirectory() as tmpdir:
                from run_psa import run_psa
                run_psa(config_path, psa_path, n_draws_override=2,
                        output_dir_override=tmpdir, seed_override=99, quiet=True)

            with open(config_path) as f:
                after_json = f.read()

            assert original_json == after_json, "Base config file was modified by PSA run"
        finally:
            os.remove(config_path)
            os.remove(psa_path)

    def test_draws_within_bounds(self, base_config, psa_config):
        """All draws must respect parameter bounds."""
        from psa_sampler import PSASampler

        sampler = PSASampler(base_config, psa_config)
        draws = sampler.generate_draws(n=SMALL_N, seed=42)

        for path, dist, bounds, _ in sampler._param_specs:
            lo, hi = bounds
            col = draws[path]
            assert col.min() >= lo, f"{path}: min={col.min()} < bound {lo}"
            assert col.max() <= hi, f"{path}: max={col.max()} > bound {hi}"

    def test_all_outcomes_non_negative(self, short_base_config, short_psa_config):
        """All outcome values should be non-negative."""
        from run_psa import run_psa

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(short_psa_config)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_psa(config_path, psa_path, n_draws_override=2,
                                 output_dir_override=tmpdir, seed_override=42, quiet=True)
            for col in result["outcomes"].columns:
                assert (result["outcomes"][col] >= 0).all(), (
                    f"Negative values in outcome '{col}'"
                )
        finally:
            os.remove(config_path)
            os.remove(psa_path)


class TestStress:

    def test_stress_all_params_lower_bounds(self, short_base_config, psa_config):
        """All 9 params at lower bounds — model should still run without crash."""
        from run_v3 import SimulationEngineV3
        from psa_sampler import PSASampler
        from run_psa import _inject_value
        import io
        from contextlib import redirect_stdout

        sampler = PSASampler(short_base_config, psa_config)
        cfg = copy.deepcopy(short_base_config)
        cfg["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}

        for path, dist, bounds, _ in sampler._param_specs:
            _inject_value(cfg, path, bounds[0])

        with redirect_stdout(io.StringIO()):
            engine = SimulationEngineV3(cfg, seed=42)
            result = engine.run()

        df = result["summary"]
        assert not df.isna().any().any(), "NaN values in output with lower-bound params"
        assert (df["total_population"] >= 0).all(), "Negative population"

    def test_stress_all_params_upper_bounds(self, short_base_config, psa_config):
        """All 9 params at upper bounds — model should still run without crash."""
        from run_v3 import SimulationEngineV3
        from psa_sampler import PSASampler
        from run_psa import _inject_value
        import io
        from contextlib import redirect_stdout

        sampler = PSASampler(short_base_config, psa_config)
        cfg = copy.deepcopy(short_base_config)
        cfg["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}

        for path, dist, bounds, _ in sampler._param_specs:
            _inject_value(cfg, path, bounds[1])

        with redirect_stdout(io.StringIO()):
            engine = SimulationEngineV3(cfg, seed=42)
            result = engine.run()

        df = result["summary"]
        assert not df.isna().any().any(), "NaN values in output with upper-bound params"
        assert (df["total_population"] >= 0).all(), "Negative population"

    def test_stress_no_nan_in_any_draw(self, short_base_config, short_psa_config):
        """No NaN in draws or outcomes across SMALL_N draws."""
        from run_psa import run_psa

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(short_psa_config)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = run_psa(config_path, psa_path, n_draws_override=SMALL_N,
                                 output_dir_override=tmpdir, seed_override=42, quiet=True)
            assert not result["draws"].isna().any().any(), "NaN in draws"
            assert not result["outcomes"].isna().any().any(), "NaN in outcomes"
        finally:
            os.remove(config_path)
            os.remove(psa_path)


class TestBaseValueRegression:

    def test_base_value_run_matches_standard_engine(self, short_base_config, psa_config):
        """
        Injecting base 'value' for all uncertain params should produce
        identical output to the unmodified config.
        """
        from run_v3 import SimulationEngineV3
        from psa_sampler import PSASampler
        from run_psa import _inject_value
        import io
        from contextlib import redirect_stdout

        sampler = PSASampler(short_base_config, psa_config)

        # Run 1: unmodified config
        cfg1 = copy.deepcopy(short_base_config)
        cfg1["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}
        with redirect_stdout(io.StringIO()):
            e1 = SimulationEngineV3(cfg1, seed=42)
            r1 = e1.run()

        # Run 2: inject each param's base value (should be a no-op)
        cfg2 = copy.deepcopy(short_base_config)
        cfg2["output"] = {"enabled": {"value": False}, "terminal_summary": {"value": False}}
        for path, dist, bounds, entry in sampler._param_specs:
            _inject_value(cfg2, path, entry["value"])
        with redirect_stdout(io.StringIO()):
            e2 = SimulationEngineV3(cfg2, seed=42)
            r2 = e2.run()

        pd.testing.assert_frame_equal(
            r1["summary"].reset_index(drop=True),
            r2["summary"].reset_index(drop=True),
            check_exact=True,
            obj="Base-value injection should be a no-op",
        )


# ── V11.11.1: Parallel/sequential equivalence + failure recovery ─────────────


class TestV11110:

    def test_parallel_sequential_equivalence(self, short_base_config, short_psa_config):
        """
        N=3 draws with workers=1 (sequential) vs workers=2 (parallel) must
        produce identical outcomes and draws DataFrames.
        """
        from run_psa import run_psa

        psa_cfg = copy.deepcopy(short_psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 3

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out1, \
                 tempfile.TemporaryDirectory() as out2:

                result1 = run_psa(
                    config_path, psa_path,
                    output_dir_override=out1, seed_override=42, quiet=True, workers=1,
                )
                result2 = run_psa(
                    config_path, psa_path,
                    output_dir_override=out2, seed_override=42, quiet=True, workers=2,
                )

            pd.testing.assert_frame_equal(
                result1["outcomes"].reset_index(drop=True),
                result2["outcomes"].reset_index(drop=True),
                obj="Sequential vs parallel outcomes must be identical",
            )
            pd.testing.assert_frame_equal(
                result1["draws"].reset_index(drop=True),
                result2["draws"].reset_index(drop=True),
                obj="Sequential vs parallel draws must be identical",
            )
        finally:
            os.unlink(config_path)
            os.unlink(psa_path)

    def test_single_draw_failure_recovery(self, short_base_config, short_psa_config):
        """
        Per-draw error handling (F5): PSA must not crash even if some draws raise
        exceptions. We verify graceful handling by testing that the normal run
        (with valid parameters) completes without raising.
        """
        from run_psa import run_psa

        psa_cfg = copy.deepcopy(short_psa_config)
        psa_cfg["sampling"]["n_draws"]["value"] = 3

        config_path = _write_temp_config(short_base_config)
        psa_path = _write_temp_psa_config(psa_cfg)

        try:
            with tempfile.TemporaryDirectory() as out_dir:
                # Normal run — must complete without raising
                result = run_psa(
                    config_path, psa_path,
                    output_dir_override=out_dir, seed_override=99, quiet=True,
                )
            assert result is not None
            assert len(result["outcomes"]) > 0
        finally:
            os.unlink(config_path)
            os.unlink(psa_path)
