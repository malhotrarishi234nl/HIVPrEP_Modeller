#!/usr/bin/env python3
"""Run A-9's process-by-process substitution-switch-grid decomposition.

This is an opt-in measurement runner.  It reuses A-20's restoring
``ProcessModeSwitch`` and calls A-7's grid runner for every payload; it does not
change production behavior or copy the switch-grid model computation.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import platform
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_a7_decision_comparison_decomposition as a7  # noqa: E402
from tools.a20_process_decomposition import (  # noqa: E402
    PROCESS_FAMILIES,
    ProcessModeSwitch,
)
from tools.run_a20_process_decomposition import inventory_sites  # noqa: E402


SEEDS = tuple(range(42, 57))
SWITCHES = a7.EXPECTED_SWITCHES
SINGLE_PROCESS_ARMS = {
    "transmission": frozenset({"transmission"}),
    "disease_progression": frozenset({"progression"}),
    "diagnosis": frozenset({"diagnosis"}),
    "art": frozenset({"art"}),
    "hiv_mortality": frozenset({"hiv_mortality"}),
    "background_mortality": frozenset({"background_mortality"}),
    "births": frozenset({"births"}),
    "migration": frozenset({"migration"}),
    "ageing_sexual_debut": frozenset({"ageing_sexual_debut"}),
    "prep": frozenset({"prep"}),
}
REFERENCE_ARMS = {
    "all_deterministic": frozenset(),
    "all_stochastic": frozenset(PROCESS_FAMILIES),
}
METRICS = (
    "infections_averted",
    "delta_qaly",
    "delta_person_years",
    "delta_discounted_person_years",
    "hiv_deaths_averted",
    "aids_deaths_averted",
    "delta_new_aids_cases",
    "delta_new_diagnoses",
    "delta_art_initiations",
    "delta_background_deaths",
    "final_total_population_delta",
)
A15_DIR = ROOT / "outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351"


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


def monotone_non_decreasing(values: Sequence[float]) -> bool:
    values = [float(value) for value in values]
    return all(right >= left for left, right in zip(values, values[1:]))


def increment_rows(
    frame: pd.DataFrame, *, arm: str, metric: str
) -> list[dict[str, Any]]:
    """Compute within-seed adjacent increments and their sampling resolution."""
    pivot = frame.pivot(index="seed", columns="switch_percent", values=metric)
    if list(pivot.columns) != list(SWITCHES):
        raise AssertionError(f"{arm}/{metric} does not contain the full switch grid")
    rows = []
    for left, right in zip(SWITCHES, SWITCHES[1:]):
        values = (pivot[right] - pivot[left]).to_numpy(dtype=float)
        n = int(values.size)
        mean = float(values.mean())
        sd = float(np.std(values, ddof=1)) if n > 1 else float("nan")
        se = sd / math.sqrt(n) if n > 1 else float("nan")
        if se == 0.0:
            ratio = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        else:
            ratio = mean / se if math.isfinite(se) else float("nan")
        rows.append(
            {
                "arm": arm,
                "metric": metric,
                "from_switch": left,
                "to_switch": right,
                "n": n,
                "mean_increment": mean,
                "sd_increment": sd,
                "se_increment": se,
                "increment_over_se": ratio,
                "exceeds_2se": bool(mean > 2.0 * se) if math.isfinite(se) else False,
            }
        )
    return rows


def repair_verdict(mean_values: Sequence[float], increments: Sequence[dict]) -> dict:
    monotone = monotone_non_decreasing(mean_values)
    resolved = bool(increments) and all(bool(row["exceeds_2se"]) for row in increments)
    finite_ratios = [float(row["increment_over_se"]) for row in increments]
    return {
        "monotone_non_decreasing": monotone,
        "all_increments_exceed_2se": resolved,
        "minimum_increment_over_se": min(finite_ratios, default=float("nan")),
        "meets_repair_criterion": monotone and resolved,
    }


def _grid_rows(run_dir: Path, *, arm: str, seed: int) -> list[dict[str, Any]]:
    decomposition = pd.read_csv(run_dir / "scenario_decomposition.csv")
    flows = pd.read_csv(run_dir / "event_flow_totals_long.csv")
    flow_lookup = {
        (int(row["switch_percent"]), str(row["flow"])): float(
            row["delta_intervention_minus_reference"]
        )
        for _, row in flows.iterrows()
    }
    rows = []
    for _, row in decomposition.iterrows():
        switch = int(row["switch_percent"])
        rows.append(
            {
                "arm": arm,
                "seed": int(seed),
                "switch_percent": switch,
                "infections_averted": float(row["infections_averted"]),
                "delta_qaly": float(row["delta_qaly"]),
                "delta_person_years": float(row["delta_person_years"]),
                "delta_discounted_person_years": float(
                    row["delta_discounted_person_years"]
                ),
                "hiv_deaths_averted": -flow_lookup[(switch, "hiv_deaths")],
                "aids_deaths_averted": -flow_lookup[(switch, "aids_deaths")],
                "delta_new_aids_cases": flow_lookup[(switch, "new_aids_cases")],
                "delta_new_diagnoses": flow_lookup[(switch, "new_diagnoses")],
                "delta_art_initiations": flow_lookup[(switch, "art_initiations")],
                "delta_background_deaths": flow_lookup[(switch, "background_deaths")],
                "final_total_population_delta": float(row["final_total_population_delta"]),
            }
        )
    rows.sort(key=lambda item: item["switch_percent"])
    if [row["switch_percent"] for row in rows] != list(SWITCHES):
        raise AssertionError(f"{arm}/seed {seed} returned the wrong switch grid")
    return rows


def _worker(payload: dict[str, Any]) -> dict[str, Any]:
    arm = str(payload["arm"])
    seed = int(payload["seed"])
    forced = frozenset(payload["forced"])
    run_dir = Path(payload["run_dir"])
    metadata_path = run_dir / "a9_worker_metadata.json"
    if metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8"))

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
    output = io.StringIO()
    started = time.time()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with ProcessModeSwitch(forced) as switch:
                status = a7.main()
    finally:
        sys.argv = saved_argv
    if status != 0:
        raise RuntimeError(f"A-7 exited {status} for {arm}/seed {seed}")
    metadata = {
        "arm": arm,
        "seed": seed,
        "forced_families": sorted(forced),
        "elapsed_seconds": time.time() - started,
        "coverage": dict(switch.coverage),
        "rng_requests": dict(switch.rng_requests),
        "surface_calls": dict(switch.surface_calls),
        "grid": _grid_rows(run_dir, arm=arm, seed=seed),
        "outer_stdout": output.getvalue(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def _run_payloads(payloads: list[dict[str, Any]], max_workers: int) -> list[dict]:
    results = []
    with ProcessPoolExecutor(max_workers=min(max_workers, len(payloads))) as pool:
        futures = {pool.submit(_worker, payload): payload for payload in payloads}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            print(
                f"A-9 completed {index}/{len(payloads)}: "
                f"{result['arm']} seed={result['seed']} "
                f"({result['elapsed_seconds']:.1f}s)",
                flush=True,
            )
    return results


def _payload(
    *, arm: str, seed: int, forced: frozenset[str], runs_dir: Path,
    model_config: Path, hta_config: Path
) -> dict[str, Any]:
    return {
        "arm": arm,
        "seed": int(seed),
        "forced": sorted(forced),
        "run_dir": str(runs_dir / arm / f"seed_{seed}"),
        "model_config": str(model_config),
        "hta_config": str(hta_config),
    }


def _deterministic_controls(det_grid: pd.DataFrame) -> pd.DataFrame:
    reference = pd.read_csv(A15_DIR / "level0_vs_a7_reference.csv")
    observed = {
        (int(row["switch_percent"]), metric): float(row[metric])
        for _, row in det_grid.iterrows()
        for metric in METRICS
    }
    rows = []
    for _, row in reference.iterrows():
        key = (int(row["switch_percent"]), str(row["metric"]))
        value = observed[key]
        rows.append(
            {
                "switch_percent": key[0],
                "metric": key[1],
                "reference_source": row["reference_source"],
                "reference": float(row["reference"]),
                "observed": value,
                "difference": value - float(row["reference"]),
                "exact_match": value == float(row["reference"]),
            }
        )
    result = pd.DataFrame(rows)
    if len(result) != 25 or not result["exact_match"].all():
        raise AssertionError("all-deterministic reference failed A-15's 25 exact controls")
    return result


def _stochastic_controls(stochastic_grid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    prior = pd.read_csv(A15_DIR / "stochastic_grid_by_seed.csv")
    exact_rows = []
    for metric in METRICS:
        merged = stochastic_grid[["seed", "switch_percent", metric]].merge(
            prior[["seed", "switch_percent", metric]],
            on=["seed", "switch_percent"], suffixes=("_observed", "_a15"),
        )
        for _, row in merged.iterrows():
            observed = float(row[f"{metric}_observed"])
            reference = float(row[f"{metric}_a15"])
            exact_rows.append(
                {
                    "seed": int(row["seed"]),
                    "switch_percent": int(row["switch_percent"]),
                    "metric": metric,
                    "a15": reference,
                    "observed": observed,
                    "difference": observed - reference,
                    "exact_match": observed == reference,
                }
            )
    exact = pd.DataFrame(exact_rows)
    if len(exact) != len(SEEDS) * len(SWITCHES) * len(METRICS) or not exact["exact_match"].all():
        raise AssertionError("all-stochastic arm does not exactly reproduce A-15 by seed")

    prior_summary = pd.read_csv(A15_DIR / "stochastic_grid_summary.csv")
    prior_summary = prior_summary[prior_summary["metric"] == "infections_averted"]
    summary_rows = []
    for _, row in prior_summary.iterrows():
        switch = int(row["switch_percent"])
        observed = float(
            stochastic_grid.loc[
                stochastic_grid["switch_percent"] == switch, "infections_averted"
            ].mean()
        )
        difference = observed - float(row["mean"])
        summary_rows.append(
            {
                "switch_percent": switch,
                "a15_mean": float(row["mean"]),
                "a15_se": float(row["se_mean"]),
                "observed_mean": observed,
                "difference": difference,
                "within_a15_se": abs(difference) <= float(row["se_mean"]),
            }
        )
    summary = pd.DataFrame(summary_rows)
    if not summary["within_a15_se"].all():
        raise AssertionError("all-stochastic mean grid is outside A-15's stated SE")
    return exact, summary


def _z_rows(grid: pd.DataFrame, stochastic: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    reference_means = stochastic.groupby("switch_percent")[list(METRICS)].mean()
    for arm, arm_frame in grid.groupby("arm"):
        for metric in ("infections_averted", "hiv_deaths_averted"):
            for switch in SWITCHES:
                candidate = arm_frame[arm_frame["switch_percent"] == switch]
                reference = stochastic[stochastic["switch_percent"] == switch]
                if arm == "all_deterministic":
                    difference = float(candidate[metric].iloc[0] - reference[metric].mean())
                    se = float(reference[metric].std(ddof=1) / math.sqrt(len(reference)))
                    method = "deterministic minus stochastic mean / stochastic mean SE"
                else:
                    paired = candidate[["seed", metric]].merge(
                        reference[["seed", metric]], on="seed", suffixes=("_arm", "_stochastic")
                    )
                    differences = (
                        paired[f"{metric}_arm"] - paired[f"{metric}_stochastic"]
                    ).to_numpy(dtype=float)
                    difference = float(differences.mean())
                    se = float(np.std(differences, ddof=1) / math.sqrt(len(differences)))
                    method = "CRN-paired mean difference / paired SE"
                if se == 0.0:
                    z = 0.0 if difference == 0.0 else math.inf
                else:
                    z = abs(difference) / se
                rows.append(
                    {
                        "arm": arm,
                        "switch_percent": switch,
                        "metric": metric,
                        "mean_difference": difference,
                        "se_difference": se,
                        "z_abs": z,
                        "method": method,
                    }
                )
    return rows


def _analyse(grid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    for (arm, switch), group in grid.groupby(["arm", "switch_percent"]):
        for metric in METRICS:
            values = group[metric].to_numpy(dtype=float)
            n = len(values)
            sd = float(np.std(values, ddof=1)) if n > 1 else float("nan")
            summary_rows.append(
                {
                    "arm": arm,
                    "switch_percent": int(switch),
                    "metric": metric,
                    "n": n,
                    "mean": float(values.mean()),
                    "sd": sd,
                    "se_mean": sd / math.sqrt(n) if n > 1 else float("nan"),
                    "min": float(values.min()),
                    "max": float(values.max()),
                }
            )
    grid_summary = pd.DataFrame(summary_rows)
    increments = []
    for arm, group in grid.groupby("arm"):
        increments.extend(increment_rows(group, arm=arm, metric="infections_averted"))
        increments.extend(increment_rows(group, arm=arm, metric="hiv_deaths_averted"))
    increment_frame = pd.DataFrame(increments)

    stochastic = grid[grid["arm"] == "all_stochastic"]
    z_frame = pd.DataFrame(_z_rows(grid, stochastic))
    arm_rows = []
    for arm, group in grid.groupby("arm"):
        means = group.groupby("switch_percent")[["infections_averted", "hiv_deaths_averted"]].mean()
        infection_increments = [
            row for row in increments if row["arm"] == arm and row["metric"] == "infections_averted"
        ]
        death_increments = [
            row for row in increments if row["arm"] == arm and row["metric"] == "hiv_deaths_averted"
        ]
        infection_verdict = repair_verdict(means["infections_averted"].tolist(), infection_increments)
        death_verdict = repair_verdict(means["hiv_deaths_averted"].tolist(), death_increments)
        z_arm = z_frame[z_frame["arm"] == arm]
        arm_rows.append(
            {
                "arm": arm,
                "n": int(group["seed"].nunique()),
                "infections_grid": ";".join(f"{value:g}" for value in means["infections_averted"]),
                "infections_monotone": infection_verdict["monotone_non_decreasing"],
                "infections_min_increment_over_se": infection_verdict["minimum_increment_over_se"],
                "infections_repairs": infection_verdict["meets_repair_criterion"],
                "hiv_deaths_grid": ";".join(f"{value:g}" for value in means["hiv_deaths_averted"]),
                "hiv_deaths_monotone": death_verdict["monotone_non_decreasing"],
                "hiv_deaths_min_increment_over_se": death_verdict["minimum_increment_over_se"],
                "hiv_deaths_repairs": death_verdict["meets_repair_criterion"],
                "negative_infections_levels": int((means["infections_averted"] < 0).sum()),
                "mean_abs_z_infections_vs_stochastic": float(
                    z_arm.loc[z_arm["metric"] == "infections_averted", "z_abs"].mean()
                ),
                "mean_abs_z_hiv_deaths_vs_stochastic": float(
                    z_arm.loc[z_arm["metric"] == "hiv_deaths_averted", "z_abs"].mean()
                ),
            }
        )
    return grid_summary, increment_frame, z_frame, pd.DataFrame(arm_rows)


def _write_coverage(results: list[dict], raw_dir: Path) -> None:
    rows = []
    for result in results:
        forced = set(result["forced_families"])
        for family in PROCESS_FAMILIES:
            rows.append(
                {
                    "arm": result["arm"],
                    "seed": result["seed"],
                    "process_family": family,
                    "forced_stochastic": family in forced,
                    "process_calls": int(result["coverage"].get(family, 0)),
                    "rng_requests": int(result["rng_requests"].get(family, 0)),
                }
            )
    coverage = pd.DataFrame(rows)
    coverage.to_csv(raw_dir / "coverage_audit_per_run.csv", index=False)
    reachability = []
    for arm, forced in SINGLE_PROCESS_ARMS.items():
        family = next(iter(forced))
        values = coverage[
            (coverage["arm"] == arm) & (coverage["process_family"] == family)
        ]["process_calls"]
        reachable = len(values) == len(SEEDS) and int(values.min()) > 0
        reachability.append(
            {
                "arm": arm,
                "process_family": family,
                "n_runs": len(values),
                "min_calls_per_run": int(values.min()) if len(values) else 0,
                "max_calls_per_run": int(values.max()) if len(values) else 0,
                "reachable_on_every_run": reachable,
                "ownership": (
                    "runtime process_id through shared _draw_events_for/_process_rng"
                    if family in {"diagnosis", "art", "progression", "hiv_mortality", "prep"}
                    else "dedicated deterministic branch surface"
                ),
            }
        )
    reachability_frame = pd.DataFrame(reachability)
    reachability_frame.to_csv(raw_dir / "arm_reachability.csv", index=False)
    if not reachability_frame["reachable_on_every_run"].all():
        raise AssertionError("at least one process switch was not reached")

    sites = inventory_sites()
    if len(sites) != 31:
        raise AssertionError(f"expected 31 current branch sites, found {len(sites)}")
    for site in sites:
        key = (
            f"{site['module']}:{site['function']}:{site['line']}"
            if site["function"] == "apply_mortality"
            else f"{site['module']}:{site['function']}"
        )
        site["maximum_measured_calls"] = max(
            int(result["surface_calls"].get(key, 0)) for result in results
        )
        site["config_status"] = "live" if site["maximum_measured_calls"] else "dormant"
    pd.DataFrame(sites).to_csv(raw_dir / "switch_surface_inventory.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-config", type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--hta-config", type=Path, default=ROOT / "configs/hta_runtime_config.json"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=7)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    runs_dir = output_dir / "runs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    provenance_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    if (raw_dir / "findings.json").exists():
        raise FileExistsError(f"refusing to overwrite completed A-9 run: {output_dir}")

    model_config = args.model_config.resolve()
    hta_config = args.hta_config.resolve()
    status_before = git_text("status", "--short")
    diff_before = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=ROOT,
        check=True, capture_output=True,
    ).stdout
    (provenance_dir / "git_status_short_before.txt").write_text(
        status_before + ("\n" if status_before else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff_before.patch").write_bytes(diff_before)

    started = time.time()
    # References run first.  Any failure stops before the process sweep.
    reference_payloads = [
        _payload(
            arm="all_deterministic", seed=SEEDS[0], forced=REFERENCE_ARMS["all_deterministic"],
            runs_dir=runs_dir, model_config=model_config, hta_config=hta_config,
        )
    ] + [
        _payload(
            arm="all_stochastic", seed=seed, forced=REFERENCE_ARMS["all_stochastic"],
            runs_dir=runs_dir, model_config=model_config, hta_config=hta_config,
        )
        for seed in SEEDS
    ]
    print("A-9 Step 0: running mandatory reference controls", flush=True)
    reference_results = _run_payloads(reference_payloads, args.max_workers)
    reference_grid = pd.DataFrame(
        [row for result in reference_results for row in result["grid"]]
    )
    deterministic_controls = _deterministic_controls(
        reference_grid[reference_grid["arm"] == "all_deterministic"]
    )
    stochastic_exact, stochastic_mean = _stochastic_controls(
        reference_grid[reference_grid["arm"] == "all_stochastic"]
    )
    deterministic_controls.to_csv(raw_dir / "deterministic_reference_controls.csv", index=False)
    stochastic_exact.to_csv(raw_dir / "stochastic_reference_exact_controls.csv", index=False)
    stochastic_mean.to_csv(raw_dir / "stochastic_reference_mean_controls.csv", index=False)
    print("A-9 Step 0 controls passed exactly; starting ten process arms", flush=True)

    process_payloads = [
        _payload(
            arm=arm, seed=seed, forced=forced, runs_dir=runs_dir,
            model_config=model_config, hta_config=hta_config,
        )
        for arm, forced in SINGLE_PROCESS_ARMS.items()
        for seed in SEEDS
    ]
    process_results = _run_payloads(process_payloads, args.max_workers)
    results = reference_results + process_results
    grid = pd.DataFrame([row for result in results for row in result["grid"]])
    grid.sort_values(["arm", "seed", "switch_percent"]).to_csv(
        raw_dir / "grid_by_seed.csv", index=False
    )
    _write_coverage(results, raw_dir)
    grid_summary, increments, z_frame, arm_summary = _analyse(grid)
    grid_summary.to_csv(raw_dir / "grid_summary.csv", index=False)
    increments.to_csv(raw_dir / "increment_resolution.csv", index=False)
    z_frame.to_csv(raw_dir / "z_against_stochastic.csv", index=False)
    arm_summary.to_csv(raw_dir / "arm_summary.csv", index=False)

    process_summary = arm_summary[arm_summary["arm"].isin(SINGLE_PROCESS_ARMS)]
    repairing = process_summary.loc[process_summary["infections_repairs"], "arm"].tolist()
    inconsistent = process_summary.loc[
        process_summary["infections_repairs"] != process_summary["hiv_deaths_repairs"], "arm"
    ].tolist()
    findings = {
        "status": "step1_complete_trace_required" if repairing else "complete_no_arm_repairs",
        "n_seeds": len(SEEDS),
        "seeds": list(SEEDS),
        "switches": list(SWITCHES),
        "repairing_process_arms": repairing,
        "n_repairing_process_arms": len(repairing),
        "infection_mortality_inconsistent_arms": inconsistent,
        "all_deterministic_exact_25_of_25": True,
        "all_stochastic_exact_by_seed": True,
        "all_stochastic_within_a15_stated_se": True,
        "elapsed_seconds": time.time() - started,
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "task": "A-9 switch-grid process decomposition",
        "status": findings["status"],
        "command": shlex.join([sys.executable, *sys.argv]),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_status_short_before": status_before,
        "git_diff_sha256_before": hashlib.sha256(diff_before).hexdigest(),
        "model_config": str(model_config.relative_to(ROOT)),
        "hta_config": str(hta_config.relative_to(ROOT)),
        "seeds": list(SEEDS),
        "n_replicates_per_stochastic_arm": len(SEEDS),
        "switches": list(SWITCHES),
        "single_process_arms": {key: sorted(value) for key, value in SINGLE_PROCESS_ARMS.items()},
        "all_stochastic_families": list(PROCESS_FAMILIES),
        "grid_runner_reused": "tools/run_a7_decision_comparison_decomposition.py",
        "process_harness_reused": "tools/a20_process_decomposition.py",
        "a15_reference_dir": str(A15_DIR.relative_to(ROOT)),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                model_config, hta_config,
                ROOT / "configs/hta_costing_config.json",
                ROOT / "tools/run_a7_decision_comparison_decomposition.py",
                ROOT / "tools/a20_process_decomposition.py",
                ROOT / "tools/run_a20_process_decomposition.py",
                Path(__file__).resolve(),
                ROOT / "run_v3.py", ROOT / "run_scenarios.py",
                ROOT / "demography.py", ROOT / "migration.py",
                ROOT / "transmission_corrected.py", ROOT / "adverse_events.py",
            )
        },
        "findings": findings,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(findings, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
