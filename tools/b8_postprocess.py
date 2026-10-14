#!/usr/bin/env python3
"""B-8 post-processing: derived tables, artifact hashes, manifest closure.

Separated from `run_b8_deterministic_target_level_comparison.py` so that no
model run is needed to regenerate the derived tables.  Everything here is
computed from files already written by the runner plus two existing artifacts
(the A-5a replicate file and the HM prototype's per-target implausibility
table).  No new simulation is executed.

Derived tables written:

* `raw/deterministic_percentile_rank.csv` - where the single deterministic value
  sits inside the n=50 stochastic replicate distribution for each target-year.
  This is the effect-size companion to |z|: |z| against the SE of the mean is
  large whenever n is large, whereas the percentile rank does not shrink with n.
* `raw/illustrative_implausibility_at_nearfit.csv` - the tier-1 implausibility
  I = |z_obs - f(x)| / total_sigma recomputed at THIS ONE near-fit parameter
  point under each run mode, using the `total_sigma` the HM prototype computed
  from its own 4,000-particle ensemble.  APPROXIMATION, stated as such in the
  report: V_disc is defined relative to a wave design's output variance, so a
  faithful recomputation needs the whole wave re-run.  The table shows the
  DIRECTION and rough SIZE of the run-mode effect on implausibility, not a new
  implausibility estimate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile_rank_table(replicates: pd.DataFrame, deterministic: pd.DataFrame) -> pd.DataFrame:
    det = deterministic.set_index(["metric", "year"])["value"]
    rows: list[dict] = []
    for (metric, year), group in replicates.groupby(["metric", "year"], sort=True):
        values = group["value"].to_numpy(dtype=float)
        point = float(det.loc[(metric, int(year))])
        rank = 100.0 * float(np.mean(values < point)) + 50.0 * float(np.mean(values == point))
        rows.append(
            {
                "metric": metric,
                "year": int(year),
                "deterministic": point,
                "n_replicates": int(values.size),
                "replicate_median": float(np.median(values)),
                "replicate_mean": float(values.mean()),
                "percentile_rank_of_deterministic": rank,
                "replicate_skewness": float(pd.Series(values).skew()),
                "deterministic_above_replicate_median": point > float(np.median(values)),
                "deterministic_above_all_replicates": bool(point > values.max()),
            }
        )
    return pd.DataFrame(rows)


def implausibility_table(comparison: pd.DataFrame, implausibility_csv: Path) -> pd.DataFrame:
    obs = pd.read_csv(implausibility_csv)
    keep = [
        "target_key",
        "series_name",
        "model_output",
        "year",
        "epistemic_tier",
        "role",
        "z",
        "total_sigma",
        "i_max",
        "frac_ruled_out",
    ]
    obs = obs[keep].rename(columns={"model_output": "metric", "z": "observed_target"})
    merged = obs.merge(
        comparison[["metric", "year", "deterministic", "stochastic_mean"]],
        on=["metric", "year"],
        how="inner",
    )
    merged["I_deterministic_at_nearfit"] = (
        merged["observed_target"] - merged["deterministic"]
    ).abs() / merged["total_sigma"]
    merged["I_stochastic_mean_at_nearfit"] = (
        merged["observed_target"] - merged["stochastic_mean"]
    ).abs() / merged["total_sigma"]
    merged["delta_I_stochastic_minus_deterministic"] = (
        merged["I_stochastic_mean_at_nearfit"] - merged["I_deterministic_at_nearfit"]
    )
    merged["switching_to_stochastic_mean_worsens_fit"] = (
        merged["delta_I_stochastic_minus_deterministic"] > 0
    )
    return merged.sort_values(["series_name", "year"]).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--a5a-dir", type=Path, required=True)
    parser.add_argument(
        "--implausibility-csv",
        type=Path,
        default=ROOT
        / "outputs/calibration/hm_prototype_20260910/raw"
        / "post_c1_without_non_msm__per_target_implausibility.csv",
    )
    parser.add_argument(
        "--cross-version-determinism-json",
        type=Path,
        default=None,
        help=(
            "Optional JSON of the annual outputs produced by running the same "
            "deterministic point on the A-5a-era source tree; used to record "
            "whether the run_v3.py change since A-5a moved the deterministic path."
        ),
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw"
    comparison = pd.read_csv(raw_dir / "target_level_comparison.csv")
    deterministic = pd.read_csv(raw_dir / "deterministic_annual_outputs.csv")
    replicates = pd.read_csv(args.a5a_dir.resolve() / "raw" / "replicates.csv")

    ranks = percentile_rank_table(replicates, deterministic)
    ranks.to_csv(raw_dir / "deterministic_percentile_rank.csv", index=False)

    imp = implausibility_table(comparison, args.implausibility_csv.resolve())
    imp.to_csv(raw_dir / "illustrative_implausibility_at_nearfit.csv", index=False)

    cross_version = None
    if args.cross_version_determinism_json is not None:
        old = json.loads(args.cross_version_determinism_json.read_text(encoding="utf-8"))
        mismatches = []
        for _, row in deterministic.iterrows():
            old_value = old[row["metric"]][str(int(row["year"]))]
            if float(old_value) != float(row["value"]):
                mismatches.append(
                    {
                        "metric": row["metric"],
                        "year": int(row["year"]),
                        "a5a_era_code": float(old_value),
                        "current_code": float(row["value"]),
                    }
                )
        cross_version = {
            "n_target_years_compared": int(len(deterministic)),
            "n_mismatches": len(mismatches),
            "deterministic_path_bit_identical_across_run_v3_change": not mismatches,
            "mismatches": mismatches[:20],
        }
        (raw_dir / "cross_version_determinism_check.json").write_text(
            json.dumps(cross_version, indent=2) + "\n"
        )

    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["postprocess_command"] = (
        "tools/b8_postprocess.py --output-dir <this dir> --a5a-dir "
        f"{args.a5a_dir} --implausibility-csv {args.implausibility_csv}"
    )
    manifest["input_sha256"]["hm_prototype/per_target_implausibility.csv"] = sha256(
        args.implausibility_csv.resolve()
    )
    manifest["input_sha256"]["a5a/raw/replicates.csv"] = sha256(
        args.a5a_dir.resolve() / "raw" / "replicates.csv"
    )
    manifest["input_sha256"][f"code/tools/{Path(__file__).name}"] = sha256(
        Path(__file__).resolve()
    )
    if cross_version is not None:
        manifest["cross_version_determinism_check"] = {
            k: v for k, v in cross_version.items() if k != "mismatches"
        }

    artifact_paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    manifest["output_sha256"] = {
        str(path.relative_to(output_dir)): sha256(path) for path in artifact_paths
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    all_paths = sorted(path for path in output_dir.rglob("*") if path.is_file())
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(output_dir)}\n"
            for path in all_paths
            if path.name != "SHA256SUMS.txt"
        ),
        encoding="utf-8",
    )
    print(f"B-8 post-processing complete for {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
