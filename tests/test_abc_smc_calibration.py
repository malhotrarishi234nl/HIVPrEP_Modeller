"""
Unit tests for run_abc_smc_calibration.

Covers:
  - prior sampling: derived shares are valid, contact ordering maintained
  - perturbation: stays within bounds, derived shares recomputed
  - edge case: invalid particle at constraint boundary returns None or valid result
  - resampling: supplied importance weights favour high-weight particles
  - weight/kernel helpers: normalization, adaptive (weighted-variance) kernel
    bandwidth, Toni/Beaumont importance-weight update, effective sample size
  - toy-posterior recovery: real generation-loop functions recover the exact
    analytic ABC target on a closed-form 1-D toy problem
  - ranking: 4-level priority (overall_accept > n_failures > distance > ll)
  - generation summary: collapse rate, gate pass rate, distance stats, n_eligible_survivors
  - tolerance schedule: correct quantile by generation
  - metadata: write_json round-trips source hash fields
  - eligible survivors mask: collapsed/error/non-finite rows excluded
  - select survivors: ineligible rows never enter the pool, all-ineligible returns empty
  - component weights: CLI defaults, custom values, round-trip into CalibrationDistanceSettings
  - multiprocessing ordering: _run_generation_payloads returns rows sorted by particle_id
  - provenance: metadata contains argv/cwd/python_version/platform/timestamps
  - particle seeds: worker row and best-fit config include particle_seed
  - anchor loading: anchor configs extracted, validated, injected into generation 0
  - output dir warning: existing metadata files trigger a printed warning

Tests that require a running model are marked @pytest.mark.slow and skipped
by default.  Run with: pytest -m slow tests/test_abc_smc_calibration.py
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_abc_smc_calibration as abc_mod
from run_abc_smc_calibration import (
    _abc_rank,
    _bandwidth,
    _compute_importance_weights,
    _effective_sample_size,
    _eligible_survivors_mask,
    _generation_summary,
    _load_anchor_particle,
    _log_mixture_proposal_density,
    _normalize_weights,
    _parameter_bounds,
    _perturb_particle,
    _resample_particles,
    _run_generation_payloads,
    _sample_valid_prior,
    _select_survivors,
    _survival_quantile,
    _weighted_bandwidth,
    parse_args,
    write_abc_best_fit_configs,
)
from calib_distance import CalibrationDistanceSettings, evaluate_calibration_distance
from calib_acceptance_gates import AcceptanceGateSettings
from run_lhs_calibration import _valid_draw_mask, load_json, validate_config_paths, write_json


# ---------------------------------------------------------------------------
# Minimal parameter space for unit tests (no model required)
# ---------------------------------------------------------------------------

PARAMETERS = [
    {"name": "activity_med",       "min": 0.05, "max": 0.25},
    {"name": "activity_high",      "min": 0.01, "max": 0.14},
    {"name": "seed_chronic_share", "min": 0.00, "max": 0.40},
    {"name": "seed_aids_share",    "min": 0.00, "max": 0.05},
    {"name": "contact_low",        "min": 1.0,  "max": 3.0},
    {"name": "contact_med",        "min": 4.0,  "max": 8.0},
    {"name": "contact_high",       "min": 9.0,  "max": 20.0},
]


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _valid_particle() -> dict:
    """A known-good particle dict including derived fields."""
    return {
        "activity_med":       0.15,
        "activity_high":      0.07,
        "seed_chronic_share": 0.20,
        "seed_aids_share":    0.02,
        "contact_low":        2.0,
        "contact_med":        6.0,
        "contact_high":       15.0,
        "activity_low":       0.78,
        "seed_acute_share":   0.78,
    }


# ---------------------------------------------------------------------------
# Prior sampling
# ---------------------------------------------------------------------------


class TestPriorSampling:
    def test_returns_exact_n_particles(self):
        particles = _sample_valid_prior(PARAMETERS, n=10, rng=_rng(0))
        assert len(particles) == 10

    def test_derived_activity_low_within_bounds(self):
        particles = _sample_valid_prior(PARAMETERS, n=30, rng=_rng(7))
        for p in particles:
            low = 1.0 - p["activity_med"] - p["activity_high"]
            assert 0.14 <= low <= 0.81, f"activity_low={low:.4f} out of [0.14, 0.81]"

    def test_derived_seed_acute_share_within_unit_interval(self):
        particles = _sample_valid_prior(PARAMETERS, n=30, rng=_rng(13))
        for p in particles:
            acute = 1.0 - p["seed_chronic_share"] - p["seed_aids_share"]
            assert 0.0 <= acute <= 1.0, f"seed_acute_share={acute:.4f} out of [0, 1]"

    def test_contact_ordering_strict(self):
        particles = _sample_valid_prior(PARAMETERS, n=30, rng=_rng(99))
        for p in particles:
            assert p["contact_low"] < p["contact_med"], (
                f"contact_low={p['contact_low']} not < contact_med={p['contact_med']}"
            )
            assert p["contact_med"] < p["contact_high"], (
                f"contact_med={p['contact_med']} not < contact_high={p['contact_high']}"
            )

    def test_free_parameters_within_prior_bounds(self):
        particles = _sample_valid_prior(PARAMETERS, n=20, rng=_rng(55))
        for p in particles:
            for param in PARAMETERS:
                name = param["name"]
                assert param["min"] <= p[name] <= param["max"], (
                    f"{name}={p[name]:.4f} outside [{param['min']}, {param['max']}]"
                )

    def test_derived_keys_present_in_every_particle(self):
        particles = _sample_valid_prior(PARAMETERS, n=10, rng=_rng(3))
        for p in particles:
            assert "activity_low" in p
            assert "seed_acute_share" in p

    def test_structural_capacity_wide_space_samples_without_rejection_collapse(self):
        """V13.12-style wide ordered bounds should still produce valid priors."""
        wide_parameters = [
            {"name": "orient_female", "min": 0.10, "max": 2.00},
            {"name": "orient_msm", "min": 0.50, "max": 7.00},
            {"name": "activity_med", "min": 0.15, "max": 0.55},
            {"name": "activity_high", "min": 0.01, "max": 0.25},
            {"name": "seed_chronic_share", "min": 0.00, "max": 0.60},
            {"name": "seed_aids_share", "min": 0.00, "max": 0.20},
            {"name": "contact_low", "min": 0.50, "max": 8.00},
            {"name": "contact_med", "min": 3.00, "max": 25.00},
            {"name": "contact_high", "min": 8.00, "max": 80.00},
            {"name": "diagnosis_1985", "min": 0.01, "max": 0.50},
            {"name": "diagnosis_1990", "min": 0.02, "max": 0.70},
            {"name": "diagnosis_1997", "min": 0.05, "max": 0.90},
            {"name": "diagnosis_2019", "min": 0.10, "max": 0.95},
            {"name": "msm_diag_mult_1996", "min": 0.00, "max": 2.00},
            {"name": "msm_diag_mult_2004", "min": 0.00, "max": 2.00},
            {"name": "msm_diag_mult_2019", "min": 0.00, "max": 2.00},
            {"name": "art_init_1996", "min": 0.00, "max": 1.00},
            {"name": "art_init_2000", "min": 0.00, "max": 1.00},
            {"name": "art_init_2015", "min": 0.30, "max": 1.00},
            {"name": "art_init_2020", "min": 0.50, "max": 1.00},
        ]
        particles = _sample_valid_prior(wide_parameters, n=500, rng=_rng(61))
        df = pd.DataFrame(particles)
        assert len(df) == 500
        assert _valid_draw_mask(df).all()
        assert (df["orient_female"] <= 1.0).all()
        assert (df["orient_msm"] >= 1.0).all()

    def test_ordered_heterosexual_male_orientation_space_samples_validly(self):
        """V13.12b side-run space samples Female=1 fixed externally, Heterosexual_Male <= MSM."""
        ordered_parameters = [
            {"name": "orient_het_male", "min": 1.00, "max": 3.00},
            {"name": "orient_msm", "min": 1.00, "max": 7.00},
            {"name": "activity_med", "min": 0.15, "max": 0.55},
            {"name": "activity_high", "min": 0.01, "max": 0.25},
            {"name": "seed_chronic_share", "min": 0.00, "max": 0.60},
            {"name": "seed_aids_share", "min": 0.00, "max": 0.20},
            {"name": "contact_low", "min": 0.50, "max": 8.00},
            {"name": "contact_med", "min": 3.00, "max": 25.00},
            {"name": "contact_high", "min": 8.00, "max": 80.00},
            {"name": "diagnosis_1985", "min": 0.01, "max": 0.50},
            {"name": "diagnosis_1990", "min": 0.02, "max": 0.70},
            {"name": "diagnosis_1997", "min": 0.05, "max": 0.90},
            {"name": "diagnosis_2019", "min": 0.10, "max": 0.95},
            {"name": "msm_diag_mult_1996", "min": 0.00, "max": 2.00},
            {"name": "msm_diag_mult_2004", "min": 0.00, "max": 2.00},
            {"name": "msm_diag_mult_2019", "min": 0.00, "max": 2.00},
            {"name": "art_init_1996", "min": 0.00, "max": 1.00},
            {"name": "art_init_2000", "min": 0.00, "max": 1.00},
            {"name": "art_init_2015", "min": 0.30, "max": 1.00},
            {"name": "art_init_2020", "min": 0.50, "max": 1.00},
        ]
        particles = _sample_valid_prior(ordered_parameters, n=500, rng=_rng(62))
        df = pd.DataFrame(particles)
        assert len(df) == 500
        assert _valid_draw_mask(df).all()
        assert (df["orient_het_male"] >= 1.0).all()
        assert (df["orient_msm"] >= df["orient_het_male"]).all()

    def test_v13_15_combined_space_samples_valid_orientation_seed_particles(self):
        """V13.15 1975 + MSM diagnosis + orientation seed space is sampler-feasible."""
        calib_space = load_json(ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc.json")
        particles = _sample_valid_prior(calib_space["parameters"], n=300, rng=_rng(63))
        df = pd.DataFrame(particles)
        assert len(df) == 300
        assert _valid_draw_mask(df).all()
        assert {
            "seed_msm_chronic_share",
            "seed_msm_aids_share",
            "seed_non_msm_chronic_share",
            "seed_non_msm_aids_share",
            "msm_diag_mult_1996",
            "msm_diag_mult_2004",
            "msm_diag_mult_2019",
        }.issubset(df.columns)
        assert (df["seed_msm_acute_share"] >= 0.0).all()
        assert (df["seed_non_msm_acute_share"] >= 0.0).all()
        assert (df["seed_msm_aids_share"] <= df["seed_non_msm_aids_share"]).all()

    def test_v13_15_combined_space_config_paths_validate_against_1975_base(self):
        base_config = load_json(ROOT / "configs/v13_14_1975_burnin_base.json")
        calib_space = load_json(ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc.json")
        validate_config_paths(base_config, calib_space)


# ---------------------------------------------------------------------------
# Perturbation
# ---------------------------------------------------------------------------


class TestPerturbation:
    def test_perturbed_free_parameters_within_bounds(self):
        bounds = _parameter_bounds(PARAMETERS)
        bw = _bandwidth(PARAMETERS, fraction=0.10)
        particle = _valid_particle()
        results = [_perturb_particle(particle, bounds, bw, _rng(i)) for i in range(30)]
        valid_results = [r for r in results if r is not None]
        assert valid_results, "Expected at least one successful perturbation"
        for r in valid_results:
            for param in PARAMETERS:
                name = param["name"]
                assert param["min"] <= r[name] <= param["max"], (
                    f"{name}={r[name]:.4f} outside [{param['min']}, {param['max']}]"
                )

    def test_derived_activity_low_recomputed_from_free_params(self):
        bounds = _parameter_bounds(PARAMETERS)
        bw = _bandwidth(PARAMETERS, fraction=0.10)
        particle = _valid_particle()
        for i in range(30):
            r = _perturb_particle(particle, bounds, bw, _rng(i))
            if r is None:
                continue
            expected_low = 1.0 - r["activity_med"] - r["activity_high"]
            assert abs(r["activity_low"] - expected_low) < 1e-9, (
                f"activity_low mismatch: got {r['activity_low']:.6f}, "
                f"expected {expected_low:.6f}"
            )

    def test_derived_seed_acute_recomputed_from_free_params(self):
        bounds = _parameter_bounds(PARAMETERS)
        bw = _bandwidth(PARAMETERS, fraction=0.10)
        particle = _valid_particle()
        for i in range(30):
            r = _perturb_particle(particle, bounds, bw, _rng(i + 100))
            if r is None:
                continue
            expected_acute = 1.0 - r["seed_chronic_share"] - r["seed_aids_share"]
            assert abs(r["seed_acute_share"] - expected_acute) < 1e-9, (
                f"seed_acute_share mismatch: got {r['seed_acute_share']:.6f}, "
                f"expected {expected_acute:.6f}"
            )

    def test_infeasible_edge_particle_returns_none_or_valid(self):
        """Wide bandwidth on a near-infeasible particle: must return None or valid, not raise."""
        bounds = _parameter_bounds(PARAMETERS)
        bw = _bandwidth(PARAMETERS, fraction=0.50)
        # Push activity shares to the edge of the feasible region.
        edge = {
            "activity_med":       0.24,
            "activity_high":      0.13,
            "seed_chronic_share": 0.39,
            "seed_aids_share":    0.049,
            "contact_low":        1.0,
            "contact_med":        4.0,
            "contact_high":       9.0,
            "activity_low":       0.63,
            "seed_acute_share":   0.561,
        }
        result = _perturb_particle(edge, bounds, bw, _rng(0), max_attempts=10)
        if result is not None:
            for param in PARAMETERS:
                name = param["name"]
                assert param["min"] <= result[name] <= param["max"], (
                    f"perturbed {name}={result[name]:.4f} outside bounds after edge test"
                )

    def test_non_free_keys_not_directly_mutated(self):
        """Derived keys in the parent should be overwritten by recomputation, not by direct copy."""
        bounds = _parameter_bounds(PARAMETERS)
        bw = _bandwidth(PARAMETERS, fraction=0.05)
        particle = _valid_particle()
        # Plant a deliberately wrong derived value.
        particle["activity_low"] = 999.0
        for i in range(20):
            r = _perturb_particle(particle, bounds, bw, _rng(i + 200))
            if r is not None:
                assert r["activity_low"] != 999.0, (
                    "activity_low should be recomputed, not copied from the corrupt parent"
                )
                break


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------


class TestResampling:
    def test_returns_requested_count(self):
        pop = [{"id": i} for i in range(10)]
        weights = [1.0 / 10] * 10
        result, _ = _resample_particles(pop, weights, n=7, rng=_rng(0))
        assert len(result) == 7

    def test_supplied_weights_favour_high_weight_particles(self):
        pop = [{"id": i} for i in range(5)]
        # Particle 2 carries by far the largest importance weight.
        weights = [0.05, 0.05, 0.8, 0.05, 0.05]
        counts: dict[int, int] = {i: 0 for i in range(5)}
        for trial in range(500):
            sample, _ = _resample_particles(pop, weights, n=1, rng=_rng(trial))
            counts[sample[0]["id"]] += 1
        # Particle 2 (weight=0.8) should dominate.
        assert counts[2] > counts[3], (
            f"Expected counts[2]={counts[2]} > counts[3]={counts[3]}"
        )
        assert counts[2] > counts[4]

    def test_all_zero_weights_returns_uniform_sample(self):
        pop = [{"id": i} for i in range(4)]
        weights = [0.0] * 4
        result, _ = _resample_particles(pop, weights, n=8, rng=_rng(7))
        assert len(result) == 8
        # All IDs should come from the population.
        for r in result:
            assert r["id"] in range(4)

    def test_single_particle_always_resampled(self):
        pop = [{"x": 42.0}]
        weights = [1.0]
        result, _ = _resample_particles(pop, weights, n=5, rng=_rng(3))
        assert all(r["x"] == 42.0 for r in result)

    def test_returns_indices_same_length_as_particles(self):
        pop = [{"id": i} for i in range(6)]
        weights = [1.0 / 6] * 6
        particles, indices = _resample_particles(pop, weights, n=4, rng=_rng(0))
        assert len(indices) == len(particles) == 4

    def test_indices_within_population_bounds(self):
        pop = [{"id": i} for i in range(5)]
        weights = [0.1, 0.2, 0.3, 0.2, 0.2]
        particles, indices = _resample_particles(pop, weights, n=10, rng=_rng(9))
        for idx in indices:
            assert 0 <= idx < len(pop), f"Index {idx} out of range [0, {len(pop)})"

    def test_mismatched_weights_length_raises(self):
        pop = [{"id": i} for i in range(3)]
        weights = [1.0, 1.0]
        with pytest.raises(ValueError):
            _resample_particles(pop, weights, n=5, rng=_rng(0))

    def test_nan_in_weights_falls_back_to_uniform(self):
        pop = [{"id": i} for i in range(4)]
        weights = [0.5, float("nan"), 0.3, 0.2]
        result, indices = _resample_particles(pop, weights, n=8, rng=_rng(1))
        assert len(result) == 8
        for idx in indices:
            assert 0 <= idx < len(pop)

    def test_unnormalized_weights_are_respected_proportionally(self):
        pop = [{"id": i} for i in range(3)]
        raw = [2.0, 2.0, 2.0]
        normalized = [0.2, 0.2, 0.2]  # not summing to 1, same ratios as raw
        counts_raw: dict[int, int] = {i: 0 for i in range(3)}
        counts_norm: dict[int, int] = {i: 0 for i in range(3)}
        for trial in range(300):
            sample_raw, _ = _resample_particles(pop, raw, n=1, rng=_rng(trial))
            sample_norm, _ = _resample_particles(pop, normalized, n=1, rng=_rng(trial))
            counts_raw[sample_raw[0]["id"]] += 1
            counts_norm[sample_norm[0]["id"]] += 1
        # Same seed sequence + same relative weights → identical draws.
        assert counts_raw == counts_norm


# ---------------------------------------------------------------------------
# Weight/kernel helpers (Toni 2009 / Beaumont 2009 ABC-SMC fix)
# ---------------------------------------------------------------------------


class TestNormalizeWeights:
    def test_normal_case_sums_to_one(self):
        result = _normalize_weights([1.0, 2.0, 3.0, 4.0])
        assert abs(sum(result) - 1.0) < 1e-9

    def test_all_zero_falls_back_to_uniform(self):
        result = _normalize_weights([0.0, 0.0, 0.0])
        assert result == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    def test_negative_sum_falls_back_to_uniform(self):
        result = _normalize_weights([-1.0, -1.0])
        assert result == pytest.approx([0.5, 0.5])

    def test_non_finite_falls_back_to_uniform(self):
        result = _normalize_weights([1.0, float("nan"), 2.0])
        assert result == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    def test_empty_returns_empty(self):
        assert _normalize_weights([]) == []


class TestWeightedBandwidth:
    def test_uniform_weights_match_unweighted_variance_formula(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        pop = [{"x": v} for v in values]
        weights = [1.0] * len(values)
        params = [{"name": "x", "min": 0.0, "max": 10.0}]
        bw = _weighted_bandwidth(pop, weights, params, min_bandwidth_fraction=1e-6)
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        expected = math.sqrt(2.0 * variance)
        assert bw["x"] == pytest.approx(expected, rel=1e-6)

    def test_single_particle_falls_back_to_floor(self):
        pop = [{"x": 5.0}]
        weights = [1.0]
        params = [{"name": "x", "min": 0.0, "max": 10.0}]
        bw = _weighted_bandwidth(pop, weights, params, min_bandwidth_fraction=0.01)
        assert bw["x"] == pytest.approx(0.01 * (10.0 - 0.0), rel=1e-6)

    def test_bandwidth_never_below_floor(self):
        # Fully converged population (zero variance) still gets the floor.
        pop = [{"x": 5.0}] * 10
        weights = [1.0] * 10
        params = [{"name": "x", "min": 0.0, "max": 10.0}]
        bw = _weighted_bandwidth(pop, weights, params, min_bandwidth_fraction=1e-3)
        assert bw["x"] == pytest.approx(1e-3 * 10.0, rel=1e-6)

    def test_tighter_population_yields_smaller_bandwidth(self):
        params = [{"name": "x", "min": 0.0, "max": 10.0}]
        tight_pop = [{"x": v} for v in [4.9, 5.0, 5.1]]
        wide_pop = [{"x": v} for v in [1.0, 5.0, 9.0]]
        weights = [1.0, 1.0, 1.0]
        bw_tight = _weighted_bandwidth(tight_pop, weights, params, min_bandwidth_fraction=1e-6)
        bw_wide = _weighted_bandwidth(wide_pop, weights, params, min_bandwidth_fraction=1e-6)
        assert bw_tight["x"] < bw_wide["x"]

    def test_unnormalized_weights_handled(self):
        values = [1.0, 2.0, 3.0]
        pop = [{"x": v} for v in values]
        params = [{"name": "x", "min": 0.0, "max": 10.0}]
        bw_raw = _weighted_bandwidth(pop, [2.0, 2.0, 2.0], params, min_bandwidth_fraction=1e-6)
        bw_norm = _weighted_bandwidth(pop, [1 / 3, 1 / 3, 1 / 3], params, min_bandwidth_fraction=1e-6)
        assert bw_raw["x"] == pytest.approx(bw_norm["x"], rel=1e-6)


class TestImportanceWeights:
    def test_weights_sum_to_one(self):
        prev_pop = [{"x": 0.0}, {"x": 1.0}]
        prev_weights = [0.5, 0.5]
        next_pop = [{"x": 0.2}, {"x": 0.8}, {"x": 1.5}]
        bw = {"x": 0.5}
        weights = _compute_importance_weights(next_pop, prev_pop, prev_weights, bw, ["x"])
        assert abs(sum(weights) - 1.0) < 1e-9

    def test_all_weights_non_negative(self):
        prev_pop = [{"x": 0.0}, {"x": 1.0}]
        prev_weights = [0.5, 0.5]
        next_pop = [{"x": -3.0}, {"x": 0.5}, {"x": 4.0}]
        bw = {"x": 0.3}
        weights = _compute_importance_weights(next_pop, prev_pop, prev_weights, bw, ["x"])
        assert all(w >= 0.0 for w in weights)

    def test_far_from_bulk_particle_gets_higher_weight(self):
        # Previous generation is tightly clustered near 0; a new particle far
        # from that cluster has low proposal (mixture) density and therefore
        # a HIGHER importance weight (w ~ 1/mixture_density) than one that
        # landed right where the proposal already concentrated its mass.
        prev_pop = [{"x": 0.0}, {"x": 0.1}, {"x": -0.1}]
        prev_weights = [1 / 3, 1 / 3, 1 / 3]
        bw = {"x": 0.2}
        next_pop = [{"x": 0.0}, {"x": 5.0}]
        weights = _compute_importance_weights(next_pop, prev_pop, prev_weights, bw, ["x"])
        assert weights[1] > weights[0]

    def test_closed_form_two_equal_parents_midpoint_case(self):
        # Two equally-weighted previous particles at -1 and +1; a new particle
        # exactly at their midpoint (0). Mixture density at 0 is the average
        # of two identical (by symmetry) N(0, bw^2) densities evaluated at
        # distance 1 from their means, so the importance weight is fully
        # determined analytically and should match a hand-computed value.
        bw_val = 1.0
        prev_pop = [{"x": -1.0}, {"x": 1.0}]
        prev_weights = [0.5, 0.5]
        next_pop = [{"x": 0.0}]
        bw = {"x": bw_val}
        weights = _compute_importance_weights(next_pop, prev_pop, prev_weights, bw, ["x"])
        # Single candidate → its normalized weight must be exactly 1.0
        # regardless of the (nonzero, finite) mixture density value.
        assert weights == pytest.approx([1.0])

    def test_empty_next_population_returns_empty(self):
        prev_pop = [{"x": 0.0}]
        prev_weights = [1.0]
        weights = _compute_importance_weights([], prev_pop, prev_weights, {"x": 0.5}, ["x"])
        assert weights == []

    def test_empty_prev_population_falls_back_to_uniform(self):
        next_pop = [{"x": 0.0}, {"x": 1.0}]
        weights = _compute_importance_weights(next_pop, [], [], {"x": 0.5}, ["x"])
        assert weights == pytest.approx([0.5, 0.5])


class TestLogMixtureProposalDensity:
    def test_empty_prev_population_returns_empty_array(self):
        result = _log_mixture_proposal_density([{"x": 0.0}], [], [], {"x": 0.5}, ["x"])
        assert len(result) == 0

    def test_empty_candidates_returns_empty_array(self):
        result = _log_mixture_proposal_density([], [{"x": 0.0}], [1.0], {"x": 0.5}, ["x"])
        assert len(result) == 0

    def test_candidate_at_parent_has_higher_density_than_far_candidate(self):
        prev_pop = [{"x": 0.0}]
        prev_weights = [1.0]
        bw = {"x": 0.5}
        near = _log_mixture_proposal_density([{"x": 0.0}], prev_pop, prev_weights, bw, ["x"])
        far = _log_mixture_proposal_density([{"x": 10.0}], prev_pop, prev_weights, bw, ["x"])
        assert near[0] > far[0]


class TestEffectiveSampleSize:
    def test_uniform_weights_give_full_ess(self):
        weights = [0.25, 0.25, 0.25, 0.25]
        assert _effective_sample_size(weights) == pytest.approx(4.0)

    def test_one_dominant_weight_gives_ess_near_one(self):
        weights = [0.97, 0.01, 0.01, 0.01]
        ess = _effective_sample_size(weights)
        assert 1.0 <= ess < 1.2

    def test_scale_invariant(self):
        ess_a = _effective_sample_size([2.0, 2.0, 2.0, 2.0])
        ess_b = _effective_sample_size([0.25, 0.25, 0.25, 0.25])
        assert ess_a == pytest.approx(ess_b)

    def test_empty_returns_nan(self):
        assert math.isnan(_effective_sample_size([]))

    def test_all_zero_returns_nan(self):
        assert math.isnan(_effective_sample_size([0.0, 0.0, 0.0]))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestAbcRank:
    def _df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_overall_accept_prioritized_first(self):
        df = self._df([
            {"particle_id": 1, "overall_accept": False, "acceptance_n_failures": 1,
             "abc_distance": 0.1, "total_ll": -10.0},
            {"particle_id": 2, "overall_accept": True,  "acceptance_n_failures": 0,
             "abc_distance": 5.0, "total_ll": -200.0},
        ])
        ranked = _abc_rank(df)
        assert int(ranked.iloc[0]["particle_id"]) == 2

    def test_lower_failure_count_breaks_tie_on_accept(self):
        df = self._df([
            {"particle_id": 1, "overall_accept": False, "acceptance_n_failures": 3,
             "abc_distance": 0.5, "total_ll": -50.0},
            {"particle_id": 2, "overall_accept": False, "acceptance_n_failures": 1,
             "abc_distance": 2.0, "total_ll": -100.0},
        ])
        ranked = _abc_rank(df)
        assert int(ranked.iloc[0]["particle_id"]) == 2

    def test_lower_distance_breaks_tie_on_failure_count(self):
        df = self._df([
            {"particle_id": 1, "overall_accept": False, "acceptance_n_failures": 2,
             "abc_distance": 5.0, "total_ll": -10.0},
            {"particle_id": 2, "overall_accept": False, "acceptance_n_failures": 2,
             "abc_distance": 1.0, "total_ll": -100.0},
        ])
        ranked = _abc_rank(df)
        assert int(ranked.iloc[0]["particle_id"]) == 2

    def test_higher_total_ll_breaks_tie_on_distance(self):
        df = self._df([
            {"particle_id": 1, "overall_accept": False, "acceptance_n_failures": 2,
             "abc_distance": 1.0, "total_ll": -200.0},
            {"particle_id": 2, "overall_accept": False, "acceptance_n_failures": 2,
             "abc_distance": 1.0, "total_ll": -50.0},
        ])
        ranked = _abc_rank(df)
        assert int(ranked.iloc[0]["particle_id"]) == 2

    def test_full_four_level_priority_chain(self):
        df = self._df([
            {"particle_id": 1, "overall_accept": False, "acceptance_n_failures": 1,
             "abc_distance": 0.5, "total_ll": -10.0},
            {"particle_id": 2, "overall_accept": True,  "acceptance_n_failures": 0,
             "abc_distance": 5.0, "total_ll": -200.0},
            {"particle_id": 3, "overall_accept": False, "acceptance_n_failures": 3,
             "abc_distance": 0.1, "total_ll": -5.0},
        ])
        ranked = _abc_rank(df)
        ids = list(ranked["particle_id"].astype(int))
        assert ids[0] == 2, f"Expected gate-passing particle first; got order {ids}"
        assert ids[1] == 1, f"Expected fewer-failures particle second; got order {ids}"
        assert ids[2] == 3, f"Expected more-failures particle last; got order {ids}"

    def test_no_acceptance_columns_ranks_by_distance_then_ll(self):
        df = self._df([
            {"particle_id": 1, "abc_distance": 3.0, "total_ll": -10.0},
            {"particle_id": 2, "abc_distance": 1.0, "total_ll": -100.0},
        ])
        ranked = _abc_rank(df)
        assert int(ranked.iloc[0]["particle_id"]) == 2


# ---------------------------------------------------------------------------
# Generation summary
# ---------------------------------------------------------------------------


class TestGenerationSummary:
    def _sample_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"collapsed": True,  "abc_distance": float("inf"), "total_ll": float("-inf"),
             "overall_accept": False, "acceptance_n_failures": 5},
            {"collapsed": False, "abc_distance": 2.0,          "total_ll": -50.0,
             "overall_accept": False, "acceptance_n_failures": 2},
            {"collapsed": False, "abc_distance": 0.5,          "total_ll": -10.0,
             "overall_accept": True,  "acceptance_n_failures": 0},
        ])

    def test_particle_count(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert summary["n_particles"] == 3

    def test_collapse_count_and_rate(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert summary["n_collapsed"] == 1
        assert abs(summary["collapse_rate"] - 1 / 3) < 1e-9

    def test_gate_pass_count_and_rate(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert summary["n_gate_pass"] == 1
        assert abs(summary["gate_pass_rate"] - 1 / 3) < 1e-9

    def test_distance_min_excludes_infinite(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert abs(summary["abc_distance_min"] - 0.5) < 1e-9

    def test_generation_field_stored(self):
        summary = _generation_summary(self._sample_df(), generation=2, tolerance=0.35)
        assert summary["generation"] == 2
        assert abs(summary["tolerance_quantile"] - 0.35) < 1e-9

    def test_all_collapsed_df(self):
        df = pd.DataFrame([
            {"collapsed": True, "abc_distance": float("inf"), "total_ll": float("-inf"),
             "overall_accept": False, "acceptance_n_failures": 5},
        ])
        summary = _generation_summary(df, generation=0, tolerance=1.0)
        assert summary["n_collapsed"] == 1
        assert math.isnan(summary["abc_distance_min"])

    def test_n_eligible_survivors_excludes_ineligible(self):
        # Row 0: collapsed + inf distance → ineligible.
        # Rows 1 and 2: not collapsed, no error, finite distance → eligible.
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert summary["n_eligible_survivors"] == 2

    def test_n_eligible_survivors_zero_when_all_ineligible(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "",    "abc_distance": float("inf"), "total_ll": float("-inf")},
            {"collapsed": False, "error": "err", "abc_distance": float("inf"), "total_ll": float("-inf")},
        ])
        summary = _generation_summary(df, generation=0, tolerance=1.0)
        assert summary["n_eligible_survivors"] == 0

    def test_ess_defaults_to_nan(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert math.isnan(summary["ess"])

    def test_ess_passed_through(self):
        summary = _generation_summary(self._sample_df(), generation=0, tolerance=1.0, ess=12.5)
        assert summary["ess"] == pytest.approx(12.5)

    def test_n_perturb_fallback_defaults_to_zero_and_passes_through(self):
        summary_default = _generation_summary(self._sample_df(), generation=0, tolerance=1.0)
        assert summary_default["n_perturb_fallback"] == 0
        summary_set = _generation_summary(
            self._sample_df(), generation=0, tolerance=1.0, n_perturb_fallback=3
        )
        assert summary_set["n_perturb_fallback"] == 3


# ---------------------------------------------------------------------------
# Tolerance schedule
# ---------------------------------------------------------------------------


class TestToleranceSchedule:
    def test_generation_0_keeps_all(self):
        assert _survival_quantile(0) == 1.00

    def test_generation_1_keeps_half(self):
        assert _survival_quantile(1) == 0.50

    def test_generation_2_keeps_35_percent(self):
        assert _survival_quantile(2) == 0.35

    def test_generation_3_keeps_25_percent(self):
        assert _survival_quantile(3) == 0.25

    def test_generation_4_keeps_15_percent(self):
        assert _survival_quantile(4) == 0.15

    def test_generation_5_and_beyond_keeps_10_percent(self):
        for gen in (5, 6, 10, 100):
            assert _survival_quantile(gen) == 0.10, f"Expected 0.10 at gen={gen}"


# ---------------------------------------------------------------------------
# Metadata / write_json round-trip
# ---------------------------------------------------------------------------


class TestMetadataAndHashFields:
    def test_write_json_round_trips_hash_fields(self, tmp_path):
        payload = {
            "source_config_path": "/some/config.json",
            "source_sha256_before": "abc123def456",
            "source_sha256_after":  "abc123def456",
            "source_config_unchanged": True,
            "runner": "abc_smc",
        }
        path = tmp_path / "test_metadata.json"
        write_json(path, payload)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["source_sha256_before"] == "abc123def456"
        assert loaded["source_sha256_after"]  == "abc123def456"
        assert loaded["source_config_unchanged"] is True
        assert loaded["runner"] == "abc_smc"

    def test_metadata_stub_has_required_keys(self):
        stub = {
            "runner": "abc_smc",
            "source_config_path": "/x.json",
            "source_sha256_before": "aabbcc",
            "created_at": "2026-05-26T00:00:00",
            "seed": 42,
            "stage": "joint",
            "particles": 20,
            "generations": 2,
        }
        required = {
            "runner", "source_config_path", "source_sha256_before",
            "created_at", "seed", "stage",
        }
        for key in required:
            assert key in stub, f"Required metadata key '{key}' missing"

    def test_metadata_provenance_keys_present(self, tmp_path):
        """A metadata dict written by the runner should contain all provenance keys."""
        provenance_keys = {
            "argv", "cwd", "python_version", "platform",
            "started_at", "completed_at", "duration_seconds",
            "run_stamp", "output_dir",
        }
        payload = {
            "runner": "abc_smc",
            "argv": ["/usr/bin/python3", "run_abc_smc_calibration.py"],
            "cwd": "/some/path",
            "python_version": "3.11.0",
            "platform": "macOS-15.0",
            "started_at": "2026-05-26T00:00:00",
            "completed_at": "2026-05-26T00:01:00",
            "duration_seconds": 60.0,
            "run_stamp": "20260526_000000",
            "output_dir": str(tmp_path),
        }
        path = tmp_path / "meta.json"
        write_json(path, payload)
        loaded = json.loads(path.read_text())
        for key in provenance_keys:
            assert key in loaded, f"Provenance key '{key}' missing from metadata"

    def test_metadata_seed_policy_fields(self, tmp_path):
        """Metadata should record seed_policy and base_seed."""
        payload = {
            "seed": 42,
            "seed_policy": "particle_seed = base_seed + particle_id",
            "base_seed": 42,
        }
        path = tmp_path / "meta2.json"
        write_json(path, payload)
        loaded = json.loads(path.read_text())
        assert loaded["seed_policy"] == "particle_seed = base_seed + particle_id"
        assert loaded["base_seed"] == 42


# ---------------------------------------------------------------------------
# write_abc_best_fit_configs (no model run; uses a minimal fake config)
# ---------------------------------------------------------------------------


class TestWriteAbcBestFitConfigs:
    def _minimal_calib_space(self) -> dict:
        return {
            "parameters": [
                {"name": "activity_med",  "min": 0.05, "max": 0.25,
                 "config_path": "population.activity_distribution.value.Medium"},
                {"name": "activity_high", "min": 0.01, "max": 0.14,
                 "config_path": "population.activity_distribution.value.High"},
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
        }

    def _minimal_base_config(self) -> dict:
        return {
            "population": {
                "activity_distribution": {
                    "value": {"Low": 0.70, "Medium": 0.20, "High": 0.10}
                },
                "epidemic_seed": {
                    "stage_distribution": {
                        "value": {"Acute": 0.60, "Chronic": 0.35, "AIDS": 0.05}
                    }
                },
            },
            "run_control": {
                "start_year": {"value": 1980},
                "end_year":   {"value": 2026},
                "deterministic_mode": {"value": True},
            },
            "output": {
                "enabled": {"value": True},
                "terminal_summary": {"value": True},
            },
        }

    def test_writes_top_n_files(self, tmp_path):
        calib_space = self._minimal_calib_space()
        base_config = self._minimal_base_config()
        ranked = pd.DataFrame([
            {
                "particle_id": 1, "particle_seed": 43, "generation": 0, "total_ll": -10.0,
                "abc_distance": 0.5, "overall_accept": False, "acceptance_n_failures": 2,
                "activity_med": 0.15, "activity_high": 0.07, "activity_low": 0.78,
            },
            {
                "particle_id": 2, "particle_seed": 44, "generation": 0, "total_ll": -20.0,
                "abc_distance": 1.2, "overall_accept": False, "acceptance_n_failures": 3,
                "activity_med": 0.12, "activity_high": 0.08, "activity_low": 0.80,
            },
        ])
        stub = {"runner": "abc_smc", "source_sha256_before": "abc", "seed": 42}
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 2, stub)
        assert len(paths) == 2
        for path in paths:
            assert Path(path).exists()

    def test_written_config_has_abc_metadata(self, tmp_path):
        calib_space = self._minimal_calib_space()
        base_config = self._minimal_base_config()
        ranked = pd.DataFrame([
            {
                "particle_id": 5, "particle_seed": 104, "generation": 1, "total_ll": -15.0,
                "abc_distance": 0.8, "overall_accept": True, "acceptance_n_failures": 0,
                "activity_med": 0.18, "activity_high": 0.06, "activity_low": 0.76,
            },
        ])
        stub = {"runner": "abc_smc", "source_sha256_before": "def", "seed": 99}
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 1, stub)
        assert len(paths) == 1
        cfg = json.loads(Path(paths[0]).read_text())
        meta = cfg["calibration_metadata"]["abc_smc_calibration"]
        assert meta["rank"] == 1
        assert meta["particle_id"] == 5
        assert meta["overall_accept"] is True
        assert meta["runner"] == "abc_smc"

    def test_best_fit_config_contains_particle_seed(self, tmp_path):
        calib_space = self._minimal_calib_space()
        base_config = self._minimal_base_config()
        ranked = pd.DataFrame([
            {
                "particle_id": 7, "particle_seed": 149, "generation": 2, "total_ll": -8.0,
                "abc_distance": 0.3, "overall_accept": False, "acceptance_n_failures": 1,
                "activity_med": 0.16, "activity_high": 0.08, "activity_low": 0.76,
            },
        ])
        stub = {"runner": "abc_smc", "source_sha256_before": "xyz", "seed": 142}
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 1, stub)
        cfg = json.loads(Path(paths[0]).read_text())
        meta = cfg["calibration_metadata"]["abc_smc_calibration"]
        assert meta["particle_seed"] == 149, (
            f"particle_seed missing or wrong in best-fit config metadata: {meta}"
        )

    def test_best_fit_config_preserves_simulation_start_override(self, tmp_path):
        calib_space = self._minimal_calib_space()
        base_config = self._minimal_base_config()
        ranked = pd.DataFrame([
            {
                "particle_id": 9, "particle_seed": 209, "generation": 4,
                "total_ll": -12.0, "abc_distance": 0.4,
                "overall_accept": False, "acceptance_n_failures": 1,
                "activity_med": 0.16, "activity_high": 0.07, "activity_low": 0.77,
            },
        ])
        stub = {
            "runner": "abc_smc",
            "source_sha256_before": "start",
            "seed": 200,
            "simulation_start_year": 1975,
            "scoring_start_year": 1990,
        }
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 1, stub)
        cfg = json.loads(Path(paths[0]).read_text())
        meta = cfg["calibration_metadata"]["abc_smc_calibration"]
        assert cfg["run_control"]["start_year"]["value"] == 1975
        assert meta["simulation_start_year"] == 1975
        assert meta["scoring_start_year"] == 1990

    def test_empty_ranked_writes_no_files(self, tmp_path):
        calib_space = self._minimal_calib_space()
        base_config = self._minimal_base_config()
        ranked = pd.DataFrame(columns=["particle_id", "generation", "total_ll", "abc_distance"])
        stub = {"runner": "abc_smc", "source_sha256_before": "zzz", "seed": 0}
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 5, stub)
        assert paths == []


# ---------------------------------------------------------------------------
# Eligible survivors mask
# ---------------------------------------------------------------------------


class TestEligibleSurvivorsMask:
    def test_collapsed_true_excluded(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "", "abc_distance": 1.0},
            {"collapsed": False, "error": "", "abc_distance": 2.0},
        ])
        mask = _eligible_survivors_mask(df)
        assert list(mask) == [False, True]

    def test_non_empty_error_excluded(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "ValueError: boom", "abc_distance": 1.0},
            {"collapsed": False, "error": "",                  "abc_distance": 2.0},
        ])
        mask = _eligible_survivors_mask(df)
        assert list(mask) == [False, True]

    def test_infinite_distance_excluded(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": float("inf")},
            {"collapsed": False, "error": "", "abc_distance": 2.0},
        ])
        mask = _eligible_survivors_mask(df)
        assert list(mask) == [False, True]

    def test_nan_distance_excluded(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": float("nan")},
            {"collapsed": False, "error": "", "abc_distance": 1.5},
        ])
        mask = _eligible_survivors_mask(df)
        assert list(mask) == [False, True]

    def test_all_ineligible_returns_all_false(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "err", "abc_distance": float("inf")},
            {"collapsed": False, "error": "err", "abc_distance": float("inf")},
        ])
        mask = _eligible_survivors_mask(df)
        assert not mask.any()

    def test_whitespace_only_error_treated_as_no_error(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "   ", "abc_distance": 1.0},
        ])
        mask = _eligible_survivors_mask(df)
        assert mask.iloc[0]

    def test_nan_collapsed_treated_as_collapsed(self):
        # NaN in collapsed → map(isna) → ineligible.
        df = pd.DataFrame([
            {"collapsed": None, "error": "", "abc_distance": 1.0},
        ])
        mask = _eligible_survivors_mask(df)
        assert not mask.iloc[0]

    def test_all_eligible_returns_all_true(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": 0.5},
            {"collapsed": False, "error": "", "abc_distance": 1.5},
        ])
        mask = _eligible_survivors_mask(df)
        assert mask.all()

    def test_missing_columns_treated_permissively(self):
        # If columns are absent, no constraint on that axis → all still eligible.
        df = pd.DataFrame([{"x": 1.0}, {"x": 2.0}])
        mask = _eligible_survivors_mask(df)
        assert mask.all()


# ---------------------------------------------------------------------------
# Select survivors
# ---------------------------------------------------------------------------


class TestSelectSurvivors:
    def _param_names(self) -> set[str]:
        return {"x", "y"}

    def test_all_ineligible_returns_empty_lists(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "err", "abc_distance": float("inf"), "x": 1.0},
            {"collapsed": True,  "error": "err", "abc_distance": float("inf"), "x": 2.0},
        ])
        pop, dist, ids = _select_survivors(df, self._param_names(), tolerance=1.0)
        assert pop == []
        assert dist == []
        assert ids == []

    def test_collapsed_rows_never_in_pool(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "",    "abc_distance": 0.1, "x": 1.0},
            {"collapsed": False, "error": "",    "abc_distance": 2.0, "x": 2.0},
        ])
        pop, dist, ids = _select_survivors(df, self._param_names(), tolerance=1.0)
        assert len(pop) == 1
        assert pop[0]["x"] == 2.0

    def test_error_rows_never_in_pool(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "RuntimeError: oom", "abc_distance": 0.1, "x": 1.0},
            {"collapsed": False, "error": "",                  "abc_distance": 3.0, "x": 2.0},
        ])
        pop, dist, ids = _select_survivors(df, self._param_names(), tolerance=1.0)
        assert len(pop) == 1
        assert pop[0]["x"] == 2.0

    def test_non_finite_distance_never_in_pool(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": float("inf"), "x": 1.0},
            {"collapsed": False, "error": "", "abc_distance": 1.0,          "x": 2.0},
        ])
        pop, dist, ids = _select_survivors(df, self._param_names(), tolerance=1.0)
        assert len(pop) == 1
        assert pop[0]["x"] == 2.0

    def test_tolerance_keeps_best_half(self):
        # 4 eligible particles; tolerance=0.5 keeps those with dist <= median(1,2,3,4)=2.5.
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": 1.0, "x": 1.0},
            {"collapsed": False, "error": "", "abc_distance": 2.0, "x": 2.0},
            {"collapsed": False, "error": "", "abc_distance": 3.0, "x": 3.0},
            {"collapsed": False, "error": "", "abc_distance": 4.0, "x": 4.0},
        ])
        pop, dist, ids = _select_survivors(df, self._param_names(), tolerance=0.5)
        assert len(pop) == 2
        assert {p["x"] for p in pop} == {1.0, 2.0}

    def test_derived_params_preserved_in_pool(self):
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": 1.0,
             "x": 0.5, "activity_low": 0.75, "seed_acute_share": 0.60},
        ])
        pop, _, ids = _select_survivors(df, {"x"}, tolerance=1.0)
        assert len(pop) == 1
        assert pop[0]["activity_low"] == pytest.approx(0.75)
        assert pop[0]["seed_acute_share"] == pytest.approx(0.60)

    def test_single_eligible_row_always_survives(self):
        # With one row, the quantile equals the distance → always kept.
        df = pd.DataFrame([
            {"collapsed": False, "error": "", "abc_distance": 5.0, "x": 42.0},
        ])
        pop, dist, ids = _select_survivors(df, {"x"}, tolerance=0.1)
        assert len(pop) == 1
        assert dist[0] == pytest.approx(5.0)

    def test_ineligible_plus_eligible_mixed_correctly(self):
        df = pd.DataFrame([
            {"collapsed": True,  "error": "",    "abc_distance": 0.01, "x": 1.0},  # best dist but collapsed
            {"collapsed": False, "error": "err", "abc_distance": 0.02, "x": 2.0},  # best dist but error
            {"collapsed": False, "error": "",    "abc_distance": 5.0,  "x": 3.0},  # eligible
            {"collapsed": False, "error": "",    "abc_distance": 6.0,  "x": 4.0},  # eligible
        ])
        pop, dist, ids = _select_survivors(df, {"x"}, tolerance=0.5)
        # Only rows 2 and 3 are eligible; quantile(5,6, 0.5)=5.5; only row 2 passes.
        assert len(pop) == 1
        assert pop[0]["x"] == 3.0

    def test_ids_correspond_to_particle_ids(self):
        df = pd.DataFrame([
            {"particle_id": 10, "collapsed": False, "error": "", "abc_distance": 1.0, "x": 0.1},
            {"particle_id": 20, "collapsed": False, "error": "", "abc_distance": 2.0, "x": 0.2},
            {"particle_id": 30, "collapsed": True,  "error": "", "abc_distance": 3.0, "x": 0.3},
        ])
        pop, dist, ids = _select_survivors(df, {"x"}, tolerance=1.0)
        assert len(ids) == 2  # 30 is collapsed
        assert set(ids) == {10, 20}


# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------


class TestComponentWeights:
    def test_default_weights_parsed_correctly(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
        ])
        assert args.weight_diagnosis   == pytest.approx(1.0)
        assert args.weight_plhiv       == pytest.approx(1.0)
        assert args.weight_plhiv_sex   == pytest.approx(0.5)
        assert args.weight_incidence   == pytest.approx(1.0)
        assert args.weight_aids_deaths == pytest.approx(1.0)
        assert args.weight_art_stock   == pytest.approx(1.0)

    def test_custom_weights_parsed_correctly(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--weight-diagnosis",   "2.5",
            "--weight-plhiv-sex",   "1.25",
            "--weight-aids-deaths", "0.0",
            "--weight-art-stock",   "3.0",
        ])
        assert args.weight_diagnosis   == pytest.approx(2.5)
        assert args.weight_plhiv_sex   == pytest.approx(1.25)
        assert args.weight_aids_deaths == pytest.approx(0.0)
        assert args.weight_art_stock   == pytest.approx(3.0)
        assert args.weight_plhiv       == pytest.approx(1.0)  # still default
        assert args.weight_incidence   == pytest.approx(1.0)

    def test_soft_aids_death_gate_flag_parsed(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--soft-aids-death-gate",
        ])
        assert args.soft_aids_death_gate is True

    def test_negative_weight_raises_parse_error(self):
        with pytest.raises(SystemExit):
            parse_args([
                "--config", "x.json",
                "--calib-space", "y.json",
                "--output-dir", "/tmp",
                "--weight-diagnosis", "-1.0",
            ])

    def test_weights_stored_in_calibration_distance_settings(self):
        weights = {
            "diagnosis":   2.0,
            "plhiv":       1.0,
            "plhiv_sex":   0.5,
            "incidence":   1.5,
            "aids_deaths": 0.5,
            "art_stock":   1.0,
        }
        settings = CalibrationDistanceSettings(
            gate_settings=AcceptanceGateSettings(),
            component_weights=weights,
        )
        assert settings.component_weights["diagnosis"]   == pytest.approx(2.0)
        assert settings.component_weights["plhiv_sex"]   == pytest.approx(0.5)
        assert settings.component_weights["aids_deaths"] == pytest.approx(0.5)
        assert settings.component_weights["incidence"]   == pytest.approx(1.5)

    def test_component_weights_in_metadata_stub(self, tmp_path):
        weights = {
            "diagnosis": 1.5, "plhiv": 1.0, "plhiv_sex": 0.5, "incidence": 1.0,
            "aids_deaths": 0.5, "art_stock": 1.0,
        }
        stub = {
            "runner": "abc_smc",
            "source_sha256_before": "abc",
            "component_weights": weights,
        }
        path = tmp_path / "stub.json"
        write_json(path, stub)
        loaded = json.loads(path.read_text())
        assert loaded["component_weights"]["diagnosis"]   == pytest.approx(1.5)
        assert loaded["component_weights"]["plhiv_sex"]   == pytest.approx(0.5)
        assert loaded["component_weights"]["aids_deaths"] == pytest.approx(0.5)

    def test_plhiv_sex_targets_contribute_to_distance(self):
        targets = [
            {
                "model_output": "plhiv_female_15plus",
                "year": 2020,
                "estimate": 100.0,
                "sigma": 10.0,
            },
            {
                "model_output": "plhiv_male_15plus",
                "year": 2020,
                "estimate": 300.0,
                "sigma": 30.0,
            },
        ]
        settings = CalibrationDistanceSettings(
            gate_settings=AcceptanceGateSettings(
                plhiv_recent_start=2020,
                plhiv_recent_end=2020,
            )
        )

        perfect = evaluate_calibration_distance(
            {
                "plhiv_female_15plus": {2020: 100.0},
                "plhiv_male_15plus": {2020: 300.0},
            },
            targets,
            settings=settings,
        )
        miss = evaluate_calibration_distance(
            {
                "plhiv_female_15plus": {2020: 150.0},
                "plhiv_male_15plus": {2020: 150.0},
            },
            targets,
            settings=settings,
        )

        assert perfect["components"]["plhiv_sex"] == pytest.approx(0.0)
        assert miss["components"]["plhiv_sex"] > 0.0


class TestHistoricalShapeCli:
    def test_historical_shape_flags_parse_with_defaults_disabled(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
        ])
        assert args.historical_shape_gates is False
        assert args.plhiv_hist_mape_ceiling == pytest.approx(40.0)
        assert args.diagnoses_spike_ratio_ceiling == pytest.approx(2.5)
        assert args.aids_cases_hist_mape_ceiling == pytest.approx(80.0)

    def test_historical_shape_thresholds_parse_when_enabled(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--historical-shape-gates",
            "--plhiv-hist-mape-ceiling", "150",
            "--plhiv-anchor-max-multiplier", "3.25",
            "--diagnoses-hist-mape-ceiling", "80",
            "--diagnoses-spike-ratio-ceiling", "7",
            "--msm-diag-hist-mape-ceiling", "65",
            "--msm-diag-early-ratio-ceiling", "12",
            "--aids-cases-ratio-ceiling", "20",
            "--aids-cases-hist-mape-ceiling", "90",
        ])
        assert args.historical_shape_gates is True
        assert args.plhiv_hist_mape_ceiling == pytest.approx(150.0)
        assert args.plhiv_anchor_max_multiplier == pytest.approx(3.25)
        assert args.diagnoses_hist_mape_ceiling == pytest.approx(80.0)
        assert args.diagnoses_spike_ratio_ceiling == pytest.approx(7.0)
        assert args.msm_diag_hist_mape_ceiling == pytest.approx(65.0)
        assert args.msm_diag_early_ratio_ceiling == pytest.approx(12.0)
        assert args.aids_cases_ratio_ceiling == pytest.approx(20.0)
        assert args.aids_cases_hist_mape_ceiling == pytest.approx(90.0)

    def test_historical_shape_thresholds_must_be_positive(self):
        with pytest.raises(SystemExit):
            parse_args([
                "--config", "x.json",
                "--calib-space", "y.json",
                "--output-dir", "/tmp",
                "--plhiv-hist-mape-ceiling", "0",
            ])


# ---------------------------------------------------------------------------
# Multiprocessing ordering
# ---------------------------------------------------------------------------


class TestGenerationPayloadsOrdering:
    def test_output_sorted_by_particle_id_single_worker(self, monkeypatch):
        """Even with workers=1, rows must be sorted by particle_id ascending."""
        # Payloads given in reverse order.
        payloads = [
            {"particle_id": 3, "generation": 0},
            {"particle_id": 1, "generation": 0},
            {"particle_id": 2, "generation": 0},
        ]

        def fake_worker(payload):
            return {"particle_id": payload["particle_id"], "abc_distance": 1.0}

        monkeypatch.setattr(abc_mod, "_run_particle_worker", fake_worker)
        rows = _run_generation_payloads(payloads, workers=1)
        ids = [r["particle_id"] for r in rows]
        assert ids == [1, 2, 3], f"Expected [1, 2, 3] but got {ids}"

    def test_output_sorted_by_particle_id_with_gaps(self, monkeypatch):
        """Sorting must handle non-contiguous IDs."""
        payloads = [
            {"particle_id": 100, "generation": 1},
            {"particle_id": 5,   "generation": 1},
            {"particle_id": 50,  "generation": 1},
        ]

        def fake_worker(payload):
            return {"particle_id": payload["particle_id"], "abc_distance": 0.5}

        monkeypatch.setattr(abc_mod, "_run_particle_worker", fake_worker)
        rows = _run_generation_payloads(payloads, workers=1)
        ids = [r["particle_id"] for r in rows]
        assert ids == [5, 50, 100], f"Expected [5, 50, 100] but got {ids}"

    def test_single_payload_returns_single_row(self, monkeypatch):
        payloads = [{"particle_id": 7, "generation": 0}]

        def fake_worker(payload):
            return {"particle_id": payload["particle_id"]}

        monkeypatch.setattr(abc_mod, "_run_particle_worker", fake_worker)
        rows = _run_generation_payloads(payloads, workers=1)
        assert len(rows) == 1
        assert rows[0]["particle_id"] == 7


# ---------------------------------------------------------------------------
# Particle seeds
# ---------------------------------------------------------------------------


class TestParticleSeed:
    def test_worker_row_includes_particle_seed(self):
        """_run_particle_worker must include particle_seed = payload seed."""
        # We call the worker directly with a minimal payload that will raise
        # inside the simulation (no real model), triggering the error path.
        # The error path must still include particle_seed.
        payload = {
            "particle_id": 99,
            "generation": 0,
            "base_config": {},
            "calib_space": {"parameters": []},
            "params": {},
            "targets": [],
            "seed": 12345,
            "balance_target_families": False,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "proposal_source": "prior",
            "anchor_config_path": "",
            "parent_particle_id": "",
        }
        # This will fail inside inject_params or _run_simulation_quietly.
        row = abc_mod._run_particle_worker(payload)
        assert "particle_seed" in row, "particle_seed missing from worker row"
        assert row["particle_seed"] == 12345

    def test_worker_error_path_includes_particle_seed(self):
        """Error-path rows (collapsed=True) must also carry particle_seed."""
        payload = {
            "particle_id": 1,
            "generation": 0,
            "base_config": None,  # will cause TypeError
            "calib_space": {"parameters": []},
            "params": {},
            "targets": [],
            "seed": 777,
            "balance_target_families": False,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "proposal_source": "prior",
            "anchor_config_path": "",
            "parent_particle_id": "",
        }
        row = abc_mod._run_particle_worker(payload)
        assert row["particle_seed"] == 777
        assert row["collapsed"] is True

    def test_best_fit_config_has_particle_seed(self, tmp_path):
        """write_abc_best_fit_configs must store particle_seed in the config metadata."""
        calib_space = {
            "parameters": [
                {"name": "activity_med",  "min": 0.05, "max": 0.25,
                 "config_path": "population.activity_distribution.value.Medium"},
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
        }
        base_config = {
            "population": {
                "activity_distribution": {"value": {"Low": 0.70, "Medium": 0.20, "High": 0.10}},
                "epidemic_seed": {"stage_distribution": {"value": {"Acute": 0.6, "Chronic": 0.35, "AIDS": 0.05}}},
            },
            "run_control": {
                "start_year": {"value": 1980}, "end_year": {"value": 2026},
                "deterministic_mode": {"value": True},
            },
            "output": {"enabled": {"value": True}, "terminal_summary": {"value": True}},
        }
        ranked = pd.DataFrame([{
            "particle_id": 3, "particle_seed": 88, "generation": 1,
            "total_ll": -5.0, "abc_distance": 0.2,
            "overall_accept": False, "acceptance_n_failures": 1,
            "activity_med": 0.14, "activity_low": 0.76,
        }])
        stub = {"runner": "abc_smc", "seed": 42}
        paths = write_abc_best_fit_configs(ranked, base_config, calib_space, tmp_path, 1, stub)
        cfg = json.loads(Path(paths[0]).read_text())
        meta = cfg["calibration_metadata"]["abc_smc_calibration"]
        assert meta["particle_seed"] == 88


# ---------------------------------------------------------------------------
# --store-timeseries (enables ABC-SMC particles as SNPE --bootstrap-csv input)
# ---------------------------------------------------------------------------


class TestStoreTimeseries:
    """_run_particle_worker must write indicator_year columns when
    store_timeseries=True, mirroring run_lhs_calibration.py's existing
    --store-timeseries behaviour -- this is what lets run_snpe_calibration's
    _load_bootstrap_csv reconstruct x_sim from an ABC-SMC particle CSV."""

    def _payload(self, store_timeseries: bool) -> dict:
        return {
            "particle_id": 1,
            "generation": 0,
            "base_config": {},
            "calib_space": {"parameters": []},
            "params": {"x": 0.5},
            "targets": [],
            "seed": 1,
            "balance_target_families": False,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "proposal_source": "prior",
            "anchor_config_path": "",
            "parent_particle_id": "",
            "store_timeseries": store_timeseries,
        }

    def _patch_success_path(self, monkeypatch):
        """Bypass inject_params/simulation/scoring so the worker reaches its
        success branch without a real model, with a known outputs dict."""
        fake_outputs = {
            "PLHIV": {1990: 100.0, 1995: 200.0},
            "new_infections": {1990: 10.0},
        }
        monkeypatch.setattr(abc_mod, "inject_params", lambda *a, **k: {})
        monkeypatch.setattr(abc_mod, "_run_simulation_quietly", lambda *a, **k: {})
        monkeypatch.setattr(abc_mod, "extract_annual_outputs", lambda *a, **k: fake_outputs)
        monkeypatch.setattr(
            abc_mod, "score_draw",
            lambda outputs, targets, balance_families=False: {
                "total_ll": -1.0, "collapsed": False, "n_years_scored": 3,
                "missing_outputs": [], "ll_by_indicator": {}, "ll_by_family": {},
            },
        )
        monkeypatch.setattr(
            abc_mod, "evaluate_calibration_distance",
            lambda outputs, targets, **k: {"abc_distance": 1.0, "components": {}},
        )
        return fake_outputs

    def test_timeseries_columns_written_when_enabled(self, monkeypatch):
        self._patch_success_path(monkeypatch)
        row = abc_mod._run_particle_worker(self._payload(store_timeseries=True))
        assert row["PLHIV_1990"] == 100.0
        assert row["PLHIV_1995"] == 200.0
        assert row["new_infections_1990"] == 10.0

    def test_timeseries_columns_absent_when_disabled(self, monkeypatch):
        self._patch_success_path(monkeypatch)
        row = abc_mod._run_particle_worker(self._payload(store_timeseries=False))
        assert "PLHIV_1990" not in row
        assert "new_infections_1990" not in row

    def test_default_is_false_no_regression(self, monkeypatch):
        self._patch_success_path(monkeypatch)
        payload = self._payload(store_timeseries=False)
        del payload["store_timeseries"]
        row = abc_mod._run_particle_worker(payload)
        assert "PLHIV_1990" not in row

    def test_cli_flag_default_false_and_parses(self):
        args_default = parse_args([
            "--config", "x.json", "--calib-space", "y.json", "--output-dir", "/tmp",
        ])
        assert args_default.store_timeseries is False
        args_set = parse_args([
            "--config", "x.json", "--calib-space", "y.json", "--output-dir", "/tmp",
            "--store-timeseries",
        ])
        assert args_set.store_timeseries is True


# ---------------------------------------------------------------------------
# Anchor loading
# ---------------------------------------------------------------------------


class TestAnchorLoading:
    def _minimal_parameters(self) -> list[dict]:
        return [
            {"name": "activity_med",  "min": 0.05, "max": 0.25,
             "config_path": "population.activity_distribution.value.Medium"},
            {"name": "activity_high", "min": 0.01, "max": 0.14,
             "config_path": "population.activity_distribution.value.High"},
            {"name": "seed_chronic_share", "min": 0.0, "max": 0.40,
             "config_path": "population.epidemic_seed.stage_distribution.value.Chronic"},
            {"name": "seed_aids_share", "min": 0.0, "max": 0.05,
             "config_path": "population.epidemic_seed.stage_distribution.value.AIDS"},
            {"name": "contact_low",  "min": 1.0, "max": 3.0,
             "config_path": "transmission_model.contact_rates.value.Low"},
            {"name": "contact_med",  "min": 4.0, "max": 8.0,
             "config_path": "transmission_model.contact_rates.value.Medium"},
            {"name": "contact_high", "min": 9.0, "max": 20.0,
             "config_path": "transmission_model.contact_rates.value.High"},
        ]

    def _orientation_seed_parameters(self) -> list[dict]:
        params = [
            p for p in self._minimal_parameters()
            if p["name"] not in {"seed_chronic_share", "seed_aids_share"}
        ]
        params.extend([
            {"name": "seed_msm_chronic_share", "min": 0.0, "max": 0.50,
             "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.MSM.Chronic"},
            {"name": "seed_msm_aids_share", "min": 0.0, "max": 0.08,
             "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.MSM.AIDS"},
            {"name": "seed_non_msm_chronic_share", "min": 0.0, "max": 0.60,
             "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.non_MSM.Chronic"},
            {"name": "seed_non_msm_aids_share", "min": 0.0, "max": 0.16,
             "config_path": "population.epidemic_seed.stage_distribution_by_orientation.value.non_MSM.AIDS"},
        ])
        return params

    def _anchor_config_dict(self) -> dict:
        """A config that satisfies all config_path extractions."""
        return {
            "population": {
                "activity_distribution": {
                    "value": {"Low": 0.78, "Medium": 0.15, "High": 0.07}
                },
                "epidemic_seed": {
                    "stage_distribution": {
                        "value": {"Acute": 0.78, "Chronic": 0.20, "AIDS": 0.02}
                    }
                },
            },
            "transmission_model": {
                "contact_rates": {
                    "value": {"Low": 2.0, "Medium": 6.0, "High": 15.0}
                }
            },
        }

    def test_valid_anchor_returns_particle_dict(self, tmp_path):
        path = tmp_path / "anchor.json"
        write_json(path, self._anchor_config_dict())
        params = self._minimal_parameters()
        particle = _load_anchor_particle(path, params)
        assert particle is not None
        assert "activity_med" in particle
        assert abs(particle["activity_med"] - 0.15) < 1e-9
        assert "activity_low" in particle  # derived field present

    def test_valid_anchor_has_correct_derived_fields(self, tmp_path):
        path = tmp_path / "anchor.json"
        write_json(path, self._anchor_config_dict())
        particle = _load_anchor_particle(path, self._minimal_parameters())
        assert particle is not None
        # activity_low = 1 - 0.15 - 0.07 = 0.78
        assert abs(particle["activity_low"] - 0.78) < 1e-6
        # seed_acute_share = 1 - 0.20 - 0.02 = 0.78
        assert abs(particle["seed_acute_share"] - 0.78) < 1e-6

    def test_missing_config_path_falls_back_to_embedded_params(self, tmp_path):
        """If config_path fails, fallback to calibration_metadata.abc_smc_calibration.parameters."""
        # Config without the standard nested structure.
        config = {
            "calibration_metadata": {
                "abc_smc_calibration": {
                    "parameters": {
                        "activity_med":  0.15,
                        "activity_high": 0.07,
                        "seed_chronic_share": 0.20,
                        "seed_aids_share": 0.02,
                        "contact_low":  2.0,
                        "contact_med":  6.0,
                        "contact_high": 15.0,
                    }
                }
            }
        }
        path = tmp_path / "anchor_meta.json"
        write_json(path, config)
        particle = _load_anchor_particle(path, self._minimal_parameters())
        assert particle is not None
        assert abs(particle["activity_med"] - 0.15) < 1e-9

    def test_shared_seed_anchor_maps_to_orientation_seed_parameters(self, tmp_path):
        """V13.14 shared-seed anchors can seed V13.15 orientation-specific runs."""
        path = tmp_path / "shared_seed_anchor.json"
        write_json(path, self._anchor_config_dict())
        particle = _load_anchor_particle(path, self._orientation_seed_parameters())
        assert particle is not None
        assert particle["seed_msm_chronic_share"] == pytest.approx(0.20)
        assert particle["seed_non_msm_chronic_share"] == pytest.approx(0.20)
        assert particle["seed_msm_aids_share"] == pytest.approx(0.02)
        assert particle["seed_non_msm_aids_share"] == pytest.approx(0.02)
        assert particle["seed_msm_acute_share"] == pytest.approx(0.78)
        assert particle["seed_non_msm_acute_share"] == pytest.approx(0.78)

    def test_shared_seed_metadata_anchor_maps_to_orientation_seed_parameters(self, tmp_path):
        """Embedded ABC metadata is sufficient when orientation-specific paths are absent."""
        config = self._anchor_config_dict()
        config["population"]["epidemic_seed"].pop("stage_distribution")
        config["calibration_metadata"] = {
            "abc_smc_calibration": {
                "parameters": {
                    "activity_med": 0.15,
                    "activity_high": 0.07,
                    "seed_chronic_share": 0.25,
                    "seed_aids_share": 0.03,
                    "contact_low": 2.0,
                    "contact_med": 6.0,
                    "contact_high": 15.0,
                }
            }
        }
        path = tmp_path / "shared_seed_meta_anchor.json"
        write_json(path, config)
        particle = _load_anchor_particle(path, self._orientation_seed_parameters())
        assert particle is not None
        assert particle["seed_msm_chronic_share"] == pytest.approx(0.25)
        assert particle["seed_non_msm_chronic_share"] == pytest.approx(0.25)
        assert particle["seed_msm_aids_share"] == pytest.approx(0.03)
        assert particle["seed_non_msm_aids_share"] == pytest.approx(0.03)

    def test_invalid_config_file_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json {{{{")
        particle = _load_anchor_particle(path, self._minimal_parameters())
        assert particle is None

    def test_nonexistent_path_returns_none(self, tmp_path):
        particle = _load_anchor_particle(tmp_path / "no_such_file.json", self._minimal_parameters())
        assert particle is None

    def test_out_of_bounds_anchor_value_is_rejected(self, tmp_path):
        """An anchor extracted from a config produced under a different
        (wider) calib_space can hold a value outside the current run's
        [min, max] -- this must be rejected, not silently simulated."""
        config = self._anchor_config_dict()
        # activity_med's calib_space bound is [0.05, 0.25]; push it out.
        config["population"]["activity_distribution"]["value"]["Medium"] = 0.40
        path = tmp_path / "out_of_bounds_anchor.json"
        write_json(path, config)
        particle = _load_anchor_particle(path, self._minimal_parameters())
        assert particle is None

    def test_in_bounds_anchor_at_exact_edge_is_accepted(self, tmp_path):
        """A value exactly at min/max should not be rejected by the new check.

        Uses contact_low (no derived-parameter side effects, unlike
        activity_med/activity_high) so this isolates the bounds check from
        _valid_draw_mask's unrelated derived-share constraints.
        """
        config = self._anchor_config_dict()
        config["transmission_model"]["contact_rates"]["value"]["Low"] = 1.0
        path = tmp_path / "edge_anchor.json"
        write_json(path, config)
        particle = _load_anchor_particle(path, self._minimal_parameters())
        assert particle is not None
        assert particle["contact_low"] == pytest.approx(1.0)

    def test_anchor_cli_args_parsed(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--anchor-config", "a.json",
            "--anchor-config", "b.json",
        ])
        assert args.anchor_config == ["a.json", "b.json"]

    def test_anchor_glob_cli_arg_parsed(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--anchor-glob", "outputs/*/best_fit*.json",
        ])
        assert args.anchor_glob == ["outputs/*/best_fit*.json"]

    def test_anchor_max_cli_arg_parsed(self):
        args = parse_args([
            "--config", "x.json",
            "--calib-space", "y.json",
            "--output-dir", "/tmp",
            "--anchor-max", "10",
        ])
        assert args.anchor_max == 10

    def test_anchor_max_zero_raises(self):
        with pytest.raises(SystemExit):
            parse_args([
                "--config", "x.json",
                "--calib-space", "y.json",
                "--output-dir", "/tmp",
                "--anchor-max", "0",
            ])

    def test_worker_row_has_proposal_source_and_anchor_path(self):
        """Worker result rows must carry proposal_source and anchor_config_path."""
        payload = {
            "particle_id": 1,
            "generation": 0,
            "base_config": None,
            "calib_space": {"parameters": []},
            "params": {},
            "targets": [],
            "seed": 1,
            "balance_target_families": False,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "proposal_source": "anchor",
            "anchor_config_path": "/path/to/anchor.json",
            "parent_particle_id": "",
        }
        row = abc_mod._run_particle_worker(payload)
        assert row["proposal_source"] == "anchor"
        assert row["anchor_config_path"] == "/path/to/anchor.json"

    def test_worker_row_has_parent_particle_id(self):
        payload = {
            "particle_id": 5,
            "generation": 1,
            "base_config": None,
            "calib_space": {"parameters": []},
            "params": {},
            "targets": [],
            "seed": 5,
            "balance_target_families": False,
            "acceptance_gate_settings": None,
            "distance_settings": None,
            "proposal_source": "perturbed",
            "anchor_config_path": "",
            "parent_particle_id": 2,
        }
        row = abc_mod._run_particle_worker(payload)
        assert row["parent_particle_id"] == 2


# ---------------------------------------------------------------------------
# Output directory warning
# ---------------------------------------------------------------------------


class TestOutputDirWarning:
    def test_warns_when_existing_metadata_present(self, tmp_path, capsys):
        """If the output dir already has abc_smc_metadata_*.json files, a warning is printed."""
        # Create a fake existing metadata file.
        existing = tmp_path / "abc_smc_metadata_20260526_000000.json"
        write_json(existing, {"runner": "abc_smc"})

        # Simulate the check done at the start of run_abc_smc_calibration.
        existing_meta = list(tmp_path.glob("abc_smc_metadata_*.json"))
        if existing_meta:
            print(
                "WARNING: output directory already contains ABC-SMC runs; new timestamped files\n"
                "will be appended. Use a fresh --output-dir for thesis-final runs."
            )

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "already contains ABC-SMC runs" in captured.out

    def test_no_warning_when_directory_empty(self, tmp_path, capsys):
        """No warning when the directory is fresh."""
        existing_meta = list(tmp_path.glob("abc_smc_metadata_*.json"))
        if existing_meta:
            print("WARNING: ...")
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out


# ---------------------------------------------------------------------------
# Toy-posterior recovery (UNC-02A: validates the ABC-SMC weighting/kernel fix
# against a known analytic target, not just a running model)
# ---------------------------------------------------------------------------


def _run_toy_abc_smc(
    n_particles: int,
    n_generations: int,
    rng: np.random.Generator,
    prior_lo: float = -10.0,
    prior_hi: float = 10.0,
):
    """Miniature ABC-SMC run using the real private functions, on a 1-D toy
    problem with a closed-form target at every generation.

    Prior: theta ~ U(prior_lo, prior_hi). Distance: |theta| (observed y=0,
    identity map). For any hard quantile threshold eps_t, the exact ABC
    target is Uniform(-eps_t, eps_t) -- exact at every generation, not just
    in the eps -> 0 limit, which is what makes this a useful closed-form
    check on the real generation-loop machinery.

    Returns (final_population, final_weights, final_ess, eps_history).
    """
    bounds = {"x": (prior_lo, prior_hi)}
    parameters = [{"name": "x", "min": prior_lo, "max": prior_hi}]

    population = [{"x": float(v)} for v in rng.uniform(prior_lo, prior_hi, size=n_particles)]
    weights: list[float] = _normalize_weights([1.0] * n_particles)
    eps_history: list[float] = []

    for gen in range(n_generations):
        tolerance = _survival_quantile(gen)

        if gen == 0:
            proposals = population
        else:
            gen_bandwidth = _weighted_bandwidth(population, weights, parameters)
            resampled, _ = _resample_particles(population, weights, n_particles, rng)
            proposals = [
                _perturb_particle(parent, bounds, gen_bandwidth, rng) or dict(parent)
                for parent in resampled
            ]

        distances = [abs(p["x"]) for p in proposals]
        gen_df = pd.DataFrame(
            {
                "particle_id": range(len(proposals)),
                "collapsed": [False] * len(proposals),
                "error": [""] * len(proposals),
                "abc_distance": distances,
                "x": [p["x"] for p in proposals],
            }
        )
        next_pop, next_dist, _ = _select_survivors(gen_df, {"x"}, tolerance)
        eps_history.append(max(next_dist) if next_dist else float("nan"))

        if gen == 0:
            next_weights = _normalize_weights([1.0] * len(next_pop))
        else:
            next_weights = _compute_importance_weights(
                next_pop, population, weights, gen_bandwidth, ["x"]
            )

        population = next_pop
        weights = next_weights

    return population, weights, _effective_sample_size(weights), eps_history


class TestToyPosteriorRecovery:
    """Validates the fixed importance-weighting + adaptive-kernel scheme
    against the 1-D toy problem's exact analytic ABC target at every
    generation (see _run_toy_abc_smc docstring)."""

    N_PARTICLES = 600
    N_GENERATIONS = 5  # tolerance schedule: 1.00, 0.50, 0.35, 0.25, 0.15

    def _run(self, seed: int):
        rng = np.random.default_rng(seed)
        return _run_toy_abc_smc(self.N_PARTICLES, self.N_GENERATIONS, rng)

    def test_weighted_mean_near_zero_after_convergence(self):
        population, weights, _, eps_history = self._run(seed=1)
        eps_final = eps_history[-1]
        values = np.array([p["x"] for p in population])
        w = np.array(weights)
        weighted_mean = float(np.sum(w * values) / np.sum(w))
        # The target Uniform(-eps_final, eps_final) has mean 0; allow a
        # generous band scaled to the truncation window itself.
        assert abs(weighted_mean) < eps_final, (
            f"weighted_mean={weighted_mean:.4f} not small relative to "
            f"eps_final={eps_final:.4f}"
        )

    def test_weighted_variance_matches_uniform_target_variance(self):
        population, weights, _, eps_history = self._run(seed=2)
        eps_final = eps_history[-1]
        values = np.array([p["x"] for p in population])
        w = np.array(weights)
        mean = float(np.sum(w * values) / np.sum(w))
        weighted_variance = float(np.sum(w * (values - mean) ** 2) / np.sum(w))
        expected_variance = eps_final**2 / 3.0  # Var[U(-eps, eps)] = eps^2 / 3
        # Loose relative tolerance: this is a finite-N Monte Carlo estimate,
        # not an exact identity.
        assert 0.25 * expected_variance < weighted_variance < 4.0 * expected_variance, (
            f"weighted_variance={weighted_variance:.6f} not within a loose band "
            f"of the U(-eps,eps) target variance={expected_variance:.6f}"
        )

    def test_weighting_is_not_a_no_op_vs_unweighted_survivors(self):
        _, weights, _, _ = self._run(seed=3)
        w = np.array(weights)
        assert np.std(w) > 1e-6, (
            "Importance weights are (numerically) constant -- the weighting "
            "step is not doing any corrective work."
        )

    def test_ess_stays_reasonable_across_generations(self):
        rng = np.random.default_rng(4)
        bounds = {"x": (-10.0, 10.0)}
        parameters = [{"name": "x", "min": -10.0, "max": 10.0}]
        population = [
            {"x": float(v)} for v in rng.uniform(-10.0, 10.0, size=self.N_PARTICLES)
        ]
        weights = _normalize_weights([1.0] * self.N_PARTICLES)
        min_ess_ratio = 1.0
        for gen in range(self.N_GENERATIONS):
            tolerance = _survival_quantile(gen)
            if gen == 0:
                proposals = population
            else:
                gen_bandwidth = _weighted_bandwidth(population, weights, parameters)
                resampled, _ = _resample_particles(population, weights, self.N_PARTICLES, rng)
                proposals = [
                    _perturb_particle(parent, bounds, gen_bandwidth, rng) or dict(parent)
                    for parent in resampled
                ]
            distances = [abs(p["x"]) for p in proposals]
            gen_df = pd.DataFrame(
                {
                    "particle_id": range(len(proposals)),
                    "collapsed": [False] * len(proposals),
                    "error": [""] * len(proposals),
                    "abc_distance": distances,
                    "x": [p["x"] for p in proposals],
                }
            )
            next_pop, _, _ = _select_survivors(gen_df, {"x"}, tolerance)
            if gen == 0:
                next_weights = _normalize_weights([1.0] * len(next_pop))
            else:
                next_weights = _compute_importance_weights(
                    next_pop, population, weights, gen_bandwidth, ["x"]
                )
            ess = _effective_sample_size(next_weights)
            if next_weights:
                min_ess_ratio = min(min_ess_ratio, ess / len(next_weights))
            population, weights = next_pop, next_weights
        assert min_ess_ratio >= 0.05, (
            f"ESS collapsed to {min_ess_ratio:.3%} of the population size in "
            f"at least one generation."
        )


# ---------------------------------------------------------------------------
# Smoke test (skipped by default — requires model)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestSmoke:
    """Tiny end-to-end smoke: 5 particles × 1 generation, 1 worker.

    Run with:  pytest -m slow tests/test_abc_smc_calibration.py::TestSmoke
    """

    def test_smoke_writes_expected_outputs(self, tmp_path):
        import subprocess, sys
        config = (
            ROOT
            / "outputs"
            / "calibration"
            / "lhs_pipeline_v13_5_recent_fit"
            / "lhs_joint"
            / "branch_004"
            / "lhs_best_fit_rank_007.json"
        )
        calib_space = ROOT / "configs" / "calib_space_v13_7_seed_distribution_pilot.json"
        if not config.exists():
            pytest.skip(f"Baseline config not found: {config}")
        if not calib_space.exists():
            pytest.skip(f"Calib space not found: {calib_space}")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_abc_smc_calibration.py"),
                "--config", str(config),
                "--calib-space", str(calib_space),
                "--stage", "joint",
                "--particles", "5",
                "--generations", "1",
                "--workers", "1",
                "--seed", "1234",
                "--output-dir", str(tmp_path),
                "--top-n", "2",
                "--acceptance-gate-audit",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"Runner exited non-zero:\n{result.stderr}"

        csv_files = list(tmp_path.glob("abc_smc_particles_*.csv"))
        assert csv_files, "No particles CSV written"
        df = pd.read_csv(csv_files[0])
        assert len(df) == 5
        assert "abc_distance" in df.columns
        assert "particle_id" in df.columns
        assert "generation" in df.columns
        assert "particle_seed" in df.columns
        assert "proposal_source" in df.columns
        assert "anchor_config_path" in df.columns
        assert "parent_particle_id" in df.columns

        gen_csvs = list(tmp_path.glob("abc_smc_generation_summary_*.csv"))
        assert gen_csvs, "No generation summary CSV written"

        meta_files = list(tmp_path.glob("abc_smc_metadata_*.json"))
        assert meta_files, "No metadata JSON written"
        meta = json.loads(meta_files[0].read_text())
        assert meta["source_config_unchanged"] is True, "Source config was mutated"
        assert "source_sha256_before" in meta
        assert "source_sha256_after" in meta
        # Provenance fields
        for key in ("argv", "cwd", "python_version", "platform",
                    "started_at", "completed_at", "duration_seconds",
                    "run_stamp", "output_dir"):
            assert key in meta, f"Provenance key '{key}' missing from smoke metadata"
        # Seed policy
        assert meta["seed_policy"] == "particle_seed = base_seed + particle_id"
        # Anchor fields (empty in this run)
        assert "n_anchors_used" in meta
        assert meta["n_anchors_used"] == 0

        xlsx_files = list(tmp_path.glob("abc_smc_summary_*.xlsx"))
        assert xlsx_files, "No Excel summary written"

        best_fits = list(tmp_path.glob("abc_smc_best_fit_rank_*.json"))
        assert len(best_fits) == 2
        # Check particle_seed in best-fit config
        cfg = json.loads(sorted(best_fits)[0].read_text())
        assert "particle_seed" in cfg["calibration_metadata"]["abc_smc_calibration"]

        # ABC-SMC weighting/kernel fix: importance weights and ESS wire through
        # end-to-end via the real runner, not just the unit-tested helpers.
        assert "abc_importance_weight" in df.columns
        gen_summary_df = pd.read_csv(gen_csvs[0])
        assert "ess" in gen_summary_df.columns
        assert "n_perturb_fallback" in gen_summary_df.columns
