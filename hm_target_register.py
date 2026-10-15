"""
History matching -- the target register (HM-1 / task B-4).

Loads ``configs/hm_target_register.json``, reads the disease workbook, and
resolves, for every (series, year) scalar target:

  * ``z``        -- the observed or estimated target value
  * ``v_obs``    -- observation variance, as a provenance-carrying BudgetTerm
  * ``tier``     -- 1 (observed count) or 2 (another model's output)
  * ``role``     -- calibration / calibration_restricted / cross_validation /
                    excluded_pending_c1

``V_disc`` is NOT resolved here. It is assembled later by ``build_budgets``
under the register's explicit, versioned method. Some historical registers use
simulator-output variance; the separate robust-NROY production v1.3 proposal
uses a target-relative 10% project assumption. The latter is not attributed to
Andrianakis et al. as an exact published rule.

Everything this module reports about a target is traceable to a workbook cell or
to a declared rule written down in the register JSON.  Nothing is inferred
silently: where a quantity has no source, the resulting term carries status
``"underivable"`` and appears in ``escalations()``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import hm_variance_budget as vb


DEFAULT_REGISTER_PATH = Path(__file__).resolve().parent / "configs" / "hm_target_register.json"

CALIBRATION_ROLES = {"calibration", "calibration_restricted"}
CROSS_VALIDATION_ROLES = {"cross_validation"}
EXCLUDED_ROLES = {"excluded_pending_c1", "excluded"}
VALID_ROLES = CALIBRATION_ROLES | CROSS_VALIDATION_ROLES | EXCLUDED_ROLES


class RegisterError(ValueError):
    """Raised when the register is internally inconsistent or contradicts the workbook."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise RegisterError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_constant(token: str) -> None:
    raise RegisterError(f"non-finite JSON token {token!r} is not allowed")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise RegisterError(f"JSON number {token!r} overflows to a non-finite value")
    return value


def _strict_json_load(path: Path, *, label: str) -> object:
    """Load authority JSON without duplicate or non-finite-number ambiguity."""
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
        )
    except RegisterError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegisterError(f"{label} is not valid strict JSON: {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ScalarTarget:
    """One (series, year) target: the atomic unit the implausibility acts on."""

    key: str
    series_name: str
    model_output: str
    year: int
    z: float
    v_obs: vb.BudgetTerm
    tier: int
    role: str
    source_sheet: str
    source_organisation: str
    z_derivation: str

    @property
    def is_calibration(self) -> bool:
        return self.role in CALIBRATION_ROLES

    @property
    def is_cross_validation(self) -> bool:
        return self.role in CROSS_VALIDATION_ROLES

    @property
    def particle_column(self) -> str:
        """Column name this target maps to in a particle CSV (``<model_output>_<year>``)."""
        return f"{self.model_output}_{self.year}"


@dataclass
class TargetRegister:
    """The resolved register: metadata plus every scalar target."""

    meta: dict
    targets: list[ScalarTarget] = field(default_factory=list)

    # -- selection ---------------------------------------------------------
    def by_role(self, roles: Iterable[str]) -> list[ScalarTarget]:
        wanted = set(roles)
        return [target for target in self.targets if target.role in wanted]

    def calibration_targets(self) -> list[ScalarTarget]:
        return self.by_role(CALIBRATION_ROLES)

    def cross_validation_targets(self) -> list[ScalarTarget]:
        return self.by_role(CROSS_VALIDATION_ROLES)

    def series_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for target in self.targets:
            seen.setdefault(target.series_name, None)
        return list(seen)

    def restrict_years(
        self,
        targets: Sequence[ScalarTarget],
        years: Iterable[int] | Mapping[str, Iterable[int]] | None,
    ) -> list[ScalarTarget]:
        """Scoring granularity: all years, global anchors, or per-series anchors.

        Per-year scoring makes every (series, year) pair a separate output, which
        under a maximum rule is a far more stringent test than the eight-to-
        eighteen summary outputs used in the reference application.  Anchor-year
        scoring reduces that.  This is a scientific decision, so the register does
        not pick one: the caller declares it and the diagnostics report both.
        """
        if years is None:
            return list(targets)
        if isinstance(years, Mapping):
            wanted = {
                str(series): {int(year) for year in series_years}
                for series, series_years in years.items()
            }
            return [
                target for target in targets
                if target.series_name in wanted and target.year in wanted[target.series_name]
            ]
        wanted = {int(year) for year in years}
        return [target for target in targets if target.year in wanted]

    # -- reporting ---------------------------------------------------------
    def as_table(self) -> pd.DataFrame:
        """The section-4 register table, one row per scalar target."""
        return pd.DataFrame(
            [
                {
                    "target_key": target.key,
                    "series_name": target.series_name,
                    "model_output": target.model_output,
                    "year": target.year,
                    "epistemic_tier": target.tier,
                    "role": target.role,
                    "z": target.z,
                    "v_obs_variance": target.v_obs.variance,
                    "v_obs_sigma": target.v_obs.sigma,
                    "v_obs_status": target.v_obs.status,
                    "v_obs_derivation": target.v_obs.derivation,
                    "v_obs_bias_direction": target.v_obs.direction,
                    "source_sheet": target.source_sheet,
                    "source_organisation": target.source_organisation,
                    "z_derivation": target.z_derivation,
                }
                for target in self.targets
            ]
        )

    def series_summary(self) -> pd.DataFrame:
        """One row per series -- the human-readable register of spec section 4."""
        table = self.as_table()
        if table.empty:
            return table
        grouped = (
            table.groupby(["series_name", "model_output", "epistemic_tier", "role", "source_sheet", "source_organisation"], dropna=False)
            .agg(
                n_years=("year", "size"),
                first_year=("year", "min"),
                last_year=("year", "max"),
                v_obs_sigma_median=("v_obs_sigma", "median"),
                v_obs_statuses=("v_obs_status", lambda values: ";".join(sorted(set(values)))),
            )
            .reset_index()
        )
        return grouped

    def escalations(self) -> list[str]:
        """Targets whose V_obs could not be derived -- spec section 10, third bullet."""
        messages: list[str] = []
        seen: set[str] = set()
        for target in self.targets:
            if target.v_obs.status != "underivable" or target.series_name in seen:
                continue
            seen.add(target.series_name)
            messages.append(
                f"[ESCALATION] {target.series_name} (tier {target.tier}, role {target.role}): "
                f"V_obs is underivable. {target.v_obs.derivation} "
                f"Bias direction: {target.v_obs.direction}."
            )
        return messages


# ==========================================================================
# Workbook reading
# ==========================================================================

def _read_sheet(workbook_path: Path, sheet_name: str, cache: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if sheet_name not in cache:
        try:
            cache[sheet_name] = pd.read_excel(workbook_path, sheet_name=sheet_name, header=1)
        except ValueError as exc:  # pragma: no cover - depends on workbook
            raise RegisterError(f"Workbook missing sheet {sheet_name!r}") from exc
    return cache[sheet_name]


def _to_float(value) -> float | None:
    """Parse a workbook cell, treating UNAIDS's literal '(NULL)' as missing."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", ".", "..", "...", "NA", "N/A", "nan", "(NULL)", "NULL"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if np.isfinite(parsed) else None


def _to_year(value) -> int | None:
    """Parse a year cell.  '<=1995', 'Total' and blanks are not years."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    if text.startswith("<=") or not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _column(row: pd.Series, name: str | None) -> float | None:
    if not name:
        return None
    if name not in row.index:
        raise RegisterError(f"Workbook row missing column {name!r}")
    return _to_float(row.get(name))


def _combine(row: pd.Series, base: str | None, add: str | None, subtract: str | None) -> float | None:
    """base (+ add) (- subtract), returning None if any required part is missing."""
    value = _column(row, base)
    if value is None:
        return None
    if add:
        addend = _column(row, add)
        if addend is None:
            return None
        value += addend
    if subtract:
        subtrahend = _column(row, subtract)
        if subtrahend is None:
            return None
        value -= subtrahend
    return value


def _apply_filters(frame: pd.DataFrame, filters: Mapping[str, str] | None, series_name: str) -> pd.DataFrame:
    work = frame
    for column, expected in (filters or {}).items():
        if column not in work.columns:
            raise RegisterError(f"{series_name}: filter column {column!r} missing from sheet")
        work = work[work[column].astype(str).str.strip() == str(expected).strip()]
    return work


# ==========================================================================
# V_obs resolution
# ==========================================================================

def _resolve_v_obs(spec: dict, row: pd.Series, defaults: dict, series_name: str, year: int) -> vb.BudgetTerm:
    v_obs_spec = spec.get("v_obs", {})
    method = v_obs_spec.get("method")

    if method == "published_interval":
        divisor = float(v_obs_spec.get("divisor", vb.NORMAL_95_INTERVAL_DIVISOR))
        low = _combine(row, v_obs_spec.get("lower_column") or spec.get("lower_column"),
                       spec.get("lower_add_column"), spec.get("lower_subtract_column"))
        high = _combine(row, v_obs_spec.get("upper_column") or spec.get("upper_column"),
                        spec.get("upper_add_column"), spec.get("upper_subtract_column"))
        if low is None or high is None:
            return vb.v_obs_underivable(
                f"{series_name} {year}: published interval columns are empty for this year"
            )
        term = vb.v_obs_from_interval(low, high, divisor=divisor, source=f"{series_name} {year}")
        caveat = v_obs_spec.get("caveat")
        if caveat:
            term = vb.BudgetTerm(
                name=term.name,
                variance=term.variance,
                status=term.status,
                derivation=f"{term.derivation}. CAVEAT: {caveat}",
                direction=term.direction,
            )
        return term

    if method == "delay_correction":
        reported = _combine(row, v_obs_spec.get("reported_column"),
                            v_obs_spec.get("reported_add_column"), v_obs_spec.get("reported_subtract_column"))
        adjusted = _combine(row, v_obs_spec.get("adjusted_column"),
                            v_obs_spec.get("adjusted_add_column"), v_obs_spec.get("adjusted_subtract_column"))
        fraction = float(defaults.get(v_obs_spec.get("fraction_key", "shm_delay_uncertainty_fraction"), 1.0))
        rule = str(defaults.get(v_obs_spec.get("extrapolation_rule_key", "shm_delay_extrapolation_rule"), ""))
        term = vb.v_obs_from_delay_correction(
            reported,
            adjusted,
            fraction=fraction,
            extrapolated=adjusted is None and bool(rule),
            extrapolation_rule=rule,
        )
        missing = v_obs_spec.get("missing_component")
        if missing and missing.get("status") == "underivable":
            term = vb.BudgetTerm(
                name=term.name,
                variance=term.variance,
                status="underivable",
                derivation=(
                    f"{term.derivation}. INCOMPLETE: the "
                    f"{missing.get('name', 'missing')} component contributes 0.0 -- "
                    f"{missing.get('reason', 'no source')}"
                ),
                direction="over_exclusion",
            )
        return term

    if method == "underivable":
        return vb.v_obs_underivable(f"{series_name}: {v_obs_spec.get('reason', 'no reason recorded')}")

    if method == "explicit_sigma":
        return vb.v_obs_explicit(
            float(v_obs_spec["sigma"]),
            derivation=str(v_obs_spec.get("derivation", "explicit sigma supplied by the register")),
        )

    raise RegisterError(f"{series_name}: unknown v_obs method {method!r}")


# ==========================================================================
# Non-workbook source: audited RIVM AIDS evidence candidate (2026-09-16)
# ==========================================================================

RIVM_CANDIDATE_SOURCE_TYPE = "rivm_aids_candidate_json"
RIVM_CANDIDATE_GROUPS = {"msm", "non_msm", "total"}


def _rivm_candidate_targets(spec: dict, defaults: dict, model_dir: Path) -> list[ScalarTarget]:
    """Resolve targets from ``candidate_newest_vetted_*.json``.

    The candidate is itself the audited output of ``tools/build_rivm_aids_candidate.py``
    under the user-approved newest-vetted-edition-per-year policy.  Its SHA-256 is
    pinned in the register so a rebuilt candidate cannot silently change z.
    ``non_msm`` is total-of-routes minus MSM -- an exact identity in the source
    table, not an estimate.  Records carrying a percentage-consistency flag,
    an incompleteness flag or a pooled period are refused, never repaired.
    """
    series_name = spec["series_name"]
    path = (model_dir / spec["candidate_path"]).resolve()
    if not path.is_file():
        raise RegisterError(f"{series_name}: candidate file not found: {path}")
    digest = _sha256(path)
    expected = spec.get("candidate_sha256")
    if not expected or digest != expected:
        raise RegisterError(
            f"{series_name}: candidate SHA-256 {digest} does not match the pinned {expected}"
        )
    group = spec.get("group")
    if group not in RIVM_CANDIDATE_GROUPS:
        raise RegisterError(f"{series_name}: group must be one of {sorted(RIVM_CANDIDATE_GROUPS)}")
    candidate = _strict_json_load(path, label=f"{series_name} candidate")
    if not isinstance(candidate, dict):
        raise RegisterError(f"{series_name}: candidate must be a JSON object")
    first_year, last_year = (int(value) for value in spec["year_coverage"])
    restricted = spec.get("restricted_years")
    scored_first, scored_last = (int(v) for v in restricted) if restricted else (first_year, last_year)
    targets: list[ScalarTarget] = []
    seen: set[int] = set()
    for record in candidate["selected"]:
        period = str(record["period"]).strip()
        if not period.isdigit():
            raise RegisterError(f"{series_name}: non-annual period {period!r} in selected records")
        year = int(period)
        if not (scored_first <= year <= scored_last):
            continue
        if record.get("incomplete") or record.get("pooled_or_total") or record.get("percentage_consistency_flags"):
            raise RegisterError(f"{series_name}: flagged record for {year} reached the selected set")
        if year in seen:
            raise RegisterError(f"{series_name}: duplicate selected record for {year}")
        seen.add(year)
        counts = record["counts"]
        if "msm" not in counts:
            raise RegisterError(f"{series_name}: {year} record has no MSM route count")
        total = float(record["total_derived_from_groups"])
        if abs(total - sum(float(v) for v in counts.values())) > 1e-9:
            raise RegisterError(f"{series_name}: {year} route counts do not sum to the recorded total")
        z = {"msm": float(counts["msm"]), "total": total, "non_msm": total - float(counts["msm"])}[group]
        targets.append(
            ScalarTarget(
                key=f"{series_name}@{year}",
                series_name=series_name,
                model_output=spec["model_output"],
                year=year,
                z=z,
                v_obs=_resolve_v_obs(spec, pd.Series(dtype=object), defaults, series_name, year),
                tier=int(spec["epistemic_tier"]),
                role=spec.get("role", "calibration"),
                source_sheet=f"{spec['candidate_path']}#{record['source_id']}",
                source_organisation=spec.get("source_organisation", ""),
                z_derivation=(
                    f"{spec.get('z_derivation', '')} [{year}: {record['source_id']} "
                    f"{record['source_file']} PDF pp.{record['pdf_pages']}, group={group}]"
                ),
            )
        )
    if not targets:
        raise RegisterError(f"{series_name}: candidate resolved zero years over {scored_first}-{scored_last}")
    return targets


# ==========================================================================
# Loading
# ==========================================================================

def load_register(
    register_path: str | Path | None = None,
    *,
    workbook_path: str | Path | None = None,
    overrides: Mapping[str, object] | None = None,
) -> TargetRegister:
    """Read the register JSON and resolve every scalar target against the workbook.

    ``overrides`` replaces entries in the register's ``defaults`` block -- used by
    the sensitivity suite to sweep a declared multiplier without editing the file.
    """
    register_path = Path(register_path) if register_path else DEFAULT_REGISTER_PATH
    register = _strict_json_load(register_path, label="target register")
    if not isinstance(register, dict):
        raise RegisterError(f"Target register must be a JSON object: {register_path}")

    defaults = dict(register.get("defaults", {}))
    defaults.update(dict(overrides or {}))

    if workbook_path is None:
        # source_workbook is recorded relative to the model directory (the repo root
        # for this package), matching calib_target_loader.default_disease_workbook_path.
        workbook_path = (Path(__file__).resolve().parent / register["source_workbook"]).resolve()
    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise RegisterError(f"Disease workbook not found: {workbook_path}")
    expected_workbook_sha256 = register.get("source_workbook_sha256")
    if expected_workbook_sha256 is not None:
        expected_workbook_sha256 = str(expected_workbook_sha256)
        if (
            len(expected_workbook_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_workbook_sha256)
        ):
            raise RegisterError(
                "source_workbook_sha256 must be an exact lowercase SHA-256 digest"
            )
        actual_workbook_sha256 = _sha256(workbook_path)
        if actual_workbook_sha256 != expected_workbook_sha256:
            raise RegisterError(
                f"Disease workbook SHA-256 {actual_workbook_sha256} does not match "
                f"the register-bound {expected_workbook_sha256}"
            )

    cache: dict[str, pd.DataFrame] = {}
    targets: list[ScalarTarget] = []

    for spec in register["targets"]:
        series_name = spec["series_name"]
        role = spec.get("role", "calibration")
        if role not in VALID_ROLES:
            raise RegisterError(f"{series_name}: unknown role {role!r}; expected one of {sorted(VALID_ROLES)}")
        tier = int(spec["epistemic_tier"])
        if tier not in (1, 2):
            raise RegisterError(f"{series_name}: epistemic_tier must be 1 or 2, got {tier}")
        if spec.get("source_type") == RIVM_CANDIDATE_SOURCE_TYPE:
            targets.extend(_rivm_candidate_targets(spec, defaults, Path(__file__).resolve().parent))
            continue

        frame = _apply_filters(_read_sheet(workbook_path, spec["source_sheet"], cache), spec.get("filters"), series_name)
        first_year, last_year = (int(value) for value in spec["year_coverage"])
        restricted = spec.get("restricted_years")
        if restricted:
            scored_first, scored_last = (int(value) for value in restricted)
        else:
            scored_first, scored_last = first_year, last_year

        year_column = spec["year_column"]
        if year_column not in frame.columns:
            raise RegisterError(f"{series_name}: year column {year_column!r} missing from sheet")

        n_rows_used = 0
        for _, row in frame.iterrows():
            year = _to_year(row.get(year_column))
            if year is None or not (scored_first <= year <= scored_last):
                continue
            z = _combine(row, spec["estimate_column"], spec.get("estimate_add_column"), spec.get("estimate_subtract_column"))
            if z is None:
                continue
            v_obs = _resolve_v_obs(spec, row, defaults, series_name, year)
            targets.append(
                ScalarTarget(
                    key=f"{series_name}@{year}",
                    series_name=series_name,
                    model_output=spec["model_output"],
                    year=year,
                    z=float(z),
                    v_obs=v_obs,
                    tier=tier,
                    role=role,
                    source_sheet=spec["source_sheet"],
                    source_organisation=spec.get("source_organisation", ""),
                    z_derivation=spec.get("z_derivation", ""),
                )
            )
            n_rows_used += 1

        if n_rows_used == 0:
            raise RegisterError(
                f"{series_name}: resolved zero years from sheet {spec['source_sheet']!r} "
                f"over {scored_first}-{scored_last}. The register and the workbook disagree."
            )

    if not targets:
        raise RegisterError(f"Register {register_path} resolved no targets at all")

    return TargetRegister(meta={k: v for k, v in register.items() if k != "targets"} | {"defaults_effective": defaults}, targets=targets)


# ==========================================================================
# Mapping targets onto a matrix of simulator outputs
# ==========================================================================

#: Model outputs that a particle CSV does not carry directly but that are exactly
#: reconstructable from columns it does carry.  Only exact identities belong here
#: -- nothing approximate, nothing imputed.
DERIVED_OUTPUTS: dict[str, tuple[str, str]] = {
    # The workbook's non-MSM diagnosis series is 'everyone who is not MSM+trans',
    # which is exactly total minus MSM in the model's own accounting.  The
    # reachability audit computed it the same way and labelled it "(derived)".
    "new_diagnoses_non_msm": ("new_diagnoses", "new_diagnoses_msm"),
}


def resolve_simulated(
    targets: Sequence[ScalarTarget],
    frame: pd.DataFrame,
) -> tuple[dict[str, np.ndarray], list[ScalarTarget], list[dict]]:
    """Extract simulator outputs for each target from a particle/wave table.

    Returns ``(simulated, resolved_targets, unresolved)``.  A target whose column
    is absent is REPORTED in ``unresolved`` and dropped -- never silently filled,
    and never quietly treated as a pass.  Dropping a target weakens the test, so
    the caller must report the list.
    """
    simulated: dict[str, np.ndarray] = {}
    resolved: list[ScalarTarget] = []
    unresolved: list[dict] = []
    for target in targets:
        output = target.model_output
        direct = f"{output}_{target.year}"
        if direct in frame.columns:
            values = pd.to_numeric(frame[direct], errors="coerce").to_numpy(dtype=float)
        elif output in DERIVED_OUTPUTS:
            minuend, subtrahend = DERIVED_OUTPUTS[output]
            left, right = f"{minuend}_{target.year}", f"{subtrahend}_{target.year}"
            if left in frame.columns and right in frame.columns:
                values = (
                    pd.to_numeric(frame[left], errors="coerce").to_numpy(dtype=float)
                    - pd.to_numeric(frame[right], errors="coerce").to_numpy(dtype=float)
                )
            else:
                unresolved.append({"target_key": target.key, "reason": f"missing {left} and/or {right}"})
                continue
        else:
            unresolved.append({"target_key": target.key, "reason": f"column {direct} not present"})
            continue
        simulated[target.key] = values
        resolved.append(target)
    return simulated, resolved, unresolved


# ==========================================================================
# Budget assembly (needs an ensemble, so it lives here rather than in the register)
# ==========================================================================

def build_budgets(
    targets: Sequence[ScalarTarget],
    simulated: Mapping[str, np.ndarray],
    *,
    defaults: Mapping[str, object],
    v_ensemble: Mapping[str, vb.BudgetTerm] | None = None,
    disc_multiplier: float | None = None,
    disc_method: str | None = None,
) -> dict[str, vb.VarianceBudget]:
    """Assemble the full four-term budget for each target, given a scored ensemble.

    ``simulated`` maps ``target.key`` to that target's simulator outputs across the
    ensemble. ``V_disc`` follows the register's declared, versioned method. The
    focused v1.3 register uses a target-relative 10% project assumption; it must
    not be attributed to Andrianakis et al. as an exact published rule. Historical
    registers can still explicitly request the output-variance method.
    ``v_ensemble`` supplies a per-target seed-variability term where one exists;
    targets without one get an explicit "unavailable" term that contributes 0.0
    and is reported.
    """
    disc_defaults = dict(defaults.get("v_disc", {}) or {})
    method = str(disc_method or disc_defaults.get("method", "fraction_of_output_variance"))
    multiplier = float(disc_multiplier if disc_multiplier is not None else disc_defaults.get("multiplier", 0.10))
    disc_source = str(disc_defaults.get("source", "Andrianakis et al. 2015"))
    ens_defaults = dict(defaults.get("v_ensemble", {}) or {})
    ens_reason = str(ens_defaults.get("reason", "no seed-variability estimate available"))
    tier2_multiplier = float(defaults.get("tier2_disc_increment_multiplier", 0.0))
    tier2_rationale = str(defaults.get("tier2_disc_increment_rationale", ""))

    budgets: dict[str, vb.VarianceBudget] = {}
    for target in targets:
        values = simulated.get(target.key)
        if values is None:
            raise RegisterError(f"No simulator outputs supplied for target {target.key}")
        if method == "fraction_of_output_variance":
            disc = vb.v_disc_fraction_of_output_variance(values, multiplier=multiplier, source=disc_source)
        elif method == "relative_to_target":
            disc = vb.v_disc_relative_to_target(
                target.z,
                fraction=multiplier,
                rationale=str(disc_defaults.get("rationale", "declared target-relative sensitivity")),
            )
        else:
            raise RegisterError(
                f"Unknown V_disc method {method!r}; expected "
                "'fraction_of_output_variance' or 'relative_to_target'"
            )
        ensemble_term = (v_ensemble or {}).get(target.key) or vb.v_ensemble_unavailable(ens_reason)
        extra: list[vb.BudgetTerm] = []
        if target.tier == 2 and tier2_multiplier > 0.0:
            extra.append(
                vb.v_disc_tier2_increment(
                    disc.variance,
                    increment_multiplier=tier2_multiplier,
                    rationale=tier2_rationale,
                )
            )
        budgets[target.key] = vb.assemble(
            target.key,
            v_obs=target.v_obs,
            v_ensemble=ensemble_term,
            v_disc=disc,
            extra_terms=extra,
        )
    return budgets
