"""
Reliability helpers for HTA calibration handoff and baseline coverage checks.

These functions are intentionally small and side-effect free so they can be
used by both the command-line runner and the Excel report writer.
"""

from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


def summarize_calibration_handoff(config: dict) -> dict:
    """Return a compact status summary for the model calibration handoff."""
    runtime_deterministic = _extract_nested_value(
        config,
        ("run_control", "deterministic_mode"),
    )
    lhs = (
        config.get("calibration_metadata", {})
        .get("lhs_calibration")
    )
    if not isinstance(lhs, dict) or not lhs:
        return {
            "status": "unversioned",
            "is_lhs_calibrated": False,
            "runtime_deterministic": runtime_deterministic,
            "label": "No LHS calibration metadata found",
            "warnings": [
                "HTA base config does not identify an LHS-selected fit; verify "
                "that this is the intended calibrated handoff before using HTA "
                "outputs for decision interpretation."
            ],
        }

    stage = lhs.get("stage", "?")
    rank = lhs.get("rank", "?")
    draw_id = lhs.get("draw_id", "?")
    total_ll = lhs.get("total_ll")
    score_text = _format_score(total_ll)

    warnings = []
    metadata_deterministic = lhs.get("deterministic_calibration")
    if (
        metadata_deterministic is not None
        and runtime_deterministic is not None
        and bool(metadata_deterministic) != bool(runtime_deterministic)
    ):
        warnings.append(
            "LHS metadata deterministic_calibration="
            f"{metadata_deterministic}, but run_control.deterministic_mode="
            f"{runtime_deterministic}; runtime behavior will follow "
            "run_control unless the HTA runner overrides it."
        )
    if stage != "joint":
        warnings.append(
            f"LHS metadata stage is '{stage}', not 'joint'; HTA should normally "
            "use the final joint-stage calibration."
        )
    if rank != 1:
        warnings.append(
            f"LHS metadata rank is {rank}; HTA should normally use rank 1 unless "
            "a documented sensitivity analysis is being run."
        )
    if not lhs.get("parameters"):
        warnings.append(
            "LHS metadata does not include the calibrated parameter vector; "
            "auditability is reduced."
        )

    return {
        "status": "lhs_calibrated",
        "is_lhs_calibrated": True,
        "stage": stage,
        "rank": rank,
        "draw_id": draw_id,
        "total_ll": total_ll,
        "created_at": lhs.get("created_at", ""),
        "deterministic_calibration": lhs.get("deterministic_calibration"),
        "runtime_deterministic": runtime_deterministic,
        "family_balanced_score": lhs.get("family_balanced_score"),
        "rerank_top": lhs.get("rerank_top"),
        "rerank_replicates": lhs.get("rerank_replicates"),
        "label": f"LHS {stage} rank {rank}, draw {draw_id} ({score_text})",
        "warnings": warnings,
    }


def calibration_handoff_rows(config: dict) -> list[tuple[str, object, str]]:
    """Return report-ready rows describing the calibration handoff."""
    summary = summarize_calibration_handoff(config)
    rows = [
        (
            "Calibration handoff",
            summary["label"],
            "calibration_metadata.lhs_calibration",
        )
    ]
    if summary.get("is_lhs_calibrated"):
        rows.extend(
            [
                (
                    "LHS fit created",
                    summary.get("created_at", ""),
                    "calibration_metadata.lhs_calibration.created_at",
                ),
                (
                    "Runtime deterministic flag",
                    summary.get("runtime_deterministic"),
                    "run_control.deterministic_mode",
                ),
                (
                    "LHS scoring mode",
                    (
                        f"family_balanced={summary.get('family_balanced_score')}; "
                        f"rerank_top={summary.get('rerank_top')}; "
                        f"replicates={summary.get('rerank_replicates')}"
                    ),
                    "calibration_metadata.lhs_calibration",
                ),
            ]
        )
    else:
        rows.append(
            (
                "Calibration warning",
                "; ".join(summary.get("warnings", [])),
                "runtime reliability check",
            )
        )
    return rows


def format_calibration_handoff_for_console(config: dict) -> list[str]:
    """Return concise console lines for the HTA runner."""
    summary = summarize_calibration_handoff(config)
    lines = [f"Calibration handoff: {summary['label']}"]
    for warning in summary.get("warnings", []):
        lines.append(f"  WARNING: {warning}")
    return lines


def infer_steps_per_year(years: Iterable[float]) -> int:
    """Infer expected timesteps per year from a sequence of fractional years."""
    values = sorted({float(year) for year in years})
    diffs = [
        round(values[i + 1] - values[i], 10)
        for i in range(len(values) - 1)
        if 0.0 < values[i + 1] - values[i] <= 1.0
    ]
    if not diffs:
        return 1
    min_step = min(diffs)
    if min_step <= 0:
        return 1
    return max(1, int(round(1.0 / min_step)))


def baseline_year_coverage(df: pd.DataFrame, year_col: str = "year") -> pd.DataFrame:
    """
    Count observed timesteps per integer year and flag incomplete years.

    The freeze-year baseline file commonly contains only the snapshot row at
    YYYY.0. Summing that as an annual flow is misleading, so reports should
    expose the coverage count next to baseline key-year values.
    """
    if year_col not in df.columns or df.empty:
        return pd.DataFrame(
            columns=[
                "year_int",
                "timesteps_in_year",
                "expected_timesteps",
                "complete_year",
            ]
        )

    year_values = pd.to_numeric(df[year_col], errors="coerce").dropna()
    expected = infer_steps_per_year(year_values)
    year_int = year_values.apply(lambda value: int(math.floor(value)))
    coverage = (
        pd.DataFrame({"year_int": year_int})
        .groupby("year_int", sort=True)
        .size()
        .rename("timesteps_in_year")
        .reset_index()
    )
    coverage["expected_timesteps"] = expected
    coverage["complete_year"] = coverage["timesteps_in_year"] >= expected
    return coverage


def _format_score(value: object) -> str:
    try:
        return f"total_ll={float(value):,.1f}"
    except (TypeError, ValueError):
        return "total_ll=unknown"


def _extract_nested_value(config: dict, path: tuple[str, ...]) -> object:
    node = config
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node
