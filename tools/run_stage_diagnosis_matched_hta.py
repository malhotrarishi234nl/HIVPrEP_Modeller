"""Matched HTA comparison of legacy and stage-diagnosis model structures.

This analysis harness reuses the validated A-16 stochastic HTA estimand, but
replaces its single epidemiological configuration with the two frozen
configuration exports from the stage-diagnosis baseline review.  Diagnosis
structure is the only intended model difference.  Both structures use the
same stochastic seeds, HTA runtime configuration, costing inputs, scenarios,
time horizon, and price-root definitions.

The default is 15 stochastic seeds (42--56).  Use ``--dry-run`` to validate
the inputs and print the trajectory plan without starting the simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
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

import coupled_calibration_search as ccs  # noqa: E402
from tools.run_a16_stochastic_headline import (  # noqa: E402
    BASE_SCENARIO,
    SCENARIOS,
    SWITCHES,
    SWITCH_PREFIX,
    ZIN_WTP_TIERS,
    _run_worker,
    parse_seeds,
    pooled_price_table,
    ratio_of_means,
    summarize,
)

DEFAULT_REVIEW = (
    ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
)
STRUCTURES = {
    "legacy": ("D_1788_v000", "legacy_configuration.json"),
    "candidate": ("D_1788_v109", "candidate_configuration.json"),
}
CORE_METRICS = (
    "delta_cost",
    "delta_qaly",
    "infections_averted",
    "nmb_20k",
    "nmb_50k",
    "nmb_80k",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_frozen_structure(path: Path, expected_tuple: str) -> tuple[dict, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("tuple_id") != expected_tuple:
        raise ValueError(
            f"{path}: expected tuple_id={expected_tuple}, got {payload.get('tuple_id')}"
        )
    config = payload.get("config")
    if not isinstance(config, dict):
        raise ValueError(f"{path}: missing full config dictionary")
    observed_hash = ccs.sha256_json(config)
    if observed_hash != payload.get("config_sha256"):
        raise ValueError(
            f"{path}: frozen config hash mismatch: {observed_hash} != "
            f"{payload.get('config_sha256')}"
        )
    return payload, config


def validate_matched_configs(configs: dict[str, dict]) -> list[str]:
    """Require every non-diagnosis leaf to match exactly."""

    def leaves(node: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
        if isinstance(node, dict):
            result: dict[tuple[str, ...], Any] = {}
            for key, value in node.items():
                result.update(leaves(value, prefix + (str(key),)))
            return result
        if isinstance(node, list):
            result = {}
            for index, value in enumerate(node):
                result.update(leaves(value, prefix + (str(index),)))
            return result
        return {prefix: node}

    legacy = leaves(configs["legacy"])
    candidate = leaves(configs["candidate"])
    all_paths = legacy.keys() | candidate.keys()
    missing = object()
    changed = [
        ".".join(path)
        for path in all_paths
        if legacy.get(path, missing) != candidate.get(path, missing)
    ]
    unexpected = [
        path
        for path in changed
        if not path.startswith("transmission_model.disease.diagnosis.")
    ]
    if unexpected:
        raise ValueError(f"non-diagnosis configuration differences: {unexpected}")
    if not changed:
        raise ValueError("the two frozen structures are unexpectedly identical")
    return sorted(changed)


def summarize_by_structure(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    price_metrics = tuple(
        f"{kind}_{int(wtp / 1000)}k"
        for kind in ("ce_threshold_price", "qaly_loss_compensation_price")
        for wtp in ZIN_WTP_TIERS
    )
    for structure in STRUCTURES:
        for scenario in SCENARIOS:
            group = rows[
                (rows["structure"] == structure) & (rows["scenario"] == scenario)
            ]
            for metric in CORE_METRICS + price_metrics:
                if metric not in group:
                    continue
                records.append(
                    {
                        "structure": structure,
                        "scenario": scenario,
                        "metric": metric,
                        **summarize(group[metric].tolist()),
                    }
                )
    return pd.DataFrame(records)


def paired_structure_differences(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Candidate-minus-legacy differences on identical seed/scenario pairs."""
    indexed = rows.set_index(["structure", "seed", "scenario"])
    detail: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for metric in CORE_METRICS:
            legacy = indexed.loc[("legacy", slice(None), scenario), metric]
            candidate = indexed.loc[("candidate", slice(None), scenario), metric]
            legacy.index = legacy.index.droplevel([0, 2])
            candidate.index = candidate.index.droplevel([0, 2])
            if not legacy.index.equals(candidate.index):
                raise ValueError(f"unmatched seeds for {scenario} {metric}")
            differences = candidate - legacy
            for seed, value in differences.items():
                detail.append(
                    {
                        "seed": int(seed),
                        "scenario": scenario,
                        "metric": metric,
                        "candidate_minus_legacy": float(value),
                    }
                )
            summary.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    **summarize(differences.tolist()),
                }
            )
    return pd.DataFrame(detail), pd.DataFrame(summary)


def build_decision_summary(
    rows: pd.DataFrame, pooled_prices: pd.DataFrame
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for structure in STRUCTURES:
        structure_rows = rows[rows["structure"] == structure]
        for scenario in SCENARIOS:
            group = structure_rows[structure_rows["scenario"] == scenario]
            qaly = summarize(group["delta_qaly"].tolist())
            cost = summarize(group["delta_cost"].tolist())
            infection = summarize(group["infections_averted"].tolist())
            ratio = ratio_of_means(group["delta_cost"], group["delta_qaly"])
            row: dict[str, Any] = {
                "structure": structure,
                "scenario": scenario,
                "n_seeds": len(group),
                "mean_delta_cost": cost["mean"],
                "mean_delta_qaly": qaly["mean"],
                "mean_infections_averted": infection["mean"],
                "ratio_of_means_icer": ratio["ratio_of_means"],
                "icer_status": ratio["status"],
            }
            for wtp in ZIN_WTP_TIERS:
                slug = f"{int(wtp / 1000)}k"
                row[f"mean_nmb_{slug}"] = float(group[f"nmb_{slug}"].mean())
                row[f"probability_ce_{slug}"] = float((group[f"nmb_{slug}"] > 0).mean())
                if scenario != BASE_SCENARIO:
                    price = pooled_prices[
                        (pooled_prices["structure"] == structure)
                        & (pooled_prices["scenario"] == scenario)
                        & (pooled_prices["wtp"] == wtp)
                    ].iloc[0]
                    row[f"pooled_nmb_zero_price_{slug}"] = price["nmb_zero_price"]
                    row[f"pooled_ce_threshold_price_{slug}"] = price[
                        "ce_threshold_price"
                    ]
                    row[f"pooled_qaly_loss_compensation_price_{slug}"] = price[
                        "qaly_loss_compensation_price"
                    ]
            records.append(row)
    return pd.DataFrame(records)


def render_report(
    decision: pd.DataFrame,
    differences: pd.DataFrame,
    seeds: Sequence[int],
) -> str:
    diff = differences.set_index(["scenario", "metric"])
    lines = [
        "# Matched diagnosis-structure HTA comparison",
        "",
        f"Legacy `D_1788_v000` and candidate `D_1788_v109`; identical stochastic seeds "
        f"{min(seeds)}–{max(seeds)} (n={len(seeds)}), economics, uptake scenarios, "
        "40-year horizon, and intervention assumptions.",
        "",
        "These are structural-sensitivity results, not a declaration that either "
        "historical model is the true baseline.",
        "",
        "## Headline at €80,000/QALY",
        "",
        "| switch | structure | ΔCost (€m) | ΔQALY | infections averted | NMB (€m) | P(NMB>0) | pooled price root (€/yr) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for switch in SWITCHES:
        scenario = f"{SWITCH_PREFIX}{switch}"
        for structure in STRUCTURES:
            row = decision[
                (decision["structure"] == structure)
                & (decision["scenario"] == scenario)
            ].iloc[0]
            root = row.get("pooled_nmb_zero_price_80k", math.nan)
            lines.append(
                f"| {switch}% | {structure} | {row['mean_delta_cost']/1e6:,.2f} | "
                f"{row['mean_delta_qaly']:,.1f} | {row['mean_infections_averted']:,.1f} | "
                f"{row['mean_nmb_80k']/1e6:,.2f} | {row['probability_ce_80k']:.2f} | "
                f"{root:,.0f} |"
            )
    lines += [
        "",
        "## Candidate-minus-legacy effect on the intervention comparison",
        "",
        "Positive values mean the candidate diagnosis structure produces a larger "
        "incremental quantity than the legacy structure.",
        "",
        "| switch | ΔΔCost (€m) | ΔΔQALY | Δ infections averted | ΔNMB at €80k (€m) |",
        "|---:|---:|---:|---:|---:|",
    ]
    for switch in SWITCHES:
        scenario = f"{SWITCH_PREFIX}{switch}"
        lines.append(
            f"| {switch}% | {diff.loc[(scenario, 'delta_cost'), 'mean']/1e6:,.2f} | "
            f"{diff.loc[(scenario, 'delta_qaly'), 'mean']:,.1f} | "
            f"{diff.loc[(scenario, 'infections_averted'), 'mean']:,.1f} | "
            f"{diff.loc[(scenario, 'nmb_80k'), 'mean']/1e6:,.2f} |"
        )
    lines += [
        "",
        "Intervals, all three ZIN thresholds, conditional price quantities, and "
        "seed-level values are retained in the accompanying CSV files.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument(
        "--hta-config", type=Path, default=ROOT / "configs/hta_runtime_config.json"
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--stochastic-seeds", default="42-56")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seeds = parse_seeds(args.stochastic_seeds)
    if len(seeds) < 15 and not args.dry_run:
        raise ValueError("matched headline comparison requires at least 15 seeds")
    review_dir = args.review_dir.resolve()
    hta_config = args.hta_config.resolve()
    if not hta_config.exists():
        raise FileNotFoundError(hta_config)

    payloads: dict[str, dict] = {}
    configs: dict[str, dict] = {}
    for structure, (tuple_id, filename) in STRUCTURES.items():
        payload, config = load_frozen_structure(review_dir / filename, tuple_id)
        payloads[structure] = payload
        configs[structure] = config
    changed = validate_matched_configs(configs)

    total_model_runs = len(STRUCTURES) * len(seeds)
    total_arm_trajectories = total_model_runs * len(SCENARIOS) * 2
    total_simulation_trajectories = total_model_runs + total_arm_trajectories
    print(
        json.dumps(
            {
                "status": "validated",
                "structures": list(STRUCTURES),
                "seeds": seeds,
                "model_runs": total_model_runs,
                "scenario_arm_trajectories": total_arm_trajectories,
                "historical_prefreeze_trajectories": total_model_runs,
                "total_simulation_trajectories": total_simulation_trajectories,
                "estimated_runtime_four_workers": "approximately 15-20 minutes",
                "diagnosis_leaves_changed": changed,
                "hta_config": str(hta_config),
            },
            indent=2,
        )
    )
    if args.dry_run:
        return 0
    if args.output_dir is None:
        raise ValueError("--output-dir is required unless --dry-run is used")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir()
    exported: dict[str, Path] = {}
    for structure, config in configs.items():
        path = output_dir / f"{structure}_model_config.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        exported[structure] = path

    command = shlex.join([sys.executable, *sys.argv])
    manifest = {
        "task": "matched legacy-versus-candidate diagnosis-structure HTA",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "structures": {
            structure: {
                "tuple_id": STRUCTURES[structure][0],
                "frozen_config_sha256": payloads[structure]["config_sha256"],
                "wrapper_file_sha256": sha256_file(review_dir / STRUCTURES[structure][1]),
            }
            for structure in STRUCTURES
        },
        "diagnosis_leaves_changed": changed,
        "stochastic_seeds": seeds,
        "n_seeds": len(seeds),
        "hta_config": str(hta_config),
        "hta_config_sha256": sha256_file(hta_config),
        "costing_config_sha256": sha256_file(ROOT / "configs/hta_costing_config.json"),
        "scenarios": list(SCENARIOS),
        "zin_wtp_tiers": list(ZIN_WTP_TIERS),
        "comparison": "candidate minus legacy; paired on identical seed and scenario",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    jobs = [
        {
            "structure": structure,
            "mode": "stochastic",
            "seed": seed,
            "run_dir": str(runs_dir / f"{structure}_seed_{seed}"),
            "model_config": str(exported[structure]),
            "hta_config": str(hta_config),
        }
        for structure in STRUCTURES
        for seed in seeds
    ]
    started = time.time()
    worker_results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(_run_worker, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            result = future.result()
            result["structure"] = job["structure"]
            worker_results.append(result)
            print(
                f"completed {job['structure']} seed {job['seed']} in "
                f"{result['elapsed_seconds']:.1f}s",
                flush=True,
            )

    records: list[dict[str, Any]] = []
    for result in worker_results:
        for row in result["rows"]:
            records.append({"structure": result["structure"], **row})
    rows = pd.DataFrame(records).sort_values(["structure", "seed", "scenario"])
    expected_rows = len(STRUCTURES) * len(seeds) * len(SCENARIOS)
    if len(rows) != expected_rows:
        raise AssertionError(f"expected {expected_rows} comparison rows, got {len(rows)}")
    rows.to_csv(output_dir / "per_seed_economics.csv", index=False)

    summaries = summarize_by_structure(rows)
    paired_detail, paired_summary = paired_structure_differences(rows)
    price_frames = []
    for structure in STRUCTURES:
        prices = pooled_price_table(rows[rows["structure"] == structure])
        prices.insert(0, "structure", structure)
        price_frames.append(prices)
    pooled_prices = pd.concat(price_frames, ignore_index=True)
    decision = build_decision_summary(rows, pooled_prices)

    summaries.to_csv(output_dir / "within_structure_summary_long.csv", index=False)
    paired_detail.to_csv(output_dir / "paired_structure_differences.csv", index=False)
    paired_summary.to_csv(output_dir / "paired_structure_difference_summary.csv", index=False)
    pooled_prices.to_csv(output_dir / "pooled_price_thresholds.csv", index=False)
    decision.to_csv(output_dir / "decision_summary.csv", index=False)
    (output_dir / "REPORT.md").write_text(
        render_report(decision, paired_summary, seeds), encoding="utf-8"
    )

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["elapsed_seconds"] = time.time() - started
    manifest["output_sha256"] = {
        filename: sha256_file(output_dir / filename)
        for filename in (
            "per_seed_economics.csv",
            "within_structure_summary_long.csv",
            "paired_structure_differences.csv",
            "paired_structure_difference_summary.csv",
            "pooled_price_thresholds.csv",
            "decision_summary.csv",
            "REPORT.md",
        )
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"completed matched HTA comparison in {manifest['elapsed_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
