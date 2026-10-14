#!/usr/bin/env python3
"""Resumable stochastic CRN runner for a split HTA policy matrix.

One worker owns one stochastic seed and runs the complete scenario matrix from
one historical pre-freeze trajectory. This preserves the reviewed trajectory
budget and common-random-number pairing across policy scenarios.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

from config_utils import extract_value
from run_scenarios import MultiScenarioRunner

ROOT = Path(__file__).resolve().parent
HASHED_MODULES = (
    "run_policy_matrix.py", "run_scenarios.py", "run_v3.py",
    "hta_config_loader.py", "economics.py", "counter_based_rng.py",
)


class PolicyMatrixError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes())
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _module_hashes() -> dict[str, str]:
    return {name: _sha256(ROOT / name) for name in HASHED_MODULES}


def _seed_list(value: str) -> list[int]:
    seeds: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            if end < start:
                raise PolicyMatrixError(f"descending seed range: {token}")
            seeds.extend(range(start, end + 1))
        else:
            seeds.append(int(token))
    if not seeds or len(seeds) != len(set(seeds)):
        raise PolicyMatrixError("seeds must be a non-empty unique set")
    return seeds


def _scenario_rows(runner: MultiScenarioRunner, seed: int) -> list[dict]:
    perspective = runner.hta_config.get("perspective", {}).get("primary", "payer")
    wtp = float(extract_value(runner.hta_config["wtp_threshold_eur_per_qaly"]))
    scenarios = {item["name"]: item for item in runner.hta_config["analysis_scenarios"]}
    rows = []
    for name, arm_results in runner.results.items():
        scenario = scenarios[name]
        comparator_arm = scenario.get("comparator_arm") or scenario["arm_order"][0]
        intervention_arm = scenario.get("intervention_arm") or scenario["arm_order"][1]
        comparator = arm_results[comparator_arm]
        intervention = arm_results[intervention_arm]
        if perspective == "societal":
            ref_cost = float(comparator.total_costs_disc_societal)
            int_cost = float(intervention.total_costs_disc_societal)
        else:
            ref_cost = float(comparator.total_costs_disc)
            int_cost = float(intervention.total_costs_disc)
        ref_qaly = float(comparator.total_qalys_disc)
        int_qaly = float(intervention.total_qalys_disc)
        delta_cost = int_cost - ref_cost
        delta_qaly = int_qaly - ref_qaly
        rows.append({
            "seed": seed,
            "scenario": name,
            "label": scenario.get("label", name),
            "family": scenario.get("analysis_metadata", {}).get("family", "context"),
            "comparator_arm": comparator_arm,
            "intervention_arm": intervention_arm,
            "perspective": perspective,
            "reference_cost": ref_cost,
            "intervention_cost": int_cost,
            "delta_cost": delta_cost,
            "reference_qaly": ref_qaly,
            "intervention_qaly": int_qaly,
            "delta_qaly": delta_qaly,
            "icer": delta_cost / delta_qaly if abs(delta_qaly) > 1e-9 else float("inf"),
            "nmb": wtp * delta_qaly - delta_cost,
            "reference_infections": float(comparator.cumul_new_infections),
            "intervention_infections": float(intervention.cumul_new_infections),
            "infections_averted": float(
                comparator.cumul_new_infections - intervention.cumul_new_infections
            ),
        })
    return rows


def _worker(payload: dict) -> dict:
    seed = int(payload["seed"])
    run_dir = Path(payload["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        runner = MultiScenarioRunner(
            payload["model_config"], str(run_dir / "engine_outputs"), payload["hta_config"]
        )
        runner.hta_config["outputs"].setdefault("excel_report", {})["enabled"] = False
        runner.hta_config["outputs"].setdefault("csv_files", {})["enabled"] = False
        runner.base_config["run_control"]["deterministic_mode"] = {"value": False}
        runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
            "value": False
        }
        runner.base_config["run_control"]["seed"] = {"value": seed}
        log_path = run_dir / "runner.log"
        with log_path.open("w", encoding="utf-8") as handle:
            with contextlib.redirect_stdout(handle), contextlib.redirect_stderr(handle):
                runner.run()
        return {
            "status": "COMPLETE",
            "seed": seed,
            "rows": _scenario_rows(runner, seed),
            "snapshot_checksum": runner.snapshot_manager.baseline_snapshot.population_checksum,
            "elapsed_seconds": time.time() - started,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "seed": seed,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "elapsed_seconds": time.time() - started,
        }


def prepare(model_config: Path, hta_config: Path, output_dir: Path, seeds: list[int]) -> dict:
    model_config = model_config.resolve()
    hta_config = hta_config.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PolicyMatrixError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    probe = MultiScenarioRunner(str(model_config), str(output_dir / "unused"), str(hta_config))
    scenarios = probe.hta_config["analysis_scenarios"]
    for scenario in scenarios:
        for arm in scenario["arm_order"]:
            probe._build_arm_config(arm, scenario)
    arms_per_seed = sum(len(item["arm_order"]) for item in scenarios)
    plan = {
        "schema_version": "1.0",
        "status": "POLICY_MATRIX_PREPARED",
        "model_config": _rel(model_config),
        "hta_config": _rel(hta_config),
        "input_sha256": {
            "model_config": _sha256(model_config),
            "hta_config": _sha256(hta_config),
        },
        "module_sha256": _module_hashes(),
        "seeds": seeds,
        "seed_policy": "common_random_numbers_across_all_scenarios",
        "scenarios": [item["name"] for item in scenarios],
        "scenario_count": len(scenarios),
        "forward_arms_per_seed": arms_per_seed,
        "historical_prefreeze_per_seed": 1,
        "maximum_trajectories": len(seeds) * (arms_per_seed + 1),
        "simulations_started": 0,
        "generated_utc": _now(),
    }
    _atomic_json(output_dir / "plan.json", plan)
    return plan


def _load_plan(plan_path: Path) -> tuple[dict, Path]:
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = plan_path.parent
    for key in ("model_config", "hta_config"):
        path = _resolve(plan[key])
        if not path.is_file() or _sha256(path) != plan["input_sha256"][key]:
            raise PolicyMatrixError(f"{key} is missing or changed")
    if plan.get("module_sha256") != _module_hashes():
        raise PolicyMatrixError(
            "policy execution code changed since preparation; prepare a new plan"
        )
    return plan, root


def run(plan_path: Path, workers: int) -> dict:
    plan, root = _load_plan(plan_path)
    if workers < 1:
        raise PolicyMatrixError("workers must be positive")
    result_dir = root / "seed_results"
    result_dir.mkdir(exist_ok=True)
    pending = []
    for seed in plan["seeds"]:
        path = result_dir / f"seed_{int(seed)}.json"
        if path.is_file():
            try:
                if json.loads(path.read_text())["status"] == "COMPLETE":
                    continue
            except (KeyError, json.JSONDecodeError):
                pass
        pending.append(int(seed))
    started = time.time()
    failures = []
    with ProcessPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as pool:
        futures = {
            pool.submit(_worker, {
                "seed": seed,
                "model_config": str(_resolve(plan["model_config"])),
                "hta_config": str(_resolve(plan["hta_config"])),
                "run_dir": str(root / "runs" / f"seed_{seed}"),
            }): seed for seed in pending
        }
        for future in as_completed(futures):
            result = future.result()
            seed = int(result["seed"])
            if result["status"] == "COMPLETE":
                _atomic_json(result_dir / f"seed_{seed}.json", result)
            else:
                _atomic_json(root / "failures" / f"seed_{seed}.json", result)
                failures.append(seed)
            print(f"policy matrix: seed {seed} {result['status']}", flush=True)
    completed = sum((result_dir / f"seed_{int(seed)}.json").is_file() for seed in plan["seeds"])
    manifest = {
        "status": "POLICY_MATRIX_COMPLETE" if completed == len(plan["seeds"]) else "POLICY_MATRIX_PARTIAL",
        "expected_seeds": len(plan["seeds"]),
        "completed_seeds": completed,
        "failed_seeds_this_attempt": failures,
        "workers": workers,
        "elapsed_seconds_this_attempt": time.time() - started,
        "updated_utc": _now(),
    }
    _atomic_json(root / "run_manifest.json", manifest)
    return manifest


def finalize(plan_path: Path) -> dict:
    plan, root = _load_plan(plan_path)
    rows = []
    missing = []
    checksums = set()
    for seed in plan["seeds"]:
        path = root / "seed_results" / f"seed_{int(seed)}.json"
        if not path.is_file():
            missing.append(int(seed))
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "COMPLETE":
            missing.append(int(seed))
            continue
        rows.extend(payload["rows"])
        checksums.add(payload["snapshot_checksum"])
    if missing:
        raise PolicyMatrixError(f"cannot finalize; missing seeds: {missing}")
    frame = pd.DataFrame(rows).sort_values(["scenario", "seed"])
    frame.to_csv(root / "policy_seed_results.csv", index=False)
    summary = frame.groupby(["scenario", "label", "family"], sort=False).agg(
        seeds=("seed", "nunique"),
        mean_delta_cost=("delta_cost", "mean"),
        sd_delta_cost=("delta_cost", "std"),
        mean_delta_qaly=("delta_qaly", "mean"),
        sd_delta_qaly=("delta_qaly", "std"),
        mean_nmb=("nmb", "mean"),
        probability_positive_nmb=("nmb", lambda values: float((values > 0).mean())),
        mean_infections_averted=("infections_averted", "mean"),
    ).reset_index()
    summary.to_csv(root / "policy_summary.csv", index=False)
    manifest = {
        "status": "POLICY_MATRIX_FINALIZED",
        "seeds": len(plan["seeds"]),
        "scenarios": len(plan["scenarios"]),
        "rows": len(frame),
        "unique_prefreeze_snapshot_checksums": len(checksums),
        "outputs": {
            name: _sha256(root / name)
            for name in ("policy_seed_results.csv", "policy_summary.csv")
        },
        "generated_utc": _now(),
    }
    _atomic_json(root / "final_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--finalize", action="store_true")
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--hta-config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--seeds", default="42-56")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            if not all((args.model_config, args.hta_config, args.output_dir)):
                raise PolicyMatrixError("prepare requires model, HTA, and output paths")
            result = prepare(
                args.model_config, args.hta_config, args.output_dir, _seed_list(args.seeds)
            )
        elif args.run:
            if args.plan is None:
                raise PolicyMatrixError("run requires --plan")
            result = run(args.plan, args.workers)
        else:
            if args.plan is None:
                raise PolicyMatrixError("finalize requires --plan")
            result = finalize(args.plan)
        print(json.dumps(result, indent=2))
        return 0
    except PolicyMatrixError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
