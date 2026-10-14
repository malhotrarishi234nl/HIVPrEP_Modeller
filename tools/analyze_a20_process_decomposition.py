#!/usr/bin/env python3
"""Consolidate A-20 Step 1/2 evidence into reproducible tables."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.a20_process_decomposition import PROCESS_FAMILIES  # noqa: E402
from tools.run_a20_process_decomposition import (  # noqa: E402
    annual_digest,
    inventory_sites,
    run_annual,
    sha256,
)
from tools.run_a20_step2_localisation import run_one  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--step1-dir", type=Path, required=True)
    parser.add_argument("--step2-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    config_path = args.model_config.resolve()
    step1_dir = args.step1_dir.resolve()
    step2_dir = args.step2_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir()

    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.rstrip()
    manifest = {
        "task": "A-20 consolidated analysis",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip(),
        "git_dirty": bool(status),
        "input_sha256": {
            "model_config": sha256(config_path),
            "step1_summary": sha256(step1_dir / "raw/step1_arm_summary.csv"),
            "step1_replicates": sha256(
                step1_dir / "raw/step1_arm_replicates_1975_2025.csv"
            ),
            "step2_states": sha256(step2_dir / "raw/state_marginals_by_seed.csv"),
            "switch": sha256(ROOT / "tools/a20_process_decomposition.py"),
            "runner": sha256(Path(__file__).resolve()),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        det_state_rows, det_annual = run_one(
            base,
            arm="deterministic_baseline",
            seed=7001,
            forced=frozenset(),
            deterministic=True,
            log=log,
        )
        no_annual, _, _, no_surface = run_annual(
            base,
            deterministic=True,
            seed=7001,
            forced=frozenset(),
            run_number=9001,
            log=log,
        )
        all_annual, _, _, all_surface = run_annual(
            base,
            deterministic=True,
            seed=7001,
            forced=PROCESS_FAMILIES,
            run_number=9002,
            log=log,
        )

    # The two instrumented controls must still equal the Step-1 gate hashes.
    gate = json.loads((step1_dir / "raw/step0_equivalence_control.json").read_text())
    control = {
        "deterministic_digest": annual_digest(no_annual),
        "expected_deterministic_digest": gate["production_deterministic_sha256"],
        "all_forced_digest": annual_digest(all_annual),
        "expected_stochastic_digest": gate["production_stochastic_seed_7001_sha256"],
    }
    control["both_match"] = (
        control["deterministic_digest"] == control["expected_deterministic_digest"]
        and control["all_forced_digest"] == control["expected_stochastic_digest"]
    )
    if not control["both_match"]:
        raise AssertionError("post-analysis switch instrumentation changed a control corner")
    (raw_dir / "analysis_equivalence_control.json").write_text(
        json.dumps(control, indent=2) + "\n", encoding="utf-8"
    )

    sites = inventory_sites()
    for row in sites:
        if row["function"] == "apply_mortality":
            key = f"{row['module']}:{row['function']}:{row['line']}"
        else:
            key = f"{row['module']}:{row['function']}"
        row["exercised_calls_per_run"] = int(max(no_surface[key], all_surface[key]))
        row["config_status"] = (
            "live" if row["exercised_calls_per_run"] else "dormant"
        )
    inventory = pd.DataFrame(sites)
    inventory.to_csv(raw_dir / "switch_surface_inventory.csv", index=False)

    step1_det = pd.read_csv(step1_dir / "raw/deterministic_baseline_1975_2025.csv")
    det_frame = pd.DataFrame(
        [
            {
                "metric": metric,
                "year": year,
                "value": value,
            }
            for metric, yearly in det_annual.items()
            for year, value in yearly.items()
            if metric in step1_det["metric"].unique()
        ]
    )
    det_check = det_frame.merge(
        step1_det, on=["metric", "year"], suffixes=("_analysis", "_step1")
    )
    det_reproduction = {
        "n_rows": int(len(det_check)),
        "max_absolute_difference": float(
            (det_check["value_analysis"] - det_check["value_step1"]).abs().max()
        ),
    }
    if det_reproduction["max_absolute_difference"] != 0.0:
        raise AssertionError("analysis deterministic trace does not reproduce Step 1")
    (raw_dir / "deterministic_trace_reproduction.json").write_text(
        json.dumps(det_reproduction, indent=2) + "\n", encoding="utf-8"
    )

    # Time profile against both reference trajectories.
    reps = pd.read_csv(step1_dir / "raw/step1_arm_replicates_1975_2025.csv")
    ref = pd.read_csv(step1_dir / "raw/stochastic_reference_1975_2025.csv")
    winner = (
        reps[reps["arm"] == "background_mortality"]
        .groupby(["metric", "year"], as_index=False)["value"].mean()
        .rename(columns={"value": "background_mortality_mean"})
    )
    stochastic = (
        ref.groupby(["metric", "year"], as_index=False)["value"].mean()
        .rename(columns={"value": "stochastic_mean"})
    )
    timeline = (
        step1_det[["metric", "year", "value"]]
        .rename(columns={"value": "deterministic"})
        .merge(winner, on=["metric", "year"])
        .merge(stochastic, on=["metric", "year"])
    )
    valid = (timeline["deterministic"] > 0) & (timeline["stochastic_mean"] > 0)
    timeline["deterministic_log_ratio"] = np.where(
        valid, np.log(timeline["deterministic"] / timeline["stochastic_mean"]), np.nan
    )
    timeline["winner_log_ratio"] = np.where(
        valid,
        np.log(timeline["background_mortality_mean"] / timeline["stochastic_mean"]),
        np.nan,
    )
    timeline["absolute_log_ratio_displacement"] = (
        timeline["deterministic_log_ratio"].abs()
        - timeline["winner_log_ratio"].abs()
    )
    timeline["winner_move_vs_deterministic_pct"] = 100.0 * (
        timeline["background_mortality_mean"] / timeline["deterministic"] - 1.0
    )
    timeline.to_csv(raw_dir / "winner_time_profile_1975_2025.csv", index=False)

    scored = pd.read_csv(step1_dir / "raw/step1_scored_target_years.csv")
    metric_summary = (
        scored[scored["arm"] == "background_mortality"]
        .groupby("metric", as_index=False)
        .agg(
            n_target_years=("year", "size"),
            median_abs_log_ratio=("log_ratio_det_over_stoch_mean", lambda x: x.abs().median()),
            median_pct_difference=("pct_difference", "median"),
            n_inside_stochastic_range=("det_inside_stochastic_range", "sum"),
        )
    )
    metric_summary.to_csv(raw_dir / "winner_metric_summary.csv", index=False)

    # State-space effect: winning-arm mean minus deterministic and stochastic.
    states = pd.read_csv(step2_dir / "raw/state_marginals_by_seed.csv")
    states = pd.concat([states, pd.DataFrame(det_state_rows)], ignore_index=True)
    states.to_csv(raw_dir / "state_marginals_with_deterministic.csv", index=False)
    means = states.groupby(["arm", "year", "axis", "stratum"], as_index=False)[
        "plhiv"
    ].mean()
    wide = means.pivot(
        index=["year", "axis", "stratum"], columns="arm", values="plhiv"
    ).reset_index()
    wide["winner_minus_deterministic"] = (
        wide["background_mortality"] - wide["deterministic_baseline"]
    )
    wide["winner_minus_stochastic"] = (
        wide["background_mortality"] - wide["stochastic_reference"]
    )
    effects = (
        wide.groupby(["year", "axis"])["winner_minus_deterministic"]
        .sum().rename("total_winner_minus_deterministic").reset_index()
    )
    wide = wide.merge(effects, on=["year", "axis"])
    with np.errstate(divide="ignore", invalid="ignore"):
        wide["share_of_arm_effect"] = np.where(
            wide["total_winner_minus_deterministic"] != 0,
            wide["winner_minus_deterministic"]
            / wide["total_winner_minus_deterministic"],
            np.nan,
        )
    wide.to_csv(raw_dir / "state_space_effect.csv", index=False)

    summary = pd.read_csv(step1_dir / "raw/step1_arm_summary.csv")
    winner_row = summary[summary["arm"] == "background_mortality"].iloc[0]
    plhiv = timeline[timeline["metric"] == "PLHIV"].sort_values("year")
    selected_years = [1975, 1990, 1995, 2000, 2010, 2015, 2020, 2025]
    top_state = {}
    for year in selected_years:
        top_state[str(year)] = {}
        for axis in sorted(wide["axis"].unique()):
            part = wide[(wide["year"] == year) & (wide["axis"] == axis)].copy()
            part = part.reindex(
                part["winner_minus_deterministic"].abs().sort_values(ascending=False).index
            )
            top_state[str(year)][axis] = [
                {
                    "stratum": row.stratum,
                    "winner_minus_deterministic": float(row.winner_minus_deterministic),
                    "share_of_arm_effect": float(row.share_of_arm_effect),
                }
                for row in part.head(5).itertuples()
            ]

    findings = {
        "winner": "background_mortality",
        "median_abs_log_ratio": float(winner_row.median_abs_log_ratio_vs_stochastic),
        "displacement": float(winner_row.displacement_from_baseline),
        "fraction_of_baseline_recovered": float(
            winner_row.displacement_from_baseline / 0.1883772284060879
        ),
        "median_pct_difference_vs_stochastic": float(
            winner_row.median_pct_difference_vs_stochastic
        ),
        "median_abs_pct_move_vs_deterministic": float(
            winner_row.median_abs_pct_move_vs_deterministic
        ),
        "time_profile": {
            str(year): {
                "deterministic_log_ratio": float(
                    plhiv.loc[plhiv["year"] == year, "deterministic_log_ratio"].iloc[0]
                ),
                "winner_log_ratio": float(
                    plhiv.loc[plhiv["year"] == year, "winner_log_ratio"].iloc[0]
                ),
                "winner_move_vs_deterministic_pct": float(
                    plhiv.loc[
                        plhiv["year"] == year, "winner_move_vs_deterministic_pct"
                    ].iloc[0]
                ),
            }
            for year in selected_years
        },
        "first_nonzero_plhiv_effect_year": int(
            plhiv.loc[
                plhiv["background_mortality_mean"] != plhiv["deterministic"], "year"
            ].iloc[0]
        ),
        "top_state_effects": top_state,
    }
    (raw_dir / "analysis_findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "complete"
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        str(path.relative_to(output_dir)): sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    }
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {path}\n" for path, digest in hashes.items()),
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in findings.items() if k != "top_state_effects"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
