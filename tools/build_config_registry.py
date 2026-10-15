#!/usr/bin/env python3
"""Render or check the tracked, non-promotional configuration inventory.

The tool is deliberately read-only: ``--print`` writes the canonical CSV to
stdout and ``--check`` compares that rendering with ``CONFIG_REGISTRY.csv``.
Classification is explicit. Adding, removing, or changing a file under
``configs/`` therefore requires a reviewed registry update.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "configs"
REGISTRY_PATH = ROOT / "CONFIG_REGISTRY.csv"

ROLE_CURRENT = "current candidate"
ROLE_PROPOSED = "proposed"
ROLE_HISTORICAL = "historical"
ROLE_DIAGNOSTIC = "diagnostic"
ROLE_TEST = "test/fixture"
ROLE_SUITE = "suite/tool"
ROLE_REVIEW = "review-required"
ALLOWED_ROLES = {
    ROLE_CURRENT,
    ROLE_PROPOSED,
    ROLE_HISTORICAL,
    ROLE_DIAGNOSTIC,
    ROLE_TEST,
    ROLE_SUITE,
    ROLE_REVIEW,
}


def _names(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip()}


# Every tracked file below configs/ appears exactly once. These classifications
# are intentionally conservative; they describe evidence status, not filename
# prestige or whether a loader happens to accept the file.
ROLE_GROUPS: Mapping[str, set[str]] = {
    ROLE_CURRENT: _names("""
        V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json
    """),
    ROLE_PROPOSED: _names("""
        calib_space_v13_25_unordered_orientation_20260921.json
        collaborator_parameter_contract.json
        coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json
        hm_target_register_robust_nroy_production_20261015.json
        robust_nroy_freeze_production.json
    """),
    ROLE_HISTORICAL: _names("""
        V13.18_20260601_basecase.json
        V13.22_20260604.json
        V13.22_20260604_final_candidate_rank012.json
        V13.22_20260604_nearfit_rank001_basecase.json
        V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json
        calib_space_v13_10_msm_diag_multiplier_pilot.json
        calib_space_v13_11_orientation_seed_stage_pilot.json
        calib_space_v13_12_structural_capacity_msm_multiplier.json
        calib_space_v13_12b_structural_capacity_ordered_orientation.json
        calib_space_v13_15_1975_orientation_seed_abc.json
        calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json
        calib_space_v13_6_gate_repair.json
        calib_space_v13_7_seed_distribution_pilot.json
        calib_space_v13_9_3_msm_denominator_bridge.json
        coupled_search_plan_20260918.json
        coupled_search_plan_unordered_orientation_20260921.json
        hm_focused_warm_start_20260916.csv
        hm_forecast_windows_warm_start_20260921.csv
        hm_target_register.json
        hm_target_register_focused_20260916.json
        hm_target_register_rivm_msm_aids_20260916.json
        hm_wave_plan_baseline_learning_20260915.json
        hm_wave_plan_focused_continuation_20260915.json
        hm_wave_plan_focused_diagnoses_then_aids_20260916.json
        hm_wave_plan_forecast_windows_20260921.json
        v10_configuration.json
        v10_v6_rel_pop.json
        v11_configuration.json
    """),
    ROLE_DIAGNOSTIC: _names("""
        V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json
        art_interpolation_shape_audit_20260921.json
        calib_space_v13_14_1975_burnin_diagnostic.json
        calib_space_v13_24_1970_snpe_wide_no_disease.json
        coupled_search_plan_unordered_orientation_activity_diagnostics_20260921.json
        diagnosis_triangulation_orchestration_20260922.json
        forecast_readiness_confirmation_20260921.json
        hm_wave_plan_msm_aids_diagnostic_20260916.json
        initialisation_1990_readiness.json
        msm_beta_response_audit_20260921.json
        post2015_stock_flow_decomposition_20260921.json
        post2019_diagnosis_disruption_audit_20260921.json
        recent_mechanism_audit_20260921.json
        rivm_aids_source_policy_20260916.json
        stage_structured_diagnosis_audit_20260922.json
        stage_structured_diagnosis_focused_calibration_20260922.json
        temporal_diagnosis_shape_audit_20260921.json
        v13_14_1975_burnin_base.json
        v13_24_1970_population_snpe_base.json
    """),
    ROLE_TEST: _names("""
        1980_test.json
        v10_dist_test.json
        v10_edge_test.json
        v10_test_adj_0.json
        v10_test_adj_1.json
        v10_test_adj_2.json
        v10_test_adj_3.json
        v10_test_pop_adj.json
        v6_golden_reference.json
    """),
    ROLE_SUITE: _names("""
        README.md
        V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.derivation.json
        disease_target_mapping.json
        hm_wave_plan_template.json
        hta_config.json
        hta_config_adjusted.json
        hta_costing_config.json
        hta_prep_policy_suite_20260929.overlay.json
        hta_runtime_config.json
        psa_conference_20260929.json
        psa_config.json
        suites/README.md
        suites/conference_overnight_20260929.json
        suites/examples/on_art_break_year_2008_vs_2010.json
    """),
    ROLE_REVIEW: _names("""
        calib_space.json
        model_final.json
    """),
}

DEFAULT_STATUS = {
    ROLE_CURRENT: (
        "PREPARED_NOT_APPROVED — mechanical shared M0/M1 candidate; production "
        "selection is blocked"
    ),
    ROLE_PROPOSED: "UNSIGNED_PROPOSAL — requires scientific review and applicable gates",
    ROLE_HISTORICAL: (
        "HISTORICAL_RETAINED — valid only for its recorded code/workflow snapshot"
    ),
    ROLE_DIAGNOSTIC: "DIAGNOSTIC_ONLY — not production evidence",
    ROLE_TEST: "TEST_ONLY — never scientific evidence",
    ROLE_SUITE: (
        "WORKFLOW_INPUT — requires separate review in the named suite/tool workflow"
    ),
    ROLE_REVIEW: "UNCLASSIFIED_FOR_USE — provenance and role must be resolved first",
}

DEFAULT_SAFE_NOTE = {
    ROLE_CURRENT: "Do not use for production until every stated blocker and gate is closed.",
    ROLE_PROPOSED: "Inspect only; a proposal is not a signed decision or run authorization.",
    ROLE_HISTORICAL: "Use only to reproduce its recorded historical lineage; do not relabel current.",
    ROLE_DIAGNOSTIC: "Use only for the named diagnostic with its original assumptions and code snapshot.",
    ROLE_TEST: "Use only in tests; never substitute into calibration, policy, PSA, or production runs.",
    ROLE_SUITE: "Use only through the named tool/suite after reviewing its own inputs and evidence status.",
    ROLE_REVIEW: "Do not use until a maintainer resolves the ambiguous name, provenance, and intended workflow.",
}

STATUS_OVERRIDES = {
    "V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json": (
        "REQUIRES_RECALIBRATION — Option B 1980 calibration candidate; not fitted; "
        "not validated; not production eligible"
    ),
    "V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.derivation.json": (
        "DERIVATION_EVIDENCE — exact Option B parent/source hashes and JSON-path diff; "
        "not production approval"
    ),
    "robust_nroy_freeze_production.json": (
        "UNSIGNED_PROPOSAL — 45 owner-assigned decisions remain unsigned; fails closed"
    ),
    "calib_space_v13_25_unordered_orientation_20260921.json": (
        "UNSIGNED_PROPOSAL — proposed robust-NROY M0 space; bounds and injection paths require review"
    ),
    "collaborator_parameter_contract.json": (
        "UNAPPROVED_CONTRACT — fail-closed edit allowlist; production use requires exact "
        "SHA-256 binding in the canonical production approval"
    ),
    "coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json": (
        "PREPARATION_ONLY_NOT_AUTHORIZED — exact Option B inputs and 1980 window are bound; "
        "seed decisions and execution authorization remain unsigned"
    ),
    "hm_target_register_robust_nroy_production_20261015.json": (
        "UNSIGNED_PROPOSAL — proposed 13-target contract; target constructs and provenance require review"
    ),
}

SAFE_NOTE_OVERRIDES = {
    "V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json": (
        "Use only as the declared starting config for a newly authorized calibration. Do not report its "
        "inherited values as a 1980 fit and do not use it for policy, PSA, or production runs."
    ),
    "V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.derivation.json": (
        "Inspect to audit the candidate derivation; it does not authorize calibration or production."
    ),
    "robust_nroy_freeze_production.json": (
        "Validation-only use is safe; it must continue to fail closed until all decisions and evidence "
        "dependencies are signed."
    ),
    "calib_space_v13_25_unordered_orientation_20260921.json": (
        "Do not treat bounds as accepted; use only after joint scientific review and signed-plan binding."
    ),
    "collaborator_parameter_contract.json": (
        "Use to validate draft copies only; binding its exact hash in production approval means the "
        "approver reviewed every listed path and interval."
    ),
    "coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json": (
        "Validation-only use is safe. Historical 1975 vectors are proposal seeds only; do not "
        "prepare or run until seed fields and execution authorization are explicitly approved."
    ),
    "hm_target_register_robust_nroy_production_20261015.json": (
        "Do not treat targets as accepted; use only after source-window, construct, and provenance review."
    ),
}

REGISTRY_COLUMNS = (
    "path",
    "sha256",
    "role_class",
    "scientific_status",
    "key_provenance_or_version",
    "consumers_or_references",
    "safe_collaborator_use",
)


def _flatten_roles() -> dict[str, str]:
    roles: dict[str, str] = {}
    duplicates: list[str] = []
    for role, paths in ROLE_GROUPS.items():
        if role not in ALLOWED_ROLES:
            raise ValueError(f"unknown role class {role!r}")
        for relative in paths:
            if relative in roles:
                duplicates.append(relative)
            roles[relative] = role
    if duplicates:
        raise ValueError(f"config classifications are duplicated: {sorted(duplicates)}")
    return roles


def config_files(config_dir: Path = CONFIG_DIR) -> set[str]:
    """Return repository files, excluding only untracked machine/cache debris."""
    ignored_names = {".DS_Store"}
    out: set[str] = set()
    for path in config_dir.rglob("*"):
        if not path.is_file() or path.name in ignored_names:
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        out.add(path.relative_to(config_dir).as_posix())
    return out


def inventory_problems(config_dir: Path = CONFIG_DIR) -> list[str]:
    classified = set(_flatten_roles())
    present = config_files(config_dir)
    problems: list[str] = []
    missing = sorted(classified - present)
    extra = sorted(present - classified)
    if missing:
        problems.append(f"classified config files are missing: {missing}")
    if extra:
        problems.append(f"config files lack registry classification: {extra}")
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(raw: Any) -> Any:
    if isinstance(raw, Mapping) and "value" in raw:
        return raw["value"]
    return raw


def _short(raw: Any, *, limit: int = 180) -> str:
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    else:
        text = str(raw)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def provenance_or_version(path: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"INVALID JSON: {exc}"
        if not isinstance(payload, Mapping):
            return "JSON value; no object-level provenance/version"
        found: list[str] = []
        schema = _value(payload.get("schema"))
        if schema:
            found.append(f"schema={_short(schema)}")
        meta = payload.get("meta")
        if isinstance(meta, Mapping):
            version = _value(meta.get("version"))
            if version:
                found.append(f"meta.version={_short(version)}")
            status = _value(meta.get("model_notes"))
            if isinstance(status, Mapping) and status.get("calibration_status"):
                found.append(
                    f"calibration_status={_short(status['calibration_status'], limit=120)}"
                )
        for key in ("version", "design", "name", "source_config_path", "source_config"):
            value = _value(payload.get(key))
            if value:
                found.append(f"{key}={_short(value)}")
        if not found and payload.get("_readme"):
            found.append(f"_readme={_short(payload['_readme'])}")
        return "; ".join(found[:4]) or "JSON; no explicit version/provenance field"
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            rows = sum(1 for _ in reader)
        return f"CSV rows={rows}; columns={_short(header)}"
    if path.suffix.lower() == ".md":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                return f"documentation={_short(line.lstrip('#').strip())}"
        return "Markdown documentation; no heading"
    return f"{path.suffix.lstrip('.') or 'binary'} file; no structured provenance"


def _repository_text_files(root: Path) -> list[Path]:
    excluded_roots = {".git", ".venv", "outputs", "node_modules", "__pycache__"}
    allowed_suffixes = {".py", ".md", ".json", ".csv", ".toml", ".yaml", ".yml", ".sh", ".txt"}
    excluded_files = {
        REGISTRY_PATH.resolve(),
        Path(__file__).resolve(),
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True,
        capture_output=True, text=False,
    ).stdout.split(b"\0")
    out: list[Path] = []
    for raw in tracked:
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in excluded_roots for part in relative.parts):
            continue
        if path.resolve() in excluded_files:
            continue
        out.append(path)
    return sorted(out)


def reference_index(root: Path = ROOT) -> dict[str, str]:
    """Return concise tracked/source references for each classified config."""
    roles = _flatten_roles()
    hits: dict[str, list[str]] = {relative: [] for relative in roles}
    basenames: dict[str, list[str]] = {}
    for relative in roles:
        basenames.setdefault(Path(relative).name, []).append(relative)
    for candidate in _repository_text_files(root):
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        label = candidate.relative_to(root).as_posix()
        for relative in roles:
            full = f"configs/{relative}"
            basename = Path(relative).name
            basename_is_unique = len(basenames[basename]) == 1 and basename not in {"README.md"}
            if full in text or (basename_is_unique and basename in text):
                if label != full:
                    hits[relative].append(label)
    rendered: dict[str, str] = {}
    for relative, paths in hits.items():
        unique = sorted(set(paths))
        if not unique:
            rendered[relative] = "no repository reference found; review before moving or removing"
        elif len(unique) <= 12:
            rendered[relative] = "; ".join(unique)
        else:
            rendered[relative] = "; ".join(unique[:12]) + f"; ... (+{len(unique) - 12} more)"
    return rendered


def registry_rows(root: Path = ROOT) -> list[dict[str, str]]:
    problems = inventory_problems(root / "configs")
    if problems:
        raise ValueError("; ".join(problems))
    roles = _flatten_roles()
    references = reference_index(root)
    rows: list[dict[str, str]] = []
    for relative in sorted(roles):
        role = roles[relative]
        path = root / "configs" / relative
        rows.append({
            "path": f"configs/{relative}",
            "sha256": _sha256(path),
            "role_class": role,
            "scientific_status": STATUS_OVERRIDES.get(relative, DEFAULT_STATUS[role]),
            "key_provenance_or_version": provenance_or_version(path),
            "consumers_or_references": references[relative],
            "safe_collaborator_use": SAFE_NOTE_OVERRIDES.get(relative, DEFAULT_SAFE_NOTE[role]),
        })
    return rows


def render_registry(rows: Iterable[Mapping[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REGISTRY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def check_registry(root: Path = ROOT, registry_path: Path | None = None) -> list[str]:
    path = registry_path or (root / REGISTRY_PATH.name)
    try:
        expected = render_registry(registry_rows(root))
    except ValueError as exc:
        return [str(exc)]
    if not path.exists():
        return [f"registry is missing: {path}"]
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        return [
            f"registry drift: {path} does not match the current configs tree, content hashes, "
            "classifications, provenance fields, or references; inspect with "
            "`python tools/build_config_registry.py --print`"
        ]
    return []


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail if the tracked registry drifted")
    mode.add_argument("--print", action="store_true", dest="print_registry", help="print canonical CSV")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.print_registry:
        try:
            sys.stdout.write(render_registry(registry_rows()))
        except ValueError as exc:
            print(f"CONFIG REGISTRY ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    problems = check_registry()
    if problems:
        for problem in problems:
            print(f"CONFIG REGISTRY ERROR: {problem}", file=sys.stderr)
        return 1
    print(f"CONFIG REGISTRY OK: {len(registry_rows())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
