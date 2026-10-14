"""Run Phase-2 task A-1: decompose incremental QALYs by health state.

The experiment is deliberately limited to ``base_case_context`` (oral PrEP
versus no PrEP).  It preserves the existing total-QALY calculation and reads
the additive diagnostic components emitted by ``EconomicsAccumulator``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from economics import QALY_COMPONENT_KEYS  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402


BASE_SCENARIO = "base_case_context"
REFERENCE_ARM = "no_prep"
INTERVENTION_ARM = "oral_prep"

# This partition sums exactly to total QALYs.  ``all_susceptible`` is reported
# separately because it is useful diagnostically but would double-count the
# first two rows if included in the partition.
PARTITION_COMPONENTS: Dict[str, tuple[str, ...]] = {
    "susceptible_no_prep": ("susceptible_no_prep",),
    "on_prep": (
        "susceptible_oral_prep",
        "susceptible_lenacapavir",
        "susceptible_lenacapavir_waning",
    ),
    "hiv_states": (
        "hiv_acute",
        "hiv_chronic_on_art",
        "hiv_chronic_off_art",
        "hiv_aids_on_art",
        "hiv_aids_off_art",
    ),
    "utility_decrements": (
        "utility_decrement_art_initiation",
        "utility_decrement_adverse_events",
    ),
}


def _parse_seeds(value: str) -> List[int]:
    """Parse comma-separated integers and inclusive ranges such as ``42-56``."""
    seeds: List[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(
                    f"seed range must be ascending, got {token!r}"
                )
            seeds.extend(range(start, end + 1))
        else:
            seeds.append(int(token))
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    if len(seeds) != len(set(seeds)):
        raise argparse.ArgumentTypeError("duplicate seeds are not allowed")
    return seeds


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(args: Iterable[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.rstrip()


def _component_totals(result) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for component in QALY_COMPONENT_KEYS:
        column = f"step_qaly_{component}_disc"
        missing_steps = [
            record.get("step")
            for record in result.timestep_records
            if column not in record
        ]
        if missing_steps:
            raise KeyError(
                f"{result.analysis_scenario}/{result.name} is missing {column} "
                f"at steps {missing_steps[:5]}"
            )
        totals[component] = math.fsum(
            float(record[column]) for record in result.timestep_records
        )
    component_sum = math.fsum(totals.values())
    if not math.isclose(
        component_sum,
        float(result.total_qalys_disc),
        rel_tol=1e-12,
        abs_tol=1e-6,
    ):
        raise AssertionError(
            f"QALY components do not reconcile for "
            f"{result.analysis_scenario}/{result.name}: components={component_sum:.12f}, "
            f"legacy total={result.total_qalys_disc:.12f}, "
            f"residual={component_sum - result.total_qalys_disc:.12f}"
        )
    return totals


def _run_one(
    *,
    model_config: Path,
    hta_config: Path,
    run_dir: Path,
    deterministic: bool,
    seed: int,
) -> tuple[dict, List[dict], List[dict]]:
    run_dir.mkdir(parents=True, exist_ok=False)
    runner_output = run_dir / "runner_outputs"
    log_path = run_dir / "runner_stdout.log"

    runner = MultiScenarioRunner(
        str(model_config),
        str(runner_output),
        str(hta_config),
    )
    matching = [
        scenario
        for scenario in runner.hta_config["analysis_scenarios"]
        if scenario["name"] == BASE_SCENARIO
    ]
    if len(matching) != 1:
        raise ValueError(
            f"expected exactly one {BASE_SCENARIO!r} scenario, found {len(matching)}"
        )
    runner.hta_config["analysis_scenarios"] = matching
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False

    runner.base_config["run_control"]["deterministic_mode"]["value"] = deterministic
    runner.base_config.setdefault("simulation_controls", {})[
        "deterministic_mode"
    ] = {"value": deterministic}
    runner.base_config["run_control"]["seed"] = {"value": seed}

    started = time.time()
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
            runner.run()
    elapsed = time.time() - started

    results = runner.results[BASE_SCENARIO]
    reference = results[REFERENCE_ARM]
    intervention = results[INTERVENTION_ARM]
    arm_rows: List[dict] = []
    arm_totals: Dict[str, Dict[str, float]] = {}
    for arm_name, result in (
        (REFERENCE_ARM, reference),
        (INTERVENTION_ARM, intervention),
    ):
        totals = _component_totals(result)
        arm_totals[arm_name] = totals
        arm_rows.extend(
            {
                "mode": "deterministic" if deterministic else "stochastic",
                "seed": seed,
                "arm": arm_name,
                "component": component,
                "qaly_disc": value,
            }
            for component, value in totals.items()
        )

    delta_components = {
        component: (
            arm_totals[INTERVENTION_ARM][component]
            - arm_totals[REFERENCE_ARM][component]
        )
        for component in QALY_COMPONENT_KEYS
    }
    delta_qaly = intervention.total_qalys_disc - reference.total_qalys_disc
    component_delta_sum = math.fsum(delta_components.values())
    if not math.isclose(
        component_delta_sum,
        delta_qaly,
        rel_tol=1e-12,
        abs_tol=1e-6,
    ):
        raise AssertionError(
            f"incremental components do not reconcile: components={component_delta_sum:.12f}, "
            f"legacy delta={delta_qaly:.12f}"
        )

    reference_person_years = math.fsum(
        float(record["total_population"]) * float(record["step_fraction"])
        for record in reference.timestep_records
    )
    intervention_person_years = math.fsum(
        float(record["total_population"]) * float(record["step_fraction"])
        for record in intervention.timestep_records
    )
    row = {
        "mode": "deterministic" if deterministic else "stochastic",
        "seed": seed,
        "elapsed_seconds": elapsed,
        "reference_total_qaly_disc": reference.total_qalys_disc,
        "intervention_total_qaly_disc": intervention.total_qalys_disc,
        "delta_qaly": delta_qaly,
        "component_delta_sum": component_delta_sum,
        "component_reconciliation_residual": component_delta_sum - delta_qaly,
        "infections_averted": (
            reference.cumul_new_infections - intervention.cumul_new_infections
        ),
        "reference_person_years": reference_person_years,
        "intervention_person_years": intervention_person_years,
        "delta_person_years": intervention_person_years - reference_person_years,
    }
    for component, value in delta_components.items():
        row[f"delta_{component}"] = value
    for partition_name, members in PARTITION_COMPONENTS.items():
        row[f"delta_partition_{partition_name}"] = math.fsum(
            delta_components[member] for member in members
        )
    row["delta_diagnostic_all_susceptible"] = (
        row["delta_partition_susceptible_no_prep"]
        + row["delta_partition_on_prep"]
    )
    if len(reference.timestep_records) != len(intervention.timestep_records):
        raise AssertionError("reference and intervention timestep counts differ")
    timestep_rows: List[dict] = []
    for reference_record, intervention_record in zip(
        reference.timestep_records, intervention.timestep_records
    ):
        if (
            reference_record["step"] != intervention_record["step"]
            or reference_record["year"] != intervention_record["year"]
        ):
            raise AssertionError("reference and intervention timestep alignment differs")
        timestep_row = {
            "mode": "deterministic" if deterministic else "stochastic",
            "seed": seed,
            "step": reference_record["step"],
            "year": reference_record["year"],
            "delta_total_population": (
                intervention_record["total_population"]
                - reference_record["total_population"]
            ),
            "delta_qaly_disc": (
                intervention_record["step_qalys_disc"]
                - reference_record["step_qalys_disc"]
            ),
        }
        for component in QALY_COMPONENT_KEYS:
            column = f"step_qaly_{component}_disc"
            timestep_row[f"delta_{component}"] = (
                intervention_record[column] - reference_record[column]
            )
        for partition_name, members in PARTITION_COMPONENTS.items():
            timestep_row[f"delta_partition_{partition_name}"] = math.fsum(
                timestep_row[f"delta_{member}"] for member in members
            )
        timestep_row["delta_diagnostic_all_susceptible"] = (
            timestep_row["delta_partition_susceptible_no_prep"]
            + timestep_row["delta_partition_on_prep"]
        )
        timestep_rows.append(timestep_row)
    return row, arm_rows, timestep_rows


def _variance_summary(
    runs: pd.DataFrame,
    columns: List[str],
    labels: List[str],
    deterministic_row: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stochastic = runs.loc[runs["mode"] == "stochastic"]
    if len(stochastic) < 2:
        raise ValueError("variance decomposition requires at least two stochastic seeds")

    values = stochastic[columns].to_numpy(dtype=float)
    covariance = np.cov(values, rowvar=False, ddof=1)
    covariance = np.atleast_2d(covariance)
    total = stochastic["delta_qaly"].to_numpy(dtype=float)
    total_variance = float(np.var(total, ddof=1))
    covariance_with_total = np.array(
        [np.cov(values[:, idx], total, ddof=1)[0, 1] for idx in range(len(columns))]
    )
    allocated = covariance.sum(axis=1)

    if not math.isclose(float(covariance.sum()), total_variance, rel_tol=1e-10, abs_tol=1e-4):
        raise AssertionError(
            "component covariance matrix does not reconcile to direct delta-QALY variance: "
            f"matrix={covariance.sum()}, direct={total_variance}"
        )
    if not np.allclose(allocated, covariance_with_total, rtol=1e-10, atol=1e-4):
        raise AssertionError("allocated covariance differs from covariance with total")

    rows = []
    n = len(stochastic)
    for idx, (column, label) in enumerate(zip(columns, labels)):
        component = stochastic[column].to_numpy(dtype=float)
        rows.append(
            {
                "component": label,
                "n": n,
                "deterministic_delta_qaly": float(deterministic_row[column]),
                "stochastic_mean_delta_qaly": float(np.mean(component)),
                "stochastic_sd_delta_qaly": float(np.std(component, ddof=1)),
                "stochastic_se_delta_qaly": float(np.std(component, ddof=1) / np.sqrt(n)),
                "standalone_sample_variance": float(covariance[idx, idx]),
                "covariance_with_total_delta_qaly": float(covariance_with_total[idx]),
                "allocated_variance_share_percent": (
                    float(100.0 * allocated[idx] / total_variance)
                    if total_variance != 0.0
                    else np.nan
                ),
            }
        )
    rows.append(
        {
            "component": "TOTAL_DELTA_QALY",
            "n": n,
            "deterministic_delta_qaly": float(deterministic_row["delta_qaly"]),
            "stochastic_mean_delta_qaly": float(np.mean(total)),
            "stochastic_sd_delta_qaly": float(np.std(total, ddof=1)),
            "stochastic_se_delta_qaly": float(np.std(total, ddof=1) / np.sqrt(n)),
            "standalone_sample_variance": total_variance,
            "covariance_with_total_delta_qaly": total_variance,
            "allocated_variance_share_percent": 100.0,
        }
    )
    covariance_frame = pd.DataFrame(covariance, index=labels, columns=labels)
    covariance_frame.index.name = "component"
    return pd.DataFrame(rows), covariance_frame


def _write_analysis(
    output_dir: Path,
    run_rows: List[dict],
    arm_rows: List[dict],
    timestep_rows: List[dict],
) -> None:
    runs = pd.DataFrame(run_rows)
    runs.to_csv(output_dir / "component_deltas_by_run.csv", index=False)
    pd.DataFrame(arm_rows).to_csv(output_dir / "arm_component_totals_long.csv", index=False)
    timesteps = pd.DataFrame(timestep_rows)
    timesteps.to_csv(output_dir / "timestep_component_deltas.csv", index=False)
    value_columns = [
        column
        for column in timesteps.columns
        if column.startswith("delta_") and column != "delta_total_population"
    ]
    annual = (
        timesteps.assign(year_int=np.floor(timesteps["year"]).astype(int))
        .groupby(["mode", "seed", "year_int"], as_index=False)[value_columns]
        .sum()
        .rename(columns={"year_int": "year"})
    )
    annual.to_csv(output_dir / "annual_component_deltas.csv", index=False)

    deterministic_rows = runs.loc[runs["mode"] == "deterministic"]
    if len(deterministic_rows) != 1:
        raise ValueError(
            f"expected one deterministic run for analysis, found {len(deterministic_rows)}"
        )
    deterministic_row = deterministic_rows.iloc[0]

    partition_labels = list(PARTITION_COMPONENTS)
    partition_columns = [f"delta_partition_{label}" for label in partition_labels]
    partition_summary, partition_covariance = _variance_summary(
        runs, partition_columns, partition_labels, deterministic_row
    )
    partition_summary.to_csv(output_dir / "partition_variance_summary.csv", index=False)
    partition_covariance.to_csv(output_dir / "partition_covariance_matrix.csv")

    detail_labels = list(QALY_COMPONENT_KEYS)
    detail_columns = [f"delta_{label}" for label in detail_labels]
    detail_summary, detail_covariance = _variance_summary(
        runs, detail_columns, detail_labels, deterministic_row
    )
    detail_summary.to_csv(output_dir / "detailed_component_variance_summary.csv", index=False)
    detail_covariance.to_csv(output_dir / "detailed_component_covariance_matrix.csv")

    stochastic = runs.loc[runs["mode"] == "stochastic"]
    combined = stochastic["delta_diagnostic_all_susceptible"].to_numpy(dtype=float)
    total = stochastic["delta_qaly"].to_numpy(dtype=float)
    combined_summary = pd.DataFrame(
        [
            {
                "component": "all_susceptible_including_prep",
                "n": len(stochastic),
                "deterministic_delta_qaly": float(
                    deterministic_row["delta_diagnostic_all_susceptible"]
                ),
                "stochastic_mean_delta_qaly": float(np.mean(combined)),
                "stochastic_sd_delta_qaly": float(np.std(combined, ddof=1)),
                "stochastic_se_delta_qaly": float(
                    np.std(combined, ddof=1) / np.sqrt(len(stochastic))
                ),
                "standalone_sample_variance": float(np.var(combined, ddof=1)),
                "covariance_with_total_delta_qaly": float(
                    np.cov(combined, total, ddof=1)[0, 1]
                ),
                "covariance_share_of_total_variance_percent": float(
                    100.0
                    * np.cov(combined, total, ddof=1)[0, 1]
                    / np.var(total, ddof=1)
                ),
            }
        ]
    )
    combined_summary.to_csv(output_dir / "combined_susceptible_diagnostic.csv", index=False)

    correlation_columns = [
        "delta_qaly",
        "delta_diagnostic_all_susceptible",
        "delta_partition_hiv_states",
        "delta_person_years",
        "infections_averted",
    ]
    stochastic[correlation_columns].corr().to_csv(
        output_dir / "diagnostic_correlations.csv"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-config",
        type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--hta-config",
        type=Path,
        default=ROOT / "configs/hta_runtime_config.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=_parse_seeds, default=_parse_seeds("42-56"))
    parser.add_argument(
        "--deterministic-seed",
        type=int,
        default=42,
        help="Seed recorded for the deterministic run; dynamics remain deterministic.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir()

    model_config = args.model_config.resolve()
    hta_config = args.hta_config.resolve()
    command = shlex.join([sys.executable, *sys.argv])
    costing_config = ROOT / "configs/hta_costing_config.json"
    code_inputs = (
        "run_scenarios.py",
        "run_v3.py",
        "economics.py",
        "adverse_events.py",
        "scenario_comparator.py",
        "state_snapshot.py",
        "demography.py",
        "migration.py",
        "transmission_corrected.py",
        "tools/run_a1_qaly_variance_decomposition.py",
    )
    manifest = {
        "task": "A-1 variance decomposition of delta QALY by health state",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_status_short": _git(["status", "--short"]),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "model_config": str(model_config),
        "hta_config": str(hta_config),
        "deterministic_seed": args.deterministic_seed,
        "stochastic_seeds": args.seeds,
        "scenario": BASE_SCENARIO,
        "reference_arm": REFERENCE_ARM,
        "intervention_arm": INTERVENTION_ARM,
        "sha256": {
            str(model_config.relative_to(ROOT)): _sha256(model_config),
            str(hta_config.relative_to(ROOT)): _sha256(hta_config),
            str(costing_config.relative_to(ROOT)): _sha256(costing_config),
            **{path: _sha256(ROOT / path) for path in code_inputs},
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    run_rows: List[dict] = []
    arm_rows: List[dict] = []
    timestep_rows: List[dict] = []
    row, arms, timesteps = _run_one(
        model_config=model_config,
        hta_config=hta_config,
        run_dir=raw_dir / "deterministic",
        deterministic=True,
        seed=args.deterministic_seed,
    )
    run_rows.append(row)
    arm_rows.extend(arms)
    timestep_rows.extend(timesteps)

    for seed in args.seeds:
        row, arms, timesteps = _run_one(
            model_config=model_config,
            hta_config=hta_config,
            run_dir=raw_dir / f"stochastic_seed_{seed}",
            deterministic=False,
            seed=seed,
        )
        run_rows.append(row)
        arm_rows.extend(arms)
        timestep_rows.extend(timesteps)
        print(
            f"seed={seed} delta_qaly={row['delta_qaly']:.6f} "
            f"infections_averted={row['infections_averted']} "
            f"elapsed={row['elapsed_seconds']:.1f}s",
            flush=True,
        )

    _write_analysis(output_dir, run_rows, arm_rows, timestep_rows)
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-1 outputs written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
