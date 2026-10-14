"""Run one A-11 code/economic-config counterfactual on selected HTA scenarios.

The script is deliberately diagnostic-only.  It imports the model implementation
from ``--code-root``, replaces only the composed economic blocks with the exact
``--costing-config`` content, and leaves production files unchanged.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
import importlib.util
from typing import Iterable, Sequence

import pandas as pd


DEFAULT_SCENARIOS = (
    "base_case_context",
    "scenario_a_lena_substitution__switch_10",
)

FLOW_COLUMNS = (
    "new_infections",
    "hiv_deaths",
    "aids_deaths",
    "background_deaths",
    "childhood_deaths",
    "births",
    "net_migration",
    "new_aids_cases",
    "new_diagnoses",
    "art_initiations",
    "total_population",
)

ECONOMIC_TOP_LEVEL_KEYS = {
    "_format_version",
    "_notes",
    "discounting",
    "wtp_threshold_eur_per_qaly",
    "perspective",
    "utilities",
    "utility_decrements",
    "adverse_events",
    "prevention_costs",
    "disease_costs",
    "_costing_update_note",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_economic_blocks(composed: dict, costing: dict) -> dict:
    """Return a copy with the composed economic layer replaced exactly."""
    replaced = copy.deepcopy(composed)
    for key in ECONOMIC_TOP_LEVEL_KEYS:
        replaced.pop(key, None)
    for key in ECONOMIC_TOP_LEVEL_KEYS:
        if key in costing:
            replaced[key] = copy.deepcopy(costing[key])
    return replaced


def select_scenarios(scenarios: Sequence[dict], names: Iterable[str]) -> list[dict]:
    wanted = tuple(names)
    indexed = {scenario["name"]: scenario for scenario in scenarios}
    missing = [name for name in wanted if name not in indexed]
    if missing:
        raise ValueError(f"requested scenarios not found: {missing}")
    return [indexed[name] for name in wanted]


def person_years(result, *, discounted: bool) -> float:
    return math.fsum(
        float(record["total_population"])
        * float(record["step_fraction"])
        * (float(record["discount_factor_health"]) if discounted else 1.0)
        for record in result.timestep_records
    )


def summarize_pair(scenario: dict, results: dict) -> dict:
    reference_name, intervention_name = scenario["arm_order"]
    reference = results[reference_name]
    intervention = results[intervention_name]
    infections_averted = float(
        reference.cumul_new_infections - intervention.cumul_new_infections
    )
    delta_qaly = float(
        intervention.total_qalys_disc - reference.total_qalys_disc
    )
    return {
        "scenario": scenario["name"],
        "reference_arm": reference_name,
        "intervention_arm": intervention_name,
        "reference_qaly_disc": float(reference.total_qalys_disc),
        "intervention_qaly_disc": float(intervention.total_qalys_disc),
        "delta_qaly": delta_qaly,
        "reference_infections": float(reference.cumul_new_infections),
        "intervention_infections": float(intervention.cumul_new_infections),
        "infections_averted": infections_averted,
        "qaly_per_averted_infection": (
            delta_qaly / infections_averted if infections_averted != 0 else math.nan
        ),
        "reference_discounted_person_years": person_years(
            reference, discounted=True
        ),
        "intervention_discounted_person_years": person_years(
            intervention, discounted=True
        ),
        "delta_discounted_person_years": (
            person_years(intervention, discounted=True)
            - person_years(reference, discounted=True)
        ),
        "reference_undiscounted_person_years": person_years(
            reference, discounted=False
        ),
        "intervention_undiscounted_person_years": person_years(
            intervention, discounted=False
        ),
        "delta_undiscounted_person_years": (
            person_years(intervention, discounted=False)
            - person_years(reference, discounted=False)
        ),
    }


def simulation_delta_rows(scenario: dict, results: dict) -> list[dict]:
    reference_name, intervention_name = scenario["arm_order"]
    reference = results[reference_name]
    intervention = results[intervention_name]
    reference_records = getattr(reference, "simulation_records", [])
    intervention_records = getattr(intervention, "simulation_records", [])
    if len(reference_records) != len(intervention_records):
        raise AssertionError("simulation record lengths differ between arms")
    rows = []
    for reference_row, intervention_row in zip(
        reference_records, intervention_records
    ):
        if (reference_row["step"], reference_row["year"]) != (
            intervention_row["step"], intervention_row["year"]
        ):
            raise AssertionError("simulation timestep axes differ between arms")
        row = {
            "scenario": scenario["name"],
            "step": reference_row["step"],
            "year": reference_row["year"],
        }
        for column in FLOW_COLUMNS:
            if column in reference_row and column in intervention_row:
                row[f"reference_{column}"] = float(reference_row[column])
                row[f"intervention_{column}"] = float(intervention_row[column])
                row[f"delta_{column}"] = float(
                    intervention_row[column] - reference_row[column]
                )
        rows.append(row)
    return rows


def git_text(repo: Path, args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.rstrip()


def load_module_override(module_name: str, path: Path) -> None:
    """Load a diagnostic module override before importing the selected code root."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} override from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--costing-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--demography-override", type=Path)
    parser.add_argument("--allocation-override", type=Path)
    parser.add_argument(
        "--scenarios", nargs="+", default=list(DEFAULT_SCENARIOS)
    )
    args = parser.parse_args()

    code_root = args.code_root.resolve()
    model_config = args.model_config.resolve()
    runtime_config = args.runtime_config.resolve()
    costing_config = args.costing_config.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_output = output_dir / "raw_runner"
    raw_output.mkdir()

    sys.path.insert(0, str(code_root))
    demography_override = (
        args.demography_override.resolve() if args.demography_override else None
    )
    allocation_override = (
        args.allocation_override.resolve() if args.allocation_override else None
    )
    if allocation_override is not None:
        load_module_override("stochastic_allocation", allocation_override)
    if demography_override is not None:
        load_module_override("demography", demography_override)
    from run_scenarios import MultiScenarioRunner  # noqa: PLC0415

    with costing_config.open(encoding="utf-8") as handle:
        costing = json.load(handle)

    runner = MultiScenarioRunner(
        str(model_config), str(raw_output), str(runtime_config)
    )
    runner.hta_config = replace_economic_blocks(runner.hta_config, costing)
    runner.hta_config["_costing_config_path"] = str(costing_config)
    runner.hta_config["analysis_scenarios"] = select_scenarios(
        runner.hta_config["analysis_scenarios"], args.scenarios
    )
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    runner.base_config["run_control"]["deterministic_mode"]["value"] = True
    runner.base_config.setdefault("simulation_controls", {})[
        "deterministic_mode"
    ] = {"value": True}
    runner.base_config["run_control"]["seed"] = {"value": args.seed}

    manifest = {
        "task": "A-11 QALY-gap code/economic-config counterfactual",
        "label": args.label,
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_root": str(code_root),
        "code_git_commit": git_text(code_root, ["rev-parse", "HEAD"]),
        "code_git_status_short": git_text(code_root, ["status", "--short"]),
        "model_config": str(model_config),
        "runtime_config": str(runtime_config),
        "costing_config": str(costing_config),
        "seed": args.seed,
        "deterministic": True,
        "scenarios": list(args.scenarios),
        "demography_override": (
            str(demography_override) if demography_override else None
        ),
        "allocation_override": (
            str(allocation_override) if allocation_override else None
        ),
        "python": sys.version,
        "platform": platform.platform(),
        "pandas_version": pd.__version__,
        "sha256": {
            "model_config": sha256(model_config),
            "runtime_config": sha256(runtime_config),
            "costing_config": sha256(costing_config),
            **{
                f"code/{name}": sha256(code_root / name)
                for name in (
                    "run_scenarios.py",
                    "economics.py",
                    "demography.py",
                    "run_v3.py",
                    "scenario_comparator.py",
                )
            },
            **(
                {"override/demography.py": sha256(demography_override)}
                if demography_override
                else {}
            ),
            **(
                {"override/stochastic_allocation.py": sha256(allocation_override)}
                if allocation_override
                else {}
            ),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    started = time.time()
    log_path = output_dir / "runner_stdout.log"
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(
            log_handle
        ):
            runner.run()

    rows = []
    simulation_rows = []
    selected = select_scenarios(runner.hta_config["analysis_scenarios"], args.scenarios)
    for scenario in selected:
        row = summarize_pair(scenario, runner.results[scenario["name"]])
        row["label"] = args.label
        rows.append(row)
        simulation_rows.extend(
            simulation_delta_rows(scenario, runner.results[scenario["name"]])
        )
    summary_path = output_dir / "counterfactual_summary.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False)
    simulation_path = output_dir / "timestep_simulation_deltas.csv"
    pd.DataFrame(simulation_rows).to_csv(simulation_path, index=False)

    manifest["elapsed_seconds"] = time.time() - started
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["output_sha256"] = {
        "counterfactual_summary.csv": sha256(summary_path),
        "runner_stdout.log": sha256(log_path),
        "timestep_simulation_deltas.csv": sha256(simulation_path),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-11 counterfactual written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
