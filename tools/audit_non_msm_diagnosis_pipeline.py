#!/usr/bin/env python3
"""
Structured audit for missing non-MSM / heterosexual diagnoses.

This tool is diagnostic-only. It does not change model behavior or calibration
inputs. It reruns selected best-fit configs and controlled variants, then writes
CSV/Markdown artifacts that make the MSM vs non-MSM decomposition explicit.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_v3 import SimulationEngineV3


DEFAULT_RANK1 = (
    ROOT
    / "outputs/calibration/abc_smc_v13_21_non_msm_diag_gate_20260603"
    / "abc_smc_best_fit_rank_001.json"
)
DEFAULT_PARTICLES = (
    ROOT
    / "outputs/calibration/abc_smc_v13_21_non_msm_diag_gate_20260603"
    / "abc_smc_particles_20260603_232829.csv"
)
DEFAULT_TARGETS = (
    ROOT
    / "outputs/calibration/abc_smc_v13_21_non_msm_diag_gate_20260603"
    / "normalized_targets_joint_20260603_232829.csv"
)
DEFAULT_OUTDIR = ROOT / "outputs/calibration/non_msm_diagnosis_audit_20260604"


def _extract_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _set_value(cfg: dict, path: list[str], value: Any) -> None:
    cur = cfg
    for key in path[:-1]:
        cur = cur[key]
    leaf = cur[path[-1]]
    if isinstance(leaf, dict) and "value" in leaf:
        leaf["value"] = value
    else:
        cur[path[-1]] = value


def _set_seed_msm_fraction(cfg: dict, value: float) -> dict:
    cfg = copy.deepcopy(cfg)
    _set_value(cfg, ["population", "epidemic_seed", "msm_fraction"], float(value))
    return cfg


def _disable_prep(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    for key in ["oral_prep", "lenacapavir", "Oral_PrEP", "Lenacapavir"]:
        if key in cfg.get("interventions", {}):
            cfg["interventions"][key].setdefault("enabled", {})["value"] = False
    return cfg


def _make_non_msm_migrant_heavy(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    mh = cfg["demography"]["migration"]["migrant_hiv"]
    mh["orientation_distribution"]["value"] = {
        "MSM": 0.0,
        "Heterosexual_Male": 0.5,
        "Female": 0.5,
    }
    return cfg


def _run(cfg: dict, seed: int, start_year: int, end_year: int) -> pd.DataFrame:
    cfg = copy.deepcopy(cfg)
    _set_value(cfg, ["run_control", "start_year"], start_year)
    _set_value(cfg, ["run_control", "end_year"], end_year)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = SimulationEngineV3(cfg, seed=seed).run(run_number=1)
    return result["summary"].copy()


def _annualize(summary: pd.DataFrame, scenario: str, start_year: int, end_year: int) -> pd.DataFrame:
    work = summary.sort_values("year").copy()
    work["calib_year"] = work["year"].astype(float).apply(int)
    work = work[(work["calib_year"] >= start_year) & (work["calib_year"] <= end_year)]

    rows: list[dict[str, Any]] = []
    flow_cols = [
        "new_infections",
        "new_infections_msm",
        "new_infections_non_msm",
        "new_diagnoses",
        "new_diagnoses_msm",
        "aids_deaths",
        "new_aids_cases",
        "art_initiations",
        "migrant_hiv_entries",
        "migrant_hiv_msm_entries",
        "migrant_hiv_non_msm_entries",
        "migrant_hiv_hetero_male_entries",
        "migrant_hiv_female_entries",
        "migrant_hiv_on_art_entries",
    ]
    stock_cols = [
        "sexually_active_population",
        "prevalence",
        "on_art_stock",
        "msm_hiv_pos",
        "non_msm_hiv_pos",
        "msm_undiagnosed_hiv_pos",
        "non_msm_undiagnosed_hiv_pos",
        "msm_total",
        "msm_on_oral",
        "msm_on_lena",
    ]
    for year, year_df in work.groupby("calib_year", sort=True):
        final = year_df.iloc[-1]
        row: dict[str, Any] = {"scenario": scenario, "year": int(year)}
        for col in flow_cols:
            row[col] = float(year_df[col].sum()) if col in year_df.columns else math.nan
        for col in stock_cols:
            row[col] = float(final[col]) if col in final.index else math.nan
        row["PLHIV"] = (
            row["prevalence"] * row["sexually_active_population"]
            if not math.isnan(row["prevalence"]) and not math.isnan(row["sexually_active_population"])
            else math.nan
        )
        row["new_diagnoses_non_msm"] = row["new_diagnoses"] - row["new_diagnoses_msm"]
        rows.append(row)
    return pd.DataFrame(rows)


def _target_non_msm(targets_path: Path) -> pd.DataFrame:
    targets = pd.read_csv(targets_path)
    diag = targets[targets["model_output"].isin(["new_diagnoses", "new_diagnoses_msm"])]
    wide = diag.pivot_table(
        index="year",
        columns="model_output",
        values="estimate",
        aggfunc="first",
    ).reset_index()
    wide["target_non_msm_diagnoses"] = wide["new_diagnoses"] - wide["new_diagnoses_msm"]
    return wide


def _particle_summary(particles_path: Path) -> pd.DataFrame:
    df = pd.read_csv(particles_path, low_memory=False)
    df = df[~df["collapsed"].astype(bool)].copy()
    df["non_msm_dx_model"] = (
        df["acceptance_diagnosis_gate_new_diagnoses_model"]
        - df["acceptance_diagnosis_gate_new_diagnoses_msm_model"]
    )
    df["non_msm_dx_target"] = (
        df["acceptance_diagnosis_gate_new_diagnoses_target"]
        - df["acceptance_diagnosis_gate_new_diagnoses_msm_target"]
    )
    df["non_msm_dx_ratio"] = df["non_msm_dx_model"] / df["non_msm_dx_target"]
    keep = [
        "particle_id",
        "generation",
        "abc_distance",
        "total_ll",
        "overall_accept",
        "non_msm_diag_passed",
        "acceptance_n_failures",
        "acceptance_fail_reasons",
        "hist_n_failures",
        "hist_fail_reasons",
        "acceptance_diagnosis_gate_new_diagnoses_model",
        "acceptance_diagnosis_gate_new_diagnoses_msm_model",
        "non_msm_dx_model",
        "non_msm_dx_target",
        "non_msm_dx_ratio",
        "non_msm_diag_recent_mape",
        "non_msm_diag_hist_mape",
        "seed_msm_fraction",
        "seed_prevalence",
        "epsilon_hetero",
        "epsilon_msm",
        "diagnosis_2015",
        "msm_diag_mult_2015",
        "p_msm",
    ]
    keep = [col for col in keep if col in df.columns]
    return df.sort_values(["abc_distance", "total_ll"], ascending=[True, False])[keep]


def _write_report(
    outdir: Path,
    annual: pd.DataFrame,
    particles: pd.DataFrame,
    targets: pd.DataFrame,
    rank1_path: Path,
) -> None:
    rank = annual[annual["scenario"] == "rank001"].copy()
    recent = rank[(rank["year"] >= 2015) & (rank["year"] <= 2024)]
    non_msm_positive_recent = int((recent["new_diagnoses_non_msm"] > 0).sum())
    first_zero_recent = recent.loc[recent["new_diagnoses_non_msm"] == 0, "year"].min()
    particle_nonzero = int((particles["non_msm_dx_model"] > 0).sum())
    particle_n = len(particles)
    target_2024 = targets.loc[targets["year"] == 2024, "target_non_msm_diagnoses"]
    target_2024_value = float(target_2024.iloc[0]) if not target_2024.empty else math.nan
    rank_2024 = rank.loc[rank["year"] == 2024]
    rank_2024_non_msm = (
        float(rank_2024["new_diagnoses_non_msm"].iloc[0])
        if not rank_2024.empty
        else math.nan
    )
    migrant_recent = recent["migrant_hiv_non_msm_entries"].sum()

    lines = [
        "# Non-MSM Diagnosis Pipeline Audit",
        "",
        "## Inputs",
        "",
        f"- Rank-1 config: `{rank1_path}`",
        "- Production behavior was not changed by this audit.",
        "",
        "## Key Findings",
        "",
        (
            f"- Particle table: `{particle_nonzero}` of `{particle_n}` noncollapsed "
            "particles generated positive derived 2024 non-MSM diagnoses."
        ),
        (
            f"- Rank-1 recent years with positive non-MSM diagnoses: "
            f"`{non_msm_positive_recent}` of `10` for 2015-2024."
        ),
        (
            f"- Rank-1 first zero non-MSM diagnosis year in the recent window: "
            f"`{int(first_zero_recent) if not math.isnan(first_zero_recent) else 'none'}`."
        ),
        (
            f"- Rank-1 2024 non-MSM diagnoses: model `{rank_2024_non_msm:.0f}` "
            f"vs target `{target_2024_value:.0f}`."
        ),
        (
            f"- Rank-1 2015-2024 non-MSM migrant HIV entries: "
            f"`{migrant_recent:.0f}`. These are represented as pre-arrival "
            "diagnosed entries and are not included in `new_diagnoses`."
        ),
        "",
        "## Classification",
        "",
        "- Low-level diagnosis accounting should be checked by `tests/test_non_msm_diagnosis_pipeline_audit.py`.",
        "- If those tests pass, the immediate failure is not total/MSM diagnosis counter arithmetic.",
        "- The calibrated failure mode is structural/accounting: recent non-MSM infections and undiagnosed non-MSM PLHIV collapse to zero, while non-MSM diagnosed migrant entries continue to enter outside the diagnosis counter.",
        "",
        "## Artifacts",
        "",
        "- `annual_non_msm_diagnosis_decomposition.csv`",
        "- `target_non_msm_diagnosis_series.csv`",
        "- `particle_non_msm_gate_summary.csv`",
    ]
    (outdir / "NON_MSM_DIAGNOSIS_PIPELINE_AUDIT.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rank1-config", type=Path, default=DEFAULT_RANK1)
    parser.add_argument("--particles", type=Path, default=DEFAULT_PARTICLES)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--seed", type=int, default=1322 + 4966)
    parser.add_argument("--start-year", type=int, default=1975)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(args.rank1_config.read_text())

    scenarios = {
        "rank001": cfg,
        "seed_msm_fraction_0_80": _set_seed_msm_fraction(cfg, 0.80),
        "seed_msm_fraction_0_50_extreme_audit": _set_seed_msm_fraction(cfg, 0.50),
        "prep_disabled": _disable_prep(cfg),
        "non_msm_migrant_heavy": _make_non_msm_migrant_heavy(cfg),
    }
    annual_frames = []
    for name, scenario_cfg in scenarios.items():
        summary = _run(scenario_cfg, args.seed, args.start_year, args.end_year)
        annual_frames.append(_annualize(summary, name, args.start_year, args.end_year))

    annual = pd.concat(annual_frames, ignore_index=True)
    targets = _target_non_msm(args.targets)
    particles = _particle_summary(args.particles)

    annual.to_csv(args.output_dir / "annual_non_msm_diagnosis_decomposition.csv", index=False)
    targets.to_csv(args.output_dir / "target_non_msm_diagnosis_series.csv", index=False)
    particles.to_csv(args.output_dir / "particle_non_msm_gate_summary.csv", index=False)
    _write_report(args.output_dir, annual, particles, targets, args.rank1_config)

    print(f"Wrote audit artifacts to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
