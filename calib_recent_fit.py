"""
Recent-fit audit and eligibility checks for calibration candidates.

The recent-fit gate is deliberately asymmetric. It prevents candidates from
winning by under-producing recent observed diagnoses or by collapsing latest
observed incidence, while treating high incidence as an audit warning rather
than an automatic failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


RECENT_FIT_VERSION = "v13.5"


@dataclass(frozen=True)
class RecentFitSettings:
    min_observed_fraction: float = 0.75
    sigma_multiplier: float = 3.0
    diagnosis_year: int | None = None
    infection_output: str = "new_infections"
    diagnosis_outputs: tuple[str, ...] = ("new_diagnoses", "new_diagnoses_msm")
    plhiv_output: str = "PLHIV"


def evaluate_recent_fit(
    model_outputs: Mapping[str, Mapping[int, float]],
    target_records: Iterable[dict],
    settings: RecentFitSettings | None = None,
) -> dict:
    """
    Evaluate broad recent-fit plausibility against observed target records.

    Hard failures are limited to directly observed recent diagnoses and latest
    observed incidence lower-tail collapse. Warnings surface high incidence and
    low PLHIV without turning them into hidden mechanism constraints.
    """
    settings = settings or RecentFitSettings()
    targets = list(target_records or [])
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {
        "version": RECENT_FIT_VERSION,
        "min_observed_fraction": settings.min_observed_fraction,
        "sigma_multiplier": settings.sigma_multiplier,
    }

    for output in settings.diagnosis_outputs:
        target = (
            _target_for_year(targets, output, settings.diagnosis_year)
            if settings.diagnosis_year is not None
            else _latest_target(targets, output)
        )
        if target is None:
            warnings.append(f"{output}:missing_target")
            continue
        diagnosis_year = int(target["year"])
        model_value = _model_value(model_outputs, output, diagnosis_year)
        if model_value is None:
            failures.append(f"{output}:{diagnosis_year}:missing_model_output")
            continue
        observed = float(target["estimate"])
        ratio = model_value / observed if observed else float("inf")
        metrics[f"{output}_gate_year"] = diagnosis_year
        metrics[f"{output}_{diagnosis_year}_model"] = model_value
        metrics[f"{output}_{diagnosis_year}_target"] = observed
        metrics[f"{output}_{diagnosis_year}_observed_fraction"] = ratio
        if ratio < settings.min_observed_fraction:
            failures.append(
                f"{output}:{diagnosis_year}:model_fraction_"
                f"{ratio:.3f}<min_{settings.min_observed_fraction:.3f}"
            )

    infection_target = _latest_target(targets, settings.infection_output)
    if infection_target is None:
        warnings.append(f"{settings.infection_output}:missing_target")
    else:
        year = int(infection_target["year"])
        model_value = _model_value(model_outputs, settings.infection_output, year)
        if model_value is None:
            failures.append(f"{settings.infection_output}:{year}:missing_model_output")
        else:
            z = _z_score(model_value, infection_target)
            lower = float(infection_target["estimate"]) - settings.sigma_multiplier * float(infection_target["sigma"])
            upper = float(infection_target["estimate"]) + settings.sigma_multiplier * float(infection_target["sigma"])
            metrics[f"{settings.infection_output}_{year}_model"] = model_value
            metrics[f"{settings.infection_output}_{year}_target"] = float(infection_target["estimate"])
            metrics[f"{settings.infection_output}_{year}_z"] = z
            if model_value < lower:
                failures.append(
                    f"{settings.infection_output}:{year}:z_{z:.2f}<"
                    f"-{settings.sigma_multiplier:.2f}"
                )
            elif model_value > upper:
                warnings.append(
                    f"{settings.infection_output}:{year}:z_{z:.2f}>"
                    f"{settings.sigma_multiplier:.2f}"
                )

    plhiv_target = _latest_target(targets, settings.plhiv_output)
    if plhiv_target is not None:
        year = int(plhiv_target["year"])
        model_value = _model_value(model_outputs, settings.plhiv_output, year)
        if model_value is not None:
            z = _z_score(model_value, plhiv_target)
            metrics[f"{settings.plhiv_output}_{year}_model"] = model_value
            metrics[f"{settings.plhiv_output}_{year}_target"] = float(plhiv_target["estimate"])
            metrics[f"{settings.plhiv_output}_{year}_z"] = z
            if z < -settings.sigma_multiplier:
                warnings.append(
                    f"{settings.plhiv_output}:{year}:z_{z:.2f}<"
                    f"-{settings.sigma_multiplier:.2f}"
                )

    return {
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
    }


def recent_fit_row_fields(result: dict) -> dict:
    """Flatten an evaluate_recent_fit result into ranked-CSV friendly fields."""
    metrics = result.get("metrics", {})
    fields = {
        "recent_fit_version": metrics.get("version", RECENT_FIT_VERSION),
        "recent_fit_pass": bool(result.get("passed", False)),
        "recent_fit_n_failures": len(result.get("failures", [])),
        "recent_fit_n_warnings": len(result.get("warnings", [])),
        "recent_fit_fail_reasons": ";".join(result.get("failures", [])),
        "recent_fit_warning_reasons": ";".join(result.get("warnings", [])),
    }
    for key, value in metrics.items():
        fields[f"recent_fit_{key}"] = value
    return fields


def _target_for_year(targets: list[dict], output: str, year: int) -> dict | None:
    matches = [
        target for target in targets
        if target.get("model_output") == output and int(target.get("year")) == int(year)
    ]
    return _prefer_primary(matches)


def _latest_target(targets: list[dict], output: str) -> dict | None:
    matches = [target for target in targets if target.get("model_output") == output]
    if not matches:
        return None
    latest_year = max(int(target["year"]) for target in matches)
    return _target_for_year(matches, output, latest_year)


def _prefer_primary(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    for target in matches:
        if target.get("calibration_use") == "primary":
            return target
    return matches[0]


def _model_value(
    model_outputs: Mapping[str, Mapping[int, float]],
    output: str,
    year: int,
) -> float | None:
    series = model_outputs.get(output, {})
    if year not in series:
        return None
    return float(series[year])


def _z_score(model_value: float, target: dict) -> float:
    sigma = max(float(target["sigma"]), 1e-9)
    return (float(model_value) - float(target["estimate"])) / sigma
