"""A-14 follow-up: why do granularity levels 1, 2 and 3 coincide?

Levels 2 and 3 add the ART and PrEP semantic axes on top of level 1's HIV
stage.  The sweep found all three byte-identical.  A stratum axis can only
change an allocation if the array carries non-zero counts at more than one of
its index values: if every event-eligible person sits at a single index of that
axis, partitioning on it splits the array into one non-empty block and some
all-zero blocks, and `deterministic_allocate_events` returns zeros for an
all-zero block, so the concatenated result is unchanged.

This runner measures that occupancy directly, per call site and per semantic
axis, over every timestep of both arms, at level 1 (the trajectory levels 1-3
share).  Diagnostic only; no production behaviour changes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import platform
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_scenarios as run_scenarios_module  # noqa: E402
from tools.allocation_granularity import axes_for_process  # noqa: E402
from tools.run_a14_allocation_granularity_sweep import (  # noqa: E402
    ARM_ENGINES,
    A14AllocationEngine,
    sha256,
)
from tools.run_a4_common_random_numbers import BASE_SCENARIO, configure_runner  # noqa: E402
from tools.run_a12_deterministic_allocation_audit import select_scenario  # noqa: E402


OCCUPANCY: list[dict[str, Any]] = []


class OccupancyEngine(A14AllocationEngine):
    A14_LEVEL = 1

    def _draw_events_for(self, process_id, n, p):
        if self.deterministic and self._a14_is_arm_engine:
            population = np.asarray(n)
            axis_map = axes_for_process(process_id)
            for semantic, tensor_axis in axis_map.items():
                if tensor_axis is None:
                    OCCUPANCY.append(
                        {
                            "process_id": str(process_id),
                            "semantic_axis": semantic,
                            "tensor_axis": -1,
                            "axis_length": 1,
                            "occupied_indices": 1,
                            "pool": float(population.sum()),
                            "status": "collapsed_before_call",
                        }
                    )
                    continue
                moved = np.moveaxis(population, tensor_axis, 0)
                per_index = moved.reshape(moved.shape[0], -1).sum(axis=1)
                OCCUPANCY.append(
                    {
                        "process_id": str(process_id),
                        "semantic_axis": semantic,
                        "tensor_axis": tensor_axis,
                        "axis_length": int(moved.shape[0]),
                        "occupied_indices": int((per_index > 0).sum()),
                        "pool": float(population.sum()),
                        "status": "present_in_array",
                    }
                )
        return super()._draw_events_for(process_id, n, p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "runs" / "axis_occupancy_level1"
    run_dir.mkdir(parents=True, exist_ok=False)

    ARM_ENGINES.clear()
    OCCUPANCY.clear()
    run_scenarios_module.SimulationEngineV3 = OccupancyEngine

    runner = configure_runner(
        str(args.model_config.resolve()),
        str(args.runtime_config.resolve()),
        str(run_dir / "runner_outputs"),
        deterministic=True,
        seed=args.seed,
    )
    runner.hta_config["analysis_scenarios"] = select_scenario(
        runner.hta_config["analysis_scenarios"], BASE_SCENARIO
    )
    with (run_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            runner.run()

    frame = pd.DataFrame(OCCUPANCY)
    summary = (
        frame.groupby(["process_id", "semantic_axis"], as_index=False)
        .agg(
            calls=("pool", "size"),
            axis_length=("axis_length", "max"),
            max_occupied_indices=("occupied_indices", "max"),
            mean_occupied_indices=("occupied_indices", "mean"),
            total_pool=("pool", "sum"),
            status=("status", "first"),
        )
        .sort_values(["process_id", "semantic_axis"])
    )
    summary["can_change_allocation"] = summary["max_occupied_indices"] > 1
    summary.to_csv(output_dir / "axis_occupancy_audit.csv", index=False)

    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("follow_ups", {})["axis_occupancy_audit"] = {
        "command": shlex.join([sys.executable, *sys.argv]),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "diagnose why granularity levels 1, 2 and 3 coincide",
        "level_used_for_trajectory": 1,
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "code_sha256": {
            "tools/run_a14_axis_occupancy_audit.py": sha256(
                ROOT / "tools/run_a14_axis_occupancy_audit.py"
            )
        },
        "output_sha256": {
            "axis_occupancy_audit.csv": sha256(output_dir / "axis_occupancy_audit.csv")
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
