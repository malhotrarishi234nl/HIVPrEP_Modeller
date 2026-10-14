#!/usr/bin/env python3
"""Focused stage-structured diagnosis calibration and decision harness (2026-09-22).

Brief: TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_FOCUSED_CALIBRATION_2026-09-22.md

Structural investigation only: not a production calibration and not an HTA
baseline. Two fixed epidemiologic backbones (candidates 1326 and 1788) keep every
injected parameter except the three MSM-relative routine diagnosis multiplier
anchors and the AIDS symptom probability.

Every mode is a separate command; preparation and execution are never combined:

  --validate-only      build and check every configuration; no engine, no writes
  --prepare            create a NEW output directory and freeze plan and design
  --smoke              optional six-trajectory identity/boundary check (own directory)
  --stage a|b|c        run one frozen stage (c needs --shortlist-sha256)
  --freeze-shortlist   freeze the Stage-C shortlist from a COMPLETE Stage B
  --report             write summaries, figures and REPORT.md from the journals
  --orchestrate        A -> B -> freeze -> (C only with --shortlist-sha256) -> report
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import math
import os
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_stage_structured_diagnosis_audit as audit
from aids_observation import OBSERVED_AIDS_PROXY_COMPONENTS
from config_utils import extract_value
from coupled_calibration_search import ContractError
from schedule_utils import TimeSchedule
from stage_structured_diagnosis import load_stage_structured_diagnosis

ROOT = Path(__file__).resolve().parent
DESIGN = "stage_structured_diagnosis_focused_calibration_20260922"
DEFAULT_PLAN = ROOT / "configs/stage_structured_diagnosis_focused_calibration_20260922.json"
DEFAULT_OUTPUT = ROOT / "outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922"
DEFAULT_SMOKE_OUTPUT = ROOT / (
    "outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922_smoke"
)
NOTE_SOURCE = ROOT / "IMPLEMENTATION_NOTE_STAGE_STRUCTURED_DIAGNOSIS_FOCUSED_CALIBRATION_2026-09-22.md"

PROFILE_PATH = ("transmission_model", "disease", "diagnosis", "stage_structured_diagnosis")
MSM_SCHEDULE_PATH = ("transmission_model", "disease", "diagnosis", "msm_relative_diagnosis",
                     "schedule", "value")
BASE_SCHEDULE_PATH = ("transmission_model", "disease", "diagnosis", "schedule")
MULTIPLIER_NAMES = ("msm_diag_mult_1996", "msm_diag_mult_2004", "msm_diag_mult_2019")
SYMPTOM_NAME = "aids_symptom_annual_probability"
FOCUSED_NAMES = MULTIPLIER_NAMES + (SYMPTOM_NAME,)
EXPECTED_FOCUSED = {
    "msm_diag_mult_1996": ("1996", 0.0, 2.0),
    "msm_diag_mult_2004": ("2004", 0.0, 2.0),
    "msm_diag_mult_2019": ("2019", 0.0, 2.0),
    SYMPTOM_NAME: (None, 0.0, 0.5),
}
STAGES = ("stage_a", "stage_b", "stage_c")
STAGE_ALIASES = {"a": "stage_a", "b": "stage_b", "c": "stage_c"}
DX_STAGES = ("acute", "chronic", "aids")

STATUS_PREPARED = "PREPARED"
STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"
STATUS_PARTIAL = "PARTIAL_TIME_LIMIT"

#: Quarterly grid on which effective routine paths are compared for duplicates.
PATH_GRID = tuple(1975.0 + 0.25 * i for i in range(51 * 4))
#: Series retained for pre-1996 years (the 1985 boundary check); every series is
#: retained for 1996-2025.
EARLY_SERIES = (
    "new_diagnoses_endogenous", "new_diagnoses_endogenous_msm",
    "new_diagnoses_endogenous_non_msm", "new_diagnoses_exogenous_non_msm",
    *(f"hiv_diagnoses_in_{s}_stage{suffix}" for s in DX_STAGES
      for suffix in ("", "_msm", "_non_msm")),
    "aids_progression_while_undiagnosed_msm", "aids_progression_while_diagnosed_msm",
)

LABEL_SUPPORTED = "Mechanism supported for broader recalibration"
LABEL_INFORMATIVE = "Mechanism informative but insufficient"
LABEL_NOT_SUPPORTED = "Mechanism not supported over the tested focused space"
DECISION_LABELS = (LABEL_SUPPORTED, LABEL_INFORMATIVE, LABEL_NOT_SUPPORTED)


# ==========================================================================
# Small helpers
# ==========================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(path: Path, value) -> None:
    rcc.dump_json(Path(path), value)


def _read(path: Path):
    return json.loads(Path(path).read_text())


def _get(mapping: Mapping, path: Sequence[str]):
    node = mapping
    for key in path:
        node = node[key]
    return node


def _flatten(obj, prefix: tuple = ()) -> dict[tuple, object]:
    if isinstance(obj, dict):
        out: dict[tuple, object] = {}
        if not obj:
            out[prefix] = {}
        for key, value in obj.items():
            out.update(_flatten(value, prefix + (str(key),)))
        return out
    if isinstance(obj, list):
        out = {}
        if not obj:
            out[prefix] = []
        for i, value in enumerate(obj):
            out.update(_flatten(value, prefix + (f"[{i}]",)))
        return out
    return {prefix: obj}


def changed_paths(before: Mapping, after: Mapping) -> list[str]:
    """Dotted leaf paths that differ, were added, or were removed."""
    a, b = _flatten(before), _flatten(after)
    keys = set(a) | set(b)
    out = []
    for key in keys:
        if key not in a or key not in b or type(a[key]) is not type(b[key]) or a[key] != b[key]:
            out.append(".".join(key))
    return sorted(out)


def _allowed(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or path.startswith(p + ".") for p in prefixes)


class EngineGuard:
    """Makes any SimulationEngineV3 construction inside the block fail loudly.

    Used by --validate-only so that "zero engines constructed" is enforced, not
    merely asserted.
    """

    def __enter__(self):
        import run_v3  # noqa: PLC0415

        self._cls = run_v3.SimulationEngineV3
        self._init = self._cls.__init__

        def refuse(*_args, **_kwargs):
            raise ContractError("engine construction is forbidden in this mode")

        self._cls.__init__ = refuse
        return self

    def __exit__(self, *exc):
        self._cls.__init__ = self._init
        return False


# ==========================================================================
# Plan
# ==========================================================================

def _seed_sets(plan: dict) -> dict[str, list[int]]:
    return {
        "stage_a": [int(s) for s in plan["stage_a"]["seeds"]],
        "stage_b": [int(s) for s in plan["stage_b"]["seeds"]],
        "stage_c": [int(s) for s in plan["stage_c"]["seeds"]],
        "smoke": [int(s) for s in plan["smoke"]["seeds"]],
        "lhs_rng": [int(plan["stage_b"]["lhs_rng_seed"])],
    }


def stage_a_grid(plan: dict) -> list[tuple[float, float]]:
    return [(float(r), float(p)) for r in plan["stage_a"]["rho"]
            for p in plan["stage_a"]["p_symptom_aids"]]


def load_plan(path: Path = DEFAULT_PLAN) -> dict:
    plan = _read(path)
    if plan.get("design") != DESIGN or plan.get("status") != "FROZEN":
        raise ContractError("plan is not the frozen focused-calibration design")
    if [int(x) for x in plan["candidate_ids"]] != [1326, 1788]:
        raise ContractError("backbones must be exactly candidates 1326 and 1788")
    if int(plan["symptom_start_year"]) != 1985:
        raise ContractError("symptom_start_year must be the declared 1985 boundary")
    if float(plan["stage_b_routine_hazard_scale"]) != 1.0:
        raise ContractError("Stage B must fix routine_hazard_scale = 1")
    focused = {p["name"]: (p["anchor_year"], float(p["lower"]), float(p["upper"]))
               for p in plan["focused_parameters"]}
    if [p["name"] for p in plan["focused_parameters"]] != list(FOCUSED_NAMES) or \
            focused != EXPECTED_FOCUSED:
        raise ContractError(f"focused coordinates or ranges differ from the brief: {focused}")
    rho = [float(x) for x in plan["stage_a"]["rho"]]
    symptom = [float(x) for x in plan["stage_a"]["p_symptom_aids"]]
    if rho != [0.70, 0.75, 0.80, 0.85, 0.90, 0.95] or symptom != [0.0, 0.1, 0.2, 0.3, 0.4]:
        raise ContractError("Stage-A grid differs from the brief")
    n_backbones = len(plan["candidate_ids"])
    seeds = _seed_sets(plan)
    budgets = {
        "stage_a": n_backbones * len(rho) * len(symptom) * len(seeds["stage_a"]),
        "stage_b": n_backbones * int(plan["stage_b"]["vectors_per_backbone"])
        * len(seeds["stage_b"]),
        "stage_c": int(plan["stage_c"]["max_tuples"]) * len(seeds["stage_c"]),
    }
    declared = {"stage_a": int(plan["stage_a"]["trajectory_budget"]),
                "stage_b": int(plan["stage_b"]["trajectory_budget"]),
                "stage_c": int(plan["stage_c"]["max_trajectory_budget"])}
    if budgets != declared or declared != {"stage_a": 300, "stage_b": 1280, "stage_c": 200}:
        raise ContractError(f"stage budgets {budgets} differ from declared {declared}")
    if int(plan["total_trajectory_budget_max"]) != sum(budgets.values()) or \
            sum(budgets.values()) != 1780:
        raise ContractError("total trajectory budget is not 1,780")
    if int(plan["stage_b"]["vectors_per_backbone"]) != 128 or len(seeds["stage_b"]) != 5 or \
            len(seeds["stage_a"]) != 5 or len(seeds["stage_c"]) != 20 or \
            int(plan["stage_c"]["max_tuples"]) != 10:
        raise ContractError("replicate counts or vector counts differ from the brief")
    flat = [s for values in seeds.values() for s in values]
    if len(flat) != len(set(flat)):
        raise ContractError("Stage-A, discovery, confirmation, smoke and LHS seeds overlap")
    timing = plan["time"]
    # The brief's 45/42-minute cap assumed ~8 s per trajectory; measured 16 s at 8 workers,
    # so the user raised it to 75/72 minutes on 2026-09-22 (recorded in the plan).
    if float(timing["hard_cap_seconds"]) != 4500 or float(timing["stop_dispatch_seconds"]) != 4320:
        raise ContractError("time caps differ from the declared 75 min hard / 72 min stop")
    if int(timing["workers_max"]) != 8:
        raise ContractError("workers_max must be 8")
    return plan


def plan_budget_table(plan: dict) -> list[dict]:
    per = float(plan["time"]["prior_seconds_per_trajectory"])
    workers = int(plan["time"]["workers_max"])
    rows = []
    for stage, n in (("stage_a", plan["stage_a"]["trajectory_budget"]),
                     ("stage_b", plan["stage_b"]["trajectory_budget"]),
                     ("stage_c", plan["stage_c"]["max_trajectory_budget"])):
        rows.append({"stage": stage, "trajectories": int(n),
                     "approx_minutes_at_8_workers": round(int(n) * per / workers / 60.0, 1)})
    total = sum(r["trajectories"] for r in rows)
    rows.append({"stage": "total", "trajectories": total,
                 "approx_minutes_at_8_workers": round(total * per / workers / 60.0, 1)})
    return rows


# ==========================================================================
# Seed preflight
# ==========================================================================

def seed_preflight(plan: dict, *, plan_path: Path = DEFAULT_PLAN,
                   outputs_root: Path | None = None, configs_root: Path | None = None) -> dict:
    """Recorded seed use under outputs/ manifests and configs/ plans, plus declared ranges.

    Manifests and plans carrying this design name are this experiment's own and
    are skipped so that a resumed validation does not collide with itself.
    """
    from run_joint_fit_comparison_20260917 import _seeds_from_obj  # noqa: PLC0415

    outputs_root = Path(outputs_root) if outputs_root is not None else ROOT / "outputs"
    configs_root = Path(configs_root) if configs_root is not None else ROOT / "configs"
    found: set[int] = set(rcc.DECLARED_FOREIGN_SEEDS)
    found.update(ccs.DISCOVERY_SEEDS, ccs.CONFIRMATION_SEEDS, ccs.SMOKE_SEEDS)
    found.add(ccs.PROPOSAL_RNG_SEED)
    scanned = {"manifests": 0, "configs": 0, "own_skipped": 0}
    for path in sorted(outputs_root.rglob("manifest.json")) if outputs_root.exists() else []:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("design") == DESIGN:
            scanned["own_skipped"] += 1
            continue
        scanned["manifests"] += 1
        _seeds_from_obj(payload, found)
    own_plan = Path(plan_path).resolve()
    for path in sorted(configs_root.glob("*.json")) if configs_root.exists() else []:
        if path.resolve() == own_plan:
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("design") == DESIGN:
            scanned["own_skipped"] += 1
            continue
        scanned["configs"] += 1
        _seeds_from_obj(payload, found)
    reserved = _seed_sets(plan)
    flat = [s for values in reserved.values() for s in values]
    if len(flat) != len(set(flat)):
        raise ContractError("reserved seed sets are not mutually disjoint")
    overlap = sorted(set(flat) & found)
    if overlap:
        raise ContractError(f"reserved seeds collide with recorded use: {overlap}")
    return {"reserved": reserved, "overlap": overlap, "n_recorded_seeds": len(found),
            **{f"n_{k}": v for k, v in scanned.items()},
            "limitation": "Only seeds recorded in outputs/**/manifest.json, configs/*.json "
                          "and the declared foreign ranges are visible to the scan."}


# ==========================================================================
# Effective routine path arithmetic (engine semantics, no engine)
# ==========================================================================

def _schedule(block: Mapping, default_before: float) -> TimeSchedule:
    return TimeSchedule(
        {str(k): float(v) for k, v in extract_value(block["value"]).items()},
        interpolation=extract_value(block.get("interpolation", {"value": "stepwise"})),
        default_before=default_before,
    )


def _profile_values(config: Mapping) -> tuple[float, float, float | None]:
    block = _get(config, PROFILE_PATH[:-1]).get(PROFILE_PATH[-1], {})
    profile = load_stage_structured_diagnosis({"disease": {"diagnosis": {
        "stage_structured_diagnosis": block}}})
    if not profile.enabled:
        return 1.0, 0.0, None
    return (profile.routine_hazard_scale, profile.aids_symptom_annual_probability,
            profile.symptom_start_year)


def effective_msm_routine_probability(config: Mapping, years: Sequence[float],
                                      rho: float | None = None) -> np.ndarray:
    """Annual MSM routine diagnosis probability exactly as the engine forms it.

    ``clip(p_base(t) * max(m(t), 0), 0, 1)`` then, for a routine scale rho,
    ``1 - (1 - p)^rho`` (the annual probability of ``rho * -log(1 - p)``).
    """
    diagnosis = _get(config, PROFILE_PATH[:-1])
    base = _schedule(diagnosis["schedule"], 0.0)
    msm = diagnosis["msm_relative_diagnosis"]
    if extract_value(msm.get("enabled", {"value": False})) is not True:
        raise ContractError("the MSM relative-diagnosis layer must be enabled")
    multiplier = _schedule(msm["schedule"], 1.0)
    scale = _profile_values(config)[0] if rho is None else float(rho)
    out = []
    for year in years:
        p = float(np.clip(float(base.get_value(float(year))) *
                          max(float(multiplier.get_value(float(year))), 0.0), 0.0, 1.0))
        if scale == 1.0:
            out.append(p)
        elif p >= 1.0:
            out.append(1.0 if scale > 0.0 else 0.0)
        else:
            out.append(float(-math.expm1(scale * math.log1p(-p))))
    return np.asarray(out, dtype=float)


def routine_path_key(config: Mapping, backbone: int, tolerance: float) -> str:
    path = effective_msm_routine_probability(config, PATH_GRID)
    digits = max(0, int(round(-math.log10(tolerance))))
    return ccs.sha256_json([int(backbone)] + [round(float(v), digits) for v in path])


def reporting_years(config: Mapping, plan: dict) -> list[int]:
    diagnosis = _get(config, PROFILE_PATH[:-1])
    years = {int(y) for y in extract_value(diagnosis["schedule"]["value"])}
    years |= {int(y) for y in extract_value(diagnosis["msm_relative_diagnosis"]["schedule"]["value"])}
    years |= {int(y) for y in plan["target_years"]}
    return sorted(years)


# ==========================================================================
# Configuration builders
# ==========================================================================

def stage_block(rho: float, symptom: float, start_year: int) -> dict:
    return {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": float(rho)},
        "aids_symptom_annual_probability": {"value": float(symptom)},
        "symptom_start_year": {"value": int(start_year)},
    }


def with_stage_block(config: dict, rho: float, symptom: float, start_year: int) -> dict:
    out = copy.deepcopy(config)
    _get(out, PROFILE_PATH[:-1])[PROFILE_PATH[-1]] = stage_block(rho, symptom, start_year)
    rcc.assert_stochastic_msm_config(out)
    return out


def without_stage_block(config: dict) -> dict:
    out = copy.deepcopy(config)
    _get(out, PROFILE_PATH[:-1]).pop(PROFILE_PATH[-1], None)
    return out


def stage_a_config(base: dict, rho: float, symptom: float, plan: dict) -> dict:
    return with_stage_block(base, rho, symptom, int(plan["symptom_start_year"]))


def stage_b_config(base: dict, vector: Mapping[str, float], plan: dict) -> dict:
    out = with_stage_block(base, float(plan["stage_b_routine_hazard_scale"]),
                           float(vector[SYMPTOM_NAME]), int(plan["symptom_start_year"]))
    schedule = _get(out, MSM_SCHEDULE_PATH)
    for parameter in plan["focused_parameters"]:
        if parameter["anchor_year"] is None:
            continue
        if parameter["anchor_year"] not in schedule:
            raise ContractError(f"multiplier anchor {parameter['anchor_year']} is absent")
        schedule[parameter["anchor_year"]] = float(vector[parameter["name"]])
    rcc.assert_stochastic_msm_config(out)
    return out


def backbone_vector(base: dict, plan: dict) -> dict[str, float]:
    schedule = _get(base, MSM_SCHEDULE_PATH)
    vector = {p["name"]: float(schedule[p["anchor_year"]])
              for p in plan["focused_parameters"] if p["anchor_year"] is not None}
    vector[SYMPTOM_NAME] = 0.0
    return vector


def _bounds(plan: dict) -> tuple[np.ndarray, np.ndarray]:
    lower = np.array([float(p["lower"]) for p in plan["focused_parameters"]])
    upper = np.array([float(p["upper"]) for p in plan["focused_parameters"]])
    return lower, upper


def normalise(vector: Mapping[str, float], plan: dict) -> np.ndarray:
    lower, upper = _bounds(plan)
    x = np.array([float(vector[n]) for n in FOCUSED_NAMES])
    return (x - lower) / (upper - lower)


def denormalise(u: Sequence[float], plan: dict) -> dict[str, float]:
    lower, upper = _bounds(plan)
    x = lower + np.asarray(u, dtype=float) * (upper - lower)
    return {name: float(value) for name, value in zip(FOCUSED_NAMES, x)}


def warm_proposal(base: dict, warm: Mapping, plan: dict) -> dict:
    """Represent a prior (rho, symptom) profile with rho=1 at the multiplier anchors.

    ``m'(a) = (1 - (1 - p_base(a) m(a))^rho) / p_base(a)`` at each anchor ``a``.
    Never clipped: an out-of-range anchor or symptom value rejects the proposal
    with its reason. The representation is exact at the anchors; the deviation
    elsewhere is recorded (linear interpolation of m' versus the rho-scaled path,
    and pre-1996 the neutral multiplier cannot carry rho < 1 at all).
    """
    rho, symptom = float(warm["rho"]), float(warm["p_symptom_aids"])
    diagnosis = _get(base, PROFILE_PATH[:-1])
    base_schedule = _schedule(diagnosis["schedule"], 0.0)
    schedule = _get(base, MSM_SCHEDULE_PATH)
    vector, reasons = {}, []
    for parameter in plan["focused_parameters"]:
        name, anchor = parameter["name"], parameter["anchor_year"]
        if anchor is None:
            vector[name] = symptom
            if not float(parameter["lower"]) <= symptom <= float(parameter["upper"]):
                reasons.append(f"{name}={symptom!r} outside [{parameter['lower']}, "
                               f"{parameter['upper']}]")
            continue
        m = float(schedule[anchor])
        if rho == 1.0:
            value = m
        else:
            p_base = float(base_schedule.get_value(float(anchor)))
            if p_base <= 0.0:
                reasons.append(f"{name}: base probability is zero at {anchor}")
                continue
            p = min(max(p_base * m, 0.0), 1.0)
            target = 1.0 if p >= 1.0 else float(-math.expm1(rho * math.log1p(-p)))
            value = target / p_base
        vector[name] = float(value)
        if not float(parameter["lower"]) <= value <= float(parameter["upper"]):
            reasons.append(f"{name}={value!r} outside [{parameter['lower']}, "
                           f"{parameter['upper']}]")
    record = {"id": warm["id"], "rho": rho, "p_symptom_aids": symptom,
              "vector": vector, "valid": not reasons, "rejection_reasons": reasons}
    if not reasons:
        candidate = stage_b_config(base, vector, plan)
        reference = effective_msm_routine_probability(base, PATH_GRID, rho=rho)
        represented = effective_msm_routine_probability(candidate, PATH_GRID)
        grid = np.asarray(PATH_GRID)
        post = grid >= 1996.0
        record["max_abs_path_deviation_1996_2025"] = float(
            np.max(np.abs(represented[post] - reference[post])))
        record["max_abs_path_deviation_before_1996"] = float(
            np.max(np.abs(represented[~post] - reference[~post])))
        record["anchor_path_deviation"] = {
            str(a): float(abs(effective_msm_routine_probability(candidate, [a])[0] -
                              effective_msm_routine_probability(base, [a], rho=rho)[0]))
            for a in (1996, 2004, 2019)}
    return record


def maximin_lhs(n: int, d: int, seed: int, n_candidates: int,
                exclude: np.ndarray | None = None) -> np.ndarray:
    """Reproducible maximin Latin hypercube in [0,1]^d.

    Among ``n_candidates`` random LHS designs, keep the one with the largest
    minimum Euclidean distance between its points and to the excluded (warm)
    points. Strict ``>`` keeps the earliest design on ties.
    """
    rng = np.random.default_rng(int(seed))
    exclude = np.zeros((0, d)) if exclude is None else np.atleast_2d(exclude)
    best, best_score = None, -1.0
    for _ in range(int(n_candidates)):
        design = np.empty((n, d))
        for j in range(d):
            design[:, j] = (rng.permutation(n) + rng.random(n)) / n
        points = np.vstack([design, exclude]) if len(exclude) else design
        diff = design[:, None, :] - points[None, :, :]
        dist = np.sqrt((diff ** 2).sum(axis=2))
        dist[np.arange(n), np.arange(n)] = np.inf
        score = float(dist.min())
        if score > best_score:
            best, best_score = design, score
    return best


def build_design(plan: dict, bases: Mapping[int, dict]) -> dict:
    """Every tuple of Stage A and Stage B, with its configuration. No engine."""
    tolerance = float(plan["duplicate_effective_path_tolerance"])
    stage_a, stage_b, warm_records = [], [], []
    for cid in [int(c) for c in plan["candidate_ids"]]:
        base = bases[cid]
        for rho, symptom in stage_a_grid(plan):
            config = stage_a_config(base, rho, symptom, plan)
            stage_a.append({
                "stage": "stage_a", "tuple_id": f"A_{cid}_rho{rho:.2f}_aids{symptom:.2f}",
                "backbone": cid, "rho": rho, "p_symptom_aids": symptom,
                "vector": None, "origin": "stage_a_grid", "config": config,
            })
        entries, seen = [], []

        def accept(vector: dict, origin: str, provenance: dict) -> None:
            u = normalise(vector, plan)
            if seen and np.any(np.max(np.abs(np.vstack(seen) - u), axis=1) <= ccs.DUPLICATE_TOLERANCE):
                raise ContractError(f"duplicate Stage-B vector from {origin}")
            seen.append(u)
            entries.append({"vector": vector, "u": u, "origin": origin, "provenance": provenance})

        identity = backbone_vector(base, plan)
        for warm in plan["stage_b"]["warm_proposals"]:
            record = warm_proposal(base, warm, plan)
            record["backbone"] = cid
            warm_records.append(record)
            if record["valid"]:
                if warm["id"] == "identity" and record["vector"] != identity:
                    raise ContractError("identity warm proposal differs from the backbone")
                accept(record["vector"], f"warm:{warm['id']}", {"warm_id": warm["id"]})
        if not entries or entries[0]["origin"] != "warm:identity":
            raise ContractError("the identity structural point must be the first Stage-B vector")
        n_fill = int(plan["stage_b"]["vectors_per_backbone"]) - len(entries)
        fill = maximin_lhs(n_fill, len(FOCUSED_NAMES), int(plan["stage_b"]["lhs_rng_seed"]),
                           int(plan["stage_b"]["lhs_maximin_candidates"]),
                           exclude=np.vstack(seen))
        for row, u in enumerate(fill):
            accept(denormalise(u, plan), "maximin_lhs", {"lhs_row": row})
        for index, entry in enumerate(entries):
            config = stage_b_config(base, entry["vector"], plan)
            stage_b.append({
                "stage": "stage_b", "tuple_id": f"B_{cid}_v{index:03d}", "backbone": cid,
                "rho": 1.0, "p_symptom_aids": float(entry["vector"][SYMPTOM_NAME]),
                "vector": entry["vector"], "u": [float(x) for x in entry["u"]],
                "origin": entry["origin"], "provenance": entry["provenance"],
                "is_identity": index == 0, "config": config,
            })
    for item in stage_a + stage_b:
        item["config_sha256"] = ccs.sha256_json(item["config"])
        item["routine_path_key"] = routine_path_key(item["config"], item["backbone"], tolerance)
    duplicates = {}
    for stage, items in (("stage_a", stage_a), ("stage_b", stage_b)):
        groups: dict[str, list[str]] = {}
        for item in items:
            groups.setdefault(item["routine_path_key"], []).append(item["tuple_id"])
        duplicates[stage] = [ids for ids in groups.values() if len(ids) > 1]
    return {"stage_a": stage_a, "stage_b": stage_b, "warm_records": warm_records,
            "duplicate_routine_paths": duplicates}


def check_design(plan: dict, bases: Mapping[int, dict], design: dict,
                 space_json: dict | None = None, base_config: dict | None = None,
                 raw_params: Mapping[int, dict] | None = None) -> dict:
    """Implementation checks 4-6, 11 and 12 on the concrete configurations."""
    checks: dict[str, object] = {}
    profile_prefix = ".".join(PROFILE_PATH)
    anchor_paths = [".".join(MSM_SCHEDULE_PATH + (p["anchor_year"],))
                    for p in plan["focused_parameters"] if p["anchor_year"] is not None]
    base_schedule_sha = {cid: ccs.sha256_json(_get(b, BASE_SCHEDULE_PATH))
                         for cid, b in bases.items()}
    for item in design["stage_a"] + design["stage_b"]:
        base = bases[item["backbone"]]
        config = item["config"]
        allowed = [profile_prefix] + (anchor_paths if item["stage"] == "stage_b" else [])
        outside = [p for p in changed_paths(base, config) if not _allowed(p, allowed)]
        if outside:
            raise ContractError(f"{item['tuple_id']} changed outside the focused paths: {outside}")
        if ccs.sha256_json(_get(config, BASE_SCHEDULE_PATH)) != base_schedule_sha[item["backbone"]]:
            raise ContractError(f"{item['tuple_id']} changed the base diagnosis schedule")
        rho, symptom, start = _profile_values(config)
        if start != float(plan["symptom_start_year"]):
            raise ContractError(f"{item['tuple_id']} lacks the 1985 symptom boundary")
        if item["stage"] == "stage_b":
            if rho != 1.0:
                raise ContractError(f"{item['tuple_id']} does not fix rho = 1")
            schedule = _get(config, MSM_SCHEDULE_PATH)
            for p in plan["focused_parameters"]:
                expected = item["vector"][p["name"]]
                actual = symptom if p["anchor_year"] is None else float(schedule[p["anchor_year"]])
                if actual != expected:
                    raise ContractError(f"{item['tuple_id']} {p['name']} is {actual}, expected {expected}")
                if not float(p["lower"]) <= actual <= float(p["upper"]):
                    raise ContractError(f"{item['tuple_id']} {p['name']} outside declared range")
        else:
            if (rho, symptom) != (item["rho"], item["p_symptom_aids"]):
                raise ContractError(f"{item['tuple_id']} profile values differ from the grid")
    checks["base_schedule_unchanged_byte_for_byte"] = True
    checks["only_focused_paths_differ"] = True
    checks["stage_b_rho_fixed_at_one"] = True
    checks["symptom_boundary_declared_everywhere"] = True
    for stage, n_expected in (("stage_a", 60), ("stage_b", 256)):
        ids = [i["tuple_id"] for i in design[stage]]
        shas = [(i["backbone"], i["config_sha256"]) for i in design[stage]]
        if len(ids) != n_expected or len(set(ids)) != len(ids) or len(set(shas)) != len(shas):
            raise ContractError(f"{stage}: tuple identifiers or configurations are not distinct")
    checks["tuple_ids_and_configs_distinct"] = True
    # Cross-check the surgical Stage-B edit against the production injection path.
    if space_json is not None and base_config is not None and raw_params is not None:
        mismatches = []
        for item in design["stage_b"]:
            params = dict(raw_params[item["backbone"]])
            for name in MULTIPLIER_NAMES:
                params[name] = float(item["vector"][name])
            injected = rcc.injected_config(base_config, params, space_json)
            injected = with_stage_block(injected, 1.0, item["p_symptom_aids"],
                                        int(plan["symptom_start_year"]))
            if ccs.sha256_json(injected) != item["config_sha256"]:
                mismatches.append(item["tuple_id"])
        if mismatches:
            raise ContractError(f"surgical Stage-B configs differ from production injection: "
                                f"{mismatches[:5]}")
        checks["stage_b_matches_production_injection"] = True
    checks["duplicate_routine_paths"] = design["duplicate_routine_paths"]
    return checks


# ==========================================================================
# Validation (no engine, no simulation, no writes)
# ==========================================================================

def _source(plan: dict) -> tuple[Path, dict, dict, dict, dict, dict]:
    source, search, confirmation, state = audit._source_context(plan)
    prior = ROOT / plan["prior_audit"]
    if ccs.sha256_file(prior / "manifest.json") != plan["prior_audit_manifest_sha256"] or \
            ccs.sha256_file(prior / "profile_summary.csv") != \
            plan["prior_audit_profile_summary_sha256"]:
        raise ContractError("prior audit artifacts differ from the frozen plan")
    bases, raw = audit._base_configs(plan, source, state)
    return source, search, confirmation, state, bases, raw


def validate(plan_path: Path = DEFAULT_PLAN, *, injection_crosscheck: bool = True) -> dict:
    with EngineGuard():
        plan = load_plan(plan_path)
        seed_check = seed_preflight(plan, plan_path=plan_path)
        source, search, confirmation, state, bases, raw = _source(plan)
        design = build_design(plan, bases)
        space_json = json.loads(state["paths"]["calib_space"].read_text())
        checks = check_design(
            plan, bases, design,
            space_json=space_json if injection_crosscheck else None,
            base_config=state["base_config"],
            raw_params={cid: raw[cid]["params"] for cid in bases},
        )
        again = build_design(plan, bases)
        if [i["config_sha256"] for i in again["stage_b"]] != \
                [i["config_sha256"] for i in design["stage_b"]]:
            raise ContractError("Stage-B design is not reproducible")
        checks["stage_b_design_reproducible"] = True
    current = rcc.module_hashes()
    original = search["context"]["module_sha256"]
    changed = sorted(n for n in set(current) | set(original) if current.get(n) != original.get(n))
    warm = [{k: v for k, v in r.items()} for r in design["warm_records"]]
    return {
        "ok": True, "design": DESIGN, "candidate_ids": plan["candidate_ids"],
        "stage_a_tuples": len(design["stage_a"]), "stage_b_tuples": len(design["stage_b"]),
        "budget": plan_budget_table(plan), "seed_preflight": seed_check,
        "warm_proposals": warm, "checks": checks,
        "stage_b_origins": pd.Series([i["origin"] for i in design["stage_b"]])
        .value_counts().sort_index().to_dict(),
        "intentional_code_differences_since_source_search": changed,
        "design_sha256": design_sha(design),
        "simulations_started": 0, "engines_constructed": 0, "artifacts_written": 0,
    }


def design_record(design: dict) -> dict:
    """The design without configurations (configs are rebuilt and hash-checked)."""
    strip = lambda item: {k: v for k, v in item.items() if k != "config"}  # noqa: E731
    return {"stage_a": [strip(i) for i in design["stage_a"]],
            "stage_b": [strip(i) for i in design["stage_b"]],
            "warm_records": design["warm_records"],
            "duplicate_routine_paths": design["duplicate_routine_paths"]}


def design_sha(design: dict) -> str:
    return ccs.sha256_json(ccs.json_safe(design_record(design)))


# ==========================================================================
# Output directory, provenance and state
# ==========================================================================

def runtime_hashes() -> dict[str, str]:
    hashes = rcc.module_hashes()
    for name in ("stage_structured_diagnosis.py", "run_stage_structured_diagnosis_audit.py",
                 "run_post2015_stock_flow_decomposition.py", Path(__file__).name):
        hashes[name] = ccs.sha256_file(ROOT / name)
    return hashes


def prepare(plan_path: Path, output_dir: Path, *, validation: dict | None = None,
            bases: Mapping[int, dict] | None = None) -> dict:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ContractError(f"output directory already exists; never overwritten: {output_dir}")
    validation = validation if validation is not None else validate(plan_path)
    plan = load_plan(plan_path)
    if bases is None:
        bases = _source(plan)[4]
    design = build_design(plan, bases)
    if design_sha(design) != validation["design_sha256"]:
        raise ContractError("design changed between validation and preparation")
    output_dir.mkdir(parents=True)
    for sub in ("journal/stage_a", "journal/stage_b", "journal/stage_c", "figures"):
        (output_dir / sub).mkdir(parents=True)
    shutil.copyfile(plan_path, output_dir / "frozen_plan.json")
    _dump(output_dir / "validation.json", validation)
    _dump(output_dir / "design.json", design_record(design))
    pd.DataFrame([{
        "tuple_id": i["tuple_id"], "backbone": i["backbone"], "origin": i["origin"],
        "is_identity": i["is_identity"], **i["vector"],
        **{f"u_{n}": u for n, u in zip(FOCUSED_NAMES, i["u"])},
        "config_sha256": i["config_sha256"], "routine_path_key": i["routine_path_key"],
    } for i in design["stage_b"]]).to_csv(output_dir / "stage_b_design.csv", index=False)
    if NOTE_SOURCE.is_file():
        shutil.copyfile(NOTE_SOURCE, output_dir / "IMPLEMENTATION_NOTE.md")
    manifest = {
        "design": DESIGN, "status": STATUS_PREPARED, "prepared_utc": _now(),
        "plan_sha256": ccs.sha256_file(plan_path),
        "design_sha256": ccs.sha256_file(output_dir / "design.json"),
        "runtime_module_sha256": runtime_hashes(),
        "candidate_ids": plan["candidate_ids"],
        "seeds": _seed_sets(plan),
        "budget": plan_budget_table(plan),
        "stages": {s: {"status": STATUS_NOT_STARTED, "completed": 0, "failed": 0}
                   for s in STAGES},
        "shortlist_sha256": None,
        "clock": {"active_seconds": 0.0, "first_dispatch_utc": None,
                  "stop_dispatch_seconds": plan["time"]["stop_dispatch_seconds"],
                  "hard_cap_seconds": plan["time"]["hard_cap_seconds"]},
        "environment": rcc.environment(),
    }
    _dump(output_dir / "manifest.json", manifest)
    return manifest


@dataclass
class Prepared:
    output_dir: Path
    plan_path: Path
    plan: dict
    manifest: dict
    design: dict
    bases: dict
    state: dict


def load_prepared(output_dir: Path, plan_path: Path, *, check_modules: bool = True,
                  context: tuple | None = None) -> Prepared:
    """Refuse any plan or provenance mismatch before touching the run."""
    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ContractError(f"{output_dir} is not a prepared run; run --prepare first")
    manifest = _read(manifest_path)
    if manifest.get("design") != DESIGN:
        raise ContractError("output directory belongs to a different design")
    frozen = output_dir / "frozen_plan.json"
    if not frozen.is_file() or ccs.sha256_file(frozen) != ccs.sha256_file(plan_path) or \
            manifest["plan_sha256"] != ccs.sha256_file(plan_path):
        raise ContractError("plan differs from the frozen plan of this run")
    if ccs.sha256_file(output_dir / "design.json") != manifest["design_sha256"]:
        raise ContractError("design.json differs from the prepared design")
    if check_modules:
        current = runtime_hashes()
        recorded = manifest["runtime_module_sha256"]
        drift = sorted(n for n in set(current) | set(recorded) if current.get(n) != recorded.get(n))
        if drift:
            raise ContractError(f"runtime code changed since preparation: {drift}")
    plan = load_plan(plan_path)
    if context is None:
        _, _, _, state, bases, _ = _source(plan)
    else:
        state, bases = context
    design = build_design(plan, bases)
    if ccs.sha256_json(ccs.json_safe(design_record(design))) != \
            ccs.sha256_json(_read(output_dir / "design.json")):
        raise ContractError("rebuilt design differs from the prepared design")
    return Prepared(output_dir, Path(plan_path), plan, manifest, design, bases, state)


def _save_manifest(prepared: Prepared) -> None:
    _dump(prepared.output_dir / "manifest.json", prepared.manifest)


# ==========================================================================
# One trajectory (constructs an engine; never called by validate/tests)
# ==========================================================================

def _pre_boundary_expected_zero(config: dict, start_year: float) -> bool:
    years = [y for y in PATH_GRID if y < start_year]
    return bool(np.all(effective_msm_routine_probability(config, years, rho=1.0) == 0.0)) and \
        float(_schedule(_get(config, PROFILE_PATH[:-1])["schedule"], 0.0)
              .get_value(max(years))) == 0.0


def check_trajectory_outputs(outputs: dict, plan: dict, config: dict) -> dict:
    """Per-trajectory identities: stage counters, AIDS proxy, 1985 boundary."""
    def value(series: str, year: int) -> float:
        values = outputs[series]
        return float(values[year] if year in values else values[str(year)])

    first, last = int(plan["annual_start_year"]), int(plan["annual_end_year"])
    for year in range(first, last + 1):
        for suffix in ("", "_msm", "_non_msm"):
            total = sum(value(f"hiv_diagnoses_in_{s}_stage{suffix}", year) for s in DX_STAGES)
            endogenous = value("new_diagnoses_endogenous" + suffix, year)
            if total != endogenous:
                raise ContractError(f"stage counters fail total identity in {year}{suffix}")
            proxy = value("aids_first_diagnosis_proxy" + suffix, year)
            components = sum(value(n + suffix, year) for n in OBSERVED_AIDS_PROXY_COMPONENTS)
            if proxy != components:
                raise ContractError(f"AIDS proxy components fail identity in {year}{suffix}")
    start = int(plan["symptom_start_year"])
    lo, hi = (int(y) for y in plan["boundary_check_years"])
    pre = {y: value("hiv_diagnoses_in_aids_stage", y) for y in range(lo, start)}
    post = {y: value("hiv_diagnoses_in_aids_stage", y) for y in range(start, hi + 1)}
    verifiable = _pre_boundary_expected_zero(config, start)
    if verifiable and any(v != 0.0 for v in pre.values()):
        raise ContractError(f"AIDS-stage diagnoses before {start}: {pre}")
    return {"pre_boundary_aids_stage_diagnoses": float(sum(pre.values())),
            "post_boundary_aids_stage_diagnoses_to_check_year": float(sum(post.values())),
            "pre_boundary_zero_verifiable": verifiable}


def run_trajectory(config: dict, meta: dict, plan: dict) -> dict:
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(copy.deepcopy(config), seed=int(meta["seed"]))
        engine.output_writer.enabled = False
        ledger, snapshots, mortality = audit._install_observers(
            engine, int(plan["ledger_first_year"]), int(plan["ledger_last_year"])
        )
        result = engine.run(run_number=1)
    snapshots[2026] = audit._stock_snapshot(engine.state.population.copy(), engine.groups, 2026)
    outputs = extract_annual_outputs(result, int(plan["annual_start_year"]),
                                     int(plan["annual_end_year"]))
    boundary = check_trajectory_outputs(outputs, plan, config)
    ledger_rows = audit._ledger_rows(ledger, engine, int(plan["ledger_first_year"]),
                                     int(plan["ledger_last_year"]))
    full_from = int(plan["full_output_start_year"])
    kept = {}
    for series, values in outputs.items():
        kept[series] = {int(y): float(v) for y, v in values.items()
                        if int(y) >= full_from or series in EARLY_SERIES}
    return {
        **meta, "annual_outputs": kept, "boundary": boundary,
        "snapshots": [row for year in sorted(snapshots) for row in snapshots[year]],
        "mortality": [{"year": y, "series": s, "value": v}
                      for (y, s), v in sorted(mortality.items())],
        "ledger": ledger_rows,
        "summary_sha256": ccs.sha256_json(result["summary"].to_dict(orient="list")),
        "final_state_sha256": ccs.sha256_json(result["final_state"].tolist()),
        "wall_seconds": time.time() - started, "error": "",
    }


def worker(payload: dict) -> dict:
    meta = {k: payload[k] for k in ("stage", "tuple_id", "backbone", "seed")}
    try:
        return run_trajectory(payload["config"], meta, payload["plan"])
    except Exception as exc:  # noqa: BLE001 - preserved as evidence, never substituted
        return {**meta, "error": f"{type(exc).__name__}: {exc}"}


# ==========================================================================
# Dispatch with the time stop
# ==========================================================================

class SerialExecutor:
    """In-process executor for tests; futures complete at submission."""

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers

    def submit(self, fn, *args):
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False):
        return None


def dispatch(tasks: list[dict], *, workers: int, worker_fn: Callable, executor_factory,
             clock: Callable[[], float], stop_after: float, hard_after: float,
             on_dispatch: Callable[[dict], None], on_result: Callable[[dict], None]) -> dict:
    """Bounded in-flight dispatch. No new work after ``stop_after`` seconds; at
    ``hard_after`` in-flight work is abandoned (recorded, never journaled)."""
    pending = list(tasks)
    inflight: dict[Future, dict] = {}
    start = clock()
    pool = executor_factory(max_workers=int(workers))
    hard_stopped = False
    lost: list[dict] = []
    try:
        while pending or inflight:
            elapsed = clock() - start
            while pending and len(inflight) < int(workers) and elapsed < stop_after:
                task = pending.pop(0)
                on_dispatch(task)
                inflight[pool.submit(worker_fn, task)] = task
            if not inflight:
                break
            remaining = hard_after - (clock() - start)
            if remaining <= 0:
                hard_stopped = True
                lost = list(inflight.values())
                break
            done, _ = wait(list(inflight), timeout=min(5.0, remaining),
                           return_when=FIRST_COMPLETED)
            for future in done:
                task = inflight.pop(future)
                try:
                    item = future.result()
                except Exception as exc:  # noqa: BLE001 - e.g. a broken worker process
                    item = {k: task[k] for k in ("stage", "tuple_id", "backbone", "seed")}
                    item["error"] = f"{type(exc).__name__}: {exc}"
                on_result(item)
    finally:
        if hard_stopped:
            # Abandon in-flight work: terminate worker processes, never wait on them.
            for process in list((getattr(pool, "_processes", None) or {}).values()):
                with contextlib.suppress(Exception):
                    process.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=True)
    return {"elapsed_seconds": clock() - start, "not_dispatched": len(pending),
            "lost_in_flight": [{k: t[k] for k in ("tuple_id", "seed")} for t in lost],
            "time_stopped": bool(pending) or hard_stopped, "hard_stopped": hard_stopped}


def _journal_path(output_dir: Path, stage: str, tuple_id: str, seed: int) -> Path:
    return output_dir / "journal" / stage / f"{tuple_id}__seed_{int(seed)}.json"


def load_journal(output_dir: Path, stage: str) -> list[dict]:
    directory = Path(output_dir) / "journal" / stage
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))] \
        if directory.exists() else []


def stage_tuples(prepared: Prepared, stage: str) -> list[dict]:
    if stage in ("stage_a", "stage_b"):
        return prepared.design[stage]
    shortlist_path = prepared.output_dir / "frozen_shortlist.json"
    if not shortlist_path.is_file():
        raise ContractError("Stage C needs a frozen shortlist")
    shortlist = _read(shortlist_path)
    by_id = {i["tuple_id"]: i for i in prepared.design["stage_b"]}
    tuples = []
    for entry in shortlist["entries"]:
        source = by_id[entry["tuple_id"]]
        if source["config_sha256"] != entry["config_sha256"]:
            raise ContractError(f"shortlisted {entry['tuple_id']} config differs from discovery")
        tuples.append({**source, "stage": "stage_c", "role": entry["role"]})
    if len(tuples) > int(prepared.plan["stage_c"]["max_tuples"]):
        raise ContractError("shortlist exceeds ten tuples")
    return tuples


def stage_seeds(plan: dict, stage: str) -> list[int]:
    return _seed_sets(plan)[stage]


def run_stage(prepared: Prepared, stage: str, *, workers: int, resume: bool,
              shortlist_sha256: str | None = None, worker_fn: Callable = worker,
              executor_factory=ProcessPoolExecutor,
              clock: Callable[[], float] = time.monotonic) -> dict:
    if stage not in STAGES:
        raise ContractError(f"unknown stage {stage}")
    if not 1 <= int(workers) <= int(prepared.plan["time"]["workers_max"]):
        raise ContractError("workers must be in [1, 8]")
    manifest = prepared.manifest
    status = manifest["stages"][stage]["status"]
    if status == STATUS_COMPLETE:
        raise ContractError(f"{stage} is already COMPLETE; refusing to overwrite a completed stage")
    if status == STATUS_FAILED:
        raise ContractError(f"{stage} FAILED; failures are preserved and never retried silently")
    if stage == "stage_b" and manifest["stages"]["stage_a"]["status"] != STATUS_COMPLETE:
        raise ContractError("Stage B requires a COMPLETE Stage A")
    if stage == "stage_c":
        if manifest["stages"]["stage_b"]["status"] != STATUS_COMPLETE:
            raise ContractError("Stage C requires a COMPLETE Stage B")
        path = prepared.output_dir / "frozen_shortlist.json"
        if not path.is_file() or manifest.get("shortlist_sha256") is None:
            raise ContractError("Stage C requires a frozen shortlist (--freeze-shortlist)")
        if ccs.sha256_file(path) != manifest["shortlist_sha256"]:
            raise ContractError("frozen_shortlist.json changed after freezing")
        if shortlist_sha256 != manifest["shortlist_sha256"]:
            raise ContractError("Stage C needs --shortlist-sha256 equal to the frozen shortlist "
                                "hash, confirming the shortlist was reviewed")
    done = load_journal(prepared.output_dir, stage)
    if done and not resume:
        raise ContractError(f"{stage} has {len(done)} journaled trajectories; use --resume")
    if any(item.get("error") for item in done):
        raise ContractError(f"{stage} contains failed trajectories; refusing to continue")
    seeds = stage_seeds(prepared.plan, stage)
    finished = {(i["tuple_id"], int(i["seed"])) for i in done}
    tuples = stage_tuples(prepared, stage)
    tasks = []
    for item in tuples:
        for seed in seeds:
            if (item["tuple_id"], seed) in finished:
                continue
            config = item["config"]
            if ccs.sha256_json(config) != item["config_sha256"]:
                raise ContractError(f"{item['tuple_id']} config hash drifted")
            tasks.append({"stage": stage, "tuple_id": item["tuple_id"],
                          "backbone": int(item["backbone"]), "seed": int(seed),
                          "config": config, "plan": prepared.plan})
    planned = len(tuples) * len(seeds)
    cap = {"stage_a": prepared.plan["stage_a"]["trajectory_budget"],
           "stage_b": prepared.plan["stage_b"]["trajectory_budget"],
           "stage_c": prepared.plan["stage_c"]["max_trajectory_budget"]}[stage]
    if planned > int(cap):
        raise ContractError(f"{stage} would schedule {planned} > cap {cap}")
    clock_state = manifest["clock"]
    stop_left = float(clock_state["stop_dispatch_seconds"]) - float(clock_state["active_seconds"])
    hard_left = float(clock_state["hard_cap_seconds"]) - float(clock_state["active_seconds"])
    if tasks and stop_left <= 0:
        manifest["stages"][stage]["status"] = STATUS_PARTIAL
        manifest["status"] = STATUS_PARTIAL
        _save_manifest(prepared)
        raise ContractError("the dispatch stop is already reached; no new work")
    if tasks and clock_state["first_dispatch_utc"] is None:
        clock_state["first_dispatch_utc"] = _now()
    manifest["stages"][stage].update({"status": STATUS_RUNNING, "planned": planned,
                                      "started_utc": manifest["stages"][stage].get(
                                          "started_utc") or _now()})
    manifest["status"] = f"{stage.upper()}_RUNNING"
    _save_manifest(prepared)
    dispatch_log = prepared.output_dir / "journal" / f"{stage}_dispatch.jsonl"
    counters = {"completed": 0, "failed": 0}

    def on_dispatch(task):
        rcc.append_journal(dispatch_log, {"event": "dispatch", "tuple_id": task["tuple_id"],
                                          "seed": task["seed"], "utc": _now()})

    def on_result(item):
        _dump(_journal_path(prepared.output_dir, stage, item["tuple_id"], item["seed"]), item)
        counters["failed" if item.get("error") else "completed"] += 1
        rcc.append_journal(dispatch_log, {"event": "result", "tuple_id": item["tuple_id"],
                                          "seed": item["seed"], "error": item.get("error", ""),
                                          "utc": _now()})

    outcome = {"elapsed_seconds": 0.0, "not_dispatched": 0, "lost_in_flight": [],
               "time_stopped": False, "hard_stopped": False}
    if tasks:
        outcome = dispatch(tasks, workers=workers, worker_fn=worker_fn,
                           executor_factory=executor_factory, clock=clock,
                           stop_after=stop_left, hard_after=hard_left,
                           on_dispatch=on_dispatch, on_result=on_result)
    clock_state["active_seconds"] = float(clock_state["active_seconds"]) + \
        float(outcome["elapsed_seconds"])
    journal = load_journal(prepared.output_dir, stage)
    failures = [i for i in journal if i.get("error")]
    entry = manifest["stages"][stage]
    entry.update({"completed": len(journal) - len(failures), "failed": len(failures),
                  "last_invocation": {**outcome, "workers": int(workers), "utc": _now()}})
    if failures:
        entry["status"] = STATUS_FAILED
        manifest["status"] = STATUS_FAILED
        pd.DataFrame(failures)[["tuple_id", "seed", "error"]].to_csv(
            prepared.output_dir / f"failures_{stage}.csv", index=False)
    elif len(journal) == planned:
        entry["status"] = STATUS_COMPLETE
        entry["completed_utc"] = _now()
        manifest["status"] = {"stage_a": "STAGE_A_COMPLETE", "stage_b": ccs.STATUS_SEARCH_COMPLETE,
                              "stage_c": ccs.STATUS_CONFIRMATION_COMPLETE}[stage]
    else:
        entry["status"] = STATUS_PARTIAL if outcome["time_stopped"] else STATUS_RUNNING
        manifest["status"] = STATUS_PARTIAL if outcome["time_stopped"] else \
            f"{stage.upper()}_INCOMPLETE"
    _save_manifest(prepared)
    return {"stage": stage, **entry, "active_seconds_total": clock_state["active_seconds"]}


# ==========================================================================
# Scoring and per-tuple summaries
# ==========================================================================

def _annual(item: dict, series: str, year: int) -> float:
    values = item["annual_outputs"][series]
    return float(values[year] if year in values else values[str(year)])


def _snapshot(item: dict, year: int, prefix: str | None = None, series: str | None = None,
              activity: str = "All") -> float:
    total = 0.0
    for row in item["snapshots"]:
        if int(row["snapshot_year"]) != year or row["activity"] != activity:
            continue
        if (series is not None and row["series"] == series) or \
                (prefix is not None and row["series"].startswith(prefix)):
            total += float(row["value"])
    return total


def _deaths(item: dict, years: Iterable[int]) -> float:
    wanted = set(int(y) for y in years)
    return float(sum(float(r["value"]) for r in item["mortality"] if int(r["year"]) in wanted))


def trajectory_metrics(item: dict, targets: Sequence[ccs.TargetSpec]) -> dict:
    by_key = {t.key: t.z for t in targets}
    dx = lambda y: _annual(item, "new_diagnoses_msm", y)  # noqa: E731
    proxy = lambda y: _annual(item, "aids_first_diagnosis_proxy_msm", y)  # noqa: E731
    stage_all = {s: sum(_annual(item, f"hiv_diagnoses_in_{s}_stage_msm", y)
                        for y in range(1996, 2026)) for s in DX_STAGES}
    stage_recent = {s: sum(_annual(item, f"hiv_diagnoses_in_{s}_stage_msm", y)
                           for y in (2022, 2023, 2024)) for s in DX_STAGES}
    n_all, n_recent = sum(stage_all.values()), sum(stage_recent.values())
    row = {
        "dx2024": dx(2024), "dx2019": dx(2019),
        "dx2024_abs_error": abs(dx(2024) - by_key["shm_new_diagnoses_msm@2024"]),
        "dx2019_abs_error": abs(dx(2019) - by_key["shm_new_diagnoses_msm@2019"]),
        "aids2019": proxy(2019), "aids2022": proxy(2022),
        "aids2019_abs_error": abs(proxy(2019) - by_key["rivm_aids_msm@2019"]),
        "aids2022_abs_error": abs(proxy(2022) - by_key["rivm_aids_msm@2022"]),
        "cum_msm_infections_2015_2024": sum(_annual(item, "new_infections_msm", y)
                                            for y in range(2015, 2025)),
        "cum_msm_diagnoses_2015_2024": sum(dx(y) for y in range(2015, 2025)),
        "cum_hiv_deaths_2015_2024": _deaths(item, range(2015, 2025)),
        "undiagnosed_msm_start2015": _snapshot(item, 2015, prefix="msm_undiagnosed_"),
        "undiagnosed_msm_start2025": _snapshot(item, 2025, prefix="msm_undiagnosed_"),
        "on_art_stock_start2025": _snapshot(item, 2025, series="on_art_stock_state"),
        "diagnosed_off_art_start2025": _snapshot(item, 2025, series="diagnosed_off_art_stock"),
        "fraction_msm_dx_aids_1996_2025": stage_all["aids"] / n_all if n_all else float("nan"),
        "fraction_msm_dx_aids_2022_2024": stage_recent["aids"] / n_recent if n_recent else float("nan"),
        "pre_boundary_aids_stage_dx": float(item["boundary"]["pre_boundary_aids_stage_diagnoses"]),
        "boundary_to_check_year_aids_stage_dx": float(
            item["boundary"]["post_boundary_aids_stage_diagnoses_to_check_year"]),
    }
    for year in (2019, 2022):
        for component in OBSERVED_AIDS_PROXY_COMPONENTS:
            row[f"{component}_msm_{year}"] = _annual(item, f"{component}_msm", year)
    for stage in DX_STAGES:
        row[f"fraction_msm_dx_{stage}_1996_2025"] = stage_all[stage] / n_all if n_all else float("nan")
    return row


def score_tuple(items: Sequence[dict], targets: Sequence[ccs.TargetSpec]) -> tuple[dict, list[dict]]:
    """Unchanged contract: 4-group search statistic and the target-by-target I_max rule."""
    matrix = np.asarray([[_annual(i, t.model_output, t.year) for t in targets] for i in items])
    means, variances = matrix.mean(axis=0), matrix.var(axis=0, ddof=1)
    primary = ccs.acceptance(means, variances, targets, replicates=len(items),
                             discrepancy=ccs.PRIMARY_DISCREPANCY)
    sensitivity = ccs.acceptance(means, variances, targets, replicates=len(items),
                                 discrepancy=ccs.SENSITIVITY_DISCREPANCY)
    residuals = ccs.search_residuals(means, targets)
    groups = ccs.group_rms(residuals, targets)
    j, mean_r = ccs.objective(groups)
    score = {"n_seeds": len(items), "search_j": j, "search_mean_r": mean_r,
             "primary_i_max": primary.i_max, "primary_binding_target": primary.binding_target,
             "primary_pass": primary.passed,
             "primary_failed_targets": ";".join(primary.failed_targets),
             "sensitivity_i_max": sensitivity.i_max,
             "sensitivity_binding_target": sensitivity.binding_target,
             "sensitivity_pass": sensitivity.passed,
             **{f"R_{g}": v for g, v in groups.items()}}
    rows = []
    for i, target in enumerate(targets):
        rows.append({"target": target.key, "group": target.group, "year": target.year,
                     "observed": target.z, "mean": float(means[i]),
                     "sd": float(math.sqrt(variances[i])),
                     "signed_residual": float(means[i] - target.z),
                     "search_residual": float(residuals[i]),
                     "primary_implausibility": primary.implausibility[i],
                     "sensitivity_implausibility": sensitivity.implausibility[i]})
    return score, rows


def summarise_stage(journal: Sequence[dict], tuples: Sequence[dict],
                    targets: Sequence[ccs.TargetSpec], seeds: Sequence[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per COMPLETE tuple (all declared seeds present), plus target residuals."""
    by_tuple: dict[str, list[dict]] = {}
    for item in journal:
        if not item.get("error"):
            by_tuple.setdefault(item["tuple_id"], []).append(item)
    rows, residual_rows = [], []
    for spec in tuples:
        items = sorted(by_tuple.get(spec["tuple_id"], []), key=lambda i: int(i["seed"]))
        if [int(i["seed"]) for i in items] != sorted(int(s) for s in seeds):
            continue
        score, residuals = score_tuple(items, targets)
        metrics = pd.DataFrame([trajectory_metrics(i, targets) for i in items])
        means = metrics.mean(numeric_only=True).to_dict()
        sds = metrics.std(numeric_only=True, ddof=1).to_dict()
        row = {"tuple_id": spec["tuple_id"], "backbone": spec["backbone"],
               "origin": spec.get("origin"), "role": spec.get("role"),
               "is_identity": bool(spec.get("is_identity", False)),
               "rho": spec["rho"], "p_symptom_aids": spec["p_symptom_aids"],
               "routine_path_key": spec["routine_path_key"],
               "config_sha256": spec["config_sha256"], **score,
               **{k: v for k, v in means.items()},
               **{f"sd__{k}": v for k, v in sds.items()}}
        if spec.get("vector") is not None:
            row.update({k: spec["vector"][k] for k in FOCUSED_NAMES})
            row.update({f"u_{n}": u for n, u in zip(FOCUSED_NAMES, spec["u"])})
        rows.append(row)
        for r in residuals:
            residual_rows.append({"tuple_id": spec["tuple_id"], "backbone": spec["backbone"], **r})
    return pd.DataFrame(rows), pd.DataFrame(residual_rows)


# ==========================================================================
# Stage A analysis
# ==========================================================================

def pareto_mask(values: np.ndarray) -> np.ndarray:
    """Non-dominated rows when every column is minimised."""
    values = np.asarray(values, dtype=float)
    mask = np.ones(len(values), dtype=bool)
    for i in range(len(values)):
        dominated = np.all(values <= values[i], axis=1) & np.any(values < values[i], axis=1)
        mask[i] = not bool(dominated.any())
    return mask


def smoothness(surface: pd.DataFrame, metric: str, multiple: float) -> dict:
    """Second differences along each rho and symptom grid line, in MC-SE units."""
    worst, flags, sign_changes = 0.0, 0, 0
    se_col = f"sd__{metric}"
    for axis, other in (("rho", "p_symptom_aids"), ("p_symptom_aids", "rho")):
        for _, line in surface.groupby(other):
            line = line.sort_values(axis)
            values = line[metric].to_numpy(float)
            if len(values) < 3:
                continue
            se = line[se_col].to_numpy(float) / np.sqrt(line["n_seeds"].to_numpy(float))
            second = values[:-2] - 2 * values[1:-1] + values[2:]
            second_se = np.sqrt(se[:-2] ** 2 + 4 * se[1:-1] ** 2 + se[2:] ** 2)
            ratio = np.abs(second) / np.where(second_se > 0, second_se, np.inf)
            worst = max(worst, float(np.nanmax(ratio)) if len(ratio) else 0.0)
            flags += int(np.sum(ratio > multiple))
            first = np.sign(np.diff(values))
            first = first[first != 0]
            sign_changes = max(sign_changes, int(np.sum(first[1:] != first[:-1])))
    return {"metric": metric, "max_second_difference_in_se": worst,
            "n_second_differences_beyond_threshold": flags,
            "max_first_difference_sign_changes_on_a_line": sign_changes,
            "smooth": flags == 0}


STAGE_A_SMOOTHNESS_METRICS = ("dx2024", "aids2019", "aids2022", "cum_msm_infections_2015_2024",
                              "cum_hiv_deaths_2015_2024")


def analyse_stage_a(surface: pd.DataFrame, plan: dict) -> tuple[pd.DataFrame, list[dict]]:
    if surface.empty:
        return surface, []
    out = []
    smooth_rows = []
    for cid, group in surface.groupby("backbone"):
        group = group.copy()
        group["combined_aids_2019_2022_abs_error"] = group.aids2019_abs_error + \
            group.aids2022_abs_error
        values = group[["dx2024_abs_error", "combined_aids_2019_2022_abs_error",
                        "cum_hiv_deaths_2015_2024"]].to_numpy(float)
        group["pareto_efficient_explanation_only"] = pareto_mask(values)
        out.append(group)
        if len(group) == len(stage_a_grid(plan)):
            for metric in STAGE_A_SMOOTHNESS_METRICS:
                smooth_rows.append({"backbone": int(cid), **smoothness(
                    group, metric, float(plan["decision_thresholds"]["stage_a_smoothness_se_multiple"]))})
    return pd.concat(out, ignore_index=True), smooth_rows


def prior_audit_comparison(surface: pd.DataFrame, plan: dict,
                           targets: Sequence[ccs.TargetSpec]) -> list[dict]:
    """Stage A (corrected boundary) versus the prior audit at rho=0.80.

    (0.80, 0.00) is unaffected by the boundary (seed-noise reference);
    (0.80, 0.35) is the mean of Stage-A (0.80, 0.30) and (0.80, 0.40).
    'Material' = the difference exceeds 10% of the observed target.
    """
    prior = pd.read_csv(ROOT / plan["prior_audit"] / "profile_summary.csv")
    z = {t.key: t.z for t in targets}
    thresholds = {"dx2024_abs_error": 0.1 * z["shm_new_diagnoses_msm@2024"],
                  "aids2019_abs_error": 0.1 * z["rivm_aids_msm@2019"],
                  "aids2022_abs_error": 0.1 * z["rivm_aids_msm@2022"]}
    columns = {"dx2024_abs_error": "diagnosis_2024_absolute_error",
               "aids2019_abs_error": "aids_proxy_2019_absolute_error",
               "aids2022_abs_error": "aids_proxy_2022_absolute_error"}
    rows = []
    for cid in plan["candidate_ids"]:
        s = surface[surface.backbone == int(cid)]
        for prior_id, points in (("rho_0p80_aids_0p00", [(0.80, 0.0)]),
                                 ("rho_0p80_aids_0p35", [(0.80, 0.3), (0.80, 0.4)])):
            chosen = s[[any(np.isclose(r.rho, a) and np.isclose(r.p_symptom_aids, b)
                            for a, b in points) for r in s.itertuples()]]
            p = prior[(prior.candidate_id == int(cid)) & (prior.profile_id == prior_id)]
            if len(chosen) != len(points) or p.empty:
                continue
            for metric, prior_col in columns.items():
                now = float(chosen[metric].mean())
                before = float(p.iloc[0][prior_col])
                rows.append({"backbone": int(cid), "prior_profile": prior_id,
                             "stage_a_points": ";".join(f"{a:.2f}/{b:.2f}" for a, b in points),
                             "metric": metric, "stage_a_value": now, "prior_audit_value": before,
                             "difference": now - before,
                             "material_threshold": thresholds[metric],
                             "material_change": abs(now - before) > thresholds[metric]})
    return rows


# ==========================================================================
# Discovery, shortlist
# ==========================================================================

def add_discovery_context(discovery: pd.DataFrame, plan: dict) -> pd.DataFrame:
    """Boundary occupancy and neighbourhood behaviour, per backbone."""
    if discovery.empty:
        return discovery
    th = plan["decision_thresholds"]
    out = []
    for cid, group in discovery.groupby("backbone"):
        group = group.copy()
        identity = group[group.is_identity]
        base_err = float(identity.iloc[0].dx2024_abs_error) if not identity.empty else float("nan")
        group["improves_2024_vs_discovery_identity"] = group.dx2024_abs_error < base_err
        group["same_qualitative_behaviour"] = group.primary_pass & \
            group.improves_2024_vs_discovery_identity
        u = group[[f"u_{n}" for n in FOCUSED_NAMES]].to_numpy(float)
        edge = float(th["boundary_occupancy_fraction"])
        at_bound = (u <= edge) | (u >= 1.0 - edge)
        for j, name in enumerate(FOCUSED_NAMES):
            group[f"at_bound_{name}"] = at_bound[:, j]
        group["boundary_point"] = at_bound.any(axis=1)
        distance = np.sqrt(((u[:, None, :] - u[None, :, :]) ** 2).sum(axis=2))
        same = group.same_qualitative_behaviour.to_numpy(bool)
        radius = float(th["neighbourhood_radius_normalized"])
        n_nb, share = [], []
        for i in range(len(group)):
            neighbours = np.flatnonzero((distance[i] <= radius) & (np.arange(len(group)) != i))
            n_nb.append(len(neighbours))
            share.append(float(same[neighbours].mean()) if len(neighbours) else 0.0)
        group["n_neighbours"] = n_nb
        group["neighbour_share_same_behaviour"] = share
        group["has_nearby_region"] = (group.n_neighbours >= int(th["neighbourhood_min_neighbours"])) & \
            (group.neighbour_share_same_behaviour >= float(th["neighbourhood_min_share_same_behaviour"]))
        out.append(group)
    return pd.concat(out, ignore_index=True)


def select_shortlist(discovery: pd.DataFrame, plan: dict) -> tuple[list[dict], list[dict]]:
    """Frozen, deterministic Stage-C shortlist (at most ten tuples).

    Slots: one backbone-identity comparator per backbone (paired fresh-seed
    reference), then mechanism tuples: best 2024 primary pass, best balanced
    (lowest J) primary pass, each backbone's best primary pass, lowest-J pass in
    each symptom bin, farthest-point diversity on the diagnosis/symptom
    trade-off, and only then near misses. Duplicate effective MSM routine paths
    are excluded among mechanism tuples.
    """
    max_tuples = int(plan["stage_c"]["max_tuples"])
    entries: list[dict] = []
    exclusions: list[dict] = []
    for cid in plan["candidate_ids"]:
        identity = discovery[(discovery.backbone == int(cid)) & discovery.is_identity]
        if identity.empty:
            raise ContractError(f"backbone {cid} identity is missing from discovery")
        row = identity.iloc[0]
        entries.append({"tuple_id": row.tuple_id, "backbone": int(cid),
                        "role": "backbone_comparator", "selected_as": "backbone_comparator"})
    mech = discovery[~discovery.is_identity].copy()
    order = ["search_j", "search_mean_r", "tuple_id"]
    passes = mech[mech.primary_pass].sort_values(order)
    used_paths: set[str] = set()
    chosen: set[str] = set()

    def add(row, reason: str, role: str = "mechanism") -> bool:
        if len(entries) >= max_tuples or row.tuple_id in chosen:
            return False
        if row.routine_path_key in used_paths:
            exclusions.append({"tuple_id": row.tuple_id, "reason": "duplicate effective MSM routine path",
                               "while_selecting": reason})
            return False
        entries.append({"tuple_id": row.tuple_id, "backbone": int(row.backbone), "role": role,
                        "selected_as": reason})
        chosen.add(row.tuple_id)
        used_paths.add(row.routine_path_key)
        return True

    def first(frame: pd.DataFrame, reason: str, role: str = "mechanism") -> None:
        for row in frame.itertuples(index=False):
            if add(row, reason, role):
                return

    first(passes.sort_values(["dx2024_abs_error", "search_j", "tuple_id"]),
          "best_2024_diagnosis_primary_pass")
    first(passes, "best_balanced_four_group_primary_pass")
    for cid in plan["candidate_ids"]:
        if not any(e["backbone"] == int(cid) and e["role"] == "mechanism" for e in entries):
            first(passes[passes.backbone == int(cid)], "best_primary_pass_for_backbone")
    bins = [float(x) for x in plan["stage_c"]["trade_off_symptom_bins"]]
    for low, high in zip(bins[:-1], bins[1:]):
        in_bin = passes[(passes.p_symptom_aids >= low) &
                        ((passes.p_symptom_aids < high) | (high == bins[-1]))]
        first(in_bin, f"lowest_J_pass_symptom_bin_[{low},{high}]")
    features = ["dx2024_abs_error", "R_aids_after_2015", "p_symptom_aids"]
    while len(entries) < max_tuples:
        remaining = passes[~passes.tuple_id.isin(chosen) & ~passes.routine_path_key.isin(used_paths)]
        if remaining.empty:
            break
        pool = passes[passes.tuple_id.isin(chosen)]
        scale = passes[features].std(ddof=0).replace(0.0, 1.0)
        if pool.empty:
            best = remaining.iloc[0]
        else:
            a = (remaining[features] / scale).to_numpy(float)
            b = (pool[features] / scale).to_numpy(float)
            distance = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            best = remaining.iloc[int(np.argmax(distance))]
        add(best, "farthest_point_trade_off_diversity")
    near = mech[~mech.primary_pass].sort_values(["primary_i_max", "search_j", "tuple_id"])
    for row in near.itertuples(index=False):
        if len(entries) >= max_tuples:
            break
        add(row, "near_miss_lowest_I_max", role="mechanism_near_miss")
    ids = [e["tuple_id"] for e in entries]
    if len(ids) != len(set(ids)) or len(ids) > max_tuples:
        raise ContractError("shortlist is not a set of at most ten distinct tuples")
    lookup = discovery.set_index("tuple_id")
    for entry in entries:
        row = lookup.loc[entry["tuple_id"]]
        entry.update({"config_sha256": row.config_sha256, "routine_path_key": row.routine_path_key,
                      "discovery_primary_pass": bool(row.primary_pass),
                      "discovery_primary_i_max": float(row.primary_i_max),
                      "discovery_search_j": float(row.search_j),
                      "discovery_dx2024_abs_error": float(row.dx2024_abs_error),
                      **{k: float(row[k]) for k in FOCUSED_NAMES}})
    return entries, exclusions


def freeze_shortlist(prepared: Prepared) -> dict:
    manifest = prepared.manifest
    if manifest["stages"]["stage_b"]["status"] != STATUS_COMPLETE:
        raise ContractError("the shortlist is frozen only from a COMPLETE Stage B; a partial "
                            "discovery archive is never treated as the frozen design")
    path = prepared.output_dir / "frozen_shortlist.json"
    if path.exists():
        raise ContractError("shortlist is already frozen; never overwritten")
    if load_journal(prepared.output_dir, "stage_c"):
        raise ContractError("confirmation trajectories exist before the shortlist")
    targets = prepared.state["targets"]
    discovery, residuals = summarise_stage(load_journal(prepared.output_dir, "stage_b"),
                                           prepared.design["stage_b"], targets,
                                           stage_seeds(prepared.plan, "stage_b"))
    if len(discovery) != len(prepared.design["stage_b"]):
        raise ContractError("discovery is incomplete")
    discovery = add_discovery_context(discovery, prepared.plan)
    discovery.to_csv(prepared.output_dir / "discovery_candidates.csv", index=False)
    residuals.to_csv(prepared.output_dir / "discovery_target_residuals.csv", index=False)
    entries, exclusions = select_shortlist(discovery, prepared.plan)
    payload = {
        "design": DESIGN, "frozen_utc": _now(), "entries": entries,
        "exclusions": exclusions,
        "confirmation_seeds": stage_seeds(prepared.plan, "stage_c"),
        "trajectories": len(entries) * len(stage_seeds(prepared.plan, "stage_c")),
        "discovery_candidates_sha256": ccs.sha256_file(prepared.output_dir / "discovery_candidates.csv"),
        "selection_rule": select_shortlist.__doc__.strip(),
    }
    _dump(path, payload)
    manifest["shortlist_sha256"] = ccs.sha256_file(path)
    manifest["status"] = "SHORTLIST_FROZEN_AWAITING_REVIEW"
    _save_manifest(prepared)
    return {"shortlist": str(path), "shortlist_sha256": manifest["shortlist_sha256"],
            "n_entries": len(entries), "trajectories": payload["trajectories"]}


# ==========================================================================
# Confirmation evidence and the decision (pure functions, unit-tested)
# ==========================================================================

CLAUSES = (
    ("c1_primary_pass_fresh_seeds", "passes the 13-target primary contract on fresh seeds"),
    ("c2_material_2024_improvement", "materially improves 2024 MSM diagnosis error relative to its backbone"),
    ("c3_post2015_aids_not_both_materially_worse", "does not materially worsen both post-2015 AIDS targets"),
    ("c4_earlier_diagnosis_acceptable", "retains acceptable earlier diagnosis trajectory"),
    ("c5_no_mortality_or_latent_pathology", "has no unexplained mortality or latent-state pathology"),
    ("c6_not_boundary_point", "is not an isolated boundary point"),
    ("c7_nearby_region", "has a nearby region with the same qualitative behaviour"),
)


def paired_seed_improvements(tuple_items: Sequence[dict], comparator_items: Sequence[dict],
                             observed: float) -> int:
    base = {int(i["seed"]): abs(_annual(i, "new_diagnoses_msm", 2024) - observed)
            for i in comparator_items}
    return int(sum(abs(_annual(i, "new_diagnoses_msm", 2024) - observed) < base[int(i["seed"])]
                   for i in tuple_items if int(i["seed"]) in base))


def evaluate_clauses(row: Mapping, comparator: Mapping, discovery_row: Mapping | None,
                     targets_z: Mapping[str, float], plan: dict,
                     earlier_targets_within_cutoff: bool) -> dict:
    """Clause flags for one confirmed mechanism tuple against its paired comparator."""
    th = plan["decision_thresholds"]
    relative = lambda a, b: (a - b) / b if b else float("inf")  # noqa: E731
    death_change = relative(row["cum_hiv_deaths_2015_2024"], comparator["cum_hiv_deaths_2015_2024"])
    infection_change = relative(row["cum_msm_infections_2015_2024"],
                                comparator["cum_msm_infections_2015_2024"])
    undiagnosed_change = relative(row["undiagnosed_msm_start2025"],
                                  comparator["undiagnosed_msm_start2025"])
    art_change = relative(row["on_art_stock_start2025"], comparator["on_art_stock_start2025"])
    material = float(th["material_epidemic_change_fraction"])
    reduction = 1.0 - row["dx2024_abs_error"] / comparator["dx2024_abs_error"] \
        if comparator["dx2024_abs_error"] > 0 else 0.0
    worse2019 = row["aids2019_abs_error"] - comparator["aids2019_abs_error"] > \
        float(th["material_aids_worsening_fraction_of_target"]) * targets_z["rivm_aids_msm@2019"]
    worse2022 = row["aids2022_abs_error"] - comparator["aids2022_abs_error"] > \
        float(th["material_aids_worsening_fraction_of_target"]) * targets_z["rivm_aids_msm@2022"]
    flags = {
        "material_hiv_death_increase": death_change > material,
        "material_msm_infection_increase": infection_change > material,
        "material_undiagnosed_2025_change": abs(undiagnosed_change) > material,
        "material_art_stock_2025_change": abs(art_change) > material,
        "negative_stock": min(row["undiagnosed_msm_start2025"], row["on_art_stock_start2025"],
                              row["diagnosed_off_art_start2025"]) < 0,
    }
    clauses = {
        "c1_primary_pass_fresh_seeds": bool(row["primary_pass"]),
        "c2_material_2024_improvement": bool(
            reduction >= float(th["material_2024_error_reduction_fraction"]) and
            int(row["paired_seeds_improved_2024"]) >= int(th["min_improved_paired_seeds_2024"])),
        "c3_post2015_aids_not_both_materially_worse": not (worse2019 and worse2022),
        "c4_earlier_diagnosis_acceptable": bool(
            row["R_diagnoses_1996_2015"] <= float(th["earlier_diagnosis_group_rms_tolerance"]) *
            comparator["R_diagnoses_1996_2015"] and earlier_targets_within_cutoff),
        "c5_no_mortality_or_latent_pathology": not any(flags.values()),
        "c6_not_boundary_point": bool(discovery_row is not None and
                                      not bool(discovery_row["boundary_point"])),
        "c7_nearby_region": bool(discovery_row is not None and
                                 bool(discovery_row["has_nearby_region"])),
    }
    return {**clauses, **flags, "dx2024_error_reduction_fraction": reduction,
            "hiv_death_change_fraction": death_change, "msm_infection_change_fraction": infection_change,
            "undiagnosed_2025_change_fraction": undiagnosed_change, "art_stock_2025_change_fraction": art_change,
            "aids2019_materially_worse": worse2019, "aids2022_materially_worse": worse2022,
            "primary_i_max_improved_vs_comparator": row["primary_i_max"] < comparator["primary_i_max"]}


FOLLOW_UPS = {
    "incomplete": "Complete the frozen design (resume the remaining stage within a newly declared "
                  "time budget) before any classification beyond this one.",
    "c1_primary_pass_fresh_seeds": "Confirm the leading discovery region on a second independent "
                                   "20-seed matrix before any recalibration.",
    "c2_material_2024_improvement": "Replicate the best 2024-diagnosis tuple against its paired "
                                    "backbone on a larger fresh-seed matrix.",
    "c3_post2015_aids_not_both_materially_worse": "Run a bounded sensitivity of the AIDS observation "
                                                  "mapping (late-presentation constructs) around the "
                                                  "leading tuple.",
    "c4_earlier_diagnosis_acceptable": "Refine only the 1996/2004 MSM anchors around the leading "
                                       "tuple with the symptom probability held fixed.",
    "c5_no_mortality_or_latent_pathology": "A documented decision review of the leading tuple's "
                                           "mortality and 2025 latent-state changes against "
                                           "available empirical comparators.",
    "c6_not_boundary_point": "A local 4-D refinement inside the frozen ranges around the leading "
                             "tuple to test whether the useful region leaves the boundary.",
    "c7_nearby_region": "A local 4-D refinement inside the frozen ranges around the leading tuple "
                        "to test whether its behaviour is shared by neighbours.",
}


def classify_decision(evaluations: Sequence[Mapping], *, design_complete: bool,
                      discovery_apparent_improvement: bool) -> dict:
    """Exactly one of the three labels; each is reachable (see unit fixtures).

    supported      -- some confirmed mechanism tuple satisfies all seven clauses;
    not supported  -- only for a COMPLETE design with no stable (fresh-seed)
                      improvement in 2024 diagnosis fit or primary I_max, or when
                      every apparent improvement fails on fresh seeds AND shows
                      material epidemic changes;
    informative    -- everything else, with the single smallest follow-up.
    """
    clause_keys = [k for k, _ in CLAUSES]
    for evaluation in evaluations:
        if all(bool(evaluation[k]) for k in clause_keys):
            return {"label": LABEL_SUPPORTED, "tuple_id": evaluation["tuple_id"],
                    "reason": "all seven clauses hold for this confirmed tuple",
                    "follow_up": None}
    stable = [e for e in evaluations if e["c2_material_2024_improvement"] or
              (e["c1_primary_pass_fresh_seeds"] and e["primary_i_max_improved_vs_comparator"])]
    all_fail_with_pathology = bool(evaluations) and all(
        (not e["c1_primary_pass_fresh_seeds"]) and
        (e["material_hiv_death_increase"] or e["material_msm_infection_increase"])
        for e in evaluations)
    if design_complete and (not stable or (discovery_apparent_improvement and all_fail_with_pathology)):
        return {"label": LABEL_NOT_SUPPORTED, "tuple_id": None,
                "reason": ("no confirmed tuple shows a stable fresh-seed improvement in 2024 "
                           "diagnosis fit or primary I_max" if not stable else
                           "every apparent improvement failed on fresh seeds with material "
                           "epidemic changes"),
                "follow_up": None}
    if not design_complete and not evaluations:
        return {"label": LABEL_INFORMATIVE, "tuple_id": None,
                "reason": "the frozen design is incomplete, so neither 'supported' nor "
                          "'not supported' can be established",
                "follow_up": FOLLOW_UPS["incomplete"]}
    ranked = sorted(evaluations, key=lambda e: (-sum(bool(e[k]) for k in clause_keys),
                                                e.get("primary_i_max", float("inf")),
                                                e["tuple_id"]))
    leader = ranked[0]
    failing = [k for k in clause_keys if not bool(leader[k])]
    follow = FOLLOW_UPS[failing[0]] if failing else FOLLOW_UPS["incomplete"]
    if not design_complete:
        follow = FOLLOW_UPS["incomplete"]
    return {"label": LABEL_INFORMATIVE, "tuple_id": leader["tuple_id"],
            "reason": "stable improvement exists but the leading tuple fails: " +
                      ", ".join(failing)
            if stable else "design incomplete; no stable improvement established yet",
            "failing_clauses": failing, "follow_up": follow}


def confirmation_evidence(prepared: Prepared, confirmation: pd.DataFrame,
                          discovery: pd.DataFrame, journal: Sequence[dict],
                          residuals: pd.DataFrame) -> pd.DataFrame:
    if confirmation.empty:
        return confirmation
    targets = prepared.state["targets"]
    z = {t.key: t.z for t in targets}
    by_tuple: dict[str, list[dict]] = {}
    for item in journal:
        if not item.get("error"):
            by_tuple.setdefault(item["tuple_id"], []).append(item)
    comparators = confirmation[confirmation.role == "backbone_comparator"].set_index("backbone")
    disc = discovery.set_index("tuple_id") if not discovery.empty else None
    earlier = [f"shm_new_diagnoses_msm@{y}" for y in (1996, 2000, 2004, 2010, 2015)]
    rows = []
    for row in confirmation.to_dict(orient="records"):
        if row["role"] == "backbone_comparator" or row["backbone"] not in comparators.index:
            rows.append({**row})
            continue
        comparator = comparators.loc[row["backbone"]]
        cid = comparator["tuple_id"]
        row["paired_seeds_improved_2024"] = paired_seed_improvements(
            by_tuple[row["tuple_id"]], by_tuple[cid], z["shm_new_diagnoses_msm@2024"])
        r = residuals[(residuals.tuple_id == row["tuple_id"]) & residuals.target.isin(earlier)]
        within = bool((r.primary_implausibility <= float(prepared.plan["implausibility_cutoff"])).all())
        drow = disc.loc[row["tuple_id"]] if disc is not None and row["tuple_id"] in disc.index else None
        evaluation = evaluate_clauses(row, comparator, drow, z, prepared.plan, within)
        paired = paired_differences(by_tuple[row["tuple_id"]], by_tuple[cid], targets)
        rows.append({**row, **evaluation, **paired, "comparator_tuple_id": cid})
    frame = pd.DataFrame(rows)
    # Comparator rows carry no clause values; keep boolean semantics for the rest.
    flag_columns = [k for k, _ in CLAUSES] + [
        "material_hiv_death_increase", "material_msm_infection_increase",
        "material_undiagnosed_2025_change", "material_art_stock_2025_change", "negative_stock",
        "aids2019_materially_worse", "aids2022_materially_worse",
        "primary_i_max_improved_vs_comparator"]
    for column in flag_columns:
        if column in frame:
            frame[column] = frame[column].astype("boolean")
    return frame


def paired_differences(items: Sequence[dict], base: Sequence[dict],
                       targets: Sequence[ccs.TargetSpec]) -> dict:
    """Mean paired (same-seed) change and a t-based 95% interval."""
    from scipy import stats  # noqa: PLC0415

    b = {int(i["seed"]): trajectory_metrics(i, targets) for i in base}
    out = {}
    for metric in ("dx2024", "aids2019", "aids2022", "cum_msm_infections_2015_2024",
                   "cum_hiv_deaths_2015_2024", "undiagnosed_msm_start2025", "on_art_stock_start2025"):
        diffs = np.array([trajectory_metrics(i, targets)[metric] - b[int(i["seed"])][metric]
                          for i in items if int(i["seed"]) in b])
        if len(diffs) < 2:
            continue
        half = float(stats.t.ppf(0.975, len(diffs) - 1) * diffs.std(ddof=1) / np.sqrt(len(diffs)))
        out[f"paired_change__{metric}"] = float(diffs.mean())
        out[f"paired_ci95_low__{metric}"] = float(diffs.mean()) - half
        out[f"paired_ci95_high__{metric}"] = float(diffs.mean()) + half
    return out


# ==========================================================================
# Identifiability (exploratory; not posterior)
# ==========================================================================

def effective_path_table(tuples: Sequence[dict], plan: dict) -> pd.DataFrame:
    rows = []
    for item in tuples:
        years = reporting_years(item["config"], plan)
        values = effective_msm_routine_probability(item["config"], years)
        for year, value in zip(years, values):
            rows.append({"stage": item["stage"], "tuple_id": item["tuple_id"],
                         "backbone": item["backbone"], "year": year,
                         "effective_msm_routine_annual_probability": float(value),
                         "p_symptom_aids": item["p_symptom_aids"],
                         "symptom_active": year >= int(plan["symptom_start_year"])})
    return pd.DataFrame(rows)


def local_sensitivity(frame: pd.DataFrame, x_columns: Sequence[str], y: np.ndarray,
                      centre: int, k: int, seed: int = 0) -> dict:
    """Standardised local linear sensitivity of the 13 target means (in sqrt(B) units)."""
    x = frame[list(x_columns)].to_numpy(float)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    xs = (x - x.mean(axis=0)) / scale
    distance = np.sqrt(((xs - xs[centre]) ** 2).sum(axis=1))
    nearest = np.argsort(distance, kind="stable")[:min(k, len(frame))]

    def fit(index):
        design = np.column_stack([np.ones(len(index)), xs[index] - xs[centre]])
        coef, *_ = np.linalg.lstsq(design, y[index], rcond=None)
        fitted = design @ coef
        ss_res = ((y[index] - fitted) ** 2).sum(axis=0)
        ss_tot = ((y[index] - y[index].mean(axis=0)) ** 2).sum(axis=0)
        r2 = 1.0 - ss_res / np.where(ss_tot > 0, ss_tot, np.inf)
        return coef[1:].T, r2

    if len(nearest) <= len(x_columns) + 2:
        return {"stable": False, "reason": "too few neighbours"}
    jac, r2 = fit(nearest)
    s = np.linalg.svd(jac, compute_uv=False)
    rng = np.random.default_rng(seed)
    conds = []
    for _ in range(200):
        sample = rng.choice(nearest, size=len(nearest), replace=True)
        if len(np.unique(sample)) <= len(x_columns) + 1:
            continue
        sb = np.linalg.svd(fit(sample)[0], compute_uv=False)
        conds.append(sb[0] / sb[-1] if sb[-1] > 0 else np.inf)
    conds = np.asarray(conds, dtype=float)
    finite = conds[np.isfinite(conds)]
    cv = float(finite.std() / finite.mean()) if len(finite) > 10 and finite.mean() > 0 else float("inf")
    median_r2 = float(np.median(r2))
    rank_1pct = int(np.sum(s > s[0] * 1e-2)) if s[0] > 0 else 0
    full_rank = rank_1pct == len(x_columns)
    # A condition number is only interpretable for a full-rank matrix.
    stable = bool(full_rank and median_r2 >= 0.5 and cv < 0.5)
    symptom_r2 = None
    if SYMPTOM_NAME in x_columns or "symptom" in x_columns[-1]:
        others, last = jac[:, :-1], jac[:, -1]
        coef, *_ = np.linalg.lstsq(others, last, rcond=None)
        resid = last - others @ coef
        symptom_r2 = float(1.0 - (resid ** 2).sum() / max((last ** 2).sum(), 1e-300))
    return {"n_local": int(len(nearest)), "singular_values": [float(v) for v in s],
            "numerical_rank": int(np.sum(s > s[0] * 1e-8)) if s[0] > 0 else 0,
            "rank_at_1pct": rank_1pct, "n_coordinates": len(x_columns), "full_rank": full_rank,
            "condition_number": float(s[0] / s[-1]) if full_rank and s[-1] > 0 else float("inf"),
            "median_target_r2": median_r2, "bootstrap_condition_cv": cv,
            "stable": stable, "symptom_column_explained_by_routine_r2": symptom_r2}


def identifiability(discovery: pd.DataFrame, residuals: pd.DataFrame, design_b: Sequence[dict],
                    plan: dict) -> dict:
    from scipy import stats  # noqa: PLC0415

    if discovery.empty:
        return {"available": False}
    configs = {i["tuple_id"]: i["config"] for i in design_b}
    out = {"available": True, "label": "exploratory; not posterior correlations", "backbones": {}}
    for cid, group in discovery.groupby("backbone"):
        group = group.reset_index(drop=True)
        leading = group[group.primary_pass & ~group.is_identity]
        basis = "discovery primary passes"
        if len(leading) < 5:
            leading = group[~group.is_identity].nsmallest(20, "search_j")
            basis = "20 lowest-J discovery vectors (fewer than five primary passes)"
        years = [int(y) for y in plan["target_years"]]
        paths = {tid: effective_msm_routine_probability(configs[tid], years).tolist()
                 for tid in leading.tuple_id}
        u = leading[[f"u_{n}" for n in FOCUSED_NAMES]].to_numpy(float)
        edge = float(plan["decision_thresholds"]["boundary_occupancy_fraction"])
        occupancy = {n: float(((u[:, j] <= edge) | (u[:, j] >= 1 - edge)).mean())
                     for j, n in enumerate(FOCUSED_NAMES)}
        correlations = {}
        if len(leading) >= 5:
            for a in range(len(FOCUSED_NAMES)):
                for b in range(a + 1, len(FOCUSED_NAMES)):
                    rho, p = stats.spearmanr(u[:, a], u[:, b])
                    correlations[f"{FOCUSED_NAMES[a]}~{FOCUSED_NAMES[b]}"] = {
                        "spearman": float(rho), "p_value": float(p)}
        wide = residuals.pivot(index="tuple_id", columns="target", values="search_residual")
        targets_order = list(ccs.FROZEN_TARGET_ORDER)
        lead_y = wide.loc[leading.tuple_id, targets_order].to_numpy(float)
        indistinct = []
        for i in range(len(leading)):
            for j in range(i + 1, len(leading)):
                if np.linalg.norm(u[i] - u[j]) >= 0.5 and np.max(np.abs(lead_y[i] - lead_y[j])) <= 1.0:
                    indistinct.append((leading.tuple_id.iloc[i], leading.tuple_id.iloc[j]))
        y_all = wide.loc[group.tuple_id, targets_order].to_numpy(float)
        centre = int(group.index[group.tuple_id == leading.sort_values("search_j").tuple_id.iloc[0]][0])
        eff = pd.DataFrame([effective_msm_routine_probability(configs[t], [1996, 2004, 2019]).tolist()
                            for t in group.tuple_id], columns=["eff_1996", "eff_2004", "eff_2019"])
        eff[SYMPTOM_NAME] = group[SYMPTOM_NAME].to_numpy(float)
        k = min(40, len(group))
        raw_sens = local_sensitivity(group, list(FOCUSED_NAMES), y_all, centre, k)
        eff_sens = local_sensitivity(eff, ["eff_1996", "eff_2004", "eff_2019", SYMPTOM_NAME],
                                     y_all, centre, k)
        symptom_vs_aids_fraction = stats.spearmanr(group[SYMPTOM_NAME],
                                                   group["fraction_msm_dx_aids_1996_2025"])
        out["backbones"][str(int(cid))] = {
            "leading_basis": basis, "n_leading": int(len(leading)),
            "effective_paths_at_target_years": {"years": years, "paths": paths},
            "boundary_occupancy": occupancy, "pairwise_rank_correlations": correlations,
            "n_materially_different_but_indistinguishable_pairs": len(indistinct),
            "indistinguishable_rule": "normalized parameter distance >= 0.5 and every target mean "
                                      "within 1 sqrt(B_i)",
            "indistinguishable_examples": indistinct[:10],
            "local_sensitivity_raw_coordinates": raw_sens,
            "local_sensitivity_effective_path_coordinates": eff_sens,
            "symptom_vs_aids_stage_fraction_spearman": float(symptom_vs_aids_fraction[0]),
        }
    return out


# ==========================================================================
# Report
# ==========================================================================

def _fmt(value, digits: int = 1) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}"


def write_figures(output_dir: Path, surface: pd.DataFrame, discovery: pd.DataFrame,
                  evidence: pd.DataFrame, paths: pd.DataFrame, shortlist_ids: Sequence[str]) -> list[str]:
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    written = []
    figures = Path(output_dir) / "figures"
    figures.mkdir(exist_ok=True)
    if not surface.empty:
        backbones = sorted(surface.backbone.unique())
        fig, axes = plt.subplots(2, len(backbones), figsize=(5.5 * len(backbones), 8), squeeze=False)
        for c, cid in enumerate(backbones):
            s = surface[surface.backbone == cid]
            for r, (metric, title) in enumerate((("dx2024_abs_error", "2024 MSM diagnosis |error|"),
                                                  ("R_aids_after_2015", "AIDS after-2015 group RMS"))):
                grid = s.pivot(index="p_symptom_aids", columns="rho", values=metric)
                image = axes[r, c].imshow(grid.to_numpy(float), origin="lower", aspect="auto",
                                          cmap="viridis")
                axes[r, c].set_xticks(range(len(grid.columns)), [f"{v:.2f}" for v in grid.columns])
                axes[r, c].set_yticks(range(len(grid.index)), [f"{v:.2f}" for v in grid.index])
                axes[r, c].set_xlabel("rho (routine hazard scale)")
                axes[r, c].set_ylabel("AIDS symptom annual probability")
                axes[r, c].set_title(f"Backbone {cid}: {title}")
                fig.colorbar(image, ax=axes[r, c])
        fig.suptitle("Stage A response surface (1985 symptom boundary; explanation only)")
        fig.tight_layout()
        fig.savefig(figures / "fig1_stage_a_response_surface.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig1_stage_a_response_surface.png")
    if not discovery.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        for cid, marker in zip(sorted(discovery.backbone.unique()), ("o", "s")):
            d = discovery[discovery.backbone == cid]
            ax.scatter(d.dx2024_abs_error, d.R_aids_after_2015, c=d.primary_pass.map(
                {True: "#1b7837", False: "#bbbbbb"}), marker=marker, s=18, label=f"backbone {cid}")
            ident = d[d.is_identity]
            ax.scatter(ident.dx2024_abs_error, ident.R_aids_after_2015, facecolors="none",
                       edgecolors="black", marker=marker, s=90)
        chosen = discovery[discovery.tuple_id.isin(shortlist_ids)]
        ax.scatter(chosen.dx2024_abs_error, chosen.R_aids_after_2015, marker="x", c="#d6604d",
                   s=60, label="shortlisted")
        ax.set_xlabel("2024 MSM diagnosis |error| (discovery mean)")
        ax.set_ylabel("AIDS after-2015 group RMS (search statistic)")
        ax.set_title("Stage B discovery trade-off (green = primary pass; ring = identity)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(figures / "fig2_discovery_tradeoff.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig2_discovery_tradeoff.png")
    mech = evidence[evidence.role != "backbone_comparator"] if not evidence.empty else evidence
    if not mech.empty and "paired_change__dx2024" in mech:
        metrics = [("dx2024", "2024 MSM dx"), ("aids2019", "AIDS proxy 2019"),
                   ("aids2022", "AIDS proxy 2022"), ("cum_hiv_deaths_2015_2024", "HIV deaths 15-24")]
        fig, axes = plt.subplots(1, len(metrics), figsize=(16, 0.45 * len(mech) + 2), sharey=True)
        labels = mech.tuple_id.tolist()
        for ax, (metric, title) in zip(axes, metrics):
            centre = mech[f"paired_change__{metric}"].to_numpy(float)
            low = centre - mech[f"paired_ci95_low__{metric}"].to_numpy(float)
            high = mech[f"paired_ci95_high__{metric}"].to_numpy(float) - centre
            ax.errorbar(centre, range(len(labels)), xerr=[low, high], fmt="o")
            ax.axvline(0, color="grey", lw=0.8)
            ax.set_title(f"{title}: paired change")
        axes[0].set_yticks(range(len(labels)), labels)
        fig.suptitle("Stage C fresh-seed paired changes versus backbone comparator (95% CI)")
        fig.tight_layout()
        fig.savefig(figures / "fig3_confirmation_vs_backbone.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig3_confirmation_vs_backbone.png")
    if not paths.empty and shortlist_ids:
        p = paths[paths.tuple_id.isin(shortlist_ids) & (paths.stage == "stage_b")]
        fig, ax = plt.subplots(figsize=(8, 5))
        for tid, g in p.groupby("tuple_id"):
            ax.plot(g.year, g.effective_msm_routine_annual_probability, marker=".", label=tid)
        ax.set_xlabel("year")
        ax.set_ylabel("effective MSM routine annual diagnosis probability")
        ax.set_title("Effective MSM routine paths of shortlisted tuples")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(figures / "fig4_effective_routine_paths.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig4_effective_routine_paths.png")
    if len(written) > 4:
        raise ContractError("no more than four figures")
    return written


def _stage_frames(prepared: Prepared, stage: str, journal: Sequence[dict]) -> dict:
    """Seed-level annual (wide), decomposition, ledger means and latent state."""
    if not journal:
        return {}
    annual_rows, ledger_rows, latent_rows = [], [], []
    for item in journal:
        if item.get("error"):
            continue
        keys = {"stage": stage, "tuple_id": item["tuple_id"], "backbone": item["backbone"],
                "seed": item["seed"]}
        years = sorted({int(y) for values in item["annual_outputs"].values() for y in values})
        deaths: dict[int, dict[str, float]] = {}
        for r in item["mortality"]:
            deaths.setdefault(int(r["year"]), {})[r["series"]] = float(r["value"])
        for year in years:
            row = {**keys, "year": year}
            for series, values in item["annual_outputs"].items():
                if year in values or str(year) in values:
                    row[series] = float(values[year] if year in values else values[str(year)])
            row.update(deaths.get(year, {}))
            if year in deaths:
                row["hiv_deaths"] = sum(deaths[year].values())
            annual_rows.append(row)
        for r in item["ledger"]:
            ledger_rows.append({**keys, **r})
        for r in item["snapshots"]:
            if int(r["snapshot_year"]) == 2025:
                latent_rows.append({**keys, **r})
    annual = pd.DataFrame(annual_rows)
    ledger = pd.DataFrame(ledger_rows)
    latent = pd.DataFrame(latent_rows)
    return {"annual": annual, "ledger": ledger, "latent": latent}


def _decomposition(annual: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in annual.columns if c.startswith("hiv_diagnoses_in_") or
               c.startswith("new_diagnoses_endogenous") or c == "new_diagnoses_exogenous_non_msm" or
               c.startswith("aids_progression_while_") or c.startswith("aids_first_diagnosis_proxy")]
    long = annual.melt(id_vars=["stage", "tuple_id", "backbone", "seed", "year"], value_vars=columns,
                       var_name="series").dropna(subset=["value"])
    return long.groupby(["stage", "tuple_id", "backbone", "series", "year"], as_index=False).agg(
        mean=("value", "mean"), sd=("value", "std"), n_seeds=("value", "size"))


def late_presentation(frames: Sequence[pd.DataFrame], plan: dict) -> pd.DataFrame:
    th = plan["decision_thresholds"]
    upper = float(th["late_stage_context_upper_bound"])
    pooled = th["pooled_clinical_aids_context_numerator"] / th["pooled_clinical_aids_context_denominator"]
    logical = th["pooled_clinical_aids_context_numerator"] / th["late_stage_context_denominator"]
    rows = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for row in frame.to_dict(orient="records"):
            fraction = row["fraction_msm_dx_aids_2022_2024"]
            rows.append({
                "stage": row["stage"], "tuple_id": row["tuple_id"], "backbone": row["backbone"],
                "role": row.get("role"),
                "construct_1_fraction_msm_dx_in_clinical_aids_2022_2024": fraction,
                "construct_1_fraction_msm_dx_in_clinical_aids_1996_2025":
                    row["fraction_msm_dx_aids_1996_2025"],
                "construct_2_aids_proxy_2022": row["aids2022"],
                "construct_2_component_progression_while_diagnosed_2022":
                    row["aids_progression_while_diagnosed_msm_2022"],
                "construct_2_component_dx_in_aids_2022": row["hiv_diagnoses_in_aids_stage_msm_2022"],
                "construct_3_shm_msm_late_stage_fraction_2022_2024_context": upper,
                "construct_1_within_one_sided_upper_bound": bool(fraction <= upper),
                "arithmetic_context_208_over_742": logical,
                "pooled_clinical_aids_fraction_all_groups_cross_validation_only": pooled,
                "note": "construct 3 (CD4<350 or AIDS) is broader than construct 1; 32.1% is a "
                        "one-sided upper bound, not an equality target",
            })
    return pd.DataFrame(rows)


def write_report(prepared: Prepared, *, skip_figures: bool = False) -> dict:
    out = prepared.output_dir
    plan = prepared.plan
    targets = prepared.state["targets"]
    z = {t.key: t.z for t in targets}
    manifest = prepared.manifest
    status = {s: manifest["stages"][s]["status"] for s in STAGES}

    ja = load_journal(out, "stage_a")
    surface, residuals_a = summarise_stage(ja, prepared.design["stage_a"], targets,
                                           stage_seeds(plan, "stage_a"))
    if not surface.empty:
        surface.insert(0, "stage", "stage_a")
    surface, smooth_rows = analyse_stage_a(surface, plan)
    prior_rows = prior_audit_comparison(surface, plan, targets) if not surface.empty else []
    surface.to_csv(out / "stage_a_response_surface.csv", index=False)
    pd.DataFrame(smooth_rows).to_csv(out / "stage_a_smoothness.csv", index=False)
    pd.DataFrame(prior_rows).to_csv(out / "stage_a_vs_prior_audit.csv", index=False)

    jb = load_journal(out, "stage_b")
    discovery, residuals_b = summarise_stage(jb, prepared.design["stage_b"], targets,
                                             stage_seeds(plan, "stage_b"))
    if not discovery.empty:
        discovery.insert(0, "stage", "stage_b")
        discovery = add_discovery_context(discovery, plan)
    if status["stage_b"] == STATUS_COMPLETE and (out / "discovery_candidates.csv").exists():
        frozen_disc = pd.read_csv(out / "discovery_candidates.csv")
        if len(frozen_disc) != len(discovery):
            raise ContractError("discovery archive differs from the one the shortlist used")
    else:
        discovery.to_csv(out / "discovery_candidates.csv", index=False)
        residuals_b.to_csv(out / "discovery_target_residuals.csv", index=False)

    shortlist = _read(out / "frozen_shortlist.json") if (out / "frozen_shortlist.json").exists() else None
    jc = load_journal(out, "stage_c")
    confirmation = pd.DataFrame()
    residuals_c = pd.DataFrame()
    if shortlist is not None:
        tuples_c = stage_tuples(prepared, "stage_c")
        confirmation, residuals_c = summarise_stage(jc, tuples_c, targets,
                                                    stage_seeds(plan, "stage_c"))
        if not confirmation.empty:
            confirmation.insert(0, "stage", "stage_c")
            confirmation = confirmation_evidence(prepared, confirmation, discovery, jc, residuals_c)
    confirmation.to_csv(out / "confirmation_summary.csv", index=False)
    residuals_c.to_csv(out / "confirmation_target_residuals.csv", index=False)

    # Seed-level and decomposition outputs.
    decomposition, ledgers, latents = [], [], []
    for stage, journal in (("stage_a", ja), ("stage_b", jb), ("stage_c", jc)):
        frames = _stage_frames(prepared, stage, journal)
        if not frames:
            continue
        frames["annual"].to_csv(out / f"seed_level_annual_{stage}.csv.gz", index=False,
                                compression="gzip")
        frames["ledger"].to_csv(out / f"post2015_stock_flow_seed_level_{stage}.csv.gz",
                                index=False, compression="gzip")
        decomposition.append(_decomposition(frames["annual"]))
        numeric = [c for c in frames["ledger"].columns if c.startswith("delta__") or
                   c in ("stock_start", "stock_end", "stock_change", "flow_sum", "balance_residual")]
        led = frames["ledger"].groupby(["stage", "tuple_id", "backbone", "year", "activity", "metric"],
                                       as_index=False)[numeric].mean()
        led["max_abs_seed_balance_residual"] = frames["ledger"].groupby(
            ["stage", "tuple_id", "backbone", "year", "activity", "metric"])["balance_residual"] \
            .apply(lambda s: s.abs().max()).to_numpy()
        ledgers.append(led)
        latents.append(frames["latent"].groupby(["stage", "tuple_id", "backbone", "series", "activity"],
                                                as_index=False)["value"].agg(["mean", "std", "size"])
                       .rename(columns={"size": "n_seeds"}))
    decomposition = pd.concat(decomposition, ignore_index=True) if decomposition else pd.DataFrame()
    decomposition.to_csv(out / "stage_diagnosis_decomposition.csv", index=False)
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    if not ledger.empty and float(ledger.max_abs_seed_balance_residual.max()) != 0.0:
        raise ContractError("stock-flow ledger does not close exactly")
    ledger.to_csv(out / "post2015_stock_flow.csv", index=False)
    latent = pd.concat(latents, ignore_index=True) if latents else pd.DataFrame()
    if not latent.empty:
        latent.insert(latent.columns.get_loc("series"), "state_timing",
                      "2024_year_end_equals_2025_starting_state")
    latent.to_csv(out / "latent_state_2025.csv", index=False)
    late = late_presentation([surface, discovery, confirmation], plan)
    late.to_csv(out / "late_presentation_context.csv", index=False)

    all_tuples = prepared.design["stage_a"] + prepared.design["stage_b"]
    paths = effective_path_table(all_tuples, plan)
    paths.to_csv(out / "effective_msm_routine_paths.csv", index=False)
    ident = identifiability(discovery, residuals_b, prepared.design["stage_b"], plan) \
        if status["stage_b"] == STATUS_COMPLETE else {"available": False}
    _dump(out / "identifiability.json", ident)

    # Exogenous non-MSM invariance across tuples (same backbone and seed).
    exo = {}
    for stage, journal in (("stage_a", ja), ("stage_b", jb), ("stage_c", jc)):
        seen: dict[tuple, dict] = {}
        worst = 0.0
        for item in journal:
            if item.get("error"):
                continue
            key = (item["backbone"], int(item["seed"]))
            series = {int(y): float(v) for y, v in
                      item["annual_outputs"]["new_diagnoses_exogenous_non_msm"].items()}
            if key in seen:
                worst = max(worst, max(abs(series[y] - seen[key][y]) for y in series))
            else:
                seen[key] = series
        exo[stage] = worst

    design_complete = all(status[s] == STATUS_COMPLETE for s in STAGES)
    mech = confirmation[confirmation.role != "backbone_comparator"] \
        if not confirmation.empty else confirmation
    evaluations = mech.to_dict(orient="records") if not mech.empty and \
        "c1_primary_pass_fresh_seeds" in mech else []
    apparent = bool(not discovery.empty and discovery[~discovery.is_identity & discovery.primary_pass &
                                                      discovery.improves_2024_vs_discovery_identity].shape[0])
    decision = classify_decision(evaluations, design_complete=design_complete,
                                 discovery_apparent_improvement=apparent)
    shortlist_ids = [e["tuple_id"] for e in shortlist["entries"]] if shortlist else []
    figures = [] if skip_figures else write_figures(out, surface, discovery, confirmation, paths,
                                                    shortlist_ids)

    lines = _report_lines(prepared, status, surface, smooth_rows, prior_rows, discovery,
                          shortlist, confirmation, decision, ident, late, exo, figures, z,
                          design_complete)
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    manifest["report"] = {"generated_utc": _now(), "decision": decision,
                          "design_complete": design_complete, "figures": figures,
                          "exogenous_non_msm_max_abs_difference_across_tuples": exo}
    if design_complete:
        manifest["status"] = "REPORTED_COMPLETE"
    _save_manifest(prepared)
    return {"decision": decision, "design_complete": design_complete, "stage_status": status}


def _report_lines(prepared, status, surface, smooth_rows, prior_rows, discovery, shortlist,
                  confirmation, decision, ident, late, exo, figures, z, design_complete) -> list[str]:
    plan = prepared.plan
    mech = confirmation[confirmation.role != "backbone_comparator"] if not confirmation.empty else confirmation
    comps = confirmation[confirmation.role == "backbone_comparator"] if not confirmation.empty else confirmation
    n_disc_pass = int(discovery[~discovery.is_identity].primary_pass.sum()) if not discovery.empty else 0
    n_conf_pass = int(mech.primary_pass.sum()) if not mech.empty else 0
    lines = ["# Focused stage-structured diagnosis calibration", "",
             f"Design `{DESIGN}`. Structural investigation, not production calibration and not "
             "an HTA baseline. Production configuration and HTA baseline are unchanged.", "",
             "Stage status: " + ", ".join(f"{s} **{v}**" for s, v in status.items()) + ".", ""]
    if not design_complete:
        lines += ["> **The frozen design is not complete.** Nothing below treats a partial archive "
                  "as the completed design.", ""]
    lines += ["## First-page answers", ""]
    lines.append(f"1. **Primary passes.** Discovery (5 seeds, non-identity vectors): "
                 f"**{n_disc_pass}** of {max(len(discovery) - 2, 0) if not discovery.empty else 0}. "
                 f"Confirmed on 20 fresh seeds: **{n_conf_pass}** of {len(mech)} mechanism tuples.")
    if not mech.empty and "dx2024_error_reduction_fraction" in mech:
        best = mech.sort_values("dx2024_abs_error").iloc[0]
        comp = comps.set_index("backbone").loc[best.backbone]
        lines.append(f"2. **2024 MSM diagnosis error (confirmed).** Best confirmed tuple "
                     f"`{best.tuple_id}`: |error| {_fmt(best.dx2024_abs_error)} vs "
                     f"{_fmt(comp.dx2024_abs_error)} for its backbone comparator on the same seeds "
                     f"(reduction {_fmt(100 * best.dx2024_error_reduction_fraction)}%; "
                     f"{int(best.paired_seeds_improved_2024)}/20 seeds improved). Observed "
                     f"{_fmt(z['shm_new_diagnoses_msm@2024'], 0)}. Range over confirmed mechanism "
                     f"tuples: {_fmt(mech.dx2024_abs_error.min())}-{_fmt(mech.dx2024_abs_error.max())}.")
        lines.append("3. **2019/2022 AIDS proxy and components.** " + "; ".join(
            f"`{r.tuple_id}` 2019 {_fmt(r.aids2019)} (dx-in-AIDS {_fmt(r.hiv_diagnoses_in_aids_stage_msm_2019)}, "
            f"diagnosed progression {_fmt(r.aids_progression_while_diagnosed_msm_2019)}), 2022 "
            f"{_fmt(r.aids2022)} (dx-in-AIDS {_fmt(r.hiv_diagnoses_in_aids_stage_msm_2022)}, "
            f"diagnosed progression {_fmt(r.aids_progression_while_diagnosed_msm_2022)})"
            for r in confirmation.itertuples()) +
            f". Observed 2019 {_fmt(z['rivm_aids_msm@2019'], 0)}, 2022 {_fmt(z['rivm_aids_msm@2022'], 0)}.")
        lines.append("4. **Infections and HIV deaths (2015-2024, paired change vs comparator).** " + "; ".join(
            f"`{r.tuple_id}` infections {_fmt(100 * r.msm_infection_change_fraction)}%, deaths "
            f"{_fmt(100 * r.hiv_death_change_fraction)}%" + (" **(material, >10%)**"
                                                              if r.material_hiv_death_increase or
                                                              r.material_msm_infection_increase else "")
            for r in mech.itertuples()) + ".")
    else:
        lines += ["2. **2024 MSM diagnosis error (confirmed).** Not available: no completed confirmation.",
                  "3. **2019/2022 AIDS proxy.** Not available from confirmation; see Stage A and discovery tables.",
                  "4. **Infections and deaths.** Not available from confirmation."]
    frac = late[late.stage == "stage_c"] if not late.empty else late
    if frac.empty:
        frac = late[late.stage == "stage_b"] if not late.empty else late
    if not frac.empty:
        col = "construct_1_fraction_msm_dx_in_clinical_aids_2022_2024"
        lines.append(f"5. **Fraction of MSM diagnoses in clinical AIDS (2022-2024).** Range "
                     f"{_fmt(100 * frac[col].min())}%-{_fmt(100 * frac[col].max())}% across "
                     f"{'confirmed' if (frac.stage == 'stage_c').all() else 'discovery'} tuples. "
                     "This is construct 1 (diagnosis while in the model's clinical-AIDS compartment). "
                     "SHM's 32.1% (238/742) counts CD4<350 **or** AIDS, a broader construct: it is a "
                     "one-sided upper bound only, never an equality target (208/742 = 28.0% is an "
                     f"arithmetic ceiling if every pooled clinical-AIDS case were MSM). "
                     f"{int((~frac.construct_1_within_one_sided_upper_bound).sum())} tuple(s) exceed 32.1%.")
    else:
        lines.append("5. **Clinical-AIDS fraction.** Not available.")
    if not mech.empty and "c6_not_boundary_point" in mech:
        lines.append(f"6. **Region breadth.** Confirmed mechanism tuples at a coordinate boundary: "
                     f"{int((~mech.c6_not_boundary_point).sum())}/{len(mech)}; with a nearby discovery "
                     f"region of the same behaviour: {int(mech.c7_nearby_region.sum())}/{len(mech)}. "
                     + ("Useful region is **broad**." if mech.c7_nearby_region.any() and
                        mech.c6_not_boundary_point.any() else
                        "Useful region is **narrow or boundary-dependent**."))
    elif not discovery.empty:
        good = discovery[discovery.same_qualitative_behaviour & ~discovery.is_identity]
        lines.append(f"6. **Region breadth (discovery only).** {len(good)} discovery vectors pass and "
                     f"improve 2024 vs identity; {int(good.boundary_point.sum())} of them sit at a boundary.")
    else:
        lines.append("6. **Region breadth.** Not available.")
    if ident.get("available"):
        parts = []
        for cid, b in ident["backbones"].items():
            raw = b["local_sensitivity_raw_coordinates"]
            eff = b["local_sensitivity_effective_path_coordinates"]
            def describe(sens: dict) -> str:
                if "rank_at_1pct" not in sens:
                    return f"not estimable ({sens.get('reason', 'unstable')})"
                rank = f"effective rank {sens['rank_at_1pct']}/{sens['n_coordinates']}"
                if not sens["full_rank"]:
                    return rank + " (rank-deficient: not separately identified)"
                return (rank + f", condition number {_fmt(sens['condition_number'], 1)}"
                        + ("" if sens["stable"] else " (numerically unstable; not interpreted)"))

            parts.append(f"backbone {cid}: raw coordinates {describe(raw)}; effective-path "
                         f"coordinates {describe(eff)}; "
                         f"{b['n_materially_different_but_indistinguishable_pairs']} materially different "
                         "but indistinguishable leading pairs; symptom column explained by routine "
                         f"columns R^2={_fmt(raw.get('symptom_column_explained_by_routine_r2'), 2)}")
        lines.append("7. **Identifiability (exploratory).** " + "; ".join(parts) +
                     ". Precise anchor estimates are not reported where the ridge is non-identifiable.")
    else:
        lines.append("7. **Identifiability.** Not available until discovery completes.")
    lines.append(f"8. **Broader recalibration justified?** "
                 f"{'Yes — preparation of a proposal only, not production adoption.' if decision['label'] == LABEL_SUPPORTED else 'No.'}")
    lines += ["", f"**Decision: {decision['label']}**", "", f"Reason: {decision['reason']}."]
    if decision.get("follow_up"):
        lines.append(f"Single smallest follow-up: {decision['follow_up']}")
    lines += ["", "### Evidence for every clause", ""]
    if not mech.empty and "c1_primary_pass_fresh_seeds" in mech:
        header = "| tuple | " + " | ".join(k.split("_", 1)[0] for k, _ in CLAUSES) + " |"
        lines += [header, "|" + "---|" * (len(CLAUSES) + 1)]
        for r in mech.to_dict(orient="records"):
            lines.append(f"| `{r['tuple_id']}` | " + " | ".join("yes" if r[k] else "no"
                                                                 for k, _ in CLAUSES) + " |")
        lines += [""] + [f"- **{k.split('_', 1)[0]}**: {text}." for k, text in CLAUSES]
        lines.append("- Thresholds are predeclared in the frozen plan (`decision_thresholds`).")
    else:
        lines.append("No confirmed tuple is available, so no clause can be satisfied on fresh seeds.")
    lines += ["", "## Stage A: corrected moderate response surface", ""]
    if not surface.empty:
        cols = ["backbone", "rho", "p_symptom_aids", "dx2024", "dx2024_abs_error", "aids2019", "aids2022",
                "primary_i_max", "primary_pass", "cum_msm_infections_2015_2024", "cum_hiv_deaths_2015_2024",
                "pareto_efficient_explanation_only"]
        lines += [surface[cols].to_markdown(index=False, floatfmt=".2f"), ""]
        lines += ["Smoothness (second differences in MC standard errors; predeclared threshold "
                  f"{plan['decision_thresholds']['stage_a_smoothness_se_multiple']}):", "",
                  pd.DataFrame(smooth_rows).to_markdown(index=False, floatfmt=".2f")
                  if smooth_rows else "(incomplete surface)", ""]
        if prior_rows:
            material = [r for r in prior_rows if r["material_change"] and
                        r["prior_profile"] == "rho_0p80_aids_0p35"]
            lines += ["1985 availability boundary versus the prior audit:", "",
                      pd.DataFrame(prior_rows).to_markdown(index=False, floatfmt=".2f"), "",
                      ("**The corrected availability boundary materially changes the prior "
                       "rho=0.80/AIDS=0.35 result** for: " +
                       ", ".join(f"{r['backbone']} {r['metric']}" for r in material) + "."
                       if material else
                       "The corrected availability boundary does **not** materially change the "
                       "prior rho=0.80/AIDS=0.35 result (differences within 10% of target). The "
                       "(0.80, 0.00) rows share no boundary effect and show seed noise only."), ""]
        lines.append("Pareto-efficient profiles are for explanation only; no continuous optimum is "
                     "selected from the coarse grid. rho is not carried into Stage B.")
    else:
        lines.append("Stage A has no complete profiles.")
    lines += ["", "## Stage B: focused discovery", ""]
    if not discovery.empty:
        top = discovery.sort_values("search_j").head(15)
        cols = ["tuple_id", "origin", *FOCUSED_NAMES, "search_j", "primary_i_max", "primary_pass",
                "dx2024_abs_error", "boundary_point", "has_nearby_region"]
        lines += ["Fifteen lowest search J (ranking only; not a likelihood or posterior):", "",
                  top[cols].to_markdown(index=False, floatfmt=".3f"), ""]
    if shortlist:
        lines += ["## Frozen shortlist", "",
                  pd.DataFrame(shortlist["entries"])[["tuple_id", "backbone", "role", "selected_as",
                                                      "discovery_primary_pass",
                                                      "discovery_search_j"]]
                  .to_markdown(index=False, floatfmt=".3f"), ""]
    lines += ["## Stage C: fresh confirmation", ""]
    if not confirmation.empty:
        cols = [c for c in ["tuple_id", "role", "primary_i_max", "primary_pass", "dx2024",
                            "dx2024_abs_error", "aids2019", "aids2022",
                            "cum_hiv_deaths_2015_2024", "undiagnosed_msm_start2025"]
                if c in confirmation]
        lines += [confirmation[cols].to_markdown(index=False, floatfmt=".2f"), ""]
    else:
        lines.append("No completed confirmation tuples.")
    lines += ["", "## Implementation checks", "",
              "- Exogenous non-MSM diagnoses: maximum absolute difference across tuples with the "
              "same backbone and seed = " + ", ".join(f"{k} {_fmt(v, 3)}" for k, v in exo.items()) + ".",
              "- Stage counters equal endogenous diagnoses and the AIDS proxy equals its two "
              "components in every trajectory-year (checked per trajectory; a failure stops it).",
              "- AIDS-stage diagnoses before 1985 are zero in every trajectory (checked per trajectory).",
              "- Post-2015 undiagnosed MSM stock-flow ledger closes exactly (max residual 0).",
              "", "## Late presentation", "",
              "Three constructs are kept separate: (1) HIV diagnosis while in the model's "
              "clinical-AIDS compartment; (2) the RIVM AIDS proxy (diagnosed-stage progression plus "
              "diagnosis in AIDS); (3) surveillance late diagnosis (CD4<350 or AIDS). Constructs 1 and "
              "2 are not 'all late diagnoses'. See `late_presentation_context.csv`.",
              "", "## Figures", ""] + [f"- `{f}`" for f in figures] + [
              "", "## Reproducibility", "", "See `USER_RUN_COMMANDS.md` in the repository root, "
              "`manifest.json`, `frozen_plan.json`, `validation.json`.", "",
              f"**{decision['label']}**"]
    return lines


# ==========================================================================
# Smoke (user-launched; its own directory)
# ==========================================================================

def smoke(plan_path: Path, output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ContractError(f"smoke output directory already exists: {output_dir}")
    validate(plan_path, injection_crosscheck=False)
    plan = load_plan(plan_path)
    _, _, _, _, bases, _ = _source(plan)
    design = build_design(plan, bases)
    output_dir.mkdir(parents=True)
    seed = int(plan["smoke"]["seeds"][0])
    rows, started = [], time.time()
    for cid in [int(c) for c in plan["candidate_ids"]]:
        base = bases[cid]
        identity = with_stage_block(base, 1.0, 0.0, int(plan["symptom_start_year"]))
        warm = next(i for i in design["stage_b"] if i["backbone"] == cid and
                    i["origin"] == "warm:prior_rho0p80_aids0p35")
        runs = {}
        for label, config in (("legacy_control", without_stage_block(base)),
                              ("enabled_identity_1985", identity),
                              (warm["tuple_id"], warm["config"])):
            runs[label] = run_trajectory(config, {"stage": "smoke", "tuple_id": label,
                                                  "backbone": cid, "seed": seed}, plan)
        a, b = runs["legacy_control"], runs["enabled_identity_1985"]
        if a["summary_sha256"] != b["summary_sha256"] or \
                a["final_state_sha256"] != b["final_state_sha256"]:
            raise ContractError(f"backbone {cid}: enabled identity differs from legacy control")
        for label, item in runs.items():
            rows.append({"backbone": cid, "run": label, "seed": seed,
                         "summary_sha256": item["summary_sha256"],
                         "final_state_sha256": item["final_state_sha256"],
                         "pre_1985_aids_stage_dx": item["boundary"]["pre_boundary_aids_stage_diagnoses"],
                         "aids_stage_dx_1985_1987": item["boundary"][
                             "post_boundary_aids_stage_diagnoses_to_check_year"],
                         "wall_seconds": item["wall_seconds"]})
    per = float(np.mean([r["wall_seconds"] for r in rows]))
    total = int(plan["total_trajectory_budget_max"])
    result = {"design": DESIGN, "role": "smoke", "ok": True, "identity_exact": True,
              "smoke_seeds": plan["smoke"]["seeds"], "simulations_started": len(rows),
              "elapsed_seconds": time.time() - started, "mean_trajectory_seconds": per,
              "estimated_full_design_minutes_at_8_workers": per * total / 8 / 60,
              "rows": rows}
    pd.DataFrame(rows).to_csv(output_dir / "smoke_comparison.csv", index=False)
    _dump(output_dir / "manifest.json", result)
    return result


# ==========================================================================
# CLI
# ==========================================================================

def orchestrate(plan_path: Path, output_dir: Path, *, workers: int,
                shortlist_sha256: str | None, skip_figures: bool) -> dict:
    """Resumable one-command path. Never prepares, never starts C unreviewed."""
    log = []
    for stage in ("stage_a", "stage_b"):
        prepared = load_prepared(output_dir, plan_path)
        if prepared.manifest["stages"][stage]["status"] == STATUS_COMPLETE:
            log.append({stage: "already COMPLETE"})
            continue
        result = run_stage(prepared, stage, workers=workers, resume=True)
        log.append(result)
        if result["status"] != STATUS_COMPLETE:
            write_report(load_prepared(output_dir, plan_path), skip_figures=skip_figures)
            return {"stopped_after": stage, "log": log}
    prepared = load_prepared(output_dir, plan_path)
    if prepared.manifest.get("shortlist_sha256") is None:
        log.append(freeze_shortlist(prepared))
        prepared = load_prepared(output_dir, plan_path)
    if shortlist_sha256 is None:
        write_report(prepared, skip_figures=skip_figures)
        return {"stopped_after": "shortlist_frozen", "log": log,
                "next": "review frozen_shortlist.json, then rerun with --shortlist-sha256 "
                        f"{prepared.manifest['shortlist_sha256']}"}
    if prepared.manifest["stages"]["stage_c"]["status"] != STATUS_COMPLETE:
        log.append(run_stage(prepared, "stage_c", workers=workers, resume=True,
                             shortlist_sha256=shortlist_sha256))
    report = write_report(load_prepared(output_dir, plan_path), skip_figures=skip_figures)
    return {"stopped_after": "report", "log": log, "report": report}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--smoke-output-dir", type=Path, default=DEFAULT_SMOKE_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--shortlist-sha256", default=None)
    parser.add_argument("--skip-figures", action="store_true")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--smoke", action="store_true")
    modes.add_argument("--stage", choices=sorted(STAGE_ALIASES))
    modes.add_argument("--freeze-shortlist", action="store_true")
    modes.add_argument("--report", action="store_true")
    modes.add_argument("--orchestrate", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 8:
        raise ContractError("workers must be in [1, 8]")
    if args.validate_only:
        result = validate(args.plan)
    elif args.prepare:
        result = prepare(args.plan, args.output_dir)
    elif args.smoke:
        result = smoke(args.plan, args.smoke_output_dir)
    elif args.stage:
        result = run_stage(load_prepared(args.output_dir, args.plan), STAGE_ALIASES[args.stage],
                           workers=args.workers, resume=args.resume,
                           shortlist_sha256=args.shortlist_sha256)
    elif args.freeze_shortlist:
        result = freeze_shortlist(load_prepared(args.output_dir, args.plan))
    elif args.report:
        result = write_report(load_prepared(args.output_dir, args.plan),
                              skip_figures=args.skip_figures)
    else:
        result = orchestrate(args.plan, args.output_dir, workers=args.workers,
                             shortlist_sha256=args.shortlist_sha256,
                             skip_figures=args.skip_figures)
    print(json.dumps(ccs.json_safe(result), indent=2, default=str))


if __name__ == "__main__":
    main()
