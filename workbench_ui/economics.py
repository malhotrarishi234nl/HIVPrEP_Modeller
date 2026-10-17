"""Typed local adapter for the repository's existing HTA scenario runner."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping

import pandas as pd

from config_utils import validate_hta_config
from hta_config_loader import compose_split_hta_config
from model_workbench.workers import (
    ALL_AVAILABLE_WORKERS,
    MAX_CONFIGURED_WORKERS,
    effective_worker_count,
    logical_cpu_count,
)


DEFAULT_RUNTIME_CONFIG = "configs/hta_runtime_config.json"
DEFAULT_COSTING_CONFIG = "configs/hta_costing_config.json"
DEFAULT_POLICY_OVERLAY = "configs/hta_prep_policy_suite_20260929.overlay.json"
ECONOMIC_MANIFEST = "economic_manifest.json"


class EconomicWorkbenchError(RuntimeError):
    pass


@dataclass(frozen=True)
class EconomicDraft:
    name: str
    base_config: str
    runtime_config: str = DEFAULT_RUNTIME_CONFIG
    costing_config: str = DEFAULT_COSTING_CONFIG
    policy_overlay: str = DEFAULT_POLICY_OVERLAY
    seeds: tuple[int, ...] = (42001,)
    model_overrides: Mapping[str, float] = field(default_factory=dict)
    costing_overrides: Mapping[str, float] = field(default_factory=dict)
    scenario_ids: tuple[str, ...] | None = None
    primary_scenario_family: str = "scenario_lena_substitution"
    include_patient_time: bool = True
    include_productivity_loss: bool = False
    evaluation_years: int = 40
    workers: int = 1


@dataclass(frozen=True)
class EconomicControl:
    path: str
    label: str
    group: str
    value: float
    minimum: float
    maximum: float
    unit: str
    help: str


@dataclass(frozen=True)
class EconomicResult:
    root: Path
    manifest: dict[str, Any]
    arms: pd.DataFrame
    comparisons: pd.DataFrame
    budget: pd.DataFrame


@dataclass(frozen=True)
class PolicyScenario:
    id: str
    source_id: str
    group: str
    label: str
    comparator: str
    intervention: str
    setting: str
    default_primary: bool


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EconomicWorkbenchError(f"cannot read configuration: {path}") from exc
    if not isinstance(value, dict):
        raise EconomicWorkbenchError(f"configuration must contain an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unwrap(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _path_parts(path: str) -> list[str]:
    parts = [part for part in path.split(".") if part]
    if not parts:
        raise EconomicWorkbenchError("empty configuration path")
    return parts


def _path_get(payload: Any, path: str) -> Any:
    current = payload
    for part in _path_parts(path):
        if isinstance(current, list):
            current = next((item for item in current if str(item.get("id")) == part), None)
            if current is None:
                raise EconomicWorkbenchError(f"configuration is missing {path}")
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise EconomicWorkbenchError(f"configuration is missing {path}")
    return current


def _path_set(payload: Any, path: str, value: Any) -> None:
    parts = _path_parts(path)
    current = payload
    for part in parts[:-1]:
        if isinstance(current, list):
            current = next((item for item in current if str(item.get("id")) == part), None)
            if current is None:
                raise EconomicWorkbenchError(f"configuration is missing {path}")
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise EconomicWorkbenchError(f"configuration is missing {path}")
    final = parts[-1]
    if not isinstance(current, dict) or final not in current:
        raise EconomicWorkbenchError(f"configuration is missing {path}")
    current[final] = value


def _label(entry: Mapping[str, Any], fallback: str) -> str:
    return str(entry.get("label") or fallback.replace("_", " ").title())


def _materialize_policy_runtime(root: Path, overlay_path: str) -> dict[str, Any]:
    """Materialize the reviewed policy overlay without writing or running anything."""
    overlay_file = root / overlay_path
    overlay = _read_json(overlay_file)
    base_relative = str(overlay.get("base_runtime_config", DEFAULT_RUNTIME_CONFIG))
    runtime = _read_json(root / base_relative)
    for patch in overlay.get("patches", []):
        if patch.get("op") != "replace":
            raise EconomicWorkbenchError(f"unsupported policy overlay operation: {patch!r}")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in str(patch["path"]).lstrip("/").split("/")]
        current: Any = runtime
        for part in parts[:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        leaf = parts[-1]
        if isinstance(current, list):
            current[int(leaf)] = copy.deepcopy(patch["value"])
        else:
            if leaf not in current:
                raise EconomicWorkbenchError(f"policy overlay path does not exist: {patch['path']}")
            current[leaf] = copy.deepcopy(patch["value"])
    for key, value in overlay.get("replace_top_level", {}).items():
        runtime[key] = copy.deepcopy(value)
    runtime["_workbench_policy_source"] = {
        "overlay": overlay_path,
        "suite_id": overlay.get("suite_id"),
    }
    return runtime


def _scenario_source_id(expanded_id: str) -> str:
    return expanded_id.split("__", 1)[0]


def _scenario_group(scenario: Mapping[str, Any]) -> str:
    if scenario.get("name") == "base_case_context":
        return "Reference"
    family = str(scenario.get("analysis_metadata", {}).get("family", ""))
    if family == "oral_matched_expansion":
        return "Oral PrEP expansion"
    if family == "oral_benchmark":
        return "Oral PrEP benchmark"
    if family == "lena_substitution":
        return "Lenacapavir substitution"
    if family == "lena_add_on":
        return "Lenacapavir add-on"
    if family == "lena_persistence_sensitivity":
        return "Lenacapavir persistence"
    return str(scenario.get("comparison_type", "Policy")).replace("_", " ").title()


def _scenario_setting(scenario: Mapping[str, Any]) -> str:
    scenario_id = str(scenario.get("name", ""))
    if scenario_id == "base_case_context":
        return "No PrEP context versus oral PrEP held at 13,000"
    if "__switch_" in scenario_id:
        return f"{scenario_id.rsplit('__switch_', 1)[1]}% of eligible oral users switch"
    if "__add_on_" in scenario_id:
        return f"+{int(scenario_id.rsplit('__add_on_', 1)[1]):,} users"
    metadata = scenario.get("analysis_metadata", {})
    coverage = metadata.get("coverage_change_by_2030")
    if coverage is not None:
        return f"{int(coverage):+,} users by 2030"
    persistence = metadata.get("annual_lena_discontinuation")
    if persistence is not None:
        return f"{float(persistence):.0%} annual discontinuation"
    return str(scenario.get("comparison_type", "Policy comparison")).replace("_", " ")


def policy_scenario_matrix(
    root: Path | str,
    overlay_path: str = DEFAULT_POLICY_OVERLAY,
) -> list[PolicyScenario]:
    root = Path(root).resolve()
    runtime = _materialize_policy_runtime(root, overlay_path)
    composed = compose_split_hta_config(runtime, str(root / DEFAULT_RUNTIME_CONFIG))
    rows: list[PolicyScenario] = []
    for scenario in composed.get("analysis_scenarios", []):
        scenario_id = str(scenario["name"])
        rows.append(PolicyScenario(
            id=scenario_id,
            source_id=_scenario_source_id(scenario_id),
            group=_scenario_group(scenario),
            label=str(scenario.get("label", scenario_id)),
            comparator=str(scenario.get("comparator_arm", "")),
            intervention=str(scenario.get("intervention_arm", "")),
            setting=_scenario_setting(scenario),
            default_primary=bool(scenario.get("primary_comparison", False)),
        ))
    return rows


def cost_scope_label(include_patient_time: bool, include_productivity_loss: bool) -> str:
    parts = ["Healthcare payer"]
    if include_patient_time:
        parts.append("patient time")
    if include_productivity_loss:
        parts.append("productivity loss")
    return " + ".join(parts)


def _selected_policy_runtime(root: Path, draft: EconomicDraft) -> dict[str, Any]:
    runtime = _materialize_policy_runtime(root, draft.policy_overlay)
    runtime["study_defaults"]["evaluation_years"] = int(draft.evaluation_years)
    matrix = policy_scenario_matrix(root, draft.policy_overlay)
    known = {row.id: row for row in matrix}
    selected = set(known) if draft.scenario_ids is None else set(draft.scenario_ids)
    unknown = selected - set(known)
    if unknown:
        raise EconomicWorkbenchError(f"unknown policy scenarios: {sorted(unknown)}")
    if not selected:
        raise EconomicWorkbenchError("select at least one policy or reference comparison")

    include_context = "base_case_context" in selected
    runtime["base_cases"]["no_prep_counterfactual"]["include_in_report"] = include_context
    selected_sources = {
        known[scenario_id].source_id
        for scenario_id in selected
        if scenario_id != "base_case_context"
    }
    runtime["scenarios"] = {
        key: copy.deepcopy(value)
        for key, value in runtime.get("scenarios", {}).items()
        if key in selected_sources
    }

    switch_ids = {
        int(scenario_id.rsplit("__switch_", 1)[1])
        for scenario_id in selected
        if "__switch_" in scenario_id
    }
    if "scenario_lena_substitution" in selected_sources:
        pathway = runtime["prep_target_pathways"]["lena_substitution_grid"]
        values = pathway["switch_share_of_eligible_oral_users"]["value"]
        pathway["switch_share_of_eligible_oral_users"]["value"] = [
            value for value in values if int(round(float(value) * 100)) in switch_ids
        ]

    add_on_ids = {
        f"add_on_{scenario_id.rsplit('__add_on_', 1)[1]}"
        for scenario_id in selected
        if "__add_on_" in scenario_id
    }
    if "scenario_lena_add_on" in selected_sources:
        pathway = runtime["prep_target_pathways"]["lena_add_on_grid"]
        values = pathway["additional_target_stock"]["value"]
        pathway["additional_target_stock"]["value"] = [
            value for value in values if str(value.get("label")) in add_on_ids
        ]

    if selected_sources:
        primary = draft.primary_scenario_family
        if primary not in selected_sources:
            raise EconomicWorkbenchError("the summary comparison family must be included in the matrix")
        for scenario_id, scenario in runtime["scenarios"].items():
            scenario["primary_comparison"] = scenario_id == primary
    else:
        runtime.setdefault("comparison_selection", {})["primary_comparison_rule"] = "none_required"

    # Assert that selection survived grid expansion exactly as shown in the UI.
    composed = compose_split_hta_config(runtime, str(root / DEFAULT_RUNTIME_CONFIG))
    expanded = {str(item["name"]) for item in composed.get("analysis_scenarios", [])}
    if expanded != selected:
        missing = sorted(selected - expanded)
        extra = sorted(expanded - selected)
        raise EconomicWorkbenchError(
            f"policy matrix selection did not materialize exactly (missing={missing}, extra={extra})"
        )
    return runtime


def economic_controls(root: Path | str, costing_path: str = DEFAULT_COSTING_CONFIG) -> list[EconomicControl]:
    root = Path(root).resolve()
    costing = _read_json(root / costing_path)
    controls: list[EconomicControl] = []

    def add(path: str, label: str, group: str, unit: str, minimum: float, maximum: float, help_text: str = "") -> None:
        value = float(_path_get(costing, path))
        controls.append(EconomicControl(path, label, group, value, minimum, maximum, unit, help_text))

    add("wtp_threshold_eur_per_qaly.value", "Willingness-to-pay threshold", "Analysis settings", "EUR per QALY", 0, 1_000_000)
    add("discounting.cost_rate.value", "Annual cost discount rate", "Analysis settings", "proportion", 0, 0.20)
    add("discounting.health_effects_rate.value", "Annual health-effect discount rate", "Analysis settings", "proportion", 0, 0.20)

    add(
        "perspective.patient_time_costs.oral_prep_visits_per_year.value",
        "Oral PrEP visits per year", "Patient time and productivity", "visits", 0, 52,
        str(costing["perspective"]["patient_time_costs"]["oral_prep_visits_per_year"].get("notes", "")),
    )
    add(
        "perspective.patient_time_costs.lenacapavir_visits_per_year.value",
        "Lenacapavir visits per year", "Patient time and productivity", "visits", 0, 52,
        str(costing["perspective"]["patient_time_costs"]["lenacapavir_visits_per_year"].get("notes", "")),
    )
    add(
        "perspective.patient_time_costs.hiv_art_visits_per_year.value",
        "HIV treatment visits per year", "Patient time and productivity", "visits", 0, 52,
        str(costing["perspective"]["patient_time_costs"]["hiv_art_visits_per_year"].get("notes", "")),
    )
    add(
        "perspective.patient_time_costs.time_per_visit_hours.value",
        "Travel and appointment time per visit", "Patient time and productivity", "hours", 0, 24,
        str(costing["perspective"]["patient_time_costs"]["time_per_visit_hours"].get("notes", "")),
    )
    productivity = costing["perspective"]["productivity_loss"]
    add(
        "perspective.productivity_loss.friction_period_weeks.value",
        "Friction period", "Patient time and productivity", "weeks", 0, 104,
        str(productivity["friction_period_weeks"].get("notes", "")),
    )
    add(
        "perspective.productivity_loss.average_hourly_wage.value",
        "Hourly value of time", "Patient time and productivity", "EUR per hour", 0, 500,
        str(productivity["average_hourly_wage"].get("notes", "")),
    )
    add(
        "perspective.productivity_loss.employment_rate_modifier.value",
        "Employment-rate modifier", "Patient time and productivity", "proportion", 0, 1,
        str(productivity["employment_rate_modifier"].get("notes", "")),
    )
    for stage, entry in productivity.get("loss_rate_by_stage", {}).items():
        if not isinstance(entry, dict) or "value" not in entry:
            continue
        add(
            f"perspective.productivity_loss.loss_rate_by_stage.{stage}.value",
            f"Productivity loss rate: {stage.replace('_', ' ')}",
            "Patient time and productivity", "proportion", 0, 1,
            str(entry.get("notes", "")),
        )

    for item in costing.get("prevention_costs", {}).get("shared", []):
        item_id = str(item.get("id"))
        add(
            f"prevention_costs.shared.{item_id}.value",
            _label(item, item_id), "Prevention costs", "EUR", 0, 1_000_000,
            str(item.get("notes", "")),
        )
    for arm, entries in costing.get("prevention_costs", {}).get("arm_specific", {}).items():
        for key, entry in entries.items():
            add(
                f"prevention_costs.arm_specific.{arm}.{key}.value",
                _label(entry, f"{arm} {key}"), "Prevention costs", "EUR", 0, 1_000_000,
                str(entry.get("notes", "")),
            )
    for key, entry in costing.get("disease_costs", {}).items():
        if isinstance(entry, dict) and "value" in entry:
            add(
                f"disease_costs.{key}.value", _label(entry, key), "HIV care costs",
                "EUR", 0, 1_000_000, str(entry.get("notes", "")),
            )
    for key, entry in costing.get("utilities", {}).items():
        if key.startswith("_") or not isinstance(entry, dict) or "value" not in entry:
            continue
        add(
            f"utilities.{key}.value", _label(entry, key), "Health-state utilities",
            "utility", 0, 1, str(entry.get("notes", "")),
        )
    for item in costing.get("adverse_events", {}).get("items", []):
        item_id = str(item.get("id"))
        base_label = _label(item, item_id)
        add(
            f"adverse_events.items.{item_id}.prevalence.value",
            f"{base_label}: prevalence", "Adverse events", "proportion", 0, 1,
            str(item.get("prevalence", {}).get("notes", "")),
        )
        add(
            f"adverse_events.items.{item_id}.utility_decrement.value",
            f"{base_label}: utility decrement", "Adverse events", "utility", -1, 0,
            str(item.get("utility_decrement", {}).get("notes", "")),
        )
        add(
            f"adverse_events.items.{item_id}.cost.value",
            f"{base_label}: treatment cost", "Adverse events", "EUR", 0, 1_000_000,
            str(item.get("cost", {}).get("notes", "")),
        )
    return controls


def economic_setup(root: Path | str, runtime_path: str = DEFAULT_RUNTIME_CONFIG, costing_path: str = DEFAULT_COSTING_CONFIG) -> dict[str, Any]:
    root = Path(root).resolve()
    runtime = _read_json(root / runtime_path)
    costing = _read_json(root / costing_path)
    return {
        "start_year": int(runtime["study_defaults"]["start_year"]),
        "freeze_year": int(runtime["study_defaults"]["freeze_year"]),
        "evaluation_years": int(runtime["study_defaults"]["evaluation_years"]),
        "cost_scope": cost_scope_label(
            bool(costing.get("perspective", {}).get("patient_time_costs", {}).get("enabled", False)),
            bool(costing.get("perspective", {}).get("productivity_loss", {}).get("enabled", False)),
        ),
        "include_patient_time": bool(costing.get("perspective", {}).get("patient_time_costs", {}).get("enabled", False)),
        "include_productivity_loss": bool(costing.get("perspective", {}).get("productivity_loss", {}).get("enabled", False)),
        "informal_care_available": False,
        "adverse_event_mode": str(costing.get("adverse_events", {}).get("mode", "not recorded")),
        "scenarios": policy_scenario_matrix(root),
    }


def validate_economic_draft(root: Path | str, draft: EconomicDraft) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    if not draft.name.strip():
        errors.append("analysis name is required")
    for label, relative in (
        ("model", draft.base_config),
        ("runtime", draft.runtime_config),
        ("costing", draft.costing_config),
        ("policy matrix", draft.policy_overlay),
    ):
        if not (root / relative).is_file():
            errors.append(f"{label} configuration does not exist: {relative}")
    if not draft.seeds:
        errors.append("at least one stochastic run is required")
    if len(draft.seeds) > 20:
        errors.append("the local economic runner supports at most 20 stochastic runs")
    if len(draft.seeds) != len(set(draft.seeds)):
        errors.append("random seeds must be unique")
    if any(seed < 0 or seed > 2**32 - 1 for seed in draft.seeds):
        errors.append("random seeds must be integers in [0, 4294967295]")
    if draft.evaluation_years < 1 or draft.evaluation_years > 80:
        errors.append("economic horizon must be between 1 and 80 years")
    if draft.workers != ALL_AVAILABLE_WORKERS and not (
        1 <= draft.workers <= MAX_CONFIGURED_WORKERS
    ):
        errors.append(
            f"workers must be 1 to {MAX_CONFIGURED_WORKERS}, or all available"
        )
    if errors:
        return errors
    try:
        contract = _read_json(root / "configs/collaborator_parameter_contract.json")
        model_rules = {
            str(item["path"]): item for item in contract.get("parameters", [])
        }
        unknown_model = set(draft.model_overrides) - set(model_rules)
        if unknown_model:
            errors.append(f"unsupported epidemiological inputs: {sorted(unknown_model)}")
        for path, value in draft.model_overrides.items():
            rule = model_rules.get(path)
            if rule is None:
                continue
            number = float(value)
            minimum = float(rule["minimum"])
            maximum = float(rule["maximum"])
            if not math.isfinite(number) or not minimum <= number <= maximum:
                errors.append(f"{path} must be between {minimum:g} and {maximum:g}")
            must_equal = rule.get("must_equal")
            if must_equal and must_equal in draft.model_overrides:
                if not math.isclose(number, float(draft.model_overrides[must_equal]), rel_tol=0, abs_tol=1e-12):
                    errors.append(f"{path} must equal {must_equal}")
        costing = _read_json(root / draft.costing_config)
        allowed = {control.path: control for control in economic_controls(root, draft.costing_config)}
        unknown = set(draft.costing_overrides) - set(allowed)
        if unknown:
            errors.append(f"unsupported economic inputs: {sorted(unknown)}")
        for path, value in draft.costing_overrides.items():
            control = allowed.get(path)
            if control is None:
                continue
            number = float(value)
            if not math.isfinite(number) or not control.minimum <= number <= control.maximum:
                errors.append(f"{control.label} must be between {control.minimum:g} and {control.maximum:g}")
            else:
                _path_set(costing, path, number)
        costing["perspective"]["patient_time_costs"]["enabled"] = bool(draft.include_patient_time)
        costing["perspective"]["productivity_loss"]["enabled"] = bool(draft.include_productivity_loss)
        costing["perspective"]["primary"] = (
            "societal" if draft.include_patient_time or draft.include_productivity_loss else "payer"
        )
        validate_hta_config(costing)
        runtime = _selected_policy_runtime(root, draft)
        composed = compose_split_hta_config(runtime, str(root / draft.runtime_config))
        if int(_unwrap(composed["time_horizon"]["freeze_year"])) != int(_unwrap(costing["discounting"]["base_year"])):
            errors.append("the policy freeze year and economic discounting base year must match")
    except (EconomicWorkbenchError, KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def _set_seed(config: dict[str, Any], seed: int) -> None:
    run_control = config.setdefault("run_control", {})
    current = run_control.get("seed")
    if isinstance(current, dict):
        current["value"] = seed
    else:
        run_control["seed"] = seed


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Key order is meaningful: the HTA validator requires a distribution's source
    # parameter to appear earlier in the document, so never sort keys here.
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def execute_economic_draft(
    root: Path | str,
    draft: EconomicDraft,
    run_root: Path | str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    root = Path(root).resolve()
    run_root = Path(run_root).resolve()
    output_root = (root / "outputs" / "workbench_economic").resolve()
    try:
        run_root.relative_to(output_root)
    except ValueError as exc:
        raise EconomicWorkbenchError(f"economic run directory must be inside {output_root}") from exc
    if run_root == output_root:
        raise EconomicWorkbenchError("economic run needs a named output directory")
    errors = validate_economic_draft(root, draft)
    if errors:
        raise EconomicWorkbenchError("; ".join(errors))
    if run_root.exists() and (not run_root.is_dir() or any(run_root.iterdir())):
        raise EconomicWorkbenchError(f"economic run directory must be new or empty: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "logs").mkdir()
    config_dir = run_root / "configs"
    config_dir.mkdir()

    costing = _read_json(root / draft.costing_config)
    for path, value in draft.costing_overrides.items():
        _path_set(costing, path, float(value))
    costing["perspective"]["patient_time_costs"]["enabled"] = bool(draft.include_patient_time)
    costing["perspective"]["productivity_loss"]["enabled"] = bool(draft.include_productivity_loss)
    costing["perspective"]["primary"] = (
        "societal" if draft.include_patient_time or draft.include_productivity_loss else "payer"
    )
    costing_out = config_dir / "costing.json"
    _write_json(costing_out, costing)
    runtime = _selected_policy_runtime(root, draft)
    runtime["study_defaults"]["economic_inputs_file"] = "costing.json"
    runtime_out = config_dir / "runtime.json"
    _write_json(runtime_out, runtime)

    manifest: dict[str, Any] = {
        "schema": "workbench_economic_run/1.0.0",
        "status": "RUNNING",
        "name": draft.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "draft": asdict(draft),
        "inputs": {
            "base_config": draft.base_config,
            "runtime_config": draft.runtime_config,
            "costing_config": draft.costing_config,
            "policy_overlay": draft.policy_overlay,
        },
        "cost_scope": cost_scope_label(draft.include_patient_time, draft.include_productivity_loss),
        "policy_period": {
            "start_year": int(runtime["study_defaults"]["freeze_year"]),
            "evaluation_years": int(runtime["study_defaults"]["evaluation_years"]),
        },
        "execution": {
            "requested_workers": int(draft.workers),
            "effective_workers": effective_worker_count(draft.workers, len(draft.seeds)),
            "logical_cpus": logical_cpu_count(),
        },
        "derived_configs": {
            "runtime": str(runtime_out.relative_to(run_root)),
            "runtime_sha256": _sha256(runtime_out),
            "costing": str(costing_out.relative_to(run_root)),
            "costing_sha256": _sha256(costing_out),
        },
        "runs": [],
    }
    manifest_path = run_root / ECONOMIC_MANIFEST
    _write_json(manifest_path, manifest)
    started = time.monotonic()
    try:
        tasks: list[tuple[dict[str, Any], list[str], Path, Path, Path]] = []
        for seed in draft.seeds:
            base = _read_json(root / draft.base_config)
            for path, value in draft.model_overrides.items():
                _path_set(base, path, float(value))
            _set_seed(base, seed)
            base_path = config_dir / f"model_seed_{seed:010d}.json"
            _write_json(base_path, base)
            result_dir = run_root / f"seed_{seed:010d}" / "results"
            result_dir.mkdir(parents=True)
            stdout_path = run_root / "logs" / f"seed_{seed:010d}.stdout.log"
            stderr_path = run_root / "logs" / f"seed_{seed:010d}.stderr.log"
            argv = [
                sys.executable,
                str(root / "run_scenarios.py"),
                str(base_path),
                "--hta",
                str(runtime_out),
                "--output_dir",
                str(result_dir),
            ]
            record: dict[str, Any] = {
                "seed": seed,
                "status": "QUEUED",
                "model_config": str(base_path.relative_to(run_root)),
                "model_config_sha256": _sha256(base_path),
                "result_dir": str(result_dir.relative_to(run_root)),
                "stdout_log": str(stdout_path.relative_to(run_root)),
                "stderr_log": str(stderr_path.relative_to(run_root)),
                "argv": argv,
            }
            manifest["runs"].append(record)
            tasks.append((record, argv, result_dir, stdout_path, stderr_path))
        _write_json(manifest_path, manifest)

        def execute_seed(
            task: tuple[dict[str, Any], list[str], Path, Path, Path]
        ) -> tuple[dict[str, Any], list[str]]:
            record, argv, result_dir, stdout_path, stderr_path = task
            env = dict(__import__("os").environ)
            env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
            with stdout_path.open("x", encoding="utf-8") as stdout, stderr_path.open("x", encoding="utf-8") as stderr:
                completed = runner(argv, cwd=str(root), env=env, text=True, stdout=stdout, stderr=stderr, shell=False)
            if completed.returncode != 0:
                raise EconomicWorkbenchError(
                    f"economic seed {record['seed']} exited with code "
                    f"{completed.returncode}; see {stderr_path}"
                )
            required = [result_dir / "arm_summary.csv", result_dir / "comparison_table.csv"]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                raise EconomicWorkbenchError(f"economic output verification failed; missing {missing}")
            return record, [
                str(path.relative_to(run_root))
                for path in sorted(result_dir.glob("hta_report_*.xlsx"))
            ]

        failures: list[BaseException] = []
        parallelism = effective_worker_count(draft.workers, len(tasks))
        with ThreadPoolExecutor(
            max_workers=parallelism, thread_name_prefix="economic-run"
        ) as pool:
            future_map = {}
            for task in tasks:
                task[0]["status"] = "RUNNING"
                future_map[pool.submit(execute_seed, task)] = task[0]
            _write_json(manifest_path, manifest)
            for future in as_completed(future_map):
                record = future_map[future]
                try:
                    _, workbooks = future.result()
                except BaseException as exc:
                    record["status"] = "FAILED"
                    record["failure"] = {
                        "type": type(exc).__name__, "message": str(exc)
                    }
                    failures.append(exc)
                else:
                    record["returncode"] = 0
                    record["status"] = "COMPLETE"
                    record["workbooks"] = workbooks
                _write_json(manifest_path, manifest)
        if failures:
            raise failures[0]
        manifest["status"] = "COMPLETE"
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
        manifest["completed_utc"] = datetime.now(timezone.utc).isoformat()
        _write_json(manifest_path, manifest)
        return manifest
    except BaseException as exc:
        manifest["status"] = "FAILED"
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        for record in manifest["runs"]:
            if record["status"] in {"RUNNING", "QUEUED"}:
                record["status"] = "FAILED"
        _write_json(manifest_path, manifest)
        raise


def load_economic_result(path: Path | str) -> EconomicResult:
    root = Path(path).resolve()
    manifest = _read_json(root / ECONOMIC_MANIFEST)
    arm_frames: list[pd.DataFrame] = []
    comparison_frames: list[pd.DataFrame] = []
    budget_frames: list[pd.DataFrame] = []
    for record in manifest.get("runs", []):
        if record.get("status") != "COMPLETE":
            continue
        result_dir = root / record["result_dir"]
        seed = int(record["seed"])
        for filename, target in (("arm_summary.csv", arm_frames), ("comparison_table.csv", comparison_frames), ("budget_impact.csv", budget_frames)):
            candidate = result_dir / filename
            if candidate.is_file():
                frame = pd.read_csv(candidate)
                frame.insert(0, "seed", seed)
                target.append(frame)
    concat = lambda frames: pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return EconomicResult(root, manifest, concat(arm_frames), concat(comparison_frames), concat(budget_frames))


def available_economic_results(root: Path | str) -> list[dict[str, str]]:
    output = Path(root).resolve() / "outputs" / "workbench_economic"
    rows: list[dict[str, str]] = []
    if not output.is_dir():
        return rows
    manifests = sorted(output.glob(f"*/{ECONOMIC_MANIFEST}"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in manifests:
        try:
            payload = _read_json(path)
        except EconomicWorkbenchError:
            continue
        if payload.get("status") == "COMPLETE":
            rows.append({"path": str(path.parent.relative_to(Path(root).resolve())), "label": str(payload.get("name") or path.parent.name)})
    return rows


ECONOMIC_RECIPE_SCHEMA = "workbench_ui_economic_recipe/1.0.0"


def _recipe_dir(root: Path | str) -> Path:
    return Path(root).resolve() / "operator_configs" / "workbench_recipes"


def _recipe_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def save_economic_recipe(root: Path | str, name: str, settings: Mapping[str, Any]) -> Path:
    """Persist the forecast/economics form so it can be reopened later.

    ``settings`` is the JSON-serialisable form state (selected scenarios, cost
    scope, input overrides, run design).  Existing recipes are never replaced.
    """

    slug = _recipe_slug(name)
    if not slug:
        raise EconomicWorkbenchError("analysis name must contain a letter or number")
    directory = _recipe_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"economic-{slug}.json"
    if path.exists():
        raise EconomicWorkbenchError(
            f"a saved economic configuration named {slug!r} already exists; choose a new name"
        )
    payload = {
        "schema": ECONOMIC_RECIPE_SCHEMA,
        "id": f"economic-{slug}",
        "name": name.strip(),
        "settings": dict(settings),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def list_economic_recipes(root: Path | str) -> list[dict[str, Any]]:
    directory = _recipe_dir(root)
    rows: list[dict[str, Any]] = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("economic-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") == ECONOMIC_RECIPE_SCHEMA and isinstance(payload.get("settings"), dict):
            rows.append({"id": payload["id"], "name": payload["name"], "settings": payload["settings"]})
    return rows
