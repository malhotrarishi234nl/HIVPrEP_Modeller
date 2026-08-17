#!/usr/bin/env python3
"""
V13.17 targeted 1970/1972 burn-in probe.

This is a deterministic, diagnostic-only probe. It takes existing calibrated
candidate configs, applies earlier HMD demography plus seed-year variants in
memory, reruns the model, and reports whether the AIDS-death peak moves toward
the observed 1995 peak without destroying diagnosis, PLHIV, ART, or incidence.
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
from tools.audit_1975_burnin_population import (  # noqa: E402
    _age_sex_distribution_for_year,
    _total_population_for_year,
)
from tools.audit_event_ordering_v13_16 import _apply_embedded_fixed_parameters  # noqa: E402


SIM_END_EXCLUSIVE = CALIB_END_YEAR + 1


def default_scenarios() -> list[dict]:
    return [
        {
            "scenario": "start1975_seed1975_reference",
            "start_year": 1975,
            "seed_year": 1975.0,
            "description": "Reference: existing 1975 burn-in start and seed timing.",
        },
        {
            "scenario": "start1972_seed1972",
            "start_year": 1972,
            "seed_year": 1972.0,
            "description": "Earlier disease burn-in with HMD 1972 demographic start.",
        },
        {
            "scenario": "start1970_seed1970",
            "start_year": 1970,
            "seed_year": 1970.0,
            "description": "Maximum early burn-in probe with HMD 1970 demographic start.",
        },
        {
            "scenario": "start1970_seed1972",
            "start_year": 1970,
            "seed_year": 1972.0,
            "description": "1970 demography but delayed epidemic introduction to 1972.",
        },
        {
            "scenario": "start1970_seed1975",
            "start_year": 1970,
            "seed_year": 1975.0,
            "description": "Demographic-only early burn-in; disease seed remains 1975.",
        },
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
            ROOT / "outputs/calibration/abc_smc_v13_15_1975_orientation_seed_big/abc_smc_best_fit_rank_002.json",
            ROOT / "outputs/calibration/abc_smc_v13_15_1975_orientation_seed_big/abc_smc_best_fit_rank_003.json",
            ROOT / "outputs/calibration/abc_smc_v13_15_1975_orientation_seed_big/abc_smc_best_fit_rank_004.json",
            ROOT / "outputs/calibration/abc_smc_v13_15_1975_orientation_seed_big/abc_smc_best_fit_rank_005.json",
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


def _candidate_label(path: Path, idx: int) -> str:
    return f"c{idx:02d}_{path.parent.name}_{path.stem}"


def prepare_burnin_config(base_config: dict, *, start_year: int, seed_year: float) -> dict:
    """Apply fixed metadata, then override start/seed year and HMD demography."""
    config = copy.deepcopy(base_config)
    _apply_embedded_fixed_parameters(config)

    set_nested(config, "run_control.start_year.value", int(start_year))
    set_nested(config, "run_control.end_year.value", SIM_END_EXCLUSIVE)
    set_nested(config, "run_control.deterministic_mode.value", True)
    set_nested(config, "population.epidemic_seed.seed_year.value", float(seed_year))

    dist = _age_sex_distribution_for_year(int(start_year))
    if dist is not None:
        set_nested(config, "population.age_sex_distribution.value", dist)
    total = _total_population_for_year(int(start_year))
    if total is not None:
        set_nested(config, "population.total_size.value", int(total))

    try:
        set_nested(config, "output.enabled.value", False)
        set_nested(config, "output.terminal_summary.value", False)
    except Exception:
        config.setdefault("output", {})["enabled"] = {"value": False}
        config.setdefault("output", {})["terminal_summary"] = {"value": False}
    return config


def _run_quietly(config: dict, seed: int) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine = SimulationEngineV3(config, seed=seed)
        return engine.run(run_number=1)


def _target_peak(targets: list[dict]) -> tuple[int | None, float | None]:
    rows = [
        (int(t["year"]), float(t["estimate"]))
        for t in targets
        if t.get("model_output") == "aids_deaths" and t.get("estimate") is not None
    ]
    if not rows:
        return None, None
    return max(rows, key=lambda item: item[1])


def _annual_rows(outputs: dict, *, candidate: str, candidate_path: Path, scenario: dict) -> list[dict]:
    years = sorted(set().union(*(v.keys() for v in outputs.values() if isinstance(v, dict))))
    rows = []
    for year in years:
        row = {
            "candidate": candidate,
            "candidate_path": str(candidate_path),
            "scenario": scenario["scenario"],
            "start_year": scenario["start_year"],
            "seed_year": scenario["seed_year"],
            "year": int(year),
        }
        for indicator in [
            "PLHIV",
            "new_infections",
            "aids_deaths",
            "new_diagnoses",
            "new_diagnoses_msm",
            "new_aids_cases",
            "on_art_stock",
        ]:
            if indicator in outputs:
                row[indicator] = outputs[indicator].get(year)
        rows.append(row)
    return rows


def _summarize_outputs(
    outputs: dict,
    gate_fields: dict,
    *,
    targets: list[dict],
) -> dict:
    aids = outputs.get("aids_deaths", {})
    summary: dict = {}
    if aids:
        peak_year = max(aids, key=aids.get)
        summary["aids_deaths_peak_year"] = int(peak_year)
        summary["aids_deaths_peak_value"] = float(aids[peak_year])
        summary["aids_deaths_1990_2005"] = float(
            sum(v for y, v in aids.items() if 1990 <= y <= 2005)
        )
        summary["aids_deaths_pre1990"] = float(
            sum(v for y, v in aids.items() if y < 1990)
        )
        summary["aids_deaths_1990_1997"] = float(
            sum(v for y, v in aids.items() if 1990 <= y <= 1997)
        )
    target_peak_year, target_peak_value = _target_peak(targets)
    summary["target_aids_deaths_peak_year"] = target_peak_year
    summary["target_aids_deaths_peak_value"] = target_peak_value
    if target_peak_year is not None and "aids_deaths_peak_year" in summary:
        summary["aids_peak_year_error"] = int(summary["aids_deaths_peak_year"] - target_peak_year)

    # Carry selected gate fields with shorter aliases for easier scanning.
    aliases = {
        "acceptance_diagnosis_gate_new_diagnoses_ratio": "diagnosis_ratio_2024",
        "acceptance_diagnosis_gate_new_diagnoses_msm_ratio": "msm_diagnosis_ratio_2024",
        "acceptance_plhiv_gate_recent_median_abs_pct": "plhiv_recent_mape",
        "acceptance_incidence_gate_recent_median_model_target_ratio": "incidence_recent_ratio",
        "acceptance_aids_death_gate_bounds_fraction": "aids_death_bounds_fraction",
        "acceptance_art_stock_gate_recent_median_abs_pct": "art_recent_mape",
    }
    for source, dest in aliases.items():
        if source in gate_fields:
            summary[dest] = gate_fields[source]
    return summary


def _write_html(output_dir: Path, summary_df: pd.DataFrame, timeseries_df: pd.DataFrame, created_at: str) -> None:
    key_cols = [
        "candidate",
        "scenario",
        "acceptance_n_failures",
        "diagnosis_gate_pass",
        "aids_death_gate_pass",
        "diagnosis_ratio_2024",
        "msm_diagnosis_ratio_2024",
        "aids_death_bounds_fraction",
        "aids_deaths_peak_year",
        "aids_deaths_peak_value",
        "aids_peak_year_error",
        "aids_deaths_1990_2005",
        "aids_deaths_pre1990",
        "plhiv_recent_mape",
        "art_recent_mape",
    ]
    key_cols = [c for c in key_cols if c in summary_df.columns]
    style = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; color: #18243d; }
      table { border-collapse: collapse; width: 100%; margin: 16px 0 28px; font-size: 13px; }
      th, td { border: 1px solid #d6dbe3; padding: 6px 8px; text-align: right; }
      th:first-child, td:first-child { text-align: left; }
      th { background: #f2f4f7; }
      .meta { color: #56637a; }
    </style>
    """
    html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>V13.17 1970 Burn-In Probe</title>{style}</head>
<body>
<h1>V13.17 1970/1972 Burn-In Probe</h1>
<p class="meta">Created {html.escape(created_at)}</p>
<h2>Summary</h2>
{summary_df[key_cols].to_html(index=False)}
<h2>Timeseries Preview</h2>
{timeseries_df.head(180).to_html(index=False)}
</body></html>
"""
    (output_dir / "burnin_1970_probe_report.html").write_text(html_text, encoding="utf-8")


def _write_markdown(output_dir: Path, summary_df: pd.DataFrame, created_at: str) -> None:
    diag_aids = summary_df["diagnosis_gate_pass"].astype(bool) & summary_df["aids_death_gate_pass"].astype(bool)
    best = summary_df.sort_values(
        ["acceptance_n_failures", "aids_peak_year_error", "aids_death_bounds_fraction"],
        ascending=[True, True, False],
        na_position="last",
    ).head(12)
    cols = [
        "candidate",
        "scenario",
        "acceptance_n_failures",
        "diagnosis_ratio_2024",
        "aids_death_bounds_fraction",
        "aids_deaths_peak_year",
        "aids_peak_year_error",
        "plhiv_recent_mape",
        "art_recent_mape",
    ]
    cols = [c for c in cols if c in best.columns]
    lines = [
        "# V13.17 1970/1972 Burn-In Probe Summary",
        "",
        f"Created: `{created_at}`",
        "",
        f"- Candidate/scenario rows: `{len(summary_df)}`.",
        f"- Overall accept rows: `{int(summary_df['overall_accept'].astype(bool).sum())}`.",
        f"- Diagnosis + AIDS-death rows: `{int(diag_aids.sum())}`.",
        "",
        "## Best Rows",
        "",
        _markdown_table(best[cols]),
        "",
        "## Decision Rule",
        "",
        "A 1970/1972 start is promising only if it moves the AIDS-death peak "
        "toward 1995 and improves AIDS-death bounds without causing large losses "
        "in diagnosis, PLHIV, ART, or incidence fit.",
        "",
    ]
    (output_dir / "V13_17_1970_BURNIN_PROBE_SUMMARY.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    rows = ["| " + " | ".join(map(str, df.columns)) + " |"]
    rows.append("| " + " | ".join(["---"] * len(df.columns)) + " |")
    for rec in df.to_dict("records"):
        values = []
        for col in df.columns:
            value = rec[col]
            if isinstance(value, float):
                values.append(f"{value:.4g}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def run_probe(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().isoformat(timespec="seconds")
    targets = load_disease_targets(
        args.target_workbook,
        args.target_mapping,
        stage="joint",
        start_year=1990,
        end_year=CALIB_END_YEAR,
    )

    scenarios = default_scenarios()
    candidate_paths = _candidate_paths(args)
    summary_rows: list[dict] = []
    timeseries_rows: list[dict] = []

    for cand_idx, candidate_path in enumerate(candidate_paths, start=1):
        if not candidate_path.exists():
            raise FileNotFoundError(f"Candidate config not found: {candidate_path}")
        candidate = _candidate_label(candidate_path, cand_idx)
        base_config = load_json(candidate_path)
        for scen_idx, scenario in enumerate(scenarios, start=1):
            config = prepare_burnin_config(
                base_config,
                start_year=int(scenario["start_year"]),
                seed_year=float(scenario["seed_year"]),
            )
            seed = int(args.seed + cand_idx * 1000 + scen_idx)
            result = _run_quietly(config, seed=seed)
            outputs = extract_annual_outputs(
                result,
                int(scenario["start_year"]),
                CALIB_END_YEAR,
            )
            gate_result = evaluate_acceptance_gates(outputs, targets, AcceptanceGateSettings())
            gate_fields = acceptance_gate_row_fields(gate_result)
            row = {
                "candidate": candidate,
                "candidate_path": str(candidate_path),
                "scenario": scenario["scenario"],
                "description": scenario["description"],
                "start_year": scenario["start_year"],
                "seed_year": scenario["seed_year"],
                "seed": seed,
            }
            row.update(gate_fields)
            row.update(_summarize_outputs(outputs, gate_fields, targets=targets))
            summary_rows.append(row)
            timeseries_rows.extend(
                _annual_rows(outputs, candidate=candidate, candidate_path=candidate_path, scenario=scenario)
            )

    summary_df = pd.DataFrame(summary_rows)
    timeseries_df = pd.DataFrame(timeseries_rows)
    summary_path = output_dir / "burnin_1970_probe_summary.csv"
    timeseries_path = output_dir / "burnin_1970_probe_timeseries.csv"
    summary_df.to_csv(summary_path, index=False)
    timeseries_df.to_csv(timeseries_path, index=False)
    _write_html(output_dir, summary_df, timeseries_df, created_at)
    _write_markdown(output_dir, summary_df, created_at)
    manifest = {
        "created_at": created_at,
        "candidate_paths": [str(p) for p in candidate_paths],
        "scenarios": scenarios,
        "output_dir": str(output_dir),
    }
    (output_dir / "burnin_1970_probe_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {summary_path}")
    print(f"Wrote {timeseries_path}")
    print(f"Wrote {output_dir / 'burnin_1970_probe_report.html'}")
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", help="Candidate config JSON path; repeatable.")
    parser.add_argument("--candidate-glob", action="append", help="Candidate config glob; repeatable.")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs/calibration/v13_17_1970_burnin_probe"),
    )
    parser.add_argument("--seed", type=int, default=1717)
    parser.add_argument("--target-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    return run_probe(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

