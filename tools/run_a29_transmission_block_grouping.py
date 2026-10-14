#!/usr/bin/env python3
"""Run A-29's deterministic transmission block-grouping comparison."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import platform
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.postchange_baseline import annual_digest  # noqa: E402
from transmission_corrected import TransmissionEngine  # noqa: E402


START_YEAR = 1975
END_YEAR = 2025
DETERMINISTIC_SEED = 7001
STOCHASTIC_SEEDS = (42, 43, 44)
LEVELS = (0, 1, 2, 3, 4, 5)
LEVEL_LABELS = {
    0: "whole array (pre-A-29 production)",
    1: "orientation (A-29 production)",
    2: "orientation x activity",
    3: "orientation x activity x age",
    4: "orientation x activity x age x prep_state",
    5: "cellwise np.round, no allocator",
}
LEVEL_AXES = {
    0: (),
    1: (1,),
    2: (1, 2),
    3: (1, 2, 0),
    4: (1, 2, 0, 3),
}
OUTPUT_METRICS = (
    "cumulative_msm_infections",
    "cumulative_non_msm_infections",
    "cumulative_new_diagnoses_msm",
    "cumulative_new_diagnoses_non_msm",
    "final_total_plhiv",
)


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


def configured(base: dict[str, Any], seed: int, deterministic: bool) -> dict[str, Any]:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": bool(deterministic)}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": bool(deterministic)
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def block_keys(shape: tuple[int, ...], axes: tuple[int, ...]):
    for combination in product(*(range(shape[axis]) for axis in axes)):
        key = [slice(None)] * len(shape)
        for axis, index in zip(axes, combination):
            key[axis] = slice(index, index + 1)
        yield tuple(key)


def cellwise_allocate(expected: np.ndarray, capacity: np.ndarray) -> np.ndarray:
    expected_arr = np.asarray(expected, dtype=float)
    capacity_arr = np.asarray(capacity).astype(np.int64, copy=False)
    if expected_arr.shape != capacity_arr.shape:
        raise ValueError("expected and capacity shapes differ")
    capped = np.minimum(expected_arr, capacity_arr.astype(float))
    return np.minimum(np.round(capped).astype(np.int64), capacity_arr)


@dataclass
class AllocationRecorder:
    level: int
    calls: list[dict[str, Any]] = field(default_factory=list)
    orientation_expected: dict[int, float] = field(default_factory=dict)
    orientation_allocated: dict[int, int] = field(default_factory=dict)

    def record(
        self,
        expected: np.ndarray,
        capacity: np.ndarray,
        allocated: np.ndarray,
    ) -> None:
        expected_arr = np.asarray(expected, dtype=float)
        capacity_arr = np.asarray(capacity).astype(np.int64, copy=False)
        allocated_arr = np.asarray(allocated, dtype=np.int64)
        capped = np.minimum(expected_arr, capacity_arr.astype(float))
        axes = tuple(range(expected_arr.ndim)) if self.level == 5 else LEVEL_AXES[self.level]
        max_block_error = 0
        target_total = 0
        n_blocks = 0
        for key in block_keys(expected_arr.shape, axes):
            if self.level == 5:
                target = int(np.minimum(
                    np.round(capped[key]).astype(np.int64), capacity_arr[key]
                ).sum())
            else:
                target = min(
                    int(np.round(capped[key].sum())), int(capacity_arr[key].sum())
                )
            observed = int(allocated_arr[key].sum())
            max_block_error = max(max_block_error, abs(observed - target))
            target_total += target
            n_blocks += 1

        over_capacity = int(np.maximum(allocated_arr - capacity_arr, 0).sum())
        negative = int((allocated_arr < 0).sum())
        if max_block_error or over_capacity or negative:
            raise AssertionError(
                f"level {self.level} allocation invariant failed: "
                f"block_error={max_block_error}, over_capacity={over_capacity}, "
                f"negative_cells={negative}"
            )

        call_index = len(self.calls)
        self.calls.append(
            {
                "level": self.level,
                "level_label": LEVEL_LABELS[self.level],
                "call_index": call_index,
                "n_blocks": n_blocks,
                "capped_expected_total": float(capped.sum()),
                "sum_block_targets": int(target_total),
                "allocated_total": int(allocated_arr.sum()),
                "max_abs_block_conservation_error": int(max_block_error),
                "over_capacity_events": over_capacity,
                "negative_cells": negative,
            }
        )
        for orient_idx in range(expected_arr.shape[1]):
            self.orientation_expected[orient_idx] = (
                self.orientation_expected.get(orient_idx, 0.0)
                + float(capped[:, orient_idx, :, :].sum())
            )
            self.orientation_allocated[orient_idx] = (
                self.orientation_allocated.get(orient_idx, 0)
                + int(allocated_arr[:, orient_idx, :, :].sum())
            )


class TransmissionGroupingPatch(contextlib.AbstractContextManager):
    """Temporarily select a diagnostic level and restore on every exit path."""

    def __init__(self, level: int, recorder: AllocationRecorder) -> None:
        if level not in LEVELS:
            raise ValueError(f"unknown A-29 level {level}")
        self.level = int(level)
        self.recorder = recorder
        self.original = TransmissionEngine._deterministic_allocate_expected_infections

    def __enter__(self):
        original = self.original
        level = self.level
        recorder = self.recorder

        def allocate(expected, capacity):
            if level == 5:
                result = cellwise_allocate(expected, capacity)
            else:
                result = original(expected, capacity, group_axes=LEVEL_AXES[level])
            recorder.record(expected, capacity, result)
            return result

        TransmissionEngine._deterministic_allocate_expected_infections = staticmethod(
            allocate
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        TransmissionEngine._deterministic_allocate_expected_infections = staticmethod(
            self.original
        )
        return False


def summarize(frame: pd.DataFrame) -> dict[str, float]:
    diagnoses_non_msm = frame["new_diagnoses"] - frame["new_diagnoses_msm"]
    return {
        "cumulative_msm_infections": float(frame["new_infections_msm"].sum()),
        "cumulative_non_msm_infections": float(
            frame["new_infections_non_msm"].sum()
        ),
        "cumulative_new_diagnoses_msm": float(frame["new_diagnoses_msm"].sum()),
        "cumulative_new_diagnoses_non_msm": float(diagnoses_non_msm.sum()),
        "final_total_plhiv": float(frame["adult_hiv_pos"].iloc[-1]),
    }


def run_model(config: dict[str, Any], seed: int) -> tuple[pd.DataFrame, dict]:
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        engine = SimulationEngineV3(config, seed=seed)
        result = engine.run(run_number=0)
    return result["summary"], extract_annual_outputs(result, START_YEAR, END_YEAR)


def decoupling_rows() -> list[dict[str, Any]]:
    rows = []
    capacity = np.ones((1, 2, 312, 1), dtype=np.int64)
    for level in (0, 1):
        for scale in (0, 1, 5, 25):
            expected = np.zeros(capacity.shape, dtype=float)
            expected[0, 0, :300, 0] = 0.0012 * scale
            expected[0, 1, 300:, 0] = 0.40
            allocated = TransmissionEngine._deterministic_allocate_expected_infections(
                expected, capacity, group_axes=LEVEL_AXES[level]
            )
            rows.append(
                {
                    "level": level,
                    "level_label": LEVEL_LABELS[level],
                    "heterosexual_scale": scale,
                    "heterosexual_expectation": float(expected[:, 0].sum()),
                    "heterosexual_allocated": int(allocated[:, 0].sum()),
                    "msm_allocated": int(allocated[:, 1].sum()),
                    "total_allocated": int(allocated.sum()),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--stochastic-seeds", type=int, nargs="*", default=list(STOCHASTIC_SEEDS)
    )
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    scripts_dir = output_dir / "scripts"
    raw_dir.mkdir()
    scripts_dir.mkdir()
    shutil.copy2(Path(__file__).resolve(), scripts_dir / Path(__file__).name)
    shutil.copy2(
        ROOT / "tests" / "test_a29_transmission_block_grouping.py",
        scripts_dir / "test_a29_transmission_block_grouping.py",
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    stored_baseline = json.loads(
        (ROOT / "tools" / "postchange_baseline.json").read_text(encoding="utf-8")
    )["baseline"]
    manifest = {
        "task": "A-29 deterministic transmission block grouping",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_status_short": git_text("status", "--short"),
        "config": str(config_path),
        "config_sha256": sha256(config_path),
        "deterministic_seed": DETERMINISTIC_SEED,
        "stochastic_seeds": list(args.stochastic_seeds),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "code_sha256": {
            name: sha256(ROOT / name)
            for name in (
                "transmission_corrected.py",
                "run_v3.py",
                "engine_v3.py",
                "stochastic_allocation.py",
                "exogenous_diagnoses.py",
                "tools/run_a29_transmission_block_grouping.py",
                "tests/test_a29_transmission_block_grouping.py",
            )
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    started = time.time()
    deterministic_rows = []
    call_rows = []
    orientation_rows = []
    level_digests = {}
    for level in LEVELS:
        recorder = AllocationRecorder(level)
        level_started = time.time()
        with TransmissionGroupingPatch(level, recorder):
            frame, annual = run_model(
                configured(base, DETERMINISTIC_SEED, True), DETERMINISTIC_SEED
            )
        elapsed = time.time() - level_started
        frame.to_csv(raw_dir / f"level_{level}_summary.csv", index=False)
        digest = annual_digest(annual)
        level_digests[str(level)] = digest
        row = {
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "elapsed_seconds": elapsed,
            "annual_digest": digest,
            **summarize(frame),
        }
        deterministic_rows.append(row)
        call_rows.extend(recorder.calls)
        for orient_idx, label in enumerate(engine_orientations(base)):
            expected = recorder.orientation_expected.get(orient_idx, 0.0)
            allocated = recorder.orientation_allocated.get(orient_idx, 0)
            orientation_rows.append(
                {
                    "level": level,
                    "level_label": LEVEL_LABELS[level],
                    "orientation_index": orient_idx,
                    "orientation": label,
                    "capped_expected_infections": expected,
                    "allocated_infections": allocated,
                    "allocated_minus_expected": allocated - expected,
                }
            )

    expected_level0 = stored_baseline["deterministic"]["digest"]
    if level_digests["0"] != expected_level0:
        raise AssertionError(
            f"level 0 failed frozen pre-A-29 control: {level_digests['0']} != "
            f"{expected_level0}"
        )
    if level_digests["1"] == level_digests["0"]:
        raise AssertionError("production orientation grouping made no model-output change")

    stochastic_rows = []
    for seed in args.stochastic_seeds:
        seed_started = time.time()
        frame, annual = run_model(configured(base, seed, False), seed)
        frame.to_csv(raw_dir / f"stochastic_seed_{seed}_summary.csv", index=False)
        digest = annual_digest(annual)
        stored = stored_baseline.get("stochastic", {}).get("per_seed", {}).get(str(seed))
        if stored is not None and digest != stored["digest"]:
            raise AssertionError(
                f"stochastic control drifted for seed {seed}: {digest} != {stored['digest']}"
            )
        stochastic_rows.append(
            {
                "mode": "stochastic",
                "seed": seed,
                "elapsed_seconds": time.time() - seed_started,
                "annual_digest": digest,
                **summarize(frame),
            }
        )

    deterministic = pd.DataFrame(deterministic_rows)
    stochastic = pd.DataFrame(stochastic_rows)
    baseline = deterministic.loc[deterministic["level"] == 0].iloc[0]
    deltas = []
    for _, row in deterministic.iterrows():
        delta = {"level": int(row["level"]), "level_label": row["level_label"]}
        for metric in OUTPUT_METRICS:
            delta[metric] = float(row[metric])
            delta[f"delta_vs_level0__{metric}"] = float(row[metric] - baseline[metric])
        deltas.append(delta)

    stochastic_summary = []
    for metric in OUTPUT_METRICS:
        values = stochastic[metric].astype(float)
        stochastic_summary.append(
            {
                "metric": metric,
                "n": int(len(values)),
                "mean": float(values.mean()),
                "sample_sd": float(values.std(ddof=1)) if len(values) > 1 else None,
                "level0": float(baseline[metric]),
                "level1": float(
                    deterministic.loc[deterministic["level"] == 1, metric].iloc[0]
                ),
                "level0_minus_stochastic_mean": float(baseline[metric] - values.mean()),
                "level1_minus_stochastic_mean": float(
                    deterministic.loc[deterministic["level"] == 1, metric].iloc[0]
                    - values.mean()
                ),
            }
        )

    deterministic.to_csv(output_dir / "level_summary.csv", index=False)
    pd.DataFrame(deltas).to_csv(output_dir / "downstream_deltas_vs_level0.csv", index=False)
    stochastic.to_csv(output_dir / "stochastic_runs.csv", index=False)
    pd.DataFrame(stochastic_summary).to_csv(
        output_dir / "deterministic_vs_stochastic.csv", index=False
    )
    pd.DataFrame(call_rows).to_csv(raw_dir / "allocation_call_conservation.csv", index=False)
    pd.DataFrame(orientation_rows).to_csv(
        raw_dir / "orientation_allocation_totals.csv", index=False
    )
    pd.DataFrame(decoupling_rows()).to_csv(
        raw_dir / "allocator_decoupling.csv", index=False
    )

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["elapsed_seconds"] = time.time() - started
    manifest["controls"] = {
        "level0_matches_frozen_pre_a29_digest": True,
        "level0_digest": level_digests["0"],
        "stochastic_digests_match_frozen_controls": True,
        "allocation_calls_with_conservation_violation": 0,
        "allocation_calls_with_capacity_violation": 0,
    }
    artifact_paths = [
        output_dir / "level_summary.csv",
        output_dir / "downstream_deltas_vs_level0.csv",
        output_dir / "stochastic_runs.csv",
        output_dir / "deterministic_vs_stochastic.csv",
        raw_dir / "allocation_call_conservation.csv",
        raw_dir / "orientation_allocation_totals.csv",
        raw_dir / "allocator_decoupling.csv",
    ]
    manifest["output_sha256"] = {
        str(path.relative_to(output_dir)): sha256(path) for path in artifact_paths
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-29 outputs written to {output_dir}")
    return 0


def engine_orientations(base: dict[str, Any]) -> list[str]:
    groups = base["transmission_model"]["groups"]
    orientations = groups["orientations"]
    if isinstance(orientations, dict) and "value" in orientations:
        orientations = orientations["value"]
    return [
        str(item["name"]["value"] if isinstance(item["name"], dict) else item["name"])
        for item in orientations
    ]


if __name__ == "__main__":
    raise SystemExit(main())
