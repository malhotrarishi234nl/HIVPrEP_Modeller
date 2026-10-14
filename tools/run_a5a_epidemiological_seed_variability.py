#!/usr/bin/env python3
"""Run Phase-2 A-5a: stochastic variability of annual calibration outputs.

The run is deliberately single-arm and contains no economic calculations.  At
one fixed epidemiological configuration it runs 40--50 stochastic replicates,
then estimates the unbiased sample variance for every (metric, target-year).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import chi2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402


METRICS = (
    "PLHIV",
    "new_diagnoses",
    "new_diagnoses_msm",
    "aids_deaths",
    "on_art_stock",
    "new_infections",
    "new_aids_cases",
)
SCENARIO = "submitted_nearfit_rank001_stochastic"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: Iterable[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def parse_seeds(text: str) -> list[int]:
    """Parse comma-separated integers and inclusive ranges (e.g. 7001-7050)."""
    seeds: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(f"descending seed range: {token}")
            seeds.extend(range(start, end + 1))
        else:
            seeds.append(int(token))
    if len(seeds) != len(set(seeds)):
        raise argparse.ArgumentTypeError("seed list contains duplicates")
    if not 40 <= len(seeds) <= 50:
        raise argparse.ArgumentTypeError(
            f"A-5a requires 40--50 stochastic replicates, got {len(seeds)}"
        )
    return seeds


def target_years_from_register(register: dict) -> dict[str, list[int]]:
    """Return the union of declared, non-excluded target years per A-5a output."""
    years: dict[str, set[int]] = {metric: set() for metric in METRICS}
    for target in register.get("targets", []):
        metric = target.get("model_output")
        if metric not in years or target.get("role") == "excluded":
            continue
        coverage = target.get("restricted_years", target.get("year_coverage"))
        if not isinstance(coverage, list) or len(coverage) != 2:
            raise ValueError(f"invalid year coverage for {target.get('series_name')}: {coverage}")
        start, end = map(int, coverage)
        years[metric].update(range(start, end + 1))
    missing = [metric for metric, values in years.items() if not values]
    if missing:
        raise ValueError(f"target register has no years for A-5a metrics: {missing}")
    return {metric: sorted(values) for metric, values in years.items()}


def variance_rows(replicates: pd.DataFrame) -> pd.DataFrame:
    """Compute year-specific unbiased variance and a normal-theory 95% CI."""
    required = {"scenario", "seed", "metric", "year", "value"}
    if missing := required - set(replicates):
        raise ValueError(f"replicate table missing columns: {sorted(missing)}")
    rows: list[dict] = []
    for (scenario, metric, year), group in replicates.groupby(
        ["scenario", "metric", "year"], sort=True
    ):
        values = group.sort_values("seed")["value"].to_numpy(dtype=float)
        n = len(values)
        if n < 2 or not np.all(np.isfinite(values)):
            raise ValueError(f"invalid replicate values for {metric}/{year}")
        variance = float(np.var(values, ddof=1))
        df = n - 1
        if variance == 0.0:
            ci_low = ci_high = 0.0
        else:
            ci_low = float(df * variance / chi2.ppf(0.975, df))
            ci_high = float(df * variance / chi2.ppf(0.025, df))
        rows.append(
            {
                "scenario": scenario,
                "metric": metric,
                "year": int(year),
                "n_replicates": n,
                "mean": float(np.mean(values)),
                "sample_variance_v_ensemble": variance,
                "sample_sd": float(np.std(values, ddof=1)),
                "variance_ci95_normal_theory_low": ci_low,
                "variance_ci95_normal_theory_high": ci_high,
                "minimum": float(np.min(values)),
                "maximum": float(np.max(values)),
            }
        )
    return pd.DataFrame(rows)


def metric_summary(yearly: pd.DataFrame) -> pd.DataFrame:
    """Summarise the distribution of target-year sample variances by metric."""
    rows: list[dict] = []
    for (scenario, metric), group in yearly.groupby(["scenario", "metric"], sort=True):
        variances = group["sample_variance_v_ensemble"].to_numpy(dtype=float)
        replicate_ns = group["n_replicates"].unique()
        if len(replicate_ns) != 1:
            raise ValueError(f"inconsistent replicate counts for {metric}")
        rows.append(
            {
                "scenario": scenario,
                "metric": metric,
                "n_replicates_per_year": int(replicate_ns[0]),
                "n_target_years": len(variances),
                "first_target_year": int(group["year"].min()),
                "last_target_year": int(group["year"].max()),
                "mean_of_target_year_sample_variances": float(np.mean(variances)),
                "p95_of_target_year_sample_variances": float(np.percentile(variances, 95)),
                "maximum_target_year_sample_variance": float(np.max(variances)),
            }
        )
    return pd.DataFrame(rows)


def validate_tables(
    replicates: pd.DataFrame,
    yearly: pd.DataFrame,
    summary: pd.DataFrame,
    seeds: Sequence[int],
    target_years: dict[str, list[int]],
) -> None:
    expected_targets = sum(len(target_years[metric]) for metric in METRICS)
    if len(replicates) != expected_targets * len(seeds):
        raise AssertionError("replicate row count does not match target-years x seeds")
    if len(yearly) != expected_targets or len(summary) != len(METRICS):
        raise AssertionError("summary row count does not match declared targets")
    if set(replicates["seed"]) != set(seeds):
        raise AssertionError("replicate table seed set differs from requested seeds")
    if set(replicates["metric"]) != set(METRICS):
        raise AssertionError("replicate table metric set differs from A-5a specification")
    if not (yearly["n_replicates"] == len(seeds)).all():
        raise AssertionError("a target-year has an incomplete replicate set")
    direct = (
        replicates.groupby(["scenario", "metric", "year"])["value"]
        .var(ddof=1)
        .sort_index()
    )
    recorded = yearly.set_index(["scenario", "metric", "year"])[
        "sample_variance_v_ensemble"
    ].sort_index()
    if not np.allclose(direct.to_numpy(), recorded.to_numpy(), rtol=1e-14, atol=1e-12):
        raise AssertionError(
            "recorded V_ensemble does not numerically match direct pandas variance"
        )


def render_report(
    *, config: Path, register: Path, seeds: Sequence[int], yearly: pd.DataFrame,
    summary: pd.DataFrame, elapsed: float, git_commit: str, dirty: bool,
) -> str:
    table = summary.to_markdown(index=False, floatfmt=".6g")
    zero_count = int((yearly["sample_variance_v_ensemble"] == 0.0).sum())
    return f"""# A-5a epidemiological seed-variability report

**Status:** complete  
**Scenario:** `{SCENARIO}`  
**Replicates:** n={len(seeds)} stochastic runs; seeds `{min(seeds)}-{max(seeds)}`  
**Config:** `{config}`  
**Target register:** `{register}`  
**Git commit:** `{git_commit}`; working tree {'dirty' if dirty else 'clean'}  
**Elapsed:** {elapsed:.1f} seconds

## Result

`raw/yearly_v_ensemble.csv` is the operative result: one unbiased sample variance
(`ddof=1`) for every calibration-output/year pair, each based on n={len(seeds)}.
It covers {len(yearly)} scalar target-years and all seven A-5a series. There are
{zero_count} target-years with exactly zero estimated stochastic variance.

{table}

The final two columns requested by the Andrianakis convention are descriptive
summaries across the target-year sample variances for each metric. The history-
matching budget should use the **year-specific** value, not replace it with the
cross-year mean or p95. Strictly, Andrianakis' mean/p95 convention is across
fixed input design points for the *same scalar output*. This audit has one fixed
input configuration, so a true across-design-point mean and p95 are not
identifiable; at one design point both equal that target-year's single variance.

Each target-year row also carries a 95% chi-square confidence interval for the
variance under the normal-replicate assumption. It is an uncertainty diagnostic,
not a replacement for `V_ensemble`.

## Reproduction and provenance

The exact invocation, Python/platform details, code/config hashes, seed list,
git status, and hash of the complete pre-run dirty diff are in `manifest.json`.
The captured status and diff are in `provenance/`; output hashes are in
`SHA256SUMS.txt`. Raw seed-level values are retained in `raw/replicates.csv`.

## Acceptance

- [x] Post-fix code, stochastic mode, one fixed epidemiological configuration.
- [x] n={len(seeds)} (within the specified 40--50) with exact seeds recorded.
- [x] `PLHIV`, `new_diagnoses`, `new_diagnoses_msm`, `aids_deaths`,
  `on_art_stock`, `new_infections`, and `new_aids_cases` reported per target year.
- [x] Unbiased year-specific `V_ensemble`, plus cross-year mean and p95 sample-
  variance summaries, recorded with their interpretation.
- [x] Persistent raw data, exact provenance, and SHA-256 hashes written.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--target-register", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("7001-7050"))
    args = parser.parse_args()

    config_path = args.model_config.resolve()
    register_path = args.target_register.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir()
    provenance_dir.mkdir()

    git_commit = git_text(["rev-parse", "HEAD"])
    git_status = git_text(["status", "--short"])
    git_diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"], cwd=ROOT, check=True,
        capture_output=True,
    ).stdout
    (provenance_dir / "git_status_short.txt").write_text(
        git_status + ("\n" if git_status else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(git_diff)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    register = json.loads(register_path.read_text(encoding="utf-8"))
    target_years = target_years_from_register(register)
    config["run_control"]["deterministic_mode"]["value"] = False
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {"value": False}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False

    code_files = ("run_v3.py", "engine_v3.py", "calib_extractor.py", "stochastic_allocation.py")
    manifest = {
        "task": "A-5a epidemiological seed variability",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": SCENARIO,
        "mode": "stochastic",
        "n_replicates": len(args.seeds),
        "seeds": list(args.seeds),
        "model_config": str(config_path),
        "target_register": str(register_path),
        "git_commit": git_commit,
        "git_dirty": bool(git_status),
        "git_status_short_sha256": sha256(provenance_dir / "git_status_short.txt"),
        "git_diff_sha256": sha256(provenance_dir / "git_diff.patch"),
        "python": sys.version,
        "platform": platform.platform(),
        "input_sha256": {
            "model_config": sha256(config_path),
            "target_register": sha256(register_path),
            **{f"code/{name}": sha256(ROOT / name) for name in code_files},
        },
        "variance_estimator": "numpy/pandas sample variance, ddof=1",
        "target_years": target_years,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    start = time.time()
    rows: list[dict] = []
    timing_rows: list[dict] = []
    log_path = output_dir / "runner_stdout.log"
    with log_path.open("w", encoding="utf-8") as log:
        for index, seed in enumerate(args.seeds, start=1):
            run_config = json.loads(json.dumps(config))
            run_config["run_control"]["seed"] = {"value": int(seed)}
            run_started = time.time()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                result = SimulationEngineV3(run_config, seed=int(seed)).run(run_number=index)
            log.write(f"===== seed {seed} =====\n{buffer.getvalue()}")
            annual = extract_annual_outputs(result, start_year=1975, end_year=2025)
            for metric in METRICS:
                if metric not in annual:
                    raise KeyError(f"simulation did not emit required A-5a metric {metric}")
                for year in target_years[metric]:
                    if year not in annual[metric]:
                        raise KeyError(f"simulation missing {metric}/{year}")
                    rows.append(
                        {"scenario": SCENARIO, "seed": int(seed), "metric": metric,
                         "year": int(year), "value": float(annual[metric][year])}
                    )
            timing_rows.append(
                {"run_index": index, "seed": int(seed),
                 "elapsed_seconds": time.time() - run_started}
            )
            print(f"A-5a replicate {index}/{len(args.seeds)} seed={seed}", flush=True)

    replicates = pd.DataFrame(rows)
    yearly = variance_rows(replicates)
    summary = metric_summary(yearly)
    validate_tables(replicates, yearly, summary, args.seeds, target_years)
    replicates.to_csv(raw_dir / "replicates.csv", index=False)
    yearly.to_csv(raw_dir / "yearly_v_ensemble.csv", index=False)
    summary.to_csv(raw_dir / "metric_variance_summary.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(raw_dir / "run_timings.csv", index=False)

    elapsed = time.time() - start
    report_path = output_dir / "A5A_EPIDEMIOLOGICAL_SEED_VARIABILITY.md"
    report_path.write_text(
        render_report(config=config_path, register=register_path, seeds=args.seeds,
                      yearly=yearly, summary=summary, elapsed=elapsed,
                      git_commit=git_commit, dirty=bool(git_status)),
        encoding="utf-8",
    )
    artifact_paths = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    output_hashes = {
        str(path.relative_to(output_dir)): sha256(path) for path in artifact_paths
    }
    manifest.update(
        {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed,
            "counts": {
                "replicate_rows": len(replicates),
                "target_year_rows": len(yearly),
                "metric_summary_rows": len(summary),
            },
            "output_sha256": output_hashes,
        }
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    all_hash_paths = sorted(path for path in output_dir.rglob("*") if path.is_file())
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(output_dir)}\n"
            for path in all_hash_paths if path.name != "SHA256SUMS.txt"
        ), encoding="utf-8"
    )
    print(f"A-5a artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
