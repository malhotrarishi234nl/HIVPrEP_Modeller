#!/usr/bin/env python3
"""Generate reviewable robust-NROY observed-value and ``B_i`` evidence.

This command is preparation-only.  It resolves the frozen 13 targets through
the authoritative target register and workbook, reconstructs the non-process
variance components with the existing coupled-calibration code, and writes a
hash-bound evidence package.  It never constructs the simulation engine.

Without ``--draft`` the plan must be fully signed and load through
``nroy_contract.FrozenPlan``.  ``--draft`` is useful before sign-off, but its
outputs are labelled ``PRE_SIGN_REVIEW`` and are invalid for production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_identity_gate as nig  # noqa: E402
import run_robust_nroy_freeze as rn  # noqa: E402

SCHEMA = "robust_nroy_acceptance_inputs/1.0.0"
DEFAULT_WAVE_PLAN = ROOT / "configs/hm_wave_plan_forecast_windows_20260921.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def portable_path(path: Path) -> str:
    """Record a path relative to the repository when possible."""
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), ROOT.resolve())


def proposal(raw: dict, dotted: str):
    node = raw
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    value = node[parts[-1]]
    if nc.is_unsigned(value):
        value = node.get(f"{parts[-1]}_proposal")
    if value is None or nc.is_unsigned(value):
        raise nc.ContractError(f"{dotted} has no signed value or recorded proposal")
    return value


def write_json(path: Path, payload: object) -> None:
    # Preserve insertion order: the observed/B_i maps are themselves a
    # reviewable rendering of the frozen target-vector order.
    path.write_text(json.dumps(payload, indent=2) + "\n")


def generate(args: argparse.Namespace) -> int:
    plan_path = resolve(args.plan)
    raw = nc.load_plan_json(plan_path, root=ROOT)
    if args.draft:
        plan = None
        register_path = resolve(proposal(raw, "target_contract.register_path"))
        target_order = tuple(proposal(raw, "target_contract.frozen_order"))
        base_config = resolve(proposal(raw, "models.base_config"))
        m0_space = resolve(proposal(raw, "models.m0.calib_space"))
        status = "PRE_SIGN_REVIEW"
    else:
        plan = nc.FrozenPlan.load(plan_path, root=ROOT)
        register_path = resolve(plan.targets.register_path)
        target_order = tuple(plan.targets.frozen_order)
        base_config = resolve(plan.require("models.base_config"))
        m0_space = resolve(plan.require("models.m0.calib_space"))
        status = "FINAL" if getattr(args, "approve_for_production", False) else "FINAL_REVIEW_REQUIRED"

    if getattr(args, "approve_for_production", False) and args.draft:
        raise nc.ContractError("a pre-sign draft cannot be approved for production")
    if getattr(args, "approve_for_production", False) and not str(
        getattr(args, "approved_by", "") or ""
    ).strip():
        raise nc.ContractError("--approve-for-production requires --approved-by")

    if target_order != tuple(ccs.FROZEN_TARGET_ORDER):
        raise nc.ContractError(
            "acceptance-input generator is pinned to the reviewed 13-target order; "
            f"plan declares {list(target_order)}"
        )
    wave_plan_path = resolve(args.wave_plan)
    wave_plan = nc.strict_json_load(wave_plan_path, what="history-matching wave plan")
    if not isinstance(wave_plan, dict):
        raise nc.ContractError("history-matching wave plan must be a JSON object")
    targets = ccs.load_frozen_targets(register_path, wave_plan)

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise nc.ContractError(f"refusing to overwrite non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    observed = {target.key: target.z for target in targets}
    base_variance = {target.key: target.b_variance for target in targets}
    observed_path = out / "observed_values.json"
    variance_path = out / "base_variance.json"
    components_path = out / "component_variance_budget.csv"
    write_json(observed_path, observed)
    write_json(variance_path, base_variance)

    fields = list(targets[0].as_row())
    with components_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(target.as_row() for target in targets)

    register_raw = nc.strict_json_load(register_path, what="target register")
    if not isinstance(register_raw, dict):
        raise nc.ContractError("target register must be a JSON object")
    workbook_path = resolve(register_raw["source_workbook"])
    expected_workbook_sha256 = register_raw.get("source_workbook_sha256")
    if expected_workbook_sha256 is not None and sha256(workbook_path) != expected_workbook_sha256:
        raise nc.ContractError(
            "source workbook does not match source_workbook_sha256 in the target register"
        )
    command = " ".join(shlex.quote(value) for value in [sys.executable, *sys.argv])
    provenance = {
        "schema": SCHEMA,
        "status": status,
        "production_eligible": status == "FINAL",
        "approved": status == "FINAL",
        "approved_by": str(getattr(args, "approved_by", "")).strip() if status == "FINAL" else None,
        "approved_utc": datetime.now(timezone.utc).isoformat() if status == "FINAL" else None,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "plan_sha256": sha256(plan_path),
        "target_order": list(target_order),
        "target_count": len(target_order),
        "acceptance_rule": "I_max <= 3 on B_i + s_i^2 (1 + 1/K)",
        "base_variance_formula": "B_i = V_obs + V_code + V_disc + declared extra non-process terms",
        "process_variance": "excluded from B_i; added during scoring as s_i^2 (1 + 1/K)",
        "discrepancy": {
            "method": ccs.DISCREPANCY_METHOD,
            "fraction": ccs.PRIMARY_DISCREPANCY,
            "added_exactly_once": True,
        },
        "source_bindings": {
            "plan": {"path": portable_path(plan_path), "sha256": sha256(plan_path)},
            "base_config": {"path": portable_path(base_config), "sha256": sha256(base_config)},
            "m0_calibration_space": {"path": portable_path(m0_space), "sha256": sha256(m0_space)},
            "target_register": {"path": portable_path(register_path), "sha256": sha256(register_path)},
            "source_workbook": {
                "path": portable_path(workbook_path),
                "sha256": sha256(workbook_path),
                **(
                    {"expected_sha256": expected_workbook_sha256}
                    if expected_workbook_sha256 is not None else {}
                ),
            },
            "wave_plan": {"path": portable_path(wave_plan_path), "sha256": sha256(wave_plan_path)},
        },
        "scientific_code_sha256": rn._code_sha256(),
        "scientific_runtime": nig.scientific_runtime_fingerprint(),
        "outputs": {
            "observed_values": {"path": observed_path.name, "sha256": sha256(observed_path)},
            "base_variance": {"path": variance_path.name, "sha256": sha256(variance_path)},
            "component_table": {"path": components_path.name, "sha256": sha256(components_path)},
        },
        "limitations": [
            "V_obs is underivable for several targets and contributes zero, biasing toward over-exclusion.",
            "The 10% target-relative model-discrepancy term is a declared assumption, not an estimate.",
            *(["This draft binds an unsigned plan and must be regenerated after sign-off."] if args.draft else []),
            *(["This signed-plan package still requires explicit owner review and approval."] if status == "FINAL_REVIEW_REQUIRED" else []),
        ],
    }
    write_json(out / "acceptance_input_provenance.json", provenance)
    print(f"wrote {status} acceptance-input package: {out}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--wave-plan", default=str(DEFAULT_WAVE_PLAN))
    parser.add_argument("--out", required=True)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument(
        "--approve-for-production", action="store_true",
        help="mark a signed-plan package FINAL only after its component table was reviewed",
    )
    parser.add_argument(
        "--approved-by", default="",
        help="reviewer recorded when --approve-for-production is used",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(generate(parse_args()))
    except (nc.ContractError, nc.UnsignedConfigError, FileNotFoundError, KeyError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
