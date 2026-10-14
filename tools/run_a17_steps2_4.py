#!/usr/bin/env python3
"""Run A-17 Steps 2--4 after the Step-1 timing gate has passed."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.a17_residual_placement import (  # noqa: E402
    DiagnosticStochasticRounding,
    allocation_bias_regimes,
    compare_annual_levels,
    stochastic_round_expected,
    synthetic_multiplicative_comparison,
    timing_summary,
)
from tools.run_a17_residual_placement import annual_rows, run_annual, sha256  # noqa: E402


def target_years_from_register(register: dict) -> dict[str, set[int]]:
    years: dict[str, set[int]] = {}
    for target in register.get("targets", []):
        metric = target.get("model_output")
        if target.get("role") == "excluded" or metric is None:
            continue
        coverage = target.get("restricted_years", target.get("year_coverage"))
        if not isinstance(coverage, list) or len(coverage) != 2:
            raise ValueError(f"invalid target coverage: {coverage}")
        years.setdefault(metric, set()).update(range(int(coverage[0]), int(coverage[1]) + 1))
    return years


def annual_digest(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["metric", "year"])[["metric", "year", "value"]]
    return hashlib.sha256(ordered.to_csv(index=False).encode("utf-8")).hexdigest()


def rare_event_check(n_seeds: int = 5000) -> dict:
    """Numerically verify no ENG-01 rare-event erasure under stochastic rounding."""
    expected = np.full(10_000, 1e-4, dtype=float)
    totals = np.empty(n_seeds, dtype=float)
    for seed in range(1, n_seeds + 1):
        rng = np.random.default_rng(seed)
        totals[seed - 1] = stochastic_round_expected(expected, rng.random(expected.shape)).sum()
    mean = float(totals.mean())
    sd = float(totals.std(ddof=1))
    se = sd / math.sqrt(n_seeds)
    expected_total = float(expected.sum())
    return {
        "n_cells": len(expected),
        "expectation_per_cell": float(expected[0]),
        "expected_total": expected_total,
        "n_replicates": n_seeds,
        "mean_allocated_total": mean,
        "sample_sd": sd,
        "standard_error": se,
        "z_mean_minus_expected": (mean - expected_total) / se,
        "fraction_zero_total": float(np.mean(totals == 0.0)),
        "fully_cellwise_round_to_nearest_total": int(np.rint(expected).sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--target-register", type=Path, required=True)
    parser.add_argument("--step1-dir", type=Path, required=True)
    args = parser.parse_args()

    step1_dir = args.step1_dir.resolve()
    raw_dir = step1_dir / "raw"
    manifest_path = step1_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    findings_path = raw_dir / "findings.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    if findings["step1_falsifies_growth_phase_compounding"]:
        raise RuntimeError("Step 1 falsified the hypothesis; brief requires stopping")

    command = shlex.join([sys.executable, *sys.argv])
    started = time.time()

    # Step 2: current production allocator, 50 pre-specified synthetic regimes.
    regimes, deciles = allocation_bias_regimes(seed=1701)
    regimes.to_csv(raw_dir / "step2_size_correlation_regimes.csv", index=False)
    deciles.to_csv(raw_dir / "step2_size_deciles.csv", index=False)

    # Step 3: all five candidates against the exact fractional process.
    paths, final_summary, synthetic_design = synthetic_multiplicative_comparison(
        steps=300, stochastic_seeds=500, design_seed=1702
    )
    paths.to_csv(raw_dir / "step3_synthetic_trajectories.csv", index=False)
    final_summary.to_csv(raw_dir / "step3_final_divergence.csv", index=False)
    rare = rare_event_check(n_seeds=5000)
    (raw_dir / "step3_rare_event_check.json").write_text(
        json.dumps(rare, indent=2) + "\n", encoding="utf-8"
    )

    # Selection is pre-specified by construction: independent stochastic
    # rounding is the only candidate whose conditional expectation equals each
    # cell's fractional expectation.  The epidemic result is not consulted.
    selected = "stochastic_rounding"

    # Step 4: opt-in monkeypatch context; production modules are restored on exit.
    config = json.loads(args.model_config.resolve().read_text(encoding="utf-8"))
    step4_rows: list[pd.DataFrame] = []
    step4_timings: list[dict] = []
    log_path = step1_dir / "step4_runner_stdout.log"
    with log_path.open("w", encoding="utf-8") as log:
        for repeat in (1, 2):
            run_started = time.time()
            with DiagnosticStochasticRounding():
                annual = run_annual(
                    config,
                    deterministic=True,
                    seed=7001,
                    run_number=repeat,
                    log=log,
                )
            frame = pd.DataFrame(
                annual_rows(annual, seed=7001, deterministic=True)
            )
            frame["repeat"] = repeat
            step4_rows.append(frame)
            step4_timings.append(
                {"repeat": repeat, "seed": 7001, "elapsed_seconds": time.time() - run_started}
            )
            print(f"A-17 Step 4 diagnostic repeat {repeat}/2 complete", flush=True)

    first, second = step4_rows
    digest_first = annual_digest(first)
    digest_second = annual_digest(second)
    reproducible = digest_first == digest_second
    if not reproducible:
        raise AssertionError("A-17 candidate failed fixed-seed reproducibility")
    first.to_csv(raw_dir / "step4_candidate_annual_outputs.csv", index=False)
    pd.DataFrame(step4_timings).to_csv(raw_dir / "step4_run_timings.csv", index=False)
    (raw_dir / "step4_reproducibility.json").write_text(
        json.dumps(
            {
                "seed": 7001,
                "repeat_1_sha256": digest_first,
                "repeat_2_sha256": digest_second,
                "bit_reproducible": reproducible,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    stochastic = pd.read_csv(raw_dir / "step1_stochastic_replicates_1975_2025.csv")
    baseline = pd.read_csv(raw_dir / "step1_yearly_log_ratio.csv")
    candidate = compare_annual_levels(first, stochastic)
    candidate.to_csv(raw_dir / "step4_yearly_log_ratio.csv", index=False)
    timing_summary(candidate).to_csv(raw_dir / "step4_timing_summary.csv", index=False)

    register = json.loads(args.target_register.resolve().read_text(encoding="utf-8"))
    scored_years = target_years_from_register(register)
    scored_mask = np.array(
        [int(row.year) in scored_years.get(row.metric, set()) for row in candidate.itertuples()]
    )
    baseline_scored = baseline.loc[scored_mask].copy()
    candidate_scored = candidate.loc[scored_mask].copy()
    baseline_median = float(baseline_scored["log_ratio_det_over_stoch_mean"].abs().median())
    candidate_median = float(candidate_scored["log_ratio_det_over_stoch_mean"].abs().median())
    candidate_inside_fraction = float(candidate_scored["det_inside_stochastic_range"].mean())
    median_halved = candidate_median < 0.5 * baseline_median
    majority_inside = candidate_inside_fraction > 0.5
    success = bool(median_halved and majority_inside)

    step4_summary_rows = []
    for metric, candidate_group in candidate_scored.groupby("metric", sort=True):
        baseline_group = baseline_scored[baseline_scored["metric"] == metric]
        base_med = float(baseline_group["log_ratio_det_over_stoch_mean"].abs().median())
        cand_med = float(candidate_group["log_ratio_det_over_stoch_mean"].abs().median())
        step4_summary_rows.append(
            {
                "metric": metric,
                "n_scored_years": len(candidate_group),
                "baseline_median_abs_log_ratio": base_med,
                "candidate_median_abs_log_ratio": cand_med,
                "candidate_to_baseline_ratio": cand_med / base_med if base_med else math.nan,
                "candidate_n_years_inside_stochastic_range": int(
                    candidate_group["det_inside_stochastic_range"].sum()
                ),
                "candidate_fraction_years_inside_stochastic_range": float(
                    candidate_group["det_inside_stochastic_range"].mean()
                ),
            }
        )
    step4_summary = pd.DataFrame(step4_summary_rows)
    step4_summary.to_csv(raw_dir / "step4_scored_year_summary.csv", index=False)

    correlations = regimes["pearson_n_vs_error"].dropna()
    important = regimes[
        (regimes["spread"] == "high_skew")
        & (regimes["cell_count"] == 1200)
        & (regimes["p"] == 1e-4)
    ].iloc[0]
    synthetic_stoch = final_summary[
        final_summary["rule"] == "stochastic_rounding"
    ].iloc[0]
    cellwise = final_summary[
        final_summary["rule"] == "fully_cellwise_rounding"
    ].iloc[0]
    findings.update(
        {
            "later_steps_run": True,
            "step2": {
                "n_regimes": len(regimes),
                "n_positive_pearson_correlations": int((correlations > 0).sum()),
                "n_negative_pearson_correlations": int((correlations < 0).sum()),
                "n_zero_pearson_correlations": int((correlations == 0).sum()),
                "n_undefined_pearson_correlations": int(regimes["pearson_n_vs_error"].isna().sum()),
                "epidemic_shaped_high_skew_small_p_pearson": float(
                    important["pearson_n_vs_error"]
                ),
                "epidemic_shaped_high_skew_small_p_spearman": float(
                    important["spearman_n_vs_error"]
                ),
            },
            "step3": {
                "selected_candidate": selected,
                "selection_basis": "cellwise conditional unbiasedness by construction",
                "stochastic_rounding_final_relative_divergence_of_ensemble_mean": float(
                    synthetic_stoch["final_relative_divergence"]
                ),
                "fully_cellwise_final_relative_divergence": float(
                    cellwise["final_relative_divergence"]
                ),
                "rare_event_check": rare,
            },
            "step4": {
                "baseline_median_abs_log_ratio_scored_target_years": baseline_median,
                "success_half_threshold": 0.5 * baseline_median,
                "candidate_median_abs_log_ratio_scored_target_years": candidate_median,
                "median_below_half_baseline": bool(median_halved),
                "candidate_fraction_scored_target_years_inside_stochastic_range": candidate_inside_fraction,
                "majority_inside_stochastic_range": bool(majority_inside),
                "success": success,
                "bit_reproducible_fixed_seed": reproducible,
            },
        }
    )
    findings_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")

    manifest["steps_2_to_4_command"] = command
    manifest["steps_2_to_4_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["steps_2_to_4_elapsed_seconds"] = time.time() - started
    manifest["steps_2_to_4_input_sha256"] = {
        "model_config": sha256(args.model_config.resolve()),
        "target_register": sha256(args.target_register.resolve()),
        "tools/a17_residual_placement.py": sha256(
            ROOT / "tools/a17_residual_placement.py"
        ),
        "tools/run_a17_steps2_4.py": sha256(Path(__file__).resolve()),
        "counter_based_rng.py": sha256(ROOT / "counter_based_rng.py"),
        "stochastic_allocation.py": sha256(ROOT / "stochastic_allocation.py"),
        "run_v3.py": sha256(ROOT / "run_v3.py"),
        "demography.py": sha256(ROOT / "demography.py"),
        "transmission_corrected.py": sha256(ROOT / "transmission_corrected.py"),
    }
    manifest["step2_design"] = {
        "seed": 1701,
        "cell_counts": [20, 200, 1200],
        "count_spreads": ["lognormal_sigma_0.1", "lognormal_sigma_0.8", "lognormal_sigma_2.0"],
        "additional_regime": "1200 cells: 600 at n=9900 and 600 at n=100",
        "p": [1e-4, 1e-3, 1e-2, 0.1, 0.5],
    }
    manifest["step3_design"] = synthetic_design
    manifest["step3_rules"] = [
        "largest remainder (current control)",
        "counter-based stochastic rounding (500-seed mean plus fixed seed)",
        "rotating systematic placement",
        "largest remainder with hash-based size-independent tie break",
        "fully cellwise round-to-nearest (ENG-01 control)",
    ]
    manifest["step4_variant_scope"] = (
        "Opt-in context manager patches the shared binomial residual allocator, "
        "adult background-mortality binding, and deterministic transmission residual "
        "allocator only during each diagnostic run; class/module bindings are restored "
        "on context exit. Production defaults and files are unchanged."
    )
    manifest["result"] = findings
    output_paths = sorted(
        path
        for path in step1_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    manifest["output_sha256"] = {
        str(path.relative_to(step1_dir)): sha256(path) for path in output_paths
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    hash_paths = sorted(
        path for path in step1_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (step1_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(step1_dir)}\n" for path in hash_paths),
        encoding="utf-8",
    )
    print(f"A-17 Steps 2-4 artifacts written to {step1_dir}")
    print(f"A-17 Step 4 success criterion: {'PASS' if success else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
