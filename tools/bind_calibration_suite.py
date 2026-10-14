#!/usr/bin/env python3
"""Freeze selected coupled-calibration candidates as HTA model backbones.

This adapter runs no simulation. It applies the exact calibration injection
path to the automatically selected confirmed candidate for each case, writes
immutable model JSON files, and records hashes linking every output to the
confirmation evidence that selected it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import compare_coupled_calibration_suite as comparison_adapter  # noqa: E402
import run_coupled_calibration as calibration_runner  # noqa: E402


class BindingError(ValueError):
    """Calibration evidence cannot be bound without ambiguity."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def bind(
    suite_root: Path, output_dir: Path, *, allow_unconfirmed: bool = False
) -> dict:
    normalized = comparison_adapter.load_comparison(suite_root)
    if not allow_unconfirmed:
        failed = [
            case_id for case_id in normalized["case_ids"]
            if not bool(normalized["loaded"][case_id]["selected"]["_pass"])
        ]
        if failed:
            raise BindingError(
                "refusing automatic HTA binding because fresh confirmation did not "
                f"pass for: {failed}"
            )
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BindingError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    bound: dict[str, dict] = {}
    for case_id in normalized["case_ids"]:
        item = normalized["loaded"][case_id]
        selected_id = int(item["selected"]["candidate_id"])
        raw = item["raw"]
        params = raw.get("params")
        if not isinstance(params, dict) or not params:
            raise BindingError(
                f"selected candidate {selected_id} for {case_id} lacks injected parameters"
            )

        plan = item["plan"]
        base_path = ROOT / plan["inputs"]["base_config"]
        space_path = ROOT / plan["inputs"]["calib_space"]
        if not base_path.is_file() or not space_path.is_file():
            raise BindingError(f"resolved inputs for {case_id} are missing")
        base_config = json.loads(base_path.read_text(encoding="utf-8"))
        space_json = json.loads(space_path.read_text(encoding="utf-8"))
        model = calibration_runner.injected_config(base_config, params, space_json)

        confirmation_pass = bool(item["selected"]["_pass"])
        model["_calibration_binding"] = {
            "case_id": case_id,
            "design": plan["design"]["name"],
            "selected_candidate_id": selected_id,
            "selection_rule": (
                "confirmed primary pass first, then lowest confirmation I_max, "
                "then confirmation J, then candidate id"
            ),
            "confirmation_primary_pass": confirmation_pass,
            "confirmation_primary_i_max": float(
                item["selected"]["confirmation_primary_i_max"]
            ),
            "confirmation_j": float(item["selected"]["confirmation_j"]),
            "params_sha256": raw.get("params_sha256"),
            "selected_candidate_raw_sha256": _sha256(
                item["paths"]["selected_candidate_raw"]
            ),
            "source_confirmation_manifest_sha256": _sha256(
                item["paths"]["confirmation_manifest"]
            ),
            "source_plan_sha256": _sha256(item["paths"]["plan"]),
            "bound_utc": datetime.now(timezone.utc).isoformat(),
            "interpretation": (
                "Point-calibrated HTA backbone. The confirmation shortlist is not a "
                "posterior sample and must not be presented as one."
            ),
        }
        filename = f"selected_model_{case_id}.json"
        path = output_dir / filename
        _atomic_json(path, model)
        bound[case_id] = {
            "file": filename,
            "sha256": _sha256(path),
            "selected_candidate_id": selected_id,
            "confirmation_primary_pass": confirmation_pass,
        }

    manifest = {
        "schema_version": "1.0",
        "status": "CALIBRATION_MODELS_BOUND",
        "suite_root": str(Path(suite_root).resolve()),
        "comparison_group": normalized["comparison_group"],
        "case_order": normalized["case_ids"],
        "models": bound,
        "simulations_started": 0,
        "allow_unconfirmed": bool(allow_unconfirmed),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(output_dir / "binding_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-unconfirmed",
        action="store_true",
        help="explicitly bind a best available non-passer for provisional analysis",
    )
    args = parser.parse_args(argv)
    try:
        print(json.dumps(bind(
            args.suite_root, args.output_dir,
            allow_unconfirmed=args.allow_unconfirmed,
        ), indent=2))
        return 0
    except (BindingError, comparison_adapter.ComparisonError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
