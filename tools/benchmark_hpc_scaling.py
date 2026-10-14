#!/usr/bin/env python3
"""Benchmark calibration trajectory throughput across HPC process counts.

This is an engineering benchmark, not calibration evidence. It repeatedly runs
one frozen configuration on a separate seed namespace, discards model outputs,
and recommends the worker count with the highest measured throughput.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
import run_coupled_calibration as runner  # noqa: E402


ENGINEERING_SEED_BASE = 9_000_000


def _worker(payload: tuple[dict, int]) -> float:
    config, seed = payload
    started = time.perf_counter()
    runner.run_one_trajectory(config, int(seed), observe=False)
    return time.perf_counter() - started


def recommend(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("benchmark produced no rows")
    valid = [row for row in rows if row.get("status") == "COMPLETE"]
    if not valid:
        raise ValueError("every scaling point failed")
    best = max(valid, key=lambda row: (row["trajectories_per_second"], -row["workers"]))
    # Each adaptive calibration batch contains 64 candidate tasks. More than 64
    # workers per case cannot accelerate the 28 sequential batch barriers and
    # only adds process-startup/memory overhead. The full-machine recommendation
    # remains useful for the 24,000-task PSA phase.
    calibration_workers = min(64, max(1, int(best["workers"]) // 2))
    return {
        "recommended_total_workers": int(best["workers"]),
        "recommended_psa_total_workers": int(best["workers"]),
        "recommended_workers_per_parallel_calibration": calibration_workers,
        "selection_rule": "highest measured aggregate trajectories per second; ties prefer fewer workers",
        "calibration_cap_reason": (
            "64 candidates per adaptive batch; concurrency above 64 per case "
            "cannot shorten the sequential batch barriers"
        ),
    }


def benchmark(
    prepared_dir: Path,
    output_path: Path,
    worker_counts: Sequence[int],
    tasks_per_point: int,
) -> dict:
    prepared = runner.load_prepared(prepared_dir)
    first = prepared["initial"][0]
    space_json = json.loads(
        prepared["state"]["paths"]["calib_space"].read_text(encoding="utf-8")
    )
    config = runner.injected_config(
        prepared["state"]["base_config"], first["params"], space_json
    )
    available = os.cpu_count() or 1
    requested_counts = sorted(set(int(value) for value in worker_counts))
    if not requested_counts or any(value < 1 for value in requested_counts):
        raise ValueError(
            f"worker counts must be positive integers: {requested_counts}"
        )
    # A prepared suite can move between machines.  Ignore scaling points that
    # exceed the current host and always test the host's full detected capacity.
    # This keeps an H4D-authored plan safe on smaller cloud or bare-metal hosts.
    skipped_counts = [value for value in requested_counts if value > available]
    counts = sorted({value for value in requested_counts if value <= available} | {available})
    if tasks_per_point < max(counts):
        raise ValueError("tasks_per_point must be at least the largest worker count")

    # Pay import/cache/startup cost once before the recorded scaling points.
    warmup_seconds = _worker((config, ENGINEERING_SEED_BASE - 1))
    rows = []
    for point, workers in enumerate(counts):
        seeds = [
            ENGINEERING_SEED_BASE + point * tasks_per_point + offset
            for offset in range(tasks_per_point)
        ]
        started = time.perf_counter()
        durations = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker, (config, seed)) for seed in seeds]
            for future in as_completed(futures):
                durations.append(float(future.result()))
        elapsed = time.perf_counter() - started
        rows.append({
            "status": "COMPLETE",
            "workers": workers,
            "tasks": tasks_per_point,
            "elapsed_seconds": elapsed,
            "trajectories_per_second": tasks_per_point / elapsed,
            "mean_worker_trajectory_seconds": sum(durations) / len(durations),
            "min_worker_trajectory_seconds": min(durations),
            "max_worker_trajectory_seconds": max(durations),
        })
        print(
            f"HPC benchmark: {workers} workers -> {tasks_per_point / elapsed:.2f} "
            f"trajectories/s ({elapsed:.1f}s)",
            flush=True,
        )
    baseline = rows[0]
    for row in rows:
        row["speedup_vs_first_point"] = (
            row["trajectories_per_second"] / baseline["trajectories_per_second"]
        )
        row["parallel_efficiency_vs_first_point"] = (
            row["speedup_vs_first_point"]
            / (row["workers"] / baseline["workers"])
        )
    result = {
        "status": "HPC_SCALING_BENCHMARK_COMPLETE",
        "interpretation": (
            "Engineering throughput only. Repeated configurations and engineering seeds "
            "must not be pooled with calibration evidence."
        ),
        "prepared_dir": str(prepared_dir.resolve()),
        "prepared_manifest_sha256": ccs.sha256_file(prepared_dir / "manifest.json"),
        "candidate_id": int(first["candidate_id"]),
        "engineering_seed_base": ENGINEERING_SEED_BASE,
        "tasks_per_point": tasks_per_point,
        "warmup_seconds": warmup_seconds,
        "detected_cpus": available,
        "worker_counts_requested": requested_counts,
        "worker_counts_tested": counts,
        "worker_counts_skipped_above_detected_cpus": skipped_counts,
        "platform": platform.platform(),
        "points": rows,
        **recommend(rows),
        "simulations_started": 1 + tasks_per_point * len(rows),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    return result


def _counts(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-counts", default="32,64,96,128,160,192")
    parser.add_argument("--tasks-per-point", type=int, default=192)
    args = parser.parse_args(argv)
    result = benchmark(
        args.prepared, args.output, _counts(args.worker_counts), args.tasks_per_point
    )
    print(json.dumps({
        "status": result["status"],
        "recommended_total_workers": result["recommended_total_workers"],
        "recommended_psa_total_workers": result["recommended_psa_total_workers"],
        "recommended_workers_per_parallel_calibration": result[
            "recommended_workers_per_parallel_calibration"
        ],
        "simulations_started": result["simulations_started"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
