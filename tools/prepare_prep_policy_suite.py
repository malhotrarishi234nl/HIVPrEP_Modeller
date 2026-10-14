"""Materialize and validate the PrEP policy-suite runtime configuration.

This command runs no model trajectories.  It turns the reviewable overlay into
the ordinary runtime JSON consumed by the existing HTA configuration loader and
writes a compact plan that records scenario names, assumptions, and budgets.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = pointer.lstrip("/").split("/")
    node = document
    for raw in parts[:-1]:
        key = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    leaf = parts[-1].replace("~1", "/").replace("~0", "~")
    return node, leaf


def apply_patches(document: dict, patches: Sequence[dict]) -> dict:
    result = copy.deepcopy(document)
    for patch in patches:
        if patch.get("op") != "replace":
            raise ValueError(f"unsupported patch operation: {patch!r}")
        parent, leaf = _pointer_parent(result, patch["path"])
        if isinstance(parent, list):
            parent[int(leaf)] = copy.deepcopy(patch["value"])
        else:
            if leaf not in parent:
                raise KeyError(f"replace path does not exist: {patch['path']}")
            parent[leaf] = copy.deepcopy(patch["value"])
    return result


def materialize(overlay_path: Path) -> tuple[dict, dict]:
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    base_path = ROOT / overlay["base_runtime_config"]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    runtime = apply_patches(base, overlay.get("patches", []))
    for key, value in overlay.get("replace_top_level", {}).items():
        runtime[key] = copy.deepcopy(value)
    runtime["_suite_provenance"] = {
        "suite_id": overlay["suite_id"],
        "base_runtime_config": overlay["base_runtime_config"],
        "base_runtime_config_sha256": sha256_file(base_path),
        "overlay": str(overlay_path.relative_to(ROOT)),
        "overlay_sha256": sha256_file(overlay_path),
    }
    return overlay, runtime


def build_bridge_runtime(runtime: dict) -> dict:
    """Return the five-scenario 2010 bridge as an ordinary runtime config."""
    bridge = copy.deepcopy(runtime)
    source = runtime["scenarios"]
    scenarios = {
        "scenario_oral_matched_add_1500": copy.deepcopy(
            source["scenario_oral_matched_add_1500"]
        ),
        "scenario_oral_wang_28000": copy.deepcopy(source["scenario_oral_wang_28000"]),
    }

    substitution = copy.deepcopy(source["scenario_lena_substitution"])
    substitution["primary_comparison"] = True
    substitution["label"] = "Lenacapavir 50% substitution within 13,000 total PrEP stock"
    substitution["intervention_arm"]["interventions"][1]["pathway"] = (
        "lena_substitution_50"
    )
    scenarios["scenario_lena_substitution__switch_50"] = substitution

    add_on = copy.deepcopy(source["scenario_lena_add_on"])
    add_on["primary_comparison"] = False
    add_on["label"] = "Lenacapavir +1,500 add-on above 13,000 oral PrEP users"
    add_on["intervention_arm"]["interventions"][1]["pathway"] = "lena_add_on_1500"
    scenarios["scenario_lena_add_on__add_on_1500"] = add_on

    for scenario in scenarios.values():
        if scenario is not substitution:
            scenario["primary_comparison"] = False
    bridge["scenarios"] = scenarios
    bridge["_suite_provenance"]["scope"] = "2010_bridge"
    return bridge


def validate(overlay: dict, runtime: dict, bridge_runtime: dict | None = None) -> dict:
    from hta_config_loader import compose_split_hta_config

    # compose_split_hta_config requires a real path for resolving the costing file.
    base_path = ROOT / overlay["base_runtime_config"]
    composed = compose_split_hta_config(runtime, str(base_path))
    scenarios = composed["analysis_scenarios"]
    names = [scenario["name"] for scenario in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("expanded scenario names are not unique")
    expected_count = overlay["analysis_plan"]["trajectory_budget"]["primary_2008"]["scenarios"]
    if len(names) != expected_count:
        raise ValueError(f"expected {expected_count} expanded scenarios, found {len(names)}")

    bridge = overlay["analysis_plan"]["bridge_scenarios"]
    missing_bridge = sorted(set(bridge) - set(names))
    if missing_bridge:
        raise ValueError(f"bridge scenarios are missing: {missing_bridge}")

    if bridge_runtime is None:
        bridge_runtime = build_bridge_runtime(runtime)
    bridge_composed = compose_split_hta_config(bridge_runtime, str(base_path))
    bridge_names = [item["name"] for item in bridge_composed["analysis_scenarios"]]
    if bridge_names != bridge:
        raise ValueError(
            "materialized bridge scenario order/content differs from the reviewed "
            f"plan: expected {bridge!r}, found {bridge_names!r}"
        )

    persistence_rows = []
    for scenario in scenarios:
        metadata = scenario.get("analysis_metadata", {})
        annual = metadata.get("annual_lena_discontinuation")
        if annual is None:
            continue
        configured = (
            scenario.get("prep_schedule_overrides", {})
            .get("oral_prep_plus_lena", {})
            .get("Lenacapavir", {})
            .get("discontinuation", {})
            .get("2025")
        )
        if configured != annual:
            raise ValueError(
                f"{scenario['name']} metadata/config discontinuation mismatch: "
                f"{annual!r} versus {configured!r}"
            )
        persistence_rows.append(
            {
                "scenario": scenario["name"],
                "annual_probability": annual,
                "quarterly_probability": 1.0 - (1.0 - float(annual)) ** 0.25,
            }
        )

    budget = overlay["analysis_plan"]["trajectory_budget"]
    primary = budget["primary_2008"]
    bridge_budget = budget["bridge_2010"]
    computed_primary = primary["seeds"] * (
        primary["forward_arms_per_seed"] + primary["historical_prefreeze_per_seed"]
    )
    computed_bridge = bridge_budget["seeds"] * (
        bridge_budget["forward_arms_per_seed"]
        + bridge_budget["historical_prefreeze_per_seed"]
    )
    if computed_primary != primary["maximum_trajectories"]:
        raise ValueError("primary trajectory budget is inconsistent")
    if computed_bridge != bridge_budget["maximum_trajectories"]:
        raise ValueError("bridge trajectory budget is inconsistent")
    if computed_primary + computed_bridge != budget["maximum_total"]:
        raise ValueError("total trajectory budget is inconsistent")

    return {
        "suite_id": overlay["suite_id"],
        "status": overlay["status"],
        "launch_ready": bool(overlay["launch"]["ready"]),
        "launch_blockers": overlay["launch"]["blockers"],
        "expanded_scenario_count": len(names),
        "expanded_scenarios": names,
        "bridge_scenarios": bridge,
        "persistence_scenarios": persistence_rows,
        "trajectory_budget": budget,
        "report_profile": overlay["report_profile"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlay",
        type=Path,
        default=ROOT / "configs/hta_prep_policy_suite_20260929.overlay.json",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    overlay_path = args.overlay.resolve()
    overlay, runtime = materialize(overlay_path)
    bridge_runtime = build_bridge_runtime(runtime)
    plan = validate(overlay, runtime, bridge_runtime)
    print(json.dumps(plan, indent=2))
    if args.validate_only:
        return 0
    if args.output_dir is None:
        raise ValueError("--output-dir is required unless --validate-only is used")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runtime_path = output_dir / "hta_runtime_primary_2008.json"
    bridge_path = output_dir / "hta_runtime_bridge_2010.json"
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    bridge_path.write_text(
        json.dumps(bridge_runtime, indent=2) + "\n", encoding="utf-8"
    )
    plan["runtime_configs"] = {
        "primary_2008": {
            "file": runtime_path.name,
            "sha256": sha256_file(runtime_path),
        },
        "bridge_2010": {
            "file": bridge_path.name,
            "sha256": sha256_file(bridge_path),
        },
    }
    plan["overlay_sha256"] = sha256_file(overlay_path)
    (output_dir / "plan.json").write_text(
        json.dumps(plan, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
