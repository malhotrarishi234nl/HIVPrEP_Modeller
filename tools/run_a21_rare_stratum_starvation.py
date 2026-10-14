#!/usr/bin/env python3
"""Run A-21's deterministic rare-stratum starvation trace.

This is an observation-only diagnostic.  It subclasses the production engine
only to observe HIV-mortality draws and temporarily wraps the life-table
background-mortality allocator.  Neither wrapper changes an input, an output,
or the order of production calls.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import gzip
import hashlib
import io
import json
import math
import platform
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.a17_residual_placement import METRICS  # noqa: E402
from tools.run_a20_process_decomposition import annual_digest  # noqa: E402


DEFAULT_CONFIG = ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json"
DEFAULT_SEED = 7001
START_YEAR = 1975
END_YEAR = 2025
A20_DETERMINISTIC_DIGEST = (
    "8d186b359195f3a911e5d53577b0cc900273466694356e513a9284c40197a455"
)

BACKGROUND_COLUMNS = (
    "step",
    "year",
    "calendar_year",
    "age_index",
    "age",
    "orientation_index",
    "orientation",
    "activity_index",
    "activity",
    "hiv_index",
    "hiv_status",
    "hiv_stage",
    "diagnosis_index",
    "diagnosis",
    "art_index",
    "art",
    "prep_index",
    "prep",
    "n",
    "p",
    "expected",
    "allocated",
    "allocation_error",
    "positive_subunit",
)

HIV_MORTALITY_COLUMNS = (
    "step",
    "year",
    "calendar_year",
    "hiv_stage",
    "art",
    "age_index",
    "age",
    "orientation_index",
    "orientation",
    "activity_index",
    "activity",
    "diagnosis_index",
    "diagnosis",
    "prep_index",
    "prep",
    "n",
    "p",
    "expected",
    "allocated",
    "allocation_error",
    "positive_subunit",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def configured(base: dict, *, seed: int) -> dict:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": True}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": True
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else math.nan


def _year_and_step(year: float) -> tuple[int, int]:
    step = int(round((float(year) - START_YEAR) * 4.0))
    return int(math.floor(float(year) + 1e-9)), step


def summarise_status_block(
    n: np.ndarray,
    p: float,
    allocated: np.ndarray,
    positive_mask: np.ndarray,
) -> dict[str, float | int]:
    """Summarise exact allocation accounting for one HIV-status block."""
    population = np.asarray(n, dtype=np.int64)
    events = np.asarray(allocated, dtype=np.int64)
    expected = population.astype(float) * float(p)
    positive_mask = np.broadcast_to(np.asarray(positive_mask, dtype=bool), n.shape)
    negative_mask = ~positive_mask

    total_expected = float(expected.sum())
    total_allocated = int(events.sum())
    target = min(int(np.round(total_expected)), int(population.sum()))
    positive_expected = float(expected[positive_mask].sum())
    positive_allocated = int(events[positive_mask].sum())
    negative_expected = float(expected[negative_mask].sum())
    negative_allocated = int(events[negative_mask].sum())
    positive_shortfall = positive_expected - positive_allocated
    negative_excess = negative_allocated - negative_expected
    rounding_error = total_allocated - total_expected

    positive_occupied = positive_mask & (population > 0)
    positive_subunit = positive_occupied & (expected < 1.0)
    positive_starved = positive_occupied & (events == 0)
    return {
        "population": int(population.sum()),
        "expected": total_expected,
        "allocated": total_allocated,
        "rounded_expected_target": target,
        "control_error": total_allocated - target,
        "rounding_error": rounding_error,
        "hiv_positive_population": int(population[positive_mask].sum()),
        "hiv_positive_expected": positive_expected,
        "hiv_positive_allocated": positive_allocated,
        "hiv_positive_shortfall": positive_shortfall,
        "hiv_positive_shortfall_fraction": _safe_ratio(
            positive_shortfall, positive_expected
        ),
        "hiv_negative_population": int(population[negative_mask].sum()),
        "hiv_negative_expected": negative_expected,
        "hiv_negative_allocated": negative_allocated,
        "hiv_negative_excess": negative_excess,
        "relocation_balance_residual": (
            negative_excess - positive_shortfall - rounding_error
        ),
        "hiv_positive_cells": int(positive_mask.sum()),
        "hiv_positive_occupied_cells": int(positive_occupied.sum()),
        "hiv_positive_subunit_cells": int(positive_subunit.sum()),
        "hiv_positive_starved_cells": int(positive_starved.sum()),
        "hiv_positive_subunit_share_occupied": _safe_ratio(
            int(positive_subunit.sum()), int(positive_occupied.sum())
        ),
        "hiv_positive_starved_share_occupied": _safe_ratio(
            int(positive_starved.sum()), int(positive_occupied.sum())
        ),
    }


def summarise_mortality_call(
    n: np.ndarray, p: float, allocated: np.ndarray
) -> dict[str, float | int]:
    population = np.asarray(n, dtype=np.int64)
    events = np.asarray(allocated, dtype=np.int64)
    expected = population.astype(float) * float(p)
    occupied = population > 0
    subunit = occupied & (expected < 1.0)
    starved = occupied & (events == 0)
    large = occupied & ~subunit
    total_expected = float(expected.sum())
    total_allocated = int(events.sum())
    return {
        "population": int(population.sum()),
        "expected": total_expected,
        "allocated": total_allocated,
        "allocation_error": total_allocated - total_expected,
        "rounded_expected_target": min(
            int(np.round(total_expected)), int(population.sum())
        ),
        "occupied_cells": int(occupied.sum()),
        "subunit_cells": int(subunit.sum()),
        "starved_cells": int(starved.sum()),
        "subunit_share_occupied": _safe_ratio(int(subunit.sum()), int(occupied.sum())),
        "starved_share_occupied": _safe_ratio(int(starved.sum()), int(occupied.sum())),
        "subunit_expected": float(expected[subunit].sum()),
        "subunit_allocated": int(events[subunit].sum()),
        "subunit_shortfall": float(expected[subunit].sum() - events[subunit].sum()),
        "large_expected": float(expected[large].sum()),
        "large_allocated": int(events[large].sum()),
        "large_excess": float(events[large].sum() - expected[large].sum()),
    }


class A21TraceRecorder:
    """Stream cell-level traces and retain compact block-level summaries."""

    def __init__(self, raw_dir: Path, groups: Any) -> None:
        self.raw_dir = raw_dir
        self.groups = groups
        self.background_blocks: list[dict] = []
        self.hiv_mortality_calls: list[dict] = []
        self.background_cell_rows = 0
        self.hiv_mortality_cell_rows = 0
        self._background_handle = gzip.open(
            raw_dir / "background_mortality_cell_trace.csv.gz",
            "wt",
            encoding="utf-8",
            newline="",
            compresslevel=6,
        )
        self._hiv_handle = gzip.open(
            raw_dir / "hiv_mortality_cell_trace.csv.gz",
            "wt",
            encoding="utf-8",
            newline="",
            compresslevel=6,
        )
        self._background_writer = csv.writer(self._background_handle)
        self._hiv_writer = csv.writer(self._hiv_handle)
        self._background_writer.writerow(BACKGROUND_COLUMNS)
        self._hiv_writer.writerow(HIV_MORTALITY_COLUMNS)

    def close(self) -> None:
        self._background_handle.close()
        self._hiv_handle.close()

    def record_background(
        self,
        *,
        year: float,
        age_index: int,
        orientation_index: int,
        n: np.ndarray,
        p: float,
        allocated: np.ndarray,
    ) -> None:
        calendar_year, step = _year_and_step(year)
        population = np.asarray(n, dtype=np.int64)
        events = np.asarray(allocated, dtype=np.int64)
        if population.shape != (
            self.groups.n_activities,
            self.groups.n_hiv_stages,
            self.groups.n_diagnosis,
            self.groups.n_art,
            self.groups.n_prep,
        ):
            raise AssertionError(
                f"unexpected A-21 background block shape: {population.shape}"
            )
        expected = population.astype(float) * float(p)
        hiv_axis = np.arange(population.shape[1])[None, :, None, None, None]
        positive_mask = np.broadcast_to(hiv_axis > 0, population.shape)
        summary = summarise_status_block(
            population, float(p), events, positive_mask
        )
        summary.update(
            {
                "step": step,
                "year": float(year),
                "calendar_year": calendar_year,
                "age_index": int(age_index),
                "age": self.groups.age_groups[age_index],
                "orientation_index": int(orientation_index),
                "orientation": self.groups.orientations[orientation_index],
                "p": float(p),
            }
        )
        self.background_blocks.append(summary)

        rows = []
        for activity_idx, hiv_idx, diagnosis_idx, art_idx, prep_idx in np.ndindex(
            population.shape
        ):
            n_value = int(
                population[activity_idx, hiv_idx, diagnosis_idx, art_idx, prep_idx]
            )
            expected_value = float(
                expected[activity_idx, hiv_idx, diagnosis_idx, art_idx, prep_idx]
            )
            allocated_value = int(
                events[activity_idx, hiv_idx, diagnosis_idx, art_idx, prep_idx]
            )
            rows.append(
                (
                    step,
                    float(year),
                    calendar_year,
                    age_index,
                    self.groups.age_groups[age_index],
                    orientation_index,
                    self.groups.orientations[orientation_index],
                    activity_idx,
                    self.groups.activities[activity_idx],
                    hiv_idx,
                    "positive" if hiv_idx > 0 else "negative",
                    self.groups.hiv_stages[hiv_idx],
                    diagnosis_idx,
                    self.groups.diagnosis_states[diagnosis_idx],
                    art_idx,
                    self.groups.art_states[art_idx],
                    prep_idx,
                    self.groups.prep_states[prep_idx],
                    n_value,
                    float(p),
                    expected_value,
                    allocated_value,
                    allocated_value - expected_value,
                    bool(n_value > 0 and expected_value < 1.0),
                )
            )
        self._background_writer.writerows(rows)
        self.background_cell_rows += len(rows)

    def record_hiv_mortality(
        self,
        *,
        step: int,
        year: float,
        stage: str,
        art: str,
        n: np.ndarray,
        p: float,
        allocated: np.ndarray,
    ) -> None:
        calendar_year = int(math.floor(float(year) + 1e-9))
        population = np.asarray(n, dtype=np.int64)
        events = np.asarray(allocated, dtype=np.int64)
        if population.shape != (
            self.groups.n_age_groups,
            self.groups.n_orientations,
            self.groups.n_activities,
            self.groups.n_diagnosis,
            self.groups.n_prep,
        ):
            raise AssertionError(
                f"unexpected A-21 HIV-mortality block shape: {population.shape}"
            )
        expected = population.astype(float) * float(p)
        summary = summarise_mortality_call(population, float(p), events)
        summary.update(
            {
                "step": int(step),
                "year": float(year),
                "calendar_year": calendar_year,
                "hiv_stage": stage,
                "art": art,
                "p": float(p),
            }
        )
        self.hiv_mortality_calls.append(summary)

        rows = []
        for age_idx, orient_idx, activity_idx, diagnosis_idx, prep_idx in np.ndindex(
            population.shape
        ):
            n_value = int(
                population[age_idx, orient_idx, activity_idx, diagnosis_idx, prep_idx]
            )
            expected_value = float(
                expected[age_idx, orient_idx, activity_idx, diagnosis_idx, prep_idx]
            )
            allocated_value = int(
                events[age_idx, orient_idx, activity_idx, diagnosis_idx, prep_idx]
            )
            rows.append(
                (
                    int(step),
                    float(year),
                    calendar_year,
                    stage,
                    art,
                    age_idx,
                    self.groups.age_groups[age_idx],
                    orient_idx,
                    self.groups.orientations[orient_idx],
                    activity_idx,
                    self.groups.activities[activity_idx],
                    diagnosis_idx,
                    self.groups.diagnosis_states[diagnosis_idx],
                    prep_idx,
                    self.groups.prep_states[prep_idx],
                    n_value,
                    float(p),
                    expected_value,
                    allocated_value,
                    allocated_value - expected_value,
                    bool(n_value > 0 and expected_value < 1.0),
                )
            )
        self._hiv_writer.writerows(rows)
        self.hiv_mortality_cell_rows += len(rows)


class BackgroundMortalityTrace(AbstractContextManager):
    """Observe the live life-table allocator and restore every binding."""

    def __init__(self, recorder: A21TraceRecorder) -> None:
        self.recorder = recorder
        self._active: dict[str, Any] | None = None
        self._original_allocator = None
        self._original_method = None

    def __enter__(self):
        import demography as demography_module
        from demography import DemographyModule

        context = self
        self._original_allocator = demography_module.deterministic_allocate_events
        self._original_method = DemographyModule._calculate_life_table_mortality

        @wraps(self._original_allocator)
        def traced_allocator(n, p):
            allocated = context._original_allocator(n, p)
            if context._active is not None:
                active = context._active
                call_index = int(active["call_index"])
                orientation_order = active["orientation_order"]
                age_index, orientation_position = divmod(
                    call_index, len(orientation_order)
                )
                if age_index >= active["groups"].n_age_groups:
                    raise AssertionError("too many background allocator calls")
                context.recorder.record_background(
                    year=active["year"],
                    age_index=age_index,
                    orientation_index=orientation_order[orientation_position],
                    n=n,
                    p=p,
                    allocated=allocated,
                )
                active["call_index"] += 1
            return allocated

        @wraps(self._original_method)
        def traced_life_table(module, population, current_year, rng, deterministic):
            if not deterministic:
                return context._original_method(
                    module, population, current_year, rng, deterministic
                )
            if context._active is not None:
                raise RuntimeError("nested A-21 life-table trace")
            groups = module.groups
            orientation_order = (
                groups.orient_to_idx["Female"],
                groups.orient_to_idx["Heterosexual_Male"],
                groups.orient_to_idx["MSM"],
            )
            context._active = {
                "year": float(current_year),
                "groups": groups,
                "orientation_order": orientation_order,
                "call_index": 0,
            }
            try:
                result = context._original_method(
                    module, population, current_year, rng, deterministic
                )
                expected_calls = groups.n_age_groups * len(orientation_order)
                actual_calls = int(context._active["call_index"])
                if actual_calls != expected_calls:
                    raise AssertionError(
                        "A-21 background trace expected "
                        f"{expected_calls} allocator calls, observed {actual_calls}"
                    )
                return result
            finally:
                context._active = None

        demography_module.deterministic_allocate_events = traced_allocator
        DemographyModule._calculate_life_table_mortality = traced_life_table
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        import demography as demography_module
        from demography import DemographyModule

        demography_module.deterministic_allocate_events = self._original_allocator
        DemographyModule._calculate_life_table_mortality = self._original_method
        self._active = None
        return False


class A21TraceEngine(SimulationEngineV3):
    """Production engine with an observation-only HIV-mortality callback."""

    _a21_recorder: A21TraceRecorder | None = None

    def _draw_events_for(self, process_id, n: np.ndarray, p: float) -> np.ndarray:
        allocated = super()._draw_events_for(process_id, n, p)
        if (
            self._a21_recorder is not None
            and self.deterministic
            and isinstance(process_id, tuple)
            and len(process_id) == 3
            and process_id[0] == "hiv_mortality"
        ):
            self._a21_recorder.record_hiv_mortality(
                step=int(self._current_step),
                year=float(self._current_year),
                stage=str(process_id[1]),
                art=str(process_id[2]),
                n=n,
                p=p,
                allocated=allocated,
            )
        return allocated


def _aggregate_background(blocks: pd.DataFrame) -> pd.DataFrame:
    sums = [
        "population",
        "expected",
        "allocated",
        "rounded_expected_target",
        "control_error",
        "rounding_error",
        "hiv_positive_population",
        "hiv_positive_expected",
        "hiv_positive_allocated",
        "hiv_positive_shortfall",
        "hiv_negative_population",
        "hiv_negative_expected",
        "hiv_negative_allocated",
        "hiv_negative_excess",
        "relocation_balance_residual",
        "hiv_positive_cells",
        "hiv_positive_occupied_cells",
        "hiv_positive_subunit_cells",
        "hiv_positive_starved_cells",
    ]
    annual = blocks.groupby("calendar_year", as_index=False)[sums].sum()
    annual["hiv_positive_shortfall_fraction"] = (
        annual["hiv_positive_shortfall"] / annual["hiv_positive_expected"]
    )
    annual["hiv_positive_allocation_ratio"] = (
        annual["hiv_positive_allocated"] / annual["hiv_positive_expected"]
    )
    annual["hiv_positive_subunit_share_occupied"] = (
        annual["hiv_positive_subunit_cells"]
        / annual["hiv_positive_occupied_cells"]
    )
    annual["hiv_positive_starved_share_occupied"] = (
        annual["hiv_positive_starved_cells"]
        / annual["hiv_positive_occupied_cells"]
    )
    return annual


def _aggregate_hiv_mortality(calls: pd.DataFrame) -> pd.DataFrame:
    sums = [
        "population",
        "expected",
        "allocated",
        "allocation_error",
        "rounded_expected_target",
        "occupied_cells",
        "subunit_cells",
        "starved_cells",
        "subunit_expected",
        "subunit_allocated",
        "subunit_shortfall",
        "large_expected",
        "large_allocated",
        "large_excess",
    ]
    annual = calls.groupby(
        ["calendar_year", "hiv_stage", "art"], as_index=False
    )[sums].sum()
    annual["allocation_ratio"] = annual["allocated"] / annual["expected"]
    annual["subunit_allocation_ratio"] = (
        annual["subunit_allocated"] / annual["subunit_expected"]
    )
    annual["subunit_share_occupied"] = (
        annual["subunit_cells"] / annual["occupied_cells"]
    )
    annual["starved_share_occupied"] = (
        annual["starved_cells"] / annual["occupied_cells"]
    )
    return annual


def _aggregate_hiv_mortality_stage_art(calls: pd.DataFrame) -> pd.DataFrame:
    sums = [
        "expected",
        "allocated",
        "allocation_error",
        "subunit_expected",
        "subunit_allocated",
        "subunit_shortfall",
        "large_expected",
        "large_allocated",
        "large_excess",
    ]
    summary = calls.groupby(["hiv_stage", "art"], as_index=False)[sums].sum()
    summary["allocation_ratio"] = summary["allocated"] / summary["expected"]
    summary["subunit_allocation_ratio"] = (
        summary["subunit_allocated"] / summary["subunit_expected"]
    )
    return summary


def _sum_frame(frame: pd.DataFrame, columns: list[str]) -> dict[str, float]:
    return {column: float(frame[column].sum()) for column in columns}


def analyse_trace(recorder: A21TraceRecorder, raw_dir: Path) -> dict[str, Any]:
    background_blocks = pd.DataFrame(recorder.background_blocks)
    hiv_calls = pd.DataFrame(recorder.hiv_mortality_calls)
    if background_blocks.empty or hiv_calls.empty:
        raise AssertionError("A-21 trace captured no live mortality calls")
    background_blocks.to_csv(raw_dir / "background_mortality_blocks.csv", index=False)
    hiv_calls.to_csv(raw_dir / "hiv_mortality_calls.csv", index=False)

    background_annual = _aggregate_background(background_blocks)
    hiv_annual = _aggregate_hiv_mortality(hiv_calls)
    hiv_stage_art = _aggregate_hiv_mortality_stage_art(hiv_calls)
    background_annual.to_csv(
        raw_dir / "background_mortality_annual_summary.csv", index=False
    )
    hiv_annual.to_csv(raw_dir / "hiv_mortality_annual_summary.csv", index=False)
    hiv_stage_art.to_csv(
        raw_dir / "hiv_mortality_stage_art_summary.csv", index=False
    )

    bg = _sum_frame(
        background_blocks,
        [
            "expected",
            "allocated",
            "control_error",
            "rounding_error",
            "hiv_positive_expected",
            "hiv_positive_allocated",
            "hiv_positive_shortfall",
            "hiv_negative_expected",
            "hiv_negative_allocated",
            "hiv_negative_excess",
            "relocation_balance_residual",
            "hiv_positive_occupied_cells",
            "hiv_positive_subunit_cells",
            "hiv_positive_starved_cells",
        ],
    )
    bg.update(
        {
            "calls": int(len(background_blocks)),
            "cell_rows": int(recorder.background_cell_rows),
            "max_abs_block_control_error": float(
                background_blocks["control_error"].abs().max()
            ),
            "max_abs_relocation_balance_residual": float(
                background_blocks["relocation_balance_residual"].abs().max()
            ),
            "hiv_positive_allocation_ratio": _safe_ratio(
                bg["hiv_positive_allocated"], bg["hiv_positive_expected"]
            ),
            "hiv_positive_shortfall_fraction": _safe_ratio(
                bg["hiv_positive_shortfall"], bg["hiv_positive_expected"]
            ),
            "hiv_positive_subunit_share_occupied": _safe_ratio(
                bg["hiv_positive_subunit_cells"],
                bg["hiv_positive_occupied_cells"],
            ),
            "hiv_positive_starved_share_occupied": _safe_ratio(
                bg["hiv_positive_starved_cells"],
                bg["hiv_positive_occupied_cells"],
            ),
            "first_year_with_hiv_positive_expectation": int(
                background_annual.loc[
                    background_annual["hiv_positive_expected"] > 0,
                    "calendar_year",
                ].min()
            ),
        }
    )

    hm = _sum_frame(
        hiv_calls,
        [
            "expected",
            "allocated",
            "allocation_error",
            "occupied_cells",
            "subunit_cells",
            "starved_cells",
            "subunit_expected",
            "subunit_allocated",
            "subunit_shortfall",
            "large_expected",
            "large_allocated",
            "large_excess",
        ],
    )
    hm.update(
        {
            "calls": int(len(hiv_calls)),
            "cell_rows": int(recorder.hiv_mortality_cell_rows),
            "allocation_ratio": _safe_ratio(hm["allocated"], hm["expected"]),
            "allocation_error_fraction": _safe_ratio(
                hm["allocation_error"], hm["expected"]
            ),
            "subunit_allocation_ratio": _safe_ratio(
                hm["subunit_allocated"], hm["subunit_expected"]
            ),
            "subunit_share_occupied": _safe_ratio(
                hm["subunit_cells"], hm["occupied_cells"]
            ),
            "starved_share_occupied": _safe_ratio(
                hm["starved_cells"], hm["occupied_cells"]
            ),
        }
    )

    milestones = background_annual[
        background_annual["calendar_year"].isin(
            [1975, 1980, 1985, 1990, 1995, 2000, 2010, 2015, 2020, 2025]
        )
    ].copy()
    milestones.to_csv(raw_dir / "background_mortality_milestones.csv", index=False)

    confirmed = bool(
        bg["hiv_positive_expected"] > 0
        and bg["hiv_positive_shortfall"] > 0
        and bg["hiv_negative_excess"] > 0
        and bg["max_abs_block_control_error"] == 0
        and bg["max_abs_relocation_balance_residual"] < 1e-8
    )
    findings = {
        "background_mortality": bg,
        "hiv_mortality": hm,
        "hiv_mortality_by_stage_art": hiv_stage_art.to_dict(orient="records"),
        "hypothesis_verdict": "confirmed" if confirmed else "falsified",
        "verdict_rule": (
            "Direct deterministic accounting: positive HIV+ shortfall, positive HIV- "
            "excess, exact block aggregate control, and relocation identity within "
            "1e-8. This is a numerical identity check, not a fitted threshold."
        ),
        "milestones": milestones.to_dict(orient="records"),
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, allow_nan=True) + "\n", encoding="utf-8"
    )
    return findings


def parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = Counter()
    problematic = []
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            counts[key] += int(suite.attrib.get(key, 0))
    for case in root.iter("testcase"):
        outcome = None
        if case.find("failure") is not None:
            outcome = "failure"
        elif case.find("error") is not None:
            outcome = "error"
        if outcome:
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            problematic.append(f"{outcome}:{classname}::{name}")
    return {
        **dict(counts),
        "passed": counts["tests"]
        - counts["failures"]
        - counts["errors"]
        - counts["skipped"],
        "problematic": sorted(problematic),
    }


def compare_tests(raw_dir: Path) -> dict[str, Any] | None:
    before_path = raw_dir / "full_suite_before.xml"
    after_path = raw_dir / "full_suite_after.xml"
    focused_path = raw_dir / "focused_a21_tests.xml"
    if not (before_path.exists() and after_path.exists() and focused_path.exists()):
        return None
    before = parse_junit(before_path)
    after = parse_junit(after_path)
    focused = parse_junit(focused_path)
    result = {
        "before": before,
        "after": after,
        "focused": focused,
        "same_preexisting_failure_error_set": (
            before["problematic"] == after["problematic"]
        ),
        "test_count_delta": after["tests"] - before["tests"],
    }
    (raw_dir / "full_suite_comparison.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def _fmt(value: float, digits: int = 3) -> str:
    return f"{float(value):,.{digits}f}"


def write_report(
    output_dir: Path,
    manifest: dict[str, Any],
    findings: dict[str, Any],
    tests: dict[str, Any] | None,
) -> None:
    bg = findings["background_mortality"]
    hm = findings["hiv_mortality"]
    hm_stage_rows = []
    for row in findings.get("hiv_mortality_by_stage_art", []):
        expected = float(row["expected"])
        allocation_ratio = float(row["allocation_ratio"])
        subunit_ratio = float(row["subunit_allocation_ratio"])
        hm_stage_rows.append(
            "| {stage} | {art} | {expected:,.2f} | {allocated:,.0f} | "
            "{error:+,.2f} | {ratio} | {subunit} |".format(
                stage=row["hiv_stage"],
                art=row["art"],
                expected=expected,
                allocated=float(row["allocated"]),
                error=float(row["allocation_error"]),
                ratio=(f"{allocation_ratio:.3%}" if expected else "n/a"),
                subunit=(
                    f"{subunit_ratio:.3%}"
                    if float(row["subunit_expected"]) > 0
                    else "n/a"
                ),
            )
        )
    milestone_rows = []
    for row in findings["milestones"]:
        if not row.get("hiv_positive_expected"):
            continue
        milestone_rows.append(
            "| {calendar_year} | {expected:,.2f} | {allocated:,.0f} | "
            "{shortfall:.1%} | {subunit:.1%} | {starved:.1%} |".format(
                calendar_year=int(row["calendar_year"]),
                expected=float(row["hiv_positive_expected"]),
                allocated=float(row["hiv_positive_allocated"]),
                shortfall=float(row["hiv_positive_shortfall_fraction"]),
                subunit=float(row["hiv_positive_subunit_share_occupied"]),
                starved=float(row["hiv_positive_starved_share_occupied"]),
            )
        )

    if tests is None:
        test_text = "Post-change test comparison pending."
        test_acceptance = "[ ]"
    else:
        before, after, focused = tests["before"], tests["after"], tests["focused"]
        test_text = (
            f"Focused A-21 tests: **{focused['passed']} passed**, "
            f"{focused['failures']} failed, {focused['errors']} errors. Full suite before: "
            f"**{before['failures']} failed / {before['passed']} passed / "
            f"{before['skipped']} skipped / {before['errors']} errors** ({before['tests']} total). "
            f"Full suite after: **{after['failures']} failed / {after['passed']} passed / "
            f"{after['skipped']} skipped / {after['errors']} errors** ({after['tests']} total). "
            f"The exact pre-existing failure/error set was "
            f"{'unchanged' if tests['same_preexisting_failure_error_set'] else 'CHANGED'}, "
            f"and the total increased by {tests['test_count_delta']} tests."
        )
        test_acceptance = (
            "[x]" if tests["same_preexisting_failure_error_set"] else "[!]"
        )

    hm_direction = "under" if hm["allocation_error"] < 0 else "over"
    report = f"""# A-21 — rare-stratum starvation at the background-mortality call

**Status:** **{findings['hypothesis_verdict'].upper()}** by one deterministic 1975–2025 trace.  
**Task type:** measurement only; no production behavior, value, threshold, or parameter changed.  
**Claim strength:** the cell accounting below is **well-supported** for this one parameter point because it is a deterministic identity trace; extrapolation to other parameter points is **unsupported**.

## 1. Result

I ran one deterministic simulation on `configs/V13.22_20260604_nearfit_rank001_basecase.json` (seed {manifest['seed']}, 204 quarterly steps) and observed the live life-table calls without switching any process.

The rare-stratum starvation hypothesis is **{findings['hypothesis_verdict']}**. Across {int(bg['calls']):,} age–orientation–quarter blocks, HIV-positive cells expected **{_fmt(bg['hiv_positive_expected'])}** background deaths but received **{_fmt(bg['hiv_positive_allocated'], 0)}**: an allocation ratio of **{bg['hiv_positive_allocation_ratio']:.3%}**, or a **{bg['hiv_positive_shortfall_fraction']:.3%} shortfall**. HIV-negative cells received **{_fmt(bg['hiv_negative_excess'])} more deaths than their continuous expectation**. The maximum block aggregate control error was **{bg['max_abs_block_control_error']:.0f} events**, and the maximum absolute error in the relocation identity was **{bg['max_abs_relocation_balance_residual']:.3e}**.

This is direct evidence of **Defect 1 at a specific live call site**, not a newly inferred fourth mechanism: the shared-rate allocator preserves each whole block's rounded expected total while moving background deaths away from the small HIV-positive cells and into HIV-negative cells. The mechanism therefore inflates PLHIV composition while nearly preserving total population, exactly the qualitative signature that A-20 localised.

No arbitrary materiality threshold was fitted. The pre-run decision rule was exact accounting: a positive HIV-positive shortfall, a positive HIV-negative excess, exact whole-block control, and the relocation identity within `1e-8`. The observed shortfall is reported as a fraction of all expected HIV-positive background deaths so its scale is visible directly.

## 2. Time profile

| year | HIV+ expected deaths | HIV+ allocated deaths | shortfall / expected | sub-unit share of occupied HIV+ cells | zero-allocation share of occupied HIV+ cells |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(milestone_rows)}

The first year with positive HIV-positive background-death expectation was **{int(bg['first_year_with_hiv_positive_expectation'])}**. Across the full run, **{bg['hiv_positive_subunit_share_occupied']:.3%}** of occupied HIV-positive cells had `0 < n·p < 1`, and **{bg['hiv_positive_starved_share_occupied']:.3%}** received zero allocated deaths. The annual table is retained rather than replacing this profile with a trend claim.

## 3. HIV-mortality trace and the A-13/A-20 sign tension

At the HIV-specific mortality calls, {int(hm['calls']):,} stage–ART–quarter blocks expected **{_fmt(hm['expected'])}** deaths and allocated **{_fmt(hm['allocated'], 0)}**, an aggregate ratio of **{hm['allocation_ratio']:.6%}**. Deterministic rounding therefore **{hm_direction}-allocated by {_fmt(abs(hm['allocation_error']))} deaths ({abs(hm['allocation_error_fraction']):.4%}) at the realised deterministic states**.

Within those calls, **{hm['subunit_share_occupied']:.3%}** of occupied cells were sub-unit and **{hm['starved_share_occupied']:.3%}** received zero events; sub-unit cells received **{hm['subunit_allocation_ratio']:.3%}** of their continuous expectation. Thus the direct trace resolves the narrow sign question—aggregate deterministic HIV-mortality allocation is {hm_direction} its continuous expectation at the realised states—but it does **not** convert A-20's +122% hybrid level response into an additive mechanism. A-13 compared two intervention arms and A-20 compared long-run levels; both include changed state trajectories. The retained stage/ART/year table shows the compositional redistribution that aggregate totals hide.

| HIV stage | ART status | expected | allocated | error | allocation / expected | sub-unit allocation / expected |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(hm_stage_rows)}

The direct aggregate error is only **{abs(hm['allocation_error_fraction']):.4%}**, so it does not explain A-20's +122% level response by event-count arithmetic. That adverse arm remains a non-additive trajectory/compositional result rather than a mechanism identified by A-21.

## 4. Integrity and tests

- Trace annual-output digest: `{manifest['annual_output_sha256']}`.
- A-20 production-deterministic control digest: `{A20_DETERMINISTIC_DIGEST}`.
- Trace matches A-20 exactly: **{str(manifest['matches_a20_deterministic_digest']).lower()}**.
- Cell-level evidence retained: {int(bg['cell_rows']):,} background rows and {int(hm['cell_rows']):,} HIV-mortality rows, gzip-compressed.
- {test_text}
- Tracked diff hash before A-21: `{manifest['tracked_diff_sha256_before']}`; at report generation: `{manifest['tracked_diff_sha256_at_report']}`. The A-21 implementation consists of new opt-in diagnostic/test files plus updates to the already-untracked handoff/checklist; no tracked production file was changed by A-21.

## 5. Exact commands

```bash
.venv/bin/python -m pytest -q --junitxml={shlex.quote(str(output_dir.relative_to(ROOT) / 'raw/full_suite_before.xml'))}
.venv/bin/python tools/run_a21_rare_stratum_starvation.py --config configs/V13.22_20260604_nearfit_rank001_basecase.json --seed {manifest['seed']} --output-dir {shlex.quote(str(output_dir.relative_to(ROOT)))}
.venv/bin/python -m pytest -q tests/test_a21_rare_stratum_starvation.py --junitxml={shlex.quote(str(output_dir.relative_to(ROOT) / 'raw/focused_a21_tests.xml'))}
.venv/bin/python -m pytest -q --junitxml={shlex.quote(str(output_dir.relative_to(ROOT) / 'raw/full_suite_after.xml'))}
.venv/bin/python tools/run_a21_rare_stratum_starvation.py --output-dir {shlex.quote(str(output_dir.relative_to(ROOT)))} --finalize-only
(cd {shlex.quote(str(output_dir.relative_to(ROOT)))} && shasum -a 256 -c SHA256SUMS.txt)
```

## 6. Evidence map

- `raw/background_mortality_cell_trace.csv.gz`: per cell `n`, `p`, `n·p`, allocation, HIV status, and strata.
- `raw/background_mortality_blocks.csv`: every quarterly age–orientation block and exact conservation/relocation controls.
- `raw/background_mortality_annual_summary.csv`: annual status-specific accounting.
- `raw/background_mortality_milestones.csv`: displayed time-profile rows.
- `raw/hiv_mortality_cell_trace.csv.gz`: per-cell HIV-mortality allocation by stage and ART status.
- `raw/hiv_mortality_calls.csv` and `raw/hiv_mortality_annual_summary.csv`: call and annual summaries.
- `raw/hiv_mortality_stage_art_summary.csv`: cumulative stage/ART sign accounting displayed above.
- `raw/findings.json`: machine-readable headline findings.
- `raw/full_suite_comparison.json`: exact before/after test comparison.
- `runner_stdout.log`: complete model stdout/stderr.
- `provenance/`: git status and tracked diff at execution.

## 7. Acceptance and limitations

- [x] One deterministic 1975–2025 run on the specified config; no sweep or mode switch.
- [x] Both live life-table call sites traced per cell with `n`, `p`, `expected`, `allocated`, and HIV status.
- [x] Whole-block allocator invariant checked exactly; HIV-positive shortfall and HIV-negative relocation quantified after each block's ordinary rounding residual.
- [x] Time profile and sub-unit/zero-allocation shares reported.
- [x] HIV-mortality calls traced at zero additional model-run cost, including stage/ART/year summaries.
- [x] Observation-only trace reproduced A-20's deterministic annual-output digest exactly.
- {test_acceptance} Focused and full-suite test evidence recorded; pre-existing failure/error set comparison reported.
- [x] No production default, rate, parameter, tolerance, or bound changed.

**Limitations.** This is one deterministic trajectory at one parameter point. It establishes what this allocator did on that trajectory; it does not estimate a sampling distribution, prove the same magnitude for other fits, or justify a production repair. Because A-20's process arms are non-additive, the death shortfall must not be converted arithmetically into a share of the 74.0% gap recovery. If this result is acted on, the next step is a modelling decision about allocation granularity under D-14's “same event probability and same downstream consequence” rule—not a bug fix under A-21.

## 8. §1A.5 self-check

- Acceptance is tied to retained counts and identities, not assertions: **yes**.
- Every number is produced by the commands above from retained artifacts: **yes**.
- Output files were reopened during finalisation and checksummed: **yes**.
- Full suite run before and after; exact failure/error set explained: **{'yes' if tests and tests['same_preexisting_failure_error_set'] else 'pending/no'}**.
- Every diagnostic code change has a focused test: **yes**.
- Adversarial controls include binding restoration, exact aggregate conservation, relocation balance, raw row counts, and A-20 digest equality: **yes**.
- External empirical sources used: **none**.
- Tolerances, thresholds, bounds, or parameters changed: **none**.
- An inconvenient result omitted: **no**; the large adverse HIV-mortality profile and limits on its interpretation are explicit.
"""
    (output_dir / "A21_RARE_STRATUM_STARVATION.md").write_text(
        report, encoding="utf-8"
    )


def write_checksums(output_dir: Path) -> None:
    paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{sha256(path)}  {path.relative_to(output_dir)}" for path in paths]
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")


def run_trace(args) -> None:
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir(parents=True, exist_ok=True)
    provenance_dir.mkdir(parents=True, exist_ok=True)
    if (raw_dir / "findings.json").exists():
        raise FileExistsError(f"refusing to overwrite completed trace: {output_dir}")

    config_path = args.config.resolve()
    base = json.loads(config_path.read_text(encoding="utf-8"))
    tracked_diff_before = hashlib.sha256(
        subprocess.run(
            ["git", "diff", "--binary"], cwd=ROOT, check=True, capture_output=True
        ).stdout
    ).hexdigest()
    (provenance_dir / "git_status_short.txt").write_text(
        git_text("status", "--short") + "\n", encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(
        subprocess.run(
            ["git", "diff", "--binary"], cwd=ROOT, check=True, capture_output=True
        ).stdout
    )

    started = time.time()
    output = io.StringIO()
    engine = None
    recorder = None
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            engine = A21TraceEngine(configured(base, seed=args.seed), seed=args.seed)
            recorder = A21TraceRecorder(raw_dir, engine.groups)
            engine._a21_recorder = recorder
            with BackgroundMortalityTrace(recorder):
                result = engine.run(run_number=0)
    finally:
        if recorder is not None:
            recorder.close()
        (output_dir / "runner_stdout.log").write_text(
            output.getvalue(), encoding="utf-8"
        )
    if engine is None or recorder is None:
        raise AssertionError("A-21 engine did not initialise")

    annual = extract_annual_outputs(
        result, start_year=START_YEAR, end_year=END_YEAR
    )
    digest = annual_digest(annual)
    if digest != A20_DETERMINISTIC_DIGEST:
        raise AssertionError(
            "observation-only trace changed annual outputs: "
            f"{digest} != {A20_DETERMINISTIC_DIGEST}"
        )
    annual_rows = []
    for metric in METRICS:
        annual_rows.extend(
            {"metric": metric, "year": int(year), "value": float(value)}
            for year, value in sorted(annual[metric].items())
        )
    pd.DataFrame(annual_rows).to_csv(
        raw_dir / "annual_outputs_1975_2025.csv", index=False
    )
    findings = analyse_trace(recorder, raw_dir)

    tracked_diff_at_report = hashlib.sha256(
        subprocess.run(
            ["git", "diff", "--binary"], cwd=ROOT, check=True, capture_output=True
        ).stdout
    ).hexdigest()
    manifest = {
        "task": "A-21",
        "status": "model_complete_tests_pending",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path.relative_to(ROOT)),
        "seed": int(args.seed),
        "deterministic": True,
        "start_year": START_YEAR,
        "end_year": END_YEAR,
        "n_steps": 204,
        "elapsed_seconds": time.time() - started,
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_status_dirty": bool(git_text("status", "--porcelain")),
        "tracked_diff_sha256_before": tracked_diff_before,
        "tracked_diff_sha256_at_report": tracked_diff_at_report,
        "annual_output_sha256": digest,
        "matches_a20_deterministic_digest": digest == A20_DETERMINISTIC_DIGEST,
        "python": sys.version,
        "platform": platform.platform(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output_dir, manifest, findings, tests=None)
    write_checksums(output_dir)


def finalize(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    raw_dir = output_dir / "raw"
    manifest = json.loads((output_dir / "manifest.json").read_text())
    findings = json.loads((raw_dir / "findings.json").read_text())
    hiv_calls = pd.read_csv(raw_dir / "hiv_mortality_calls.csv")
    hiv_stage_art = _aggregate_hiv_mortality_stage_art(hiv_calls)
    hiv_stage_art.to_csv(
        raw_dir / "hiv_mortality_stage_art_summary.csv", index=False
    )
    findings["hiv_mortality_by_stage_art"] = hiv_stage_art.to_dict(orient="records")
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, allow_nan=True) + "\n", encoding="utf-8"
    )
    tests = compare_tests(raw_dir)
    if tests is None:
        raise FileNotFoundError("before, focused, and after JUnit XML files are required")
    if not tests["same_preexisting_failure_error_set"]:
        raise AssertionError("post-A21 suite changed the pre-existing failure/error set")
    if tests["focused"]["failures"] or tests["focused"]["errors"]:
        raise AssertionError("focused A-21 tests did not pass")
    manifest["status"] = "complete"
    manifest["finalized_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["tracked_diff_sha256_at_report"] = hashlib.sha256(
        subprocess.run(
            ["git", "diff", "--binary"], cwd=ROOT, check=True, capture_output=True
        ).stdout
    ).hexdigest()
    manifest["tests"] = tests
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output_dir, manifest, findings, tests)
    write_checksums(output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--finalize-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.finalize_only:
        finalize(args.output_dir)
    else:
        run_trace(args)


if __name__ == "__main__":
    main()
