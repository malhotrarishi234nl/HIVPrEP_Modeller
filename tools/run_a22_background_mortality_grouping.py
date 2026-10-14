#!/usr/bin/env python3
"""Run A-22's background-mortality block-grouping diagnostic."""

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
from collections import defaultdict
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
from tools.a17_residual_placement import (  # noqa: E402
    METRICS,
    compare_annual_levels,
)
from tools.a22_background_mortality_grouping import (  # noqa: E402
    LEVELS,
    LEVEL_LABELS,
    allocate_background_events,
    grouped_target,
    grouping_slices,
)
from tools.run_a20_process_decomposition import annual_digest  # noqa: E402
from tools.run_a21_rare_stratum_starvation import parse_junit  # noqa: E402
from tools.run_b8_deterministic_target_level_comparison import (  # noqa: E402
    target_years_from_register,
)


START_YEAR = 1975
END_YEAR = 2025
DEFAULT_SEED = 7001
BASELINE_MEDIAN_ABS_LOG_RATIO = 0.18838
MATERIAL_THRESHOLD = 0.09419
MEDIAN_SE = 0.020609
ENG01_THRESHOLD = 0.50
A20_DETERMINISTIC_DIGEST = (
    "8d186b359195f3a911e5d53577b0cc900273466694356e513a9284c40197a455"
)
A21_HIV_POSITIVE_EXPECTED = 8029.743402348166
A21_HIV_POSITIVE_ALLOCATED = 6259
ERAS = (
    (1975, 1990, "1975-1990"),
    (1991, 2000, "1991-2000"),
    (2001, 2010, "2001-2010"),
    (2011, 2025, "2011-2025"),
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


def tracked_diff_bytes() -> bytes:
    return subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def configured(base: dict[str, Any], seed: int) -> dict[str, Any]:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": True}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": True
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def annual_rows(annual: dict[str, dict[int, float]], level: int) -> list[dict]:
    expected_years = set(range(START_YEAR, END_YEAR + 1))
    rows: list[dict] = []
    for metric in METRICS:
        if metric not in annual or set(annual[metric]) != expected_years:
            raise AssertionError(f"incomplete annual extraction for {metric}")
        rows.extend(
            {
                "level": level,
                "level_label": LEVEL_LABELS[level],
                "metric": metric,
                "year": int(year),
                "value": float(value),
            }
            for year, value in sorted(annual[metric].items())
        )
    return rows


def _calendar_year(year: float) -> int:
    return int(math.floor(float(year) + 1e-9))


class A22Recorder:
    """Retain exact block identities and pooled semantic-class accounting."""

    CLASS_AXES = {
        "activity": 0,
        "hiv_stage": 1,
        "diagnosis": 2,
        "art": 3,
        "prep": 4,
    }

    def __init__(self, level: int, groups: Any) -> None:
        self.level = int(level)
        self.groups = groups
        self.blocks: list[dict[str, Any]] = []
        self.classes: dict[tuple[str, int, str], dict[str, float]] = defaultdict(
            lambda: {"expected": 0.0, "allocated": 0.0}
        )

    def _add_class(
        self, axis: str, index: int, label: str, expected: float, allocated: int
    ) -> None:
        row = self.classes[(axis, int(index), str(label))]
        row["expected"] += float(expected)
        row["allocated"] += int(allocated)

    def record(
        self,
        *,
        year: float,
        age_index: int,
        orientation_index: int,
        n: np.ndarray,
        p: float,
        allocated: np.ndarray,
    ) -> None:
        population = np.asarray(n, dtype=np.int64)
        events = np.asarray(allocated, dtype=np.int64)
        expected = population.astype(float) * float(p)
        hiv_axis = np.arange(population.shape[1])[None, :, None, None, None]
        positive = np.broadcast_to(hiv_axis > 0, population.shape)
        positive_occupied = positive & (population > 0)
        positive_subunit = positive_occupied & (expected < 1.0)
        positive_zero = positive_occupied & (events == 0)

        if self.level == 5:
            cell_targets = np.minimum(
                np.round(expected).astype(np.int64), population
            )
            subgroup_error = int(np.abs(events - cell_targets).max())
        else:
            subgroup_error = 0
            for key in grouping_slices(population.shape, self.level):
                target = min(
                    int(np.round(float(expected[key].sum()))),
                    int(population[key].sum()),
                )
                subgroup_error = max(
                    subgroup_error, abs(int(events[key].sum()) - target)
                )

        self.blocks.append(
            {
                "level": self.level,
                "level_label": LEVEL_LABELS[self.level],
                "calendar_year": _calendar_year(year),
                "year": float(year),
                "age_index": int(age_index),
                "age": self.groups.age_groups[age_index],
                "orientation_index": int(orientation_index),
                "orientation": self.groups.orientations[orientation_index],
                "p": float(p),
                "expected": float(expected.sum()),
                "allocated": int(events.sum()),
                "production_block_target": min(
                    int(np.round(float(expected.sum()))), int(population.sum())
                ),
                "grouped_target": grouped_target(population, p, self.level),
                "max_abs_subgroup_control_error": int(subgroup_error),
                "hiv_positive_expected": float(expected[positive].sum()),
                "hiv_positive_allocated": int(events[positive].sum()),
                "hiv_positive_occupied_cells": int(positive_occupied.sum()),
                "hiv_positive_subunit_cells": int(positive_subunit.sum()),
                "hiv_positive_zero_cells": int(positive_zero.sum()),
            }
        )

        labels = {
            "activity": self.groups.activities,
            "hiv_stage": self.groups.hiv_stages,
            "diagnosis": self.groups.diagnosis_states,
            "art": self.groups.art_states,
            "prep": self.groups.prep_states,
        }
        for axis_name, axis in self.CLASS_AXES.items():
            for index, label in enumerate(labels[axis_name]):
                self._add_class(
                    axis_name,
                    index,
                    str(label),
                    float(np.take(expected, index, axis=axis).sum()),
                    int(np.take(events, index, axis=axis).sum()),
                )
        # Joint semantic classes catch a defect moved between combinations of
        # the declared axes even when a broad one-axis marginal looks healthy.
        # Age, orientation, and quarter are intentionally pooled because they
        # already define separate production calls and a single sub-half-event
        # call is not evidence of systematic class starvation.
        for index in np.ndindex(population.shape):
            label = "|".join(
                (
                    f"activity={labels['activity'][index[0]]}",
                    f"stage={labels['hiv_stage'][index[1]]}",
                    f"diagnosis={labels['diagnosis'][index[2]]}",
                    f"art={labels['art'][index[3]]}",
                    f"prep={labels['prep'][index[4]]}",
                )
            )
            self._add_class(
                "joint_semantic",
                int(np.ravel_multi_index(index, population.shape)),
                label,
                float(expected[index]),
                int(events[index]),
            )
        self._add_class(
            "hiv_status",
            0,
            "negative",
            float(expected[~positive].sum()),
            int(events[~positive].sum()),
        )
        self._add_class(
            "hiv_status",
            1,
            "positive",
            float(expected[positive].sum()),
            int(events[positive].sum()),
        )

    def class_rows(self) -> list[dict[str, Any]]:
        rows = []
        for (axis, index, label), totals in sorted(self.classes.items()):
            expected = totals["expected"]
            allocated = totals["allocated"]
            if expected <= 0.0:
                continue
            ratio = allocated / expected
            rows.append(
                {
                    "level": self.level,
                    "level_label": LEVEL_LABELS[self.level],
                    "class_axis": axis,
                    "class_index": index,
                    "class_label": label,
                    "expected": expected,
                    "allocated": int(allocated),
                    "allocation_ratio": ratio,
                    "below_50pct_expectation": bool(ratio < ENG01_THRESHOLD),
                }
            )
        return rows


class BackgroundGroupingPatch(AbstractContextManager):
    """Patch only live life-table allocations; restore after normal/error exit."""

    def __init__(self, level: int, recorder: A22Recorder) -> None:
        self.level = int(level)
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
        def grouped_allocator(n, p):
            if context._active is None:
                return context._original_allocator(n, p)
            allocated = allocate_background_events(
                n, p, context.level, allocator=context._original_allocator
            )
            active = context._active
            call_index = int(active["call_index"])
            orientation_order = active["orientation_order"]
            age_index, orientation_position = divmod(
                call_index, len(orientation_order)
            )
            if age_index >= active["groups"].n_age_groups:
                raise AssertionError("too many background allocator calls")
            context.recorder.record(
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
        def grouped_life_table(module, population, current_year, rng, deterministic):
            if not deterministic:
                return context._original_method(
                    module, population, current_year, rng, deterministic
                )
            if context._active is not None:
                raise RuntimeError("nested A-22 life-table grouping context")
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
                        f"expected {expected_calls} background calls, got {actual_calls}"
                    )
                return result
            finally:
                context._active = None

        demography_module.deterministic_allocate_events = grouped_allocator
        DemographyModule._calculate_life_table_mortality = grouped_life_table
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        import demography as demography_module
        from demography import DemographyModule

        demography_module.deterministic_allocate_events = self._original_allocator
        DemographyModule._calculate_life_table_mortality = self._original_method
        self._active = None
        return False


def score(
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    target_years: dict[str, list[int]],
    level: int,
) -> pd.DataFrame:
    comparison = compare_annual_levels(candidate, reference)
    comparison["level"] = level
    comparison["level_label"] = LEVEL_LABELS[level]
    scored = pd.concat(
        [
            comparison[
                (comparison["metric"] == metric)
                & comparison["year"].isin(years)
            ]
            for metric, years in target_years.items()
        ]
    )
    return scored.sort_values(["metric", "year"]).reset_index(drop=True)


def aggregate_identity(
    blocks: pd.DataFrame, class_audit: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    whole_rows = []
    era_rows = []
    for level, group in blocks.groupby("level", sort=True):
        expected = float(group["expected"].sum())
        allocated = int(group["allocated"].sum())
        positive_expected = float(group["hiv_positive_expected"].sum())
        positive_allocated = int(group["hiv_positive_allocated"].sum())
        occupied = int(group["hiv_positive_occupied_cells"].sum())
        subunit = int(group["hiv_positive_subunit_cells"].sum())
        zero = int(group["hiv_positive_zero_cells"].sum())
        level_classes = class_audit[class_audit["level"] == level]
        below = level_classes[level_classes["below_50pct_expectation"]]
        whole_rows.append(
            {
                "level": int(level),
                "level_label": LEVEL_LABELS[int(level)],
                "expected": expected,
                "allocated": allocated,
                "round_global_expected": int(np.round(expected)),
                "allocated_minus_round_global_expected": (
                    allocated - int(np.round(expected))
                ),
                "sum_production_block_targets": int(
                    group["production_block_target"].sum()
                ),
                "sum_grouped_targets": int(group["grouped_target"].sum()),
                "allocated_minus_sum_grouped_targets": (
                    allocated - int(group["grouped_target"].sum())
                ),
                "max_abs_subgroup_control_error": int(
                    group["max_abs_subgroup_control_error"].max()
                ),
                "hiv_positive_expected": positive_expected,
                "hiv_positive_allocated": positive_allocated,
                "hiv_positive_shortfall": positive_expected - positive_allocated,
                "hiv_positive_shortfall_fraction": (
                    (positive_expected - positive_allocated) / positive_expected
                ),
                "hiv_positive_subunit_share_occupied": subunit / occupied,
                "hiv_positive_zero_share_occupied": zero / occupied,
                "eng01_below_50pct_class_count": int(len(below)),
                "eng01_control_pass": bool(len(below) == 0),
                "eng01_min_class_allocation_ratio": float(
                    level_classes["allocation_ratio"].min()
                ),
                "eng01_below_50pct_classes": "; ".join(
                    f"{r.class_axis}={r.class_label} ({r.allocation_ratio:.3%})"
                    for r in below.itertuples()
                ),
            }
        )
        for first, last, label in ERAS:
            era = group[
                group["calendar_year"].between(first, last, inclusive="both")
            ]
            era_expected = float(era["hiv_positive_expected"].sum())
            era_allocated = int(era["hiv_positive_allocated"].sum())
            era_rows.append(
                {
                    "level": int(level),
                    "level_label": LEVEL_LABELS[int(level)],
                    "era": label,
                    "hiv_positive_expected": era_expected,
                    "hiv_positive_allocated": era_allocated,
                    "hiv_positive_shortfall": era_expected - era_allocated,
                    "hiv_positive_shortfall_fraction": (
                        (era_expected - era_allocated) / era_expected
                        if era_expected
                        else math.nan
                    ),
                }
            )
    return pd.DataFrame(whole_rows), pd.DataFrame(era_rows)


def _format_ratio(value: float) -> str:
    return f"{value:.3%}"


def write_report(
    output_dir: Path,
    manifest: dict[str, Any],
    findings: dict[str, Any],
    tests: dict[str, Any] | None,
) -> None:
    raw_dir = output_dir / "raw"
    summary = pd.read_csv(raw_dir / "level_summary.csv")
    identity = pd.read_csv(raw_dir / "background_mortality_identity_by_level.csv")
    eras = pd.read_csv(raw_dir / "background_mortality_identity_by_era.csv")

    ranked_rows = []
    for row in summary.sort_values("rank_by_primary").itertuples():
        ranked_rows.append(
            f"| {row.rank_by_primary} | {row.level} | {row.level_label} | "
            f"{row.median_absolute_log_ratio:.5f} | "
            f"{'yes' if row.material else 'no'} | {row.elapsed_seconds:.2f} | "
            f"{row.annual_output_sha256[:12]} |"
        )

    identity_rows = []
    for row in identity.itertuples():
        identity_rows.append(
            f"| {row.level} | {row.hiv_positive_expected:,.3f} | "
            f"{row.hiv_positive_allocated:,.0f} | "
            f"{_format_ratio(row.hiv_positive_shortfall_fraction)} | "
            f"{_format_ratio(row.hiv_positive_subunit_share_occupied)} | "
            f"{_format_ratio(row.hiv_positive_zero_share_occupied)} | "
            f"{row.allocated:,} vs {row.round_global_expected:,} "
            f"({row.allocated_minus_round_global_expected:+,}) | "
            f"{row.allocated_minus_sum_grouped_targets:+,} | "
            f"{'PASS' if row.eng01_control_pass else 'FAIL'} "
            f"({row.eng01_below_50pct_class_count}) |"
        )

    era_rows = []
    for row in eras.itertuples():
        era_rows.append(
            f"| {row.level} | {row.era} | {row.hiv_positive_expected:,.3f} | "
            f"{row.hiv_positive_allocated:,.0f} | "
            f"{_format_ratio(row.hiv_positive_shortfall_fraction)} |"
        )

    recommendation_rows = []
    for row in summary.sort_values("level").itertuples():
        role = "control" if row.level in (4, 5) else "candidate"
        if row.level == findings.get("recommended_level"):
            verdict = "recommend"
        elif row.material and role == "candidate":
            verdict = "not preferred under the coarser-within-resolution rule"
        elif role == "control":
            verdict = "inadmissible falsification control"
        else:
            verdict = "do not recommend; not material"
        recommendation_rows.append(
            f"- Level {row.level}: **{verdict}**; cost {row.elapsed_seconds:.2f} s "
            f"for one deterministic 1975–2025 trajectory; ENG-01 "
            f"{'passes' if row.eng01_control_pass else 'fails'}."
        )

    if tests is None:
        tests_text = "Test finalisation pending."
        test_box = "[ ]"
    else:
        before, after, focused = tests["before"], tests["after"], tests["focused"]
        tests_text = (
            f"Focused A-22: **{focused['passed']} passed**. Full suite before: "
            f"{before['failures']} failed / {before['passed']} passed / "
            f"{before['skipped']} skipped / {before['errors']} errors; after: "
            f"{after['failures']} failed / {after['passed']} passed / "
            f"{after['skipped']} skipped / {after['errors']} errors. Exact "
            f"failure/error identity set unchanged: "
            f"**{str(tests['same_preexisting_failure_error_set']).lower()}**."
        )
        test_box = "[x]" if tests["same_preexisting_failure_error_set"] else "[!]"

    recommended = findings.get("recommended_level")
    result_sentence = (
        f"Level **{recommended}** is recommended for Rishi's D-14 decision."
        if recommended is not None
        else "No grouping level is recommended; the escalation condition fired."
    )
    before_destination = output_dir.relative_to(ROOT) / "raw/full_suite_before.xml"
    before_source_text = manifest.get("full_suite_before_source")
    if before_source_text and before_source_text != str(before_destination):
        before_commands = (
            f".venv/bin/python -m pytest -q --junitxml={shlex.quote(before_source_text)}\n"
            f"cp {shlex.quote(before_source_text)} {shlex.quote(str(before_destination))}"
        )
        before_source_arg = (
            " --full-suite-before-source " + shlex.quote(before_source_text)
        )
    else:
        before_commands = (
            ".venv/bin/python -m pytest -q --junitxml="
            f"{shlex.quote(str(before_destination))}"
        )
        before_source_arg = ""

    report = f"""# A-22 — background-mortality block grouping

**Status:** **{findings['status'].upper()}**. {result_sentence}  
**Task type:** measurement and recommendation only; production behavior was not changed.  
**Claim strength:** results are **well-supported for this one deterministic trajectory and one parameter point**; extrapolation to other fits is **unsupported**.

## 1. Pre-specified criterion and resolution

The primary decision variable is the median absolute log ratio against the post-CRN stochastic ensemble (**n=50**, seeds 7001–7050) across the 222 scored target-years. A level is material below **0.09419**, half the deterministic baseline 0.18838. The median per-year log-ratio SE is **0.020609**, so the 0.09419 threshold is **4.57× the median SE** and can discriminate. Levels within **one stated median SE (0.020609)** of the best material candidate are treated as indistinguishable and the coarser is preferred; this uses the same stated resolution rather than a newly fitted threshold.

The mandatory ENG-01 control follows the brief's fixed **50% of expectation** threshold. A “cell class” is pre-defined here as both (a) each one-axis semantic marginal—HIV status, HIV stage, diagnosis, ART, activity, or PrEP—and (b) each joint activity × HIV-stage × diagnosis × ART × PrEP class. Every class is pooled over ages, orientations, and quarters; zero-expectation structural classes are excluded. This pooling avoids declaring a class starved from a single sub-half-event call while the joint classes prevent a broad marginal from hiding relocation into another semantic combination.

Every level uses the **same unchanged life-table mortality probability** for every person. Only the cells that compete in the same deterministic integer-allocation block change.

## 2. Primary result — all six levels ranked

| rank | level | grouping | median absolute log ratio | material | runtime (s) | annual-output digest |
|---:|---:|---|---:|:---:|---:|---|
{chr(10).join(ranked_rows)}

Observed level-0 baseline: **{findings['baseline_observed']:.7f}**. Recommendation status: **{findings['status']}**. {findings['recommendation_reason']}

## 3. A-21 identity, conservation, and ENG-01 control

`allocated vs round(global expected)` reports the literal all-cell comparison requested in the brief. Because rates are allocated separately by age, orientation, quarter, and (for levels 1–5) subgroup, ordinary rounding does not commute with summation. The load-bearing conservation identity is therefore also reported: total allocation equals the **sum of the rounded target of every actual allocation block**. It closes exactly at every level.

| level | HIV+ expected | HIV+ allocated | shortfall | HIV+ sub-unit occupied | HIV+ occupied receiving zero | allocated vs round(global expected) (difference) | allocation − sum(group targets) | ENG-01 (classes below 50%) |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(identity_rows)}

The full semantic-class audit, including the names and ratios of every failure, is `raw/eng01_cell_class_audit.csv`. Maximum absolute within-group conservation error is **{findings['max_abs_subgroup_control_error']}** deaths.

### Era breakdown

| level | era | HIV+ expected | HIV+ allocated | shortfall |
|---:|---|---:|---:|---:|
{chr(10).join(era_rows)}

## 4. Recommendation and cost

{chr(10).join(recommendation_rows)}

The recommendation is based only on the pre-specified epidemiological target-year measure, the stated resolution/tie-break, the A-21 identity, and the ENG-01 control. No ICER, ΔQALY, or other economic output was computed.

## 5. Controls, output collapse, and integrity

- Level 0 annual outputs match production/A-20 bit-for-bit: **{str(findings['level0_matches_a20_digest']).lower()}** (`{findings['level0_digest']}`).
- Level 0 reproduces B-8 in **{findings['b8_n_scored_target_years']}/222** target-years; maximum absolute difference **{findings['b8_max_absolute_difference']}**.
- Level 0 reproduces A-21 HIV-positive background deaths exactly: expected **{findings['a21_level0_expected']!r}**, allocated **{findings['a21_level0_allocated']}**.
- Byte-identical annual-output groups: {findings['collapse_description']}.
- Re-run trigger: repeat A-22 if configuration or model changes cause currently empty/structurally restricted diagnosis, ART, or PrEP states to become occupied, because grouping effects are occupancy-dependent.
- The patch is opt-in and restoring; focused tests cover restoration after an exception. {tests_text}
- Tracked diff hash before implementation: `{manifest['tracked_diff_sha256_before']}`; at report finalisation: `{manifest.get('tracked_diff_sha256_at_report', 'pending')}`. Existing tracked changes pre-date A-22; A-22 changed no tracked production file.

## 6. Exact commands

```bash
{before_commands}
.venv/bin/python -m pytest -q tests/test_a22_background_mortality_grouping.py --junitxml={shlex.quote(str(output_dir.relative_to(ROOT) / 'raw/focused_a22_tests.xml'))}
.venv/bin/python tools/run_a22_background_mortality_grouping.py --config configs/V13.22_20260604_nearfit_rank001_basecase.json --target-register configs/hm_target_register.json --stochastic-reference outputs/calibration/a20_process_decomposition_20260912_221520/raw/stochastic_reference_1975_2025.csv --b8-deterministic outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/raw/deterministic_annual_outputs.csv --output-dir {shlex.quote(str(output_dir.relative_to(ROOT)))}{before_source_arg}
.venv/bin/python -m pytest -q --junitxml={shlex.quote(str(output_dir.relative_to(ROOT) / 'raw/full_suite_after.xml'))}
.venv/bin/python tools/run_a22_background_mortality_grouping.py --output-dir {shlex.quote(str(output_dir.relative_to(ROOT)))} --finalize-only
(cd {shlex.quote(str(output_dir.relative_to(ROOT)))} && shasum -a 256 -c SHA256SUMS.txt)
```

## 7. Evidence map

- `raw/level_summary.csv` and `raw/scored_target_years.csv`: primary ranking and all 222 comparisons per level.
- `raw/background_mortality_identity_by_level.csv` and `raw/background_mortality_identity_by_era.csv`: A-21 identities and era profile.
- `raw/background_mortality_blocks.csv`: every realised allocation-call identity.
- `raw/eng01_cell_class_audit.csv`: mandatory semantic-class starvation control.
- `raw/annual_outputs_1975_2025.csv`, `raw/output_collapse_matrix.csv`, and `raw/run_timings.csv`: trajectories, exact-collapse audit, and cost.
- `raw/control_results.json`, `raw/findings.json`, `manifest.json`, and `provenance/`: controls and reproduction metadata.

## 8. Acceptance and prominent limitation

- [x] Six deterministic levels run over 1975–2025 at the specified parameter point.
- [x] Criterion and resolution stated above results; all levels ranked.
- [x] A-21 whole-run and era identities, occupied-cell regime, conservation, and ENG-01 control reported per level.
- [x] Runtime and byte-identical output collapse audited.
- [x] Level-0 production, B-8, and A-21 controls passed exactly.
- {test_box} Focused and full-suite evidence recorded; pre-existing failure/error identity set compared.
- [x] No mortality rate, value, tolerance, bound, production default, or other call site changed; no economic output computed.

**Limitation — prominent:** this is **one parameter point and one deterministic trajectory**. It establishes what the allocator does at this fit, not the magnitude at other fits. The recommendation is therefore a D-14 modelling recommendation, not a production implementation.

## 9. §1A.5 self-check

- Acceptance criterion met with retained numbers: **yes**.
- Every figure re-runnable from exact commands and retained artifacts: **yes**.
- Outputs reopened and used to generate this report: **yes**.
- Full suite before/after and exact failure/error set recorded: **{'yes' if tests else 'pending'}**.
- Every diagnostic change has focused tests, including adversarial restoration and control fixtures: **yes**.
- Sources used: retained model artifacts and source code only; no external empirical claim added.
- Threshold, tolerance, bound, probability, or parameter changed: **no**.
- Inconvenient outcomes omitted: **no**; both falsification controls and every ENG-01 failure are retained.
"""
    (output_dir / "A22_BACKGROUND_MORTALITY_GROUPING.md").write_text(
        report, encoding="utf-8"
    )


def write_checksums(output_dir: Path) -> None:
    paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(output_dir)}\n" for path in paths),
        encoding="utf-8",
    )


def compare_tests(raw_dir: Path) -> dict[str, Any] | None:
    paths = {
        "before": raw_dir / "full_suite_before.xml",
        "focused": raw_dir / "focused_a22_tests.xml",
        "after": raw_dir / "full_suite_after.xml",
    }
    if not all(path.exists() for path in paths.values()):
        return None
    parsed = {name: parse_junit(path) for name, path in paths.items()}
    parsed["same_preexisting_failure_error_set"] = (
        parsed["before"]["problematic"] == parsed["after"]["problematic"]
    )
    parsed["test_count_delta"] = parsed["after"]["tests"] - parsed["before"]["tests"]
    (raw_dir / "full_suite_comparison.json").write_text(
        json.dumps(parsed, indent=2) + "\n", encoding="utf-8"
    )
    return parsed


def run(args: argparse.Namespace) -> None:
    if args.seed != DEFAULT_SEED:
        raise ValueError("A-22 deterministic control seed is fixed at 7001")
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir(parents=True, exist_ok=True)
    provenance_dir.mkdir(parents=True, exist_ok=True)
    if (raw_dir / "findings.json").exists():
        raise FileExistsError(f"refusing to overwrite completed A-22 run: {output_dir}")

    config_path = args.config.resolve()
    register_path = args.target_register.resolve()
    reference_path = args.stochastic_reference.resolve()
    b8_path = args.b8_deterministic.resolve()
    before_source = (
        args.full_suite_before_source.resolve()
        if args.full_suite_before_source is not None
        else raw_dir / "full_suite_before.xml"
    )
    before_destination = raw_dir / "full_suite_before.xml"
    if not before_destination.exists():
        raise FileNotFoundError("full-suite before JUnit artifact is required before A-22")
    if not before_source.exists() or sha256(before_source) != sha256(before_destination):
        raise AssertionError("copied full-suite before artifact does not match its source")
    tracked_before = tracked_diff_bytes()
    tracked_before_hash = hashlib.sha256(tracked_before).hexdigest()
    status_before = git_text("status", "--short")
    (provenance_dir / "git_status_short.txt").write_text(
        status_before + ("\n" if status_before else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(tracked_before)

    register = json.loads(register_path.read_text(encoding="utf-8"))
    target_years = target_years_from_register(register)
    if sum(len(years) for years in target_years.values()) != 222:
        raise AssertionError("target register no longer yields 222 scored years")
    reference = pd.read_csv(reference_path)
    if sorted(reference["seed"].unique()) != list(range(7001, 7051)):
        raise AssertionError("stochastic reference is not seeds 7001-7050")
    base = json.loads(config_path.read_text(encoding="utf-8"))

    manifest = {
        "task": "A-22 background-mortality block grouping",
        "status": "running",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path.relative_to(ROOT)),
        "target_register": str(register_path.relative_to(ROOT)),
        "stochastic_reference": str(reference_path.relative_to(ROOT)),
        "b8_deterministic": str(b8_path.relative_to(ROOT)),
        "seed": args.seed,
        "stochastic_reference_seeds": list(range(7001, 7051)),
        "levels": list(LEVELS),
        "window": [START_YEAR, END_YEAR],
        "criterion": {
            "baseline": BASELINE_MEDIAN_ABS_LOG_RATIO,
            "material_threshold": MATERIAL_THRESHOLD,
            "median_per_year_log_ratio_se": MEDIAN_SE,
            "threshold_in_median_se": MATERIAL_THRESHOLD / MEDIAN_SE,
            "indistinguishable_band": MEDIAN_SE,
            "eng01_threshold": ENG01_THRESHOLD,
        },
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_dirty": bool(status_before),
        "tracked_diff_sha256_before": tracked_before_hash,
        "full_suite_before_source": str(before_source.relative_to(ROOT)),
        "full_suite_before_sha256": sha256(before_destination),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                config_path,
                register_path,
                reference_path,
                b8_path,
                ROOT / "demography.py",
                ROOT / "stochastic_allocation.py",
                ROOT / "tools/a22_background_mortality_grouping.py",
                Path(__file__).resolve(),
            )
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    all_annual_rows: list[dict] = []
    all_blocks: list[dict] = []
    all_class_rows: list[dict] = []
    timings: list[dict] = []
    digests: dict[int, str] = {}
    output = io.StringIO()
    started = time.time()
    for level in LEVELS:
        print(f"A-22 level {level}/5: {LEVEL_LABELS[level]}", flush=True)
        run_output = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(run_output), contextlib.redirect_stderr(run_output):
            engine = SimulationEngineV3(configured(base, args.seed), seed=args.seed)
            recorder = A22Recorder(level, engine.groups)
            with BackgroundGroupingPatch(level, recorder):
                result = engine.run(run_number=level)
        elapsed = time.time() - t0
        output.write(f"===== level {level}: {LEVEL_LABELS[level]} =====\n")
        output.write(run_output.getvalue())
        annual = extract_annual_outputs(result, START_YEAR, END_YEAR)
        digest = annual_digest(annual)
        digests[level] = digest
        all_annual_rows.extend(annual_rows(annual, level))
        all_blocks.extend(recorder.blocks)
        all_class_rows.extend(recorder.class_rows())
        timings.append(
            {
                "level": level,
                "level_label": LEVEL_LABELS[level],
                "elapsed_seconds": elapsed,
                "annual_output_sha256": digest,
            }
        )
        print(f"A-22 level {level} complete in {elapsed:.2f}s", flush=True)
    (output_dir / "runner_stdout.log").write_text(output.getvalue(), encoding="utf-8")

    annual_frame = pd.DataFrame(all_annual_rows)
    blocks = pd.DataFrame(all_blocks)
    class_audit = pd.DataFrame(all_class_rows)
    annual_frame.to_csv(raw_dir / "annual_outputs_1975_2025.csv", index=False)
    blocks.to_csv(raw_dir / "background_mortality_blocks.csv", index=False)
    class_audit.to_csv(raw_dir / "eng01_cell_class_audit.csv", index=False)
    pd.DataFrame(timings).to_csv(raw_dir / "run_timings.csv", index=False)

    identity, eras = aggregate_identity(blocks, class_audit)
    identity.to_csv(raw_dir / "background_mortality_identity_by_level.csv", index=False)
    eras.to_csv(raw_dir / "background_mortality_identity_by_era.csv", index=False)

    scored_frames = []
    summary_rows = []
    for level in LEVELS:
        candidate = annual_frame[annual_frame["level"] == level][
            ["metric", "year", "value"]
        ]
        scored = score(candidate, reference, target_years, level)
        scored_frames.append(scored)
        primary = float(scored["log_ratio_det_over_stoch_mean"].abs().median())
        ident = identity.loc[identity["level"] == level].iloc[0]
        timing = timings[level]
        summary_rows.append(
            {
                "level": level,
                "level_label": LEVEL_LABELS[level],
                "median_absolute_log_ratio": primary,
                "material": bool(primary < MATERIAL_THRESHOLD),
                "difference_from_baseline": primary - BASELINE_MEDIAN_ABS_LOG_RATIO,
                "eng01_control_pass": bool(ident["eng01_control_pass"]),
                "eng01_below_50pct_class_count": int(
                    ident["eng01_below_50pct_class_count"]
                ),
                "elapsed_seconds": timing["elapsed_seconds"],
                "annual_output_sha256": digests[level],
            }
        )
    scored_all = pd.concat(scored_frames, ignore_index=True)
    scored_all.to_csv(raw_dir / "scored_target_years.csv", index=False)
    summary = pd.DataFrame(summary_rows).sort_values(
        ["median_absolute_log_ratio", "level"]
    )
    summary["rank_by_primary"] = np.arange(1, len(summary) + 1)
    summary.to_csv(raw_dir / "level_summary.csv", index=False)

    level0_primary = float(
        summary.loc[summary["level"] == 0, "median_absolute_log_ratio"].iloc[0]
    )
    if not np.isclose(level0_primary, 0.1883772, atol=5e-7):
        raise AssertionError(f"level 0 primary baseline changed: {level0_primary}")
    if digests[0] != A20_DETERMINISTIC_DIGEST:
        raise AssertionError("level 0 does not reproduce A-20 production output")

    b8 = pd.read_csv(b8_path)
    level0 = annual_frame[annual_frame["level"] == 0]
    b8_cmp = level0.merge(b8, on=["metric", "year"], suffixes=("_fresh", "_b8"))
    b8_scored = pd.concat(
        [
            b8_cmp[
                (b8_cmp["metric"] == metric) & b8_cmp["year"].isin(years)
            ]
            for metric, years in target_years.items()
        ]
    )
    b8_max_diff = float((b8_scored["value_fresh"] - b8_scored["value_b8"]).abs().max())
    if len(b8_scored) != 222 or b8_max_diff != 0.0:
        raise AssertionError("level 0 fails the B-8 control")

    level0_identity = identity[identity["level"] == 0].iloc[0]
    if float(level0_identity["hiv_positive_expected"]) != A21_HIV_POSITIVE_EXPECTED:
        raise AssertionError("level 0 fails A-21 HIV+ expected-death control")
    if int(level0_identity["hiv_positive_allocated"]) != A21_HIV_POSITIVE_ALLOCATED:
        raise AssertionError("level 0 fails A-21 HIV+ allocated-death control")
    if int(identity["max_abs_subgroup_control_error"].max()) != 0:
        raise AssertionError("a grouping level violated within-block conservation")

    collapse_rows = []
    for left in LEVELS:
        for right in LEVELS:
            collapse_rows.append(
                {
                    "level_left": left,
                    "level_right": right,
                    "byte_identical_annual_output": digests[left] == digests[right],
                }
            )
    pd.DataFrame(collapse_rows).to_csv(
        raw_dir / "output_collapse_matrix.csv", index=False
    )
    digest_groups: dict[str, list[int]] = defaultdict(list)
    for level, digest in digests.items():
        digest_groups[digest].append(level)
    collapse_description = "; ".join(
        "/".join(map(str, levels)) if len(levels) > 1 else f"{levels[0]} alone"
        for levels in digest_groups.values()
    )

    best_all = int(summary.iloc[0]["level"])
    status = "complete"
    recommended_level: int | None = None
    if best_all in (4, 5):
        status = "measurement_problem_control_won"
        recommendation_reason = (
            f"Level {best_all}, a falsification control, ranked best; per the brief, "
            "stop and diagnose the measurement rather than recommend it."
        )
    else:
        material_candidates = summary[
            (summary["level"] <= 3) & summary["material"]
        ]
        if material_candidates.empty:
            status = "no_material_level"
            recommendation_reason = (
                "No candidate crossed the fixed materiality threshold; grouping alone "
                "does not repair deterministic calibration at this fit."
            )
        else:
            best_candidate_primary = float(
                material_candidates["median_absolute_log_ratio"].min()
            )
            indistinguishable = material_candidates[
                material_candidates["median_absolute_log_ratio"]
                <= best_candidate_primary + MEDIAN_SE
            ]
            chosen = indistinguishable.sort_values("level").iloc[0]
            if not bool(chosen["eng01_control_pass"]):
                status = "eng01_control_failed"
                recommendation_reason = (
                    f"Level {int(chosen['level'])} is selected by the primary/tie-break "
                    "rule but moves a semantic cell class below 50% of expectation; "
                    "the brief forbids recommending it."
                )
            else:
                recommended_level = int(chosen["level"])
                recommendation_reason = (
                    f"Level {recommended_level} is material and is the coarsest candidate "
                    "within one stated median SE of the best material candidate; its "
                    "ENG-01 semantic-class control passes."
                )

    controls = {
        "level0_matches_a20_digest": digests[0] == A20_DETERMINISTIC_DIGEST,
        "level0_digest": digests[0],
        "b8_n_scored_target_years": int(len(b8_scored)),
        "b8_max_absolute_difference": b8_max_diff,
        "a21_level0_expected": float(level0_identity["hiv_positive_expected"]),
        "a21_level0_allocated": int(level0_identity["hiv_positive_allocated"]),
        "max_abs_subgroup_control_error": int(
            identity["max_abs_subgroup_control_error"].max()
        ),
    }
    (raw_dir / "control_results.json").write_text(
        json.dumps(controls, indent=2) + "\n", encoding="utf-8"
    )
    findings = {
        "status": status,
        "recommended_level": recommended_level,
        "recommendation_reason": recommendation_reason,
        "baseline_observed": level0_primary,
        "best_level_all_six": best_all,
        "material_threshold": MATERIAL_THRESHOLD,
        "median_se": MEDIAN_SE,
        "collapse_description": collapse_description,
        "elapsed_seconds": time.time() - started,
        **controls,
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )
    manifest["status"] = "model_complete_tests_pending"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["elapsed_seconds"] = findings["elapsed_seconds"]
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output_dir, manifest, findings, tests=None)
    write_checksums(output_dir)
    print(json.dumps(findings, indent=2), flush=True)


def finalize(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    raw_dir = output_dir / "raw"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    findings = json.loads((raw_dir / "findings.json").read_text(encoding="utf-8"))
    tests = compare_tests(raw_dir)
    if tests is None:
        raise FileNotFoundError("before, focused, and after JUnit XML files are required")
    if tests["focused"]["failures"] or tests["focused"]["errors"]:
        raise AssertionError("focused A-22 tests did not pass")
    if not tests["same_preexisting_failure_error_set"]:
        raise AssertionError("post-A-22 suite changed the pre-existing failure/error set")
    current_hash = hashlib.sha256(tracked_diff_bytes()).hexdigest()
    if current_hash != manifest["tracked_diff_sha256_before"]:
        raise AssertionError("A-22 changed the pre-existing tracked diff")
    manifest["status"] = "complete"
    manifest["finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["tracked_diff_sha256_at_report"] = current_hash
    manifest["tests"] = tests
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output_dir, manifest, findings, tests)
    write_checksums(output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--target-register", type=Path, default=ROOT / "configs/hm_target_register.json"
    )
    parser.add_argument(
        "--stochastic-reference",
        type=Path,
        default=(
            ROOT
            / "outputs/calibration/a20_process_decomposition_20260912_221520/raw/stochastic_reference_1975_2025.csv"
        ),
    )
    parser.add_argument(
        "--b8-deterministic",
        type=Path,
        default=(
            ROOT
            / "outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/raw/deterministic_annual_outputs.csv"
        ),
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--full-suite-before-source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--finalize-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.finalize_only:
        finalize(args.output_dir)
    else:
        run(args)


if __name__ == "__main__":
    main()
