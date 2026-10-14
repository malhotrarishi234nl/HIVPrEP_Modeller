"""
Tests for SNPE calibration components:
  - calib_snpe_summary_stats: shape, log-transform, sentinel, x_obs
  - run_snpe_calibration: prior building, tensor helpers, bounds validation,
    worker schema (no model required), smoke integration (slow, marked)
  - _train_snpe / _extract_training_losses: proposal-correction fix -- round
    0 omits the proposal kwarg, round >= 1 with a real proposal includes it,
    force_first_round_loss defaults False and is forwarded when requested
  - _round_summary: additive loss/fallback diagnostic fields
  - --force-first-round-loss CLI flag: default False, parses to True

Run fast tests:   pytest tests/test_snpe_calibration.py
Run all (slow):   pytest -m slow tests/test_snpe_calibration.py
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_snpe_summary_stats import (
    COLLAPSE_SENTINEL,
    DEFAULT_SUMMARY_STAT_SPEC,
    N_SUMMARY_STATS,
    STAT_COLUMN_PREFIX,
    compute_summary_stats,
    observed_summary_stats,
    stat_row_fields,
    summary_stat_labels,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_outputs(value: float = 1000.0) -> dict:
    """Build a minimal model_outputs dict covering all default stat years."""
    outputs: dict[str, dict[int, float]] = {}
    for indicator, years in DEFAULT_SUMMARY_STAT_SPEC:
        outputs[indicator] = {year: value for year in years}
    return outputs


def _identity_worker(payload: dict) -> dict:
    """Picklable stand-in for _run_snpe_particle_worker (module-level so
    ProcessPoolExecutor can pickle it across processes)."""
    return {"particle_id": payload["particle_id"]}


def _reversed_completion_worker(payload: dict) -> dict:
    """Picklable stand-in that makes later-submitted payloads finish first,
    to prove result ordering isn't accidentally correct just because
    completion happens to match submission order."""
    import time
    time.sleep(0.05 * (6 - payload["particle_id"]))
    return {"particle_id": payload["particle_id"]}


def _fake_targets(value: float = 1000.0) -> list[dict]:
    """Build a minimal target_records list covering all default stat years."""
    records = []
    for indicator, years in DEFAULT_SUMMARY_STAT_SPEC:
        for year in years:
            records.append({"model_output": indicator, "year": year, "estimate": value})
    return records


# ---------------------------------------------------------------------------
# 1. Summary statistics — shape and type
# ---------------------------------------------------------------------------


class TestSummaryStatShape:
    def test_labels_length_matches_n_summary_stats(self):
        labels = summary_stat_labels()
        assert len(labels) == N_SUMMARY_STATS

    def test_labels_length_custom_spec(self):
        spec = [("PLHIV", [2020, 2021]), ("new_infections", [2022])]
        labels = summary_stat_labels(spec)
        assert len(labels) == 3

    def test_compute_returns_correct_length(self):
        x = compute_summary_stats(_fake_outputs())
        assert len(x) == N_SUMMARY_STATS

    def test_compute_returns_float32(self):
        x = compute_summary_stats(_fake_outputs())
        assert x.dtype == np.float32

    def test_observed_returns_correct_length(self):
        x_obs = observed_summary_stats(_fake_targets())
        assert len(x_obs) == N_SUMMARY_STATS

    def test_observed_returns_float32(self):
        x_obs = observed_summary_stats(_fake_targets())
        assert x_obs.dtype == np.float32

    def test_compute_and_observed_same_shape_for_same_spec(self):
        custom_spec = [("PLHIV", [2020, 2021, 2022]), ("aids_deaths", [2015, 2023])]
        x_sim = compute_summary_stats(_fake_outputs(), spec=custom_spec)
        x_obs = observed_summary_stats(_fake_targets(), spec=custom_spec)
        assert x_sim.shape == x_obs.shape


# ---------------------------------------------------------------------------
# 2. Summary statistics — log1p transform
# ---------------------------------------------------------------------------


class TestSummaryStatTransform:
    def test_log1p_applied_to_positive_values(self):
        outputs = _fake_outputs(value=999.0)
        x = compute_summary_stats(outputs)
        expected = math.log1p(999.0)
        assert np.allclose(x, expected, atol=1e-5), (
            f"Expected all values ≈ log1p(999)={expected:.4f}, got min={x.min():.4f}"
        )

    def test_zero_input_maps_to_zero(self):
        outputs = _fake_outputs(value=0.0)
        x = compute_summary_stats(outputs)
        assert np.allclose(x, 0.0, atol=1e-6)

    def test_all_values_non_negative(self):
        x = compute_summary_stats(_fake_outputs(value=500.0))
        assert np.all(x >= 0.0), "log1p of non-negative values must be non-negative"

    def test_larger_input_gives_larger_output(self):
        x_small = compute_summary_stats(_fake_outputs(value=100.0))
        x_large = compute_summary_stats(_fake_outputs(value=10000.0))
        assert np.all(x_large > x_small)

    def test_non_msm_diagnoses_are_derived_from_total_minus_msm(self):
        spec = [("new_diagnoses_non_msm", [2024])]
        outputs = {
            "new_diagnoses": {2024: 517.0},
            "new_diagnoses_msm": {2024: 318.0},
        }
        x = compute_summary_stats(outputs, spec=spec)
        assert x[0] == pytest.approx(math.log1p(199.0))


# ---------------------------------------------------------------------------
# 3. Summary statistics — sentinel / missing value handling
# ---------------------------------------------------------------------------


class TestSummaryStatSentinel:
    def test_missing_indicator_uses_sentinel(self):
        # Remove all outputs for PLHIV — those positions should be sentinel
        outputs = _fake_outputs(value=500.0)
        del outputs["PLHIV"]
        x = compute_summary_stats(outputs)
        sentinel_log = math.log1p(COLLAPSE_SENTINEL)
        # PLHIV is the first indicator in DEFAULT_SUMMARY_STAT_SPEC
        n_plhiv_years = len(DEFAULT_SUMMARY_STAT_SPEC[0][1])
        assert np.allclose(x[:n_plhiv_years], sentinel_log, atol=1e-5)

    def test_missing_year_uses_sentinel(self):
        outputs = _fake_outputs(value=500.0)
        # Remove year 2023 from every indicator
        for series in outputs.values():
            series.pop(2023, None)
        x = compute_summary_stats(outputs)
        assert np.all(np.isfinite(x)), "All values must be finite even when years are missing"

    def test_nan_input_uses_sentinel(self):
        outputs = _fake_outputs(value=float("nan"))
        x = compute_summary_stats(outputs)
        sentinel_log = math.log1p(COLLAPSE_SENTINEL)
        assert np.allclose(x, sentinel_log, atol=1e-5)

    def test_negative_input_uses_sentinel(self):
        outputs = _fake_outputs(value=-10.0)
        x = compute_summary_stats(outputs)
        sentinel_log = math.log1p(COLLAPSE_SENTINEL)
        assert np.allclose(x, sentinel_log, atol=1e-5)

    def test_inf_input_uses_sentinel(self):
        outputs = _fake_outputs(value=float("inf"))
        x = compute_summary_stats(outputs)
        assert np.all(np.isfinite(x))

    def test_empty_outputs_all_sentinel(self):
        x = compute_summary_stats({})
        sentinel_log = math.log1p(COLLAPSE_SENTINEL)
        assert np.allclose(x, sentinel_log, atol=1e-5)


# ---------------------------------------------------------------------------
# 4. Summary statistics — observed (x_obs)
# ---------------------------------------------------------------------------


class TestObservedSummaryStats:
    def test_matches_log1p_of_target_estimate(self):
        targets = _fake_targets(value=2500.0)
        x_obs = observed_summary_stats(targets)
        expected = math.log1p(2500.0)
        assert np.allclose(x_obs, expected, atol=1e-5)

    def test_missing_target_uses_sentinel(self):
        # Only provide PLHIV targets; all others should be sentinel
        records = [
            {"model_output": "PLHIV", "year": y, "estimate": 1000.0}
            for _, years in DEFAULT_SUMMARY_STAT_SPEC[:1]
            for y in years
        ]
        x_obs = observed_summary_stats(records)
        n_plhiv = len(DEFAULT_SUMMARY_STAT_SPEC[0][1])
        sentinel_log = math.log1p(COLLAPSE_SENTINEL)
        # Non-PLHIV positions should be sentinel
        assert np.allclose(x_obs[n_plhiv:], sentinel_log, atol=1e-5)

    def test_empty_targets_all_sentinel(self):
        x_obs = observed_summary_stats([])
        assert np.all(np.isfinite(x_obs))

    def test_observed_non_msm_diagnoses_are_derived_from_targets(self):
        spec = [("new_diagnoses_non_msm", [2024])]
        targets = [
            {"model_output": "new_diagnoses", "year": 2024, "estimate": 517.0},
            {"model_output": "new_diagnoses_msm", "year": 2024, "estimate": 318.0},
        ]
        x_obs = observed_summary_stats(targets, spec=spec)
        assert x_obs[0] == pytest.approx(math.log1p(199.0))


# ---------------------------------------------------------------------------
# 5. stat_row_fields
# ---------------------------------------------------------------------------


class TestStatRowFields:
    def test_returns_dict_with_correct_prefix(self):
        x = compute_summary_stats(_fake_outputs())
        fields = stat_row_fields(x)
        for key in fields:
            assert key.startswith(STAT_COLUMN_PREFIX)

    def test_returns_correct_number_of_fields(self):
        x = compute_summary_stats(_fake_outputs())
        fields = stat_row_fields(x)
        assert len(fields) == N_SUMMARY_STATS

    def test_raises_on_length_mismatch(self):
        with pytest.raises(ValueError, match="length"):
            stat_row_fields(np.zeros(5, dtype=np.float32))

    def test_all_values_finite(self):
        x = compute_summary_stats(_fake_outputs())
        fields = stat_row_fields(x)
        assert all(math.isfinite(v) for v in fields.values())


# ---------------------------------------------------------------------------
# 6. run_snpe_calibration helpers (no model required)
# ---------------------------------------------------------------------------


class TestSnpeHelpers:
    """Tests for pure helper functions in run_snpe_calibration.py.

    These tests import from run_snpe_calibration but never run the simulator
    or the neural network, so they don't require sbi to be installed on all
    machines.  They are skipped if sbi/torch are unavailable.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping SNPE helper tests")
        pytest.importorskip("torch", reason="torch not installed; skipping SNPE helper tests")

    def test_build_prior_dimension(self):
        from run_snpe_calibration import _build_prior, _param_names
        calib_space = {
            "parameters": [
                {"name": "a", "min": 0.0, "max": 1.0},
                {"name": "b", "min": 2.0, "max": 5.0},
            ]
        }
        prior = _build_prior(calib_space)
        import torch
        sample = prior.sample((10,))
        assert sample.shape == (10, 2)

    def test_build_prior_respects_bounds(self):
        from run_snpe_calibration import _build_prior
        import torch
        calib_space = {
            "parameters": [
                {"name": "x", "min": 3.0, "max": 4.0},
            ]
        }
        prior = _build_prior(calib_space)
        samples = prior.sample((1000,))
        assert samples.min().item() >= 3.0 - 1e-5
        assert samples.max().item() <= 4.0 + 1e-5

    def test_clip_to_bounds_clips_over(self):
        from run_snpe_calibration import _clip_to_bounds
        calib_space = {
            "parameters": [
                {"name": "p", "min": 0.0, "max": 1.0},
            ]
        }
        clipped = _clip_to_bounds({"p": 1.5}, calib_space)
        assert clipped["p"] == pytest.approx(1.0)

    def test_clip_to_bounds_clips_under(self):
        from run_snpe_calibration import _clip_to_bounds
        calib_space = {
            "parameters": [
                {"name": "p", "min": 0.5, "max": 1.0},
            ]
        }
        clipped = _clip_to_bounds({"p": 0.1}, calib_space)
        assert clipped["p"] == pytest.approx(0.5)

    def test_clip_to_bounds_valid_unchanged(self):
        from run_snpe_calibration import _clip_to_bounds
        calib_space = {
            "parameters": [
                {"name": "p", "min": 0.0, "max": 1.0},
            ]
        }
        clipped = _clip_to_bounds({"p": 0.7}, calib_space)
        assert clipped["p"] == pytest.approx(0.7)

    def test_param_names_order(self):
        from run_snpe_calibration import _param_names
        calib_space = {
            "parameters": [
                {"name": "beta", "min": 0.0, "max": 1.0},
                {"name": "gamma", "min": 0.0, "max": 1.0},
            ]
        }
        assert _param_names(calib_space) == ["beta", "gamma"]

    def test_theta_from_rows(self):
        from run_snpe_calibration import _theta_from_rows
        rows = [{"a": 0.3, "b": 2.5}, {"a": 0.7, "b": 4.1}]
        theta = _theta_from_rows(rows, ["a", "b"])
        import torch
        assert theta.shape == (2, 2)
        assert theta[0, 0].item() == pytest.approx(0.3, abs=1e-5)

    def test_x_from_rows(self):
        from run_snpe_calibration import _x_from_rows
        stat_labels = summary_stat_labels()[:3]
        rows = [
            {f"x_{label}": float(i) for i, label in enumerate(stat_labels)},
            {f"x_{label}": float(i + 10) for i, label in enumerate(stat_labels)},
        ]
        x = _x_from_rows(rows, stat_labels)
        assert x.shape == (2, 3)

    def test_tensor_to_param_dicts_rejects_out_of_bounds(self):
        from run_snpe_calibration import _tensor_to_param_dicts
        import torch
        calib_space = {
            "parameters": [
                {"name": "p", "min": 0.0, "max": 1.0},
            ]
        }
        samples = torch.tensor([[1.2]], dtype=torch.float32)
        with pytest.raises(ValueError, match="outside bounds"):
            _tensor_to_param_dicts(samples, ["p"], calib_space)

    def test_sample_from_posterior_filters_outside_prior(self):
        from run_snpe_calibration import _sample_from_posterior
        import torch

        class FakePosterior:
            def __init__(self):
                self.kwargs = None

            def sample(self, shape, **kwargs):
                self.kwargs = kwargs
                return torch.tensor([[-0.4], [0.4], [1.6], [0.6]], dtype=torch.float32)

        posterior = FakePosterior()
        calib_space = {
            "parameters": [
                {"name": "p", "min": 0.0, "max": 1.0},
            ]
        }
        draws = _sample_from_posterior(
            posterior,
            torch.tensor([0.0], dtype=torch.float32),
            2,
            ["p"],
            calib_space,
        )

        assert posterior.kwargs["reject_outside_prior"] is False
        assert draws == [{"p": pytest.approx(0.4)}, {"p": pytest.approx(0.6)}]

    def test_write_best_fit_configs_marks_fallback_and_retires_inherited_abc(
        self,
        tmp_path,
    ):
        from run_snpe_calibration import write_snpe_best_fit_configs
        import json

        base_config = {
            "foo": {"value": 0.0},
            "run_control": {
                "start_year": {"value": 1980},
                "end_year": {"value": 2026},
                "deterministic_mode": {"value": True},
            },
            "output": {
                "enabled": {"value": True},
                "terminal_summary": {"value": True},
            },
            "calibration_metadata": {
                "abc_smc_calibration": {
                    "abc_distance": 99.0,
                    "overall_accept": False,
                }
            },
        }
        calib_space = {
            "parameters": [
                {
                    "name": "p",
                    "min": 0.0,
                    "max": 1.0,
                    "config_path": "foo.value",
                }
            ]
        }
        rows = [
            {
                "particle_id": 1,
                "snpe_round": 0,
                "p": 0.8,
                "abc_distance": 10.0,
                "overall_accept": False,
                "total_ll": -10.0,
                "acceptance_n_failures": 2,
                "acceptance_fail_reasons": "gate_a;gate_b",
            },
            {
                "particle_id": 2,
                "snpe_round": 0,
                "p": 0.2,
                "abc_distance": 5.0,
                "overall_accept": False,
                "total_ll": -5.0,
                "acceptance_n_failures": 1,
                "acceptance_fail_reasons": "gate_a",
            },
        ]

        paths = write_snpe_best_fit_configs(
            all_rows=rows,
            posterior_sample_rows=[],
            base_config=base_config,
            calib_space=calib_space,
            output_dir=tmp_path,
            top_n=1,
            timestamp="20260605_120000",
            metadata_stub={"git_commit": "abc123", "calib_space_path": "space.json"},
        )

        with open(paths[0], encoding="utf-8") as f:
            written = json.load(f)
        meta = written["_snpe_metadata"]
        calibration_meta = written["calibration_metadata"]

        assert written["foo"]["value"] == pytest.approx(0.2)
        assert meta["selection_source"] == "fallback_all_particles"
        assert meta["n_posterior_accepted"] == 0
        assert meta["acceptance_n_failures"] == 1
        assert "abc_smc_calibration" not in calibration_meta
        assert "source_base_abc_smc_calibration" in calibration_meta
        assert calibration_meta["snpe_calibration"] == meta

    def test_write_best_fit_configs_prefers_accepted_posterior(self, tmp_path):
        from run_snpe_calibration import write_snpe_best_fit_configs
        import json

        base_config = {
            "foo": {"value": 0.0},
            "run_control": {
                "start_year": {"value": 1980},
                "end_year": {"value": 2026},
                "deterministic_mode": {"value": True},
            },
            "output": {
                "enabled": {"value": True},
                "terminal_summary": {"value": True},
            },
        }
        calib_space = {
            "parameters": [
                {
                    "name": "p",
                    "min": 0.0,
                    "max": 1.0,
                    "config_path": "foo.value",
                }
            ]
        }
        all_rows = [
            {
                "particle_id": 1,
                "snpe_round": 0,
                "p": 0.1,
                "abc_distance": 1.0,
                "overall_accept": False,
                "total_ll": -1.0,
            }
        ]
        posterior_rows = [
            {
                "particle_id": "post_1",
                "snpe_round": "posterior",
                "p": 0.9,
                "abc_distance": 20.0,
                "overall_accept": True,
                "total_ll": -20.0,
            }
        ]

        paths = write_snpe_best_fit_configs(
            all_rows=all_rows,
            posterior_sample_rows=posterior_rows,
            base_config=base_config,
            calib_space=calib_space,
            output_dir=tmp_path,
            top_n=1,
            timestamp="20260605_120000",
            metadata_stub={},
        )

        with open(paths[0], encoding="utf-8") as f:
            written = json.load(f)
        assert written["foo"]["value"] == pytest.approx(0.9)
        assert written["_snpe_metadata"]["selection_source"] == "accepted_posterior"


# ---------------------------------------------------------------------------
# 6b. _train_snpe / _extract_training_losses (proposal-correction fix)
# ---------------------------------------------------------------------------


class _FakeInference:
    """Minimal stand-in for sbi.inference.SNPE, recording call arguments.

    Not a real sbi object — just enough surface (append_simulations, train,
    summary) to verify _train_snpe's call shape without needing a real flow.
    """

    def __init__(self):
        self.append_calls: list[dict] = []
        self.train_calls: list[dict] = []
        self.summary = {"training_loss": [0.5, 0.3], "validation_loss": [0.6, 0.4]}

    def append_simulations(self, theta, x, **kwargs):
        self.append_calls.append({"theta": theta, "x": x, "kwargs": kwargs})
        return self

    def train(self, **kwargs):
        self.train_calls.append(kwargs)
        return "fake_density_estimator"


class TestTrainSnpe:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping _train_snpe tests")
        pytest.importorskip("torch", reason="torch not installed; skipping _train_snpe tests")

    def test_round_zero_calls_append_without_proposal_kwarg(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        _train_snpe(inf, torch.zeros((2, 1)), torch.zeros((2, 1)), proposal=None, round_num=0)
        assert len(inf.append_calls) == 1
        assert "proposal" not in inf.append_calls[0]["kwargs"]

    def test_nonzero_round_with_proposal_calls_append_with_proposal_kwarg(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        fake_proposal = object()
        _train_snpe(
            inf, torch.zeros((2, 1)), torch.zeros((2, 1)),
            proposal=fake_proposal, round_num=1,
        )
        assert inf.append_calls[0]["kwargs"].get("proposal") is fake_proposal

    def test_nonzero_round_with_none_proposal_omits_proposal_kwarg(self):
        # round_num > 0 but no posterior trained yet (proposal=None) should
        # still behave like a prior round -- no proposal kwarg.
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        _train_snpe(inf, torch.zeros((2, 1)), torch.zeros((2, 1)), proposal=None, round_num=2)
        assert "proposal" not in inf.append_calls[0]["kwargs"]

    def test_default_force_first_round_loss_is_false(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        _train_snpe(inf, torch.zeros((2, 1)), torch.zeros((2, 1)), proposal=None, round_num=0)
        assert inf.train_calls[0]["force_first_round_loss"] is False

    def test_force_first_round_loss_true_when_requested(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        _train_snpe(
            inf, torch.zeros((2, 1)), torch.zeros((2, 1)),
            proposal=None, round_num=1, force_first_round_loss=True,
        )
        assert inf.train_calls[0]["force_first_round_loss"] is True

    def test_show_train_summary_defaults_true(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        _train_snpe(inf, torch.zeros((2, 1)), torch.zeros((2, 1)), proposal=None, round_num=0)
        assert inf.train_calls[0]["show_train_summary"] is True

    def test_returns_trained_density_estimator(self):
        from run_snpe_calibration import _train_snpe
        import torch

        inf = _FakeInference()
        result = _train_snpe(
            inf, torch.zeros((2, 1)), torch.zeros((2, 1)), proposal=None, round_num=0
        )
        assert result == "fake_density_estimator"


class TestExtractTrainingLosses:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping loss-extraction tests")

    def test_extracts_final_losses_from_summary_dict(self):
        from run_snpe_calibration import _extract_training_losses

        train_loss, val_loss = _extract_training_losses(_FakeInference())
        assert train_loss == pytest.approx(0.3)
        assert val_loss == pytest.approx(0.4)

    def test_missing_summary_returns_nan(self):
        from run_snpe_calibration import _extract_training_losses

        class _NoSummary:
            pass

        train_loss, val_loss = _extract_training_losses(_NoSummary())
        assert math.isnan(train_loss)
        assert math.isnan(val_loss)

    def test_malformed_summary_never_raises(self):
        from run_snpe_calibration import _extract_training_losses

        class _BadSummary:
            summary = "not a dict"

        train_loss, val_loss = _extract_training_losses(_BadSummary())
        assert math.isnan(train_loss)
        assert math.isnan(val_loss)


class TestRoundSummary:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping _round_summary tests")

    def test_loss_fields_default_to_nan_and_fallback_defaults_false(self):
        from run_snpe_calibration import _round_summary

        rows = [{"snpe_round": 0, "collapsed": False, "abc_distance": 1.0, "overall_accept": True}]
        rs = _round_summary(rows, 0, training_duration_s=1.0, simulation_duration_s=2.0)
        assert math.isnan(rs["final_train_loss"])
        assert math.isnan(rs["final_val_loss"])
        assert rs["used_fallback_loss"] is False

    def test_supplied_diagnostic_values_pass_through(self):
        from run_snpe_calibration import _round_summary

        rows = [{"snpe_round": 1, "collapsed": False, "abc_distance": 1.0, "overall_accept": True}]
        rs = _round_summary(
            rows, 1, training_duration_s=1.0, simulation_duration_s=2.0,
            final_train_loss=0.42, final_val_loss=0.55, used_fallback_loss=True,
        )
        assert rs["final_train_loss"] == pytest.approx(0.42)
        assert rs["final_val_loss"] == pytest.approx(0.55)
        assert rs["used_fallback_loss"] is True


class TestRunRoundPayloadsOrdering:
    """_run_round_payloads must restore submission order regardless of the
    order ProcessPoolExecutor.as_completed() happens to yield results in --
    row order feeds theta/x tensors directly, so an unstable order makes
    training non-reproducible even with a fixed torch seed."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping ordering tests")

    def test_single_worker_preserves_order(self, monkeypatch):
        import run_snpe_calibration as snpe_mod
        from run_snpe_calibration import _run_round_payloads

        payloads = [{"particle_id": i} for i in [3, 1, 2]]
        monkeypatch.setattr(
            snpe_mod, "_run_snpe_particle_worker",
            lambda p: {"particle_id": p["particle_id"]},
        )
        rows = _run_round_payloads(payloads, workers=1)
        assert [r["particle_id"] for r in rows] == [3, 1, 2]

    def test_multi_worker_restores_submission_order_despite_reversed_completion(
        self, monkeypatch
    ):
        import run_snpe_calibration as snpe_mod
        from run_snpe_calibration import _run_round_payloads

        payloads = [{"particle_id": i} for i in range(6)]
        monkeypatch.setattr(snpe_mod, "_run_snpe_particle_worker", _reversed_completion_worker)
        rows = _run_round_payloads(payloads, workers=3)
        assert [r["particle_id"] for r in rows] == list(range(6))

    def test_string_particle_ids_do_not_crash_and_preserve_order(self, monkeypatch):
        """The final-posterior-evaluation call site uses string ids like
        "post_10" -- these must not crash a particle_id-based sort key and
        must still come back in submission order."""
        import run_snpe_calibration as snpe_mod
        from run_snpe_calibration import _run_round_payloads

        payloads = [{"particle_id": f"post_{i}"} for i in [10, 2, 1]]
        monkeypatch.setattr(snpe_mod, "_run_snpe_particle_worker", _identity_worker)
        rows = _run_round_payloads(payloads, workers=2)
        assert [r["particle_id"] for r in rows] == ["post_10", "post_2", "post_1"]


# ---------------------------------------------------------------------------
# 7. Worker schema test (no model — exercises error-path only)
# ---------------------------------------------------------------------------


class TestWorkerSchema:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping worker schema tests")

    def test_error_path_returns_correct_schema(self):
        """Worker must return a dict with required keys even on simulation failure."""
        from run_snpe_calibration import _run_snpe_particle_worker
        payload = {
            "particle_id": 99,
            "snpe_round": 0,
            "base_config": {"this": "will_fail"},  # invalid config → triggers except block
            "calib_space": {"parameters": [{"name": "x", "min": 0.0, "max": 1.0}]},
            "params": {"x": 0.5},
            "targets": [],
            "seed": 0,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "simulation_start_year": None,
            "scoring_start_year": None,
            "summary_stat_spec": None,
        }
        row = _run_snpe_particle_worker(payload)

        # Required keys always present
        assert row["particle_id"] == 99
        assert row["snpe_round"] == 0
        assert row["collapsed"] is True
        assert isinstance(row["error"], str) and len(row["error"]) > 0
        # Stat columns filled with sentinel (not NaN/inf)
        for label in summary_stat_labels():
            val = row.get(f"x_{label}")
            assert val is not None and math.isfinite(val), (
                f"x_{label} should be finite sentinel, got {val}"
            )

    def test_error_path_overall_accept_is_false(self):
        from run_snpe_calibration import _run_snpe_particle_worker
        payload = {
            "particle_id": 1,
            "snpe_round": 0,
            "base_config": {},
            "calib_space": {"parameters": []},
            "params": {},
            "targets": [],
            "seed": 0,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "simulation_start_year": None,
            "scoring_start_year": None,
            "summary_stat_spec": None,
        }
        row = _run_snpe_particle_worker(payload)
        assert row.get("overall_accept") is False


# ---------------------------------------------------------------------------
# 8. parse_args defaults
# ---------------------------------------------------------------------------


class TestParseArgs:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping parse_args tests")

    def test_density_estimator_default_is_maf(self):
        from run_snpe_calibration import parse_args
        args = parse_args([])
        assert args.density_estimator == "maf"

    def test_density_estimator_maf_accepted(self):
        from run_snpe_calibration import parse_args
        args = parse_args(["--density-estimator", "maf"])
        assert args.density_estimator == "maf"

    def test_rounds_default(self):
        from run_snpe_calibration import parse_args
        args = parse_args([])
        assert args.rounds == 4

    def test_workers_default(self):
        from run_snpe_calibration import parse_args
        args = parse_args([])
        assert args.workers == -1

    def test_soft_aids_gate_default_false(self):
        from run_snpe_calibration import parse_args
        args = parse_args([])
        assert args.soft_aids_gate is False


class TestForceFirstRoundLossCli:
    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping parse_args tests")

    def test_default_is_false(self):
        from run_snpe_calibration import parse_args
        args = parse_args([])
        assert args.force_first_round_loss is False

    def test_flag_parses_to_true(self):
        from run_snpe_calibration import parse_args
        args = parse_args(["--force-first-round-loss"])
        assert args.force_first_round_loss is True


# ---------------------------------------------------------------------------
# 9. LHS --store-timeseries patch
# ---------------------------------------------------------------------------


class TestLhsStoreTimeseries:
    def test_flag_not_present_by_default(self):
        """Ensure --store-timeseries defaults to False → no regression."""
        from run_lhs_calibration import parse_args as lhs_parse_args
        args = lhs_parse_args([])
        assert args.store_timeseries is False

    def test_flag_can_be_enabled(self):
        from run_lhs_calibration import parse_args as lhs_parse_args
        args = lhs_parse_args(["--store-timeseries"])
        assert args.store_timeseries is True


# ---------------------------------------------------------------------------
# 10. Smoke integration test (slow — requires sbi, torch, full model)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestSnpeSmokeRun:
    """Full SNPE smoke test: 2 rounds, small bootstrap, verifies output files exist.

    Bootstrap/round-particle counts are chosen to exceed the `>= 10 usable
    particles` training gate in run_snpe_calibration() so that round 1 (whose
    proposals come from the round-0 posterior, not the prior) actually
    exercises the proposal-corrected training path this fix targets --
    at 1 round with only a handful of particles the training branch was
    silently skipped and this test would pass without ever training.

    Requires:
      - sbi installed
      - v11 model config present at configs/calib_space_v13_15_1975_orientation_seed_abc.json
      - model_final.json or equivalent config present

    Run with: pytest -m slow tests/test_snpe_calibration.py::TestSnpeSmokeRun
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_sbi(self):
        pytest.importorskip("sbi", reason="sbi not installed; skipping smoke test")
        pytest.importorskip("torch", reason="torch not installed; skipping smoke test")

    def test_smoke_run_produces_output_files(self, tmp_path):
        from run_snpe_calibration import parse_args, run_snpe_calibration

        calib_space_path = ROOT / "configs" / "calib_space.json"
        config_path = ROOT / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"
        if not calib_space_path.exists() or not config_path.exists():
            pytest.skip("Production config files not present")

        args = parse_args([
            "--config", str(config_path),
            "--calib-space", str(calib_space_path),
            "--output-dir", str(tmp_path),
            "--bootstrap", "12",
            "--rounds", "2",
            "--round-particles", "12",
            "--posterior-samples", "16",
            "--final-samples", "6",
            "--workers", "1",
            "--top-n", "2",
        ])
        run_snpe_calibration(args)

        # Verify required output files exist
        csv_files = list(tmp_path.glob("snpe_particles_*.csv"))
        assert len(csv_files) == 1, "Expected exactly one particles CSV"

        round_files = list(tmp_path.glob("snpe_round_summary_*.csv"))
        assert len(round_files) == 1

        meta_files = list(tmp_path.glob("snpe_metadata_*.json"))
        assert len(meta_files) == 1

        excel_files = list(tmp_path.glob("snpe_summary_*.xlsx"))
        assert len(excel_files) == 1

        # Verify particles CSV has required columns
        import pandas as pd
        df = pd.read_csv(csv_files[0])
        assert "particle_id" in df.columns
        assert "snpe_round" in df.columns
        assert "collapsed" in df.columns
        assert "overall_accept" in df.columns

        # Verify at least one x_ column present
        x_cols = [c for c in df.columns if c.startswith("x_PLHIV_")]
        assert len(x_cols) > 0, "No summary stat columns found in particles CSV"

        # Verify metadata JSON is valid
        import json
        with open(meta_files[0]) as f:
            meta = json.load(f)
        assert meta["density_estimator"] == "maf"
        assert meta["bootstrap"] == 12
        assert meta["rounds"] == 2
        assert meta["proposal_correction"] is True

        # Round summary should carry the new loss/fallback diagnostics.
        round_summary_df = pd.read_csv(round_files[0])
        for col in ("final_train_loss", "final_val_loss", "used_fallback_loss"):
            assert col in round_summary_df.columns

    def test_smoke_run_posterior_samples_non_empty(self, tmp_path):
        """Directly falsifies the previously-logged "zero posterior samples
        after round 0" symptom: with the proposal correction restored, a
        2-round run over the >=10-particle training gate should still be
        able to train and produce final posterior samples."""
        from run_snpe_calibration import parse_args, run_snpe_calibration

        calib_space_path = ROOT / "configs" / "calib_space.json"
        config_path = ROOT / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"
        if not calib_space_path.exists() or not config_path.exists():
            pytest.skip("Production config files not present")

        args = parse_args([
            "--config", str(config_path),
            "--calib-space", str(calib_space_path),
            "--output-dir", str(tmp_path),
            "--bootstrap", "12",
            "--rounds", "2",
            "--round-particles", "12",
            "--posterior-samples", "16",
            "--final-samples", "8",
            "--workers", "1",
            "--top-n", "2",
        ])
        run_snpe_calibration(args)

        import pandas as pd

        post_files = list(tmp_path.glob("snpe_posterior_samples_*.csv"))
        assert post_files, (
            "No posterior samples CSV written -- consistent with the "
            "'zero posterior samples after round 0' regression this fix targets."
        )
        post_df = pd.read_csv(post_files[0])
        assert len(post_df) > 0, "Posterior samples CSV is empty"


class TestTrainRoundWithFallback:
    """Regression tests for a fallback-retry bug found by adversarial review:
    when the proposal-corrected _train_snpe() call fails after its
    append_simulations() already succeeded, the retry must pass
    skip_append=True or that round's simulations get silently appended
    twice into sbi's accumulated dataset, double-weighting them in every
    subsequent round's training with no error or log line to indicate it.

    These exercise _train_round_with_fallback directly (extracted from the
    main run loop specifically so this retry logic is unit-testable without
    depending on a full simulation run reaching round >= 1 with >= 10 usable
    particles -- a precondition that turned out to be too fragile to build a
    reliable integration test on top of).
    """

    def test_success_on_first_attempt_no_retry(self, monkeypatch):
        import run_snpe_calibration as snpe_mod

        calls: list[bool] = []

        def fake_train_snpe(inference, theta, x, proposal, round_num, **kwargs):
            calls.append(kwargs.get("skip_append", False))
            return "density_estimator_stub"

        monkeypatch.setattr(snpe_mod, "_train_snpe", fake_train_snpe)

        result, used_fallback = snpe_mod._train_round_with_fallback(
            inference=object(), theta=None, x=None, proposal=None,
            round_num=1, force_first_round_loss=False,
        )
        assert result == "density_estimator_stub"
        assert used_fallback is False
        assert calls == [False]

    def test_retry_after_failure_passes_skip_append_true(self, monkeypatch):
        import run_snpe_calibration as snpe_mod

        calls: list[bool] = []

        def fake_train_snpe(inference, theta, x, proposal, round_num, **kwargs):
            calls.append(kwargs.get("skip_append", False))
            if len(calls) == 1:
                raise RuntimeError("forced failure for regression test")
            return "density_estimator_stub"

        monkeypatch.setattr(snpe_mod, "_train_snpe", fake_train_snpe)

        result, used_fallback = snpe_mod._train_round_with_fallback(
            inference=object(), theta=None, x=None, proposal=None,
            round_num=1, force_first_round_loss=False,
        )
        assert result == "density_estimator_stub"
        assert used_fallback is True
        assert calls == [False, True], (
            "Retry must pass skip_append=True to avoid double-appending "
            "this round's simulations into sbi's accumulated dataset"
        )

    def test_both_attempts_fail_returns_none_no_crash(self, monkeypatch):
        import run_snpe_calibration as snpe_mod

        def fake_train_snpe(inference, theta, x, proposal, round_num, **kwargs):
            raise RuntimeError("forced failure for regression test")

        monkeypatch.setattr(snpe_mod, "_train_snpe", fake_train_snpe)

        result, used_fallback = snpe_mod._train_round_with_fallback(
            inference=object(), theta=None, x=None, proposal=None,
            round_num=1, force_first_round_loss=False,
        )
        assert result is None
        assert used_fallback is False
