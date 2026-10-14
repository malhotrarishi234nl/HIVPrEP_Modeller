#!/usr/bin/env python3
"""Resumable PSA x HTA scenario-matrix runner.

The existing per-draw HTA implementation remains the scientific execution
path. This module adds a frozen plan, shared parameter draws across scenarios,
stable shards, one atomic result per (scenario, draw), resume, and compact
merged CSV outputs. It deliberately writes no scenario-per-sheet workbooks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from hta_config_loader import load_hta_config
from hta_sampler import HTASampler
from psa_sampler import PSASampler

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "1.0"
_WORKER_CONTEXT: dict[str, Any] = {}
HASHED_MODULES = (
    "run_psa_hta_matrix.py", "run_psa_hta.py", "run_v3.py",
    "hta_config_loader.py", "psa_sampler.py", "hta_sampler.py",
)


class MatrixError(ValueError):
    """The matrix plan or retained result set violates its contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _relative(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot read {path}: {exc}") from exc


def _module_hashes() -> dict[str, str]:
    return {name: _sha256(ROOT / name) for name in HASHED_MODULES}


def prepare(
    *, model_config: Path, hta_config: Path, psa_config: Path,
    output_dir: Path, n_draws: int, seed: int, shard_count: int,
    scenario_names: Sequence[str] | None = None,
) -> dict:
    if n_draws < 1 or shard_count < 1:
        raise MatrixError("n_draws and shard_count must be positive")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise MatrixError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    model_config = model_config.resolve()
    hta_config = hta_config.resolve()
    psa_config = psa_config.resolve()
    for path in (model_config, hta_config, psa_config):
        if not path.is_file():
            raise MatrixError(f"missing input: {path}")
    model = _read_json(model_config)
    hta = load_hta_config(str(hta_config))
    psa = _read_json(psa_config)
    scenarios = hta.get("analysis_scenarios", [])
    by_name = {item["name"]: item for item in scenarios}
    selected_names = list(scenario_names or by_name)
    if len(selected_names) != len(set(selected_names)):
        raise MatrixError("scenario selection contains duplicates")
    missing = sorted(set(selected_names) - set(by_name))
    if missing:
        raise MatrixError(f"unknown scenarios: {missing}")

    epi_sampler = PSASampler(model, psa)
    hta_sampler = HTASampler(hta, psa)
    epi_draws = epi_sampler.generate_draws(n=n_draws, seed=seed)
    hta_draws = hta_sampler.generate_draws(n=n_draws, seed=seed + 1)
    epi_draws.index.name = "draw_index"
    hta_draws.index.name = "draw_index"
    epi_path = output_dir / "epi_draws.csv"
    hta_path = output_dir / "hta_draws.csv"
    epi_draws.to_csv(epi_path)
    hta_draws.to_csv(hta_path)
    epi_sampler.get_parameter_summary().to_csv(
        output_dir / "epi_parameter_summary.csv", index=False
    )
    hta_sampler.get_parameter_summary().to_csv(
        output_dir / "hta_parameter_summary.csv", index=False
    )

    tasks = []
    ordinal = 0
    for draw_index in range(n_draws):
        for scenario_name in selected_names:
            tasks.append({
                "task_ordinal": ordinal,
                "draw_index": draw_index,
                "scenario": scenario_name,
                "shard": ordinal % shard_count,
            })
            ordinal += 1
    tasks_path = output_dir / "tasks.csv"
    pd.DataFrame(tasks).to_csv(tasks_path, index=False)

    plan = {
        "schema_version": SCHEMA_VERSION,
        "status": "PSA_MATRIX_PREPARED",
        "model_config": _relative(model_config),
        "hta_config": _relative(hta_config),
        "psa_config": _relative(psa_config),
        "input_sha256": {
            "model_config": _sha256(model_config),
            "hta_config": _sha256(hta_config),
            "psa_config": _sha256(psa_config),
        },
        "module_sha256": _module_hashes(),
        "draws": n_draws,
        "draw_seed": seed,
        "simulation_seed_rule": "draw_seed + draw_index",
        "method": psa["sampling"]["method"]["value"],
        "epi_parameters": epi_sampler.param_paths,
        "hta_parameters": hta_sampler.param_paths,
        "scenarios": selected_names,
        "scenario_count": len(selected_names),
        "task_count": len(tasks),
        "shard_count": shard_count,
        "task_assignment": "task_ordinal modulo shard_count",
        "draw_files": {
            "epi": {"file": epi_path.name, "sha256": _sha256(epi_path)},
            "hta": {"file": hta_path.name, "sha256": _sha256(hta_path)},
            "tasks": {"file": tasks_path.name, "sha256": _sha256(tasks_path)},
        },
        "uncertainty_interpretation": (
            "Parameter PSA conditional on the selected point-calibrated backbone. "
            "This is not calibration-posterior uncertainty."
        ),
        "crn_interpretation": (
            "The same parameter draw index and deterministic simulation seed are used "
            "across scenarios to sharpen paired policy contrasts."
        ),
        "simulations_started": 0,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "generated_utc": _now(),
    }
    _atomic_json(output_dir / "plan.json", plan)
    return plan


def _load_plan(plan_path: Path) -> tuple[dict, Path]:
    plan_path = plan_path.resolve()
    plan = _read_json(plan_path)
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise MatrixError("unsupported plan schema")
    root = plan_path.parent
    for key in ("model_config", "hta_config", "psa_config"):
        path = _resolve(plan[key])
        if not path.is_file() or _sha256(path) != plan["input_sha256"][key]:
            raise MatrixError(f"{key} is missing or changed since preparation")
    for key, entry in plan["draw_files"].items():
        path = root / entry["file"]
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            raise MatrixError(f"frozen {key} file is missing or changed")
    if plan.get("module_sha256") != _module_hashes():
        raise MatrixError("PSA execution code changed since preparation; prepare a new plan")
    return plan, root


def _init_worker(context: dict) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = context


def _run_task(task: dict) -> dict:
    from run_psa_hta import _run_single_hta_draw

    started = time.time()
    try:
        draw_index = int(task["draw_index"])
        scenario = _WORKER_CONTEXT["scenarios"][task["scenario"]]
        result = _run_single_hta_draw(
            draw_index=draw_index,
            epi_draw_row=_WORKER_CONTEXT["epi_draws"][draw_index],
            hta_draw_row=_WORKER_CONTEXT["hta_draws"][draw_index],
            base_config=_WORKER_CONTEXT["model"],
            hta_config=_WORKER_CONTEXT["hta"],
            seed=int(_WORKER_CONTEXT["seed"]),
            analysis_scenario=scenario,
            freeze_year=_WORKER_CONTEXT["freeze_year"],
            eval_years=_WORKER_CONTEXT["eval_years"],
            quiet=True,
        )
        return {
            "status": "COMPLETE",
            **task,
            "arm_results": result["arm_results"],
            "incremental": result["incremental"],
            "elapsed_seconds": time.time() - started,
        }
    except Exception as exc:  # retain exact task failure and continue other tasks
        return {
            "status": "FAILED",
            **task,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "elapsed_seconds": time.time() - started,
        }


def _result_path(shard_dir: Path, task: dict) -> Path:
    safe_scenario = str(task["scenario"]).replace("/", "_")
    return shard_dir / "results" / safe_scenario / f"draw_{int(task['draw_index']):05d}.json"


def _valid_completed_result(path: Path, task: dict) -> bool:
    if not path.is_file():
        return False
    try:
        result = _read_json(path)
    except MatrixError:
        return False
    return (
        result.get("status") == "COMPLETE"
        and int(result.get("draw_index", -1)) == int(task["draw_index"])
        and result.get("scenario") == task["scenario"]
    )


def run_shard(
    *, plan_path: Path, shard_index: int, workers: int,
    max_tasks: int | None = None,
) -> dict:
    plan, root = _load_plan(plan_path)
    if not 0 <= shard_index < int(plan["shard_count"]):
        raise MatrixError("shard_index is outside the prepared range")
    if workers < 1:
        raise MatrixError("workers must be positive")
    tasks = pd.read_csv(root / plan["draw_files"]["tasks"]["file"])
    tasks = tasks.loc[tasks["shard"] == shard_index].to_dict("records")
    shard_dir = root / "shards" / f"shard_{shard_index:03d}"
    pending = [task for task in tasks if not _valid_completed_result(
        _result_path(shard_dir, task), task
    )]
    if max_tasks is not None:
        pending = pending[:max(0, int(max_tasks))]

    model = _read_json(_resolve(plan["model_config"]))
    hta = load_hta_config(str(_resolve(plan["hta_config"])))
    epi_frame = pd.read_csv(root / plan["draw_files"]["epi"]["file"], index_col=0)
    hta_frame = pd.read_csv(root / plan["draw_files"]["hta"]["file"], index_col=0)
    context = {
        "model": model,
        "hta": hta,
        "scenarios": {item["name"]: item for item in hta["analysis_scenarios"]},
        "epi_draws": {int(i): row.to_dict() for i, row in epi_frame.iterrows()},
        "hta_draws": {int(i): row.to_dict() for i, row in hta_frame.iterrows()},
        "seed": int(plan["draw_seed"]),
        "freeze_year": hta["time_horizon"]["freeze_year"],
        "eval_years": hta["time_horizon"]["evaluation_years"],
    }
    shard_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    completed_now = 0
    failures = []
    queue = list(pending)
    with ProcessPoolExecutor(
        max_workers=min(workers, max(1, len(queue))),
        initializer=_init_worker,
        initargs=(context,),
    ) as pool:
        running = {}
        while queue or running:
            while queue and len(running) < workers * 2:
                task = queue.pop(0)
                running[pool.submit(_run_task, task)] = task
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task = running.pop(future)
                result = future.result()
                if result["status"] == "COMPLETE":
                    _atomic_json(_result_path(shard_dir, task), result)
                    completed_now += 1
                else:
                    failure_path = shard_dir / "failures" / (
                        f"{task['scenario']}__draw_{int(task['draw_index']):05d}.json"
                    )
                    _atomic_json(failure_path, result)
                    failures.append(str(failure_path.relative_to(root)))
                print(
                    f"shard {shard_index}: {completed_now}/{len(pending)} newly complete, "
                    f"{len(failures)} failed",
                    flush=True,
                )

    total_complete = sum(
        _valid_completed_result(_result_path(shard_dir, task), task) for task in tasks
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "SHARD_COMPLETE" if total_complete == len(tasks) else "SHARD_PARTIAL",
        "shard_index": shard_index,
        "shard_count": int(plan["shard_count"]),
        "expected_tasks": len(tasks),
        "completed_tasks": total_complete,
        "newly_completed_tasks": completed_now,
        "failures_this_attempt": failures,
        "workers": workers,
        "elapsed_seconds_this_attempt": time.time() - started,
        "updated_utc": _now(),
    }
    _atomic_json(shard_dir / "manifest.json", manifest)
    return manifest


def finalize(*, plan_path: Path, allow_partial: bool = False) -> dict:
    plan, root = _load_plan(plan_path)
    tasks = pd.read_csv(root / plan["draw_files"]["tasks"]["file"]).to_dict("records")
    incremental_rows = []
    arm_rows = []
    missing = []
    for task in tasks:
        shard_dir = root / "shards" / f"shard_{int(task['shard']):03d}"
        path = _result_path(shard_dir, task)
        if not _valid_completed_result(path, task):
            missing.append({"scenario": task["scenario"], "draw_index": int(task["draw_index"])})
            continue
        result = _read_json(path)
        common = {
            "scenario": task["scenario"],
            "draw_index": int(task["draw_index"]),
            "shard": int(task["shard"]),
        }
        for comparison, values in result["incremental"].items():
            incremental_rows.append({**common, "comparison": comparison, **values})
        for arm, values in result["arm_results"].items():
            arm_rows.append({**common, "arm": arm, **values})
    if missing and not allow_partial:
        raise MatrixError(
            f"cannot finalize: {len(missing)} of {len(tasks)} tasks are incomplete"
        )
    incremental = pd.DataFrame(incremental_rows)
    arms = pd.DataFrame(arm_rows)
    incremental.to_csv(root / "psa_matrix_incremental.csv", index=False)
    arms.to_csv(root / "psa_matrix_arm_outcomes.csv", index=False)
    if incremental.empty:
        summary = pd.DataFrame()
    else:
        summary = incremental.groupby(["scenario", "comparison"], sort=False).agg(
            draws=("draw_index", "nunique"),
            mean_delta_cost=("delta_cost", "mean"),
            p025_delta_cost=("delta_cost", lambda values: float(values.quantile(0.025))),
            p50_delta_cost=("delta_cost", "median"),
            p975_delta_cost=("delta_cost", lambda values: float(values.quantile(0.975))),
            mean_delta_qaly=("delta_qaly", "mean"),
            p025_delta_qaly=("delta_qaly", lambda values: float(values.quantile(0.025))),
            p50_delta_qaly=("delta_qaly", "median"),
            p975_delta_qaly=("delta_qaly", lambda values: float(values.quantile(0.975))),
            mean_nmb=("nmb", "mean"),
            sd_nmb=("nmb", "std"),
            p025_nmb=("nmb", lambda values: float(values.quantile(0.025))),
            p50_nmb=("nmb", "median"),
            p975_nmb=("nmb", lambda values: float(values.quantile(0.975))),
            probability_cost_effective=("nmb", lambda values: float((values > 0).mean())),
            mean_infections_averted=("infections_averted", "mean"),
        ).reset_index()
    summary.to_csv(root / "psa_matrix_summary.csv", index=False)
    convergence_rows = []
    if not incremental.empty:
        for (scenario, comparison), group in incremental.groupby(
            ["scenario", "comparison"], sort=False
        ):
            group = group.sort_values("draw_index")
            n_total = len(group)
            checkpoints = sorted(set(
                [min(n_total, value) for value in (100, 250, 500, 1000, n_total)]
            ))
            for checkpoint in checkpoints:
                prefix = group.iloc[:checkpoint]
                convergence_rows.append({
                    "scenario": scenario,
                    "comparison": comparison,
                    "draws": checkpoint,
                    "mean_nmb": float(prefix["nmb"].mean()),
                    "se_mean_nmb": float(prefix["nmb"].sem()) if checkpoint > 1 else float("nan"),
                    "probability_cost_effective": float((prefix["nmb"] > 0).mean()),
                    "mean_delta_qaly": float(prefix["delta_qaly"].mean()),
                    "mean_delta_cost": float(prefix["delta_cost"].mean()),
                })
    pd.DataFrame(convergence_rows).to_csv(root / "psa_matrix_convergence.csv", index=False)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "PSA_MATRIX_COMPLETE" if not missing else "PSA_MATRIX_PARTIAL",
        "expected_tasks": len(tasks),
        "completed_tasks": len(tasks) - len(missing),
        "missing_tasks": len(missing),
        "allow_partial": bool(allow_partial),
        "outputs": {
            name: {"sha256": _sha256(root / name), "bytes": (root / name).stat().st_size}
            for name in (
                "psa_matrix_incremental.csv",
                "psa_matrix_arm_outcomes.csv",
                "psa_matrix_summary.csv",
                "psa_matrix_convergence.csv",
            )
        },
        "generated_utc": _now(),
    }
    _atomic_json(root / "final_manifest.json", manifest)
    if missing:
        _atomic_json(root / "missing_tasks.json", {"tasks": missing})
    return manifest


def _parse_scenarios(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run-shard", action="store_true")
    mode.add_argument("--finalize", action="store_true")
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--hta-config", type=Path)
    parser.add_argument("--psa-config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--n-draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--shard-count", type=int, default=8)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scenarios", help="comma-separated scenario names; default all")
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            required = (args.model_config, args.hta_config, args.psa_config, args.output_dir)
            if any(value is None for value in required):
                raise MatrixError("prepare requires model, HTA, PSA, and output paths")
            result = prepare(
                model_config=args.model_config,
                hta_config=args.hta_config,
                psa_config=args.psa_config,
                output_dir=args.output_dir,
                n_draws=args.n_draws,
                seed=args.seed,
                shard_count=args.shard_count,
                scenario_names=_parse_scenarios(args.scenarios),
            )
        elif args.run_shard:
            if args.plan is None or args.shard_index is None:
                raise MatrixError("run-shard requires --plan and --shard-index")
            result = run_shard(
                plan_path=args.plan, shard_index=args.shard_index,
                workers=args.workers, max_tasks=args.max_tasks,
            )
        else:
            if args.plan is None:
                raise MatrixError("finalize requires --plan")
            result = finalize(plan_path=args.plan, allow_partial=args.allow_partial)
        print(json.dumps(result, indent=2))
        return 0
    except MatrixError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
