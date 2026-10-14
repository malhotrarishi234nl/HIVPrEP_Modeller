"""Run A-14: the deterministic allocation granularity sweep.

Diagnostic only.  No production behaviour changes: `run_v3.py`,
`stochastic_allocation.py` and every production call site are untouched.  The
runner substitutes an opt-in engine subclass *inside this process only*, whose
`_draw_events_for` routes deterministic draws through
`tools.allocation_granularity.allocate_for_process` at a declared granularity
level.  Level 0 delegates to the production allocator and is bit-identical.

Two properties the runner enforces rather than assumes:

  * the **frozen 2025 baseline is identical at every level** — the granularity
    level is applied only to arm engines (those constructed with an
    `initial_snapshot`), never to the pre-freeze baseline engine, so all five
    levels fork from the same state.  The snapshot checksum is recorded per
    level and compared.
  * an **unmapped call site is fatal**, not silently level 0.

Comparison reference: `outputs/hta_results/a4_common_random_numbers_20260910_170048/
stochastic_summary.json` (n=15, seeds 42-56).  No new stochastic runs are made.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_scenarios as run_scenarios_module  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.allocation_granularity import (  # noqa: E402
    LEVEL_LABELS,
    LEVELS,
    allocate_for_process,
    describe_levels,
    stratum_axes_for,
)
from tools.run_a4_common_random_numbers import (  # noqa: E402
    BASE_SCENARIO,
    DETERMINISTIC_REFERENCE,
    configure_runner,
)
from tools.run_a12_deterministic_allocation_audit import (  # noqa: E402
    select_scenario,
    summarize_base_case,
)

A4_STOCHASTIC_SUMMARY = (
    ROOT
    / "outputs/hta_results/a4_common_random_numbers_20260910_170048/stochastic_summary.json"
)

#: Pre-specified selection metrics (see report section 0.2).  `delta_qaly` is
#: reported alongside but is deliberately NOT in this list.
SELECTION_METRICS = (
    "infections_averted",
    "delta_hiv_deaths",
    "delta_background_deaths",
    "delta_undiscounted_person_years",
    "delta_discounted_person_years",
    "delta_final_population",
)
ACCEPTANCE_Z = 2.0

STAGE_NAMES = ("acute", "chronic", "aids")
#: Arm engines captured at construction, in `arm_order`.  Module-level so it
#: works under any multiprocessing start method.
ARM_ENGINES: list = []

STAGE_PROCESS_IDS = {
    "diagnosis_non_msm": "diagnosis",
    "diagnosis_msm": "diagnosis",
    "diagnosis_all_orientations": "diagnosis",
    "art_initiation": "art_initiation",
    "art_dropout": "art_dropout",
}


class A14AllocationEngine(SimulationEngineV3):
    """Engine whose deterministic allocation granularity is declared, not implied."""

    A14_LEVEL = 0

    def __init__(self, *args, **kwargs):
        # Only arm engines receive a restored frozen snapshot.  The pre-freeze
        # baseline engine must stay at production granularity so every level
        # forks from an identical 2025 state.
        is_arm_engine = kwargs.get("initial_snapshot") is not None
        super().__init__(*args, **kwargs)
        self._a14_level = int(type(self).A14_LEVEL) if is_arm_engine else 0
        self._a14_is_arm_engine = bool(is_arm_engine)
        self._a14_stage_totals: dict[str, dict[str, float]] = {}
        self._a14_axis_log: dict[str, str] = {}
        if is_arm_engine:
            # Arm engines are constructed in `arm_order`; the runner zips this
            # list against that order and asserts the lengths agree.
            ARM_ENGINES.append(self)

    def _draw_events_for(self, process_id, n: np.ndarray, p: float) -> np.ndarray:
        if not self.deterministic:
            # Stochastic mode is the reference, not the thing under test.
            return super()._draw_events_for(process_id, n, p)

        population = np.asarray(n)
        if self._a14_level == 0:
            events = super()._draw_events_for(process_id, n, p)
        else:
            axes = stratum_axes_for(process_id, self._a14_level, population.ndim)
            self._a14_axis_log[str(process_id)] = ",".join(str(a) for a in axes)
            events = allocate_for_process(process_id, population, p, self._a14_level)

        stage_process = STAGE_PROCESS_IDS.get(process_id)
        if stage_process is not None:
            event_array = np.asarray(events, dtype=np.int64)
            for stage_index, stage_name in enumerate(STAGE_NAMES):
                pool = int(np.take(population, stage_index, axis=3).sum())
                actual = int(np.take(event_array, stage_index, axis=3).sum())
                slug = f"{stage_process}_{stage_name}"
                record = self._a14_stage_totals.setdefault(
                    slug, {"pool": 0.0, "expected": 0.0, "actual": 0.0}
                )
                record["pool"] += pool
                record["expected"] += pool * float(p)
                record["actual"] += actual
        return events


def _make_engine_class(level: int) -> type:
    return type(f"A14AllocationEngineLevel{level}", (A14AllocationEngine,), {"A14_LEVEL": level})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def _run_level_worker(payload: tuple[str, str, str, int, int]) -> dict[str, Any]:
    model_config, runtime_config, run_dir_text, level, seed = payload
    ARM_ENGINES.clear()
    engine_class = _make_engine_class(level)
    run_scenarios_module.SimulationEngineV3 = engine_class

    run_dir = Path(run_dir_text)
    run_dir.mkdir(parents=True, exist_ok=False)
    runner = configure_runner(
        model_config,
        runtime_config,
        str(run_dir / "runner_outputs"),
        deterministic=True,
        seed=seed,
    )
    runner.hta_config["analysis_scenarios"] = select_scenario(
        runner.hta_config["analysis_scenarios"], BASE_SCENARIO
    )
    started = time.time()
    with (run_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            runner.run()
    elapsed = time.time() - started

    scenario = runner.hta_config["analysis_scenarios"][0]
    arm_rows, delta = summarize_base_case(scenario, runner.results[BASE_SCENARIO])

    arm_order = list(scenario["arm_order"])
    if len(ARM_ENGINES) != len(arm_order):
        raise AssertionError(
            f"A-14 captured {len(ARM_ENGINES)} arm engines for {len(arm_order)} arms"
        )
    stage_rows: list[dict[str, Any]] = []
    axis_log: dict[str, str] = {}
    for arm_name, engine in zip(arm_order, ARM_ENGINES):
        axis_log.update(engine._a14_axis_log)
        for slug, record in sorted(engine._a14_stage_totals.items()):
            stage_rows.append(
                {
                    "level": level,
                    "arm": arm_name,
                    "process_stage": slug,
                    "pool": record["pool"],
                    "expected": record["expected"],
                    "actual": record["actual"],
                    "actual_minus_expected": record["actual"] - record["expected"],
                }
            )
    snapshot = runner.snapshot_manager.baseline_snapshot
    snapshot_checksum = getattr(snapshot, "population_checksum", None)
    snapshot_total = int(snapshot.population.sum())

    delta["level"] = level
    delta["seed"] = seed
    delta["elapsed_seconds"] = elapsed
    for row in arm_rows:
        row["level"] = level
    return {
        "level": level,
        "delta": delta,
        "arm_rows": arm_rows,
        "stage_rows": stage_rows,
        "axis_log": axis_log,
        "frozen_snapshot_checksum": snapshot_checksum,
        "frozen_snapshot_total_population": snapshot_total,
    }


def stochastic_reference() -> dict[str, dict[str, float]]:
    payload = json.loads(A4_STOCHASTIC_SUMMARY.read_text(encoding="utf-8"))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--levels", type=str, default="0,1,2,3,4")
    args = parser.parse_args()

    levels = [int(token) for token in args.levels.split(",") if token.strip()]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    model_config = args.model_config.resolve()
    runtime_config = args.runtime_config.resolve()

    payloads = [
        (
            str(model_config),
            str(runtime_config),
            str(runs_dir / f"level_{level}"),
            level,
            args.seed,
        )
        for level in levels
    ]

    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(len(payloads), os.cpu_count() or 1)) as pool:
        for result in pool.map(_run_level_worker, payloads):
            results.append(result)
    results.sort(key=lambda row: row["level"])

    # Validity guard: every level must fork from an identical frozen 2025 state.
    checksums = {row["level"]: row["frozen_snapshot_checksum"] for row in results}
    totals = {row["level"]: row["frozen_snapshot_total_population"] for row in results}
    if len(set(checksums.values())) != 1 or len(set(totals.values())) != 1:
        raise AssertionError(
            "A-14 frozen baseline differs across levels; the sweep is confounded: "
            f"checksums={checksums} totals={totals}"
        )

    deltas = pd.DataFrame([row["delta"] for row in results])
    arms = pd.DataFrame([r for row in results for r in row["arm_rows"]])
    stages = pd.DataFrame([r for row in results for r in row["stage_rows"]])

    deltas.to_csv(output_dir / "level_incremental_metrics.csv", index=False)
    arms.to_csv(output_dir / "level_arm_summary.csv", index=False)
    stages.to_csv(output_dir / "level_stage_allocation_identities.csv", index=False)

    reference = stochastic_reference()
    n_stochastic = int(reference["n"])
    z_rows = []
    for row in results:
        level = row["level"]
        delta = row["delta"]
        for metric in (*SELECTION_METRICS, "delta_qaly"):
            stats = reference["metrics"][metric]
            value = float(delta[metric])
            se = float(stats["se_mean"])
            gap = value - float(stats["mean"])
            z_rows.append(
                {
                    "level": level,
                    "level_label": LEVEL_LABELS[level],
                    "metric": metric,
                    "in_selection_set": metric in SELECTION_METRICS,
                    "deterministic": value,
                    "stochastic_mean": float(stats["mean"]),
                    "stochastic_sd": float(stats["sd"]),
                    "stochastic_se_mean": se,
                    "n_stochastic": n_stochastic,
                    "deterministic_minus_stochastic_mean": gap,
                    "z_abs": abs(gap) / se if se else math.nan,
                    "passes_2se": (abs(gap) / se <= ACCEPTANCE_Z) if se else None,
                }
            )
    z_table = pd.DataFrame(z_rows)
    z_table.to_csv(output_dir / "level_z_table.csv", index=False)

    selection = z_table.loc[z_table["in_selection_set"]]
    verdict_rows = []
    for level, group in selection.groupby("level"):
        failing = group.loc[~group["passes_2se"].astype(bool), "metric"].tolist()
        verdict_rows.append(
            {
                "level": int(level),
                "level_label": LEVEL_LABELS[int(level)],
                "max_z_abs_selection_set": float(group["z_abs"].max()),
                "n_metrics_failing": len(failing),
                "failing_metrics": ";".join(failing),
                "passes": len(failing) == 0,
            }
        )
    verdicts = pd.DataFrame(verdict_rows).sort_values("level")
    verdicts.to_csv(output_dir / "level_pass_fail.csv", index=False)

    # Axis map actually applied, per level and call site.
    axis_rows = []
    for row in results:
        for process_id, axes in sorted(row["axis_log"].items()):
            axis_rows.append(
                {"level": row["level"], "process_id": process_id, "stratum_tensor_axes": axes}
            )
    pd.DataFrame(axis_rows).to_csv(output_dir / "applied_stratum_axes.csv", index=False)

    # Level-0 fidelity against the eleven A-12/A-4 deterministic reference values.
    fidelity_rows = []
    if 0 in levels:
        level0 = next(row["delta"] for row in results if row["level"] == 0)
        for metric, expected in DETERMINISTIC_REFERENCE.items():
            observed = float(level0[metric])
            fidelity_rows.append(
                {
                    "metric": metric,
                    "a12_a4_reference": expected,
                    "a14_level0": observed,
                    "exact_match": observed == float(expected),
                    "difference": observed - float(expected),
                }
            )
    fidelity = pd.DataFrame(fidelity_rows)
    fidelity.to_csv(output_dir / "level0_reference_fidelity.csv", index=False)

    manifest = {
        "task": "A-14 allocation granularity sweep",
        "command": shlex.join([sys.executable, *sys.argv]),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "runtime_config": str(runtime_config),
        "scenario": BASE_SCENARIO,
        "deterministic_seed": args.seed,
        "levels": levels,
        "stochastic_reference": str(A4_STOCHASTIC_SUMMARY.relative_to(ROOT)),
        "stochastic_reference_n": n_stochastic,
        "stochastic_reference_seeds": list(range(42, 57)),
        "frozen_baseline_identical_across_levels": True,
        "frozen_baseline_population_checksum": results[0]["frozen_snapshot_checksum"],
        "frozen_baseline_total_population": results[0]["frozen_snapshot_total_population"],
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "sha256": {
            "model_config": sha256(model_config),
            "runtime_config": sha256(runtime_config),
            "a4_stochastic_summary": sha256(A4_STOCHASTIC_SUMMARY),
            **{
                f"code/{name}": sha256(ROOT / name)
                for name in (
                    "tools/run_a14_allocation_granularity_sweep.py",
                    "tools/allocation_granularity.py",
                    "tests/test_a14_allocation_granularity.py",
                    "run_v3.py",
                    "stochastic_allocation.py",
                    "run_scenarios.py",
                    "counter_based_rng.py",
                    "transmission_corrected.py",
                    "demography.py",
                )
            },
        },
    }
    artifact_names = [
        "level_incremental_metrics.csv",
        "level_arm_summary.csv",
        "level_stage_allocation_identities.csv",
        "level_z_table.csv",
        "level_pass_fail.csv",
        "applied_stratum_axes.csv",
        "level0_reference_fidelity.csv",
    ]
    manifest["output_sha256"] = {
        name: sha256(output_dir / name) for name in artifact_names
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-14 sweep artifacts written to {output_dir}")
    print(verdicts.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
