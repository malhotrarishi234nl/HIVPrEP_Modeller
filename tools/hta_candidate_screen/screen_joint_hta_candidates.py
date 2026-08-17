#!/usr/bin/env python3
"""Fast HTA forward screen for saved joint LHS candidate configs.

This script does not modify LHS outputs. It creates a base-case-only HTA runtime
config in the screening output directory, runs no-PrEP vs oral-PrEP for a fixed
shortlist of saved joint candidate JSONs, and writes a compact CSV summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Candidate:
    branch: str
    rank: int

    @property
    def label(self) -> str:
        return f"{self.branch}_rank{self.rank:03d}"


DEFAULT_CANDIDATES = [
    Candidate("branch_001", 1),
    Candidate("branch_002", 2),
    Candidate("branch_005", 1),
    Candidate("branch_004", 2),
    Candidate("branch_002", 3),
    Candidate("branch_001", 5),
    Candidate("branch_004", 6),
    Candidate("branch_001", 6),
    Candidate("branch_001", 7),
    Candidate("branch_003", 3),
    Candidate("branch_003", 4),
    Candidate("branch_005", 5),
    Candidate("branch_003", 6),
    Candidate("branch_005", 9),
]


def _single_ranked_csv(branch_dir: Path) -> Path:
    matches = sorted(branch_dir.glob("lhs_ranked_results_*.csv"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one ranked CSV in {branch_dir}, found {len(matches)}")
    return matches[0]


def _candidate_config(joint_dir: Path, candidate: Candidate) -> Path:
    path = joint_dir / candidate.branch / f"lhs_best_fit_rank_{candidate.rank:03d}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _rank_row(joint_dir: Path, candidate: Candidate) -> dict[str, Any]:
    csv_path = _single_ranked_csv(joint_dir / candidate.branch)
    df = pd.read_csv(csv_path)
    row = df.iloc[candidate.rank - 1].to_dict()
    row["ranked_csv"] = str(csv_path)
    return row


def _write_screen_runtime_config(base_runtime: Path, output_dir: Path) -> Path:
    with base_runtime.open() as f:
        cfg = json.load(f)

    cfg["comparison_selection"]["primary_comparison_rule"] = "allow_none_for_screen"
    cfg["scenarios"] = {}
    outputs = cfg.setdefault("outputs", {})
    outputs.setdefault("excel_report", {})["enabled"] = False
    csv_outputs = outputs.setdefault("csv_outputs", {})
    for key in list(csv_outputs):
        csv_outputs[key] = key in {"arm_summary", "comparison_table", "scenario_register"}
    outputs.setdefault("csv_files", {})["enabled"] = True
    outputs.setdefault("transparency_tabs", {})["enabled"] = False
    outputs.setdefault("baseline_full_tab", {})["enabled"] = False
    outputs.setdefault("scenario_full_data_tabs", {})["enabled"] = False

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "hta_runtime_base_case_screen.json"
    with path.open("w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    return path


def _run_candidate(config_path: Path, runtime_config: Path, outdir: Path, force: bool) -> None:
    arm_summary = outdir / "arm_summary.csv"
    if arm_summary.exists() and not force:
        return

    outdir.mkdir(parents=True, exist_ok=True)
    log_path = outdir / "screen_run.log"
    cmd = [
        sys.executable,
        "run_scenarios.py",
        str(config_path),
        "--hta",
        str(runtime_config),
        "--output_dir",
        str(outdir),
    ]
    with log_path.open("w") as log:
        proc = subprocess.run(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"{config_path} failed with exit code {proc.returncode}; see {log_path}")


def _arm_value(df: pd.DataFrame, arm: str, column: str) -> float:
    rows = df[(df["scenario"] == "base_case_context") & (df["arm"] == arm)]
    if rows.empty:
        return float("nan")
    return float(rows.iloc[0][column])


def _summarize_candidate(joint_dir: Path, output_root: Path, candidate: Candidate) -> dict[str, Any]:
    row = _rank_row(joint_dir, candidate)
    outdir = output_root / candidate.label
    arm_summary = pd.read_csv(outdir / "arm_summary.csv")

    no_prep_inf = _arm_value(arm_summary, "no_prep", "cumul_new_infections")
    oral_inf = _arm_value(arm_summary, "oral_prep", "cumul_new_infections")
    no_prep_qaly = _arm_value(arm_summary, "no_prep", "total_qalys_disc")
    oral_qaly = _arm_value(arm_summary, "oral_prep", "total_qalys_disc")
    no_prep_cost = _arm_value(arm_summary, "no_prep", "total_costs_disc_societal")
    oral_cost = _arm_value(arm_summary, "oral_prep", "total_costs_disc_societal")

    infections_averted = no_prep_inf - oral_inf
    delta_qaly = oral_qaly - no_prep_qaly
    delta_cost = oral_cost - no_prep_cost
    icer = delta_cost / delta_qaly if delta_qaly > 0 else float("nan")
    recent_balance = (
        abs(float(row["recent_fit_new_infections_2023_z"]))
        + abs(float(row["recent_fit_PLHIV_2023_z"]))
        + abs(float(row["recent_fit_new_diagnoses_2024_observed_fraction"]) - 1.0)
        + abs(float(row["recent_fit_new_diagnoses_msm_2024_observed_fraction"]) - 1.0)
    )
    forward_pass = (
        no_prep_inf > oral_inf
        and oral_inf <= 7500
        and no_prep_inf <= 15000
    )

    return {
        "candidate": candidate.label,
        "branch": candidate.branch,
        "rank": candidate.rank,
        "draw_id": int(row["draw_id"]),
        "total_ll": float(row["total_ll"]),
        "recent_balance_score": recent_balance,
        "diag_2024": float(row["recent_fit_new_diagnoses_2024_model"]),
        "diag_2024_fraction": float(row["recent_fit_new_diagnoses_2024_observed_fraction"]),
        "msm_diag_2024": float(row["recent_fit_new_diagnoses_msm_2024_model"]),
        "msm_diag_2024_fraction": float(row["recent_fit_new_diagnoses_msm_2024_observed_fraction"]),
        "infections_2023": float(row["recent_fit_new_infections_2023_model"]),
        "infections_2023_z": float(row["recent_fit_new_infections_2023_z"]),
        "plhiv_2023": float(row["recent_fit_PLHIV_2023_model"]),
        "plhiv_2023_z": float(row["recent_fit_PLHIV_2023_z"]),
        "epsilon_msm": float(row["epsilon_msm"]),
        "contact_high": float(row["contact_high"]),
        "hta_no_prep_infections": no_prep_inf,
        "hta_oral_prep_infections": oral_inf,
        "hta_infections_averted": infections_averted,
        "hta_delta_qaly": delta_qaly,
        "hta_delta_cost_societal": delta_cost,
        "hta_icer_societal": icer,
        "forward_screen_pass": forward_pass,
        "output_dir": str(output_root / candidate.label),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--joint-dir",
        default="outputs/calibration/lhs_pipeline_v13_5_recent_fit/lhs_joint",
    )
    parser.add_argument("--runtime-config", default="configs/hta_runtime_config.json")
    parser.add_argument("--output-dir", default="outputs/hta_candidate_screen_14")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    joint_dir = Path(args.joint_dir)
    output_root = Path(args.output_dir)
    runtime_config = _write_screen_runtime_config(Path(args.runtime_config), output_root)

    rows: list[dict[str, Any]] = []
    for i, candidate in enumerate(DEFAULT_CANDIDATES, start=1):
        config_path = _candidate_config(joint_dir, candidate)
        outdir = output_root / candidate.label
        print(f"[{i}/{len(DEFAULT_CANDIDATES)}] screening {candidate.label}", flush=True)
        _run_candidate(config_path, runtime_config, outdir, args.force)
        rows.append(_summarize_candidate(joint_dir, output_root, candidate))

    rows.sort(
        key=lambda r: (
            not r["forward_screen_pass"],
            -r["total_ll"],
            r["hta_oral_prep_infections"],
            r["recent_balance_score"],
        )
    )
    summary_path = output_root / "candidate_screen_summary.csv"
    with summary_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {summary_path}")
    print(pd.DataFrame(rows).head(14)[[
        "candidate",
        "total_ll",
        "recent_balance_score",
        "hta_no_prep_infections",
        "hta_oral_prep_infections",
        "hta_infections_averted",
        "forward_screen_pass",
    ]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
