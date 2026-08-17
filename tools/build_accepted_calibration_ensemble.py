#!/usr/bin/env python3
"""
Build the accepted calibration ensemble manifest from an ABC-SMC run directory.

Post-calibration assembly only. Does not rerun calibration or modify configs.

Usage:
    python3 tools/build_accepted_calibration_ensemble.py \
        --run-dir outputs/calibration/abc_smc_v13_8_2_margin5_targeted_local_rank007 \
        --output-dir outputs/calibration/accepted_ensemble_v13_8_3
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _find_particles_csv(run_dir: Path) -> Path | None:
    matches = sorted(run_dir.glob("abc_smc_particles_*.csv"))
    return matches[-1] if matches else None


def _load_particles(run_dir: Path) -> pd.DataFrame | None:
    csv_path = _find_particles_csv(run_dir)
    if csv_path is None:
        return None
    return pd.read_csv(csv_path, low_memory=False)


def _extract_manifest_row(
    rank_path: Path,
    rank_file_index: int,
    output_dir: Path,
    particles_by_id: dict[int, dict],
) -> dict[str, Any]:
    config = _load_json(rank_path)
    meta = config.get("calibration_metadata", {}).get("abc_smc_calibration", {})
    particle_id = meta.get("particle_id")
    particle_row = particles_by_id.get(particle_id, {}) if particle_id is not None else {}

    copied_path = output_dir / "copied_configs" / rank_path.name

    # Gate metrics from the particles CSV (recorded at calibration time)
    diag_total_ratio = particle_row.get("acceptance_diagnosis_gate_new_diagnoses_ratio")
    diag_msm_ratio = particle_row.get("acceptance_diagnosis_gate_new_diagnoses_msm_ratio")
    incidence_model = particle_row.get("acceptance_incidence_gate_model")
    incidence_target = particle_row.get("acceptance_incidence_gate_target")
    incidence_upper = particle_row.get("acceptance_incidence_gate_upper")
    incidence_effective_upper = particle_row.get("acceptance_incidence_gate_effective_upper")
    incidence_recent_ratio = particle_row.get("acceptance_incidence_gate_recent_median_model_target_ratio")
    plhiv_recent_mape = particle_row.get("acceptance_plhiv_gate_recent_median_abs_pct")
    aids_bounds_fraction = particle_row.get("acceptance_aids_death_gate_bounds_fraction")
    art_recent_mape = particle_row.get("acceptance_art_stock_gate_recent_median_abs_pct")

    return {
        "rank": meta.get("rank", rank_file_index),
        "source_config_path": str(rank_path.resolve()),
        "copied_config_path": str(copied_path.resolve()),
        "generation": meta.get("generation"),
        "particle_id": particle_id,
        "particle_seed": meta.get("particle_seed"),
        "proposal_source": particle_row.get("proposal_source"),
        "parent_particle_id": particle_row.get("parent_particle_id"),
        "abc_distance": meta.get("abc_distance"),
        "total_ll": meta.get("total_ll"),
        "diagnosis_total_ratio": diag_total_ratio,
        "diagnosis_msm_ratio": diag_msm_ratio,
        "incidence_2023_model": incidence_model,
        "incidence_2023_target": incidence_target,
        "incidence_2023_upper": incidence_upper,
        "incidence_2023_effective_upper": incidence_effective_upper,
        "incidence_recent_median_model_target_ratio": incidence_recent_ratio,
        "plhiv_recent_mape": plhiv_recent_mape,
        "aids_death_bounds_fraction": aids_bounds_fraction,
        "art_stock_recent_mape": art_recent_mape,
        "diagnosis_gate_pass": particle_row.get("diagnosis_gate_pass"),
        "plhiv_gate_pass": particle_row.get("plhiv_gate_pass"),
        "incidence_gate_pass": particle_row.get("incidence_gate_pass"),
        "aids_death_gate_pass": particle_row.get("aids_death_gate_pass"),
        "art_stock_gate_pass": particle_row.get("art_stock_gate_pass"),
        "overall_accept": meta.get("overall_accept", False),
        "acceptance_n_failures": meta.get("acceptance_n_failures"),
        "acceptance_gate_audit_embedded": meta.get("acceptance_gate_audit"),
        "stage": meta.get("stage"),
        "created_at": meta.get("created_at"),
    }


def _select_base_case(rows: list[dict]) -> dict | None:
    accepted = [r for r in rows if r.get("overall_accept")]
    if not accepted:
        return None
    # Rule: lowest abc_distance; on near-tie prefer higher diagnosis ratio and
    # lower incidence relative to effective upper bound.
    def sort_key(r: dict):
        dist = r.get("abc_distance") or float("inf")
        neg_diag = -(r.get("diagnosis_total_ratio") or 0.0)
        inc_margin = (r.get("incidence_2023_model") or 0.0) / max(
            r.get("incidence_2023_effective_upper") or 1.0, 1.0
        )
        return (dist, neg_diag, inc_margin)

    return min(accepted, key=sort_key)


def _write_economic_inputs(rows: list[dict], base_row: dict | None, output_dir: Path) -> None:
    lines = []
    econ_rows = []

    if base_row is not None:
        bc_path = base_row.get("copied_config_path", base_row.get("source_config_path", ""))
        lines.append("# Base case")
        lines.append(bc_path)
        lines.append("")
        econ_rows.append({
            "role": "base_case",
            "rank": base_row.get("rank"),
            "config_path": bc_path,
            "abc_distance": base_row.get("abc_distance"),
            "diagnosis_total_ratio": base_row.get("diagnosis_total_ratio"),
            "incidence_2023_model": base_row.get("incidence_2023_model"),
        })

    lines.append(f"# Accepted ensemble ({len(rows)} candidates)")
    lines.append("# Suggested primary PSA subset: top 20 by rank (or all 59 if runtime permits)")
    lines.append("")
    lines.append("# All accepted configs:")
    for r in rows:
        cp = r.get("copied_config_path", r.get("source_config_path", ""))
        lines.append(cp)
        econ_rows.append({
            "role": "ensemble",
            "rank": r.get("rank"),
            "config_path": cp,
            "abc_distance": r.get("abc_distance"),
            "diagnosis_total_ratio": r.get("diagnosis_total_ratio"),
            "incidence_2023_model": r.get("incidence_2023_model"),
        })

    (output_dir / "economic_model_candidate_inputs.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    pd.DataFrame(econ_rows).to_csv(
        output_dir / "economic_model_candidate_inputs.csv", index=False
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build accepted calibration ensemble manifest from ABC-SMC run."
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="ABC-SMC run directory containing abc_smc_best_fit_rank_*.json files.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output directory for ensemble manifest and copied configs.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Limit accepted candidates (default: all).",
    )
    parser.add_argument(
        "--min-candidates",
        type=int,
        default=20,
        help="Exit if fewer accepted candidates found (default: 20).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir = Path(args.run_dir)
    output_dir = Path(args.output_dir)

    if not run_dir.is_dir():
        print(f"ERROR: run-dir not found: {run_dir}", file=sys.stderr)
        return 1

    rank_files = sorted(run_dir.glob("abc_smc_best_fit_rank_*.json"))
    if not rank_files:
        print(f"ERROR: no abc_smc_best_fit_rank_*.json files in {run_dir}", file=sys.stderr)
        return 1

    # Load particles CSV for gate metrics and proposal lineage
    particles_df = _load_particles(run_dir)
    if particles_df is not None:
        particles_by_id: dict[int, dict] = {
            int(row["particle_id"]): row.to_dict()
            for _, row in particles_df.iterrows()
            if "particle_id" in particles_df.columns
        }
        print(f"Loaded particles CSV: {len(particles_by_id)} rows")
    else:
        particles_by_id = {}
        print("WARNING: no particles CSV found; gate metrics will be missing from manifest")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "copied_configs").mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for idx, rank_path in enumerate(rank_files, start=1):
        config = _load_json(rank_path)
        meta = config.get("calibration_metadata", {}).get("abc_smc_calibration", {})
        if not meta.get("overall_accept"):
            continue
        row = _extract_manifest_row(rank_path, idx, output_dir, particles_by_id)
        rows.append(row)

    rows.sort(key=lambda r: r.get("rank") if r.get("rank") is not None else 9999)

    if args.max_candidates is not None:
        rows = rows[: args.max_candidates]

    n = len(rows)
    if n < args.min_candidates:
        print(
            f"ERROR: only {n} accepted candidates found; min-candidates={args.min_candidates}",
            file=sys.stderr,
        )
        return 1

    print(f"Found {n} accepted candidates")

    # Copy configs
    for row in rows:
        src = Path(row["source_config_path"])
        dst = Path(row["copied_config_path"])
        shutil.copy2(src, dst)

    # Write manifest
    df = pd.DataFrame(rows)
    df.to_csv(output_dir / "accepted_ensemble_manifest.csv", index=False)
    df.to_json(
        output_dir / "accepted_ensemble_manifest.json",
        orient="records",
        indent=2,
        default_handler=str,
    )

    # Write config paths text
    paths_txt = output_dir / "accepted_config_paths.txt"
    paths_txt.write_text(
        "\n".join(str(r["copied_config_path"]) for r in rows) + "\n",
        encoding="utf-8",
    )

    # Select and write base case
    base_row = _select_base_case(rows)
    if base_row is not None:
        (output_dir / "base_case_candidate.json").write_text(
            json.dumps(base_row, indent=2, default=str), encoding="utf-8"
        )
        print(
            f"Base case: rank {base_row['rank']}, "
            f"abc_distance={base_row.get('abc_distance', 'N/A'):.4f}, "
            f"diagnosis_ratio={base_row.get('diagnosis_total_ratio', 'N/A')}, "
            f"incidence_2023={base_row.get('incidence_2023_model', 'N/A')}"
        )
    else:
        print("WARNING: no base case selected (no overall_accept=True rows)")

    # Write economic model inputs
    _write_economic_inputs(rows, base_row, output_dir)

    print(f"Manifest CSV:  {output_dir / 'accepted_ensemble_manifest.csv'}")
    print(f"Manifest JSON: {output_dir / 'accepted_ensemble_manifest.json'}")
    print(f"Config paths:  {output_dir / 'accepted_config_paths.txt'}")
    print(f"Copied configs: {output_dir / 'copied_configs'} ({n} files)")
    print(f"Economic inputs: {output_dir / 'economic_model_candidate_inputs.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
