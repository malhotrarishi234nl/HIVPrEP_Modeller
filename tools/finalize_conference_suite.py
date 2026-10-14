#!/usr/bin/env python3
"""Bind completed 2008/2010 policy and PSA outputs into paired long tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd


class FinalizeError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _require(path: Path) -> Path:
    if not path.is_file():
        raise FinalizeError(f"required completed output is missing: {path}")
    return path


def _require_status(path: Path, expected: str) -> None:
    payload = json.loads(_require(path).read_text(encoding="utf-8"))
    if payload.get("status") != expected:
        raise FinalizeError(
            f"{path} has status {payload.get('status')!r}; expected {expected!r}"
        )


def _paired(
    left: pd.DataFrame, right: pd.DataFrame, *, keys: list[str], metrics: list[str]
) -> pd.DataFrame:
    left_columns = [*keys, *metrics]
    right_columns = [*keys, *metrics]
    merged = left[left_columns].merge(
        right[right_columns], on=keys, how="inner", validate="one_to_one",
        suffixes=("__2008", "__2010"),
    )
    for metric in metrics:
        merged[f"difference__2008_minus_2010__{metric}"] = (
            merged[f"{metric}__2008"] - merged[f"{metric}__2010"]
        )
    return merged


def finalize(root: Path) -> dict:
    root = root.resolve()
    output = root / "combined"
    output.mkdir(parents=True, exist_ok=True)
    _require_status(
        root / "policy_primary_2008/final_manifest.json", "POLICY_MATRIX_FINALIZED"
    )
    _require_status(
        root / "policy_bridge_2010/final_manifest.json", "POLICY_MATRIX_FINALIZED"
    )
    _require_status(root / "psa_primary_2008/final_manifest.json", "PSA_MATRIX_COMPLETE")
    _require_status(root / "psa_bridge_2010/final_manifest.json", "PSA_MATRIX_COMPLETE")
    policy_2008_path = _require(root / "policy_primary_2008/policy_seed_results.csv")
    policy_2010_path = _require(root / "policy_bridge_2010/policy_seed_results.csv")
    psa_2008_path = _require(root / "psa_primary_2008/psa_matrix_incremental.csv")
    psa_2010_path = _require(root / "psa_bridge_2010/psa_matrix_incremental.csv")

    policy_metrics = [
        "delta_cost", "delta_qaly", "nmb", "infections_averted",
        "reference_cost", "intervention_cost", "reference_qaly", "intervention_qaly",
    ]
    policy = _paired(
        pd.read_csv(policy_2008_path), pd.read_csv(policy_2010_path),
        keys=["seed", "scenario"], metrics=policy_metrics,
    )
    policy.to_csv(output / "paired_policy_2008_minus_2010.csv", index=False)
    policy_summary = policy.groupby("scenario", sort=False).agg(
        paired_seeds=("seed", "nunique"),
        mean_delta_cost_difference=("difference__2008_minus_2010__delta_cost", "mean"),
        mean_delta_qaly_difference=("difference__2008_minus_2010__delta_qaly", "mean"),
        mean_nmb_difference=("difference__2008_minus_2010__nmb", "mean"),
        mean_infections_averted_difference=(
            "difference__2008_minus_2010__infections_averted", "mean"
        ),
    ).reset_index()
    policy_summary.to_csv(output / "paired_policy_structural_summary.csv", index=False)

    psa_metrics = ["delta_cost", "delta_qaly", "nmb", "infections_averted"]
    psa = _paired(
        pd.read_csv(psa_2008_path), pd.read_csv(psa_2010_path),
        keys=["draw_index", "scenario", "comparison"], metrics=psa_metrics,
    )
    psa.to_csv(output / "paired_psa_2008_minus_2010.csv", index=False)
    psa_summary = psa.groupby(["scenario", "comparison"], sort=False).agg(
        paired_draws=("draw_index", "nunique"),
        mean_delta_cost_difference=("difference__2008_minus_2010__delta_cost", "mean"),
        mean_delta_qaly_difference=("difference__2008_minus_2010__delta_qaly", "mean"),
        mean_nmb_difference=("difference__2008_minus_2010__nmb", "mean"),
        mean_infections_averted_difference=(
            "difference__2008_minus_2010__infections_averted", "mean"
        ),
    ).reset_index()
    psa_summary.to_csv(output / "paired_psa_structural_summary.csv", index=False)

    sources = [policy_2008_path, policy_2010_path, psa_2008_path, psa_2010_path]
    outputs = sorted(output.glob("*.csv"))
    manifest = {
        "status": "CONFERENCE_RESULTS_BOUND",
        "pairing": "2008 minus 2010 on identical stochastic seeds / PSA draw indices",
        "policy_common_scenarios": int(policy["scenario"].nunique()),
        "policy_paired_rows": len(policy),
        "psa_common_scenarios": int(psa["scenario"].nunique()),
        "psa_paired_rows": len(psa),
        "source_sha256": {str(path.relative_to(root)): _sha256(path) for path in sources},
        "output_sha256": {path.name: _sha256(path) for path in outputs},
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(output / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(finalize(args.suite_root), indent=2))
        return 0
    except FinalizeError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
