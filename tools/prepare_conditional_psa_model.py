#!/usr/bin/env python3
"""Freeze calibrated coordinates for a conditional parameter PSA.

The calibration shortlist is not a posterior.  A PSA conditional on one
selected calibrated backbone must therefore keep every calibrated coordinate
fixed while sampling the remaining Tier-3 parameters.  This adapter changes
only tier/distribution metadata in a copy; numeric model values are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_utils import validate_tier_schema


class ConditionalPSAError(ValueError):
    """The requested conditional-PSA backbone cannot be built safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for name, index in re.findall(r"([^.[\]]+)|\[(\d+)\]", path):
        tokens.append(name if name else int(index))
    return tokens


def _parameter_entry(model: dict, config_path: str) -> tuple[dict, str] | None:
    current: object = model
    tier_entries: list[tuple[dict, str]] = []
    walked: list[str] = []
    for token in _tokens(config_path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                raise ConditionalPSAError(f"cannot resolve calibration path {config_path}")
            current = current[token]
            walked.append(f"[{token}]")
        else:
            if not isinstance(current, dict) or token not in current:
                raise ConditionalPSAError(f"cannot resolve calibration path {config_path}")
            current = current[token]
            walked.append(token)
        if isinstance(current, dict) and "tier" in current:
            tier_entries.append((current, ".".join(walked).replace(".[", "[")))
    if not tier_entries:
        # Plain numeric calibration coordinates are not discoverable by the
        # tier-based PSA sampler, so they are already fixed for PSA purposes.
        return None
    return tier_entries[-1]


def prepare(model_path: Path, calibration_space_path: Path, output_path: Path) -> dict:
    model_path = model_path.resolve()
    calibration_space_path = calibration_space_path.resolve()
    output_path = output_path.resolve()
    model = json.loads(model_path.read_text(encoding="utf-8"))
    space = json.loads(calibration_space_path.read_text(encoding="utf-8"))
    source_binding = model.get("_calibration_binding")
    if not isinstance(source_binding, dict):
        raise ConditionalPSAError("model lacks _calibration_binding provenance")

    fixed_entries: dict[int, str] = {}
    parameter_names = []
    already_fixed_unannotated = []
    for parameter in space.get("parameters", []):
        resolved = _parameter_entry(model, parameter["config_path"])
        if resolved is None:
            already_fixed_unannotated.append(str(parameter["name"]))
            parameter_names.append(str(parameter["name"]))
            continue
        entry, entry_path = resolved
        fixed_entries[id(entry)] = entry_path
        parameter_names.append(str(parameter["name"]))
        entry["tier"] = "fixed"
        entry.pop("bounds", None)
        entry.pop("distribution", None)
        entry.pop("calibration_target", None)

    violations = validate_tier_schema(model)
    if violations:
        raise ConditionalPSAError(
            "conditional PSA model still violates tier schema:\n"
            + "\n".join(f"- {item}" for item in violations)
        )
    model["_conditional_psa_binding"] = {
        "interpretation": (
            "Parameter PSA conditional on one selected point-calibrated backbone; "
            "calibrated coordinates are fixed and are not treated as independent "
            "posterior draws."
        ),
        "source_model": str(model_path),
        "source_model_sha256": _sha256(model_path),
        "calibration_space": str(calibration_space_path),
        "calibration_space_sha256": _sha256(calibration_space_path),
        "calibrated_parameter_names": parameter_names,
        "fixed_tier_entries": sorted(fixed_entries.values()),
        "already_fixed_unannotated": already_fixed_unannotated,
        "numeric_values_changed": 0,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise ConditionalPSAError(f"refusing to overwrite existing file: {output_path}")
    output_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "CONDITIONAL_PSA_MODEL_PREPARED",
        "output": str(output_path),
        "sha256": _sha256(output_path),
        "calibrated_parameters": len(parameter_names),
        "fixed_tier_entries": len(fixed_entries),
        "numeric_values_changed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--calibration-space", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.model, args.calibration_space, args.output), indent=2))
        return 0
    except (OSError, json.JSONDecodeError, ConditionalPSAError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
