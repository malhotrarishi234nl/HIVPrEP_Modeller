#!/usr/bin/env python3
"""
Non-production aging-operator POC.

This script compares two aggregate age-sex demographic operators without
touching the production HIV engine:

1. memoryless_fractional:
   Matches the current engine's bin-level aging idea: each timestep a fixed
   fraction of every 5-year bin moves to the next bin.

2. residence_queue:
   Splits each 5-year bin into quarterly residence phases. People advance one
   phase per quarter and only leave the age bin after 5 years.

Both variants use the same observed HMD births and deaths, plus optional net
migration from the model config. This is not a replacement model; it is a
controlled magnitude test for age-structure drift.

Run from repo root:
    python3 diagnostics/aging_operator_poc.py --config 1980_test.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_reader import ConfigReader
from config_utils import extract_value


HMD_POP_PATH = ROOT.parent / "data" / "raw" / "validation" / "HMD_NL_Pop.txt"
HMD_DEATH_PATH = ROOT / "Validation " / "HMD_NL_Death.txt"
HMD_BIRTH_PATH = ROOT / "Validation " / "HMD_NL_Birth.txt"
DEFAULT_CONFIG = ROOT / "configs" / "1980_test.json"
DEFAULT_OUT_DIR = ROOT / "outputs" / "diagnostics" / "aging_operator_poc"
SEXES = ("female", "male")


def model_bins_from_config(config: dict) -> List[str]:
    reader = ConfigReader(config)
    v6 = reader.as_v6_dict()
    child_bins = extract_value(v6["childhood"]["age_bins"])
    adult_bins = [
        item["name"]
        for item in extract_value(v6["groups"]["age_groups"])
        if int(item["min"]) < 100
    ]
    return child_bins + adult_bins + ["100+"]


def bin_start(label: str) -> int:
    if label.endswith("+"):
        return int(label[:-1])
    return int(label.split("-")[0])


def age_to_bin(age: int, age_interval: str, bins: List[str]) -> str | None:
    if age_interval == "+" or age >= 100:
        return "100+"
    for label in bins:
        if label == "100+":
            continue
        lo, hi = [int(x) for x in label.split("-")]
        if lo <= age <= hi:
            return label
    return None


def zero_state(bins: List[str]) -> Dict[str, Dict[str, float]]:
    return {label: {"female": 0.0, "male": 0.0} for label in bins}


def load_hmd_population(path: Path, bins: List[str], years: Iterable[int]) -> Dict[int, dict]:
    wanted = set(years)
    out = defaultdict(lambda: zero_state(bins))
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
            label = age_to_bin(age, row.get("AgeInterval", "").strip(), bins)
            if label is None:
                continue
            sex = "male" if row["Sex"].strip() == "m" else "female"
            out[year][label][sex] += float(row["Population"])
    return out


def load_hmd_deaths(path: Path, bins: List[str], years: Iterable[int]) -> Dict[int, dict]:
    wanted = set(years)
    out = defaultdict(lambda: zero_state(bins))
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
            label = age_to_bin(age, row.get("AgeInterval", "").strip(), bins)
            if label is None:
                continue
            sex = "male" if row["Sex"].strip() == "m" else "female"
            out[year][label][sex] += float(row["Deaths"])
    return out


def load_hmd_births(path: Path, years: Iterable[int]) -> Dict[int, dict]:
    wanted = set(years)
    out = defaultdict(lambda: {"female": 0.0, "male": 0.0})
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Access", "").strip() != "O":
                continue
            year = int(row["Year"])
            if year not in wanted:
                continue
            sex = "male" if row["Sex"].strip() == "m" else "female"
            out[year][sex] += float(row["Births"])
    return out


def migration_by_year(config: dict) -> Dict[int, float]:
    v6 = ConfigReader(config).as_v6_dict()
    mig = v6.get("migration", {})
    if not extract_value(mig.get("enabled", False)):
        return {}
    quantity = mig.get("quantity", {})
    mode = extract_value(quantity.get("mode", "year_specific"))
    if mode == "year_specific":
        raw = extract_value(quantity.get("year_specific", {}))
        return {int(k): float(v) for k, v in raw.items()}
    if mode == "flat":
        flat = float(extract_value(quantity.get("flat", 0.0)))
        years = range(1900, 2101)
        return {year: flat for year in years}
    return {}


def distribute_migration(
    state: Dict[str, Dict[str, float]],
    amount: float,
    bins: List[str],
    profile: str,
) -> Dict[str, Dict[str, float]]:
    allocation = zero_state(bins)
    if profile == "none":
        return allocation
    if abs(amount) < 1e-9:
        return allocation

    eligible = []
    for label in bins:
        start = bin_start(label)
        if profile == "working_age":
            if 20 <= start <= 40:
                eligible.append(label)
        else:
            eligible.append(label)
    if not eligible:
        eligible = bins

    weights = []
    keys = []
    for label in eligible:
        for sex in SEXES:
            weights.append(max(state[label][sex], 0.0))
            keys.append((label, sex))
    weights_arr = np.asarray(weights, dtype=float)
    if weights_arr.sum() <= 0:
        weights_arr = np.ones_like(weights_arr) / len(weights_arr)
    else:
        weights_arr = weights_arr / weights_arr.sum()

    for (label, sex), share in zip(keys, weights_arr):
        allocation[label][sex] += amount * float(share)
    return allocation


class MemorylessFractional:
    def __init__(self, initial: Dict[str, Dict[str, float]], bins: List[str]):
        self.bins = bins
        self.state = {label: dict(initial[label]) for label in bins}

    def add(self, additions: Dict[str, Dict[str, float]]) -> None:
        for label in self.bins:
            for sex in SEXES:
                self.state[label][sex] += additions[label][sex]
                if self.state[label][sex] < 0:
                    self.state[label][sex] = 0.0

    def age_one_quarter(self) -> None:
        p = 1.0 - (1.0 - 1.0 / 5.0) ** 0.25
        next_state = zero_state(self.bins)
        for idx, label in enumerate(self.bins):
            for sex in SEXES:
                count = self.state[label][sex]
                if idx == len(self.bins) - 1:
                    next_state[label][sex] += count
                else:
                    moving = count * p
                    next_state[label][sex] += count - moving
                    next_state[self.bins[idx + 1]][sex] += moving
        self.state = next_state

    def totals(self) -> Dict[str, Dict[str, float]]:
        return {label: dict(self.state[label]) for label in self.bins}


class ResidenceQueue:
    def __init__(self, initial: Dict[str, Dict[str, float]], bins: List[str], phases: int = 20):
        self.bins = bins
        self.phases = phases
        self.state = {
            label: {
                sex: np.full(phases, initial[label][sex] / phases, dtype=float)
                for sex in SEXES
            }
            for label in bins
        }

    def add(self, additions: Dict[str, Dict[str, float]]) -> None:
        for label in self.bins:
            for sex in SEXES:
                value = additions[label][sex]
                if value >= 0:
                    self.state[label][sex][0] += value
                else:
                    total = self.state[label][sex].sum()
                    if total <= 0:
                        continue
                    remove = min(-value, total)
                    self.state[label][sex] *= (total - remove) / total

    def age_one_quarter(self) -> None:
        next_state = {
            label: {sex: np.zeros(self.phases, dtype=float) for sex in SEXES}
            for label in self.bins
        }
        for idx, label in enumerate(self.bins):
            for sex in SEXES:
                phases = self.state[label][sex]
                next_state[label][sex][1:] += phases[:-1]
                outflow = phases[-1]
                if idx == len(self.bins) - 1:
                    next_state[label][sex][-1] += outflow
                else:
                    next_state[self.bins[idx + 1]][sex][0] += outflow
        self.state = next_state

    def totals(self) -> Dict[str, Dict[str, float]]:
        return {
            label: {sex: float(self.state[label][sex].sum()) for sex in SEXES}
            for label in self.bins
        }


def subtract_deaths(
    state: Dict[str, Dict[str, float]],
    annual_deaths: Dict[str, Dict[str, float]],
    bins: List[str],
    quarter_fraction: float = 0.25,
) -> Dict[str, Dict[str, float]]:
    additions = zero_state(bins)
    for label in bins:
        for sex in SEXES:
            additions[label][sex] -= min(state[label][sex], annual_deaths[label][sex] * quarter_fraction)
    return additions


def add_births(annual_births: Dict[str, float], bins: List[str], quarter_fraction: float = 0.25) -> Dict[str, Dict[str, float]]:
    additions = zero_state(bins)
    additions["0-4"]["female"] += annual_births["female"] * quarter_fraction
    additions["0-4"]["male"] += annual_births["male"] * quarter_fraction
    return additions


def merge_additions(*blocks: Dict[str, Dict[str, float]], bins: List[str]) -> Dict[str, Dict[str, float]]:
    merged = zero_state(bins)
    for block in blocks:
        for label in bins:
            for sex in SEXES:
                merged[label][sex] += block[label][sex]
    return merged


def total_population(state: Dict[str, Dict[str, float]]) -> float:
    return sum(state[label][sex] for label in state for sex in SEXES)


def old_age_share(state: Dict[str, Dict[str, float]], min_age: int) -> float:
    total = total_population(state)
    if total <= 0:
        return 0.0
    old = sum(
        state[label][sex]
        for label in state
        if bin_start(label) >= min_age
        for sex in SEXES
    )
    return old / total


def compare_to_hmd(
    variant: str,
    year: int,
    state: Dict[str, Dict[str, float]],
    hmd_state: Dict[str, Dict[str, float]],
    bins: List[str],
) -> dict:
    model_total = total_population(state)
    hmd_total = total_population(hmd_state)
    row = {
        "variant": variant,
        "year": year,
        "model_total": round(model_total, 3),
        "hmd_total": round(hmd_total, 3),
        "total_gap": round(model_total - hmd_total, 3),
        "total_gap_pct": None if hmd_total <= 0 else (model_total - hmd_total) / hmd_total * 100.0,
        "model_share_75plus": old_age_share(state, 75),
        "hmd_share_75plus": old_age_share(hmd_state, 75),
        "model_share_85plus": old_age_share(state, 85),
        "hmd_share_85plus": old_age_share(hmd_state, 85),
        "model_share_95plus": old_age_share(state, 95),
        "hmd_share_95plus": old_age_share(hmd_state, 95),
    }
    for label in ["75-79", "80-84", "85-89", "90-94", "95-99", "100+"]:
        model_bin = state[label]["female"] + state[label]["male"]
        hmd_bin = hmd_state[label]["female"] + hmd_state[label]["male"]
        row[f"{label}_gap"] = round(model_bin - hmd_bin, 3)
        row[f"{label}_ratio"] = None if hmd_bin <= 0 else model_bin / hmd_bin
    return row


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_poc(config_path: Path, out_dir: Path, start_year: int, end_year: int, migration_profile: str) -> None:
    with config_path.open(encoding="utf-8") as f:
        config = json.load(f)
    bins = model_bins_from_config(config)
    years = range(start_year, end_year + 1)
    hmd_pop = load_hmd_population(HMD_POP_PATH, bins, years)
    hmd_deaths = load_hmd_deaths(HMD_DEATH_PATH, bins, years)
    hmd_births = load_hmd_births(HMD_BIRTH_PATH, years)
    net_migration = migration_by_year(config)

    variants = {
        "memoryless_fractional": MemorylessFractional(hmd_pop[start_year], bins),
        "residence_queue": ResidenceQueue(hmd_pop[start_year], bins),
    }

    rows = []
    detail_rows = []
    for year in range(start_year, end_year):
        for variant_name, operator in variants.items():
            if year in hmd_pop:
                rows.append(compare_to_hmd(variant_name, year, operator.totals(), hmd_pop[year], bins))

            for _quarter in range(4):
                current = operator.totals()
                deaths = subtract_deaths(current, hmd_deaths[year], bins)
                births = add_births(hmd_births[year], bins)
                migration = distribute_migration(
                    current,
                    net_migration.get(year, 0.0) * 0.25,
                    bins,
                    migration_profile,
                )
                operator.add(merge_additions(deaths, births, migration, bins=bins))
                operator.age_one_quarter()

            for label in bins:
                detail_rows.append(
                    {
                        "variant": variant_name,
                        "year": year + 1,
                        "age_group": label,
                        "model_total": round(operator.totals()[label]["female"] + operator.totals()[label]["male"], 3),
                        "hmd_total": round(hmd_pop[year + 1][label]["female"] + hmd_pop[year + 1][label]["male"], 3)
                        if year + 1 in hmd_pop else None,
                    }
                )

    for variant_name, operator in variants.items():
        if end_year in hmd_pop:
            rows.append(compare_to_hmd(variant_name, end_year, operator.totals(), hmd_pop[end_year], bins))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "aging_operator_poc_summary.csv", rows)
    write_csv(out_dir / "aging_operator_poc_age_bins.csv", detail_rows)

    print("Aging operator POC complete.")
    print(f"Output directory: {out_dir}")
    for variant_name in variants:
        final_row = next(r for r in rows if r["variant"] == variant_name and r["year"] == end_year)
        print(
            f"{variant_name}: total_gap={final_row['total_gap']:,.0f}, "
            f"75+ share model={final_row['model_share_75plus']:.3%} "
            f"vs HMD={final_row['hmd_share_75plus']:.3%}, "
            f"85+ share model={final_row['model_share_85plus']:.3%} "
            f"vs HMD={final_row['hmd_share_85plus']:.3%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a non-production aging-operator POC.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start-year", type=int, default=1980)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument(
        "--migration-profile",
        choices=["working_age", "mirror", "none"],
        default="working_age",
        help="How to distribute net migration in the aggregate POC.",
    )
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    run_poc(config_path, out_dir, args.start_year, args.end_year, args.migration_profile)


if __name__ == "__main__":
    main()
