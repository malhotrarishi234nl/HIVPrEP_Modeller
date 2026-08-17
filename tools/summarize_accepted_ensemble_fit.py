#!/usr/bin/env python3
"""
Extract model-output timeseries and fit statistics for the accepted calibration
ensemble. Generates summary CSVs and calibration fit plots.

Usage:
    python3 tools/summarize_accepted_ensemble_fit.py \
        --manifest-csv outputs/calibration/accepted_ensemble_v13_8_3/accepted_ensemble_manifest.csv \
        --output-dir outputs/calibration/accepted_ensemble_v13_8_3
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from statistics import median
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from run_v3 import SimulationEngineV3

CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025

OUTPUTS_OF_INTEREST = [
    "PLHIV",
    "new_infections",
    "aids_deaths",
    "new_diagnoses",
    "new_diagnoses_msm",
    "new_aids_cases",
    "on_art_stock",
]

# (output_name, filename, plot_title)
PLOT_CONFIGS = [
    ("PLHIV", "ensemble_fit_plhiv.png", "PLHIV (People Living with HIV)"),
    ("new_infections", "ensemble_fit_new_infections.png", "New HIV Infections"),
    ("aids_deaths", "ensemble_fit_aids_deaths.png", "AIDS Deaths"),
    ("new_diagnoses", "ensemble_fit_new_diagnoses.png", "New Diagnoses (Total)"),
    ("new_diagnoses_msm", "ensemble_fit_new_diagnoses_msm.png", "New Diagnoses (MSM)"),
    ("new_aids_cases", "ensemble_fit_new_aids_cases.png", "New AIDS Cases"),
    ("on_art_stock", "ensemble_fit_art_stock.png", "ART Stock"),
]


def _run_candidate(config_path: str, seed: int) -> dict:
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    config["run_control"]["deterministic_mode"]["value"] = True
    config["run_control"]["start_year"]["value"] = CALIB_START_YEAR
    config["run_control"]["end_year"]["value"] = CALIB_END_YEAR + 1
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    with contextlib.redirect_stdout(io.StringIO()):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)


def _build_timeseries_rows(
    all_outputs: list[dict], manifest_rows: list[tuple[int, str]]
) -> list[dict]:
    rows = []
    for (rank, config_path), outputs in zip(manifest_rows, all_outputs):
        for output_name in OUTPUTS_OF_INTEREST:
            series = outputs.get(output_name, {})
            for year, value in sorted(series.items()):
                rows.append({
                    "rank": rank,
                    "config_path": config_path,
                    "output": output_name,
                    "year": year,
                    "model_value": value,
                })
    return rows


def _quantile_approx(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[-1]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _build_summary_rows(
    all_outputs: list[dict], targets_by_output: dict
) -> list[dict]:
    output_year_values: dict[tuple, list[float]] = {}
    for outputs in all_outputs:
        for output_name in OUTPUTS_OF_INTEREST:
            series = outputs.get(output_name, {})
            for year, value in series.items():
                key = (output_name, int(year))
                output_year_values.setdefault(key, []).append(float(value))

    rows = []
    for (output_name, year), values in sorted(output_year_values.items()):
        target_record = targets_by_output.get((output_name, year))
        n = len(values)
        row = {
            "output": output_name,
            "year": year,
            "target": float(target_record["estimate"]) if target_record else None,
            "target_lower": (
                float(target_record["lower"])
                if target_record and target_record.get("lower") is not None
                else None
            ),
            "target_upper": (
                float(target_record["upper"])
                if target_record and target_record.get("upper") is not None
                else None
            ),
            "model_median": median(values),
            "model_p2_5": _quantile_approx(values, 0.025),
            "model_p25": _quantile_approx(values, 0.25),
            "model_p75": _quantile_approx(values, 0.75),
            "model_p97_5": _quantile_approx(values, 0.975),
            "model_min": min(values),
            "model_max": max(values),
            "n_candidates": n,
        }
        rows.append(row)
    return rows


def _make_targets_by_output(targets: list[dict]) -> dict:
    by_output: dict = {}
    for t in targets:
        key = (t["model_output"], int(t["year"]))
        if key not in by_output or t.get("calibration_use") == "primary":
            by_output[key] = t
    return by_output


def _plot_output(
    output_name: str,
    title: str,
    summary_df: pd.DataFrame,
    targets_by_output: dict,
    output_path: Path,
    n_candidates: int,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = summary_df[summary_df["output"] == output_name].sort_values("year")
    if df.empty:
        print(f"  Skipping {output_name}: no data")
        return

    years = df["year"].values
    fig, ax = plt.subplots(figsize=(10, 6))

    # Ensemble intervals
    ax.fill_between(
        years, df["model_p2_5"], df["model_p97_5"],
        alpha=0.18, color="#2171b5", label="Ensemble 95% interval",
    )
    ax.fill_between(
        years, df["model_p25"], df["model_p75"],
        alpha=0.32, color="#2171b5", label="Ensemble IQR",
    )
    ax.plot(years, df["model_median"], color="#2171b5", linewidth=1.8, label="Ensemble median")

    # Target data and source bounds
    target_years, target_vals, target_lowers, target_uppers = [], [], [], []
    for year in sorted(set(int(y) for y in years)):
        t = targets_by_output.get((output_name, year))
        if t and t.get("estimate") is not None:
            target_years.append(year)
            target_vals.append(float(t["estimate"]))
            lo = t.get("lower")
            up = t.get("upper")
            target_lowers.append(float(lo) if lo is not None else float(t["estimate"]))
            target_uppers.append(float(up) if up is not None else float(t["estimate"]))

    if target_years:
        ax.fill_between(
            target_years, target_lowers, target_uppers,
            alpha=0.12, color="#d62728", label="Source bounds",
        )
        ax.plot(
            target_years, target_vals, "o-", color="#d62728",
            linewidth=1.5, markersize=4, label="Target data",
        )

    ax.set_xlabel("Year", fontsize=11)
    ax.set_ylabel(title, fontsize=11)
    ax.set_title(
        f"Accepted Ensemble Fit: {title}\n(n={n_candidates} candidates, V13.8.2 gates)",
        fontsize=12,
    )
    ax.legend(loc="best", fontsize=9, framealpha=0.85)
    ax.set_xlim(1990, CALIB_END_YEAR)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.tick_params(labelsize=10)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output_path.name}")


def _plot_combined_panel(
    summary_df: pd.DataFrame,
    targets_by_output: dict,
    output_path: Path,
    n_candidates: int,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    available = [cfg for cfg in PLOT_CONFIGS if cfg[0] in summary_df["output"].values]
    if not available:
        return

    ncols = 2
    nrows = (len(available) + 1) // 2
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, 5 * nrows))
    axes_flat = axes.flatten() if nrows > 1 else [axes[0], axes[1]]

    for ax, (output_name, _filename, title) in zip(axes_flat, available):
        df = summary_df[summary_df["output"] == output_name].sort_values("year")
        if df.empty:
            ax.set_visible(False)
            continue
        years = df["year"].values
        ax.fill_between(years, df["model_p2_5"], df["model_p97_5"], alpha=0.18, color="#2171b5")
        ax.fill_between(years, df["model_p25"], df["model_p75"], alpha=0.32, color="#2171b5")
        ax.plot(years, df["model_median"], color="#2171b5", linewidth=1.5)

        target_years, target_vals, target_lowers, target_uppers = [], [], [], []
        for year in sorted(set(int(y) for y in years)):
            t = targets_by_output.get((output_name, year))
            if t and t.get("estimate") is not None:
                target_years.append(year)
                target_vals.append(float(t["estimate"]))
                lo = t.get("lower")
                up = t.get("upper")
                target_lowers.append(float(lo) if lo is not None else float(t["estimate"]))
                target_uppers.append(float(up) if up is not None else float(t["estimate"]))
        if target_years:
            ax.fill_between(target_years, target_lowers, target_uppers, alpha=0.12, color="#d62728")
            ax.plot(target_years, target_vals, "o-", color="#d62728", linewidth=1.2, markersize=3)

        ax.set_title(title, fontsize=10)
        ax.set_xlim(1990, CALIB_END_YEAR)
        ax.grid(True, alpha=0.25, linestyle="--")
        ax.tick_params(labelsize=8)

    for extra_ax in axes_flat[len(available):]:
        extra_ax.set_visible(False)

    fig.suptitle(
        f"V13.8.3 Accepted Ensemble Fit — {n_candidates} candidates, V13.8.2 gates",
        fontsize=13, y=1.01,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved panel: {output_path.name}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize accepted ensemble fit and produce plots."
    )
    parser.add_argument(
        "--manifest-csv", required=True,
        help="Manifest CSV produced by build_accepted_calibration_ensemble.py",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory to write fit summary and figures.",
    )
    parser.add_argument(
        "--targets-workbook",
        default=str(default_disease_workbook_path(ROOT)),
    )
    parser.add_argument(
        "--target-mapping",
        default=str(default_target_mapping_path(ROOT)),
    )
    parser.add_argument("--stage", default="joint")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-plots", action="store_true",
        help="Skip figure generation (timeseries and summary CSVs still written).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    manifest = pd.read_csv(args.manifest_csv)
    if "copied_config_path" in manifest.columns:
        config_paths = manifest["copied_config_path"].tolist()
    else:
        config_paths = manifest["source_config_path"].tolist()
    ranks = manifest["rank"].tolist()
    manifest_rows = list(zip(ranks, config_paths))

    targets = load_disease_targets(
        args.targets_workbook, args.target_mapping,
        stage=args.stage, start_year=1990, end_year=CALIB_END_YEAR,
    )
    targets_by_output = _make_targets_by_output(targets)

    n = len(manifest_rows)
    print(f"Running {n} accepted candidates deterministically ...")
    all_outputs: list[dict] = []
    for idx, (rank, config_path) in enumerate(manifest_rows, start=1):
        print(
            f"  [{idx:3d}/{n}] rank {rank}: {Path(config_path).name} ...",
            end=" ", flush=True,
        )
        outputs = _run_candidate(config_path, seed=args.seed + idx)
        all_outputs.append(outputs)
        print("done")

    # Timeseries CSV
    ts_rows = _build_timeseries_rows(all_outputs, manifest_rows)
    ts_df = pd.DataFrame(ts_rows)
    ts_path = output_dir / "accepted_ensemble_fit_timeseries.csv"
    ts_df.to_csv(ts_path, index=False)
    print(f"Timeseries: {ts_path}")

    # Summary CSV
    summary_rows = _build_summary_rows(all_outputs, targets_by_output)
    summary_df = pd.DataFrame(summary_rows)
    summary_path = output_dir / "accepted_ensemble_fit_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"Summary:    {summary_path}")

    if not args.skip_plots:
        print("Generating figures ...")
        present_outputs = set(summary_df["output"].unique())
        for output_name, filename, title in PLOT_CONFIGS:
            if output_name in present_outputs:
                _plot_output(
                    output_name, title, summary_df, targets_by_output,
                    figures_dir / filename, n,
                )
        _plot_combined_panel(
            summary_df, targets_by_output,
            figures_dir / "ensemble_fit_combined_panel.png", n,
        )
    else:
        print("Skipping plots (--skip-plots).")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
