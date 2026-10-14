#!/usr/bin/env python3
"""Localise A-9's first broken switch increment from retained A-7 traces."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SWITCHES = (10, 25, 50, 75, 100)


def cumulative_pair(
    frame: pd.DataFrame, low: int, high: int
) -> pd.DataFrame:
    pivot = frame.pivot(
        index=["step", "year"], columns="switch_percent", values="infections_averted"
    ).sort_index()
    result = pd.DataFrame(index=pivot.index)
    result["low_switch"] = low
    result["high_switch"] = high
    result["low_cumulative_infections_averted"] = pivot[low].cumsum()
    result["high_cumulative_infections_averted"] = pivot[high].cumsum()
    result["high_minus_low"] = (
        result["high_cumulative_infections_averted"]
        - result["low_cumulative_infections_averted"]
    )
    return result.reset_index()


def first_incorrect_crossover(path: pd.DataFrame) -> dict[str, Any] | None:
    broken = path[path["high_minus_low"] < 0]
    if broken.empty:
        return None
    row = broken.iloc[0]
    return {
        "step": int(row["step"]),
        "year": float(row["year"]),
        "high_minus_low": float(row["high_minus_low"]),
    }


def compartment_contrast(
    frame: pd.DataFrame, *, step: int, year: float, low: int, high: int
) -> pd.DataFrame:
    indexed = frame.set_index(["step", "year", "switch_percent"])
    low_row = indexed.loc[(step, year, low)]
    high_row = indexed.loc[(step, year, high)]
    columns = [column for column in frame if column.startswith("delta_")]
    rows = []
    for column in columns:
        low_value = float(low_row[column])
        high_value = float(high_row[column])
        rows.append(
            {
                "component": column.removeprefix("delta_"),
                "low_switch": low,
                "high_switch": high,
                "low_intervention_minus_reference": low_value,
                "high_intervention_minus_reference": high_value,
                "high_minus_low": high_value - low_value,
                "nonzero": high_value != low_value,
            }
        )
    return pd.DataFrame(rows).sort_values(
        "high_minus_low", key=lambda values: values.abs(), ascending=False
    )


def _winner_paths(runs_dir: Path, arm: str, low: int, high: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    cumulative = []
    compartments = []
    for seed in range(42, 57):
        run_dir = runs_dir / arm / f"seed_{seed}"
        path = cumulative_pair(
            pd.read_csv(run_dir / "timestep_qaly_deltas.csv"), low, high
        )
        path["seed"] = seed
        cumulative.append(path)
        simulation = pd.read_csv(run_dir / "timestep_simulation_deltas.csv")
        compartments.append((seed, simulation))
    return pd.concat(cumulative, ignore_index=True), compartments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.step1_dir.resolve()
    raw_dir = output_dir / "raw"
    runs_dir = output_dir / "runs"
    findings_path = raw_dir / "findings.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    winners = list(findings["repairing_process_arms"])
    if not winners:
        raise ValueError("Step 2 is not triggered when no process arm repairs the grid")

    grid = pd.read_csv(raw_dir / "grid_by_seed.csv")
    increments = pd.read_csv(raw_dir / "increment_resolution.csv")
    arm_summary = pd.read_csv(raw_dir / "arm_summary.csv")
    deterministic = grid[grid["arm"] == "all_deterministic"].sort_values("switch_percent")
    deterministic_values = deterministic.set_index("switch_percent")["infections_averted"]
    broken_pairs = [
        (low, high)
        for low, high in zip(SWITCHES, SWITCHES[1:])
        if float(deterministic_values[high]) < float(deterministic_values[low])
    ]
    if not broken_pairs:
        raise AssertionError("deterministic reference unexpectedly has no broken increment")
    low, high = broken_pairs[0]

    deterministic_run = runs_dir / "all_deterministic" / "seed_42"
    deterministic_path = cumulative_pair(
        pd.read_csv(deterministic_run / "timestep_qaly_deltas.csv"), low, high
    )
    deterministic_path.to_csv(
        raw_dir / "step2_deterministic_cumulative_first_broken_pair.csv", index=False
    )
    crossover = first_incorrect_crossover(deterministic_path)
    if crossover is None:
        raise AssertionError("endpoint is broken but cumulative trace never crosses below zero")
    compartments = compartment_contrast(
        pd.read_csv(deterministic_run / "timestep_simulation_deltas.csv"),
        step=crossover["step"], year=crossover["year"], low=low, high=high,
    )
    compartments.to_csv(
        raw_dir / "step2_deterministic_compartments_at_first_crossover.csv", index=False
    )

    endpoint_rows = []
    winner_path_rows = []
    winner_compartment_rows = []
    winner_details = []
    for arm in winners:
        arm_increment = increments[
            (increments["arm"] == arm)
            & (increments["metric"] == "infections_averted")
            & (increments["from_switch"] == low)
            & (increments["to_switch"] == high)
        ].iloc[0]
        endpoint_rows.append(
            {
                "arm": arm,
                "low_switch": low,
                "high_switch": high,
                "deterministic_increment": float(deterministic_values[high] - deterministic_values[low]),
                "arm_mean_increment": float(arm_increment["mean_increment"]),
                "arm_se_increment": float(arm_increment["se_increment"]),
                "arm_increment_over_se": float(arm_increment["increment_over_se"]),
                "fixes_broken_endpoint_pair": bool(arm_increment["exceeds_2se"]),
            }
        )
        paths, simulations = _winner_paths(runs_dir, arm, low, high)
        for (step, year), group in paths.groupby(["step", "year"]):
            values = group["high_minus_low"].to_numpy(dtype=float)
            winner_path_rows.append(
                {
                    "arm": arm,
                    "step": int(step),
                    "year": float(year),
                    "low_switch": low,
                    "high_switch": high,
                    "n": len(values),
                    "mean_high_minus_low": float(values.mean()),
                    "sd_high_minus_low": float(np.std(values, ddof=1)),
                    "se_high_minus_low": float(np.std(values, ddof=1) / math.sqrt(len(values))),
                    "min_high_minus_low": float(values.min()),
                    "max_high_minus_low": float(values.max()),
                }
            )
        winner_path = pd.DataFrame(
            [row for row in winner_path_rows if row["arm"] == arm]
        ).sort_values(["step", "year"])
        later = winner_path[
            (winner_path["step"] > crossover["step"])
            | (
                (winner_path["step"] == crossover["step"])
                & (winner_path["year"] >= crossover["year"])
            )
        ]
        permanent = None
        for index, row in later.iterrows():
            if (winner_path.loc[index:, "mean_high_minus_low"] >= 0).all():
                permanent = row
                break
        at_crossover = winner_path[
            (winner_path["step"] == crossover["step"])
            & (winner_path["year"] == crossover["year"])
        ].iloc[0]

        per_seed_compartments = []
        for seed, simulation in simulations:
            contrast = compartment_contrast(
                simulation, step=crossover["step"], year=crossover["year"], low=low, high=high
            )
            contrast["seed"] = seed
            per_seed_compartments.append(contrast)
        all_compartments = pd.concat(per_seed_compartments, ignore_index=True)
        for component, group in all_compartments.groupby("component"):
            values = group["high_minus_low"].to_numpy(dtype=float)
            winner_compartment_rows.append(
                {
                    "arm": arm,
                    "step": crossover["step"],
                    "year": crossover["year"],
                    "component": component,
                    "n": len(values),
                    "mean_high_minus_low": float(values.mean()),
                    "sd_high_minus_low": float(np.std(values, ddof=1)),
                    "se_high_minus_low": float(np.std(values, ddof=1) / math.sqrt(len(values))),
                }
            )
        winner_details.append(
            {
                "arm": arm,
                "mean_increment_at_deterministic_crossover": float(at_crossover["mean_high_minus_low"]),
                "se_at_deterministic_crossover": float(at_crossover["se_high_minus_low"]),
                "permanently_nonnegative_from_step": int(permanent["step"]) if permanent is not None else None,
                "permanently_nonnegative_from_year": float(permanent["year"]) if permanent is not None else None,
            }
        )

    pd.DataFrame(endpoint_rows).to_csv(raw_dir / "step2_endpoint_repair.csv", index=False)
    pd.DataFrame(winner_path_rows).to_csv(
        raw_dir / "step2_winner_cumulative_first_broken_pair.csv", index=False
    )
    pd.DataFrame(winner_compartment_rows).sort_values(
        "mean_high_minus_low", key=lambda values: values.abs(), ascending=False
    ).to_csv(raw_dir / "step2_winner_compartments_at_deterministic_crossover.csv", index=False)

    background = arm_summary[arm_summary["arm"] == "background_mortality"].copy()
    background.to_csv(raw_dir / "step2_a21_background_prior_test.csv", index=False)
    findings["status"] = "complete_inconsistent_localisation"
    findings["step2"] = {
        "first_broken_endpoint_pair": [low, high],
        "first_incorrect_crossover": crossover,
        "deterministic_endpoint_increment": float(deterministic_values[high] - deterministic_values[low]),
        "winner_details": winner_details,
        "a21_background_mortality_prior_repairs": bool(background.iloc[0]["infections_repairs"]),
        "a21_background_mortality_prior_monotone": bool(background.iloc[0]["infections_monotone"]),
        "a21_background_mortality_min_increment_over_se": float(
            background.iloc[0]["infections_min_increment_over_se"]
        ),
        "mechanism_conclusion": (
            "A-21's background-mortality mechanism does not meet the repair criterion. "
            "The HIV-mortality arm localises the infections endpoint but breaks the HIV-deaths grid, "
            "so no repair or same-mechanism attribution is supported."
        ),
    }
    findings_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = findings["status"]
    manifest["findings"] = findings
    manifest["step2_analysis"] = "tools/analyze_a9_switch_grid_step2.py"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(findings["step2"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
