"""HTA config loading helpers.

Supports both the legacy monolithic ``configs/hta_config.json`` shape and the
split runtime/costing HTA config shape. The split loader composes a v2-compatible
dict for existing economics/reporting modules so economic parameter values are
not transformed during the first migration step.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Iterable, List, Tuple


ECONOMIC_TOP_LEVEL_KEYS = {
    "_format_version",
    "_notes",
    "discounting",
    "wtp_threshold_eur_per_qaly",
    "perspective",
    "utilities",
    "utility_decrements",
    "adverse_events",
    "prevention_costs",
    "disease_costs",
    "_costing_update_note",
}


def load_hta_config(config_path: str) -> Dict[str, Any]:
    """Load a monolithic or split HTA config as a v2-compatible dict."""
    config_path = os.path.abspath(config_path)
    with open(config_path, "r") as f:
        raw = json.load(f)

    if _is_runtime_config(raw):
        return compose_split_hta_config(raw, config_path)

    return raw


def compose_split_hta_config(
    runtime_config: Dict[str, Any],
    runtime_config_path: str,
) -> Dict[str, Any]:
    """Compose split runtime + costing configs into the existing v2 shape."""
    costing_path = _resolve_costing_path(runtime_config, runtime_config_path)
    with open(costing_path, "r") as f:
        costing_config = json.load(f)

    merged = copy.deepcopy(costing_config)
    study_defaults = runtime_config.get("study_defaults", {})

    merged["time_horizon"] = {
        "freeze_year": study_defaults["freeze_year"],
        "evaluation_years": study_defaults["evaluation_years"],
        "prep_reset_at_fork": {
            "value": False,
            "notes": (
                "Split runtime config uses per-arm prep_stock_handling. "
                "Default is carry-over; no-PrEP attribution arms reset explicitly."
            ),
        },
    }

    # Keep discounting base year anchored to the freeze year unless the costing
    # config already carries the same value.
    discounting = merged.setdefault("discounting", {})
    discounting.setdefault("base_year", {"value": study_defaults["freeze_year"]})

    outputs = copy.deepcopy(runtime_config.get("outputs", {}))
    if "csv_files" not in outputs:
        csv_outputs = outputs.get("csv_outputs", {})
        outputs["csv_files"] = {
            "enabled": any(bool(v) for v in csv_outputs.values())
            if csv_outputs else True
        }
    merged["outputs"] = outputs

    merged["analysis_scenarios"] = _runtime_scenarios_to_legacy(runtime_config)
    merged["_runtime_config"] = copy.deepcopy(runtime_config)
    merged["_runtime_config_path"] = runtime_config_path
    merged["_costing_config_path"] = costing_path
    return merged


def economic_subset(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return only top-level economic blocks for parity checks."""
    return {
        key: copy.deepcopy(config[key])
        for key in ECONOMIC_TOP_LEVEL_KEYS
        if key in config
    }


def _is_runtime_config(config: Dict[str, Any]) -> bool:
    return (
        isinstance(config, dict)
        and "study_defaults" in config
        and "economic_inputs_file" in config.get("study_defaults", {})
    )


def _resolve_costing_path(
    runtime_config: Dict[str, Any],
    runtime_config_path: str,
) -> str:
    rel = runtime_config["study_defaults"]["economic_inputs_file"]
    if os.path.isabs(rel):
        return rel

    candidates = [
        os.path.join(os.path.dirname(runtime_config_path), rel),
        os.path.join(os.getcwd(), rel),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"Could not resolve economic_inputs_file '{rel}' from {runtime_config_path}"
    )


def _runtime_scenarios_to_legacy(runtime_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    scenarios: List[Dict[str, Any]] = []
    base_cases = runtime_config.get("base_cases", {})

    if (
        isinstance(base_cases.get("no_prep_counterfactual"), dict)
        and isinstance(base_cases.get("oral_prep_standard_of_care"), dict)
        and base_cases["no_prep_counterfactual"].get("include_in_report", True)
    ):
        scenarios.append({
            "name": "base_case_context",
            "label": "Base case context: no PrEP vs oral PrEP SOC",
            "comparison_type": "attribution",
            "primary_comparison": False,
            "arm_order": ["no_prep", "oral_prep"],
            "arm_prep_reset": {"no_prep": True, "oral_prep": False},
            "arm_pathways": {
                "oral_prep": _pathways_for_base_case(
                    runtime_config, "oral_prep_standard_of_care"
                )
            },
            "reporting": {
                "include_in_decision_summary": False,
                "include_in_epidemiology": True,
                "include_price_sensitivity": False,
            },
        })

    for scenario_id, scenario in runtime_config.get("scenarios", {}).items():
        scenarios.extend(_expand_runtime_scenario(runtime_config, scenario_id, scenario))

    _validate_primary_comparison_count(runtime_config)
    return scenarios


def _expand_runtime_scenario(
    runtime_config: Dict[str, Any],
    scenario_id: str,
    scenario: Dict[str, Any],
) -> List[Dict[str, Any]]:
    comparator_base = scenario["comparator_base_case"]
    comparator_arm = _base_case_to_arm(comparator_base)
    intervention = scenario["intervention_arm"]
    intervention_arm = _intervention_to_arm(scenario["comparison_type"], intervention)

    base_pathways = {
        comparator_arm: _pathways_for_base_case(runtime_config, comparator_base),
        intervention_arm: _resolve_intervention_pathways(runtime_config, intervention),
    }

    variants = _expand_arm_pathway_variants(base_pathways)
    expanded: List[Dict[str, Any]] = []
    for suffix, arm_pathways, variant_label in variants:
        name = scenario_id if not suffix else f"{scenario_id}__{suffix}"
        label = scenario["label"] if not variant_label else f"{scenario['label']} ({variant_label})"
        legacy = {
            "name": name,
            "label": label,
            "comparison_type": scenario.get("comparison_type"),
            "primary_comparison": bool(scenario.get("primary_comparison", False)),
            "arm_order": [comparator_arm, intervention_arm],
            "arm_prep_reset": {
                comparator_arm: False,
                intervention_arm: False,
            },
            "arm_pathways": arm_pathways,
            "comparator_arm": comparator_arm,
            "intervention_arm": intervention_arm,
            "reporting": copy.deepcopy(scenario.get("reporting", {})),
        }
        if "price_sensitivity" in scenario:
            legacy["price_sensitivity"] = copy.deepcopy(scenario["price_sensitivity"])
        if "benchmark_references" in scenario:
            legacy["benchmark_references"] = copy.deepcopy(scenario["benchmark_references"])
        expanded.append(legacy)
    return expanded


def _validate_primary_comparison_count(
    runtime_config: Dict[str, Any],
) -> None:
    selection = runtime_config.get("comparison_selection", {})
    rule = selection.get("primary_comparison_rule", "exactly_one_required")
    if rule != "exactly_one_required":
        return

    primaries = []
    for scenario_id, scenario in runtime_config.get("scenarios", {}).items():
        if (
            scenario.get("primary_comparison")
            and scenario.get("reporting", {}).get("include_in_decision_summary", True)
        ):
            primaries.append(scenario_id)
    if len(primaries) != 1:
        raise ValueError(
            "Runtime HTA config requires exactly one primary comparison; "
            f"found {len(primaries)}: {primaries}"
        )


def _pathways_for_base_case(
    runtime_config: Dict[str, Any],
    base_case_id: str,
) -> List[Dict[str, Any]]:
    base_case = runtime_config.get("base_cases", {}).get(base_case_id, {})
    return _resolve_interventions(runtime_config, base_case.get("interventions", []))


def _resolve_intervention_pathways(
    runtime_config: Dict[str, Any],
    intervention: Dict[str, Any],
) -> List[Dict[str, Any]]:
    return _resolve_interventions(runtime_config, intervention.get("interventions", []))


def _resolve_interventions(
    runtime_config: Dict[str, Any],
    interventions: Iterable[Dict[str, str]],
) -> List[Dict[str, Any]]:
    pathways = runtime_config.get("prep_target_pathways", {})
    resolved = []
    for item in interventions:
        pathway_id = item["pathway"]
        pathway = copy.deepcopy(pathways[pathway_id])
        pathway["_pathway_id"] = pathway_id
        resolved.append(pathway)
    return resolved


def _base_case_to_arm(base_case_id: str) -> str:
    mapping = {
        "no_prep_counterfactual": "no_prep",
        "oral_prep_standard_of_care": "oral_prep",
    }
    if base_case_id not in mapping:
        raise ValueError(f"Unsupported comparator_base_case: {base_case_id}")
    return mapping[base_case_id]


def _intervention_to_arm(comparison_type: str, intervention: Dict[str, Any]) -> str:
    explicit = intervention.get("engine_arm")
    if explicit:
        return explicit
    if comparison_type in {"substitution", "add_on"}:
        return "oral_prep_plus_lena"
    if comparison_type == "oral_expansion_benchmark":
        return "oral_prep_expansion"
    return intervention.get("id", "intervention")


def _expand_arm_pathway_variants(
    arm_pathways: Dict[str, List[Dict[str, Any]]]
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]], str]]:
    variants: List[Tuple[str, Dict[str, List[Dict[str, Any]]], str]] = [
        ("", copy.deepcopy(arm_pathways), "")
    ]

    for arm_name, pathways in arm_pathways.items():
        for p_idx, pathway in enumerate(pathways):
            mode = pathway.get("mode")
            if mode == "switch_share":
                values = pathway.get("switch_share_of_eligible_oral_users", {}).get("value")
                if isinstance(values, list):
                    variants = _explode_variants(
                        variants, arm_name, p_idx,
                        lambda p, value: _set_switch_share_variant(p, value),
                        values,
                        lambda value: f"switch_{int(round(float(value) * 100))}",
                        lambda value: f"{float(value):.0%} switch",
                    )
            elif mode == "additional_target_stock":
                values = pathway.get("additional_target_stock", {}).get("value")
                if isinstance(values, list):
                    variants = _explode_variants(
                        variants, arm_name, p_idx,
                        lambda p, value: _set_additional_target_variant(p, value),
                        values,
                        lambda value: value["label"],
                        lambda value: value["label"].replace("_", " "),
                    )
    return variants


def _explode_variants(
    variants: List[Tuple[str, Dict[str, List[Dict[str, Any]]], str]],
    arm_name: str,
    pathway_index: int,
    setter,
    values: Iterable[Any],
    suffix_fn,
    label_fn,
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]], str]]:
    expanded = []
    for base_suffix, base_pathways, base_label in variants:
        for value in values:
            new_pathways = copy.deepcopy(base_pathways)
            setter(new_pathways[arm_name][pathway_index], value)
            suffix = suffix_fn(value)
            label = label_fn(value)
            combined_suffix = suffix if not base_suffix else f"{base_suffix}__{suffix}"
            combined_label = label if not base_label else f"{base_label}; {label}"
            expanded.append((combined_suffix, new_pathways, combined_label))
    return expanded


def _set_switch_share_variant(pathway: Dict[str, Any], value: float) -> None:
    pathway["switch_share_of_eligible_oral_users"]["value"] = float(value)


def _set_additional_target_variant(pathway: Dict[str, Any], value: Dict[str, Any]) -> None:
    pathway["additional_target_stock"]["value"] = copy.deepcopy(value["target_stock"])
    pathway["_variant_label"] = value["label"]
