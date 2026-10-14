#!/usr/bin/env python3
"""Run Phase-2 B-8: deterministic versus stochastic levels of the calibration targets.

A-5a (`outputs/calibration/a5a_epidemiological_seed_variability_20260910_162000/`)
already supplies the stochastic half: n=50 replicates, seeds 7001-7050, at one
fixed epidemiological configuration.  B-8 runs the *same* configuration in
deterministic mode and compares the two annual series, year by year, for all
seven A-5a calibration outputs.

Everything except ``run_control.deterministic_mode`` is held identical to A-5a:
same config file, same horizon (``extract_annual_outputs(start_year=1975,
end_year=2025)``), same target-year derivation from the target register, same
metric set.  Any departure is recorded in the manifest under ``departures``.

There is only ONE deterministic run, so there is NO deterministic standard
error.  ``abs_z`` below is (deterministic - stochastic mean) / stochastic SE:
a point value compared against the sampling distribution of the stochastic
mean.  ``abs_z_single_draw`` divides by the replicate SD instead and answers a
different question - how unusual the deterministic value would be as a single
stochastic replicate.  Both are reported; neither is a test of the deterministic
value's own precision, because a deterministic run has no sampling variation.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402

# Identical to tools/run_a5a_epidemiological_seed_variability.py
METRICS = (
    "PLHIV",
    "new_diagnoses",
    "new_diagnoses_msm",
    "aids_deaths",
    "on_art_stock",
    "new_infections",
    "new_aids_cases",
)
SCENARIO_DET = "submitted_nearfit_rank001_deterministic"
SCENARIO_STO = "submitted_nearfit_rank001_stochastic"
EXTRACT_START_YEAR = 1975
EXTRACT_END_YEAR = 2025


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: Iterable[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def target_years_from_register(register: dict) -> dict[str, list[int]]:
    """Verbatim copy of the A-5a derivation, so the year sets cannot drift."""
    years: dict[str, set[int]] = {metric: set() for metric in METRICS}
    for target in register.get("targets", []):
        metric = target.get("model_output")
        if metric not in years or target.get("role") == "excluded":
            continue
        coverage = target.get("restricted_years", target.get("year_coverage"))
        if not isinstance(coverage, list) or len(coverage) != 2:
            raise ValueError(
                f"invalid year coverage for {target.get('series_name')}: {coverage}"
            )
        start, end = map(int, coverage)
        years[metric].update(range(start, end + 1))
    missing = [metric for metric, values in years.items() if not values]
    if missing:
        raise ValueError(f"target register has no years for B-8 metrics: {missing}")
    return {metric: sorted(values) for metric, values in years.items()}


def run_deterministic(config: dict, seed: int, run_number: int, log) -> dict:
    """One deterministic simulation; returns the annual calibration outputs."""
    run_config = json.loads(json.dumps(config))
    run_config["run_control"]["seed"] = {"value": int(seed)}
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        result = SimulationEngineV3(run_config, seed=int(seed)).run(run_number=run_number)
    log.write(f"===== deterministic seed {seed} =====\n{buffer.getvalue()}")
    return extract_annual_outputs(
        result, start_year=EXTRACT_START_YEAR, end_year=EXTRACT_END_YEAR
    )


def run_stochastic(config: dict, seed: int, run_number: int, log) -> dict:
    """One stochastic simulation on CURRENT code, for the A-5a drift spot-check."""
    run_config = json.loads(json.dumps(config))
    run_config["run_control"]["deterministic_mode"]["value"] = False
    run_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": False
    }
    run_config["run_control"]["seed"] = {"value": int(seed)}
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        result = SimulationEngineV3(run_config, seed=int(seed)).run(run_number=run_number)
    log.write(f"===== stochastic spot-check seed {seed} =====\n{buffer.getvalue()}")
    return extract_annual_outputs(
        result, start_year=EXTRACT_START_YEAR, end_year=EXTRACT_END_YEAR
    )


def annual_rows(annual: dict, target_years: dict[str, list[int]], seed: int) -> list[dict]:
    rows: list[dict] = []
    for metric in METRICS:
        if metric not in annual:
            raise KeyError(f"simulation did not emit required B-8 metric {metric}")
        for year in target_years[metric]:
            if year not in annual[metric]:
                raise KeyError(f"simulation missing {metric}/{year}")
            rows.append(
                {
                    "scenario": SCENARIO_DET,
                    "seed": int(seed),
                    "metric": metric,
                    "year": int(year),
                    "value": float(annual[metric][year]),
                }
            )
    return rows


def build_comparison(
    deterministic: pd.DataFrame, stochastic: pd.DataFrame
) -> pd.DataFrame:
    """Join the deterministic point value onto the A-5a stochastic summary."""
    needed = {
        "metric",
        "year",
        "n_replicates",
        "mean",
        "sample_variance_v_ensemble",
        "sample_sd",
        "minimum",
        "maximum",
    }
    if missing := needed - set(stochastic.columns):
        raise ValueError(f"A-5a summary missing columns: {sorted(missing)}")

    merged = stochastic.merge(
        deterministic[["metric", "year", "value"]].rename(
            columns={"value": "deterministic"}
        ),
        on=["metric", "year"],
        how="outer",
        indicator=True,
    )
    if not (merged["_merge"] == "both").all():
        bad = merged[merged["_merge"] != "both"][["metric", "year", "_merge"]]
        raise AssertionError(f"metric/year sets differ between modes:\n{bad}")
    merged = merged.drop(columns=["_merge"])

    n = merged["n_replicates"].to_numpy(dtype=float)
    sd = merged["sample_sd"].to_numpy(dtype=float)
    mean = merged["mean"].to_numpy(dtype=float)
    det = merged["deterministic"].to_numpy(dtype=float)

    se = sd / np.sqrt(n)
    diff = det - mean
    with np.errstate(divide="ignore", invalid="ignore"):
        abs_z = np.abs(np.where(se > 0.0, diff / se, np.nan))
        abs_z_single = np.abs(np.where(sd > 0.0, diff / sd, np.nan))
        pct_diff = np.where(mean != 0.0, 100.0 * diff / mean, np.nan)

    merged["stochastic_mean"] = mean
    merged["stochastic_sd"] = sd
    merged["stochastic_se"] = se
    merged["difference_det_minus_stoch_mean"] = diff
    merged["pct_difference_vs_stoch_mean"] = pct_diff
    merged["abs_z_vs_se_of_mean"] = abs_z
    merged["abs_z_single_draw_vs_sd"] = abs_z_single
    merged["det_inside_stoch_replicate_range"] = (det >= merged["minimum"]) & (
        det <= merged["maximum"]
    )
    merged["exceeds_2se"] = abs_z > 2.0
    merged["near_zero_stochastic_variance"] = sd <= 0.0
    ordered = [
        "metric",
        "year",
        "deterministic",
        "stochastic_mean",
        "stochastic_sd",
        "stochastic_se",
        "n_replicates",
        "difference_det_minus_stoch_mean",
        "pct_difference_vs_stoch_mean",
        "abs_z_vs_se_of_mean",
        "abs_z_single_draw_vs_sd",
        "det_inside_stoch_replicate_range",
        "minimum",
        "maximum",
        "exceeds_2se",
        "near_zero_stochastic_variance",
        "sample_variance_v_ensemble",
    ]
    return merged[ordered].sort_values(["metric", "year"]).reset_index(drop=True)


def series_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for metric, group in comparison.groupby("metric", sort=True):
        n_years = len(group)
        exceed = int(group["exceeds_2se"].sum())
        finite_z = group["abs_z_vs_se_of_mean"].dropna()
        rows.append(
            {
                "metric": metric,
                "n_target_years": n_years,
                "n_replicates_stochastic": int(group["n_replicates"].iloc[0]),
                "n_years_abs_z_gt_2": exceed,
                "frac_years_abs_z_gt_2": exceed / n_years,
                "majority_of_years_gt_2se": exceed > n_years / 2,
                "median_abs_z": float(finite_z.median()) if len(finite_z) else math.nan,
                "max_abs_z": float(finite_z.max()) if len(finite_z) else math.nan,
                "year_of_max_abs_z": (
                    int(group.loc[finite_z.idxmax(), "year"]) if len(finite_z) else -1
                ),
                "median_pct_difference": float(
                    group["pct_difference_vs_stoch_mean"].median()
                ),
                "mean_pct_difference": float(
                    group["pct_difference_vs_stoch_mean"].mean()
                ),
                "n_years_det_above_stoch_mean": int(
                    (group["difference_det_minus_stoch_mean"] > 0).sum()
                ),
                "n_years_det_below_stoch_mean": int(
                    (group["difference_det_minus_stoch_mean"] < 0).sum()
                ),
                "n_years_det_outside_replicate_range": int(
                    (~group["det_inside_stoch_replicate_range"]).sum()
                ),
                "n_years_zero_stochastic_variance": int(
                    group["near_zero_stochastic_variance"].sum()
                ),
                "median_abs_z_single_draw": float(
                    group["abs_z_single_draw_vs_sd"].median()
                ),
                "sum_deterministic": float(group["deterministic"].sum()),
                "sum_stochastic_mean": float(group["stochastic_mean"].sum()),
            }
        )
    return pd.DataFrame(rows)


def target_fit_table(
    comparison: pd.DataFrame, implausibility_csv: Path
) -> pd.DataFrame:
    """Distance to the tier-1 observed target under each mode, where an observation exists.

    The observed values ``z`` are READ FROM an existing artifact
    (the HM prototype's per-target implausibility table), not recomputed here.
    """
    if not implausibility_csv.exists():
        return pd.DataFrame()
    obs = pd.read_csv(implausibility_csv)
    obs = obs[["target_key", "series_name", "model_output", "year", "z", "role"]].rename(
        columns={"model_output": "metric", "z": "observed_target"}
    )
    merged = obs.merge(comparison, on=["metric", "year"], how="inner")
    merged["det_minus_target"] = merged["deterministic"] - merged["observed_target"]
    merged["stoch_mean_minus_target"] = (
        merged["stochastic_mean"] - merged["observed_target"]
    )
    merged["abs_det_minus_target"] = merged["det_minus_target"].abs()
    merged["abs_stoch_minus_target"] = merged["stoch_mean_minus_target"].abs()
    merged["deterministic_closer_to_target"] = (
        merged["abs_det_minus_target"] < merged["abs_stoch_minus_target"]
    )
    return merged.sort_values(["series_name", "year"]).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--target-register", type=Path, required=True)
    parser.add_argument("--a5a-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--seed",
        type=int,
        default=7001,
        help="Nominal seed for the deterministic run (A-5a's first seed).",
    )
    parser.add_argument(
        "--seed-invariance-check",
        type=int,
        nargs="*",
        default=[7050, 42],
        help="Extra seeds run deterministically to verify seed-independence.",
    )
    parser.add_argument(
        "--stochastic-spotcheck-seeds",
        type=int,
        nargs="*",
        default=list(range(7101, 7111)),
        help=(
            "Fresh stochastic seeds run on CURRENT code to test whether the "
            "A-4 counter-based-RNG change since A-5a shifted the sampling "
            "distribution of the annual outputs. Pass no values to skip."
        ),
    )
    parser.add_argument(
        "--implausibility-csv",
        type=Path,
        default=ROOT
        / "outputs/calibration/hm_prototype_20260910/raw"
        / "post_c1_without_non_msm__per_target_implausibility.csv",
    )
    args = parser.parse_args()

    config_path = args.model_config.resolve()
    register_path = args.target_register.resolve()
    a5a_dir = args.a5a_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir()
    provenance_dir.mkdir()

    git_commit = git_text(["rev-parse", "HEAD"])
    git_status = git_text(["status", "--short"])
    git_diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    (provenance_dir / "git_status_short.txt").write_text(
        git_status + ("\n" if git_status else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(git_diff)

    # --- stochastic half: read A-5a, and verify it is the artifact it claims to be
    a5a_manifest = json.loads((a5a_dir / "manifest.json").read_text(encoding="utf-8"))
    a5a_yearly_path = a5a_dir / "raw" / "yearly_v_ensemble.csv"
    a5a_yearly_hash = sha256(a5a_yearly_path)
    recorded_hash = a5a_manifest["output_sha256"]["raw/yearly_v_ensemble.csv"]
    if a5a_yearly_hash != recorded_hash:
        raise AssertionError(
            "A-5a yearly_v_ensemble.csv hash does not match its own manifest; "
            f"file={a5a_yearly_hash} manifest={recorded_hash}"
        )
    if a5a_manifest["input_sha256"]["model_config"] != sha256(config_path):
        raise AssertionError(
            "B-8 config is not the config A-5a ran; the comparison would not be "
            "at a fixed parameter point"
        )
    # Code drift since A-5a. `calib_extractor.py` (the extraction path) and
    # `stochastic_allocation.py` (the object under study) must not have moved or
    # the comparison is meaningless. `run_v3.py` DID move between A-5a and now
    # (the A-4 counter-based-RNG change): recorded here, quantified by the
    # deterministic bit-equivalence and stochastic spot-check runs below.
    code_drift = {}
    for name in ("run_v3.py", "engine_v3.py", "calib_extractor.py", "stochastic_allocation.py"):
        recorded = a5a_manifest["input_sha256"][f"code/{name}"]
        current = sha256(ROOT / name)
        code_drift[name] = {
            "a5a_sha256": recorded,
            "current_sha256": current,
            "unchanged": recorded == current,
        }
    for name in ("calib_extractor.py", "stochastic_allocation.py", "engine_v3.py"):
        if not code_drift[name]["unchanged"]:
            raise AssertionError(
                f"{name} changed since A-5a ran; the comparison would not be "
                "at a fixed extraction/allocation path. Re-run A-5a first."
            )
    stochastic = pd.read_csv(a5a_yearly_path)
    stochastic = stochastic[stochastic["scenario"] == SCENARIO_STO].copy()

    register = json.loads(register_path.read_text(encoding="utf-8"))
    target_years = target_years_from_register(register)
    if target_years != {
        metric: [int(y) for y in years]
        for metric, years in a5a_manifest["target_years"].items()
    }:
        raise AssertionError(
            "target-year sets differ from A-5a; the register changed since A-5a ran"
        )

    # --- deterministic half: A-5a's config handling, with the run mode flipped
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["run_control"]["deterministic_mode"]["value"] = True
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {"value": True}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False

    code_files = (
        "run_v3.py",
        "engine_v3.py",
        "calib_extractor.py",
        "stochastic_allocation.py",
    )
    manifest = {
        "task": "B-8 deterministic versus stochastic calibration target levels",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario_deterministic": SCENARIO_DET,
        "scenario_stochastic": SCENARIO_STO,
        "mode": "deterministic (1 run) versus A-5a stochastic ensemble (n=50)",
        "deterministic_seed": int(args.seed),
        "seed_invariance_check_seeds": [int(s) for s in args.seed_invariance_check],
        "stochastic_source_dir": str(a5a_dir),
        "stochastic_n_replicates": a5a_manifest["n_replicates"],
        "stochastic_seeds": a5a_manifest["seeds"],
        "model_config": str(config_path),
        "target_register": str(register_path),
        "extraction": {
            "function": "calib_extractor.extract_annual_outputs",
            "start_year": EXTRACT_START_YEAR,
            "end_year": EXTRACT_END_YEAR,
        },
        "departures_from_a5a": [
            "run_control.deterministic_mode flipped from false to true (the "
            "intended and only intended difference)",
            "one run instead of 50; there is therefore no deterministic standard error",
        ],
        "git_commit": git_commit,
        "git_dirty": bool(git_status),
        "git_status_short": git_status,
        "git_status_short_sha256": sha256(provenance_dir / "git_status_short.txt"),
        "git_diff_sha256": sha256(provenance_dir / "git_diff.patch"),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "code_drift_since_a5a": code_drift,
        "input_sha256": {
            "model_config": sha256(config_path),
            "target_register": sha256(register_path),
            "a5a/raw/yearly_v_ensemble.csv": a5a_yearly_hash,
            "a5a/manifest.json": sha256(a5a_dir / "manifest.json"),
            f"code/tools/{Path(__file__).name}": sha256(Path(__file__).resolve()),
            **{f"code/{name}": sha256(ROOT / name) for name in code_files},
        },
        "z_definition": (
            "abs_z_vs_se_of_mean = |deterministic - stochastic mean| / "
            "(stochastic sample SD / sqrt(50)). A single deterministic run has no "
            "standard error of its own; this compares a point value against the "
            "sampling distribution of the stochastic mean."
        ),
        "target_years": target_years,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    start = time.time()
    timing_rows: list[dict] = []
    log_path = output_dir / "runner_stdout.log"
    det_rows: list[dict] = []
    invariance_rows: list[dict] = []
    with log_path.open("w", encoding="utf-8") as log:
        run_started = time.time()
        annual = run_deterministic(config, args.seed, 1, log)
        timing_rows.append(
            {
                "run_index": 1,
                "seed": int(args.seed),
                "role": "primary",
                "elapsed_seconds": time.time() - run_started,
            }
        )
        det_rows = annual_rows(annual, target_years, args.seed)
        print(f"B-8 deterministic primary run complete (seed={args.seed})", flush=True)

        for index, seed in enumerate(args.seed_invariance_check, start=2):
            run_started = time.time()
            other = run_deterministic(config, int(seed), index, log)
            timing_rows.append(
                {
                    "run_index": index,
                    "seed": int(seed),
                    "role": "seed_invariance_check",
                    "elapsed_seconds": time.time() - run_started,
                }
            )
            rows = annual_rows(other, target_years, int(seed))
            invariance_rows.extend(rows)
            print(f"B-8 deterministic check run complete (seed={seed})", flush=True)

        spot_rows: list[dict] = []
        next_index = 2 + len(args.seed_invariance_check)
        for offset, seed in enumerate(args.stochastic_spotcheck_seeds):
            index = next_index + offset
            run_started = time.time()
            spot = run_stochastic(config, int(seed), index, log)
            timing_rows.append(
                {
                    "run_index": index,
                    "seed": int(seed),
                    "role": "stochastic_spotcheck",
                    "elapsed_seconds": time.time() - run_started,
                }
            )
            for row in annual_rows(spot, target_years, int(seed)):
                row["scenario"] = "spotcheck_stochastic_current_code"
                spot_rows.append(row)
            print(f"B-8 stochastic spot-check run complete (seed={seed})", flush=True)

    deterministic = pd.DataFrame(det_rows)

    # Seed invariance: deterministic mode must not depend on the seed.
    invariance_report: list[dict] = []
    if invariance_rows:
        checks = pd.DataFrame(invariance_rows)
        base = deterministic.set_index(["metric", "year"])["value"]
        for seed, group in checks.groupby("seed"):
            other = group.set_index(["metric", "year"])["value"]
            aligned = base.align(other, join="inner")
            max_abs = float(np.max(np.abs(aligned[0].to_numpy() - aligned[1].to_numpy())))
            invariance_report.append(
                {
                    "seed": int(seed),
                    "n_compared": int(len(aligned[0])),
                    "max_abs_difference_vs_primary": max_abs,
                    "identical": max_abs == 0.0,
                }
            )

    spotcheck_summary: list[dict] = []
    spotcheck_yearly = pd.DataFrame()
    if spot_rows:
        spot_df = pd.DataFrame(spot_rows)
        agg = (
            spot_df.groupby(["metric", "year"])["value"]
            .agg(
                spot_mean="mean",
                spot_var=lambda v: float(np.var(v, ddof=1)),
                spot_n="size",
            )
            .reset_index()
        )
        spotcheck_yearly = agg.merge(
            stochastic[
                ["metric", "year", "mean", "sample_variance_v_ensemble", "n_replicates"]
            ],
            on=["metric", "year"],
            how="inner",
        ).rename(
            columns={
                "mean": "a5a_mean",
                "sample_variance_v_ensemble": "a5a_var",
                "n_replicates": "a5a_n",
            }
        )
        se_diff = np.sqrt(
            spotcheck_yearly["a5a_var"] / spotcheck_yearly["a5a_n"]
            + spotcheck_yearly["spot_var"] / spotcheck_yearly["spot_n"]
        )
        diff = spotcheck_yearly["spot_mean"] - spotcheck_yearly["a5a_mean"]
        with np.errstate(divide="ignore", invalid="ignore"):
            spotcheck_yearly["abs_z_welch"] = np.abs(
                np.where(se_diff > 0, diff / se_diff, np.nan)
            )
        spotcheck_yearly["difference"] = diff
        for metric, group in spotcheck_yearly.groupby("metric", sort=True):
            z = group["abs_z_welch"].dropna()
            spotcheck_summary.append(
                {
                    "metric": metric,
                    "n_target_years": int(len(group)),
                    "n_years_abs_z_gt_2": int((z > 2.0).sum()),
                    "frac_years_abs_z_gt_2": float((z > 2.0).mean())
                    if len(z)
                    else math.nan,
                    "median_abs_z": float(z.median()) if len(z) else math.nan,
                    "max_abs_z": float(z.max()) if len(z) else math.nan,
                }
            )

    comparison = build_comparison(deterministic, stochastic)
    summary = series_summary(comparison)
    fit = target_fit_table(comparison, args.implausibility_csv.resolve())

    expected_targets = sum(len(target_years[metric]) for metric in METRICS)
    if len(comparison) != expected_targets:
        raise AssertionError(
            f"comparison has {len(comparison)} rows, expected {expected_targets}"
        )
    if len(summary) != len(METRICS):
        raise AssertionError("summary row count does not match the metric set")

    deterministic.to_csv(raw_dir / "deterministic_annual_outputs.csv", index=False)
    comparison.to_csv(raw_dir / "target_level_comparison.csv", index=False)
    summary.to_csv(raw_dir / "series_divergence_summary.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(raw_dir / "run_timings.csv", index=False)
    if invariance_report:
        pd.DataFrame(invariance_report).to_csv(
            raw_dir / "seed_invariance_check.csv", index=False
        )
    if len(fit):
        fit.to_csv(raw_dir / "tier1_target_fit_direction.csv", index=False)
    if len(spotcheck_yearly):
        spotcheck_yearly.to_csv(raw_dir / "a5a_drift_spotcheck_yearly.csv", index=False)
        pd.DataFrame(spotcheck_summary).to_csv(
            raw_dir / "a5a_drift_spotcheck_summary.csv", index=False
        )
        pd.DataFrame(spot_rows).to_csv(
            raw_dir / "a5a_drift_spotcheck_replicates.csv", index=False
        )

    escalations = {
        "majority_of_years_beyond_2se": sorted(
            summary.loc[summary["majority_of_years_gt_2se"], "metric"].tolist()
        ),
        "new_diagnoses_diverges_any_year": bool(
            summary.loc[summary["metric"] == "new_diagnoses", "n_years_abs_z_gt_2"].iloc[0]
            > 0
        ),
    }
    findings = {
        "deterministic_seed": int(args.seed),
        "code_drift_since_a5a": code_drift,
        "a5a_drift_spotcheck": spotcheck_summary,
        "seed_invariance": invariance_report,
        "escalations": escalations,
        "series": summary.to_dict(orient="records"),
    }
    if len(fit):
        fit_rows = []
        for series_name, group in fit.groupby("series_name", sort=True):
            fit_rows.append(
                {
                    "series_name": series_name,
                    "n_target_years": int(len(group)),
                    "median_abs_det_minus_target": float(
                        group["abs_det_minus_target"].median()
                    ),
                    "median_abs_stoch_minus_target": float(
                        group["abs_stoch_minus_target"].median()
                    ),
                    "n_years_deterministic_closer": int(
                        group["deterministic_closer_to_target"].sum()
                    ),
                    "sum_abs_det_minus_target": float(
                        group["abs_det_minus_target"].sum()
                    ),
                    "sum_abs_stoch_minus_target": float(
                        group["abs_stoch_minus_target"].sum()
                    ),
                }
            )
        findings["tier1_fit_direction"] = fit_rows
    (raw_dir / "findings.json").write_text(json.dumps(findings, indent=2) + "\n")

    elapsed = time.time() - start
    manifest.update(
        {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed,
            "counts": {
                "deterministic_rows": int(len(deterministic)),
                "comparison_rows": int(len(comparison)),
                "series_summary_rows": int(len(summary)),
                "tier1_fit_rows": int(len(fit)),
            },
            "seed_invariance": invariance_report,
            "a5a_drift_spotcheck": spotcheck_summary,
            "a5a_drift_spotcheck_seeds": [int(x) for x in args.stochastic_spotcheck_seeds],
            "escalations": escalations,
        }
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"B-8 raw artifacts written to {output_dir} in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
