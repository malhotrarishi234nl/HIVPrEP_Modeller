#!/usr/bin/env python3
"""Confirm selected history-matching parameter vectors under many fresh seeds.

This is a stability check, not another history-matching wave.  It extracts exact
parameter vectors from a completed, checksum-verified run; evaluates each vector
under fresh independent seeds; averages those replicates; and recomputes the
prespecified target-relative implausibility scores with V_ensemble scaled by the
new replicate count.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import hm_implausibility as hm
import hm_target_register as tr
import run_history_matching as rhm


REPO_ROOT = Path(__file__).resolve().parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_manifest(source: Path) -> dict:
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Source run has no manifest.json: {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artefacts = list(manifest.get("artefacts", []))
    if not artefacts:
        raise ValueError("Source manifest records no artefacts")
    prefix = source.as_posix().rstrip("/") + "/"
    for entry in artefacts:
        relative = str(entry["path"])
        path = (source / relative).resolve()
        if path.as_posix() != source.as_posix() and not path.as_posix().startswith(prefix):
            raise ValueError(f"Unsafe source artefact path: {relative}")
        if not path.is_file():
            raise ValueError(f"Source artefact is missing: {relative}")
        if path.stat().st_size != int(entry["bytes"]) or _sha256(path) != entry["sha256"]:
            raise ValueError(f"Source artefact failed integrity verification: {relative}")
    return manifest


def _check_source_compatibility(
    manifest: dict,
    *,
    base_config_path: Path,
    calib_space_path: Path,
    register_path: Path,
    v_ensemble_path: Path | None,
) -> None:
    current_paths = {
        "base_config": base_config_path,
        "calib_space": calib_space_path,
        "target_register": register_path,
    }
    if v_ensemble_path is not None:
        current_paths["v_ensemble"] = v_ensemble_path
    elif manifest.get("run_settings", {}).get("v_ensemble_mode") != "predictive_local":
        raise ValueError("Omitting the variance file requires a predictive_local source")
    parent = dict(manifest.get("input_provenance", {}))
    for name, path in current_paths.items():
        parent_hash = (parent.get(name) or {}).get("sha256")
        current_hash = _sha256(path)
        if not parent_hash or current_hash != parent_hash:
            raise ValueError(
                f"Confirmation input mismatch for {name}: parent={parent_hash}, current={current_hash}"
            )
    parent_modules = ((parent.get("runtime") or {}).get("modules") or {})
    current_modules = rhm.repository_provenance()["modules"]
    differences = sorted(
        name for name in set(parent_modules) | set(current_modules)
        if parent_modules.get(name) != current_modules.get(name)
    )
    if differences:
        raise ValueError(
            "Confirmation runtime differs from the source run in: "
            f"{differences}. Do not attribute changes solely to stochastic confirmation."
        )


def _confirmation_worker(payload: dict) -> dict:
    result = rhm._evaluate_worker(payload)  # same evaluated simulator path as history matching
    result["source_design_id"] = int(payload["source_design_id"])
    result["confirmation_seed"] = int(payload["replicate_seeds"][0])
    result["confirmation_task_id"] = int(payload["design_id"])
    return result


def aggregate_replicates(
    raw: pd.DataFrame,
    selected: pd.DataFrame,
    parameter_names: Sequence[str],
) -> pd.DataFrame:
    """Aggregate one-row-per-seed output into one mean row per parameter vector."""
    metadata = {
        "design_id", "source_design_id", "confirmation_seed", "confirmation_task_id",
        "n_replicates", "replicate_seeds", "error",
    }
    output_columns = [
        column for column in raw.columns
        if column not in metadata
        and column not in parameter_names
        and not column.startswith("mcvar__")
    ]
    rows = []
    selected_by_id = selected.set_index("design_id")
    for source_id, group in raw.groupby("source_design_id", sort=True):
        source_row = selected_by_id.loc[int(source_id)]
        row = {"design_id": int(source_id)}
        row.update({name: float(source_row[name]) for name in parameter_names})
        for column in output_columns:
            values = pd.to_numeric(group[column], errors="coerce").to_numpy(dtype=float)
            row[column] = float(np.mean(values)) if np.all(np.isfinite(values)) else np.nan
            row[f"mcvar__{column}"] = (
                float(np.var(values, ddof=1)) if len(values) >= 2 and np.all(np.isfinite(values)) else np.nan
            )
        seeds = sorted(int(seed) for seed in group["confirmation_seed"])
        row["n_replicates"] = len(seeds)
        row["replicate_seeds"] = ";".join(str(seed) for seed in seeds)
        row["error"] = ""
        rows.append(row)
    return pd.DataFrame(rows).sort_values("design_id").reset_index(drop=True)


def _score_table(scored: dict, confirmed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    details = []
    for row_index, design in confirmed.reset_index(drop=True).iterrows():
        for column_index, target in enumerate(scored["targets"]):
            budget = scored["budgets"][target.key]
            output_column = f"{target.model_output}_{target.year}"
            details.append({
                "design_id": int(design["design_id"]),
                "target_key": target.key,
                "observed": float(target.z),
                "confirmed_mean": float(design[output_column]),
                "empirical_sd_per_run": float(np.sqrt(design[f"mcvar__{output_column}"])),
                "empirical_se_of_mean": float(np.sqrt(design[f"mcvar__{output_column}"] / design["n_replicates"])),
                "total_sigma": float(np.sqrt(budget.total_variance)),
                "implausibility": float(scored["matrix"][row_index, column_index]),
            })
    combined = scored["combined"]
    summary = pd.DataFrame({
        "design_id": confirmed["design_id"].astype(int),
        "i_max": combined.i_max,
        "i_second": combined.i_second,
        "i_third": combined.i_third,
        "binding_target": combined.binding_target,
        "is_nroy": combined.nroy_mask(cutoff=hm.IMPLAUSIBILITY_CUTOFF),
    })
    return pd.DataFrame(details), summary


def _comparison_table(
    original: pd.DataFrame,
    confirmed: pd.DataFrame,
    targets: Sequence[tr.ScalarTarget],
) -> pd.DataFrame:
    original_by_id = original.set_index("design_id")
    rows = []
    for _, design in confirmed.iterrows():
        design_id = int(design["design_id"])
        old = original_by_id.loc[design_id]
        for target in targets:
            column = f"{target.model_output}_{target.year}"
            new_mean = float(design[column])
            old_mean = float(old[column])
            mcvar = float(design[f"mcvar__{column}"])
            comparison_se = np.sqrt(mcvar * (1.0 / 3.0 + 1.0 / int(design["n_replicates"])))
            rows.append({
                "design_id": design_id,
                "target_key": target.key,
                "observed": float(target.z),
                "original_3rep_mean": old_mean,
                "confirmed_mean": new_mean,
                "confirmed_minus_original": new_mean - old_mean,
                "empirical_se_of_confirmed_mean": np.sqrt(mcvar / int(design["n_replicates"])),
                "original_vs_confirmed_shift_z": (
                    (old_mean - new_mean) / comparison_se if comparison_se > 0.0 else np.nan
                ),
            })
    return pd.DataFrame(rows)


def _write_manifest(output_dir: Path, payload: dict) -> None:
    artefacts = []
    for path in sorted(p for p in output_dir.rglob("*") if p.is_file() and p.name != "manifest.json"):
        artefacts.append({
            "path": str(path.relative_to(output_dir)),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    payload = {**payload, "artefacts": artefacts}
    (output_dir / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--design-ids", nargs="+", type=int, required=True)
    parser.add_argument("--source-sensitivity", default="target_relative_10pct")
    parser.add_argument("--base-config", required=True)
    parser.add_argument("--calib-space", required=True)
    parser.add_argument("--register", default="configs/hm_target_register.json")
    parser.add_argument("--v-ensemble", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--replicates", type=int, default=50)
    parser.add_argument("--simulation-seed", type=int, default=191000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.replicates < 2:
        raise ValueError("Confirmation requires at least two fresh replicates")
    if len(set(args.design_ids)) != len(args.design_ids):
        raise ValueError("--design-ids contains duplicates")

    source = Path(args.source_run).resolve()
    output_dir = Path(args.output_dir).resolve()
    if source == output_dir:
        raise ValueError("Confirmation output directory must differ from its read-only source")
    base_path = Path(args.base_config).resolve()
    space_path = Path(args.calib_space).resolve()
    register_path = Path(args.register).resolve()
    v_ensemble_path = Path(args.v_ensemble).resolve()
    manifest = _verified_manifest(source)
    _check_source_compatibility(
        manifest,
        base_config_path=base_path,
        calib_space_path=space_path,
        register_path=register_path,
        v_ensemble_path=v_ensemble_path,
    )

    history = list(manifest.get("history", []))
    if not history or int(history[-1].get("n_errors", 0)) != 0:
        raise ValueError("Source run has no error-free final wave")
    wave = int(history[-1]["wave"])
    evaluated_path = source / f"wave_{wave:02d}" / "evaluated.csv"
    eligible_path = source / f"wave_{wave:02d}" / f"sensitivity_{args.source_sensitivity}" / "nroy_members.csv"
    if not eligible_path.is_file():
        raise ValueError(f"Source sensitivity NROY file does not exist: {eligible_path}")
    evaluated = pd.read_csv(evaluated_path)
    eligible = pd.read_csv(eligible_path)
    eligible_ids = set(pd.to_numeric(eligible["design_id"], errors="raise").astype(int))
    invalid = sorted(set(args.design_ids) - eligible_ids)
    if invalid:
        raise ValueError(
            f"Design IDs are not members of sensitivity '{args.source_sensitivity}': {invalid}; "
            f"eligible IDs are {sorted(eligible_ids)}"
        )

    with base_path.open(encoding="utf-8") as handle:
        base_config = json.load(handle)
    with space_path.open(encoding="utf-8") as handle:
        calib_space = json.load(handle)
    parameter_names = [str(parameter["name"]) for parameter in calib_space["parameters"]]
    selected = evaluated.loc[evaluated["design_id"].isin(args.design_ids), ["design_id", *parameter_names]].copy()
    if len(selected) != len(args.design_ids):
        raise ValueError("One or more design IDs are absent from the source evaluated.csv")

    seeds = list(range(args.simulation_seed, args.simulation_seed + args.replicates))
    parent_seeds = {
        int(seed)
        for seed in str(manifest.get("run_settings", {}).get("simulation_seed", "")).split(";")
        if seed.strip().isdigit()
    }
    parent_start = manifest.get("run_settings", {}).get("simulation_seed")
    parent_k = int(manifest.get("run_settings", {}).get("replicates_per_design_point", 1))
    parent_stride = int(manifest.get("run_settings", {}).get("replicate_seed_stride", 1))
    if parent_start is not None:
        parent_seeds = {int(parent_start) + parent_stride * i for i in range(parent_k)}
    overlap = sorted(set(seeds) & parent_seeds)
    if overlap:
        raise ValueError(f"Confirmation seeds overlap source seeds: {overlap}")

    validation = {
        "status": "VALID",
        "source_run": str(source),
        "source_manifest_sha256": _sha256(source / "manifest.json"),
        "source_wave": wave,
        "source_sensitivity": args.source_sensitivity,
        "design_ids": args.design_ids,
        "parameter_count": len(parameter_names),
        "replicates_per_design": args.replicates,
        "seeds": [seeds[0], seeds[-1]],
        "total_simulations": len(selected) * len(seeds),
        "workers": args.workers,
    }
    if args.validate_only:
        print(json.dumps(validation, indent=2))
        return 0
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Refusing to write into non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output_dir / "selected_parameters.csv", index=False)

    tasks = []
    task_id = 0
    settings = manifest.get("run_settings", {})
    for design in selected.itertuples(index=False):
        params = {name: float(getattr(design, name)) for name in parameter_names}
        for seed in seeds:
            tasks.append({
                "design_id": task_id,
                "source_design_id": int(design.design_id),
                "params": params,
                "base_config": base_config,
                "calib_space": calib_space,
                "seed": seed,
                "replicate_seeds": [seed],
                "deterministic_calibration": False,
                "simulation_start_year": settings.get("simulation_start_year", 1975),
                "scoring_start_year": settings.get("scoring_start_year", 1990),
            })
            task_id += 1

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_confirmation_worker, payload) for payload in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 10 == 0 or len(rows) == len(tasks):
                print(f"confirmation: completed {len(rows)}/{len(tasks)} simulations", flush=True)
    raw = pd.DataFrame(rows).sort_values("confirmation_task_id").reset_index(drop=True)
    raw.to_csv(output_dir / "raw_replicates.csv", index=False)
    errors = raw["error"].fillna("").astype(str).str.strip().ne("")
    if errors.any():
        raw.loc[errors].to_csv(output_dir / "errors.csv", index=False)
        raise RuntimeError(f"{int(errors.sum())} confirmation simulations failed; see errors.csv")

    confirmed = aggregate_replicates(raw, selected, parameter_names)
    confirmed.to_csv(output_dir / "confirmed_means.csv", index=False)
    register = tr.load_register(register_path)
    target_series = history[-1]["target_series"]
    scored_years = manifest.get("scored_years")
    v_ensemble = rhm.load_v_ensemble_terms(v_ensemble_path, replicates_per_design_point=args.replicates)
    summaries = []
    score_details = {}
    for label, multiplier in (("target_relative_10pct", 0.10), ("target_relative_20pct", 0.20)):
        scored = rhm.score_wave(
            confirmed,
            register,
            target_series,
            disc_multiplier=multiplier,
            disc_method="relative_to_target",
            v_ensemble=v_ensemble,
            scored_years=scored_years,
        )
        detail, summary = _score_table(scored, confirmed)
        detail.to_csv(output_dir / f"target_scores_{label}.csv", index=False)
        summary.insert(1, "scoring_rule", label)
        summaries.append(summary)
        score_details[label] = scored
    summary = pd.concat(summaries, ignore_index=True)
    summary.to_csv(output_dir / "confirmation_summary.csv", index=False)

    targets = score_details["target_relative_10pct"]["targets"]
    comparison = _comparison_table(evaluated, confirmed, targets)
    comparison.to_csv(output_dir / "original_vs_confirmed.csv", index=False)

    lines = [
        "# History-matching stochastic confirmation",
        "",
        f"Source wave: {wave}; source sensitivity: `{args.source_sensitivity}`.",
        f"Fresh seeds: {seeds[0]}–{seeds[-1]} ({args.replicates} per design).",
        "",
        "| Design ID | Rule | I_max | Binding target | NROY |",
        "|---:|---|---:|---|---|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.design_id} | {row.scoring_rule} | {row.i_max:.4f} | "
            f"{row.binding_target} | {'yes' if row.is_nroy else 'no'} |"
        )
    lines.extend([
        "",
        "This confirmation evaluates stochastic stability of selected parameter vectors. It is not a new wave,",
        "does not update the NROY region, and does not turn the selected vectors into posterior samples.",
    ])
    (output_dir / "RUN_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        **validation,
        "base_config": {"path": str(base_path), "sha256": _sha256(base_path)},
        "calib_space": {"path": str(space_path), "sha256": _sha256(space_path)},
        "target_register": {"path": str(register_path), "sha256": _sha256(register_path)},
        "v_ensemble": {"path": str(v_ensemble_path), "sha256": _sha256(v_ensemble_path)},
        "script": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
        "python": sys.version,
        "platform": platform.platform(),
    }
    _write_manifest(output_dir, payload)
    print(summary.to_json(orient="records", indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
