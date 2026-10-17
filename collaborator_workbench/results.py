"""Import, normalize, and report existing HIV-PrEP model outputs.

This module is read-only with respect to source results.  It understands the
ordinary ``run_model.py`` manifest/CSV contract, can combine independent seed
runs, and falls back to a generic table inspector for unfamiliar CSV files.

Annualization is deliberately allow-list based.  Unknown quarterly columns are
never summed or averaged by guesswork; they remain available in the raw table
and are listed as unclassified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from collaborator_workbench.model_diagrams import (
    ANALYSIS_WORKFLOW_SVG,
    DISEASE_PATHWAY_SVG,
    STATE_SPACE_SVG,
)
from collaborator_workbench.model_guide import (
    FORMULAS,
    HISTORY_MATCHING_COMPONENTS,
    HISTORY_MATCHING_INTRO,
    KEY_TERMS,
    MODEL_INTRO,
    MODEL_SCOPE,
    PARAMETER_FAMILIES,
    REFERENCES,
    VALIDATION_NOTE,
)
from collaborator_workbench.workbook import build_workbook
from workbench_ui.presentation import (
    display_model,
    display_probability,
    display_status,
    display_warning,
)


RUN_MANIFEST = "run_reproducibility_manifest.json"
SUPPORTED_MANIFEST_SCHEMAS = {"model_run_reproducibility/1.0.0", "model_run_reproducibility/1.1.0"}
DEFAULT_MAX_CSV_FILES = 200
DEFAULT_MAX_CSV_BYTES = 512 * 1024 * 1024


class ResultImportError(ValueError):
    """An output cannot be imported safely or unambiguously."""


def _label(name: str) -> str:
    special = {"msm": "MSM", "hiv": "HIV", "aids": "AIDS", "art": "ART", "prep": "PrEP"}
    return " ".join(special.get(word.lower(), word.capitalize()) for word in name.split("_"))


@dataclass(frozen=True)
class MetricSemantic:
    """Explicit rule used to turn timestep output into an annual value."""

    name: str
    label: str
    aggregation: str
    unit: str
    category: str


@dataclass
class ImportedTable:
    """One source CSV and the information discovered about it."""

    source_path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    frame: pd.DataFrame
    role: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportedRun:
    """One model trajectory, normally one route/seed CSV."""

    run_id: str
    source: ImportedTable
    annual: pd.DataFrame
    metric_catalog: pd.DataFrame
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    model: str | None = None
    p: float | None = None
    seed: int | None = None
    status: str | None = None
    classification: str | None = None
    production_eligible: bool | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def route_id(self) -> str:
        model = self.model or "unspecified"
        return f"{model}|p={self.p if self.p is not None else 'none'}"


@dataclass
class ResultBundle:
    """Imported source plus normalized model runs and generic tables."""

    source_path: Path
    runs: list[ImportedRun]
    tables: list[ImportedTable]
    annual_long: pd.DataFrame
    across_run_summary: pd.DataFrame
    warnings: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.runs and len(self.runs) == len(self.tables):
            return "model"
        if self.runs:
            return "mixed"
        return "generic"


@dataclass(frozen=True)
class ReportArtifacts:
    report_dir: Path
    workbook_xlsx: Path
    index_html: Path
    annual_csv: Path | None
    summary_csv: Path | None
    metric_catalog_csv: Path | None
    source_inventory_csv: Path
    provenance_json: Path
    figures: tuple[Path, ...]


def _semantics() -> dict[str, MetricSemantic]:
    flows = {
        "new_infections", "hiv_deaths", "aids_deaths", "new_aids_cases",
        "new_diagnoses", "new_diagnoses_msm", "new_diagnoses_endogenous",
        "new_diagnoses_endogenous_msm", "new_diagnoses_endogenous_non_msm",
        "new_diagnoses_exogenous_non_msm", "art_initiations",
        "art_initiations_acute", "art_initiations_chronic", "art_initiations_aids",
        "background_deaths", "childhood_deaths", "births", "sexual_debuts",
        "continuous_debuts", "flush_debuts", "net_migration",
        "migrant_hiv_entries", "migrant_hiv_on_art_entries", "migrant_hiv_msm_entries",
        "migrant_hiv_non_msm_entries", "migrant_hiv_hetero_male_entries",
        "migrant_hiv_female_entries", "migrant_hiv_requested_entries",
        "migrant_hiv_capped_entries", "migrant_hiv_cap_shortfall",
        "prep_oral_initiations", "prep_lena_initiations", "prep_oral_dropouts",
        "prep_lena_dropouts", "prep_oral_corridor_additions",
        "prep_lena_corridor_additions", "prep_oral_corridor_removals",
        "prep_lena_corridor_removals", "prep_oral_target_additions",
        "prep_lena_target_additions", "prep_lena_switches",
        "prep_lena_switch_shortfall", "new_infections_msm",
        "new_infections_non_msm", "new_infections_msm_on_prep",
        "aids_art_initiations", "same_step_aids_art_rescue_proxy",
        # Explicit AIDS observation flows used by calib_extractor.py.
        "aids_progression_while_undiagnosed", "aids_progression_while_diagnosed",
        "aids_progression_while_undiagnosed_msm",
        "aids_progression_while_undiagnosed_non_msm",
        "aids_progression_while_diagnosed_msm",
        "aids_progression_while_diagnosed_non_msm",
        "aids_progression_while_diagnosed_off_art",
        "aids_progression_while_diagnosed_on_art",
        "aids_progression_while_diagnosed_off_art_msm",
        "aids_progression_while_diagnosed_off_art_non_msm",
        "aids_progression_while_diagnosed_on_art_msm",
        "aids_progression_while_diagnosed_on_art_non_msm",
        "hiv_diagnoses_in_aids_stage", "hiv_diagnoses_in_aids_stage_msm",
        "hiv_diagnoses_in_aids_stage_non_msm",
    }
    stocks = {
        "on_art_stock", "adult_hiv_pos", "plhiv_female_15plus",
        "plhiv_male_15plus", "plhiv_child_0_14", "total_population",
        "sexually_active_population", "childhood_population", "msm_total",
        "msm_susceptible", "msm_eligible_prep", "msm_high_risk",
        "msm_high_risk_susceptible", "msm_high_risk_on_prep", "msm_hiv_pos",
        "non_msm_hiv_pos", "msm_undiagnosed_hiv_pos",
        "non_msm_undiagnosed_hiv_pos", "msm_on_oral", "msm_on_lena",
        "aids_stock_pre_mortality", "aids_off_art_pre_mortality",
        "aids_on_art_pre_mortality", "aids_undiagnosed_pre_mortality",
        "aids_diagnosed_pre_mortality",
    }
    start_stocks = {
        "aids_stock_start", "aids_off_art_start", "aids_on_art_start",
        "aids_undiagnosed_start", "aids_diagnosed_start",
    }
    proportions = {"prevalence", "prep_coverage_Oral_PrEP", "prep_coverage_Lenacapavir"}
    annual_rates = {"incidence_rate"}
    person_time = {
        "chronic_diagnosed_off_art_person_years",
        "chronic_diagnosed_on_art_person_years",
        "chronic_diagnosed_off_art_person_years_msm",
        "chronic_diagnosed_off_art_person_years_non_msm",
        "chronic_diagnosed_on_art_person_years_msm",
        "chronic_diagnosed_on_art_person_years_non_msm",
    }

    result: dict[str, MetricSemantic] = {}
    for name in sorted(flows):
        result[name] = MetricSemantic(name, _label(name), "annual_sum", "people", "flow")
    for name in sorted(stocks):
        result[name] = MetricSemantic(name, _label(name), "year_end", "people", "stock")
    for name in sorted(start_stocks):
        result[name] = MetricSemantic(name, _label(name), "year_start", "people", "stock")
    for name in sorted(proportions):
        result[name] = MetricSemantic(name, _label(name), "year_end", "proportion", "proportion")
    for name in sorted(annual_rates):
        result[name] = MetricSemantic(name, _label(name), "annual_mean", "per person-year", "rate")
    for name in sorted(person_time):
        result[name] = MetricSemantic(name, _label(name), "annual_sum", "person-years", "person_time")
    return result


METRIC_SEMANTICS = _semantics()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ResultImportError(f"duplicate JSON key {key!r} in {path}")
            out[key] = value
        return out

    def reject_constant(value: str) -> Any:
        raise ResultImportError(f"non-finite JSON value {value!r} in {path}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ResultImportError(f"non-finite JSON value {value!r} in {path}")
        return parsed

    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                             parse_constant=reject_constant, parse_float=finite_float)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultImportError(f"cannot read JSON manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultImportError(f"manifest must contain one JSON object: {path}")
    return payload


def annualize_model_output(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Annualize known native model metrics using explicit scientific semantics.

    Annual sums are missing if any timestep is missing, preventing partial-year
    data from being presented as a complete event count.  Stocks/proportions use
    the final recorded timestep, except explicitly named ``*_start`` diagnostic
    stocks, which use the first timestep.  ``incidence_rate`` is already an
    annualized per-person rate at each step and is averaged over the year.
    """
    if "year" not in frame.columns:
        return pd.DataFrame(), _catalog(frame.columns, ()), ["No 'year' column; annualization was not attempted."]
    work = frame.copy()
    numeric_year = pd.to_numeric(work["year"], errors="coerce")
    if numeric_year.isna().any() or not np.isfinite(numeric_year).all():
        return pd.DataFrame(), _catalog(frame.columns, ()), ["The 'year' column contains invalid values; annualization was not attempted."]
    work["_source_order"] = np.arange(len(work))
    work["_numeric_year"] = numeric_year.astype(float)
    work["_calendar_year"] = np.floor(work["_numeric_year"]).astype(int)
    work = work.sort_values(["_numeric_year", "_source_order"], kind="stable")

    known = [name for name in METRIC_SEMANTICS if name in work.columns]
    warnings: list[str] = []
    if not known:
        per_year = work.groupby("_calendar_year", sort=True).size()
        if (per_year <= 1).all():
            warnings.append("No native model metrics were recognized; the CSV is available in generic inspection mode.")
        else:
            warnings.append("Unknown timestep metrics were not annualized because their stock/flow semantics are not declared.")
        return pd.DataFrame(), _catalog(frame.columns, known), warnings

    rows: list[dict[str, float | int]] = []
    for year, group in work.groupby("_calendar_year", sort=True):
        row: dict[str, float | int] = {"year": int(year)}
        for name in known:
            semantic = METRIC_SEMANTICS[name]
            values = pd.to_numeric(group[name], errors="coerce")
            if semantic.aggregation == "annual_sum":
                value = values.sum(min_count=len(group))
            elif semantic.aggregation == "year_start":
                value = values.iloc[0]
            elif semantic.aggregation == "year_end":
                value = values.iloc[-1]
            elif semantic.aggregation == "annual_mean":
                value = values.mean() if values.notna().all() else np.nan
            else:  # pragma: no cover - guarded by the registry
                raise AssertionError(semantic.aggregation)
            row[name] = float(value) if pd.notna(value) else np.nan

        # Match calib_extractor's all-age PLHIV definition, per trajectory.
        if "adult_hiv_pos" in row:
            child = row.get("plhiv_child_0_14", 0.0)
            row["PLHIV"] = row["adult_hiv_pos"] + child
        elif "prevalence" in row and "sexually_active_population" in row:
            row["PLHIV"] = row["prevalence"] * row["sexually_active_population"]
        rows.append(row)

    annual = pd.DataFrame(rows)
    if "PLHIV" in annual.columns:
        known.append("PLHIV")
    unknown_numeric = [
        col for col in frame.select_dtypes(include=[np.number]).columns
        if col not in known and col not in {"year", "step"}
    ]
    if unknown_numeric:
        warnings.append(
            "Unclassified numeric columns remain available only in the raw table: "
            + ", ".join(sorted(unknown_numeric))
        )
    return annual, _catalog(frame.columns, known), warnings


def _catalog(all_columns: Iterable[str], included: Iterable[str]) -> pd.DataFrame:
    included_set = set(included)
    rows: list[dict[str, Any]] = []
    for name in all_columns:
        if name in {"year", "step"}:
            continue
        semantic = METRIC_SEMANTICS.get(name)
        rows.append({
            "metric": name,
            "label": semantic.label if semantic else _label(name),
            "category": semantic.category if semantic else "unclassified",
            "unit": semantic.unit if semantic else "unspecified",
            "annual_aggregation": semantic.aggregation if semantic else "not_annualized",
            "included_in_normalized_annual": name in included_set,
        })
    if "PLHIV" in included_set:
        rows.append({"metric": "PLHIV", "label": "PLHIV", "category": "derived_stock",
                     "unit": "people", "annual_aggregation": "year_end",
                     "included_in_normalized_annual": True})
    return pd.DataFrame(rows)


def inspect_output(path: str | Path, *, max_csv_files: int = DEFAULT_MAX_CSV_FILES,
                   max_csv_bytes: int = DEFAULT_MAX_CSV_BYTES) -> ResultBundle:
    """Load an existing run directory or CSV without changing the source.

    Native manifests take precedence and their scientific-output hashes are
    verified.  Other CSVs are inspected generically.  A broad directory scan is
    capped so selecting the repository root cannot accidentally load thousands
    of files into memory.
    """
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ResultImportError(f"output path does not exist: {source}")
    if not source.is_file() and not source.is_dir():
        raise ResultImportError(f"output path is neither a file nor directory: {source}")
    if source.is_file() and source.suffix.lower() not in {".csv", ".json"}:
        raise ResultImportError("individual output imports currently require a CSV or recognized candidate JSON")

    manifests = [] if source.is_file() else sorted(source.rglob(RUN_MANIFEST))
    manifest_by_csv: dict[Path, tuple[Path, dict[str, Any], list[str]]] = {}
    bundle_warnings: list[str] = []
    for manifest_path in manifests:
        try:
            manifest = _strict_json(manifest_path)
        except ResultImportError as exc:
            bundle_warnings.append(str(exc))
            continue
        warnings = _validate_manifest(manifest, manifest_path)
        csv_path = _manifest_csv_path(manifest, manifest_path)
        if csv_path is None or not csv_path.is_file():
            bundle_warnings.append(f"Manifest has no readable scientific CSV: {manifest_path}")
            continue
        manifest_by_csv[csv_path] = (manifest_path, manifest, warnings)

    if source.is_file():
        candidates = [source] if source.suffix.lower() == ".csv" else []
        candidate_jsons = [source] if source.suffix.lower() == ".json" else []
    else:
        candidates = sorted({*manifest_by_csv, *source.rglob("*.csv")})
        candidates = [p for p in candidates if not _is_generated_report_file(p)]
        # Calibration candidates are intentionally selected by name here.  A
        # broad directory can contain many large configs/manifests that are not
        # result data.  An explicitly selected JSON is always examined.
        candidate_jsons = sorted(source.rglob("*candidate*.json"))
    if len(candidates) > max_csv_files:
        raise ResultImportError(
            f"{len(candidates)} CSV files found under {source}; select a narrower run directory "
            f"or raise max_csv_files (current limit {max_csv_files})."
        )

    tables: list[ImportedTable] = []
    runs: list[ImportedRun] = []
    used_ids: set[str] = set()
    for json_path in candidate_jsons:
        try:
            candidate_table, candidate_runs, candidate_warnings = _import_candidate_json(
                json_path, source, used_ids, max_bytes=max_csv_bytes
            )
        except ResultImportError as exc:
            if source.is_file():
                raise
            bundle_warnings.append(str(exc))
            continue
        if candidate_table is None:
            if source.is_file():
                raise ResultImportError(
                    f"JSON is not a recognized calibration candidate with per_seed annual outputs: {source}"
                )
            continue
        tables.append(candidate_table)
        runs.extend(candidate_runs)
        bundle_warnings.extend(candidate_warnings)

    for csv_path in candidates:
        try:
            stat = csv_path.stat()
        except OSError as exc:
            bundle_warnings.append(f"Cannot inspect {csv_path}: {exc}")
            continue
        if stat.st_size > max_csv_bytes:
            bundle_warnings.append(
                f"Skipped {csv_path}: {stat.st_size} bytes exceeds the {max_csv_bytes}-byte safety limit."
            )
            continue
        try:
            frame = pd.read_csv(csv_path)
        except Exception as exc:
            bundle_warnings.append(f"Cannot read CSV {csv_path}: {type(exc).__name__}: {exc}")
            continue
        relative = csv_path.name if source.is_file() else csv_path.relative_to(source).as_posix()
        role = "model_timeseries" if "year" in frame and any(c in METRIC_SEMANTICS for c in frame) else "generic_csv"
        table = ImportedTable(csv_path, relative, _sha256_file(csv_path), stat.st_size, frame, role)
        tables.append(table)
        if role != "model_timeseries":
            continue

        annual, catalog, warnings = annualize_model_output(frame)
        manifest_path: Path | None = None
        manifest: dict[str, Any] | None = None
        model = None
        p_value = None
        seed = None
        status = None
        classification = None
        production_eligible = None
        if csv_path in manifest_by_csv:
            manifest_path, manifest, manifest_warnings = manifest_by_csv[csv_path]
            warnings.extend(manifest_warnings)
            route = manifest.get("route", {}) if isinstance(manifest.get("route"), Mapping) else {}
            model = route.get("model")
            p_value = route.get("p")
            seed = route.get("seed")
            status = manifest.get("status")
            classification = manifest.get("classification") or manifest.get("run_class")
            production_eligible = manifest.get("production_eligible")
            scientific = manifest.get("scientific_output")
            expected_hash = scientific.get("sha256") if isinstance(scientific, Mapping) else None
            if expected_hash and expected_hash != table.sha256:
                warnings.append(
                    f"Scientific CSV SHA-256 does not match its manifest ({table.sha256} != {expected_hash})."
                )
        run_id = _unique_run_id(_base_run_id(csv_path, model, p_value, seed), used_ids)
        runs.append(ImportedRun(run_id, table, annual, catalog, manifest_path, manifest,
                                str(model) if model is not None else None,
                                float(p_value) if p_value is not None else None,
                                int(seed) if seed is not None else None, status,
                                str(classification) if classification is not None else None,
                                production_eligible if isinstance(production_eligible, bool) else None,
                                warnings))

    if not tables:
        raise ResultImportError(f"no readable CSV outputs found at {source}")
    annual_long = _annual_long(runs)
    across = _across_run_summary(annual_long)
    return ResultBundle(source, runs, tables, annual_long, across, bundle_warnings)


def _import_candidate_json(path: Path, source_root: Path, used_ids: set[str], *,
                           max_bytes: int) -> tuple[ImportedTable | None, list[ImportedRun], list[str]]:
    """Import a retained calibration candidate containing per-seed annual output."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ResultImportError(f"cannot inspect candidate JSON {path}: {exc}") from exc
    if size > max_bytes:
        raise ResultImportError(f"candidate JSON exceeds the {max_bytes}-byte safety limit: {path}")
    payload = _strict_json(path)
    per_seed = payload.get("per_seed")
    if not isinstance(per_seed, list) or not per_seed:
        return None, [], []
    if not all(isinstance(item, Mapping) and isinstance(item.get("annual_outputs"), Mapping)
               for item in per_seed):
        raise ResultImportError(f"candidate per_seed entries do not all contain annual_outputs: {path}")

    relative = path.name if source_root.is_file() else path.relative_to(source_root).as_posix()
    target_order = payload.get("target_order", [])
    means = payload.get("means", [])
    variances = payload.get("sample_variances", [])
    target_rows: list[dict[str, Any]] = []
    if isinstance(target_order, list):
        for index, target in enumerate(target_order):
            target_rows.append({
                "target_index": index,
                "target": json.dumps(target, sort_keys=True) if isinstance(target, (dict, list)) else target,
                "mean": means[index] if isinstance(means, list) and index < len(means) else np.nan,
                "sample_variance": (
                    variances[index] if isinstance(variances, list) and index < len(variances) else np.nan
                ),
            })
    target_frame = pd.DataFrame(target_rows)
    table = ImportedTable(path, relative, _sha256_file(path), size, target_frame,
                          "calibration_candidate_annual")

    provenance_path = path.parent / "provenance.json"
    provenance: dict[str, Any] = {}
    provenance_warnings: list[str] = []
    if provenance_path.is_file():
        try:
            provenance = _strict_json(provenance_path)
        except ResultImportError as exc:
            provenance_warnings.append(str(exc))
    expected_files = provenance.get("files")
    if isinstance(expected_files, Mapping):
        expected_hash = expected_files.get(path.name)
        actual_hash = _sha256_file(path)
        if isinstance(expected_hash, str) and expected_hash != actual_hash:
            provenance_warnings.append(
                f"Candidate SHA-256 does not match provenance ({actual_hash} != {expected_hash})."
            )
    classification = provenance.get("classification") or payload.get("classification") or "CALIBRATION_CANDIDATE_ARTIFACT"
    scientific_status = provenance.get("scientific_status") or payload.get("status") or "RETAINED"
    compatibility = provenance.get("current_code_compatibility")
    if compatibility:
        provenance_warnings.append(f"Current-code compatibility: {compatibility}")
    lineage = provenance.get("lineage")
    if lineage:
        provenance_warnings.append(f"Recorded lineage: {lineage}")

    candidate_id = payload.get("candidate_id", path.stem)
    route = payload.get("route", {}) if isinstance(payload.get("route"), Mapping) else {}
    model = route.get("model") or payload.get("model")
    p_value = route.get("p")
    runs: list[ImportedRun] = []
    for index, entry in enumerate(per_seed):
        seed = entry.get("seed")
        annual, catalog = _candidate_annual_frame(entry["annual_outputs"], path, index)
        base = f"candidate-{candidate_id}-seed{seed if seed is not None else index + 1}"
        run_id = _unique_run_id(re.sub(r"[^A-Za-z0-9_.-]+", "-", str(base)), used_ids)
        runs.append(ImportedRun(
            run_id=run_id,
            source=table,
            annual=annual,
            metric_catalog=catalog,
            manifest_path=provenance_path if provenance_path.is_file() else None,
            manifest=provenance or None,
            model=str(model) if model is not None else None,
            p=float(p_value) if p_value is not None else None,
            seed=int(seed) if seed is not None else None,
            status=str(scientific_status),
            classification=str(classification),
            production_eligible=(
                provenance.get("production_eligible")
                if isinstance(provenance.get("production_eligible"), bool) else None
            ),
            warnings=list(provenance_warnings),
        ))
    return table, runs, provenance_warnings


def _candidate_annual_frame(outputs: Mapping[str, Any], path: Path,
                            seed_index: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    years: set[int] = set()
    normalized: dict[str, dict[int, float]] = {}
    for metric, year_values in outputs.items():
        if not isinstance(metric, str) or not isinstance(year_values, Mapping):
            raise ResultImportError(
                f"candidate annual_outputs must map metric -> year -> value ({path}, seed index {seed_index})"
            )
        metric_values: dict[int, float] = {}
        for raw_year, raw_value in year_values.items():
            try:
                year = int(raw_year)
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ResultImportError(
                    f"invalid annual value for {metric!r} year {raw_year!r} in {path}"
                ) from exc
            if not math.isfinite(value):
                value = np.nan
            metric_values[year] = value
            years.add(year)
        normalized[metric] = metric_values
    rows = [{"year": year, **{metric: values.get(year, np.nan)
                               for metric, values in normalized.items()}}
            for year in sorted(years)]
    annual = pd.DataFrame(rows)
    catalog_rows = []
    for metric in normalized:
        semantic = METRIC_SEMANTICS.get(metric)
        catalog_rows.append({
            "metric": metric,
            "label": semantic.label if semantic else _label(metric),
            "category": semantic.category if semantic else "reported_annual",
            "unit": semantic.unit if semantic else "unspecified",
            "annual_aggregation": semantic.aggregation if semantic else "reported_annual",
            "included_in_normalized_annual": True,
        })
    return annual, pd.DataFrame(catalog_rows)


def _validate_manifest(manifest: Mapping[str, Any], path: Path) -> list[str]:
    warnings: list[str] = []
    schema = manifest.get("schema")
    if schema not in SUPPORTED_MANIFEST_SCHEMAS:
        warnings.append(f"Unrecognized manifest schema {schema!r}: {path}")
    if manifest.get("status") != "COMPLETE":
        warnings.append(f"Manifest status is {manifest.get('status')!r}, not 'COMPLETE': {path}")
    route = manifest.get("route")
    if not isinstance(route, Mapping) or route.get("model") not in {"M0", "M1"}:
        warnings.append(f"Manifest has no recognized M0/M1 route: {path}")
    return warnings


def _manifest_csv_path(manifest: Mapping[str, Any], manifest_path: Path) -> Path | None:
    scientific = manifest.get("scientific_output")
    if not isinstance(scientific, Mapping):
        return None
    relative = scientific.get("relative_path")
    if not isinstance(relative, str) or not relative.strip():
        return None
    candidate = (manifest_path.parent / relative).resolve()
    try:
        candidate.relative_to(manifest_path.parent.resolve())
    except ValueError:
        return None
    return candidate


def _is_generated_report_file(path: Path) -> bool:
    return path.name in {
        "annual_normalized.csv", "across_run_summary.csv", "metric_catalog.csv",
        "source_inventory.csv",
    } and "workbench_report" in path.parts


def _base_run_id(path: Path, model: Any, p_value: Any, seed: Any) -> str:
    if model is not None and seed is not None:
        p_text = "" if p_value is None else f"-p{p_value:g}"
        return f"{model}{p_text}-seed{seed}"
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-")
    return clean or "run"


def _unique_run_id(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _annual_long(runs: Sequence[ImportedRun]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for run in runs:
        catalog = run.metric_catalog.set_index("metric").to_dict("index") if not run.metric_catalog.empty else {}
        for record in run.annual.to_dict("records"):
            year = int(record.pop("year"))
            for metric, value in record.items():
                meta = catalog.get(metric, {})
                rows.append({"run_id": run.run_id, "route_id": run.route_id,
                             "model": run.model, "p": run.p, "seed": run.seed,
                             "classification": run.classification, "year": year,
                             "metric": metric, "label": meta.get("label", _label(metric)),
                             "value": value, "unit": meta.get("unit", "unspecified"),
                             "annual_aggregation": meta.get("annual_aggregation", "unspecified"),
                             "source_file": run.source.relative_path})
    columns = ["run_id", "route_id", "model", "p", "seed", "classification",
               "year", "metric", "label", "value", "unit", "annual_aggregation", "source_file"]
    return pd.DataFrame(rows, columns=columns)


def _across_run_summary(long: pd.DataFrame) -> pd.DataFrame:
    columns = ["route_id", "model", "p", "year", "metric", "label", "unit",
               "annual_aggregation", "n", "mean", "median", "q05", "q95", "minimum", "maximum"]
    if long.empty:
        return pd.DataFrame(columns=columns)
    group_cols = ["route_id", "year", "metric", "label", "unit", "annual_aggregation"]
    rows: list[dict[str, Any]] = []
    for keys, group in long.groupby(group_cols, dropna=False, sort=True):
        values = pd.to_numeric(group["value"], errors="coerce").dropna()
        first = group.iloc[0]
        rows.append(dict(zip(group_cols, keys)) | {
            "model": first["model"], "p": first["p"], "n": int(values.size),
            "mean": values.mean() if len(values) else np.nan,
            "median": values.median() if len(values) else np.nan,
            "q05": values.quantile(0.05) if len(values) else np.nan,
            "q95": values.quantile(0.95) if len(values) else np.nan,
            "minimum": values.min() if len(values) else np.nan,
            "maximum": values.max() if len(values) else np.nan,
        })
    return pd.DataFrame(rows)[columns]


def build_report(bundle: ResultBundle, output_dir: str | Path, *,
                 figure_metrics: Sequence[str] | None = None) -> ReportArtifacts:
    """Write normalized tables, SVG figures, provenance, and an offline HTML report.

    The destination must be absent or empty; source artifacts are never modified.
    """
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise ResultImportError(f"report destination is not a directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise ResultImportError(f"report directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    figures_dir = destination / "figures"
    figures_dir.mkdir(exist_ok=True)

    annual_path = None
    summary_path = None
    catalog_path = None
    if not bundle.annual_long.empty:
        annual_path = destination / "annual_normalized.csv"
        bundle.annual_long.to_csv(annual_path, index=False)
        summary_path = destination / "across_run_summary.csv"
        bundle.across_run_summary.to_csv(summary_path, index=False)
        catalogs = []
        for run in bundle.runs:
            if not run.metric_catalog.empty:
                part = run.metric_catalog.copy()
                part.insert(0, "run_id", run.run_id)
                catalogs.append(part)
        if catalogs:
            catalog_path = destination / "metric_catalog.csv"
            pd.concat(catalogs, ignore_index=True).drop_duplicates().to_csv(catalog_path, index=False)

    inventory = pd.DataFrame([{
        "relative_path": table.relative_path, "role": table.role,
        "rows": len(table.frame), "columns": len(table.frame.columns),
        "size_bytes": table.size_bytes, "sha256": table.sha256,
    } for table in bundle.tables])
    inventory_path = destination / "source_inventory.csv"
    inventory.to_csv(inventory_path, index=False)

    generated_utc = datetime.now(timezone.utc).isoformat()
    reference_id = _bundle_reference_id(bundle)
    provenance_path = destination / "provenance.json"
    provenance = {
        "schema": "collaborator_results_report/1.1.0",
        "generated_utc": generated_utc,
        "result_reference": reference_id,
        "source_path": str(bundle.source_path),
        "source_files": inventory.to_dict("records"),
        "runs": [{
            "run_id": run.run_id, "model": run.model, "p": run.p, "seed": run.seed,
            "status": run.status, "classification": run.classification,
            "production_eligible": run.production_eligible,
            "manifest_path": str(run.manifest_path) if run.manifest_path else None,
            "manifest_sha256": _sha256_file(run.manifest_path) if run.manifest_path else None,
            "warnings": run.warnings,
        } for run in bundle.runs],
        "warnings": bundle.warnings,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    figures = tuple(_write_figures(bundle, figures_dir, figure_metrics))
    workbook_path = build_workbook(
        bundle,
        destination / "model_results.xlsx",
        reference_id=reference_id,
        generated_utc=generated_utc,
        figure_metrics=figure_metrics,
    )
    html_path = destination / "index.html"
    html_path.write_text(
        _render_html(bundle, figures, reference_id=reference_id, generated_utc=generated_utc),
        encoding="utf-8",
    )
    return ReportArtifacts(
        destination, workbook_path, html_path, annual_path, summary_path,
        catalog_path, inventory_path, provenance_path, figures,
    )


def _write_figures(bundle: ResultBundle, figures_dir: Path,
                   requested: Sequence[str] | None) -> list[Path]:
    if bundle.annual_long.empty:
        return []
    available = set(bundle.annual_long["metric"])
    default_order = ["new_infections", "new_diagnoses", "PLHIV", "prevalence",
                     "aids_deaths", "on_art_stock", "msm_on_oral", "msm_on_lena"]
    metrics = list(requested) if requested is not None else [m for m in default_order if m in available]
    metrics = [m for m in metrics if m in available]
    if not metrics:
        metrics = sorted(available)[:4]

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - normal project requirements include it
        raise ResultImportError("matplotlib is required to generate report figures") from exc

    paths: list[Path] = []
    for metric in metrics:
        data = bundle.annual_long[bundle.annual_long["metric"] == metric].copy()
        if data.empty:
            continue
        fig, ax = plt.subplots(figsize=(8.5, 4.6))
        for route_id, route in data.groupby("route_id", dropna=False, sort=True):
            route_runs = route["run_id"].nunique()
            route_label = _route_label(route.iloc[0])
            if route_runs > 1:
                summary = bundle.across_run_summary
                summary = summary[(summary["metric"] == metric) & (summary["route_id"] == route_id)]
                x = summary["year"].to_numpy(dtype=float)
                median = summary["median"].to_numpy(dtype=float)
                q05 = summary["q05"].to_numpy(dtype=float)
                q95 = summary["q95"].to_numpy(dtype=float)
                ax.fill_between(x, q05, q95, alpha=0.18)
                ax.plot(x, median, linewidth=2, label=f"{route_label} median (n={route_runs})")
            else:
                single = route.sort_values("year")
                ax.plot(single["year"], single["value"], linewidth=2, label=route_label)
        first = data.iloc[0]
        ax.set_title(str(first["label"]))
        ax.set_xlabel("Calendar year")
        ax.set_ylabel(str(first["unit"]))
        ax.grid(alpha=0.2)
        ax.legend(frameon=False)
        fig.tight_layout()
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", metric)
        path = figures_dir / f"{safe}.svg"
        fig.savefig(path, format="svg", metadata={"Date": None})
        plt.close(fig)
        paths.append(path)
    return paths


def _bundle_reference_id(bundle: ResultBundle) -> str:
    """Return a compact content reference derived from imported source identities."""
    identities = [
        {"relative_path": table.relative_path, "sha256": table.sha256}
        for table in sorted(bundle.tables, key=lambda item: item.relative_path)
    ]
    encoded = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"HIVPREP-{sha256(encoded).hexdigest()[:12].upper()}"


def _route_label(row: Mapping[str, Any]) -> str:
    model = row.get("model")
    p_value = row.get("p")
    if model == "M0":
        return "M0 — routine diagnosis"
    if model == "M1":
        if p_value is None or pd.isna(p_value):
            return "M1 — adds AIDS-symptom diagnosis"
        return f"M1 — AIDS-symptom diagnosis p={float(p_value):.0%}"
    return "Model trajectory"


def _render_html(bundle: ResultBundle, figures: Sequence[Path], *,
                 reference_id: str, generated_utc: str) -> str:
    run_rows = []
    for run in bundle.runs:
        warning = "; ".join(run.warnings) if run.warnings else ""
        run_rows.append({
            "run": display_warning(run.run_id),
            "model setup": display_model(run.model),
            "symptom-triggered diagnosis chance": display_probability(run.p, model=run.model),
            "seed": "" if run.seed is None else run.seed,
            "run status": display_status(run.status),
            "notes": display_warning(warning),
        })
    all_warnings = list(dict.fromkeys(
        bundle.warnings + [warning for run in bundle.runs for warning in run.warnings]
    ))
    warning_html = "".join(f"<li>{escape(display_warning(item))}</li>" for item in all_warnings)
    figures_html = "".join(
        f'<figure><img src="figures/{escape(path.name)}" alt="{escape(path.stem)} chart">'
        f'<figcaption>Source: result <code>{escape(reference_id)}</code>; '
        'the figure is calculated from the imported trajectory files listed in '
        '<code>source_inventory.csv</code>.</figcaption></figure>'
        for path in figures
    ) or "<p>No normalized time-series figures were generated.</p>"
    run_table = pd.DataFrame(run_rows).to_html(index=False, escape=True, border=0) if run_rows else "<p>No native model runs recognized.</p>"
    inventory = pd.DataFrame([{"file": t.relative_path, "role": t.role,
                               "rows": len(t.frame), "columns": len(t.frame.columns),
                               "sha256": t.sha256} for t in bundle.tables])
    previews = []
    for table in bundle.tables:
        preview = table.frame.head(50).copy()
        preview = preview.apply(
            lambda column: column.map(
                lambda value: display_warning(value) if isinstance(value, str) else value
            )
        )
        previews.append(
            f"<details><summary>{escape(display_warning(table.relative_path))} — {len(table.frame):,} rows, "
            f"{len(table.frame.columns):,} columns</summary>"
            + preview.to_html(index=False, escape=True, border=0)
            + "</details>"
        )
    history_items = "".join(
        f"<li><strong>{escape(title)}:</strong> {escape(explanation)}</li>"
        for title, explanation in HISTORY_MATCHING_COMPONENTS
    )
    term_table = pd.DataFrame(KEY_TERMS).rename(
        columns={"term": "Term", "meaning": "Meaning"}
    ).to_html(index=False, escape=True, border=0)
    parameter_table = pd.DataFrame(PARAMETER_FAMILIES).rename(columns={
        "family": "Parameter family", "controls": "Examples", "role": "Role",
        "interface treatment": "Interface treatment",
    }).to_html(index=False, escape=True, border=0)
    formula_table = pd.DataFrame(
        [{"Use": label, "Definition": formula} for label, formula in FORMULAS]
    ).to_html(index=False, escape=True, border=0)
    references_html = "".join(
        f'<li><a href="{escape(item["url"])}">{escape(item["label"])}</a></li>'
        for item in REFERENCES
    )
    route_count = len({run.route_id for run in bundle.runs})
    trajectory_word = "trajectory" if len(bundle.runs) == 1 else "trajectories"
    route_word = "setup" if route_count == 1 else "setups"
    if bundle.runs:
        interpretation = (
            f"This report contains {len(bundle.runs)} stochastic {trajectory_word} "
            f"across {route_count} model {route_word}. The charts summarize the imported "
            "model outputs; they do not by themselves establish causal effects or policy value."
        )
        if len(bundle.runs) == 1:
            uncertainty_note = (
                "Only one trajectory is available, so variation across stochastic runs cannot be estimated; "
                "there is no stochastic range and these are not confidence intervals."
            )
        else:
            uncertainty_note = (
                "Where multiple runs share a setup, the line is the median and the shaded area is the "
                "5th–95th percentile range across those runs. This is a stochastic range, not a confidence interval."
            )
    else:
        interpretation = "No native model trajectories were recognized in the imported files."
        uncertainty_note = "No stochastic summary was calculated."
    available_metrics = (
        int(bundle.annual_long["metric"].nunique()) if not bundle.annual_long.empty else 0
    )
    available_years = (
        int(bundle.annual_long["year"].nunique()) if not bundle.annual_long.empty else 0
    )
    summary_cards = (
        '<div class="summary-grid" aria-label="Result summary">'
        f'<div class="metric"><strong>{len(bundle.runs):,}</strong><span>trajectories</span></div>'
        f'<div class="metric"><strong>{route_count:,}</strong><span>model setups</span></div>'
        f'<div class="metric"><strong>{available_metrics:,}</strong><span>outcomes</span></div>'
        f'<div class="metric"><strong>{available_years:,}</strong><span>annual observations</span></div>'
        '</div>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model results</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#17202a}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;display:block;overflow:auto}}th,td{{border-bottom:1px solid #ddd;padding:.45rem;text-align:left;white-space:nowrap}}th{{background:#f5f6f7}}
img{{max-width:100%;height:auto}}figure{{margin:1.5rem 0}}figcaption{{color:#566573;font-size:.88rem;margin-top:.35rem}}details{{margin:1rem 0}}summary{{cursor:pointer;font-weight:600}}
.warning{{background:#fff6dd;border-left:4px solid #c88700;padding:.75rem 1rem}}.note{{background:#eef6ff;border-left:4px solid #2f80ed;padding:.75rem 1rem}}.diagram-note{{color:#566573;font-size:.88rem;margin-top:.2rem}}code{{background:#f3f3f3;padding:.1rem .25rem}}
.summary-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin:1rem 0 1.25rem}}.metric{{border:1px solid #d8dee5;border-top:3px solid #557a95;border-radius:.35rem;padding:.8rem;background:#fbfcfd}}.metric strong{{display:block;font-size:1.45rem}}.metric span{{color:#566573;font-size:.86rem}}
</style></head><body>
<h1>Model results</h1>
<p><strong>Result reference:</strong> <code>{escape(reference_id)}</code></p>
<p>Report generated: {escape(generated_utc)}</p>
<p>Imported {len(bundle.tables)} source file(s); recognized {len(bundle.runs)} annual model trajectory record(s).</p>
{f'<section class="warning"><h2>Import notes</h2><ul>{warning_html}</ul></section>' if warning_html else ''}
<h2>What these results show</h2>
{summary_cards}
<p>{escape(interpretation)}</p>
<section class="note"><strong>How to read the variation:</strong> {escape(uncertainty_note)}</section>
<h2>Figures</h2>{figures_html}
<details open><summary>Included runs</summary>{run_table}</details>
<h2>Model in brief</h2>
<p>{escape(MODEL_INTRO)}</p><p>{escape(MODEL_SCOPE)}</p>
{DISEASE_PATHWAY_SVG}
<p class="diagram-note">Author's schematic of implemented model logic. Arrows show possible transitions, not constant transition probabilities.</p>
<h3>History matching</h3><p>{escape(HISTORY_MATCHING_INTRO)}</p><ul>{history_items}</ul>
<section class="note"><strong>Calibration and validation are separate.</strong> {escape(VALIDATION_NOTE)}</section>
<details open><summary>Key terms</summary>{term_table}</details>
<details><summary>Parameter map</summary>{parameter_table}<p>This is a family-level map. Exact values belong to the effective configuration and its recorded hash.</p>{STATE_SPACE_SVG}<p class="diagram-note">Author's schematic of the implemented population state space.</p></details>
<details><summary>Formulas and method references</summary>{formula_table}<ul>{references_html}</ul></details>
<h2>Analysis workflow</h2>{ANALYSIS_WORKFLOW_SVG}
<p class="diagram-note">Author's schematic of the implemented scenario and economic layers. Scientific interpretation still depends on the selected fitted state and assumptions.</p>
<h2>Downloads and provenance</h2>
<p>The exact source paths, machine status codes, classifications, hashes and run metadata are preserved in <code>source_inventory.csv</code> and <code>provenance.json</code>. The result reference is derived from the relative source filenames and hashes; it is not a scientific publication DOI.</p>
<details><summary>File inventory</summary>{inventory.to_html(index=False, escape=True, border=0)}</details>
<p>Normalized data and metric definitions are stored beside this page.</p>
<details><summary>Source data preview</summary>{''.join(previews)}</details>
</body></html>"""
