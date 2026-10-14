"""Pure analysis helpers for A-17's residual-placement diagnostic.

This module is diagnostic infrastructure only.  Nothing in the production
simulation imports it.
"""

from __future__ import annotations

import math
from contextlib import AbstractContextManager
from dataclasses import dataclass
from hashlib import blake2b

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from counter_based_rng import CounterBasedRNGFactory
from stochastic_allocation import deterministic_allocate_events


METRICS = (
    "PLHIV",
    "new_diagnoses",
    "new_diagnoses_msm",
    "aids_deaths",
    "on_art_stock",
    "new_infections",
    "new_aids_cases",
)


def compare_annual_levels(
    deterministic: pd.DataFrame, stochastic_replicates: pd.DataFrame
) -> pd.DataFrame:
    """Compare one deterministic value with an n-seed ensemble by metric/year."""
    det_required = {"metric", "year", "value"}
    sto_required = {"seed", "metric", "year", "value"}
    if missing := det_required - set(deterministic):
        raise ValueError(f"deterministic table missing columns: {sorted(missing)}")
    if missing := sto_required - set(stochastic_replicates):
        raise ValueError(f"stochastic table missing columns: {sorted(missing)}")

    duplicates = deterministic.duplicated(["metric", "year"])
    if duplicates.any():
        raise ValueError("deterministic table has duplicate metric/year rows")

    summary = (
        stochastic_replicates.groupby(["metric", "year"])["value"]
        .agg(
            stochastic_mean="mean",
            stochastic_sd=lambda values: float(np.std(values, ddof=1)),
            stochastic_min="min",
            stochastic_max="max",
            n_replicates="size",
        )
        .reset_index()
    )
    merged = deterministic[["metric", "year", "value"]].rename(
        columns={"value": "deterministic"}
    ).merge(summary, on=["metric", "year"], how="outer", indicator=True)
    if not (merged["_merge"] == "both").all():
        raise AssertionError("deterministic and stochastic metric/year sets differ")
    merged = merged.drop(columns="_merge")

    det = merged["deterministic"].to_numpy(dtype=float)
    mean = merged["stochastic_mean"].to_numpy(dtype=float)
    sd = merged["stochastic_sd"].to_numpy(dtype=float)
    n = merged["n_replicates"].to_numpy(dtype=float)
    diff = det - mean
    se = sd / np.sqrt(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["log_ratio_det_over_stoch_mean"] = np.where(
            (det > 0.0) & (mean > 0.0), np.log(det / mean), np.nan
        )
        merged["abs_z_vs_se_of_mean"] = np.where(se > 0.0, np.abs(diff / se), np.nan)
        merged["pct_difference"] = np.where(mean != 0.0, 100.0 * diff / mean, np.nan)
    merged["stochastic_se"] = se
    merged["difference"] = diff
    merged["det_inside_stochastic_range"] = (
        (det >= merged["stochastic_min"]) & (det <= merged["stochastic_max"])
    )
    return merged.sort_values(["metric", "year"]).reset_index(drop=True)


def timing_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    """Return the first outside-range year and log-ratio extrema per series."""
    rows: list[dict] = []
    for metric, group in comparison.groupby("metric", sort=True):
        group = group.sort_values("year")
        outside = group[~group["det_inside_stochastic_range"]]
        finite = group[np.isfinite(group["log_ratio_det_over_stoch_mean"])]
        rows.append(
            {
                "metric": metric,
                "n_years": int(len(group)),
                "n_years_outside_stochastic_range": int(len(outside)),
                "first_year_outside_stochastic_range": (
                    int(outside.iloc[0]["year"]) if len(outside) else math.nan
                ),
                "first_finite_log_ratio_year": (
                    int(finite.iloc[0]["year"]) if len(finite) else math.nan
                ),
                "first_finite_log_ratio": (
                    float(finite.iloc[0]["log_ratio_det_over_stoch_mean"])
                    if len(finite)
                    else math.nan
                ),
                "max_abs_log_ratio": (
                    float(finite["log_ratio_det_over_stoch_mean"].abs().max())
                    if len(finite)
                    else math.nan
                ),
                "year_of_max_abs_log_ratio": (
                    int(
                        finite.loc[
                            finite["log_ratio_det_over_stoch_mean"].abs().idxmax(),
                            "year",
                        ]
                    )
                    if len(finite)
                    else math.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def step1_falsifies_compounding(comparison: pd.DataFrame) -> bool:
    """Apply A-17's pre-specified seed-year/first-full-year stop rule."""
    plhiv = comparison[
        (comparison["metric"] == "PLHIV") & comparison["year"].isin([1975, 1976])
    ]
    if set(plhiv["year"].astype(int)) != {1975, 1976}:
        raise ValueError("PLHIV comparison must contain both 1975 and 1976")
    return bool((~plhiv["det_inside_stochastic_range"]).any())


def _validate_expected(expected: np.ndarray, capacity: np.ndarray | None = None):
    expected_arr = np.asarray(expected, dtype=float)
    if np.any(~np.isfinite(expected_arr)) or np.any(expected_arr < 0.0):
        raise ValueError("expected events must be finite and non-negative")
    if capacity is None:
        capacity_arr = None
        capped = expected_arr
    else:
        capacity_arr = np.asarray(capacity, dtype=np.int64)
        if capacity_arr.shape != expected_arr.shape or np.any(capacity_arr < 0):
            raise ValueError("capacity must be non-negative and match expected shape")
        capped = np.minimum(expected_arr, capacity_arr.astype(float))
    return expected_arr, capacity_arr, capped


def largest_remainder_expected(
    expected: np.ndarray,
    capacity: np.ndarray | None = None,
    *,
    tie_keys: np.ndarray | None = None,
) -> np.ndarray:
    """Generalised largest-remainder allocation used by the synthetic harness."""
    _, capacity_arr, capped = _validate_expected(expected, capacity)
    base = np.floor(capped).astype(np.int64)
    residual_capacity = (
        np.ones_like(base, dtype=np.int64)
        if capacity_arr is None
        else capacity_arr - base
    )
    target = int(np.round(capped.sum()))
    if capacity_arr is not None:
        target = min(target, int(capacity_arr.sum()))
    remainder = target - int(base.sum())
    if remainder <= 0:
        return base

    eligible = np.flatnonzero(residual_capacity.ravel() > 0)
    fractions = (capped - base).ravel()
    if tie_keys is None:
        order = eligible[np.argsort(-fractions[eligible], kind="stable")]
    else:
        keys = np.asarray(tie_keys).ravel()
        if keys.shape != fractions.shape:
            raise ValueError("tie_keys must match expected shape")
        order = eligible[np.lexsort((keys[eligible], -fractions[eligible]))]
    result = base.copy()
    result.ravel()[order[: min(remainder, len(order))]] += 1
    return result


def size_independent_tie_keys(size: int, *, seed: int, step: int) -> np.ndarray:
    """Stable hash ordering that contains no count/size information."""
    keys = np.empty(size, dtype=np.uint64)
    for index in range(size):
        digest = blake2b(
            f"a17-tie|{seed}|{step}|{index}".encode("ascii"), digest_size=8
        ).digest()
        keys[index] = int.from_bytes(digest, "little")
    return keys


def stochastic_round_expected(
    expected: np.ndarray,
    uniforms: np.ndarray,
    capacity: np.ndarray | None = None,
) -> np.ndarray:
    """Independently round each cell up with its fractional expectation."""
    _, capacity_arr, capped = _validate_expected(expected, capacity)
    uniforms_arr = np.asarray(uniforms, dtype=float)
    if uniforms_arr.shape != capped.shape:
        raise ValueError("uniforms must match expected shape")
    if np.any((uniforms_arr < 0.0) | (uniforms_arr >= 1.0)):
        raise ValueError("uniforms must lie in [0, 1)")
    base = np.floor(capped).astype(np.int64)
    result = base + (uniforms_arr < (capped - base)).astype(np.int64)
    if capacity_arr is not None:
        result = np.minimum(result, capacity_arr)
    return result


def rotating_systematic_expected(
    expected: np.ndarray, *, step: int, capacity: np.ndarray | None = None
) -> np.ndarray:
    """Rotate the residual offset through the fractional-rank order each step."""
    _, capacity_arr, capped = _validate_expected(expected, capacity)
    base = np.floor(capped).astype(np.int64)
    residual_capacity = (
        np.ones_like(base, dtype=np.int64)
        if capacity_arr is None
        else capacity_arr - base
    )
    target = int(np.round(capped.sum()))
    if capacity_arr is not None:
        target = min(target, int(capacity_arr.sum()))
    remainder = target - int(base.sum())
    eligible = np.flatnonzero(residual_capacity.ravel() > 0)
    if remainder <= 0 or not len(eligible):
        return base
    fractions = (capped - base).ravel()
    ranked = eligible[np.argsort(-fractions[eligible], kind="stable")]
    ranked = np.roll(ranked, -(int(step) % len(ranked)))
    result = base.copy()
    result.ravel()[ranked[: min(remainder, len(ranked))]] += 1
    return result


def fully_cellwise_round_expected(
    expected: np.ndarray, capacity: np.ndarray | None = None
) -> np.ndarray:
    """ENG-01 control: independently round-to-nearest without total preservation."""
    _, capacity_arr, capped = _validate_expected(expected, capacity)
    result = np.rint(capped).astype(np.int64)
    if capacity_arr is not None:
        result = np.minimum(result, capacity_arr)
    return result


def allocation_bias_regimes(seed: int = 1701) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Measure current largest-remainder error/size correlation over 50 regimes."""
    rng = np.random.default_rng(seed)
    probabilities = (1e-4, 1e-3, 1e-2, 0.1, 0.5)
    regimes: list[tuple[str, int, np.ndarray]] = []
    for cell_count in (20, 200, 1200):
        for label, sigma in (("narrow", 0.1), ("moderate", 0.8), ("high_skew", 2.0)):
            counts = np.maximum(
                1, np.rint(rng.lognormal(np.log(1000.0), sigma, cell_count))
            ).astype(np.int64)
            regimes.append((label, cell_count, counts))
    regimes.append(
        (
            "epidemic_shaped_bimodal",
            1200,
            np.concatenate(
                [np.full(600, 9_900, dtype=np.int64), np.full(600, 100, dtype=np.int64)]
            ),
        )
    )

    summary_rows: list[dict] = []
    decile_rows: list[dict] = []
    for spread, cell_count, counts in regimes:
        for probability in probabilities:
            expected = counts.astype(float) * probability
            allocated = deterministic_allocate_events(counts, probability)
            error = allocated.astype(float) - expected
            pearson = (
                float(np.corrcoef(counts, error)[0, 1])
                if np.std(counts) > 0.0 and np.std(error) > 0.0
                else math.nan
            )
            spearman = (
                float(spearmanr(counts, error).statistic)
                if np.std(counts) > 0.0 and np.std(error) > 0.0
                else math.nan
            )
            regime_id = f"{spread}__cells_{cell_count}__p_{probability:g}"
            summary_rows.append(
                {
                    "regime": regime_id,
                    "spread": spread,
                    "cell_count": cell_count,
                    "p": probability,
                    "n_min": int(counts.min()),
                    "n_median": float(np.median(counts)),
                    "n_max": int(counts.max()),
                    "expected_total": float(expected.sum()),
                    "allocated_total": int(allocated.sum()),
                    "aggregate_error": float(error.sum()),
                    "pearson_n_vs_error": pearson,
                    "spearman_n_vs_error": spearman,
                }
            )
            deciles = pd.qcut(
                pd.Series(counts).rank(method="first"), 10, labels=False
            ).to_numpy()
            for decile in range(10):
                mask = deciles == decile
                expected_sum = float(expected[mask].sum())
                allocated_sum = int(allocated[mask].sum())
                decile_rows.append(
                    {
                        "regime": regime_id,
                        "size_decile": decile + 1,
                        "n_cells": int(mask.sum()),
                        "mean_n": float(counts[mask].mean()),
                        "expected_events": expected_sum,
                        "allocated_events": allocated_sum,
                        "allocation_error": allocated_sum - expected_sum,
                        "relative_error": (
                            allocated_sum / expected_sum - 1.0
                            if expected_sum > 0.0
                            else math.nan
                        ),
                    }
                )
    return pd.DataFrame(summary_rows), pd.DataFrame(decile_rows)


RULES = (
    "largest_remainder",
    "stochastic_rounding",
    "rotating_systematic",
    "largest_remainder_size_independent_tiebreak",
    "fully_cellwise_rounding",
)


def _synthetic_path(
    rule: str,
    initial: np.ndarray,
    rates: np.ndarray,
    steps: int,
    *,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    state = np.asarray(initial, dtype=np.int64).copy()
    totals = np.empty(steps + 1, dtype=float)
    totals[0] = state.sum()
    factory = CounterBasedRNGFactory(seed)
    for step in range(steps):
        expected = state.astype(float) * rates
        if rule == "largest_remainder":
            events = largest_remainder_expected(expected)
        elif rule == "stochastic_rounding":
            uniforms = factory.for_process(
                year=float(step), timestep=step, process_id="a17_synthetic_growth"
            ).random(size=state.shape)
            events = stochastic_round_expected(expected, uniforms)
        elif rule == "rotating_systematic":
            events = rotating_systematic_expected(expected, step=step)
        elif rule == "largest_remainder_size_independent_tiebreak":
            events = largest_remainder_expected(
                expected,
                tie_keys=size_independent_tie_keys(state.size, seed=seed, step=step),
            )
        elif rule == "fully_cellwise_rounding":
            events = fully_cellwise_round_expected(expected)
        else:
            raise ValueError(f"unknown A-17 rule {rule}")
        state += events
        totals[step + 1] = state.sum()
    return totals, state


def synthetic_multiplicative_comparison(
    *, steps: int = 300, stochastic_seeds: int = 500, design_seed: int = 1702
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Compare all five rules with an exact heterogeneous linear growth process."""
    design_rng = np.random.default_rng(design_seed)
    initial = np.maximum(1, np.rint(np.geomspace(5.0, 5000.0, 120))).astype(np.int64)
    rates = np.geomspace(5e-4, 2e-2, initial.size)
    design_rng.shuffle(rates)  # rates are deliberately independent of initial size

    exact_cells = initial.astype(float)[:, None] * (
        (1.0 + rates[:, None]) ** np.arange(steps + 1)[None, :]
    )
    exact_totals = exact_cells.sum(axis=0)

    path_rows: list[dict] = []
    final_cells: dict[str, np.ndarray] = {}
    for rule in RULES:
        if rule == "stochastic_rounding":
            ensemble = np.empty((stochastic_seeds, steps + 1), dtype=float)
            final_ensemble = np.empty((stochastic_seeds, initial.size), dtype=float)
            for seed in range(1, stochastic_seeds + 1):
                totals, cells = _synthetic_path(
                    rule, initial, rates, steps, seed=seed
                )
                ensemble[seed - 1] = totals
                final_ensemble[seed - 1] = cells
            fixed_totals = ensemble[0]
            mean_totals = ensemble.mean(axis=0)
            sd_totals = ensemble.std(axis=0, ddof=1)
            final_cells[rule] = final_ensemble.mean(axis=0)
            for step in range(steps + 1):
                se = sd_totals[step] / math.sqrt(stochastic_seeds)
                path_rows.append(
                    {
                        "rule": rule,
                        "step": step,
                        "exact_total": exact_totals[step],
                        "rule_total": mean_totals[step],
                        "fixed_seed_total": fixed_totals[step],
                        "ensemble_sd": sd_totals[step],
                        "ensemble_se": se,
                        "z_mean_minus_exact": (
                            (mean_totals[step] - exact_totals[step]) / se
                            if se > 0.0
                            else 0.0
                        ),
                        "relative_divergence": mean_totals[step] / exact_totals[step] - 1.0,
                        "log_ratio": math.log(mean_totals[step] / exact_totals[step]),
                    }
                )
        else:
            totals, cells = _synthetic_path(rule, initial, rates, steps, seed=1)
            final_cells[rule] = cells.astype(float)
            for step in range(steps + 1):
                path_rows.append(
                    {
                        "rule": rule,
                        "step": step,
                        "exact_total": exact_totals[step],
                        "rule_total": totals[step],
                        "fixed_seed_total": totals[step],
                        "ensemble_sd": math.nan,
                        "ensemble_se": math.nan,
                        "z_mean_minus_exact": math.nan,
                        "relative_divergence": totals[step] / exact_totals[step] - 1.0,
                        "log_ratio": math.log(totals[step] / exact_totals[step]),
                    }
                )

    paths = pd.DataFrame(path_rows)
    final_rows: list[dict] = []
    exact_final = exact_cells[:, -1]
    for rule in RULES:
        error = final_cells[rule] - exact_final
        final_rows.append(
            {
                "rule": rule,
                "final_total": float(final_cells[rule].sum()),
                "exact_final_total": float(exact_final.sum()),
                "final_relative_divergence": float(
                    final_cells[rule].sum() / exact_final.sum() - 1.0
                ),
                "mean_absolute_cell_error": float(np.mean(np.abs(error))),
                "pearson_initial_n_vs_final_error": float(
                    np.corrcoef(initial, error)[0, 1]
                ),
            }
        )
    design = {
        "n_cells": int(initial.size),
        "steps": steps,
        "stochastic_replicates": stochastic_seeds,
        "initial_min": int(initial.min()),
        "initial_max": int(initial.max()),
        "rate_min": float(rates.min()),
        "rate_max": float(rates.max()),
        "design_seed": design_seed,
        "rates_shuffled_independently_of_initial_size": True,
    }
    return paths, pd.DataFrame(final_rows), design


def counter_based_stochastic_round(
    n: np.ndarray,
    p: float,
    *,
    seed: int,
    year: float,
    timestep: int,
    process_id,
) -> np.ndarray:
    """Candidate used by the opt-in model diagnostic."""
    n_arr = np.asarray(n, dtype=np.int64)
    expected = n_arr.astype(float) * float(p)
    uniforms = CounterBasedRNGFactory(seed).for_process(
        year=year,
        timestep=timestep,
        process_id=("a17_stochastic_rounding", process_id),
    ).random(size=n_arr.shape)
    return stochastic_round_expected(expected, uniforms, capacity=n_arr)


@dataclass
class _DiagnosticRuntime:
    engine: object | None = None
    demography_call_index: int = 0


class DiagnosticStochasticRounding(AbstractContextManager):
    """Temporarily route only residual allocations through the A-17 candidate."""

    def __init__(self) -> None:
        self.runtime = _DiagnosticRuntime()

    def __enter__(self):
        import demography
        from run_v3 import SimulationEngineV3
        from transmission_corrected import TransmissionEngine

        self._demography_module = demography
        self._engine_cls = SimulationEngineV3
        self._transmission_cls = TransmissionEngine
        self._original_step = SimulationEngineV3.step_simulation
        self._original_draw_for = SimulationEngineV3._draw_events_for
        self._original_demography_allocate = demography.deterministic_allocate_events
        self._original_transmission_allocate = (
            TransmissionEngine._deterministic_allocate_expected_infections
        )
        runtime = self.runtime

        def patched_step(engine, step):
            runtime.engine = engine
            runtime.demography_call_index = 0
            return self._original_step(engine, step)

        def patched_draw_for(engine, process_id, n, p):
            if not engine.deterministic:
                return self._original_draw_for(engine, process_id, n, p)
            return counter_based_stochastic_round(
                n,
                p,
                seed=engine.seed,
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=process_id,
            )

        def patched_demography_allocate(n, p):
            engine = runtime.engine
            if engine is None:
                raise RuntimeError("A-17 demography allocation outside a model step")
            call_index = runtime.demography_call_index
            runtime.demography_call_index += 1
            return counter_based_stochastic_round(
                n,
                p,
                seed=engine.seed,
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=("adult_background_mortality", call_index),
            )

        def patched_transmission_allocate(expected, capacity):
            engine = runtime.engine
            if engine is None:
                raise RuntimeError("A-17 transmission allocation outside a model step")
            expected_arr = np.asarray(expected, dtype=float)
            capacity_arr = np.asarray(capacity, dtype=np.int64)
            uniforms = CounterBasedRNGFactory(engine.seed).for_process(
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=("a17_stochastic_rounding", "transmission"),
            ).random(size=expected_arr.shape)
            return stochastic_round_expected(expected_arr, uniforms, capacity_arr)

        SimulationEngineV3.step_simulation = patched_step
        SimulationEngineV3._draw_events_for = patched_draw_for
        demography.deterministic_allocate_events = patched_demography_allocate
        TransmissionEngine._deterministic_allocate_expected_infections = staticmethod(
            patched_transmission_allocate
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._engine_cls.step_simulation = self._original_step
        self._engine_cls._draw_events_for = self._original_draw_for
        self._demography_module.deterministic_allocate_events = (
            self._original_demography_allocate
        )
        self._transmission_cls._deterministic_allocate_expected_infections = staticmethod(
            self._original_transmission_allocate
        )
        self.runtime.engine = None
        return False
