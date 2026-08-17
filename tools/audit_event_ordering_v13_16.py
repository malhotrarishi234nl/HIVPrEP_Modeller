#!/usr/bin/env python3
"""
V13.16 diagnostic event-ordering audit.

Runs selected calibration candidate configs under guarded event-order variants
and writes candidate-level gate summaries plus annual stock/flow diagnostics.
This tool does not change production behavior; variants are activated only by
run_control.diagnostic_event_order_variant in an in-memory config copy.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import glob
import html
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_acceptance_gates import (  # noqa: E402
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_extractor import extract_annual_outputs  # noqa: E402
from calib_target_loader import (  # noqa: E402
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from run_lhs_calibration import CALIB_END_YEAR, load_json, set_nested  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402


DEFAULT_VARIANTS = [
    "current",
    "art_lag_after_diagnosis",
    "mortality_before_diagnosis_art",
]


def _candidate_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for value in args.candidate or []:
        paths.append(Path(value))
    for pattern in args.candidate_glob or []:
        paths.extend(Path(p) for p in glob.glob(pattern))
    if not paths:
        paths = [
            ROOT / "outputs/calibration/abc_smc_v13_15_1975_orientation_seed_big/abc_smc_best_fit_rank_001.json",
            ROOT / "outputs/calibration/abc_smc_v13_14_1975_burnin_pilot/abc_smc_best_fit_rank_001.json",
        ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            deduped.append(Path(resolved))
            seen.add(resolved)
    return deduped


def _set_if_present(config: dict, path: str, value) -> None:
    try:
        set_nested(config, path, value)
    except Exception:
        pass


def _config_for_variant(base_config: dict, variant: str) -> dict:
    config = copy.deepcopy(base_config)
    _apply_embedded_fixed_parameters(config)
    config.setdefault("run_control", {})["diagnostic_event_order_variant"] = {"value": variant}
    config.setdefault("run_control", {}).setdefault("deterministic_mode", {})["value"] = True
    _set_if_present(config, "output.enabled.value", False)
    _set_if_present(config, "output.terminal_summary.value", False)
    return config


def _apply_embedded_fixed_parameters(config: dict) -> None:
    """Recover fixed parameters from ABC metadata in existing best-fit configs.

    Some pre-V13.16 best-fit JSONs were written with `run_control.start_year`
    reset to the default calibration start even though the evaluated particle
    used `--simulation-start-year 1975`. The metadata retained the fixed
    parameter list, so the audit applies it before running variants.
    """
    fixed_parameters = (
        config.get("calibration_metadata", {})
        .get("abc_smc_calibration", {})
        .get("fixed_parameters", [])
    )
    if not isinstance(fixed_parameters, list):
        return
    for fixed in fixed_parameters:
        try:
            set_nested(config, fixed["config_path"], fixed["value"])
        except Exception:
            continue


def _run_quietly(config: dict, seed: int) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(config, seed=seed)
        return engine.run(run_number=1)


def _annual_event_order_rows(
    summary: pd.DataFrame,
    *,
    candidate_name: str,
    candidate_path: Path,
    variant: str,
) -> list[dict]:
    work = summary.copy()
    work["calib_year"] = work["year"].astype(float).apply(int)
    rows: list[dict] = []
    flow_cols = [
        "new_infections",
        "new_diagnoses",
        "new_diagnoses_msm",
        "new_aids_cases",
        "aids_deaths",
        "hiv_deaths",
        "art_initiations",
        "art_initiations_aids",
        "same_step_aids_art_rescue_proxy",
    ]
    stock_cols = [
        "on_art_stock",
        "aids_stock_start",
        "aids_off_art_start",
        "aids_on_art_start",
        "aids_undiagnosed_start",
        "aids_stock_pre_mortality",
        "aids_off_art_pre_mortality",
        "aids_on_art_pre_mortality",
        "aids_undiagnosed_pre_mortality",
    ]
    for year, year_df in work.groupby("calib_year", sort=True):
        final = year_df.iloc[-1]
        row = {
            "candidate": candidate_name,
            "candidate_path": str(candidate_path),
            "variant": variant,
            "year": int(year),
        }
        for col in flow_cols:
            if col in year_df.columns:
                row[col] = float(year_df[col].sum())
        for col in stock_cols:
            if col in year_df.columns:
                row[f"{col}_end"] = float(final[col])
                row[f"{col}_mean"] = float(year_df[col].mean())
                row[f"{col}_max"] = float(year_df[col].max())
        rows.append(row)
    return rows


def _candidate_label(path: Path, idx: int) -> str:
    parent = path.parent.name
    return f"c{idx:02d}_{parent}_{path.stem}"


def _write_report(
    output_dir: Path,
    summary_df: pd.DataFrame,
    timeseries_df: pd.DataFrame,
    *,
    created_at: str,
) -> None:
    css = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; color: #18243d; }
      table { border-collapse: collapse; width: 100%; margin: 16px 0 28px; font-size: 13px; }
      th, td { border: 1px solid #d6dbe3; padding: 6px 8px; text-align: right; }
      th:first-child, td:first-child { text-align: left; }
      th { background: #f2f4f7; }
      .meta { color: #56637a; }
    </style>
    """
    key_cols = [
        "candidate",
        "variant",
        "overall_accept",
        "acceptance_n_failures",
        "diagnosis_gate_pass",
        "aids_death_gate_pass",
        "acceptance_diagnosis_gate_new_diagnoses_ratio",
        "acceptance_diagnosis_gate_new_diagnoses_msm_ratio",
        "acceptance_aids_death_gate_bounds_fraction",
        "acceptance_plhiv_gate_recent_median_abs_pct",
        "acceptance_art_stock_gate_recent_median_abs_pct",
        "aids_deaths_1990_2005",
        "aids_deaths_peak_year",
        "aids_deaths_peak_value",
        "same_step_aids_art_rescue_proxy_1990_2005",
    ]
    key_cols = [c for c in key_cols if c in summary_df.columns]
    html_text = f"""
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><title>V13.16 Event Ordering Audit</title>{css}</head>
    <body>
      <h1>V13.16 Event Ordering Audit</h1>
      <p class="meta">Created {html.escape(created_at)}</p>
      <h2>Candidate Summary</h2>
      {summary_df[key_cols].to_html(index=False, escape=False)}
      <h2>Annual Diagnostics Preview</h2>
      {timeseries_df.head(120).to_html(index=False, escape=False)}
    </body>
    </html>
    """
    (output_dir / "event_ordering_audit_report.html").write_text(html_text, encoding="utf-8")


def _write_markdown_summary(output_dir: Path, summary_df: pd.DataFrame, created_at: str) -> None:
    diag_aids = (
        summary_df["diagnosis_gate_pass"].astype(bool)
        & summary_df["aids_death_gate_pass"].astype(bool)
    )
    lines = [
        "# V13.16 Event Ordering Audit Summary",
        "",
        f"Created: `{created_at}`",
        "",
        "## Headline",
        "",
        f"- Candidate/variant rows: `{len(summary_df)}`.",
        f"- Overall accept rows: `{int(summary_df['overall_accept'].astype(bool).sum())}`.",
        f"- Diagnosis + AIDS-death gate rows: `{int(diag_aids.sum())}`.",
        "",
        "## Best Rows",
        "",
    ]
    best_cols = [
        "candidate",
        "variant",
        "acceptance_n_failures",
        "acceptance_diagnosis_gate_new_diagnoses_ratio",
        "acceptance_aids_death_gate_bounds_fraction",
        "aids_deaths_1990_2005",
        "same_step_aids_art_rescue_proxy_1990_2005",
    ]
    best_cols = [c for c in best_cols if c in summary_df.columns]
    sort_cols = [c for c in ["acceptance_n_failures", "abc_distance"] if c in summary_df.columns]
    best_df = (
        summary_df.sort_values(sort_cols, na_position="last")
        if sort_cols
        else summary_df
    )
    lines.extend(_markdown_table(best_df.head(12)[best_cols]))
    lines.extend([
        "",
        "## Interpretation Prompt",
        "",
        "Compare `current` against `art_lag_after_diagnosis` and "
        "`mortality_before_diagnosis_art`. If AIDS-death bound fraction rises "
        "materially while diagnosis, PLHIV, and ART stock remain close, same-step "
        "diagnosis/ART rescue is a likely structural limiter. If not, the "
        "remaining conflict is more likely burn-in/epidemic history, progression "
        "timing, or a missing late-presenter/entry mechanism.",
        "",
    ])
    (output_dir / "V13_16_EVENT_ORDERING_AUDIT_SUMMARY.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _markdown_table(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    headers = [str(c) for c in df.columns]
    rows = []
    rows.append("| " + " | ".join(headers) + " |")
    rows.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for record in df.to_dict("records"):
        values = []
        for col in df.columns:
            value = record.get(col, "")
            if isinstance(value, float):
                values.append(f"{value:.4g}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return rows


def run_audit(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().isoformat(timespec="seconds")

    targets = load_disease_targets(
        args.target_workbook,
        args.target_mapping,
        stage="joint",
        start_year=args.scoring_start_year,
        end_year=CALIB_END_YEAR,
    )

    summary_rows: list[dict] = []
    timeseries_rows: list[dict] = []
    candidate_paths = _candidate_paths(args)

    for cand_idx, path in enumerate(candidate_paths, start=1):
        if not path.exists():
            raise FileNotFoundError(f"Candidate config not found: {path}")
        candidate_name = _candidate_label(path, cand_idx)
        base_config = load_json(path)
        for variant_idx, variant in enumerate(args.variants, start=1):
            config = _config_for_variant(base_config, variant)
            seed = int(args.seed + cand_idx * 1000 + variant_idx)
            result = _run_quietly(config, seed)
            summary = result["summary"]
            outputs = extract_annual_outputs(
                result,
                args.scoring_start_year,
                CALIB_END_YEAR,
            )
            gate_result = evaluate_acceptance_gates(
                outputs,
                targets,
                settings=AcceptanceGateSettings(),
            )
            row = {
                "candidate": candidate_name,
                "candidate_path": str(path),
                "variant": variant,
                "seed": seed,
            }
            row.update(acceptance_gate_row_fields(gate_result))
            annual = pd.DataFrame(_annual_event_order_rows(
                summary,
                candidate_name=candidate_name,
                candidate_path=path,
                variant=variant,
            ))
            if not annual.empty:
                audit_years = annual[(annual["year"] >= 1990) & (annual["year"] <= 2005)]
                row["aids_deaths_1990_2005"] = float(audit_years.get("aids_deaths", pd.Series(dtype=float)).sum())
                row["same_step_aids_art_rescue_proxy_1990_2005"] = float(
                    audit_years.get("same_step_aids_art_rescue_proxy", pd.Series(dtype=float)).sum()
                )
                if "aids_deaths" in annual.columns and annual["aids_deaths"].notna().any():
                    peak_idx = annual["aids_deaths"].idxmax()
                    row["aids_deaths_peak_year"] = int(annual.loc[peak_idx, "year"])
                    row["aids_deaths_peak_value"] = float(annual.loc[peak_idx, "aids_deaths"])
            summary_rows.append(row)
            timeseries_rows.extend(annual.to_dict("records"))

    summary_df = pd.DataFrame(summary_rows)
    timeseries_df = pd.DataFrame(timeseries_rows)
    summary_path = output_dir / "event_ordering_audit_summary.csv"
    timeseries_path = output_dir / "event_ordering_audit_timeseries.csv"
    summary_df.to_csv(summary_path, index=False)
    timeseries_df.to_csv(timeseries_path, index=False)
    _write_report(output_dir, summary_df, timeseries_df, created_at=created_at)
    _write_markdown_summary(output_dir, summary_df, created_at)

    manifest = {
        "created_at": created_at,
        "candidate_paths": [str(p) for p in candidate_paths],
        "variants": list(args.variants),
        "output_dir": str(output_dir),
        "summary_path": str(summary_path),
        "timeseries_path": str(timeseries_path),
    }
    (output_dir / "event_ordering_audit_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {summary_path}")
    print(f"Wrote {timeseries_path}")
    print(f"Wrote {output_dir / 'event_ordering_audit_report.html'}")
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", help="Candidate config JSON path; repeatable.")
    parser.add_argument("--candidate-glob", action="append", help="Candidate config glob; repeatable.")
    parser.add_argument(
        "--variants",
        nargs="+",
        default=DEFAULT_VARIANTS,
        choices=[
            "current",
            "mortality_before_diagnosis_art",
            "art_lag_after_diagnosis",
            "mortality_before_progression",
        ],
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs/calibration/v13_16_event_ordering_audit"),
    )
    parser.add_argument("--seed", type=int, default=1616)
    parser.add_argument("--scoring-start-year", type=int, default=1990)
    parser.add_argument(
        "--target-workbook",
        default=str(default_disease_workbook_path(ROOT)),
    )
    parser.add_argument(
        "--target-mapping",
        default=str(default_target_mapping_path(ROOT)),
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    return run_audit(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
