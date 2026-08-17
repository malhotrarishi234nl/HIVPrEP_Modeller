"""Build a standalone calibration fit HTML report for one model config.

This is the lightweight JSON -> HTML/CSV path for reviewing a calibrated
candidate outside the full LHS pipeline diagnostics.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from run_lhs_calibration import CALIB_END_YEAR, CALIB_START_YEAR
from run_lhs_diagnostics import (
    OUTPUT_LABELS,
    OUTPUT_ORDER,
    heatmap_html,
    residual_summary_frame,
    svg_line_chart,
    target_fit_frame,
)
from run_v3 import SimulationEngineV3


def read_json(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def flatten_outputs(outputs: dict[str, dict[int, float]]) -> pd.DataFrame:
    rows = []
    for model_output, series in outputs.items():
        for year, value in series.items():
            rows.append(
                {
                    "model_output": model_output,
                    "year": int(year),
                    "value": float(value),
                }
            )
    return pd.DataFrame(rows).sort_values(["model_output", "year"])


def run_config_outputs(
    config_path: Path,
    seed: int,
    deterministic_mode: bool | None,
    start_year: int,
    end_year: int,
) -> dict[str, dict[int, float]]:
    config = read_json(config_path)
    run_control = config.setdefault("run_control", {})
    run_control.setdefault("start_year", {})["value"] = start_year
    run_control.setdefault("end_year", {})["value"] = end_year + 1
    if deterministic_mode is not None:
        run_control.setdefault("deterministic_mode", {})["value"] = bool(deterministic_mode)
    config.setdefault("output", {}).setdefault("enabled", {})["value"] = False
    config.setdefault("output", {}).setdefault("terminal_summary", {})["value"] = False

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = SimulationEngineV3(config, seed=seed).run(run_number=1)
    return extract_annual_outputs(result, start_year, end_year)


def build_html(
    output_path: Path,
    config_path: Path,
    fit: pd.DataFrame,
    residual_summary: pd.DataFrame,
    annual_outputs: pd.DataFrame,
) -> None:
    charts = []
    for output in [o for o in OUTPUT_ORDER if o in set(fit["model_output"])]:
        chart_data = (
            fit[fit["model_output"] == output][["year", "estimate", "model"]]
            .dropna(subset=["model"])
            .sort_values("year")
        )
        if not chart_data.empty:
            charts.append(svg_line_chart(chart_data, OUTPUT_LABELS.get(output, output)))

    worst = fit.sort_values("abs_z", ascending=False).head(30)
    created = datetime.now().isoformat(timespec="seconds")
    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Calibration Fit Report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 28px; color: #1f2937; }}
h1, h2 {{ color: #1f3864; }}
.meta {{ color: #666; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 18px; }}
.panel {{ border: 1px solid #d9d9d9; border-radius: 6px; padding: 14px; background: #fff; }}
table.data {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 22px; }}
table.data th {{ background: #1f3864; color: #fff; text-align: left; }}
table.data th, table.data td {{ border: 1px solid #d0d0d0; padding: 6px 8px; }}
.heatmap {{ border-collapse: collapse; font-size: 11px; width: 100%; }}
.heatmap th, .heatmap td {{ border: 1px solid #d0d0d0; padding: 4px; text-align: center; }}
.heatmap th {{ background: #1f3864; color: white; position: sticky; left: 0; }}
.chart {{ width: 100%; height: auto; }}
.chart-title {{ font-weight: 700; fill: #1f3864; font-size: 14px; }}
.tick {{ fill: #555; font-size: 10px; }}
.legend {{ font-size: 12px; font-weight: 700; }}
</style>
</head>
<body>
<h1>Calibration Fit Report</h1>
<p class="meta">Created {created} from {html.escape(str(config_path))}</p>
<p class="meta">Calibration window: {int(annual_outputs["year"].min())}-{int(annual_outputs["year"].max())}</p>

<h2>Residual Summary</h2>
{residual_summary.to_html(index=False, classes="data", float_format=lambda x: f"{x:.2f}", escape=True)}

<h2>Trajectory Fit</h2>
<div class="grid">{''.join(f'<div class="panel">{chart}</div>' for chart in charts)}</div>

<h2>Residual Heatmap</h2>
{heatmap_html(fit)}

<h2>Worst Target-Year Residuals</h2>
{worst[["model_output", "year", "estimate", "model", "pct_diff", "z_score", "weighted_ll", "target_name"]].to_html(index=False, classes="data", float_format=lambda x: f"{x:.2f}", escape=True)}
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build standalone HTML/CSV calibration fit diagnostics for one config JSON."
    )
    parser.add_argument("--config", required=True, help="Path to model config JSON.")
    parser.add_argument("--output-dir", required=True, help="Directory for HTML and CSV outputs.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-year", type=int, default=CALIB_START_YEAR)
    parser.add_argument("--end-year", type=int, default=CALIB_END_YEAR)
    parser.add_argument("--targets-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    parser.add_argument("--stage", default="joint", choices=["backbone", "diagnosis", "art", "joint", "all"])
    parser.add_argument(
        "--stochastic",
        action="store_true",
        help="Use the config's stochastic setting instead of forcing deterministic mode.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()

    targets = load_disease_targets(
        args.targets_workbook,
        args.target_mapping,
        stage=args.stage,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    target_df = pd.DataFrame(targets)
    deterministic_mode = None if args.stochastic else True
    outputs = run_config_outputs(
        config_path=config_path,
        seed=args.seed,
        deterministic_mode=deterministic_mode,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    annual_outputs = flatten_outputs(outputs)
    fit = target_fit_frame(outputs, target_df)
    residual_summary = residual_summary_frame(fit)

    output_dir.mkdir(parents=True, exist_ok=True)
    annual_path = output_dir / "annual_outputs.csv"
    fit_path = output_dir / "fit_residuals.csv"
    summary_path = output_dir / "residual_summary.csv"
    html_path = output_dir / "calibration_report.html"

    annual_outputs.to_csv(annual_path, index=False)
    fit.to_csv(fit_path, index=False)
    residual_summary.to_csv(summary_path, index=False)
    build_html(html_path, config_path, fit, residual_summary, annual_outputs)

    print(f"Annual outputs: {annual_path}")
    print(f"Fit residuals:  {fit_path}")
    print(f"Summary:        {summary_path}")
    print(f"HTML report:    {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
