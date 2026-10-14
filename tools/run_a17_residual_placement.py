#!/usr/bin/env python3
"""Run A-17 Step 1: locate the deterministic/stochastic level gap from 1975.

The task brief makes later allocator experiments conditional on this result.
If PLHIV is already outside the stochastic replicate range in the 1975 seed
year or in 1976, this runner records the falsification and stops before Steps
2--4, as required by the brief.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.a17_residual_placement import (  # noqa: E402
    METRICS,
    compare_annual_levels,
    step1_falsifies_compounding,
    timing_summary,
)

START_YEAR = 1975
END_YEAR = 2025
DEFAULT_SEEDS = tuple(range(7001, 7051))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def parse_seeds(text: str) -> list[int]:
    seeds: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if "-" in token:
            start, end = (int(value) for value in token.split("-", 1))
            seeds.extend(range(start, end + 1))
        elif token:
            seeds.append(int(token))
    if len(seeds) != 50 or len(set(seeds)) != 50:
        raise argparse.ArgumentTypeError("A-17 Step 1 requires 50 distinct seeds")
    return seeds


def configured(base: dict, *, deterministic: bool, seed: int) -> dict:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"]["value"] = deterministic
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def run_annual(base: dict, *, deterministic: bool, seed: int, run_number: int, log) -> dict:
    config = configured(base, deterministic=deterministic, seed=seed)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        result = SimulationEngineV3(config, seed=seed).run(run_number=run_number)
    mode = "deterministic" if deterministic else "stochastic"
    log.write(f"===== {mode} seed {seed} =====\n{buffer.getvalue()}")
    return extract_annual_outputs(result, start_year=START_YEAR, end_year=END_YEAR)


def annual_rows(annual: dict, *, seed: int, deterministic: bool) -> list[dict]:
    rows: list[dict] = []
    expected_years = set(range(START_YEAR, END_YEAR + 1))
    for metric in METRICS:
        if metric not in annual or set(annual[metric]) != expected_years:
            raise AssertionError(f"incomplete A-17 annual extraction for {metric}")
        for year in range(START_YEAR, END_YEAR + 1):
            rows.append(
                {
                    "mode": "deterministic" if deterministic else "stochastic",
                    "seed": int(seed),
                    "metric": metric,
                    "year": year,
                    "value": float(annual[metric][year]),
                }
            )
    return rows


def write_hashes(output_dir: Path) -> dict[str, str]:
    paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    return {str(path.relative_to(output_dir)): sha256(path) for path in paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=list(DEFAULT_SEEDS))
    args = parser.parse_args()

    config_path = args.model_config.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir()
    provenance_dir.mkdir()

    status = git_text("status", "--short")
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    (provenance_dir / "git_status_short.txt").write_text(
        status + ("\n" if status else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(diff)

    command = shlex.join([sys.executable, *sys.argv])
    manifest = {
        "task": "A-17 residual placement rule",
        "stage": "Step 1 timing/falsification gate",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_config": str(config_path),
        "seeds": list(args.seeds),
        "n_stochastic_replicates": len(args.seeds),
        "deterministic_seed": int(args.seeds[0]),
        "extraction_window": [START_YEAR, END_YEAR],
        "metrics": list(METRICS),
        "pre_specification": {
            "step1_stop_rule": (
                "Falsify the growth-phase compounding hypothesis and stop if "
                "year-end PLHIV is outside the n=50 stochastic replicate min-max "
                "in 1975 (seed year) or 1976 (first full post-seed year)."
            ),
            "step4_success_if_reached": (
                "Median absolute log ratio across scored years below half baseline "
                "and within the stochastic ensemble range in a majority of years."
            ),
            "candidate_selection_if_reached": (
                "Demonstrated unbiasedness in allocator and synthetic-process tests, "
                "not epidemic or calibration fit."
            ),
        },
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            "model_config": sha256(config_path),
            "run_v3.py": sha256(ROOT / "run_v3.py"),
            "engine_v3.py": sha256(ROOT / "engine_v3.py"),
            "calib_extractor.py": sha256(ROOT / "calib_extractor.py"),
            "stochastic_allocation.py": sha256(ROOT / "stochastic_allocation.py"),
            "counter_based_rng.py": sha256(ROOT / "counter_based_rng.py"),
            "tools/a17_residual_placement.py": sha256(
                ROOT / "tools/a17_residual_placement.py"
            ),
            "tools/run_a17_residual_placement.py": sha256(Path(__file__).resolve()),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    stochastic_rows: list[dict] = []
    timing_rows: list[dict] = []
    started = time.time()
    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        for index, seed in enumerate(args.seeds, start=1):
            run_started = time.time()
            annual = run_annual(
                base, deterministic=False, seed=seed, run_number=index, log=log
            )
            stochastic_rows.extend(
                annual_rows(annual, seed=seed, deterministic=False)
            )
            timing_rows.append(
                {
                    "mode": "stochastic",
                    "seed": seed,
                    "elapsed_seconds": time.time() - run_started,
                }
            )
            print(f"A-17 Step 1 stochastic {index}/50 seed={seed}", flush=True)

        run_started = time.time()
        det_seed = int(args.seeds[0])
        det_annual = run_annual(
            base, deterministic=True, seed=det_seed, run_number=51, log=log
        )
        deterministic_rows = annual_rows(
            det_annual, seed=det_seed, deterministic=True
        )
        timing_rows.append(
            {
                "mode": "deterministic",
                "seed": det_seed,
                "elapsed_seconds": time.time() - run_started,
            }
        )

    stochastic = pd.DataFrame(stochastic_rows)
    deterministic = pd.DataFrame(deterministic_rows)
    comparison = compare_annual_levels(deterministic, stochastic)
    summary = timing_summary(comparison)
    falsified = step1_falsifies_compounding(comparison)

    expected_rows = len(METRICS) * (END_YEAR - START_YEAR + 1)
    if len(comparison) != expected_rows:
        raise AssertionError(f"expected {expected_rows} comparison rows")
    if not (comparison["n_replicates"] == 50).all():
        raise AssertionError("at least one metric/year lacks all 50 replicates")

    stochastic.to_csv(raw_dir / "step1_stochastic_replicates_1975_2025.csv", index=False)
    deterministic.to_csv(raw_dir / "step1_deterministic_1975_2025.csv", index=False)
    comparison.to_csv(raw_dir / "step1_yearly_log_ratio.csv", index=False)
    summary.to_csv(raw_dir / "step1_timing_summary.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(raw_dir / "run_timings.csv", index=False)
    findings = {
        "step1_falsifies_growth_phase_compounding": falsified,
        "stop_after_step1": falsified,
        "plhiv_1975_or_1976_outside_stochastic_range": falsified,
        "later_steps_run": False,
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )

    manifest.update(
        {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.time() - started,
            "result": findings,
            "counts": {
                "stochastic_rows": len(stochastic),
                "deterministic_rows": len(deterministic),
                "comparison_rows": len(comparison),
            },
        }
    )
    manifest["output_sha256"] = write_hashes(output_dir)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        str(path.relative_to(output_dir)): sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    print(f"A-17 Step 1 artifacts written to {output_dir}")
    print(f"A-17 Step 1 falsification gate: {'FIRED' if falsified else 'not fired'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

