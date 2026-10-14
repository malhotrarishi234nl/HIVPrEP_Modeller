"""Run A-15: does A-13's cross-stage allocation mechanism explain A-9?

Diagnostic only.  No production behaviour changes.  `run_v3.py`,
`stochastic_allocation.py`, `transmission_corrected.py` and every production
call site are byte-identical; the granularity level is applied by substituting
an engine subclass inside this process only, exactly as A-14 does.

Design constraints taken from the task brief (§4) and honoured literally:

  * **The grid runner is reused, not rewritten.**  The substitution switch grid
    is executed by calling `tools.run_a7_decision_comparison_decomposition.main`
    with a patched `sys.argv`.  Every metric in the grid table is read back from
    the CSVs that runner wrote, so the A-15 grid is the A-7 grid by
    construction.
  * **The allocation harness is reused, not duplicated.**  The engine subclass
    is `tools.run_a14_allocation_granularity_sweep.A14AllocationEngine`, which
    routes deterministic draws through `tools.allocation_granularity` and
    already records the A-13 within-arm stage identities.  A-15 adds only an
    (analysis scenario, arm) tag so those identities can be attributed to a
    switch level.
  * **Level 0 must reproduce the A-9 trigger sequence** +38/+7/+34/+113/+96
    before anything else is reported.  `level0_vs_a7_reference.csv` records that
    comparison; a mismatch is a finding, not something to paper over.

Stochastic mode (``--stochastic-seeds``) runs the same A-7 grid with the
production stochastic path (CRN, counter-based streams as in A-4), which is the
reference the deterministic approximation is judged against.  Allocation
granularity is inert in stochastic mode by construction — `_draw_events_for`
returns the production binomial draw before any granularity code is reached —
so stochastic rows are labelled `allocation_level = "stochastic"`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_scenarios as run_scenarios_module  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402
import tools.run_a7_decision_comparison_decomposition as a7  # noqa: E402
from tools.allocation_granularity import LEVEL_LABELS  # noqa: E402
from tools.run_a14_allocation_granularity_sweep import (  # noqa: E402
    ARM_ENGINES,
    A14AllocationEngine,
)

#: The A-9 trigger sequence, quoted from the handoff task board entry for A-9
#: and from `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md` §4.  A-15 exists
#: to explain these five numbers, so they are asserted against, not replaced.
A9_TRIGGER_INFECTIONS_AVERTED = {10: 38.0, 25: 7.0, 50: 34.0, 75: 113.0, 100: 96.0}
A9_TRIGGER_SWITCH_100_PERSON_YEARS = -33.5

A7_REFERENCE_DIR = (
    ROOT / "outputs/hta_results/a7_decision_comparison_decomposition_20260910_141500"
)

SWITCHES: tuple[int, ...] = a7.EXPECTED_SWITCHES
REFERENCE_ARM = a7.REFERENCE_ARM
INTERVENTION_ARM = a7.INTERVENTION_ARM

#: Per-arm annual series retained for the level-1 transmission-mechanism probe.
ARM_SERIES_COLUMNS = (
    "new_infections",
    "on_art_stock",
    "adult_hiv_pos",
    "hiv_deaths",
    "aids_deaths",
    "new_aids_cases",
    "new_diagnoses",
    "art_initiations",
    "total_population",
    "sexually_active_population",
    "msm_on_oral",
    "msm_on_lena",
)

#: Set by the patched `_build_arm_config` immediately before each arm engine is
#: constructed, so stage identities can be attributed to (scenario, arm).
CURRENT_TAG: dict[str, Any] = {"scenario": None, "arm": None}
RUNNER_REF: dict[str, Any] = {"runner": None}

#: Run mode for the payload currently executing in this worker process.  A
#: process pool may hand several payloads to the same process, so the monkey
#: patches below must be **stateless** — they read this dict at call time rather
#: than closing over a payload's values.
RUN_MODE: dict[str, Any] = {"stochastic": False, "seed": 42}


class A15AllocationEngine(A14AllocationEngine):
    """A-14's engine plus an (analysis scenario, arm) tag on arm engines."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(self, "_a14_is_arm_engine", False):
            self._a15_tag = (CURRENT_TAG["scenario"], CURRENT_TAG["arm"])
        else:
            self._a15_tag = None


def make_engine_class(level: int) -> type:
    return type(
        f"A15AllocationEngineLevel{level}",
        (A15AllocationEngine,),
        {"A14_LEVEL": int(level)},
    )


def patch_arm_tagging() -> None:
    """Tag each arm engine with the scenario/arm it was built for.

    `run_scenarios._run_hta_scenarios` calls `_build_arm_config(arm, scenario)`
    immediately before constructing that arm's engine, so wrapping it records
    the identity without relying on construction order.  Construction order is
    additionally asserted by `expected_arm_tags`.
    """
    original = MultiScenarioRunner._build_arm_config
    if getattr(original, "_a15_wrapped", False):
        return

    def wrapper(self, arm_name, analysis_scenario, param_overrides=None):
        CURRENT_TAG["scenario"] = analysis_scenario.get("name")
        CURRENT_TAG["arm"] = arm_name
        RUNNER_REF["runner"] = self
        return original(self, arm_name, analysis_scenario, param_overrides)

    wrapper._a15_wrapped = True  # type: ignore[attr-defined]
    MultiScenarioRunner._build_arm_config = wrapper  # type: ignore[assignment]


def apply_run_mode(base_config: dict, run_mode: dict[str, Any]) -> dict:
    """Write `run_mode` into a runner's base config.  Pure, and idempotent.

    Both flag locations are set because `SimulationEngineV3` reads
    `simulation_controls.deterministic_mode` (run_v3.py:224) while the HTA
    runner and several tests read `run_control.deterministic_mode`.
    """
    deterministic = not bool(run_mode["stochastic"])
    run_control = base_config.setdefault("run_control", {})
    existing = run_control.get("deterministic_mode")
    if isinstance(existing, dict):
        existing["value"] = deterministic  # keep provenance metadata intact
    else:
        run_control["deterministic_mode"] = {"value": deterministic}
    base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    run_control["seed"] = {"value": int(run_mode["seed"])}
    return base_config


def patch_run_mode() -> None:
    """Apply `RUN_MODE` to the runner's own config at the moment `run()` starts.

    `a7.main` hardcodes deterministic mode.  Rather than fork the grid runner
    (which the brief forbids), the flag is set from `RUN_MODE` after `a7.main`
    has finished configuring the runner.  The wrapper is **stateless** — it
    reads `RUN_MODE` at call time — so a worker process that has already run a
    stochastic payload cannot leak that mode into a later deterministic one.
    """
    original = MultiScenarioRunner.run
    if getattr(original, "_a15_run_mode", False):
        return

    def wrapper(self):
        apply_run_mode(self.base_config, RUN_MODE)
        return original(self)

    wrapper._a15_run_mode = True  # type: ignore[attr-defined]
    MultiScenarioRunner.run = wrapper  # type: ignore[assignment]


def expected_arm_tags() -> list[tuple[str, str]]:
    """The (scenario, arm) sequence arm engines must be constructed in."""
    return [
        (f"{a7.SCENARIO_PREFIX}{switch}", arm)
        for switch in SWITCHES
        for arm in (REFERENCE_ARM, INTERVENTION_ARM)
    ]


# ──────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested in tests/test_a15_a9_allocation_hypothesis.py)
# ──────────────────────────────────────────────────────────────────────────


def monotonicity_report(values: Sequence[float]) -> dict[str, Any]:
    """Describe monotonicity of a grid sequence ordered by switch level.

    Monotone means non-decreasing in the switch percentage: substituting more
    oral PrEP with lenacapavir should not avert fewer infections.  A descent is
    an adjacent pair where the value falls.
    """
    series = [float(v) for v in values]
    descents = [
        {"from_index": i, "drop": series[i] - series[i + 1]}
        for i in range(len(series) - 1)
        if series[i + 1] < series[i]
    ]
    return {
        "sequence": series,
        "n_descents": len(descents),
        "monotone_non_decreasing": len(descents) == 0,
        "largest_drop": max((d["drop"] for d in descents), default=0.0),
        "total_drop": float(sum(d["drop"] for d in descents)),
        "descent_positions": [int(d["from_index"]) for d in descents],
    }


def exact_match_rows(
    observed: dict[int, float], reference: dict[int, float]
) -> list[dict[str, Any]]:
    """Row-wise exact comparison of a switch-indexed grid against a reference."""
    rows = []
    for switch in sorted(reference):
        obs = float(observed[switch]) if switch in observed else float("nan")
        ref = float(reference[switch])
        rows.append(
            {
                "switch_percent": switch,
                "reference": ref,
                "observed": obs,
                "difference": obs - ref,
                "exact_match": obs == ref,
            }
        )
    return rows


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


# ──────────────────────────────────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────────────────────────────────


def _run_grid_worker(payload: dict[str, Any]) -> dict[str, Any]:
    level = payload["level"]
    seed = int(payload["seed"])
    stochastic = bool(payload["stochastic"])
    run_dir = Path(payload["run_dir"])

    ARM_ENGINES.clear()
    CURRENT_TAG["scenario"] = None
    CURRENT_TAG["arm"] = None
    RUN_MODE["stochastic"] = stochastic
    RUN_MODE["seed"] = seed
    run_scenarios_module.SimulationEngineV3 = make_engine_class(level)
    patch_arm_tagging()
    patch_run_mode()

    run_dir.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "tools/run_a7_decision_comparison_decomposition.py",
        "--model-config",
        payload["model_config"],
        "--hta-config",
        payload["hta_config"],
        "--output-dir",
        str(run_dir),
        "--seed",
        str(seed),
    ]
    saved_argv = sys.argv
    sys.argv = argv
    started = time.time()
    try:
        with contextlib.redirect_stdout(open(os.devnull, "w", encoding="utf-8")):
            status = a7.main()
    finally:
        sys.argv = saved_argv
    elapsed = time.time() - started
    if status != 0:
        raise RuntimeError(f"A-7 grid runner exited {status} for level {level}")

    label = "stochastic" if stochastic else str(level)

    # Attribute stage identities and per-arm series to (scenario, arm).
    tags = [engine._a15_tag for engine in ARM_ENGINES]
    if tags != expected_arm_tags():
        raise AssertionError(
            f"A-15 arm-engine tags do not match the expected grid order: {tags}"
        )

    stage_rows: list[dict[str, Any]] = []
    series_rows: list[dict[str, Any]] = []
    for engine in ARM_ENGINES:
        scenario_name, arm_name = engine._a15_tag
        switch = a7._scenario_switch(scenario_name)
        for slug, record in sorted(engine._a14_stage_totals.items()):
            stage_rows.append(
                {
                    "allocation_level": label,
                    "switch_percent": switch,
                    "arm": arm_name,
                    "process_stage": slug,
                    "pool": record["pool"],
                    "expected": record["expected"],
                    "actual": record["actual"],
                    "actual_minus_expected": record["actual"] - record["expected"],
                }
            )
        outputs = getattr(engine, "outputs", {}) or {}
        years = list(outputs.get("year", []))
        for index, year in enumerate(years):
            row = {
                "allocation_level": label,
                "seed": seed,
                "switch_percent": switch,
                "arm": arm_name,
                "step": outputs.get("step", [None] * len(years))[index],
                "year": float(year),
            }
            for column in ARM_SERIES_COLUMNS:
                values = outputs.get(column)
                row[column] = (
                    float(values[index])
                    if values is not None and index < len(values)
                    else float("nan")
                )
            series_rows.append(row)

    runner = RUNNER_REF["runner"]
    snapshot = runner.snapshot_manager.baseline_snapshot if runner else None

    decomposition = pd.read_csv(run_dir / "scenario_decomposition.csv")
    flows = pd.read_csv(run_dir / "event_flow_totals_long.csv")

    return {
        "allocation_level": label,
        "level": level,
        "seed": seed,
        "stochastic": stochastic,
        "elapsed_seconds": elapsed,
        "decomposition": decomposition.to_dict("records"),
        "flows": flows.to_dict("records"),
        "stage_rows": stage_rows,
        "series_rows": series_rows,
        "frozen_snapshot_checksum": getattr(snapshot, "population_checksum", None),
        "frozen_snapshot_total_population": (
            int(snapshot.population.sum()) if snapshot is not None else None
        ),
    }


# ──────────────────────────────────────────────────────────────────────────
# Assembly
# ──────────────────────────────────────────────────────────────────────────


def _grid_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    flow_lookup: dict[tuple[int, str], float] = {}
    for row in result["flows"]:
        flow_lookup[(int(row["switch_percent"]), row["flow"])] = float(
            row["delta_intervention_minus_reference"]
        )
    rows = []
    for record in result["decomposition"]:
        switch = int(record["switch_percent"])
        rows.append(
            {
                "allocation_level": result["allocation_level"],
                "level_label": (
                    "stochastic (production binomial, CRN)"
                    if result["stochastic"]
                    else LEVEL_LABELS[result["level"]]
                ),
                "seed": result["seed"],
                "switch_percent": switch,
                "infections_averted": float(record["infections_averted"]),
                "delta_qaly": float(record["delta_qaly"]),
                "delta_person_years": float(record["delta_person_years"]),
                "delta_discounted_person_years": float(
                    record["delta_discounted_person_years"]
                ),
                "hiv_deaths_averted": -flow_lookup[(switch, "hiv_deaths")],
                "aids_deaths_averted": -flow_lookup[(switch, "aids_deaths")],
                "delta_new_aids_cases": flow_lookup[(switch, "new_aids_cases")],
                "delta_new_diagnoses": flow_lookup[(switch, "new_diagnoses")],
                "delta_art_initiations": flow_lookup[(switch, "art_initiations")],
                "delta_background_deaths": flow_lookup[(switch, "background_deaths")],
                "final_total_population_delta": float(
                    record["final_total_population_delta"]
                ),
            }
        )
    rows.sort(key=lambda row: row["switch_percent"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-config",
        type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--hta-config", type=Path, default=ROOT / "configs/hta_runtime_config.json"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--levels", type=str, default="0,1,2,3")
    parser.add_argument(
        "--stochastic-seeds",
        type=str,
        default="",
        help="comma-separated seeds for the stochastic reference grid, e.g. 42-56",
    )
    parser.add_argument("--max-workers", type=int, default=5)
    args = parser.parse_args()

    levels = [int(token) for token in args.levels.split(",") if token.strip()]
    stochastic_seeds: list[int] = []
    if args.stochastic_seeds.strip():
        for token in args.stochastic_seeds.split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                low, high = token.split("-")
                stochastic_seeds.extend(range(int(low), int(high) + 1))
            else:
                stochastic_seeds.append(int(token))

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    model_config = args.model_config.resolve()
    hta_config = args.hta_config.resolve()

    payloads: list[dict[str, Any]] = [
        {
            "level": level,
            "seed": args.seed,
            "stochastic": False,
            "run_dir": str(runs_dir / f"level_{level}"),
            "model_config": str(model_config),
            "hta_config": str(hta_config),
        }
        for level in levels
    ]
    payloads.extend(
        {
            "level": 0,
            "seed": seed,
            "stochastic": True,
            "run_dir": str(runs_dir / f"stochastic_seed_{seed}"),
            "model_config": str(model_config),
            "hta_config": str(hta_config),
        }
        for seed in stochastic_seeds
    )

    results: list[dict[str, Any]] = []
    started = time.time()
    with ProcessPoolExecutor(
        max_workers=min(len(payloads), max(1, args.max_workers))
    ) as pool:
        for result in pool.map(_run_grid_worker, payloads):
            results.append(result)
    wall_seconds = time.time() - started

    deterministic_results = [r for r in results if not r["stochastic"]]
    stochastic_results = [r for r in results if r["stochastic"]]
    deterministic_results.sort(key=lambda row: row["level"])
    stochastic_results.sort(key=lambda row: row["seed"])

    # Validity guard: every *deterministic* run must fork from an identical
    # frozen 2025 state, so a between-level difference cannot be a different
    # starting population.  Stochastic runs deliberately do not share one: the
    # 1980-2025 baseline is itself drawn, so each seed has its own frozen state,
    # exactly as in A-4.  Their checksums are recorded rather than asserted.
    deterministic_checksums = {
        r["allocation_level"]: r["frozen_snapshot_checksum"]
        for r in deterministic_results
    }
    if len(set(deterministic_checksums.values())) != 1:
        raise AssertionError(
            "A-15 frozen baseline differs across deterministic levels; the grid "
            f"is confounded: {deterministic_checksums}"
        )
    stochastic_checksums = {
        r["seed"]: r["frozen_snapshot_checksum"] for r in stochastic_results
    }

    grid_rows: list[dict[str, Any]] = []
    for result in deterministic_results:
        grid_rows.extend(_grid_rows(result))
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(output_dir / "a9_grid_by_allocation_level.csv", index=False)

    stage = pd.DataFrame(
        [row for result in deterministic_results for row in result["stage_rows"]]
    )
    stage.to_csv(output_dir / "stage_allocation_identities.csv", index=False)

    series = pd.DataFrame(
        [row for result in results for row in result["series_rows"]]
    )
    series.to_csv(output_dir / "per_arm_annual_series.csv", index=False)

    # Level-0 fidelity against the A-9 trigger sequence and the A-7 artefacts.
    level0 = next((r for r in deterministic_results if r["level"] == 0), None)
    fidelity_rows: list[dict[str, Any]] = []
    if level0 is not None:
        level0_grid = {row["switch_percent"]: row for row in _grid_rows(level0)}
        for row in exact_match_rows(
            {k: v["infections_averted"] for k, v in level0_grid.items()},
            A9_TRIGGER_INFECTIONS_AVERTED,
        ):
            row["metric"] = "infections_averted"
            row["reference_source"] = "A-9 trigger sequence (handoff task board / brief §4)"
            fidelity_rows.append(row)
        a7_reference = pd.read_csv(A7_REFERENCE_DIR / "scenario_decomposition.csv")
        for metric in (
            "infections_averted",
            "delta_qaly",
            "delta_person_years",
            "delta_discounted_person_years",
        ):
            reference = {
                int(r["switch_percent"]): float(r[metric])
                for _, r in a7_reference.iterrows()
            }
            for row in exact_match_rows(
                {k: v[metric] for k, v in level0_grid.items()}, reference
            ):
                row["metric"] = metric
                row["reference_source"] = str(
                    (A7_REFERENCE_DIR / "scenario_decomposition.csv").relative_to(ROOT)
                )
                fidelity_rows.append(row)
    pd.DataFrame(fidelity_rows).to_csv(
        output_dir / "level0_vs_a7_reference.csv", index=False
    )

    # Monotonicity, per allocation level, on every reported metric.
    monotonicity_rows = []
    metrics = (
        "infections_averted",
        "delta_qaly",
        "delta_person_years",
        "hiv_deaths_averted",
    )
    for level_label, group in grid.groupby("allocation_level"):
        ordered = group.sort_values("switch_percent")
        for metric in metrics:
            report = monotonicity_report(ordered[metric].tolist())
            monotonicity_rows.append(
                {
                    "allocation_level": level_label,
                    "metric": metric,
                    "sequence": ";".join(f"{v:g}" for v in report["sequence"]),
                    "n_descents": report["n_descents"],
                    "monotone_non_decreasing": report["monotone_non_decreasing"],
                    "largest_drop": report["largest_drop"],
                    "total_drop": report["total_drop"],
                    "descent_positions": ";".join(
                        str(p) for p in report["descent_positions"]
                    ),
                }
            )
    pd.DataFrame(monotonicity_rows).to_csv(
        output_dir / "monotonicity_summary.csv", index=False
    )

    # Byte-identity check across stratified levels on THIS config.
    identity_rows = []
    for left in deterministic_results:
        for right in deterministic_results:
            if left["level"] >= right["level"]:
                continue
            left_grid = pd.DataFrame(_grid_rows(left)).drop(
                columns=["allocation_level", "level_label"]
            )
            right_grid = pd.DataFrame(_grid_rows(right)).drop(
                columns=["allocation_level", "level_label"]
            )
            identity_rows.append(
                {
                    "level_a": left["level"],
                    "level_b": right["level"],
                    "grids_identical": bool(left_grid.equals(right_grid)),
                    "max_abs_difference": float(
                        (left_grid - right_grid).abs().to_numpy().max()
                    ),
                }
            )
    pd.DataFrame(identity_rows).to_csv(
        output_dir / "level_identity_checks.csv", index=False
    )

    # Stochastic reference grid.
    stochastic_summary = pd.DataFrame()
    if stochastic_results:
        stochastic_rows: list[dict[str, Any]] = []
        for result in stochastic_results:
            stochastic_rows.extend(_grid_rows(result))
        stochastic = pd.DataFrame(stochastic_rows)
        stochastic.to_csv(output_dir / "stochastic_grid_by_seed.csv", index=False)
        summary_rows = []
        for switch, group in stochastic.groupby("switch_percent"):
            for metric in (
                "infections_averted",
                "delta_qaly",
                "delta_person_years",
                "delta_discounted_person_years",
                "hiv_deaths_averted",
            ):
                values = group[metric].to_numpy(dtype=float)
                n = int(values.size)
                sd = float(np.std(values, ddof=1)) if n > 1 else float("nan")
                summary_rows.append(
                    {
                        "switch_percent": int(switch),
                        "metric": metric,
                        "n": n,
                        "mean": float(values.mean()),
                        "sd": sd,
                        "se_mean": sd / np.sqrt(n) if n > 1 else float("nan"),
                        "min": float(values.min()),
                        "max": float(values.max()),
                    }
                )
        stochastic_summary = pd.DataFrame(summary_rows)
        stochastic_summary.to_csv(
            output_dir / "stochastic_grid_summary.csv", index=False
        )

        # |z| of each deterministic level against the stochastic mean.
        z_rows = []
        lookup = {
            (int(r["switch_percent"]), r["metric"]): r
            for _, r in stochastic_summary.iterrows()
        }
        for _, row in grid.iterrows():
            for metric in (
                "infections_averted",
                "delta_qaly",
                "delta_person_years",
                "delta_discounted_person_years",
                "hiv_deaths_averted",
            ):
                stats = lookup[(int(row["switch_percent"]), metric)]
                se = float(stats["se_mean"])
                gap = float(row[metric]) - float(stats["mean"])
                z_rows.append(
                    {
                        "allocation_level": row["allocation_level"],
                        "switch_percent": int(row["switch_percent"]),
                        "metric": metric,
                        "deterministic": float(row[metric]),
                        "stochastic_mean": float(stats["mean"]),
                        "stochastic_sd": float(stats["sd"]),
                        "stochastic_se_mean": se,
                        "n_stochastic": int(stats["n"]),
                        "gap": gap,
                        "z_abs": abs(gap) / se if se else float("nan"),
                    }
                )
        pd.DataFrame(z_rows).to_csv(
            output_dir / "deterministic_vs_stochastic_z.csv", index=False
        )

    artifact_names = [
        "a9_grid_by_allocation_level.csv",
        "stage_allocation_identities.csv",
        "per_arm_annual_series.csv",
        "level0_vs_a7_reference.csv",
        "monotonicity_summary.csv",
        "level_identity_checks.csv",
    ]
    if stochastic_results:
        artifact_names.extend(
            [
                "stochastic_grid_by_seed.csv",
                "stochastic_grid_summary.csv",
                "deterministic_vs_stochastic_z.csv",
            ]
        )

    manifest = {
        "task": "A-15 does A-13's allocation mechanism explain A-9",
        "command": shlex.join([sys.executable, *sys.argv]),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "hta_config": str(hta_config),
        "scenarios": [f"{a7.SCENARIO_PREFIX}{switch}" for switch in SWITCHES],
        "reference_arm": REFERENCE_ARM,
        "intervention_arm": INTERVENTION_ARM,
        "deterministic_levels": levels,
        "deterministic_seed": args.seed,
        "stochastic_seeds": stochastic_seeds,
        "a9_trigger_infections_averted": A9_TRIGGER_INFECTIONS_AVERTED,
        "grid_runner_reused": "tools/run_a7_decision_comparison_decomposition.py",
        "allocation_harness_reused": "tools/allocation_granularity.py",
        "frozen_baseline_identical_across_deterministic_levels": True,
        "frozen_baseline_population_checksum_deterministic": deterministic_results[0][
            "frozen_snapshot_checksum"
        ],
        "frozen_baseline_total_population_deterministic": deterministic_results[0][
            "frozen_snapshot_total_population"
        ],
        "frozen_baseline_population_checksum_by_stochastic_seed": stochastic_checksums,
        "wall_seconds": wall_seconds,
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "sha256": {
            "model_config": sha256(model_config),
            "hta_config": sha256(hta_config),
            "costing_config": sha256(ROOT / "configs/hta_costing_config.json"),
            **{
                f"code/{name}": sha256(ROOT / name)
                for name in (
                    "tools/run_a15_a9_allocation_hypothesis.py",
                    "tools/run_a7_decision_comparison_decomposition.py",
                    "tools/run_a14_allocation_granularity_sweep.py",
                    "tools/allocation_granularity.py",
                    "tests/test_a15_a9_allocation_hypothesis.py",
                    "run_v3.py",
                    "run_scenarios.py",
                    "stochastic_allocation.py",
                    "transmission_corrected.py",
                    "economics.py",
                    "demography.py",
                    "counter_based_rng.py",
                )
            },
        },
        "output_sha256": {
            name: sha256(output_dir / name) for name in artifact_names
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"A-15 artifacts written to {output_dir}")
    print(grid.to_string(index=False))
    print(pd.DataFrame(monotonicity_rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
