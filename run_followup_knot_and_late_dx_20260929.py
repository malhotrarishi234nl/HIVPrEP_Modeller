#!/usr/bin/env python3
"""Follow-up runs after the 2008-vs-2010 conference suite (prepared 2026-09-29).

DECLARED BEFORE ANY TRAJECTORY IS RUN
--------------------------------------
This is a POST-HOC extension. It was designed after seeing the conference-suite results
and after the supervisor's comment on the diagnosis peak. It must be disclosed as such.

Step 1  Same-theta knot test. The two selected candidates (break_2010 #1946, break_2008 #1787)
        are each run under BOTH ramp-endpoint configurations (knot 2010, knot 2008) on the same
        fresh seeds. Cross-knot cells hold the other 33 calibrated coordinates fixed, so any
        difference between own- and cross-knot curves is attributable to the knot alone.
Step 3  The own-knot cells are fresh-seed re-confirmations. The selected candidates were chosen
        on seeds 210100-210119; these seeds (230100-230119) were not used in selection.
Step 2  Late-diagnosis engine on the new backbones. For each backbone, only the four diagnosis
        coordinates (three MSM anchors 1996/2004/2019 and the AIDS symptom probability) are
        searched, exactly as in the 2026-09-22 stage-diagnosis work; the other 33 coordinates
        stay at the backbone values. Discovery uses 5 seeds (230200-230204); a frozen shortlist
        (at most 3 per backbone) is confirmed on the SAME 20 fresh seeds as steps 1/3, so each
        finalist is paired with its backbone's own-knot legacy cell.

Declared computational choices (not empirical estimates): 96 discovery vectors per backbone
(1 transfer anchor + 95 maximin-LHS points in an L-infinity box of half-width 0.25 in
normalised coordinates centred on the backbone's own multipliers and the symptom probability
of D_1788_v109); shortlist rule in select_finalists(); everything else is the frozen
evaluation path (rcc.evaluate_candidate_worker, ccs.build_record, ccs.acceptance).

Nothing here modifies a frozen tool. Every trajectory goes through the same worker as the suite.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_stage_structured_diagnosis_focused_calibration as fc
from coupled_calibration_search import ContractError
from tools.artifact_vault import (
    ArtifactVaultError,
    CONFERENCE_ARTIFACT_ID,
    conference_artifact_uri,
    configured_conference_artifact_root,
    require_conference_artifact_subpath,
)

ROOT = Path(__file__).resolve().parent
SUITE = ROOT / "outputs/suites/on_art_break_year_2008_vs_2010_20260928_rev6"
CONFERENCE_CALIBRATION_RELATIVE = Path(
    "outputs/suites/on_art_break_year_2008_vs_2010_20260928_rev6"
)
_ARTIFACT_ROOT, ARTIFACT_ROOT_SOURCE = configured_conference_artifact_root(ROOT)
FC_PLAN = ROOT / "outputs/calibration/stage_structured_diagnosis_focused_calibration_20260922/frozen_plan.json"
FC_PLAN_SHA = "534e0d8f04f1aba662699f2b1c9427295953fc606fcf08c391511f260c8a0675"
PRIOR_V109 = ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_configuration.json"
WORKBOOK = ROOT.parent / "data/raw/validation/DiseaseData.xlsx"
OUT_REAL = (Path(os.environ["FOLLOWUP_OUT_ROOT"]) if "FOLLOWUP_OUT_ROOT" in os.environ
            else ROOT / "outputs/calibration/followup_knot_and_late_dx_20260929")

CASES = {"break_2010": {"candidate_id": 1946, "knot": 2010},
         "break_2008": {"candidate_id": 1787, "knot": 2008}}
CONF_SEEDS = tuple(range(230100, 230120))     # 20 fresh confirmation seeds
DISC_SEEDS = tuple(range(230200, 230205))     # 5 discovery seeds
N_VECTORS = 96
BOX_HALF = 0.25
DESIGN_SEED = 230400
FINALISTS = 3
SEC_PER_TRAJ_PER_WORKER = 16.3                # runbook figure, 2026-09-22
KEEP_SERIES = ("new_diagnoses_msm", "new_diagnoses", "aids_first_diagnosis_proxy_msm",
               "new_infections_msm", "new_infections", "PLHIV", "aids_deaths", "new_aids_cases")
ALLOWED_PREFIXES = (
    "transmission_model.disease.diagnosis.stage_structured_diagnosis",
    "transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.1996",
    "transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.2004",
    "transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.2019",
)
FAKE = False


def out_root() -> Path:
    return OUT_REAL.with_name(OUT_REAL.name + "_FAKE") if FAKE else OUT_REAL


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=float))
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------------

def require_historical_source() -> Path:
    """Fail before running if the retained calibration artifact is unavailable."""

    return require_conference_artifact_subpath(ROOT, CONFERENCE_CALIBRATION_RELATIVE)


def load_case(case: str) -> dict:
    plan = json.loads((SUITE / "cases" / case / "resolved_configs/coupled_search_plan.json").read_text())
    inputs = plan["inputs"]
    paths = {k: ROOT / inputs[k] for k in ("base_config", "calib_space", "target_register", "wave_plan")}
    for name, path in paths.items():
        if not path.is_file():
            raise ContractError(f"{case}: input missing: {name} -> {path}")
        if ccs.sha256_file(path) != inputs["expected_sha256"][name]:
            raise ContractError(f"{case}: {name} hash differs from the plan's expected hash")
    space_json = json.loads(paths["calib_space"].read_text())
    wave_plan = json.loads(paths["wave_plan"].read_text())
    return {"plan": plan, "paths": paths, "space_json": space_json,
            "space": ccs.ParameterSpace.from_calib_space(space_json),
            "base_config": json.loads(paths["base_config"].read_text()),
            "targets": ccs.load_frozen_targets(paths["target_register"], wave_plan)}


def load_theta(case: str) -> dict:
    cid = CASES[case]["candidate_id"]
    backup = require_historical_source()
    path = backup / "cases" / case / "calibration/confirmation/raw" / f"candidate_{cid}.json"
    raw = json.loads(path.read_text())
    params = {k: float(v) for k, v in raw["params"].items()}
    if ccs.vector_identity(params) != raw["params_sha256"]:
        raise ContractError(f"{case}: parameter identity differs from the recorded hash")
    return {"params": params, "raw": raw}


_CTX: dict | None = None


def context() -> dict:
    global _CTX
    if _CTX is not None:
        return _CTX
    if ccs.sha256_file(FC_PLAN) != FC_PLAN_SHA:
        raise ContractError("the prior focused frozen plan differs from its pinned hash")
    cases = {c: load_case(c) for c in CASES}
    keys = [[t.key for t in cases[c]["targets"]] for c in CASES]
    if keys[0] != keys[1]:
        raise ContractError("the two cases resolve different target lists")
    _CTX = {"fcplan": json.loads(FC_PLAN.read_text()), "cases": cases,
            "theta": {c: load_theta(c) for c in CASES}, "targets": cases["break_2010"]["targets"],
            "source_artifact": {"artifact_id": CONFERENCE_ARTIFACT_ID,
                                "artifact_uri": conference_artifact_uri(CONFERENCE_CALIBRATION_RELATIVE),
                                "root_selection": ARTIFACT_ROOT_SOURCE,
                                "scientific_status": "historical; not current Option-B evidence"}}
    return _CTX


def check_knot(config: dict, theta: dict, knot: int) -> None:
    sched = config["transmission_model"]["disease"]["progression"]["chronic_to_aids_on_art"]["schedule"]["value"]
    if sorted(int(k) for k in sched) != [2000, int(knot)]:
        raise ContractError(f"unexpected on-ART schedule keys {sorted(sched)} for knot {knot}")
    if not (math.isclose(float(sched["2000"]), theta["chronic_to_aids_on_art_early"], rel_tol=0, abs_tol=1e-15)
            and math.isclose(float(sched[str(knot)]), theta["chronic_to_aids_on_art_recent"],
                             rel_tol=0, abs_tol=1e-15)):
        raise ContractError("on-ART schedule values do not equal the candidate's early/recent coordinates")


def build_cells(ctx: dict) -> list[dict]:
    cells = []
    for i, tcase in enumerate(CASES):
        for j, kcase in enumerate(CASES):
            st = ctx["cases"][kcase]
            theta = ctx["theta"][tcase]["params"]
            cfg = rcc.injected_config(st["base_config"], theta, st["space_json"])
            check_knot(cfg, theta, CASES[kcase]["knot"])
            sha = ccs.sha256_json(cfg)
            if tcase == kcase and sha != ctx["theta"][tcase]["raw"]["injected_config_sha256"]:
                raise ContractError(f"{tcase}: own-knot cell does not rebuild the original run configuration")
            cells.append({"label": f"theta_{tcase}__knot_{kcase}", "cid": 9000 + 10 * i + j,
                          "theta_case": tcase, "knot_case": kcase,
                          "role": "own" if tcase == kcase else "cross",
                          "config": cfg, "params": theta, "config_sha256": sha})
    return cells


def _generate_late_design(ctx: dict, cells: list[dict]) -> list[dict]:
    fcp = ctx["fcplan"]
    prior = json.loads(PRIOR_V109.read_text())["config"]
    sched = fc._get(prior, fc.MSM_SCHEDULE_PATH)
    block = fc._get(prior, fc.PROFILE_PATH)
    transfer = {p["name"]: float(sched[p["anchor_year"]])
                for p in fcp["focused_parameters"] if p["anchor_year"] is not None}
    transfer[fc.SYMPTOM_NAME] = float(block["aids_symptom_annual_probability"]["value"])
    rows = []
    for bi, tcase in enumerate(CASES):
        own = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "own")
        backbone = own["config"]
        if "stage_structured_diagnosis" in backbone["transmission_model"]["disease"]["diagnosis"]:
            raise ContractError(f"{tcase}: backbone already carries a stage-structured block")
        centre = dict(fc.backbone_vector(backbone, fcp))
        centre[fc.SYMPTOM_NAME] = transfer[fc.SYMPTOM_NAME]
        centre_u = fc.normalise(centre, fcp)
        lo, hi = np.clip(centre_u - BOX_HALF, 0, 1), np.clip(centre_u + BOX_HALF, 0, 1)
        unit = fc.maximin_lhs(N_VECTORS - 1, len(fc.FOCUSED_NAMES), DESIGN_SEED + bi, 1000, exclude=None)
        entries = [("transfer_D_1788_v109", transfer)]
        entries += [("maximin_box", fc.denormalise(u, fcp)) for u in lo + unit * (hi - lo)]
        for idx, (origin, vec) in enumerate(entries):
            cfg = fc.stage_b_config(backbone, vec, fcp)
            bad = [p for p in fc.changed_paths(backbone, cfg)
                   if not any(p == a or p.startswith(a + ".") for a in ALLOWED_PREFIXES)]
            if bad:
                raise ContractError(f"{tcase} v{idx}: unexpected configuration changes: {bad[:4]}")
            if float(cfg["transmission_model"]["disease"]["diagnosis"]["stage_structured_diagnosis"]
                     ["routine_hazard_scale"]["value"]) != 1.0:
                raise ContractError("routine_hazard_scale must be the identity (1.0)")
            rows.append({"tuple_id": f"LD_{tcase}_v{idx:03d}", "cid": 20000 + 1000 * bi + idx,
                         "backbone_case": tcase, "theta_candidate": CASES[tcase]["candidate_id"],
                         "origin": origin, "vector": vec, "u": [float(x) for x in fc.normalise(vec, fcp)],
                         "config": cfg, "config_sha256": ccs.sha256_json(cfg), "params": own["params"]})
    if len({r["config_sha256"] for r in rows}) != len(rows):
        raise ContractError("duplicate late-diagnosis configurations in the design")
    return rows


def _check_changes(backbone: dict, cfg: dict, tag: str) -> None:
    bad = [p for p in fc.changed_paths(backbone, cfg)
           if not any(p == a or p.startswith(a + ".") for a in ALLOWED_PREFIXES)]
    if bad:
        raise ContractError(f"{tag}: unexpected configuration changes: {bad[:4]}")


def build_late_design(ctx: dict, cells: list[dict]) -> list[dict]:
    """Load the frozen design if it exists (exact stored vectors); otherwise generate and freeze it."""
    path = out_root() / "late_dx/design.json"
    if not path.exists():
        rows = _generate_late_design(ctx, cells)
        atomic_json(path, design_record(rows))
        return rows
    stored = json.loads(path.read_text())
    if ccs.sha256_json(stored["design"]) != stored["design_sha256"]:
        raise ContractError("the frozen late-diagnosis design file was modified after it was written")
    rows = []
    for e in stored["design"]:
        own = next(c for c in cells if c["theta_case"] == e["backbone_case"] and c["role"] == "own")
        cfg = fc.stage_b_config(own["config"], e["vector"], ctx["fcplan"])
        if ccs.sha256_json(cfg) != e["config_sha256"]:
            raise ContractError(f"{e['tuple_id']} no longer rebuilds to its stored configuration hash")
        _check_changes(own["config"], cfg, e["tuple_id"])
        rows.append({**e, "config": cfg, "params": own["params"]})
    return rows


def seed_preflight() -> None:
    used = set(CONF_SEEDS) | set(DISC_SEEDS)
    hits = []
    files = list((ROOT / "configs").glob("*.json"))
    files += list((ROOT / "outputs/calibration").glob("*/frozen_plan.json"))
    files += list((ROOT / "outputs/calibration").glob("*/design.json"))
    files += list(SUITE.glob("cases/*/resolved_configs/coupled_search_plan.json"))
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"\b(2301\d\d|2302\d\d)\b", text):
            if int(m.group(1)) in used:
                hits.append((f.name, m.group(1)))
    if hits:
        raise ContractError(f"declared seeds already appear in prior plans: {hits[:5]}")


# ----------------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------------

def fake_worker(payload: dict) -> dict:
    """Synthetic stand-in used ONLY by --fake-engine to test the pipeline. Never scientific."""
    cfg_sha = ccs.sha256_json(payload["config"])
    shift = (int(cfg_sha[:6], 16) / 0xFFFFFF - 0.5) * 0.5
    rng = np.random.default_rng(zlib.crc32((cfg_sha[:12] + str(payload["replicate_seeds"])).encode()))
    years = list(range(1975, 2026))
    out = {}
    for name, scale in (("new_diagnoses_msm", 700.0), ("aids_first_diagnosis_proxy_msm", 110.0)):
        base = scale * (1 + shift) * np.exp(-0.5 * ((np.array(years) - 2008) / 7.0) ** 2) + 0.2 * scale
        out[name] = {y: float(v * (1 + 0.1 * rng.standard_normal())) for y, v in zip(years, base)}
    for name in KEEP_SERIES:
        out.setdefault(name, {y: float(rng.random() * 100) for y in years})
    cols, tyears = payload["target_columns"], payload["target_years"]
    matrix = [[out[c][y] for c, y in zip(cols, tyears)]]
    return {"candidate_id": payload["candidate_id"], "batch": payload["batch"], "origin": payload["origin"],
            "error": "", "config_sha256": cfg_sha, "params_sha256": ccs.vector_identity(payload["params"]),
            "replicate_seeds": payload["replicate_seeds"], "target_matrix": matrix,
            "per_seed": [{"seed": payload["replicate_seeds"][0], "annual_outputs": out}],
            "wall_seconds": 0.0}


def make_tasks(items: list[dict], seeds, root: Path, ctx: dict, stage_key) -> list[dict]:
    tasks = []
    for it in items:
        for s in seeds:
            tasks.append({"item": it, "seed": int(s),
                          "path": root / stage_key(it) / f"seed_{int(s)}.json"})
    return tasks


def run_tasks(tasks: list[dict], workers: int, ctx: dict, label: str) -> None:
    pending = [t for t in tasks if not t["path"].exists()]
    print(f"[{label}] {len(tasks)} trajectories planned, {len(tasks) - len(pending)} already complete, "
          f"{len(pending)} to run on {workers} workers (~{len(pending) * SEC_PER_TRAJ_PER_WORKER / workers / 60:.1f} min)",
          flush=True)
    if not pending:
        return
    worker = fake_worker if FAKE else rcc.evaluate_candidate_worker
    targets = ctx["targets"]
    started, finished, failure = time.time(), 0, None
    it = iter(pending)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        inflight: dict = {}

        def submit() -> bool:
            try:
                t = next(it)
            except StopIteration:
                return False
            item = t["item"]
            proposal = {"candidate_id": item["cid"], "batch": -1,
                        "origin": item.get("origin", item.get("label", "")), "params": item["params"]}
            payload = rcc._payload_for(proposal, config=item["config"], seeds=[t["seed"]], targets=targets)
            inflight[pool.submit(worker, payload)] = t
            return True

        for _ in range(workers * 2):
            if not submit():
                break
        while inflight:
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in done:
                t = inflight.pop(fut)
                row = fut.result()
                if row.get("error"):
                    failure = failure or (t["path"].parent.name, t["seed"], row["error"])
                    continue
                if row.get("config_sha256") != t["item"]["config_sha256"]:
                    failure = failure or (t["path"].parent.name, t["seed"], "config hash mismatch")
                    continue
                ps = row["per_seed"][0]
                annual = {s: {str(y): v for y, v in ps["annual_outputs"][s].items()}
                          for s in KEEP_SERIES if s in ps["annual_outputs"]}
                atomic_json(t["path"], {
                    "label": t["item"].get("label", t["item"].get("tuple_id")), "cid": t["item"]["cid"],
                    "seed": t["seed"], "target_row": row["target_matrix"][0], "annual": annual,
                    "config_sha256": row["config_sha256"], "wall_seconds": row["wall_seconds"],
                    "finished_utc": now(), "fake_engine": FAKE})
                finished += 1
                rate = (time.time() - started) / finished
                print(f"[{label}] {finished}/{len(pending)} done, about "
                      f"{rate * (len(pending) - finished) / 60:.1f} min left at the current pace", flush=True)
                if failure is None:
                    submit()
    if failure:
        raise ContractError(f"[{label}] stopped: {failure[0]} seed {failure[1]}: {failure[2]}. "
                            f"Completed trajectories are kept; rerun the same command to resume.")


# ----------------------------------------------------------------------------
# Scoring, selection, reporting
# ----------------------------------------------------------------------------

def read_group(dirpath: Path, seeds) -> list[dict]:
    rows = []
    for s in seeds:
        p = dirpath / f"seed_{int(s)}.json"
        if not p.exists():
            raise ContractError(f"missing trajectory result: {p}")
        rows.append(json.loads(p.read_text()))
    return rows


def score(rows: list[dict], item: dict, space, targets, origin: str):
    m = np.array([r["target_row"] for r in rows], dtype=float)
    return ccs.build_record(
        candidate_id=item["cid"], batch=-1, origin=origin, provenance={"label": item.get("label", item.get("tuple_id"))},
        params=item["params"], space=space, replicate_seeds=[r["seed"] for r in rows],
        means=m.mean(axis=0), sample_variances=m.var(axis=0, ddof=1), targets=targets, replicates=len(rows))


def record_row(rec, targets) -> dict:
    row = {"I_max": rec.primary.i_max, "binding_target": rec.primary.binding_target,
           "primary_pass": rec.primary_pass, "J": rec.j, "mean_group_rms": rec.mean_r,
           "mean_I": float(np.mean(rec.primary.implausibility))}
    for t, i_val in zip(targets, rec.primary.implausibility):
        if t.key in ("shm_new_diagnoses_msm@2010", "shm_new_diagnoses_msm@2024", "rivm_aids_msm@2022"):
            row["I_" + t.key] = float(i_val)
    return row


def select_finalists(records: list, targets) -> list[tuple[object, list[str]]]:
    """Rule declared before any discovery trajectory (see module docstring)."""
    idx24 = [t.key for t in targets].index("shm_new_diagnoses_msm@2024")
    z24 = targets[idx24].z
    passers = [r for r in records if r.primary_pass]
    pool = passers if passers else sorted(records, key=ccs.ranking_key)[:FINALISTS]
    why: dict = {}
    picks: list = []

    def add(r, reason):
        if r.candidate_id not in why:
            picks.append(r)
            why[r.candidate_id] = []
        why[r.candidate_id].append(reason)

    tag = "" if passers else " (NO primary passer: lowest-J near misses)"
    add(min(pool, key=ccs.ranking_key), "lowest search J" + tag)
    add(min(pool, key=lambda r: (abs(float(r.means[idx24]) - z24), r.candidate_id)), "smallest 2024 diagnosis error" + tag)
    add(min(pool, key=lambda r: (r.primary.i_max, r.candidate_id)), "lowest I_max" + tag)
    for r in sorted(pool, key=ccs.ranking_key):
        if len(picks) >= FINALISTS:
            break
        if r.candidate_id in why:
            continue
        add(r, "next lowest J" + tag)
    return [(r, why[r.candidate_id]) for r in picks[:FINALISTS]]


def observed_msm():
    if not WORKBOOK.exists():
        return None
    raw = pd.read_excel(WORKBOOK, sheet_name="Stiching 1.1 (Diagnoses)", header=None)
    h = next(i for i in range(20) if any("Year of diagnosis" in str(v) for v in raw.iloc[i].values))
    df = pd.read_excel(WORKBOOK, sheet_name="Stiching 1.1 (Diagnoses)", header=h)
    df = df[["Year of diagnosis", "MSM+Trans (CALC)"]].dropna()
    df.columns = ["year", "obs"]
    df = df[pd.to_numeric(df.year, errors="coerce").notna()]
    df["year"] = df.year.astype(int)
    return df.set_index("year").obs.astype(float)


def curve(rows: list[dict], series: str = "new_diagnoses_msm") -> pd.DataFrame:
    return pd.DataFrame({r["seed"]: pd.Series({int(y): v for y, v in r["annual"][series].items()}) for r in rows}).sort_index()


def curve_metrics(cv: pd.DataFrame, obs) -> dict:
    if obs is None:
        return {}
    m = cv.mean(axis=1).loc[1996:2024]
    o = obs.loc[1996:2024]
    rel = m / o - 1
    return {"peak_year": int(m.idxmax()), "peak_value": float(m.max()),
            "obs_peak_year": int(o.idxmax()), "obs_peak_value": float(o.max()),
            "err_2008": float(rel.loc[2008]), "err_2010": float(rel.loc[2010]),
            "bias_2004_2015": float(rel.loc[2004:2015].mean()), "err_2024": float(rel.loc[2024]),
            "mape_1996_2024": float(rel.abs().mean())}


def paired_curve(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """b minus a on identical seeds."""
    d = (b - a).loc[1996:2024]
    se = d.std(axis=1, ddof=1) / math.sqrt(d.shape[1])
    mean_d = d.mean(axis=1)
    base = a.mean(axis=1).loc[1996:2024]
    return {"n_years_over_2se": int((mean_d.abs() > 2 * se).sum()), "n_years": int(len(mean_d)),
            "max_abs_rel_diff": float((mean_d.abs() / base).max()),
            "rms_diff": float(np.sqrt((mean_d ** 2).mean())),
            "mean_seed_sd_of_a": float(a.loc[1996:2024].std(axis=1, ddof=1).mean()),
            "diff_2008": float(mean_d.loc[2008]), "diff_2024": float(mean_d.loc[2024])}, mean_d, se


def md_table(df: pd.DataFrame, floatfmt: str = "{:.3f}") -> str:
    if df.empty:
        return "_(none)_"
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(floatfmt.format(v) if isinstance(v, (float, np.floating)) else str(v)
                                      for v in r.values) + " |")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def design_record(rows: list[dict]) -> dict:
    slim = [{k: r[k] for k in ("tuple_id", "cid", "backbone_case", "theta_candidate", "origin", "vector",
                               "u", "config_sha256")} for r in rows]
    return {"design": slim, "design_sha256": ccs.sha256_json(slim), "n_vectors_per_backbone": N_VECTORS,
            "box_half_width_normalised": BOX_HALF, "design_seed": DESIGN_SEED,
            "confirmation_seeds": list(CONF_SEEDS), "discovery_seeds": list(DISC_SEEDS),
            "source_artifact": {"artifact_id": CONFERENCE_ARTIFACT_ID,
                                "artifact_uri": conference_artifact_uri(CONFERENCE_CALIBRATION_RELATIVE),
                                "scientific_status": "historical; not current Option-B evidence"}}


def counts() -> dict:
    n_knot = len(CASES) ** 2 * len(CONF_SEEDS)
    n_disc = len(CASES) * N_VECTORS * len(DISC_SEEDS)
    n_conf = len(CASES) * FINALISTS * len(CONF_SEEDS)
    return {"knot_test": n_knot, "late_dx_discovery": n_disc, "late_dx_confirmation": n_conf,
            "total": n_knot + n_disc + n_conf}


def cmd_validate(workers: int) -> None:
    ctx = context()
    seed_preflight()
    cells = build_cells(ctx)
    design = build_late_design(ctx, cells)
    rec = design_record(design)
    c = counts()
    print("VALIDATE OK (no trajectory was run)")
    print(f"  inputs verified against plan hashes; targets: {len(ctx['targets'])}")
    for cell in cells:
        print(f"  cell {cell['label']:<40s} role={cell['role']:<5s} config_sha256={cell['config_sha256'][:12]}")
    print(f"  late-dx design: {len(design)} vectors, design_sha256={rec['design_sha256'][:12]}")
    print(f"  seeds: confirmation {CONF_SEEDS[0]}-{CONF_SEEDS[-1]}, discovery {DISC_SEEDS[0]}-{DISC_SEEDS[-1]} (no reuse found)")
    print(f"  trajectories: knot-test {c['knot_test']}, discovery {c['late_dx_discovery']}, "
          f"confirmation {c['late_dx_confirmation']}, TOTAL {c['total']}")
    print(f"  estimate at {workers} workers x {SEC_PER_TRAJ_PER_WORKER}s: {c['total'] * SEC_PER_TRAJ_PER_WORKER / workers / 60:.0f} min "
          f"(knot-test alone {c['knot_test'] * SEC_PER_TRAJ_PER_WORKER / workers / 60:.1f} min)")


def cmd_knot(workers: int) -> None:
    ctx = context()
    seed_preflight()
    cells = build_cells(ctx)
    tasks = make_tasks(cells, CONF_SEEDS, out_root(), ctx, lambda it: "knot_test/" + it["label"])
    run_tasks(tasks, workers, ctx, "knot-test")


def cmd_discover(workers: int) -> None:
    ctx = context()
    seed_preflight()
    design = build_late_design(ctx, build_cells(ctx))
    tasks = make_tasks(design, DISC_SEEDS, out_root(), ctx, lambda it: "late_dx/discovery/" + it["tuple_id"])
    run_tasks(tasks, workers, ctx, "late-dx-discovery")


def discovery_records(ctx: dict, design: list[dict]) -> list[tuple[dict, object]]:
    out = []
    for row in design:
        rows = read_group(out_root() / "late_dx/discovery" / row["tuple_id"], DISC_SEEDS)
        st = ctx["cases"][row["backbone_case"]]
        out.append((row, score(rows, row, st["space"], ctx["targets"], "late_dx_discovery")))
    return out


def cmd_select() -> None:
    ctx = context()
    design = build_late_design(ctx, build_cells(ctx))
    root = out_root()
    sl_path = root / "late_dx/shortlist.json"
    if sl_path.exists():
        print(f"shortlist already frozen: {sl_path} (sha256 {ccs.sha256_file(sl_path)[:16]})")
        return
    scored = discovery_records(ctx, design)
    table = []
    for row, rec in scored:
        table.append({"tuple_id": row["tuple_id"], "backbone": row["backbone_case"], "origin": row["origin"],
                      **{f"x_{k}": v for k, v in row["vector"].items()}, **record_row(rec, ctx["targets"])})
    pd.DataFrame(table).to_csv(root / "late_dx/discovery_summary.csv", index=False)
    entries = []
    for bi, tcase in enumerate(CASES):
        subset = [(row, rec) for row, rec in scored if row["backbone_case"] == tcase]
        by_id = {rec.candidate_id: row for row, rec in subset}
        for rec, why in select_finalists([rec for _, rec in subset], ctx["targets"]):
            entries.append({"tuple_id": by_id[rec.candidate_id]["tuple_id"], "cid": rec.candidate_id,
                            "backbone_case": tcase, "why": why, "discovery_I_max": rec.primary.i_max,
                            "discovery_J": rec.j, "config_sha256": by_id[rec.candidate_id]["config_sha256"]})
    frozen = {"created_utc": now(), "rule": "select_finalists() in run_followup_knot_and_late_dx_20260929.py",
              "confirmation_seeds": list(CONF_SEEDS), "entries": entries,
              "design_sha256": design_record(design)["design_sha256"],
              "source_artifact": ctx["source_artifact"]}
    atomic_json(sl_path, frozen)
    print(f"froze {len(entries)} finalists -> {sl_path}")
    for e in entries:
        print(f"  {e['tuple_id']}: I_max {e['discovery_I_max']:.2f} J {e['discovery_J']:.2f} ({'; '.join(e['why'])})")


def cmd_confirm(workers: int) -> None:
    ctx = context()
    seed_preflight()
    design = {r["tuple_id"]: r for r in build_late_design(ctx, build_cells(ctx))}
    sl_path = out_root() / "late_dx/shortlist.json"
    if not sl_path.exists():
        raise ContractError("no frozen shortlist: run late-dx-select first")
    frozen = json.loads(sl_path.read_text())
    items = [design[e["tuple_id"]] for e in frozen["entries"]]
    for e in frozen["entries"]:
        if design[e["tuple_id"]]["config_sha256"] != e["config_sha256"]:
            raise ContractError(f"{e['tuple_id']} no longer rebuilds to its frozen configuration")
    tasks = make_tasks(items, CONF_SEEDS, out_root(), ctx, lambda it: "late_dx/confirmation/" + it["tuple_id"])
    run_tasks(tasks, workers, ctx, "late-dx-confirmation")


SMOKE_SEED = 230299


def cmd_smoke(workers: int) -> None:
    """One M0 and one M1 trajectory on a seed used nowhere else: proves the engine path before the long run."""
    ctx = context()
    cells = build_cells(ctx)
    design = build_late_design(ctx, cells)
    items = [next(c for c in cells if c["role"] == "own" and c["theta_case"] == "break_2010"), design[0]]
    tasks = make_tasks(items, [SMOKE_SEED], out_root(), ctx,
                       lambda it: "smoke/" + it.get("label", it.get("tuple_id")))
    run_tasks(tasks, min(workers, 2), ctx, "smoke")
    rows = [json.loads(t["path"].read_text()) for t in tasks]
    for r in rows:
        if not np.all(np.isfinite(r["target_row"])):
            raise ContractError(f"smoke: non-finite target output in {r['label']}")
    print("SMOKE OK")
    for r in rows:
        d = r["annual"]["new_diagnoses_msm"]
        print(f"  {r['label']:<42s} dx2008={d['2008']:.0f} dx2024={d['2024']:.0f} wall={r['wall_seconds']:.0f}s")
    if len({r["config_sha256"] for r in rows}) != 2:
        raise ContractError("smoke: the two smoke items ran the same configuration")


def cmd_status() -> None:
    root = out_root()
    c = counts()
    have = lambda sub: len(list((root / sub).glob("**/seed_*.json"))) if (root / sub).exists() else 0
    print(f"output root: {root}")
    print(f"knot_test:            {have('knot_test')}/{c['knot_test']}")
    print(f"late_dx discovery:    {have('late_dx/discovery')}/{c['late_dx_discovery']}")
    print(f"late_dx shortlist:    {'FROZEN' if (root / 'late_dx/shortlist.json').exists() else 'not yet'}")
    print(f"late_dx confirmation: {have('late_dx/confirmation')}/{c['late_dx_confirmation']}")


def cmd_report() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ctx = context()
    root = out_root()
    obs = observed_msm()
    cells = build_cells(ctx)
    targets = ctx["targets"]
    banner = "**SYNTHETIC OUTPUT FROM --fake-engine: pipeline test only, not scientific.**\n\n" if FAKE else ""
    lines = [f"# Follow-up: knot test and late-diagnosis engine ({'FAKE' if FAKE else 'real'} run)", "", banner,
             f"Source artifact: `{conference_artifact_uri(CONFERENCE_CALIBRATION_RELATIVE)}` "
             "(historical 2026-09-29 lineage; not current Option-B evidence).", "",
             "Post-hoc extension designed after the conference-suite results; see the script docstring for "
             "the declared design. Fresh confirmation seeds 230100-230119.", ""]
    # ---- step 1 / 3
    rows, curves, recs = [], {}, {}
    for cell in cells:
        g = read_group(root / "knot_test" / cell["label"], CONF_SEEDS)
        st = ctx["cases"][cell["knot_case"]]
        rec = score(g, cell, st["space"], targets, "knot_test")
        recs[cell["label"]] = rec
        curves[cell["label"]] = curve(g)
        rows.append({"cell": cell["label"], "role": cell["role"], **record_row(rec, targets),
                     **curve_metrics(curves[cell["label"]], obs)})
    knot_df = pd.DataFrame(rows)
    knot_df.to_csv(root / "knot_test_summary.csv", index=False)
    lines += ["## Steps 1 and 3: same-theta knot test (fresh seeds)", "",
              "`own` cells are fresh-seed re-confirmations of the selected candidates; `cross` cells run the "
              "same 33 coordinates under the other knot.", "",
              md_table(knot_df[[c for c in ["cell", "role", "I_max", "binding_target", "J", "mean_I", "peak_year",
                                            "peak_value", "err_2008", "err_2010", "bias_2004_2015", "err_2024",
                                            "mape_1996_2024"] if c in knot_df.columns]]), ""]
    pair_rows, pair_curves = [], {}
    for tcase in CASES:
        own = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "own")["label"]
        cross = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "cross")["label"]
        summ, mean_d, se = paired_curve(curves[own], curves[cross])
        pair_rows.append({"theta": tcase, "cross_minus_own": f"{cross} - {own}", **summ})
        pair_curves[tcase] = (mean_d, se)
        pd.DataFrame({"mean_diff": mean_d, "se": se}).to_csv(root / f"knot_test_paired_curve_{tcase}.csv")
    pair_df = pd.DataFrame(pair_rows)
    pair_df.to_csv(root / "knot_test_paired_summary.csv", index=False)
    lines += ["### Paired curve differences (cross-knot minus own-knot, same theta, same seeds)", "",
              md_table(pair_df), "",
              "`n_years_over_2se` counts years 1996-2024 where the mean paired difference exceeds two standard "
              "errors; `rms_diff` and `max_abs_rel_diff` size the difference against `mean_seed_sd_of_a`.", ""]
    if obs is not None:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
        for ax, tcase in zip(axes, CASES):
            own = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "own")["label"]
            cross = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "cross")["label"]
            o = obs.loc[1996:2024]
            ax.plot(o.index, o.values, "k-", lw=2, label="observed (SHM MSM)")
            for lab, col in ((own, "tab:blue"), (cross, "tab:orange")):
                m, s = curves[lab].mean(axis=1).loc[1996:2024], curves[lab].std(axis=1, ddof=1).loc[1996:2024]
                ax.plot(m.index, m.values, color=col, label=lab.replace("__", "\n"))
                ax.fill_between(m.index, m - s, m + s, color=col, alpha=0.15)
            ax.set_title(f"theta {tcase} (candidate {CASES[tcase]['candidate_id']})")
            ax.set_xlabel("year")
            ax.legend(fontsize=7)
        axes[0].set_ylabel("MSM diagnoses per year")
        fig.tight_layout()
        fig.savefig(root / "fig_knot_test.png", dpi=150)
        plt.close(fig)
        lines += ["![knot test](fig_knot_test.png)", ""]
    # ---- step 2
    sl_path = root / "late_dx/shortlist.json"
    conf_root = root / "late_dx/confirmation"
    if (root / "late_dx/discovery_summary.csv").exists():
        disc = pd.read_csv(root / "late_dx/discovery_summary.csv")
        summ = disc.groupby("backbone").agg(n=("tuple_id", "count"), n_pass=("primary_pass", "sum"),
                                            best_J=("J", "min"), best_I_max=("I_max", "min")).reset_index()
        lines += ["## Step 2: late-diagnosis engine on the new backbones", "",
                  "Discovery (5 seeds, 96 vectors per backbone):", "", md_table(summ), ""]
        transfer = disc[disc.origin == "transfer_D_1788_v109"][["tuple_id", "backbone", "I_max", "J", "primary_pass"]]
        lines += ["Transfer of the D_1788_v109 vector without re-tuning:", "", md_table(transfer), ""]
    if sl_path.exists() and conf_root.exists() and any(conf_root.glob("*/seed_*.json")):
        frozen = json.loads(sl_path.read_text())
        design = {r["tuple_id"]: r for r in build_late_design(ctx, cells)}
        crow, ccurves = [], {}
        for tcase in CASES:
            own = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "own")
            crow.append({"cell": own["label"] + " (legacy, M0)", "role": "M0", **record_row(recs[own["label"]], targets),
                         **curve_metrics(curves[own["label"]], obs)})
        prow = []
        for e in frozen["entries"]:
            item = design[e["tuple_id"]]
            g = read_group(conf_root / e["tuple_id"], CONF_SEEDS)
            st = ctx["cases"][item["backbone_case"]]
            rec = score(g, item, st["space"], targets, "late_dx_confirmation")
            ccurves[e["tuple_id"]] = curve(g)
            crow.append({"cell": e["tuple_id"], "role": "M1", **record_row(rec, targets),
                         **curve_metrics(ccurves[e["tuple_id"]], obs)})
            own = next(c for c in cells if c["theta_case"] == item["backbone_case"] and c["role"] == "own")["label"]
            summ, _, _ = paired_curve(curves[own], ccurves[e["tuple_id"]])
            prow.append({"finalist": e["tuple_id"], "paired_with": own, "selected_for": "; ".join(e["why"]), **summ})
        cdf = pd.DataFrame(crow)
        cdf.to_csv(root / "late_dx_confirmation_summary.csv", index=False)
        pdf = pd.DataFrame(prow)
        pdf.to_csv(root / "late_dx_paired_summary.csv", index=False)
        lines += ["Fresh-seed confirmation (20 seeds) of frozen finalists against each backbone's own legacy cell:", "",
                  md_table(cdf[[c for c in ["cell", "role", "I_max", "binding_target", "J", "mean_I", "peak_year",
                                            "peak_value", "err_2008", "err_2010", "bias_2004_2015", "err_2024",
                                            "mape_1996_2024", "I_rivm_aids_msm@2022"] if c in cdf.columns]]), "",
                  "Paired curve differences (finalist minus legacy, same seeds):", "", md_table(pdf), ""]
        if obs is not None:
            fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
            for ax, tcase in zip(axes, CASES):
                o = obs.loc[1996:2024]
                ax.plot(o.index, o.values, "k-", lw=2, label="observed")
                own = next(c for c in cells if c["theta_case"] == tcase and c["role"] == "own")["label"]
                m = curves[own].mean(axis=1).loc[1996:2024]
                ax.plot(m.index, m.values, "--", color="gray", label="legacy (M0)")
                for tid, cv in ccurves.items():
                    if design[tid]["backbone_case"] == tcase:
                        m = cv.mean(axis=1).loc[1996:2024]
                        ax.plot(m.index, m.values, label=tid.replace("LD_", ""))
                ax.set_title(f"backbone {tcase}")
                ax.set_xlabel("year")
                ax.legend(fontsize=7)
            axes[0].set_ylabel("MSM diagnoses per year")
            fig.tight_layout()
            fig.savefig(root / "fig_late_dx.png", dpi=150)
            plt.close(fig)
            lines += ["![late dx](fig_late_dx.png)", ""]
    lines += ["## Limits (declared)", "",
              "- Post-hoc extension; disclose as such.",
              "- Calibration compatibility (I_max <= 3) is not predictive validity; the tolerance is wide.",
              "- The late-diagnosis search is 4-dimensional with the other 33 coordinates fixed at the backbone; it "
              "is a targeted re-tune, not a joint recalibration.",
              "- Discovery used 5 seeds and 96 vectors per backbone (computational choices, not optimality claims).",
              "- Finalists are selected on discovery seeds and confirmed on separate fresh seeds shared with the legacy cells.",
              "- Curve metrics (MAPE, bias) are descriptive; they are not part of the frozen 13-target contract."]
    (root / "REPORT.md").write_text("\n".join(lines))
    print(f"wrote {root / 'REPORT.md'}")


def main() -> None:
    global FAKE
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["validate", "smoke", "knot-test", "late-dx-discover", "late-dx-select",
                                        "late-dx-confirm", "report", "status", "all"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fake-engine", action="store_true",
                    help="synthetic stand-in worker to test the pipeline; writes to a separate _FAKE directory")
    args = ap.parse_args()
    if args.command != "status":
        try:
            require_historical_source()
        except ArtifactVaultError as exc:
            ap.error(str(exc))
    FAKE = bool(args.fake_engine)
    cmds = {"validate": lambda: cmd_validate(args.workers), "smoke": lambda: cmd_smoke(args.workers), "knot-test": lambda: cmd_knot(args.workers),
            "late-dx-discover": lambda: cmd_discover(args.workers), "late-dx-select": cmd_select,
            "late-dx-confirm": lambda: cmd_confirm(args.workers), "report": cmd_report, "status": cmd_status}
    if args.command == "all":
        for name in ("validate", "smoke", "knot-test", "late-dx-discover", "late-dx-select", "late-dx-confirm", "report"):
            print(f"\n===== {name} =====", flush=True)
            cmds[name]()
    else:
        cmds[args.command]()


if __name__ == "__main__":
    main()
