#!/usr/bin/env python3
"""Run full HTA for the short-listed saved joint LHS candidates."""

from __future__ import annotations

import argparse
import csv
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


SHORTLIST = [
    Candidate("branch_001", 1),
    Candidate("branch_005", 1),
    Candidate("branch_004", 2),
    Candidate("branch_002", 3),
    Candidate("branch_001", 5),
    Candidate("branch_003", 4),
]

EXISTING_FULL_OUTPUTS = {
    "branch_001_rank001": Path("outputs/hta_split_run_branch001_rank001_screen"),
}


def _candidate_config(joint_dir: Path, candidate: Candidate) -> Path:
    path = joint_dir / candidate.branch / f"lhs_best_fit_rank_{candidate.rank:03d}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _run_candidate(
    config_path: Path,
    runtime_config: Path,
    outdir: Path,
    force: bool,
) -> None:
    arm_summary = outdir / "arm_summary.csv"
    if arm_summary.exists() and not force:
        return

    outdir.mkdir(parents=True, exist_ok=True)
    log_path = outdir / "full_hta_run.log"
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


def _arm_value(df: pd.DataFrame, scenario: str, arm: str, column: str) -> float:
    rows = df[(df["scenario"] == scenario) & (df["arm"] == arm)]
    if rows.empty:
        return float("nan")
    return float(rows.iloc[0][column])


def _summarize(candidate: Candidate, outdir: Path, screen_row: dict[str, Any]) -> dict[str, Any]:
    arm_summary = pd.read_csv(outdir / "arm_summary.csv")
    comparison_path = outdir / "comparison_table.csv"
    comparisons = pd.read_csv(comparison_path) if comparison_path.exists() else pd.DataFrame()

    def comp_value(scenario: str, column: str) -> float:
        if comparisons.empty or column not in comparisons:
            return float("nan")
        rows = comparisons[comparisons["scenario"] == scenario]
        if rows.empty:
            return float("nan")
        return float(rows.iloc[0][column])

    return {
        "candidate": candidate.label,
        "draw_id": screen_row.get("draw_id", ""),
        "total_ll": screen_row.get("total_ll", ""),
        "recent_balance_score": screen_row.get("recent_balance_score", ""),
        "base_no_prep_inf": _arm_value(
            arm_summary, "base_case_context", "no_prep", "cumul_new_infections"
        ),
        "base_oral_prep_inf": _arm_value(
            arm_summary, "base_case_context", "oral_prep", "cumul_new_infections"
        ),
        "switch100_lena_inf": _arm_value(
            arm_summary,
            "scenario_a_lena_substitution__switch_100",
            "oral_prep_plus_lena",
            "cumul_new_infections",
        ),
        "addon3000_lena_inf": _arm_value(
            arm_summary,
            "scenario_b_lena_add_on__add_on_3000",
            "oral_prep_plus_lena",
            "cumul_new_infections",
        ),
        "oral_expansion_inf": _arm_value(
            arm_summary,
            "scenario_c_oral_expansion_benchmark",
            "oral_prep_expansion",
            "cumul_new_infections",
        ),
        "base_oral_vs_no_prep_icer": comp_value("base_case_context", "icer"),
        "addon3000_icer": comp_value("scenario_b_lena_add_on__add_on_3000", "icer"),
        "oral_expansion_icer": comp_value("scenario_c_oral_expansion_benchmark", "icer"),
        "output_dir": str(outdir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--joint-dir",
        default="outputs/calibration/lhs_pipeline_v13_5_recent_fit/lhs_joint",
    )
    parser.add_argument("--runtime-config", default="configs/hta_runtime_config.json")
    parser.add_argument("--screen-summary", default="outputs/hta_candidate_screen_14/candidate_screen_summary.csv")
    parser.add_argument("--output-dir", default="outputs/hta_candidate_full_shortlist")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    joint_dir = Path(args.joint_dir)
    runtime_config = Path(args.runtime_config)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    screen_rows = {
        row["candidate"]: row
        for row in csv.DictReader(Path(args.screen_summary).open())
    }

    summary_rows = []
    for i, candidate in enumerate(SHORTLIST, start=1):
        label = candidate.label
        outdir = EXISTING_FULL_OUTPUTS.get(label, output_root / label)
        print(f"[{i}/{len(SHORTLIST)}] full HTA {label}", flush=True)
        if label not in EXISTING_FULL_OUTPUTS:
            _run_candidate(
                _candidate_config(joint_dir, candidate),
                runtime_config,
                outdir,
                args.force,
            )
        summary_rows.append(_summarize(candidate, outdir, screen_rows.get(label, {})))

    summary_path = output_root / "full_hta_shortlist_summary.csv"
    with summary_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nWrote {summary_path}")
    print(pd.DataFrame(summary_rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
