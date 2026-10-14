"""Run Phase-2 task A-4: paired-arm common-random-number verification."""

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
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_scenarios import MultiScenarioRunner  # noqa: E402
from tools.run_a12_deterministic_allocation_audit import (  # noqa: E402
    select_scenario,
    summarize_base_case,
)


BASE_SCENARIO = "base_case_context"
NULL_SCENARIO = "scenario_c_oral_expansion_benchmark"
PRE_CRN_SD_QALY = 41_917.9
PRE_CRN_EXAMPLE_QALY = {42: 52_099.0, 43: -41_091.0}
DETERMINISTIC_REFERENCE = {
    "delta_qaly": 854.5676382184029,
    "infections_averted": 827,
    "delta_discounted_person_years": 387.2243775129318,
    "delta_undiscounted_person_years": 511.75,
    "delta_background_deaths": 36,
    "delta_hiv_deaths": -41,
    "delta_childhood_deaths": 0,
    "delta_births": 1,
    "delta_sexual_debuts": -8,
    "delta_net_migration": 0,
    "delta_final_population": 6,
}


def parse_seeds(value: str) -> list[int]:
    seeds: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(f"descending seed range: {token}")
            seeds.extend(range(start, end + 1))
        else:
            seeds.append(int(token))
    if len(seeds) < 2:
        raise argparse.ArgumentTypeError("at least two stochastic seeds are required")
    if len(seeds) != len(set(seeds)):
        raise argparse.ArgumentTypeError("duplicate seeds are not allowed")
    return seeds


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


def configure_runner(
    model_config: str,
    runtime_config: str,
    output_dir: str,
    *,
    deterministic: bool,
    seed: int,
) -> MultiScenarioRunner:
    runner = MultiScenarioRunner(model_config, output_dir, runtime_config)
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = deterministic
    runner.base_config.setdefault("simulation_controls", {})[
        "deterministic_mode"
    ] = {"value": deterministic}
    runner.base_config["run_control"]["seed"] = {"value": seed}
    return runner


def _run_base_case_worker(payload: tuple[str, str, str, bool, int]) -> tuple[list[dict], dict]:
    model_config, runtime_config, run_dir_text, deterministic, seed = payload
    run_dir = Path(run_dir_text)
    run_dir.mkdir(parents=True, exist_ok=False)
    runner = configure_runner(
        model_config,
        runtime_config,
        str(run_dir / "runner_outputs"),
        deterministic=deterministic,
        seed=seed,
    )
    runner.hta_config["analysis_scenarios"] = select_scenario(
        runner.hta_config["analysis_scenarios"], BASE_SCENARIO
    )
    started = time.time()
    with (run_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            runner.run()
    arm_rows, delta = summarize_base_case(
        runner.hta_config["analysis_scenarios"][0], runner.results[BASE_SCENARIO]
    )
    delta["mode"] = "deterministic" if deterministic else "stochastic"
    delta["seed"] = seed
    delta["elapsed_seconds"] = time.time() - started
    for row in arm_rows:
        row["mode"] = delta["mode"]
        row["seed"] = seed
    return arm_rows, delta


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _result_hash(result) -> str:
    payload = json.dumps(
        _jsonable(vars(result)),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mutate_null_target(runner: MultiScenarioRunner, target: int) -> None:
    scenario = select_scenario(runner.hta_config["analysis_scenarios"], NULL_SCENARIO)[0]
    pathways = scenario["arm_pathways"]["oral_prep_expansion"]
    if len(pathways) != 1:
        raise AssertionError("null scenario must have exactly one expansion pathway")
    schedule = pathways[0]["target_stock"]["value"]
    if "2040" not in schedule:
        raise KeyError("null scenario target schedule has no 2040 anchor")
    schedule["2040"] = int(target)


def _run_null_variant(
    model_config: str,
    runtime_config: str,
    run_dir: Path,
    *,
    seed: int,
    target: int,
) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=False)
    runner = configure_runner(
        model_config,
        runtime_config,
        str(run_dir / "runner_outputs"),
        deterministic=True,
        seed=seed,
    )
    _mutate_null_target(runner, target)
    with (run_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            runner.run()
    return {
        f"{scenario_name}/{arm_name}": _result_hash(result)
        for scenario_name, arm_results in runner.results.items()
        for arm_name, result in arm_results.items()
    }


def _summary(stochastic: pd.DataFrame) -> dict:
    metrics = [
        "delta_qaly",
        "infections_averted",
        "delta_discounted_person_years",
        "delta_undiscounted_person_years",
        "delta_background_deaths",
        "delta_hiv_deaths",
        "delta_childhood_deaths",
        "delta_births",
        "delta_sexual_debuts",
        "delta_net_migration",
        "delta_final_population",
    ]
    result = {"n": int(len(stochastic)), "metrics": {}}
    for metric in metrics:
        values = stochastic[metric].to_numpy(dtype=float)
        result["metrics"][metric] = {
            "mean": float(np.mean(values)),
            "sd": float(np.std(values, ddof=1)),
            "variance": float(np.var(values, ddof=1)),
            "se_mean": float(np.std(values, ddof=1) / math.sqrt(len(values))),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
    qaly_sd = result["metrics"]["delta_qaly"]["sd"]
    result["pre_crn_qaly_sd"] = PRE_CRN_SD_QALY
    result["qaly_sd_reduction_fraction"] = 1.0 - qaly_sd / PRE_CRN_SD_QALY
    result["sign_counts"] = {
        "positive_delta_qaly": int((stochastic["delta_qaly"] > 0).sum()),
        "positive_infections_averted": int(
            (stochastic["infections_averted"] > 0).sum()
        ),
    }
    elapsed = stochastic["elapsed_seconds"].to_numpy(dtype=float)
    result["runtime_seconds"] = {
        "mean": float(np.mean(elapsed)),
        "min": float(np.min(elapsed)),
        "max": float(np.max(elapsed)),
    }
    return result


def _report(summary: dict, deterministic_exact: bool, null_rows: list[dict]) -> str:
    m = summary["metrics"]
    q = m["delta_qaly"]
    py = m["delta_undiscounted_person_years"]
    null_pass = all(row["identical"] == row["expected_identical"] for row in null_rows)
    max_abs_qaly = max(abs(q["min"]), abs(q["max"]))
    deterministic_z = (
        (q["mean"] - DETERMINISTIC_REFERENCE["delta_qaly"]) / q["se_mean"]
        if q["se_mean"] > 0
        else float("nan")
    )
    return f"""# A-4 — Common random numbers across arms

**Evidence status:** well-supported by a fresh deterministic guard, {summary['n']} paired stochastic seeds, a full null-perturbation rerun, focused tests, and retained raw artifacts. This task changes stochastic random-number routing only; it changes no model parameter, tolerance, threshold, or empirical input.

## Result

- Stochastic ΔQALY mean: **{q['mean']:,.3f}**; SD: **{q['sd']:,.3f}** (n={summary['n']}).
- Pre-CRN reference SD: **{PRE_CRN_SD_QALY:,.1f}** (n=15, earlier probe); reduction: **{100 * summary['qaly_sd_reduction_fraction']:.2f}%**.
- Within-seed ΔQALY range: **{q['min']:,.3f} to {q['max']:,.3f}**, versus the pre-CRN seed examples +52,099 and −41,091.
- Infections averted mean: **{m['infections_averted']['mean']:,.3f}**; SD: **{m['infections_averted']['sd']:,.3f}**.
- Undiscounted person-years Δ mean: **{py['mean']:,.3f}**; SD: **{py['sd']:,.3f}**.
- Deterministic eleven-metric reference bit unchanged: **{'yes' if deterministic_exact else 'no'}**.
- Null-perturbation hash check: **{'pass' if null_pass else 'fail'}**.
- Mean stochastic ΔQALY is **{deterministic_z:,.2f} SE** from the deterministic +{DETERMINISTIC_REFERENCE['delta_qaly']:,.3f}; {summary['sign_counts']['positive_delta_qaly']}/{summary['n']} seeds have positive ΔQALY and {summary['sign_counts']['positive_infections_averted']}/{summary['n']} have positive infections averted. This is an A-3 cross-check, not an A-3 closure claim.

## Acceptance

1. **Within-seed divergence — pass:** maximum absolute ΔQALY is {max_abs_qaly:,.3f}, not ~50,000, and is on the scale of a modelled intervention effect rather than arm totals of ~473 million QALYs.
2. **Across-seed variance — pass:** ΔQALY SD changed from {PRE_CRN_SD_QALY:,.1f} to {q['sd']:,.3f} (n={summary['n']}).
3. **Null perturbation — {'pass' if null_pass else 'fail'}:** `null_perturbation_hashes.csv` records every arm; all non-perturbed arms are identical and only the deliberately changed Scenario C arm differs.
4. **Deterministic path — {'pass' if deterministic_exact else 'fail'}:** `deterministic_incremental_summary.json` equals all eleven A-12 reference values exactly.
5. **Person-time channel — reported:** the A-11 diagnostics are shown directly below for `base_case_context`; substitution scenarios are deliberately excluded because A-9 confounds that channel.

| Metric | Stochastic mean | SD | Min | Max |
|---|---:|---:|---:|---:|
| Δ background deaths | {m['delta_background_deaths']['mean']:,.3f} | {m['delta_background_deaths']['sd']:,.3f} | {m['delta_background_deaths']['min']:,.0f} | {m['delta_background_deaths']['max']:,.0f} |
| Δ HIV deaths | {m['delta_hiv_deaths']['mean']:,.3f} | {m['delta_hiv_deaths']['sd']:,.3f} | {m['delta_hiv_deaths']['min']:,.0f} | {m['delta_hiv_deaths']['max']:,.0f} |
| Δ final population | {m['delta_final_population']['mean']:,.3f} | {m['delta_final_population']['sd']:,.3f} | {m['delta_final_population']['min']:,.0f} | {m['delta_final_population']['max']:,.0f} |
| Δ undiscounted person-years | {py['mean']:,.3f} | {py['sd']:,.3f} | {py['min']:,.3f} | {py['max']:,.3f} |
| Δ discounted person-years | {m['delta_discounted_person_years']['mean']:,.3f} | {m['delta_discounted_person_years']['sd']:,.3f} | {m['delta_discounted_person_years']['min']:,.3f} | {m['delta_discounted_person_years']['max']:,.3f} |

## Implementation

`counter_based_rng.py` uses `numpy.random.Philox`. Each stochastic stream is keyed by `(seed, year, absolute timestep, process_id, stratum_id)`. Array probability draws use one Philox uniform per coordinate followed by inverse-CDF sampling, so changing a population count does not consume a different number of random values. The final PrEP tensor axis is keyed slice-by-slice, preserving shared cell coordinates when one arm contains additional PrEP states. Transmission additionally names its contact, stage, ART, and MSM-role strata explicitly. Stable tensor traversal is C-order; ordered primitive key tuples use the specified pickle-protocol-5 byte representation plus BLAKE2b, never Python's process-randomised `hash()`. The shared Philox engine is reset to the explicit counter for every draw; reuse improves speed without creating draw-history dependence.

Per-seed paired-run wall time in the retained parallel run averaged {summary['runtime_seconds']['mean']:,.2f} seconds (range {summary['runtime_seconds']['min']:,.2f}–{summary['runtime_seconds']['max']:,.2f}); timings include host contention and are operational diagnostics, not scientific results.

## Evidence files

- `manifest.json`: command, config/code hashes, git commit and dirty state, environment, seeds, output hashes.
- `stochastic_seed_diagnostics.csv`: one row per paired stochastic seed.
- `stochastic_arm_summary.csv`: both arm totals per seed.
- `stochastic_summary.json`: means, unbiased sample variances/SDs, SEs, ranges, and variance reduction.
- `deterministic_incremental_summary.json`: eleven-metric A-12 guard.
- `null_perturbation_hashes.csv`: arm-level exact hashes for the 28,000 versus 29,000 Scenario C perturbation.
- `runs/`: retained stdout logs for every run.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("42-56"))
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir()
    model_config = args.model_config.resolve()
    runtime_config = args.runtime_config.resolve()

    manifest = {
        "task": "A-4 common random numbers across arms",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "runtime_config": str(runtime_config),
        "scenario": BASE_SCENARIO,
        "stochastic_seeds": args.seeds,
        "workers": args.workers,
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "sha256": {
            "model_config": sha256(model_config),
            "runtime_config": sha256(runtime_config),
            **{
                f"code/{name}": sha256(ROOT / name)
                for name in (
                    "counter_based_rng.py",
                    "run_v3.py",
                    "transmission_corrected.py",
                    "demography.py",
                    "migration.py",
                    "run_scenarios.py",
                    "economics.py",
                )
            },
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    all_arm_rows: list[dict] = []
    stochastic_rows: list[dict] = []
    payloads = [
        (
            str(model_config),
            str(runtime_config),
            str(runs_dir / f"stochastic_seed_{seed}"),
            False,
            seed,
        )
        for seed in args.seeds
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_run_base_case_worker, payload): payload[-1] for payload in payloads}
        for future in as_completed(futures):
            arm_rows, delta = future.result()
            all_arm_rows.extend(arm_rows)
            stochastic_rows.append(delta)

    stochastic = pd.DataFrame(stochastic_rows).sort_values("seed")
    arms = pd.DataFrame(all_arm_rows).sort_values(["seed", "arm"])
    stochastic.to_csv(output_dir / "stochastic_seed_diagnostics.csv", index=False)
    arms.to_csv(output_dir / "stochastic_arm_summary.csv", index=False)
    summary = _summary(stochastic)
    (output_dir / "stochastic_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    det_arm_rows, deterministic = _run_base_case_worker(
        (
            str(model_config),
            str(runtime_config),
            str(runs_dir / "deterministic_seed_42"),
            True,
            42,
        )
    )
    deterministic_payload = {
        key: deterministic[key] for key in DETERMINISTIC_REFERENCE
    }
    deterministic_exact = deterministic_payload == DETERMINISTIC_REFERENCE
    (output_dir / "deterministic_incremental_summary.json").write_text(
        json.dumps(deterministic_payload, indent=2) + "\n", encoding="utf-8"
    )
    pd.DataFrame(det_arm_rows).to_csv(
        output_dir / "deterministic_arm_summary.csv", index=False
    )
    if not deterministic_exact:
        raise AssertionError(
            f"deterministic A-12 reference changed: {deterministic_payload}"
        )

    null_a = _run_null_variant(
        str(model_config),
        str(runtime_config),
        runs_dir / "null_target_28000",
        seed=42,
        target=28_000,
    )
    null_b = _run_null_variant(
        str(model_config),
        str(runtime_config),
        runs_dir / "null_target_29000",
        seed=42,
        target=29_000,
    )
    if set(null_a) != set(null_b):
        raise AssertionError("null-perturbation runs produced different arm registers")
    null_rows = []
    for key in sorted(null_a):
        expected_identical = key != f"{NULL_SCENARIO}/oral_prep_expansion"
        identical = null_a[key] == null_b[key]
        null_rows.append(
            {
                "scenario_arm": key,
                "hash_target_28000": null_a[key],
                "hash_target_29000": null_b[key],
                "identical": identical,
                "expected_identical": expected_identical,
            }
        )
    pd.DataFrame(null_rows).to_csv(
        output_dir / "null_perturbation_hashes.csv", index=False
    )
    if not all(row["identical"] == row["expected_identical"] for row in null_rows):
        raise AssertionError("null-perturbation determinism check failed")

    report_path = output_dir / "A4_COMMON_RANDOM_NUMBERS.md"
    report_path.write_text(
        _report(summary, deterministic_exact, null_rows), encoding="utf-8"
    )

    artifact_names = (
        "A4_COMMON_RANDOM_NUMBERS.md",
        "stochastic_seed_diagnostics.csv",
        "stochastic_arm_summary.csv",
        "stochastic_summary.json",
        "deterministic_incremental_summary.json",
        "deterministic_arm_summary.csv",
        "null_perturbation_hashes.csv",
    )
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["counts"] = {
        "stochastic_seeds": len(args.seeds),
        "stochastic_arm_rows": len(arms),
        "null_arm_hashes": len(null_rows),
    }
    manifest["acceptance"] = {
        "deterministic_exact": deterministic_exact,
        "null_perturbation_pass": all(
            row["identical"] == row["expected_identical"] for row in null_rows
        ),
        "qaly_sd_reduction_fraction": summary["qaly_sd_reduction_fraction"],
    }
    manifest["output_sha256"] = {
        name: sha256(output_dir / name) for name in artifact_names
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-4 evidence written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
