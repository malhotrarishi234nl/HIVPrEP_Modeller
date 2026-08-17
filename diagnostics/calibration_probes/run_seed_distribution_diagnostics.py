#!/usr/bin/env python3
"""
Diagnostics-only fixed-total seed stage-distribution probe.

This tests whether redistributing the existing 1980 epidemic seed across Acute,
Chronic, and AIDS stages improves early AIDS fit without adding extra seeded
people. Candidate configs are copied in memory and production model behavior is
not changed.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_calibration_acceptance import candidate_metadata, load_json, set_nested
from calib_acceptance_gates import (
    AcceptanceGateSettings,
    acceptance_gate_row_fields,
    evaluate_acceptance_gates,
)
from calib_extractor import extract_annual_outputs
from config_utils import extract_value
from run_historical_seed_diagnostics import add_annual_stage_stocks, stage_stocks
from run_v3 import SimulationEngineV3
CALIB_START_YEAR = 1980
CALIB_END_YEAR = 2025


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "acute100",
        "shares": {"Acute": 1.00, "Chronic": 0.00, "AIDS": 0.00},
        "rationale": "Diagnostics reproduction of current all-acute seed.",
    },
    {
        "scenario": "acute95_chronic05",
        "shares": {"Acute": 0.95, "Chronic": 0.05, "AIDS": 0.00},
        "rationale": "Very conservative infection-age mix.",
    },
    {
        "scenario": "acute90_chronic10",
        "shares": {"Acute": 0.90, "Chronic": 0.10, "AIDS": 0.00},
        "rationale": "Conservative chronic historical stock.",
    },
    {
        "scenario": "acute80_chronic20",
        "shares": {"Acute": 0.80, "Chronic": 0.20, "AIDS": 0.00},
        "rationale": "Upper conservative chronic-only mix.",
    },
    {
        "scenario": "acute95_aids05",
        "shares": {"Acute": 0.95, "Chronic": 0.00, "AIDS": 0.05},
        "rationale": "Small prevalent AIDS stock bound.",
    },
    {
        "scenario": "acute90_chronic05_aids05",
        "shares": {"Acute": 0.90, "Chronic": 0.05, "AIDS": 0.05},
        "rationale": "Small mixed mature stock.",
    },
    {
        "scenario": "acute85_chronic10_aids05",
        "shares": {"Acute": 0.85, "Chronic": 0.10, "AIDS": 0.05},
        "rationale": "More mature but still bounded distribution.",
    },
    {
        "scenario": "acute75_chronic20_aids05",
        "shares": {"Acute": 0.75, "Chronic": 0.20, "AIDS": 0.05},
        "rationale": "Stress test within the no-multiplier distribution family.",
    },
)


def validate_shares(shares: dict[str, float]) -> None:
    total = sum(float(value) for value in shares.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"Seed stage shares must sum to 1.0, got {total}")
    for stage, value in shares.items():
        if stage not in {"Acute", "Chronic", "AIDS"}:
            raise ValueError(f"Unsupported seed stage: {stage}")
        if value < 0:
            raise ValueError(f"Seed stage share cannot be negative: {stage}={value}")


def apply_distribution_seed(engine: SimulationEngineV3, shares: dict[str, float]) -> int:
    """Apply a fixed-total epidemic seed split across HIV stages."""
    validate_shares(shares)
    susc_idx = engine.state.groups.hiv_to_idx["Susceptible"]
    undiag_idx = 0
    no_art_idx = 0
    no_prep_idx = 0

    msm_orient_idx = engine.state.groups.orient_to_idx.get("MSM")
    hetero_m_orient_idx = engine.state.groups.orient_to_idx.get("Heterosexual_Male")
    female_orient_idx = engine.state.groups.orient_to_idx.get("Female")
    msm_frac = engine._epidemic_seed_msm_frac
    het_frac = 1.0 - msm_frac
    total_seeded = 0

    def count_susceptibles(orient_idx):
        if orient_idx is None:
            return 0
        return int(np.sum(
            engine.state.population[:, orient_idx, :, susc_idx, undiag_idx, no_art_idx, no_prep_idx]
        ))

    def stage_counts(n_to_seed: int) -> dict[str, int]:
        raw = {stage: n_to_seed * float(share) for stage, share in shares.items()}
        counts = {stage: int(value) for stage, value in raw.items()}
        remainder = n_to_seed - sum(counts.values())
        if remainder:
            order = sorted(raw, key=lambda stage: raw[stage] - counts[stage], reverse=True)
            for stage in order[:remainder]:
                counts[stage] += 1
        return counts

    def apply_stage_orient_seed(orient_idx, n_to_seed: int):
        nonlocal total_seeded
        if orient_idx is None or n_to_seed <= 0:
            return
        susc_slice = engine.state.population[
            :, orient_idx, :, susc_idx, undiag_idx, no_art_idx, no_prep_idx
        ].copy()
        total_susc = int(np.sum(susc_slice))
        if total_susc <= 0:
            return
        n_to_seed = min(n_to_seed, total_susc)
        weights = susc_slice / total_susc
        for stage, stage_n in stage_counts(n_to_seed).items():
            if stage_n <= 0:
                continue
            allocs = (weights * stage_n).astype(int)
            remainder = stage_n - int(np.sum(allocs))
            if remainder > 0:
                largest = np.unravel_index(np.argmax(susc_slice), susc_slice.shape)
                allocs[largest] += remainder
            stage_idx = engine.state.groups.hiv_to_idx[stage]
            for age_idx in range(engine.state.groups.n_age_groups):
                for act_idx in range(engine.state.groups.n_activities):
                    n = int(allocs[age_idx, act_idx])
                    if n <= 0:
                        continue
                    engine.state.population[
                        age_idx, orient_idx, act_idx,
                        susc_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] -= n
                    engine.state.population[
                        age_idx, orient_idx, act_idx,
                        stage_idx, undiag_idx, no_art_idx, no_prep_idx
                    ] += n
                    total_seeded += n

    n_msm_susc = count_susceptibles(msm_orient_idx)
    n_hetm_susc = count_susceptibles(hetero_m_orient_idx)
    n_fem_susc = count_susceptibles(female_orient_idx)
    n_het_susc = n_hetm_susc + n_fem_susc
    n_seed_msm = int(n_msm_susc * engine._epidemic_seed_prev * msm_frac)
    n_seed_het = int(n_het_susc * engine._epidemic_seed_prev * het_frac)

    apply_stage_orient_seed(msm_orient_idx, n_seed_msm)
    if n_seed_het > 0 and n_het_susc > 0:
        n_seed_fem = int(n_seed_het * (n_fem_susc / max(n_het_susc, 1)))
        n_seed_hetm = n_seed_het - n_seed_fem
        apply_stage_orient_seed(female_orient_idx, n_seed_fem)
        apply_stage_orient_seed(hetero_m_orient_idx, n_seed_hetm)
    return total_seeded


def run_candidate_with_distribution_seed(
    config: dict,
    seed: int,
    deterministic: bool,
    shares: dict[str, float],
) -> dict[str, Any]:
    config = copy.deepcopy(config)
    if deterministic:
        set_nested(config, "run_control.deterministic_mode.value", True)
    set_nested(config, "run_control.start_year.value", CALIB_START_YEAR)
    set_nested(config, "run_control.end_year.value", CALIB_END_YEAR + 1)
    set_nested(config, "output.enabled.value", False)
    set_nested(config, "output.terminal_summary.value", False)
    set_nested(config, "population.epidemic_seed.enabled.value", False)

    stage_rows: list[dict[str, float]] = []
    seed_year = float(extract_value(config["population"]["epidemic_seed"]["seed_year"]))
    seed_fired = False
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(config, seed=seed)
        # Preserve seed controls from the original config even though built-in
        # seeding is disabled for this diagnostics-only distribution seed.
        seed_block = config["population"]["epidemic_seed"]
        engine._epidemic_seed_enabled = False
        engine._epidemic_seed_year = seed_year
        engine._epidemic_seed_prev = float(extract_value(seed_block["seed_prevalence"]))
        engine._epidemic_seed_msm_frac = float(extract_value(seed_block["msm_fraction"]))
        for step in range(engine.time.n_steps):
            current_year = engine.time.start_year + (step * engine.time.years_per_step)
            if not seed_fired and current_year >= seed_year:
                apply_distribution_seed(engine, shares)
                seed_fired = True
            engine.step_simulation(step)
            engine.validate_prep_mass_balance(step)
            engine.validate_art_mass_balance(step)
            year = engine.time.start_year + (step * engine.time.years_per_step)
            stage_rows.append({"year": year, **stage_stocks(engine)})
    return {
        "summary": pd.DataFrame(engine.outputs),
        "stage_stocks": pd.DataFrame(stage_rows),
        "final_state": engine.state.population.copy(),
        "seed": seed,
    }


def build_rows(args: argparse.Namespace) -> pd.DataFrame:
    targets = pd.read_csv(args.normalized_targets_csv).to_dict("records")
    settings = AcceptanceGateSettings()
    rows: list[dict[str, Any]] = []
    selected_years = (1985, 1990, 1993, 1995, 1996, 1997, 2000, 2005, 2010, 2015, 2020, 2023)

    for config_idx, config_path in enumerate(args.configs, start=1):
        source_config = load_json(config_path)
        seed_prev = float(extract_value(source_config["population"]["epidemic_seed"]["seed_prevalence"]))
        for scenario_idx, scenario in enumerate(SCENARIOS, start=1):
            seed = args.seed + config_idx * 100 + scenario_idx
            result = run_candidate_with_distribution_seed(
                source_config,
                seed=seed,
                deterministic=args.deterministic,
                shares=scenario["shares"],
            )
            outputs = extract_annual_outputs(result, CALIB_START_YEAR, CALIB_END_YEAR)
            add_annual_stage_stocks(outputs, result["stage_stocks"])
            acceptance = evaluate_acceptance_gates(outputs, targets, settings=settings)
            row = {
                "config_path": str(Path(config_path).resolve()),
                "config_name": Path(config_path).name,
                "scenario": scenario["scenario"],
                "scenario_rationale": scenario["rationale"],
                "seed_prevalence": seed_prev,
                "seed_acute_share": scenario["shares"]["Acute"],
                "seed_chronic_share": scenario["shares"]["Chronic"],
                "seed_aids_share": scenario["shares"]["AIDS"],
                "audit_deterministic_runtime": bool(args.deterministic),
                **candidate_metadata(source_config),
                **acceptance_gate_row_fields(acceptance),
            }
            for output in (
                "PLHIV",
                "new_infections",
                "new_diagnoses",
                "new_diagnoses_msm",
                "new_aids_cases",
                "aids_deaths",
                "on_art_stock",
                "aids_stock",
                "aids_stock_off_art",
                "aids_stock_on_art",
            ):
                for year in selected_years:
                    if year in outputs.get(output, {}):
                        row[f"{output}_{year}"] = outputs[output][year]
            rows.append(row)
    return pd.DataFrame(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diagnostics-only fixed-total seed stage-distribution scenarios."
    )
    parser.add_argument("configs", nargs="+", help="Candidate config JSON paths.")
    parser.add_argument("--normalized-targets-csv", required=True)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "outputs" / "calibration" / "seed_distribution_diagnostics_v13_6.csv"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--stochastic", dest="deterministic", action="store_false")
    args = parser.parse_args(argv)
    if not Path(args.normalized_targets_csv).exists():
        parser.error(f"--normalized-targets-csv not found: {args.normalized_targets_csv}")
    missing = [path for path in args.configs if not Path(path).exists()]
    if missing:
        parser.error(f"Config path(s) not found: {', '.join(missing)}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    df = build_rows(args)
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"Seed distribution diagnostic written: {output_csv}")
    display_cols = [
        "config_name",
        "scenario",
        "overall_accept",
        "diagnosis_gate_pass",
        "plhiv_gate_pass",
        "incidence_gate_pass",
        "aids_death_gate_pass",
        "art_stock_gate_pass",
        "aids_stock_1995",
        "aids_deaths_1995",
        "new_aids_cases_1995",
        "aids_deaths_1997",
        "PLHIV_2023",
    ]
    print(df[[col for col in display_cols if col in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
