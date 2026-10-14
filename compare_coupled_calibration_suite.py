#!/usr/bin/env python3
"""Compare completed cases from a coupled-calibration experiment suite.

This adapter reads immutable search/confirmation artifacts. It never imports or
constructs the simulation engine and never reruns calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

import run_coupled_calibration as runner
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent


class ComparisonError(ValueError):
    """Completed case artifacts do not satisfy the comparison contract."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read JSON artifact {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _case_paths(suite_root: Path, case_id: str) -> dict[str, Path]:
    root = suite_root / "cases" / case_id
    return {
        "plan": root / "resolved_configs/coupled_search_plan.json",
        "search_manifest": root / "calibration/search/manifest.json",
        "confirmation_manifest": root / "calibration/confirmation/manifest.json",
        "confirmation_summary": root / "calibration/confirmation/confirmation_summary.csv",
        "target_results": root / "calibration/confirmation/paired_discovery_confirmation.csv",
    }


def _best_candidate(frame: pd.DataFrame) -> pd.Series:
    required = {
        "candidate_id", "confirmation_primary_pass", "confirmation_primary_i_max",
        "confirmation_j",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ComparisonError(f"confirmation summary lacks columns: {missing}")
    ranked = frame.assign(
        _pass=frame["confirmation_primary_pass"].astype(str).str.lower().eq("true")
    ).sort_values(
        ["_pass", "confirmation_primary_i_max", "confirmation_j", "candidate_id"],
        ascending=[False, True, True, True],
    )
    if ranked.empty:
        raise ComparisonError("confirmation summary contains no candidates")
    return ranked.iloc[0]


def _target_output_map(plan: dict, target_keys: set[str]) -> dict[str, str]:
    """Resolve observational target labels to simulator annual-output names."""
    relative_path = plan.get("inputs", {}).get("target_register")
    if not relative_path:
        raise ComparisonError("plan lacks inputs.target_register")
    register_path = ROOT / relative_path
    expected_hash = plan.get("inputs", {}).get("expected_sha256", {}).get(
        "target_register"
    )
    if expected_hash and _sha256(register_path) != expected_hash:
        raise ComparisonError(f"target register hash differs from plan: {register_path}")
    register = _read_json(register_path)
    by_series = {
        str(item["series_name"]): str(item["model_output"])
        for item in register.get("targets", [])
        if item.get("series_name") and item.get("model_output")
    }
    mapping = {}
    for target_key in target_keys:
        series, _ = target_key.rsplit("@", 1)
        if series not in by_series:
            raise ComparisonError(
                f"target register lacks a model_output mapping for {series}"
            )
        mapping[target_key] = by_series[series]
    return mapping


def _seed_target_frame(
    raw: dict, target_keys: set[str], output_by_target: dict[str, str]
) -> pd.DataFrame:
    rows = []
    for replicate in raw.get("per_seed", []):
        seed = int(replicate["seed"])
        outputs = replicate["annual_outputs"]
        for target_key in sorted(target_keys):
            _, year_text = target_key.rsplit("@", 1)
            year = int(year_text)
            model_output = output_by_target[target_key]
            values = outputs.get(model_output, {})
            if str(year) in values:
                value = values[str(year)]
            elif year in values:
                value = values[year]
            else:
                raise ComparisonError(f"raw candidate lacks {target_key} for seed {seed}")
            rows.append({"seed": seed, "target_key": target_key, "value": float(value)})
    if not rows:
        raise ComparisonError("raw confirmation candidate contains no per-seed outputs")
    frame = pd.DataFrame(rows)
    if frame.duplicated(["seed", "target_key"]).any():
        raise ComparisonError("raw confirmation candidate repeats a seed/target row")
    return frame


def load_comparison(suite_root: Path) -> dict:
    """Validate and normalize two completed cases without writing artifacts."""
    suite_root = Path(suite_root)
    source = _read_json(suite_root / "suite_source.json")
    comparison = source.get("comparison", {})
    case_ids = comparison.get("cases", [])
    if not isinstance(case_ids, list) or len(case_ids) != 2 or len(set(case_ids)) != 2:
        raise ComparisonError("suite comparison must declare exactly two distinct cases")

    loaded: dict[str, dict] = {}
    for case_id in case_ids:
        paths = _case_paths(suite_root, case_id)
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            raise ComparisonError(f"case {case_id} is incomplete; missing {missing}")
        plan = _read_json(paths["plan"])
        try:
            runner.assert_plan_matches(plan)
        except ContractError as exc:
            raise ComparisonError(f"case {case_id} plan is not executable: {exc}") from exc
        search_manifest = _read_json(paths["search_manifest"])
        confirmation_manifest = _read_json(paths["confirmation_manifest"])
        if search_manifest.get("status") != "SEARCH_COMPLETE":
            raise ComparisonError(f"case {case_id} search is not complete")
        if confirmation_manifest.get("status") != "CONFIRMATION_COMPLETE":
            raise ComparisonError(f"case {case_id} confirmation is not complete")
        if search_manifest.get("design") != plan["design"]["name"]:
            raise ComparisonError(f"case {case_id} search design does not match its plan")
        if confirmation_manifest.get("design") != plan["design"]["name"]:
            raise ComparisonError(f"case {case_id} confirmation design does not match its plan")
        summary = pd.read_csv(paths["confirmation_summary"])
        targets = pd.read_csv(paths["target_results"])
        selected = _best_candidate(summary)
        selected_targets = targets.loc[
            targets["candidate_id"].astype(int) == int(selected["candidate_id"])
        ].copy()
        if selected_targets.empty:
            raise ComparisonError(
                f"case {case_id} has no per-target rows for selected candidate "
                f"{int(selected['candidate_id'])}"
            )
        raw_path = paths["confirmation_summary"].parent / "raw" / (
            f"candidate_{int(selected['candidate_id'])}.json"
        )
        if not raw_path.is_file():
            raise ComparisonError(f"case {case_id} lacks selected-candidate raw output: {raw_path}")
        raw = _read_json(raw_path)
        expected_seeds = sorted(int(v) for v in plan["seeds"]["confirmation"])
        actual_seeds = sorted(int(v) for v in raw.get("replicate_seeds", []))
        if actual_seeds != expected_seeds:
            raise ComparisonError(f"case {case_id} raw confirmation seeds differ from its plan")
        paths["selected_candidate_raw"] = raw_path
        loaded[case_id] = {
            "paths": paths, "plan": plan, "search": search_manifest,
            "confirmation": confirmation_manifest, "summary": summary,
            "selected": selected, "targets": selected_targets, "raw": raw,
        }

    baseline_id, alternative_id = case_ids
    baseline = loaded[baseline_id]
    alternative = loaded[alternative_id]
    base_seeds = baseline["plan"]["seeds"]
    alt_seeds = alternative["plan"]["seeds"]
    if base_seeds != alt_seeds:
        raise ComparisonError("paired comparison plans do not carry identical CRN declarations")
    group = base_seeds.get("comparison_group")
    if not group:
        raise ComparisonError("paired comparison plans lack seeds.comparison_group")
    if set(base_seeds.get("paired_designs", [])) != {
        baseline["plan"]["design"]["name"], alternative["plan"]["design"]["name"]
    }:
        raise ComparisonError("paired_designs does not exactly name the compared plans")

    target_keys = [set(item["targets"]["target_key"].astype(str)) for item in loaded.values()]
    if target_keys[0] != target_keys[1]:
        raise ComparisonError("selected candidates do not have the same target set")

    baseline_output_map = _target_output_map(baseline["plan"], target_keys[0])
    alternative_output_map = _target_output_map(alternative["plan"], target_keys[1])
    if baseline_output_map != alternative_output_map:
        raise ComparisonError("paired comparison plans resolve targets to different outputs")
    baseline_seed = _seed_target_frame(
        baseline["raw"], target_keys[0], baseline_output_map
    ).rename(
        columns={"value": f"{baseline_id}__value"}
    )
    alternative_seed = _seed_target_frame(
        alternative["raw"], target_keys[1], alternative_output_map
    ).rename(
        columns={"value": f"{alternative_id}__value"}
    )
    paired_seed = baseline_seed.merge(
        alternative_seed, on=["seed", "target_key"], validate="one_to_one"
    )
    delta_column = f"difference__{alternative_id}_minus_{baseline_id}"
    paired_seed[delta_column] = (
        paired_seed[f"{alternative_id}__value"] - paired_seed[f"{baseline_id}__value"]
    )
    paired_rows = []
    for target_key, group_frame in paired_seed.groupby("target_key", sort=True):
        differences = group_frame[delta_column]
        paired_rows.append({
            "target_key": target_key,
            "paired_replicates": int(len(group_frame)),
            "mean_paired_difference": float(differences.mean()),
            "sd_paired_difference": float(differences.std(ddof=1)),
            "se_paired_difference": float(differences.sem(ddof=1)),
            "cross_case_correlation": float(group_frame[
                [f"{baseline_id}__value", f"{alternative_id}__value"]
            ].corr().iloc[0, 1]),
        })
    paired_summary = pd.DataFrame(paired_rows)

    case_rows = []
    candidate_frames = []
    for case_id in case_ids:
        item = loaded[case_id]
        selected = item["selected"]
        case_rows.append({
            "case_id": case_id,
            "design": item["plan"]["design"]["name"],
            "selected_candidate_id": int(selected["candidate_id"]),
            "confirmation_primary_pass": bool(selected["_pass"]),
            "confirmation_primary_i_max": float(selected["confirmation_primary_i_max"]),
            "confirmation_j": float(selected["confirmation_j"]),
            "n_confirmation_candidates": int(len(item["summary"])),
            "n_confirmation_primary_pass": int(item["confirmation"].get(
                "n_confirmation_primary_pass", 0
            )),
        })
        candidate_frame = item["summary"].copy()
        candidate_frame.insert(0, "case_id", case_id)
        candidate_frames.append(candidate_frame)

    base_targets = baseline["targets"].add_prefix(f"{baseline_id}__")
    alt_targets = alternative["targets"].add_prefix(f"{alternative_id}__")
    target_comparison = base_targets.merge(
        alt_targets,
        left_on=f"{baseline_id}__target_key",
        right_on=f"{alternative_id}__target_key",
        validate="one_to_one",
    )
    target_comparison.insert(0, "target_key", target_comparison.pop(
        f"{baseline_id}__target_key"
    ))
    for metric in (
        "confirmation_mean", "confirmation_sample_variance",
        "confirmation_primary_implausibility",
    ):
        left = f"{baseline_id}__{metric}"
        right = f"{alternative_id}__{metric}"
        if left in target_comparison and right in target_comparison:
            target_comparison[f"delta_{metric}__{alternative_id}_minus_{baseline_id}"] = (
                target_comparison[right] - target_comparison[left]
            )

    return {
        "suite_root": suite_root,
        "comparison": comparison,
        "case_ids": case_ids,
        "comparison_group": group,
        "case_summary": pd.DataFrame(case_rows),
        "candidate_summary": pd.concat(candidate_frames, ignore_index=True),
        "target_comparison": target_comparison,
        "paired_seed_differences": paired_seed,
        "paired_seed_summary": paired_summary,
        "loaded": loaded,
    }


def write_comparison(suite_root: Path, output_dir: Path) -> dict:
    normalized = load_comparison(suite_root)
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ComparisonError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized["case_summary"].to_csv(output_dir / "case_summary.csv", index=False)
    normalized["candidate_summary"].to_csv(output_dir / "candidate_summary.csv", index=False)
    normalized["target_comparison"].to_csv(output_dir / "target_comparison.csv", index=False)
    normalized["paired_seed_differences"].to_csv(
        output_dir / "paired_seed_differences.csv", index=False
    )
    normalized["paired_seed_summary"].to_csv(
        output_dir / "paired_seed_summary.csv", index=False
    )

    source_files = {}
    for case_id, item in normalized["loaded"].items():
        source_files[case_id] = {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in item["paths"].items()
        }
    manifest = {
        "adapter": "compare_coupled_calibration_suite.py",
        "status": "COMPARISON_COMPLETE",
        "comparison_id": normalized["comparison"].get("id"),
        "comparison_group": normalized["comparison_group"],
        "cases": normalized["case_ids"],
        "selection_rule": "primary pass first, then lowest confirmation I_max, then J, then id",
        "paired_replicates": len(normalized["loaded"][normalized["case_ids"][0]][
            "plan"
        ]["seeds"]["confirmation"]),
        "crn_interpretation": "Paired seeds reduce Monte Carlo noise in contrasts; they do not "
                              "increase independent sample size.",
        "source_files": source_files,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "simulations_started": 0,
    }
    _atomic_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    lines = [
        "# Coupled calibration case comparison", "",
        f"Comparison: `{manifest['comparison_id']}`", "",
        f"CRN group: `{manifest['comparison_group']}`", "",
        "The selected row in each case is the confirmed primary passer with the lowest "
        "confirmation I_max; if no candidate passes, it is the lowest-I_max confirmed candidate. "
        "This ranking is descriptive and is not a hypothesis test.", "",
        normalized["case_summary"].to_markdown(index=False), "",
        "Paired seeds reduce Monte Carlo noise in case differences. They do not create additional "
        "independent evidence. No simulation was run by this adapter.",
    ]
    _atomic_text(output_dir / "COMPARISON_REPORT.md", "\n".join(lines) + "\n")
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        normalized = load_comparison(args.suite_root)
        if args.validate_only:
            print(json.dumps({
                "ok": True, "cases": normalized["case_ids"],
                "comparison_group": normalized["comparison_group"],
                "simulations_started": 0, "files_written": 0,
            }, indent=2))
            return 0
        if args.output_dir is None:
            raise ComparisonError("--output-dir is required unless --validate-only is used")
        manifest = write_comparison(args.suite_root, args.output_dir)
        print(json.dumps(manifest, indent=2))
        return 0
    except ComparisonError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
