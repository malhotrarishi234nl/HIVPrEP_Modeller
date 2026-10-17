"""Parameter evidence register for reviewers.

Walks a model or costing configuration and returns one row per parameter that
carries a ``value`` together with whatever evidence the config records: unit,
tier, bounds, distribution, source and notes.  A 95% interval is derived from
the declared distribution, or from the recorded bounds when no distribution
is given.  Nothing here is invented: a missing source or distribution is
reported as missing, which is itself useful review information.

This module has no Streamlit dependency so it can be tested directly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping

import pandas as pd

try:  # scipy ships with the operator requirements; degrade gracefully anyway.
    from scipy import stats as _stats
except Exception:  # pragma: no cover - exercised only without scipy
    _stats = None

MODEL_CONFIG = (
    "configs/V13.22_1980_recalibration_candidate_stochastic_m0_m1_20261015.json"
)
COSTING_CONFIG = "configs/hta_costing_config.json"

# Sections of the register, in the order a reviewer reads them.
_COSTING_SECTIONS = {
    "prevention_costs": "Prevention costs",
    "disease_costs": "HIV care costs",
    "utilities": "Health-state utilities",
    "utility_decrements": "Health-state utilities",
    "adverse_events": "Adverse events",
    "perspective": "Patient time and productivity",
    "discounting": "Analysis settings",
    "wtp_threshold_eur_per_qaly": "Analysis settings",
}
_MODEL_SECTIONS = (
    ("population", "Population and epidemic start"),
    ("demography", "Demography"),
    ("transmission_model.mixing", "Mixing"),
    ("transmission_model.disease.transmission", "Transmission"),
    ("transmission_model.disease", "Disease progression and treatment"),
    ("transmission_model", "Transmission model (other)"),
    ("testing", "Diagnosis and testing"),
    ("diagnosis", "Diagnosis and testing"),
    ("prep", "PrEP"),
)

COLUMNS = [
    "Area", "Parameter", "Value", "Unit", "Distribution", "95% interval",
    "Interval basis", "Evidence status", "Source", "Notes", "Config path",
]


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, Mapping[str, Any]]]:
    if isinstance(node, dict):
        if "value" in node and not isinstance(node["value"], (dict, list)):
            yield path, node
        for key, child in node.items():
            if key == "value":
                continue
            yield from _walk(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            tag = child.get("id") if isinstance(child, dict) else None
            yield from _walk(child, f"{path}.{tag or index}")


def _interval_from_distribution(dist: Mapping[str, Any]) -> tuple[float, float] | None:
    kind = str(dist.get("type", "")).lower()
    try:
        if kind == "beta" and "alpha" in dist and "beta" in dist and _stats:
            a, b = float(dist["alpha"]), float(dist["beta"])
            if a <= 0 or b <= 0:
                return None
            return float(_stats.beta.ppf(0.025, a, b)), float(_stats.beta.ppf(0.975, a, b))
        if kind == "gamma" and "mean" in dist and "se" in dist and _stats:
            mean, se = float(dist["mean"]), float(dist["se"])
            if mean > 0 and se > 0:
                shape, scale = (mean / se) ** 2, se**2 / mean
                return (
                    float(_stats.gamma.ppf(0.025, shape, scale=scale)),
                    float(_stats.gamma.ppf(0.975, shape, scale=scale)),
                )
        if kind == "lognormal" and "mu" in dist and "sigma" in dist:
            mu, sigma = float(dist["mu"]), float(dist["sigma"])
            return math.exp(mu - 1.959964 * sigma), math.exp(mu + 1.959964 * sigma)
    except (TypeError, ValueError):
        return None
    return None


def _describe_distribution(dist: Any) -> str:
    if not isinstance(dist, Mapping) or not dist:
        return "Not recorded"
    kind = str(dist.get("type", "")).lower()
    if kind == "fixed":
        return "Fixed"
    if kind == "beta" and "alpha" in dist:
        if float(dist["alpha"]) <= 0 or float(dist["beta"]) <= 0:
            return "Beta (placeholder, not parameterised)"
        return f"Beta({dist['alpha']:g}, {dist['beta']:g})"
    if kind == "gamma" and "mean" in dist:
        return f"Gamma(mean {dist['mean']:g}, SE {dist['se']:g})"
    if kind == "lognormal" and "mu" in dist:
        return f"Lognormal(μ {dist['mu']:g}, σ {dist['sigma']:g})"
    if kind == "linked":
        return "Linked to another input"
    return str(dist.get("type", "Not recorded"))


def _interval(node: Mapping[str, Any]) -> tuple[str, str]:
    dist = node.get("distribution")
    if isinstance(dist, Mapping):
        derived = _interval_from_distribution(dist)
        if derived and all(math.isfinite(x) for x in derived):
            return f"{derived[0]:.4g} – {derived[1]:.4g}", "From distribution"
    bounds = node.get("bounds")
    if isinstance(bounds, list) and len(bounds) == 2:
        return f"{bounds[0]:.4g} – {bounds[1]:.4g}", "Recorded bounds"
    return "—", "None recorded"


def _status(node: Mapping[str, Any]) -> str:
    raw = node.get("distribution_status") or node.get("tier") or ""
    return str(raw).replace("_", " ").capitalize() if raw else "Not recorded"


def _label(path: str, node: Mapping[str, Any]) -> str:
    explicit = node.get("label")
    if explicit:
        return str(explicit)
    parts = [p for p in path.split(".") if p not in {"value", "advanced"}]
    return " › ".join(p.replace("_", " ") for p in parts[-3:])


def _area_for_model(path: str) -> str:
    for prefix, label in _MODEL_SECTIONS:
        if path.startswith(prefix):
            return label
    return "Other model inputs"


def _scalar(value: Any) -> float | str:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def _rows(config: Mapping[str, Any], area_of, origin: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, node in _walk(config):
        if path.startswith(("meta", "run_control", "output")):
            continue
        value = node["value"]
        if isinstance(value, (bool, str)) and not node.get("source"):
            continue  # switches and labels are not evidence-bearing parameters
        interval, basis = _interval(node)
        rows.append({
            "Area": area_of(path),
            "Parameter": _label(path, node),
            "Value": _scalar(value),
            "Unit": str(node.get("unit", "")),
            "Distribution": _describe_distribution(node.get("distribution")),
            "95% interval": interval,
            "Interval basis": basis,
            "Evidence status": _status(node),
            "Source": str(node.get("source", "") or "Not recorded"),
            "Notes": str(node.get("notes", "")),
            "Config path": f"{origin}::{path}",
            "_dist": node.get("distribution") if isinstance(node.get("distribution"), Mapping) else None,
        })
    return rows


def build_register(root: Path | str) -> pd.DataFrame:
    """Return the combined model + economic parameter register."""

    root = Path(root)
    frames: list[dict[str, Any]] = []
    model_path = root / MODEL_CONFIG
    if model_path.is_file():
        model = json.loads(model_path.read_text(encoding="utf-8"))
        frames += _rows(model, _area_for_model, "model")
    cost_path = root / COSTING_CONFIG
    if cost_path.is_file():
        costing = json.loads(cost_path.read_text(encoding="utf-8"))

        def area(path: str) -> str:
            return _COSTING_SECTIONS.get(path.split(".")[0], "Economic inputs (other)")

        frames += _rows(costing, area, "costing")
    frame = pd.DataFrame(frames, columns=[*COLUMNS, "_dist"])
    if frame.empty:
        return frame
    frame["Value"] = frame["Value"].astype(str)
    return frame


def evidence_summary(frame: pd.DataFrame) -> dict[str, int]:
    """Counts a reviewer wants before reading rows."""

    if frame.empty:
        return {"parameters": 0, "with_source": 0, "with_interval": 0, "with_distribution": 0}
    return {
        "parameters": len(frame),
        "with_source": int((frame["Source"] != "Not recorded").sum()),
        "with_interval": int((frame["Interval basis"] != "None recorded").sum()),
        "with_distribution": int(
            (~frame["Distribution"].isin(["Not recorded", "Fixed"])).sum()
        ),
    }


def cost_matrix(root: Path | str) -> pd.DataFrame:
    """Unit costs by item and arm, as a reviewer-readable matrix."""

    path = Path(root) / COSTING_CONFIG
    if not path.is_file():
        return pd.DataFrame()
    costing = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in costing.get("prevention_costs", {}).get("shared", []):
        rows.append({
            "Group": "Prevention (shared)", "Item": item.get("label", item.get("id")),
            "Applies to": ", ".join(item.get("applies_to", [])) or "all arms",
            "Frequency": item.get("frequency", ""), "Value (EUR)": item.get("value"),
            "Source": item.get("source", ""),
        })
    for arm, entries in costing.get("prevention_costs", {}).get("arm_specific", {}).items():
        for key, entry in entries.items():
            if not isinstance(entry, Mapping) or "value" not in entry:
                continue
            rows.append({
                "Group": "Prevention (arm-specific)", "Item": entry.get("label", f"{arm}: {key}"),
                "Applies to": arm.replace("_", " "), "Frequency": entry.get("frequency", ""),
                "Value (EUR)": entry.get("value"), "Source": entry.get("source", ""),
            })
    for key, entry in costing.get("disease_costs", {}).items():
        if isinstance(entry, Mapping) and "value" in entry:
            rows.append({
                "Group": "HIV care", "Item": entry.get("label", key.replace("_", " ")),
                "Applies to": ", ".join(entry.get("applies_to_stages", [])) or "people with HIV",
                "Frequency": entry.get("frequency", ""), "Value (EUR)": entry.get("value"),
                "Source": entry.get("source", ""),
            })
    for item in costing.get("adverse_events", {}).get("items", []):
        cost = item.get("cost", {})
        if isinstance(cost, Mapping) and "value" in cost:
            rows.append({
                "Group": "Adverse events", "Item": item.get("label", item.get("id")),
                "Applies to": str(item.get("arm", "")).replace("_", " "),
                "Frequency": item.get("frequency", ""), "Value (EUR)": cost.get("value"),
                "Source": cost.get("source", ""),
            })
    return pd.DataFrame(rows)


def distribution_curve(dist: Mapping[str, Any] | None) -> tuple[pd.DataFrame, tuple[float, float]] | None:
    """Density of a declared distribution plus its central 95% interval.

    Returns ``None`` for fixed, linked or unparameterised distributions.
    """

    if not dist or _stats is None:
        return None
    kind = str(dist.get("type", "")).lower()
    try:
        if kind == "beta" and float(dist["alpha"]) > 0 and float(dist["beta"]) > 0:
            frozen = _stats.beta(float(dist["alpha"]), float(dist["beta"]))
        elif kind == "gamma" and float(dist["mean"]) > 0 and float(dist["se"]) > 0:
            mean, se = float(dist["mean"]), float(dist["se"])
            frozen = _stats.gamma((mean / se) ** 2, scale=se**2 / mean)
        elif kind == "lognormal":
            frozen = _stats.lognorm(float(dist["sigma"]), scale=math.exp(float(dist["mu"])))
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    import numpy as np

    xs = np.linspace(frozen.ppf(0.0005), frozen.ppf(0.9995), 300)
    curve = pd.DataFrame({"value": xs, "density": frozen.pdf(xs)})
    return curve, (float(frozen.ppf(0.025)), float(frozen.ppf(0.975)))
