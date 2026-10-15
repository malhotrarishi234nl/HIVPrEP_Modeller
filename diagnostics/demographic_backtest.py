#!/usr/bin/env python3
"""
Demographic engine backtest diagnostics.

This script is intentionally read-only with respect to model behavior. It runs
the existing engine, instruments demographic state in memory, and writes CSV
diagnostics that separate:

1. Annual model births/deaths/migration versus HMD vital stats.
2. Excess adult deaths into Jan-1-stock and deaths-per-stock components.
3. Current fractional aging cohort leakage versus idealized 5-year bin movement.

Run from the repository root:
    python3 diagnostics/demographic_backtest.py --config 1980_test.json
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import io
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_v3 import SimulationEngineV3
from config_utils import extract_value


DEFAULT_CONFIG = ROOT / "configs" / "1980_test.json"
DEFAULT_OUT_DIR = ROOT / "outputs" / "diagnostics" / "demographic_backtest"
HMD_POP_PATH = ROOT.parent / "data" / "raw" / "validation" / "HMD_NL_Pop.txt"
HMD_DEATH_PATH = ROOT / "Validation " / "HMD_NL_Death.txt"
HMD_VITAL_PATH = ROOT / "Validation " / "HMD_NL_VitalStats_1970_2023.csv"


def _age_group_sort_key(label: str) -> int:
    if label.endswith("+"):
        return int(label[:-1])
    return int(label.split("-")[0])


def _model_age_label(age: int, age_groups: List[str], age_min: List[int], age_max: List[int]) -> str | None:
    for label, lo, hi in zip(age_groups, age_min, age_max):
        if lo <= age <= hi:
            return label
    if age >= age_min[-1]:
        return age_groups[-1]
    return None


def _aggregate_100_plus(label: str) -> str:
    return "100+" if _age_group_sort_key(label) >= 100 else label


def _split_counts(total: float, weights: Iterable[float]) -> List[int]:
    weights_arr = np.asarray(list(weights), dtype=float)
    if weights_arr.sum() <= 0:
        weights_arr = np.ones_like(weights_arr) / len(weights_arr)
    else:
        weights_arr = weights_arr / weights_arr.sum()

    raw = float(total) * weights_arr
    base = np.floor(raw).astype(int)
    remainder = int(round(float(total))) - int(base.sum())
    if remainder > 0:
        order = np.argsort(-(raw - base), kind="stable")
        for idx in order[:remainder]:
            base[idx] += 1
    return base.tolist()


def load_hmd_vitals(path: Path) -> Dict[int, dict]:
    rows: Dict[int, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            year = int(row["year"])
            rows[year] = {
                "births": int(row["births_hmd"]) if row.get("births_hmd") else None,
                "deaths": int(row["deaths_hmd"]) if row.get("deaths_hmd") else None,
            }
    return rows


def load_hmd_age_sex_population(
    path: Path,
    years: Iterable[int],
    age_groups: List[str],
    age_min: List[int],
    age_max: List[int],
    aggregate_100_plus: bool = False,
) -> Dict[int, Dict[str, Dict[str, int]]]:
    wanted = set(years)
    out: Dict[int, Dict[str, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"female": 0, "male": 0})
    )
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Type", "").strip() != "O":
                continue
            if row.get("Sex", "").strip() not in {"m", "f"}:
                continue
            year = int(row["Year"])
            if year not in wanted:
                continue
            age_raw = row["Age"].strip()
            if age_raw in {"TOT", "UNK"}:
                continue
            try:
                age = int(age_raw.rstrip("+"))
            except ValueError:
                continue
            label = _model_age_label(age, age_groups, age_min, age_max)
            if label is None:
                continue
            if aggregate_100_plus:
                label = _aggregate_100_plus(label)
            sex = "male" if row["Sex"].strip() == "m" else "female"
            out[year][label][sex] += int(float(row["Population"]))
    return out


def load_hmd_age_sex_deaths(
    path: Path,
    years: Iterable[int],
    age_groups: List[str],
    age_min: List[int],
    age_max: List[int],
    aggregate_100_plus: bool = True,
) -> Dict[int, Dict[str, Dict[str, int]]]:
    wanted = set(years)
    out: Dict[int, Dict[str, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"female": 0, "male": 0})
    )
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Access", "").strip() != "O":
                continue
            if row.get("Sex", "").strip() not in {"m", "f"}:
                continue
            year = int(row["Year"])
            if year not in wanted:
                continue
            age_raw = row["Age"].strip()
            if age_raw in {"TOT", "UNK"}:
                continue
            try:
                age = int(age_raw.rstrip("+"))
            except ValueError:
                continue
            label = _model_age_label(age, age_groups, age_min, age_max)
            if label is None:
                continue
            if aggregate_100_plus:
                label = _aggregate_100_plus(label)
            sex = "male" if row["Sex"].strip() == "m" else "female"
            out[year][label][sex] += int(float(row["Deaths"]))
    return out


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        config = json.load(f)
    config = copy.deepcopy(config)
    config["run_control"]["deterministic_mode"]["value"] = True
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def run_instrumented_model(config_path: Path):
    config = load_config(config_path)
    adult_death_records = []
    adult_stock_records = []

    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(config)
        original_step = engine.step_simulation
        original_hiv_mortality = engine._step_hiv_mortality
        original_mortality = engine.demography.calculate_background_mortality
        hiv_mortality_call = {"step": 0}

        def adult_age_sex_arrays(population):
            female_idx = engine.groups.orient_to_idx["Female"]
            hetero_idx = engine.groups.orient_to_idx["Heterosexual_Male"]
            msm_idx = engine.groups.orient_to_idx["MSM"]
            return {
                "female": population[:, female_idx, :, :, :, :, :].sum(axis=(1, 2, 3, 4, 5)),
                "male": (
                    population[:, hetero_idx, :, :, :, :, :].sum(axis=(1, 2, 3, 4, 5))
                    + population[:, msm_idx, :, :, :, :, :].sum(axis=(1, 2, 3, 4, 5))
                ),
            }

        def wrapped_step(step: int):
            current_year = engine.time.start_year + step * engine.time.years_per_step
            if abs(current_year - round(current_year)) < 1e-9:
                stock = adult_age_sex_arrays(engine.state.population)
                adult_stock_records.append(
                    {
                        "year": int(round(current_year)),
                        "female_pop": stock["female"].astype(float),
                        "male_pop": stock["male"].astype(float),
                    }
                )
            return original_step(step)

        def wrapped_hiv_mortality():
            current_year = engine.time.start_year + hiv_mortality_call["step"] * engine.time.years_per_step
            hiv_mortality_call["step"] += 1

            disease_config = engine.config["disease"]
            hiv_deaths = 0
            aids_deaths = 0
            female_deaths = np.zeros(engine.groups.n_age_groups, dtype=float)
            male_deaths = np.zeros(engine.groups.n_age_groups, dtype=float)

            female_idx = engine.groups.orient_to_idx["Female"]
            hetero_idx = engine.groups.orient_to_idx["Heterosexual_Male"]
            msm_idx = engine.groups.orient_to_idx["MSM"]

            hiv_mortality = {
                "Acute": engine.time.annual_to_step_rate(extract_value(disease_config["mortality"]["acute"])),
                "Chronic": engine.time.annual_to_step_rate(extract_value(disease_config["mortality"]["chronic"])),
                "AIDS": engine.time.annual_to_step_rate(extract_value(disease_config["mortality"]["aids"])),
            }
            art_reduction = extract_value(disease_config["treatment"]["art_mortality_reduction"])

            for stage_name, mortality_rate in hiv_mortality.items():
                stage_idx = engine.groups.hiv_to_idx[stage_name]

                not_art = engine.state.population[:, :, :, stage_idx, :, 0, :]
                deaths_not_art = engine._draw_events(not_art, mortality_rate)
                engine.state.population[:, :, :, stage_idx, :, 0, :] -= deaths_not_art

                on_art = engine.state.population[:, :, :, stage_idx, :, 1, :]
                reduced_rate = mortality_rate * (1 - art_reduction)
                deaths_art = engine._draw_events(on_art, reduced_rate)
                engine.state.population[:, :, :, stage_idx, :, 1, :] -= deaths_art

                stage_deaths = deaths_not_art + deaths_art
                female_deaths += stage_deaths[:, female_idx, :, :, :].sum(axis=(1, 2, 3))
                male_deaths += (
                    stage_deaths[:, hetero_idx, :, :, :].sum(axis=(1, 2, 3))
                    + stage_deaths[:, msm_idx, :, :, :].sum(axis=(1, 2, 3))
                )

                step_deaths = int(np.sum(deaths_not_art) + np.sum(deaths_art))
                hiv_deaths += step_deaths
                if stage_name == "AIDS":
                    aids_deaths += step_deaths

            adult_death_records.append(
                {
                    "year": float(current_year),
                    "source": "hiv",
                    "female_deaths": female_deaths,
                    "male_deaths": male_deaths,
                }
            )
            return {"total": hiv_deaths, "aids": aids_deaths}

        def wrapped_background_mortality(population, current_year, rng, deterministic=False):
            deaths = original_mortality(population, current_year, rng, deterministic)
            death_stock = adult_age_sex_arrays(deaths)
            adult_death_records.append(
                {
                    "year": float(current_year),
                    "source": "background",
                    "female_deaths": death_stock["female"],
                    "male_deaths": death_stock["male"],
                }
            )
            return deaths

        engine.step_simulation = wrapped_step
        engine._step_hiv_mortality = wrapped_hiv_mortality
        engine.demography.calculate_background_mortality = wrapped_background_mortality
        result = engine.run()
        engine._step_hiv_mortality = original_hiv_mortality

    return engine, result["summary"], adult_death_records, adult_stock_records


def write_annual_flow_comparison(summary_df, vital_rows: Dict[int, dict], out_path: Path) -> None:
    df = summary_df.copy()
    df["year_int"] = np.floor(df["year"] + 1e-9).astype(int)
    annual = df.groupby("year_int").agg(
        {
            "births": "sum",
            "background_deaths": "sum",
            "childhood_deaths": "sum",
            "hiv_deaths": "sum",
            "net_migration": "sum",
            "total_population": "last",
            "sexually_active_population": "last",
            "childhood_population": "last",
        }
    ).reset_index()

    rows = []
    for _, row in annual.iterrows():
        year = int(row["year_int"])
        observed = vital_rows.get(year, {})
        model_deaths = int(row["background_deaths"] + row["childhood_deaths"] + row["hiv_deaths"])
        obs_deaths = observed.get("deaths")
        obs_births = observed.get("births")
        rows.append(
            {
                "year": year,
                "model_deaths": model_deaths,
                "observed_deaths": obs_deaths,
                "death_excess": None if obs_deaths is None else model_deaths - obs_deaths,
                "death_excess_pct": None if obs_deaths is None else (model_deaths - obs_deaths) / obs_deaths * 100.0,
                "model_births": int(row["births"]),
                "observed_births": obs_births,
                "birth_gap": None if obs_births is None else int(row["births"]) - obs_births,
                "net_migration": int(row["net_migration"]),
                "total_population_end_year": int(row["total_population"]),
                "sexually_active_population_end_year": int(row["sexually_active_population"]),
                "childhood_population_end_year": int(row["childhood_population"]),
            }
        )
    write_csv(out_path, rows)


def adult_model_year_stock(
    engine: SimulationEngineV3,
    adult_death_records: List[dict],
    adult_stock_records: List[dict],
    start_year: int,
    end_year: int,
) -> Tuple[dict, dict]:
    deaths = defaultdict(lambda: defaultdict(lambda: {"female": 0.0, "male": 0.0}))
    stock = defaultdict(lambda: defaultdict(lambda: {"female": 0.0, "male": 0.0}))

    for record in adult_death_records:
        year = int(math.floor(record["year"] + 1e-9))
        if not (start_year <= year <= end_year):
            continue
        for age_idx, label in enumerate(engine.groups.age_groups):
            agg_label = _aggregate_100_plus(label)
            deaths[year][agg_label]["female"] += float(record["female_deaths"][age_idx])
            deaths[year][agg_label]["male"] += float(record["male_deaths"][age_idx])

    for record in adult_stock_records:
        year = int(record["year"])
        if not (start_year <= year <= end_year):
            continue
        for age_idx, label in enumerate(engine.groups.age_groups):
            agg_label = _aggregate_100_plus(label)
            stock[year][agg_label]["female"] += float(record["female_pop"][age_idx])
            stock[year][agg_label]["male"] += float(record["male_pop"][age_idx])
    return deaths, stock


def write_death_decomposition(
    engine: SimulationEngineV3,
    adult_death_records: List[dict],
    adult_stock_records: List[dict],
    start_year: int,
    end_year: int,
    out_path: Path,
) -> None:
    years = range(start_year, end_year + 1)
    model_deaths, model_stock = adult_model_year_stock(
        engine, adult_death_records, adult_stock_records, start_year, end_year
    )
    hmd_deaths = load_hmd_age_sex_deaths(
        HMD_DEATH_PATH,
        years,
        engine.groups.age_groups,
        engine.groups.age_min,
        engine.groups.age_max,
        aggregate_100_plus=True,
    )
    hmd_stock = load_hmd_age_sex_population(
        HMD_POP_PATH,
        years,
        engine.groups.age_groups,
        engine.groups.age_min,
        engine.groups.age_max,
        aggregate_100_plus=True,
    )

    rows = []
    labels = sorted(
        {
            label
            for year in years
            for source in (model_deaths, hmd_deaths)
            for label in source[year].keys()
        },
        key=_age_group_sort_key,
    )
    for year in years:
        for label in labels:
            for sex in ("female", "male"):
                md = model_deaths[year][label][sex]
                ms = model_stock[year][label][sex]
                hd = hmd_deaths[year][label][sex]
                hs = hmd_stock[year][label][sex]
                obs_rate = hd / hs if hs > 0 else 0.0
                model_rate = md / ms if ms > 0 else 0.0
                expected_model_deaths_at_obs_rate = ms * obs_rate
                rows.append(
                    {
                        "year": year,
                        "age_group": label,
                        "sex": sex,
                        "model_deaths": round(md, 6),
                        "observed_deaths": round(hd, 6),
                        "death_excess": round(md - hd, 6),
                        "model_jan1_stock": round(ms, 6),
                        "observed_jan1_stock": round(hs, 6),
                        "stock_ratio": None if hs <= 0 else ms / hs,
                        "model_deaths_per_jan1_stock": model_rate,
                        "observed_deaths_per_jan1_stock": obs_rate,
                        "rate_ratio_jan1_stock": None if obs_rate <= 0 else model_rate / obs_rate,
                        "deaths_per_jan1_stock_component": round(md - expected_model_deaths_at_obs_rate, 6),
                        "jan1_stock_component": round(expected_model_deaths_at_obs_rate - hd, 6),
                    }
                )
    write_csv(out_path, rows)


def config_mortality_rate(engine: SimulationEngineV3, age_label: str, sex: str) -> float:
    survival_data = engine.demography.life_table_config["survival_by_age_limit"]["value"]
    if age_label == "100+":
        component_labels = [
            label for label in engine.groups.age_groups
            if _age_group_sort_key(label) >= 100
        ]
        rates = [config_mortality_rate(engine, label, sex) for label in component_labels]
        return float(np.mean(rates)) if rates else 0.0
    else:
        idx = engine.groups.age_to_idx[age_label]
        lo, hi = engine.groups.age_min[idx], engine.groups.age_max[idx]
    midpoint = (lo + hi) / 2.0
    key = "female_survival" if sex == "female" else "male_survival"
    survival = 1.0
    for entry in survival_data:
        if midpoint < entry["age_limit"]:
            survival = float(entry.get(key, 1.0))
            break
    return 1.0 - survival


def write_replay_summary(
    engine: SimulationEngineV3,
    adult_death_records: List[dict],
    adult_stock_records: List[dict],
    start_year: int,
    end_year: int,
    out_path: Path,
) -> None:
    years = range(start_year, end_year + 1)
    model_deaths, model_stock = adult_model_year_stock(
        engine, adult_death_records, adult_stock_records, start_year, end_year
    )
    hmd_deaths = load_hmd_age_sex_deaths(
        HMD_DEATH_PATH, years, engine.groups.age_groups, engine.groups.age_min, engine.groups.age_max, True
    )
    hmd_stock = load_hmd_age_sex_population(
        HMD_POP_PATH, years, engine.groups.age_groups, engine.groups.age_min, engine.groups.age_max, True
    )

    labels = sorted(
        {label for year in years for label in set(model_stock[year]) | set(hmd_stock[year])},
        key=_age_group_sort_key,
    )
    rows = []
    for year in years:
        current_model = 0.0
        model_stock_hmd_rates = 0.0
        hmd_stock_model_rates = 0.0
        hmd_stock_model_rates_excluding_100plus = 0.0
        observed_excluding_100plus = 0.0
        observed = 0.0
        for label in labels:
            for sex in ("female", "male"):
                current_model += model_deaths[year][label][sex]
                observed += hmd_deaths[year][label][sex]
                obs_stock = hmd_stock[year][label][sex]
                obs_rate = hmd_deaths[year][label][sex] / obs_stock if obs_stock > 0 else 0.0
                model_stock_hmd_rates += model_stock[year][label][sex] * obs_rate
                model_rate_deaths = hmd_stock[year][label][sex] * config_mortality_rate(engine, label, sex)
                hmd_stock_model_rates += model_rate_deaths
                if label != "100+":
                    hmd_stock_model_rates_excluding_100plus += model_rate_deaths
                    observed_excluding_100plus += hmd_deaths[year][label][sex]
        rows.append(
            {
                "year": year,
                "current_model_adult_deaths": round(current_model, 6),
                "observed_hmd_adult_deaths": round(observed, 6),
                "current_model_excess": round(current_model - observed, 6),
                "model_jan1_stock_with_hmd_rates": round(model_stock_hmd_rates, 6),
                "model_jan1_stock_with_hmd_rates_excess": round(model_stock_hmd_rates - observed, 6),
                "hmd_jan1_stock_with_model_rates_approx": round(hmd_stock_model_rates, 6),
                "hmd_jan1_stock_with_model_rates_excess_approx": round(hmd_stock_model_rates - observed, 6),
                "hmd_jan1_stock_with_model_rates_excluding_100plus": round(hmd_stock_model_rates_excluding_100plus, 6),
                "observed_hmd_adult_deaths_excluding_100plus": round(observed_excluding_100plus, 6),
                "hmd_jan1_stock_with_model_rates_excess_excluding_100plus": round(
                    hmd_stock_model_rates_excluding_100plus - observed_excluding_100plus, 6
                ),
            }
        )
    write_csv(out_path, rows)


def write_aging_operator_benchmark(engine: SimulationEngineV3, out_path: Path) -> None:
    """
    Benchmark the legacy memoryless fractional aging operator.

    A single cohort starts in one age bin. With quarterly steps and the current
    legacy annual transfer probability, a share of that cohort can move more
    than one 5-year bin within five calendar years. This diagnostic quantifies
    the leakage that motivated residence-queue aging.
    """
    step_years = engine.time.years_per_step
    p_step = 1.0 - (1.0 - 1.0 / 5.0) ** step_years
    start_bins = ["65-69", "70-74", "75-79", "80-84", "85-89", "90-94"]
    horizons = [1, 5, 10, 20]
    cohort_size = 100_000.0
    rows = []

    for start_label in start_bins:
        start_idx = engine.groups.age_to_idx[start_label]
        for horizon in horizons:
            steps = int(round(horizon / step_years))
            vector = np.zeros(engine.groups.n_age_groups, dtype=float)
            vector[start_idx] = cohort_size
            for _ in range(steps):
                moved = np.zeros_like(vector)
                stayed = np.zeros_like(vector)
                for idx in range(engine.groups.n_age_groups - 1):
                    move = vector[idx] * p_step
                    stay = vector[idx] - move
                    stayed[idx] += stay
                    moved[idx + 1] += move
                stayed[-1] += vector[-1]
                vector = stayed + moved

            for idx, count in enumerate(vector):
                if count <= 0.001:
                    continue
                rows.append(
                    {
                        "start_age_group": start_label,
                        "horizon_years": horizon,
                        "destination_age_group": engine.groups.age_groups[idx],
                        "share_of_cohort": count / cohort_size,
                        "people_per_100k": count,
                        "bins_advanced": idx - start_idx,
                    }
                )
    write_csv(out_path, rows)


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize_outputs(out_dir: Path, start_year: int, end_year: int) -> None:
    annual_path = out_dir / "annual_flow_comparison.csv"
    replay_path = out_dir / "death_replay_summary.csv"
    age_path = out_dir / "age_death_decomposition.csv"

    with annual_path.open(encoding="utf-8", newline="") as f:
        annual_rows = list(csv.DictReader(f))
    overlap = [
        r for r in annual_rows
        if r["observed_deaths"] and start_year <= int(r["year"]) <= end_year
    ]
    death_excess = sum(float(r["death_excess"]) for r in overlap)
    model_deaths = sum(float(r["model_deaths"]) for r in overlap)
    observed_deaths = sum(float(r["observed_deaths"]) for r in overlap)
    birth_gap = sum(float(r["birth_gap"]) for r in overlap if r["birth_gap"])

    with replay_path.open(encoding="utf-8", newline="") as f:
        replay_rows = list(csv.DictReader(f))
    replay_overlap = [r for r in replay_rows if start_year <= int(r["year"]) <= end_year]
    current_excess = sum(float(r["current_model_excess"]) for r in replay_overlap)
    model_stock_hmd_rate_excess = sum(float(r["model_jan1_stock_with_hmd_rates_excess"]) for r in replay_overlap)
    hmd_stock_model_rate_excess_excl_100 = sum(
        float(r["hmd_jan1_stock_with_model_rates_excess_excluding_100plus"])
        for r in replay_overlap
    )

    with age_path.open(encoding="utf-8", newline="") as f:
        age_rows = [
            r for r in csv.DictReader(f)
            if start_year <= int(r["year"]) <= end_year
        ]
    rate_component = sum(float(r["deaths_per_jan1_stock_component"]) for r in age_rows)
    stock_component = sum(float(r["jan1_stock_component"]) for r in age_rows)

    print("Demographic backtest complete.")
    print(f"Output directory: {out_dir}")
    print()
    print(f"{start_year}-{end_year} all-age annual flow comparison:")
    print(f"  model deaths:    {model_deaths:,.0f}")
    print(f"  observed deaths: {observed_deaths:,.0f}")
    print(f"  death excess:    {death_excess:,.0f}")
    print(f"  birth gap:       {birth_gap:,.0f}")
    print()
    print(f"{start_year}-{end_year} adult death replay (Jan-1 stock denominators):")
    print(f"  current model adult excess:              {current_excess:,.0f}")
    print(f"  model Jan-1 stock + HMD rates excess:    {model_stock_hmd_rate_excess:,.0f}")
    print(f"  HMD Jan-1 stock + model rates excess*:   {hmd_stock_model_rate_excess_excl_100:,.0f}")
    print("  *Summary excludes 100+ because HMD collapses that bin and model rates vary within it.")
    print()
    print("Adult excess decomposition:")
    print(f"  Jan-1 stock component:               {stock_component:,.0f}")
    print(f"  deaths-per-Jan-1-stock component:    {rate_component:,.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run demographic engine backtest diagnostics.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Model config JSON path.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory.")
    parser.add_argument("--start-year", type=int, default=1980)
    parser.add_argument("--end-year", type=int, default=2023)
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir

    engine, summary_df, adult_death_records, adult_stock_records = run_instrumented_model(config_path)
    vital_rows = load_hmd_vitals(HMD_VITAL_PATH)

    write_annual_flow_comparison(summary_df, vital_rows, out_dir / "annual_flow_comparison.csv")
    write_death_decomposition(
        engine,
        adult_death_records,
        adult_stock_records,
        args.start_year,
        args.end_year,
        out_dir / "age_death_decomposition.csv",
    )
    write_replay_summary(
        engine,
        adult_death_records,
        adult_stock_records,
        args.start_year,
        args.end_year,
        out_dir / "death_replay_summary.csv",
    )
    write_aging_operator_benchmark(engine, out_dir / "aging_operator_benchmark.csv")
    summarize_outputs(out_dir, args.start_year, args.end_year)


if __name__ == "__main__":
    main()
