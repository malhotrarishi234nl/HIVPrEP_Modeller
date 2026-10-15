#!/usr/bin/env python3
"""Run the ordinary model with an explicit M0 or M1 diagnosis route.

This is the small operator-facing wrapper around ``SimulationEngineV3``.  It
does not calibrate, search, or choose parameters.  Collaborators can edit a
copy of the JSON config, validate the chosen route, and run it without editing
Python code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from config_reader import ConfigReader
from config_utils import extract_value
import stage_structured_diagnosis as ssd


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "configs" / (
    "V13.22_20260604_nearfit_rank001_basecase_onart_two_era_"
    "stochastic_m0_m1_20261015.json"
)


class OperatorConfigError(ValueError):
    """A plain-language problem in the collaborator-facing run request."""


PROTECTED_ROOTS = {
    "_readme", "meta", "run_control", "output", "v13_14_diagnostic",
    "calibration_metadata",
}


def load_shared_config(path: str | Path) -> dict:
    """Load and validate the protected shared-config fields without an engine."""
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text())
    except FileNotFoundError as exc:
        raise OperatorConfigError(f"config file does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise OperatorConfigError(
            f"config is not valid JSON ({config_path}, line {exc.lineno}): {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise OperatorConfigError("config must contain one JSON object")

    try:
        canonical = json.loads(DEFAULT_CONFIG.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise OperatorConfigError(
            f"canonical config is unavailable or invalid: {DEFAULT_CONFIG}"
        ) from exc
    forbidden = _first_forbidden_edit(payload, canonical)
    if forbidden is not None:
        raise OperatorConfigError(
            f"forbidden config edit at {_format_path(forbidden)}; collaborator "
            "copies may change only existing scientific parameter value fields. "
            f"Start from a fresh canonical copy: {DEFAULT_CONFIG}"
        )

    deterministic = payload.get("run_control", {}).get("deterministic_mode")
    deterministic = extract_value(deterministic)
    if deterministic is not False:
        raise OperatorConfigError(
            "run_control.deterministic_mode.value must be false in the shared "
            "stochastic config"
        )
    try:
        profile = ssd.validate_shared_base_config(payload)
        # Exercise the same V10-to-engine config translation used at runtime.
        translated = ConfigReader(payload).as_v6_dict()
        translated_profile = ssd.load_stage_structured_diagnosis(translated)
    except (ssd.StageStructuredDiagnosisConfigError, KeyError, TypeError, ValueError) as exc:
        raise OperatorConfigError(str(exc)) from exc
    if translated_profile != profile:
        raise OperatorConfigError(
            "ConfigReader changed the stage_structured_diagnosis block; the config "
            "is not safe to run"
        )
    return payload


def _first_forbidden_edit(
    candidate: Any, canonical: Any, path: tuple[str | int, ...] = (),
    *, in_editable_value: bool = False,
) -> tuple[str | int, ...] | None:
    """Return the first non-operator edit while preserving JSON schema.

    Existing ``value`` payloads outside protected roots and the M0/M1 routing
    block are operator-editable. Object keys, list lengths, scalar types and
    every non-``value`` field must remain canonical.
    """
    protected = _protected_path(path)
    editable = in_editable_value and not protected

    if isinstance(canonical, dict):
        if not isinstance(candidate, dict):
            return path
        for key in canonical:
            if key not in candidate:
                return path + (key,)
        for key in sorted(set(candidate).difference(canonical)):
            return path + (key,)
        for key, canonical_value in canonical.items():
            child_path = path + (key,)
            child_editable = editable or (
                key == "value" and not _protected_path(child_path)
            )
            found = _first_forbidden_edit(
                candidate[key], canonical_value, child_path,
                in_editable_value=child_editable,
            )
            if found is not None:
                return found
        return None

    if isinstance(canonical, list):
        if not isinstance(candidate, list) or len(candidate) != len(canonical):
            return path
        for index, canonical_value in enumerate(canonical):
            found = _first_forbidden_edit(
                candidate[index], canonical_value, path + (index,),
                in_editable_value=editable,
            )
            if found is not None:
                return found
        return None

    if candidate == canonical:
        return None
    if editable and _same_json_scalar_type(candidate, canonical):
        return None
    return path


def _protected_path(path: tuple[str | int, ...]) -> bool:
    if path and path[0] in PROTECTED_ROOTS:
        return True
    profile = ssd.PROFILE_PATH
    return len(path) >= len(profile) and tuple(path[:len(profile)]) == profile


def _same_json_scalar_type(candidate: Any, canonical: Any) -> bool:
    if isinstance(canonical, bool):
        return isinstance(candidate, bool)
    if isinstance(canonical, (int, float)):
        return isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
    if canonical is None:
        return candidate is None
    return type(candidate) is type(canonical)


def _format_path(path: tuple[str | int, ...]) -> str:
    if not path:
        return "<root>"
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + part
    return text


def config_for_model(
    base_config: Mapping[str, Any], *, model: str,
    aids_symptom_annual_probability: float | None,
    output_dir: str | Path | None = None,
) -> dict:
    """Create an explicit runtime config without mutating ``base_config``."""
    if model == "M0" and aids_symptom_annual_probability is not None:
        raise OperatorConfigError("--p is only valid with --model M1")
    if model == "M1" and aids_symptom_annual_probability is None:
        raise OperatorConfigError(
            "--model M1 requires --p AIDS_SYMPTOM_ANNUAL_PROBABILITY"
        )
    try:
        config = ssd.route_shared_config(
            base_config, model=model, p=aids_symptom_annual_probability
        )
    except ssd.StageStructuredDiagnosisConfigError as exc:
        raise OperatorConfigError(str(exc)) from exc

    if output_dir is not None:
        output = config["output"]
        _set_value(output, "enabled", True)
        _set_value(output, "directory", str(Path(output_dir)))
        _set_value(output, "terminal_summary", True)
    return config


def _set_value(block: dict, key: str, value: Any) -> None:
    if key not in block:
        raise OperatorConfigError(f"config is missing output.{key}")
    current = block[key]
    if isinstance(current, dict):
        if "value" not in current:
            raise OperatorConfigError(f"output.{key} must contain a value field")
        current["value"] = value
    else:
        block[key] = value


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(
        description="Validate or run one explicit M0/M1 model configuration."
    )
    out.add_argument("--config", default=str(DEFAULT_CONFIG), help="model config JSON")
    out.add_argument("--model", required=True, choices=("M0", "M1"))
    out.add_argument(
        "--p", type=float, default=None, metavar="PROBABILITY",
        help="AIDS-symptom annual probability; required for M1 and forbidden for M0",
    )
    out.add_argument("--seed", type=int, default=None, help="override the config RNG seed")
    out.add_argument(
        "--output-dir", default=None,
        help="directory for model outputs; required unless --validate-only is used",
    )
    out.add_argument(
        "--validate-only", action="store_true",
        help="validate the config and route without constructing the engine",
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base = load_shared_config(args.config)
        runtime = config_for_model(
            base, model=args.model,
            aids_symptom_annual_probability=args.p, output_dir=args.output_dir,
        )
        # Re-check the shaped config through ConfigReader.  M0 deliberately has
        # no optional block; M1 must remain enabled after translation.
        translated = ConfigReader(runtime).as_v6_dict()
        profile = ssd.load_stage_structured_diagnosis(translated)
        if args.model == "M0" and profile.enabled:
            raise OperatorConfigError("M0 route unexpectedly enabled M1 diagnosis")
        if args.model == "M1" and not profile.enabled:
            raise OperatorConfigError("M1 route did not enable stage-structured diagnosis")
        if args.validate_only:
            print(
                f"VALID: {args.model} config route; stochastic=true; "
                "engine was not constructed"
            )
            return 0
        if args.output_dir is None:
            raise OperatorConfigError(
                "--output-dir is required for a run so results are not silently discarded"
            )
        output_path = Path(args.output_dir)
        if output_path.exists() and not output_path.is_dir():
            raise OperatorConfigError(f"output path is not a directory: {output_path}")
        if output_path.is_dir() and any(output_path.iterdir()):
            raise OperatorConfigError(
                f"output directory is not empty: {output_path}; choose a new run directory"
            )
    except OperatorConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    from run_v3 import SimulationEngineV3  # engine import only after validation

    engine = SimulationEngineV3(runtime, seed=args.seed)
    engine.run(run_number=1)
    print(f"COMPLETE: {args.model} results written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
